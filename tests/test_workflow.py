from mini_swe_rsi.context_budget import ContextBudget
import importlib.util
import json
import shlex

import pytest

from mini_swe_rsi.core import ROOT, improvement_contract, read

spec = importlib.util.spec_from_file_location('workflow_fixture', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def command(request):
    return {'command':'sica-workflow '+shlex.quote(json.dumps(request))}


def test_pinned_fields_and_core_prompts():
    steps,specs,roles = w.definitions()
    assert len(steps) == 7
    assert list(specs) == ['pragmatist','taste_maker','utility_optimizer','testing_manager']
    assert steps[0]['instruction'].startswith('Use the archive explorer agent')
    assert 'MUST call' in steps[2]['instruction']
    assert 'DO NOT attempt the task yourself' in w.core_prompt('review_committee_member',dict(specialisation='spec',context='context',proposal='proposal'))
    assert 'outer-loop entrypoint orchestrator' in roles['main_orchestrator']['SYSTEM_PROMPT']
    assert 'general_problem_solver' == roles['problem_solver']['AGENT_NAME']
    assert 'then you must invoke one of your' not in w.core_prompt('coder',dict(programming_instructions='task')) # unused upstream variable
    for role in ('software_developer','reviewer','archive_explorer'):
        assert 'Invoke meta_improvement_reasoning_structure' not in w.instructions(role)
    assert improvement_contract()['workflow_sources'] == {n:v['sha256'] for n,v in read(ROOT/'upstream.lock.json')['sica']['workflow_files'].items()}


def test_sequential_success_failure_bad_ids_and_interruption(tmp_path):
    flow = w.Workflow(tmp_path, lambda *a: None)
    state = flow.dispatch({'op':'invoke'})
    with pytest.raises(ValueError):
        flow.dispatch({'op':'complete','invocation_id':state['invocation_id'],'tool':'bad'})
    for index in range(7):
        state = flow.current()
        assert state['index'] == index
        result = flow.dispatch({'op':'complete','invocation_id':state['invocation_id'],'tool':state['completion_tool']})
    assert flow.status == 'completed' and flow.completed_steps == 7
    assert 'Reasoning structure complete' in result
    flow.dispatch({'op':'invoke'})
    assert flow.completed_steps == 0
    state=flow.current()
    flow.dispatch({'op':'complete','invocation_id':state['invocation_id'],'tool':state['completion_tool'],'success':False})
    assert flow.status == 'failed' and flow.active is None
    with pytest.raises(ValueError):
        w.Workflow(tmp_path, lambda *a:None)
    other=w.Workflow(tmp_path/'early', lambda *a:None)
    other.dispatch({'op':'invoke'})
    other.finish({'exit_status':'Submitted'})
    assert other.status=='incomplete' and other.completed_steps == 0 and other.active


def test_four_reviews_and_capability_boundary(tmp_path, monkeypatch):
    calls=[]
    def delegate(identity,prompt,readonly):
        calls.append((identity,prompt,readonly))
        return {'exit_status':'Submitted','submission':identity}
    flow=w.Workflow(tmp_path,delegate)
    result=flow.dispatch({'op':'committee','context':'evidence','proposal':'design'})
    assert len(calls)==4 and all(c[2] for c in calls)
    assert all(spec in calls[i][1] for i,spec in enumerate(flow.specs.values()))
    assert result == ''.join(f"\n\nReviewer {i}'s review:\n{c[0]}" for i,c in enumerate(calls))
    assert all('DO NOT attempt the task yourself' in c[1] for c in calls)
    for mode in (True,'orchestrator'):
        assert flow.execute({'command':'touch /home/agent/workdir/forbidden'},mode)['returncode']==1
        report="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfull review"
        out=flow.execute({'command':"printf '%s\\n' "+shlex.quote(report)},mode)
        assert out['output'].startswith(report)
    for mode in (True,'editor'):
        assert flow.execute(command({'op':'invoke'}),mode)['returncode']==1
    assert flow.execute(command({'op':'read','path':'/etc/passwd'}),True)['returncode']==1
    assert flow.execute({'command':'sica-workflow bad && touch x'},'orchestrator')['returncode']==1
    monkeypatch.setattr(w,'archive_overview',lambda:'ORIGINAL OVERVIEW')
    flow.call_role('archive_explorer','focus')
    assert 'ORIGINAL OVERVIEW' in calls[-1][1] and calls[-1][2]
    flow.call_role('software_developer','implement')
    flow.call_role('general_problem_solver','document')
    assert not calls[-1][2] and not calls[-2][2]


def test_child_failure_not_fake_success(tmp_path):
    flow=w.Workflow(tmp_path,lambda *a: {'exit_status':'LimitsExceeded','submission':''})
    with pytest.raises(ValueError,match='LimitsExceeded'):
        flow.dispatch({'op':'committee','context':'','proposal':''})
    assert flow.counter==1
    assert flow.completed_steps==0


@pytest.mark.skipif(__import__('os').environ.get('RUN_DOCKER_TESTS') != '1', reason='Docker fixture')
@pytest.mark.parametrize('limit', [30, 6, 500, None])
def test_docker_native_workflow_roles_share_archived_model_and_broker(tmp_path, monkeypatch, limit):
    import io
    import re
    import time
    from mini_swe_rsi.broker import Broker
    from mini_swe_rsi.core import Budget, tree_hash, write
    from mini_swe_rsi.driver import Driver, initial_code
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda:dict(OPENAI_BASE_URL='https://fixture/v1',OPENAI_API_KEY='fixture-secret',LOCAL_LLM_MODEL='fixture'))
    budget=Budget(tmp_path)
    if limit is None:
        write(budget.path, {'started':0,'phases':{'preflight':0},'unbounded_diagnostic':True})
    budget.enter('preflight')
    context = ContextBudget({'max_output_tokens':393216,'context_window':1048576}) if limit is None else ContextBudget()
    broker=Broker(tmp_path,budget,context=context);broker.model='fixture'
    requests=[]; main_turn=0; coder_turn=0; archive_turn=0
    plan=['meta_improvement_reasoning_structure' if limit==30 else 'invoke','complete','complete','committee','complete','archive_explorer','software_developer','complete','complete','general_problem_solver','complete','complete','submit']
    if limit == 500:
        plan = ['list']*35 + plan  # Prove the actual archived main agent crosses its former30 limit.
    if limit is None:
        plan = ['forbidden_archive'] + plan
    class Endpoint:
        def open(self,request,**kw):
            nonlocal main_turn,coder_turn,archive_turn
            if limit is None:
                assert kw['timeout'] is None
            payload=json.loads(request.data);requests.append(payload)
            system, user = payload['messages'][0]['content'], payload['messages'][1]['content']
            assert system.startswith(tuple(d['SYSTEM_PROMPT'] for d in w.definitions()[2].values()))
            assert 'Please solve this issue' not in user and 'Create a script to reproduce' not in user
            assert 'Shared session at role start:' in user and 'remaining_calls' in user
            assert 'Trusted current improvement context:' in user
            assert 'independent' in system and '1000' in system
            assert all(not payload.get(key) for key in ('tools','tool_choice','functions','function_call'))
            for message in payload['messages'][2:]:
                if message['role']=='user':
                    assert 'Shared session:' in message['content']
                    assert 'head, tail or sed' not in message['content']
                    assert 'redirect output to a file' not in message['content']
            task=payload['messages'][0]['content'] + '\n' + payload['messages'][1]['content']
            if 'Self-contained task:' in task: # old protocol must never be used
                raise AssertionError('Unrendered role prompt')
            if 'You are a member of a Meta-Agent design review committee' in task:
                cmd="printf '%s\\n' "+shlex.quote('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nreview complete')
            elif 'You are an expert archive analyst' in task:
                assert 'high-level overview' in task
                archive_turn+=1
                cmd=(command({'op':'archive','kind':'best','iteration':0})['command'] if limit is None and archive_turn==1
                     else "printf '%s\\n' "+shlex.quote('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\narchive evidence'))
            elif 'You are an expert software engineer' in task:
                coder_turn+=1
                cmd="printf editor > /home/agent/workdir/fixture.txt" if coder_turn==1 else 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
            elif 'You are a very-competent problem solver' in task:
                cmd="printf '%s\\n' "+shlex.quote('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\ndocumentation complete')
            else:
                op=plan[main_turn];main_turn+=1
                if op=='submit':cmd='echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
                else:
                    req={'op':op}
                    if op=='forbidden_archive':req={'op':'archive','kind':'best','iteration':0}
                    elif op=='complete':
                        text='\n'.join(str(m.get('content','')) for m in payload['messages'])
                        req = {'op':re.findall(r'step_[a-f0-9]+_complete',text)[-1], 'success':True}
                    elif op=='list':req['path']='/home/agent/workdir'
                    elif op=='committee':req.update(context='fixture evidence',proposal='fixture design')
                    elif op in ('archive_explorer','software_developer','general_problem_solver'):req={'op':'delegate','role':op,'task':'fixture task'}
                    cmd=command(req)['command']
            response={'id':'fixture','created':int(time.time()),'object':'chat.completion','model':'fixture','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':f'THOUGHT: Fixture.\n```mswea_bash_command\n{cmd}\n```'}}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
            return io.BytesIO(json.dumps(response).encode())
    broker.opener=Endpoint(); driver=Driver(tmp_path,None,broker,budget)
    try:
        archive=tmp_path/'archive';code=archive/'agent_0/agent_code';initial_code(code)
        import shutil
        target=tmp_path/'candidate';shutil.copytree(code,target)
        original=tree_hash(code)
        token=broker.session('workflow',calls=limit,seconds=None if limit is None else 600)
        status,_=driver.controller(code,tmp_path/'logs',['improve','--parent-iter','0','--best-iter','0','--candidate-id','agent_1','--current-iter','0'],token,archive=archive,target=target)
        usage=broker.finish(token)
        state=read(tmp_path/'logs/workflow_state.json')
        assert len(requests)==usage['calls'] and (limit is None or len(requests)<=limit)
        assert all(p['model']=='fixture' and p['max_tokens']==context.contract['output_tokens'] for p in requests)
        assert tree_hash(code)==original
        assert read(tmp_path/'logs/improvement_verification.json')['status']=='verified'
        roles=list((tmp_path/'logs/roles').glob('*/activation.json'))
        assert all(read(p)['agent_file'].startswith('/home/agent/archive/agent_0/agent_code/') for p in roles)
        assert all(read(p)['shared_broker_session'] for p in roles)
        assert read(tmp_path/'logs/activation.json')['step_limit']==500
        assert read(tmp_path/'logs/activation.json')['wall_time_limit_seconds']==0
        assert all(read(p)['step_limit']==(1000 if read(p)['role'].endswith('software_developer') else 500) for p in roles)
        if limit is None:
            assert usage['deadline'] is None
            assert read(tmp_path/'logs/activation.json')['budget']['global_remaining_seconds'] is None
            assert 'no additional diagnostic global/stage deadline' in (tmp_path/'logs/improvement_prompt.txt').read_text()
            assert usage['max_calls'] is None and usage['iterations']['main']==14
            assert usage['iterations']['006_software_developer']==2
            events=[json.loads(line) for line in (tmp_path/'logs/workflow_events.jsonl').read_text().splitlines()]
            assert any(e.get('kind')=='operation_error' and 'unavailable to orchestrator' in e.get('error','') for e in events)
            assert any(e.get('op')=='archive' and e.get('role')=='archive_explorer' for e in events)
        if limit in (30,500,None):
            assert status==0 and state['status']=='completed' and state['completed_steps']==7
            assert len(roles)==7 # four reviewers, archive, coder, general
            assert (target/'fixture.txt').read_text()=='editor'
            assert len(requests)==(56 if limit==500 else 23 if limit is None else 21)
        else:
            assert status!=0 and state['status']=='incomplete' and state['completed_steps']==2
            assert len(requests)==6 and not (target/'fixture.txt').exists()
            assert read(tmp_path/'logs/result.json')['exit_status']=='LimitsExceeded'
            failures=[read(p) for p in (tmp_path/'logs/roles').glob('*/result.json') if read(p)['exit_status']!='Submitted']
            assert failures and failures[-1]['exit_status']=='LimitsExceeded'
            assert failures[-1]['raw_exception']['type']=='APIError'
            assert usage['rejected_calls'] and usage['calls']==6
    finally:
        driver.close();broker.close()


