"""Authenticated catalog capacity mapping; tariff metadata is ignored."""
import copy

import pytest

from mini_swe_rsi.broker import Broker, ModelError


@pytest.fixture
def catalog_broker(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(
        OPENAI_BASE_URL='https://token.sensenova.cn/v1', OPENAI_API_KEY='fixture', LLM_MODEL_NAME='glm-5.2'))
    broker = Broker(tmp_path)
    model = {'id': 'glm-5.2', 'context_length': 1048576, 'max_output_length': 131072,
             'pricing': dict.fromkeys(('prompt', 'completion', 'input_cache_read', 'image', 'request'), '0'),
             'supported_features': ['reasoning'], 'quantization': 'fp8'}
    broker.remote = lambda path: {'data': [model]} if path == '/models' else pytest.fail('Unexpected native lookup')
    yield broker, model
    broker.close()


def test_catalog_maps_capacities_and_freezes_actual_service_declarations(catalog_broker):
    broker, model = catalog_broker
    identity = broker.identity()
    assert identity['model'] == 'glm-5.2' and not identity['weights_verifiable']
    assert identity['max_tokens'] == 131072 and broker.context.contract['total_tokens'] == 1048576
    assert broker.context.contract['capacity_source'] == 'authenticated provider metadata'
    assert broker.prefix_protocol is None
    frozen = copy.deepcopy(broker.service_identity)
    assert broker.identity() == identity and broker.service_identity == frozen
    model['quantization'] = 'different'
    broker.identity()
    assert broker.service_identity != frozen


@pytest.mark.parametrize('change', [
    {'context_length': None}, {'context_length': True}, {'max_output_length': '131072'},
    {'context_window': 65536}, {'max_output_tokens': 2048}, {'max_output_length': 1048576}])
def test_missing_invalid_and_conflicting_capacities_reject(catalog_broker, change):
    broker, model = catalog_broker
    model.update(change)
    with pytest.raises(ModelError):
        broker.identity()


@pytest.mark.parametrize('tariff', [None, {}, {'prompt': '0'},
    dict.fromkeys(('prompt', 'completion', 'input_cache_read', 'image', 'request'), '0.1'),
    {**dict.fromkeys(('prompt', 'completion', 'input_cache_read', 'image', 'request'), '0'), 'extra_charge': '1'}])
def test_tariff_metadata_does_not_affect_identity_or_benchmark(catalog_broker, tariff):
    broker, model = catalog_broker
    broker.identity()
    frozen = copy.deepcopy(broker.service_identity)
    model['pricing'] = tariff
    broker.identity()
    assert broker.service_identity == frozen
    assert 'pricing' not in broker.service_identity['sensenova_catalog']
    token = broker.session('task:fixture', solver='fixture')
    broker.check_session(broker.sessions[token])
    assert broker.sessions[token]['calls'] == 0
