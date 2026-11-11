#!/usr/bin/env python3
"""
agy-websearch-mcp: lightweight Google Search Grounding MCP server.

- Pure Python 3 standard library, zero third-party dependencies.
- Stdio JSON-RPC 2.0 MCP server (default mode).
- Optional Tavily-compatible HTTP API (`python3 server.py serve`) with token auth,
  rate limiting and request size limits so it can sit behind a reverse proxy.
- Built-in OAuth 2.0 login wizard (`python3 server.py login`).
- Pluggable engines: google (Gemini grounding, default), grok (xAI), codex (OpenAI Responses /
  ChatGPT Codex). grok/codex can reuse credentials saved by the pi coding agent (read-only).
"""

from __future__ import annotations

import argparse
import base64
import hmac
import http.server
import json
import logging
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections import Counter, OrderedDict

__version__ = "2.0.0"
SERVER_NAME = "agy-websearch-mcp"

logging.basicConfig(
    level=getattr(logging, os.environ.get("AGY_LOG_LEVEL", "INFO").upper(), logging.INFO),
    stream=sys.stderr,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)
log = logging.getLogger(SERVER_NAME)

# --------------------------------------------------------------------------------------
# Constants / limits
# --------------------------------------------------------------------------------------

_C_RAW = bytes([107, 106, 109, 107, 106, 106, 108, 106, 108, 106, 111, 99, 107, 119, 46, 55, 50, 41, 41, 51, 52, 104, 50, 104, 107, 54, 57, 40, 63, 104, 105, 111, 44, 46, 53, 54, 53, 48, 50, 110, 61, 110, 106, 105, 63, 42, 116, 59, 42, 42, 41, 116, 61, 53, 53, 61, 54, 63, 47, 41, 63, 40, 57, 53, 52, 46, 63, 52, 46, 116, 57, 53, 55])
_S_RAW = bytes([29, 21, 25, 9, 10, 2, 119, 17, 111, 98, 28, 13, 8, 110, 98, 108, 22, 62, 22, 16, 107, 55, 22, 24, 98, 41, 2, 25, 110, 32, 108, 43, 30, 27, 60])


def _decode_cred(data: bytes) -> str:
    return bytes(b ^ 0x5A for b in data).decode("utf-8")


CLIENT_ID = os.environ.get("AGY_CLIENT_ID", _decode_cred(_C_RAW))
CLIENT_SECRET = os.environ.get("AGY_CLIENT_SECRET", _decode_cred(_S_RAW))
AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent"

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

MAX_QUERY_LEN = 2000
MAX_DOMAINS = 10
MAX_RESULTS_LIMIT = 20
DEFAULT_MAX_RESULTS = 10
MAX_SNIPPET_LEN = 1200
UPSTREAM_TIMEOUT = float(os.environ.get("AGY_UPSTREAM_TIMEOUT", "30"))
ANSWER_MAX_TOKENS = int(os.environ.get("AGY_ANSWER_MAX_TOKENS", "800"))

ENGINE_LABELS = {"google": "Google", "grok": "Grok", "codex": "Codex"}


