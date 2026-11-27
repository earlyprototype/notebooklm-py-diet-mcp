"""Mock-based tests for all diet MCP tool functions.

Each test verifies that the tool calls the correct SDK method with the
expected arguments and returns a well-structured result dict.
"""

import asyncio
import json
import logging
import os
import wave
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import av
import fitz
import httpx
import pytest
from mcp.server.fastmcp.utilities.logging import configure_logging
from notebooklm import ArtifactType, AudioFormat, AudioLength, GenerationStatus, QuizDifficulty, QuizQuantity

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
    mcp,
    research_and_import,
    switch_account,
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
    async def test_generates_and_downloads_report(self, mock_ctx, mock_client, tmp_path):
        def write_report(notebook_id, output_path, artifact_id):
            Path(output_path).write_text(f"# {artifact_id}", encoding="utf-8")

        output = tmp_path / "report.md"
        mock_client.artifacts.download_report.side_effect = write_report
        result = await generate_and_download("nb-1", "report", str(output), ctx=mock_ctx)
        assert result["success"] is True
        assert result["artifact_type"] == "report"
        mock_client.artifacts.generate_report.assert_awaited_once()
        mock_client.artifacts.wait_for_completion.assert_awaited_once()
        mock_client.artifacts.download_report.assert_awaited_once_with("nb-1", ANY, artifact_id="task-report")
        assert output.read_text(encoding="utf-8") == "# task-report"

    async def test_generates_and_downloads_audio(self, mock_ctx, mock_client, tmp_path):
        def download_wave(notebook_id, output_path, artifact_id):
            with wave.open(output_path, "wb") as audio:
                audio.setparams((1, 1, 8000, 0, "NONE", ""))
                audio.writeframes(b"\x80" * 80)

        mock_client.artifacts.download_audio.side_effect = download_wave
        output = tmp_path / "audio.wav"
        result = await generate_and_download("nb-1", "audio", str(output), ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.generate_audio.assert_awaited_once_with(
            "nb-1", audio_format=AudioFormat.DEEP_DIVE, audio_length=AudioLength.DEFAULT
        )
        assert mock_client.artifacts.download_audio.await_args.kwargs["artifact_id"] == "task-audio"
        with wave.open(str(output)) as audio:
            assert audio.getnframes() == 80
        wrong_format = tmp_path / "audio.mp3"
        mismatch = await generate_and_download("nb-1", "audio", str(wrong_format), ctx=mock_ctx)
        assert "wav" in mismatch["error"]
        assert not wrong_format.exists()

    async def test_generates_and_downloads_slide_deck(self, mock_ctx, mock_client, tmp_path):
        result = await generate_and_download(
            "nb-1",
            "slide_deck",
            str(tmp_path / "slides.pdf"),
            instructions="Corporate style",
            ctx=mock_ctx,
        )
        assert result["success"] is True
        mock_client.artifacts.generate_slide_deck.assert_awaited_once()

    async def test_generates_and_downloads_quiz(self, mock_ctx, mock_client, tmp_path):
        def write_quiz(notebook_id, output_path, artifact_id, output_format):
            Path(output_path).write_text(json.dumps({"title": artifact_id, "questions": []}), encoding="utf-8")

        mock_client.artifacts.download_quiz.side_effect = write_quiz
        output = tmp_path / "quiz.json"
        result = await generate_and_download("nb-1", "quiz", str(output), ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.generate_quiz.assert_awaited_once_with(
            "nb-1", quantity=QuizQuantity.STANDARD, difficulty=QuizDifficulty.MEDIUM
        )
        mock_client.artifacts.download_quiz.assert_awaited_once_with(
            "nb-1", ANY, artifact_id="task-quiz", output_format="json"
        )
        assert json.loads(output.read_text(encoding="utf-8"))["title"] == "task-quiz"

    async def test_generates_and_downloads_infographic(self, mock_ctx, mock_client, tmp_path):
        def download_png(notebook_id, output_path, artifact_id):
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), 0)
            pixmap.clear_with(255)
            pixmap.save(output_path)
            return output_path

        mock_client.artifacts.download_infographic.side_effect = download_png
        output = tmp_path / "out.png"
        result = await generate_and_download("nb-1", "infographic", str(output), ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.generate_infographic.assert_awaited_once()
        assert mock_client.artifacts.download_infographic.await_args.kwargs["artifact_id"] == "task-infographic"
        assert fitz.Pixmap(str(output)).width == 4

    async def test_failed_generation_does_not_download(self, mock_ctx, mock_client):
        mock_client.artifacts.wait_for_completion.side_effect = None
        mock_client.artifacts.wait_for_completion.return_value = GenerationStatus(
            "task-report", "failed", error="quota"
        )
        result = await generate_and_download("nb-1", "report", "output.md", ctx=mock_ctx)
        assert "quota" in result["error"]
        mock_client.artifacts.download_report.assert_not_awaited()

    async def test_failed_generation_keeps_sdk_status_details_private(self, mock_ctx, mock_client):
        mock_client.artifacts.generate_report.return_value = GenerationStatus(
            "task-report", "failed", error="download?signature=synthetic-marker"
        )
        result = await generate_and_download("nb-1", "report", "output.md", ctx=mock_ctx)
        assert result["artifact_id"] == "task-report"
        assert "generate_and_download" in result["error"]
        assert "synthetic-marker" not in json.dumps(result)

    async def test_invalid_quiz_format_does_not_start_generation(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "quiz", "out.pdf", quiz_output_format="pdf", ctx=mock_ctx)
        assert "format" in result["error"].lower()
        mock_client.artifacts.generate_quiz.assert_not_awaited()

    async def test_unknown_audio_format(self, mock_ctx, mock_client):
        result = await generate_and_download("nb-1", "audio", "out.m4a", audio_format="lecture", ctx=mock_ctx)
        assert "audio" in result["error"].lower()
        assert "lecture" in result["error"].lower()
        mock_client.artifacts.generate_audio.assert_not_awaited()

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
    async def test_downloads_selected_pdf(self, mock_ctx, mock_client, tmp_path):
        decks = {
            "earlier": SimpleNamespace(id="earlier", kind=ArtifactType.SLIDE_DECK),
            "task-slide_deck": SimpleNamespace(id="task-slide_deck", kind=ArtifactType.SLIDE_DECK),
        }

        def download(notebook_id, output_path, artifact_id=None):
            deck = decks[artifact_id or "earlier"]
            with fitz.open() as doc:
                doc.new_page().insert_text((32, 32), deck.id)
                doc.save(output_path)
            return output_path

        artifacts = mock_client.artifacts
        artifacts.get.side_effect = lambda notebook_id, artifact_id: decks.get(artifact_id)
        artifacts.download_slide_deck.side_effect = download
        generated = tmp_path / "generated.pdf"
        exported = tmp_path / "exported.pdf"
        gen_result = await generate_and_download("nb-1", "slide_deck", str(generated), ctx=mock_ctx)
        assert gen_result["success"], gen_result
        export_result = await export_artifact("nb-1", "earlier", str(exported), export_format="pdf", ctx=mock_ctx)
        assert export_result["success"], export_result
        assert [call.kwargs["artifact_id"] for call in artifacts.download_slide_deck.await_args_list] == [
            "task-slide_deck",
            "earlier",
        ]
        for path, expected in [(generated, "task-slide_deck"), (exported, "earlier")]:
            with fitz.open(path) as doc:
                assert doc.is_pdf
                assert doc[0].get_text().strip() == expected
        artifacts.export.assert_not_awaited()

    @pytest.mark.parametrize(("kind", "path", "fmt"), [("slide_deck", "out.pdf", "csv"), ("report", "out.pdf", "")])
    async def test_unsupported_format_preserves_file(self, mock_ctx, mock_client, tmp_path, kind, path, fmt):
        mock_client.artifacts.get.return_value = SimpleNamespace(id="item", kind=ArtifactType(kind))
        output = tmp_path / path
        output.write_text("existing", encoding="utf-8")
        result = await export_artifact("nb-1", "item", str(output), fmt, ctx=mock_ctx)
        assert "format" in result["error"].lower()
        assert output.read_text(encoding="utf-8") == "existing"

    async def test_missing_artifact(self, mock_ctx, mock_client, tmp_path):
        mock_client.artifacts.get.return_value = None
        output = tmp_path / "out.json"
        result = await export_artifact("nb-1", "absent", str(output), ctx=mock_ctx)
        assert "absent" in result["error"]
        assert not output.exists()

    async def test_quiz_markdown_export(self, mock_ctx, mock_client, tmp_path):
        def write_quiz(notebook_id, output_path, artifact_id, output_format):
            Path(output_path).write_text(f"# {artifact_id}", encoding="utf-8")

        output = tmp_path / "review.md"
        mock_client.artifacts.get.return_value = SimpleNamespace(id="quiz-id", kind=ArtifactType.QUIZ)
        mock_client.artifacts.download_quiz.side_effect = write_quiz
        result = await export_artifact("nb-1", "quiz-id", str(output), ctx=mock_ctx)
        assert result["success"]
        mock_client.artifacts.download_quiz.assert_awaited_once_with(
            "nb-1", ANY, artifact_id="quiz-id", output_format="markdown"
        )
        assert output.read_text(encoding="utf-8") == "# quiz-id"

    async def test_text_export_encoding_failure_preserves_file(self, mock_ctx, mock_client, tmp_path):
        def write_report(notebook_id, output_path, artifact_id):
            Path(output_path).write_text("Invalid surrogate: \ud800", encoding="utf-8")

        mock_client.artifacts.get.return_value = SimpleNamespace(id="report", kind=ArtifactType.REPORT)
        mock_client.artifacts.download_report.side_effect = write_report
        output = tmp_path / "report.md"
        output.write_bytes(b"# Original report\n")
        result = await export_artifact("nb-1", "report", str(output), ctx=mock_ctx)
        assert "UnicodeEncodeError" in result["error"]
        assert output.read_bytes() == b"# Original report\n"
        assert list(tmp_path.iterdir()) == [output]


def encoded_media(container_format, codec):
    buffer = BytesIO()
    with av.open(buffer, mode="w", format=container_format) as container:
        if codec == "mpeg4":
            stream = container.add_stream(codec, rate=25)
            stream.width = stream.height = 16
            frame = av.VideoFrame(16, 16, "yuv420p")
        else:
            stream = container.add_stream(codec, rate=44100)
            frame = av.AudioFrame(format="s16", layout="mono", samples=1024)
            frame.sample_rate = 44100
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for _ in range(1 if codec == "mpeg4" else 8):
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("kind", "fmt", "payload", "valid"),
    [
        ("infographic", "png", "jpeg", False),
        ("infographic", "png", "png", True),
        ("slide_deck", "pdf", "empty", False),
        ("slide_deck", "pdf", "truncated_pdf", False),
        ("audio", "mp3", "adts", False),
        ("audio", "mp3", "mp3", True),
        ("audio", "m4a", "m4a", True),
        ("audio", "m4a", "mp4", False),
        ("video", "mp4", "m4a", False),
        ("video", "mp4", "mp4", True),
    ],
)
async def test_binary_export_validates_before_replace(mock_ctx, mock_client, tmp_path, kind, fmt, payload, valid):
    if payload in ("jpeg", "png"):
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), 0)
        pixmap.clear_with(255)
        data = pixmap.tobytes(payload)
    elif payload in ("empty", "truncated_pdf"):
        data = b"" if payload == "empty" else b"%PDF-1.7\n"
    else:
        container_format, codec = {
            "mp3": ("mp3", "libmp3lame"),
            "m4a": ("mp4", "aac"),
            "mp4": ("mp4", "mpeg4"),
            "adts": ("adts", "aac"),
        }[payload]
        data = encoded_media(container_format, codec)
    mock_client.artifacts.get.return_value = SimpleNamespace(id="item", kind=ArtifactType(kind))
    download = getattr(mock_client.artifacts, f"download_{kind}")
    download.side_effect = lambda nb, path, artifact_id: Path(path).write_bytes(data)
    output = tmp_path / f"existing.{fmt}"
    output.write_bytes(b"previous contents")
    result = await export_artifact("nb-1", "item", str(output), ctx=mock_ctx)
    if valid:
        assert result["success"], result
        assert output.read_bytes() == data
    else:
        assert fmt in result["error"].lower()
        if payload == "adts":
            assert "AAC" in result["error"]
        assert output.read_bytes() == b"previous contents"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("operation", ["generate_and_download", "export_artifact"])
