"""Agents under evaluation. An agent gets a task and a ``call`` function, and returns steps.

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
        self, task: dict[str, Any], prompt: str, setup: list[Any], call: CallTool
    ) -> list[Step]: ...


class ReplayAgent:
    name = "replay"

    def __init__(self, key: str = "replay") -> None:
        self._key = key  # which script to replay; negative controls use their own

    async def run(
        self, task: dict[str, Any], prompt: str, setup: list[Any], call: CallTool
    ) -> list[Step]:
        steps: list[Step] = []
        for scripted in task[self._key]:
            context = {"setup": setup, "steps": [s.result for s in steps]}
            steps.append(await call(scripted["tool"], resolve_refs(scripted["args"], context)))
        return steps
