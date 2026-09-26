---
title: 'Triage decision schema'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: 'b03eebd6f8bfceb73b842a55e0eb74040ca7ac39'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md'
  - '{project-root}/TRIAGE_POLICY.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no fixed shape for a triage decision, so the Epic 2 agent and the Epic 3 eval have nothing to validate against. Bad model output would pass silently.

**Approach:** One importable pydantic model, `TriageDecision`, plus `parse_decision()`, which accepts a JSON string or an already-parsed value and raises `DecisionError` (a `ValueError`) naming the offending field. Covers CAP-1 of the Epic 1 spec.

## Boundaries & Constraints

**Always:**
- Categories: billing, bug, access, performance, how-to. Priorities: P1 to P4. Routes: billing-team, bug-team, access-team, performance-team, how-to-team.
- Unknown extra fields are rejected.
- Category and route are independent fields: each only needs a valid value. The policy's category-to-route pairing is the agent's job, and the Epic 3 eval must be able to score a wrong route rather than have it rejected.
- Rationale must be a non-empty string after trimming; the returned decision holds the trimmed text. Whether it is one sentence is not enforced.
- Values are case-sensitive and type-strict: `Billing`, `p2` and a numeric `priority` are all rejected.
- The module is `triage_schema.py` at the repo root, next to `run_agent.py`, so Epic 2 imports it as `from triage_schema import TriageDecision, parse_decision`.
- Uses the existing `pydantic` dependency. No new packages, no network, no API keys.

**Never:** touch `seed/`, `mcp/triage_server.py`, `TRIAGE_POLICY.md`, `eval/labelled_tickets.csv` or `run_agent.py`; build the loader (story 2); add any agent or MCP code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid decision | `{"category":"billing","priority":"P2","route":"billing-team","rationale":"Double charge, so P2."}` as dict or JSON string | Returns a `TriageDecision` | N/A |
| Bad category | category `refund` | Rejected | `DecisionError` naming `category` |
| Bad priority | priority `P0` | Rejected | `DecisionError` naming `priority` |
| Bad route | route `sales-team` | Rejected | `DecisionError` naming `route` |
| Missing rationale | no `rationale` key | Rejected | `DecisionError` naming `rationale` |
| Blank rationale | `"   "` | Rejected | `DecisionError` naming `rationale` |
| Wrong type or case | `"rationale": null`, `"priority": 2` or category `Billing` | Rejected | `DecisionError` naming that field |
| Several bad fields | category `refund` and priority `P0` | Rejected | One `DecisionError` naming both fields |
| Extra field | valid plus `"note":"x"` | Rejected | `DecisionError` naming `note` |
| Not an object | `[]` or `"[]"` | Rejected | `DecisionError` saying a JSON object is required |
| Invalid JSON | `"{not json"` | Rejected | `DecisionError` saying the text is not valid JSON |

</frozen-after-approval>

## Code Map

- `run_agent.py` -- imports `triage` from an `agent` module at the repo root; shows root-level modules are the convention. Do not change.
- `pyproject.toml` -- `pydantic>=2.8` and `pytest` already present; `testpaths = ["tests"]`, but `tests/` does not exist yet and pytest cannot import a repo-root module without `pythonpath = ["."]`.
- `TRIAGE_POLICY.md` -- source of the category, priority and route values.
- `mcp/triage_server.py` -- unrelated to this story.

## Tasks & Acceptance

**Execution:**
- [x] `triage_schema.py` -- add `TriageDecision` (Literal-typed fields, extra fields forbidden, trimmed non-empty rationale), `DecisionError`, and `parse_decision()` -- single validation entry point for Epic 2 and Epic 3
- [x] `pyproject.toml` -- add `pythonpath = ["."]` under `[tool.pytest.ini_options]` -- lets tests import root-level modules; nothing else changes
- [x] `tests/test_triage_schema.py` -- one test per I/O matrix row, asserting the error message names the field -- proves CAP-1 success criterion

**Acceptance Criteria:**
- Given the five valid categories, four priorities and five routes, when each value is used in an otherwise valid decision, then it is accepted.
- Given any input in the error rows above, when `parse_decision()` runs, then it raises `DecisionError` and never returns a partial decision.
- Given `from triage_schema import TriageDecision, parse_decision` from the repo root, then the import works with no network or environment variables set.

## Implementation Notes

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Bad value echoed into error with no length limit (blind, edge) | medium | patch | A 500-char value gave a 613-char error; fixed with `_short()`, capped at 80 chars. Test added. |
| Deeply nested JSON escapes as `RecursionError` (blind, edge, edge claim) | medium | patch | Reproduced: `parse_decision("[" * 200000)` raised `RecursionError`. Now caught and reported as `DecisionError`. Test added. |
| `bytes`/`bytearray` input path untested (blind, edge, gap) | medium | patch | Code accepts bytes but no test passed any. Tests added for both types and invalid UTF-8. |
| Error tests only check that a field name appears (blind, edge, gap) | low | patch | A wrong extra field would still pass. Assertion now compares the exact set of named fields. |
| Missing category/priority/route, empty and scalar inputs untested (blind, edge) | low | patch | Only `rationale` was tested as missing. Rows and inputs added. |
| `...` sentinel in test helper is obscure (blind) | low | patch | Renamed to `MISSING`. |
| No `max_length` on rationale (blind, edge) | low | rejected | Real but unlikely, and a limit is a new rule the spec does not set; fix adds a guard. |
| Duplicate JSON keys: last one wins (edge) | low | rejected | Standard `json` behaviour; a fix needs a custom parser hook. Structured output does not produce duplicates in practice. |
| `NaN`/`Infinity` accepted at parse time (blind) | false | rejected | They cannot be valid enum or string values; the model still rejects them. |
| Non-UTF-8 bytes give a decode-style message (edge) | low | rejected | Still a `DecisionError` naming invalid JSON; only wording differs. |
| Enums not tied to `TRIAGE_POLICY.md` (blind) | low | rejected | Policy is read-only, so drift is unlikely; a policy-parsing test adds more complexity than it saves. |
| Independence test enforces an unsourced rule (blind) | false | rejected | The spec's frozen Boundaries state category and route are independent. |
| `pydantic` may not be a direct dependency; `pythonpath` is a shortcut (blind) | false | rejected | `pyproject.toml` lists `pydantic>=2.8` directly; `pythonpath` is a spec task. |
| Design Notes error wording differs from actual pydantic text (edge claim) | false | rejected | The spec calls the wording illustrative and tests assert field names only. Fix would edit the spec. |
| Verification-gap reviewer did not run pytest (gap, other) | false | rejected | Not a defect. `uv run pytest` runs and passes (46 tests). |
| `.idea/` staged and `.DS_Store` untracked (blind) | low | defer | Pre-existing, not part of this story's diff. |

## Design Notes

`parse_decision` turns pydantic's `ValidationError` into `DecisionError` with one line per failing field, e.g. `category: must be one of billing, bug, access, performance, how-to (got 'refund')`. The wording is illustrative; tests assert the field name appears, not the exact text. That keeps errors readable for a person and for Epic 2's retry-once logic, which needs one exception type to catch.

## Verification

**Commands:**
- `uv run pytest tests/test_triage_schema.py` -- expected: all tests pass
- `uv run python -c "from triage_schema import parse_decision; print(parse_decision('{\"category\":\"billing\",\"priority\":\"P2\",\"route\":\"billing-team\",\"rationale\":\"Double charge.\"}'))"` -- expected: prints the decision
