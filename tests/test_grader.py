"""Unit tests for the trajectory grader. The grader decides pass/fail for every eval,
so each check is tested on its own here, not only through the suite."""

import pytest

from evals.grader import (
    Step,
    check_budget,
    check_forbidden,
    check_patient_scope,
    check_required,
    check_state,
    grade,
    matches,
    render,
    resolve_path,
    resolve_refs,
)
from evals.harness import diff


def _step(tool: str, ok: bool = True, **args: object) -> Step:
    return Step(tool, dict(args), ok)


# ---- matchers and references --------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        ("PAT-0001", "PAT-0001", True),
        ("PAT-0001", "PAT-0002", False),
        ({"$icontains": "juniper"}, "Dr. JUNIPER", True),
        ({"$icontains": "juniper"}, "Dr. Birch", False),
        ({"$icontains": "x"}, None, False),
        ({"$prefix": "2026-11-04"}, "2026-11-04T09:00:00", True),
        ({"$prefix": "2026-11-04"}, "2026-11-05T09:00:00", False),
        ({"$in": ["a", "b"]}, "b", True),
        ({"$in": ["a", "b"]}, "c", False),
        ({"$any": True}, None, True),
        ({"a": 1, "b": 2}, {"a": 1, "b": 2}, True),  # multi-key dicts are literals
    ],
)
def test_matches(expected: object, actual: object, result: bool) -> None:
    assert matches(expected, actual) is result


def test_resolve_path_and_refs() -> None:
    ctx = {
        "setup": [{"appointment_id": "APT-00007"}],
        "steps": [None, {"slots": [{"slot_id": "S"}]}],
    }
    assert resolve_path("setup[0].appointment_id", ctx) == "APT-00007"
    assert resolve_path("steps[1].slots[0].slot_id", ctx) == "S"
    nested = {"a": [{"$ref": "steps[1].slots[0].slot_id"}], "b": "literal"}
    assert resolve_refs(nested, ctx) == {"a": ["S"], "b": "literal"}
    assert render("Cancel {setup[0].appointment_id} now", ctx) == "Cancel APT-00007 now"


# ---- step checks -----------------------------------------------------------------------------

STEPS = [
    _step("get_referral_status", patient_id="PAT-0001"),
    _step("list_open_slots", provider_id="PRV-004"),
    _step("book_appointment", patient_id="PAT-0001", referral_id="REF-0001"),
]
CHECK_THEN_BOOK = [
    {"tool": "get_referral_status", "args": {"patient_id": "PAT-0001"}, "ok": True},
    {"tool": "book_appointment", "args": {"patient_id": "PAT-0001"}, "ok": True},
]


def test_required_in_order() -> None:
    assert check_required({"required": CHECK_THEN_BOOK}, STEPS).passed


def test_required_out_of_order_fails() -> None:
    check = check_required({"required": CHECK_THEN_BOOK}, list(reversed(STEPS)))
    assert not check.passed and "out of order" in check.detail


def test_required_unordered_accepts_any_order() -> None:
    expect = {"required": CHECK_THEN_BOOK, "ordered": False}
    assert check_required(expect, list(reversed(STEPS))).passed


def test_required_respects_ok_flag() -> None:
    failed_booking = [*STEPS[:2], _step("book_appointment", ok=False, patient_id="PAT-0001")]
    assert not check_required({"required": CHECK_THEN_BOOK}, failed_booking).passed


def test_forbidden() -> None:
    expect = {"forbidden": [{"tool": "book_appointment", "ok": True}]}
    assert not check_forbidden(expect, STEPS).passed
    assert check_forbidden(expect, STEPS[:2]).passed
    refused = [_step("book_appointment", ok=False, patient_id="PAT-0001")]
    assert check_forbidden(expect, refused).passed  # a refused attempt is not a forbidden success


def test_patient_scope_ignores_refused_calls() -> None:
    other = [_step("get_referral_status", patient_id="PAT-0002")]
    assert not check_patient_scope(["PAT-0001"], other).passed
    refused = [_step("get_referral_status", ok=False, patient_id="PAT-0002")]
    assert check_patient_scope(["PAT-0001"], refused).passed
    assert check_patient_scope(["PAT-0001"], [_step("search_providers")]).passed


def test_budgets() -> None:
    errors = [_step("book_appointment", ok=False), _step("book_appointment", ok=False)]
    calls, errs = check_budget({"max_calls": 1, "max_errors": 1}, errors)
    assert not calls.passed and not errs.passed
    calls, errs = check_budget({}, [_step("health")])  # defaults: 10 calls, 0 errors
    assert calls.passed and errs.passed


# ---- state -----------------------------------------------------------------------------------

ROW = {"patient_id": "PAT-0008", "specialty": "primary_care", "date": "2026-11-02"}


def test_state_exact_match() -> None:
    expect = {"state": {"booked": [{"patient_id": "PAT-0008", "date": "2026-11-02"}]}}
    assert check_state(expect, {"booked": [ROW], "cancelled": []}).passed


def test_state_missing_unexpected_and_default_no_change() -> None:
    expect = {"state": {"booked": [{"patient_id": "PAT-0008"}]}}
    assert not check_state(expect, {"booked": [], "cancelled": []}).passed
    assert not check_state(expect, {"booked": [ROW, ROW], "cancelled": []}).passed
    assert not check_state({}, {"booked": [ROW], "cancelled": []}).passed
    assert check_state({}, {"booked": [], "cancelled": []}).passed


def test_state_rows_match_distinct_changes() -> None:
    expect = {"state": {"booked": [{"patient_id": "PAT-0008"}, {"patient_id": "PAT-0008"}]}}
    assert not check_state(expect, {"booked": [ROW], "cancelled": []}).passed


def test_grade_resolves_setup_refs() -> None:
    task = {
        "id": "t",
        "patients": ["PAT-0021"],
        "expect": {
            "state": {"cancelled": [{"appointment_id": {"$ref": "setup[1].appointment_id"}}]}
        },
    }
    change = {
        "booked": [],
        "cancelled": [{"appointment_id": "APT-00009", "patient_id": "PAT-0021"}],
    }
    ok = grade(task, [], change, {"setup": [None, {"appointment_id": "APT-00009"}]})
    assert ok.passed
    wrong = grade(task, [], change, {"setup": [None, {"appointment_id": "APT-00010"}]})
    assert wrong.failed() == ["final_state"]


# ---- diff ------------------------------------------------------------------------------------


def _row(i: int, status: str) -> dict:
    return {"id": i, "status": status, "patient_id": "P", "start": "2026-11-02T09:00:00"}


def test_diff_reports_net_change_only() -> None:
    before = {1: _row(1, "booked"), 2: _row(2, "booked")}
    after = {
        1: _row(1, "cancelled"),  # cancelled during the task
        2: _row(2, "booked"),  # untouched
        3: _row(3, "booked"),  # new booking
        4: _row(4, "cancelled"),  # booked and cancelled within the task: no net change
    }
    change = diff(before, after)
    assert [r["appointment_id"] for r in change["booked"]] == ["APT-00003"]
    assert [r["appointment_id"] for r in change["cancelled"]] == ["APT-00001"]
    assert change["booked"][0]["date"] == "2026-11-02"
