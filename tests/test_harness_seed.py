"""Prepared H0 stays identical to the previously generated runtime source."""
import ast
import os
import re
import subprocess
import sys

import pytest

from mini_swe_rsi.core import ROOT, digest, read, tree_hash, execution_hash
from mini_swe_rsi.driver import initial_code


def test_prepared_seed_matches_previous_harness_and_has_no_billing(tmp_path):
    source = ROOT/'src/mini_swe_rsi/harness/template/minisweagent'
    locked = read(ROOT/'upstream.lock.json')['mini']['seed_files']
    first, second = tmp_path/'first', tmp_path/'second'
    initial_code(first)
    initial_code(second)
    assert tree_hash(first) == tree_hash(second) == 'acb730d8680c3d13906d452ff72ebb77d4f1dfbf62d90608bf6248b424f7ba2a'
    assert execution_hash(first) == '2f02804efa0c41b87f5d13639e49e071e956a358967c7558adc70087ad1e205a'
    assert set(locked) == {str(p.relative_to(source)) for p in source.rglob('*')
                           if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    for name, expected in locked.items():
        assert digest(source/name) == digest(first/'minisweagent'/name) == expected
        if name.endswith(('.py', '.yaml')):
            assert not re.search(r'cost|billing|pricing|tariff|dollar', (source/name).read_text(), re.I), name
        if name.endswith('.py'):
            ast.parse((source/name).read_text(), filename=name)
    assert not (first/'minisweagent/run').exists()
    assert not (first/'minisweagent/config/benchmarks').exists()
    assert not (first/'minisweagent/__main__.py').exists()
    agent = first/'minisweagent/agents/default.py'
    agent.write_text(agent.read_text() + '\nCANDIDATE_MARKER = True\n')
    evolved = tree_hash(first)
    initial_code(first)
    assert tree_hash(first) == evolved


def test_slim_package_retains_runtime_module_imports(tmp_path):
    seed = tmp_path/'seed'
    initial_code(seed)
    env = os.environ.copy()
    env['PYTHONPATH'] = str(seed)
    script = '''
import sys
from pathlib import Path
import minisweagent
from minisweagent.config import get_config_from_spec
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
from minisweagent.models.test_models import DeterministicModel, make_output
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
assert Path(minisweagent.__file__).resolve().is_relative_to(Path(sys.argv[1]))
config = get_config_from_spec('mini_textbased.yaml')
model = LitellmTextbasedModel(model_name='fixture')
environment = LocalEnvironment(cwd='.')
agent = DefaultAgent(model, environment, **config['agent'])
assert agent.get_template_vars()['step_limit'] == config['agent']['step_limit']
try:
    get_config_from_spec('swebench')
except FileNotFoundError:
    pass
else:
    raise AssertionError('Removed bundled benchmark configuration was found')
stub = DeterministicModel(outputs=[
    make_output('local check', [{'command': 'printf retained'}]),
    make_output('done', [{'command': 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'}]),
])
agent = DefaultAgent(stub, environment, **{**config['agent'], 'step_limit': 3})
result = agent.run('Offline smoke test')
assert result['exit_status'] == 'Submitted' and agent.n_calls == 2
assert any('retained' in message['content'] for message in agent.messages
           if message['role'] == 'user')
'''
    result = subprocess.run([sys.executable, '-c', script, str(seed)],
                            env=env, cwd=seed.parent, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr



@pytest.mark.parametrize('failure', ['source_hash', 'copy'])
def test_invalid_seed_never_publishes_partial_harness(tmp_path, monkeypatch, failure):
    from mini_swe_rsi import driver
    seed = tmp_path/'seed'
    if failure == 'source_hash':
        original = driver.digest
        monkeypatch.setattr(driver, 'digest', lambda path:
                            '0'*64 if path.name == 'default.py' else original(path))
        error = RuntimeError
    else:
        def fail(*args, **kwargs):
            raise OSError('Copy failed')
        monkeypatch.setattr(driver.shutil, 'copyfile', fail)
        error = OSError
    with pytest.raises(error):
        initial_code(seed)
    assert not seed.exists()
    assert not list(tmp_path.glob('.harness-seed-*'))


@pytest.mark.parametrize('name', ['src/mini_swe_rsi/driver.py', 'upstream.lock.json'])
def test_seed_implementation_is_frozen_for_recovery(tmp_path, monkeypatch, name):
    from mini_swe_rsi import online
    from mini_swe_rsi.online import OnlineError, OnlineLoop
    from tests.test_online_state import make_loop
    loop = make_loop(tmp_path)
    assert name in loop.control_manifest['files']
    original_digest = online.digest
    monkeypatch.setattr(online, 'digest', lambda path:
                        '0'*64 if path == ROOT/name else original_digest(path))
    with pytest.raises(OnlineError, match='control_manifest'):
        OnlineLoop(loop.root, ('t1',), active_code=loop.active_code,
                   model_identity=loop.model_identity, service_identity=loop.service_identity)
