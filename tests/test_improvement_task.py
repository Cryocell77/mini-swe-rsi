"""Online prompt identity and host launch boundary regressions."""
import hashlib
import runpy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import ROOT, improvement_contract, write


def _prelaunch_driver(tmp_path):
    from mini_swe_rsi.driver import Driver

    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    driver.broker = SimpleNamespace(
        model='fixture',
        sessions={'fixture': {'deadline': time.time() + 600}},
        budget_limits=lambda: {
            'main_iterations': 500,
            'role_iterations': {'archive_explorer': 500, 'software_developer': 1000,
                                'general_problem_solver': 500, 'reviewer': 500},
            'model_max_output_tokens': 2048,
            'context_max_tokens': 32768,
        })
    return driver


def _online_args(version='H0000'):
    return ['improve', '--parent-iter', '0', '--best-iter', '0',
            '--current-iter', '0', '--candidate-id', 'P0001', '--task-id', 'task',
            '--round-id', 'R0001', '--online-version', version]


def test_online_improvement_prompt_binds_task_round_and_version():
    render = runpy.run_path(str(ROOT/'runtime/bridge.py'))['improvement_prompt']
    prompt = render(2, 2, 2, 'P0001', ROOT/'vendor/sica/agent_main.py',
                    task_id='task-1', round_id='R0001', version='H0002',
                    feedback='Observed public failure')
    assert 'task-1' in prompt and 'R0001' in prompt and 'H0002' in prompt
    assert 'parent=agent_2; proposer=agent_2; candidate=P0001' in prompt
    assert 'Observed public failure' in prompt
    assert 'best performing agent' not in prompt
    contract = improvement_contract()
    assert contract['renderer_sha256'] == hashlib.sha256((ROOT/'runtime/bridge.py').read_bytes()).hexdigest()


