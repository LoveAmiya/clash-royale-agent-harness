from skills.base import Skill, SkillContext


class RAGEvidenceSkill(Skill):
    name = "RAGEvidenceSkill"

    def __init__(self, rag_answer_builder=None, reviewer_model_builder=None):
        self._rag_answer_builder = rag_answer_builder
        self._reviewer_model_builder = reviewer_model_builder

    def can_handle(self, parsed: dict) -> bool:
        intent = parsed.get("intent")
        if intent == "deck_query":
            return (
                parsed.get("card_name") is None
                and parsed.get("rank") is None
                and parsed.get("top_n") is None
            )
        if intent == "card_query":
            return (
                parsed.get("card_name") is None
                and parsed.get("rank") is None
                and parsed.get("top_n") is None
            )
        return False

    async def run(self, context: SkillContext) -> str:
        intent = context.parsed.get("intent")
        source_type = "deck" if intent == "deck_query" else "card"

        if context.retriever is None:
            if source_type == "deck":
                return "当前无法使用检索回答卡组开放问题，请先启动 Ollama embedding 服务后重试。"
            return "当前无法使用检索回答卡牌开放问题，请先启动 Ollama embedding 服务后重试。"

        if not context.api_key:
            if source_type == "deck":
                return "当前无法使用检索回答卡组开放问题，请先设置 OPENAI_API_KEY 后重试。"
            return "当前无法使用检索回答卡牌开放问题，请先设置 OPENAI_API_KEY 后重试。"

        if self._rag_answer_builder is None or self._reviewer_model_builder is None:
            raise RuntimeError("RAGEvidenceSkill is not configured")

        return await self._rag_answer_builder(
            user_text=context.user_text,
            parsed=context.parsed,
            retriever=context.retriever,
            source_type=source_type,
            reviewer_model=self._reviewer_model_builder(context.api_key),
            api_key=context.api_key,
            metadata=context.metadata,
            event_sink=context.event_sink,
            stream_content=context.stream_content,
        )
