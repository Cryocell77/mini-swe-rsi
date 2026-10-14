from __future__ import annotations

import json
import math
import runpy
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from pathlib import Path

import docker
import requests
import yaml

from .archive_view import archive_view, online_archive_view
from .core import ROOT, append_event, command, digest, read, tree_hash, execution_hash, write, improvement_contract


def session_remaining(deadline):
    return max(1, deadline-time.time()) if math.isfinite(deadline) else None


class InfrastructureError(RuntimeError):
    pass


class GradingUnavailable(InfrastructureError):
    """A grading failure must never trigger another model-generated answer."""


MECHANISM_BOOTSTRAP = r"""
import hashlib, os, runpy, sys
from pathlib import Path
try:
    root = Path(os.environ['RSI_SOURCE_ROOT']).resolve(strict=True)
    entry = Path(sys.argv[1]).resolve(strict=True)
    entry.relative_to(root)
    assert root == Path('/source') and Path.cwd() == root
    assert entry.is_file()
    Path(os.environ['MSWEA_GLOBAL_CONFIG_DIR']).mkdir(parents=True, exist_ok=True)
    expected = os.environ['RSI_MECHANISM_TEST_SHA256']
    assert hashlib.sha256(entry.read_bytes()).hexdigest() == expected
except Exception as exc:
    print('RSI_MECHANISM_SETUP_ERROR ' + type(exc).__name__, flush=True)
    sys.exit(125)
print('RSI_MECHANISM_SETUP_OK ' + expected, flush=True)
sys.path.insert(0, str(root))
sys.argv = sys.argv[1:]
runpy.run_path(str(entry), run_name='__main__')
"""


def validate_code(code):
    for p in Path(code).rglob('*'):
        if p.is_symlink() or not (p.is_dir() or p.is_file()):
            raise ValueError('Candidate contains a symlink or special file')
    if not (Path(code) / 'minisweagent/agents/default.py').is_file():
        raise ValueError('Candidate removed the agent entrypoint')
    if sum(p.stat().st_size for p in Path(code).rglob('*') if p.is_file()) > 32*1024*1024:
        raise ValueError('Candidate exceeds source size budget')


def initial_code(destination):
    destination = Path(destination)
    if destination.exists():
        return
    source = ROOT / '.upstream/mini/src/minisweagent'
    if not source.is_dir():
        raise RuntimeError('Run scripts/bootstrap.py to fetch pinned mini source')
    for name, expected in read(ROOT/'upstream.lock.json')['mini']['files'].items():
        if digest(source/name) != expected:
            raise RuntimeError('Pinned mini source changed: ' + name)
    destination.mkdir(parents=True)
    shutil.copytree(source, destination / 'minisweagent', ignore=shutil.ignore_patterns('__pycache__'))
    (destination / '__init__.py').write_text('')
    (destination / '__main__.py').write_text('from bridge import main\nmain(__file__)\n')
    shutil.copy(ROOT / '.upstream/mini/LICENSE.md', destination / 'LICENSE.mini')
    (destination / 'agent_change_log.md').write_text('# H0\nOfficial pinned mini-swe-agent; no evolved strategies.\n')
    (destination / 'description.txt').write_text('H0: official mini-swe-agent, fixed model and host execution adapter.\n')
    (destination / 'README.md').write_text(
        '# Archived mini-swe-agent\nEditable implementation: minisweagent/. '
        'Text action configuration: minisweagent/config/mini_textbased.yaml. '
        'The host provides the same model and fixed resource limits to every version. '
        'benchmark/improve entrypoints use the immutable bridge. '
        'No grader, reference patches or held-out evaluations are accessible. '
        'Dependency installation is not available inside isolated runs.\n')


