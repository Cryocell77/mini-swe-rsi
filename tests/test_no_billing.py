"""The actual generated Harness contains no monetary implementation or placeholders."""
import json
import os
import subprocess
import sys

import pytest

from mini_swe_rsi.core import ROOT, append_event, write
from mini_swe_rsi.report import build_online_report
from mini_swe_rsi.online import OnlineLoop


@pytest.mark.parametrize('malformed', [False, True])
def test_generated_mini_query_has_no_billing_and_retains_evidence(tmp_path, malformed):
    from mini_swe_rsi.driver import initial_code

    seed = tmp_path/'seed'
    initial_code(seed)
    # Import the actual generated Harness in a separate process.
    script = '''
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import litellm
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import FormatError, LimitsExceeded
def unexpected_price(*args, **kwargs):
    raise AssertionError('Monetary lookup must never be invoked')
litellm.cost_calculator.completion_cost = unexpected_price
model = LitellmTextbasedModel(model_name='openai/unpriced-fixture')
assert not hasattr(model, '_calculate_cost')
assert 'cost_tracking' not in type(model.config).model_fields
malformed = sys.argv[2] == 'True'
response = litellm.ModelResponse(
    choices=[{'message': {'role': 'assistant', 'content': 'missing action' if malformed else
                         'candidate customization\\n```mswea_bash_command\\necho hello\\n```'},
              'finish_reason': 'stop'}],
    usage={'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7})
model._query = lambda *args, **kwargs: response
assert not hasattr(GLOBAL_MODEL_STATS, 'cost')
assert not hasattr(GLOBAL_MODEL_STATS, 'cost_limit')
if malformed:
    try:
        model.query([{'role':'user', 'content':'task'}])
    except FormatError as exc:
        extra = exc.messages[0]['extra']
    else:
        raise AssertionError('Missing action must remain a format error')
else:
    agent = DefaultAgent(model, SimpleNamespace(get_template_vars=lambda: {}, serialize=lambda: {}), system_template='sys', instance_template='task',
                         step_limit=2)
    assert not hasattr(agent, 'cost')
    assert 'cost_limit' not in type(agent.config).model_fields
    assert 'model_cost' not in agent.get_template_vars()
    assert 'instance_cost' not in agent.serialize()['info']['model_stats']
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
assert 'cost' not in extra
assert extra['response']['usage']['total_tokens'] == 7
assert GLOBAL_MODEL_STATS.n_calls == (1 if malformed else 2)
'''
    result = subprocess.run([sys.executable, '-c', script, str(seed), str(malformed)],
                            env={**os.environ, 'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
                                 'MSWEA_GLOBAL_COST_LIMIT': 'obsolete-invalid',
                                 'MSWEA_COST_TRACKING': 'obsolete-invalid'},
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
