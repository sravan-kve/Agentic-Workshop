---
id: SPEC-epic-1
companions: [../../../TRIAGE_POLICY.md, ../../../mcp/triage_server.py]
sources: [../../../INTENT.md]
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Epic 1: triage data and schema

## Why

The workshop's triage agent (Epic 2) and its eval (Epic 3) have nothing to stand on yet: no fixed shape for a triage decision, and no local database for the MCP server to read. This epic is the vision's foundation, laid first so attendees can build the agent against a stable contract. It affects the workshop attendees and every downstream epic, which reads `app.db` and validates decisions against this schema.

## Capabilities

- **CAP-1**
  - **intent:** A triage decision is accepted only if it is a JSON object with a valid category, priority, route and rationale; anything else is rejected with a clear error.
  - **success:** `{"category": "billing", "priority": "P2", "route": "billing-team", "rationale": "<one sentence>"}` validates. Each of these is rejected with an error naming the offending field: category `refund`, priority `P0`, route `sales-team`, a missing rationale, and a non-object such as a JSON list. Valid values are categories billing, bug, access, performance, how-to; priorities P1 to P4; routes billing-team, bug-team, access-team, performance-team, how-to-team.

- **CAP-2**
  - **intent:** A person can load the seed data into a local SQLite database with one command.
  - **success:** After `uv run python load_seed.py` on a checkout with no `app.db`, `app.db` exists with tables `tickets` and `customers`. Their columns match the CSV headers, and each holds one row per row of its CSV. The queries in `mcp/triage_server.py` return rows, e.g. `T-1042` from `tickets` and `C-77` from `customers`.

- **CAP-3**
  - **intent:** Loading is repeatable: running the loader again leaves the same database.
  - **success:** Running `load_seed.py` twice in a row exits 0 both times, and after the second run both tables hold exactly the same rows as after the first, with no duplicates.

## Constraints

- Python 3.12 or newer, managed with uv; add packages with `uv add`, never pip.
- Everything under `seed/` is read-only; the loader only reads it.
- No network calls and no API keys anywhere in this epic.
- Table and column names are fixed by `mcp/triage_server.py`: `tickets(ticket_id, customer_id, created_at, text)` and `customers(customer_id, name, plan, open_tickets)`. That server is read-only and must keep working unchanged.
- `app.db` is a generated file and is never committed.

## Non-goals

- The agent, the MCP tools, evals and any user interface.
- Changing `mcp/triage_server.py`, `TRIAGE_POLICY.md` or anything in `seed/`.

## Success signal

On a fresh checkout, `uv run python load_seed.py` (run twice) builds an `app.db` that `mcp/triage_server.py` can read as-is. A well-formed triage decision passes the schema, and a decision with an off-list category, priority or route, or a missing rationale, fails with an error that says why.

## Assumptions

- Re-running the loader drops and rebuilds the tables rather than appending, since that is what makes the second run identical.
- `open_tickets` is stored as an integer, because `TRIAGE_POLICY.md`'s Enterprise rule compares it to 3.
- Unknown extra fields in a decision are rejected, reading "anything else is rejected" strictly.
- The schema is an importable Python definition, since Epic 2 validates its structured output against it.

## Open Questions

- Must category and route agree, as in the `TRIAGE_POLICY.md` table (billing → billing-team), or are they independent fields that each just need a valid value?
- Is "one-sentence rationale" enforced beyond a non-empty string?
- Where should the schema live (module and filename) so Epic 2 can import it?
