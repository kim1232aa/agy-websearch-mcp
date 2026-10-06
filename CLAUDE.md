# AGY-WebSearch-MCP Developer Guide (CLAUDE.md)

## Overview
Ultra-lightweight Model Context Protocol (MCP) server providing Google Search Grounding capabilities reverse-engineered from Google Antigravity CLI (`agy`).

- **Zero External Dependencies**: Pure Python 3 standard library only (`urllib`, `json`, `http.server`, `socket`, `platform`).
- **Protocol**: JSON-RPC 2.0 over Stdio.
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

## MCP Tools
1. `search_web` (Primary, aligned with native `agy` tool definition):
   - `query` (`string`, required): Search keyword or question. Supports standard Google operators.
   - `domain` (`string`, optional): Target domain to prioritize or filter (mapped upstream to `includedDomains`).
   - `toolAction` (`string`, optional): Agent status metadata (e.g. `'Searching the web'`).
   - `toolSummary` (`string`, optional): Task category metadata (e.g. `'Web search'`).
2. `agy_web_search` (Alias for backwards compatibility with previous configurations).

## Credentials Lookup Precedence
1. `AGY_CREDENTIALS_PATH` environment variable.
2. `./credentials.json` adjacent to `server.py`.
3. `./credentials.json` in current working directory.
4. OS-standard user data directory:
   - Linux: `~/.local/share/agy-websearch-mcp/credentials.json`
   - macOS: `~/Library/Application Support/agy-websearch-mcp/credentials.json`
   - Windows: `%APPDATA%\agy-websearch-mcp\credentials.json`
5. Legacy agy cache: `~/.gemini/antigravity-cli/antigravity-oauth-token`.

## Coding Conventions
- Maintain strictly zero third-party dependencies (`pip install` must never be required).
- Do not hardcode local environment values (IP addresses, specific proxy ports, personal user paths).
- Keep credentials safe: `credentials.json` must remain ignored by git.
