"""Envelope compatibility must preserve actions, reject ambiguity and keep gates."""
import copy
import importlib
import importlib.util

import pytest

from mini_swe_rsi.broker import adapt_response
from mini_swe_rsi.core import ROOT

CMD = 'sica-workflow {"op":"state"}'
CANON = '```mswea_bash_command\n' + CMD + '\n```'


@pytest.fixture
def official_parser(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'.upstream/mini/src'))
    module = importlib.import_module('minisweagent.models.utils.actions_text')
    def parse(text):
        return module.parse_regex_actions(text, action_regex=r'```mswea_bash_command\s*\n(.*?)\n```',
                                          format_error_template='{{error}}')
    return parse


def raw(content='', reasoning=None, finish='stop'):
    message={'role':'assistant','content':content}
    if reasoning is not None:message['reasoning_content']=reasoning
    return {'choices':[{'finish_reason':finish,'message':message}]}


@pytest.mark.parametrize('action',[
    CANON,
    '```bash\n'+CMD+'\n```',
    '<mswea_bash_command>'+CMD+'</mswea_bash_command>',
    '<bash_command>\n'+CMD+'\n</bash_command>',
    '<tool_call>\n<mswea_bash_command>\n'+CMD+'\n</mswea_bash_command>\n</tool_call>',
    '<tool_call><bash_command>'+CMD+'</bash_command></tool_call>',
])
@pytest.mark.parametrize('field',['content','reasoning_content'])
def test_single_complete_envelopes_reach_official_parser_without_changing_command(action,field,official_parser):
    text='Format name mswea_bash_command is ordinary prose.\n'+action+'\nEnd of explanation.'
    source=raw(content=text) if field=='content' else raw(reasoning=text)
    original=copy.deepcopy(source)
    returned,why=adapt_response(source)
    assert source==original
    assert official_parser(returned['choices'][0]['message']['content'])==[{'command':CMD}]
    if action==CANON and field=='content':assert returned==source
    else:assert why.startswith(('normalized','promoted'))


@pytest.mark.parametrize('text',[
    CANON+'\n'+CANON,
    CANON+'\n```bash\necho second\n```',
    CANON+'\n<bash_command>echo second</bash_command>',
    '<bash_command>one</bash_command><bash_command>two</bash_command>',
    '<tool_call><mswea_bash_command>'+CMD+'</mswea_bash_command>',
    '<tool_call>mswea_bash_command\n'+CMD+'\n```',
    '<mswea_bash_command>'+CMD+'</bash_command>',
    '<bash_command></bash_command>',
    '```bash\necho incomplete',
    '```bash\necho ok\n```\n```',
    '~~~bash\necho unsupported\n~~~\n'+CANON,
    '<bash_command><tool_call>echo nested</tool_call></bash_command>',
    CANON+'\n</tool_call',
    '````bash\necho four-fence\n````',
])
def test_mixed_multiple_and_incomplete_envelopes_cannot_leak_one_action(text,official_parser):
    source=raw(content=text);original=copy.deepcopy(source)
    returned,why=adapt_response(source)
    assert source==original
    assert why.startswith('rejected ambiguous content')
    with pytest.raises(Exception) as exc:official_parser(returned['choices'][0]['message']['content'])
    assert type(exc.value).__name__=='FormatError'


def test_nonstop_native_tools_and_plain_prose_are_not_reinterpreted():
    for source in (raw(content='ordinary prose'),raw(content='```bash\necho hi\n```',finish='length')):
        assert adapt_response(source)[0]==source
    source=raw(content='<bash_command>echo hi</bash_command>')
    source['choices'][0]['message']['tool_calls']=[{'id':'native'}]
    assert adapt_response(source)[0]==source


def test_normalized_action_cannot_bypass_step_three_role_permissions(tmp_path,official_parser):
    spec=importlib.util.spec_from_file_location('envelope_stage_workflow',ROOT/'runtime/workflow.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    delegated=[]
    flow=module.Workflow(tmp_path,lambda *args:delegated.append(args),online=True,
                         feedback={'task_id':'t','evidence_refs':['tasks/t/trace.txt']})
    flow.dispatch({'op':'invoke'});flow.active['index']=2;flow.completed_steps=2;flow.status='improving';flow.save()
    command='sica-workflow {"op":"delegate","role":"software_developer","task":"edit prematurely"}'
    returned,_=adapt_response(raw(content='<bash_command>'+command+'</bash_command>'))
    action=official_parser(returned['choices'][0]['message']['content'])[0]
    result=flow.execute(action,readonly='orchestrator',role='orchestrator')
    assert result['returncode']==1 and 'only at step 4' in result['output']
    assert not delegated and flow.counter==0
