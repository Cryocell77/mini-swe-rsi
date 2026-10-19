"""Interactive local coding tasks using the same host-owned RSI loop."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import shlex
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import docker
import requests

from .core import digest, read, tree_hash, write, improvement_contract
from .driver import Driver, InfrastructureError, validate_code
from .online import OnlineLoop
from .online_contract import CHECK_POLICY_REVISION


class Versions:
    def __init__(self, root):
        self.root = Path(root)

    def entries(self):
        return sorted((p for p in (self.root/'versions').glob('V*')
                       if p.name[1:].isdigit() and p.is_dir()),
                      key=lambda p: int(p.name[1:]), reverse=True)

    def select(self, version=None, *, ask=input, show=print):
        entries = self.entries()
        if version is None and entries:
            while True:
                choice = ask('Harness：1 原始版本 / 2 晋升版本 [1]: ').strip() or '1'
                if choice in ('1', '2'):
                    break
            if choice == '2':
                for entry in entries:
                    info = read(entry/'metadata.json')
                    show(f"{entry.name}  {info['created']}  来源 {info['run']}")
                while version not in {p.name for p in entries}:
                    version = ask(f'选择版本号 [{entries[0].name}]: ').strip() or entries[0].name
        if version in (None, 'original'):
            return None, 'original'
        entry = next((p for p in entries if p.name == version), None)
        if entry is None:
            raise ValueError(f'Unknown single-input version: {version}')
        info = read(entry/'metadata.json')
        code = entry/'agent_code'
        validate_code(code)
        if tree_hash(code) != info['source_hash']:
            raise ValueError('Saved single-input version source changed')
        if info['control_manifest'] != OnlineLoop._control_manifest():
            raise ValueError('Saved version uses different framework code; select original')
        return code, version

    def publish(self, loop, parent):
        if loop.active_version == 'H0000':
            return None
        decisions = [read(p) for p in (loop.root/'decisions').glob('*.json')]
        if not any(d.get('decision') == 'accepted' for d in decisions):
            raise ValueError('Cannot publish a version without an accepted host decision')
        code = loop.active_code  # Revalidates the host-owned version identity.
        entries = self.entries()
        name = f"V{int(entries[0].name[1:]) + 1 if entries else 1:06d}"
        destination = self.root/'versions'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.publish-', dir=destination.parent) as temp:
            staging = Path(temp)/name
            shutil.copytree(code, staging/'agent_code')
            shutil.copyfile(code.parent/'version_identity.json', staging/'version_identity.json')
            write(staging/'metadata.json', {
                'version': name, 'parent': parent, 'run': str(loop.root),
                'run_version': loop.active_version, 'created': time.strftime('%Y-%m-%d %H:%M:%S %z'),
                'source_hash': tree_hash(staging/'agent_code'),
                'control_manifest': loop.control_manifest, 'decisions': decisions})
            os.rename(staging, destination)
        return name


def snapshot_workspace(workspace, destination, *, exclude=None):
    """Copy source, never follow links or include repository credentials/caches."""
    workspace, destination = Path(workspace).resolve(), Path(destination).resolve()
    if not workspace.is_dir():
        raise ValueError('Workspace must be an existing directory')
    if destination.is_relative_to(workspace):
        raise ValueError('Single-task run/store must be outside the input workspace')
    ignored = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules'}
    exclude = Path(exclude).resolve() if exclude else None
    def ignore(directory, names):
        return [n for n in names if n in ignored or n.startswith('.env')
                or Path(directory, n).resolve() == exclude]
    def copy(source, target):
        if Path(source).is_symlink():
            raise ValueError(f'Workspace symlinks are unsupported: {source}')
        return shutil.copy2(source, target)
    # symlinks=True avoids following directory links; reject all links afterwards.
    shutil.copytree(workspace, destination, ignore=ignore, copy_function=copy, symlinks=True)
    if any(p.is_symlink() for p in destination.rglob('*')):
        raise ValueError('Workspace symlinks are unsupported')


def prepare_image(client, root, workspace, image, *, verify_file=None):
    base = client.images.get(image)  # Resolve once; Dockerfile uses the immutable ID.
    context = root/'private/build'
    context.mkdir(parents=True)
    snapshot_workspace(workspace, context/'workspace', exclude=verify_file)
    (context/'Dockerfile').write_text(
        f'FROM {base.id}\nUSER root\nRUN rm -rf /testbed && mkdir /testbed\nWORKDIR /testbed\nCOPY workspace/ /testbed/\n'
        'RUN git init -q && git add -Af && git -c user.name=RSI -c user.email=rsi@localhost '
        'commit -qm baseline --allow-empty\n')
    frozen, _ = client.images.build(path=str(context), rm=True, network_mode='none',
                                    platform='linux/' + base.attrs['Architecture'])
    write(root/'image_inventory.json', {'single-task': {'image_id': frozen.id, 'base_image_id': base.id}})
    return frozen


class SingleDriver(Driver):
    def __init__(self, root, prompt, broker, task_image, check, check_timeout, verify_file=None):
        super().__init__(root, SimpleNamespace(rows={'single-task': {'problem_statement': prompt}}), broker)
        self.task_image = task_image
        self.task_platform = 'linux/' + task_image.attrs['Architecture']
        self.check = check
        self.check_timeout = check_timeout
        self.verify_file = verify_file

    def frozen_image(self, task_id):
        return self.task_image

    def score(self, task_id, patch, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'patch.diff').write_text(patch)
        if self.verify_file is not None:
            shutil.copyfile(self.verify_file, directory/'verify')
        # The command and optional private verifier are never mounted in the solver.
        (directory/'check.sh').write_text('set -e\n'
            + ('git apply --binary /verification/patch.diff\n' if patch.strip() else '')
            + 'exec bash -lc ' + shlex.quote(self.check) + '\n')
        container = self.client.containers.run(self.task_image.id,
            ['bash', '/verification/check.sh'], detach=True, network_disabled=True,
            volumes={str(directory): {'bind': '/verification', 'mode': 'ro'}},
            working_dir='/testbed', cap_drop=['ALL'], security_opt=['no-new-privileges'],
            mem_limit='2g', nano_cpus=1000000000, pids_limit=256)
        self.running.append(container)
        expired = []
        def stop_at_deadline():
            expired.append(True)
            try:
                container.kill()
            except docker.errors.APIError:
                pass
        timer = threading.Timer(self.check_timeout, stop_at_deadline)
        timer.daemon = True
        timer.start()
        try:
            status = container.wait(timeout=self.check_timeout + 10)['StatusCode']
            (directory/'process.log').write_bytes(container.logs())
            result = {'completed': True, 'resolved': status == 0 and not expired, 'exit_code': status,
                      'timed_out': bool(expired), 'image_id': self.task_image.id}
            write(directory/'result.json', result)
            return result
        except (docker.errors.APIError, requests.exceptions.RequestException) as exc:
            raise InfrastructureError(f'Local verifier infrastructure: {type(exc).__name__}') from exc
        finally:
            timer.cancel()
            container.remove(force=True)
            self.running.remove(container)


def run_single(args, *, ask=input, show=print):
    from .broker import Broker
    from .report import build_online_report
    store = args.store.resolve()
    if store.is_relative_to(args.workspace.resolve()):
        raise ValueError('Single-input store must be outside the input workspace')
    store.mkdir(parents=True, exist_ok=True)
    with (store/'.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another single-input session is using this store') from None
        versions = Versions(store)
        code, selected = versions.select(args.version, ask=ask, show=show)
        prompt = args.prompt or ask('请输入编程任务：\n> ').strip()
        if not prompt.strip():
            raise ValueError('Task prompt must not be empty')
        root = (args.run or store/'executions'/f'{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}').resolve()
        root.mkdir(parents=True, exist_ok=False)
        verifier = None
        if args.verify_file:
            verifier = root/'private/verify'
            verifier.parent.mkdir(parents=True)
            shutil.copyfile(args.verify_file, verifier)
        write(root/'single_task.json', {'prompt': prompt, 'workspace': str(args.workspace.resolve()),
              'check': args.check, 'check_timeout': args.check_timeout, 'selected_version': selected,
              'verifier_sha256': digest(verifier) if verifier else None})
        client = docker.from_env()
        broker = driver = loop = None
        show(f'运行目录：{root}\n使用版本：{selected}')
        try:
            image = prepare_image(client, root, args.workspace, args.image, verify_file=args.verify_file)
            broker = Broker(root)
            model_identity = broker.identity()
            driver = SingleDriver(root, prompt, broker, image, args.check, args.check_timeout, verifier)
            contract = {'protocol': OnlineLoop.protocol, 'tasks': ['single-task'],
                'data': {'kind': 'single-input', 'task_sha256': digest(root/'single_task.json'),
                         'image_inventory_sha256': digest(root/'image_inventory.json')},
                'runtime_image': driver.image.id, 'improvement_task': improvement_contract(),
                'check_policy': {'revision': CHECK_POLICY_REVISION, 'require_task_pass': True,
                    'criterion': 'current_task_failure_fixed_or_mechanism_parent_failed_candidate_passed'}}
            if (root/'transport_policy.json').exists():
                contract['transport_policy'] = read(root/'transport_policy.json')
            loop = OnlineLoop(root, ['single-task'], driver=driver, broker=broker, active_code=code,
                model_identity=model_identity, service_identity=broker.service_identity, contract=contract)
            result = loop.run()
            published = versions.publish(loop, selected)
            show(json.dumps({'run': str(root), 'result': result, 'promoted_version': published}, ensure_ascii=False))
            return result
        except Exception as exc:
            write(root/'failure.json', {'type': type(exc).__name__, 'reason': str(exc)[:500],
                                       'time': time.time(), 'command': 'single'})
            raise
        finally:
            if loop:
                loop.close()
            else:
                if driver:
                    driver.close()
                if broker:
                    broker.close()
            client.close()
            if (root/'configuration.json').exists():
                active_error = sys.exception()
                try:
                    build_online_report(root)
                except Exception:
                    if active_error is None:
                        raise
