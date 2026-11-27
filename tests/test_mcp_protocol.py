"""MCP protocol-level tests.

These tests verify that every tool is invocable through the MCP JSON-RPC
protocol layer, exactly as Cursor or Claude Code would call them. This
catches JSON Schema issues and parameter serialisation bugs.

Discovery runs against the production process over stdio. In-memory
invocations use the same registered tools with a synthetic SDK client.
"""

import json
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from notebooklm_mcp_server import AppContext, mcp


@pytest.fixture
def mcp_session(monkeypatch, mock_client):
    @asynccontextmanager
    async def mock_lifespan(_server):
        yield AppContext(client=mock_client, profile="test")

    monkeypatch.setattr(mcp._mcp_server, "lifespan", mock_lifespan)
    return create_connected_server_and_client_session(mcp, raise_exceptions=True)


async def test_production_stdio_discovery(tmp_path):
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parents[1] / "notebooklm_mcp_server.py")],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "NOTEBOOKLM_HOME": str(tmp_path / ".notebooklm")},
    )
    async with (
        stdio_client(server) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resources()
        notebooks = await session.read_resource("notebooklm://notebooks")
        templates = await session.list_resource_templates()
        prompts = await session.list_prompts()
        prompt = await session.get_prompt("generate_styled_slides", {"notebook_id": "demo", "style": "creative"})

    assert {tool.name for tool in tools.tools} == {
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
    assert {str(resource.uri) for resource in resources.resources} == {"notebooklm://notebooks"}
    assert "Not authenticated" in notebooks.contents[0].text
    assert {template.uriTemplate for template in templates.resourceTemplates} == {"notebooklm://notebook/{notebook_id}"}
    assert {item.name for item in prompts.prompts} == {
        "analyze_notebook_sources",
        "research_topic_workflow",
        "generate_styled_slides",
    }
    assert "Tone: Bold, contemporary, confident, visually striking." in prompt.messages[0].content.text
    assert "notebook_id: demo" in prompt.messages[0].content.text


class TestProtocolToolInvocation:
    """Verify tools are callable through the MCP protocol layer."""

    async def test_list_notebooks_via_protocol(self, mcp_session):
        async with mcp_session as session:
            result = await session.call_tool("list_notebooks_tool", {})
        text = result.content[0].text
        data = json.loads(text)
        assert data["count"] == 2
        assert data["notebooks"][0]["id"] == "nb-1"

    async def test_add_sources_via_protocol(self, mcp_session):
        """The bug that started it all: list[dict] was not callable via MCP."""
        sources_json = json.dumps(
            [
                {"type": "text", "title": "Test", "value": "Hello world"},
            ]
        )
        async with mcp_session as session:
            result = await session.call_tool(
                "add_sources",
                {"notebook_id": "nb-proto", "sources": sources_json},
            )
        text = result.content[0].text
        data = json.loads(text)
        assert data["succeeded"] == 1

    async def test_ask_question_with_source_ids_via_protocol(self, mcp_session):
        async with mcp_session as session:
            result = await session.call_tool(
                "ask_question",
                {
                    "notebook_id": "nb-proto",
                    "question": "What is innovation?",
                    "source_ids": "src-1,src-2",
                },
            )
        text = result.content[0].text
        data = json.loads(text)
        assert data["answer"] == "The key findings are..."

    async def test_generate_and_download_via_protocol(self, mcp_session):
        async with mcp_session as session:
            result = await session.call_tool(
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
        async with mcp_session as session:
            result = await session.call_tool(
                "add_sources",
                {"notebook_id": "nb-proto", "sources": "not json"},
            )
        text = result.content[0].text
        data = json.loads(text)
        assert "error" in data
        assert "Invalid JSON" in data["error"]

    async def test_tool_schemas_have_no_complex_types(self, mcp_session):
        """Every tool parameter schema should be a simple scalar type."""
        async with mcp_session as session:
            result = await session.list_tools()
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
