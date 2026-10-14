"""Host-owned model and tool budgets. No API credential enters an agent container."""
from __future__ import annotations

import json
import math
import copy
import hashlib
import inspect
import re
import secrets as random_secrets
import subprocess
import tempfile
import threading
import queue
import socket
import time
import urllib.error
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import append_event, secrets, read, write, BudgetExceeded
from .context_budget import ContextBudget


class ModelError(RuntimeError):
    pass


TEXT_PROTOCOL = 'mini-text-bash-v4'


DEFAULT_TRANSPORT_POLICY = {
    'version': 1,
    # ``None`` preserves the native broker's historical deadline behavior;
    # online runs opt in through transport_policy.json or the constructor.
    'request_timeout_seconds': None,
    'pause_on_http_status': [],
    'pause_on_invalid_completion': False,
    'pause_on_missing_usage': False,
    'pause_on_transport_error': False,
}


def _transport_policy(root, supplied):
    """Load and validate the host-frozen transport fault policy."""
    path = root / 'transport_policy.json'
    if supplied is None and path.exists():
        supplied = read(path)
    if supplied is None:
        return dict(DEFAULT_TRANSPORT_POLICY)
    if not isinstance(supplied, dict):
        raise ValueError('transport_policy must be an object')
    allowed = set(DEFAULT_TRANSPORT_POLICY)
    if set(supplied) - allowed:
        raise ValueError('transport_policy has unknown fields')
    policy = {**DEFAULT_TRANSPORT_POLICY, **supplied}
    if type(policy.get('version')) is not int or policy['version'] != 1:
        raise ValueError('transport_policy version must be 1')
    timeout = policy.get('request_timeout_seconds')
    if timeout is not None and (not isinstance(timeout, (int, float))
                                or isinstance(timeout, bool) or not math.isfinite(timeout)
                                or timeout <= 0):
        raise ValueError('request_timeout_seconds must be a positive finite number or null')
    statuses = policy.get('pause_on_http_status')
    if (not isinstance(statuses, list)
            or any(type(status) is not int or not 100 <= status <= 599 for status in statuses)
            or len(set(statuses)) != len(statuses)):
        raise ValueError('pause_on_http_status must be a list of unique HTTP status integers')
    for key in ('pause_on_invalid_completion', 'pause_on_missing_usage', 'pause_on_transport_error'):
        if type(policy.get(key)) is not bool:
            raise ValueError(f'{key} must be boolean')
    return policy


def canonical_parameters(parameters):
    """Ollama may reorder map entries; preserve values while freezing content."""
    lines = []
    for line in parameters.splitlines():
        fields = line.strip().split(None, 1)
        if fields:
            lines.append(' '.join(fields))
    return '\n'.join(sorted(lines)) + '\n'


def normalize_messages(messages):
    """Fixed text protocol: one content string and optional assistant reasoning."""
    if not isinstance(messages, list):
        raise ModelError('Invalid messages')
    normalized = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant'):
            raise ModelError('Invalid text message role')
        if any(message.get(k) for k in ('tool_calls', 'function_call', 'tool_call_id')):
            raise ModelError('Native tools are outside the text bash protocol')
        content = message.get('content')
        if not isinstance(content, str):
            raise ModelError('Invalid text message content')
        clean = {'role': message['role'], 'content': content}
        reasoning = message.get('reasoning_content')
        if reasoning is not None:
            if message['role'] != 'assistant' or not isinstance(reasoning, str):
                raise ModelError('Invalid reasoning_content')
            clean['reasoning_content'] = reasoning
        normalized.append(clean)
    return normalized


_ACTION_TAG = re.compile(r'</?(?:tool_call|mswea_bash_command|bash_command)\b')
_FENCED_ACTION = re.compile(
    r'^```(?P<format>mswea_bash_command|bash)[ \t]*\r?\n'
    r'(?P<command>.*?)\r?\n^```[ \t]*(?=\r?$)',
    re.MULTILINE | re.DOTALL)
# Only delimiter lines frame a code block. Backticks in prose/JSON are data.
_FENCE_LINE = re.compile(r'^[ \t]*(?:`{3,}|~{3,})[^\r\n]*\r?$', re.MULTILINE)
_INLINE_CODE = re.compile(r'(?<!`)(`+)(?!`)[^\r\n]*?(?<!`)\1(?!`)')
_QUOTED_TEXT = re.compile(r'"(?:[^"\\\r\n]|\\[^\r\n])*"')
_QUOTED_EXAMPLES = re.compile(_INLINE_CODE.pattern + '|' + _QUOTED_TEXT.pattern)


_XML_ACTION = re.compile(
    r'(?P<outer><tool_call>\s*)?'
    r'<(?P<tag>mswea_bash_command|bash_command)>[ \t]*\r?\n?'
    r'(?P<command>.*?)</(?P=tag)>(?(outer)\s*</tool_call>)', re.DOTALL)


def _mask_quoted_examples(text):
    """Keep offsets while excluding inline code and quoted JSON/prose data."""
    # One pass respects the earliest outer quotation; two separate passes
    # could pair a backtick inside JSON with one beyond the closing XML tag.
    return _QUOTED_EXAMPLES.sub(lambda match: ' ' * len(match.group()), text)


def _single_text_action(text):
    """Parse structural envelopes, preserving quoted examples as command data."""
    fence_lines = list(_FENCE_LINE.finditer(text))
    if fence_lines:
        matches = list(_FENCED_ACTION.finditer(text))
        if len(matches) != 1 or len(fence_lines) != 2:
            raise ValueError('multiple, incomplete or unsupported fences')
        match = matches[0]
        # Each delimiter must belong to the one accepted block. This rejects
        # extra/malformed fence lines rather than selecting a convenient block.
        if any(not match.start() <= line.start() < match.end() for line in fence_lines):
            raise ValueError('mixed or incomplete action envelopes')
        kind = match['format']
        command = match['command']
    else:
        inspection = _mask_quoted_examples(text)
        matches = list(_XML_ACTION.finditer(inspection))
        if not matches and not _ACTION_TAG.search(inspection):
            return None
        if len(matches) != 1:
            raise ValueError('multiple or incomplete XML actions')
        match = matches[0]
        kind = 'xml:' + match['tag']
        command = text[match.start('command'):match.end('command')]
        if _ACTION_TAG.search(_mask_quoted_examples(command)):
            raise ValueError('nested XML actions')
    outside = _mask_quoted_examples(text[:match.start()] + text[match.end():])
    if _ACTION_TAG.search(outside):
        raise ValueError('mixed or incomplete action envelopes')
    if not command.strip():
        raise ValueError('empty action')
    return match, kind, '```mswea_bash_command\n' + command + '\n```'


