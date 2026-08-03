# 卡牌中文名与别名维护

## 可编辑文件

`data/card_aliases.zh-CN.json` 是人工审阅入口：

- `display_name`：前端、结构化查询、自由问答和环境分析统一使用的标准中文名。
- `aliases`：自由问答自然语言解析允许识别的旧称、简称和社区称呼。
- 在 `base8` 口径中，英文 `card_name` 是快照、SQLite 和 API 使用的稳定主键，不作为最终回答展示名。
- 在 `full_loadout` 口径中，主键是官方数字卡牌 ID；中文名称和英文名称都不能替代该数字 ID。

修改 JSON 后需要重启 8091 API 和 8080 Web 服务。启动时会校验文件结构；格式错误会直接报出文件路径，不会静默使用损坏的词表。

## 回答展示规则

`answer_presentation.py` 在事实校验完成后统一处理用户可见文本：

1. `base8` 的英文卡牌主键或 `full_loadout` 的官方数字 ID 转为 `display_name`。
2. 确定无歧义的中文旧称转为 `display_name`。
3. 清理 Markdown 井号标题和星号强调。
4. 将 `conclusion`、`data evidence`、`data boundaries` 转为中文纯文本标题。

前端还会对 SSE 累计文本执行一次纯文本清理，作为防御性兜底。

## 解析规则

1. 英文空格、点、下划线和连字符差异会归一化。
2. 进化形态接受“进化”“觉醒”“evo”“evolved”等前后缀。
3. 英雄形态接受“英雄”“hero”等前后缀。
4. 同一个归一化别名只能属于一张卡牌，测试会拒绝冲突。
5. 自由问答保留模型意图解析和本地高置信别名兜底；结构化页面不需要别名解析：
   普通 8 卡提交 Supercell 英文标准名，完整配置提交官方数字 ID。

两个结构化目录不能混用：普通 8 卡读取 `GET /api/cards/catalog`，完整配置读取
`GET /api/loadouts/catalog`。详细边界见 [完整配置数据契约](FULL_LOADOUT_DATA_CONTRACT.md)。

## 导出命令

重新导出当前有效别名表：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_card_aliases.py
```

导出当前快照的环境体系人工命名包：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_archetype_naming_package.py
```

输出位于 `data/manual_review/archetype_naming_<snapshot_id>.json`。人工填写每个体系的 `reviewed_name` 和 `review_notes`；“Unclassified deck family”会保留代表卡组与核心卡，供后续拆分。
