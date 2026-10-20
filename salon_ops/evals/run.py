"""Run the salon's eval suite against its live server and write results JSON + a report.

    python -m salon_ops.evals.run                     # replay agent, writes report.md here
    python -m salon_ops.evals.run --out results.json  # also keep the raw results

The harness, grader, agents and report are ``mcp_ops_kit.evals``; this module is the salon's
``EvalDomain`` and CLI. Exit code 1 if any golden task fails or any control is not caught.
"""

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from mcp_ops_kit.crypto import DataKey
from mcp_ops_kit.evals import run as kit
from mcp_ops_kit.evals.agents import Agent, ReplayAgent
from mcp_ops_kit.evals.grader import SubjectRule
from mcp_ops_kit.evals.harness import EvalDomain, TaskData, TaskSpec
from salon_ops import seed
from salon_ops.auth import ALL_SCOPES, AUTH
from salon_ops.config import Settings
from salon_ops.server import build_http_app, make_store
from salon_ops.store import SalonStore, visit_id

HERE = Path(__file__).parent

SYSTEM_PROMPT = """\
You are the booking assistant for a hair salon. All data is synthetic.
Act only through the tools. The current salon date and time is {now}.

- Look IDs up with the tools; never invent stylist, opening, visit or customer IDs.
- A visit can only be booked for a service the stylist offers.
- If a request cannot or must not be done as asked (service not offered, opening taken,
  too late to cancel, not permitted, in the past), do not work around it on your own: make
  no change, and say why.
- There is no one to answer follow-up questions. Do the request as asked, then stop and
  reply with a short summary of what you did.
"""


def _prepare(root: Path, data_key: DataKey) -> None:
    # seed once, encrypted; each task copies this file
    SalonStore(str(root / "template.db"), data_key=data_key).close()


def _start_task(spec: TaskSpec) -> TaskData:
    db_path = str(spec.workdir / f"task-{spec.number}.db")
    shutil.copyfile(spec.workdir / "template.db", db_path)
    settings = Settings(
        port=spec.port,
        db_path=db_path,
        tokens_file=spec.tokens_file,
        rate_limit_per_minute=100_000,  # evals measure agent behaviour, not the limiter
        now=spec.now,
        audit_log=spec.audit_log,
        data_key=spec.data_key,
    )
    store = make_store(settings)
    app = build_http_app(settings, store)

    def close() -> None:
        store.close()
        app.state.audit.close()

    return TaskData(app, store.snapshot_visits, close)


DOMAIN = EvalDomain(
    name="salon",
    serve_command="python -m salon_ops",
    golden_dir=HERE / "golden",
    auth=AUTH,
    principals={"staff": ALL_SCOPES, "reader": ("read:catalog",)},
    bound_prefix="customer:",
    subject=SubjectRule(arg="customer_id", task_key="customers", check="customer_scope"),
    default_now=seed.SALON_NOW,
    record_key="visit_id",
    record_id=visit_id,
    prepare=_prepare,
    start_task=_start_task,
    system_prompt=SYSTEM_PROMPT,
)


def load(name: str) -> list[dict[str, Any]]:
    return kit.load(DOMAIN, name)


def run_suite(
    agent: Agent | None = None,
    tasks: list[dict] | None = None,
    negatives: list[dict] | None = None,
) -> dict[str, Any]:
    return kit.run_suite(DOMAIN, agent, tasks, negatives)


def _build_agent(args: argparse.Namespace) -> Agent:
    if args.agent == "replay":
        return ReplayAgent()
    from mcp_ops_kit.evals.openai_agent import OpenAIAgent

    if not args.model:
        raise SystemExit("--model is required with --agent openai (the exact OpenAI model ID)")
    return OpenAIAgent(
        args.model,
        SYSTEM_PROMPT,
        seed.SALON_NOW,
        max_turns=args.max_turns,
        reasoning_effort=args.reasoning_effort,
    )


def main(argv: list[str] | None = None) -> int:
    return kit.main(
        lambda backend, url: DOMAIN,
        _build_agent,
        argv,
        prog="python -m salon_ops.evals.run",
        report_dir=HERE,
    )


if __name__ == "__main__":
    sys.exit(main())
