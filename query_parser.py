"""将自由表达的玩家问题转换为经过校验的路由字段。

解析器优先使用兼容 LLM 的 JSON 契约，同时保留本地归一化和兜底规则作为确定性
安全网。因此 Router 消费的是标准卡名、范围受限的排名和已知意图，而不是原始自然语言。
"""

import json
from pathlib import Path
from typing import Any

# Root entry points may import this module without loading the package bootstrap.
import app_config  # noqa: F401 - initializes the src package path for root runs.

from clashroyale_agent.qa.intents import (
    VALID_METRICS,
    is_supported_single_intent,
    is_valid_metric,
)
from clashroyale_agent.qa.card_aliases import CardAliasResolver, normalize_card_alias
from clashroyale_agent.qa.metrics import extract_metrics, get_metric, normalize_metrics
from clashroyale_agent.qa.parser_entities import (
    apply_selected_entity_mode,
    detect_entity_reference as detect_packaged_entity_reference,
)
from clashroyale_agent.qa.parser_schema import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
    MAX_SUBQUERIES,
    PARSER_SYSTEM_PROMPT,
    TOWER_ENTITY_NAMES,
)
from clashroyale_agent.qa.parser_primitives import (
    coerce_round_value,
    extract_date,
    extract_json_block,
    extract_round_number,
    extract_text_content,
    normalize_text,
)
from clashroyale_agent.qa.parser_metadata import (
    LocalParseMetadataDependencies,
    build_parse_metadata,
    infer_local_parse_metadata as infer_packaged_local_parse_metadata,
    make_multi_intent_result,
    merge_parse_metadata,
    subquery_semantic_key,
)
from clashroyale_agent.qa.parser_fallback import (
    FallbackParseDependencies,
    fallback_parse_query as fallback_packaged_parse_query,
)
from clashroyale_agent.qa.parser_multi_intent import (
    MultiIntentDependencies,
    fallback_parse_multi_intent as fallback_packaged_multi_intent,
    normalize_multi_intent_query as normalize_packaged_multi_intent_query,
)
from clashroyale_agent.qa.parser_normalization import (
    ParserNormalizationDependencies,
    normalize_parsed_query as normalize_packaged_parsed_query,
)
from clashroyale_agent.qa.parser_rules import (
    has_explicit_rank_signal,
    has_explicit_top_n_signal,
    has_implicit_list_signal,
    is_asking_players,
    is_card_compare_query as is_packaged_card_compare_query,
    is_card_cooccurrence_query as is_packaged_card_cooccurrence_query,
    is_card_query as is_packaged_card_query,
    is_card_rank_lookup_query as is_packaged_card_rank_lookup_query,
    is_card_ranking_query,
    is_deck_query,
    is_match_preparation_query,
    is_meta_analysis_query as is_packaged_meta_analysis_query,
    is_meta_delta_query,
    is_schedule_query,
    is_schedule_summary_query,
)
from clashroyale_agent.qa.ranking import (
    CHINESE_NUM_MAP,
    coerce_rank_value,
    coerce_top_n_value,
    extract_cn_number,
    extract_rank_target,
    extract_top_n,
)


# 路由前先归一化玩家昵称和中英文写法；标准 key 也是卡牌元数据和下游 Skill 使用的名称。
CARD_ALIASES = {
    "Hog Rider": ["hog rider", "hog", "野猪骑士", "猪"],
    "Miner": ["miner", "矿工"],
    "Poison": ["poison", "毒药"],
    "Firecracker": ["firecracker", "烟花炮手"],
    "Lava Hound": ["lava hound", "熔岩猎犬", "天狗"],
    "Balloon": ["balloon", "气球兵", "气球"],
    "Skeleton King": ["skeleton king", "骷髅王"],
    "Zappies": ["zappies", "电击车小队"],
    "Rascals": ["rascals", "淘气三人组"],
    "Tower Princess": ["tower princess", "公主塔"],
    "The Log": ["the log", "滚木"],
    "Skeletons": ["skeletons", "小骷髅"],
    "Fireball": ["fireball", "火球"],
    "Arrows": ["arrows", "箭雨"],
    "Tornado": ["tornado", "龙卷风"],
    "Barbarian Barrel": ["barbarian barrel", "滚桶", "野蛮人滚桶"],
    "Electro Spirit": ["electro spirit", "电灵"],
    "Dark Prince": ["dark prince", "黑王"],
    "Royal Giant": ["royal giant", "皇巨", "皇家巨人"],
    "X-Bow": ["x-bow", "xbow", "弩", "连弩"],
    "Goblin Drill": ["goblin drill", "钻机"],
    "Graveyard": ["graveyard", "墓园"],
    "Princess": ["princess", "公主"],
    "Monk": ["monk", "武僧"],
    "Goblin Cage": ["goblin cage", "哥布林牢笼"],
    "Freeze": ["freeze", "冰冻"],
    "Executioner": ["executioner", "刽子手"],
    "Electro Wizard": ["electro wizard", "电法"],
    "Electro Giant": ["electro giant", "e-giant", "egiant", "雷电巨人", "电巨"],
    "Baby Dragon": ["baby dragon", "绿龙", "青龙", "龙宝"],
}


