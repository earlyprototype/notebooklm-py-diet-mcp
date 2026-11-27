# Setup Guide

Detailed instructions for installing, configuring, and testing the notebooklm-py-diet-mcp server.

For a quick-start overview, see the [README](../README.md).

## Prerequisites

- Python 3.10 or later
- A Google account with access to [NotebookLM](https://notebooklm.google.com/)
- [Cursor](https://cursor.com/), [Claude Code](https://docs.claude.com/en/docs/claude-code), or another MCP-compatible client

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/earlyprototype/notebooklm-py-diet-mcp.git
cd notebooklm-py-diet-mcp
```

### 2. Create a virtual environment and install

```bash
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# macOS / Linux
source venv/bin/activate

# Recommended: editable install with dev dependencies
pip install -e ".[dev]"

# Alternative: install from requirements.txt
pip install -r requirements.txt
```

### 3. Install Playwright (required for browser-based login and auto-reauthentication)

```bash
playwright install chromium
```

Playwright is included as a dependency via `notebooklm-py[browser]`, but the Chromium browser binary must be installed separately.

### 4. Authenticate with Google NotebookLM

```bash
# Windows (PowerShell)
$env:NOTEBOOKLM_HOME = "$HOME\.notebooklm-work"

# macOS / Linux
export NOTEBOOKLM_HOME=~/.notebooklm-work

# Login (opens a browser -- sign into your Google account)
notebooklm login

# Verify authentication
notebooklm list
```

Use a separate `NOTEBOOKLM_HOME` for each account, such as `~/.notebooklm` for personal and `~/.notebooklm-design` for design. SDK 0.8.6 stores a fresh default session at `profiles/default/storage_state.json` inside that directory. The SDK also recognizes the older `storage_state.json` at the account root.

If `NOTEBOOKLM_AUTH_JSON` is set, the server uses it at startup, even when the account directory contains a saved session. Use `switch_account` to select a file-backed session at runtime. Unset `NOTEBOOKLM_AUTH_JSON` before restarting to keep using that saved account. `get_account_info` reports the authentication source and lists accounts with usable storage paths. If the active SDK profile is invalid, you can still use these tools to select another account.

## Server Configuration

If an existing configuration uses the `notebooklm-mcp` command, reinstall the diet package with `pip install -e .` and replace the command with the virtual environment's `bin/notebooklm-diet-mcp` or `Scripts\notebooklm-diet-mcp.exe` on Windows. Restart the MCP client and confirm that `research_and_import` appears in its tool list. The SDK uses `notebooklm-mcp` for its own server. The Python script configurations below also launch the diet server.

If you also use the SDK server, upgrading the diet package can remove the old shared executable. To restore it, check the SDK version with `pip show notebooklm-py`, then run `pip install --force-reinstall --no-deps "notebooklm-py==<installed-version>"` in the same environment.

### Cursor

Add the following to your `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "<path-to-venv>/python",
      "args": [
        "<path-to-repo>/notebooklm_mcp_server.py"
      ]
    }
  }
}
```

**Windows example:**

```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "C:\\Users\\You\\projects\\notebooklm-py-diet-mcp\\venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\You\\projects\\notebooklm-py-diet-mcp\\notebooklm_mcp_server.py"
      ]
    }
  }
}
```

No `NOTEBOOKLM_HOME` environment variable is needed in the configuration -- the server manages account profiles internally via the `switch_account` and `get_account_info` tools.

Restart Cursor after saving.

### Claude Code

The repository ships a project-scoped `.mcp.json`, so opening the repository folder in Claude Code is enough -- it prompts you to approve the `notebooklm` server on first launch. It resolves the interpreter as `venv/bin/python` relative to the repository root, matching the virtual environment created in step 2.

Override that path with the `NOTEBOOKLM_MCP_PYTHON` environment variable when your interpreter lives elsewhere -- notably on Windows:

```powershell
# Windows (PowerShell)
$env:NOTEBOOKLM_MCP_PYTHON = "venv\Scripts\python.exe"
```

To register the server globally instead, so it is available from any project, use absolute paths:

```bash
# macOS / Linux
claude mcp add notebooklm -- /path/to/repo/venv/bin/python /path/to/repo/notebooklm_mcp_server.py

