import json
import logging
import asyncio
from dataclasses import dataclass

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from answer_builder import build_retrieval_query
from app_config import (
    COMPRESS_CHAR_BUDGET,
    COMPRESS_MAX_ITEMS,
    RERANK_TOP_N,
    RETRIEVAL_ALPHA,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    OPENAI_CLIENT_KWARGS,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    SYNTHESIS_REASONING_EFFORT,
    OPENAI_WIRE_API,
    MODEL_CALL_TIMEOUT_SECONDS,
)
from harness.executor import SkillExecutor
from hybrid_retriever import HybridRetriever
from model_gateway import generate_model_text, uses_responses_api
from planner.planner import RuleBasedPlanner
from query_parser import extract_text_content
from retrieval_postprocess import (
    build_context_and_refs,
    compress_results,
    rerank_results,
)
from skills.base import SkillContext
from skills.meta_evidence import build_meta_evidence_pack
from skills.registry import build_default_registry


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AnswerResult:
    answer: str
    trace_id: str | None
    parsed: dict
    plan: dict | None
    selected_skill: str | None
    mode: str | None
    metadata: dict


def build_reviewer_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    common_kwargs = {
        "model_name": OPENAI_MODEL,
        "api_key": api_key,
        "stream": False,
        "client_kwargs": OPENAI_CLIENT_KWARGS,
    }
    if OPENAI_WIRE_API == "responses":
        return OpenAIResponseModel(
            **common_kwargs,
            reasoning_effort=OPENAI_REASONING_EFFORT,
        )
    if OPENAI_WIRE_API == "chat_completions":
        return OpenAIChatModel(
            **common_kwargs,
            reasoning_effort=OPENAI_REASONING_EFFORT,
        )
    raise ValueError(f"Unsupported OPENAI_WIRE_API: {OPENAI_WIRE_API}")


def build_snapshot_fallback_answer(
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
) -> str:
    """模型不可用时，直接整理快照事实，不把本地榜单伪装成实时 Meta 推演。"""
    top_decks = sorted(top_decks_data, key=lambda item: item.get("rank", 10**9))[:5]
    top_cards = sorted(
        cards_meta_data,
        key=lambda item: float(item.get("usage_rate", 0) or 0),
        reverse=True,
    )[:5]

    deck_lines = [
        f"- 第 {item.get('rank')} 名：{item.get('deck_name', '未命名卡组')}（平均费用 {item.get('avg_elixir', '未知')}）"
        for item in top_decks
    ]
    card_lines = [
        f"- {item.get('card_name', '未命名卡牌')}：使用率 {item.get('usage_rate', '未知')}%，胜率 {item.get('win_rate', '未知')}%"
        for item in top_cards
    ]

    return (
        "模型服务暂不可用，以下是基于本地数据快照的直接整理，不是 LLM 的策略推演。\n\n"
        "本地排行榜前五卡组：\n"
        + ("\n".join(deck_lines) if deck_lines else "- 当前没有可用的卡组快照。")
        + "\n\n"
        "快照中使用率靠前的卡牌：\n"
        + ("\n".join(card_lines) if card_lines else "- 当前没有可用的卡牌快照。")
        + "\n\n"
        "数据边界：该项目保存的是排行榜和单卡静态快照，不含全量卡组使用率分布、版本更新时间或实时对局样本；"
        "因此可以展示榜单前列构筑，但不能严谨断言它们就是整个实时环境中占比最高的流派。"
    )


