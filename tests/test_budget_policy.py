"""Scope and transport regressions, using simulated endpoints only."""
import io
import json
import time

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.context_budget import ContextBudget
from mini_swe_rsi.core import secrets
from tests.event_helpers import broker_events


CAPS = dict(id='deepseek-flash', context_window=1048576, max_output_tokens=393216,
            owned_by='deepseek', api_capabilities={})


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda:dict(
        OPENAI_BASE_URL='https://api.deepseek.com', OPENAI_API_KEY='private-fixture', LOCAL_LLM_MODEL='deepseek-flash'))
    instance = Broker(tmp_path)
    instance.remote = lambda path: {'data':[CAPS]}
    instance.identity()
    yield instance
    instance.close()


def reply(text='done', finish='stop', reasoning='', usage=None):
    return {'choices':[{'index':0, 'finish_reason':finish, 'message':{
        'role':'assistant', 'content':text, 'reasoning_content':reasoning}}],
        'usage':usage or {'prompt_tokens':5,'completion_tokens':2,'total_tokens':7,'prompt_cache_hit_tokens':3}}


def endpoint(broker, responses):
    requests = []
    def open(request, **kwargs):
        requests.append((request.full_url,json.loads(request.data),kwargs))
        return io.BytesIO(json.dumps(next(responses)).encode())
    broker.opener = type('Endpoint',(),{'open':staticmethod(open)})()
    return requests


