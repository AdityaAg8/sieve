from __future__ import annotations

from collections.abc import Iterator

import pytest

from sieve.cli import net


@pytest.fixture(autouse=True)
def _reset_network_guard() -> Iterator[None]:
    """`net.install_guard()` monkeypatches `socket.socket.connect` process-wide and on purpose
    never undoes itself in production. Tests still need isolation from each other."""
    net._reset_guard_for_tests()
    yield
    net._reset_guard_for_tests()
