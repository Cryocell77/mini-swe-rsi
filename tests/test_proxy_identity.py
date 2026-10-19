"""Local Qoder proxy metadata is a declaration, never backend identity proof."""
import copy
import io
import json
import urllib.error

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from tests.event_helpers import broker_events


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(
        OPENAI_BASE_URL='http://127.0.0.1:8317/v1', OPENAI_API_KEY='fixture', LLM_MODEL_NAME='qfmodel'))
    broker = Broker(tmp_path)
    selected = {'id': 'qfmodel', 'owned_by': 'qoder'}
    catalog = [{'slug': 'qfmodel', 'context_window': 180000, 'max_context_window': 180000,
                'max_tokens': 8192, 'base_instructions': 'UNTRUSTED CATALOG PROMPT'}]
    broker.remote = lambda path: {'data': [selected]} if path == '/models' else {'models': catalog}
    yield broker, selected, catalog
    broker.close()


def test_proxy_declaration_freezes_without_catalog_prompt(proxy):
    broker, selected, catalog = proxy
    identity = broker.identity()
    assert identity['model'] == 'qfmodel' and identity['max_tokens'] == 8192
    assert not identity['weights_verifiable']
    assert broker.context.contract['total_tokens'] == 180000
    assert not broker.service_identity['underlying_model_verifiable']
    assert 'UNTRUSTED' not in str(broker.service_identity)
    frozen = copy.deepcopy(broker.service_identity)
    catalog[0]['max_tokens'] = 4096
    broker.identity()
    assert broker.service_identity != frozen
    token = broker.session('task:fixture', solver='fixture')
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
    event = broker_events(broker)[-1]
    assert event['http_status'] == 429 and 'private' not in str(event)
    assert broker.sessions[token]['unmetered_calls'] == 1


def test_proxy_task_accounts_tokens_without_billing_configuration(proxy):
    broker, _, _ = proxy
    broker.identity()
    response = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'fixture'}}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}
    broker.request_json = lambda *args: response
    token = broker.session('task:fixture', solver='fixture')
    broker.complete(token, {'messages': [{'role': 'user', 'content': 'fixture'}]})
    metrics = broker.finish(token)
    assert metrics['calls'] == 1 and metrics['tokens'] == 7
    assert not any('cost' in key or 'billing' in key for key in metrics)


def test_workbuddy_uses_the_same_authenticated_capacity_contract(proxy):
    broker, selected, catalog = proxy
    selected['owned_by'] = 'workbuddy'
    identity = broker.identity()
    assert identity['model'] == 'qfmodel'
    assert broker.service_identity['owned_by'] == 'workbuddy'


def test_obsolete_billing_environment_is_ignored(tmp_path, monkeypatch):
    from mini_swe_rsi.core import secrets
    monkeypatch.setattr('mini_swe_rsi.core.ROOT', tmp_path)
    base = 'OPENAI_BASE_URL=http://127.0.0.1/v1\nOPENAI_API_KEY=fixture\nLLM_MODEL_NAME=qfmodel\n'
    (tmp_path/'.env').write_text(base+'RSI_BILLING_MODE=subscription\n')
    expected = secrets()
    assert 'RSI_BILLING_MODE' not in expected
    (tmp_path/'.env').write_text(base+'RSI_BILLING_MODE=free_guessed\n')
    assert secrets() == expected
