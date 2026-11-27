"""
NotebookLM Diet MCP Server
A lightweight MCP server exposing Google NotebookLM capabilities as
workflow-oriented tools for AI agents in Cursor, Claude Code, and
other MCP-compatible clients.

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
import inspect
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from weakref import WeakValueDictionary

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from notebooklm import AudioFormat, AudioLength, NotebookLMClient, QuizDifficulty, QuizQuantity
from notebooklm.paths import get_storage_path

ACTIVE_PROFILE_PATH = Path.home() / ".notebooklm-active.json"
_PROFILE_AWARE_STORAGE = "profile" in inspect.signature(get_storage_path).parameters

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


def _storage_path(home: Path) -> Path:
    """Resolve the CLI's storage layout without changing the running client's home."""
    legacy = home / "storage_state.json"
    if not _PROFILE_AWARE_STORAGE:
        return legacy

    # The SDK resolver only accepts a process-wide home. Read the target home's
    # default when resolving a different account for discovery or switching.
    profile = os.environ.get("NOTEBOOKLM_PROFILE")
    if not profile:
        with suppress(OSError, ValueError):
            config = json.loads((home / "config.json").read_text(encoding="utf-8"))
            if isinstance(config, dict) and isinstance(config.get("default_profile"), str):
                profile = config["default_profile"]
    profile = profile or "default"
    profiles = (home / "profiles").resolve()
    directory = (profiles / profile).resolve()
    if directory == profiles or not directory.is_relative_to(profiles):
        raise ValueError("Invalid SDK profile directory")
    storage = directory / legacy.name
    if profile == "default" and not storage.exists() and legacy.exists():
        return legacy
    return storage


def _read_active_profile() -> str | None:
    """Read the persisted active profile name, if any."""
    try:
        data = json.loads(ACTIVE_PROFILE_PATH.read_text(encoding="utf-8"))
        return data.get("active")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _write_active_profile(name: str) -> None:
    """Persist the active profile name."""
    with TemporaryDirectory(dir=ACTIVE_PROFILE_PATH.parent) as directory:
        staged = Path(directory) / "active.json"
        staged.touch(mode=0o600)
        staged.write_text(json.dumps({"active": name}, indent=2), encoding="utf-8")
        staged.replace(ACTIVE_PROFILE_PATH)


def _current_profile_name() -> str:
    """Determine the current profile name from persisted state or env."""
    saved = _read_active_profile()
    if saved:
        return saved

    home = os.environ.get("NOTEBOOKLM_HOME", "")
    if home:
        directory = Path(home).expanduser().resolve()
        for name in DEFAULT_PROFILES:
            if _resolve_profile_dir(name) == directory:
                return name
        name = directory.name.removeprefix(".notebooklm-")
        return name if _resolve_profile_dir(name) == directory else str(directory)

    return "personal"


# HTTP sessions share the SDK home and on-disk login files. Keep each lock
# alive while an operation holds or waits for it.
_account_locks: WeakValueDictionary[Path | None, asyncio.Lock] = WeakValueDictionary()


def _account_lock(profile: str | None = None) -> asyncio.Lock:
    """Lock a profile's login file, or the process-wide SDK home when omitted."""
    key = _resolve_profile_dir(profile).resolve() if profile is not None else None
    return _account_locks.setdefault(key, asyncio.Lock())


@dataclass
class AppContext:
    """Application context with a switchable NotebookLM client."""

    client: NotebookLMClient | None
    profile: str
    switch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_current(self, profile: str, client: NotebookLMClient | None) -> bool:
        return self.profile == profile and self.client is client


def _find_cli() -> Path | None:
    """Locate the notebooklm CLI executable in the current environment."""
    venv_dir = Path(sys.executable).parent
    for name in ("notebooklm", "notebooklm.exe"):
        p = venv_dir / name
        if p.exists():
            return p
    return None


LOGIN_TIMEOUT = 300


async def _kill_and_reap_login(process: subprocess.Popen, exited: asyncio.Future | None) -> None:
    process.kill()
    cancellation = None
    while process.returncode is None:
        try:
            if exited is not None and not exited.done():
                await asyncio.wait({exited})
            elif process.poll() is None:
                # No executor waiter is available. poll() reaps without blocking the loop.
                await asyncio.sleep(0.01)
        except asyncio.CancelledError as exc:
            # A cancellation during cleanup must also wait for the profile to be free.
            cancellation = exc
    if cancellation is not None:
        raise cancellation


