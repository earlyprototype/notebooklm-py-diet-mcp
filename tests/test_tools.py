"""Mock-based tests for all diet MCP tool functions.

Each test verifies that the tool calls the correct SDK method with the
expected arguments and returns a well-structured result dict.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from notebooklm_mcp_server import (
    add_sources,
    ask_question,
    create_notebook,
    export_artifact,
    generate_and_download,
    get_account_info,
    list_artifacts,
    list_notebooks_tool,
    list_sources,
    research_and_import,
)

# ============================================================================
# NOTEBOOKS
# ============================================================================


class TestListNotebooks:
    async def test_returns_notebook_list(self, mock_ctx, mock_client):
        result = await list_notebooks_tool(mock_ctx)
        assert result["count"] == 2
        assert result["notebooks"][0]["id"] == "nb-1"
        mock_client.notebooks.list.assert_awaited()

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await list_notebooks_tool(mock_ctx)
        assert "error" in result


class TestCreateNotebook:
    async def test_creates_notebook(self, mock_ctx, mock_client):
        result = await create_notebook("Test Notebook", mock_ctx)
        assert result["id"] == "nb-new"
        assert result["success"] is True
        mock_client.notebooks.create.assert_awaited_once_with("Test Notebook")


# ============================================================================
# SOURCES
# ============================================================================


class TestListSources:
    async def test_returns_source_list(self, mock_ctx, mock_client):
        result = await list_sources("nb-1", ctx=mock_ctx)
        assert result["count"] == 2
        assert result["sources"][0]["id"] == "src-1"
        mock_client.sources.list.assert_awaited_once_with("nb-1")

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await list_sources("nb-1", ctx=mock_ctx)
        assert "error" in result


class TestAddSources:
    async def test_adds_multiple_sources(self, mock_ctx, mock_client):
        sources_json = json.dumps(
            [
                {"type": "url", "value": "https://example.com"},
                {"type": "text", "value": "Some text", "title": "My Notes"},
            ]
        )
        result = await add_sources("nb-1", sources_json, ctx=mock_ctx)
        assert result["total"] == 2
        assert result["succeeded"] == 2
        mock_client.sources.add_url.assert_awaited_once()
        mock_client.sources.add_text.assert_awaited_once()

    async def test_adds_file_source(self, mock_ctx, mock_client):
        sources_json = json.dumps([{"type": "file", "value": "/tmp/doc.pdf"}])
        result = await add_sources("nb-1", sources_json, ctx=mock_ctx)
        assert result["succeeded"] == 1
        mock_client.sources.add_file.assert_awaited_once()

    async def test_handles_unknown_source_type(self, mock_ctx, mock_client):
        sources_json = json.dumps([{"type": "unknown", "value": "data"}])
        result = await add_sources("nb-1", sources_json, ctx=mock_ctx)
        assert result["succeeded"] == 0
        assert "error" in result["results"][0]

    async def test_rejects_invalid_json(self, mock_ctx, mock_client):
        result = await add_sources("nb-1", "not valid json", ctx=mock_ctx)
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    async def test_rejects_non_array_json(self, mock_ctx, mock_client):
        result = await add_sources("nb-1", '{"type": "url"}', ctx=mock_ctx)
        assert "error" in result
        assert "JSON array" in result["error"]

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await add_sources("nb-1", "[]", ctx=mock_ctx)
        assert "error" in result


# ============================================================================
# CHAT
# ============================================================================


class TestAskQuestion:
    async def test_returns_answer(self, mock_ctx, mock_client):
        result = await ask_question("nb-1", "What are the key findings?", ctx=mock_ctx)
        assert result["answer"] == "The key findings are..."
        assert result["has_citations"] is True
        mock_client.chat.ask.assert_awaited_once()

    async def test_with_source_ids(self, mock_ctx, mock_client):
        result = await ask_question("nb-1", "Summarise", source_ids="src-1,src-2", ctx=mock_ctx)
        assert result["answer"] == "The key findings are..."

    async def test_configures_persona_before_asking(self, mock_ctx, mock_client):
        result = await ask_question(
            "nb-1",
            "What are the key trends?",
            persona="strategy analyst",
            ctx=mock_ctx,
        )
        assert result["answer"] == "The key findings are..."
        mock_client.chat.configure.assert_awaited_once()
        mock_client.chat.ask.assert_awaited_once()

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await ask_question("nb-1", "Test?", ctx=mock_ctx)
        assert "error" in result


# ============================================================================
# ARTIFACTS
# ============================================================================


class TestGenerateAndDownload:
    async def test_generates_and_downloads_report(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "report", "/tmp/out.pdf", ctx=mock_ctx)
        assert result["success"] is True
        assert result["artifact_type"] == "report"
        mock_client.artifacts.generate_report.assert_awaited_once()
        mock_client.artifacts.wait_for_completion.assert_awaited_once()
        mock_client.artifacts.download_report.assert_awaited_once()

    async def test_generates_and_downloads_audio(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "audio", "/tmp/out.wav", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.generate_audio.assert_awaited_once()
        mock_client.artifacts.download_audio.assert_awaited_once()

    async def test_generates_and_downloads_slide_deck(self, mock_ctx, mock_client):
        result = await generate_and_download(
            "nb-1",
            "slide_deck",
            "/tmp/out.pdf",
            instructions="Corporate style",
            ctx=mock_ctx,
        )
        assert result["success"] is True
        mock_client.artifacts.generate_slide_deck.assert_awaited_once()

    async def test_generates_and_downloads_quiz(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "quiz", "/tmp/out.json", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.generate_quiz.assert_awaited_once()

    async def test_generates_and_downloads_infographic(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "infographic", "/tmp/out.pdf", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.generate_infographic.assert_awaited_once()

    async def test_rejects_invalid_artifact_type(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "podcast", "/tmp/out.mp3", ctx=mock_ctx)
        assert "error" in result

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await generate_and_download("nb-1", "report", "/tmp/out.pdf", ctx=mock_ctx)
        assert "error" in result


class TestListArtifacts:
    async def test_lists_artifacts(self, mock_ctx, mock_client):
        result = await list_artifacts("nb-1", ctx=mock_ctx)
        assert result["count"] == 0
        mock_client.artifacts.list.assert_awaited_once()


class TestExportArtifact:
    async def test_exports_artifact(self, mock_ctx, mock_client, tmp_path):
        output = tmp_path / "out.pdf"
        result = await export_artifact("nb-1", "art-1", str(output), ctx=mock_ctx)
        assert result["success"] is True
        assert output.read_bytes() == b"exported-data"
        mock_client.artifacts.export.assert_awaited_once()


# ============================================================================
# RESEARCH
# ============================================================================


class TestResearchAndImport:
    @pytest.fixture(autouse=True)
    def skip_poll_delay(self, monkeypatch):
        monkeypatch.setattr("notebooklm_mcp_server.asyncio.sleep", AsyncMock())

    async def test_researches_and_imports(self, mock_ctx, mock_client):
        result = await research_and_import("nb-1", "quantum computing", ctx=mock_ctx)
        assert result["success"] is True
        assert result["query"] == "quantum computing"
        mock_client.research.start.assert_awaited_once()
        mock_client.research.import_sources.assert_awaited_once()

    async def test_times_out_without_importing_partial_results(self, mock_ctx, mock_client):
        mock_client.research.poll.return_value = SimpleNamespace(
            task_id="research-1", status="in_progress", results=["partial"]
        )

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert "timed out" in result["error"].lower()
        assert not result.get("success")
        assert mock_client.research.poll.await_count == 60
        mock_client.research.import_sources.assert_not_awaited()

    @pytest.mark.parametrize(
        ("response", "reason"),
        [
            ({"status": "error", "error": "Quota exceeded", "results": ["partial"]}, "Quota exceeded"),
            (SimpleNamespace(task_id="research-1", status="failed", sources=["partial"]), "failed"),
            ({"sources": ["partial"]}, "status"),
        ],
    )
    async def test_stops_on_poll_failure(self, mock_ctx, mock_client, response, reason):
        mock_client.research.poll.return_value = response

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert reason in result["error"]
        assert not result.get("success")
        mock_client.research.poll.assert_awaited_once()
        mock_client.research.import_sources.assert_not_awaited()

    async def test_imports_matching_sdk_sources_on_last_poll(self, mock_ctx, mock_client):
        sources = [{"url": "https://example.com/a", "title": "A"}, {"url": "https://example.com/b", "title": "B"}]
        mock_client.research.start.return_value = {"task_id": "research-1"}
        mock_client.research.poll.side_effect = [
            {"task_id": "other", "status": "completed", "sources": []},
            *[{"task_id": "research-1", "status": "in_progress"}] * 58,
            {"task_id": "research-1", "status": "completed", "sources": sources},
        ]

        result = await research_and_import("nb-1", "test", max_results=1, ctx=mock_ctx)

        assert result["success"] is True
        assert result["results_found"] == 2
        assert result["imported"] == 1
        mock_client.research.import_sources.assert_awaited_once_with("nb-1", task_id="research-1", sources=sources[:1])

    async def test_finds_completed_task_second_in_notebook_poll(self, mock_ctx, mock_client):
        newer = {"task_id": "newer", "status": "completed", "sources": [{"url": "https://example.com/newer"}]}
        sources = [{"url": "https://example.com/requested", "title": "Requested"}]
        requested = {"task_id": "research-1", "status": "completed", "sources": sources}
        mock_client.research.poll.return_value = {**newer, "tasks": [newer, requested]}

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert result["success"] is True
        assert result["imported"] == 1
        mock_client.research.poll.assert_awaited_once_with("nb-1")
        mock_client.research.import_sources.assert_awaited_once_with("nb-1", task_id="research-1", sources=sources)

    async def test_ignores_unidentified_notebook_completion(self, mock_ctx, mock_client):
        mock_client.research.poll.return_value = {"status": "completed", "sources": [{"url": "https://example.com"}]}

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert "timed out" in result["error"].lower()
        mock_client.research.import_sources.assert_not_awaited()

    async def test_completed_task_with_no_sources(self, mock_ctx, mock_client):
        mock_client.research.poll.return_value = {"task_id": "research-1", "status": "completed", "sources": []}

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert result["success"] is True
        assert result["results_found"] == result["imported"] == 0
        mock_client.research.import_sources.assert_not_awaited()

    @pytest.mark.parametrize(
        ("sources", "confirmed"),
        [
            pytest.param([{"url": "", "title": "Skipped"}], [], id="skipped"),
            pytest.param(
                [{"url": "https://example.com/a", "title": "A"}, {"url": "https://example.com/b", "title": "B"}],
                [{"id": "src-a", "title": "A"}],
                id="partly-confirmed",
            ),
        ],
    )
    async def test_reports_incomplete_import(self, mock_ctx, mock_client, sources, confirmed):
        mock_client.research.poll.return_value = {"task_id": "research-1", "status": "completed", "sources": sources}
        mock_client.research.import_sources.return_value = confirmed

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert result["imported"] == len(confirmed)
        assert result["results_found"] == len(sources)
        assert result["success"] is False
        assert "confirm" in result["error"].lower()
        assert result["import_details"] == confirmed

    async def test_polls_by_id_when_supported(self, mock_ctx, mock_client):
        source = SimpleNamespace(url="https://example.com", title="Example")

        async def poll(notebook_id, task_id):
            assert (notebook_id, task_id) == ("nb-1", "research-1")
            return SimpleNamespace(task_id=task_id, status="completed", sources=(source,))

        mock_client.research.poll = poll

        result = await research_and_import("nb-1", "test", ctx=mock_ctx)

        assert result["success"] is True
        mock_client.research.import_sources.assert_awaited_once_with("nb-1", task_id="research-1", sources=(source,))

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await research_and_import("nb-1", "test", ctx=mock_ctx)
        assert "error" in result


# ============================================================================
# ACCOUNT MANAGEMENT
# ============================================================================


class TestGetAccountInfo:
    async def test_returns_account_details(self, mock_ctx, app_context):
        result = await get_account_info(mock_ctx)
        assert result["current_account"] == "test"
        assert "available_profiles" in result


# ============================================================================
# SCHEMA VALIDATION -- prevents regression of complex-type parameters
# ============================================================================

SCALAR_TYPES = {"string", "integer", "number", "boolean"}
SAFE_COMPOUND = {"string", "integer", "number", "boolean", "null"}


class TestToolSchemas:
    """Verify that every registered tool uses only MCP-client-safe types.

    FastMCP converts Python type annotations to JSON Schema. Complex
    types such as list[dict] produce schemas that Cursor and other MCP
    clients cannot reliably invoke. All tool parameters should resolve
    to scalar JSON Schema types (string, integer, number, boolean) or
    simple unions thereof (e.g. string | null via anyOf).
    """

    def test_all_tool_parameters_are_scalar(self):
        from notebooklm_mcp_server import mcp as server

        violations = []
        for tool in server._tool_manager._tools.values():
            schema = tool.parameters
            props = schema.get("properties", {})
            for param_name, param_schema in props.items():
                if param_name == "ctx":
                    continue
                if not _is_safe_schema(param_schema):
                    violations.append(f"{tool.name}.{param_name}: {json.dumps(param_schema)}")

        assert not violations, "Tool parameters with complex types that will break MCP clients:\n" + "\n".join(
            violations
        )


def _is_safe_schema(schema: dict) -> bool:
    """Return True if a parameter schema uses only client-safe types."""
    if schema.get("type") in SCALAR_TYPES:
        return True
    if "anyOf" in schema:
        return all(opt.get("type") in SAFE_COMPOUND for opt in schema["anyOf"])
    return schema.get("type") == "null"
