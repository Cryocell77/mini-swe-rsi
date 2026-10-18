"""Shared recursive filtering for public online evidence.

Callers still decide which top-level fields are public. This helper only
applies the common recursive credential and private-evidence filter to values
that a caller has already selected.
"""

PRIVATE_PUBLIC_KEYS = frozenset({
    'api_key', 'openai_api_key', 'rsi_token', 'authorization', 'password',
    'secret', 'credentials', 'raw_response', 'messages', 'prompt',
    'grade', 'evidence_path', 'private', 'session_token',
})


def public_value(value, key=''):
    """Return a recursively filtered copy of a selected public value."""
    lowered = key.lower()
    if lowered in PRIVATE_PUBLIC_KEYS or lowered.endswith('_api_key'):
        return '[redacted]'
    if isinstance(value, dict):
        return {
            name: public_value(item, name)
            for name, item in value.items()
            if name.lower() not in PRIVATE_PUBLIC_KEYS and not name.lower().endswith('_api_key')
        }
    if isinstance(value, list):
        return [public_value(item, key) for item in value]
    return value
