"""The salon's eval gate: every golden task passes and every negative control is caught."""

import json
from typing import Any

import pytest

from salon_ops.evals.golden.make_tasks import build
from salon_ops.evals.run import load, run_suite

CHECK_NAMES = {
    "agent_error",
    "required_steps",
    "forbidden_steps",
    "preconditions",
    "customer_scope",
    "max_calls",
    "max_errors",
    "final_state",
}


@pytest.fixture(scope="module")
def results() -> dict[str, Any]:
    return run_suite()


def test_every_salon_task_passes(results: dict[str, Any]) -> None:
    assert {r["id"]: r["failed_checks"] for r in results["tasks"] if not r["passed"]} == {}


def test_every_salon_negative_control_is_caught(results: dict[str, Any]) -> None:
    missed = [c["id"] for c in results["negative_controls"] if not c["caught"]]
    assert missed == []


def test_salon_controls_cover_every_check() -> None:
    assert {n for c in load("negative.json") for n in c["expect_failed"]} == CHECK_NAMES


def test_salon_evals_exercise_every_principal_and_tool(results: dict[str, Any]) -> None:
    principals = {r["as"] for r in results["tasks"]}
    assert {"staff", "reader"} <= principals
    assert any(p.startswith("customer:") for p in principals)
    tools = {s["tool"] for r in results["tasks"] for s in r["steps"]}
    assert tools == {
        "search_stylists",
        "list_openings",
        "get_customer_visits",
        "book_visit",
        "cancel_visit",
    }


def test_committed_salon_json_matches_the_generator() -> None:
    tasks, negatives = build()
    assert json.loads(json.dumps(tasks)) == load("tasks.json")
    assert json.loads(json.dumps(negatives)) == load("negative.json")
