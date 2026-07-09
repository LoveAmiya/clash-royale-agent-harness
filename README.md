# Clash Royale Skill-based Agent Harness

[English](#english) | [中文](#中文)

---

## English

Clash Royale Skill-based Agent Harness is a local FastAPI project for answering Clash Royale team preparation questions.

The project is not a generic chatbot. It focuses on a controlled business-style Agent workflow:

- Parse the user question into structured fields.
- Route the question to a registered skill.
- Answer precise schedule, deck, and card questions from local JSON data.
- Use optional retrieval for open-ended card/deck preparation questions.
- Return traceable responses through a backend API and a browser chat UI.

The default demo is local and deterministic for direct data queries. Optional LLM/RAG behavior requires your own environment variables and local embedding service.

### Features

- FastAPI backend
- Browser chat interface
- Rule-based query parser with optional LLM fallback
- Skill registry and skill routing
- Direct JSON answers for schedule, deck, and card queries
- Optional RAG path for open-ended strategy questions
- Traceable execution harness
- Local evaluation and unit tests
- Dockerfile and PowerShell run scripts

### Project Structure

```text
data/                 Local schedule, card, deck, and RAG data
evaluation/           Local evaluation cases and metrics
harness/              Skill execution and trace harness
planner/              Lightweight planning layer
skills/               Skill registry and business skills
tests/                Unit tests
app_config.py         Environment-driven configuration
answer_builder.py     Local JSON answer builder
query_parser.py       Natural language parser
query_answering.py    Direct query vs RAG routing
runtime_multi.py      FastAPI backend entry
web_app.py            Browser chat UI
client.py             CLI client
hybrid_retriever.py   BM25 + dense retrieval helper
retrieval_postprocess.py  Rerank, compression, and references
```

### Quick Start

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy the environment template if you need local configuration:

```powershell
Copy-Item .env.example .env
```

Do not commit `.env`.

### Run Backend

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

### Run Web UI

Open another PowerShell window:

```powershell
.\run_web.ps1
```

Open:

```text
http://127.0.0.1:8080
```

Demo questions:

- 我们第五轮打谁？
- 下一轮对手是谁？
- 使用率第三的卡牌是什么？
- 现在热门卡组有哪些？
- 帮我根据下一轮对手做备战建议。

### Tests

```powershell
.\run_tests.ps1
```

Or:

```powershell
python -m unittest discover -s tests
```

### Docker

Build:

```powershell
docker build -t clash-royale-agent .
```

Run backend:

```powershell
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
```

### Configuration

The main environment variables are documented in `.env.example`.

Useful defaults:

```text
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
SILICONFLOW_MODEL_NAME=Qwen/Qwen3-8B
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
```

Direct JSON queries can run without a real LLM key. Open-ended RAG questions require `SILICONFLOW_API_KEY` and a working embedding service.

### Current Scope

This is a local prototype for interview/demo use, not a production online service.

Current boundaries:

- No authentication system
- No hosted deployment
- No production monitoring
- No full Docker Compose stack yet
- RAG depends on optional local embedding and LLM configuration

---

## 中文

Clash Royale Skill-based Agent Harness 是一个面向《皇室战争》战队备战场景的本地 FastAPI 项目。

它不是通用聊天机器人，而是一个受控的业务型 Agent workflow：

- 把用户问题解析成结构化字段。
- 根据解析结果路由到注册好的 skill。
- 对明确的赛程、卡组、卡牌问题，直接查询本地 JSON 数据。
- 对开放型备战/卡组分析问题，可选走检索增强链路。
- 通过后端 API 和浏览器聊天界面返回可追踪结果。

默认 demo 对直接数据查询是本地确定性的。可选 LLM/RAG 能力需要你自己的环境变量和本地 embedding 服务。

### 功能特点

- FastAPI 后端
- 浏览器聊天界面
- 规则解析器，可选 LLM fallback
- Skill registry 和 skill routing
- 基于本地 JSON 的赛程、卡组、卡牌查询
- 面向开放问题的可选 RAG 链路
- 可追踪执行 harness
- 本地评测和单元测试
- Dockerfile 和 PowerShell 运行脚本

### 项目结构

```text
data/                 本地赛程、卡牌、卡组和 RAG 数据
evaluation/           本地评测用例和指标
harness/              Skill 执行与 trace harness
planner/              轻量规划层
skills/               Skill 注册表和业务 skill
tests/                单元测试
app_config.py         环境变量驱动配置
answer_builder.py     本地 JSON 答案生成
query_parser.py       自然语言解析
query_answering.py    直接查询和 RAG 路由
runtime_multi.py      FastAPI 后端入口
web_app.py            浏览器聊天界面
client.py             命令行客户端
hybrid_retriever.py   BM25 + dense retrieval
retrieval_postprocess.py  重排、压缩和引用整理
```

### 快速开始

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果需要本地配置，复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

不要提交 `.env`。

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

### 启动网页界面

另开一个 PowerShell：

```powershell
.\run_web.ps1
```

浏览器打开：

```text
http://127.0.0.1:8080
```

可演示问题：

- 我们第五轮打谁？
- 下一轮对手是谁？
- 使用率第三的卡牌是什么？
- 现在热门卡组有哪些？
- 帮我根据下一轮对手做备战建议。

### 测试

```powershell
.\run_tests.ps1
```

或：

```powershell
python -m unittest discover -s tests
```

### Docker

构建：

```powershell
docker build -t clash-royale-agent .
```

运行后端：

```powershell
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
```

### 配置

主要环境变量写在 `.env.example` 里。

常用默认值：

```text
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
SILICONFLOW_MODEL_NAME=Qwen/Qwen3-8B
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
```

明确的本地 JSON 查询不需要真实 LLM key。开放型 RAG 问题需要配置 `SILICONFLOW_API_KEY` 和可用的 embedding 服务。

### 当前边界

这是一个本地 prototype，适合演示和面试讲解，不是生产级线上服务。

当前边界：

- 没有鉴权系统
- 没有线上托管部署
- 没有生产级监控
- 暂时没有完整 Docker Compose 编排
- RAG 依赖可选本地 embedding 和 LLM 配置
