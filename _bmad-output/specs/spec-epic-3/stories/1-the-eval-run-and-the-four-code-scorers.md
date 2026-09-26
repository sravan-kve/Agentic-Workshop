---
title: 'The eval run and the four code scorers'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: '53e0dd7e330c518a4c5a6076435964eedd1e5f80'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-epic-3/SPEC.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing measures how well the triage agent decides. There is no `eval/run_eval.py`, so the 20 labelled tickets in `eval/labelled_tickets.csv` are never run through the agent or scored. Covers CAP-1, CAP-2, CAP-3, CAP-4, CAP-5 and CAP-8 of the Epic 3 spec.

**Approach:** Add `eval/run_eval.py`. `uv run python eval/run_eval.py` builds one row per CSV ticket, drives the existing `triage()` through `mlflow.genai.evaluate`, and scores every ticket with four code scorers: `valid_schema`, `category_match`, `priority_match` and `tool_order`. Every escalation is approved automatically and counted, so the run never waits for a person. It logs exactly one MLflow run to `sqlite:///mlflow.db`, experiment `triage-agent`, and prints the four scorer means and the auto-approved escalation count. The `rationale_judge` scorer and `eval/latest_report.json` are story 2.

## Boundaries & Constraints

**Always:**
- Built with `mlflow.genai.evaluate`, not a hand-rolled scoring loop. Each row is `{"inputs": {"ticket_id": ...}, "expectations": {"expected_category": ..., "expected_priority": ...}}`.
- The predict function calls `triage(ticket_id, approve=...)` from `agent.py` as-is, inside one `mlflow.trace` span named `triage`, so a resumed escalation stays in that ticket's single trace.
- `approve` returns `True` and counts under a lock, because MLflow runs tickets in parallel. It applies only inside this script; `run_agent.py` still asks a person.
- The predict function never raises: on any agent error it returns `{"error": "<message>"}`, so the run finishes and that ticket scores 0 on every scorer.
- Scores are 0 or 1: `valid_schema` = the output passes `parse_decision`; `category_match` and `priority_match` = the output equals the label; `tool_order` = the trace has a `get_ticket` span starting before a `get_customer_history` span.
- MLflow setup matches `run_agent.py`: `sqlite:///mlflow.db`, experiment `triage-agent`, `mlflow.langchain.autolog()`. `.env` is loaded, and the repo root goes on `sys.path` so `agent` and `triage_schema` import when run as `eval/run_eval.py`.
- The provider is whatever `PROVIDER` and `.env` say, as for `run_agent.py`.

**Never:** edit `eval/labelled_tickets.csv`, `TRIAGE_POLICY.md`, `agent.py`, `triage_schema.py` or `run_agent.py`; change the agent's prompts or decision logic; build `rationale_judge` or write `eval/latest_report.json` (story 2); add packages; write any new file outside MLflow's store.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full run | 20 CSV rows | One MLflow run, 20 traces, four scores per ticket; prints the four means and the escalation count | N/A |
| Escalating ticket | Agent asks to escalate (e.g. `T-1044`) | Approved automatically, counted, no terminal read | N/A |
| Non-escalating ticket | `T-1042` | Never counted | N/A |
| Agent error | `triage()` raises for one ticket | Run completes; that ticket scores 0 on all four | Output is `{"error": "<message>"}` |
| Invalid decision | Output fails `parse_decision` | `valid_schema` 0; matches follow the output as-is | N/A |
| Wrong tool order | `get_customer_history` before `get_ticket`, or either missing | `tool_order` 0 | N/A |
| Label mismatch | Category or priority differs from the label | That matcher 0 | N/A |

</frozen-after-approval>

## Code Map

- `eval/labelled_tickets.csv` -- 20 rows: `ticket_id`, `expected_category`, `expected_priority`, `expected_tools`, `judge_notes`. Read only. `expected_tools` and `judge_notes` are not used in this story.
- `agent.py` -- `async triage(ticket_id, *, model=None, approve=None) -> dict`. `approve(ticket_id, reason)` may be sync or async. Do not change.
- `triage_schema.py` -- `parse_decision()` and `DecisionError`. Use for `valid_schema`.
- `run_agent.py` -- the MLflow setup to mirror. Do not change.
- Installed API, checked on a two-ticket trial: `mlflow.genai.evaluate(data=, scorers=, predict_fn=)`; a `@mlflow.genai.scorers.scorer` function may take `outputs`, `expectations` and `trace`; it returns one run, metrics named `<scorer>/mean`; the predict function receives `ticket_id` as a keyword argument. With a wrapping `mlflow.trace`, each ticket, including an escalating one, gives one trace with `get_ticket`, `get_customer_history` and `escalate_to_human` spans.
- Two MLflow-related limits to keep in mind: MLflow runs tickets in parallel (it can be capped with the `MLFLOW_GENAI_EVAL_MAX_WORKERS` environment variable), and provider rate limits can fail a ticket, which the never-raise rule turns into a scored 0.

## Tasks & Acceptance

**Execution:**
- [x] `eval/run_eval.py` -- CSV loader, `approve` counter with a lock, the traced never-raising predict function, the four scorers, and the `mlflow.genai.evaluate` call with MLflow setup and printed means and count -- CAP-1 to 5 and CAP-8
- [x] `tests/test_run_eval.py` -- offline: the scorers on hand-built outputs and fake traces, the loader on the real CSV, the approve counter, the predict function with a patched `triage` (error becomes `{"error": ...}`), and one small `mlflow.genai.evaluate` run on a temp sqlite store with a patched `triage` proving exactly one run and per-ticket scores -- covers the matrix

