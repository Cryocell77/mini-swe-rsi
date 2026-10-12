import io
import json
import time

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.core import Budget


def test_model_and_limits_are_host_owned(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                      OPENAI_API_KEY='private-key', LOCAL_LLM_MODEL='fixed'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixed'
    received = []
    class Opener:
        def open(self, request, **kwargs):
            received.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [], 'usage': {'total_tokens': 7,
                                         'prompt_tokens': 5, 'completion_tokens': 2}}).encode())
    broker.opener = Opener()
    token = broker.session('fixture', calls=1)
    try:
        broker.complete(token, {'model':'stronger', 'messages':[{'role':'user','content':'hello'}], 'max_tokens':99999})
        assert received[0]['model'] == 'fixed'
        assert received[0]['max_tokens'] == 2048
        with pytest.raises(ModelError, match='LimitsExceeded'):
            broker.complete(token, {'messages':[]})
        assert broker.finish(token)['tokens'] == 7
        token = broker.session('context')
        with pytest.raises(ModelError, match='ContextLimitExceeded'):
            broker.complete(token, {'messages':[{'role':'user','content':'x'*60001}]})
        assert broker.sessions[token]['calls'] == 0
        broker.sessions[token]['deadline'] = time.time()-1
        with pytest.raises(ModelError, match='TimeExceeded'):
            broker.complete(token, {'messages':[]})
        assert 'private-key' not in (tmp_path/'events.jsonl').read_text()
    finally:
        broker.close()


def test_context_error_is_non_retryable_for_official_client(tmp_path, monkeypatch):
    import litellm
    import httpx
    from openai import OpenAI
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                      OPENAI_API_KEY='private-key', LOCAL_LLM_MODEL='fixed'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixed'
    token = broker.session('context-client')
    client = OpenAI(base_url=f'http://127.0.0.1:{broker.port}/v1', api_key=token,
                    http_client=httpx.Client(trust_env=False), max_retries=0)
    try:
        with pytest.raises(litellm.ContextWindowExceededError):
            litellm.completion(model='openai/fixed', api_base=f'http://127.0.0.1:{broker.port}/v1',
                api_key=token, messages=[{'role':'user','content':'x'*60001}], num_retries=0, client=client)
        assert broker.sessions[token]['calls'] == 0
    finally:
        client.close()
        broker.close()


def test_finish_waits_for_inflight_usage_accounting(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                      OPENAI_API_KEY='private-key', LOCAL_LLM_MODEL='fixed'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixed'
    entered, release = threading.Event(), threading.Event()
    class DelayedResponse:
        def open(self, request, **kwargs):
            entered.set()
            assert release.wait(2)
            return io.BytesIO(json.dumps({'choices':[], 'usage':{'total_tokens':7,
                               'prompt_tokens':5,'completion_tokens':2}}).encode())
    broker.opener = DelayedResponse()
    token = broker.session('inflight')
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            response = pool.submit(broker.complete, token, {'messages':[]})
            try:
                assert entered.wait(1)
                snapshot = pool.submit(broker.finish, token)
                with pytest.raises(TimeoutError):
                    snapshot.result(timeout=.05)
            finally:
                release.set()
            response.result(timeout=1)
            assert snapshot.result(timeout=1)['tokens'] == 7
    finally:
        broker.close()


def test_normalized_text_messages_match_budget_and_wire(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1', OPENAI_API_KEY='secret'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixed'
    received = []
    class Opener:
        def open(self, request, **kwargs):
            received.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [], 'usage': {'total_tokens': 1}}).encode())
    broker.opener = Opener()
    token = broker.session('normalization')
    messages = [{'role':'assistant', 'content':'x'*30000, 'reasoning_content':'r'*10000,
                 'provider_specific_fields':{'reasoning':'r'*10000}, 'extra':{'unused':'z'*20000}, 'name':None}]
    try:
        broker.complete(token, {'messages':messages})
        wire = received[0]['messages']
        assert set(wire[0]) == {'role','content','reasoning_content'}
        event = broker.events[-1]
        assert event['messages'] == wire
        assert event['normalized_characters'] == len(json.dumps(wire, ensure_ascii=False))
        assert event['original_characters'] > 60000 > event['normalized_characters']
        for bad in ([{'role':'tool','content':'hidden'}], [{'role':'user','content':[]}],
                    [{'role':'assistant','content':'', 'tool_calls':[{'id':'x'}]}]):
            with pytest.raises(ModelError):
                broker.complete(token, {'messages':bad})
        with pytest.raises(ModelError, match='ContextLimitExceeded'):
            broker.complete(token, {'messages':[{'role':'assistant','content':'x'*50000,'reasoning_content':'r'*10000}]})
        assert len(received) == 1
    finally:
        broker.close()
