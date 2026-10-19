import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mini_swe_rsi.evaluation import grader


@pytest.mark.parametrize("operation", ["image", "inspect"])
def test_removed_and_unknown_operations_are_rejected_before_runtime_setup(
        tmp_path, monkeypatch, operation):
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps({"operation": operation}))
    monkeypatch.setattr(sys, "argv", ["grader.py", str(job_path)])

    def unexpected_setup(*args, **kwargs):
        raise AssertionError("runtime setup must not run for a rejected operation")

    monkeypatch.setattr(grader, "direct_assets", unexpected_setup)
    monkeypatch.setattr(grader.docker, "from_env", unexpected_setup)

    with pytest.raises(ValueError, match="Unsupported evaluator operation"):
        grader.main()


class _FakeImage:
    id = "sha256:frozen"
    attrs = {"Architecture": "amd64"}

    def __init__(self):
        self.tags = []

    def tag(self, name):
        self.tags.append(name)


class _Images:
    def __init__(self, image):
        self.image = image
        self.pulled = []
        self.lookups = []

    def get(self, name):
        self.lookups.append(name)
        raise grader.docker.errors.ImageNotFound(name)

    def pull(self, name, **kwargs):
        self.pulled.append((name, kwargs))
        return self.image


def test_frozen_image_restores_by_digest_and_retags_missing_mutable_label():
    image = _FakeImage()
    images = _Images(image)
    client = SimpleNamespace(images=images)
    spec = SimpleNamespace(instance_image_key="swebench/example:latest")
    frozen = {
        "image": "swebench/example:latest",
        "image_id": "sha256:frozen",
        "repo_digests": ["swebench/example@sha256:immutable"],
    }

    grader.frozen_image(client, spec, frozen)

    assert images.pulled == [("swebench/example@sha256:immutable", {"platform": "linux/amd64"})]
    assert image.tags == ["swebench/example:latest"]