async def build_rag_answer(
    user_text: str,
    parsed: dict,
    retriever: HybridRetriever,
    source_type: str,
    reviewer_model: OpenAIChatModel,
    api_key: str = "",
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

    reviewer_instructions = """你是一个严格基于检索证据回答问题的助手。
规则：
1. 只能根据提供的检索证据回答。
2. 如果证据不足，必须明确说“我不知道”或“当前数据里没有”。
3. 回答要简洁清楚，末尾必须保留“参考来源：”部分。
4. 不要输出推理过程。"""
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
    try:
        if uses_responses_api():
            if not api_key:
                return "当前无法调用模型完成检索综合，请先设置 OPENAI_API_KEY 后重试。\n参考来源：\n" + refs_text
            return await asyncio.wait_for(
                generate_model_text(
                    api_key=api_key,
                    instructions=reviewer_instructions,
                    input_text=grounded_question,
                    reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                ),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
        result = await asyncio.wait_for(reviewer_agent(grounded_msg), timeout=MODEL_CALL_TIMEOUT_SECONDS)
        return extract_text_content(result)
    except asyncio.TimeoutError:
        return "模型在限定时间内未返回，未生成不可靠结论。请检查 OPENAI_MODEL、网络或稍后重试。\n参考来源：\n" + refs_text
    except Exception:
        # 第三方模型 SDK 的超时不一定继承 asyncio.TimeoutError，必须在 Skill 内降级。
        logger.exception("rag reviewer model call failed")
        return (
            "模型调用失败，未生成不可靠结论。请检查 OPENAI_API_KEY、OPENAI_MODEL、网络或服务商状态后重试。"
            "\n参考来源：\n"
            + refs_text
        )


async def build_evidence_synthesis_answer(
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever,
    api_key: str,
    metadata: dict | None = None,
) -> str:
    """通过 RAG 检索和静态快照完成可追溯的开放问题综合。"""
    evidence, sources = build_meta_evidence_pack(schedule_data, top_decks_data, cards_meta_data)
    results = retriever.hybrid_search(
        query=user_text,
        top_k_bm25=RETRIEVAL_TOP_K_BM25,
        top_k_dense=RETRIEVAL_TOP_K_DENSE,
        final_top_k=RETRIEVAL_FINAL_TOP_K,
        alpha=RETRIEVAL_ALPHA,
        source_type=None,
    )
    if metadata is not None:
        metadata["retrieval_mode"] = results[0].get("retrieval_mode", "none") if results else "none"
        metadata["retrieved_doc_ids"] = [item["doc"].get("doc_id") for item in results]
        metadata["synthesis_reasoning_effort"] = SYNTHESIS_REASONING_EFFORT
    reranked = rerank_results(user_text, parsed, results, top_n=RERANK_TOP_N)
    compressed = compress_results(
        reranked,
        max_items=COMPRESS_MAX_ITEMS,
        char_budget=COMPRESS_CHAR_BUDGET,
    )
    if not compressed:
        return (
            "当前知识库没有检索到足够相关的证据，因此不生成策略性结论。"
            "请补充对应的卡组、对手或战术资料后重试。\n\n参考来源：\n"
            f"{sources}"
        )
    retrieved_context, retrieval_refs = build_context_and_refs(compressed)
    reviewer_instructions = """你是皇室战争战队赛分析助手，使用中文回答。
数据结论只能来自证据包或检索证据；策略建议只能基于检索到的通用战术原则，且必须标为推演。
禁止虚构具体胜率、使用率、对手真实卡组、更新日期、卡牌组件或数据来源。
未出现在证据包的卡牌要明确说明没有快照统计；用户假设的对手体系只能称为训练假设。
按“结论、数据依据、训练/对局建议、数据边界”组织回答，不要展示内部推理过程。"""
    reviewer_agent = ReActAgent(
        name="MetaEvidenceReviewer",
        sys_prompt=(
            "你是皇室战争战队赛分析助手，使用中文回答。\n"
            "你必须区分‘数据结论’与‘策略建议’：数据结论只能来自证据包或检索证据；策略建议只能"
            "基于检索到的通用战术原则，且必须标为推演，不能假装成当前版本统计。\n"
            "禁止虚构具体胜率、使用率、对手真实卡组、更新日期、卡牌组件或数据来源。\n"
            "如果提问的卡牌不在证据包，明确说明没有该卡的快照统计，再给出有限的通用建议。\n"
            "如果用户假设对手会使用某一类体系，称其为训练假设，不要称为对手情报。\n"
            "回答用以下结构：结论、数据依据、训练/对局建议、数据边界。不要展示内部推理过程。"
        ),
        model=build_reviewer_model(api_key),
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )
    reviewer_agent.set_console_output_enabled(enabled=False)
    prompt = (
        f"用户问题：{user_text}\n"
        f"已解析意图：{parsed.get('intent')}\n\n"
        "本地快照证据包：\n"
        f"{evidence}\n\n"
        "RAG 检索证据：\n"
        f"{retrieved_context}\n\n"
        "请严格按照系统规则回答，并在最后保留‘参考来源：’标题。"
    )
    try:
        if uses_responses_api():
            answer = await asyncio.wait_for(
                generate_model_text(
                    api_key=api_key,
                    instructions=reviewer_instructions,
                    input_text=prompt,
                    reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                ),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
        else:
            result = await asyncio.wait_for(
                reviewer_agent(Msg(name="user", role="user", content=prompt)),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
            answer = extract_text_content(result).strip()
    except asyncio.TimeoutError:
        logger.warning("evidence synthesis model call timed out")
        if metadata is not None:
            metadata["model_generation"] = "fallback_after_model_timeout"
            metadata["model_failure_type"] = "TimeoutError"
        answer = build_snapshot_fallback_answer(top_decks_data, cards_meta_data)
    except Exception as exc:
        logger.exception("evidence synthesis model call failed")
        if metadata is not None:
            metadata["model_generation"] = "fallback_after_model_error"
            metadata["model_failure_type"] = type(exc).__name__
        answer = build_snapshot_fallback_answer(top_decks_data, cards_meta_data)
    return f"{answer}\n\n参考来源：\n{sources}\n{retrieval_refs}"


DIRECT_SKILL_REGISTRY = build_default_registry(
    rag_answer_builder=build_rag_answer,
    reviewer_model_builder=build_reviewer_model,
    evidence_synthesis_builder=build_evidence_synthesis_answer,
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
    include_metadata: bool = False,
) -> str | AnswerResult:
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
        if include_metadata:
            return AnswerResult(
                answer=answer,
                trace_id=context.metadata.get("trace_id"),
                parsed=parsed,
                plan=context.metadata.get("plan"),
                selected_skill=context.metadata.get("selected_skill"),
                mode=context.metadata.get("mode"),
                metadata=dict(context.metadata),
            )
        return answer

    logger.info("answer route intent=reject mode=fallback")
    fallback_answer = (
        "当前系统主要支持：\n"
        "- 赛程查询\n"
        "- 下一轮对战/谁上场查询\n"
        "- 热门卡组查询\n"
        "- 单卡使用率/胜率查询\n"
        "- 卡牌排行榜查询"
    )
    if include_metadata:
        return AnswerResult(
            answer=fallback_answer,
            trace_id=context.metadata.get("trace_id"),
            parsed=parsed,
            plan=context.metadata.get("plan"),
            selected_skill=None,
            mode="fallback",
            metadata=dict(context.metadata),
        )
    return fallback_answer


def read_trace(trace_id: str | None) -> list[dict]:
    if not trace_id:
        return []
    return SKILL_EXECUTOR.recorder.read_trace(trace_id)
