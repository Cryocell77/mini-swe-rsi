"""Recovery coverage for candidate decision construction and custody."""
from __future__ import annotations

import shutil

import pytest

from mini_swe_rsi.core import read, tree_hash, write
from mini_swe_rsi.driver import initial_code
from mini_swe_rsi.online import OnlineError, OnlineLoop, execution_hash
from tests.test_online_acceptance import FakeBroker, FakeDriver, make_loop


def _prepare_checking_recovery(tmp_path):
    initial = tmp_path / 'initial'
    initial_code(initial)
    driver = FakeDriver(('accept',))
    broker = FakeBroker()
    loop = make_loop(tmp_path, initial, driver, broker, tasks=('t1',))
    feedback = loop.execute_task()

    round_dir = loop._round_dir(1)
    proposal_dir = loop._proposal_dir(1)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    candidate = proposal_dir / 'agent_code'
    shutil.copytree(loop.active_code, candidate)
    (candidate / 'minisweagent' / 'recovery_marker.py').write_text('MARKER = 1\n')

    plan = {'criterion': 'current_task_failure_fixed', 'revision': '1'}
    write(round_dir / 'check_plan_declared.json', plan)
    write(round_dir / 'check_plan_frozen.json', plan)
    write(round_dir / 'diagnosis.json', {})
    write(round_dir / 'online_workflow_result.json', {
        'status': 'completed', 'completed_steps': 7, 'process_status': 0,
        'metrics': {'calls': 1, 'tokens': 10},
    })

    parent_full = feedback['full_source_hash']
    parent_exec = feedback['execution_hash']
    candidate_full = tree_hash(candidate)
    candidate_exec = execution_hash(candidate)
    job_id = 'candidate:R0001:P0001'
    write(loop.root / 'candidate_checks/candidate:R0001:P0001.json', {
        'status': 'completed', 'job_id': job_id,
        'metadata': {'job_id': job_id, 'task_id': 't1', 'round_id': 'R0001',
                     'candidate': 'P0001', 'parent_version': 'H0000',
                     'check_revision': '1', 'stage': 'candidate_check',
                     'agent': 'main', 'role': 'orchestrator',
                     'session_token_sha256': '0' * 64},
        'result': {'record': {'problem_id': 't1', 'score': 1.0}},
    })
    write(round_dir / 'candidate_check.json', {
        'job_id': job_id, 'task_id': 't1', 'check_revision': '1',
        'round_id': 'R0001', 'proposal': 'P0001', 'parent_version': 'H0000',
        'parent_hash': parent_full, 'parent_execution_hash': parent_exec,
        'candidate_hash': candidate_full, 'candidate_execution_hash': candidate_exec,
        'checked_candidate_hash': candidate_full,
        'checked_candidate_execution_hash': candidate_exec,
        'check': {'record': {'problem_id': 't1', 'score': 1.0}},
    })
    write(loop.root / 'online_state.json', {
        **loop.state, 'status': 'checking', 'current_task': 't1',
        'current_round': 'R0001', 'current_proposal': 'P0001',
    })
    return loop, driver, broker, candidate


def test_checking_recovery_creates_missing_candidate_identity(tmp_path):
    loop, driver, broker, candidate = _prepare_checking_recovery(tmp_path)

    recovered = OnlineLoop(loop.root, ('t1',), driver=driver, broker=broker,
                           model_identity={'model': 'fixture'},
                           service_identity={'endpoint': 'fixture'})

    identity = read(recovered.root / 'proposals/P0001/candidate_identity.json')
    assert identity == {
        'proposal': 'P0001', 'parent_version': 'H0000',
        'parent_hash': tree_hash(loop.root / 'versions/H0000/agent_code'),
        'candidate_hash': tree_hash(candidate),
        'execution_hash': execution_hash(candidate), 'plan_revision': '1',
    }
    assert read(recovered.root / 'decisions/R0001.json')['decision'] == 'accepted'
    assert recovered.active_version == 'H0001'


def test_checking_recovery_rejects_changed_existing_candidate_identity(tmp_path):
    loop, driver, broker, _ = _prepare_checking_recovery(tmp_path)
    write(loop.root / 'proposals/P0001/candidate_identity.json', {
        'proposal': 'P0001', 'parent_version': 'H0000',
        'parent_hash': 'tampered', 'candidate_hash': 'tampered',
        'execution_hash': 'tampered', 'plan_revision': '1',
    })

    with pytest.raises(OnlineError, match='Candidate identity differs'):
        OnlineLoop(loop.root, ('t1',), driver=driver, broker=broker,
                   model_identity={'model': 'fixture'},
                   service_identity={'endpoint': 'fixture'})


@pytest.mark.parametrize('corruption', ['summary', 'check_identity', 'job_identity', 'plan_revision'])
def test_recovery_never_creates_identity_before_evidence_validation(tmp_path, corruption):
    loop, driver, broker, _ = _prepare_checking_recovery(tmp_path)
    if corruption == 'summary':
        path = loop._round_dir(1) / 'online_workflow_result.json'
        workflow = read(path)
        workflow['execution_summary'] = {'round_id': 'R9999'}
        write(path, workflow)
    elif corruption == 'check_identity':
        path = loop._round_dir(1) / 'candidate_check.json'
        check = read(path)
        check.pop('candidate_hash')
        write(path, check)
    elif corruption == 'job_identity':
        path = loop.root / 'candidate_checks/candidate:R0001:P0001.json'
        job = read(path)
        job['metadata']['round_id'] = 'R9999'
        write(path, job)
    else:
        for name in ('check_plan_declared.json', 'check_plan_frozen.json'):
            path = loop._round_dir(1) / name
            plan = read(path)
            plan.pop('revision')
            write(path, plan)

    with pytest.raises(OnlineError, match='differs'):
        OnlineLoop(loop.root, ('t1',), driver=driver, broker=broker,
                   model_identity={'model': 'fixture'},
                   service_identity={'endpoint': 'fixture'})
    assert not (loop._proposal_dir(1) / 'candidate_identity.json').exists()
    assert not (loop.root / 'decisions/R0001.json').exists()


def test_checking_recovery_preserves_ordinary_rejection(tmp_path):
    loop, driver, broker, _ = _prepare_checking_recovery(tmp_path)
    path = loop._round_dir(1) / 'candidate_check.json'
    check = read(path)
    check['checked_candidate_execution_hash'] = 'different-checked-source'
    write(path, check)

    recovered = OnlineLoop(loop.root, ('t1',), driver=driver, broker=broker,
                           model_identity={'model': 'fixture'},
                           service_identity={'endpoint': 'fixture'})
    assert read(loop.root / 'decisions/R0001.json')['decision'] == 'rejected'
    assert recovered.active_version == 'H0000'
    assert not (loop._proposal_dir(1) / 'candidate_identity.json').exists()
