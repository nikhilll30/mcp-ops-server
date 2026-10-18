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
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.agents import Agent, ReplayAgent
from evals.grader import Check, Grade, Step, grade, render, resolve_refs
from evals.harness import Harness, TaskServer, diff
from mcp_ops_server import seed

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


async def _run_task(srv: TaskServer, task: dict[str, Any], agent: Agent) -> dict[str, Any]:
    setup: list[Any] = []
    if task.get("setup"):
        async with srv.session("staff") as staff:
            for s in task["setup"]:
                step = await srv.call(staff, s["tool"], resolve_refs(s["args"], {"setup": setup}))
                if not step.ok:  # a broken fixture fails this task, not the whole run
                    failed = Grade(task["id"], False, [Check("setup", False, step.error or "")])
                    return _record(task, task["prompt"], [], {}, failed)
                setup.append(step.result)
    prompt = render(task["prompt"], {"setup": setup})
    recorded: list[Step] = []  # the authoritative trajectory: what actually reached the server
    agent_error = None
    metrics: dict[str, Any] | None = None
    before = srv.snapshot()
    async with srv.session(task["as"]) as session:
        listed = await session.list_tools()
        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed.tools
        ]

        async def call(tool: str, args: Any) -> Step:
            if isinstance(args, dict):
                step = await srv.call(session, tool, args)
            else:  # e.g. a model's malformed JSON: never sent, but recorded and graded
                step = Step(
                    tool,
                    {"_invalid_arguments": args if isinstance(args, str) else repr(args)},
                    ok=False,
                    error="invalid arguments: expected a JSON object; not sent to the server",
                )
            recorded.append(step)
            return step

        start = time.perf_counter()
        try:
            metrics = await agent.run(task, prompt, setup, call, tools)
        except Exception as exc:  # noqa: BLE001 - an agent crash fails its task, not the run
            agent_error = f"{type(exc).__name__}: {exc}"
            metrics = getattr(exc, "agent_metrics", None)  # usage spent before the failure
        wall_ms = (time.perf_counter() - start) * 1000
    change = diff(before, srv.snapshot())
    context = {"setup": setup, "steps": [s.result for s in recorded]}
    result = grade(task, recorded, change, context, agent_error=agent_error)
    record = _record(task, prompt, recorded, change, result)
    record["wall_ms"] = round(wall_ms, 1)
    record["agent_metrics"] = metrics  # reported, never graded
    record["agent_error"] = agent_error
    return record


