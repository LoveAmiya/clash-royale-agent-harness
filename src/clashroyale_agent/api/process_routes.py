"""Process route registration and SSE response orchestration."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from clashroyale_agent.api.schemas import ProcessRequest
from clashroyale_agent.api.sse import format_sse_data, split_stream_chunks
from clashroyale_agent.ops.runtime_events import RuntimeEventEmitter


ProcessEndpoint = Callable[[Request, ProcessRequest | None], Awaitable[Any]]


@dataclass(frozen=True)
class ProcessRuntimeDependencies:
    """Runtime-owned implementations used by the stable ``/process`` contract."""

    app: Any
    validate_dataset_scope: Callable[[str | None], str]
    load_active_manifest: Callable[[], dict | None]
    default_dataset_scope: str
    structured_query_error: Callable[..., Exception]
    get_user_text: Callable[[ProcessRequest], str]
    max_query_chars: int
    normalize_request_id: Callable[[object], str]
    resolve_client_id: Callable[..., str]
    trust_proxy_headers: bool
    runtime_metrics_factory: Callable[[], Any]
    process_quota_factory: Callable[[], Any]
    logger: Any
    openai_model: str
    build_answer: Callable[..., Awaitable[Any]]
    read_trace: Callable[[str | None], list[dict]]
    redact_for_client: Callable[[object], object]
    record_model_stream_mode: Callable[[object], None]
    semantic_content_interval_seconds: float
    runtime_event_emitter_factory: Callable[..., Any] = RuntimeEventEmitter
    sse_data: Callable[[dict], str] = format_sse_data
    split_stream_chunks: Callable[[str], Any] = split_stream_chunks
    now: Callable[[], float] = time.perf_counter
    new_uuid: Callable[[], Any] = uuid.uuid4


def build_process_runtime_dependencies(dependencies_cls: Any, runtime: dict[str, Any]) -> Any:
    """Bind process-route providers from the runtime compatibility namespace."""
    return dependencies_cls(
        app=runtime["app"], validate_dataset_scope=runtime["_validate_dataset_scope"],
        load_active_manifest=lambda: runtime["_active_snapshot_group_manifest"](runtime["DATA_DIR"]),
        default_dataset_scope=runtime["DEFAULT_DATASET_SCOPE"], structured_query_error=runtime["StructuredQueryError"],
        get_user_text=runtime["get_user_text"], max_query_chars=runtime["MAX_QUERY_CHARS"],
        normalize_request_id=runtime["normalize_request_id"], resolve_client_id=runtime["resolve_client_id"],
        trust_proxy_headers=runtime["TRUST_PROXY_HEADERS"], runtime_metrics_factory=runtime["RuntimeMetrics"],
        process_quota_factory=lambda: runtime["create_process_quota"](
            backend=runtime["PROCESS_QUOTA_BACKEND"], max_concurrent=runtime["PROCESS_MAX_CONCURRENT"],
            requests_per_minute=runtime["PROCESS_RATE_LIMIT_PER_MINUTE"], redis_url=runtime["REDIS_URL"],
            lease_seconds=runtime["PROCESS_QUOTA_LEASE_SECONDS"], key_prefix=runtime["PROCESS_QUOTA_KEY_PREFIX"],
            fail_mode=runtime["PROCESS_QUOTA_FAIL_MODE"],
        ),
        logger=runtime["logger"], openai_model=runtime["OPENAI_MODEL"], build_answer=runtime["build_answer"],
        read_trace=runtime["read_trace"], redact_for_client=runtime["redact_for_client"],
        record_model_stream_mode=runtime["record_model_stream_mode"],
        semantic_content_interval_seconds=runtime["SEMANTIC_CONTENT_INTERVAL_SECONDS"],
    )


async def handle_process_request(
    request: Request | ProcessRequest,
    payload: ProcessRequest | None = None,
    *,
    dependencies: ProcessRuntimeDependencies,
) -> StreamingResponse:
    """Serve one process request while preserving the existing SSE event contract."""
    request_object = request if isinstance(request, Request) else None
    if payload is None:
        payload = request
    dataset_scope = dependencies.validate_dataset_scope(payload.dataset_scope)
    active_group = dependencies.load_active_manifest()
    if active_group is None and dataset_scope != dependencies.default_dataset_scope:
        raise dependencies.structured_query_error(
            "DATASET_SCOPE_NOT_READY",
            "The requested rolling dataset scope has not been published yet.",
            status_code=503,
            details={"dataset_scope": dataset_scope},
        )
    user_text = dependencies.get_user_text(payload)
    if not user_text:
        raise HTTPException(status_code=422, detail="a non-empty user question is required")
    if len(user_text) > dependencies.max_query_chars:
        raise HTTPException(
            status_code=413,
            detail=f"user question exceeds {dependencies.max_query_chars} characters",
        )

    incoming_request_id = request_object.headers.get("X-Request-ID") if request_object else None
    request_id = (
        getattr(request_object.state, "request_id", dependencies.normalize_request_id(incoming_request_id))
        if request_object
        else dependencies.normalize_request_id(None)
    )
    client_id = (
        dependencies.resolve_client_id(
            request_object.client.host if request_object.client is not None else None,
            request_object.headers.get("X-Forwarded-For"),
            trust_proxy_headers=dependencies.trust_proxy_headers,
        )
        if request_object
        else "local-test"
    )
    metrics = getattr(dependencies.app.state, "runtime_metrics", None)
    if metrics is None:
        metrics = dependencies.runtime_metrics_factory()
        dependencies.app.state.runtime_metrics = metrics
    quota = getattr(dependencies.app.state, "process_quota", None)
    if quota is None:
        quota = dependencies.process_quota_factory()
        dependencies.app.state.process_quota = quota
    decision = await quota.try_acquire(client_id)
    if not decision.allowed:
        backend_unavailable = decision.reason == "quota_backend_unavailable"
        metrics.record_process(
            outcome="failure" if backend_unavailable else "rate_limited",
            total_seconds=0.0,
        )
        raise HTTPException(
            status_code=503 if backend_unavailable else 429,
            detail=(
                "process quota backend is unavailable"
                if backend_unavailable
                else "process request rate or concurrency limit exceeded"
            ),
            headers={"Retry-After": str(decision.retry_after_seconds or 1)},
        )

    dependencies.logger.info("request received request_id=%s query_chars=%s", request_id, len(user_text))

    response_id = f"resp-{dependencies.new_uuid().hex}"
    message_id = f"msg-{dependencies.new_uuid().hex}"
    started_at = dependencies.now()
    first_execution_at: float | None = None
    first_content_at: float | None = None
    answer_result: Any | None = None
    outcome = "failure"
    answer_task_holder: list[asyncio.Task] = []

    def encode(event: dict) -> str:
        return dependencies.sse_data({"request_id": request_id, **event})

    async def _event_stream():
        nonlocal first_execution_at, first_content_at, answer_result
        yield encode(
            {
                "object": "response",
                "id": response_id,
                "status": "in_progress",
                "session_id": payload.session_id,
            }
        )
        yield encode(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
            }
        )
        yield encode(
            {
                "object": "progress",
                "status": "in_progress",
                "stage": "parse",
                "label": "正在解析问题并选择执行路径...",
            }
        )

        event_sink = dependencies.runtime_event_emitter_factory(
            request_id=request_id,
            question=user_text,
            attributes={
                "snapshot_group_id": active_group.get("snapshot_group_id") if active_group else None,
                "snapshot_id": (
                    active_group.get("datasets", {}).get(dataset_scope, {}).get("snapshot_id")
                    if active_group
                    else None
                ),
                "dataset_scope": dataset_scope,
                "deck_mode": payload.deck_mode,
                "entity_mode": payload.entity_mode,
                "model": dependencies.openai_model,
            },
        )
        answer_kwargs = {"event_sink": event_sink, "request_id": request_id}
        if dataset_scope != dependencies.default_dataset_scope:
            answer_kwargs["dataset_scope"] = dataset_scope
        if payload.deck_mode != "base8":
            answer_kwargs["deck_mode"] = payload.deck_mode
        if payload.entity_mode != "base8":
            answer_kwargs["entity_mode"] = payload.entity_mode
        if payload.intent_hint is not None:
            answer_kwargs["intent_hint"] = payload.intent_hint
        answer_task = asyncio.create_task(
            dependencies.build_answer(user_text, dependencies.app, **answer_kwargs)
        )
        answer_task_holder.append(answer_task)
        stages = [
            ("route", "正在确定结构化查询或 RAG 路径..."),
            ("retrieve", "正在检索本地知识库与证据来源..."),
            ("synthesize", "正在调用模型生成可追溯回答..."),
        ]
        stage_index = 0
        while not answer_task.done() or not event_sink.empty():
            try:
                event = await asyncio.wait_for(event_sink.next_event(), timeout=0.7)
                if event.get("object") == "execution" and first_execution_at is None:
                    first_execution_at = dependencies.now()
                if event.get("object") == "content" and first_content_at is None:
                    first_content_at = dependencies.now()
                yield encode(event)
            except asyncio.TimeoutError:
                if answer_task.done():
                    continue
                stage, label = stages[min(stage_index, len(stages) - 1)]
                yield encode(
                    {
                        "object": "progress",
                        "status": "in_progress",
                        "stage": stage,
                        "label": label,
                    }
                )
                stage_index += 1

        try:
            answer_result = answer_task.result()
        except Exception as exc:
            dependencies.logger.error(
                "answer generation failed type=%s detail=%s",
                type(exc).__name__,
                str(exc)[:500],
                exc_info=True,
            )
            yield encode(
                {
                    "object": "error",
                    "status": "failed",
                    "message": "生成回答失败，请检查后端日志、模型配置和检索服务。",
                }
            )
            yield encode({"object": "response", "id": response_id, "status": "failed"})
            return

        answer_result.metadata["request_id"] = request_id
        answer_text = answer_result.answer
        recent_answers = getattr(dependencies.app.state, "recent_answers", None)
        feedback_store = getattr(dependencies.app.state, "feedback_store", None)
        answer_record = None
        if recent_answers is not None:
            snapshot = getattr(dependencies.app.state, "live_snapshot", None)
            answer_record = {
                "request_id": request_id,
                "question": user_text,
                "answer": answer_text,
                "snapshot_id": snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None,
                "parsed": answer_result.parsed,
                "selected_skill": answer_result.selected_skill,
            }
            recent_answers.put(**answer_record)
        if feedback_store is not None and answer_record is not None:
            feedback_store.register_answer(answer_record)
        if event_sink.content_count == 0:
            yield encode(
                {
                    "object": "progress",
                    "status": "in_progress",
                    "stage": "stream",
                    "label": "正在逐段输出回答...",
                }
            )
            chunks = list(dependencies.split_stream_chunks(answer_text))
            for index, chunk in enumerate(chunks):
                if first_content_at is None:
                    first_content_at = dependencies.now()
                yield encode(
                    {
                        "object": "content",
                        "type": "text",
                        "status": "in_progress",
                        "msg_id": message_id,
                        "text": chunk,
                        "delta": True,
                    }
                )
                if index < len(chunks) - 1:
                    await asyncio.sleep(dependencies.semantic_content_interval_seconds)
        trace_events = dependencies.read_trace(answer_result.trace_id)
        yield encode(
            {
                "object": "trace",
                "status": "completed",
                "trace_id": answer_result.trace_id,
                "parsed": answer_result.parsed,
                "plan": answer_result.plan,
                "selected_skill": answer_result.selected_skill,
                "mode": answer_result.mode,
                "metadata": dependencies.redact_for_client(answer_result.metadata),
                "sub_results": dependencies.redact_for_client(answer_result.sub_results),
                "events": dependencies.redact_for_client(trace_events),
            }
        )
        yield encode(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "text", "text": answer_text}],
            }
        )
        yield encode(
            {
                "object": "response",
                "id": response_id,
                "status": "completed",
                "output": [
                    {
                        "object": "message",
                        "id": message_id,
                        "role": "assistant",
                        "content": [{"type": "text", "text": answer_text}],
                    }
                ],
            }
        )

    async def event_stream():
        nonlocal outcome
        completed = False
        try:
            async for event in _event_stream():
                yield event
            completed = True
            if answer_result is not None:
                outcome = "success"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            unfinished_tasks = [task for task in answer_task_holder if not task.done()]
            if not completed:
                outcome = "cancelled"
            for task in unfinished_tasks:
                task.cancel()
            finished_at = dependencies.now()
            if answer_result is not None:
                live_metadata = answer_result.metadata.get("live_data", {})
                if isinstance(live_metadata, dict):
                    metrics.record_snapshot_collection(live_metadata.get("collection_metrics"))
                metrics.record_model_stream(
                    answer_result.metadata.get("model_stream"),
                    first_content_seconds=(first_content_at - started_at) if first_content_at else None,
                    total_seconds=finished_at - started_at,
                )
                dependencies.record_model_stream_mode(answer_result.metadata.get("model_stream"))
            metrics.record_process(
                outcome=outcome,
                total_seconds=finished_at - started_at,
                first_execution_seconds=(first_execution_at - started_at) if first_execution_at else None,
                first_content_seconds=(first_content_at - started_at) if first_content_at else None,
            )
            await quota.release(decision.lease_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Request-ID": request_id,
        },
    )


def register_process_routes(
    app: FastAPI,
    *,
    process_endpoint: ProcessEndpoint,
) -> None:
    """Register the model-backed /process route on an app."""

    @app.post("/process")
    async def process(request: Request, payload: ProcessRequest | None = None):
        return await process_endpoint(request, payload)


__all__ = [
    "ProcessEndpoint",
    "ProcessRuntimeDependencies",
    "build_process_runtime_dependencies",
    "handle_process_request",
    "register_process_routes",
]
