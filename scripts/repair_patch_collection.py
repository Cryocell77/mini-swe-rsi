"""One-time, audited recovery for the initial image-preparation diff bug.

No task model calls: replay the previously recorded patch into its frozen image,
collect relative to that image's initial HEAD, then independently score it.
The first answer is retained; a spurious later model attempt is excluded.
"""
import argparse
import io
import json
import re
import tarfile
import time
from pathlib import Path

from mini_swe_rsi.core import append_event, read, tree_hash, write
from mini_swe_rsi.experiment import Experiment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    if (args.run/'patch_collection_repair.json').exists():
        print('Initial patch collection repair already complete; no changes made.')
        return
    exp = Experiment(args.run, phase='baseline')
    root = exp.root
    try:
        events = [json.loads(s) for s in (root/'events.jsonl').read_text().splitlines()]
        for pid in exp.data.ids('dev'):
            source = root/'preflight'/pid if (root/'preflight'/pid).exists() else root/'results/run_1/agent_0/benchmarks/swe_lite_dev/traces'/pid
            paths = sorted(source.glob('attempt_*'))
            eligible = [p for p in paths if (p/'patch.diff').exists() and not (p/'interrupted.json').exists()]
            if not eligible:
                continue
            attempt = eligible[0]
            if (attempt/'patch_repair.json').exists():
                continue
            raw = attempt/'patch.raw_base_commit.diff'
            if not raw.exists():
                raw.write_bytes((attempt/'patch.diff').read_bytes())
            image = exp.driver.frozen_image(pid)
            c = exp.driver.client.containers.run(image.id, ['sleep','infinity'], detach=True,
                platform='linux/amd64', network_disabled=True, cap_drop=['ALL'],
                security_opt=['no-new-privileges'], mem_limit='2g', nano_cpus=1000000000, pids_limit=256)
            try:
                def execute(command):
                    out = c.exec_run(['timeout','60','bash','-lc',command], workdir='/testbed')
                    if out.exit_code:
                        raise RuntimeError('Patch recovery command failed: '+out.output.decode(errors='replace')[-1000:])
                    return out.output.decode(errors='replace')
                base = execute('git rev-parse HEAD').strip()
                assert re.fullmatch('[0-9a-f]{40}', base)
                delta = execute('git diff --numstat '+exp.data.rows[pid]['base_commit'])
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode='w') as archive:
                    info = tarfile.TarInfo('recorded.patch')
                    info.size = raw.stat().st_size
                    archive.addfile(info, io.BytesIO(raw.read_bytes()))
                c.put_archive('/tmp', buf.getvalue())
                # Reconstruct the recorded base->worktree diff exactly, including
                # image preparation changes, then subtract the frozen image HEAD.
                execute('git reset --hard '+exp.data.rows[pid]['base_commit'])
                if raw.stat().st_size:
                    execute('git apply --binary /tmp/recorded.patch')
                normalized = execute('git add -A && git diff --cached --binary '+base)
            finally:
                c.remove(force=True)
            (attempt/'patch.diff').write_text(normalized)
            old_path = attempt/'host_result.json'
            backup = attempt/'host_result.pre_patch_fix.json'
            if old_path.exists() or backup.exists():
                old = read(backup if backup.exists() else old_path)
                if not backup.exists():
                    old_path.rename(backup)
                saved = {'result': old['result'], 'metrics': old['metrics'],
                    'agent_seconds': old['record']['wall_time'],
                    'started': old['record']['timestamp']-old['total_seconds'],
                    'code_hash': old['code_hash'], 'behavioral_hash': old['behavioral_hash']}
            else:
                label = 'task:'+str(attempt.relative_to(root))
                calls = [e for e in events if e.get('kind')=='model_call' and e.get('label')==label]
                assert calls and all('usage' in e for e in calls)
                metrics = {'label':label, 'calls':len(calls), 'tokens':sum(e['usage']['total_tokens'] for e in calls),
                    'prompt_tokens':sum(e['usage'].get('prompt_tokens',0) for e in calls),
                    'completion_tokens':sum(e['usage'].get('completion_tokens',0) for e in calls),
                    'cached_tokens':sum((e['usage'].get('prompt_tokens_details') or {}).get('cached_tokens',0) for e in calls),
                    'tool_calls':sum(e.get('kind')=='tool_call' and e.get('label')==label for e in events),
                    'max_calls':30, 'deadline':None, 'errors':[], 'reconstructed_from_event_ledger':True}
                duration = float((attempt/'process.log').read_text().strip().splitlines()[-1].split('|')[-1])
                launch = max(e['time'] for e in events if e.get('kind')=='agent_launch' and e['time']<calls[0]['time'])
                saved = {'result':read(attempt/'result.json'), 'metrics':metrics, 'agent_seconds':duration,
                    'started':launch, 'wall_time_source':'Bridge run duration; host initialization overhead unavailable.',
                    'code_hash':tree_hash(exp.code()), 'behavioral_hash':tree_hash(exp.code(),True)}
            saved['patch_base_commit'] = base
            saved['repair'] = 'Frozen-image replay, excluding preparation-only file mode changes. No new task model calls.'
            write(attempt/'task_execution.json', saved)
            r = exp.driver.finish_task(pid, attempt)
            write(attempt/'patch_repair.json', {'completed':True, 'time':time.time(), 'image_head':base,
                'image_id':image.id, 'raw_bytes':raw.stat().st_size, 'normalized_bytes':len(normalized.encode()),
                'image_preparation_numstat':delta,
                'score':r['score'], 'timing_note':saved.get('wall_time_source','Original host measurement retained.')})
            append_event(root, {'kind':'patch_collection_repair','task_id':pid,'attempt':str(attempt.relative_to(root)),
                'new_score':r['score'],'no_new_task_model_calls':True})
            for discarded in eligible[1:]:
                write(discarded/'interrupted.json', {'reason':'Spurious retry caused by grading classification bug. Excluded without selecting by score.'})
                if (discarded/'host_result.json').exists():
                    (discarded/'host_result.json').rename(discarded/'excluded_host_result.json')
            print(pid, 'repaired score',r['score'],flush=True)
        agent = root/'results/run_1/agent_0'
        tracker = agent/'benchmarks/swe_lite_dev/results.jsonl'
        if tracker.exists():
            tracker.rename(tracker.with_name('results.pre_patch_fix.jsonl'))
        for summary in (agent/'benchmarks/swe_lite_dev/traces').glob('*/summary.txt'):
            summary.rename(summary.with_name('summary.pre_patch_fix.txt'))
        write(root/'patch_collection_repair.json', {'complete':True,'time':time.time(),
            'reason':'Collect from immutable image preparation HEAD, not dataset base commit. Prior task model outputs retained.'})
    finally:
        exp.close()


if __name__=='__main__':
    main()
