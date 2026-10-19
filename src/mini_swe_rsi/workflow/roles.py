"""Pinned role definitions, capabilities and prompt rendering; no workflow state."""
import ast
import copy
import hashlib
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().with_name('sources')
if not SOURCE.exists():
    SOURCE = Path('/workflow_sources')


def _role_class(source, name):
    return next(n for n in ast.parse((source/(name+'.py')).read_text()).body
                if isinstance(n, ast.ClassDef))


def _role_fields(source, name):
    return {target.id: node.value for node in _role_class(source, name).body
            if isinstance(node, ast.Assign) for target in node.targets
            if isinstance(target, ast.Name)}


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
        roles[name] = {key: ast.literal_eval(value) for key, value in _role_fields(source, name).items()
                       if key in ('SYSTEM_PROMPT', 'AGENT_DESCRIPTION', 'AGENT_NAME')}
    return steps, specialisations, roles


def tool_aliases(source=SOURCE):
    """Only the two exact TOOL_NAMEs in the pinned source are accepted aliases."""
    aliases = {}
    for filename, expected, operation in (
        ('meta_improvement', 'meta_improvement_reasoning_structure', 'invoke'),
        ('committee_design', 'meta_improvement_review_committee', 'committee'),
    ):
        name = ast.literal_eval(_role_fields(source, filename)['TOOL_NAME'])
        if name != expected:
            raise ValueError('Pinned workflow TOOL_NAME differs: ' + filename)
        aliases[name] = operation
    return aliases


