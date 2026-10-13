import pytest
from mini_swe_rsi.core import Data, freeze_dev_selection, write, read


def data():
    d = Data.__new__(Data)
    d.manifest = {'splits': {'dev':[f'dev-{i}' for i in range(30)], 'val':['val'], 'test':['test']}}
    return d


def test_prefix_is_ordered_frozen_and_does_not_change_held_out(tmp_path):
    write(tmp_path/'execution_scope.json', {'tracks':['recursive'], 'dev_count':20})
    d = data()
    freeze_dev_selection(d, tmp_path)
    assert d.ids('dev') == [f'dev-{i}' for i in range(20)]
    assert d.ids('val') == ['val'] and d.ids('test') == ['test']
    assert len(d.manifest['splits']['dev']) == 30
    again = data()
    freeze_dev_selection(again, tmp_path)
    assert again.ids('dev') == d.ids('dev')
    write(tmp_path/'execution_scope.json', {'dev_count':19})
    with pytest.raises(ValueError, match='Frozen development selection changed'):
        freeze_dev_selection(data(), tmp_path)
    write(tmp_path/'execution_scope.json', {})
    with pytest.raises(ValueError, match='Frozen development selection changed'):
        freeze_dev_selection(data(), tmp_path)


@pytest.mark.parametrize('count', [0,31,True,20.0,'20'])
def test_invalid_prefix_rejected(tmp_path,count):
    write(tmp_path/'execution_scope.json', {'dev_count':count})
    with pytest.raises(ValueError, match='Invalid development prefix'):
        freeze_dev_selection(data(), tmp_path)
    assert not (tmp_path/'dev_selection.json').exists()


def test_default_remains_full_dev30(tmp_path):
    d=data()
    freeze_dev_selection(d,tmp_path)
    assert len(d.ids('dev')) == 30
    assert not (tmp_path/'dev_selection.json').exists()


def test_report_uses_selected_count_and_marks_scope_deferral(tmp_path):
    import time
    from mini_swe_rsi.report import build_report
    write(tmp_path/'dev_selection.json', {'count':20})
    folder=tmp_path/'results/run_1/agent_0/benchmarks/swe_lite_dev'
    folder.mkdir(parents=True)
    (folder/'results.jsonl').write_text('{"problem_id":"a","score":1.0}\n')
    write(tmp_path/'budget.json', {'started':time.time(), 'phases':{}})
    write(tmp_path/'gates.json', {'passed':False,'status':'remaining_evaluations_deferred','mechanism':{'passed':True}})
    report=build_report(tmp_path)
    assert report['development_tasks'] == 20
    assert report['incomplete_search']['recursive'][0]['expected'] == 20
    assert report['stop_classification'] == 'user_scope_deferred'
