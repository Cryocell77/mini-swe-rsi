"""Immutable entrypoint; all editable agent/model modules load from the selected archive."""
import argparse
import ast
import functools
import json
import hashlib
import math
import os
import platform
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import yaml


def unbounded_observation(template):
    # Remove only the known mini observation budget branch; preserve candidate additions.
    import re
    pattern = r'{%-?\s*if output\.output \| length < 10000\s*-?%}.*?{%-?\s*endif\s*-?%}'
    return re.sub(pattern, '<output>{{output.output}}</output>', template, flags=re.S)


def budget_config():
    value = json.loads(os.environ['RSI_BUDGET_CONFIG'])
    remaining = value.get('session_remaining_seconds')
    if remaining is not None and (not isinstance(remaining, (int, float))
                                  or isinstance(remaining, bool)
                                  or not math.isfinite(remaining) or remaining <= 0):
        raise ValueError('session_remaining_seconds must be null or a positive finite number')
    return value


def model_request_timeout(budget):
    """Resolve the SDK timeout without turning an unbounded host budget into 600s."""
    remaining = budget.get('session_remaining_seconds')
    return httpx.Timeout(None) if remaining is None else remaining


def apply_model_request_timeout(model, budget):
    """Inject an unbounded timeout only at the SDK call boundary.

    The model config remains JSON/Pydantic serializable for trajectory saves;
    the ``httpx.Timeout`` object lives only in the bound request method.
    """
    if budget.get('session_remaining_seconds') is None:
        model._query = functools.partial(model._query, timeout=model_request_timeout(budget))
    return model


