import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.datasets import (
    build_dataset_catalog_payload,
    dataset_scope_display_name,
    dataset_scope_parts,
    load_active_snapshot_group_manifest,
    rag_scope_stats_for_manifest,
    resolve_official_structured_repository,
    resolve_rolling_dataset_retriever,
    resolve_structured_group_repository,
    unavailable_dataset_payload,
    validate_dataset_scope,
)
from rolling_corpus import DATASET_WINDOW_DEFINITIONS
from structured_query import StructuredQueryError


class _Repository:
    calls = []

    def __init__(self, data_dir, group_id, scope):
        self.data_dir = data_dir
        self.group_id = group_id
        self.scope = scope

    @classmethod
    def for_snapshot_group(cls, data_dir, group_id, scope):
        cls.calls.append((data_dir, group_id, scope))
        return cls(data_dir, group_id, scope)


class _OfficialRepository:
    calls = []

    def __init__(self, data_dir, snapshot_id):
        self.data_dir = data_dir
        self.snapshot_id = snapshot_id
        self.__class__.calls.append((data_dir, snapshot_id))


class _Lock:
    def __init__(self):
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *_exc_info):
        return False


class _Retriever:
    calls = []
    instances = []
    next_dense_available = True
    next_docs_fingerprint = "fingerprint-1"

    def __init__(self, documents, **kwargs):
        self.documents = documents
        self.kwargs = kwargs
        self.dense_available = self.__class__.next_dense_available
        self.docs_fingerprint = self.__class__.next_docs_fingerprint
        self.closed = False
        self.__class__.calls.append((documents, kwargs))
        self.__class__.instances.append(self)

    def close(self):
        self.closed = True


