"""Host-owned model and tool budgets. No API credential enters an agent container."""
from __future__ import annotations

import json
import copy
import hashlib
import re
import secrets as random_secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import append_event, secrets, BudgetExceeded
from .context_budget import ContextBudget


class ModelError(RuntimeError):
    pass


TEXT_PROTOCOL = 'mini-text-bash-v3'


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


def adapt_response(response):
    """Promote only an unambiguous complete action, never free-form reasoning."""
    returned = copy.deepcopy(response)
    choices = returned.get('choices')
    reason = 'unchanged'
    if not isinstance(choices, list) or len(choices) != 1:
        return returned, 'unchanged: expected one choice'
    choice = choices[0]
    if not isinstance(choice, dict):
        return returned, 'unchanged: invalid choice'
    message = choice.get('message', {})
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return returned, 'unchanged: invalid assistant message'
    if message.get('content') not in (None, ''):
        return returned, 'unchanged: nonempty content'
    if choice.get('finish_reason') != 'stop':
        return returned, 'unchanged: non-stop finish'
    if any(message.get(key) for key in ('tool_calls', 'function_call', 'refusal')):
        return returned, 'unchanged: native tool or refusal'
    fields = {key: message[key] for key in ('reasoning_content', 'reasoning') if message.get(key) is not None}
    if not fields or any(not isinstance(value, str) for value in fields.values()):
        return returned, 'unchanged: missing or invalid reasoning'
    if len(set(fields.values())) != 1:
        return returned, 'unchanged: conflicting reasoning fields'
    reasoning = next(iter(fields.values()))
    # Count markers/fences separately so malformed extra actions cannot hide
    # outside the one complete match. Only the matched block reaches content.
    matches = list(re.finditer(r'```mswea_bash_command[ \t]*\n((?:(?!```).)+?)\n```', reasoning, re.DOTALL))
    if len(matches) == 1 and reasoning.count('mswea_bash_command') == 1 and reasoning.count('```') == 2 and matches[0].group(1).strip():
        match = matches[0]
        message['content'] = match.group(0)
        for key in fields:
            message[key] = (reasoning[:match.start()] + reasoning[match.end():]).strip()
        reason = 'promoted single complete reasoning action; removed action from ' + ','.join(fields)
    else:
        reason = 'unchanged: reasoning has no unique complete action'

    return returned, reason


