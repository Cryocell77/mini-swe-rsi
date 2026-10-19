"""Prepare SWE-bench records without running models or pulling task images."""
import argparse
import json
from pathlib import Path
import tempfile

from mini_swe_rsi.core import Data, digest, read, write


def load_source(source, split, revision=None):
    local = Path(source).expanduser()
    if local.is_file():
        if local.suffix == '.jsonl':
            rows = [json.loads(line) for line in local.read_text().splitlines() if line.strip()]
        else:
            rows = read(local)
        return rows, {'file': str(local.resolve()), 'sha256': digest(local), 'split': split}
    from datasets import load_dataset
    dataset = load_dataset(source, split=split, revision=revision)
    return list(dataset), {'dataset': source, 'split': split, 'revision': revision,
                           'fingerprint': dataset._fingerprint}


def write_dataset(rows, destination, split, source):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError('Dataset destination already exists: ' + str(destination))
    if not isinstance(rows, list) or not rows:
        raise ValueError('Dataset must be a nonempty list of SWE-bench records')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dataset-', dir=destination.parent) as tmp:
        staging = Path(tmp)/'data'
        write(staging/'raw.json', rows)
        write(staging/'manifest.json', {
            'source': source, 'default_split': split,
            'raw_sha256': digest(staging/'raw.json'),
            'splits': {split: [row.get('instance_id') for row in rows]}})
        Data(staging, split=split)
        staging.rename(destination)
    return {'data': str(destination), 'split': split, 'tasks': len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--split', default='test')
    parser.add_argument('--revision')
    args = parser.parse_args()
    rows, source = load_source(args.dataset, args.split, args.revision)
    from .swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
    for row in rows:
        if row.get('version') not in MAP_REPO_VERSION_TO_SPECS.get(row.get('repo'), {}):
            raise ValueError('Unsupported evaluator repository/version: ' + str(row.get('instance_id')))
    result = write_dataset(rows, args.data, args.split, source)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
