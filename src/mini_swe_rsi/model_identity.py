"""Resolve provider identity and capacities without owning broker/session state."""
import hashlib
import json
import re
import urllib.parse
import urllib.request

from .context_budget import ContextBudget
from .model_protocol import ModelError


def canonical_parameters(parameters):
    """Ollama may reorder map entries; preserve values while freezing content."""
    lines = []
    for line in parameters.splitlines():
        fields = line.strip().split(None, 1)
        if fields:
            lines.append(' '.join(fields))
    return '\n'.join(sorted(lines)) + '\n'


def _capacity_fields(selected, metadata, fields, error):
    for standard, native in fields:
        value = metadata.get(native)
        if type(value) is not int or value <= 0 or (standard in selected and selected[standard] != value):
            raise ModelError(error)
        selected = {**selected, standard: value}
    return selected


def resolve_identity(config, remote, opener):
    hostname = urllib.parse.urlparse(config['OPENAI_BASE_URL']).hostname
    models = remote('/models')['data']
    requested = config['LLM_MODEL_NAME']
    selected = next((m for m in models if m['id'] == requested), None)
    if selected is None and ':' not in requested.rsplit('/', 1)[-1]:
        selected = next((m for m in models if m['id'] == requested + ':latest'), None)
    if selected is None:
        raise ModelError('Configured model is not advertised by the endpoint')
    proxy_catalog = None
    if (hostname in ('127.0.0.1', 'localhost', '::1')
            and selected.get('owned_by') in ('qoder', 'workbuddy')
            and ('context_window' not in selected or 'max_output_tokens' not in selected)):
        matches = [m for m in remote('/models?client_version=pi').get('models', [])
                   if m.get('slug') == selected['id']]
        if len(matches) != 1:
            raise ModelError('No unique proxy capacity declaration for configured model')
        entry = matches[0]
        proxy_catalog = {key: entry.get(key) for key in ('slug', 'context_window', 'max_context_window', 'max_tokens')}
        selected = _capacity_fields(selected, entry,
            (('context_window', 'context_window'), ('max_output_tokens', 'max_tokens')),
            'Invalid or conflicting proxy capacity declaration')
        maximum = entry.get('max_context_window')
        if maximum is not None and (type(maximum) is not int or maximum < selected['context_window']):
            raise ModelError('Invalid proxy maximum context declaration')
    catalog = None
    if hostname == 'token.sensenova.cn':
        catalog = {key: selected.get(key) for key in ('context_length', 'max_output_length',
            'supported_features', 'supported_sampling_parameters', 'quantization')}
        selected = _capacity_fields(selected, catalog,
            (('context_window', 'context_length'), ('max_output_tokens', 'max_output_length')),
            'Invalid or conflicting SenseNova capacity metadata')
    native = None
    if 'context_window' not in selected or 'max_output_tokens' not in selected:
        # Ollama's OpenAI model list omits capacities. Verify the native
        # service and the same model's digest instead of guessing a model.
        if hostname == 'api.deepseek.com':
            raise ModelError('Endpoint must advertise valid context_window and max_output_tokens')
        version = remote('/api/version').get('version')
        tags = remote('/api/tags').get('models', [])
        tag = next((m for m in tags if m.get('name') == selected['id']), None)
        if not isinstance(version, str) or not tag or not re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', tag.get('digest', '')):
            raise ModelError('Unverified Ollama service/model identity')
        request = urllib.request.Request(config['OPENAI_BASE_URL'].rstrip('/').removesuffix('/v1') + '/api/show',
            data=json.dumps({'model': selected['id'], 'verbose': False}).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+config['OPENAI_API_KEY']})
        try:
            with opener.open(request, timeout=30) as response:
                shown = json.load(response)
        except Exception as exc:
            raise ModelError('Ollama model metadata unavailable: ' + type(exc).__name__) from None
        if shown.get('remote_model') or shown.get('remote_host') or ':cloud' in selected['id']:
            raise ModelError('Ollama cloud model cannot be verified through local native metadata')
        parameters = shown.get('parameters', '')
        match = re.search(r'^num_ctx\s+(\d+)\s*$', parameters, re.MULTILINE)
        info = shown.get('model_info', {})
        architecture = info.get('general.architecture')
        maximum = info.get(str(architecture)+'.context_length')
        context = int(match.group(1)) if match else None
        if type(maximum) is not int or context is None or not 1 < context <= maximum:
            raise ModelError('Ollama must advertise an explicit valid num_ctx and architecture capacity')
        output_match = re.search(r'^num_predict\s+(-?\d+)\s*$', parameters, re.MULTILINE)
        num_predict = int(output_match.group(1)) if output_match else -1
        if num_predict not in (-1, -2) and num_predict <= 0:
            raise ModelError('Invalid Ollama num_predict')
        output = min(context-1, num_predict) if num_predict > 0 else context-1
        template = shown.get('template')
        if not isinstance(template, str) or not template:
            raise ModelError('Ollama chat template unavailable')
        native = {'provider':'ollama', 'version':version, 'digest':tag['digest'],
            'parameters':canonical_parameters(parameters), 'architecture_context_length':maximum,
            'template_sha256':hashlib.sha256(template.encode()).hexdigest(),
            'output_capacity_source':'positive num_predict bounded by context' if num_predict > 0 else 'derived context-1 bound; provider has no independent output cap',
            'num_predict':num_predict, 'cloud':False}
        selected = {**selected, 'digest':tag['digest'], 'context_window':context, 'max_output_tokens':output}
    try:
        context = ContextBudget(selected)
    except ValueError as exc:
        raise ModelError('Endpoint must advertise valid context_window and max_output_tokens') from exc
    model = selected['id']
    official = hostname == 'api.deepseek.com'
    prefix_protocol = 'deepseek-beta' if official and model.startswith('deepseek-') else None
    service_identity = {'capacity_source':'authenticated /models',
        'context_window':selected['context_window'], 'max_output_tokens':selected['max_output_tokens'],
        'owned_by':selected.get('owned_by'), 'api_capabilities':selected.get('api_capabilities'),
        'prefix_protocol':prefix_protocol}
    if catalog is not None:
        service_identity.update(capacity_source='authenticated SenseNova /models field mapping',
                                     sensenova_catalog=catalog)
    if proxy_catalog is not None:
        service_identity.update(capacity_source='authenticated loopback proxy /models?client_version=pi',
            proxy_catalog=proxy_catalog, underlying_model_verifiable=False)
    if native is not None:
        service_identity.update(capacity_source='authenticated Ollama /api/show', ollama=native)
    identity = {'requested':requested, 'model':model,
            'weights':selected.get('digest'), 'weights_verifiable':bool(selected.get('digest')),
            'temperature_requested':0, 'thinking':'provider default', 'reasoning_effort':'provider default',
            'max_tokens':context.contract['output_tokens'], 'model_concurrency':1}
    return identity, context, service_identity
