"""Reproducible patch and trimming for H0; never rewrite evolved code."""
import subprocess

from ..core import ROOT, digest, read


SEED_PATCH = ROOT/'src/mini_swe_rsi/assets/mini-no-billing.patch'
SEED_MANIFEST = ROOT/'src/mini_swe_rsi/assets/mini-no-billing.json'
SLIM_MANIFEST = ROOT/'src/mini_swe_rsi/assets/mini-online-slim.json'


def apply_seed_trim(package, upstream, billing_files):
    """Remove only the audited standalone runner and benchmark paths."""
    manifest = read(SLIM_MANIFEST)
    expected = {name: billing_files.get(name, {}).get('after', sha)
                for name, sha in upstream['files'].items()
                if name == '__main__.py'
                or name.startswith('run/')
                or name.startswith('config/benchmarks/')}
    if (not isinstance(manifest, dict) or manifest.get('version') != 'mini-online-slim-v1'
            or manifest.get('upstream_commit') != upstream['commit']
            or manifest.get('removed_files') != expected):
        raise RuntimeError('Slim Harness manifest identity or removal list mismatch')
    for name, expected_hash in expected.items():
        target = package/name
        if target.is_symlink() or not target.is_file():
            raise RuntimeError('Slim Harness removal input missing: ' + name)
        if digest(target) != expected_hash:
            raise RuntimeError('Slim Harness removal input mismatch: ' + name)

    # All inputs are checked before any unlink, so a bad manifest or source
    # cannot leave a partially trimmed package in the published destination.
    for name in expected:
        (package/name).unlink()
    for root_name in ('run', 'config/benchmarks'):
        root = package/root_name
        if root.exists():
            for path in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
            root.rmdir()


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
    apply_seed_trim(package, upstream, files)
