"""Deterministic, explainable Clash Royale deck archetype classification."""

from __future__ import annotations

from dataclasses import dataclass


CLASSIFIER_VERSION = "feature_weighted_v2_1"


@dataclass(frozen=True)
class ArchetypeCatalogItem:
    key: str
    name: str
    family: str
    description: str


@dataclass(frozen=True)
class ArchetypeRule:
    key: str
    name: str
    family: str
    description: str
    anchors: tuple[str, ...]
    required_all: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()
    supports: tuple[tuple[str, float], ...] = ()
    blockers: tuple[tuple[str, float], ...] = ()
    feature_weights: tuple[tuple[str, float], ...] = ()
    min_score: float = 5.5
    priority: int = 0


@dataclass(frozen=True)
class DeckArchetype:
    key: str
    name: str
    family: str
    confidence: float
    score: float
    matched_signals: tuple[str, ...]
    reason: str


CHEAP_CYCLE_CARDS = frozenset(
    {
        "Bats",
        "Electro Spirit",
        "Fire Spirit",
        "Goblins",
        "Heal Spirit",
        "Ice Spirit",
        "Skeletons",
        "Spear Goblins",
        "The Log",
        "Zap",
    }
)
BRIDGE_PRESSURE_CARDS = frozenset(
    {
        "Bandit",
        "Battle Ram",
        "Cannon Cart",
        "Dark Prince",
        "Electro Wizard",
        "Magic Archer",
        "Ram Rider",
        "Royal Ghost",
    }
)
BAIT_CARDS = frozenset(
    {
        "Dart Goblin",
        "Goblin Barrel",
        "Goblin Gang",
        "Princess",
        "Rascals",
        "Rocket",
        "Skeleton Army",
        "Skeleton Barrel",
    }
)
SPLIT_LANE_CARDS = frozenset(
    {
        "Flying Machine",
        "Goblin Cage",
        "Royal Hogs",
        "Royal Recruits",
        "Three Musketeers",
        "Zappies",
    }
)
HEAVY_SUPPORT_CARDS = frozenset(
    {
        "Baby Dragon",
        "Dark Prince",
        "Electro Dragon",
        "Lightning",
        "Lumberjack",
        "Mega Minion",
        "Night Witch",
        "Phoenix",
        "Tornado",
    }
)
AIR_SUPPORT_CARDS = frozenset(
    {
        "Balloon",
        "Baby Dragon",
        "Bats",
        "Flying Machine",
        "Inferno Dragon",
        "Mega Minion",
        "Minions",
        "Skeleton Dragons",
    }
)
DEFENSIVE_BUILDINGS = frozenset(
    {
        "Bomb Tower",
        "Cannon",
        "Goblin Cage",
        "Inferno Tower",
        "Tesla",
        "Tombstone",
    }
)
SMALL_SPELLS = frozenset(
    {
        "Arrows",
        "Barbarian Barrel",
        "Giant Snowball",
        "Royal Delivery",
        "The Log",
        "Zap",
    }
)
BIG_SPELLS = frozenset(
    {
        "Earthquake",
        "Fireball",
        "Freeze",
        "Lightning",
        "Poison",
        "Rocket",
    }
)
STRATEGIC_ANCHORS = frozenset(
    {
        "Balloon",
        "Battle Ram",
        "Electro Giant",
        "Elixir Golem",
        "Giant",
        "Goblin Barrel",
        "Goblin Drill",
        "Goblin Giant",
        "Golem",
        "Graveyard",
        "Hog Rider",
        "Lava Hound",
        "Mega Knight",
        "Miner",
        "Mortar",
        "P.E.K.K.A",
        "Ram Rider",
        "Royal Giant",
        "Royal Hogs",
        "Royal Recruits",
        "Three Musketeers",
        "X-Bow",
    }
)


