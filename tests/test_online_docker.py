"""Real-container online inheritance acceptance.

This test is opt-in because it starts the pinned runtime image and two isolated
improvement processes.  The model endpoint is a local deterministic HTTP
fixture; no ``.env`` value, credential, real model, or benchmark grader is
used.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

docker = pytest.importorskip("docker")

from mini_swe_rsi.broker import Broker
from mini_swe_rsi.context_budget import ContextBudget
from mini_swe_rsi.driver import Driver, initial_code
from mini_swe_rsi.core import Budget, read, tree_hash, write
from mini_swe_rsi.online import OnlineLoop, execution_hash


class FixtureModel:
    """A fake OpenAI text endpoint returning only a final mini action."""

    def __init__(self):
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                fixture.requests.append(payload)
                response = {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": payload.get("model", "fixture"),
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant",
                        "content": "THOUGHT: finish this deterministic fixture.\n"
                                   "```mswea_bash_command\n"
                                   "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
                                   "```"}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10,
                              "total_tokens": 30}}
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class OnlineWorkflowModel:
    """Deterministic text endpoint for the real online Docker loop.

    It emits only the normal mini bash action.  The workflow, relay, broker,
    candidate callback, and Docker processes remain production objects; this
    server supplies no host-side shortcut for any of those paths.
    """

    def __init__(self):
        self.requests = []
        self._lock = threading.Lock()
        self._turns = {}
        self.active = 0
        self.max_active = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                system = str((payload.get("messages") or [{}])[0].get("content", ""))
                joined = "\n".join(str(m.get("content", "")) for m in payload.get("messages", []))
                with fixture._lock:
                    fixture.active += 1
                    fixture.max_active = max(fixture.max_active, fixture.active)
                    fixture.requests.append(payload)
                try:
                    # A tiny delay makes an accidental concurrent model call
                    # observable instead of relying on scheduler timing.
                    time.sleep(0.01)
                    content = fixture.response(system, joined)
                    response = {
                        "id": "online-fixture-completion",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": payload.get("model", "fixture"),
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {
                            "role": "assistant", "content": content}}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10,
                                  "total_tokens": 30}}
                    data = json.dumps(response).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    with fixture._lock:
                        fixture.active -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    @staticmethod
    def _action(command):
        return "THOUGHT: deterministic online fixture.\n```mswea_bash_command\n" + command + "\n```"

    @staticmethod
    def _workflow(request):
        return "sica-workflow " + json.dumps(request, separators=(",", ":"))

    @staticmethod
    def _round(text):
        match = re.search(r"R\d{4}", text)
        return match.group(0) if match else None

    @staticmethod
    def _evidence(text):
        match = re.search(r"tasks/[A-Za-z0-9_.-]+/trace\.txt", text)
        return match.group(0) if match else "tasks/0001-t1/trace.txt"

    @staticmethod
    def _completion_tool(text):
        matches = re.findall(r"step_[a-f0-9]+_complete", text)
        if not matches:
            raise AssertionError("workflow completion tool was absent from model context")
        return matches[-1]

    def _role(self, system):
        if "expert software engineer" in system:
            return "coder"
        if "member of a Meta-Agent design review committee" in system:
            return "reviewer"
        if "expert archive analyst" in system:
            return "archive"
        if "very-competent problem solver" in system:
            return "problem_solver"
        return "orchestrator"

    def response(self, system, text):
        role = self._role(system)
        round_id = self._round(text)
        if role == "orchestrator" and round_id is None:
            # Real task execution and candidate execution use the benchmark
            # command.  Their grader is host-controlled by the test fixture.
            return self._action("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
        if role != "orchestrator":
            key = (role, round_id)
            turn = self._turns.get(key, 0)
            self._turns[key] = turn + 1
            if role == "coder" and turn == 0:
                marker = "DOCKER_GENERATION_H1" if round_id == "R0001" else "DOCKER_GENERATION_H2"
                command = ("sed -i '/^  instance_template:/i\\    " + marker + "' "
                           "/home/agent/workdir/minisweagent/config/mini_textbased.yaml && "
                           "cat > /home/agent/workdir/minisweagent/mechanism_fixture.py <<'PY'\n"
                           "from pathlib import Path\n"
                           "import sys\n"
                           "expected = '" + marker + "'\n"
                           "text = Path('minisweagent/config/mini_textbased.yaml').read_text()\n"
                           "raise SystemExit(0 if expected in text else 1)\n"
                           "PY")
                return self._action(command)
            return self._action(self._workflow({"op": "submit",
                                               "report": f"Complete {role} evidence for {round_id}"}))

        key = ("orchestrator", round_id)
        turn = self._turns.get(key, 0)
        self._turns[key] = turn + 1
        evidence = self._evidence(text)
        if turn == 0:
            command = self._workflow({"op": "invoke"})
        elif round_id == "R0003":
            # The third task is a real workflow run which reaches the normal
            # two-step decision and then skips all implementation stages.
            if turn in (1, 3):
                command = self._workflow({"op": "delegate", "role": "archive_explorer",
                                          "task": "report fixture evidence"})
            elif turn in (2, 4):
                command = self._workflow({"op": self._completion_tool(text), "success": True,
                                          "report": "fixture diagnostic report", "evidence_refs": [evidence]})
            elif turn == 5:
                command = self._workflow({"op": "diagnosis_decision", "decision": "skip",
                                          "reason": "No actionable improvement in fixture task",
                                          "failure_modes": [], "effective_mechanisms": ["fixture"],
                                          "improvement_opportunities": [], "evidence_refs": [evidence]})
            else:
                command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        else:
            operations = {
                1: {"op": "delegate", "role": "archive_explorer", "task": "report fixture failure evidence"},
                2: {"op": self._completion_tool(text), "success": True,
                    "report": "fixture failure report", "evidence_refs": [evidence]},
                3: {"op": "delegate", "role": "archive_explorer", "task": "report fixture effective evidence"},
                4: {"op": self._completion_tool(text), "success": True,
                    "report": "fixture effective mechanism report", "evidence_refs": [evidence]},
                5: {"op": "diagnosis_decision", "decision": "improve",
                    "reason": "Fixture supplies an actionable Harness improvement",
                    "failure_modes": ["fixture behavior"], "effective_mechanisms": ["fixture"],
                    "improvement_opportunities": ["marker in loaded system template"],
                    "evidence_refs": [evidence]},
                6: {"op": "committee", "context": "fixture public evidence",
                    "proposal": "Add the generation marker to the loaded system template."},
                # The host freezes the acceptance criterion at step 3.  The
                # mechanism plan belongs to that same declaration so step 5
                # cannot introduce a new test after the candidate is edited.
                7: {"op": self._completion_tool(text), "success": True,
                    "check_plan": ({"criterion": "current_task_failure_fixed"}
                                   if round_id == "R0001" else {
                                       "criterion": "mechanism_parent_failed_candidate_passed",
                                       "mechanism_plan": {
                                           "test_path": "minisweagent/mechanism_fixture.py",
                                           "args": [], "timeout_seconds": 60}})},
                8: {"op": "delegate", "role": "software_developer",
                    "task": "Edit the loaded system template with the generation marker and verify it."},
                9: {"op": self._completion_tool(text), "success": True},
                10: {"op": "candidate_check", "job_id": f"candidate:{round_id}:P{int(round_id[1:]):04d}",
                     "task_id": {"R0001": "t1", "R0002": "t2"}[round_id], "round_id": round_id,
                     "candidate": f"P{int(round_id[1:]):04d}",
                     "parent_version": f"H{int(round_id[1:]) - 1:04d}", "check_revision": "1"},
                11: {"op": self._completion_tool(text), "success": True},
                12: {"op": "delegate", "role": "general_problem_solver", "task": "Document the fixture change."},
                13: {"op": self._completion_tool(text), "success": True},
                14: {"op": "delegate", "role": "general_problem_solver", "task": "Record fixture history."},
                15: {"op": self._completion_tool(text), "success": True},
            }
            command = (self._workflow(operations[turn]) if turn in operations
                       else "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
        return self._action(command)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1",
                    reason="Explicit real Docker acceptance test")
def test_two_candidate_generations_load_in_distinct_real_processes(tmp_path, monkeypatch):
    """The second improver must execute H1's source after H0's round."""
    try:
        client = docker.from_env()
        client.images.get("mini-swe-rsi-runtime:0.1")
    except Exception as exc:
        pytest.skip(f"Docker runtime image unavailable: {type(exc).__name__}")

    endpoint = FixtureModel()
    monkeypatch.setattr("mini_swe_rsi.broker.secrets", lambda: {
        "OPENAI_BASE_URL": endpoint.url,
        "OPENAI_API_KEY": "fixture-only-key",
        "LOCAL_LLM_MODEL": "fixture-model"})
    root = tmp_path / "docker-run"
    budget = Budget(root)
    budget.enter("search")
    context = ContextBudget({"context_window": 32768, "max_output_tokens": 2048})
    broker = Broker(root, budget, context=context)
    broker.model = "fixture-model"
    # The fixture has no token tariff.  Mark the local fixture as a non-cloud
    # service so host accounting still executes instead of rejecting a call.
    broker.service_identity = {"ollama": {"cloud": False}}
    broker.pricing = {"input_per_million": 0.0, "cached_input_per_million": 0.0,
                      "output_per_million": 0.0}

    class FixtureData:
        def image(self, task_id):
            image = client.images.get("mini-swe-rsi-runtime:0.1")
            return {"image_id": image.id, "repo_digests": [],
                    "image": "mini-swe-rsi-runtime:0.1"}

    driver = None
    try:
        driver = Driver(root, FixtureData(), broker, budget)
        h0 = root / "versions/H0000/agent_code"
        initial_code(h0)
        h1 = root / "versions/H0001/agent_code"
        h2 = root / "versions/H0002/agent_code"
        shutil.copytree(h0, h1)
        shutil.copytree(h1, h2)
        # Make each selected source observably different.  The controller
        # still runs the real archived mini process and only the target copy is
        # writable, so a label cannot masquerade as inheritance.  Change the
        # loaded system template itself; the endpoint assertions below prove
        # that each selected generation reached a fresh process.
        for source, marker in ((h1, "DOCKER_GENERATION_H1"), (h2, "DOCKER_GENERATION_H2")):
            config_path = source / "minisweagent/config/mini_textbased.yaml"
            config = yaml.safe_load(config_path.read_text())
            config["agent"]["system_template"] += f"\n{marker}\n"
            config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        assert tree_hash(h0) != tree_hash(h1) != tree_hash(h2)
        prompt = root / "feedback.json"
        (root / "tasks/fixture").mkdir(parents=True)
        (root / "tasks/fixture/trace.txt").write_text("public fixture trace\n")
        write(prompt, {"task_id": "fixture-task", "round_id": "R0001",
                       "evidence_refs": ["tasks/fixture/trace.txt"]})

        hashes = []
        for index, source, target, version in ((0, h0, root / "rounds/R0001/working_candidate", "H0000"),
                                               (1, h1, root / "rounds/R0002/working_candidate", "H0001")):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            logs = root / "rounds" / f"R{index + 1:04d}"
            token = broker.session(f"online-R{index + 1:04d}", seconds=120, calls=8)
            try:
                status, _ = driver.controller(
                    source, logs,
                    ["improve", "--online", "--workdir", "/home/agent/workdir",
                     "--best-iter", str(index), "--current-iter", str(index),
                     "--parent-iter", str(index), "--candidate-id", f"P{index + 1:04d}",
                     "--task-id", "fixture-task", "--round-id", f"R{index + 1:04d}",
                     "--online-version", version, "--prompt-file", "/task.txt"],
                    token, target=target, prompt=prompt)
            finally:
                metrics = broker.finish(token)
            assert status == 0
            assert metrics["calls"] >= 1 and metrics["tokens"] >= metrics["calls"]
            assert (logs / "improvement_task.json").exists()
            assert (logs / "improvement_verification.json").read_text()
            hashes.append(tree_hash(source))

        events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
        launches = [event for event in events if event.get("kind") == "agent_launch"]
        assert len(launches) == 2
        assert [event["code_hash"] for event in launches] == hashes
        assert all(event["command"] == "improve" for event in launches)
        assert [event["improvement_task"]["parent"] for event in launches] == [0, 1]
        assert [event["improvement_task"]["version"] for event in launches] == ["H0000", "H0001"]
        # The endpoint saw two independent model sessions, and the next
        # improver's prompt names the evolved active version.
        assert len(endpoint.requests) == 2
        first_prompt = "\n".join(message["content"] for message in endpoint.requests[0]["messages"])
        second_prompt = "\n".join(message["content"] for message in endpoint.requests[1]["messages"])
        assert "active Harness version is H0000" in first_prompt
        assert "active Harness version is H0001" in second_prompt
        assert "DOCKER_GENERATION_H1" not in first_prompt
        assert "DOCKER_GENERATION_H1" in second_prompt
    finally:
        if driver is not None:
            driver.close()
        broker.close()
        endpoint.close()


def _build_online_fixture_image(client, tmp_path):
    # A pinned SWE-bench x86 image is used only as an inert git testbed.  The
    # score is mocked below, so no real benchmark task or grading data is read.
    tag = "swebench/sweb.eval.x86_64.sympy_1776_sympy-24102:latest"
    try:
        image = client.images.get(tag)
    except Exception as exc:
        pytest.skip(f"Pinned x86 fixture image unavailable: {type(exc).__name__}")
    if image.attrs.get("Architecture") != "amd64":
        pytest.skip("Pinned x86 fixture image is not amd64")
    return image, tag


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1",
                    reason="Explicit real Docker acceptance test")
def test_online_loop_docker_full_candidate_relay_and_skip_path(tmp_path, monkeypatch):
    """Run three real tasks: H0->H1->H2, then a two-step terminal skip.

    The only fake boundary is the model HTTP response and official score.  The
    improvement controller and candidate check each run in fresh containers;
    candidate_check reaches the host through the authenticated relay route.
    """
    try:
        client = docker.from_env()
        client.images.get("mini-swe-rsi-runtime:0.1")
    except Exception as exc:
        pytest.skip(f"Docker runtime image unavailable: {type(exc).__name__}")
    endpoint = OnlineWorkflowModel()
    fixture_image = None
    broker = None
    driver = None
    try:
        fixture_image, fixture_tag = _build_online_fixture_image(client, tmp_path)
        monkeypatch.setattr("mini_swe_rsi.broker.secrets", lambda: {
            "OPENAI_BASE_URL": endpoint.url,
            "OPENAI_API_KEY": "fixture-only-key",
            "LOCAL_LLM_MODEL": "fixture-model"})
        root = tmp_path / "online-loop"
        budget = Budget(root)
        budget.enter("search")
        context = ContextBudget({"context_window": 65536, "max_output_tokens": 4096})
        broker = Broker(root, budget, context=context)
        broker.model = "fixture-model"
        broker.service_identity = {"ollama": {"cloud": False}}
        broker.pricing = {"input_per_million": 0.0, "cached_input_per_million": 0.0,
                          "output_per_million": 0.0}

        class FixtureData:
            rows = {task: {"problem_statement": f"Deterministic fixture task {task}"}
                    for task in ("t1", "t2", "t3")}

            def __init__(self):
                self.image_calls = []

            def image(self, task_id):
                self.image_calls.append(task_id)
                return {"image_id": fixture_image.id, "repo_digests": [], "image": fixture_tag}

        data = FixtureData()
        driver = Driver(root, data, broker, budget)

        # Exercise the real Docker task/candidate containers while keeping the
        # official grader deterministic and local to this test.  Driver.score
        # receives a private grading directory for both parent and candidate;
        # its path is intentionally not a candidate/parent signal.  The call
        # ordinal is the fixture's explicit host-side score record instead.
        score_calls = {"t1": 0, "t2": 0, "t3": 0}

        def fixture_score(task_id, patch, directory):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            score_calls[task_id] += 1
            ordinal = score_calls[task_id]
            # t1 demonstrates a direct score improvement.  t2 has parent=1
            # and candidate=1: its declared mechanism criterion still has to
            # pass, while the separate acceptance test covers candidate=0
            # regression rejection.  Candidate checks use the same private
            # grading-root shape as parent checks, so ordinal state is the
            # only reliable distinction in this deterministic fixture.
            if task_id == "t1":
                resolved = 0.0 if ordinal == 1 else 1.0
            elif task_id == "t2":
                resolved = 1.0
            else:
                resolved = 0.0
            result = {"completed": True, "resolved": resolved,
                      "task_id": task_id, "patch_sha256": __import__("hashlib").sha256(
                          patch.encode()).hexdigest()}
            write(directory / "result.json", result)
            return result

        driver.score = fixture_score
        initial = root / "versions/H0000/agent_code"
        initial_code(initial)
        loop = OnlineLoop(root, ("t1", "t2", "t3"), driver=driver, broker=broker,
                          active_code=initial,
                          model_identity={"model": "fixture-model"},
                          service_identity={"endpoint": endpoint.url})
        assert loop.run() == {**loop.state, "status": "completed"}
        assert loop.active_version == "H0002"
        assert loop.state["next_task_index"] == 3
        r1_decision = read(root / "decisions/R0001.json")
        r2_decision = read(root / "decisions/R0002.json")
        assert r1_decision["acceptance"]["improvement_criterion"] == "current_task_failure_fixed"
        assert r2_decision["acceptance"]["improvement_criterion"] == "mechanism_parent_failed_candidate_passed"
        assert r2_decision["acceptance"]["parent_score"] == 1.0
        assert r2_decision["acceptance"]["candidate_score"] == 1.0
        r2_check = read(root / "rounds/R0002/candidate_check.json")["check"]
        assert r2_check["mechanism_check"]["parent_failed"] is True
        assert r2_check["mechanism_check"]["candidate_passed"] is True
        frozen_plan = read(root / "rounds/R0002/check_plan_frozen.json")
        assert frozen_plan["criterion"] == "mechanism_parent_failed_candidate_passed"
        assert frozen_plan["mechanism_plan"] == {
            "test_path": "minisweagent/mechanism_fixture.py",
            "args": [], "timeout_seconds": 60}
        declared_plan = read(root / "rounds/R0002/check_plan_declared.json")
        assert declared_plan["criterion"] == frozen_plan["criterion"]
        assert declared_plan["mechanism_plan"] == frozen_plan["mechanism_plan"]
        assert "mechanism_plan" not in read(root / "rounds/R0002/candidate_check_result.json")["request"]

        # Task cursor and image selection cover all three tasks; candidate
        # checks add two independent executions of t1/t2 through Driver.
        assert data.image_calls.count("t1") >= 2
        assert data.image_calls.count("t2") >= 2
        assert data.image_calls.count("t3") == 1
        assert (root / "decisions/R0003.json").read_text()
        skipped = read(root / "decisions/R0003.json")
        assert skipped["decision"] == "skipped_no_actionable_improvement"
        assert skipped["completed_steps"] == 2
        assert skipped["skipped_steps"] == [3, 4, 5, 6, 7]
        assert not (root / "rounds/R0003/candidate_check.json").exists()

        # The coder changed the actual loaded system template in each source;
        # identity and execution hashes prove these are real inherited sources.
        h0 = root / "versions/H0000/agent_code"
        h1 = root / "versions/H0001/agent_code"
        h2 = root / "versions/H0002/agent_code"
        config1 = (h1 / "minisweagent/config/mini_textbased.yaml").read_text()
        config2 = (h2 / "minisweagent/config/mini_textbased.yaml").read_text()
        assert "DOCKER_GENERATION_H1" in config1
        assert "DOCKER_GENERATION_H2" in config2
        assert execution_hash(h0) != execution_hash(h1) != execution_hash(h2)

        events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
        model_events = [event for event in events if event.get("kind") == "model_call"]
        assert len(model_events) == len(endpoint.requests)
        # Usage is returned in the completion response, not in the request
        # payload captured by this endpoint.  Every response above declares
        # exactly 30 total tokens, so broker metering must match that ledger.
        assert sum(event["usage"]["total_tokens"] for event in model_events) == 30 * len(endpoint.requests)
        assert all(event.get("model") == "fixture-model" for event in model_events)
        assert endpoint.max_active == 1
        candidate_events = [event for event in events if event.get("kind") == "candidate_check_complete"]
        assert [event["job_id"] for event in candidate_events] == [
            "candidate:R0001:P0001", "candidate:R0002:P0002"]
        assert all((root / f"rounds/R000{i}/candidate_check_result.json").exists() for i in (1, 2))

        improve_launches = [event for event in events if event.get("kind") == "agent_launch"
                            and event.get("command") == "improve"]
        assert len(improve_launches) == 3
        assert [event["improvement_task"]["version"] for event in improve_launches] == [
            "H0000", "H0001", "H0002"]
        for event in improve_launches:
            mounts = {mount["destination"]: mount["read_write"] for mount in event["mounts"]}
            assert mounts["/home/agent/agent_code"] is False
            assert mounts["/home/agent/workdir"] is True
        benchmark_launches = [event for event in events
                              if event.get("kind") == "agent_launch"
                              and event.get("command") == "benchmark"]
        # t1/t2 each run once as the parent and once through the candidate
        # relay; t3 runs once before its terminal skip.  Every process sees a
        # read-only selected Harness source.
        assert len(benchmark_launches) >= 5
        for event in benchmark_launches:
            mounts = {mount["destination"]: mount["read_write"] for mount in event["mounts"]}
            assert mounts["/home/agent/agent_code"] is False

        # The actual fresh model processes saw the inherited template markers.
        improve_payloads = []
        for payload in endpoint.requests:
            text = "\n".join(str(m.get("content", "")) for m in payload.get("messages", []))
            if "ONLINE SINGLE-TASK MODE" in text:
                improve_payloads.append(text)
        assert any("active Harness version is H0000" in text and "DOCKER_GENERATION_H1" not in text
                   for text in improve_payloads)
        assert any("active Harness version is H0001" in text and "DOCKER_GENERATION_H1" in text
                   for text in improve_payloads)
        assert any("active Harness version is H0002" in text and "DOCKER_GENERATION_H2" in text
                   for text in improve_payloads)
    finally:
        if driver is not None:
            driver.close()
        if broker is not None:
            broker.close()
        endpoint.close()
