"""Public, frozen archive mount; authoritative SICA selection reads the original."""
from pathlib import Path
import re
import shutil

from .core import digest, read, write

PUBLIC_TRACE = {'answer.txt', 'trace.txt', 'execution_tree.txt', 'patch.diff', 'summary.txt', 'score.txt'}


def archive_view(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError('Symlink archive')
    files = []
    for agent in sorted(source.iterdir()):
        if not re.fullmatch(r'agent_\d+', agent.name):
            continue
        if agent.is_symlink() or not agent.is_dir():
            raise ValueError('Invalid archive agent')
        code = agent/'agent_code'
        if not code.exists():
            continue
        if code.is_symlink():
            raise ValueError('Symlink source')
        for p in code.rglob('*'):
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                raise ValueError('Source contains symlink or special file')
            relative = p.relative_to(code)
            if '__pycache__' in relative.parts:
                continue
            if p.is_file():
                if p.name == '.env' or p.name.startswith('.env.'):
                    raise ValueError('Credential environment file in editable source')
                files.append(p)
        public = [agent/'meta_improvement_logs/summary.txt']
        if (agent/'benchmarks').is_symlink():
            raise ValueError('Invalid public archive directory')
        for benchmark in (agent/'benchmarks').glob('*'):
            if benchmark.name != 'swe_lite_dev':
                continue
            if benchmark.is_symlink() or not benchmark.is_dir() or (benchmark/'traces').is_symlink():
                raise ValueError('Invalid public archive directory')
            public += [benchmark/'results.jsonl', benchmark/'perf.json']
            for trace in (benchmark/'traces').glob('*'):
                if trace.is_symlink() or not trace.is_dir():
                    raise ValueError('Invalid public archive directory')
                public += [trace/name for name in PUBLIC_TRACE]
        for p in public:
            if p.exists():
                if p.is_symlink() or any(parent.is_symlink() for parent in p.parents if parent != source.parent) or not p.is_file():
                    raise ValueError('Invalid public archive file')
                files.append(p)
    manifest = {str(p.relative_to(source)): digest(p) for p in files}
    marker = destination.parent/(destination.name + '.manifest.json')
    if destination.exists():
        if destination.is_symlink() or any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in destination.rglob('*')):
            raise RuntimeError('Frozen archive view contains a symlink or special file')
        if read(marker) != manifest or {str(p.relative_to(destination)): digest(p) for p in destination.rglob('*') if p.is_file()} != manifest:
            raise RuntimeError('Frozen archive view changed or source evidence changed')
        return destination
    destination.mkdir(parents=True)
    for p in files:
        target = destination/p.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    write(marker, manifest)
    return destination
