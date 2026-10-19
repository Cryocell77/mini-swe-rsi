"""Declared location, frozen test equality and setup failure semantics."""
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_swe_rsi.core import ROOT,tree_hash
from mini_swe_rsi.driver import Driver,InfrastructureError,initial_code


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS')!='1',reason='Explicit isolated mechanism containers')
def test_real_mechanism_preserves_relative_file_root_and_missing_parent_tests(tmp_path):
    import docker
    client=docker.from_env()
    driver=Driver.__new__(Driver);driver.client=client;driver.image=client.images.get('mini-swe-rsi-runtime:0.1');driver.running=[]
    parent=tmp_path/'parent';candidate=tmp_path/'candidate';initial_code(parent);shutil.copytree(parent,candidate)
    (candidate/'minisweagent/feature_marker.py').write_text('VALUE = 1\n')
    test=candidate/'tests/nested/test_feature.py';test.parent.mkdir(parents=True)
    test.write_text('''import os,sys,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[2]
assert root==Path(os.environ['RSI_SOURCE_ROOT'])==Path('/source')
assert Path.cwd()==root
assert Path(__file__)==root/'tests/nested/test_feature.py'
assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==os.environ['RSI_MECHANISM_TEST_SHA256']
assert sys.argv[1:]==['expected-argument']
try:
    (root/'must_not_write.py').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('source was writable')
assert not Path('/logs').exists()
assert not (root/'minisweagent/feature_marker.py').exists() or __import__('minisweagent.feature_marker',fromlist=['VALUE']).VALUE==1
sys.exit(0 if (root/'minisweagent/feature_marker.py').exists() else 1)
''')
    before=(tree_hash(parent),tree_hash(candidate))
    try:
        result=driver.mechanism_check(parent,candidate,{'test_path':'tests/nested/test_feature.py','args':['expected-argument']},tmp_path/'check')
        assert result['parent_failed'] and result['candidate_passed'] and result['same_test']
        assert result['execution_contract']['test_entry']=='/source/tests/nested/test_feature.py'
        assert result['execution_contract']['setup_valid']
        assert not (parent/'tests').exists()
        assert (tree_hash(parent),tree_hash(candidate))==before
        for name in ('parent','candidate'):
            assert 'RSI_MECHANISM_SETUP_OK '+result['test_sha256'] in (tmp_path/'check'/f'{name}.log').read_text()
        assert driver.running==[]
    finally:
        for container in list(driver.running):container.remove(force=True)
        client.close()


class FakeContainer:
    id='fixture'
    def __init__(self,status,log):self.status,self.log=status,log;self.removed=False
    def wait(self,timeout):return {'StatusCode':self.status}
    def logs(self):return self.log.encode()
    def remove(self,force):self.removed=True


@pytest.mark.parametrize('status,log',[(125,'RSI_MECHANISM_SETUP_ERROR AssertionError'),(1,'python: cannot open entry'),(None,''),(137,'killed')])
def test_invalid_setup_is_never_counted_as_parent_feature_failure(tmp_path,status,log):
    source=tmp_path/'source';source.mkdir();(source/'untouched.py').write_text('original')
    frozen=tmp_path/'frozen.py';frozen.write_text('raise AssertionError("not executed by fixture")')
    c=FakeContainer(status,log);captured={}
    def run(*args,**kwargs):captured.update(kwargs);return c
    driver=Driver.__new__(Driver);driver.running=[];driver.image=SimpleNamespace(id='fixture')
    driver.client=SimpleNamespace(containers=SimpleNamespace(run=run))
    with pytest.raises(InfrastructureError,match='setup invalid'):
        driver._run_mechanism_process(source,frozen,[],10,test_path='tests/check.py')
    assert c.removed and not driver.running
    assert not (source/'tests').exists()
    assert not Path(next(iter(captured['volumes']))).exists() # disposable view cleaned
    assert captured['network_disabled'] and captured['read_only']
    assert all(v['mode']=='ro' for v in captured['volumes'].values())
