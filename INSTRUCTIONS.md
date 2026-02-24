The notebooklm MCP server provides access to Google NotebookLM for querying knowledge bases, adding sources, and generating content. This is the diet (lightweight) server with 14 workflow-oriented tools.

ACCOUNT CONTEXT:
- Multiple Google accounts are supported via named profiles (e.g. work, personal, design)
- Use get_account_info to see the active account and all available profiles
- Use switch_account to change the active profile at runtime -- no restart required
- Use create_profile to set up a new account -- this launches a browser for Google sign-in
- The active profile is persisted across server restarts in ~/.notebooklm-active.json
- Workflow for a new account: create_profile -> (user signs in) -> switch_account

IMPORTANT - Workflow patterns:
1. Always call list_notebooks_tool first to get notebook IDs before calling other tools
2. Most tools require a notebook_id parameter -- never guess this, always look it up
3. When adding sources via add_sources, set wait=True so processing completes before querying
4. Content generation can take 30-120 seconds -- inform the user it may take a moment

IMPORTANT - ask_question tool:
- This is the primary tool for retrieving information from NotebookLM
- Answers are AI-generated from the notebook's indexed sources with citations
- Frame questions clearly and specifically for best results
- If the answer is insufficient, try rephrasing or breaking into smaller questions
- Use source_ids to restrict the query to specific sources
- Use conversation_id to continue an existing conversation thread
- Use persona to set a chat role before asking (e.g. "strategy analyst", "tutor", "concise summariser") -- this configures the chat persona automatically
- Use response_length to control verbosity: "short", "medium", or "long"

IMPORTANT - add_sources tool:
- Adds multiple sources in a single call -- each source is a dict with "type" and "value"
- Supported types: "url" (web pages, articles, YouTube), "text" (requires a "title" field), "file" (local files)
- Example: add_sources("nb-1", [{"type": "url", "value": "https://example.com"}, {"type": "text", "value": "Content", "title": "Notes"}])
- Sources take time to process; always use wait=True unless the user explicitly says otherwise
- After adding sources, confirm what was added before proceeding
- Use list_sources to see all sources in a notebook

IMPORTANT - generate_and_download tool:
- Generates an artifact and downloads it in one step
- Supported artifact types: report, audio, slide_deck, quiz, infographic
- Correct file extensions for output_path:
    - report: .pdf
    - audio: .wav
    - slide_deck: .pdf
    - quiz: depends on quiz_output_format param (.json, .md, .html)
    - infographic: .pdf
- Audio options: audio_format (deep-dive, brief, critique, debate), audio_length (short, medium, long)
- Quiz options: quiz_quantity (few, standard, more), quiz_difficulty (easy, medium, hard)
- Use instructions parameter to customise generation -- for slide decks, pass a design template to control visual style
- The templates/slide_styles.md file contains three ready-to-use templates: Corporate, Educational, Creative. Copy the contents of any template into the instructions parameter.

IMPORTANT - list_artifacts and export_artifact:
- list_artifacts shows previously generated artifacts in a notebook (optionally filtered by type)
- export_artifact downloads a specific artifact by ID to a file
- Use these to retrieve content that was generated previously without regenerating it

IMPORTANT - research_and_import tool:
- Researches a topic and imports the results as notebook sources in a single call
- Params: query (search terms), source ("web" or "drive"), max_results (default 5)
- The tool starts the research, polls until complete, and imports the top results automatically
- This replaces the manual three-step workflow of start -> poll -> import

IMPORTANT - PDF / PNG conversion utilities:
- pdf_to_png: Converts a PDF into individual PNG images (one per page). Use this to make slide deck pages visible to LLMs for visual review or editing. Default DPI of 200 balances quality and file size; increase for higher fidelity if needed. Output goes to a <filename>_pages/ folder beside the PDF by default.
- png_to_pdf: Combines PNG images back into a single PDF. Accepts either an explicit list of image paths or a directory of PNGs. When using a directory, files are sorted alphabetically -- the page_001.png naming from pdf_to_png preserves correct page order automatically.
- Typical round-trip workflow: generate_and_download (slide_deck) -> pdf_to_png -> LLM reviews/edits images -> png_to_pdf -> final PDF

AUTOMATIC RE-AUTHENTICATION:
- The server handles expired or missing Google sessions automatically
- The server starts without validating the session, so it is always available
- On the first tool call (and every subsequent call), the session is validated. If credentials are missing or expired, the server automatically launches a browser for Google sign-in, waits for the user to complete it, reconnects, and resumes the original operation
- The user will see a browser window open and a message explaining what is happening -- no manual intervention is required beyond signing in
- If automatic re-authentication fails (e.g. browser automation unavailable), the server falls back to instructing the user to run notebooklm login manually

ERROR HANDLING:
- Authentication errors are handled automatically (see above) -- you should rarely need to advise manual re-authentication
- If notebooks return empty, confirm the correct account is active using get_account_info
- Network timeouts on generation tools are normal for long content -- retry once before reporting failure
- The generate_and_download tool handles the full generate-wait-download cycle internally; if it fails, the error message will indicate at which stage

STYLE:
- Present NotebookLM answers in a clear, readable format
- When citing NotebookLM responses, note they come from the notebook's sources
- Do not repeat the raw JSON structure to the user; extract and present the answer naturally
