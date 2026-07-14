"""将用户问题转发给 Agent 后端的本地浏览器界面。

本进程只负责展示，不直接导入路由或检索代码，因此 Web UI 可以独立于运行在
``BACKEND_URL`` 的 Agent 服务启动、替换或排错。
"""

import json
import uuid

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app_config import BACKEND_URL, WEB_HOST, WEB_PORT


app = FastAPI(title="CR Agent Web UI")


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

    <div id="chatBox" class="chat-box"></div>

    <section class="trace-panel" aria-live="polite">
      <div class="trace-heading">
        <span>执行记录</span>
        <span id="traceSummary" class="trace-summary">等待请求</span>
      </div>
      <div id="traceList" class="trace-list"></div>
    </section>

    <div class="composer">
      <textarea id="inputBox" placeholder="输入问题，例如：\n- 我们第五轮打谁\n- 使用率第三的卡牌是什么\n- 现在热门卡组有哪些"></textarea>

      <div class="actions">
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
    const statusEl = document.getElementById("status");
    const traceSummary = document.getElementById("traceSummary");
    const traceList = document.getElementById("traceList");

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
      statusEl.textContent = text;
    }

    function addTraceLine(text) {
      const line = document.createElement("div");
      line.className = "trace-line";
      line.textContent = text;
      traceList.appendChild(line);
    }

    function renderTrace(trace) {
      traceList.innerHTML = "";
      const traceId = trace.trace_id || "未记录";
      const parsed = trace.parsed || {};
      traceSummary.textContent = `${traceId.slice(0, 18)}...`;
      addTraceLine(`解析：${parsed.intent || "unknown"} | ${parsed.parse_source || "unknown"} | ${parsed.parse_confidence || "unknown"}`);
      addTraceLine(`路由：${trace.selected_skill || "fallback"} | ${trace.mode || "unknown"}`);

      const metadata = trace.metadata || {};
      if (metadata.retrieval_mode) {
        const documents = (metadata.retrieved_doc_ids || []).join(", ");
        addTraceLine(`检索：${metadata.retrieval_mode} | 文档=${documents || "无"}`);
      }

      const steps = trace.plan && trace.plan.steps ? trace.plan.steps : [];
      if (steps.length) {
        addTraceLine(`计划：${steps.map(step => step.skill_name).join(" -> ")}`);
      }

      const events = trace.events || [];
      const completed = events.find(event => event.state === "SUCCESS" || event.state === "FAILED");
      if (completed) {
        const outcome = completed.success ? "完成" : "失败";
        addTraceLine(`执行：${outcome} | 耗时=${completed.latency_ms ?? "-"}ms`);
      }
    }

    function handleSseEvent(event, agentBubble) {
      if (event.object === "progress") {
        setLoading(true, event.label || "正在处理...");
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
      }
    }

    async function sendMessage() {
      const message = inputBox.value.trim();
      if (!message) return;

      appendMessage("user", message);
      inputBox.value = "";
      setLoading(true, "正在请求后端...");
      traceSummary.textContent = "执行中";
      traceList.innerHTML = "";
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
      traceList.innerHTML = "";
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
