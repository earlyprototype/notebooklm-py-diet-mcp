"""Tests for the server lifespan (startup/shutdown scenarios).

Verifies behaviour with valid credentials, expired credentials,
and missing credentials -- all mocked, no network access.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notebooklm_mcp_server import app_lifespan, AppContext


class TestLifespanValidCredentials:
    async def test_creates_client_when_storage_exists(self, tmp_path):
        profile_dir = tmp_path / ".notebooklm"
        profile_dir.mkdir()
        (profile_dir / "storage_state.json").write_text("{}", encoding="utf-8")

        mock_client = AsyncMock()
        mock_client.close = AsyncMock()

        server = MagicMock()

        with (
            patch("notebooklm_mcp_server._current_profile_name", return_value="personal"),
            patch("notebooklm_mcp_server._resolve_profile_dir", return_value=profile_dir),
            patch("notebooklm_mcp_server._create_client", return_value=mock_client) as mock_create,
        ):
            async with app_lifespan(server) as ctx:
                assert isinstance(ctx, AppContext)
                assert ctx.client is mock_client
                assert ctx.profile == "personal"
                mock_create.assert_awaited_once_with("personal")

            mock_client.close.assert_awaited_once()


class TestLifespanExpiredCredentials:
    async def test_starts_with_none_client_on_expired_session(self, tmp_path):
        profile_dir = tmp_path / ".notebooklm-work"
        profile_dir.mkdir()
        (profile_dir / "storage_state.json").write_text("{}", encoding="utf-8")

        server = MagicMock()

        with (
            patch("notebooklm_mcp_server._current_profile_name", return_value="work"),
            patch("notebooklm_mcp_server._resolve_profile_dir", return_value=profile_dir),
            patch("notebooklm_mcp_server._create_client", side_effect=ValueError("Session expired")),
        ):
            async with app_lifespan(server) as ctx:
                assert ctx.client is None
                assert ctx.profile == "work"


class TestLifespanMissingCredentials:
    async def test_starts_with_none_client_when_no_storage(self, tmp_path):
        profile_dir = tmp_path / ".notebooklm-personal"
        profile_dir.mkdir()

        server = MagicMock()

        with (
            patch("notebooklm_mcp_server._current_profile_name", return_value="personal"),
            patch("notebooklm_mcp_server._resolve_profile_dir", return_value=profile_dir),
        ):
            async with app_lifespan(server) as ctx:
                assert ctx.client is None
                assert ctx.profile == "personal"
