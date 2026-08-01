# 皇室战争数据问答系统启动手册

本文只描述当前生效的本机运行方式。采集操作请交给独立任务，并先阅读
[`docs/SNAPSHOT_COLLECTION_HANDOFF.md`](docs/SNAPSHOT_COLLECTION_HANDOFF.md)。

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

真实凭证只从当前进程或 Windows 用户环境读取，不写入仓库：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "<key>", "User")
[Environment]::SetEnvironmentVariable("SUPERCELL_API_TOKEN", "<token>", "User")
```

修改用户环境变量后必须重启对应进程。采集前还必须确保 Supercell Key
白名单包含当前公网出口 IP。

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

结构化页面不调用模型。自由问答保留自然语言解析、多意图拆分和高级 RAG；环境分析先
检索当前范围的聚合证据，再由配置模型综合。战队赛赛程与备战功能已移除。

自由问答的普通结构化问题通常只调用一次模型解析，随后由本地 SQLite 生成答案；开放 RAG
问题通常调用一次解析和一次证据综合。综合完成后，本地质量门逐句校验数值和引用，不再调用
模型。未受证据支持的数值句会被省略，其余已验证内容继续返回并注明边界；若最终校验仍失败，
返回明确的安全拒答和已验证来源，不应显示通用“生成回答失败”。

## 测试

完整本地门禁：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

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
