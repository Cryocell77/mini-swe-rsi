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
    return ['improve', '--online', '--parent-iter', '0', '--best-iter', '0',
            '--current-iter', '0', '--candidate-id', 'P0001', '--task-id', 'task',
            '--round-id', 'R0001', '--online-version', version]


def test_online_improvement_prompt_binds_task_round_and_version():
    render = runpy.run_path(str(ROOT/'runtime/bridge.py'))['improvement_prompt']
    prompt = render(2, 2, 2, 'P0001', ROOT/'vendor/sica/agent_main.py',
                    online=True, task_id='task-1', round_id='R0001', version='H0002',
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
        driver.controller(code, logs, ['improve', '--online'], 'fixture')
    assert (logs/'result.json').read_text().find('TimeExceeded') >= 0


def test_online_improvement_rejects_parent_and_proposer_identity_mismatch(tmp_path):
    from mini_swe_rsi.driver import Driver, initial_code

    code = tmp_path/'code'
    initial_code(code)
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    args = ['improve', '--online', '--parent-iter', '0', '--best-iter', '1',
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
    args = ['improve', '--online', '--parent-iter', '0', '--best-iter', '0',
            '--current-iter', '0', '--candidate-id', 'P0001', '--task-id', 'task',
            '--round-id', 'R0001', '--online-version', 'H0000']
    with pytest.raises(RuntimeError, match='Frozen improvement task contract'):
        driver.controller(code, tmp_path/'logs', args, 'fixture')