_RULES = (
    ArchetypeRule(
        "hog_eq",
        "野猪地震",
        "小费轮转",
        "野猪骑士与地震法术构成的快速轮转压力。",
        ("Hog Rider",),
        required_all=("Earthquake",),
        supports=(("Firecracker", 0.8), ("Cannon", 0.5), ("Tesla", 0.4)),
        feature_weights=(("cheap_cycle", 0.25), ("small_spell", 0.25)),
        priority=90,
    ),
    ArchetypeRule(
        "hog_cycle",
        "野猪速转",
        "小费轮转",
        "以野猪骑士为单一稳定胜利条件的低费轮转。",
        ("Hog Rider",),
        supports=(("Musketeer", 0.5), ("Cannon", 0.5), ("Ice Golem", 0.5)),
        blockers=(("Earthquake", 4.0),),
        feature_weights=(("cheap_cycle", 0.35), ("small_spell", 0.2)),
        priority=40,
    ),
    ArchetypeRule(
        "royal_hogs_cycle",
        "皇家野猪轮转",
        "小费轮转",
        "以皇家野猪持续分路施压的轮转卡组。",
        ("Royal Hogs",),
        supports=(("Earthquake", 1.0), ("Royal Delivery", 0.5), ("Archer Queen", 0.5)),
        blockers=(("Royal Recruits", 5.0),),
        feature_weights=(("cheap_cycle", 0.25), ("split_lane", 0.25)),
        priority=55,
    ),
    ArchetypeRule(
        "miner_control",
        "矿工消耗",
        "消耗控制",
        "以矿工反复获取塔伤，并用炸弹人或控制组件扩大节奏优势。",
        ("Miner",),
        supports=(("Wall Breakers", 1.5), ("Poison", 0.7), ("Bomb Tower", 0.5), ("Magic Archer", 0.4)),
        blockers=(("Balloon", 3.5), ("Mortar", 2.5), ("Lava Hound", 5.0)),
        feature_weights=(("cheap_cycle", 0.2), ("defensive_building", 0.2)),
        priority=35,
    ),
    ArchetypeRule(
        "goblin_drill_control",
        "哥布林钻机控制",
        "消耗控制",
        "以哥布林钻机持续制造塔前压力的控制卡组。",
        ("Goblin Drill",),
        supports=(("Wall Breakers", 0.8), ("Poison", 0.7), ("Fireball", 0.4), ("Bomb Tower", 0.5)),
        feature_weights=(("cheap_cycle", 0.2), ("defensive_building", 0.2)),
        priority=60,
    ),
    ArchetypeRule(
        "bait_pressure",
        "诱导消耗",
        "消耗控制",
        "通过飞桶、骷髅气球、攻城炸弹人等多重小型威胁进行法术诱导和持续消耗。",
        ("Goblin Barrel", "Skeleton Barrel", "Wall Breakers"),
        supports=(("Princess", 1.0), ("Goblin Gang", 0.8), ("Rocket", 0.7), ("Rascals", 0.6), ("Dart Goblin", 0.5)),
        feature_weights=(("bait", 0.35), ("cheap_cycle", 0.15)),
        priority=65,
    ),
    ArchetypeRule(
        "graveyard_control",
        "骷髅召唤控制",
        "消耗控制",
        "防守后用骷髅召唤配合坦克或法术完成反击。",
        ("Graveyard",),
        supports=(("Poison", 0.8), ("Freeze", 0.7), ("Baby Dragon", 0.5), ("Tombstone", 0.4)),
        blockers=(("Giant", 1.5),),
        feature_weights=(("defensive_building", 0.15), ("big_spell", 0.2)),
        priority=50,
    ),
    ArchetypeRule(
        "x_bow_siege",
        "X连弩自闭",
        "建筑自闭",
        "以X连弩隔河锁塔，并依靠建筑和低费牌保护进攻建筑。",
        ("X-Bow",),
        supports=(("Tesla", 1.0), ("Archers", 0.5), ("Knight", 0.4), ("Fireball", 0.3)),
        feature_weights=(("cheap_cycle", 0.3), ("defensive_building", 0.35)),
        priority=100,
    ),
    ArchetypeRule(
        "mortar_control",
        "迫击炮消耗",
        "建筑自闭",
        "以迫击炮隔河施压，配合杂毛、矿工或桶类组件持续消耗。",
        ("Mortar",),
        supports=(("Skeleton Barrel", 0.8), ("Miner", 0.7), ("Cannon Cart", 0.6), ("Rascals", 0.5)),
        feature_weights=(("cheap_cycle", 0.2), ("bait", 0.2)),
        priority=95,
    ),
    ArchetypeRule(
        "pekka_bridge",
        "皮卡桥头冲锋",
        "桥头冲锋",
        "以皮卡防守反击，并用多张桥头压力组件连续逼迫对手。",
        ("P.E.K.K.A",),
        required_any=("Battle Ram", "Ram Rider", "Bandit", "Royal Ghost"),
        supports=(("Battle Ram", 1.2), ("Ram Rider", 1.2), ("Bandit", 0.8), ("Royal Ghost", 0.7), ("Magic Archer", 0.5)),
        feature_weights=(("bridge_pressure", 0.35),),
        priority=85,
    ),
    ArchetypeRule(
        "bridge_pressure",
        "桥头冲锋",
        "桥头冲锋",
        "以攻城锤或蛮羊骑士为核心，在桥头连续形成即时压力。",
        ("Battle Ram", "Ram Rider"),
        supports=(("Bandit", 0.8), ("Royal Ghost", 0.7), ("Magic Archer", 0.5), ("Cannon Cart", 0.4)),
        blockers=(("P.E.K.K.A", 4.0), ("Royal Recruits", 2.5),),
        feature_weights=(("bridge_pressure", 0.4),),
        priority=45,
    ),
    ArchetypeRule(
        "armored_tempo",
        "重装节奏",
        "桥头冲锋",
        "以超级骑士或非桥头型皮卡承担防守反击核心的中费节奏卡组。",
        ("Mega Knight", "P.E.K.K.A"),
        supports=(("Miner", 0.4), ("Goblin Barrel", 0.4), ("Wall Breakers", 0.4), ("Inferno Dragon", 0.4)),
        blockers=(("Battle Ram", 1.8), ("Ram Rider", 1.8), ("Royal Recruits", 2.0)),
        feature_weights=(("bridge_pressure", 0.15), ("bait", 0.1)),
        priority=25,
    ),
    ArchetypeRule(
        "royal_recruits_split",
        "皇家卫队分路",
        "分路压制",
        "以皇家卫队稳定分线，配合皇家野猪或远程支援形成双路压力。",
        ("Royal Recruits",),
        supports=(("Royal Hogs", 1.2), ("Flying Machine", 0.8), ("Zappies", 0.8), ("Goblin Cage", 0.5)),
        feature_weights=(("split_lane", 0.4),),
        priority=88,
    ),
    ArchetypeRule(
        "three_musketeers_split",
        "三枪分路",
        "分路压制",
        "以三个火枪手拆分部署，在双路同时积累威胁。",
        ("Three Musketeers",),
        supports=(("Battle Ram", 0.7), ("Royal Ghost", 0.5), ("Bandit", 0.5), ("Elixir Collector", 0.8)),
        feature_weights=(("split_lane", 0.25), ("bridge_pressure", 0.15)),
        priority=82,
    ),
    ArchetypeRule(
        "royal_giant_push",
        "皇家巨人推进",
        "重型推进",
        "以皇家巨人为远程坦克，在防守后组织中大型推进。",
        ("Royal Giant",),
        supports=(("Fisherman", 0.8), ("Hunter", 0.6), ("Lightning", 0.5), ("Phoenix", 0.4)),
        feature_weights=(("heavy_support", 0.15),),
        priority=72,
    ),
    ArchetypeRule(
        "giant_push",
        "巨人推进",
        "重型推进",
        "以巨人承担前排并组织地面推进。",
        ("Giant",),
        supports=(("Prince", 0.6), ("Dark Prince", 0.5), ("Graveyard", 0.5), ("Sparky", 0.5)),
        blockers=(("Goblin Giant", 5.0), ("Electro Giant", 5.0), ("Royal Giant", 5.0)),
        feature_weights=(("heavy_support", 0.2),),
        priority=58,
    ),
    ArchetypeRule(
        "elixir_golem_push",
        "圣水戈仑推进",
        "重型推进",
        "以圣水戈仑吸收伤害，配合治疗、狂暴和高密度支援单位形成一波推进。",
        ("Elixir Golem",),
        supports=(("Battle Healer", 0.9), ("Night Witch", 0.7), ("Rage", 0.7), ("Electro Dragon", 0.6)),
        feature_weights=(("heavy_support", 0.25),),
        priority=84,
    ),
    ArchetypeRule(
        "golem_push",
        "戈仑推进",
        "重型推进",
        "以戈仑为前排，围绕一波高投入推进构建支援。",
        ("Golem",),
        supports=(("Night Witch", 0.9), ("Lumberjack", 0.6), ("Lightning", 0.6), ("Baby Dragon", 0.5)),
        feature_weights=(("heavy_support", 0.3),),
        priority=80,
    ),
    ArchetypeRule(
        "electro_giant_push",
        "雷电巨人推进",
        "重型推进",
        "以雷电巨人克制近身单位并组织法术支援推进。",
        ("Electro Giant",),
        supports=(("Tornado", 0.9), ("Lightning", 0.6), ("Golden Knight", 0.5), ("Bomber", 0.4)),
        feature_weights=(("heavy_support", 0.25), ("big_spell", 0.15)),
        priority=92,
    ),
    ArchetypeRule(
        "goblin_giant_push",
        "哥布林巨人推进",
        "重型推进",
        "以哥布林巨人为前排，常与电磁炮或双王子形成地面推进。",
        ("Goblin Giant",),
        supports=(("Sparky", 0.9), ("Prince", 0.5), ("Dark Prince", 0.5), ("Rage", 0.4)),
        feature_weights=(("heavy_support", 0.2),),
        priority=78,
    ),
    ArchetypeRule(
        "lava_air_push",
        "天狗空军推进",
        "空中压制",
        "以熔岩猎犬承伤并在空中堆叠支援单位。",
        ("Lava Hound",),
        supports=(("Balloon", 1.0), ("Skeleton Dragons", 0.7), ("Mega Minion", 0.5), ("Inferno Dragon", 0.5)),
        feature_weights=(("air_support", 0.3),),
        priority=98,
    ),
    ArchetypeRule(
        "balloon_pressure",
        "气球压制",
        "空中压制",
        "以气球兵制造高爆发塔伤，配合冰冻、矿工或低费组件创造进塔窗口。",
        ("Balloon",),
        supports=(("Freeze", 0.8), ("Lumberjack", 0.6), ("Miner", 0.5), ("Bowler", 0.4)),
        blockers=(("Lava Hound", 6.0),),
        feature_weights=(("air_support", 0.15), ("cheap_cycle", 0.1)),
        priority=48,
    ),
)

