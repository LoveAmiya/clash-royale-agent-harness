# 传奇之路双通道滚动采集手册

本文是独立采集任务的操作手册。采集由 Windows 计划任务和项目后端脚本运行，不依赖
Codex 保持开启，也不应使用 Codex 定时轮询。

项目根目录：`<repo-root>`，即本机克隆后的仓库根目录。

相关契约：

- [滚动数据仓库计划](plans/rolling-path-of-legend-corpus.md)
- [完整配置数据契约](FULL_LOADOUT_DATA_CONTRACT.md)
- [双通道架构决策](decisions/ADR-013-parallel-ranked-and-one-hop-collection.md)

## 一、不可变数据契约

- 数据源严格限定为 Supercell 官方 `pathOfLegend` battlelog。
- 核心通道 `daily_ranked` 每轮冻结当时可获得的全球传奇之路前 1000 名；榜单不足
  1000 人时采完全部实际可获得玩家后正常结束，空榜单不得发布空批次。
- 扩展通道 `weekly_expanded` 从同轮榜单种子出发，只迭代一层合法传奇之路对手，
  不继续迭代对手的对手。名称中的 `weekly` 仅为历史兼容命名。
- 非传奇之路对局不计数、不入库，也不能贡献扩展对手。
- `battle_id` 与双方顺序无关；同一对局在事实库 `battles` 中只保存一条，重复发现只新增
  批次观察关系。
- 同一 battlelog 同时生成 `base8` 和字段完整时的 `full_loadout`，不增加官方请求。
- `base8` 的卡牌主键是 Supercell 英文标准名；`full_loadout` 的塔楼和卡牌主键是纯数字
  官方 ID。中文名称仅用于显示和别名解析。
- 每个 token 对应的采集并发固定为 1，默认最多 1 请求/秒。两个 token 可让两个通道并行，
  但不得提高单通道并发，也不得在 429 后高频重试。
- 事实和观察关系保留真实 35 x 24 小时；到期后按观察时间淘汰。
- 采集、去重、统计、RAG 文档生成和审计的云端 LLM/embedding 调用均为 0；embedding
  仅使用本机 Ollama。
- `F:` 盘剩余空间不足 20 GiB 时拒绝启动。

## 二、双通道、文件与锁

| 项目 | 核心通道 | 扩展通道 |
|---|---|---|
| 模式 | `daily_ranked` | `weekly_expanded` |
| token 槽位 | `0` | `1` |
| 范围 | 当前全球榜单，最多 1000 人 | 榜单种子的一层 POL 对手 |
| 单通道并发 | `1` | `1` |
| 活动暂存上限 | 512 MiB | 4 GiB |
| 计划任务 | `ClashRoyale-Daily-Ranked-Every-2h` | `ClashRoyale-Expanded-Continuous` |

关键文件：

- 长期事实库：`data/corpus/corpus.sqlite`
- 单写进程锁：`data/corpus/writer.lock`
- 最新全局状态：`data/corpus/collection_status.json`
- 分通道状态：`data/corpus/collection_status.<mode>.json`
- 可续传暂存：`data/rolling_lanes/<mode>/active/`
- SQLite/Python 运行时临时目录：`tmp/collector-runtime/<mode>/`
- 核心监督日志：`logs/daily-ranked-supervisor.jsonl`
- 核心运行日志：`logs/daily-ranked-schedule.jsonl`
- 扩展运行日志：`logs/weekly-expanded-schedule.jsonl`
- 活动快照组指针：`data/active_snapshot_group.json`
- 派生快照组：`data/snapshot_groups/<snapshot_group_id>/`

两个通道可以并行执行网络采集。只有导入、验收、过期、物化和发布阶段持有同一
`writer.lock`，因此不会并发修改事实库。等待写锁最长 2 小时；仍未取得写锁时以退出码
`4` 延后合并，暂存保留并在后续运行恢复。该情况不是数据失败，不发送手机告警。

