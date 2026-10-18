from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/Users/maxwell/Documents/mini-swe-rsi-old/data/swe-bench-lite-x86-recovery-validated')
MANIFEST_HASH = 'f3398c5087e1b2ea2e7ffeb5d2d0db7e69d912bb79d7bb9a0200aa737e1b6579'


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


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob('*')):
        if not p.is_file() or p.is_symlink() or '__pycache__' in p.parts:
            continue
        content = p.read_bytes()
        h.update(str(p.relative_to(path)).encode() + b'\0' + content)
    return h.hexdigest()


def attempt_result_paths(root, filename='host_result.json'):
    """Return durable task-result paths in retry order.

    Results are written below explicit, regular ``attempt_N`` directories.
    Keep retry ordering and the filesystem boundary in one place; callers still
    decide which result fields establish scoring, activation, or takeover
    authority.
    """
    root = Path(root)
    attempts = sorted(
        (p for p in root.glob('attempt_*') if p.is_dir() and not p.is_symlink()
         and p.name[8:].isascii() and p.name[8:].isdigit()),
        key=lambda p: (int(p.name[8:]), p.name),
    )
    return [p / filename for p in attempts]


def read_regular_json(path):
    """Read a durable JSON record only when it is a regular, non-link file."""
    path = Path(path)
    if not path.exists() or not path.is_file() or path.is_symlink():
        return None
    value = read(path)
    if not isinstance(value, dict):
        raise ValueError(f'JSON record is not an object: {path}')
    return value


def result_matches_source(result, expected_hashes):
    """Match a host result against a full or execution source digest."""
    hashes = {result.get(key) for key in ('code_hash', 'full_source_hash', 'execution_hash')} - {None}
    expected = set(expected_hashes) - {None}
    return bool(hashes) and bool(hashes.intersection(expected))


def execution_hash(path):
    """Hash the selected mini Harness runtime, including package resources."""
    h = hashlib.sha256()
    path = Path(path)
    excluded_root = {'README.md', 'LICENSE.mini', 'description.txt', 'agent_change_log.md'}
    for p in sorted(path.rglob('*')):
        if not p.is_file() or p.is_symlink() or '__pycache__' in p.parts:
            continue
        relative = p.relative_to(path)
        if relative.parts and relative.parts[0] != 'minisweagent' and p.name not in ('__init__.py', '__main__.py'):
            continue
        # These names are documentation files only at the archive root.  A
        # package resource with the same basename can be loaded at runtime and
        # must remain part of the immutable execution identity.
        if len(relative.parts) == 1 and p.name in excluded_root:
            continue
        h.update(str(relative).encode() + b'\0' + p.read_bytes())
    return h.hexdigest()


def command(args, timeout=60, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          timeout=timeout, check=True, **kwargs).stdout


def secrets():
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / '.env')
    names = {env[k] for k in ('LOCAL_LLM_MODEL', 'LLM_MODEL_NAME') if env.get(k)}
    if len(names) > 1:
        raise ValueError('Conflicting model names in .env')
    env['LOCAL_LLM_MODEL'] = next(iter(names), None)
    required = ['OPENAI_BASE_URL', 'OPENAI_API_KEY', 'LOCAL_LLM_MODEL']
    if any(not env.get(k) for k in required):
        raise ValueError('Missing model configuration in .env')
    config = {k: env[k] for k in required}
    return config


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
    from .budget_policy import policy_contract
    return {'version': 'sica-mini-improvement-v7',
            'limits': {'calls': None, 'seconds': None},
            'budget_policy': policy_contract(),
            'renderer_sha256': digest(ROOT/'runtime/bridge.py'),
            'upstream_task_sha256': digest(ROOT/'vendor/sica/agent_main.py'),
            'workflow_sha256': digest(ROOT/'runtime/workflow.py'),
            'check_plan_sha256': digest(ROOT/'runtime/check_plan.py'),
            'relay_sha256': digest(ROOT/'runtime/relay.py'),
            'workflow_sources': {p.name:digest(p) for p in sorted((ROOT/'vendor/sica_workflow').glob('*')) if p.is_file()}}
