"""Durable candidate-check registry; broker authentication stays at the RPC boundary."""
import re
import threading

from .core import append_event, read_regular_json, write
from .model_protocol import ModelError


# Candidate job identity is host-owned.  The session digest is deliberately
# kept out of the request payload: it binds the relay to the session that was
# authorized by the host, but is not a model-visible identity field.
CANDIDATE_JOB_METADATA_FIELDS = (
    'job_id', 'task_id', 'round_id', 'candidate', 'parent_version',
    'check_revision', 'stage', 'agent', 'role', 'session_token_sha256',
)
# Keeping this as an explicit tuple makes accidental exposure of the private
# digest in a future payload change easy to review.
CANDIDATE_PUBLIC_IDENTITY_FIELDS = (
    'job_id', 'task_id', 'round_id', 'candidate', 'parent_version',
    'check_revision', 'stage',
)
CANDIDATE_JOB_STATES = ('registered', 'running', 'completed', 'failed', 'interrupted')


def validate_candidate_job_record(job_id, metadata, state):
    """Validate a persisted candidate job before reuse or recovery."""
    if not isinstance(job_id, str) or not job_id or len(job_id) > 160:
        raise ValueError('Invalid candidate job id')
    if (not isinstance(metadata, dict)
            or any(not isinstance(metadata.get(key), str) or not metadata[key]
                   for key in CANDIDATE_JOB_METADATA_FIELDS)):
        raise ValueError('Candidate job metadata is incomplete')
    if metadata.get('job_id') != job_id:
        raise ValueError('Candidate job metadata job_id differs')
    session_hash = metadata.get('session_token_sha256')
    if not isinstance(session_hash, str) or re.fullmatch(r'[0-9a-f]{64}', session_hash) is None:
        raise ValueError('Candidate job session token hash is invalid')
    if not isinstance(state, dict):
        raise ValueError('Candidate job record is not an object')
    if state.get('job_id') != job_id:
        raise ValueError('Persisted candidate job id differs')
    if state.get('metadata') != metadata:
        raise ValueError('Persisted candidate job metadata differs')
    status = state.get('status')
    if status not in CANDIDATE_JOB_STATES:
        raise ValueError('Unknown candidate job status')
    result = state.get('result')
    error = state.get('error')
    if status == 'completed':
        valid = isinstance(result, dict) and error is None
    elif status in ('failed', 'interrupted'):
        valid = (result is None and isinstance(error, dict)
                 and isinstance(error.get('type'), str) and bool(error['type'])
                 and isinstance(error.get('message'), str))
    else:
        valid = result is None and error is None
    if not valid:
        raise ValueError('Candidate job fields differ from its status')
    return True


class CandidateJobs:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.jobs = {}

    def register(self, job_id, callback, metadata):
        """Register one authenticated, idempotent candidate check.

        ``callback`` runs in the host process and owns all path resolution.
        Re-registering an id is allowed only for the exact same metadata, so a
        restarted relay cannot silently point a job at another candidate.
        """
        with self.lock:
            prior = self.jobs.get(job_id)
            if prior is not None:
                if prior['metadata'] != metadata:
                    raise ValueError('Candidate job identity changed')
                return
            record = self.root / 'candidate_checks' / (job_id.replace('/', '_') + '.json')
            record_present = record.exists() or record.is_symlink()
            prior_state = read_regular_json(record) if record_present else None
            if record_present:
                if prior_state is None:
                    raise ValueError('Persisted candidate job record is not a regular object')
                validate_candidate_job_record(job_id, metadata, prior_state)
            prior_status = prior_state.get('status') if prior_state is not None else None
            prior_error = prior_state.get('error') if prior_state is not None else None
            if prior_status == 'running':
                prior_status = 'interrupted'
                prior_error = {'type':'OnlineInterrupted',
                               'message':'Candidate check was interrupted before completion; use a new revision'}
            prior_result = prior_state.get('result') if prior_state is not None else None
            self.jobs[job_id] = {'callback': callback, 'metadata': metadata,
                                           'result': prior_result,
                                           'error': prior_error, 'running': False,
                                           'record': record}
            status = prior_status or 'registered'
            persisted = {'job_id': job_id, 'metadata': metadata, 'status': status}
            if prior_result is not None:
                persisted['result'] = prior_result
            if prior_error is not None:
                persisted['error'] = prior_error
            write(record, persisted)

    def execute(self, job_id, job, payload):
        with self.lock:
            if job['result'] is not None:
                return job['result']
            if job.get('error') is not None:
                raise ModelError('Candidate check previously failed; use a new check revision')
            if job['running']:
                return {'job_id': job_id, 'status': 'running',
                        'check_revision': job['metadata'].get('check_revision')}
            job['running'] = True
            running = {'job_id':job_id, 'metadata':job['metadata'], 'status':'running'}
            write(job['record'], running)
        try:
            callback = job['callback']
            # The authenticated payload is the single production callback
            # contract.  It contains the frozen identity and check plan that
            # the host registered for this job.
            result = callback(payload)
            if not isinstance(result, dict):
                raise ModelError('Candidate check returned an invalid result')
            with self.lock:
                job['result'] = result
                completed = {'job_id':job_id, 'metadata':job['metadata'],
                             'status':'completed', 'result':result}
                write(job['record'], completed)
            event = {'kind':'candidate_check_complete', 'job_id':job_id,
                     'task_id':job['metadata'].get('task_id'),
                     'check_revision':job['metadata'].get('check_revision')}
            append_event(self.root, event)
            return result
        except Exception as exc:
            with self.lock:
                job['error'] = {'type':type(exc).__name__, 'message':str(exc)}
                failed = {'job_id':job_id, 'metadata':job['metadata'],
                          'status':'failed', 'error':job['error']}
                write(job['record'], failed)
            raise
        finally:
            with self.lock:
                job['running'] = False