_OTHER = ArchetypeCatalogItem(
    "other",
    "其他卡组",
    "其他",
    "无清晰核心、多个互斥核心冲突或现有规则覆盖不足的卡组。",
)
ARCHETYPE_CATALOG = tuple(
    [ArchetypeCatalogItem(rule.key, rule.name, rule.family, rule.description) for rule in _RULES]
    + [_OTHER]
)
_CATALOG_BY_NAME = {item.name: item for item in ARCHETYPE_CATALOG}
_RULE_BY_NAME = {rule.name: rule for rule in _RULES}


def _feature_counts(cards: frozenset[str]) -> dict[str, int]:
    return {
        "cheap_cycle": len(cards & CHEAP_CYCLE_CARDS),
        "bridge_pressure": len(cards & BRIDGE_PRESSURE_CARDS),
        "bait": len(cards & BAIT_CARDS),
        "split_lane": len(cards & SPLIT_LANE_CARDS),
        "heavy_support": len(cards & HEAVY_SUPPORT_CARDS),
        "air_support": len(cards & AIR_SUPPORT_CARDS),
        "defensive_building": len(cards & DEFENSIVE_BUILDINGS),
        "small_spell": len(cards & SMALL_SPELLS),
        "big_spell": len(cards & BIG_SPELLS),
    }


