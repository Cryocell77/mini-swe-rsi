"""Public, frozen view of online run evidence."""
from pathlib import Path
import json
import hashlib

from .core import digest, read, write
from .public_filter import public_value

def online_archive_view(source, destination):
    """Create a read-only public view of completed online evidence.

    Only accepted version source and files explicitly written as public
    feedback/diagnosis/check snapshots are copied.  ``private`` and unfinished
    working candidates are never traversed.
    """
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError('Invalid online source root')
    if not (source/'online_state.json').exists():
        raise ValueError('Not an online run')
    state = read(source/'online_state.json')
    occurred_tasks = []
    for task_dir in sorted((source/'tasks').iterdir()) if (source/'tasks').exists() else []:
        feedback = task_dir/'public_feedback.json'
        if feedback.is_file() and not feedback.is_symlink():
            value = read(feedback)
            if isinstance(value, dict) and isinstance(value.get('task_id'), str):
                occurred_tasks.append(value['task_id'])
    files = [source/'configuration.json', source/'online_state.json']
    for name in ('online_report.json', 'online_report.md'):
        if (source/name).exists():
            files.append(source/name)
    for path in (source/'tasks').glob('*') if (source/'tasks').exists() else []:
        for name in ('public_feedback.json', 'trace.txt',
                     'patch.diff', 'answer.txt', 'activation.json', 'execution_summary.json'):
            item = path/name
            if item.is_file():
                files.append(item)
    for path in (source/'rounds').glob('R*') if (source/'rounds').exists() else []:
        for name in ('diagnosis.json', 'public_feedback.json', 'candidate_check.json',
                     'online_workflow_result.json'):
            item = path/name
            if item.is_file():
                files.append(item)
    for path in (source/'decisions').glob('R*.json') if (source/'decisions').exists() else []:
        files.append(path)
    active_number = int(state.get('active_version', 'H0000')[1:])
    for path in (source/'versions').glob('H*/agent_code') if (source/'versions').exists() else []:
        if path.is_symlink():
            raise ValueError('Invalid online version source')
        try:
            if int(path.parent.name[1:]) > active_number:
                continue
        except (ValueError, IndexError):
            continue
        for item in path.rglob('*'):
            if item.is_symlink():
                raise ValueError('Invalid online version source')
            if item.is_file() and '__pycache__' not in item.parts:
                if item.name == '.env' or item.name.startswith('.env.'):
                    raise ValueError('Credentials cannot be published as online source')
                files.append(item)
    for item in files:
        if item.exists() and (item.is_symlink() or not item.resolve().is_relative_to(source.resolve())
                              or any(p.is_symlink() for p in item.parents if p != source.parent)):
            raise ValueError('Invalid online public file')
    files = [p for p in files if p.is_file()]
    manifest = {str(p.relative_to(source)):digest(p) for p in files}
    marker = destination.parent/(destination.name + '.manifest.json')

    def public_decision(value):
        if not isinstance(value, dict):
            return value
        allowed = ('round_id', 'task_id', 'proposal', 'version', 'parent_version',
                   'decision', 'status', 'reason', 'completed_steps', 'active',
                   'skipped_steps', 'diagnosis', 'candidate_check', 'acceptance')
        return public_value({key:value[key] for key in allowed if key in value})

    def public_workflow(value):
        if not isinstance(value, dict):
            return value
        allowed = ('status', 'completed_steps', 'skipped_steps', 'process_status')
        result = {key:value[key] for key in allowed if key in value}
        summary = value.get('execution_summary')
        if isinstance(summary, dict):
            result['execution_summary'] = public_value({key:summary[key] for key in (
                'round_id', 'proposal', 'process_status', 'candidate_check',
                'acceptance') if key in summary})
        return result

    def public_bytes(item):
        if item.name == 'candidate_check.json':
            payload = read(item)
            check = payload.get('check') if isinstance(payload, dict) else None
            if isinstance(check, dict):
                payload['check'] = {key:check[key] for key in
                    ('score', 'passed', 'execution_status', 'candidate_hash',
                     'execution_hash', 'parent_hash') if key in check}
            return (json.dumps(public_value(payload), ensure_ascii=False, indent=2) + '\n').encode()
        if item.name in ('configuration.json', 'online_state.json'):
            value = public_value(read(item))
            # The online control contract contains the complete future task
            # order.  Historical role context may expose only tasks with a
            # durable public feedback record.
            if item.name == 'configuration.json' and isinstance(value, dict) and 'tasks' in value:
                value['tasks'] = occurred_tasks
            return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
        if item.name == 'public_feedback.json':
            return (json.dumps(public_value(read(item)), ensure_ascii=False, indent=2) + '\n').encode()
        if item.name == 'online_workflow_result.json':
            return (json.dumps(public_workflow(read(item)), ensure_ascii=False, indent=2) + '\n').encode()
        if item.parent.name == 'decisions' and item.name.startswith('R'):
            return (json.dumps(public_decision(read(item)), ensure_ascii=False, indent=2) + '\n').encode()
        return item.read_bytes()

    def expected_view_manifest():
        return {str(item.relative_to(source)): hashlib.sha256(public_bytes(item)).hexdigest()
                for item in files}

    if destination.exists():
        if destination.is_symlink() or any(p.is_symlink() or not (p.is_file() or p.is_dir())
                                           for p in destination.rglob('*')):
            raise RuntimeError('Frozen online archive view contains a symlink or special file')
        actual = {str(p.relative_to(destination)):digest(p) for p in destination.rglob('*') if p.is_file()}
        if read(marker) != manifest or actual != expected_view_manifest():
            raise RuntimeError('Frozen online archive view changed')
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    for item in files:
        target = destination/item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(public_bytes(item))
    write(marker, manifest)
    return destination
