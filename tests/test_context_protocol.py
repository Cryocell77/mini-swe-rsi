import copy
import json

import pytest

from mini_swe_rsi.broker import adapt_response
from mini_swe_rsi.context_budget import ContextBudget


CAPABILITIES = {'context_window': 32768, 'max_output_tokens': 2048}

ACTION = '```mswea_bash_command\necho hello\n```'


def test_context_requires_explicit_provider_capabilities():
    with pytest.raises(TypeError, match='Explicit provider capabilities'):
        ContextBudget(None)


def response(reasoning=ACTION, content='', finish='stop', **extra):
    return {'choices': [{'finish_reason': finish, 'message': {
        'role': 'assistant', 'content': content, 'reasoning_content': reasoning, **extra}}]}


def test_promote_only_action_preserves_raw_response():
    raw = response()
    original = copy.deepcopy(raw)
    returned, why = adapt_response(raw)
    assert raw == original
    assert returned['choices'][0]['message']['content'] == ACTION
    assert why.startswith('promoted single complete reasoning action')
    assert returned['choices'][0]['message']['reasoning_content'] == ''


@pytest.mark.parametrize('raw', [response(ACTION + '\n' + ACTION),
    response(ACTION[:-3]), response(finish='length'), response(content='Existing'),
    response(tool_calls=[{'id': 'call'}]), response(function_call={'name': 'x'}),
    response(refusal='No'), response('```mswea_bash_command\n \n```'), response('plain thoughts')])
def test_ambiguous_reasoning_is_never_promoted(raw):
    returned, why = adapt_response(raw)
    assert returned == raw
    assert why.startswith('unchanged')






def test_only_action_extracted_from_thought_prose_and_raw_reasoning_alias():
    raw = response('Think before action.\n' + ACTION + '\nAfterthought.')
    returned, why = adapt_response(raw)
    message = returned['choices'][0]['message']
    assert message['content'] == ACTION
    assert message['reasoning_content'] == 'Think before action.\n\nAfterthought.'
    raw = response()
    message = raw['choices'][0]['message']
    message['reasoning'] = message.pop('reasoning_content')
    returned, _ = adapt_response(raw)
    assert returned['choices'][0]['message']['content'] == ACTION
    assert returned['choices'][0]['message']['reasoning'] == ''


def test_conflicting_reasoning_aliases_are_not_promoted():
    raw = response(reasoning='Other reasoning')
    raw['choices'][0]['message']['reasoning'] = ACTION
    assert adapt_response(raw)[0] == raw


def test_all_observed_empty_content_action_responses_offline():
    import json
    from mini_swe_rsi.core import ROOT
    path = ROOT/'runs/dataset-diagnostic-20261012-130957/events.jsonl'
    if not path.exists():
        pytest.skip('Read-only historical diagnostic evidence is local and untracked')
    observed = 0
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if event.get('kind') != 'model_call':
            continue
        raw = event.get('response', {})
        choices = raw.get('choices', [])
        if len(choices) != 1:
            continue
        message = choices[0].get('message', {})
        reasoning = message.get('reasoning_content', message.get('reasoning', ''))
        if not message.get('content') and '```mswea_bash_command' in reasoning:
            original = copy.deepcopy(raw)
            returned, why = adapt_response(raw)
            assert raw == original
            assert why.startswith('promoted single complete reasoning action')
            assert returned['choices'][0]['message']['content'].startswith('```mswea_bash_command\n')
            observed += 1
    assert observed == 11




@pytest.mark.parametrize('prose', [
    'The format marker is mswea_bash_command. Mentioning mswea_bash_command is not an action.',
    '格式是 `mswea_bash_command`，请输出一个 mswea_bash_command 命令块。',
    '<thought>Use mswea_bash_command for a read-only operation.</thought>',
])
def test_plain_marker_mentions_do_not_hide_one_complete_action(prose):
    raw = response(prose + '\n' + ACTION + '\nThe marker mswea_bash_command was used above.')
    original = copy.deepcopy(raw)
    returned, why = adapt_response(raw)
    assert returned['choices'][0]['message']['content'] == ACTION
    assert 'mswea_bash_command' in returned['choices'][0]['message']['reasoning_content']
    assert why.startswith('promoted')
    assert raw == original


@pytest.mark.parametrize('reasoning', [
    'Format mswea_bash_command.\n' + ACTION + '\n' + ACTION,
    'Format mswea_bash_command.\n' + ACTION + '\n```mswea_bash_command\necho unfinished',
    'Format mswea_bash_command.\n```bash\necho example\n```\n' + ACTION,
    'Format mswea_bash_command.\n' + ACTION + '\n```',
    'Format mswea_bash_command only; no command block.',
])
def test_marker_mentions_do_not_relax_fence_ambiguity_rules(reasoning):
    raw = response(reasoning)
    original = copy.deepcopy(raw)
    returned, why = adapt_response(raw)
    assert returned == original
    assert raw == original
    assert why.startswith('unchanged')
