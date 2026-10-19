"""Independent acceptance checks for the online evolution contract.

The small driver below is deliberately limited to host bookkeeping.  The
Docker test in ``test_online_docker.py`` exercises the real Driver/controller
path; these tests focus on persistence, phase custody, and broker semantics.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mini_swe_rsi.context_budget import ContextBudget
from mini_swe_rsi.core import attempt_result_paths, read, tree_hash, write
from mini_swe_rsi.core import execution_hash as driver_execution_hash
from mini_swe_rsi.online import OnlineError, OnlineLoop, execution_hash
from mini_swe_rsi.driver import initial_code
from mini_swe_rsi.broker import Broker, ModelError
from mini_swe_rsi.core import ROOT
from tests.event_helpers import broker_events


_WORKFLOW_SPEC = importlib.util.spec_from_file_location("online_acceptance_workflow",
                                                        ROOT / "src/mini_swe_rsi/workflow/workflow.py")
_WORKFLOW = importlib.util.module_from_spec(_WORKFLOW_SPEC)
_WORKFLOW_SPEC.loader.exec_module(_WORKFLOW)


class FakeBroker:
    """Host-only broker facade used for online state tests."""

    def __init__(self):
        self.sessions = []
        self.finished = []
        self.candidate_jobs = {}

    def budget_limits(self):
        return {"seconds": 600, "calls": 50, "main_iterations": 50,
                "role_iterations": {"archive_explorer": 50, "reviewer": 50,
                                     "software_developer": 50, "general_problem_solver": 50}}

    def session(self, label, **kwargs):
        token = f"token-{len(self.sessions)}"
        self.sessions.append((token, label, kwargs))
        return token

    def finish(self, token):
        self.finished.append(token)
        # Keep the shape of Broker.finish, including independently measured
        # stage usage, so the loop cannot silently discard diagnostics usage.
        return {"label": token, "calls": 4, "tokens": 80, "prompt_tokens": 60,
                "completion_tokens": 20, "cached_tokens": 0,
                "unmetered_calls": 0}

    def register_candidate_job(self, job_id, callback, metadata=None):
        if job_id in self.candidate_jobs and self.candidate_jobs[job_id]["metadata"] != (metadata or {}):
            raise AssertionError("candidate job identity changed")
        self.candidate_jobs.setdefault(job_id, {"callback": callback, "metadata": metadata or {},
                                       "calls": 0, "result": None})

    def candidate_check(self, token, payload):
        job = self.candidate_jobs[payload["job_id"]]
        job["calls"] += 1
        if job["result"] is None:
            job["result"] = job["callback"](payload)
        return job["result"]


class FakeDriver:
    """A deterministic host driver for testing OnlineLoop's state machine."""

    def __init__(self, modes=("skip",), scores=None):
        self.modes = list(modes)
        self.scores = scores or {}
        self.task_calls = []
        self.controller_calls = []
        self.check_calls = []
        self.broker = None

    def task_retry(self, code, task_id, output):
        output = Path(output)
        self.task_calls.append((str(code), task_id, str(output)))
        output.mkdir(parents=True, exist_ok=True)
        attempt = output / "attempt_0"
        attempt.mkdir(parents=True, exist_ok=True)
        score = float(self.scores.get(task_id, 0))
        metrics = {"calls": 3, "tokens": 30, "prompt_tokens": 20,
                   "completion_tokens": 10, "cached_tokens": 0,
                   "unmetered_calls": 0}
        result = {"exit_status": "Submitted", "submission": "fixture"}
        record = {"problem_id": task_id, "score": score, "tokens_used": 30,
                  "wall_time": 0.01}
        execution = {"result": result, "metrics": metrics,
                     "code_hash": tree_hash(code)}
        write(attempt / "task_execution.json", execution)
        write(attempt / "host_result.json", {"result": result, "metrics": metrics,
                                              "record": record, "grade": {"resolved": score},
                                              "code_hash": tree_hash(code)})
        # The host writes the active version after the driver returns.  The
        # fixture leaves it unset so it cannot accidentally claim H0 when the
        # second generation executes the next task.
        for name, content in (("trace.txt", "public fixture trace\n"),
                              ("patch.diff", ""), ("answer.txt", "fixture\n")):
            (attempt / name).write_text(content)
        write(attempt / "activation.json", {"command": "benchmark", "fixture": True})
        root = output.parents[1]
        write(root / "private/task_grading" / output.relative_to(root) / "attempts.json", [])
        return record

    def controller(self, code, logs, command_args, token, *, target=None, prompt=None, **_):
        index = len(self.controller_calls)
        mode = self.modes[min(index, len(self.modes) - 1)]
        self.controller_calls.append((str(code), str(logs), tuple(command_args), str(target)))
        logs = Path(logs)
        logs.mkdir(parents=True, exist_ok=True)
        feedback = read(prompt) if prompt and Path(prompt).exists() else {}
        feedback_refs = feedback.get("evidence_refs", [])
        feedback_task = feedback.get("task_id", "fixture")
        version = Path(code).parents[0].name
        reports = {"1": {"report": "fixture failure report", "evidence_refs": feedback_refs,
                          "identity": "001_archive_explorer"},
                   "2": {"report": "fixture effective mechanism report", "evidence_refs": feedback_refs,
                          "identity": "002_archive_explorer"}}
        if mode == "skip":
            diagnosis = {"task_id": feedback_task,
                         "round_id": f"R{index + 1:04d}", "decision": "skip",
                         "executor": version, "proposer": version, "parent": version,
                         "reason": "No actionable improvement", "evidence_refs": feedback_refs,
                         "reports": reports}
            write(logs / "diagnosis.json", diagnosis)
            write(logs / "workflow_state.json", {
                "status": "skipped_no_actionable_improvement", "completed_steps": 2,
                "active": None, "skipped_steps": [3, 4, 5, 6, 7]})
        else:
            target = Path(target)
            # This is a real source change in the candidate tree, so acceptance
            # cannot pass merely because a version label changed.
            marker = target / "minisweagent" / "online_fixture_marker.py"
            marker.write_text(f"MARKER = {index!r}\n")
            diagnosis = {"task_id": feedback_task, "round_id": f"R{index + 1:04d}",
                         "executor": version, "proposer": version, "parent": version,
                         "decision": "improve", "reason": "Fixture evidence",
                         "evidence_refs": feedback_refs, "reports": reports}
            write(logs / "diagnosis.json", diagnosis)
            # The real workflow freezes this declaration at step 3.  Keep the
            # host-only fixture on the same durable contract so strict online
            # callback validation exercises the same path.
            write(logs / "check_plan_declared.json",
                  {"criterion": "current_task_failure_fixed"})
            write(logs / "workflow_state.json", {"status": "improving",
                                                   "completed_steps": 4,
                                                   "active": {"index": 4}})
            if self.broker is not None:
                round_id = command_args[command_args.index("--round-id") + 1]
                proposal_id = command_args[command_args.index("--candidate-id") + 1]
                self.broker.candidate_check(token, {
                    "job_id": f"candidate:{round_id}:{proposal_id}",
                    "task_id": feedback_task, "round_id": round_id,
                    "candidate": proposal_id, "parent_version": version,
                    "stage": "candidate_check", "check_revision": "1"})
            write(logs / "workflow_state.json", {"status": "completed",
                                                   "completed_steps": 7,
                                                   "active": None})
        # Strict host validation requires durable evidence that both diagnosis
        # reports came from successful archive_explorer calls at their bound
        # diagnostic steps.  The real Docker path gets these events from
        # Workflow; this facade records the same host event shape.
        (logs / "workflow_events.jsonl").write_text(
            json.dumps({"kind": "role_end", "role": "archive_explorer", "step_index": 0,
                        "identity": "001_archive_explorer",
                        "result": {"exit_status": "Submitted", "submission": "fixture archive"}}) + "\n"
            + json.dumps({"kind": "role_end", "role": "archive_explorer", "step_index": 1,
                          "identity": "002_archive_explorer",
                          "result": {"exit_status": "Submitted", "submission": "fixture archive"}}) + "\n")
        write(logs / "result.json", {"exit_status": "Submitted", "submission": "fixture"})
        return 0, "fixture"

    def candidate_check(self, candidate, task_id, output, *, job_id=None,
                        parent_hash, check_revision="1", parent_code=None,
                        mechanism_plan=None):
        self.check_calls.append((str(candidate), task_id, str(output), job_id))
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        score = 1.0 if (Path(candidate) / "minisweagent" / "online_fixture_marker.py").exists() else 0.0
        record = {"problem_id": task_id, "score": score, "tokens_used": 12,
                  "wall_time": 0.01}
        attempt = output / "attempt_0"
        write(attempt / "host_result.json", {"record": record,
                                              "metrics": {"calls": 1, "tokens": 12},
                                              "result": {"exit_status": "Submitted"},
                                              "code_hash": tree_hash(candidate)})
        write(attempt / "task_execution.json", {"code_hash": tree_hash(candidate)})
        write(attempt / "activation.json", {"command": "benchmark"})
        root = output.parents[2]
        write(root / "private/candidate_checks" / (str(job_id).replace('/', '_') + '.json'),
              {"job_id": job_id, "task_id": task_id,
               "check_revision": str(check_revision),
               "candidate_hash": tree_hash(candidate),
               "execution_hash": execution_hash(candidate),
               "parent_hash": parent_hash,
               "record": record})
        return {"job_id": job_id, "task_id": task_id, "check_revision": str(check_revision),
                "record": record, "metrics": {"calls": 1, "tokens": 12},
                "execution_status": "Submitted"}