async def test_artifact_http_failure_preserves_file_and_omits_url(
    mock_ctx, mock_client, tmp_path, operation, capsys, caplog
):
    async def failed_download(notebook_id, output_path, artifact_id):
        Path(output_path).write_bytes(b"partial")
        transport = httpx.MockTransport(lambda request: httpx.Response(403))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.invalid/download?signature=synthetic-marker")
            response.raise_for_status()

    mock_client.artifacts.download_slide_deck.side_effect = failed_download
    output = tmp_path / "slides.pdf"
    output.write_bytes(b"existing")
    # Apply FastMCP's normal stderr configuration without pytest's root handlers.
    with caplog.at_level(mcp.settings.log_level), patch.object(logging.getLogger(), "handlers", []):
        configure_logging(mcp.settings.log_level)
        if operation == "generate_and_download":
            expected_id = "task-slide_deck"
            result = await generate_and_download("nb-1", "slide_deck", str(output), ctx=mock_ctx)
        else:
            expected_id = "older"
            mock_client.artifacts.get.return_value = SimpleNamespace(id=expected_id, kind=ArtifactType.SLIDE_DECK)
            result = await export_artifact("nb-1", expected_id, str(output), ctx=mock_ctx)
    stderr = capsys.readouterr().err
    assert "synthetic-marker" not in stderr
    assert "example.invalid" not in stderr
    assert "synthetic-marker" not in json.dumps(result)
    assert "example.invalid" not in json.dumps(result)
    assert result["artifact_id"] == expected_id
    assert operation in result["error"]
    assert "403" in result["error"]
    assert output.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [output]


