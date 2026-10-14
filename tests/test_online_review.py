"""Primary review regressions for online validation and evidence custody."""
import importlib.util
import json

import pytest

from mini_swe_rsi.core import ROOT


spec = importlib.util.spec_from_file_location('online_review_workflow', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def diagnosed(tmp_path):
    ref = 'tasks/0001-task/trace.txt'
    flow = w.Workflow(tmp_path, lambda *_: {'exit_status': 'Submitted', 'submission': 'Evidence inspected'},
                      online=True, identities={'version': 'H0000'},
                      feedback={'task_id': 'task', 'round_id': 'R0001', 'evidence_refs': [ref]})
    flow.dispatch({'op': 'invoke'})
    for index in range(2):
        flow.dispatch({'op': 'delegate', 'role': 'archive_explorer',
                       'task': f'Inspect evidence for diagnostic step {index + 1}'})
        flow.dispatch({'op': flow.current()['completion_tool'], 'report': f'Diagnostic report {index}',
                       'evidence_refs': [ref]})
    flow.dispatch({'op': 'diagnosis_decision', 'decision': 'improve', 'reason': 'Observed mechanism failure',
                   'improvement_opportunities': ['Repair the observed mechanism'], 'evidence_refs': [ref]})
    return flow


def test_step_three_requires_a_model_declared_plan(tmp_path):
    flow = diagnosed(tmp_path)
    completion = flow.current()['completion_tool']
    with pytest.raises(ValueError, match='explicit check_plan'):
        flow.dispatch({'op': completion})
    assert flow.completed_steps == 2
    assert not (tmp_path/'check_plan_declared.json').exists()
    plan = {'criterion': 'current_task_failure_fixed'}
    flow.dispatch({'op': completion, 'check_plan': plan})
    assert flow.completed_steps == 3
    assert json.loads((tmp_path/'check_plan_declared.json').read_text()) == {**plan, 'revision': '1'}


@pytest.mark.parametrize('plan', [
    {},
    {'criterion': 'mechanism_parent_failed_candidate_passed'},
    {'criterion': 'mechanism_parent_failed_candidate_passed', 'mechanism_plan': {'test_path': '../private/test.py'}},
    {'criterion': 'current_task_failure_fixed', 'parent_version': 'H9999'},
])
def test_model_cannot_replace_host_identity_or_omit_mechanism_plan(tmp_path, plan):
    flow = diagnosed(tmp_path)
    with pytest.raises(ValueError):
        flow.dispatch({'op': flow.current()['completion_tool'], 'check_plan': plan})
    assert flow.completed_steps == 2


def test_diagnostic_report_cannot_make_a_private_reference_trusted(tmp_path):
    ref = 'tasks/0001-task/trace.txt'
    flow = w.Workflow(tmp_path, lambda *_: {'exit_status': 'Submitted', 'submission': 'Public evidence inspected'}, online=True,
                      feedback={'task_id': 'task', 'round_id': 'R0001', 'evidence_refs': [ref]})
    flow.dispatch({'op': 'invoke'})
    flow.dispatch({'op': 'delegate', 'role': 'archive_explorer', 'task': 'Inspect public evidence'})
    with pytest.raises(ValueError, match='public task feedback'):
        flow.dispatch({'op': flow.current()['completion_tool'], 'report': 'Invented evidence',
                       'evidence_refs': ['private/grading/result.json']})
    assert flow.completed_steps == 0


def test_candidate_check_must_use_step_three_mechanism_plan(tmp_path):
    flow = diagnosed(tmp_path)
    plan = {'criterion': 'mechanism_parent_failed_candidate_passed',
            'mechanism_plan': {'test_path': 'tests/check.py', 'args': [], 'timeout_seconds': 60}}
    flow.dispatch({'op': flow.current()['completion_tool'], 'check_plan': plan})
    flow.dispatch({'op': flow.current()['completion_tool']})
    calls = []
    flow.candidate_check = lambda payload: calls.append(payload) or {'passed': True}
    request = {'op': 'candidate_check', 'job_id': 'candidate:R0001:P0001', 'task_id': 'task',
               'round_id': 'R0001', 'candidate': 'P0001', 'parent_version': 'H0000', 'check_revision': '1'}
    with pytest.raises(ValueError, match='cannot change'):
        flow.dispatch({**request, 'mechanism_plan': {**plan['mechanism_plan'], 'test_path': 'tests/other.py'}})
    assert not calls
    flow.dispatch(request)
    assert len(calls) == 1
    assert flow.completed_steps == 4


def test_online_archive_does_not_rank_unpaired_versions(tmp_path):
    flow = w.Workflow(tmp_path, lambda *_: None, online=True,
                      identities={'public_archive': str(tmp_path/'archive')},
                      feedback={'task_id': 'task', 'round_id': 'R0001',
                                'evidence_refs': ['tasks/0001-task/trace.txt']})
    with pytest.raises(ValueError, match='different task coverage'):
        flow.dispatch({'op': 'archive', 'kind': 'best', 'iteration': 0}, role='archive_explorer')
    assert 'Do not rank' in flow.dispatch({'op': 'archive', 'kind': 'overview'}, role='archive_explorer')


def test_online_cli_finalizer_uses_online_report(tmp_path, monkeypatch):
    from mini_swe_rsi import cli, report
    from mini_swe_rsi.online import OnlineLoop
    seen = []

    class Loop:
        def run(self):
            return {'status': 'completed'}

        def close(self):
            seen.append('closed')

    monkeypatch.setattr(OnlineLoop, 'from_environment', lambda *a, **k: Loop())
    monkeypatch.setattr(report, 'build_online_report', lambda root: seen.append('online_report'))
    monkeypatch.setattr(cli, 'build_report', lambda root: pytest.fail('Online mode must not invoke native report'))
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'online', '--run', str(tmp_path/'run'), '--task-limit', '3'])
    cli.main()
    assert seen == ['closed', 'online_report']
