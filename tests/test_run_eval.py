import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import mlflow
import pytest

import agent

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
sys.modules["run_eval"] = run_eval
_spec.loader.exec_module(run_eval)

GOOD = {
    "category": "billing",
    "priority": "P2",
    "route": "billing-team",
    "rationale": "Double charge.",
}
EXP = {"expected_category": "billing", "expected_priority": "P2"}


def fake_trace(*spans):
    return SimpleNamespace(data=SimpleNamespace(spans=[SimpleNamespace(name=n, start_time_ns=t) for n, t in spans]))


def test_loader_reads_all_real_rows():
    rows = run_eval.load_rows()
    assert len(rows) == 20
    assert rows[0] == {
        "inputs": {"ticket_id": "T-1042"},
        "expectations": {"expected_category": "billing", "expected_priority": "P2"},
    }


def test_valid_schema():
    assert run_eval.valid_schema(outputs=GOOD) == 1
    assert run_eval.valid_schema(outputs={**GOOD, "priority": "P9"}) == 0
    assert run_eval.valid_schema(outputs={"error": "boom"}) == 0


def test_matchers():
    assert run_eval.category_match(outputs=GOOD, expectations=EXP) == 1
    assert run_eval.priority_match(outputs=GOOD, expectations=EXP) == 1
    assert run_eval.category_match(outputs={**GOOD, "category": "bug"}, expectations=EXP) == 0
    assert run_eval.priority_match(outputs={**GOOD, "priority": "P1"}, expectations=EXP) == 0
    assert run_eval.category_match(outputs={"error": "x"}, expectations=EXP) == 0
    assert run_eval.priority_match(outputs={"error": "x"}, expectations=EXP) == 0


def test_tool_order():
    assert run_eval.tool_order(trace=fake_trace(("get_ticket", 1), ("get_customer_history", 2))) == 1
    assert run_eval.tool_order(trace=fake_trace(("get_customer_history", 1), ("get_ticket", 2))) == 0
    assert run_eval.tool_order(trace=fake_trace(("get_ticket", 1))) == 0
    assert run_eval.tool_order(trace=fake_trace(("get_customer_history", 1))) == 0
    assert run_eval.tool_order(trace=fake_trace()) == 0


def test_approve_counts():
    run_eval.reset_approved()
    assert run_eval.approve("T-1044", "why") is True
    assert run_eval.approve("T-1048", "why") is True
    assert run_eval.approved_count() == 2
    run_eval.reset_approved()
    assert run_eval.approved_count() == 0


def test_predict_returns_error_on_failure(monkeypatch):
    async def boom(ticket_id, **kw):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agent, "triage", boom)
    assert run_eval.predict("T-1042") == {"error": "model exploded"}


def test_evaluate_one_run_with_scores(monkeypatch, tmp_path):
    async def fake_triage(ticket_id, *, approve=None, **kw):
        with mlflow.start_span(name="get_ticket"):
            pass
        if ticket_id == "T-1043":
            raise RuntimeError("boom")
        with mlflow.start_span(name="get_customer_history"):
            pass
        if ticket_id == "T-1044":
            approve(ticket_id, "needs human")
        return GOOD

    monkeypatch.setattr(agent, "triage", fake_triage)
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("triage-agent-test")
    rows = [r for r in run_eval.load_rows() if r["inputs"]["ticket_id"] in ("T-1042", "T-1043", "T-1044")]
    result = run_eval.run_eval(rows)

    assert run_eval.approved_count() == 1
    exp = mlflow.get_experiment_by_name("triage-agent-test")
    assert len(mlflow.search_runs([exp.experiment_id])) == 1
    assert result.metrics["valid_schema/mean"] == pytest.approx(2 / 3)
    assert result.metrics["tool_order/mean"] == pytest.approx(2 / 3)
    assert 0 <= result.metrics["category_match/mean"] <= 1
    assert result.metrics["priority_match/mean"] <= 2 / 3


def test_approve_counts_each_ticket_once():
    run_eval.reset_approved()
    run_eval.approve("T-1044", "first")
    run_eval.approve("T-1044", "again after a retry")
    assert run_eval.approved_count() == 1
    run_eval.reset_approved()


def _patch_triage(monkeypatch, behaviours):
    import agent

    calls = []

    async def fake_triage(ticket_id, *, approve=None, **kw):
        calls.append(ticket_id)
        result = behaviours[min(len(calls) - 1, len(behaviours) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(agent, "triage", fake_triage)
    return calls


GOOD = {"category": "billing", "priority": "P2", "route": "billing-team", "rationale": "ok"}


def test_predict_waits_and_retries_on_rate_limit(monkeypatch):
    waits = []
    monkeypatch.setattr(run_eval.time, "sleep", waits.append)
    calls = _patch_triage(
        monkeypatch,
        [RuntimeError("Error code: 429 - Rate limit reached. Please try again in 3.5s."), GOOD],
    )
    assert run_eval.predict("T-1042") == GOOD
    assert len(calls) == 2 and waits == [4.5]


def test_predict_gives_up_after_the_retry_limit(monkeypatch):
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    calls = _patch_triage(monkeypatch, [RuntimeError("429 rate limit")])
    out = run_eval.predict("T-1042")
    assert "429" in out["error"] and len(calls) == run_eval.RATE_LIMIT_RETRIES + 1


def test_predict_does_not_retry_other_errors(monkeypatch):
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: pytest.fail("slept"))
    calls = _patch_triage(monkeypatch, [ValueError("bad provider")])
    assert run_eval.predict("T-1042") == {"error": "bad provider"} and len(calls) == 1


def test_retry_wait_uses_the_hint_and_is_capped():
    assert run_eval._retry_wait(RuntimeError("try again in 250ms")) == pytest.approx(1.25)
    assert run_eval._retry_wait(RuntimeError("try again in 5m")) == run_eval.RATE_LIMIT_MAX_WAIT
    assert run_eval._retry_wait(RuntimeError("no hint")) == run_eval.DEFAULT_WAIT