@pytest.fixture
def initial_tree(tmp_path):
    code = tmp_path / "initial"
    initial_code(code)
    return code


def make_loop(tmp_path, initial_tree, driver, broker=None, tasks=("t1", "t2")):
    broker = broker or FakeBroker()
    driver.broker = broker
    return OnlineLoop(tmp_path / "run", tasks, driver=driver,
                      broker=broker, active_code=initial_tree,
                      model_identity={"model": "fixture"}, service_identity={"endpoint": "fixture"})


def test_two_steps_skip_is_terminal_and_does_not_start_step_three(initial_tree, tmp_path):
    driver = FakeDriver(("skip",))
    broker = FakeBroker()
    loop = make_loop(tmp_path, initial_tree, driver, broker)

    result = loop.run_once()

    assert result == "H0000"
    assert loop.active_version == "H0000"
    assert loop.state["next_task_index"] == 1
    assert loop.state["next_proposal"] == 1
    assert len(driver.controller_calls) == 1
    assert driver.check_calls == []
    decision = read(loop.root / "decisions/R0001.json")
    assert decision["decision"] == "skipped_no_actionable_improvement"
    assert decision["completed_steps"] == 2
    assert decision["active"] is None
    assert decision["skipped_steps"] == [3, 4, 5, 6, 7]

    # A valid skip must survive the finalizer and must not be rewritten as
    # incomplete merely because no seventh step ran.
    workflow_root = tmp_path / "workflow"
    (workflow_root / "tasks/t1").mkdir(parents=True)
    (workflow_root / "tasks/t1/trace.txt").write_text("public fixture trace\n")
    flow = _WORKFLOW.Workflow(workflow_root, lambda *_: {
                                    "exit_status": "Submitted",
                                    "submission": "fixture archive report"},
                                    feedback={"task_id": "t1",
                                    "round_id": "R0001",
                                    "evidence_refs": ["tasks/t1/trace.txt"]})
    flow.dispatch({"op": "invoke"})
    for _ in range(2):
        flow.call_role("archive_explorer", "fixture archive evidence")
        flow.dispatch({"op": flow.current()["completion_tool"], "success": True,
                       "report": "fixture diagnostic report",
                       "evidence_refs": ["tasks/t1/trace.txt"]})
    flow.dispatch({"op": "diagnosis_decision", "decision": "skip",
                   "reason": "No actionable improvement",
                   "evidence_refs": ["tasks/t1/trace.txt"]})
    flow.finish({"exit_status": "Submitted"})
    assert flow.status == "skipped_no_actionable_improvement"
    assert read(tmp_path / "workflow/workflow_state.json")["status"] == flow.status