@pytest.mark.parametrize("outcome,completed,resolved,reason", [
    ("empty", True, False, "empty_patch"),
    ("resolved", True, True, None),
    ("unresolved", True, False, None),
    ("patch_failure", True, False, "patch_apply_failed"),
    ("timeout_log", True, False, "tests_timeout"),
    ("timeout_output", True, False, "tests_timeout"),
    ("missing_report", False, False, None),
    *[(outcome, False, False, "invalid_report") for outcome in (
        "invalid_json", "invalid_encoding", "report_list", "missing_task", "task_null",
        "task_list", "missing_resolved", "resolved_null", "resolved_string",
        "resolved_number", "multiple_reports")],
])
def test_score_entrypoint_preserves_results_and_failure_classification(
        tmp_path, monkeypatch, outcome, completed, resolved, reason):
    # Execute the real grader entrypoint with a simulated official evaluator.
    modules = {}
    for name in ("mini_swe_rsi.evaluation.swebench", "mini_swe_rsi.evaluation.swebench.harness", "mini_swe_rsi.evaluation.swebench.harness.test_spec",
                 "mini_swe_rsi.evaluation.swebench.harness.test_spec.test_spec", "mini_swe_rsi.evaluation.swebench.harness.run_evaluation"):
        modules[name] = ModuleType(name)
        modules[name].__path__ = []
        monkeypatch.setitem(sys.modules, name, modules[name])
    root = tmp_path / "scoring"
    task = {"instance_id": "fixture"}
    job = {"operation": "score", "directory": str(root), "task": task,
           "arch": "x86_64", "image_namespace": "swebench", "instance_image_tag": "latest",
           "patch": "  \n" if outcome == "empty" else "model patch",
           "result_file": str(root / "result.json"), "run_id": "fixture-run",
           "frozen_image": {"image_id": "sha256:frozen"}}
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job))
    spec, client = object(), object()
    calls = {"spec": [], "assets": [], "frozen": [], "isolation": [], "run": []}
    boundaries = [{"network_mode": "none", "id": "fixture-container"}]

    def make_spec(row, **kwargs):
        calls["spec"].append((row, kwargs))
        return spec

    def run_instance(actual_spec, prediction, **kwargs):
        calls["run"].append((actual_spec, prediction, kwargs))
        logs = root / "logs/run_evaluation/fixture-run/task"
        logs.mkdir(parents=True)
        if outcome in ("resolved", "unresolved"):
            (logs / "report.json").write_text(json.dumps({"fixture": {"resolved": resolved}}))
        elif reason == "invalid_report":
            reports = {
                "report_list": [], "missing_task": {"other": {"resolved": True}},
                "task_null": {"fixture": None}, "task_list": {"fixture": []},
                "missing_resolved": {"fixture": {}},
                "resolved_null": {"fixture": {"resolved": None}},
                "resolved_string": {"fixture": {"resolved": "false"}},
                "resolved_number": {"fixture": {"resolved": 1}},
            }
            content = json.dumps(reports.get(outcome, {"fixture": {"resolved": True}}))
            (logs / "report.json").write_bytes(
                b"\xff" if outcome == "invalid_encoding" else
                ("{" if outcome == "invalid_json" else content).encode())
            if outcome == "multiple_reports":
                extra = logs / "extra"
                extra.mkdir()
                (extra / "report.json").write_text(content)
        elif outcome == "patch_failure":
            (logs / "run_instance.log").write_text(">>>>> Patch Apply Failed")
        elif outcome == "timeout_log":
            (logs / "run_instance.log").write_text("Test timed out after 300 seconds.")
        elif outcome == "timeout_output":
            (logs / "test_output.txt").write_text(">>>>> Tests Timed Out")
        else:
            (logs / "run_instance.log").write_text("Scoring infrastructure unavailable")

    modules["mini_swe_rsi.evaluation.swebench.harness.test_spec.test_spec"].make_test_spec = make_spec
    modules["mini_swe_rsi.evaluation.swebench.harness.run_evaluation"].run_instance = run_instance
    monkeypatch.setattr(grader, "direct_assets", lambda path: calls["assets"].append(path))
    monkeypatch.setattr(grader.docker, "from_env", lambda: client)
    monkeypatch.setattr(grader, "frozen_image", lambda *args: calls["frozen"].append(args))
    monkeypatch.setattr(grader, "isolate", lambda actual:
                        calls["isolation"].append(actual) or boundaries)
    monkeypatch.setattr(sys, "argv", ["grader.py", str(job_path)])
    monkeypatch.chdir(tmp_path)  # Restore cwd after the real entrypoint changes it.
    grader.main()

    result = json.loads(Path(job["result_file"]).read_text())
    assert result["completed"] is completed and result["resolved"] is resolved
    assert result.get("reason") == reason
    assert calls["frozen"] == [(client, spec, job["frozen_image"])]
    assert calls["assets"] == [root.parent.parent / "spec-assets"]
    assert calls["spec"] == [(task, {"namespace": "swebench", "arch": "x86_64",
        "base_image_tag": "mini-rsi-v4", "env_image_tag": "mini-rsi-v4",
        "instance_image_tag": "latest"})]
    if outcome == "empty":
        assert not calls["run"] and not calls["isolation"]
        assert result["container_boundaries"] == []
    else:
        assert calls["isolation"] == [client]
        assert calls["run"] == [(spec, {"instance_id": "fixture",
            "model_name_or_path": "mini-swe-rsi", "model_patch": "model patch"},
            {"rm_image": False, "force_rebuild": False, "client": client,
             "run_id": "fixture-run", "timeout": 300})]
        assert result["container_boundaries"] == boundaries
    if outcome == "missing_report":
        assert result["error"] == "Official evaluator produced no report"
    if reason == "invalid_report":
        assert result["error"].startswith("Invalid official evaluation report:")
