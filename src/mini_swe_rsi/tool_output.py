"""Bounded streaming capture for untrusted online improvement commands."""
import hashlib
import selectors
import subprocess
import time
import uuid


def capture_stage_output(args, seconds, tools, limits):
    """Keep observations small and make every omitted byte auditable on disk.

    Exceeding the hard byte limit destroys the command sandbox (including its
    descendants). The saved artifact is explicitly a prefix, never full output.
    Benchmark solver execution does not use this local-check resource policy.
    """
    tools.output_dir.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + '.log'
    path = tools.output_dir / name
    maximum = limits['stage_output_max_bytes']
    stored = observed = 0
    error = ''
    sha = hashlib.sha256()
    deadline = time.monotonic() + seconds + 5
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    try:
        with path.open('xb') as output, selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error = 'CommandTimeout'
                    break
                if not selector.select(min(remaining, .1)):
                    continue
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                observed += len(chunk)
                saved = chunk[:maximum-stored]
                output.write(saved)
                sha.update(saved)
                stored += len(saved)
                if observed > maximum:
                    error = 'ToolOutputLimitExceeded'
                    break
            if not error:
                try:
                    code = process.wait(timeout=max(.01, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    error = 'CommandTimeout'
    finally:
        try:
            if process.poll() is None or error:
                # Killing only `docker exec` on the host leaves the child alive.
                tools.close()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            process.stdout.close()
    if error:
        code = -1
    metadata = {'artifact': 'tool_outputs/' + name,
                'sandbox_path': '/home/agent/tool_outputs/' + name,
                'stored_bytes': stored, 'observed_bytes': observed,
                'sha256': sha.hexdigest(), 'complete': not bool(error),
                'inlined': not error and stored <= limits['stage_output_inline_bytes']}
    if metadata['inlined']:
        text = path.read_bytes().decode(errors='replace')
    else:
        preview = limits['stage_output_preview_bytes']
        with path.open('rb') as output:
            head = output.read(preview).decode(errors='replace')
            output.seek(max(0, stored-preview))
            tail = output.read(preview).decode(errors='replace')
        status = ('INCOMPLETE PREFIX: command sandbox destroyed; /tmp state lost.'
                  if error else 'COMPLETE ARTIFACT: output not inlined.')
        text = (f'{error or "ToolOutputStored"}: {status}\n'
                f'Stored bytes: {stored}; observed bytes: {observed}; SHA256 of stored bytes: {sha.hexdigest()}\n'
                f'Read-only artifact: {metadata["sandbox_path"]}. '
                'Read bounded excerpts with a normal shell command; do not print the whole file.\n'
                f'--- FIRST {preview} BYTES ---\n{head}\n'
                f'--- LAST {preview} STORED BYTES ---\n{tail}')
    return {'output': text, 'returncode': code, 'exception_info': error,
            'output_artifact': metadata}
