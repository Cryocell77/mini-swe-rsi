import runpy
from pathlib import Path

import pytest

from mini_swe_rsi import online
from mini_swe_rsi.core import execution_hash, tree_hash
from mini_swe_rsi.online import OnlineError, OnlineLoop


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = runpy.run_path(str(ROOT / 'src/mini_swe_rsi/workflow/workflow.py'))
BASELINE_FILES = {
    '__main__.py': 'print("entry")\n',
    'README.md': 'root docs\n',
    'description.txt': 'description\n',
    'agent_change_log.md': 'changes\n',
    'LICENSE.mini': 'license\n',
    'minisweagent/__init__.py': '',
    'minisweagent/agents/default.py': 'x = 1\n',
    'minisweagent/config/mini_textbased.yaml': 'model: fixture\n',
    'minisweagent/README.md': 'package resource\n',
    'minisweagent/description.txt': 'another resource\n',
    'minisweagent/__pycache__/ignore.pyc': 'ignored',
    'misc/other.py': 'nonruntime\n',
}
BASELINE_FULL = 'ed75f9fb9e43cba46ca18976d7b4e6cfdf979f7cf48750bfc3ab6ce4cdf2b68a'
BASELINE_EXECUTION = 'fb8ffee6618089cd691cd7c6d55e6b30be68f2307c62fc23ab8fca285e87d4f2'


def _fixture_tree(root):
    for relative, content in BASELINE_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return {'full': BASELINE_FULL, 'execution': BASELINE_EXECUTION}


def test_shared_hashes_match_frozen_baseline(tmp_path):
    fixture = _fixture_tree(tmp_path)
    assert fixture['full'] != fixture['execution']
    assert tree_hash(tmp_path) == fixture['full']
    assert execution_hash(tmp_path) == fixture['execution']
    assert WORKFLOW['candidate_source_hash'](tmp_path) == fixture['full']
    assert WORKFLOW['execution_hash'](tmp_path) == fixture['execution']


def test_execution_hash_keeps_package_resources_and_excludes_root_docs(tmp_path):
    _fixture_tree(tmp_path)
    full_before = tree_hash(tmp_path)
    execution_before = execution_hash(tmp_path)
    (tmp_path / 'README.md').write_text('changed root documentation\n')
    assert tree_hash(tmp_path) != full_before
    assert execution_hash(tmp_path) == execution_before
    (tmp_path / 'minisweagent' / 'README.md').write_text('changed package resource\n')
    assert execution_hash(tmp_path) != execution_before


@pytest.mark.parametrize('kind', ['missing', 'file', 'symlink'])
def test_hashes_reject_invalid_roots(tmp_path, kind):
    if kind == 'missing':
        path = tmp_path / 'missing'
    elif kind == 'file':
        path = tmp_path / 'file'
        path.write_text('x\n')
    else:
        target = tmp_path / 'target'
        target.mkdir()
        path = tmp_path / 'link'
        path.symlink_to(target, target_is_directory=True)
    for function in (tree_hash, execution_hash,
                     WORKFLOW['candidate_source_hash'], WORKFLOW['execution_hash']):
        with pytest.raises(ValueError):
            function(path)


def test_source_hash_module_is_frozen_in_online_control_manifest(tmp_path, monkeypatch):
    code = tmp_path / 'code' / 'minisweagent' / 'agents'
    code.mkdir(parents=True)
    (code / 'default.py').write_text('x = 1\n')
    loop = OnlineLoop(tmp_path / 'run', ('t1',), active_code=code.parents[1])
    original_digest = online.digest
    source_hash_path = ROOT / 'src/mini_swe_rsi/workflow/source_hash.py'
    monkeypatch.setattr(
        online, 'digest',
        lambda path: '0' * 64 if path == source_hash_path else original_digest(path),
    )
    with pytest.raises(OnlineError, match='control_manifest'):
        OnlineLoop(loop.root, ('t1',))


def test_workflow_identity_covers_role_renderer_without_new_record_fields(tmp_path):
    source = ROOT/'src/mini_swe_rsi/workflow'
    for name in ('workflow.py', 'roles.py'):
        (tmp_path/name).write_bytes((source/name).read_bytes())
    identity = WORKFLOW['workflow_hash'](tmp_path)
    assert identity == WORKFLOW['workflow_hash'](source)
    (tmp_path/'roles.py').write_text((tmp_path/'roles.py').read_text() + '\n# changed renderer\n')
    assert WORKFLOW['workflow_hash'](tmp_path) != identity
