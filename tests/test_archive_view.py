import pytest
from mini_swe_rsi.archive_view import online_archive_view
from mini_swe_rsi.core import tree_hash, write
from mini_swe_rsi.driver import initial_code


def online_run(tmp_path):
    source = tmp_path/'run'
    write(source/'online_state.json', {'active_version':'H0000'})
    write(source/'configuration.json', {'tasks':['task']})
    code = source/'versions/H0000/agent_code'
    initial_code(code)
    (code/'config.json').write_text('{"normal_candidate_config":true}')
    (code/'.ordinary').write_text('candidate-owned source')
    (code/'script.custom').write_text('editable source')
    task = source/'tasks/0001-task'
    task.mkdir(parents=True)
    write(task/'public_feedback.json', {'task_id':'task', 'report':'public', 'api_key':'secret'})
    for name in ('trace.txt','execution_tree.txt','answer.txt','patch.diff','activation.json','execution_summary.json'):
        (task/name).write_text('{}' if name.endswith('.json') else 'public')
    (task/'private').mkdir()
    (task/'private'/'raw_response.json').write_text('secret')
    return source


def test_public_online_archive_whitelist_and_frozen_view(tmp_path):
    source = online_run(tmp_path)
    code = source/'versions/H0000/agent_code'
    view = online_archive_view(source, tmp_path/'view')
    assert tree_hash(view/'versions/H0000/agent_code') == tree_hash(code)
    names = {p.name for p in view.rglob('*') if p.is_file()}
    assert {'configuration.json','online_state.json','public_feedback.json','patch.diff'} <= names
    assert not {'raw_response.json', 'execution_tree.txt'} & names
    assert online_archive_view(source, view) == view
    (view/'tasks/0001-task/patch.diff').write_text('modified')
    with pytest.raises(RuntimeError, match='Frozen'):
        online_archive_view(source, view)


def test_online_archive_rejects_symlink_exposure(tmp_path):
    source = online_run(tmp_path)
    code = source/'versions/H0000/agent_code'
    (code/'leak.py').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink|Invalid online version source'):
        online_archive_view(source, tmp_path/'view')


def test_online_public_evidence_symlink_fails_closed(tmp_path):
    source = online_run(tmp_path)
    external = tmp_path/'external'
    external.mkdir()
    (external/'trace.txt').write_text('private')
    (source/'tasks/0001-task/trace.txt').unlink()
    (source/'tasks/0001-task/trace.txt').symlink_to(external/'trace.txt')
    with pytest.raises(ValueError, match='Invalid online public file'):
        online_archive_view(source, tmp_path/'view')
