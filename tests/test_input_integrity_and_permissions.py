"""Original role boundaries and bound step tools."""
import importlib.util
import json

import pytest

from mini_swe_rsi.core import ROOT

spec = importlib.util.spec_from_file_location('permission_fixture', ROOT/'src/mini_swe_rsi/workflow/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def command(req):
    return {'command': 'sica-workflow ' + json.dumps(req, ensure_ascii=False)}


def test_online_role_boundaries_and_archive_overview_permissions(tmp_path):
    expected = {
        'orchestrator':{'invoke','state','complete','read','list','delegate','committee'},
        'archive_explorer':{'read','list','archive'},
        'reviewer':{'read','list','search'},
        'software_developer':{'read','list','search','committee'},
        'general_problem_solver':{'read','list','search','committee','delegate'}}
    for role, operations in expected.items():
        assert w.role_capabilities(role)['operations'] == operations
        assert ('Online archive analysis:' in w.instructions(role)) == (role=='archive_explorer')
    assert w.role_capabilities('general_problem_solver')['delegates']=={'software_developer'}
    flow=w.Workflow(tmp_path,lambda *a:pytest.fail('Unauthorized delegate'), feedback={'task_id':'task','round_id':'R0001','evidence_refs':['tasks/task/trace.txt']})
    req={'op':'archive','kind':'overview','role':'archive_explorer'}
    for role in ('orchestrator','reviewer','software_developer','general_problem_solver'):
        assert flow.execute(command(req),True,role)['returncode']==1
    assert flow.execute(command(req),True,'archive_explorer')['returncode']==0
    assert flow.execute(command({'op':'delegate','role':'archive_explorer','task':'x'}),'editor','general_problem_solver')['returncode']==1


def test_bound_completion_rejects_wrong_stale_names_and_ids(tmp_path):
    flow=w.Workflow(tmp_path,lambda *a:None, feedback={'task_id':'task','round_id':'R0001','evidence_refs':['tasks/task/trace.txt']})
    current=flow.dispatch({'op':'invoke'})
    tool=current['completion_tool']
    assert len(tool.split('_')[1])==4  # Original _make_id uses four hex digits.
    for req in ({'op':'complete'}, {'op':'complete','tool':'meta_improvement_reasoning_structure'},
                {'op':tool,'invocation_id':'stale'}, {'op':tool,'tool':'wrong'}):
        assert flow.execute(command(req),'orchestrator','orchestrator')['returncode']==1
    assert flow.completed_steps==0
    assert flow.execute(command({'op':tool,'success':True}),True,'reviewer')['returncode']==1
    assert flow.execute(command({'op':tool,'success':True}),'orchestrator','orchestrator')['returncode']==1
    assert flow.completed_steps==0










def test_parameter_order_is_not_service_identity_drift():
    from mini_swe_rsi.broker import canonical_parameters
    first='temperature 1\nstop "  literal  spaces  "\nnum_ctx   65536\n'
    second='num_ctx 65536\nstop "  literal  spaces  "\ntemperature   1\n'
    assert canonical_parameters(first)==canonical_parameters(second)
    assert '"  literal  spaces  "' in canonical_parameters(first)
    assert canonical_parameters(first)!=canonical_parameters(second.replace('65536','32768'))


def test_original_completion_arguments():
    import ast
    tree=ast.parse((ROOT/'src/mini_swe_rsi/workflow/sources/sequential.py').read_text())
    cls=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='SequentialReasoningStep')
    assert {n.target.id for n in cls.body if isinstance(n,ast.AnnAssign)}=={'success'}
