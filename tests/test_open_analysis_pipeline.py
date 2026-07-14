import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import install_test_stubs

install_test_stubs()

from harness.trace import TraceEvent, TraceRecorder
from hybrid_retriever import HybridRetriever
from rag_data_builder import build_strategy_docs
from runtime_multi import query_needs_rag, split_stream_chunks
from query_answering import AnswerResult
from skills.base import SkillContext
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill


class OpenAnalysisRoutingTests(unittest.TestCase):
    def test_open_analysis_intents_require_rag(self):
        self.assertTrue(query_needs_rag({"intent": "meta_analysis_query"}))
        self.assertTrue(query_needs_rag({"intent": "match_preparation_query"}))

    def test_stream_chunks_are_bounded_and_reconstruct_answer(self):
        answer = "这是一个需要逐段显示的回答，用于证明 SSE 不会只发送一整块内容。"
        chunks = list(split_stream_chunks(answer, chunk_size=10))

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), answer)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))


class TraceReadTests(unittest.TestCase):
    def test_trace_recorder_reads_only_requested_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = TraceRecorder(log_path=Path(tmpdir) / "traces.jsonl")
            recorder.record(TraceEvent("trace-a", "SUCCESS", "SkillA", "card_query", "direct"))
            recorder.record(TraceEvent("trace-b", "SUCCESS", "SkillB", "deck_query", "rag"))

            events = recorder.read_trace("trace-b")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["selected_skill"], "SkillB")


class RetrievalFallbackTests(unittest.TestCase):
    def test_hybrid_retriever_degrades_to_bm25_when_dense_index_fails(self):
        docs = [
            {
                "doc_id": "strategy_air",
                "source_type": "strategy",
                "text": "空军防守要保留对空单位和范围法术。",
                "metadata": {},
            }
        ]
        with patch.object(HybridRetriever, "_build_dense_index", side_effect=RuntimeError("ollama unavailable")):
            retriever = HybridRetriever(docs)

        results = retriever.hybrid_search("空军防守", final_top_k=1)

        self.assertFalse(retriever.dense_available)
        self.assertEqual(results[0]["doc"]["doc_id"], "strategy_air")
        self.assertEqual(results[0]["retrieval_mode"], "bm25_only")

    def test_strategy_documents_are_part_of_the_retrieval_corpus(self):
        docs = build_strategy_docs()

        self.assertTrue(docs)
        self.assertTrue(all(doc["source_type"] == "strategy" for doc in docs))
        self.assertTrue(all(doc["metadata"]["source"] for doc in docs))


class EvidenceSynthesisRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_analysis_requires_retriever_before_calling_model(self):
        skill = EvidenceSynthesisSkill(answer_builder=lambda **kwargs: "should not run")
        context = SkillContext(
            user_text="空军防守怎么准备？",
            parsed={"intent": "match_preparation_query"},
            schedule_data=[],
            top_decks_data=[],
            cards_meta_data=[],
            retriever=None,
            api_key="test-key",
        )

        answer = await skill.run(context)

        self.assertIn("检索", answer)


class ProcessSSETests(unittest.IsolatedAsyncioTestCase):
    async def test_process_sends_progress_trace_and_multiple_content_events(self):
        import runtime_multi

        result = AnswerResult(
            answer="这是一个足够长的 SSE 回答，用于确认后端会拆分多个内容事件发送给浏览器。" * 4,
            trace_id="trace-test",
            parsed={"intent": "meta_analysis_query", "parse_source": "local_rule"},
            plan={"steps": []},
            selected_skill="EvidenceSynthesisSkill",
            mode="rag_synthesis",
            metadata={},
        )
        request = runtime_multi.ProcessRequest(
            input=[{"role": "user", "content": [{"type": "text", "text": "测试"}]}]
        )

        with patch.object(runtime_multi, "build_answer", AsyncMock(return_value=result)), patch.object(
            runtime_multi,
            "read_trace",
            return_value=[{"state": "SUCCESS", "latency_ms": 12, "success": True}],
        ):
            response = await runtime_multi.process(request)
            payload = ""
            async for chunk in response.body_iterator:
                payload += chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk

        events = [json.loads(frame[6:]) for frame in payload.split("\n\n") if frame.startswith("data: ")]
        objects = [event["object"] for event in events]
        content_events = [event for event in events if event["object"] == "content"]

        self.assertIn("progress", objects)
        self.assertIn("trace", objects)
        self.assertGreater(len(content_events), 1)
        self.assertLess(objects.index("progress"), objects.index("content"))

    async def test_open_query_initializes_retriever_off_the_event_loop(self):
        import runtime_multi

        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[],
                schedule_data=[],
                top_decks_data=[],
                retriever=None,
            )
        )
        result = AnswerResult(
            answer="ok",
            trace_id="trace-test",
            parsed={"intent": "meta_analysis_query"},
            plan=None,
            selected_skill="EvidenceSynthesisSkill",
            mode="rag_synthesis",
            metadata={},
        )

        with patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "meta_analysis_query"}),
        ), patch.object(runtime_multi.asyncio, "to_thread", AsyncMock(return_value=object())) as to_thread, patch.object(
            runtime_multi,
            "answer_query",
            AsyncMock(return_value=result),
        ):
            answer = await runtime_multi.build_answer("开放问题", app)

        self.assertIs(answer, result)
        to_thread.assert_awaited_once_with(runtime_multi.ensure_retriever, app)
