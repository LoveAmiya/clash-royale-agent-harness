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
- Multi-intent decomposition with per-subquery execution and partial results
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

`/health` only confirms that the Python process is alive. Use `/ready` before
routing strict live-data traffic; it reports `ready`, `degraded`, or
`unavailable` based on the model credential, complete official snapshot, and RAG
index. `/snapshot/status` exposes the active sample, while `/metrics` exports
low-cardinality Prometheus-compatible runtime metrics.

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
- 雷电巨人的使用率、胜率，还有当前环境主流卡组。

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

This is the default quality gate. It runs the complete unit/integration suite,
then the static 348-case deterministic evaluation corpus. The corpus covers
English card metrics, Chinese aliases, multiple requested metrics, card
comparisons, rank lookups, deck rankings, schedule queries, out-of-domain
rejections, RAG routing, and multi-intent decomposition. Each invocation writes
a new JSON report under `evaluation/reports/`; failure rows are retained and the
script exits non-zero, so they cannot be hidden by a green unit-test run.

GitHub Actions runs this deterministic gate and a container liveness check for
pull requests and `main` pushes without external credentials. The separate
manual `Live API Smoke` workflow must run on a protected self-hosted runner whose
public IP is registered with Supercell; it can use repository secrets and upload
its JSON reports as artifacts.

To run only the Python tests:

```powershell
python -m unittest discover -s tests
```

To inspect or reproduce the deterministic evaluation report directly:

```powershell
python -m evaluation.run_eval
python -m evaluation.run_eval --report evaluation/reports/manual-evaluation.json
```

When the local embedding service is available, run the snapshot RAG retrieval
benchmark separately. It builds silver-label cases from the active official
snapshot's card, deck, archetype, pair, counter, and matchup evidence; it
refuses to label a BM25-only run as hybrid retrieval.

```powershell
python evaluation/retrieval_benchmark.py --report evaluation/reports/retrieval-benchmark.json
```

The optional live smoke test is a separate external-system gate. It requires a
running strict backend, a valid model API key, and a valid Supercell API token;
it verifies the model parser, official snapshot provenance, multi-intent
orchestration, RAG synthesis, SSE execution events, and final trace rather than
substituting repository fixtures.

```powershell
$env:RUN_LIVE_API_SMOKE = "true"
$env:LIVE_API_BACKEND_URL = "http://127.0.0.1:8095"
.\run_tests.ps1
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
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=https://crs.ruinique.com
OPENAI_WIRE_API=responses
OPENAI_REASONING_EFFORT=medium
PARSER_REASONING_EFFORT=medium
SYNTHESIS_REASONING_EFFORT=medium
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
OLLAMA_EMBED_TIMEOUT_SECONDS=10
PARSER_CALL_TIMEOUT_SECONDS=45
MODEL_CALL_TIMEOUT_SECONDS=120
MAX_REQUEST_BODY_BYTES=65536
MAX_QUERY_CHARS=8000
PROCESS_MAX_CONCURRENT=8
PROCESS_RATE_LIMIT_PER_MINUTE=30
ALLOWED_ORIGINS=http://127.0.0.1:8080,http://localhost:8080
ADMIN_API_KEY=
```

The default hosts are loopback-only. Set `RUNTIME_HOST=0.0.0.0` deliberately for
container or reverse-proxy deployment. A caller may send a bounded
`X-Request-ID`; the runtime returns it in HTTP headers, SSE execution/content
events, and the final Trace. The legacy live-sample settings endpoint remains
disabled by default and also requires `X-Admin-Key` when explicitly enabled.

Dependencies are maintained in `requirements.in` and compiled into
`requirements.lock.txt`. Docker and CI install the lock file; regenerate it with
`pip-compile --strip-extras --output-file requirements.lock.txt requirements.in`
when intentionally changing direct dependencies.

For local runs, set `OPENAI_API_KEY` in the current PowerShell session before starting the backend. Do not put a real key in source code or commit it to Git. The default runtime uses the configured OpenAI-compatible relay with the Responses API, `gpt-5.5`, and `medium` reasoning effort for parsing and final synthesis.

In strict production mode, card, deck, matchup, and environment answers all use the last complete official Supercell daily snapshot. The snapshot contains exactly 20,000 unique battle-log records, normalized raw battles, card/deck aggregates, and sampled deck matchups. Environment analysis retrieves documents generated from that same snapshot, then uses an LLM to synthesize a bounded answer with a snapshot ID, sample size, and collection time. The local Qdrant index is persistent and is reused after restart when the snapshot ID is unchanged.

