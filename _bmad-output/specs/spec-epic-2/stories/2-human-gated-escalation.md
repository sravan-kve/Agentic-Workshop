---
title: 'Human-gated escalation'
type: 'feature'
created: '2026-09-26'
status: 'in-review'
baseline_commit: 'c6d295af60afc32a66461b5093fec9df200f4cb3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-epic-2/SPEC.md'
  - '{project-root}/TRIAGE_POLICY.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The policy says to escalate a P1 Enterprise ticket to a person, but the agent has no `escalate_to_human` tool, and story 1's prompt tells it the tool does not exist. Nothing pauses for a human. Covers CAP-5 of the Epic 2 spec.

**Approach:** Add a local `escalate_to_human` tool to the agent (not to `mcp/triage_server.py`) and gate it with LangChain's `HumanInTheLoopMiddleware`, so every call pauses the run for a yes or no. The default prompt is a terminal question; `triage()` also takes an optional `approve` callable so callers without a person, such as the Epic 3 eval, can supply their own answer. Only an explicit "yes" runs the tool.

## Boundaries & Constraints

**Always:**
- `escalate_to_human` is defined in `agent.py` as a local tool taking `ticket_id` and `reason`. It records nothing outside the run and returns a short confirmation string; there is no real ticketing system.
- Every call to it is gated with `HumanInTheLoopMiddleware` (`approve` and `reject` only, no edit), with a checkpointer and a `thread_id`.
- Only the exact answers `y` or `yes` (any case, trimmed) approve. Anything else, including an empty answer or a closed stdin, rejects. Nothing escalates without an explicit yes.
- The decision `triage()` returns is unchanged: four fields, valid under `parse_decision`, whether approved, rejected or never asked. Whether it escalated is not a decision field.
- After the person answers, the terminal prints one line: `Escalated ticket <id> to a human.` or `Not escalated.`
- The system prompt tells the model to call `escalate_to_human` once, when the policy's rule applies (final priority P1 and an Enterprise customer), and never otherwise. This replaces story 1's "not available" line.
- If the retry from story 1 runs the agent again, a person already asked is not asked again: the first answer is reused.

**Never:** edit `triage_schema.py`, `mcp/triage_server.py`, `TRIAGE_POLICY.md`, `seed/`, `load_seed.py` or the MLflow lines in `run_agent.py`; add fields to the decision; add packages; auto-approve without a person or an explicit `approve` callable.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| P1 Enterprise, yes | `T-1044`, answer `yes` | Run pauses, then completes; tool runs; prints `Escalated ticket T-1044 to a human.`; valid decision returned | N/A |
| P1 Enterprise, no | `T-1044`, answer `no` | Run completes; tool does not run; prints `Not escalated.`; valid decision returned | N/A |
| Unclear answer | empty line, `maybe`, or closed stdin | Treated as no | N/A |
| Not P1 Enterprise | `T-1042` | Never pauses, never asks | N/A |
| Supplied `approve` | `approve` callable passed to `triage()` | Used instead of the terminal | N/A |
| Retry after invalid output | Person answered, then output fails validation | Second attempt reuses the answer, no second prompt | Story 1's error after the second failure |

</frozen-after-approval>

## Code Map

- `agent.py` -- `triage()`, `_load_tools()`, `TOOL_RULES` (line about `escalate_to_human` being unavailable), `create_agent(...)` call. Extend here; keep the story 1 retry and error behaviour.
- `run_agent.py` -- calls `asyncio.run(triage(ticket_id))`; needs no change if the terminal prompt is the default inside `agent.py`.
- `TRIAGE_POLICY.md` -- escalation rule (P1 and Enterprise).
- `tests/test_agent.py` -- `ScriptedModel` and `fake_server` fixtures to reuse; the scripted model needs a way to call `escalate_to_human`.
- Installed API: `langchain.agents.middleware.HumanInTheLoopMiddleware(interrupt_on={...})`, `langgraph.checkpoint.memory.InMemorySaver`, resume with `langgraph.types.Command(resume={"decisions": [...]})`, interrupts under `result["__interrupt__"]`.
- Live check ticket: `T-1044` (Globex, Enterprise, 3 open tickets, whole team locked out) should resolve P1.

## Tasks & Acceptance

**Execution:**
- [x] `agent.py` -- add `escalate_to_human`, the middleware gate, checkpointer and thread, the interrupt-resume loop with the terminal prompt and optional `approve`, the reused answer across retry, and the prompt change -- CAP-5
- [x] `tests/test_agent.py` -- cover the matrix offline with the scripted model and a stub `approve`; one test drives the default prompt with a patched `input` -- proves nothing escalates without yes

**Acceptance Criteria:**
- Given a ticket that resolves to P1 with an Enterprise customer, when `run_agent.py` runs, then it pauses for a yes/no at the terminal; `yes` completes as escalated, `no` completes without escalating.
- Given any answer other than an explicit yes, then the tool never runs.
- Given `uv run pytest` with no network and no keys, then all tests pass.
- Given story 1's cases (`T-1042`, `T-1099`), then their behaviour is unchanged and they never pause.

## Implementation Notes

- `agent.py`: local `escalate_to_human` tool, `HumanInTheLoopMiddleware` (approve/reject) with `InMemorySaver` and a per-attempt `thread_id`, an interrupt-resume loop in `triage()`, optional `approve` (sync or async), terminal default `_terminal_approve`, and the first answer reused across the story 1 retry.
- Live results (2026-09-26, Groq): `T-1044` + `yes` printed `Escalated ticket T-1044 to a human.` with a valid `access`/`P1`/`access-team` decision; `no` and closed stdin printed `Not escalated.`; `T-1042` never prompted. In MLflow only the `yes` trace holds an `escalate_to_human` span; all traces finished OK.
- Known noise: MLflow 3.16.1's LangChain tracer lacks `on_interrupt` and `on_resume`, so each pause logs `Error in MlflowLangchainTracer.on_interrupt callback: AttributeError`. The run and traces are fine. The fix is in MLflow or a version bump, not in `run_agent.py`'s protected lines.
- The "no" test checks what the model receives (a rejection message, no tool result) rather than patching the tool.

## Spec Change Log

## Review Triage Log

## Design Notes

The Epic 1 schema is read-only and rejects extra fields, and `run_agent.py` prints the returned dict, so "escalated" is shown as a terminal line and in the MLflow trace (the tool call and its result), not as a decision field. Epic 3's eval can pass its own `approve` so it never blocks on stdin.

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass, offline
- `PROVIDER=groq uv run python run_agent.py T-1044` and answer `yes` -- expected: valid decision, `Escalated ticket T-1044 to a human.`
- Same command, answer `no` -- expected: valid decision, `Not escalated.`
- `PROVIDER=groq uv run python run_agent.py T-1042` -- expected: no prompt

**Manual checks (if no CLI):**
- In the MLflow UI, confirm the `escalate_to_human` call appears in the `T-1044` trace only when answered yes.
