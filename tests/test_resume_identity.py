import asyncio
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import tree_hash, write
from mini_swe_rsi.driver import Driver, GradingUnavailable
from mini_swe_rsi.experiment import Experiment


def test_completed_task_cannot_be_reused_for_changed_source(tmp_path):
    driver = Driver.__new__(Driver)
    code = tmp_path/'code'
    code.mkdir()
    source = code/'agent.py'
    source.write_text('x=1\n')
    write(tmp_path/'task/attempt_0/host_result.json', {'code_hash':tree_hash(code),
                                                    'record':{'score':1}})
    assert driver.task_retry(code, 'task', tmp_path/'task')['score'] == 1
    source.write_text('x=2\n')
    with pytest.raises(GradingUnavailable, match='mixed-version'):
        driver.task_retry(code, 'task', tmp_path/'task')


def test_evaluation_freezes_code_even_when_all_results_exist(tmp_path):
    experiment = Experiment.__new__(Experiment)
    code = tmp_path/'code'
    code.mkdir()
    source = code/'agent.py'
    source.write_text('x=1\n')
    experiment.data = SimpleNamespace(ids=lambda split:[])
    asyncio.run(experiment.evaluate_versions({'H0':code}, 'dev', tmp_path/'evaluation'))
    source.write_text('x=2\n')
    with pytest.raises(RuntimeError, match='mixed-version'):
        asyncio.run(experiment.evaluate_versions({'H0':code}, 'dev', tmp_path/'evaluation'))


def test_interrupted_probe_resumes_development_without_new_proposal(tmp_path):
    experiment = Experiment.__new__(Experiment)
    experiment.root = tmp_path
    write(tmp_path/'champions.json', {'native':1})
    (tmp_path/'probe_evidence').mkdir()
    expected = []
    for pair in range(2):
        for role in ('evolved','fixed'):
            path = tmp_path/f'probes/pair_{pair}/{role}'
            code = path/'agent_code'
            code.mkdir(parents=True)
            (code/'agent.py').write_text('x=1\n')
            write(path/'lineage.json', {'candidate_hash':tree_hash(code)})
            expected.append(path/'dev')
    observed = []
    async def evaluate(versions, split, output):
        assert split == 'dev'
        observed.append(output)
    experiment.evaluate_versions = evaluate
    asyncio.run(experiment.probes())
    assert set(observed) == set(expected)


def test_old_protocol_resume_rejected_before_any_runtime_or_budget_mutation(tmp_path):
    from mini_swe_rsi.experiment import Experiment
    write(tmp_path/'configuration.json', {'model': {'model':'same-actual-model'}})
    with pytest.raises(RuntimeError, match='Message protocol changed'):
        Experiment(tmp_path)
    assert not (tmp_path/'budget.json').exists()
