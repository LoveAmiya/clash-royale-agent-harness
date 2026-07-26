"""将自由表达的玩家问题转换为经过校验的路由字段。

解析器优先使用兼容 LLM 的 JSON 契约，同时保留本地归一化和兜底规则作为确定性
安全网。因此 Router 消费的是标准卡名、范围受限的排名和已知意图，而不是原始自然语言。
"""

import json
import re
from functools import lru_cache
from typing import Any


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
    "Arrows": ["\u7bad\u96e8"],
    "Baby Dragon": ["\u98de\u9f99\u5b9d\u5b9d", "\u5b9d\u5b9d\u9f99", "\u7eff\u9f99", "\u9752\u9f99", "\u9f99\u5b9d"],
    "Balloon": ["\u6c14\u7403\u5175", "\u6c14\u7403"],
    "Bandit": ["\u5e7b\u5f71\u523a\u5ba2", "\u523a\u5ba2"],
    "Barbarian Barrel": ["\u91ce\u86ee\u4eba\u6eda\u6876", "\u86ee\u6876", "\u6eda\u6876"],
    "Barbarian Hut": ["\u91ce\u86ee\u4eba\u5c0f\u5c4b"],
    "Barbarians": ["\u91ce\u86ee\u4eba", "\u86ee\u4eba"],
    "Bats": ["\u8759\u8760"],
    "Battle Healer": ["\u6218\u6597\u6cbb\u7597\u5e08", "\u6cbb\u7597\u5e08"],
    "Battle Ram": ["\u653b\u57ce\u69cc"],
    "Berserker": ["\u72c2\u6218\u58eb"],
    "Bomb Tower": ["\u70b8\u5f39\u5854"],
    "Bomber": ["\u70b8\u5f39\u5175"],
    "Boss Bandit": ["\u9996\u9886\u5e7b\u5f71\u523a\u5ba2"],
    "Bowler": ["\u98de\u6876\u54e5\u5e03\u6797", "\u4fdd\u9f84\u7403\u624b"],
    "Cannon": ["\u52a0\u519c\u70ae"],
    "Cannon Cart": ["\u52a0\u519c\u70ae\u6218\u8f66"],
    "Cannoneer": ["\u52a0\u519c\u70ae\u624b"],
    "Clone": ["\u514b\u9686", "\u514b\u9686\u6cd5\u672f"],
    "Dagger Duchess": ["\u98de\u5200\u5973\u738b"],
    "Dark Prince": ["\u9ed1\u6697\u738b\u5b50", "\u9ed1\u738b"],
    "Dart Goblin": ["\u5439\u7bad\u54e5\u5e03\u6797", "\u5439\u7bad"],
    "Earthquake": ["\u5730\u9707"],
    "Electro Dragon": ["\u96f7\u7535\u98de\u9f99", "\u7535\u9f99"],
    "Electro Giant": ["\u96f7\u7535\u5de8\u4eba", "\u7535\u5de8", "e-giant", "egiant"],
    "Electro Spirit": ["\u96f7\u7535\u7cbe\u7075", "\u5c0f\u7535\u7cbe\u7075", "\u7535\u7cbe\u7075"],
    "Electro Wizard": ["\u7535\u6cd5\u5e08", "\u7535\u6cd5", "ewiz"],
    "Elite Barbarians": ["\u91ce\u86ee\u4eba\u7cbe\u9510", "\u86ee\u7cbe"],
    "Elixir Collector": ["\u5723\u6c34\u6536\u96c6\u5668", "\u5723\u6c34\u673a"],
    "Elixir Golem": ["\u5723\u6c34\u6208\u4ed1", "\u5723\u6c34\u77f3\u4eba"],
    "Executioner": ["\u5203\u5b50\u624b"],
    "Fire Spirit": ["\u706b\u7cbe\u7075"],
    "Fireball": ["\u706b\u7403", "fb"],
    "Firecracker": ["\u70df\u82b1\u70ae\u624b", "\u70df\u82b1"],
    "Fisherman": ["\u6e14\u592b"],
    "Flying Machine": ["\u98de\u884c\u5668"],
    "Freeze": ["\u51b0\u51bb"],
    "Furnace": ["\u70c8\u7130\u7194\u7089"],
    "Giant": ["\u5de8\u4eba"],
    "Giant Skeleton": ["\u5de8\u4eba\u9ab7\u9ac5"],
    "Giant Snowball": ["\u5de8\u578b\u96ea\u7403", "\u5927\u96ea\u7403"],
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
    "Golden Knight": ["\u9ec4\u91d1\u9a91\u58eb", "gk"],
    "Golem": ["\u6208\u4ed1\u77f3\u4eba", "\u77f3\u4eba"],
    "Graveyard": ["\u5893\u56ed"],
    "Guards": ["\u9ab7\u9ac5\u5b88\u536b"],
    "Heal Spirit": ["\u6cbb\u7597\u7cbe\u7075"],
    "Hog Rider": ["\u91ce\u732a\u9a91\u58eb", "\u91ce\u732a", "hog"],
    "Hunter": ["\u730e\u4eba"],
    "Ice Golem": ["\u51b0\u6208\u4ed1", "\u51b0\u4eba"],
    "Ice Spirit": ["\u51b0\u7cbe\u7075"],
    "Ice Wizard": ["\u51b0\u6cd5\u5e08", "\u51b0\u6cd5"],
    "Inferno Dragon": ["\u5730\u72f1\u98de\u9f99", "\u5730\u72f1\u9f99"],
    "Inferno Tower": ["\u5730\u72f1\u4e4b\u5854", "\u5730\u72f1\u5854"],
    "Knight": ["\u9a91\u58eb"],
    "Lava Hound": ["\u7194\u5ca9\u730e\u72ac", "\u5929\u72d7"],
    "Lightning": ["\u95ea\u7535", "\u5927\u7535"],
    "Little Prince": ["\u5c0f\u738b\u5b50", "lp"],
    "Lumberjack": ["\u4f10\u6728\u5de5"],
    "Magic Archer": ["\u9b54\u6cd5\u795e\u7bad\u624b", "\u795e\u7bad"],
    "Mega Knight": ["\u8d85\u7ea7\u9a91\u58eb", "\u8d85\u9a91", "mk"],
    "Mega Minion": ["\u91cd\u7532\u4ea1\u7075", "\u91cd\u7532\u4ea1\u7075"],
    "Mighty Miner": ["\u673a\u7532\u77ff\u5de5", "\u5f3a\u529b\u77ff\u5de5"],
    "Miner": ["\u77ff\u5de5"],
    "Mini P.E.K.K.A": ["\u8ff7\u4f60\u76ae\u5361", "\u5c0f\u76ae\u5361", "mini pekka"],
    "Minion Horde": ["\u4ea1\u7075\u5927\u519b"],
    "Minions": ["\u4ea1\u7075"],
    "Mirror": ["\u955c\u50cf"],
    "Monk": ["\u6b66\u50e7"],
    "Mortar": ["\u8feb\u51fb\u70ae"],
    "Mother Witch": ["\u6bcd\u5deb", "\u8001\u5deb\u5a46"],
    "Musketeer": ["\u706b\u67aa\u624b"],
    "Night Witch": ["\u6697\u591c\u5973\u5deb"],
    "P.E.K.K.A": ["\u76ae\u5361\u8d85\u4eba", "\u5927\u76ae\u5361", "pekka"],
    "Phoenix": ["\u51e4\u51f0"],
    "Poison": ["\u6bd2\u836f"],
    "Prince": ["\u738b\u5b50"],
    "Princess": ["\u516c\u4e3b"],
    "Rage": ["\u72c2\u66b4"],
    "Ram Rider": ["\u653b\u57ce\u69cc\u9a91\u58eb"],
    "Rascals": ["\u6dd8\u6c14\u4e09\u4eba\u7ec4"],
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
    "Skeleton King": ["\u9ab7\u9ac5\u738b", "sk"],
    "Skeletons": ["\u9ab7\u9ac5\u5175", "\u5c0f\u9ab7\u9ac5"],
    "Sparky": ["\u7535\u78c1\u70ae", "\u5927\u7535\u78c1\u70ae"],
    "Spear Goblins": ["\u54e5\u5e03\u6797\u6295\u77db\u624b"],
    "Spirit Empress": ["\u7075\u9b42\u5973\u7687"],
    "Suspicious Bush": ["\u53ef\u7591\u8349\u4e1b"],
    "Tesla": ["\u7279\u65af\u62c9\u7535\u78c1\u5854", "\u7535\u78c1\u5854"],
    "The Log": ["\u6eda\u6728"],
    "Three Musketeers": ["\u4e09\u4e2a\u706b\u67aa\u624b", "3m"],
    "Tombstone": ["\u5893\u7891"],
    "Tornado": ["\u98d3\u98ce", "\u9f99\u5377\u98ce"],
    "Tower Princess": ["\u5854\u697c\u516c\u4e3b", "\u516c\u4e3b\u5854"],
    "Valkyrie": ["\u5973\u6b66\u795e"],
    "Vines": ["\u85e4\u8513"],
    "Void": ["\u865a\u7a7a"],
    "Wall Breakers": ["\u7834\u5899\u8005"],
    "Witch": ["\u5973\u5deb"],
    "Wizard": ["\u6cd5\u5e08"],
    "X-Bow": ["\u8fde\u5f29", "xbow", "x-bow"],
    "Zap": ["\u7535\u51fb", "\u5c0f\u7535", "zap"],
    "Zappies": ["\u7535\u51fb\u5c0f\u961f"],
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
    "Skeletons": ["\u5c0f\u9ab7\u9ac5", "\u9ab7\u9ac5\u5175", "skeles"],
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


