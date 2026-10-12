"""Immutable entrypoint; all editable agent/model modules load from the selected archive."""
import argparse
import ast
import json
import os
import platform
import sys
import time
import urllib.request
from pathlib import Path

import yaml


def rpc(path, value):
    request = urllib.request.Request(os.environ['RSI_BROKER'] + path, json.dumps(value).encode(),
              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['RSI_TOKEN']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=185) as f:
        return json.load(f)


def improvement_prompt(best, current):
    tree = ast.parse(Path('/upstream/agent_main.py').read_text())
    func = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'improve_agent')
    strings = [n.value for n in ast.walk(func) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    # Use the original upstream task; substitute unavailable SICA-specific tool terminology only.
    original = next(n for n in ast.walk(func) if isinstance(n, ast.JoinedStr) and any(
        isinstance(v, ast.Constant) and 'It is meta-improvement time.' in str(v.value) for v in n.values))
    text = ''
    for value in original.values:
        if isinstance(value, ast.Constant):
            text += str(value.value)
        else:
            name = value.value.id
            text += str({'best_iter': best, 'current_iter': current,
                         'testing_procedure': 'You can inspect and syntax-check Python locally. Benchmark grading is host-controlled.'}[name])
    text = text.replace('/home/sandbox/workdir', '/home/agent/workdir')
    text = text.replace('invoke the meta improvement reasoning structure as early as reasonably possible',
                        'inspect the archive using the available bash command interface')
    text = text.replace('using the archive exploration tools available to you', 'reading the archive with bash')
    text = text.replace('(call a sub-agent to)', '')
    return text


def main(entry):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['benchmark', 'improve'])
    parser.add_argument('--prompt-file')
    parser.add_argument('--logdir', required=True)
    parser.add_argument('--workdir', default='/home/agent/workdir')
    parser.add_argument('--best-iter', type=int, default=0)
    parser.add_argument('--current-iter', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--cost-threshold', default=3)
    args = parser.parse_args()
    code = Path(entry).resolve().parent
    sys.path.insert(0, str(code))
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
    class TaskEnvironment(LocalEnvironment):
        def execute(self, action, **kwargs):
            out = rpc('/tool', action)
            self._check_finished(out)
            return out
        def get_template_vars(self, **kwargs):
            return {**super().get_template_vars(**kwargs),
                    'cwd': '/testbed' if args.command == 'benchmark' else args.workdir,
                    'machine': 'x86_64' if args.command == 'benchmark' else platform.machine()}
    config = yaml.safe_load((code / 'minisweagent/config/mini_textbased.yaml').read_text())
    config['agent'].pop('mode', None)
    config['agent'].update(step_limit=30, cost_limit=0, wall_time_limit_seconds=600,
                           output_path=Path(args.logdir) / 'trajectory.json')
    model_config = config['model']
    model_config.update(model_name='openai/' + os.environ['RSI_MODEL'], cost_tracking='ignore_errors')
    model_config['model_kwargs'].update(api_base=os.environ['RSI_BROKER'] + '/v1',
                    api_key=os.environ['RSI_TOKEN'], temperature=0, max_tokens=2048,
                    num_retries=0, timeout=180)
    environment = TaskEnvironment(
        cwd='/testbed' if args.command == 'benchmark' else args.workdir, timeout=60,
        env=config['environment'].get('env', {}))
    agent = DefaultAgent(LitellmTextbasedModel(**model_config), environment, **config['agent'])
    logs = Path(args.logdir)
    logs.mkdir(parents=True, exist_ok=True)
    # This evidence is supplementary; host independently records the mounted source digest and model calls.
    activation = {'agent_file': str(Path(sys.modules[DefaultAgent.__module__].__file__).resolve()),
                  'config_file': str(code / 'minisweagent/config/mini_textbased.yaml'),
                  'platform': platform.uname()._asdict(),
                  'command': args.command}
    (logs / 'activation.json').write_text(json.dumps(activation))
    task = Path(args.prompt_file).read_text() if args.command == 'benchmark' else improvement_prompt(args.best_iter, args.current_iter)
    start = time.time()
    try:
        result = agent.run(task)
    except Exception as exc:
        result = {'exit_status': type(exc).__name__, 'submission': ''}
    finally:
        agent.save(logs / 'trajectory.json')
    (logs / 'answer.txt').write_text(result.get('submission', ''))
    (logs / 'result.json').write_text(json.dumps(result))
    trace = '\n\n'.join(str(m.get('content', '')) for m in agent.messages)
    (logs / 'trace.txt').write_text(trace)
    (logs / 'execution_tree.txt').write_text(trace)
    print(f'0|0|0|{time.time()-start:.3f}')
    if result.get('exit_status') != 'Submitted':
        raise SystemExit(1)