def load_resume_authorization(path, logs, expected_identity, workdir, workflow_module):
    """Validate the host-only online workflow continuation authorization."""
    path = Path(path)
    logs = Path(logs)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Resume authorization must be a regular file')
    try:
        authorization_bytes = path.read_bytes()
        authorization = json.loads(authorization_bytes)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('Resume authorization is unreadable') from exc
    required = {'schema', 'state_sha256', 'state', 'identities', 'candidate_source_sha256'}
    if (not isinstance(authorization, dict) or not required.issubset(authorization)
            or authorization.get('schema') != workflow_module.RESUME_SCHEMA):
        raise ValueError('Invalid online workflow resume authorization schema')
    if (not isinstance(authorization['state_sha256'], str)
            or not isinstance(authorization['candidate_source_sha256'], str)):
        raise ValueError('Resume authorization hashes are invalid')
    state_path = logs/'workflow_state.json'
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError('Resume authorization has no original workflow state')
    state_bytes = state_path.read_bytes()
    state_sha256 = hashlib.sha256(state_bytes).hexdigest()
    if authorization['state_sha256'] != state_sha256:
        raise ValueError('Resume workflow state hash mismatch')
    try:
        state = json.loads(state_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError('Original workflow state is unreadable') from exc
    if authorization['state'] != state:
        raise ValueError('Resume authorization state differs from original state')
    active = state.get('active') if isinstance(state, dict) else None
    if (not isinstance(active, dict) or type(active.get('index')) is not int
            or not 0 <= active['index'] <= 4
            or state.get('completed_steps') != active['index']
            or state.get('status') not in ('active', 'diagnosis_pending', 'improving')
            or state.get('candidate_check') is not None):
        raise ValueError('Resume authorization does not identify an active pre-check workflow')
    if active['index'] == 4 and (
            authorization.get('resume_incomplete_check') is not True
            or not isinstance(state.get('check_plan'), dict)):
        raise ValueError('Step-5 restore requires explicit incomplete-check authorization')
    identities = authorization['identities']
    if not isinstance(identities, dict) or set(identities) != set(expected_identity):
        raise ValueError('Resume authorization identities are invalid')
    for key, expected in expected_identity.items():
        if identities.get(key) != expected:
            raise ValueError(f'Resume authorization identity mismatch: {key}')
    source_hash = workflow_module.candidate_source_hash(workdir)
    if authorization['candidate_source_sha256'] != source_hash:
        raise ValueError('Resume candidate source hash mismatch')
    cache_seed = authorization.get('committee_cache_seed', [])
    if not isinstance(cache_seed, list):
        raise ValueError('Resume committee cache seed is invalid')
    return {'state':state, 'committee_cache_seed':cache_seed, 'identities':identities,
            'candidate_source_sha256':authorization['candidate_source_sha256'],
            'authorization_sha256':hashlib.sha256(authorization_bytes).hexdigest()}


def _resume_context(state, source_exists=True):
    """Render the fixed continuation context shared by host and container."""
    if not isinstance(state, dict):
        raise ValueError('Resume authorization state is invalid')
    active = state.get('active') or {}
    diagnosis = state.get('diagnosis')
    reports = diagnosis.get('reports', {}) if isinstance(diagnosis, dict) else {}
    summaries = []
    for key in ('1', '2'):
        report = reports.get(key, {}) if isinstance(reports, dict) else {}
        if isinstance(report, dict):
            summaries.append(f'step {key}: {str(report.get("report", ""))[:1200]}')
    source_text = 'present' if source_exists else 'missing'
    return ('\nEXPLICIT HOST-AUTHORIZED WORKFLOW RESUME. This is a new model context; '
            'the prior model conversation is not resumed. The candidate source is '
            + source_text + '. The saved workflow is already at stage '
            + str(active.get('index')) + ' with '
            + str(state.get('completed_steps')) + ' completed steps and status '
            + str(state.get('status')) + '. Completed diagnostic summary:\n'
            + ('\n'.join(summaries) if summaries else '(none)')
            + '\nDo not invoke a new reasoning structure and do not repeat completed steps. '
              'First call the current state operation, then continue from its returned stage.\n')


def resume_prompt(base_task, authorization):
    """Add the host-authorized continuation context to a rendered task.

    The helper is deliberately importable through ``runpy`` so the host can
    render the same bytes before starting the container.  It is idempotent to
    support callers that already passed ``resume_state`` to ``improvement_prompt``.
    """
    if not isinstance(base_task, str):
        raise ValueError('Resume prompt base task must be text')
    if not isinstance(authorization, dict) or not isinstance(authorization.get('state'), dict):
        raise ValueError('Resume authorization is invalid')
    marker = '\nEXPLICIT HOST-AUTHORIZED WORKFLOW RESUME.'
    return base_task if marker in base_task else base_task + _resume_context(
        authorization['state'], authorization.get('candidate_source_sha256') is not None)


def rpc(path, value, identity='main', role='orchestrator'):
    request = urllib.request.Request(os.environ['RSI_BROKER'] + path, json.dumps(value).encode(),
              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['RSI_TOKEN'],
                       'X-RSI-Agent':identity, 'X-RSI-Role':role})
    # Candidate validation includes a separate task and grading process.  Its
    # own deadlines remain host-owned; a tool socket deadline must not cancel
    # a completed-but-not-yet-returned check and cause duplicate work.
    timeout = None if path == '/candidate_check' else budget_config()['tool_timeout_seconds'] + 30
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as f:
        return json.load(f)


def classified_failure(exc, status):
    reason = status.get('terminal_reason')
    if reason not in ('LimitsExceeded','TimeExceeded','ContextLimitExceeded','InputIntegrityError','CostExceeded'):
        reason = type(exc).__name__
    return {'exit_status':reason, 'raw_exception':{'type':type(exc).__name__, 'message':str(exc)}, 'submission':''}


