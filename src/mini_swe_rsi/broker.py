"""Host-owned model and tool budgets. No API credential enters an agent container."""
from __future__ import annotations

import json
import secrets as random_secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import append_event, secrets


class ModelError(RuntimeError):
    pass


TEXT_PROTOCOL = 'mini-text-bash-v2'


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


class Broker:
    def __init__(self, root, budget):
        self.root, self.budget = root, budget
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
                        out['error']['message'] = 'exceed context limit: host serialized-context limit of 60000 characters (ContextLimitExceeded)'
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
        return {'requested': requested, 'model': selected, 'weights': weights,
                'temperature': 0, 'max_tokens': 2048, 'model_concurrency': 1}

    def session(self, label, solver=None, seconds=600, calls=30):
        token = random_secrets.token_hex(32)
        self.sessions[token] = dict(label=label, solver=solver, calls=0, tokens=0,
                                    cwd='/testbed',
                                    prompt_tokens=0, completion_tokens=0, cached_tokens=0,
                                    unmetered_calls=0,
                                    deadline=time.time() + min(seconds, self.budget.remaining()),
                                    max_calls=calls, tool_calls=0, errors=[])
        return token

    def finish(self, token):
        # A killed controller may leave its last HTTP request finishing on the
        # broker thread. Snapshot usage only after that request is accounted for.
        with self.lock:
            data = self.sessions.pop(token)
            return {k: v for k, v in data.items() if k != 'solver'}

    def complete(self, token, payload):
        with self.lock:
            self.budget.check()
            s = self.sessions[token]
            if time.time() >= s['deadline']:
                raise ModelError('TimeExceeded')
            if s['calls'] >= s['max_calls']:
                raise ModelError('LimitsExceeded')
            original = payload.get('messages')
            if any(payload.get(k) for k in ('tools', 'tool_choice', 'functions', 'function_call')):
                raise ModelError('Native tools are outside the text bash protocol')
            messages = normalize_messages(original)
            original_chars = len(json.dumps(original, ensure_ascii=False))
            normalized_chars = len(json.dumps(messages, ensure_ascii=False))
            audit = dict(kind='message_normalization', label=s['label'], protocol=TEXT_PROTOCOL,
                         original_characters=original_chars, normalized_characters=normalized_chars)
            append_event(self.root, audit)
            if normalized_chars > 60000:
                s['errors'].append('ContextLimitExceeded')
                raise ModelError('ContextLimitExceeded')
            s['calls'] += 1
            body = {'model': self.model, 'messages': messages, 'temperature': 0,
                    'max_tokens': 2048, 'stream': False}
            request = urllib.request.Request(self.config['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions',
                      data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                      'Authorization': 'Bearer ' + self.config['OPENAI_API_KEY']})
            start = time.time()
            event = dict(kind='model_call', label=s['label'], call=s['calls'], model=self.model,
                         protocol=TEXT_PROTOCOL, original_characters=original_chars,
                         normalized_characters=normalized_chars)
            try:
                with self.opener.open(request, timeout=max(.1, min(180, s['deadline'] - time.time()))) as f:
                    result = json.load(f)
                usage = result.get('usage', {})
                if not isinstance(usage, dict) or 'total_tokens' not in usage:
                    raise ModelError('Endpoint omitted usage; token accounting unavailable')
                for key in ('prompt_tokens', 'completion_tokens'):
                    s[key] += usage.get(key, 0)
                s['tokens'] += usage['total_tokens']
                s['cached_tokens'] += (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
                event.update(usage=usage, messages=messages, response=result)
                return result
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
        self.budget.check()
        if not s['solver'] or time.time() >= s['deadline']:
            raise ModelError('Tool session expired')
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
