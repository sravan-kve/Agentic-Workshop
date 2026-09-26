import asyncio
import functools
import json
import shutil
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

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
    tool_calls_seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if not tool_msgs:
            msg = AIMessage(content="", tool_calls=[{"name": "get_ticket", "args": {"ticket_id": self.ticket_id}, "id": "c1"}])
        elif len(tool_msgs) == 1:
            ticket = json.loads(_text(tool_msgs[0]))
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "get_customer_history", "args": {"customer_id": ticket["customer_id"]}, "id": "c2"}],
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


def _model(*finals, ticket_id="T-1042"):
    return ScriptedModel(finals=list(finals), ticket_id=ticket_id, tool_calls_seen=[])


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


@sync
async def test_invalid_output_twice_raises_with_validation_text(fake_server):
    model = _model({**GOOD, "priority": "P9"})
    with pytest.raises(RuntimeError, match="failed validation") as err:
        await agent.triage("T-1042", model=model)
    assert "priority" in str(err.value)
    assert model.calls == 2


@sync
async def test_unknown_ticket_surfaces_tool_error(fake_server):
    with pytest.raises(Exception, match="No ticket with ID T-0000"):
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
    monkeypatch.setenv("MODEL", "custom-model")
    assert agent.build_model().model_name == "custom-model"


def test_system_prompt_is_policy_plus_rules():
    prompt = agent._system_prompt()
    assert (ROOT / "TRIAGE_POLICY.md").read_text() in prompt
    assert "get_ticket" in prompt and "get_customer_history" in prompt and "untrusted data" in prompt
