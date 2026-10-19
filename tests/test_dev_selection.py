import pytest

from mini_swe_rsi.core import Data, read, write
from mini_swe_rsi.online import OnlineError, OnlineLoop, freeze_task_selection


def data(order=None):
    d = Data.__new__(Data)
    dev = order or [f'dev-{i}' for i in range(30)]
    d.manifest = {'splits': {'dev': list(dev), 'val':['val'], 'test':['test']}}
    return d


def test_ordered_selection_is_frozen_and_restart_stable(tmp_path):
    d = data()
    selected = freeze_task_selection(d, tmp_path, 20)
    assert selected == [f'dev-{i}' for i in range(20)]
    frozen = read(tmp_path/'online_task_selection.json')
    assert frozen == {'ids': selected, 'task_limit': 20, 'source_ids': d.ids('dev'), 'split': 'dev'}
    assert d.ids('dev') == [f'dev-{i}' for i in range(30)]
    assert freeze_task_selection(data(), tmp_path, 20) == selected


def test_default_selects_full_manifest_and_ignores_legacy_prefix_artifacts(tmp_path):
    write(tmp_path/'execution_scope.json', {'tracks':['recursive'], 'dev_count':20})
    write(tmp_path/'dev_selection.json', {'ids':['dev-9', 'dev-8'], 'count':2})
    d = data()
    assert freeze_task_selection(d, tmp_path, None) == d.ids('dev')
    frozen = read(tmp_path/'online_task_selection.json')
    assert frozen == {'ids': d.ids('dev'), 'task_limit': None, 'source_ids': d.ids('dev'), 'split': 'dev'}
    # The helper never consults or rewrites the removed prefix selection.
    assert read(tmp_path/'dev_selection.json')['count'] == 2


@pytest.mark.parametrize('limit', [0, -1, True, 20.0, '20'])
def test_invalid_task_limit_rejected(tmp_path, limit):
    with pytest.raises(OnlineError, match='positive integer'):
        freeze_task_selection(data(), tmp_path, limit)
    assert not (tmp_path/'online_task_selection.json').exists()


def test_resume_rejects_reordered_rescoped_or_changed_selection(tmp_path):
    freeze_task_selection(data(), tmp_path, 2)
    with pytest.raises(OnlineError, match='task selection changed'):
        freeze_task_selection(data(), tmp_path, 3)
    with pytest.raises(OnlineError, match='task selection changed'):
        freeze_task_selection(data(), tmp_path, None)
    path = tmp_path/'online_task_selection.json'
    saved = read(path)
    write(path, {**saved, 'ids': list(reversed(saved['ids']))})
    with pytest.raises(OnlineError, match='task selection changed'):
        freeze_task_selection(data(), tmp_path, 2)


def test_selection_is_bound_to_the_manifest_order_and_scope(tmp_path):
    freeze_task_selection(data(), tmp_path, 2)
    with pytest.raises(OnlineError, match='task selection changed'):
        freeze_task_selection(data(['dev-1', 'dev-0'] + [f'dev-{i}' for i in range(2, 30)]), tmp_path, 2)


@pytest.mark.parametrize('name', ['budget.json', 'execution_scope.json', 'dev_selection.json'])
def test_legacy_run_artifacts_are_not_silently_migrated(tmp_path, name):
    root = tmp_path/'run'
    root.mkdir()
    write(root/name, {'legacy': True})
    with pytest.raises(OnlineError, match='Legacy run artifact'):
        OnlineLoop.from_environment(root, data_path=tmp_path/'data')
    assert not (root/'online_task_selection.json').exists()
