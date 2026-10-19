"""Provider capacities and reported usage define the context budget."""
from __future__ import annotations


def context_contract():
    return {'algorithm': 'provider-capacity-and-usage-v3',
            'capacity_source': 'authenticated provider metadata',
            'output_policy': 'declared model maximum',
            'post_request_check': 'actual prompt plus completion'}


class ContextBudget:
    def __init__(self, capabilities):
        if not isinstance(capabilities, dict):
            raise TypeError('Explicit provider capabilities are required')
        from .budget_policy import sica_budget
        try:
            policy = sica_budget(capabilities['max_output_tokens'], capabilities['context_window'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Explicit provider capabilities must include valid capacities') from exc
        self.contract = {**context_contract(), 'total_tokens': policy['context_max_tokens'],
                         'output_tokens': policy['model_max_output_tokens']}

    def measure(self, messages):
        return {'host_text_characters': sum(len(m[k]) for m in messages
                                            for k in ('content', 'reasoning_content') if k in m),
                'reserved_output_tokens': self.contract['output_tokens'],
                'total_token_budget': self.contract['total_tokens']}
