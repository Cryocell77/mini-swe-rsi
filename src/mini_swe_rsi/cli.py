import argparse
import asyncio
import fcntl
import json
import time
from pathlib import Path

from .core import write
from .experiment import Experiment
from .report import build_report


def main():
    parser = argparse.ArgumentParser(description='Pinned SICA + mini-swe-agent fixed-model experiments')
    parser.add_argument('command', choices=['baseline', 'evolve', 'evaluate', 'report'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--final', action='store_true')
    parser.add_argument('--minimal', type=Path)
    args = parser.parse_args()
    root = args.run.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('This experiment already has an active process')
        if args.command == 'report':
            result = build_report(root)
            print(json.dumps({k: result.get(k) for k in ('test_sealed','total_model_calls','total_tokens','failure')}, ensure_ascii=False))
            return
        experiment = None
        try:
            previous_failure = root/'failure.json'
            if previous_failure.exists():
                from .core import append_event, read
                append_event(root, {'kind':'recovery_started', 'previous_failure':read(previous_failure)})
                history = root/'failure_history'
                history.mkdir(exist_ok=True)
                previous_failure.rename(history/f'{time.time_ns()}.json')
            phase = ('final' if args.final else 'validation') if args.command == 'evaluate' else None
            experiment = Experiment(root, args.data, phase=phase)
            if args.command == 'baseline':
                asyncio.run(experiment.preflight() if args.preflight_only else experiment.baseline())
            elif args.command == 'evolve':
                asyncio.run(experiment.evolve())
            elif args.final:
                if not args.minimal:
                    parser.error('--final requires --minimal and passed gates/replay')
                asyncio.run(experiment.final(args.minimal))
            else:
                asyncio.run(experiment.validate())
            if previous_failure.exists():
                previous_failure.unlink()
        except Exception as e:
            write(root/'failure.json', {'type': type(e).__name__, 'reason': str(e)[:500],
                                       'time': time.time(), 'command': args.command})
            raise
        finally:
            if experiment:
                experiment.close()
            build_report(root)


if __name__ == '__main__':
    main()
