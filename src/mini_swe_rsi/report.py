from __future__ import annotations

import json
import re
import time
import tomllib
from collections import Counter
from pathlib import Path

import numpy as np

from .core import ROOT, PHASE_HOURS, read, tree_hash, write
from .experiment import records


_PRIVATE_REPORT_KEYS = {
    'api_key', 'openai_api_key', 'rsi_token', 'authorization', 'password',
    'secret', 'credentials', 'raw_response', 'messages', 'prompt',
    'grade', 'evidence_path', 'private', 'session_token',
}


def _public_report_value(value, key=''):
    """Copy report data while dropping credentials and grader internals."""
    lowered = key.lower()
    if lowered in _PRIVATE_REPORT_KEYS or lowered.endswith('_api_key'):
        return '[redacted]'
    if isinstance(value, dict):
        return {name: _public_report_value(item, name) for name, item in value.items()
                if name.lower() not in _PRIVATE_REPORT_KEYS and not name.lower().endswith('_api_key')}
    if isinstance(value, list):
        return [_public_report_value(item, key) for item in value]
    return value


def build_online_report(root):
    """Produce an online-only report without native champion/gate statistics."""
    root = Path(root).resolve()
    state = read(root/'online_state.json') if (root/'online_state.json').exists() else None
    config = (_public_report_value(read(root/'configuration.json'))
              if (root/'configuration.json').exists() else None)
    tasks = []
    for path in sorted((root/'tasks').iterdir()) if (root/'tasks').exists() else []:
        if not path.is_dir():
            continue
        feedback = path/'public_feedback.json'
        if feedback.exists():
            tasks.append(_public_report_value(read(feedback)))
    rounds = []
    for path in sorted((root/'decisions').glob('R*.json')) if (root/'decisions').exists() else []:
        rounds.append(_public_report_value(read(path)))
    decisions = Counter(item.get('decision') for item in rounds)
    events = []
    usage = {'calls': 0, 'tokens': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
             'unmetered_calls': 0, 'model_seconds': 0.0}
    by_activity = {}
    priced_calls, known_cost, log_complete = 0, 0.0, True
    if (root/'events.jsonl').exists():
        public_keys = {'kind', 'task_id', 'round_id', 'version', 'parent', 'proposal',
                       'decision', 'stage', 'status', 'time', 'online'}
        with (root/'events.jsonl').open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    log_complete = False
                    continue
                if event.get('online'):
                    events.append({key:event[key] for key in public_keys if key in event})
                if event.get('kind') != 'model_call':
                    continue
                label = event.get('label', '')
                activity = ('candidate_task' if label.startswith('task:proposals/') else
                            'task' if label.startswith('task:tasks/') else event.get('role', 'other'))
                totals = by_activity.setdefault(activity, {key: 0 for key in usage})
                for target in (usage, totals):
                    target['calls'] += 1
                    target['model_seconds'] += event.get('seconds', 0)
                    measured = event.get('usage')
                    if isinstance(measured, dict) and all(type(measured.get(k)) is int for k in
                                                         ('total_tokens', 'prompt_tokens', 'completion_tokens')):
                        target['tokens'] += measured['total_tokens']
                        target['prompt_tokens'] += measured['prompt_tokens']
                        target['completion_tokens'] += measured['completion_tokens']
                    else:
                        target['unmetered_calls'] += 1
                if event.get('cost_upper_bound_usd') is not None:
                    priced_calls += 1
                    known_cost += event['cost_upper_bound_usd']
    result = {'mode':'online', 'run':str(root), 'configuration':config,
              'state':state, 'tasks':tasks, 'rounds':rounds,
              'counts':{'tasks':len(tasks), 'rounds':len(rounds),
                        'accepted':decisions.get('accepted',0),
                        'rejected':decisions.get('rejected',0),
                        'skipped':decisions.get('skipped_no_actionable_improvement',0),
                        'diagnosis_failed':decisions.get('diagnosis_failed',0)},
              'online_events':events}
    result.update(total_model_calls=usage['calls'], total_tokens=usage['tokens'],
                  usage=usage, usage_by_activity=by_activity,
                  token_accounting_complete=log_complete and usage['unmetered_calls'] == 0,
                  cost_usd=known_cost if usage['calls'] and priced_calls == usage['calls'] and log_complete else None,
                  event_log_complete=log_complete, test_sealed=True,
                  interpretation='First task attempts and post-feedback checks are separate. Online acceptance does not establish general RSI benefit.')
    if (root/'failure.json').exists():
        result['failure'] = _public_report_value(read(root/'failure.json'))
    write(root/'online_report.json', result)
    lines = ['# mini-swe-rsi online report', '', f'Run: {root}',
             f'Active version: {state.get("active_version") if state else "unknown"}.',
             f'Tasks completed: {len(tasks)}; rounds committed: {len(rounds)}.',
             f'Accepted: {decisions.get("accepted",0)}; rejected: {decisions.get("rejected",0)}; '
             f'skipped: {decisions.get("skipped_no_actionable_improvement",0)}.',
             f'Model calls: {usage["calls"]}; metered tokens: {usage["tokens"]}; unmetered calls: {usage["unmetered_calls"]}.',
             'No general RSI benefit is inferred from these online checks.', '']
    for item in rounds:
        lines.append(f"{item.get('round_id')}: {item.get('decision')}"
                     + (f" ({item.get('parent_version')} -> {item.get('acceptance',{}).get('candidate_score')})"
                        if item.get('decision') == 'accepted' else ''))
    (root/'online_report.md').write_text('\n'.join(lines) + '\n')
    return result


