"""
NotebookLM MCP Server
Exposes Google NotebookLM capabilities as MCP tools and resources for use
with Cursor, Claude Code, and other MCP-compatible LLM clients.

Built on top of notebooklm-py: https://github.com/teng-lin/notebooklm-py

Requires:
    - notebooklm-py >= 0.3.2
    - mcp[cli] >= 1.0.0
    - A valid NotebookLM authentication session (run: notebooklm login)

Account selection:
    The server reads NOTEBOOKLM_HOME to determine the initial account.
    Accounts can be switched at runtime via the switch_account tool,
    and the choice is persisted in ~/.notebooklm-active.json so it
    survives server restarts.
"""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from notebooklm import NotebookLMClient

ACTIVE_PROFILE_PATH = Path.home() / ".notebooklm-active.json"

# Well-known profile names mapped to directory suffixes.
# Users can add more by authenticating with any directory name.
DEFAULT_PROFILES = {
    "personal": ".notebooklm",
    "work": ".notebooklm-work",
    "design": ".notebooklm-design",
}


def _resolve_profile_dir(name: str) -> Path:
    """Turn a profile name or path into an absolute directory."""
    if os.path.isabs(name):
        return Path(name)
    suffix = DEFAULT_PROFILES.get(name.lower(), f".notebooklm-{name.lower()}")
    return Path.home() / suffix


def _read_active_profile() -> str | None:
    """Read the persisted active profile name, if any."""
    try:
        data = json.loads(ACTIVE_PROFILE_PATH.read_text(encoding="utf-8"))
        return data.get("active")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _write_active_profile(name: str) -> None:
    """Persist the active profile name."""
    ACTIVE_PROFILE_PATH.write_text(
        json.dumps({"active": name}, indent=2),
        encoding="utf-8",
    )


def _current_profile_name() -> str:
    """Determine the current profile name from persisted state or env."""
    # 1. Check persisted preference
    saved = _read_active_profile()
    if saved:
        return saved

    # 2. Fall back to NOTEBOOKLM_HOME env var
    home = os.environ.get("NOTEBOOKLM_HOME", "")
    if home:
        folder = os.path.basename(os.path.expanduser(home))
        for name, suffix in DEFAULT_PROFILES.items():
            if folder == suffix:
                return name
        return folder.replace(".notebooklm-", "").replace(".notebooklm", "personal")

    # 3. Default
    return "personal"


@dataclass
class AppContext:
    """Application context with a switchable NotebookLM client."""

    client: NotebookLMClient | None
    profile: str


def _find_cli() -> Path | None:
    """Locate the notebooklm CLI executable in the current environment."""
    venv_dir = Path(sys.executable).parent
    for name in ("notebooklm", "notebooklm.exe"):
        p = venv_dir / name
        if p.exists():
            return p
    return None


async def _run_login(profile: str, ctx: Context | None = None) -> bool:
    """Launch notebooklm login for a profile and wait for completion.

    Returns True if credentials were created/refreshed successfully.
    """
    cli = _find_cli()
    if not cli:
        return False

    target_dir = _resolve_profile_dir(profile)
    target_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["NOTEBOOKLM_HOME"] = str(target_dir)

    if ctx:
        await ctx.info(
            f"Session expired for '{profile}'. "
            "Launching browser for re-authentication -- "
            "please sign into your Google account."
        )

    try:
        process = subprocess.Popen(
            [str(cli), "login"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _stdout, _stderr = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, process.communicate),
            timeout=300,
        )
    except asyncio.TimeoutError:
        with suppress(Exception):
            process.kill()
        return False
    except Exception:
        return False

    storage_file = target_dir / "storage_state.json"
    return storage_file.exists() and process.returncode == 0


async def _create_client(profile: str) -> NotebookLMClient:
    """Create and initialise a NotebookLM client for the given profile.

    The client is an async context manager that must be entered before
    API calls will work. This function handles that automatically.
    Callers are responsible for calling ``await client.close()`` when
    the client is no longer needed.

    Raises ValueError if the stored session is expired or invalid.
    Callers that need a non-fatal path should catch the exception.
    """
    profile_dir = _resolve_profile_dir(profile)
    os.environ["NOTEBOOKLM_HOME"] = str(profile_dir)
    client = await NotebookLMClient.from_storage()
    if hasattr(client, "__aenter__"):
        await client.__aenter__()
    return client


