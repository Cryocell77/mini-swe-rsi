import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

from .core import write


def main():
    parser = argparse.ArgumentParser(description='Pinned online mini-swe-agent RSI runs')
    parser.add_argument('command', choices=['online', 'report'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--resume', action='store_true',
                        help='Resume the persisted online task/round cursor')
    parser.add_argument('--resume-checkpoint', type=Path,
                        help='Explicit host-private authorization for pre-check online workflow restoration')
    parser.add_argument('--billing-mode', choices=['subscription'],
                        help='Freeze the authenticated subscription billing contract for online mode')
    parser.add_argument('--task-limit', type=int,
                        help='Freeze an explicit online development-prefix length (for example 3 or 20)')
    args = parser.parse_args()
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
            print(json.dumps({k: result.get(k) for k in ('test_sealed','total_model_calls','total_tokens','failure')}, ensure_ascii=False))
            return
        try:
            previous_failure = root/'failure.json'
            if previous_failure.exists():
                from .core import append_event, read
                append_event(root, {'kind':'recovery_started', 'previous_failure':read(previous_failure)})
                history = root/'failure_history'
                history.mkdir(exist_ok=True)
                previous_failure.rename(history/f'{time.time_ns()}.json')
            if args.command == 'online':
                from .online import OnlineLoop
                online_kwargs = {key:value for key, value in (
                    ('billing_mode', args.billing_mode), ('task_limit', args.task_limit),
                    ('resume_checkpoint', args.resume_checkpoint))
                    if value is not None}
                loop = OnlineLoop.from_environment(root, args.data, **online_kwargs)
                try:
                    result = loop.run()
                    print(json.dumps(result, ensure_ascii=False))
                finally:
                    loop.close()
                return
        except Exception as e:
            write(root/'failure.json', {'type': type(e).__name__, 'reason': str(e)[:500],
                                       'time': time.time(), 'command': args.command})
            raise
        finally:
            if args.command == 'online':
                from .report import build_online_report
                active_error = sys.exception()
                try:
                    build_online_report(root)
                except BaseException:
                    # A startup/run failure can occur before the online
                    # contract is written; preserve that original error.
                    if active_error is None:
                        raise


if __name__ == '__main__':
    main()
