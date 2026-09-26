import json
import re

import pytest

from triage_schema import DecisionError, TriageDecision, parse_decision

VALID = {
    "category": "billing",
    "priority": "P2",
    "route": "billing-team",
    "rationale": "Double charge, so P2.",
}


MISSING = object()


def with_change(**changes):
    decision = {**VALID, **changes}
    return {key: value for key, value in decision.items() if value is not MISSING}


def test_valid_decision_from_dict_and_json_string():
    from_dict = parse_decision(VALID)
    from_json = parse_decision(json.dumps(VALID))
    assert isinstance(from_dict, TriageDecision)
    assert from_dict == from_json
    assert from_dict.category == "billing"


@pytest.mark.parametrize("category", ["billing", "bug", "access", "performance", "how-to"])
def test_every_category_is_accepted(category):
    assert parse_decision(with_change(category=category)).category == category


@pytest.mark.parametrize("priority", ["P1", "P2", "P3", "P4"])
def test_every_priority_is_accepted(priority):
    assert parse_decision(with_change(priority=priority)).priority == priority


@pytest.mark.parametrize("route", ["billing-team", "bug-team", "access-team", "performance-team", "how-to-team"])
def test_every_route_is_accepted(route):
    assert parse_decision(with_change(route=route)).route == route


def test_category_and_route_are_independent():
    assert parse_decision(with_change(category="bug", route="billing-team")).route == "billing-team"


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"category": "refund"}, ["category"]),
        ({"priority": "P0"}, ["priority"]),
        ({"route": "sales-team"}, ["route"]),
        ({"category": MISSING}, ["category"]),
        ({"priority": MISSING}, ["priority"]),
        ({"route": MISSING}, ["route"]),
        ({"rationale": MISSING}, ["rationale"]),
        ({"rationale": "   "}, ["rationale"]),
        ({"rationale": None}, ["rationale"]),
        ({"priority": 2}, ["priority"]),
        ({"category": "Billing"}, ["category"]),
        ({"priority": "p2"}, ["priority"]),
        ({"category": "refund", "priority": "P0"}, ["category", "priority"]),
        ({"note": "x"}, ["note"]),
    ],
)
def test_bad_decision_is_rejected_naming_the_fields(changes, fields):
    with pytest.raises(DecisionError) as err:
        parse_decision(with_change(**changes))
    named = set(re.findall(r"^(\w+):", str(err.value), re.M))
    assert named == set(fields)


@pytest.mark.parametrize("raw", [[], "[]", "null", "1", '"x"', 42, None])
def test_non_object_is_rejected(raw):
    with pytest.raises(DecisionError, match="JSON object"):
        parse_decision(raw)


def test_invalid_json_is_rejected():
    with pytest.raises(DecisionError, match="not valid JSON"):
        parse_decision("{not json")


def test_rationale_is_stored_trimmed():
    assert parse_decision(with_change(rationale="  Double charge.  ")).rationale == "Double charge."


def test_decision_error_is_a_value_error():
    assert issubclass(DecisionError, ValueError)


def test_empty_input_is_rejected():
    for raw in ("", b"", {}):
        with pytest.raises(DecisionError):
            parse_decision(raw)


@pytest.mark.parametrize("wrap", [bytes, bytearray])
def test_json_bytes_are_accepted(wrap):
    assert parse_decision(wrap(json.dumps(VALID).encode())) == parse_decision(VALID)


def test_invalid_utf8_bytes_are_rejected():
    with pytest.raises(DecisionError, match="not valid JSON"):
        parse_decision(b"\xff")


def test_deeply_nested_json_is_a_decision_error():
    with pytest.raises(DecisionError, match="not valid JSON"):
        parse_decision("[" * 200_000)


def test_long_bad_value_is_truncated_in_the_error():
    with pytest.raises(DecisionError) as err:
        parse_decision(with_change(category="x" * 500))
    assert len(str(err.value)) < 300
