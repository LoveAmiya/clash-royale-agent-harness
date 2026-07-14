import json
import logging
import os
import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from app_config import (
    CARDS_META_FILE,
    RUNTIME_HOST,
    RUNTIME_PORT,
    SCHEDULE_FILE,
    OPENAI_CLIENT_KWARGS,
    OPENAI_MODEL,
    PARSER_REASONING_EFFORT,
    OPENAI_REASONING_EFFORT,
    OPENAI_WIRE_API,
    PARSER_CALL_TIMEOUT_SECONDS,
    TOP_DECKS_FILE,
)
from hybrid_retriever import HybridRetriever, load_docs
from model_gateway import generate_model_text
from query_answering import AnswerResult, answer_query, read_trace
from query_parser import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
    PARSER_SYSTEM_PROMPT,
    build_parse_metadata,
    extract_json_block,
    extract_text_content,
    fallback_parse_query,
    merge_parse_metadata,
    normalize_parsed_query,
)


logger = logging.getLogger(__name__)


def load_json_file(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"没有找到数据文件: {path.resolve()}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ProcessRequest(BaseModel):
    session_id: str | None = None
    user_id: str | None = None
    input: list[dict]


def get_user_text(request: ProcessRequest) -> str:
    for message in request.input:
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if block.get("type") == "text":
                return str(block.get("text", "")).strip()
    return ""


def build_chat_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    """根据中转站协议创建模型；当前 Codex 中转站使用 Responses API。"""
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


def build_parser_agent(api_key: str) -> ReActAgent:
    parser_agent = ReActAgent(
        name="Parser",
        sys_prompt=PARSER_SYSTEM_PROMPT,
        model=build_chat_model(api_key),
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )
    parser_agent.set_console_output_enabled(enabled=False)
    return parser_agent


async def parse_user_query(user_text: str, cards_meta_data: list[dict], api_key: str | None) -> dict:
    local_parsed = fallback_parse_query(user_text, cards_meta_data)
    if not api_key:
        logger.warning("no api key available, using fallback parser result")
        return local_parsed

    try:
        parse_result = await asyncio.wait_for(
            generate_model_text(
                api_key=api_key,
                instructions=PARSER_SYSTEM_PROMPT,
                input_text=user_text,
                reasoning_effort=PARSER_REASONING_EFFORT,
            ),
            timeout=PARSER_CALL_TIMEOUT_SECONDS,
        )
        parse_text = parse_result
        logger.debug("parser raw output=%s", parse_text)

        parsed = extract_json_block(parse_text)
        if parsed is None:
            logger.warning("parser returned non-json output, using fallback parser")
            return merge_parse_metadata(
                local_parsed,
                build_parse_metadata(
                    parse_source=local_parsed.get("parse_source", "local_rule"),
                    parse_confidence=local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_LOW),
                    parse_reason="llm parser returned non-json output; kept local parse",
                ),
            )

        normalized = normalize_parsed_query(parsed, user_text, cards_meta_data)
        return merge_parse_metadata(
            normalized,
            build_parse_metadata(
                parse_source="llm_parser",
                parse_confidence=LOCAL_PARSE_CONFIDENCE_HIGH,
                parse_reason="gpt-5.5 structured parser output validated locally",
            ),
        )
    except Exception as exc:
        logger.warning("parser agent failed, using fallback parser: %s", exc)
        return merge_parse_metadata(
            local_parsed,
            build_parse_metadata(
                parse_source=local_parsed.get("parse_source", "local_rule"),
                parse_confidence=local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_LOW),
                parse_reason=f"llm parser failed; kept local parse: {exc}",
            ),
        )


