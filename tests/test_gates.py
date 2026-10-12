from mini_swe_rsi.core import write
from mini_swe_rsi.experiment import save_records
from mini_swe_rsi.report import compute_gates


def test_gates_require_both_repeats_and_no_actual_probe_quota_overrun(tmp_path):
    ids = ['a', 'b']
    write(tmp_path/'mechanism.json', {'passed': True})
    good = {i: {'problem_id': i, 'score': 1.0, 'tokens_used': 110} for i in ids}
    base = {i: {'problem_id': i, 'score': 0.0, 'tokens_used': 100} for i in ids}
    for repeat in range(2):
        folder = tmp_path/f'private/validation/repeat_{repeat}'
        save_records(folder/'reference/results.jsonl', good)
        save_records(folder/'H0/results.jsonl', base)
        folder = tmp_path/f'private/probe_validation/pair_{repeat}'
        save_records(folder/'evolved/results.jsonl', good)
        save_records(folder/'fixed/results.jsonl', base)
        for role in ('evolved', 'fixed'):
            write(tmp_path/f'probes/pair_{repeat}/{role}/lineage.json',
                  {'metrics': {'calls': 30, 'errors': []}, 'process_status': 0, 'exit_status': 'Submitted'})
    assert compute_gates(tmp_path, ids)['passed']
    good['a']['token_accounting_complete'] = False
    save_records(tmp_path/'private/validation/repeat_1/reference/results.jsonl', good)
    assert not compute_gates(tmp_path, ids)['passed']
    good['a']['token_accounting_complete'] = True
    good['a']['tokens_used'] = 111
    save_records(tmp_path/'private/validation/repeat_1/reference/results.jsonl', good)
    assert not compute_gates(tmp_path, ids)['passed']
    good['a']['tokens_used'] = 110
    save_records(tmp_path/'private/validation/repeat_1/reference/results.jsonl', good)
    write(tmp_path/'probes/pair_1/evolved/lineage.json',
          {'metrics': {'calls': 30, 'errors': ['ContextLimitExceeded']},
           'process_status': 0, 'exit_status': 'ContextWindowExceededError'})
    assert compute_gates(tmp_path, ids)['passed']
    write(tmp_path/'probes/pair_1/evolved/lineage.json',
          {'metrics': {'calls': 31, 'errors': []}, 'process_status': 0, 'exit_status': 'Submitted'})
    assert not compute_gates(tmp_path, ids)['passed']
