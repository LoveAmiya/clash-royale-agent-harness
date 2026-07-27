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
FEEDBACK_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/feedback"
READY_STATUS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/ready"
MODEL_STATUS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/model/status"
METRICS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/metrics"
FEEDBACK_STATS_URL = f"{BACKEND_URL.rsplit('/', 1)[0]}/feedback/stats"


HTML_PAGE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>皇室战争战队赛统筹 Agent</title>
  <style>
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
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
    .dashboard-panel {
      margin-bottom: 16px;
      border: 1px solid #dbe3ef;
      border-radius: 8px;
      background: #fff;
      padding: 14px 16px;
    }
    .dashboard-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .dashboard-title {
      color: #1f2937;
      font-size: 14px;
      font-weight: 700;
    }
    .dashboard-state {
      color: #2563eb;
      font-size: 12px;
      white-space: nowrap;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    .viz-card {
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 12px;
      min-width: 0;
      background: #fafafa;
    }
    .viz-card-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      color: #111827;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .viz-subtitle {
      color: #64748b;
      font-size: 12px;
      line-height: 1.5;
      margin-bottom: 10px;
    }
    .status-pill {
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      white-space: nowrap;
      background: #e5e7eb;
      color: #374151;
    }
    .status-ok { background: #dcfce7; color: #166534; }
    .status-warn { background: #fef3c7; color: #92400e; }
    .status-bad { background: #fee2e2; color: #991b1b; }
    .viz-flow {
      display: grid;
      gap: 7px;
    }
    .viz-step {
      border-left: 3px solid #93c5fd;
      padding-left: 8px;
      line-height: 1.45;
    }
    .viz-step-title {
      color: #111827;
      font-size: 12px;
      font-weight: 700;
    }
    .viz-step-detail {
      color: #64748b;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .viz-kv {
      display: grid;
      grid-template-columns: minmax(88px, auto) minmax(0, 1fr);
      gap: 6px 10px;
      color: #334155;
      font-size: 12px;
      line-height: 1.45;
    }
    .viz-kv-label { color: #64748b; }
    .viz-kv-value {
      color: #111827;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    .source-bars {
      display: grid;
      gap: 7px;
      margin-top: 8px;
    }
    .source-row {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr) 40px;
      align-items: center;
      gap: 7px;
      font-size: 12px;
      color: #334155;
    }
    .source-meter {
      height: 7px;
      border-radius: 999px;
      background: #e5e7eb;
      overflow: hidden;
    }
    .source-meter > span {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: #60a5fa;
    }
    .ops-list {
      display: grid;
      gap: 8px;
    }
    @media (max-width: 900px) {
      .dashboard-grid { grid-template-columns: 1fr; }
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
    .feedback-actions {
      display: flex;
      gap: 8px;
      margin: 6px 0 0 4px;
    }
    .feedback-actions button {
      padding: 6px 10px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      background: #fff;
      color: #374151;
      font-size: 12px;
    }
    .feedback-actions button:disabled { opacity: 0.55; cursor: default; }
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

    <section class="dashboard-panel" aria-live="polite" aria-label="系统可视化面板">
      <div class="dashboard-header">
        <span class="dashboard-title">系统可视化</span>
        <span id="dashboardState" class="dashboard-state">正在读取</span>
      </div>
      <div class="dashboard-grid">
        <article id="dataLineageViz" class="viz-card" aria-label="数据血缘与快照对齐">
          <div class="viz-card-title">
            <span>数据血缘</span>
            <span class="status-pill">等待</span>
          </div>
          <div class="viz-subtitle">展示 Supercell 快照如何同时驱动结构化查询和 RAG。</div>
        </article>
        <article id="qualityGateViz" class="viz-card" aria-label="RAG 质量门槛">
          <div class="viz-card-title">
            <span>RAG 质量</span>
            <span class="status-pill">等待</span>
          </div>
          <div class="viz-subtitle">展示证据切片、召回探针和无效文档校验。</div>
        </article>
        <article id="opsViz" class="viz-card" aria-label="运行与模型状态">
          <div class="viz-card-title">
            <span>运行状态</span>
            <span class="status-pill">等待</span>
          </div>
          <div class="viz-subtitle">展示模型熔断、流式模式、反馈和配额状态。</div>
        </article>
      </div>
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
        <label id="sampleControl" class="sample-control" for="sampleTarget" hidden>
          实时样本
          <select id="sampleTarget" aria-label="Supercell 实时采样场次">
            <option value="20000" selected>20000 场</option>
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
    const dashboardState = document.getElementById("dashboardState");
    const dataLineageViz = document.getElementById("dataLineageViz");
    const qualityGateViz = document.getElementById("qualityGateViz");
    const opsViz = document.getElementById("opsViz");

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

    function makeEl(tag, className = "", text = "") {
      const el = document.createElement(tag);
      if (className) el.className = className;
      if (text !== "") el.textContent = text;
      return el;
    }

    function shortValue(value, left = 10, right = 6) {
      if (!value) return "无";
      const text = String(value);
      if (text.length <= left + right + 3) return text;
      return text.slice(0, left) + "..." + text.slice(-right);
    }

    function numberValue(value, fallback = 0) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function formatMaybePercent(value) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return "未记录";
      return (Math.round(parsed * 1000) / 10) + "%";
    }

    function clearCard(panel, title, status, tone, subtitle) {
      panel.innerHTML = "";
      const header = makeEl("div", "viz-card-title");
      header.appendChild(makeEl("span", "", title));
      const pillClass = "status-pill " + (tone ? "status-" + tone : "");
      header.appendChild(makeEl("span", pillClass, status));
      panel.appendChild(header);
      if (subtitle) panel.appendChild(makeEl("div", "viz-subtitle", subtitle));
    }

    function appendKvRows(parent, rows) {
      const box = makeEl("div", "viz-kv");
      rows.forEach(([label, value]) => {
        box.appendChild(makeEl("div", "viz-kv-label", label));
        box.appendChild(makeEl("div", "viz-kv-value", value == null || value === "" ? "无" : String(value)));
      });
      parent.appendChild(box);
      return box;
    }

    function appendFlow(parent, steps) {
      const flow = makeEl("div", "viz-flow");
      steps.forEach(([title, detail]) => {
        const item = makeEl("div", "viz-step");
        item.appendChild(makeEl("div", "viz-step-title", title));
        item.appendChild(makeEl("div", "viz-step-detail", detail));
        flow.appendChild(item);
      });
      parent.appendChild(flow);
      return flow;
    }

    function appendSourceBars(parent, counts) {
      const entries = Object.entries(counts || {})
        .map(([name, value]) => [name, numberValue(value)])
        .filter(([, value]) => value > 0)
        .sort((a, b) => b[1] - a[1]);
      if (!entries.length) {
        parent.appendChild(makeEl("div", "viz-subtitle", "暂无证据分布数据"));
        return;
      }
      const maxValue = Math.max(...entries.map(([, value]) => value));
      const bars = makeEl("div", "source-bars");
      entries.slice(0, 9).forEach(([name, value]) => {
        const row = makeEl("div", "source-row");
        row.appendChild(makeEl("span", "", name));
        const meter = makeEl("div", "source-meter");
        const fill = makeEl("span");
        fill.style.width = Math.max(3, Math.round((value / maxValue) * 100)) + "%";
        meter.appendChild(fill);
        row.appendChild(meter);
        row.appendChild(makeEl("span", "", String(value)));
        bars.appendChild(row);
      });
      parent.appendChild(bars);
    }

    function metricLineCount(metricsText) {
      return String(metricsText || "")
        .split("\n")
        .filter(line => line.startsWith("cr_agent_"))
        .length;
    }

    function renderVisualizationDashboard(snapshot, extra = {}) {
      const rag = snapshot.rag || {};
      const quality = rag.quality || rag.validation || (extra.ready && extra.ready.rag_document_validation) || {};
      const sourceCounts = quality.source_counts || rag.document_counts || {};
      const collection = snapshot.collection_metrics || {};
      const leaderboard = snapshot.leaderboard || {};
      const runtime = snapshot.runtime || {};
      const ready = extra.ready || {};
      const model = extra.model || {};
      const feedback = extra.feedback || {};
      const aligned = Boolean(
        rag.fully_aligned ||
        (ready.snapshot_rag_aligned && ready.snapshot_rag_fingerprint_aligned) ||
        (rag.snapshot_aligned && rag.fingerprint_aligned)
      );

      clearCard(
        dataLineageViz,
        "数据血缘",
        aligned ? "完全对齐" : "需关注",
        aligned ? "ok" : "warn",
        "从官方战斗日志到结构化聚合、RAG 证据和 active retriever 的链路。"
      );
      appendFlow(dataLineageViz, [
        ["Supercell battle logs", String(collection.raw_battle_records || 0) + " raw / " + String(snapshot.sample_battles || 0) + " usable"],
        ["official_daily_snapshot", (snapshot.snapshot_id || "无") + " | " + formatSnapshotTime(snapshot.fetched_at)],
        ["结构化聚合", "cards_meta / top_decks / card_deck_stats 均来自当前快照"],
        ["RAG evidence docs", String(quality.document_count || Object.values(sourceCounts).reduce((a, b) => a + numberValue(b), 0)) + " docs"],
        ["active retriever", aligned ? "snapshot_id 与 docs_fingerprint 匹配" : "等待新索引完成或回退旧索引"]
      ]);
      appendKvRows(dataLineageViz, [
        ["候选池", "前 " + String(leaderboard.candidate_limit || "-") + " 名"],
        ["扫描排名", leaderboard.scanned_rank_end ? String(leaderboard.rank_start || 1) + "-" + String(leaderboard.scanned_rank_end) : "未完成"],
        ["docs 指纹", shortValue(rag.snapshot_docs_fingerprint || quality.docs_fingerprint)],
        ["index 指纹", shortValue(rag.index_docs_fingerprint)]
      ]);

      const invalidCount = numberValue(quality.invalid_document_count || (quality.invalid_evidence_doc_ids || []).length);
      const qualityPassed = quality.passed !== false && invalidCount === 0;
      clearCard(
        qualityGateViz,
        "RAG 质量",
        qualityPassed ? "通过" : "阻止切换",
        qualityPassed ? "ok" : "bad",
        "每次快照发布前校验证据字段完整性、数值一致性、引用和召回探针。"
      );
      appendKvRows(qualityGateViz, [
        ["文档总数", quality.document_count || Object.values(sourceCounts).reduce((a, b) => a + numberValue(b), 0)],
        ["Recall@5", formatMaybePercent(quality.probe_recall_at_5)],
        ["探针数", quality.probe_count || "未记录"],
        ["无效文档", invalidCount],
        ["失败项", (quality.failures || []).length]
      ]);
      appendSourceBars(qualityGateViz, sourceCounts);

      const streamModes = model.stream_modes || {};
      const streamSummary = Object.entries(streamModes)
        .map(([name, value]) => name + ":" + value)
        .join(" | ") || "未观测";
      const quota = ready.quota || {};
      const quotaOk = quota.available !== false;
      const circuitOpen = model.circuit_state === "open";
      clearCard(
        opsViz,
        "运行状态",
        circuitOpen ? "模型熔断" : quotaOk ? "正常" : "配额后端异常",
        circuitOpen || !quotaOk ? "warn" : "ok",
        "展示模型网关、SSE 流式模式、反馈闭环、限流配额和 Prometheus 指标。"
      );
      appendKvRows(opsViz, [
        ["ready", ready.status || "未知"],
        ["quota", (quota.backend || "memory") + " | " + (quotaOk ? "available" : "unavailable")],
        ["model", (model.circuit_state || "unknown") + " | failures=" + String(model.consecutive_failures ?? 0)],
        ["stream", streamSummary],
        ["feedback", "正向 " + String(feedback.positive || 0) + " | 负向 " + String(feedback.negative || 0) + " | 总计 " + String(feedback.total || 0)],
        ["请求", String(runtime.process_requests || 0) + " | 成功 " + String(runtime.successes || 0) + " | 失败 " + String(runtime.failures || 0)],
        ["P95", runtime.sample_size ? String(runtime.process_p95_ms || 0) + " ms" : "暂无样本"],
        ["metrics", String(metricLineCount(extra.metrics)) + " 条 Prometheus 指标"]
      ]);
      dashboardState.textContent = extra.partial ? "部分状态可用" : "已更新";
    }

    async function loadVisualizationStatus(snapshot) {
      dashboardState.textContent = "正在读取";
      const readyPromise = fetch("/ready").then(resp => resp.ok ? resp.json() : null);
      const modelPromise = fetch("/model/status").then(resp => resp.ok ? resp.json() : null);
      const metricsPromise = fetch("/metrics").then(resp => resp.ok ? resp.text() : "");
      const feedbackPromise = fetch("/feedback/stats").then(resp => resp.ok ? resp.json() : null);
      const [ready, model, metrics, feedback] = await Promise.allSettled([
        readyPromise,
        modelPromise,
        metricsPromise,
        feedbackPromise
      ]);
      renderVisualizationDashboard(snapshot, {
        ready: ready.status === "fulfilled" ? ready.value : null,
        model: model.status === "fulfilled" ? model.value : null,
        metrics: metrics.status === "fulfilled" ? metrics.value : "",
        feedback: feedback.status === "fulfilled" ? feedback.value : null,
        partial: [ready, model, metrics, feedback].some(result => result.status !== "fulfilled")
      });
    }

    function renderSnapshotStatus(snapshot) {
      const leaderboard = snapshot.leaderboard || {};
      const metrics = snapshot.collection_metrics || {};
      const rag = snapshot.rag || {};
      const runtime = snapshot.runtime || {};
      const lastAttempt = snapshot.last_refresh_attempt || {};
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
        ["RAG 证据文档", `${Object.values(rag.document_counts || {}).reduce((total, value) => total + Number(value || 0), 0)} 篇`],
        ["最近刷新尝试", lastAttempt.status ? `${lastAttempt.status} | ${formatSnapshotTime(lastAttempt.finished_at)}` : "无"],
        ["刷新冷却", snapshot.cooldown_remaining_seconds ? `${Math.ceil(snapshot.cooldown_remaining_seconds)} 秒` : "无"]
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
        const snapshot = await resp.json();
        renderSnapshotStatus(snapshot);
        await loadVisualizationStatus(snapshot);
      } catch (_) {
        snapshotState.textContent = "无法连接后端";
        snapshotGrid.innerHTML = "";
        dashboardState.textContent = "无法连接后端";
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
      if (event.request_id) agentBubble.dataset.requestId = event.request_id;
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

    async function sendFeedback(requestId, rating, correction = null) {
      const response = await fetch("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: requestId, rating, correction })
      });
      if (!response.ok) throw new Error(await response.text() || "反馈提交失败");
    }

    function addFeedbackControls(agentBubble) {
      const requestId = agentBubble.dataset.requestId;
      if (!requestId || agentBubble.parentElement.querySelector(".feedback-actions")) return;
      const actions = document.createElement("div");
      actions.className = "feedback-actions";
      const positive = document.createElement("button");
      positive.type = "button";
      positive.textContent = "有帮助";
      const negative = document.createElement("button");
      negative.type = "button";
      negative.textContent = "需改进";
      actions.append(positive, negative);
      agentBubble.parentElement.appendChild(actions);
      const finish = () => actions.querySelectorAll("button").forEach(button => button.disabled = true);
      positive.addEventListener("click", async () => {
        try { await sendFeedback(requestId, "positive"); finish(); statusEl.textContent = "反馈已记录"; }
        catch (err) { statusEl.textContent = err.message; }
      });
      negative.addEventListener("click", async () => {
        const correction = window.prompt("请说明应如何改进（可取消）", "");
        if (correction === null) return;
        try { await sendFeedback(requestId, "negative", correction); finish(); statusEl.textContent = "纠错候选已记录"; }
        catch (err) { statusEl.textContent = err.message; }
      });
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
        if (streamCompleted) addFeedbackControls(agentBubble);
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


class FeedbackProxyRequest(BaseModel):
    request_id: str
    rating: str
    correction: str | None = None


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


async def proxy_backend_json(url: str, *, unavailable: str, failed: str, invalid: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail=unavailable) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=failed) from exc

    try:
        body = response.json()
    except ValueError:
        body = {"detail": invalid}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("detail", failed))
    return body


async def proxy_backend_text(url: str, *, unavailable: str, failed: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail=unavailable) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=failed) from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text or failed)
    return response.text


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


@app.post("/feedback")
async def submit_feedback(request: FeedbackProxyRequest):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(FEEDBACK_URL, json=request.model_dump())
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="backend feedback service is unavailable") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="backend feedback request failed") from exc
    try:
        body = response.json()
    except ValueError:
        body = {"detail": "backend returned an invalid feedback response"}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("detail", "feedback request failed"))
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