def test_role_preparation_error_is_identified(tmp_path, monkeypatch):
    flow=w.Workflow(tmp_path,lambda *a:None)
    def fail():
        raise ValueError('overview failure')
    monkeypatch.setattr(w,'archive_overview',fail)
    with pytest.raises(ValueError,match='overview failure'):
        flow.call_role('archive_explorer','focus')
    events=[json.loads(line) for line in (tmp_path/'workflow_events.jsonl').read_text().splitlines()]
    assert events[-1]['kind']=='role_error' and events[-1]['identity']==events[0]['identity']


def test_global_thirty_actual_calls_and_deadline_across_roles(tmp_path, monkeypatch):
    import io
    import time
    from mini_swe_rsi.broker import Broker, ModelError
    from mini_swe_rsi.core import Budget
    monkeypatch.setattr('mini_swe_rsi.broker.secrets',lambda:dict(OPENAI_BASE_URL='https://fixture/v1',OPENAI_API_KEY='secret',LOCAL_LLM_MODEL='fixture'))
    budget=Budget(tmp_path);budget.enter('preflight')
    broker=Broker(tmp_path,budget,context=ContextBudget());broker.model='fixture';actual=[]
    class Endpoint:
        def open(self,request,**kw):
            actual.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices':[],'usage':{'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}}).encode())
    broker.opener=Endpoint();token=broker.session('roles',calls=30)
    def delegate(identity,prompt,readonly):
        broker.complete(token,{'model':'forbidden-stronger','messages':[{'role':'user','content':prompt}]})
        return {'exit_status':'Submitted','submission':identity}
    flow=w.Workflow(tmp_path/'workflow',delegate)
    try:
        for i in range(7):
            flow.dispatch({'op':'committee','proposal':'design','context':'evidence'})
        flow.call_role('software_developer','edit')
        flow.call_role('general_problem_solver','document')
        with pytest.raises(ModelError,match='LimitsExceeded'):
            flow.call_role('software_developer','no new budget')
        assert len(actual)==30 and broker.sessions[token]['calls']==30
        assert all(p['model']=='fixture' and p['max_tokens']==2048 for p in actual)
        broker.sessions[token]['deadline']=time.time()-1
        with pytest.raises(ModelError,match='TimeExceeded'):
            flow.call_role('general_problem_solver','no new time budget')
        assert len(actual)==30
    finally:
        broker.close()


