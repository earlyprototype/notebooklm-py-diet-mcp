"""Tests for the server lifespan (startup/shutdown scenarios).

Verifies behaviour with valid credentials, expired credentials,
and missing credentials -- all mocked, no network access.
"""

import asyncio
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext, suppress
from functools import partial
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from anyio import CancelScope
from notebooklm.paths import get_storage_path

import notebooklm_mcp_server as server
from notebooklm_mcp_server import AppContext, _run_login, app_lifespan, create_profile, get_account_info, switch_account
from tests.conftest import wait_for_login_pid


@pytest.fixture
def create_disposable_profile(tmp_path, monkeypatch, mock_ctx):
    monkeypatch.setattr(server, "_run_login", _run_login)
    monkeypatch.setattr(server, "_find_cli", lambda: sys.executable)
    return partial(create_profile, str(tmp_path / "disposable"), mock_ctx)


@pytest.fixture
async def occupied_default_executor():
    loop = asyncio.get_running_loop()
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop.set_default_executor(executor)
        # The login's event loop has one worker, reserved until teardown.
        worker = loop.run_in_executor(None, release.wait)
        try:
            yield
        finally:
            release.set()
            await worker


@pytest.mark.parametrize("interrupt", ["cancel", "timeout", "cancel_during_reap", "scope", "scope_during_reap"])
async def test_interrupted_create_profile_reaps_before_unlock(
    interrupt, tmp_path, monkeypatch, create_disposable_profile, occupied_default_executor
):
    times_out = interrupt in {"timeout", "scope_during_reap"}
    scope_cancelled = interrupt in {"scope", "scope_during_reap"}
    monkeypatch.setattr(server, "LOGIN_TIMEOUT", 0.2 if times_out else 30)
    popen = subprocess.Popen
    launched = asyncio.Event()
    stopping = asyncio.Event()
    allow_reap = threading.Event()
    child = None

    def launch(args, env, **kwargs):
        nonlocal child
        assert args[-1] == "login"
        assert env["NOTEBOOKLM_HOME"] == str(tmp_path / "disposable")
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
    scope = CancelScope()

    async def first_login():
        with scope:
            return await create_disposable_profile()

    first = asyncio.create_task(first_login())
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
        second = asyncio.create_task(create_disposable_profile())
        tasks.append(second)
        await asyncio.sleep(0)
        assert not observed_exit_codes
        if interrupt == "scope":
            scope.cancel()
        elif not times_out:
            first.cancel()
        await asyncio.wait_for(stopping.wait(), 5)
        assert not first.done(), "create_profile released the lock before reaping its child"
        if scope_cancelled:
            # A cancelled outer scope retries at each checkpoint until shielded.
            cancel = Mock(wraps=first.cancel)
            monkeypatch.setattr(first, "cancel", cancel)
            if times_out:
                scope.cancel()
            for _ in range(10):
                await asyncio.sleep(0)
            assert cancel.call_count == 0, "scope cancellation repeatedly interrupted the reap"
            assert not first.done()
            assert not observed_exit_codes
        if interrupt == "cancel_during_reap":
            assert first.cancel()
            await asyncio.sleep(0)
            assert not first.done()
        allow_reap.set()
        _, pending = await asyncio.wait(tasks, timeout=3)
        assert not pending, "profile operations stalled behind the occupied worker"
        if scope_cancelled:
            assert first.result() is None
            assert scope.cancelled_caught
        elif times_out:
            assert not first.result()["success"]
        else:
            with pytest.raises(asyncio.CancelledError):
                await first
        assert "already exists" in second.result()["error"]
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