def test_alias_and_conflicting_model_names(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.core.ROOT', tmp_path)
    (tmp_path/'.env').write_text('OPENAI_BASE_URL=https://fixture\nOPENAI_API_KEY=fixture\nLLM_MODEL_NAME=deepseek-flash\n')
    assert secrets()['LOCAL_LLM_MODEL'] == 'deepseek-flash'
    with (tmp_path/'.env').open('a') as file:
        file.write('LOCAL_LLM_MODEL=different\n')
    with pytest.raises(ValueError, match='Conflicting model names'):
        secrets()


def test_remote_identity_never_uses_qwen_tokenizer(broker):
    assert broker.context.tokenizer is None
    assert broker.context.contract['total_tokens'] == 1048576
    assert broker.budget_limits()['model_max_output_tokens'] == 393216
    assert broker.identity()['weights_verifiable'] is False
    for record in (broker.budget_limits(), broker.service_identity):
        assert not any('cost' in key or 'billing' in key or 'pricing' in key for key in record)
    broker.remote = lambda path:{'data':[{'id':'deepseek-flash'}]}
    with pytest.raises(ModelError,match='advertise valid'):
        broker.identity()


@pytest.fixture
def ollama_broker(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda:dict(
        OPENAI_BASE_URL='http://fixture/v1', OPENAI_API_KEY='private-fixture', LOCAL_LLM_MODEL='qwen35-4b-agent'))
    instance = Broker(tmp_path)
    data = {'/models':{'data':[{'id':'qwen35-4b-agent:latest'}]},
            '/api/version':{'version':'fixture'},
            '/api/tags':{'models':[{'name':'qwen35-4b-agent:latest','digest':'a'*64}]}}
    shown = {'parameters':'num_ctx 65536\n', 'template':'fixture-template',
             'model_info':{'general.architecture':'qwen35','qwen35.context_length':262144}}
    instance.remote = lambda path:data[path]
    instance.opener = type('Endpoint',(),{'open':staticmethod(lambda *args,**kwargs:io.BytesIO(json.dumps(shown).encode()))})()
    # Native tokenizer authenticity is exercised separately; this capacity fixture
    # supplies the same verified metadata without shipping a second vocabulary.
    instance.service_configuration = lambda model, identity=None: {
        'parameters':shown['parameters'],
        'template_sha256':__import__('hashlib').sha256(shown['template'].encode()).hexdigest()}
    yield instance, data, shown
    instance.close()


def test_ollama_alias_capabilities_and_token_usage(ollama_broker):
    broker, data, shown = ollama_broker
    identity = broker.identity()
    assert identity['requested']=='qwen35-4b-agent' and identity['model']=='qwen35-4b-agent:latest'
    assert identity['weights']=='a'*64 and identity['weights_verifiable']
    assert broker.context.contract['total_tokens']==65536
    assert broker.budget_limits()['model_max_output_tokens']==65535
    assert broker.service_identity['ollama']['num_predict']==-1
    token = broker.session('task:fixture',solver='fixture')
    requests = endpoint(broker,iter([reply()]))
    broker.complete(token,{'messages':[{'role':'user','content':'task'}]})
    assert requests[0][1]['model']=='qwen35-4b-agent:latest'
    assert requests[0][1]['max_tokens']==65535
    assert broker.finish(token)['tokens'] == 7


def test_ollama_finite_output_and_invalid_or_cloud_metadata(ollama_broker):
    broker, data, shown = ollama_broker
    shown['parameters'] += 'num_predict 8192\n'
    assert broker.identity()['max_tokens']==8192
    frozen = dict(broker.service_identity)
    shown['parameters']='num_ctx 32768\nnum_predict 8192\n'
    broker.identity()
    assert broker.service_identity!=frozen
    shown['parameters']='num_predict 8192\n'
    with pytest.raises(ModelError,match='explicit valid num_ctx'):
        broker.identity()
    shown['parameters']='num_ctx 999999\n'
    with pytest.raises(ModelError,match='explicit valid num_ctx'):
        broker.identity()
    shown['parameters']='num_ctx 65536\n'
    shown['remote_host']='https://cloud.invalid'
    with pytest.raises(ModelError,match='cloud model'):
        broker.identity()


def test_ollama_missing_digest_or_model_is_not_guessed(ollama_broker):
    broker, data, shown = ollama_broker
    data['/api/tags']['models'][0]['digest']=''
    with pytest.raises(ModelError,match='Unverified Ollama'):
        broker.identity()
    data['/models']['data']=[{'id':'some-other-model:latest'}]
    with pytest.raises(ModelError,match='not advertised'):
        broker.identity()


def test_independent_iterations_and_improvement_has_no_extra_deadline(broker):
    token = broker.session('proposal', seconds=None)
    requests = endpoint(broker, iter([reply(),reply(),reply()]))
    session = broker.sessions[token]
    assert session['max_calls'] is None
    assert broker.status(token)['remaining_seconds'] is None
    session['iterations']['main'] = 499
    broker.complete(token,{'messages':[]})
    with pytest.raises(ModelError,match='LimitsExceeded'):
        broker.complete(token,{'messages':[]})
    session['iterations']['001_software_developer'] = 999
    broker.complete(token,{'messages':[]},'001_software_developer','software_developer')
    with pytest.raises(ModelError,match='LimitsExceeded'):
        broker.complete(token,{'messages':[]},'001_software_developer','software_developer')
    broker.complete(token,{'messages':[]},'002_reviewer','reviewer')
    assert len(requests)==3 and session['calls']==3
    assert session['iterations']['main']==500 and session['iterations']['001_software_developer']==1000


def test_prefix_continuation_is_one_iteration_and_aggregates_usage(broker):
    token=broker.session('proposal', seconds=None)
    requests=endpoint(broker,iter([reply('```mswea_bash_command\necho ', 'length'), reply('hello\n```')]))
    result=broker.complete(token,{'messages':[{'role':'user','content':'task'}], 'max_tokens':2048})
    assert len(requests)==2 and all(p[1]['max_tokens']==393216 for p in requests)
    assert requests[0][0].endswith('/chat/completions')
    assert requests[1][0]=='https://api.deepseek.com/beta/chat/completions'
    assert requests[1][1]['messages'][-1]['prefix'] is True
    assert requests[1][1]['messages'][-1]['content']=='```mswea_bash_command\necho '
    assert result['choices'][0]['message']['content']=='```mswea_bash_command\necho hello\n```'
    assert result['usage']['total_tokens']==14
    assert result['usage']['prompt_cache_hit_tokens']==6
    metrics=broker.finish(token)
    assert metrics['calls']==2 and metrics['iterations']=={'main':1} and metrics['cached_tokens']==6
    model_events = [e for e in broker_events(broker) if e.get('kind') == 'model_call']
    assert [e['logical_completion_final'] for e in model_events]==[False,True]


def test_five_total_requests_never_returns_incomplete_action(broker):
    token=broker.session('proposal',seconds=None)
    requests=endpoint(broker,iter([reply('partial', 'length')]*6))
    with pytest.raises(ModelError,match='ContinuationLimitExceeded'):
        broker.complete(token,{'messages':[]})
    assert len(requests)==5 and broker.sessions[token]['calls']==5
    assert not any('agent_response' in e for e in broker_events(broker))


def test_no_fake_continuation_on_unsupported_provider(broker):
    broker.prefix_protocol=None
    token=broker.session('proposal',seconds=None)
    requests=endpoint(broker,iter([reply('partial','length'),reply('regenerated')]))
    with pytest.raises(ModelError,match='prefix continuation unavailable'):
        broker.complete(token,{'messages':[]})
    assert len(requests)==1


def test_no_old_context_cap_and_usage_is_authoritative(broker):
    token=broker.session('proposal')
    requests=endpoint(broker,iter([reply(usage={'total_tokens':40002,'prompt_tokens':40000,'completion_tokens':2})]))
    broker.complete(token,{'messages':[{'role':'user','content':'x '*40000}]})
    assert len(requests)==1
    assert broker_events(broker)[-1]['host_prompt_tokens_estimate'] is None
    token=broker.session('over-capacity')
    endpoint(broker,iter([reply(usage={'total_tokens':1048578,'prompt_tokens':1048576,'completion_tokens':2})]))
    with pytest.raises(ModelError,match='ContextLimitExceeded'):
        broker.complete(token,{'messages':[]})
    assert broker.finish(token)['tokens']==1048578


def test_tool_scope_output_and_benchmark_token_accounting(broker, monkeypatch):
    token=broker.session('benchmark',solver='fixture')
    calls=[]
    def run(args, **kwargs):
        calls.append((args,kwargs['timeout']))
        kwargs['stdout'].write(b'x'*(1024*1024+10))
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr('mini_swe_rsi.broker.subprocess.run',run)
    result=broker.tool(token,{'command':'fixture','generous_expected_duration':1800})
    assert len(result['output'])==1024*1024+10
    assert 600 < calls[0][1] <= 605
    with pytest.raises(ModelError,match='between 5 and 1800'):
        broker.tool(token,{'command':'fixture','generous_expected_duration':1})
    endpoint(broker, iter([reply()]))
    broker.complete(token, {'messages': []})
    assert broker.finish(token)['tokens'] == 7


def test_budget_limits_keep_enforced_scopes_and_remove_unused_fileview_placeholders():
    from mini_swe_rsi.budget_policy import policy_contract, sica_budget

    limits = sica_budget(393216, 1048576)
    assert set(limits) == {
        'main_iterations', 'role_iterations', 'completion_max_requests',
        'benchmark_wall_seconds', 'improvement_wall_seconds',
        'tool_timeout_seconds', 'command_min_seconds', 'command_max_seconds',
        'command_default_seconds', 'stage_output_inline_bytes',
        'stage_output_max_bytes', 'stage_output_preview_bytes',
        'model_max_output_tokens', 'context_max_tokens'}
    assert limits['main_iterations'] == 500
    assert limits['role_iterations'] == {
        'archive_explorer': 500, 'software_developer': 1000,
        'general_problem_solver': 500, 'reviewer': 500}
    assert limits['completion_max_requests'] == 5
    assert (limits['benchmark_wall_seconds'], limits['improvement_wall_seconds']) == (600, 0)
    assert (limits['tool_timeout_seconds'], limits['command_min_seconds'],
            limits['command_max_seconds'], limits['command_default_seconds']) == (600, 5, 1800, 600)
    assert (limits['stage_output_inline_bytes'], limits['stage_output_max_bytes'],
            limits['stage_output_preview_bytes']) == (300000, 8 * 1024 * 1024, 4096)
    assert (limits['model_max_output_tokens'], limits['context_max_tokens']) == (393216, 1048576)
    assert policy_contract()['version'] == 'sica-budget-scopes-v2'


def test_pinned_upstream_budget_scopes_match_policy():
    import ast
    from mini_swe_rsi.core import ROOT, read, digest
    from mini_swe_rsi.budget_policy import sica_budget

    policy = sica_budget(393216, 1048576)
    sources = ROOT/'.upstream/sica'
    for path, expected in read(ROOT/'upstream.lock.json')['sica']['budget_files'].items():
        assert digest(sources/path) == expected
    base = ast.parse((sources/'base_agent/src/agents/base_agent.py').read_text())
    iterations = next(n.value.value for n in ast.walk(base) if isinstance(n, ast.AnnAssign)
                      and isinstance(n.target, ast.Name) and n.target.id == 'MAX_ITERATIONS')
    assert iterations == policy['main_iterations'] == 500
    calls = [n for n in ast.walk(base) if isinstance(n, ast.Call)]
    continuation = next(k.value.value for n in calls for k in n.keywords if k.arg == 'max_continuations')
    assert continuation == policy['completion_max_requests'] == 5
    coder = ast.parse((ROOT/'vendor/sica_workflow/coder.py').read_text())
    iterations = next(n.value.value for n in ast.walk(coder) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'MAX_ITERATIONS' for t in n.targets))
    assert iterations == policy['role_iterations']['software_developer'] == 1000