@pytest.mark.parametrize('mutation',['missing','extra','changed'])
def test_pinned_sources_reject_missing_extra_or_changed(tmp_path, monkeypatch, mutation):
    import shutil
    from mini_swe_rsi import core
    shutil.copytree(ROOT/'vendor/sica_workflow',tmp_path/'vendor/sica_workflow')
    shutil.copy(ROOT/'upstream.lock.json',tmp_path/'upstream.lock.json')
    source=tmp_path/'vendor/sica_workflow/meta_improvement.py'
    if mutation=='missing':source.unlink()
    elif mutation=='changed':source.write_text(source.read_text()+'# altered\n')
    else:(source.parent/'extra.py').write_text('pass')
    monkeypatch.setattr(core,'ROOT',tmp_path)
    with pytest.raises(RuntimeError,match='Pinned workflow sources'):
        core.improvement_contract()


def test_atomic_state_write_preserves_previous_valid_state(tmp_path, monkeypatch):
    from pathlib import Path
    flow=w.Workflow(tmp_path,lambda *a:None)
    previous=read(tmp_path/'workflow_state.json')
    def interrupted(*a,**kw):
        raise OSError('simulated interruption before atomic replace')
    monkeypatch.setattr(Path,'replace',interrupted)
    flow.status='active'
    with pytest.raises(OSError,match='interruption'):
        flow.save()
    assert read(tmp_path/'workflow_state.json')==previous