def adapt_response(response):
    """Normalize one explicit text action; retain the untouched provider evidence."""
    returned = copy.deepcopy(response)
    choices = returned.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        return returned, 'unchanged: expected one choice'
    choice = choices[0]
    if not isinstance(choice, dict):
        return returned, 'unchanged: invalid choice'
    message = choice.get('message', {})
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return returned, 'unchanged: invalid assistant message'
    if choice.get('finish_reason') != 'stop':
        return returned, 'unchanged: non-stop finish'
    if any(message.get(key) for key in ('tool_calls', 'function_call', 'refusal')):
        return returned, 'unchanged: native tool or refusal'
    content = message.get('content')
    if content not in (None, ''):
        if not isinstance(content, str):
            return returned, 'unchanged: invalid content'
        try:
            action = _single_text_action(content)
        except ValueError as exc:
            # Merely returning mixed content is unsafe: mini might execute the
            # one canonical block while ignoring a second XML/bash action.
            message['content'] = 'Action format rejected: ' + str(exc)
            return returned, 'rejected ambiguous content: ' + str(exc)
        if action is None or action[1] == 'mswea_bash_command':
            return returned, 'unchanged: nonempty content'
        match, kind, canonical = action
        message['content'] = content[:match.start()] + canonical + content[match.end():]
        return returned, 'normalized single complete content action: ' + kind
    fields = {key: message[key] for key in ('reasoning_content', 'reasoning') if message.get(key) is not None}
    if not fields or any(not isinstance(value, str) for value in fields.values()):
        return returned, 'unchanged: missing or invalid reasoning'
    if len(set(fields.values())) != 1:
        return returned, 'unchanged: conflicting reasoning fields'
    reasoning = next(iter(fields.values()))
    try:
        action = _single_text_action(reasoning)
    except ValueError:
        action = None
    if action is None:
        return returned, 'unchanged: reasoning has no unique complete action'
    match, kind, canonical = action
    message['content'] = canonical
    for key in fields:
        message[key] = (reasoning[:match.start()] + reasoning[match.end():]).strip()
    return returned, 'promoted single complete reasoning action; removed action from ' + ','.join(fields) + '; format=' + kind

