import pytest
from mini_swe_rsi.archive_view import archive_view
from mini_swe_rsi.core import tree_hash
from mini_swe_rsi.driver import initial_code


def test_public_archive_whitelist_and_frozen_view(tmp_path):
    source = tmp_path/'archive'
    code = source/'agent_0/agent_code'
    initial_code(code)
    (code/'config.json').write_text('{"normal_candidate_config":true}')
    (code/'.ordinary').write_text('candidate-owned source')
    (code/'script.custom').write_text('editable source')
    benchmark = source/'agent_0/benchmarks/swe_lite_dev'
    trace = benchmark/'traces/task'
    trace.mkdir(parents=True)
    for name in ('results.jsonl','results.pre_patch_fix.jsonl','perf.json'):
        (benchmark/name).write_text('{}')
    for name in ('trace.txt','summary.txt','answer.txt','patch.diff','trajectory.json','host_result.json','debug.json'):
        (trace/name).write_text('public' if name.endswith('.txt') else 'secret')
    view = archive_view(source, tmp_path/'view')
    assert tree_hash(view/'agent_0/agent_code') == tree_hash(code)
    names = {p.name for p in view.rglob('*') if p.is_file()}
    assert {'results.jsonl','perf.json','summary.txt','patch.diff'} <= names
    assert not {'results.pre_patch_fix.jsonl','trajectory.json','host_result.json','debug.json'} & names
    assert archive_view(source, view) == view
    (view/'agent_0/benchmarks/swe_lite_dev/perf.json').write_text('modified')
    with pytest.raises(RuntimeError, match='Frozen'):
        archive_view(source, view)


def test_archive_rejects_symlink_exposure(tmp_path):
    source = tmp_path/'archive'
    code = source/'agent_0/agent_code'
    initial_code(code)
    (code/'leak.py').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        archive_view(source, tmp_path/'view')


def test_public_evidence_symlink_fails_closed(tmp_path):
    source = tmp_path/'archive'
    initial_code(source/'agent_0/agent_code')
    external = tmp_path/'external'
    external.mkdir()
    (external/'trace.txt').write_text('private')
    traces = source/'agent_0/benchmarks/swe_lite_dev/traces'
    traces.mkdir(parents=True)
    (traces/'task').symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='Invalid public'):
        archive_view(source, tmp_path/'view')
