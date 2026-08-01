# 独立采集任务提示词

将下面整段作为新 Codex 任务的第一条消息。它只负责数据采集、监控、验收和发布，
不承担业务功能开发。

```text
项目目录是 F:\All projects\agentscope-doc-qa-rescue-codex-crash。

先完整阅读 docs/SNAPSHOT_COLLECTION_HANDOFF.md、
docs/FULL_LOADOUT_DATA_CONTRACT.md 和
docs/plans/rolling-path-of-legend-corpus.md，然后严格按文档接管传奇之路滚动采集。

生产采集每天都固定使用 weekly_expanded。名称中的 weekly 是历史兼容命名，不表示每周一次。
不要选择 daily_ranked；它只保留为底层历史兼容模式，不属于当前生产采集流程。

开始前先做只读检查：当前分支和工作区状态、F 盘剩余空间、是否已有采集/物化写进程、
计划任务状态、data/corpus/collection_status.json、data/active_snapshot_group.json，
并运行 Supercell 预检。不得输出任何 API key/token。预检失败时停止并只告诉我需要加入
白名单的当前公网 IP 或其他明确失败原因，不要反复请求官方 API。

确认可以启动后，只启动一个 run_rolling_collection.ps1 -Mode weekly_expanded 进程。
从冻结的全球传奇之路前 1000 名开始，只沿已通过 pathOfLegend、稳定 battle_id、双方八卡
校验的对局扩散，目标严格 200000 场批内
唯一对局。并发保持 1、默认最多 1 请求/秒，不因进度慢提高并发。非传奇之路对局不计数、
不入库，也不能贡献对手 tag。

采集同时保留 base8 和 full_loadout（塔楼、八卡、觉醒、精英），不能改变基础 battle_id。
完整配置缺失或非法时只能排除 full_loadout，不能丢弃合法 base8 对局。采集、清洗、去重、
统计、RAG 文档生成和审计禁止调用云端 LLM 或云端 embedding；本机 Ollama 只允许在验收
通过后的三十范围发布阶段按现有链路运行。

采集运行期间只做低 Token 只读监控。每小时读取一次
data/corpus/collection_status.json、本轮 stdout/stderr 最后一行和进程状态，中文简短汇报：
status、mode、usable_battles/target_battles、sampled/fetched_players、request_count、
rate_limited、elapsed_seconds、error。不要打印玩家 tag、玩家列表、原始对局、完整 JSON
或日志全文；不要改代码、不要触发第二次采集、不要运行项目模型/RAG/embedding。
状态回调未刷新时清楚区分“状态文件值”和“磁盘工作区仍在更新”，不要扫描全库求实时值。

采集结束后按交接文档做本地确定性验收。所有批次要求前 100 成功 100/100、前 1000
至少 990/1000、rate_limited=0、无预算耗尽、无范围污染和事实冲突。每个生产批次还要求
批内唯一对局恰好 200000、source_exhausted=false。完整配置要求 unknown_special_slots=0、
slot_contract_failures=0、载荷冲突=0。只输出计数、通过数和失败原因，不输出原始记录。

只有批次验收通过后才允许沿现有后端链路进行过期、三十范围物化、聚合 RAG 文档、本机
embedding 和原子发布。最终必须核对 data/active_snapshot_group.json 与
GET http://127.0.0.1:8091/api/datasets 的 snapshot_group_id 一致，返回恰好三十个
dataset_scope，所有非空范围 ready，RAG fully_aligned=true。尚无批次覆盖的历史 7 天分段
保持 ready=false 是正常空态，不得用当前数据回填。完整配置还要核对 complete_loadout_ready
与 entity_stats_ready；只有两者都为 true 才能开放实体查询。注意 /snapshot/status 是旧兼容接口，不能用来判断
滚动快照组。发布失败时保留旧活动组，不要伪造成功或自动回退其他范围。

成功发布后按 00_START_HERE.md 安全重启 8091 后端和 8080 前端，不得结束其他 Python
或采集进程。最后汇报批次 ID/模式、唯一对局、事实新增、观察关系、覆盖率、限流/冲突、
完整配置覆盖、保留批次、滚动事实总数、活动快照组、三十范围/RAG 对齐和服务状态。
全部验收通过后，精确删除本批 data/rolling_work/<batch_id> 成功工作区，避免每天保留重复的
aggregates.sqlite 和 raw_battles.jsonl；不得通配删除，不得删除失败待续传或仍在运行的工作区。

数据源不开源。data/、rolling_data_exports/、日志、状态文件、原始对局、玩家 tag、SQLite、
Qdrant 和任何 API key/token 都不得加入 Git、提交或推送。远程仓库只允许前后端源码、测试、
无敏感信息的配置模板和文档；唯一例外是只含人工审阅名称与别名的
data/card_aliases.zh-CN.json。也不得把私有数据打进 Docker 镜像、CI artifact 或测试报告。
完成后停止监控，不要继续占用这个任务。
```
