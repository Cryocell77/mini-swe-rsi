"""Transport fault lifecycle tests for the host-owned broker relay."""

import io
import json
import threading
import time
import urllib.error

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.context_budget import ContextBudget
from mini_swe_rsi.core import read, write


def _response(*, usage=None, choices=None, error=None):
    value = {'choices': [] if choices is None else choices}
    if usage is not None:
        value['usage'] = usage
    if error is not None:
        value['error'] = error
    return io.BytesIO(json.dumps(value).encode())


@pytest.fixture
def make_broker(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: {
        'OPENAI_BASE_URL': 'https://fixture/v1',
        'OPENAI_API_KEY': 'private-key',
        'LOCAL_LLM_MODEL': 'fixed',
    })

    def factory(policy):
        broker = Broker(tmp_path, context=ContextBudget(), transport_policy=policy)
        broker.model = 'fixed'
        return broker

    yield factory


def _call(broker):
    token = broker.session('online', seconds=None)
    return token, {'messages': [{'role': 'user', 'content': 'fixture'}]}


def test_transport_timeout_pauses_sticky_and_does_not_make_second_request(make_broker):
    release = threading.Event()
    entered = threading.Event()

    class Endpoint:
        calls = 0

        def open(self, request, **kwargs):
            self.calls += 1
            entered.set()
            release.wait(1)
            return _response(usage={'total_tokens': 3, 'prompt_tokens': 2, 'completion_tokens': 1})

    broker = make_broker({
        'request_timeout_seconds': .03,
        'pause_on_transport_error': True,
    })
    endpoint = Endpoint()
    broker.opener = endpoint
    token, payload = _call(broker)
    try:
        with pytest.raises(ModelError, match='TransportTimeout'):
            broker.complete(token, payload)
        assert entered.is_set() and endpoint.calls == 1
        marker = read(broker.transport_pause_path)
        assert marker['status'] == 'paused' and marker['reason'] == 'transport_timeout'
        before = dict(broker.sessions[token])
        with pytest.raises(ModelError, match='TransportPaused'):
            broker.complete(token, payload)
        assert endpoint.calls == 1
        assert broker.sessions[token]['calls'] == before['calls']
        assert broker.sessions[token]['errors'] == before['errors']
    finally:
        release.set()
        broker.close()


def test_http500_persists_safe_pause_without_remote_body(make_broker):
    class Endpoint:
        calls = 0

        def open(self, request, **kwargs):
            self.calls += 1
            raise urllib.error.HTTPError(request.full_url, 500, 'private reason', {},
                                         io.BytesIO(b'private response body'))

    broker = make_broker({'pause_on_http_status': [500]})
    endpoint = Endpoint()
    broker.opener = endpoint
    token, payload = _call(broker)
    try:
        with pytest.raises(ModelError, match='HTTPError'):
            broker.complete(token, payload)
        text = (broker.root / 'events.jsonl').read_text()
        assert 'private response body' not in text and 'private reason' not in text
        marker = read(broker.transport_pause_path)
        assert marker['http_status'] == 500 and marker['reason'] == 'http_status'
        with pytest.raises(ModelError, match='TransportPaused'):
            broker.complete(token, payload)
        assert endpoint.calls == 1
    finally:
        broker.close()


def test_missing_usage_and_error_completion_pause_without_exposing_error_object(make_broker):
    cases = [
        ({'pause_on_missing_usage': True}, _response(choices=[]), 'missing_usage',
         'token accounting unavailable'),
        ({'pause_on_invalid_completion': True}, _response(
            choices=[{'finish_reason': 'error', 'message': {'role': 'assistant', 'content': ''}}]),
         'finish_reason_error', 'Upstream completion error'),
        ({'pause_on_invalid_completion': True}, _response(error={'message': 'private body', 'type': 'server_error'}),
         'upstream_error', 'Upstream completion error'),
    ]
    for policy, response, reason, message in cases:
        broker = make_broker(policy)
        broker.opener = type('Endpoint', (), {'calls': 0, 'open': lambda self, request, **kwargs: response})()
        token, payload = _call(broker)
        try:
            with pytest.raises(ModelError, match=message):
                broker.complete(token, payload)
            marker = read(broker.transport_pause_path)
            assert marker['reason'] == reason
            assert 'private body' not in (broker.root / 'events.jsonl').read_text()
            assert 'agent_response' not in broker.events[-1]
        finally:
            broker.close()
            broker.transport_pause_path.unlink(missing_ok=True)


def test_late_response_is_usage_ledger_only_and_never_reaches_agent(make_broker):
    release = threading.Event()
    class Endpoint:
        calls = 0
        def open(self, request, **kwargs):
            self.calls += 1
            release.wait(1)
            return _response(usage={'total_tokens': 8, 'prompt_tokens': 5, 'completion_tokens': 3})

    broker = make_broker({'request_timeout_seconds': .03, 'pause_on_transport_error': True})
    endpoint = Endpoint()
    broker.opener = endpoint
    token, payload = _call(broker)
    try:
        with pytest.raises(ModelError, match='TransportTimeout'):
            broker.complete(token, payload)
        release.set()
        deadline = time.time() + 1
        while not any(event.get('kind') == 'late_model_response' for event in broker.events):
            assert time.time() < deadline
            time.sleep(.01)
        late = next(event for event in broker.events if event.get('kind') == 'late_model_response')
        assert late['returned_to_agent'] is False
        assert late['usage'] == {'total_tokens': 8, 'prompt_tokens': 5, 'completion_tokens': 3}
        assert 'agent_response' not in late
        assert endpoint.calls == 1
    finally:
        release.set()
        broker.close()


def test_pause_intercepts_candidate_registration_and_tool(make_broker):
    broker = make_broker({'pause_on_invalid_completion': True})
    token, payload = _call(broker)
    broker.pause_event.set()
    try:
        with pytest.raises(ModelError, match='TransportPaused'):
            broker.register_candidate_job('candidate:P1', lambda: {'passed': True})
        broker.sessions[token]['solver'] = 'fixture'
        with pytest.raises(ModelError, match='TransportPaused'):
            broker.tool(token, {'command': 'true', 'generous_expected_duration': 5})
        assert broker.sessions[token]['calls'] == 0
        assert broker.sessions[token]['errors'] == []
    finally:
        broker.close()


def test_transport_policy_can_be_loaded_from_root_file(make_broker):
    # The fixture's temporary root is also the broker root; write the policy
    # before constructing a fresh broker to exercise the persisted contract.
    broker = make_broker({})
    root = broker.root
    broker.close()
    write(root / 'transport_policy.json', {
        'version': 1,
        'request_timeout_seconds': 900,
        'pause_on_http_status': [500],
        'pause_on_invalid_completion': True,
        'pause_on_missing_usage': True,
        'pause_on_transport_error': True,
    })
    loaded = make_broker(None)
    try:
        assert loaded.transport_policy['request_timeout_seconds'] == 900
        assert loaded.transport_policy['pause_on_http_status'] == [500]
    finally:
        loaded.close()
