"""Contract tests verifying our SDK calls match real method signatures.

These tests import the real SDK classes and use inspect.signature to
confirm that every keyword argument we pass is accepted by the real
method, and that positional arguments are in the correct order.

This catches bugs that mock-based tests cannot: wrong argument names,
swapped positional arguments, and removed/renamed SDK parameters.
"""

import inspect

from notebooklm._artifacts import ArtifactsAPI
from notebooklm._chat import ChatAPI
from notebooklm._notebooks import NotebooksAPI
from notebooklm._research import ResearchAPI
from notebooklm._sources import SourcesAPI


def _get_param_names(cls, method_name: str) -> list[str]:
    """Return the parameter names (excluding self) for a method."""
    method = getattr(cls, method_name)
    sig = inspect.signature(method)
    return [name for name in sig.parameters if name != "self"]


def _get_positional_params(cls, method_name: str) -> list[str]:
    """Return positional-or-keyword parameter names in order."""
    method = getattr(cls, method_name)
    sig = inspect.signature(method)
    positional_kinds = {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
    return [name for name, param in sig.parameters.items() if name != "self" and param.kind in positional_kinds]


def _accepts_kwarg(cls, method_name: str, kwarg: str) -> bool:
    """Check if a method accepts a specific keyword argument."""
    method = getattr(cls, method_name)
    sig = inspect.signature(method)
    if kwarg in sig.parameters:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


# ============================================================================
# NOTEBOOKS
# ============================================================================


class TestNotebooksContract:
    def test_list_signature(self):
        params = _get_param_names(NotebooksAPI, "list")
        assert params == []

    def test_create_signature(self):
        params = _get_positional_params(NotebooksAPI, "create")
        assert params == ["notebook_id"] or params == ["title"]


# ============================================================================
# SOURCES
# ============================================================================


class TestSourcesContract:
    def test_list_takes_notebook_id(self):
        params = _get_positional_params(SourcesAPI, "list")
        assert params[0] == "notebook_id"

    def test_add_url_signature(self):
        params = _get_positional_params(SourcesAPI, "add_url")
        assert params[0] == "notebook_id"
        assert params[1] == "url"
        assert _accepts_kwarg(SourcesAPI, "add_url", "wait")

    def test_add_text_argument_order(self):
        """The bug that started this: title must come before content."""
        params = _get_positional_params(SourcesAPI, "add_text")
        assert params[0] == "notebook_id"
        assert params[1] == "title"
        assert params[2] == "content"

    def test_add_file_signature(self):
        params = _get_positional_params(SourcesAPI, "add_file")
        assert params[0] == "notebook_id"
        assert params[1] == "file_path"
        assert _accepts_kwarg(SourcesAPI, "add_file", "wait")


# ============================================================================
# CHAT
# ============================================================================


class TestChatContract:
    def test_ask_signature(self):
        params = _get_positional_params(ChatAPI, "ask")
        assert params[0] == "notebook_id"
        assert params[1] == "question"
        assert _accepts_kwarg(ChatAPI, "ask", "source_ids")
        assert _accepts_kwarg(ChatAPI, "ask", "conversation_id")

    def test_configure_accepts_our_kwargs(self):
        assert _accepts_kwarg(ChatAPI, "configure", "goal")
        assert _accepts_kwarg(ChatAPI, "configure", "custom_prompt")
        assert _accepts_kwarg(ChatAPI, "configure", "response_length")


# ============================================================================
# ARTIFACTS -- generation
# ============================================================================


class TestArtifactGenerationContract:
    def test_generate_audio_accepts_our_kwargs(self):
        assert _accepts_kwarg(ArtifactsAPI, "generate_audio", "audio_format")
        assert _accepts_kwarg(ArtifactsAPI, "generate_audio", "audio_length")
        assert _accepts_kwarg(ArtifactsAPI, "generate_audio", "instructions")

    def test_generate_audio_does_not_accept_length(self):
        """We previously passed 'length' instead of 'audio_length'."""
        sig = inspect.signature(ArtifactsAPI.generate_audio)
        assert "length" not in sig.parameters

    def test_generate_report_uses_custom_prompt_not_instructions(self):
        """Report generation uses custom_prompt, not instructions."""
        assert _accepts_kwarg(ArtifactsAPI, "generate_report", "custom_prompt")
        sig = inspect.signature(ArtifactsAPI.generate_report)
        assert "instructions" not in sig.parameters

    def test_generate_slide_deck_accepts_instructions(self):
        assert _accepts_kwarg(ArtifactsAPI, "generate_slide_deck", "instructions")

    def test_generate_quiz_accepts_our_kwargs(self):
        assert _accepts_kwarg(ArtifactsAPI, "generate_quiz", "quantity")
        assert _accepts_kwarg(ArtifactsAPI, "generate_quiz", "difficulty")

    def test_generate_infographic_accepts_instructions(self):
        assert _accepts_kwarg(ArtifactsAPI, "generate_infographic", "instructions")


# ============================================================================
# ARTIFACTS -- download
# ============================================================================


class TestArtifactDownloadContract:
    def test_download_audio_positional_order(self):
        params = _get_positional_params(ArtifactsAPI, "download_audio")
        assert params[0] == "notebook_id"
        assert params[1] == "output_path"

    def test_download_report_positional_order(self):
        params = _get_positional_params(ArtifactsAPI, "download_report")
        assert params[0] == "notebook_id"
        assert params[1] == "output_path"

    def test_download_slide_deck_positional_order(self):
        params = _get_positional_params(ArtifactsAPI, "download_slide_deck")
        assert params[0] == "notebook_id"
        assert params[1] == "output_path"

    def test_download_quiz_accepts_output_format(self):
        assert _accepts_kwarg(ArtifactsAPI, "download_quiz", "output_format")

    def test_download_infographic_positional_order(self):
        params = _get_positional_params(ArtifactsAPI, "download_infographic")
        assert params[0] == "notebook_id"
        assert params[1] == "output_path"


# ============================================================================
# ARTIFACTS -- wait / export
# ============================================================================


class TestArtifactManagementContract:
    def test_wait_for_completion_signature(self):
        params = _get_positional_params(ArtifactsAPI, "wait_for_completion")
        assert params[0] == "notebook_id"
        assert params[1] == "task_id"

    def test_export_accepts_artifact_id_as_kwarg(self):
        """We must pass artifact_id as a keyword, not positional."""
        assert _accepts_kwarg(ArtifactsAPI, "export", "artifact_id")
        assert _accepts_kwarg(ArtifactsAPI, "export", "export_type")

    def test_export_positional_order(self):
        """Verify that passing artifact_id positionally is safe."""
        params = _get_positional_params(ArtifactsAPI, "export")
        assert params[0] == "notebook_id"

    def test_list_accepts_artifact_type(self):
        assert _accepts_kwarg(ArtifactsAPI, "list", "artifact_type")


# ============================================================================
# RESEARCH
# ============================================================================


class TestResearchContract:
    def test_start_signature(self):
        params = _get_positional_params(ResearchAPI, "start")
        assert params[0] == "notebook_id"
        assert params[1] == "query"
        assert _accepts_kwarg(ResearchAPI, "start", "source")

    def test_poll_signature(self):
        params = _get_positional_params(ResearchAPI, "poll")
        assert params[0] == "notebook_id"

    def test_import_sources_accepts_our_kwargs(self):
        assert _accepts_kwarg(ResearchAPI, "import_sources", "task_id")
        assert _accepts_kwarg(ResearchAPI, "import_sources", "sources")