The RAG corpus is not one raw document per battle. It derives high-information evidence documents for card profiles, exact deck profiles, heuristic archetypes, card-pair observations, observed card-versus-card counter evidence, and deck matchups. On startup and after a new snapshot is published, indexing is preheated in the background. Requests only use an activated retriever matching the active snapshot; the UI reports `ready`, `bm25_only`, `building`, `not_ready`, or `failed` rather than charging the first user request with embedding work.

### Daily Official Snapshot

Set `SUPERCELL_API_TOKEN` to enable the official Clash Royale API adapter. The production target is fixed at 20,000 unique battles, collected from rank 1 upward through paginated leaderboard results with a maximum candidate pool of 3,000 players. The collector stops as soon as it reaches the target. A partial, timed-out, or rate-limited collection is rejected; the last complete official snapshot remains available and is marked stale while a replacement is unavailable. The browser UI exposes this provenance, candidate pool, actual scanned rank range, sample size, collection time, and duplicate count without triggering a refresh. The model parser remains the first request step. In strict external mode, no repository fixture is represented as live data.

```text
SUPERCELL_API_TOKEN=your_official_token
SUPERCELL_LIVE_DATA_ENABLED=true
```

### Data Freshness and Sources

`official_daily_snapshot.json` is the canonical data source. After a complete collection it regenerates `top_decks.json`, `cards_meta.json`, and `rag_documents.json` atomically. The runtime loads only a complete snapshot and checks its age on every restart; it does not answer production data questions from the old repository fixtures.

`schedule.json` is intentionally separate and remains available for schedule-only
questions while the first game-data snapshot is collecting. In strict mode,
`cards_meta.json` is retained only as a parser catalog for card names and aliases;
it is never passed to card, deck, matchup, or RAG answer Skills. The snapshot
status endpoint exposes this split through `data_sources`.

This project intentionally does not scrape RoyaleAPI from an LLM prompt or call its retired public API: RoyaleAPI's own legacy documentation states that its [public API was sunset](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/getting_started.md), and its [legacy popular-decks endpoint is not implemented](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/endpoints/popular_decks.md). A future live-data adapter should use a maintained, documented and authorized provider with a deterministic ingestion job, not unrestricted model browsing.

### Workflow Overview

```text
User Question
  -> Query Parser (one intent or subqueries[])
  -> Per-subquery Skill Router
  -> Official Daily Snapshot JSON and/or Snapshot RAG Retrieval
  -> Deterministic section aggregation
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
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=https://crs.ruinique.com
OPENAI_WIRE_API=responses
OPENAI_REASONING_EFFORT=medium
PARSER_REASONING_EFFORT=medium
SYNTHESIS_REASONING_EFFORT=medium
OLLAMA_EMBED_URL=http://localhost:11434/api/embed
EMBED_MODEL=bge-m3:latest
OLLAMA_EMBED_TIMEOUT_SECONDS=10
PARSER_CALL_TIMEOUT_SECONDS=45
MODEL_CALL_TIMEOUT_SECONDS=120
```

本地运行前，请在当前 PowerShell 窗口设置 `OPENAI_API_KEY`，不要把真实 Key 写进源码或提交到 Git。默认运行时使用已配置的 OpenAI 兼容中转站、Responses API、`gpt-5.5`，解析与最终综合统一使用 `medium` 推理强度。

直接基于 JSON 的查询可以只依赖本地数据运行。环境分析和战队备战类问题会先从 RAG 语料检索证据，再由 LLM 生成受限综合回答，并在答案末尾附来源链接和数据时效边界。Ollama embedding 不可用时，检索会在短超时后自动降级为 BM25。浏览器会直接消费后端 SSE，显示处理中状态和最终执行 Trace。

### 数据时效与来源

`top_decks.json` 与 `cards_meta.json` 会在每条记录中保留原始 RoyaleAPI 页面 URL，但它们是仓库快照，不是本次回答时实时获取的数据。Agent 不会把这些快照说成“当前版本实时结论”或“对手真实情报”。

项目不会让 LLM 直接抓取 RoyaleAPI 页面，也不会调用已经停止维护的旧公开 API：RoyaleAPI 自己的旧版文档明确说明[公开 API 已停止服务](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/getting_started.md)，[旧版热门卡组接口也未实现](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/endpoints/popular_decks.md)。后续若接入实时数据，应选择仍在维护、文档完整且授权明确的数据提供方，并通过确定性的采集任务更新，而不是让模型无限制浏览网页。

### 工作流概览

```text
User Question
  -> Query Parser
  -> Skill Router
  -> Local JSON Answer or RAG Retrieval
  -> Evidence-grounded Model Synthesis
  -> Trace Harness
  -> API Response / Browser UI
```