def improvement_prompt(best, current, parent, candidate, upstream=Path("/upstream/agent_main.py"),
                       budget=None, task_id=None,
                       round_id=None, version=None, feedback=None,
                       resume_state=None, resume_source_exists=None, workflow_module=None):
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
    text = text.replace('using the archive exploration tools available to you', 'using delegated archive analysis and permitted file inspection')
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
             'The benchmark has a 600-second task deadline; improvement has no separate task deadline and no additional global/stage deadline.\n')
    text += ('\nONLINE SINGLE-TASK MODE. The active Harness version is '+str(version or f'H{parent:04d}')+
             '; executor, proposer, and candidate parent are this same version. '
             'Diagnose only the just-completed public task before considering a change. '
             'After successful steps 1 and 2, call sica-workflow with '
             '{"op":"diagnosis_decision","decision":"skip","reason":"diagnosis conclusion",'
             '"failure_modes":[],"effective_mechanisms":[],"improvement_opportunities":[],'
             '"evidence_refs":["copy a real reference from public feedback"]}. '
             'Use decision="improve" with a nonempty improvement_opportunities list when diagnosis supports a change. '
             'For completing steps 1 and 2, include report and evidence_refs in the current completion operation. '
             'A skip must not call committee, coding, or candidate checks.\n'
             f'Online identity: task_id={task_id}; round_id={round_id}; version={version}.\n')
    check_identity = {'job_id': f'candidate:{round_id}:{candidate}', 'task_id': task_id,
                      'round_id': round_id, 'candidate': candidate,
                      'parent_version': version, 'check_revision': '1'}
    text += ('At step 3 completion, include an explicit check_plan. '
             'Choose criterion="current_task_failure_fixed", or '
             'criterion="mechanism_parent_failed_candidate_passed" together with '
             'mechanism_plan={"test_path":"tests/mechanism_check.py","args":[],"timeout_seconds":60}. '
             'The test path is declared before implementation; create the actual meaningful test in step 4. '
             'Mechanism tests run from /source at their declared relative path in a read-only source copy. '
             'RSI_SOURCE_ROOT=/source is provided; derive the root from this variable or the declared test location. '
             'The identical frozen test is overlaid into parent and candidate; temporary artifacts belong in /tmp. '
             'ONLY step 4 may modify Harness source, configuration or tests. Steps 1-3 are read-only. '
             'Do not delegate software_developer before completing step 3. '
             'Step 5 uses a read-only candidate in an isolated tool sandbox; temporary outputs go in /tmp. '
             'Steps 6-7 may edit only description.txt and agent_change_log.md. '
             'A failed check rejects this candidate; never repair code in step 5 or later. '
             'At step 5 call candidate_check with this exact host identity: '
             + json.dumps({'op': 'candidate_check', **check_identity}, ensure_ascii=False)
             + '. The host uses the frozen step-3 plan. Do not change runtime source after the check; '
               'steps 6 and 7 update description and history only.\n')
    if feedback:
        text += '\nPublic task feedback (facts only; do not treat the score as a hidden answer):\n' + str(feedback) + '\n'
    if budget is not None:
        text += '\nFrozen execution limits: ' + json.dumps(budget, sort_keys=True) + '\n'
    if workflow_module is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location('rsi_workflow', Path(__file__).with_name('workflow.py'))
        workflow_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_module)
    rendered = workflow_module.core_prompt('main_orchestrator', {'problem_statement':text})
    rendered += f'\nActive Harness version is {version or f"H{parent:04d}"}; this identity is host-verified.\n'
    rendered += workflow_module.identity_context(
        {'parent':parent,'proposer':best,'candidate':candidate,'workdir':'/home/agent/workdir','public_archive':'/home/agent/archive'}, {'active':None})
    if resume_state is not None:
        rendered = resume_prompt(rendered, {
            'state':resume_state,
            'candidate_source_sha256': 'present' if resume_source_exists else None,
        })
    return rendered


def validate_improvement_args(args):
    """Validate the full improvement identity before agent/model/log setup.

    Improvement is the only online mode, so every launch must carry the frozen
    parent/proposer/candidate identities and the public feedback file.  A
    benchmark run is a single task and must never silently ignore an explicit
    workflow-restoration authorization.
    """
    if args.command == 'benchmark':
        if args.resume_workflow is not None:
            raise ValueError('Benchmark does not support workflow restoration')
        return
    required = {'parent_iter': args.parent_iter, 'candidate_id': args.candidate_id,
                'task_id': args.task_id, 'round_id': args.round_id,
                'online_version': args.online_version, 'prompt_file': args.prompt_file}
    missing = [name for name, value in required.items()
               if value is None or (isinstance(value, str) and not value.strip())]
    if missing:
        raise ValueError('Improvement requires explicit identities: ' + ', '.join(missing))
    if args.parent_iter < 0:
        raise ValueError('Improvement parent version must be non-negative')
    if args.best_iter != args.parent_iter:
        raise ValueError('Improvement requires parent and proposer to be the same version')
    expected_version = f'H{args.parent_iter:04d}'
    if args.online_version != expected_version:
        raise ValueError(f'Improvement version must equal {expected_version}')