async def _ensure_authenticated(
    app: "AppContext",
    ctx: Context,
) -> bool:
    """Test the current session and re-authenticate if expired.

    Handles three cases:
    1. No client at all (no credentials on disk) -- triggers login.
    2. Client exists but session is stale -- triggers re-login.
    3. Client exists and session is valid -- returns immediately.

    On successful re-authentication, replaces app.client in place.
    Returns True if the session is valid (or was successfully refreshed).
    """
    # Case 1: no client yet (no credentials existed at startup)
    if app.client is not None:
        try:
            await app.client.notebooks.list()
            return True
        except Exception:
            pass

    # Session is missing or expired -- attempt automatic re-authentication
    if app.client is None:
        await ctx.info(
            "No credentials found. Launching browser for authentication -- please sign into your Google account."
        )
    else:
        await ctx.info("Session expired. Launching browser for re-authentication...")

    success = await _run_login(app.profile, ctx)
    if not success:
        await ctx.error("Automatic re-authentication failed. Please run 'notebooklm login' manually in your terminal.")
        return False

    # Close old client if one exists
    if app.client is not None:
        with suppress(Exception):
            await app.client.__aexit__(None, None, None)

    app.client = await _create_client(app.profile)
    await ctx.info("Authentication successful. Resuming operation.")
    return True


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage NotebookLM client lifecycle with account switching support.

    The lifespan creates a client from stored credentials but does NOT
    validate the session at startup. Validation and automatic
    re-authentication are handled lazily by _ensure_authenticated on
    the first tool call, when the MCP transport is live and can relay
    progress messages to the user.
    """
    profile = _current_profile_name()
    profile_dir = _resolve_profile_dir(profile)
    storage_file = profile_dir / "storage_state.json"

    client = None
    if storage_file.exists():
        try:
            client = await _create_client(profile)
        except (ValueError, Exception):
            # Session expired or credentials invalid -- the server still
            # starts; _ensure_authenticated will handle re-login on the
            # first tool call when the MCP transport is live.
            client = None

    ctx = AppContext(client=client, profile=profile)
    try:
        yield ctx
    finally:
        if ctx.client is not None:
            with suppress(Exception):
                await ctx.client.__aexit__(None, None, None)


# Initialise FastMCP server with lifespan
mcp = FastMCP("NotebookLM", lifespan=app_lifespan)


# ============================================================================
# RESOURCES - Read-only data access
# ============================================================================


@mcp.resource("notebooklm://notebooks")
async def list_notebooks(ctx: Context[ServerSession, AppContext]) -> str:
    """List all NotebookLM notebooks with IDs and titles."""
    app = ctx.request_context.lifespan_context
    if app.client is None:
        return (
            "Not authenticated. Please call any tool (e.g. list_notebooks_tool) "
            "first to trigger automatic authentication."
        )

    try:
        notebooks = await app.client.notebooks.list()
    except Exception:
        return (
            "Session expired. Please call any tool (e.g. list_notebooks_tool) "
            "to trigger automatic re-authentication, then retry this resource."
        )

    if not notebooks:
        return "No notebooks found."

    result = "# Available NotebookLM Notebooks\n\n"
    for nb in notebooks:
        result += f"- **{nb.title}** (ID: `{nb.id}`)\n"

    return result


@mcp.resource("notebooklm://notebook/{notebook_id}")
async def get_notebook_info(notebook_id: str, ctx: Context[ServerSession, AppContext]) -> str:
    """Get detailed information about a specific notebook."""
    app = ctx.request_context.lifespan_context
    if app.client is None:
        return (
            "Not authenticated. Please call any tool (e.g. list_notebooks_tool) "
            "first to trigger automatic authentication."
        )

    try:
        notebooks = await app.client.notebooks.list()
    except Exception:
        return (
            "Session expired. Please call any tool (e.g. list_notebooks_tool) "
            "to trigger automatic re-authentication, then retry this resource."
        )

    notebook = next((nb for nb in notebooks if nb.id == notebook_id), None)

    if not notebook:
        return f"Notebook {notebook_id} not found."

    sources = await app.client.sources.list(notebook_id)

    result = f"# Notebook: {notebook.title}\n\n"
    result += f"**ID:** `{notebook_id}`\n\n"
    result += f"## Sources ({len(sources)})\n\n"

    for source in sources:
        result += f"- {source.title}\n"

    return result


# ============================================================================
# TOOLS - Executable actions
# ============================================================================


@mcp.tool()
async def list_notebooks_tool(ctx: Context[ServerSession, AppContext]) -> dict:
    """List all NotebookLM notebooks.

    Returns a dictionary with notebook titles and IDs.
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info("Fetching notebooks...")
    notebooks = await client.notebooks.list()

    return {"count": len(notebooks), "notebooks": [{"id": nb.id, "title": nb.title} for nb in notebooks]}


