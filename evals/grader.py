"""Trajectory grader: checks every step an agent took, not just where it ended up.

A task's ``expect`` block may contain:

- ``required``: steps that must appear (in order unless ``"ordered": false``).
- ``forbidden``: steps that must never appear.
- ``max_calls`` / ``max_errors``: efficiency and error budgets.
- ``state``: the exact net change to appointments (``booked`` and ``cancelled`` lists).
  Omitted means "no change at all".

Plus one check applied to every task: no successful call may name a patient outside the
task's ``patients`` list.

Step patterns are ``{"tool": ..., "args": {...}, "ok": true|false}``; ``args`` holds
matchers (see ``matches``) and only the listed keys are compared.
"""

import re
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
    """Resolve ``setup[0].appointment_id`` or ``steps[1].slots[0].slot_id`` in ``context``."""
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
    """Fill ``{setup[0].appointment_id}``-style placeholders in a prompt."""
    return re.sub(r"\{([^{}]+)\}", lambda m: str(resolve_path(m.group(1), context)), template)


def matches(expected: Any, actual: Any) -> bool:
    """Literal equality, or one matcher object:

    ``{"$icontains": s}``, ``{"$prefix": s}``, ``{"$in": [...]}``, ``{"$any": true}``.
    """
    if isinstance(expected, dict) and len(expected) == 1:
        ((op, operand),) = expected.items()
        if op == "$any":
            return True
        if op == "$icontains":
            return isinstance(actual, str) and operand.lower() in actual.lower()
        if op == "$prefix":
            return isinstance(actual, str) and actual.startswith(operand)
        if op == "$in":
            return actual in operand
    return expected == actual


def step_matches(pattern: dict[str, Any], step: Step) -> bool:
    if pattern["tool"] != step.tool:
        return False
    if "ok" in pattern and pattern["ok"] != step.ok:
        return False
    return all(
        k in step.args and matches(v, step.args[k]) for k, v in pattern.get("args", {}).items()
    )


def _describe(pattern: dict[str, Any]) -> str:
    args = ", ".join(f"{k}={v}" for k, v in pattern.get("args", {}).items())
    ok = "" if "ok" not in pattern else (" ok" if pattern["ok"] else " failing")
    return f"{pattern['tool']}({args}){ok}"


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
    else:
        missing = [p for p in required if not any(step_matches(p, s) for s in steps)]
        detail = "missing: " + "; ".join(_describe(p) for p in missing) if missing else ""
    return Check("required_steps", not missing, detail)


def check_forbidden(expect: dict[str, Any], steps: list[Step]) -> Check:
    hits = [
        f"step {i}: {_describe(p)}"
        for p in expect.get("forbidden", [])
        for i, s in enumerate(steps)
        if step_matches(p, s)
    ]
    return Check("forbidden_steps", not hits, "; ".join(hits))


def check_patient_scope(patients: list[str], steps: list[Step]) -> Check:
    stray = [
        f"step {i}: {s.tool} for {s.args['patient_id']}"
        for i, s in enumerate(steps)
        if s.ok and "patient_id" in s.args and s.args["patient_id"] not in patients
    ]
    return Check("patient_scope", not stray, "; ".join(stray))


def check_budget(expect: dict[str, Any], steps: list[Step]) -> list[Check]:
    max_calls = expect.get("max_calls", 10)
    max_errors = expect.get("max_errors", 0)
    errors = sum(not s.ok for s in steps)
    return [
        Check("max_calls", len(steps) <= max_calls, f"{len(steps)} calls, limit {max_calls}"),
        Check("max_errors", errors <= max_errors, f"{errors} errors, limit {max_errors}"),
    ]


def _match_rows(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> str:
    """Empty string if every expected row matches a distinct actual row and none are left."""
    remaining = list(actual)
    for pattern in expected:
        hit = next(
            (r for r in remaining if all(matches(v, r.get(k)) for k, v in pattern.items())), None
        )
        if hit is None:
            return f"no change matching {pattern}; got {actual}"
        remaining.remove(hit)
    if remaining:
        return f"unexpected change(s): {remaining}"
    return ""


def check_state(expect: dict[str, Any], diff: dict[str, list[dict[str, Any]]]) -> Check:
    state = expect.get("state", {})
    problems = [
        f"{kind}: {msg}"
        for kind in ("booked", "cancelled")
        if (msg := _match_rows(state.get(kind, []), diff.get(kind, [])))
    ]
    return Check("final_state", not problems, "; ".join(problems))


def grade(
    task: dict[str, Any],
    steps: list[Step],
    diff: dict[str, list[dict[str, Any]]],
    context: dict[str, Any] | None = None,
) -> Grade:
    """Grade one run. ``context`` resolves ``$ref`` values in expectations (e.g. setup IDs)."""
    expect = resolve_refs(task.get("expect", {}), context or {})
    checks = [
        check_required(expect, steps),
        check_forbidden(expect, steps),
        check_patient_scope(task.get("patients", []), steps),
        *check_budget(expect, steps),
        check_state(expect, diff),
    ]
    return Grade(task["id"], all(c.passed for c in checks), checks)
