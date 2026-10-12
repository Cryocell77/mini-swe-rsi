from pathlib import Path

import pytest

from mini_swe_rsi.core import Data, write


def fixture_data():
    data = Data.__new__(Data)
    data.ids = lambda split: [split]
    data.image = lambda pid: {'image_id':'sha256:fixed-'+pid,'repo_digests':['frozen@sha256:'+pid]}
    return data


def test_inventory_rejects_changed_metadata_on_resume(tmp_path):
    data = fixture_data()
    data.freeze_images(tmp_path)
    data.freeze_images(tmp_path)
    data.image = lambda pid: {'image_id':'sha256:changed'}
    with pytest.raises(ValueError, match='across restart'):
        data.freeze_images(tmp_path)


def test_inventory_backfill_must_match_prior_scoring_jobs(tmp_path):
    data = fixture_data()
    write(tmp_path/'private/grading/job/job.json', {'task':{'instance_id':'dev'},
                                                 'frozen_image':{'image_id':'different'}})
    with pytest.raises(ValueError, match='Prior scoring image'):
        data.freeze_images(tmp_path)
    assert not (tmp_path/'image_inventory.json').exists()
