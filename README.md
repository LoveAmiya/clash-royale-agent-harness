# Clash Royale Agent Harness

[English](#english) | [中文](#中文)

---

## English

Clash Royale Agent Harness is a FastAPI-based data QA workflow for answering Clash Royale card, deck, matchup, and meta questions from official evidence.

The system uses LLM-first query parsing with deterministic validation and fallback, skill routing, atomically published SQLite-derived snapshot grounding, retrieval augmentation, traceable execution, and a browser chat interface. It is designed around a controlled domain workflow rather than a generic chatbot loop.

### Current Production Data Path

The active data path is a rolling, deduplicated Path of Legend fact store in
`data/corpus/corpus.sqlite`. Every production collection freezes the global top
1,000 and expands only through accepted Path of Legend battles until 200,000
unique battles are collected. The legacy mode name is `weekly_expanded`, but it
now runs every day. The API publishes thirty fixed scopes: `7d`, `7-14d`,
`14-21d`, `21-28d`, `28-35d`, and `35d`, each crossed with top
100/200/500/1000/all. The browser selects the scope explicitly, so the model
never routes the data source.

Each new battlelog is stored as both `base8` and, when official fields are
complete, `full_loadout` (tower, eight cards, evolutions, and elite slots). Raw
battles are not embedded. Structured statistics and high-density RAG evidence
are materialized locally and switched as one atomic snapshot group.

Use `GET /api/datasets` and `data/active_snapshot_group.json` as the rolling
publication authority. `GET /snapshot/status` is retained for the legacy
single-snapshot compatibility path.

### Highlights

- FastAPI backend
- Browser chat interface
- LLM-first structured query parsing with deterministic validation and fallback
- Multi-intent decomposition with per-subquery execution and partial results
- Skill registry and skill routing
- Grounded answers from thirty rolling Path of Legend dataset scopes
- Advanced RAG path for open-ended meta and deck analysis
- Traceable execution harness
- Local evaluation suite and unit tests
- Dockerfile and PowerShell helper scripts
- Snapshot-scoped RAG quality gates and grounded numeric/citation validation
- Model-provider circuit breaker and stream capability telemetry
- Request-bound feedback and reviewable continuous-evaluation candidates
- Split collector/API deployment with Caddy, Prometheus, Grafana, Loki, and Promtail
- Browser operations dashboard for snapshot lineage, RAG quality gates, model circuit state, quota, feedback, and Prometheus metrics

Verified locally on 2026-08-02:

- `764` unit/integration tests were discovered: `763` passed and `1` was skipped by design.
- `344/344` active deterministic evaluation cases passed; `4` optional RAG-route cases were skipped by design.
- `25/25` snapshot citation/grounding probes passed, with an invalid-citation rate of `0`.
- `28/28` deterministic fault-injection scenarios passed.
- A local live-model RAG smoke test passed through `llm_parser` -> `EvidenceSynthesisSkill` -> `rag_synthesis`; provider generation and numeric/citation grounding both passed.

### Repository Layout

```text
data/                    Private local snapshots plus the tracked safe alias catalog
evaluation/              Evaluation cases and metrics
harness/                 Skill execution and trace harness
planner/                 Lightweight planning layer
skills/                  Skill registry and domain skills
tests/                   Unit tests
app_config.py            Environment-driven configuration
answer_builder.py        Deterministic structured answer builder
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
.\run_tests.ps1
```

The public quality gate needs neither a real API key nor private snapshot data.
It temporarily uses the literal sentinel `test-key` to exercise configured-key
branches while external providers are disabled or mocked, then restores the
caller's environment. A public clone can run all deterministic tests and
container liveness checks; data-backed product features require a separately
generated or mounted authorized private data volume.

Create a local environment file only when optional providers are needed:

```powershell
Copy-Item .env.example .env
```

Keep `.env` out of version control.

### Run the Backend

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1
```

`run_api.ps1` starts a read-only API role. It follows only atomically published
artifacts and never contacts Supercell. Collection is a separate scheduled or
manual workflow documented in
[`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md).

