"""A real archived agent trace must replay identically unless effective behavior changes."""
import io
import json
import os
import subprocess
import sys
import time

import pytest

from mini_swe_rsi.broker import Broker
from mini_swe_rsi.core import Budget, read, write
from mini_swe_rsi.driver import Driver, initial_code


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='Explicit Docker integration test')
def test_replay_detects_late_context_change_but_rejects_unused_source_change(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda: dict(OPENAI_BASE_URL='https://fixture/v1',
                     OPENAI_API_KEY='private-real-key', LOCAL_LLM_MODEL='fixture'))
    budget = Budget(tmp_path)
    budget.enter('preflight')
    broker = Broker(tmp_path, budget)
    broker.model = 'fixture'
    driver = Driver(tmp_path, None, broker, budget)
    solver = driver.client.containers.run(driver.image.id,
        ['bash','-lc','mkdir -p /testbed; sleep infinity'], detach=True, network_disabled=True)
    try:
        task = tmp_path/'prompt.txt'
        task.write_text('Finish the fixture task.')
        codes = [tmp_path/f'version_{i}/agent_code' for i in range(4)]
        for i, code in enumerate(codes):
            initial_code(code)
            source = code/'minisweagent/agents/default.py'
            if i in (1, 3):
                source.write_text(source.read_text().replace('self.model.query(self.messages)',
                    'self.model.query(self.messages[-2:] if self.n_calls > 1 else self.messages)'))
            if i in (2, 3):
                with source.open('a') as f:
                    f.write('\nUNUSED_TEST_CONSTANT = 1\n')

            class RecordedEndpoint:
                count = 0
                def open(self, request, **kwargs):
                    commands = ["printf 'hello\\n'", 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT']
                    command = commands[self.count]
                    self.count += 1
                    value = {'id':'fixture', 'created':int(time.time()), 'object':'chat.completion', 'model':'fixture',
                        'choices':[{'index':0, 'finish_reason':'stop', 'message':{'role':'assistant',
                        'content':f'THOUGHT: Fixture.\n```mswea_bash_command\n{command}\n```'}}],
                        'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
                    return io.BytesIO(json.dumps(value).encode())
            broker.opener = RecordedEndpoint()
            token = broker.session(f'fixture:{i}', solver=solver.id)
            logs = tmp_path/f'logs_{i}'
            status, output = driver.controller(code, logs, ['benchmark','--prompt-file','/task.txt'], token, prompt=task)
            broker.finish(token)
            assert status == 0, output
            assert read(logs/'result.json')['exit_status'] == 'Submitted', output
            events = [json.loads(s) for s in (tmp_path/'events.jsonl').read_text().splitlines()]
            observed = [e for e in events if e.get('label') == f'fixture:{i}']
            job = tmp_path/f'replay_{i}.json'
            result = tmp_path/f'replay_{i}_result.json'
            write(job, {'h0_code':str(codes[0]), 'task':task.read_text(), 'model':'fixture',
                'platform':read(logs/'activation.json')['platform'], 'events':observed})
            subprocess.run([sys.executable,'-m','mini_swe_rsi.replay',str(job),str(result)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
            proof = read(result)
            assert proof['changed'] == (i in (1, 3)), proof
            assert proof['live_model_calls'] == 0
            if i in (1, 3):
                assert proof['witness_call'] == 2
            else:
                assert proof['complete_prefix'], proof
            sandbox_job = read(job)
            sandbox_job.pop('h0_code')
            calibrated = driver.replay(code, sandbox_job, tmp_path/f'calibration_{i}')
            assert not calibrated['changed'] and calibrated['complete_prefix'], calibrated
            if i == 3:
                parent = driver.replay(codes[1], sandbox_job, tmp_path/'parent_replay')
                assert not parent['changed'] and parent['complete_prefix'], parent
                # H1 -> H0 reversion changes behavior relative to H1.
                control_events = [e for e in events if e.get('label') == 'fixture:0']
                reverted = driver.replay(codes[1], {**sandbox_job,'events':control_events}, tmp_path/'reversion_replay')
                assert reverted['changed'] and reverted['witness_call'] == 2, reverted
    finally:
        solver.remove(force=True)
        driver.close()
        broker.close()