def normalize_card_alias(text: str) -> str:
    """Make harmless English spelling differences resolve to one card alias."""
    # Keep token boundaries so short Latin aliases cannot match inside ordinary
    # words. Punctuation becomes a separator, and the catalog includes compact
    # alternatives for forms such as "e giant" and "egiant".
    normalized = re.sub(r"[._-]+", " ", text.strip().lower())
    return re.sub(r"\s+", " ", normalized)


def _card_catalog_key(cards_meta_data: list[dict]) -> tuple[str, ...]:
    return tuple(str(item.get("card_name", "")).strip() for item in cards_meta_data)


def build_card_aliases(cards_meta_data: list[dict]) -> dict[str, list[str]]:
    """Build complete aliases for the stable parser catalog plus snapshot cards."""
    return _build_card_aliases(_card_catalog_key(cards_meta_data))


@lru_cache(maxsize=8)
def _build_card_aliases(snapshot_card_names: tuple[str, ...]) -> dict[str, list[str]]:
    """Cache the immutable alias catalog for the active card snapshot."""
    canonical_names = list(
        dict.fromkeys(
            [
                *CARD_ALIASES,
                *CARD_ALIAS_OVERRIDES,
                *CARD_COMMUNITY_ALIASES,
                *CARD_FORM_CATALOG,
                *snapshot_card_names,
            ]
        )
    )
    aliases = {name: list(CARD_ALIASES.get(name, [])) for name in canonical_names if name}
    for canonical, values in CARD_ALIAS_OVERRIDES.items():
        aliases.setdefault(canonical, []).extend(values)
    for canonical, values in CARD_COMMUNITY_ALIASES.items():
        aliases.setdefault(canonical, []).extend(values)
    for canonical in canonical_names:
        if not canonical:
            continue
        values = aliases.setdefault(canonical, [])
        values.extend([canonical.lower(), canonical.lower().replace(" ", "")])

        if canonical.endswith(" Evolution"):
            base = canonical.removesuffix(" Evolution")
            for base_alias in aliases.get(base, []):
                values.extend([
                    f"{base_alias}\u8fdb\u5316", f"\u8fdb\u5316{base_alias}",
                    f"\u89c9\u9192{base_alias}", f"{base_alias}\u89c9\u9192",
                    f"evo {base_alias}", f"evolved {base_alias}",
                ])
            values.extend([f"{base.lower()} evolution", f"evo {base.lower()}"])
        elif canonical.startswith("Hero "):
            base = canonical.removeprefix("Hero ")
            for base_alias in aliases.get(base, []):
                values.extend([
                    f"\u82f1\u96c4{base_alias}", f"{base_alias}\u82f1\u96c4",
                    f"hero {base_alias}",
                ])

        # Preserve declaration order while retaining both tokenized and compact
        # Latin spellings. This accepts "evo-mk", "evo mk", and "evomk"
        # without making a short alias match inside an unrelated word.
        normalized_values = []
        for alias in values:
            if not alias.strip():
                continue
            normalized = normalize_card_alias(alias)
            normalized_values.append(normalized)
            compact = normalized.replace(" ", "")
            if compact != normalized:
                normalized_values.append(compact)
        aliases[canonical] = list(dict.fromkeys(normalized_values))
    return aliases