def main(entry):
    parser = build_parser()
    args = parser.parse_args()
    validate_improvement_args(args)
    budgets = budget_config()
    main_iterations = budgets['main_iterations']
    wall_seconds = budgets['improvement_wall_seconds' if args.command == 'improve' else 'benchmark_wall_seconds']
    if args.command == 'improve':
        # Rejected shared-budget requests must not enter archived transport retry backoff.
        os.environ['MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT'] = '1'
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
            out = workflow.execute(action, self.readonly, self.role) if workflow else None
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
        resume_authorization = None
        if args.resume_workflow is not None:
            expected_identity = {
                'task_id': args.task_id,
                'round_id': args.round_id,
                'version': args.online_version,
                'parent': args.parent_iter,
                'proposer': args.best_iter,
                'candidate': args.candidate_id,
            }
            resume_authorization = load_resume_authorization(
                args.resume_workflow, args.logdir, expected_identity, args.workdir, workflow_module)
        templates = workflow_module.template_manifest(selected_config)
        config = workflow_module.role_templates(selected_config, 'orchestrator')
        config['agent']['system_template'] += (
            f'\nactive Harness version is {args.online_version or f"H{args.parent_iter:04d}"}; '
            'the host verifies this identity before accepting a takeover.\n')
    config['agent'].pop('mode', None)
    config['agent'].update(step_limit=main_iterations, cost_limit=0, wall_time_limit_seconds=wall_seconds,
                           output_path=Path(args.logdir) / 'trajectory.json')
    model_config = config['model']
    model_config.update(model_name='openai/' + os.environ['RSI_MODEL'], cost_tracking='ignore_errors')
    model_config['model_kwargs'].update(api_base=os.environ['RSI_BROKER'] + '/v1',
                    api_key=os.environ['RSI_TOKEN'], temperature=0, max_tokens=budgets['model_max_output_tokens'],
                    num_retries=0, timeout=budgets['session_remaining_seconds'],
                    extra_headers={'X-RSI-Agent':'main','X-RSI-Role':'orchestrator'})
    environment = TaskEnvironment(
        cwd='/testbed' if args.command == 'benchmark' else args.workdir, timeout=budgets['command_default_seconds'],
        env=config['environment'].get('env', {}))
    agent_model = apply_model_request_timeout(LitellmTextbasedModel(**model_config), budgets)
    agent = DefaultAgent(agent_model, environment, **config['agent'])
    logs = Path(args.logdir)
    logs.mkdir(parents=True, exist_ok=True)
    # This evidence is supplementary; host independently records the mounted source digest and model calls.
    activation = {'agent_file': str(Path(sys.modules[DefaultAgent.__module__].__file__).resolve()),
                  'budget':budgets, 'step_limit':main_iterations, 'wall_time_limit_seconds':wall_seconds,
                  'model_timeout_contract': {
                      'config_timeout': budgets['session_remaining_seconds'],
                      'request_timeout': ('httpx.Timeout(None)' if budgets['session_remaining_seconds'] is None
                                          else budgets['session_remaining_seconds']),
                      'injected_at_query_boundary': budgets['session_remaining_seconds'] is None,
                  },
                  'model_file': str(Path(sys.modules[LitellmTextbasedModel.__module__].__file__).resolve()),
                  'config_file': str(code / 'minisweagent/config/mini_textbased.yaml'),
                  'platform': platform.uname()._asdict(),
                  'command': args.command,
                  'template_manifest': templates if args.command == 'improve' else None,
                  'tool_capability':'orchestrator' if args.command == 'improve' else 'bash'}
    (logs / 'activation.json').write_text(json.dumps(activation))
    if args.command == 'improve':
        feedback = Path(args.prompt_file).read_text() if args.prompt_file else None
        task = improvement_prompt(args.best_iter, args.current_iter, args.parent_iter, args.candidate_id,
                                  budget=budgets, task_id=args.task_id, round_id=args.round_id,
                                  version=args.online_version, feedback=feedback,
                                  workflow_module=workflow_module)
        if resume_authorization:
            task = resume_prompt(task, resume_authorization)
        metadata = {'protocol': os.environ['RSI_IMPROVEMENT_PROTOCOL'], 'parent': args.parent_iter, 'proposer': args.best_iter,
                    'candidate': args.candidate_id, 'prompt_sha256': hashlib.sha256(task.encode()).hexdigest(),
                    'renderer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'upstream_task_sha256': hashlib.sha256(Path('/upstream/agent_main.py').read_bytes()).hexdigest(),
                    'relay_sha256':hashlib.sha256(Path(__file__).with_name('relay.py').read_bytes()).hexdigest(),
                    'workflow_sha256':hashlib.sha256(Path(__file__).with_name('workflow.py').read_bytes()).hexdigest(),
                    'template_manifest':templates,
                    'workflow_sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('/workflow_sources').glob('*')) if p.is_file()}}
        metadata.update(online=True, task_id=args.task_id, round_id=args.round_id,
                        version=args.online_version)
        if resume_authorization:
            metadata['resume_authorization_sha256'] = resume_authorization['authorization_sha256']
        (logs / 'improvement_task.json').write_text(json.dumps(metadata))
        (logs / 'improvement_prompt.txt').write_text(task)
    else:
        task = Path(args.prompt_file).read_text()
    if args.command == 'improve':
        identities = {'parent':args.parent_iter, 'proposer':args.best_iter, 'candidate':args.candidate_id,
                      'workdir':args.workdir, 'public_archive':'/home/agent/archive',
                      'model':os.environ.get('RSI_MODEL'),
                      'source_execution_hash':workflow_module.execution_hash(args.workdir)}
        identities.update(version=args.online_version, parent_version=args.online_version,
                          executor=args.online_version,
                          task_id=args.task_id, round_id=args.round_id)
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
            adapted = workflow_module.role_templates(selected_config, role)
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
            child_model = apply_model_request_timeout(
                LitellmTextbasedModel(**child_model_config), budgets)
            child = DefaultAgent(child_model, child_environment, **child_config)
            (child_logs/'activation.json').write_text(json.dumps({**activation, 'role':identity,
                          'tool_capability':'read_only' if readonly else 'editing', 'shared_broker_session':True,
                          'template_sha256':templates['roles'][role], 'identities':identities,
                          'step_limit':child_config['step_limit'], 'wall_time_limit_seconds':wall_seconds}))
            try:
                result = child.run(prompt, session_status=rpc('/status', {}, identity, role))
            except Exception as exc:
                result = failure(exc)
                budget_exit = result.get('exit_status') in (
                    'LimitsExceeded', 'TimeExceeded', 'ContextLimitExceeded',
                    'InputIntegrityError', 'CostExceeded')
                if workflow_module.is_model_request_exception(exc) and not budget_exit:
                    (child_logs/'result.json').write_text(json.dumps(result))
                    raise workflow_module.WorkflowTransportError(
                        f'{type(exc).__name__}: {exc}') from exc
            finally:
                child.save(child_logs/'trajectory.json')
            (child_logs/'result.json').write_text(json.dumps(result))
            return result
        feedback_data = {}
        if args.prompt_file:
            try:
                feedback_data = json.loads(Path(args.prompt_file).read_text())
            except (ValueError, OSError):
                feedback_data = {'summary': Path(args.prompt_file).read_text()}
        def candidate_check(request):
            # The host broker resolves the opaque job id to a frozen candidate
            # path.  No path supplied by the model is forwarded.
            # Stage is a host-bound identity field.  Keep it out of the model
            # operation shape, but add it at the authenticated relay boundary
            # so a job registered for another phase cannot be reused.
            return rpc('/candidate_check', {**request, 'stage': 'candidate_check'},
                       'main', 'orchestrator')
        workflow = workflow_module.Workflow(logs, delegate, identities=identities,
                                            feedback=feedback_data,
                                            stage_boundary=lambda: rpc('/stage_boundary', {}),
                                            candidate_check=candidate_check,
                                            resume_state=resume_authorization['state'] if resume_authorization else None,
                                            resume_committee_cache=(resume_authorization['committee_cache_seed']
                                                                    if resume_authorization else None))
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


def build_parser():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('command', choices=['benchmark', 'improve'])
    parser.add_argument('--prompt-file')
    parser.add_argument('--logdir', required=True)
    parser.add_argument('--workdir', default='/home/agent/workdir')
    parser.add_argument('--best-iter', type=int, default=0)
    parser.add_argument('--parent-iter', type=int, required=False)
    parser.add_argument('--candidate-id')
    parser.add_argument('--current-iter', type=int, default=0)
    parser.add_argument('--task-id')
    parser.add_argument('--round-id')
    parser.add_argument('--online-version')
    parser.add_argument('--resume-workflow', type=Path,
                        help='Host-authorized online workflow continuation file')
    return parser
