"""Stage gates and real filesystem boundaries, independent of model compliance."""
import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import ROOT, read, write
from mini_swe_rsi.stage_tools import StageTools, stage_permissions, tool_mounts

spec = importlib.util.spec_from_file_location('stage_policy_workflow', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)


def flow_at_three(tmp_path):
    candidate = tmp_path/'candidate'; candidate.mkdir()
    (candidate/'source.py').write_text('value = 0\n')
    (candidate/'description.txt').write_text('original\n')
    (candidate/'agent_change_log.md').write_text('original\n')
    flow = w.Workflow(tmp_path/'logs', lambda *_: {'exit_status':'Submitted','submission':'evidence'},
                      online=True, identities={'workdir':str(candidate)},
                      feedback={'task_id':'t','round_id':'R0001','evidence_refs':['tasks/t/trace.txt']})
    flow.dispatch({'op':'invoke'})
    for _ in range(2):
        flow.call_role('archive_explorer','inspect')
        flow.dispatch({'op':flow.current()['completion_tool'],'report':'evidence',
                       'evidence_refs':['tasks/t/trace.txt']})
    flow.dispatch({'op':'diagnosis_decision','decision':'improve','reason':'observed failure',
                   'improvement_opportunities':['repair'],'evidence_refs':['tasks/t/trace.txt']})
    return flow, candidate


def test_step_three_blocks_direct_and_nested_editors_without_incrementing_counter(tmp_path):
    flow, candidate = flow_at_three(tmp_path)
    before = flow.counter
    for role in ('software_developer','general_problem_solver'):
        with pytest.raises(ValueError,match='only at step'):
            flow.call_role(role,'premature implementation')
        with pytest.raises(ValueError):
            flow.dispatch({'op':'delegate','role':role,'task':'edit'},role='orchestrator')
    result=flow.execute({'command':'python -c "open(\'source.py\',\'w\').write(\'bad\')"'},'editor','software_developer')
    assert result['returncode']==1
    assert flow.counter==before
    assert (candidate/'source.py').read_text()=='value = 0\n'


def test_step_four_freezes_every_non_document_file_and_step_five_rejects_tampering(tmp_path):
    flow, candidate=flow_at_three(tmp_path)
    flow.dispatch({'op':flow.current()['completion_tool'],'check_plan':{'criterion':'current_task_failure_fixed'}})
    flow.call_role('software_developer','implement')
    (candidate/'source.py').write_text('value = 1\n')
    (candidate/'test_check.py').write_text('assert True\n')
    barrier=[];flow.stage_boundary=lambda:barrier.append(True)
    flow.dispatch({'op':flow.current()['completion_tool']})
    assert barrier and flow.current()['index']==4
    (candidate/'test_check.py').write_text('assert False\n')
    with pytest.raises(ValueError,match='outside step 4'):
        flow.dispatch({'op':'state'})


def test_docs_do_not_invalidate_source_but_all_other_files_do(tmp_path):
    flow,candidate=flow_at_three(tmp_path)
    (candidate/'description.txt').write_text('new description')
    flow.assert_stage_source()
    (candidate/'README.md').write_text('not an allowed documentation output')
    with pytest.raises(ValueError,match='outside step 4'):
        flow.assert_stage_source()


@pytest.mark.parametrize('index',[None,0,1,2])
def test_host_refuses_shell_before_step_four(tmp_path,index):
    write(tmp_path/'workflow_state.json',{'active':{'index':index},'completed_steps':index,'status':'improving'})
    with pytest.raises(ValueError,match='steps 4-7'):
        stage_permissions(tmp_path)


def test_document_mount_cannot_follow_symlink(tmp_path):
    (tmp_path/'description.txt').symlink_to('/etc/passwd')
    with pytest.raises(ValueError,match='symlink'):
        tool_mounts(tmp_path,None,6)


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS')!='1',reason='Explicit real filesystem isolation test')
def test_real_stage_sandbox_denies_writes_and_destroys_background_writer(tmp_path):
    import docker
    client=docker.from_env()
    driver=SimpleNamespace(client=client,image=client.images.get('mini-swe-rsi-runtime:0.1'),running=[])
    logs=tmp_path/'logs'; candidate=tmp_path/'candidate';candidate.mkdir()
    for name in ('source.py','description.txt','agent_change_log.md'):
        (candidate/name).write_text('original\n')
    tools=StageTools(driver,logs,candidate,None)
    def stage(step):
        write(logs/'workflow_state.json',{'active':{'index':step-1},'completed_steps':step-1,'status':'improving'})
    def run(command):
        return subprocess.run(tools.command(command,10),capture_output=True,text=True,timeout=15)
    try:
        stage(4)
        assert run("printf 'modified\n' > source.py").returncode==0
        assert run("(sleep 2; printf bad > source.py) >/tmp/background.log 2>&1 &").returncode==0
        old=tools.container.id
        tools.close() # same barrier used before the workflow freezes step 4
        with pytest.raises(docker.errors.NotFound):client.containers.get(old)
        stage(5)
        for command in ["printf bad > source.py", "python -c \"open('/home/agent/workdir/source.py','w').write('bad')\"",
                        "printf bad > description.txt"]:
            assert run(command).returncode!=0
        assert run('test ! -e /logs && test -z "$RSI_TOKEN" && test -z "$OPENAI_API_KEY"').returncode==0
        assert run("printf artifact > /tmp/output.txt").returncode==0
        assert run('test -f /tmp/output.txt').returncode==0
        assert tools.container.attrs['HostConfig']['NetworkMode']=='none'
        for step in (6,7):
            stage(step)
            assert run("printf documented > description.txt; printf logged > agent_change_log.md").returncode==0
            assert run('printf bad > source.py').returncode!=0
            assert run('printf bad > new_test.py').returncode!=0
        assert (candidate/'source.py').read_text()=='modified\n'
    finally:
        tools.close();client.close()
