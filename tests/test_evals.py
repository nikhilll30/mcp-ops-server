"""Runs the full eval suite (replay agent) against a real server, as CI does.

If a server change breaks a known-good agent flow, or the grader stops catching a known-bad
one, this fails. The golden set itself is also checked for consistency."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.golden.make_tasks import build
from evals.run import load, report, run_suite
from mcp_ops_kit.evals.agents import ReplayAgent

CHECK_NAMES = {
    "agent_error",
    "required_steps",
    "forbidden_steps",
    "preconditions",
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
    assert sum(f"| `{c['id']}` |" in md for c in results["negative_controls"]) == len(
        results["negative_controls"]
    )
    (tmp_path / "r.json").write_text(json.dumps(results))  # results are JSON-serialisable


def test_committed_golden_files_match_the_generator() -> None:
    """tasks.json is generated; edit evals/golden/make_tasks.py, then regenerate."""
    tasks, negatives = build()
    assert load("tasks.json") == tasks, "run: python -m evals.golden.make_tasks"
    assert load("negative.json") == negatives, "run: python -m evals.golden.make_tasks"


# ---- Codex review, Part 4: structural guarantees ------------------------------------------


def _tasks(*ids: str) -> list[dict[str, Any]]:
    by_id = {t["id"]: t for t in load("tasks.json")}
    return [by_id[i] for i in ids]


class _HidingAgent(ReplayAgent):
    """Does a correct run plus an out-of-scope lookup, and claims it only did the replay."""

    name = "hiding"

    async def run(self, task, prompt, setup, call, tools):  # noqa: ANN001, ANN201
        await call("get_referral_status", {"patient_id": "PAT-0002"})
        await super().run(task, prompt, setup, call, tools)
        return []  # whatever an agent returns or reports is ignored


def test_grading_uses_the_recorded_trajectory_not_the_agents_report() -> None:
    (result,) = run_suite(_HidingAgent(), _tasks("r06-no-referrals"), [])["tasks"]
    assert result["steps"][0]["args"] == {"patient_id": "PAT-0002"}
    assert "patient_scope" in result["failed_checks"]


class _SabotageAgent(ReplayAgent):
    """In b01, books the slot k02's setup needs; behaves normally otherwise."""

    name = "sabotage"

    async def run(self, task, prompt, setup, call, tools):  # noqa: ANN001, ANN201
        if task["id"] == "b01-earliest-primary-care":
            k02 = next(t for t in load("tasks.json") if t["id"].startswith("k02"))
            slot = k02["setup"][0]["args"]["slot_id"]
            await call("book_appointment", {"patient_id": "PAT-0008", "slot_id": slot})
            return
        await super().run(task, prompt, setup, call, tools)


def test_a_failing_task_cannot_poison_the_next_one() -> None:
    tasks = _tasks("b01-earliest-primary-care", "k02-double-booked-patient")
    b01, k02 = run_suite(_SabotageAgent(), tasks, [])["tasks"]
    assert not b01["passed"]
    assert k02["passed"], k02["failed_checks"]


def test_results_do_not_depend_on_the_callers_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_OPS_NOW", "2026-11-30T08:00:00")  # every slot would be past
    results = run_suite(
        tasks=_tasks("b01-earliest-primary-care", "k03-date-in-the-past"), negatives=[]
    )
    assert [r["passed"] for r in results["tasks"]] == [True, True]


# ---- Part 7: backend parity --------------------------------------------------------------


def _trajectory(r: dict[str, Any]) -> tuple:
    steps = [(s["tool"], s["args"], s["ok"], s["error"], s["result"]) for s in r["steps"]]
    return steps, r["change"], r["grade"]


@pytest.mark.fhir
def test_fhir_backend_gives_the_same_eval_run_as_sqlite(
    results: dict[str, Any], fhir_url: str
) -> None:
    """Not just the same pass count: every tool result, error text, state change and check,
    for every golden task and negative control, is identical on FHIR and SQLite."""
    fhir = run_suite(backend="fhir", fhir_url=fhir_url)
    assert fhir["backend"] == "fhir" and results["backend"] == "sqlite"
    runs = zip(
        results["tasks"] + results["negative_controls"],
        fhir["tasks"] + fhir["negative_controls"],
        strict=True,
    )
    differ = [a["id"] for a, b in runs if _trajectory(a) != _trajectory(b)]
    assert differ == []
