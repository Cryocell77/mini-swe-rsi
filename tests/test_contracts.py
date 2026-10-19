from pathlib import Path

import pytest

from mini_swe_rsi.core import ROOT, tree_hash
from mini_swe_rsi.driver import validate_code


def test_full_source_hash_includes_comments_and_symlinks_rejected(tmp_path):
    code = tmp_path/'minisweagent/agents'
    code.mkdir(parents=True)
    p = code/'default.py'
    p.write_text('x = 1\n')
    before = tree_hash(tmp_path)
    p.write_text('# comment only\nx = 1\n')
    assert tree_hash(tmp_path) != before
    p.write_text('x = 2\n')
    assert tree_hash(tmp_path) != before
    (code/'escape').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):
        validate_code(tmp_path)


def test_improvement_prompt_keeps_upstream_task(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('bridge', ROOT/'src/mini_swe_rsi/workflow/bridge.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self,*a,**kw: original(ROOT/'src/mini_swe_rsi/workflow/task/agent_main.py')
                        if str(self)=='/upstream/agent_main.py' else original(self,*a,**kw))
    prompt = bridge.improvement_prompt(1, 'agent_4')
    assert 'parent=agent_1; proposer=agent_1; candidate=agent_4' in prompt
    assert 'meta improvement reasoning structure' in prompt
    assert 'description.txt' in prompt
    assert '/home/sandbox/workdir' not in prompt
