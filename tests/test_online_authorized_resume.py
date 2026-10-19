"""Host custody and accounting checks for explicitly authorized restoration."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import ROOT, digest, read, tree_hash, write
from mini_swe_rsi.recovery import load_workflow_authorization, restore_session_accounting

spec = importlib.util.spec_from_file_location('host_resume_workflow', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)


def authorization_fixture(tmp_path):
    root=tmp_path/'run'; logs=root/'rounds/R0001'; candidate=logs/'candidate_work'
    candidate.mkdir(parents=True); (candidate/'partial.py').write_text('PRESERVED = True\n')
    identities={'task_id':'t1','round_id':'R0001','version':'H0000',
                'parent':0,'proposer':0,'candidate':'P0001'}
    flow=w.Workflow(logs,lambda *_:{'exit_status':'Submitted','submission':'evidence'},
                    feedback={'task_id':'t1','round_id':'R0001',
                                          'evidence_refs':['tasks/t1/trace.txt']})
    flow.dispatch({'op':'invoke'})
    for _ in range(2):
        flow.call_role('archive_explorer','inspect evidence')
        flow.dispatch({'op':flow.current()['completion_tool'],'report':'report',
                       'evidence_refs':['tasks/t1/trace.txt']})
    flow.dispatch({'op':'diagnosis_decision','decision':'improve','reason':'observed problem',
                   'improvement_opportunities':['repair'],'evidence_refs':['tasks/t1/trace.txt']})
    path=root/'private/resume.json'
    write(path,{'schema':'online-workflow-resume-v1','identities':identities,
                'state':read(logs/'workflow_state.json'),
                'state_sha256':digest(logs/'workflow_state.json'),
                'candidate_source_sha256':tree_hash(candidate)})
    return root,logs,candidate,identities,path


def test_authorization_preserves_partial_code_and_rejects_tampering(tmp_path):
    root,logs,candidate,identities,path=authorization_fixture(tmp_path)
    before=(candidate/'partial.py').read_bytes()
    a=load_workflow_authorization(path,root,logs,candidate,identities)
    assert a['state']['completed_steps']==2
    assert (candidate/'partial.py').read_bytes()==before
    with pytest.raises(ValueError,match='identities'):
        load_workflow_authorization(path,root,logs,candidate,{**identities,'version':'H0001'})
    (candidate/'partial.py').write_text('MUTATED = True\n')
    with pytest.raises(ValueError,match='candidate changed'):
        load_workflow_authorization(path,root,logs,candidate,identities)


def test_resume_cannot_use_an_unbound_or_changed_state_file(tmp_path):
    root,logs,candidate,identities,path=authorization_fixture(tmp_path)
    outside=root/'public.json';outside.write_bytes(path.read_bytes())
    with pytest.raises(ValueError,match='host-private'):
        load_workflow_authorization(outside,root,logs,candidate,identities)
    state=read(logs/'workflow_state.json');state['role_calls']+=1
    write(logs/'workflow_state.json',state)
    with pytest.raises(ValueError,match='state changed'):
        load_workflow_authorization(path,root,logs,candidate,identities)


def test_accounting_restore_cannot_reset_deadline_or_allowances():
    broker=SimpleNamespace(sessions={'token':{'deadline':123,'max_calls':None,
                                             'inflight':False}})
    data={key:1 for key in ('calls','tokens','prompt_tokens','completion_tokens',
                            'cached_tokens','tool_calls','unmetered_calls')}
    data.update(iterations={'main':62,'018_software_developer':10},errors=['InfrastructureInterrupted'],
                deadline=99999999,max_calls=1000000,started=1)
    restore_session_accounting(broker,'token',data)
    assert broker.sessions['token']['deadline']==123
    assert broker.sessions['token']['max_calls'] is None
    assert broker.sessions['token']['iterations']==data['iterations']
    assert broker.sessions['token']['unmetered_calls']==1
    with pytest.raises(ValueError,match='accounting'):
        restore_session_accounting(broker,'token',{**data,'calls':True})


@pytest.mark.skipif(__import__('os').environ.get('RUN_DOCKER_TESTS')!='1', reason='Explicit Docker restoration check')
def test_real_controller_restores_stage_and_preserves_candidate_bytes(tmp_path, monkeypatch):
    import shutil
    from mini_swe_rsi.broker import Broker
    from mini_swe_rsi.context_budget import ContextBudget
    from mini_swe_rsi.driver import Driver, initial_code

    fixture_spec=importlib.util.spec_from_file_location('resume_http_fixture', ROOT/'tests/test_online_docker.py')
    fixture=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture)
    endpoint=fixture.FixtureModel()
    monkeypatch.setattr('mini_swe_rsi.broker.secrets',lambda:{
        'OPENAI_BASE_URL':endpoint.url,'OPENAI_API_KEY':'fixture-only',
        'LLM_MODEL_NAME':'fixture-model'})
    root=tmp_path/'run'
    broker=Broker(root,context=ContextBudget({'context_window':65536,'max_output_tokens':4096}))
    broker.model='fixture-model';broker.service_identity={'ollama':{'cloud':False}}
    driver=None
    try:
        driver=Driver(root,SimpleNamespace(),broker)
        source=root/'versions/H0000/agent_code';initial_code(source)
        logs=root/'rounds/R0001';target=logs/'candidate_work'
        shutil.copytree(source,target)
        (target/'minisweagent/partial_change.py').write_text('PRESERVE_THIS = True\n')
        prompt=root/'feedback.json'
        feedback={'task_id':'t1','round_id':'R0001','evidence_refs':['tasks/t1/trace.txt']}
        write(prompt,feedback)
        flow=w.Workflow(logs,lambda *_:{'exit_status':'Submitted','submission':'archive evidence'},
                        feedback=feedback)
        flow.dispatch({'op':'invoke'})
        for _ in range(2):
            flow.call_role('archive_explorer','evidence')
            flow.dispatch({'op':flow.current()['completion_tool'],'report':'evidence',
                           'evidence_refs':feedback['evidence_refs']})
        flow.dispatch({'op':'diagnosis_decision','decision':'improve','reason':'repair mechanism',
                       'improvement_opportunities':['repair'],'evidence_refs':feedback['evidence_refs']})
        flow.counter=18;flow.save()
        invocation=flow.current()['invocation_id'];before=tree_hash(target)
        authorization=root/'private/authorization.json'
        write(authorization,{'schema':'online-workflow-resume-v1',
              'state':read(logs/'workflow_state.json'),'state_sha256':digest(logs/'workflow_state.json'),
              'identities':{'task_id':'t1','round_id':'R0001','version':'H0000',
                            'parent':0,'proposer':0,'candidate':'P0001'},
              'candidate_source_sha256':before,'committee_cache_seed':[]})
        token=broker.session('restore-fixture',seconds=None)
        status,_=driver.controller(source,logs,
            ['improve','--task-id','t1','--round-id','R0001','--candidate-id','P0001',
             '--parent-iter','0',
             '--online-version','H0000','--prompt-file','/task.txt'],
            token,target=target,prompt=prompt,resume_checkpoint=authorization)
        metrics=broker.finish(token)
        assert status==0
        assert metrics['calls']==1
        saved=read(logs/'workflow_state.json')
        assert saved['completed_steps']==2 and saved['role_calls']==18
        assert saved['active']['invocation_id']==invocation
        assert saved['active']['index']==2 and saved['status']=='incomplete'
        assert tree_hash(target)==before
        assert read(logs/'improvement_verification.json')['status']=='verified'
        assert read(logs/'improvement_task.json')['resume_authorization_sha256']==digest(authorization)
        assert len(endpoint.requests)==1
    finally:
        if driver:driver.close()
        broker.close();endpoint.close()


def test_explicit_step5_resume_binds_frozen_candidate(tmp_path):
    import shutil
    root,logs,candidate,identities,path=authorization_fixture(tmp_path)
    state=read(logs/'workflow_state.json')
    state['active']['index']=4
    state['completed_steps']=4
    state['check_plan']={'criterion':'current_task_failure_fixed'}
    write(logs/'workflow_state.json',state)
    auth=read(path)
    auth.update(state=state,state_sha256=digest(logs/'workflow_state.json'))
    write(path,auth)
    with pytest.raises(ValueError,match='explicit incomplete-check'):
        load_workflow_authorization(path,root,logs,candidate,identities)
    auth['resume_incomplete_check']=True
    write(path,auth)
    with pytest.raises(ValueError,match='frozen candidate'):
        load_workflow_authorization(path,root,logs,candidate,identities)
    shutil.copytree(candidate,logs/'candidate_check_source')
    assert load_workflow_authorization(path,root,logs,candidate,identities)['state']['completed_steps']==4
    (logs/'candidate_check_source/partial.py').write_text('tampered')
    with pytest.raises(ValueError,match='frozen candidate'):
        load_workflow_authorization(path,root,logs,candidate,identities)


def test_restore_still_rejects_completed_candidate_check(tmp_path):
    root,logs,candidate,identities,path=authorization_fixture(tmp_path)
    state=read(logs/'workflow_state.json');state['candidate_check']={'result':{'passed':True}}
    write(logs/'workflow_state.json',state)
    auth=read(path);auth.update(state=state,state_sha256=digest(logs/'workflow_state.json'))
    write(path,auth)
    with pytest.raises(ValueError,match='unfinished workflow'):
        load_workflow_authorization(path,root,logs,candidate,identities)


def test_candidate_check_uses_second_attempt_evidence(tmp_path, monkeypatch):
    from mini_swe_rsi.driver import Driver
    import mini_swe_rsi.driver as module
    code=tmp_path/'code';code.mkdir();(code/'agent.py').write_text('pass\n')
    output=tmp_path/'check'
    record={'score':1.0,'problem_id':'t1'}
    write(output/'attempt_1/host_result.json', {
        'record':record,'result':{'exit_status':'Submitted'},
            'grade':{'resolved':True},'metrics':{'calls':7},
            'code_hash':tree_hash(code)})
    monkeypatch.setattr(module,'validate_code',lambda _:None)
    driver=object.__new__(Driver);driver.root=tmp_path
    monkeypatch.setattr(driver,'task_retry',lambda *a,**k:record)
    result=driver.candidate_check(code,'t1',output,job_id='retry-job',parent_hash=tree_hash(code))
    assert result['execution_status']=='Submitted'
    assert result['metrics']['calls']==7
    private=read(tmp_path/'private/candidate_checks/retry-job.json')
    assert private['grade']['resolved'] is True
    assert private['evidence_path']==str(output/'attempt_1/host_result.json')
