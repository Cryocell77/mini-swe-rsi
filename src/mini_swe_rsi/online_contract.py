"""Shared online identity and policy checks; reporting may omit a policy."""

ONLINE_PROTOCOL = 'mini-swe-rsi-online-v7'
CHECK_POLICY_REVISION = '2'


def validate_online_contract(contract, *, require_policy=True, error_type=ValueError):
    if not isinstance(contract, dict):
        raise error_type('Online contract must be an object')
    keys = ('protocol', 'online_protocol')
    if (not any(key in contract for key in keys)
            or any(contract[key] != ONLINE_PROTOCOL for key in keys if key in contract)):
        raise error_type(f'Unsupported online protocol; only {ONLINE_PROTOCOL} is accepted')
    if 'common_tasks' in contract:
        raise error_type('Online contract common_tasks is unsupported')
    if not require_policy and 'check_policy' not in contract:
        return
    policy = contract.get('check_policy')
    if not isinstance(policy, dict):
        raise error_type('Online check policy must be an object')
    if 'common_tasks' in policy:
        raise error_type('Online check policy common_tasks is unsupported')
    if policy.get('revision') != CHECK_POLICY_REVISION:
        raise error_type('Online check policy revision is unsupported')
