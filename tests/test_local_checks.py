"""Regressions for the real step-5 looping stub and oversized observations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mini_swe_rsi.budget_policy import sica_budget
from mini_swe_rsi.core import ROOT, write
from mini_swe_rsi.tool_output import capture_stage_output


LIMITS = sica_budget(128000, 1000000)


class LocalTools:
    def __init__(self, directory):
        self.output_dir = directory
        self.closed = False

    def close(self):
        self.closed = True


def capture(tmp_path, code, limits=LIMITS):
    tools = LocalTools(tmp_path/'tool_outputs')
    result = capture_stage_output([sys.executable, '-c', code], 10, tools, limits)
    data = (tmp_path/result['output_artifact']['artifact']).read_bytes()
    assert result['output_artifact']['sha256'] == hashlib.sha256(data).hexdigest()
    return tools, result, data


def test_small_output_is_unchanged_including_stderr_and_exit_status(tmp_path):
    tools, result, data = capture(tmp_path, "import os; os.write(1, '你好\\n'.encode()); os.write(2,b'error\\n'); raise SystemExit(7)")
    assert result['returncode'] == 7 and not result['exception_info']
    assert result['output'] == data.decode() == '你好\nerror\n'
    assert result['output_artifact']['complete'] and result['output_artifact']['inlined']
    assert not tools.closed


def test_large_output_is_complete_on_disk_and_explicitly_indexed(tmp_path):
    tools, result, data = capture(tmp_path, "import os; os.write(1,b'HEAD'+b'x'*400000+b'TAIL')")
    assert data == b'HEAD'+b'x'*400000+b'TAIL'
    assert result['returncode'] == 0 and result['exception_info'] == ''
    assert result['output_artifact']['complete'] and not result['output_artifact']['inlined']
    assert len(result['output']) < 10000
    assert 'COMPLETE ARTIFACT' in result['output'] and 'HEAD' in result['output'] and 'TAIL' in result['output']
    assert not tools.closed


def test_runaway_output_kills_sandbox_and_marks_artifact_as_prefix(tmp_path):
    tools, result, data = capture(tmp_path, "import os\nwhile True: os.write(1,b'x'*65536)")
    assert tools.closed
    assert result['returncode'] == -1 and result['exception_info'] == 'ToolOutputLimitExceeded'
    assert len(data) == LIMITS['stage_output_max_bytes']
    assert not result['output_artifact']['complete']
    assert result['output_artifact']['observed_bytes'] > len(data)
    assert len(result['output']) < 10000 and 'INCOMPLETE PREFIX' in result['output']


def test_existing_command_timeout_destroys_sandbox_without_fake_success(tmp_path):
    tools = LocalTools(tmp_path/'tool_outputs')
    result = capture_stage_output([sys.executable, '-c', 'import time; time.sleep(20)'], .01, tools, LIMITS)
    assert tools.closed and result['exception_info'] == 'CommandTimeout'
    assert result['returncode'] == -1 and not result['output_artifact']['complete']


# Same failure pattern as the observed generated script: one FormatError, then
# zero-cost valid actions forever. `fixed` adds a genuine protocol termination.
STUB_SCRIPT = '''
from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import FormatError, InterruptAgentFlow
import sys
class Model:
    def __init__(self): self.n = 0
    def query(self, messages):
        self.n += 1
        if self.n == 1:
            raise FormatError({'role':'user', 'content':'missing action', 'extra':{'cost':0}})
        if sys.argv[1] == 'fixed' and self.n == 3:
            raise InterruptAgentFlow({'role':'exit', 'content':'done', 'extra':{'exit_status':'Submitted'}})
        return {'role':'assistant', 'content':'echo hello', 'extra':{'actions':[{'command':'echo hello'}], 'cost':0}}
    def format_message(self, **kwargs): return kwargs
    def format_observation_messages(self, message, outputs, template_vars):
        return [{'role':'user', 'content':'hello'}]
    def get_template_vars(self): return {}
    def serialize(self): return {}
class Env:
    def execute(self, action):
        print('[env.execute] action=', action, flush=True)
        return {'returncode':0, 'output':'hello'}
    def get_template_vars(self): return {}
    def serialize(self): return {}
fixed = sys.argv[1] == 'fixed'
agent = DefaultAgent(Model(), Env(), system_template='sys', instance_template='task', step_limit=4 if fixed else 0)
if sys.argv[1] == 'bounded': agent.config.step_limit = 4
result = agent.run('test')
print('RESULT', result['exit_status'], 'CALLS', agent.model.n)
'''


@pytest.mark.parametrize('mode,expected', [('bounded','RESULT LimitsExceeded CALLS 4'), ('fixed','RESULT Submitted CALLS 3')])
def test_original_stub_needs_explicit_termination_and_fixed_harness_finishes(tmp_path, mode, expected):
    script = tmp_path/'test_stub.py'; script.write_text(STUB_SCRIPT)
    result = subprocess.run([sys.executable, str(script), mode],
                            env={**os.environ, 'PYTHONPATH':str(ROOT/'.upstream/mini/src')},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert result.stdout.count('[env.execute]') == (1 if mode == 'fixed' else 3)


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Real runaway sandbox isolation')
def test_real_loop_stops_before_oom_and_next_tool_can_read_saved_prefix(tmp_path, monkeypatch):
    import docker
    from mini_swe_rsi.broker import Broker
    from mini_swe_rsi.context_budget import ContextBudget
    from mini_swe_rsi.core import Budget
    from mini_swe_rsi.stage_tools import StageTools

    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda:dict(
        OPENAI_BASE_URL='https://fixture/v1', OPENAI_API_KEY='fixture', LOCAL_LLM_MODEL='fixture'))
    client = docker.from_env()
    driver = SimpleNamespace(client=client, image=client.images.get('mini-swe-rsi-runtime:0.1'), running=[])
    candidate = tmp_path/'candidate'; candidate.mkdir()
    shutil.copytree(ROOT/'.upstream/mini/src/minisweagent', candidate/'minisweagent')
    (candidate/'loop.py').write_text(STUB_SCRIPT)
    source_hash = hashlib.sha256((candidate/'loop.py').read_bytes()).hexdigest()
    logs = tmp_path/'logs'
    write(logs/'workflow_state.json', {'active':{'index':4}, 'completed_steps':4, 'status':'improving'})
    budget = Budget(tmp_path); budget.enter('preflight')
    broker = Broker(tmp_path, budget, ContextBudget({'context_window':1000000,'max_output_tokens':128000}))
    tools = StageTools(driver, logs, candidate, None, tmp_path/'tool_outputs')
    token = broker.session('local-check', solver='fixture', seconds=None)
    broker.sessions[token]['cost_limit_usd'] = None  # Improvement tools, not a benchmark task.
    broker.sessions[token]['stage_tools'] = tools
    try:
        result = broker.tool(token, {'command':'python loop.py runaway'})
        assert result['exception_info'] == 'ToolOutputLimitExceeded'
        assert tools.container is None and not driver.running
        assert len(result['output']) < 10000
        meta = result['output_artifact']
        assert not meta['complete'] and meta['stored_bytes'] == LIMITS['stage_output_max_bytes']
        artifact = tmp_path/meta['artifact']
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == meta['sha256']
        # Fresh sandbox: saved prefix is visible read-only, no parent logs or credentials.
        result = broker.tool(token, {'command':f"wc -c {meta['sandbox_path']} && test ! -e /logs && test -z \"$RSI_TOKEN\""})
        assert result['returncode'] == 0 and str(meta['stored_bytes']) in result['output']
        denied = broker.tool(token, {'command':f"printf bad > {meta['sandbox_path']}"})
        assert denied['returncode'] != 0
        fixed = broker.tool(token, {'command':'python loop.py fixed'})
        assert fixed['returncode'] == 0 and 'RESULT Submitted CALLS 3' in fixed['output']
        tools.container.reload()
        assert not tools.container.attrs['State']['OOMKilled']
        assert hashlib.sha256((candidate/'loop.py').read_bytes()).hexdigest() == source_hash
        events = [json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()]
        assert events[0]['output_artifact'] == meta
        assert (tmp_path/'events.jsonl').stat().st_size < 30000
        assert broker.sessions[token]['calls'] == 0
    finally:
        tools.close(); broker.close(); client.close()
