"""Current candidate relay identity and persistence contract."""
from __future__ import annotations

import hashlib

import pytest

from mini_swe_rsi.broker import (
    Broker,
    CANDIDATE_JOB_METADATA_FIELDS,
    ModelError,
    validate_candidate_job_record,
)
from mini_swe_rsi.context_budget import ContextBudget
from mini_swe_rsi.core import read, write


def _metadata(job_id="candidate:R0001:P0001", token="token"):
    return {
        "job_id": job_id,
        "task_id": "t1",
        "round_id": "R0001",
        "candidate": "P0001",
        "parent_version": "H0000",
        "check_revision": "1",
        "stage": "candidate_check",
        "agent": "candidate-main",
        "role": "orchestrator",
        "session_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
    }


@pytest.mark.parametrize(
    "status,completion",
    [
        ("registered", {}),
        ("running", {}),
        ("completed", {"result": {"passed": True}}),
        ("failed", {"error": {"type": "ModelError", "message": "failed"}}),
        ("interrupted", {"error": {"type": "OnlineInterrupted", "message": "stopped"}}),
    ],
)
def test_validate_candidate_job_record_accepts_each_state(status, completion):
    metadata = _metadata()
    state = {"job_id": metadata["job_id"], "metadata": metadata, "status": status,
             **completion}
    assert validate_candidate_job_record(metadata["job_id"], metadata, state) is True


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda metadata, state: metadata.pop("stage"), "metadata is incomplete"),
        (lambda metadata, state: state.pop("job_id"), "job id differs"),
        (lambda metadata, state: state.update(status="bogus"), "Unknown candidate job status"),
    ],
)
def test_validate_candidate_job_record_rejects_missing_identity_and_status(mutate, match):
    metadata = _metadata()
    state = {"job_id": metadata["job_id"], "metadata": metadata, "status": "registered"}
    mutate(metadata, state)
    with pytest.raises(ValueError, match=match):
        validate_candidate_job_record("candidate:R0001:P0001", metadata, state)


def test_registration_requires_complete_metadata_but_pause_stays_first(tmp_path, monkeypatch):
    monkeypatch.setattr("mini_swe_rsi.broker.secrets", lambda: {
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "fixture",
        "LOCAL_LLM_MODEL": "fixture",
    })
    broker = Broker(tmp_path, context=ContextBudget({"context_window": 32768,
                                                       "max_output_tokens": 2048}))
    try:
        with pytest.raises(ValueError, match="metadata is incomplete"):
            broker.register_candidate_job("candidate:R0001:P0001", lambda payload: {})
        broker.pause_event.set()
        with pytest.raises(ModelError, match="TransportPaused"):
            broker.register_candidate_job("candidate:R0001:P0001", lambda payload: {})
    finally:
        broker.close()


def test_persisted_running_record_becomes_interrupted_and_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr("mini_swe_rsi.broker.secrets", lambda: {
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "fixture",
        "LOCAL_LLM_MODEL": "fixture",
    })
    broker = Broker(tmp_path, context=ContextBudget({"context_window": 32768,
                                                       "max_output_tokens": 2048}))
    token = broker.session("candidate", seconds=30, calls=0)
    metadata = _metadata(token=token)
    path = tmp_path / "candidate_checks" / "candidate:R0001:P0001.json"
    write(path, {"job_id": metadata["job_id"], "metadata": metadata, "status": "running"})
    calls = []
    try:
        broker.register_candidate_job(metadata["job_id"],
                                      lambda payload: calls.append(payload) or {"passed": True},
                                      metadata)
        persisted = read(path)
        assert persisted["status"] == "interrupted"
        assert isinstance(persisted["error"], dict)
        payload = {key: metadata[key] for key in CANDIDATE_JOB_METADATA_FIELDS[:7]}
        with pytest.raises(ModelError, match="previously failed"):
            broker.candidate_check(token, payload, agent="candidate-main", role="orchestrator")
        assert calls == []
    finally:
        broker.finish(token)
        broker.close()


