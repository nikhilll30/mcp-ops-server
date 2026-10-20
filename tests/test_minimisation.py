"""Data minimisation (Part 9): each tool returns exactly the fields its task needs.

The field set of every tool is pinned here with the reason for each field, for the clinic
and the salon, and checked twice: against the declared output schema, and against real
responses on every backend, in both the structured content and the text content. The text
is what a model reads, and the SDK renders it from the raw return value, so before Part 9 a
store returning an extra key leaked it there (the SDK strips it only from the structured
content); the kit now rebuilds the text from the validated result. Adding a field means
adding it here, with a reason. No tool returns a patient's or customer's name, contact
details or any free text about them; the synthetic records hold none.
"""

import asyncio
import json
from typing import Any

import pytest
from conftest import DERM_PATIENT, DERM_REFERRAL, as_principal, first_open_slot
from mcp.server.mcpserver import MCPServer

from mcp_ops_server.server import build_server as build_clinic
from salon_ops import seed as salon_seed
from salon_ops.auth import ALL_SCOPES
from salon_ops.server import build_server as build_salon
from salon_ops.store import SalonStore

APPOINTMENT = {
    "appointment_id": "to cancel it later",
    "status": "booked or cancelled: the outcome",
    "patient_id": "whose appointment it is, to confirm the right patient",
    "provider_id": "which provider, to confirm and to search again",
    "provider_name": "what the agent tells the patient",
    "specialty": "what the agent tells the patient",
    "slot_id": "which slot was taken",
    "start": "when",
    "end": "until when",
    "referral_id": "which referral was used, if any",
}
CLINIC: dict[str, dict[str, dict[str, str]]] = {
    "health": {"": {"status": "liveness", "version": "which build answers"}},
    "search_providers": {
        "": {"providers": "the matches", "count": "how many"},
        "providers[]": {
            "provider_id": "to list slots",
            "name": "to match a requested provider",
            "specialty": "to match a requested specialty",
            "location": "to match a requested clinic",
            "requires_referral": "whether to check referrals before booking",
        },
    },
    "list_open_slots": {
        "": {
            "provider_id": "echo of the query",
            "start_date": "the range searched (end_date may be defaulted)",
            "end_date": "the range searched",
            "slots": "the open slots",
            "count": "how many",
            "truncated": "whether more exist past the limit",
        },
        "slots[]": {"slot_id": "to book", "start": "when", "end": "until when"},
    },
    "book_appointment": {"": APPOINTMENT},
    "cancel_appointment": {"": APPOINTMENT},
    "get_referral_status": {
        "": {"patient_id": "whose referrals", "referrals": "the referrals", "count": "how many"},
        "referrals[]": {
            "referral_id": "to pass to book_appointment",
            "specialty": "which specialty it covers",
            "status": "only approved ones can be used",
            "issued_on": "which is newest",
        },
    },
}
VISIT = {
    "visit_id": "to cancel it later",
    "status": "booked or cancelled",
    "customer_id": "whose visit",
    "stylist_id": "which stylist",
    "stylist_name": "what the agent tells the customer",
    "studio": "where to go",
    "service": "what was booked",
    "opening_id": "which opening was taken",
    "start": "when",
    "end": "until when",
}
SALON: dict[str, dict[str, dict[str, str]]] = {
    "health": CLINIC["health"],
    "search_stylists": {
        "": {"stylists": "the matches", "count": "how many"},
        "stylists[]": {
            "stylist_id": "to list openings",
            "name": "to match a requested stylist",
            "studio": "to match a requested studio",
            "services": "a visit must be for a service offered",
        },
    },
    "list_openings": {
        "": {
            "stylist_id": "echo",
            "start_date": "range searched",
            "end_date": "range searched",
            "openings": "the openings",
            "count": "how many",
            "truncated": "more past the limit",
        },
        "openings[]": {"opening_id": "to book", "start": "when", "end": "until when"},
    },
    "get_customer_visits": {
        "": {"customer_id": "whose visits", "visits": "upcoming visits", "count": "how many"},
        "visits[]": VISIT,
    },
    "book_visit": {"": VISIT},
    "cancel_visit": {"": VISIT},
}