# Canonical Chinese names plus common Chinese-community abbreviations. The
# standard English card name is always accepted separately from cards_meta.
# Evolution and Hero forms are derived below to keep the base terminology in
# one auditable place.
CARD_ALIAS_OVERRIDES = {
    "Archer Queen": ["\u5f13\u7bad\u5973\u7687", "aq"],
    "Archers": ["\u5f13\u7bad\u624b"],
    "Arrows": ["\u4e07\u7bad\u9f50\u53d1", "\u7bad\u96e8"],
    "Baby Dragon": ["\u98de\u9f99\u5b9d\u5b9d", "\u5b9d\u5b9d\u9f99", "\u7eff\u9f99", "\u9752\u9f99", "\u9f99\u5b9d"],
    "Balloon": ["\u6c14\u7403\u5175", "\u6c14\u7403"],
    "Bandit": ["\u5e7b\u5f71\u523a\u5ba2", "\u523a\u5ba2"],
    "Barbarian Barrel": ["\u91ce\u86ee\u4eba\u6eda\u6876", "\u86ee\u6876", "\u6eda\u6876"],
    "Barbarian Hut": ["\u91ce\u86ee\u4eba\u5c0f\u5c4b"],
    "Barbarians": ["\u91ce\u86ee\u4eba", "\u86ee\u4eba"],
    "Bats": ["\u8759\u8760"],
    "Battle Healer": ["\u6218\u6597\u5929\u4f7f", "\u6218\u6597\u6cbb\u7597\u5e08", "\u6cbb\u7597\u5e08"],
    "Battle Ram": ["\u91ce\u86ee\u4eba\u653b\u57ce\u9524", "\u653b\u57ce\u69cc", "\u653b\u57ce\u9524"],
    "Berserker": ["\u72c2\u6218\u58eb"],
    "Bomb Tower": ["\u70b8\u5f39\u5854"],
    "Bomber": ["\u70b8\u5f39\u5175"],
    "Boss Bandit": ["\u523a\u5ba2\u5934\u9886", "\u9996\u9886\u5e7b\u5f71\u523a\u5ba2"],
    "Bowler": ["\u5de8\u77f3\u6295\u624b", "\u4fdd\u9f84\u7403\u624b"],
    "Cannon": ["\u52a0\u519c\u70ae"],
    "Cannon Cart": ["\u52a0\u519c\u70ae\u6218\u8f66"],
    "Cannoneer": ["\u52a0\u519c\u70ae\u624b"],
    "Clone": ["\u514b\u9686\u6cd5\u672f", "\u514b\u9686"],
    "Dagger Duchess": ["\u98de\u5200\u5973\u738b"],
    "Dark Prince": ["\u9ed1\u6697\u738b\u5b50", "\u9ed1\u738b"],
    "Dart Goblin": ["\u5439\u7bad\u54e5\u5e03\u6797", "\u5439\u7bad"],
    "Earthquake": ["\u5730\u9707\u6cd5\u672f", "\u5730\u9707"],
    "Electro Dragon": ["\u96f7\u7535\u98de\u9f99", "\u7535\u9f99"],
    "Electro Giant": ["\u96f7\u7535\u5de8\u4eba", "\u7535\u5de8", "e-giant", "egiant"],
    "Electro Spirit": ["\u96f7\u7535\u7cbe\u7075", "\u5c0f\u7535\u7cbe\u7075", "\u7535\u7cbe\u7075"],
    "Electro Wizard": ["\u95ea\u7535\u6cd5\u5e08", "\u7535\u6cd5\u5e08", "\u7535\u6cd5", "ewiz"],
    "Elite Barbarians": ["\u91ce\u86ee\u4eba\u7cbe\u9510", "\u86ee\u7cbe"],
    "Elixir Collector": ["\u5723\u6c34\u6536\u96c6\u5668", "\u5723\u6c34\u673a"],
    "Elixir Golem": ["\u5723\u6c34\u6208\u4ed1", "\u5723\u6c34\u77f3\u4eba"],
    "Executioner": ["\u98de\u65a7\u5c60\u592b", "\u5203\u5b50\u624b"],
    "Fire Spirit": ["\u70c8\u7130\u7cbe\u7075", "\u706b\u7cbe\u7075"],
    "Fireball": ["\u706b\u7403", "fb"],
    "Firecracker": ["\u70df\u82b1\u70ae\u624b", "\u70df\u82b1"],
    "Fisherman": ["\u6e14\u592b"],
    "Flying Machine": ["\u98de\u884c\u5668"],
    "Freeze": ["\u51b0\u51bb\u6cd5\u672f", "\u51b0\u51bb"],
    "Furnace": ["\u70c8\u7130\u7194\u7089"],
    "Giant": ["\u5de8\u4eba"],
    "Giant Skeleton": ["\u9ab7\u9ac5\u5de8\u4eba", "\u5de8\u4eba\u9ab7\u9ac5", "\u5de8\u578b\u9ab7\u9ac5"],
    "Giant Snowball": ["\u5927\u96ea\u7403", "\u5de8\u578b\u96ea\u7403"],
    "Goblin Barrel": ["\u54e5\u5e03\u6797\u98de\u6876", "\u98de\u6876"],
    "Goblin Cage": ["\u54e5\u5e03\u6797\u7262\u7b3c"],
    "Goblin Curse": ["\u54e5\u5e03\u6797\u8bc5\u5492"],
    "Goblin Demolisher": ["\u54e5\u5e03\u6797\u7206\u7834\u624b"],
    "Goblin Drill": ["\u54e5\u5e03\u6797\u94bb\u673a", "\u94bb\u673a"],
    "Goblin Gang": ["\u54e5\u5e03\u6797\u56e2\u4f19"],
    "Goblin Giant": ["\u54e5\u5e03\u6797\u5de8\u4eba"],
    "Goblin Hut": ["\u54e5\u5e03\u6797\u5c0f\u5c4b"],
    "Goblin Machine": ["\u54e5\u5e03\u6797\u673a\u7532"],
    "Goblins": ["\u54e5\u5e03\u6797"],
    "Goblinstein": ["\u54e5\u5e03\u6797\u65af\u5766"],
    "Golden Knight": ["\u9ec4\u91d1\u5723\u9a91", "\u9ec4\u91d1\u9a91\u58eb", "gk"],
    "Golem": ["\u6208\u4ed1\u77f3\u4eba", "\u77f3\u4eba"],
    "Graveyard": ["\u9ab7\u9ac5\u53ec\u5524", "\u5893\u56ed"],
    "Guards": ["\u9ab7\u9ac5\u5b88\u536b"],
    "Heal Spirit": ["\u6cbb\u7597\u7cbe\u7075"],
    "Hog Rider": ["\u91ce\u732a\u9a91\u58eb", "\u91ce\u732a", "hog"],
    "Hunter": ["\u730e\u4eba"],
    "Ice Golem": ["\u6208\u4ed1\u51b0\u4eba", "\u51b0\u6208\u4ed1", "\u51b0\u4eba"],
    "Ice Spirit": ["\u51b0\u96ea\u7cbe\u7075", "\u51b0\u7cbe\u7075"],
    "Ice Wizard": ["\u5bd2\u51b0\u6cd5\u5e08", "\u51b0\u6cd5\u5e08", "\u51b0\u6cd5"],
    "Inferno Dragon": ["\u5730\u72f1\u98de\u9f99", "\u5730\u72f1\u9f99"],
    "Inferno Tower": ["\u5730\u72f1\u4e4b\u5854", "\u5730\u72f1\u5854"],
    "Knight": ["\u9a91\u58eb"],
    "Lava Hound": ["\u7194\u5ca9\u730e\u72ac", "\u5929\u72d7"],
    "Lightning": ["\u96f7\u7535\u6cd5\u672f", "\u95ea\u7535", "\u5927\u7535"],
    "Little Prince": ["\u5c0f\u738b\u5b50", "lp"],
    "Lumberjack": ["\u72c2\u66b4\u6a35\u592b", "\u4f10\u6728\u5de5"],
    "Magic Archer": ["\u795e\u7bad\u6e38\u4fa0", "\u9b54\u6cd5\u795e\u7bad\u624b", "\u795e\u7bad"],
    "Mega Knight": ["\u8d85\u7ea7\u9a91\u58eb", "\u8d85\u9a91", "mk"],
    "Mega Minion": ["\u91cd\u7532\u4ea1\u7075", "\u91cd\u7532\u4ea1\u7075"],
    "Mighty Miner": ["\u5a01\u731b\u77ff\u5de5", "\u673a\u7532\u77ff\u5de5", "\u5f3a\u529b\u77ff\u5de5"],
    "Miner": ["\u77ff\u5de5", "\u6398\u5730\u77ff\u5de5"],
    "Mini P.E.K.K.A": ["\u8ff7\u4f60\u76ae\u5361", "\u5c0f\u76ae\u5361", "mini pekka"],
    "Minion Horde": ["\u4ea1\u7075\u5927\u519b"],
    "Minions": ["\u4ea1\u7075"],
    "Mirror": ["\u955c\u50cf\u6cd5\u672f", "\u955c\u50cf"],
    "Monk": ["\u76d6\u4e16\u6b66\u50e7", "\u6b66\u50e7"],
    "Mortar": ["\u8feb\u51fb\u70ae"],
    "Mother Witch": ["\u5973\u5deb\u5a46\u5a46", "\u6bcd\u5deb", "\u8001\u5deb\u5a46"],
    "Musketeer": ["\u706b\u67aa\u624b"],
    "Night Witch": ["\u6697\u591c\u5973\u5deb"],
    "P.E.K.K.A": ["\u76ae\u5361\u8d85\u4eba", "\u5927\u76ae\u5361", "pekka"],
    "Phoenix": ["\u51e4\u51f0"],
    "Poison": ["\u6bd2\u836f\u6cd5\u672f", "\u6bd2\u836f"],
    "Prince": ["\u738b\u5b50"],
    "Princess": ["\u516c\u4e3b"],
    "Rage": ["\u72c2\u66b4"],
    "Ram Rider": ["\u86ee\u7f8a\u9a91\u58eb", "\u653b\u57ce\u69cc\u9a91\u58eb", "\u653b\u57ce\u9524\u9a91\u58eb"],
    "Rascals": ["\u7eff\u6797\u56e2\u4f19", "\u6dd8\u6c14\u4e09\u4eba\u7ec4"],
    "Rocket": ["\u706b\u7bad"],
    "Royal Chef": ["\u7687\u5bb6\u4e3b\u53a8"],
    "Royal Delivery": ["\u7687\u5bb6\u901f\u9012"],
    "Royal Ghost": ["\u7687\u5bb6\u5e7d\u7075", "\u7687\u5e7d"],
    "Royal Giant": ["\u7687\u5bb6\u5de8\u4eba", "\u7687\u5de8", "rg"],
    "Royal Hogs": ["\u7687\u5bb6\u91ce\u732a"],
    "Royal Recruits": ["\u7687\u5bb6\u536b\u961f"],
    "Ronin": ["\u6d6a\u4eba", "\u6d6a\u5ba2", "\u6d6a\u4eba\u6b66\u58eb"],
    "Rune Giant": ["\u7b26\u6587\u5de8\u4eba"],
    "Skeleton Army": ["\u9ab7\u9ac5\u519b\u56e2", "\u9ab7\u9ac5\u6d77"],
    "Skeleton Barrel": ["\u9ab7\u9ac5\u6c14\u7403"],
    "Skeleton Dragons": ["\u9ab7\u9ac5\u98de\u9f99"],
    "Skeleton King": ["\u9ab7\u9ac5\u5e1d\u738b", "\u9ab7\u9ac5\u738b", "sk"],
    "Skeletons": ["\u9ab7\u9ac5\u5175", "\u5c0f\u9ab7\u9ac5"],
    "Sparky": ["\u7535\u78c1\u70ae", "\u5927\u7535\u78c1\u70ae"],
    "Spear Goblins": ["\u54e5\u5e03\u6797\u6295\u77db\u624b"],
    "Spirit Empress": ["\u7cbe\u7075\u5973\u7687", "\u7075\u9b42\u5973\u7687"],
    "Suspicious Bush": ["\u53ef\u7591\u8349\u4e1b"],
    "Tesla": ["\u7279\u65af\u62c9\u7535\u78c1\u5854", "\u7535\u78c1\u5854"],
    "The Log": ["\u590d\u4ec7\u6eda\u6728", "\u6eda\u6728"],
    "Three Musketeers": ["\u4e09\u4e2a\u706b\u67aa\u624b", "3m"],
    "Tombstone": ["\u9ab7\u9ac5\u5893\u7891", "\u5893\u7891"],
    "Tornado": ["\u98d3\u98ce\u6cd5\u672f", "\u98d3\u98ce", "\u9f99\u5377\u98ce"],
    "Tower Princess": ["\u5854\u697c\u516c\u4e3b", "\u516c\u4e3b\u5854"],
    "Valkyrie": ["\u74e6\u57fa\u4e3d\u6b66\u795e", "\u5973\u6b66\u795e"],
    "Vines": ["\u85e4\u8513\u6cd5\u672f", "\u85e4\u8513"],
    "Void": ["\u865a\u7a7a\u6cd5\u672f", "\u865a\u7a7a"],
    "Wall Breakers": ["\u653b\u57ce\u70b8\u5f39\u4eba", "\u7834\u5899\u8005"],
    "Witch": ["\u5973\u5deb"],
    "Wizard": ["\u6cd5\u5e08"],
    "X-Bow": ["X\u8fde\u5f29", "\u8fde\u5f29", "xbow", "x-bow"],
    "Zap": ["\u7535\u51fb\u6cd5\u672f", "\u7535\u51fb", "\u5c0f\u7535", "zap"],
    "Zappies": ["\u7535\u51fb\u8f66\u5c0f\u961f", "\u7535\u51fb\u5c0f\u961f"],
}


