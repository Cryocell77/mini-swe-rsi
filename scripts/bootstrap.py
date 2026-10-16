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

    # Verify every source file retained in the lock.  The mini package is
    # stored below src/, while the SICA task prompt and license come from
    # their historical upstream locations rather than archive-root names.
    for filename, expected in entry.get('files', {}).items():
        if name == 'mini':
            source = target/'src/minisweagent'/filename
        elif name == 'sica' and filename == 'agent_main.py':
            source = target/'base_agent/__main__.py'
        else:
            source = target/filename
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f'{name}: source file differs from the lock: {filename}')

        # Keep the small retained SICA source bundle in sync with the fetched
        # archive.  The removed runner and batch-analysis files have no entry
        # here and therefore cannot be recreated by bootstrap.
        if name == 'sica':
            destination = root/'vendor/sica'/filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

    for filename, source in entry.get('workflow_files', {}).items():
        raw = (target/source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError(f'{name}: workflow source differs: {filename}')
        destination = root/'vendor/sica_workflow'/filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)

    for filename, expected in entry.get('budget_files', {}).items():
        if hashlib.sha256((target/filename).read_bytes()).hexdigest() != expected:
            raise ValueError(f'{name}: budget source differs: {filename}')