@mcp.tool()
async def create_notebook(title: str, ctx: Context[ServerSession, AppContext]) -> dict:
    """Create a new NotebookLM notebook.

    Args:
        title: Title for the new notebook

    Returns:
        Dictionary with notebook ID and title
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Creating notebook: {title}")
    notebook = await client.notebooks.create(title)

    return {"id": notebook.id, "title": notebook.title, "success": True}


@mcp.tool()
async def add_source_url(
    notebook_id: str, url: str, wait: bool = True, ctx: Context[ServerSession, AppContext] = None
) -> dict:
    """Add a URL as a source to a notebook.

    Args:
        notebook_id: ID of the notebook
        url: URL to add as a source
        wait: Whether to wait for processing to complete

    Returns:
        Dictionary with source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Adding URL source: {url}")
    await ctx.report_progress(0.3, 1.0, "Processing URL...")

    source = await client.sources.add_url(notebook_id, url, wait=wait)

    await ctx.report_progress(1.0, 1.0, "Complete")

    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def add_source_text(notebook_id: str, text: str, title: str, ctx: Context[ServerSession, AppContext]) -> dict:
    """Add text content as a source to a notebook.

    Args:
        notebook_id: ID of the notebook
        text: Text content to add
        title: Title for the source

    Returns:
        Dictionary with source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Adding text source: {title}")
    source = await client.sources.add_text(notebook_id, text, title=title)

    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def get_source(
    notebook_id: str,
    source_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Get metadata for a specific source in a notebook.

    Args:
        notebook_id: ID of the notebook
        source_id: ID of the source to retrieve

    Returns:
        Dictionary with source metadata
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    source = await app.client.sources.get(notebook_id, source_id)
    return {"id": source.id, "title": source.title}


@mcp.tool()
async def get_source_fulltext(
    notebook_id: str,
    source_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Get the full indexed text content of a source.

    Args:
        notebook_id: ID of the notebook
        source_id: ID of the source

    Returns:
        Dictionary with the full text content
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    result = await app.client.sources.get_fulltext(notebook_id, source_id)
    return {"content": getattr(result, "content", str(result))}


@mcp.tool()
async def get_source_guide(
    notebook_id: str,
    source_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Get the AI-generated source guide (summary and keywords).

    Args:
        notebook_id: ID of the notebook
        source_id: ID of the source

    Returns:
        Dictionary with summary and keywords
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    result = await app.client.sources.get_guide(notebook_id, source_id)
    return {
        "summary": getattr(result, "summary", str(result)),
        "keywords": getattr(result, "keywords", []),
    }


@mcp.tool()
async def add_source_youtube(
    notebook_id: str,
    url: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Add a YouTube video as a source to a notebook.

    Args:
        notebook_id: ID of the notebook
        url: YouTube video URL

    Returns:
        Dictionary with the new source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Adding YouTube source: {url}")
    source = await app.client.sources.add_youtube(notebook_id, url)
    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def add_source_file(
    notebook_id: str,
    file_path: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Upload a local file as a source to a notebook.

    Args:
        notebook_id: ID of the notebook
        file_path: Path to the file to upload

    Returns:
        Dictionary with the new source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Uploading file source: {file_path}")
    source = await app.client.sources.add_file(notebook_id, file_path)
    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def add_source_drive(
    notebook_id: str,
    file_id: str,
    title: str,
    mime_type: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Add a Google Drive file as a source to a notebook.

    Args:
        notebook_id: ID of the notebook
        file_id: Google Drive file ID
        title: Display title for the source
        mime_type: MIME type of the file (e.g. application/pdf)

    Returns:
        Dictionary with the new source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Adding Drive source: {title}")
    source = await app.client.sources.add_drive(notebook_id, file_id, title, mime_type)
    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def rename_source(
    notebook_id: str,
    source_id: str,
    title: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Rename a source in a notebook.

    Args:
        notebook_id: ID of the notebook
        source_id: ID of the source to rename
        title: New title for the source

    Returns:
        Dictionary with updated source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    source = await app.client.sources.rename(notebook_id, source_id, title)
    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def refresh_source(
    notebook_id: str,
    source_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Re-fetch and re-index the content of a URL-based source.

    Args:
        notebook_id: ID of the notebook
        source_id: ID of the source to refresh

    Returns:
        Dictionary with refreshed source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Refreshing source content...")
    source = await app.client.sources.refresh(notebook_id, source_id)
    return {"id": source.id, "title": source.title, "success": True}


@mcp.tool()
async def delete_source(
    notebook_id: str,
    source_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Remove a source from a notebook. This action cannot be undone.

    Args:
        notebook_id: ID of the notebook
        source_id: ID of the source to delete

    Returns:
        Dictionary confirming deletion
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.sources.delete(notebook_id, source_id)
    return {"success": True, "deleted_source_id": source_id}


@mcp.tool()
async def get_notebook(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Look up a single notebook by its ID.

    Args:
        notebook_id: ID of the notebook to retrieve

    Returns:
        Dictionary with notebook metadata
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    notebook = await app.client.notebooks.get(notebook_id)
    return {"id": notebook.id, "title": notebook.title}


@mcp.tool()
async def delete_notebook(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Permanently delete a notebook. This action cannot be undone.

    Args:
        notebook_id: ID of the notebook to delete

    Returns:
        Dictionary confirming deletion
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.notebooks.delete(notebook_id)
    return {"success": True, "deleted_id": notebook_id}


@mcp.tool()
async def rename_notebook(
    notebook_id: str,
    title: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Rename a notebook.

    Args:
        notebook_id: ID of the notebook to rename
        title: New title for the notebook

    Returns:
        Dictionary with the updated notebook details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    notebook = await app.client.notebooks.rename(notebook_id, title)
    return {"id": notebook.id, "title": notebook.title, "success": True}


@mcp.tool()
async def get_notebook_description(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Get the AI-generated description and suggested topics for a notebook.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with description and suggested topics
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    result = await app.client.notebooks.get_description(notebook_id)
    return {
        "description": getattr(result, "description", str(result)),
        "suggested_topics": getattr(result, "suggested_topics", []),
    }


@mcp.tool()
async def get_notebook_summary(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Get a raw text summary of a notebook's contents.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with the summary text
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    summary = await app.client.notebooks.get_summary(notebook_id)
    return {"summary": str(summary)}


@mcp.tool()
async def share_notebook(
    notebook_id: str,
    public: bool = False,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Share a notebook by updating its sharing settings.

    Args:
        notebook_id: ID of the notebook to share
        public: Whether to make the notebook publicly accessible

    Returns:
        Dictionary with the sharing result
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    result = await app.client.notebooks.share(notebook_id, {"public": public})
    return {"success": True, "result": result}


@mcp.tool()
async def remove_notebook_from_recent(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Remove a notebook from the recent notebooks list.

    Args:
        notebook_id: ID of the notebook to remove from recents

    Returns:
        Dictionary confirming the operation
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.notebooks.remove_from_recent(notebook_id)
    return {"success": True, "notebook_id": notebook_id}


@mcp.tool()
async def ask_question(
    notebook_id: str,
    question: str,
    source_ids: list[str] | None = None,
    conversation_id: str | None = None,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Ask a question to a NotebookLM notebook and get an AI-generated answer.

    Args:
        notebook_id: ID of the notebook to query
        question: The question to ask
        source_ids: Restrict the query to specific source IDs (optional)
        conversation_id: Continue an existing conversation thread (optional)

    Returns:
        Dictionary with the answer and citation information
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Querying notebook: {notebook_id}")
    await ctx.report_progress(0.5, 1.0, "Generating answer...")

    kwargs = {}
    if source_ids:
        kwargs["source_ids"] = source_ids
    if conversation_id:
        kwargs["conversation_id"] = conversation_id

    result = await app.client.chat.ask(notebook_id, question, **kwargs)

    await ctx.report_progress(1.0, 1.0, "Complete")

    return {
        "answer": result.answer,
        "has_citations": hasattr(result, "citations") and bool(result.citations),
        "question": question,
        "conversation_id": getattr(result, "conversation_id", None),
    }


@mcp.tool()
async def configure_chat(
    notebook_id: str,
    goal: str = "",
    response_length: str = "",
    custom_prompt: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Configure the chat persona and behaviour for a notebook.

    Args:
        notebook_id: ID of the notebook
        goal: High-level goal or persona for the chat (e.g. "tutor", "analyst")
        response_length: Preferred response length (short, medium, long)
        custom_prompt: Custom system prompt for the chat

    Returns:
        Dictionary confirming the configuration
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    kwargs = {}
    if goal:
        kwargs["goal"] = goal
    if response_length:
        kwargs["response_length"] = response_length
    if custom_prompt:
        kwargs["custom_prompt"] = custom_prompt

    await app.client.chat.configure(notebook_id, **kwargs)
    return {"success": True, "notebook_id": notebook_id, "config": kwargs}


@mcp.tool()
async def get_chat_history(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Retrieve the conversation history for a notebook.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with the list of chat messages
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    history = await app.client.chat.get_history(notebook_id)
    return {
        "count": len(history),
        "messages": [
            {"role": getattr(m, "role", "unknown"), "content": getattr(m, "content", str(m))} for m in history
        ],
    }


@mcp.tool()
async def generate_audio_overview(
    notebook_id: str,
    instructions: str = "",
    audio_format: str = "deep-dive",
    length: str = "medium",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate an audio overview (podcast) from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation
        audio_format: Format type (deep-dive, brief, critique, debate)
        length: Length (short, medium, long)

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Generating audio overview: {audio_format} ({length})")
    await ctx.report_progress(0.2, 1.0, "Starting generation...")

    status = await client.artifacts.generate_audio(
        notebook_id, instructions=instructions, format=audio_format, length=length
    )

    await ctx.report_progress(0.5, 1.0, "Waiting for generation to complete...")
    await client.artifacts.wait_for_completion(notebook_id, status.task_id)

    await ctx.report_progress(1.0, 1.0, "Audio generation complete")

    return {"task_id": status.task_id, "status": "completed", "format": audio_format, "length": length}


@mcp.tool()
async def download_audio(notebook_id: str, output_path: str, ctx: Context[ServerSession, AppContext]) -> dict:
    """Download the generated audio overview to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the audio file

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Downloading audio to: {output_path}")
    await ctx.report_progress(0.5, 1.0, "Downloading...")

    await client.artifacts.download_audio(notebook_id, output_path)

    await ctx.report_progress(1.0, 1.0, "Download complete")

    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_quiz(
    notebook_id: str,
    quantity: str = "standard",
    difficulty: str = "medium",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate a quiz from notebook sources.

    Args:
        notebook_id: ID of the notebook
        quantity: Number of questions (few, standard, more)
        difficulty: Difficulty level (easy, medium, hard)

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Generating quiz: {difficulty} difficulty, {quantity} questions")
    await ctx.report_progress(0.3, 1.0, "Starting generation...")

    status = await client.artifacts.generate_quiz(notebook_id, quantity=quantity, difficulty=difficulty)

    await ctx.report_progress(0.7, 1.0, "Waiting for completion...")
    await client.artifacts.wait_for_completion(notebook_id, status.task_id)

    await ctx.report_progress(1.0, 1.0, "Quiz generation complete")

    return {"task_id": status.task_id, "status": "completed", "quantity": quantity, "difficulty": difficulty}


@mcp.tool()
async def download_quiz(
    notebook_id: str, output_path: str, output_format: str = "json", ctx: Context[ServerSession, AppContext] = None
) -> dict:
    """Download the generated quiz to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the quiz file
        output_format: Format (json, markdown, html)

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    await ctx.info(f"Downloading quiz as {output_format} to: {output_path}")
    await ctx.report_progress(0.5, 1.0, "Downloading...")

    await client.artifacts.download_quiz(notebook_id, output_path, output_format=output_format)

    await ctx.report_progress(1.0, 1.0, "Download complete")

    return {"output_path": output_path, "format": output_format, "success": True}


# ============================================================================
# ARTIFACTS -- additional generation and download tools
# ============================================================================


@mcp.tool()
async def generate_video(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate a video from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating video...")
    status = await app.client.artifacts.generate_video(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_video(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated video to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the video file

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_video(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_report(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate a report from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating report...")
    status = await app.client.artifacts.generate_report(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_report(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated report to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the report file

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_report(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_flashcards(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate flashcards from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating flashcards...")
    status = await app.client.artifacts.generate_flashcards(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_flashcards(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated flashcards to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the flashcards file

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_flashcards(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_slide_deck(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate a slide deck from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating slide deck...")
    status = await app.client.artifacts.generate_slide_deck(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_slide_deck(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated slide deck to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the slide deck

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_slide_deck(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_infographic(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate an infographic from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating infographic...")
    status = await app.client.artifacts.generate_infographic(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_infographic(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated infographic to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the infographic

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_infographic(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_data_table(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate a data table from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating data table...")
    status = await app.client.artifacts.generate_data_table(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_data_table(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated data table to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the data table

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_data_table(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


@mcp.tool()
async def generate_mind_map(
    notebook_id: str,
    instructions: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate a mind map from notebook sources.

    Args:
        notebook_id: ID of the notebook
        instructions: Custom instructions for generation

    Returns:
        Dictionary with task status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Generating mind map...")
    status = await app.client.artifacts.generate_mind_map(notebook_id, instructions=instructions)
    await app.client.artifacts.wait_for_completion(notebook_id, status.task_id)
    return {"task_id": status.task_id, "status": "completed"}


@mcp.tool()
async def download_mind_map(
    notebook_id: str,
    output_path: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Download the generated mind map to a file.

    Args:
        notebook_id: ID of the notebook
        output_path: Path where to save the mind map

    Returns:
        Dictionary with download status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.download_mind_map(notebook_id, output_path)
    return {"output_path": output_path, "success": True}


# ============================================================================
# ARTIFACTS -- management tools
# ============================================================================


@mcp.tool()
async def list_artifacts(
    notebook_id: str,
    artifact_type: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """List artifacts in a notebook, optionally filtered by type.

    Args:
        notebook_id: ID of the notebook
        artifact_type: Filter by type (audio, video, report, quiz, flashcards,
                       slide_deck, infographic, data_table, mind_map).
                       Leave empty for all types.

    Returns:
        Dictionary with the list of artifacts
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    artifacts = await app.client.artifacts.list(notebook_id, artifact_type or None)
    return {
        "count": len(artifacts),
        "artifacts": [{"id": getattr(a, "id", str(a)), "title": getattr(a, "title", "")} for a in artifacts],
    }


@mcp.tool()
async def get_artifact(
    notebook_id: str,
    artifact_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Get metadata for a specific artifact.

    Args:
        notebook_id: ID of the notebook
        artifact_id: ID of the artifact

    Returns:
        Dictionary with artifact metadata
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    artifact = await app.client.artifacts.get(notebook_id, artifact_id)
    return {
        "id": getattr(artifact, "id", str(artifact)),
        "title": getattr(artifact, "title", ""),
    }


@mcp.tool()
async def delete_artifact(
    notebook_id: str,
    artifact_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Delete an artifact from a notebook. This action cannot be undone.

    Args:
        notebook_id: ID of the notebook
        artifact_id: ID of the artifact to delete

    Returns:
        Dictionary confirming deletion
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.artifacts.delete(notebook_id, artifact_id)
    return {"success": True, "deleted_artifact_id": artifact_id}


@mcp.tool()
async def rename_artifact(
    notebook_id: str,
    artifact_id: str,
    title: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Rename an artifact.

    Args:
        notebook_id: ID of the notebook
        artifact_id: ID of the artifact to rename
        title: New title for the artifact

    Returns:
        Dictionary with the updated artifact details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    artifact = await app.client.artifacts.rename(notebook_id, artifact_id, title)
    return {
        "id": getattr(artifact, "id", str(artifact)),
        "title": getattr(artifact, "title", ""),
        "success": True,
    }


@mcp.tool()
async def export_artifact(
    notebook_id: str,
    artifact_id: str,
    output_path: str,
    export_format: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Export an artifact to a file in the requested format.

    Uses the appropriate export method based on the artifact type.
    For reports, export_report is used; for data tables, export_data_table;
    for all others, the generic export method.

    Args:
        notebook_id: ID of the notebook
        artifact_id: ID of the artifact to export
        output_path: Path where to save the exported file
        export_format: Desired format (pdf, csv, json, etc.). Leave empty for default.

    Returns:
        Dictionary with export status
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Exporting artifact to: {output_path}")
    data = await app.client.artifacts.export(notebook_id, artifact_id, export_format or None)
    Path(output_path).write_bytes(data if isinstance(data, bytes) else str(data).encode("utf-8"))
    return {"output_path": output_path, "success": True}


# ============================================================================
# RESEARCH -- web and Drive research with auto-import
# ============================================================================


@mcp.tool()
async def start_research(
    notebook_id: str,
    query: str,
    source: str = "web",
    mode: str = "fast",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Start a research task that searches the web or Google Drive.

    Results can be polled with poll_research and imported with
    import_research_sources.

    Args:
        notebook_id: ID of the notebook
        query: Research query
        source: Where to search -- "web" or "drive"
        mode: Research depth -- "fast" or "deep"

    Returns:
        Dictionary with the research task ID
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Starting {source} research ({mode}): {query}")
    result = await app.client.research.start(notebook_id, query, source, mode)
    return {"task_id": result.task_id, "status": "started"}


@mcp.tool()
async def poll_research(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Check the status and results of a running research task.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with the current research status and any results
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    result = await app.client.research.poll(notebook_id)
    return {
        "status": getattr(result, "status", str(result)),
        "results": getattr(result, "results", []),
    }


@mcp.tool()
async def import_research_sources(
    notebook_id: str,
    task_id: str,
    sources: list[str],
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Import selected sources from a completed research task into the notebook.

    Args:
        notebook_id: ID of the notebook
        task_id: ID of the research task
        sources: List of source identifiers to import

    Returns:
        Dictionary with import results
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Importing {len(sources)} research sources...")
    result = await app.client.research.import_sources(notebook_id, task_id, sources)
    return {"success": True, "result": result}


# ============================================================================
# NOTES -- notebook notes and mind maps
# ============================================================================


@mcp.tool()
async def list_notes(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """List all notes in a notebook.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with the list of notes
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    notes = await app.client.notes.list(notebook_id)
    return {
        "count": len(notes),
        "notes": [{"id": n.id, "title": n.title} for n in notes],
    }


@mcp.tool()
async def create_note(
    notebook_id: str,
    title: str,
    content: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Create a new note in a notebook.

    Args:
        notebook_id: ID of the notebook
        title: Title for the note
        content: Text content of the note (optional)

    Returns:
        Dictionary with the new note details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    note = await app.client.notes.create(notebook_id, title, content)
    return {"id": note.id, "title": note.title, "success": True}


@mcp.tool()
async def get_note(
    notebook_id: str,
    note_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Get the content of a specific note.

    Args:
        notebook_id: ID of the notebook
        note_id: ID of the note

    Returns:
        Dictionary with note content
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    note = await app.client.notes.get(notebook_id, note_id)
    return {"id": note.id, "title": note.title, "content": note.content}


@mcp.tool()
async def update_note(
    notebook_id: str,
    note_id: str,
    content: str = "",
    title: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Update an existing note's content and/or title.

    Args:
        notebook_id: ID of the notebook
        note_id: ID of the note to update
        content: New content for the note (optional)
        title: New title for the note (optional)

    Returns:
        Dictionary with the updated note details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    kwargs = {}
    if content:
        kwargs["content"] = content
    if title:
        kwargs["title"] = title

    note = await app.client.notes.update(notebook_id, note_id, **kwargs)
    return {"id": note.id, "title": note.title, "success": True}


@mcp.tool()
async def delete_note(
    notebook_id: str,
    note_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Delete a note from a notebook. This action cannot be undone.

    Args:
        notebook_id: ID of the notebook
        note_id: ID of the note to delete

    Returns:
        Dictionary confirming deletion
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.notes.delete(notebook_id, note_id)
    return {"success": True, "deleted_note_id": note_id}


@mcp.tool()
async def list_mind_maps(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """List all mind maps in a notebook.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with the list of mind maps
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    mind_maps = await app.client.notes.list_mind_maps(notebook_id)
    return {
        "count": len(mind_maps),
        "mind_maps": [{"id": getattr(m, "id", str(m)), "title": getattr(m, "title", "")} for m in mind_maps],
    }


@mcp.tool()
async def delete_mind_map(
    notebook_id: str,
    mind_map_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Delete a mind map from a notebook. This action cannot be undone.

    Args:
        notebook_id: ID of the notebook
        mind_map_id: ID of the mind map to delete

    Returns:
        Dictionary confirming deletion
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.notes.delete_mind_map(notebook_id, mind_map_id)
    return {"success": True, "deleted_mind_map_id": mind_map_id}


# ============================================================================
# SHARING -- notebook access and permissions
# ============================================================================


@mcp.tool()
async def get_sharing_status(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Get the current sharing status and permissions for a notebook.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with sharing status details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    status = await app.client.sharing.get_status(notebook_id)
    return {
        "is_public": getattr(status, "is_public", False),
        "view_level": getattr(status, "view_level", "private"),
        "users": getattr(status, "users", []),
    }


@mcp.tool()
async def set_notebook_public(
    notebook_id: str,
    public: bool,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Set whether a notebook is publicly accessible.

    Args:
        notebook_id: ID of the notebook
        public: True to make public, False to make private

    Returns:
        Dictionary confirming the change
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.sharing.set_public(notebook_id, public)
    return {"success": True, "notebook_id": notebook_id, "public": public}


@mcp.tool()
async def set_notebook_view_level(
    notebook_id: str,
    level: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Set the view permission level for a shared notebook.

    Args:
        notebook_id: ID of the notebook
        level: Permission level (e.g. "view", "comment", "edit")

    Returns:
        Dictionary confirming the change
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.sharing.set_view_level(notebook_id, level)
    return {"success": True, "notebook_id": notebook_id, "view_level": level}


@mcp.tool()
async def add_shared_user(
    notebook_id: str,
    email: str,
    permission: str = "view",
    notify: bool = True,
    message: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Share a notebook with a specific user by email.

    Args:
        notebook_id: ID of the notebook
        email: Email address of the user to share with
        permission: Permission level (view, comment, edit)
        notify: Whether to send a notification email
        message: Optional message to include in the notification

    Returns:
        Dictionary confirming the share
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.sharing.add_user(notebook_id, email, permission, notify, message)
    return {"success": True, "email": email, "permission": permission}


@mcp.tool()
async def update_shared_user(
    notebook_id: str,
    email: str,
    permission: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Update the permission level for a user who already has access.

    Args:
        notebook_id: ID of the notebook
        email: Email address of the shared user
        permission: New permission level (view, comment, edit)

    Returns:
        Dictionary confirming the update
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.sharing.update_user(notebook_id, email, permission)
    return {"success": True, "email": email, "permission": permission}


@mcp.tool()
async def remove_shared_user(
    notebook_id: str,
    email: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Remove a user's access to a shared notebook.

    Args:
        notebook_id: ID of the notebook
        email: Email address of the user to remove

    Returns:
        Dictionary confirming the removal
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.sharing.remove_user(notebook_id, email)
    return {"success": True, "removed_email": email}


# ============================================================================
# SETTINGS -- output language preferences
# ============================================================================


@mcp.tool()
async def get_output_language(
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Get the current output language setting for NotebookLM.

    Returns:
        Dictionary with the current language code
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    language = await app.client.settings.get_output_language()
    return {"language": str(language)}


@mcp.tool()
async def set_output_language(
    language: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Set the output language for NotebookLM responses.

    Args:
        language: Language code (e.g. "en", "fr", "de", "es", "ja")

    Returns:
        Dictionary confirming the language change
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await app.client.settings.set_output_language(language)
    return {"success": True, "language": language}


# ============================================================================
# ACCOUNT MANAGEMENT
# ============================================================================


@mcp.tool()
async def get_account_info(ctx: Context[ServerSession, AppContext]) -> dict:
    """Show the current NotebookLM account and available profiles.

    Returns:
        Dictionary with current account, config path, available profiles,
        and instructions for switching or adding accounts.
    """
    app = ctx.request_context.lifespan_context
    profile_dir = _resolve_profile_dir(app.profile)

    # Discover available profiles (directories that exist)
    available = {}
    for name, suffix in DEFAULT_PROFILES.items():
        path = Path.home() / suffix
        if path.exists() and (path / "storage_state.json").exists():
            available[name] = str(path)

    # Check for any extra .notebooklm-* directories
    for p in Path.home().glob(".notebooklm-*"):
        if p.is_dir() and (p / "storage_state.json").exists():
            label = p.name.replace(".notebooklm-", "")
            if label not in available:
                available[label] = str(p)

    return {
        "current_account": app.profile,
        "config_path": str(profile_dir),
        "available_profiles": available,
        "switch_instructions": (
            "Use the switch_account tool with a profile name "
            "(e.g. 'work', 'personal', 'design') to change account. "
            "No restart required."
        ),
        "new_account_instructions": (
            "To authenticate a new account, run in your terminal:\n"
            "  notebooklm login\n"
            "with NOTEBOOKLM_HOME set to the desired profile directory, e.g.:\n"
            "  NOTEBOOKLM_HOME=~/.notebooklm-<name> notebooklm login"
        ),
    }


@mcp.tool()
async def switch_account(
    profile: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Switch the active NotebookLM account to a different Google profile.

    The change takes effect immediately and is persisted across server
    restarts. Use get_account_info to see available profiles.

    Args:
        profile: Profile name to switch to (e.g. 'work', 'personal',
                 'design', or any custom name matching ~/.notebooklm-<name>)

    Returns:
        Dictionary confirming the switch with the new profile details.
    """
    app = ctx.request_context.lifespan_context
    target_dir = _resolve_profile_dir(profile)
    storage_file = target_dir / "storage_state.json"

    if not storage_file.exists():
        return {
            "success": False,
            "error": f"No credentials found for profile '{profile}' at {target_dir}",
            "hint": (f"Authenticate first by running:\n  NOTEBOOKLM_HOME={target_dir} notebooklm login"),
        }

    previous = app.profile

    # Close existing client
    await ctx.info(f"Switching from '{previous}' to '{profile}'...")
    if app.client is not None:
        with suppress(Exception):
            await app.client.__aexit__(None, None, None)

    # Create new client for the target profile
    app.client = await _create_client(profile)
    app.profile = profile

    # Persist the choice
    _write_active_profile(profile)

    await ctx.info(f"Switched to '{profile}' account")

    return {
        "success": True,
        "previous_account": previous,
        "current_account": profile,
        "config_path": str(target_dir),
    }


@mcp.tool()
async def create_profile(
    profile: str,
    ctx: Context[ServerSession, AppContext],
) -> dict:
    """Create a new NotebookLM account profile and launch the Google login.

    This opens a browser window for the user to sign into their Google
    account. Once authentication is complete, the profile is ready to
    use via switch_account.

    Args:
        profile: Name for the new profile (e.g. 'work', 'design',
                 'testing'). Will be stored at ~/.notebooklm-<name>.

    Returns:
        Dictionary with the profile status and next steps.
    """
    target_dir = _resolve_profile_dir(profile)
    storage_file = target_dir / "storage_state.json"

    if storage_file.exists():
        return {
            "success": False,
            "error": f"Profile '{profile}' already exists at {target_dir}",
            "hint": "Use switch_account to switch to it, or choose a different name.",
        }

    success = await _run_login(profile, ctx)

    if not success:
        return {
            "success": False,
            "error": f"Login failed for profile '{profile}'",
            "hint": (
                "Ensure playwright and chromium are installed:\n"
                "  pip install playwright && playwright install chromium\n"
                "Or try running notebooklm login manually in your terminal."
            ),
        }

    return {
        "success": True,
        "profile": profile,
        "config_path": str(target_dir),
        "next_step": (f"Profile '{profile}' is ready. Use switch_account with profile='{profile}' to start using it."),
    }


# ============================================================================
# UTILITY -- PDF / PNG conversion tools
# ============================================================================


@mcp.tool()
async def pdf_to_png(
    pdf_path: str,
    output_directory: str = "",
    dpi: int = 200,
) -> dict:
    """Convert a PDF file to individual PNG images (one per page).

    Useful for making slide deck pages visible to LLMs for review or editing.

    Args:
        pdf_path: Path to the source PDF file
        output_directory: Directory to write PNGs into. Defaults to a folder
            beside the PDF named <filename>_pages/
        dpi: Render resolution (default 200 -- good balance of quality and size)

    Returns:
        Dictionary with output directory, list of page image paths, and page count
    """
    import fitz  # pymupdf

    pdf = Path(pdf_path)
    if not pdf.is_file():
        return {"error": f"PDF not found: {pdf_path}"}

    out_dir = Path(output_directory) if output_directory else pdf.parent / f"{pdf.stem}_pages"
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf))
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[str] = []

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=matrix)
        out_file = out_dir / f"page_{i + 1:03d}.png"
        pix.save(str(out_file))
        pages.append(str(out_file))

    doc.close()

    return {
        "output_directory": str(out_dir),
        "pages": pages,
        "page_count": len(pages),
        "dpi": dpi,
        "success": True,
    }


@mcp.tool()
async def png_to_pdf(
    image_paths: list[str] | None = None,
    image_directory: str = "",
    output_path: str = "",
) -> dict:
    """Combine PNG images into a single PDF document.

    Provide either a list of image paths or a directory containing PNGs.
    When using a directory, images are sorted alphabetically (the naming
    convention from pdf_to_png -- page_001.png, page_002.png, etc. --
    preserves correct order automatically).

    Args:
        image_paths: Explicit ordered list of image file paths
        image_directory: Directory of PNG files to combine (alternative to image_paths)
        output_path: Path for the output PDF. Defaults to <directory>/combined.pdf

    Returns:
        Dictionary with the output PDF path and page count
    """
    import fitz  # pymupdf

    if image_paths:
        files = [Path(p) for p in image_paths]
    elif image_directory:
        src = Path(image_directory)
        if not src.is_dir():
            return {"error": f"Directory not found: {image_directory}"}
        files = sorted(src.glob("*.png"))
    else:
        return {"error": "Provide either image_paths or image_directory"}

    if not files:
        return {"error": "No PNG files found"}

    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        return {"error": f"Files not found: {', '.join(missing)}"}

    doc = fitz.open()
    for img_path in files:
        img_doc = fitz.open(str(img_path))
        rect = img_doc[0].rect
        pdf_bytes = img_doc.convert_to_pdf()
        img_doc.close()
        img_pdf = fitz.open("pdf", pdf_bytes)
        page = doc.new_page(width=rect.width, height=rect.height)
        page.show_pdf_page(page.rect, img_pdf, 0)
        img_pdf.close()

    out = Path(output_path) if output_path else (files[0].parent / "combined.pdf")
    doc.save(str(out))
    doc.close()

    return {
        "output_path": str(out),
        "page_count": len(files),
        "success": True,
    }


# ============================================================================
# PROMPTS - Reusable templates
# ============================================================================


@mcp.prompt()
def analyze_notebook_sources(notebook_id: str, focus_area: str = "main themes") -> str:
    """Generate a prompt for analyzing notebook sources.

    Args:
        notebook_id: ID of the notebook to analyze
        focus_area: What to focus on in the analysis

    Returns:
        Formatted prompt for analysis
    """
    return f"""Please analyze the sources in notebook {notebook_id}.

Focus on: {focus_area}

Provide:
1. Key themes and concepts
2. Important findings or insights
3. Connections between sources
4. Gaps or areas needing more research

Use the NotebookLM MCP tools to query the notebook for specific information."""


@mcp.prompt()
def research_topic_workflow(topic: str, depth: str = "comprehensive") -> str:
    """Generate a prompt for researching a topic using NotebookLM.

    Args:
        topic: Topic to research
        depth: Level of depth (quick, standard, comprehensive)

    Returns:
        Formatted workflow prompt
    """
    return f"""Research Workflow for: {topic}
Depth: {depth}

Steps:
1. Create a new notebook for this research topic
2. Add relevant sources (URLs, documents, or text)
3. Ask key questions to understand the topic:
   - What are the fundamental concepts?
   - What are current trends or developments?
   - What are the key challenges or debates?
4. Generate a quiz to test understanding
5. Create an audio overview for easy review

Use the NotebookLM MCP tools to execute this workflow."""


# ============================================================================
# SERVER EXECUTION
# ============================================================================

if __name__ == "__main__":
    transport = "stdio"
    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        transport = "streamable-http"

    mcp.run(transport=transport)
