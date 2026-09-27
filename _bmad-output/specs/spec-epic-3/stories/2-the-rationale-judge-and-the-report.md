---
title: 'The rationale judge and the report'
type: 'feature'
created: '2026-09-26'
status: 'in-review'
baseline_commit: '5b8d8e39a119988bd1f112e50b282d241eecf87f'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-epic-3/SPEC.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The eval scores category, priority, schema and tool order, but nothing judges whether the agent's rationale is sound, and the results only exist on screen and in MLflow. There is no total token cost and no report a person or a script can reuse. Covers CAP-6 and CAP-7 of the Epic 3 spec.

**Approach:** Add a fifth scorer, `rationale_judge`, to `eval/run_eval.py`. For each ticket it asks a Groq model (`ChatGroq`, model from `JUDGE_MODEL`, key from `GROQ_API_KEY`) whether the decision's rationale is sound, given that ticket's `judge_notes`, and records `pass` or `fail` with a one-line reason. After the run, the script prints the mean of all five scorers, the agent's total tokens (read from the run's MLflow traces), the auto-approved escalation count, and writes the same numbers to `eval/latest_report.json`.

## Boundaries & Constraints

**Always:**
- The judge always uses `ChatGroq` with `JUDGE_MODEL` (default `openai/gpt-oss-120b`) and `GROQ_API_KEY`, whatever `PROVIDER` says. It never reads `GEMINI_API_KEY`. If `GROQ_API_KEY` is missing, the script stops before running any ticket, with a message naming the variable.
- The judge sees only that ticket's decision (category, priority, route, rationale) and its `judge_notes` from `eval/labelled_tickets.csv`, and answers `pass` or `fail` with a one-line reason. `judge_notes` is added to each row's expectations. `TRIAGE_POLICY.md` is not passed to the judge.
- The judge's output is read as `pass` or `fail` only; anything else, or a call that fails, is a `fail` whose reason starts with `judge error:`. A rate-limited judge call waits and retries the same way `predict` does.
- A ticket the agent failed on (an `{"error": ...}` output) is a `fail` for the judge too, without a model call.
- `rationale_judge`'s mean is the pass rate: `pass` counts 1 and `fail` 0. MLflow does not average text values, so the script computes this mean itself from the run's per-ticket results.
- Total tokens are the sum of `total_tokens` over the traces of this run; a trace with no token usage counts 0.
- `eval/latest_report.json` holds: `run_id`, the five scorer means by name, `total_tokens`, `auto_approved_escalations` and `tickets_with_agent_errors`. Printed output shows the same numbers.
- The four code scorers, the predict function and the escalation behaviour from story 1 keep working unchanged.

**Never:** edit `eval/labelled_tickets.csv`, `TRIAGE_POLICY.md`, `agent.py`, `triage_schema.py` or `run_agent.py`; add packages; write any file other than `eval/latest_report.json` outside MLflow's store; commit the report (it is already git-ignored).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Sound rationale | Judge answers pass | `pass` plus its reason on that ticket | N/A |
| Unsound rationale | Judge answers fail | `fail` plus its reason | N/A |
| Judge reply unreadable | Not `pass` or `fail` | `fail`, reason starts `judge error:` | N/A |
| Judge call fails | Groq error, not rate limit | `fail`, reason starts `judge error:` | The run continues |
| Judge rate-limited | 429 | Waits and retries, then as above | Same wait rule as `predict` |
| Agent error ticket | Output is `{"error": ...}` | `fail`, no judge call | N/A |
| No Groq key | `GROQ_API_KEY` unset | Stops before any ticket runs | Message names `GROQ_API_KEY` |
| Gemini key absent | `GEMINI_API_KEY` unset, agent on Groq | Judge unaffected | N/A |
| Full run | 20 tickets | Five means, total tokens and escalation count printed and written to `eval/latest_report.json` | N/A |

</frozen-after-approval>

## Code Map