@lru_cache(maxsize=8)
def _card_alias_patterns(snapshot_card_names: tuple[str, ...]) -> tuple[tuple[str, re.Pattern], ...]:
    patterns: list[tuple[str, re.Pattern]] = []
    for card_name, aliases in _build_card_aliases(snapshot_card_names).items():
        for alias in aliases:
            if not alias:
                continue
            if re.fullmatch(r"[a-z0-9 .'-]+", alias):
                expression = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
            else:
                expression = re.escape(alias)
            patterns.append((card_name, re.compile(expression)))
    return tuple(patterns)


CHINESE_NUM_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
    "三十": 30,
}


PARSER_SYSTEM_PROMPT = (
    "你是一个查询参数解析器。\n"
    "请把用户问题解析成 JSON，不要输出多余解释。\n\n"
    "输出格式固定为：\n"
    "{\n"
    '  "intent": "schedule_query | schedule_summary_query | deck_query | card_query | card_compare_query | card_rank_lookup_query | meta_analysis_query | match_preparation_query | reject",\n'
    '  "metric": "usage_rate | win_rate | clean_win_rate | null",\n'
    '  "compare_metric": "usage_rate | win_rate | clean_win_rate | null",\n'
    '  "rank": 具体名次或 null,\n'
    '  "top_n": 前几个或 null,\n'
    '  "card_name": 具体卡名或 null,\n'
    '  "card_names": ["标准卡名1", "标准卡名2"] 或 null,\n'
    '  "round": 轮次或 null,\n'
    '  "date": "YYYY-MM-DD 或 null",\n'
    '  "ask_players": true 或 false\n'
    "}\n\n"
    "规则：\n"
    "1. 问赛程、下一轮、谁上场、某轮打谁 -> schedule_query。\n"
    "1.1 问总结一下接下来的赛程、后面还有几场比赛、赛程压力怎么样 -> schedule_summary_query。\n"
    "1.2 问下一轮怎么准备、下一场比赛有什么准备建议、推荐可练卡组 -> match_preparation_query。\n"
    "1.3 问当前环境、当前主流卡组、卡牌定位、搭配、克制关系、打法或反制方案 -> meta_analysis_query。\n"
    "2. 问热门卡组、卡组排行、某名次卡组 -> deck_query。\n"
    "3. 问单卡使用率/胜率，或问前几张高使用率卡牌、某名次卡牌 -> card_query。\n"
    "3.1 问两张卡哪个更高/更强/谁更高，解析为 card_compare_query，并给出 card_names。\n"
    "3.2 问某张卡在某个榜单排第几，解析为 card_rank_lookup_query。\n"
    "4. “第三名/第3名/排名第三/第3个” 解析为 rank=3。\n"
    "5. “前20个/来5个/给我看几个” 解析为 top_n；如果只是“几个”且未给数字，默认 top_n=5。\n"
    "6. “最热门/最高使用率/第一名” 可以解析为 rank=1。\n"
    "7. 如果用户明确提到某张卡，card_name 填标准卡名，否则为 null。\n"
    "8. 问胜率前十 -> metric=win_rate；问净胜率 -> clean_win_rate；没特别说明 -> usage_rate。\n"
    "9. 如果用户提到具体比赛日期，date 填 YYYY-MM-DD。\n"
    "10. 如果无法归类，intent=reject。\n\n"
    "只输出 JSON。"
)

PARSER_SYSTEM_PROMPT += """

For independent requests joined by punctuation or conjunctions, return one object with
intent="multi_intent" and a "subqueries" array. Each subquery must have a stable id
(q1, q2, ...), one supported intent, and only that intent's fields. For a named card
asking more than one statistic, include metrics as an ordered array of usage_rate,
win_rate, and/or clean_win_rate while retaining metric as the first item. Never merge
an exact JSON statistic with an open-ended meta-analysis into one subquery.
"""

LOCAL_PARSE_CONFIDENCE_HIGH = "high"
LOCAL_PARSE_CONFIDENCE_MEDIUM = "medium"
LOCAL_PARSE_CONFIDENCE_LOW = "low"


def normalize_text(text: str) -> str:
    return text.strip().lower()


def extract_text_content(result: Any) -> str:
    if hasattr(result, "get_text_content"):
        return result.get_text_content()
    return str(result)