def test_two_generations_are_real_source_changes_and_next_round_uses_new_parent(initial_tree, tmp_path):
    driver = FakeDriver(("accept", "accept"))
    broker = FakeBroker()
    loop = make_loop(tmp_path, initial_tree, driver, broker, tasks=("t1", "t2", "t3"))

    assert loop.run_once() == "H0001"
    first_hash = execution_hash(loop.active_code)
    assert loop.run_once() == "H0002"
    second_hash = execution_hash(loop.active_code)
    assert first_hash != execution_hash(initial_tree)
    assert second_hash != first_hash
    assert loop.state["next_task_index"] == 2
    assert [entry[1] for entry in driver.task_calls] == ["t1", "t2"]
    # Both improvement rounds must be bound to the active version at their
    # start; no H0 fallback is acceptable after the first takeover.
    assert driver.controller_calls[0][0].endswith("H0000/agent_code")
    assert driver.controller_calls[1][0].endswith("H0001/agent_code")
    # The host checks an immutable snapshot, then atomically installs the
    # candidate source.  Check identity must still advance with the proposal.
    assert driver.check_calls[0][3] == "candidate:R0001:P0001"
    assert driver.check_calls[1][3] == "candidate:R0002:P0002"
    assert Path(driver.check_calls[0][0], "minisweagent/online_fixture_marker.py").read_text() == "MARKER = 0\n"
    assert Path(driver.check_calls[1][0], "minisweagent/online_fixture_marker.py").read_text() == "MARKER = 1\n"


