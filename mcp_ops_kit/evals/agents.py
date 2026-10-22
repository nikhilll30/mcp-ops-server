"""Agents under evaluation. An agent gets a task, its prompt, setup results, the tools the
server advertises (name, description, input schema) and a ``call`` function, and acts only
through ``call``.

The harness records every call made through ``call``; that record, not anything the agent
returns or claims, is what gets graded. An agent may return a metrics dict (token usage,
its final answer) for the report; it is never used for grading, with one exception that can
only hurt the agent: ``undelivered``, the number of its trailing calls whose results never
reached its model (cut off by a turn limit). Those results do not count as the agent having
seen planted text, so reporting more can fail an injection task, never pass one. An agent
with an async ``aclose()`` has it called once after its last task.

``ReplayAgent`` needs no LLM: it replays a scripted trajectory, resolving ``$ref`` values
against earlier results (``steps[1].slots[0].slot_id``) and setup results. It runs free and
deterministically in CI, so a server change that breaks a known-good agent flow fails the
build. ``openai_agent.OpenAIAgent`` is an LLM agent behind the same interface.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from mcp_ops_kit.evals.grader import Step, resolve_refs

# ``call(tool, args)``. Arguments that are not a dict (a model's malformed JSON) are recorded
# as a failed step and never sent to the server.
CallTool = Callable[[str, Any], Awaitable[Step]]


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
