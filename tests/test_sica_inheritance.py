"""Exercise the unmodified upstream meta loop through its adapted process boundary."""
import pytest
import asyncio
import json
import time
from types import SimpleNamespace
from pathlib import Path

from mini_swe_rsi.core import read, tree_hash, write
from mini_swe_rsi.driver import initial_code
from mini_swe_rsi.sica import SicaAdapter


@pytest.mark.parametrize('process_status,agent_status,changed', [(0,'Submitted',True),(0,'Submitted',False),(0,'ContextWindowExceededError',False),(1,'ContextWindowExceededError',True)])
def test_original_meta_loop_keeps_parent_and_proposer_selection_on_resume(tmp_path, monkeypatch, process_status, agent_status, changed):
    monkeypatch.chdir(tmp_path)
    ids = [str(i) for i in range(4)]
    data = SimpleNamespace(ids=lambda split: ids, rows={i: {'problem_statement': i} for i in ids})
    executions = []

    class BrokerFixture:
        def session(self, *args, **kwargs):
            assert kwargs == {'calls':500, 'seconds':600}
            return 'fixture'

        def finish(self, token):
            return {'tokens': 20, 'cached_tokens': 0, 'calls': 1,
                    'deadline': time.time()+590, 'errors': []}

        def summary(self, messages, label):
            return 'Fixture summary'

    class DriverFixture:
        def task_retry(self, code, pid, trace):
            record = dict(problem_id=pid, score=1.0, tokens_used=20, num_cached_tokens=0,
                          cost_estimate=0, wall_time=1)
            last = trace/'attempt_0'
            write(last/'host_result.json', {'record': record, 'code_hash':tree_hash(code)})
            for filename in ('trace.txt', 'patch.diff'):
                (last/filename).write_text('fixture')
            return record

        def controller(self, code, logs, args, token, **kwargs):
            executions.append(str(code))
            assert args[args.index('--parent-iter')+1] == '2'
            assert args[args.index('--candidate-id')+1] == 'agent_3'
            assert args[args.index('--best-iter')+1] == ('1' if 'run_1' in str(code) else '0')
            target = kwargs['target']
            # The latest qualifying parent is 2; the best improver is 1.
            assert (target/'parent_marker.txt').read_text() == '2'
            if changed:
                with (target/'minisweagent/agents/default.py').open('a') as f:
                    f.write('\nFIXTURE_BEHAVIOR_CHANGE = True\n')
            write(logs/'result.json', {'exit_status': agent_status})
            write(logs/'activation.json', {'agent_file': str(code/'minisweagent/agents/default.py')})
            return process_status, 'fixture'

    adapter = SicaAdapter(tmp_path, DriverFixture(), BrokerFixture(), data)
    for run in (1, 2):
        exp = tmp_path/f'results/run_{run}'
        for i, scores in enumerate(([0, 0, 0, 0], [1, 0, 0, 0], [0, 0, 0, 0])):
            code = exp/f'agent_{i}/agent_code'
            initial_code(code)
            (code/'parent_marker.txt').write_text(str(i))
            b = exp/f'agent_{i}/benchmarks/swe_lite_dev'
            b.mkdir(parents=True)
            rows = [dict(problem_id=str(j), score=s, tokens_used=10,
                         num_cached_tokens=0, cost_estimate=0, wall_time=1) for j, s in enumerate(scores)]
            (b/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            write(b/'perf.json', dict(avg_score=sum(scores)/4, tokens=40, num_cached_tokens=0, cost=0, time=4))
        lineage = asyncio.run(adapter.proposal(run, 2, fixed=run == 2))
        assert lineage['parent'] == 2
        assert lineage['source_changed'] == changed
        assert lineage['process_status'] == process_status
        summary = (exp/'agent_2/meta_improvement_logs/summary.txt').read_text()
        if process_status == 0 and agent_status == 'Submitted' and changed:
            assert summary == 'Fixture summary'
        else:
            assert 'Agent code updated' not in summary
            assert 'No source change' in summary or 'remains eligible' in summary
        assert lineage['proposer'].endswith(f'agent_{1 if run == 1 else 0}/agent_code')
        assert asyncio.run(adapter.proposal(run, 2, fixed=run == 2)) == lineage
    assert len(executions) == 2  # A resume cannot silently generate a second proposal.
    exp = tmp_path/'results/run_1'
    asyncio.run(adapter.benchmark(exp, exp/'agent_3'))
    assert read(exp/'agent_3/benchmarks/swe_lite_dev/perf.json')['avg_score'] == 1.0