def test_candidate_check_relay_is_idempotent_concurrent_and_preserves_metrics(tmp_path):
    # Exercise the actual authenticated Broker HTTP handler, not a mocked
    # OnlineLoop method.  Two requests race at the same job id; one callback
    # and one immutable result are required.
    monkey = pytest.MonkeyPatch()
    monkey.setattr("mini_swe_rsi.broker.secrets", lambda: {
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "fixture",
        "LLM_MODEL_NAME": "fixture"})
    root = tmp_path / "broker"
    broker = Broker(root, context=ContextBudget({"context_window": 32768,
                                                 "max_output_tokens": 2048}))
    broker.model = "fixture"
    broker.service_identity = {"ollama": {"cloud": False}}
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def callback(payload):
        assert payload['job_id'] == job_id
        calls.append(time.time())
        entered.set()
        assert release.wait(2)
        return {"metrics": {"calls": 1, "tokens": 11}, "record": {"score": 1}}

    job_id = "candidate:R0001:P0001"
    token1 = broker.session("candidate-1", seconds=30, calls=0)
    metadata = {"job_id": job_id, "task_id": "t1", "round_id": "R0001",
                "candidate": "P0001", "parent_version": "H0000",
                "check_revision": "1", "stage": "candidate_check",
                "agent": "candidate-main", "role": "orchestrator",
                "session_token_sha256": hashlib.sha256(token1.encode()).hexdigest()}
    public_payload = {key: metadata[key] for key in
                      ("job_id", "task_id", "round_id", "candidate", "parent_version",
                       "check_revision", "stage")}
    broker.register_candidate_job(job_id, callback, metadata)
    with pytest.raises(ValueError, match="identity changed"):
        broker.register_candidate_job(job_id, callback,
                                      {**metadata, "task_id": "other-task"})
    token2 = broker.session("candidate-2", seconds=30, calls=0)
    with pytest.raises(ModelError, match="Unknown candidate check job"):
        broker.candidate_check(token1,
                               {**public_payload, "job_id": "candidate:R0001:P9999"},
                               agent="candidate-main", role="orchestrator")
    with pytest.raises(ModelError, match="identity differs"):
        broker.candidate_check(token1, {**public_payload, "task_id": "other-task"},
                               agent="candidate-main", role="orchestrator")
    results = []
    errors = []

    def request(token):
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{broker.port}/candidate_check",
                data=json.dumps(public_payload).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + token,
                         "X-RSI-Agent": "candidate-main",
                         "X-RSI-Role": "orchestrator"})
            with urllib.request.urlopen(request, timeout=3) as response:
                results.append(json.load(response))
        except Exception as exc:  # surfaced below for a useful assertion
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    errors.append(json.load(exc).get("error", {}))
                finally:
                    exc.close()
            else:
                errors.append(exc)

    first = threading.Thread(target=request, args=(token1,))
    second = threading.Thread(target=request, args=(token1,))
    first.start()
    assert entered.wait(1)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(2)
    second.join(2)
    try:
        assert not first.is_alive() and not second.is_alive(), "candidate check deadlocked"
        # The broker may reject a concurrent duplicate while the first job is
        # running; it must then return the immutable result on a retry.  A
        # duplicate callback or a blocked thread is the defect this test is
        # designed to catch.
        assert len(calls) == 1, "callback ran more than once for an immutable job"
        assert not first.is_alive() and not second.is_alive()
        result = broker.candidate_check(token1, public_payload,
                                        agent="candidate-main", role="orchestrator")
        assert result["metrics"] == {"calls": 1, "tokens": 11}
        persisted = read(root / "candidate_checks" / (job_id.replace("/", "_") + ".json"))
        assert persisted["status"] == "completed"
        assert persisted["result"]["metrics"] == result["metrics"]
        assert not errors or all("already running" in str(error) for error in errors)
        bad_auth = urllib.request.Request(
            f"http://127.0.0.1:{broker.port}/candidate_check",
            data=json.dumps(public_payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer invalid-token",
                     "X-RSI-Agent": "candidate-main",
                     "X-RSI-Role": "orchestrator"})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(bad_auth, timeout=3)
        assert denied.value.code in (401, 403)
        with pytest.raises(ModelError, match="execution identity"):
            broker.candidate_check(token1, public_payload, agent="other-agent", role="orchestrator")
        restarted = Broker(root, context=ContextBudget({"context_window": 32768,
                                                        "max_output_tokens": 2048}))
        restarted.model = "fixture"
        restarted.service_identity = {"ollama": {"cloud": False}}
        restarted.register_candidate_job(job_id, lambda: pytest.fail("replayed callback"), metadata)
        assert restarted.candidate_jobs[job_id]["result"] == result
        token3 = restarted.session("candidate-restarted", seconds=30, calls=0)
        with pytest.raises(ModelError, match="session differs"):
            restarted.candidate_check(token3, public_payload,
                                      agent="candidate-main", role="orchestrator")
        restarted.finish(token3)
        restarted.close()
        assert any(e.get("kind") == "candidate_check_complete" and
                   e.get("job_id") == job_id for e in broker_events(broker))
    finally:
        broker.finish(token1)
        broker.finish(token2)
        broker.close()
        monkey.undo()


