"""Result lookup must preserve failure classification and evidence custody."""
import shutil

import pytest

from mini_swe_rsi.core import read, result_matches_source, tree_hash, write
from mini_swe_rsi.driver import Driver, GradingUnavailable, InfrastructureError
from mini_swe_rsi.online import OnlineError
from tests.test_online_state import make_loop


@pytest.mark.parametrize('corrupt', ['null', '[]', '{incomplete'])
def test_corrupt_first_result_cannot_be_hidden_by_a_later_valid_result(tmp_path, corrupt):
    loop = make_loop(tmp_path)
    task_retry = loop.driver.task_retry

    def corrupt_result(code, task_id, output):
        record = task_retry(code, task_id, output)
        first = output / 'attempt_0/host_result.json'
        write(output / 'attempt_1/host_result.json', read(first))
        first.write_text(corrupt)
        return record

    loop.driver.task_retry = corrupt_result
    with pytest.raises(OnlineError, match='Task host result is corrupt'):
        loop.execute_task()
    assert not (loop.root / 'tasks/0001-t1/feedback.json').exists()
    assert loop.active_version == 'H0000'


def test_feedback_and_parent_record_select_the_same_matching_attempt(tmp_path):
    loop = make_loop(tmp_path)
    task_retry = loop.driver.task_retry

    def second_attempt(code, task_id, output):
        record = task_retry(code, task_id, output)
        first, second = output / 'attempt_0', output / 'attempt_1'
        shutil.move(first, second)
        (second / 'trace.txt').write_text('matching second attempt evidence')
        write(first / 'host_result.json', {
            'code_hash': 'another-source', 'record': {'score': 99},
        })
        return record

    loop.driver.task_retry = second_attempt
    feedback = loop.execute_task()
    task = loop.root / 'tasks/0001-t1'
    assert read(task / 'execution_summary.json')['attempt'] == 'attempt_1'
    assert (task / 'trace.txt').read_text() == 'matching second attempt evidence'
    assert loop._load_parent_hash(feedback) == feedback['full_source_hash']


@pytest.mark.parametrize('mutation', ['missing_record', 'wrong_task', 'wrong_score'])
def test_parent_hash_requires_matching_nested_feedback_record(tmp_path, mutation):
    loop = make_loop(tmp_path)
    feedback = loop.execute_task()
    host_path = loop._task_dir(0, 't1') / 'attempt_0/host_result.json'
    host = read(host_path)
    if mutation == 'missing_record':
        host.pop('record')
    elif mutation == 'wrong_task':
        host['record']['problem_id'] = 'other-task'
    else:
        host['record']['score'] = 99
    write(host_path, host)
    with pytest.raises(OnlineError, match='Parent task result'):
        loop._load_parent_hash(feedback)


def test_existing_summary_requires_complete_current_identity_and_is_not_overwritten(tmp_path):
    loop = make_loop(tmp_path)
    feedback = loop.execute_task()
    summary_path = loop._task_dir(0, 't1') / 'execution_summary.json'
    original = read(summary_path)
    broken = dict(original)
    broken.pop('attempt')
    write(summary_path, broken)
    with pytest.raises(OnlineError, match='summary identity'):
        loop._feedback('t1', 0, loop._task_dir(0, 't1'))
    assert read(summary_path) == broken


def test_result_source_identity_requires_only_complete_code_hash():
    assert result_matches_source({'code_hash': 'full'}, 'full')
    assert not result_matches_source({'code_hash': 'different'}, 'full')
    for record in ({'execution_hash': 'full'},
                   {'code_hash': 'full', 'execution_hash': 'stale'},
                   {'code_hash': 'full', 'full_source_hash': 'full'}):
        with pytest.raises(ValueError, match='non-canonical'):
            result_matches_source(record, 'full')


def test_candidate_metadata_cannot_fall_back_to_an_unmatched_top_level_record(tmp_path, monkeypatch):
    code = tmp_path / 'code'
    code.mkdir()
    (code / 'agent.py').write_text('value = 1\n')
    output = tmp_path / 'check'
    record = {'problem_id': 't1', 'score': 1}
    write(output / 'host_result.json', {
        'code_hash': 'another-source', 'record': record,
        'grade': {'resolved': True}, 'result': {'exit_status': 'Submitted'},
    })
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    monkeypatch.setattr('mini_swe_rsi.driver.validate_code', lambda _: None)
    monkeypatch.setattr(driver, 'task_retry', lambda *args, **kwargs: record)
    with pytest.raises(InfrastructureError, match='no matching completed host result'):
        driver.candidate_check(code, 't1', output, job_id='check', parent_hash=tree_hash(code))
    assert not (tmp_path / 'private/candidate_checks/check.json').exists()


def test_grading_recovery_cannot_reuse_a_linked_attempt(tmp_path, monkeypatch):
    code = tmp_path / 'code'
    code.mkdir()
    (code / 'agent.py').write_text('value = 1\n')
    outside = tmp_path / 'outside'
    write(outside / 'host_result.json', {
        'code_hash': tree_hash(code), 'record': {'score': 1},
    })
    output = tmp_path / 'task'
    output.mkdir()
    (output / 'attempt_0').symlink_to(outside, target_is_directory=True)
    driver = Driver.__new__(Driver)
    monkeypatch.setattr(driver, 'task', lambda *args, **kwargs: pytest.fail('Unexpected model run'))
    with pytest.raises(GradingUnavailable, match='not a regular directory'):
        driver.task_retry(code, 't1', output)
