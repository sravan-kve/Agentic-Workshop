---
title: 'Seed loader'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: '6e2eda5bc457bd024b18d0f58dbe1dd7c18a843b'
route: 'oneshot'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The MCP server reads `app.db`, but nothing creates it, so the Epic 2 agent has no data to triage. Covers CAP-2 of the Epic 1 spec.

**Approach:** Add `load_seed.py` at the repo root. `uv run python load_seed.py` reads `seed/tickets.csv` and `seed/customers.csv` and writes `app.db` with tables `tickets(ticket_id, customer_id, created_at, text)` and `customers(customer_id, name, plan, open_tickets)`, one row per CSV row. `open_tickets` is an integer. The loader only reads `seed/`, makes no network calls, and never commits `app.db` (already gitignored). Idempotence (CAP-3) is story 3.

</frozen-after-approval>

## Implementation Notes

Table columns are the fixed names from `mcp/triage_server.py`; the loader checks each CSV header against them and fails before writing anything on a mismatch. A test asserts the exact names. A story-2 run on an existing `app.db` is not specified; story 3 makes re-runs repeatable.

## Review Triage Log

Code review against `spec/sravan-epic-1` (four layers): no blocking findings. Patched: unclosed connection, silently skipping MCP test (`importorskip`), `sys.path` leak, unordered row comparison, header test only covering tickets, test file encoding. Deferred to story 3: re-run on an existing `app.db` fails with "table already exists". Rejected as low or out of scope: primary keys and duplicate/orphan checks (new rules the spec does not set), partial tables after a failed insert, BOM and short-row handling, friendlier CLI errors.
