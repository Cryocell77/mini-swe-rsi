"""Frozen host estimate; endpoint prompt usage remains the billing authority."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json

from .core import ROOT

TOTAL_TOKENS = 32768
OUTPUT_TOKENS = 2048
# Conservative allowance for roles, separators, assistant generation prefix and
# endpoint-specific framing. This is not a claim to reproduce server templating.
FRAMING_BASE = 512
FRAMING_PER_MESSAGE = 32
ALGORITHM = 'qwen-text-fields-v1'
LOCK_PATH = ROOT / 'tokenizer.lock.json'


def context_contract():
    return {'algorithm': 'provider-capacity-and-usage-v2',
            'host_tokenizer': None, 'capacity_source': 'authenticated provider metadata',
            'pre_request_estimate_enforced': False,
            'output_policy': 'declared model maximum', 'post_request_check': 'actual prompt plus completion'}


def legacy_context_contract():
    lock = json.loads(LOCK_PATH.read_text())
    return {'algorithm': ALGORITHM, 'tokenizer': lock, 'total_tokens': TOTAL_TOKENS,
            'output_tokens': OUTPUT_TOKENS, 'framing_base_tokens': FRAMING_BASE,
            'framing_per_message_tokens': FRAMING_PER_MESSAGE}


class ContextBudget:
    def __init__(self, capabilities=None):
        # Historical Qwen accounting is retained solely for offline replay/tests.
        # Live experiments always construct this with authenticated model metadata.
        self.provider = capabilities is not None
        if self.provider:
            from .budget_policy import sica_budget
            policy = sica_budget(capabilities['max_output_tokens'], capabilities['context_window'])
            self.contract = {**context_contract(), 'total_tokens': policy['context_max_tokens'],
                             'output_tokens': policy['model_max_output_tokens']}
            self.tokenizer = None
            return
        from tokenizers import Tokenizer
        self.contract = legacy_context_contract()
        identity = self.contract['tokenizer']
        if importlib.metadata.version('tokenizers') != identity['library_version']:
            raise RuntimeError('Frozen tokenizer library version mismatch')
        path = ROOT / identity['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != identity['sha256']:
            raise RuntimeError('Frozen tokenizer file digest mismatch')
        self.tokenizer = Tokenizer.from_file(str(path))

    def measure(self, messages):
        if self.provider:
            return {'host_prompt_tokens_estimate': None,
                    'host_text_characters': sum(len(m[k]) for m in messages for k in ('content','reasoning_content') if k in m),
                    'reserved_output_tokens': self.contract['output_tokens'],
                    'total_token_budget': self.contract['total_tokens'], 'host_estimate_enforced': False}
        text_tokens = sum(len(self.tokenizer.encode(message[key], add_special_tokens=False).ids)
                          for message in messages for key in ('content', 'reasoning_content') if key in message)
        framing = FRAMING_BASE + FRAMING_PER_MESSAGE * len(messages)
        return {'host_text_tokens': text_tokens, 'host_framing_tokens': framing,
                'host_prompt_tokens_estimate': text_tokens + framing,
                'reserved_output_tokens': OUTPUT_TOKENS, 'total_token_budget': TOTAL_TOKENS}

    def fits(self, measurement):
        if self.provider:
            return True  # The endpoint enforces its own tokenizer/context; no fabricated estimate.
        return measurement['host_prompt_tokens_estimate'] + OUTPUT_TOKENS <= TOTAL_TOKENS