class SearchError(Exception):
    """A search could not be completed.

    kind: bad_request | auth | upstream | timeout | rate_limited | internal
    """

    def __init__(self, message: str, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind


class UpstreamHTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


# --------------------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------------------

def get_user_data_dir() -> str:
    """Standard cross-platform user data directory."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, SERVER_NAME)
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Application Support/{SERVER_NAME}")
    xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(xdg_data, SERVER_NAME)


LEGACY_CRED_PATHS = (
    os.path.expanduser(f"~/.config/{SERVER_NAME}/credentials.json"),
    os.path.expanduser(f"~/.{SERVER_NAME}/credentials.json"),
    os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token"),
)


def get_possible_cred_paths() -> list[str]:
    """Lookup order: env override, next to server.py, user data dir, legacy locations."""
    paths = []
    env_path = os.environ.get("AGY_CREDENTIALS_PATH")
    if env_path:
        paths.append(os.path.expanduser(env_path))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(script_dir, "credentials.json"))
    paths.append(os.path.join(get_user_data_dir(), "credentials.json"))
    paths.append(os.path.expanduser(f"~/.local/share/{SERVER_NAME}/credentials.json"))
    paths.extend(LEGACY_CRED_PATHS)
    return list(OrderedDict.fromkeys(paths))


def get_save_cred_path() -> str:
    env_path = os.environ.get("AGY_CREDENTIALS_PATH")
    if env_path:
        return os.path.expanduser(env_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(script_dir, "credentials.json")
    if os.path.exists(local_path):
        return local_path
    return os.path.join(get_user_data_dir(), "credentials.json")


def save_credentials(cred_data: dict, save_path: str) -> None:
    """Write credentials with 0600 permissions; never follow symlinks, never fall back to a looser mode."""
    parent_dir = os.path.dirname(save_path)
    if parent_dir and not os.path.isdir(parent_dir):
        os.makedirs(parent_dir, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(save_path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cred_data, f, indent=2)
    if os.name == "posix":
        os.chmod(save_path, 0o600)


# --------------------------------------------------------------------------------------
# HTTP helpers (upstream)
# --------------------------------------------------------------------------------------

def _urlopen_json(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise UpstreamHTTPError(e.code, body) from None
    except (socket.timeout, TimeoutError):
        raise SearchError("upstream request timed out", "timeout") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise SearchError("upstream request timed out", "timeout") from None
        raise SearchError(f"upstream connection failed: {e.reason}", "upstream") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise SearchError("upstream returned invalid JSON", "upstream") from None
    if not isinstance(data, dict):
        raise SearchError("upstream returned an unexpected payload", "upstream")
    return data


def post_json(url: str, body: dict, headers: dict, timeout: float = UPSTREAM_TIMEOUT) -> dict:
    data = json.dumps(body).encode("utf-8")
    hdrs = dict(headers)
    hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    return _urlopen_json(req, timeout)


def post_form(url: str, fields: dict, timeout: float = 15) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    return _urlopen_json(req, timeout)


# --------------------------------------------------------------------------------------
# Google OAuth token manager
# --------------------------------------------------------------------------------------

class TokenManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.access_token = None
        self.expires_at = 0.0
        self._warned_paths = set()

    def get_refresh_token(self) -> str:
        for path in get_possible_cred_paths():
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:  # unreadable / malformed → try next candidate
                log.debug("Failed reading %s: %s", path, e)
                continue
            token = None
            if isinstance(data, dict):
                token = data.get("refresh_token")
                if not token and isinstance(data.get("token"), dict):
                    token = data["token"].get("refresh_token")
            if token:
                if path in LEGACY_CRED_PATHS and path not in self._warned_paths:
                    self._warned_paths.add(path)
                    log.warning(
                        "Using credentials from legacy location %s; run `python3 %s login` to migrate to %s",
                        path, os.path.abspath(__file__), os.path.join(get_user_data_dir(), "credentials.json"),
                    )
                return token
        raise SearchError(
            "未检测到有效授权凭据！请先运行以下命令完成一次性登录授权：\n"
            f"  python3 {os.path.abspath(__file__)} login",
            "auth",
        )

    def invalidate(self) -> None:
        with self._lock:
            self.access_token = None
            self.expires_at = 0.0

    def get_access_token(self) -> str:
        with self._lock:
            if self.access_token and time.time() < self.expires_at - 60:
                return self.access_token
            refresh_token = self.get_refresh_token()
            log.info("Refreshing Google OAuth access token...")
            try:
                result = post_form(TOKEN_URL, {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                })
            except UpstreamHTTPError as e:
                raise SearchError(f"Google OAuth token refresh failed: HTTP {e.status} - {e.body}", "auth") from None
            token = result.get("access_token")
            if not isinstance(token, str) or not token:
                raise SearchError("Google OAuth token refresh returned no access_token", "auth")
            expires_in = result.get("expires_in", 3600)
            self.access_token = token
            self.expires_at = time.time() + (expires_in if isinstance(expires_in, (int, float)) else 3600)
            log.info("Access token refreshed successfully (valid for %ss)", expires_in)
            return token


token_manager = TokenManager()


def get_client_platform_info() -> tuple[str, str, str]:
    import platform
    if sys.platform == "darwin":
        platform_name, ua_platform = "DARWIN", "darwin"
    elif sys.platform == "win32":
        platform_name, ua_platform = "WINDOWS", "windows"
    else:
        platform_name, ua_platform = "LINUX", "linux"
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return platform_name, ua_platform, arch


# --------------------------------------------------------------------------------------
# Input normalization (shared by MCP, HTTP and CLI)
# --------------------------------------------------------------------------------------

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def normalize_query(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchError("query must be a non-empty string", "bad_request")
    query = value.strip()
    if len(query) > MAX_QUERY_LEN:
        raise SearchError(f"query exceeds {MAX_QUERY_LEN} characters", "bad_request")
    return query


def clean_domain(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SearchError("domain must be a string", "bad_request")
    clean = value.strip().lower()
    if not clean:
        return None
    if "://" in clean:
        clean = urllib.parse.urlsplit(clean).hostname or ""
    else:
        clean = clean.split("/", 1)[0].split(":", 1)[0]
    if clean.startswith("www."):
        clean = clean[4:]
    clean = clean.rstrip(".")
    if not clean:
        return None
    if not _DOMAIN_RE.match(clean):
        raise SearchError(f"invalid domain: {value!r}", "bad_request")
    return clean


def normalize_domains(value) -> list[str]:
    """Accept None, a single domain string, or a list of domain strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise SearchError("include_domains must be a list of strings", "bad_request")
    if len(value) > MAX_DOMAINS:
        raise SearchError(f"at most {MAX_DOMAINS} domains are allowed", "bad_request")
    out: list[str] = []
    for item in value:
        domain = clean_domain(item)
        if domain and domain not in out:
            out.append(domain)
    return out


def normalize_max_results(value, default: int = DEFAULT_MAX_RESULTS) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SearchError("max_results must be an integer", "bad_request")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise SearchError("max_results must be an integer", "bad_request")
    if value < 0 or value > MAX_RESULTS_LIMIT:
        raise SearchError(f"max_results must be between 0 and {MAX_RESULTS_LIMIT}", "bad_request")
    return value


def normalize_engine(value, allowed=None) -> str:
    if value is None:
        return "google"
    if not isinstance(value, str):
        raise SearchError("engine must be a string", "bad_request")
    engine = value.strip().lower() or "google"
    if engine not in ENGINES:
        raise SearchError(f"unknown engine {value!r}; use one of: {', '.join(ENGINES)}", "bad_request")
    if allowed is not None and engine not in allowed:
        raise SearchError(f"engine {engine!r} is not enabled on this server", "bad_request")
    return engine


def normalize_include_answer(value) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in ("basic", "advanced", "true"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", ""):
        return False
    raise SearchError("include_answer must be a boolean, 'basic' or 'advanced'", "bad_request")


# --------------------------------------------------------------------------------------
# Search result model
# --------------------------------------------------------------------------------------

def make_result(engine: str, query: str, answer: str, results: list[dict], search_queries: list[str]) -> dict:
    return {
        "engine": engine,
        "query": query,
        "answer": answer or "",
        "results": results,
        "search_queries": search_queries,
    }


def make_item(title, url: str, content: str = "", score: float = 0.0) -> dict:
    return {
        "title": (title or "").strip() or url,
        "url": url,
        "content": (content or "")[:MAX_SNIPPET_LEN],
        "score": round(float(score), 4),
    }


def _is_http_url(value) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def format_markdown(result: dict) -> str:
    """Render a structured result as the Markdown text used by the MCP tool / CLI."""
    label = ENGINE_LABELS.get(result.get("engine", ""), result.get("engine", "Search"))
    parts = []
    if result.get("answer"):
        parts.append(result["answer"].strip())
    lines = [f"- [{item['title']}]({item['url']})" for item in result.get("results", [])]
    if lines:
        parts.append("### 权威溯源链接 (Sources)\n" + "\n".join(lines))
    if result.get("search_queries"):
        parts.append(f"*{label} 执行查询: {', '.join(result['search_queries'])}*")
    return "\n\n".join(parts) if parts else f"No results returned by {label}."


# --------------------------------------------------------------------------------------
# Engine: Google (Gemini grounding via CloudCode PA)
# --------------------------------------------------------------------------------------

def _parse_google_response(query: str, data: dict, max_results: int) -> dict:
    response = data.get("response")
    if not isinstance(response, dict):
        raise SearchError("Google Grounding returned an unexpected response shape", "upstream")
    candidates = response.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return make_result("google", query, "", [], [])
    candidate = candidates[0]

    parts = (candidate.get("content") or {}).get("parts") or []
    answer = "\n".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()

    metadata = candidate.get("groundingMetadata") or {}
    search_queries = [q for q in (metadata.get("webSearchQueries") or []) if isinstance(q, str)]
    chunks = metadata.get("groundingChunks") or []
    supports = metadata.get("groundingSupports") or []

    # groundingSupports maps spans of the model's answer to the chunks that back them.
    # NOTE: segment.text is the model's answer text, not an excerpt from the page.
    chunk_hits: Counter = Counter()
    chunk_texts: dict[int, list[str]] = {}
    for support in supports:
        if not isinstance(support, dict):
            continue
        segment = support.get("segment") or {}
        text = segment.get("text") if isinstance(segment, dict) else None
        for idx in support.get("groundingChunkIndices") or []:
            if isinstance(idx, int) and not isinstance(idx, bool):
                chunk_hits[idx] += 1
                if isinstance(text, str) and text.strip():
                    chunk_texts.setdefault(idx, []).append(text.strip())
    total_hits = sum(chunk_hits.values())

    results: list[dict] = []
    seen: set[str] = set()
    for idx, chunk in enumerate(chunks):
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if not isinstance(web, dict):
            continue
        uri = web.get("uri")
        if not _is_http_url(uri) or uri in seen:
            continue
        seen.add(uri)
        snippet = " ".join(OrderedDict.fromkeys(chunk_texts.get(idx, [])))
        score = chunk_hits[idx] / total_hits if total_hits else 0.0
        results.append(make_item(web.get("title"), uri, snippet, score))
    results.sort(key=lambda item: -item["score"])
    return make_result("google", query, answer, results[:max_results], search_queries)


def search_google(query: str, domains: tuple = (), max_results: int = DEFAULT_MAX_RESULTS) -> dict:
    platform_name, ua_platform, arch = get_client_platform_info()
    google_search: dict = {}
    if domains:
        google_search["includedDomains"] = list(domains)
    body = {
        "project": "aicode-consumers",
        "model": "gemini-2.5-flash",
        "request": {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "tools": [{"googleSearch": google_search}],
        },
    }
    data = None
    for attempt in (1, 2):
        access_token = token_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": f"antigravity/1.15.8 {ua_platform}/{arch}",
            "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
            "Client-Metadata": json.dumps({"ideType": "ANTIGRAVITY", "platform": platform_name, "pluginType": "GEMINI"}),
        }
        try:
            data = post_json(API_URL, body, headers)
            break
        except UpstreamHTTPError as e:
            if e.status == 401 and attempt == 1:
                log.info("Google API returned 401; refreshing access token once and retrying")
                token_manager.invalidate()
                continue
            kind = "rate_limited" if e.status == 429 else "upstream"
            raise SearchError(f"Google Grounding API error: HTTP {e.status} - {e.body}", kind) from None
    return _parse_google_response(query, data, max_results)


# --------------------------------------------------------------------------------------
# Engines: OpenAI-Responses-style APIs (xAI Grok, OpenAI/Codex)
# --------------------------------------------------------------------------------------

def _parse_responses_output(data: dict):
    texts: list[str] = []
    queries: list[str] = []
    sources: "OrderedDict[str, str]" = OrderedDict()
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "web_search_call":
            action = item.get("action") or {}
            if not isinstance(action, dict):
                continue
            for q in action.get("queries") or []:
                if isinstance(q, str):
                    queries.append(q)
            if isinstance(action.get("query"), str):
                queries.append(action["query"])
            for source in action.get("sources") or []:
                if isinstance(source, dict) and _is_http_url(source.get("url")):
                    sources.setdefault(source["url"], source.get("title") or "")
        elif kind == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    texts.append(part["text"])
                for ann in part.get("annotations") or []:
                    if isinstance(ann, dict) and ann.get("type") == "url_citation" and _is_http_url(ann.get("url")):
                        sources.setdefault(ann["url"], ann.get("title") or "")
    return "\n".join(texts).strip(), sources, list(OrderedDict.fromkeys(queries))


def _strip_tracking(url: str) -> str:
    """Drop the `utm_source=openai` marker OpenAI appends to cited URLs."""
    parts = urllib.parse.urlsplit(url)
    if "utm_source=" not in parts.query:
        return url
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != "utm_source"]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(kept)))


