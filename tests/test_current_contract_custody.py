"""Current-only identities retain source custody and legal crash recovery."""
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import execution_hash, read, tree_hash, write
from mini_swe_rsi.online import OnlineError, OnlineLoop
from tests.test_online_state import make_loop


@pytest.mark.parametrize('key', [
    'task_id', 'round_id', 'version', 'full_source_hash', 'execution_hash', 'attempt',
])
@pytest.mark.parametrize('change', ['missing', 'wrong'])
def test_existing_task_summary_identity_cannot_be_repaired(tmp_path, key, change):
    loop = make_loop(tmp_path)
    loop.execute_task()
    task = loop._task_dir(0, 't1')
    path = task / 'execution_summary.json'
    summary = read(path)
    if change == 'missing':
        summary.pop(key)
    else:
        summary[key] = 'another-identity'
    write(path, summary)
    before = path.read_bytes()
    with pytest.raises(OnlineError, match='summary identity'):
        loop._feedback('t1', 0, task)
    assert path.read_bytes() == before


def test_missing_task_summary_is_rebuilt_without_task_inference(tmp_path):
    loop = make_loop(tmp_path)
    loop.execute_task()
    task = loop._task_dir(0, 't1')
    path = task / 'execution_summary.json'
    summary = read(path)
    path.unlink()
    loop.driver.task_retry = lambda *a, **kw: pytest.fail('Repeated inference')
    loop._feedback('t1', 0, task)
    assert read(path) == summary


@pytest.mark.parametrize('missing', ['job_id', 'metadata'])
def test_completed_candidate_without_identity_never_replays(tmp_path, missing):
    loop = OnlineLoop.__new__(OnlineLoop)
    loop.root = tmp_path
    loop.broker = SimpleNamespace(candidate_check=lambda *a: pytest.fail('Replayed check'))
    identity = {
        'job_id': 'candidate:R0001:P0001', 'task_id': 't1', 'round_id': 'R0001',
        'candidate': 'P0001', 'parent_version': 'H0000', 'check_revision': '1',
        'stage': 'candidate_check', 'agent': 'main', 'role': 'orchestrator',
        'session_token_sha256': '0' * 64,
    }
    path = tmp_path / 'candidate_checks/candidate:R0001:P0001.json'
    saved = {'job_id': identity['job_id'], 'metadata': identity,
             'status': 'completed', 'result': {}}
    saved.pop(missing)
    write(path, saved)
    before = path.read_bytes()
    with pytest.raises(OnlineError, match='Persisted candidate job record is invalid'):
        loop._check_candidate(tmp_path / 'candidate',
                              {'task_id': 't1', 'round_id': 'R0001', 'version': 'H0000'},
                              tmp_path / 'round', 'P0001', identity)
    assert path.read_bytes() == before


def test_public_driver_hashes_survive_private_record_enrichment(tmp_path):
    loop = make_loop(tmp_path)
    candidate_check = loop.driver.candidate_check

    def public_result(candidate, task_id, output, **kwargs):
        result = candidate_check(candidate, task_id, output, **kwargs)
        result.pop('record', None)
        return {**result, 'candidate_hash': tree_hash(candidate),
                'execution_hash': execution_hash(candidate)}

    loop.driver.candidate_check = public_result
    assert loop.run_once() == 'H0001'
    snapshot = read(loop.root / 'rounds/R0001/candidate_check.json')
    assert snapshot['check']['candidate_hash'] == snapshot['checked_candidate_hash']
    assert snapshot['check']['execution_hash'] == snapshot['checked_candidate_execution_hash']
    assert snapshot['check']['record']['score'] == 1


def test_private_candidate_completion_requires_parent_hash(tmp_path):
    loop = make_loop(tmp_path)
    candidate_check = loop.driver.candidate_check

    def missing_parent(candidate, task_id, output, **kwargs):
        result = candidate_check(candidate, task_id, output, **kwargs)
        path = loop.root / 'private/candidate_checks' / (kwargs['job_id'] + '.json')
        saved = read(path)
        saved.pop('parent_hash')
        write(path, saved)
        return result

    loop.driver.candidate_check = missing_parent
    assert loop.run_once() == 'H0000'
    decision = read(loop.root / 'decisions/R0001.json')
    assert decision['decision'] == 'rejected'
    assert 'parent identity' in decision['reason']
