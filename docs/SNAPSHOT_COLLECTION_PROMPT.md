# 独立采集排障任务提示词

将下面整段作为新 Codex 任务的第一条消息。正常采集由 Windows 计划任务和后端脚本负责；
这个任务只做一次性状态检查、故障归因、修复和验收，不建立 Codex 定时监听。

```text
项目目录是 F:\All projects\agentscope-doc-qa-rescue-codex-crash。

先完整阅读 docs/SNAPSHOT_COLLECTION_HANDOFF.md、
docs/FULL_LOADOUT_DATA_CONTRACT.md 和
docs/decisions/ADR-013-parallel-ranked-and-one-hop-collection.md，然后按文档排查传奇之路双通道
滚动采集。保留工作区已有改动，不输出任何 token、玩家 tag、原始对局或完整日志。

当前生产有两个独立通道：
1. daily_ranked 使用 token 槽位 0，采集当前全球传奇之路榜单，最多前 1000 人，不扩展对手。
   核心监督器以每轮实际开始时间维持两小时周期；超时后只立即补跑一轮，不累计过期任务。
2. weekly_expanded 使用 token 槽位 1，只迭代榜单种子的一层合法 POL 对手，不扩展第二层。
   扩展任务每 15 分钟检查，已有轮次运行时 IgnoreNew，结束后近似连续开始下一轮。

两个通道各自并发固定为 1、默认最多 1 请求/秒，可以并行网络采集，但导入、验收、过期、
物化和发布共用一个 corpus writer lock。battle_id 必须全局去重；重复对局只增加观察关系。
不得提高并发、绕过去重、混入非 pathOfLegend 对局或为了凑量扩展第二层。

断点暂存固定为 data/rolling_lanes/<mode>/active：核心上限 512 MiB、扩展上限 4 GiB、两者
合计上限 5 GiB。不得删除仍在运行、待合并、失败待续传或尚未发布的 active 目录。
Python/SQLite 临时文件应位于 tmp/collector-runtime/<mode> 的 F 盘目录，不能落到空间紧张的
C 盘。F 盘低于 20 GiB 时不得启动。

先做只读检查：
- ClashRoyale-Daily-Ranked-Every-2h 与 ClashRoyale-Expanded-Continuous 计划任务状态；
- 核心监督器、两通道 PowerShell/Python 子进程；
- 两份 schedule JSONL 的失败事件和最后两条；
- data/corpus/collection_status.json 及两个分通道状态；
- C/F 盘剩余空间和实际 TEMP/TMP 路径；
- 当前活动快照组和三十范围对齐状态。

只读取错误末尾和结构化字段。按批次汇报 status、batch_id、exit_code、失败类型、去重事实
累计与增量、去重前观察累计与增量、完整卡组累计与增量、数据库/暂存大小、榜单/扩展覆盖、
发布状态。不要粘贴完整状态 JSON、stdout/stderr、玩家列表或命令行中的凭据。

失败处理规则：
- IP/token/官方探针失败：PushPlus 告警，停止该轮网络采集，等待白名单或网络恢复。
- 429、预算耗尽、覆盖或范围验收失败、事实冲突、发布失败：按具体错误告警，不伪造成功。
- writer lock 等待超时的退出码 4：暂存可恢复，静默延后，不发送手机告警。
- accepted_publication_failed：事实已经验收入库，不重新采集；下一通道运行必须在 Supercell
  预检前优先执行纯发布重试，成功后原子恢复 accepted。发布期间继续保留旧活动快照。

扩展批次达到 200000 场可以结束；若一层队列自然耗尽，也允许结束，但排行榜覆盖必须达标、
扩展队列和可用 POL 对局不得为空、已排队对手请求成功率至少 99%。空榜单不得发布空批次。

每场合法 battlelog 同时保留 base8 和字段完整时的 full_loadout。base8 使用 Supercell 英文
标准名；full_loadout 的塔楼和卡牌使用纯数字官方 ID，并保留觉醒和精英字段。不能混用主键。
采集、清洗、去重、统计和审计不得调用云端 LLM/embedding；发布只使用本机 Ollama。

修复必须先写会失败的回归测试，再做最小修改。至少运行聚焦测试、PowerShell AST 语法检查、
git diff --check、敏感信息扫描和完整 unittest discover。修复线上任务时，不停止无关进程；
如必须重启某一采集通道，先确认断点已落盘，并验证恢复后仍只有一个同模式实例。

最终只汇报根因、修复、验证、当前两个通道状态、累计数据量、发布状态和是否需要用户处理。
正常采集恢复后结束本次 Codex 任务，不创建任何 Codex 自动化。
```