class Driver:
    def __init__(self, root, data, broker, budget):
        self.root, self.data, self.broker, self.budget = Path(root), data, broker, budget
        self.client = docker.from_env()
        self.running = []
        self.network = None
        self.transport = None
        self.relay = None
        try:
            self.image = self.client.images.get('mini-swe-rsi-runtime:0.1')
            self.network = self.client.networks.create('mini-rsi-' + uuid.uuid4().hex[:12], internal=True)
            self.transport = self.client.networks.create('mini-rsi-transport-' + uuid.uuid4().hex[:12])
            self.relay = self.client.containers.create(self.image.id, ['python', '/runtime/relay.py'],
                volumes={str(ROOT/'runtime'): {'bind': '/runtime', 'mode': 'ro'}},
                environment={'HOST_BROKER': f'http://host.docker.internal:{broker.port}',
                             'RSI_RELAY_TIMEOUT':str(max(1,self.budget.remaining())+30)
                                 if math.isfinite(self.budget.remaining()) else 'none'},
                network=self.transport.name, cap_drop=['ALL'], security_opt=['no-new-privileges'],
                mem_limit='128m', pids_limit=64, read_only=True, tmpfs={'/tmp':'size=16m'})
            self.network.connect(self.relay)
            self.relay.start()
            self.relay.reload()
            self.broker_url = 'http://' + self.relay.attrs['NetworkSettings']['Networks'][self.network.name]['IPAddress'] + ':8000'
        except Exception:
            self._cleanup_resources()
            raise

    def _cleanup_resources(self):
        for c in list(self.running):
            try:
                c.remove(force=True)
            except Exception:
                pass
        self.running.clear()
        if self.relay is not None:
            try:
                self.relay.remove(force=True)
            except Exception:
                pass
            self.relay = None
        for network_name in ('network', 'transport'):
            network = getattr(self, network_name, None)
            if network is not None:
                try:
                    network.remove()
                except Exception:
                    pass
                setattr(self, network_name, None)

    def close(self):
        self._cleanup_resources()

    def frozen_image(self, task_id):
        frozen = self.data.image(task_id)
        try:
            image = self.client.images.get(frozen['image_id'])
        except docker.errors.ImageNotFound:
            image = self.client.images.pull(frozen['repo_digests'][0], platform='linux/amd64')
        if image.id != frozen['image_id'] or image.attrs['Architecture'] != 'amd64':
            raise InfrastructureError('Task image differs from frozen eligibility image')
        return image

    def controller(self, code, logs, command_args, token, archive=None, target=None, prompt=None,
                   resume_checkpoint=None):
        pause_guard = getattr(getattr(self, 'broker', None), '_raise_if_paused', None)
        if pause_guard is not None:
            pause_guard()
        if resume_checkpoint is not None and command_args[0] != 'improve':
            raise RuntimeError('Workflow restoration cannot change a benchmark launch')
        validate_code(code)
        selected_hash = tree_hash(code)
        logs = Path(logs).resolve()
        logs.mkdir(parents=True, exist_ok=True)
        mounts = {str(ROOT / 'runtime'): {'bind': '/runtime', 'mode': 'ro'},
                  str(ROOT / 'vendor/sica'): {'bind': '/upstream', 'mode': 'ro'},
                  str(logs): {'bind': '/logs', 'mode': 'rw'}}
        task_identity = None
        execution_budget = None
        if command_args[0] == 'improve':
            if (logs/'workflow_state.json').exists() and resume_checkpoint is None:
                raise RuntimeError('Existing workflow state cannot be overwritten or silently resumed')
            mounts[str(ROOT/'vendor/sica_workflow')] = {'bind':'/workflow_sources','mode':'ro'}
            def argument(name):
                return command_args[command_args.index(name)+1]
            parent, proposer = int(argument('--parent-iter')), int(argument('--best-iter'))
            candidate = argument('--candidate-id')
            online = '--online' in command_args
            resume = None
            if resume_checkpoint is not None:
                if not online or target is None:
                    raise RuntimeError('Explicit workflow restoration is online only')
                from .recovery import load_workflow_authorization
                resume = load_workflow_authorization(
                    resume_checkpoint, self.root, logs, target,
                    {'task_id':argument('--task-id'), 'round_id':argument('--round-id'),
                     'version':argument('--online-version'), 'parent':parent,
                     'proposer':proposer, 'candidate':candidate})
                mounts[str(Path(resume_checkpoint).resolve())] = {'bind':'/resume_authorization.json','mode':'ro'}
                command_args = [*command_args, '--resume-workflow', '/resume_authorization.json']
            contract = improvement_contract()
            configuration = self.root/'configuration.json'
            if configuration.exists():
                saved_configuration = read(configuration)
                frozen_contract = (saved_configuration.get('control_manifest', {}).get('improvement')
                                   if online else saved_configuration.get('improvement_task'))
                if frozen_contract != contract:
                    raise RuntimeError('Frozen improvement task contract changed before launch')
            execution_budget = {**self.broker.budget_limits(), 'global_remaining_seconds':session_remaining(self.broker.sessions[token]['deadline'])}
            shared_calls = execution_budget['main_iterations']
            render = runpy.run_path(str(ROOT/'runtime/bridge.py'))['improvement_prompt']
            render_kwargs = {'calls':shared_calls, 'budget':execution_budget}
            if online:
                render_kwargs.update(online=True,
                    task_id=argument('--task-id') if '--task-id' in command_args else None,
                    round_id=argument('--round-id') if '--round-id' in command_args else None,
                    version=argument('--online-version') if '--online-version' in command_args else f'H{parent:04d}')
                if prompt is not None and Path(prompt).exists():
                    render_kwargs['feedback'] = Path(prompt).read_text()
            rendered = render(proposer, int(argument('--current-iter')), parent, candidate,
                              ROOT/'vendor/sica/agent_main.py', **render_kwargs)
            if resume is not None:
                rendered = runpy.run_path(str(ROOT/'runtime/bridge.py'))['resume_prompt'](rendered, resume)
            template_manifest = runpy.run_path(str(ROOT/'runtime/workflow.py'))['template_manifest']
            selected_config = yaml.safe_load((Path(code)/'minisweagent/config/mini_textbased.yaml').read_text())
            task_identity = {'template_manifest':template_manifest(selected_config, shared_calls), 'protocol': contract['version'], 'parent': parent, 'proposer': proposer,
                             'candidate': candidate, 'prompt_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
                             'renderer_sha256': contract['renderer_sha256'],
                             'upstream_task_sha256': contract['upstream_task_sha256'],
                             'workflow_sha256':contract['workflow_sha256'],
                             'relay_sha256':contract['relay_sha256'],
                             'workflow_sources':contract['workflow_sources']}
            if online:
                task_identity.update(task_id=argument('--task-id'), round_id=argument('--round-id'),
                                     version=argument('--online-version'), online=True)
            if resume is not None:
                task_identity['resume_authorization_sha256'] = digest(resume_checkpoint)
            write(logs/'host_improvement_task.json', {'contract': contract, **task_identity})
        module = 'agent_code'
        if archive is not None:
            online_archive = bool(task_identity and task_identity.get('online'))
            view = (online_archive_view(archive, logs.parent/(logs.name + '_archive_view'))
                    if online_archive else archive_view(archive, logs.parent/(logs.name + '_archive_view')))
            mounts[str(view.resolve())] = {'bind': '/home/agent/archive', 'mode': 'ro'}
            if online_archive:
                # Online versions are Hxxxx/agent_code paths rather than the
                # native agent_N module hierarchy.  Load the selected source
                # directly while exposing the sanitized historical view.
                mounts[str(Path(code).resolve())] = {'bind': '/home/agent/agent_code', 'mode': 'ro'}
                module = 'agent_code'
                relative = Path(code).resolve().relative_to(Path(archive).resolve())
                if tree_hash(view / relative) != tree_hash(code):
                    raise RuntimeError('Online archive view source differs from selected active version')
                if target and resume_checkpoint is None and tree_hash(target) != selected_hash:
                    raise RuntimeError('Online candidate differs from selected active parent')
            else:
                relative = Path(code).resolve().relative_to(Path(archive).resolve())
                module = 'archive.' + '.'.join(relative.parts)
            if not online_archive and tree_hash(view/relative) != tree_hash(code):
                raise RuntimeError('Sanitized view source differs from selected archive')
            if not online_archive and task_identity and relative != Path(f'agent_{task_identity["proposer"]}/agent_code'):
                raise RuntimeError('Improvement proposer differs from selected archived module')
            if not online_archive and task_identity and not (view/f'agent_{task_identity["parent"]}/agent_code').is_dir():
                raise RuntimeError('Improvement parent is missing from the frozen archive')
            if not online_archive and task_identity:
                parent_source = view/f'agent_{task_identity["parent"]}/agent_code/minisweagent'
                candidate_source = Path(target)/'minisweagent' if target else None
                if not candidate_source or not candidate_source.is_dir() or tree_hash(parent_source) != tree_hash(candidate_source):
                    raise RuntimeError('Improvement parent differs from candidate starting source')
        else:
            mounts[str(Path(code).resolve())] = {'bind': '/home/agent/agent_code', 'mode': 'ro'}
            if task_identity and task_identity.get('online') and target:
                if resume_checkpoint is None and tree_hash(target) != selected_hash:
                    raise RuntimeError('Online candidate does not start from the active parent version')
        if target:
            mounts[str(Path(target).resolve())] = {'bind': '/home/agent/workdir',
                'mode': 'ro' if task_identity and task_identity.get('online') else 'rw'}
        if prompt:
            mounts[str(Path(prompt).resolve())] = {'bind': '/task.txt', 'mode': 'ro'}
        if execution_budget is None:
            execution_budget = {**self.broker.budget_limits(), 'global_remaining_seconds':session_remaining(self.broker.sessions[token]['deadline'])}
        env = {'RSI_BUDGET_CONFIG':json.dumps(execution_budget), 'PYTHONPATH': '/runtime:/home/agent', 'RSI_MODEL': self.broker.model,
               'RSI_BROKER': self.broker_url, 'RSI_TOKEN': token,
               'MSWEA_CONFIG_ROOT': '/tmp/mini-config', 'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
               'OPENAI_API_KEY': 'broker-only', 'DO_NOT_TRACK': '1'}
        if task_identity:
            env['RSI_IMPROVEMENT_PROTOCOL'] = task_identity['protocol']
            env['RSI_IMPROVEMENT_CALLS'] = str(shared_calls)
        args = ['python', '-m', module, *command_args, '--logdir', '/logs']
        c = self.client.containers.run(self.image.id, args, detach=True, volumes=mounts,
                network=self.network.name, environment=env, cap_drop=['ALL'],
                security_opt=['no-new-privileges'], mem_limit='2g', nano_cpus=1000000000,
                pids_limit=128)
        self.running.append(c)
        if command_args[0] == 'improve':
            self.broker.sessions[token]['solver'] = c.id
            self.broker.sessions[token]['cwd'] = '/home/agent/workdir'
            if task_identity and task_identity.get('online'):
                from .stage_tools import StageTools
                self.broker.sessions[token]['stage_tools'] = StageTools(
                    self, logs, target, view if archive is not None else None)
        append_event(self.root, dict(kind='agent_launch', code=str(Path(code).resolve()),
                     code_hash=tree_hash(code), behavioral_hash=tree_hash(code, True),
                     module=module, image_id=self.image.id, container_id=c.id,
                     command=command_args[0], improvement_task=task_identity, archive=str(archive) if archive else None,
                     mounts=[{'destination': m['Destination'], 'read_write': m['RW']} for m in c.attrs['Mounts']],
                     network_internal=True, cap_drop=c.attrs['HostConfig']['CapDrop']))
        deadline = self.broker.sessions[token]['deadline']
        expired = []
        def stop_at_deadline():
            expired.append(True)
            try:
                c.kill()
            except docker.errors.APIError:
                pass
        timer = None
        if math.isfinite(deadline):
            timer = threading.Timer(max(.1, deadline-time.time()), stop_at_deadline)
            timer.daemon = True
            timer.start()
        try:
            try:
                result = c.wait(timeout=max(.1, deadline - time.time() + 10) if math.isfinite(deadline) else None)
            except requests.exceptions.ReadTimeout:
                c.kill()
                result = {'StatusCode': 137}
                write(logs/'result.json', {'exit_status': 'TimeExceeded', 'submission': ''})
            if pause_guard is not None:
                pause_guard()
            if expired and not (logs/'result.json').exists():
                write(logs/'result.json', {'exit_status': 'TimeExceeded', 'submission': ''})
            if task_identity and (logs/'workflow_state.json').exists():
                workflow_state = read(logs/'workflow_state.json')
                if workflow_state.get('status') in ('active','not_invoked'):
                    workflow_state['status'] = 'incomplete'
                    workflow_state['host_process_status'] = result['StatusCode']
                    write(logs/'workflow_state.json', workflow_state)
            if task_identity and not (logs/'improvement_task.json').exists():
                write(logs/'improvement_verification.json', {'status': 'unverified_failure'})
                if result['StatusCode'] == 0:
                    raise RuntimeError('Successful improvement omitted prompt identity evidence')
            if task_identity and (logs/'improvement_task.json').exists():
                if (read(logs/'improvement_task.json') != task_identity
                    or digest(logs/'improvement_prompt.txt') != task_identity['prompt_sha256']):
                    raise RuntimeError('Executed improvement prompt identity differs from host renderer')
                write(logs/'improvement_verification.json', {'status': 'verified'})
            output = c.logs().decode(errors='replace')
            (logs / 'process.log').write_text(output.replace(token, '<session-token>'))
            if tree_hash(code) != selected_hash:
                raise RuntimeError('Selected archived source changed during execution')
            if archive is not None and tree_hash(view/relative) != selected_hash:
                raise RuntimeError('Mounted source changed during execution')
            return result['StatusCode'], output
        finally:
            stage_tools = self.broker.sessions[token].get('stage_tools')
            if stage_tools is not None:
                stage_tools.close()
            if timer is not None:
                timer.cancel()
            try:
                c.remove(force=True)
            except docker.errors.NotFound:
                pass
            self.running.remove(c)

    def score(self, task_id, patch, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        job = {'operation': 'score', 'directory': str(directory), 'task': self.data.rows[task_id],
               'patch': patch, 'arch': 'x86_64', 'image_namespace': 'swebench',
               'instance_image_tag': 'latest', 'run_id': uuid.uuid4().hex,
               'result_file': str(directory / 'result.json'), 'timeout': int(min(300, self.budget.remaining())),
               'frozen_image': self.data.image(task_id)}
        write(directory / 'job.json', job)
        python = ROOT / '.eval-venv/bin/python'
        try:
            with (directory / 'process.log').open('w') as log:
                subprocess.run([str(python), str(ROOT / 'src/mini_swe_rsi/grader.py'), str(directory / 'job.json')],
                        stdout=log, stderr=subprocess.STDOUT,
                        timeout=max(1, min(360, self.budget.remaining())), check=True)
            result = read(directory / 'result.json')
            if not result.get('completed'):
                raise InfrastructureError('Official grader produced no completed result')
            return result
        except (subprocess.SubprocessError, OSError) as exc:
            raise InfrastructureError(f'Official grader process: {type(exc).__name__}') from None
        finally:
            for container in self.client.containers.list(all=True):
                if job['run_id'] in container.name:
                    container.remove(force=True)

    def replay(self, code, job, directory):
        """Run untrusted archived code without network, credentials, tools or grader."""
        self.budget.check()
        validate_code(code)
        directory = Path(directory).resolve()
        job = {**job, 'reference_code': '/home/agent/agent_code', 'budget':self.broker.budget_limits()}
        write(directory/'job.json', job)
        output = directory/'output'
        output.mkdir(exist_ok=True)
        mounts = {str(Path(code).resolve()): {'bind':'/home/agent/agent_code','mode':'ro'},
                  str(ROOT/'src/mini_swe_rsi/replay.py'): {'bind':'/replay.py','mode':'ro'},
                  str(directory/'job.json'): {'bind':'/input/job.json','mode':'ro'},
                  str(output): {'bind':'/logs','mode':'rw'}}
        c = self.client.containers.run(self.image.id,
            ['python','/replay.py','/input/job.json','/logs/result.json'], detach=True,
            volumes=mounts, network_disabled=True, read_only=True, tmpfs={'/tmp':'size=64m'},
            environment={'LITELLM_LOCAL_MODEL_COST_MAP':'True','DO_NOT_TRACK':'1',
                         'MSWEA_CONFIG_ROOT':'/tmp/mini-config','XDG_CONFIG_HOME':'/tmp/config',
                         'XDG_CACHE_HOME':'/tmp/cache'},
            cap_drop=['ALL'], security_opt=['no-new-privileges'], mem_limit='2g',
            nano_cpus=1000000000, pids_limit=128,
            hostname=job.get('platform',{}).get('node'))
        append_event(self.root, {'kind':'behavior_replay_launch','code_hash':tree_hash(code),
                     'container_id':c.id,'network_disabled':True,'directory':str(directory),
                     'image_id':self.image.id})
        try:
            try:
                status = c.wait(timeout=max(1,min(60,self.budget.remaining())))['StatusCode']
            except requests.exceptions.ReadTimeout:
                c.kill()
                status = 137
            (directory/'process.log').write_bytes(c.logs())
            if status != 0 or not (output/'result.json').exists():
                return {'changed':False,'complete_prefix':False,'reason':'Isolated replay incomplete',
                        'process_status':status}
            return read(output/'result.json')
        finally:
            c.remove(force=True)

    def task(self, code, task_id, output, *, grading_namespace='grading'):
        self.budget.check()
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        frozen = self.frozen_image(task_id)
        solver = self.client.containers.run(frozen.id, ['sleep', 'infinity'], detach=True,
                    platform='linux/amd64', network_disabled=True, cap_drop=['ALL'],
                    security_opt=['no-new-privileges'], mem_limit='2g', nano_cpus=1000000000,
                    pids_limit=256, working_dir='/testbed')
        self.running.append(solver)
        # Public SWE-bench images include a preparation commit (e.g. file modes).
        # Collect only the agent's changes relative to this immutable image's HEAD.
        base = command(['docker', 'exec', '-w', '/testbed', solver.id, 'git', 'rev-parse', 'HEAD']).strip()
        token = self.broker.session('task:' + str(output.relative_to(self.root)), solver=solver.id)
        start = time.time()
        try:
            prompt = output / 'prompt.txt'
            prompt.write_text(self.data.rows[task_id]['problem_statement'])
            code_status, _ = self.controller(code, output, ['benchmark', '--prompt-file', '/task.txt'], token, prompt=prompt)
            result = (read(output / 'result.json') if (output / 'result.json').exists() else
                      {'exit_status': 'AgentProcessError', 'submission': '', 'process_status': code_status})
            # A hard deadline can interrupt the agent before it saves its own transcript.
            # Keep the host's failure result and usable public evidence, without another model call.
            write(output / 'result.json', result)
            transcript = ''
            if (output / 'trajectory.json').exists():
                saved = read(output / 'trajectory.json')
                transcript = '\n\n'.join(str(m.get('content', '')) for m in saved.get('messages', []))
            for filename, content in [('trace.txt', transcript), ('execution_tree.txt', transcript),
                                      ('answer.txt', result.get('submission', ''))]:
                if not (output / filename).exists():
                    (output / filename).write_text(content)
            if not (output / 'activation.json').exists():
                write(output / 'activation.json', {'command': 'benchmark', 'agent_activation_unobserved': True})
            patch = command(['docker', 'exec', '-w', '/testbed', solver.id, 'bash', '-lc',
                    'git add -A && git diff --cached --binary ' + base], timeout=min(30, self.budget.remaining()))
            (output / 'patch.diff').write_text(patch)
        finally:
            metrics = self.broker.finish(token)
            solver.remove(force=True)
            self.running.remove(solver)
        agent_seconds = time.time()-start
        write(output/'metrics.json', metrics)
        write(output/'task_execution.json', {'result': result, 'metrics': metrics,
              'agent_seconds': agent_seconds, 'started': start, 'patch_base_commit': base,
              'code_hash': tree_hash(code), 'behavioral_hash': tree_hash(code, True)})
        return self.finish_task(task_id, output, grading_namespace=grading_namespace)

    def finish_task(self, task_id, output, *, grading_namespace='grading'):
        output = Path(output).resolve()
        namespace_path = Path(grading_namespace) if isinstance(grading_namespace, str) else Path('.')
        if (not isinstance(grading_namespace, str) or not grading_namespace
                or namespace_path.is_absolute() or '..' in namespace_path.parts
                or '.' in namespace_path.parts):
            raise ValueError('grading_namespace must be a safe relative directory path')
        saved = read(output/'task_execution.json')
        result, metrics = saved['result'], saved['metrics']
        ledger = self.root/'private/task_grading'/output.relative_to(self.root)/'attempts.json'
        attempts = read(ledger) if ledger.exists() else []
        grade = None
        for item in attempts:
            result_file = Path(item['directory'])/'result.json'
            if result_file.exists() and read(result_file).get('completed'):
                grade = read(result_file)
                break
        while grade is None and len(attempts) < 2:
            self.budget.check()
            directory = self.root/'private'/grading_namespace/uuid.uuid4().hex
            attempts.append({'directory': str(directory), 'time': time.time()})
            write(ledger, attempts)
            try:
                grade = self.score(task_id, (output/'patch.diff').read_text(), directory)
            except InfrastructureError as exc:
                append_event(self.root, {'kind':'infrastructure_error', 'task_id': task_id,
                    'scope':'grading_only', 'attempt':len(attempts)-1, 'error':str(exc)})
        if grade is None:
            raise GradingUnavailable('Both scoring attempts failed; preserving the answer without another model run')
        record = {'problem_id': task_id, 'score': float(grade['resolved']), 'tokens_used': metrics['tokens'],
                  'num_cached_tokens': metrics['cached_tokens'],
                  'cost_estimate': metrics.get('cost_upper_bound_usd') if metrics.get('cost_accounting_available', False) else None,
                  'wall_time': saved['agent_seconds'], 'timed_out': result.get('exit_status') in ('TimeExceeded', 'TimeoutError'),
                  'cost_threshold_exceeded': result.get('exit_status') == 'CostExceeded', 'timestamp': time.time()}
        write(output / 'host_result.json', {'result': result, 'metrics': metrics, 'grade': grade,
              'record': record, 'total_seconds': time.time()-saved['started'],
              'patch_base_commit': saved['patch_base_commit'],
              'code_hash': saved['code_hash'], 'behavioral_hash': saved['behavioral_hash']})
        return record

    def task_retry(self, code, task_id, output, *, grading_namespace='grading'):
        for attempt in range(2):
            attempt_dir = Path(output)/f'attempt_{attempt}'
            if attempt_dir.exists():
                if (attempt_dir/'host_result.json').exists():
                    prior = read(attempt_dir/'host_result.json')
                    if prior.get('code_hash') != tree_hash(code):
                        raise GradingUnavailable('Completed task source changed; refusing mixed-version results')
                    return prior['record']
                if (attempt_dir/'task_execution.json').exists():
                    saved = read(attempt_dir/'task_execution.json')
                    if saved['code_hash'] != tree_hash(code):
                        raise GradingUnavailable('Saved task source changed before grading recovery')
                    return self.finish_task(task_id, attempt_dir,
                                           grading_namespace=grading_namespace)
                # Retain interrupted/failed infrastructure attempts; no overwritten trajectories.
                if not (attempt_dir/'interrupted.json').exists():
                    write(attempt_dir/'interrupted.json', {'reason': 'Prior attempt did not complete; retained on resume.'})
                continue
            try:
                return self.task(code, task_id, attempt_dir,
                                 grading_namespace=grading_namespace)
            except GradingUnavailable:
                raise
            except InfrastructureError as e:
                append_event(self.root, dict(kind='infrastructure_error', task_id=task_id,
                                             attempt=attempt, error=str(e)))
                if attempt:
                    raise
        raise InfrastructureError('Both allowed task attempts are incomplete; refusing an additional inference retry')

    @staticmethod
    def _mechanism_plan(plan):
        """Validate the small, deterministic offline mechanism test contract."""
        if not isinstance(plan, dict):
            raise InfrastructureError('Mechanism check plan must be an object')
        relative = plan.get('test_path', plan.get('entrypoint'))
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                or '..' in Path(relative).parts or Path(relative).suffix != '.py'):
            raise InfrastructureError('Mechanism check requires a relative Python test_path')
        args = plan.get('args', [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args) or len(args) > 32:
            raise InfrastructureError('Mechanism check args must be a short string list')
        timeout = plan.get('timeout_seconds', 60)
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise InfrastructureError('Mechanism check timeout must be 1..600 seconds')
        return {'test_path': relative, 'args': args, 'timeout_seconds': timeout}

    def _run_mechanism_process(self, source, test_file, args, timeout, *, test_path):
        """Preserve the declared test location in an isolated source overlay."""
        source, test_file = Path(source).resolve(), Path(test_file).resolve()
        relative = Path(self._mechanism_plan({'test_path': test_path})['test_path'])
        test_bytes = test_file.read_bytes()
        test_hash = hashlib.sha256(test_bytes).hexdigest()
        # The parent may not have tests/ at all. Never create mountpoints or
        # overwrite tests in an archived source; only the disposable overlay.
        with tempfile.TemporaryDirectory(prefix='mini-rsi-mechanism-') as temporary:
            overlay = Path(temporary)/'source'
            shutil.copytree(source, overlay, ignore=shutil.ignore_patterns('__pycache__'))
            entry = overlay/relative
            if entry.is_dir():
                raise InfrastructureError('Mechanism entry collides with a directory')
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_bytes(test_bytes)
            container = self.client.containers.run(self.image.id,
                ['python', '-c', MECHANISM_BOOTSTRAP, '/source/' + relative.as_posix(), *args], detach=True,
                volumes={str(overlay): {'bind': '/source', 'mode': 'ro'}},
                network_disabled=True, read_only=True, tmpfs={'/tmp': 'size=32m'},
                working_dir='/source', environment={'PYTHONPATH': '/source',
                    'RSI_SOURCE_ROOT':'/source', 'RSI_MECHANISM_TEST_SHA256':test_hash,
                    'PYTHONDONTWRITEBYTECODE':'1', 'DO_NOT_TRACK': '1',
                    'MSWEA_GLOBAL_CONFIG_DIR': '/tmp/mini-config', 'HOME':'/tmp'},
                cap_drop=['ALL'], security_opt=['no-new-privileges'], mem_limit='512m',
                nano_cpus=1000000000, pids_limit=64)
            self.running.append(container)
            try:
                try:
                    status = container.wait(timeout=timeout).get('StatusCode')
                except requests.exceptions.ReadTimeout:
                    try:
                        container.kill()
                    except docker.errors.APIError:
                        pass
                    raise InfrastructureError('Mechanism test process timed out; no parent failure conclusion')
                output = container.logs().decode(errors='replace')
                if (type(status) is not int or status in (125, 137)
                        or 'RSI_MECHANISM_SETUP_OK ' + test_hash not in output.splitlines()):
                    raise InfrastructureError('Mechanism execution setup invalid; no parent failure conclusion')
                return status, output
            finally:
                try:
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass
                self.running.remove(container)

    def mechanism_check(self, parent, candidate, plan, output, *, job_id=None,
                        check_revision='1'):
        """Run one frozen mechanism test against parent and candidate.

        The test file is copied from the candidate before either process starts,
        then mounted read-only into both fresh containers.  A model can propose
        the relative path, but cannot select a host path or execute it on the
        host.  The public result contains only execution identities and status;
        logs stay in the host check directory.
        """
        parent, candidate, output = (Path(parent).resolve(), Path(candidate).resolve(),
                                     Path(output).resolve())
        validate_code(parent)
        validate_code(candidate)
        frozen_plan = self._mechanism_plan(plan)
        test_source = candidate / frozen_plan['test_path']
        if not test_source.is_file() or test_source.is_symlink():
            raise InfrastructureError('Mechanism test path is absent from candidate')
        output.mkdir(parents=True, exist_ok=True)
        frozen_test = output / 'mechanism_test.py'
        test_bytes = test_source.read_bytes()
        if frozen_test.exists() and frozen_test.read_bytes() != test_bytes:
            raise InfrastructureError('Frozen mechanism test changed across retries')
        if not frozen_test.exists():
            frozen_test.write_bytes(test_bytes)
        test_hash = hashlib.sha256(test_bytes).hexdigest()
        before = {'parent': tree_hash(parent), 'candidate': tree_hash(candidate)}
        parent_status, parent_output = self._run_mechanism_process(
            parent, frozen_test, frozen_plan['args'], frozen_plan['timeout_seconds'],
            test_path=frozen_plan['test_path'])
        candidate_status, candidate_output = self._run_mechanism_process(
            candidate, frozen_test, frozen_plan['args'], frozen_plan['timeout_seconds'],
            test_path=frozen_plan['test_path'])
        after = {'parent': tree_hash(parent), 'candidate': tree_hash(candidate)}
        if before != after:
            raise InfrastructureError('Harness changed during mechanism check')
        logs = {}
        for name, text in (('parent', parent_output), ('candidate', candidate_output)):
            path = output / f'{name}.log'
            path.write_text(text)
            logs[name] = {'exit_status': parent_status if name == 'parent' else candidate_status,
                          'passed': (parent_status if name == 'parent' else candidate_status) == 0,
                          'log_ref': f'mechanism/{name}.log',
                          'output_sha256': hashlib.sha256(text.encode()).hexdigest()}
        return {'test_path': frozen_plan['test_path'], 'test_sha256': test_hash,
                'parent': logs['parent'], 'candidate': logs['candidate'],
                'execution_contract': {'version':'relative-source-overlay-v1', 'source_root':'/source',
                                       'test_entry':'/source/' + frozen_plan['test_path'], 'setup_valid':True},
                'same_test': True, 'parent_failed': parent_status != 0,
                'candidate_passed': candidate_status == 0}

    def candidate_check(self, candidate, task_id, output, *, job_id=None,
                        parent_record=None, check_revision='1', parent_code=None,
                        mechanism_plan=None):
        """Run a candidate in a fresh task process and return an immutable check.

        The candidate path is resolved by the host caller and is never accepted
        from a relay payload.  ``task_retry`` creates a new solver container and
        independent grader job, so this cannot accidentally reuse the parent's
        task process or hidden answer.
        """
        candidate = Path(candidate).resolve()
        output = Path(output).resolve()
        validate_code(candidate)
        before = tree_hash(candidate)
        execution_before = execution_hash(candidate)
        result = self.task_retry(candidate, task_id, output,
                                 grading_namespace='candidate_checks/proposals')
        after = tree_hash(candidate)
        execution_after = execution_hash(candidate)
        if before != after or execution_before != execution_after:
            raise InfrastructureError('Candidate source changed during independent check')
        host = output / 'attempt_0' / 'host_result.json'
        if not host.exists():
            host = output / 'host_result.json'
        evidence = read(host) if host.exists() else {}
        private = {'job_id':job_id, 'task_id':task_id, 'check_revision':str(check_revision),
                   'candidate_hash':after, 'execution_hash':execution_after,
                   'parent_hash':parent_record.get('code_hash') if parent_record else None,
                   'record':result, 'grade':evidence.get('grade'),
                   'execution_status':evidence.get('result',{}).get('exit_status'),
                   'metrics':evidence.get('metrics'), 'evidence_path':str(host)}
        if job_id:
            private_path = self.root / 'private/candidate_checks' / (str(job_id).replace('/', '_') + '.json')
            write(private_path, private)
        # Relay/model-facing output is restricted to public task facts.  Grader
        # details, host paths and token ledgers stay in the private snapshot.
        mechanism = None
        if mechanism_plan is not None:
            if parent_code is None:
                raise InfrastructureError('Mechanism check requires the active parent source')
            mechanism = self.mechanism_check(parent_code, candidate, mechanism_plan,
                                             output / 'mechanism', job_id=job_id,
                                             check_revision=check_revision)
            private['mechanism_check'] = mechanism
            if job_id:
                write(private_path, private)
        public_metrics = {}
        metrics = evidence.get('metrics') or {}
        for key in ('calls', 'wall_time', 'started'):
            if key in metrics:
                public_metrics[key] = metrics[key]
        return {'job_id':job_id, 'task_id':task_id, 'check_revision':str(check_revision),
                'candidate_hash':after, 'execution_hash':execution_after,
                'parent_hash':parent_record.get('code_hash') if parent_record else None,
                'score':result.get('score'), 'passed':bool(result.get('score')),
                'execution_status':evidence.get('result',{}).get('exit_status'),
                'metrics':public_metrics, 'mechanism_check':mechanism}