Native Windows execution is the recommended production collection path. The
Supercell key must allow the host's current public egress IP. Use Docker for
build/CI or offline validation unless the container has a stable, allowlisted
public egress IP; Docker Desktop traffic may use a different and changing IP.

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
low-cardinality Prometheus-compatible runtime metrics. While a replacement is
refreshing or cooling down, the last complete snapshot remains active and
`/ready` reports `degraded` instead of claiming full readiness.

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

- 使用率第三的卡牌是什么？
- 比较火球和毒药的胜率。
- 现在热门卡组有哪些？
- 雷电巨人的使用率、胜率，还有当前环境主流卡组。

The end-to-end restart and acceptance checklist is maintained in
[`00_START_HERE.md`](00_START_HERE.md). It covers readiness and snapshot/RAG
alignment, Chinese and English aliases, multi-intent ordering, raw SSE events,
model stream fallback, feedback, retrieval evaluation, two-instance Redis load
tests, and the Prometheus-to-Alertmanager notification drill.

### API Usage

All-card structured rankings are available without model calls:

```text
GET /api/cards/rankings?dataset_scope=7d_all&sort_by=usage_rate
```

`sort_by` accepts `usage_rate`, `clean_win_rate`, or `rating`.

Complete-loadout entity rankings include only card forms and tower troops that
were actually observed in the selected scope:

```text
GET /api/entities/rankings?dataset_scope=7d_all&sort_by=usage_rate
GET /api/entities/catalog?dataset_scope=7d_all
GET /api/entities/card%3A26000000%3Aevolution/stats?dataset_scope=7d_all
POST /api/entities/compare
```

Entity IDs are stable: `card:{official_card_id}:ordinary`,
`card:{official_card_id}:evolution`, `card:{official_card_id}:elite`, and
`tower:{official_tower_id}`. A scope without complete-loadout entity statistics
returns `ENTITY_STATS_NOT_READY`; it never falls back to base-eight statistics.

```powershell
curl -X POST http://127.0.0.1:8091/process `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"当前环境以哪些体系为主？\"}"
```

### Tests

```powershell
.\run_tests.ps1
```

This is the default credential-free quality gate. It runs the complete unit/integration suite,
then the static 348-case deterministic evaluation corpus. The corpus covers
English card metrics, Chinese aliases, multiple requested metrics, card
comparisons, rank lookups, deck rankings, schedule queries, out-of-domain
rejections, RAG routing, and multi-intent decomposition. Each invocation writes
a new JSON report under `evaluation/reports/`; failure rows are retained and the
script exits non-zero, so they cannot be hidden by a green unit-test run.

Neither the test runner nor GitHub Actions reads a private snapshot or makes a
real model/Supercell request. GitHub Actions runs this deterministic gate and a container liveness check for
pull requests and `main` pushes without external credentials. The separate
manual `Live API Smoke` workflow must run on a protected self-hosted runner whose
public IP is registered with Supercell; it can use repository secrets and upload
its JSON reports as artifacts.

### Private runtime data

The repository publishes application code, tests, documentation, and safe
configuration templates only. Battle facts, player identifiers, collection
checkpoints, SQLite databases, derived statistics, RAG documents, Qdrant indexes,
logs, status files, and exports remain local under ignored paths. A public clone
therefore needs a separately provisioned private data volume before data-backed
features can serve results. The reviewed Chinese card-name/alias configuration is
the only tracked file under `data/`; it contains no battles, player identifiers,
credentials, or aggregate performance data.

Git history must not be used as a snapshot transport. Share an approved local
export out of band when analysis access is intentional; never commit it or bake
it into a container image.

To run only the Python tests:

```powershell
python -m unittest discover -s tests
```

The unit suite and deterministic contract corpus use anonymous values generated
from the reviewed card-name catalog. They do not read a private snapshot. Run
the public quality gate plus synthetic fault injection with:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

To inspect or reproduce the anonymous parser/routing contract report directly:

```powershell
python -m evaluation.run_eval
python -m evaluation.run_eval --report evaluation/reports/manual-evaluation.json
```

When the local embedding service is available, run the snapshot RAG retrieval
benchmark separately. It builds silver-label cases from the active official
snapshot's card, deck, archetype, pair, counter, and matchup evidence; it
refuses to label a BM25-only run as hybrid retrieval.

```powershell
python -m evaluation.retrieval_benchmark --report evaluation/reports/retrieval-benchmark.json
```

Every deterministic, retrieval, citation, fault-injection, and live-parser
report now includes a `scorecard` node. To compare several existing reports as
one regression unit without rerunning a model or benchmark, use the offline
aggregator:

```powershell
python -m evaluation.scorecard `
  --report evaluation/reports/manual-evaluation.json `
  --report evaluation/reports/retrieval-benchmark.json `
  --report evaluation/reports/citation-latest.json `
  --report evaluation/reports/fault-injection-latest.json `
  --dataset-scope 7d_all `
  --deck-mode base8 `
  --entity-mode base8 `
  --output evaluation/reports/unified-scorecard.json