async def _run_login(profile: str, ctx: Context | None = None) -> bool:
    """Launch notebooklm login for a profile and wait for completion."""
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
        with subprocess.Popen(
            [str(cli), "login"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as process:
            exited = None
            try:
                exited = asyncio.get_running_loop().run_in_executor(None, process.wait)
                done, _ = await asyncio.wait({exited}, timeout=LOGIN_TIMEOUT)
                if not done:
                    return False
                exited.result()
            finally:
                if process.returncode is None:
                    await _kill_and_reap_login(process, exited)
    except Exception:
        return False

    storage_file = _storage_path(target_dir)
    return process.returncode == 0 and storage_file.is_file()


async def _create_client(profile: str) -> NotebookLMClient:
    """Create and initialise a NotebookLM client for the given profile."""
    storage = _storage_path(_resolve_profile_dir(profile))
    client = await NotebookLMClient.from_storage(path=str(storage))
    if hasattr(client, "__aenter__"):
        await client.__aenter__()
    return client


async def _ensure_authenticated(
    app: "AppContext",
    ctx: Context,
) -> NotebookLMClient | None:
    """Return the client authenticated for the account selected at entry.

    Handles three cases:
    1. No client at all -- triggers login.
    2. Client exists but session is stale -- triggers re-login.
    3. Client exists and session is valid -- returns immediately.
    """
    profile = app.profile
    previous_client = app.client
    if previous_client is not None:
        try:
            await previous_client.notebooks.list()
            return previous_client if app.is_current(profile, previous_client) else None
        except Exception:
            pass

    if previous_client is None:
        await ctx.info(
            "No credentials found. Launching browser for authentication -- please sign into your Google account."
        )
    else:
        await ctx.info("Session expired. Launching browser for re-authentication...")

    async with _account_lock(profile):
        if not app.is_current(profile, previous_client):
            return None
        success = await _run_login(profile, ctx)
        if not success:
            await ctx.error(
                "Automatic re-authentication failed. Please run 'notebooklm login' manually in your terminal."
            )
            return None

        if not app.is_current(profile, previous_client):
            return None
        new_client = await _create_client(profile)
        if not app.is_current(profile, previous_client):
            with suppress(Exception):
                await new_client.__aexit__(None, None, None)
            return None

        app.client = new_client
        if previous_client is not None:
            with suppress(Exception):
                await previous_client.__aexit__(None, None, None)
    await ctx.info("Authentication successful. Resuming operation.")
    return new_client if app.is_current(profile, new_client) else None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage NotebookLM client lifecycle."""
    async with _account_lock():
        profile = _current_profile_name()
        profile_dir = _resolve_profile_dir(profile)
        os.environ["NOTEBOOKLM_HOME"] = str(profile_dir)
    storage_file = _storage_path(profile_dir)

    client = None
    if storage_file.is_file():
        try:
            client = await _create_client(profile)
        except Exception:
            client = None

    ctx = AppContext(client=client, profile=profile)
    try:
        yield ctx
    finally:
        if ctx.client is not None:
            with suppress(Exception):
                await ctx.client.__aexit__(None, None, None)


mcp = FastMCP("NotebookLM", lifespan=app_lifespan)
# HTTPX request logs and HTTP Core traces can contain signed artifact URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _omit_download_url(record: logging.LogRecord) -> bool:
    # Both SDK layouts include the host or signed URL in these records.
    return not str(record.msg).startswith(("Downloaded %s", "Download failed for %s"))


for _logger in ("notebooklm._artifacts", "notebooklm._artifact.downloads"):
    logging.getLogger(_logger).addFilter(_omit_download_url)


# ============================================================================
# RESOURCES
# ============================================================================


@mcp.resource("notebooklm://notebooks")
async def list_notebooks() -> str:
    """List all NotebookLM notebooks with IDs and titles."""
    # FastMCP treats a resource function with any parameters as a URI template.
    app = mcp.get_context().request_context.lifespan_context
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
# TOOLS -- Notebooks
# ============================================================================


@mcp.tool()
async def list_notebooks_tool(ctx: Context[ServerSession, AppContext]) -> dict:
    """List all NotebookLM notebooks.

    Returns a dictionary with notebook titles and IDs.
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info("Fetching notebooks...")
    notebooks = await app.client.notebooks.list()

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

    await ctx.info(f"Creating notebook: {title}")
    notebook = await app.client.notebooks.create(title)

    return {"id": notebook.id, "title": notebook.title, "success": True}


# ============================================================================
# TOOLS -- Sources
# ============================================================================


@mcp.tool()
async def list_sources(
    notebook_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """List all sources in a notebook.

    Args:
        notebook_id: ID of the notebook

    Returns:
        Dictionary with source count and source details
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    sources = await app.client.sources.list(notebook_id)
    return {
        "count": len(sources),
        "sources": [
            {
                "id": s.id,
                "title": s.title,
                "kind": getattr(s, "kind", None),
                "status": getattr(s, "status", None),
            }
            for s in sources
        ],
    }


@mcp.tool()
async def add_sources(
    notebook_id: str,
    sources: str,
    wait: bool = True,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Add multiple sources to a notebook in a single call.

    Each source is a dictionary with a "type" and "value" field.
    Supported types: "url", "text", "file".
    Text sources also require a "title" field.

    Args:
        notebook_id: ID of the notebook
        sources: JSON array of sources, e.g.
            [{"type": "url", "value": "https://example.com"},
             {"type": "text", "value": "Content here", "title": "My Notes"},
             {"type": "file", "value": "/path/to/document.pdf"}]
        wait: Whether to wait for processing to complete

    Returns:
        Dictionary with results for each source
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    try:
        parsed = json.loads(sources) if isinstance(sources, str) else sources
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in sources parameter: {e}"}

    if not isinstance(parsed, list):
        return {"error": "sources must be a JSON array"}

    results = []
    for i, src in enumerate(parsed):
        src_type = src.get("type", "").lower()
        value = src.get("value", "")
        await ctx.info(f"Adding source {i + 1}/{len(parsed)}: {src_type}")
        try:
            if src_type == "url":
                added = await app.client.sources.add_url(notebook_id, value, wait=wait)
            elif src_type == "text":
                title = src.get("title", f"Text source {i + 1}")
                added = await app.client.sources.add_text(notebook_id, title, value)
            elif src_type == "file":
                added = await app.client.sources.add_file(notebook_id, value, wait=wait)
            else:
                results.append({"index": i, "error": f"Unknown source type: {src_type}"})
                continue
            results.append({"index": i, "id": added.id, "title": added.title, "success": True})
        except Exception as e:
            results.append({"index": i, "error": str(e)})

    succeeded = sum(1 for r in results if r.get("success"))
    return {"total": len(parsed), "succeeded": succeeded, "results": results}


# ============================================================================
# TOOLS -- Chat
# ============================================================================


@mcp.tool()
async def ask_question(
    notebook_id: str,
    question: str,
    source_ids: str = "",
    conversation_id: str = "",
    persona: str = "",
    response_length: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Ask a question to a NotebookLM notebook and get an AI-generated answer.

    Args:
        notebook_id: ID of the notebook to query
        question: The question to ask
        source_ids: Comma-separated source IDs to restrict the query (optional)
        conversation_id: Continue an existing conversation thread (optional)
        persona: Set chat persona before asking (optional). Use a descriptive
            role like "tutor", "analyst", "concise summariser". Cleared if empty.
        response_length: Set response length before asking (optional).
            One of: short, medium, long. Cleared if empty.

    Returns:
        Dictionary with the answer and citation information
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}

    parsed_source_ids = [s.strip() for s in source_ids.split(",") if s.strip()] if source_ids else None

    if persona or response_length:
        configure_kwargs: dict = {}
        if persona:
            configure_kwargs["goal"] = "custom"
            configure_kwargs["custom_prompt"] = persona
        if response_length:
            configure_kwargs["response_length"] = response_length
        with suppress(Exception):
            await app.client.chat.configure(notebook_id, **configure_kwargs)

    await ctx.info(f"Querying notebook: {notebook_id}")
    await ctx.report_progress(0.5, 1.0, "Generating answer...")

    kwargs = {}
    if parsed_source_ids:
        kwargs["source_ids"] = parsed_source_ids
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


# ============================================================================
# TOOLS -- Artifacts
# ============================================================================


_DOWNLOAD_FORMATS = {
    "audio": ("m4a", "mp3", "wav"),
    "video": ("mp4",),
    "report": ("markdown",),
    "slide_deck": ("pdf",),
    "quiz": ("json", "markdown", "html"),
    "flashcards": ("json", "markdown", "html"),
    "infographic": ("png",),
    "data_table": ("csv",),
    "mind_map": ("json",),
}


class _ArtifactError(ValueError):
    """An artifact diagnostic suitable for the tool response."""


def _generation_failure(message: str, detail: str | None) -> _ArtifactError:
    # Status text comes from the backend and can include download credentials.
    if detail and "quota" in detail.lower():
        message += " (quota)"
    return _ArtifactError(message)


def _artifact_error(operation: str, error: Exception) -> str:
    # SDK versions may wrap the HTTP error. Only its numeric status is public.
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        status = getattr(getattr(cause, "response", None), "status_code", None)
        if isinstance(status, int):
            return f"{operation} failed (HTTP {status})"
        cause = cause.__cause__ or cause.__context__
    if isinstance(error, _ArtifactError) and "://" not in str(error):
        return f"{operation} failed: {error}"
    return f"{operation} failed ({type(error).__name__})"


def _download_format(kind: str, path: str, requested: str) -> str:
    formats = _DOWNLOAD_FORMATS.get(kind)
    if not formats:
        raise _ArtifactError(f"Unsupported artifact type: {kind}")
    aliases = {"md": "markdown"}
    if kind == "audio":
        aliases["mp4"] = "m4a"
    suffix = Path(path).suffix.lower().lstrip(".")
    extension = aliases.get(suffix, suffix)
    requested = requested.lower()
    fmt = aliases.get(requested, requested) or extension or formats[0]
    if fmt not in formats:
        raise _ArtifactError(f"Unsupported format for {kind}: {fmt}. Use: {', '.join(formats)}")
    if extension and extension != fmt:
        raise _ArtifactError(f"File extension .{suffix} does not match the requested {fmt} format")
    return fmt


def _generation_option(enum_type, value: str, **aliases):
    name = value.lower().replace("-", "_")
    try:
        return enum_type[aliases.get(name, name).upper()]
    except KeyError as e:
        raise _ArtifactError(f"Unsupported {enum_type.__name__} option: {value}") from e


def _media_format(path: Path) -> str | None:
    import av

    # Limit probing to local media containers. A playlist must not open secondary inputs.
    options = {"format_whitelist": "wav,mp3,mov,aac", "protocol_whitelist": ""}
    with path.open("rb") as stream, av.open(stream, options=options) as media:
        audio = media.streams.audio
        video = [s for s in media.streams.video if not s.disposition & av.stream.Disposition.attached_pic]
        native = media.format.name
        if "mp4" in native.split(","):
            compatible = media.metadata.get("compatible_brands", "")
            brands = [compatible[i : i + 4] for i in range(0, len(compatible), 4)]
            brands.append(media.metadata.get("major_brand", ""))
            if not any(brand.startswith(("iso", "mp4", "M4A", "M4V", "dash", "avc1", "MSNV")) for brand in brands):
                return None
            native = "mp4" if video else "m4a"
        elif native == "mp3" and (not audio or audio[0].codec_context.codec.id != av.Codec("mp3", "r").id):
            return None
        tracks = video if native == "mp4" else audio
        if tracks and next(media.decode(tracks[0]), None) is not None:
            return native
    return None


def _validate_binary(path: Path, fmt: str) -> None:
    import fitz

    message = f"Downloaded file is not a readable {fmt.upper()} file"
    # Release parser tracebacks and their file handles before cleaning up the staged file.
    with suppress(OSError, ValueError, RuntimeError):
        if fmt == "pdf":
            with fitz.open(path) as doc:
                if doc.is_pdf and doc.page_count and doc.load_page(0) is not None:
                    return
        elif fmt == "png":
            with path.open("rb") as stream:
                if stream.read(8) == b"\x89PNG\r\n\x1a\n" and fitz.Pixmap(str(path)).width:
                    return
        else:
            native = _media_format(path)
            if native == fmt:
                return
            if native == "aac":
                message = f"NotebookLM supplied ADTS AAC. Export as {fmt.upper()} is unsupported for this recording."
            elif native in _DOWNLOAD_FORMATS["audio"] and fmt in _DOWNLOAD_FORMATS["audio"]:
                message += f". NotebookLM supplied {native} audio. Use the {native} output format."
    raise _ArtifactError(message)


_BINARY_ARTIFACTS = {"audio", "video", "slide_deck", "infographic"}


def _check_download_credentials(kind: str) -> None:
    if kind in _BINARY_ARTIFACTS and not _PROFILE_AWARE_STORAGE and "NOTEBOOKLM_AUTH_JSON" in os.environ:
        raise _ArtifactError("Unset NOTEBOOKLM_AUTH_JSON so the SDK downloads with the selected profile's cookies")


@asynccontextmanager
async def _legacy_media_home(profile: str) -> AsyncIterator[None]:
    """Keep SDK 0.3.2's cookie lookup on this client's home until the download exits."""
    async with _account_lock():
        previous = os.environ.get("NOTEBOOKLM_HOME")
        os.environ["NOTEBOOKLM_HOME"] = str(_resolve_profile_dir(profile))
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("NOTEBOOKLM_HOME", None)
            else:
                os.environ["NOTEBOOKLM_HOME"] = previous


async def _download_artifact(
    app: AppContext, client: NotebookLMClient, notebook_id: str, artifact_id: str, kind: str, path: str, fmt: str
):
    if not artifact_id:
        raise _ArtifactError("A download requires an artifact ID")
    _check_download_credentials(kind)
    download = getattr(client.artifacts, f"download_{kind}")
    options = {"output_format": fmt} if kind in ("quiz", "flashcards") else {}
    binary = kind in _BINARY_ARTIFACTS
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as directory:
        staged = Path(directory) / f"download.{fmt}"
        profile = app.profile
        home = _legacy_media_home(profile) if binary and not _PROFILE_AWARE_STORAGE else nullcontext()
        async with _account_lock(profile), app.switch_lock, home:
            if not app.is_current(profile, client):
                raise _ArtifactError("Active account changed before download")
            await download(notebook_id, str(staged), artifact_id=artifact_id, **options)
        if binary:
            _validate_binary(staged, fmt)
        if os.name == "posix":
            staged.chmod(0o600)
        staged.replace(destination)


@mcp.tool()
async def generate_and_download(
    notebook_id: str,
    artifact_type: str,
    output_path: str,
    instructions: str = "",
    audio_format: str = "deep-dive",
    audio_length: str = "medium",
    quiz_quantity: str = "standard",
    quiz_difficulty: str = "medium",
    quiz_output_format: str = "json",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Generate an artifact and download it in a single call.

    Supports: report, audio, slide_deck, quiz, infographic.

    Args:
        notebook_id: ID of the notebook
        artifact_type: One of: report, audio, slide_deck, quiz, infographic
        output_path: Path where to save the downloaded file. Use the correct
            extension: .md (report), .pdf (slide_deck), .png (infographic),
            .m4a (native audio), .json/.md/.html (quiz, based on quiz_output_format).
            If NotebookLM supplies WAV or MP3 audio, use the matching extension.
        instructions: Custom instructions for generation (optional).
            For slide decks, pass a design template here to control visual style.
        audio_format: Audio format: deep-dive, brief, critique, debate
        audio_length: Audio length: short, medium, long
        quiz_quantity: Quiz quantity: few, standard, more
        quiz_difficulty: Quiz difficulty: easy, medium, hard
        quiz_output_format: Quiz download format: json, markdown, html

    Returns:
        Dictionary with generation and download status
    """
    app = ctx.request_context.lifespan_context
    client = await _ensure_authenticated(app, ctx)
    if client is None:
        return {"error": "Authentication failed or the active account changed."}

    artifact_type = artifact_type.lower().replace(" ", "_")
    valid_types = {"report", "audio", "slide_deck", "quiz", "infographic"}
    if artifact_type not in valid_types:
        return {"error": f"Unsupported artifact type: {artifact_type}. Use one of: {', '.join(sorted(valid_types))}"}

    artifacts = client.artifacts
    await ctx.info(f"Generating {artifact_type}...")

    artifact_id = None
    try:
        if app.client is not client:
            raise _ArtifactError("Active account changed before generation")
        fmt = _download_format(artifact_type, output_path, quiz_output_format if artifact_type == "quiz" else "")
        _check_download_credentials(artifact_type)
        gen_kwargs: dict = {}
        if instructions:
            gen_kwargs["instructions"] = instructions

        if artifact_type == "audio":
            gen_kwargs["audio_format"] = _generation_option(AudioFormat, audio_format)
            gen_kwargs["audio_length"] = _generation_option(AudioLength, audio_length, medium="default")
            status = await artifacts.generate_audio(notebook_id, **gen_kwargs)
        elif artifact_type == "report":
            report_kwargs: dict = {}
            if instructions:
                report_kwargs["custom_prompt"] = instructions
            status = await artifacts.generate_report(notebook_id, **report_kwargs)
        elif artifact_type == "slide_deck":
            status = await artifacts.generate_slide_deck(notebook_id, **gen_kwargs)
        elif artifact_type == "quiz":
            gen_kwargs["quantity"] = _generation_option(QuizQuantity, quiz_quantity, few="fewer")
            gen_kwargs["difficulty"] = _generation_option(QuizDifficulty, quiz_difficulty)
            status = await artifacts.generate_quiz(notebook_id, **gen_kwargs)
        elif artifact_type == "infographic":
            status = await artifacts.generate_infographic(notebook_id, **gen_kwargs)

        artifact_id = status.task_id or None
        if not artifact_id or status.is_failed:
            raise _generation_failure("Artifact generation did not start", status.error)
        await ctx.info(f"Waiting for {artifact_type} generation to complete...")
        completed = await artifacts.wait_for_completion(notebook_id, artifact_id)
        if not completed.is_complete or completed.task_id != artifact_id:
            raise _generation_failure(f"Artifact {artifact_id} did not complete", completed.error)

        await ctx.info(f"Downloading {artifact_type} to {output_path}...")
        await _download_artifact(app, client, notebook_id, artifact_id, artifact_type, output_path, fmt)

        return {
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
            "output_path": output_path,
            "success": True,
        }

    except Exception as e:
        return {
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
            "error": _artifact_error("generate_and_download", e),
        }


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
async def export_artifact(
    notebook_id: str,
    artifact_id: str,
    output_path: str,
    export_format: str = "",
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Export an artifact to a file in the requested format.

    Uses the appropriate export method based on the artifact type.

    Args:
        notebook_id: ID of the notebook
        artifact_id: ID of the artifact to export
        output_path: Path where to save the exported file
        export_format: File format. Slide decks use PDF, reports Markdown, infographics PNG,
            data tables CSV, mind maps JSON, video MP4 and audio its native encoding.
            Quizzes and flashcards support JSON, Markdown and HTML.
            Leave empty to infer from output_path or use the default.

    Returns:
        Dictionary with export status
    """
    app = ctx.request_context.lifespan_context
    client = await _ensure_authenticated(app, ctx)
    if client is None:
        return {"error": "Authentication failed or the active account changed."}

    await ctx.info(f"Exporting artifact to: {output_path}")
    try:
        if app.client is not client:
            raise _ArtifactError("Active account changed before export")
        artifact = await client.artifacts.get(notebook_id, artifact_id)
        if artifact is None:
            return {"error": f"Artifact {artifact_id} not found"}
        kind = artifact.kind.value
        fmt = _download_format(kind, output_path, export_format)
        await _download_artifact(app, client, notebook_id, artifact_id, kind, output_path, fmt)
        return {"artifact_id": artifact_id, "output_path": output_path, "success": True}
    except Exception as e:
        return {"artifact_id": artifact_id, "error": _artifact_error("export_artifact", e)}


# ============================================================================
# TOOLS -- Research
# ============================================================================


RESEARCH_TIMEOUT = 120


def _research_field(response, name: str):
    # SDK 0.3.2 returns dicts. Newer releases expose the same fields as attributes.
    return response.get(name) if isinstance(response, dict) else getattr(response, name, None)


async def _poll_research(research, notebook_id: str, task_id: str) -> Sequence:
    # SDK 0.3.2 only accepts notebook_id and reports the most recent task.
    kwargs = {"task_id": task_id} if "task_id" in inspect.signature(research.poll).parameters else {}
    for _ in range(60):
        await asyncio.sleep(2)
        result = await research.poll(notebook_id, **kwargs)
        polled_id = _research_field(result, "task_id")
        if not polled_id:
            raise ValueError(f"Research task {task_id} not found")
        if polled_id != task_id:
            raise ValueError(f"Expected research task {task_id}, received {polled_id}")
        status = _research_field(result, "status")
        if status == "completed":
            return result["sources"] if isinstance(result, dict) else result.sources
        if status not in ("in_progress", "pending"):
            raise ValueError(f"Research task {task_id} returned status {status}")
    raise asyncio.TimeoutError


@mcp.tool()
async def research_and_import(
    notebook_id: str,
    query: str,
    source: str = "web",
    max_results: int = 5,
    ctx: Context[ServerSession, AppContext] = None,
) -> dict:
    """Research a topic and import the results as notebook sources in a single call.

    Starts a research task, polls until completion, and automatically imports
    the top results. A successful call reports submitted and acknowledged counts.
    When the SDK acknowledges only part of a batch, imported is null.

    Args:
        notebook_id: ID of the notebook
        query: Research query
        source: Where to search: "web" or "drive"
        max_results: Maximum number of results to import

    Returns:
        Dictionary with research results and import status
    """
    app = ctx.request_context.lifespan_context
    profile = app.profile
    client = await _ensure_authenticated(app, ctx)
    if app.profile != profile:
        return {"query": query, "imported": 0, "error": "Active research profile changed before start"}
    if client is None:
        return {"error": "Authentication failed. Please check the logs."}

    await ctx.info(f"Researching: {query}")
    if max_results < 0:
        return {"query": query, "imported": 0, "error": "max_results must be nonnegative"}
    try:
        if not app.is_current(profile, client):
            return {"query": query, "imported": 0, "error": "Active research profile changed before start"}
        task = await client.research.start(notebook_id, query, source=source)
        task_id = _research_field(task, "task_id")
        if not task_id:
            return {"query": query, "imported": 0, "error": "Research start returned no task ID"}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESEARCH_TIMEOUT
        poll = asyncio.create_task(_poll_research(client.research, notebook_id, task_id))
        try:
            done, _ = await asyncio.wait({poll}, timeout=RESEARCH_TIMEOUT)
            if not done:
                raise asyncio.TimeoutError
        except BaseException:
            poll.cancel()
            await asyncio.gather(poll, return_exceptions=True)
            raise
        results = poll.result()
        # A poll can return before the event loop delivers its overdue timeout.
        if loop.time() >= deadline:
            raise asyncio.TimeoutError
        to_import = results[:max_results]
        if to_import:
            await ctx.info(f"Importing {len(to_import)} sources...")
            if loop.time() >= deadline:
                raise asyncio.TimeoutError
    except asyncio.TimeoutError:
        return {"query": query, "imported": 0, "error": "Research timed out"}
    except Exception as e:
        return {"query": query, "imported": 0, "error": str(e)}

    try:
        if to_import:
            if not app.is_current(profile, client):
                return {"query": query, "imported": 0, "error": "Active research profile changed before import"}
            import_result = await client.research.import_sources(notebook_id, task_id=task_id, sources=to_import)
        else:
            import_result = {"imported": 0}

        submitted = len(to_import)
        acknowledged = len(import_result) if submitted else 0
        return {
            "query": query,
            "results_found": len(results),
            "submitted": submitted,
            "acknowledged": acknowledged,
            "imported": acknowledged if submitted == acknowledged else None,
            "import_details": import_result,
            "success": True,
        }

    except Exception as e:
        return {"query": query, "error": str(e)}


# ============================================================================
# TOOLS -- Account Management
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

    available = {}
    for name, suffix in DEFAULT_PROFILES.items():
        path = Path.home() / suffix
        if path.is_dir() and _storage_path(path).is_file():
            available[name] = str(path)

    for p in Path.home().glob(".notebooklm-*"):
        if p.is_dir() and _storage_path(p).is_file():
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

    A switch waits for an active download to finish and persists across
    server restarts. Use get_account_info to see available profiles.

    Args:
        profile: Profile name to switch to (e.g. 'work', 'personal',
                 'design', or any custom name matching ~/.notebooklm-<name>)

    Returns:
        Dictionary confirming the switch with the new profile details.
    """
    app = ctx.request_context.lifespan_context
    target_dir = _resolve_profile_dir(profile)
    storage_file = _storage_path(target_dir)

    if not storage_file.is_file():
        return {
            "success": False,
            "error": f"No credentials found for profile '{profile}' at {target_dir}",
            "hint": (f"Authenticate first by running:\n  NOTEBOOKLM_HOME={target_dir} notebooklm login"),
        }

    await ctx.info(f"Switching from '{app.profile}' to '{profile}'...")
    async with app.switch_lock:
        new_client = await _create_client(profile)
        try:
            async with _account_lock():
                _write_active_profile(profile)
                os.environ["NOTEBOOKLM_HOME"] = str(target_dir)
        except BaseException:
            with suppress(Exception):
                await new_client.__aexit__(None, None, None)
            raise

        previous = app.profile
        previous_client = app.client
        app.client = new_client
        app.profile = profile
        if previous_client is not None:
            with suppress(Exception):
                await previous_client.__aexit__(None, None, None)

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
    async with _account_lock(profile):
        if _storage_path(target_dir).is_file():
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
# TOOLS -- Utilities
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
    image_paths: str = "",
    image_directory: str = "",
    output_path: str = "",
) -> dict:
    """Combine PNG images into a single PDF document.

    Provide either a list of image paths or a directory containing PNGs.
    When using a directory, images are sorted alphabetically (the naming
    convention from pdf_to_png -- page_001.png, page_002.png, etc. --
    preserves correct order automatically).

    Args:
        image_paths: Comma-separated image file paths, or a JSON array of paths
        image_directory: Directory of PNG files to combine (alternative to image_paths)
        output_path: Path for the output PDF. Defaults to <directory>/combined.pdf

    Returns:
        Dictionary with the output PDF path and page count
    """
    import fitz  # pymupdf

    resolved_paths = None
    if image_paths:
        image_paths = image_paths.strip()
        if image_paths.startswith("["):
            try:
                resolved_paths = json.loads(image_paths)
            except json.JSONDecodeError as e:
                return {"error": f"Invalid JSON in image_paths: {e}"}
        else:
            resolved_paths = [p.strip() for p in image_paths.split(",") if p.strip()]

    if resolved_paths:
        files = [Path(p) for p in resolved_paths]
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
# PROMPTS
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


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STYLE_SECTIONS = {"corporate": "## Corporate", "educational": "## Educational", "creative": "## Creative"}


def _load_slide_template(style: str) -> str:
    """Extract a single template section from slide_styles.md."""
    styles_file = _TEMPLATE_DIR / "slide_styles.md"
    if not styles_file.is_file():
        return f"Template file not found at {styles_file}"

    content = styles_file.read_text(encoding="utf-8")
    header = _STYLE_SECTIONS.get(style.lower())
    if not header:
        return f"Unknown style '{style}'. Available: {', '.join(_STYLE_SECTIONS)}"

    start = content.find(header)
    if start == -1:
        return f"Style '{style}' not found in template file"

    after_header = content[start + len(header) :]
    for other in _STYLE_SECTIONS.values():
        if other != header:
            end = after_header.find(other)
            if end != -1:
                after_header = after_header[:end]
                break

    return (header + after_header).rstrip("-\n ").strip()


@mcp.prompt()
def generate_styled_slides(
    notebook_id: str,
    style: str = "corporate",
    output_path: str = "slides.pdf",
) -> str:
    """Generate a slide deck using a bundled design template.

    Reads the selected template from templates/slide_styles.md and
    provides complete instructions for generating and downloading
    a styled slide deck.

    Args:
        notebook_id: ID of the notebook to generate slides from
        style: Design template to use: corporate, educational, or creative
        output_path: Where to save the downloaded PDF

    Returns:
        Formatted prompt with the template and tool call instructions
    """
    template = _load_slide_template(style)

    return f"""Generate a styled slide deck from notebook {notebook_id}.

Style: {style}

Call the generate_and_download tool with these parameters:
  notebook_id: {notebook_id}
  artifact_type: slide_deck
  output_path: {output_path}
  instructions: (paste the design template below)

--- DESIGN TEMPLATE ---
{template}
--- END TEMPLATE ---

After downloading, consider using pdf_to_png to split the PDF into
individual page images for review."""


# ============================================================================
# SERVER EXECUTION
# ============================================================================

if __name__ == "__main__":
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"

    mcp.run(transport=transport)
