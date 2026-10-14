import importlib.util
import json
import shlex
import sys

import pytest

from mini_swe_rsi.core import ROOT

sys.path.insert(0, str(ROOT / '.upstream/mini/src'))
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted


spec = importlib.util.spec_from_file_location('completion_workflow_fixture', ROOT / 'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def command(request):
    return {'command': 'sica-workflow ' + shlex.quote(json.dumps(request, ensure_ascii=False))}


def raw_command(request):
    return {'command': 'sica-workflow ' + json.dumps(request, ensure_ascii=False)}


@pytest.mark.parametrize(
    ('readonly', 'role'),
    [(True, 'reviewer'), ('editor', 'software_developer'), ('orchestrator', 'orchestrator')],
)
def test_structured_submission_preserves_complete_report_for_every_role(tmp_path, readonly, role):
    flow = w.Workflow(tmp_path / role, lambda *args: None)
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
    flow = w.Workflow(tmp_path, lambda *args: None)
    report = w.FINAL_OUTPUT_SENTINEL + '\nlegacy complete'

    output = flow.execute(command({'op': 'submit', 'report': report}), True, 'reviewer')

    assert output == {'output': report + '\n', 'returncode': 0}
    assert output['output'].count(w.FINAL_OUTPUT_SENTINEL) == 1


def test_raw_json_submission_reaches_mini_without_shell_tokenization(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None)
    report = (
        '第一行\n'
        "apostrophe: it's safe; quoted: \"double quotes\"\n"
        'shell-looking: $(touch /tmp/nope) && echo hacked | cat > /tmp/nope\n'
        'unicode: café 🚀\n'
    )

    output = flow.execute(raw_command({'op': 'submit', 'report': report}), True, 'reviewer')

    expected = w.FINAL_OUTPUT_SENTINEL + '\n' + report + '\n'
    assert output == {'output': expected, 'returncode': 0}
    with pytest.raises(Submitted) as raised:
        LocalEnvironment()._check_finished(output)
    assert raised.value.messages[0]['extra']['submission'] == report + '\n'


def test_raw_json_transport_also_handles_structured_workflow_operations(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None)

    output = flow.execute(raw_command({'op': 'invoke'}), role='orchestrator')

    assert output['returncode'] == 0
    assert flow.active is not None
    assert flow.current()['index'] == 0


def test_raw_json_trailing_shell_syntax_is_rejected_without_execution(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None)
    command_text = raw_command({'op': 'submit', 'report': 'complete'})['command']

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
    flow = w.Workflow(tmp_path, lambda *args: None)

    output = flow.execute(command({'op': 'submit', 'report': report}), True, 'reviewer')

    assert output['returncode'] == 1
    assert flow.completed_steps == 0
    assert flow.active is None


def test_structured_submission_rejects_shell_wrapped_or_extra_fields(tmp_path):
    flow = w.Workflow(tmp_path, lambda *args: None)
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
