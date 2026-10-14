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


@pytest.mark.parametrize('call',[140,141,142])
def test_real_committee_responses_keep_json_examples_and_raw_bytes(call,official_parser):
    import hashlib,json
    fixtures=json.loads((ROOT/'tests/fixtures/committee_fence_responses.json').read_text())
    case=next(x for x in fixtures if x['call']==call)
    source=case['response'];original=copy.deepcopy(source)
    text=source['choices'][0]['message']['content']
    assert hashlib.sha256(text.encode()).hexdigest()==case['content_sha256']
    original_action=official_parser(text)
    returned,why=adapt_response(source)
    assert source==original and returned==original
    assert why=='unchanged: nonempty content'
    assert official_parser(returned['choices'][0]['message']['content'])==original_action
    command=original_action[0]['command']
    request=json.loads(command.removeprefix('sica-workflow '))
    assert request['op']==case['expected_operation']=='committee'
    assert request['context'] and request['proposal']


@pytest.mark.parametrize('field',['content','reasoning_content'])
@pytest.mark.parametrize('format',['mswea_bash_command','bash'])
def test_inline_and_json_quoted_fences_are_data_not_extra_envelopes(field,format,official_parser):
    import json
    payload={'op':'committee','context':'Use regex r"```mswea_bash_command\\s*\\n(.*?)\\n```".',
             'proposal':'Keep `<tool_call>` and `</tool_call>` as literal strings; preserve ```bash ... ``` examples.'}
    command='sica-workflow '+json.dumps(payload,ensure_ascii=False)
    explanation='The regex `r"```mswea_bash_command\\s*\\n(.*?)\\n```"` and closing `</tool_call>` are examples.\n'
    block='```'+format+'\n'+command+'\n```'
    text=explanation+block
    source=raw(content=text) if field=='content' else raw(reasoning=text)
    before=copy.deepcopy(source);returned,_=adapt_response(source)
    assert source==before
    parsed=official_parser(returned['choices'][0]['message']['content'])
    assert parsed==[{'command':command}]
    assert json.loads(parsed[0]['command'].removeprefix('sica-workflow '))==payload


def test_crlf_fence_boundaries_preserve_json_command(official_parser):
    command='sica-workflow {"op":"committee","proposal":"```bash is data"}'
    content='Example `</tool_call>` is prose.\r\n```mswea_bash_command\r\n'+command+'\r\n```\r\n'
    source=raw(content=content)
    returned,_=adapt_response(source)
    assert returned==source
    assert official_parser(returned['choices'][0]['message']['content'])==[{'command':command}]


@pytest.mark.parametrize('extra',[
    '\n```bash\necho second\n```',
    '\n```mswea_bash_command\necho second\n```',
    '\n```bash\necho incomplete',
    '\n```',
    '\n<tool_call><bash_command>echo second</bash_command></tool_call>',
    '\n<tool_call>',
    '\n~~~bash\necho unsupported\n~~~',
])
def test_quoted_examples_cannot_hide_real_second_or_incomplete_action(extra,official_parser):
    import json
    command='sica-workflow '+json.dumps({'op':'committee','context':'r"```mswea_bash_command\\s*\\n(.*?)\\n```"'})
    content='Example `</tool_call>` is prose.\n```mswea_bash_command\n'+command+'\n```'+extra
    source=raw(content=content);original=copy.deepcopy(source)
    returned,why=adapt_response(source)
    assert source==original and why.startswith('rejected ambiguous content')
    with pytest.raises(Exception) as exc:official_parser(returned['choices'][0]['message']['content'])
    assert type(exc.value).__name__=='FormatError'


def test_xml_action_with_inline_prose_examples_preserves_command(official_parser):
    source=raw(content='The example ````bash ... ```` and `<tool_call>` are documentation.\n<tool_call><bash_command>'+CMD+'</bash_command></tool_call>')
    returned,why=adapt_response(source)
    assert why.startswith('normalized single complete content action')
    assert official_parser(returned['choices'][0]['message']['content'])==[{'command':CMD}]


