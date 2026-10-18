"""Agents under evaluation. An agent gets a task, its prompt, setup results, the tools the
server advertises (name, description, input schema) and a ``call`` function, and acts only
through ``call``.

The harness records every call made through ``call``; that record, not anything the agent
returns or claims, is what gets graded. An agent may return a metrics dict (token usage,
its final answer) for the report; it is never used for grading.

``ReplayAgent`` needs no LLM: it replays a scripted trajectory, resolving ``$ref`` values
against earlier results (``steps[1].slots[0].slot_id``) and setup results. It runs free and
deterministically in CI, so a server change that breaks a known-good agent flow fails the
build. Part 5 adds an LLM agent behind the same interface.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from evals.grader import Step, resolve_refs

CallTool = Callable[[str, dict[str, Any]], Awaitable[Step]]


class Agent(Protocol):
    name: str

    async def run(
        self,
        task: dict[str, Any],
        prompt: str,
        setup: list[Any],
        call: CallTool,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any] | None: ...


class ReplayAgent:
    name = "replay"

    async def run(
        self,
        task: dict[str, Any],
        prompt: str,
        setup: list[Any],
        call: CallTool,
        tools: list[dict[str, Any]],
    ) -> None:
        results: list[Any] = []
        for scripted in task["replay"]:
            context = {"setup": setup, "steps": results}
            step = await call(scripted["tool"], resolve_refs(scripted["args"], context))
            results.append(step.result)
