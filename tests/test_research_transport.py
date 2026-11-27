"""Exercise research polling with the real SDK and offline batchexecute replies."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
from notebooklm import AuthTokens, NotebookLMClient
from notebooklm.rpc import RPCMethod

from notebooklm_mcp_server import research_and_import


def task_row(task_id, status_code, title):
    source = [f"https://example.com/{task_id}", title, None, 1]
    return [task_id, [None, ["test", 1], None, [[source], ""], status_code]]


async def run_research(rows, mock_ctx, mock_client, monkeypatch):
    calls = []
    imports = []

    async def reply(_transport, request):
        method = RPCMethod(request.url.params["rpcids"])
        calls.append(method)
        if method == RPCMethod.START_FAST_RESEARCH:
            payload = ["requested"]
        elif method == RPCMethod.POLL_RESEARCH:
            payload = [rows]
        elif method == RPCMethod.IMPORT_RESEARCH:
            envelope = json.loads(parse_qs(request.content.decode())["f.req"][0])
            params = json.loads(envelope[0][0][1])
            imports.append((params[2], [entry[2] for entry in params[4]]))
            payload = [[[["src-1"], "Requested"]]]
        else:
            raise AssertionError(f"Unexpected RPC {method}")
        frame = json.dumps([["wrb.fr", method.value, json.dumps(payload)]])
        return httpx.Response(200, text=")]}'\n\n" + f"{len(frame)}\n{frame}\n")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reply)
    monkeypatch.setattr("notebooklm_mcp_server.asyncio", SimpleNamespace(sleep=AsyncMock()))
    monkeypatch.setenv("NOTEBOOKLM_BACKEND", "web")
    auth = AuthTokens(cookies={}, csrf_token="test", session_id="test")
    async with NotebookLMClient(auth) as client:
        mock_client.research = client.research
        result = await research_and_import("nb-1", "test", ctx=mock_ctx)
    return result, calls, imports


async def test_imports_completed_second_row(mock_ctx, mock_client, monkeypatch):
    rows = [task_row("newer", 2, "Newer"), task_row("requested", 2, "Requested")]
    result, calls, imports = await run_research(rows, mock_ctx, mock_client, monkeypatch)

    assert result.get("success") is True, result
    assert result["imported"] == 1
    assert calls == [RPCMethod.START_FAST_RESEARCH, RPCMethod.POLL_RESEARCH, RPCMethod.IMPORT_RESEARCH]
    assert imports == [("requested", [["https://example.com/requested", "Requested"]])]


async def test_reports_cancellation_on_first_poll(mock_ctx, mock_client, monkeypatch):
    rows = [task_row("requested", 4, "Partial")]
    result, calls, imports = await run_research(rows, mock_ctx, mock_client, monkeypatch)

    assert "cancelled" in result["error"].lower(), result
    assert not result.get("success")
    assert calls == [RPCMethod.START_FAST_RESEARCH, RPCMethod.POLL_RESEARCH]
    assert imports == []
