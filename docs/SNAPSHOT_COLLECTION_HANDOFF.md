# 传奇之路滚动采集交接

本文是独立采集任务的唯一操作手册。业务开发任务不再负责采集或长时间监控。

项目根目录：`F:\All projects\agentscope-doc-qa-rescue-codex-crash`

相关契约：

- [滚动数据仓库计划](plans/rolling-path-of-legend-corpus.md)
- [完整配置数据契约](FULL_LOADOUT_DATA_CONTRACT.md)
- [独立任务提示词](SNAPSHOT_COLLECTION_PROMPT.md)

## 一、不可变数据契约

- 数据源严格限定为 Supercell 官方 `pathOfLegend` battlelog。
- 排行榜种子是当次冻结的全球传奇之路前 1000 名和官方名次。
- `weekly_expanded` 只允许从已经通过传奇之路、稳定 ID、双方八卡校验的对局扩散对手 tag。
- 生产采集每天都运行 `weekly_expanded`；名称中的 `weekly` 是历史兼容命名，不再表示每周一次。
- `daily_ranked` 仅保留为底层历史兼容模式，不进入计划任务、默认命令或正常交接流程。
- 非传奇之路对局不计数、不入库，也不能贡献扩散 tag。
- `battle_id` 对双方顺序无关；同一对局在事实库 `battles` 中只能有一条，重复发现只新增观察关系。
- 同一 battlelog 同时生成 `base8` 和 `full_loadout`，不增加官方请求。
- 完整配置保留塔楼、八卡 ID、觉醒和精英；完整配置缺失或非法时只排除 `full_loadout`，不丢弃合法 `base8` 事实。
- `base8` 的卡牌主键固定为 Supercell 英文标准名；`full_loadout` 的塔楼和卡牌主键固定为纯数字官方 ID。中文名称只用于显示和别名解析，两种主键不得在采集、物化或 API 请求中混用。
- 采集并发固定为 1，默认最多 1 请求/秒；不能为了赶进度提高并发或在 429 后猛重试。
- 事实和观察关系保留真实 35×24 小时；有效扩散批次在窗口内不设数量上限。
- 采集、清洗、去重、统计、RAG 文档生成和审计的云端 LLM/embedding 调用均为 0；embedding 仅使用本机 Ollama。
- 新扩散批次预计写入后剩余空间不足 20 GiB 时拒绝启动。

## 二、文件、锁和权威状态

- 长期事实库：`data/corpus/corpus.sqlite`
- 单写进程锁：`data/corpus/writer.lock`
- 当前采集状态：`data/corpus/collection_status.json`
- 断点工作区：`data/rolling_work/<batch_id>/`
- 活动快照组指针：`data/active_snapshot_group.json`
- 派生快照组：`data/snapshot_groups/<snapshot_group_id>/`

采集脚本不是 8092 HTTP 服务。旧 `run_collector.ps1`、`data/snapshot_work`、
`/snapshot/status` 和旧单快照监控说明仅作兼容，不得用于新的滚动采集。

滚动发布的权威业务接口是：

```text
http://127.0.0.1:8091/api/datasets
```

## 三、生产采集和调度

上海时区每天 03:00 运行一次：

- 每天固定执行 `weekly_expanded`：先抓冻结的前 1000 名，再沿合法传奇之路对局扩散。
- 每个成功批次必须恰好包含 200,000 场批内唯一对局；与历史批次重复时只增加观察关系，不重复写入事实。
- 不再运行日采。需要临时补采时仍使用 `weekly_expanded`，并受同一写锁、限流和验收门槛约束。
- 35 天窗口内扩散批次不设 5 份或 35 份数量上限；真实时间到期后由观察关系过期机制淘汰。

查看计划任务：

```powershell
Get-ScheduledTask -TaskName 'ClashRoyale-Rolling-PathOfLegend'
Get-ScheduledTaskInfo -TaskName 'ClashRoyale-Rolling-PathOfLegend'
```

