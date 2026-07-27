# 皇室战争 Agent 启动手册

## Daily Official Snapshot Mode (Current)

Production data answers use one complete official Supercell snapshot per day.
The target is fixed at 20,000 unique battle-log records. Collection starts at
global leaderboard rank 1, follows ranking cursors in order through a candidate
pool of up to 3,000 players, and stops as soon as the target is reached. A
collection that times out, is rate limited, or returns fewer than 20,000 battles
is discarded and never replaces the last published snapshot.

The web UI has a persistent Current Data Snapshot panel. It shows the official
source, current snapshot status, usable-battle count, collection timestamp,
candidate leaderboard range, actual rank range scanned, usable player count,
and deduplicated battle-log records. Loading this panel only reads the published
snapshot; it never starts an API refresh.

RAG is preheated in the background after a complete snapshot is restored or
published. User requests never build embeddings. The panel also shows the RAG
state: `ready` (dense plus BM25), `bm25_only` (same-snapshot lexical fallback),
`building`, `not_ready`, or `failed`. The RAG corpus is derived from the 20,000
raw battles as card/deck profiles, heuristic archetypes, card pairs, observed
counter evidence, and deck matchups. Raw battles are retained for aggregation
and audit, not embedded one document per battle. Preheating sends these derived
documents to Ollama in bounded batches (`EMBED_BATCH_SIZE`, default `32`) and
writes matching batches to Qdrant.

发布前会逐条校验全部卡牌和全部 `top_decks` 卡组切片：数值必须与结构化快照一致，完整卡组必须恰好八张不同卡，任何 `None%`、null、重复文档 ID 或来源缺失都会阻止发布。对局 matchup 只有样本数至少 5 场才进入 RAG，但低样本明细仍保留在 `official_daily_snapshot.json` 供审计。卡牌搭配与克制聚合至少需要 20 场。

索引复用同时检查 `snapshot_id` 与 `docs_fingerprint`。新快照、RAG 文档和新索引通过验证后才一起切换；失败时继续服务上一份完整快照和索引。`/snapshot/status` 与 `/ready` 会显示快照文档指纹、active RAG 指纹、索引指纹及是否一致。

After a successful collection the backend atomically updates these files:

- `data/official_daily_snapshot.json`: canonical raw/aggregated official data.
- `data/cards_meta.json` and `data/top_decks.json`: structured query datasets.
- `card_deck_stats` inside `data/official_daily_snapshot.json`: per-card top exact deck variants, derived from all 20,000 raw battles. Queries such as "Electro Giant decks" use this index instead of searching only the global top-30 decks.
- `data/rag_documents.json`: RAG evidence generated from the same snapshot.
- `data/daily_snapshot_qdrant/`: persistent local vector index keyed by `snapshot_id`.

`schedule.json` remains a separately maintained local schedule source. In strict
mode, `cards_meta.json`, `top_decks.json`, and `rag_documents.json` are derived
compatibility artifacts only: card/deck/RAG answer Skills receive data only from
the complete `official_daily_snapshot.json`. `cards_meta.json` may still be read
as a parser-only card-name and alias catalog before the first snapshot exists.