def test_online_phase_permissions_and_evidence_are_host_checked(initial_tree, tmp_path):
    (tmp_path / "workflow/tasks/t1").mkdir(parents=True)
    (tmp_path / "workflow/tasks/t1/trace.txt").write_text("public fixture trace\n")
    flow = _WORKFLOW.Workflow(tmp_path / "workflow", lambda *_: {"exit_status": "Submitted",
                                                                  "submission": "fixture"},
                                    feedback={"task_id": "t1", "round_id": "R0001",
                                    "evidence_refs": ["tasks/t1/trace.txt"]})
    invoked = flow.dispatch({"op": "invoke"})
    first = invoked["completion_tool"]
    flow.call_role("archive_explorer", "fixture archive evidence")
    flow.dispatch({"op": first, "success": True, "report": "fixture failure report",
                   "evidence_refs": ["tasks/t1/trace.txt"]})
    second = flow.current()["completion_tool"]
    flow.call_role("archive_explorer", "fixture archive evidence")
    flow.dispatch({"op": second, "success": True, "report": "fixture evidence",
                   "evidence_refs": ["tasks/t1/trace.txt"]})
    third = flow.current()["completion_tool"]
    assert flow.status == "diagnosis_pending"
    with pytest.raises(ValueError):
        flow.dispatch({"op": "committee", "context": "x", "proposal": "x"})
    with pytest.raises(ValueError):
        flow.dispatch({"op": "delegate", "role": "software_developer", "task": "x"})
    with pytest.raises(ValueError):
        flow.dispatch({"op": "delegate", "role": "general_problem_solver", "task": "x"})
    with pytest.raises(ValueError):
        flow.dispatch({"op": "candidate_check", "job_id": "P0001"})
    with pytest.raises(ValueError, match="diagnosis decision"):
        flow.dispatch({"op": third, "success": True})
    with pytest.raises(ValueError, match="evidence_refs"):
        flow.dispatch({"op": "diagnosis_decision", "decision": "skip",
                       "reason": "bad ref", "evidence_refs": ["private/grader.json"]})
    with pytest.raises(ValueError, match="only to an online coordinator"):
        flow.dispatch({"op": "diagnosis_decision", "decision": "skip",
                       "reason": "x", "evidence_refs": []}, role="software_developer")
    assert flow.status == "diagnosis_pending"

    # A feedback package without public evidence cannot be turned into a
    # normal skip; this must remain distinct from a valid no-actionable-change
    # diagnosis.
    no_evidence = _WORKFLOW.Workflow(tmp_path / "no-evidence", lambda *_: {
                                         "exit_status": "Submitted",
                                         "submission": "fixture archive report"},
                                     feedback={"task_id": "t1",
                                     "round_id": "R0001", "evidence_refs": []})
    no_evidence.dispatch({"op": "invoke"})
    no_evidence.call_role("archive_explorer", "fixture archive evidence")
    with pytest.raises(ValueError, match="evidence"):
        no_evidence.dispatch({"op": no_evidence.current()["completion_tool"],
                              "success": True, "report": "missing public refs",
                              "evidence_refs": []})

    candidate_requests = []
    improve = _WORKFLOW.Workflow(tmp_path / "improve", lambda *_: {
                                 "exit_status": "Submitted",
                                 "submission": "fixture archive report"}, feedback={"task_id": "t1", "round_id": "R0001",
                                           "evidence_refs": ["tasks/t1/trace.txt"]},
                                 candidate_check=lambda request: (candidate_requests.append(request)
                                                                  or {"metrics": {"calls": 1,
                                                                                   "tokens": 12},
                                                                      "record": {"score": 1}}))
    improve.dispatch({"op": "invoke"})
    for _ in range(2):
        improve.call_role("archive_explorer", "fixture archive evidence")
        improve.dispatch({"op": improve.current()["completion_tool"], "success": True,
                          "report": "fixture evidence",
                          "evidence_refs": ["tasks/t1/trace.txt"]})
    opened = improve.dispatch({"op": "diagnosis_decision", "decision": "improve",
                               "reason": "evidence supports a change",
                               "evidence_refs": ["tasks/t1/trace.txt"],
                               "improvement_opportunities": ["fixture"]})
    assert improve.status == "improving" and opened["index"] == 2
    improve.dispatch({"op": opened["completion_tool"], "success": True,
                      "check_plan": {"criterion": "current_task_failure_fixed"}})
    assert improve.completed_steps == 3 and improve.current()["index"] == 3
    declared_plan = read(tmp_path / "improve/check_plan_declared.json")
    assert declared_plan["criterion"] == "current_task_failure_fixed"
    improve.dispatch({"op": improve.current()["completion_tool"], "success": True})
    assert improve.current()["index"] == 4 and improve.completed_steps == 4
    check_result = improve.dispatch({"op": "candidate_check", "job_id": "candidate:R0001:P0001",
                                     "task_id": "t1", "round_id": "R0001",
                                     "candidate": "P0001", "parent_version": "H0000",
                                     "check_revision": "1"})
    assert check_result["result"]["metrics"] == {"calls": 1, "tokens": 12}
    assert candidate_requests == [{"job_id": "candidate:R0001:P0001", "task_id": "t1",
                                   "round_id": "R0001", "candidate": "P0001",
                                   "parent_version": "H0000", "check_revision": "1"}]


