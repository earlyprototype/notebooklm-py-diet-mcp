# NotebookLM-py to MCP Server -- Technical Assessment

**Date:** 2026-02-13 (initial assessment) | 2026-02-18 (v1.0 feature parity achieved)  
**Objective:** Assess requirements to convert notebooklm-py SDK into a deployable MCP server for Cursor

## Executive Summary

The notebooklm-py MCP server has achieved **full SDK feature parity** with the upstream notebooklm-py v0.3.2 SDK. All 7 API modules are wrapped, delivering 68 tools, 2 resources, and 2 prompts. The server is packaged via `pyproject.toml`, has a CI-safe test suite, and comprehensive documentation.

**Key Facts:**
- **SDK version:** notebooklm-py v0.3.2
- **Server architecture:** Single-file FastMCP server (~2,200 lines)
- **Tool count:** 68 tools across 9 categories (Notebooks, Sources, Chat, Artifacts, Research, Notes, Sharing, Settings, Account Management)
- **Dependencies:** MCP Python SDK (v1.x stable), notebooklm-py[browser] (includes Playwright)

## 1. MCP Server Architecture Overview

### What is MCP?

The Model Context Protocol (MCP) is an open protocol enabling LLM applications to integrate with external data sources and tools in a standardized way. It uses JSON-RPC 2.0 for communication.

**Core MCP Concepts:**
- **Resources:** Data/context exposure (like GET endpoints) - read-only access to information
- **Tools:** Executable functions (like POST endpoints) - perform actions with side effects
- **Prompts:** Reusable templates for LLM interactions
- **Transports:** stdio, SSE, Streamable HTTP

### MCP Python SDK (v1.x - Current Stable)

**Key Features:**
- FastMCP framework for rapid server development
- Decorator-based tool/resource registration
- Automatic schema generation from type hints
- Built-in progress reporting, logging, and elicitation
- Multiple transport options (stdio, HTTP, SSE)

**Installation:**
```bash
pip install "mcp[cli]"
```

## 2. NotebookLM-py SDK Capabilities (all wrapped)

### Functionality Coverage

| Category | SDK Capabilities | MCP Tools |
|----------|-----------------|-----------|
| **Notebooks** | Create, list, get, rename, delete, describe, summarise, share, remove from recent | 9 |
| **Sources** | Add URL/text/YouTube/file/Drive; get, fulltext, guide, rename, refresh, delete | 12 |
| **Chat** | Ask (with source filtering & threading), configure persona, get history | 3 |
| **Artifacts** | Generate + download 9 types; list, get, delete, rename, export | 23 |
| **Research** | Start web/Drive research, poll, import sources | 3 |
| **Notes** | List, create, get, update, delete notes; list/delete mind maps | 7 |
| **Sharing** | Get status, set public, set view level, add/update/remove users | 6 |
| **Settings** | Get/set output language | 2 |
| **Account Mgmt** | Get info, switch account, create profile | 3 |

### Current Architecture

```
notebooklm-py SDK (v0.3.2)
├── CLI (via Click)
├── Python API (AsyncIO-based)
│   ├── NotebookLMClient
│   ├── notebooks module
│   ├── sources module
│   ├── chat module
│   ├── artifacts module
│   └── sharing module
└── Authentication (browser-based, cookie storage)
```

## 3. Proposed MCP Server Architecture

### Server Structure

```
notebooklm-py-mcp/
├── server.py                 # Main FastMCP server
├── tools/
│   ├── notebooks.py         # Notebook management tools
│   ├── sources.py           # Source management tools
│   ├── chat.py              # Chat/query tools
│   ├── content.py           # Content generation tools
│   └── research.py          # Research tools
├── resources/
│   ├── notebooks.py         # Notebook list/info resources
│   └── sources.py           # Source content resources
├── prompts/
│   └── templates.py         # Prompt templates
├── utils/
│   ├── auth.py              # Authentication handling
│   └── client.py            # NotebookLM client wrapper
└── config.py                # Configuration management
```

### Recommended MCP Components

#### A. Resources (Read-Only Data Access)

