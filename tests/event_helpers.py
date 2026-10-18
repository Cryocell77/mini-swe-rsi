"""Helpers for reading the broker's durable event audit in tests."""

import json


def broker_events(broker):
    """Return durable broker events without relying on an in-memory ledger."""
    path = broker.root / 'events.jsonl'
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A concurrent append can leave the final line mid-write; the
            # next poll will observe the complete durable record.
            continue
    return events