def test_recovery_after_decision_does_not_repeat_task_check_or_takeover(initial_tree, tmp_path):
    driver = FakeDriver(("accept",))
    broker = FakeBroker()
    loop = make_loop(tmp_path, initial_tree, driver, broker, tasks=("t1", "t2"))
    feedback = loop.execute_task()

    # Simulate a process dying after the immutable decision was written but
    # before the state cursor was advanced.  Recovery must commit that exact
    # decision rather than re-running task inference or candidate checking.
    round_dir = loop._round_dir(1)
    round_dir.mkdir(parents=True, exist_ok=True)
    proposal = loop.root / "proposals/P0001/agent_code"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copytree(loop.active_code, proposal)
    (proposal / "minisweagent" / "recovery_marker.py").write_text("MARKER = 1\n")
    write(proposal.parent / "candidate_identity.json", {
        "proposal": "P0001", "parent_version": "H0000",
        "parent_hash": tree_hash(loop.active_code),
        "candidate_hash": tree_hash(proposal),
        "execution_hash": execution_hash(proposal),
        "plan_revision": "1"})
    write(round_dir / "diagnosis.json", {"task_id": "t1", "round_id": "R0001",
                                          "decision": "improve", "reason": "fixture",
                                          "evidence_refs": feedback["evidence_refs"],
                                          "reports": {"1": {"report": "failure",
                                                              "evidence_refs": feedback["evidence_refs"]},
                                                      "2": {"report": "mechanism",
                                                              "evidence_refs": feedback["evidence_refs"]}}})
    write(round_dir / "online_workflow_result.json", {
        "status": "completed", "completed_steps": 7, "process_status": 0,
        "metrics": {"calls": 4, "tokens": 80}})
    decision = {"round_id": "R0001", "task_id": "t1", "proposal": "P0001",
                "parent_version": "H0000", "decision": "accepted", "completed_steps": 7,
                "candidate_check": "candidate_check.json", "diagnosis": read(round_dir / "diagnosis.json"),
                "acceptance": {"passed": True, "source_changed": True,
                               "execution_changed": True, "score_known": True,
                               "parent_score": 0.0, "candidate_score": 1.0,
                               "improvement_criterion": "current_task_failure_fixed"}}
    write(loop.root / "decisions/R0001.json", decision)
    write(loop.root / "online_state.json", {**loop.state, "status": "checking",
                                              "current_task": "t1", "current_round": "R0001",
                                              "current_proposal": "P0001"})
    check_snapshot = {"job_id": "candidate:R0001:P0001", "task_id": "t1",
                      "check_revision": "1", "round_id": "R0001", "proposal": "P0001",
                      "parent_version": "H0000",
                      "parent_hash": tree_hash(loop.active_code),
                      "candidate_hash": tree_hash(proposal),
                      "parent_execution_hash": execution_hash(loop.active_code),
                      "candidate_execution_hash": execution_hash(proposal),
                      "checked_candidate_hash": tree_hash(proposal),
                      "checked_candidate_execution_hash": execution_hash(proposal),
                      "check": {"record": {"score": 1.0},
                                "metrics": {"calls": 1, "tokens": 12}}}
    write(round_dir / "candidate_check.json", check_snapshot)
    # A recovery implementation should detect the existing decision and move
    # the cursor atomically.  The current task feedback remains one record.
    recovered = OnlineLoop(loop.root, ("t1", "t2"), driver=driver, broker=broker,
                           model_identity={"model": "fixture"},
                           service_identity={"endpoint": "fixture"})
    before_tasks, before_checks = len(driver.task_calls), len(driver.check_calls)
    assert recovered.active_version == "H0001"
    assert recovered.state["next_task_index"] == 1
    assert len(driver.task_calls) == before_tasks
    assert len(driver.check_calls) == before_checks
    assert len(list((recovered.root / "versions").glob("H0001/agent_code"))) == 1


