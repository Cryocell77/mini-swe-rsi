"""SICA budget scopes; provider capacities come from the configured endpoint."""
from .core import ROOT, digest, read


def sica_budget(model_max_output_tokens, context_max_tokens):
    if any(type(n) is not int or n <= 0 for n in (model_max_output_tokens, context_max_tokens)):
        raise ValueError('Model must advertise positive integer output/context capacities')
    if model_max_output_tokens >= context_max_tokens:
        raise ValueError('Output capacity must leave room for input')
    return {'main_iterations': 500,
            'role_iterations': {'archive_explorer': 500, 'software_developer': 1000,
                                'general_problem_solver': 500, 'reviewer': 500},
            'completion_max_requests': 5, 'summary_max_requests': 1,
            'benchmark_wall_seconds': 600, 'improvement_wall_seconds': 0,
            'tool_timeout_seconds': 600, 'command_min_seconds': 5,
            'command_max_seconds': 1800, 'command_default_seconds': 600,
            'model_max_output_tokens': model_max_output_tokens,
            'context_max_tokens': context_max_tokens,
            'fileview_regeneration_chars': 300000,
            'fileview_regeneration_implemented': False,
            'benchmark_cost_threshold_usd': 3,
            'cost_threshold_enforced': False,
            'cost_limitation': 'No frozen provider pricing contract; monetary threshold not reproduced'}


def policy_contract():
    return {'version': 'sica-budget-scopes-v1', 'sha256': digest(ROOT/'src/mini_swe_rsi/budget_policy.py'),
            'upstream_sources':read(ROOT/'upstream.lock.json')['sica']['budget_files']}
