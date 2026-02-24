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

You can authenticate multiple accounts by changing `NOTEBOOKLM_HOME` to a different directory for each (e.g. `~/.notebooklm-personal`, `~/.notebooklm-design`).

## Server Configuration

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

```bash
claude mcp add notebooklm -- python /path/to/notebooklm_mcp_server.py
```

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