def test_execution_summary_and_takeover_summary_are_the_same_record(initial_tree, tmp_path):
    driver = FakeDriver(("accept",))
    loop = make_loop(tmp_path, initial_tree, driver, FakeBroker(), tasks=("t1",))
    loop.run_once()
    workflow_result = read(loop.root / "rounds/R0001/online_workflow_result.json")
    decision = read(loop.root / "decisions/R0001.json")
    execution_summary = workflow_result.get("execution_summary") or workflow_result.get("summary")
    takeover_summary = decision.get("takeover_summary") or decision.get("summary")
    assert execution_summary is not None
    assert takeover_summary == execution_summary


def test_execution_hash_changes_for_loaded_harness_but_ignores_description(initial_tree):
    before = execution_hash(initial_tree)
    # Driver private completion records and OnlineLoop snapshots must use one
    # source identity definition; otherwise a valid Docker candidate is
    # rejected even though its tree is unchanged.
    assert driver_execution_hash(initial_tree) == before
    (initial_tree / "description.txt").write_text("documentation only\n")
    assert execution_hash(initial_tree) == before
    (initial_tree / "minisweagent" / "loaded_change.py").write_text("VALUE = 1\n")
    assert execution_hash(initial_tree) != before


def test_candidate_zero_with_passing_parent_is_rejected_by_declared_mechanism_criterion(
        initial_tree, tmp_path):
    """A mechanism criterion cannot turn a regression into a takeover."""
    candidate = tmp_path / "candidate"
    import shutil
    shutil.copytree(initial_tree, candidate)
    (candidate / "minisweagent" / "online_fixture_marker.py").write_text("MARKER = 1\n")
    parent_hash = execution_hash(initial_tree)
    candidate_hash = execution_hash(candidate)
    snapshot = {
        "parent_hash": parent_hash,
        "parent_execution_hash": parent_hash,
        "candidate_hash": tree_hash(candidate),
        "candidate_execution_hash": candidate_hash,
        "checked_candidate_hash": tree_hash(candidate),
        "checked_candidate_execution_hash": candidate_hash,
        "check": {
            "record": {"score": 0.0},
            "mechanism_check": {"parent_failed": False, "candidate_passed": True},
        },
    }
    loop = make_loop(tmp_path, initial_tree, FakeDriver(("accept",)), FakeBroker(),
                     tasks=("t1",))
    result = loop._acceptance(
        candidate,
        {"record": {"score": 1.0}},
        snapshot,
        {"criterion": "mechanism_parent_failed_candidate_passed"},
    )
    assert result["parent_score"] == 1.0
    assert result["candidate_score"] == 0.0
    assert result["non_regression"] is False
    assert result["passed"] is False


