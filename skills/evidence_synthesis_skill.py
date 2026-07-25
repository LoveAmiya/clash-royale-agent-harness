"""处理需要多份证据和模型综合判断的环境、备战类问题。"""

from skills.base import Skill, SkillContext


class EvidenceSynthesisSkill(Skill):
    name = "EvidenceSynthesisSkill"

    def __init__(self, answer_builder=None):
        self._answer_builder = answer_builder

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") in {"meta_analysis_query", "match_preparation_query"}

    async def run(self, context: SkillContext) -> str:
        if not context.api_key:
            return "此类环境和备战问题需要 RAG 检索与模型综合，请先设置 OPENAI_API_KEY 后重试。"
        if context.retriever is None:
            rag_status = (context.metadata or {}).get("rag_status")
            if rag_status == "building":
                return "RAG 索引正在后台预热；当前不会使用未完成的向量索引生成开放结论，请稍后重试。"
            if rag_status == "failed":
                return "当前快照的 RAG 索引构建失败，系统不会使用旧快照或无证据内容生成开放结论。"
            if rag_status == "not_ready":
                return "当前快照的 RAG 索引尚未准备完成，暂时无法可靠生成开放分析。"
            return "开放分析所需的本地知识库尚未就绪，无法可靠生成策略建议。请检查日志中的检索初始化信息后重试。"
        if self._answer_builder is None:
            raise RuntimeError("EvidenceSynthesisSkill is not configured")

        return await self._answer_builder(
            user_text=context.user_text,
            parsed=context.parsed,
            schedule_data=context.schedule_data,
            top_decks_data=context.top_decks_data,
            cards_meta_data=context.cards_meta_data,
            retriever=context.retriever,
            api_key=context.api_key,
            metadata=context.metadata,
            event_sink=context.event_sink,
            stream_content=context.stream_content,
        )
