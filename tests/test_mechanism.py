import json
from types import SimpleNamespace

from mini_swe_rsi.core import tree_hash, write
from mini_swe_rsi.experiment import Experiment


def test_mechanism_requires_two_behavior_changes_and_actual_archived_proposer(tmp_path):
    experiment = Experiment.__new__(Experiment)
    experiment.root = tmp_path
    experiment.data = SimpleNamespace(ids=lambda split: ['task1'], rows={'task1':{'problem_statement':'fixture'}})
    experiment.code = lambda run=1, iteration=0: codes[iteration]
    experiment.driver = SimpleNamespace(replay=lambda code, job, directory: {
        'changed':directory.name == 'parent', 'complete_prefix':directory.name == 'self'})
    write(tmp_path/'model_identity.json', {'model':'fixture'})
    codes = []
    for i in range(3):
        code = tmp_path/f'results/run_1/agent_{i}/agent_code'
        code.mkdir(parents=True)
        (code/'implementation.py').write_text(f'behavior = {i}\n')
        codes.append(code)
    events = [{'kind': 'model_call', 'call': 1, 'label': 'task:preflight/task1/attempt_0',
               'messages': [{'content': 'H0'}]}]
    for i in (1, 2):
        lineage = {'source_changed': True, 'exit_status': 'LimitsExceeded', 'generation': i,
                   'parent':i-1, 'target': str(codes[i]), 'proposer': str(codes[i-1]), 'proposer_hash': tree_hash(codes[i-1]),
                   'activation': {k: f'/home/agent/archive/agent_{i-1}/agent_code/{filename}'
                                  for k, filename in [('agent_file', 'default.py'), ('config_file', 'config.yaml')]}}
        write(codes[i].parent/'lineage.json', lineage)
        attempt = codes[i].parent/'benchmarks/swe_lite_dev/traces/task1/attempt_0'
        write(attempt/'activation.json', {'platform':{}})
        write(attempt/'host_result.json', {'metrics':{'label':f'task:results/run_1/agent_{i}/benchmarks/swe/traces/task1/attempt_0'}, 'result':{'exit_status':'Submitted'}})
        events.extend([{'kind': 'model_call', 'call': 1,
                        'label': f'task:results/run_1/agent_{i}/benchmarks/swe/traces/task1/attempt_0',
                        'messages': [{'content': f'H{i}'}]},
                       {'kind': 'agent_launch', 'command': 'improve', 'code': str(codes[i-1]),
                        'code_hash': tree_hash(codes[i-1])}])
    (tmp_path/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    assert experiment.mechanism()['passed']
    # Rewriting files while continuing to load H0 does not prove recursion.
    lineage['activation']['agent_file'] = '/home/agent/archive/agent_0/agent_code/default.py'
    write(codes[2].parent/'lineage.json', lineage)
    assert not experiment.mechanism()['passed']

    # Inherited behavior plus an unused edit is not another changed generation.
    experiment.driver.replay = lambda code, job, directory: {'changed':False, 'complete_prefix':True}
    import shutil
    shutil.rmtree(tmp_path/'private/parent_behavior_replay')
    assert not experiment.mechanism()['passed']


def test_futility_uses_raw_files_as_conservative_behavior_change_upper_bound(tmp_path):
    experiment = Experiment.__new__(Experiment)
    experiment.root = tmp_path
    codes = []
    for i in range(4):
        code = tmp_path/f'results/run_1/agent_{i}/agent_code'
        code.mkdir(parents=True)
        (code/'agent.py').write_text('x=1\n')
        codes.append(code)
    experiment.code = lambda run=1, iteration=0: codes[iteration]
    for i in (1, 2):
        write(codes[i].parent/'lineage.json', {'parent':0,'generation':i,'target':str(codes[i])})
    assert not experiment.recursion_unreachable(2)
    write(codes[3].parent/'lineage.json', {'parent':0,'generation':3,'target':str(codes[3])})
    assert not experiment.recursion_unreachable(2)  # Earlier iterations are revisited on resume.
    assert experiment.recursion_unreachable(3)
    proof = json.loads((tmp_path/'futility.json').read_text())
    assert proof['maximum_possible_behavior_changes'] == 1
    # Even a documentation change is counted as potentially behavioral, so an
    # unsupported source normalization cannot prematurely stop the experiment.
    (codes[3]/'README.md').write_text('Changed documentation.\n')
    assert not experiment.recursion_unreachable(3)
