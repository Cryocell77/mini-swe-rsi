"""Independent acceptance of original SICA -> archived mini -> RW edit -> inheritance."""
from mini_swe_rsi.context_budget import ContextBudget
import asyncio
import io
import json
import os
import shlex
import time
from types import SimpleNamespace

import pytest

from mini_swe_rsi.broker import Broker
from mini_swe_rsi.core import Budget, read, tree_hash, write
from mini_swe_rsi.driver import Driver, initial_code
from mini_swe_rsi.sica import SicaAdapter


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Explicit Docker integration test')
def test_original_sica_edits_candidate_then_executes_that_archived_improver(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(
        OPENAI_BASE_URL='https://fixture/v1', OPENAI_API_KEY='private-fixture-key', LOCAL_LLM_MODEL='fixture'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget, context=ContextBudget())
    broker.model = 'fixture'
    seen = []

    class Endpoint:
        def open(self, request, **kwargs):
            messages = json.loads(request.data)['messages']
            seen.append(messages)
            command = (
                "python - <<'PY'\n"
                "from pathlib import Path\nimport yaml\n"
                "p = Path('/home/agent/workdir/minisweagent/config/mini_textbased.yaml')\n"
                "data = yaml.safe_load(p.read_text())\n"
                "data['agent']['system_template'] += '\\nINHERITED_REPAIR_FIXTURE\\n'\n"
                "p.write_text(yaml.safe_dump(data))\nPY"
                if len(seen) % 4 == 2 else 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
            )
            if len(seen) % 4 == 1:
                command = 'sica-workflow '+shlex.quote(json.dumps({'op':'delegate','role':'software_developer','task':'Perform the fixture edit and return.'}))
            response = {'id': 'fixture', 'created': int(time.time()), 'object': 'chat.completion',
                        'model': 'fixture', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content':
                                    f'THOUGHT: Fixture edit.\n```mswea_bash_command\n{command}\n```'}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
            return io.BytesIO(json.dumps(response).encode())

    broker.opener = Endpoint()
    summaries = []

    def summarize(messages, label):
        summaries.append(messages)
        assert 'Agent code updated' in messages[1]['content']
        return 'Fixture: source changed; independent task benefit has not been evaluated.'

    broker.summary = summarize
    driver = Driver(tmp_path, None, broker, budget)
    ids = ['a', 'b', 'c', 'd']
    data = SimpleNamespace(ids=lambda split: ids, rows={i: {'problem_statement': i} for i in ids})
    adapter = SicaAdapter(tmp_path, driver, broker, data)
    experiment = tmp_path/'results/run_1'

    def performance(iteration, score):
        directory = experiment/f'agent_{iteration}/benchmarks/swe_lite_dev'
        directory.mkdir(parents=True, exist_ok=True)
        rows = [dict(problem_id=i, score=score, tokens_used=20, num_cached_tokens=0,
                     cost_estimate=0, wall_time=1) for i in ids]
        (directory/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        write(directory/'perf.json', dict(avg_score=score, tokens=80, num_cached_tokens=0, cost=0, time=4))

    try:
        code = experiment/'agent_0/agent_code'
        initial_code(code)
        original_hash = tree_hash(code)
        performance(0, 0)
        first = asyncio.run(adapter.proposal(1, 0))
        assert first['execution_succeeded'] and first['raw_source_changed']
        assert first['parent'] == 0 and first['proposer_hash'] == original_hash
        assert tree_hash(code) == original_hash  # The improver edited only its RW target.
        first_hash = tree_hash(experiment/'agent_1/agent_code')
        performance(1, 1)  # Deterministic selection fixture, not a measured model score.
        second = asyncio.run(adapter.proposal(1, 1))
        assert second['execution_succeeded'] and second['raw_source_changed']
        assert second['parent'] == 1 and second['proposer_hash'] == first_hash
        assert second['proposer'].endswith('agent_1/agent_code')
        assert tree_hash(experiment/'agent_1/agent_code') == first_hash
        assert 'INHERITED_REPAIR_FIXTURE' not in seen[0][0]['content']
        assert 'INHERITED_REPAIR_FIXTURE' in seen[4][0]['content']
        assert 'INHERITED_REPAIR_FIXTURE' in seen[5][0]['content']
        second_hash = tree_hash(experiment/'agent_2/agent_code')
        performance(2, 1)
        third = asyncio.run(adapter.proposal(1, 2, fixed=True))
        assert third['parent'] == 2 and third['proposer_hash'] == original_hash
        assert third['execution_succeeded'] and third['raw_source_changed']
        assert tree_hash(experiment/'agent_2/agent_code') == second_hash
        assert 'INHERITED_REPAIR_FIXTURE' not in seen[8][0]['content']
        assert 'INHERITED_REPAIR_FIXTURE' not in seen[9][0]['content']
        for index, (parent, proposer, candidate) in enumerate(((0, 0, 'agent_1'), (1, 1, 'agent_2'), (2, 0, 'agent_3'))):
            prompt = seen[index*4][1]['content']
            assert f'parent=agent_{parent}; proposer=agent_{proposer}; candidate={candidate}' in prompt
            assert 'as early as reasonably possible' in prompt
            assert 'current candidate has not been benchmarked' in prompt
            assert 'each agent invocation has its own iteration limit' in prompt and '"context_max_tokens": 32768' in prompt
            logs = experiment/f'agent_{index}/meta_improvement_logs'
            identity = read(logs/'improvement_task.json')
            assert read(logs/'improvement_verification.json')['status'] == 'verified'
            host_identity = read(logs/'host_improvement_task.json')
            assert identity == {k: v for k, v in host_identity.items() if k != 'contract'}
            assert (identity['parent'], identity['proposer'], identity['candidate']) == (parent, proposer, candidate)
            import hashlib
            assert identity['prompt_sha256'] == hashlib.sha256((logs/'improvement_prompt.txt').read_bytes()).hexdigest()
        assert len(seen) == 12 and len(summaries) == 3
        launches = [json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()
                    if json.loads(line)['kind'] == 'agent_launch']
        assert [e['module'] for e in launches] == ['archive.agent_0.agent_code', 'archive.agent_1.agent_code', 'archive.agent_0.agent_code']
        assert [e['code_hash'] for e in launches] == [original_hash, first_hash, original_hash]
        assert all(next(m['read_write'] for m in e['mounts']
                        if m['destination'] == '/home/agent/workdir') for e in launches)
        assert all(not next(m['read_write'] for m in e['mounts']
                            if m['destination'] == '/home/agent/archive') for e in launches)
        assert read(experiment/'agent_1/lineage.json')['proposer_hash'] == original_hash
    finally:
        driver.close()
        broker.close()
