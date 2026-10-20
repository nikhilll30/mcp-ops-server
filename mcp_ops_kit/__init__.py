"""mcp-ops-kit: the domain-free parts of an ops MCP server and its trajectory evals.

Extracted from two working domains in this repo (the clinic in ``mcp_ops_server`` and the
salon in ``salon_ops``), not designed up front. A domain supplies its tools, its store and
an ``AuthConfig``; the kit supplies:

- ``auth``: hashed bearer tokens, scopes and a subject binding, enforced in server code;
- ``limits``: a per-client rate limit, applied before argument validation;
- ``server``: the MCP server wiring, the authenticated HTTP app, ``/healthz``, a
  fail-closed entry point;
- ``sqlite``: one deadline per call over SQLite (lock wait, busy wait, execution);
- ``evals``: the harness, the trajectory grader, the replay and OpenAI agents, the runner
  with its report and CI gate, and an end-to-end smoke test.
"""

__version__ = "0.1.0"
