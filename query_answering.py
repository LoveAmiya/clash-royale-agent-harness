import json
import logging

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel

from answer_builder import build_retrieval_query
from app_config import (
    COMPRESS_CHAR_BUDGET,
    COMPRESS_MAX_ITEMS,
    RERANK_TOP_N,
    RETRIEVAL_ALPHA,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    SILICONFLOW_BASE_URL,
    SILICONFLOW_MODEL_NAME,
)
from harness.executor import SkillExecutor
from hybrid_retriever import HybridRetriever
from planner.planner import RuleBasedPlanner
from query_parser import extract_text_content
from retrieval_postprocess import (
    build_context_and_refs,
    compress_results,
    rerank_results,
)
from skills.base import SkillContext
from skills.registry import build_default_registry


logger = logging.getLogger(__name__)


def build_reviewer_model(api_key: str) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name=SILICONFLOW_MODEL_NAME,
        api_key=api_key,
        client_kwargs={"base_url": SILICONFLOW_BASE_URL},
        stream=False,
    )


async def build_rag_answer(
    user_text: str,
    parsed: dict,
    retriever: HybridRetriever,
    source_type: str,
    reviewer_model: OpenAIChatModel,
) -> str:
    retrieval_query = build_retrieval_query(parsed, user_text)

    results = retriever.hybrid_search(
        query=retrieval_query,
        top_k_bm25=RETRIEVAL_TOP_K_BM25,
        top_k_dense=RETRIEVAL_TOP_K_DENSE,
        final_top_k=RETRIEVAL_FINAL_TOP_K,
        alpha=RETRIEVAL_ALPHA,
        source_type=source_type,
    )

    if not results:
        return "我不知道，当前数据里没有检索到足够相关的信息。\n参考来源：无"

    reranked = rerank_results(
        question=user_text,
        parsed=parsed,
        results=results,
        top_n=RERANK_TOP_N,
    )

    if not reranked:
        return "我不知道，当前数据里没有足够高相关的候选结果。\n参考来源：无"

    compressed = compress_results(
        reranked,
        max_items=COMPRESS_MAX_ITEMS,
        char_budget=COMPRESS_CHAR_BUDGET,
    )

    if not compressed:
        return "我不知道，当前数据里没有足够可用的压缩上下文。\n参考来源：无"

    retrieved_context, refs_text = build_context_and_refs(compressed)

    reviewer_agent = ReActAgent(
        name="Reviewer",
        sys_prompt=(
            "你是一个严格基于检索证据回答问题的助手。\n"
            "规则：\n"
            "1. 只能根据提供的检索证据回答。\n"
            "2. 如果证据不足，必须明确说“我不知道”或“当前数据里没有”。\n"
            "3. 回答要简洁清楚。\n"
            "4. 回答末尾必须保留“参考来源：”部分。\n"
            "5. 不要输出推理过程。"
        ),
        model=reviewer_model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )
    reviewer_agent.set_console_output_enabled(enabled=False)

    grounded_question = (
        f"用户问题：{user_text}\n\n"
        f"结构化参数：{json.dumps(parsed, ensure_ascii=False)}\n\n"
        "下面是经过召回、重排序和压缩后的候选证据，请严格基于这些证据作答：\n\n"
        f"{retrieved_context}\n\n"
        f"请在回答末尾附上：\n参考来源：\n{refs_text}\n\n"
        "如果这些证据仍不足以回答问题，请明确说“我不知道”或“当前数据里没有”。"
    )

    grounded_msg = Msg(name="user", role="user", content=grounded_question)
    result = await reviewer_agent(grounded_msg)
    return extract_text_content(result)


DIRECT_SKILL_REGISTRY = build_default_registry(
    rag_answer_builder=build_rag_answer,
    reviewer_model_builder=build_reviewer_model,
)
SKILL_EXECUTOR = SkillExecutor(DIRECT_SKILL_REGISTRY)
RULE_BASED_PLANNER = RuleBasedPlanner()


async def answer_query(
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
) -> str:
    intent = parsed["intent"]
    context = SkillContext(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        retriever=retriever,
        api_key=api_key,
        metadata={},
    )
    plan = RULE_BASED_PLANNER.build_plan(context)
    if plan is not None:
        context.metadata["plan"] = plan.to_dict()
    answer = await SKILL_EXECUTOR.execute(context)
    if answer is not None:
        logger.info("answer route intent=%s mode=skill_executor", intent)
        return answer

    logger.info("answer route intent=reject mode=fallback")
    return (
        "当前系统主要支持：\n"
        "- 赛程查询\n"
        "- 下一轮对战/谁上场查询\n"
        "- 热门卡组查询\n"
        "- 单卡使用率/胜率查询\n"
        "- 卡牌排行榜查询"
    )
