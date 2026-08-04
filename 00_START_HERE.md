# 皇室战争数据问答系统启动手册

本文只描述当前生效的本机运行方式。采集操作请交给独立任务，并先阅读
[`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md)。

## 先选择运行目标

本项目有三种彼此隔离的运行目标，不要把它们的依赖混在一起：

1. **公开质量门**：只验证源码、匿名契约 fixtures 和故障降级，不需要真实 API Key，
   也不读取任何私有快照。首次克隆后先运行 `run_tests.ps1`。
2. **本地业务界面**：读取本机已发布的 SQLite 派生快照组。结构化页面直接查询本地数据；
   自由问答的模型解析和 RAG 综合需要 `OPENAI_API_KEY`。
3. **独立采集**：调用 Supercell API 生成或更新私有事实库，只允许采集任务读取
   `SUPERCELL_API_TOKEN`。API 角色和前端都不负责采集。

首次安装：

```powershell
Set-Location 'F:\All projects\agentscope-doc-qa-rescue-codex-crash'
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

公开仓库不包含业务快照，因此公开克隆可以完整运行质量门和容器存活检查，但在挂载或生成
授权的私有数据之前，不能提供真实卡牌、卡组、对局和 RAG 结果。

## 当前数据架构

- 长期事实库：`data/corpus/corpus.sqlite`。
- 活动快照组指针：`data/active_snapshot_group.json`。
- 默认查询范围：`7d_all`。
- 固定范围：当前 0-7 天、四个历史 7 天分段和累计 0-35 天，每个窗口提供前 100、前 200、前 500、前 1000 和全量，共 30 个 `dataset_scope`。
- 数据口径：`base8` 为默认基础八卡；`loadout_entity` 查询普通/觉醒/精英卡牌实体和塔楼；卡组接口继续用 `deck_mode=base8|full_loadout`。
- 业务 API 只读已发布派生数据，不在用户请求中采集、统计或构建 embedding。
- 采集、去重、统计和 RAG 文档生成不调用云端模型；RAG embedding 使用本机 Ollama。

`GET /api/datasets` 是滚动快照组的权威状态接口。旧的 `GET /snapshot/status`
仍用于兼容单快照链路，不能用它判断滚动事实库是否已经发布。

## 环境变量

模型提供方是固定工程契约：

```text
OPENAI_BASE_URL=https://crs.ruinique.com
OPENAI_WIRE_API=responses
OPENAI_MODEL=gpt-5.5
OPENAI_REVIEW_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=medium
PARSER_REASONING_EFFORT=medium
SYNTHESIS_REASONING_EFFORT=medium
```

真实凭证只从当前进程或 Windows 用户环境读取，不写入仓库。`OPENAI_API_KEY` 只用于模型解析、
审查与综合；`SUPERCELL_API_TOKEN` 只用于独立采集：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "<key>", "User")
[Environment]::SetEnvironmentVariable("SUPERCELL_API_TOKEN", "<token>", "User")
```

修改用户环境变量后必须重启对应进程。采集前还必须确保 Supercell Key
白名单包含当前公网出口 IP。

`run_tests.ps1` 不需要上述任何真实凭证。脚本会临时放入字面量 `test-key`，仅用于覆盖
“凭证已配置”分支；外部调用在公开门禁中被禁用或 mock，脚本结束后会恢复原环境变量。

## 启动后端

推荐使用 API 角色，它只读取已经发布的快照，不联系 Supercell：

```powershell
Set-Location 'F:\All projects\agentscope-doc-qa-rescue-codex-crash'
powershell -ExecutionPolicy Bypass -File .\run_api.ps1
```

默认地址：`http://127.0.0.1:8091`。

后端首次启动需要加载本地数据和索引，端口可能在几十秒后才监听。`/health` 可访问后，
`/ready` 仍可能暂时返回 `degraded`、`rag_status=building`；必须继续等待 RAG 预热完成再验收：

