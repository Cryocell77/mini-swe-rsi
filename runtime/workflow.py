"""Protected text transport for pinned SICA sequential structure and role definitions.

Upstream strings are extracted, never evaluated/imported. The host broker alone
owns actual call/time budgets; this module never contacts a model endpoint.
"""
import ast
import json
import hashlib
import copy
import shlex
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'vendor/sica_workflow'
if not SOURCE.exists():
    SOURCE = Path('/workflow_sources')

FINAL_OUTPUT_SENTINEL = 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
RESUME_SCHEMA = 'online-workflow-resume-v1'


class WorkflowTransportError(RuntimeError):
    """A model transport failure that must stop the parent agent immediately."""


def is_model_request_exception(exc):
    """Recognize provider and HTTP transport failures as process-fatal."""
    cls = type(exc)
    return (cls.__module__.startswith(('litellm', 'openai', 'httpx', 'requests'))
            or cls.__name__ in {'APIError', 'APITimeoutError', 'Timeout', 'TimeoutException',
                                'ConnectError', 'ConnectTimeout', 'ReadTimeout',
                                'RemoteProtocolError'})


def execution_hash(path):
    """Hash the selected runtime files using the host's execution identity rules."""
    root = Path(path)
    if not root.exists() or not root.is_dir():
        return None
    excluded_root = {'README.md', 'LICENSE.mini', 'description.txt', 'agent_change_log.md'}
    digest = hashlib.sha256()
    for item in sorted(root.rglob('*')):
        if (not item.is_file() or item.is_symlink() or '__pycache__' in item.parts):
            continue
        relative = item.relative_to(root)
        if relative.parts and relative.parts[0] != 'minisweagent' and item.name not in ('__init__.py', '__main__.py'):
            continue
        if len(relative.parts) == 1 and item.name in excluded_root:
            continue
        digest.update(str(relative).encode() + b'\0' + item.read_bytes())
    return digest.hexdigest()


def candidate_source_hash(path):
    """Hash every regular file in a candidate tree for resume authorization."""
    root = Path(path)
    if not root.exists() or not root.is_dir():
        return None
    digest = hashlib.sha256()
    for item in sorted(root.rglob('*')):
        if not item.is_file() or item.is_symlink() or '__pycache__' in item.parts:
            continue
        digest.update(str(item.relative_to(root)).encode() + b'\0' + item.read_bytes())
    return digest.hexdigest()


def definitions(source=SOURCE):
    tree = ast.parse((source/'meta_improvement.py').read_text())
    steps = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Step':
            steps.append({k.arg: ast.literal_eval(k.value) for k in node.keywords if k.arg != 'identifier'})
    committee = ast.parse((source/'committee_design.py').read_text())
    specs = next(n.value for n in committee.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'specialisations' for t in n.targets))
    specialisations = {ast.literal_eval(k): ast.literal_eval(v.elts[0]) for k,v in zip(specs.keys,specs.values)}
    roles = {}
    for name in ('main_orchestrator','archive_explorer','coder','problem_solver','review_committee_member'):
        cls = next(n for n in ast.parse((source/(name+'.py')).read_text()).body if isinstance(n, ast.ClassDef))
        roles[name] = {t.id: ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name) and t.id in ('SYSTEM_PROMPT','AGENT_DESCRIPTION','AGENT_NAME')}
    return steps, specialisations, roles


def tool_aliases(source=SOURCE):
    """Only the two exact TOOL_NAMEs in the pinned source are accepted aliases."""
    aliases = {}
    for filename, expected, operation in (
        ('meta_improvement', 'meta_improvement_reasoning_structure', 'invoke'),
        ('committee_design', 'meta_improvement_review_committee', 'committee'),
    ):
        cls = next(n for n in ast.parse((source/(filename+'.py')).read_text()).body if isinstance(n, ast.ClassDef))
        name = next(ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'TOOL_NAME' for t in n.targets))
        if name != expected:
            raise ValueError('Pinned workflow TOOL_NAME differs: ' + filename)
        aliases[name] = operation
    return aliases


