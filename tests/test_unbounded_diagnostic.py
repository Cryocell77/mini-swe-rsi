"""No added online clock; native per-session and task limits still apply."""
import io
import json
import math

import pytest

from mini_swe_rsi.broker import Broker, ModelError


def test_no_global_deadline_and_bounded_task_session_serialize_without_infinity(tmp_path, monkeypatch):
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda:dict(
        OPENAI_BASE_URL='http://fixture/v1',OPENAI_API_KEY='fixture',LOCAL_LLM_MODEL='fixture'))
    from mini_swe_rsi.context_budget import ContextBudget
    broker = Broker(tmp_path, context=ContextBudget({'context_window':65536,'max_output_tokens':8192}))
    broker.model = 'fixture'
    observed = []
    class Endpoint:
        def open(self, request, **kwargs):
            observed.append(kwargs['timeout'])
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'fixture'}}],
                'usage':{'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}}).encode())
    broker.opener = Endpoint()
    try:
        # Improvement sessions have no extra total or phase deadline, and the
        # host must wait without inventing a finite socket deadline.
        token = broker.session('proposal:fixture', seconds=None)
        assert broker.status(token)['remaining_seconds'] is None
        json.dumps(broker.status(token), allow_nan=False)
        broker.complete(token,{'messages':[{'role':'user','content':'fixture'}]})
        assert observed == [None]
        broker.sessions[token]['iterations']['main'] = 500
        with pytest.raises(ModelError,match='LimitsExceeded'):
            broker.complete(token,{'messages':[]})
        metrics = broker.finish(token)
        assert metrics['deadline'] is None and metrics['calls'] == 1
        json.dumps(metrics,allow_nan=False)
        # A benchmark task keeps its own real 600-second deadline.
        task = broker.session('task:fixture')
        assert math.isfinite(broker.sessions[task]['deadline'])
        assert 599 <= broker.status(task)['remaining_seconds'] <= 600
        broker.sessions[task]['deadline'] = 0
        with pytest.raises(ModelError,match='TimeExceeded'):
            broker.check_session(broker.sessions[task])
        # The removed persistent phase clock must not be recreated.
        assert not (tmp_path/'budget.json').exists()
    finally:
        broker.close()
