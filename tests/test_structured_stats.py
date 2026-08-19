import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from structured_stats import build_structured_stats
from structured_query import StructuredQueryError, StructuredStatsRepository
from src.clashroyale_agent.stats.query_contracts import validate_deck as packaged_validate_deck
from src.clashroyale_agent.stats.build_primitives import sha256 as packaged_sha256
from src.clashroyale_agent.stats.card_queries import card_catalog as packaged_card_catalog
from src.clashroyale_agent.stats.card_detail_queries import card_stats as packaged_card_stats
from src.clashroyale_agent.stats.entity_queries import entity_catalog as packaged_entity_catalog
from src.clashroyale_agent.stats.loadout_catalog import loadout_catalog as packaged_loadout_catalog
from src.clashroyale_agent.stats.answer_payload import build_answer_payload as packaged_answer_payload
from src.clashroyale_agent.stats.deck_queries import deck_profile as packaged_deck_profile
from src.clashroyale_agent.stats.full_loadout_queries import full_loadout_profile as packaged_full_loadout_profile
from src.clashroyale_agent.stats.archetype_queries import archetypes as packaged_archetypes
from src.clashroyale_agent.stats.finalize import finalize_decks as packaged_finalize_decks
from src.clashroyale_agent.stats.math_primitives import result as packaged_result
from src.clashroyale_agent.stats.loadout_stats import increment_loadout_features as packaged_increment_loadout_features
from src.clashroyale_agent.stats.schema import create_schema as packaged_create_schema
from src.clashroyale_agent.stats.write_primitives import upsert_deck as packaged_upsert_deck


SNAPSHOT_ID = "supercell-structured-test"
DECK_A = ["Earthquake", "Hog Rider", "A2", "A3", "A4", "A5", "A6", "A7"]
DECK_B = ["Goblin Barrel", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]


def _loadout(deck: list[str], *, tower_id: str, evolved_index: int, elite_index: int) -> dict:
    card_id_base = 26000100 if deck == DECK_B else 26000000
    return {
        "schema_version": 1,
        "tower": {
            "id": tower_id,
            "name": "Tower Princess" if tower_id == "159000000" else "Cannoneer",
            "level": 15,
            "max_level": 15,
            "evolution_level": 0,
            "max_evolution_level": None,
            "elite": False,
            "elite_detection": "level_above_max_v1",
        },
        "cards": [
            {
                "id": str(card_id_base + index),
                "name": name,
                "level": 15 if index == elite_index else 14,
                "max_level": 14,
                "evolution_level": 1 if index == evolved_index else 2 if index == elite_index else 0,
                "max_evolution_level": 1,
                "elite": index == elite_index,
                "elite_detection": "official_evolution_level_v1",
            }
            for index, name in enumerate(deck)
        ],
        "complete": True,
    }