def test_unbounded_observation_keeps_archived_harness_customization():
    import runpy
    import yaml
    from jinja2 import Template
    from mini_swe_rsi.core import ROOT

    config = yaml.safe_load((ROOT/'.upstream/mini/src/minisweagent/config/mini_textbased.yaml').read_text())
    template = config['model']['observation_template'] + '\nCANDIDATE_CUSTOMIZATION'
    adapted = runpy.run_path(str(ROOT/'runtime/bridge.py'))['unbounded_observation'](template)
    value = Template(adapted).render(output={'output':'x'*12000, 'returncode':0, 'exception_info':''})
    assert 'x'*12000 in value and 'CANDIDATE_CUSTOMIZATION' in value
    assert 'elided_chars' not in value


def test_model_total_deadline_does_not_wait_on_keepalive_transport(broker):
    import threading
    release=threading.Event()
    token=broker.session('deadline',seconds=.05)
    def blocked(*args, **kwargs):
        release.wait(2)
        return io.BytesIO(json.dumps(reply()).encode())
    broker.opener=type('Endpoint',(),{'open':staticmethod(blocked)})()
    started=time.time()
    try:
        with pytest.raises(ModelError,match='TimeExceeded'):
            broker.complete(token,{'messages':[]})
        metrics=broker.finish(token)
        assert time.time()-started < .5
        assert metrics['calls']==1 and metrics['unmetered_calls']==1
        assert metrics['terminal_reason']=='TimeExceeded'
    finally:
        release.set()