def paired(left, right, ids):
    complete = set(left) == set(right) == set(ids)
    if not complete:
        return {'complete': False, 'left_coverage': len(left), 'right_coverage': len(right), 'expected': len(ids)}
    delta = np.array([left[i]['score'] - right[i]['score'] for i in ids])
    draws = np.random.default_rng(42).choice(delta, size=(10000, len(delta)), replace=True).mean(axis=1)
    return {'complete': True, 'left_successes': sum(left[i]['score'] for i in ids),
            'right_successes': sum(right[i]['score'] for i in ids), 'difference': float(delta.mean()),
            'wins': int((delta > 0).sum()), 'losses': int((delta < 0).sum()),
            'ci95': np.quantile(draws, [.025, .975]).tolist(),
            'token_accounting_complete': all(left[i].get('token_accounting_complete', True)
                                            and right[i].get('token_accounting_complete', True) for i in ids),
            'left_tokens': sum(left[i]['tokens_used'] for i in ids),
            'right_tokens': sum(right[i]['tokens_used'] for i in ids)}


def compute_gates(root, ids):
    root = Path(root)
    mechanism = read(root/'mechanism.json')
    task = []
    for repeat in range(2):
        folder = root/f'private/validation/repeat_{repeat}'
        task.append(paired(records(folder/'reference/results.jsonl'), records(folder/'H0/results.jsonl'), ids))
    probes = []
    for pair in range(2):
        folder = root/f'private/probe_validation/pair_{pair}'
        probes.append(paired(records(folder/'evolved/results.jsonl'), records(folder/'fixed/results.jsonl'), ids))
    task_pass = all(r['complete'] and r['token_accounting_complete'] and r['difference'] > 0
                    and r['left_tokens'] <= 1.1*r['right_tokens'] for r in task)
    # Equal caps are enforced by the host. Judge the produced candidate, including
    # a valid partial edit left at the deadline; a submission label is not a gate.
    probe_budget_ok = True
    for pair in range(2):
        for role in ('evolved', 'fixed'):
            path = root/f'probes/pair_{pair}/{role}/lineage.json'
            if not path.exists():
                probe_budget_ok = False
                continue
            data = read(path)
            # Current sessions have independent per-agent iteration limits and
            # no shared request cap. Older records without max_calls retain
            # their historical 30-request contract.
            max_calls = data['metrics'].get('max_calls', 30)
            if max_calls is not None and data['metrics']['calls'] > max_calls:
                probe_budget_ok = False
    probe_pass = probe_budget_ok and all(r['complete'] and r['difference'] > 0 for r in probes)
    return {'passed': mechanism['passed'] and task_pass and probe_pass,
            'mechanism': mechanism, 'task_benefit': {'passed': task_pass, 'pairs': task},
            'improver_benefit': {'passed': probe_pass, 'pairs': probes,
                               'interpretation': 'Two exploratory pairs, insufficient for a general RSI claim.'}}


