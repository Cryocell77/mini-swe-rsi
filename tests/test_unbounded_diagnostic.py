"""No added diagnostic clock; native iteration/task limits still apply."""
import io
import json
import math

import pytest

from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.core import ROOT, Budget, read, write
from mini_swe_rsi.driver import session_remaining


def test_unbounded_persists_and_serializes_without_infinite_socket_timeout(tmp_path, monkeypatch):
    write(tmp_path/'budget.json', {'started':0,'phases':{'preflight':0},'unbounded_diagnostic':True})
    budget = Budget(tmp_path)
    budget.enter('preflight')
    assert math.isinf(budget.remaining())
    restarted = Budget(tmp_path)
    restarted.enter('preflight')
    assert math.isinf(restarted.remaining()) and read(budget.path)['started'] == 0
    monkeypatch.setattr('mini_swe_rsi.broker.secrets', lambda:dict(
        OPENAI_BASE_URL='http://fixture/v1',OPENAI_API_KEY='fixture',LOCAL_LLM_MODEL='fixture'))
    from mini_swe_rsi.context_budget import ContextBudget
    broker = Broker(tmp_path,budget,context=ContextBudget({'context_window':65536,'max_output_tokens':8192}))
    broker.model = 'fixture'
    observed = []
    class Endpoint:
        def open(self, request, **kwargs):
            observed.append(kwargs['timeout'])
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'fixture'}}],
                'usage':{'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}}).encode())
    broker.opener = Endpoint()
    try:
        token = broker.session('proposal:fixture',seconds=None)
        assert broker.status(token)['remaining_seconds'] is None
        json.dumps(broker.status(token),allow_nan=False)
        assert session_remaining(broker.sessions[token]['deadline']) is None
        broker.complete(token,{'messages':[{'role':'user','content':'fixture'}]})
        assert observed == [None]
        broker.sessions[token]['iterations']['main'] = 500
        with pytest.raises(ModelError,match='LimitsExceeded'):
            broker.complete(token,{'messages':[]})
        metrics = broker.finish(token)
        assert metrics['deadline'] is None and metrics['calls'] == 1
        json.dumps(metrics,allow_nan=False)
        task = broker.session('task:fixture')
        assert math.isfinite(broker.sessions[task]['deadline'])
        assert 599 <= broker.status(task)['remaining_seconds'] <= 600
        broker.sessions[task]['deadline'] = 0
        with pytest.raises(ModelError,match='TimeExceeded'):
            broker.check_session(broker.sessions[task])
    finally:
        broker.close()