| Resource URI | Description | Returns |
|--------------|-------------|---------|
| `notebooklm://notebooks` | List all notebooks | Notebook list with IDs and titles |
| `notebooklm://notebook/{id}` | Get notebook details | Full notebook metadata |
| `notebooklm://notebook/{id}/sources` | List sources in notebook | Source list with titles and URIs |
| `notebooklm://notebook/{id}/source/{source_id}` | Get source content | Full text of indexed source |
| `notebooklm://notebook/{id}/chat/history` | Get conversation history | Chat messages |

#### B. Tools (Executable Actions)

| Tool Name | Parameters | Description | Returns |
|-----------|------------|-------------|---------|
| `create_notebook` | `title: str` | Create new notebook | Notebook ID |
| `delete_notebook` | `notebook_id: str` | Delete notebook | Success confirmation |
| `add_source_url` | `notebook_id: str, url: str, wait: bool` | Add URL source | Source details |
| `add_source_file` | `notebook_id: str, file_path: str, wait: bool` | Add file source | Source details |
| `add_source_text` | `notebook_id: str, text: str, title: str` | Add text source | Source details |
| `ask_question` | `notebook_id: str, question: str` | Ask question to notebook | Answer with citations |
| `generate_audio` | `notebook_id: str, instructions: str, format: str, length: str` | Generate audio overview | Task status |
| `generate_quiz` | `notebook_id: str, quantity: str, difficulty: str` | Generate quiz | Task status |
| `generate_slides` | `notebook_id: str, format: str` | Generate slide deck | Task status |
| `download_audio` | `notebook_id: str, output_path: str` | Download audio file | File path |
| `download_quiz` | `notebook_id: str, output_path: str, format: str` | Download quiz | File path |
| `wait_for_generation` | `notebook_id: str, task_id: str` | Wait for content generation | Completion status |
| `research_web` | `notebook_id: str, query: str, mode: str` | Web research with auto-import | Research results |
| `research_drive` | `notebook_id: str, query: str, mode: str` | Google Drive research | Research results |

#### C. Prompts (Reusable Templates)

| Prompt Name | Arguments | Description |
|-------------|-----------|-------------|
| `analyze_sources` | `notebook_id, focus_area` | Template for source analysis |
| `generate_summary` | `notebook_id, style` | Template for summary generation |
| `create_quiz` | `notebook_id, topic, difficulty` | Template for quiz creation |
| `research_topic` | `topic, depth` | Template for research queries |

## 4. Technical Implementation Details

### 4.1 FastMCP Server Setup

```python
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession
import os
from notebooklm import NotebookLMClient

# Initialize MCP server with lifespan management
mcp = FastMCP(
    "NotebookLM",
    version="1.0.0",
    description="MCP server for Google NotebookLM integration",
)

# Lifespan context for authentication
@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Manage NotebookLM client lifecycle."""
    os.environ['NOTEBOOKLM_HOME'] = os.path.expanduser('~/.notebooklm-work')
    client = await NotebookLMClient.from_storage()
    try:
        yield {"client": client}
    finally:
        await client.close()

mcp = FastMCP("NotebookLM", lifespan=app_lifespan)
```

### 4.2 Tool Implementation Examples