def build_report(root):
    root = Path(root).resolve()
    result = {'run': str(root), 'test_sealed': not (root/'test_seal.json').exists(),
              'cost_usd': None, 'cost_note': 'Local endpoint has no verified price. SICA cost=0 is only a protocol placeholder.'}
    dev_count = read(root/'dev_selection.json')['count'] if (root/'dev_selection.json').exists() else 30
    result['development_tasks'] = dev_count
    search_count = read(root/'search_selection.json')['count'] if (root/'search_selection.json').exists() else dev_count
    result['search_tasks'] = search_count
    for name in ('search_selection', 'dev_selection', 'recursive_track_result', 'configuration', 'preflight', 'patch_collection_repair', 'baseline', 'champions', 'mechanism', 'gates', 'failure', 'futility', 'image_validation', 'verification'):
        p = root/f'{name}.json'
        if p.exists():
            result[name] = read(p)
    events = []
    if (root/'events.jsonl').exists():
        events = [json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    calls = [e for e in events if e['kind'] == 'model_call']
    result['total_model_calls'] = len(calls)
    result['total_tokens'] = sum(e.get('usage', {}).get('total_tokens', 0) for e in calls)
    result['model_error_calls'] = sum('error' in e for e in calls)
    result['unknown_usage_calls'] = sum('usage' not in e for e in calls)
    result['model_seconds'] = sum(e.get('seconds', 0) for e in calls)
    result['model_usage_by_role'] = {}
    for role in ('task','proposal','probe','summary'):
        selected = [e for e in calls if e.get('label','').split(':',1)[0] == role]
        result['model_usage_by_role'][role] = {'calls':len(selected),
            'tokens':sum(e.get('usage',{}).get('total_tokens',0) for e in selected),
            'seconds':sum(e.get('seconds',0) for e in selected)}
    result['infrastructure_errors'] = [e for e in events if e['kind'] == 'infrastructure_error']
    result['budget'] = read(root/'budget.json') if (root/'budget.json').exists() else None
    if result['budget']:
        finished = read(root/'run_finished.json')['time'] if (root/'run_finished.json').exists() else time.time()
        result['elapsed_seconds'] = finished-result['budget']['started']
        result['phase_limits_hours'] = PHASE_HOURS
        ordered = sorted(result['budget']['phases'].items(),key=lambda item:item[1])
        result['phase_elapsed_seconds'] = {phase:(ordered[i+1][1] if i+1<len(ordered) else finished)-started
                                           for i,(phase,started) in enumerate(ordered)}
        result['expired_phases'] = [phase for phase,elapsed in result['phase_elapsed_seconds'].items()
                                   if elapsed > PHASE_HOURS[phase]*3600]
        # A completed phase stays historical; classify a stop only if coverage is unfinished.
        active = max(((phase, started) for phase, started in result['budget']['phases'].items()
                      if phase != 'report'), key=lambda item:item[1], default=None)
        stopped_at = result.get('failure',{}).get('time',time.time())
        if result.get('failure') and (stopped_at-result['budget']['started'] >= 24*3600
            or (active and stopped_at-active[1] >= PHASE_HOURS[active[0]]*3600)):
            result['stop_classification'] = 'budget_insufficient'
        elif result.get('gates', {}).get('status') == 'remaining_evaluations_deferred':
            result['stop_classification'] = 'user_scope_deferred'
        elif result.get('gates') and not result['gates'].get('passed'):
            gates = result['gates']
            result['stop_classification'] = ('mechanism_not_established' if not gates.get('mechanism',{}).get('passed')
                else 'harness_no_benefit' if not gates.get('task_benefit',{}).get('passed')
                else 'improver_no_positive_evidence')
    if (root/'model_identity_end.json').exists():
        result['model_identity_end'] = read(root/'model_identity_end.json')
        result['model_identity_verified_end'] = result['model_identity_end'] == read(root/'model_identity.json')
    if (root/'runtime-image.json').exists():
        result['runtime_archive'] = read(root/'runtime-image.json')
    if (root/'image_inventory.json').exists():
        from .core import digest
        result['frozen_image_inventory'] = {'file':'image_inventory.json',
                'sha256':digest(root/'image_inventory.json'), 'count':len(read(root/'image_inventory.json'))}
    result['code_size'] = {label: sum(len(p.read_text().splitlines()) for p in folder.rglob('*.py'))
                           for label, folder in [('project', ROOT/'src'), ('runtime', ROOT/'runtime'),
                              ('scripts', ROOT/'scripts'), ('tests', ROOT/'tests'), ('vendored_sica', ROOT/'vendor/sica')]}
    result['code_size']['own_production_python'] = sum(result['code_size'][k] for k in ('project','runtime','scripts'))
    result['code_size_note'] = 'Python lines including reused grader; tests and vendored upstream separate. Installed dependency source is not included in Python LOC; package counts reported separately.'
    mini = root/'results/run_1/agent_0/agent_code/minisweagent'
    result['code_size']['archived_official_mini'] = sum(len(p.read_text().splitlines()) for p in mini.rglob('*.py'))
    metadata = tomllib.loads((ROOT/'pyproject.toml').read_text())
    result['dependencies'] = {'direct_project': len(metadata['project']['dependencies']),
                              'locked_host': len(tomllib.loads((ROOT/'uv.lock').read_text())['package']),
                              'evaluator': len((ROOT/'evaluator.lock.txt').read_text().splitlines()),
                              'locked_runtime':sum(bool(re.match(r'^[A-Za-z0-9_.-]+==',line))
                                     for line in (ROOT/'runtime/requirements.lock').read_text().splitlines())}
    result['process_contract'] = {'model_concurrency': 1, 'task': ['host_broker','restricted_relay','mini_controller','solver'],
                                  'grading': ['private_evaluator','fresh_scoring_container'],
                                  'improvement': ['host_broker','restricted_relay','archived_mini_controller'],
                                  'primary_slots': {'task':4,'grading':4,'improvement':3},
                                  'count_note':'Host driver and broker share one process. Broker and relay remain during grading. Counts exclude the external model server, Docker daemon and temporary command/test children; they are architecture slots, not a sampled OS-process peak.'}
    task_metrics = []
    for p in root.rglob('host_result.json'):
        r = read(p)
        task_metrics.append({'path': str(p.relative_to(root)), 'exit_status': r['result'].get('exit_status'),
                             'record': r['record'], 'metrics': r['metrics'],
                             'total_seconds': r.get('total_seconds'), 'grading_reason': r.get('grade', {}).get('reason')})
    result['attempts'] = task_metrics
    # Preflight evidence is copied into the initial archive; count the physical attempt once.
    unique_attempts = {r['metrics']['label']: r for r in task_metrics}
    ledger_by_label = {}
    for event in calls:
        ledger_by_label.setdefault(event['label'], []).append(event)
    mismatches = []
    for label, attempt in unique_attempts.items():
        observed = ledger_by_label.get(label, [])
        actual = {'calls':len(observed),
                  'tokens':sum(e.get('usage',{}).get('total_tokens',0) for e in observed),
                  'unmetered_calls':sum('usage' not in e for e in observed)}
        stored = {k:attempt['metrics'].get(k,0) for k in actual}
        if actual != stored:
            mismatches.append({'label':label,'ledger':actual,'task_metrics':stored})
    result['usage_reconciliation'] = {'checked_task_attempts':len(unique_attempts),
            'mismatches':mismatches,'passed':not mismatches,
            'scope':'Completed task snapshots versus the host model-call ledger; all calls, including excluded infrastructure attempts, remain in total cost.'}
    result['task_exit_counts'] = dict(Counter(r['exit_status'] for r in unique_attempts.values()))
    result['context_limit_attempts'] = sum('ContextLimitExceeded' in r['metrics'].get('errors', [])
                                           for r in unique_attempts.values())
    result['format_failure_attempts'] = sum('Format' in str(r['exit_status']) for r in unique_attempts.values())
    result['agent_process_error_attempts'] = sum(r['exit_status'] == 'AgentProcessError' for r in unique_attempts.values())
    result['interrupted_attempts'] = [str(p.relative_to(root)) for p in root.rglob('interrupted.json')
                                    if not '/benchmarks/' in str(p)]
    result['audit_snapshots'] = [e for e in events if e['kind'] == 'implementation_snapshot']
    result['engineering_events'] = [e for e in events if e['kind'] in
                                   ('engineering_pause', 'engineering_cleanup', 'patch_collection_repair', 'recovery_started')]
    result['requested_model_ids'] = sorted({e['model'] for e in calls})
    result['response_model_ids'] = sorted({e['response']['model'] for e in calls if e.get('response', {}).get('model')})
    result['search'] = {}
    for run in (1, 2):
        agents = []
        partial = []
        for folder in sorted((root/f'results/run_{run}').glob('agent_*')):
            perf = folder/'benchmarks/swe_lite_dev/perf.json'
            if perf.exists():
                item = {'iteration': int(folder.name.split('_')[1]), 'perf': read(perf)}
                item['code_hash'] = tree_hash(folder/'agent_code')
                item['same_code_as_H0'] = item['code_hash'] == result.get('baseline',{}).get('code_hash')
                if (folder/'lineage.json').exists():
                    item['lineage'] = read(folder/'lineage.json')
                agents.append(item)
            else:
                rows = records(folder/'benchmarks/swe_lite_dev/results.jsonl')
                partial.append({'iteration':int(folder.name.split('_')[1]),
                                'coverage':sum(r.get('score') is not None for r in rows.values()),
                                'started_tasks':len(rows),
                                'expected':search_count,'score_is_not_complete':True,
                                'lineage':read(folder/'lineage.json') if (folder/'lineage.json').exists() else None})
        result.setdefault('incomplete_search', {})['recursive' if run == 1 else 'fixed'] = partial
        result['search']['recursive' if run == 1 else 'fixed'] = agents
    if (root/'test_seal.json').exists():
        manifest = read(Path(result['configuration']['data_manifest']))
        ids = manifest['splits']['test']
        final = {k: records(root/f'private/final/{k}/results.jsonl') for k in ('H0','reference','minimal','fixed')}
        effect = paired(final['minimal'], final['H0'], ids)
        fidelity = paired(final['minimal'], final['reference'], ids)
        result['final'] = {'effect': effect, 'fidelity': fidelity,
             'effect_passed': effect['complete'] and effect['ci95'][0] > 0,
             'fidelity_passed': fidelity['complete'] and fidelity['ci95'][0] >= -.03,
             'native_vs_fixed': paired(final['reference'], final['fixed'], ids)}
    gates = result.get('gates', {})
    final = result.get('final', {})
    result['conclusions'] = {
        'recursive_mechanism': result.get('mechanism', {}).get('passed'),
        'held_out_task_benefit': gates.get('task_benefit', {}).get('passed'),
        'improver_positive_signal': gates.get('improver_benefit', {}).get('passed'),
        'final_task_effect': final.get('effect_passed') if final.get('effect', {}).get('complete') else None,
        'simplification_fidelity': final.get('fidelity_passed') if final.get('fidelity', {}).get('complete') else None}
    write(root/'report.json', result)
    lines = ['# mini-swe-rsi 实验报告', '', f"运行：{root}",
             f"模型调用：{result['total_model_calls']}；已计量 tokens：{result['total_tokens']}；未知用量调用：{result['unknown_usage_calls']}。",
             f"测试集封存：{result['test_sealed']}。美元成本未知，未将本地推理计为免费。", '']
    if result.get('elapsed_seconds') is not None:
        lines += [f"含排障和报告的已记录墙钟：{result['elapsed_seconds']/3600:.2f} 小时；全局上限24小时。", '']
    if 'model_identity_verified_end' in result:
        lines += [f"末尾模型身份与开始一致：{result['model_identity_verified_end']}。", '']
    reconciliation = result['usage_reconciliation']
    lines += [f"用量对账：{reconciliation['checked_task_attempts']} 个完成任务，差异 {len(reconciliation['mismatches'])} 个。", '']
    if result.get('patch_collection_repair'):
        lines += ['适配排障记录：早期补丁错误包含镜像准备提交；已按 Git 历史重建并独立重评同一份模型输出。',
                  '原补丁、原评分与排障成本保留；因评分分类错误多出的作答已排除，没有择优。详见工程审计事件。', '']
    if result.get('image_validation'):
        images = result['image_validation']
        lines += [f"镜像清单冻结160项；本地已核对 {images['present']} 项，缺失 {len(images['missing'])} 项。缺失镜像恢复未计入已缓存任务的吞吐预测。", '']
    if 'baseline' in result:
        lines.append(f"H0 开发集成功率：{result['baseline']['perf']['avg_score']:.2%}（{result['baseline'].get('coverage', dev_count)}题完整覆盖）。")
    else:
        lines.append('基线尚未完成，不报告完整成功率。')
    for label, value in [('递归机制', result.get('mechanism',{}).get('passed')),
                         ('精简门槛', result.get('gates',{}).get('passed'))]:
        lines.append(f'{label}：{value if value is not None else "未测得"}。')
    labels = {'held_out_task_benefit': 'Harness 验证集收益', 'improver_positive_signal': '改进能力初步信号',
              'final_task_effect': '最终测试集任务效果', 'simplification_fidelity': '精简保真'}
    for key, label in labels.items():
        value = result['conclusions'][key]
        lines.append(f'{label}：{"通过" if value is True else "未通过" if value is False else "未测得／证据不足"}。')
    if any(result['search'].values()):
        lines += ['', f'| 轨道 | 归档版本 | 搜索子集{search_count}题成功率 | 任务 tokens | 与H0源码相同 |', '|---|---:|---:|---:|---|' ]
        for arm, agents in result['search'].items():
            for agent in agents:
                perf = agent['perf']
                lines.append(f"| {arm} | {agent['iteration']} | {perf['avg_score']:.2%} | {perf['tokens']} | {'是' if agent['same_code_as_H0'] else '否'} |")
        lines += ['', '开发集分数只用于候选选择，不能作为验证集或测试集收益结论。相同源码副本的分数变化不能证明 Harness 改进。']
    for key, label in [('effect', '精简版 − H0'), ('fidelity', '精简版 − 完整参考版')]:
        pair = final.get(key, {})
        if pair.get('complete'):
            lines.append(f"{label}：差值 {pair['difference']:.2%}，配对 bootstrap 95% CI {pair['ci95']}；胜/负 {pair['wins']}/{pair['losses']}。")
    if result.get('stop_classification'):
        lines += ['', '停止分类：'+result['stop_classification']]
    if 'failure' in result:
        lines += ['', '停止原因：' + result['failure']['reason']]
    elif gates.get('reason'):
        lines += ['', '停止原因：' + gates['reason']]
    if result.get('futility'):
        proof = result['futility']
        lines += [f"已生成 {proof['completed_native_proposals']} 份递归提案；即使剩余提案全部改变行为，最多也只能得到 {proof['maximum_possible_behavior_changes']} 代，低于要求的2代。父子完整源码摘要见 futility.json。"]
    if not result.get('gates',{}).get('passed'):
        lines += ['', '未进行精简，未宣称 RSI 有效；test100 保持封存。']
    lines += ['', '完整逐题结果、谱系与资源记录见 report.json、events.jsonl、results/ 和 private/。',
              '行为证据来自自回放校准后、相同模型回复与工具输出下候选和实际父版本的差异。回放使用无网络隔离容器；缺少记录或校准不一致时不计行为证据。']
    (root/'report.md').write_text('\n'.join(lines)+'\n')
    return result
