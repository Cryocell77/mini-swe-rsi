"""Online identities, snapshot validation and acceptance rules without loop state."""
import hashlib
import json
import math

from .core import tree_hash, execution_hash
from .online_contract import OnlineError


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def candidate_job_identity(task_id, round_id, parent_version, candidate):
    return {'job_id': f'candidate:{round_id}:{candidate}',
            'task_id': task_id, 'round_id': round_id, 'candidate': candidate,
            'parent_version': parent_version, 'stage': 'candidate_check',
            'check_revision': '1', 'agent': 'main', 'role': 'orchestrator'}


def identity_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def candidate_identity(proposal_id, parent_version, parent_hash, candidate, plan_revision):
    return {'proposal': proposal_id, 'parent_version': parent_version,
            'parent_hash': parent_hash, 'candidate_hash': tree_hash(candidate),
            'execution_hash': execution_hash(candidate), 'plan_revision': plan_revision}


def validate_candidate_snapshot(snapshot, feedback, proposal_id, candidate, *, accepted=False):
    candidate_exec = execution_hash(candidate)
    expected = {
        'job_id': f'candidate:{feedback["round_id"]}:{proposal_id}',
        'task_id': feedback['task_id'], 'check_revision': '1',
        'round_id': feedback['round_id'], 'proposal': proposal_id,
        'parent_version': feedback['version'], 'parent_hash': feedback['full_source_hash'],
        'parent_execution_hash': feedback['execution_hash'],
        'candidate_hash': tree_hash(candidate),
        'candidate_execution_hash': candidate_exec,
    }
    if (not isinstance(snapshot, dict)
            or any(snapshot.get(key) != value for key, value in expected.items())
            or not isinstance(snapshot.get('checked_candidate_hash'), str)
            or not snapshot['checked_candidate_hash']
            or not isinstance(snapshot.get('checked_candidate_execution_hash'), str)
            or not snapshot['checked_candidate_execution_hash']
            or (accepted and snapshot['checked_candidate_execution_hash'] != candidate_exec)):
        raise OnlineError('Candidate check identity differs during recovery')


def evaluate_candidate(candidate, feedback, snapshot, plan=None, *, require_task_pass=False):
    check = snapshot.get('check', {})
    if isinstance(check, dict) and 'common_tasks' in check:
        raise OnlineError('Candidate check common_tasks is unsupported')
    if isinstance(plan, dict) and 'common_tasks' in plan:
        raise OnlineError('Candidate check plan common_tasks is unsupported')
    record = check.get('record', {}) if isinstance(check, dict) else {}
    parent_score = feedback.get('record', {}).get('score')
    candidate_score = record.get('score', check.get('score'))
    final_full, final_exec = tree_hash(candidate), execution_hash(candidate)
    source_changed = final_full != snapshot['parent_hash']
    exec_changed = final_exec != snapshot['parent_execution_hash']
    checked_match = final_exec == snapshot['checked_candidate_execution_hash']
    score_known = finite_number(candidate_score) and finite_number(parent_score)
    current_fixed = score_known and parent_score < 1 and candidate_score >= 1
    mech = check.get('mechanism_check') if isinstance(check, dict) else None
    mechanism = isinstance(mech, dict) and mech.get('parent_failed') is True and mech.get('candidate_passed') is True
    selected = (plan or {}).get('criterion')
    criterion_passed = current_fixed if selected == 'current_task_failure_fixed' else mechanism if selected == 'mechanism_parent_failed_candidate_passed' else False
    non_regression = score_known and candidate_score >= parent_score
    passed = bool(source_changed and exec_changed and checked_match and non_regression and criterion_passed)
    if require_task_pass:
        passed = passed and score_known and candidate_score >= 1
    return {'passed': passed, 'source_changed': source_changed, 'execution_changed': exec_changed,
            'checked_execution_matches': checked_match, 'score_known': score_known,
            'non_regression': non_regression,
            'parent_score': parent_score, 'candidate_score': candidate_score,
            'checked_candidate_hash': snapshot['checked_candidate_hash'],
            'final_candidate_hash': final_full,
            'final_candidate_execution_hash': final_exec,
            'improvement_criterion': selected if criterion_passed else None,
            'reasons': [] if passed else ['acceptance criterion or immutable hash check failed']}
