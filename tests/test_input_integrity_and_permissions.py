"""Original role boundaries, bound step tools and honest input accounting."""
import hashlib
import importlib.util
import io
import json

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.context_budget import ContextBudget, LOCK_PATH
from mini_swe_rsi.core import ROOT, read
from tests.event_helpers import broker_events

spec = importlib.util.spec_from_file_location('permission_fixture', ROOT/'runtime/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def command(req):
    import shlex
    return {'command':'sica-workflow '+shlex.quote(json.dumps(req))}


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


@pytest.fixture
def audited_broker(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets',lambda:dict(OPENAI_BASE_URL='https://fixture/v1',OPENAI_API_KEY='fixture',LOCAL_LLM_MODEL='fixture'))
    context=ContextBudget({'context_window':65536,'max_output_tokens':65535})
    context.enable_verified_text_audit(read(LOCK_PATH))
    broker=Broker(tmp_path,context);broker.model='fixture'
    yield broker
    broker.close()


def test_oversize_rejected_before_request_and_output_limit_unchanged(audited_broker):
    b=audited_broker
    b.opener=type('NoRequests',(),{'open':lambda *a,**k:pytest.fail('Oversize input reached provider')})()
    messages=[{'role':'user','content':'historical task. '*50000}]
    m=b.context.measure(messages)
    assert m['host_text_tokens']>65536 and not b.context.fits(m)
    token=b.session('proposal')
    with pytest.raises(ModelError,match='ContextLimitExceeded'):
        b.complete(token,{'messages':messages})
    assert b.finish(token)['calls']==0
    assert b.budget_limits()['model_max_output_tokens']==65535


def test_silent_input_loss_retains_usage_but_never_returns_action(audited_broker):
    b=audited_broker;messages=[{'role':'user','content':'historical task. '*1000}]
    wire=[]
    def open(request,**kw):
        wire.append(json.loads(request.data))
        return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}}).encode())
    b.opener=type('Endpoint',(),{'open':staticmethod(open)})()
    token=b.session('proposal')
    with pytest.raises(ModelError,match='InputIntegrityError'):
        b.complete(token,{'messages':messages})
    metrics=b.finish(token)
    assert metrics['tokens']==110 and metrics['calls']==1 and metrics['unmetered_calls']==0
    assert wire[0]['messages']==messages and wire[0]['max_tokens']==65535
    event = broker_events(b)[-1]
    assert event['input_integrity']['status']=='failed'
    assert 'agent_response' not in event
    assert event['wire_messages_sha256']==hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def test_verified_tokenizer_required_and_framing_is_not_claimed_exact(audited_broker):
    b=audited_broker
    metadata={'tokenizer.ggml.tokens':['a'],'tokenizer.ggml.merges':['a b'],'tokenizer.ggml.token_type':[1]}
    sha=hashlib.sha256(json.dumps(metadata,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    shown={'parameters':'num_ctx 65536\n','template':'{{ .Prompt }}','details':{'family':'qwen35'},'model_info':metadata}
    b.opener=type('Endpoint',(),{'open':staticmethod(lambda *a,**k:io.BytesIO(json.dumps(shown).encode()))})()
    assert b.service_configuration('fixture',{'service_tokenizer_sha256':sha})['service_tokenizer_sha256']==sha
    with pytest.raises(ModelError,match='does not match'):
        b.service_configuration('fixture',{'service_tokenizer_sha256':'wrong'})
    assert b.context.contract['input_integrity']['exact_chat_template'] is False
    assert b.context.contract['total_tokens']==65536


def test_parameter_order_is_not_service_identity_drift():
    from mini_swe_rsi.broker import canonical_parameters
    first='temperature 1\nstop "  literal  spaces  "\nnum_ctx   65536\n'
    second='num_ctx 65536\nstop "  literal  spaces  "\ntemperature   1\n'
    assert canonical_parameters(first)==canonical_parameters(second)
    assert '"  literal  spaces  "' in canonical_parameters(first)
    assert canonical_parameters(first)!=canonical_parameters(second.replace('65536','32768'))


def test_original_completion_arguments_and_archive_defaults():
    import ast
    tree=ast.parse((ROOT/'vendor/sica_workflow/sequential.py').read_text())
    cls=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='SequentialReasoningStep')
    assert {n.target.id for n in cls.body if isinstance(n,ast.AnnAssign)}=={'success'}
    tree=ast.parse((ROOT/'.upstream/sica/base_agent/src/tools/archive_tools.py').read_text())
    for name in ('BestProblems','WorstProblems'):
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
        field=next(n.value for n in cls.body if isinstance(n,ast.AnnAssign) and n.target.id=='count')
        assert {k.arg:ast.literal_eval(k.value) for k in field.keywords if k.arg in ('default','lt')}=={'default':3,'lt':10}
