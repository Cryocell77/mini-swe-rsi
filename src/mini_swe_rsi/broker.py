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
import queue
import socket
import time
import urllib.error
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import append_event, secrets, BudgetExceeded
from .context_budget import ContextBudget


class ModelError(RuntimeError):
    pass


TEXT_PROTOCOL = 'mini-text-bash-v4'


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
        self.context = context if context is not None else None
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
                and selected.get('owned_by') == 'qoder'
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
            self.service_identity.update(capacity_source='authenticated loopback Qoder proxy /models?client_version=pi',
                proxy_catalog=proxy_catalog, underlying_model_verifiable=False)
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
                'remaining_calls':None if s['max_calls'] is None else max(0,s['max_calls']-s['calls']),
                'agent_iterations':dict(s['iterations']),
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

    def check_session(self, s, model_request=True):
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
            if not getattr(self, 'pricing', None) and self.context.provider and not local_ollama:
                self.reject(s, 'Benchmark pricing contract unavailable', 'pricing_contract')
            if s['cost_upper_bound_usd'] >= s['cost_limit_usd']:
                self.reject(s, 'CostExceeded', 'published_peak_cost_upper_bound',kind=kind)

    def request_json(self, request, deadline, label, call):
        """Bound total wall time even if an endpoint sends whitespace keepalives."""
        seconds = min(deadline-time.time(), self.budget.remaining())
        if seconds <= 0:
            raise ModelError('TimeExceeded')
        replies = queue.Queue(maxsize=1)
        expired = threading.Event()
        responses = []

        def fetch():
            try:
                with self.opener.open(request, timeout=seconds) as response:
                    responses.append(response)
                    result = json.load(response)
                replies.put((True,result))
                if expired.is_set():
                    append_event(self.root, {'kind':'late_model_response','label':label,'call':call,
                        'usage':result.get('usage'), 'returned_to_agent':False})
            except Exception as exc:
                replies.put((False,exc))

        threading.Thread(target=fetch,daemon=True).start()
        try:
            success, result = replies.get(timeout=seconds)
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
            raise ModelError('TimeExceeded') from None
        if not success:
            raise result
        return result

    def complete(self, token, payload, agent='main', role='orchestrator'):
        with self.lock:
            s = self.sessions[token]
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
                event['wire_messages_sha256'] = hashlib.sha256(json.dumps(wire,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                try:
                    result = self.request_json(request,s['deadline'],s['label'],s['calls'])
                    event.update(messages=wire, response=result)
                    usage = result.get('usage', {})
                    if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in aggregate_usage):
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
                    raise ModelError(reason) from None
                finally:
                    event['seconds'] = time.time()-start
                    self.events.append(event)
                    append_event(self.root,event)

    def tool(self, token, action):
        s = self.sessions[token]
        self.check_session(s, model_request=False)
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