```powershell
Invoke-RestMethod http://127.0.0.1:8091/health
Invoke-RestMethod http://127.0.0.1:8091/ready
Invoke-RestMethod http://127.0.0.1:8091/api/datasets
```

验收标准：

- `/health.status == healthy`。
- `/ready.status == ready`；该接口仍包含兼容快照的就绪信息。
- `/api/datasets.snapshot_group_id` 与 `data/active_snapshot_group.json` 一致。
- `/api/datasets.datasets` 恰好有 30 个范围。
- 当前 7 天和累计 35 天的非空范围必须 `ready=true`；尚无批次覆盖的历史分段保持 `ready=false` 是正常空态，不能用当前数据回填。
- `/api/datasets.rag.fully_aligned == true`。

## 启动前端

另开一个 PowerShell：

```powershell
Set-Location 'F:\All projects\agentscope-doc-qa-rescue-codex-crash'
powershell -ExecutionPolicy Bypass -File .\run_web.ps1
```

打开 `http://127.0.0.1:8080`。前端默认代理
`http://127.0.0.1:8091/process`。

只读检查：

```powershell
(Invoke-WebRequest http://127.0.0.1:8080 -UseBasicParsing).StatusCode
```

应返回 `200`。

## 安全重启

优先在原终端按 `Ctrl+C`。原终端丢失时，先核对端口进程命令行，只结束本项目的
`runtime_multi.py` 和 `web_app.py`，不要按名称批量结束所有 Python 进程，也不要碰采集器：

```powershell
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in 8080, 8091 }
$listeners | Select-Object LocalPort, OwningProcess
Get-CimInstance Win32_Process |
  Where-Object { $listeners.OwningProcess -contains $_.ProcessId } |
  Select-Object ProcessId, Name, CommandLine
```

确认后先启动 `run_api.ps1` 并等待 `/ready`，再启动 `run_web.ps1`。

## 数据范围和卡组口径

所有结构化查询、自由问答和环境分析都显式携带 `dataset_scope`，模型不负责选择范围。
不传时兼容默认值为 `7d_all`；非法或未就绪范围必须返回明确错误，不能静默回退。

固定范围前缀：

```text
7d       当前 0-7 天
d7_14    7-14 天前
d14_21   14-21 天前
d21_28   21-28 天前
d28_35   28-35 天前
35d      累计 0-35 天
```

每个前缀都组合 `top_100/top_200/top_500/top_1000/all` 五个层级。

`base8` 会覆盖所有符合基础八卡合同的有效事实。`full_loadout` 只使用同时具备合法塔楼、
八卡 ID 和觉醒/精英槽位的对局；没有精确样本时返回无证据，不自动回退到 `base8`。

两种口径的标识符合同固定如下：

| 口径 | 卡牌主键 | 请求目录 | 规则 |
|---|---|---|---|
| `base8` | Supercell 英文标准名，例如 `Archers` | `GET /api/cards/catalog` | 中文只用于显示和别名解析 |
| `full_loadout` | 纯数字官方卡牌 ID，例如 `26000001` | `GET /api/loadouts/catalog` | 另需纯数字 `tower_id` 和每张卡的特殊状态 |

不要把基础八卡目录中的英文标准名填进完整配置的 `card_id`。后端不会按中文或英文名称
猜测官方 ID；此类请求固定返回 `INVALID_FULL_LOADOUT`。页面应显示中文名称，但普通模式
提交英文标准名，完整配置模式提交官方数字 ID。这是接口合同，不是数据缺失或语言兼容问题。

完整配置入口需要同时满足两个状态：

- `complete_loadout_ready=true`：当前范围存在合法完整载荷或完整卡组统计。
- `entity_stats_ready=true`：当前活动组已经用新版 schema 物化 `loadout_entity_stats`，可查询普通/觉醒/精英卡牌实体和塔楼。

