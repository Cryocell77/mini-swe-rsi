"""Explicit host authorization for pre-check online workflow restoration."""
from pathlib import Path
import hashlib
import json

from .core import digest, read, tree_hash


def load_workflow_authorization(path, root, logs, candidate, identities):
    path, root, logs = Path(path), Path(root).resolve(), Path(logs)
    if path.is_symlink() or not path.is_file() or root/'private' not in path.resolve().parents:
        raise ValueError('Workflow resume requires a host-private authorization file')
    data = read(path)
    if data.get('schema') != 'online-workflow-resume-v1':
        raise ValueError('Unknown workflow resume authorization schema')
    if data.get('identities') != identities:
        raise ValueError('Workflow resume identities differ')
    if (root/'configuration.json').exists():
        control = read(root/'configuration.json')['control_manifest']
        control_hash = hashlib.sha256(json.dumps(control, sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest()
        if data.get('control_manifest_sha256') != control_hash:
            raise ValueError('Workflow resume control identity differs')
    state_path = logs/'workflow_state.json'
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError('Workflow resume state is missing')
    state = read(state_path)
    if digest(state_path) != data.get('state_sha256') or state != data.get('state'):
        raise ValueError('Workflow resume state changed after authorization')
    active = state.get('active') or {}
    index = active.get('index')
    if (type(index) is not int or not 0 <= index <= 4
            or state.get('completed_steps') != index
            or state.get('status') not in ('active', 'diagnosis_pending', 'improving')
            or state.get('candidate_check') is not None):
        raise ValueError('Only an active unfinished workflow through step 5 can be explicitly restored')
    if index == 4:
        if data.get('resume_incomplete_check') is not True or not isinstance(state.get('check_plan'), dict):
            raise ValueError('Step-5 restore requires explicit incomplete-check authorization')
        frozen = logs/'candidate_check_source'
        if not frozen.is_dir() or tree_hash(frozen) != data.get('candidate_source_sha256'):
            raise ValueError('Step-5 frozen candidate changed before restoration')
    if tree_hash(candidate) != data.get('candidate_source_sha256'):
        raise ValueError('Workflow resume candidate changed after authorization')
    return data


def restore_session_accounting(broker, token, accounting):
    """Carry measured and explicitly unknown usage forward, never allowances."""
    integer_keys = ('calls', 'tokens', 'prompt_tokens', 'completion_tokens',
                    'cached_tokens', 'tool_calls', 'unmetered_calls')
    if (not isinstance(accounting, dict)
            or any(type(accounting.get(key)) is not int or accounting[key] < 0 for key in integer_keys)):
        raise ValueError('Invalid restored session accounting')
    iterations = accounting.get('iterations')
    if (not isinstance(iterations, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0
                                              for k, v in iterations.items())):
        raise ValueError('Invalid restored role iteration accounting')
    session = broker.sessions[token]
    for key in integer_keys:
        session[key] = accounting[key]
    session['iterations'] = dict(iterations)
    session['errors'] = list(accounting.get('errors', []))
    # Deadline, concurrency, price policy and role allowances remain freshly
    # host-bound. A checkpoint cannot grant an extra budget.
    if accounting.get('started') is not None:
        session['started'] = accounting['started']