安装或替换默认计划任务：

```powershell
.\scripts\install_rolling_schedule.ps1 -Hour 3 -Replace
```

## 四、启动前检查

1. 确认没有另一个采集、过期或物化写进程。
2. 确认 `F:` 盘剩余空间至少 20 GiB。
3. 运行 Supercell 只读预检；不得打印 token：

```powershell
Set-Location 'F:\All projects\agentscope-doc-qa-rescue-codex-crash'
.\.venv\Scripts\python.exe -m supercell_preflight --timeout-seconds 20
```

只有探针成功且当前公网 IP 已在 Supercell Key 白名单中，才能启动采集。

## 五、启动采集

生产采集：

```powershell
.\run_rolling_collection.ps1 -Mode weekly_expanded
```

需要后台运行时，为本轮使用独立日志名：

```powershell
$mode = 'weekly_expanded'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = ".\logs\rolling-$mode-$stamp.stdout.log"
$stderr = ".\logs\rolling-$mode-$stamp.stderr.log"
Start-Process powershell.exe -WindowStyle Hidden `
  -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','.\run_rolling_collection.ps1','-Mode',$mode `
  -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
```

不要启动第二个采集器。`CorpusWriterLock` 会拒绝并发写，但操作人员仍应先核对进程。

## 六、低 Token 监控

采集期间只读取状态文件和本轮日志末尾，每小时一次即可。不要读取或打印原始对局、
玩家 tag、完整 JSON、玩家列表；不要调用项目模型、RAG 或 embedding；不要修改代码或
触发第二次采集。

```powershell
$status = Get-Content '.\data\corpus\collection_status.json' -Raw -Encoding utf8 |
  ConvertFrom-Json
[pscustomobject]@{
  status = $status.status
  mode = $status.collection_mode
  usable_battles = $status.usable_battles
  target_battles = $status.target_battles
  sampled_players = $status.sampled_players
  fetched_players = $status.fetched_players
  request_count = $status.request_count
  rate_limited = $status.rate_limited
  elapsed_seconds = $status.elapsed_seconds
  error = $status.error
}
```

进度回调默认每小时写一次，所以磁盘可能已经前进而状态数字尚未刷新。状态停滞时只检查：

- 本轮进程是否仍存活。
- 本轮 stderr 最后一行是否出现 403、429、超时或磁盘错误。
- `data/rolling_work/<batch_id>/aggregates.sqlite` 的修改时间是否继续变化。

不得为了获得更实时的数字修改采集器或扫描整个事实库。

## 七、批次验收门槛

所有批次必须满足：

- 前 100 名成功 `100/100`。
- 前 1000 名成功率至少 99%，即至少 `990/1000`。
- `rate_limited == 0`。
- `refresh_budget_exhausted == false`。
- 无事实冲突、无传奇之路范围污染。

生产扩散批次还必须满足：

- 批内唯一对局恰好 `200000`。
- `source_exhausted == false`。
- 扩散只来自已接受的传奇之路对局。

完整配置审计还应满足：

- `complete_battle_rows`、塔楼、觉醒、精英计数可解释。
- `unknown_special_slots == 0`。
- `slot_contract_failures == 0`。
- 完整载荷冲突为 0。

`data/corpus/collection_status.json` 的最终成功状态应为：

```text
status=accepted
validation.passed=true
publication.status=published
publication.dataset_count=30
publication.fully_aligned=true
```

失败批次不得进入正式观察关系；旧活动快照组继续服务。

## 八、发布与业务验收

采集脚本在批次验收通过后自动执行过期、三十范围物化、聚合 RAG 构建、本机 embedding
和原子指针切换。不要再手工运行旧 `build_structured_stats.py`。

若批次已经入库但发布失败，可在确认没有写进程后单独重试物化：

```powershell
.\.venv\Scripts\python.exe .\scripts\materialize_rolling_snapshot.py
```

发布成功后重启业务 API 和前端，操作步骤见项目根目录 `00_START_HERE.md`。然后核对：

```powershell
$pointer = Get-Content '.\data\active_snapshot_group.json' -Raw -Encoding utf8 |
  ConvertFrom-Json
