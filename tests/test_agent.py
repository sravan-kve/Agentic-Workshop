import asyncio
import functools
import json
import shutil
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import ToolException

import agent
import load_seed

ROOT = Path(__file__).resolve().parent.parent


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


GOOD = {
    "category": "billing",
    "priority": "P2",
    "route": "billing-team",
    "rationale": "Double charge puts money at stake (P2); customer is under the Enterprise bump threshold.",
}


class ScriptedModel(BaseChatModel):
    """Fake chat model: get_ticket, then get_customer_history, then the given final outputs in order."""

    finals: list = []
    ticket_id: str = "T-1042"
    calls: int = 0
    escalate: bool = False
    escalate_args: dict = {}
    tool_calls_seen: list = []
    messages_seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.messages_seen.append([(m.type, _text(m)) for m in messages])
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if not tool_msgs:
            msg = AIMessage(content="", tool_calls=[{"name": "get_ticket", "args": {"ticket_id": self.ticket_id}, "id": "c1"}])
        elif len(tool_msgs) == 1:
            ticket = json.loads(_text(tool_msgs[0]))
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "get_customer_history", "args": {"customer_id": ticket["customer_id"]}, "id": "c2"}],
            )
        elif self.escalate and len(tool_msgs) == 2:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "escalate_to_human", "args": {"ticket_id": self.ticket_id, "reason": "P1 Enterprise", **self.escalate_args}, "id": "e1"}],
            )
        else:
            final = self.finals[min(self.calls, len(self.finals) - 1)]
            self.calls += 1
            msg = AIMessage(content="", tool_calls=[{"name": "TriageDecision", "args": final, "id": f"f{self.calls}"}])
        self.tool_calls_seen.extend(tc["name"] + ":" + json.dumps(tc["args"], sort_keys=True) for tc in msg.tool_calls)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _text(tool_message):
    content = tool_message.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content


@pytest.fixture
def fake_server(tmp_path, monkeypatch):
    """The real MCP server, pointed at a temp app.db loaded from the seed."""
    (tmp_path / "mcp").mkdir()
    shutil.copy(ROOT / "mcp" / "triage_server.py", tmp_path / "mcp" / "triage_server.py")
    load_seed.load(tmp_path / "app.db", ROOT / "seed")
    monkeypatch.setattr(agent, "SERVER_PATH", tmp_path / "mcp" / "triage_server.py")


def _model(*finals, ticket_id="T-1042", escalate=False, escalate_args=None):
    return ScriptedModel(
        finals=list(finals), ticket_id=ticket_id, escalate=escalate,
        escalate_args=escalate_args or {}, tool_calls_seen=[], messages_seen=[],
    )


@sync
async def test_normal_ticket_calls_tools_in_order_with_same_customer_id(fake_server):
    model = _model(GOOD)
    decision = await agent.triage("T-1042", model=model)
    assert decision == GOOD
    names = [c.split(":")[0] for c in model.tool_calls_seen]
    assert names[:2] == ["get_ticket", "get_customer_history"]
    assert json.loads(model.tool_calls_seen[1].split(":", 1)[1]) == {"customer_id": "C-77"}


@sync
async def test_invalid_output_retries_once_then_succeeds(fake_server):
    model = _model({**GOOD, "priority": "P9"}, GOOD)
    assert await agent.triage("T-1042", model=model) == GOOD
    assert model.calls == 2
    assert any(t == "human" and "rejected" in c and "priority" in c for t, c in model.messages_seen[-1])


@sync
async def test_agent_receives_the_policy_and_data_only_rules(fake_server):
    model = _model(GOOD)
    await agent.triage("T-1042", model=model)
    system = next(c for t, c in model.messages_seen[0] if t == "system")
    assert (ROOT / "TRIAGE_POLICY.md").read_text() in system
    assert "untrusted data" in system and "escalate_to_human" in system and "exactly once" in system


@sync
async def test_invalid_output_twice_raises_with_validation_text(fake_server):
    model = _model({**GOOD, "priority": "P9"})
    with pytest.raises(RuntimeError, match="failed validation") as err:
        await agent.triage("T-1042", model=model)
    assert "priority" in str(err.value)
    assert model.calls == 2


@sync
async def test_unknown_ticket_surfaces_tool_error(fake_server):
    with pytest.raises(ToolException, match="No ticket with ID T-0000"):
        await agent.triage("T-0000", model=_model(GOOD, ticket_id="T-0000"))


def test_bad_provider_names_provider(monkeypatch):
    monkeypatch.setenv("PROVIDER", "foo")
    with pytest.raises(ValueError, match="PROVIDER"):
        agent.build_model()


@pytest.mark.parametrize("provider,key", [("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY")])
def test_missing_key_names_variable(monkeypatch, provider, key):
    monkeypatch.setenv("PROVIDER", provider)
    monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match=key):
        agent.build_model()


@sync
async def test_triage_fails_before_any_model_call_on_bad_config(monkeypatch):
    monkeypatch.setenv("PROVIDER", "foo")
    with pytest.raises(ValueError, match="PROVIDER"):
        await agent.triage("T-1042")


