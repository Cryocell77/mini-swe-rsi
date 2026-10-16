import ast
from pathlib import Path

import pytest

from mini_swe_rsi.core import Budget, BudgetExceeded, ROOT, read, tree_hash, write
from mini_swe_rsi.driver import validate_code
def test_budget_survives_restart_and_enforces_both_limits(tmp_path):
    b = Budget(tmp_path)
    b.enter('baseline')
    data = read(b.path)
    data['phases']['baseline'] -= 7201
    write(b.path, data)
    restarted = Budget(tmp_path)
    with pytest.raises(BudgetExceeded):
        restarted.enter('baseline')
    data['started'] -= 24*3600+1
    write(b.path, data)
    with pytest.raises(BudgetExceeded):
        restarted.enter('search')


def test_comments_are_not_behavior_and_symlinks_rejected(tmp_path):
    code = tmp_path/'minisweagent/agents'
    code.mkdir(parents=True)
    p = code/'default.py'
    p.write_text('x = 1\n')
    before = tree_hash(tmp_path, True)
    p.write_text('# comment only\nx = 1\n')
    assert tree_hash(tmp_path, True) == before
    p.write_text('x = 2\n')
    assert tree_hash(tmp_path, True) != before
    (code/'escape').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):
        validate_code(tmp_path)


def test_improvement_prompt_keeps_upstream_task(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('bridge', ROOT/'runtime/bridge.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self,*a,**kw: original(ROOT/'vendor/sica/agent_main.py')
                        if str(self)=='/upstream/agent_main.py' else original(self,*a,**kw))
    prompt = bridge.improvement_prompt(2,3,1,'agent_4')
    assert 'parent=agent_1; proposer=agent_2; candidate=agent_4' in prompt
    assert 'meta improvement reasoning structure' in prompt
    assert 'description.txt' in prompt
    assert '/home/sandbox/workdir' not in prompt
