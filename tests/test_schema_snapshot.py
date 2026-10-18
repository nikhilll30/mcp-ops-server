"""Schema-drift guard: any change to a tool's name, description, schemas or annotations
fails here until the snapshot is regenerated on purpose.

Agents (and the Part 4 evals) depend on these contracts. To accept a deliberate change:

    UPDATE_SNAPSHOTS=1 python -m pytest tests/test_schema_snapshot.py

and commit the updated snapshot together with the code change, so the diff shows reviewers
exactly what agents will see differently.
"""

import asyncio
import json
import os
from pathlib import Path

from mcp_ops_server.server import build_server

SNAPSHOT = Path(__file__).parent / "snapshots" / "tool_schemas.json"


def _current() -> str:
    tools = asyncio.run(build_server().list_tools())
    data = {
        t.name: {
            "description": t.description,
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
            "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None,
        }
        for t in sorted(tools, key=lambda t: t.name)
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def test_tool_schemas_match_snapshot() -> None:
    current = _current()
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(current)
    assert SNAPSHOT.exists(), "No snapshot yet. Run with UPDATE_SNAPSHOTS=1 to create it."
    assert current == SNAPSHOT.read_text(), (
        "Tool schemas changed. If intended, run "
        "`UPDATE_SNAPSHOTS=1 python -m pytest tests/test_schema_snapshot.py` and commit "
        "tests/snapshots/tool_schemas.json with the change."
    )