class Broker:
    def __init__(self, root, budget, context=None, billing_mode=None,
                 transport_policy=None):
        self.root, self.budget = root, budget
        self.context = context if context is not None else None
        self.transport_policy = _transport_policy(root, transport_policy)
        self.transport_pause_path = root / 'transport_pause.json'
        self.transport_pause = None
        if self.transport_pause_path.exists():
            try:
                marker = read(self.transport_pause_path)
            except Exception:
                marker = None
            if not isinstance(marker, dict) or marker.get('status') != 'paused':
                # A file in the pause-marker location is host state.  A
                # corrupt marker fails closed instead of silently reopening
                # model/tool traffic after an interrupted fault.
                marker = {'version': 1, 'status': 'paused',
                          'reason': 'invalid_pause_marker'}
                write(self.transport_pause_path, marker)
            self.transport_pause = marker
        self.pause_event = threading.Event()
        if self.transport_pause is not None:
            self.pause_event.set()
        self.config = secrets()
        if billing_mode is not None:
            if billing_mode != 'subscription':
                raise ValueError('Only the explicit subscription billing mode is supported')
            self.config['RSI_BILLING_MODE'] = billing_mode
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.lock = threading.Lock()
        # The host still serializes model/tool work, while this separate
        # condition lets finish() wait without holding up the transport.
        self._state_condition = threading.Condition(self.lock)
        self.request_lock = threading.Lock()
        self._active_request_meta = None
        self.candidate_lock = threading.RLock()
        self.sessions = {}
        self.events = []
        # Candidate checks are registered by the host before a container can
        # request them.  A remote client supplies only the opaque job id and
        # frozen check parameters; it can never choose a host path or task.
        self.candidate_jobs = {}
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                if token not in broker.sessions:
                    self.send_error(403)
                    return
                size = int(self.headers.get('Content-Length', 0))
                if size <= 0:
                    self.send_error(413)
                    return
                try:
                    payload = json.loads(self.rfile.read(size))
                    if self.path == '/v1/chat/completions':
                        out = broker.complete(token, payload, self.headers.get('X-RSI-Agent','main'), self.headers.get('X-RSI-Role','orchestrator'))
                    elif self.path == '/status':
                        out = broker.status(token)
                    elif self.path == '/tool':
                        out = broker.tool(token, payload)
                    elif self.path == '/stage_boundary':
                        out = broker.stage_boundary(token)
                    elif self.path == '/candidate_check':
                        out = broker.candidate_check(token, payload,
                                                     self.headers.get('X-RSI-Agent','main'),
                                                     self.headers.get('X-RSI-Role','orchestrator'))
                    else:
                        self.send_error(404)
                        return
                    status = 200
                except Exception as exc:
                    out = {'error': {'message': str(exc), 'type': type(exc).__name__}}
                    status = 400
                    if str(exc) == 'ContextLimitExceeded':
                        out['error'].update(type='invalid_request_error', code='context_length_exceeded')
                        out['error']['message'] = 'exceed context limit: frozen model context capacity (ContextLimitExceeded)'
                    elif str(exc) == 'InputIntegrityError':
                        out['error'].update(type='invalid_request_error', code='input_integrity_failed')
                    elif str(exc) in ('TimeExceeded', 'LimitsExceeded','CostExceeded'):
                        out['error'].update(type='permission_error', code='budget_exhausted')
                        status = 403
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _raise_if_paused(self):
        if self.pause_event.is_set():
            raise ModelError('TransportPaused')

    @staticmethod
    def _valid_usage(usage):
        keys = ('total_tokens', 'prompt_tokens', 'completion_tokens')
        return (isinstance(usage, dict)
                and all(type(usage.get(key)) is int and usage[key] >= 0 for key in keys))

    def _pause_enabled(self, category, status=None):
        policy = self.transport_policy
        if category == 'http_status':
            return status in policy['pause_on_http_status']
        if category == 'missing_usage':
            return policy['pause_on_missing_usage']
        if category in ('invalid_completion', 'upstream_error', 'finish_reason_error', 'invalid_response'):
            return policy['pause_on_invalid_completion']
        if category in ('transport_timeout', 'transport_error'):
            return policy['pause_on_transport_error']
        return False

    @staticmethod
    def _safe_metrics(s):
        return {key: s.get(key, 0) for key in (
            'calls', 'tokens', 'prompt_tokens', 'completion_tokens',
            'cached_tokens', 'unmetered_calls')}

    def _pause_transport(self, s, category, event, status=None):
        """Persist a secret-free sticky transport pause exactly once."""
        if self.pause_event.is_set():
            return
        marker = {
            'version': 1,
            'status': 'paused',
            'reason': category,
            'event_id': event.get('event_id'),
            'label': s.get('label'),
            'call': event.get('call'),
            'metrics': self._safe_metrics(s),
        }
        if status is not None:
            marker['http_status'] = status
        write(self.transport_pause_path, marker)
        self.transport_pause = marker
        self.pause_event.set()
        pause_event = {'kind': 'transport_pause', **marker}
        self.events.append(pause_event)
        append_event(self.root, pause_event)

    @staticmethod
    def _response_fault(result):
        if not isinstance(result, dict):
            return 'invalid_response'
        if result.get('error') is not None:
            return 'upstream_error'
        choices = result.get('choices')
        if not isinstance(choices, list):
            return 'invalid_response'
        for choice in choices:
            if isinstance(choice, dict):
                if choice.get('finish_reason') == 'error':
                    return 'finish_reason_error'
                message = choice.get('message')
                if isinstance(message, dict) and message.get('error') is not None:
                    return 'upstream_error'
        return None

    @staticmethod
    def _safe_response(result):
        """Keep provider errors out of host ledgers while retaining normal output."""
        if not isinstance(result, dict):
            return {'error': {'type': 'invalid_response'}}
        fault = Broker._response_fault(result)
        if fault == 'invalid_response':
            safe = {key: result[key] for key in ('id', 'object', 'created', 'model') if key in result}
            safe['error'] = {'type': fault}
            return safe
        if fault == 'finish_reason_error':
            safe = {key: result[key] for key in ('id', 'object', 'created', 'model') if key in result}
            safe['error'] = {'type': fault}
            return safe
        if fault == 'upstream_error' and result.get('error') is None:
            safe = {key: result[key] for key in ('id', 'object', 'created', 'model') if key in result}
            safe['error'] = {'type': fault}
            return safe
        if result.get('error') is None:
            return result
        safe = {key: result[key] for key in ('id', 'object', 'created', 'model') if key in result}
        error = result.get('error')
        if isinstance(error, dict):
            safe_error = {key: error[key] for key in ('type', 'code') if key in error}
        else:
            safe_error = {'type': 'upstream_error'}
        safe['error'] = safe_error
        return safe

    def _record_late_response(self, token, label, call, event_id, result):
        usage = result.get('usage') if isinstance(result, dict) else None
        valid = self._valid_usage(usage)
        metrics = None
        with self.lock:
            s = self.sessions.get(token) if token is not None else None
            if s is not None and valid:
                s['prompt_tokens'] += usage['prompt_tokens']
                s['completion_tokens'] += usage['completion_tokens']
                s['tokens'] += usage['total_tokens']
                cached = usage.get('prompt_cache_hit_tokens',
                                   (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0))
                if type(cached) is int and 0 <= cached <= usage['prompt_tokens']:
                    s['cached_tokens'] += cached
                if s.get('unmetered_calls', 0):
                    s['unmetered_calls'] -= 1
                metrics = self._safe_metrics(s)
        late = {'kind': 'late_model_response', 'label': label, 'call': call,
                'event_id': event_id, 'usage': usage if valid else None,
                'usage_valid': valid, 'returned_to_agent': False}
        if not valid:
            late['usage_error'] = 'missing_or_invalid'
        if metrics is not None:
            late['metrics'] = metrics
        self.events.append(late)
        append_event(self.root, late)

    def _finish_inflight(self, token):
        with self._state_condition:
            s = self.sessions.get(token)
            if s is not None:
                s['inflight'] = False
            self._state_condition.notify_all()

    def register_candidate_job(self, job_id, callback, metadata=None):
        """Register one authenticated, idempotent candidate check.

        ``callback`` runs in the host process and owns all path resolution.
        Re-registering an id is allowed only for the exact same metadata, so a
        restarted relay cannot silently point a job at another candidate.
        """
        self._raise_if_paused()
        if not isinstance(job_id, str) or not job_id or len(job_id) > 160:
            raise ValueError('Invalid candidate job id')
        if not callable(callback):
            raise TypeError('Candidate callback must be callable')
        metadata = metadata or {}
        with self.candidate_lock:
            prior = self.candidate_jobs.get(job_id)
            if prior is not None:
                if prior['metadata'] != metadata:
                    raise ValueError('Candidate job identity changed')
                return
            record = self.root / 'candidate_checks' / (job_id.replace('/', '_') + '.json')
            prior_state = read(record) if record.exists() else {}
            if prior_state and prior_state.get('metadata') not in (None, metadata):
                raise ValueError('Candidate job identity changed in persisted state')
            prior_status = prior_state.get('status')
            prior_error = prior_state.get('error')
            if prior_status == 'running':
                prior_status = 'interrupted'
                prior_error = {'type':'OnlineInterrupted',
                               'message':'Candidate check was interrupted before completion; use a new revision'}
            self.candidate_jobs[job_id] = {'callback': callback, 'metadata': metadata,
                                           'result': prior_state.get('result'),
                                           'error': prior_error, 'running': False,
                                           'record': record}
            write(record, {'job_id': job_id, 'metadata': metadata,
                           'status': ('completed' if prior_state.get('result') is not None else
                                      prior_status or 'registered'),
                           'result': prior_state.get('result'), 'error': prior_error})

    def candidate_check(self, token, payload, agent='main', role='orchestrator'):
        """Execute or return a frozen candidate check through the broker."""
        self._raise_if_paused()
        if not isinstance(payload, dict):
            raise ModelError('Candidate check payload must be an object')
        job_id = payload.get('job_id')
        with self.candidate_lock:
            job = self.candidate_jobs.get(job_id)
        if job is None:
            raise ModelError('Unknown candidate check job')
        self.check_session(self.sessions[token], model_request=False)
        identity_keys = ('job_id', 'task_id', 'check_revision', 'round_id', 'candidate',
                         'parent_version', 'stage')
        bound = {k: job['metadata'][k] for k in identity_keys if k in job['metadata']}
        requested = {k: payload[k] for k in identity_keys if k in payload}
        # Every identity field registered by the host must be echoed by the
        # caller.  A caller cannot omit a bound stage/round/candidate field or
        # add an unregistered identity field and still reuse the job.
        if set(requested) != set(bound) or requested != bound:
            raise ModelError('Candidate check identity differs from host registration')
        for key, actual in (('agent', agent), ('role', role)):
            expected_identity = job['metadata'].get(key)
            if expected_identity is not None and expected_identity != actual:
                raise ModelError('Candidate check execution identity differs from host registration')
        expected_session_hash = job['metadata'].get('session_token_sha256')
        if expected_session_hash is not None and hashlib.sha256(token.encode()).hexdigest() != expected_session_hash:
            raise ModelError('Candidate check session differs from host registration')
        with self.candidate_lock:
            if job['result'] is not None:
                return job['result']
            if job.get('error') is not None:
                raise ModelError('Candidate check previously failed; use a new check revision')
            if job['running']:
                return {'job_id': job_id, 'status': 'running',
                        'check_revision': job['metadata'].get('check_revision')}
            job['running'] = True
            write(job['record'], {'job_id':job_id, 'metadata':job['metadata'], 'status':'running'})
        try:
            callback = job['callback']
            # Existing host callbacks are zero-argument closures.  Online
            # step 3 may additionally freeze a mechanism plan in the request;
            # pass the authenticated payload only to callbacks that opt into
            # that argument, keeping the legacy callback contract intact.
            accepts_payload = bool(inspect.signature(callback).parameters)
            result = callback(payload) if accepts_payload else callback()
            if not isinstance(result, dict):
                raise ModelError('Candidate check returned an invalid result')
            with self.candidate_lock:
                job['result'] = result
                write(job['record'], {'job_id':job_id, 'metadata':job['metadata'],
                                      'status':'completed', 'result':result})
            event = {'kind':'candidate_check_complete', 'job_id':job_id,
                     'task_id':job['metadata'].get('task_id'),
                     'check_revision':job['metadata'].get('check_revision')}
            self.events.append(event)
            append_event(self.root, event)
            return result
        except Exception as exc:
            with self.candidate_lock:
                job['error'] = {'type':type(exc).__name__, 'message':str(exc)}
                write(job['record'], {'job_id':job_id, 'metadata':job['metadata'],
                                      'status':'failed', 'error':job['error']})
            raise
        finally:
            with self.candidate_lock:
                job['running'] = False

    def remote(self, path):
        url = self.config['OPENAI_BASE_URL'].rstrip('/')
        if path.startswith('/api/'):
            url = url.removesuffix('/v1')
        request = urllib.request.Request(url + path, headers={
            'Authorization': 'Bearer ' + self.config['OPENAI_API_KEY']})
        try:
            with self.opener.open(request, timeout=min(30, self.budget.remaining())) as f:
                return json.load(f)
        except Exception as e:
            raise ModelError(f'Model identity request failed: {type(e).__name__}') from None

    def service_configuration(self, model, tokenizer_identity=None):
        request = urllib.request.Request(self.config['OPENAI_BASE_URL'].rstrip('/').removesuffix('/v1') + '/api/show',
            data=json.dumps({'model': model, 'verbose': True}).encode(), headers={
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.config['OPENAI_API_KEY']})
        try:
            with self.opener.open(request, timeout=min(30, self.budget.remaining())) as response:
                shown = json.load(response)
        except Exception as exc:
            raise ModelError('Service context configuration unavailable: ' + type(exc).__name__) from None
        parameters = shown.get('parameters', '')
        match = re.search(r'^num_ctx\s+(\d+)\s*$', parameters, re.MULTILINE)
        if not match:
            raise ModelError('Service does not advertise an explicit num_ctx')
        configured_context = int(match.group(1))
        if configured_context < self.context.contract['total_tokens']:
            raise ModelError('Service num_ctx is below frozen host total token budget')
        template = shown.get('template')
        if not isinstance(template, str) or not template:
            raise ModelError('Service chat template is unavailable')
        family = (shown.get('details') or {}).get('family')
        if family != 'qwen35':
            raise ModelError('Service model family does not match frozen Qwen3.5 tokenizer')
        metadata = {key: value for key, value in (shown.get('model_info') or {}).items()
                    if key.startswith('tokenizer.ggml.')}
        if any(not isinstance(metadata.get(key), list) or not metadata[key]
               for key in ('tokenizer.ggml.tokens', 'tokenizer.ggml.merges', 'tokenizer.ggml.token_type')):
            raise ModelError('Service tokenizer metadata is incomplete')
        tokenizer_sha = hashlib.sha256(json.dumps(metadata, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        identity = tokenizer_identity or self.context.contract['tokenizer']
        if tokenizer_sha != identity['service_tokenizer_sha256']:
            raise ModelError('Service tokenizer does not match frozen Qwen tokenizer identity')
        return {'service_tokenizer_sha256': tokenizer_sha,
                'model_family': family, 'configured_context_tokens': configured_context,
                'parameters': canonical_parameters(parameters),
                'template_sha256': hashlib.sha256(template.encode()).hexdigest(),
                'architecture_context_length': (shown.get('model_info') or {}).get('qwen35.context_length')}

    def identity(self):
        models = self.remote('/models')['data']
        requested = self.config['LOCAL_LLM_MODEL']
        selected = next((m for m in models if m['id'] == requested), None)
        if selected is None and ':' not in requested.rsplit('/', 1)[-1]:
            selected = next((m for m in models if m['id'] == requested + ':latest'), None)
        if selected is None:
            raise ModelError('Configured model is not advertised by the endpoint')
        proxy_catalog = None
        if (urllib.parse.urlparse(self.config['OPENAI_BASE_URL']).hostname in ('127.0.0.1', 'localhost', '::1')
                and selected.get('owned_by') in ('qoder', 'workbuddy')
                and ('context_window' not in selected or 'max_output_tokens' not in selected)):
            matches = [m for m in self.remote('/models?client_version=pi').get('models', [])
                       if m.get('slug') == selected['id']]
            if len(matches) != 1:
                raise ModelError('No unique proxy capacity declaration for configured model')
            entry = matches[0]
            proxy_catalog = {key: entry.get(key) for key in ('slug', 'context_window', 'max_context_window', 'max_tokens')}
            for standard, field in (('context_window', 'context_window'), ('max_output_tokens', 'max_tokens')):
                value = entry.get(field)
                if type(value) is not int or value <= 0 or (standard in selected and selected[standard] != value):
                    raise ModelError('Invalid or conflicting proxy capacity declaration')
                selected = {**selected, standard: value}
            maximum = entry.get('max_context_window')
            if maximum is not None and (type(maximum) is not int or maximum < selected['context_window']):
                raise ModelError('Invalid proxy maximum context declaration')
        catalog = None
        if urllib.parse.urlparse(self.config['OPENAI_BASE_URL']).hostname == 'token.sensenova.cn':
            catalog = {key: selected.get(key) for key in ('context_length', 'max_output_length',
                'supported_features', 'supported_sampling_parameters', 'quantization', 'pricing')}
            for standard, native_key in (('context_window', 'context_length'),
                                         ('max_output_tokens', 'max_output_length')):
                value = catalog[native_key]
                if type(value) is not int or value <= 0 or (standard in selected and selected[standard] != value):
                    raise ModelError('Invalid or conflicting SenseNova capacity metadata')
                selected = {**selected, standard: value}
        native = None
        if 'context_window' not in selected or 'max_output_tokens' not in selected:
            # Ollama's OpenAI model list omits capacities. Verify the native
            # service and the same model's digest instead of guessing a model.
            if urllib.parse.urlparse(self.config['OPENAI_BASE_URL']).hostname == 'api.deepseek.com':
                raise ModelError('Endpoint must advertise valid context_window and max_output_tokens')
            version = self.remote('/api/version').get('version')
            tags = self.remote('/api/tags').get('models', [])
            tag = next((m for m in tags if m.get('name') == selected['id']), None)
            if not isinstance(version, str) or not tag or not re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', tag.get('digest', '')):
                raise ModelError('Unverified Ollama service/model identity')
            request = urllib.request.Request(self.config['OPENAI_BASE_URL'].rstrip('/').removesuffix('/v1') + '/api/show',
                data=json.dumps({'model': selected['id'], 'verbose': False}).encode(),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+self.config['OPENAI_API_KEY']})
            try:
                with self.opener.open(request, timeout=min(30, self.budget.remaining())) as response:
                    shown = json.load(response)
            except Exception as exc:
                raise ModelError('Ollama model metadata unavailable: ' + type(exc).__name__) from None
            if shown.get('remote_model') or shown.get('remote_host') or ':cloud' in selected['id']:
                raise ModelError('Ollama cloud model requires an explicit capacity/pricing contract')
            parameters = shown.get('parameters', '')
            match = re.search(r'^num_ctx\s+(\d+)\s*$', parameters, re.MULTILINE)
            info = shown.get('model_info', {})
            architecture = info.get('general.architecture')
            maximum = info.get(str(architecture)+'.context_length')
            context = int(match.group(1)) if match else None
            if type(maximum) is not int or context is None or not 1 < context <= maximum:
                raise ModelError('Ollama must advertise an explicit valid num_ctx and architecture capacity')
            output_match = re.search(r'^num_predict\s+(-?\d+)\s*$', parameters, re.MULTILINE)
            num_predict = int(output_match.group(1)) if output_match else -1
            if num_predict not in (-1, -2) and num_predict <= 0:
                raise ModelError('Invalid Ollama num_predict')
            output = min(context-1, num_predict) if num_predict > 0 else context-1
            template = shown.get('template')
            if not isinstance(template, str) or not template:
                raise ModelError('Ollama chat template unavailable')
            native = {'provider':'ollama', 'version':version, 'digest':tag['digest'],
                'parameters':canonical_parameters(parameters), 'architecture_context_length':maximum,
                'template_sha256':hashlib.sha256(template.encode()).hexdigest(),
                'output_capacity_source':'positive num_predict bounded by context' if num_predict > 0 else 'derived context-1 bound; provider has no independent output cap',
                'num_predict':num_predict, 'cloud':False}
            selected = {**selected, 'digest':tag['digest'], 'context_window':context, 'max_output_tokens':output}
        from .budget_policy import sica_budget
        try:
            limits = sica_budget(selected['max_output_tokens'], selected['context_window'])
        except (KeyError, ValueError) as exc:
            raise ModelError('Endpoint must advertise valid context_window and max_output_tokens') from exc
        self.model = selected['id']
        self.context = ContextBudget(selected)
        official = urllib.parse.urlparse(self.config['OPENAI_BASE_URL']).hostname == 'api.deepseek.com'
        self.prefix_protocol = 'deepseek-beta' if official and self.model.startswith('deepseek-') else None
        self.pricing = ({'source':'https://api-docs.deepseek.com/quick_start/pricing/',
                         'checked_date':'2026-10-12', 'basis':'published peak rates; conservative cost upper bound',
                         'cached_input_per_million':.006, 'input_per_million':.3,
                         'output_per_million':1.2}
                        if official and self.model == 'deepseek-flash' else None)
        if catalog is not None:
            tariff = catalog['pricing']
            # Only explicit all-zero rates are unambiguous without currency/unit
            # documentation. Never infer paid rates or ignore extra charge fields.
            required = {'prompt', 'completion', 'input_cache_read', 'image', 'request'}
            if (isinstance(tariff, dict) and set(tariff) == required
                    and all(type(value) in (str, int, float) and str(value) in ('0', '0.0')
                            for value in tariff.values())):
                self.pricing = {'source': 'authenticated SenseNova /models pricing',
                    'basis': 'explicit all-zero service tariff; not an independently verified invoice',
                    'cached_input_per_million': 0.0, 'input_per_million': 0.0, 'output_per_million': 0.0}
        self.service_identity = {'capacity_source':'authenticated /models',
            'context_window':selected['context_window'], 'max_output_tokens':selected['max_output_tokens'],
            'owned_by':selected.get('owned_by'), 'api_capabilities':selected.get('api_capabilities'),
            'prefix_protocol':self.prefix_protocol, 'pricing':self.pricing}
        if catalog is not None:
            self.service_identity.update(capacity_source='authenticated SenseNova /models field mapping',
                                         sensenova_catalog=catalog)
        if proxy_catalog is not None:
            self.service_identity.update(capacity_source='authenticated loopback proxy /models?client_version=pi',
                proxy_catalog=proxy_catalog, underlying_model_verifiable=False)
        if self.config.get('RSI_BILLING_MODE'):
            if self.config['RSI_BILLING_MODE'] != 'subscription' or proxy_catalog is None:
                raise ModelError('Subscription contract requires the verified local proxy catalog')
            self.service_identity['billing_contract'] = {'mode': 'subscription',
                'source': 'explicit subscription billing mode',
                'usd_token_tariff_available': False, 'benchmark_usd_threshold_reproduced': False}
        if native is not None:
            self.service_identity.update(capacity_source='authenticated Ollama /api/show', ollama=native)
            if architecture == 'qwen35':
                from .core import read
                from .context_budget import LOCK_PATH
                tokenizer_identity = read(LOCK_PATH)
                verified = self.service_configuration(self.model, tokenizer_identity)
                if canonical_parameters(verified['parameters']) != native['parameters'] or verified['template_sha256'] != native['template_sha256']:
                    raise ModelError('Service metadata changed during tokenizer verification')
                self.context.enable_verified_text_audit(tokenizer_identity)
                self.service_identity['input_integrity'] = self.context.contract['input_integrity']
        return {'requested':requested, 'model':self.model,
                'weights':selected.get('digest'), 'weights_verifiable':bool(selected.get('digest')),
                'temperature_requested':0, 'thinking':'provider default', 'reasoning_effort':'provider default',
                'max_tokens':limits['model_max_output_tokens'], 'model_concurrency':1}

    def budget_limits(self):
        if self.context is None:
            raise ModelError('Model identity/capabilities must be frozen before use')
        from .budget_policy import sica_budget
        limits = sica_budget(self.context.contract['output_tokens'], self.context.contract['total_tokens'])
        if getattr(self, 'pricing', None):
            limits.update(cost_threshold_enforced=True, cost_limitation=None, pricing=self.pricing)
        return limits

    def session(self, label, solver=None, seconds=600, calls=None):
        token = random_secrets.token_hex(32)
        remaining = self.budget.remaining()
        self.sessions[token] = dict(label=label, solver=solver, calls=0, tokens=0,
                                    cwd='/testbed', started=time.time(), iterations={},
                                    prompt_tokens=0, completion_tokens=0, cached_tokens=0,
                                    cost_upper_bound_usd=0.0, cost_limit_usd=3 if solver else None,
                                    cost_accounting_available=bool(getattr(self, 'pricing', None)),
                                    unmetered_calls=0,
                                    deadline=time.time() + (remaining if seconds is None else min(seconds, remaining)),
                                    max_calls=calls, tool_calls=0, errors=[], terminal_reason=None,
                                    rejected_calls=[], inflight=False)
        return token

    def finish(self, token):
        # A killed controller may leave its last HTTP request finishing on the
        # broker thread. Snapshot usage only after that request is accounted for.
        with self._state_condition:
            s = self.sessions[token]
            while s.get('inflight'):
                self._state_condition.wait()
            data = self.sessions.pop(token)
            return {k: (None if k == 'deadline' and not math.isfinite(v) else v)
                    for k, v in data.items() if k not in ('solver', 'stage_tools')}

    def status(self, token):
        # Readable even after expiration; never disclose session credentials or container identity.
        s = self.sessions[token]
        return {'calls':s['calls'], 'max_calls':s['max_calls'],
                'remaining_calls':None if s['max_calls'] is None else max(0,s['max_calls']-s['calls']),
                'agent_iterations':dict(s['iterations']),
                'remaining_seconds':max(0,s['deadline']-time.time()) if math.isfinite(s['deadline']) else None,
                'terminal_reason':s['terminal_reason'], 'rejected_calls':list(s['rejected_calls'])}

    def reject(self, s, reason, basis='host_budget', terminal=True, kind='model_rejection'):
        s['terminal_reason'] = reason if terminal else None
        s['errors'].append(reason)
        event = dict(kind=kind, label=s['label'], reason=reason,
                     basis=basis, calls=s['calls'], actual_call=False)
        s['rejected_calls'].append({'reason':reason, 'basis':basis, 'calls':s['calls']})
        self.events.append(event)
        append_event(self.root, event)
        raise ModelError(reason)

    def check_session(self, s, model_request=True):
        self._raise_if_paused()
        kind = 'model_rejection' if model_request else 'tool_rejection'
        try:
            self.budget.check()
        except BudgetExceeded:
            self.reject(s, 'TimeExceeded', 'experiment_budget',kind=kind)
        if time.time() >= s['deadline']:
            self.reject(s, 'TimeExceeded', 'session_deadline',kind=kind)
        if model_request and s['max_calls'] is not None and s['calls'] >= s['max_calls']:
            self.reject(s, 'LimitsExceeded')
        if s['cost_limit_usd'] is not None:
            local_ollama = getattr(self, 'service_identity', {}).get('ollama', {}).get('cloud') is False
            subscription = getattr(self, 'service_identity', {}).get('billing_contract', {}).get('mode') == 'subscription'
            if not getattr(self, 'pricing', None) and self.context.provider and not local_ollama and not subscription:
                self.reject(s, 'Benchmark pricing contract unavailable', 'pricing_contract')
            if s['cost_upper_bound_usd'] >= s['cost_limit_usd']:
                self.reject(s, 'CostExceeded', 'published_peak_cost_upper_bound',kind=kind)

    def request_json(self, request, deadline, label, call, session_token=None, event_id=None):
        """Bound one request's wall time and quarantine late transport replies."""
        if session_token is None and event_id is None:
            active = self._active_request_meta
            if active is not None:
                session_token, event_id = active
        seconds = min(deadline-time.time(), self.budget.remaining())
        if seconds <= 0:
            raise ModelError('TimeExceeded')
        watchdog = self.transport_policy.get('request_timeout_seconds')
        watchdog_limited = watchdog is not None and (not math.isfinite(seconds) or watchdog < seconds)
        wait_seconds = min(seconds, watchdog) if watchdog is not None else (seconds if math.isfinite(seconds) else None)
        replies = queue.Queue(maxsize=1)
        expired = threading.Event()
        responses = []

        def fetch():
            try:
                with self.opener.open(request, timeout=wait_seconds) as response:
                    responses.append(response)
                    result = json.load(response)
                if expired.is_set():
                    self._record_late_response(session_token, label, call, event_id, result)
                    return
                try:
                    replies.put_nowait((True, result))
                except queue.Full:
                    # A timed-out caller owns the decision; a late reply is
                    # accounted by the branch above and never reaches it.
                    self._record_late_response(session_token, label, call, event_id, result)
            except Exception as exc:
                try:
                    replies.put_nowait((False,exc))
                except queue.Full:
                    pass

        threading.Thread(target=fetch,daemon=True).start()
        try:
            success, result = replies.get(timeout=wait_seconds)
        except queue.Empty:
            expired.set()
            for response in responses:
                # Best effort remote cancellation; caller never waits beyond its deadline.
                connection = getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                if connection is not None:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            raise ModelError('TransportTimeout' if watchdog_limited else 'TimeExceeded') from None
        if not success:
            raise result
        return result

    def complete(self, token, payload, agent='main', role='orchestrator'):
        with self.request_lock:
            try:
                return self._complete_impl(token, payload, agent, role)
            finally:
                self._finish_inflight(token)

    def _complete_impl(self, token, payload, agent='main', role='orchestrator'):
        with self.lock:
            s = self.sessions[token]
            self._raise_if_paused()
            s['inflight'] = True
            limits = self.budget_limits()
            self.check_session(s)
            if role not in ('orchestrator', *limits['role_iterations']) or not isinstance(agent,str) or len(agent)>128:
                self.reject(s, 'Invalid agent identity', 'text_protocol', terminal=False)
            iteration = s['iterations'].get(agent, 0)
            iteration_limit = limits['role_iterations'].get(role, limits['main_iterations'])
            if iteration >= iteration_limit:
                self.reject(s, 'LimitsExceeded', 'agent_iterations')
            s['iterations'][agent] = iteration + 1
            s['terminal_reason'] = None
            original = payload.get('messages')
            if any(payload.get(k) for k in ('tools', 'tool_choice', 'functions', 'function_call')):
                self.reject(s, 'Native tools are outside the text bash protocol', 'text_protocol', terminal=False)
            try:
                messages = normalize_messages(original)
            except ModelError as exc:
                self.reject(s, str(exc), 'text_protocol', terminal=False)
            original_chars = len(json.dumps(original, ensure_ascii=False))
            current = copy.deepcopy(messages)
            accumulated = {'content':'', 'reasoning_content':''}
            aggregate_usage = dict(total_tokens=0, prompt_tokens=0, completion_tokens=0)
            maximum = limits['summary_max_requests'] if s['label'] == 'summary' else limits['completion_max_requests']
            aggregate_cached = 0
            for attempt in range(maximum):
                self.check_session(s)
                measurement = self.context.measure(current)
                append_event(self.root, dict(kind='message_normalization', label=s['label'], protocol=TEXT_PROTOCOL,
                    original_characters=original_chars, normalized_characters=len(json.dumps(current,ensure_ascii=False)), **measurement))
                if not self.context.fits(measurement):
                    self.reject(s, 'ContextLimitExceeded', 'host_context')
                wire = copy.deepcopy(current)
                prefix = getattr(self, 'prefix_protocol', None) == 'deepseek-beta' and wire and wire[-1]['role'] == 'assistant'
                base = self.config['OPENAI_BASE_URL'].rstrip('/')
                if prefix:
                    wire[-1]['prefix'] = True
                    base = urllib.parse.urlunparse(urllib.parse.urlparse(base)._replace(path='/beta'))
                body = {'model':self.model, 'messages':wire, 'temperature':0,
                        'max_tokens':limits['model_max_output_tokens'], 'stream':False}
                request = urllib.request.Request(base + '/chat/completions', data=json.dumps(body).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+self.config['OPENAI_API_KEY']})
                s['calls'] += 1
                start = time.time()
                event = dict(kind='model_call', label=s['label'], call=s['calls'], model=self.model,
                    protocol=TEXT_PROTOCOL, agent=agent, role=role, iteration=iteration+1,
                    continuation_index=attempt, logical_completion_final=False, original_messages=messages,
                    original_characters=original_chars, normalized_characters=len(json.dumps(current,ensure_ascii=False)), **measurement)
                event['event_id'] = f"{s['label']}:call:{s['calls']}:attempt:{attempt}"
                event['wire_messages_sha256'] = hashlib.sha256(json.dumps(wire,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                try:
                    # Do not hold the state lock across provider I/O.  finish()
                    # waits on ``inflight`` and can therefore account usage
                    # without queueing behind a slow transport.
                    self.lock.release()
                    try:
                        self._active_request_meta = (token, event['event_id'])
                        result = self.request_json(request, s['deadline'], s['label'], s['calls'])
                    finally:
                        self._active_request_meta = None
                        self.lock.acquire()
                    event.update(messages=wire, response=self._safe_response(result))
                    response_fault = self._response_fault(result)
                    usage = result.get('usage', {})
                    if response_fault is not None:
                        event['transport_fault'] = response_fault
                        if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in aggregate_usage):
                            event['missing_usage'] = True
                        else:
                            # A provider can attach billable usage to an
                            # error completion.  Record valid counts without
                            # ever handing the failed response to the agent.
                            cached = usage.get('prompt_cache_hit_tokens',
                                               (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0))
                            if not (type(cached) is int and 0 <= cached <= usage['prompt_tokens']):
                                cached = 0
                                event['cache_usage_invalid'] = True
                            for key in aggregate_usage:
                                aggregate_usage[key] += usage[key]
                            for key in ('prompt_tokens', 'completion_tokens'):
                                s[key] += usage[key]
                            s['tokens'] += usage['total_tokens']
                            s['cached_tokens'] += cached
                            event['usage'] = usage
                            if getattr(self, 'pricing', None):
                                price = self.pricing
                                cost = ((usage['prompt_tokens'] - cached) * price['input_per_million']
                                        + cached * price['cached_input_per_million']
                                        + usage['completion_tokens'] * price['output_per_million']) / 1e6
                                s['cost_upper_bound_usd'] += cost
                                event['cost_upper_bound_usd'] = cost
                        raise ModelError('Upstream completion error')
                    if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in aggregate_usage):
                        event['transport_fault'] = 'missing_usage'
                        raise ModelError('Endpoint omitted usage; token accounting unavailable')
                    cached = usage.get('prompt_cache_hit_tokens', (usage.get('prompt_tokens_details') or {}).get('cached_tokens',0))
                    cache_valid = type(cached) is int and 0 <= cached <= usage['prompt_tokens']
                    if not cache_valid:
                        cached = 0  # Conservative cost bound; retain the valid charged token counts.
                        event['cache_usage_invalid'] = True
                    for key in aggregate_usage:
                        aggregate_usage[key] += usage[key]
                    for key in ('prompt_tokens','completion_tokens'):
                        s[key] += usage[key]
                    s['tokens'] += usage['total_tokens']
                    s['cached_tokens'] += cached
                    aggregate_cached += cached
                    event['usage'] = usage
                    if getattr(self,'pricing',None):
                        price = self.pricing
                        cost = ((usage['prompt_tokens']-cached)*price['input_per_million'] + cached*price['cached_input_per_million']
                                + usage['completion_tokens']*price['output_per_million']) / 1e6
                        s['cost_upper_bound_usd'] += cost
                        event['cost_upper_bound_usd'] = cost
                    if not cache_valid:
                        raise ModelError('Invalid cached token usage')
                    estimate = measurement['host_prompt_tokens_estimate']
                    if estimate is not None:
                        event['service_prompt_minus_host_estimate'] = usage['prompt_tokens'] - estimate
                    if self.context.provider and self.context.tokenizer is not None:
                        # A generous framing tolerance avoids pretending to know
                        # provider serialization. Gross missing text fails closed.
                        gap = measurement['host_text_tokens'] - usage['prompt_tokens']
                        allowance = measurement['host_framing_tokens']
                        event['input_integrity'] = {'text_token_deficit':gap,'tolerance_tokens':allowance,
                            'status':'failed' if gap > allowance else 'no_gross_truncation_detected',
                            'exact_preservation_proven':False}
                        if gap > allowance:
                            s['terminal_reason'] = 'InputIntegrityError'
                            raise ModelError('InputIntegrityError')
                    combined = (usage['prompt_tokens'] + usage['completion_tokens'] if self.context.provider
                                else usage['prompt_tokens'] + measurement['reserved_output_tokens'])
                    if combined > measurement['total_token_budget']:
                        event.update(context_budget_basis='service_prompt_tokens',
                            response_adaptation='not returned: service usage exceeded frozen capacity')
                        s['terminal_reason'] = 'ContextLimitExceeded'
                        raise ModelError('ContextLimitExceeded')
                    choices = result.get('choices', [])
                    choice = choices[0] if len(choices)==1 else {}
                    message = choice.get('message', {})
                    for key in accumulated:
                        value = (message.get('reasoning_content',message.get('reasoning')) if key=='reasoning_content' else message.get(key)) or ''
                        if not isinstance(value,str):
                            raise ModelError('Invalid assistant text')
                        accumulated[key] += value
                    clipped = choice.get('finish_reason') in ('length','insufficient_system_resource') and s['label'] != 'summary'
                    if clipped:
                        if attempt+1 >= maximum:
                            raise ModelError('ContinuationLimitExceeded')
                        if getattr(self,'prefix_protocol',None) != 'deepseek-beta':
                            raise ModelError('Provider prefix continuation unavailable')
                        current = copy.deepcopy(messages)
                        prefix_message = dict(role='assistant', **accumulated)
                        if current and current[-1]['role']=='assistant':
                            current[-1] = prefix_message
                        else:
                            current.append(prefix_message)
                        continue
                    combined_response = copy.deepcopy(result)
                    if choices:
                        combined_response['choices'][0]['message'].update(accumulated)
                        if 'reasoning' in message and 'reasoning_content' not in message:
                            combined_response['choices'][0]['message']['reasoning'] = combined_response['choices'][0]['message'].pop('reasoning_content')
                    combined_response['usage'] = {**aggregate_usage, 'prompt_cache_hit_tokens':aggregate_cached,
                        'prompt_tokens_details':{'cached_tokens':aggregate_cached}}
                    returned, adaptation = adapt_response(combined_response)
                    event.update(agent_response=returned, response_adaptation=adaptation, logical_completion_final=True)
                    return returned
                except Exception as exc:
                    reason = str(exc) if isinstance(exc,ModelError) else type(exc).__name__
                    if isinstance(exc, urllib.error.HTTPError):
                        event['http_status'] = exc.code
                        event['transport_fault'] = 'http_status'
                    if reason == 'TimeExceeded':
                        s['terminal_reason'] = reason
                    if isinstance(exc,urllib.error.HTTPError) and exc.code == 400:
                        # Classify an endpoint context failure without retaining arbitrary error bodies/secrets.
                        try:
                            error = json.loads(exc.read()).get('error',{})
                            if error.get('code') in ('context_length_exceeded','context_window_exceeded'):
                                reason = 'ContextLimitExceeded'
                                s['terminal_reason'] = reason
                        except (ValueError,AttributeError):
                            pass
                    s['errors'].append(reason)
                    if 'usage' not in event:
                        s['unmetered_calls'] += 1
                    event['error'] = reason
                    category = event.get('transport_fault')
                    status = event.get('http_status')
                    if category is None:
                        if reason == 'TransportTimeout' or 'timeout' in type(exc).__name__.lower():
                            category = 'transport_timeout'
                        elif (isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout))
                              or not isinstance(exc, ModelError)):
                            category = 'transport_error'
                    if category is not None and not self._pause_enabled(category, status):
                        if event.get('missing_usage') and self._pause_enabled('missing_usage'):
                            category = 'missing_usage'
                        else:
                            category = None
                    if category is not None and self._pause_enabled(category, status):
                        self._pause_transport(s, category, event, status=status)
                    event['metrics'] = self._safe_metrics(s)
                    raise ModelError(reason) from None
                finally:
                    event['seconds'] = time.time()-start
                    self.events.append(event)
                    append_event(self.root,event)

    def stage_boundary(self, token):
        with self.request_lock:
            tools = self.sessions[token].get('stage_tools')
            if tools is not None:
                tools.close()
            return {'status':'closed'}

    def tool(self, token, action):
        with self.request_lock:
            try:
                return self._tool_impl(token, action)
            finally:
                self._finish_inflight(token)

    def _tool_impl(self, token, action):
        with self.lock:
            s = self.sessions[token]
            self._raise_if_paused()
            self.check_session(s, model_request=False)
            s['inflight'] = True
        if not s['solver']:
            raise ModelError('Tool session unavailable')
        command = action.get('command', '')
        if not isinstance(command, str):
            raise ModelError('Invalid command')
        s['tool_calls'] += 1
        limits = self.budget_limits()
        duration = action.get('generous_expected_duration', limits['command_default_seconds'])
        if not isinstance(duration,(float,int)) or isinstance(duration,bool) or not limits['command_min_seconds'] <= duration <= limits['command_max_seconds']:
            raise ModelError('Command duration must be between 5 and 1800 seconds')
        seconds = max(.1, min(duration, limits['tool_timeout_seconds'], s['deadline']-time.time(), self.budget.remaining()))
        args = ['docker', 'exec', '-w', s['cwd'], s['solver'], 'timeout', '-k', '2', str(seconds),
                'bash', '-lc', command]
        if s.get('stage_tools') is not None:
            args = s['stage_tools'].command(command, seconds)
        with tempfile.TemporaryFile() as output:
            try:
                p = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, timeout=seconds+5)
                code, error = p.returncode, ''
            except subprocess.TimeoutExpired:
                code, error = -1, 'CommandTimeout'
            output.seek(0)
            text = output.read().decode(errors='replace')
        append_event(self.root, dict(kind='tool_call', label=s['label'], command=command,
                                    returncode=code, output=text, exception_info=error))
        return {'output': text, 'returncode': code, 'exception_info': error}

    def summary(self, messages, label):
        token = self.session(label, seconds=None)
        try:
            return self.complete(token, {'messages': messages})['choices'][0]['message']['content'] or ''
        finally:
            self.finish(token)
