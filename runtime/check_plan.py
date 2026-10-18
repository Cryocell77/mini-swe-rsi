"""Pure validation and normalization for the offline mechanism check plan."""

from pathlib import Path


def normalize_mechanism_plan(plan):
    """Return the canonical mechanism plan or raise ``ValueError``.

    This module deliberately has no workflow, filesystem, or container
    dependencies.  The host and the isolated runtime load it independently so
    both sides validate the same small input contract.
    """
    if not isinstance(plan, dict):
        raise ValueError('Mechanism check plan must be an object')
    if 'entrypoint' in plan:
        raise ValueError('Mechanism check plan does not support entrypoint; use test_path')
    allowed = {'test_path', 'args', 'timeout_seconds'}
    unknown = set(plan) - allowed
    if unknown:
        raise ValueError('Unknown mechanism check plan field')

    relative = plan.get('test_path')
    if (not isinstance(relative, str) or not relative
            or Path(relative).is_absolute() or '..' in Path(relative).parts
            or Path(relative).suffix != '.py'):
        raise ValueError('Mechanism check requires a relative Python test_path')

    args = plan.get('args', [])
    if (not isinstance(args, list) or len(args) > 32
            or any(not isinstance(arg, str) for arg in args)):
        raise ValueError('Mechanism check args must be a short string list')

    timeout = plan.get('timeout_seconds', 60)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Mechanism check timeout must be 1..600 seconds')
    return {'test_path': relative, 'args': list(args),
            'timeout_seconds': timeout}
