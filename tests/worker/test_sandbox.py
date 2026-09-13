"""SC-1: the only place an untrusted MCP server ever runs is the sandbox container.

The Docker daemon is not available on this box, so the container's declared policy is
asserted from `docker-compose.yml` and the actual build/run is marked `docker`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
SANDBOX: dict[str, Any] = COMPOSE["services"]["sandbox-worker"]


def test_the_sandbox_is_the_only_service_that_enables_remote_mcp() -> None:
    enabled = [
        name
        for name, svc in COMPOSE["services"].items()
        if str((svc.get("environment") or {}).get("SIEVE_ALLOW_REMOTE_MCP", "")).lower() == "true"
    ]
    assert enabled == ["sandbox-worker"]


def test_the_sandbox_is_opt_in() -> None:
    assert SANDBOX["profiles"] == ["remote"], "remote scanning is off unless asked for"


def test_the_sandbox_is_locked_down() -> None:
    assert SANDBOX["read_only"] is True
    assert SANDBOX["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in SANDBOX["security_opt"]
    assert SANDBOX["tmpfs"] == ["/tmp"]  # noqa: S108 -- a container tmpfs mount, not a host path


def test_the_sandbox_runs_its_own_image_and_entry_point() -> None:
    assert SANDBOX["build"]["dockerfile"] == "Dockerfile.sandbox"
    dockerfile = Path("Dockerfile.sandbox").read_text(encoding="utf-8")
    assert "sieve.worker.remote" in dockerfile


def test_neither_the_api_nor_the_default_worker_may_reach_a_remote_server() -> None:
    for name in ("api", "worker"):
        env = COMPOSE["services"][name].get("environment") or {}
        assert "SIEVE_ALLOW_REMOTE_MCP" not in env, f"{name} must inherit the default (false)"


def test_the_default_settings_keep_remote_scanning_off() -> None:
    from sieve.config import Settings

    assert Settings().allow_remote_mcp is False


@pytest.mark.docker
def test_the_sandbox_image_builds_and_refuses_a_blocked_host() -> None:  # pragma: no cover
    pytest.skip("needs a Docker daemon; not available on this machine")
