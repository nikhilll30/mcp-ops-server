"""Render docs/demo/demo.gif from a real run: every command below is executed and its actual
output is what the GIF shows. Nothing is typed in by hand.

    pip install -e ".[dev,docs]"
    python docs/demo/record_demo.py              # uses the published image
    IMAGE=mcp-ops-server:local python docs/demo/record_demo.py   # or a local build

The GIF displays the published image name. Set IMAGE to render from a local build of the same
Dockerfile. The token is shown truncated. The container is removed afterwards.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SHOWN_IMAGE = "ghcr.io/nikhilll30/mcp-ops-server"
COLS, ROWS = 100, 30
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
SIZE, PAD = 15, 14
BG, FG, PROMPT, DIM, OK, BAD = "#0d1117", "#e6edf3", "#7ee787", "#8b949e", "#3fb950", "#f85149"
OUT = Path(__file__).with_name("demo.gif")


def run(cmd: str, env: dict[str, str]) -> str:
    image = env["IMAGE"]
    real = cmd.replace(SHOWN_IMAGE, image)
    proc = subprocess.run(  # noqa: S602 - fixed demo commands, run on purpose through a shell
        real, shell=True, env=env, capture_output=True, text=True, timeout=300, check=False
    )
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        sys.exit(f"demo command failed ({proc.returncode}): {real}\n{out}")
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    data = Path(tempfile.mkdtemp(prefix="mcp-demo-"))
    data.chmod(0o755)  # like a plain mkdir: the container's non-root user must read the file
    env = {**os.environ, "IMAGE": os.environ.get("IMAGE", SHOWN_IMAGE), "DATA": str(data)}
    scopes = "--scope read:schedule --scope write:schedule --scope read:referrals"
    lines: list[tuple[str, str]] = []  # (kind, text): kind is "cmd" | "out" | "note"

    def step(cmd: str, shown: str | None = None, note: str | None = None) -> str:
        if note:
            if lines:
                lines.append(("out", ""))
            lines.append(("note", f"# {note}"))
        out = run(cmd, env)
        lines.append(("cmd", shown or cmd))
        lines.extend(("out", ln) for ln in out.rstrip().splitlines())
        return out

    try:
        token = step(
            f'docker run --rm --user "$(id -u):$(id -g)" -v "$DATA:/data" {SHOWN_IMAGE} '
            f"python -m mcp_ops_server.auth issue --file /data/tokens.json --client demo {scopes}",
            shown='TOKEN=$(docker run --rm --user "$(id -u):$(id -g)" -v ./mcp-data:/data \\\n'
            f"    {SHOWN_IMAGE} \\\n"
            "    python -m mcp_ops_server.auth issue --file /data/tokens.json --client demo ...)",
            note="1. issue a token (only its SHA-256 hash is stored)",
        ).strip()
        lines.pop()  # never show the token itself
        env["MCP_TOKEN"] = token
        step(
            f'docker run -d --name mcp-demo -p 127.0.0.1:8000:8000 -v "$DATA:/data:ro" '
            f"{SHOWN_IMAGE} >/dev/null && for i in $(seq 30); do "
            "[ \"$(docker inspect -f '{{.State.Health.Status}}' mcp-demo)\" = healthy ] && break; "
            "sleep 1; done; docker ps --filter name=mcp-demo --filter health=healthy "
            "--format '{{.Names}}  {{.Status}}' | grep healthy",
            shown=f"docker run -d -p 127.0.0.1:8000:8000 -v ./mcp-data:/data:ro {SHOWN_IMAGE}\n"
            "docker ps",
            note="2. serve it (non-root, health-checked)",
        )
        step(
            "python scripts/smoke_test.py",
            note="3. end to end over HTTP: auth, then a real book + cancel",
        )
        step(
            "python -m evals.run --report /tmp/demo-report.md | sed 's/; report:.*//'",
            shown="python -m evals.run",
            note="4. the CI eval gate: 31 golden tasks, 12 negative controls",
        )
    finally:
        subprocess.run(["docker", "rm", "-f", "mcp-demo"], capture_output=True, check=False)  # noqa: S607
    os.chdir(root)
    render(lines)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")  # noqa: T201


def render(lines: list[tuple[str, str]]) -> None:
    font = ImageFont.truetype(FONT, SIZE)
    cw = round(font.getlength("M"))
    lh = SIZE + 5
    w, h = PAD * 2 + cw * COLS, PAD * 2 + lh * ROWS
    shown: list[tuple[str, str]] = []
    frames: list[Image.Image] = []
    durations: list[int] = []

    def color(kind: str, text: str) -> str:
        if kind == "note":
            return DIM
        if kind == "out" and re.match(r"^ok ", text):
            return OK
        if kind == "out" and ("FAIL" in text or "Error" in text):
            return BAD
        return FG

    def frame(extra: tuple[str, str] | None, ms: int) -> None:
        img = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(img)
        visible = (shown + ([extra] if extra else []))[-ROWS:]
        for i, (kind, text) in enumerate(visible):
            y = PAD + i * lh
            x = PAD
            if kind == "cmd":
                d.text((x, y), "$ ", font=font, fill=PROMPT)
            if kind in ("cmd", "cont"):
                x += cw * 2
            d.text((x, y), text[: COLS - 2], font=font, fill=color(kind, text))
        frames.append(img)
        durations.append(ms)

    for kind, text in lines:
        if kind == "cmd":
            parts = text.split("\n")
            for i, part in enumerate(parts):
                # A line after a trailing backslash continues the command: no prompt.
                kind_i = "cont" if i and parts[i - 1].endswith("\\") else "cmd"
                for n in range(0, len(part) + 1, 6):  # type the command, 6 chars per frame
                    frame((kind_i, part[:n]), 30)
                shown.append((kind_i, part))
            frame(None, 500)
        elif kind == "note":
            shown.append((kind, text))
            frame(None, 700)
        else:
            shown.append((kind, text))
            frame(None, 110)
    frame(None, 4000)
    frames[0].save(
        OUT, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True
    )


if __name__ == "__main__":
    main()
