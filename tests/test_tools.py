"""Mock-based tests for all MCP tool functions.

Each test verifies that the tool calls the correct SDK method with the
expected arguments and returns a well-structured result dict.
"""

from notebooklm_mcp_server import (
    add_shared_user,
    add_source_drive,
    add_source_file,
    add_source_text,
    # Sources
    add_source_url,
    add_source_youtube,
    # Chat
    ask_question,
    check_source_freshness,
    configure_chat,
    create_note,
    create_notebook,
    delete_artifact,
    delete_mind_map,
    delete_note,
    delete_notebook,
    delete_source,
    # Artifacts -- download
    download_audio,
    download_data_table,
    download_flashcards,
    download_infographic,
    download_mind_map,
    download_quiz,
    download_report,
    download_slide_deck,
    download_video,
    export_artifact,
    # Artifacts -- generation
    generate_audio_overview,
    generate_data_table,
    generate_flashcards,
    generate_infographic,
    generate_mind_map,
    generate_quiz,
    generate_report,
    generate_slide_deck,
    generate_video,
    # Account
    get_account_info,
    get_artifact,
    get_chat_history,
    get_note,
    get_notebook,
    get_notebook_description,
    get_notebook_summary,
    # Settings
    get_output_language,
    # Sharing
    get_sharing_status,
    get_source,
    get_source_fulltext,
    get_source_guide,
    import_research_sources,
    # Artifacts -- management
    list_artifacts,
    list_mind_maps,
    # Notebooks
    list_notebooks_tool,
    # Notes
    list_notes,
    list_sources,
    poll_research,
    refresh_source,
    remove_notebook_from_recent,
    remove_shared_user,
    rename_artifact,
    rename_notebook,
    rename_source,
    set_notebook_public,
    set_notebook_view_level,
    set_output_language,
    share_notebook,
    # Research
    start_research,
    suggest_reports,
    update_note,
    update_shared_user,
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
        assert result["success"] is True
        assert result["id"] == "nb-new"
        mock_client.notebooks.create.assert_awaited_once_with("Test Notebook")


class TestGetNotebook:
    async def test_gets_notebook(self, mock_ctx, mock_client):
        result = await get_notebook("nb-1", mock_ctx)
        assert result["id"] == "nb-1"
        assert result["title"] == "Research"
        mock_client.notebooks.get.assert_awaited_once_with("nb-1")


class TestDeleteNotebook:
    async def test_deletes_notebook(self, mock_ctx, mock_client):
        result = await delete_notebook("nb-1", mock_ctx)
        assert result["success"] is True
        assert result["deleted_id"] == "nb-1"
        mock_client.notebooks.delete.assert_awaited_once_with("nb-1")


class TestRenameNotebook:
    async def test_renames_notebook(self, mock_ctx, mock_client):
        result = await rename_notebook("nb-1", "New Title", mock_ctx)
        assert result["success"] is True
        assert result["title"] == "Renamed"
        mock_client.notebooks.rename.assert_awaited_once_with("nb-1", "New Title")


class TestGetNotebookDescription:
    async def test_gets_description(self, mock_ctx, mock_client):
        result = await get_notebook_description("nb-1", mock_ctx)
        assert "description" in result
        assert "suggested_topics" in result
        mock_client.notebooks.get_description.assert_awaited_once_with("nb-1")


class TestGetNotebookSummary:
    async def test_gets_summary(self, mock_ctx, mock_client):
        result = await get_notebook_summary("nb-1", mock_ctx)
        assert "summary" in result
        mock_client.notebooks.get_summary.assert_awaited_once_with("nb-1")


class TestShareNotebook:
    async def test_shares_notebook(self, mock_ctx, mock_client):
        result = await share_notebook("nb-1", True, mock_ctx)
        assert result["success"] is True
        mock_client.notebooks.share.assert_awaited_once()


class TestRemoveNotebookFromRecent:
    async def test_removes_from_recent(self, mock_ctx, mock_client):
        result = await remove_notebook_from_recent("nb-1", mock_ctx)
        assert result["success"] is True
        mock_client.notebooks.remove_from_recent.assert_awaited_once_with("nb-1")


# ============================================================================
# SOURCES
# ============================================================================


class TestAddSourceUrl:
    async def test_adds_url_source(self, mock_ctx, mock_client):
        result = await add_source_url("nb-1", "https://example.com", True, mock_ctx)
        assert result["success"] is True
        assert result["id"] == "src-new"
        mock_client.sources.add_url.assert_awaited_once_with("nb-1", "https://example.com", wait=True)


class TestAddSourceText:
    async def test_adds_text_source(self, mock_ctx, mock_client):
        result = await add_source_text("nb-1", "Some content", "Title", mock_ctx)
        assert result["success"] is True
        assert result["id"] == "src-text"
        mock_client.sources.add_text.assert_awaited_once_with("nb-1", "Some content", title="Title")


class TestGetSource:
    async def test_gets_source(self, mock_ctx, mock_client):
        result = await get_source("nb-1", "src-1", mock_ctx)
        assert result["id"] == "src-1"
        mock_client.sources.get.assert_awaited_once_with("nb-1", "src-1")


class TestGetSourceFulltext:
    async def test_gets_fulltext(self, mock_ctx, mock_client):
        result = await get_source_fulltext("nb-1", "src-1", mock_ctx)
        assert result["content"] == "Full text content"
        mock_client.sources.get_fulltext.assert_awaited_once_with("nb-1", "src-1")


class TestGetSourceGuide:
    async def test_gets_guide(self, mock_ctx, mock_client):
        result = await get_source_guide("nb-1", "src-1", mock_ctx)
        assert "summary" in result
        assert "keywords" in result
        mock_client.sources.get_guide.assert_awaited_once_with("nb-1", "src-1")


class TestAddSourceYoutube:
    async def test_adds_youtube_source(self, mock_ctx, mock_client):
        result = await add_source_youtube("nb-1", "https://youtube.com/watch?v=abc", mock_ctx)
        assert result["success"] is True
        assert result["id"] == "src-yt"
        mock_client.sources.add_youtube.assert_awaited_once()


class TestAddSourceFile:
    async def test_adds_file_source(self, mock_ctx, mock_client):
        result = await add_source_file("nb-1", "/tmp/doc.pdf", mock_ctx)
        assert result["success"] is True
        mock_client.sources.add_file.assert_awaited_once_with("nb-1", "/tmp/doc.pdf")


class TestAddSourceDrive:
    async def test_adds_drive_source(self, mock_ctx, mock_client):
        result = await add_source_drive("nb-1", "drive-id-123", "My Doc", "application/pdf", mock_ctx)
        assert result["success"] is True
        mock_client.sources.add_drive.assert_awaited_once_with("nb-1", "drive-id-123", "My Doc", "application/pdf")


class TestRenameSource:
    async def test_renames_source(self, mock_ctx, mock_client):
        result = await rename_source("nb-1", "src-1", "New Name", mock_ctx)
        assert result["success"] is True
        mock_client.sources.rename.assert_awaited_once_with("nb-1", "src-1", "New Name")


class TestRefreshSource:
    async def test_refreshes_source(self, mock_ctx, mock_client):
        result = await refresh_source("nb-1", "src-1", mock_ctx)
        assert result["success"] is True
        mock_client.sources.refresh.assert_awaited_once_with("nb-1", "src-1")


class TestDeleteSource:
    async def test_deletes_source(self, mock_ctx, mock_client):
        result = await delete_source("nb-1", "src-1", mock_ctx)
        assert result["success"] is True
        assert result["deleted_source_id"] == "src-1"
        mock_client.sources.delete.assert_awaited_once_with("nb-1", "src-1")


class TestListSources:
    async def test_returns_source_list(self, mock_ctx, mock_client):
        result = await list_sources("nb-1", ctx=mock_ctx)
        assert result["count"] == 2
        assert result["sources"][0]["id"] == "src-1"
        assert result["sources"][1]["title"] == "Research Paper"
        mock_client.sources.list.assert_awaited_once_with("nb-1")

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await list_sources("nb-1", ctx=mock_ctx)
        assert "error" in result


class TestCheckSourceFreshness:
    async def test_returns_fresh(self, mock_ctx, mock_client):
        result = await check_source_freshness("nb-1", "src-1", ctx=mock_ctx)
        assert result["is_fresh"] is True
        assert result["needs_refresh"] is False
        mock_client.sources.check_freshness.assert_awaited_once_with("nb-1", "src-1")

    async def test_returns_stale(self, mock_ctx, mock_client):
        mock_client.sources.check_freshness.return_value = False
        result = await check_source_freshness("nb-1", "src-1", ctx=mock_ctx)
        assert result["is_fresh"] is False
        assert result["needs_refresh"] is True


# ============================================================================
# CHAT
# ============================================================================


class TestAskQuestion:
    async def test_returns_answer(self, mock_ctx, mock_client):
        result = await ask_question("nb-1", "What are the findings?", ctx=mock_ctx)
        assert "answer" in result
        assert result["question"] == "What are the findings?"
        mock_client.chat.ask.assert_awaited_once()

    async def test_with_source_ids(self, mock_ctx, mock_client):
        result = await ask_question("nb-1", "Summary?", source_ids=["src-1"], ctx=mock_ctx)
        assert "answer" in result
        mock_client.chat.ask.assert_awaited()


class TestConfigureChat:
    async def test_configures_chat(self, mock_ctx, mock_client):
        result = await configure_chat("nb-1", goal="tutor", response_length="short", ctx=mock_ctx)
        assert result["success"] is True
        assert result["config"]["goal"] == "tutor"
        mock_client.chat.configure.assert_awaited_once()


class TestGetChatHistory:
    async def test_gets_history(self, mock_ctx, mock_client):
        result = await get_chat_history("nb-1", ctx=mock_ctx)
        assert result["count"] == 2
        assert result["messages"][0]["role"] == "user"
        mock_client.chat.get_history.assert_awaited_once_with("nb-1")


# ============================================================================
# ARTIFACTS -- generation
# ============================================================================


class TestGenerateAudioOverview:
    async def test_generates_audio(self, mock_ctx, mock_client):
        result = await generate_audio_overview("nb-1", "", "deep-dive", "medium", mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_audio.assert_awaited_once()
        mock_client.artifacts.wait_for_completion.assert_awaited()


class TestGenerateVideo:
    async def test_generates_video(self, mock_ctx, mock_client):
        result = await generate_video("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_video.assert_awaited_once()


class TestGenerateReport:
    async def test_generates_report(self, mock_ctx, mock_client):
        result = await generate_report("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_report.assert_awaited_once()


class TestGenerateQuiz:
    async def test_generates_quiz(self, mock_ctx, mock_client):
        result = await generate_quiz("nb-1", "standard", "medium", mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_quiz.assert_awaited_once()


class TestGenerateFlashcards:
    async def test_generates_flashcards(self, mock_ctx, mock_client):
        result = await generate_flashcards("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_flashcards.assert_awaited_once()


class TestGenerateSlideDeck:
    async def test_generates_slide_deck(self, mock_ctx, mock_client):
        result = await generate_slide_deck("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_slide_deck.assert_awaited_once()


class TestGenerateInfographic:
    async def test_generates_infographic(self, mock_ctx, mock_client):
        result = await generate_infographic("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_infographic.assert_awaited_once()


class TestGenerateDataTable:
    async def test_generates_data_table(self, mock_ctx, mock_client):
        result = await generate_data_table("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_data_table.assert_awaited_once()


class TestGenerateMindMap:
    async def test_generates_mind_map(self, mock_ctx, mock_client):
        result = await generate_mind_map("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.artifacts.generate_mind_map.assert_awaited_once()


# ============================================================================
# ARTIFACTS -- download
# ============================================================================


class TestDownloadAudio:
    async def test_downloads_audio(self, mock_ctx, mock_client):
        result = await download_audio("nb-1", "/tmp/audio.mp3", mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_audio.assert_awaited_once_with("nb-1", "/tmp/audio.mp3")


class TestDownloadVideo:
    async def test_downloads_video(self, mock_ctx, mock_client):
        result = await download_video("nb-1", "/tmp/video.mp4", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_video.assert_awaited_once_with("nb-1", "/tmp/video.mp4")


class TestDownloadReport:
    async def test_downloads_report(self, mock_ctx, mock_client):
        result = await download_report("nb-1", "/tmp/report.pdf", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_report.assert_awaited_once_with("nb-1", "/tmp/report.pdf")


class TestDownloadQuiz:
    async def test_downloads_quiz(self, mock_ctx, mock_client):
        result = await download_quiz("nb-1", "/tmp/quiz.json", "json", mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_quiz.assert_awaited_once_with("nb-1", "/tmp/quiz.json", output_format="json")


class TestDownloadFlashcards:
    async def test_downloads_flashcards(self, mock_ctx, mock_client):
        result = await download_flashcards("nb-1", "/tmp/cards.json", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_flashcards.assert_awaited_once()


class TestDownloadSlideDeck:
    async def test_downloads_slide_deck(self, mock_ctx, mock_client):
        result = await download_slide_deck("nb-1", "/tmp/slides.pdf", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_slide_deck.assert_awaited_once()


class TestDownloadInfographic:
    async def test_downloads_infographic(self, mock_ctx, mock_client):
        result = await download_infographic("nb-1", "/tmp/infographic.png", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_infographic.assert_awaited_once()


class TestDownloadDataTable:
    async def test_downloads_data_table(self, mock_ctx, mock_client):
        result = await download_data_table("nb-1", "/tmp/table.csv", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_data_table.assert_awaited_once()


class TestDownloadMindMap:
    async def test_downloads_mind_map(self, mock_ctx, mock_client):
        result = await download_mind_map("nb-1", "/tmp/mindmap.png", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.download_mind_map.assert_awaited_once()


# ============================================================================
# ARTIFACTS -- management
# ============================================================================


class TestListArtifacts:
    async def test_lists_artifacts(self, mock_ctx, mock_client):
        result = await list_artifacts("nb-1", ctx=mock_ctx)
        assert result["count"] == 0
        mock_client.artifacts.list.assert_awaited_once()


class TestGetArtifact:
    async def test_gets_artifact(self, mock_ctx, mock_client):
        result = await get_artifact("nb-1", "art-1", ctx=mock_ctx)
        assert result["id"] == "art-1"
        mock_client.artifacts.get.assert_awaited_once_with("nb-1", "art-1")


class TestDeleteArtifact:
    async def test_deletes_artifact(self, mock_ctx, mock_client):
        result = await delete_artifact("nb-1", "art-1", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.delete.assert_awaited_once_with("nb-1", "art-1")


class TestRenameArtifact:
    async def test_renames_artifact(self, mock_ctx, mock_client):
        result = await rename_artifact("nb-1", "art-1", "New Name", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.rename.assert_awaited_once_with("nb-1", "art-1", "New Name")


class TestExportArtifact:
    async def test_exports_artifact(self, mock_ctx, mock_client, tmp_path):
        out = str(tmp_path / "export.pdf")
        result = await export_artifact("nb-1", "art-1", out, "pdf", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.artifacts.export.assert_awaited_once()


class TestSuggestReports:
    async def test_returns_suggestions(self, mock_ctx, mock_client):
        result = await suggest_reports("nb-1", ctx=mock_ctx)
        assert result["count"] == 2
        assert result["suggestions"][0]["title"] == "Executive Summary"
        assert result["suggestions"][1]["prompt"] == "Analyse in depth"
        mock_client.artifacts.suggest_reports.assert_awaited_once_with("nb-1")

    async def test_returns_error_on_auth_failure(self, mock_ctx, app_context):
        app_context.client = None
        result = await suggest_reports("nb-1", ctx=mock_ctx)
        assert "error" in result


# ============================================================================
# RESEARCH
# ============================================================================


class TestStartResearch:
    async def test_starts_research(self, mock_ctx, mock_client):
        result = await start_research("nb-1", "digital twins", ctx=mock_ctx)
        assert result["status"] == "started"
        assert result["task_id"] == "research-1"
        mock_client.research.start.assert_awaited_once()


class TestPollResearch:
    async def test_polls_research(self, mock_ctx, mock_client):
        result = await poll_research("nb-1", ctx=mock_ctx)
        assert result["status"] == "completed"
        mock_client.research.poll.assert_awaited_once_with("nb-1")


class TestImportResearchSources:
    async def test_imports_sources(self, mock_ctx, mock_client):
        result = await import_research_sources("nb-1", "research-1", ["src-a", "src-b"], ctx=mock_ctx)
        assert result["success"] is True
        mock_client.research.import_sources.assert_awaited_once()


# ============================================================================
# NOTES
# ============================================================================


class TestListNotes:
    async def test_lists_notes(self, mock_ctx, mock_client):
        result = await list_notes("nb-1", ctx=mock_ctx)
        assert result["count"] == 1
        assert result["notes"][0]["id"] == "note-1"
        mock_client.notes.list.assert_awaited_once_with("nb-1")


class TestCreateNote:
    async def test_creates_note(self, mock_ctx, mock_client):
        result = await create_note("nb-1", "My Note", "Content", ctx=mock_ctx)
        assert result["success"] is True
        assert result["id"] == "note-new"
        mock_client.notes.create.assert_awaited_once_with("nb-1", "My Note", "Content")


class TestGetNote:
    async def test_gets_note(self, mock_ctx, mock_client):
        result = await get_note("nb-1", "note-1", ctx=mock_ctx)
        assert result["id"] == "note-1"
        assert result["content"] == "Content 1"
        mock_client.notes.get.assert_awaited_once_with("nb-1", "note-1")


class TestUpdateNote:
    async def test_updates_note(self, mock_ctx, mock_client):
        result = await update_note("nb-1", "note-1", content="Updated", title="New Title", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.notes.update.assert_awaited_once()


class TestDeleteNote:
    async def test_deletes_note(self, mock_ctx, mock_client):
        result = await delete_note("nb-1", "note-1", ctx=mock_ctx)
        assert result["success"] is True
        assert result["deleted_note_id"] == "note-1"
        mock_client.notes.delete.assert_awaited_once_with("nb-1", "note-1")


class TestListMindMaps:
    async def test_lists_mind_maps(self, mock_ctx, mock_client):
        result = await list_mind_maps("nb-1", ctx=mock_ctx)
        assert result["count"] == 0
        mock_client.notes.list_mind_maps.assert_awaited_once_with("nb-1")


class TestDeleteMindMap:
    async def test_deletes_mind_map(self, mock_ctx, mock_client):
        result = await delete_mind_map("nb-1", "mm-1", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.notes.delete_mind_map.assert_awaited_once_with("nb-1", "mm-1")


# ============================================================================
# SHARING
# ============================================================================


class TestGetSharingStatus:
    async def test_gets_status(self, mock_ctx, mock_client):
        result = await get_sharing_status("nb-1", ctx=mock_ctx)
        assert result["is_public"] is False
        assert result["view_level"] == "private"
        mock_client.sharing.get_status.assert_awaited_once_with("nb-1")


class TestSetNotebookPublic:
    async def test_sets_public(self, mock_ctx, mock_client):
        result = await set_notebook_public("nb-1", True, ctx=mock_ctx)
        assert result["success"] is True
        assert result["public"] is True
        mock_client.sharing.set_public.assert_awaited_once_with("nb-1", True)


class TestSetNotebookViewLevel:
    async def test_sets_view_level(self, mock_ctx, mock_client):
        result = await set_notebook_view_level("nb-1", "edit", ctx=mock_ctx)
        assert result["success"] is True
        assert result["view_level"] == "edit"
        mock_client.sharing.set_view_level.assert_awaited_once_with("nb-1", "edit")


class TestAddSharedUser:
    async def test_adds_shared_user(self, mock_ctx, mock_client):
        result = await add_shared_user("nb-1", "user@example.com", "edit", ctx=mock_ctx)
        assert result["success"] is True
        assert result["email"] == "user@example.com"
        mock_client.sharing.add_user.assert_awaited_once()


class TestUpdateSharedUser:
    async def test_updates_shared_user(self, mock_ctx, mock_client):
        result = await update_shared_user("nb-1", "user@example.com", "view", ctx=mock_ctx)
        assert result["success"] is True
        mock_client.sharing.update_user.assert_awaited_once_with("nb-1", "user@example.com", "view")


class TestRemoveSharedUser:
    async def test_removes_shared_user(self, mock_ctx, mock_client):
        result = await remove_shared_user("nb-1", "user@example.com", ctx=mock_ctx)
        assert result["success"] is True
        assert result["removed_email"] == "user@example.com"
        mock_client.sharing.remove_user.assert_awaited_once_with("nb-1", "user@example.com")


# ============================================================================
# SETTINGS
# ============================================================================


class TestGetOutputLanguage:
    async def test_gets_language(self, mock_ctx, mock_client):
        result = await get_output_language(ctx=mock_ctx)
        assert result["language"] == "en"
        mock_client.settings.get_output_language.assert_awaited_once()


class TestSetOutputLanguage:
    async def test_sets_language(self, mock_ctx, mock_client):
        result = await set_output_language("fr", ctx=mock_ctx)
        assert result["success"] is True
        assert result["language"] == "fr"
        mock_client.settings.set_output_language.assert_awaited_once_with("fr")


# ============================================================================
# ACCOUNT MANAGEMENT
# ============================================================================


class TestGetAccountInfo:
    async def test_returns_account_details(self, mock_ctx, app_context):
        result = await get_account_info(mock_ctx)
        assert result["current_account"] == "test"
        assert "available_profiles" in result
