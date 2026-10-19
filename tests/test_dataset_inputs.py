"""Dataset loading, selected-image custody and initial-attempt metrics."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import Data, digest, read, write
from mini_swe_rsi.evaluation.prepare import load_source, write_dataset
from mini_swe_rsi.online import freeze_task_selection
from mini_swe_rsi.report import build_online_report


def rows(count=3):
    return [{'instance_id': f'django__django-{i}', 'repo': 'django/django',
             'base_commit': 'a'*40, 'version': '1.9', 'problem_statement': 'Fix the task.',
             'test_patch': '', 'FAIL_TO_PASS': [], 'PASS_TO_PASS': []}
            for i in range(count)]


def prepared(tmp_path, count=3, split='test'):
    destination = tmp_path/'data'
    write_dataset(rows(count), destination, split, {'dataset': 'fixture', 'split': split})
    return Data(destination)


def test_complete_lite_sized_split_has_no_old_count_or_dev_requirement(tmp_path):
    data = prepared(tmp_path, 300)
    assert data.split == 'test' and len(data.ids('test')) == 300
    assert freeze_task_selection(data, tmp_path/'run') == data.ids('test')
    assert read(tmp_path/'run/online_task_selection.json')['split'] == 'test'


def test_manifest_can_select_a_subset_of_a_larger_raw_dataset(tmp_path):
    data = prepared(tmp_path)
    manifest = read(data.path/'manifest.json')
    manifest['splits']['test'] = data.ids('test')[:1]
    write(data.path/'manifest.json', manifest)
    assert len(Data(data.path).rows) == 3
    assert Data(data.path).ids('test') == ['django__django-0']


@pytest.mark.parametrize('extension', ['.json', '.jsonl'])
def test_local_dataset_import_retains_order_and_source_digest(tmp_path, extension):
    source = tmp_path/('tasks'+extension)
    contents = rows()
    source.write_text(json.dumps(contents) if extension == '.json' else
                      '\n'.join(json.dumps(row) for row in contents)+'\n')
    loaded, provenance = load_source(str(source), 'validation')
    assert loaded == contents and provenance['sha256'] == digest(source)
    write_dataset(loaded, tmp_path/'data', 'validation', provenance)
    assert Data(tmp_path/'data').ids('validation') == [row['instance_id'] for row in contents]


@pytest.mark.parametrize('change', ['raw_bytes', 'overlap', 'unknown', 'duplicate'])
def test_dataset_rejects_changed_or_inconsistent_records(tmp_path, change):
    data = prepared(tmp_path)
    manifest = read(data.path/'manifest.json')
    if change == 'raw_bytes':
        with (data.path/'raw.json').open('a') as stream:
            stream.write(' ')
    elif change == 'overlap':
        manifest['splits']['validation'] = [data.ids('test')[0]]
    elif change == 'unknown':
        manifest['splits']['test'][0] = 'unknown'
    else:
        contents = read(data.path/'raw.json')
        contents.append(contents[0])
        write(data.path/'raw.json', contents)
        manifest['raw_sha256'] = digest(data.path/'raw.json')
    write(data.path/'manifest.json', manifest)
    with pytest.raises(ValueError):
        Data(data.path)


def test_preparation_never_overwrites_an_existing_dataset(tmp_path):
    data = prepared(tmp_path)
    before = digest(data.path/'raw.json')
    with pytest.raises(FileExistsError):
        write_dataset(rows(2), data.path, 'test', {})
    assert digest(data.path/'raw.json') == before


def test_selected_images_are_pinned_once_and_resume_ignores_mutable_tags(tmp_path, monkeypatch):
    import docker
    data = prepared(tmp_path)
    selected = freeze_task_selection(data, tmp_path/'run', 1)
    tag = data.image_name(selected[0])
    image = SimpleNamespace(id='sha256:original', attrs={
        'Architecture': 'amd64', 'RepoDigests': ['swebench/fixture@sha256:original']})
    calls = []
    def get(name):
        calls.append(name)
        return image
    monkeypatch.setattr(docker, 'from_env', lambda: SimpleNamespace(images=SimpleNamespace(get=get)))
    data.freeze_images(tmp_path/'run', selected)
    assert calls == [tag]
    inventory = read(tmp_path/'run/image_inventory.json')
    assert set(inventory) == set(selected)
    image.id = 'sha256:tag-changed'
    resumed = Data(data.path)
    resumed.freeze_images(tmp_path/'run', selected)
    assert calls == [tag]
    assert resumed.image(selected[0])['image_id'] == 'sha256:original'
    assert read(tmp_path/'run/image_inventory.json') == inventory


def test_declared_nondefault_split_is_selected_and_frozen(tmp_path):
    data = prepared(tmp_path)
    ids = data.ids('test')
    manifest = read(data.path/'manifest.json')
    manifest['splits'] = {'dev': ids[:1], 'test': ids[1:]}
    write(data.path/'manifest.json', manifest)
    assert freeze_task_selection(Data(data.path, split='dev'), tmp_path/'run') == ids[:1]
    with pytest.raises(ValueError, match='split'):
        Data(data.path, split='missing')


def test_report_scores_initial_attempts_only_and_requires_complete_coverage(tmp_path):
    from mini_swe_rsi.online_contract import ONLINE_PROTOCOL
    write(tmp_path/'configuration.json', {'protocol': ONLINE_PROTOCOL, 'tasks': ['a', 'b']})
    write(tmp_path/'online_state.json', {'protocol': ONLINE_PROTOCOL, 'active_version': 'H0000'})
    write(tmp_path/'tasks/0001-a/public_feedback.json', {'task_id': 'a', 'record': {'score': 1.0}})
    write(tmp_path/'proposals/P0001/check/result.json', {'score': 1.0})
    report = build_online_report(tmp_path)
    assert report['evaluation'] == {'selected_tasks': 2, 'scored_tasks': 1,
                                     'resolved_tasks': 1, 'complete': False, 'resolved_rate': None}
    write(tmp_path/'tasks/0002-b/public_feedback.json', {'task_id': 'b', 'record': {'score': 0.0}})
    report = build_online_report(tmp_path)
    assert report['evaluation']['resolved_rate'] == 0.5
    assert report['evaluation']['complete']
