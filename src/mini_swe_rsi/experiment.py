from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .broker import Broker, TEXT_PROTOCOL
from .core import ROOT, Budget, Data, append_event, digest, read, tree_hash, write
from .driver import Driver, initial_code
from .sica import SicaAdapter


@contextlib.contextmanager
def working(path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def records(path):
    p = Path(path)
    if not p.exists():
        return {}
    return {r['problem_id']: r for r in map(json.loads, p.read_text().splitlines())}


def save_records(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(''.join(json.dumps(r) + '\n' for r in data.values()))
    temp.replace(path)


class Experiment:
    def __init__(self, root, data_path=None, phase=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root/'configuration.json').exists() and read(self.root/'configuration.json').get('message_protocol') != TEXT_PROTOCOL:
            raise RuntimeError('Message protocol changed; start a separate experiment')
        self.budget = Budget(self.root)
        if phase is None:
            phase = ('search' if (self.root/'baseline.json').exists() else
                     'baseline' if (self.root/'preflight.json').exists() else 'preflight')
        self.budget.enter(phase)
        self.data = Data(data_path) if data_path else Data()
        self.data.freeze_images(self.root)
        self.broker = Broker(self.root, self.budget)
        self.driver = None
        try:
            identity = self.broker.identity()
            if (self.root / 'model_identity.json').exists():
                if read(self.root / 'model_identity.json') != identity:
                    raise RuntimeError('Model identity changed across experiment/restart')
            else:
                write(self.root / 'model_identity.json', identity)
            self.driver = Driver(self.root, self.data, self.broker, self.budget)
            self.adapter = SicaAdapter(self.root, self.driver, self.broker, self.data)
            for i in (1, 2):
                exp = self.root / f'results/run_{i}'
                exp.mkdir(parents=True, exist_ok=True)
                initial_code(exp / 'agent_0/agent_code')
            if (self.root/'baseline.json').exists():
                expected = read(self.root/'baseline.json')['code_hash']
                if any(tree_hash(self.code(run)) != expected for run in (1, 2)):
                    raise RuntimeError('Frozen H0 source changed')
            configuration = {'message_protocol': TEXT_PROTOCOL, 'upstream': read(ROOT / 'upstream.lock.json'),
                  'data_manifest': str(self.data.path/'manifest.json'),
                  'model': identity, 'limits': {'calls': 30, 'task_seconds': 600,
                   'command_seconds': 60, 'max_tokens': 2048, 'context_characters': 60000},
                  'runtime_image': self.driver.image.id,
                  'native_run': 1, 'fixed_proposer_run': 2}
            if (self.root/'configuration.json').exists():
                if read(self.root/'configuration.json') != configuration:
                    raise RuntimeError('Frozen experiment configuration changed on resume')
            else:
                write(self.root/'configuration.json', configuration)
            source_hash = tree_hash(ROOT/'src')
            snapshot_manifest = {name:tree_hash(ROOT/name) for name in ('src','runtime','scripts','vendor')}
            snapshot_manifest.update({name:digest(ROOT/name) for name in
                                     ('pyproject.toml','uv.lock','evaluator.lock.txt','upstream.lock.json')})
            snapshot_id = hashlib.sha256(json.dumps(snapshot_manifest,sort_keys=True).encode()).hexdigest()
            snapshot = self.root/'private/implementation_snapshots'/snapshot_id
            if not (snapshot/'manifest.json').exists():
                if snapshot.exists():
                    shutil.rmtree(snapshot)
                for name in ('src', 'runtime', 'scripts', 'vendor'):
                    shutil.copytree(ROOT/name, snapshot/name,
                                    ignore=shutil.ignore_patterns('__pycache__'))
                for name in ('pyproject.toml', 'uv.lock', 'evaluator.lock.txt', 'upstream.lock.json'):
                    shutil.copy(ROOT/name, snapshot/name)
                write(snapshot/'manifest.json', snapshot_manifest)
            append_event(self.root, {'kind': 'implementation_snapshot',
                'project_hash': source_hash, 'bridge_hash': tree_hash(ROOT/'runtime'),
                'dependency_lock': digest(ROOT/'uv.lock'), 'phase': self.budget.phase,
                'source_snapshot':str(snapshot.relative_to(self.root))})
        except Exception:
            self.close()
            raise

    def close(self):
        if self.driver:
            self.driver.close()
        if hasattr(self, 'broker'):
            self.broker.close()

    def code(self, run=1, iteration=0):
        return self.root / f'results/run_{run}/agent_{iteration}/agent_code'

    def assert_model(self):
        if self.broker.identity() != read(self.root / 'model_identity.json'):
            raise RuntimeError('Model identity changed during experiment')

    async def preflight(self):
        if (self.root/'preflight.json').exists():
            return read(self.root/'preflight.json')
        self.budget.enter('preflight')
        # Use only development tasks, and retain the observations as genuine H0 baseline attempts.
        timings = []
        for pid in self.data.ids('dev')[:3]:
            out = self.root / 'preflight' / pid
            prior = list(out.glob('attempt_*/host_result.json'))
            if prior:
                r = read(prior[-1])['record']
            else:
                r = await asyncio.to_thread(self.driver.task_retry, self.code(), pid, out)
            attempts = sorted(out.glob('attempt_*/host_result.json'))
            timings.append(read(attempts[-1]).get('total_seconds', r['wall_time']))
        self.assert_model()
        result = {'complete': True, 'ids': self.data.ids('dev')[:3],
                  'mean_task_seconds': sum(timings)/len(timings),
                  'estimated_final_seconds': 400*sum(timings)/len(timings),
                  'final_budget_seconds': 9*3600,
                  'prediction_is_uncertain': True}
        write(self.root/'preflight.json', result)
        return result

    async def baseline(self):
        if (self.root/'baseline.json').exists():
            expected = read(self.root/'baseline.json')['code_hash']
            if any(tree_hash(self.code(run)) != expected for run in (1, 2)):
                raise RuntimeError('Frozen H0 changed; refusing to relabel baseline results')
            self.assert_model()
            return
        await self.preflight()
        self.budget.enter('baseline')
        exp = self.root/'results/run_1'
        agent = exp/'agent_0'
        tracker_path = agent/'benchmarks/swe_lite_dev/results.jsonl'
        tracker_path.parent.mkdir(parents=True, exist_ok=True)
        completed = records(tracker_path)
        for pid in self.data.ids('dev')[:3]:
            if pid not in completed:
                source = self.root/'preflight'/pid
                r = read(sorted(source.glob('attempt_*/host_result.json'))[-1])['record']
                target = agent/'benchmarks/swe_lite_dev/traces'/pid
                shutil.copytree(source, target, dirs_exist_ok=True)
                attempt = sorted(target.glob('attempt_*/host_result.json'))[-1].parent
                for name in ('trace.txt', 'execution_tree.txt', 'patch.diff', 'answer.txt', 'activation.json'):
                    shutil.copy(attempt/name, target/name)
                # Summaries are generated with the same fixed model, including reused preflight attempts.
                if not (target/'summary.txt').exists():
                    summary = await self.adapter.runner.generate_contextual_summary(
                        self.data.rows[pid]['problem_statement'], (target/'patch.diff').read_text()[:6000],
                        (target/'trace.txt').read_text()[-18000:], r['score'], None, 'Private tests withheld.')
                    (target/'summary.txt').write_text(summary)
                completed[pid] = r
        save_records(tracker_path, completed)
        with working(self.root):
            await self.adapter.benchmark(Path('results/run_1'), Path('results/run_1/agent_0'))
        # Both arms begin with exactly the same initial archive and observed evidence.
        fixed = self.root/'results/run_2/agent_0'
        if not (fixed/'benchmarks/swe_lite_dev/perf.json').exists():
            shutil.copytree(agent/'benchmarks', fixed/'benchmarks', dirs_exist_ok=True)
        self.assert_model()
        write(self.root/'baseline.json', {'complete': True, 'coverage': 30,
              'perf': read(agent/'benchmarks/swe_lite_dev/perf.json'),
              'code_hash': tree_hash(self.code())})

    async def evolve(self):
        if not (self.root/'baseline.json').exists():
            await self.baseline()
        self.budget.enter('search')
        with working(self.root):
            for generation in range(4):
                for run in ((1, 2) if generation % 2 == 0 else (2, 1)):
                    self.budget.check()
                    await self.adapter.proposal(run, generation, fixed=run == 2)
                    if run == 1 and self.recursion_unreachable(generation+1):
                        self.assert_model()
                        mechanism = self.mechanism()
                        write(self.root/'mechanism.json', mechanism)
                        write(self.root/'gates.json', {'passed':False, 'mechanism':mechanism,
                            'task_benefit':{'status':'not_run'}, 'improver_benefit':{'status':'not_run'},
                            'reason':'Recursive mechanism threshold is unreachable within four proposals; no simplification or held-out evaluation.'})
                        return
                    await self.adapter.benchmark(Path(f'results/run_{run}'), Path(f'results/run_{run}/agent_{generation+1}'))
            best = {str(run): self.adapter.best(Path(f'results/run_{run}')) for run in (1, 2)}
        write(self.root/'champions.json', {'native': best['1'], 'fixed': best['2'],
                  'native_hash': tree_hash(self.code(1, best['1'])),
                  'fixed_hash': tree_hash(self.code(2, best['2']))})
        self.assert_model()
        mechanism = self.mechanism()
        write(self.root/'mechanism.json', mechanism)
        if not mechanism['passed']:
            write(self.root/'gates.json', {'passed': False, 'mechanism': mechanism,
                      'task_benefit': {'status': 'not_run'}, 'improver_benefit': {'status': 'not_run'},
                      'reason': 'No verified recursive behavioral chain; simplification stopped.'})
            return
        await self.probes()
        await self.validate()

    def recursion_unreachable(self, completed):
        """A raw-identical candidate cannot supply a behavior-changing generation."""
        lineage = [read(p) for p in sorted((self.root/'results/run_1').glob('agent_*/lineage.json'))]
        lineage = [record for record in lineage if record['generation'] <= completed]
        if len(lineage) != completed:
            raise RuntimeError('Native proposal count does not match persisted lineage')
        changes = []
        for record in lineage:
            parent_hash = tree_hash(self.code(1, record['parent']))
            candidate_hash = tree_hash(Path(record['target']))
            changes.append({'generation':record['generation'],'parent_hash':parent_hash,
                            'candidate_hash':candidate_hash,'raw_changed':parent_hash != candidate_hash})
        possible = sum(c['raw_changed'] for c in changes) + 4-completed
        if possible >= 2:
            return False
        write(self.root/'futility.json', {'proposal_limit':4,'completed_native_proposals':completed,
              'required_behavior_changes':2,'maximum_possible_behavior_changes':possible,
              'code_comparisons':changes,
              'reason':'Even treating every raw source change and remaining proposal as a behavior change, two generations cannot be reached.'})
        return True

    def mechanism(self):
        if ((self.root/'baseline.json').exists() and
            tree_hash(self.code()) != read(self.root/'baseline.json')['code_hash']):
            raise RuntimeError('Initial agent source changed; counterfactual replay is not trusted')
        lineage = [read(p) for p in sorted((self.root/'results/run_1').glob('agent_*/lineage.json'))]
        changed = [r for r in lineage if r['source_changed']]
        events = []
        if (self.root/'events.jsonl').exists():
            events = [json.loads(line) for line in (self.root/'events.jsonl').read_text().splitlines()]
        def archived_proposer_executed(r):
            activation = r.get('activation') or {}
            iteration = Path(r['proposer']).parent.name
            prefix = f'/home/agent/archive/{iteration}/agent_code/'
            return (all(activation.get(k, '').startswith(prefix) for k in ('agent_file', 'config_file'))
                    and any(e.get('kind') == 'agent_launch' and e.get('command') == 'improve'
                            and e.get('code_hash') == r['proposer_hash']
                            and e.get('code') == r['proposer'] for e in events))
        # Each change must alter its selected parent's behavior, not merely H0's.
        witnesses, replay_proofs = [], []
        for r in changed:
            candidate = Path(r['target']).parent
            parent_code = self.code(1, r['parent'])
            for pid in self.data.ids('dev'):
                trace = candidate/'benchmarks/swe_lite_dev/traces'/pid
                attempts = sorted(trace.glob('attempt_*/host_result.json'))
                if not attempts:
                    continue
                attempt = attempts[-1].parent
                activation = read(attempt/'activation.json') if (attempt/'activation.json').exists() else {}
                if 'platform' not in activation:
                    continue
                host = read(attempt/'host_result.json')
                label = host['metrics']['label']
                observed = [e for e in events if e.get('label') == label
                            and e.get('kind') in ('model_call', 'tool_call')]
                if not observed:
                    continue
                folder = self.root/'private/parent_behavior_replay'/candidate.name/pid
                job = {'task': self.data.rows[pid]['problem_statement'],
                       'model': read(self.root/'model_identity.json')['model'],
                       'candidate_exit_status': host['result'].get('exit_status'),
                       'platform': activation['platform'], 'events': observed}
                proofs = {}
                for role, code in [('self', Path(r['target'])), ('parent', parent_code)]:
                    result_file = folder/role/'proof.json'
                    source_hash = tree_hash(code)
                    if result_file.exists() and read(result_file).get('source_hash') == source_hash:
                        proof = read(result_file)
                    else:
                        proof = {**self.driver.replay(code, job, folder/role), 'source_hash':source_hash}
                        write(result_file, proof)
                    proofs[role] = proof
                    if role == 'self' and (proof.get('changed') or not proof.get('complete_prefix')):
                        break  # Dynamic/unreplayable traces cannot establish behavior changes.
                if ('parent' in proofs and proofs['parent'].get('changed')
                    and not proofs['self'].get('changed') and proofs['self'].get('complete_prefix')):
                    witnesses.append(r)
                    replay_proofs.append({'generation':r['generation'], 'parent':r['parent'], 'task':pid,
                        'proof_directory':str(folder.relative_to(self.root)), **proofs})
                    break
        continued = [r for r in lineage if '/agent_0/' not in r['proposer'] and archived_proposer_executed(r)
                     and any(r['proposer_hash'] == tree_hash(Path(c['target'])) for c in witnesses)]
        return {'passed': len(witnesses) >= 2 and bool(continued),
                'source_changed_generations': [r['generation'] for r in changed],
                'observed_behavior_generations': [r['generation'] for r in witnesses],
                'evolved_proposer_executions': len(continued),
                'archived_noninitial_proposer_executions': sum('/agent_0/' not in r['proposer']
                     and archived_proposer_executed(r) for r in lineage),
                'counterfactual_replay_witnesses': replay_proofs,
                'criterion': 'Two parent-relative behavioral changes, calibrated by self replay, with actual execution of a behavior-changed archived proposer; replay is network-isolated.'}

    async def probes(self):
        champion = read(self.root/'champions.json')['native']
        evidence = self.root/'probe_evidence'
        if not evidence.exists():
            from .archive_view import archive_view
            archive_view(self.root/'results/run_1', evidence)
        for pair in range(2):
            for role in (('evolved', 'fixed') if pair == 0 else ('fixed', 'evolved')):
                path = self.root/f'probes/pair_{pair}/{role}'
                if (path/'lineage.json').exists():
                    if tree_hash(path/'agent_code') != read(path/'lineage.json')['candidate_hash']:
                        raise RuntimeError('Frozen probe source changed')
                    await self.evaluate_versions({'candidate':path/'agent_code'}, 'dev', path/'dev')
                    continue
                if path.exists():
                    raise RuntimeError('Interrupted probe retained; refusing an unbudgeted repeat')
                path.mkdir(parents=True)
                target = path/'agent_code'
                shutil.copytree(self.code(), target)
                token = self.broker.session(f'probe:{pair}:{role}')
                proposer = evidence/f'agent_{champion if role == "evolved" else 0}/agent_code'
                try:
                    status, _ = await asyncio.to_thread(self.driver.controller, proposer, path/'logs',
                        ['improve', '--workdir', '/home/agent/workdir', '--best-iter', str(champion if role == 'evolved' else 0),
                         '--current-iter', str(pair)], token, archive=self.root/'probe_evidence', target=target)
                finally:
                    metrics = self.broker.finish(token)
                write(path/'lineage.json', {'proposer_hash': tree_hash(proposer), 'starting_hash': tree_hash(self.code()),
                     'candidate_hash': tree_hash(target), 'process_status': status, 'metrics': metrics,
                     'exit_status': read(path/'logs/result.json').get('exit_status') if (path/'logs/result.json').exists() else 'ProcessError'})
                await self.evaluate_versions({'candidate': target}, 'dev', path/'dev')

    async def evaluate_versions(self, versions, split, output):
        output = Path(output)
        frozen = {name:tree_hash(code) for name, code in versions.items()}
        identity = output/'code_versions.json'
        if identity.exists() and read(identity) != frozen:
            raise RuntimeError('Evaluation source changed; refusing mixed-version results')
        write(identity, frozen)
        data = {name: records(output/name/'results.jsonl') for name in versions}
        for pid in self.data.ids(split):
            for name, code in versions.items():
                self.budget.check()
                if pid in data[name]:
                    continue
                trace = output/name/'traces'/pid
                r = await asyncio.to_thread(self.driver.task_retry, code, pid, trace)
                measured = read(sorted(trace.glob('attempt_*/host_result.json'))[-1])['metrics']
                r = {**r, 'token_accounting_complete': measured.get('unmetered_calls', 0) == 0}
                data[name][pid] = r
                save_records(output/name/'results.jsonl', data[name])
        return data

    async def validate(self):
        if not (self.root/'mechanism.json').exists() or not read(self.root/'mechanism.json')['passed']:
            raise RuntimeError('No verified recursive mechanism; held-out validation is not opened')
        self.budget.enter('validation')
        best = read(self.root/'champions.json')
        native = self.code(1, best['native'])
        if tree_hash(native) != best['native_hash'] or tree_hash(self.code()) != read(self.root/'baseline.json')['code_hash']:
            raise RuntimeError('Frozen source changed before validation')
        for repeat in range(2):
            versions = {'H0': self.code(), 'reference': native} if repeat == 0 else {'reference': native, 'H0': self.code()}
            await self.evaluate_versions(versions, 'val',
                                         self.root/f'private/validation/repeat_{repeat}')
        for pair in range(2):
            for role in ('evolved', 'fixed'):
                candidate = self.root/f'probes/pair_{pair}/{role}'
                if tree_hash(candidate/'agent_code') != read(candidate/'lineage.json')['candidate_hash']:
                    raise RuntimeError('Frozen probe changed before validation')
                if set(records(candidate/'dev/candidate/results.jsonl')) != set(self.data.ids('dev')):
                    raise RuntimeError('Probe development evaluation is incomplete; validation stays sealed')
            await self.evaluate_versions({role: self.root/f'probes/pair_{pair}/{role}/agent_code'
                                          for role in ('evolved', 'fixed')}, 'val', self.root/f'private/probe_validation/pair_{pair}')
        from .report import compute_gates
        write(self.root/'gates.json', compute_gates(self.root, self.data.ids('val')))
        self.assert_model()

    async def final(self, minimal):
        if not read(self.root/'gates.json')['passed']:
            raise RuntimeError('Simplification gates have not passed; test100 remains sealed')
        replay = read(self.root/'simplification_replay.json')
        if not replay.get('passed') or replay.get('minimal_hash') != tree_hash(minimal):
            raise RuntimeError('Missing differential replay evidence for this minimal version')
        best = read(self.root/'champions.json')
        versions = {'H0': self.code(), 'reference': self.code(1, best['native']),
                    'minimal': Path(minimal).resolve(), 'fixed': self.code(2, best['fixed'])}
        if (tree_hash(versions['H0']) != read(self.root/'baseline.json')['code_hash'] or
            tree_hash(versions['reference']) != best['native_hash'] or tree_hash(versions['fixed']) != best['fixed_hash']):
            raise RuntimeError('Frozen champion source changed before final evaluation')
        seal = {k: tree_hash(v) for k, v in versions.items()}
        if (self.root/'test_seal.json').exists() and read(self.root/'test_seal.json') != seal:
            raise RuntimeError('Frozen final versions changed; refusing repeated test selection')
        write(self.root/'test_seal.json', seal)
        self.budget.enter('final')
        await self.evaluate_versions(versions, 'test', self.root/'private/final')
        self.assert_model()