def test_exact_original_tool_name_aliases_preserve_semantics_and_permissions(tmp_path, monkeypatch):
    assert w.tool_aliases()=={'meta_improvement_reasoning_structure':'invoke','meta_improvement_review_committee':'committee'}
    monkeypatch.setattr(w.uuid,'uuid4',lambda:type('FixedUUID',(),{'hex':'fixture'})())
    canonical=w.Workflow(tmp_path/'canonical',lambda *a:None)
    aliased=w.Workflow(tmp_path/'alias',lambda *a:None)
    # Exact request observed on actual model calls29/30. 'step' never completes a step.
    observed={'op':'meta_improvement_reasoning_structure','step':1}
    outcome=aliased.execute(command(observed),'orchestrator')
    assert outcome['returncode']==0
    assert json.loads(outcome['output'])==canonical.dispatch({'op':'invoke'})
    assert aliased.completed_steps==0 and aliased.current()['index']==0
    again=aliased.execute(command(observed),'orchestrator')
    assert again['returncode']==1 and 'already active' in again['output']
    events=[json.loads(line) for line in (tmp_path/'alias/workflow_events.jsonl').read_text().splitlines()]
    assert any(e.get('requested_op')==observed['op'] and e.get('op')=='invoke' for e in events)
    calls=[]
    def delegate(identity,prompt,readonly):
        calls.append((identity,prompt,readonly));return {'exit_status':'Submitted','submission':identity}
    flow=w.Workflow(tmp_path/'reviews',delegate)
    request={'op':'meta_improvement_review_committee','context':'evidence','proposal':'design'}
    output=flow.execute(command(request),'editor')
    assert output['returncode']==0 and len(calls)==4 and all(c[2] for c in calls)
    assert json.loads(output['output'])==''.join(f"\n\nReviewer {i}'s review:\n{c[0]}" for i,c in enumerate(calls))
    assert flow.completed_steps==0 and flow.active is None
    for mode in ('editor',True):
        denied=w.Workflow(tmp_path/str(mode),delegate)
        assert denied.execute(command(observed),mode)['returncode']==1
        assert denied.active is None and denied.completed_steps==0
    for mode in (True,):
        assert flow.execute(command(request),mode)['returncode']==1
    assert len(calls)==4
    for invalid in ('review_committee','meta_improvement_structure','step_future_complete'):
        assert flow.execute(command({'op':invalid}),'orchestrator')['returncode']==1
    state=aliased.current()
    assert aliased.execute(command({'op':'complete','invocation_id':state['invocation_id'],'tool':'future_complete'}),'orchestrator')['returncode']==1
    assert aliased.completed_steps==0
    assert 'meta_improvement_reasoning_structure' in w.instructions()
    assert 'meta_improvement_review_committee' in w.instructions('software_developer')
