# agy-websearch-mcp 🔍

> **超轻量级 Google Search Grounding MCP 服务（逆向提取自 Google Antigravity / `agy`）**  
> **零额外依赖（仅 Python 3 标准库）、免安装 200MB+ 的 `agy` 二进制客户端、内置一键 OAuth 2.0 自动授权向导。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/Protocol-MCP%202024--11--05-green.svg)](https://modelcontextprotocol.io/)

---

## 🌟 核心特性 (Features)

- 🪶 **极致轻量（Zero Dependency）**：纯 Python 3 原生标准库编写，**无需 `pip install` 任何第三方包**，无需 Node.js，无需常驻守护进程。
- 🚫 **免装庞大客户端**：彻底摆脱原生 `agy`（Google Antigravity CLI）高达 209MB 的二进制文件及运行时的 LanguageServer 内存开销（节省 >250MB 内存）。
- 🎯 **强大的 Google 实时搜索**：直连 Google 官方 Gemini Grounding 上游网关（`generateContent` + `googleSearch`），返回权威结构化答复、实际搜索词与溯源引用链接（Sources）。
- 🔑 **内置一键自动授权向导**：运行 `python3 server.py login` 即可通过浏览器一键登录并自动捕获凭据，同时支持远程无头服务器（Headless/SSH）手动粘贴 Code 授权。
- 🔄 **凭据无感自动续期**：自动在内存中维护 Google OAuth Access Token，并在过期前自动刷新，保证长期稳定运行。
- 🔌 **全生态 Agent 即插即用**：采用标准 Model Context Protocol (MCP) JSON-RPC 2.0 Stdio 协议，完美支持 **Kimi Code**、**Claude Code**、**Cursor**、**Cline**、**Windsurf** 等各类 AI 工具。

---

## 🏗️ 架构与对比 (Architecture & Comparison)

`agy` 官方客户端的搜索能力之所以极强，是因为它底层挂载了 Google 原生的 **Search Grounding** 引擎。然而直接运行 `agy` 存在本地端口随机分配、必须保持臃肿进程常驻等痛点。

| 对比维度 | 原生 `agy` 客户端 | **agy-websearch-mcp（本项目）** |
| :--- | :--- | :--- |
| **磁盘占用** | ~210 MB (ELF 64-bit 独立包) | **~15 KB** (单个 Python 脚本) |
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
```

---

## 🧩 工具定义与入参 (Tool Schema)

本 MCP 服务对齐了 `agy` 原版内置的搜索工具形态，注册并提供以下工具：

- **`search_web`**（推荐，原版标准命名）
- **`agy_web_search`**（全兼容别名）

### 入参 Schema

| 参数字段 | 类型 | 必填 | 作用与说明 |
| :--- | :--- | :---: | :--- |
| **`query`** | `string` | **是** | 核心检索词。支持 Google 高级检索语法（如 `site:`, `filetype:`, 双引号等）。 |
| **`domain`** | `string` | 否 | **域名限定/优先**。例如 `python.org` 或 `docs.rs`，原生映射为 Google 上游的 `includedDomains` 过滤。 |
| **`toolAction`** | `string` | 否 | 原版 Agent 动作描述元数据（如 `'Searching the web'`）。 |
| **`toolSummary`** | `string` | 否 | 原版 Agent 任务分类摘要元数据（如 `'Web search'`）。 |

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

### 4. Cline / Roo Code (VS Code Extension)
在扩展的 MCP 设置文件 `cline_mcp_settings.json` 中添加：
```json
{
  "mcpServers": {
    "agy-search": {
      "command": "python3",
      "args": ["/path/to/agy-websearch-mcp/server.py"],
      "disabled": false,
      "autoApprove": ["agy_web_search"]
    }
  }
}
```

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

凭据查找优先级：
1. `AGY_CREDENTIALS_PATH` 环境变量指定的位置
2. 当前脚本同级目录 `./credentials.json`
3. 当前执行工作目录 `credentials.json`
4. 跨平台标准用户目录（Linux: `~/.local/share/agy-websearch-mcp/credentials.json`，macOS: `~/Library/Application Support/agy-websearch-mcp/credentials.json`，Windows: `%APPDATA%\agy-websearch-mcp\credentials.json`）
5. 本机既有 agy 客户端缓存 `~/.gemini/antigravity-cli/antigravity-oauth-token`（如有）

---

## 🔒 安全与隐私 (Security & Privacy)

- **直接通信**：所有检索请求直接发送至 Google 官方域名（`oauth2.googleapis.com` 与 `daily-cloudcode-pa.googleapis.com`），无任何中间转发层。
- **本地存储**：OAuth Refresh Token 仅保存在本机用户专有权限目录下，绝不上报或跨设备共享。
- **无硬编码机密**：项目代码中不包含任何个人敏感 Token，请勿将你的 `credentials.json` 提交至公共仓库。

---

## 📄 开源许可 (License)

本项目基于 [MIT License](LICENSE) 开源。仅供学习交流、个人效率提升与自动化研究使用。