On restart, the backend loads the last complete snapshot immediately. It only
collects and vectorizes again when that snapshot is at least 24 hours old. A
cold start with no published snapshot must finish the official collection before
answering data or RAG questions. The refresh has a 60-minute default maximum runtime;
it normally stops earlier once 20,000 unique records have been collected. Configure only the
credentials and port, then start normally:

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
$env:SUPERCELL_LIVE_DATA_ENABLED = "true"
$env:EXTERNAL_API_REQUIRED = "true"
$env:RUNTIME_PORT = "8095"
powershell -ExecutionPolicy Bypass -File .\run_backend.ps1
```

## 项目做什么

这是面向战队赛统筹场景的 Skill-based Agent。它先把自然语言问题解析为意图和槽位，再路由到赛程、卡牌、卡组、对比、备战或 RAG Skill。

确定性问题优先从本地 JSON 数据回答；开放式环境分析和备战问题会先使用 RAG 检索，再由模型基于证据综合回答，避免把所有问题都交给模型猜测。

## 先测试

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

### Test Quality Gate

`run_tests.ps1` first runs every unittest, including snapshot lifecycle, alias
parsing, multi-intent routing, deterministic Skills, RAG evidence/preheat, and
SSE streaming contracts. It then runs the static 348-case evaluation corpus.
The corpus checks English card metrics, Chinese aliases, comparisons, card rank
lookups, deck rankings, schedule queries, safe out-of-domain rejection, optional
RAG routing, and multi-intent decomposition. Every execution writes a new
timestamped JSON report in `evaluation/reports/`; failed rows retain the parsed
payload, selected Skill, answer, and per-assertion errors. A failed evaluation
returns a non-zero exit code, so do not delete a report to make a run appear
healthy.

Run only the deterministic evaluation or choose an explicit report name:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_eval
.\.venv\Scripts\python.exe -m evaluation.run_eval --report evaluation\reports\manual-evaluation.json
```

When Ollama embeddings are available, the snapshot retrieval benchmark can be
run separately. It evaluates 97 current-snapshot evidence queries across card,
deck, archetype, pair, counter, and matchup documents and refuses to call a
BM25-only result hybrid retrieval.

```powershell
.\.venv\Scripts\python.exe -m evaluation.retrieval_benchmark --report evaluation\reports\retrieval-benchmark.json
```

The default suite performs no external API calls. To add the real complete-chain
smoke test after a strict backend is healthy, use the live API smoke-test command
below. It verifies model parsing, official Supercell snapshot
provenance, multi-intent execution, RAG model synthesis, SSE, and Trace data.
The smoke test sends three requests: a structured card ranking, a mixed
multi-intent question, and a pure open RAG question. With `--report`, completed
stages are written immediately so an upstream failure remains auditable.

## 启动后端

打开第一个 PowerShell：

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
$env:SUPERCELL_API_TOKEN = [Environment]::GetEnvironmentVariable("SUPERCELL_API_TOKEN", "User")
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) { throw "用户环境变量 OPENAI_API_KEY 未配置" }
if ([string]::IsNullOrWhiteSpace($env:SUPERCELL_API_TOKEN)) { throw "用户环境变量 SUPERCELL_API_TOKEN 未配置" }
$env:RUNTIME_HOST = "127.0.0.1"
$env:RUNTIME_PORT = "8091"
$env:SUPERCELL_LIVE_DATA_ENABLED = "true"
$env:EXTERNAL_API_REQUIRED = "true"
$env:SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS = "3600"
powershell -ExecutionPolicy Bypass -File .\run_backend.ps1
```

Supercell Key 必须允许这台 Windows 主机的当前公网出口 IP。本项目推荐在 Windows 本机运行正式采集；Docker 只有在容器出口 IP 固定且已加入 Key 白名单时才适合刷新官方快照。Docker Desktop 的出口 IP 可能与主机浏览器看到的公网 IP 不同，也可能变化。

后端地址：`http://127.0.0.1:8091`

健康检查：`http://127.0.0.1:8091/health`

在另一个 PowerShell 中验证后端：

```powershell
Invoke-RestMethod http://127.0.0.1:8091/health
```

生产探针区分存活与可回答状态：`/health` 只检查进程；`/ready` 会检查模型凭证、完整官方快照和 RAG 状态。严格模式首次采集期间 `/ready` 返回 `503`；已有完整旧快照时，刷新中、冷却中、RAG 预热或快照陈旧均返回 `200` 与 `status: degraded`。`/metrics` 提供 Prometheus 文本指标，`/snapshot/status` 还会显示最近刷新尝试、冷却剩余时间、请求数、失败数、限流数与 P95 回答耗时。

默认仅监听 `127.0.0.1`。容器或反向代理部署才显式设置 `RUNTIME_HOST=0.0.0.0`；同时设置 `ALLOWED_ORIGINS`、请求长度/并发/每分钟限流环境变量。`/settings/live-sample` 默认关闭，若显式启用还必须提供 `X-Admin-Key`。

