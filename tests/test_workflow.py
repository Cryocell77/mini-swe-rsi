"""Offline tests for the retained online SICA workflow transport."""
import importlib.util
import json
import shlex

import pytest

from mini_swe_rsi.core import ROOT, improvement_contract, read

spec = importlib.util.spec_from_file_location('workflow_fixture', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
FEEDBACK = {'task_id': 'task', 'round_id': 'R0001',
            'evidence_refs': ['tasks/task/trace.txt']}


def command(request):
    return {'command': 'sica-workflow ' + shlex.quote(json.dumps(request))}


def test_pinned_fields_and_core_prompts():
    steps, specs, roles = w.definitions()
    assert len(steps) == 7
    assert list(specs) == ['pragmatist', 'taste_maker', 'utility_optimizer', 'testing_manager']
    assert steps[0]['instruction'].startswith('Use the archive explorer agent')
    assert 'MUST call' in steps[2]['instruction']
    assert 'DO NOT attempt the task yourself' in w.core_prompt(
        'review_committee_member', {'specialisation': 'spec', 'context': 'context', 'proposal': 'proposal'})
    assert 'outer-loop entrypoint orchestrator' in roles['main_orchestrator']['SYSTEM_PROMPT']
    assert improvement_contract()['workflow_sources'] == {
        n: v['sha256'] for n, v in read(ROOT/'upstream.lock.json')['sica']['workflow_files'].items()}


def test_online_workflow_requires_diagnostic_evidence_before_completion(tmp_path):
    flow = w.Workflow(tmp_path, lambda *a: None, online=True, feedback=FEEDBACK)
    state = flow.dispatch({'op': 'invoke'})
    with pytest.raises(ValueError):
        flow.dispatch({'op': 'complete', 'invocation_id': state['invocation_id'], 'tool': 'bad'})
    with pytest.raises(ValueError, match='archive_explorer submission'):
        flow.dispatch({'op': 'complete', 'invocation_id': state['invocation_id'],
                       'tool': state['completion_tool']})
    assert flow.completed_steps == 0


def test_online_committee_is_read_only_and_role_permissions_are_enforced(tmp_path):
    calls = []

    def delegate(identity, prompt, readonly):
        calls.append((identity, prompt, readonly))
        return {'exit_status': 'Submitted', 'submission': identity}

    flow = w.Workflow(tmp_path, delegate, online=True, feedback=FEEDBACK)
    result = flow.dispatch({'op': 'committee', 'context': 'evidence', 'proposal': 'design'})
    assert len(calls) == 4 and all(item[2] for item in calls)
    assert result == ''.join(f"\n\nReviewer {i}'s review:\n{item[0]}"
                             for i, item in enumerate(calls))
    assert all('DO NOT attempt the task yourself' in item[1] for item in calls)
    assert flow.execute({'command': 'touch /home/agent/workdir/forbidden'}, True)['returncode'] == 1
    assert flow.execute({'command': 'sica-workflow bad'}, True)['returncode'] == 1
    assert flow.execute(command({'op': 'read', 'path': '/etc/passwd'}), True)['returncode'] == 1


def test_child_failure_is_not_fake_success(tmp_path):
    flow = w.Workflow(tmp_path, lambda *a: {'exit_status': 'LimitsExceeded', 'submission': ''}, online=True, feedback=FEEDBACK)
    with pytest.raises(ValueError, match='LimitsExceeded'):
        flow.dispatch({'op': 'committee', 'context': '', 'proposal': ''})
    assert flow.counter == 1
    assert flow.completed_steps == 0


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'changed'])
def test_pinned_sources_reject_missing_extra_or_changed(tmp_path, monkeypatch, mutation):
    import shutil
    from mini_swe_rsi import core

    shutil.copytree(ROOT/'vendor/sica_workflow', tmp_path/'vendor/sica_workflow')
    shutil.copy(ROOT/'upstream.lock.json', tmp_path/'upstream.lock.json')
    source = tmp_path/'vendor/sica_workflow/meta_improvement.py'
    if mutation == 'missing':
        source.unlink()
    elif mutation == 'extra':
        (source.parent/'extra.py').write_text('pass\n')
    else:
        source.write_text(source.read_text() + '# changed\n')
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    with pytest.raises(RuntimeError, match='Pinned workflow sources'):
        core.improvement_contract()


def test_atomic_state_write_preserves_previous_valid_state(tmp_path, monkeypatch):
    from pathlib import Path

    flow = w.Workflow(tmp_path, lambda *a: None, online=True, feedback=FEEDBACK)
    previous = read(tmp_path/'workflow_state.json')

    def interrupted(*args, **kwargs):
        raise OSError('simulated interruption before atomic replace')

    monkeypatch.setattr(Path, 'replace', interrupted)
    flow.status = 'active'
    with pytest.raises(OSError, match='interruption'):
        flow.save()
    assert read(tmp_path/'workflow_state.json') == previous


def test_tool_aliases_preserve_online_permissions(tmp_path):
    assert w.tool_aliases() == {
        'meta_improvement_reasoning_structure': 'invoke',
        'meta_improvement_review_committee': 'committee'}
    flow = w.Workflow(tmp_path, lambda *a: None, online=True, feedback=FEEDBACK)
    request = {'op': 'meta_improvement_reasoning_structure', 'step': 1}
    outcome = flow.execute(command(request), 'orchestrator')
    assert outcome['returncode'] == 0
    assert flow.completed_steps == 0
    assert flow.execute(command(request), 'editor')['returncode'] == 1
