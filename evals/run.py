"""Run the eval suite against a live server and write results JSON + a Markdown report.

    python -m evals.run                     # replay agent, writes evals/report.md
    python -m evals.run --out results.json  # also keep the raw results

Exit code 1 if any golden task fails or any negative control is not caught.
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.agents import Agent, ReplayAgent
from evals.grader import Check, Grade, grade, render, resolve_refs
from evals.harness import Harness, Sessions, diff

GOLDEN = Path(__file__).parent / "golden"


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((GOLDEN / name).read_text())


def _record(task: dict[str, Any], prompt: str, steps: list, change: dict, result: Grade) -> dict:
    return {
        "id": task["id"],
        "category": task["category"],
        "as": task["as"],
        "prompt": prompt,
        "steps": [asdict(s) for s in steps],
        "change": change,
        "grade": result.to_dict(),
        "passed": result.passed,
        "failed_checks": result.failed(),
    }


async def _run_task(
    h: Harness, sessions: Sessions, task: dict[str, Any], agent: Agent
) -> dict[str, Any]:
    setup: list[Any] = []
    if task.get("setup"):
        staff = await sessions.get("staff")
        for s in task["setup"]:
            step = await h.call(staff, s["tool"], resolve_refs(s["args"], {"setup": setup}))
            if not step.ok:  # a broken fixture fails this task, not the whole run
                failed = Grade(task["id"], False, [Check("setup", False, step.error or "")])
                return _record(task, task["prompt"], [], {}, failed)
            setup.append(step.result)
    prompt = render(task["prompt"], {"setup": setup})
    session = await sessions.get(task["as"])
    before = h.snapshot()

    async def call(tool: str, args: dict[str, Any]):  # noqa: ANN202
        return await h.call(session, tool, args)

    steps = await agent.run(task, prompt, setup, call)
    change = diff(before, h.snapshot())
    return _record(task, prompt, steps, change, grade(task, steps, change, {"setup": setup}))


def run_suite(
    agent: Agent | None = None, tasks: list[dict] | None = None, negatives: list[dict] | None = None
) -> dict[str, Any]:
    agent = agent or ReplayAgent()
    tasks = load("tasks.json") if tasks is None else tasks
    negatives = load("negative.json") if negatives is None else negatives
    by_id = {t["id"]: t for t in tasks}
    neg_tasks = [
        {**by_id[n["task"]], "id": n["id"], "replay": n["replay"], "_neg": n} for n in negatives
    ]
    principals = {t["as"] for t in tasks}

    async def run_golden(h: Harness) -> list[dict]:
        async with h.sessions() as sessions:
            return [await _run_task(h, sessions, t, agent) for t in tasks]

    async def run_controls(h: Harness) -> list[dict]:
        controls = []
        async with h.sessions() as sessions:
            for t in neg_tasks:  # always replayed: they check the grader, not the agent
                controls.append(await _run_control(h, sessions, t))
        return controls

    async def _run_control(h: Harness, sessions: Sessions, t: dict) -> dict:
        r = await _run_task(h, sessions, t, ReplayAgent())
        want = set(t["_neg"]["expect_failed"])
        r["description"] = t["_neg"]["description"]
        r["expected_failed_checks"] = sorted(want)
        # Caught = failed for the expected reasons. A setup failure is not "caught".
        r["caught"] = not r["passed"] and want <= set(r["failed_checks"])
        return r

    # Each suite gets a freshly seeded server, so controls never see golden-run bookings.
    with Harness(principals) as h:
        golden = asyncio.run(run_golden(h))
    with Harness({t["as"] for t in neg_tasks}) as h:
        controls = asyncio.run(run_controls(h))
    return {
        "agent": agent.name,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tasks": golden,
        "negative_controls": controls,
        "summary": summarize(golden, controls),
    }


def _pct(values: list[float], q: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def summarize(golden: list[dict], controls: list[dict]) -> dict[str, Any]:
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in golden:
        by_cat[r["category"]].append(r["passed"])
    lat: dict[str, list[float]] = defaultdict(list)
    for r in golden:
        for s in r["steps"]:
            lat[s["tool"]].append(s["latency_ms"])
    calls = [len(r["steps"]) for r in golden]
    return {
        "tasks": len(golden),
        "passed": sum(r["passed"] for r in golden),
        "by_category": {c: {"tasks": len(v), "passed": sum(v)} for c, v in sorted(by_cat.items())},
        "tool_calls": sum(calls),
        "tool_errors": sum(not s["ok"] for r in golden for s in r["steps"]),
        "negative_controls": len(controls),
        "negative_caught": sum(c["caught"] for c in controls),
        "latency_ms": {
            tool: {
                "calls": len(v),
                "p50": round(_pct(v, 50), 1),
                "p95": round(_pct(v, 95), 1),
                "max": round(max(v), 1),
            }
            for tool, v in sorted(lat.items())
        },
    }


def report(results: dict[str, Any]) -> str:
    s = results["summary"]
    lines = [
        "# Eval report",
        "",
        f"Agent: **{results['agent']}** · finished {results['finished_at']} · "
        f"server: `python -m mcp_ops_server` over Streamable HTTP with issued tokens.",
        "",
        f"**{s['passed']}/{s['tasks']} golden tasks passed** · "
        f"**{s['negative_caught']}/{s['negative_controls']} negative controls caught** · "
        f"{s['tool_calls']} tool calls, {s['tool_errors']} tool errors.",
        "",
        "## By category",
        "",
        "| Category | Passed |",
        "|---|---|",
        *[f"| {c} | {v['passed']}/{v['tasks']} |" for c, v in s["by_category"].items()],
        "",
        "## Tasks",
        "",
        "| Task | As | Calls | Errors | Result | Failed checks |",
        "|---|---|---|---|---|---|",
    ]
    for r in results["tasks"]:
        errors = sum(not st["ok"] for st in r["steps"])
        lines.append(
            f"| `{r['id']}` | {r['as']} | {len(r['steps'])} | {errors} | "
            f"{'pass' if r['passed'] else '**FAIL**'} | {', '.join(r['failed_checks']) or '-'} |"
        )
    lines += [
        "",
        "## Negative controls (deliberately bad trajectories the grader must fail)",
        "",
        "| Control | What it does wrong | Expected to fail | Caught |",
        "|---|---|---|---|",
    ]
    for c in results["negative_controls"]:
        lines.append(
            f"| `{c['id']}` | {c['description']} | {', '.join(c['expected_failed_checks'])} | "
            f"{'yes' if c['caught'] else '**NO**'} |"
        )
    lines += [
        "",
        "## Tool latency (client-measured, local server, replay agent)",
        "",
        "| Tool | Calls | p50 ms | p95 ms | max ms |",
        "|---|---|---|---|---|",
        *[
            f"| `{t}` | {v['calls']} | {v['p50']} | {v['p95']} | {v['max']} |"
            for t, v in s["latency_ms"].items()
        ],
        "",
        "Latency is from one local run and varies by machine; it is a baseline, not a benchmark.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--out", help="write raw results JSON here")
    parser.add_argument("--report", default=str(Path(__file__).parent / "report.md"))
    args = parser.parse_args(argv)
    results = run_suite()
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    Path(args.report).write_text(report(results))
    s = results["summary"]
    print(  # noqa: T201 - CLI summary
        f"{s['passed']}/{s['tasks']} tasks passed; "
        f"{s['negative_caught']}/{s['negative_controls']} negative controls caught"
    )
    ok = s["passed"] == s["tasks"] and s["negative_caught"] == s["negative_controls"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
