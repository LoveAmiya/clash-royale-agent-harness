import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import install_test_stubs, sample_cards, sample_decks, sample_schedule

install_test_stubs()

from skills.base import SkillContext
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill
from skills.meta_evidence import build_meta_evidence_pack
from skills.registry import SkillRegistry
from harness.executor import SkillExecutor
from harness.trace import TraceRecorder
import query_answering
from query_answering import build_snapshot_fallback_answer
from runtime_events import RuntimeEventEmitter


class MetaEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = sample_schedule()
        cls.deck_data = sample_decks()
        cls.card_data = sample_cards()

    def test_evidence_pack_contains_local_facts_and_source_urls(self):
        evidence, sources = build_meta_evidence_pack(
            self.schedule_data,
            self.deck_data,
            self.card_data,
        )

        self.assertIn(self.deck_data[0]["deck_name"], evidence)
        self.assertIn("Fireball", evidence)
        self.assertIn("unit-test fixture", evidence)
        self.assertIn("top_decks.json", sources)

    def test_snapshot_fallback_exposes_facts_and_data_boundary(self):
        answer = build_snapshot_fallback_answer(
            self.deck_data,
            self.card_data,
        )

        self.assertIn(self.deck_data[0]["deck_name"], answer)
        self.assertIn("使用率靠前的卡牌", answer)
        self.assertIn("数据边界", answer)
        self.assertIn("不是 LLM 的策略推演", answer)
    def test_evidence_pack_labels_supercell_records_as_live_sources(self):
        evidence, sources = build_meta_evidence_pack(
            [],
            [{"rank": 1, "deck_name": "Live Deck", "source": "Supercell API live sample"}],
            [{"rank": 1, "card_name": "Zap", "usage_rate": 10, "source": "Supercell API live sample"}],
        )

        self.assertIn("Supercell API live sample", evidence)
        self.assertNotIn("repository static snapshot", evidence)
        self.assertIn("Supercell API live sample", sources)
        self.assertNotIn("top_decks.json", sources)
        self.assertNotIn("cards_meta.json", sources)

    def test_live_evidence_pack_includes_snapshot_sample_boundary(self):
        evidence, _ = build_meta_evidence_pack(
            [],
            [{
                "rank": 1,
                "deck_name": "Live Deck",
                "source": "Supercell API live sample",
                "snapshot_id": "supercell-test",
                "sample_battles": 20000,
                "target_battles": 20000,
                "fetched_at": "2026-07-26T17:43:32+00:00",
            }],
            [],
        )

        self.assertIn("snapshot_id=supercell-test", evidence)
        self.assertIn("sample_battles=20000 场", evidence)
        self.assertIn("fetched_at=2026-07-26T17:43:32+00:00", evidence)


