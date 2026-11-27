"""Account workflows with temporary SDK and legacy session-state files."""

import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from notebooklm._auth import refresh
from notebooklm.paths import get_storage_path

import notebooklm_mcp_server as server

from .conftest import make_mock_client


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


@pytest.fixture
def account_homes(tmp_path):
    for account, sdk_profile in [(".notebooklm", "personal-sdk"), (".notebooklm-work", "work-sdk")]:
        home = tmp_path / account
        state = home / "profiles" / sdk_profile / "storage_state.json"
        state.parent.mkdir(parents=True)
        state.write_text(dummy_state(account), encoding="utf-8")
        (home / "config.json").write_text(json.dumps({"default_profile": sdk_profile}), encoding="utf-8")


@pytest.fixture
def hook_routes(monkeypatch):
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
    return routes


@pytest.fixture
def expired_notebooks_rpc(monkeypatch):
    """Reject the first list call and the first homepage visit to trigger SDK recovery."""
    expired_rpc = True
    expired_home = True

    async def reply(_transport, request):
        nonlocal expired_rpc, expired_home
        if request.method == "POST":
            if expired_rpc:
                expired_rpc = False
                return httpx.Response(401)
            payload = json.dumps([["wrb.fr", request.url.params["rpcids"], "[[]]"]])
            return httpx.Response(200, text=")]}'\n\n" + f"{len(payload)}\n{payload}\n")
        if request.url.host == "accounts.google.com":
            return httpx.Response(200)
        if expired_home:
            expired_home = False
            return httpx.Response(302, headers={"location": "https://accounts.google.com/ServiceLogin"})
        return httpx.Response(200, text='{"SNlM0e":"dummy-csrf","FdrFJe":"dummy-session"}')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reply)


async def test_switch_routes_cold_and_mid_session_refresh_to_selected_home(
    tmp_path, account_homes, hook_routes, mock_ctx, offline_token_fetch, expired_notebooks_rpc
):
    async with server.app_lifespan(server.mcp) as app:
        mock_ctx.request_context.lifespan_context = app
        offline_token_fetch.side_effect = [ValueError("Authentication expired"), ("dummy-csrf", "dummy-session")]
        result = await server.switch_account("work", mock_ctx)
        assert result["success"], result
        assert await server.list_notebooks(mock_ctx) == "No notebooks found."
        work = tmp_path / ".notebooklm-work"
        assert hook_routes == [(str(work), "work-sdk", str(work / "profiles/work-sdk/storage_state.json"))] * 2


async def test_refresh_after_other_http_session_closes(
    tmp_path, account_homes, hook_routes, mock_ctx, offline_token_fetch, expired_notebooks_rpc
):
    async with AsyncExitStack() as first:
        await first.enter_async_context(server.app_lifespan(server.mcp))
        async with server.app_lifespan(server.mcp) as second:
            mock_ctx.request_context.lifespan_context = second
            assert (await server.switch_account("work", mock_ctx))["success"]
            await first.aclose()
            assert (await server.get_account_info(mock_ctx))["current_account"] == "work"
            assert await server.list_notebooks(mock_ctx) == "No notebooks found."
            work = tmp_path / ".notebooklm-work"
            assert hook_routes == [(str(work), "work-sdk", str(work / "profiles/work-sdk/storage_state.json"))]


async def test_cancelled_request_settles_its_sdk_refresh(account_homes, mock_ctx, offline_token_fetch, monkeypatch):
    refresh_started = asyncio.Event()
    refresh_stopped = asyncio.Event()
    release = asyncio.Event()

    async def reply(_transport, request):
        if request.method == "POST":
            return httpx.Response(401)
        refresh_started.set()
        try:
            await release.wait()
        finally:
            refresh_stopped.set()
        return httpx.Response(200, text='{"SNlM0e":"dummy-csrf","FdrFJe":"dummy-session"}')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reply)
    async with server.app_lifespan(server.mcp) as app:
        mock_ctx.request_context.lifespan_context = app
        assert (await server.switch_account("work", mock_ctx))["success"]
        request = asyncio.create_task(server.list_notebooks(mock_ctx))
        try:
            await asyncio.wait_for(refresh_started.wait(), timeout=5)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert refresh_stopped.is_set()
            assert app.client.is_connected
        finally:
            release.set()


@pytest.mark.parametrize("switch_before", ["first_poll", "import"])
async def test_switch_interrupts_research(switch_before, tmp_path, monkeypatch, mock_ctx, mock_client):
    write_state(tmp_path / ".notebooklm-work/profiles/default/storage_state.json")
    work_client = make_mock_client()
    monkeypatch.setattr(server, "_create_client", AsyncMock(return_value=work_client))

    async def switch(*args):
        mock_client.research.start.assert_awaited_once()
        assert (await server.switch_account("work", mock_ctx))["success"]

    if switch_before == "import":

        async def switch_before_import(message):
            if message.startswith("Importing "):
                await switch()

        mock_ctx.info.side_effect = switch_before_import
    sleep = switch if switch_before == "first_poll" else AsyncMock()
    monkeypatch.setattr(server, "asyncio", SimpleNamespace(sleep=sleep))
    result = await server.research_and_import("nb-1", "test", ctx=mock_ctx)
    assert "interrupt" in result["error"].lower()
    assert not result.get("success")
    assert mock_client.research.poll.await_count == (0 if switch_before == "first_poll" else 1)
    work_client.research.poll.assert_not_awaited()
    mock_client.research.import_sources.assert_not_awaited()
    work_client.research.import_sources.assert_not_awaited()


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
