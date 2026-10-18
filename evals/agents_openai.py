"""Live LLM agent: an OpenAI model driving the server's own tools via Chat Completions.

The model sees exactly the tools the server advertises (names, descriptions, input
schemas), so a vague tool description shows up as a lower score here. Every tool call goes
through the harness's ``call``, so the grader sees the real trajectory; the metrics this
agent returns (tokens, latency, final answer) are for the report only.

Needs ``pip install -e ".[live]"`` and ``OPENAI_API_KEY``. The model ID is always passed in
(``--model``): nothing here assumes which models exist.
"""

import json
import time
from typing import Any

from mcp_ops_server import seed

SYSTEM_PROMPT = """\
You are the scheduling assistant for a clinic. All data is synthetic.
Act only through the tools. The current clinic date and time is {now}.

- Look IDs up with the tools; never invent provider, slot, referral or appointment IDs.
- Specialties that need a referral can only be booked with an approved referral for that
  patient and that specialty. Check the referral before booking.
- If a request cannot or must not be done as asked (no approved referral, slot taken, not
  permitted, in the past), do not work around it on your own: make no booking, and say why.
  A fallback the user asked for (for example "otherwise the next open slot") is part of the
  request, so do that.
- There is no one to answer follow-up questions. Do the request as asked, then stop and
  reply with a short summary of what you did.
"""


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


class OpenAIAgent:
    name = "openai"

    def __init__(self, model: str, client: Any = None, max_turns: int = 12) -> None:
        if not model:
            raise ValueError("model is required: pass the exact OpenAI model ID")
        self.model = model
        self.max_turns = max_turns
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
        now = task.get("now", seed.CLINIC_NOW.isoformat(timespec="minutes"))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(now=now)},
            {"role": "user", "content": prompt},
        ]
        metrics: dict[str, Any] = {
            "model": self.model,
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_calls": 0,
            "llm_latency_ms": [],
            "final_answer": None,
            "stop": "max_turns",
        }
        try:
            await self._loop(messages, to_openai_tools(tools), metrics, call)
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
        messages: list[dict[str, Any]],
        oa_tools: list[dict[str, Any]],
        metrics: dict[str, Any],
        call: Any,
    ) -> None:
        for _ in range(self.max_turns):
            start = time.perf_counter()
            response = await self._client.chat.completions.create(
                model=self.model, messages=messages, tools=oa_tools
            )
            metrics["llm_latency_ms"].append(round((time.perf_counter() - start) * 1000, 1))
            metrics["llm_calls"] += 1
            if response.usage is not None:
                metrics["input_tokens"] += response.usage.prompt_tokens or 0
                metrics["output_tokens"] += response.usage.completion_tokens or 0

            message = response.choices[0].message
            tool_calls = message.tool_calls or []
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    **(
                        {
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in tool_calls
                            ]
                        }
                        if tool_calls
                        else {}
                    ),
                }
            )
            if not tool_calls:
                metrics["final_answer"] = message.content
                metrics["stop"] = "answered"
                break
            for tc in tool_calls:
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": await _run_tool(tc, call)}
                )


async def _run_tool(tc: Any, call: Any) -> str:
    """Execute one tool call through the harness and return the text the model will see.

    Malformed arguments still go to ``call``: the harness records them as a failed step
    (counted against the task's call and error budgets) without sending them to the server.
    """
    raw = tc.function.arguments or "{}"
    try:
        args = json.loads(raw)
    except ValueError:
        args = raw
    step = await call(tc.function.name, args)
    if step.ok:
        return json.dumps(step.result)
    return f"Error: {step.error}"