- `eval/run_eval.py` -- from story 1: `load_rows` (add `judge_notes` to expectations), the scorers and `SCORERS` list, `_is_rate_limit` and `_retry_wait` (reuse for the judge), `run_eval()` and `main()` (extend to print and write the report). Keep story 1 behaviour.
- `eval/labelled_tickets.csv` -- `judge_notes` column; read only.
- `tests/test_run_eval.py` -- story 1's tests and fake trace helpers; the loader test checks the expectations shape and will need `judge_notes`.
- `.gitignore` -- already ignores `eval/latest_report.json`.
- Installed API, checked on a small trial: a scorer may return `mlflow.entities.Feedback(value="pass", rationale="...")`; the run's `result.result_df` has columns `<scorer>/value` and `<scorer>/rationale` per ticket; MLflow gives no `/mean` for text values; each trace has `info.token_usage` with `total_tokens`; the judge's own model call does not add a trace to the run. `mlflow.get_run(run_id).info.experiment_id` gives the experiment to search traces in.
- `langchain_groq.ChatGroq(model=, api_key=, temperature=)` -- already a dependency.

## Tasks & Acceptance

**Execution:**
- [x] `eval/run_eval.py` -- add the `rationale_judge` scorer with a patchable judge-model factory, `judge_notes` in the rows, the pass-rate mean, total tokens, `write_report`, and the missing-key check in `main` -- CAP-6 and CAP-7
- [x] `tests/test_run_eval.py` -- offline with a fake judge model: pass, fail, unreadable, error, rate-limit retry, agent-error ticket, missing key, no `GEMINI_API_KEY` read, the token sum on fake traces, the report file contents, and one small `mlflow.genai.evaluate` run showing the judge column and the printed report -- covers the matrix

**Acceptance Criteria:**
- Given `uv run python eval/run_eval.py` with valid keys, when it finishes, then every ticket has a `rationale_judge` value of `pass` or `fail` with a reason, and the script prints five scorer means, total tokens and the escalation count.
- Given that run, then `eval/latest_report.json` holds the same numbers.
- Given `GEMINI_API_KEY` unset and the agent on Groq, then the judge works; given `GROQ_API_KEY` unset, then the script stops before any ticket runs.
- Given `uv run pytest` with no network and no keys, then all tests pass.

## Implementation Notes

- `eval/run_eval.py`: `rationale_judge` scorer via a patchable `_judge_model()` (always `ChatGroq`, `JUDGE_MODEL`/`GROQ_API_KEY`, never `GEMINI_API_KEY`), a strict one-line-JSON prompt/parse (`_parse_verdict`), `judge_pass_rate` (mean computed from `result_df` since MLflow does not average text scores), `sum_tokens`/`total_tokens` (sum over the run's traces), `build_report`/`write_report`, and a `GROQ_API_KEY` pre-check in `main`.
- `load_rows` adds `judge_notes` to each row's `expectations`.
- Live result (Groq, 2026-09-26, 8 min 29 s): `valid_schema` 0.90, `category_match` 0.85, `priority_match` 0.90, `tool_order` 0.90, `rationale_judge` 0.80, total tokens 65390, 3 auto-approved escalations, 2 tickets with agent errors. `eval/latest_report.json` matches the printed numbers exactly. Confirmed against traces directly: 20 traces, summed trace tokens equal the reported total, and the errored ticket's `rationale_judge` assessment is `fail` with "the agent failed on this ticket; no judge call".
- The 2 agent errors are provider issues already known from story 2.2, not new: `T-1099` hit a network `Connection error`, and `T-1048` hit Groq's `tool_use_failed` flake (deferred in story 2.2's review). Neither is caused by this story's code.
- Run took 8 min 29 s at 1 worker, since the judge now shares Groq's tokens-per-minute limit with the agent.

## Spec Change Log

## Review Triage Log

## Design Notes

The judge is a plain function calling `ChatGroq`, wrapped as an MLflow scorer, so its prompt and parsing can be tested with a fake model. Asking the judge for one line of JSON (`{"verdict": "pass", "reason": "..."}`) keeps parsing strict. The judge runs on the same Groq organisation as an agent run on Groq, so the two share Groq's token-per-minute limit; the story 1 retry rule covers it.

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass, offline
- `PROVIDER=groq uv run python eval/run_eval.py` -- expected: five means, total tokens, escalation count, and a new `eval/latest_report.json` with the same numbers
- `env -u GROQ_API_KEY uv run python eval/run_eval.py` -- expected: stops at once naming `GROQ_API_KEY`

**Manual checks (if no CLI):**
- In the MLflow UI, open the run and confirm each trace has a `rationale_judge` value and reason.
