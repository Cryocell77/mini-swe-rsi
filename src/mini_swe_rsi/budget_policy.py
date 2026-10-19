"""SICA budget scopes; provider capacities come from the configured endpoint."""
from .core import ROOT, digest


def sica_budget(model_max_output_tokens, context_max_tokens):
    if any(type(n) is not int or n <= 0 for n in (model_max_output_tokens, context_max_tokens)):
        raise ValueError('Model must advertise positive integer output/context capacities')
    if model_max_output_tokens >= context_max_tokens:
        raise ValueError('Output capacity must leave room for input')
    return {'main_iterations': 500,
            'role_iterations': {'archive_explorer': 500, 'software_developer': 1000,
                                'general_problem_solver': 500, 'reviewer': 500},
            'completion_max_requests': 5,
            # A benchmark task has a 600-second deadline.  Improvement passes 0,
            # which mini_swe_agent treats as no additional wall-clock limit.
            'benchmark_wall_seconds': 600, 'improvement_wall_seconds': 0,
            'tool_timeout_seconds': 600, 'command_min_seconds': 5,
            'command_max_seconds': 1800, 'command_default_seconds': 600,
            # Byte/resource limits for untrusted improvement shell output;
            # these are not token estimates or experiment time budgets.
            'stage_output_inline_bytes': 300000,
            'stage_output_max_bytes': 8 * 1024 * 1024,
            'stage_output_preview_bytes': 4096,
            'model_max_output_tokens': model_max_output_tokens,
            'context_max_tokens': context_max_tokens}


def policy_contract():
    return {'version': 'sica-budget-scopes-v2', 'sha256': digest(ROOT/'src/mini_swe_rsi/budget_policy.py')}
