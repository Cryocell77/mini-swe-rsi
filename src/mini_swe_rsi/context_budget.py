"""Frozen host estimate; endpoint prompt usage remains authoritative."""
from __future__ import annotations

import hashlib
import importlib.metadata

from .core import ROOT

# Conservative allowance for roles, separators, assistant generation prefix and
# endpoint-specific framing. This is not a claim to reproduce server templating.
FRAMING_BASE = 512
FRAMING_PER_MESSAGE = 32
LOCK_PATH = ROOT / 'tokenizer.lock.json'


def context_contract():
    return {'algorithm': 'provider-capacity-and-usage-v3',
            'host_tokenizer': None, 'capacity_source': 'authenticated provider metadata',
            'pre_request_estimate_enforced': False,
            'output_policy': 'declared model maximum', 'post_request_check': 'actual prompt plus completion'}


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
        self.tokenizer = None

    def enable_verified_text_audit(self, identity):
        """Service-verified text accounting; no claim of exact chat templating."""
        from tokenizers import Tokenizer
        if importlib.metadata.version('tokenizers') != identity['library_version']:
            raise RuntimeError('Frozen tokenizer library version mismatch')
        path = ROOT / identity['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != identity['sha256']:
            raise RuntimeError('Frozen tokenizer file digest mismatch')
        self.tokenizer = Tokenizer.from_file(str(path))
        self.contract['input_integrity'] = {'algorithm':'verified-qwen-content-v1',
            'tokenizer':identity, 'framing_base':FRAMING_BASE, 'framing_per_message':FRAMING_PER_MESSAGE,
            'exact_chat_template':False, 'minimum_generation_room':1,
            'claim':'verified content counting and gross truncation detection; not proof of exact provider prompt'}
        self.contract.update(host_tokenizer=identity, pre_request_estimate_enforced=True)

    def measure(self, messages):
        measurement = {'host_prompt_tokens_estimate': None,
                'host_text_characters': sum(len(m[k]) for m in messages for k in ('content','reasoning_content') if k in m),
                'reserved_output_tokens': self.contract['output_tokens'],
                'total_token_budget': self.contract['total_tokens'], 'host_estimate_enforced': False}
        if self.tokenizer is not None:
            # Ollama accepts content; replay of reasoning_content is not assumed.
            tokens = sum(len(self.tokenizer.encode(m['content'],add_special_tokens=False).ids) for m in messages)
            framing = FRAMING_BASE + FRAMING_PER_MESSAGE * len(messages)
            measurement.update(host_text_tokens=tokens, host_framing_tokens=framing,
                host_prompt_tokens_estimate=tokens+framing, host_estimate_enforced=True,
                input_integrity_algorithm='verified-qwen-content-v1')
        return measurement

    def fits(self, measurement):
        estimate = measurement['host_prompt_tokens_estimate']
        return estimate is None or estimate + 1 <= self.contract['total_tokens']