def _schema_fields(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, set[str]]:
    """Field names per path ("" for the top level, "slots[]" for list items) from a JSON
    schema, following $refs."""

    def resolve(s: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in s:
            s = defs[s["$ref"].split("/")[-1]]
        return s

    out: dict[str, set[str]] = {}
    top = resolve(schema)
    out[""] = set(top["properties"])
    for name, prop in top["properties"].items():
        items = prop.get("items")
        if prop.get("type") == "array" and items and "properties" in resolve(items):
            out[f"{name}[]"] = set(resolve(items)["properties"])
    return out


def _value_fields(value: dict[str, Any]) -> dict[str, set[str]]:
    out = {"": set(value)}
    for name, v in value.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            keys = {frozenset(item) for item in v}
            assert len(keys) == 1, f"{name}[] items have different fields: {keys}"
            out[f"{name}[]"] = set(next(iter(keys)))
    return out


def call(server: MCPServer, tool: str, **args: Any) -> dict[str, Any]:  # noqa: ANN401
    """conftest's call, also checking that the text an agent reads is the same object."""
    result = asyncio.run(server.call_tool(tool, args))
    assert result.is_error is False and result.structured_content is not None
    (block,) = result.content
    assert json.loads(block.text) == result.structured_content, f"{tool}: text differs"
    return result.structured_content


def _pinned(table: dict[str, dict[str, str]]) -> dict[str, set[str]]:
    return {path: set(fields) for path, fields in table.items()}


@pytest.mark.parametrize(("domain", "pinned"), [("clinic", CLINIC), ("salon", SALON)])
def test_declared_output_schemas_match_the_pinned_fields(domain: str, pinned: dict) -> None:
    built = build_clinic() if domain == "clinic" else build_salon()
    tools = asyncio.run(built.list_tools())
    assert {t.name for t in tools} == set(pinned)
    for tool in tools:
        schema = tool.output_schema
        assert schema is not None, f"{tool.name} has no output schema"
        assert _schema_fields(schema, schema.get("$defs", {})) == _pinned(pinned[tool.name]), (
            tool.name
        )


def test_clinic_responses_carry_only_the_pinned_fields(server: MCPServer) -> None:
    """On every backend (SQLite, encrypted SQLite, and FHIR in CI)."""
    slot = first_open_slot(server, "PRV-004", DERM_PATIENT)
    calls = {
        "health": {},
        "search_providers": {"specialty": "dermatology"},
        "list_open_slots": {"provider_id": "PRV-004", "start_date": "2026-11-02"},
        "get_referral_status": {"patient_id": DERM_PATIENT},
        "book_appointment": {
            "patient_id": DERM_PATIENT,
            "slot_id": slot,
            "referral_id": DERM_REFERRAL,
        },
    }
    seen = {tool: call(server, tool, **args) for tool, args in calls.items()}
    seen["cancel_appointment"] = call(
        server,
        "cancel_appointment",
        appointment_id=seen["book_appointment"]["appointment_id"],
        patient_id=DERM_PATIENT,
    )
    for tool, value in seen.items():
        fields = _value_fields(value)
        pinned = _pinned(CLINIC[tool])
        assert fields == {p: pinned[p] for p in fields}, tool


def test_salon_responses_carry_only_the_pinned_fields() -> None:
    server = build_salon(SalonStore())
    with as_principal("salon-staff", list(ALL_SCOPES)):
        stylists = call(server, "search_stylists", service="cut")
        stylist = stylists["stylists"][0]["stylist_id"]
        day = salon_seed.SALON_NOW.date().isoformat()
        openings = call(server, "list_openings", stylist_id=stylist, start_date=day)
        opening = openings["openings"][0]["opening_id"]
        booked = call(
            server, "book_visit", customer_id="CUS-0005", opening_id=opening, service="cut"
        )
        seen = {
            "health": call(server, "health"),
            "search_stylists": stylists,
            "list_openings": openings,
            "book_visit": booked,
            "get_customer_visits": call(server, "get_customer_visits", customer_id="CUS-0005"),
            "cancel_visit": call(
                server, "cancel_visit", visit_id=booked["visit_id"], customer_id="CUS-0005"
            ),
        }
    for tool, value in seen.items():
        fields = _value_fields(value)
        pinned = _pinned(SALON[tool])
        assert fields == {p: pinned[p] for p in fields}, tool


def test_an_undeclared_field_from_a_store_never_reaches_the_agent() -> None:
    """The mutation that found the gap: a backend adds a field. Before the kit rebuilt the
    text content, ``location`` reached the agent in the text (not in structured content)."""
    from mcp_ops_server.backends.sqlite import SqliteBackend

    class Leaky(SqliteBackend):
        def search_providers(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            out = super().search_providers(*args, **kwargs)
            out["providers"] = [p | {"home_address": "1 Synthetic Way"} for p in out["providers"]]
            return out | {"internal_note": "x"}

    result = asyncio.run(build_clinic(Leaky()).call_tool("search_providers", {}))
    assert "home_address" not in result.content[0].text
    assert "internal_note" not in result.content[0].text
    assert "home_address" not in json.dumps(result.structured_content)
