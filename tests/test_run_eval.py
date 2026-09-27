import importlib.util
import os
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
        "expectations": {
            "expected_category": "billing",
            "expected_priority": "P2",
            "judge_notes": rows[0]["expectations"]["judge_notes"],
        },
    }
    assert rows[0]["expectations"]["judge_notes"].startswith("Double charge")


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
    assert run_eval.tool_order(outputs=GOOD, trace=fake_trace(("get_ticket", 1), ("get_customer_history", 2))) == 1
    assert run_eval.tool_order(outputs=GOOD, trace=fake_trace(("get_customer_history", 1), ("get_ticket", 2))) == 0
    assert run_eval.tool_order(outputs=GOOD, trace=fake_trace(("get_ticket", 1))) == 0
    assert run_eval.tool_order(outputs=GOOD, trace=fake_trace(("get_customer_history", 1))) == 0
    assert run_eval.tool_order(outputs=GOOD, trace=fake_trace()) == 0


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
    _judge(monkeypatch, '{"verdict": "pass", "reason": "sound"}')
    monkeypatch.setenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")  # restored after the test
    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    try:
        mlflow.set_experiment("triage-agent-test")
        rows = [r for r in run_eval.load_rows() if r["inputs"]["ticket_id"] in ("T-1042", "T-1043", "T-1044")]
        result = run_eval.run_eval(rows)

        assert run_eval.approved_count() == 1 and run_eval.errored_count() == 1
        exp = mlflow.get_experiment_by_name("triage-agent-test")
        assert len(mlflow.search_runs([exp.experiment_id])) == 1
        report = run_eval.build_report(result)
        assert report["total_tokens"] == 0 and report["auto_approved_escalations"] == 1
        assert report["run_id"] == result.run_id
    finally:
        mlflow.set_tracking_uri(previous_uri)
    # T-1042 matches its labels (billing/P2); T-1043 errors (0 on all four); T-1044 returns billing/P2 but is labelled access/P1
    assert result.metrics["valid_schema/mean"] == pytest.approx(2 / 3)
    assert result.metrics["category_match/mean"] == pytest.approx(1 / 3)
    assert result.metrics["priority_match/mean"] == pytest.approx(1 / 3)
    assert result.metrics["tool_order/mean"] == pytest.approx(2 / 3)
    df = result.result_df
    assert sorted(df["rationale_judge/value"]) == ["fail", "pass", "pass"]
    assert df["rationale_judge/rationale"].notna().all()
    assert report["scorer_means"]["rationale_judge"] == pytest.approx(2 / 3)


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


def test_tool_order_is_zero_for_an_errored_ticket_even_if_the_tools_ran():
    trace = fake_trace(("get_ticket", 1), ("get_customer_history", 2))
    assert run_eval.tool_order(outputs={"error": "boom"}, trace=trace) == 0
    assert run_eval.tool_order(outputs=GOOD, trace=trace) == 1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Error code: 429 - Rate limit reached", True),
        ("RESOURCE_EXHAUSTED quota", True),
        ("Too Many Requests", True),
        ("ticket T-4290 not found, port 14291", False),
        ("model exploded", False),
    ],
)
def test_rate_limit_detection(text, expected):
    assert run_eval._is_rate_limit(RuntimeError(text)) is expected


def test_worker_default_is_one_and_a_caller_value_is_kept(monkeypatch):
    seen = []
    monkeypatch.setattr(mlflow.genai, "evaluate", lambda **kw: seen.append(os.environ["MLFLOW_GENAI_EVAL_MAX_WORKERS"]) or SimpleNamespace(metrics={}))
    monkeypatch.setenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", "x")
    monkeypatch.delenv("MLFLOW_GENAI_EVAL_MAX_WORKERS")  # records the original state so it is restored
    run_eval.run_eval([])
    monkeypatch.setenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", "3")
    run_eval.run_eval([])
    assert seen == ["1", "3"]


class FakeJudge:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        r = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(content=r)


JEXP = {**EXP, "judge_notes": "Double charge is P2."}


def _judge(monkeypatch, *replies):
    fake = FakeJudge(*replies)
    monkeypatch.setattr(run_eval, "_judge_model", lambda: fake)
    return fake


def _run_judge(outputs=GOOD):
    fb = run_eval.rationale_judge(outputs=outputs, expectations=JEXP)
    return fb.value, fb.rationale


def test_judge_pass_and_fail(monkeypatch):
    fake = _judge(monkeypatch, '{"verdict": "pass", "reason": "sound"}', '{"verdict": "FAIL", "reason": "weak"}')
    assert _run_judge() == ("pass", "sound")
    assert _run_judge() == ("fail", "weak")
    p = fake.prompts[0]
    assert "Double charge is P2." in p and "billing-team" in p and "Double charge." in p


