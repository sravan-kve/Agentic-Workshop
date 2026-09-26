"""The triage decision schema: what a valid triage decision looks like.

Usage: from triage_schema import TriageDecision, parse_decision
"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

Category = Literal["billing", "bug", "access", "performance", "how-to"]
Priority = Literal["P1", "P2", "P3", "P4"]
Route = Literal["billing-team", "bug-team", "access-team", "performance-team", "how-to-team"]


class DecisionError(ValueError):
    """A triage decision that does not match the schema. The message names each bad field."""


class TriageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: Category
    priority: Priority
    route: Route
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _short(value: object, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def parse_decision(raw: object) -> TriageDecision:
    """Validate a decision given as a JSON string or an already-parsed value."""
    data = raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise DecisionError(f"decision is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise DecisionError(f"decision must be a JSON object, got {type(data).__name__}")
    try:
        return TriageDecision.model_validate(data)
    except ValidationError as err:
        lines = []
        for problem in err.errors():
            field = ".".join(str(part) for part in problem["loc"])
            if problem["type"] == "missing":
                lines.append(f"{field}: field is required")
            elif problem["type"] == "extra_forbidden":
                lines.append(f"{field}: unknown field")
            else:
                lines.append(f"{field}: {problem['msg']} (got {_short(problem['input'])})")
        raise DecisionError("invalid triage decision:\n" + "\n".join(lines)) from err