@pytest.mark.parametrize("case", [
    "missing_metadata", "missing_job_id", "null", "linked",
    "registered_result", "running_result", "failed_result",
    "completed_error", "running_error", "completed_missing_result", "bad_status",
])
def test_registration_rejects_invalid_persisted_record_without_overwriting(
        tmp_path, monkeypatch, case):
    monkeypatch.setattr("mini_swe_rsi.broker.secrets", lambda: {
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "fixture",
        "LOCAL_LLM_MODEL": "fixture",
    })
    broker = Broker(tmp_path, context=ContextBudget({"context_window": 32768,
                                                       "max_output_tokens": 2048}))
    metadata = _metadata()
    path = tmp_path / "candidate_checks" / "candidate:R0001:P0001.json"
    state = {"job_id": metadata["job_id"], "metadata": metadata, "status": "registered"}
    if case == "missing_metadata":
        state.pop("metadata")
    elif case == "missing_job_id":
        state.pop("job_id")
    elif case == "registered_result":
        state["result"] = {"passed": True}
    elif case == "running_result":
        state.update(status="running", result={"passed": True})
    elif case == "failed_result":
        state.update(status="failed", result={"passed": True},
                     error={"type": "ModelError", "message": "failed"})
    elif case == "completed_error":
        state.update(status="completed", result={"passed": True},
                     error={"type": "ModelError", "message": "late error"})
    elif case == "running_error":
        state.update(status="running", error={"type": "ModelError", "message": "late error"})
    elif case == "completed_missing_result":
        state.update(status="completed")
    elif case == "bad_status":
        state["status"] = "unknown"
    path.parent.mkdir(parents=True, exist_ok=True)
    if case == "null":
        path.write_text("null\n")
        before = path.read_bytes()
    elif case == "linked":
        target = tmp_path / "target.json"
        write(target, state)
        path.symlink_to(target)
        before = target.read_bytes()
    else:
        write(path, state)
        before = path.read_bytes()
    calls = []
    try:
        with pytest.raises(ValueError):
            broker.register_candidate_job(metadata["job_id"],
                                          lambda payload: calls.append(payload), metadata)
        assert calls == []
        if case == "linked":
            assert path.is_symlink() and path.resolve().read_bytes() == before
        else:
            assert path.read_bytes() == before
    finally:
        broker.close()


def test_empty_exception_message_is_persisted_and_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr("mini_swe_rsi.broker.secrets", lambda: {
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "fixture",
        "LOCAL_LLM_MODEL": "fixture",
    })
    broker = Broker(tmp_path, context=ContextBudget({"context_window": 32768,
                                                       "max_output_tokens": 2048}))
    token = broker.session("candidate", seconds=30, calls=0)
    metadata = _metadata(token=token)
    payload = {key: metadata[key] for key in CANDIDATE_JOB_METADATA_FIELDS[:7]}
    calls = []

    class EmptyMessageError(RuntimeError):
        pass

    def callback(request):
        calls.append(request)
        raise EmptyMessageError()

    try:
        broker.register_candidate_job(metadata["job_id"], callback, metadata)
        with pytest.raises(EmptyMessageError):
            broker.candidate_check(token, payload, agent="candidate-main", role="orchestrator")
        persisted = read(tmp_path / "candidate_checks" / "candidate:R0001:P0001.json")
        assert persisted["status"] == "failed"
        assert persisted["error"] == {"type": "EmptyMessageError", "message": ""}
        assert len(calls) == 1

        restarted = Broker(tmp_path, context=ContextBudget({"context_window": 32768,
                                                              "max_output_tokens": 2048}))
        replay_calls = []
        restarted.register_candidate_job(metadata["job_id"],
                                          lambda request: replay_calls.append(request), metadata)
        assert restarted.candidate_jobs[metadata["job_id"]]["error"]["message"] == ""
        assert replay_calls == []
        restarted.close()
    finally:
        broker.finish(token)
        broker.close()
