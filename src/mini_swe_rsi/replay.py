"""Isolated reference execution with recorded responses and tool outputs."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def canonical(messages):
    keys = ('role', 'content', 'reasoning_content')
    return [{k: m[k] for k in keys if m.get(k) is not None} for m in messages]


class ReplayStop(BaseException):
    pass


def run(job):
    reference_code = job.get('reference_code', job.get('h0_code'))
    sys.path.insert(0, reference_code)
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    import litellm
    import yaml
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    events = [e for e in job['events'] if e['kind'] in ('model_call', 'tool_call')]
    state = {'queries': 0, 'tools': 0, 'cursor': 0, 'changed': False, 'reason': None}

    def next_event(kind):
        if state['cursor'] >= len(events):
            state['reason'] = 'Observed trace ended; no unrecorded response is synthesized.'
            if job.get('candidate_exit_status') in ('Submitted', 'LimitsExceeded', 'RepeatedFormatError'):
                state.update(changed=True, reason='Reference attempts another operation after the candidate has terminated.')
            raise ReplayStop()
        event = events[state['cursor']]
        if event['kind'] != kind:
            state.update(changed=True, reason='Different ordering of model queries and executed commands.')
            raise ReplayStop()
        return event

    class RecordedModel(LitellmTextbasedModel):
        abort_exceptions = [*LitellmTextbasedModel.abort_exceptions, ReplayStop]
        def _query(self, messages, **kwargs):
            i = state['queries']
            observed = next_event('model_call')
            if 'response' not in observed:
                state['reason'] = 'Missing response; replay evidence is incomplete.'
                raise ReplayStop()
            if canonical(messages) != canonical(observed['messages']):
                state.update(changed=True, reason='Different model input under the same recorded responses and tool outputs.', witness_call=i+1)
                raise ReplayStop()
            state['queries'] += 1
            state['cursor'] += 1
            return litellm.ModelResponse(**observed['response'])

        def _calculate_cost(self, response):
            # The live adapter's unpriced fixed model uses the same zero protocol placeholder.
            return {'cost': 0.0}

    class RecordedEnvironment(LocalEnvironment):
        def execute(self, action, **kwargs):
            i = state['tools']
            observed = next_event('tool_call')
            if 'output' not in observed:
                state['reason'] = 'Missing raw tool output; replay evidence is incomplete.'
                raise ReplayStop()
            if action.get('command') != observed['command']:
                state.update(changed=True, reason='Different executed command under the same model response.', witness_tool=i+1)
                raise ReplayStop()
            state['tools'] += 1
            state['cursor'] += 1
            output = {k: observed[k] for k in ('output', 'returncode', 'exception_info')}
            self._check_finished(output)
            return output

        def get_template_vars(self, **kwargs):
            return {**super().get_template_vars(**kwargs), **job['platform'],
                    'cwd': '/testbed', 'machine': 'x86_64'}

    config = yaml.safe_load((Path(reference_code)/'minisweagent/config/mini_textbased.yaml').read_text())
    config['agent'].pop('mode', None)
    config['agent'].update(step_limit=30, cost_limit=0, wall_time_limit_seconds=600, output_path=None)
    config['model'].update(model_name='openai/'+job['model'], cost_tracking='ignore_errors')
    env = RecordedEnvironment(cwd='/testbed', timeout=60, env=config['environment'].get('env', {}))
    agent = DefaultAgent(RecordedModel(**config['model']), env, **config['agent'])
    ended_normally = False
    try:
        result = agent.run(job['task'])
        state['reference_exit_status'] = result.get('exit_status')
        ended_normally = True
    except ReplayStop:
        pass
    except Exception as exc:
        state['reason'] = 'Replay failed: '+type(exc).__name__
    if ended_normally and state['cursor'] < len(events):
        state.update(changed=True, reason='Reference terminates before a later operation observed in the candidate.')
    state['complete_prefix'] = state['cursor'] == len(events)
    state['live_model_calls'] = 0
    return state


if __name__ == '__main__':
    job = json.loads(Path(sys.argv[1]).read_text())
    result = run(job)
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