def extract_json_block(text: str) -> dict | None:
    """尽力从模型输出中提取一个 JSON 对象。

    此处只做语法提取；后续归一化仍会在 Skill 接收结果前校验意图白名单和字段范围。
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def extract_cn_number(text: str) -> int | None:
    return CHINESE_NUM_MAP.get(text)


def coerce_rank_value(value: Any, max_n: int = 30) -> int | None:
    """将模型或用户提供的排名转换为安全的 1..max_n 整数边界。"""
    if isinstance(value, int):
        return max(1, min(value, max_n))
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(1, min(int(stripped), max_n))

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return max(1, min(cn_number, max_n))

    return extract_rank_target(stripped, max_n=max_n)


def coerce_top_n_value(value: Any, max_n: int = 30) -> int | None:
    if isinstance(value, int):
        return max(1, min(value, max_n))
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(1, min(int(stripped), max_n))

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return max(1, min(cn_number, max_n))

    extracted = extract_top_n(stripped, default=max_n, max_n=max_n)
    if extracted == max_n and stripped not in {"前30", "三十"} and not any(ch.isdigit() for ch in stripped):
        return None
    return extracted


def coerce_round_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return cn_number

    return extract_round_number(stripped)


def extract_round_number(question: str) -> int | None:
    patterns = [
        r"第\s*(\d+)\s*轮",
        r"round\s*(\d+)",
        r"\br\s*(\d+)\b",
    ]
    q = question.lower()
    for pattern in patterns:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            return int(m.group(1))

    m_cn = re.search(r"第\s*([一二两三四五六七八九十]+)\s*轮", question)
    if m_cn:
        return extract_cn_number(m_cn.group(1))

    return None


def extract_date(question: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", question)
    if iso_match:
        year, month, day = iso_match.group(1).split("-")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    md_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", question)
    if md_match:
        month = int(md_match.group(1))
        day = int(md_match.group(2))
        return f"2026-{month:02d}-{day:02d}"

    return None


def extract_rank_target(question: str, max_n: int = 30) -> int | None:
    patterns = [
        r"第\s*(\d+)\s*名",
        r"排名\s*(\d+)",
        r"第\s*(\d+)\s*个",
        r"第\s*(\d+)(?!\s*轮)",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            return max(1, min(int(m.group(1)), max_n))

    cn_patterns = [
        r"第\s*([一二两三四五六七八九十]+)\s*名",
        r"排名\s*([一二两三四五六七八九十]+)",
        r"第\s*([一二两三四五六七八九十]+)\s*个",
        r"第\s*([一二两三四五六七八九十]+)(?!\s*轮)",
    ]
    for pattern in cn_patterns:
        m = re.search(pattern, question)
        if m:
            n = extract_cn_number(m.group(1))
            if n is not None:
                return max(1, min(n, max_n))

    return None


def extract_top_n(question: str, default: int | None = None, max_n: int = 30) -> int | None:
    patterns = [
        r"前\s*(\d+)",
        r"给我看\s*(\d+)\s*个",
        r"来\s*(\d+)\s*个",
        r"\btop\s*(\d+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            return max(1, min(int(m.group(1)), max_n))

    cn_patterns = [
        r"前\s*([一二两三四五六七八九十]+)",
    ]
    for pattern in cn_patterns:
        m = re.search(pattern, question)
        if m:
            n = extract_cn_number(m.group(1))
            if n is not None:
                return max(1, min(n, max_n))

    if "几个" in question or "一些" in question:
        return min(5, max_n)

    if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
        return min(5, max_n)

    return default


def resolve_card_name(text: str, cards_meta_data: list[dict]) -> str | None:
    """将别名解析为卡牌 Skill 使用的数据集标准名称。"""
    matches = resolve_card_names(text, cards_meta_data)
    return matches[0] if matches else None


def resolve_card_names(text: str, cards_meta_data: list[dict]) -> list[str]:
    """Resolve distinct, non-overlapping card mentions in the user's order."""
    q = normalize_card_alias(text)
    matches: list[tuple[int, int, str]] = []
    # Latin names and abbreviations use precompiled token-boundary patterns so
    # aliases such as "mm" and "fb" cannot match inside ordinary words.
    for card_name, pattern in _card_alias_patterns(_card_catalog_key(cards_meta_data)):
        for match in pattern.finditer(q):
            matches.append((match.start(), match.end(), card_name))

    # Prefer the longest alias at a text position, then reject shorter aliases
    # contained inside it (for example "Giant" and "Lightning" inside
    # "Electro Giant"). This also preserves the user's mention order.
    selected: list[tuple[int, int, str]] = []
    seen_cards: set[str] = set()
    for start, end, card_name in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]), item[2])):
        if card_name in seen_cards:
            continue
        if any(start < selected_end and end > selected_start for selected_start, selected_end, _ in selected):
            continue
        selected.append((start, end, card_name))
        seen_cards.add(card_name)
    return [card_name for _, _, card_name in selected]


def get_metric(question: str) -> str:
    q = question.lower()
    if "净胜率" in q or "cwr" in q or "clean win" in q:
        return "clean_win_rate"
    if "胜率" in q or "win rate" in q:
        return "win_rate"
    return "usage_rate"


VALID_METRICS = ("usage_rate", "win_rate", "clean_win_rate")
MAX_SUBQUERIES = 4


def extract_metrics(question: str) -> list[str]:
    """Return all explicitly requested card metrics in a stable display order."""
    q = question.lower()
    metrics = []
    if "使用率" in q or "usage rate" in q:
        metrics.append("usage_rate")
    if "胜率" in q or "win rate" in q:
        metrics.append("win_rate")
    if "净胜率" in q or "cwr" in q or "clean win" in q:
        metrics.append("clean_win_rate")
    return metrics


def normalize_metrics(value: Any, question: str, intent: str) -> list[str] | None:
    if intent != "card_query":
        return None

    raw_metrics = value if isinstance(value, list) else []
    metrics = [metric for metric in raw_metrics if metric in VALID_METRICS]
    if not metrics:
        metrics = extract_metrics(question)
    if not metrics:
        metrics = [get_metric(question)]
    return list(dict.fromkeys(metrics))


def is_asking_players(question: str) -> bool:
    q = question.lower()
    keywords = ["谁上", "谁打", "上场", "选手", "对战选手", "player", "who plays"]
    return any(k in q for k in keywords)