class EvidenceSynthesisSkillTests(unittest.IsolatedAsyncioTestCase):
    def build_context(self, intent: str, api_key: str = "test-key") -> SkillContext:
        return SkillContext(
            user_text="根据当前热门卡组制定战队赛备战策略",
            parsed={"intent": intent},
            schedule_data=[{"round": 1, "status": "upcoming"}],
            top_decks_data=[{"rank": 1, "deck_name": "Deck A"}],
            cards_meta_data=[{"rank": 1, "card_name": "Card A", "usage_rate": 10}],
            retriever=object(),
            api_key=api_key,
        )

    async def test_routes_meta_analysis_to_configured_builder(self):
        calls = []

        async def builder(**kwargs):
            calls.append(kwargs)
            return "模型综合结论"

        skill = EvidenceSynthesisSkill(answer_builder=builder)
        answer = await skill.run(self.build_context("meta_analysis_query"))

        self.assertEqual(answer, "模型综合结论")
        self.assertEqual(calls[0]["user_text"], "根据当前热门卡组制定战队赛备战策略")
        self.assertEqual(calls[0]["api_key"], "test-key")

    async def test_requires_api_key_instead_of_using_old_template(self):
        skill = EvidenceSynthesisSkill(answer_builder=lambda **kwargs: "should not run")
        answer = await skill.run(self.build_context("meta_analysis_query", api_key=""))

        self.assertIn("OPENAI_API_KEY", answer)

    async def test_trace_marks_evidence_synthesis_mode(self):
        async def builder(**kwargs):
            return "模型综合结论"

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "traces.jsonl"
            executor = SkillExecutor(
                SkillRegistry([EvidenceSynthesisSkill(answer_builder=builder)]),
                recorder=TraceRecorder(log_path=log_path),
            )

            answer = await executor.execute(self.build_context("meta_analysis_query"))

            self.assertEqual(answer, "模型综合结论")
            last_event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last_event["mode"], "rag_synthesis")

    async def test_strict_mode_does_not_replace_failed_rag_model_call_with_snapshot_answer(self):
        class StaticRetriever:
            def hybrid_search(self, *args, **kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "bm25_only",
                        "doc": {
                            "doc_id": "strategy_fixture",
                            "source_type": "strategy",
                            "text": "Use reliable air defense and maintain spell cycle discipline.",
                            "metadata": {"title": "Fixture", "scope": "test", "source": "fixture"},
                        },
                    }
                ]

        metadata = {}
        with patch.object(query_answering, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            query_answering, "uses_responses_api", return_value=True
        ), patch.object(
            query_answering, "generate_model_text", AsyncMock(side_effect=RuntimeError("model unavailable"))
        ):
            with self.assertRaisesRegex(RuntimeError, "RAG model API call failed"):
                await query_answering.build_evidence_synthesis_answer(
                    user_text="Analyze the current environment.",
                    parsed={"intent": "meta_analysis_query"},
                    schedule_data=[],
                    top_decks_data=[{"rank": 1, "deck_name": "Deck A"}],
                    cards_meta_data=[{"rank": 1, "card_name": "Zap", "usage_rate": 10}],
                    retriever=StaticRetriever(),
                    api_key="test-key",
                    metadata=metadata,
                )
        self.assertEqual(metadata["model_generation"], "unavailable")

    async def test_strict_mode_retrieves_only_active_snapshot_documents(self):
        class RecordingRetriever:
            def __init__(self):
                self.kwargs = None

            def hybrid_search(self, **kwargs):
                self.kwargs = kwargs
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "snapshot_fixture",
                            "source_type": "snapshot",
                            "text": "Official daily snapshot evidence.",
                            "metadata": {"snapshot_id": "fixture", "source": "Supercell API live sample"},
                        },
                    }
                ]

        retriever = RecordingRetriever()
        with patch.object(query_answering, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            query_answering, "uses_responses_api", return_value=True
        ), patch.object(query_answering, "generate_model_text", AsyncMock(return_value="Grounded answer")):
            await query_answering.build_evidence_synthesis_answer(
                user_text="Analyze the current environment.",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=retriever,
                api_key="test-key",
                metadata={"dataset_scope": "35d_top_500"},
            )

        self.assertIsNone(retriever.kwargs["source_type"])
        self.assertEqual(retriever.kwargs["dataset_scope"], "35d_top_500")

    async def test_environment_change_query_retrieves_only_meta_delta_documents(self):
        class RecordingRetriever:
            def __init__(self):
                self.kwargs = None

            def hybrid_search(self, **kwargs):
                self.kwargs = kwargs
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "delta_fixture",
                            "source_type": "meta_delta",
                            "text": "相邻七天环境变化证据。",
                            "metadata": {
                                "snapshot_id": "fixture",
                                "source": "Supercell API live sample",
                                "baseline_scope": "d7_14_all",
                            },
                        },
                    }
                ]

        retriever = RecordingRetriever()
        with patch.object(query_answering, "uses_responses_api", return_value=True), patch.object(
            query_answering, "generate_model_text", AsyncMock(return_value="环境变化结论。")
        ):
            await query_answering.build_evidence_synthesis_answer(
                user_text="最近环境发生了什么变化？",
                parsed={"intent": "meta_analysis_query", "analysis_type": "meta_delta"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=retriever,
                api_key="test-key",
                metadata={"dataset_scope": "7d_all"},
            )

        self.assertEqual(retriever.kwargs["source_type"], "meta_delta")
        self.assertEqual(retriever.kwargs["dataset_scope"], "7d_all")

    async def test_streaming_evidence_synthesis_accumulates_public_model_deltas(self):
        class StaticRetriever:
            def hybrid_search(self, **_kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "snapshot_fixture",
                            "source_type": "snapshot",
                            "text": "Official snapshot evidence.",
                            "metadata": {"snapshot_id": "fixture", "source": "Supercell API live sample"},
                        },
                    }
                ]

        async def model_stream(**_kwargs):
            yield "第一段结论。"
            yield "第二段结论。"

        emitter = RuntimeEventEmitter()
        metadata = {}
        with patch.object(query_answering, "uses_responses_api", return_value=True), patch.object(
            query_answering, "generate_model_text_stream", model_stream
        ):
            answer = await query_answering.build_evidence_synthesis_answer(
                user_text="分析当前环境。",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=StaticRetriever(),
                api_key="test-key",
                metadata=metadata,
                event_sink=emitter,
                stream_content=True,
            )

        events = []
        while not emitter.empty():
            events.append(await emitter.next_event())
        streamed_text = "".join(event["text"] for event in events if event["object"] == "content")
        span_names = {
            event.get("span_name")
            for event in events
            if event["object"] == "execution" and event.get("status") == "completed"
        }

        self.assertIn("第一段结论。第二段结论。", answer)
        self.assertEqual(streamed_text, answer)
        self.assertEqual(metadata["model_stream"], "streaming")
        self.assertTrue({"retrieval", "rerank", "review", "synthesis", "validation"}.issubset(span_names))

    async def test_streaming_evidence_synthesis_drops_unsupported_numeric_sentence(self):
        class StaticRetriever:
            def hybrid_search(self, **_kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "snapshot_fixture",
                            "source_type": "snapshot",
                            "text": "Official snapshot usage rate 4.3%.",
                            "metadata": {"snapshot_id": "fixture", "source": "Supercell API live sample"},
                        },
                    }
                ]

        async def model_stream(**_kwargs):
            yield "可保留的环境结论。"
            yield "使用率 99.9%。"
            yield "仍可保留的配卡结论。"

        emitter = RuntimeEventEmitter()
        metadata = {}
        with patch.object(query_answering, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            query_answering, "uses_responses_api", return_value=True
        ), patch.object(query_answering, "generate_model_text_stream", model_stream):
            answer = await query_answering.build_evidence_synthesis_answer(
                user_text="分析当前环境。",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=StaticRetriever(),
                api_key="test-key",
                metadata=metadata,
                event_sink=emitter,
                stream_content=True,
            )

        self.assertIn("可保留的环境结论", answer)
        self.assertIn("仍可保留的配卡结论", answer)
        self.assertNotIn("99.9%", answer)
        self.assertIn("未通过证据校验", answer)
        self.assertEqual(metadata["grounding_sentences_dropped"], 1)

    async def test_completed_evidence_synthesis_drops_unsupported_numeric_sentence(self):
        class StaticRetriever:
            def hybrid_search(self, **_kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "snapshot_fixture",
                            "source_type": "snapshot",
                            "text": "Official snapshot usage rate 4.3%.",
                            "metadata": {"snapshot_id": "fixture", "source": "Supercell API live sample"},
                        },
                    }
                ]

        metadata = {}
        with patch.object(query_answering, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            query_answering, "uses_responses_api", return_value=True
        ), patch.object(
            query_answering,
            "generate_model_text",
            AsyncMock(return_value="可保留的环境结论。使用率 99.9%。仍可保留的配卡结论。"),
        ):
            answer = await query_answering.build_evidence_synthesis_answer(
                user_text="分析当前环境。",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=StaticRetriever(),
                api_key="test-key",
                metadata=metadata,
                stream_content=False,
            )

        self.assertIn("可保留的环境结论", answer)
        self.assertIn("仍可保留的配卡结论", answer)
        self.assertNotIn("99.9%", answer)
        self.assertEqual(metadata["grounding_sentences_dropped"], 1)

    async def test_evidence_synthesis_marks_completed_result_fallback_when_stream_is_unavailable(self):
        class StaticRetriever:
            def hybrid_search(self, **_kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "snapshot_fixture",
                            "source_type": "snapshot",
                            "text": "Official snapshot evidence.",
                            "metadata": {"snapshot_id": "fixture", "source": "Supercell API live sample"},
                        },
                    }
                ]

        async def unavailable_stream(**_kwargs):
            raise RuntimeError("stream is not supported")
            yield "unreachable"

        emitter = RuntimeEventEmitter()
        metadata = {}
        with patch.object(query_answering, "uses_responses_api", return_value=True), patch.object(
            query_answering, "generate_model_text_stream", unavailable_stream
        ), patch.object(query_answering, "generate_model_text", AsyncMock(return_value="完成后返回的结论。")):
            answer = await query_answering.build_evidence_synthesis_answer(
                user_text="分析当前环境。",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=StaticRetriever(),
                api_key="test-key",
                metadata=metadata,
                event_sink=emitter,
                stream_content=True,
            )

        events = []
        while not emitter.empty():
            events.append(await emitter.next_event())
        streamed_text = "".join(event["text"] for event in events if event["object"] == "content")
        execution_details = [event.get("detail", "") for event in events if event["object"] == "execution"]

        self.assertEqual(metadata["model_stream"], "fallback_chunked")
        self.assertEqual(streamed_text, answer)
        self.assertIn("完成后返回的结论。", answer)
        self.assertTrue(any("分段" in detail for detail in execution_details))

    async def test_meta_analysis_excludes_local_schedule_from_model_evidence(self):
        class StaticRetriever:
            def hybrid_search(self, **kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "retrieval_mode": "hybrid",
                        "doc": {
                            "doc_id": "strategy_fixture",
                            "source_type": "strategy",
                            "text": "Use reliable air defense.",
                            "metadata": {"title": "Fixture", "scope": "test", "source": "fixture"},
                        },
                    }
                ]

        with patch.object(query_answering, "uses_responses_api", return_value=True), patch.object(
            query_answering, "generate_model_text", AsyncMock(return_value="Grounded answer")
        ) as generate:
            answer = await query_answering.build_evidence_synthesis_answer(
                user_text="Analyze the current environment.",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[{"round": 1, "status": "upcoming", "opponent_team": "Private Team"}],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=StaticRetriever(),
                api_key="test-key",
            )

        self.assertNotIn("schedule.json", answer)
        self.assertNotIn("赛程信息", generate.await_args.kwargs["input_text"])
        self.assertNotIn("Private Team", generate.await_args.kwargs["input_text"])
