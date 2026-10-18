"""Reused scoring asset from mini-swe-rsi-old; no evolution/controller code reused.

Changes: frozen image identity, empty patch handling and official timeout classification.
"""
import json
import hashlib
import os
import re
import sys
from pathlib import Path

import docker
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def grading_failure_reason(text):
    # Both forms are emitted by the pinned official evaluator.
    if '>>>>> Patch Apply Failed' in text:
        return 'patch_apply_failed'
    if '>>>>> Tests Timed Out' in text or 'Test timed out after' in text:
        return 'tests_timeout'
    return None


def direct_assets(cache):
    """Only the trusted host fetches recipe files; preserve verified TLS and bytes."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])))
    def get(url, **kwargs):
        frozen = re.fullmatch(r"https://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/[^?#]+", url)
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if frozen and path.exists():
            data = json.loads(path.read_text())
            content = bytes.fromhex(data["content"])
            if data["url"] != url or hashlib.sha256(content).hexdigest() != data["sha256"]:
                raise ValueError("Fixed recipe cache content changed")
            response = requests.Response()
            response.status_code, response._content = data["status"], content
            response.encoding, response.url = data["encoding"], url
            return response
        # No caller can opt out of TLS verification or inject a proxy.
        kwargs.update(timeout=30, verify=True, proxies={})
        response = session.get(url, **kwargs)
        if frozen and response.status_code in (200, 404):
            data = {"url": url, "status": response.status_code, "encoding": response.encoding,
                    "content": response.content.hex(), "sha256": hashlib.sha256(response.content).hexdigest()}
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(data))
            temp.replace(path)
        return response
    requests.get = get
    return get


def isolate(client):
    # DockerClient.containers returns a new collection on every access. Hook the
    # persistent API client instead, and replace the entire host configuration.
    create = client.api.create_container
    boundaries = []
    def isolated_create(*args, **kwargs):
        kwargs.update(network_disabled=True, networking_config=None,
                      host_config=client.api.create_host_config(network_mode="none", cap_drop=["ALL"],
                      security_opt=["no-new-privileges"], mem_limit="2g", nano_cpus=1000000000, pids_limit=256))
        result = create(*args, **kwargs)
        actual = client.api.inspect_container(result["Id"])
        host = actual["HostConfig"]
        if host["NetworkMode"] != "none" or host.get("Privileged") or host.get("Binds"):
            client.api.remove_container(result["Id"], force=True)
            raise RuntimeError("Official scoring container boundary was not enforced")
        boundaries.append({"id": result["Id"], "network_mode": host["NetworkMode"],
                           "memory": host["Memory"], "nano_cpus": host["NanoCpus"], "cap_drop": host["CapDrop"]})
        return result
    client.api.create_container = isolated_create
    return boundaries


def frozen_image(client, spec, frozen):
    if spec.instance_image_key != frozen['image']:
        raise RuntimeError('Official evaluator image recipe differs from frozen asset')
    try:
        image = client.images.get(frozen['image_id'])
    except docker.errors.ImageNotFound:
        image = client.images.pull(frozen['repo_digests'][0], platform='linux/amd64')
    if image.id != frozen['image_id'] or image.attrs['Architecture'] != 'amd64':
        raise RuntimeError('Frozen image identity mismatch')
    try:
        canonical = client.images.get(spec.instance_image_key)
        if canonical.id != image.id:
            raise RuntimeError('Mutable image tag changed; refusing scoring')
    except docker.errors.ImageNotFound:
        image.tag(spec.instance_image_key)


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    operation = job.get("operation")
    if operation != "score":
        raise ValueError(f"Unsupported evaluator operation: {operation!r}; only 'score' is supported")

    from swebench.harness.test_spec.test_spec import make_test_spec
    from swebench.harness.run_evaluation import run_instance
    root = Path(job["directory"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    direct_assets(root.parent.parent / "spec-assets")
    row = job["task"]
    spec = make_test_spec(row, namespace=job.get("image_namespace"), arch=job["arch"],
                          base_image_tag="mini-rsi-v4", env_image_tag="mini-rsi-v4",
                          instance_image_tag=job.get("instance_image_tag", "mini-rsi-v4"))
    client = docker.from_env()
    if 'frozen_image' in job:
        frozen_image(client, spec, job['frozen_image'])
    if not job['patch'].strip():
        Path(job['result_file']).write_text(json.dumps({'completed': True, 'resolved': False,
            'reason': 'empty_patch', 'container_boundaries': []}))
        return
    # Keep official patch application, tests and grading; restrict only container creation.
    boundaries = isolate(client)
    # The canonical evaluation runs fresh containers, independently of the solver container.
    result = run_instance(spec, {"instance_id": row["instance_id"],
                          "model_name_or_path": "mini-swe-rsi", "model_patch": job["patch"]},
                          rm_image=False, force_rebuild=False, client=client,
                          run_id=job["run_id"], timeout=job.get("timeout", 300))
    reports = list((root / "logs/run_evaluation" / job["run_id"]).rglob("report.json"))
    if not reports:
        logs = list((root / "logs/run_evaluation" / job["run_id"]).rglob("run_instance.log"))
        text = logs[0].read_text() if logs else ""
        # The constant marker is written to test_output.txt, whereas the
        # evaluator's exception text is written to run_instance.log.
        outputs = list((root / 'logs/run_evaluation' / job['run_id']).rglob('test_output.txt'))
        text += '\n' + '\n'.join(p.read_text(errors='replace') for p in outputs)
        reason = grading_failure_reason(text)
        result = {"completed": reason is not None, "resolved": False, "reason": reason,
                  "error": None if reason else "Official evaluator produced no report"}
    else:
        report = json.loads(reports[0].read_text())
        result = {"completed": True, "resolved": bool(report.get(row["instance_id"], {}).get("resolved", False))}
    result["container_boundaries"] = boundaries
    Path(job["result_file"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