def is_schedule_query(question: str) -> bool:
    q = question.lower()
    keywords = ["下一轮", "赛程", "对战", "打谁", "上场", "round", "match", "轮"]
    return any(k in q for k in keywords)


def is_schedule_summary_query(question: str) -> bool:
    q = question.lower()
    explicit_phrases = [
        "接下来的赛程",
        "后面的赛程",
        "赛程压力",
        "赛程总结",
        "总结赛程",
        "总结一下赛程",
        "后面还有几场比赛",
        "还有几场比赛",
        "剩下几场比赛",
        "剩余几场比赛",
    ]
    if any(phrase in q for phrase in explicit_phrases):
        return True

    summary_intent_keywords = ["总结", "概况", "压力", "密集", "还有几场", "剩下几场", "剩余几场"]
    schedule_domain_keywords = ["赛程", "比赛", "对阵", "下一轮", "后面几轮", "后续几轮", "轮次", "round", "match", "upcoming"]

    return any(keyword in q for keyword in summary_intent_keywords) and any(
        keyword in q for keyword in schedule_domain_keywords
    )


def is_match_preparation_query(question: str) -> bool:
    q = question.lower()
    explicit_phrases = [
        "下一轮怎么准备",
        "下一场比赛有什么准备建议",
        "备战建议",
        "推荐几套可练的卡组",
        "帮我推荐几套可练的卡组",
        "给我备战建议",
    ]
    if any(phrase in q for phrase in explicit_phrases):
        return True

    preparation_keywords = ["准备", "备战", "练", "训练", "推荐"]
    match_domain_keywords = ["下一轮", "下一场", "比赛", "对手", "赛程", "卡组", "meta", "单卡"]

    return any(keyword in q for keyword in preparation_keywords) and any(
        keyword in q for keyword in match_domain_keywords
    )


def is_meta_analysis_query(question: str) -> bool:
    q = question.lower()
    if any(phrase in q for phrase in ("current meta", "current environment", "meta decks", "mainstream decks")):
        return True
    analysis_keywords = [
        "当前版本",
        "当前环境",
        "当前主流卡组",
        "整体环境",
        "环境是什么样",
        "环境怎么样",
        "meta环境",
        "进攻风格",
        "卡组构筑",
        "构筑思路",
        "卡组体系",
        "定位",
        "搭配",
        "主要怕什么",
        "克制",
        "反制",
        "速转",
        "空军",
        "重甲推进",
        "打法",
    ]
    domain_keywords = ["卡组", "卡牌", "单卡", "meta", "环境", "绿龙", "青龙", "龙宝", "baby dragon"]
    return any(keyword in q for keyword in analysis_keywords) and (
        any(keyword in q for keyword in domain_keywords) or resolve_card_name(question, []) is not None
    )


def is_deck_query(question: str) -> bool:
    q = question.lower()
    keywords = ["热门卡组", "高使用率卡组", "最热门卡组", "卡组", "deck"]
    return any(k in q for k in keywords)


def is_card_query(question: str, cards_meta_data: list[dict]) -> bool:
    q = question.lower()
    keywords = ["使用率", "胜率", "单卡", "卡牌", "meta", "热门卡牌", "card"]
    return any(k in q for k in keywords) or resolve_card_name(question, cards_meta_data) is not None


def is_card_ranking_query(question: str) -> bool:
    q = question.lower()
    keywords = ["前", "排行", "排名", "高使用率", "热门卡牌", "使用率最高", "胜率最高", "top", "分别是谁", "第"]
    return any(k in q for k in keywords)


def is_card_compare_query(question: str, cards_meta_data: list[dict]) -> bool:
    q = question.lower()
    compare_keywords = ["哪个", "谁更", "更高", "更强", "比较", "vs", "对比"]
    return len(resolve_card_names(question, cards_meta_data)) >= 2 and any(k in q for k in compare_keywords)


def is_card_rank_lookup_query(question: str, cards_meta_data: list[dict]) -> bool:
    if resolve_card_name(question, cards_meta_data) is None:
        return False
    q = question.lower()
    ranking_keywords = ["排第几", "排名多少", "排名第几", "榜排第几", "榜排名多少", "榜单排名多少"]
    english_rank_lookup_phrases = [
        "ranking position",
        "rank position",
        "what rank",
        "what position",
    ]
    return any(keyword in q for keyword in ranking_keywords + english_rank_lookup_phrases)


def has_explicit_rank_signal(question: str) -> bool:
    patterns = [
        r"第\s*\d+\s*名",
        r"排名\s*\d+",
        r"第\s*[一二两三四五六七八九十]+\s*名",
        r"排名\s*[一二两三四五六七八九十]+",
    ]
    return any(re.search(pattern, question) for pattern in patterns)


def has_explicit_top_n_signal(question: str) -> bool:
    patterns = [
        r"前\s*\d+",
        r"给我看\s*\d+\s*个",
        r"来\s*\d+\s*个",
        r"前\s*[一二两三四五六七八九十]+",
        r"\btop\s*\d+\b",
    ]
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)


def has_implicit_list_signal(question: str) -> bool:
    return any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么", "几个", "一些"])


def build_parse_metadata(
    *,
    parse_source: str,
    parse_confidence: str,
    parse_reason: str,
) -> dict:
    return {
        "parse_source": parse_source,
        "parse_confidence": parse_confidence,
        "parse_reason": parse_reason,
    }


def merge_parse_metadata(parsed: dict, metadata: dict) -> dict:
    result = dict(parsed)
    result.update(metadata)
    return result


