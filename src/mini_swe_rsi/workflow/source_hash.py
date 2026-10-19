"""Pure source-tree hashing shared by the host and standalone runtime.

This module intentionally depends only on the Python standard library.  The
host loads it with ``runpy`` and the container workflow does the same because
the container mounts ``runtime`` as a standalone directory.
"""

import hashlib
from pathlib import Path


_EXECUTION_ROOT_EXCLUSIONS = {
    'README.md',
    'LICENSE.mini',
    'description.txt',
    'agent_change_log.md',
}


def _digest(path, *, execution_only=False):
    """Hash a validated tree with the selected source identity rules."""
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f'Source hash root must be an existing directory: {path}')
    digest = hashlib.sha256()
    for item in sorted(root.rglob('*')):
        if item.is_symlink() or not item.is_file() or '__pycache__' in item.parts:
            continue
        relative = item.relative_to(root)
        if execution_only:
            if (relative.parts and relative.parts[0] != 'minisweagent'
                    and item.name not in ('__init__.py', '__main__.py')):
                continue
            # Documentation is excluded only at the archive root.  A package
            # resource with the same basename can be loaded at runtime.
            if len(relative.parts) == 1 and item.name in _EXECUTION_ROOT_EXCLUSIONS:
                continue
        digest.update(str(relative).encode() + b'\0' + item.read_bytes())
    return digest.hexdigest()


def tree_hash(path):
    """Hash every regular file in a source tree.

    A missing path, a file, or a symlink used as the root is invalid.  Child
    links and special files are ignored here; callers retain their existing
    validation guards and this function never follows a link.
    """
    return _digest(path)


def execution_hash(path):
    """Hash the selected mini Harness runtime, including package resources."""
    return _digest(path, execution_only=True)


candidate_source_hash = tree_hash


def workflow_hash(root):
    """Bind standalone workflow components under one implementation identity."""
    root = Path(root)
    digest = hashlib.sha256()
    for name in ('workflow.py', 'roles.py', 'review_cache.py'):
        digest.update(name.encode() + b'\0')
        digest.update(hashlib.sha256((root/name).read_bytes()).digest())
    return digest.hexdigest()