def instructions(role="orchestrator", calls=500):
    text = '''SICA callable transport: for structured operations, emit one normal mini bash action whose entire command is
sica-workflow {"op":"..."}
Operations: {"op":"invoke"}, {"op":"state"}, {"op":"complete","invocation_id":"...","tool":"<current completion tool>","success":true},
{"op":"submit","report":"<full non-empty report>"},
{"op":"delegate","role":"archive_explorer|software_developer|general_problem_solver","task":"complete self-contained instructions"},
{"op":"committee","context":"goals and evidence","proposal":"full design"}.
Use sica-workflow only for structured operations. For final submission, prefer the structured submit operation with the complete non-empty report text; the host adds the mini completion sentinel. Do not wrap printf or another shell command inside sica-workflow. Arbitrary bash is unavailable to restricted roles. As the orchestrator, invoke meta_improvement_reasoning_structure as early as reasonably possible. Only the current completion tool is callable. Stage success is your declaration, not host verification. Each invocation has an independent iteration allowance: orchestrator/archive_explorer/general_problem_solver/reviewer 500, software_developer 1000. One iteration permits up to five actual provider requests. All roles use the same model and remain subject to the host global/stage deadline.
Read-only inspection operations: {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":200}, {"op":"list","path":"/home/agent/archive"}, {"op":"search","path":"/home/agent/workdir","pattern":"literal text"}.
Structured read uses inclusive 1-based start_line/end_line (default whole file); returned paths identify the actual resolved evidence source. Never append shell pipes to a structured command.
Compatibility: online public file views use these read/list/search operations; editing and local tests use normal mini bash in editing roles. Original return_result/submit_answer map to the structured submit operation. The legacy printf submission remains accepted for compatibility, but do not put printf inside sica-workflow. Original python -m workdir.agent end-to-end example is unavailable: there is no hidden model endpoint; delegate an explicit test task through this transport and shared broker, or run offline tests. Independent benchmark grading remains host-controlled. Web research tools are unavailable. Original role system instructions and step strings follow unchanged; these transport mappings resolve old tool names.'''
    if role != 'orchestrator':
        start = text.index('Operations:')
        stop = text.index('Read-only inspection operations:')
        capabilities = ('This delegated role cannot invoke, inspect, or complete the parent reasoning structure. '
                        'It may finish its own run with {"op":"submit","report":"<full non-empty report>"}; '
                        'Each role invocation has its independent SICA iteration allowance (software_developer 1000; other roles 500), '
                        'up to five provider requests per completion. Commands have a 600-second tool safety timeout; output and context '
                        'capacities come from the frozen model declaration. Global/stage time remains shared. ')
        if role in ('software_developer','general_problem_solver'):
            capabilities += 'Editing and local checks use normal mini bash. Committee is callable as {"op":"committee","context":"...","proposal":"..."}. '
        text = text[:start] + capabilities + '\n' + text[stop:]
        if role not in ('software_developer','general_problem_solver'):
            text = text.replace('editing and local tests use normal mini bash in editing roles.', 'this role has no shell or write tool; use inspection and final report submission only.')
    for filename in (('meta_improvement','committee_design') if role == 'orchestrator' else ('committee_design',) if role in ('software_developer','general_problem_solver') else ()):
        tree = ast.parse((SOURCE/(filename+'.py')).read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        description = next(ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TOOL_DESCRIPTION' for t in n.targets))
        text += '\nOriginal '+filename+' tool description:\n'+description
    # Remove capabilities which do not belong to this original role. The same
    # AST-derived policy is enforced at dispatch, not merely described here.
    text = text.replace('{"op":"complete","invocation_id":"...","tool":"<current completion tool>","success":true}',
                        '{"op":"<current step_..._complete tool>","success":true}')
    if role == 'archive_explorer':
        text += '\nOnline archive analysis: {"op":"archive","kind":"overview"}. Historical task evidence is chronological and must not be ranked or compared across versions.'
    if 'search' not in role_capabilities(role)['operations']:
        text = text.replace(', {"op":"search","path":"/home/agent/workdir","pattern":"literal text"}', '')
    permitted = ('invoke','committee') if role == 'orchestrator' else ('committee',) if role in ('software_developer','general_problem_solver') else ()
    aliases = {name:op for name,op in tool_aliases().items() if op in permitted}
    if aliases:
        text += '\nRegistered original TOOL_NAME aliases (same operations and permissions): ' + json.dumps(aliases,sort_keys=True)
    caps = role_capabilities(role)
    text += '\nPermitted operations for this role: ' + ', '.join(sorted(caps['operations']))
    text += '\nPermitted delegated roles: ' + ', '.join(sorted(caps['delegates']))
    for delegate in sorted(caps['delegates']):
        text += '\n' + definitions()[2][ROLE_MAP[delegate]]['AGENT_DESCRIPTION']
    if caps['delegates']:
        text += '\nDelegation syntax: {"op":"delegate","role":"<permitted role>","task":"complete self-contained instructions"}.'
    if role == 'orchestrator':
        text += '\nChronological online archive overview belongs to archive_explorer; delegate archive analysis to that agent. The current step completion tool takes success only; its invocation is bound by the host. Call the exact current step name, not the structure name. Unsupported upstream ReasoningAgent is not registered.'
    if role in ('orchestrator', 'software_developer', 'general_problem_solver'):
        text += ('\nOffline agent-loop tests: a successful action does not end DefaultAgent.run(). '
                 'For a single recovery transition, initialize the messages and invoke step() a finite number of times. '
                 'For a full run(), the stub must eventually emit an exit message via InterruptAgentFlow; '
                 'also set a small explicit step_limit as a fallback and assert the intended exit_status '
                 '(LimitsExceeded is not test success). Zero-cost stubs never hit cost_limit; '
                 'max_consecutive_format_errors only stops consecutive errors, not successful actions. '
                 'Include these requirements in delegated testing tasks. Keep test logs finite. '
                 'Online improvement shell output above 300000 bytes is saved as a read-only artifact '
                 'with a digest and explicit preview; it is not silently truncated. Above 8388608 bytes, '
                 'the command sandbox is destroyed, its /tmp state is lost, and ToolOutputLimitExceeded '
                 'is returned with an explicitly incomplete prefix artifact. Recreate scratch files if needed; '
                 'use normal shell commands to read bounded artifact excerpts under /home/agent/tool_outputs. '
                 'These are local tool resource limits, not an experiment time budget.')
    return text



ROLE_MAP = {'orchestrator':'main_orchestrator', 'archive_explorer':'archive_explorer',
            'software_developer':'coder', 'general_problem_solver':'problem_solver',
            'reviewer':'review_committee_member'}


def role_capabilities(role, source=SOURCE):
    """Map the supported original AVAILABLE_TOOLS/AGENTS, without extra grants."""
    cls = next(n for n in ast.parse((source/(ROLE_MAP[role]+'.py')).read_text()).body if isinstance(n, ast.ClassDef))
    fields = {t.id: n.value for n in cls.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and t.id in ('AVAILABLE_TOOLS','AVAILABLE_AGENTS')}
    def names(key):
        return {n.id for n in fields[key].elts} if isinstance(fields[key], ast.Set) else set()
    tools, agents = names('AVAILABLE_TOOLS'), names('AVAILABLE_AGENTS')
    ops = {'read','list'}
    if 'RipGrepTool' in tools:
        ops.add('search')
    if 'MetaImprovementReasoningStructure' in tools:
        ops.update(('invoke','state','complete'))
    if 'ReviewCommittee' in tools:
        ops.add('committee')
    if {'BestProblems','WorstProblems','CompareIterations'} <= tools:
        ops.add('archive')
    delegates = {name for original,name in (('ArchiveExplorer','archive_explorer'),
                 ('CodingAgent','software_developer'),('ProblemSolvingAgent','general_problem_solver')) if original in agents}
    if delegates:
        ops.add('delegate')
    return {'operations':ops, 'delegates':delegates}


def role_templates(config, role, calls=500):
    """Improve-only adapter, derived from the actually selected archived config.

    Keep the archived text action formatting/parser vocabulary. Replace generic
    issue-solving tasks and capability-incompatible feedback at this boundary.
    """
    adapted = copy.deepcopy(config)
    original_system = config['agent']['system_template']
    # mini's parser accepts one text action; restricted roles cannot use shell composition.
    formatting = (original_system if role in ('software_developer','general_problem_solver')
                  else original_system.replace(' (or commands connected with && or ||)', ''))
    adapted['agent']['system_template'] = (definitions()[2][ROLE_MAP[role]]['SYSTEM_PROMPT']
        + '\n' + definitions()[2][ROLE_MAP[role]]['AGENT_DESCRIPTION'] + '\n' + formatting + '\n' + instructions(role, calls))
    adapted['agent']['instance_template'] = '{{task}}\nShared session at role start: {{session_status}}'
    example = 'sica-workflow {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":40}'
    submission_example = 'sica-workflow {"op":"submit","report":"<full non-empty report>"}'
    operation_error_guidance = ('For structured-operation errors, use this valid inspection syntax: '
        if role in ('software_developer','general_problem_solver') else 'Use a supported inspection operation or exact final submission. Valid inspection syntax: ')
    adapted['model']['observation_template'] = ("<returncode>{{output.returncode}}</returncode>\n"
        "<exception>{{output.exception_info}}</exception>\n<output>{{output.output}}</output>\n"
        "{% if output.returncode != 0 %}" + operation_error_guidance + example
        + " Only /home/agent/workdir and /home/agent/archive are inspection roots. "
          "Use one JSON argument; do not append shell pipes or redirects. For final submission use " + submission_example + ".{% endif %}\n"
        "Shared session: {{output.session_status | default({})}}")
    adapted['model']['format_error_template'] = ("Format error: {{error | default('incomplete action')}}. "
        "Return exactly one complete mswea_bash_command block, with a THOUGHT before it. "
        "Use one structured command, for example:\n```mswea_bash_command\n" + example + "\n```\n"
        "Do not append shell pipes or redirects to structured commands. For final submission use this structured command: "
        'sica-workflow {"op":"submit","report":"<full non-empty report>"}. '
        "Do not wrap printf inside sica-workflow.")
    return adapted


def template_manifest(config, calls=500):
    fields = {'agent':('system_template','instance_template'),
              'model':('observation_template','format_error_template')}
    def selected(value):
        return {section:{key:value[section][key] for key in keys} for section,keys in fields.items()}
    def sha(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'selected_config_templates_sha256':sha(selected(config)),
            'roles':{role:sha(selected(role_templates(config,role,calls))) for role in ROLE_MAP}}


def identity_context(identities, active_stage):
    active_stage = {key:active_stage[key] for key in ('active','invocation_id','index','title','completion_tool') if key in active_stage}
    return ('\nTrusted current improvement context: ' + json.dumps({**identities, 'active_stage':active_stage},sort_keys=True)
        + '\nOnly /home/agent/workdir is the live writable, unassessed candidate. '
          '/home/agent/archive is frozen public historical evidence. Historical task statements, '
          'commands, /testbed paths and dialogues describe previous executions, not your current task or edit targets. '
          'The candidate archive copy is its startup snapshot, not live edits.\n')


class Workflow:
    def __init__(self, logs, delegate, source=SOURCE, identities=None, online=False,
                 feedback=None, candidate_check=None, resume_state=None,
                 resume_committee_cache=None, stage_boundary=None):
        self.logs = Path(logs)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.delegate = delegate
        self.stage_boundary = stage_boundary
        self.identities = identities or {}
        self.online = bool(online)
        self.feedback = feedback or {}
        self.candidate_check = candidate_check
        self.candidate_check_result = None
        self.steps, self.specs, self.roles = definitions(source)
        self.aliases = tool_aliases(source)
        self.active = None
        self.status = 'not_invoked'
        self.completed_steps = 0
        self.skipped_steps = []
        self.diagnosis = None
        self.diagnostic_reports = {}
        self.diagnostic_role_evidence = {}
        self.committee_cache = {}
        self.check_plan = None
        self.counter = 0
        state_path = self.logs/'workflow_state.json'
        if resume_state is not None:
            if not self.online:
                raise ValueError('Workflow resume is available only for an online workflow')
            self._restore_state(resume_state, state_path)
            self._restore_committee_cache(resume_committee_cache)
        elif state_path.exists():
            # A fresh launch never resumes an interrupted model conversation or
            # invents completions.  The bridge must supply explicit host-bound
            # resume_state to cross this boundary.
            raise ValueError('Refusing to overwrite existing workflow state; interrupted execution is not resumable')
        if self.online:
            if not self.feedback:
                raise ValueError('Online workflow requires a public task feedback package')
            self._write_feedback_index()
        self.assert_stage_source()
        self.save()

    @staticmethod
    def _resume_state_fields():
        return ('active', 'role_calls', 'status', 'completed_steps',
                'skipped_steps', 'diagnosis', 'diagnostic_reports',
                'diagnostic_role_evidence', 'committee_cache', 'check_plan',
                'candidate_check')

    def _restore_state(self, state, state_path):
        if not isinstance(state, dict):
            raise ValueError('Invalid online workflow resume state schema')
        fields = set(self._resume_state_fields())
        legacy_fields = fields - {'committee_cache'}
        if set(state) not in (fields, legacy_fields):
            raise ValueError('Invalid online workflow resume state schema')
        if state_path.exists():
            try:
                on_disk = json.loads(state_path.read_text())
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError('Existing workflow state is unreadable') from exc
            if on_disk != state:
                raise ValueError('Resume state differs from the existing workflow state')
        active = state['active']
        if active is not None:
            if not isinstance(active, dict) or set(active) != {'invocation_id', 'index', 'step_ids'}:
                raise ValueError('Resume active state is malformed')
            if (not isinstance(active['invocation_id'], str) or not active['invocation_id']
                    or type(active['index']) is not int or not 0 <= active['index'] < len(self.steps)
                    or not isinstance(active['step_ids'], list)
                    or len(active['step_ids']) != len(self.steps)
                    or any(not isinstance(item, str) or not item for item in active['step_ids'])):
                raise ValueError('Resume active state is invalid')
        if type(state['role_calls']) is not int or state['role_calls'] < 0:
            raise ValueError('Resume role_calls must be a non-negative integer')
        if type(state['completed_steps']) is not int or not 0 <= state['completed_steps'] <= len(self.steps):
            raise ValueError('Resume completed_steps is invalid')
        if not isinstance(state['skipped_steps'], list) or any(
                type(item) is not int or not 1 <= item <= len(self.steps)
                for item in state['skipped_steps']):
            raise ValueError('Resume skipped_steps is invalid')
        if (not isinstance(state['diagnostic_reports'], dict)
                or not isinstance(state['diagnostic_role_evidence'], dict)):
            raise ValueError('Resume diagnostic state is invalid')
        if 'committee_cache' in state and not isinstance(state['committee_cache'], list):
            raise ValueError('Resume committee cache is invalid')
        if state['check_plan'] is not None and not isinstance(state['check_plan'], dict):
            raise ValueError('Resume check_plan is invalid')
        if state['candidate_check'] is not None and not isinstance(state['candidate_check'], dict):
            raise ValueError('Resume candidate_check is invalid')
        valid_statuses = {'not_invoked', 'active', 'diagnosis_pending', 'diagnosis_failed',
                          'improving', 'completed', 'failed', 'incomplete',
                          'skipped_no_actionable_improvement'}
        if state['status'] not in valid_statuses:
            raise ValueError('Resume status is invalid')
        if active is None or state['status'] not in ('active', 'diagnosis_pending', 'improving'):
            raise ValueError('Resume requires an active online workflow')
        if state['completed_steps'] != active['index']:
            raise ValueError('Resume completed_steps must match the active stage index')
        self.active = copy.deepcopy(active)
        self.counter = state['role_calls']
        self.status = state['status']
        self.completed_steps = state['completed_steps']
        self.skipped_steps = copy.deepcopy(state['skipped_steps'])
        self.diagnosis = copy.deepcopy(state['diagnosis'])
        self.diagnostic_reports = copy.deepcopy(state['diagnostic_reports'])
        self.diagnostic_role_evidence = copy.deepcopy(state['diagnostic_role_evidence'])
        self.check_plan = copy.deepcopy(state['check_plan'])
        self.candidate_check_result = copy.deepcopy(state['candidate_check'])
        self._restore_committee_cache(state.get('committee_cache', []))

    def _restore_committee_cache(self, seeds):
        if seeds is None:
            return
        if not isinstance(seeds, list):
            raise ValueError('Resume committee cache seed must be a list')
        for seed in seeds:
            if not isinstance(seed, dict):
                raise ValueError('Resume committee cache seed is malformed')
            required = {'context', 'proposal', 'reviewer_specialisation_name',
                        'submission', 'role_identity', 'source_execution_hash'}
            if not required.issubset(seed):
                raise ValueError('Resume committee cache seed is incomplete')
            if (not isinstance(seed['context'], str) or not isinstance(seed['proposal'], str)
                    or not isinstance(seed['reviewer_specialisation_name'], str)
                    or not isinstance(seed['submission'], str)
                    or not isinstance(seed['role_identity'], str) or not seed['role_identity']
                    or not isinstance(seed['source_execution_hash'], str)):
                raise ValueError('Resume committee cache seed has invalid types')
            entry = self._committee_cache_entry(seed['proposal'], seed['context'],
                                                seed['reviewer_specialisation_name'],
                                                seed['submission'], seed['role_identity'],
                                                seed['source_execution_hash'],
                                                seed.get('parent_version'), seed.get('model'),
                                                require_current=False)
            if entry is not None:
                self.committee_cache[entry['cache_key']] = entry

    def _write_feedback_index(self):
        """Persist the public facts supplied to diagnosis without private grader data."""
        allowed = ('task_id', 'task_index', 'round_id', 'version', 'executor',
                   'proposer', 'parent_version', 'task_statement', 'model',
                   'exit_status', 'score', 'passed', 'tokens_used', 'wall_time',
                   'evidence_refs', 'summary', 'reports', 'full_source_hash',
                   'execution_hash', 'check_identity', 'check_plan', 'public')
        public = {key:self.feedback[key] for key in allowed if key in self.feedback}
        public.setdefault('evidence_refs', [])
        if not isinstance(public['evidence_refs'], list):
            raise ValueError('feedback evidence_refs must be a list')
        # ``record`` is a host-produced public score/resource summary.  Keep
        # only stable fields; grader internals and private evidence remain
        # outside the model-visible package.
        record = self.feedback.get('record')
        if isinstance(record, dict):
            public['record'] = {key:record[key] for key in (
                'problem_id', 'score', 'passed', 'tokens_used',
                'num_cached_tokens', 'wall_time', 'timed_out',
                'cost_threshold_exceeded', 'token_accounting_complete') if key in record}
        self.feedback = public
        path = self.logs / 'public_feedback.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(public, ensure_ascii=False, sort_keys=True))
        temporary.replace(path)

    def save(self):
        temporary = self.logs/'workflow_state.json.tmp'
        temporary.write_text(json.dumps({'active':self.active,'role_calls':self.counter,'status':self.status,
                                         'completed_steps':self.completed_steps,
                                         'skipped_steps':self.skipped_steps,
                                         'diagnosis':self.diagnosis,
                                         'diagnostic_reports':self.diagnostic_reports,
                                         'diagnostic_role_evidence':self.diagnostic_role_evidence,
                                         'committee_cache':list(self.committee_cache.values()),
                                         'check_plan':self.check_plan,
                                         'candidate_check':self.candidate_check_result}, ensure_ascii=False))
        temporary.replace(self.logs/'workflow_state.json')

    def event(self, **event):
        with (self.logs/'workflow_events.jsonl').open('a') as f:
            f.write(json.dumps(event)+'\n')
        self.save()

    def current(self):
        if not self.active:
            return {'active':None}
        a = self.active
        step = self.steps[a['index']]
        identifier = a['step_ids'][a['index']]
        description = f'You MUST call this tool as soon as you have completed step {identifier}.'
        if step.get('done_description'):
            description += f"\nThe step {identifier} is done when: {step['done_description']}"
        if step.get('failed_description'):
            description += f"\nThe step {identifier} has failed if: {step['failed_description']}. If you detect this failure mode, then invoke this tool immediately."
        return {**a, **step, 'completion_tool': identifier+'_complete', 'completion_description':description}

    def check_stage_role(self, role):
        if not self.online:
            return
        index = self.active['index'] if self.active else None
        if role == 'software_developer' and (index != 3 or self.status != 'improving'):
            raise ValueError('software_developer is available only at step 4; finish proposal review first')
        if role == 'general_problem_solver' and (index not in (3, 4, 5, 6) or self.status != 'improving'):
            raise ValueError('general_problem_solver is available only at steps 4-7')

    def stage_source_hash(self):
        root = self.identities.get('workdir')
        if not root or not Path(root).is_dir():
            return None
        digest = hashlib.sha256()
        for item in sorted(Path(root).rglob('*')):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                raise ValueError('Candidate contains a symlink or special file')
            if not item.is_file() or '__pycache__' in item.parts:
                continue
            relative = item.relative_to(root)
            if str(relative) in ('description.txt', 'agent_change_log.md'):
                continue
            digest.update(str(relative).encode() + b'\0' + item.read_bytes())
        return digest.hexdigest()

    def assert_stage_source(self, freeze=False):
        if not self.online:
            return
        index = self.active['index'] if self.active else None
        source_hash = self.stage_source_hash()
        if source_hash is None:
            return
        path = self.logs / 'stage_source_guard.json'
        if not path.exists() or freeze:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'source_sha256':source_hash}))
            temporary.replace(path)
        elif index != 3 and json.loads(path.read_text())['source_sha256'] != source_hash:
            raise ValueError('Harness source changed outside step 4; candidate cannot advance')

    def call_role(self, role, task, specialisation='', context=''):
        mapping = {'archive_explorer':'archive_explorer','software_developer':'coder','general_problem_solver':'problem_solver','reviewer':'review_committee_member'}
        if role not in mapping:
            raise ValueError('Unknown role')
        self.check_stage_role(role)
        self.assert_stage_source()
        self.counter += 1
        identity = f'{self.counter:03d}_{role}'
        call_state = self.current()
        call_index = call_state.get('index') if self.active else None
        call_step = call_index + 1 if isinstance(call_index, int) else None
        self.event(kind='role_start', role=role, identity=identity)
        try:
            definition = self.roles[mapping[role]]
            values = {'analysis_focus':task, 'programming_instructions':task, 'problem_statement':task,
                      'specialisation':specialisation, 'context':context, 'proposal':task}
            if role == 'archive_explorer':
                values['analysis'] = (self.online_overview() if self.online
                                      else 'Online archive overview is unavailable outside an online workflow.')
            prompt = core_prompt(mapping[role], values) + identity_context(self.identities, self.current())
            result = self.delegate(identity, prompt, role in ('archive_explorer','reviewer'))
            self.event(kind='role_end', identity=identity, role=role,
                       step_index=call_index, result=result)
            if result.get('exit_status') != 'Submitted':
                raise ValueError(f'Role {identity} failed: {result.get("exit_status")}')
            if self.online and call_index in (0, 1) and role == 'archive_explorer':
                submission = result.get('submission')
                if not isinstance(submission, str) or not submission.strip():
                    raise ValueError(f'Online diagnostic role {identity} returned an empty submission')
                self.diagnostic_role_evidence[str(call_index + 1)] = {
                    'identity': identity,
                    'role': role,
                    'step': call_step,
                    'index': call_index,
                    'invocation_id': self.current()['invocation_id'],
                    'submission_nonempty': True,
                }
                self.save()
            return result.get('submission','')
        except Exception as exc:
            self.event(kind='role_error', identity=identity, error=str(exc))
            if is_model_request_exception(exc):
                raise WorkflowTransportError(f'{type(exc).__name__}: {exc}') from exc
            raise

    def dispatch(self, request, role='orchestrator'):
        self.assert_stage_source()
        requested_op = request['op']
        op = self.aliases.get(requested_op, requested_op)
        current_completion = self.current().get('completion_tool')
        if requested_op == current_completion and current_completion is not None:
            if request.get('tool', current_completion) != current_completion:
                raise ValueError('Conflicting completion tool')
            op = 'complete'
            request = {**request, 'tool':current_completion}
        caps = role_capabilities(role)
        if self.online and role == 'orchestrator':
            caps = {**caps, 'operations': set(caps['operations']) | {'diagnosis_decision', 'decision', 'candidate_check'}}
        if self.online and op in ('diagnosis_decision', 'decision', 'candidate_check') and role != 'orchestrator':
            raise ValueError(f'{op} is available only to an online coordinator')
        self.event(kind='operation_request', requested_op=requested_op, op=op, role=role)
        if op not in caps['operations']:
            raise ValueError(f'Operation {requested_op} is unavailable to {role}; permitted: {sorted(caps["operations"])}')
        if self.online and self.status == 'diagnosis_pending' and op in ('committee', 'delegate'):
            raise ValueError('Online diagnosis decision is required before committee or implementation roles')
        if self.online and self.status == 'active' and self.active and self.active['index'] < 2:
            if op == 'committee' or (op == 'delegate' and request.get('role') != 'archive_explorer'):
                raise ValueError('Online steps 1 and 2 permit diagnosis-only roles')
        if op == 'invoke':
            if self.active:
                raise ValueError('Structure already active')
            self.status = 'active'
            self.completed_steps = 0
            self.active = {'invocation_id':'reasoning_structure_invocation_'+uuid.uuid4().hex[-4:], 'index':0,'step_ids':['step_'+uuid.uuid4().hex[-4:] for _ in self.steps]}
            self.event(kind='invoke', state=self.current())
            return self.current()
        if op == 'state':
            return self.current()
        if op == 'complete':
            if not self.active or request.get('invocation_id', self.active['invocation_id']) != self.active['invocation_id'] or request.get('tool') != self.current()['completion_tool']:
                raise ValueError('Only the active invocation/current completion tool is available: ' + str(current_completion))
            if self.online and self.active['index'] == 2 and self.completed_steps == 2 and self.status == 'diagnosis_pending':
                raise ValueError('Online diagnosis decision is required before step 3')
            success = request.get('success', True)
            if type(success) is not bool:
                raise ValueError('success must be boolean')
            previous = self.current()
            if self.online and self.stage_boundary is not None:
                self.stage_boundary()
            diagnostic_evidence = None
            if self.online and success and previous['index'] in (0, 1):
                diagnostic_evidence = self._require_diagnostic_role_evidence(previous)
            if self.online and success and previous['index'] == 2:
                self.check_plan = self._freeze_check_plan(request.get('check_plan'))
                self.event(kind='check_plan_frozen', check_plan=self.check_plan)
            if (self.online and success and previous['index'] == 4
                    and self.candidate_check_result is None):
                raise ValueError('Online step 5 requires a completed host candidate_check')
            if self.online and success and previous['index'] in (0, 1):
                report = request.get('report')
                refs = request.get('evidence_refs', [])
                if not isinstance(report, str) or not report.strip() or not isinstance(refs, list) or not refs:
                    self.status, self.active = 'diagnosis_failed', None
                    self.event(kind='diagnosis_failed', reason='step report/evidence_refs missing',
                               step=previous['index'] + 1)
                    raise ValueError('Online diagnostic steps require a report and evidence_refs')
                self.validate_evidence_refs(refs)
                self.diagnostic_reports[str(previous['index'] + 1)] = {
                    'report': report, 'evidence_refs': refs,
                    # Bind the persisted report to the host-generated role
                    # identity; never copy a model-supplied identity field.
                    'identity': diagnostic_evidence['identity'],
                    'role': diagnostic_evidence['role'],
                    'step_index': previous['index']}
            if self.online and success and previous['index'] == 3:
                self.assert_stage_source(freeze=True)
            if success:
                self.completed_steps += 1
            if not success or self.active['index'] == len(self.steps)-1:
                self.active = None
                self.status = 'completed' if success else 'failed'
                output = 'Reasoning structure complete. Please proceed to complete the rest of the task as appropriate.' if success else f'Reasoning structure exited due to error in step {previous["step_ids"][previous["index"]]}. Please proceed to complete the rest of the task as appropriate.'
            else:
                self.active['index'] += 1
                if self.online and self.completed_steps == 2:
                    self.status = 'diagnosis_pending'
                output = self.current()
            self.event(kind='complete', previous=previous, declared_success=success, output=output)
            return output
        if op in ('diagnosis_decision', 'decision'):
            if not self.online or role != 'orchestrator':
                raise ValueError('diagnosis_decision is available only to an online coordinator')
            if not self.active or self.active['index'] != 2 or self.completed_steps != 2 or self.status != 'diagnosis_pending':
                raise ValueError('diagnosis_decision is available only after successful steps 1 and 2')
            decision = request.get('decision')
            if decision not in ('skip', 'improve'):
                raise ValueError('decision must be skip or improve')
            reason = request.get('reason')
            refs = request.get('evidence_refs', [])
            failures = request.get('failure_modes', [])
            mechanisms = request.get('effective_mechanisms', [])
            opportunities = request.get('improvement_opportunities', [])
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('diagnosis decision requires a reason')
            if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
                raise ValueError('evidence_refs must be a list of non-empty strings')
            if not isinstance(failures, list) or not isinstance(mechanisms, list) or not isinstance(opportunities, list):
                raise ValueError('diagnosis fields must be lists')
            valid_refs = set(self.feedback.get('evidence_refs', []))
            if not refs or not valid_refs or not set(refs).issubset(valid_refs):
                raise ValueError('diagnosis evidence_refs must refer to public feedback')
            self.validate_evidence_refs(refs)
            if decision == 'skip' and opportunities:
                raise ValueError('A normal skip must declare no actionable improvement opportunities')
            if decision == 'improve' and not opportunities:
                raise ValueError('An improvement decision requires an actionable opportunity')
            diagnosis = {'task_id':self.feedback.get('task_id'), 'round_id':self.feedback.get('round_id'),
                         'executor':self.identities.get('version', self.identities.get('executor')),
                         'proposer':self.identities.get('version', self.identities.get('proposer')),
                         'parent':self.identities.get('version', self.identities.get('parent')),
                         'reports':self.diagnostic_reports, 'failure_modes':failures,
                         'effective_mechanisms':mechanisms, 'improvement_opportunities':opportunities,
                         'decision':decision, 'reason':reason, 'evidence_refs':refs}
            diagnosis_path = self.logs / 'diagnosis.json'
            temporary = diagnosis_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2))
            temporary.replace(diagnosis_path)
            self.diagnosis = diagnosis
            if decision == 'skip':
                self.active = None
                self.status = 'skipped_no_actionable_improvement'
                self.skipped_steps = [3,4,5,6,7]
                output = 'Diagnosis recorded: no actionable Harness improvement. Continue with the next task.'
                self.event(kind='diagnosis_decision', decision=decision, diagnosis=diagnosis,
                           skipped_steps=[3,4,5,6,7])
                return output
            self.status = 'improving'
            self.event(kind='diagnosis_decision', decision=decision, diagnosis=diagnosis,
                       opened_steps=[3,4,5,6,7])
            return self.current()
        if op == 'candidate_check':
            if not self.online or role != 'orchestrator':
                raise ValueError('candidate_check is available only to an online coordinator')
            if not self.active or self.active['index'] != 4 or self.completed_steps != 4 or self.status != 'improving':
                raise ValueError('candidate_check is available only at step 5 after steps 1-4')
            if self.candidate_check is None:
                raise ValueError('No authenticated host candidate check is registered')
            required = ('job_id', 'task_id', 'round_id', 'candidate', 'parent_version',
                        'check_revision')
            if any(not isinstance(request.get(key), str) or not request[key] for key in required):
                raise ValueError('candidate_check requires a complete frozen identity')
            check_request = {key:request[key] for key in required}
            if self.check_plan is None:
                raise ValueError('Candidate check requires the step-3 frozen plan')
            if 'mechanism_plan' in request:
                if request['mechanism_plan'] != self.check_plan.get('mechanism_plan'):
                    raise ValueError('Candidate check cannot change the step-3 mechanism plan')
            result = self.candidate_check(check_request)
            if not isinstance(result, dict):
                raise ValueError('Host candidate check returned an invalid result')
            if result.get('status') == 'running':
                raise ValueError('Host candidate check is still running; retry the same frozen request')
            self.candidate_check_result = {'request':check_request,
                                           'result':result}
            path = self.logs / 'candidate_check_result.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.candidate_check_result, ensure_ascii=False, indent=2))
            temporary.replace(path)
            self.event(kind='candidate_check', request=self.candidate_check_result['request'],
                       result=result)
            return {'status':'candidate_checked', 'result':result}
        if op == 'delegate':
            if request.get('role') not in caps['delegates']:
                raise ValueError('Delegate is unavailable to this original role')
            return self.call_role(request['role'], request['task'])
        if op == 'committee':
            reviews = ''
            for i,(name,spec) in enumerate(self.specs.items()):
                submission, role_identity = self._committee_review(
                    request['proposal'], request['context'], name, spec)
                reviews += f"\n\nReviewer {i}'s review:\n" + submission
            return reviews
        if op == 'archive':
            if not self.online:
                raise ValueError('Archive analysis is available only in an online workflow')
            if request.get('kind', 'overview') != 'overview':
                raise ValueError('Online archive supports only chronological overview; ranking and comparison are unsupported')
            return self.online_overview()
        if op in ('read','list','search'):
            return self.inspect(request)
        raise ValueError('Unknown workflow operation')

    def _source_execution_hash(self):
        workdir = self.identities.get('workdir')
        if workdir and Path(workdir).is_dir():
            return execution_hash(workdir)
        supplied = self.identities.get('source_execution_hash')
        if isinstance(supplied, str) and supplied:
            return supplied
        return None

    def _committee_cache_context(self, proposal, context):
        return {
            'proposal': proposal,
            'context': context,
            'parent_version': self.identities.get('parent_version', self.identities.get('version')),
            'model': self.identities.get('model'),
            'source_execution_hash': self._source_execution_hash(),
        }

    @staticmethod
    def _committee_cache_key(context, specialisation):
        return json.dumps({**context, 'reviewer_specialisation_name':specialisation},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def _committee_cache_entry(self, proposal, context, specialisation, submission,
                               role_identity, source_execution_hash,
                               parent_version=None, model=None, require_current=True):
        current = self._committee_cache_context(proposal, context)
        stored_parent = current['parent_version'] if parent_version is None else parent_version
        stored_model = current['model'] if model is None else model
        if (require_current and (source_execution_hash != current['source_execution_hash']
                or stored_parent != current['parent_version']
                or stored_model != current['model'])):
            return None
        cache_context = {'proposal': proposal, 'context': context,
                         'parent_version': stored_parent, 'model': stored_model,
                         'source_execution_hash': source_execution_hash}
        return {'cache_key': self._committee_cache_key(cache_context, specialisation),
                'proposal': proposal, 'context': context,
                'reviewer_specialisation_name': specialisation,
                'submission': submission, 'role_identity': role_identity,
                'source_execution_hash': source_execution_hash,
                'parent_version': stored_parent, 'model': stored_model}

    def _committee_review(self, proposal, context, specialisation, task):
        cache_context = self._committee_cache_context(proposal, context)
        if all(value is not None for value in cache_context.values()):
            key = self._committee_cache_key(cache_context, specialisation)
            cached = self.committee_cache.get(key)
            if isinstance(cached, dict):
                return cached['submission'], cached['role_identity']
        before = self.counter
        submission = self.call_role('reviewer', proposal, task, context)
        role_identity = f'{before + 1:03d}_reviewer'
        entry = self._committee_cache_entry(
            proposal, context, specialisation, submission, role_identity,
            cache_context['source_execution_hash'], cache_context['parent_version'],
            cache_context['model'])
        if entry is not None:
            self.committee_cache[entry['cache_key']] = entry
            self.save()
        return submission, role_identity

    def _require_diagnostic_role_evidence(self, step):
        """Require host-recorded archive evidence before online diagnosis advances."""
        index = step['index']
        evidence = self.diagnostic_role_evidence.get(str(index + 1))
        if (not isinstance(evidence, dict)
                or not isinstance(evidence.get('identity'), str)
                or not evidence.get('identity')
                or evidence.get('role') != 'archive_explorer'
                or evidence.get('step') != index + 1
                or evidence.get('index') != index
                or evidence.get('invocation_id') != step.get('invocation_id')
                or evidence.get('submission_nonempty') is not True):
            raise ValueError(
                f'Online step {index + 1} requires a successful archive_explorer submission')
        return evidence

    def validate_evidence_refs(self, refs):
        allowed = set(self.feedback.get('evidence_refs', []))
        if (not isinstance(refs, list) or not refs or any(
                not isinstance(ref, str) or ref not in allowed or Path(ref).is_absolute()
                or '..' in Path(ref).parts or not ref.startswith('tasks/') for ref in refs)):
            raise ValueError('Evidence references must belong to the host-provided public task feedback')

    def online_overview(self):
        archive = Path(self.identities.get('public_archive', '/home/agent/archive'))
        items = []
        for path in sorted((archive/'tasks').glob('*/public_feedback.json')):
            data = json.loads(path.read_text())
            items.append({key: data.get(key) for key in ('task_id', 'version', 'record', 'evidence_refs')})
        return ('Online chronological task evidence; different versions have different task coverage. '
                'Do not rank versions by unpaired average scores.\n' + json.dumps(items, ensure_ascii=False))

    def _freeze_check_plan(self, requested):
        """Freeze the model's declared criterion before implementation starts."""
        if not isinstance(requested, dict):
            raise ValueError('Online step 3 requires an explicit check_plan object')
        if set(requested) - {'criterion', 'mechanism_plan'}:
            raise ValueError('Unknown check_plan field')
        plan = copy.deepcopy(requested)
        options = {'current_task_failure_fixed', 'mechanism_parent_failed_candidate_passed'}
        if plan.get('criterion') not in options:
            raise ValueError('Online check_plan criterion is not supported')
        mechanism = plan.get('mechanism_plan')
        if plan['criterion'] == 'mechanism_parent_failed_candidate_passed' and mechanism is None:
            raise ValueError('Mechanism acceptance requires mechanism_plan in step 3 check_plan')
        if mechanism is not None:
            if not isinstance(mechanism, dict):
                raise ValueError('check_plan mechanism_plan must be an object')
            relative = mechanism.get('test_path', mechanism.get('entrypoint'))
            if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                    or '..' in Path(relative).parts or Path(relative).suffix != '.py'):
                raise ValueError('check_plan mechanism_plan requires a relative Python test_path')
            args = mechanism.get('args', [])
            if (not isinstance(args, list) or len(args) > 32
                    or any(not isinstance(arg, str) for arg in args)):
                raise ValueError('check_plan mechanism_plan args must be a short string list')
            timeout = mechanism.get('timeout_seconds', 60)
            if type(timeout) is not int or not 1 <= timeout <= 600:
                raise ValueError('check_plan mechanism_plan timeout must be 1..600 seconds')
            plan['mechanism_plan'] = {'test_path':relative, 'args':args,
                                      'timeout_seconds':timeout}
        plan['revision'] = '1'
        path = self.logs / 'check_plan_declared.json'
        if path.exists() and json.loads(path.read_text()) != plan:
            raise ValueError('The step-3 declared plan is immutable')
        encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(encoded)
        temporary.replace(path)
        return plan

    def finish(self, result):
        if self.online and self.stage_boundary is not None:
            self.stage_boundary()
        self.assert_stage_source()
        if self.status not in ('completed','failed','skipped_no_actionable_improvement','diagnosis_failed'):
            self.status = 'incomplete'
        self.event(kind='task_exit', exit_status=result.get('exit_status'), status=self.status, completed_steps=self.completed_steps)

    def inspect(self, request):
        path = Path(request['path']).resolve()
        roots = [Path('/home/agent/workdir').resolve(), Path('/home/agent/archive').resolve()]
        if not any(path == root or root in path.parents for root in roots):
            raise ValueError('Inspection restricted to candidate/public archive')
        op = request['op']
        if path.exists() and not (path.is_file() or path.is_dir()):
            raise ValueError('Only regular files/directories may be inspected')
        if op == 'read':
            lines = path.read_text(errors='replace').splitlines()
            start, end = request.get('start_line', 1), request.get('end_line', max(1,len(lines)))
            if type(start) is not int or type(end) is not int or start < 1 or end < start:
                raise ValueError('read requires 1-based inclusive start_line/end_line')
            content = '\n'.join(f'{i+1}: {lines[i]}' for i in range(start-1,min(end,len(lines))))
            return {'path':str(path), 'evidence_kind':'historical_archive' if roots[1] == path or roots[1] in path.parents else 'live_candidate',
                    'start_line':start, 'end_line':min(end,len(lines)), 'total_lines':len(lines),
                    'content':content, 'content_truncated':False}
        if op == 'list':
            return sorted(p.name for p in path.iterdir())
        hits = []
        for p in ([path] if path.is_file() else path.rglob('*')):
            if p.is_file() and any(p.resolve() == r or r in p.resolve().parents for r in roots):
                try:
                    for i,line in enumerate(p.read_text(errors='replace').splitlines()):
                        if request['pattern'] in line:
                            hits.append(f'{p}:{i+1}:{line}')
                except OSError:
                    pass
        return hits

    def _structured_submission(self, request):
        """Adapt SICA's result tools to mini's existing submission sentinel.

        This is intentionally handled at the transport boundary instead of
        ``dispatch``: submission is an agent exit, while the seven-step
        completion tools remain owned by the orchestrator and their original
        role capability set stays unchanged.
        """
        if set(request) != {'op', 'report'}:
            raise ValueError('submit requires exactly op and report')
        report = request['report']
        prefix = FINAL_OUTPUT_SENTINEL + '\n'
        if type(report) is not str or not report.strip():
            raise ValueError('report must be a non-empty string')
        if report == FINAL_OUTPUT_SENTINEL:
            raise ValueError('report must contain a non-empty submission')
        if report.startswith(prefix):
            body = report[len(prefix):]
            if not body.strip():
                raise ValueError('report must contain a non-empty submission')
            output = report
        else:
            output = prefix + report
        return {'output':output + '\n','returncode':0}

    def execute(self, action, readonly=False, role=None):
        command = action.get('command','')
        requested_op = None
        try:
            if command.startswith('sica-workflow'):
                prefix = 'sica-workflow'
                if len(command) == len(prefix) or not command[len(prefix)].isspace():
                    raise ValueError('Expected one JSON argument')
                raw_argument = command[len(prefix):].lstrip()
                if raw_argument.startswith('{'):
                    # Raw JSON is preferred. Parsing starts after the transport
                    # prefix, so JSON string contents never pass through a shell
                    # tokenizer or execute shell syntax.
                    request = json.loads(raw_argument)
                else:
                    # Preserve compatibility with the original quoted-JSON form.
                    parts = shlex.split(command)
                    if len(parts) != 2 or parts[0] != prefix:
                        raise ValueError('Expected one JSON argument')
                    request = json.loads(parts[1])
                if type(request) is not dict:
                    raise ValueError('Expected one JSON object')
                requested_op = request.get('op')
                if requested_op == 'submit':
                    return self._structured_submission(request)
                op = self.aliases.get(requested_op, requested_op)
                # Role comes from the actual executing environment, never JSON.
                trusted_role = role or ('reviewer' if readonly is True else 'software_developer' if readonly == 'editor' else 'orchestrator')
                return {'output':json.dumps(self.dispatch(request, trusted_role)), 'returncode':0}
            parts = shlex.split(command)
            if readonly and readonly != 'editor':
                if len(parts) >= 2 and parts[0] == 'echo' and parts[1] == 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' and not any(p in (';', '&&', '||', '|', '>', '<') for p in parts[2:]):
                    return {'output':' '.join(parts[1:])+'\n','returncode':0}
                # Parse the official printf submission without running a shell.
                if len(parts) == 3 and parts[0] == 'printf' and parts[1] == '%s\\n' and parts[2].startswith('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n'):
                    return {'output':parts[2]+'\n','returncode':0}
                raise ValueError('Role permits only structured workflow operations or exact submission')
            if self.online and (not self.active or self.active['index'] not in (3, 4, 5, 6)):
                raise ValueError('Shell is unavailable outside steps 4-7; only step 4 permits source edits')
            self.assert_stage_source()
            return None
        except WorkflowTransportError:
            # A provider request failure is terminal for this model process;
            # returning a normal tool error would invite the coordinator to
            # spend more calls on unrelated roles.
            raise
        except Exception as exc:
            self.event(kind='operation_error', requested_op=requested_op, error=str(exc))
            return {'output':str(exc),'returncode':1}


def core_prompt(role, values, source=SOURCE):
    """Render original direct prompt assignments with a closed f-string evaluator.

    Optional previous runs/requirements are supplied as task context by caller;
    no upstream executable code is evaluated here.
    """
    cls = next(n for n in ast.parse((source/(role+'.py')).read_text()).body if isinstance(n, ast.ClassDef))
    func = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'construct_core_prompt')
    def render(node):
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.JoinedStr):
            return ''.join(render(v) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return render(node.value)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'self':
            return str(values[node.attr])
        if isinstance(node, ast.Name):
            return str(values[node.id])
        raise ValueError('Unsupported upstream prompt expression')
    prompt = ''
    for n in func.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'prompt' for t in n.targets):
            prompt = render(n.value)
        elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == 'prompt':
            prompt += render(n.value)
    return prompt