## 启动前端可视化

打开第二个 PowerShell：

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
powershell -ExecutionPolicy Bypass -File .\run_web.ps1
```

浏览器打开：`http://127.0.0.1:8080`

建议演示问题：

```text
我们第五轮打谁？
使用率第三的卡牌是什么？
现在热门卡组有哪些？
帮我根据下一轮对手做备战建议。
```

前端会用 SSE 实时显示默认展开的“执行说明”：已验证的解析结论、实际 Skill 路由、官方快照、RAG 检索和模型生成状态。它不展示模型私有思维链。结构化结果按标题、指标、数据边界和来源分块输出；RAG 回答会转发模型的公开文本增量。上游不支持 token 流时，执行说明会标记“模型未提供 token 流”，并以完成后结果分段输出；Trace 的 `metadata.model_stream` 会明确标为 `streaming`、`fallback_chunked` 或 `unavailable`。面试讲解链路：`Query Parser -> Router -> Official Snapshot 或 RAG -> Model Synthesis -> Execution Events + Trace -> SSE UI`。

## 模型何时调用

赛程、固定排名、单卡胜率等高置信度问题会直接读取本地 JSON，不会消耗模型调用。开放式环境分析和备战问题强制进入 RAG，再调用 OpenAI 模型综合证据；Ollama embedding 不可用时会在 10 秒后自动降级为 BM25。解析器和模型调用分别有 45 秒、120 秒上限，超时会返回明确错误。模型 Key 只从当前进程的 `OPENAI_API_KEY` 环境变量读取。

## 失败先查

```text
1. 后端是否已经先于前端启动。
2. 8091 和 8080 是否被占用。
3. 终端是否提示缺少依赖。
4. 本地 JSON 查询不应依赖真实 LLM Key。
5. Trace 中的 `hybrid` 表示向量和 BM25 都可用，`bm25_only` 表示 Ollama 不可用但已自动降级。
6. 检查 `OPENAI_MODEL`、网络和模型调用超时配置，开放问题不会无限等待。
```

## 模型连接检查

开放式问题会调用模型。`OPENAI_API_KEY`、`OPENAI_MODEL` 与 `OPENAI_BASE_URL` 必须来自同一个 OpenAI 兼容服务商；没有配置 `OPENAI_BASE_URL` 时，程序会使用官方 OpenAI 地址。

启动前可以只检查变量是否存在，不要在终端打印 Key：

```powershell
"OPENAI_API_KEY 已配置：$(-not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY))"
"OPENAI_MODEL 已配置：$(-not [string]::IsNullOrWhiteSpace($env:OPENAI_MODEL))"
"OPENAI_BASE_URL 已配置：$(-not [string]::IsNullOrWhiteSpace($env:OPENAI_BASE_URL))"
```

正式介绍见：`README.md`

## 本项目模型配置

本项目使用 Codex 已配置的 OpenAI 兼容中转站：`https://crs.ruinique.com`。启动脚本会设置 `OPENAI_WIRE_API=responses`、`OPENAI_MODEL=gpt-5.5`，并让解析与 RAG 最终综合统一使用 `medium` 推理强度，以控制响应时间。真实凭证仍然只读取 `OPENAI_API_KEY` 环境变量，不写入项目文件。

## 严格实时 API 模式

默认后端启动脚本会启用 `EXTERNAL_API_REQUIRED=true`。这个模式用于真实环境，行为如下：