```

The unified output covers retrieval recall, assertion support, citation
precision, refusal accuracy, boundary violations, latency, tokens, and cost.
It records `snapshot_group_id`, `snapshot_id`, `dataset_scope`, `deck_mode`,
`entity_mode`, `model`, and `prompt_hash`; unavailable measurements are exposed
through `metric_coverage` instead of being confused with observed zero values.
The aggregator reads only local JSON reports and never sends questions or
player tags to a provider.

The optional live smoke test is a separate external-system gate. It requires a
running strict backend, a valid model API key, and a valid Supercell API token;
it sends a structured ranking question, a mixed multi-intent question, and a
pure open RAG question. It verifies the model parser, official snapshot
provenance, multi-intent orchestration, RAG synthesis, grounding validation,
SSE execution events, and final trace rather than substituting repository
fixtures. With `--report`, each completed stage is persisted immediately so a
later external failure does not erase earlier evidence.

```powershell
$env:RUN_LIVE_API_SMOKE = "true"
$env:LIVE_API_BACKEND_URL = "http://127.0.0.1:8091"
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

### Docker

```powershell
docker build -t clash-royale-agent .
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
```

The container health check validates packaging, not Supercell IP authorization.
Do not expect daily refreshes from Docker unless its actual public egress IP is
stable and included in the Supercell key allowlist.

### Configuration

The main configuration keys are documented in `.env.example`.

Common values:

```text
OPENAI_API_KEY=your_key_here
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
OPENAI_MODEL=gpt-5.5
OPENAI_REVIEW_MODEL=gpt-5.5
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
SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS=28800
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

### Quality and Operations

RAG activation is snapshot-scoped. A candidate index must pass document-count,
source-coverage, uniqueness, and deterministic multi-probe Recall@5 checks for
each source type before it can replace the active retriever. Generated RAG
answers validate cited document IDs and bind unit-bearing numeric claims to the
matching metric and known card entity in retrieved evidence. Reports include
overall and per-source recall under `data/rag_quality/` and are not committed.
The probe query is generated from user-visible card, deck, matchup, and archetype
slots; it never contains the target document ID or copies the target document
body. The quality report records the exact evidence fingerprint.

Every published snapshot validates the complete derived corpus before writing
the canonical file. All `cards_meta` rows and all `top_decks` rows must have a
matching RAG document with identical metrics. Decks must contain eight distinct
cards. Matchups with at least five sampled games are all included; lower-sample
matchups remain auditable in the canonical snapshot but are not promoted as RAG
evidence. Aggregate pair/counter evidence requires at least 20 games. `None%`,
null metrics, duplicate IDs, missing provenance, and any structured/RAG mismatch
block publication.

`/model/status` reports sanitized provider capabilities, passive live-call
detection time, and circuit state. No synthetic startup call consumes model
quota. `/metrics` exports provider calls, circuit state, stream-mode counts, and
per-mode first-content/total latency for real-stream, chunked fallback, and
unavailable results. The default circuit opens after three consecutive provider
failures and permits one half-open probe after 60 seconds.

Completed answers can receive feedback through `POST /feedback` using only the
server-issued `request_id`. The browser exposes helpful/needs-improvement
controls. Corrections remain review candidates in SQLite and never silently
modify the deterministic corpus. Export candidates with:

```powershell
python -m evaluation.export_feedback_cases
```

Reviewers explicitly mark stable candidates `review_status: approved` and run
`python -m evaluation.run_eval --feedback-cases evaluation/feedback_candidates.jsonl`.
Re-exporting candidates preserves existing review decisions, notes, and manually
curated assertions instead of resetting them to pending.

The production Compose topology runs one Supercell collector and two read-only
API processes. Caddy load-balances the APIs and terminates local HTTPS;
Prometheus/Grafana persist metrics and Loki/Promtail centralize structured JSON
logs. Both API instances share an atomic Redis rate/concurrency quota with
expiring leases, and raw ASGI body bytes are bounded even for chunked requests.
Prometheus routes alerts through Alertmanager to a persistent, rotating webhook
receiver. Grafana provisions an operations dashboard at `http://127.0.0.1:3000`;
Prometheus retains 30 days, Loki retains 7 days, and symptom alerts cover request
failure rate, snapshot/RAG misalignment, and an open model circuit. Set a strong
`GRAFANA_ADMIN_PASSWORD` before deployment. Validate and launch it with:

