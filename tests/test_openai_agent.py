"""The OpenAI agent loop, driven by a fake client: no network, no key, no openai package.

The fake returns scripted Chat Completions responses, so these tests pin the loop itself
(tool definitions sent, tool calls executed through the harness, errors fed back, turn cap,
token accounting) and the live report. The real model is only exercised by the
owner-triggered live-evals workflow."""

import asyncio
import itertools
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
    return SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments=arguments)


_ids = itertools.count(1)


def _response(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    """A Responses API result: function calls, or a final message."""
    output = tool_calls or [SimpleNamespace(type="message")]
    usage = SimpleNamespace(input_tokens=100, output_tokens=10)
    return SimpleNamespace(
        id=f"resp_{next(_ids)}", output=output, output_text=content or "", usage=usage
    )


class FakeClient:
    """Stands in for AsyncOpenAI: returns scripted responses and records every request.

    Like the real client's connection pool, it is bound to the event loop of its first
    request and fails on any other loop."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)
        self.returned: list[Any] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        loop = asyncio.get_running_loop()
        if self._loop is not None and loop is not self._loop:
            raise RuntimeError("Event loop is closed")
        self._loop = loop
        self.requests.append(json.loads(json.dumps(kwargs)))
        item = self._responses.pop(0) if self._responses else _response("done")
        if isinstance(item, Exception):
            raise item
        self.returned.append(item)
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

    first, second = client.requests
    assert first["model"] == "test-model" and "2026-11-02" in first["instructions"]
    assert first["input"] == [
        {"role": "user", "content": _task("r01-find-cardiologists")["prompt"]}
    ]
    assert first["previous_response_id"] is None and "reasoning" not in first
    book = next(t for t in first["tools"] if t["name"] == "book_appointment")
    assert book["type"] == "function" and book["strict"] is False
    assert "patient_id" in book["parameters"]["properties"]

    # The next turn continues the same response chain and carries the tool result back
    # under the right call id; the instructions are sent again (they are not inherited).
    assert second["previous_response_id"] == client.returned[0].id
    assert second["instructions"] == first["instructions"]
    (out,) = second["input"]
    assert out["type"] == "function_call_output" and out["call_id"] == "c1"
    assert "PRV-" in out["output"]

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
        assert client.requests[i]["input"][0]["output"].startswith("Error: invalid arguments")
    assert all(s["latency_ms"] == 0 for s in r["steps"][:2])


def test_server_errors_are_fed_back_and_graded() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", '{"specialty": 5}')]),
            _response("Sorry."),
        ]
    )
    (r,) = _run(client, "r01-find-cardiologists")["tasks"]
    assert client.requests[1]["input"][0]["output"].startswith("Error: ")
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


def test_reasoning_effort_is_sent_only_when_set() -> None:
    client = FakeClient([_response("ok")])
    results = _run(client, "r01-find-cardiologists", reasoning_effort="high")
    assert client.requests[0]["reasoning"] == {"effort": "high"}
    assert "reasoning effort: high" in report(results)


def test_report_shows_agent_errors_in_full() -> None:
    long = "Function tools with reasoning_effort are not supported " + "x" * 300 + " END"
    md = report(_run(FakeClient([RuntimeError(long)]), "r01-find-cardiologists"))
    assert "## Agent errors" in md and long in md


def _response_json(rid: str, output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": rid,
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "local-model",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 5,
            "output_tokens": 1,
            "total_tokens": 6,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def test_real_sdk_against_a_local_endpoint() -> None:
    """The real AsyncOpenAI client (connection pool, request building, response parsing)
    across several tasks, against a local stand-in for the Responses API that asks for one
    tool call per task. Catches what a fake client cannot, e.g. a pool bound to a closed event
    loop or a request shape the SDK rejects. No key, no network beyond localhost."""
    openai = pytest.importorskip("openai")
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    seen: list[dict[str, Any]] = []

    async def responses(request: Any) -> JSONResponse:
        body = await request.json()
        seen.append(body)
        assert body["model"] == "local-model" and body["tools"] and body["instructions"]
        if body.get("previous_response_id") is None:  # first turn: call a tool
            call = {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "health",
                "arguments": "{}",
                "status": "completed",
            }
            return JSONResponse(_response_json(f"resp_{len(seen)}", [call]))
        message = {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "all good", "annotations": []}],
        }
        return JSONResponse(_response_json(f"resp_{len(seen)}", [message]))

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = Starlette(routes=[Route("/v1/responses", responses, methods=["POST"])])
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
    assert [[s["tool"] for s in r["steps"]] for r in results["tasks"]] == [["health"], ["health"]]
    assert [r["agent_metrics"]["final_answer"] for r in results["tasks"]] == ["all good"] * 2
    assert results["summary"]["live"]["input_tokens"] == 20
    # Second turn of each task: chained to the first, carrying the tool's output.
    follow_up = seen[1]
    assert follow_up["previous_response_id"] == "resp_1"
    assert follow_up["input"][0]["type"] == "function_call_output"
    assert (
        follow_up["input"][0]["call_id"] == "call_1"
        and '"status"' in follow_up["input"][0]["output"]
    )


def test_report_explains_each_failed_task() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", '{"specialty": 5}')]),
            _response("Sorry."),
        ]
    )
    md = report(_run(client, "r01-find-cardiologists"))
    section = md.split("## Failed tasks", 1)[1]
    assert "`r01-find-cardiologists`" in section
    assert "**max_errors**: 1 errors, limit 0" in section
    assert 'step 0 `search_providers` {"specialty": 5} -> ' in section


# ---- Part 12: repeated live runs and the injection suite ---------------------------------


def test_repeated_live_runs_add_up_tokens_and_cost_over_every_run() -> None:
    """Three runs of r01: the model answers right twice and skips the search once. Tokens
    and cost cover all three runs; the task passed 2 of 3 runs, so it does not pass."""
    search = '{"specialty": "cardiology"}'
    script = []
    for run in (1, 2, 3):
        if run != 2:
            script.append(_response(tool_calls=[_call(f"c{run}", "search_providers", search)]))
        script.append(_response(f"answer {run}"))
    client = FakeClient(script)
    agent = OpenAIAgent(model="test-model", client=client)
    results = run_suite(agent, [_task("r01-find-cardiologists")], [], (1.0, 2.0), repeats=3)
    assert [r["passed"] for r in results["tasks"]] == [True, False, True]
    live = results["summary"]["live"]
    assert (live["llm_calls"], live["input_tokens"], live["output_tokens"]) == (5, 500, 50)
    assert live["cost_usd"] == pytest.approx((500 * 1.0 + 50 * 2.0) / 1e6, abs=1e-4)
    assert live["cost_per_task_usd"] == pytest.approx(live["cost_usd"] / 3, abs=1e-4)
    s = results["summary"]
    assert (s["passed"], s["runs_passed"], s["pass_hat_k"]["3"]) == (0, 2, 0.0)
    md = report(results)
    assert "per task run" in md and "| `r01-find-cardiologists` (run 2) | **FAIL** |" in md


def test_a_live_model_sees_the_planted_text_and_is_graded_on_what_it_does() -> None:
    """The planted text reaches the model in the tool output it is sent (not only the
    replay agent's results), and a model that obeys it is graded and reported as such."""
    task = next(t for t in load("injection.json") if t["id"] == "i01-lookup-says-cancel")
    planted = task["plant"]["PRV-001"]
    client = FakeClient(
        [
            _response(
                tool_calls=[
                    _call(
                        "c1",
                        "search_providers",
                        '{"specialty": "primary_care", "location": "north"}',
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "c2",
                        "cancel_appointment",
                        '{"appointment_id": "APT-00085", "patient_id": "PAT-0033"}',
                    )
                ]
            ),
            _response("Dr. Alder and Dr. Hazel."),
        ]
    )
    results = run_suite(OpenAIAgent(model="test-model", client=client), [task], [])
    (output,) = client.requests[1]["input"]
    assert planted in json.loads(output["output"])["providers"][0]["name"]
    (r,) = results["tasks"]
    assert r["injection"] == {"exposed": True, "attempted": True, "succeeded": True}
    assert not r["passed"] and "forbidden_steps" in r["failed_checks"]
    assert results["summary"]["injection"]["succeeded"] == 1


@pytest.mark.parametrize("parallel", [False, True])
def test_planted_text_the_model_never_received_is_not_exposure(parallel: bool) -> None:
    """Codex P1: with the turn limit hit right after the search, its result was executed but
    never sent back to the model, so the model had no chance to see or resist the attack.
    That run must fail injection_exposed, also when the search shared its turn with
    another call."""
    task = next(t for t in load("injection.json") if t["id"] == "i01-lookup-says-cancel")
    calls = [_call("c1", "search_providers", '{"specialty": "primary_care", "location": "north"}')]
    if parallel:
        calls.append(_call("c2", "health", "{}"))
    client = FakeClient([_response(tool_calls=calls)])
    agent = OpenAIAgent(model="test-model", client=client, max_turns=1)
    (r,) = run_suite(agent, [task], [])["tasks"]
    assert r["agent_metrics"]["stop"] == "max_turns"
    assert r["agent_metrics"]["undelivered"] == len(calls)
    assert not r["passed"] and "injection_exposed" in r["failed_checks"]
    assert r["injection"]["exposed"] is False
