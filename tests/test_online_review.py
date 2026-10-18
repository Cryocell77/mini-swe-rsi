"""Primary review regressions for online validation and evidence custody."""
import importlib.util
import json

import pytest

from mini_swe_rsi.core import ROOT
from mini_swe_rsi.core import write


spec = importlib.util.spec_from_file_location('online_review_workflow', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def diagnosed(tmp_path):
    ref = 'tasks/0001-task/trace.txt'
    flow = w.Workflow(tmp_path, lambda *_: {'exit_status': 'Submitted', 'submission': 'Evidence inspected'},
                      identities={'version': 'H0000'},
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
    flow = w.Workflow(tmp_path, lambda *_: {'exit_status': 'Submitted', 'submission': 'Public evidence inspected'}, feedback={'task_id': 'task', 'round_id': 'R0001', 'evidence_refs': [ref]})
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
    flow = w.Workflow(tmp_path, lambda *_: None, identities={'public_archive': str(tmp_path/'archive')},
                      feedback={'task_id': 'task', 'round_id': 'R0001',
                                'evidence_refs': ['tasks/0001-task/trace.txt']})
    with pytest.raises(ValueError, match='chronological overview|different task coverage'):
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
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'online', '--run', str(tmp_path/'run'), '--task-limit', '3'])
    cli.main()
    assert seen == ['closed', 'online_report']


def test_successful_online_run_propagates_report_finalizer_value_error(tmp_path, monkeypatch):
    from mini_swe_rsi import cli, report
    from mini_swe_rsi.online import OnlineLoop
    seen = []

    class Loop:
        def run(self):
            seen.append('ran')
            return {'status': 'completed'}

        def close(self):
            seen.append('closed')

    def report_fail(root):
        raise ValueError('report finalizer failure')

    monkeypatch.setattr(OnlineLoop, 'from_environment', lambda *a, **k: Loop())
    monkeypatch.setattr(report, 'build_online_report', report_fail)
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'online', '--run', str(tmp_path/'run')])
    with pytest.raises(ValueError, match='report finalizer failure'):
        cli.main()
    assert seen == ['ran', 'closed']


@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit])
def test_cli_preserves_interrupt_setup_signal_when_report_finalizer_fails(tmp_path, monkeypatch, signal):
    from mini_swe_rsi import cli, report
    from mini_swe_rsi.online import OnlineLoop

    def start(*args, **kwargs):
        raise signal('startup signal')

    def report_fail(root):
        raise ValueError('report finalizer failure')

    run = tmp_path/'run'
    monkeypatch.setattr(OnlineLoop, 'from_environment', start)
    monkeypatch.setattr(report, 'build_online_report', report_fail)
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'online', '--run', str(run)])
    with pytest.raises(signal):
        cli.main()
    assert not (run/'failure.json').exists()


@pytest.mark.parametrize('kind', ['malformed_config', 'malformed_state', 'conflicting_protocol', 'native_root'])
def test_online_report_rejects_malformed_or_native_roots(tmp_path, kind):
    from mini_swe_rsi.report import build_online_report

    run = tmp_path/'run'
    run.mkdir()
    if kind == 'malformed_config':
        (run/'configuration.json').write_text('{not-json')
    else:
        write(run/'configuration.json', {'online_protocol': 'mini-swe-rsi-online-v3'})
        write(run/'online_state.json', {'protocol': 'mini-swe-rsi-online-v3', 'active_version': 'H0000'})
        if kind == 'malformed_state':
            write(run/'online_state.json', ['invalid'])
        elif kind == 'conflicting_protocol':
            write(run/'configuration.json', {
                'online_protocol': 'mini-swe-rsi-online-v3', 'protocol': 'native-v1'})
        elif kind == 'native_root':
            write(run/'baseline.json', {'legacy': True})
    with pytest.raises((ValueError, json.JSONDecodeError), match='Online|online|legacy|protocol|JSON|Expecting'):
        build_online_report(run)


def test_online_v2_and_common_task_contracts_are_explicitly_rejected(tmp_path):
    from mini_swe_rsi.online import OnlineError, OnlineLoop

    with pytest.raises(OnlineError, match='Unsupported online protocol'):
        OnlineLoop(tmp_path/'v2', ('task',), contract={
            'protocol': 'mini-swe-rsi-online-v2',
            'check_policy': {'revision': '1', 'common_tasks': []},
        })
    with pytest.raises(OnlineError, match='common_tasks is unsupported'):
        OnlineLoop(tmp_path/'common', ('task',), contract={
            'protocol': 'mini-swe-rsi-online-v3',
            'check_policy': {'revision': '2', 'common_tasks': []},
        })


def test_online_report_rejects_common_task_policy_in_v3_root(tmp_path):
    from mini_swe_rsi.report import build_online_report

    run = tmp_path/'run'
    run.mkdir()
    write(run/'configuration.json', {
        'online_protocol': 'mini-swe-rsi-online-v3',
        'protocol': 'mini-swe-rsi-online-v3',
        'check_policy': {'revision': '2', 'common_tasks': []},
    })
    write(run/'online_state.json', {'protocol': 'mini-swe-rsi-online-v3', 'active_version': 'H0000'})
    with pytest.raises(ValueError, match='common_tasks is unsupported'):
        build_online_report(run)


def test_cli_report_failure_is_not_silently_suppressed(tmp_path, monkeypatch):
    from mini_swe_rsi import cli, report

    def fail(root):
        raise ValueError('report contract failure')

    monkeypatch.setattr(report, 'build_online_report', fail)
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'report', '--run', str(tmp_path/'run')])
    with pytest.raises(ValueError, match='report contract failure'):
        cli.main()


def test_cli_preserves_online_startup_error_when_report_finalizer_fails(tmp_path, monkeypatch):
    from mini_swe_rsi import cli, report
    from mini_swe_rsi.online import OnlineLoop

    def start(*args, **kwargs):
        raise RuntimeError('startup contract failure')

    def report_fail(root):
        raise ValueError('report finalizer failure')

    monkeypatch.setattr(OnlineLoop, 'from_environment', start)
    monkeypatch.setattr(report, 'build_online_report', report_fail)
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'online', '--run', str(tmp_path/'run')])
    with pytest.raises(RuntimeError, match='startup contract failure'):
        cli.main()


@pytest.mark.parametrize('command', ['baseline', 'evolve', 'evaluate'])
def test_cli_rejects_removed_native_commands(command, monkeypatch):
    from mini_swe_rsi import cli

    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', command, '--run', '/tmp/native'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
