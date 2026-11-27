"""Tests for the server lifespan (startup/shutdown scenarios).

Verifies behaviour with valid credentials, expired credentials,
and missing credentials -- all mocked, no network access.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from notebooklm.paths import get_storage_path

from notebooklm_mcp_server import AppContext, _run_login, app_lifespan, create_profile, get_account_info, switch_account


@pytest.mark.parametrize("layout", ["fresh", "legacy", "both", "configured", "environment"])
async def test_login_startup_and_switch_follow_sdk_storage(layout, tmp_path, monkeypatch, mock_ctx):
    target = tmp_path / ".notebooklm-work"
    target.mkdir()
    root = target / "storage_state.json"
    if layout in {"legacy", "both", "configured", "environment"}:
        root.touch()
    if layout == "both":
        nested = target / "profiles" / "default" / root.name
        nested.parent.mkdir(parents=True)
        nested.touch()
    monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
    if layout in {"configured", "environment"}:
        (target / "config.json").write_text('{"default_profile": "team"}', encoding="utf-8")
    if layout == "environment":
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "selected")
    with monkeypatch.context() as cli_env:
        cli_env.setenv("NOTEBOOKLM_HOME", str(target))
        expected = get_storage_path()

    previous_home = str(tmp_path / ".notebooklm-test")
    monkeypatch.setenv("NOTEBOOKLM_HOME", previous_home)
    monkeypatch.setattr("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", tmp_path / "active.json")
    monkeypatch.setattr("notebooklm_mcp_server._find_cli", lambda: "synthetic-cli")

    def login(args, env, **kwargs):
        assert env["NOTEBOOKLM_HOME"] == str(target)
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.touch()
        return Mock(returncode=0, communicate=Mock(return_value=(b"", b"")))

    monkeypatch.setattr("notebooklm_mcp_server.subprocess.Popen", login)
    factory = AsyncMock(return_value=AsyncMock())
    monkeypatch.setattr("notebooklm_mcp_server.NotebookLMClient.from_storage", factory)
    assert await _run_login("work")
    assert (await get_account_info(mock_ctx))["available_profiles"]["work"] == str(target)
    assert "already exists" in (await create_profile("work", mock_ctx))["error"]
    assert (await switch_account("work", mock_ctx))["success"]
    factory.assert_awaited_once_with(path=str(expected))
    factory.reset_mock()
    async with app_lifespan(MagicMock()) as app:
        assert app.profile == "work"
        factory.assert_awaited_once_with(path=str(expected))


class TestLifespanValidCredentials:
    async def test_creates_client_when_storage_exists(self, tmp_path):
        profile_dir = tmp_path / ".notebooklm"
        profile_dir.mkdir()
        (profile_dir / "storage_state.json").write_text("{}", encoding="utf-8")

        mock_client = AsyncMock()

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

            mock_client.__aexit__.assert_awaited_once()


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
