import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

from .core import write


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == 'single':
        from .single import run_single
        if args.check_timeout <= 0:
            parser.error('--check-timeout must be positive')
        if not args.check.strip():
            parser.error('--check must not be empty')
        run_single(args)
        return
    if args.command == 'prepare':
        from .core import ROOT, command
        import os
        command_args = [ROOT/'.eval-venv/bin/python', '-m', 'mini_swe_rsi.evaluation.prepare',
                        '--dataset', args.dataset, '--data', args.data, '--split', args.split or 'test']
        if args.revision:
            command_args.extend(['--revision', args.revision])
        print(command(command_args, timeout=None,
                      env={**os.environ, 'PYTHONPATH': str(ROOT/'src')}), end='')
        return
    root = args.run.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('This experiment already has an active process')
        if args.command == 'report':
            from .report import build_online_report
            result = build_online_report(root)
            print(json.dumps({k: result.get(k) for k in ('total_model_calls','total_tokens','failure')}, ensure_ascii=False))
            return
        try:
            previous_failure = root/'failure.json'
            if previous_failure.exists():
                from .core import append_event, read
                append_event(root, {'kind':'recovery_started', 'previous_failure':read(previous_failure)})
                history = root/'failure_history'
                history.mkdir(exist_ok=True)
                previous_failure.rename(history/f'{time.time_ns()}.json')
            from .online import OnlineLoop
            online_kwargs = {key:value for key, value in (
                ('task_limit', args.task_limit),
                ('split', args.split),
                ('resume_checkpoint', args.resume_checkpoint))
                if value is not None}
            loop = OnlineLoop.from_environment(root, args.data, **online_kwargs)
            try:
                result = loop.run()
                print(json.dumps(result, ensure_ascii=False))
            finally:
                loop.close()
        except Exception as e:
            write(root/'failure.json', {'type': type(e).__name__, 'reason': str(e)[:500],
                                       'time': time.time(), 'command': args.command})
            raise
        finally:
            from .report import build_online_report
            active_error = sys.exception()
            try:
                build_online_report(root)
            except BaseException:
                # A startup/run failure can occur before the online
                # contract is written; preserve that original error.
                if active_error is None:
                    raise


def build_parser():
    parser = argparse.ArgumentParser(
        description='Online mini-swe-agent RSI runs and SWE-bench data preparation', allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    from .core import ROOT
    single = commands.add_parser('single', help='Solve a local coding task and run RSI', allow_abbrev=False)
    single.add_argument('--workspace', type=Path, required=True, help='Source directory; copied into an isolated container')
    single.add_argument('--check', required=True, help='Independent verification shell command, run in /testbed')
    single.add_argument('--verify-file', type=Path, help='Private verifier mounted only for grading as /verification/verify')
    single.add_argument('--check-timeout', type=int, default=300)
    single.add_argument('--image', default='mini-swe-rsi-runtime:0.1', help='Local Linux image with project dependencies installed')
    single.add_argument('--store', type=Path, default=ROOT/'runs/single-input', help='Persistent single-input promotion store')
    single.add_argument('--run', type=Path, help='New execution directory; defaults to a unique directory inside the store')
    single.add_argument('--prompt', help='Task text; omit to enter interactively')
    single.add_argument('--version', help='original or a saved Vxxxxxx; omit for interactive selection')
    online = commands.add_parser('online', help='Run or continue online RSI', allow_abbrev=False)
    online.add_argument('--run', type=Path, required=True)
    online.add_argument('--data', type=Path, required=True,
                        help='Prepared dataset directory; required for new and resumed runs')
    online.add_argument('--split', help='Dataset split; defaults to the saved or manifest selection')
    online.add_argument('--resume-checkpoint', type=Path,
                        help='Host-private authorization for unfinished workflow restoration')
    online.add_argument('--task-limit', type=int,
                        help='Run the first N tasks of the selected split; omit for the complete split')
    report = commands.add_parser('report', help='Generate an offline report', allow_abbrev=False)
    report.add_argument('--run', type=Path, required=True)
    prepare = commands.add_parser('prepare', help='Prepare a SWE-bench dataset', allow_abbrev=False)
    prepare.add_argument('--dataset', required=True, help='Hugging Face dataset id or local JSON/JSONL file')
    prepare.add_argument('--data', type=Path, required=True, help='New output dataset directory')
    prepare.add_argument('--split', default='test')
    prepare.add_argument('--revision', help='Hugging Face dataset revision')
    return parser


if __name__ == '__main__':
    main()
