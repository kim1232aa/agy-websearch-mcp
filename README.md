# agy-websearch-mcp 🔍

> **超轻量级 Google Search Grounding MCP 服务（逆向提取自 Google Antigravity / `agy`）**  
> **零额外依赖（仅 Python 3 标准库）、免安装 200MB+ 的 `agy` 二进制客户端、内置一键 OAuth 2.0 自动授权向导。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/Protocol-MCP%202024--11--05%20~%202025--06--18-green.svg)](https://modelcontextprotocol.io/)

---

## 🌟 核心特性 (Features)

- 🪶 **极致轻量（Zero Dependency）**：纯 Python 3 原生标准库编写，**无需 `pip install` 任何第三方包**，无需 Node.js，无需常驻守护进程。
- 🚫 **免装庞大客户端**：彻底摆脱原生 `agy`（Google Antigravity CLI）高达 209MB 的二进制文件及运行时的 LanguageServer 内存开销（节省 >250MB 内存）。
- 🎯 **强大的 Google 实时搜索**：直连 Google 官方 Gemini Grounding 上游网关（`generateContent` + `googleSearch`），返回权威结构化答复、实际搜索词与溯源引用链接（Sources）。
- 🔑 **内置一键自动授权向导**：运行 `python3 server.py login` 即可通过浏览器一键登录并自动捕获凭据，同时支持远程无头服务器（Headless/SSH）手动粘贴 Code 授权。
- 🔄 **凭据无感自动续期**：自动在内存中维护 Google OAuth Access Token，并在过期前自动刷新，保证长期稳定运行。
- 🔌 **全生态 Agent 即插即用**：采用标准 Model Context Protocol (MCP) JSON-RPC 2.0 Stdio 协议，完美支持 **Kimi Code**、**Claude Code**、**Cursor**、**Cline**、**Windsurf**、**pi (pi-mcp-adapter)** 等各类 AI 工具。
- 🌐 **可选 Tavily 兼容 HTTP API**：`python3 server.py serve` 提供 `POST /search`（请求/响应字段对齐 Tavily Search），内置 Token 鉴权、限流、并发与请求体限制，可放在反向代理后对外提供服务。

---

## 🏗️ 架构与对比 (Architecture & Comparison)

`agy` 官方客户端的搜索能力之所以极强，是因为它底层挂载了 Google 原生的 **Search Grounding** 引擎。然而直接运行 `agy` 存在本地端口随机分配、必须保持臃肿进程常驻等痛点。

| 对比维度 | 原生 `agy` 客户端 | **agy-websearch-mcp（本项目）** |
| :--- | :--- | :--- |
| **磁盘占用** | ~210 MB (ELF 64-bit 独立包) | **~60 KB** (单个 Python 脚本) |
| **外部运行依赖** | 需安装完整的 agy 及相关工具链 | **纯 Python 3 标准库**（0 外部库） |
| **内存/后台消耗** | 后台常驻 LanguageServer（占用 200MB+） | **按需执行**，检索时仅临时占用数 MB 内存 |
| **搜索响应延迟** | 较长（进程唤醒与握手耗时约 15~18 秒） | **极快**（直连上游网关，约 2~4 秒） |
| **跨机器迁移** | 繁琐，每台机器都需配置完整环境 | **极简**，拷走脚本和凭据即可运行 |
| **授权获取** | 必须安装并运行完整客户端交互登录 | **单命令一键自动授权**（`server.py login`） |

---

## 🚀 快速上手 (Quick Start)

### 1. 克隆仓库
```bash
git clone https://github.com/kim1232aa/agy-websearch-mcp.git
cd agy-websearch-mcp
```

### 2. 一键授权登录 (仅需一次)
直接执行内置的授权向导：
```bash
python3 server.py login
```
- **本地桌面环境**：会自动调起默认浏览器，使用 Google 账号登录并确认授权，网页提示成功后终端将自动换取并保存凭据。
- **远程服务器 / SSH 环境**：终端会输出授权 URL，在本地电脑浏览器中打开该 URL 登录，并将重定向后的最终网址粘贴回终端即可。

*(凭据将安全保存在跨平台标准用户目录（Linux: `~/.local/share/agy-websearch-mcp/credentials.json`, macOS: `~/Library/Application Support/agy-websearch-mcp/credentials.json`, Windows: `%APPDATA%\agy-websearch-mcp\credentials.json`）或当前项目目录下的 `credentials.json`，且已配置 `.gitignore` 防止意外提交)*

### 3. 命令行快速检索测试
你可以在命令行直接测试搜索效果（支持通过 `--domain` 限定域名）：
```bash
python3 server.py --search "2026年最新科技新闻"
python3 server.py --search "Python downloads" --domain "python.org"
python3 server.py --engine grok --search "gold price today"
python3 server.py --engine codex --search "silver price today"
python3 server.py --search "Python 3.13 release date" --max-results 3 --json   # 输出结构化 JSON
```
搜索失败时命令以非 0 退出码结束，错误信息输出到 stderr。

---

## 🧩 工具定义与入参 (Tool Schema)

本 MCP 服务对齐了 `agy` 原版内置的搜索工具形态，注册并提供以下工具：

- **`search_web`**（推荐，原版标准命名）
- **`agy_web_search`**（已弃用的兼容别名，仅为不破坏旧配置而保留，新配置请使用 `search_web`）

### 入参 Schema

| 参数字段 | 类型 | 必填 | 作用与说明 |
| :--- | :--- | :---: | :--- |
| **`query`** | `string` | **是** | 核心检索词。支持 Google 高级检索语法（如 `site:`, `filetype:`, 双引号等）。 |
| **`domain`** | `string` | 否 | **域名优先**。例如 `python.org` 或 `docs.rs`，映射为 Google 上游的 `includedDomains`。实测该参数是"优先"而非严格过滤，结果中仍可能出现其他域名。 |
| **`max_results`** | `integer` | 否 | 返回的来源链接数量上限，0–20，默认 10。 |
| **`engine`** | `string` | 否 | 搜索引擎：`google`（默认，Gemini Grounding）、`grok`（xAI `web_search`）、`codex`（OpenAI Responses `web_search`）。 |
| **`toolAction`** | `string` | 否 | 原版 Agent 动作描述元数据（如 `'Searching the web'`），服务端忽略。 |
| **`toolSummary`** | `string` | 否 | 原版 Agent 任务分类摘要元数据（如 `'Web search'`），服务端忽略。 |

### 返回内容

- `content[0].text`：Markdown 文本（答复 + `### 权威溯源链接 (Sources)` + 实际执行的搜索词）。
- 不返回 `structuredContent`（部分客户端会把它与文本一起展示，导致 token 翻倍）；需要结构化数据请用 HTTP API 或 `--search ... --json`。
- 参数非法 / 未知工具：JSON-RPC 错误 `-32602`；上游搜索失败：`isError: true` 的工具结果。

> **关于来源链接**：Google Grounding 返回的 `url` 是 `vertexaisearch.cloud.google.com/grounding-api-redirect/...` 跳转链接，`title` 通常只是域名；`content` 来自 `groundingSupports`，是**模型答复中由该来源支撑的片段**，不是网页原文摘录；`score` 为该来源被引用的占比。

---

## 🛠️ MCP 客户端接入配置 (Client Configuration)

> **💡 路径提示**：请将下方配置中的 `/path/to/agy-websearch-mcp/server.py` 替换为你实际克隆的项目绝对路径（Windows 用户形如 `C:\\path\\to\\agy-websearch-mcp\\server.py`）。

### 1. Kimi Code CLI
在 `~/.kimi-code/mcp.json` 中添加：
```json
{
  "mcpServers": {
    "agy-search": {
      "command": "python3",
      "args": ["/path/to/agy-websearch-mcp/server.py"]
    }
  }
}
```

### 2. Claude Code
在终端直接通过 CLI 注册：
```bash
claude mcp add agy-search python3 /path/to/agy-websearch-mcp/server.py
```
或在 `~/.claude.json` 中配置：
```json
{
  "mcpServers": {
    "agy-search": {
      "command": "python3",
      "args": ["/path/to/agy-websearch-mcp/server.py"]
    }
  }
}
```

### 3. Cursor
在 Cursor 设置的 `Features` -> `MCP Servers` 中点击 `Add new MCP server`：
- **Name**: `agy-search`
- **Type**: `command`
- **Command**: `python3 /path/to/agy-websearch-mcp/server.py`

### 4. pi (pi-mcp-adapter)
在 `~/.config/mcp/mcp.json`（用户级共享配置）中添加：
```json
{
  "mcpServers": {
    "agy-websearch": {
      "command": "python3",
      "args": ["/path/to/agy-websearch-mcp/server.py"]
    }
  }
}
```

### 5. Cline / Roo Code (VS Code Extension)
在扩展的 MCP 设置文件 `cline_mcp_settings.json` 中添加：
```json
{
  "mcpServers": {
    "agy-search": {
      "command": "python3",
      "args": ["/path/to/agy-websearch-mcp/server.py"],
      "disabled": false,
      "autoApprove": ["search_web", "agy_web_search"]
    }
  }
}
```

---

## 🌍 Tavily 兼容 HTTP API (可选)

部分工具只能接入 HTTP 搜索 API（例如支持"Tavily Compatible"自定义源的插件），无法直接加载 stdio MCP。为此提供可选的 HTTP 模式：

```bash
# 生成一个随机 Token（至少 16 位）
python3 server.py gen-token

# 仅监听本机，放在 Nginx / Caddy 等反向代理之后
AGY_HTTP_TOKENS=<token> python3 server.py serve --host 127.0.0.1 --port 8765
```

```bash
curl -s http://127.0.0.1:8765/search \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"query":"latest Debian stable release","max_results":5,"include_answer":true}'
```

| 接口 | 说明 |
| :--- | :--- |
| `POST /search`（别名 `/v1/search`） | 请求字段：`query`（必填）、`max_results`（0–20，默认 10）、`include_answer`（bool / `basic` / `advanced`）、`include_domains`（域名数组，最多 10 个）、`engine`（可选，须在 `--engines` 中启用）。其他 Tavily 字段会被忽略。 |
| `GET /health` | 健康检查（无需鉴权），返回 `{"status":"ok"}` |

响应字段：`query`、`answer`（未请求时为 `null`）、`images`（恒为空数组）、`results[]`（`title`、`url`、`content`、`score`、`raw_content`=null）、`response_time`、`request_id`，另附 `engine`、`search_queries`。错误格式为 `{"detail":{"error":"..."}}`：400 参数错误、401 鉴权失败、411/413/415 请求体问题、429 限流（带 `Retry-After`）、502 上游失败、503 繁忙、504 上游超时。

**安全默认值**
- 未配置 Token 时拒绝启动；`--no-auth` 只允许配合回环地址使用。Token 支持 `Authorization: Bearer` 或 `X-API-Key`，使用常量时间比较。
- 默认只监听 `127.0.0.1`；按客户端 IP 限流（`--rate-limit`，默认 30 次/分钟）、上游并发上限（`--max-concurrency`，默认 4）、请求体上限（`--max-body`，默认 16KB）、连接超时 30 秒。
- 默认只开放 `google` 引擎（`--engines google,grok,codex` 可显式开放更多），客户端无法指定上游地址。注意：开放 `codex`/`grok` 后，持有 API Token 的调用方会消耗对应账号（例如 ChatGPT 订阅）的额度。
- 仅当前面确实有反向代理时才加 `--trust-proxy`，此时才使用 `X-Real-IP` / `X-Forwarded-For` 做限流与日志；日志只记录查询长度，不记录查询内容和 Token。
- 对公网暴露时请务必使用 HTTPS，并把 Token 存放在仓库之外（例如 systemd `EnvironmentFile` 或 `--token-file`）。

---

## 🌐 代理与网络环境配置 (可选)

本项目原生直连 Google 官方 API。如果你的网络环境无法直接访问 Google（例如处于中国大陆网络环境下），可以通过以下两种方式配置代理：

### 方式一：在 MCP 客户端配置中注入代理环境变量
以 Kimi Code / Cline 为例，在配置中添加 `env` 字段：
```json
{
  "mcpServers": {
    "agy-search": {
      "command": "python3",
      "args": ["/path/to/agy-websearch-mcp/server.py"],
      "env": {
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "http://127.0.0.1:7890"
      }
    }
  }
}
```
*(注：请将 `7890` 替换为你实际运行的本地代理客户端端口，如 7890、10808 等)*

### 方式二：系统全局环境变量
脚本会自动遵循操作系统的标准环境变量：
- `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
- `AGY_CREDENTIALS_PATH`：手动指定凭据文件路径（可选）

---

## 🔧 环境变量与高级选项

| 环境变量 | 默认值 | 作用说明 |
| :--- | :--- | :--- |
| `HTTP_PROXY` / `HTTPS_PROXY` | 无 | 指定访问 Google API 的 HTTP/HTTPS 代理服务器 |
| `AGY_CREDENTIALS_PATH` | 自动查找 | 手动指定凭据文件 `credentials.json` 的存放路径 |
| `AGY_HTTP_TOKENS` | 无 | `serve` 模式的 API Token，多个用逗号分隔 |
| `AGY_LOG_LEVEL` | `INFO` | 日志级别（日志输出到 stderr） |
| `AGY_UPSTREAM_TIMEOUT` | `30` | 上游请求超时（秒） |
| `AGY_ANSWER_MAX_TOKENS` | `800` | grok / codex 引擎答复的 `max_output_tokens` |
| `XAI_API_KEY` / `XAI_BASE_URL` | 无 / `https://api.x.ai/v1` | grok 引擎凭据；未设置时依次读取 pi 的 `xai` 登录、grok CLI 的 `~/.grok/auth.json` |
| `GROK_SEARCH_MODEL` | `grok-4.5` | grok 引擎模型 |
| `CODEX_API_KEY` 或 `OPENAI_API_KEY` | 无 | codex 引擎凭据（按量计费的 OpenAI API）；未设置时读取 `~/.codex/auth.json`，再没有则使用 pi 的 `openai-codex`（ChatGPT 订阅）登录 |
| `AGY_PI_AUTH_PATH` | `$PI_CODING_AGENT_DIR/auth.json`（默认 `~/.pi/agent/auth.json`） | pi 凭据文件位置。**只读**：本服务从不刷新或写回 pi 的 token，access token 过期后请在 pi 里用一次对应模型让 pi 自己刷新 |
| `CODEX_BASE_URL` 或 `OPENAI_BASE_URL` | `https://api.openai.com/v1` | codex 引擎接口地址（也会读取 `~/.codex/config.toml` 的 `openai_base_url`） |
| `CODEX_SEARCH_MODEL` | `gpt-6-luna` | codex 引擎模型 |

凭据查找优先级：
1. `AGY_CREDENTIALS_PATH` 环境变量指定的位置
2. 当前脚本同级目录 `./credentials.json`
3. 跨平台标准用户目录（Linux: `~/.local/share/agy-websearch-mcp/credentials.json`，macOS: `~/Library/Application Support/agy-websearch-mcp/credentials.json`，Windows: `%APPDATA%\agy-websearch-mcp\credentials.json`）
4. 旧版位置（读取时会在日志中提示迁移）：`~/.config/agy-websearch-mcp/credentials.json`、`~/.agy-websearch-mcp/credentials.json`、本机既有 agy 客户端缓存 `~/.gemini/antigravity-cli/antigravity-oauth-token`

> 出于安全考虑，不再从"当前工作目录"读取 `credentials.json`（避免在不受信任的目录中启动时读到他人放置的凭据）。凭据文件写入时强制 `0600` 权限且拒绝跟随符号链接。

---

## 🧪 测试

```bash
python3 -m unittest discover -s tests -v
```
测试完全离线（上游引擎被替换为假实现），覆盖参数校验、Google / Responses 响应解析、MCP 协议错误处理、凭据文件权限以及 HTTP API 的鉴权、限流和错误码。

---

## 🔒 安全与隐私 (Security & Privacy)

- **直接通信**：所有检索请求直接发送至 Google 官方域名（`oauth2.googleapis.com` 与 `daily-cloudcode-pa.googleapis.com`），无任何中间转发层。
- **本地存储**：OAuth Refresh Token 仅保存在本机用户专有权限目录下，绝不上报或跨设备共享。
- **无硬编码机密**：项目代码中不包含任何个人敏感 Token，请勿将你的 `credentials.json` 提交至公共仓库。

---

## 📄 开源许可 (License)

本项目基于 [MIT License](LICENSE) 开源。仅供学习交流、个人效率提升与自动化研究使用。
