"""Shared fixtures and mocks for the notebooklm-py-mcp test suite.

All tests are CI-safe: no Google credentials or network access required.
"""

import asyncio
import shutil
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec
from zipfile import is_zipfile

import notebooklm
import pytest
from notebooklm import GenerationStatus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(params=["installed", "non_zip"] if sys.platform == "win32" else ["installed"])
def synthetic_login_sdk(request, tmp_path, monkeypatch):
    from notebooklm_mcp_server import _find_cli

    cli = _find_cli()
    assert cli is not None, "the test environment needs the SDK's installed notebooklm launcher"
    fixture_sdk = Path(__file__).parent / "fixtures" / "login_sdk"
    if sys.platform == "win32" and (request.param == "non_zip" or not is_zipfile(cli)):
        # The isolated fallback ignores PYTHONPATH. Install the synthetic CLI
        # in a disposable venv where the stdio server can still load the SDK.
        environment = tmp_path / "login-venv"
        venv.EnvBuilder().create(environment)
        packages = environment / "Lib" / "site-packages"
        sdk = Path(notebooklm.__file__).parent
        shutil.copytree(sdk, packages / "notebooklm", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(fixture_sdk / "notebooklm" / "notebooklm_cli.py", packages / "notebooklm" / "notebooklm_cli.py")
        (packages / "test_dependencies.pth").write_text(
            f"import site; site.addsitedir({str(sdk.parent)!r})\n", encoding="utf-8"
        )
        (environment / "Scripts" / "notebooklm.exe").touch()
        monkeypatch.setattr(sys, "executable", str(environment / "Scripts" / "python.exe"))
    return str(fixture_sdk)


async def wait_for_login_pid(profile):
    async def read_pid():
        marker = profile / "login.pid"
        while not marker.exists():
            await asyncio.sleep(0.01)
        return int(marker.read_text(encoding="ascii"))

    return await asyncio.wait_for(read_pid(), timeout=10)


@dataclass
class FakeNotebook:
    id: str
    title: str


@dataclass
class FakeSource:
    id: str
    title: str


@dataclass
class FakeAnswer:
    answer: str
    citations: list


@dataclass
class FakeNote:
    id: str
    title: str
    content: str


@dataclass
class FakeSharingStatus:
    is_public: bool
    view_level: str
    users: list


def make_mock_client():
    """Build a fully-mocked NotebookLMClient with all API modules."""
    client = AsyncMock()

    # NotebooksAPI
    client.notebooks.list = AsyncMock(
        return_value=[
            FakeNotebook(id="nb-1", title="Research"),
            FakeNotebook(id="nb-2", title="Strategy"),
        ]
    )
    client.notebooks.create = AsyncMock(return_value=FakeNotebook(id="nb-new", title="New Notebook"))
    client.notebooks.get = AsyncMock(return_value=FakeNotebook(id="nb-1", title="Research"))
    client.notebooks.delete = AsyncMock(return_value=None)
    client.notebooks.rename = AsyncMock(return_value=FakeNotebook(id="nb-1", title="Renamed"))
    client.notebooks.get_description = AsyncMock(
        return_value=SimpleNamespace(description="AI summary of notebook", suggested_topics=["topic1", "topic2"])
    )
    client.notebooks.get_summary = AsyncMock(return_value="Summary text of the notebook")
    client.notebooks.share = AsyncMock(return_value={"shared": True})
    client.notebooks.remove_from_recent = AsyncMock(return_value=None)

    # SourcesAPI
    client.sources.list = AsyncMock(
        return_value=[
            FakeSource(id="src-1", title="Wikipedia"),
            FakeSource(id="src-2", title="Research Paper"),
        ]
    )
    client.sources.get = AsyncMock(return_value=FakeSource(id="src-1", title="Wikipedia"))
    client.sources.get_fulltext = AsyncMock(return_value=SimpleNamespace(content="Full text content"))
    client.sources.get_guide = AsyncMock(
        return_value=SimpleNamespace(summary="Source guide summary", keywords=["key1", "key2"])
    )
    client.sources.add_url = AsyncMock(return_value=FakeSource(id="src-new", title="Added URL"))
    client.sources.add_text = AsyncMock(return_value=FakeSource(id="src-text", title="Added Text"))
    client.sources.add_youtube = AsyncMock(return_value=FakeSource(id="src-yt", title="YouTube Video"))
    client.sources.add_file = AsyncMock(return_value=FakeSource(id="src-file", title="Uploaded File"))
    client.sources.add_drive = AsyncMock(return_value=FakeSource(id="src-drive", title="Drive File"))
    client.sources.rename = AsyncMock(return_value=FakeSource(id="src-1", title="Renamed Source"))
    client.sources.refresh = AsyncMock(return_value=FakeSource(id="src-1", title="Refreshed Source"))
    client.sources.delete = AsyncMock(return_value=None)
    client.sources.check_freshness = AsyncMock(return_value=True)

    # ChatAPI
    client.chat.ask = AsyncMock(return_value=FakeAnswer(answer="The key findings are...", citations=["source1"]))
    client.chat.configure = AsyncMock(return_value=None)
    client.chat.get_history = AsyncMock(
        return_value=[
            SimpleNamespace(role="user", content="What is this?"),
            SimpleNamespace(role="assistant", content="This is the answer."),
        ]
    )

    # ArtifactsAPI
    for artifact in (
        "audio",
        "video",
        "report",
        "flashcards",
        "slide_deck",
        "infographic",
        "data_table",
        "mind_map",
        "quiz",
    ):
        setattr(
            client.artifacts,
            f"generate_{artifact}",
            AsyncMock(return_value=GenerationStatus(f"task-{artifact}", "pending")),
        )
        setattr(client.artifacts, f"download_{artifact}", AsyncMock(return_value=None))

    client.artifacts.wait_for_completion = AsyncMock(
        side_effect=lambda nb, task_id: GenerationStatus(task_id, "completed")
    )

    def download_deck(notebook_id, output_path, artifact_id):
        import fitz

        with fitz.open() as doc:
            doc.new_page().insert_text((32, 32), artifact_id)
            doc.save(output_path)
        return output_path

    client.artifacts.download_slide_deck.side_effect = download_deck
    client.artifacts.list = AsyncMock(return_value=[])
    client.artifacts.get = AsyncMock(return_value=SimpleNamespace(id="art-1", title="Artifact"))
    client.artifacts.delete = AsyncMock(return_value=None)
    client.artifacts.rename = AsyncMock(return_value=SimpleNamespace(id="art-1", title="Renamed"))
    client.artifacts.suggest_reports = AsyncMock(
        return_value=[
            SimpleNamespace(
                title="Executive Summary", description="High-level overview", prompt="Summarise", audience_level=2
            ),
            SimpleNamespace(
                title="Technical Deep Dive",
                description="Detailed analysis",
                prompt="Analyse in depth",
                audience_level=3,
            ),
        ]
    )

    # ResearchAPI
    async def legacy_poll(notebook_id):
        """SDK 0.3.2 polls the notebook's most recent research task."""

    client.research.start = AsyncMock(return_value={"task_id": "research-1", "report_id": None})
    client.research.poll = create_autospec(
        legacy_poll,
        return_value={
            "status": "completed",
            "task_id": "research-1",
            "sources": [{"url": f"https://example.org/{i}", "title": f"Source {i}"} for i in range(3)],
        },
    )
    client.research.import_sources = AsyncMock(return_value=[{"id": "src-0", "title": "Source 0"}])

    # NotesAPI
    client.notes.list = AsyncMock(
        return_value=[
            FakeNote(id="note-1", title="Note 1", content="Content 1"),
        ]
    )
    client.notes.create = AsyncMock(return_value=FakeNote(id="note-new", title="New Note", content=""))
    client.notes.get = AsyncMock(return_value=FakeNote(id="note-1", title="Note 1", content="Content 1"))
    client.notes.update = AsyncMock(return_value=FakeNote(id="note-1", title="Updated", content="New content"))
    client.notes.delete = AsyncMock(return_value=None)
    client.notes.list_mind_maps = AsyncMock(return_value=[])
    client.notes.delete_mind_map = AsyncMock(return_value=None)

    # SharingAPI
    client.sharing.get_status = AsyncMock(
        return_value=FakeSharingStatus(is_public=False, view_level="private", users=[])
    )
    client.sharing.set_public = AsyncMock(return_value=None)
    client.sharing.set_view_level = AsyncMock(return_value=None)
    client.sharing.add_user = AsyncMock(return_value=None)
    client.sharing.update_user = AsyncMock(return_value=None)
    client.sharing.remove_user = AsyncMock(return_value=None)

    # SettingsAPI
    client.settings.get_output_language = AsyncMock(return_value="en")
    client.settings.set_output_language = AsyncMock(return_value=None)

    client.close = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_client():
    """Provide a fully-mocked NotebookLMClient."""
    return make_mock_client()


@pytest.fixture
def app_context(mock_client):
    """Provide an AppContext with a mocked client, ready for tool tests."""
    from notebooklm_mcp_server import AppContext

    return AppContext(client=mock_client, profile="test")


@pytest.fixture
def mock_ctx(app_context, monkeypatch, tmp_path):
    """Provide a mocked MCP Context wired to app_context."""
    monkeypatch.setattr("notebooklm_mcp_server._run_login", AsyncMock(return_value=False))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_context
    ctx.info = AsyncMock()
    ctx.error = AsyncMock()
    ctx.report_progress = AsyncMock()
    return ctx
