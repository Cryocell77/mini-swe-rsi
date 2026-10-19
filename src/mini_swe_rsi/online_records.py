"""Persist online state and publish versions using the same rules during replay."""
import os
import shutil
from pathlib import Path

from .core import read, write, tree_hash, execution_hash
from .driver import initial_code, validate_code
from .online_checks import decision_parent
from .online_contract import OnlineError, ONLINE_PROTOCOL, validate_online_contract


class OnlineRecords:
    def __init__(self, root, tasks):
        self.root, self.tasks = Path(root), tasks

    def configuration(self, expected):
        path = self.root/'configuration.json'
        if not path.exists():
            write(path, expected)
            return expected
        saved = read(path)
        validate_online_contract(saved, error_type=OnlineError)
        for key in ('protocol', 'tasks', 'control_manifest', 'check_policy', 'improvement_task',
                    'data', 'runtime_image', 'transport_policy', 'task_limit'):
            if saved.get(key) != expected.get(key):
                raise OnlineError(f'Frozen online contract changed: {key}')
        for key in ('model', 'service'):
            if expected.get(key) is not None and saved.get(key) != expected[key]:
                raise OnlineError(f'Frozen online identity changed: {key}')
        return saved

    def initialize(self, active_code, runtime_identity, had_config):
        code = self.root/'versions/H0000/agent_code'
        if not code.exists():
            if active_code is not None and Path(active_code).exists():
                code.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(Path(active_code), code)
            else:
                initial_code(code)
        validate_code(code)
        identity = {'version': 'H0000', 'full_source_hash': tree_hash(code),
                    'execution_hash': execution_hash(code), **runtime_identity}
        path = code.parent/'version_identity.json'
        if had_config and not path.exists():
            raise OnlineError('Online contract exists without H0000 identity')
        if path.exists() and read(path) != identity:
            raise OnlineError('H0000 source or control identity changed')
        write(path, identity)
        path = self.root/'online_state.json'
        if not path.exists():
            write(path, {'protocol': ONLINE_PROTOCOL, 'active_version': 'H0000',
                'next_task_index': 0, 'next_round': 1, 'next_proposal': 1,
                'status': 'idle', 'current_task': None, 'current_round': None,
                'current_proposal': None})
        self.validate_state(read(path))

    def validate_state(self, state):
        validate_online_contract(state, require_policy=False, error_type=OnlineError)
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
        state = read(self.root/'online_state.json')
        self.validate_state(state)
        return state

    def save_state(self, changes):
        state = self.state
        state.update(changes)
        self.validate_state(state)
        write(self.root/'online_state.json', state)

    def code(self, version, runtime_identity):
        path = self.root/'versions'/version/'agent_code'
        identity = read(path.parent/'version_identity.json')
        expected = {'version': version, **runtime_identity}
        if (not isinstance(identity, dict)
                or any(key not in identity or identity[key] != value for key, value in expected.items())):
            raise OnlineError('Active version control identity differs from current runtime')
        if tree_hash(path) != identity.get('full_source_hash') or execution_hash(path) != identity.get('execution_hash'):
            raise OnlineError('Active version source changed after acceptance')
        return path

    def commit_decision(self, feedback, decision, active_version):
        path = self.root/'decisions'/f"{feedback['round_id']}.json"
        if path.exists():
            return read(path)
        parent, _ = decision_parent(feedback, decision)
        if parent != active_version:
            raise OnlineError('Decision parent is not the active version')
        write(path, decision)
        return decision

    def install_version(self, version, parent, source, runtime_identity, *, accepted_hashes, allow_install):
        dest = self.root/'versions'/version/'agent_code'
        full, execution = accepted_hashes
        expected = {'version': version, 'parent_version': parent,
                    'full_source_hash': full, 'execution_hash': execution, **runtime_identity}
        def verify():
            if not dest.is_dir():
                raise OnlineError('Accepted decision has no durable child source')
            path = dest.parent/'version_identity.json'
            if not path.is_file() or path.is_symlink():
                raise OnlineError('Accepted version identity is missing during recovery')
            if read(path) != expected:
                raise OnlineError('Accepted version identity differs during recovery')
            if tree_hash(dest) != full or execution_hash(dest) != execution:
                raise OnlineError('Accepted version source differs during recovery')
        if not dest.exists() and allow_install:
            if dest.parent.exists() and any(dest.parent.iterdir()):
                raise OnlineError('Accepted version directory has an unexpected partial state')
            temp = dest.parent/(dest.name + '.tmp')
            if temp.exists():
                shutil.rmtree(temp)
            shutil.copytree(source, temp)
            validate_code(temp)
            # Bind the copied bytes, then check them against the accepted source.
            identity = {**expected, 'full_source_hash': tree_hash(temp), 'execution_hash': execution_hash(temp)}
            write(temp.parent/'version_identity.json', identity)
            if dest.exists():
                raise OnlineError(f'Refusing to overwrite immutable online version: {dest}')
            os.replace(str(temp), str(dest))
        verify()
