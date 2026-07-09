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
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
SILICONFLOW_MODEL_NAME=Qwen/Qwen3-8B
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
```

Direct JSON-backed questions can run with local data only. Open-ended retrieval answers can be enabled with an LLM key and embedding service.

### Workflow Overview

```text
User Question
  -> Query Parser
  -> Skill Router
  -> Local JSON Answer or RAG Path
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
SILICONFLOW_MODEL_NAME=Qwen/Qwen3-8B
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
```

直接基于 JSON 的查询可以只依赖本地数据运行。开放式检索回答可以通过 LLM Key 和 embedding 服务启用。

### 工作流概览

```text
User Question
  -> Query Parser
  -> Skill Router
  -> Local JSON Answer or RAG Path
  -> Trace Harness
  -> API Response / Browser UI
```
