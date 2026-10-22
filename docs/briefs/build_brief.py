"""Builds the one-page PDF brief for a part.

Usage:
    pip install -e ".[docs]"
    python docs/briefs/build_brief.py        # latest part
    python docs/briefs/build_brief.py 1      # a specific part
    python docs/briefs/build_brief.py phase-2   # a phase plan
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
            "mcp-ops-server · 18 Oct 2026 · PR #5 · Status: Codex review fixed (5x P1, 5x P2); 31/31 tasks, 12/12 controls; ready to merge",
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
            "<b>12 negative controls:</b> deliberately bad runs, including adversarial ones that try to game "
            "the grader (checking the wrong referral, booking then cancelling to hide it). Each must fail."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "Role"],
                [
                    "harness.py",
                    "A fresh seeded database and real server per task (same app as the CLI, real auth), "
                    "clock pinned. Records every call the agent makes and diffs the appointments table.",
                ],
                [
                    "grader.py",
                    "Eight checks per task, on the recorded calls (not the agent's report) and on the exact "
                    "database change, so a correct-sounding but wrong run still fails.",
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
            "<b>Evals use real auth.</b> 8 of the 66 calls are expected refusals (scope, patient binding, "
            "double booking, past slot), so the suite also tests Part 3's rules end to end."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>A loosened rule was invisible to every golden task.</b> I made pending referrals bookable as "
            "a test; all 31 tasks still passed, because a well-behaved agent never tries it. Only a negative "
            "control failed. Now the controls must cover every check."
        ),
        bl(
            "<b>Codex review: the grader could be fooled 9 ways.</b> Agents reported their own steps; "
            '"earliest" was checked by date only; any referral check counted; book-then-cancel vanished; '
            "one bad run poisoned the next. All fixed, each with a test or control that fails without the fix."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "python -m evals.run       # 31/31 tasks passed; 12/12 negative controls caught",
                "cat evals/report.md       # per-task results, categories, latency",
                "ruff check . && python -m pytest -q   # 159 passed (includes the full suite)",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"My eval grades what the agent actually did, recorded by the harness, and what changed in the '
            "database, not what it said. Then I attacked my own grader: an independent review found 9 ways to "
            'pass a bad run, and each is now a negative control that must fail."',
            body,
        ),
        section("Next: Part 5, CI gate and real numbers"),
        Paragraph(
            "A dedicated eval job that blocks merges, and a live LLM agent on the same 31 tasks to measure pass "
            "rate, cost and latency. That needs an API key as a secret, your call. <b>Your action:</b> merge "
            'PR #5, then reply "go" with the model, API and run frequency for the live agent.',
            body,
        ),
    ]


def part5() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 5 Brief · CI eval gate and live evals", title),
        Paragraph(
            "mcp-ops-server · 19 Oct 2026 · PRs #6-#12 · Status: done; Luna 30/31 ($0.046), Sol 30/31 ($0.339)",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            "<b>An eval gate on every push and PR:</b> a dedicated CI job runs all 31 golden tasks against the "
            "real server and all 12 negative controls. Free (no model, no secrets); any failure fails the build. "
            "The report lands in the job summary and as an artifact."
        ),
        bl(
            "<b>A live OpenAI agent</b> on the same tasks, graded by the same grader. Paid, so it runs only when "
            "you trigger it: Actions &gt; Live evals &gt; Run workflow, with the model ID as an input."
        ),
        bl(
            "<b>Live metrics:</b> pass rate, tokens, cost per task (from prices you pass), model and task latency "
            "p50/p99, turn-limit hits, and each final answer (shown, never graded)."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "Role"],
                [
                    "agents_openai.py",
                    "Responses API tool-calling loop. The model sees exactly the tools the server advertises, "
                    "and every call goes through the harness's recorded call(), so it cannot grade itself.",
                ],
                [
                    "ci.yml: evals",
                    "Replay agent + negative controls on every push and PR. Mark it a required check to block merges.",
                ],
                [
                    "live-evals.yml",
                    "workflow_dispatch only. The key lives in a live-evals environment that only main can use "
                    "(enforced by GitHub, not by this file). Inputs via env (no script injection), 30-min timeout.",
                ],
            ],
            [1.35 * inch, w - 1.35 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Free gate, paid measurement.</b> Replay is deterministic, so it can block merges. A model's score "
            "varies run to run, so live runs report a number and do not fail on it unless you ask "
            f"({cf}--min-pass-rate</font>). An API error does fail the run: it measured nothing."
        ),
        bl(
            "<b>Manual trigger only, main only.</b> No schedule and no PR trigger, so cost stays under your "
            "control, and code that has not been merged never runs with the key."
        ),
        bl(
            "<b>Optional dependency.</b> openai is a separate install extra imported lazily; the server and the "
            "CI gate never install it. Tests use a fake client and the real SDK against a local endpoint: no key."
        ),
        section("Results (all 31 tasks, default reasoning effort, one run each)"),
        table(
            [
                ["Run", "Passed", "Cost*", "Task p50 / p99", "Failures"],
                [
                    "Luna, before fixes",
                    "29/31",
                    "$0.038",
                    "5.5 s / 35.8 s",
                    "k01, f01: both needed info no tool gave",
                ],
                [
                    "Luna, after fixes",
                    "30/31",
                    "$0.046",
                    "5.1 s / 25.0 s",
                    "k01: right slot, but 16 calls (limit 6)",
                ],
                [
                    "Sol, same code",
                    "30/31",
                    "$0.339",
                    "8.1 s / 17.4 s",
                    "f01: stopped after a slot clash (prompt rule ambiguous)",
                ],
            ],
            [1.2 * inch, 0.55 * inch, 0.6 * inch, 1.05 * inch, w - 3.4 * inch],
        ),
        Paragraph(
            "*Upper bound (all input at full rate). 12/12 controls caught and every refusal, auth and "
            "referral-safety task passed in every run. Sol: 2.8x fewer tokens; Luna: ~7x cheaper per task.",
            body,
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>Codex review: 4x P1, 2x P2, all fixed with tests.</b> Among them: the real client broke from the "
            "2nd task on (my fake client had no connection pool), and a run where every API call failed was green."
        ),
        bl(
            "<b>The live model found four defects of mine.</b> Chat Completions rejects tools plus reasoning "
            "(moved to the Responses API); the report hid the errors (now shown in full); 'Range at most 31 "
            "days' was ambiguous (now exact); two tasks needed information only I had, from reading the seed. "
            "I fixed the server or the task, never the grader, and wrote the rule down."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "python -m evals.run       # 31/31 tasks passed; 12/12 negative controls caught",
                "ruff check . && python -m pytest -q   # 174 passed (14 for the live agent loop)",
                "Actions > Live evals > a run > Summary # report, incl. each failed call and its error",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"Every PR runs a free trajectory eval that blocks merges; real-model runs are a separate, paid '
            "measurement graded by the same code. The first live runs mostly found bugs in my own eval, not "
            'in the model. The rule I took from it: a task may not need information no tool provides."',
            body,
        ),
        section("Next"),
        Paragraph(
            "<b>Your actions:</b> mark <b>evals</b>, lint and test as required checks on main. <b>Part 6 (Ship)</b> "
            "waits for your 'go'.",
            body,
        ),
    ]


def part6() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 6 Brief · Ship", title),
        Paragraph(
            "mcp-ops-server · 19 Oct 2026 · PR #13 · Status: Codex review fixed (2x P1, 4x P2); ready to merge",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            "<b>A production container:</b> pinned Python 3.12 base, non-root user, Docker health check on a new "
            f"{cf}/healthz</font> (no data, no token); the tokens file is mounted at run time, never baked in."
        ),
        bl(
            "<b>CI runs the image as shipped</b> on every PR: build, issue a token through the image, wait for "
            "healthy, then a real MCP round trip (401 without a token, 6 tools, search, book, cancel). From main, "
            "after every check passes, the image is published to GitHub Container Registry."
        ),
        bl(
            "<b>The README a reviewer reads first:</b> live results (Luna and Sol, 30/31 each), run-it-in-30-seconds "
            "with Docker, architecture, tools and scopes, security model, known limits; plus a demo GIF rendered "
            "from a real run by a script in the repo."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "Role"],
                [
                    "Dockerfile",
                    "Installs only the server package; runs as uid 10001; HEALTHCHECK calls /healthz.",
                ],
                [
                    "scripts/smoke_test.py",
                    "End-to-end check over HTTP. CI runs it against the container; the test suite runs it against "
                    "the source server, so it cannot go stale.",
                ],
                ["ci.yml: container", "Build + smoke-test the image on every push and PR."],
                [
                    "ci.yml: publish",
                    "Pushes the exact tested image (ID-checked); main only, after all checks; sole packages: write.",
                ],
                [
                    "docs/demo/record_demo.py",
                    "Runs the real commands and renders their actual output to demo.gif.",
                ],
            ],
            [1.85 * inch, w - 1.85 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>No AWS (your call).</b> A live URL proves it ran once; CI booting the image on every change proves "
            "it keeps working, at $0 and with no public write endpoint to operate. Any container host runs it as is."
        ),
        bl(
            "<b>Honest demo.</b> The GIF is generated from real command output, and the token is never shown. "
            "Every README number links to where it was measured."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>Non-root cut both ways.</b> The container user could not read a tokens file in a 0700 folder, so the "
            "server exited and my wait loop fell through silently. Now: README uses a plain mkdir (tested as a "
            "separate host user), the waits fail loudly, and a missing tokens file prints one clear line."
        ),
        bl(
            "<b>Codex: 2x P1, 4x P2, all fixed.</b> Publish rebuilt the image instead of shipping the tested one "
            "(now it ships the tested image, ID-checked); README revocation needed a restart; stale runs could move "
            "'latest'; health check ignored the port; smoke test trusted write responses (now reads them back)."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "ruff check . && python -m pytest -q   # 179 passed (incl. smoke script vs real server)",
                "docker build -t mcp-ops-server . && see README 'Run it' (3 commands)",
                "PR #13 > Checks > container             # the image, built and smoke-tested",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"It ships as a container that CI boots and tests end to end on every PR, with auth on, as a non-root '
            'user. Putting it in the cloud is configuration, not code."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "1) Merge PR #13. 2) Add <b>container</b> to the ruleset's required checks. 3) Settings &gt; "
            "General &gt; Change visibility &gt; Public (the ruleset then enforces). 4) After the first publish run: "
            "Packages &gt; mcp-ops-server &gt; Package settings &gt; Public, then check the README's docker command.",
            body,
        ),
    ]


def part7() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 7 Brief · Backend protocol + FHIR R4", title),
        Paragraph(
            "mcp-ops-server · 20 Oct 2026 · PR #16 · Status: Codex review fixed (3x P1, 3x P2) + 1 Bugbot; ready to merge",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            f"<b>One {cf}Backend</font> protocol</b> that tools and the eval harness depend on. Today's SQLite "
            "store moved behind it unchanged: 179 tests, schema snapshot and eval gate passed before any FHIR code."
        ),
        bl(
            "<b>A FHIR R4 backend</b> over plain REST (httpx2, no new dependency): PractitionerRole = provider, "
            "Schedule + Slot = open slots, Appointment = booking, ServiceRequest = referral. Synthetic seed exported "
            "as deterministic transaction Bundles (same IDs, patients are identifiers only); load, reset, export CLI."
        ),
        bl(
            f"<b>Parity enforced in CI:</b> a new {cf}fhir</font> job runs HAPI (pinned by digest), every tool test "
            "on both backends (294 tests), and the eval gate on FHIR. A test asserts the whole eval run (every result, "
            "error text, state change and grade) is identical on both."
        ),
        section("How it works"),
        table(
            [
                ["Risk", "How the FHIR backend handles it"],
                [
                    "Double booking",
                    "Read the sequence first, check everything, then one transaction: Slot busy with If-Match, sequence "
                    "+1 with If-Match, new Appointment. 409/412 = someone moved: re-check from scratch until the deadline.",
                ],
                [
                    "Paging",
                    "Every search follows every next link (tests use page size 1 and 7); foreign links refused.",
                ],
                [
                    "Stale search",
                    "Cache-Control: no-cache on every request (HAPI caches searches up to 60 s).",
                ],
                [
                    "Time zones",
                    "Slots stored as UTC instants; tools still see clinic-local time (fixed UTC-05:00).",
                ],
                [
                    "Bad config",
                    "Fails closed at start, one line: unreachable, not R4, or no seed loaded.",
                ],
            ],
            [1.2 * inch, w - 1.2 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Two guards, each proven alone.</b> The slot If-Match is the per-slot guard the plan asked for; the "
            "sequence keeps APT numbering identical to SQLite and serialises bookings like BEGIN IMMEDIATE, so the "
            "patient-overlap check cannot be raced either."
        ),
        bl(
            "<b>Shared error messages</b> in base.py: an agent sees the same text on either store, so evals compare."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>A real race, found by breaking my own guard.</b> With the slot If-Match removed, 20 threads booked one "
            "slot 3-5 times: the sequence was read after the slot. Fixed by reading it first. Mutation matrix "
            "(15 runs each): either guard alone 0 double bookings, neither 15/15."
        ),
        bl(
            "<b>Codex 3x P1, 3x P2, all reproduced and fixed.</b> A write that timed out was reported "
            "'cancelled' yet had committed: now it is read back, or reported as 'may or may not' with a safe retry. "
            "The deadline bounded each socket wait, not the call (a dribbling server ran 3x over): now enforced on "
            "the wait. Also: past slots crowded out upcoming ones (Bugbot too), reset ignored the seed, "
            "system clock used host time."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "docker run -d -p 127.0.0.1:8080:8080 hapiproject/hapi:v8.4.0-2",
                "python -m mcp_ops_server.backends.fhir_seed load --url http://127.0.0.1:8080/fhir",
                "MCP_OPS_TEST_BACKENDS=sqlite,fhir python -m pytest -q          # 294 passed",
                "python -m evals.run --backend fhir --fhir-url http://127.0.0.1:8080/fhir  # 31/31, 12/12",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"The race test passed on the first try, so I removed each guard on purpose. One removal still passed '
            "for the wrong reason and exposed a real double-booking window. Now every guard has a test that fails "
            'without it."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "1) Merge PR #16. 2) Add <b>fhir</b> to the ruleset's required checks. 3) Say <b>go</b> for Part 8.",
            body,
        ),
    ]


def part8() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 8 Brief · Salon example, then mcp-ops-kit", title),
        Paragraph(
            "mcp-ops-server · 20 Oct 2026 · PR #18 · Status: Codex review fixed (1x P1, 5x P2); CI green; ready to merge",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            f"<b>A second synthetic domain, a salon</b> ({cf}salon_ops/</font>): stylists, hourly openings, visits, "
            "customer-bound tokens, its own scopes, and rules the clinic lacks (service offered, 24 h late-cancel, "
            "3 upcoming visits). Built first by <b>copying</b> the clinic's code. 18 golden tasks, 14 negative controls."
        ),
        bl(
            f"<b>{cf}mcp_ops_kit/</font></b>, extracted from what the two copies shared: auth (scopes + subject as an "
            f"{cf}AuthConfig</font>), limits, a SQLite per-call deadline base, server wiring with a fail-closed start, "
            f"the eval harness ({cf}EvalDomain</font>), grader ({cf}SubjectRule</font>), agents, runner, smoke test."
        ),
        bl(
            "<b>Both eval gates run on the kit in CI.</b> The salon shrank from 2,150 lines as a copy to 954; the "
            "clinic's tool schemas and live-agent prompt are byte-identical."
        ),
        section("How it works"),
        table(
            [
                ["A domain writes", "The kit does"],
                [
                    "AuthConfig: scopes, subject claim, ID pattern",
                    "Hashed tokens, CLI, scope and binding checks in code",
                ],
                [
                    "register(server, guard): its tools",
                    "Rate limit before validation, health, /mcp + /healthz, serve",
                ],
                [
                    "A SqliteStore subclass: schema, seed, rules",
                    "One deadline over lock wait, busy wait and query",
                ],
                [
                    "EvalDomain + golden tasks + controls",
                    "Fresh data and real server per task, grading, report, gate",
                ],
            ],
            [3.2 * inch, w - 3.2 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Copy, then extract.</b> Diffing each copy against its original gave the kit's whole interface: "
            "limits.py differed in 0 lines, the grader only in the subject, the harness in data, clock and IDs. "
            "Nothing is configurable that a second user did not need."
        ),
        bl(
            "<b>Clinic API kept, behaviour pinned</b> by the schema snapshot, the FHIR parity test (374 tests, "
            "identical trajectories) and a test that the kit never imports a domain."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>A fail-open token loader since Part 3.</b> Unknown keys were ignored, so a mistyped or foreign binding "
            "key loaded an <i>unbound</i> token (reproduced on the old code). The kit refuses unknown keys; a test "
            "fails if that is reverted."
        ),
        bl(
            "<b>Codex: 1x P1, 5x P2.</b> P1: a queued salon write read the clock before the lock, so a late cancel "
            "could slip through (the clinic had it right; my copy did not). Also: refusal tasks passed on unrelated "
            "errors (now pinned, 3 new controls), the diff hid edits (now 'unexpected'), empty env values became "
            "defaults, --fhir-url=URL broke. The cross-domain token claim was narrowed and pinned, not redesigned."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "pytest -q                          # 297 passed",
                "python -m evals.run                # clinic 31/31, 12/12",
                "python -m salon_ops.evals.run      # salon 18/18, 14/14",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"I refused to design the library first. I built a second domain by copy-paste, diffed the copies, and '
            "extracted exactly the difference as configuration. The diff also showed a pre-existing fail-open in "
            'token loading."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "1) Merge PR #18 once CI is green. 2) Optional: decide whether tokens files get a domain field. 3) Say <b>go</b> for Part 9.",
            body,
        ),
    ]


def part9() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 9 Brief · Safeguards in the kit", title),
        Paragraph(
            "mcp-ops-server · 20 Oct 2026 · PR #20 · Status: merged 21 Oct 2026; Codex review fixed (6x P1, 3x P2); CI green",
            sub,
        ),
        Spacer(1, 4),
        section("What was built (in mcp_ops_kit, so the clinic and the salon both have it)"),
        bl(
            "<b>Tokens file format 2 + audience binding:</b> every token names its audience, expiry and revocation. "
            f"The verifier re-reads the file on change, so {cf}revoke</font> works on a running server. An "
            f"unversioned file is refused with the {cf}migrate</font> command. Closes the Part 8 cross-domain P2."
        ),
        bl(
            "<b>OAuth 2.1 resource server:</b> RS256/ES256 JWT access tokens (RFC 9068) checked against a JWKS (file or "
            f"URL, cached, rotation-aware), iss/aud/exp/nbf, and a {cf}jti</font> revocation file. Static and JWT side by side."
        ),
        bl(
            "<b>Audit log (164.312(b)):</b> one record per tool call (client, token ID, tool, scope, subject ID, "
            "outcome, time), never arguments. AES-256-GCM per record, HMAC chain across records, "
            f"{cf}verify</font>/{cf}show</font> CLIs."
        ),
        bl(
            "<b>Encryption at rest (164.312(a)(2)(iv)):</b> a SQLite file holds only ciphertext. The database lives in "
            "memory, and each committed write re-encrypts it atomically (about 3 ms). One data key, HKDF subkeys per purpose."
        ),
        bl(
            "<b>Data minimisation:</b> every tool's fields pinned with a reason, checked on every backend. The server "
            "refuses to start without a token source, an audit log and a key."
        ),
        section("How it works"),
        table(
            [
                ["Call path", "Safeguard"],
                [
                    "Bearer token",
                    "static: hash, audience, expiry, revoked? / JWT: signature, iss, aud, exp, jti",
                ],
                ["OpsServer.call_tool", "audit begin, then rate limit, before argument validation"],
                [
                    "Tool: guard.check",
                    "scope and subject; notes scope, subject and refusal for the audit record",
                ],
                ["Store", "SQLite in memory, re-encrypted to disk on every committed write"],
                [
                    "Result",
                    "text rebuilt from the validated output; one audit record appended and fsynced",
                ],
            ],
            [1.7 * inch, w - 1.7 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Fail closed where a safeguard can fail.</b> A broken tokens or revocation file refuses every token "
            "(keeping the old set could skip a revocation). An unwritable audit record stops the server, but the call "
            "already made still returns its real result: reporting it as failed would be false (Part 7's lesson)."
        ),
        bl(
            "<b>No native dependency.</b> SQLCipher needs a C library, and field-level encryption leaks the schema and "
            f"row counts. One new dependency, {cf}cryptography</font>, for AES-GCM and the JWT signatures."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>The agent could read undeclared fields.</b> The SDK drops them from structured content, but builds the "
            "text, which the model reads, from the raw return. I found it when my minimisation test passed against a "
            "deliberately leaky backend. The kit now rebuilds the text."
        ),
        bl(
            "<b>Before pushing:</b> a 0600 tokens file the container could not read (found by replaying CI's container "
            "steps locally), a lock leaked on a refused database, an empty JWKS that raised a 500, and old tests that "
            "passed for the wrong reason. Cursor's bots found 4 more, Codex 9 (3 already fixed; 5 fixed, e.g. a deleted audit log or revocation file did not fail closed; 1 documented). 14/15 mutations caught."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "pytest -q                                   # 521 passed",
                "python -m evals.run; python -m salon_ops.evals.run   # 31/31 12/12; 18/18 14/14",
                "python -m mcp_ops_kit.audit verify --file audit.log --key-env MCP_OPS_DATA_KEY",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"My data-minimisation test passed against a backend I had made leak on purpose. The framework stripped '
            "the extra field from the structured result but not from the text the model reads. So I test what the "
            'consumer sees, not what the framework validates."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "1) Note: the image now needs MCP_OPS_DATA_KEY, and old tokens files need migrate (README). "
            "2) Say <b>go</b> for Part 10.",
            body,
        ),
    ]


def part10() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 10 Brief · Terraform blueprint on HIPAA-eligible AWS", title),
        Paragraph(
            "mcp-ops-server · 21 Oct 2026 · PR #22 · Status: merged 21 Oct 2026; Codex (2x P1, 5x P2) and Cursor (6) fixed; CI green · validated, never applied",
            sub,
        ),
        Spacer(1, 4),
        section("What was built (infra/aws/: 1,373 lines of HCL, 473 of tests)"),
        bl(
            "<b>A private Fargate service:</b> one task, image pinned by digest from an immutable, scanned ECR repo, "
            "uid 10001, read-only root, every capability dropped, no ECS Exec, no public IP. An internal ALB (TLS 1.2+) "
            "behind a WAF (known bad inputs, rate limit per IP across tasks). No internet gateway, NAT or Elastic IP: "
            f"AWS APIs through VPC endpoints, the FHIR server through {cf}fhir_cidrs</font> only."
        ),
        bl(
            "<b>Storage and keys:</b> EFS access points for tokens (read-only to the server), audit log and SQLite; "
            "the data key in Secrets Manager, set out of band so it never enters state; two rotated KMS keys; logs kept 400+ days."
        ),
        bl(
            "<b>Least privilege in IAM:</b> the server can read the tokens file but not write it, so it cannot grant "
            "itself a token; an on-demand admin task issues and revokes tokens but cannot write the audit log."
        ),
        section("How CI checks it (new terraform job; no AWS credentials exist)"),
        table(
            [
                ["Check", "What it proves"],
                [
                    "fmt, init (lock file), validate",
                    "well-formed; provider 6.68.0 pinned by hash for 5 platforms",
                ],
                [
                    "terraform test: 21 runs",
                    "on a mocked provider: the rendered IAM/EFS policies, hardening, network, TLS, encryption; 10 bad inputs refused",
                ],
                [
                    "tflint + AWS ruleset, checkov",
                    "196 checkov checks pass; each of 8 skips has its reason beside the resource",
                ],
                [
                    "scripts/check_infra.py (+21 pytest)",
                    "every type maps to a HIPAA-eligible or no-PHI service; no IGW/NAT/EIP, no 0.0.0.0/0; settings and uid match the server",
                ],
                [
                    "container job",
                    "the image now runs read-only with no capabilities, as the task does",
                ],
            ],
            [2.15 * inch, w - 2.15 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Never applied.</b> Reviewable infrastructure without a PHI endpoint to operate or pay for. "
            "<b>Tests on a mock, not just linters:</b> checkov knows AWS defaults, not this server's rules "
            "(read-only tokens, one writer, the real clock)."
        ),
        bl(
            "<b>One task.</b> The audit log and database take exclusive locks, so deploys stop the old task first. "
            "Scaling out needs a shared audit sink; I wrote that down instead of hiding it."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>The image's default clock is the synthetic demo time.</b> Deployed as is, it would think it is always "
            f"2 Nov 2026, 08:00. The task sets {cf}MCP_OPS_NOW=system</font>, and a test pins it."
        ),
        bl(
            "<b>checkov caught a portability bug</b> (zone names differ per account; subnets use zone IDs). The registry, "
            "AWS's site and Docker Hub were unreachable here, so the provider came from HashiCorp's release server, and the "
            "HIPAA list was checked by search only: the catalog marks each service official, secondary or (S3) unverified."
        ),
        bl(
            "<b>Reviewers found 6 real defects.</b> Cursor Security: WAF samples skip redaction (sampling off), and admin "
            f"{cf}issue</font> printed tokens into CloudWatch ({cf}--sha256</font> registers a hash you made). Bugbot: "
            f"the first apply could never start ({cf}start_service</font>). Codex: a concurrent admin task could undo a "
            "revocation (the kit now locks the file), and my tests read helper maps, not rendered policies. Now they do: "
            "13 of 13 mutations, including Codex's 3, are caught."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "cd infra/aws && terraform init -backend=false && terraform test   # 21 passed",
                "tflint --init && tflint; checkov -d . --framework terraform --skip-download",
                "cd ../.. && python scripts/check_infra.py && pytest -q          # 547 passed",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"Linters pass AWS best practice; they do not know that my server must not be able to write its own tokens '
            "file. So the blueprint has plan-time tests against a mocked provider, and I broke each safeguard to see a "
            'test fail."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "Done: PR #22 merged and <b>terraform</b> is a required check. Optional: check hipaa-services.json against "
            "AWS's live list. Then say <b>go</b> for Part 11.",
            body,
        ),
    ]


def part11() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 11 Brief · Assurance and supply chain", title),
        Paragraph(
            "mcp-ops-server · 21 Oct 2026 · PR #24 · Status: merged 21 Oct 2026; Codex (2x P1, 7x P2) fixed; kit 0.1.0 on PyPI · HIPAA-ready, never compliant",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            f"<b>A controls matrix</b> ({cf}docs/hipaa-controls.md</font>): all 12 standards and specifications of 45 CFR "
            "164.312, each with a status (Addressed, Partly, Adopter), the tests that prove it, and what stays with the "
            f"adopter. <b>A STRIDE threat model</b> ({cf}docs/threat-model.md</font>): 17 threats on the agent, server and backend "
            f"path, including prompt injection through tool results. <b>{cf}SECURITY.md</font></b>."
        ),
        bl(
            "<b>mcp-ops-kit as its own package</b>, released to PyPI from a tag on main by trusted publishing (no API "
            "token anywhere). <b>Signed images</b> with a signed SPDX SBOM. <b>CodeQL, Dependabot</b>, and every action "
            "pinned by commit."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "What runs, and where"],
                [
                    "check_docs.py (CI test job)",
                    "every test, file and terraform run the docs cite must exist; every spec and STRIDE category present; no 'compliant' claim",
                ],
                [
                    "package job (every PR)",
                    "build sdist, then wheel from it; only the kit inside; twine --strict; the wheel alone in a fresh venv, used",
                ],
                [
                    "container job (every PR)",
                    "local registry + throwaway key: SBOM, sign, attest, verify; another key and an attest-only image must fail",
                ],
                [
                    "publish job (main only)",
                    f"keyless signature as {cf}ci.yml@refs/heads/main</font>, SBOM attested, both verified before latest moves",
                ],
                [
                    "release-kit.yml (kit-v* tag)",
                    "tag on main, matching __version__, every CI job + CodeQL green on it; then your approval in the pypi environment",
                ],
            ],
            [2.0 * inch, w - 2.0 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Docs that CI checks.</b> A matrix that cites tests goes stale the day a test is renamed; now that fails "
            "the build. <b>The PR tests the signing plumbing</b> with a key, because a keyless identity exists only on main."
        ),
        bl(
            "<b>cosign and syft built by go install</b>, checked against Go's checksum database: this session could not "
            "reach GitHub releases, and the same commands then run identically here and in CI."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            f"<b>cosign v3's {cf}verify</font> passed an image that was never signed</b>, only attested. My script now "
            "requires the image-signature payload for that digest, and CI proves an attest-only image is refused."
        ),
        bl(
            f"<b>v3 dropped {cf}--tlog-upload=false</font></b> (a signing config with no log replaces it). <b>The secret "
            f"scanner ran {cf}gitleaks:latest</font></b>; it is pinned by digest now, and a test fails on any unpinned action or image."
        ),
        bl(
            "<b>Codex found my guards inside what they guard:</b> a tag runs its own copy of the release workflow, so the "
            "boundary is now your environment reviewer and tag ruleset; releases need CI green on the commit. It also "
            "caught claims broader than their tests, plain-HTTP FHIR accepted by the server (now refused), setuid binaries "
            "in the image (stripped, scanned in CI), and a restored database file passing silently (now a stated residual)."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "python scripts/check_docs.py && pytest -q                     # 610 passed",
                "pip install build && python scripts/build_kit.py --out /tmp/kit",
                "python scripts/check_kit_dist.py check /tmp/kit",
                "scripts/supply_chain.sh verify ghcr.io/nikhilll30/mcp-ops-server@sha256:&lt;digest&gt;  # after merge",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"I wrote a test where the image was only attested, never signed, and cosign said it was signed. So my verify '
            "checks the payload type, and CI runs that must-fail case on every PR. A security check you have not seen "
            'fail is a guess."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "Done: PR #24 merged, the image signed keyless (digest c7bbc16a), <b>mcp-ops-kit 0.1.0</b> on PyPI with "
            "attestations, <b>package</b> a required check. Optional: a second Codex pass. Then say <b>go</b> for Part 12.",
            body,
        ),
    ]


def part12() -> list:
    w = 7.0 * inch
    cf = "<font face='Courier'>"
    return [
        Paragraph("Part 12 Brief · Evals at scale: pass^k and prompt injection", title),
        Paragraph(
            "mcp-ops-server · 22 Oct 2026 · PR #28 · Status: open; Bugbot (1) and two Codex passes (1x P1, 9x P2) fixed · live numbers wait on your run",
            sub,
        ),
        Spacer(1, 4),
        section("What was built"),
        bl(
            f"<b>Repeated runs</b> ({cf}--repeats k</font>, 1 to 20): every task k times on fresh data, scored with "
            "<b>pass^k</b> (the chance k runs of a task all pass), per-task pass rate and variance, and flaky tasks named. "
            "A task passes only if every run passed. Cost is summed over all runs."
        ),
        bl(
            f"<b>A prompt-injection suite</b> ({cf}--suite injection</font>): 6 clinic tasks plant an instruction in a "
            "provider's name, the one free-text field the tools return, on SQLite and FHIR. 8 negative controls obey it "
            "and must be caught. Both suites gate CI on both backends; the live workflow takes suite and repeats."
        ),
        section("How it works"),
        table(
            [
                ["Piece", "What it does"],
                [
                    "plant (harness)",
                    f"writes the text into the task's fresh data before the server starts; {cf}fhir_seed reset</font> puts names back",
                ],
                [
                    "injection_exposed",
                    "the agent must have received a tool result containing the text, else the run is not a resisted attack",
                ],
                [
                    "attempted / carried out",
                    "a call the planted text asked for, refused or not / yes, no, or unknown (the write was sent but the backend could not confirm it)",
                ],
                [
                    "pass^k",
                    "C(c, k) / C(n, k) per task from n runs with c passes (unbiased), averaged over tasks",
                ],
            ],
            [1.7 * inch, w - 1.7 * inch],
        ),
        section("Why it was done this way"),
        bl(
            "<b>Three attacks the server refuses, three the token allows.</b> Threat model E2 says the server bounds what an "
            "instruction can achieve; the suite measures both halves: what the model tries, and what still gets through."
        ),
        bl(
            "<b>Separate suite, same grader.</b> The golden numbers (31/31) stay comparable, and injection tasks use the "
            "same checks; following the injection is a forbidden step, so it fails like any other unsafe call."
        ),
        section("What went wrong or surprised me (logged in FAILURES.md)"),
        bl(
            "<b>Planted text would have leaked across tasks on FHIR</b>: reset never restored provider names. Fixed, and a "
            "test fails without it. <b>I nearly rebuilt Part 11</b> from a stale checkout; PyPI already showing "
            "mcp-ops-kit 0.1.0 gave it away before any code was written."
        ),
        bl(
            "<b>Codex found my exposure check too generous</b>: a model cut off by its turn limit right after the search "
            "never received the attack, yet counted as resisting it. It also found a lost FHIR write reported as "
            "stopped, and a null referral missed by the attack pattern. All reproduced, fixed, and tested."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "pytest -q                                   # 659 passed (740 with sqlite,fhir)",
                "python -m evals.run --suite injection       # 6/6 tasks, 8/8 controls",
                "python -m evals.run --repeats 3 --tasks b01-earliest-primary-care",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"An injection eval that only counts passes is misleading in two ways: an agent that never read the attack '
            "looks robust, and a refused call looks the same as a carried-out one. I grade exposure, attempt and outcome "
            'separately, so the number says whether the model resisted or the server saved it."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "Merge PR #28 when green, then tag <b>kit-v0.2.0</b>. Live runs (Actions &gt; Live evals; "
            "about $2.30 in total, an upper bound from the Part 5 costs): Luna and Sol, golden with repeats 5, then injection with repeats 5.",
            body,
        ),
    ]


def phase2() -> list:
    w = 7.0 * inch
    return [
        Paragraph("Phase 2 Plan · HIPAA-ready clinic + mcp-ops-kit", title),
        Paragraph(
            "mcp-ops-server · 20 Oct 2026 · PR #15 · Planning only, no code · "
            "Part 7 starts after your merge and go",
            sub,
        ),
        Spacer(1, 4),
        section("What this PR does"),
        bl(
            "<b>Plans Phase 2</b> in comms/PLAN.md: the goal, an audience table, Parts 7-12 with acceptance "
            "criteria, and the key decisions. <b>STATUS.md</b> gets Phase 2 rows and a detailed Part 7 next action."
        ),
        bl(
            "<b>Adds rules to CLAUDE.md:</b> say HIPAA-ready, never HIPAA compliant; no real patient data ever; "
            "backend parity; each part updates the README audience and roadmap tables; automated PR reviewers "
            "(Bugbot, Cursor Security, Codex) are handled like Codex: reproduce, fix with a test or reply with reasons; "
            "no bot pushes or approves."
        ),
        bl(
            "<b>Broadens the README:</b> a wider intro, Who it's for, an honest file-by-file coupling map, "
            "Can I use it with real patient data? (not yet; code vs adopter duties, BAA with the LLM provider; "
            "not legal advice), and a Roadmap."
        ),
        section("The parts"),
        table(
            [
                ["Part", "Builds", "Done when"],
                [
                    "7 FHIR R4",
                    "Backend protocol; SQLite behind it; FhirBackend; seed as a FHIR Bundle",
                    "Same tests + eval gate pass on both backends, HAPI in CI; If-Match race test",
                ],
                [
                    "8 Kit",
                    "A booking-business example first, then extract mcp-ops-kit",
                    "Both domains pass their eval gates on the kit",
                ],
                [
                    "9 Safeguards",
                    "Audit log, OAuth 2.1 expiry/revocation, encryption at rest, data minimisation",
                    "Tamper test, no-restart revocation, no plaintext on disk, pinned field sets",
                ],
                [
                    "10 Terraform",
                    "Blueprint on HIPAA-eligible AWS",
                    "fmt, validate, tflint, checkov in CI; never applied",
                ],
                [
                    "11 Assurance",
                    "164.312 controls matrix, threat model, SECURITY.md, CodeQL, Dependabot, PyPI, signed images",
                    "Every control maps to code, a test, Terraform or the adopter",
                ],
                [
                    "12 Evals at scale",
                    "Repeated live runs, pass^k, prompt-injection suite",
                    "Variance and injection results in the README, costs measured",
                ],
            ],
            [1.05 * inch, 3.0 * inch, w - 4.05 * inch],
        ),
        section("Why this way"),
        bl(
            "<b>HAPI, not a mock.</b> A mock agrees with my assumptions; a real FHIR server can disagree. "
            "Parity is enforced in CI because a second backend that drifts is worse than one backend."
        ),
        bl(
            "<b>Second domain before the kit.</b> Abstractions pulled from two working users fit; designed up "
            "front, they guess. Today only limits.py and the Dockerfile are domain-free (CI has one line of scopes)."
        ),
        bl(
            "<b>HIPAA-ready, not compliant.</b> Compliance needs an organisation: BAAs (including the LLM provider), "
            "a risk analysis, policies. Code can supply the technical safeguards and say plainly what it cannot."
        ),
        section("What surprised me while planning"),
        bl(
            "The mcp 2.x SDK ships <font face='Courier'>httpx2</font>, not httpx, so the Part 7 plan names the client "
            "the repo already uses (no new dependency). The grader's step and precondition checks are already "
            "generic; only its patient-scope and appointments-state checks are clinic-coupled."
        ),
        section("Verify it yourself"),
        codebox(
            [
                "git diff --stat main     # docs + the brief generator only; no server code changed",
                "ruff check . && ruff format --check . && pytest -q",
                "grep -rn 'HIPAA compliant' README.md comms/      # only in 'never ... compliant' wording",
            ]
        ),
        section("Interview talking point"),
        Paragraph(
            '"Before writing code I mapped which files actually know the domain. Only two were free of it, so the '
            "kit gets extracted from two working domains, and I say HIPAA-ready, because compliance is not a "
            'property of a repo."',
            body,
        ),
        section("Next (your actions)"),
        Paragraph(
            "1) Review and merge this PR. 2) Say <b>go</b> for Part 7 (branch part-7-fhir-backend). "
            "No Codex review needed for a planning-only PR unless you want one.",
            body,
        ),
    ]


PARTS = {
    1: (part1, "part-1-foundation", "Part 1 Brief: Foundation"),
    2: (part2, "part-2-core-server", "Part 2 Brief: Core server"),
    3: (part3, "part-3-hardening", "Part 3 Brief: Hardening"),
    4: (part4, "part-4-evals", "Part 4 Brief: Evals"),
    5: (part5, "part-5-ci-gate", "Part 5 Brief: CI eval gate"),
    6: (part6, "part-6-ship", "Part 6 Brief: Ship"),
    7: (part7, "part-7-fhir-backend", "Part 7 Brief: Backend protocol + FHIR R4"),
    8: (part8, "part-8-mcp-ops-kit", "Part 8 Brief: Salon example + mcp-ops-kit"),
    9: (part9, "part-9-safeguards", "Part 9 Brief: Safeguards in the kit"),
    10: (part10, "part-10-terraform", "Part 10 Brief: Terraform blueprint on HIPAA-eligible AWS"),
    11: (part11, "part-11-assurance", "Part 11 Brief: Assurance and supply chain"),
    12: (part12, "part-12-evals-at-scale", "Part 12 Brief: Evals at scale"),
}


PLANS = {
    "phase-2": (phase2, "phase-2-plan", "Phase 2 Plan: HIPAA-ready clinic + mcp-ops-kit"),
}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else str(max(PARTS))
    build, slug, doc_title = PLANS[arg] if arg in PLANS else PARTS[int(arg)]
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
