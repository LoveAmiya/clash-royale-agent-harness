"""执行已选择的 Skill，并记录完整的查询级 Trace。"""

import inspect
import time

from harness.state import FAILED, FALLBACK, PENDING, RUNNING, SUCCESS
from harness.trace import TraceEvent, TraceRecorder
from skills.base import SkillContext
from skills.registry import SkillRegistry


class SkillExecutor:
    """连接解析/路由结果与 Skill 执行过程，不隐藏失败。

    每个请求都有 PENDING -> RUNNING -> SUCCESS/FAILED 的 Trace；当没有 Skill
    可以安全处理解析意图时，进入 FALLBACK 终态。
    """
    def __init__(self, registry: SkillRegistry, recorder: TraceRecorder | None = None):
        self.registry = registry
        self.recorder = recorder or TraceRecorder()

    async def execute(self, context: SkillContext):
        """选择并运行 Skill，同时记录耗时和执行证据。

        ``inspect.isawaitable`` 让简单的同步查询 Skill 与 I/O 密集的 RAG Skill
        能通过同一套接口执行。
        """
        trace_id = self.recorder.new_trace_id()
        intent = context.parsed.get("intent")

        self.recorder.record(
            TraceEvent(
                trace_id=trace_id,
                state=PENDING,
                selected_skill=None,
                intent=intent,
                mode=None,
                metadata=context.metadata,
                parsed=context.parsed,
            )
        )

        # 兼容旧的 resolve(parsed) 接口，同时优先使用能承载更多路由上下文的 select(context)。
        skill = self.registry.select(context) if hasattr(self.registry, "select") else self.registry.resolve(context.parsed)

        if skill is None:
            self.recorder.record(
                TraceEvent(
                    trace_id=trace_id,
                    state=FALLBACK,
                    selected_skill=None,
                    intent=intent,
                    mode="fallback",
                    metadata=context.metadata,
                    latency_ms=0,
                    success=False,
                    parsed=context.parsed,
                )
            )
            return None

        # 该标签是可观测性元数据，而不是路由策略；它让 UI 和评测能比较直接结构化回答与 RAG 回答。
        if skill.name == "RAGEvidenceSkill":
            mode = "rag"
        elif skill.name == "EvidenceSynthesisSkill":
            mode = "evidence_synthesis"
        else:
            mode = "direct"
        self.recorder.record(
            TraceEvent(
                trace_id=trace_id,
                state=RUNNING,
                selected_skill=skill.name,
                intent=intent,
                mode=mode,
                metadata=context.metadata,
                parsed=context.parsed,
            )
        )

        started_at = time.perf_counter()
        try:
            answer = skill.run(context)
            if inspect.isawaitable(answer):
                answer = await answer

            latency_ms = int((time.perf_counter() - started_at) * 1000)
            self.recorder.record(
                TraceEvent(
                    trace_id=trace_id,
                    state=SUCCESS,
                    selected_skill=skill.name,
                    intent=intent,
                    mode=mode,
                    metadata=context.metadata,
                    latency_ms=latency_ms,
                    success=True,
                    parsed=context.parsed,
                )
            )
            return answer
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            self.recorder.record(
                TraceEvent(
                    trace_id=trace_id,
                    state=FAILED,
                    selected_skill=skill.name,
                    intent=intent,
                    mode=mode,
                    metadata=context.metadata,
                    latency_ms=latency_ms,
                    success=False,
                    error=str(exc),
                    parsed=context.parsed,
                )
            )
            raise
