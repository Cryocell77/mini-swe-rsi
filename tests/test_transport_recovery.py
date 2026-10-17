import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from mini_swe_rsi.core import ROOT

sys.path.insert(0, str(ROOT / '.upstream/mini/src'))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = load_module('transport_recovery_workflow', 'runtime/workflow.py')
b = load_module('transport_recovery_bridge', 'runtime/bridge.py')


REF = 'tasks/t1/trace.txt'


def online_flow(tmp_path, delegate=None, source_hash='candidate-source'):
    return w.Workflow(
        tmp_path,
        delegate or (lambda *args: {'exit_status': 'Submitted', 'submission': 'archive'}),
        identities={
            'version': 'H0000', 'parent_version': 'H0000', 'model': 'fixture-model',
            'source_execution_hash': source_hash, 'task_id': 't1', 'round_id': 'R0001',
        },
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
    )


def make_active_resume_state(tmp_path):
    flow = online_flow(tmp_path)
    flow.dispatch({'op': 'invoke'})
    flow.call_role('archive_explorer', 'step one')
    flow.dispatch({'op': flow.current()['completion_tool'], 'success': True,
                   'report': 'failure', 'evidence_refs': [REF]})
    flow.call_role('archive_explorer', 'step two')
    flow.dispatch({'op': flow.current()['completion_tool'], 'success': True,
                   'report': 'mechanism', 'evidence_refs': [REF]})
    flow.dispatch({'op': 'diagnosis_decision', 'decision': 'improve',
                   'reason': 'fixture', 'failure_modes': [],
                   'effective_mechanisms': [], 'improvement_opportunities': ['change'],
                   'evidence_refs': [REF]})
    flow.counter = 18
    flow.save()
    return flow


def test_unbounded_model_timeout_is_explicit_httpx_none_and_finite_is_host_deadline():
    unbounded = b.model_request_timeout({'session_remaining_seconds': None})
    assert isinstance(unbounded, httpx.Timeout)
    assert all(getattr(unbounded, key) is None for key in ('connect', 'read', 'write', 'pool'))
    assert b.model_request_timeout({'session_remaining_seconds': 37.5}) == 37.5


def test_unbounded_timeout_stays_serializable_but_reaches_query_boundary():
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    model = LitellmTextbasedModel(model_name='fixture', model_kwargs={'timeout': None})
    b.apply_model_request_timeout(model, {'session_remaining_seconds': None})
    assert model.serialize()['info']['config']['model']['model_kwargs']['timeout'] is None

    seen = {}

    class FakeModel:
        def _query(self, messages, **kwargs):
            seen.update(kwargs)

    fake = b.apply_model_request_timeout(FakeModel(), {'session_remaining_seconds': None})
    fake._query([])
    assert isinstance(seen['timeout'], httpx.Timeout)


def test_workflow_transport_error_is_fatal_to_parent_agent(tmp_path):
    flow = w.Workflow(
        tmp_path, lambda *args: (_ for _ in ()).throw(w.WorkflowTransportError('HTTP 500')),
        identities={'public_archive': str(tmp_path / 'archive')},
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
    )
    flow.dispatch({'op': 'invoke'})

    with pytest.raises(w.WorkflowTransportError, match='HTTP 500'):
        flow.execute({'command': 'sica-workflow {"op":"delegate","role":"archive_explorer","task":"diagnose"}'},
                     role='orchestrator')
    assert flow.counter == 1


def test_provider_transport_exception_is_wrapped_as_fatal(tmp_path):
    flow = w.Workflow(
        tmp_path, lambda *args: (_ for _ in ()).throw(httpx.ReadTimeout('broker closed')),
        identities={'public_archive': str(tmp_path / 'archive')},
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
    )
    flow.dispatch({'op': 'invoke'})

    with pytest.raises(w.WorkflowTransportError, match='ReadTimeout'):
        flow.call_role('archive_explorer', 'diagnose')


def test_default_state_overwrite_remains_rejected(tmp_path):
    flow = online_flow(tmp_path)
    flow.dispatch({'op': 'invoke'})

    with pytest.raises(ValueError, match='Refusing to overwrite'):
        w.Workflow(tmp_path, lambda *args: None, feedback={'task_id':'t1','round_id':'R0001','evidence_refs':[REF]})


