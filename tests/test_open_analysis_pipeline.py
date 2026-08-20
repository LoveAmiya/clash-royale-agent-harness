import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from support import install_test_stubs

install_test_stubs()

from harness.trace import TraceEvent, TraceRecorder
from hybrid_retriever import HybridRetriever
from rag_data_builder import build_strategy_docs
from runtime_events import RuntimeEventEmitter
from runtime_multi import emit_semantic_content, query_needs_rag, split_answer_semantic_chunks, split_stream_chunks
import query_answering
from query_answering import AnswerResult, DATA_ANALYSIS_SYSTEM_PROMPT, answer_query
from skills.base import SkillContext
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill


class OpenAnalysisRoutingTests(unittest.TestCase):
    def test_analysis_prompt_has_no_clan_war_or_gameplay_scope(self):
        self.assertIn("数据分析助手", DATA_ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("不得提供具体打法", DATA_ANALYSIS_SYSTEM_PROMPT)
        self.assertNotIn("战队赛分析助手", DATA_ANALYSIS_SYSTEM_PROMPT)

    def test_open_analysis_intents_require_rag(self):
        self.assertTrue(query_needs_rag({"intent": "meta_analysis_query"}))
        self.assertFalse(query_needs_rag({"intent": "match_preparation_query"}))

    def test_stream_chunks_are_bounded_and_reconstruct_answer(self):
        answer = "这是一个需要逐段显示的回答，用于证明 SSE 不会只发送一整块内容。"
        chunks = list(split_stream_chunks(answer, chunk_size=10))

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), answer)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))

    def test_semantic_chunks_keep_structured_answer_sections_together(self):
        answer = "## 卡牌数据：Electro Giant\n- 使用率：4.0%\n- 胜率：64.4%\n\n数据边界：官方样本\n\n参考来源：\n[1] Supercell API"

        chunks = list(split_answer_semantic_chunks(answer))

        self.assertEqual("".join(chunks), answer)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0], "## 卡牌数据：Electro Giant\n")
        self.assertIn("使用率：4.0%", chunks[1])
        self.assertTrue(chunks[2].startswith("数据边界"))


class SemanticContentPacingTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_sections_are_spaced_without_changing_text(self):
        emitter = RuntimeEventEmitter()
        answer = "标题\n- 指标\n\n数据边界：样本\n\n参考来源：\n[1] 官方"

        with patch("runtime_multi.asyncio.sleep", AsyncMock()) as sleep:
            await emit_semantic_content(emitter, answer)

        events = []
        while not emitter.empty():
            events.append(await emitter.next_event())
        self.assertEqual("".join(event["text"] for event in events), answer)
        self.assertEqual(sleep.await_count, len(events) - 1)


class BufferedAnswerEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_buffered_single_intent_keeps_execution_event_sink(self):
        emitter = RuntimeEventEmitter()
        observed = {}

        async def execute(context):
            observed["event_sink"] = context.event_sink
            observed["stream_content"] = context.stream_content
            return "answer"

        with patch.object(query_answering.SKILL_EXECUTOR, "execute", side_effect=execute):
            await answer_query(
                user_text="当前环境如何",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=object(),
                api_key="test-key",
                event_sink=emitter,
                stream_content=False,
            )

        self.assertIs(observed["event_sink"], emitter)
        self.assertFalse(observed["stream_content"])


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
    def test_default_index_path_resolves_qdrant_root_when_retriever_is_created(self):
        class FakeQdrant:
            def __init__(self, *args, **kwargs):
                pass

            def close(self):
                return None

        docs = [{
            "doc_id": "snapshot-late-env:overview",
            "source_type": "snapshot",
            "text": "Isolated test evidence",
            "metadata": {"snapshot_id": "snapshot-late-env"},
        }]
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"CR_AGENT_QDRANT_ROOT": temp_dir},
        ), patch("hybrid_retriever.QdrantClient", FakeQdrant), patch.object(
            HybridRetriever, "_build_dense_index"
        ):
            retriever = HybridRetriever(docs)

        self.assertEqual(retriever.index_path, Path(temp_dir) / "snapshot-late-env")

    def test_snapshot_index_is_reused_without_reembedding_after_restart(self):
        class FakeQdrant:
            collections_by_path = {}

            def __init__(self, *args, **kwargs):
                path = kwargs.get("path") or (args[0] if args else ":memory:")
                self.collections = self.collections_by_path.setdefault(str(path), set())

            def collection_exists(self, name):
                return name in self.collections

            def delete_collection(self, name):
                self.collections.discard(name)

            def create_collection(self, collection_name, vectors_config):
                self.collections.add(collection_name)

            def upsert(self, collection_name, points):
                return None

        docs = [
            {
                "doc_id": "snapshot-1:overview",
                "source_type": "snapshot",
                "text": "Official daily snapshot",
                "metadata": {"snapshot_id": "snapshot-1", "source": "Supercell API live sample"},
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir, patch("hybrid_retriever.QdrantClient", FakeQdrant), patch.object(
            HybridRetriever, "embed_texts", return_value=[[0.0] * 1024]
        ) as embed:
            first = HybridRetriever(docs, index_path=Path(temp_dir))
            second = HybridRetriever(docs, index_path=Path(temp_dir))

        self.assertTrue(first.dense_available)
        self.assertTrue(second.dense_available)
        self.assertEqual(embed.call_count, 1)

    def test_vector_index_is_reused_for_equivalent_new_snapshot_documents(self):
        class FakeQdrant:
            collections_by_path = {}

            def __init__(self, *args, **kwargs):
                path = kwargs.get("path") or (args[0] if args else ":memory:")
                self.collections = self.collections_by_path.setdefault(str(path), set())

            def collection_exists(self, name):
                return name in self.collections

            def delete_collection(self, name):
                self.collections.discard(name)

            def create_collection(self, collection_name, vectors_config):
                self.collections.add(collection_name)

            def upsert(self, collection_name, points):
                return None

        first_docs = [{
            "doc_id": "snapshot-1:overview",
            "source_type": "snapshot",
            "text": "Stable scoped evidence",
            "metadata": {"snapshot_id": "snapshot-1", "dataset_scope": "7d_all"},
        }]
        second_docs = [{
            "doc_id": "snapshot-2:overview",
            "source_type": "snapshot",
            "text": "Stable scoped evidence",
            "metadata": {"snapshot_id": "snapshot-2", "dataset_scope": "7d_all"},
        }]
        with tempfile.TemporaryDirectory() as temp_dir, patch("hybrid_retriever.QdrantClient", FakeQdrant), patch.object(
            HybridRetriever, "embed_texts", return_value=[[0.0] * 1024]
        ) as embed:
            HybridRetriever(first_docs, index_path=Path(temp_dir))
            second = HybridRetriever(second_docs, index_path=Path(temp_dir))

        self.assertTrue(second.reused_persisted_index)
        self.assertEqual(embed.call_count, 1)

    def test_dense_index_batches_embeddings_and_qdrant_upserts(self):
        class FakeQdrant:
            def __init__(self, *args, **kwargs):
                self.collections = set()
                self.upserts = []

            def collection_exists(self, name):
                return name in self.collections

            def delete_collection(self, name):
                self.collections.discard(name)

            def create_collection(self, collection_name, vectors_config):
                self.collections.add(collection_name)

            def upsert(self, collection_name, points):
                self.upserts.append(list(points))

        docs = [
            {
                "doc_id": f"snapshot-1:{index}",
                "source_type": "card_profile",
                "text": f"Official evidence {index}",
                "metadata": {"snapshot_id": "snapshot-1"},
            }
            for index in range(5)
        ]
        embed = Mock(side_effect=lambda texts: [[float(index)] * 1024 for index, _ in enumerate(texts)])

        with tempfile.TemporaryDirectory() as temp_dir, patch("hybrid_retriever.QdrantClient", FakeQdrant), patch(
            "hybrid_retriever.EMBED_BATCH_SIZE", 2
        ), patch.object(HybridRetriever, "embed_texts", embed):
            retriever = HybridRetriever(docs, index_path=Path(temp_dir))

        self.assertTrue(retriever.dense_available)
        self.assertEqual([call.args[0] for call in embed.call_args_list], [[doc["text"] for doc in docs[:2]], [doc["text"] for doc in docs[2:4]], [docs[4]["text"]]])
        self.assertEqual([len(points) for points in retriever.qdrant.upserts], [2, 2, 1])

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
            retriever = HybridRetriever(docs, in_memory=True)

        results = retriever.hybrid_search("空军防守", final_top_k=1)

        self.assertFalse(retriever.dense_available)
        self.assertEqual(results[0]["doc"]["doc_id"], "strategy_air")
        self.assertEqual(results[0]["retrieval_mode"], "bm25_only")

    def test_hybrid_retriever_filters_documents_by_dataset_scope(self):
        docs = [
            {
                "doc_id": "group:7d:card",
                "source_type": "card",
                "text": "Fireball usage evidence",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all"},
            },
            {
                "doc_id": "group:35d:card",
                "source_type": "card",
                "text": "Fireball usage evidence",
                "metadata": {"snapshot_id": "group", "dataset_scope": "35d_all"},
            },
        ]
        with patch.object(HybridRetriever, "_build_dense_index", side_effect=RuntimeError("offline")):
            retriever = HybridRetriever(docs, in_memory=True)

        results = retriever.hybrid_search("Fireball usage", dataset_scope="35d_all")

        self.assertEqual([item["doc"]["doc_id"] for item in results], ["group:35d:card"])

    def test_dense_filter_pushes_scope_and_source_type_into_qdrant(self):
        query_filter = HybridRetriever._build_dense_filter(
            dataset_scope="7d_all",
            source_type="archetype",
        )

        conditions = {condition.key: condition.match.value for condition in query_filter.must}
        self.assertEqual(
            conditions,
            {
                "metadata.dataset_scope": "7d_all",
                "source_type": "archetype",
            },
        )

    def test_hybrid_retriever_filters_deck_evidence_by_mode_but_keeps_shared_evidence(self):
        docs = [
            {
                "doc_id": "base",
                "source_type": "deck",
                "text": "same evidence",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all", "deck_mode": "base8"},
            },
            {
                "doc_id": "full",
                "source_type": "full_loadout",
                "text": "same evidence",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all", "deck_mode": "full_loadout"},
            },
            {
                "doc_id": "shared",
                "source_type": "card",
                "text": "same evidence",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all"},
            },
        ]
        with patch.object(HybridRetriever, "_build_dense_index", side_effect=RuntimeError("offline")):
            retriever = HybridRetriever(docs, in_memory=True)

        results = retriever.hybrid_search(
            "same evidence", dataset_scope="7d_all", deck_mode="full_loadout", final_top_k=5
        )

        self.assertEqual({item["doc"]["doc_id"] for item in results}, {"full", "shared"})

    def test_rrf_fusion_prefers_documents_recalled_by_both_lanes(self):
        docs = [
            {
                "doc_id": "bm25-only",
                "source_type": "card",
                "text": "fireball fireball fireball spell pressure",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all"},
            },
            {
                "doc_id": "balanced",
                "source_type": "card",
                "text": "fireball spell answer",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all"},
            },
            {
                "doc_id": "dense-only",
                "source_type": "deck_profile",
                "text": "miner poison control",
                "metadata": {"snapshot_id": "group", "dataset_scope": "7d_all"},
            },
        ]

        with patch.object(HybridRetriever, "_build_dense_index", side_effect=RuntimeError("offline")):
            retriever = HybridRetriever(docs, in_memory=True)
        retriever.dense_available = True
        retriever.dense_search = Mock(
            return_value=[
                {"internal_id": 2, "score": 0.99, "doc": docs[2]},
                {"internal_id": 1, "score": 0.80, "doc": docs[1]},
            ]
        )

        results = retriever.hybrid_search(
            "fireball spell",
            top_k_bm25=2,
            top_k_dense=2,
            final_top_k=3,
            fusion_mode="rrf",
            query_vector=[0.0] * 1024,
        )

        self.assertEqual(results[0]["doc"]["doc_id"], "balanced")
        self.assertEqual(results[0]["fusion_mode"], "rrf")
        self.assertEqual(results[0]["bm25_rank"], 2)
        self.assertEqual(results[0]["dense_rank"], 2)
        self.assertEqual(results[0]["candidate_pool"]["bm25"], 2)
        self.assertEqual(results[0]["candidate_pool"]["dense"], 2)
        self.assertEqual(len(retriever.dense_search.call_args.kwargs["query_vector"]), 1024)

    def test_lazy_bm25_keeps_only_two_scope_indexes(self):
        documents = [
            {
                "doc_id": f"doc-{scope}",
                "source_type": "snapshot",
                "text": f"dataset evidence {scope}",
                "metadata": {"snapshot_id": "group", "dataset_scope": scope},
            }
            for scope in ("7d_all", "35d_all", "7d_top_100")
        ]
        with patch.object(HybridRetriever, "_build_dense_index", side_effect=RuntimeError("offline")):
            retriever = HybridRetriever(
                documents,
                in_memory=True,
                lazy_scope_bm25=True,
                bm25_scope_cache_size=2,
            )
        retriever.bm25_search("evidence", dataset_scope="7d_all")
        retriever.bm25_search("evidence", dataset_scope="35d_all")
        retriever.bm25_search("evidence", dataset_scope="7d_top_100")

        self.assertEqual(len(retriever._bm25_scope_cache), 2)
        self.assertNotIn("7d_all", retriever._bm25_scope_cache)

    def test_strategy_documents_are_part_of_the_retrieval_corpus(self):
        docs = build_strategy_docs()

        self.assertTrue(docs)
        self.assertTrue(all(doc["source_type"] == "strategy" for doc in docs))
        self.assertTrue(all(doc["metadata"]["source"] for doc in docs))


class EvidenceSynthesisRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_analysis_requires_retriever_before_calling_model(self):
        skill = EvidenceSynthesisSkill(answer_builder=lambda **kwargs: "should not run")
        context = SkillContext(
            user_text="当前环境以哪些体系为主？",
            parsed={"intent": "meta_analysis_query"},
            schedule_data=[],
            top_decks_data=[],
            cards_meta_data=[],
            retriever=None,
            api_key="test-key",
        )

        answer = await skill.run(context)

        self.assertIn("检索", answer)


class ProcessSSETests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # This suite supplies its own retriever state. Isolate it from a real
        # rolling snapshot group that may be active in the local workspace.
        import runtime_multi

        rolling_manifest = patch.object(runtime_multi, "_active_snapshot_group_manifest", return_value=None)
        rolling_manifest.start()
        self.addCleanup(rolling_manifest.stop)
        retriever = patch.object(
            runtime_multi,
            "ensure_dataset_retriever",
            side_effect=lambda app, _dataset_scope: getattr(app.state, "retriever", None),
        )
        retriever.start()
        self.addCleanup(retriever.stop)

    async def test_process_forwards_validated_page_intent_to_answer_pipeline(self):
        import runtime_multi

        result = AnswerResult(
            answer="环境分析",
            trace_id="trace-page-intent",
            parsed={"intent": "meta_analysis_query", "parse_source": "interface_contract"},
            plan=None,
            selected_skill="EvidenceSynthesisSkill",
            mode="rag_synthesis",
            metadata={},
        )
        request = runtime_multi.ProcessRequest(
            intent_hint="meta_analysis_query",
            input=[{"role": "user", "content": [{"type": "text", "text": "分析当前环境"}]}],
        )

        with patch.object(runtime_multi, "build_answer", AsyncMock(return_value=result)) as build_answer, patch.object(
            runtime_multi, "read_trace", return_value=[]
        ):
            response = await runtime_multi.process(request)
            _ = [chunk async for chunk in response.body_iterator]

        self.assertEqual(build_answer.await_args.kwargs["intent_hint"], "meta_analysis_query")

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

        async def build_with_events(*_args, event_sink=None, **_kwargs):
            self.assertIsNotNone(event_sink)
            await event_sink.execution(
                step_id="parse",
                phase="parse",
                status="running",
                title="正在解析问题",
                detail="调用模型解析结构化意图",
            )
            await event_sink.content("先到达的正文", delta=True)
            await event_sink.content("，第二个正文块", delta=True)
            return result

        with patch.object(runtime_multi, "build_answer", side_effect=build_with_events), patch.object(
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
        self.assertIn("execution", objects)
        self.assertIn("trace", objects)
        self.assertGreater(len(content_events), 1)
        self.assertLess(objects.index("execution"), objects.index("content"))
        self.assertLess(objects.index("content"), objects.index("trace"))

    async def test_process_sends_fallback_content_before_trace_when_no_skill_emits_content(self):
        import runtime_multi

        result = AnswerResult(
            answer="结构化结果已经完成。\n\n数据边界：官方快照。",
            trace_id="trace-fallback",
            parsed={"intent": "card_query", "parse_source": "llm_parser"},
            plan={"steps": []},
            selected_skill="CardMetaSkill",
            mode="direct",
            metadata={},
        )
        request = runtime_multi.ProcessRequest(
            input=[{"role": "user", "content": [{"type": "text", "text": "测试"}]}]
        )

        with patch.object(runtime_multi, "build_answer", AsyncMock(return_value=result)), patch.object(
            runtime_multi, "read_trace", return_value=[]
        ):
            response = await runtime_multi.process(request)
            payload = ""
            async for chunk in response.body_iterator:
                payload += chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk

        events = [json.loads(frame[6:]) for frame in payload.split("\n\n") if frame.startswith("data: ")]
        objects = [event["object"] for event in events]
        self.assertIn("content", objects)
        self.assertLess(objects.index("content"), objects.index("trace"))

    async def test_open_query_never_initializes_retriever_on_the_request_path(self):
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
        to_thread.assert_not_awaited()

    async def test_environment_page_intent_skips_model_parser_but_keeps_rag_answering(self):
        import runtime_multi

        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[], bootstrap_cards_meta_data=[], schedule_data=[], top_decks_data=[],
                card_deck_stats_data={}, retriever=object(), live_snapshot=None, live_snapshot_at=0.0,
                live_error=None, rag_status="ready", rag_snapshot_id="snapshot-1",
                rag_docs_fingerprint="fingerprint-1",
            )
        )
        result = AnswerResult(
            answer="环境 RAG 回答",
            trace_id="trace-page-intent",
            parsed={"intent": "meta_analysis_query"},
            plan=None,
            selected_skill="EvidenceSynthesisSkill",
            mode="rag_synthesis",
            metadata={},
        )

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "EXTERNAL_API_REQUIRED", True
        ), patch.object(runtime_multi, "query_requires_official_snapshot", return_value=False), patch.object(
            runtime_multi, "parse_user_query", AsyncMock()
        ) as parser, patch.object(runtime_multi, "answer_query", AsyncMock(return_value=result)) as answer_query:
            answer = await runtime_multi.build_answer(
                "分析当前环境",
                app,
                intent_hint="meta_analysis_query",
            )

        parser.assert_not_awaited()
        self.assertEqual(answer_query.await_args.kwargs["parsed"]["intent"], "meta_analysis_query")
        self.assertTrue(answer_query.await_args.kwargs["stream_content"])
        self.assertEqual(answer.metadata["parser_api"]["status"], "interface_contract")
        self.assertEqual(answer.answer, "环境 RAG 回答")