def infer_local_parse_metadata(parsed: dict, question: str) -> dict:
    q = question.lower()
    intent = parsed.get("intent")
    rank = parsed.get("rank")
    top_n = parsed.get("top_n")
    card_name = parsed.get("card_name")
    round_no = parsed.get("round")
    target_date = parsed.get("date")
    ask_players = parsed.get("ask_players", False)
    metric = parsed.get("metric")
    compare_metric = parsed.get("compare_metric")
    card_names = parsed.get("card_names") or []

    if intent == "reject":
        return build_parse_metadata(
            parse_source="local_reject",
            parse_confidence=LOCAL_PARSE_CONFIDENCE_LOW,
            parse_reason="local rules could not classify the query",
        )

    strong_signals = 0
    weak_signals = 0
    reasons = [f"intent={intent}"]

    if intent == "schedule_query":
        if round_no is not None:
            strong_signals += 1
            reasons.append("round matched")
        if target_date is not None:
            strong_signals += 1
            reasons.append("date matched")
        if ask_players:
            weak_signals += 1
            reasons.append("player intent matched")
        if any(keyword in q for keyword in ["下一轮", "赛程", "对战", "打谁", "上场", "round", "match", "轮"]):
            strong_signals += 1
            reasons.append("schedule keyword matched")

    elif intent == "schedule_summary_query":
        if is_schedule_summary_query(question):
            strong_signals += 2
            reasons.append("strict schedule summary pattern matched")

    elif intent == "match_preparation_query":
        if is_match_preparation_query(question):
            strong_signals += 2
            reasons.append("strict match preparation pattern matched")

    elif intent == "meta_analysis_query":
        if is_meta_analysis_query(question):
            strong_signals += 2
            reasons.append("strict meta analysis pattern matched")

    elif intent == "deck_query":
        if rank is not None and has_explicit_rank_signal(question):
            strong_signals += 1
            reasons.append("explicit rank matched")
        elif rank is not None:
            weak_signals += 1
            reasons.append("implicit rank inferred")
        if top_n is not None and has_explicit_top_n_signal(question):
            strong_signals += 1
            reasons.append("explicit top_n matched")
        elif top_n is not None and has_implicit_list_signal(question):
            weak_signals += 1
            reasons.append("implicit list size inferred")
        if "热门卡组" in question or "deck" in q or "卡组" in question:
            strong_signals += 1
            reasons.append("deck keyword matched")
        if metric is not None:
            weak_signals += 1
            reasons.append("metric inferred")

    elif intent == "card_query":
        if card_name is not None:
            strong_signals += 1
            reasons.append("card_name matched")
        if rank is not None and has_explicit_rank_signal(question):
            strong_signals += 1
            reasons.append("explicit rank matched")
        elif rank is not None:
            weak_signals += 1
            reasons.append("implicit rank inferred")
        if top_n is not None and has_explicit_top_n_signal(question):
            strong_signals += 1
            reasons.append("explicit top_n matched")
        elif top_n is not None and has_implicit_list_signal(question):
            weak_signals += 1
            reasons.append("implicit list size inferred")
        if (
            ("胜率" in question)
            or ("净胜率" in question)
            or ("使用率" in question)
            or ("cwr" in q)
        ):
            strong_signals += 1
            reasons.append("metric keyword matched")
        elif metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            weak_signals += 1
            reasons.append("metric inferred")
        if "卡牌" in question or "热门卡牌" in question or "card" in q:
            strong_signals += 1
            reasons.append("card keyword matched")

    elif intent == "card_compare_query":
        if len(card_names) >= 2:
            strong_signals += 1
            reasons.append("multiple card names matched")
        if any(keyword in q for keyword in ["哪个", "谁更", "更高", "更强", "比较", "vs", "对比"]):
            strong_signals += 1
            reasons.append("compare keyword matched")
        if compare_metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            strong_signals += 1
            reasons.append("compare metric matched")

    elif intent == "card_rank_lookup_query":
        if card_name is not None:
            strong_signals += 1
            reasons.append("card_name matched")
        if metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            strong_signals += 1
            reasons.append("metric matched")
        if any(keyword in q for keyword in [
            "排第几", "排名多少", "排名第几", "榜排第几", "榜排名多少", "榜单排名多少",
            "ranking position", "rank position", "what rank", "what position",
        ]):
            strong_signals += 1
            reasons.append("rank lookup keyword matched")

    if strong_signals >= 2:
        confidence = LOCAL_PARSE_CONFIDENCE_HIGH
    elif strong_signals >= 1 or weak_signals >= 2:
        confidence = LOCAL_PARSE_CONFIDENCE_MEDIUM
    else:
        confidence = LOCAL_PARSE_CONFIDENCE_LOW

    return build_parse_metadata(
        parse_source="local_rule",
        parse_confidence=confidence,
        parse_reason=", ".join(reasons),
    )


