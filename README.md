# notebooklm-py-diet-mcp

A lightweight [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server for [Google NotebookLM](https://notebooklm.google.com/) -- designed for practical use with AI agents in [Cursor](https://cursor.com/), [Claude Code](https://docs.claude.com/en/docs/claude-code), and other MCP-compatible clients.

Built on top of [**notebooklm-py**](https://github.com/teng-lin/notebooklm-py) by [Teng Lin](https://github.com/teng-lin) -- the unofficial Python API for Google NotebookLM.

> **Unofficial** -- This project uses undocumented Google APIs via notebooklm-py. It is not affiliated with Google. APIs may change without notice. Best suited for research, prototyping, and personal productivity workflows.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)

## Why Diet?

Standard MCP servers for NotebookLM expose 70+ individual tools. This creates token overhead, increases latency, and can overwhelm the model's tool selection. The diet server packages the same capabilities into **14 workflow-oriented tools** that cover the full feature set through composite operations.

For full SDK parity (72 individual tools), see [notebooklm-py-MCP](https://github.com/earlyprototype/notebooklm-py-MCP).

## Tools (14)

| Tool | Description |
|------|-------------|
| `list_notebooks_tool` | List all notebooks with IDs and titles |
| `create_notebook` | Create a new notebook |
| `list_sources` | List all sources in a notebook |
| `add_sources` | Add multiple sources (URL, text, file) in a single call |
| `ask_question` | Query a notebook with optional persona, source filtering, and threading |
| `generate_and_download` | Generate and download an artifact in one step (report, audio, slide deck, quiz, infographic) |
| `list_artifacts` | List artifacts in a notebook |
| `export_artifact` | Export an artifact to a file |
| `research_and_import` | Research a topic and import results as sources automatically |
| `get_account_info` | Show the active account and available profiles |
| `switch_account` | Switch to a different Google account profile |
| `create_profile` | Create a new account profile and launch browser sign-in |
| `pdf_to_png` | Convert a PDF to individual PNG images (one per page) |
| `png_to_pdf` | Combine PNG images into a single PDF document |

### MCP Resources

| URI | Description |
|-----|-------------|
| `notebooklm://notebooks` | List all notebooks (read-only) |
| `notebooklm://notebook/{id}` | Notebook details including sources |

### MCP Prompts

| Prompt | Description |
|--------|-------------|
| `analyze_notebook_sources` | Template for analysing notebook sources by theme |
| `research_topic_workflow` | Guided research workflow using NotebookLM tools |

### Bundled Templates

The `templates/slide_styles.md` file contains three ready-to-use slide design templates (Corporate, Educational, Creative). Pass any template's contents as the `instructions` parameter to `generate_and_download` when creating slide decks.

## Prerequisites

- Python 3.10 or later
- A Google account with access to [NotebookLM](https://notebooklm.google.com/)
- [Cursor](https://cursor.com/) or another MCP-compatible client

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/earlyprototype/notebooklm-py-diet-mcp.git
cd notebooklm-py-diet-mcp
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# macOS / Linux
source venv/bin/activate

pip install -e ".[dev]"
```

Alternatively, install from `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 3. Install Playwright (required for first-time login and auto-reauthentication)

```bash
playwright install chromium
```

### 4. Authenticate with Google NotebookLM

```bash
# Set the account profile directory
# Windows (PowerShell)
$env:NOTEBOOKLM_HOME = "$HOME\.notebooklm-work"

# macOS / Linux
export NOTEBOOKLM_HOME=~/.notebooklm-work

# Login (opens a browser window -- select your Google account)
notebooklm login

# Verify
notebooklm list
```

## Configuration

### Cursor

Add the following to your `.cursor/mcp.json` file:

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

Replace the placeholder paths with your actual paths. The server manages account profiles internally -- no `NOTEBOOKLM_HOME` environment variable is needed. Use `switch_account` and `get_account_info` to manage profiles at runtime.

Restart Cursor after saving the configuration.

### Claude Code

```bash
claude mcp add notebooklm -- python /path/to/notebooklm_mcp_server.py
```

### HTTP Transport (for MCP Inspector or remote access)

```bash
python notebooklm_mcp_server.py --http
```

Then connect your client to `http://localhost:8000/mcp`.

## Multiple Google Accounts

Each Google account is stored in a separate directory. Set `NOTEBOOKLM_HOME` to switch between them:

```bash
# Authenticate different accounts
NOTEBOOKLM_HOME=~/.notebooklm-work notebooklm login      # Work account
NOTEBOOKLM_HOME=~/.notebooklm notebooklm login            # Personal account
NOTEBOOKLM_HOME=~/.notebooklm-design notebooklm login     # Another account
```

The `get_account_info` tool shows the currently active profile and available alternatives. Use `switch_account` to change at runtime without restarting.

## Usage Examples

Once configured, you can interact with NotebookLM directly from your AI agent:

**List notebooks:**
> "List my NotebookLM notebooks"

**Query a knowledge base:**
> "Ask the Strategy notebook: what are our key objectives for 2026?"

**Set a persona and ask:**
> "As a strategy analyst, summarise the key risks in my Research notebook"

**Add multiple sources at once:**
> "Add these URLs to my Research notebook: https://example.com/article1, https://example.com/article2"

**Generate and download content:**
> "Generate a podcast overview for the Project notebook and save it"
> "Generate a report from the Strategy notebook and download it as PDF"
> "Create an infographic from the Training notebook"

**Research and import:**
> "Research 'digital fabrication trends' and import the top results into my Research notebook"

**Generate a styled slide deck:**
> "Generate a slide deck for the Strategy notebook using the Corporate template"

## Project Structure

```
notebooklm-py-diet-mcp/
  notebooklm_mcp_server.py   # MCP server (14 tools, resources, prompts)
  pyproject.toml              # Python packaging and tool configuration
  requirements.txt            # Convenience dependency file
  INSTRUCTIONS.md             # Context injected into the LLM when loaded
  LICENSE                     # MIT licence
  README.md                   # This file
  tests/
    conftest.py               # Shared test fixtures and mocks
    test_helpers.py            # Unit tests for helper functions
    test_tools.py              # Mock-based tool tests
    test_lifespan.py           # Server startup scenario tests
  templates/
    slide_styles.md           # Bundled slide design templates
  docs/
    setup.md                  # Detailed setup guide
```

## Development

### Running tests

```bash
pip install -e ".[dev]"
pytest
```

### Linting

```bash
ruff check .
ruff format .
```

## INSTRUCTIONS.md

The `INSTRUCTIONS.md` file is loaded by MCP clients alongside the server and provides the LLM with usage context -- workflow patterns, error handling guidance, and tool conventions. Place it next to `notebooklm_mcp_server.py` or in the MCP server metadata directory used by your client.

## Acknowledgements

This project would not exist without [**notebooklm-py**](https://github.com/teng-lin/notebooklm-py) by [Teng Lin](https://github.com/teng-lin) and contributors. It provides the complete Python API for Google NotebookLM that this MCP server wraps.

- [notebooklm-py on GitHub](https://github.com/teng-lin/notebooklm-py)
- [notebooklm-py on PyPI](https://pypi.org/project/notebooklm-py/)

The MCP server is built using the [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk) by Anthropic.

## Licence

MIT -- see [LICENSE](LICENSE) for details.

## Disclaimer

This is an **unofficial** project. It is not affiliated with, endorsed by, or supported by Google. It relies on undocumented APIs that may change at any time. Use at your own risk. See the [notebooklm-py security policy](https://github.com/teng-lin/notebooklm-py/blob/main/SECURITY.md) for credential handling guidance.
