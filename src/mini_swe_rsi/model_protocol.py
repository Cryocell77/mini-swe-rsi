"""Text-only model protocol and response normalization, independent of sessions."""
import copy
import re


class ModelError(RuntimeError):
    pass


TEXT_PROTOCOL = 'mini-text-bash-v4'


def normalize_messages(messages):
    """Fixed text protocol: one content string and optional assistant reasoning."""
    if not isinstance(messages, list):
        raise ModelError('Invalid messages')
    normalized = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant'):
            raise ModelError('Invalid text message role')
        if any(message.get(k) for k in ('tool_calls', 'function_call', 'tool_call_id')):
            raise ModelError('Native tools are outside the text bash protocol')
        content = message.get('content')
        if not isinstance(content, str):
            raise ModelError('Invalid text message content')
        clean = {'role': message['role'], 'content': content}
        reasoning = message.get('reasoning_content')
        if reasoning is not None:
            if message['role'] != 'assistant' or not isinstance(reasoning, str):
                raise ModelError('Invalid reasoning_content')
            clean['reasoning_content'] = reasoning
        normalized.append(clean)
    return normalized


_ACTION_TAG = re.compile(r'</?(?:tool_call|mswea_bash_command|bash_command)\b')
_FENCED_ACTION = re.compile(
    r'^```(?P<format>mswea_bash_command|bash)[ \t]*\r?\n'
    r'(?P<command>.*?)\r?\n^```[ \t]*(?=\r?$)',
    re.MULTILINE | re.DOTALL)
# Only delimiter lines frame a code block. Backticks in prose/JSON are data.
_FENCE_LINE = re.compile(r'^[ \t]*(?:`{3,}|~{3,})[^\r\n]*\r?$', re.MULTILINE)
_INLINE_CODE = re.compile(r'(?<!`)(`+)(?!`)[^\r\n]*?(?<!`)\1(?!`)')
_QUOTED_TEXT = re.compile(r'"(?:[^"\\\r\n]|\\[^\r\n])*"')
_QUOTED_EXAMPLES = re.compile(_INLINE_CODE.pattern + '|' + _QUOTED_TEXT.pattern)


_XML_ACTION = re.compile(
    r'(?P<outer><tool_call>\s*)?'
    r'<(?P<tag>mswea_bash_command|bash_command)>[ \t]*\r?\n?'
    r'(?P<command>.*?)</(?P=tag)>(?(outer)\s*</tool_call>)', re.DOTALL)


def _mask_quoted_examples(text):
    """Keep offsets while excluding inline code and quoted JSON/prose data."""
    # One pass respects the earliest outer quotation; two separate passes
    # could pair a backtick inside JSON with one beyond the closing XML tag.
    return _QUOTED_EXAMPLES.sub(lambda match: ' ' * len(match.group()), text)


def _single_text_action(text):
    """Parse structural envelopes, preserving quoted examples as command data."""
    fence_lines = list(_FENCE_LINE.finditer(text))
    if fence_lines:
        matches = list(_FENCED_ACTION.finditer(text))
        if len(matches) != 1 or len(fence_lines) != 2:
            raise ValueError('multiple, incomplete or unsupported fences')
        match = matches[0]
        # Each delimiter must belong to the one accepted block. This rejects
        # extra/malformed fence lines rather than selecting a convenient block.
        if any(not match.start() <= line.start() < match.end() for line in fence_lines):
            raise ValueError('mixed or incomplete action envelopes')
        kind = match['format']
        command = match['command']
    else:
        inspection = _mask_quoted_examples(text)
        matches = list(_XML_ACTION.finditer(inspection))
        if not matches and not _ACTION_TAG.search(inspection):
            return None
        if len(matches) != 1:
            raise ValueError('multiple or incomplete XML actions')
        match = matches[0]
        kind = 'xml:' + match['tag']
        command = text[match.start('command'):match.end('command')]
        if _ACTION_TAG.search(_mask_quoted_examples(command)):
            raise ValueError('nested XML actions')
    outside = _mask_quoted_examples(text[:match.start()] + text[match.end():])
    if _ACTION_TAG.search(outside):
        raise ValueError('mixed or incomplete action envelopes')
    if not command.strip():
        raise ValueError('empty action')
    return match, kind, '```mswea_bash_command\n' + command + '\n```'


def adapt_response(response):
    """Normalize one explicit text action; retain the untouched provider evidence."""
    returned = copy.deepcopy(response)
    choices = returned.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        return returned, 'unchanged: expected one choice'
    choice = choices[0]
    if not isinstance(choice, dict):
        return returned, 'unchanged: invalid choice'
    message = choice.get('message', {})
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return returned, 'unchanged: invalid assistant message'
    if choice.get('finish_reason') != 'stop':
        return returned, 'unchanged: non-stop finish'
    if any(message.get(key) for key in ('tool_calls', 'function_call', 'refusal')):
        return returned, 'unchanged: native tool or refusal'
    content = message.get('content')
    if content not in (None, ''):
        if not isinstance(content, str):
            return returned, 'unchanged: invalid content'
        try:
            action = _single_text_action(content)
        except ValueError as exc:
            # Merely returning mixed content is unsafe: mini might execute the
            # one canonical block while ignoring a second XML/bash action.
            message['content'] = 'Action format rejected: ' + str(exc)
            return returned, 'rejected ambiguous content: ' + str(exc)
        if action is None or action[1] == 'mswea_bash_command':
            return returned, 'unchanged: nonempty content'
        match, kind, canonical = action
        message['content'] = content[:match.start()] + canonical + content[match.end():]
        return returned, 'normalized single complete content action: ' + kind
    fields = {key: message[key] for key in ('reasoning_content', 'reasoning') if message.get(key) is not None}
    if not fields or any(not isinstance(value, str) for value in fields.values()):
        return returned, 'unchanged: missing or invalid reasoning'
    if len(set(fields.values())) != 1:
        return returned, 'unchanged: conflicting reasoning fields'
    reasoning = next(iter(fields.values()))
    try:
        action = _single_text_action(reasoning)
    except ValueError:
        action = None
    if action is None:
        return returned, 'unchanged: reasoning has no unique complete action'
    match, kind, canonical = action
    message['content'] = canonical
    for key in fields:
        message[key] = (reasoning[:match.start()] + reasoning[match.end():]).strip()
    return returned, 'promoted single complete reasoning action; removed action from ' + ','.join(fields) + '; format=' + kind