class Broker:
    def __init__(self, root, budget, context=None):
        self.root, self.budget = root, budget
        self.context = context if context is not None else ContextBudget()
        self.config = secrets()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.lock = threading.Lock()
        self.sessions = {}
        self.events = []
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
                if not 0 < size <= 2_000_000:
                    self.send_error(413)
                    return
                try:
                    payload = json.loads(self.rfile.read(size))
                    if self.path == '/v1/chat/completions':
                        out = broker.complete(token, payload)
                    elif self.path == '/status':
                        out = broker.status(token)
                    elif self.path == '/tool':
                        out = broker.tool(token, payload)
                    else:
                        self.send_error(404)
                        return
                    status = 200
                except Exception as exc:
                    out = {'error': {'message': str(exc), 'type': type(exc).__name__}}
                    status = 400
                    if str(exc) == 'ContextLimitExceeded':
                        out['error'].update(type='invalid_request_error', code='context_length_exceeded')
                        out['error']['message'] = 'exceed context limit: host token context budget of 32768 including 2048 output reserve (ContextLimitExceeded)'
                    elif str(exc) in ('TimeExceeded', 'LimitsExceeded'):
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

    def service_configuration(self, model):
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
        if tokenizer_sha != self.context.contract['tokenizer']['service_tokenizer_sha256']:
            raise ModelError('Service tokenizer does not match frozen Qwen tokenizer identity')
        return {'service_tokenizer_sha256': tokenizer_sha,
                'model_family': family, 'configured_context_tokens': configured_context,
                'parameters': parameters,
                'template_sha256': hashlib.sha256(template.encode()).hexdigest(),
                'architecture_context_length': (shown.get('model_info') or {}).get('qwen35.context_length')}

    def identity(self):
        models = self.remote('/models')['data']
        requested = self.config['LOCAL_LLM_MODEL']
        ids = [m['id'] for m in models]
        selected = requested if requested in ids else requested + ':latest'
        if selected not in ids:
            raise ModelError('Configured model is not advertised by the endpoint')
        self.model = selected
        weights = None
        try:
            tags = self.remote('/api/tags')
            weights = next(({'name': m['name'], 'digest': m.get('digest'), 'details': m.get('details')}
                            for m in tags['models'] if m['name'] == selected), None)
        except ModelError:
            pass
        self.service_identity = self.service_configuration(selected)
        return {'requested': requested, 'model': selected, 'weights': weights,
                'temperature': 0, 'max_tokens': 2048, 'model_concurrency': 1}

    def session(self, label, solver=None, seconds=600, calls=30):
        token = random_secrets.token_hex(32)
        self.sessions[token] = dict(label=label, solver=solver, calls=0, tokens=0,
                                    cwd='/testbed',
                                    prompt_tokens=0, completion_tokens=0, cached_tokens=0,
                                    unmetered_calls=0,
                                    deadline=time.time() + min(seconds, self.budget.remaining()),
                                    max_calls=calls, tool_calls=0, errors=[], terminal_reason=None, rejected_calls=[])
        return token

    def finish(self, token):
        # A killed controller may leave its last HTTP request finishing on the
        # broker thread. Snapshot usage only after that request is accounted for.
        with self.lock:
            data = self.sessions.pop(token)
            return {k: v for k, v in data.items() if k != 'solver'}

    def status(self, token):
        # Readable even after expiration; never disclose session credentials or container identity.
        s = self.sessions[token]
        return {'calls':s['calls'], 'max_calls':s['max_calls'],
                'remaining_calls':max(0,s['max_calls']-s['calls']),
                'remaining_seconds':max(0,s['deadline']-time.time()),
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

    def complete(self, token, payload):
        with self.lock:
            s = self.sessions[token]
            try:
                self.budget.check()
            except BudgetExceeded:
                self.reject(s, 'TimeExceeded', 'experiment_budget')
            if time.time() >= s['deadline']:
                self.reject(s, 'TimeExceeded')
            if s['calls'] >= s['max_calls']:
                self.reject(s, 'LimitsExceeded')
            s['terminal_reason'] = None
            original = payload.get('messages')
            if any(payload.get(k) for k in ('tools', 'tool_choice', 'functions', 'function_call')):
                self.reject(s, 'Native tools are outside the text bash protocol', 'text_protocol', terminal=False)
            try:
                messages = normalize_messages(original)
            except ModelError as exc:
                self.reject(s, str(exc), 'text_protocol', terminal=False)
            original_chars = len(json.dumps(original, ensure_ascii=False))
            normalized_chars = len(json.dumps(messages, ensure_ascii=False))
            audit = dict(kind='message_normalization', label=s['label'], protocol=TEXT_PROTOCOL,
                         original_characters=original_chars, normalized_characters=normalized_chars)
            measurement = self.context.measure(messages)
            audit.update(measurement)
            append_event(self.root, audit)
            if not self.context.fits(measurement):
                self.reject(s, 'ContextLimitExceeded', 'host_context')
            s['terminal_reason'] = None
            s['calls'] += 1
            body = {'model': self.model, 'messages': messages, 'temperature': 0,
                    'max_tokens': 2048, 'stream': False}
            request = urllib.request.Request(self.config['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions',
                      data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                      'Authorization': 'Bearer ' + self.config['OPENAI_API_KEY']})
            start = time.time()
            event = dict(kind='model_call', label=s['label'], call=s['calls'], model=self.model,
                         protocol=TEXT_PROTOCOL, original_characters=original_chars,
                         normalized_characters=normalized_chars, **measurement)
            try:
                with self.opener.open(request, timeout=max(.1, min(180, s['deadline'] - time.time()))) as f:
                    result = json.load(f)
                event.update(messages=messages, response=result)
                usage = result.get('usage', {})
                if not isinstance(usage, dict) or any(not isinstance(usage.get(key), int) or isinstance(usage.get(key), bool) or usage[key] < 0 for key in ('total_tokens', 'prompt_tokens', 'completion_tokens')):
                    raise ModelError('Endpoint omitted usage; token accounting unavailable')
                for key in ('prompt_tokens', 'completion_tokens'):
                    s[key] += usage.get(key, 0)
                s['tokens'] += usage['total_tokens']
                s['cached_tokens'] += (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
                event.update(usage=usage, messages=messages, response=result)
                actual_prompt = usage.get('prompt_tokens')
                if isinstance(actual_prompt, int):
                    event['service_prompt_minus_host_estimate'] = actual_prompt - measurement['host_prompt_tokens_estimate']
                if isinstance(actual_prompt, int) and actual_prompt + measurement['reserved_output_tokens'] > measurement['total_token_budget']:
                    event.update(context_budget_basis='service_prompt_tokens',
                                 response_adaptation='not returned: actual service prompt exceeded frozen budget')
                    s['terminal_reason'] = 'ContextLimitExceeded'
                    raise ModelError('ContextLimitExceeded')
                returned, adaptation = adapt_response(result)
                event.update(agent_response=returned, response_adaptation=adaptation)
                return returned
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ModelError) else type(exc).__name__
                s['errors'].append(reason)
                if 'usage' not in event:
                    s['unmetered_calls'] += 1
                event['error'] = reason
                raise ModelError(reason) from None
            finally:
                event['seconds'] = time.time() - start
                self.events.append(event)
                append_event(self.root, event)

    def tool(self, token, action):
        s = self.sessions[token]
        try:
            self.budget.check()
        except BudgetExceeded:
            self.reject(s, 'TimeExceeded', 'experiment_budget', kind='tool_rejection')
        if time.time() >= s['deadline']:
            self.reject(s, 'TimeExceeded', 'session_deadline', kind='tool_rejection')
        if not s['solver']:
            raise ModelError('Tool session unavailable')
        command = action.get('command', '')
        if not isinstance(command, str) or len(command) > 100000:
            raise ModelError('Invalid command')
        s['tool_calls'] += 1
        seconds = max(1, min(60, int(s['deadline'] - time.time()), int(self.budget.remaining())))
        args = ['docker', 'exec', '-w', s['cwd'], s['solver'], 'timeout', '-k', '2', str(seconds),
                'bash', '-lc', command]
        with tempfile.TemporaryFile() as output:
            try:
                p = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, timeout=seconds+5)
                code, error = p.returncode, ''
            except subprocess.TimeoutExpired:
                code, error = -1, 'CommandTimeout'
            output.seek(0)
            text = output.read(1024*1024).decode(errors='replace')
        append_event(self.root, dict(kind='tool_call', label=s['label'], command=command,
                                    returncode=code, output=text, exception_info=error))
        return {'output': text, 'returncode': code, 'exception_info': error}

    def summary(self, messages, label):
        token = self.session(label, calls=1, seconds=180)
        try:
            return self.complete(token, {'messages': messages})['choices'][0]['message']['content'] or ''
        finally:
            self.finish(token)
