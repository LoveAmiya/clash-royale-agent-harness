"""将用户问题转发给 Agent 后端的本地浏览器界面。

本进程只负责展示，不直接导入路由或检索代码，因此 Web UI 可以独立于运行在
``BACKEND_URL`` 的 Agent 服务启动、替换或排错。
"""

import uuid
from urllib.parse import quote, urlsplit

import httpx
import uvicorn
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app_config import BACKEND_URL, WEB_HOST, WEB_PORT
from clashroyale_agent.web import proxy_helpers as web_proxy_helpers
from clashroyale_agent.web import runtime as web_runtime
from clashroyale_agent.web import sse_proxy as web_sse_proxy
from clashroyale_agent.web.schemas import (
    CardCompareProxyRequest as PackageCardCompareProxyRequest,
    ChatRequest as PackageChatRequest,
    DeckMatchupProxyRequest as PackageDeckMatchupProxyRequest,
    DeckProfileProxyRequest as PackageDeckProfileProxyRequest,
    EntityCompareProxyRequest as PackageEntityCompareProxyRequest,
    FeedbackProxyRequest as PackageFeedbackProxyRequest,
    LiveSampleSettingsRequest as PackageLiveSampleSettingsRequest,
)
from rolling_corpus import DEFAULT_DATASET_SCOPE
from web_ui_template import HTML_PAGE as MODERN_HTML_PAGE


app = web_runtime.create_web_app()
LIVE_SAMPLE_SETTINGS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/settings/live-sample"
SNAPSHOT_STATUS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/snapshot/status"
FEEDBACK_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/feedback"
READY_STATUS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/ready"
MODEL_STATUS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/model/status"
METRICS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/metrics"
FEEDBACK_STATS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/feedback/stats"
STRUCTURED_API_BASE_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/api"
BACKEND_HTTPX_TRUST_ENV = (urlsplit(BACKEND_URL).hostname or "").casefold() not in {
    "127.0.0.1",
    "localhost",
    "::1",
}



# Keep the prior template in source history while the web server presents the
# isolated multi-view workbench. Backend proxy routes below remain unchanged.
HTML_PAGE = MODERN_HTML_PAGE



# Route annotations and validation use packaged request models. The aliases
# keep the historical root-level import names stable for existing callers.
ChatRequest = PackageChatRequest
LiveSampleSettingsRequest = PackageLiveSampleSettingsRequest
FeedbackProxyRequest = PackageFeedbackProxyRequest
CardCompareProxyRequest = PackageCardCompareProxyRequest
EntityCompareProxyRequest = PackageEntityCompareProxyRequest
DeckProfileProxyRequest = PackageDeckProfileProxyRequest
DeckMatchupProxyRequest = PackageDeckMatchupProxyRequest


