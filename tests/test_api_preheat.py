import threading
import types
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.preheat import (
    acquire_rag_preheat_lock,
    find_active_rag_retriever,
    find_reusable_rag_retriever,
    resolve_rag_preheat_target,
    run_rag_preheat_in_thread,
)
from clashroyale_agent.api.rag_preheat import record_rag_preheat_baseline


class ApiPreheatTests(unittest.IsolatedAsyncioTestCase):
    def test_record_rag_preheat_baseline_records_elapsed_and_outcome(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        record_rag_preheat_baseline(
            app,
            started_at=10.0,
            completed_at=12.75,
            outcome="ready",
            snapshot_id="snapshot-1",
        )

        self.assertEqual(
            app.state.rag_preheat_baseline,
            {"elapsed_seconds": 2.75, "outcome": "ready", "snapshot_id": "snapshot-1"},
        )

    def test_acquire_rag_preheat_lock_creates_missing_lock_and_acquires_it(self):
        created = []
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        def lock_factory():
            lock = threading.Lock()
            created.append(lock)
            return lock

        lock = acquire_rag_preheat_lock(app, lock_factory=lock_factory)
        try:
            self.assertEqual(created, [lock])
            self.assertIs(app.state.rag_preheat_lock, lock)
            self.assertFalse(lock.acquire(blocking=False))
        finally:
            lock.release()

    def test_acquire_rag_preheat_lock_returns_none_when_existing_lock_is_held(self):
        existing = threading.Lock()
        existing.acquire()
        app = types.SimpleNamespace(state=types.SimpleNamespace(rag_preheat_lock=existing))

        try:
            lock = acquire_rag_preheat_lock(
                app,
                lock_factory=lambda: self.fail("existing lock should be reused"),
            )
        finally:
            existing.release()

        self.assertIsNone(lock)
        self.assertIs(app.state.rag_preheat_lock, existing)

    def test_resolve_rag_preheat_target_prefers_candidate_snapshot(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(live_snapshot={"snapshot_id": "live-snapshot"}, rag_status="ready")
        )
        candidate_snapshot = {"snapshot_id": "candidate-snapshot"}

        target = resolve_rag_preheat_target(app, candidate_snapshot=candidate_snapshot)

        self.assertIsNotNone(target)
        self.assertIs(target.snapshot, candidate_snapshot)
        self.assertEqual(target.snapshot_id, "candidate-snapshot")
        self.assertEqual(app.state.rag_status, "ready")

    def test_resolve_rag_preheat_target_falls_back_to_live_snapshot(self):
        live_snapshot = {"snapshot_id": "live-snapshot"}
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot=live_snapshot, rag_status="ready"))

        target = resolve_rag_preheat_target(app, candidate_snapshot=None)

        self.assertIsNotNone(target)
        self.assertIs(target.snapshot, live_snapshot)
        self.assertEqual(target.snapshot_id, "live-snapshot")

    def test_resolve_rag_preheat_target_marks_not_ready_when_snapshot_id_is_missing(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot={"snapshot_id": ""}, rag_status="ready"))

        target = resolve_rag_preheat_target(app, candidate_snapshot=None)

        self.assertIsNone(target)
        self.assertEqual(app.state.rag_status, "not_ready")

    def test_find_reusable_rag_retriever_accepts_aligned_active_index(self):
        retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                retriever=retriever,
                rag_snapshot_id="snapshot-1",
                rag_docs_fingerprint="fingerprint-1",
            )
        )

        reusable = find_reusable_rag_retriever(
            app,
            snapshot_id="snapshot-1",
            docs_fingerprint="fingerprint-1",
            activate_snapshot=False,
        )

        self.assertIs(reusable, retriever)

    def test_find_reusable_rag_retriever_rejects_misaligned_or_activation_builds(self):
        retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")
        defaults = {
            "app": types.SimpleNamespace(
                state=types.SimpleNamespace(
                    retriever=retriever,
                    rag_snapshot_id="snapshot-1",
                    rag_docs_fingerprint="fingerprint-1",
                )
            ),
            "snapshot_id": "snapshot-1",
            "docs_fingerprint": "fingerprint-1",
            "activate_snapshot": False,
        }
        cases = [
            {"activate_snapshot": True},
            {"snapshot_id": "snapshot-2"},
            {"docs_fingerprint": "fingerprint-2"},
            {
                "app": types.SimpleNamespace(
                    state=types.SimpleNamespace(
                        retriever=None,
                        rag_snapshot_id="snapshot-1",
                        rag_docs_fingerprint="fingerprint-1",
                    )
                )
            },
            {
                "app": types.SimpleNamespace(
                    state=types.SimpleNamespace(
                        retriever=types.SimpleNamespace(docs_fingerprint="fingerprint-2"),
                        rag_snapshot_id="snapshot-1",
                        rag_docs_fingerprint="fingerprint-1",
                    )
                )
            },
        ]

        for case in cases:
            with self.subTest(case=case):
                values = {**defaults, **case}
                self.assertIsNone(find_reusable_rag_retriever(**values))

    def test_find_active_rag_retriever_accepts_current_ready_snapshot(self):
        retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                retriever=retriever,
                rag_snapshot_id="snapshot-1",
                rag_status="ready",
                live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-1"},
                rag_docs_fingerprint="fingerprint-1",
            )
        )

        active = find_active_rag_retriever(app, active_snapshot_id="snapshot-1")

        self.assertIs(active, retriever)

    def test_find_active_rag_retriever_rejects_misaligned_state(self):
        retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")
        defaults = {
            "app": types.SimpleNamespace(
                state=types.SimpleNamespace(
                    retriever=retriever,
                    rag_snapshot_id="snapshot-1",
                    rag_status="ready",
                    live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-1"},
                    rag_docs_fingerprint="fingerprint-1",
                )
            ),
            "active_snapshot_id": "snapshot-1",
        }
        cases = [
            {"active_snapshot_id": "snapshot-2"},
            {
                "app": types.SimpleNamespace(
                    state=types.SimpleNamespace(
                        retriever=None,
                        rag_snapshot_id="snapshot-1",
                        rag_status="ready",
                        live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-1"},
                        rag_docs_fingerprint="fingerprint-1",
                    )
                )
            },
            {
                "app": types.SimpleNamespace(
                    state=types.SimpleNamespace(
                        retriever=retriever,
                        rag_snapshot_id="snapshot-1",
                        rag_status="building",
                        live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-1"},
                        rag_docs_fingerprint="fingerprint-1",
                    )
                )
            },
            {
                "app": types.SimpleNamespace(
                    state=types.SimpleNamespace(
                        retriever=retriever,
                        rag_snapshot_id="snapshot-1",
                        rag_status="ready",
                        live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-2"},
                        rag_docs_fingerprint="fingerprint-1",
                    )
                )
            },
            {
                "app": types.SimpleNamespace(
                    state=types.SimpleNamespace(
                        retriever=types.SimpleNamespace(docs_fingerprint="fingerprint-2"),
                        rag_snapshot_id="snapshot-1",
                        rag_status="ready",
                        live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-1"},
                        rag_docs_fingerprint="fingerprint-1",
                    )
                )
            },
        ]

        for case in cases:
            with self.subTest(case=case):
                values = {**defaults, **case}
                self.assertIsNone(find_active_rag_retriever(**values))

    async def test_run_rag_preheat_in_thread_passes_arguments_off_event_loop(self):
        caller_thread_id = threading.get_ident()
        calls = []
        app = types.SimpleNamespace(state=types.SimpleNamespace())
        candidate_snapshot = {"snapshot_id": "snapshot-1"}

        def preheat(received_app, *, candidate_snapshot=None, activate_snapshot=False):
            calls.append(
                {
                    "app": received_app,
                    "candidate_snapshot": candidate_snapshot,
                    "activate_snapshot": activate_snapshot,
                    "thread_id": threading.get_ident(),
                }
            )
            return "ignored"

        result = await run_rag_preheat_in_thread(
            preheat,
            app,
            candidate_snapshot=candidate_snapshot,
            activate_snapshot=True,
        )

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["app"], app)
        self.assertIs(calls[0]["candidate_snapshot"], candidate_snapshot)
        self.assertTrue(calls[0]["activate_snapshot"])
        self.assertNotEqual(calls[0]["thread_id"], caller_thread_id)

    async def test_run_rag_preheat_in_thread_uses_safe_defaults(self):
        calls = []
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        def preheat(received_app, *, candidate_snapshot=None, activate_snapshot=False):
            calls.append((received_app, candidate_snapshot, activate_snapshot))

        await run_rag_preheat_in_thread(preheat, app)

        self.assertEqual(calls, [(app, None, False)])


if __name__ == "__main__":
    unittest.main()
