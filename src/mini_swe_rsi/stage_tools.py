"""Host-owned shell isolation for the online seven-step workflow."""
from pathlib import Path

from .core import read


def stage_permissions(logs):
    state = read(Path(logs) / 'workflow_state.json')
    active = state.get('active') or {}
    index = active.get('index')
    if (type(index) is not int or state.get('completed_steps') != index
            or state.get('status') != 'improving' or index not in (3, 4, 5, 6)):
        raise ValueError('Online shell is available only at steps 4-7')
    return index + 1


def tool_mounts(candidate, archive, step):
    candidate = Path(candidate).resolve()
    # Validate before constructing file mounts: a symlink must never grant an
    # alias to source, private state or another host path.
    for item in candidate.rglob('*'):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError('Candidate contains a symlink or special file')
    mounts = {str(candidate): {'bind': '/home/agent/workdir', 'mode': 'rw' if step == 4 else 'ro'}}
    if archive is not None:
        mounts[str(Path(archive).resolve())] = {'bind': '/home/agent/archive', 'mode': 'ro'}
    if step in (6, 7):
        for name in ('description.txt', 'agent_change_log.md'):
            item = candidate / name
            if not item.is_file():
                raise ValueError('Documentation file is missing')
            mounts[str(item)] = {'bind': '/home/agent/workdir/' + name, 'mode': 'rw'}
    return mounts


class StageTools:
    """Tools run away from controller memory, tokens and writable workflow logs.

    A fresh sandbox is used on every stage transition, so a background process
    from step 4 cannot retain a writable mount after the candidate is frozen.
    """
    def __init__(self, driver, logs, candidate, archive):
        self.driver, self.logs, self.candidate, self.archive = driver, Path(logs), candidate, archive
        self.container = None
        self.step = None

    def command(self, command, seconds):
        import subprocess
        step = stage_permissions(self.logs)
        if step != self.step:
            self.close()
            self.container = self.driver.client.containers.run(
                self.driver.image.id, ['python', '-c', 'import time; time.sleep(10**9)'],
                detach=True, volumes=tool_mounts(self.candidate, self.archive, step),
                network_mode='none', read_only=True, tmpfs={'/tmp': 'size=256m'},
                working_dir='/home/agent/workdir',
                environment={'PYTHONPATH': '/home/agent/workdir', 'PYTHONDONTWRITEBYTECODE': '1',
                             'HOME': '/tmp', 'MSWEA_CONFIG_ROOT': '/tmp/mini-config'},
                cap_drop=['ALL'], security_opt=['no-new-privileges'],
                mem_limit='2g', nano_cpus=1000000000, pids_limit=128)
            self.step = step
            self.driver.running.append(self.container)
        return ['docker', 'exec', '-w', '/home/agent/workdir', self.container.id,
                'timeout', '-k', '2', str(seconds), 'bash', '-lc', command]

    def close(self):
        if self.container is not None:
            self.container.remove(force=True)
            if self.container in self.driver.running:
                self.driver.running.remove(self.container)
            self.container = None
        self.step = None
