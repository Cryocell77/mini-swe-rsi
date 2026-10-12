"""Task adaptation is independent from the unchanged text model protocol."""
import asyncio
import hashlib
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import ROOT, improvement_contract, write
from mini_swe_rsi.experiment import Experiment
from mini_swe_rsi.broker import TEXT_PROTOCOL


def test_original_improvement_intent_and_distinct_identities():
    render = runpy.run_path(str(ROOT/'runtime/bridge.py'))['improvement_prompt']
    prompt = render(1, 2, 2, 'agent_3', ROOT/'vendor/sica/agent_main.py')
    assert 'carry out meta-improvement reasoning directly as early as reasonably possible' in prompt
    assert 'coming up with a good feature or change to implement' in prompt
    assert 'carefully implementing this making the minimal required changes' in prompt
    assert 'append a new entry describing the changes made in this iteration (2)' in prompt
    assert 'parent=agent_2; proposer=agent_1; candidate=agent_3' in prompt
    assert 'best performing agent in /home/agent/workdir' not in prompt
    assert 'read-only frozen startup snapshot' in prompt
    assert 'current candidate has not been benchmarked' in prompt
    assert '30 model calls, 600 seconds per task, 60 seconds per bash command' in prompt
    assert '2048 output tokens' in prompt and '32768 total context tokens' in prompt
    contract = improvement_contract()
    assert contract['renderer_sha256'] == hashlib.sha256((ROOT/'runtime/bridge.py').read_bytes()).hexdigest()


@pytest.mark.parametrize('contract', [None, {'version': 'old'}, {**improvement_contract(), 'renderer_sha256': 'changed'}])
def test_old_or_changed_task_rejected_before_budget_runtime_or_model(tmp_path, monkeypatch, contract):
    config = {'message_protocol': TEXT_PROTOCOL}
    if contract is not None:
        config['improvement_task'] = contract
    write(tmp_path/'configuration.json', config)
    def forbidden(*a, **kw):
        pytest.fail('Task contract must be rejected before context/runtime/model setup')
    monkeypatch.setattr('mini_swe_rsi.experiment.context_contract', forbidden)
    monkeypatch.setattr('mini_swe_rsi.experiment.Budget', forbidden)
    monkeypatch.setattr('mini_swe_rsi.experiment.Driver', forbidden)
    monkeypatch.setattr('mini_swe_rsi.experiment.Broker', forbidden)
    with pytest.raises(RuntimeError, match='Improvement task contract changed'):
        Experiment(tmp_path)
    assert not (tmp_path/'budget.json').exists()


def test_probes_keep_h0_parent_separate_from_evolved_proposer(tmp_path):
    exp = Experiment.__new__(Experiment)
    exp.root = tmp_path
    write(tmp_path/'champions.json', {'native': 4})
    evidence = tmp_path/'probe_evidence'
    evidence.mkdir()
    for iteration in (0, 4):
        code = evidence/f'agent_{iteration}/agent_code'
        code.mkdir(parents=True)
        (code/'source.py').write_text(f'x={iteration}\n')
    h0 = tmp_path/'h0'
    h0.mkdir()
    (h0/'source.py').write_text('x=0\n')
    exp.code = lambda: h0
    seen = []
    def controller(code, logs, args, token, **kw):
        assert (kw['target']/'source.py').read_text() == 'x=0\n'
        assert args[args.index('--parent-iter')+1] == '0'
        proposer = args[args.index('--best-iter')+1]
        assert code == evidence/f'agent_{proposer}/agent_code'
        candidate = args[args.index('--candidate-id')+1]
        assert candidate.startswith('probe_pair_')
        seen.append((proposer, candidate))
        write(logs/'result.json', {'exit_status':'Submitted'})
        return 0, ''
    exp.driver = SimpleNamespace(controller=controller)
    exp.broker = SimpleNamespace(session=lambda *a: 'fixture', finish=lambda *a: {})
    async def evaluate(*a):
        pass
    exp.evaluate_versions = evaluate
    asyncio.run(exp.probes())
    assert [p for p, c in seen] == ['4', '0', '0', '4']
    assert len({c for p, c in seen}) == 4


def test_benchmark_uses_exact_prompt_file_without_improvement_additions():
    # Inspect the actual boundary branch, without substituting mini's own templates.
    import ast
    tree = ast.parse((ROOT/'runtime/bridge.py').read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    branch = next(n for n in ast.walk(main) if isinstance(n, ast.If) and n.orelse
                  and ast.unparse(n.test) == "args.command == 'improve'")
    assert ast.unparse(branch.orelse[0]) == 'task = Path(args.prompt_file).read_text()'


@pytest.mark.parametrize('mismatch', ['proposer', 'parent'])
def test_declared_identity_is_bound_to_actual_mounted_source_before_launch(tmp_path, monkeypatch, mismatch):
    import shutil
    from mini_swe_rsi.driver import Driver, initial_code
    archive = tmp_path/'archive'
    code = archive/'agent_0/agent_code'
    initial_code(code)
    parent = archive/'agent_1/agent_code'
    shutil.copytree(code, parent)
    (parent/'minisweagent/agents/default.py').write_text(
        (parent/'minisweagent/agents/default.py').read_text()+'\nPARENT_FIXTURE=1\n')
    target = tmp_path/'target'
    shutil.copytree(code, target)
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    # No client/runtime exists: validation must fail before any container launch.
    monkeypatch.setattr('mini_swe_rsi.driver.archive_view', lambda *a: archive)
    args = ['improve', '--best-iter', '1' if mismatch == 'proposer' else '0',
            '--parent-iter', '1' if mismatch == 'parent' else '0',
            '--current-iter', '0', '--candidate-id', 'fixture']
    with pytest.raises(RuntimeError, match='proposer differs|parent differs'):
        driver.controller(code, tmp_path/'logs', args, 'fixture', archive=archive, target=target)


@pytest.mark.parametrize('saved_contract', [improvement_contract(), None])
def test_runtime_renderer_change_before_launch_is_rejected(tmp_path, monkeypatch, saved_contract):
    from mini_swe_rsi.driver import Driver, initial_code
    code = tmp_path/'code'
    initial_code(code)
    config = {} if saved_contract is None else {'improvement_task': saved_contract}
    write(tmp_path/'configuration.json', config)
    driver = Driver.__new__(Driver)
    driver.root = tmp_path
    changed = {**improvement_contract(), 'renderer_sha256': 'changed-after-initialization'}
    monkeypatch.setattr('mini_swe_rsi.driver.improvement_contract', lambda: changed)
    def forbidden(*args, **kwargs):
        pytest.fail('Frozen contract must be checked before loading the renderer')
    monkeypatch.setattr('mini_swe_rsi.driver.runpy.run_path', forbidden)
    args = ['improve', '--best-iter', '0', '--parent-iter', '0',
            '--current-iter', '0', '--candidate-id', 'agent_1']
    # No client, broker or image exists: no container or model can be started.
    with pytest.raises(RuntimeError, match='Frozen improvement task contract changed before launch'):
        driver.controller(code, tmp_path/'logs', args, 'fixture', target=code)