def _task_now(task: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(task["now"]) if "now" in task else seed.CLINIC_NOW


async def run_tasks(h: Harness, tasks: list[dict], agent: Agent) -> list[dict]:
    """Each task on its own freshly seeded server, so tasks cannot affect each other."""
    results = []
    for task in tasks:
        with h.task_server(_task_now(task)) as srv:
            results.append(await _run_task(srv, task, agent))
    return results


async def _run_all(
    h: Harness, tasks: list[dict], neg_tasks: list[dict], agent: Agent
) -> tuple[list[dict], list[dict]]:
    # One event loop for the whole suite: an agent's HTTP client pools connections bound to
    # the loop that opened them, so a loop per task breaks the second task.
    try:
        golden = await run_tasks(h, tasks, agent)
    finally:
        if hasattr(agent, "aclose"):
            await agent.aclose()
    # Controls always use the replay agent: they check the grader, not the agent.
    controls = await run_tasks(h, neg_tasks, ReplayAgent())
    return golden, controls


def run_suite(
    agent: Agent | None = None,
    tasks: list[dict] | None = None,
    negatives: list[dict] | None = None,
    prices: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Run golden tasks with ``agent`` and negative controls with the replay agent.

    ``prices`` is (USD per 1M input tokens, USD per 1M output tokens) for cost reporting.
    """
    agent = agent or ReplayAgent()
    tasks = load("tasks.json") if tasks is None else tasks
    negatives = load("negative.json") if negatives is None else negatives
    by_id = {t["id"]: t for t in tasks}
    neg_tasks = [
        {**by_id[n["task"]], "id": n["id"], "replay": n["replay"], "_neg": n} for n in negatives
    ]
    with Harness({t["as"] for t in tasks}) as h:
        golden, controls = asyncio.run(_run_all(h, tasks, neg_tasks, agent))
    for r, t in zip(controls, neg_tasks, strict=True):
        want = set(t["_neg"]["expect_failed"])
        r["description"] = t["_neg"]["description"]
        r["expected_failed_checks"] = sorted(want)
        # Caught = failed for the expected reasons. A setup failure is not "caught".
        r["caught"] = not r["passed"] and want <= set(r["failed_checks"])
    return {
        "agent": agent.name,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tasks": golden,
        "negative_controls": controls,
        "summary": summarize(golden, controls, prices),
    }


def _pct(values: list[float], q: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def _live_summary(golden: list[dict], prices: tuple[float, float] | None) -> dict[str, Any] | None:
    runs = [r["agent_metrics"] for r in golden if r.get("agent_metrics")]
    if not runs:
        return None
    tokens_in = sum(m["input_tokens"] for m in runs)
    tokens_out = sum(m["output_tokens"] for m in runs)
    llm = [ms for m in runs for ms in m["llm_latency_ms"]]
    wall = [r["wall_ms"] for r in golden]
    out: dict[str, Any] = {
        "model": runs[0]["model"],
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "llm_calls": sum(m["llm_calls"] for m in runs),
        "hit_max_turns": sum(m["stop"] == "max_turns" for m in runs),
        "llm_latency_ms": {"p50": round(_pct(llm, 50), 1), "p99": round(_pct(llm, 99), 1)}
        if llm
        else None,
        "task_latency_ms": {"p50": round(_pct(wall, 50), 1), "p99": round(_pct(wall, 99), 1)},
        "cost_usd": None,
    }
    if prices:
        cost = tokens_in / 1e6 * prices[0] + tokens_out / 1e6 * prices[1]
        out["cost_usd"] = round(cost, 4)
        out["cost_per_task_usd"] = round(cost / len(golden), 4)
        out["prices_per_million"] = {"input": prices[0], "output": prices[1]}
    return out


def summarize(
    golden: list[dict], controls: list[dict], prices: tuple[float, float] | None = None
) -> dict[str, Any]:
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
        "agent_errors": sum(r.get("agent_error") is not None for r in golden),
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
        "live": _live_summary(golden, prices),
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
        f"{s['tool_calls']} tool calls, {s['tool_errors']} tool errors, "
        f"{s['agent_errors']} agent errors (crashes or API failures).",
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
        f"## Tool latency (client-measured, local server, {results['agent']} agent)",
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
    if s.get("live"):
        lines += _live_section(results)
    return "\n".join(lines)


def _cell(text: str | None, limit: int = 120) -> str:
    text = " ".join((text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _live_section(results: dict[str, Any]) -> list[str]:
    live = results["summary"]["live"]
    cost = (
        f"${live['cost_usd']:.4f} total, ${live['cost_per_task_usd']:.4f} per task "
        f"(at ${live['prices_per_million']['input']}/${live['prices_per_million']['output']} "
        "per 1M input/output tokens)"
        if live["cost_usd"] is not None
        else "not computed (pass --price-in and --price-out)"
    )
    llm = live["llm_latency_ms"] or {"p50": "-", "p99": "-"}
    lines = [
        "## Live agent",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Model | `{live['model']}` |",
        f"| Tokens | {live['input_tokens']:,} in, {live['output_tokens']:,} out |",
        f"| Cost | {cost} |",
        f"| Model calls | {live['llm_calls']} ({live['hit_max_turns']} tasks hit the turn limit) |",
        f"| Model latency per call | p50 {llm['p50']} ms, p99 {llm['p99']} ms |",
        f"| Task latency | p50 {live['task_latency_ms']['p50']} ms, "
        f"p99 {live['task_latency_ms']['p99']} ms |",
        "",
        "Final answers are shown for reading, not graded: grading uses the recorded tool calls "
        "and the database change.",
        "",
        "| Task | Result | Final answer |",
        "|---|---|---|",
    ]
    for r in results["tasks"]:
        m = r.get("agent_metrics") or {}
        answer = {
            "answered": m.get("final_answer"),
            "max_turns": "(hit turn limit)",
        }.get(m.get("stop"), f"(agent error: {r.get('agent_error')})")
        lines.append(f"| `{r['id']}` | {'pass' if r['passed'] else '**FAIL**'} | {_cell(answer)} |")
    return [*lines, ""]


def _build_agent(args: argparse.Namespace) -> Agent:
    if args.agent == "replay":
        return ReplayAgent()
    from evals.agents_openai import OpenAIAgent

    if not args.model:
        raise SystemExit("--model is required with --agent openai (the exact OpenAI model ID)")
    return OpenAIAgent(model=args.model, max_turns=args.max_turns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--agent", choices=["replay", "openai"], default="replay")
    parser.add_argument("--model", help="model ID for --agent openai")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--tasks", default="", help="comma-separated task IDs (default: all)")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens, for cost")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens, for cost")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        help="fail below this golden pass rate (default 1.0 for replay, 0 for live runs)",
    )
    parser.add_argument("--out", help="write raw results JSON here")
    parser.add_argument("--report", help="Markdown report path")
    args = parser.parse_args(argv)

    agent = _build_agent(args)
    tasks = load("tasks.json")
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        unknown = wanted - {t["id"] for t in tasks}
        if unknown:
            raise SystemExit(f"unknown task IDs: {sorted(unknown)}")
        tasks = [t for t in tasks if t["id"] in wanted]
    negatives = [n for n in load("negative.json") if n["task"] in {t["id"] for t in tasks}]
    prices = (args.price_in, args.price_out) if args.price_in is not None else None
    if (args.price_in is None) != (args.price_out is None):
        raise SystemExit("pass both --price-in and --price-out, or neither")

    results = run_suite(agent, tasks, negatives, prices)
    default_report = "report.md" if args.agent == "replay" else "results/live-report.md"
    report_path = Path(args.report or Path(__file__).parent / default_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report(results))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2) + "\n")

    s = results["summary"]
    print(  # noqa: T201 - CLI summary
        f"{s['passed']}/{s['tasks']} tasks passed; "
        f"{s['negative_caught']}/{s['negative_controls']} negative controls caught; "
        f"report: {report_path}"
    )
    # Negative controls test the grader, so they must always be caught. The golden pass rate
    # gates replay runs (deterministic); live runs measure a model and do not fail by default.
    # An agent error (crash, bad key, unknown model, API outage after the SDK's retries) is
    # not a measurement, so it always fails the run.
    min_rate = (
        args.min_pass_rate
        if args.min_pass_rate is not None
        else (1.0 if args.agent == "replay" else 0.0)
    )
    rate = s["passed"] / s["tasks"] if s["tasks"] else 1.0
    ok = (
        rate >= min_rate
        and s["negative_caught"] == s["negative_controls"]
        and s["agent_errors"] == 0
    )
    if s["agent_errors"]:
        print(f"{s['agent_errors']} task(s) ended in an agent error; see the report")  # noqa: T201
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
