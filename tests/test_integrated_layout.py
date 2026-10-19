"""Run source initialization without download caches and bind the local grader."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mini_swe_rsi.core import ROOT


def test_harness_and_workflow_initialize_from_a_source_only_checkout(tmp_path):
    checkout = tmp_path/'checkout'
    shutil.copytree(ROOT/'src/mini_swe_rsi', checkout/'src/mini_swe_rsi',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(ROOT/'upstream.lock.json', checkout/'upstream.lock.json')
    script = '''
import json, socket
def no_network(*args, **kwargs):
    raise AssertionError('Initialization must not download source')
socket.socket.connect = no_network
from mini_swe_rsi.driver import initial_code
from mini_swe_rsi.core import improvement_contract
initial_code('generated')
print(json.dumps({'protocol': improvement_contract()['version']}))
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=checkout,
                            env={**os.environ, 'PYTHONPATH': str(checkout/'src'),
                                 'PYTHONDONTWRITEBYTECODE': '1'},
                            capture_output=True, text=True, check=True, timeout=60)
    assert json.loads(result.stdout)['protocol'] == 'sica-mini-improvement-v8'
    assert (checkout/'generated/minisweagent/agents/default.py').is_file()
    assert (checkout/'generated/LICENSE.mini').is_file()
    assert not (checkout/'.upstream').exists()
    assert not (checkout/'vendor').exists()


def test_local_grader_source_changes_invalidate_online_recovery(tmp_path, monkeypatch):
    from mini_swe_rsi import online
    from tests.test_online_state import make_loop

    loop = make_loop(tmp_path)
    source = 'src/mini_swe_rsi/evaluation/swebench/harness/grading.py'
    assert source in loop.control_manifest['files']
    original_digest = online.digest
    monkeypatch.setattr(online, 'digest', lambda path:
                        '0'*64 if path == ROOT/source else original_digest(path))
    with pytest.raises(online.OnlineError, match='control_manifest'):
        online.OnlineLoop(loop.root, ('t1',), active_code=loop.active_code,
                          model_identity=loop.model_identity,
                          service_identity=loop.service_identity)
