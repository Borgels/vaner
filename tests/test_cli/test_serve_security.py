# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest
import typer

from vaner.cli.main import _require_safe_mcp_sse_exposure, _require_safe_proxy_exposure


def test_proxy_requires_token_for_non_loopback_host() -> None:
    with pytest.raises(typer.BadParameter):
        _require_safe_proxy_exposure("0.0.0.0", "")


def test_proxy_allows_non_loopback_host_with_token() -> None:
    _require_safe_proxy_exposure("0.0.0.0", "abc123")


def test_mcp_sse_refuses_non_loopback_host() -> None:
    with pytest.raises(typer.BadParameter):
        _require_safe_mcp_sse_exposure("0.0.0.0")
