"""Builds the one-page PDF brief for a part.

Usage:
    pip install -e ".[docs]"
    python docs/briefs/build_brief.py        # latest part
    python docs/briefs/build_brief.py 1      # a specific part
"""

import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#1b1f24")
MUTED = colors.HexColor("#5b6470")
ACCENT = colors.HexColor("#2f5d8a")
RULE = colors.HexColor("#d8dde3")
CODE_BG = colors.HexColor("#f3f5f7")

title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=INK)
sub = ParagraphStyle("s", fontName="Helvetica", fontSize=9, leading=12, textColor=MUTED)
h = ParagraphStyle(
    "h",
    fontName="Helvetica-Bold",
    fontSize=10.5,
    leading=13,
    textColor=ACCENT,
    spaceBefore=7,
    spaceAfter=2,
)
body = ParagraphStyle(
    "b",
    fontName="Helvetica",
    fontSize=8.8,
    leading=11.6,
    textColor=INK,
    alignment=TA_LEFT,
)
bullet = ParagraphStyle("bl", parent=body, leftIndent=10, bulletIndent=0, spaceAfter=1.5)
code = ParagraphStyle("c", fontName="Courier", fontSize=8, leading=10.5, textColor=INK)


def bl(text: str) -> Paragraph:
    return Paragraph(text, bullet, bulletText="•")


def section(name: str) -> Paragraph:
    return Paragraph(name.upper(), h)


def table(rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(c, body) for c in r] for r in rows]
    t = Table(data, colWidths=widths)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, ACCENT),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    for i in range(len(rows[0])):
        t.setStyle(TableStyle([("FONTNAME", (i, 0), (i, 0), "Helvetica-Bold")]))
    return t


