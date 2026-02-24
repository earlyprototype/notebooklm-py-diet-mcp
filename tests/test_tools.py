"""Mock-based tests for all diet MCP tool functions.

Each test verifies that the tool calls the correct SDK method with the
expected arguments and returns a well-structured result dict.
"""

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
        sources = [
            {"type": "url", "value": "https://example.com"},
            {"type": "text", "value": "Some text", "title": "My Notes"},
        ]
        result = await add_sources("nb-1", sources, ctx=mock_ctx)
        assert result["total"] == 2
        assert result["succeeded"] == 2
        mock_client.sources.add_url.assert_awaited_once()
        mock_client.sources.add_text.assert_awaited_once()

    async def test_adds_file_source(self, mock_ctx, mock_client):
        sources = [{"type": "file", "value": "/tmp/doc.pdf"}]
        result = await add_sources("nb-1", sources, ctx=mock_ctx)
        assert result["succeeded"] == 1
        mock_client.sources.add_file.assert_awaited_once()

    async def test_handles_unknown_source_type(self, mock_ctx, mock_client):
        sources = [{"type": "unknown", "value": "data"}]
        result = await add_sources("nb-1", sources, ctx=mock_ctx)
        assert result["succeeded"] == 0
        assert "error" in result["results"][0]

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await add_sources("nb-1", [], ctx=mock_ctx)
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
        result = await ask_question("nb-1", "Summarise", source_ids=["src-1"], ctx=mock_ctx)
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
    async def test_exports_artifact(self, mock_ctx, mock_client):
        result = await export_artifact("nb-1", "art-1", "/tmp/out.pdf", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.export.assert_awaited_once()


# ============================================================================
# RESEARCH
# ============================================================================


class TestResearchAndImport:
    async def test_researches_and_imports(self, mock_ctx, mock_client):
        result = await research_and_import("nb-1", "quantum computing", ctx=mock_ctx)
        assert result["success"] is True
        assert result["query"] == "quantum computing"
        mock_client.research.start.assert_awaited_once()
        mock_client.research.import_sources.assert_awaited_once()

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
