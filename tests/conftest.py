"""Test-only Docker image override for isolated runtime image reviews."""
import os

import pytest


@pytest.fixture(autouse=True)
def _runtime_image_override(monkeypatch):
    image = os.environ.get('RSI_TEST_RUNTIME_IMAGE')
    if not image:
        return
    from docker.models.images import ImageCollection

    original = ImageCollection.get

    def get(collection, name, *args, **kwargs):
        if name == 'mini-swe-rsi-runtime:0.1':
            name = image
        return original(collection, name, *args, **kwargs)

    monkeypatch.setattr(ImageCollection, 'get', get)
