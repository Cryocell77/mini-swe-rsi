from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .core import read, write
from .public_filter import public_value as _public_report_value
from .online_contract import validate_online_contract


_LEGACY_MARKERS = (
    'baseline.json', 'preflight.json', 'gates.json', 'champions.json',
    'recursive_track_result.json', 'verification.json', 'test_seal.json',
    'results', 'probes', 'private/final',
    'budget.json', 'execution_scope.json', 'dev_selection.json',
)


def _ensure_online_root(root):
    """Reject native experiment roots before producing an online report."""
    root = Path(root).resolve()
    config_path = root/'configuration.json'
    state_path = root/'online_state.json'
    config = state = None
    if config_path.exists():
        config = read(config_path)
        validate_online_contract(config, require_policy=False)
    elif not state_path.exists():
        raise ValueError('Online report requires online_state.json or an online configuration')
    if state_path.exists():
        state = read(state_path)
        validate_online_contract(state, require_policy=False)
    for marker in _LEGACY_MARKERS:
        if (root/marker).exists():
            raise ValueError(f'Refusing to report a legacy native run as online: {marker}')
    return root


def build_online_report(root):
    """Produce an online-only report without native champion/gate statistics."""
    root = _ensure_online_root(root)
    state = (_public_report_value(read(root/'online_state.json'))
             if (root/'online_state.json').exists() else None)
    config = (_public_report_value(read(root/'configuration.json'))
              if (root/'configuration.json').exists() else None)
    tasks = []
    for path in sorted((root/'tasks').iterdir()) if (root/'tasks').exists() else []:
        if not path.is_dir():
            continue
        feedback = path/'public_feedback.json'
        if feedback.exists():
            tasks.append(_public_report_value(read(feedback)))
    rounds = []
    for path in sorted((root/'decisions').glob('R*.json')) if (root/'decisions').exists() else []:
        rounds.append(_public_report_value(read(path)))
    decisions = Counter(item.get('decision') for item in rounds)
    events = []
    usage = {'calls': 0, 'tokens': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
             'unmetered_calls': 0, 'model_seconds': 0.0}
    by_activity = {}
    log_complete = True
    if (root/'events.jsonl').exists():
        public_keys = {'kind', 'task_id', 'round_id', 'version', 'parent', 'proposal',
                       'decision', 'stage', 'status', 'time', 'online'}
        with (root/'events.jsonl').open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    log_complete = False
                    continue
                if event.get('online'):
                    events.append({key:event[key] for key in public_keys if key in event})
                if event.get('kind') != 'model_call':
                    continue
                label = event.get('label', '')
                activity = ('candidate_task' if label.startswith('task:proposals/') else
                            'task' if label.startswith('task:tasks/') else event.get('role', 'other'))
                totals = by_activity.setdefault(activity, {key: 0 for key in usage})
                for target in (usage, totals):
                    target['calls'] += 1
                    target['model_seconds'] += event.get('seconds', 0)
                    measured = event.get('usage')
                    if isinstance(measured, dict) and all(type(measured.get(k)) is int for k in
                                                         ('total_tokens', 'prompt_tokens', 'completion_tokens')):
                        target['tokens'] += measured['total_tokens']
                        target['prompt_tokens'] += measured['prompt_tokens']
                        target['completion_tokens'] += measured['completion_tokens']
                    else:
                        target['unmetered_calls'] += 1
    result = {'mode':'online', 'run':str(root), 'configuration':config,
              'state':state, 'tasks':tasks, 'rounds':rounds,
              'counts':{'tasks':len(tasks), 'rounds':len(rounds),
                        'accepted':decisions.get('accepted',0),
                        'rejected':decisions.get('rejected',0),
                        'skipped':decisions.get('skipped_no_actionable_improvement',0),
                        'diagnosis_failed':decisions.get('diagnosis_failed',0)},
              'online_events':events}
    result.update(total_model_calls=usage['calls'], total_tokens=usage['tokens'],
                  usage=usage, usage_by_activity=by_activity,
                  token_accounting_complete=log_complete and usage['unmetered_calls'] == 0,
                  event_log_complete=log_complete,
                  interpretation='First task attempts and post-feedback checks are separate. Online acceptance does not establish general RSI benefit.')
    if (root/'failure.json').exists():
        result['failure'] = _public_report_value(read(root/'failure.json'))
    write(root/'online_report.json', result)
    lines = ['# mini-swe-rsi online report', '', f'Run: {root}',
             f'Active version: {state.get("active_version") if state else "unknown"}.',
             f'Tasks completed: {len(tasks)}; rounds committed: {len(rounds)}.',
             f'Accepted: {decisions.get("accepted",0)}; rejected: {decisions.get("rejected",0)}; '
             f'skipped: {decisions.get("skipped_no_actionable_improvement",0)}.',
             f'Model calls: {usage["calls"]}; metered tokens: {usage["tokens"]}; unmetered calls: {usage["unmetered_calls"]}.',
             'No general RSI benefit is inferred from these online checks.', '']
    for item in rounds:
        lines.append(f"{item.get('round_id')}: {item.get('decision')}"
                     + (f" ({item.get('parent_version')} -> {item.get('acceptance',{}).get('candidate_score')})"
                        if item.get('decision') == 'accepted' else ''))
    (root/'online_report.md').write_text('\n'.join(lines) + '\n')
    return result
