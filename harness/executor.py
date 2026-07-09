import inspect
import time

from harness.state import FAILED, FALLBACK, PENDING, RUNNING, SUCCESS
from harness.trace import TraceEvent, TraceRecorder
from skills.base import SkillContext
from skills.registry import SkillRegistry


class SkillExecutor:
    def __init__(self, registry: SkillRegistry, recorder: TraceRecorder | None = None):
        self.registry = registry
        self.recorder = recorder or TraceRecorder()

    async def execute(self, context: SkillContext):
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

        mode = "rag" if skill.name == "RAGEvidenceSkill" else "direct"
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
