import copy

import pytest

from mini_swe_rsi.broker import adapt_response
from mini_swe_rsi.context_budget import ContextBudget, OUTPUT_TOKENS, TOTAL_TOKENS

ACTION = '```mswea_bash_command\necho hello\n```'


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


def test_actual_qwen_tokenizer_budget_boundary_and_multilingual():
    context = ContextBudget()
    measurement = context.measure([{'role': 'user', 'content': '中文 mixed text'}])
    assert measurement['host_text_tokens'] == len(context.tokenizer.encode('中文 mixed text', add_special_tokens=False).ids)
    limit = TOTAL_TOKENS - OUTPUT_TOKENS
    assert context.fits({'host_prompt_tokens_estimate': limit})
    assert not context.fits({'host_prompt_tokens_estimate': limit + 1})
    assert context.measure([{'role':'assistant', 'content':'', 'reasoning_content':'思考'}])['host_text_tokens'] > 0
    # Long compressible text is no longer rejected by a character surrogate.
    assert context.fits(context.measure([{'role':'user', 'content':'x' * 100000}]))
    assert not context.fits(context.measure([{'role':'user', 'content':'x ' * 32768}]))


def test_missing_or_corrupt_tokenizer_never_falls_back(tmp_path, monkeypatch):
    import mini_swe_rsi.context_budget as module
    from mini_swe_rsi.core import write, improvement_contract
    lock = tmp_path/'lock.json'
    monkeypatch.setattr(module, 'LOCK_PATH', lock)
    with pytest.raises(FileNotFoundError):
        module.ContextBudget()
    write(lock, {'library_version':'wrong', 'file':'missing', 'sha256':'bad'})
    with pytest.raises(RuntimeError, match='library version mismatch'):
        module.ContextBudget()


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


@pytest.mark.parametrize('num_ctx,family,accepted', [(65536,'qwen35',True),(16384,'qwen35',False),
                                                      (65536,'other',False),(None,'qwen35',False)])
def test_service_context_identity_is_separate_and_must_support_budget(num_ctx, family, accepted):
    import hashlib
    import io
    import json
    from types import SimpleNamespace
    from mini_swe_rsi.broker import Broker, ModelError
    broker = Broker.__new__(Broker)
    broker.config = {'OPENAI_BASE_URL':'https://fixture/v1','OPENAI_API_KEY':'secret'}
    broker.budget = SimpleNamespace(remaining=lambda:30)
    shown = {'parameters':f'num_ctx {num_ctx}' if num_ctx else 'temperature 1',
             'details':{'family':family},'template':'{{ .Prompt }}',
             'model_info':{'qwen35.context_length':262144}}
    metadata = {'tokenizer.ggml.tokens':['fixture'], 'tokenizer.ggml.merges':['a b'],
                'tokenizer.ggml.token_type':[1], 'tokenizer.ggml.pre':'qwen35'}
    shown['model_info'].update(metadata)
    tokenizer_sha = hashlib.sha256(json.dumps(metadata, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    broker.context = SimpleNamespace(contract={'total_tokens':32768,
        'tokenizer':{'service_tokenizer_sha256':tokenizer_sha}})
    broker.opener = SimpleNamespace(open=lambda *a, **k:io.BytesIO(json.dumps(shown).encode()))
    if accepted:
        identity = broker.service_configuration('fixed')
        assert identity['service_tokenizer_sha256'] == tokenizer_sha
        assert identity['configured_context_tokens'] == 65536
        assert identity['architecture_context_length'] == 262144
        assert identity['template_sha256'] == hashlib.sha256(shown['template'].encode()).hexdigest()
        shown['model_info']['tokenizer.ggml.tokens'] = ['changed']
        with pytest.raises(ModelError, match='does not match frozen'):
            broker.service_configuration('fixed')
        del shown['model_info']['tokenizer.ggml.merges']
        with pytest.raises(ModelError, match='metadata is incomplete'):
            broker.service_configuration('fixed')
    else:
        with pytest.raises(ModelError):
            broker.service_configuration('fixed')


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
