"""Posts, or updates, one PR comment summarising a Sieve SARIF scan (PRD §15.2).

Standalone on purpose: this runs as a plain `python action/comment.py sieve.sarif` step in the
composite action, without a project dependency install, so it uses only the standard library.
Only `level == "error"` results are worth interrupting a reviewer for -- `needs_review` and
`dismissed` map to `warning`/`note` in `sieve/sarif.py` and are left for the SARIF upload (Code
Scanning) to surface, not the comment.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MARKER = "<!-- sieve-action -->"
OWASP_PROJECT = "https://owasp.org/www-project-mcp-top-10/"
GITHUB_API = "https://api.github.com"


def _owasp_link(owasp_ids: list[str]) -> str:
    if not owasp_ids:
        return OWASP_PROJECT
    return f"{OWASP_PROJECT}#{owasp_ids[0]}"


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def error_results(sarif: dict[str, Any]) -> list[dict[str, Any]]:
    """Every `level == "error"` result across every run, in document order."""
    out: list[dict[str, Any]] = []
    for run in sarif.get("runs", []):
        for result in run.get("results", []):
            if result.get("level") != "error":
                continue
            props = result.get("properties") or {}
            message = result.get("message") or {}
            out.append(
                {
                    "tool_name": props.get("tool_name") or "(unknown tool)",
                    "sub_label": result.get("ruleId") or "triage",
                    "reasoning": message.get("text") or "",
                    "owasp": list(props.get("owasp") or []),
                }
            )
    return out


def render_comment(sarif: dict[str, Any]) -> str | None:
    """The full comment body, or `None` when there is nothing at `level == "error"`."""
    errors = error_results(sarif)
    if not errors:
        return None
    lines = [
        MARKER,
        "### Sieve found tool-poisoning risk",
        "",
        "| Tool | Sub-label | Why | OWASP MCP Top 10 |",
        "| --- | --- | --- | --- |",
    ]
    for error in errors:
        owasp = error["owasp"]
        link_text = owasp[0] if owasp else "MCP Top 10"
        owasp_cell = f"[{link_text}]({_owasp_link(owasp)})"
        lines.append(
            f"| `{error['tool_name']}` | {error['sub_label']} | "
            f"{_escape_cell(error['reasoning'])} | {owasp_cell} |"
        )
    return "\n".join(lines) + "\n"


def find_existing_comment(comments: list[dict[str, Any]]) -> int | None:
    """The id of this action's own previous comment on the PR, if any (found via `MARKER`)."""
    for comment in comments:
        if MARKER in (comment.get("body") or ""):
            return int(comment["id"])
    return None


def _api_request(
    url: str, token: str, *, method: str = "GET", data: dict[str, Any] | None = None
) -> Any:
    body = None
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    with urllib.request.urlopen(request) as response:  # noqa: S310 - fixed GitHub API host
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


def post_or_update_comment(sarif_path: str) -> None:
    sarif = json.loads(Path(sarif_path).read_text(encoding="utf-8"))
    body = render_comment(sarif)
    if body is None:
        print("sieve: no level=error findings; not commenting")
        return

    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr_number = event["pull_request"]["number"]

    base = f"{GITHUB_API}/repos/{repo}"
    comments = _api_request(f"{base}/issues/{pr_number}/comments", token) or []
    existing = find_existing_comment(comments)
    if existing is not None:
        _api_request(
            f"{base}/issues/comments/{existing}", token, method="PATCH", data={"body": body}
        )
        print(f"sieve: updated PR comment {existing}")
    else:
        _api_request(
            f"{base}/issues/{pr_number}/comments", token, method="POST", data={"body": body}
        )
        print("sieve: posted a new PR comment")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: comment.py <sarif-path>", file=sys.stderr)
        return 2
    try:
        post_or_update_comment(argv[1])
    except urllib.error.HTTPError as exc:
        print(
            f"sieve: GitHub API error {exc.code}: {exc.read().decode('utf-8', 'replace')}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