def test_active_source_staleness_is_rejected_on_restart(initial_tree, tmp_path):
    loop = make_loop(tmp_path, initial_tree, FakeDriver(("skip",)), FakeBroker(),
                     tasks=("t1",))
    # This file is deliberately outside execution_hash but remains part of the
    # immutable source identity.  A restart must detect the stale active tree.
    (loop.active_code / "description.txt").write_text("stale active source\n")
    with pytest.raises(OnlineError, match="source or control identity|source changed|stale"):
        OnlineLoop(loop.root, ("t1",), driver=FakeDriver(("skip",)),
                   broker=FakeBroker())


def test_active_identity_allows_explicit_empty_identities_but_requires_all_fields(
        initial_tree, tmp_path):
    loop = OnlineLoop(tmp_path / "run", ("t1",), active_code=initial_tree,
                      model_identity=None, service_identity=None)
    # ``None`` is a valid explicit identity input; its persisted digest is
    # still required and must not be replaced by an absent field.
    assert loop.active_code.is_dir()
    identity_path = loop.root / "versions/H0000/version_identity.json"
    identity = read(identity_path)
    for key in ("control_manifest", "control_manifest_sha256",
                "model_identity_sha256", "service_identity_sha256"):
        missing = dict(identity)
        missing.pop(key)
        write(identity_path, missing)
        with pytest.raises(OnlineError, match="control identity"):
            _ = loop.active_code
        write(identity_path, identity)


@pytest.mark.parametrize("missing", ["candidate_identity.json", "candidate_check.json"])
def test_accepted_recovery_requires_candidate_records(initial_tree, tmp_path, missing):
    driver = FakeDriver(("accept",))
    broker = FakeBroker()
    loop = make_loop(tmp_path, initial_tree, driver, broker, tasks=("t1", "t2"))
    feedback = loop.execute_task()
    round_dir = loop._round_dir(1)
    round_dir.mkdir(parents=True, exist_ok=True)
    proposal = loop.root / "proposals/P0001/agent_code"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copytree(loop.active_code, proposal)
    (proposal / "minisweagent" / "recovery_marker.py").write_text("MARKER = 1\n")
    write(proposal.parent / "candidate_identity.json", {
        "proposal": "P0001", "parent_version": "H0000",
        "parent_hash": tree_hash(loop.active_code),
        "candidate_hash": tree_hash(proposal),
        "execution_hash": execution_hash(proposal), "plan_revision": "1"})
    write(round_dir / "candidate_check.json", {
        "job_id": "candidate:R0001:P0001", "task_id": "t1",
        "check_revision": "1", "round_id": "R0001", "proposal": "P0001",
        "parent_version": "H0000", "parent_hash": tree_hash(loop.active_code),
        "candidate_hash": tree_hash(proposal),
        "parent_execution_hash": execution_hash(loop.active_code),
        "candidate_execution_hash": execution_hash(proposal),
        "checked_candidate_hash": tree_hash(proposal),
        "checked_candidate_execution_hash": execution_hash(proposal),
        "check": {"record": {"score": 1.0}}})
    write(loop.root / "decisions/R0001.json", {
        "round_id": "R0001", "task_id": "t1", "proposal": "P0001",
        "parent_version": "H0000", "decision": "accepted"})
    write(loop.root / "online_state.json", {
        **loop.state, "status": "checking", "current_task": "t1",
        "current_round": "R0001", "current_proposal": "P0001"})
    (proposal.parent / missing if missing == "candidate_identity.json"
     else round_dir / missing).unlink()
    with pytest.raises(OnlineError, match="candidate identity|candidate check"):
        OnlineLoop(loop.root, ("t1", "t2"), driver=driver, broker=broker,
                   model_identity={"model": "fixture"},
                   service_identity={"endpoint": "fixture"})


def test_top_level_result_is_ignored_by_attempt_layout(tmp_path):
    task = tmp_path / "task"
    write(task / "host_result.json", {"record": {"score": 1}})
    assert attempt_result_paths(task) == []
    for name in ('attempt_10', 'attempt_2', 'attempt_0', 'attempt_old'):
        write(task / name / 'host_result.json', {'record': {'score': 1}})
    (task / 'attempt_1').symlink_to(task / 'attempt_0', target_is_directory=True)
    assert [p.parent.name for p in attempt_result_paths(task)] == [
        'attempt_0', 'attempt_2', 'attempt_10']
