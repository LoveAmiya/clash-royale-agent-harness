# 完整配置数据契约

状态：已实现，适用于 2026-07-31 之后启动的采集工作区。

## 两种查询口径

- `base8`：只使用双方各 8 张卡牌的 Supercell 英文标准名精确值。它是默认口径，并继续覆盖旧数据。
- `full_loadout`：使用纯数字官方塔楼 ID、8 个纯数字官方卡牌 ID，以及每张卡的官方特殊模式。它是更精确但样本更稀疏的高级口径。

同一次官方 battlelog 请求同时生成两种口径，不启动第二个采集器，也不增加 Supercell 请求量。`battle_id` 仍只由对局时间、双方 tag、基础 8 卡和皇冠结果生成；塔楼或特殊模式不参与 ID，因此旧事实可以被新观察安全补全。

### 标识符边界

- 基础八卡表沿用英文标准名作为卡牌主键和卡组签名元素。相关响应中的历史字段
  `card_id` 可能因此包含 `Archers` 这样的英文标准名，而不是数字。
- 完整配置表和实体表只接受官方数字 ID。`tower_id`、完整配置 `card_id` 以及实体 ID
  中的 official ID 部分都必须由十进制数字组成。
- 中文名称仅用于页面显示和自然语言别名解析，不进入任何卡组签名。
- `GET /api/cards/catalog` 服务于 `base8`；`GET /api/loadouts/catalog` 服务于
  `full_loadout`。前端必须按所选模式切换目录，不能复用另一个模式的请求值。
- 后端不根据中文或英文名称猜测完整配置的官方 ID，也不把非法完整配置回退到 `base8`。
  英文名称出现在完整配置 `card_id` 中时固定返回 `INVALID_FULL_LOADOUT`。

## 官方字段映射

- 塔楼：优先读取 `supportCards[0]`，兼容 `towerTroop`。
- 普通卡：保存官方 `id`、`name`、`level`、`maxLevel`、`evolutionLevel` 和 `maxEvolutionLevel`。
- `evolutionLevel` 缺失或为 `0`：普通卡。
- `evolutionLevel == 1`：觉醒。
- `evolutionLevel == 2`：精英。
- 其他非零值：`unknown`，该侧载荷不进入完整配置统计，但仍保留基础 8 卡统计。

普通等级和星级不用于推断精英状态。`starLevel` 是外观信息；`level/maxLevel` 是按稀有度偏移的普通等级信息。

完整配置必须同时满足：1 个官方塔楼 ID、8 个官方卡牌 ID、觉醒不超过 2 张、精英不超过 2 张、觉醒与精英合计不超过 3 张。任何字段缺失或槽位约束异常时，仅排除完整配置口径，不丢弃已经符合传奇之路和基础 8 卡契约的对局。

## 存储和冲突

基础事实继续存于 `battles`。完整配置存于独立的 `battle_loadouts`，以 `battle_id` 一对一关联：

- 旧事实没有载荷时，新观察可以补全。
- 不完整载荷可以被质量更高的载荷替换。
- 两份完整载荷内容矛盾时，不覆盖旧值；记录冲突并使当前批次验收失败。
- 过期删除最后一个观察关系时，外键级联删除对应载荷。

滚动物化为每个 `dataset_scope` 生成 `full_loadout_stats`、`full_loadout_matchup_stats` 和统一的 `loadout_entity_stats`。实体 ID 固定为 `card:{official_card_id}:ordinary|evolution|elite` 与 `tower:{official_tower_id}`，只统计完整载荷中实际出现的形态，不把所有卡牌虚拟展开三份。基础 8 卡表保持不变。RAG 仍只索引聚合证据，不向量化单场对局，并用 `deck_mode`/`entity_mode` 标记证据口径。

`GET /api/datasets` 的完整配置能力由两个字段表达：

- `complete_loadout_ready`：存在合法完整载荷或完整卡组统计。
- `entity_stats_ready`：当前活动组已物化统一实体统计，可供单卡、双卡、排名、自由问答和环境分析使用。

完整配置的全站入口要求两者同时为 `true`。旧活动组可能已经有完整载荷但没有统一实体表，
此时必须置灰，等待下一代快照组物化，不能回退或伪造实体统计。

## API

卡组画像、卡组对阵和自由问答请求新增 `deck_mode`，默认 `base8`。完整配置的卡组画像请求使用：

```json
{
  "deck_mode": "full_loadout",
  "dataset_scope": "7d_all",
  "loadout": {
    "tower_id": "159000000",
    "cards": [
      {"card_id": "26000000", "evolution_level": 1, "elite": false}
    ]
  }
}
```

`cards` 必须恰好 8 项。官方特殊模式编码必须一致：普通为 `(0, false)`，觉醒为 `(1, false)`，精英为 `(2, true)`。非法配置返回 `INVALID_FULL_LOADOUT`；没有精确样本返回 `NO_FULL_LOADOUT_EVIDENCE` 或 `NO_FULL_LOADOUT_MATCHUP_EVIDENCE`，不会自动回退到基础 8 卡数据。

完整配置实体查询使用 `/api/entities/catalog`、`/api/entities/rankings`、
`/api/entities/{entity_id}/stats` 和 `/api/entities/compare`。未物化时固定返回
`ENTITY_STATS_NOT_READY`，而不是读取旧基础卡统计。

## 验收和成本边界

固定探针与完成后抽样只输出完整塔楼数、完整载荷数、觉醒槽数、精英槽数、未知模式数和槽位失败数，不打印原始记录。未知模式或槽位失败不会污染完整配置统计。

采集、标准化、去重、SQLite 写入、结构化统计、RAG 文档生成和审计均为本地确定性处理：云端 LLM 调用 0，云端 embedding 调用 0。只有业务自由问答的意图解析和证据综合使用配置的模型 API；向量化继续使用本机 Ollama。