旧活动组可能出现前者为 `true`、后者为 `false`。这表示原始载荷已经存在，但尚无新版实体
统计，不代表数据丢失；前端此时必须置灰完整配置，不能把 `base8` 结果冒充成觉醒/精英结果。

## 当前接口

- `GET /api/datasets`
- `GET /api/cards/catalog?dataset_scope=7d_all`
- `GET /api/cards/rankings?dataset_scope=7d_all&sort_by=usage_rate`
- `GET /api/cards/{card_id}/stats?dataset_scope=7d_all`
- `GET /api/loadouts/catalog?dataset_scope=7d_all`
- `GET /api/entities/catalog?dataset_scope=7d_all`
- `GET /api/entities/rankings?dataset_scope=7d_all&sort_by=usage_rate`
- `GET /api/entities/{entity_id}/stats?dataset_scope=7d_all`
- `POST /api/entities/compare`
- `POST /api/cards/compare`
- `POST /api/decks/profile`
- `POST /api/decks/matchup`
- `GET /api/meta/archetypes?dataset_scope=7d_all`
- `POST /process` 与 `/process/stream`

结构化页面不调用模型。自由问答保留自然语言解析、多意图拆分和高级 RAG，但不是所有
子问题都会进入 RAG：排行、单卡、双卡、精确八卡、对阵、共现和常见搭配在解析后由本地
SQLite 直答；开放式环境、体系或趋势分析才检索当前范围的聚合证据，再由配置模型综合。
战队赛赛程与备战功能已移除。

自由问答的普通结构化问题通常只调用一次模型解析，随后由本地 SQLite 生成答案；开放 RAG
问题通常调用一次解析和一次证据综合。综合完成后，本地质量门逐句校验数值和引用，不再调用
模型。未受证据支持的数值句会被省略，其余已验证内容继续返回并注明边界；若最终校验仍失败，
返回明确的安全拒答和已验证来源，不应显示通用“生成回答失败”。

## 测试

完整本地门禁：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

该命令与 GitHub Actions 使用同一公开门禁：单元/集成测试、348 条匿名契约评测和 28 条
合成故障注入。它不读取 `data/corpus/corpus.sqlite`、活动快照组、真实 Key 或网络 provider。

2026-08-02 本机完整门禁：公开 inventory 发现 `766` 项测试，分为 L0 单元/契约 `147`、L1 API/UI 集成 `42`、L2 AI/RAG 回归 `429`、L3 韧性/安全/运维 `148`；确定性评测 `344/344` 个启用用例通过，另有 `4` 个可选 RAG 路由用例跳过；`28/28` 个故障注入场景通过。2026-08-04 针对结构化/RAG 分流、完整配置实体、精确八卡、共现和多意图仲裁的聚焦回归套件 `106/106` 通过。检索消融 80 个用例中，MRR@5 从 BM25 的 `0.7556` 提升到 Hybrid + rerank 的 `0.9875`。完整方法见 `docs/QUALITY_EVALUATION_STRATEGY.md`。

只运行单元测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

测试、采集、统计和索引预热不要并行运行，以免 8 GB 可用内存被多个 Python/Ollama
进程同时占用。

## 采集入口

正常业务启动不触发采集。采集只允许独立任务运行以下入口：

```powershell
.\run_rolling_collection.ps1 -Mode weekly_expanded
```

