"""Evaluate the triage agent over the labelled tickets with four code scorers.

Usage: uv run python eval/run_eval.py
"""

import asyncio
import csv
import os
import re
import sys
import threading
import time
from pathlib import Path

import mlflow
from mlflow.genai.scorers import scorer

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from triage_schema import DecisionError, parse_decision  # noqa: E402

LABELS_CSV = ROOT / "eval" / "labelled_tickets.csv"
SCORER_NAMES = ["valid_schema", "category_match", "priority_match", "tool_order"]

RATE_LIMIT_RETRIES = 5
RATE_LIMIT_MAX_WAIT = 60  # seconds
DEFAULT_WAIT = 20

_lock = threading.Lock()
_approved: set[str] = set()  # ticket ids, so a retried ticket counts once


def load_rows(path: Path = LABELS_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [
            {
                "inputs": {"ticket_id": r["ticket_id"]},
                "expectations": {
                    "expected_category": r["expected_category"],
                    "expected_priority": r["expected_priority"],
                },
            }
            for r in csv.DictReader(f)
        ]


def approve(ticket_id: str, reason: str) -> bool:
    """Auto-approve every escalation and count it. Only used inside this script."""
    with _lock:
        _approved.add(ticket_id)
    return True


def approved_count() -> int:
    with _lock:
        return len(_approved)


def reset_approved() -> None:
    with _lock:
        _approved.clear()


def _is_rate_limit(err: Exception) -> bool:
    text = f"{type(err).__name__} {err}".lower()
    return "429" in text or "rate limit" in text or "resource_exhausted" in text or "ratelimit" in text


def _retry_wait(err: Exception) -> float:
    """Seconds to wait before retrying, from the provider's 'try again in ...' hint when it gives one."""
    m = re.search(r"try again in (\d+(?:\.\d+)?)\s*(ms|s|m)\b", str(err))
    if not m:
        return DEFAULT_WAIT
    value = float(m.group(1)) * {"ms": 0.001, "s": 1, "m": 60}[m.group(2)]
    return min(value + 1, RATE_LIMIT_MAX_WAIT)


@mlflow.trace(name="triage", span_type="AGENT")
def predict(ticket_id: str) -> dict:
    """Run the agent on one ticket; never raises. A rate-limited call waits and retries a few times."""
    from agent import triage

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return asyncio.run(triage(ticket_id, approve=approve))
        except Exception as err:  # noqa: BLE001 - a failed ticket must score 0, not stop the run
            if _is_rate_limit(err) and attempt < RATE_LIMIT_RETRIES:
                time.sleep(_retry_wait(err))
                continue
            return {"error": str(err) or type(err).__name__}


def _decision(outputs):
    try:
        return parse_decision(outputs)
    except DecisionError:
        return None


@scorer
def valid_schema(outputs) -> int:
    return int(_decision(outputs) is not None)


def _field_match(outputs, expected, field) -> int:
    got = outputs.get(field) if isinstance(outputs, dict) else None
    return int(got is not None and got == expected)


@scorer
def category_match(outputs, expectations) -> int:
    return _field_match(outputs, expectations.get("expected_category"), "category")


@scorer
def priority_match(outputs, expectations) -> int:
    return _field_match(outputs, expectations.get("expected_priority"), "priority")


@scorer
def tool_order(trace) -> int:
    if trace is None:
        return 0
    spans = trace.data.spans
    ticket = [s.start_time_ns for s in spans if s.name == "get_ticket"]
    history = [s.start_time_ns for s in spans if s.name == "get_customer_history"]
    if not ticket or not history:
        return 0
    return int(min(ticket) < min(history))


SCORERS = [valid_schema, category_match, priority_match, tool_order]


def run_eval(rows: list[dict] | None = None):
    reset_approved()
    # one ticket at a time by default: provider token-per-minute limits are low; the user can raise it
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")
    return mlflow.genai.evaluate(
        data=rows if rows is not None else load_rows(),
        scorers=SCORERS,
        predict_fn=predict,
    )


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    mlflow.set_tracking_uri("sqlite:///mlflow.db")
    mlflow.set_experiment("triage-agent")
    mlflow.langchain.autolog()

    result = run_eval()
    for name in SCORER_NAMES:
        value = result.metrics.get(f"{name}/mean")
        print(f"{name}: {value:.2f}" if value is not None else f"{name}: n/a")
    print(f"auto-approved escalations: {approved_count()}")


if __name__ == "__main__":
    main()