def fallback_parse_query(question: str, cards_meta_data: list[dict]) -> dict:
    """结构化模型输出失败时提供确定性的路由字段。

    兜底逻辑刻意保守：保留可追溯的本地依据，不会把无法识别的问题伪装为合法 Skill 调用。
    """
    intent = "reject"
    if is_schedule_summary_query(question):
        intent = "schedule_summary_query"
    elif is_match_preparation_query(question):
        intent = "match_preparation_query"
    elif is_meta_analysis_query(question):
        intent = "meta_analysis_query"
    elif is_card_compare_query(question, cards_meta_data):
        intent = "card_compare_query"
    elif is_card_rank_lookup_query(question, cards_meta_data):
        intent = "card_rank_lookup_query"
    elif is_schedule_query(question):
        intent = "schedule_query"
    elif is_deck_query(question):
        intent = "deck_query"
    elif is_card_query(question, cards_meta_data):
        intent = "card_query"

    card_name = resolve_card_name(question, cards_meta_data)
    card_names = resolve_card_names(question, cards_meta_data)
    metric = get_metric(question) if intent in {"deck_query", "card_query", "card_rank_lookup_query"} else None
    compare_metric = get_metric(question) if intent == "card_compare_query" else None
    rank_target = extract_rank_target(question, max_n=30)
    top_n = extract_top_n(question, default=None, max_n=30)
    round_no = extract_round_number(question)
    target_date = extract_date(question)

    if card_name and not is_card_ranking_query(question):
        rank_target = None
        top_n = None

    if rank_target is not None:
        top_n = None

    if intent == "schedule_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None

    if intent == "schedule_summary_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "match_preparation_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "meta_analysis_query":
        metric = None
        compare_metric = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "deck_query":
        card_names = None

    if intent == "card_query":
        card_names = None

    if intent == "card_rank_lookup_query":
        compare_metric = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "card_compare_query":
        metric = None
        card_name = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None
        if not card_names:
            card_names = None

    if intent == "deck_query" and rank_target is None and top_n is None:
        if any(keyword in question for keyword in ["热门卡组", "高使用率卡组", "最热门卡组", "卡组"]):
            if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
                top_n = 5

    if intent == "card_query" and card_name is None and rank_target is None and top_n is None:
        if any(keyword in question for keyword in ["热门卡牌", "高使用率卡牌", "使用率最高", "胜率最高"]):
            if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
                top_n = 5

    parsed = {
        "intent": intent,
        "metric": metric,
        "metrics": normalize_metrics(None, question, intent),
        "compare_metric": compare_metric,
        "rank": rank_target,
        "top_n": top_n,
        "card_name": card_name,
        "card_names": card_names,
        "round": round_no,
        "date": target_date,
        "ask_players": is_asking_players(question),
    }
    return merge_parse_metadata(parsed, infer_local_parse_metadata(parsed, question))