```python
# Example: Create Notebook Tool
@mcp.tool()
async def create_notebook(
    title: str,
    ctx: Context[ServerSession, dict]
) -> dict:
    """Create a new NotebookLM notebook."""
    client = ctx.request_context.lifespan_context["client"]
    
    await ctx.info(f"Creating notebook: {title}")
    notebook = await client.notebooks.create(title)
    
    return {
        "id": notebook.id,
        "title": notebook.title,
        "created": True
    }

# Example: Ask Question Tool with Progress
@mcp.tool()
async def ask_question(
    notebook_id: str,
    question: str,
    ctx: Context[ServerSession, dict]
) -> dict:
    """Ask a question to a NotebookLM notebook."""
    client = ctx.request_context.lifespan_context["client"]
    
    await ctx.info(f"Querying notebook: {notebook_id}")
    await ctx.report_progress(0.3, 1.0, "Sending question...")
    
    result = await client.chat.ask(notebook_id, question)
    
    await ctx.report_progress(1.0, 1.0, "Complete")
    
    return {
        "answer": result.answer,
        "has_citations": hasattr(result, 'citations') and bool(result.citations)
    }

# Example: Generate Audio with Wait
@mcp.tool()
async def generate_audio_overview(
    notebook_id: str,
    instructions: str = "",
    format: str = "deep-dive",
    length: str = "medium",
    ctx: Context[ServerSession, dict]
) -> dict:
    """Generate audio overview (podcast) from notebook sources."""
    client = ctx.request_context.lifespan_context["client"]
    
    await ctx.info(f"Generating audio: {format} ({length})")
    status = await client.artifacts.generate_audio(
        notebook_id, 
        instructions=instructions,
        format=format,
        length=length
    )
    
    await ctx.report_progress(0.5, 1.0, "Waiting for generation...")
    await client.artifacts.wait_for_completion(notebook_id, status.task_id)
    await ctx.report_progress(1.0, 1.0, "Complete")
    
    return {
        "task_id": status.task_id,
        "status": "completed"
    }
```

### 4.3 Resource Implementation Examples

```python
# Example: List Notebooks Resource
@mcp.resource("notebooklm://notebooks")
async def list_notebooks(ctx: Context[ServerSession, dict]) -> str:
    """List all NotebookLM notebooks."""
    client = ctx.request_context.lifespan_context["client"]
    notebooks = await client.notebooks.list()
    
    result = "# NotebookLM Notebooks\n\n"
    for nb in notebooks:
        result += f"- **{nb.title}** (ID: `{nb.id}`)\n"
    
    return result

# Example: Get Source Content Resource
@mcp.resource("notebooklm://notebook/{notebook_id}/source/{source_id}")
async def get_source_content(
    notebook_id: str, 
    source_id: str,
    ctx: Context[ServerSession, dict]
) -> str:
    """Get the full text content of a source."""
    client = ctx.request_context.lifespan_context["client"]
    
    # This would use the fulltext API if available
    source = await client.sources.get_fulltext(notebook_id, source_id)
    return source.content
```

### 4.4 Prompt Implementation Examples

```python
from mcp.server.fastmcp.prompts import base

@mcp.prompt()
def analyze_sources(notebook_id: str, focus_area: str = "main themes") -> str:
    """Generate a prompt for analyzing notebook sources."""
    return f"""Please analyze the sources in notebook {notebook_id}.

Focus on: {focus_area}

Provide:
1. Key themes and concepts
2. Important findings or insights
3. Connections between sources
4. Gaps or areas needing more research"""

@mcp.prompt()
def create_research_query(topic: str, depth: str = "deep") -> list[base.Message]:
    """Generate a structured research query prompt."""
    return [
        base.UserMessage(f"I need to research: {topic}"),
        base.UserMessage(f"Depth level: {depth}"),
        base.AssistantMessage("I'll help you create a comprehensive research plan. What specific aspects would you like to focus on?")
    ]
```

## 5. Authentication & Configuration

### Authentication Strategy

NotebookLM-py uses browser-based authentication with cookie storage. The MCP server needs to handle this properly:

**Option 1: Pre-authenticated (Recommended)**
- User authenticates once via CLI: `notebooklm login`
- MCP server reads from `~/.notebooklm-work/storage_state.json`
- Server uses existing credentials
- Benefits: Simple, secure, no re-authentication needed

**Option 2: Environment Variable**
- Store auth JSON in environment variable
- Useful for containerized deployments
- Set `NOTEBOOKLM_AUTH_JSON` with credentials

**Implementation:**
```python
import os
from notebooklm import NotebookLMClient

async def init_client():
    """Initialize NotebookLM client with authentication."""
    # Set account config
    os.environ['NOTEBOOKLM_HOME'] = os.path.expanduser('~/.notebooklm-work')
    
    # Create client from stored credentials
    client = await NotebookLMClient.from_storage()
    return client
```

### Configuration File

