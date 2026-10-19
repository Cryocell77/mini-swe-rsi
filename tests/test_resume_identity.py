"""Online recovery keeps task order, source identity and grading evidence bound."""
import pytest

from mini_swe_rsi.core import tree_hash, write
from mini_swe_rsi.driver import Driver, GradingUnavailable


def test_completed_task_cannot_be_reused_for_changed_source(tmp_path):
    driver = Driver.__new__(Driver)
    code = tmp_path/'code'
    code.mkdir()
    source = code/'agent.py'
    source.write_text('x=1\n')
    write(tmp_path/'task/attempt_0/host_result.json', {
        'code_hash': tree_hash(code), 'record': {'score': 1}})
    assert driver.task_retry(code, 'task', tmp_path/'task')['score'] == 1
    source.write_text('x=2\n')
    with pytest.raises(GradingUnavailable, match='mixed-version'):
        driver.task_retry(code, 'task', tmp_path/'task')


def test_interrupted_task_execution_is_graded_without_a_second_model_run(tmp_path, monkeypatch):
    driver = Driver.__new__(Driver)
    code = tmp_path/'code'
    code.mkdir()
    (code/'agent.py').write_text('x=1\n')
    attempt = tmp_path/'task/attempt_0'
    write(attempt/'task_execution.json', {
        'code_hash': tree_hash(code), 'record': {'score': 0},
        'result': {'exit_status': 'Submitted'}, 'metrics': {},
    })
    called = []
    driver.finish_task = lambda task, output, **kwargs: called.append((task, output)) or {'score': 0}
    assert driver.task_retry(code, 'task', tmp_path/'task') == {'score': 0}
    assert called and called[0][0] == 'task'