# ============================================================================
# RESEARCH
# ============================================================================


class TestResearchAndImport:
    @pytest.fixture(autouse=True)
    def skip_poll_delay(self, monkeypatch):
        monkeypatch.setattr("notebooklm_mcp_server.asyncio.sleep", AsyncMock())

    async def test_researches_and_imports(self, mock_ctx, mock_client):
        completed = mock_client.research.poll.return_value
        mock_client.research.poll.side_effect = [{"task_id": "research-1", "status": "in_progress"}, completed]
        result = await research_and_import("nb-1", "quantum computing", max_results=1, ctx=mock_ctx)
        assert result["success"] is True
        assert result["query"] == "quantum computing"
        mock_client.research.start.assert_awaited_once()
        assert result["results_found"] == 3
        assert result["imported"] == 1
        mock_client.research.import_sources.assert_awaited_once_with(
            "nb-1", task_id="research-1", sources=completed["sources"][:1]
        )

    @pytest.mark.parametrize("started", [None, {}])
    async def test_no_started_task(self, mock_ctx, mock_client, started):
        mock_client.research.start.return_value = started
        result = await research_and_import("nb-1", "topic", ctx=mock_ctx)
        assert "task" in result["error"].lower()
        assert result["imported"] == 0
        mock_client.research.poll.assert_not_awaited()
        mock_client.research.import_sources.assert_not_awaited()

    async def test_typed_task_polls_requested_id(self, mock_ctx, mock_client):
        source = SimpleNamespace(url="https://example.org/current", title="Current")
        mock_client.research.start.return_value = SimpleNamespace(task_id="requested")

        async def poll(notebook_id, task_id=None):
            assert notebook_id == "nb-1"
            assert task_id == "requested"
            return SimpleNamespace(task_id=task_id, status="completed", sources=(source,))

        mock_client.research.poll = poll
        result = await research_and_import("nb-1", "topic", ctx=mock_ctx)
        assert result["success"], result
        mock_client.research.import_sources.assert_awaited_once_with("nb-1", task_id="requested", sources=(source,))

    async def test_profile_switch_after_poll_aborts_import(self, mock_ctx, mock_client, app_context):
        other_client = AsyncMock()

        async def switch_before_import(message):
            if message.startswith("Importing"):
                app_context.client = other_client
                app_context.profile = "other"

        mock_ctx.info.side_effect = switch_before_import
        result = await research_and_import("nb-1", "topic", ctx=mock_ctx)
        mock_client.research.poll.assert_awaited_once()
        mock_client.research.import_sources.assert_not_awaited()
        other_client.research.import_sources.assert_not_awaited()
        assert "profile" in result["error"].lower()
        assert result["imported"] == 0

    async def test_profile_switch_during_authentication_aborts_start(self, mock_ctx, mock_client, app_context):
        other_client = AsyncMock()

        async def switch_during_check():
            app_context.profile = "other"
            app_context.client = other_client
            return []

        mock_client.notebooks.list.side_effect = switch_during_check
        result = await research_and_import("nb-1", "topic", ctx=mock_ctx)
        for client in (mock_client, other_client):
            client.research.start.assert_not_awaited()
            client.research.import_sources.assert_not_awaited()
        assert "profile" in result["error"].lower()
        assert result["imported"] == 0

    async def test_profile_switch_during_login_keeps_new_account(self, mock_ctx, mock_client, app_context, monkeypatch):
        app_context.client = None
        other_client = AsyncMock()

        async def finish_login(profile):
            app_context.profile = "other"
            app_context.client = other_client
            return mock_client

        monkeypatch.setattr("notebooklm_mcp_server._run_login", AsyncMock(return_value=True))
        monkeypatch.setattr("notebooklm_mcp_server._create_client", finish_login)
        result = await research_and_import("nb-1", "topic", ctx=mock_ctx)
        assert "error" in result
        assert app_context.client is other_client
        mock_client.__aexit__.assert_awaited_once()
        for client in (mock_client, other_client):
            client.research.start.assert_not_awaited()
            client.research.import_sources.assert_not_awaited()

    async def test_login_for_intended_profile_can_start_research(self, mock_ctx, mock_client, app_context, monkeypatch):
        app_context.client = None
        monkeypatch.setattr("notebooklm_mcp_server._run_login", AsyncMock(return_value=True))
        monkeypatch.setattr("notebooklm_mcp_server._create_client", AsyncMock(return_value=mock_client))
        result = await research_and_import("nb-1", "topic", max_results=1, ctx=mock_ctx)
        assert result["imported"] == 1
        mock_client.research.start.assert_awaited_once()

    async def test_partial_acknowledgement(self, mock_ctx, mock_client):
        result = await research_and_import("nb-1", "topic", max_results=2, ctx=mock_ctx)
        mock_client.research.import_sources.assert_awaited_once_with(
            "nb-1", task_id="research-1", sources=mock_client.research.poll.return_value["sources"][:2]
        )
        assert result["success"]
        assert result["submitted"] == 2
        assert result["acknowledged"] == 1
        assert result["imported"] is None

    @pytest.mark.parametrize(
        ("poll", "error"),
        [
            ({"status": "no_research"}, "research-1"),
            (
                {
                    "status": "completed",
                    "task_id": "other",
                    "sources": [{"title": "Wrong", "url": "https://example.org"}],
                },
                "other",
            ),
            ({"status": "in_progress", "task_id": "research-1"}, "timed out"),
        ],
    )
    async def test_unsuccessful_poll_imports_nothing(self, mock_ctx, mock_client, poll, error):
        mock_client.research.poll.return_value = poll
        result = await research_and_import("nb-1", "topic", ctx=mock_ctx)
        assert error in result["error"]
        assert result["imported"] == 0
        mock_client.research.import_sources.assert_not_awaited()

    async def test_stalled_poll_respects_deadline(self, mock_ctx, mock_client, monkeypatch):
        monkeypatch.setattr("notebooklm_mcp_server.RESEARCH_TIMEOUT", 0.05)

        async def stalled_poll(notebook_id):
            await asyncio.Event().wait()

        mock_client.research.poll.side_effect = stalled_poll
        result = await asyncio.wait_for(research_and_import("nb-1", "topic", ctx=mock_ctx), timeout=2)
        assert "timed out" in result["error"]
        assert result["imported"] == 0
        mock_client.research.import_sources.assert_not_awaited()

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