def codebox(lines: list[str]) -> Table:
    t = Table(
        [[Paragraph("<br/>".join(ln.replace(" ", "&nbsp;") for ln in lines), code)]],
        colWidths=[7.0 * inch],
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def part1() -> list:
    w = 7.0 * inch
    return [
        Paragraph("Part 1 Brief · Foundation", title),
        Paragraph(
            "mcp-ops-server · 17 Oct 2026 · PR #1 · Status: CI green on push and PR, ready to merge",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            "<b>A running MCP server</b> with one <font face='Courier'>health</font> tool, reachable "
            "over Streamable HTTP at <font face='Courier'>/mcp</font>. Verified by a real client "
            "call, not just unit tests: it returned <font face='Courier'>{status: ok, version: 0.1.0}</font>."
        ),
        bl(
            "<b>CI on every push and PR:</b> lint (ruff), format check, tests (pytest), and a secret "
            "scan (gitleaks) over full git history."
        ),
        bl(
            "<b>The project's operating system:</b> <font face='Courier'>comms/PLAN.md</font> (architecture "
            "and 6 parts), <font face='Courier'>STATUS.md</font>, <font face='Courier'>AGENTS.md</font> "
            "(rules any coding agent reads), <font face='Courier'>FAILURES.md</font>, MIT license."
        ),
        section("How it works"),
        table(
            [
                ["File", "Role"],
                [
                    "mcp_ops_server/server.py",
                    "build_server() creates an MCPServer and registers tools. "
                    "A factory function, so every test gets a fresh server with no shared state.",
                ],
                [
                    "mcp_ops_server/__main__.py",
                    "python -m mcp_ops_server starts it; host and port come "
                    "from env vars (MCP_OPS_HOST, MCP_OPS_PORT), never hard-coded.",
                ],
                [
                    "tests/test_health.py",
                    "Lists tools and calls health through the SDK's own "
                    "call_tool, so the test exercises real MCP schema generation.",
                ],
                [".github/workflows/ci.yml", "Three parallel jobs: lint, test, secrets."],
            ],
            [1.9 * inch, w - 1.9 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>MCP SDK v2, pinned.</b> Most tutorials use <font face='Courier'>FastMCP</font> (v1). In "
            "v2 it is <font face='Courier'>MCPServer</font>. Pinning <font face='Courier'>mcp&gt;=2.3,&lt;3</font> "
            "stops silent breakage from either direction."
        ),
        bl(
            "<b>TypedDict return type.</b> With a plain <font face='Courier'>dict</font>, the SDK sends "
            "only text, so agents get no machine-readable output schema. A TypedDict makes the SDK publish "
            "an output schema and return <font face='Courier'>structured_content</font>. This matters in "
            "Part 4, where evals check tool outputs field by field."
        ),
        bl(
            "<b>Secrets scanned from commit one.</b> The repo goes public later, and history goes with it. "
            "Scanning early means nothing ever needs scrubbing."
        ),
        bl(
            "<b>Synthetic data only</b>, written into the agent rules as a hard rule, not a convention."
        ),
        section("What went wrong (logged in FAILURES.md)"),
        bl(
            "<b>Agent delegation over GitHub failed.</b> @codex got no response; @cursor was gated behind "
            "a paid add-on. New workflow: Claude implements, Codex independently reviews each part."
        ),
        bl(
            "<b>Secret scan failed only on PRs.</b> My first fix scoped it to push. Codex's review (P1) caught "
            "that fork PRs would then skip scanning. Final fix: gitleaks CLI on push and PR, read-only token."
        ),
        bl(
            "<b>Codex review: 3 findings, all fixed.</b> The fork-PR gap above; tests never started the real "
            "server (added an HTTP integration test that fails fast if startup breaks); the brief generator's "
            "dependency was undeclared (added a <font face='Courier'>docs</font> extra)."
        ),
        section("Verify it yourself"),
        codebox(
            [
                'pip install -e ".[dev]"',
                "ruff check . && pytest -q          # 3 passed (incl. live HTTP test)",
                "python -m mcp_ops_server           # serves http://127.0.0.1:8000/mcp",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"I treated tool output schemas as an API contract from day one: the health tool returns a '
            'typed structure, so agents and evals can validate fields instead of parsing text."',
            body,
        ),
        section("Next: Part 2, core server"),
        Paragraph(
            "Synthetic clinic data in SQLite, five tools (search_providers, list_open_slots, "
            "book_appointment, cancel_appointment, get_referral_status), unit tests, then a Codex review. "
            '<b>Your action:</b> merge PR #1 on GitHub, then reply "go".',
            body,
        ),
    ]


def part2() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 2 Brief · Core server", title),
        Paragraph(
            "mcp-ops-server · 18 Oct 2026 · PR #3 · Status: Codex review fixed (2x P1, 3x P2), 66 tests, ready to merge",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            f"<b>Synthetic clinic data</b> ({cf}seed.py</font>, fixed seed): 12 providers across 6 specialties "
            "(4 need a referral), 40 patients, 2,688 thirty-minute slots over 4 weeks, 42 referrals, 128 "
            "pre-booked appointments. The same seed always gives the same IDs."
        ),
        bl(
            f"<b>Five tools</b> over a SQLite store: {cf}search_providers</font>, {cf}list_open_slots</font>, "
            f"{cf}book_appointment</font>, {cf}cancel_appointment</font>, {cf}get_referral_status</font>."
        ),
        bl(
            "<b>66 tests</b> (up from 3), including races between separate database connections and a real "
            "HTTP client running the full find, check, book, cancel flow."
        ),
        section("How it works"),
        table(
            [
                ["Layer", "Role"],
                [
                    "tools/",
                    "Thin wrappers. Typed inputs (enums, ID patterns, bounded limits) and a TypedDict output for "
                    "each tool. Store errors become ToolErrors whose message names the next tool to call.",
                ],
                [
                    "store.py",
                    "All business rules. Booking checks: slot open, patient free at that time, approved referral "
                    "owned by this patient and matching the specialty. One check-then-insert transaction.",
                ],
                [
                    "SQLite schema",
                    f"Partial unique index {cf}(slot_id) WHERE status='booked'</font>: the database itself refuses "
                    "a second booking, so a cancelled slot can be rebooked but an open one is never booked twice.",
                ],
            ],
            [1.2 * inch, w - 1.2 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Rules in the store, constraint in the schema.</b> The app check gives agents a clear error; the "
            "index makes double booking impossible even if a future code path forgets the check."
        ),
        bl(
            "<b>Cancelling needs the owner's patient ID</b>, and someone else's appointment gives the same "
            '"not found" as a fake ID, so the tool can\'t be used to probe which appointments exist.'
        ),
        bl(
            "<b>Deterministic, clock-free data</b>, so Part 4 eval tasks can name exact slots and referrals. "
            "Trade-off: booking past slots is not blocked yet."
        ),
        section("What went wrong (logged in FAILURES.md)"),
        bl(
            "<b>Threads.</b> The SDK runs tools in worker threads. A mutation test with the store lock removed "
            f"crashed with {cf}sqlite3.InterfaceError</font>. Kept the lock and added BEGIN IMMEDIATE."
        ),
        bl(
            f"<b>Regex.</b> pydantic's {cf}\\d</font> matches any Unicode digit, so a patient ID written in "
            f"Arabic-Indic digits passed validation. Switched to {cf}[0-9]</font> and added a test."
        ),
        bl(
            "<b>Codex review: 2x P1, 3x P2, all reproduced, then fixed.</b> A failed COMMIT left the store unable to "
            "write; my race test shared one connection, so it never exercised BEGIN IMMEDIATE; two processes "
            "starting together both tried to create the schema; APT-100000 could be booked but not cancelled; "
            "year-9999 dates crashed. Each fix has a test that fails with the bug put back."
        ),
        section("Verify it yourself"),
        codebox(
            [
                'pip install -e ".[dev]"',
                "ruff check . && python -m pytest -q   # 66 passed",
                "python -m mcp_ops_server              # then call tools at http://127.0.0.1:8000/mcp",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"Double booking is prevented twice: in application code so the agent gets an actionable error, and '
            "as a partial unique index so the database refuses it. The review caught that my race test only "
            "proved the Python lock, so I rewrote it across separate connections and showed each test fails "
            'when its protection is removed."',
            body,
        ),
        section("Next: Part 3, hardening"),
        Paragraph(
            "Bearer auth and scopes enforced in the server, rate limits, payload size and timeouts, and a "
            '"schema drift" snapshot test that flags any change to a tool schema. <b>Your action:</b> merge PR #3, '
            'then reply "go".',
            body,
        ),
    ]


def part3() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 3 Brief · Hardening", title),
        Paragraph(
            "mcp-ops-server · 18 Oct 2026 · PR #4 · Status: Codex review fixed (2x P1, 1x P2), 119 tests, ready to merge",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            "<b>Bearer-token auth.</b> No token or a wrong one gets HTTP 401. Tokens are stored only as SHA-256 "
            "hashes, and the server refuses to start without a tokens file (fails closed)."
        ),
        bl(
            "<b>Scopes and patient binding, checked in server code.</b> Each tool needs its scope "
            f"({cf}read:schedule</font>, {cf}write:schedule</font>, {cf}read:referrals</font>). A token "
            "bound to one patient can only book, cancel or read referrals for that patient."
        ),
        bl(
            "<b>Limits:</b> a per-client rate limit on every tool call, a 64 KiB request cap (SDK default: 4 MiB), "
            "one time limit per call covering all waits, and 100 sessions with a 10-minute idle timeout."
        ),
        bl(
            "<b>Simulated clinic clock</b> (default Mon 2 Nov 2026, 08:00): past slots are hidden and can't be "
            "booked. <b>Schema snapshot test:</b> any change to what agents see fails CI until approved."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "Role"],
                [
                    "SDK middleware",
                    "Verifies the bearer token on every HTTP request and puts it in a context variable "
                    "the tools can read.",
                ],
                [
                    "Guard",
                    "Every tool call is rate-limited before its arguments are even validated. Then each tool "
                    "checks: authenticated? has the scope? right patient? Refusals never touch the database.",
                ],
                [
                    "Store",
                    "One deadline per call, set before any waiting. Lock waits and SQLite busy waits use the "
                    "time left; a slow statement is interrupted; a late write rolls back instead of committing.",
                ],
            ],
            [1.2 * inch, w - 1.2 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Security lives in code, not prompts.</b> Tools check the token themselves, so even a call "
            "that skips HTTP (as the unit tests do) is refused without one."
        ),
        bl(
            "<b>Interrupt inside SQLite, don't abandon a thread.</b> A thread can't be killed; a thread timeout "
            'would report "failed" while the booking still committed. Interrupting SQLite rolls it back.'
        ),
        bl(
            '<b>Simulated time.</b> A real clock would make every synthetic slot "past" after November 2026 '
            f"and break the evals. {cf}MCP_OPS_NOW=system</font> switches to the real clock."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>The schema test caught a real bug on its first CI run:</b> Python 3.12 keeps docstring indents, 3.13 "
            "strips them, so agents saw different tool descriptions. Now normalized; tests pass on both."
        ),
        bl(
            "<b>The obvious timeout was wrong</b> (see above). Caught at design time; a test proves a timed-out "
            "write never lands."
        ),
        bl(
            "<b>Codex review: 2x P1, 1x P2, all reproduced, then fixed.</b> The time limit ignored lock waits "
            "(a call took 336 ms against a 200 ms limit). Tokens sharing a client name could delete each "
            "other's sessions. Invalid calls and health skipped the rate limit. Each fix has a test that "
            "fails with the bug put back."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "python -m mcp_ops_server.auth issue --file tokens.json --client dev \\",
                "    --scope read:schedule --scope write:schedule --scope read:referrals   # prints token",
                "MCP_OPS_TOKENS_FILE=tokens.json python -m mcp_ops_server   # 401 without the token",
                "ruff check . && python -m pytest -q                        # 119 passed",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"Authorization is enforced inside every tool, not in the prompt and not only at the HTTP edge. '
            "A token bound to one patient can't book for another, even if the model is tricked into asking. "
            'And every protection has a test that fails when I delete it."',
            body,
        ),
        section("Next: Part 4, evals"),
        Paragraph(
            "A golden set of about 30 tasks, a step-by-step (trajectory) grader, a replay agent that needs no LLM, "
            'and a report. <b>Your action:</b> merge PR #4, then reply "go".',
            body,
        ),
    ]


def part4() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 4 Brief · Evals", title),
        Paragraph(
            "mcp-ops-server · 18 Oct 2026 · PR #5 · Status: 31/31 tasks pass, 7/7 negative controls caught, awaiting Codex review",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            "<b>31 golden tasks</b> in 7 categories (lookups, bookings, referral bookings, refusals, "
            "cancellations, auth, conflicts). Each has a prompt, who it runs as (staff, read-only, or a "
            "patient-bound portal token) and what must and must not happen."
        ),
        bl(
            "<b>A trajectory grader</b> that checks every step, not just the final answer: required steps "
            "in order, forbidden steps, no touching other patients, call and error budgets, and the "
            "<b>exact change in the database</b>."
        ),
        bl(
            "<b>A replay agent</b> (no LLM, free, deterministic) that runs in CI, plus "
            f"{cf}evals/report.md</font>: per-task results, pass rate by category, tool latency."
        ),
        bl(
            "<b>7 negative controls:</b> deliberately bad runs (wrong patient, skipped referral check, "
            "writes during a lookup, flailing) that the grader must fail. Together they cover every check."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "Role"],
                [
                    "harness.py",
                    "Starts the real server with issued tokens and a file database; agents talk to it over "
                    "MCP like any client. Diffs the appointments table before and after each task.",
                ],
                [
                    "grader.py",
                    "Six checks per task. final_state compares the actual database change to the expected "
                    "bookings and cancellations, so a correct-sounding but wrong run still fails.",
                ],
                [
                    "agents.py",
                    "One Agent interface. Replay now; the Part 5 LLM agent plugs into the same slot and is "
                    "graded identically.",
                ],
            ],
            [1.2 * inch, w - 1.2 * inch],
        ),
        section("Why it was done this way"),
        bl(
            '<b>Grade the database, not the reply.</b> An agent can say "booked!" and book the wrong '
            "patient. The diff catches that; a final-answer check would not."
        ),
        bl(
            "<b>Grade the steps.</b> Booking with a valid referral ID but never checking its status can "
            "still succeed. Only step-level grading sees that the check was skipped."
        ),
        bl(
            "<b>Evals use real auth.</b> 7 of the 64 calls are expected refusals (scope, patient binding, "
            "double booking), so the suite also tests Part 3's rules end to end."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>A loosened rule was invisible to every golden task.</b> I made pending referrals bookable as "
            "a test; all 31 tasks still passed, because a well-behaved agent never tries it. Only a negative "
            "control failed. Now the controls must cover every check."
        ),
        bl(
            "<b>Controls crashed on a shared database</b> by reusing a slot the golden run had booked. They now "
            "get a fresh server, and a broken setup fails one task instead of the run."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "python -m evals.run       # 31/31 tasks passed; 7/7 negative controls caught",
                "cat evals/report.md       # per-task results, categories, latency",
                "ruff check . && python -m pytest -q   # 150 passed (includes the full suite)",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"My eval grades what the agent did, step by step, and what changed in the database, not what it '
            "said. And I test the grader: deliberately bad runs must fail. One caught a server regression that "
            'every well-behaved task missed."',
            body,
        ),
        section("Next: Part 5, CI gate and real numbers"),
        Paragraph(
            "A dedicated eval job that blocks merges, and a live LLM agent on the same 31 tasks to measure pass "
            "rate, cost and latency. That needs an API key as a secret, your call. <b>Your action:</b> paste "
            "the Codex prompt, then merge PR #5 once fixes are in.",
            body,
        ),
    ]


PARTS = {
    1: (part1, "part-1-foundation", "Part 1 Brief: Foundation"),
    2: (part2, "part-2-core-server", "Part 2 Brief: Core server"),
    3: (part3, "part-3-hardening", "Part 3 Brief: Hardening"),
    4: (part4, "part-4-evals", "Part 4 Brief: Evals"),
}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else max(PARTS)
    build, slug, doc_title = PARTS[n]
    out = f"docs/briefs/{slug}.pdf"
    doc = SimpleDocTemplate(
        out,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.55 * inch,
        title=doc_title,
        author="Nikhil",
    )
    doc.build(build())
    print(out)
