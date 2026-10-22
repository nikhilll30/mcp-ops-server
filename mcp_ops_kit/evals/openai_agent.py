"""Live LLM agent: an OpenAI model driving the server's own tools via the Responses API.

The model sees exactly the tools the server advertises (names, descriptions, input
schemas), so a vague tool description shows up as a lower score here. Every tool call goes
through the harness's ``call``, so the grader sees the real trajectory; the metrics this
agent returns (tokens, latency, final answer) are for the report only.

Responses, not Chat Completions: current reasoning models reject function tools combined
with reasoning on ``/v1/chat/completions`` (400, "Function tools with reasoning_effort are
not supported"), and turning reasoning off would understate them. Turns are chained with
``previous_response_id``, so the model's reasoning carries across tool calls.

Needs ``pip install -e ".[live]"`` and ``OPENAI_API_KEY``. The model ID is always passed in
(``--model``): nothing here assumes which models exist. The domain supplies the system
prompt, a template with ``{now}`` (the task's pinned time, or the domain's default).
"""

import json
import time
from datetime import datetime
from typing import Any


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Explicitly non-strict: strict schemas need every property required and no extra keys,
    # and the server's tools have optional arguments.
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
            "strict": False,
        }
        for t in tools
    ]


class OpenAIAgent:
    name = "openai"

    def __init__(
        self,
        model: str,
        system_prompt: str,
        default_now: datetime,
        client: Any = None,
        max_turns: int = 12,
        reasoning_effort: str | None = None,
    ) -> None:
        if not model:
            raise ValueError("model is required: pass the exact OpenAI model ID")
        if "{now}" not in system_prompt:
            raise ValueError("system_prompt must contain {now}: the agent has to know the date")
        self.model = model
        self.system_prompt = system_prompt
        self.default_now = default_now
        self.max_turns = max_turns
        self.reasoning_effort = reasoning_effort  # None: the model's default
        if client is None:
            from openai import AsyncOpenAI  # optional dependency, only for live runs

            client = AsyncOpenAI()  # reads OPENAI_API_KEY; the SDK retries 429/5xx
        self._client = client

    async def run(
        self,
        task: dict[str, Any],
        prompt: str,
        setup: list[Any],
        call: Any,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = task.get("now", self.default_now.isoformat(timespec="minutes"))
        metrics: dict[str, Any] = {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort or "default",
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_calls": 0,
            "llm_latency_ms": [],
            "final_answer": None,
            "stop": "max_turns",
            "undelivered": 0,  # tool results executed but never sent back to the model
        }
        try:
            await self._loop(
                self.system_prompt.format(now=now), prompt, to_openai_tools(tools), metrics, call
            )
        except Exception as exc:
            metrics["stop"] = "error"
            exc.agent_metrics = metrics  # type: ignore[attr-defined]  # tokens already spent
            raise
        return metrics

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def _loop(
        self,
        instructions: str,
        prompt: str,
        oa_tools: list[dict[str, Any]],
        metrics: dict[str, Any],
        call: Any,
    ) -> None:
        extra: dict[str, Any] = {}
        if self.reasoning_effort:
            extra["reasoning"] = {"effort": self.reasoning_effort}
        new_input: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        previous_id = None
        for _ in range(self.max_turns):
            start = time.perf_counter()
            response = await self._client.responses.create(
                model=self.model,
                instructions=instructions,  # not inherited via previous_response_id
                input=new_input,
                tools=oa_tools,
                previous_response_id=previous_id,
                **extra,
            )
            metrics["llm_latency_ms"].append(round((time.perf_counter() - start) * 1000, 1))
            metrics["llm_calls"] += 1
            if response.usage is not None:
                metrics["input_tokens"] += response.usage.input_tokens or 0
                metrics["output_tokens"] += response.usage.output_tokens or 0

            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                metrics["final_answer"] = response.output_text
                metrics["stop"] = "answered"
                return
            previous_id = response.id
            new_input = [
                {
                    "type": "function_call_output",
                    "call_id": c.call_id,
                    "output": await _run_tool(c, call),
                }
                for c in calls
            ]
        # The turn limit: the last turn's tool results were executed but never sent back,
        # so the model never saw them (and had no chance to resist what they held).
        metrics["undelivered"] = len(new_input) if previous_id is not None else 0


async def _run_tool(fc: Any, call: Any) -> str:
    """Execute one tool call through the harness and return the text the model will see.

    Malformed arguments still go to ``call``: the harness records them as a failed step
    (counted against the task's call and error budgets) without sending them to the server.
    """
    raw = fc.arguments or "{}"
    try:
        args = json.loads(raw)
    except ValueError:
        args = raw
    step = await call(fc.name, args)
    if step.ok:
        return json.dumps(step.result)
    return f"Error: {step.error}"
