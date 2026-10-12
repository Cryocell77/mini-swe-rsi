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
    lock = json.loads(LOCK_PATH.read_text())
    return {'algorithm': ALGORITHM, 'tokenizer': lock, 'total_tokens': TOTAL_TOKENS,
            'output_tokens': OUTPUT_TOKENS, 'framing_base_tokens': FRAMING_BASE,
            'framing_per_message_tokens': FRAMING_PER_MESSAGE}


class ContextBudget:
    def __init__(self):
        from tokenizers import Tokenizer
        self.contract = context_contract()
        identity = self.contract['tokenizer']
        if importlib.metadata.version('tokenizers') != identity['library_version']:
            raise RuntimeError('Frozen tokenizer library version mismatch')
        path = ROOT / identity['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != identity['sha256']:
            raise RuntimeError('Frozen tokenizer file digest mismatch')
        self.tokenizer = Tokenizer.from_file(str(path))

    def measure(self, messages):
        text_tokens = sum(len(self.tokenizer.encode(message[key], add_special_tokens=False).ids)
                          for message in messages for key in ('content', 'reasoning_content') if key in message)
        framing = FRAMING_BASE + FRAMING_PER_MESSAGE * len(messages)
        return {'host_text_tokens': text_tokens, 'host_framing_tokens': framing,
                'host_prompt_tokens_estimate': text_tokens + framing,
                'reserved_output_tokens': OUTPUT_TOKENS, 'total_token_budget': TOTAL_TOKENS}

    def fits(self, measurement):
        return measurement['host_prompt_tokens_estimate'] + OUTPUT_TOKENS <= TOTAL_TOKENS