def normalize_parsed_query(parsed: dict, question: str, cards_meta_data: list[dict]) -> dict:
    """在 Router/Skill 选择前校验并修复解析字段。

    这是模型输出的可信边界：它会限制数值范围、标准化卡牌别名，并写入解析置信度元数据。
    """
    result = {
        "intent": parsed.get("intent"),
        "metric": parsed.get("metric"),
        "metrics": parsed.get("metrics"),
        "compare_metric": parsed.get("compare_metric"),
        "rank": parsed.get("rank"),
        "top_n": parsed.get("top_n"),
        "card_name": parsed.get("card_name"),
        "card_names": parsed.get("card_names"),
        "round": parsed.get("round"),
        "date": parsed.get("date"),
        "ask_players": parsed.get("ask_players", False),
        "parse_source": parsed.get("parse_source"),
        "parse_confidence": parsed.get("parse_confidence"),
        "parse_reason": parsed.get("parse_reason"),
    }

    if result["intent"] not in {"schedule_query", "schedule_summary_query", "deck_query", "card_query", "card_compare_query", "card_rank_lookup_query", "meta_analysis_query", "match_preparation_query", "reject"}:
        return fallback_parse_query(question, cards_meta_data)

    # Model-provided Chinese aliases must become the same canonical keys used by JSON Skills.
    if isinstance(result["card_name"], str):
        result["card_name"] = (
            resolve_card_name(result["card_name"], cards_meta_data)
            or resolve_card_name(question, cards_meta_data)
        )
    if isinstance(result["card_names"], list):
        canonical_names: list[str] = []
        for raw_name in result["card_names"]:
            canonical_name = resolve_card_name(str(raw_name), cards_meta_data)
            if canonical_name and canonical_name not in canonical_names:
                canonical_names.append(canonical_name)
        for canonical_name in resolve_card_names(question, cards_meta_data):
            if canonical_name not in canonical_names:
                canonical_names.append(canonical_name)
        result["card_names"] = canonical_names

    if result["metric"] not in {"usage_rate", "win_rate", "clean_win_rate", None}:
        result["metric"] = get_metric(question)
    if result["compare_metric"] not in {"usage_rate", "win_rate", "clean_win_rate", None}:
        result["compare_metric"] = get_metric(question)

    coerced_rank = coerce_rank_value(result["rank"], max_n=30)
    if coerced_rank is not None:
        result["rank"] = coerced_rank

    coerced_top_n = coerce_top_n_value(result["top_n"], max_n=30)
    if coerced_top_n is not None:
        result["top_n"] = coerced_top_n

    coerced_round = coerce_round_value(result["round"])
    if coerced_round is not None:
        result["round"] = coerced_round

    if not isinstance(result["ask_players"], bool):
        result["ask_players"] = is_asking_players(question)

    if result["intent"] == "schedule_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        if not isinstance(result["round"], int):
            result["round"] = extract_round_number(question)
        if not result["date"]:
            result["date"] = extract_date(question)

    if result["intent"] == "schedule_summary_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None

    if result["intent"] == "match_preparation_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None

    if result["intent"] == "meta_analysis_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)

    if result["intent"] == "deck_query":
        result["card_names"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)
        if result["metric"] is None:
            result["metric"] = "usage_rate"
        if result["card_name"] and result["rank"] is None and result["top_n"] is None:
            result["top_n"] = 5
        elif result["rank"] is None and result["top_n"] is None and any(
            keyword in question for keyword in ["热门卡组", "主流卡组", "卡组有哪些", "哪些卡组"]
        ):
            result["top_n"] = 5

    if result["intent"] == "card_query":
        result["card_names"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)
        if result["card_name"] and not is_card_ranking_query(question):
            result["rank"] = None
            result["top_n"] = None
        if result["metric"] is None:
            result["metric"] = get_metric(question)
        result["metrics"] = normalize_metrics(result["metrics"], question, result["intent"])

    if result["intent"] == "card_rank_lookup_query":
        result["compare_metric"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)
        if result["metric"] is None:
            result["metric"] = get_metric(question)

    if result["intent"] == "card_compare_query":
        result["metric"] = None
        result["card_name"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not isinstance(result["card_names"], list) or len(result["card_names"]) < 2:
            result["card_names"] = resolve_card_names(question, cards_meta_data)
        if result["compare_metric"] is None:
            result["compare_metric"] = get_metric(question)

    if not result["parse_source"]:
        result["parse_source"] = "llm_parser"
    if result["parse_confidence"] not in {
        LOCAL_PARSE_CONFIDENCE_HIGH,
        LOCAL_PARSE_CONFIDENCE_MEDIUM,
        LOCAL_PARSE_CONFIDENCE_LOW,
    }:
        result["parse_confidence"] = LOCAL_PARSE_CONFIDENCE_MEDIUM
    if not result["parse_reason"]:
        result["parse_reason"] = "normalized parser output"

    return result


def _subquery_key(parsed: dict) -> tuple:
    return (
        parsed.get("intent"),
        parsed.get("card_name"),
        tuple(parsed.get("metrics") or []),
        tuple(parsed.get("card_names") or []),
        parsed.get("rank"),
        parsed.get("top_n"),
        parsed.get("round"),
        parsed.get("date"),
    )


def _make_multi_intent_result(subqueries: list[dict], question: str) -> dict:
    return {
        "intent": "multi_intent",
        "subqueries": subqueries,
        "parse_source": "local_rule",
        "parse_confidence": LOCAL_PARSE_CONFIDENCE_HIGH,
        "parse_reason": f"split {len(subqueries)} independent intents from compound query: {question[:80]}",
    }


def fallback_parse_multi_intent(question: str, cards_meta_data: list[dict]) -> dict:
    """Conservatively discover independent local and RAG questions in one utterance."""
    candidates: list[dict] = []
    seen: set[tuple] = set()

    def add_candidate(candidate: dict) -> None:
        if candidate.get("intent") == "reject":
            return
        if candidate.get("intent") == "card_query" and candidate.get("card_name"):
            for existing in candidates:
                if existing.get("intent") != "card_query" or existing.get("card_name") != candidate.get("card_name"):
                    continue
                merged_metrics = list(existing.get("metrics") or [])
                for metric in candidate.get("metrics") or [candidate.get("metric")]:
                    if metric and metric not in merged_metrics:
                        merged_metrics.append(metric)
                existing["metrics"] = merged_metrics
                existing["metric"] = merged_metrics[0] if merged_metrics else existing.get("metric")
                return
        key = _subquery_key(candidate)
        if key in seen or len(candidates) >= MAX_SUBQUERIES:
            return
        seen.add(key)
        candidates.append(candidate)

    card_names = resolve_card_names(question, cards_meta_data)
    requested_metrics = extract_metrics(question)
    if (
        card_names
        and requested_metrics
        and not is_card_compare_query(question, cards_meta_data)
        and not is_card_rank_lookup_query(question, cards_meta_data)
    ):
        # A request such as "Fireball and Poison usage" contains two
        # independent measurements, not one ambiguous card lookup. Keep them
        # separate so each result has an auditable Skill and output section.
        for card_name in card_names:
            card_query = fallback_parse_query(question, cards_meta_data)
            card_query.update(
                {
                    "intent": "card_query",
                    "card_name": card_name,
                    "card_names": None,
                    "metric": requested_metrics[0],
                    "metrics": requested_metrics,
                    "compare_metric": None,
                    "rank": None,
                    "top_n": None,
                    "round": None,
                    "date": None,
                    "ask_players": False,
                }
            )
            add_candidate(card_query)

    segments = [part.strip() for part in re.split(r"[，,；;。！？!?]|(?:还有|以及|并且|同时)", question) if part.strip()]
    for segment in segments:
        add_candidate(fallback_parse_query(segment, cards_meta_data))
    full_query = fallback_parse_query(question, cards_meta_data)
    if not any(candidate.get("intent") == full_query.get("intent") for candidate in candidates):
        add_candidate(full_query)

    # Do not manufacture a separate Top-N deck ranking from an implicit list
    # word when the same request already asks for open-ended meta analysis.
    if any(candidate.get("intent") == "meta_analysis_query" for candidate in candidates) and not (
        has_explicit_rank_signal(question) or has_explicit_top_n_signal(question)
    ):
        candidates = [candidate for candidate in candidates if candidate.get("intent") != "deck_query"]

    if len(candidates) <= 1:
        return candidates[0] if candidates else fallback_parse_query(question, cards_meta_data)

    subqueries = []
    for index, candidate in enumerate(candidates, start=1):
        subquery = dict(candidate)
        subquery["id"] = f"q{index}"
        subqueries.append(subquery)
    return _make_multi_intent_result(subqueries, question)


def normalize_multi_intent_query(parsed: dict, question: str, cards_meta_data: list[dict]) -> dict:
    """Validate an LLM multi-intent payload while retaining the single-intent contract."""
    if parsed.get("intent") != "multi_intent":
        return normalize_parsed_query(parsed, question, cards_meta_data)

    normalized_subqueries: list[dict] = []
    seen: set[tuple] = set()
    raw_subqueries = parsed.get("subqueries") if isinstance(parsed.get("subqueries"), list) else []
    for raw_subquery in raw_subqueries[:MAX_SUBQUERIES]:
        if not isinstance(raw_subquery, dict):
            continue
        normalized = normalize_parsed_query(raw_subquery, question, cards_meta_data)
        if normalized.get("intent") == "reject":
            continue
        key = _subquery_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        normalized["id"] = str(raw_subquery.get("id") or f"q{len(normalized_subqueries) + 1}")
        normalized_subqueries.append(normalized)

    if len(normalized_subqueries) <= 1:
        return normalized_subqueries[0] if normalized_subqueries else fallback_parse_multi_intent(question, cards_meta_data)

    result = _make_multi_intent_result(normalized_subqueries, question)
    result["parse_source"] = parsed.get("parse_source") or "llm_parser"
    result["parse_confidence"] = parsed.get("parse_confidence") or LOCAL_PARSE_CONFIDENCE_HIGH
    result["parse_reason"] = parsed.get("parse_reason") or "validated llm multi-intent output"
    return result
