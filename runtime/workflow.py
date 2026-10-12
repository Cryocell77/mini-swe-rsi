"""Protected text transport for pinned SICA sequential structure and role definitions.

Upstream strings are extracted, never evaluated/imported. The host broker alone
owns actual call/time budgets; this module never contacts a model endpoint.
"""
import ast
import json
import shlex
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'vendor/sica_workflow'
if not SOURCE.exists():
    SOURCE = Path('/workflow_sources')


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


def instructions(role="orchestrator"):
    text = '''SICA callable transport: emit one normal mini bash action whose entire command is
sica-workflow '<JSON object>'
Operations: {"op":"invoke"}, {"op":"state"}, {"op":"complete","invocation_id":"...","tool":"<current completion tool>","success":true},
{"op":"delegate","role":"archive_explorer|software_developer|general_problem_solver","task":"complete self-contained instructions"},
{"op":"committee","context":"goals and evidence","proposal":"full design"}.
As the orchestrator, use only structured workflow operations, inspection, and final submission; arbitrary bash is unavailable. Invoke meta_improvement_reasoning_structure as early as reasonably possible. Only the current completion tool is callable. Stage success is your declaration, not host verification. All roles share the SAME 30 actual calls and 600 seconds with you; there is no additional role budget.
Read-only inspection operations: {"op":"archive","kind":"overview|best|worst|compare","iteration":0,"iterations":[0,1],"n":5} maps to original ArchiveAnalyzer.\n {"op":"read","path":"/home/agent/workdir/README.md"}, {"op":"list","path":"/home/agent/archive"}, {"op":"search","path":"/home/agent/workdir","pattern":"literal text"}.
Compatibility: original file views/archive analysis use these public read/list/search operations; editing and local tests use normal mini bash in editing roles. Original return_result/submit_answer map to mini submission. Return the FULL report with this command: printf '%s\\n' 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n<full report here>' (replace the placeholder, preserve the newline). Original python -m workdir.agent end-to-end example is unavailable: there is no hidden model endpoint; delegate an explicit test task through this transport and shared broker, or run offline tests. Independent benchmark grading remains host-controlled. Web research tools are unavailable. Original role system instructions and step strings follow unchanged; these transport mappings resolve old tool names.'''
    if role != 'orchestrator':
        start = text.index('Operations:')
        stop = text.index('Read-only inspection operations:')
        capabilities = 'This delegated role cannot invoke, inspect, or complete the parent reasoning structure. All roles share the same 30 actual model calls, 600 seconds, 60-second commands, 2048 output tokens and 32768 total context tokens; there is no fresh allowance. '
        if role in ('software_developer','general_problem_solver'):
            capabilities += 'Editing and local checks use normal mini bash. Committee is callable as {"op":"committee","context":"...","proposal":"..."}. '
        text = text[:start] + capabilities + '\n' + text[stop:]
    for filename in (('meta_improvement','committee_design') if role == 'orchestrator' else ('committee_design',) if role in ('software_developer','general_problem_solver') else ()):
        tree = ast.parse((SOURCE/(filename+'.py')).read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        description = next(ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TOOL_DESCRIPTION' for t in n.targets))
        text += '\nOriginal '+filename+' tool description:\n'+description
    return text



class Workflow:
    def __init__(self, logs, delegate, source=SOURCE):
        self.logs = Path(logs)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.delegate = delegate
        self.steps, self.specs, self.roles = definitions(source)
        self.active = None
        self.status = 'not_invoked'
        self.completed_steps = 0
        self.counter = 0
        # A fresh launch never resumes an interrupted model conversation or invents completions.
        if (self.logs/'workflow_state.json').exists():
            raise ValueError('Refusing to overwrite existing workflow state; interrupted execution is not resumable')
        self.save()

    def save(self):
        temporary = self.logs/'workflow_state.json.tmp'
        temporary.write_text(json.dumps({'active':self.active,'role_calls':self.counter,'status':self.status,'completed_steps':self.completed_steps}))
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

    def call_role(self, role, task, specialisation='', context=''):
        mapping = {'archive_explorer':'archive_explorer','software_developer':'coder','general_problem_solver':'problem_solver','reviewer':'review_committee_member'}
        if role not in mapping:
            raise ValueError('Unknown role')
        self.counter += 1
        identity = f'{self.counter:03d}_{role}'
        self.event(kind='role_start', role=role, identity=identity)
        try:
            definition = self.roles[mapping[role]]
            values = {'analysis_focus':task, 'programming_instructions':task, 'problem_statement':task,
                      'specialisation':specialisation, 'context':context, 'proposal':task}
            if role == 'archive_explorer':
                values['analysis'] = archive_overview()
            prompt = definition['SYSTEM_PROMPT']+'\n'+definition['AGENT_DESCRIPTION']+'\n'+core_prompt(mapping[role], values)+'\n'+instructions(role)
            result = self.delegate(identity, prompt, role in ('archive_explorer','reviewer'))
            self.event(kind='role_end', identity=identity, result=result)
            if result.get('exit_status') != 'Submitted':
                raise ValueError(f'Role {identity} failed: {result.get("exit_status")}')
            return result.get('submission','')
        except Exception as exc:
            self.event(kind='role_error', identity=identity, error=str(exc))
            raise

    def dispatch(self, request):
        op = request['op']
        if op == 'invoke':
            if self.active:
                raise ValueError('Structure already active')
            self.status = 'active'
            self.completed_steps = 0
            self.active = {'invocation_id':'reasoning_structure_invocation_'+uuid.uuid4().hex, 'index':0,'step_ids':['step_'+uuid.uuid4().hex for _ in self.steps]}
            self.event(kind='invoke', state=self.current())
            return self.current()
        if op == 'state':
            return self.current()
        if op == 'complete':
            if not self.active or request.get('invocation_id') != self.active['invocation_id'] or request.get('tool') != self.current()['completion_tool']:
                raise ValueError('Only the active invocation/current completion tool is available')
            success = request.get('success', True)
            if type(success) is not bool:
                raise ValueError('success must be boolean')
            previous = self.current()
            if success:
                self.completed_steps += 1
            if not success or self.active['index'] == len(self.steps)-1:
                self.active = None
                self.status = 'completed' if success else 'failed'
                output = 'Reasoning structure complete. Please proceed to complete the rest of the task as appropriate.' if success else f'Reasoning structure exited due to error in step {previous["step_ids"][previous["index"]]}. Please proceed to complete the rest of the task as appropriate.'
            else:
                self.active['index'] += 1
                output = self.current()
            self.event(kind='complete', previous=previous, declared_success=success, output=output)
            return output
        if op == 'delegate':
            if request.get('role') not in ('archive_explorer','software_developer','general_problem_solver'):
                raise ValueError('Only declared public delegate roles are callable')
            return self.call_role(request['role'], request['task'])
        if op == 'committee':
            reviews = ''
            for i,(name,spec) in enumerate(self.specs.items()):
                reviews += f"\n\nReviewer {i}'s review:\n"+self.call_role('reviewer',request['proposal'],spec,request['context'])
            return reviews
        if op == 'archive':
            analyzer = archive_analyzer()
            kind = request.get('kind','overview')
            if kind == 'overview':
                return analyzer.analyze_run_formatted()
            if kind == 'compare':
                return analyzer.compare_iterations_formatted(request['iterations'])
            if kind in ('best','worst'):
                method = analyzer.get_best_performing_problems_formatted if kind == 'best' else analyzer.get_worst_performing_problems_formatted
                return method(request['iteration'], request.get('n',5), request.get('benchmark'))
            raise ValueError('Unknown archive analysis operation')
        if op in ('read','list','search'):
            return self.inspect(request)
        raise ValueError('Unknown workflow operation')

    def finish(self, result):
        if self.status not in ('completed','failed'):
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
            return path.read_text(errors='replace')[:30000]
        if op == 'list':
            return sorted(p.name for p in path.iterdir())[:1000]
        hits = []
        for p in ([path] if path.is_file() else path.rglob('*')):
            if p.is_file() and any(p.resolve() == r or r in p.resolve().parents for r in roots):
                try:
                    for i,line in enumerate(p.read_text(errors='replace').splitlines()):
                        if request['pattern'] in line:
                            hits.append(f'{p}:{i+1}:{line[:300]}')
                            if len(hits) >= 100:
                                return hits
                except OSError:
                    pass
        return hits

    def execute(self, action, readonly=False):
        command = action.get('command','')
        try:
            parts = shlex.split(command)
            if parts and parts[0] == 'sica-workflow':
                if len(parts) != 2:
                    raise ValueError('Expected one JSON argument')
                request = json.loads(parts[1])
                if readonly == 'editor' and request.get('op') not in ('read','list','search','archive','committee','delegate'):
                    raise ValueError('Delegates cannot alter the parent workflow lifecycle')
                if readonly is True and request.get('op') not in ('read','list','search','archive'):
                    raise ValueError('Read-only role cannot invoke mutable workflow operations')
                return {'output':json.dumps(self.dispatch(request)), 'returncode':0}
            if readonly and readonly != 'editor':
                if len(parts) >= 2 and parts[0] == 'echo' and parts[1] == 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' and not any(p in (';', '&&', '||', '|', '>', '<') for p in parts[2:]):
                    return {'output':' '.join(parts[1:])+'\n','returncode':0}
                # Parse the official printf submission without running a shell.
                if len(parts) == 3 and parts[0] == 'printf' and parts[1] == '%s\\n' and parts[2].startswith('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n'):
                    return {'output':parts[2]+'\n','returncode':0}
                raise ValueError('Role permits only structured workflow operations or exact submission')
            return None
        except Exception as exc:
            self.event(kind='operation_error', error=str(exc))
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


def archive_analyzer():
    import importlib.util
    import sys
    source = Path('/upstream/archive_analysis.py')
    if not source.exists():
        source = Path(__file__).resolve().parents[1]/'vendor/sica/archive_analysis.py'
    spec = importlib.util.spec_from_file_location('rsi_original_archive_analysis', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ArchiveAnalyzer('/home/agent/archive')

def archive_overview():
    return archive_analyzer().analyze_run_formatted()

    # terminal recording lives outside transport dispatch and cannot mark stages done.
