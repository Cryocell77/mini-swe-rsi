import importlib.util
import json

import pytest

from mini_swe_rsi.core import ROOT


spec = importlib.util.spec_from_file_location('online_diagnostic_workflow', ROOT / 'src/mini_swe_rsi/workflow/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


REF = 'tasks/t1/trace.txt'


def make_flow(tmp_path, delegate):
    flow = w.Workflow(
        tmp_path,
        delegate,
        identities={'version': 'H0000', 'public_archive': str(tmp_path / 'archive')},
        feedback={'task_id': 't1', 'round_id': 'R0001', 'evidence_refs': [REF]},
    )
    flow.dispatch({'op': 'invoke'})
    return flow


def complete_diagnostic_step(flow):
    flow.dispatch({
        'op': flow.current()['completion_tool'],
        'success': True,
        'report': 'diagnostic report',
        'evidence_refs': [REF],
        'identity': 'model-forged-identity',
    })


def test_empty_submitted_diagnostic_cannot_advance_and_can_retry(tmp_path):
    responses = iter([
        {'exit_status': 'Submitted', 'submission': ''},
        {'exit_status': 'Submitted', 'submission': 'archive evidence'},
    ])
    flow = make_flow(tmp_path, lambda *args: next(responses))

    with pytest.raises(ValueError, match='empty submission'):
        flow.call_role('archive_explorer', 'diagnose step one')
    assert flow.current()['index'] == 0 and flow.completed_steps == 0
    with pytest.raises(ValueError, match='archive_explorer submission'):
        complete_diagnostic_step(flow)

    flow.call_role('archive_explorer', 'retry step one')
    complete_diagnostic_step(flow)
    assert flow.current()['index'] == 1 and flow.completed_steps == 1


def test_successful_reviewer_cannot_substitute_for_archive_diagnostic(tmp_path):
    flow = make_flow(tmp_path, lambda *args: {'exit_status': 'Submitted', 'submission': 'review'})

    flow.call_role('reviewer', 'wrong diagnostic role')

    with pytest.raises(ValueError, match='archive_explorer submission'):
        complete_diagnostic_step(flow)
    assert flow.current()['index'] == 0 and flow.completed_steps == 0
    assert not flow.diagnostic_role_evidence


def test_diagnostic_evidence_is_bound_to_current_step_and_host_identity(tmp_path):
    flow = make_flow(tmp_path, lambda identity, *args: {
        'exit_status': 'Submitted', 'submission': 'archive evidence', 'identity': 'model-forged-identity',
    })

    flow.call_role('archive_explorer', 'step one')
    evidence = flow.diagnostic_role_evidence['1']
    assert evidence['identity'] == '001_archive_explorer'
    assert evidence['role'] == 'archive_explorer'
    assert evidence['step'] == 1 and evidence['index'] == 0
    assert evidence['invocation_id'] == flow.current()['invocation_id']

    events = [json.loads(line) for line in (tmp_path / 'workflow_events.jsonl').read_text().splitlines()]
    role_end = next(event for event in events if event['kind'] == 'role_end')
    assert role_end['identity'] == '001_archive_explorer'
    assert role_end['role'] == 'archive_explorer'
    assert role_end['step_index'] == 0

    # A stale host record from another step/invocation cannot be used to make
    # the current completion tool succeed.
    flow.diagnostic_role_evidence['1'] = {
        **evidence, 'step': 2, 'index': 1, 'invocation_id': 'stale-invocation',
    }
    with pytest.raises(ValueError, match='archive_explorer submission'):
        complete_diagnostic_step(flow)
    assert flow.current()['index'] == 0 and flow.completed_steps == 0

    flow.diagnostic_role_evidence['1'] = evidence
    complete_diagnostic_step(flow)
    assert flow.diagnostic_reports['1']['identity'] == '001_archive_explorer'
    assert flow.diagnostic_reports['1']['role'] == 'archive_explorer'
    assert flow.diagnostic_reports['1']['step_index'] == 0


def test_two_successful_archive_diagnostics_advance_in_order(tmp_path):
    flow = make_flow(tmp_path, lambda *args: {'exit_status': 'Submitted', 'submission': 'archive evidence'})

    flow.call_role('archive_explorer', 'step one')
    complete_diagnostic_step(flow)
    assert flow.current()['index'] == 1 and flow.status == 'active'
    flow.call_role('archive_explorer', 'step two')
    complete_diagnostic_step(flow)

    assert flow.current()['index'] == 2
    assert flow.completed_steps == 2
    assert flow.status == 'diagnosis_pending'
    assert set(flow.diagnostic_role_evidence) == {'1', '2'}


def test_last_successful_archive_retry_binds_current_step_identity(tmp_path):
    flow = make_flow(tmp_path, lambda identity, *args: {
        'exit_status': 'Submitted', 'submission': identity + ' evidence'})

    flow.call_role('archive_explorer', 'first attempt')
    flow.call_role('archive_explorer', 'last successful retry')
    complete_diagnostic_step(flow)

    assert flow.diagnostic_role_evidence['1']['identity'] == '002_archive_explorer'
    assert flow.diagnostic_reports['1']['identity'] == '002_archive_explorer'