- 每个问题先调用模型 API 做解析；模型未返回可验证结果时直接说明失败，不把本地规则伪装成模型解析。
- 卡牌指标和卡组样本只来自每日一次的 20,000 场完整 Supercell 官方战斗日志快照。采集从全球排行榜第 1 名开始，按官方分页顺序扫描，候选池最多前 3000 名；达到 20,000 条唯一可用对局立即停止。Trace 和页面快照面板会展示实际场次、候选池、实际扫描排名、有效玩家、失败数和重复记录。未完成的采集绝不发布，也不读取 `cards_meta.json` 或 `top_decks.json` 充当实时结果。
- 复合问题会分成最多四个子任务。例如“雷电巨人的使用率、胜率，还有当前环境主流卡组”会分别执行 `CardMetaSkill` 与 `EvidenceSynthesisSkill`；前者保留精确结构化数值，后者进行检索后调用模型 API 生成回答。
- RAG 的检索文档仍是仓库中的知识库；`model_generation=api` 表示最终环境分析确实由模型 API 生成。`bm25_only` 仅表示本地向量 embedding 服务未运行，检索已回退到 BM25，不影响模型 API 或 Supercell API 的调用。

先在当前 PowerShell 设置凭证和端口。不要在终端、截图或聊天中打印任何 Token：

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
$env:SUPERCELL_API_TOKEN = [Environment]::GetEnvironmentVariable("SUPERCELL_API_TOKEN", "User")
$env:SUPERCELL_LIVE_DATA_ENABLED = "true"
$env:EXTERNAL_API_REQUIRED = "true"
$env:SUPERCELL_LEADERBOARD_PLAYERS = "3000"  # 候选池上限，按第 1 名起顺序分页
$env:SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS = "3600"
$env:RUNTIME_PORT = "8091"
powershell -ExecutionPolicy Bypass -File .\run_backend.ps1
```

网页不会提供采样档位切换。页面顶部的“Current Data Snapshot”面板只读展示当前已发布快照的来源、目标、排行榜候选池、实际扫描范围、采集时间、有效玩家和去重数量；打开页面不会触发新的官方 API 请求。

受控采集默认每秒最多 2 个官方请求、严格按排行榜顺序逐名读取 battle log、遵守 `429` 响应的 `Retry-After`，刷新预算默认 60 分钟。`SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS` 可在 60 秒到 7200 秒之间调整；生产建议保持 3600，网络较慢时可设为 7200。未达到 20,000 场的结果会被丢弃并进入 5/15/30 分钟递增冷却，期间继续服务上一次完整快照，不会立即重复抓取。

## 质量、反馈与生产拓扑

生产演示分支已经把两个 API 实例的进程内限流升级为 Redis 原子共享配额，并为并发槽使用带 TTL 的租约；Redis 不可用时默认 fail-closed。请求体上限会按 ASGI 实际接收字节计算，因此没有 `Content-Length` 的 chunked 请求同样会在进入业务逻辑前返回 413。

压测与告警验收命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_load_test.ps1 -Profile smoke
powershell -ExecutionPolicy Bypass -File .\run_load_test.ps1 -Profile load
powershell -ExecutionPolicy Bypass -File .\run_load_test.ps1 -Profile soak -SoakDuration 30m
powershell -ExecutionPolicy Bypass -File .\test_alert_pipeline.ps1
```

k6 会经过 Caddy 请求两个真实 API 实例并共享同一个 Redis；测试关闭外部模型与 Supercell 调用，不消耗额度。告警演练覆盖 `Prometheus -> Alertmanager -> 持久化 webhook`，成功和失败都会保留 JSON 报告。完整配置、实测指标和边界见 `docs/production-demo.md`。

每次 RAG 预热会先执行同一 `snapshot_id` 的离线质量门槛：文档数量、证据类型覆盖、文档 ID 唯一性，以及每种证据默认均匀抽取 3 条的 Recall@5。`RAG_PROBES_PER_SOURCE` 可在 1-20 间调整；报告包含总体和分证据类型召回率并写入 `data/rag_quality/`，严格模式下未通过时不会切换 active retriever。RAG 最终回答还会校验引用文档 ID，并把带单位数值绑定到指标和已知卡牌实体；证据中 Poison 的数值不能被错配给 Electro Giant，失败时不输出未经支撑的开放结论。

质量探针只使用卡名、卡组名、对局双方和体系名等用户可见槽位，不会把目标 `doc_id` 或原文复制进查询。完整离线验收还会执行从当前快照自动抽样的引用/数值一致性基准和 28 个故障注入场景；需要真实 dense 消融时先启动 Ollama，再设置 `$env:RUN_RAG_RETRIEVAL_BENCHMARK = "true"` 后运行 `.\run_tests.ps1`。

