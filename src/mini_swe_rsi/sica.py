"""Original SICA functions with explicit agent/model/data execution boundary adapters.

Selection, archive statistics and meta-generation function bodies are not rewritten.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

from .core import ROOT, append_event, digest, read, tree_hash, write
from .driver import validate_code


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def load_original():
    vendor = ROOT / 'vendor/sica'
    for file, expected in read(ROOT / 'upstream.lock.json')['sica']['files'].items():
        if digest(vendor / file) != expected:
            raise RuntimeError(f'Pinned SICA source changed: {file}')
    analysis = module('_sica_archive', vendor / 'archive_analysis.py')
    base = module('_sica_benchmark', vendor / 'benchmark_base.py')
    source = ast.parse((vendor / 'runner.py').read_text())
    source.body = [n for n in source.body if not
                   (isinstance(n, ast.ImportFrom) and (n.module or '').startswith('base_agent'))]
    runner = types.ModuleType('_sica_runner')
    runner.__dict__.update(benchmark_registry={}, BaseBenchmark=base.BaseBenchmark,
                 BenchmarkTracker=base.BenchmarkTracker, Problem=base.Problem,
                 ArchiveAnalyzer=analysis.ArchiveAnalyzer, compute_statistics=analysis.compute_statistics,
                 ScoreType=analysis.ScoreType, Message=types.SimpleNamespace,
                 TextContent=types.SimpleNamespace, Model=types.SimpleNamespace(GEMINI_FLASH_2='fixed-model'))
    exec(compile(source, str(vendor / 'runner.py'), 'exec'), runner.__dict__)
    return runner


class SicaAdapter:
    def __init__(self, root, driver, broker, data):
        self.root, self.driver, self.broker, self.data = Path(root), driver, broker, data
        self.runner = load_original()
        self.pending = {}
        self.fixed = False
        self.last_proposal = None
        adapter = self

        class SWE(self.runner.BaseBenchmark):
            name = 'swe_lite_dev'
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self._problems = [adapter.runner.Problem(i, data.rows[i]['problem_statement'], None, None)
                                  for i in data.ids('dev')]
            @property
            def problems(self):
                return self._problems

        async def completion(messages, **kwargs):
            plain = [{'role': m.role, 'content': ''.join(c.text for c in m.content)} for m in messages]
            text = await asyncio.to_thread(broker.summary, plain, 'summary')
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])

        async def process_job(exp_dir, agent_dir, job, worker_id):
            tracker = adapter.runner.benchmark_trackers[job.benchmark_name]
            pid = job.problem.problem_id
            if pid in tracker.results and tracker.results[pid].is_complete():
                return True
            if pid not in tracker.results:
                tracker.start_problem(pid)
            trace = agent_dir / 'benchmarks' / job.benchmark.name / 'traces' / pid
            record = await asyncio.to_thread(driver.task_retry, agent_dir / 'agent_code', pid, trace)
            # Only the public task execution and aggregate outcome enter the search archive.
            attempts = sorted(trace.glob('attempt_*/host_result.json'))
            last = attempts[-1].parent
            for filename in ('answer.txt', 'trace.txt', 'execution_tree.txt', 'activation.json', 'trajectory.json', 'patch.diff'):
                if (last / filename).exists():
                    shutil.copy(last / filename, trace / filename)
            tracker.update_problem(pid, **{k: v for k, v in record.items() if k != 'problem_id'})
            summary = await adapter.runner.generate_contextual_summary(job.problem.statement,
                (trace / 'patch.diff').read_text()[:6000], (trace / 'trace.txt').read_text()[-18000:],
                record['score'], None, 'Official independent grading; private tests and references withheld.')
            (trace / 'summary.txt').write_text(summary)
            (trace / 'score.txt').write_text(str(record['score']))
            await adapter.runner.update_benchmark_progress(exp_dir, job.benchmark.name, pid, 30)
            return True

        original_statistics = self.runner.generate_benchmark_statistics
        async def statistics(agent_dir, benchmark_name):
            tracker = adapter.runner.BenchmarkTracker(agent_dir / 'benchmarks' / benchmark_name / 'results.jsonl')
            if set(tracker.results) != set(data.ids('dev')) or not all(r.is_complete() for r in tracker.results.values()):
                raise RuntimeError('Incomplete dev coverage; refusing archive selection')
            await original_statistics(agent_dir, benchmark_name)

        async def docker_command(*args):
            args = list(map(str, args))
            if args[:2] == ['docker', 'run']:
                name = args[args.index('--name')+1]
                mounts = [args[i+1] for i, a in enumerate(args) if a == '-v']
                paths = {v.rsplit(':', 2)[1]: Path(v.rsplit(':', 2)[0]) for v in mounts}
                adapter.pending[name] = paths
                return True, '', ''
            if args[:2] == ['docker', 'exec']:
                paths = adapter.pending[args[2]]
                archive, target = paths['/home/agent/archive'], paths['/home/agent/workdir']
                cmd = args[3:]
                mod = cmd[cmd.index('-m')+1]
                best = 0 if adapter.fixed else int(mod.split('.')[1].split('_')[1])
                code = archive / f'agent_{best}/agent_code'
                logs = next(v for k, v in paths.items() if 'meta_improvement_logs_' in k)
                iteration = cmd[cmd.index('--current-iter')+1]
                from .core import improvement_contract
                token = broker.session('proposal:' + str(target), **improvement_contract()['limits'])
                raw_before = tree_hash(target)
                before = tree_hash(target, True)
                try:
                    status, output = await asyncio.to_thread(driver.controller, code, logs,
                        ['improve', '--workdir', '/home/agent/workdir', '--best-iter', str(best),
                         '--current-iter', iteration, '--parent-iter', str(adapter.selected_parent),
                         '--candidate-id', target.parent.name], token, archive=archive, target=target)
                finally:
                    metrics = broker.finish(token)
                validate_code(target)
                after = tree_hash(target, True)
                agent_result = read(logs/'result.json') if (logs/'result.json').exists() else {}
                execution_succeeded = status == 0 and agent_result.get('exit_status') == 'Submitted'
                adapter.last_proposal = dict(kind='proposal', proposer=str(code.resolve()),
                    proposer_hash=tree_hash(code), target=str(target.resolve()),
                    before=before, after=after, source_changed=before != after,
                    raw_before=raw_before, raw_after=tree_hash(target), raw_source_changed=raw_before != tree_hash(target),
                    metrics=metrics, process_status=status, execution_succeeded=execution_succeeded,
                    exit_status=agent_result.get('exit_status', 'ProcessError'),
                    activation=read(logs/'activation.json') if (logs/'activation.json').exists() else None)
                proposal = adapter.last_proposal
                proposal['change_status'] = 'changed' if proposal['raw_source_changed'] else 'no_change'
                success = proposal['execution_succeeded'] and proposal['raw_source_changed']
                if not success:
                    summary = ('Improvement execution ' + ('completed' if proposal['execution_succeeded'] else 'failed')
                               + f"; process status={status}; agent exit_status={proposal['exit_status']}. "
                               + ('Source changed; candidate remains eligible for independent benchmark evaluation.'
                                  if proposal['raw_source_changed'] else 'No source change was produced.')
                               + ' No benchmark improvement is established by this execution status.\n')
                    (logs/'summary.txt').write_text(summary)
                append_event(adapter.root, proposal)
                return success, f"{metrics['tokens']}|{metrics['cached_tokens']}|0|{600-max(0, metrics['deadline']-__import__('time').time())}", output[-2000:] if status else ''
            if args[:2] == ['docker', 'buildx']:
                # mini has a frozen runtime dependency contract; source/config changes load next launch.
                return True, 'Fixed dependency image; source changes take effect on next launch.', ''
            if args[:3] == ['docker', 'rm', '-f']:
                adapter.pending.pop(args[3], None)
                return True, '', ''
            raise RuntimeError('Unexpected upstream execution boundary')

        async def ready(*args, **kwargs):
            return None  # container is created synchronously at the actual archived-agent exec boundary

        self.benchmark_type = SWE
        self.runner.benchmark_registry = {SWE.name: SWE}
        self.runner.create_completion = completion
        self.runner.process_job = process_job
        self.runner.generate_benchmark_statistics = statistics
        self.runner.run_docker_command = docker_command
        self.runner.wait_for_container_ready = ready

    async def benchmark(self, exp, agent):
        identity = agent/'code_identity.json'
        frozen = {'code_hash':tree_hash(agent/'agent_code')}
        if identity.exists() and read(identity) != frozen:
            raise RuntimeError('Archived source changed after benchmark began')
        write(identity, frozen)
        for result in (agent/'benchmarks').glob('*/traces/*/attempt_*/host_result.json'):
            if read(result).get('code_hash') != frozen['code_hash']:
                raise RuntimeError('Archive contains task results from another source version')
        # Original queue, tracker, completion/resume semantics and aggregation.
        self.runner.update_metadata(exp, benchmark_progress={})
        await self.runner.run_benchmarks_with_job_queue(exp, agent, [self.benchmark_type], max_workers=1)

    def best(self, exp):
        return self.runner.ArchiveAnalyzer(exp).get_best_agent_iteration()

    def record_diff(self, exp, lineage):
        target = Path(lineage['target'])
        parent = Path(exp)/f"agent_{lineage['parent']}/agent_code"
        result = subprocess.run(['git','diff','--no-index','--no-ext-diff','--no-textconv','--binary','--',
                                 str(parent.resolve()),str(target.resolve())],
                                capture_output=True, timeout=30)
        if result.returncode not in (0,1):
            raise RuntimeError('Unable to record archived Harness diff')
        (target.parent/'harness.diff').write_bytes(result.stdout)
        write(target.parent/'harness_diff.json', {'parent_hash':tree_hash(parent),
              'candidate_hash':tree_hash(target),'bytes':len(result.stdout),
              'diff_sha256':digest(target.parent/'harness.diff'),
              'note':'Source hashes exclude derived __pycache__ files; the binary-capable diff preserves the archived directory comparison.'})

    async def proposal(self, exp_id, iteration, fixed=False):
        exp = Path('results') / f'run_{exp_id}'
        current, following = exp / f'agent_{iteration}', exp / f'agent_{iteration+1}'
        if (following / 'lineage.json').exists():
            lineage = read(following / 'lineage.json')
            self.record_diff(exp, lineage)
            return lineage
        if following.exists():
            # Never retry a partially generated model proposal silently on resume.
            raise RuntimeError('Interrupted proposal: retained for audit; refusing duplicate generation')
        self.fixed, self.last_proposal = fixed, None
        following.mkdir(parents=True)
        aa = self.runner.ArchiveAnalyzer(exp)
        base = int(self.runner.select_base_agent(aa, iteration, score_type='mean_score'))
        self.selected_parent = base
        await self.runner.run_meta_agent_benchmark(exp_id, iteration, exp, current, following)
        if self.last_proposal is None:
            raise RuntimeError('Upstream proposal did not reach archived agent execution')
        lineage = dict(self.last_proposal, parent=base, generation=iteration+1, fixed_proposer=fixed)
        write(following / 'lineage.json', lineage)
        self.record_diff(exp, lineage)
        self.runner.update_metadata(exp, agent_iteration=iteration+1, benchmark_progress={})
        return lineage
