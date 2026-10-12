"""Immutable entrypoint; all editable agent/model modules load from the selected archive."""
import argparse
import ast
import json
import hashlib
import os
import platform
import sys
import time
import urllib.request
from pathlib import Path

import yaml


def unbounded_observation(template):
    # Remove only the known mini observation budget branch; preserve candidate additions.
    import re
    pattern = r'{%-?\s*if output\.output \| length < 10000\s*-?%}.*?{%-?\s*endif\s*-?%}'
    return re.sub(pattern, '<output>{{output.output}}</output>', template, flags=re.S)


def budget_config():
    return json.loads(os.environ['RSI_BUDGET_CONFIG'])


def rpc(path, value, identity='main', role='orchestrator'):
    request = urllib.request.Request(os.environ['RSI_BROKER'] + path, json.dumps(value).encode(),
              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['RSI_TOKEN'],
                       'X-RSI-Agent':identity, 'X-RSI-Role':role})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=budget_config()['tool_timeout_seconds'] + 30) as f:
        return json.load(f)


def classified_failure(exc, status):
    reason = status.get('terminal_reason')
    if reason not in ('LimitsExceeded','TimeExceeded','ContextLimitExceeded','CostExceeded'):
        reason = type(exc).__name__
    return {'exit_status':reason, 'raw_exception':{'type':type(exc).__name__, 'message':str(exc)}, 'submission':''}


def improvement_prompt(best, current, parent, candidate, upstream=Path("/upstream/agent_main.py"), calls=500, budget=None):
    tree = ast.parse(Path(upstream).read_text())
    func = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'improve_agent')
    # Preserve the upstream task intent; adapt tool terminology and execution identities.
    original = next(n for n in ast.walk(func) if isinstance(n, ast.JoinedStr) and any(
        isinstance(v, ast.Constant) and 'It is meta-improvement time.' in str(v.value) for v in n.values))
    text = ''
    for value in original.values:
        if isinstance(value, ast.Constant):
            text += str(value.value)
        else:
            name = value.value.id
            text += str({'best_iter': best, 'current_iter': current,
                         'testing_procedure': 'You can inspect and syntax-check Python locally. Benchmark grading is host-controlled.'}[name])
    text = text.replace('/home/sandbox/workdir', '/home/agent/workdir')
    text = text.replace('invoke the meta improvement reasoning structure as early as reasonably possible',
                        'invoke the meta improvement reasoning structure as early as reasonably possible')
    text = text.replace('using the archive exploration tools available to you', 'using structured archive analysis and read/list/search operations')
    text = text.replace(
        f'The agent code in this directory is the best performing in the sequence of agent iterations, and corresponds to agent iteration {best}.',
        f'The code in /home/agent/workdir is the current unassessed candidate {candidate}, copied from selected parent agent_{parent}. '
        f'The executing improver is agent_{best}; it may differ from the parent. '
        'The parent is the selected starting version, not necessarily the best performing version.')
    text = text.replace('combine with the current best performing agent.', 'combine with the selected starting candidate.')
    text = text.replace('While you have the best performing agent in /home/agent/workdir,',
                        'While /home/agent/workdir contains the writable candidate copied from the selected parent,')
    text += (f'\nTask identities: parent=agent_{parent}; proposer=agent_{best}; candidate={candidate}.\n'
             'Only /home/agent/workdir is the live writable candidate. /home/agent/archive is a read-only frozen startup snapshot; '
             'any copy of the current candidate there is its initial snapshot, not live edits or an evaluated improvement. '
             'The current candidate has not been benchmarked and has no established performance benefit.\n'
             'Budget authority: each agent invocation has its own iteration limit, with up to five provider requests per completion. '
             'The benchmark has a 600-second task deadline; improvement has no separate task deadline and remains subject to the host global/stage deadline.\n')
    if budget is not None:
        text += '\nFrozen execution budget: ' + json.dumps(budget, sort_keys=True) + '\n'
    import importlib.util
    spec = importlib.util.spec_from_file_location('rsi_workflow', Path(__file__).with_name('workflow.py'))
    workflow_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow_module)
    return workflow_module.core_prompt('main_orchestrator', {'problem_statement':text}) + workflow_module.identity_context(
        {'parent':parent,'proposer':best,'candidate':candidate,'workdir':'/home/agent/workdir','public_archive':'/home/agent/archive'}, {'active':None})


