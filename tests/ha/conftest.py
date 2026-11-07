"""HA integration test harness (issue #100).

Requires ``pytest-homeassistant-custom-component`` — see
``tests/requirements_test.txt``. Run from repo root::

    pytest tests/ha -v
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# phacc disables sockets in every test's runtest_setup — a hook that
# always runs after pytest_socket's own marker/flag handling, so no
# marker can re-enable. On Windows, asyncio's ProactorEventLoop builds
# its wakeup self-pipe from a real AF_INET socketpair inside *fixture*
# setup (including phacc's session-scoped ones) — ordering can't be
# won. Neutralise the guard for this suite: the API client is fully
# mocked, so no real network traffic occurs anyway.
import pytest_socket  # noqa: E402

pytest_socket.disable_socket = lambda allow_unix_socket=False: None  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations: None) -> None:
    """Make ``custom_components/family_finance`` loadable in tests."""
    return


@pytest.fixture()
def mock_api() -> Generator[AsyncMock]:
    """Patch the API client — tests assert HA-side behaviour, not HTTP."""
    with patch(
        "custom_components.family_finance.api.FamilyFinanceClient"
        ".async_fetch_summary",
        new_callable=AsyncMock,
    ) as fetch, patch(
        "custom_components.family_finance.api.FamilyFinanceClient"
        ".async_validate",
        new_callable=AsyncMock,
    ) as validate:
        validate.return_value = {"version": "0.1.0"}
        fetch.return_value = {
            "app_version": "0.1.0", "account_count": 3}
        fetch.validate = validate  # type: ignore[attr-defined]
        yield fetch
