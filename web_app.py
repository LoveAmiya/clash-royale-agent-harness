"""将用户问题转发给 Agent 后端的本地浏览器界面。

本进程只负责展示，不直接导入路由或检索代码，因此 Web UI 可以独立于运行在
``BACKEND_URL`` 的 Agent 服务启动、替换或排错。
"""

import json
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app_config import BACKEND_URL, WEB_HOST, WEB_PORT


app = FastAPI(title="CR Agent Web UI")
LIVE_SAMPLE_SETTINGS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/settings/live-sample"
SNAPSHOT_STATUS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/snapshot/status"


HTML_PAGE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>皇室战争战队赛统筹 Agent</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      background: #f5f7fb;
      color: #222;
    }
    .container {
      max-width: 960px;
      margin: 0 auto;
      padding: 24px 16px 40px;
    }
    .title {
      font-size: 26px;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .subtitle {
      color: #666;
      margin-bottom: 20px;
    }
    .snapshot-panel {
      margin-bottom: 16px;
      border: 1px solid #dbe3ef;
      border-radius: 8px;
      background: #fff;
      padding: 14px 16px;
    }
    .snapshot-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .snapshot-title {
      color: #1f2937;
      font-size: 14px;
      font-weight: 700;
    }
    .snapshot-state {
      color: #2563eb;
      font-size: 12px;
      white-space: nowrap;
    }
    .snapshot-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px 16px;
    }
    .snapshot-item { min-width: 0; }
    .snapshot-label {
      color: #64748b;
      font-size: 12px;
      margin-bottom: 3px;
    }
    .snapshot-value {
      color: #111827;
      font-size: 13px;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    @media (max-width: 720px) {
      .snapshot-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .chat-box {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 16px;
      padding: 16px;
      min-height: 480px;
      max-height: 68vh;
      overflow-y: auto;
      box-shadow: 0 6px 18px rgba(0,0,0,0.04);
    }
    .msg {
      margin-bottom: 14px;
      display: flex;
      flex-direction: column;
    }
    .msg.user { align-items: flex-end; }
    .msg.agent { align-items: flex-start; }
    .bubble {
      max-width: 82%;
      padding: 12px 14px;
      border-radius: 14px;
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 15px;
    }
    .user .bubble {
      background: #2563eb;
      color: #fff;
      border-top-right-radius: 6px;
    }
    .agent .bubble {
      background: #f3f4f6;
      color: #111827;
      border-top-left-radius: 6px;
    }
    .meta {
      font-size: 12px;
      color: #6b7280;
      margin-bottom: 4px;
      padding: 0 4px;
    }
    .composer {
      margin-top: 16px;
      display: flex;
      gap: 12px;
      align-items: stretch;
    }
    textarea {
      flex: 1;
      resize: none;
      min-height: 92px;
      max-height: 220px;
      padding: 14px;
      border: 1px solid #d1d5db;
      border-radius: 14px;
      outline: none;
      font-size: 15px;
      line-height: 1.5;
      background: #fff;
    }
    textarea:focus {
      border-color: #2563eb;
      box-shadow: 0 0 0 3px rgba(37,99,235,0.12);
    }
    .actions {
      width: 120px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .sample-control {
      display: grid;
      gap: 4px;
      color: #475569;
      font-size: 12px;
      font-weight: 600;
    }
    #sampleTarget {
      width: 100%;
      min-height: 40px;
      padding: 8px;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      background: #fff;
      color: #111827;
      font: inherit;
    }
    #sampleTarget:disabled {
      background: #f3f4f6;
      color: #94a3b8;
    }
    button {
      border: none;
      border-radius: 12px;
      padding: 12px 14px;
      font-size: 15px;
      cursor: pointer;
      transition: 0.2s;
    }
    #sendBtn {
      background: #2563eb;
      color: white;
      font-weight: 600;
    }
    #sendBtn:disabled {
      background: #93c5fd;
      cursor: not-allowed;
    }
    #clearBtn {
      background: #e5e7eb;
      color: #111827;
    }
    .tips {
      margin-top: 14px;
      color: #6b7280;
      font-size: 13px;
      line-height: 1.7;
    }
    .status {
      margin-top: 10px;
      font-size: 13px;
      color: #2563eb;
      min-height: 20px;
    }
    .trace-panel {
      margin-top: 16px;
      border-top: 1px solid #dbe3ef;
      padding-top: 14px;
    }
    .trace-heading {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 14px;
      font-weight: 700;
      color: #1f2937;
    }
    .trace-summary {
      color: #2563eb;
      font-weight: 400;
    }
    .trace-list {
      display: grid;
      gap: 6px;
      margin-top: 10px;
      font-family: Consolas, "Microsoft YaHei", monospace;
      font-size: 12px;
      color: #475569;
    }
    .trace-panel details > summary {
      cursor: pointer;
      list-style: none;
    }
    .trace-panel details > summary::-webkit-details-marker { display: none; }
    .debug-trace {
      margin-top: 12px;
      color: #64748b;
      font-family: Consolas, "Microsoft YaHei", monospace;
      font-size: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .trace-line {
      border-left: 3px solid #93c5fd;
      padding-left: 8px;
      line-height: 1.5;
      word-break: break-word;
    }
    code {
      background: #f3f4f6;
      padding: 2px 6px;
      border-radius: 6px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="title">皇室战争战队赛统筹 Agent</div>
    <div class="subtitle">网页版客户端。调用你本地运行的 <code>runtime_multi.py</code> 服务。</div>

    <section class="snapshot-panel" aria-live="polite" aria-label="官方数据快照状态">
      <div class="snapshot-header">
        <span class="snapshot-title">当前数据快照</span>
        <span id="snapshotState" class="snapshot-state">正在读取</span>
      </div>
      <div id="snapshotGrid" class="snapshot-grid"></div>
    </section>

    <div id="chatBox" class="chat-box"></div>

    <section class="trace-panel" aria-live="polite">
      <details id="executionPanel" open>
      <summary class="trace-heading">
        <span>执行说明</span>
        <span id="traceSummary" class="trace-summary">等待请求</span>
      </summary>
      <div id="traceList" class="trace-list"></div>
      </details>
      <details class="debug-trace">
        <summary>调试详情</summary>
        <pre id="debugTrace"></pre>
      </details>
    </section>

    <div class="composer">
      <textarea id="inputBox" placeholder="输入问题，例如：\n- 我们第五轮打谁\n- 使用率第三的卡牌是什么\n- 现在热门卡组有哪些"></textarea>

      <div class="actions">
        <label id="sampleControl" class="sample-control" for="sampleTarget">
          实时样本
          <select id="sampleTarget" aria-label="Supercell 实时采样场次">
            <option value="200">200 场</option>
            <option value="400" selected>400 场</option>
            <option value="1000">1000 场</option>
            <option value="2000">2000 场</option>
            <option value="5000">5000 场</option>
            <option value="10000">10000 场</option>
            <option value="20000">20000 场</option>
          </select>
        </label>
        <button id="sendBtn">发送</button>
        <button id="clearBtn" type="button">清空记录</button>
      </div>
    </div>

    <div id="status" class="status"></div>

    <div class="tips">
      建议先启动后端：<code>py runtime_multi.py</code><br/>
      再启动本页面：<code>py web_app.py</code><br/>
      然后浏览器打开：<code>http://127.0.0.1:8080</code>
    </div>
  </div>

  <script>
    const chatBox = document.getElementById("chatBox");
    const inputBox = document.getElementById("inputBox");
    const sendBtn = document.getElementById("sendBtn");
    const clearBtn = document.getElementById("clearBtn");
    const sampleTarget = document.getElementById("sampleTarget");
    const sampleControl = document.getElementById("sampleControl");
    const statusEl = document.getElementById("status");
    const traceSummary = document.getElementById("traceSummary");
    const traceList = document.getElementById("traceList");
    const executionPanel = document.getElementById("executionPanel");
    const debugTrace = document.getElementById("debugTrace");
    const snapshotState = document.getElementById("snapshotState");
    const snapshotGrid = document.getElementById("snapshotGrid");

    let sessionId = localStorage.getItem("cr_agent_session_id");
    if (!sessionId) {
      sessionId = crypto.randomUUID();
      localStorage.setItem("cr_agent_session_id", sessionId);
    }

    function appendMessage(role, text) {
      const wrapper = document.createElement("div");
      wrapper.className = `msg ${role}`;

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = role === "user" ? "你" : "Agent";

      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = text;

      wrapper.appendChild(meta);
      wrapper.appendChild(bubble);
      chatBox.appendChild(wrapper);
      chatBox.scrollTop = chatBox.scrollHeight;
      return bubble;
    }

    function setLoading(loading, text = "") {
      sendBtn.disabled = loading;
      sampleTarget.disabled = loading;
      statusEl.textContent = text;
    }

    function formatSnapshotTime(value) {
      if (!value) return "未生成";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
    }

    function snapshotStateLabel(status) {
      return {
        ready: "可用",
        refreshing: "正在更新",
        stale: "使用上次成功快照",
        cooldown: "冷却中",
        unavailable: "未就绪"
      }[status] || status || "未知";
    }

    function ragStateLabel(status) {
      return {
        not_required: "不需要 RAG",
        not_ready: "等待预热",
        building: "后台构建中",
        ready: "向量检索可用",
        bm25_only: "BM25 降级可用",
        failed: "构建失败"
      }[status] || status || "未知";
    }

    function renderSnapshotStatus(snapshot) {
      const leaderboard = snapshot.leaderboard || {};
      const metrics = snapshot.collection_metrics || {};
      const rag = snapshot.rag || {};
      const runtime = snapshot.runtime || {};
      const scanRange = leaderboard.scanned_rank_end
        ? `${leaderboard.rank_start || 1}-${leaderboard.scanned_rank_end}`
        : "尚未完成";
      const values = [
        ["服务请求", `${runtime.process_requests || 0} | 成功 ${runtime.successes || 0}`],
        ["服务异常", `失败 ${runtime.failures || 0} | 限流 ${runtime.rate_limited || 0}`],
        ["回答耗时 P95", runtime.sample_size ? `${runtime.process_p95_ms || 0} ms` : "暂无样本"],
        ["来源", snapshot.source || "Supercell Official API"],
        ["快照状态", snapshotStateLabel(snapshot.status)],
        ["有效对局", `${snapshot.sample_battles || 0}/${snapshot.target_battles || 20000}`],
        ["采集时间", formatSnapshotTime(snapshot.fetched_at)],
        ["候选排行榜", `前 ${leaderboard.candidate_limit || "-"} 名`],
        ["实际扫描排名", scanRange],
        ["有效玩家", `${leaderboard.sampled_players || 0} 人`],
        ["跳过重复", `${metrics.duplicates_skipped || 0} 条`],
        ["RAG 索引", ragStateLabel(rag.status)],
        ["RAG 证据文档", `${Object.values(rag.document_counts || {}).reduce((total, value) => total + Number(value || 0), 0)} 篇`]
      ];
      snapshotState.textContent = snapshotStateLabel(snapshot.status);
      snapshotGrid.innerHTML = "";
      values.forEach(([label, value]) => {
        const item = document.createElement("div");
        item.className = "snapshot-item";
        const labelEl = document.createElement("div");
        labelEl.className = "snapshot-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("div");
        valueEl.className = "snapshot-value";
        valueEl.textContent = value;
        item.appendChild(labelEl);
        item.appendChild(valueEl);
        snapshotGrid.appendChild(item);
      });
    }

    async function loadSnapshotStatus() {
      try {
        const resp = await fetch("/snapshot/status");
        if (!resp.ok) throw new Error("snapshot status request failed");
        renderSnapshotStatus(await resp.json());
      } catch (_) {
        snapshotState.textContent = "无法连接后端";
        snapshotGrid.innerHTML = "";
      }
    }

    async function loadLiveSampleSettings() {
      const resp = await fetch("/settings/live-sample");
      if (!resp.ok) throw new Error("无法读取实时采样设置");
      const settings = await resp.json();
      sampleControl.hidden = !settings.can_update_target;
      const value = String(settings.target_battles);
      if ([...sampleTarget.options].some(option => option.value === value)) {
        sampleTarget.value = value;
      }
    }

    async function updateLiveSampleSettings() {
      const targetBattles = Number(sampleTarget.value);
      sampleTarget.disabled = true;
      statusEl.textContent = `已切换为 ${targetBattles} 场，正在刷新官方样本...`;
      try {
        const resp = await fetch("/settings/live-sample", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_battles: targetBattles })
        });
        if (!resp.ok) throw new Error((await resp.text()) || "更新实时采样设置失败");
        const settings = await resp.json();
        statusEl.textContent = `实时样本目标已更新为 ${settings.target_battles} 场，正在从 Supercell API 刷新。`;
      } catch (err) {
        statusEl.textContent = `实时样本设置失败：${err.message}`;
        await loadLiveSampleSettings().catch(() => {});
      } finally {
        sampleTarget.disabled = false;
      }
    }

    const executionLines = new Map();

    function resetExecution() {
      executionLines.clear();
      traceList.innerHTML = "";
      debugTrace.textContent = "";
      executionPanel.open = true;
    }

    function renderExecution(event) {
      const stepId = event.step_id || `${event.phase || "runtime"}.${event.timestamp || Date.now()}`;
      let line = executionLines.get(stepId);
      if (!line) {
        line = document.createElement("div");
        line.className = "trace-line";
        traceList.appendChild(line);
        executionLines.set(stepId, line);
      }
      const elapsed = Number.isFinite(event.elapsed_ms) ? ` | 耗时=${event.elapsed_ms}ms` : "";
      line.textContent = `${event.title || event.phase || "处理中"}：${event.detail || ""}${elapsed}`;
    }

    function addTraceLine(text) {
      const line = document.createElement("div");
      line.className = "trace-line";
      line.textContent = text;
      traceList.appendChild(line);
    }

    function renderTraceLegacy(trace) {
      const traceId = trace.trace_id || "未记录";
      const parsed = trace.parsed || {};
      traceSummary.textContent = `${traceId.slice(0, 18)}...`;
      renderExecution({
        step_id: "request.complete",
        phase: "complete",
        status: "completed",
        title: "请求已完成",
        detail: `${trace.selected_skill || "fallback"} | ${trace.mode || "unknown"}`
      });
      debugTrace.textContent = JSON.stringify(trace, null, 2);
      return;
      addTraceLine(`解析：${parsed.intent || "unknown"} | ${parsed.parse_source || "unknown"} | ${parsed.parse_confidence || "unknown"}`);
      addTraceLine(`路由：${trace.selected_skill || "fallback"} | ${trace.mode || "unknown"}`);

      const metadata = trace.metadata || {};
      if (metadata.live_data) {
        const live = metadata.live_data;
        const target = live.target_battles ? `/${live.target_battles}` : "";
        const players = live.sampled_players ? ` | 玩家=${live.sampled_players}` : "";
        const failed = live.failed_players ? ` | 失败=${live.failed_players}` : "";
        const freshness = live.freshness ? ` | ${live.freshness}` : "";
        addTraceLine(`实时数据：${live.status}${live.sample_battles ? ` | 样本对局=${live.sample_battles}${target}` : ""}${players}${failed}${freshness}`);
        const collection = live.collection_metrics || {};
        if (collection.request_count) {
          addTraceLine(`采集：请求=${collection.request_count} | 429=${collection.rate_limited || 0} | 重试=${collection.retried_requests || 0} | 缓存=${collection.cache_hits || 0} | 耗时=${collection.collection_duration_seconds ?? "-"}s`);
        }
      }
      const subResults = trace.sub_results || metadata.sub_results || [];
      if (subResults.length) {
        addTraceLine(`多意图：${subResults.length} 个子问题`);
        subResults.forEach((result) => {
          const docs = (result.metadata?.retrieved_doc_ids || []).join(", ");
          const latency = Number.isFinite(result.latency_ms) ? ` | 耗时=${result.latency_ms}ms` : "";
          addTraceLine(`${result.id}：${result.title || result.parsed?.intent || "unknown"} | ${result.selected_skill || "fallback"} | ${result.status || "unknown"}${latency}${docs ? ` | 文档=${docs}` : ""}`);
        });
      }
      if (metadata.retrieval_mode) {
        const documents = (metadata.retrieved_doc_ids || []).join(", ");
        addTraceLine(`检索：${metadata.retrieval_mode} | 文档=${documents || "无"}`);
      }
      if (metadata.rag) {
        addTraceLine(`RAG 索引：${metadata.rag.status || "unknown"} | 快照=${metadata.rag.snapshot_id || "无"}`);
      }

      const steps = trace.plan && trace.plan.steps ? trace.plan.steps : [];
      if (steps.length) {
        addTraceLine(`计划：${steps.map(step => step.skill_name).join(" -> ")}`);
      }

      if (Number.isFinite(metadata.total_latency_ms)) {
        addTraceLine(`总耗时=${metadata.total_latency_ms}ms`);
        return;
      }

      const events = trace.events || [];
      const completed = events.find(event => event.state === "SUCCESS" || event.state === "FAILED");
      if (completed) {
        const outcome = completed.success ? "完成" : "失败";
        addTraceLine(`执行：${outcome} | 耗时=${completed.latency_ms ?? "-"}ms`);
      }
    }

    function renderTrace(trace) {
      const traceId = trace.trace_id || "未记录";
      traceSummary.textContent = `${traceId.slice(0, 18)}...`;
      renderExecution({
        step_id: "request.complete",
        phase: "complete",
        status: "completed",
        title: "请求已完成",
        detail: `${trace.selected_skill || "fallback"} | ${trace.mode || "unknown"}`
      });
      debugTrace.textContent = JSON.stringify(trace, null, 2);
    }

    function handleSseEvent(event, agentBubble) {
      if (event.object === "progress") {
        setLoading(true, event.label || "正在处理...");
        return;
      }
      if (event.object === "execution") {
        renderExecution(event);
        return;
      }
      if (event.object === "content" && event.type === "text") {
        agentBubble.textContent += event.text || "";
        chatBox.scrollTop = chatBox.scrollHeight;
        return;
      }
      if (event.object === "trace") {
        renderTrace(event);
        return;
      }
      if (event.object === "error") {
        throw new Error(event.message || "后端处理失败");
      }
      if (event.object === "response" && event.status === "completed") {
        setLoading(false, "");
        loadSnapshotStatus();
      }
    }

    async function sendMessage() {
      const message = inputBox.value.trim();
      if (!message) return;

      appendMessage("user", message);
      inputBox.value = "";
      setLoading(true, "正在请求后端...");
      traceSummary.textContent = "执行中";
      resetExecution();
      const agentBubble = appendMessage("agent", "");

      try {
        const resp = await fetch("/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            message,
            session_id: sessionId,
            user_id: "web-user-1"
          })
        });

        if (!resp.ok) {
          const errText = await resp.text();
          throw new Error(errText || "请求失败");
        }

        if (!resp.body) {
          throw new Error("浏览器不支持流式响应");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        let streamCompleted = false;

        while (!streamCompleted) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary;
          while ((boundary = buffer.indexOf("\\n\\n")) >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const dataLine = frame.split("\\n").find(line => line.startsWith("data: "));
            if (!dataLine) continue;

            const event = JSON.parse(dataLine.slice(6));
            handleSseEvent(event, agentBubble);
            streamCompleted = event.object === "response" && (event.status === "completed" || event.status === "failed");
          }
        }

        if (!agentBubble.textContent) {
          agentBubble.textContent = "未获取到回答。";
        }
        setLoading(false, "");
      } catch (err) {
        agentBubble.textContent = "请求失败：" + err.message;
        setLoading(false, "请求失败");
      }
    }

    sendBtn.addEventListener("click", sendMessage);
    sampleTarget.addEventListener("change", updateLiveSampleSettings);
    loadLiveSampleSettings().catch(() => {
      statusEl.textContent = "暂时无法读取实时采样设置。";
    });
    loadSnapshotStatus();

    inputBox.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    clearBtn.addEventListener("click", () => {
      chatBox.innerHTML = "";
      sessionId = crypto.randomUUID();
      localStorage.setItem("cr_agent_session_id", sessionId);
      statusEl.textContent = "已清空本地会话并生成新 session_id";
      traceSummary.textContent = "等待请求";
      resetExecution();
    });
  </script>
