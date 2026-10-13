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


def test_failed_search_set_is_common_and_preserves_source_baseline(tmp_path):
    import json
    from mini_swe_rsi.core import apply_failed_search_selection, digest
    d=data()
    write(tmp_path/'execution_scope.json', {'dev_count':20})
    freeze_dev_selection(d,tmp_path)
    source=tmp_path/'private/failure_subset_migration/h0_results_full.jsonl'
    source.parent.mkdir(parents=True)
    rows=[{'problem_id':i,'score':0 if i in ['dev-2','dev-7'] else 1} for i in d.ids('dev')]
    source.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    write(tmp_path/'baseline.json', {'complete':True,'coverage':20})
    marker={'policy':'frozen baseline failures','ids':['dev-2','dev-7'],'count':2,'source_results_sha256':digest(source),'baseline_coverage':20}
    write(tmp_path/'search_selection.json',marker)
    apply_failed_search_selection(d,tmp_path)
    assert d.ids('dev') == ['dev-2','dev-7']
    assert read(tmp_path/'baseline.json')['coverage'] == 20
    assert len(source.read_text().splitlines()) == 20
    marker['ids']=['dev-1','dev-2']
    write(tmp_path/'search_selection.json',marker)
    other=data()
    freeze_dev_selection(other,tmp_path)
    with pytest.raises(ValueError,match='Frozen failure selection changed'):
        apply_failed_search_selection(other,tmp_path)
