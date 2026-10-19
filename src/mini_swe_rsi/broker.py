"""Host-owned model and tool budgets. No API credential enters an agent container."""
from __future__ import annotations

import json
import math
import copy
import hashlib
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

from .core import append_event, secrets, read, read_regular_json, write
from .model_protocol import ModelError, TEXT_PROTOCOL, normalize_messages, adapt_response
from .model_identity import resolve_identity, canonical_parameters
from .candidate_jobs import (CandidateJobs, validate_candidate_job_record,
                             CANDIDATE_JOB_METADATA_FIELDS, CANDIDATE_PUBLIC_IDENTITY_FIELDS,
                             CANDIDATE_JOB_STATES)


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


class Broker:
    def __init__(self, root, context=None, transport_policy=None):
        self.root = root
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
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.lock = threading.Lock()
        # The host still serializes model/tool work, while this separate
        # condition lets finish() wait without holding up the transport.
        self._state_condition = threading.Condition(self.lock)
        self.request_lock = threading.Lock()
        self._active_request_meta = None
        self._candidate_registry = CandidateJobs(root)
        self.candidate_lock = self._candidate_registry.lock
        self.sessions = {}
        # Candidate checks are registered by the host before a container can
        # request them.  A remote client supplies only the opaque job id and
        # frozen check parameters; it can never choose a host path or task.
        self.candidate_jobs = self._candidate_registry.jobs
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

    def _raise_if_paused(self):
        if self.pause_event.is_set():
            raise ModelError('TransportPaused')

    @staticmethod
    def _valid_usage(usage):
        keys = ('total_tokens', 'prompt_tokens', 'completion_tokens')
        return (isinstance(usage, dict)
                and all(type(usage.get(key)) is int and usage[key] >= 0 for key in keys))

    def _account_usage(self, session, usage, aggregate, event):
        """Account one valid provider usage record and return cache details.

        Error and successful completions share the same token ledger. ``event``
        retains the original usage; invalid cache counts are flagged and excluded
        from cached-token totals without discarding valid prompt/output usage.
        """
        if 'prompt_cache_hit_tokens' in usage:
            # An explicit null is a provider accounting error; it must not be
            # treated as an omitted cache field.
            cached = usage['prompt_cache_hit_tokens']
        else:
            details = usage.get('prompt_tokens_details')
            if details:
                # Truthy non-mappings used to fail while reading ``.get``;
                # retain the conservative invalid-cache result explicitly.
                cached = details.get('cached_tokens', 0) if isinstance(details, dict) else None
            else:
                cached = 0
        cache_valid = type(cached) is int and 0 <= cached <= usage['prompt_tokens']
        if not cache_valid:
            cached = 0
            event['cache_usage_invalid'] = True
        for key in aggregate:
            aggregate[key] += usage[key]
        for key in ('prompt_tokens', 'completion_tokens'):
            session[key] += usage[key]
        session['tokens'] += usage['total_tokens']
        session['cached_tokens'] += cached
        event['usage'] = usage
        return cache_valid, cached

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
        if fault is None:
            return result
        safe = {key: result[key] for key in ('id', 'object', 'created', 'model') if key in result}
        error = result.get('error')
        if isinstance(error, dict):
            safe_error = {key: error[key] for key in ('type', 'code') if key in error}
        else:
            safe_error = {'type': fault}
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
        append_event(self.root, late)

    def _finish_inflight(self, token):
        with self._state_condition:
            s = self.sessions.get(token)
            if s is not None:
                s['inflight'] = False
            self._state_condition.notify_all()

    def register_candidate_job(self, job_id, callback, metadata):
        self._raise_if_paused()
        self._candidate_registry.register(job_id, callback, metadata)

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
        identity_keys = CANDIDATE_PUBLIC_IDENTITY_FIELDS
        bound = {k: job['metadata'][k] for k in identity_keys}
        if payload != bound:
            raise ModelError('Candidate check identity differs from host registration')
        for key, actual in (('agent', agent), ('role', role)):
            if job['metadata'][key] != actual:
                raise ModelError('Candidate check execution identity differs from host registration')
        expected_session_hash = job['metadata']['session_token_sha256']
        if hashlib.sha256(token.encode()).hexdigest() != expected_session_hash:
            raise ModelError('Candidate check session differs from host registration')
        return self._candidate_registry.execute(job_id, job, payload)

    def remote(self, path):
        url = self.config['OPENAI_BASE_URL'].rstrip('/')
        if path.startswith('/api/'):
            url = url.removesuffix('/v1')
        request = urllib.request.Request(url + path, headers={
            'Authorization': 'Bearer ' + self.config['OPENAI_API_KEY']})
        try:
            with self.opener.open(request, timeout=30) as f:
                return json.load(f)
        except Exception as e:
            raise ModelError(f'Model identity request failed: {type(e).__name__}') from None

    def identity(self):
        identity, self.context, self.service_identity = resolve_identity(
            self.config, self.remote, self.opener)
        self.model = identity['model']
        self.prefix_protocol = self.service_identity['prefix_protocol']
        return identity

    def budget_limits(self):
        if self.context is None:
            raise ModelError('Model identity/capabilities must be frozen before use')
        from .budget_policy import sica_budget
        return sica_budget(self.context.contract['output_tokens'], self.context.contract['total_tokens'])

    def session(self, label, solver=None, seconds=600, calls=None):
        token = random_secrets.token_hex(32)
        # A session deadline is the only wall-clock limit for its own model and
        # tool calls.  ``None`` means the session has no host deadline; the
        # internal representation stays non-finite so JSON serialization below
        # can map it to ``None`` instead of emitting Infinity.
        deadline = math.inf if seconds is None else time.time() + seconds
        self.sessions[token] = dict(label=label, solver=solver, calls=0, tokens=0,
                                    cwd='/testbed', started=time.time(), iterations={},
                                    prompt_tokens=0, completion_tokens=0, cached_tokens=0,
                                    unmetered_calls=0,
                                    deadline=deadline,
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
        append_event(self.root, event)
        raise ModelError(reason)

    def check_session(self, s, model_request=True):
        self._raise_if_paused()
        kind = 'model_rejection' if model_request else 'tool_rejection'
        if time.time() >= s['deadline']:
            self.reject(s, 'TimeExceeded', 'session_deadline',kind=kind)
        if model_request and s['max_calls'] is not None and s['calls'] >= s['max_calls']:
            self.reject(s, 'LimitsExceeded')

    def request_json(self, request, deadline, label, call, session_token=None, event_id=None):
        """Bound one request's wall time and quarantine late transport replies."""
        if session_token is None and event_id is None:
            active = self._active_request_meta
            if active is not None:
                session_token, event_id = active
        seconds = deadline-time.time()
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
            maximum = limits['completion_max_requests']
            aggregate_cached = 0
            for attempt in range(maximum):
                self.check_session(s)
                measurement = self.context.measure(current)
                append_event(self.root, dict(kind='message_normalization', label=s['label'], protocol=TEXT_PROTOCOL,
                    original_characters=original_chars, normalized_characters=len(json.dumps(current,ensure_ascii=False)), **measurement))
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
                        if not self._valid_usage(usage):
                            event['missing_usage'] = True
                        else:
                            # A provider can attach billable usage to an
                            # error completion.  Record valid counts without
                            # ever handing the failed response to the agent.
                            self._account_usage(s, usage, aggregate_usage, event)
                        raise ModelError('Upstream completion error')
                    if not self._valid_usage(usage):
                        event['transport_fault'] = 'missing_usage'
                        raise ModelError('Endpoint omitted usage; token accounting unavailable')
                    cache_valid, cached = self._account_usage(s, usage, aggregate_usage, event)
                    aggregate_cached += cached
                    if not cache_valid:
                        raise ModelError('Invalid cached token usage')
                    combined = usage['prompt_tokens'] + usage['completion_tokens']
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
                    clipped = choice.get('finish_reason') in ('length','insufficient_system_resource')
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
                    if category is not None:
                        self._pause_transport(s, category, event, status=status)
                    event['metrics'] = self._safe_metrics(s)
                    raise ModelError(reason) from None
                finally:
                    event['seconds'] = time.time()-start
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
        seconds = max(.1, min(duration, limits['tool_timeout_seconds'], s['deadline']-time.time()))
        args = ['docker', 'exec', '-w', s['cwd'], s['solver'], 'timeout', '-k', '2', str(seconds),
                'bash', '-lc', command]
        if s.get('stage_tools') is not None:
            from .tool_output import capture_stage_output
            tools = s['stage_tools']
            args = tools.command(command, seconds)
            result = capture_stage_output(args, seconds, tools, limits)
            append_event(self.root, dict(kind='tool_call', label=s['label'], command=command, **result))
            return result
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
