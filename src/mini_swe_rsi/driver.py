from __future__ import annotations

import json
import runpy
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import docker
import requests
import yaml

from .archive_view import archive_view
from .core import ROOT, append_event, command, digest, read, tree_hash, write, improvement_contract


class InfrastructureError(RuntimeError):
    pass


class GradingUnavailable(InfrastructureError):
    """A grading failure must never trigger another model-generated answer."""


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
        self.image = self.client.images.get('mini-swe-rsi-runtime:0.1')
        self.network = self.client.networks.create('mini-rsi-' + uuid.uuid4().hex[:12], internal=True)
        self.running = []
        self.transport = self.client.networks.create('mini-rsi-transport-' + uuid.uuid4().hex[:12])
        self.relay = self.client.containers.create(self.image.id, ['python', '/runtime/relay.py'],
            volumes={str(ROOT/'runtime'): {'bind': '/runtime', 'mode': 'ro'}},
            environment={'HOST_BROKER': f'http://host.docker.internal:{broker.port}'},
            network=self.transport.name, cap_drop=['ALL'], security_opt=['no-new-privileges'],
            mem_limit='128m', pids_limit=64, read_only=True, tmpfs={'/tmp':'size=16m'})
        self.network.connect(self.relay)
        self.relay.start()
        self.relay.reload()
        self.broker_url = 'http://' + self.relay.attrs['NetworkSettings']['Networks'][self.network.name]['IPAddress'] + ':8000'

    def close(self):
        for c in self.running:
            try:
                c.remove(force=True)
            except docker.errors.NotFound:
                pass
        self.relay.remove(force=True)
        self.network.remove()
        self.transport.remove()

    def frozen_image(self, task_id):
        frozen = self.data.image(task_id)
        try:
            image = self.client.images.get(frozen['image_id'])
        except docker.errors.ImageNotFound:
            image = self.client.images.pull(frozen['repo_digests'][0], platform='linux/amd64')
        if image.id != frozen['image_id'] or image.attrs['Architecture'] != 'amd64':
            raise InfrastructureError('Task image differs from frozen eligibility image')
        return image

    def controller(self, code, logs, command_args, token, archive=None, target=None, prompt=None):
        validate_code(code)
        selected_hash = tree_hash(code)
        logs = Path(logs).resolve()
        logs.mkdir(parents=True, exist_ok=True)
        mounts = {str(ROOT / 'runtime'): {'bind': '/runtime', 'mode': 'ro'},
                  str(ROOT / 'vendor/sica'): {'bind': '/upstream', 'mode': 'ro'},
                  str(logs): {'bind': '/logs', 'mode': 'rw'}}
        task_identity = None
        if command_args[0] == 'improve':
            if (logs/'workflow_state.json').exists():
                raise RuntimeError('Existing workflow state cannot be overwritten or silently resumed')
            mounts[str(ROOT/'vendor/sica_workflow')] = {'bind':'/workflow_sources','mode':'ro'}
            def argument(name):
                return command_args[command_args.index(name)+1]
            parent, proposer = int(argument('--parent-iter')), int(argument('--best-iter'))
            candidate = argument('--candidate-id')
            contract = improvement_contract()
            configuration = self.root/'configuration.json'
            if configuration.exists() and read(configuration).get('improvement_task') != contract:
                raise RuntimeError('Frozen improvement task contract changed before launch')
            render = runpy.run_path(str(ROOT/'runtime/bridge.py'))['improvement_prompt']
            rendered = render(proposer, int(argument('--current-iter')), parent, candidate,
                              ROOT/'vendor/sica/agent_main.py')
            template_manifest = runpy.run_path(str(ROOT/'runtime/workflow.py'))['template_manifest']
            selected_config = yaml.safe_load((Path(code)/'minisweagent/config/mini_textbased.yaml').read_text())
            task_identity = {'template_manifest':template_manifest(selected_config), 'protocol': contract['version'], 'parent': parent, 'proposer': proposer,
                             'candidate': candidate, 'prompt_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
                             'renderer_sha256': contract['renderer_sha256'],
                             'upstream_task_sha256': contract['upstream_task_sha256'],
                             'workflow_sha256':contract['workflow_sha256'],
                             'relay_sha256':contract['relay_sha256'],
                             'workflow_sources':contract['workflow_sources']}
            write(logs/'host_improvement_task.json', {'contract': contract, **task_identity})
        module = 'agent_code'
        if archive is not None:
            view = archive_view(archive, logs.parent/(logs.name + '_archive_view'))
            mounts[str(view.resolve())] = {'bind': '/home/agent/archive', 'mode': 'ro'}
            relative = Path(code).resolve().relative_to(Path(archive).resolve())
            if tree_hash(view/relative) != tree_hash(code):
                raise RuntimeError('Sanitized view source differs from selected archive')
            module = 'archive.' + '.'.join(relative.parts)
            if task_identity and relative != Path(f'agent_{task_identity["proposer"]}/agent_code'):
                raise RuntimeError('Improvement proposer differs from selected archived module')
            if task_identity and not (view/f'agent_{task_identity["parent"]}/agent_code').is_dir():
                raise RuntimeError('Improvement parent is missing from the frozen archive')
            if task_identity:
                parent_source = view/f'agent_{task_identity["parent"]}/agent_code/minisweagent'
                candidate_source = Path(target)/'minisweagent' if target else None
                if not candidate_source or not candidate_source.is_dir() or tree_hash(parent_source) != tree_hash(candidate_source):
                    raise RuntimeError('Improvement parent differs from candidate starting source')
        else:
            mounts[str(Path(code).resolve())] = {'bind': '/home/agent/agent_code', 'mode': 'ro'}
        if target:
            mounts[str(Path(target).resolve())] = {'bind': '/home/agent/workdir', 'mode': 'rw'}
        if prompt:
            mounts[str(Path(prompt).resolve())] = {'bind': '/task.txt', 'mode': 'ro'}
        env = {'PYTHONPATH': '/runtime:/home/agent', 'RSI_MODEL': self.broker.model,
               'RSI_BROKER': self.broker_url, 'RSI_TOKEN': token,
               'MSWEA_CONFIG_ROOT': '/tmp/mini-config', 'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
               'OPENAI_API_KEY': 'broker-only', 'DO_NOT_TRACK': '1'}
        if task_identity:
            env['RSI_IMPROVEMENT_PROTOCOL'] = task_identity['protocol']
        args = ['python', '-m', module, *command_args, '--logdir', '/logs']
        c = self.client.containers.run(self.image.id, args, detach=True, volumes=mounts,
                network=self.network.name, environment=env, cap_drop=['ALL'],
                security_opt=['no-new-privileges'], mem_limit='2g', nano_cpus=1000000000,
                pids_limit=128)
        self.running.append(c)
        if command_args[0] == 'improve':
            self.broker.sessions[token]['solver'] = c.id
            self.broker.sessions[token]['cwd'] = '/home/agent/workdir'
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
        timer = threading.Timer(max(.1, deadline-time.time()), stop_at_deadline)
        timer.daemon = True
        timer.start()
        try:
            try:
                result = c.wait(timeout=max(.1, deadline - time.time() + 10))
            except requests.exceptions.ReadTimeout:
                c.kill()
                result = {'StatusCode': 137}
                write(logs/'result.json', {'exit_status': 'TimeExceeded', 'submission': ''})
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
               'result_file': str(directory / 'result.json'), 'timeout': min(300, int(self.budget.remaining())),
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
        job = {**job, 'reference_code': '/home/agent/agent_code'}
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

    def task(self, code, task_id, output):
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
        return self.finish_task(task_id, output)

    def finish_task(self, task_id, output):
        output = Path(output).resolve()
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
            directory = self.root/'private/grading'/uuid.uuid4().hex
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
                  'num_cached_tokens': metrics['cached_tokens'], 'cost_estimate': 0.0,
                  'wall_time': saved['agent_seconds'], 'timed_out': result.get('exit_status') in ('TimeExceeded', 'TimeoutError'),
                  'cost_threshold_exceeded': False, 'timestamp': time.time()}
        write(output / 'host_result.json', {'result': result, 'metrics': metrics, 'grade': grade,
              'record': record, 'total_seconds': time.time()-saved['started'],
              'patch_base_commit': saved['patch_base_commit'],
              'code_hash': saved['code_hash'], 'behavioral_hash': saved['behavioral_hash']})
        return record

    def task_retry(self, code, task_id, output):
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
                    return self.finish_task(task_id, attempt_dir)
                # Retain interrupted/failed infrastructure attempts; no overwritten trajectories.
                if not (attempt_dir/'interrupted.json').exists():
                    write(attempt_dir/'interrupted.json', {'reason': 'Prior attempt did not complete; retained on resume.'})
                continue
            try:
                return self.task(code, task_id, attempt_dir)
            except GradingUnavailable:
                raise
            except InfrastructureError as e:
                append_event(self.root, dict(kind='infrastructure_error', task_id=task_id,
                                             attempt=attempt, error=str(e)))
                if attempt:
                    raise
        raise InfrastructureError('Both allowed task attempts are incomplete; refusing an additional inference retry')