async def test_successful_login_with_occupied_executor(
    monkeypatch, create_disposable_profile, occupied_default_executor
):
    monkeypatch.setattr(server, "LOGIN_TIMEOUT", 2)
    monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
    popen = subprocess.Popen

    def launch(args, env, **kwargs):
        script = "from pathlib import Path; Path('storage_state.json').touch()"
        return popen([sys.executable, "-c", script], cwd=env["NOTEBOOKLM_HOME"], env=env, **kwargs)

    monkeypatch.setattr(server.subprocess, "Popen", launch)
    login = asyncio.create_task(create_disposable_profile())
    done, _ = await asyncio.wait({login}, timeout=4)
    assert done, "login failed to observe child exit while the worker was occupied"
    assert login.result()["success"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console launcher and process handles")
@pytest.mark.parametrize("interrupt", ["cancel", "timeout"])
async def test_installed_login_interpreter_exited_at_next_lock(
    interrupt, tmp_path, monkeypatch, mock_ctx, synthetic_login_sdk
):
    import _winapi

    import win32con

    profile = tmp_path / "disposable"
    monkeypatch.setenv("PYTHONPATH", synthetic_login_sdk)
    monkeypatch.setattr(server, "_run_login", _run_login)
    monkeypatch.setattr(server, "LOGIN_TIMEOUT", 3 if interrupt == "timeout" else 30)
    first = asyncio.create_task(create_profile(str(profile), mock_ctx))
    handle = None
    tasks = [first]
    try:
        pid = await wait_for_login_pid(profile)
        handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE | win32con.PROCESS_TERMINATE, False, pid)
        assert _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT
        observed = []

        def check_storage(directory):
            # create_profile checks for existing storage immediately under the lock.
            observed.append(_winapi.WaitForSingleObject(handle, 0))
            return directory / "login.pid"

        monkeypatch.setattr(server, "_storage_path", check_storage)
        second = asyncio.create_task(create_profile(str(profile), mock_ctx))
        tasks.append(second)
        await asyncio.sleep(0)
        assert observed == []
        if interrupt == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            assert not (await first)["success"]
        assert "already exists" in (await second)["error"]
        assert observed == [_winapi.WAIT_OBJECT_0]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if handle is not None:
            try:
                if _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT:
                    # Windows also denies termination when exit is already in progress.
                    with suppress(PermissionError):
                        _winapi.TerminateProcess(handle, 1)
                    assert _winapi.WaitForSingleObject(handle, 5000) == _winapi.WAIT_OBJECT_0
            finally:
                _winapi.CloseHandle(handle)


async def test_cancel_at_child_exit_reaches_caller(monkeypatch, create_disposable_profile):
    popen = subprocess.Popen
    monkeypatch.setattr(server.subprocess, "Popen", lambda args, **kw: popen([sys.executable, "-c", "pass"], **kw))
    loop = asyncio.get_running_loop()
    submit = loop.run_in_executor
    accepted = []

    def wait_for_child(executor, wait):
        exited = submit(executor, wait)
        exited.add_done_callback(lambda _: accepted.append(login.cancel()))
        return exited

    monkeypatch.setattr(loop, "run_in_executor", wait_for_child)
    login = asyncio.create_task(create_disposable_profile())
    with pytest.raises(asyncio.CancelledError):
        await login
    assert accepted == [True]


async def test_rejected_waiter_reaps_child_without_blocking_loop(monkeypatch, create_disposable_profile):
    loop_thread = threading.get_ident()
    blocking_waits = []
    children = []

    class LoginChild(subprocess.Popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, "-c", "import time; time.sleep(0.5)"], **kwargs)
            children.append(self)

        def wait(self, *args, **kwargs):
            if threading.get_ident() == loop_thread and self.returncode is None:
                blocking_waits.append(self.pid)
            return super().wait(*args, **kwargs)

    def reject(executor, wait):
        raise RuntimeError("Executor has shut down")

    monkeypatch.setattr(server.subprocess, "Popen", LoginChild)
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", reject)
    assert not (await create_disposable_profile())["success"]
    assert blocking_waits == []
    (child,) = children
    assert child.returncode not in (None, 0)


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
