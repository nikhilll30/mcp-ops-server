"""The OpenAI agent loop, driven by a fake client: no network, no key, no openai package.

The fake returns scripted Chat Completions responses, so these tests pin the loop itself
(tool definitions sent, tool calls executed through the harness, errors fed back, turn cap,
token accounting) and the live report. The real model is only exercised by the
owner-triggered live-evals workflow."""

import json
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
    """Stands in for AsyncOpenAI: returns scripted responses and records every request."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
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


def test_invalid_arguments_are_not_sent_to_the_server() -> None:
    client = FakeClient(
        [
            _response(tool_calls=[_call("c1", "search_providers", "{not json")]),
            _response(tool_calls=[_call("c2", "search_providers", "[1, 2]")]),
            _response(tool_calls=[_call("c3", "search_providers", '{"specialty": "cardiology"}')]),
            _response("Found them."),
        ]
    )
    (r,) = _run(client, "r01-find-cardiologists")["tasks"]
    assert len(r["steps"]) == 1  # only the valid call reached the server
    assert r["passed"], r["failed_checks"]
    assert client.requests[1]["messages"][-1]["content"].startswith("Error: invalid JSON")
    assert client.requests[2]["messages"][-1]["content"].startswith("Error: invalid JSON")


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


def test_api_failure_fails_the_task_not_the_run() -> None:
    (r,) = _run(FakeClient([RuntimeError("rate limited")]), "r01-find-cardiologists")["tasks"]
    assert not r["passed"]
    assert "agent_error" in r["failed_checks"]
    assert r["agent_metrics"] is None


def test_model_is_required() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAIAgent(model="", client=FakeClient([]))


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
