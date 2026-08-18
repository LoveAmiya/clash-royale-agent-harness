import asyncio
import types
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.lifecycle import (
    initialize_runtime_data_state,
    initialize_runtime_services,
    record_api_startup_baseline,
    shutdown_runtime_resources,
)


class ApiLifecycleTests(unittest.TestCase):
    def test_initialize_runtime_services_sets_startup_service_state(self):
        calls = []
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        class Metrics:
            pass

        class RecentAnswers:
            def __init__(self, *, max_items, ttl_seconds):
                self.max_items = max_items
                self.ttl_seconds = ttl_seconds

        class Feedback:
            def __init__(self, database_path, *, max_correction_chars, answer_ttl_seconds):
                self.database_path = database_path
                self.max_correction_chars = max_correction_chars
                self.answer_ttl_seconds = answer_ttl_seconds

        class Quota:
            async def probe(self):
                calls.append("probe")

        async def run():
            await initialize_runtime_services(
                app,
                runtime_metrics_factory=Metrics,
                recent_answer_cache_factory=RecentAnswers,
                feedback_store_factory=Feedback,
                process_quota_factory=lambda **kwargs: calls.append(kwargs) or Quota(),
                feedback_db_file="feedback.sqlite3",
                feedback_cache_max_items=5,
                feedback_cache_ttl_seconds=60,
                feedback_max_correction_chars=4000,
                process_quota_backend="memory",
                process_max_concurrent=2,
                process_rate_limit_per_minute=30,
                redis_url="redis://example",
                process_quota_lease_seconds=120,
                process_quota_key_prefix="test",
                process_quota_fail_mode="closed",
            )

        asyncio.run(run())

        self.assertIsInstance(app.state.runtime_metrics, Metrics)
        self.assertEqual(app.state.recent_answers.max_items, 5)
        self.assertEqual(app.state.recent_answers.ttl_seconds, 60)
        self.assertEqual(app.state.feedback_store.database_path, "feedback.sqlite3")
        self.assertEqual(app.state.feedback_store.max_correction_chars, 4000)
        self.assertEqual(app.state.feedback_store.answer_ttl_seconds, 60)
        self.assertIsNotNone(app.state.process_quota)
        self.assertEqual(calls[0]["backend"], "memory")
        self.assertEqual(calls[0]["max_concurrent"], 2)
        self.assertEqual(calls[0]["requests_per_minute"], 30)
        self.assertEqual(calls[0]["redis_url"], "redis://example")
        self.assertEqual(calls[0]["lease_seconds"], 120)
        self.assertEqual(calls[0]["key_prefix"], "test")
        self.assertEqual(calls[0]["fail_mode"], "closed")
        self.assertEqual(calls[1], "probe")

    def test_initialize_runtime_data_state_sets_private_snapshot_defaults(self):
        locks = []
        bootstrap_cards = [{"name": "Knight"}]
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        def lock_factory():
            lock = f"lock-{len(locks)}"
            locks.append(lock)
            return lock

        initialize_runtime_data_state(
            app,
            bootstrap_cards_meta_data=bootstrap_cards,
            live_sample_target_battles=200000,
            lock_factory=lock_factory,
        )

        self.assertEqual(app.state.schedule_data, [])
        self.assertEqual(app.state.bootstrap_top_decks_data, [])
        self.assertIs(app.state.bootstrap_cards_meta_data, bootstrap_cards)
        self.assertEqual(app.state.top_decks_data, [])
        self.assertEqual(app.state.cards_meta_data, bootstrap_cards)
        self.assertIsNot(app.state.cards_meta_data, bootstrap_cards)
        self.assertEqual(app.state.card_deck_stats_data, {})
        self.assertIsNone(app.state.retriever)
        self.assertIsNone(app.state.rolling_retriever)
        self.assertIsNone(app.state.rolling_retriever_group_id)
        self.assertEqual(app.state.structured_group_repositories, {})
        self.assertEqual(app.state.rag_status, "not_required")
        self.assertIsNone(app.state.rag_snapshot_id)
        self.assertIsNone(app.state.rag_docs_fingerprint)
        self.assertIsNone(app.state.rag_document_validation)
        self.assertEqual(app.state.rag_candidate_status, "not_ready")
        self.assertIsNone(app.state.rag_candidate_error)
        self.assertIsNone(app.state.rag_candidate_validation)
        self.assertIsNone(app.state.rag_error)
        self.assertIsNone(app.state.rag_quality_report)
        self.assertIsNone(app.state.rag_preheat_task)
        self.assertIsNone(app.state.live_snapshot)
        self.assertEqual(app.state.live_snapshot_at, 0.0)
        self.assertIsNone(app.state.live_error)
        self.assertEqual(app.state.live_sample_target_battles, 200000)
        self.assertIsNone(app.state.live_snapshot_target_battles)
        self.assertIsNone(app.state.live_refresh_task)
        self.assertEqual(app.state.live_refresh_status, "missing")
        self.assertEqual(app.state.live_battle_log_cache, {})
        self.assertEqual(app.state.live_cooldown_until, 0.0)
        self.assertEqual(app.state.live_refresh_failures, 0)
        self.assertIsNone(app.state.live_last_refresh_attempt)
        self.assertEqual(app.state.rolling_retriever_lock, "lock-0")
        self.assertEqual(app.state.rag_preheat_lock, "lock-1")
        self.assertEqual(app.state.live_refresh_lock, "lock-2")
        self.assertIsNone(app.state.api_startup_baseline)
        self.assertIsNone(app.state.rag_preheat_baseline)

    def test_record_api_startup_baseline_records_non_negative_duration(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        record_api_startup_baseline(app, started_at=100.0, completed_at=101.234)

        self.assertEqual(app.state.api_startup_baseline, {"elapsed_seconds": 1.234})


class ApiLifecycleShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_runtime_resources_cancels_tasks_and_closes_resources(self):
        events = []

        async def wait_until_cancelled(name):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                events.append(f"{name}_cancelled")
                raise

        live_task = asyncio.create_task(wait_until_cancelled("live"))
        rag_task = asyncio.create_task(wait_until_cancelled("rag"))
        await asyncio.sleep(0)

        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_refresh_task=live_task,
                rag_preheat_task=rag_task,
                rolling_retriever=types.SimpleNamespace(close=lambda: events.append("rolling_closed")),
                process_quota=types.SimpleNamespace(close=lambda: async_append(events, "quota_closed")),
            )
        )

        await shutdown_runtime_resources(app)

        self.assertTrue(live_task.cancelled())
        self.assertTrue(rag_task.cancelled())
        self.assertEqual(events, ["live_cancelled", "rag_cancelled", "rolling_closed", "quota_closed"])

    async def test_shutdown_runtime_resources_ignores_missing_optional_resources(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_refresh_task=None,
                rag_preheat_task=None,
                rolling_retriever=None,
                process_quota=None,
            )
        )

        await shutdown_runtime_resources(app)


async def async_append(events, value):
    events.append(value)


if __name__ == "__main__":
    unittest.main()
