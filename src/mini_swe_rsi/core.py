from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/Users/maxwell/Documents/mini-swe-rsi-old/data/swe-bench-lite-x86-recovery-validated')
MANIFEST_HASH = 'f3398c5087e1b2ea2e7ffeb5d2d0db7e69d912bb79d7bb9a0200aa737e1b6579'
PHASE_HOURS = dict(preflight=2, baseline=2, search=6, validation=2, simplify=2, final=9, report=1)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hash(path, behavioral=False):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob('*')):
        if not p.is_file() or p.is_symlink() or '__pycache__' in p.parts:
            continue
        content = p.read_bytes()
        if behavioral and p.suffix == '.py':
            import ast
            try:
                content = ast.dump(ast.parse(content), include_attributes=False).encode()
            except SyntaxError:
                pass
        elif behavioral and p.suffix in ('.yaml', '.yml'):
            import yaml
            try:
                content = json.dumps(yaml.safe_load(content), sort_keys=True).encode()
            except (ValueError, yaml.YAMLError):
                pass
        h.update(str(p.relative_to(path)).encode() + b'\0' + content)
    return h.hexdigest()


def command(args, timeout=60, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          timeout=timeout, check=True, **kwargs).stdout


def secrets():
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / '.env')
    required = ['OPENAI_BASE_URL', 'OPENAI_API_KEY', 'LOCAL_LLM_MODEL']
    if any(not env.get(k) for k in required):
        raise ValueError('Missing model configuration in .env')
    return {k: env[k] for k in required}


class Data:
    def __init__(self, path=DATA):
        self.path = Path(path)
        if digest(self.path / 'manifest.json') != MANIFEST_HASH:
            raise ValueError('Dataset manifest differs from the approved split')
        self.manifest = read(self.path / 'manifest.json')
        if digest(self.path / 'raw.json') != self.manifest['raw_sha256']:
            raise ValueError('Raw dataset hash mismatch')
        self.rows = {r['instance_id']: r for r in read(self.path / 'raw.json')}
        splits = self.manifest['splits']
        if [len(splits[k]) for k in ('dev', 'val', 'test')] != [30, 30, 100]:
            raise ValueError('Split coverage mismatch')
        flat = sum(splits.values(), [])
        if len(flat) != len(set(flat)):
            raise ValueError('Overlapping splits')
        self.images = {r['image'].split('.')[-1].split(':')[0]: r
                       for r in map(read, (self.path / 'images').glob('*.json'))}
        evidence = {digest(p): p for p in (self.path/'preflight').glob('*.json')}
        for task_id in flat:
            expected = self.manifest['eligibility']['evidence'][task_id]
            if expected not in evidence or not read(evidence[expected]).get('passed'):
                raise ValueError('Eligibility evidence changed or missing: ' + task_id)

    def ids(self, split):
        return list(self.manifest['splits'][split])

    def image(self, task_id):
        matches = [r for r in self.images.values()
                   if task_id.replace('__', '_1776_') in r['image']]
        if len(matches) != 1:
            raise ValueError(f'No unique frozen image for {task_id}')
        return matches[0]

    def freeze_images(self, root):
        """Bind mutable old-project metadata to this experiment and prior scoring jobs."""
        root = Path(root)
        inventory = {pid:self.image(pid) for split in ('dev','val','test') for pid in self.ids(split)}
        path = root/'image_inventory.json'
        if path.exists():
            if read(path) != inventory:
                raise ValueError('Frozen image inventory changed across restart')
            return
        for job_file in (root/'private/grading').glob('*/job.json'):
            job = read(job_file)
            if job.get('frozen_image') != inventory[job['task']['instance_id']]:
                raise ValueError('Prior scoring image differs from current inventory')
        write(path, inventory)


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Wall-clock deadlines persist across restarts; a restart cannot reset time."""
    def __init__(self, root):
        self.path = Path(root) / 'budget.json'
        if not self.path.exists():
            write(self.path, {'started': time.time(), 'phases': {}})

    def enter(self, phase):
        d = read(self.path)
        if phase not in d['phases']:
            d['phases'][phase] = time.time()
            write(self.path, d)
        self.phase = phase
        self.check()

    def remaining(self):
        d = read(self.path)
        return min(d['started'] + 24 * 3600,
                   d['phases'][self.phase] + PHASE_HOURS[self.phase] * 3600) - time.time()

    def check(self):
        if self.remaining() <= 0:
            raise BudgetExceeded(f'{self.phase}: wall-clock budget exhausted')


def append_event(root, event):
    with (Path(root) / 'events.jsonl').open('a') as f:
        f.write(json.dumps({'time': time.time(), **event}, ensure_ascii=False) + '\n')


def improvement_contract():
    """Freeze the independent task renderer without changing the model protocol."""
    sources = ROOT/'vendor/sica_workflow'
    expected = read(ROOT/'upstream.lock.json')['sica']['workflow_files']
    actual = {p.name:digest(p) for p in sources.iterdir() if p.is_file()}
    if actual != {n:v['sha256'] for n,v in expected.items()}:
        raise RuntimeError('Pinned workflow sources differ from upstream lock')
    return {'version': 'sica-mini-improvement-v4',
            'limits': {'calls': 500, 'seconds': 600},
            'renderer_sha256': digest(ROOT/'runtime/bridge.py'),
            'upstream_task_sha256': digest(ROOT/'vendor/sica/agent_main.py'),
            'workflow_sha256': digest(ROOT/'runtime/workflow.py'),
            'relay_sha256': digest(ROOT/'runtime/relay.py'),
            'workflow_sources': {p.name:digest(p) for p in sorted((ROOT/'vendor/sica_workflow').glob('*')) if p.is_file()}}
