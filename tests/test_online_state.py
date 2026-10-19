"""Offline custody checks for the online scheduler.

These tests exercise host state and callback identity only; they never create a
Docker driver or call a model endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_swe_rsi.core import read, tree_hash, write
from mini_swe_rsi.driver import initial_code
from mini_swe_rsi.online import OnlineError, OnlineInterrupted, OnlineLoop, execution_hash


class Broker:
    def __init__(self):
        self.candidate_jobs = {}
        self.sessions = []

    def register_candidate_job(self, job_id, callback, metadata=None):
        self.candidate_jobs.setdefault(job_id, {'callback': callback, 'metadata': metadata or {}, 'result': None})

    def session(self, label, **kwargs):
        token = f't{len(self.sessions)}'
        self.sessions.append(token)
        return token

    def finish(self, token):
        return {'calls': 0, 'tokens': 0}

    def candidate_check(self, token, payload):
        job = self.candidate_jobs[payload['job_id']]
        if job['result'] is None:
            job['result'] = job['callback'](payload)
        return job['result']


class Driver:
    def __init__(self, tamper_task=False, diagnostic_tamper=None):
        self.tamper_task = tamper_task
        self.diagnostic_tamper = diagnostic_tamper
        self.controller_calls = []
        self.check_calls = []
        self.broker = None

    def task_retry(self, code, task_id, output):
        output = Path(output)
        (output / 'attempt_0').mkdir(parents=True, exist_ok=True)
        record = {'problem_id': task_id, 'score': 0.0}
        execution = {'code_hash': tree_hash(code), 'record': record}
        if self.tamper_task:
            execution['code_hash'] = 'wrong'
        write(output / 'attempt_0/task_execution.json', execution)
        write(output / 'attempt_0/host_result.json', {**execution, 'record': record})
        write(output / 'attempt_0/activation.json', {'command': 'benchmark'})
        root = output.parents[1]
        write(root / 'private/task_grading' / output.relative_to(root) / 'attempts.json', [])
        for name, value in {'trace.txt': 'trace', 'patch.diff': '',
                            'answer.txt': 'answer'}.items():
            (output / 'attempt_0' / name).write_text(value)
        return record

    def controller(self, code, logs, args, token, **kwargs):
        self.controller_calls.append((Path(code), tuple(args)))
        command_args = args
        feedback = read(kwargs['prompt'])
        feedback_task = feedback['task_id']
        version = 'H0000'
        target = Path(kwargs['target'])
        (target / 'minisweagent/state_marker.py').write_text('MARKER = 1\n')
        refs = feedback['evidence_refs']
        reports = {'1': {'report': 'failure', 'evidence_refs': refs,
                         'identity': '001_archive_explorer'},
                   '2': {'report': 'mechanism', 'evidence_refs': refs,
                         'identity': '002_archive_explorer'}}
        events = [
            {'kind': 'role_end', 'role': 'archive_explorer', 'step_index': 0,
             'identity': '001_archive_explorer',
             'result': {'exit_status': 'Submitted', 'submission': 'archive failure'}},
            {'kind': 'role_end', 'role': 'archive_explorer', 'step_index': 1,
             'identity': '002_archive_explorer',
             'result': {'exit_status': 'Submitted', 'submission': 'archive mechanism'}},
        ]
        if self.diagnostic_tamper == 'failure':
            events[0]['result'] = {'exit_status': 'RepeatedFormatError', 'submission': ''}
        elif self.diagnostic_tamper == 'wrong_role':
            events[0]['role'] = 'reviewer'
        elif self.diagnostic_tamper == 'wrong_step':
            events[0]['step_index'] = 1
        elif self.diagnostic_tamper == 'missing_identity':
            reports['1'].pop('identity')
        elif self.diagnostic_tamper == 'second_success':
            reports['1']['identity'] = '003_archive_explorer'
            events.append({
                'kind': 'role_end', 'role': 'archive_explorer', 'step_index': 0,
                'identity': '003_archive_explorer',
                'result': {'exit_status': 'Submitted', 'submission': 'archive retry'},
            })
        write(Path(logs) / 'diagnosis.json', {
            'task_id': feedback_task, 'round_id': 'R0001',
            'executor': 'H0000', 'proposer': 'H0000', 'parent': 'H0000',
            'decision': 'improve', 'reason': 'fixture', 'evidence_refs': refs,
            'reports': reports,
        })
        (Path(logs) / 'workflow_events.jsonl').write_text(
            ''.join(json.dumps(event) + '\n' for event in events))
        write(Path(logs) / 'check_plan_declared.json', {
            'revision': '1', 'criterion': 'current_task_failure_fixed',
        })
        if self.broker is not None:
            write(Path(logs) / 'workflow_state.json', {
                'status': 'improving', 'completed_steps': 4,
                'active': {'index': 4},
            })
            round_id = command_args[command_args.index('--round-id') + 1]
            candidate = command_args[command_args.index('--candidate-id') + 1]
            payload = {'job_id': f'candidate:{round_id}:{candidate}',
                       'task_id': feedback_task, 'round_id': round_id,
                       'candidate': candidate, 'parent_version': version,
                       'stage': 'candidate_check', 'check_revision': '1'}
            self.broker.candidate_check(token, payload)
        write(Path(logs) / 'workflow_state.json', {
            'status': 'completed', 'completed_steps': 7, 'active': None,
        })
        return 0, ''

    def candidate_check(self, candidate, task_id, output, *, job_id=None,
                        parent_hash, check_revision='1', parent_code=None,
                        mechanism_plan=None):
        self.check_calls.append((Path(candidate), {
            'job_id': job_id, 'parent_hash': parent_hash,
            'check_revision': check_revision, 'parent_code': parent_code,
            'mechanism_plan': mechanism_plan,
        }))
        record = {'problem_id': task_id, 'score': 1.0}
        output = Path(output)
        attempt = output / 'attempt_0'
        write(attempt / 'host_result.json', {
            'record': record, 'code_hash': tree_hash(candidate),
            'result': {'exit_status': 'Submitted'},
        })
        write(attempt / 'task_execution.json', {'code_hash': tree_hash(candidate)})
        write(attempt / 'activation.json', {'command': 'benchmark'})
        root = output.parents[2]
        write(root / 'private/candidate_checks' / (str(job_id).replace('/', '_') + '.json'),
              {'job_id': job_id, 'task_id': task_id, 'check_revision': str(check_revision),
               'candidate_hash': tree_hash(candidate), 'execution_hash': execution_hash(candidate),
               'parent_hash': parent_hash,
               'record': record})
        return {'job_id': job_id, 'task_id': task_id,
                'check_revision': str(check_revision),
                'execution_status': 'Submitted'}


class IncompleteDiagnosisDriver(Driver):
    def __init__(self, improve=False):
        super().__init__()
        self.improve = improve

    def controller(self, code, logs, args, token, **kwargs):
        logs = Path(logs)
        if self.improve:
            # This fixture only exercises proposal numbering after an explicit
            # improve decision; the incomplete workflow must reject before
            # diagnostic report validation can grant takeover authority.
            write(logs / 'diagnosis.json', {
                'task_id': 't1', 'round_id': 'R0001',
                'executor': 'H0000', 'proposer': 'H0000', 'parent': 'H0000',
                'decision': 'improve', 'reason': 'fixture', 'evidence_refs': [],
                'reports': {},
            })
            write(logs / 'workflow_state.json', {
                'status': 'improving', 'completed_steps': 2,
                'active': {'index': 2},
            })
        else:
            write(logs / 'workflow_state.json', {
                'status': 'incomplete', 'completed_steps': 1, 'active': None,
            })
        return 0, ''


class AbnormalProcessDriver(IncompleteDiagnosisDriver):
    def controller(self, code, logs, args, token, **kwargs):
        super().controller(code, logs, args, token, **kwargs)
        return 17, 'fixture process failure'


class TerminalProcessDriver(Driver):
    def __init__(self, workflow_status, process_status):
        super().__init__()
        self.workflow_status = workflow_status
        self.process_status = process_status

    def controller(self, code, logs, args, token, **kwargs):
        logs = Path(logs)
        logs.mkdir(parents=True, exist_ok=True)
        feedback = read(kwargs['prompt'])
        refs = feedback.get('evidence_refs', [])
        version = 'H0000'
        diagnosis = {
            'task_id': feedback['task_id'], 'round_id': 'R0001',
            'executor': version, 'proposer': version, 'parent': version,
            'decision': 'skip' if self.workflow_status == 'skipped' else 'improve',
            'reason': 'fixture', 'evidence_refs': refs,
            'reports': {'1': {'report': 'failure', 'evidence_refs': refs},
                        '2': {'report': 'mechanism', 'evidence_refs': refs}},
        }
        write(logs / 'diagnosis.json', diagnosis)
        (logs / 'workflow_events.jsonl').write_text(
            '{"kind":"role_end","identity":"001_archive_explorer"}\n'
            '{"kind":"role_end","identity":"002_archive_explorer"}\n')
        if self.workflow_status == 'skipped':
            write(logs / 'workflow_state.json', {
                'status': 'skipped_no_actionable_improvement', 'completed_steps': 2,
                'active': None, 'skipped_steps': [3, 4, 5, 6, 7],
            })
        else:
            target = Path(kwargs['target'])
            (target / 'minisweagent/terminal_fixture_marker.py').write_text('MARKER = 1\n')
            write(logs / 'check_plan_declared.json', {
                'revision': '1', 'criterion': 'current_task_failure_fixed',
            })
            write(logs / 'workflow_state.json', {
                'status': 'completed', 'completed_steps': 7, 'active': None,
            })
        return self.process_status, 'fixture process result'


def make_loop(tmp_path, driver=None, broker=None):
    source = tmp_path / 'source'
    initial_code(source)
    driver = driver or Driver()
    broker = broker or Broker()
    driver.broker = broker
    return OnlineLoop(tmp_path / 'run', ('t1',), driver=driver,
                      broker=broker, active_code=source,
                      model_identity={'model': 'fixture'},
                      service_identity={'endpoint': 'fixture'})


def test_callback_is_registered_before_check_and_uses_active_parent(tmp_path):
    driver, broker = Driver(), Broker()
    loop = make_loop(tmp_path, driver, broker)
    assert loop.run_once() == 'H0001'
    assert list(broker.candidate_jobs) == ['candidate:R0001:P0001']
    metadata = broker.candidate_jobs['candidate:R0001:P0001']['metadata']
    assert metadata['parent_version'] == 'H0000'
    assert metadata['candidate'] == 'P0001'
    assert len(metadata['session_token_sha256']) == 64
    assert driver.controller_calls[0][1][driver.controller_calls[0][1].index('--parent-iter') + 1] == '0'
    assert driver.check_calls[0][0].as_posix().endswith('P0001/agent_code')
    snapshot = read(loop.root / 'rounds/R0001/candidate_check.json')
    assert 'common_tasks' not in read(loop.root / 'rounds/R0001/check_plan.json')
    assert 'grade' not in json.dumps(snapshot)
    assert 'evidence_path' not in json.dumps(snapshot)


def test_candidate_public_result_rejects_reserved_common_tasks_field():
    from mini_swe_rsi.online import CandidateRejected, OnlineLoop

    with pytest.raises(CandidateRejected, match='common_tasks is unsupported'):
        OnlineLoop._public_check({'common_tasks': []})


def test_host_hash_mismatch_cannot_become_feedback(tmp_path):
    with pytest.raises(OnlineError, match='another Harness version'):
        make_loop(tmp_path, Driver(tamper_task=True), Broker()).run_once()


@pytest.mark.parametrize('diagnostic_tamper', [
    'failure', 'wrong_role', 'wrong_step', 'missing_identity'])
def test_diagnosis_requires_host_bound_successful_archive_reports(tmp_path, diagnostic_tamper):
    loop = make_loop(tmp_path, Driver(diagnostic_tamper=diagnostic_tamper), Broker())
    assert loop.run_once() == 'H0000'
    decision = read(loop.root / 'decisions/R0001.json')
    assert decision['decision'] == 'diagnosis_failed'
    assert decision['proposal'] is None
    assert loop.state['next_proposal'] == 1


def test_diagnosis_allows_multiple_successful_archive_calls_per_step(tmp_path):
    loop = make_loop(tmp_path, Driver(diagnostic_tamper='second_success'), Broker())
    assert loop.run_once() == 'H0001'
    decision = read(loop.root / 'decisions/R0001.json')
    assert decision['decision'] == 'accepted'
    assert decision['diagnosis']['reports']['1']['identity'] == '003_archive_explorer'


def test_active_source_tamper_is_rejected_on_restart(tmp_path):
    loop = make_loop(tmp_path, Driver(), Broker())
    (loop.active_code / 'minisweagent/tampered.py').write_text('x = 1\n')
    with pytest.raises(OnlineError, match='source or control identity'):
        OnlineLoop(loop.root, ('t1',), driver=Driver(), broker=Broker())


def test_recovery_with_execution_evidence_resumes_host_grading(tmp_path):
    loop = make_loop(tmp_path, Driver(), Broker())
    task_dir = loop._task_dir(0, 't1')
    task_dir.mkdir(parents=True, exist_ok=True)
    write(task_dir / 'attempt_0/task_execution.json', {
        'code_hash': tree_hash(loop.active_code),
        'record': {'problem_id': 't1', 'score': 0.0},
    })
    write(loop.root / 'online_state.json', {
        **loop.state, 'status': 'task_running', 'current_task': 't1',
        'current_round': 'R0001', 'current_proposal': None,
    })
    recovered = OnlineLoop(loop.root, ('t1',), driver=Driver(), broker=Broker(),
                           model_identity={'model': 'fixture'},
                           service_identity={'endpoint': 'fixture'})
    assert recovered.state['status'] == 'feedback_ready'
    assert (task_dir / 'feedback.json').exists()


def test_existing_workflow_state_blocks_new_model_process_without_mutation(tmp_path):
    driver, broker = Driver(), Broker()
    loop = make_loop(tmp_path, driver, broker)
    round_dir = loop._round_dir(1)
    round_dir.mkdir(parents=True, exist_ok=True)
    candidate_work = round_dir / 'candidate_work'
    candidate_work.mkdir()
    candidate_bytes = b'candidate bytes from interrupted run\n'
    (candidate_work / 'marker.txt').write_bytes(candidate_bytes)
    workflow_bytes = b'{"status":"improving","completed_steps":2}\n'
    (round_dir / 'workflow_state.json').write_bytes(workflow_bytes)

    with pytest.raises(OnlineInterrupted, match='workflow state'):
        loop.run_round({'task_id': 't1', 'round_id': 'R0001'})

    assert loop.state['status'] == 'interrupted'
    assert (candidate_work / 'marker.txt').read_bytes() == candidate_bytes
    assert (round_dir / 'workflow_state.json').read_bytes() == workflow_bytes
    assert driver.controller_calls == []
    assert driver.check_calls == []
    assert broker.sessions == []
    assert broker.candidate_jobs == {}


def test_interrupted_recovery_refuses_reopening_existing_workflow_state(tmp_path):
    loop = make_loop(tmp_path, Driver(), Broker())
    round_dir = loop._round_dir(1)
    round_dir.mkdir(parents=True, exist_ok=True)
    candidate_work = round_dir / 'candidate_work'
    candidate_work.mkdir()
    candidate_bytes = b'candidate bytes from interrupted run\n'
    (candidate_work / 'marker.txt').write_bytes(candidate_bytes)
    workflow_bytes = b'{"status":"improving","completed_steps":2}\n'
    (round_dir / 'workflow_state.json').write_bytes(workflow_bytes)
    write(loop.root / 'online_state.json', {
        **loop.state, 'status': 'interrupted', 'current_task': 't1',
        'current_round': 'R0001', 'current_proposal': 'P0001',
    })

    driver, broker = Driver(), Broker()
    with pytest.raises(OnlineInterrupted, match='existing model state'):
        OnlineLoop(loop.root, ('t1',), driver=driver, broker=broker,
                   model_identity={'model': 'fixture'},
                   service_identity={'endpoint': 'fixture'})

    assert (candidate_work / 'marker.txt').read_bytes() == candidate_bytes
    assert (round_dir / 'workflow_state.json').read_bytes() == workflow_bytes
    assert driver.controller_calls == []
    assert driver.check_calls == []
    assert broker.sessions == []
    assert broker.candidate_jobs == {}


def test_incomplete_diagnosis_does_not_allocate_proposal(tmp_path):
    driver, broker = IncompleteDiagnosisDriver(), Broker()
    loop = make_loop(tmp_path, driver, broker)
    assert loop.run_once() == 'H0000'
    decision = read(loop.root / 'decisions/R0001.json')
    assert decision['decision'] == 'diagnosis_failed'
    assert decision['status'] == 'diagnosis_failed'
    assert decision['proposal'] is None
    assert loop.state['next_proposal'] == 1
    assert not broker.candidate_jobs


def test_incomplete_improve_rejects_candidate_and_consumes_proposal(tmp_path):
    driver, broker = IncompleteDiagnosisDriver(improve=True), Broker()
    loop = make_loop(tmp_path, driver, broker)
    assert loop.run_once() == 'H0000'
    decision = read(loop.root / 'decisions/R0001.json')
    assert decision['decision'] == 'rejected'
    assert decision['proposal'] == 'P0001'
    assert loop.state['next_proposal'] == 2


def test_abnormal_improvement_process_stays_interrupted(tmp_path):
    loop = make_loop(tmp_path, AbnormalProcessDriver(), Broker())
    with pytest.raises(OnlineInterrupted):
        loop.run_once()
    assert loop.state['status'] == 'interrupted'
    assert loop.state['next_task_index'] == 0
    assert loop.state['next_proposal'] == 1
    assert not (loop.root / 'decisions/R0001.json').exists()


@pytest.mark.parametrize('workflow_status', ['completed', 'skipped'])
@pytest.mark.parametrize('process_status', [17, None, False])
def test_terminal_workflow_cannot_mask_abnormal_process_status(
        tmp_path, workflow_status, process_status):
    loop = make_loop(tmp_path, TerminalProcessDriver(workflow_status, process_status), Broker())
    with pytest.raises(OnlineInterrupted):
        loop.run_once()
    assert loop.state['status'] == 'interrupted'
    assert loop.state['next_task_index'] == 0
    assert loop.state['next_proposal'] == 1
    assert not (loop.root / 'decisions/R0001.json').exists()
