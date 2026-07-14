import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel

from app_config import (
    CARDS_META_FILE,
    RUNTIME_HOST,
    RUNTIME_PORT,
    SCHEDULE_FILE,
    OPENAI_MODEL,
    TOP_DECKS_FILE,
)
from hybrid_retriever import HybridRetriever, load_docs
from query_answering import answer_query
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


def build_chat_model(api_key: str) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name=OPENAI_MODEL,
        api_key=api_key,
        stream=False,
    )


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
    local_intent = local_parsed.get("intent")
    local_confidence = local_parsed.get("parse_confidence")
    if local_intent != "reject" and local_confidence in {
        LOCAL_PARSE_CONFIDENCE_HIGH,
        LOCAL_PARSE_CONFIDENCE_MEDIUM,
    }:
        logger.info(
            "using local parser result intent=%s confidence=%s",
            local_intent,
            local_confidence,
        )
        return local_parsed

    if not api_key:
        logger.warning("no api key available, using fallback parser result")
        return local_parsed

    try:
        parser_agent = build_parser_agent(api_key)
        parse_msg = Msg(name="user", role="user", content=user_text)
        parse_result = await parser_agent(parse_msg)
        parse_text = extract_text_content(parse_result)
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
                parse_confidence=LOCAL_PARSE_CONFIDENCE_MEDIUM,
                parse_reason="llm parser fallback used after local reject/low-confidence parse",
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


async def build_answer(user_text: str, app: FastAPI) -> str:
    cards_meta_data = app.state.cards_meta_data
    schedule_data = app.state.schedule_data
    top_decks_data = app.state.top_decks_data
    api_key = os.getenv("OPENAI_API_KEY")

    parsed = await parse_user_query(user_text, cards_meta_data, api_key)
    logger.info("request parsed intent=%s parsed=%s", parsed.get("intent"), parsed)

    retriever = app.state.retriever
    if query_needs_rag(parsed):
        retriever = ensure_retriever(app)

    return await answer_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        retriever=retriever,
        api_key=api_key or "",
    )


def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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

    answer_text = await build_answer(user_text, app)
    response_id = "resp-local-1"
    message_id = "msg-local-1"

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
                "object": "content",
                "type": "text",
                "status": "in_progress",
                "msg_id": message_id,
                "text": answer_text,
            }
        )
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