def test_online_resume_restores_live_state_and_continues_counter(tmp_path):
    flow = make_active_resume_state(tmp_path)
    state = json.loads((tmp_path / 'workflow_state.json').read_text())
    identities = {
        'version': 'H0000', 'parent_version': 'H0000', 'model': 'fixture-model',
        'source_execution_hash': 'candidate-source', 'task_id': 't1', 'round_id': 'R0001',
    }
    resumed = w.Workflow(tmp_path, lambda identity, *args: {
        'exit_status': 'Submitted', 'submission': identity},
        identities=identities,
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
        resume_state=state)

    assert resumed.current()['index'] == 2
    assert resumed.completed_steps == 2
    assert resumed.status == 'improving'
    assert resumed.counter == 18
    assert resumed.diagnosis == flow.diagnosis
    assert resumed.check_plan == flow.check_plan
    assert resumed.candidate_check_result == flow.candidate_check_result
    assert resumed.current()['invocation_id'] == flow.current()['invocation_id']
    assert resumed.dispatch({'op': 'state'})['index'] == 2
    with pytest.raises(ValueError, match='Structure already active'):
        resumed.dispatch({'op': 'invoke'})
    with pytest.raises(ValueError, match='only at step 4'):
        resumed.call_role('software_developer', 'continue implementation')
    assert resumed.counter == 18
    resumed.dispatch({'op':resumed.current()['completion_tool'],
                      'check_plan':{'criterion':'current_task_failure_fixed'}})
    resumed.call_role('software_developer', 'continue implementation')
    assert resumed.counter == 19


def test_resume_rejects_invalid_counter_state(tmp_path):
    flow = make_active_resume_state(tmp_path)
    state = json.loads((tmp_path / 'workflow_state.json').read_text())
    state['role_calls'] = -1
    (tmp_path / 'workflow_state.json').write_text(json.dumps(state))
    identities = {'version': 'H0000', 'source_execution_hash': 'candidate-source'}
    with pytest.raises(ValueError, match='role_calls'):
        w.Workflow(tmp_path, lambda *args: None, identities=identities,
                   feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
                   resume_state=state)


def test_resume_authorization_binds_state_identity_and_candidate_source(tmp_path):
    flow = make_active_resume_state(tmp_path)
    workdir = tmp_path / 'candidate'
    (workdir / 'minisweagent').mkdir(parents=True)
    (workdir / 'minisweagent' / '__init__.py').write_text('fixture = 1\n')
    state_bytes = (tmp_path / 'workflow_state.json').read_bytes()
    expected = {'task_id': 't1', 'round_id': 'R0001', 'version': 'H0000',
                'parent': 0, 'proposer': 0, 'candidate': 'P0001'}
    authorization = {
        'schema': w.RESUME_SCHEMA,
        'state_sha256': hashlib.sha256(state_bytes).hexdigest(),
        'state': json.loads(state_bytes), 'identities': expected,
        'candidate_source_sha256': w.candidate_source_hash(workdir),
        'committee_cache_seed': [],
    }
    auth_path = tmp_path / 'resume_authorization.json'
    auth_path.write_text(json.dumps(authorization))
    loaded = b.load_resume_authorization(auth_path, tmp_path, expected, workdir, w)
    assert loaded['state']['active']['index'] == 2
    assert loaded['authorization_sha256'] == hashlib.sha256(auth_path.read_bytes()).hexdigest()
    authorization['state_sha256'] = '0' * 64
    auth_path.write_text(json.dumps(authorization))
    with pytest.raises(ValueError, match='state hash mismatch'):
        b.load_resume_authorization(auth_path, tmp_path, expected, workdir, w)


def test_resume_prompt_is_shared_and_idempotent(tmp_path):
    make_active_resume_state(tmp_path)
    authorization = {'state': json.loads((tmp_path / 'workflow_state.json').read_text()),
                     'candidate_source_sha256': 'candidate-source'}
    base = 'base improvement task'
    rendered = b.resume_prompt(base, authorization)
    assert 'EXPLICIT HOST-AUTHORIZED WORKFLOW RESUME.' in rendered
    assert 'stage 2' in rendered and 'failure' in rendered
    assert b.resume_prompt(rendered, authorization) == rendered


def test_host_and_container_resume_prompt_bytes_match(tmp_path):
    make_active_resume_state(tmp_path)
    state = json.loads((tmp_path / 'workflow_state.json').read_text())
    authorization = {'state': state, 'candidate_source_sha256': 'candidate-source'}
    kwargs = dict(upstream=ROOT / 'vendor/sica/agent_main.py', calls=50,
                  budget={'session_remaining_seconds': None}, task_id='t1', round_id='R0001', version='H0000', feedback='public facts')
    host_base = b.improvement_prompt(0, 0, 0, 'P0001', **kwargs)
    container_base = b.improvement_prompt(0, 0, 0, 'P0001', **kwargs)
    assert host_base.encode() == container_base.encode()
    assert b.resume_prompt(host_base, authorization).encode() == b.resume_prompt(
        container_base, authorization).encode()
    # The optional legacy arguments still render the same continuation bytes,
    # while the real launch path above uses the shared helper explicitly.
    legacy = b.improvement_prompt(0, 0, 0, 'P0001', **kwargs,
                                  resume_state=state, resume_source_exists=True)
    assert legacy.encode() == b.resume_prompt(host_base, authorization).encode()


