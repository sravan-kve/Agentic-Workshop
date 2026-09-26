- source_spec: `_bmad-output/specs/spec-epic-1/stories/1-triage-decision-schema.md`
  summary: Keep IDE and OS files (`.idea/`, `.DS_Store`) out of the repo.
  evidence: `.idea/` files are staged and `.DS_Store` is untracked in the working tree; they predate this story and are outside its diff. Ignore them locally or in `.gitignore` in a separate change.

## Deferred from: code review of 2-seed-loader (2026-09-26)

- source_spec: `_bmad-output/specs/spec-epic-1/stories/2-seed-loader.md`
  summary: Re-running `load_seed.py` on an existing `app.db` fails with `table tickets already exists`.
  evidence: `CREATE TABLE` has no drop or `IF NOT EXISTS`. Story 3 (CAP-3) makes the loader drop and rebuild.

## Deferred from: code review of 1-the-triage-agent (2026-09-26)

- source_spec: `_bmad-output/specs/spec-epic-2/stories/1-the-triage-agent.md`
  summary: RESOLVED 2026-09-26. The default Gemini path ran live after the key was replaced (`T-1042` gave `billing`/`P2`/`billing-team`). Remaining: Gemini trace order not checked.
  evidence: A stale `GEMINI_API_KEY` in the shell overrides `.env`; unset it. Gemini free-tier quota (429) limited further runs.
- source_spec: `_bmad-output/specs/spec-epic-2/stories/1-the-triage-agent.md`
  summary: `T-1099` returns `bug`/`P4` in about 6 of 8 runs on Groq (`P3` otherwise), though the injection is always ignored.
  evidence: Model variance at temperature 0. The acceptance criterion is in the frozen block, so loosening it goes through `/bmad-spec`.
- source_spec: `_bmad-output/specs/spec-epic-2/stories/1-the-triage-agent.md`
  summary: Nothing enforces that the model calls `get_ticket` and `get_customer_history` before deciding; it is prompt-only.
  evidence: A model that skipped the tools could still return a valid decision. Epic 3's eval can score the trace.