Create `notebooklm-mcp-config.json`:
```json
{
  "account_type": "work",
  "auth_path": "~/.notebooklm-work",
  "default_timeout": 300,
  "enable_progress_reports": true,
  "cache_notebooks": true,
  "cache_ttl": 300
}
```

## 6. Transport & Deployment Options

### Option 1: Stdio Transport (Recommended for Cursor)

**Best for:** Local Cursor integration  
**How it works:** Communication via stdin/stdout

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Cursor Configuration:**
```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "python",
      "args": [
        "<path-to-repo>\\notebooklm_mcp_server.py"
      ],
      "env": {
        "NOTEBOOKLM_HOME": "<path-to-home>\\.notebooklm-work"
      }
    }
  }
}
```

### Option 2: Streamable HTTP Transport

**Best for:** Remote access, multiple clients, browser integration

```python
if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8000)
```

**Cursor Configuration:**
```json
{
  "mcpServers": {
    "notebooklm": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Option 3: SSE Transport

**Best for:** Real-time updates, long-running operations

```python
if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
```

## 7. Development Roadmap

### Phase 1: Core Server (2-3 days)
**Priority: HIGH**

- [ ] Set up FastMCP server structure
- [ ] Implement authentication/lifespan management
- [ ] Create core tools:
  - [ ] `list_notebooks`
  - [ ] `create_notebook`
  - [ ] `add_source_url`
  - [ ] `add_source_text`
  - [ ] `ask_question`
- [ ] Create core resources:
  - [ ] `notebooklm://notebooks`
  - [ ] `notebooklm://notebook/{id}`
- [ ] Test with MCP Inspector
- [ ] Test with Cursor integration

### Phase 2: Content Generation (1-2 days)
**Priority: MEDIUM**

- [ ] Implement generation tools:
  - [ ] `generate_audio`
  - [ ] `generate_quiz`
  - [ ] `generate_slides`
  - [ ] `generate_flashcards`
- [ ] Implement download tools
- [ ] Add progress reporting
- [ ] Test generation workflows

### Phase 3: Advanced Features (1-2 days)
**Priority: LOW**

- [ ] Research tools (web/drive)
- [ ] File source support
- [ ] Batch operations
- [ ] Prompt templates
- [ ] Caching layer
- [ ] Error recovery

### Phase 4: Polish & Documentation (1 day)
**Priority: MEDIUM**

- [ ] Comprehensive error handling
- [ ] Usage documentation
- [ ] Example workflows
- [ ] Performance optimization
- [ ] Security review

## 8. Technical Requirements

### Dependencies

```toml
[project]
name = "notebooklm-py-mcp"
version = "1.0.0"
dependencies = [
    "mcp[cli]>=1.0.0",
    "notebooklm-py>=0.3.2",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]
```

### System Requirements

- Python 3.10+
- Windows/macOS/Linux
- Active NotebookLM authentication
- Network access to NotebookLM APIs

### Performance Considerations

| Operation | Expected Time | Notes |
|-----------|---------------|-------|
| List notebooks | <1s | Cached recommended |
| Add source (URL) | 2-10s | Depends on processing |
| Ask question | 2-5s | Network latency |
| Generate audio | 30-120s | Long-running, use progress |
| Generate quiz | 10-30s | Moderate duration |
| Download artifact | 2-10s | File size dependent |

## 9. Security Considerations

### Authentication Security

- ✅ Credentials stored in user profile (`~/.notebooklm-work`)
- ✅ No credentials in server code
- ✅ Environment-based auth config
- ⚠️ Session expiration handling needed
- ⚠️ Multi-user scenarios require isolation

### Data Privacy

- ⚠️ Notebook content may contain sensitive information
- ⚠️ Consider logging policies
- ✅ No data persistence in server (stateless)
- ✅ Authentication tied to user's Google account

### Recommendations

1. **Credential Rotation:** Document re-authentication process
2. **Logging:** Avoid logging notebook content
3. **Error Messages:** Sanitize error messages to avoid leaking data
4. **Rate Limiting:** Implement client-side rate limiting
5. **Timeout Handling:** Graceful handling of API timeouts

## 10. Testing Strategy

### Unit Tests

