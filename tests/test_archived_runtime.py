"""Real Docker boundary and two-version loading, using a fake inference endpoint only."""
import json
import os
import time

import pytest
import yaml

from mini_swe_rsi.broker import Broker
from mini_swe_rsi.core import Budget, ROOT, read
from mini_swe_rsi.driver import Driver, initial_code


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Explicit Docker integration test')
def test_two_archived_agents_load_and_never_mount_private_data(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                     OPENAI_API_KEY='private-real-key', LOCAL_LLM_MODEL='fixture'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixture'
    seen = []
    def complete(token, payload):
        seen.append(payload['messages'])
        return {'id':'fixture', 'created':int(time.time()), 'object':'chat.completion', 'model':'fixture',
                'choices':[{'index':0, 'finish_reason':'stop', 'message':{'role':'assistant',
                'content':'THOUGHT: Done.\n```mswea_bash_command\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n```'}}],
                'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
    broker.complete = complete
    broker.tool = lambda token, action: {'output':'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n', 'returncode':0,'exception_info':''}
    driver = Driver(tmp_path, None, broker, budget)
    try:
        archive = tmp_path/'archive'
        for i in range(2):
            code = archive/f'agent_{i}/agent_code'
            initial_code(code)
            if i:
                config = code/'minisweagent/config/mini_textbased.yaml'
                data = yaml.safe_load(config.read_text())
                data['agent']['system_template'] += '\nARCHIVED_VERSION_ONE\n'
                config.write_text(yaml.safe_dump(data))
            # Actual container checks filtered evidence and read-only selected source.
            (code/'__main__.py').write_text(
                "from pathlib import Path\n"
                "archive = Path('/home/agent/archive')\n"
                "assert not list(archive.rglob('host_result.json'))\n"
                "assert not list(archive.rglob('trajectory.json'))\n"
                "assert not list(archive.rglob('results.pre_patch_fix.jsonl'))\n"
                "try:\n    Path(__file__).write_text('forbidden')\n"
                "except OSError:\n    pass\n"
                "else:\n    raise AssertionError('archive source writable')\n"
                "from bridge import main\nmain(__file__)\n")
            hidden = archive/f'agent_{i}/benchmarks/swe_lite_dev/traces/fixture'
            hidden.mkdir(parents=True)
            (hidden/'host_result.json').write_text('private')
            (hidden/'trajectory.json').write_text('credential')
            (hidden.parent.parent/'results.pre_patch_fix.jsonl').write_text('obsolete')
            prompt = tmp_path/'prompt.txt'
            prompt.write_text('Finish the fixture task.')
            token = broker.session('fixture')
            status, output = driver.controller(code, tmp_path/f'logs_{i}',
                      ['benchmark','--prompt-file','/task.txt'], token, archive=archive, prompt=prompt)
            broker.finish(token)
            assert status == 0, output
            assert read(tmp_path/f'logs_{i}/result.json')['exit_status'] == 'Submitted', output
            assert f'agent_{i}/agent_code/minisweagent/agents/default.py' in read(tmp_path/f'logs_{i}/activation.json')['agent_file']
        assert 'ARCHIVED_VERSION_ONE' not in seen[0][0]['content']
        assert 'ARCHIVED_VERSION_ONE' in seen[1][0]['content']
        events = [json.loads(x) for x in (tmp_path/'events.jsonl').read_text().splitlines()]
        for e in events:
            if e['kind']=='agent_launch':
                assert all(not m['read_write'] for m in e['mounts'] if m['destination'] in ['/runtime','/upstream','/home/agent/archive'])
                assert not any('.env' in m['destination'] or '/private' in m['destination'] for m in e['mounts'])
    finally:
        driver.close()
        broker.close()


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Explicit Docker integration test')
def test_controller_deadline_stops_unresponsive_archived_process(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                     OPENAI_API_KEY='private-real-key', LOCAL_LLM_MODEL='fixture'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixture'
    driver = Driver(tmp_path, None, broker, budget)
    try:
        code = tmp_path/'agent_code'
        initial_code(code)
        # A changed archived entrypoint cannot bypass the host's independent deadline.
        (code/'__main__.py').write_text('import time\ntime.sleep(100)\n')
        token = broker.session('unresponsive-fixture', seconds=1)
        start = time.monotonic()
        status, _ = driver.controller(code, tmp_path/'logs', ['improve'], token, target=tmp_path/'target')
        broker.finish(token)
        assert status == 137
        assert time.monotonic()-start < 5
        assert read(tmp_path/'logs/result.json')['exit_status'] == 'TimeExceeded'
    finally:
        driver.close()
        broker.close()


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Explicit Docker integration test')
def test_real_bridge_preserves_failure_artifacts_and_exits_nonzero(tmp_path, monkeypatch):
    from mini_swe_rsi.broker import ModelError
    from mini_swe_rsi.core import tree_hash
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                     OPENAI_API_KEY='private-real-key', LOCAL_LLM_MODEL='fixture'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixture'
    def fail(token, payload):
        raise ModelError('ContextLimitExceeded')
    broker.complete = fail
    driver = Driver(tmp_path, None, broker, budget)
    try:
        code = tmp_path/'archive/agent_0/agent_code'
        target = tmp_path/'target'
        initial_code(code)
        initial_code(target)
        frozen = tree_hash(target)
        token = broker.session('bridge-failure')
        status, output = driver.controller(code, tmp_path/'logs', ['improve'], token,
                                          archive=tmp_path/'archive', target=target)
        broker.finish(token)
        assert status != 0, output
        assert read(tmp_path/'logs/result.json')['exit_status'] == 'ContextWindowExceededError'
        assert (tmp_path/'logs/trajectory.json').is_file()
        assert (tmp_path/'logs/trace.txt').is_file()
        assert tree_hash(target) == frozen
    finally:
        driver.close()
        broker.close()
