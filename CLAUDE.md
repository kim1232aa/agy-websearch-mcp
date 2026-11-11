# AGY-WebSearch-MCP Developer Guide (CLAUDE.md)

## Overview
Ultra-lightweight Model Context Protocol (MCP) server providing Google Search Grounding capabilities reverse-engineered from Google Antigravity CLI (`agy`).

- **Zero External Dependencies**: Pure Python 3 standard library only (`urllib`, `json`, `http.server`, `socket`, `platform`).
- **Protocol**: JSON-RPC 2.0 over Stdio (MCP 2024-11-05 / 2025-03-26 / 2025-06-18, negotiated in `initialize`).
- **Optional HTTP mode**: `python3 server.py serve` exposes a Tavily-compatible `POST /search` with mandatory token auth (`AGY_HTTP_TOKENS` / `--token-file`), per-IP rate limiting, concurrency and body-size limits.
- **Upstream Endpoint**: `POST https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent` with `googleSearch` tool and `gemini-2.5-flash` model under the `aicode-consumers` project.

## CLI Usage
- **Interactive OAuth Login**:
  ```bash
  python3 server.py login
  ```
- **CLI Direct Search**:
  ```bash
  python3 server.py --search "query"
  python3 server.py --search "query" --domain "python.org"
  ```
- **Stdio MCP Server** (default when invoked without flags):
  ```bash
  python3 server.py
  ```
- **HTTP API**:
  ```bash
  AGY_HTTP_TOKENS=$(python3 server.py gen-token) python3 server.py serve --host 127.0.0.1 --port 8765
  ```
- **Tests** (offline): `python3 -m unittest discover -s tests -v`

## MCP Tools
1. `search_web`:
   - `query` (`string`, required): Search keyword or question. Supports standard Google operators.
   - `domain` (`string`, optional): Target domain to prioritize or filter (mapped upstream to `includedDomains`).
   - `engine` (`google` | `grok` | `codex`, optional, default `google`).
   - Returns Markdown text only (no `structuredContent`, to avoid clients rendering results twice); invalid params / unknown tool → JSON-RPC `-32602`; upstream failures → `isError: true`.

## Architecture
- Each engine is `search_<engine>(query, domains, max_results) -> dict` and raises `SearchError(message, kind)`; register it in `ENGINES`.
- All inputs pass through `normalize_*` helpers shared by MCP, HTTP and CLI.
- `format_markdown()` renders results for MCP/CLI; `to_tavily_response()` renders them for HTTP.
- grok/codex may reuse the pi coding agent's `auth.json` (`pi_access_token()`), strictly read-only: never refresh or write pi's tokens (pi rotates refresh tokens). Codex via ChatGPT login uses the streaming `chatgpt.com/backend-api/codex/responses` endpoint (`stream: true` is mandatory).

## Credentials Lookup Precedence
1. `AGY_CREDENTIALS_PATH` environment variable.
2. `./credentials.json` adjacent to `server.py`.
3. OS-standard user data directory:
   - Linux: `~/.local/share/agy-websearch-mcp/credentials.json`
   - macOS: `~/Library/Application Support/agy-websearch-mcp/credentials.json`
   - Windows: `%APPDATA%\agy-websearch-mcp\credentials.json`
4. Legacy locations (logged with a migration warning): `~/.config/agy-websearch-mcp/credentials.json`, `~/.agy-websearch-mcp/credentials.json`, `~/.gemini/antigravity-cli/antigravity-oauth-token`.
- The current working directory is intentionally NOT searched.

## Coding Conventions
- Maintain strictly zero third-party dependencies (`pip install` must never be required).
- Do not hardcode local environment values (IP addresses, specific proxy ports, personal user paths).
- Keep credentials safe: `credentials.json` must remain ignored by git.
- Run the offline test suite before committing; never log query contents or tokens in HTTP mode.
