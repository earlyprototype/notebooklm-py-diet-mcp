The notebooklm MCP server provides access to Google NotebookLM for querying knowledge bases, adding sources, and generating content.

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
3. When adding sources, set wait=True so processing completes before querying
4. Content generation (audio, video, report, etc.) can take 30-120 seconds -- inform the user it may take a moment

IMPORTANT - ask_question tool:
- This is the primary tool for retrieving information from NotebookLM
- Answers are AI-generated from the notebook's indexed sources with citations
- Frame questions clearly and specifically for best results
- If the answer is insufficient, try rephrasing or breaking into smaller questions
- Use source_ids to restrict the query to specific sources
- Use conversation_id to continue an existing conversation thread

IMPORTANT - Chat configuration:
- Use configure_chat to set a persona (goal), response length, or custom prompt before asking questions
- get_chat_history retrieves the full conversation log for a notebook

IMPORTANT - Content generation:
1. generate_audio_overview: Creates podcast-style audio. Formats: deep-dive, brief, critique, debate. Lengths: short, medium, long
2. generate_video: Creates video from notebook sources
3. generate_report: Creates a written report
4. generate_quiz: Creates quiz questions. Quantity: few, standard, more. Difficulty: easy, medium, hard
5. generate_flashcards: Creates flashcards for study
6. generate_slide_deck: Creates a presentation slide deck
7. generate_infographic: Creates a visual infographic
8. generate_data_table: Creates a structured data table
9. generate_mind_map: Creates a mind map from sources
10. Always generate before downloading -- download tools retrieve the most recently generated artifact
11. Generation is a two-step process: generate -> download
12. Use list_artifacts to see previously generated artifacts; get_artifact, rename_artifact, delete_artifact, and export_artifact for management

IMPORTANT - Adding sources:
- add_source_url: For web pages, articles, Wikipedia, YouTube links
- add_source_text: For pasting text content directly (requires a title)
- add_source_youtube: For YouTube videos specifically
- add_source_file: For uploading local files (PDF, TXT, etc.)
- add_source_drive: For Google Drive files (requires file_id, title, and mime_type)
- Sources take time to process; always use wait=True unless the user explicitly says otherwise
- After adding sources, confirm what was added before proceeding
- Use get_source, get_source_fulltext, and get_source_guide to inspect source content
- rename_source and refresh_source are available for source management

IMPORTANT - Research:
- start_research kicks off a web or Google Drive search task (modes: fast, deep)
- poll_research checks the status and retrieves results
- import_research_sources imports selected results as notebook sources
- Typical workflow: start_research -> poll_research (until completed) -> import_research_sources

IMPORTANT - Notes:
- list_notes, create_note, get_note, update_note, delete_note for managing notebook notes
- list_mind_maps and delete_mind_map for managing generated mind maps
- Notes are distinct from sources -- they are user-authored content within the notebook

IMPORTANT - Sharing:
- get_sharing_status shows current permissions and shared users
- set_notebook_public toggles public access
- set_notebook_view_level sets the default view permission (view, comment, edit)
- add_shared_user, update_shared_user, remove_shared_user manage individual user access
- Sharing changes take effect immediately

IMPORTANT - Settings:
- get_output_language and set_output_language control the language of NotebookLM responses
- This affects the language of generated content, answers, and summaries

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
- Destructive operations (delete_notebook, delete_source, delete_note, delete_artifact, delete_mind_map) cannot be undone -- always confirm with the user first

STYLE:
- Present NotebookLM answers in a clear, readable format
- When citing NotebookLM responses, note they come from the notebook's sources
- Do not repeat the raw JSON structure to the user; extract and present the answer naturally
