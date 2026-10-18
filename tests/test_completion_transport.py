import importlib.util
import json
import sys

import pytest

from mini_swe_rsi.core import ROOT

sys.path.insert(0, str(ROOT / '.upstream/mini/src'))
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted


spec = importlib.util.spec_from_file_location('completion_workflow_fixture', ROOT / 'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


PUBLIC_FEEDBACK = {'task_id': 'task', 'round_id': 'R0001', 'evidence_refs': []}


def command(request):
    return {'command': 'sica-workflow ' + json.dumps(request, ensure_ascii=False)}


@pytest.mark.parametrize(
    ('readonly', 'role'),
    [(True, 'reviewer'), ('editor', 'software_developer'), ('orchestrator', 'orchestrator')],
)
def test_structured_submission_preserves_complete_report_for_every_role(tmp_path, readonly, role):
    flow = w.Workflow(tmp_path / role, lambda *args: None, feedback=PUBLIC_FEEDBACK)
    state = flow.dispatch({'op': 'invoke'})
    report = (
        '第一行\nquotes: \'single\' "double"\n'
        + 'shell-looking: $(touch /tmp/nope); && || | > < `backtick`\n'
        + 'emoji: 🚀 / unicode: café / trailing spaces  '
    )

    output = flow.execute(command({'op': 'submit', 'report': report}), readonly, role)

    expected = w.FINAL_OUTPUT_SENTINEL + '\n' + report + '\n'
    assert output == {'output': expected, 'returncode': 0}
    assert flow.current()['invocation_id'] == state['invocation_id']
    assert flow.current()['index'] == 0
    assert flow.completed_steps == 0

    with pytest.raises(Submitted) as raised:
        LocalEnvironment()._check_finished(output)
    assert raised.value.messages[0]['extra']['submission'] == report + '\n'
    flow.finish({'exit_status': 'Submitted'})
    assert flow.status == 'incomplete'
    assert flow.status not in ('completed', 'skipped_no_actionable_improvement')


def test_structured_submission_accepts_exact_prefixed_legacy_report(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None, feedback=PUBLIC_FEEDBACK)
    report = w.FINAL_OUTPUT_SENTINEL + '\nlegacy complete'

    output = flow.execute(command({'op': 'submit', 'report': report}), True, 'reviewer')

    assert output == {'output': report + '\n', 'returncode': 0}
    assert output['output'].count(w.FINAL_OUTPUT_SENTINEL) == 1


def test_raw_json_submission_reaches_mini_without_shell_tokenization(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None, feedback=PUBLIC_FEEDBACK)
    report = (
        '第一行\n'
        "apostrophe: it's safe; quoted: \"double quotes\"\n"
        'shell-looking: $(touch /tmp/nope) && echo hacked | cat > /tmp/nope\n'
        'unicode: café 🚀\n'
    )

    output = flow.execute(command({'op': 'submit', 'report': report}), True, 'reviewer')

    expected = w.FINAL_OUTPUT_SENTINEL + '\n' + report + '\n'
    assert output == {'output': expected, 'returncode': 0}
    with pytest.raises(Submitted) as raised:
        LocalEnvironment()._check_finished(output)
    assert raised.value.messages[0]['extra']['submission'] == report + '\n'


def test_raw_json_transport_also_handles_structured_workflow_operations(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None, feedback=PUBLIC_FEEDBACK)

    output = flow.execute(command({'op': 'invoke'}), role='orchestrator')

    assert output['returncode'] == 0
    assert flow.active is not None
    assert flow.current()['index'] == 0


@pytest.mark.parametrize('readonly,role', [(True, 'reviewer'), ('orchestrator', 'orchestrator')])
@pytest.mark.parametrize('legacy_command', [
    'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT',
    "printf '%s\\n' 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\ncomplete'",
    "sica-workflow '{\"op\":\"submit\",\"report\":\"complete\"}'",
])
def test_removed_legacy_submission_forms_cannot_trigger_mini_submitted(
        tmp_path, readonly, role, legacy_command):
    """Legacy shell submission paths must fail before mini sees completion."""
    flow = w.Workflow(tmp_path / role, lambda *args: None, feedback=PUBLIC_FEEDBACK)

    output = flow.execute({'command': legacy_command}, readonly, role)

    assert output['returncode'] == 1
    # A returncode of zero with the sentinel would make LocalEnvironment raise
    # Submitted; rejected transport output must never enter that path.
    LocalEnvironment()._check_finished(output)
    assert flow.active is None and flow.completed_steps == 0


@pytest.mark.parametrize('readonly,role', [(True, 'reviewer'), ('orchestrator', 'orchestrator')])
@pytest.mark.parametrize('wrapper', ['quoted', 'echo', 'printf'])
def test_shell_wrapped_json_and_sentinel_wrappers_are_rejected_without_side_effect(
        tmp_path, readonly, role, wrapper):
    """The workflow boundary accepts only one raw JSON object.

    These are common legacy model emissions.  Keep the marker outside the
    JSON so a shell implementation would create it; the transport must reject
    the whole action before any shell interpretation can occur.
    """
    flow = w.Workflow(tmp_path / role, lambda *args: None, feedback=PUBLIC_FEEDBACK)
    payload = json.dumps({'op': 'submit', 'report': 'complete'}, ensure_ascii=False)
    if wrapper == 'quoted':
        command_text = "sica-workflow '" + payload + "'"
    elif wrapper == 'echo':
        command_text = 'sica-workflow echo ' + payload
    else:
        command_text = "sica-workflow printf '%s\\n' " + payload
    marker = tmp_path / f'{wrapper}-{role}-executed'
    output = flow.execute({'command': command_text + f'; touch {marker}'}, readonly, role)

    assert output['returncode'] == 1
    assert not marker.exists()
    assert flow.active is None and flow.completed_steps == 0


def test_raw_json_trailing_shell_syntax_is_rejected_without_execution(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None, feedback=PUBLIC_FEEDBACK)
    command_text = command({'op': 'submit', 'report': 'complete'})['command']

    output = flow.execute({'command': command_text + ' ; printf hacked'}, True, 'reviewer')

    assert output['returncode'] == 1
    assert 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' not in output['output']
    assert not (tmp_path / 'hacked').exists()


@pytest.mark.parametrize(
    'report',
    [
        '',
        '   ',
        None,
        42,
        [],
        {},
        w.FINAL_OUTPUT_SENTINEL,
        w.FINAL_OUTPUT_SENTINEL + '\n   ',
    ],
)
def test_structured_submission_rejects_empty_or_malformed_reports(tmp_path, report):
    flow = w.Workflow(tmp_path, lambda *args: None, feedback=PUBLIC_FEEDBACK)

    output = flow.execute(command({'op': 'submit', 'report': report}), True, 'reviewer')

    assert output['returncode'] == 1
    assert flow.completed_steps == 0
    assert flow.active is None


def test_structured_submission_rejects_shell_wrapped_or_extra_fields(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None, feedback=PUBLIC_FEEDBACK)
    report = w.FINAL_OUTPUT_SENTINEL + '\ncomplete'

    malformed_wrapper = flow.execute(
        {'command': "sica-workflow printf '%s\\n' 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\ncomplete'"},
        True,
        'reviewer',
    )
    extra_field = flow.execute(
        command({'op': 'submit', 'report': report, 'command': 'printf hacked'}),
        True,
        'reviewer',
    )

    assert malformed_wrapper['returncode'] == 1
    assert extra_field['returncode'] == 1
