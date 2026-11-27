"""Account switches overlapping artifact work, using two synthetic clients."""

import asyncio
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from notebooklm import ArtifactType

from notebooklm_mcp_server import export_artifact, generate_and_download, switch_account


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