```powershell
docker compose -f compose.production.yml config --quiet
docker compose -f compose.production.yml up --build -d
```

Reproducible two-instance k6 smoke/load/soak profiles and the complete
Prometheus-to-Alertmanager delivery drill are documented in
[`docs/production-demo.md`](docs/production-demo.md). Run them with
`run_load_test.ps1` and `test_alert_pipeline.ps1`; timestamped reports are kept
even when a threshold or delivery check fails.

Only run the containerized collector when its stable public egress IP is in the
Supercell key allowlist. On this project's Windows setup, native collection plus
containerized read-only API services remains the safer topology.

For local runs, set `OPENAI_API_KEY` in the Windows user or current process environment before starting the backend. Do not put a real key in source code or commit it to Git. The runtime uses `https://crs.ruinique.com`, the Responses API, `gpt-5.5`, and `medium` reasoning for parsing, review, and final synthesis; it does not use the official OpenAI base URL.

In strict production mode, card, deck, matchup, and environment answers use the
explicitly selected rolling `dataset_scope`. Every response carries the snapshot
group, scope, time window, unique battle count, batch composition, and matched
sample count. Environment analysis retrieves only documents from that same
scope, then uses the configured model to synthesize a bounded answer.

The RAG corpus is not one raw document per battle. It derives high-information
evidence documents for card profiles, exact deck profiles, heuristic archetypes,
card pairs, observed counters, deck matchups, observed card/tower entities, and
precomputed `meta_delta` changes. Thirty scopes are built sequentially: current
0-7 days, four historical seven-day slices, and cumulative 0-35 days, each at
top 100/200/500/1000/all levels. A new structured snapshot group and its
retrievers switch only after every publishable scope passes document validation,
fingerprint alignment, and retrieval probes. Empty historical slices remain
explicitly not ready and do not contaminate current ranges. A failed candidate
keeps the previous group active.

The browser sends the selected `dataset_scope` plus `entity_mode=base8` or
`loadout_entity` to every relevant page. Explicit evolved, elite, or tower
questions force entity evidence inside that same scope; the model never selects
or silently changes the data range. Older ten-scope groups remain readable
during migration, while entity statistics and new historical slices stay
disabled until a new group is materialized.

Complete-loadout availability has two separate gates. `complete_loadout_ready`
means legal tower/card/special-mode loadouts exist in the selected scope;
`entity_stats_ready` means the active publication has materialized the unified
ordinary/evolution/elite/tower entity table. The full-loadout UI requires both.
An older publication may report the first as true and the second as false; this
is a migration state, not proof that raw loadout data is missing, and it must
never fall back to base-eight statistics under an entity label.

Open RAG answers use one deterministic reference section. Structured snapshot
sources are numbered first and retrieved document references continue from that
number; any model-written source list is suppressed before the verified list is
appended. Numeric facts and document IDs are validated before the answer is
accepted. The model is instructed to copy numeric precision exactly. If only
some generated numeric sentences fail grounding, local validation removes those
sentences, returns the remaining validated analysis with a boundary notice, and
does not make a retry model call. A final validation failure returns a grounded
refusal with verified references instead of a generic generation error.

### Rolling Path of Legend Collection