@pytest.mark.parametrize("reply", ["maybe", '{"verdict": "great"}', "{}", '["pass"]', ""])
def test_judge_unreadable_reply_is_a_fail(monkeypatch, reply):
    _judge(monkeypatch, reply)
    value, reason = _run_judge()
    assert value == "fail" and reason.startswith("judge error:")


def test_judge_call_failure_is_a_fail_without_retry(monkeypatch):
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: pytest.fail("slept"))
    fake = _judge(monkeypatch, RuntimeError("groq down"))
    value, reason = _run_judge()
    assert value == "fail" and reason.startswith("judge error:") and len(fake.prompts) == 1


def test_judge_retries_when_rate_limited(monkeypatch):
    waits = []
    monkeypatch.setattr(run_eval.time, "sleep", waits.append)
    fake = _judge(monkeypatch, RuntimeError("Error code: 429. try again in 2s"), '{"verdict": "pass", "reason": "ok"}')
    assert _run_judge() == ("pass", "ok")
    assert len(fake.prompts) == 2 and waits == [3.0]


def test_judge_skips_model_for_agent_error(monkeypatch):
    fake = _judge(monkeypatch, '{"verdict": "pass", "reason": "x"}')
    value, _ = _run_judge({"error": "boom"})
    assert value == "fail" and fake.prompts == []


def test_judge_model_uses_groq_and_never_reads_gemini(monkeypatch):
    import langchain_groq

    seen = {}

    class FakeGroq:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(langchain_groq, "ChatGroq", FakeGroq)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    real_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda k, *a: pytest.fail(k) if k == "GEMINI_API_KEY" else real_get(k, *a))
    run_eval._judge_model()
    assert seen["model"] == "openai/gpt-oss-120b" and seen["api_key"] == "k"


def test_sum_tokens_counts_missing_usage_as_zero():
    def tr(usage):
        return SimpleNamespace(info=SimpleNamespace(token_usage=usage))

    assert run_eval.sum_tokens([tr({"total_tokens": 10}), tr(None), tr({}), tr({"total_tokens": 5})]) == 15


def test_judge_pass_rate():
    import pandas as pd

    df = pd.DataFrame({"rationale_judge/value": ["pass", "fail", "pass", "pass"]})
    assert run_eval.judge_pass_rate(df) == 0.75
    assert run_eval.judge_pass_rate(pd.DataFrame()) is None


def test_main_stops_without_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(run_eval, "run_eval", lambda: pytest.fail("ran a ticket"))
    with pytest.raises(SystemExit) as e:
        run_eval.main()
    assert "GROQ_API_KEY" in str(e.value)


def test_main_prints_and_writes_the_report(monkeypatch, capsys, tmp_path):
    import pandas as pd

    setup = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda uri: setup.append(("uri", uri)))
    monkeypatch.setattr(mlflow, "set_experiment", lambda name: setup.append(("exp", name)))
    monkeypatch.setattr(mlflow.langchain, "autolog", lambda: setup.append(("autolog", None)))
    metrics = {"valid_schema/mean": 1.0, "category_match/mean": 0.95, "priority_match/mean": 0.5}
    df = pd.DataFrame({"rationale_judge/value": ["pass", "fail"]})
    monkeypatch.setattr(run_eval, "run_eval", lambda: SimpleNamespace(metrics=metrics, result_df=df, run_id="r1"))
    monkeypatch.setattr(run_eval, "total_tokens", lambda run_id: 1234)
    report_path = tmp_path / "latest_report.json"
    monkeypatch.setattr(run_eval, "REPORT_PATH", report_path)
    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    run_eval.main()
    out = capsys.readouterr().out
    assert ("uri", "sqlite:///mlflow.db") in setup and ("exp", "triage-agent") in setup and ("autolog", None) in setup
    for line in ("valid_schema: 1.00", "category_match: 0.95", "priority_match: 0.50", "tool_order: n/a",
                 "rationale_judge: 0.50", "total tokens: 1234",
                 "auto-approved escalations: ", "tickets with agent errors: "):
        assert line in out
    import json

    report = json.loads(report_path.read_text())
    assert report["run_id"] == "r1" and report["total_tokens"] == 1234
    assert report["scorer_means"]["rationale_judge"] == 0.5 and report["scorer_means"]["tool_order"] is None
    assert set(report) == {"run_id", "scorer_means", "total_tokens", "auto_approved_escalations", "tickets_with_agent_errors"}