```python
# Test tool registration
def test_tools_registered():
    assert "create_notebook" in mcp.tools
    assert "ask_question" in mcp.tools

# Test authentication
async def test_client_initialization():
    client = await init_client()
    assert client is not None
    await client.close()
```

### Integration Tests

```python
# Test with MCP Inspector
# 1. Start server: python notebooklm_mcp_server.py
# 2. Run inspector: npx @modelcontextprotocol/inspector
# 3. Connect and test tools

# Test with Cursor
# 1. Configure in Cursor settings
# 2. Test tool invocation from agent
# 3. Verify responses
```

### Manual Testing Checklist

- [ ] Server starts without errors
- [ ] Authentication loads correctly
- [ ] List notebooks returns data
- [ ] Create notebook works
- [ ] Add source (URL) works
- [ ] Ask question returns answer
- [ ] Generate audio completes
- [ ] Download artifact succeeds
- [ ] Error handling works
- [ ] Progress reporting visible

## 11. Success Criteria

### Functional Requirements

✅ **Must Have:**
- List and create notebooks
- Add sources (URL, text)
- Ask questions with answers
- Generate audio overviews
- Work with Cursor stdio transport

✅ **Should Have:**
- Generate quizzes and flashcards
- Download artifacts
- Progress reporting
- Error handling

⏸️ **Nice to Have:**
- Web/Drive research
- File source upload
- Batch operations
- Caching layer

### Non-Functional Requirements

- **Performance:** <5s for most operations
- **Reliability:** 95%+ success rate
- **Usability:** Clear error messages
- **Maintainability:** Well-documented code
- **Security:** No credential leakage

## 12. Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| API changes in notebooklm-py | Medium | High | Pin version, monitor updates |
| Session expiration | High | Medium | Auto-refresh, clear error messages |
| Long-running operations timeout | Medium | Medium | Async patterns, progress reporting |
| Cursor MCP integration issues | Low | High | Thorough testing, fallback to HTTP |
| Rate limiting by Google | Low | Medium | Implement client-side throttling |

## 13. Next Steps

### Immediate Actions

1. **Create project structure**
   ```bash
   mkdir notebooklm-py-mcp
   cd notebooklm-py-mcp
   uv init
   ```

2. **Install dependencies**
   ```bash
   uv add "mcp[cli]" notebooklm-py
   ```

3. **Create basic server**
   - Implement FastMCP setup
   - Add authentication lifespan
   - Create 3-5 core tools
   - Test with MCP Inspector

4. **Cursor integration**
   - Configure Cursor settings
   - Test from AI agent
   - Document usage patterns

### Timeline Summary

- **Phase 1 (Core):** 2-3 days → Functional MCP server
- **Phase 2 (Generation):** 1-2 days → Content generation
- **Phase 3 (Advanced):** 1-2 days → Research & extras
- **Phase 4 (Polish):** 1 day → Documentation & testing

**Total Estimate:** 5-8 days for full implementation

## 14. Conclusion

The notebooklm-py MCP server has been implemented with **full SDK feature parity**. All 7 API modules are wrapped, delivering 68 tools across 9 categories. The server is packaged via `pyproject.toml`, has a CI-safe test suite, and comprehensive documentation.

### Key Benefits Delivered

1. **Native Cursor Integration:** NotebookLM is directly accessible from AI agents
2. **Standardized Protocol:** MCP ensures compatibility with Cursor, Claude Code, and other clients
3. **Full Feature Coverage:** Every public SDK method has a corresponding MCP tool
4. **Automated Authentication:** Expired sessions are refreshed automatically via Playwright
5. **Multi-Account Support:** Runtime account switching with persisted preferences

### Value Proposition

- Research automation across documentation and knowledge bases
- Automated Q&A against indexed sources with conversation threading
- Content generation for reports, presentations, podcasts, videos, and more
- Note-taking and mind mapping within notebooks
- Sharing and permissions management
- Integration with existing AI workflows in Cursor

**Status:** v1.0 complete  
**Architecture:** Single-file (~2,200 lines)  
**Test suite:** CI-safe, mocked  
**Risk:** Low (APIs are undocumented but stable via notebooklm-py)