两个活动暂存目录合计上限为 5 GiB。不得删除仍在运行、待续传、待合并或未发布的
`active` 目录，也不得通过修改限制绕过空间保护。

计划运行器会在启动任何 Python 进程前，将 `TEMP`、`TMP`、`TMPDIR` 和 `SQLITE_TMPDIR`
固定到项目所在的 F 盘，避免大型 SQLite 排序占满系统 C 盘。正常退出时 SQLite 会清除自身
临时文件；异常断电后遗留目录仍受 F 盘 20 GiB 启动门槛保护。

滚动发布的权威业务接口是 `http://127.0.0.1:8091/api/datasets`。
`/snapshot/status` 仅为旧单快照兼容接口。

## 三、调度规则

### 核心通道

`scripts/run_daily_ranked_supervisor.ps1` 常驻运行，并以每轮的实际开始时间作为两小时锚点：

- 一轮在两小时内完成：等到“本轮开始时间 + 2 小时”再启动下一轮。
- 一轮达到或超过两小时：完成后立即启动下一轮。
- 多个错过的周期只合并为一次补跑，不累积并发或任务队列。
- 监督器文件锁保证只有一个核心监督器；核心采集器也不会同模式重叠。

Windows 计划任务每 15 分钟触发一次只是检查监督器是否仍存活，并使用 `IgnoreNew`。
它不代表每 15 分钟采集一次，也不会改变两小时锚定规则。

### 扩展通道

扩展任务每 15 分钟检查一次。已有扩展轮次运行时，`IgnoreNew` 会忽略重复触发；上一轮结束
后，下一次检查启动新一轮，所以它在正常情况下近似连续运行。单轮由有限的一层对手队列
决定，可能持续数小时，但不会无限向外迭代。

扩展任务的单次执行上限为 10 小时。如果被强制终止，已写入的有界暂存仍保留；通常不需
人工操作，下一次 15 分钟检查会恢复。若退出属于实际失败，PushPlus 会同时告警。

## 四、电脑电源条件

- Codex 和项目聊天窗口可以关闭，不影响后端计划任务。
- 电脑必须保持开机且 Windows 系统处于唤醒状态。系统睡眠、休眠、关机或断电期间，CPU、
  网络和脚本都会暂停，采集不能继续。
- 显示器关闭或 Windows 的“关闭屏幕”不会影响采集。建议接通电源时 5 到 10 分钟关闭屏幕，
  但将“使设备进入睡眠状态”设为“从不”。这能避免屏幕长期点亮，而不牺牲采集。
- 合上笔记本盖通常会触发系统睡眠。只有明确把“合上盖子时”设为“不采取任何操作”，且
  能保证散热时，才可合盖采集。
- 从睡眠、休眠或关机恢复后，计划任务的 `StartWhenAvailable` 会重新启动缺失的任务，但
  睡眠期间不会补造采集数据，也不保证保留当时尚未落盘的网络请求。

## 五、环境变量与预检

环境变量应写入 Windows 用户级环境变量，不写入 Git、日志或命令历史：

- `SUPERCELL_API_TOKENS`：推荐写成两枚 key 的 JSON 数组，槽位 `0` 是核心 key，槽位 `1`
  是扩展 key；也兼容逗号、分号或换行分隔。
- `SUPERCELL_API_TOKEN`：核心 key 的旧兼容变量。如果该变量保存核心 key，而
  `SUPERCELL_API_TOKENS` 只保存一枚不同的扩展 key，运行器会按“旧变量核心 + 新变量扩展”
  自动组合为槽位 `0` 和 `1`。
- `COLLECT_NOTIFY_PROVIDER=pushplus`
- `COLLECT_NOTIFY_TOKEN`：PushPlus token。脚本也兼容既有的 `Pushplus` 等历史变量名。

分别执行只读预检，不启动采集：