Set `SUPERCELL_API_TOKEN` only for the separate collection workflow. Every daily
production run uses the legacy-named `weekly_expanded` mode. It starts from the
frozen global Path of Legend top 1,000 and expands only through opponent tags
from accepted `pathOfLegend` battles until the batch contains exactly 200,000
unique battles. The production mode uses concurrency `1`, defaults to at most
one request per second, and rejects any rate-limited, conflicted, or scope-invalid batch.

The collector writes checkpoints under `data/rolling_work/<batch_id>` and imports
accepted facts into `data/corpus/corpus.sqlite`. Facts are globally deduplicated;
batch observations retain collection time, source, and ranking provenance.
Observations expire after a real 35-day window. Accepted expanded batches have
no count cap inside that window. `daily_ranked` remains a compatibility-only
implementation and is not selected by the scheduler or normal operations.

Run collection only through `run_rolling_collection.ps1`. The script performs
the Supercell IP/key preflight before making the batch request. Complete commands,
monitoring rules, quality gates, and recovery boundaries are in
[`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md).

### Data Freshness and Sources

`data/corpus/corpus.sqlite` is the long-term source of truth. Business requests do
not query it directly; they use the current atomically published snapshot group.
`official_daily_snapshot.json`, `top_decks.json`, `cards_meta.json`, and the old
Qdrant directory are ignored local migration artifacts. Runtime startup and the
public test suite do not read them; the active snapshot group is the publication
authority.

Run the anonymous deterministic corpus and fault-injection gate with
`run_tests.ps1`. Snapshot-derived retrieval, citation, and live-model checks are
private local gates because they require the unpublished snapshot. Dense
retrieval needs Ollama and is run separately:

```powershell
python -m evaluation.retrieval_benchmark `
  --report evaluation/reports/retrieval-latest.json
```

Clan-war schedule and preparation capabilities are not registered in the
product. Card-name normalization uses only `data/card_aliases.zh-CN.json`, which
contains terminology but no battles, players, or performance metrics. Card,
deck, matchup, and RAG answers use the selected private snapshot group. The
snapshot status endpoint exposes this split through `data_sources`.

This project intentionally does not scrape RoyaleAPI from an LLM prompt or call its retired public API: RoyaleAPI's own legacy documentation states that its [public API was sunset](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/getting_started.md), and its [legacy popular-decks endpoint is not implemented](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/endpoints/popular_decks.md). A future live-data adapter should use a maintained, documented and authorized provider with a deterministic ingestion job, not unrestricted model browsing.

### Workflow Overview

```text
User Question
  -> Query Parser (one intent or subqueries[])
  -> Per-subquery Skill Router
  -> Selected Rolling Structured Scope and/or Scope-filtered RAG Retrieval
  -> Deterministic section aggregation
  -> Trace Harness
  -> API Response / Browser UI
```

---

## 中文

Clash Royale Agent Harness 是一个基于 FastAPI 的《皇室战争》官方数据问答工作流，用于回答单卡、卡组、对局和环境分析问题。

系统结合了大模型优先的结构化解析、确定性校验与降级、Skill 路由、SQLite 派生快照 grounding、检索增强、可追踪执行链路和浏览器界面。项目重点是受控领域工作流，而不是泛化聊天机器人。

### 当前生产数据路径

当前业务数据来自 `data/corpus/corpus.sqlite` 中严格限定传奇之路的滚动事实库。每天冻结全球
前 1000 名，并只沿已验收的传奇之路对局扩散，直到批内得到 20 万场唯一对局。生产模式仍
沿用历史名称 `weekly_expanded`，但已经改为每天执行，不再安排 `daily_ranked`。后端原子发布
当前及历史 7 天分段、累计 35 天与前 100/200/500/1000/全量组合成的三十个固定范围，前端
显式选择，模型不参与数据源路由。

同一次 battlelog 同时产生 `base8` 和可用时的 `full_loadout`（塔楼、八卡、觉醒、精英）。
单场对局不进入向量库；本地只物化结构化统计和高密度 RAG 聚合证据。

滚动发布状态以 `GET /api/datasets` 和 `data/active_snapshot_group.json` 为准。
`GET /snapshot/status` 是保留的旧单快照兼容接口。

### 项目亮点

- FastAPI 后端
- 浏览器多视图界面：总览、自由问答、单卡、双卡、卡组画像、精确对阵和环境体系
- 大模型优先的结构化问题解析，并支持确定性校验与 fallback
- Skill 注册表和 Skill 路由
- 基于三十个滚动传奇之路范围的卡牌、卡组和对局事实回答
- 面向开放环境分析的高级 RAG 链路
- 可追踪执行 harness
- 本地评测集和单元测试
- Dockerfile 和 PowerShell 辅助脚本
- 浏览器系统面板，展示快照血缘、RAG 质量门槛、模型熔断、配额、反馈和 Prometheus 指标

2026-08-02 本机验收：共发现 `764` 项单元/集成测试，其中 `763` 项通过、`1` 项按设计跳过；确定性评测 `344/344` 个启用用例通过，另有 `4` 项可选 RAG 用例按设计跳过；`25/25` 快照引用/grounding 探针与 `28/28` 故障注入场景通过。本地真实模型 RAG 冒烟通过 LLM 解析、RAG 综合和数值/引用 grounding 校验。

### 项目结构

```text
data/                    私有本地快照及可公开的安全别名配置
evaluation/              评测用例和指标
harness/                 Skill 执行与 trace harness
planner/                 轻量规划层
skills/                  Skill 注册表和领域技能
tests/                   单元测试
app_config.py            环境变量驱动配置
answer_builder.py        确定性结构化答案构建
query_parser.py          自然语言问题解析
query_answering.py       直接查询与 RAG 路由
runtime_multi.py         FastAPI 后端入口
web_app.py               Web 代理与服务入口
web_ui_template.py       多视图结构化查询与自由问答界面
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
.\run_tests.ps1
```

公开质量门不需要真实 API Key 或私有快照。脚本只会临时使用字面量 `test-key` 覆盖
“已配置凭证”分支，公开测试中的外部 provider 均被禁用或 mock，结束后恢复调用者环境。
因此公开克隆可以运行完整确定性门禁和容器存活检查；真实数据功能仍需单独生成或挂载
经过授权的私有数据目录。

只有在需要可选 provider 时，才需要创建本地环境变量文件：

```powershell
Copy-Item .env.example .env
```

不要把 `.env` 提交到版本库。

### 私有数据边界

远程仓库只发布前后端源码、测试、文档和无敏感信息的配置模板。原始对局、玩家 tag、采集
断点、SQLite、结构化统计、RAG 文档、Qdrant、日志、状态文件和导出全部留在本机，不进入
Git，也不打进 Docker 镜像。`data/` 下唯一允许跟踪的是人工审阅的中文卡牌名称与别名配置，
其中不含对局、玩家标识、凭据或表现统计。

因此，新克隆的公开代码仓库不会自带业务数据；运行数据功能前必须在本机单独采集，或挂载
经过授权的私有数据目录。Git 历史不是数据分发渠道。

### 启动后端

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1
```

`run_api.ps1` 启动只读业务角色，只跟随已原子发布的数据，不联系 Supercell。采集是独立的
计划任务或人工工作流，详见
[`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md)。

