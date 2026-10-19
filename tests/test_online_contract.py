"""Compatibility boundaries must fail before runtime setup or state mutation."""
import pytest

from mini_swe_rsi import online
from mini_swe_rsi.core import ROOT, read, write
from mini_swe_rsi.online import OnlineError, OnlineLoop
from mini_swe_rsi.report import build_online_report
from tests.test_online_state import make_loop


@pytest.mark.parametrize('config', [
    {'protocol': 'mini-swe-rsi-online-v5', 'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v4', 'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v3', 'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v2', 'check_policy': {'revision': '1', 'common_tasks': []}},
    {'protocol': 'mini-swe-rsi-online-v2', 'online_protocol': 'mini-swe-rsi-online-v8',
     'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v8', 'online_protocol': 'mini-swe-rsi-online-v2',
     'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v8', 'online_protocol': 'mini-swe-rsi-online-v8',
     'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v8', 'check_policy': {'revision': '2', 'common_tasks': ['future']}},
    {'protocol': 'mini-swe-rsi-online-v8', 'check_policy': {'revision': '2'}, 'common_tasks': ['future']},
    {'protocol': 'mini-swe-rsi-online-v8', 'check_policy': None},
    {'protocol': 'mini-swe-rsi-online-v8', 'check_policy': {'revision': '1'}},
    # Previous online contracts must fail before setup, even
    # when its fields otherwise look current.
    {'protocol': 'mini-swe-rsi-online-v6', 'online_protocol': 'mini-swe-rsi-online-v6',
     'check_policy': {'revision': '2'}},
    {'protocol': 'mini-swe-rsi-online-v7', 'online_protocol': 'mini-swe-rsi-online-v7',
     'check_policy': {'revision': '2'}},
])
def test_unsupported_contract_fails_before_data_or_model_setup(tmp_path, monkeypatch, config):
    run = tmp_path / 'run'
    path = run / 'configuration.json'
    write(path, config)
    original = path.read_bytes()
    monkeypatch.setattr(online, 'Data', lambda *args: pytest.fail('Unexpected runtime setup'))
    with pytest.raises(OnlineError, match='protocol|common_tasks|policy'):
        OnlineLoop.from_environment(run)
    assert path.read_bytes() == original
    assert list(run.iterdir()) == [path]


@pytest.mark.parametrize('protocol', ['mini-swe-rsi-online-v2', 'mini-swe-rsi-online-v3', 'mini-swe-rsi-online-v4', 'mini-swe-rsi-online-v5', 'mini-swe-rsi-online-v6', 'mini-swe-rsi-online-v7'])
@pytest.mark.parametrize('filename', ['configuration.json', 'online_state.json'])
def test_report_cannot_relabel_an_old_online_run(tmp_path, filename, protocol):
    write(tmp_path / filename, {'protocol': protocol})
    with pytest.raises(ValueError, match='Unsupported online protocol'):
        build_online_report(tmp_path)
    assert not (tmp_path / 'online_report.json').exists()


@pytest.mark.parametrize('name', ['src/mini_swe_rsi/context_budget.py',
    'src/mini_swe_rsi/online_contract.py', 'src/mini_swe_rsi/workflow/check_plan.py'])
def test_contract_implementation_is_frozen_for_recovery(tmp_path, monkeypatch, name):
    loop = make_loop(tmp_path)
    assert name in loop.control_manifest['files']
    original_digest = online.digest
    code = loop.active_code
    config = loop.root / 'configuration.json'
    before = config.read_bytes()
    monkeypatch.setattr(online, 'digest', lambda path:
                        '0' * 64 if path == ROOT/name else original_digest(path))
    with pytest.raises(OnlineError, match='control_manifest'):
        OnlineLoop(loop.root, ('t1',), active_code=code,
                   model_identity=loop.model_identity, service_identity=loop.service_identity)
    assert config.read_bytes() == before


def test_online_state_rejects_protocol_alias(tmp_path):
    loop = make_loop(tmp_path)
    state_path = loop.root / 'online_state.json'
    state = read(state_path)
    state['online_protocol'] = OnlineLoop.protocol
    write(state_path, state)
    with pytest.raises(OnlineError, match='Unsupported online protocol'):
        _ = loop.state
