"""Refuses a release unless every CI job passed on the exact commit being released.

    GITHUB_TOKEN=... python scripts/check_release_ci.py REPO SHA    # exit 1 with the reasons

The required jobs are every job in ``.github/workflows/ci.yml`` except ``publish`` (which
runs only on main and is not a check of the code), so a job added to CI is required here
too, and CodeQL's analysis for each language in ``codeql.yml``. For each one, the newest
GitHub Actions check run of that name on ``SHA`` must have finished with ``success``: a job
that is missing, still running, failed, cancelled or skipped blocks the release. Re-push
the tag once CI is green.

This runs inside the tag's own workflow, so on its own it is a tripwire, not a boundary:
the ``pypi`` environment's required reviewer and the tag ruleset are what a modified
workflow cannot skip (see comms/STATUS.md, Part 11).
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
CODEQL = ROOT / ".github" / "workflows" / "codeql.yml"
NOT_CHECKS = {"publish"}
API = "https://api.github.com"


def required_jobs(ci: Path = CI, codeql: Path = CODEQL) -> list[str]:
    """The job IDs under ``jobs:`` in ci.yml (none of its jobs sets a display ``name``),
    then ``analyze (<language>)`` for each language in codeql.yml's matrix."""
    body = ci.read_text().split("\njobs:\n", 1)[1]
    jobs = [j for j in re.findall(r"^  ([\w-]+):\n", body, re.M) if j not in NOT_CHECKS]
    langs = re.search(r"^\s+language: \[([^\]]+)\]", codeql.read_text(), re.M)
    if langs is None:
        raise SystemExit("codeql.yml has no language matrix")
    return jobs + [f"analyze ({lang.strip()})" for lang in langs.group(1).split(",")]


def problems(runs: list[dict], required: list[str]) -> list[str]:
    """Why ``runs`` (GitHub check runs for one commit) do not show every required job green."""
    out = []
    for job in required:
        mine = [
            r
            for r in runs
            if r.get("name") == job and r.get("app", {}).get("slug") == "github-actions"
        ]
        if not mine:
            out.append(f"{job}: no CI run on this commit")
            continue
        newest = max(mine, key=lambda r: (r.get("started_at") or "", r.get("id", 0)))
        if newest.get("status") != "completed":
            out.append(f"{job}: still {newest.get('status')}")
        elif newest.get("conclusion") != "success":
            out.append(f"{job}: {newest.get('conclusion')}")
    return out


def fetch_runs(repo: str, sha: str, token: str) -> list[dict]:
    runs: list[dict] = []
    page = 1
    while True:
        # filter=all: the default (latest) can leave out a run still in progress, which would
        # let an older success decide (Cursor Bugbot, PR #24).
        url = f"{API}/repos/{repo}/commits/{sha}/check-runs?filter=all&per_page=100&page={page}"
        req = urllib.request.Request(  # noqa: S310 - fixed https API host
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
            data = json.load(resp)
        runs += data["check_runs"]
        if len(runs) >= data["total_count"] or not data["check_runs"]:
            return runs
        page += 1


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not re.fullmatch(r"[0-9a-f]{40}", args[1]):
        sys.exit("usage: check_release_ci.py OWNER/REPO <40-hex commit SHA>")
    repo, sha = args
    found = problems(fetch_runs(repo, sha, os.environ["GITHUB_TOKEN"]), required_jobs())
    for p in found:
        print(f"::error::{p}")
    if found:
        sys.exit(f"CI is not green on {sha[:7]}; not releasing")
    print(f"ok: every CI job passed on {sha[:7]}: {', '.join(required_jobs())}")


if __name__ == "__main__":
    main()