def _responses_to_result(engine: str, query: str, data: dict, max_results: int) -> dict:
    answer, sources, queries = _parse_responses_output(data)
    deduped: "OrderedDict[str, str]" = OrderedDict()
    for url, title in sources.items():
        deduped.setdefault(_strip_tracking(url), title)
    answer = answer.replace("?utm_source=openai)", ")").replace("&utm_source=openai)", ")")
    results = [make_item(title, url) for url, title in deduped.items()]
    return make_result(engine, query, answer, results[:max_results], queries)


def _toml_value(path: str, key: str) -> str | None:
    if not os.path.isfile(path):
        return None
    prefix = key + " ="
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(prefix):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def pi_auth_path() -> str:
    """Location of the pi coding agent's credential store (read-only use)."""
    explicit = os.environ.get("AGY_PI_AUTH_PATH")
    if explicit:
        return os.path.expanduser(explicit)
    agent_dir = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join("~", ".pi", "agent")
    return os.path.join(os.path.expanduser(agent_dir), "auth.json")


def pi_access_token(provider: str) -> dict | None:
    """Return pi's stored OAuth entry for `provider` if it has an unexpired access token.

    The file is only read, never written: pi owns the refresh tokens and rotates them
    itself, so refreshing here could log pi out. When the access token has expired the
    caller gets a clear error telling the user to use that provider once in pi.
    Returns None when pi has no credential for the provider.
    """
    path = pi_auth_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            store = json.load(f)
    except Exception as e:
        raise SearchError(f"pi auth file unreadable ({path}): {type(e).__name__}", "auth") from None
    entry = store.get(provider) if isinstance(store, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("access"), str) or not entry["access"]:
        return None
    expires = entry.get("expires")
    if isinstance(expires, (int, float)) and expires / 1000 < time.time() + 60:
        raise SearchError(
            f"pi's {provider} access token has expired; use a {provider} model in pi once so it refreshes "
            "the token (this server never refreshes pi credentials itself)", "auth")
    return entry


