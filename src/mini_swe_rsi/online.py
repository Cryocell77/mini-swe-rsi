"""Host-owned sequential online evolution state machine.

The online adapter is deliberately small: the fixed SICA workflow and the
host driver remain authoritative for model execution.  This module owns the
task/version cursor, durable identities, and the acceptance boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from pathlib import Path

from .core import (Data, ROOT, append_event, attempt_result_paths, digest,
                   execution_hash, read, read_regular_json,
                   result_matches_source, tree_hash, write, improvement_contract)
from .driver import Driver, InfrastructureError, initial_code, validate_code
from .broker import Broker, ModelError


class OnlineError(RuntimeError):
    pass


class OnlineInterrupted(OnlineError):
    pass


class CandidateRejected(OnlineError):
    pass


def _atomic_move(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise OnlineError(f'Refusing to overwrite immutable online version: {destination}')
    os.replace(str(source), str(destination))


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def freeze_task_selection(data, root, task_limit=None):
    """Freeze the unique ordered online task selection.

    The selected ids are always an ordered prefix of the approved development
    manifest; ``task_limit=None`` selects the complete manifest.  A restart may
    repeat the same limit but can never reorder, widen, shrink or otherwise
    replace the frozen sequence.
    """
    root = Path(root)
    if task_limit is not None and (type(task_limit) is not int or task_limit < 1):
        raise OnlineError('Online task_limit must be a positive integer')
    source_ids = list(data.ids('dev'))
    selected = source_ids if task_limit is None else source_ids[:task_limit]
    selection = {'ids': selected, 'task_limit': task_limit, 'source_ids': source_ids}
    path = root/'online_task_selection.json'
    if path.exists():
        saved = read(path)
        if saved != selection:
            raise OnlineError('Frozen online task selection changed')
        return list(saved['ids'])
    write(path, selection)
    return selected


class OnlineLoop:
    """Run a fixed task sequence against one active Harness version at a time."""

    protocol = 'mini-swe-rsi-online-v2'

    def __init__(self, root, tasks, driver=None, broker=None, active_code=None,
                 model_identity=None, service_identity=None, contract=None, resume_checkpoint=None):
        self.root = Path(root).resolve()
        self.tasks = tuple(tasks)
        if not self.tasks or len(set(self.tasks)) != len(self.tasks):
            raise OnlineError('Online task sequence must be nonempty and unique')
        self.driver = driver
        self.broker = broker
        self.model_identity = model_identity
        self.service_identity = service_identity
        self.resume_checkpoint = Path(resume_checkpoint) if resume_checkpoint is not None else None
        self.contract = contract or {
            'protocol': self.protocol,
            'tasks': list(self.tasks),
            'check_policy': {
                'revision': '1',
                'common_tasks': [],
                'criterion': 'current_task_failure_fixed_or_mechanism_parent_failed_candidate_passed',
            },
        }
        self.control_manifest = self._control_manifest()
        self._improvement_token = None
        self._load_or_initialize(active_code)

    @classmethod
    def from_environment(cls, root, data_path=None, billing_mode=None, task_limit=None,
                         resume_checkpoint=None):
        root = Path(root).resolve()
        # Earlier controls persisted an experiment-wide phase clock or an
        # execution_scope/dev_selection prefix.  Those artifacts cannot be
        # converted or silently reused under the online contract.
        for name in ('budget.json', 'execution_scope.json', 'dev_selection.json'):
            if (root/name).exists():
                raise OnlineError(
                    f'Legacy run artifact {name} is unsupported; start a new online run directory')
        existing_config = root/'configuration.json'
        if existing_config.exists() and read(existing_config).get('protocol') != cls.protocol:
            raise OnlineError('Existing native run cannot be resumed as online')
        existing_contract = read(existing_config) if existing_config.exists() else {}
        if task_limit is None and 'task_limit' in existing_contract:
            task_limit = existing_contract.get('task_limit')
        data = Data(data_path) if data_path else Data()
        data.freeze_images(root)
        selected_tasks = freeze_task_selection(data, root, task_limit)
        saved_billing = existing_contract.get('billing_mode')
        if billing_mode is not None and saved_billing not in (None, billing_mode):
            raise OnlineError('Frozen online billing identity changed')
        effective_billing = billing_mode if billing_mode is not None else saved_billing
        broker = Broker(root, billing_mode=effective_billing)
        try:
            model_identity = broker.identity()
            service_identity = getattr(broker, 'service_identity', None)
            driver = Driver(root, data, broker)
            contract = {
                'protocol': cls.protocol,
                'tasks': selected_tasks,
                'model': model_identity,
                'service': service_identity,
                'data': {
                    'manifest_sha256': digest(data.path / 'manifest.json'),
                    'raw_sha256': digest(data.path / 'raw.json'),
                    'image_inventory_sha256': digest(root / 'image_inventory.json'),
                },
                'runtime_image': getattr(getattr(driver, 'image', None), 'id', None),
                'billing_mode': effective_billing,
                'task_limit': task_limit,
                'improvement_task': improvement_contract(),
                'check_policy': {
                    'revision': '1', 'common_tasks': [],
                    'criterion': 'current_task_failure_fixed_or_mechanism_parent_failed_candidate_passed',
                },
            }
            if (root/'transport_policy.json').exists():
                contract['transport_policy'] = read(root/'transport_policy.json')
            code = root / 'versions/H0000/agent_code'
            if code.exists() and not existing_config.exists():
                raise OnlineError('Unidentified H0000 source cannot start online recovery')
            initial_code(code)
            loop = cls(root, selected_tasks, driver=driver, broker=broker,
                       active_code=code, model_identity=model_identity,
                       service_identity=service_identity, contract=contract,
                       resume_checkpoint=resume_checkpoint)
            return loop
        except Exception:
            if 'driver' in locals():
                try:
                    driver.close()
                except Exception:
                    pass
            broker.close()
            raise

    def close(self):
        self._finish_improvement_session()
        if self.driver is not None:
            self.driver.close()
        if self.broker is not None:
            self.broker.close()

    def _control_manifest(self):
        """Freeze the code that interprets the online contract."""
        files = [ROOT/'runtime/bridge.py', ROOT/'runtime/workflow.py',
                 ROOT/'runtime/relay.py', ROOT/'vendor/sica/agent_main.py']
        files += [ROOT/'src/mini_swe_rsi'/name for name in
                  ('online.py','driver.py','broker.py','archive_view.py','core.py','budget_policy.py',
                   'public_filter.py','recovery.py','stage_tools.py','tool_output.py')]
        files += [ROOT/'upstream.lock.json']
        files += sorted((ROOT/'vendor/sica_workflow').glob('*'))
        return {'improvement': improvement_contract(),
                'files': {str(p.relative_to(ROOT)): digest(p) for p in files if p.is_file()}}

    def _load_or_initialize(self, active_code):
        config_path = self.root/'configuration.json'
        state_path = self.root/'online_state.json'
        self.root.mkdir(parents=True, exist_ok=True)
        had_config = config_path.exists()
        if had_config:
            saved = read(config_path)
            expected_improvement = self.contract.get('improvement_task') or improvement_contract()
            for key, value in (('protocol', self.protocol), ('online_protocol', self.protocol), ('tasks', list(self.tasks)),
                               ('control_manifest', self.control_manifest),
                               ('check_policy', self.contract.get('check_policy')),
                               ('improvement_task', expected_improvement),
                               ('data', self.contract.get('data')),
                               ('runtime_image', self.contract.get('runtime_image')),
                               ('billing_mode', self.contract.get('billing_mode')),
                               ('transport_policy', self.contract.get('transport_policy')),
                               ('task_limit', self.contract.get('task_limit'))):
                if saved.get(key) != value:
                    raise OnlineError(f'Frozen online contract changed: {key}')
            for key, value in (('model', self.model_identity), ('service', self.service_identity)):
                if value is not None and saved.get(key) != value:
                    raise OnlineError(f'Frozen online identity changed: {key}')
            self.contract = saved
            self.model_identity, self.service_identity = saved.get('model'), saved.get('service')
        else:
            self.contract = {**self.contract, 'protocol': self.protocol,
                             'online_protocol': self.protocol,
                             'tasks': list(self.tasks), 'control_manifest': self.control_manifest,
                             'model': self.model_identity, 'service': self.service_identity,
                             'improvement_task': improvement_contract()}
            write(config_path, self.contract)
        target_code = self.root/'versions/H0000/agent_code'
        if not target_code.exists():
            if active_code is not None and Path(active_code).exists():
                target_code.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(Path(active_code), target_code)
            else:
                initial_code(target_code)
        code = target_code
        validate_code(code)
        h0 = self.root/'versions/H0000'
        identity = {'version': 'H0000', 'full_source_hash': tree_hash(code),
                    'execution_hash': execution_hash(code),
                    'control_manifest': self.control_manifest,
                    'control_manifest_sha256': self._contract_digest(),
                    'model_identity_sha256': self._identity_digest(self.model_identity),
                    'service_identity_sha256': self._identity_digest(self.service_identity)}
        identity_path = h0/'version_identity.json'
        if had_config and not identity_path.exists():
            raise OnlineError('Online contract exists without H0000 identity')
        if identity_path.exists() and read(identity_path) != identity:
            raise OnlineError('H0000 source or control identity changed')
        write(identity_path, identity)
        if not state_path.exists():
            write(state_path, {'protocol': self.protocol, 'active_version': 'H0000',
                'next_task_index': 0, 'next_round': 1, 'next_proposal': 1,
                'status': 'idle', 'current_task': None, 'current_round': None,
                'current_proposal': None})
        self._validate_state(read(state_path))
        self._recover()

    def _validate_state(self, state):
        required = ('active_version', 'next_task_index', 'next_round', 'next_proposal', 'status')
        if any(k not in state for k in required):
            raise OnlineError('Online state is incomplete')
        try:
            valid_version = state['active_version'] == f"H{int(state['active_version'][1:]):04d}"
        except (TypeError, ValueError, IndexError):
            valid_version = False
        if not valid_version:
            raise OnlineError('Invalid active version identity')
        if not isinstance(state['next_task_index'], int) or not 0 <= state['next_task_index'] <= len(self.tasks):
            raise OnlineError('Invalid task cursor')
        if not isinstance(state['next_round'], int) or state['next_round'] < 1:
            raise OnlineError('Invalid round cursor')
        if not isinstance(state['next_proposal'], int) or state['next_proposal'] < 1:
            raise OnlineError('Invalid proposal cursor')
        allowed = {'idle','task_running','task_done','feedback_ready','diagnosing',
                   'diagnosis_pending','improving','checking','decision_ready',
                   'round_committed','skipped','interrupted','completed'}
        if state['status'] not in allowed:
            raise OnlineError('Unknown online state')
        if state.get('current_task') is not None and state.get('current_task') not in self.tasks:
            raise OnlineError('State current task is outside frozen sequence')
        if state.get('current_task') is not None and state.get('current_round') != f"R{self.tasks.index(state['current_task']) + 1:04d}":
            raise OnlineError('State task and round identities differ')
        if state.get('current_round') is not None:
            if state.get('current_task') is None:
                raise OnlineError('State round has no current task')
            try:
                if state['current_round'] != f"R{int(state['current_round'][1:]):04d}":
                    raise ValueError
            except (TypeError, ValueError, IndexError):
                raise OnlineError('Invalid current round identity')
        if state.get('current_proposal') is not None:
            try:
                if state['current_proposal'] != f"P{int(state['current_proposal'][1:]):04d}":
                    raise ValueError
            except (TypeError, ValueError, IndexError):
                raise OnlineError('Invalid current proposal identity')

    @property
    def state(self):
        result = read(self.root/'online_state.json')
        self._validate_state(result)
        return result

    @property
    def active_version(self):
        return self.state['active_version']

    @property
    def active_code(self):
        path = self.root/'versions'/self.active_version/'agent_code'
        identity = read(path.parent/'version_identity.json')
        if (identity.get('version') != self.active_version or identity.get('control_manifest') != self.control_manifest
                or identity.get('control_manifest_sha256') not in (None, self._contract_digest())
                or identity.get('model_identity_sha256') not in (None, self._identity_digest(self.model_identity))
                or identity.get('service_identity_sha256') not in (None, self._identity_digest(self.service_identity))):
            raise OnlineError('Active version control identity differs from current runtime')
        if tree_hash(path) != identity.get('full_source_hash') or execution_hash(path) != identity.get('execution_hash'):
            raise OnlineError('Active version source changed after acceptance')
        return path

    def _save_state(self, **changes):
        current = self.state
        current.update(changes)
        self._validate_state(current)
        write(self.root/'online_state.json', current)

    def _event(self, kind, **event):
        append_event(self.root, {'kind': kind, **event})

    def _finish_improvement_session(self):
        token = self._improvement_token
        if token is None:
            return None
        self._improvement_token = None
        if self.broker is None:
            return None
        try:
            return self.broker.finish(token)
        except Exception:
            return {'status': 'finish_failed'}

    def _discard_unrequested_candidate_job(self, round_id, proposal_id, tolerate_completed=False):
        """Remove a pre-registered endpoint when diagnosis legitimately skips.

        The endpoint must exist before a model can reach step 5, but a valid
        two-step skip leaves no candidate job or durable candidate-check
        record behind.
        """
        job_id = f'candidate:{round_id}:{proposal_id}'
        jobs = self.broker.candidate_jobs
        job = jobs.get(job_id)
        if job is not None:
            if job.get('result') is not None:
                if not tolerate_completed:
                    raise OnlineError('Skip workflow invoked candidate check')
                return
            jobs.pop(job_id, None)
        path = self.root/'candidate_checks'/(job_id.replace('/','_') + '.json')
        if path.exists():
            saved = read(path)
            if saved.get('status') == 'completed' or saved.get('result') is not None:
                if not tolerate_completed:
                    raise OnlineError('Skip workflow has a completed candidate check')
                return
            path.unlink()

    def _task_dir(self, index, task_id):
        return self.root/'tasks'/f'{index + 1:04d}-{task_id}'

    def _round_dir(self, number):
        return self.root/'rounds'/f'R{number:04d}'

    def _proposal_dir(self, number):
        return self.root/'proposals'/f'P{number:04d}'

    @staticmethod
    def _safe_ref(root, ref):
        if not isinstance(ref, str) or not ref or Path(ref).is_absolute() or '..' in Path(ref).parts:
            return False
        path = Path(root)/ref
        return path.is_file() and not path.is_symlink() and ref.startswith('tasks/')

    def _validate_refs(self, refs):
        if not isinstance(refs, list) or not refs or any(not self._safe_ref(self.root, r) for r in refs):
            raise OnlineError('Feedback evidence must contain existing public task references')
        return list(dict.fromkeys(refs))

    def _contract_digest(self):
        return hashlib.sha256(json.dumps(self.control_manifest, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()

    @staticmethod
    def _identity_digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True,
                                         separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

    def _validate_feedback(self, feedback, index, task_id, version=None):
        if not isinstance(feedback, dict) or feedback.get('task_id') != task_id:
            raise OnlineError('Task feedback task identity differs')
        if feedback.get('task_index') != index or feedback.get('round_id') != f'R{index + 1:04d}':
            raise OnlineError('Task feedback cursor identity differs')
        expected_version = self.active_version if version is None else version
        if (feedback.get('version') != expected_version
                or feedback.get('executor') != expected_version
                or feedback.get('proposer') != expected_version
                or feedback.get('parent_version') != expected_version):
            raise OnlineError('Task feedback version identity differs')
        if feedback.get('control_manifest_sha256') != self._contract_digest():
            raise OnlineError('Task feedback control identity differs')
        if feedback.get('model_identity_sha256') != self._identity_digest(self.model_identity):
            raise OnlineError('Task feedback model identity differs')
        if feedback.get('service_identity_sha256') != self._identity_digest(self.service_identity):
            raise OnlineError('Task feedback service identity differs')
        code = self.root/'versions'/expected_version/'agent_code'
        if not code.exists():
            raise OnlineError('Task feedback references a missing Harness version')
        identity_path = code.parent/'version_identity.json'
        if not identity_path.exists() or read(identity_path).get('version') != expected_version:
            raise OnlineError('Task feedback references an invalid Harness version')
        if feedback.get('full_source_hash') != tree_hash(code):
            raise OnlineError('Task feedback full source hash differs')
        if feedback.get('execution_hash') != execution_hash(code):
            raise OnlineError('Task feedback execution hash differs')
        self._validate_refs(feedback.get('evidence_refs'))
        return feedback

    def _feedback(self, task_id, index, task_dir):
        # Driver-owned execution files are the source of task identity.  A
        # model-writable workflow state file cannot establish which Harness
        # actually ran the task.
        expected_full, expected_exec = tree_hash(self.active_code), execution_hash(self.active_code)
        host_paths = attempt_result_paths(task_dir)
        selected_host = None
        selected = None
        stale_host = False
        for host_path in host_paths:
            try:
                saved = read_regular_json(host_path)
                if saved is None:
                    continue
                hashes_match = result_matches_source(saved, {expected_full, expected_exec})
            except (OSError, TypeError, ValueError) as exc:
                raise OnlineError('Task host result is corrupt') from exc
            if not hashes_match:
                stale_host = True
                continue
            if not isinstance(saved.get('record'), dict):
                raise OnlineError('Task host result has no public score record')
            selected_host, selected = host_path, saved
            break
        if selected is None:
            if stale_host:
                raise OnlineError('Task host result is bound to another Harness version')
            raise OnlineError('Task has no completed host result')
        attempt = selected_host.parent
        execution_path = attempt/'task_execution.json'
        if execution_path.exists() or execution_path.is_symlink():
            try:
                execution = read_regular_json(execution_path)
                if execution is None:
                    raise ValueError('Task execution record is not a regular file')
                matches = result_matches_source(execution, {expected_full, expected_exec})
            except (OSError, TypeError, ValueError) as exc:
                raise OnlineError('Task execution record is corrupt') from exc
            if not matches:
                raise OnlineError('Task execution record is bound to another Harness version')
        record = selected['record']
        public_record = {key: record[key] for key in (
            'problem_id', 'score', 'passed', 'tokens_used', 'num_cached_tokens',
            'wall_time', 'timed_out', 'cost_threshold_exceeded',
            'token_accounting_complete') if key in record}
        summary = task_dir/'execution_summary.json'
        if summary.exists() and summary.is_file() and not summary.is_symlink():
            try:
                old_summary = read(summary)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise OnlineError('Task execution summary is corrupt') from exc
            if old_summary.get('task_id') not in (None, task_id):
                raise OnlineError('Task execution summary task identity differs')
            summary_hash = old_summary.get('execution_hash', old_summary.get('code_hash'))
            if summary_hash not in (None, expected_full, expected_exec):
                raise OnlineError('Task execution summary version differs')
        result_info = selected.get('result') if isinstance(selected.get('result'), dict) else {}
        metrics = selected.get('metrics') if isinstance(selected.get('metrics'), dict) else {}
        public_metrics = {key: metrics[key] for key in (
            'tokens_used', 'tokens', 'num_cached_tokens', 'wall_time',
            'cost_threshold_exceeded', 'token_accounting_complete') if key in metrics}
        write(summary, {'task_id': task_id, 'round_id': f'R{index + 1:04d}',
                        'version': self.active_version,
                        'full_source_hash': expected_full, 'execution_hash': expected_exec,
                        'exit_status': result_info.get('exit_status'),
                        'record': public_record, 'metrics': public_metrics,
                        'attempt': attempt.name})
        # Promote only the public evidence needed by archive_view.  The
        # private host_result/grade and full task_execution records remain in
        # their attempt directory and are never placed in model-visible refs.
        public_names = ('trace.txt', 'patch.diff', 'answer.txt')
        for name in public_names:
            src = attempt/name
            dst = task_dir/name
            if src.exists() and src.is_file() and not src.is_symlink() and src != dst:
                shutil.copyfile(src, dst)
        activation_src = attempt/'activation.json'
        activation_dst = task_dir/'activation.json'
        if activation_src.exists() and activation_src.is_file() and not activation_src.is_symlink():
            try:
                activation = read(activation_src)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise OnlineError('Task activation evidence is corrupt') from exc
            if not isinstance(activation, dict):
                raise OnlineError('Task activation evidence is invalid')
            allowed_activation = ('command', 'agent_activation_unobserved', 'agent_file',
                                  'config_file', 'role', 'protocol', 'version',
                                  'task_id', 'round_id')
            write(activation_dst, {key: activation[key] for key in allowed_activation
                                   if key in activation})
        write(task_dir/'public_feedback.json', {
            'task_id': task_id, 'task_index': index, 'round_id': f'R{index + 1:04d}',
            'version': self.active_version, 'executor': self.active_version,
            'parent_version': self.active_version, 'record': public_record,
            'full_source_hash': expected_full, 'execution_hash': expected_exec,
            'evidence_refs': [f'tasks/{task_dir.name}/{n}' for n in
                              ('trace.txt','patch.diff','answer.txt','activation.json','execution_summary.json')
                              if (task_dir/n).exists()]})
        refs = []
        for name in ('trace.txt','patch.diff','answer.txt','activation.json','execution_summary.json'):
            p = task_dir/name
            if p.exists() and p.is_file():
                refs.append(str(p.relative_to(self.root)))
        refs = self._validate_refs(refs)
        identity = self.active_version
        return {'task_id': task_id, 'task_index': index, 'round_id': f'R{index + 1:04d}',
                'version': identity, 'executor': identity, 'proposer': identity,
                'parent_version': identity, 'full_source_hash': tree_hash(self.active_code),
                'execution_hash': execution_hash(self.active_code),
                'control_manifest_sha256': self._contract_digest(),
                'model_identity_sha256': self._identity_digest(self.model_identity),
                'service_identity_sha256': self._identity_digest(self.service_identity),
                'model_identity': self.model_identity, 'service_identity': self.service_identity,
                'record': public_record,
                'evidence_refs': refs, 'public': True}

    def execute_task(self, index=None):
        state = self.state
        index = state['next_task_index'] if index is None else index
        if index != state['next_task_index'] or index >= len(self.tasks):
            raise OnlineError('Task cursor does not permit this execution')
        task_id = self.tasks[index]
        task_dir = self._task_dir(index, task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        version, code = self.active_version, self.active_code
        self._save_state(status='task_running', current_task=task_id,
                         current_round=f'R{index + 1:04d}')
        if (task_dir/'feedback.json').exists():
            feedback = read(task_dir/'feedback.json')
            return self._validate_feedback(feedback, index, task_id)
        if self.driver is None:
            raise OnlineError('Online driver is not configured')
        before = (tree_hash(code), execution_hash(code))
        self.driver.task_retry(code, task_id, task_dir)
        if (tree_hash(code), execution_hash(code)) != before:
            raise OnlineError('Active Harness changed while executing task')
        feedback = self._feedback(task_id, index, task_dir)
        write(task_dir/'feedback.json', feedback)
        self._save_state(status='feedback_ready', current_task=task_id,
                         current_round=feedback['round_id'], current_proposal=None)
        self._event('task_feedback_ready', task_id=task_id, version=version,
                    round_id=feedback['round_id'])
        return feedback

    def _load_parent_record(self, feedback):
        path = self._task_dir(feedback['task_index'], feedback['task_id'])
        candidates = attempt_result_paths(path)
        stale = False
        for candidate in candidates:
            try:
                data = read_regular_json(candidate)
                if data is None:
                    continue
                matches = result_matches_source(
                    data, {feedback['execution_hash'], feedback['full_source_hash']})
            except (OSError, TypeError, ValueError) as exc:
                raise OnlineError('Parent task result is corrupt') from exc
            if not matches:
                stale = True
                continue
            record = data.get('record', data)
            if isinstance(record, dict):
                # Preserve host source identity for Driver's private
                # candidate record. The public prompt never receives this
                # host payload.
                return {**record,
                        'code_hash': data.get('code_hash', feedback['full_source_hash']),
                        'execution_hash': data.get('execution_hash', feedback['execution_hash']),
                        'full_source_hash': data.get('full_source_hash', feedback['full_source_hash'])}
            return record
        if stale:
            raise OnlineError('Parent task result source identity differs')
        raise OnlineError('Parent task has no completed host result')

    def _improve_with_driver(self, feedback, round_dir, proposal_id):
        existing_workflow = Path(round_dir)/'workflow_state.json'
        resume_path = self.resume_checkpoint
        resume = None
        if resume_path is not None:
            from .recovery import load_workflow_authorization
            resume = load_workflow_authorization(
                resume_path, self.root, round_dir, Path(round_dir)/'candidate_work',
                {'task_id':feedback['task_id'], 'round_id':feedback['round_id'],
                 'version':self.active_version, 'parent':int(self.active_version[1:]),
                 'proposer':int(self.active_version[1:]), 'candidate':proposal_id})
        if (existing_workflow.exists() or existing_workflow.is_symlink()) and resume is None:
            raise OnlineInterrupted('Existing workflow state cannot be reopened for a new model process')
        if self.driver is None or self.broker is None:
            raise OnlineError('Online driver and broker are required for improvement')
        parent = self.active_version
        parent_full, parent_exec = tree_hash(self.active_code), execution_hash(self.active_code)
        candidate_work = round_dir/'candidate_work'
        if resume is None:
            if candidate_work.exists():
                shutil.rmtree(candidate_work)
            shutil.copytree(self.active_code, candidate_work)
        task_id, round_id = feedback['task_id'], feedback['round_id']
        job_id = f'candidate:{round_id}:{proposal_id}'
        proposal_dir = self._proposal_dir(int(proposal_id[1:]))
        identity = {'job_id': job_id, 'task_id': task_id, 'round_id': round_id,
                    'candidate': proposal_id, 'parent_version': parent,
                    'stage': 'candidate_check', 'check_revision': '1',
                    # bridge.py identifies the main coordinator as ``main``;
                    # the candidate is still a separate host job and source
                    # process even though its relay identity is main.
                    'agent': 'main', 'role': 'orchestrator'}
        plan = {'revision': '1', 'identity': identity, 'parent_version': parent,
                'parent_hash': parent_full, 'parent_execution_hash': parent_exec,
                'criterion': None,
                'criterion_options': ['current_task_failure_fixed',
                                      'mechanism_parent_failed_candidate_passed'],
                'criterion_source': 'diagnosis',
                'task_id': task_id, 'common_tasks': self.contract['check_policy'].get('common_tasks', [])}
        write(round_dir/'check_plan.json', plan)
        prompt_path = round_dir/f'{task_id}_feedback.json'
        write(prompt_path, feedback)
        frozen = {'path': None}
        check_dir = self._proposal_dir(int(proposal_id[1:]))/'check'
        parent_record = self._load_parent_record(feedback)

        def callback(payload):
            state_path = round_dir/'workflow_state.json'
            if not state_path.exists():
                raise CandidateRejected('Candidate check requested without workflow state')
            flow_state = read(state_path)
            active = flow_state.get('active') or {}
            if (flow_state.get('status') != 'improving' or flow_state.get('completed_steps') != 4
                    or active.get('index') != 4):
                raise CandidateRejected('Candidate check is outside workflow step 5')
            # Freeze exactly the source visible before the first check request.
            if frozen['path'] is None:
                frozen['path'] = proposal_dir/'agent_code'
                if frozen['path'].exists():
                    shutil.rmtree(frozen['path'])
                frozen['path'].parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(candidate_work, frozen['path'])
                checked_copy = round_dir/'candidate_check_source'
                if checked_copy.exists():
                    shutil.rmtree(checked_copy)
                shutil.copytree(frozen['path'], checked_copy)
                write(round_dir/'candidate_snapshot.json', {
                    'candidate_hash': tree_hash(frozen['path']),
                    'execution_hash': execution_hash(frozen['path']), 'identity': identity})
            # Step 3 is the sole authority for the acceptance criterion.  The
            # coordinator may not infer one from the public score or from a
            # host compatibility default after the workflow has reached step
            # 5.  The workflow writes this model-declared file atomically.
            declared_path = round_dir/'check_plan_declared.json'
            if not declared_path.exists() or not declared_path.is_file() or declared_path.is_symlink():
                raise CandidateRejected('Candidate check requested without a frozen step-3 check plan')
            declared_plan = read(declared_path)
            options = {'current_task_failure_fixed',
                       'mechanism_parent_failed_candidate_passed'}
            if (not isinstance(declared_plan, dict)
                    or declared_plan.get('criterion') not in options):
                raise CandidateRejected('Step-3 check plan has no legal acceptance criterion')
            frozen_plan = {**declared_plan,
                           'parent_version': parent,
                           'parent_hash': parent_full,
                           'parent_execution_hash': parent_exec,
                           'identity': {k: identity[k] for k in
                                        ('job_id','task_id','round_id','candidate',
                                         'parent_version','stage','check_revision','agent','role')}}
            frozen_plan_path = round_dir/'check_plan_frozen.json'
            if frozen_plan_path.exists():
                existing = read(frozen_plan_path)
                existing_declared = {k: existing.get(k) for k in declared_plan}
                if existing_declared != declared_plan or any(
                        existing.get(k) != frozen_plan.get(k)
                        for k in ('parent_version','parent_hash','parent_execution_hash','identity')):
                    raise CandidateRejected('Candidate check plan changed after step 5 was frozen')
            else:
                write(frozen_plan_path, frozen_plan)
            mechanism_plan = frozen_plan.get('mechanism_plan')
            kwargs = {'job_id': job_id,
                      'parent_record': {**parent_record, 'code_hash': parent_full,
                                        'execution_hash': parent_exec},
                      'check_revision': '1'}
            kwargs['parent_code'] = self.active_code
            kwargs['mechanism_plan'] = mechanism_plan
            result = self.driver.candidate_check(frozen['path'], task_id, check_dir, **kwargs)
            return result

        token = self.broker.session('online:' + round_id, seconds=None, calls=None)
        self._improvement_token = token
        if resume is not None:
            from .recovery import restore_session_accounting
            restore_session_accounting(self.broker, token, resume['accounting'])
            self._event('workflow_restore_started', round_id=round_id,
                        completed_steps=resume['state']['completed_steps'],
                        authorization_sha256=digest(resume_path), new_model_conversation=True)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        identity['session_token_sha256'] = token_hash
        # Registration must use the exact authenticated improvement session.
        # The token hash is host metadata only and never enters the model
        # payload.
        self.broker.register_candidate_job(job_id, callback, identity)
        try:
            args = ['improve', '--task-id', task_id, '--round-id', round_id,
                    '--candidate-id', proposal_id, '--parent-iter', str(int(parent[1:])),
                    '--best-iter', str(int(parent[1:])), '--current-iter', str(int(parent[1:])),
                    '--online-version', parent]
            # Driver mounts the host prompt at /task.txt inside the isolated
            # container; passing the host path would make bridge.py read a
            # nonexistent container path and change the rendered identity.
            args += ['--prompt-file', '/task.txt']
            restore_kwargs = {'resume_checkpoint':resume_path} if resume is not None else {}
            self.resume_checkpoint = None
            status, _ = self.driver.controller(self.active_code, round_dir, args, token,
                                                archive=self.root, target=candidate_work,
                                                prompt=prompt_path, **restore_kwargs)
            workflow = read(round_dir/'workflow_state.json') if (round_dir/'workflow_state.json').exists() else {'status': 'incomplete'}
            workflow['process_status'] = status
            workflow['metrics'] = None
            token = None
            write(round_dir/'online_workflow_result.json', workflow)
            return workflow, candidate_work, identity, plan
        finally:
            if token is not None:
                try:
                    metrics = self.broker.finish(token)
                except Exception:
                    metrics = {'status': 'finish_failed'}
                if self._improvement_token == token:
                    self._improvement_token = None
                if (round_dir/'online_workflow_result.json').exists():
                    result = read(round_dir/'online_workflow_result.json')
                    result.setdefault('metrics', metrics)
                    write(round_dir/'online_workflow_result.json', result)

    def _check_candidate(self, candidate, feedback, round_dir, proposal_id, identity=None):
        identity = identity or {'job_id': f'candidate:{feedback["round_id"]}:{proposal_id}',
                                'task_id': feedback['task_id'], 'round_id': feedback['round_id'],
                                'candidate': proposal_id, 'parent_version': feedback['version'],
                                'check_revision': '1', 'stage': 'candidate_check'}
        job_id = identity['job_id']
        record_path = self.root/'candidate_checks'/(job_id.replace('/','_') + '.json')
        if record_path.exists():
            persisted = read(record_path)
            if persisted.get('status') == 'completed' and persisted.get('metadata'):
                if any(persisted['metadata'].get(k) != value for k, value in identity.items()):
                    raise OnlineError('Persisted candidate job identity differs')
                if persisted.get('job_id') not in (None, job_id):
                    raise OnlineError('Persisted candidate job id differs')
                result = persisted.get('result')
            else:
                result = None
        else:
            result = None
        frozen = round_dir/'candidate_check_source'
        if not frozen.exists():
            frozen.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidate, frozen, dirs_exist_ok=True)
        before_full, before_exec = tree_hash(frozen), execution_hash(frozen)
        if result is None:
            token = self._improvement_token
            own_token = token is None
            if own_token:
                token = self.broker.session('candidate:' + job_id, seconds=None, calls=None)
            try:
                payload = dict(identity)
                result = self.broker.candidate_check(token, payload)
            finally:
                if own_token:
                    self.broker.finish(token)
        if not isinstance(result, dict):
            raise CandidateRejected('Candidate check returned no host result')
        # Select one matching attempt for the score, activation and execution
        # evidence, including when task_retry succeeded on its second attempt.
        check_output = self._proposal_dir(int(proposal_id[1:]))/'check'
        selected_attempt = None
        for host_path in attempt_result_paths(check_output):
            try:
                host = read_regular_json(host_path)
                matches = host is not None and result_matches_source(host, {before_full, before_exec})
            except (OSError, TypeError, ValueError) as exc:
                raise CandidateRejected('Candidate host result is corrupt') from exc
            if matches:
                selected_attempt = host_path.parent
                break
        if selected_attempt is None:
            raise CandidateRejected('Candidate check has no completed host result for its frozen source')
        if 'record' not in result:
            result = {**result, 'record': host.get('record'),
                      'execution_hash': host.get('execution_hash', host.get('code_hash')),
                      'candidate_hash': host.get('candidate_hash')}
        if not isinstance(result.get('record'), dict):
            raise CandidateRejected('Candidate check returned no host score record')
        private_path = self.root/'private/candidate_checks'/(job_id.replace('/', '_') + '.json')
        try:
            private_result = read_regular_json(private_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CandidateRejected('Candidate private completion record is corrupt') from exc
        if private_result is None:
            raise CandidateRejected('Candidate check has no private host completion record')
        if (private_result.get('job_id') != job_id
                or private_result.get('task_id') != feedback['task_id']
                or str(private_result.get('check_revision')) != str(identity.get('check_revision', '1'))
                or private_result.get('candidate_hash') is None
                or private_result.get('execution_hash') is None):
            raise CandidateRejected('Candidate private completion identity differs')
        if private_result.get('parent_hash') not in (None, feedback['full_source_hash']):
            raise CandidateRejected('Candidate private parent identity differs')
        activation_path = selected_attempt/'activation.json'
        try:
            activation = read_regular_json(activation_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CandidateRejected('Candidate activation evidence is corrupt') from exc
        if not isinstance(activation, dict) or activation.get('command') != 'benchmark':
            raise CandidateRejected('Candidate check lacks host activation evidence for a fresh process')
        task_execution = selected_attempt/'task_execution.json'
        try:
            task_execution_data = read_regular_json(task_execution)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CandidateRejected('Candidate task execution evidence is corrupt') from exc
        if task_execution_data is None or task_execution_data.get('code_hash') not in (before_full,):
            raise CandidateRejected('Candidate check task execution identity is missing or stale')
        after_full, after_exec = tree_hash(frozen), execution_hash(frozen)
        if (before_full, before_exec) != (after_full, after_exec):
            raise CandidateRejected('Candidate source changed during check')
        candidate_full, candidate_exec = tree_hash(candidate), execution_hash(candidate)
        if candidate_exec != before_exec:
            raise CandidateRejected('Candidate changed after its check snapshot')
        if (private_result.get('candidate_hash') != before_full
                or private_result.get('execution_hash') != before_exec):
            raise CandidateRejected('Candidate private source hash differs from checked source')
        private_record = private_result.get('record')
        public_record = result.get('record') if isinstance(result.get('record'), dict) else None
        if (isinstance(private_record, dict) and isinstance(public_record, dict)
                and private_record.get('score') != public_record.get('score')):
            raise CandidateRejected('Candidate private/public score records differ')
        check = self._public_check(result)
        snapshot = {'job_id': job_id, 'task_id': feedback['task_id'],
                    'check_revision': str(identity.get('check_revision','1')),
                    'round_id': feedback['round_id'], 'proposal': proposal_id,
                    'parent_version': feedback['version'],
                    'parent_hash': feedback['full_source_hash'],
                    'parent_execution_hash': feedback['execution_hash'],
                    'candidate_hash': candidate_full, 'candidate_execution_hash': candidate_exec,
                    'checked_candidate_hash': before_full,
                    'checked_candidate_execution_hash': before_exec, 'check': check}
        snapshot['activation'] = {k: activation[k] for k in ('command','agent_file','config_file') if k in activation}
        write(round_dir/'candidate_check.json', snapshot)
        return snapshot

    @staticmethod
    def _public_check(result):
        # A driver may return private grade structures and host paths.  Keep a
        # tiny allowlist for model-visible/public snapshots.
        out = {}
        for key in ('job_id','task_id','check_revision','round_id','candidate','parent_version',
                    'candidate_hash','execution_hash','parent_hash','execution_status','metrics',
                    'score','passed','record','mechanism_check','common_tasks'):
            if key in result:
                value = result[key]
                if key == 'record' and isinstance(value, dict):
                    value = {k: value[k] for k in ('problem_id','score','passed') if k in value}
                if key == 'mechanism_check' and isinstance(value, dict):
                    value = {k: value[k] for k in
                             ('test_path','test_sha256','same_test','parent_failed','candidate_passed')
                             if k in value}
                out[key] = value
        return out

    def _acceptance(self, candidate, feedback, snapshot, plan=None):
        check = snapshot.get('check', {})
        record = check.get('record', {}) if isinstance(check, dict) else {}
        parent_score = feedback.get('record', {}).get('score')
        candidate_score = record.get('score', check.get('score'))
        final_full, final_exec = tree_hash(candidate), execution_hash(candidate)
        source_changed = final_full != snapshot['parent_hash']
        exec_changed = final_exec != snapshot['parent_execution_hash']
        checked_match = final_exec == snapshot['checked_candidate_execution_hash']
        score_known = _finite_number(candidate_score) and _finite_number(parent_score)
        current_fixed = score_known and parent_score < 1 and candidate_score >= 1
        mech = check.get('mechanism_check') if isinstance(check, dict) else None
        mechanism = isinstance(mech, dict) and mech.get('parent_failed') is True and mech.get('candidate_passed') is True
        common_ok = True
        common = check.get('common_tasks', {}) if isinstance(check, dict) else {}
        required_common = (plan or {}).get('common_tasks', self.contract['check_policy'].get('common_tasks', []))
        if required_common:
            common_ok = isinstance(common, dict) and set(common) >= set(required_common)
            if common_ok:
                for task_id in required_common:
                    pair = common[task_id]
                    if isinstance(pair, dict):
                        common_ok = common_ok and _finite_number(pair.get('parent')) and _finite_number(pair.get('candidate')) and pair['candidate'] >= pair['parent']
                    else:
                        common_ok = common_ok and _finite_number(pair)
        elif common:
            common_ok = isinstance(common, dict) and all(
                isinstance(v, dict) and _finite_number(v.get('parent'))
                and _finite_number(v.get('candidate')) and v['candidate'] >= v['parent']
                for v in common.values())
        selected = (plan or {}).get('criterion')
        criterion_passed = current_fixed if selected == 'current_task_failure_fixed' else mechanism if selected == 'mechanism_parent_failed_candidate_passed' else False
        non_regression = score_known and candidate_score >= parent_score
        passed = bool(source_changed and exec_changed and checked_match and common_ok and non_regression and criterion_passed)
        return {'passed': passed, 'source_changed': source_changed, 'execution_changed': exec_changed,
                'checked_execution_matches': checked_match, 'score_known': score_known,
                'non_regression': non_regression,
                'parent_score': parent_score, 'candidate_score': candidate_score,
                'checked_candidate_hash': snapshot['checked_candidate_hash'],
                'final_candidate_hash': final_full,
                'final_candidate_execution_hash': final_exec,
                'improvement_criterion': selected if criterion_passed else None,
                'reasons': [] if passed else ['acceptance criterion or immutable hash check failed']}

    def _validate_diagnosis(self, diagnosis, feedback, workflow):
        if not isinstance(diagnosis, dict):
            raise OnlineError('Missing diagnosis')
        # These values are host-bound workflow identities.  Missing and null
        # values are both invalid: a model-authored report cannot opt out of
        # the parent/round binding by omitting a field.
        required_identity = {
            'task_id': feedback['task_id'], 'round_id': feedback['round_id'],
            'executor': self.active_version, 'proposer': self.active_version,
            'parent': self.active_version,
        }
        for key, expected in required_identity.items():
            if key not in diagnosis or diagnosis[key] != expected:
                raise OnlineError(f'Diagnosis {key} identity mismatch')
        optional_identity = {
            'parent_version': self.active_version,
            'full_source_hash': feedback.get('full_source_hash'),
            'execution_hash': feedback.get('execution_hash'),
            'control_manifest_sha256': self._contract_digest(),
            'model_identity_sha256': self._identity_digest(self.model_identity),
            'service_identity_sha256': self._identity_digest(self.service_identity),
        }
        for key, expected in optional_identity.items():
            if key in diagnosis and (diagnosis[key] is None or diagnosis[key] != expected):
                raise OnlineError(f'Diagnosis {key} identity mismatch')
        reports = diagnosis.get('reports')
        if not isinstance(reports, dict) or set(reports) != {'1', '2'}:
            raise OnlineError('Diagnosis must contain both step 1 and step 2 reports')
        refs = self._validate_refs(diagnosis.get('evidence_refs'))
        allowed = set(feedback['evidence_refs'])
        if not set(refs).issubset(allowed):
            raise OnlineError('Diagnosis references private or non-feedback evidence')
        # ``diagnostic_reports`` is model-visible text, so require the strict
        # host-bound role event and report identity contract below.  There is
        # no legacy fallback for anonymous reports or generic role_end events.
        successful_role_ends = {0: [], 1: []}
        events_path = self._round_dir(int(feedback['round_id'][1:]))/'workflow_events.jsonl'
        if events_path.exists() and events_path.is_file() and not events_path.is_symlink():
            for line in events_path.read_text(errors='replace').splitlines():
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if event.get('kind') != 'role_end':
                    continue
                identity = event.get('identity')
                result = event.get('result')
                step_index = event.get('step_index')
                if (event.get('role') != 'archive_explorer'
                        or type(step_index) is not int or step_index not in (0, 1)
                        or not isinstance(identity, str) or not identity.strip()
                        or not isinstance(result, dict)
                        or result.get('exit_status') != 'Submitted'
                        or not isinstance(result.get('submission'), str)
                        or not result['submission'].strip()):
                    continue
                successful_role_ends[step_index].append(identity)
        if any(not successful_role_ends[index] for index in (0, 1)):
            raise OnlineError('Each diagnostic step requires a successful archive_explorer role')
        step_zero_ids = successful_role_ends[0]
        step_one_ids = successful_role_ends[1]
        if (len(set(step_zero_ids)) != len(step_zero_ids)
                or len(set(step_one_ids)) != len(step_one_ids)
                or set(step_zero_ids).intersection(step_one_ids)):
            raise OnlineError('Diagnostic archive role identity was duplicated or reused across steps')
        for key, step_index in (('1', 0), ('2', 1)):
            report = reports[key]
            if not isinstance(report, dict) or not report.get('report'):
                raise OnlineError('Diagnostic report is incomplete')
            report_refs = report.get('evidence_refs')
            if not isinstance(report_refs, list) or not report_refs or not set(report_refs).issubset(allowed):
                raise OnlineError('Diagnostic report references non-public evidence')
            report_identity = report.get('identity')
            if (not isinstance(report_identity, str) or not report_identity.strip()
                    or report_identity not in successful_role_ends[step_index]):
                raise OnlineError('Diagnostic report is not bound to its successful archive_explorer role')
            if ('step_index' in report and report.get('step_index') != step_index):
                raise OnlineError('Diagnostic report step identity mismatch')
            if ('role' in report and report.get('role') != 'archive_explorer'):
                raise OnlineError('Diagnostic report role identity mismatch')
        decision = diagnosis.get('decision')
        if decision not in ('skip','improve'):
            raise OnlineError('Diagnosis has no explicit decision')
        if decision == 'skip':
            if (workflow.get('status') != 'skipped_no_actionable_improvement'
                    or workflow.get('completed_steps') != 2
                    or workflow.get('active') is not None
                    or workflow.get('skipped_steps') != [3,4,5,6,7]):
                raise OnlineError('Skip workflow is not a valid two-step terminal state')
        elif (workflow.get('status') != 'completed' or workflow.get('completed_steps') != 7
              or workflow.get('active') is not None):
            raise OnlineError('Improvement workflow did not complete all seven steps')
        return diagnosis

    def _diagnosis_decision(self, diagnosis, feedback):
        """Return a host-bound early decision, or None for failed diagnosis."""
        if not isinstance(diagnosis, dict) or diagnosis.get('decision') not in ('skip', 'improve'):
            return None
        expected = {
            'task_id': feedback['task_id'], 'round_id': feedback['round_id'],
            'executor': self.active_version, 'proposer': self.active_version,
            'parent': self.active_version,
        }
        if any(diagnosis.get(key) != value for key, value in expected.items()):
            return None
        return diagnosis['decision']

    def _commit_round(self, feedback, decision):
        path = self.root/'decisions'/f"{feedback['round_id']}.json"
        if path.exists():
            old = read(path)
            # A durable decision is immutable. Recovery uses its exact
            # contents and never silently rewrites it.
            return self._apply_existing_decision(feedback, old)
        if decision.get('parent_version') not in (None, self.active_version):
            raise OnlineError('Decision parent is not the active version')
        write(path, decision)
        return self._apply_existing_decision(feedback, decision)

    def _apply_existing_decision(self, feedback, decision):
        accepted = decision.get('decision') == 'accepted'
        parent = decision.get('parent_version', feedback.get('version'))
        if parent != feedback.get('version'):
            raise OnlineError('Durable decision parent does not match task feedback')
        try:
            parent_number = int(parent[1:]) if isinstance(parent, str) and parent.startswith('H') else -1
        except (TypeError, ValueError):
            parent_number = -1
        if parent_number < 0 or parent != f'H{parent_number:04d}':
            raise OnlineError('Durable decision has an invalid parent version')
        idx = feedback['task_index']
        current = self.state
        if accepted:
            proposal_id = decision.get('proposal')
            if not isinstance(proposal_id, str) or not proposal_id.startswith('P'):
                raise OnlineError('Accepted decision has no proposal identity')
            source = self._proposal_dir(int(proposal_id[1:]))/'agent_code'
            if not source.exists():
                raise OnlineError('Accepted decision has no candidate source')
            source_full, source_exec = tree_hash(source), execution_hash(source)
            candidate_identity = self._proposal_dir(int(proposal_id[1:]))/'candidate_identity.json'
            if candidate_identity.exists():
                ident = read(candidate_identity)
                if ident.get('parent_version') != parent:
                    raise OnlineError('Candidate parent identity differs during recovery')
                if ident.get('candidate_hash') not in (None, source_full):
                    raise OnlineError('Candidate full hash differs during recovery')
                if ident.get('execution_hash') not in (None, source_exec):
                    raise OnlineError('Candidate execution hash differs during recovery')
            check_path = self._round_dir(int(feedback['round_id'][1:]))/'candidate_check.json'
            if check_path.exists():
                check = read(check_path)
                if check.get('parent_version') not in (None, parent) or check.get('proposal') not in (None, proposal_id):
                    raise OnlineError('Candidate check parent/proposal differs during recovery')
                if check.get('candidate_execution_hash') not in (None, source_exec):
                    raise OnlineError('Candidate check execution hash differs during recovery')
            # Version numbering is a property of the immutable decision's
            # parent, never of whichever active label happens to be present
            # while recovery is replayed.
            next_version = f"H{parent_number + 1:04d}"
            dest = self.root/'versions'/next_version/'agent_code'
            def verify_dest():
                if not dest.exists() or not dest.is_dir():
                    raise OnlineError('Accepted decision has no durable child source')
                identity_path = dest.parent/'version_identity.json'
                if not identity_path.exists() or not identity_path.is_file() or identity_path.is_symlink():
                    raise OnlineError('Accepted version identity is missing during recovery')
                identity = read(identity_path)
                expected_identity = {
                    'version': next_version,
                    'parent_version': parent,
                    'full_source_hash': source_full,
                    'execution_hash': source_exec,
                    'control_manifest': self.control_manifest,
                    'control_manifest_sha256': self._contract_digest(),
                    'model_identity_sha256': self._identity_digest(self.model_identity),
                    'service_identity_sha256': self._identity_digest(self.service_identity),
                }
                if identity != expected_identity:
                    raise OnlineError('Accepted version identity differs during recovery')
                if tree_hash(dest) != source_full or execution_hash(dest) != source_exec:
                    raise OnlineError('Accepted version source differs during recovery')

            if current['active_version'] == next_version:
                # This covers a crash after the immutable child was installed
                # but before the cursor was advanced.  Verify the child before
                # treating replay as idempotent.
                verify_dest()
            elif current['active_version'] == parent:
                if dest.exists():
                    verify_dest()
                else:
                    if dest.parent.exists() and any(dest.parent.iterdir()):
                        raise OnlineError('Accepted version directory has an unexpected partial state')
                    temp = dest.parent/(dest.name + '.tmp')
                    if temp.exists():
                        shutil.rmtree(temp)
                    shutil.copytree(source, temp)
                    validate_code(temp)
                    identity = {'version': next_version, 'parent_version': parent,
                                'full_source_hash': tree_hash(temp), 'execution_hash': execution_hash(temp),
                                'control_manifest': self.control_manifest,
                                'control_manifest_sha256': self._contract_digest(),
                                'model_identity_sha256': self._identity_digest(self.model_identity),
                                'service_identity_sha256': self._identity_digest(self.service_identity)}
                    write(temp.parent/'version_identity.json', identity)
                    _atomic_move(temp, dest)
                    verify_dest()
            else:
                raise OnlineError('Active version is neither the decision parent nor its child')
            active = next_version
        else:
            active = parent
            if current['active_version'] != parent:
                raise OnlineError('Rejected decision parent is not the active version')
        # Replaying an already applied immutable decision is a no-op.  The
        # durable child/source checks above still run before this return.
        if current['next_task_index'] > idx:
            return active
        if current['next_task_index'] < idx:
            raise OnlineError('Durable decision is ahead of the task cursor')
        self._save_state(active_version=active, next_task_index=idx + 1,
                         next_round=int(feedback['round_id'][1:]) + 1,
                         next_proposal=current['next_proposal'] + (1 if decision.get('proposal') else 0),
                         status='round_committed' if accepted else ('skipped' if decision.get('decision','').startswith('skipped') else 'round_committed'),
                         current_task=None, current_round=None, current_proposal=None)
        self._event('round_committed', round_id=feedback['round_id'], decision=decision.get('decision'), version=active)
        return active

    def _recover(self):
        state = self.state
        if state['status'] in ('completed','idle','round_committed','skipped'):
            return
        current_round = state.get('current_round')
        if current_round:
            decision_path = self.root/'decisions'/f'{current_round}.json'
            if decision_path.exists():
                idx = self.tasks.index(state['current_task'])
                feedback_path = self._task_dir(idx, state['current_task'])/'feedback.json'
                if feedback_path.exists():
                    feedback = read(feedback_path)
                    decision = read(decision_path)
                    # An accepted child may already be installed when the
                    # process stopped between the version move and cursor
                    # commit.  Validate feedback against its recorded parent
                    # before replaying the immutable decision.
                    self._validate_feedback(feedback, idx, state['current_task'],
                                            version=feedback.get('version'))
                    self._apply_existing_decision(feedback, decision)
                    return
            if self.resume_checkpoint is not None:
                from .recovery import load_workflow_authorization
                proposal_id = f"P{state['next_proposal']:04d}"
                round_dir = self._round_dir(int(current_round[1:]))
                load_workflow_authorization(
                    self.resume_checkpoint, self.root, round_dir, round_dir/'candidate_work',
                    {'task_id':state['current_task'], 'round_id':current_round,
                     'version':self.active_version, 'parent':int(self.active_version[1:]),
                     'proposer':int(self.active_version[1:]), 'candidate':proposal_id})
                self._save_state(status='feedback_ready')
                return
            if state['status'] == 'interrupted':
                workflow_path = self._round_dir(int(current_round[1:]))/'workflow_state.json'
                if workflow_path.exists() or workflow_path.is_symlink():
                    raise OnlineInterrupted(
                        'Interrupted online workflow has existing model state; refusing to reopen it')
        if state['status'] in ('task_running','task_done'):
            task = state.get('current_task')
            if task is None:
                self._save_state(status='interrupted')
                raise OnlineInterrupted('Task was interrupted before durable feedback')
            index = self.tasks.index(task)
            task_dir = self._task_dir(index, task)
            feedback_path = task_dir/'feedback.json'
            if feedback_path.exists():
                feedback = read(feedback_path)
                self._validate_feedback(feedback, index, task)
                self._save_state(status='feedback_ready', current_task=task,
                                 current_round=feedback['round_id'])
                return
            # Driver.task_retry is deliberately resumable.  A durable attempt
            # record means the benchmark process ran and only host scoring or
            # public feedback promotion remains; calling task_retry here does
            # not start a second model solution.  Without that record the
            # outcome is unknown and the online chain must pause.
            execution_evidence = [p for p in attempt_result_paths(task_dir, 'task_execution.json')
                                  if p.exists() and p.is_file() and not p.is_symlink()]
            if execution_evidence and self.driver is not None:
                expected_full, expected_exec = tree_hash(self.active_code), execution_hash(self.active_code)
                for evidence_path in execution_evidence:
                    try:
                        evidence = read(evidence_path)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        self._save_state(status='interrupted')
                        raise OnlineInterrupted('Task execution evidence is corrupt') from exc
                    if not isinstance(evidence, dict):
                        self._save_state(status='interrupted')
                        raise OnlineInterrupted('Task execution evidence is not an object')
                    if not result_matches_source(evidence, {expected_full, expected_exec}):
                        raise OnlineError('Task execution evidence is bound to a stale Harness version')
                # A model writable task log alone cannot establish a completed
                # host result; task_retry resumes grading from the execution
                # record when the host result is absent.
                for host_result in attempt_result_paths(task_dir):
                    try:
                        saved_host = read_regular_json(host_result)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        self._save_state(status='interrupted')
                        raise OnlineInterrupted('Task host result is corrupt') from exc
                    if saved_host is None:
                        continue
                    rel = host_result.parent.relative_to(self.root)
                    ledger = self.root/'private/task_grading'/rel/'attempts.json'
                    if not ledger.exists() or not ledger.is_file() or ledger.is_symlink():
                        self._save_state(status='interrupted')
                        raise OnlineInterrupted('Task host result has no private grading ledger')
                self.execute_task(index)
                return
            self._save_state(status='interrupted')
            raise OnlineInterrupted('Task was interrupted before durable execution evidence')
        if state['status'] == 'checking' and state.get('current_task') and state.get('current_proposal'):
            idx = self.tasks.index(state['current_task'])
            feedback_path = self._task_dir(idx, state['current_task'])/'feedback.json'
            round_dir = self._round_dir(int(state['current_round'][1:]))
            check_path = round_dir/'candidate_check.json'
            candidate = self._proposal_dir(int(state['current_proposal'][1:]))/'agent_code'
            workflow_path = round_dir/'online_workflow_result.json'
            diagnosis_path = round_dir/'diagnosis.json'
            # Recovery must consume the workflow's declared step-3 plan; the
            # preliminary host plan and score-based compatibility choices are
            # not acceptance evidence.
            plan_path = round_dir/'check_plan_declared.json'
            frozen_plan_path = round_dir/'check_plan_frozen.json'
            job_path = self.root/'candidate_checks'/(f'candidate:{state["current_round"]}:{state["current_proposal"]}'.replace('/','_') + '.json')
            if all(p.exists() for p in (feedback_path, candidate, workflow_path,
                                        diagnosis_path, plan_path, frozen_plan_path, job_path)):
                feedback = read(feedback_path)
                self._validate_feedback(feedback, idx, state['current_task'])
                persisted = read(job_path)
                if persisted.get('status') == 'failed':
                    error = persisted.get('error') or {}
                    if error.get('type') == 'CandidateRejected':
                        workflow = read(workflow_path)
                        summary = {'round_id': state['current_round'],
                                   'proposal': state['current_proposal'],
                                   'candidate_check': None,
                                   'acceptance': {'passed': False,
                                                  'reason': error.get('message', 'candidate check rejected')},
                                   'metrics': workflow.get('metrics')}
                        workflow['execution_summary'] = summary
                        write(workflow_path, workflow)
                        decision = {'round_id': state['current_round'],
                                    'task_id': state['current_task'],
                                    'proposal': state['current_proposal'],
                                    'parent_version': self.active_version,
                                    'decision': 'rejected', 'status': 'incomplete',
                                    'reason': error.get('message', 'candidate check rejected'),
                                    'acceptance': summary['acceptance'],
                                    'completed_steps': workflow.get('completed_steps', 7),
                                    'takeover_summary': summary}
                        self._commit_round(feedback, decision)
                        return
                    self._save_state(status='interrupted')
                    raise OnlineInterrupted('Candidate check failed before durable completion')
                if persisted.get('status') != 'completed' or not isinstance(persisted.get('result'), dict):
                    self._save_state(status='interrupted')
                    raise OnlineInterrupted('Candidate check record is not durably completed')
                if not check_path.exists():
                    self._save_state(status='interrupted')
                    raise OnlineInterrupted('Candidate check completed without a durable snapshot')
                metadata = persisted.get('metadata', {})
                for key, expected in (('job_id', persisted.get('job_id')),
                                      ('task_id', state['current_task']),
                                      ('round_id', state['current_round']),
                                      ('candidate', state['current_proposal']),
                                      ('parent_version', self.active_version)):
                    if metadata.get(key) != expected:
                        raise OnlineError('Persisted candidate job identity differs during recovery')
                snapshot = read(check_path)
                plan = read(plan_path)
                frozen_plan = read(frozen_plan_path)
                if any(frozen_plan.get(key) != plan.get(key)
                       for key in ('criterion','mechanism_plan','revision')):
                    raise OnlineError('Frozen candidate check plan differs from step-3 declaration')
                if (snapshot.get('job_id') != persisted.get('job_id')
                        or snapshot.get('parent_version') != self.active_version
                        or snapshot.get('parent_hash') != tree_hash(self.active_code)
                        or snapshot.get('parent_execution_hash') != execution_hash(self.active_code)
                        or snapshot.get('candidate_execution_hash') != execution_hash(candidate)):
                    raise OnlineError('Persisted candidate check identity differs during recovery')
                acceptance = self._acceptance(candidate, feedback, snapshot, plan)
                workflow = read(workflow_path)
                existing_summary = workflow.get('execution_summary')
                if existing_summary is not None:
                    if (not isinstance(existing_summary, dict)
                            or existing_summary.get('round_id') != state['current_round']
                            or existing_summary.get('proposal') != state['current_proposal']
                            or existing_summary.get('candidate_check') != snapshot
                            or existing_summary.get('acceptance') != acceptance):
                        raise OnlineError('Persisted execution summary differs from host check evidence')
                    summary = existing_summary
                else:
                    summary = {'round_id': state['current_round'],
                               'proposal': state['current_proposal'], 'candidate_check': snapshot,
                               'acceptance': acceptance, 'metrics': workflow.get('metrics')}
                workflow['execution_summary'] = summary
                write(workflow_path, workflow)
                decision = {'round_id': state['current_round'], 'task_id': state['current_task'],
                            'proposal': state['current_proposal'], 'parent_version': self.active_version,
                            'decision': 'accepted' if acceptance['passed'] else 'rejected',
                            'acceptance': acceptance, 'candidate_check': 'candidate_check.json',
                            'completed_steps': workflow.get('completed_steps', 7),
                            'takeover_summary': summary}
                self._commit_round(feedback, decision)
                return
        if state['status'] in ('diagnosing','diagnosis_pending','improving','checking','decision_ready'):
            self._save_state(status='interrupted')
            raise OnlineInterrupted('Online workflow interrupted before an immutable decision')

    def run_round(self, feedback):
        round_id = feedback['round_id']
        number = int(round_id[1:])
        round_dir = self._round_dir(number)
        round_dir.mkdir(parents=True, exist_ok=True)
        write(round_dir/'feedback.json', feedback)
        self._save_state(status='diagnosing', current_task=feedback['task_id'], current_round=round_id)
        proposal_id = f"P{self.state['next_proposal']:04d}"
        try:
            workflow, working, identity, plan = self._improve_with_driver(feedback, round_dir, proposal_id)
        except OnlineInterrupted:
            self._save_state(status='interrupted')
            raise
        except InfrastructureError:
            self._save_state(status='interrupted')
            raise OnlineInterrupted('Candidate/improvement infrastructure failed before a durable decision')
        # The controller's process result is host evidence.  A workflow JSON
        # marked completed/skipped cannot override an abnormal, missing, or
        # boolean process status (``bool`` is an ``int`` subclass).
        process_status = workflow.get('process_status') if isinstance(workflow, dict) else None
        if type(process_status) is not int or process_status != 0:
            self._finish_improvement_session()
            self._save_state(status='interrupted')
            raise OnlineInterrupted('Improvement process ended outside the protocol')
        parent_hash = tree_hash(self.active_code)
        diagnosis_path = round_dir/'diagnosis.json'
        early_diagnosis = read(diagnosis_path) if diagnosis_path.exists() else None

        def commit_diagnosis_failure(reason):
            workflow['metrics'] = self._finish_improvement_session()
            write(round_dir/'online_workflow_result.json', workflow)
            self._discard_unrequested_candidate_job(round_id, proposal_id,
                                                    tolerate_completed=True)
            decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                        'parent_version': self.active_version,
                        'decision': 'diagnosis_failed', 'status': 'diagnosis_failed',
                        'reason': reason, 'diagnosis': early_diagnosis,
                        'proposal': None,
                        'completed_steps': workflow.get('completed_steps', 0),
                        'acceptance': {'passed': False, 'diagnosis_failed': True}}
            return self._commit_round(feedback, decision)

        if workflow.get('status') not in ('completed', 'skipped_no_actionable_improvement'):
            # A model process that returned normally but did not complete its
            # protocol is a rejected proposal.  A killed/externally failed
            # process remains paused so an unknown result is never treated as
            # a normal skip.
            if self._diagnosis_decision(early_diagnosis, feedback) != 'improve':
                return commit_diagnosis_failure(
                    'Diagnostic workflow ended without a valid improve decision')
            workflow['metrics'] = self._finish_improvement_session()
            write(round_dir/'online_workflow_result.json', workflow)
            self._discard_unrequested_candidate_job(round_id, proposal_id,
                                                    tolerate_completed=True)
            decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                        'proposal': proposal_id, 'parent_version': self.active_version,
                        'decision': 'rejected', 'status': 'incomplete',
                        'reason': 'Improvement workflow did not complete',
                        'acceptance': {'passed': False, 'workflow_status': workflow.get('status'),
                                       'completed_steps': workflow.get('completed_steps', 0)}}
            return self._commit_round(feedback, decision)
        diagnosis = early_diagnosis
        if self._diagnosis_decision(diagnosis, feedback) is None:
            return commit_diagnosis_failure('Completed workflow has no valid diagnosis decision')
        try:
            diagnosis = self._validate_diagnosis(diagnosis, feedback, workflow)
        except OnlineError as exc:
            return commit_diagnosis_failure(f'Diagnosis validation failed: {exc}')
        if diagnosis['decision'] == 'skip':
            workflow['metrics'] = self._finish_improvement_session()
            write(round_dir/'online_workflow_result.json', workflow)
            self._discard_unrequested_candidate_job(round_id, proposal_id)
            if tree_hash(working) != parent_hash or execution_hash(working) != execution_hash(self.active_code):
                decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                            'proposal': proposal_id, 'parent_version': self.active_version,
                            'decision': 'rejected', 'status': 'incomplete',
                            'reason': 'Skip workflow modified the candidate worktree',
                            'acceptance': {'passed': False, 'source_changed': True}}
                return self._commit_round(feedback, decision)
            decision = {'round_id': round_id, 'task_id': feedback['task_id'], 'version': self.active_version,
                        'parent_version': self.active_version, 'decision': 'skipped_no_actionable_improvement',
                        'diagnosis': diagnosis, 'completed_steps': 2, 'active': None,
                        'skipped_steps': [3,4,5,6,7], 'takeover_summary': workflow.get('execution_summary', workflow.get('metrics'))}
            return self._commit_round(feedback, decision)
        # The criterion and optional mechanism test must come from the
        # workflow's completed step 3.  There is no score-based or legacy
        # fallback here: a missing/invalid declaration is a rejected proposal
        # that still consumes this P number and advances the task.
        declared_path = round_dir/'check_plan_declared.json'
        declared_plan = read(declared_path) if declared_path.exists() else None
        frozen_path = round_dir/'check_plan_frozen.json'
        frozen_plan = read(frozen_path) if frozen_path.exists() else None
        options = set(plan.get('criterion_options', ()))
        # Every driver invokes the authenticated callback under the same
        # frozen step-3 and step-5 plan contract.
        if (not isinstance(declared_plan, dict)
                or declared_plan.get('criterion') not in options
                or (not isinstance(frozen_plan, dict)
                    or any(frozen_plan.get(key) != declared_plan.get(key)
                           for key in ('criterion','mechanism_plan','revision')))):
            workflow['metrics'] = self._finish_improvement_session()
            write(round_dir/'online_workflow_result.json', workflow)
            self._discard_unrequested_candidate_job(round_id, proposal_id)
            decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                        'proposal': proposal_id, 'parent_version': self.active_version,
                        'decision': 'rejected', 'status': 'incomplete',
                        'reason': 'Step-3 acceptance criterion is missing or invalid',
                        'diagnosis': diagnosis, 'completed_steps': workflow.get('completed_steps', 7),
                        'acceptance': {'passed': False, 'criterion_declared': False}}
            return self._commit_round(feedback, decision)
        plan = {**plan, **declared_plan, 'criterion': declared_plan['criterion']}
        if declared_plan['criterion'] == 'mechanism_parent_failed_candidate_passed':
            if not isinstance(declared_plan.get('mechanism_plan'), dict):
                workflow['metrics'] = self._finish_improvement_session()
                write(round_dir/'online_workflow_result.json', workflow)
                self._discard_unrequested_candidate_job(round_id, proposal_id)
                decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                            'proposal': proposal_id, 'parent_version': self.active_version,
                            'decision': 'rejected', 'status': 'incomplete',
                            'reason': 'Mechanism criterion has no frozen mechanism plan',
                            'diagnosis': diagnosis, 'completed_steps': workflow.get('completed_steps', 7),
                            'acceptance': {'passed': False, 'criterion_declared': True,
                                           'mechanism_plan_declared': False}}
                return self._commit_round(feedback, decision)
        write(round_dir/'check_plan.json', plan)
        # Keep a host-owned copy for audit/reporting while the callback's
        # check_plan_frozen remains the immutable step-5 input.
        if (round_dir/'check_plan_frozen.json').exists():
            write(round_dir/'check_plan_host_record.json', plan)
        validate_code(working)
        self._save_state(status='checking', current_proposal=proposal_id)
        proposal_dir = self._proposal_dir(int(proposal_id[1:]))
        proposal_dir.mkdir(parents=True, exist_ok=True)
        candidate = proposal_dir/'agent_code'
        # If step 5 was requested inside the workflow, ``candidate`` is the
        # frozen source that was actually checked.  Steps 6/7 may update
        # documentation afterwards; carry those final files into the
        # proposal while the immutable checked source remains beside it.
        if candidate.exists():
            checked = round_dir/'candidate_check_source'
            if checked.exists():
                shutil.rmtree(candidate)
                shutil.copytree(working, candidate)
        else:
            shutil.copytree(working, candidate)
        write(proposal_dir/'candidate_identity.json', {'proposal': proposal_id,
            'parent_version': self.active_version, 'parent_hash': tree_hash(self.active_code),
            'candidate_hash': tree_hash(candidate), 'execution_hash': execution_hash(candidate),
            'plan_revision': plan['revision']})
        if tree_hash(candidate) == parent_hash and execution_hash(candidate) == execution_hash(self.active_code):
            decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                        'proposal': proposal_id, 'parent_version': self.active_version,
                        'decision': 'rejected', 'status': 'incomplete',
                        'reason': 'Candidate made no Harness source change',
                        'diagnosis': diagnosis, 'completed_steps': workflow.get('completed_steps', 7),
                        'acceptance': {'passed': False, 'source_changed': False,
                                       'execution_changed': False}}
            return self._commit_round(feedback, decision)
        try:
            snapshot = self._check_candidate(candidate, feedback, round_dir, proposal_id, identity)
        except CandidateRejected as exc:
            # A completed workflow with a known-invalid candidate/check is a
            # durable rejection, while an unknown host interruption remains a
            # resumable boundary handled by the recovery path.
            workflow['metrics'] = self._finish_improvement_session()
            summary = {'round_id': round_id, 'proposal': proposal_id,
                       'process_status': workflow.get('process_status'),
                       'metrics': workflow.get('metrics'), 'candidate_check': None,
                       'acceptance': {'passed': False, 'reason': str(exc)}}
            workflow['execution_summary'] = summary
            write(round_dir/'online_workflow_result.json', workflow)
            decision = {'round_id': round_id, 'task_id': feedback['task_id'],
                        'proposal': proposal_id, 'parent_version': self.active_version,
                        'decision': 'rejected', 'status': 'incomplete',
                        'reason': str(exc), 'diagnosis': diagnosis,
                        'completed_steps': workflow.get('completed_steps', 7),
                        'takeover_summary': summary,
                        'acceptance': summary['acceptance']}
            return self._commit_round(feedback, decision)
        workflow['metrics'] = self._finish_improvement_session()
        write(round_dir/'online_workflow_result.json', workflow)
        acceptance = self._acceptance(candidate, feedback, snapshot, plan)
        summary = {'round_id': round_id, 'proposal': proposal_id, 'process_status': workflow.get('process_status'),
                   'metrics': workflow.get('metrics'), 'candidate_check': snapshot, 'acceptance': acceptance}
        workflow['execution_summary'] = summary
        write(round_dir/'online_workflow_result.json', workflow)
        decision = {'round_id': round_id, 'task_id': feedback['task_id'], 'proposal': proposal_id,
                    'parent_version': self.active_version, 'decision': 'accepted' if acceptance['passed'] else 'rejected',
                    'acceptance': acceptance, 'candidate_check': 'candidate_check.json',
                    'diagnosis': diagnosis, 'completed_steps': 7, 'takeover_summary': summary}
        return self._commit_round(feedback, decision)

    def run_once(self):
        state = self.state
        if state['next_task_index'] >= len(self.tasks):
            self._save_state(status='completed')
            return None
        if state['status'] == 'interrupted':
            raise OnlineInterrupted('Online run is interrupted; resume only from a durable boundary')
        try:
            feedback = self.execute_task()
            return self.run_round(feedback)
        except ModelError:
            pause = getattr(self.broker, 'pause_event', None)
            if pause is not None and pause.is_set():
                self._save_state(status='interrupted')
                raise OnlineInterrupted('Proxy transport fault paused the online workflow') from None
            raise
        finally:
            self._finish_improvement_session()

    def run(self):
        while self.state['next_task_index'] < len(self.tasks):
            self.run_once()
        self._save_state(status='completed')
        return self.state
