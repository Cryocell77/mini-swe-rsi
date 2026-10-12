"""Local Qoder proxy metadata is a declaration, never backend identity proof."""
import copy
import io
import urllib.error

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.core import Budget


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(
        OPENAI_BASE_URL='http://127.0.0.1:8317/v1', OPENAI_API_KEY='fixture', LOCAL_LLM_MODEL='qfmodel'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    selected = {'id': 'qfmodel', 'owned_by': 'qoder'}
    catalog = [{'slug': 'qfmodel', 'context_window': 180000, 'max_context_window': 180000,
                'max_tokens': 8192, 'base_instructions': 'UNTRUSTED CATALOG PROMPT'}]
    broker.remote = lambda path: {'data': [selected]} if path == '/models' else {'models': catalog}
    yield broker, selected, catalog
    broker.close()


def test_proxy_declaration_freezes_without_prompt_or_free_price_assumption(proxy):
    broker, selected, catalog = proxy
    identity = broker.identity()
    assert identity['model'] == 'qfmodel' and identity['max_tokens'] == 8192
    assert not identity['weights_verifiable']
    assert broker.context.contract['total_tokens'] == 180000 and broker.context.tokenizer is None
    assert not broker.service_identity['underlying_model_verifiable']
    assert 'UNTRUSTED' not in str(broker.service_identity)
    frozen = copy.deepcopy(broker.service_identity)
    catalog[0]['max_tokens'] = 4096
    broker.identity()
    assert broker.service_identity != frozen
    token = broker.session('task:fixture', solver='fixture')
    with pytest.raises(ModelError, match='pricing contract unavailable'):
        broker.check_session(broker.sessions[token])
    assert broker.sessions[token]['calls'] == 0


@pytest.mark.parametrize('change', [{'max_tokens': None}, {'max_tokens': True},
    {'max_tokens': 180000}, {'context_window': '180000'}, {'max_context_window': 32768}])
def test_invalid_proxy_declaration_fails_closed(proxy, change):
    broker, selected, catalog = proxy
    catalog[0].update(change)
    with pytest.raises(ModelError):
        broker.identity()


def test_missing_duplicate_or_conflicting_model_rejects(proxy):
    broker, selected, catalog = proxy
    catalog.append(dict(catalog[0]))
    with pytest.raises(ModelError, match='unique'):
        broker.identity()
    catalog.pop()
    selected['max_output_tokens'] = 2048
    with pytest.raises(ModelError, match='conflicting'):
        broker.identity()
    selected.pop('max_output_tokens')
    catalog[0]['slug'] = 'other'
    with pytest.raises(ModelError, match='unique'):
        broker.identity()


def test_http_status_retained_without_remote_body_or_credentials(proxy):
    broker, _, _ = proxy
    broker.identity()
    def fail(*args):
        raise urllib.error.HTTPError('http://fixture', 429, 'private message', {}, io.BytesIO(b'private body'))
    broker.request_json = fail
    token = broker.session('proposal:fixture', seconds=None)
    with pytest.raises(ModelError, match='HTTPError'):
        broker.complete(token, {'messages': [{'role': 'user', 'content': 'fixture'}]})
    event = broker.events[-1]
    assert event['http_status'] == 429 and 'private' not in str(event)
    assert broker.sessions[token]['unmetered_calls'] == 1
