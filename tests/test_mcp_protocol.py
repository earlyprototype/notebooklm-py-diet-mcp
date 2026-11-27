"""MCP protocol-level tests.

These tests verify that every tool is invocable through the MCP JSON-RPC
protocol layer, exactly as Cursor or Claude Code would call them. This
catches JSON Schema issues and parameter serialisation bugs.

The general invocation tests use a fresh FastMCP server with a mock lifespan.
Research tests use the production registration with a mocked NotebookLM client.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from notebooklm_mcp_server import (
    AppContext,
    add_sources,
    ask_question,
    create_notebook,
    create_profile,
    export_artifact,
    generate_and_download,
    get_account_info,
    list_artifacts,
    list_notebooks_tool,
    list_sources,
    mcp,
    pdf_to_png,
    png_to_pdf,
    research_and_import_tool,
    switch_account,
)


def _build_mock_client() -> AsyncMock:
    """Build a mock NotebookLM client with standard return values."""
    mock_client = AsyncMock()

    mock_notebook = MagicMock()
    mock_notebook.id = "nb-proto"
    mock_notebook.title = "Protocol Test"
    mock_client.notebooks.list.return_value = [mock_notebook]
    mock_client.notebooks.create.return_value = mock_notebook

    mock_source = MagicMock()
    mock_source.id = "src-proto"
    mock_source.title = "Test Source"
    mock_client.sources.list.return_value = [mock_source]
    mock_client.sources.add_url.return_value = mock_source
    mock_client.sources.add_text.return_value = mock_source
    mock_client.sources.add_file.return_value = mock_source

    mock_answer = MagicMock()
    mock_answer.answer = "Protocol test answer"
    mock_answer.citations = []
    mock_answer.conversation_id = "conv-1"
    mock_client.chat.ask.return_value = mock_answer

    mock_status = MagicMock()
    mock_status.task_id = "task-proto"
    mock_client.artifacts.generate_slide_deck.return_value = mock_status
    mock_client.artifacts.wait_for_completion.return_value = None
    mock_client.artifacts.download_slide_deck.return_value = None
    mock_client.artifacts.list.return_value = []

    return mock_client


@asynccontextmanager
async def _mock_lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    yield AppContext(client=_build_mock_client(), profile="test")


def _build_test_server() -> FastMCP:
    """Build a fresh FastMCP server with the diet tools and a mock lifespan."""
    server = FastMCP("NotebookLM-Test", lifespan=_mock_lifespan)

    tools = [
        list_notebooks_tool,
        create_notebook,
        list_sources,
        add_sources,
        ask_question,
        generate_and_download,
        list_artifacts,
        export_artifact,
        research_and_import_tool,
        get_account_info,
        switch_account,
        create_profile,
        pdf_to_png,
        png_to_pdf,
    ]
    for fn in tools:
        server.tool()(fn)

    return server


@pytest.fixture(scope="module")
def test_server():
    return _build_test_server()


@pytest.fixture
async def mcp_session(test_server):
    try:
        async with create_connected_server_and_client_session(test_server, raise_exceptions=True) as session:
            yield session
    except (RuntimeError, Exception):
        pass


class TestProtocolToolInvocation:
    """Verify tools are callable through the MCP protocol layer."""

    async def test_list_tools_returns_all_diet_tools(self, mcp_session):
        result = await mcp_session.list_tools()
        tool_names = {t.name for t in result.tools}
        expected = {
            "list_notebooks_tool",
            "create_notebook",
            "list_sources",
            "add_sources",
            "ask_question",
            "generate_and_download",
            "list_artifacts",
            "export_artifact",
            "research_and_import",
            "get_account_info",
            "switch_account",
            "create_profile",
            "pdf_to_png",
            "png_to_pdf",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    async def test_list_notebooks_via_protocol(self, mcp_session):
        result = await mcp_session.call_tool("list_notebooks_tool", {})
        text = result.content[0].text
        data = json.loads(text)
        assert data["count"] == 1
        assert data["notebooks"][0]["id"] == "nb-proto"

    async def test_add_sources_via_protocol(self, mcp_session):
        """The bug that started it all: list[dict] was not callable via MCP."""
        sources_json = json.dumps(
            [
                {"type": "text", "title": "Test", "value": "Hello world"},
            ]
        )
        result = await mcp_session.call_tool(
            "add_sources",
            {"notebook_id": "nb-proto", "sources": sources_json},
        )
        text = result.content[0].text
        data = json.loads(text)
        assert data["succeeded"] == 1

    async def test_ask_question_with_source_ids_via_protocol(self, mcp_session):
        result = await mcp_session.call_tool(
            "ask_question",
            {
                "notebook_id": "nb-proto",
                "question": "What is innovation?",
                "source_ids": "src-1,src-2",
            },
        )
        text = result.content[0].text
        data = json.loads(text)
        assert data["answer"] == "Protocol test answer"

    async def test_generate_and_download_via_protocol(self, mcp_session):
        result = await mcp_session.call_tool(
            "generate_and_download",
            {
                "notebook_id": "nb-proto",
                "artifact_type": "slide_deck",
                "output_path": "/tmp/test_slides.pdf",
                "instructions": "Corporate style",
            },
        )
        text = result.content[0].text
        data = json.loads(text)
        assert data["success"] is True

    async def test_add_sources_rejects_bad_json_via_protocol(self, mcp_session):
        result = await mcp_session.call_tool(
            "add_sources",
            {"notebook_id": "nb-proto", "sources": "not json"},
        )
        text = result.content[0].text
        data = json.loads(text)
        assert "error" in data
        assert "Invalid JSON" in data["error"]

    async def test_tool_schemas_have_no_complex_types(self, mcp_session):
        """Every tool parameter schema should be a simple scalar type."""
        result = await mcp_session.list_tools()
        scalar_types = {"string", "integer", "number", "boolean"}
        safe_compound = {"string", "integer", "number", "boolean", "null"}
        violations = []

        for tool in result.tools:
            schema = tool.inputSchema
            props = schema.get("properties", {})
            for param_name, param_schema in props.items():
                ptype = param_schema.get("type")
                if ptype in scalar_types:
                    continue
                if "anyOf" in param_schema and all(opt.get("type") in safe_compound for opt in param_schema["anyOf"]):
                    continue
                if ptype == "null":
                    continue
                violations.append(f"{tool.name}.{param_name}: {json.dumps(param_schema)}")

        assert not violations, "Complex parameter types found in MCP tool schemas:\n" + "\n".join(violations)


class TestResearchProtocol:
    @pytest.fixture
    def call_research(self, tmp_path, mock_client, monkeypatch):
        state = tmp_path / ".notebooklm" / "profiles" / "default" / "storage_state.json"
        state.parent.mkdir(parents=True)
        state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
        monkeypatch.setattr("notebooklm_mcp_server.NotebookLMClient.from_storage", AsyncMock(return_value=mock_client))
        monkeypatch.setattr("notebooklm_mcp_server._run_login", AsyncMock(return_value=False))
        monkeypatch.setattr("notebooklm_mcp_server.asyncio", SimpleNamespace(sleep=AsyncMock()))

        async def call():
            async with create_connected_server_and_client_session(mcp, raise_exceptions=True) as session:
                return await session.call_tool("research_and_import", {"notebook_id": "nb-1", "query": "test"})

        return call

    @pytest.mark.parametrize(
        ("status", "polls", "diagnostic"),
        [
            ("in_progress", 60, "timed out after 60 polls. Its outcome is unknown."),
            ("failed", 1, "did not complete (failed)."),
        ],
        ids=["timeout", "terminal-failure"],
    )
    async def test_failed_polling(self, call_research, mock_client, status, polls, diagnostic):
        mock_client.research.poll.return_value = {
            "task_id": "research-1",
            "status": status,
            "sources": [{"url": "https://example.com/partial"}],
        }

        result = await call_research()

        assert mock_client.research.poll.await_count == polls
        mock_client.research.import_sources.assert_not_called()
        payload = json.loads(result.content[0].text)
        assert diagnostic in payload["error"]
        assert result.isError is True

    async def test_confirmed_import(self, call_research, mock_client):
        result = await call_research()

        mock_client.research.import_sources.assert_awaited_once()
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["success"] is True
        assert payload["imported"] == 1

    async def test_partial_import(self, call_research, mock_client):
        mock_client.research.poll.return_value["sources"].append({"url": "https://example.com/second"})

        result = await call_research()

        payload = json.loads(result.content[0].text)
        assert payload["results_found"] == 2
        assert payload["imported"] == 1
        assert len(payload["import_details"]) == 1
        assert "confirmed 1 of 2" in payload["error"]
        assert result.isError is True
