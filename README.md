# notebooklm-py-mcp

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes [Google NotebookLM](https://notebooklm.google.com/) capabilities as tools for AI agents in [Cursor](https://cursor.com/), [Claude Code](https://docs.claude.com/en/docs/claude-code), and other MCP-compatible clients.

Built on top of [**notebooklm-py**](https://github.com/teng-lin/notebooklm-py) by [Teng Lin](https://github.com/teng-lin) -- the unofficial Python API for Google NotebookLM.

> **Unofficial** -- This project uses undocumented Google APIs via notebooklm-py. It is not affiliated with Google. APIs may change without notice. Best suited for research, prototyping, and personal productivity workflows.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)

## Features

### MCP Tools (72)

#### Notebooks (9)

| Tool | Description |
|------|-------------|
| `list_notebooks_tool` | List all notebooks with IDs and titles |
| `create_notebook` | Create a new notebook |
| `get_notebook` | Look up a single notebook by ID |
| `delete_notebook` | Permanently delete a notebook |
| `rename_notebook` | Rename a notebook |
| `get_notebook_description` | Get AI-generated description and suggested topics |
| `get_notebook_summary` | Get raw text summary of notebook contents |
| `share_notebook` | Update sharing settings |
| `remove_notebook_from_recent` | Remove from the recent notebooks list |

#### Sources (13)

| Tool | Description |
|------|-------------|
| `list_sources` | List all sources in a notebook with IDs, titles, and status |
| `add_source_url` | Add a URL as a source (web pages, articles, YouTube) |
| `add_source_text` | Add text content as a source |
| `add_source_youtube` | Add a YouTube video as a source |
| `add_source_file` | Upload a local file as a source |
| `add_source_drive` | Add a Google Drive file as a source |
| `get_source` | Get metadata for a specific source |
| `get_source_fulltext` | Get the full indexed text content of a source |
| `get_source_guide` | Get AI-generated source guide (summary + keywords) |
| `rename_source` | Rename a source |
| `check_source_freshness` | Check if a source needs to be refreshed |
| `refresh_source` | Re-fetch and re-index a URL-based source |
| `delete_source` | Remove a source from a notebook |

#### Chat (3)

| Tool | Description |
|------|-------------|
| `ask_question` | Query a notebook with optional source filtering and conversation threading |
| `configure_chat` | Configure chat persona (goal, response length, custom prompt) |
| `get_chat_history` | Retrieve conversation history |

#### Artifacts -- Generation (9)

| Tool | Description |
|------|-------------|
| `generate_audio_overview` | Generate a podcast-style audio overview |
| `generate_video` | Generate a video from notebook sources |
| `generate_report` | Generate a written report |
| `generate_quiz` | Generate a quiz |
| `generate_flashcards` | Generate flashcards |
| `generate_slide_deck` | Generate a slide deck |
| `generate_infographic` | Generate an infographic |
| `generate_data_table` | Generate a structured data table |
| `generate_mind_map` | Generate a mind map |

#### Artifacts -- Download (9)

| Tool | Description |
|------|-------------|
| `download_audio` | Download generated audio as WAV |
| `download_video` | Download generated video |
| `download_report` | Download generated report |
| `download_quiz` | Download quiz as JSON, Markdown, or HTML |
| `download_flashcards` | Download generated flashcards |
| `download_slide_deck` | Download generated slide deck |
| `download_infographic` | Download generated infographic |
| `download_data_table` | Download generated data table |
| `download_mind_map` | Download generated mind map |

#### Artifacts -- Management (6)

| Tool | Description |
|------|-------------|
| `list_artifacts` | List artifacts in a notebook (optionally filtered by type) |
| `get_artifact` | Get metadata for a specific artifact |
| `delete_artifact` | Delete an artifact |
| `rename_artifact` | Rename an artifact |
| `export_artifact` | Export an artifact to a file in a given format |
| `suggest_reports` | Get AI-suggested report formats for a notebook |

#### Research (3)

| Tool | Description |
|------|-------------|
| `start_research` | Start a web or Drive research task |
| `poll_research` | Check status and results of a research task |
| `import_research_sources` | Import selected research results as notebook sources |

#### Notes (7)

| Tool | Description |
|------|-------------|
| `list_notes` | List all notes in a notebook |
| `create_note` | Create a new note |
| `get_note` | Get note content |
| `update_note` | Update note content and/or title |
| `delete_note` | Delete a note |
| `list_mind_maps` | List all mind maps |
| `delete_mind_map` | Delete a mind map |

#### Sharing (6)

| Tool | Description |
|------|-------------|
| `get_sharing_status` | Get current sharing status and permissions |
| `set_notebook_public` | Make a notebook public or private |
| `set_notebook_view_level` | Set the view permission level |
| `add_shared_user` | Share with a specific user by email |
| `update_shared_user` | Update a shared user's permission level |
| `remove_shared_user` | Remove a user's access |

#### Settings (2)

| Tool | Description |
|------|-------------|
| `get_output_language` | Get the current output language |
| `set_output_language` | Set the output language for responses |

#### Account Management (3)

| Tool | Description |
|------|-------------|
| `get_account_info` | Show the active account and available profiles |
| `switch_account` | Switch to a different Google account profile at runtime |
| `create_profile` | Create a new account profile and launch browser sign-in |

#### Utilities (2)

| Tool | Description |
|------|-------------|
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

## Prerequisites

- Python 3.10 or later
- A Google account with access to [NotebookLM](https://notebooklm.google.com/)
- [Cursor](https://cursor.com/) or another MCP-compatible client

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/earlyprototype/notebooklm-py-MCP.git
cd notebooklm-py-mcp
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

Replace the placeholder paths with your actual paths. For example on Windows:

```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "C:\\Users\\You\\projects\\notebooklm-py-mcp\\venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\You\\projects\\notebooklm-py-mcp\\notebooklm_mcp_server.py"
      ]
    }
  }
}
```

The server manages account profiles internally -- no `NOTEBOOKLM_HOME` environment variable is needed in the configuration. Use the `switch_account` and `get_account_info` tools to manage profiles at runtime.

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

To change which account the MCP server uses, update the `NOTEBOOKLM_HOME` value in your client configuration and restart.

The `get_account_info` tool will show the currently active profile and provide switching instructions.

## Usage Examples

Once configured, you can interact with NotebookLM directly from your AI agent:

**List notebooks:**
> "List my NotebookLM notebooks"

**Query a knowledge base:**
> "Ask the Strategy notebook: what are our key objectives for 2026?"

**Add sources:**
> "Add this URL to my Research notebook: https://example.com/article"
> "Add this YouTube video to the Training notebook: https://youtube.com/watch?v=..."

**Generate content:**
> "Generate a podcast overview for the Project notebook"
> "Generate a report from the Strategy notebook and download it as PDF"
> "Create flashcards from the Training notebook"

**Research:**
> "Start a web research task on 'digital fabrication trends' in my Research notebook"
> "Check the research results and import the top 3 sources"

**Notes:**
> "Create a note in the Strategy notebook titled 'Meeting actions'"
> "List all notes in the Research notebook"

**Sharing:**
> "Share the Project notebook with team@example.com as an editor"
> "Make the Training notebook publicly viewable"

**Create a quiz:**
> "Generate a hard quiz from the Training notebook and download it as markdown"

## Project Structure

```
notebooklm-py-mcp/
  notebooklm_mcp_server.py   # MCP server (tools, resources, prompts)
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
