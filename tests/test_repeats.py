"""Repeated runs (Part 12): every task run k times on fresh data, scored with pass^k.

A single live run cannot tell a reliable agent from a lucky one. These tests pin the
estimator, the per-task counts, which tasks are reported as flaky, that each run starts
from fresh data, and that the gate counts a task as passed only if every run passed."""

import json
import math
from pathlib import Path
from typing import Any

import pytest

from evals import run as run_mod
from evals.run import load, report, run_suite, summarize
from mcp_ops_kit.evals.agents import ReplayAgent
from mcp_ops_kit.evals.run import MAX_REPEATS, pass_hat_k


def _tasks(*ids: str) -> list[dict[str, Any]]:
    by_id = {t["id"]: t for t in load("tasks.json")}
    return [by_id[i] for i in ids]


@pytest.mark.parametrize(
    ("n", "c", "k", "expected"),
    [
        (5, 5, 5, 1.0),
        (5, 0, 1, 0.0),
        (4, 2, 1, 0.5),  # pass^1 is the pass rate
        (4, 2, 2, 1 / 6),  # C(2,2)/C(4,2): 1 of the 6 pairs of runs both passed
        (4, 2, 3, 0.0),  # fewer passes than k: no k runs can all pass
        (5, 4, 3, 0.4),  # C(4,3)/C(5,3) = 4/10
    ],
)
def test_pass_hat_k_is_the_combinatorial_estimator(n: int, c: int, k: int, expected: float) -> None:
    assert pass_hat_k(n, c, k) == pytest.approx(expected)


def _record(task_id: str, run: int, passed: bool) -> dict[str, Any]:
    return {"id": task_id, "run": run, "passed": passed, "category": "c", "steps": []}


def test_summary_counts_tasks_runs_and_flaky_tasks() -> None:
    golden = [
        _record(t, run, ok)
        for run in (1, 2, 3)
        for t, ok in (("steady", True), ("flaky", run == 2), ("broken", False))
    ]
    s = summarize(golden, [])
    assert (s["tasks"], s["passed"], s["runs"], s["runs_passed"]) == (3, 1, 9, 4)
    assert s["repeats"] == 3
    assert s["per_task"] == {
        "steady": {"runs": 3, "passed": 3},
        "flaky": {"runs": 3, "passed": 1},
        "broken": {"runs": 3, "passed": 0},
    }
    assert s["flaky"] == ["flaky"]  # a task that never passed is broken, not flaky
    assert s["run_scores"] == [1, 2, 1]
    # pass^k averages the per-task estimates: (1 + 1/3 + 0) / 3, then (1 + 0 + 0) / 3
    assert s["pass_hat_k"] == {"1": round(4 / 9, 4), "2": round(1 / 3, 4), "3": round(1 / 3, 4)}
    assert s["by_category"] == {"c": {"tasks": 3, "passed": 1}}


def test_a_single_run_summarises_as_before() -> None:
    s = summarize([_record("a", 1, True), _record("b", 1, False)], [])
    assert (s["tasks"], s["passed"], s["repeats"], s["pass_hat_k"]) == (2, 1, 1, {"1": 0.5})


class _FlakyAgent(ReplayAgent):
    """Replays correctly except on its second run of each task, where it books nothing."""

    name = "flaky"

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    async def run(self, task, prompt, setup, call, tools):  # noqa: ANN001, ANN201
        self.seen[task["id"]] = self.seen.get(task["id"], 0) + 1
        if self.seen[task["id"]] != 2:
            await super().run(task, prompt, setup, call, tools)


def test_each_run_starts_from_fresh_data_and_flaky_runs_fail_the_task() -> None:
    """b01 books the earliest primary-care slot. If run 2 reused run 1's data, that slot
    would be taken and run 3's booking would fail; it passes, so every run is fresh."""
    results = run_suite(_FlakyAgent(), _tasks("b01-earliest-primary-care"), [], repeats=3)
    runs = results["tasks"]
    assert [(r["run"], r["passed"]) for r in runs] == [(1, True), (2, False), (3, True)]
    assert runs[0]["change"] == runs[2]["change"]  # the same slot, booked from fresh data
    s = results["summary"]
    assert (s["passed"], s["runs_passed"], s["flaky"]) == (0, 2, ["b01-earliest-primary-care"])
    assert results["repeats"] == 3
    md = report(results)
    assert "**0/1 golden tasks passed in all 3 runs** · 2/3 runs passed" in md
    assert "## Repeated runs (3 per task)" in md
    assert "| `b01-earliest-primary-care` | 2/3 | 0.67 | 0.222 | yes |" in md
    assert "`b01-earliest-primary-care` (run 2)" in md  # its failure details name the run
    json.dumps(results)  # still JSON-serialisable


def test_runs_go_round_the_task_list_not_one_task_at_a_time() -> None:
    tasks = _tasks("r01-find-cardiologists", "r02-north-clinic")
    controls = [n for n in load("negative.json") if n["task"] in {t["id"] for t in tasks}]
    assert controls  # so the last line below checks something
    results = run_suite(tasks=tasks, negatives=controls, repeats=2)
    order = [(r["run"], r["id"]) for r in results["tasks"]]
    assert order == [
        (1, "r01-find-cardiologists"),
        (1, "r02-north-clinic"),
        (2, "r01-find-cardiologists"),
        (2, "r02-north-clinic"),
    ]
    # negative controls test the grader, so they run once however many repeats
    assert len(results["negative_controls"]) == len(controls)


@pytest.mark.parametrize("repeats", [0, MAX_REPEATS + 1])
def test_repeats_are_bounded(repeats: int) -> None:
    with pytest.raises(ValueError, match="repeats must be between"):
        run_suite(tasks=_tasks("r01-find-cardiologists"), negatives=[], repeats=repeats)
    with pytest.raises(SystemExit):
        run_mod.main(["--repeats", str(repeats)])


def test_the_cli_gate_fails_a_task_that_failed_any_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_mod, "_build_agent", lambda args: _FlakyAgent())
    argv = ["--tasks", "r01-find-cardiologists", "--repeats", "2", "--report", str(tmp_path / "r")]
    assert run_mod.main(argv) == 1
    assert run_mod.main([*argv[:2], "--repeats", "1", *argv[4:]]) == 0


def test_pass_hat_k_falls_with_k_for_an_inconsistent_agent() -> None:
    golden = [_record("t", run, run % 2 == 1) for run in range(1, 7)]  # 3 of 6 runs pass
    values = summarize(golden, [])["pass_hat_k"]
    assert [values[str(k)] for k in range(1, 7)] == [
        round(math.comb(3, k) / math.comb(6, k), 4) for k in range(1, 7)
    ]
    assert values["1"] == 0.5 and values["4"] == 0.0