</body>
</html>
"""


class ChatRequest(BaseModel):
    """浏览器聊天表单提交前经过校验的输入。"""
    message: str
    session_id: str | None = None
    user_id: str | None = None


class LiveSampleSettingsRequest(BaseModel):
    target_battles: int


def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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


async def proxy_live_sample_settings(method: str, payload: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(method, LIVE_SAMPLE_SETTINGS_URL, json=payload)
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="无法连接后端实时采样设置服务") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="后端实时采样设置请求失败") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"detail": "后端返回了无效的设置响应"}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("detail", "更新实时采样设置失败"))
    return body


@app.get("/settings/live-sample")
async def get_live_sample_settings():
    return await proxy_live_sample_settings("GET")


@app.put("/settings/live-sample")
async def update_live_sample_settings(request: LiveSampleSettingsRequest):
    return await proxy_live_sample_settings("PUT", {"target_battles": request.target_battles})


@app.get("/snapshot/status")
async def get_snapshot_status():
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(SNAPSHOT_STATUS_URL)
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="backend snapshot status service is unavailable") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="backend snapshot status request failed") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"detail": "backend returned an invalid snapshot status response"}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("detail", "snapshot status request failed"))
    return body


@app.post("/chat")
async def chat(req: ChatRequest):
    """透明代理后端 SSE，避免把流消费完后退化为普通 JSON。"""
    session_id = req.session_id or str(uuid.uuid4())
    user_id = req.user_id or "web-user-1"

    backend_payload = {
        "session_id": session_id,
        "user_id": user_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": req.message
                    }
                ]
            }
        ]
    }

    async def proxy_stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", BACKEND_URL, json=backend_payload) as resp:
                    if resp.status_code >= 400:
                        yield sse_data(
                            {
                                "object": "error",
                                "status": "failed",
                                "message": f"后端返回异常状态码：{resp.status_code}",
                            }
                        )
                        return

                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            yield f"{line}\n\n"
        except httpx.ConnectError:
            yield sse_data(
                {
                    "object": "error",
                    "status": "failed",
                    "message": f"无法连接到后端：{BACKEND_URL}，请先启动后端。",
                }
            )
        except httpx.HTTPError as exc:
            yield sse_data(
                {
                    "object": "error",
                    "status": "failed",
                    "message": f"转发后端流失败：{exc}",
                }
            )

    return StreamingResponse(
        proxy_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    uvicorn.run("web_app:app", host=WEB_HOST, port=WEB_PORT, reload=False)
