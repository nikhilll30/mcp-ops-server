"""The OpenAI agent loop, driven by a fake client: no network, no key, no openai package.

The fake returns scripted Chat Completions responses, so these tests pin the loop itself
(tool definitions sent, tool calls executed through the harness, errors fed back, turn cap,
token accounting) and the live report. The real model is only exercised by the
owner-triggered live-evals workflow."""

import asyncio
import json
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from evals import run as run_mod
from evals.agents_openai import OpenAIAgent
from evals.run import load, report, run_suite


def _task(task_id: str) -> dict[str, Any]:
    return next(t for t in load("tasks.json") if t["id"] == task_id)


def _call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def _response(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeClient:
    """Stands in for AsyncOpenAI: returns scripted responses and records every request.

    Like the real client's connection pool, it is bound to the event loop of its first
    request and fails on any other loop."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._loop: asyncio.AbstractEventLoop | None = None
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        loop = asyncio.get_running_loop()
        if self._loop is not None and loop is not self._loop:
            raise RuntimeError("Event loop is closed")
        self._loop = loop
        self.requests.append(json.loads(json.dumps(kwargs)))  # snapshot, messages mutate
        item = self._responses.pop(0) if self._responses else _response("done")
        if isinstance(item, Exception):
            raise item
        return item


def _run(client: FakeClient, task_id: str, **kw: Any) -> dict[str, Any]:
    agent = OpenAIAgent(model="test-model", client=client, **kw)
    return run_suite(agent, [_task(task_id)], [], prices=(1.0, 2.0))


def test_correct_run_passes_and_is_accounted() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", '{"specialty": "cardiology"}')]),
            _response("Dr. Alder and Dr. Birch are cardiologists."),
        ]
    )
    results = _run(client, "r01-find-cardiologists")
    (r,) = results["tasks"]
    assert r["passed"], r["failed_checks"]
    assert [s["tool"] for s in r["steps"]] == ["search_providers"]

    first = client.requests[0]
    assert first["model"] == "test-model"
    assert (
        first["messages"][0]["role"] == "system" and "2026-11-02" in first["messages"][0]["content"]
    )
    names = {t["function"]["name"] for t in first["tools"]}
    assert "book_appointment" in names
    book = next(t for t in first["tools"] if t["function"]["name"] == "book_appointment")
    assert "patient_id" in book["function"]["parameters"]["properties"]

    # The tool result went back to the model under the right call id.
    tool_msg = client.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert "PRV-" in tool_msg["content"]

    live = results["summary"]["live"]
    assert live["model"] == "test-model"
    assert (live["input_tokens"], live["output_tokens"], live["llm_calls"]) == (200, 20, 2)
    assert live["cost_usd"] == pytest.approx((200 * 1.0 + 20 * 2.0) / 1e6, abs=1e-4)
    assert live["hit_max_turns"] == 0
    md = report(results)
    assert "## Live agent" in md and "Dr. Alder and Dr. Birch" in md and "$0.0002" in md


def test_invalid_arguments_are_recorded_and_graded_but_not_sent() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", "{not json")]),
            _response(tool_calls=[_call("c2", "search_providers", "[1, 2]")]),
            _response(tool_calls=[_call("c3", "search_providers", '{"specialty": "cardiology"}')]),
            _response("Found them."),
        ]
    )
    (r,) = _run(client, "r01-find-cardiologists")["tasks"]
    # Every attempt is in the trajectory and counts against the budgets...
    assert [s["ok"] for s in r["steps"]] == [False, False, True]
    assert r["steps"][0]["args"] == {"_invalid_arguments": "{not json"}
    assert not r["passed"] and "max_errors" in r["failed_checks"]
    # ...the model is told why, and the server never saw the malformed calls.
    for i in (1, 2):
        assert client.requests[i]["messages"][-1]["content"].startswith("Error: invalid arguments")
    assert all(s["latency_ms"] == 0 for s in r["steps"][:2])


def test_server_errors_are_fed_back_and_graded() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", '{"specialty": 5}')]),
            _response("Sorry."),
        ]
    )
    (r,) = _run(client, "r01-find-cardiologists")["tasks"]
    assert client.requests[1]["messages"][-1]["content"].startswith("Error: ")
    assert not r["passed"] and "max_errors" in r["failed_checks"]


def test_turn_cap_stops_a_looping_model() -> None:
    loop = [
        _response(tool_calls=[_call(f"c{i}", "health", "{}")])
        for i in range(10)  # never answers
    ]
    results = _run(FakeClient(loop), "r01-find-cardiologists", max_turns=3)
    (r,) = results["tasks"]
    assert len(r["steps"]) == 3
    assert r["agent_metrics"]["stop"] == "max_turns"
    assert results["summary"]["live"]["hit_max_turns"] == 1
    assert "(hit turn limit)" in report(results)


def test_api_failure_fails_the_task_and_keeps_tokens_already_spent() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", '{"specialty": "cardiology"}')]),
            RuntimeError("rate limited"),
        ]
    )
    results = _run(client, "r01-find-cardiologists")
    (r,) = results["tasks"]
    assert not r["passed"] and "agent_error" in r["failed_checks"]
    assert r["agent_metrics"]["stop"] == "error"
    assert r["agent_metrics"]["input_tokens"] == 100 and r["agent_metrics"]["llm_calls"] == 1
    live = results["summary"]["live"]
    assert live["input_tokens"] == 100 and results["summary"]["agent_errors"] == 1
    assert "(agent error: RuntimeError: rate limited)" in report(results)


def test_one_client_serves_every_task_and_is_closed() -> None:
    client = FakeClient([_response("a"), _response("b"), _response("c")])
    tasks = [
        _task(i) for i in ("r01-find-cardiologists", "r02-north-clinic", "r03-provider-by-name")
    ]
    results = run_suite(OpenAIAgent(model="m", client=client), tasks, [])
    assert [r["agent_error"] for r in results["tasks"]] == [None, None, None]
    assert len(client.requests) == 3 and client.closed


def test_model_is_required() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAIAgent(model="", client=FakeClient([]))


def test_cli_fails_a_live_run_with_agent_errors(monkeypatch, tmp_path) -> None:
    """An outage or a bad key must not produce a green workflow with no measurement."""
    monkeypatch.setattr(
        run_mod,
        "_build_agent",
        lambda args: OpenAIAgent(model=args.model, client=FakeClient([RuntimeError("401")])),
    )
    args = ["--agent", "openai", "--model", "m", "--tasks", "r01-find-cardiologists"]
    code = run_mod.main([*args, "--report", str(tmp_path / "r.md")])
    assert code == 1


def test_cli_requires_model_and_valid_task_ids() -> None:
    with pytest.raises(SystemExit, match="--model"):
        run_mod.main(["--agent", "openai"])
    with pytest.raises(SystemExit, match="unknown task"):
        run_mod.main(["--tasks", "nope"])
    with pytest.raises(SystemExit, match="both"):
        run_mod.main(["--price-in", "1"])


def test_cli_task_filter_runs_only_selected_tasks_and_their_controls(tmp_path) -> None:
    out = tmp_path / "r.json"
    code = run_mod.main(
        [
            "--tasks",
            "r01-find-cardiologists,r04-free-times",
            "--out",
            str(out),
            "--report",
            str(tmp_path / "r.md"),
        ]
    )
    results = json.loads(out.read_text())
    assert code == 0
    assert [t["id"] for t in results["tasks"]] == ["r01-find-cardiologists", "r04-free-times"]
    assert all(
        c["id"]
        in {
            n["id"]
            for n in load("negative.json")
            if n["task"] in {"r01-find-cardiologists", "r04-free-times"}
        }
        for c in results["negative_controls"]
    )


def test_real_sdk_against_a_local_endpoint() -> None:
    """The real AsyncOpenAI client (connection pool, retries) across several tasks, against a
    local stand-in for the API. Catches what a fake client cannot, e.g. a pool bound to a
    closed event loop. No key, no network beyond localhost."""
    openai = pytest.importorskip("openai")
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def chat(request: Any) -> JSONResponse:
        body = await request.json()
        assert body["model"] == "local-model" and body["tools"]
        return JSONResponse(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "local-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            }
        )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])])
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.01)
        client = openai.AsyncOpenAI(
            base_url=f"http://127.0.0.1:{port}/v1", api_key="local-test", max_retries=0
        )
        tasks = [_task(i) for i in ("r01-find-cardiologists", "r02-north-clinic")]
        results = run_suite(OpenAIAgent(model="local-model", client=client), tasks, [])
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert [r["agent_error"] for r in results["tasks"]] == [None, None]
    assert results["summary"]["live"]["input_tokens"] == 10
