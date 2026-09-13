"""SC-1: the install is the dangerous part, not the run. These tests gate the flag set."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from bench.collect.registry import ServerCandidate
from bench.collect.sandbox_capture import (
    DOCKER_DIR,
    ExcludedServer,
    TrustedServer,
    build_args,
    build_image,
    capture,
    docker_args,
    load_exclusions,
    parse_tools_list,
    record_exclusion,
    trust_check,
)

REQUIRED = (
    "--rm",
    "-i",
    "--network",
    "none",
    "--read-only",
    "--tmpfs",
    "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 (the container's tmpfs, not a host path)
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
)

NPM = ServerCandidate(
    name="io.modelcontextprotocol/filesystem",
    repo_url="https://github.com/modelcontextprotocol/servers",
    package="@modelcontextprotocol/server-filesystem",
    package_registry="npm",
    license="MIT",
)
PYPI = NPM.model_copy(update={"package": "mcp-server-fetch", "package_registry": "pypi"})


@pytest.mark.parametrize("flag", REQUIRED)
def test_docker_args_carry_every_required_flag(flag: str) -> None:
    assert flag in docker_args("sieve-capture:x")


def test_docker_args_mount_nothing_from_the_host() -> None:
    args = docker_args("sieve-capture:x")
    assert "-v" not in args and "--mount" not in args and "--volume" not in args


def test_docker_args_end_with_the_image_tag() -> None:
    assert docker_args("sieve-capture:x")[-1] == "sieve-capture:x"


def test_trust_requires_both_provenance_and_a_read_install_script() -> None:
    assert TrustedServer(org_is_official=True, stars=3, install_scripts_reviewed=True).is_trusted()
    assert TrustedServer(
        org_is_official=False, stars=900, install_scripts_reviewed=True
    ).is_trusted()
    # Provenance alone is not trust: the install scripts must have been read.
    assert not TrustedServer(
        org_is_official=True, stars=900, install_scripts_reviewed=False
    ).is_trusted()
    assert not TrustedServer(
        org_is_official=False, stars=12, install_scripts_reviewed=True
    ).is_trusted()


def _github_client(payload: dict[str, object], status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_trust_check_reads_stars_and_org_from_the_github_api() -> None:
    client = _github_client({"stargazers_count": 1234, "owner": {"login": "modelcontextprotocol"}})
    trusted = trust_check(NPM, reviewed=True, client=client)
    assert trusted.stars == 1234
    assert trusted.org_is_official is True
    assert trusted.install_scripts_reviewed is True
    assert trusted.is_trusted()


def test_trust_check_without_a_repo_is_not_trusted() -> None:
    candidate = NPM.model_copy(update={"repo_url": None})
    trusted = trust_check(candidate, reviewed=True)
    assert trusted.stars == 0 and trusted.org_is_official is False
    assert not trusted.is_trusted()


def test_trust_check_survives_a_github_error() -> None:
    trusted = trust_check(NPM, reviewed=True, client=_github_client({}, status=404))
    assert not trusted.is_trusted()


def test_build_args_disable_lifecycle_scripts_and_carry_no_credentials() -> None:
    args = build_args(NPM, "sieve-capture:fs")
    joined = " ".join(args)
    assert args[:2] == ["docker", "build"]
    assert "--tag" in args and "sieve-capture:fs" in args
    assert "Dockerfile.node" in joined
    for forbidden in ("--secret", "--ssh", "NPM_TOKEN", "GITHUB_TOKEN", "--allow"):
        assert forbidden not in joined


def test_build_args_pick_the_python_dockerfile_for_pypi() -> None:
    assert "Dockerfile.python" in " ".join(build_args(PYPI, "sieve-capture:fetch"))


def test_build_args_reject_a_candidate_with_no_package() -> None:
    with pytest.raises(ValueError, match="no installable package"):
        build_args(NPM.model_copy(update={"package": None}), "t")


def test_both_dockerfiles_exist_and_disable_scripts() -> None:
    node = (DOCKER_DIR / "Dockerfile.node").read_text(encoding="utf-8")
    python = (DOCKER_DIR / "Dockerfile.python").read_text(encoding="utf-8")
    assert "--ignore-scripts" in node
    assert "--no-build-isolation" in python or "--only-binary" in python
    for text in (node, python):
        assert "TOKEN" not in text and "ssh" not in text.lower()


def test_parse_tools_list_reads_a_recorded_jsonrpc_result() -> None:
    payload = json.loads(
        Path("tests/bench/data/captured/tools_list_response.json").read_text(encoding="utf-8")
    )
    tools = parse_tools_list(payload)
    assert [t.name for t in tools] == ["read_file", "write_file"]
    assert tools[0].inputSchema["properties"]["path"]["type"] == "string"
    assert tools[0].description is not None


def test_parse_tools_list_skips_a_tool_with_no_schema() -> None:
    payload = {"result": {"tools": [{"name": "broken", "description": "no schema"}]}}
    assert parse_tools_list(payload) == []


def test_record_and_load_exclusions(tmp_path: Path) -> None:
    path = tmp_path / "excluded.jsonl"
    record_exclusion("a/b", "install_requires_scripts", detail="postinstall", path=path)
    record_exclusion("c/d", "capture_failed", path=path)
    loaded = load_exclusions(path)
    assert loaded == [
        ExcludedServer(name="a/b", reason="install_requires_scripts", detail="postinstall"),
        ExcludedServer(name="c/d", reason="capture_failed", detail=None),
    ]
    assert load_exclusions(tmp_path / "missing.jsonl") == []


def test_build_image_refuses_to_run_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="docker"):
        build_image(NPM, "sieve-capture:fs")


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(  # noqa: S603 (fixed argv, used only to detect a live daemon)
                ["docker", "info"],  # noqa: S607
                capture_output=True,
                timeout=5,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.docker
@pytest.mark.skipif(not _docker_available(), reason="no Docker daemon on this host")
def test_real_capture_against_the_official_filesystem_server() -> None:
    tag = "sieve-capture:filesystem"
    build_image(NPM, tag)
    tools = capture(tag)
    assert tools and all(t.inputSchema.get("type") == "object" for t in tools)
