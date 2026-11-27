"""Account workflows with temporary SDK and legacy session-state files."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from notebooklm._auth import refresh
from notebooklm.paths import get_storage_path, profile_from_storage_path

import notebooklm_mcp_server as server


@pytest.fixture(params=["profiles/default/storage_state.json", "storage_state.json"], ids=["sdk", "legacy"])
def layout(request):
    return request.param


def write_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")


def dummy_state(identity):
    cookies = [
        {"name": name, "value": identity, "domain": ".google.com", "path": "/", "secure": True}
        for name in ("SID", "OSID", "__Secure-1PSIDTS")
    ]
    return json.dumps({"cookies": cookies, "origins": []})


@pytest.fixture
def offline_token_fetch(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_BACKEND", "web")
    monkeypatch.setenv("NOTEBOOKLM_TRANSPORT", "httpx")
    fetch = AsyncMock(return_value=("dummy-csrf", "dummy-session"))
    monkeypatch.setattr(refresh, "_fetch_tokens_with_jar", fetch)
    return fetch


async def test_startup_prefers_inline_identity_to_legacy_file(tmp_path, monkeypatch, mock_ctx, offline_token_fetch):
    home = tmp_path / ".notebooklm"
    home.mkdir()
    (home / "storage_state.json").write_text(dummy_state("dummy-file"), encoding="utf-8")
    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", dummy_state("dummy-inline"))

    async with server.app_lifespan(server.mcp) as app:
        mock_ctx.request_context.lifespan_context = app
        assert app.client.auth.flat_cookies["SID"] == "dummy-inline"
        assert app.client.auth.storage_path is None
        assert (await server.get_account_info(mock_ctx))["auth_source"] == "NOTEBOOKLM_AUTH_JSON"
        assert (await server.switch_account("personal", mock_ctx))["success"]
        assert app.client.auth.flat_cookies["SID"] == "dummy-file"
        assert (await server.get_account_info(mock_ctx))["auth_source"] == "storage_state.json"


async def test_failed_switch_restores_previous_client_and_home(tmp_path, mock_ctx, offline_token_fetch):
    for dirname in (".notebooklm", ".notebooklm-work"):
        home = tmp_path / dirname
        home.mkdir()
        (home / "storage_state.json").write_text(dummy_state(dirname), encoding="utf-8")

    async with server.app_lifespan(server.mcp) as app:
        mock_ctx.request_context.lifespan_context = app
        previous = app.client
        offline_token_fetch.side_effect = OSError("dummy connection failure")
        result = await server.switch_account("work", mock_ctx)
        assert result["success"] is False
        assert app.client is previous and previous.is_connected
        assert server._current_profile_name() == app.profile == "personal"
        assert os.environ["NOTEBOOKLM_HOME"] == str(tmp_path / ".notebooklm")


async def test_switch_routes_cold_and_mid_session_refresh_to_selected_home(
    tmp_path, monkeypatch, mock_ctx, offline_token_fetch
):
    for account, sdk_profile in [(".notebooklm", "personal-sdk"), (".notebooklm-work", "work-sdk")]:
        home = tmp_path / account
        state = home / "profiles" / sdk_profile / "storage_state.json"
        state.parent.mkdir(parents=True)
        state.write_text(dummy_state(account), encoding="utf-8")
        (home / "config.json").write_text(json.dumps({"default_profile": sdk_profile}), encoding="utf-8")

    routes = []

    def record_hook(args, *, env, **kwargs):
        routes.append(
            (env["NOTEBOOKLM_HOME"], env["NOTEBOOKLM_REFRESH_PROFILE"], env["NOTEBOOKLM_REFRESH_STORAGE_PATH"])
        )
        return Mock(returncode=0)

    monkeypatch.setattr(refresh.subprocess, "run", record_hook)
    monkeypatch.setenv("NOTEBOOKLM_REFRESH_CMD", "dummy-refresh")
    monkeypatch.setenv("NOTEBOOKLM_REFRESH_CMD_MIDSESSION", "1")
    monkeypatch.delenv("_NOTEBOOKLM_REFRESH_ATTEMPTED", raising=False)
    offline_token_fetch.side_effect = [ValueError("Authentication expired"), ("dummy-csrf", "dummy-session")]
    result = await server.switch_account("work", mock_ctx)
    assert result["success"], result
    client = mock_ctx.request_context.lifespan_context.client
    try:
        path = client.auth.storage_path
        assert await refresh.try_refresh_cmd_reauth(
            storage_path=path, cookie_jar=client.auth.cookie_jar, profile=profile_from_storage_path(path)
        )
        work = tmp_path / ".notebooklm-work"
        assert routes == [(str(work), "work-sdk", str(work / "profiles/work-sdk/storage_state.json"))] * 2
    finally:
        await client.__aexit__(None, None, None)


@pytest.fixture
def opened_storage(monkeypatch, mock_client):
    """Record which state the SDK would load, without contacting Google."""
    opened = []

    async def from_storage(path=None):
        opened.append(Path(path) if path else get_storage_path())
        return mock_client

    monkeypatch.setattr(server.NotebookLMClient, "from_storage", from_storage)
    return opened


async def test_startup_uses_configured_home(tmp_path, monkeypatch, layout, opened_storage, mock_client):
    home = tmp_path / "custom-home"
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    write_state(home / layout)

    async with server.app_lifespan(server.mcp) as app:
        assert app.client is mock_client
        assert opened_storage == [home / layout]


async def test_discover_and_switch_existing_account(tmp_path, layout, opened_storage, mock_ctx):
    target = tmp_path / ".notebooklm-team"
    write_state(target / layout)
    write_state(tmp_path / ".notebooklm-work" / "profiles/default/storage_state.json")

    info = await server.get_account_info(mock_ctx)
    assert info["available_profiles"] == {"work": str(tmp_path / ".notebooklm-work"), "team": str(target)}
    assert os.environ["NOTEBOOKLM_HOME"] == str(tmp_path / ".notebooklm")
    result = await server.switch_account("team", mock_ctx)
    assert result["success"], result
    assert opened_storage == [target / layout]
    assert server._current_profile_name() == "team"


async def test_existing_profile_skips_login(tmp_path, layout, monkeypatch, mock_ctx):
    write_state(tmp_path / ".notebooklm-design" / layout)
    login = AsyncMock()
    monkeypatch.setattr(server, "_run_login", login)

    result = await server.create_profile("design", mock_ctx)
    assert result["success"] is False
    assert "already exists" in result["error"]
    login.assert_not_awaited()


@pytest.mark.parametrize(
    ("exit_code", "writes_state", "success"), [(0, True, True), (0, False, False), (1, True, False)]
)
async def test_login_checks_state_written_by_cli(tmp_path, monkeypatch, layout, exit_code, writes_state, success):
    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", dummy_state("dummy-inline"))

    def simulate_login(args, env, **kwargs):
        assert env["NOTEBOOKLM_HOME"] == str(tmp_path / ".notebooklm-work")
        assert "NOTEBOOKLM_AUTH_JSON" not in env
        if writes_state:
            write_state(Path(env["NOTEBOOKLM_HOME"]) / layout)
        return Mock(returncode=exit_code, communicate=Mock(return_value=(b"", b"")))

    monkeypatch.setattr(server, "_find_cli", lambda: tmp_path / "notebooklm")
    monkeypatch.setattr(server.subprocess, "Popen", simulate_login)
    assert await server._run_login("work") is success


async def test_sdk_profile_takes_precedence_over_legacy(tmp_path, opened_storage, mock_ctx):
    home = tmp_path / ".notebooklm-work"
    write_state(home / "storage_state.json")
    write_state(home / "profiles/default/storage_state.json")

    result = await server.switch_account("work", mock_ctx)
    assert result["success"], result
    assert opened_storage == [home / "profiles/default/storage_state.json"]


async def test_discovery_uses_each_homes_configured_sdk_profile(tmp_path, mock_ctx):
    for account in ("work", "design"):
        home = tmp_path / f".notebooklm-{account}"
        write_state(home / "profiles" / account / "storage_state.json")
        config = home / "config.json"
        config.write_text(json.dumps({"default_profile": account}), encoding="utf-8")
        os.utime(config, (1700000000, 1700000000))

    info = await server.get_account_info(mock_ctx)
    assert set(info["available_profiles"]) == {"work", "design"}


@pytest.fixture
def invalid_account(tmp_path):
    home = tmp_path / ".notebooklm-broken"
    home.mkdir()
    (home / "config.json").write_text('{"default_profile": "."}', encoding="utf-8")
    write_state(tmp_path / ".notebooklm-work/profiles/default/storage_state.json")


async def test_invalid_candidate_does_not_hide_healthy_accounts(invalid_account, mock_ctx, tmp_path):
    info = await server.get_account_info(mock_ctx)
    assert info["available_profiles"] == {"work": str(tmp_path / ".notebooklm-work")}


async def test_invalid_active_account_can_switch_after_startup(invalid_account, mock_ctx, opened_storage):
    server._write_active_profile("broken")
    async with server.app_lifespan(server.mcp) as app:
        assert app.client is None
        mock_ctx.request_context.lifespan_context = app
        assert (await server.get_account_info(mock_ctx))["current_account"] == "broken"
        assert (await server.switch_account("work", mock_ctx))["success"]
        assert app.profile == "work"


@pytest.mark.parametrize("tool", [server.switch_account, server.create_profile])
async def test_invalid_target_reports_error_without_changing_account(invalid_account, tool, mock_ctx, mock_client):
    result = await tool("broken", mock_ctx)
    assert result["success"] is False
    assert "profile" in result["error"].lower()
    assert mock_ctx.request_context.lifespan_context.client is mock_client
    server._run_login.assert_not_awaited()
