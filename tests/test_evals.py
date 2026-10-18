"""Runs the full eval suite (replay agent) against a real server, as CI does.

If a server change breaks a known-good agent flow, or the grader stops catching a known-bad
one, this fails. The golden set itself is also checked for consistency."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.golden.make_tasks import build
from evals.run import load, report, run_suite

CHECK_NAMES = {
    "required_steps",
    "forbidden_steps",
    "patient_scope",
    "max_calls",
    "max_errors",
    "final_state",
}


@pytest.fixture(scope="module")
def results() -> dict[str, Any]:
    return run_suite()


def test_every_golden_task_passes(results: dict[str, Any]) -> None:
    failing = {r["id"]: r["failed_checks"] for r in results["tasks"] if not r["passed"]}
    assert failing == {}


def test_every_negative_control_is_caught(results: dict[str, Any]) -> None:
    missed = {
        c["id"]: {"expected": c["expected_failed_checks"], "got": c["failed_checks"]}
        for c in results["negative_controls"]
        if not c["caught"]
    }
    assert missed == {}


def test_negative_controls_cover_every_check() -> None:
    covered = {name for n in load("negative.json") for name in n["expect_failed"]}
    assert covered == CHECK_NAMES


def test_golden_set_is_well_formed() -> None:
    tasks = load("tasks.json")
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)) >= 30
    for t in tasks:
        assert {"id", "category", "as", "patients", "prompt", "expect", "replay"} <= set(t), t["id"]
        assert t["as"] in {"staff", "reader"} or t["as"].startswith("portal:PAT-"), t["id"]
    by_id = {t["id"]: t for t in tasks}
    for n in load("negative.json"):
        assert n["task"] in by_id, n["id"]
        assert set(n["expect_failed"]) <= CHECK_NAMES, n["id"]


def test_evals_exercise_every_principal_and_tool(results: dict[str, Any]) -> None:
    principals = {r["as"] for r in results["tasks"]}
    assert {"staff", "reader"} <= principals and any(p.startswith("portal:") for p in principals)
    tools = {s["tool"] for r in results["tasks"] for s in r["steps"]}
    assert tools == {
        "health",
        "search_providers",
        "list_open_slots",
        "book_appointment",
        "cancel_appointment",
        "get_referral_status",
    }


def test_report_renders(results: dict[str, Any], tmp_path: Path) -> None:
    md = report(results)
    s = results["summary"]
    assert f"**{s['passed']}/{s['tasks']} golden tasks passed**" in md
    assert md.count("| `n0") == len(results["negative_controls"])
    (tmp_path / "r.json").write_text(json.dumps(results))  # results are JSON-serialisable


def test_committed_golden_files_match_the_generator() -> None:
    """tasks.json is generated; edit evals/golden/make_tasks.py, then regenerate."""
    tasks, negatives = build()
    assert load("tasks.json") == tasks, "run: python -m evals.golden.make_tasks"
    assert load("negative.json") == negatives, "run: python -m evals.golden.make_tasks"