class ApiDatasetTests(unittest.TestCase):
    def setUp(self):
        _Repository.calls = []
        _OfficialRepository.calls = []
        _Retriever.calls = []
        _Retriever.instances = []
        _Retriever.next_dense_available = True
        _Retriever.next_docs_fingerprint = "fingerprint-1"

    def test_resolve_rolling_dataset_retriever_uses_matching_cache_without_lock(self):
        cached = SimpleNamespace()
        app = SimpleNamespace(
            state=SimpleNamespace(
                rolling_retriever=cached,
                rolling_retriever_group_id="group-1",
            )
        )

        retriever = resolve_rolling_dataset_retriever(
            app,
            Path("data-root"),
            {"snapshot_group_id": "group-1", "rag_docs_fingerprint": "fingerprint-1"},
            "7d_all",
            retriever_cls=_Retriever,
            lock_factory=_Lock,
        )

        self.assertIs(retriever, cached)
        self.assertEqual(_Retriever.calls, [])
        self.assertFalse(hasattr(app.state, "rolling_retriever_lock"))

    def test_resolve_rolling_dataset_retriever_loads_documents_and_caches_candidate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_dir = root / "snapshot_groups" / "group-1"
            group_dir.mkdir(parents=True)
            (group_dir / "rag_documents.json").write_text(
                json.dumps([{"id": "doc-1"}]),
                encoding="utf-8",
            )
            app = SimpleNamespace(state=SimpleNamespace())

            retriever = resolve_rolling_dataset_retriever(
                app,
                root,
                {"snapshot_group_id": "group-1", "rag_docs_fingerprint": "fingerprint-1"},
                "7d_all",
                retriever_cls=_Retriever,
                lock_factory=_Lock,
            )

        self.assertIs(app.state.rolling_retriever, retriever)
        self.assertEqual(app.state.rolling_retriever_group_id, "group-1")
        self.assertEqual(app.state.rolling_retriever_lock.entered, 1)
        self.assertEqual(_Retriever.calls[0][0], [{"id": "doc-1"}])
        self.assertEqual(
            _Retriever.calls[0][1],
            {
                "index_path": group_dir / "qdrant",
                "lazy_scope_bm25": True,
                "bm25_scope_cache_size": 2,
            },
        )

    def test_resolve_rolling_dataset_retriever_closes_previous_on_new_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_dir = root / "snapshot_groups" / "group-2"
            group_dir.mkdir(parents=True)
            (group_dir / "rag_documents.json").write_text("[]", encoding="utf-8")
            previous = _Retriever([])
            app = SimpleNamespace(
                state=SimpleNamespace(
                    rolling_retriever=previous,
                    rolling_retriever_group_id="group-1",
                )
            )

            retriever = resolve_rolling_dataset_retriever(
                app,
                root,
                {"snapshot_group_id": "group-2", "rag_docs_fingerprint": "fingerprint-1"},
                "7d_all",
                retriever_cls=_Retriever,
                lock_factory=_Lock,
            )

        self.assertIsNot(retriever, previous)
        self.assertTrue(previous.closed)
        self.assertIs(app.state.rolling_retriever, retriever)

    def test_resolve_rolling_dataset_retriever_closes_misaligned_candidate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_dir = root / "snapshot_groups" / "group-1"
            group_dir.mkdir(parents=True)
            (group_dir / "rag_documents.json").write_text("[]", encoding="utf-8")
            app = SimpleNamespace(state=SimpleNamespace())
            _Retriever.next_docs_fingerprint = "wrong"

            with self.assertRaises(StructuredQueryError) as ctx:
                resolve_rolling_dataset_retriever(
                    app,
                    root,
                    {"snapshot_group_id": "group-1", "rag_docs_fingerprint": "fingerprint-1"},
                    "7d_all",
                    retriever_cls=_Retriever,
                    lock_factory=_Lock,
                )

        self.assertEqual(_Retriever.calls[-1][0], [])
        self.assertTrue(_Retriever.instances[-1].closed)
        self.assertEqual(ctx.exception.code, "DATASET_SCOPE_NOT_READY")

    def test_resolve_rolling_dataset_retriever_reports_unloadable_documents(self):
        app = SimpleNamespace(state=SimpleNamespace())

        with self.assertRaises(StructuredQueryError) as ctx:
            resolve_rolling_dataset_retriever(
                app,
                Path("missing-root"),
                {"snapshot_group_id": "group-1", "rag_docs_fingerprint": "fingerprint-1"},
                "7d_all",
                retriever_cls=_Retriever,
                lock_factory=_Lock,
            )

        self.assertEqual(ctx.exception.code, "DATASET_SCOPE_NOT_READY")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_resolve_official_structured_repository_uses_live_snapshot_and_cache(self):
        app = SimpleNamespace(state=SimpleNamespace(live_snapshot={"snapshot_id": "official-1"}))
        pointer_calls = []

        def load_pointer(_path):
            pointer_calls.append(_path)
            return {"snapshot_id": "from-pointer"}

        repository = resolve_official_structured_repository(
            app,
            Path("data-root"),
            pointer_loader=load_pointer,
            repository_cls=_OfficialRepository,
        )
        second = resolve_official_structured_repository(
            app,
            Path("data-root"),
            pointer_loader=load_pointer,
            repository_cls=_OfficialRepository,
        )

        self.assertIs(second, repository)
        self.assertEqual(repository.snapshot_id, "official-1")
        self.assertEqual(pointer_calls, [])
        self.assertEqual(_OfficialRepository.calls, [(Path("data-root"), "official-1")])

    def test_resolve_official_structured_repository_uses_pointer_when_live_snapshot_missing(self):
        app = SimpleNamespace(state=SimpleNamespace())
        pointer_paths = []

        def load_pointer(path):
            pointer_paths.append(path)
            return {"snapshot_id": "pointer-1"}

        repository = resolve_official_structured_repository(
            app,
            Path("data-root"),
            pointer_loader=load_pointer,
            repository_cls=_OfficialRepository,
        )

        self.assertEqual(repository.snapshot_id, "pointer-1")
        self.assertEqual(pointer_paths, [Path("data-root") / "official_snapshot_pointer.json"])
        self.assertIs(app.state.structured_repository, repository)

    def test_resolve_official_structured_repository_rebuilds_stale_cache(self):
        stale = _OfficialRepository(Path("old-root"), "old-official")
        app = SimpleNamespace(
            state=SimpleNamespace(
                live_snapshot={"snapshot_id": "official-2"},
                structured_repository=stale,
            )
        )

        repository = resolve_official_structured_repository(
            app,
            Path("data-root"),
            pointer_loader=lambda _path: {},
            repository_cls=_OfficialRepository,
        )

        self.assertIsNot(repository, stale)
        self.assertEqual(repository.snapshot_id, "official-2")
        self.assertEqual(app.state.structured_repository, repository)

    def test_resolve_official_structured_repository_raises_when_snapshot_unavailable(self):
        app = SimpleNamespace(state=SimpleNamespace())

        with self.assertRaises(StructuredQueryError) as ctx:
            resolve_official_structured_repository(
                app,
                Path("data-root"),
                pointer_loader=lambda _path: {},
                repository_cls=_OfficialRepository,
            )

        self.assertEqual(ctx.exception.code, "STRUCTURED_INDEX_UNAVAILABLE")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_resolve_structured_group_repository_builds_and_caches_repository(self):
        app = SimpleNamespace(state=SimpleNamespace())

        repository = resolve_structured_group_repository(
            app,
            Path("data-root"),
            "group-1",
            "7d_all",
            repository_cls=_Repository,
        )

        self.assertIsInstance(repository, _Repository)
        self.assertEqual(repository.group_id, "group-1")
        self.assertEqual(repository.scope, "7d_all")
        self.assertEqual(_Repository.calls, [(Path("data-root"), "group-1", "7d_all")])
        self.assertEqual(app.state.structured_group_repositories[("group-1", "7d_all")], repository)

    def test_resolve_structured_group_repository_reuses_matching_cached_repository(self):
        app = SimpleNamespace(state=SimpleNamespace())
        first = resolve_structured_group_repository(
            app,
            Path("data-root"),
            "group-1",
            "7d_all",
            repository_cls=_Repository,
        )
        second = resolve_structured_group_repository(
            app,
            Path("data-root"),
            "group-1",
            "7d_all",
            repository_cls=_Repository,
        )

        self.assertIs(second, first)
        self.assertEqual(_Repository.calls, [(Path("data-root"), "group-1", "7d_all")])

    def test_resolve_structured_group_repository_clears_stale_cache_on_new_repository(self):
        stale = _Repository(Path("old-root"), "old-group", "35d_all")
        app = SimpleNamespace(
            state=SimpleNamespace(
                structured_group_repositories={("old-group", "35d_all"): stale}
            )
        )

        repository = resolve_structured_group_repository(
            app,
            Path("data-root"),
            "group-1",
            "7d_all",
            repository_cls=_Repository,
        )

        self.assertIsNot(repository, stale)
        self.assertEqual(list(app.state.structured_group_repositories), [("group-1", "7d_all")])

    def test_build_dataset_catalog_payload_returns_unavailable_scopes_without_manifest(self):
        payload = build_dataset_catalog_payload(
            None,
            dataset_scopes=["7d_all", "7d_top_1000"],
            default_dataset_scope="7d_all",
            dataset_window_definitions=DATASET_WINDOW_DEFINITIONS,
            rag_scope_counts={},
            rag_scope_source_counts={},
            rag_source_limits={"deck": 150},
            rag_document_count_semantics="scope_sum_including_duplicates",
            rag_scope_count_semantics="bounded_evidence_documents",
            retrieval={},
            saturated_source_types=lambda *_: [],
        )

        self.assertEqual(payload["snapshot_group_id"], None)
        self.assertEqual(payload["default_dataset_scope"], "7d_all")
        self.assertEqual([item["dataset_scope"] for item in payload["datasets"]], ["7d_all", "7d_top_1000"])
        self.assertTrue(all(item["ready"] is False for item in payload["datasets"]))
        self.assertNotIn("rag", payload)

    def test_build_dataset_catalog_payload_merges_manifest_and_rag_stats(self):
        payload = build_dataset_catalog_payload(
            {
                "snapshot_group_id": "group-1",
                "published_at": "2026-08-16T00:00:00+00:00",
                "default_dataset_scope": "7d_all",
                "rag_document_count": 100,
                "fully_aligned": True,
                "datasets": {
                    "7d_all": {
                        "unique_battles": 12,
                        "structured_counts": {"full_loadout_side_records": 20},
                    },
                    "7d_top_1000": {
                        "ready": False,
                        "complete_loadout_ready": True,
                        "entity_stats_ready": True,
                        "delta_ready": True,
                    },
                },
            },
            dataset_scopes=["7d_all", "7d_top_1000", "35d_all"],
            default_dataset_scope="35d_all",
            dataset_window_definitions=DATASET_WINDOW_DEFINITIONS,
            rag_scope_counts={"7d_all": 5, "7d_top_1000": 2},
            rag_scope_source_counts={
                "7d_all": {"deck": 150, "matchup": 3},
                "7d_top_1000": {"deck": 1},
            },
            rag_source_limits={"deck": 150},
            rag_document_count_semantics="scope_sum_including_duplicates",
            rag_scope_count_semantics="bounded_evidence_documents",
            retrieval={"fusion_mode": "rrf", "candidate_top_k": 48},
            saturated_source_types=lambda counts: [
                source_type for source_type, count in (counts or {}).items() if count >= 150
            ],
        )

        self.assertEqual(payload["snapshot_group_id"], "group-1")
        self.assertEqual(payload["published_at"], "2026-08-16T00:00:00+00:00")
        self.assertTrue(payload["rag"]["fully_aligned"])
        self.assertEqual(payload["rag"]["document_count"], 100)
        self.assertEqual(payload["rag"]["scope_counts"]["7d_all"], 5)
        self.assertEqual(payload["rag"]["retrieval"], {"fusion_mode": "rrf", "candidate_top_k": 48})
        by_scope = {item["dataset_scope"]: item for item in payload["datasets"]}
        self.assertTrue(by_scope["7d_all"]["ready"])
        self.assertTrue(by_scope["7d_all"]["complete_loadout_ready"])
        self.assertEqual(by_scope["7d_all"]["rag_document_count"], 5)
        self.assertEqual(by_scope["7d_all"]["rag_source_counts"], {"deck": 150, "matchup": 3})
        self.assertEqual(by_scope["7d_all"]["rag_saturated_source_types"], ["deck"])
        self.assertFalse(by_scope["7d_top_1000"]["ready"])
        self.assertTrue(by_scope["7d_top_1000"]["complete_loadout_ready"])
        self.assertTrue(by_scope["7d_top_1000"]["entity_stats_ready"])
        self.assertTrue(by_scope["7d_top_1000"]["delta_ready"])
        self.assertFalse(by_scope["35d_all"]["ready"])
        self.assertEqual(by_scope["35d_all"]["rag_source_counts"], {})

    def test_dataset_scope_parts_splits_window_prefix_and_level(self):
        self.assertEqual(
            dataset_scope_parts("d14_21_top_2000", dataset_window_definitions=DATASET_WINDOW_DEFINITIONS),
            ("d14_21", "top_2000"),
        )

    def test_dataset_scope_display_name_matches_public_catalog_labels(self):
        self.assertEqual(
            dataset_scope_display_name("7d_all", dataset_window_definitions=DATASET_WINDOW_DEFINITIONS),
            "最近7天 · 全量",
        )
        self.assertEqual(
            dataset_scope_display_name("35d_top_1000", dataset_window_definitions=DATASET_WINDOW_DEFINITIONS),
            "最近35天 · 前1000",
        )

    def test_unavailable_dataset_payload_matches_catalog_contract(self):
        payload = unavailable_dataset_payload(
            "d7_14_top_2000",
            dataset_window_definitions=DATASET_WINDOW_DEFINITIONS,
        )

        self.assertEqual(payload["dataset_scope"], "d7_14_top_2000")
        self.assertEqual(payload["name"], "7至14天前 · 前2000")
        self.assertEqual(payload["window_days"], 7)
        self.assertEqual(payload["window_kind"], "historical_slice")
        self.assertEqual(payload["window_start_offset_days"], 7)
        self.assertEqual(payload["window_end_offset_days"], 14)
        self.assertEqual(payload["rank_limit"], 2000)
        self.assertFalse(payload["ready"])
        self.assertFalse(payload["complete_loadout_ready"])
        self.assertEqual(payload["rag_source_counts"], {})

    def test_validate_dataset_scope_uses_default_for_blank_values(self):
        self.assertEqual(
            validate_dataset_scope("", default_scope="35d_all", allowed_scopes=["7d_all", "35d_all"]),
            "35d_all",
        )
        self.assertEqual(
            validate_dataset_scope(None, default_scope="35d_all", allowed_scopes=["7d_all", "35d_all"]),
            "35d_all",
        )

    def test_validate_dataset_scope_trims_valid_values(self):
        self.assertEqual(
            validate_dataset_scope("  7d_all  ", default_scope="35d_all", allowed_scopes=["7d_all", "35d_all"]),
            "7d_all",
        )

    def test_validate_dataset_scope_rejects_unknown_values_with_contract_error(self):
        with self.assertRaises(StructuredQueryError) as raised:
            validate_dataset_scope("bad_scope", default_scope="35d_all", allowed_scopes=["7d_all", "35d_all"])

        self.assertEqual(raised.exception.code, "INVALID_DATASET_SCOPE")
        self.assertEqual(raised.exception.details["dataset_scope"], "bad_scope")
        self.assertEqual(raised.exception.details["allowed"], ["7d_all", "35d_all"])

    def test_load_active_snapshot_group_manifest_returns_none_when_pointer_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(load_active_snapshot_group_manifest(Path(tmpdir), allowed_scopes=["7d_all"]))

    def test_load_active_snapshot_group_manifest_returns_valid_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_id = "group-1"
            manifest = {
                "snapshot_group_id": group_id,
                "fully_aligned": True,
                "datasets": {"7d_all": {"ready": True}},
                "rag_docs_fingerprint": "fp-1",
                "index_docs_fingerprint": "fp-1",
            }
            (root / "active_snapshot_group.json").write_text(
                json.dumps({"snapshot_group_id": group_id}),
                encoding="utf-8",
            )
            manifest_dir = root / "snapshot_groups" / group_id
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            self.assertEqual(load_active_snapshot_group_manifest(root, allowed_scopes=["7d_all"]), manifest)

    def test_load_active_snapshot_group_manifest_reports_incomplete_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "active_snapshot_group.json").write_text(
                json.dumps({"snapshot_group_id": "missing-group"}),
                encoding="utf-8",
            )

            with self.assertRaises(StructuredQueryError) as raised:
                load_active_snapshot_group_manifest(root, allowed_scopes=["7d_all"])

        self.assertEqual(raised.exception.code, "DATASET_SCOPE_NOT_READY")
        self.assertEqual(raised.exception.status_code, 503)

    def test_load_active_snapshot_group_manifest_rejects_misaligned_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_id = "group-1"
            manifest = {
                "snapshot_group_id": group_id,
                "fully_aligned": False,
                "datasets": {"7d_all": {"ready": True}},
                "rag_docs_fingerprint": "fp-1",
                "index_docs_fingerprint": "fp-1",
            }
            (root / "active_snapshot_group.json").write_text(
                json.dumps({"snapshot_group_id": group_id}),
                encoding="utf-8",
            )
            manifest_dir = root / "snapshot_groups" / group_id
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(StructuredQueryError) as raised:
                load_active_snapshot_group_manifest(root, allowed_scopes=["7d_all"])

        self.assertEqual(raised.exception.code, "DATASET_SCOPE_NOT_READY")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.details["snapshot_group_id"], group_id)

    def test_rag_scope_stats_for_manifest_uses_published_counts(self):
        app = SimpleNamespace(state=SimpleNamespace())
        manifest = {
            "snapshot_group_id": "group-1",
            "rag_docs_fingerprint": "fp-1",
            "rag_scope_counts": {"7d_all": "3"},
            "rag_scope_source_counts": {"7d_all": {"deck": "2"}},
        }

        counts, source_counts = rag_scope_stats_for_manifest(
            app,
            manifest,
            Path("unused"),
            dataset_scopes=["7d_all", "35d_all"],
            summarize_scope_documents=lambda *_: self.fail("should not derive published counts"),
            warn=lambda *_: self.fail("should not warn for published counts"),
        )

        self.assertEqual(counts, {"7d_all": 3, "35d_all": 0})
        self.assertEqual(source_counts, {"7d_all": {"deck": 2}, "35d_all": {}})
        self.assertFalse(hasattr(app.state, "rag_scope_stats_cache"))

    def test_rag_scope_stats_for_manifest_derives_and_caches_legacy_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_id = "group-1"
            documents = [{"metadata": {"dataset_scope": "7d_all", "source_type": "deck"}}]
            docs_dir = root / "snapshot_groups" / group_id
            docs_dir.mkdir(parents=True)
            (docs_dir / "rag_documents.json").write_text(json.dumps(documents), encoding="utf-8")
            app = SimpleNamespace(state=SimpleNamespace())
            calls = []

            def summarize_scope_documents(documents_arg, scopes_arg):
                calls.append((documents_arg, scopes_arg))
                return {"7d_all": 1}, {"7d_all": {"deck": 1}}

            counts, source_counts = rag_scope_stats_for_manifest(
                app,
                {"snapshot_group_id": group_id, "rag_docs_fingerprint": "fp-1"},
                root,
                dataset_scopes=["7d_all"],
                summarize_scope_documents=summarize_scope_documents,
                warn=lambda *_: self.fail("should not warn for valid legacy docs"),
            )

        self.assertEqual(counts, {"7d_all": 1})
        self.assertEqual(source_counts, {"7d_all": {"deck": 1}})
        self.assertEqual(calls, [(documents, ["7d_all"])])
        self.assertEqual(app.state.rag_scope_stats_cache["counts"], {"7d_all": 1})

    def test_rag_scope_stats_for_manifest_returns_cached_copies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            app = SimpleNamespace(
                state=SimpleNamespace(
                    rag_scope_stats_cache={
                        "key": (str(root.resolve()), "group-1", "fp-1"),
                        "counts": {"7d_all": 1},
                        "source_counts": {"7d_all": {"deck": 1}},
                    }
                )
            )

            counts, source_counts = rag_scope_stats_for_manifest(
                app,
                {"snapshot_group_id": "group-1", "rag_docs_fingerprint": "fp-1"},
                root,
                dataset_scopes=["7d_all"],
                summarize_scope_documents=lambda *_: self.fail("should use cached stats"),
                warn=lambda *_: self.fail("should not warn for cached stats"),
            )

        counts["7d_all"] = 99
        source_counts["7d_all"]["deck"] = 99
        self.assertEqual(app.state.rag_scope_stats_cache["counts"], {"7d_all": 1})
        self.assertEqual(app.state.rag_scope_stats_cache["source_counts"], {"7d_all": {"deck": 1}})

    def test_rag_scope_stats_for_manifest_warns_and_uses_empty_docs_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            group_id = "group-1"
            docs_dir = root / "snapshot_groups" / group_id
            docs_dir.mkdir(parents=True)
            (docs_dir / "rag_documents.json").write_text("{not-json", encoding="utf-8")
            app = SimpleNamespace(state=SimpleNamespace())
            warnings = []
            calls = []

            def summarize_scope_documents(documents_arg, scopes_arg):
                calls.append((documents_arg, scopes_arg))
                return {"7d_all": 0}, {"7d_all": {}}

            counts, source_counts = rag_scope_stats_for_manifest(
                app,
                {"snapshot_group_id": group_id, "rag_docs_fingerprint": "fp-1"},
                root,
                dataset_scopes=["7d_all"],
                summarize_scope_documents=summarize_scope_documents,
                warn=lambda snapshot_group_id: warnings.append(snapshot_group_id),
            )

        self.assertEqual(warnings, [group_id])
        self.assertEqual(calls, [([], ["7d_all"])])
        self.assertEqual(counts, {"7d_all": 0})
        self.assertEqual(source_counts, {"7d_all": {}})


if __name__ == "__main__":
    unittest.main()
