"""Verify the exact source that becomes H0, not just a disabled billing flag."""
import ast
import json
import re

import pytest

from mini_swe_rsi import harness_seed
from mini_swe_rsi.core import ROOT, digest, read, tree_hash
from mini_swe_rsi.driver import initial_code


def test_generated_package_removes_all_billing_while_preserving_upstream(tmp_path):
    original = ROOT/'.upstream/mini/src/minisweagent'
    locked = read(ROOT/'upstream.lock.json')['mini']['files']
    first, second = tmp_path/'first', tmp_path/'second'
    initial_code(first)
    initial_code(second)
    assert tree_hash(first) == tree_hash(second)
    changed = read(harness_seed.SEED_MANIFEST)['files']
    for name, expected in locked.items():
        assert digest(original/name) == expected
        generated = first/'minisweagent'/name
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
    # Re-entering initialization must not overwrite an evolved Harness.
    agent = first/'minisweagent/agents/default.py'
    agent.write_text(agent.read_text() + '\nCANDIDATE_MARKER = True\n')
    evolved = tree_hash(first)
    initial_code(first)
    assert tree_hash(first) == evolved


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


@pytest.mark.parametrize('name', ['src/mini_swe_rsi/harness_seed.py',
    'src/mini_swe_rsi/assets/mini-no-billing.patch', 'src/mini_swe_rsi/assets/mini-no-billing.json'])
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
