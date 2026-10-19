"""Local task isolation, interactive selection and durable promotion."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.cli import build_parser
from mini_swe_rsi.core import read, tree_hash, write
from mini_swe_rsi.driver import initial_code
from mini_swe_rsi.online import OnlineLoop
from mini_swe_rsi.single import Versions, run_single, snapshot_workspace


def test_single_cli_and_required_verification():
    args = build_parser().parse_args(['single', '--workspace', '/tmp/project', '--check', 'pytest'])
    assert args.version is None and args.prompt is None and args.check_timeout == 300
    with pytest.raises(SystemExit):
        build_parser().parse_args(['single', '--workspace', '/tmp/project'])


def test_snapshot_excludes_credentials_verifier_and_caches(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    for name in ('app.py', '.env', '.env.local', 'private-check.py'):
        (source/name).write_text(name)
    (source/'.git').mkdir()
    (source/'.git/config').write_text('secret')
    target = tmp_path/'snapshot'
    snapshot_workspace(source, target, exclude=source/'private-check.py')
    assert [p.name for p in target.iterdir()] == ['app.py']
    assert (source/'private-check.py').exists()
    with pytest.raises(ValueError, match='outside'):
        snapshot_workspace(source, source/'output')


def test_snapshot_does_not_follow_links(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    (source/'external').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        snapshot_workspace(source, tmp_path/'snapshot')


def publish_fixture(tmp_path):
    root = tmp_path/'execution'
    code = root/'versions/H0001/agent_code'
    initial_code(code)
    write(code.parent/'version_identity.json', {'version': 'H0001'})
    write(root/'decisions/R0001.json', {'decision': 'accepted'})
    loop = SimpleNamespace(root=root, active_version='H0001', active_code=code,
                           control_manifest=OnlineLoop._control_manifest())
    versions = Versions(tmp_path/'store')
    return versions, loop


def test_versions_persist_select_newest_and_detect_tampering(tmp_path):
    versions, loop = publish_fixture(tmp_path)
    assert versions.publish(loop, 'original') == 'V000001'
    assert versions.publish(loop, 'V000001') == 'V000002'
    answers = iter(['bad', '2', 'missing', 'V000001'])
    prompts, output = [], []
    def ask(text):
        prompts.append(text)
        return next(answers)
    code, name = versions.select(ask=ask, show=output.append)
    assert name == 'V000001' and code.exists()
    assert output[0].startswith('V000002') and output[1].startswith('V000001')
    assert versions.select('original') == (None, 'original')
    (code/'README.md').write_text('tampered')
    with pytest.raises(ValueError, match='source changed'):
        versions.select(name)


def test_no_promotion_for_original_or_missing_host_decision(tmp_path):
    versions, loop = publish_fixture(tmp_path)
    loop.active_version = 'H0000'
    assert versions.publish(loop, 'original') is None
    loop.active_version = 'H0001'
    (loop.root/'decisions/R0001.json').unlink()
    with pytest.raises(ValueError, match='host decision'):
        versions.publish(loop, 'original')
    assert not versions.entries()


def test_task_prompt_follows_version_selection(tmp_path):
    versions, loop = publish_fixture(tmp_path)
    versions.publish(loop, 'original')
    prompts = []
    def ask(text):
        prompts.append(text)
        return '1' if len(prompts) == 1 else ''
    args = build_parser().parse_args(['single', '--workspace', str(tmp_path/'work'),
        '--check', 'true', '--store', str(versions.root)])
    with pytest.raises(ValueError, match='empty'):
        run_single(args, ask=ask)
    assert 'Harness: 1 Original / 2 Promoted' in prompts[0]
    assert prompts[1] == 'Enter a coding task:\n> '


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Explicit Docker acceptance')
def test_single_real_docker_loop_private_verifier_and_promotion(tmp_path, monkeypatch):
    import docker
    from mini_swe_rsi import broker as broker_module
    from mini_swe_rsi.context_budget import ContextBudget
    from tests.test_online_docker import OnlineWorkflowModel

    class Model(OnlineWorkflowModel):
        def response(self, system, text):
            if self._role(system) == 'orchestrator' and self._round(text) is None:
                if 'DOCKER_GENERATION_H1' in text:
                    return self._action("printf 'fixed\\n' > answer.txt; echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
                return self._action('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
            return super().response(system, text).replace('"task_id":"t1"', '"task_id":"single-task"')

    endpoint = Model()
    original_broker = broker_module.Broker
    def identity(self):
        self.model = 'fixture-model'
        self.service_identity = {'endpoint': 'deterministic-fixture'}
        return {'model': 'fixture-model'}
    monkeypatch.setattr(original_broker, 'identity', identity)
    monkeypatch.setattr(broker_module, 'secrets', lambda: {
        'OPENAI_BASE_URL': endpoint.url, 'OPENAI_API_KEY': 'fixture-only-key', 'LLM_MODEL_NAME': 'fixture-model'})
    class TestBroker(original_broker):
        def __init__(self, root):
            super().__init__(root, context=ContextBudget({'context_window': 65536, 'max_output_tokens': 4096}))
    monkeypatch.setattr(broker_module, 'Broker', TestBroker)
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    (workspace/'answer.txt').write_text('wrong\n')
    verifier = workspace/'private-check.py'
    verifier.write_text("from pathlib import Path\nassert Path('answer.txt').read_text() == 'fixed\\n'\n")
    args = build_parser().parse_args(['single', '--workspace', str(workspace),
        '--check', 'python /verification/verify', '--verify-file', str(verifier),
        '--store', str(tmp_path/'store'), '--run', str(tmp_path/'run'),
        '--version', 'original', '--prompt', 'Write fixed into answer.txt'])
    try:
        result = run_single(args)
        assert result['status'] == 'completed' and result['active_version'] == 'H0001'
        assert (workspace/'answer.txt').read_text() == 'wrong\n'
        assert not (args.run/'private/build/workspace/private-check.py').exists()
        assert read(args.run/'online_report.json')['evaluation']['resolved_tasks'] == 0
        versions = Versions(args.store)
        selected, name = versions.select('V000001')
        assert tree_hash(selected) == tree_hash(args.run/'versions/H0001/agent_code')
        assert name == 'V000001'
        # Verification must not accept an unchanged task, invalid patch, or a timeout.
        from mini_swe_rsi.single import SingleDriver
        client = docker.from_env()
        driver = object.__new__(SingleDriver)
        driver.client, driver.running = client, []
        driver.task_image = client.images.get(read(args.run/'image_inventory.json')['single-task']['image_id'])
        driver.check, driver.check_timeout, driver.verify_file = args.check, 30, verifier
        assert driver.score('single-task', '', tmp_path/'fail-check')['resolved'] is False
        assert driver.score('single-task', 'invalid patch\n', tmp_path/'bad-patch')['resolved'] is False
        driver.check, driver.check_timeout = 'sleep 10', 1
        assert driver.score('single-task', '', tmp_path/'timeout')['timed_out'] is True
        client.close()
    finally:
        endpoint.close()


def test_single_requires_task_pass_even_when_mechanism_improves(tmp_path):
    import shutil
    from mini_swe_rsi.core import execution_hash
    parent = tmp_path/'parent'
    candidate = tmp_path/'candidate'
    initial_code(parent)
    shutil.copytree(parent, candidate)
    (candidate/'minisweagent/improvement.py').write_text('VALUE = 1\n')
    loop = object.__new__(OnlineLoop)
    loop.contract = {'check_policy': {'require_task_pass': True}}
    snapshot = {'parent_hash': tree_hash(parent), 'parent_execution_hash': execution_hash(parent),
        'checked_candidate_hash': tree_hash(candidate),
        'checked_candidate_execution_hash': execution_hash(candidate),
        'check': {'score': 0, 'mechanism_check': {'parent_failed': True, 'candidate_passed': True}}}
    args = (candidate, {'record': {'score': 0}}, snapshot,
            {'criterion': 'mechanism_parent_failed_candidate_passed'})
    assert loop._acceptance(*args)['passed'] is False
    snapshot['check']['score'] = 1
    assert loop._acceptance(*args)['passed'] is True


def test_version_defaults_and_framework_compatibility(tmp_path):
    versions, loop = publish_fixture(tmp_path)
    assert versions.select() == (None, 'original')
    versions.publish(loop, 'original')
    answers = iter(['2', ''])
    assert versions.select(ask=lambda _: next(answers), show=lambda _: None)[1] == 'V000001'
    metadata = versions.root/'versions/V000001/metadata.json'
    value = read(metadata)
    value['control_manifest'] = {'old': True}
    write(metadata, value)
    with pytest.raises(ValueError, match='different framework'):
        versions.select('V000001')