def query_needs_rag(parsed: dict) -> bool:
    intent = parsed.get("intent")
    if intent in {"meta_analysis_query", "match_preparation_query"}:
        return True
    if intent == "deck_query":
        return parsed.get("rank") is None and parsed.get("top_n") is None
    if intent == "card_query":
        return (
            parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    return False


def ensure_retriever(app: FastAPI) -> HybridRetriever | None:
    retriever = getattr(app.state, "retriever", None)
    if retriever is not None:
        return retriever

    try:
        rag_docs = load_docs()
        retriever = HybridRetriever(rag_docs)
        app.state.retriever = retriever
        logger.info("lazy retriever initialized rag_docs=%s", len(rag_docs))
        return retriever
    except Exception as exc:
        logger.warning("failed to initialize retriever lazily: %s", exc)
        return None


async def build_answer(user_text: str, app: FastAPI) -> AnswerResult:
    cards_meta_data = app.state.cards_meta_data
    schedule_data = app.state.schedule_data
    top_decks_data = app.state.top_decks_data
    api_key = os.getenv("OPENAI_API_KEY")

    parsed = await parse_user_query(user_text, cards_meta_data, api_key)
    logger.info("request parsed intent=%s parsed=%s", parsed.get("intent"), parsed)

    retriever = app.state.retriever
    if query_needs_rag(parsed):
        # HybridRetriever may perform blocking local HTTP calls while building embeddings.
        # Keep that work off the event loop so progress SSE events remain responsive.
        retriever = await asyncio.to_thread(ensure_retriever, app)

    result = await answer_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        retriever=retriever,
        api_key=api_key or "",
        include_metadata=True,
    )
    assert isinstance(result, AnswerResult)
    return result


def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_stream_chunks(text: str, chunk_size: int = 80):
    """将最终文本分成稳定小块，保证不支持 token 流的模型也能渐进显示。"""
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.schedule_data = load_json_file(SCHEDULE_FILE)
    app.state.top_decks_data = load_json_file(TOP_DECKS_FILE)
    app.state.cards_meta_data = load_json_file(CARDS_META_FILE)
    app.state.retriever = None

    logger.info(
        "startup complete schedule=%s decks=%s cards=%s retriever=lazy",
        len(app.state.schedule_data),
        len(app.state.top_decks_data),
        len(app.state.cards_meta_data),
    )
    yield


app = FastAPI(title="ClashRoyaleMatchCoordinator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/process")
async def process(request: ProcessRequest):
    user_text = get_user_text(request)
    logger.info("request received text=%r", user_text)

    response_id = f"resp-{uuid.uuid4().hex}"
    message_id = f"msg-{uuid.uuid4().hex}"

    async def event_stream():
        yield sse_data(
            {
                "object": "response",
                "id": response_id,
                "status": "in_progress",
                "session_id": request.session_id,
            }
        )
        yield sse_data(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
            }
        )
        yield sse_data(
            {
                "object": "progress",
                "status": "in_progress",
                "stage": "parse",
                "label": "正在解析问题并选择执行路径...",
            }
        )

        answer_task = asyncio.create_task(build_answer(user_text, app))
        stages = [
            ("route", "正在确定结构化查询或 RAG 路径..."),
            ("retrieve", "正在检索本地知识库与证据来源..."),
            ("synthesize", "正在调用模型生成可追溯回答..."),
        ]
        stage_index = 0
        while not answer_task.done():
            await asyncio.wait({answer_task}, timeout=0.7)
            if answer_task.done():
                break
            stage, label = stages[min(stage_index, len(stages) - 1)]
            yield sse_data(
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
            logger.exception("answer generation failed")
            yield sse_data(
                {
                    "object": "error",
                    "status": "failed",
                    "message": "生成回答失败，请检查后端日志、模型配置和检索服务。",
                }
            )
            yield sse_data(
                {
                    "object": "response",
                    "id": response_id,
                    "status": "failed",
                }
            )
            return

        trace_events = read_trace(answer_result.trace_id)
        yield sse_data(
            {
                "object": "trace",
                "status": "completed",
                "trace_id": answer_result.trace_id,
                "parsed": answer_result.parsed,
                "plan": answer_result.plan,
                "selected_skill": answer_result.selected_skill,
                "mode": answer_result.mode,
                "metadata": answer_result.metadata,
                "events": trace_events,
            }
        )
        yield sse_data(
            {
                "object": "progress",
                "status": "in_progress",
                "stage": "stream",
                "label": "正在逐段输出回答...",
            }
        )
        answer_text = answer_result.answer
        for chunk in split_stream_chunks(answer_text):
            yield sse_data(
                {
                    "object": "content",
                    "type": "text",
                    "status": "in_progress",
                    "msg_id": message_id,
                    "text": chunk,
                }
            )
            await asyncio.sleep(0)
        yield sse_data(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "text",
                        "text": answer_text,
                    }
                ],
            }
        )
        yield sse_data(
            {
                "object": "response",
                "id": response_id,
                "status": "completed",
                "output": [
                    {
                        "object": "message",
                        "id": message_id,
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": answer_text,
                            }
                        ],
                    }
                ],
            }
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    import uvicorn

    uvicorn.run("runtime_multi:app", host=RUNTIME_HOST, port=RUNTIME_PORT, reload=False)
