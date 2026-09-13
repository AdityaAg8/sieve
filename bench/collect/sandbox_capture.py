"""Install and run an MCP server inside one disposable container, then read `tools/list`.

PRD SC-1 in one sentence: **the install is the dangerous part, not the run.** `npm install`
runs lifecycle scripts and a PyPI sdist runs a build backend, so installing on the host is
exactly the thing the sandbox exists to prevent. Install and capture therefore happen in the
same throwaway image: a build stage that has network but no repository credentials and
installs with scripts disabled, and a capture stage with the hardened flag set below.

A server that cannot be installed with scripts disabled is *excluded*, not special-cased;
the exclusion is recorded and counted in `corpus_stats.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from bench.collect.registry import ServerCandidate
from sieve.types import ToolDefinition

log = logging.getLogger(__name__)

DOCKER_DIR = Path(__file__).parent / "docker"
GITHUB_API = "https://api.github.com"
OFFICIAL_ORG = "modelcontextprotocol"
STAR_THRESHOLD = 500
CAPTURE_TIMEOUT_S = 30

EXCLUSIONS_PATH = Path("bench/data/excluded_servers.jsonl")
ExclusionReason = Literal["install_requires_scripts", "capture_failed"]

_DOCKERFILES: dict[str, str] = {"npm": "Dockerfile.node", "pypi": "Dockerfile.python"}


class CaptureFailed(Exception):  # noqa: N818 (name fixed by the lane's published interface)
    """The container did not answer `tools/list` inside the deadline."""


class TrustedServer(BaseModel):
    org_is_official: bool
    stars: int
    install_scripts_reviewed: bool

    def is_trusted(self) -> bool:
        """Provenance *and* a human having read the install scripts. Never one alone."""
        provenance = self.org_is_official or self.stars >= STAR_THRESHOLD
        return provenance and self.install_scripts_reviewed


class ExcludedServer(BaseModel):
    name: str
    reason: ExclusionReason
    detail: str | None = None


def docker_args(tag: str) -> list[str]:
    """The capture stage. Every flag here is from PRD SC-1; none is optional."""
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        # S108: this is the container's own tmpfs, mounted noexec/nosuid inside a
        # network-less read-only container - not a host temp path.
        "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        "65534:65534",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--stop-timeout",
        "5",
        tag,
    ]


def build_args(cand: ServerCandidate, tag: str, *, version: str | None = None) -> list[str]:
    """The build stage: network, but no token, no SSH agent and no mounted credential."""
    if not cand.package or not cand.package_registry:
        raise ValueError(f"{cand.name} has no installable package")
    dockerfile = _DOCKERFILES.get(cand.package_registry)
    if dockerfile is None:
        raise ValueError(f"{cand.name}: unsupported package registry {cand.package_registry}")
    args = [
        "docker",
        "build",
        "--file",
        str(DOCKER_DIR / dockerfile),
        "--build-arg",
        f"PACKAGE={cand.package}",
    ]
    if version:
        args += ["--build-arg", f"VERSION={version}"]
    return [*args, "--tag", tag, str(DOCKER_DIR)]


def _require_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("docker is not on PATH; capture cannot run outside the sandbox (SC-1)")


def build_image(cand: ServerCandidate, tag: str, *, version: str | None = None) -> None:
    """Build the disposable image. Raises `CaptureFailed` when the install needs scripts."""
    _require_docker()
    args = build_args(cand, tag, version=version)
    # noqa justified: this is the one place the lane shells out, with a fixed argv built
    # above from validated fields — never a shell string.
    completed = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
    if completed.returncode != 0:
        record_exclusion(cand.name, "install_requires_scripts", detail=completed.stderr[-500:])
        raise CaptureFailed(f"{cand.name}: install failed with lifecycle scripts disabled")


def parse_tools_list(payload: dict[str, Any]) -> list[ToolDefinition]:
    """Parse a JSON-RPC `tools/list` result. A tool with no object schema is dropped."""
    tools = (payload.get("result") or payload).get("tools") or []
    out: list[ToolDefinition] = []
    for entry in tools:
        schema = entry.get("inputSchema")
        if not isinstance(schema, dict):
            log.info("dropping tool %r: inputSchema is not an object", entry.get("name"))
            continue
        out.append(
            ToolDefinition(
                name=entry["name"],
                title=entry.get("title"),
                description=entry.get("description"),
                inputSchema=schema,
                outputSchema=entry.get("outputSchema"),
                annotations=entry.get("annotations"),
            )
        )
    return out


async def _capture_async(tag: str) -> list[ToolDefinition]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = docker_args(tag)
    params = StdioServerParameters(command=args[0], args=args[1:])
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
    return parse_tools_list({"result": result.model_dump(by_alias=True)})


def capture(tag: str, *, timeout_s: int = CAPTURE_TIMEOUT_S) -> list[ToolDefinition]:
    """Speak MCP over stdio to the container: `initialize`, then `tools/list`."""
    _require_docker()

    async def run() -> list[ToolDefinition]:
        return await asyncio.wait_for(_capture_async(tag), timeout_s)

    try:
        return asyncio.run(run())
    except TimeoutError as exc:
        raise CaptureFailed(f"{tag}: no tools/list answer within {timeout_s}s") from exc


def trust_check(
    cand: ServerCandidate, *, reviewed: bool, client: httpx.Client | None = None
) -> TrustedServer:
    """Record provenance per candidate: official org, star count, scripts reviewed."""
    owner_repo = _owner_repo(cand.repo_url)
    if owner_repo is None:
        return TrustedServer(org_is_official=False, stars=0, install_scripts_reviewed=reviewed)
    owned = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        response = http.get(f"{GITHUB_API}/repos/{owner_repo[0]}/{owner_repo[1]}")
        if response.status_code != 200:
            log.info("github said %d for %s", response.status_code, cand.repo_url)
            return TrustedServer(org_is_official=False, stars=0, install_scripts_reviewed=reviewed)
        body = response.json()
    finally:
        if owned:
            http.close()
    login = (body.get("owner") or {}).get("login", "")
    return TrustedServer(
        org_is_official=login.lower() == OFFICIAL_ORG,
        stars=int(body.get("stargazers_count") or 0),
        install_scripts_reviewed=reviewed,
    )


def _owner_repo(repo_url: str | None) -> tuple[str, str] | None:
    if not repo_url or "github.com/" not in repo_url:
        return None
    parts = repo_url.split("github.com/", 1)[1].strip("/").removesuffix(".git").split("/")
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def record_exclusion(
    name: str,
    reason: ExclusionReason,
    *,
    detail: str | None = None,
    path: Path = EXCLUSIONS_PATH,
) -> None:
    """Append one excluded server. `stats.main()` counts these; they are not silent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = ExcludedServer(name=name, reason=reason, detail=detail)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(entry.model_dump_json() + "\n")


