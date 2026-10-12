"""Fetch the exact upstream archives and verify bytes before extracting."""
import hashlib
import io
import json
import pathlib
import tarfile
import urllib.request

root = pathlib.Path(__file__).resolve().parents[1]
lock = json.loads((root/'upstream.lock.json').read_text())
for name, entry in lock.items():
    url = f"https://codeload.github.com/{entry['repository']}/tar.gz/{entry['commit']}"
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != entry['archive_sha256']:
        raise ValueError(f'{name}: archive bytes differ from the lock')
    target = root/'.upstream'/name
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive.getmembers():
            parts = pathlib.PurePosixPath(item.name).parts[1:]
            if parts:
                item.name = str(pathlib.Path(*parts))
                archive.extract(item, target, filter='data')
    print(name, entry['commit'])
