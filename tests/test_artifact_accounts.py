"""Account switches overlapping artifact work, using two synthetic clients."""

import asyncio
import json
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from notebooklm import ArtifactType
from notebooklm.auth import load_httpx_cookies

from notebooklm_mcp_server import app_lifespan, export_artifact, generate_and_download, mcp, switch_account


@pytest.fixture(params=["generate", "export"])
def report_request(request, tmp_path, mock_ctx, mock_client):
    path = str(tmp_path / "report.md")
    if request.param == "generate":
        return partial(generate_and_download, "notebook", "report", path, ctx=mock_ctx)
    mock_client.artifacts.get.return_value = SimpleNamespace(id="selected", kind=ArtifactType.REPORT)
    return partial(export_artifact, "notebook", "selected", path, ctx=mock_ctx)


@pytest.fixture
def other_client(tmp_path, monkeypatch):
    target = tmp_path / ".notebooklm-work"
    target.mkdir()
    (target / "storage_state.json").touch()
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path / ".notebooklm-test"))
    monkeypatch.setattr("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", tmp_path / "active.json")
    client = AsyncMock()
    monkeypatch.setattr("notebooklm_mcp_server.NotebookLMClient.from_storage", AsyncMock(return_value=client))
    return client


async def test_switch_during_authentication_aborts_artifact(
    report_request, mock_ctx, mock_client, other_client, tmp_path
):
    async def switch_while_validating():
        assert (await switch_account("work", mock_ctx))["success"]
        return []

    mock_client.notebooks.list.side_effect = switch_while_validating
    result = await report_request()
    for client in (mock_client, other_client):
        client.artifacts.generate_report.assert_not_awaited()
        client.artifacts.get.assert_not_awaited()
        client.artifacts.download_report.assert_not_awaited()
    assert "error" in result
    assert not (tmp_path / "report.md").exists()


async def test_switch_before_download_aborts_artifact(report_request, mock_ctx, mock_client, other_client, tmp_path):
    async def switch_after_lookup(*args, **kwargs):
        assert (await switch_account("work", mock_ctx))["success"]
        return SimpleNamespace(id="selected", kind=ArtifactType.REPORT, is_complete=True, task_id="task-report")

    mock_client.artifacts.wait_for_completion.side_effect = switch_after_lookup
    mock_client.artifacts.get.side_effect = switch_after_lookup
    result = await report_request()
    assert "error" in result
    for client in (mock_client, other_client):
        client.artifacts.download_report.assert_not_awaited()
    assert not (tmp_path / "report.md").exists()


async def test_switch_while_browser_login_is_pending(report_request, app_context, mock_ctx, other_client, monkeypatch):
    app_context.client = None
    login_started = asyncio.Event()
    finish_login = asyncio.Event()

    async def login(profile, ctx):
        login_started.set()
        await finish_login.wait()
        return True

    monkeypatch.setattr("notebooklm_mcp_server._run_login", login)
    pending = asyncio.create_task(report_request())
    try:
        await asyncio.wait_for(login_started.wait(), 5)
        assert (await asyncio.wait_for(switch_account("work", mock_ctx), 2))["success"]
    finally:
        finish_login.set()
        result = await asyncio.wait_for(pending, 5)
    assert "error" in result
    assert app_context.client is other_client
    other_client.__aexit__.assert_not_awaited()
    other_client.artifacts.get.assert_not_awaited()
    other_client.artifacts.generate_report.assert_not_awaited()


async def test_switch_during_download_keeps_original_credentials(
    report_request, mock_ctx, mock_client, other_client, tmp_path
):
    switch_started = asyncio.Event()
    observed_homes = []
    switch_task = None

    async def switch():
        switch_started.set()
        return await switch_account("work", mock_ctx)

    async def download(notebook_id, output_path, artifact_id):
        nonlocal switch_task
        switch_task = asyncio.create_task(switch())
        await switch_started.wait()
        observed_homes.append(Path(os.environ["NOTEBOOKLM_HOME"]))
        mock_client.__aexit__.assert_not_awaited()
        Path(output_path).write_text(artifact_id, encoding="utf-8")

    mock_client.artifacts.download_report.side_effect = download
    result = await report_request()
    assert (await asyncio.wait_for(switch_task, 5))["success"]
    assert observed_homes == [tmp_path / ".notebooklm-test"]
    assert result["success"]
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == result["artifact_id"]
    assert Path(os.environ["NOTEBOOKLM_HOME"]) == tmp_path / ".notebooklm-work"
    other_client.artifacts.download_report.assert_not_awaited()


@pytest.mark.parametrize("switch_timing", ["before", "during"])
async def test_legacy_download_reads_its_sessions_storage(switch_timing, mock_ctx, mock_client, tmp_path, monkeypatch):
    homes = {name: tmp_path / f".notebooklm-{name}" for name in ("test", "work")}
    for name, home in homes.items():
        home.mkdir()
        cookies = [{"name": key, "value": name, "domain": ".google.com"} for key in ("SID", "__Secure-1PSIDTS", "OSID")]
        (home / "storage_state.json").write_text(json.dumps({"cookies": cookies}), encoding="utf-8")
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(homes["test"]))
    monkeypatch.delenv("NOTEBOOKLM_AUTH_JSON", raising=False)
    monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
    monkeypatch.setattr("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", tmp_path / "active.json")
    monkeypatch.setattr("notebooklm_mcp_server._PROFILE_AWARE_STORAGE", False)
    factory = AsyncMock(side_effect=[mock_client, AsyncMock(), AsyncMock()])
    monkeypatch.setattr("notebooklm_mcp_server.NotebookLMClient.from_storage", factory)

    peer_ctx = MagicMock()
    peer_ctx.info = AsyncMock()
    switch_started = asyncio.Event()

    async def switch_peer():
        switch_started.set()
        return await switch_account("work", peer_ctx)

    # SDK 0.3.2 looks up its cookie file after awaiting the artifact metadata.
    write_deck = mock_client.artifacts.download_slide_deck.side_effect
    observed = []
    pending_switch = None

    async def download(notebook_id, output_path, artifact_id):
        nonlocal pending_switch
        if switch_timing == "during":
            pending_switch = asyncio.create_task(switch_peer())
            await switch_started.wait()
        observed.append(load_httpx_cookies()["SID"])
        return write_deck(notebook_id, output_path, artifact_id)

    mock_client.artifacts.download_slide_deck.side_effect = download
    async with app_lifespan(mcp) as first, app_lifespan(mcp) as second:
        mock_ctx.request_context.lifespan_context = first
        peer_ctx.request_context.lifespan_context = second
        output = str(tmp_path / "deck.pdf")
        if switch_timing == "before":
            assert (await switch_peer())["success"]
            mock_client.artifacts.get.return_value = SimpleNamespace(id="selected", kind=ArtifactType.SLIDE_DECK)
            result = await export_artifact("notebook", "selected", output, ctx=mock_ctx)
        else:
            result = await generate_and_download("notebook", "slide_deck", output, ctx=mock_ctx)
        if pending_switch:
            assert (await asyncio.wait_for(pending_switch, 5))["success"]
        assert result["success"], result
        assert observed == ["test"]
        assert first.profile == "test" and second.profile == "work"
        assert Path(os.environ["NOTEBOOKLM_HOME"]) == homes["work"]