sse_data = web_sse_proxy.sse_data


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回自包含的本地聊天页面。"""
    return HTML_PAGE


@app.get("/health")
async def health():
    """为 UI 进程提供轻量级存活检查接口。"""
    return {
        "ok": True,
        "backend_url": BACKEND_URL,
    }


async def proxy_backend_json(url: str, *, unavailable: str, failed: str, invalid: str) -> dict:
    return await web_proxy_helpers.proxy_backend_json(
        url,
        unavailable=unavailable,
        failed=failed,
        invalid=invalid,
        trust_env=BACKEND_HTTPX_TRUST_ENV,
        httpx_module=httpx,
    )


async def proxy_backend_text(url: str, *, unavailable: str, failed: str) -> str:
    return await web_proxy_helpers.proxy_backend_text(
        url,
        unavailable=unavailable,
        failed=failed,
        trust_env=BACKEND_HTTPX_TRUST_ENV,
        httpx_module=httpx,
    )


async def proxy_structured_api(
    method: str,
    path: str,
    payload: dict | None = None,
    dataset_scope: str | None = None,
    query_params: dict | None = None,
) -> JSONResponse:
    return await web_proxy_helpers.proxy_structured_api(
        method,
        path,
        structured_api_base_url=STRUCTURED_API_BASE_URL,
        payload=payload,
        dataset_scope=dataset_scope,
        query_params=query_params,
        trust_env=BACKEND_HTTPX_TRUST_ENV,
        httpx_module=httpx,
    )


@app.get("/ready")
async def get_backend_readiness():
    return await proxy_backend_json(
        READY_STATUS_URL,
        unavailable="backend readiness service is unavailable",
        failed="backend readiness request failed",
        invalid="backend returned an invalid readiness response",
    )


@app.get("/model/status")
async def get_model_status():
    return await proxy_backend_json(
        MODEL_STATUS_URL,
        unavailable="backend model status service is unavailable",
        failed="backend model status request failed",
        invalid="backend returned an invalid model status response",
    )


@app.get("/feedback/stats")
async def get_feedback_stats():
    return await proxy_backend_json(
        FEEDBACK_STATS_URL,
        unavailable="backend feedback stats service is unavailable",
        failed="backend feedback stats request failed",
        invalid="backend returned an invalid feedback stats response",
    )


@app.get("/metrics")
async def get_backend_metrics():
    body = await proxy_backend_text(
        METRICS_URL,
        unavailable="backend metrics service is unavailable",
        failed="backend metrics request failed",
    )
    return StreamingResponse(
        iter([body]),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


async def proxy_live_sample_settings(method: str, payload: dict | None = None) -> dict:
    return await web_proxy_helpers.proxy_backend_request_json(
        method,
        LIVE_SAMPLE_SETTINGS_URL,
        payload=payload,
        unavailable="无法连接后端实时采样设置服务",
        failed="后端实时采样设置请求失败",
        invalid="后端返回了无效的设置响应",
        trust_env=BACKEND_HTTPX_TRUST_ENV,
        httpx_module=httpx,
    )

@app.get("/settings/live-sample")
async def get_live_sample_settings():
    return await proxy_live_sample_settings("GET")


@app.put("/settings/live-sample")
async def update_live_sample_settings(request: LiveSampleSettingsRequest):
    return await proxy_live_sample_settings("PUT", {"target_battles": request.target_battles})


@app.get("/snapshot/status")
async def get_snapshot_status():
    return await proxy_backend_json(
        SNAPSHOT_STATUS_URL,
        unavailable="backend snapshot status service is unavailable",
        failed="backend snapshot status request failed",
        invalid="backend returned an invalid snapshot status response",
    )

@app.get("/api/datasets")
async def structured_datasets_proxy():
    return await proxy_structured_api("GET", "/datasets")


@app.get("/api/cards/catalog")
async def structured_card_catalog_proxy(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return await proxy_structured_api("GET", "/cards/catalog", dataset_scope=dataset_scope)


@app.get("/api/cards/rankings")
async def structured_card_rankings_proxy(
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
    sort_by: str = "usage_rate",
):
    return await proxy_structured_api(
        "GET",
        "/cards/rankings",
        dataset_scope=dataset_scope,
        query_params={"sort_by": sort_by},
    )


@app.get("/api/cards/{card_id}/stats")
async def structured_card_stats_proxy(card_id: str, dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return await proxy_structured_api(
        "GET", f"/cards/{quote(card_id, safe='')}/stats", dataset_scope=dataset_scope
    )


@app.get("/api/entities/catalog")
async def structured_entity_catalog_proxy(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return await proxy_structured_api("GET", "/entities/catalog", dataset_scope=dataset_scope)


@app.get("/api/entities/rankings")
async def structured_entity_rankings_proxy(
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
    sort_by: str = "usage_rate",
):
    return await proxy_structured_api(
        "GET",
        "/entities/rankings",
        dataset_scope=dataset_scope,
        query_params={"sort_by": sort_by},
    )


@app.get("/api/entities/{entity_id}/stats")
async def structured_entity_stats_proxy(entity_id: str, dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return await proxy_structured_api(
        "GET", f"/entities/{quote(entity_id, safe='')}/stats", dataset_scope=dataset_scope
    )


@app.get("/api/loadouts/catalog")
async def structured_loadout_catalog_proxy(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return await proxy_structured_api("GET", "/loadouts/catalog", dataset_scope=dataset_scope)


@app.post("/api/cards/compare")
async def structured_card_compare_proxy(request: CardCompareProxyRequest):
    return await proxy_structured_api("POST", "/cards/compare", request.model_dump())


@app.post("/api/entities/compare")
async def structured_entity_compare_proxy(request: EntityCompareProxyRequest):
    return await proxy_structured_api("POST", "/entities/compare", request.model_dump())


@app.post("/api/decks/profile")
async def structured_deck_profile_proxy(request: DeckProfileProxyRequest):
    return await proxy_structured_api("POST", "/decks/profile", request.model_dump())


@app.post("/api/decks/matchup")
async def structured_deck_matchup_proxy(request: DeckMatchupProxyRequest):
    return await proxy_structured_api("POST", "/decks/matchup", request.model_dump())


@app.get("/api/meta/archetypes")
async def structured_archetypes_proxy(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return await proxy_structured_api("GET", "/meta/archetypes", dataset_scope=dataset_scope)


@app.post("/feedback")
async def submit_feedback(request: FeedbackProxyRequest):
    return await web_proxy_helpers.proxy_backend_request_json(
        "POST",
        FEEDBACK_URL,
        payload=request.model_dump(),
        unavailable="backend feedback service is unavailable",
        failed="backend feedback request failed",
        invalid="backend returned an invalid feedback response",
        trust_env=BACKEND_HTTPX_TRUST_ENV,
        httpx_module=httpx,
    )

@app.post("/chat")
async def chat(req: ChatRequest):
    """透明代理后端 SSE，避免把流消费完后退化为普通 JSON。"""
    session_id = req.session_id or str(uuid.uuid4())
    user_id = req.user_id or "web-user-1"

    backend_payload = web_sse_proxy.build_backend_payload(
        message=req.message,
        session_id=session_id,
        user_id=user_id,
        intent_hint=req.intent_hint,
        dataset_scope=req.dataset_scope,
        deck_mode=req.deck_mode,
        entity_mode=req.entity_mode,
    )

    return StreamingResponse(
        web_sse_proxy.stream_backend_sse(
            backend_url=BACKEND_URL,
            backend_payload=backend_payload,
            trust_env=BACKEND_HTTPX_TRUST_ENV,
            httpx_module=httpx,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    web_runtime.run_web_app(host=WEB_HOST, port=WEB_PORT, uvicorn_module=uvicorn)