def test_bad_cache_usage_retains_valid_tokens(broker):
    token=broker.session('bad-cache')
    endpoint(broker,iter([reply(usage={'total_tokens':7,'prompt_tokens':5,'completion_tokens':2,
                                    'prompt_cache_hit_tokens':999})]))
    with pytest.raises(ModelError,match='Invalid cached token usage'):
        broker.complete(token,{'messages':[]})
    usage=broker.finish(token)
    assert usage['calls']==1 and usage['tokens']==7 and usage['unmetered_calls']==0
    assert usage['cached_tokens'] == 0


@pytest.mark.parametrize('cache_override', [
    {'prompt_cache_hit_tokens': None},
    {'prompt_tokens_details': ['invalid-cache-details']},
])
def test_explicit_invalid_cache_usage_is_not_treated_as_omitted(broker, cache_override):
    token = broker.session('invalid-cache-field')
    usage = {'total_tokens': 7, 'prompt_tokens': 5, 'completion_tokens': 2}
    usage.update(cache_override)
    endpoint(broker, iter([reply(usage=usage)]))
    with pytest.raises(ModelError, match='Invalid cached token usage'):
        broker.complete(token, {'messages': []})
    metrics = broker.finish(token)
    assert metrics['tokens'] == 7 and metrics['cached_tokens'] == 0
    assert metrics['unmetered_calls'] == 0
    assert broker_events(broker)[-1]['cache_usage_invalid'] is True


def test_error_completion_uses_common_usage_and_cache_accounting(broker):
    token = broker.session('error-cache-accounting')
    usage = {'total_tokens': 7, 'prompt_tokens': 5, 'completion_tokens': 2,
             'prompt_cache_hit_tokens': None}
    endpoint(broker, iter([{'error': {'type': 'server_error', 'message': 'private'},
                           'usage': usage}]))
    with pytest.raises(ModelError, match='Upstream completion error'):
        broker.complete(token, {'messages': []})
    metrics = broker.finish(token)
    assert metrics['tokens'] == 7 and metrics['cached_tokens'] == 0
    assert metrics['unmetered_calls'] == 0
    event = broker_events(broker)[-1]
    assert event['transport_fault'] == 'upstream_error'
    assert event['usage'] == usage and event['cache_usage_invalid'] is True
