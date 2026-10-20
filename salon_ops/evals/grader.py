"""Trajectory grader: checks every step an agent took, not just where it ended up.

A task's ``expect`` block may contain:

- ``required``: steps that must appear (in order unless ``"ordered": false``). Each
  requirement consumes its own step: two identical requirements need two calls.
- ``forbidden``: steps that must never appear.
- ``preconditions``: ``[{"step": P, "after": Q}]``: every step matching P must be preceded
  by a step matching Q (e.g. in a reschedule, every successful cancel by a successful
  booking of the new visit), so "cancel the old visit, then book the new one" fails.
- ``max_calls`` / ``max_errors``: efficiency and error budgets.
- ``state``: the exact net change to visits: ``booked`` and ``cancelled`` lists, plus
  ``transient`` (visits booked and cancelled within the task). Omitted lists mean
  "none", so an unrequested book-then-cancel cycle fails even though it nets to nothing.

Plus checks applied to every task: no successful call may name a customer outside the
task's ``customers`` list, and the agent must not crash (``agent_error``).

Step patterns are ``{"tool": ..., "args": {...}, "ok": true|false}``; ``args`` holds
matchers (see ``matches``) and only the listed keys are compared. A key whose matcher is
``{"$optional": m}`` may be absent; if present it must match ``m``.

The trajectory graded is the one the harness recorded, never one the agent reports.
"""

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Step:
    tool: str
    args: dict[str, Any]
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Grade:
    task_id: str
    passed: bool
    checks: list[Check] = field(default_factory=list)

    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- references and matchers -------------------------------------------------------------

_PATH_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def resolve_path(path: str, context: dict[str, Any]) -> Any:
    """Resolve ``setup[0].visit_id`` or ``steps[1].openings[0].opening_id`` in ``context``."""
    value: Any = context
    for name, index in _PATH_TOKEN.findall(path):
        value = value[int(index)] if index else value[name]
    return value