# Community terminology is intentionally kept separate from official names.
# Each entry is reviewed against the community terminology references recorded
# in docs/card_aliases.md.  Short aliases which are known to be ambiguous are
# deliberately excluded (for example, \u5c0f\u7535 belongs to Zap, not Electro Spirit).
CARD_COMMUNITY_ALIASES = {
    "Archer Queen": ["\u5f13\u7687", "\u5973\u738b", "archerqueen"],
    "Archers": ["\u5f13\u624b", "\u5f13\u7bad\u59b9\u59b9", "archers"],
    "Arrows": ["\u4e07\u7bad", "\u7bad", "arrow"],
    "Baby Dragon": ["\u9f99\u5b9d", "\u5c0f\u98de\u9f99", "babydragon"],
    "Balloon": ["\u6c14\u7403\u54e5", "\u6c14\u7403\u70ae", "looner"],
    "Bandit": ["\u5e7b\u523a", "\u5e7b\u5f71\u523a\u5ba2", "bandit"],
    "Barbarian Barrel": ["\u86ee\u6876", "\u91ce\u6876", "barbbarrel"],
    "Barbarian Hut": ["\u86ee\u5c4b", "\u86ee\u4eba\u623f", "barbhut"],
    "Barbarians": ["\u86ee\u5b50", "\u86ee\u4eba", "barbs"],
    "Bats": ["\u5c0f\u8759\u8760", "\u8760\u8759", "bats"],
    "Battle Healer": ["\u5976\u5988", "\u5976\u6cbb", "healer"],
    "Battle Ram": ["\u86ee\u9524", "\u653b\u57ce\u9524", "battleram"],
    "Berserker": ["\u72c2\u6218", "\u72c2\u6218\u58eb", "berserk"],
    "Bomb Tower": ["\u70b8\u5854", "\u70b8\u5f39\u5854", "\u70b8\u5f39\u9632\u5fa1\u5854", "bombtower"],
    "Bomber": ["\u70b8\u5f39\u4eba", "\u6295\u5f39\u5175", "bomber"],
    "Boss Bandit": ["\u9996\u9886\u523a\u5ba2", "\u9996\u9886\u5e7b\u523a", "bossbandit"],
    "Bowler": ["\u6eda\u77f3\u4eba", "\u63a8\u7403\u54e5", "bowler"],
    "Cannon": ["\u5c0f\u70ae", "\u52a0\u519c", "cannon"],
    "Cannon Cart": ["\u70ae\u8f66", "\u52a0\u519c\u70ae\u8f66", "cannoncart"],
    "Cannoneer": ["\u70ae\u624b", "\u5c0f\u70ae\u624b", "cannoneer"],
    "Clone": ["\u5206\u8eab", "\u514b\u9686\u672f", "clone"],
    "Dagger Duchess": ["\u98de\u5200\u5854", "\u5200\u5973\u738b", "daggerduchess"],
    "Dark Prince": ["\u9ed1\u738b\u5b50", "\u9ed1\u9a91", "darkprince"],
    "Dart Goblin": ["\u5439\u7bad", "\u5439\u7bad\u54e5\u5e03\u6797", "\u5439\u7bad\u54e5", "dartgoblin"],
    "Earthquake": ["\u5730\u9707\u672f", "\u5927\u5730\u9707", "eq"],
    "Electro Dragon": ["\u7535\u9f99", "\u95ea\u7535\u9f99", "edrag"],
    "Electro Giant": ["\u7535\u5de8", "\u96f7\u5de8", "egiant"],
    "Electro Spirit": ["\u7535\u7cbe", "\u7535\u7075", "espirit"],
    "Electro Wizard": ["\u7535\u6cd5", "\u95ea\u7535\u6cd5\u5e08", "ewiz"],
    "Elite Barbarians": ["\u86ee\u7cbe", "\u7cbe\u9510\u86ee", "ebarbs"],
    "Elixir Collector": ["\u5723\u6c34\u673a", "\u91c7\u96c6\u5668", "pump"],
    "Elixir Golem": ["\u5723\u6c34\u77f3\u4eba", "\u5723\u6c34\u6208\u4ed1", "egolem"],
    "Executioner": ["\u5203\u5b50", "\u98de\u65a7", "exe"],
    "Fire Spirit": ["\u706b\u7075", "\u5c0f\u706b\u4eba", "fispirit"],
    "Fireball": ["\u5927\u706b\u7403", "fb", "fireball"],
    "Firecracker": ["\u70df\u82b1", "\u70ae\u59d0", "fc"],
    "Fisherman": ["\u8001\u6e14", "\u94a9\u5b50", "fisherman"],
    "Flying Machine": ["\u98de\u673a", "\u98de\u884c\u5668", "\u98de\u884c\u673a\u5668", "flyingmachine"],
    "Freeze": ["\u51b0\u51bb\u672f", "\u5927\u51b0", "freeze"],
    "Furnace": ["\u7089\u5b50", "\u70c8\u7089", "furnace"],
    "Giant": ["\u5927\u4e2a\u5b50", "\u5927\u5de8\u4eba", "giant"],
    "Giant Skeleton": ["\u5927\u9ab7\u9ac5", "\u9ab7\u9ac5\u5de8\u4eba", "giantskeleton"],
    "Giant Snowball": ["\u5927\u96ea\u7403", "\u96ea\u7403", "snowball"],
    "Goblin Barrel": ["\u98de\u6876", "\u5168\u5bb6\u6876", "gbarrel"],
    "Goblin Cage": ["\u54e5\u7b3c", "\u7262\u7b3c", "gcage"],
    "Goblin Curse": ["\u54e5\u5e03\u6797\u8bc5\u5492", "\u8bc5\u5492", "gcurse"],
    "Goblin Demolisher": ["\u7206\u7834\u54e5\u5e03\u6797", "\u7206\u7834\u624b", "demolisher"],
    "Goblin Drill": ["\u94bb\u673a", "\u54e5\u94bb", "gdrill"],
    "Goblin Gang": ["\u54e5\u5e03\u6797\u56e2", "\u54e5\u5e03\u6797\u5e2e", "ggang"],
    "Goblin Giant": ["\u54e5\u5de8", "\u54e5\u5e03\u6797\u5927\u4e2a", "ggiant"],
    "Goblin Hut": ["\u54e5\u5c4b", "\u54e5\u5e03\u6797\u623f", "ghut"],
    "Goblin Machine": ["\u54e5\u5e03\u6797\u673a\u7532", "\u54e5\u673a", "gmachine"],
    "Goblins": ["\u5c0f\u54e5\u5e03\u6797", "\u5c0f\u54e5", "gobs"],
    "Goblinstein": ["\u54e5\u5e03\u6797\u535a\u58eb", "\u54e5\u65af\u5766", "goblinstein"],
    "Golden Knight": ["\u91d1\u9a91", "\u91d1\u7532\u9a91\u58eb", "gk"],
    "Golem": ["\u77f3\u4eba", "\u5927\u77f3\u4eba", "golem"],
    "Graveyard": ["\u5893\u5730", "\u5893\u56ed\u6cd5\u672f", "gy"],
    "Guards": ["\u76fe\u9ab7\u9ac5", "\u9ab7\u9ac5\u536b\u58eb", "guards"],
    "Heal Spirit": ["\u5976\u7cbe", "\u6cbb\u7597\u7cbe\u7075", "hspirit"],
    "Hog Rider": ["\u91ce\u732a", "\u732a", "hog"],
    "Hunter": ["\u730e\u4eba", "\u731b\u7537", "\u730e\u67aa", "hunter"],
    "Ice Golem": ["\u51b0\u4eba", "\u51b0\u77f3", "igolem"],
    "Ice Spirit": ["\u51b0\u7075", "\u5c0f\u51b0\u4eba", "ispirit"],
    "Ice Wizard": ["\u51b0\u6cd5", "\u51b0\u6cd5\u5e08", "iwiz"],
    "Inferno Dragon": ["\u5730\u72f1\u9f99", "\u7164\u6c14\u9f99", "idrag"],
    "Inferno Tower": ["\u5730\u72f1\u5854", "\u5730\u72f1\u4e4b\u5854", "itower"],
    "Knight": ["\u5c0f\u9a91\u58eb", "\u7cbe\u9500\u9a91\u58eb", "knight"],
    "Lava Hound": ["\u5929\u72d7", "\u5ca9\u72ac", "lavahound"],
    "Lightning": ["\u5927\u7535", "\u96f7\u7535", "lightning"],
    "Little Prince": ["\u5c0f\u738b", "\u5c0f\u738b\u5b50", "lp"],
    "Lumberjack": ["\u4f10\u6728", "\u4f10\u6728\u54e5", "lj"],
    "Magic Archer": ["\u795e\u7bad", "\u8001\u9ad8", "marcher"],
    "Mega Knight": ["\u8d85\u9a91", "\u8d85\u7ea7\u9a91\u58eb", "mk"],
    "Mega Minion": ["\u91cd\u7532\u4ea1\u7075", "\u94c1\u82cd", "mminion"],
    "Mighty Miner": ["\u5f3a\u529b\u77ff\u5de5", "\u673a\u7532\u77ff\u5de5", "mm"],
    "Miner": ["\u77ff\u5de5", "\u5c0f\u77ff", "\u6316\u77ff\u5de5", "miner"],
    "Mini P.E.K.K.A": ["\u5c0f\u76ae\u5361", "\u8ff7\u4f60\u76ae\u5361", "\u5c0f\u76ae", "minipekka"],
    "Minion Horde": ["\u4ea1\u7075\u6d77", "\u4ea1\u7075\u5927\u519b", "\u82cd\u8747\u6d77", "minionhorde"],
    "Minions": ["\u5c0f\u4ea1\u7075", "\u4ea1\u7075", "\u5c0f\u82cd\u8747", "minions"],
    "Mirror": ["\u955c\u50cf", "\u590d\u5236", "\u955c\u5b50", "mirror"],
    "Monk": ["\u6b66\u50e7", "\u548c\u5c1a", "\u548c\u5c1a\u54e5", "monk"],
    "Mortar": ["\u8feb\u51fb\u70ae", "\u70ae\u51fb", "\u8feb\u70ae", "mortar"],
    "Mother Witch": ["\u6bcd\u5deb", "\u8001\u5deb\u5a46", "mw"],
    "Musketeer": ["\u5973\u67aa", "\u706b\u67aa", "musketeer"],
    "Night Witch": ["\u591c\u5deb", "\u9ed1\u5deb", "nw"],
    "P.E.K.K.A": ["\u5927\u76ae\u5361", "\u76ae\u59d0", "pekka"],
    "Phoenix": ["\u51e4\u51f0", "\u51e4\u51f0\u9e1f", "\u4e0d\u6b7b\u9e1f", "phoenix"],
    "Poison": ["\u6bd2\u836f", "\u6bd2", "\u6bd2\u6cd5", "poison"],
    "Prince": ["\u767d\u738b", "\u767d\u9a91", "prince"],
    "Princess": ["\u516c\u4e3b", "\u5c0f\u516c\u4e3b", "\u516c\u4e3b\u59b9\u59b9", "princess"],
    "Rage": ["\u72c2\u66b4", "\u72c2\u66b4\u672f", "\u72c2\u66b4\u6cd5\u672f", "rage"],
    "Ram Rider": ["\u7f8a\u9a91", "\u653b\u57ce\u69cc\u9a91\u58eb", "\u7f8a\u9a91\u58eb", "ramrider"],
    "Rascals": ["\u6dd8\u6c14\u4e09\u4eba\u7ec4", "\u4e09\u4eba\u7ec4", "\u6dd8\u6c14\u4e09\u4eba", "rascals"],
    "Rocket": ["\u706b\u7bad", "\u5927\u706b\u7bad", "\u706b\u7bad\u672f", "rocket"],
    "Royal Chef": ["\u7687\u5bb6\u53a8\u5e08", "\u53a8\u5e08\u5854", "chef"],
    "Royal Delivery": ["\u7687\u5bb6\u5feb\u9012", "\u5feb\u9012", "delivery"],
    "Royal Ghost": ["\u7687\u9b3c", "\u7687\u5bb6\u9b3c\u9b42", "ghost"],
    "Royal Giant": ["\u7687\u5de8", "\u7687\u5bb6\u5de8\u4eba", "rg"],
    "Royal Hogs": ["\u7687\u732a", "\u7687\u5bb6\u732a", "rhogs"],
    "Royal Recruits": ["\u7687\u5bb6\u536b\u961f", "\u56fd\u738b\u5b88\u536b", "recruits"],
    "Rune Giant": ["\u7b26\u6587\u5de8\u4eba", "\u7b26\u6587\u77f3\u4eba", "\u7b26\u6587\u5927\u4e2a", "runegiant"],
    "Skeleton Army": ["\u9ab7\u9ac5\u6d77", "\u9ab7\u9ac5\u519b\u56e2", "skarmy"],
    "Skeleton Barrel": ["\u9ab7\u9ac5\u6876", "\u9ab7\u9ac5\u6c14\u7403", "sbarrel"],
    "Skeleton Dragons": ["\u9ab7\u9ac5\u9f99", "\u53cc\u9f99", "sdragons"],
    "Skeleton King": ["\u9ab7\u738b", "\u9ab7\u9ac5\u738b", "sk"],
    "Skeletons": ["\u5c0f\u9ab7\u9ac5", "\u9ab7\u9ac5\u5175", "\u9ab7\u9ac5", "skeles"],
    "Sparky": ["\u7535\u78c1\u70ae", "\u5927\u7535\u78c1\u70ae", "\u7535\u70ae", "sparky"],
    "Spear Goblins": ["\u54e5\u5e03\u6797\u77db\u624b", "\u957f\u77db\u54e5\u5e03\u6797", "sgobs"],
    "Spirit Empress": ["\u7075\u9b42\u5973\u7687", "\u7075\u540e", "\u7075\u9b42\u7687\u540e", "spiritempress"],
    "Suspicious Bush": ["\u53ef\u7591\u8349", "\u8349\u4e1b", "bush"],
    "Tesla": ["\u7535\u5854", "\u7535\u78c1\u5854", "tesla"],
    "The Log": ["\u6eda\u6728", "\u5c0f\u6728\u5934", "log"],
    "Three Musketeers": ["\u4e09\u67aa", "3\u67aa", "3m"],
    "Tombstone": ["\u5893\u7891", "\u9ab7\u9ac5\u7891", "\u575f\u7891", "tombstone"],
    "Tornado": ["\u9f99\u5377\u98ce", "\u98d3\u98ce", "nado"],
    "Tower Princess": ["\u516c\u4e3b\u5854", "\u5854\u5a18", "towerprincess"],
    "Valkyrie": ["\u5973\u6b66\u795e", "\u8f6c\u5708\u59d0", "valk"],
    "Vines": ["\u85e4\u8513", "\u7f20\u7ed5\u85e4", "\u85e4\u6761", "vines"],
    "Void": ["\u865a\u7a7a", "\u7a7a\u95f4\u6cd5\u672f", "\u865a\u7a7a\u672f", "void"],
    "Wall Breakers": ["\u7834\u5899", "\u7206\u5f39\u4eba", "wallbreakers"],
    "Witch": ["\u5973\u5deb", "\u5deb\u5a46", "\u666e\u901a\u5973\u5deb", "witch"],
    "Wizard": ["\u6cd5\u5e08", "\u706b\u6cd5", "wiz"],
    "X-Bow": ["\u8fde\u5f29", "\u52aa", "xbow"],
    "Zap": ["\u5c0f\u7535", "\u5c0f\u95ea", "zap"],
    "Zappies": ["\u7535\u51fb\u5c0f\u961f", "\u7535\u8f66", "zappies"],
}


