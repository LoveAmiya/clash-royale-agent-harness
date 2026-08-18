import unittest
from unittest.mock import Mock
from unittest.mock import AsyncMock

from support import install_test_stubs

install_test_stubs()

import app_config  # noqa: F401 - loads the src package path for direct package imports.
from clashroyale_agent.qa.retrieval_orchestration import (
    RetrievalConfig,
    retrieve_meta_candidates,
    summarize_retrieval,
)
from clashroyale_agent.qa.reviewer_models import ReviewerModelConfig, build_reviewer_model
from clashroyale_agent.qa.traces import read_trace
from clashroyale_agent.qa.multi_intent_answering import (
    MultiIntentDependencies,
    answer_multi_intent_query,
    compose_multi_intent_answer,
)


class ReviewerModelBoundaryTests(unittest.TestCase):
    def test_build_reviewer_model_uses_responses_configuration(self):
        response_model = Mock(name="OpenAIResponseModel")
        chat_model = Mock(name="OpenAIChatModel")

        build_reviewer_model(
            "test-key",
            config=ReviewerModelConfig(
                model_name="reviewer-model",
                client_kwargs={"timeout": 30},
                reasoning_effort="medium",
                wire_api="responses",
            ),
            chat_model_cls=chat_model,
            response_model_cls=response_model,
        )

        response_model.assert_called_once_with(
            model_name="reviewer-model",
            api_key="test-key",
            stream=False,
            client_kwargs={"timeout": 30},
            reasoning_effort="medium",
        )
        chat_model.assert_not_called()

    def test_build_reviewer_model_uses_chat_completions_configuration(self):
        response_model = Mock(name="OpenAIResponseModel")
        chat_model = Mock(name="OpenAIChatModel")

        build_reviewer_model(
            "test-key",
            config=ReviewerModelConfig(
                model_name="reviewer-model",
                client_kwargs={},
                reasoning_effort="low",
                wire_api="chat_completions",
            ),
            chat_model_cls=chat_model,
            response_model_cls=response_model,
        )

        chat_model.assert_called_once_with(
            model_name="reviewer-model",
            api_key="test-key",
            stream=False,
            client_kwargs={},
            reasoning_effort="low",
        )
        response_model.assert_not_called()


class RetrievalOrchestrationBoundaryTests(unittest.TestCase):
    def test_retrieve_meta_candidates_uses_global_and_typed_lanes(self):
        class FakeRetriever:
            dense_available = True

            def __init__(self):
                self.source_types = []
                self.calls = []

            def embed_text(self, query):
                self.embedded_query = query
                return [1.0, 2.0]

            def hybrid_search(self, **kwargs):
                self.source_types.append(kwargs["source_type"])
                self.calls.append(kwargs)
                name = kwargs["source_type"] or "global"
                return [
                    {
                        "doc": {"doc_id": f"doc:{name}", "text": f"{name} evidence"},
                        "final_score": 1.0,
                        "candidate_pool": {"bm25": 1},
                    }
                ]

        retriever = FakeRetriever()
        results, lanes = retrieve_meta_candidates(
            retriever,
            "query text",
            dataset_scope="7d_all",
            deck_mode="base8",
            entity_mode="base8",
            config=RetrievalConfig(
                top_k_bm25=10,
                top_k_dense=5,
                final_top_k=4,
                alpha=0.5,
                meta_lane_top_k=3,
                lane_source_types=("archetype", "deck_profile"),
            ),
        )

        self.assertEqual(retriever.source_types, [None, "archetype", "deck_profile"])
        self.assertEqual(lanes, ["global", "archetype", "deck_profile"])
        self.assertEqual(len(results), 3)
        self.assertEqual(retriever.calls[0]["final_top_k"], 4)
        self.assertTrue(all(call["final_top_k"] == 3 for call in retriever.calls[1:]))
        self.assertTrue(all(call["query_vector"] == [1.0, 2.0] for call in retriever.calls))

    def test_summarize_retrieval_reports_bounded_diagnostics(self):
        summary = summarize_retrieval(
            [
                {
                    "doc": {"text": "private evidence text"},
                    "retrieval_mode": "hybrid",
                    "fusion_mode": "rrf",
                    "rrf_k": 60,
                    "retrieval_lane_candidate_pools": {"global": {"bm25": 2}},
                }
            ],
            lanes=["global"],
        )

        self.assertEqual(summary["retrieval_mode"], "hybrid")
        self.assertEqual(summary["fusion_mode"], "rrf")
        self.assertEqual(summary["candidate_count"], 1)
        self.assertEqual(summary["lane_candidate_pools"], {"global": {"bm25": 2}})
        self.assertNotIn("private evidence text", repr(summary))


