"""Account workflows with temporary SDK and legacy session-state files."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from notebooklm.paths import get_storage_path

import notebooklm_mcp_server as server


@pytest.fixture(params=["profiles/default/storage_state.json", "storage_state.json"], ids=["sdk", "legacy"])
def layout(request):
    return request.param


def write_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")


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
    def simulate_login(args, env, **kwargs):
        assert env["NOTEBOOKLM_HOME"] == str(tmp_path / ".notebooklm-work")
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