def resolve_refs(value: Any, context: dict[str, Any]) -> Any:
    """Replace ``{"$ref": path}`` objects anywhere inside ``value``."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return resolve_path(value["$ref"], context)
        return {k: resolve_refs(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, context) for v in value]
    return value


def render(template: str, context: dict[str, Any]) -> str:
    """Fill ``{setup[0].visit_id}``-style placeholders in a prompt."""
    return re.sub(r"\{([^{}]+)\}", lambda m: str(resolve_path(m.group(1), context)), template)


def matches(expected: Any, actual: Any) -> bool:
    """Literal equality, or one matcher object:

    ``{"$icontains": s}``, ``{"$prefix": s}``, ``{"$in": [...]}``, ``{"$any": true}``,
    ``{"$optional": m}`` (when present, must match ``m``).
    """
    if isinstance(expected, dict) and len(expected) == 1:
        ((op, operand),) = expected.items()
        if op == "$any":
            return True
        if op == "$optional":
            return matches(operand, actual)
        if op == "$icontains":
            return isinstance(actual, str) and operand.lower() in actual.lower()
        if op == "$prefix":
            return isinstance(actual, str) and actual.startswith(operand)
        if op == "$in":
            return actual in operand
    return expected == actual


def _optional(matcher: Any) -> bool:
    return isinstance(matcher, dict) and set(matcher) == {"$optional"}


def step_matches(pattern: dict[str, Any], step: Step) -> bool:
    if pattern["tool"] != step.tool:
        return False
    if "ok" in pattern and pattern["ok"] != step.ok:
        return False
    for key, matcher in pattern.get("args", {}).items():
        if key not in step.args:
            if not _optional(matcher):
                return False
        elif not matches(matcher, step.args[key]):
            return False
    return True


def _describe(pattern: dict[str, Any]) -> str:
    args = ", ".join(f"{k}={v}" for k, v in pattern.get("args", {}).items())
    ok = "" if "ok" not in pattern else (" ok" if pattern["ok"] else " failing")
    return f"{pattern['tool']}({args}){ok}"


def _assign(patterns: list, items: list, fits: Callable[[Any, Any], bool]) -> bool:
    """True if every pattern can be matched to a *distinct* item (backtracking search).

    Greedy first-fit can wrongly fail (a broad pattern takes the row a narrow one needed)
    or wrongly pass (one step satisfying two identical requirements). Sizes here are tiny.
    """

    def search(i: int, used: frozenset[int]) -> bool:
        if i == len(patterns):
            return True
        return any(
            search(i + 1, used | {j})
            for j, item in enumerate(items)
            if j not in used and fits(patterns[i], item)
        )

    return search(0, frozenset())


# ---- checks ------------------------------------------------------------------------------


def check_required(expect: dict[str, Any], steps: list[Step]) -> Check:
    required = expect.get("required", [])
    if expect.get("ordered", True):
        i = 0
        for step in steps:
            if i < len(required) and step_matches(required[i], step):
                i += 1
        missing = required[i:]
        detail = f"missing or out of order from: {_describe(missing[0])}" if missing else ""
        return Check("required_steps", not missing, detail)
    passed = _assign(required, steps, step_matches)
    detail = (
        ""
        if passed
        else "cannot match each requirement to its own step: "
        + "; ".join(_describe(p) for p in required)
    )
    return Check("required_steps", passed, detail)


def check_forbidden(expect: dict[str, Any], steps: list[Step]) -> Check:
    hits = [
        f"step {i}: {_describe(p)}"
        for p in expect.get("forbidden", [])
        for i, s in enumerate(steps)
        if step_matches(p, s)
    ]
    return Check("forbidden_steps", not hits, "; ".join(hits))


def check_preconditions(expect: dict[str, Any], steps: list[Step]) -> Check:
    problems = [
        f"step {i}: {_describe(rule['step'])} without an earlier {_describe(rule['after'])}"
        for rule in expect.get("preconditions", [])
        for i, s in enumerate(steps)
        if step_matches(rule["step"], s)
        and not any(step_matches(rule["after"], earlier) for earlier in steps[:i])
    ]
    return Check("preconditions", not problems, "; ".join(problems))


def check_customer_scope(customers: list[str], steps: list[Step]) -> Check:
    stray = [
        f"step {i}: {s.tool} for {s.args['customer_id']}"
        for i, s in enumerate(steps)
        if s.ok and "customer_id" in s.args and s.args["customer_id"] not in customers
    ]
    return Check("customer_scope", not stray, "; ".join(stray))


def check_budget(expect: dict[str, Any], steps: list[Step]) -> list[Check]:
    max_calls = expect.get("max_calls", 10)
    max_errors = expect.get("max_errors", 0)
    errors = sum(not s.ok for s in steps)
    return [
        Check("max_calls", len(steps) <= max_calls, f"{len(steps)} calls, limit {max_calls}"),
        Check("max_errors", errors <= max_errors, f"{errors} errors, limit {max_errors}"),
    ]


def _row_fits(pattern: dict[str, Any], row: dict[str, Any]) -> bool:
    return all(matches(v, row.get(k)) for k, v in pattern.items())


def check_state(expect: dict[str, Any], diff: dict[str, list[dict[str, Any]]]) -> Check:
    state = expect.get("state", {})
    problems = []
    for kind in ("booked", "cancelled", "transient"):
        want, got = state.get(kind, []), diff.get(kind, [])
        if len(want) != len(got) or not _assign(want, got, _row_fits):
            problems.append(f"{kind}: expected {want}, got {got}")
    return Check("final_state", not problems, "; ".join(problems))


def grade(
    task: dict[str, Any],
    steps: list[Step],
    diff: dict[str, list[dict[str, Any]]],
    context: dict[str, Any] | None = None,
    agent_error: str | None = None,
) -> Grade:
    """Grade one run. ``context`` resolves ``$ref`` values in expectations (``setup``,
    ``steps``); ``agent_error`` is set when the agent raised instead of finishing."""
    try:
        expect = resolve_refs(task.get("expect", {}), context or {})
    except (KeyError, IndexError, TypeError) as exc:
        return Grade(task["id"], False, [Check("expectations", False, f"unresolvable: {exc!r}")])
    checks = [
        Check("agent_error", agent_error is None, agent_error or ""),
        check_required(expect, steps),
        check_forbidden(expect, steps),
        check_preconditions(expect, steps),
        check_customer_scope(task.get("customers", []), steps),
        *check_budget(expect, steps),
        check_state(expect, diff),
    ]
    return Grade(task["id"], all(c.passed for c in checks), checks)