正式采集推荐直接在 Windows 本机运行，并确保 Supercell Key 白名单包含主机当前公网出口 IP。Docker Desktop 的容器出口 IP 可能与主机不同且会变化；除非已固定并加入白名单，否则 Docker 只用于构建、CI 和离线验证。

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

单卡、双卡、卡组画像和卡组对阵页面使用中文卡牌选择器，直接查询当前快照的本地结构化索引，不调用模型。环境体系页先展示确定性表格；只有用户点击“生成分析”时，才会沿用高级 RAG 链路调用模型综合当前快照证据。自由问答页继续保留自然语言解析、多意图拆分、执行记录和高级 RAG。

全站提供普通 8 卡与完整配置两种口径。完整配置不是只看原始载荷是否存在：所选范围必须
同时满足 `complete_loadout_ready=true` 和 `entity_stats_ready=true`。前者表示已有合法塔楼、
八卡及觉醒/精英载荷，后者表示当前活动快照组已经物化普通/觉醒/精英卡牌实体和塔楼统计。
旧活动组可能前者为真、后者为假，此时前端按设计置灰完整配置，等待下一代快照组发布，
不会把普通 8 卡结果冒充为完整配置结果。

示例问题：

- 使用率第三的卡牌是什么？
- 比较火球和毒药的胜率。
- 现在热门卡组有哪些？

