#!/usr/bin/env python3
"""
agy-websearch-mcp: Ultra-lightweight Google Search Grounding MCP Server
Reverse-engineered from Google Antigravity CLI (agy).

Features:
- Pure Python 3 standard library: 0 external dependencies (no pip install required).
- No heavy agy binary or daemon processes needed (~200MB saved).
- Built-in one-click OAuth 2.0 automated login (`python3 server.py login`).
- Direct connection to Google CloudCode Grounding API with real-time search & citations.
- Standard JSON-RPC 2.0 Stdio MCP Server interface.
"""

import sys
import os
import json
import time
import socket
import threading
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
import http.server
import logging
import argparse

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[%(asctime)s] %(levelname)s: %(message)s")

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
    "https://www.googleapis.com/auth/experimentsandconfigs"
]

def get_user_data_dir() -> str:
    """Return standard cross-platform user data directory."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, "agy-websearch-mcp")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/agy-websearch-mcp")
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(xdg_data, "agy-websearch-mcp")

def get_possible_cred_paths():
    paths = []
    if os.environ.get("AGY_CREDENTIALS_PATH"):
        paths.append(os.path.expanduser(os.environ["AGY_CREDENTIALS_PATH"]))
    # Current script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(script_dir, "credentials.json"))
    # Working directory
    paths.append(os.path.abspath("credentials.json"))
    # Standard cross-platform user data directory
    paths.append(os.path.join(get_user_data_dir(), "credentials.json"))
    # Additional common fallback directories
    paths.append(os.path.expanduser("~/.local/share/agy-websearch-mcp/credentials.json"))
    paths.append(os.path.expanduser("~/.config/agy-websearch-mcp/credentials.json"))
    paths.append(os.path.expanduser("~/.agy-websearch-mcp/credentials.json"))
    # Existing native agy token fallback
    paths.append(os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token"))
    return paths

def get_save_cred_path():
    if os.environ.get("AGY_CREDENTIALS_PATH"):
        return os.path.expanduser(os.environ["AGY_CREDENTIALS_PATH"])
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(script_dir, "credentials.json")
    if os.path.exists(local_path):
        return local_path
    user_data_dir = get_user_data_dir()
    os.makedirs(user_data_dir, exist_ok=True)
    return os.path.join(user_data_dir, "credentials.json")

class TokenManager:
    def __init__(self):
        self.access_token = None
        self.expires_at = 0

    def get_refresh_token(self):
        for path in get_possible_cred_paths():
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if "refresh_token" in data:
                            return data["refresh_token"]
                        if "token" in data and "refresh_token" in data["token"]:
                            return data["token"]["refresh_token"]
                except Exception as e:
                    logging.debug(f"Failed reading {path}: {e}")

        raise RuntimeError(
            "未检测到有效授权凭据！请先运行以下命令完成一次性登录授权：\n"
            f"  python3 {os.path.abspath(__file__)} login"
        )

    def get_access_token(self):
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token

        refresh_token = self.get_refresh_token()
        logging.info("Refreshing Google OAuth access token...")

        payload = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }).encode("utf-8")

        req = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                self.access_token = result["access_token"]
                expires_in = result.get("expires_in", 3600)
                self.expires_at = time.time() + expires_in
                logging.info(f"Access token refreshed successfully (valid for {expires_in}s)")
                return self.access_token
        except Exception as e:
            logging.error(f"Failed to refresh OAuth token: {e}")
            raise

token_manager = TokenManager()

def get_client_platform_info():
    """Dynamically detect operating system and architecture for upstream API headers."""
    import platform
    sys_name = sys.platform
    if sys_name.startswith("linux"):
        platform_name = "LINUX"
        ua_platform = "linux"
    elif sys_name == "darwin":
        platform_name = "DARWIN"
        ua_platform = "darwin"
    elif sys_name == "win32":
        platform_name = "WINDOWS"
        ua_platform = "windows"
    else:
        platform_name = "LINUX"
        ua_platform = "linux"

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "amd64"

    return platform_name, ua_platform, arch

def perform_search(query: str) -> str:
    access_token = token_manager.get_access_token()
    platform_name, ua_platform, arch = get_client_platform_info()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"antigravity/1.15.8 {ua_platform}/{arch}",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps({"ideType": "ANTIGRAVITY", "platform": platform_name, "pluginType": "GEMINI"})
    }

    body = {
        "project": "aicode-consumers",
        "model": "gemini-2.5-flash",
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": query}]
                }
            ],
            "tools": [{"googleSearch": {}}]
        }
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        return f"Error executing Google Grounding Search: HTTP {e.code} - {err_msg}"
    except Exception as e:
        return f"Error executing Google Grounding Search: {str(e)}"

    # Parse response
    candidates = resp_data.get("response", {}).get("candidates", [])
    if not candidates:
        return "No results returned by Google Grounding."

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text_content = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()

    metadata = candidate.get("groundingMetadata", {})
    web_queries = metadata.get("webSearchQueries", [])
    grounding_chunks = metadata.get("groundingChunks", [])

    sources_md = []
    seen_urls = set()
    for chunk in grounding_chunks:
        web_info = chunk.get("web", {})
        title = web_info.get("title", "").strip() or "Web Source"
        uri = web_info.get("uri", "")
        if uri and uri not in seen_urls:
            seen_urls.add(uri)
            sources_md.append(f"- [{title}]({uri})")

    out_parts = []
    if text_content:
        out_parts.append(text_content)

    if sources_md:
        out_parts.append("\n### 权威溯源链接 (Sources)")
        out_parts.extend(sources_md)

    if web_queries:
        out_parts.append(f"\n*Google 执行查询: {', '.join(web_queries)}*")

    return "\n\n".join(out_parts)

# Automated OAuth Login Flow
def run_oauth_flow():
    print("=" * 72)
    print(" [agy-websearch-mcp] Google OAuth 2.0 自动授权向导")
    print("=" * 72)

    # Find free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    redirect_uri = f"http://localhost:{port}/auth/callback"
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent"
    }
    login_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    auth_code_holder = {"code": None, "server": None}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/auth/callback":
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    auth_code_holder["code"] = qs["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    html = """
                    <html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; text-align: center; padding-top: 60px;">
                    <h2 style="color: #2e7d32;">授权成功！</h2>
                    <p style="color: #555;">已成功获取 Google 授权码，您可以关闭此浏览器标签并返回终端。</p>
                    </body></html>
                    """
                    self.wfile.write(html.encode("utf-8"))
                    threading.Thread(target=auth_code_holder["server"].shutdown).start()
                    return
            self.send_response(400)
            self.end_headers()

    httpd = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    auth_code_holder["server"] = httpd
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    print(f"\n1. 请在浏览器中打开以下链接，使用 Google 账号登录并同意授权：\n")
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
            if line and not auth_code_holder["code"]:
                if "code=" in line:
                    parsed = urllib.parse.urlparse(line)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "code" in qs:
                        auth_code_holder["code"] = qs["code"][0]
                else:
                    auth_code_holder["code"] = line
                auth_code_holder["server"].shutdown()
        except Exception:
            pass

    input_thread = threading.Thread(target=manual_input_worker)
    input_thread.daemon = True
    input_thread.start()

    while not auth_code_holder["code"]:
        time.sleep(0.5)

    code = auth_code_holder["code"]
    print("\n\n已成功捕获授权码，正在向 Google 换取 Refresh Token...")

    payload = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
    }).encode("utf-8")

    req = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_resp = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"\n[错误] 换取 Token 失败: HTTP {e.code} - {err}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[错误] 换取 Token 失败: {e}")
        sys.exit(1)

    refresh_token = token_resp.get("refresh_token")
    save_path = get_save_cred_path()

    if not refresh_token and os.path.exists(save_path):
        with open(save_path, "r", encoding="utf-8") as f:
            old = json.load(f)
            refresh_token = old.get("refresh_token")

    if not refresh_token:
        print("[错误] 未能获取有效的 refresh_token，请尝试重新授权并确保勾选所需权限。")
        sys.exit(1)

    cred_data = {
        "refresh_token": refresh_token,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(cred_data, f, indent=2)

    print("=" * 72)
    print(" 恭喜！授权配置成功！")
    print(f" 凭据已安全保存至: {save_path}")
    print(" 现在你可以直接启动 MCP 并在任何 Agent/IDE 中使用了，无需安装任何 agy 客户端！")
    print("=" * 72)

# MCP JSON-RPC Server
def handle_request(line: str):
    if not line.strip():
        return
    try:
        msg = json.loads(line)
    except Exception:
        return

    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    if method == "initialize":
        response = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "agy-websearch-mcp",
                    "version": "1.0.0"
                }
            }
        }
        send_response(response)
    elif method == "notifications/initialized":
        pass
    elif method == "ping":
        send_response({"jsonrpc": "2.0", "id": msg_id, "result": {}})
    elif method == "tools/list":
        response = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "agy_web_search",
                        "description": "Powerful real-time web search powered by Google Search Grounding with authoritative citations, fresh results, and direct source URLs.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query or research question to search on Google."
                                }
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        }
        send_response(response)
    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        if tool_name == "agy_web_search":
            query = args.get("query", "")
            logging.info(f"Received search query: {query}")
            search_result = perform_search(query)
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": search_result
                        }
                    ],
                    "isError": False
                }
            }
            send_response(response)
        else:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {tool_name}"
                }
            }
            send_response(response)

def send_response(res):
    output = json.dumps(res, ensure_ascii=False)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()

def main():
    parser = argparse.ArgumentParser(description="agy-websearch-mcp: Ultra-lightweight Google Search Grounding MCP Server")
    parser.add_argument("command", nargs="?", choices=["login", "auth"], help="Run automated OAuth login flow")
    parser.add_argument("--search", type=str, help="Execute a quick test search from command line")
    parser.add_argument("--login", action="store_true", help="Run automated OAuth login flow")

    args, unknown = parser.parse_known_args()

    if args.command in ("login", "auth") or args.login:
        run_oauth_flow()
        return

    if args.search:
        print(f"Searching: {args.search}\n")
        res = perform_search(args.search)
        print(res)
        return

    logging.info("agy-websearch-mcp running in Stdio MCP mode.")
    for line in sys.stdin:
        handle_request(line)

if __name__ == "__main__":
    main()