$datasets = Invoke-RestMethod 'http://127.0.0.1:8091/api/datasets'
[pscustomobject]@{
  pointer_group = $pointer.snapshot_group_id
  api_group = $datasets.snapshot_group_id
  dataset_count = @($datasets.datasets).Count
  ready_count = @($datasets.datasets | Where-Object ready).Count
  rag_status = $datasets.rag.status
  rag_fully_aligned = $datasets.rag.fully_aligned
}
```

必须满足指针与 API 组 ID 一致、`dataset_count=30`、`rag_fully_aligned=true`。所有非空范围
必须 `ready=true`；尚无批次覆盖的历史 7 天分段应保留 `ready=false` 空态，不算发布失败，
也不得用当前 7 天数据回填。至少当前 `7d_*` 和累计 `35d_*` 的可用范围应正常发布。

对于完整配置，还要核对 `complete_loadout_ready` 和 `entity_stats_ready`。前者表示范围内存在
合法完整载荷，后者表示新版实体统计已经物化；只有两者都为 `true`，前端才能开放普通/
觉醒/精英实体和塔楼查询。旧活动组可能只有前者为 `true`，这是迁移状态，不得伪造实体统计。

业务验收全部通过后，才允许删除本次已接受批次的断点工作区
`data/rolling_work/<batch_id>/`。删除前必须精确核对批次 ID、`status=accepted`、活动指针和 API
组 ID 一致；不得通配删除 `rolling_work`，不得删除仍在运行、失败待续传或尚未发布的工作区。
正式事实和观察关系已经在 `data/corpus/corpus.sqlite` 中，成功工作区只是可再生成的采集副本。

## 九、故障边界

- `403` / IP 不匹配：停止，更新白名单并重新预检。不得替换成其他数据源。
- `429`：本批验收失败；不提高并发、不立即循环重试。
- 超时或预算耗尽：保留断点工作区，确认口径一致后续传；不删除工作区重抓。
- `source_exhausted`：报告真实短缺，不混入非传奇之路对局。
- `conflicting_battle_facts` 或完整载荷冲突：隔离本批并排查，不静默覆盖旧事实。
- `accepted_publication_failed`：事实批次已接受但活动指针未切换；保留旧组，单独排查物化。
- 磁盘不足：清理需人工确认；不要删除事实库、当前/上一代快照组或活动指针。
- 业务 `/snapshot/status` 显示旧 20 万快照：这是兼容接口，不代表滚动发布失败；检查 `/api/datasets`。

## 十、导出给外部分析

按范围导出 JSONL 和 SHA-256 清单，不复制整个 SQLite：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_rolling_scope.py --scope 7d_all
```

导出默认写入 `rolling_data_exports/`，不会调用模型或 embedding。

这些导出、原始对局、事实库、快照组、Qdrant、日志、状态文件、玩家 tag 和任何 API
凭据都属于本地私有数据，不得加入 Git、提交或推送。远程仓库只发布前后端源码、测试、
无敏感信息的配置模板和文档；提交前必须核对 `.gitignore`、暂存文件清单和密钥扫描结果。
Docker 构建上下文同样排除这些文件；不得通过容器镜像、CI artifact 或测试报告绕过该边界。
`data/card_aliases.zh-CN.json` 是唯一允许跟踪的 `data/` 文件，只包含人工审阅的名称与别名，
不包含对局、玩家标识、统计或凭据。

## 十一、完成后交接摘要

最终只汇报：批次 ID、唯一对局数、事实新增数、观察关系数、排行榜覆盖率、限流与
冲突数、完整配置覆盖摘要、保留批次数、滚动事实总数、活动快照组 ID、三十范围/RAG 对齐
结果、是否重启业务服务，以及是否已清理本批成功工作区。不要粘贴原始对局或完整状态 JSON。
