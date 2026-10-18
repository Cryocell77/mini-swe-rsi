"""Reproducible source patch for the initial Harness; never patch evolved code."""
import subprocess

from .core import ROOT, digest, read


SEED_PATCH = ROOT/'src/mini_swe_rsi/assets/mini-no-billing.patch'
SEED_MANIFEST = ROOT/'src/mini_swe_rsi/assets/mini-no-billing.json'


def apply_seed_patch(package):
    manifest = read(SEED_MANIFEST)
    upstream = read(ROOT/'upstream.lock.json')['mini']
    if (manifest['version'] != 'mini-no-billing-v1'
            or manifest['upstream_commit'] != upstream['commit']
            or digest(SEED_PATCH) != manifest['patch_sha256']):
        raise RuntimeError('Initial Harness patch identity mismatch')
    # Only exact, locked package paths may be changed. Git applies the patch
    # in a temporary seed directory, outside any existing run or candidate.
    files = manifest['files']
    paths = [line[len('--- a/'):].strip() for line in SEED_PATCH.read_text().splitlines()
             if line.startswith('--- a/')]
    targets = [line[len('+++ b/'):].strip() for line in SEED_PATCH.read_text().splitlines()
               if line.startswith('+++ b/')]
    if paths != targets or len(paths) != len(set(paths)) or set(paths) != set(files):
        raise RuntimeError('Initial Harness patch paths mismatch')
    for name, hashes in files.items():
        if (name not in upstream['files'] or hashes['before'] != upstream['files'][name]
                or digest(package/name) != hashes['before']):
            raise RuntimeError('Initial Harness patch input mismatch: ' + name)
    command = ['git', 'apply', '--directory=minisweagent', str(SEED_PATCH)]
    subprocess.run([*command[:2], '--check', *command[2:]], cwd=package.parent,
                   capture_output=True, text=True, check=True)
    subprocess.run(command, cwd=package.parent, capture_output=True, text=True, check=True)
    for name, hashes in files.items():
        if digest(package/name) != hashes['after']:
            raise RuntimeError('Initial Harness patch output mismatch: ' + name)