@pytest.mark.parametrize('call',[140,141,142])
def test_real_committee_payload_reaches_original_workflow_without_source_writes(call,tmp_path,official_parser):
    import json
    fixtures=json.loads((ROOT/'tests/fixtures/committee_fence_responses.json').read_text())
    source=next(x['response'] for x in fixtures if x['call']==call)
    spec=importlib.util.spec_from_file_location('real_committee_fence_workflow',ROOT/'runtime/workflow.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    code=tmp_path/'candidate';code.mkdir();(code/'source.py').write_text('UNCHANGED = True\n')
    calls=[]
    def delegate(identity,prompt,readonly):
        assert readonly is True
        calls.append(identity)
        return {'exit_status':'Submitted','submission':'Evidence and proposal reviewed.'}
    ref='tasks/t/trace.txt'
    flow=module.Workflow(tmp_path/'logs',delegate,online=True,identities={'workdir':str(code)},
                         feedback={'task_id':'t','evidence_refs':[ref]})
    flow.dispatch({'op':'invoke'})
    for _ in range(2):
        flow.call_role('archive_explorer','inspect evidence')
        flow.dispatch({'op':flow.current()['completion_tool'],'report':'diagnosis','evidence_refs':[ref]})
    flow.dispatch({'op':'diagnosis_decision','decision':'improve','reason':'observed failure',
                   'improvement_opportunities':['repair'],'evidence_refs':[ref]})
    before=flow.stage_source_hash();adapted,_=adapt_response(source)
    action=official_parser(adapted['choices'][0]['message']['content'])[0]
    result=flow.execute(action,readonly='orchestrator',role='orchestrator')
    assert result['returncode']==0
    assert len([x for x in calls if x.endswith('_reviewer')])==len(flow.specs)==4
    assert flow.completed_steps==2 and flow.current()['index']==2
    assert flow.stage_source_hash()==before
    assert (code/'source.py').read_text()=='UNCHANGED = True\n'


@pytest.mark.parametrize('call',[140,141,142])
def test_real_payload_with_added_second_action_still_rejected(call,official_parser):
    import json
    fixtures=json.loads((ROOT/'tests/fixtures/committee_fence_responses.json').read_text())
    source=copy.deepcopy(next(x['response'] for x in fixtures if x['call']==call))
    source['choices'][0]['message']['content']+='\n```bash\necho second\n```'
    returned,why=adapt_response(source)
    assert why.startswith('rejected ambiguous content')
    with pytest.raises(Exception) as exc:official_parser(returned['choices'][0]['message']['content'])
    assert type(exc.value).__name__=='FormatError'


@pytest.mark.parametrize('field',['content','reasoning_content'])
@pytest.mark.parametrize('text',[
    'sica-workflow {"op":"committee","proposal":"<bash_command>echo example</bash_command>"}',
    'The quoted example is "<tool_call><bash_command>echo example</bash_command></tool_call>".',
    'The JSON example {"example":"<mswea_bash_command>echo example</mswea_bash_command>"} is data.',
])
def test_xml_quoted_as_json_or_prose_cannot_become_an_action(field,text,official_parser):
    source=raw(content=text) if field=='content' else raw(reasoning=text)
    before=copy.deepcopy(source);returned,_=adapt_response(source)
    assert returned==before and source==before
    with pytest.raises(Exception) as exc:official_parser(returned['choices'][0]['message'].get('content') or '')
    assert type(exc.value).__name__=='FormatError'


def test_xml_action_can_carry_literal_closing_tags_in_json_without_truncation(official_parser):
    import json
    payload={'op':'committee','proposal':'<bash_command>example</bash_command>',
             'context':'A quoted string with \\" and </tool_call> plus ```bash as data.'}
    command='sica-workflow '+json.dumps(payload)
    source=raw(content='<tool_call><bash_command>'+command+'</bash_command></tool_call>')
    returned,why=adapt_response(source)
    assert why.startswith('normalized')
    assert official_parser(returned['choices'][0]['message']['content'])==[{'command':command}]


def test_quoted_example_does_not_hide_real_second_xml_action(official_parser):
    text='Example "<bash_command>echo quoted</bash_command>".\n'+CANON+'\n<tool_call><bash_command>echo real</bash_command></tool_call>'
    returned,why=adapt_response(raw(content=text))
    assert why.startswith('rejected ambiguous content')
    with pytest.raises(Exception) as exc:official_parser(returned['choices'][0]['message']['content'])
    assert type(exc.value).__name__=='FormatError'


def test_unpaired_backtick_inside_json_cannot_absorb_the_real_xml_closure(official_parser):
    import json
    command='sica-workflow '+json.dumps({'op':'committee','proposal':'A literal ` is data.',
                                        'context':'A literal </bash_command> is also data.'})
    source=raw(content='<tool_call><bash_command>'+command+'</bash_command></tool_call> Afterthought `quoted` text.')
    returned,why=adapt_response(source)
    assert why.startswith('normalized')
    assert official_parser(returned['choices'][0]['message']['content'])==[{'command':command}]