def main(entry):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['benchmark', 'improve'])
    parser.add_argument('--prompt-file')
    parser.add_argument('--logdir', required=True)
    parser.add_argument('--workdir', default='/home/agent/workdir')
    parser.add_argument('--best-iter', type=int, default=0)
    parser.add_argument('--parent-iter', type=int, required=False)
    parser.add_argument('--candidate-id')
    parser.add_argument('--current-iter', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--cost-threshold', default=3)
    args = parser.parse_args()
    budgets = budget_config()
    main_iterations = budgets['main_iterations']
    wall_seconds = budgets['improvement_wall_seconds' if args.command == 'improve' else 'benchmark_wall_seconds']
    if args.command == 'improve':
        # Rejected shared-budget requests must not enter archived transport retry backoff.
        os.environ['MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT'] = '1'
    if args.command == 'improve' and (args.parent_iter is None or not args.candidate_id):
        raise ValueError('Improvement requires explicit parent and candidate identities')
    code = Path(entry).resolve().parent
    sys.path.insert(0, str(code))
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
    workflow = None
    class TaskEnvironment(LocalEnvironment):
        readonly = False
        identity = 'main'
        role = 'orchestrator'
        def execute(self, action, **kwargs):
            out = workflow.execute(action, self.readonly) if workflow else None
            if out is None:
                out = rpc('/tool', action, self.identity, self.role)
            if workflow:
                out['session_status'] = rpc('/status', {}, self.identity, self.role)
            out.setdefault('exception_info', '')
            self._check_finished(out)
            return out
        def get_template_vars(self, **kwargs):
            return {**super().get_template_vars(**kwargs),
                    'cwd': '/testbed' if args.command == 'benchmark' else args.workdir,
                    'machine': 'x86_64' if args.command == 'benchmark' else platform.machine()}
    config = yaml.safe_load((code / 'minisweagent/config/mini_textbased.yaml').read_text())
    selected_config = config
    if args.command == 'benchmark':
        config['model']['observation_template'] = unbounded_observation(config['model']['observation_template'])
    workflow_module = None
    if args.command == 'improve':
        import importlib.util
        spec = importlib.util.spec_from_file_location('rsi_workflow', Path(__file__).with_name('workflow.py'))
        workflow_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_module)
        templates = workflow_module.template_manifest(selected_config, main_iterations)
        config = workflow_module.role_templates(selected_config, 'orchestrator', main_iterations)
    config['agent'].pop('mode', None)
    config['agent'].update(step_limit=main_iterations, cost_limit=0, wall_time_limit_seconds=wall_seconds,
                           output_path=Path(args.logdir) / 'trajectory.json')
    model_config = config['model']
    model_config.update(model_name='openai/' + os.environ['RSI_MODEL'], cost_tracking='ignore_errors')
    model_config['model_kwargs'].update(api_base=os.environ['RSI_BROKER'] + '/v1',
                    api_key=os.environ['RSI_TOKEN'], temperature=0, max_tokens=budgets['model_max_output_tokens'],
                    num_retries=0, timeout=budgets['global_remaining_seconds'],
                    extra_headers={'X-RSI-Agent':'main','X-RSI-Role':'orchestrator'})
    environment = TaskEnvironment(
        cwd='/testbed' if args.command == 'benchmark' else args.workdir, timeout=budgets['command_default_seconds'],
        env=config['environment'].get('env', {}))
    agent = DefaultAgent(LitellmTextbasedModel(**model_config), environment, **config['agent'])
    logs = Path(args.logdir)
    logs.mkdir(parents=True, exist_ok=True)
    # This evidence is supplementary; host independently records the mounted source digest and model calls.
    activation = {'agent_file': str(Path(sys.modules[DefaultAgent.__module__].__file__).resolve()),
                  'budget':budgets, 'step_limit':main_iterations, 'wall_time_limit_seconds':wall_seconds,
                  'model_file': str(Path(sys.modules[LitellmTextbasedModel.__module__].__file__).resolve()),
                  'config_file': str(code / 'minisweagent/config/mini_textbased.yaml'),
                  'platform': platform.uname()._asdict(),
                  'command': args.command,
                  'template_manifest': templates if args.command == 'improve' else None,
                  'tool_capability':'orchestrator' if args.command == 'improve' else 'bash'}
    (logs / 'activation.json').write_text(json.dumps(activation))
    if args.command == 'improve':
        task = improvement_prompt(args.best_iter, args.current_iter, args.parent_iter, args.candidate_id, calls=main_iterations, budget=budgets)
        metadata = {'protocol': os.environ['RSI_IMPROVEMENT_PROTOCOL'], 'parent': args.parent_iter, 'proposer': args.best_iter,
                    'candidate': args.candidate_id, 'prompt_sha256': hashlib.sha256(task.encode()).hexdigest(),
                    'renderer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'upstream_task_sha256': hashlib.sha256(Path('/upstream/agent_main.py').read_bytes()).hexdigest(),
                    'relay_sha256':hashlib.sha256(Path(__file__).with_name('relay.py').read_bytes()).hexdigest(),
                    'workflow_sha256':hashlib.sha256(Path(__file__).with_name('workflow.py').read_bytes()).hexdigest(),
                    'template_manifest':templates,
                    'workflow_sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('/workflow_sources').glob('*')) if p.is_file()}}
        (logs / 'improvement_task.json').write_text(json.dumps(metadata))
        (logs / 'improvement_prompt.txt').write_text(task)
    else:
        task = Path(args.prompt_file).read_text()
    if args.command == 'improve':
        import importlib.util
        spec = importlib.util.spec_from_file_location('rsi_workflow', Path(__file__).with_name('workflow.py'))
        workflow_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_module)
        identities = {'parent':args.parent_iter, 'proposer':args.best_iter, 'candidate':args.candidate_id,
                      'workdir':args.workdir, 'public_archive':'/home/agent/archive'}
        def failure(exc):
            try:
                status = rpc('/status', {})
            except Exception as status_exc:
                status = {'status_unavailable':type(status_exc).__name__}
            result = classified_failure(exc, status)
            result['session_status'] = status
            return result
        def delegate(identity, prompt, readonly):
            child_logs = logs / 'roles' / identity
            child_logs.mkdir(parents=True)
            role = identity.split('_', 1)[1]
            adapted = workflow_module.role_templates(selected_config, role, main_iterations)
            child_config = dict(adapted['agent'])
            child_config.pop('mode', None)
            child_model_config = {**model_config, **{k:adapted['model'][k] for k in ('observation_template','format_error_template')}}
            # SICA gives each invocation an independent iteration allowance; global time stays host-owned.
            child_config.update(step_limit=budgets['role_iterations'].get(role, budgets['main_iterations']), cost_limit=0, wall_time_limit_seconds=wall_seconds,
                                output_path=child_logs/'trajectory.json')
            child_environment = TaskEnvironment(cwd=args.workdir, timeout=budgets['command_default_seconds'],
                               env=config['environment'].get('env', {}))
            child_model_config['model_kwargs'] = {**model_config['model_kwargs'], 'extra_headers':{'X-RSI-Agent':identity,'X-RSI-Role':role}}
            child_environment.identity, child_environment.role = identity, role
            child_environment.readonly = True if readonly else 'editor'
            child = DefaultAgent(LitellmTextbasedModel(**child_model_config), child_environment, **child_config)
            (child_logs/'activation.json').write_text(json.dumps({**activation, 'role':identity,
                          'tool_capability':'read_only' if readonly else 'editing', 'shared_broker_session':True,
                          'template_sha256':templates['roles'][role], 'identities':identities,
                          'step_limit':child_config['step_limit'], 'wall_time_limit_seconds':wall_seconds}))
            try:
                result = child.run(prompt, session_status=rpc('/status', {}, identity, role))
            except Exception as exc:
                result = failure(exc)
            finally:
                child.save(child_logs/'trajectory.json')
            (child_logs/'result.json').write_text(json.dumps(result))
            return result
        workflow = workflow_module.Workflow(logs, delegate, identities=identities)
        environment.readonly = 'orchestrator'
    start = time.time()
    try:
        result = agent.run(task, session_status=rpc('/status', {})) if workflow else agent.run(task)
    except Exception as exc:
        result = failure(exc) if workflow else {'exit_status': type(exc).__name__, 'submission': ''}
    finally:
        agent.save(logs / 'trajectory.json')
    if workflow:
        workflow.finish(result)
    (logs / 'answer.txt').write_text(result.get('submission', ''))
    (logs / 'result.json').write_text(json.dumps(result))
    trace = '\n\n'.join(str(m.get('content', '')) for m in agent.messages)
    (logs / 'trace.txt').write_text(trace)
    (logs / 'execution_tree.txt').write_text(trace)
    print(f'0|0|0|{time.time()-start:.3f}')
    if result.get('exit_status') != 'Submitted':
        raise SystemExit(1)