def instructions(role="orchestrator"):
    text = '''SICA callable transport: for structured operations, emit one normal mini bash action whose entire command is
sica-workflow {"op":"..."}
Use one raw JSON object directly after sica-workflow, without shell quoting.
Operations: {"op":"invoke"}, {"op":"state"}, {"op":"complete","invocation_id":"...","tool":"<current completion tool>","success":true},
{"op":"submit","report":"<full non-empty report body; omit the completion sentinel>"},
{"op":"delegate","role":"archive_explorer|software_developer|general_problem_solver","task":"complete self-contained instructions"},
{"op":"committee","context":"goals and evidence","proposal":"full design"}.
Use sica-workflow only for structured operations. Every role finishes with the structured submit operation and its complete non-empty report; the report is the raw report body and must not include the COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT sentinel because the host adds it. Arbitrary bash is unavailable to restricted roles; editing roles may use normal mini bash only during their allowed editing steps. As the orchestrator, invoke meta_improvement_reasoning_structure as early as reasonably possible. Only the current completion tool is callable. Stage success is your declaration, not host verification. Each invocation has an independent iteration allowance: orchestrator/archive_explorer/general_problem_solver/reviewer 500, software_developer 1000. One iteration permits up to five actual provider requests. All roles use the same model and have no additional global/stage deadline.
Read-only inspection operations: {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":200}, {"op":"list","path":"/home/agent/archive"}, {"op":"search","path":"/home/agent/workdir","pattern":"literal text"}.
Structured read uses inclusive 1-based start_line/end_line (default whole file); returned paths identify the actual resolved evidence source. Never append shell pipes to a structured command.
Compatibility: online public file views use these read/list/search operations; editing and local tests use normal mini bash in editing roles. Original return_result/submit_answer map to the structured submit operation. The old echo/printf sentinel forms are unsupported. Original python -m workdir.agent end-to-end example is unavailable: there is no hidden model endpoint; delegate an explicit test task through this transport and shared broker, or run offline tests. Independent benchmark grading remains host-controlled. Web research tools are unavailable. Original role system instructions and step strings follow unchanged; these transport mappings resolve old tool names.'''
    if role != 'orchestrator':
        start = text.index('Operations:')
        stop = text.index('Read-only inspection operations:')
        capabilities = ('This delegated role cannot invoke, inspect, or complete the parent reasoning structure. '
                        'It may finish its own run with {"op":"submit","report":"<full non-empty report body; omit the completion sentinel>"}; '
                        'Each role invocation has its independent SICA iteration allowance (software_developer 1000; other roles 500), '
                        'up to five provider requests per completion. Commands have a 600-second tool safety timeout; output and context '
                        'capacities come from the frozen model declaration. No additional global/stage time limit applies. ')
        if role in ('software_developer','general_problem_solver'):
            capabilities += 'Editing and local checks use normal mini bash. Committee is callable as {"op":"committee","context":"...","proposal":"..."}. '
        text = text[:start] + capabilities + '\n' + text[stop:]
        if role not in ('software_developer','general_problem_solver'):
            text = text.replace('editing and local tests use normal mini bash in editing roles.', 'this role has no shell or write tool; use inspection and final report submission only.')
    for filename in (('meta_improvement','committee_design') if role == 'orchestrator' else ('committee_design',) if role in ('software_developer','general_problem_solver') else ()):
        description = ast.literal_eval(_role_fields(SOURCE, filename)['TOOL_DESCRIPTION'])
        text += '\nOriginal '+filename+' tool description:\n'+description
    # Remove capabilities which do not belong to this original role. The same
    # AST-derived policy is enforced at dispatch, not merely described here.
    text = text.replace('{"op":"complete","invocation_id":"...","tool":"<current completion tool>","success":true}',
                        '{"op":"<current step_..._complete tool>","success":true}')
    if role == 'archive_explorer':
        text += '\nOnline archive analysis: {"op":"archive","kind":"overview"}. Historical task evidence is chronological and must not be ranked or compared across versions.'
    caps = role_capabilities(role)
    if 'search' not in caps['operations']:
        text = text.replace(', {"op":"search","path":"/home/agent/workdir","pattern":"literal text"}', '')
    permitted = ('invoke','committee') if role == 'orchestrator' else ('committee',) if role in ('software_developer','general_problem_solver') else ()
    aliases = {name:op for name,op in tool_aliases().items() if op in permitted}
    if aliases:
        text += '\nRegistered original TOOL_NAME aliases (same operations and permissions): ' + json.dumps(aliases,sort_keys=True)
    text += '\nPermitted operations for this role: ' + ', '.join(sorted(caps['operations']))
    text += '\nPermitted delegated roles: ' + ', '.join(sorted(caps['delegates']))
    if caps['delegates']:
        roles = definitions()[2]
        for delegate in sorted(caps['delegates']):
            text += '\n' + roles[ROLE_MAP[delegate]]['AGENT_DESCRIPTION']
    if caps['delegates']:
        text += '\nDelegation syntax: {"op":"delegate","role":"<permitted role>","task":"complete self-contained instructions"}.'
    if role == 'orchestrator':
        text += '\nChronological online archive overview belongs to archive_explorer; delegate archive analysis to that agent. The current step completion tool takes success only; its invocation is bound by the host. Call the exact current step name, not the structure name. Unsupported upstream ReasoningAgent is not registered.'
    if role in ('orchestrator', 'software_developer', 'general_problem_solver'):
        text += ('\nOffline agent-loop tests: a successful action does not end DefaultAgent.run(). '
                 'For a single recovery transition, initialize the messages and invoke step() a finite number of times. '
                 'For a full run(), the stub must eventually emit an exit message via InterruptAgentFlow; '
                 'also set a small explicit step_limit as a fallback and assert the intended exit_status '
                 '(LimitsExceeded is not test success). Successful-action stubs need explicit termination; '
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
    fields = _role_fields(source, ROLE_MAP[role])
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


def role_templates(config, role):
    """Improve-only adapter, derived from the actually selected archived config.

    Keep the archived text action formatting/parser vocabulary. Replace generic
    issue-solving tasks and capability-incompatible feedback at this boundary.
    """
    adapted = copy.deepcopy(config)
    original_system = config['agent']['system_template']
    roles = definitions()[2]
    # mini's parser accepts one text action; restricted roles cannot use shell composition.
    formatting = (original_system if role in ('software_developer','general_problem_solver')
                  else original_system.replace(' (or commands connected with && or ||)', ''))
    role_definition = roles[ROLE_MAP[role]]
    adapted['agent']['system_template'] = (role_definition['SYSTEM_PROMPT']
        + '\n' + role_definition['AGENT_DESCRIPTION'] + '\n' + formatting + '\n' + instructions(role))
    adapted['agent']['instance_template'] = '{{task}}\nShared session at role start: {{session_status}}'
    example = 'sica-workflow {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":40}'
    submission_example = 'sica-workflow {"op":"submit","report":"<full non-empty report body; omit the completion sentinel>"}'
    operation_error_guidance = ('For structured-operation errors, use this valid inspection syntax: '
        if role in ('software_developer','general_problem_solver') else 'Use a supported inspection operation or exact final submission. Valid inspection syntax: ')
    adapted['model']['observation_template'] = ("<returncode>{{output.returncode}}</returncode>\n"
        "<exception>{{output.exception_info}}</exception>\n<output>{{output.output}}</output>\n"
        "{% if output.returncode != 0 %}" + operation_error_guidance + example
        + " Only /home/agent/workdir and /home/agent/archive are inspection roots. "
          "Use one JSON argument as one raw JSON object; do not shell-quote it or append shell pipes or redirects. For final submission use " + submission_example + ".{% endif %}\n"
        "Shared session: {{output.session_status | default({})}}")
    adapted['model']['format_error_template'] = ("Format error: {{error | default('incomplete action')}}. "
        "Return exactly one complete mswea_bash_command block, with a THOUGHT before it. "
        "Use one structured command, for example:\n```mswea_bash_command\n" + example + "\n```\n"
        "Do not append shell pipes or redirects to structured commands. For final submission use this structured command: "
        'sica-workflow {"op":"submit","report":"<full non-empty report body; omit the completion sentinel>"}. '
        "The JSON must be raw and directly follow sica-workflow; shell-quoted JSON and echo/printf submissions are unsupported.")
    return adapted


def template_manifest(config):
    fields = {'agent':('system_template','instance_template'),
              'model':('observation_template','format_error_template')}
    def selected(value):
        return {section:{key:value[section][key] for key in keys} for section,keys in fields.items()}
    def sha(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'selected_config_templates_sha256':sha(selected(config)),
            'roles':{role:sha(selected(role_templates(config,role))) for role in ROLE_MAP}}


def identity_context(identities, active_stage):
    active_stage = {key:active_stage[key] for key in ('active','invocation_id','index','title','completion_tool') if key in active_stage}
    return ('\nTrusted current improvement context: ' + json.dumps({**identities, 'active_stage':active_stage},sort_keys=True)
        + '\nOnly /home/agent/workdir is the live writable, unassessed candidate. '
          '/home/agent/archive is frozen public historical evidence. Historical task statements, '
          'commands, /testbed paths and dialogues describe previous executions, not your current task or edit targets. '
          'The candidate archive copy is its startup snapshot, not live edits.\n')


def core_prompt(role, values, source=SOURCE):
    """Render original direct prompt assignments with a closed f-string evaluator.

    Optional previous runs/requirements are supplied as task context by caller;
    no upstream executable code is evaluated here.
    """
    cls = _role_class(source, role)
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