CARD_ALIAS_DATA_PATH = Path(__file__).resolve().parent / "data" / "card_aliases.zh-CN.json"


def _load_editable_card_aliases() -> set[str]:
    """Load the human-editable display names and free-question aliases."""
    if not CARD_ALIAS_DATA_PATH.exists():
        return set()
    try:
        payload = json.loads(CARD_ALIAS_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read card alias data: {CARD_ALIAS_DATA_PATH}") from exc
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if payload.get("schema_version") != 1 or not isinstance(cards, dict):
        raise RuntimeError(f"invalid card alias data schema: {CARD_ALIAS_DATA_PATH}")

    loaded: set[str] = set()
    for canonical, record in cards.items():
        if not isinstance(canonical, str) or not canonical.strip() or not isinstance(record, dict):
            raise RuntimeError(f"invalid card alias entry in {CARD_ALIAS_DATA_PATH}")
        display_name = str(record.get("display_name") or "").strip()
        aliases = record.get("aliases", [])
        if not display_name or not isinstance(aliases, list) or not all(isinstance(value, str) for value in aliases):
            raise RuntimeError(f"invalid card alias entry for {canonical}: {CARD_ALIAS_DATA_PATH}")
        values = [display_name, *(value.strip() for value in aliases if value.strip())]
        CARD_ALIAS_OVERRIDES[canonical] = list(dict.fromkeys(values))
        loaded.add(canonical)
    return loaded


EDITABLE_CARD_ALIAS_NAMES = _load_editable_card_aliases()


# The daily battle sample is intentionally not a card catalogue: a legal card
# can have zero appearances in one 20,000-battle window. Keep the parser's
# canonical forms independent from mutable statistics so aliases still resolve
# to a clear "not observed in this snapshot" result instead of another card.
EVOLUTION_BASE_NAMES = (
    "Archers", "Baby Dragon", "Barbarians", "Battle Ram", "Bats", "Bomber",
    "Cannon", "Dart Goblin", "Electro Dragon", "Executioner", "Firecracker",
    "Furnace", "Giant Snowball", "Goblin Barrel", "Goblin Cage", "Goblin Drill",
    "Goblin Giant", "Hunter", "Ice Spirit", "Inferno Dragon", "Knight",
    "Lumberjack", "Mega Knight", "Minion Horde", "Mortar", "Musketeer",
    "P.E.K.K.A", "Royal Giant", "Royal Ghost", "Royal Hogs", "Royal Recruits",
    "Skeleton Army", "Skeleton Barrel", "Skeletons", "Tesla", "Valkyrie",
    "Wall Breakers", "Witch", "Wizard", "Zap",
)
HERO_BASE_NAMES = (
    "Balloon", "Barbarian Barrel", "Giant", "Goblins", "Ice Golem", "Knight",
    "Magic Archer", "Mega Minion", "Mini P.E.K.K.A", "Musketeer", "Wizard",
)
CARD_FORM_CATALOG = tuple(
    [f"{name} Evolution" for name in EVOLUTION_BASE_NAMES]
    + [f"Hero {name}" for name in HERO_BASE_NAMES]
)


_CARD_ALIAS_RESOLVER = CardAliasResolver(
    aliases=CARD_ALIASES,
    overrides=CARD_ALIAS_OVERRIDES,
    community_aliases=CARD_COMMUNITY_ALIASES,
    editable_names=EDITABLE_CARD_ALIAS_NAMES,
    form_catalog=CARD_FORM_CATALOG,
)
build_card_aliases = _CARD_ALIAS_RESOLVER.build_card_aliases
_card_catalog_key = _CARD_ALIAS_RESOLVER.card_catalog_key
_build_card_aliases = _CARD_ALIAS_RESOLVER.build_aliases
_card_alias_patterns = _CARD_ALIAS_RESOLVER.alias_patterns
resolve_card_name = _CARD_ALIAS_RESOLVER.resolve_card_name
resolve_card_names = _CARD_ALIAS_RESOLVER.resolve_card_names

def detect_entity_reference(question: str, cards_meta_data: list[dict]) -> dict:
    return detect_packaged_entity_reference(
        question, cards_meta_data, resolve_card_name
    )

def is_meta_analysis_query(question: str) -> bool:
    return is_packaged_meta_analysis_query(question, resolve_card_name)


def is_card_query(question: str, cards_meta_data: list[dict]) -> bool:
    return is_packaged_card_query(question, cards_meta_data, resolve_card_name)


def is_card_compare_query(question: str, cards_meta_data: list[dict]) -> bool:
    return is_packaged_card_compare_query(
        question, cards_meta_data, resolve_card_names
    )


def is_card_cooccurrence_query(question: str, cards_meta_data: list[dict]) -> bool:
    return is_packaged_card_cooccurrence_query(
        question, cards_meta_data, resolve_card_names
    )


def is_card_rank_lookup_query(question: str, cards_meta_data: list[dict]) -> bool:
    return is_packaged_card_rank_lookup_query(
        question, cards_meta_data, resolve_card_name
    )


def infer_local_parse_metadata(parsed: dict, question: str) -> dict:
    dependencies = LocalParseMetadataDependencies(
        is_meta_analysis_query=is_meta_analysis_query,
        is_card_cooccurrence_query=is_card_cooccurrence_query,
    )
    return infer_packaged_local_parse_metadata(parsed, question, dependencies)


def _fallback_parse_dependencies() -> FallbackParseDependencies:
    return FallbackParseDependencies(
        is_schedule_summary_query=is_schedule_summary_query,
        is_match_preparation_query=is_match_preparation_query,
        is_meta_analysis_query=is_meta_analysis_query,
        is_card_cooccurrence_query=is_card_cooccurrence_query,
        is_card_compare_query=is_card_compare_query,
        is_card_rank_lookup_query=is_card_rank_lookup_query,
        is_schedule_query=is_schedule_query,
        is_deck_query=is_deck_query,
        is_card_query=is_card_query,
        resolve_card_name=resolve_card_name,
        resolve_card_names=resolve_card_names,
        get_metric=get_metric,
        extract_rank_target=extract_rank_target,
        extract_top_n=extract_top_n,
        extract_round_number=extract_round_number,
        extract_date=extract_date,
        is_card_ranking_query=is_card_ranking_query,
        has_explicit_top_n_signal=has_explicit_top_n_signal,
        normalize_metrics=normalize_metrics,
        is_asking_players=is_asking_players,
        is_meta_delta_query=is_meta_delta_query,
        detect_entity_reference=detect_entity_reference,
        merge_parse_metadata=merge_parse_metadata,
        infer_local_parse_metadata=infer_local_parse_metadata,
    )


def fallback_parse_query(question: str, cards_meta_data: list[dict]) -> dict:
    return fallback_packaged_parse_query(
        question,
        cards_meta_data,
        _fallback_parse_dependencies(),
    )


def normalize_parsed_query(parsed: dict, question: str, cards_meta_data: list[dict]) -> dict:
    """Validate model parser output through the packaged normalization boundary."""
    dependencies = ParserNormalizationDependencies(
        fallback_parse_query=fallback_parse_query,
        resolve_card_name=resolve_card_name,
        resolve_card_names=resolve_card_names,
        is_asking_players=is_asking_players,
        is_meta_delta_query=is_meta_delta_query,
        is_card_ranking_query=is_card_ranking_query,
        has_explicit_top_n_signal=has_explicit_top_n_signal,
        detect_entity_reference=detect_entity_reference,
    )
    return normalize_packaged_parsed_query(
        parsed, question, cards_meta_data, dependencies
    )



def _multi_intent_dependencies() -> MultiIntentDependencies:
    return MultiIntentDependencies(
        fallback_parse_query=fallback_parse_query,
        resolve_card_names=resolve_card_names,
        extract_metrics=extract_metrics,
        is_card_compare_query=is_card_compare_query,
        is_card_rank_lookup_query=is_card_rank_lookup_query,
        is_card_ranking_query=is_card_ranking_query,
        subquery_semantic_key=subquery_semantic_key,
        has_explicit_rank_signal=has_explicit_rank_signal,
        has_explicit_top_n_signal=has_explicit_top_n_signal,
        make_multi_intent_result=make_multi_intent_result,
        normalize_parsed_query=normalize_parsed_query,
    )


def fallback_parse_multi_intent(question: str, cards_meta_data: list[dict]) -> dict:
    return fallback_packaged_multi_intent(
        question,
        cards_meta_data,
        _multi_intent_dependencies(),
    )

def normalize_multi_intent_query(parsed: dict, question: str, cards_meta_data: list[dict]) -> dict:
    return normalize_packaged_multi_intent_query(
        parsed,
        question,
        cards_meta_data,
        _multi_intent_dependencies(),
    )
