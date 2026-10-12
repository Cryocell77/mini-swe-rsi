"""Improve-only transport regressions, including the fourth-run observed failures."""
import importlib.util
import io
import json
import runpy
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from jinja2 import Template

from mini_swe_rsi.core import ROOT, Budget, improvement_contract
from mini_swe_rsi.broker import Broker, ModelError

w = runpy.run_path(str(ROOT/'runtime/workflow.py'))
bridge = runpy.run_path(str(ROOT/'runtime/bridge.py'))


def selected():
    return yaml.safe_load((ROOT/'.upstream/mini/src/minisweagent/config/mini_textbased.yaml').read_text())


def test_all_roles_use_original_system_core_user_and_selected_format_contract():
    config = selected()
    config['agent']['system_template'] += '\nARCHIVED FORMAT CUSTOMIZATION'
    original = json.dumps(config, sort_keys=True)
    for role, upstream in w['ROLE_MAP'].items():
        adapted = w['role_templates'](config,role)
        system = adapted['agent']['system_template']
        assert system.startswith(w['definitions']()[2][upstream]['SYSTEM_PROMPT'])
        assert 'ARCHIVED FORMAT CUSTOMIZATION' in system
        assert 'mswea_bash_command' in system
        assert adapted['agent']['instance_template'].startswith('{{task}}')
        user = Template(adapted['agent']['instance_template']).render(task='FULL ORIGINAL DELEGATED TASK')
        assert user.startswith('FULL ORIGINAL DELEGATED TASK')
        assert 'Please solve this issue' not in system + user
        assert 'Create a script to reproduce' not in system + user
        feedback = Template(adapted['model']['observation_template']).render(output={'output':'x'*12000,'returncode':1,'exception_info':'bad','session_status':{'remaining_calls':3}})
        assert 'start_line' in feedback and 'remaining_calls' in feedback
        assert 'head, tail or sed' not in feedback and 'redirect output' not in feedback
        error = Template(adapted['model']['format_error_template']).render(error='bad',actions=[])
        assert 'sica-workflow' in error and 'mswea_bash_command' in error
    assert json.dumps(config,sort_keys=True) == original
    before = w['template_manifest'](config)
    config['model']['observation_template'] += '\narchived change'
    assert w['template_manifest'](config)['selected_config_templates_sha256'] != before['selected_config_templates_sha256']
    assert improvement_contract()['version'] == 'sica-mini-improvement-v3'


def test_read_line_bounds_real_path_and_history_provenance(tmp_path, monkeypatch):
    # Keep production roots, mapping their Path objects onto temporary fixtures only.
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location('read_fixture',ROOT/'runtime/workflow.py'))
    module.__spec__.loader.exec_module(module)
    actual_path = module.Path
    candidate = tmp_path/'candidate'; archive = tmp_path/'archive'
    candidate.mkdir(); archive.mkdir()
    def mapped(value):
        return actual_path(str(value).replace('/home/agent/workdir',str(candidate)).replace('/home/agent/archive',str(archive)))
    monkeypatch.setattr(module,'Path',mapped)
    flow = module.Workflow(tmp_path/'logs',lambda *a:None)
    historical = archive/'old.txt';historical.write_text('task from old testbed\nsecond\nthird\n')
    (candidate/'link').symlink_to(historical)
    out = flow.inspect({'op':'read','path':str(candidate/'link'),'start_line':2,'end_line':3})
    assert out['path'] == str(historical) and out['evidence_kind']=='historical_archive'
    assert out['content']=='2: second\n3: third'
    for bounds in ({'start_line':0},{'start_line':1,'end_line':501},{'start_line':True},{'end_line':'2'}):
        with pytest.raises(ValueError):flow.inspect({'op':'read','path':str(historical),**bounds})
    (candidate/'escape').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):flow.inspect({'op':'read','path':str(candidate/'escape')})
    assert flow.execute({'command':"sica-workflow '{\"op\":\"read\",\"path\":\"/home/agent/archive/old.txt\"}' | head -80"},True)['returncode']==1
    assert flow.execute({'command':"python -c 'open(\"/testbed/sklearn/pipeline.py\",\"w\")'"},True)['returncode']==1