def test_committee_cache_keeps_successes_when_later_reviewer_fails(tmp_path):
    calls = []

    def failing_delegate(identity, *args):
        calls.append(identity)
        if len(calls) == 3:
            raise RuntimeError('review request failed')
        return {'exit_status': 'Submitted', 'submission': identity}

    flow = online_flow(tmp_path, failing_delegate)
    request = {'op': 'committee', 'context': 'same context', 'proposal': 'same proposal'}
    with pytest.raises(RuntimeError, match='review request failed'):
        flow.dispatch(request)
    assert len(flow.committee_cache) == 2
    first_successes = set(calls[:2])

    flow.delegate = lambda identity, *args: calls.append(identity) or {
        'exit_status': 'Submitted', 'submission': identity}
    result = flow.dispatch(request)

    assert len(flow.committee_cache) == 4
    assert first_successes.issubset(set(calls))
    assert all(identity in result for identity in first_successes)
    assert calls[-2:] == ['004_reviewer', '005_reviewer']


def test_committee_cache_does_not_cross_source_revision(tmp_path):
    calls = []
    flow = online_flow(tmp_path, lambda identity, *args: calls.append(identity) or {
        'exit_status': 'Submitted', 'submission': identity})
    request = {'op': 'committee', 'context': 'context', 'proposal': 'proposal'}
    flow.dispatch(request)
    assert len(calls) == 4
    flow.identities['source_execution_hash'] = 'changed-source'
    flow.dispatch(request)
    assert len(calls) == 8


def test_committee_cache_rehashes_real_workdir_after_source_change(tmp_path):
    workdir = tmp_path / 'candidate'
    (workdir / 'minisweagent').mkdir(parents=True)
    source = workdir / 'minisweagent' / '__init__.py'
    source.write_text('VALUE = 1\n')
    calls = []
    flow = w.Workflow(
        tmp_path / 'logs',
        lambda identity, *args: calls.append(identity) or {
            'exit_status': 'Submitted', 'submission': identity},
        identities={'version': 'H0000', 'parent_version': 'H0000', 'model': 'fixture-model',
                    'workdir': str(workdir), 'source_execution_hash': 'stale-h0',
                    'task_id': 't1', 'round_id': 'R0001'},
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
    )
    request = {'op': 'committee', 'context': 'context', 'proposal': 'proposal'}
    flow.dispatch(request)
    assert len(calls) == 4
    flow.dispatch({'op':'invoke'})
    flow.active['index']=3
    flow.completed_steps=3
    flow.status='improving'
    flow.save()
    source.write_text('VALUE = 2\n')
    flow.dispatch(request)
    assert len(calls) == 8


def test_resume_preserves_old_committee_seed_without_reusing_it(tmp_path):
    workdir = tmp_path / 'candidate'
    (workdir / 'minisweagent').mkdir(parents=True)
    (workdir / 'minisweagent' / '__init__.py').write_text('VALUE = 2\n')
    seed = {'context': 'context', 'proposal': 'proposal',
            'reviewer_specialisation_name': next(iter(w.definitions()[1])),
            'submission': 'old H0 review', 'role_identity': '001_reviewer',
            'source_execution_hash': 'old-H0-execution-hash'}
    calls = []
    flow = w.Workflow(
        tmp_path / 'logs',
        lambda identity, *args: calls.append(identity) or {
            'exit_status': 'Submitted', 'submission': identity},
        identities={'version': 'H0000', 'parent_version': 'H0000', 'model': 'fixture-model',
                    'workdir': str(workdir), 'source_execution_hash': 'stale-h0',
                    'task_id': 't1', 'round_id': 'R0001'},
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
        resume_state=None,
    )
    flow._restore_committee_cache([seed])
    assert any(entry['submission'] == 'old H0 review' for entry in flow.committee_cache.values())
    flow.dispatch({'op': 'committee', 'context': 'context', 'proposal': 'proposal'})
    assert len(calls) == 4
    assert any(entry['submission'] == 'old H0 review' for entry in flow.committee_cache.values())
