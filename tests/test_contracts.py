import ast
import json
import time
from pathlib import Path

import pytest

from mini_swe_rsi.core import Budget, BudgetExceeded, ROOT, read, tree_hash, write
from mini_swe_rsi.driver import validate_code
from mini_swe_rsi.report import paired
from mini_swe_rsi.sica import load_original


def test_upstream_functions_are_exact_and_archive_selects(tmp_path):
    r = load_original()
    for i, scores in enumerate(([0, 0, 1, 0], [1, 1, 1, 1], [0, 0, 0, 0])):
        b = tmp_path/f'agent_{i}/benchmarks/swe'
        b.mkdir(parents=True)
        rows = [dict(problem_id=str(j), score=s, tokens_used=10, num_cached_tokens=0,
                     cost_estimate=0, wall_time=1) for j,s in enumerate(scores)]
        (b/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        write(b/'perf.json', dict(avg_score=sum(scores)/4, tokens=40, num_cached_tokens=0, cost=0, time=4))
    a = r.ArchiveAnalyzer(tmp_path)
    assert a.get_best_agent_iteration() == 1
    assert r.select_base_agent(a, 2) == 1
    assert Path(r.run_meta_agent_benchmark.__code__.co_filename) == ROOT/'vendor/sica/runner.py'
    assert Path(r.select_base_agent.__code__.co_filename) == ROOT/'vendor/sica/runner.py'


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


def test_no_partial_coverage_can_pass_statistics():
    one = {'x': dict(score=1, tokens_used=10)}
    result = paired(one, one, ['x','y'])
    assert not result['complete']
    win = paired({'x': dict(score=1,tokens_used=10)}, {'x': dict(score=0,tokens_used=10)}, ['x'])
    assert win['ci95'] == [1.,1.]


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
    prompt = bridge.improvement_prompt(2,3)
    assert 'agent iteration 2' in prompt
    assert 'meta improvement reasoning structure' not in prompt
    assert 'description.txt' in prompt
    assert '/home/sandbox/workdir' not in prompt
