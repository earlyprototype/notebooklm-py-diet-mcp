"""Discover tools, resources, templates and prompts over production stdio,
read the notebooks resource, render the slide prompt and cancel a login.

In-memory sessions check schemas and invoke selected tools with a synthetic
SDK client. The research cancellation test calls the registered tool directly.
"""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, McpError, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from notebooklm_mcp_server import AppContext, mcp
from tests.conftest import wait_for_login_pid


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


async def test_stdio_cancels_login_reading_input_and_answers_ping(tmp_path, synthetic_login_sdk):
    profile = tmp_path / "disposable"
    server = StdioServerParameters(
        command=sys.executable,
        # Only the child login should import the synthetic SDK from PYTHONPATH.
        args=["-I", str(Path(__file__).resolve().parents[1] / "notebooklm_mcp_server.py")],
        cwd=tmp_path,
        env={
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "NOTEBOOKLM_HOME": str(tmp_path / ".notebooklm"),
            "PYTHONPATH": synthetic_login_sdk,
            "TEST_LOGIN_READ_INPUT": "1",
        },
    )
    async with (
        stdio_client(server) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=10)) as session,
    ):
        await session.initialize()
        request_id = session._request_id
        login = asyncio.create_task(session.call_tool("create_profile", {"profile": str(profile)}))
        try:
            await wait_for_login_pid(profile)
            await session.send_notification(
                types.ClientNotification(
                    types.CancelledNotification(params=types.CancelledNotificationParams(requestId=request_id))
                )
            )
            with pytest.raises(McpError, match="Request cancelled"):
                await login
            assert isinstance(await session.send_ping(), types.EmptyResult)
        finally:
            login.cancel()
            await asyncio.gather(login, return_exceptions=True)
    assert not (profile / "unexpected-input").exists()
    if sys.platform == "win32":
        assert (profile / "confirmed").is_file(), "the login must receive Enter in its own console"


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

    async def test_generate_and_download_via_protocol(self, mcp_session, tmp_path):
        async with mcp_session as session:
            result = await session.call_tool(
                "generate_and_download",
                {
                    "notebook_id": "nb-proto",
                    "artifact_type": "slide_deck",
                    "output_path": str(tmp_path / "slides.pdf"),
                    "instructions": "Corporate style",
                },
            )
        text = result.content[0].text
        data = json.loads(text)
        assert data["success"] is True

    @pytest.mark.parametrize("late", [False, True], ids=["on_time", "after_deadline"])
    async def test_research_poll_deadline(self, late, mcp_session, mock_client, monkeypatch):
        loop = asyncio.get_running_loop()
        clock = loop.time
        source = {"title": "Requested source", "url": "https://example.org/source"}
        timeout = 30
        monkeypatch.setattr("notebooklm_mcp_server.RESEARCH_TIMEOUT", timeout)

        async def poll(notebook_id):
            assert clock() - started < timeout
            if late:
                # Advance the monotonic clock while the SDK owns the event-loop thread.
                monkeypatch.setattr(loop, "time", lambda: clock() + timeout)
            return {"task_id": "research-1", "status": "completed", "sources": [source]}

        mock_client.research.poll.side_effect = poll
        async with mcp_session as session:
            started = clock()
            result = await session.call_tool("research_and_import", {"notebook_id": "nb-proto", "query": "topic"})
        assert not result.isError
        mock_client.research.poll.assert_awaited_once_with("nb-proto")
        data = json.loads(result.content[0].text)
        if late:
            assert loop.time() >= started + timeout
            assert data.get("error") == "Research timed out"
            assert data["imported"] == 0
            mock_client.research.import_sources.assert_not_called()
        else:
            assert data["success"]
            assert data["imported"] == 1
            mock_client.research.import_sources.assert_awaited_once_with(
                "nb-proto", task_id="research-1", sources=[source]
            )

    async def test_cancel_at_poll_completion_skips_import(self, monkeypatch, mock_client, mock_ctx):
        loop = asyncio.get_running_loop()
        accepted = []

        def cancel_caller():
            accepted.append(caller.cancel())

        async def poll(notebook_id):
            loop.call_soon(cancel_caller)
            return {"task_id": "research-1", "status": "completed", "sources": [{"title": "Requested source"}]}

        mock_client.research.poll.side_effect = poll
        monkeypatch.setattr(mcp, "get_context", lambda: mock_ctx)
        caller = asyncio.create_task(
            mcp.call_tool("research_and_import", {"notebook_id": "nb-proto", "query": "topic"})
        )
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert accepted == [True]
        mock_client.research.import_sources.assert_not_called()

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
