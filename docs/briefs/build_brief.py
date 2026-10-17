"""Builds the one-page PDF brief for a part. Usage: python docs/briefs/build_brief.py"""

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
            "<b>Secret scan failed only on PRs.</b> The gitleaks action's PR mode broke while the push run "
            "on the same commit passed. Scoped it to push, which still scans full history."
        ),
        section("Verify it yourself"),
        codebox(
            [
                'pip install -e ".[dev]"',
                "ruff check . && pytest -q          # 2 passed",
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


if __name__ == "__main__":
    out = "docs/briefs/part-1-foundation.pdf"
    doc = SimpleDocTemplate(
        out,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.55 * inch,
        title="Part 1 Brief: Foundation",
        author="Nikhil",
    )
    doc.build(part1())
    print(out)