def _other(reason: str) -> DeckArchetype:
    return DeckArchetype(
        key=_OTHER.key,
        name=_OTHER.name,
        family=_OTHER.family,
        confidence=0.0,
        score=0.0,
        matched_signals=(),
        reason=reason,
    )


def classify_deck(deck: tuple[str, ...] | list[str]) -> DeckArchetype:
    normalized = tuple(sorted(str(card).strip() for card in deck if str(card).strip()))
    cards = frozenset(normalized)
    if len(normalized) != 8 or len(cards) != 8:
        return _other("卡组不是八张互不重复的有效卡牌。")

    visible_anchors = cards & STRATEGIC_ANCHORS
    if len(visible_anchors) >= 4:
        return _other(f"检测到{len(visible_anchors)}个互斥战略核心冲突，按发明家或混搭卡组处理。")

    features = _feature_counts(cards)
    candidates: list[tuple[float, int, ArchetypeRule, tuple[str, ...]]] = []
    for rule in _RULES:
        anchors = cards & set(rule.anchors)
        if not anchors:
            continue
        if rule.required_all and not set(rule.required_all).issubset(cards):
            continue
        if rule.required_any and not (cards & set(rule.required_any)):
            continue

        score = 5.5 + 0.4 * max(0, len(anchors) - 1)
        signals = [f"核心:{card}" for card in sorted(anchors)]
        if rule.required_all:
            score += 1.5
            signals.extend(f"必需:{card}" for card in rule.required_all)
        if rule.required_any:
            score += 0.8

        for card, weight in rule.supports:
            if card in cards:
                score += weight
                signals.append(f"配件:{card}")
        for card, penalty in rule.blockers:
            if card in cards:
                score -= penalty
        for feature, weight in rule.feature_weights:
            count = features.get(feature, 0)
            if count:
                score += min(count, 4) * weight
                signals.append(f"特征:{feature}={count}")

        if score >= rule.min_score:
            candidates.append((score, rule.priority, rule, tuple(signals)))

    if not candidates:
        return _other("未检测到得分足够的主流胜利条件与配件特征。")

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    top_score, _, top_rule, top_signals = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    margin = top_score - second_score
    if (
        len(candidates) > 1
        and len(visible_anchors) >= 3
        and margin < 0.65
        and candidates[1][2].family != top_rule.family
    ):
        return _other(
            f"候选流派冲突：{top_rule.name}与{candidates[1][2].name}得分差仅{margin:.2f}。"
        )

    confidence = min(0.99, 0.58 + max(0.0, top_score - top_rule.min_score) * 0.045 + min(4.0, margin) * 0.055)
    reason = (
        f"按胜利条件锚点和配件特征判为{top_rule.name}；"
        f"得分{top_score:.2f}，次高候选差值{margin:.2f}。"
    )
    return DeckArchetype(
        key=top_rule.key,
        name=top_rule.name,
        family=top_rule.family,
        confidence=round(confidence, 3),
        score=round(top_score, 3),
        matched_signals=top_signals,
        reason=reason,
    )


def archetype_family(name: str) -> str:
    item = _CATALOG_BY_NAME.get(str(name or "").strip())
    return item.family if item else _OTHER.family


def archetype_definition(name: str) -> ArchetypeRule | None:
    return _RULE_BY_NAME.get(str(name or "").strip())
