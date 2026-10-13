import asyncio
from types import SimpleNamespace

from mini_swe_rsi.core import write, read
from mini_swe_rsi.experiment import Experiment


def test_recursive_scope_never_runs_fixed_or_held_out(tmp_path):
    write(tmp_path/'baseline.json', {})
    write(tmp_path/'execution_scope.json', {'tracks':['recursive']})
    code = tmp_path/'code'
    code.mkdir()
    (code/'main.py').write_text('pass\n')
    calls = []
    async def proposal(run, generation, fixed=False):
        calls.append(('proposal', run, generation, fixed))
    async def benchmark(exp, agent):
        calls.append(('benchmark', str(exp)))
    async def forbidden():
        raise AssertionError('Held-out/probe evaluation opened')
    e = Experiment.__new__(Experiment)
    e.root = tmp_path
    e.budget = SimpleNamespace(enter=lambda _:None, check=lambda:None)
    e.adapter = SimpleNamespace(proposal=proposal, benchmark=benchmark,
                                best=lambda exp:4 if str(exp)=='results/run_1' else (_ for _ in ()).throw(AssertionError('fixed')))
    e.code = lambda *args:code
    e.assert_model = lambda:None
    e.mechanism = lambda:{'passed':True}
    e.recursion_unreachable = lambda _:False
    e.probes = e.validate = forbidden
    asyncio.run(e.evolve())
    assert [x for x in calls if x[0]=='proposal'] == [('proposal',1,g,False) for g in range(4)]
    assert len([x for x in calls if x[0]=='benchmark']) == 4
    assert not (tmp_path/'champions.json').exists()
    assert read(tmp_path/'recursive_track_result.json')['native'] == 4
    assert read(tmp_path/'gates.json')['status'] == 'remaining_evaluations_deferred'