# Windows (PowerShell)
claude mcp add notebooklm -- C:\path\to\repo\venv\Scripts\python.exe C:\path\to\repo\notebooklm_mcp_server.py
```

Point the command at the virtual environment's interpreter, not a bare `python` -- the ambient interpreter will not have `notebooklm-py` or the MCP SDK installed and the server will fail to start.

Confirm registration with `claude mcp list`.

### HTTP Transport (MCP Inspector or remote access)

```bash
python notebooklm_mcp_server.py --http
```

Connect your client to `http://localhost:8000/mcp`.

## Testing the Server

### Running the test suite

```bash
pip install -e ".[dev]"
pytest
```

All tests are CI-safe and require no Google credentials.

### Standalone test

```bash
# stdio mode (default, for Cursor)
python notebooklm_mcp_server.py

# HTTP mode (for Inspector)
python notebooklm_mcp_server.py --http
```

### MCP Inspector (optional, recommended)

```bash
# Terminal 1: start the server
python notebooklm_mcp_server.py --http

# Terminal 2: start the inspector
npx @modelcontextprotocol/inspector
```

Open the Inspector UI and connect to `http://localhost:8000/mcp`.

### Integration tests via Cursor

Once configured, try these prompts in Cursor:

**List notebooks:**
> "List my NotebookLM notebooks"

**Query a notebook:**
> "Ask the Research notebook: what are the key findings?"

**Add sources:**
> "Add these URLs to my Project notebook: https://en.wikipedia.org/wiki/Digital_fabrication, https://example.com/article"

**Generate content:**
> "Generate a podcast overview for the Strategy notebook and download it"

**Research:**
> "Research 'digital twins' and import the results into my Research notebook"

## Troubleshooting

### MCP server not connecting

1. Verify the Python path in `mcp.json` points to the correct virtual environment
2. Verify the server script path is correct
3. Restart Cursor completely
4. Check Cursor logs: Help > Show Logs

### Authentication errors

The server handles expired sessions automatically. On the first tool call, if the session is invalid, it will launch a browser for re-authentication. If automatic re-authentication fails:

1. Verify you are authenticated: `notebooklm list`
2. Re-authenticate manually: `notebooklm login`
3. Check that `~/.notebooklm-active.json` points to a valid profile

### Import errors

```bash
# Ensure dependencies are installed in the correct environment
pip install -e ".[dev]"
```

## Development and Extension

### Adding a new tool

```python
@mcp.tool()
async def your_new_tool(
    param1: str,
    param2: int = 10,
    ctx: Context[ServerSession, AppContext] = None
) -> dict:
    """Tool description for AI agents.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Dictionary with results
    """
    app = ctx.request_context.lifespan_context
    if not await _ensure_authenticated(app, ctx):
        return {"error": "Authentication failed. Please check the logs."}
    client = app.client

    result = await client.some_method(param1, param2)

    return {
        "success": True,
        "result": result
    }
```

### Verbose logging

```bash
# Windows (PowerShell)
$env:MCP_DEBUG = "1"
python notebooklm_mcp_server.py

# macOS / Linux
MCP_DEBUG=1 python notebooklm_mcp_server.py
```

## Performance Characteristics

| Operation | Expected Duration | Notes |
|---|---|---|
| List notebooks | < 1 second | Fast, suitable for frequent calls |
| Create notebook | 1--2 seconds | Single network call |
| Add sources (per source) | 5--15 seconds | Depends on content size and processing |
| Ask question | 2--5 seconds | AI generation time |
| Generate and download audio | 30--120 seconds | Long-running; progress is reported |
| Generate and download report | 30--90 seconds | Moderate to long duration |
| Generate and download slides | 30--90 seconds | Moderate to long duration |
| Research and import | 10--60 seconds | Depends on query complexity |

## Security Considerations

- Credentials are stored in the user's home directory (`~/.notebooklm-*`) and are excluded from version control via `.gitignore`
- No credentials are embedded in the server code
- The server is stateless between requests; session state lives in the credential files
- Notebook content may be sensitive -- access is scoped to the authenticated Google account
- Multi-user deployments require separate credential directories per user