```powershell
Set-Location '<repo-root>'
.\scripts\run_daily_ranked_schedule.ps1 -Mode daily_ranked -TokenIndex 0 -DryRun
.\scripts\run_daily_ranked_schedule.ps1 -Mode weekly_expanded -TokenIndex 1 -DryRun
```

预检会检查 token 槽位、当前公网 IP/白名单、官方接口、`F:` 盘空间和同模式活动进程，且
不得打印 token。只有两个槽位各自预检成功，才具备双通道并行条件。

仅测试 PushPlus 链路，不启动采集：

```powershell
.\scripts\run_daily_ranked_schedule.ps1 -Mode daily_ranked -TokenIndex 0 -NotifyTest
```

## 六、安装、启动、停止与恢复

在管理员 PowerShell 中安装或替换两个计划任务：

```powershell
Set-Location '<repo-root>'
.\scripts\install_parallel_collection_tasks.ps1
Start-ScheduledTask -TaskName 'ClashRoyale-Daily-Ranked-Every-2h'
Start-ScheduledTask -TaskName 'ClashRoyale-Expanded-Continuous'
```

安装脚本会同时替换两个任务。若只需重启现有任务，不要重新安装，直接执行两条
`Start-ScheduledTask`。

出门关机前停止两个计划任务，并停止属于本项目的监督器与采集进程：

```powershell
$project = '<repo-root>'
Stop-ScheduledTask -TaskName 'ClashRoyale-Daily-Ranked-Every-2h'
Stop-ScheduledTask -TaskName 'ClashRoyale-Expanded-Continuous'
$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $PID -and $_.CommandLine -and
  $_.CommandLine.Contains($project) -and
  $_.CommandLine -match 'run_daily_ranked_supervisor\.ps1|run_daily_ranked_schedule\.ps1|collect_rolling_corpus\.py'
}
$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

强制停止可能中断当前网络请求，但已落盘的暂存会在下次启动时恢复。不要因此删除
`data/rolling_lanes`。

## 七、低成本状态检查

查看任务状态：

```powershell
$names = 'ClashRoyale-Daily-Ranked-Every-2h','ClashRoyale-Expanded-Continuous'
Get-ScheduledTask -TaskName $names | ForEach-Object {
  $info = $_ | Get-ScheduledTaskInfo
  [pscustomobject]@{
    TaskName = $_.TaskName
    State = $_.State
    LastRunTime = $info.LastRunTime
    NextRunTime = $info.NextRunTime
    LastTaskResult = $info.LastTaskResult
  }
}
```

查看最近运行事件，不读取完整日志：

```powershell
Get-Content '.\logs\daily-ranked-supervisor.jsonl' -Tail 5
Get-Content '.\logs\daily-ranked-schedule.jsonl' -Tail 2
Get-Content '.\logs\weekly-expanded-schedule.jsonl' -Tail 2
```

查看已发布汇总：

```powershell
$s = Get-Content '.\data\corpus\collection_status.json' -Raw -Encoding utf8 |
  ConvertFrom-Json