def _create_archive(data_dir: Path) -> None:
    archive = data_dir / "snapshot_archives" / SNAPSHOT_ID
    archive.mkdir(parents=True)
    connection = sqlite3.connect(archive / "aggregates.sqlite")
    connection.execute(
        "CREATE TABLE battles(sequence INTEGER PRIMARY KEY, battle_id TEXT UNIQUE, payload TEXT NOT NULL)"
    )
    records = [
        {
            "battle_id": "battle-1",
            "battle_time": "20260728T010000.000Z",
            "team_tag": "#A",
            "opponent_tag": "#B",
            "team_deck": DECK_A,
            "opponent_deck": DECK_B,
            "team_crowns": 2,
            "opponent_crowns": 1,
            "won": True,
        },
        {
            "battle_id": "battle-2",
            "battle_time": "20260728T010100.000Z",
            "team_tag": "#C",
            "opponent_tag": "#D",
            "team_deck": DECK_A,
            "opponent_deck": DECK_B,
            "team_crowns": 0,
            "opponent_crowns": 1,
            "won": False,
        },
        {
            "battle_id": "battle-3",
            "battle_time": "20260728T010200.000Z",
            "team_tag": "#E",
            "opponent_tag": "#F",
            "team_deck": DECK_A,
            "opponent_deck": DECK_B,
            "team_crowns": 1,
            "opponent_crowns": 1,
            "won": False,
        },
        {
            "battle_id": "battle-invalid",
            "battle_time": "20260728T010300.000Z",
            "team_deck": DECK_A[:7],
            "opponent_deck": DECK_B,
            "team_crowns": 1,
            "opponent_crowns": 0,
            "won": True,
        },
    ]
    for record in records[:3]:
        record["loadout_schema_version"] = 1
        record["team_loadout"] = _loadout(
            record["team_deck"], tower_id="159000000", evolved_index=0, elite_index=1
        )
        record["opponent_loadout"] = _loadout(
            record["opponent_deck"], tower_id="159000001", evolved_index=1, elite_index=2
        )
    connection.executemany(
        "INSERT INTO battles(sequence, battle_id, payload) VALUES (?, ?, ?)",
        [(index, record["battle_id"], json.dumps(record)) for index, record in enumerate(records, 1)],
    )
    connection.commit()
    connection.close()
    (archive / "collector_snapshot.json").write_text(
        json.dumps(
            {
                "snapshot_id": SNAPSHOT_ID,
                "sample_battles": 4,
                "target_battles": 4,
                "fetched_at": "2026-07-28T01:04:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (archive / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "snapshot_id": SNAPSHOT_ID, "complete": True}),
        encoding="utf-8",
    )


class StructuredStatsBuildTests(unittest.TestCase):
    def test_entity_queries_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_entity_catalog))

    def test_loadout_catalog_has_a_packaged_owner(self):
        self.assertTrue(callable(packaged_loadout_catalog))

    def test_answer_payload_has_a_packaged_owner(self):
        self.assertTrue(callable(packaged_answer_payload))

    def test_deck_queries_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_deck_profile))

    def test_full_loadout_queries_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_full_loadout_profile))

    def test_archetype_queries_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_archetypes))

    def test_structured_query_contracts_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_validate_deck))

    def test_structured_stats_build_primitives_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_sha256))

    def test_structured_stats_math_has_a_packaged_owner(self):
        self.assertEqual(packaged_result(2, 1), (1, 0, 0))

    def test_structured_stats_schema_has_a_packaged_owner(self):
        self.assertTrue(callable(packaged_create_schema))

    def test_structured_stats_write_primitives_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_upsert_deck))

    def test_structured_stats_loadout_features_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_increment_loadout_features))

    def test_structured_query_card_queries_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_card_catalog))

    def test_structured_query_card_detail_queries_have_a_packaged_owner(self):
        self.assertTrue(callable(packaged_card_stats))

    def test_structured_stats_finalize_has_a_packaged_owner(self):
        self.assertTrue(callable(packaged_finalize_decks))

    def test_build_expands_valid_battles_into_two_sided_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)

            manifest = build_structured_stats(data_dir, SNAPSHOT_ID)
            stats_path = data_dir / "structured_stats" / SNAPSHOT_ID / "stats.sqlite"
            connection = sqlite3.connect(stats_path)

            self.assertEqual(manifest["counts"]["source_battles"], 4)
            self.assertEqual(manifest["counts"]["included_battles"], 3)
            self.assertEqual(manifest["counts"]["excluded_incomplete_decks"], 1)
            self.assertEqual(manifest["counts"]["side_records"], 6)
            self.assertEqual(manifest["counts"]["full_loadout_battles"], 3)
            self.assertEqual(manifest["counts"]["excluded_incomplete_loadouts"], 0)

            hog = connection.execute(
                "SELECT appearances, wins, losses, draws, usage_rate, clean_win_rate, net_win_rate, rating "
                "FROM card_stats WHERE card_name = 'Hog Rider'"
            ).fetchone()
            self.assertEqual(hog[:4], (3, 1, 1, 1))
            self.assertEqual(hog[4], 50.0)
            self.assertEqual(hog[5], 50.0)
            self.assertEqual(hog[6], 0.0)
            self.assertGreaterEqual(hog[7], 0.0)
            self.assertLessEqual(hog[7], 100.0)

            deck_a_json = json.dumps(sorted(DECK_A), separators=(",", ":"))
            deck = connection.execute(
                "SELECT games, wins, losses, draws, usage_rate, clean_win_rate FROM deck_stats "
                "WHERE deck_signature = ?",
                (deck_a_json,),
            ).fetchone()
            self.assertEqual(deck, (3, 1, 1, 1, 50.0, 50.0))

            matchup = connection.execute(
                "SELECT games, wins_a, wins_b, draws, crowns_a, crowns_b, latest_battle_time "
                "FROM matchup_stats"
            ).fetchone()
            self.assertEqual(matchup[:4], (3, 1, 1, 1))
            self.assertEqual(matchup[4:6], (3, 3))
            self.assertEqual(matchup[6], "20260728T010200.000Z")

            full_loadouts = connection.execute(
                "SELECT games, wins, losses, draws, usage_rate FROM full_loadout_stats ORDER BY games DESC"
            ).fetchall()
            self.assertEqual(full_loadouts, [(3, 1, 1, 1, 50.0), (3, 1, 1, 1, 50.0)])
            full_matchup = connection.execute(
                "SELECT games, wins_a, wins_b, draws FROM full_loadout_matchup_stats"
            ).fetchone()
            self.assertEqual(full_matchup, (3, 1, 1, 1))
            towers = connection.execute(
                "SELECT tower_id, appearances FROM tower_stats ORDER BY tower_id"
            ).fetchall()
            self.assertEqual(towers, [("159000000", 3), ("159000001", 3)])
            loadout_cards = connection.execute(
                "SELECT COUNT(*), SUM(appearances), SUM(evolution_appearances), SUM(elite_appearances) "
                "FROM loadout_card_catalog"
            ).fetchone()
            self.assertEqual(loadout_cards, (16, 48, 6, 6))
            self.assertEqual(connection.execute("SELECT SUM(appearances) FROM evolution_stats").fetchone()[0], 6)
            self.assertEqual(connection.execute("SELECT SUM(appearances) FROM elite_stats").fetchone()[0], 6)
            entities = connection.execute(
                "SELECT entity_id, entity_type, card_name, special_state, appearances, usage_rate, clean_win_rate, rating "
                "FROM loadout_entity_stats ORDER BY entity_id"
            ).fetchall()
            entity_ids = {row[0] for row in entities}
            self.assertIn("tower:159000000", entity_ids)
            self.assertIn("tower:159000001", entity_ids)
            self.assertIn("card:26000000:evolution", entity_ids)
            self.assertIn("card:26000001:elite", entity_ids)
            self.assertIn("card:26000002:ordinary", entity_ids)
            self.assertNotIn("card:26000000:ordinary", entity_ids)
            self.assertTrue(all(0.0 <= row[7] <= 100.0 for row in entities))
            evolved_earthquake = connection.execute(
                "SELECT appearances, usage_rate, clean_win_rate FROM loadout_entity_stats "
                "WHERE entity_id='card:26000000:evolution'"
            ).fetchone()
            self.assertEqual(evolved_earthquake, (3, 50.0, 50.0))

            archetypes = dict(connection.execute("SELECT archetype, games FROM archetype_stats"))
            self.assertEqual(archetypes["野猪地震"], 3)
            self.assertEqual(archetypes["诱导消耗"], 3)
            connection.close()

            payload = stats_path.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["stats_sqlite_sha256"])

    def test_build_is_idempotent_when_the_existing_index_hash_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            first = build_structured_stats(data_dir, SNAPSHOT_ID)

            second = build_structured_stats(data_dir, SNAPSHOT_ID)

            self.assertEqual(second, first)

    def test_repository_returns_provenance_and_never_needs_alias_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)

            catalog = repository.card_catalog()
            hog = next(card for card in catalog["cards"] if card["card_id"] == "Hog Rider")
            self.assertEqual(hog["display_name_zh"], "\u91ce\u732a\u9a91\u58eb")
            card = repository.card_stats("Hog Rider")
            self.assertEqual(card["matched_sample_count"], 3)
            self.assertEqual(card["provenance"]["included_battles"], 3)
            self.assertEqual(card["warning"]["code"], "LOW_SAMPLE_WARNING")

            comparison = repository.compare_cards(["Hog Rider", "Goblin Barrel"])
            self.assertEqual(len(comparison["cards"]), 2)
            self.assertIn("clean_win_rate", comparison["differences"])

            payload = repository.answer_payload()
            self.assertIn("Hog Rider", payload["card_deck_stats"])
            self.assertEqual(payload["card_deck_stats"]["Hog Rider"][0]["battles"], 3)
            self.assertIsNotNone(payload["top_decks"][0]["usage_rate"])
            self.assertIsNotNone(payload["top_decks"][0]["sample_win_rate"])

    def test_card_pair_stats_returns_exact_teammate_count(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)

            result = repository.card_pair_stats(["Hog Rider", "Earthquake"])

            self.assertEqual(result["cards"], ["Hog Rider", "Earthquake"])
            self.assertGreater(result["games"], 0)
            self.assertEqual(result["matched_sample_count"], result["games"])

    def test_card_teammate_rankings_return_requested_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)

            result = repository.card_teammate_rankings("Hog Rider", top_n=3)

            self.assertEqual(result["card_id"], "Hog Rider")
            self.assertEqual(len(result["teammates"]), 3)
            self.assertGreaterEqual(
                result["teammates"][0]["games"],
                result["teammates"][1]["games"],
            )

    def test_card_rankings_support_usage_win_rate_and_rating_with_stable_ties(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            stats_path = data_dir / "structured_stats" / SNAPSHOT_ID / "stats.sqlite"
            connection = sqlite3.connect(stats_path)
            connection.execute(
                "UPDATE card_stats SET usage_rate=60, clean_win_rate=55, rating=80 "
                "WHERE card_name='Hog Rider'"
            )
            connection.execute(
                "UPDATE card_stats SET usage_rate=60, clean_win_rate=55, rating=70 "
                "WHERE card_name='Earthquake'"
            )
            connection.execute(
                "UPDATE card_stats SET usage_rate=40, clean_win_rate=70, rating=90 "
                "WHERE card_name='Goblin Barrel'"
            )
            connection.commit()
            connection.close()
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)

            usage = repository.card_rankings("usage_rate")
            win_rate = repository.card_rankings("clean_win_rate")
            rating = repository.card_rankings("rating")

        self.assertEqual(usage["sort_by"], "usage_rate")
        self.assertEqual(usage["card_count"], 16)
        self.assertEqual(
            [card["card_name"] for card in usage["cards"][:2]],
            ["Earthquake", "Hog Rider"],
        )
        self.assertEqual([card["rank"] for card in usage["cards"][:3]], [1, 1, 3])
        self.assertEqual(usage["cards"][0]["display_name_zh"], "地震法术")
        self.assertEqual(win_rate["cards"][0]["card_name"], "Goblin Barrel")
        self.assertEqual(rating["cards"][0]["card_name"], "Goblin Barrel")
        self.assertIn("rating_formula", rating["metric_definitions"])
        self.assertEqual(rating["provenance"]["included_battles"], 3)

    def test_card_rankings_reject_unknown_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)

            with self.assertRaises(StructuredQueryError) as invalid:
                repository.card_rankings("appearances")

        self.assertEqual(invalid.exception.code, "INVALID_CARD_RANKING_METRIC")

    def test_card_catalog_uses_reviewed_chinese_display_names(self):
        expected = {
            "Battle Healer": "战斗天使",
            "Battle Ram": "野蛮人攻城锤",
            "Boss Bandit": "刺客头领",
            "Goblin Barrel": "哥布林飞桶",
            "Clone": "克隆法术",
            "Earthquake": "地震法术",
            "Arrows": "万箭齐发",
            "Electro Wizard": "闪电法师",
            "Executioner": "飞斧屠夫",
            "Fire Spirit": "烈焰精灵",
            "Freeze": "冰冻法术",
            "Giant Skeleton": "骷髅巨人",
            "Golden Knight": "黄金圣骑",
            "Graveyard": "骷髅召唤",
            "Ice Golem": "戈仑冰人",
            "Ice Spirit": "冰雪精灵",
            "Ice Wizard": "寒冰法师",
            "Lightning": "雷电法术",
            "Lumberjack": "狂暴樵夫",
            "Magic Archer": "神箭游侠",
            "Mighty Miner": "威猛矿工",
            "Mirror": "镜像法术",
            "Monk": "盖世武僧",
            "Mother Witch": "女巫婆婆",
            "Poison": "毒药法术",
            "Ram Rider": "蛮羊骑士",
            "Rascals": "绿林团伙",
            "Skeleton King": "骷髅帝王",
            "Spirit Empress": "精灵女皇",
            "Tornado": "飓风法术",
            "Vines": "藤蔓法术",
            "Void": "虚空法术",
            "Wall Breakers": "攻城炸弹人",
            "Zap": "电击法术",
            "Zappies": "电击车小队",
        }
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)
            repository._card_names = set(expected)
            catalog = repository.card_catalog()["cards"]

        by_id = {item["card_id"]: item["display_name_zh"] for item in catalog}
        self.assertEqual({card_id: by_id[card_id] for card_id in expected}, expected)

    def test_repository_validates_exact_decks_and_reports_zero_matchup_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            repository = StructuredStatsRepository(data_dir, SNAPSHOT_ID)

            profile = repository.deck_profile(DECK_A)
            self.assertEqual(profile["matched_sample_count"], 3)
            matchup = repository.deck_matchup(DECK_A, DECK_B)
            self.assertEqual(matchup["matched_sample_count"], 3)
            self.assertEqual(matchup["deck_a"]["clean_win_rate"], 50.0)

            with self.assertRaises(StructuredQueryError) as duplicate:
                repository.deck_profile([DECK_A[0]] * 8)
            self.assertEqual(duplicate.exception.code, "INVALID_DECK")

            unknown = ["Earthquake", "Hog Rider", "A2", "A3", "A4", "A5", "A6", "B1"]
            with self.assertRaises(StructuredQueryError) as missing:
                repository.deck_matchup(DECK_A, unknown)
            self.assertEqual(missing.exception.code, "NO_MATCHUP_EVIDENCE")

            environment = repository.archetypes()
            self.assertEqual(environment["archetypes"][0]["games"], 3)

            full_a = _loadout(DECK_A, tower_id="159000000", evolved_index=0, elite_index=1)
            full_b = _loadout(DECK_B, tower_id="159000001", evolved_index=1, elite_index=2)
            full_profile = repository.full_loadout_profile(full_a)
            self.assertEqual(full_profile["deck_mode"], "full_loadout")
            self.assertEqual(full_profile["matched_sample_count"], 3)
            self.assertEqual(full_profile["loadout"]["loadout"]["tower"]["display_name_zh"], "公主塔")
            self.assertEqual(
                full_profile["common_opponents"][0]["loadout"]["tower"]["display_name_zh"],
                "炮塔",
            )
            full_matchup = repository.full_loadout_matchup(full_a, full_b)
            self.assertEqual(full_matchup["matched_sample_count"], 3)
            self.assertEqual(full_matchup["loadout_a"]["clean_win_rate"], 50.0)
            invalid_tower = {**full_a, "tower": {"id": "undefined"}}
            with self.assertRaises(StructuredQueryError) as invalid:
                repository.full_loadout_profile(invalid_tower)
            self.assertEqual(invalid.exception.code, "INVALID_FULL_LOADOUT")
            catalog = repository.loadout_catalog()
            self.assertEqual(len(catalog["towers"]), 2)
            self.assertEqual(len(catalog["cards"]), 16)
            self.assertTrue(any(card["can_evolve"] for card in catalog["cards"]))
            self.assertTrue(any(card["can_be_elite"] for card in catalog["cards"]))
            entity_rankings = repository.entity_rankings("usage_rate")
            self.assertTrue(any(item["entity_id"] == "tower:159000000" for item in entity_rankings["entities"]))
            self.assertTrue(any(item["entity_id"] == "card:26000000:evolution" for item in entity_rankings["entities"]))
            self.assertFalse(any(item["entity_id"] == "card:26000000:ordinary" for item in entity_rankings["entities"]))
            entity = repository.entity_stats("card:26000000:evolution")
            self.assertEqual(entity["entity"]["display_name_zh"], "觉醒地震法术")
            self.assertEqual(entity["matched_sample_count"], 3)
            resolved_entity = repository.entity_stats_by_reference("card", "Earthquake", "evolution")
            self.assertEqual(resolved_entity["entity"]["entity_id"], "card:26000000:evolution")
            resolved_tower = repository.entity_stats_by_reference("tower", "Tower Princess", "tower")
            self.assertEqual(resolved_tower["entity"]["entity_id"], "tower:159000000")
            comparison = repository.compare_entities(["card:26000000:evolution", "tower:159000000"])
            self.assertEqual(len(comparison["entities"]), 2)
            self.assertIn("clean_win_rate", comparison["differences"])
            with self.assertRaisesRegex(StructuredQueryError, "official tower or card form"):
                repository.entity_stats("card:not-official:evolution")

    def test_deck_profile_orders_opponents_by_observed_win_rate_then_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_archive(data_dir)
            build_structured_stats(data_dir, SNAPSHOT_ID)
            stats_path = data_dir / "structured_stats" / SNAPSHOT_ID / "stats.sqlite"
            signature_a = json.dumps(sorted(DECK_A), separators=(",", ":"))
            high = json.dumps([f"H{i}" for i in range(8)], separators=(",", ":"))
            low = json.dumps([f"L{i}" for i in range(8)], separators=(",", ":"))
            connection = sqlite3.connect(stats_path)
            connection.executemany(
                "INSERT INTO matchup_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (signature_a, high, 10, 9, 1, 0, 9, 1, "20260728T020000.000Z"),
                    (signature_a, low, 20, 4, 16, 0, 4, 16, "20260728T030000.000Z"),
                ],
            )
            connection.commit()
            connection.close()

            profile = StructuredStatsRepository(data_dir, SNAPSHOT_ID).deck_profile(DECK_A)

        self.assertEqual(
            [item["clean_win_rate"] for item in profile["common_opponents"]],
            [90.0, 50.0, 20.0],
        )


if __name__ == "__main__":
    unittest.main()
