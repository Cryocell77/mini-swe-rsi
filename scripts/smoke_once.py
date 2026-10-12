"""One production proposal; no baseline rerun, no validation/test evaluation."""
import argparse
import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import mini_swe_rsi.experiment as experiment_module
from mini_swe_rsi.core import ROOT, Budget, read, tree_hash, write
from mini_swe_rsi.experiment import Experiment, working


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument('--new-time-budget-seconds', type=int,
                        help='Explicitly authorized new diagnostic time allocation; never alters the old experiment')
    timing.add_argument('--unbounded-diagnostic', action='store_true',
                        help='Independent single diagnostic without additional total/stage time limits; SICA task/iteration limits remain')
    args = parser.parse_args()
    original = read(ROOT/'runs/sica-qwen-20261011/budget.json')['started']
    started = time.time()
    if not args.unbounded_diagnostic and args.new_time_budget_seconds is None and started >= original+86400:
        parser.error('Original 24-hour deadline expired; a new time allocation requires user authorization')
    if args.new_time_budget_seconds is not None and not 0 < args.new_time_budget_seconds <= 86400:
        parser.error('New time allocation must be in 1..86400 seconds')
    out = args.output.resolve()
    if out.exists():
        parser.error('Refusing to repeat or overwrite a proposal in an existing directory')
    out.mkdir(parents=True)
    deadline = (None if args.unbounded_diagnostic else
                original+86400 if args.new_time_budget_seconds is None else started+args.new_time_budget_seconds)
    write(out/'diagnostic_limits.json', {'original_24h_started':original, 'original_24h_deadline':original+86400,
        'new_time_budget_seconds':args.new_time_budget_seconds, 'diagnostic_deadline':deadline,
        'unbounded_diagnostic':args.unbounded_diagnostic,
        'one_proposal':True, 'candidate_dev_task':'django__django-14752', 'baseline_rerun':False,
        'val_test_run':False})
    write(out/'budget.json', {'started':started if args.unbounded_diagnostic or args.new_time_budget_seconds is not None else original,
                             'phases':{}, 'unbounded_diagnostic':args.unbounded_diagnostic})
    write(out/'code_revision.json', {'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                                   'src_sha256':tree_hash(ROOT/'src'), 'runtime_sha256':tree_hash(ROOT/'runtime')})

    class SingleCheckBudget(Budget):
        def remaining(self):
            if args.unbounded_diagnostic:
                return float('inf')
            return min(super().remaining(), deadline-time.time())

    original_budget_class = experiment_module.Budget
    experiment_module.Budget = SingleCheckBudget
    exp = None
    try:
        exp = Experiment(out, phase='preflight')
        history = ROOT/'runs/task-protocol-repair-20261012/real-smoke/results/run_1/agent_0/meta_improvement_logs_archive_view/agent_0'
        if tree_hash(history/'agent_code/minisweagent') != tree_hash(exp.code()/'minisweagent'):
            raise RuntimeError('Historical H0 mini source differs')
        import json
        rows = [json.loads(line) for line in (history/'benchmarks/swe_lite_dev/results.jsonl').read_text().splitlines()]
        if {r['problem_id'] for r in rows} != set(exp.data.ids('dev')):
            raise RuntimeError('Historical public dev coverage is incomplete')
        shutil.copytree(history/'benchmarks', exp.code().parent/'benchmarks')
        write(out/'historical_evidence.json', {'source':str(history), 'dev_tasks':len(rows),
            'kind':'old-model public analysis evidence only; NOT a baseline for the new model',
            'h0_mini_sha256':tree_hash(exp.code()/'minisweagent')})
        with working(out):
            proposal = asyncio.run(exp.adapter.proposal(1,0))
        write(out/'proposal_result.json',proposal)
        code = Path(proposal['target'])
        parent = out/f"results/run_1/agent_{proposal['parent']}/agent_code"
        changed = (tree_hash(code/'minisweagent',True) != tree_hash(parent/'minisweagent',True)
                   or any((code/name).read_bytes() != (parent/name).read_bytes() for name in ('__main__.py','__init__.py')))
        write(out/'runnable_source_check.json', {'runnable_changed':changed,'raw_source_changed':proposal['raw_source_changed']})
        result = (exp.driver.task_retry(code,'django__django-14752',out/'candidate-dev/django__django-14752')
                  if changed else {'skipped':True,'reason':'No runnable source change; avoiding repeated H0 solving'})
        write(out/'candidate_dev_result.json',result)
        exp.assert_model()
        write(out/'model_identity_end.json',read(out/'model_identity.json'))
        write(out/'smoke_result.json', {'completed':True,'elapsed_seconds':time.time()-started,
            'source_changed':proposal['raw_source_changed'],'execution_succeeded':proposal['execution_succeeded'],
            'exit_status':proposal['exit_status'],'baseline_rerun':False,'val_test_run':False,
            'rsi_benefit_established':False})
    except BaseException as exc:
        write(out/'smoke_failure.json', {'type':type(exc).__name__,'elapsed_seconds':time.time()-started,
            'note':'No automatic repeat proposal; exception retained by caller process log'})
        raise
    finally:
        if exp is not None:
            exp.close()
        experiment_module.Budget = original_budget_class


if __name__ == '__main__':
    main()
