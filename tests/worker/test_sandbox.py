"""SC-1: the only place an untrusted MCP server ever runs is the sandbox container.

The Docker daemon is not available on this box, so the container's declared policy is
asserted from `docker-compose.yml` and the actual build/run is marked `docker`.
"""

from __future__ import annotations

import importlib
import re
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


def dockerfile_module() -> str:
    """The module `Dockerfile.sandbox` actually runs, parsed out of its CMD."""
    dockerfile = Path("Dockerfile.sandbox").read_text(encoding="utf-8")
    match = re.search(r"CMD\s*\[(.*?)\]", dockerfile, re.DOTALL)
    assert match, "Dockerfile.sandbox has no CMD"
    argv = [part.strip().strip('"') for part in match.group(1).split(",")]
    assert "-m" in argv, f"expected a `python -m <module>` CMD, got {argv}"
    return argv[argv.index("-m") + 1]


def test_the_sandbox_runs_its_own_image() -> None:
    assert SANDBOX["build"]["dockerfile"] == "Dockerfile.sandbox"


def test_the_module_the_dockerfile_names_has_a_callable_entry_point() -> None:
    """A CMD pointing at a module with no `main()` starts a container that does nothing."""
    module = importlib.import_module(dockerfile_module())
    assert callable(getattr(module, "main", None)), f"{module.__name__} has no callable main()"


def test_neither_the_api_nor_the_default_worker_may_reach_a_remote_server() -> None:
    """`env_file: [.env]` is read for every service, so the safe value must be pinned.

    Compose gives `environment:` precedence over `env_file:`, so an explicit "false" here is
    what stops a developer's SIEVE_ALLOW_REMOTE_MCP=true from enabling the un-sandboxed
    worker (SC-1).
    """
    for name in ("api", "worker"):
        env = COMPOSE["services"][name].get("environment") or {}
        assert str(env.get("SIEVE_ALLOW_REMOTE_MCP")).lower() == "false", (
            f"{name} must pin SIEVE_ALLOW_REMOTE_MCP=false, not inherit it from .env"
        )
        # Pinned to "0", not omitted: an omitted variable is whatever `.env` says, and
        # `env_file:` sets real environment variables (see `test_a_dotenv_cannot_open_...`).
        assert str(env.get("SIEVE_SANDBOXED")) == "0", f"{name} must pin SIEVE_SANDBOXED=0"


GATED_SERVICES = ("api", "worker", "sandbox-worker")


def effective_env(service: str, dotenv: dict[str, str] | None = None) -> dict[str, str]:
    """The variables the container actually gets, in Compose's own precedence order.

    Compose's `env_file:` sets *real* environment variables -- it is not pydantic's dotenv,
    which only fills unset fields -- so anything in `.env` reaches `os.environ` unless the
    service's `environment:` block overrides it. That is the merge this reproduces.
    """
    merged: dict[str, str] = dict(dotenv or {})
    merged.update(
        {k: str(v) for k, v in (COMPOSE["services"][service].get("environment") or {}).items()}
    )
    return merged


@pytest.mark.parametrize("service", GATED_SERVICES)
def test_every_gated_service_pins_the_second_gate_explicitly(service: str) -> None:
    """Absent is not safe here: absent means "whatever .env says"."""
    declared = COMPOSE["services"][service].get("environment") or {}
    assert "SIEVE_SANDBOXED" in declared, f"{service} must pin SIEVE_SANDBOXED, not omit it"


def test_the_second_gate_is_on_for_the_sandbox_and_off_everywhere_else() -> None:
    assert effective_env("sandbox-worker")["SIEVE_SANDBOXED"] == "1"
    assert effective_env("api")["SIEVE_SANDBOXED"] == "0"
    assert effective_env("worker")["SIEVE_SANDBOXED"] == "0"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_a_dotenv_cannot_open_the_second_gate(service: str) -> None:
    """A developer's `.env` with SIEVE_SANDBOXED=1 must not reach the un-sandboxed services.

    Without the explicit "0" pin the two gates are not independent: one line in a root `.env`
    would satisfy `in_sandbox()` everywhere, leaving only the first gate standing (SC-1).
    """
    hostile = {"SIEVE_SANDBOXED": "1", "SIEVE_ALLOW_REMOTE_MCP": "true"}
    merged = effective_env(service, hostile)
    assert merged["SIEVE_SANDBOXED"] == "0"
    assert merged["SIEVE_ALLOW_REMOTE_MCP"] == "false"

    # And the same `.env` still leaves the sandbox itself working as intended.
    sandbox = effective_env("sandbox-worker", hostile)
    assert (sandbox["SIEVE_SANDBOXED"], sandbox["SIEVE_ALLOW_REMOTE_MCP"]) == ("1", "true")


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "1 ", " 1", "TRUE", "on"])
def test_in_sandbox_accepts_only_the_literal_one(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sieve.worker import remote

    monkeypatch.setenv(remote.SANDBOX_ENV_VAR, value)
    assert remote.in_sandbox() is False


def test_in_sandbox_is_true_for_exactly_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from sieve.worker import remote

    monkeypatch.setenv(remote.SANDBOX_ENV_VAR, "1")
    assert remote.in_sandbox() is True


async def test_the_runtime_refuses_a_remote_fetch_outside_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both gates, independently: the flag alone is not enough."""
    from sieve.worker import remote

    monkeypatch.setattr(remote.settings, "allow_remote_mcp", True)
    monkeypatch.delenv(remote.SANDBOX_ENV_VAR, raising=False)
    with pytest.raises(remote.NotSandboxedError, match="sandbox"):
        await remote.fetch_tools("https://target.example/mcp")

    monkeypatch.setenv(remote.SANDBOX_ENV_VAR, "0")
    with pytest.raises(remote.NotSandboxedError):
        await remote.fetch_tools("https://target.example/mcp")


async def test_the_flag_is_still_required_inside_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sieve.worker import remote

    monkeypatch.setattr(remote.settings, "allow_remote_mcp", False)
    monkeypatch.setenv(remote.SANDBOX_ENV_VAR, "1")
    with pytest.raises(remote.RemoteDisabledError, match=remote.REMOTE_DISABLED):
        await remote.fetch_tools("https://target.example/mcp")


def test_the_default_settings_keep_remote_scanning_off() -> None:
    from sieve.config import Settings

    assert Settings().allow_remote_mcp is False


@pytest.mark.docker
def test_the_sandbox_image_builds_and_refuses_a_blocked_host() -> None:  # pragma: no cover
    pytest.skip("needs a Docker daemon; not available on this machine")
