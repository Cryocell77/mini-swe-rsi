"""Verify the exact source that becomes H0, not just a disabled billing flag."""
import ast
import json
import os
import re
import subprocess
import sys

import pytest

from mini_swe_rsi.harness import seed as harness_seed
from mini_swe_rsi.core import ROOT, digest, read, tree_hash
from mini_swe_rsi.driver import initial_code


def test_generated_package_removes_all_billing_while_preserving_upstream(tmp_path):
    original = ROOT/'src/mini_swe_rsi/harness/template/minisweagent'
    locked = read(ROOT/'upstream.lock.json')['mini']['files']
    first, second = tmp_path/'first', tmp_path/'second'
    initial_code(first)
    initial_code(second)
    assert tree_hash(first) == tree_hash(second)
    changed = read(harness_seed.SEED_MANIFEST)['files']
    removed = set(read(harness_seed.SLIM_MANIFEST)['removed_files'])
    assert removed == {
        name for name in locked
        if name == '__main__.py' or name.startswith('run/')
        or name.startswith('config/benchmarks/')
    }
    for name, expected in locked.items():
        assert digest(original/name) == expected
        generated = first/'minisweagent'/name
        if name in removed:
            assert not generated.exists()
            continue
        assert digest(generated) == changed.get(name, {}).get('after', expected)
        if generated.suffix in ('.py', '.yaml'):
            assert not re.search(r'cost|billing|pricing|tariff|dollar', generated.read_text(), re.I), name
        if generated.suffix == '.py':
            ast.parse(generated.read_text(), filename=name)
    # Billing removal preserves provider transports, parsing and observations.
    for name in changed:
        if not name.startswith('models/') or not name.endswith('.py'):
            continue
        def methods(path):
            return {node.name: ast.dump(node, include_attributes=False)
                    for node in ast.walk(ast.parse(path.read_text()))
                    if isinstance(node, ast.FunctionDef) and node.name in (
                        '_query', '_prepare_messages_for_api', '_parse_actions',
                        'format_message', 'format_observation_messages')}
        assert methods(original/name) == methods(first/'minisweagent'/name), name
    assert not (first/'minisweagent/run').exists()
    assert not (first/'minisweagent/config/benchmarks').exists()
    assert not (first/'minisweagent/__main__.py').exists()
    assert (first/'__main__.py').is_file()
    # Re-entering initialization must not overwrite or re-trim an evolved Harness.
    agent = first/'minisweagent/agents/default.py'
    agent.write_text(agent.read_text() + '\nCANDIDATE_MARKER = True\n')
    restored_runner = first/'minisweagent/run/mini.py'
    restored_runner.parent.mkdir(parents=True)
    restored_runner.write_text('EVOLVED_RUNNER = True\n')
    evolved = tree_hash(first)
    initial_code(first)
    assert tree_hash(first) == evolved
    assert restored_runner.read_text() == 'EVOLVED_RUNNER = True\n'


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


@pytest.mark.parametrize('failure', ['patch_hash', 'input_hash', 'output_hash'])
def test_invalid_seed_never_publishes_partial_harness(tmp_path, monkeypatch, failure):
    seed = tmp_path/'seed'
    if failure == 'patch_hash':
        patch = tmp_path/'broken.patch'
        patch.write_bytes(harness_seed.SEED_PATCH.read_bytes() + b'\n')
        monkeypatch.setattr(harness_seed, 'SEED_PATCH', patch)
    else:
        manifest = read(harness_seed.SEED_MANIFEST)
        manifest['files']['agents/default.py']['before' if failure == 'input_hash' else 'after'] = '0'*64
        path = tmp_path/'broken.json'
        path.write_text(json.dumps(manifest))
        monkeypatch.setattr(harness_seed, 'SEED_MANIFEST', path)
    with pytest.raises(RuntimeError, match='identity|input|output'):
        initial_code(seed)
    assert not seed.exists()
    assert not list(tmp_path.glob('.harness-seed-*'))


@pytest.mark.parametrize('failure', ['paths', 'hash', 'version', 'upstream', 'input_hash'])
def test_invalid_slim_manifest_never_publishes_partial_harness(tmp_path, monkeypatch, failure):
    seed = tmp_path/'seed'
    manifest = read(harness_seed.SLIM_MANIFEST)
    if failure == 'paths':
        manifest['removed_files']['outside/core.py'] = manifest['removed_files'].pop('run/mini.py')
    elif failure == 'hash':
        manifest['removed_files']['run/mini.py'] = '0'*64
    elif failure == 'version':
        manifest['version'] = 'unknown'
    elif failure == 'upstream':
        manifest['upstream_commit'] = '0'*40
    else:
        original_digest = harness_seed.digest
        monkeypatch.setattr(harness_seed, 'digest', lambda path:
                            '0'*64 if path.name == 'hello_world.py' else original_digest(path))
    path = tmp_path/'broken-slim.json'
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(harness_seed, 'SLIM_MANIFEST', path)
    with pytest.raises(RuntimeError, match='Slim Harness'):
        initial_code(seed)
    assert not seed.exists()
    assert not list(tmp_path.glob('.harness-seed-*'))


@pytest.mark.parametrize('name', ['src/mini_swe_rsi/harness/seed.py',
    'src/mini_swe_rsi/assets/mini-no-billing.patch', 'src/mini_swe_rsi/assets/mini-no-billing.json',
    'src/mini_swe_rsi/assets/mini-online-slim.json'])
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
