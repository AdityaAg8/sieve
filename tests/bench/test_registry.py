"""Discovery against the official MCP registry, replayed from a recorded page."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from bench.collect.registry import (
    ServerCandidate,
    discover,
    fetch_spdx_license,
    filter_permissive,
)

PAGE = Path("tests/bench/data/registry-page.json")


def _client(page: dict[str, object]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_discover_parses_recorded_page() -> None:
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    cands = discover(limit=len(page["servers"]), client=_client(page))
    assert len(cands) == len(page["servers"])
    assert all(c.name for c in cands)


def test_discover_reads_package_and_repo_from_the_entry() -> None:
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    cands = discover(limit=len(page["servers"]), client=_client(page))
    by_name = {c.name: c for c in cands}
    packaged = [c for c in cands if c.package is not None]
    assert packaged, "the recorded page contains packaged servers"
    assert all(c.package_registry in {"npm", "pypi", "docker"} for c in packaged)
    with_repo = [c for c in cands if c.repo_url is not None]
    assert with_repo and all(c.repo_url.startswith("http") for c in with_repo)  # type: ignore[union-attr]
    # The registry carries no SPDX licence: it is filled later from the repository.
    assert all(c.license is None for c in by_name.values())


def test_discover_stops_at_the_limit() -> None:
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    cands = discover(limit=5, client=_client(page))
    assert len(cands) == 5


def test_filter_permissive_drops_unknown() -> None:
    keep = ServerCandidate(
        name="a", repo_url=None, package=None, package_registry=None, license="MIT"
    )
    drop = ServerCandidate(
        name="b", repo_url=None, package=None, package_registry=None, license=None
    )
    assert filter_permissive([keep, drop]) == [keep]


def test_filter_permissive_excludes_mpl_and_gpl() -> None:
    def cand(spdx: str) -> ServerCandidate:
        return ServerCandidate(
            name=spdx, repo_url=None, package=None, package_registry=None, license=spdx
        )

    kept = filter_permissive(
        [cand(s) for s in ("MIT", "Apache-2.0", "BSD-3-Clause", "MPL-2.0", "GPL-3.0")]
    )
    assert [c.license for c in kept] == ["MIT", "Apache-2.0", "BSD-3-Clause"]


def test_fetch_spdx_license_reads_the_repo_license_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/modelcontextprotocol/servers/license"
        return httpx.Response(200, json={"license": {"spdx_id": "MIT"}})

    cand = ServerCandidate(
        name="s",
        repo_url="https://github.com/modelcontextprotocol/servers",
        package=None,
        package_registry=None,
        license=None,
    )
    filled = fetch_spdx_license(cand, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert filled.license == "MIT"


def test_fetch_spdx_license_leaves_none_when_github_has_no_licence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    cand = ServerCandidate(
        name="s",
        repo_url="https://github.com/x/y",
        package=None,
        package_registry=None,
        license=None,
    )
    filled = fetch_spdx_license(cand, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert filled.license is None


def test_fetch_spdx_license_without_a_repo_is_a_no_op() -> None:
    cand = ServerCandidate(
        name="s", repo_url=None, package=None, package_registry=None, license=None
    )
    assert fetch_spdx_license(cand).license is None


def test_noassertion_is_not_a_licence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"license": {"spdx_id": "NOASSERTION"}})

    cand = ServerCandidate(
        name="s",
        repo_url="https://github.com/x/y",
        package=None,
        package_registry=None,
        license=None,
    )
    filled = fetch_spdx_license(cand, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert filled.license is None