def test_benchmark_uses_exact_prompt_file_without_improvement_additions():
    import ast

    tree = ast.parse((ROOT/'runtime/bridge.py').read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    branch = next(node for node in ast.walk(main) if isinstance(node, ast.If) and node.orelse
                  and ast.unparse(node.test) == "args.command == 'improve'")
    assert ast.unparse(branch.orelse[0]) == 'task = Path(args.prompt_file).read_text()'


def test_existing_online_workflow_evidence_cannot_be_overwritten(tmp_path):
    from mini_swe_rsi.driver import Driver, initial_code

    code = tmp_path/'code'
    initial_code(code)
    logs = tmp_path/'logs'
    write(logs/'workflow_state.json', {'status': 'incomplete'})
    write(logs/'result.json', {'exit_status': 'TimeExceeded'})
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    with pytest.raises(RuntimeError, match='Existing workflow state'):
        driver.controller(code, logs, ['improve'], 'fixture')
    assert (logs/'result.json').read_text().find('TimeExceeded') >= 0


def test_online_improvement_rejects_parent_and_proposer_identity_mismatch(tmp_path):
    from mini_swe_rsi.driver import Driver, initial_code

    code = tmp_path/'code'
    initial_code(code)
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    args = ['improve', '--parent-iter', '0', '--best-iter', '1',
            '--current-iter', '0', '--candidate-id', 'P0001', '--task-id', 'task',
            '--round-id', 'R0001', '--online-version', 'H0000']
    with pytest.raises(RuntimeError, match='parent|proposer|best'):
        driver.controller(code, tmp_path/'logs', args, 'fixture')


@pytest.mark.parametrize('case', ['version_label', 'selected_path', 'inactive_version'])
def test_online_archive_identity_guards_fail_before_container_setup(tmp_path, case):
    from mini_swe_rsi.driver import initial_code

    driver = _prelaunch_driver(tmp_path)
    archive = tmp_path/'archive'
    write(archive/'online_state.json', {'active_version': 'H0000'})
    write(archive/'configuration.json', {'control_manifest': {'improvement': improvement_contract()}})
    if case == 'selected_path':
        code = archive/'wrong/H0000/agent_code'
    else:
        code = archive/'versions/H0000/agent_code'
    initial_code(code)
    if case == 'inactive_version':
        write(archive/'online_state.json', {'active_version': 'H0001'})
    version = 'H0001' if case == 'version_label' else 'H0000'
    expected = {
        'version_label': 'version does not match',
        'selected_path': 'active archive version source',
        'inactive_version': 'active version differs',
    }[case]
    with pytest.raises(RuntimeError, match=expected):
        driver.controller(code, tmp_path/'logs', _online_args(version), 'fixture', archive=archive)
    assert not hasattr(driver, 'client')


def test_online_renderer_contract_change_is_rejected_before_runtime_setup(tmp_path, monkeypatch):
    from mini_swe_rsi.driver import Driver, initial_code

    code = tmp_path/'code'
    initial_code(code)
    write(tmp_path/'configuration.json', {'control_manifest': {'improvement': improvement_contract()}})
    changed = {**improvement_contract(), 'renderer_sha256': 'changed-before-launch'}
    monkeypatch.setattr('mini_swe_rsi.driver.improvement_contract', lambda: changed)
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    args = ['improve', '--parent-iter', '0', '--best-iter', '0',
            '--current-iter', '0', '--candidate-id', 'P0001', '--task-id', 'task',
            '--round-id', 'R0001', '--online-version', 'H0000']
    with pytest.raises(RuntimeError, match='Frozen improvement task contract'):
        driver.controller(code, tmp_path/'logs', args, 'fixture')


def _bridge_entry(monkeypatch, argv):
    import runpy
    bridge = runpy.run_path(str(ROOT/'runtime/bridge.py'))
    monkeypatch.setattr('sys.argv', ['bridge', *argv])
    # Identity validation must precede any model/agent setup and log creation.
    monkeypatch.setitem(bridge['main'].__globals__, 'budget_config',
                        lambda: pytest.fail('entry read limits before identity validation'))
    return bridge['main']


def _improvement_argv(tmp_path, **overrides):
    values = {'parent': '0', 'best': '0', 'candidate': 'P0001', 'task': 'task',
              'round': 'R0001', 'version': 'H0000', 'prompt': '/task.txt'}
    values.update(overrides)
    argv = ['improve', '--logdir', str(tmp_path/'logs'),
            '--parent-iter', values['parent'], '--best-iter', values['best'],
            '--candidate-id', values['candidate'], '--task-id', values['task'],
            '--round-id', values['round'], '--online-version', values['version'],
            '--prompt-file', values['prompt']]
    return argv


def test_improvement_entry_requires_full_identity_before_model_setup(tmp_path, monkeypatch):
    argv = ['improve', '--logdir', str(tmp_path/'logs'), '--parent-iter', '0',
            '--best-iter', '0', '--candidate-id', 'P0001', '--prompt-file', '/task.txt']
    main = _bridge_entry(monkeypatch, argv)
    with pytest.raises(ValueError, match='task_id, round_id, online_version'):
        main(str(tmp_path/'missing-entry'))
    assert not (tmp_path/'logs').exists()


def test_improvement_entry_rejects_wrong_version_or_proposer_before_model_setup(tmp_path, monkeypatch):
    wrong_version = _improvement_argv(tmp_path, version='H0005')
    with pytest.raises(ValueError, match='must equal H0000'):
        _bridge_entry(monkeypatch, wrong_version)(str(tmp_path/'missing-entry'))
    wrong_proposer = _improvement_argv(tmp_path, best='1')
    with pytest.raises(ValueError, match='parent and proposer'):
        _bridge_entry(monkeypatch, wrong_proposer)(str(tmp_path/'missing-entry'))
    assert not (tmp_path/'logs').exists()


def test_improvement_entry_rejects_missing_parent_candidate_or_feedback(tmp_path, monkeypatch):
    argv = _improvement_argv(tmp_path, candidate='')
    with pytest.raises(ValueError, match='candidate_id'):
        _bridge_entry(monkeypatch, argv)(str(tmp_path/'missing-entry'))
    argv = _improvement_argv(tmp_path, prompt='')
    with pytest.raises(ValueError, match='prompt_file'):
        _bridge_entry(monkeypatch, argv)(str(tmp_path/'missing-entry'))
    argv = [arg for arg in _improvement_argv(tmp_path) if arg != '--parent-iter']
    argv.remove('0')
    with pytest.raises(ValueError, match='parent_iter'):
        _bridge_entry(monkeypatch, argv)(str(tmp_path/'missing-entry'))
    assert not (tmp_path/'logs').exists()


def test_benchmark_entry_rejects_resume_authorization_without_model_setup(tmp_path, monkeypatch):
    argv = ['benchmark', '--logdir', str(tmp_path/'logs'),
            '--prompt-file', '/task.txt',
            '--resume-workflow', str(tmp_path/'authorization.json')]
    with pytest.raises(ValueError, match='does not support workflow restoration'):
        _bridge_entry(monkeypatch, argv)(str(tmp_path/'missing-entry'))
    assert not (tmp_path/'logs').exists()
    bridge = runpy.run_path(str(ROOT/'runtime/bridge.py'))
    assert bridge['validate_improvement_args'](
        SimpleNamespace(command='benchmark', resume_workflow=None)) is None