每日任务默认上海时间 03:00，固定运行 20 万场 `weekly_expanded` 扩散采集；该模式名是
历史兼容命名，不再表示每周一次，也不再安排 `daily_ranked`。详细口径、监控、验收、发布和
故障处理见 [`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md)。

## 远程仓库与私有数据

Git 和 Docker 镜像只发布源码、测试、文档、配置模板，以及不含对局信息的中文名称/别名
配置。`data/` 中的原始对局、事实库、统计、RAG 文档、向量索引、状态与断点，连同日志和
导出文件，都必须留在本机。公开克隆不附带业务数据，需要通过独立采集任务生成或挂载经
授权的私有数据目录。

### 安全推送代码

每次推送都先列出允许公开的文件，显式暂存。下面的文件列表对应当前结构化/RAG 分流、
完整配置实体、精确八卡、共现、前端展示和文档口径修复；后续提交应按实际审查结果修改
列表，不能为了省事改用 `git add .`：

```powershell
Set-Location 'F:\All projects\agentscope-doc-qa-rescue-codex-crash'

$publicFiles = @(
  'structured_query.py'
  'answer_builder.py'
  'app_config.py'
  'query_answering.py'
  'query_parser.py'
  'retrieval_postprocess.py'
  'rolling_materializer.py'
  'runtime_multi.py'
  'skills/base.py'
  'skills/deck_skill.py'
  'skills/exact_deck_skill.py'
  'skills/loadout_entity_skill.py'
  'skills/meta_evidence.py'
  'skills/rag_skill.py'
  'skills/registry.py'
  'skills/structured_relationship_skill.py'
  'web_ui_template.py'
  'web_app.py'
  'evaluation/run_live_api_smoke.py'
  'data/card_aliases.zh-CN.json'
  'tests/test_answer_presentation.py'
  'tests/test_business_skills.py'
  'tests/test_evidence_synthesis_skill.py'
  'tests/test_multi_intent.py'
  'tests/test_open_analysis_pipeline.py'
  'tests/test_query_logic.py'
  'tests/test_retrieval_postprocess.py'
  'tests/test_supercell_live_data.py'
  'tests/test_structured_frontend.py'
  'tests/test_structured_stats.py'
  'README.md'
  '00_START_HERE.md'
  'docs/FULL_LOADOUT_DATA_CONTRACT.md'
  'docs/SNAPSHOT_COLLECTION_HANDOFF.md'
  'docs/SNAPSHOT_COLLECTION_PROMPT.md'
  'docs/card_aliases.md'
  'docs/decisions/ADR-010-base8-and-full-loadout-facts.md'
)

git status --short
git add -- $publicFiles
git diff --cached --name-only
git diff --cached --stat
git diff --cached --name-only |
  Select-String -Pattern '^data/|^tmp/|\.env|\.sqlite|\.sqlite3|\.db|\.jsonl|\.key|\.pem'
git commit -m 'fix: align full-loadout identifiers and documentation'
git push origin main
```

各命令的作用：

| 命令 | 作用 | 通过标准 |
|---|---|---|
| `Set-Location ...` | 进入本项目仓库，避免在错误目录操作 Git | 当前目录是项目根目录 |
| `$publicFiles = @(...)` | 建立本次允许公开的文件白名单 | 列表中只有已审查源码、测试和文档 |
| `git status --short` | 查看修改、未跟踪和暂存状态 | 先识别不应提交的私有或无关文件 |
| `git add -- $publicFiles` | 只暂存白名单文件；`--` 结束 Git 选项解析 | 不会顺带加入 `data/`、日志或其他未跟踪文件 |
| `git diff --cached --name-only` | 列出即将进入提交的文件 | 输出必须与白名单一致 |
| `git diff --cached --stat` | 检查暂存变更规模 | 没有异常大文件或数据文件 |
| `Select-String ...` | 扫描暂存路径中的常见私有数据和密钥文件类型 | 应当没有输出 |
| `git commit -m ...` | 在本地创建一个包含当前暂存内容的提交 | 提交成功且不包含私有文件 |
| `git push origin main` | 把本地 `main` 新提交发送到远程 `origin` | 远程更新成功 |

`.gitignore` 是最后一道防误操作边界，不代替暂存清单审查。当前规则忽略 `data/**`、
SQLite、日志、`tmp/`、导出和 `.env`；唯一允许跟踪的 `data/` 文件是
`data/card_aliases.zh-CN.json`，其中只能包含人工审阅的名称和别名，不能包含对局、
玩家标识、统计或凭据。