### API 调用

全卡结构化排名不调用模型：

```text
GET /api/cards/rankings?dataset_scope=7d_all&sort_by=usage_rate
```

`sort_by` 可选 `usage_rate`、`clean_win_rate` 或 `rating`。

```powershell
curl -X POST http://127.0.0.1:8091/process `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"当前环境以哪些体系为主？\"}"
```

### 测试

```powershell
.\run_tests.ps1
```

该公开门禁与 GitHub Actions 一致：不读取私有 SQLite/活动快照，不使用真实模型或
Supercell 凭证，也不发起真实 provider 请求。

或：

```powershell
python -m unittest discover -s tests
```

### Docker

```powershell
docker build -t clash-royale-agent .
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
```

容器健康检查只证明镜像能启动，不证明 Supercell IP 授权可用。

### 配置

主要配置项在 `.env.example` 中说明。

常用值：

```text
RUNTIME_PORT=8091
WEB_PORT=8080
BACKEND_URL=http://127.0.0.1:8091/process
OPENAI_MODEL=gpt-5.5
OPENAI_REVIEW_MODEL=gpt-5.5
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

本地运行前，请在 Windows 用户级或当前 PowerShell 环境设置 `OPENAI_API_KEY`。项目固定使用 `https://crs.ruinique.com`、Responses API、`gpt-5.5` 和 `medium`；不要把真实 Key 或个人目录写进源码、文档或 Git。

直接结构化查询只依赖当前官方快照的本地 SQLite 索引。自由问答中的结构化问题通常调用一次模型解析，随后由本地查询生成答案；开放式环境分析通常再调用一次模型完成证据综合。综合后的数字和引用由本地质量门逐句校验，模型必须原样引用证据精度。未受支持的数值句会被省略，其余已验证内容继续返回并标注边界，不会为修复再次调用模型；最终校验失败时返回带验证来源的安全拒答，而不是通用“生成回答失败”。战队赛赛程与战队备战请求会返回已移除边界，不进入模型。Ollama embedding 不可用时，检索会在短超时后自动降级为 BM25。浏览器会直接消费后端 SSE，显示处理中状态和最终执行 Trace。

### 数据时效与来源

`data/corpus/corpus.sqlite` 是长期事实来源，但业务请求只读取当前原子发布的派生快照组。
`official_daily_snapshot.json`、`top_decks.json`、`cards_meta.json` 和旧向量目录是迁移兼容
资产，不是滚动发布的权威状态。所有结论都必须标明所选范围、窗口、唯一对局数和实际命中
样本量，不得说成全球完整环境或实时对手情报。

项目不会让 LLM 直接抓取 RoyaleAPI 页面，也不会调用已经停止维护的旧公开 API：RoyaleAPI 自己的旧版文档明确说明[公开 API 已停止服务](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/getting_started.md)，[旧版热门卡组接口也未实现](https://github.com/RoyaleAPI/cr-api-docs/blob/master/docs/endpoints/popular_decks.md)。后续若接入实时数据，应选择仍在维护、文档完整且授权明确的数据提供方，并通过确定性的采集任务更新，而不是让模型无限制浏览网页。

### 工作流概览

```text
User Question
  -> Query Parser
  -> Skill Router
  -> Selected Rolling Structured Scope or Scope-filtered RAG Retrieval
  -> Evidence-grounded Model Synthesis
  -> Trace Harness
  -> API Response / Browser UI
```

### 滚动传奇之路语料

启动与验收见 [`00_START_HERE.md`](00_START_HERE.md)。独立采集任务必须阅读
[`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md)，可直接使用
[`docs/SNAPSHOT_COLLECTION_PROMPT.md`](docs/SNAPSHOT_COLLECTION_PROMPT.md) 创建新的采集任务。
架构细节见
[`docs/plans/rolling-path-of-legend-corpus.md`](docs/plans/rolling-path-of-legend-corpus.md)。
