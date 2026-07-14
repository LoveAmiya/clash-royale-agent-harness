# Clash Royale Agent Harness

[English](#english) | [中文](#中文)

---

## English

Clash Royale Agent Harness is a FastAPI-based agent workflow for answering Clash Royale team-preparation questions from structured match, deck, card, and retrieval data.

The system combines rule-based query parsing, skill routing, local JSON grounding, optional retrieval augmentation, traceable execution, and a browser chat interface. It is designed around a controlled domain workflow rather than a generic chatbot loop.

### Highlights

- FastAPI backend
- Browser chat interface
- Structured query parsing with optional LLM fallback
- Skill registry and skill routing
- Grounded answers from local schedule, deck, and card JSON data
- Optional RAG path for open-ended preparation questions
- Traceable execution harness
- Local evaluation suite and unit tests
- Dockerfile and PowerShell helper scripts

### Repository Layout

```text
data/                    Local schedule, card, deck, and retrieval data
evaluation/              Evaluation cases and metrics
harness/                 Skill execution and trace harness
planner/                 Lightweight planning layer
skills/                  Skill registry and domain skills
tests/                   Unit tests
app_config.py            Environment-driven configuration
answer_builder.py        Local JSON answer builder
query_parser.py          Natural-language query parser
query_answering.py       Direct query and RAG routing
runtime_multi.py         FastAPI backend entry point
web_app.py               Browser chat UI
client.py                CLI client
hybrid_retriever.py      BM25 + dense retrieval helper
retrieval_postprocess.py Reranking, compression, and reference formatting
```

### Getting Started

```powershell
git clone https://github.com/LoveAmiya/clash-royale-agent-harness.git
cd clash-royale-agent-harness
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a local environment file only when optional providers are needed:

```powershell
Copy-Item .env.example .env
```

Keep `.env` out of version control.

### Run the Backend

```powershell
.\run_backend.ps1
```

Or:

```powershell
python runtime_multi.py
```

Default backend:

```text
http://127.0.0.1:8091
```

Health check:

```powershell
curl http://127.0.0.1:8091/health
```

### Run the Browser UI

Open another PowerShell window:

```powershell
.\run_web.ps1
```

Open:

```text
http://127.0.0.1:8080
```

Example questions:

- 我们第五轮打谁？
- 下一轮对手是谁？
- 使用率第三的卡牌是什么？
- 现在热门卡组有哪些？
- 帮我根据下一轮对手做备战建议。

### API Usage

```powershell
curl -X POST http://127.0.0.1:8091/process `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"下一轮对手是谁？\"}"
```

### Tests

```powershell
.\run_tests.ps1
```

Or:

```powershell
python -m unittest discover -s tests
```

### Docker

```powershell
docker build -t clash-royale-agent .
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
```

### Configuration

The main configuration keys are documented in `.env.example`.

Common values:

```text
OPENAI_API_KEY=your_key_here
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
OPENAI_MODEL=gpt-4o-mini
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
```

For local runs, set `OPENAI_API_KEY` in the current PowerShell session before starting the backend. Do not put a real key in source code or commit it to Git. `OPENAI_MODEL` is optional and defaults to `gpt-4o-mini`.

Direct JSON-backed questions can run with local data only. Environment analysis and team-preparation questions use an LLM to synthesize a bounded evidence pack from local snapshots, then append source URLs and an explicit data-time boundary. Open-ended retrieval answers can be enabled with an LLM key and embedding service.

### Data Freshness and Sources

`top_decks.json` and `cards_meta.json` preserve the original RoyaleAPI page URLs with each record, but they are repository snapshots rather than live data. The agent must not claim that they are current-version or opponent-specific intelligence.

This project intentionally does not scrape RoyaleAPI from an LLM prompt or call its retired public API: RoyaleAPI's own legacy documentation states that its [public API was sunset](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/getting_started.md), and its [legacy popular-decks endpoint is not implemented](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/endpoints/popular_decks.md). A future live-data adapter should use a maintained, documented and authorized provider with a deterministic ingestion job, not unrestricted model browsing.

### Workflow Overview

```text
User Question
  -> Query Parser
  -> Skill Router
  -> Local JSON Answer, Evidence Synthesis, or RAG Path
  -> Trace Harness
  -> API Response / Browser UI
```

---

## 中文

Clash Royale Agent Harness 是一个基于 FastAPI 的 Agent 工作流项目，用于根据赛程、卡组、卡牌和检索数据回答《皇室战争》战队备战问题。

系统结合了规则解析、Skill 路由、本地 JSON 事实数据、可选检索增强、可追踪执行链路和浏览器聊天界面。项目重点是受控领域工作流，而不是泛化聊天机器人。

### 项目亮点

- FastAPI 后端
- 浏览器聊天界面
- 结构化问题解析，并支持可选 LLM fallback
- Skill 注册表和 Skill 路由
- 基于本地赛程、卡组、卡牌 JSON 数据的事实回答
- 面向开放备战问题的可选 RAG 链路
- 可追踪执行 harness
- 本地评测集和单元测试
- Dockerfile 和 PowerShell 辅助脚本

### 项目结构

```text
data/                    本地赛程、卡牌、卡组和检索数据
evaluation/              评测用例和指标
harness/                 Skill 执行与 trace harness
planner/                 轻量规划层
skills/                  Skill 注册表和领域技能
tests/                   单元测试
app_config.py            环境变量驱动配置
answer_builder.py        本地 JSON 答案构建
query_parser.py          自然语言问题解析
query_answering.py       直接查询与 RAG 路由
runtime_multi.py         FastAPI 后端入口
web_app.py               浏览器聊天界面
client.py                命令行客户端
hybrid_retriever.py      BM25 + dense retrieval helper
retrieval_postprocess.py 重排、压缩和引用格式化
```

### 快速开始

```powershell
git clone https://github.com/LoveAmiya/clash-royale-agent-harness.git
cd clash-royale-agent-harness
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

只有在需要可选 provider 时，才需要创建本地环境变量文件：

```powershell
Copy-Item .env.example .env
```

不要把 `.env` 提交到版本库。

### 启动后端

```powershell
.\run_backend.ps1
```

或：

```powershell
python runtime_multi.py
```

默认后端地址：

```text
http://127.0.0.1:8091
```

健康检查：

```powershell
curl http://127.0.0.1:8091/health
```

### 启动浏览器界面

另开一个 PowerShell 窗口：

```powershell
.\run_web.ps1
```

浏览器打开：

```text
http://127.0.0.1:8080
```

示例问题：

- 我们第五轮打谁？
- 下一轮对手是谁？
- 使用率第三的卡牌是什么？
- 现在热门卡组有哪些？
- 帮我根据下一轮对手做备战建议。

### API 调用

```powershell
curl -X POST http://127.0.0.1:8091/process `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"下一轮对手是谁？\"}"
```

### 测试

```powershell
.\run_tests.ps1
```

或：

```powershell
python -m unittest discover -s tests
```

### Docker

```powershell
docker build -t clash-royale-agent .
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
```

### 配置

主要配置项在 `.env.example` 中说明。

常用值：

```text
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
OPENAI_MODEL=gpt-4o-mini
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
```

本地运行前，请在当前 PowerShell 窗口设置 `OPENAI_API_KEY`，不要把真实 Key 写进源码或提交到 Git。`OPENAI_MODEL` 是可选配置，默认值为 `gpt-4o-mini`。

直接基于 JSON 的查询可以只依赖本地数据运行。环境分析和战队备战类问题会由 LLM 对本地静态证据包做受限综合，并在答案末尾附来源链接和数据时效边界。开放式检索回答可以通过 LLM Key 和 embedding 服务启用。

### 数据时效与来源

`top_decks.json` 与 `cards_meta.json` 会在每条记录中保留原始 RoyaleAPI 页面 URL，但它们是仓库快照，不是本次回答时实时获取的数据。Agent 不会把这些快照说成“当前版本实时结论”或“对手真实情报”。

项目不会让 LLM 直接抓取 RoyaleAPI 页面，也不会调用已经停止维护的旧公开 API：RoyaleAPI 自己的旧版文档明确说明[公开 API 已停止服务](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/getting_started.md)，[旧版热门卡组接口也未实现](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/endpoints/popular_decks.md)。后续若接入实时数据，应选择仍在维护、文档完整且授权明确的数据提供方，并通过确定性的采集任务更新，而不是让模型无限制浏览网页。

### 工作流概览

```text
User Question
  -> Query Parser
  -> Skill Router
  -> Local JSON Answer, Evidence Synthesis, or RAG Path
  -> Trace Harness
  -> API Response / Browser UI
```