@pytest.mark.parametrize("failure", ["create", "persist", None])
async def test_account_switch_transaction(failure, mock_ctx, mock_client, app_context, monkeypatch, tmp_path):
    previous_home = str(tmp_path / ".notebooklm-test")
    monkeypatch.setenv("NOTEBOOKLM_HOME", previous_home)
    active = tmp_path / "active.json"
    active.write_text('{"active": "test"}', encoding="utf-8")
    monkeypatch.setattr("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", active)
    target = tmp_path / ".notebooklm-work"
    target.mkdir()
    (target / "storage_state.json").touch()
    candidate = AsyncMock()
    factory = AsyncMock(return_value=candidate)
    monkeypatch.setattr("notebooklm_mcp_server.NotebookLMClient.from_storage", factory)
    if failure == "create":
        factory.side_effect = ValueError("Synthetic client failure")
    elif failure == "persist":
        monkeypatch.setattr("notebooklm_mcp_server._write_active_profile", Mock(side_effect=OSError("Disk full")))

    if failure:
        with pytest.raises(ValueError if failure == "create" else OSError):
            await switch_account("work", mock_ctx)
        assert os.environ["NOTEBOOKLM_HOME"] == previous_home
        assert app_context.profile == json.loads(active.read_text(encoding="utf-8"))["active"] == "test"
        assert app_context.client is mock_client
        mock_client.__aexit__.assert_not_awaited()
        if failure == "persist":
            candidate.__aexit__.assert_awaited_once()
    else:
        result = await switch_account("work", mock_ctx)
        assert result["success"]
        assert os.environ["NOTEBOOKLM_HOME"] == str(target)
        assert app_context.profile == json.loads(active.read_text(encoding="utf-8"))["active"] == "work"
        assert app_context.client is candidate
        mock_client.__aexit__.assert_awaited_once()
        factory.assert_awaited_once_with(path=str(target / "storage_state.json"))


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
