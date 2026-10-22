"""Run the clinic's eval suite against a live server and write results JSON + a report.

    python -m evals.run                     # replay agent, writes evals/report.md
    python -m evals.run --out results.json  # also keep the raw results
    python -m evals.run --backend fhir --backend-url http://127.0.0.1:8080/fhir

The harness, grader, agents and report are ``mcp_ops_kit.evals``; this module is the
clinic's ``EvalDomain`` (its principals, patient scope, clock and data per backend) and its
CLI. Exit code 1 if any golden task fails or any negative control is not caught.
"""

import argparse
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evals.agents_openai import SYSTEM_PROMPT  # imports no openai package
from mcp_ops_kit.crypto import DataKey
from mcp_ops_kit.evals import run as kit
from mcp_ops_kit.evals.agents import Agent, ReplayAgent
from mcp_ops_kit.evals.grader import SubjectRule
from mcp_ops_kit.evals.harness import EvalDomain, TaskData, TaskSpec
from mcp_ops_kit.evals.run import report, summarize
from mcp_ops_server import seed
from mcp_ops_server.auth import ALL_SCOPES, AUTH
from mcp_ops_server.backends import fhir_seed, make_backend
from mcp_ops_server.backends.base import appointment_id
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.config import Settings
from mcp_ops_server.server import build_http_app

__all__ = ["domain", "load", "main", "report", "run_suite", "summarize"]

HERE = Path(__file__).parent
BACKENDS = ("sqlite", "fhir")
PATIENT_SCOPE = SubjectRule(arg="patient_id", task_key="patients", check="patient_scope")
PROVIDER_ID = re.compile(r"PRV-[0-9]{3}")


def plant_sqlite(store: SqliteBackend, names: Mapping[str, str]) -> None:
    """Rename providers (provider ID -> name) in a task's database: the prompt-injection
    suite's planted text. Each ID must exist. Eval-only, so it uses the store's private
    write transaction rather than adding a rename to the server's backend protocol."""
    with store._write() as c:
        for provider_id, name in names.items():
            changed = c.execute(
                "UPDATE providers SET name = ? WHERE id = ?", (name, provider_id)
            ).rowcount
            if changed != 1:
                raise ValueError(f"provider {provider_id} is not in the database")


def domain(backend: str = "sqlite", fhir_url: str | None = None) -> EvalDomain:
    """The clinic for evals, on SQLite (a copy of one seeded file per task) or on a FHIR R4
    server at ``fhir_url`` (loaded once, then reset to the seed before every task; its data
    is overwritten). A task's ``plant`` (provider ID -> name) renames those providers in its
    fresh data, on either backend."""
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    if backend == "fhir" and not fhir_url:
        raise ValueError("the fhir backend needs fhir_url")

    def prepare(root: Path, data_key: DataKey) -> None:
        if backend == "fhir":
            fhir_seed.load(fhir_url)  # type: ignore[arg-type] - checked above
        else:
            # seed once, encrypted; tasks copy the file
            SqliteBackend(str(root / "template.db"), data_key=data_key).close()

    def start_task(spec: TaskSpec) -> TaskData:
        db_path = str(spec.workdir / f"task-{spec.number}.db")
        bad = [k for k in spec.plant if not PROVIDER_ID.fullmatch(k)]
        if bad:
            raise ValueError(f"the clinic plants text in provider names only, not {bad}")
        if backend == "fhir":
            fhir_seed.reset(fhir_url)  # type: ignore[arg-type] - checked above
            if spec.plant:
                fhir_seed.set_provider_names(fhir_url, dict(spec.plant))  # type: ignore[arg-type]
        else:
            shutil.copyfile(spec.workdir / "template.db", db_path)
        settings = Settings(
            host="127.0.0.1",
            port=spec.port,
            backend=backend,
            db_path=db_path,
            fhir_url=fhir_url,
            tokens_file=spec.tokens_file,
            rate_limit_per_minute=100_000,  # evals measure agent behaviour, not the limiter
            now=spec.now,
            audit_log=spec.audit_log,
            data_key=spec.data_key,
        )
        store = make_backend(settings)
        if spec.plant and isinstance(store, SqliteBackend):
            plant_sqlite(store, spec.plant)
        app = build_http_app(settings, store)

        def close() -> None:
            store.close()
            app.state.audit.close()

        return TaskData(app, store.snapshot_appointments, close)

    return EvalDomain(
        name="clinic",
        serve_command="python -m mcp_ops_server",
        golden_dir=HERE / "golden",
        auth=AUTH,
        principals={"staff": ALL_SCOPES, "reader": ("read:schedule",)},
        bound_prefix="portal:",
        subject=PATIENT_SCOPE,
        default_now=seed.CLINIC_NOW,
        record_key="appointment_id",
        record_id=appointment_id,
        prepare=prepare,
        start_task=start_task,
        backend=backend,
        system_prompt=SYSTEM_PROMPT,
        supports_plant=True,
        # Both backends' wording for a write sent but not confirmed (fhir.py's
        # OutcomeUnknownError); SQLite never raises it, so it never matches there.
        uncertain_errors=("may or may not",),
    )


def load(name: str) -> list[dict[str, Any]]:
    return kit.load(domain(), name)


def run_suite(
    agent: Agent | None = None,
    tasks: list[dict] | None = None,
    negatives: list[dict] | None = None,
    prices: tuple[float, float] | None = None,
    backend: str = "sqlite",
    fhir_url: str | None = None,
    *,
    suite: str = "golden",
    repeats: int = 1,
) -> dict[str, Any]:
    """A suite's tasks with ``agent`` (``repeats`` times each) and its negative controls with
    the replay agent, on the chosen backend. ``prices`` is (USD per 1M input, per 1M output
    tokens) for cost."""
    return kit.run_suite(
        domain(backend, fhir_url), agent, tasks, negatives, prices, suite=suite, repeats=repeats
    )


def _build_agent(args: argparse.Namespace) -> Agent:
    if args.agent == "replay":
        return ReplayAgent()
    from evals.agents_openai import OpenAIAgent

    if not args.model:
        raise SystemExit("--model is required with --agent openai (the exact OpenAI model ID)")
    return OpenAIAgent(
        model=args.model, max_turns=args.max_turns, reasoning_effort=args.reasoning_effort
    )


def _make_domain(backend: str, backend_url: str | None) -> EvalDomain:
    if backend == "fhir" and not backend_url:
        raise SystemExit("--backend fhir needs --backend-url")
    return domain(backend, backend_url)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # --fhir-url was this CLI's flag before the runner moved to the kit (Parts 7 docs use it).
    # Both argparse forms, "--fhir-url URL" and "--fhir-url=URL" (Codex review, PR #18).
    argv = [
        "--backend-url" + a.removeprefix("--fhir-url")
        if a == "--fhir-url" or a.startswith("--fhir-url=")
        else a
        for a in argv
    ]
    return kit.main(
        _make_domain,
        lambda args: _build_agent(args),  # late-bound, so tests can stub it
        argv,
        prog="python -m evals.run",
        backends=BACKENDS,
        report_dir=HERE,
    )


if __name__ == "__main__":
    sys.exit(main())
