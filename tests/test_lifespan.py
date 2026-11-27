"""Tests for the server lifespan (startup/shutdown scenarios).

Verifies behaviour with valid credentials, expired credentials,
and missing credentials -- all mocked, no network access.
"""

import asyncio
import subprocess
import sys
import threading
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from notebooklm.paths import get_storage_path

import notebooklm_mcp_server as server
from notebooklm_mcp_server import AppContext, _run_login, app_lifespan, create_profile, get_account_info, switch_account


@pytest.mark.parametrize("interrupt", ["cancel", "timeout"])
async def test_interrupted_create_profile_reaps_before_unlock(interrupt, tmp_path, monkeypatch, mock_ctx):
    profile = str(tmp_path / "disposable")
    monkeypatch.setattr(server, "_run_login", _run_login)
    monkeypatch.setattr(server, "_find_cli", lambda: sys.executable)
    monkeypatch.setattr(server, "LOGIN_TIMEOUT", 0.2 if interrupt == "timeout" else 30)
    popen = subprocess.Popen
    launched = asyncio.Event()
    stopping = asyncio.Event()
    allow_reap = threading.Event()
    child = None

    def launch(args, env, **kwargs):
        nonlocal child
        assert args == [sys.executable, "login"]
        assert env["NOTEBOOKLM_HOME"] == profile
        child = popen([sys.executable, "-c", "import time; time.sleep(60)"], env=env, **kwargs)
        wait, kill = child.wait, child.kill

        def gated_wait(*args, **kwargs):
            assert allow_reap.wait(10)
            return wait(*args, **kwargs)

        def kill_child():
            kill()
            stopping.set()

        monkeypatch.setattr(child, "wait", gated_wait)
        monkeypatch.setattr(child, "kill", kill_child)
        launched.set()
        return child

    monkeypatch.setattr(server.subprocess, "Popen", launch)
    first = asyncio.create_task(create_profile(profile, mock_ctx))
    tasks = [first]
    first.add_done_callback(lambda _: stopping.set())
    try:
        await asyncio.wait_for(launched.wait(), 5)
        # The second create_profile checks storage immediately after acquiring the lock.
        storage = tmp_path / "disposable" / "synthetic-state"
        storage.touch()
        observed_exit_codes = []

        def existing_storage(_directory):
            observed_exit_codes.append(child.returncode)
            return storage

        monkeypatch.setattr(server, "_storage_path", existing_storage)
        second = asyncio.create_task(create_profile(profile, mock_ctx))
        tasks.append(second)
        await asyncio.sleep(0)
        assert not observed_exit_codes
        if interrupt == "cancel":
            first.cancel()
        await asyncio.wait_for(stopping.wait(), 5)
        assert not first.done(), "create_profile released the lock before reaping its child"
        allow_reap.set()
        if interrupt == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            assert not (await first)["success"]
        assert "already exists" in (await asyncio.wait_for(second, 5))["error"]
        assert child.returncode not in (None, 0)
        assert observed_exit_codes == [child.returncode]
    finally:
        allow_reap.set()
        if child is not None:
            child.kill()
            child.wait(5)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
        return nullcontext(Mock(returncode=0, wait=Mock(return_value=0)))

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
