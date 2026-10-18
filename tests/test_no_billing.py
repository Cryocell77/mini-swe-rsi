"""The retained upstream ABI must never re-enable RSI monetary accounting."""
import json
import os
import subprocess
import sys

import pytest

from mini_swe_rsi.core import ROOT, append_event, write
from mini_swe_rsi.report import build_online_report
from mini_swe_rsi.online import OnlineLoop


@pytest.mark.parametrize('malformed', [False, True])
def test_actual_mini_query_bypasses_price_lookup_and_retains_evidence(malformed):
    # Isolate imports of the pinned, editable mini source from host test imports.
    script = '''
import runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])/'.upstream/mini/src'))
import litellm
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import FormatError, LimitsExceeded
adapt = runpy.run_path(str(Path(sys.argv[1])/'runtime/bridge.py'))['disable_model_billing']
def unexpected_price(*args, **kwargs):
    raise AssertionError('Monetary lookup must never be invoked')
litellm.cost_calculator.completion_cost = unexpected_price
model = adapt(LitellmTextbasedModel(model_name='openai/unpriced-fixture'))
malformed = sys.argv[2] == 'True'
response = litellm.ModelResponse(
    choices=[{'message': {'role': 'assistant', 'content': 'missing action' if malformed else
                         'candidate customization\\n```mswea_bash_command\\necho hello\\n```'},
              'finish_reason': 'stop'}],
    usage={'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7})
model._query = lambda *args, **kwargs: response
GLOBAL_MODEL_STATS.cost_limit = 0.000001
if malformed:
    try:
        model.query([{'role':'user', 'content':'task'}])
    except FormatError as exc:
        extra = exc.messages[0]['extra']
    else:
        raise AssertionError('Missing action must remain a format error')
else:
    agent = DefaultAgent(model, object(), system_template='sys', instance_template='task',
                         cost_limit=0, step_limit=2)
    # A stale monetary accumulator cannot stop the online agent; iteration still can.
    agent.cost = 1000000
    first = agent.query()
    assert first['extra']['actions'] == [{'command': 'echo hello'}]
    assert first['content'].startswith('candidate customization')
    extra = agent.query()['extra']
    try:
        agent.query()
    except LimitsExceeded:
        pass
    else:
        raise AssertionError('Iteration limit was lost')
assert extra['cost'] == 0.0  # Required upstream ABI, never a free-service claim.
assert extra['response']['usage']['total_tokens'] == 7
assert GLOBAL_MODEL_STATS.cost == 0.0
'''
    result = subprocess.run([sys.executable, '-c', script, str(ROOT), str(malformed)],
                            env={**os.environ, 'LITELLM_LOCAL_MODEL_COST_MAP': 'True'},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_online_report_tracks_tokens_without_monetary_fields(tmp_path):
    write(tmp_path/'configuration.json', {'protocol': OnlineLoop.protocol})
    write(tmp_path/'online_state.json', {'protocol': OnlineLoop.protocol, 'active_version': 'H0000'})
    append_event(tmp_path, {'kind': 'model_call', 'label': 'task:tasks/t1',
                           'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}})
    report = build_online_report(tmp_path)
    assert report['total_model_calls'] == 1 and report['total_tokens'] == 7
    assert report['token_accounting_complete']
    for key in ('cost_usd', 'cost_estimate', 'cost_threshold_exceeded', 'billing_mode'):
        assert key not in json.dumps(report)