模型网关包含供应商熔断与能力探测。能力由真实模型调用返回的公开文本 delta 被动探测，不在启动时额外消耗一次 API；首次调用前为 `unknown`。查看 `GET /model/status` 的探测方式和最近观测时间；`GET /metrics` 包含模型调用结果、熔断状态、`streaming`/`fallback_chunked`/`unavailable` 分布，以及各模式的首内容延迟和总耗时。连续失败默认 3 次后熔断 60 秒，半开只允许一个探测请求。

前端每条完成回答提供“有帮助/需改进”。反馈只接受服务器已完成回答的 `request_id`，存入 `data/feedback.sqlite3`，不会自动改写正式评测集。导出待审核的真实问题候选：

```powershell
.\.venv\Scripts\python.exe -m evaluation.export_feedback_cases
```

审核人员编辑 `evaluation/feedback_candidates.jsonl`，只把确认过且不依赖快照具体数值的条目标记为 `review_status: approved`，再通过 `python -m evaluation.run_eval --feedback-cases evaluation/feedback_candidates.jsonl` 执行。重复导出会保留已有审核状态、审核备注与人工断言，不会把它们重置为 `pending`。

生产 Compose 将采集器与 API 分离：一个 `collector` 独占 Supercell 刷新，`api-1`、`api-2` 只读共享快照并各自构建内存 RAG，Caddy 负载均衡并提供 HTTPS，Prometheus/Grafana 与 Loki/Promtail 持久化指标和日志。Grafana 在 `http://127.0.0.1:3000` 自动加载 “Clash Royale Agent Operations” 仪表盘；指标保留 30 天、日志保留 7 天，并预置失败率、快照/RAG 不一致和模型熔断告警规则。启动前必须设置强 `GRAFANA_ADMIN_PASSWORD`。

```powershell
docker compose -f compose.production.yml config --quiet
docker compose -f compose.production.yml up --build -d
```

本机 Docker 出口 IP 未加入 Supercell 白名单时，不要启动容器内 `collector`。继续用 `run_backend.ps1` 的 `RUNTIME_ROLE=all` 在 Windows 采集；或单独以 `RUNTIME_ROLE=collector` 启动本机采集器，再启动 Compose 中除 `collector` 外的服务。开发用 Caddy 使用本地 CA，地址为 `https://localhost`；公网域名部署需把 `deploy/Caddyfile` 的 `tls internal` 改为受信任证书或 ACME 配置。

在另一个 PowerShell 验证严格模式已真正加载：

```powershell
Invoke-RestMethod http://127.0.0.1:8091/health
```

预期至少包含：`live_data_enabled: True`、`external_api_required: True` 和 `model_api_configured: True`。

## 真实 API 冒烟测试

后端保持运行时，在第二个 PowerShell 执行：

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
$env:LIVE_API_BACKEND_URL = "http://127.0.0.1:8095"
.\.venv\Scripts\python.exe evaluation\run_live_api_smoke.py
```

该测试实际发出一个实时卡牌榜请求、一个多意图请求和一个纯开放 RAG 请求，并断言 Trace 中存在：`parser_api.status=api`、`live_data.source=supercell_api`、`static_card_fallback_count=0`、`MultiIntentOrchestrator`、成功的结构化/RAG 子任务、`model_generation=api`，以及通过的数值与引用校验。使用 `--report` 时会在每个阶段完成后立即保留结果。全部通过时输出 `LIVE_API_SMOKE_OK`。

也可以把真实冒烟测试并入完整测试命令：

```powershell
$env:RUN_LIVE_API_SMOKE = "true"
$env:LIVE_API_BACKEND_URL = "http://127.0.0.1:8095"
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

离线开发需要验证 JSON 快照逻辑时，显式设为 `$env:EXTERNAL_API_REQUIRED = "false"`；这不是生产或实时演示配置。