**Acceptance Criteria:**
- Given `uv run python eval/run_eval.py` with valid keys, when it finishes, then it logged exactly one run in the `triage-agent` experiment covering all 20 tickets, with the four scorers on each.
- Given tickets that escalate, when the eval runs, then none waits for input and the printed count equals the number approved.
- Given `uv run pytest` with no network and no keys, then all tests pass.
- Given `run_agent.py`, then it still asks a person for the escalation yes/no.

## Implementation Notes

- `eval/run_eval.py`: `load_rows`, an `approve` that records ticket ids in a set under a lock (so a retried ticket counts once), the traced `predict`, the four scorers, and `run_eval`/`main`.
- Found on the first live run: Groq's free tier allows 8,000 tokens a minute, and MLflow runs 10 tickets at once by default, so 16 of 20 tickets hit a 429 and scored 0. Fixed in `run_eval.py` only: `predict` waits (from the provider's "try again in ..." hint, capped at 60 s, else 20 s) and retries a rate-limited ticket up to 5 times, and `run_eval` sets `MLFLOW_GENAI_EVAL_MAX_WORKERS` to 1 unless the caller has set it. Other errors are not retried and still become `{"error": ...}`.
- Live result (Groq, 2026-09-26, 2 min 39 s): `valid_schema` 1.00, `category_match` 0.95, `priority_match` 1.00, `tool_order` 1.00, 3 auto-approved escalations (`T-1044`, `T-1048`, `T-1057`). One MLflow run with 20 traces, 0 error outputs, `escalate_to_human` spans on exactly those three. The one category miss is `T-1045` (agent `how-to`, label `billing`). The earlier rate-limited run is also in the experiment (an eval run per invocation).

## Spec Change Log

- 2026-09-26, found on the first live run, not raised by a reviewer: the frozen text says a failing ticket returns `{"error": ...}` and scores 0, and that MLflow runs tickets in parallel. On Groq's free tier (8,000 tokens a minute, 10 parallel workers by default) 16 of 20 tickets hit a 429, so the first live run scored 0.20 on most scorers. The code now waits and retries a rate-limited ticket (up to 5 times, waiting the provider's hint, capped at 60 s) and defaults `MLFLOW_GENAI_EVAL_MAX_WORKERS` to 1. Other errors are still not retried. The frozen block was not edited; a human should decide whether to add a rate-limit row to the matrix through `/bmad-spec`. Known-bad state avoided: a run that finishes but scores 0 because of provider limits, not the agent. KEEP: rate-limit retry limited to `predict`, agent code untouched.

## Review Triage Log

Code review against `epic/3` (four layers): no hard violation of CAP-1 to 5 or CAP-8.

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| `tool_order` scored 1 for a ticket that errored after the tools ran (blind, edge) | medium | patch | Contradicted the frozen "error scores 0 on every scorer". Now 0 when the output is an error. Test added. |
| Rate-limit detection matched the digits `429` anywhere (blind, edge, acceptance) | medium | patch | Non-rate-limit errors would sleep and retry. Now matches specific phrases; tests added. |
| Nothing reports how many tickets errored (blind) | medium | patch | This is how the first live run silently scored 0. Now prints `tickets with agent errors: N`. Test added. |
| Evaluate test bounds were vacuous; worker default and `main` unchecked; `GOOD` defined twice; global state leaked (gap, blind, acceptance) | medium | patch | Exact per-scorer values now asserted; tests for the worker default, a caller-set value, and `main`; state restored. |
| Retry and worker cap not in the frozen spec (blind, acceptance) | medium | logged | Recorded in the Spec Change Log; the frozen block was not edited. |
| Retries run inside one trace so failed attempts add spans (blind, edge, acceptance) | low | rejected | `tool_order` compares the first spans; a failed attempt that reached only `get_ticket` cannot flip it. Live run showed 20 clean traces. |
| Count of approvals is per ticket, not per call (edge, acceptance) | low | rejected | Deliberate: a retried ticket must count once. |
| `import agent` outside the `try` (blind) | low | rejected | A broken import should fail the run loudly, not score every ticket 0. |
| CSV validation, `MAX_WORKERS` edge values, compound retry hints, no total deadline, NaN metrics, running event loop (edge) | low | rejected | Unlikely in the workshop flow; each fix adds guards. |
| Run ID and runtime not printed or documented (blind) | low | rejected | Cosmetic. |

## Design Notes

The eval passes its own `approve`, which `triage()` already supports; the agent's decision logic is untouched. `mlflow.genai.evaluate` names each metric `<scorer>/mean`, so the printed means come from `result.metrics`. Token totals and the JSON report come in story 2. Agent quality is not gated: a low score is a result, not a failure, and the script exits 0 when the run completes.

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass, offline
- `PROVIDER=groq uv run python eval/run_eval.py` -- expected: finishes with no prompt, prints four scorer means and an escalation count
- Open the MLflow UI, experiment `triage-agent` -- expected: one new run with 20 traces

**Manual checks (if no CLI):**
- In that run, a trace for an escalating ticket (e.g. `T-1044`) shows an `escalate_to_human` span.