def test_status_rejections_and_generic_error_do_not_inherit_old_terminal_reason(tmp_path,monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets',lambda:dict(OPENAI_BASE_URL='https://fixture/v1',OPENAI_API_KEY='secret',LOCAL_LLM_MODEL='fixture'))
    budget=Budget(tmp_path);budget.enter('preflight');broker=Broker(tmp_path,budget);broker.model='fixture'
    token=broker.session('roles',calls=1)
    def status(auth=token):
        req=urllib.request.Request(f'http://127.0.0.1:{broker.port}/status',data=b'{}',headers={'Authorization':'Bearer '+auth})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as f:return json.load(f)
    try:
        assert status()['remaining_calls']==1 and status()['terminal_reason'] is None
        with pytest.raises(urllib.error.HTTPError):status('wrong')
        with pytest.raises(ModelError):broker.complete(token,{'messages':[{'role':'user','content':'x '*40000}]})
        assert status()['calls']==0 and status()['terminal_reason']=='ContextLimitExceeded'
        class Endpoint:
            def open(self,*a,**kw):raise RuntimeError('LimitsExceeded arbitrary endpoint text')
        broker.opener=Endpoint()
        with pytest.raises(ModelError,match='RuntimeError'):broker.complete(token,{'messages':[{'role':'user','content':'hello'}]})
        assert status()['calls']==1 and status()['terminal_reason'] is None
        APIError=type('APIError',(Exception,),{})
        raw=APIError('LimitsExceeded arbitrary exception text')
        assert bridge['classified_failure'](raw,status())['exit_status']=='APIError'
        with pytest.raises(ModelError,match='LimitsExceeded'):broker.complete(token,{'messages':[]})
        result=bridge['classified_failure'](raw,status())
        assert result['exit_status']=='LimitsExceeded' and result['raw_exception']['type']=='APIError'
        assert status()['calls']==1 and len(status()['rejected_calls'])==2
        broker.sessions[token]['deadline']=0
        assert status()['remaining_seconds']==0
        with pytest.raises(ModelError,match='TimeExceeded'):broker.complete(token,{'messages':[]})
        assert status()['terminal_reason']=='TimeExceeded'
        assert 'secret' not in json.dumps(status()) and token not in json.dumps(status())
    finally:broker.close()


def test_actual_fourth_run_actions_are_rejected_without_rewriting(tmp_path):
    import re
    old=json.loads((ROOT/'tests/fixtures/role_transport_fourth_run.json').read_text())
    assert 'helpful assistant' in old['old_system']
    assert 'Please solve this issue' in old['old_user_excerpt']
    flow=w['Workflow'](tmp_path,lambda *a:None)
    actions=[re.search(r'```mswea_bash_command\n(.*?)\n```',message,re.S).group(1) for message in old['observed_actions']]
    assert len(actions)>=2
    assert any('/testbed/sklearn/pipeline.py' in action for action in actions)
    assert any(' | ' in action for action in actions)
    for action in actions:
        outcome=flow.execute({'command':action},True)
        assert outcome['returncode']==1
        outcome['exception_info']=''
        for role in ('archive_explorer','orchestrator','software_developer'):
            feedback=Template(w['role_templates'](selected(),role)['model']['observation_template']).render(output=outcome)
            assert 'start_line' in feedback and 'Use one JSON argument' in feedback
            assert '/home/agent/archive' in feedback and '/home/agent/workdir' in feedback
            assert 'head, tail or sed' not in feedback and 'redirect output to a file' not in feedback
            if role=='software_developer':assert 'For structured-operation errors' in feedback
    adapted=w['role_templates'](selected(),'archive_explorer')
    assert 'Please solve this issue' not in adapted['agent']['instance_template']
    assert 'frozen public historical evidence' in w['identity_context']({'parent':2,'proposer':1,'candidate':'agent_3'}, {'index':0,'instruction':'PARENT ONLY'})
    assert 'PARENT ONLY' not in w['identity_context']({}, {'index':0,'instruction':'PARENT ONLY'})


def test_expired_tool_has_controlled_reason_without_execution(tmp_path,monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets',lambda:dict(OPENAI_BASE_URL='https://fixture/v1',OPENAI_API_KEY='secret',LOCAL_LLM_MODEL='fixture'))
    budget=Budget(tmp_path);budget.enter('preflight');broker=Broker(tmp_path,budget)
    token=broker.session('editor',solver='fixture')
    monkeypatch.setattr('mini_swe_rsi.broker.subprocess.run',lambda *a,**kw:pytest.fail('expired command executed'))
    try:
        broker.sessions[token]['deadline']=0
        with pytest.raises(ModelError,match='TimeExceeded'):broker.tool(token,{'command':'touch x'})
        assert broker.status(token)['terminal_reason']=='TimeExceeded'
        assert broker.status(token)['calls']==0 and broker.sessions[token]['tool_calls']==0
        assert broker.events[-1]['kind']=='tool_rejection' and not broker.events[-1]['actual_call']
        broker.sessions[token]['deadline']=__import__('time').time()+60
        monkeypatch.setattr(budget,'check',lambda:(_ for _ in ()).throw(__import__('mini_swe_rsi.core',fromlist=['BudgetExceeded']).BudgetExceeded('global')))
        with pytest.raises(ModelError,match='TimeExceeded'):broker.tool(token,{'command':'touch x'})
        assert broker.events[-1]['basis']=='experiment_budget'
    finally:broker.close()