def load_exclusions(path: Path = EXCLUSIONS_PATH) -> list[ExcludedServer]:
    if not path.exists():
        return []
    return [
        ExcludedServer.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.collect.sandbox_capture",
        description="Install and run one MCP server in a disposable container and read tools/list.",
    )
    parser.add_argument("--package", required=True, help="npm or PyPI package to install")
    parser.add_argument("--registry", choices=sorted(_DOCKERFILES), default="npm")
    parser.add_argument("--name", default="", help="server name recorded on exclusions")
    parser.add_argument("--repo-url", default=None, help="GitHub repo URL, for the trust check")
    parser.add_argument("--tag", default="sieve-capture:local")
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="assert that a human has read this package's install scripts (SC-1)",
    )
    args = parser.parse_args(argv)
    cand = ServerCandidate(
        name=args.name or args.package,
        repo_url=args.repo_url,
        package=args.package,
        package_registry=args.registry,
        license=None,
    )
    trusted = trust_check(cand, reviewed=args.reviewed)
    if not trusted.is_trusted():
        print(f"refusing: {cand.name} is not trusted under SC-1 ({trusted.model_dump()})")
        return 2
    build_image(cand, args.tag)
    tools = capture(args.tag)
    print(json.dumps([t.model_dump() for t in tools], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
