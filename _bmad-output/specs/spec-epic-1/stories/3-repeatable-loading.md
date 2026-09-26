---
title: 'Repeatable loading'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: '5883eac2ceb6b7cc0cc5e9fd8c3d2dc8b77dd1f4'
route: 'oneshot'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `load_seed.py` fails with "table already exists" on a second run, so the database is not repeatable. Covers CAP-3 of the Epic 1 spec.

**Approach:** Make the loader drop and rebuild both tables, as the Epic 1 spec assumes, so two runs in a row exit 0 and leave both tables with exactly the same rows and no duplicates. The rebuild happens in one transaction, so a failed load keeps the previous database. Nothing else changes: `seed/`, `mcp/triage_server.py` and the table shapes stay as they are.

</frozen-after-approval>

## Implementation Notes

`load()` now runs `BEGIN`, then `DROP TABLE IF EXISTS` and `CREATE TABLE` per table, then the inserts, in one transaction. CSVs are read and validated before the database is opened. `main()` passes `DB_PATH` explicitly so tests can redirect it. Tests cover two runs with identical rows, `main()` twice, stale rows replaced, and a bad CSV on re-run leaving the old database intact. The failed-re-run test fails at CSV parsing, before the transaction starts; it does not exercise a mid-insert rollback.