def _jwt_claim(token: str, claim: str):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii"))).get(claim)
    except Exception:
        return None


class GrokAuth:
    """xAI credentials: XAI_API_KEY, then pi's `xai` login (read-only), then the grok CLI cache (~/.grok/auth.json)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.access_token = None
        self.expires_at = 0.0

    def get(self) -> str:
        api_key = os.environ.get("XAI_API_KEY")
        if api_key:
            return api_key
        pi_entry = pi_access_token("xai")
        if pi_entry:
            return pi_entry["access"]
        with self._lock:
            if self.access_token and time.time() < self.expires_at - 60:
                return self.access_token
            path = os.path.expanduser("~/.grok/auth.json")
            if not os.path.isfile(path):
                raise SearchError("Grok auth missing: set XAI_API_KEY, log in to xAI in pi, or log in with the grok CLI (~/.grok/auth.json)", "auth")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    store = json.load(f)
                entry = next(iter(store.values())) if isinstance(store, dict) and store else {}
            except Exception as e:
                raise SearchError(f"Grok auth.json unreadable: {e}", "auth") from None
            client_id = entry.get("oidc_client_id") if isinstance(entry, dict) else None
            refresh_token = entry.get("refresh_token") if isinstance(entry, dict) else None
            if not client_id or not refresh_token:
                raise SearchError("Grok auth.json has no refresh_token", "auth")
            try:
                result = post_form("https://auth.x.ai/oauth2/token", {
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                })
            except UpstreamHTTPError as e:
                raise SearchError(f"Grok token refresh failed: HTTP {e.status} - {e.body}", "auth") from None
            token = result.get("access_token")
            if not isinstance(token, str) or not token:
                raise SearchError("Grok token refresh returned no access_token", "auth")
            self.access_token = token
            self.expires_at = time.time() + int(result.get("expires_in") or 3600)
            return token


grok_auth = GrokAuth()


def search_grok(query: str, domains: tuple = (), max_results: int = DEFAULT_MAX_RESULTS) -> dict:
    token = grok_auth.get()
    tool: dict = {"type": "web_search"}
    if domains:
        tool["allowed_domains"] = list(domains[:5])  # xAI limit: max 5
    body = {
        "model": os.environ.get("GROK_SEARCH_MODEL", "grok-4.5"),
        "input": query,
        "tools": [tool],
        "max_output_tokens": ANSWER_MAX_TOKENS,
    }
    try:
        data = post_json(
            os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/") + "/responses",
            body,
            {"Authorization": f"Bearer {token}"},
            timeout=max(UPSTREAM_TIMEOUT, 45),
        )
    except UpstreamHTTPError as e:
        kind = "auth" if e.status in (401, 403) else "rate_limited" if e.status == 429 else "upstream"
        if e.status == 402:
            kind = "auth"
        raise SearchError(f"Grok search error: HTTP {e.status} - {e.body}", kind) from None
    return _responses_to_result("grok", query, data, max_results)


def _codex_config() -> tuple[str | None, str]:
    key = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        auth_path = os.path.expanduser("~/.codex/auth.json")
        if os.path.isfile(auth_path):
            try:
                with open(auth_path, "r", encoding="utf-8") as f:
                    key = json.load(f).get("OPENAI_API_KEY")
            except Exception:
                key = None
    base = (
        os.environ.get("CODEX_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or _toml_value(os.path.expanduser("~/.codex/config.toml"), "openai_base_url")
        or "https://api.openai.com/v1"
    )
    return key, base.rstrip("/")


CHATGPT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_SEARCH_INSTRUCTIONS = (
    "You are a web search assistant. Use the web_search tool to answer the user's query "
    "with up-to-date facts. Be concise and cite sources."
)


def post_sse_responses(url: str, body: dict, headers: dict, timeout: float) -> dict:
    """POST a streaming Responses request and rebuild {"output": [...]} from the SSE events."""
    data = json.dumps(body).encode("utf-8")
    hdrs = dict(headers)
    hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "text/event-stream")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    items: list = []
    completed = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                etype = event.get("type") if isinstance(event, dict) else None
                if etype == "response.output_item.done" and isinstance(event.get("item"), dict):
                    items.append(event["item"])
                elif etype == "response.completed":
                    completed = event.get("response") or {}
                    break
                elif etype in ("response.failed", "error"):
                    err = (event.get("response") or {}).get("error") or event.get("error") or event
                    raise SearchError(f"upstream stream error: {json.dumps(err)[:300]}", "upstream")
    except urllib.error.HTTPError as e:
        raise UpstreamHTTPError(e.code, e.read().decode("utf-8", errors="replace")[:400]) from None
    except (socket.timeout, TimeoutError):
        raise SearchError("upstream request timed out", "timeout") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise SearchError("upstream request timed out", "timeout") from None
        raise SearchError(f"upstream connection failed: {e.reason}", "upstream") from None
    if completed is None and not items:
        raise SearchError("upstream stream ended without a response", "upstream")
    output = (completed or {}).get("output") or items
    return {"output": output}


def search_codex(query: str, domains: tuple = (), max_results: int = DEFAULT_MAX_RESULTS) -> dict:
    """OpenAI web search.

    Credential order: CODEX_API_KEY / OPENAI_API_KEY / ~/.codex/auth.json (OpenAI API,
    pay-as-you-go), otherwise pi's `openai-codex` ChatGPT login (ChatGPT Codex backend,
    uses the subscription quota; read-only, never refreshed here).
    """
    tool: dict = {"type": "web_search"}
    if domains:
        tool["filters"] = {"allowed_domains": list(domains)}
    model = os.environ.get("CODEX_SEARCH_MODEL", "gpt-6-luna")
    timeout = max(UPSTREAM_TIMEOUT, 60)
    key, base = _codex_config()
    if key:
        body = {
            "model": model,
            "input": query,
            "tools": [tool],
            "max_output_tokens": ANSWER_MAX_TOKENS,
            "store": False,
        }
        try:
            data = post_json(base + "/responses", body, {"Authorization": f"Bearer {key}"}, timeout=timeout)
        except UpstreamHTTPError as e:
            kind = "auth" if e.status in (401, 402, 403) else "rate_limited" if e.status == 429 else "upstream"
            raise SearchError(f"Codex search error: HTTP {e.status} - {e.body}", kind) from None
        return _responses_to_result("codex", query, data, max_results)

    entry = pi_access_token("openai-codex")
    if not entry:
        raise SearchError(
            "Codex credentials missing: set CODEX_API_KEY / OPENAI_API_KEY, or log in to OpenAI Codex (ChatGPT) in pi",
            "auth")
    account_id = entry.get("accountId")
    if not account_id:
        auth_claim = _jwt_claim(entry["access"], "https://api.openai.com/auth")
        account_id = auth_claim.get("chatgpt_account_id") if isinstance(auth_claim, dict) else None
    if not account_id:
        raise SearchError("pi's openai-codex credential has no ChatGPT account id", "auth")
    body = {
        "model": model,
        "store": False,
        "stream": True,  # required by the ChatGPT Codex backend
        "instructions": CODEX_SEARCH_INSTRUCTIONS,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": query}]}],
        "tools": [tool],
        # max_output_tokens is not reliably supported by this backend (pi omits it too)
        "text": {"verbosity": "low"},
    }
    headers = {
        "Authorization": f"Bearer {entry['access']}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": SERVER_NAME,
    }
    url = os.environ.get("CODEX_CHATGPT_URL", CHATGPT_CODEX_URL)
    try:
        data = post_sse_responses(url, body, headers, timeout)
    except UpstreamHTTPError as e:
        kind = "auth" if e.status in (401, 402, 403) else "rate_limited" if e.status == 429 else "upstream"
        raise SearchError(f"Codex (ChatGPT) search error: HTTP {e.status} - {e.body}", kind) from None
    return _responses_to_result("codex", query, data, max_results)

ENGINES = {
    "google": search_google,
    "grok": search_grok,
    "codex": search_codex,
}


def run_search(query: str, domains=(), max_results: int = DEFAULT_MAX_RESULTS, engine: str = "google") -> dict:
    """Run a normalized search. Raises SearchError on failure."""
    fn = ENGINES.get(engine)
    if fn is None:
        raise SearchError(f"unknown engine {engine!r}", "bad_request")
    return fn(query, tuple(domains), max_results)


def perform_engine_search(query, domain=None, engine="google", max_results=DEFAULT_MAX_RESULTS) -> tuple[str, bool]:
    """Backwards-compatible helper: returns (markdown_text, is_error)."""
    try:
        result = run_search(
            normalize_query(query), normalize_domains(domain), normalize_max_results(max_results), normalize_engine(engine)
        )
        return format_markdown(result), False
    except SearchError as e:
        return f"Error: {e}", True


# --------------------------------------------------------------------------------------
# OAuth login wizard
# --------------------------------------------------------------------------------------

def run_oauth_flow() -> None:
    print("=" * 72)
    print(f" [{SERVER_NAME}] Google OAuth 2.0 自动授权向导")
    print("=" * 72)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    redirect_uri = f"http://localhost:{port}/auth/callback"
    state = secrets.token_urlsafe(16)
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    login_url = AUTH_URL + "?" + urllib.parse.urlencode(auth_params)

    holder = {"code": None, "error": None}
    done = threading.Event()

    def finish(code=None, error=None):
        if done.is_set():
            return
        holder["code"], holder["error"] = code, error
        done.set()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _page(self, status: int, heading: str, body: str = ""):
            html = (
                "<html><body style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
                f"text-align:center;padding-top:60px\"><h2>{heading}</h2><p style=\"color:#555\">{body}</p></body></html>"
            )
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/auth/callback":
                self._page(404, "Not found")
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if "error" in qs:
                err_code = qs.get("error", ["unknown"])[0]
                self._page(400, f"授权失败或被拒绝: {err_code}")
                finish(error=f"authorization denied: {err_code}")
                return
            if qs.get("state", [""])[0] != state:
                self._page(400, "CSRF 校验失败: state 不匹配")
                return
            if "code" in qs:
                self._page(200, "授权成功！", "已成功获取 Google 授权码，您可以关闭此浏览器标签并返回终端。")
                finish(code=qs["code"][0])
                return
            self._page(400, "缺少 code 参数")

    httpd = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("\n1. 请在浏览器中打开以下链接，使用 Google 账号登录并同意授权：\n")
    print(f"{login_url}\n")
    print("2. 若处于本地桌面环境，已尝试自动唤起默认浏览器；")
    print(f"3. 登录完成后 Google 会自动回调至 {redirect_uri}")
    print("   (若处于远程无头服务器无法自动回调，可将最终重定向网址或其中 code= 后面的代码粘贴在下方)\n")
    try:
        webbrowser.open(login_url)
    except Exception:
        pass
    print("正在等待授权回调 (按 Ctrl+C 可取消，或在此输入重定向链接/code): ", end="", flush=True)

    def manual_input_worker():
        try:
            line = sys.stdin.readline().strip()
        except Exception:
            return
        if not line or done.is_set():
            return
        if "code=" in line:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(line).query)
            if "state" in qs and qs["state"][0] != state:
                finish(error="pasted callback URL has a mismatched state (possible stale/foreign link)")
                return
            if "code" in qs:
                finish(code=qs["code"][0])
                return
            finish(error="pasted URL contains no code parameter")
            return
        finish(code=line)

    threading.Thread(target=manual_input_worker, daemon=True).start()

    try:
        while not done.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("\n已取消。")
        httpd.shutdown()
        sys.exit(130)
    httpd.shutdown()

    if holder["error"]:
        print(f"\n\n[错误] {holder['error']}")
        sys.exit(1)

    print("\n\n已成功捕获授权码，正在向 Google 换取 Refresh Token...")
    try:
        token_resp = post_form(TOKEN_URL, {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": holder["code"],
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
    except UpstreamHTTPError as e:
        print(f"\n[错误] 换取 Token 失败: HTTP {e.status} - {e.body}")
        sys.exit(1)
    except SearchError as e:
        print(f"\n[错误] 换取 Token 失败: {e}")
        sys.exit(1)

    refresh_token = token_resp.get("refresh_token")
    save_path = get_save_cred_path()
    if not refresh_token and os.path.isfile(save_path):
        try:
            with open(save_path, "r", encoding="utf-8") as f:
                refresh_token = json.load(f).get("refresh_token")
        except Exception:
            refresh_token = None
    if not refresh_token:
        print("[错误] 未能获取有效的 refresh_token，请尝试重新授权并确保勾选所需权限。")
        sys.exit(1)

    save_credentials({"refresh_token": refresh_token, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, save_path)
    print("=" * 72)
    print(" 恭喜！授权配置成功！")
    print(f" 凭据已安全保存至: {save_path}")
    print(" 现在你可以直接启动 MCP 并在任何 Agent/IDE 中使用了，无需安装任何 agy 客户端！")
    print("=" * 72)


# --------------------------------------------------------------------------------------
# MCP (JSON-RPC 2.0 over stdio)
# --------------------------------------------------------------------------------------

SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索关键词或查询语句 (Search query or research question).",
        },
        "domain": {
            "type": "string",
            "description": "可选。优先/限定的目标域名 (Optional domain to prioritize, e.g. 'docs.python.org', 'github.com').",
        },
        "max_results": {
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_RESULTS_LIMIT,
            "default": DEFAULT_MAX_RESULTS,
            "description": "Maximum number of source links to return.",
        },
        "engine": {
            "type": "string",
            "enum": list(ENGINES),
            "default": "google",
            "description": "Search engine: google (Gemini Grounding, default), grok (xAI web_search), codex (OpenAI / ChatGPT Codex web_search).",
        },
        "toolAction": {"type": "string", "description": "Agent status metadata, ignored by the server (e.g. 'Searching the web')."},
        "toolSummary": {"type": "string", "description": "Agent task metadata, ignored by the server (e.g. 'Web search')."},
    },
    "required": ["query"],
}

MCP_TOOLS = [
    {
        "name": "search_web",
        "description": "Perform a real-time web search powered by Google Search Grounding with authoritative citations, fresh results, and direct source URLs.",
        "inputSchema": SEARCH_INPUT_SCHEMA,
    },
    {
        "name": "agy_web_search",
        "description": "Deprecated alias of search_web, kept for backwards compatibility with older configurations. Prefer search_web.",
        "inputSchema": SEARCH_INPUT_SCHEMA,
    },
]
TOOL_NAMES = {tool["name"] for tool in MCP_TOOLS}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _rpc_result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(name, arguments) -> dict:
    if not isinstance(name, str) or name not in TOOL_NAMES:
        raise RpcError(-32602, f"Unknown tool: {name}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise RpcError(-32602, "Invalid params: arguments must be an object")
    try:
        query = normalize_query(arguments.get("query"))
        domains = normalize_domains(arguments.get("domain"))
        max_results = normalize_max_results(arguments.get("max_results"))
        engine = normalize_engine(arguments.get("engine"))
    except SearchError as e:
        raise RpcError(-32602, f"Invalid params: {e}") from None

    log.info("search tool=%s engine=%s domains=%s query_len=%d", name, engine, domains, len(query))
    try:
        result = run_search(query, domains, max_results, engine)
    except SearchError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    except Exception as e:  # never let an upstream surprise kill the stdio loop
        log.exception("unexpected error while searching")
        return {"content": [{"type": "text", "text": f"Internal error: {type(e).__name__}: {e}"}], "isError": True}
    # Only a text block: several MCP clients render structuredContent in addition to the
    # text, which would double the tokens spent on every search. Structured data is
    # available through the HTTP API and `--search ... --json`.
    return {
        "content": [{"type": "text", "text": format_markdown(result)}],
        "isError": False,
    }


def handle_request(line: str) -> dict | None:
    """Process one JSON-RPC message. Returns the response object, or None for notifications."""
    if not line.strip():
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return _rpc_error(None, -32700, "Parse error: Invalid JSON payload.")
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "Invalid Request: Expected a JSON object.")

    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params")
    is_notification = "id" not in msg

    if not isinstance(method, str):
        return None if is_notification else _rpc_error(msg_id, -32600, "Invalid Request: missing method.")
    if params is not None and not isinstance(params, dict):
        return None if is_notification else _rpc_error(msg_id, -32602, "Invalid params: expected an object.")
    params = params or {}

    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
            return _rpc_result(msg_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            })
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return _rpc_result(msg_id, {})
        if method == "tools/list":
            return _rpc_result(msg_id, {"tools": MCP_TOOLS})
        if method == "tools/call":
            return _rpc_result(msg_id, _call_tool(params.get("name"), params.get("arguments")))
        if is_notification:
            return None
        return _rpc_error(msg_id, -32601, f"Method not found: {method}")
    except RpcError as e:
        return _rpc_error(msg_id, e.code, str(e))


def write_message(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve_stdio() -> None:
    log.info("%s %s running in stdio MCP mode.", SERVER_NAME, __version__)
    for line in sys.stdin:
        try:
            response = handle_request(line)
        except Exception as e:
            log.exception("unhandled error while processing request")
            response = _rpc_error(None, -32603, f"Internal error: {type(e).__name__}")
        if response is not None:
            write_message(response)


# --------------------------------------------------------------------------------------
# Tavily-compatible HTTP API
# --------------------------------------------------------------------------------------

class HttpSettings:
    def __init__(self, tokens, engines, default_engine="google", max_concurrency=4, rate_limit_per_minute=30,
                 trust_proxy=False, max_body_bytes=16 * 1024):
        self.tokens = tuple(tokens)
        self.engines = tuple(engines)
        self.default_engine = default_engine
        self.max_concurrency = max(1, int(max_concurrency))
        self.rate_limit_per_minute = int(rate_limit_per_minute)
        self.trust_proxy = bool(trust_proxy)
        self.max_body_bytes = int(max_body_bytes)


class RateLimiter:
    """Simple per-key token bucket (capacity = per-minute limit, refilled continuously)."""

    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.refill_per_sec = per_minute / 60.0
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        if self.capacity <= 0:
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > 10000:
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < 120}
            tokens, last = self._buckets.get(key, [self.capacity, now])
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
            if tokens >= 1.0:
                self._buckets[key] = [tokens - 1.0, now]
                return True, 0.0
            self._buckets[key] = [tokens, now]
            return False, (1.0 - tokens) / self.refill_per_sec


class HttpError(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


SEARCH_ERROR_STATUS = {
    "bad_request": 400,
    "auth": 502,          # server-side upstream credentials problem, not the client's fault
    "upstream": 502,
    "timeout": 504,
    "rate_limited": 503,
    "internal": 500,
}


def parse_search_request(payload: dict, settings: HttpSettings) -> dict:
    """Validate a Tavily-style search request. Unknown fields are ignored."""
    if not isinstance(payload, dict):
        raise SearchError("request body must be a JSON object", "bad_request")
    engine = normalize_engine(payload.get("engine") or settings.default_engine, allowed=settings.engines)
    domains = normalize_domains(payload.get("include_domains"))
    if not domains and payload.get("domain") is not None:
        domains = normalize_domains(payload.get("domain"))
    return {
        "query": normalize_query(payload.get("query")),
        "domains": domains,
        "max_results": normalize_max_results(payload.get("max_results")),
        "include_answer": normalize_include_answer(payload.get("include_answer")),
        "engine": engine,
    }


def to_tavily_response(result: dict, include_answer: bool, elapsed: float, request_id: str) -> dict:
    return {
        "query": result["query"],
        "answer": (result.get("answer") or None) if include_answer else None,
        "images": [],
        "results": [
            {
                "title": item["title"],
                "url": item["url"],
                "content": item["content"],
                "score": item["score"],
                "raw_content": None,
            }
            for item in result["results"]
        ],
        "response_time": round(elapsed, 2),
        "request_id": request_id,
        "engine": result["engine"],
        "search_queries": result.get("search_queries", []),
    }


class SearchHTTPHandler(http.server.BaseHTTPRequestHandler):
    server_version = "agy-websearch"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # per-connection socket timeout (slowloris protection)

    # -- plumbing ---------------------------------------------------------------------
    def log_message(self, fmt, *args):  # route BaseHTTPRequestHandler chatter to debug logging
        log.debug("http %s - " + fmt, self.client_ip(), *args)

    @property
    def settings(self) -> HttpSettings:
        return self.server.settings  # type: ignore[attr-defined]

    def client_ip(self) -> str:
        if self.settings.trust_proxy:
            real_ip = self.headers.get("X-Real-IP")
            if real_ip:
                return real_ip.strip()
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[-1].strip()
        return self.client_address[0]

    def send_json(self, status: int, payload: dict, extra_headers: dict | None = None, close: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_error_json(self, status: int, message: str, extra_headers: dict | None = None) -> None:
        self.send_json(status, {"detail": {"error": message}}, extra_headers, close=True)

    def authorized(self) -> bool:
        tokens = self.settings.tokens
        if not tokens:
            return True
        presented = None
        auth = self.headers.get("Authorization", "")
        if auth[:7].lower() == "bearer ":
            presented = auth[7:].strip()
        elif self.headers.get("X-API-Key"):
            presented = self.headers.get("X-API-Key", "").strip()
        if not presented:
            return False
        presented_b = presented.encode("utf-8")
        return any(hmac.compare_digest(presented_b, token.encode("utf-8")) for token in tokens)

    def read_json_body(self) -> dict:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            raise HttpError(411, "Content-Length required")
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise HttpError(411, "Content-Length required")
        try:
            length = int(length_header)
        except ValueError:
            raise HttpError(400, "invalid Content-Length") from None
        if length < 0:
            raise HttpError(400, "invalid Content-Length")
        if length > self.settings.max_body_bytes:
            raise HttpError(413, f"request body exceeds {self.settings.max_body_bytes} bytes")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type and content_type != "application/json":
            raise HttpError(415, "Content-Type must be application/json")
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            raise HttpError(400, "incomplete request body")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "request body is not valid JSON") from None
        if not isinstance(payload, dict):
            raise HttpError(400, "request body must be a JSON object")
        return payload

    # -- routes -----------------------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/health", "/healthz"):
            self.send_json(200, {"status": "ok"})
        elif path in ("/search", "/v1/search"):
            self.send_error_json(405, "use POST /search", {"Allow": "POST"})
        else:
            self.send_error_json(404, "not found")

    do_HEAD = do_GET

    def _method_not_allowed(self):
        self.send_error_json(405, "method not allowed", {"Allow": "GET, POST"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def do_POST(self):
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        path = urllib.parse.urlsplit(self.path).path
        status = 500
        engine = "-"
        try:
            if path not in ("/search", "/v1/search"):
                raise HttpError(404, "not found")
            if not self.authorized():
                raise HttpError(401, "Unauthorized: missing or invalid API key",
                                {"WWW-Authenticate": 'Bearer realm="agy-websearch"'})
            allowed, retry_after = self.server.limiter.allow(self.client_ip())  # type: ignore[attr-defined]
            if not allowed:
                raise HttpError(429, "Too Many Requests", {"Retry-After": str(max(1, int(retry_after + 0.999)))})
            payload = self.read_json_body()
            try:
                params = parse_search_request(payload, self.settings)
            except SearchError as e:
                raise HttpError(400, str(e)) from None
            engine = params["engine"]

            semaphore = self.server.semaphore  # type: ignore[attr-defined]
            if not semaphore.acquire(timeout=5):
                raise HttpError(503, "server busy, retry later", {"Retry-After": "2"})
            try:
                result = run_search(params["query"], params["domains"], params["max_results"], engine)
            except SearchError as e:
                status = SEARCH_ERROR_STATUS.get(e.kind, 502)
                log.warning("http %s search failed (%s): %s", request_id, e.kind, e)
                raise HttpError(status, str(e)) from None
            finally:
                semaphore.release()

            status = 200
            body = to_tavily_response(result, params["include_answer"], time.monotonic() - started, request_id)
            self.send_json(200, body, {"X-Request-Id": request_id})
        except HttpError as e:
            status = e.status
            self.send_error_json(e.status, str(e), dict(e.headers, **{"X-Request-Id": request_id}))
        except (ConnectionError, socket.timeout):
            status = 499
        except Exception as e:
            status = 500
            log.exception("http %s unhandled error", request_id)
            try:
                self.send_error_json(500, f"internal error ({type(e).__name__})", {"X-Request-Id": request_id})
            except Exception:
                pass
        finally:
            log.info("http %s %s %s -> %d engine=%s %.2fs id=%s",
                     self.client_ip(), self.command, path, status, engine, time.monotonic() - started, request_id)


def build_http_server(host: str, port: int, settings: HttpSettings) -> http.server.ThreadingHTTPServer:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class Server(http.server.ThreadingHTTPServer):
        address_family = family
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 64

    server = Server((host, port), SearchHTTPHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.limiter = RateLimiter(settings.rate_limit_per_minute)  # type: ignore[attr-defined]
    server.semaphore = threading.BoundedSemaphore(settings.max_concurrency)  # type: ignore[attr-defined]
    return server


def _is_loopback_host(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


def load_http_tokens(token_file: str | None) -> list[str]:
    tokens: list[str] = []
    env_value = os.environ.get("AGY_HTTP_TOKENS") or os.environ.get("AGY_HTTP_TOKEN") or ""
    tokens.extend(t for t in re.split(r"[,\s]+", env_value) if t)
    if token_file:
        with open(os.path.expanduser(token_file), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tokens.append(line)
    tokens = list(OrderedDict.fromkeys(tokens))
    for token in tokens:
        if len(token) < 16:
            raise SystemExit("HTTP API tokens must be at least 16 characters long (generate one with `server.py gen-token`).")
    return tokens


def serve_http(args) -> None:
    tokens = load_http_tokens(args.token_file)
    if args.no_auth:
        if not _is_loopback_host(args.host):
            raise SystemExit("--no-auth is only allowed when binding to a loopback address.")
        tokens = []
    elif not tokens:
        raise SystemExit(
            "No API token configured. Set AGY_HTTP_TOKENS (comma-separated) or pass --token-file, "
            "or use --no-auth together with a loopback --host. Generate a token with `server.py gen-token`."
        )
    engines = [e.strip().lower() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in ENGINES]
    if unknown or not engines:
        raise SystemExit(f"--engines must be a comma-separated subset of: {', '.join(ENGINES)}")
    default_engine = engines[0]

    settings = HttpSettings(
        tokens=tokens,
        engines=engines,
        default_engine=default_engine,
        max_concurrency=args.max_concurrency,
        rate_limit_per_minute=args.rate_limit,
        trust_proxy=args.trust_proxy,
        max_body_bytes=args.max_body,
    )
    server = build_http_server(args.host, args.port, settings)

    def _stop(signum, _frame):
        log.info("signal %s received, shutting down HTTP server", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    if not _is_loopback_host(args.host):
        log.warning("Binding to a non-loopback address (%s); make sure a reverse proxy / firewall is in front.", args.host)
    log.info(
        "%s %s HTTP API listening on http://%s:%d (auth=%s, engines=%s, rate_limit=%d/min, concurrency=%d, trust_proxy=%s)",
        SERVER_NAME, __version__, args.host, server.server_address[1], "token" if tokens else "none",
        ",".join(engines), settings.rate_limit_per_minute, settings.max_concurrency, settings.trust_proxy,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.info("HTTP server stopped")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description=f"{SERVER_NAME}: lightweight Google Search Grounding MCP server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "modes:\n"
            "  (no command)      stdio MCP server\n"
            "  login | auth      interactive OAuth login wizard\n"
            "  serve             Tavily-compatible HTTP API (POST /search)\n"
            "  gen-token         print a random API token for `serve`\n"
            "  --search QUERY    one-off search from the command line\n"
        ),
    )
    parser.add_argument("command", nargs="?", choices=["login", "auth", "serve", "stdio", "gen-token"])
    parser.add_argument("--search", type=str, help="Execute a quick search from the command line")
    parser.add_argument("--domain", type=str, help="Optional domain filter (e.g. 'python.org')")
    parser.add_argument("--engine", type=str, default="google", help="google, grok, or codex")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS, help="Max source links (0-20)")
    parser.add_argument("--json", action="store_true", help="With --search: print the structured JSON result")
    parser.add_argument("--login", action="store_true", help="Run the OAuth login flow (same as `login`)")

    http_group = parser.add_argument_group("serve options")
    http_group.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    http_group.add_argument("--port", type=int, default=8765, help="Bind port (default 8765)")
    http_group.add_argument("--token-file", help="File with one API token per line (or set AGY_HTTP_TOKENS)")
    http_group.add_argument("--no-auth", action="store_true", help="Disable auth (loopback bind only)")
    http_group.add_argument("--engines", default="google",
                            help="Comma-separated engines exposed over HTTP; the first is the default (default: google)")
    http_group.add_argument("--max-concurrency", type=int, default=4, help="Concurrent upstream searches (default 4)")
    http_group.add_argument("--rate-limit", type=int, default=30, help="Requests per minute per client IP, 0 = off (default 30)")
    http_group.add_argument("--max-body", type=int, default=16 * 1024, help="Max request body bytes (default 16384)")
    http_group.add_argument("--trust-proxy", action="store_true",
                            help="Trust X-Real-IP / X-Forwarded-For from the reverse proxy for rate limiting and logs")
    return parser


def main(argv=None) -> None:
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args, _unknown = build_arg_parser().parse_known_args(argv)

    if args.command in ("login", "auth") or args.login:
        run_oauth_flow()
        return
    if args.command == "gen-token":
        print(secrets.token_urlsafe(32))
        return
    if args.command == "serve":
        serve_http(args)
        return
    if args.search:
        try:
            result = run_search(
                normalize_query(args.search),
                normalize_domains(args.domain),
                normalize_max_results(args.max_results),
                normalize_engine(args.engine),
            )
        except SearchError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_markdown(result))
        return

    serve_stdio()


if __name__ == "__main__":
    main()