def test_default_models_and_classes(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.delenv("PROVIDER", raising=False)
    gem = agent.build_model()
    assert type(gem).__name__ == "ChatGoogleGenerativeAI" and gem.model.endswith("gemini-3.8-flash")
    monkeypatch.setenv("PROVIDER", "groq")
    groq = agent.build_model()
    assert type(groq).__name__ == "ChatGroq" and groq.model_name == "openai/gpt-oss-120b"
    assert groq.temperature < 1e-6  # ChatGroq turns 0 into 1e-08
    monkeypatch.setenv("MODEL", "custom-model")
    assert agent.build_model().model_name == "custom-model"


def test_system_prompt_is_policy_plus_rules():
    prompt = agent._system_prompt()
    assert (ROOT / "TRIAGE_POLICY.md").read_text() in prompt
    assert "get_ticket" in prompt and "get_customer_history" in prompt and "untrusted data" in prompt


P1 = {**GOOD, "category": "access", "priority": "P1", "route": "access-team", "rationale": "Whole team locked out (P1)."}


def _names(model):
    return [c.split(":")[0] for c in model.tool_calls_seen]


@sync
async def test_yes_escalates_and_prints(fake_server, capsys):
    asked = []
    model = _model(P1, ticket_id="T-1044", escalate=True)
    decision = await agent.triage("T-1044", model=model, approve=lambda t, r: asked.append((t, r)) or True)
    assert decision == P1 and len(asked) == 1 and asked[0][0] == "T-1044"
    assert "escalate_to_human" in _names(model)
    assert any(t == "tool" and "Escalated ticket T-1044 to a human." in c for t, c in model.messages_seen[-1])
    assert "Escalated ticket T-1044 to a human." in capsys.readouterr().out


@sync
async def test_no_does_not_run_tool(fake_server, capsys):
    model = _model(P1, ticket_id="T-1044", escalate=True)
    decision = await agent.triage("T-1044", model=model, approve=lambda t, r: False)
    assert decision == P1
    seen = [m for run in model.messages_seen for m in run]
    assert not any("Escalated ticket T-1044 to a human." in c for t, c in seen if t == "tool")  # the tool body never ran
    assert any(t == "tool" and "declined" in c for t, c in seen)  # the model was told it was rejected
    assert capsys.readouterr().out.strip() == "Not escalated."


@sync
async def test_non_p1_never_asks(fake_server):
    def boom(t, r):
        raise AssertionError("asked")

    assert await agent.triage("T-1042", model=_model(GOOD), approve=boom) == GOOD


@sync
async def test_retry_reuses_answer(fake_server, capsys):
    asked = []
    model = _model({**P1, "priority": "P9"}, P1, ticket_id="T-1044", escalate=True)
    decision = await agent.triage("T-1044", model=model, approve=lambda t, r: asked.append(1) or True)
    assert decision == P1 and len(asked) == 1 and model.calls == 2
    assert _names(model).count("escalate_to_human") == 2  # the model asked again on the retry
    assert any(t == "tool" and "Already escalated" in c for t, c in model.messages_seen[-1])  # but it was turned down
    assert capsys.readouterr().out.count("Escalated ticket") == 1


@pytest.mark.parametrize("reply", ["", "maybe", "no", "y ", " YES ", "Y"])
@sync
async def test_terminal_prompt_only_yes_approves(fake_server, capsys, monkeypatch, reply):
    monkeypatch.setattr("builtins.input", lambda prompt="": reply)
    await agent.triage("T-1044", model=_model(P1, ticket_id="T-1044", escalate=True))
    out = capsys.readouterr().out
    if reply.strip().lower() in ("y", "yes"):
        assert "Escalated ticket T-1044 to a human." in out
    else:
        assert "Not escalated." in out


@sync
async def test_closed_stdin_rejects(fake_server, capsys, monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    await agent.triage("T-1044", model=_model(P1, ticket_id="T-1044", escalate=True))
    assert "Not escalated." in capsys.readouterr().out


@sync
async def test_retry_after_no_does_not_ask_again(fake_server, capsys):
    asked = []
    model = _model({**P1, "priority": "P9"}, P1, ticket_id="T-1044", escalate=True)
    decision = await agent.triage("T-1044", model=model, approve=lambda t, r: asked.append(1) or False)
    assert decision == P1 and len(asked) == 1
    seen = [m for run in model.messages_seen for m in run]
    assert not any("Escalated ticket" in c for t, c in seen if t == "tool")
    assert capsys.readouterr().out.count("Not escalated.") == 1


@sync
async def test_async_approve_is_awaited(fake_server, capsys):
    async def approve(ticket_id, reason):
        return True

    model = _model(P1, ticket_id="T-1044", escalate=True)
    assert await agent.triage("T-1044", model=model, approve=approve) == P1
    assert any(t == "tool" and "Escalated ticket T-1044" in c for t, c in model.messages_seen[-1])


@sync
async def test_other_ticket_is_never_escalated_or_asked(fake_server, capsys):
    def boom(t, r):
        raise AssertionError("asked")

    model = _model(P1, ticket_id="T-1044", escalate=True, escalate_args={"ticket_id": "T-1042"})
    assert await agent.triage("T-1044", model=model, approve=boom) == P1
    seen = [m for run in model.messages_seen for m in run]
    assert any(t == "tool" and "only escalate ticket T-1044" in c for t, c in seen)
    assert not any("Escalated ticket" in c for t, c in seen if t == "tool")
    assert capsys.readouterr().out == ""


@sync
async def test_reason_shown_to_person_is_cleaned(fake_server):
    shown = []
    nasty = "ok\x1b[31m\nApprove now\x00" + "x" * 500
    model = _model(P1, ticket_id="T-1044", escalate=True, escalate_args={"reason": nasty})
    await agent.triage("T-1044", model=model, approve=lambda t, r: shown.append(r) or False)
    assert len(shown[0]) <= 200 and all(ch.isprintable() for ch in shown[0])