[pscustomobject]@{
  status = $s.status
  batch_id = $s.batch_id
  mode = $s.collection_mode
  unique_battles = $s.validation.unique_battles
  ranked = "$($s.validation.ranked_successes)/$($s.validation.ranked_target)"
  expansion = "$($s.validation.expansion_successes)/$($s.validation.expansion_target)"
  published = $s.publication.status
  datasets = $s.publication.dataset_count
  aligned = $s.publication.fully_aligned
}
```

不要输出 token、玩家 tag、原始对局、完整状态 JSON 或完整日志。运行中的
`collection_status.json` 可能仍指向最近已发布批次；是否正在采集应结合计划任务、进程和
两份 schedule 日志判断。

## 八、批次验收门槛

所有批次必须满足：

- 实际榜单存在时，前 100 名必须完整成功。
- 榜单目标覆盖率至少 99%；实际榜单不足 1000 人时，目标是实际可获得人数。
- `rate_limited == 0`，且未耗尽刷新预算。
- 无事实冲突、无完整载荷冲突、无传奇之路范围污染。

扩展批次还必须满足：

- 只访问榜单种子的一层对手。
- 若达到 200,000 场批内唯一对局，可按目标完成。
- 若一层队列自然耗尽，可按有限数据源正常完成，但扩展队列不得为空、可用 POL 对局不得
  为空，且已排队对手请求成功率至少 99%。
- 不得为了凑满 200,000 场扩展第二层或混入其他模式。

最终成功状态应满足：

```text
status=accepted
validation.passed=true
publication.status=published
publication.dataset_count=30
publication.fully_aligned=true
```

失败批次不得进入正式观察关系，旧活动快照组继续服务。

## 九、PushPlus 告警与退出行为

正常成功不推送。以下可操作失败会推送不同标题和错误字段：

- 缺少指定 token 槽位。
- 当前 IP 不在白名单、官方探针失败或网络预检失败。
- `F:` 盘低于安全阈值。
- 采集异常、429、预算耗尽、范围或覆盖验收失败。
- 事实冲突、导入失败、物化或发布失败。
- 调度脚本自身异常或其他非零退出。

通知会包含失败类型、批次 ID、退出码、当前累计去重事实数、去重前观察数、完整卡组记录、
本轮增量、数据库大小、通道暂存大小和下一任务时间。通知不得包含 Supercell/PushPlus
token、玩家 tag、原始对局或完整日志。

以下情况默认不推送：成功、同模式任务已在运行而跳过、退出码 `4` 的可恢复合并延后。

## 十、故障处理

- `403` / IP 不匹配：在手机收到 PushPlus 后远程更新两个 Supercell key 的白名单，再等待
  后续调度重试；不要更换数据源。
- `429`：本批验收失败；不要提高并发或立即手工循环重试。
- 网络超时或进程终止：保留活动暂存，后续同模式运行恢复。
- 写锁等待超时 / 退出码 `4`：无需处理，另一通道发布结束后由后续运行合并。
- `source_exhausted`：扩展一层队列确已遍历且扩展成功率达标时可正常验收；否则按具体
  coverage 失败处理，不扩展第二层。
- `conflicting_battle_facts` 或完整载荷冲突：隔离本批并排查，不覆盖旧事实。
- `accepted_publication_failed`：事实批次已接受但活动指针未切换；保留旧组，单独排查物化。
- 后续任一通道启动时，会在 Supercell token/IP 预检和新网络采集之前优先重试上述已入库
  批次的发布。成功后原子恢复为 `accepted`；写锁占用时静默延后，重复发布失败才告警。
- 磁盘不足：人工核对后清理可再生文件；不得删除事实库、活动/上一代快照组、活动指针或
  未完成暂存。

若批次已经入库但发布失败，在确认没有写进程后可单独重试物化：

```powershell
.\.venv\Scripts\python.exe .\scripts\materialize_rolling_snapshot.py
```

## 十一、发布、导出与隐私边界

采集脚本在验收通过后自动执行过期、三十范围物化、聚合 RAG 构建、本机 embedding 和
原子指针切换。业务 API 使用活动快照组；不要手工运行旧 `build_structured_stats.py`。

按范围导出 JSONL 和 SHA-256 清单时，不复制整个 SQLite：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_rolling_scope.py --scope 7d_all
```

导出、原始对局、事实库、快照组、Qdrant、日志、状态文件、玩家 tag 和任何 API 凭据都属于
本地私有数据，不得加入 Git、提交、推送、容器镜像、CI artifact 或测试报告。
`data/card_aliases.zh-CN.json` 是唯一允许跟踪的 `data/` 文件。

最终只汇报：通道、状态、批次 ID、唯一事实累计与增量、去重前观察累计与增量、完整卡组
累计与增量、榜单/扩展覆盖率、限流与冲突、暂存与数据库大小、发布/三十范围对齐结果和
退出码。不要粘贴原始对局、玩家标识或完整状态 JSON。