class TraceBoundaryTests(unittest.TestCase):
    def test_read_trace_ignores_missing_trace_id(self):
        recorder = Mock()

        self.assertEqual(read_trace(None, recorder=recorder), [])

        recorder.read_trace.assert_not_called()

    def test_read_trace_delegates_to_recorder(self):
        recorder = Mock()
        recorder.read_trace.return_value = [{"event": "ok"}]

        self.assertEqual(read_trace("trace-1", recorder=recorder), [{"event": "ok"}])

        recorder.read_trace.assert_called_once_with("trace-1")


class MultiIntentAnsweringBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_compose_multi_intent_answer_preserves_titles_and_failure_text(self):
        answer = compose_multi_intent_answer(
            [
                {"title": "Card", "status": "success", "answer": "card answer"},
                {"title": "Meta", "status": "failed", "answer": "", "error": "missing retriever"},
            ]
        )

        self.assertEqual(
            answer,
            "## Card\ncard answer\n\n## Meta\n无法完成：missing retriever",
        )

    async def test_answer_multi_intent_query_deduplicates_and_preserves_order(self):
        class Result:
            def __init__(
                self,
                answer,
                trace_id,
                parsed,
                plan,
                selected_skill,
                mode,
                metadata,
                sub_results=None,
            ):
                self.answer = answer
                self.trace_id = trace_id
                self.parsed = parsed
                self.plan = plan
                self.selected_skill = selected_skill
                self.mode = mode
                self.metadata = metadata
                self.sub_results = sub_results or []

        recorder = Mock()
        recorder.new_trace_id.return_value = "trace-1"
        execute = AsyncMock(
            side_effect=[
                {
                    "id": "q1",
                    "title": "first",
                    "parsed": {"id": "q1", "intent": "card_query"},
                    "plan": None,
                    "selected_skill": "CardMetaSkill",
                    "mode": "direct",
                    "status": "success",
                    "answer": "first answer",
                    "metadata": {"model_stream": "unavailable"},
                    "error": None,
                    "latency_ms": 1,
                },
                {
                    "id": "q2",
                    "title": "second",
                    "parsed": {"id": "q2", "intent": "meta_analysis_query"},
                    "plan": None,
                    "selected_skill": "EvidenceSynthesisSkill",
                    "mode": "rag_synthesis",
                    "status": "success",
                    "answer": "second answer",
                    "metadata": {"model_stream": "fallback_chunked"},
                    "error": None,
                    "latency_ms": 2,
                },
            ]
        )
        parsed = {
            "intent": "multi_intent",
            "subqueries": [
                {"id": "q1", "intent": "card_query"},
                {"id": "q1-copy", "intent": "card_query"},
                {"id": "q2", "intent": "meta_analysis_query"},
            ],
        }

        result = await answer_multi_intent_query(
            user_text="compound",
            parsed=parsed,
            schedule_data=[],
            top_decks_data=[],
            cards_meta_data=[],
            retriever=None,
            api_key="test-key",
            dependencies=MultiIntentDependencies(
                answer_result_cls=Result,
                execute_subquery=execute,
                recorder=recorder,
                subquery_semantic_key=lambda item: (item.get("intent"),),
            ),
        )

        self.assertEqual(execute.await_count, 2)
        self.assertEqual([item["id"] for item in result.sub_results], ["q1", "q2"])
        self.assertEqual(result.trace_id, "trace-1")
        self.assertEqual(result.selected_skill, "MultiIntentOrchestrator")
        self.assertEqual(result.metadata["model_stream"], "fallback_chunked")


if __name__ == "__main__":
    unittest.main()
