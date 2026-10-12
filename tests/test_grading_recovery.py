import pytest

from mini_swe_rsi.core import Budget, tree_hash, write
from mini_swe_rsi.driver import Driver, GradingUnavailable, InfrastructureError
from mini_swe_rsi.grader import grading_failure_reason


def test_official_timeout_is_a_task_failure_not_an_infrastructure_retry():
    assert grading_failure_reason('EvaluationError: Test timed out after 300 seconds.') == 'tests_timeout'
    assert grading_failure_reason('>>>>> Tests Timed Out') == 'tests_timeout'
    assert grading_failure_reason('>>>>> Patch Apply Failed') == 'patch_apply_failed'
    assert grading_failure_reason('Docker daemon unavailable') is None


@pytest.mark.parametrize('recover', [True, False])
def test_grading_recovery_keeps_same_answer_and_respects_persistent_retry_cap(tmp_path, recover):
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    driver.budget = Budget(tmp_path)
    driver.budget.enter('baseline')
    code = tmp_path/'code'
    code.mkdir()
    (code/'implementation.py').write_text('x = 1\n')
    output = tmp_path/'task/attempt_0'
    write(output/'task_execution.json', {'result': {'exit_status': 'Submitted'},
        'metrics': {'tokens': 100, 'cached_tokens': 80, 'calls': 10},
        'agent_seconds': 2, 'started': 1, 'patch_base_commit': 'fixture',
        'code_hash': tree_hash(code), 'behavioral_hash': tree_hash(code, True)})
    (output/'patch.diff').write_text('same model-produced patch')
    calls = []
    def score(pid, patch, directory):
        calls.append(patch)
        if len(calls) == 1 or not recover:
            raise InfrastructureError('fixture scoring service unavailable')
        result = {'completed': True, 'resolved': True}
        write(directory/'result.json', result)
        return result
    driver.score = score
    driver.task = lambda *args: pytest.fail('A grading error must not rerun the model')
    if recover:
        assert driver.task_retry(code, 'task', tmp_path/'task')['score'] == 1
        assert driver.task_retry(code, 'task', tmp_path/'task')['tokens_used'] == 100
    else:
        for _ in range(2):
            with pytest.raises(GradingUnavailable):
                driver.task_retry(code, 'task', tmp_path/'task')
    assert calls == ['same model-produced patch']*2
