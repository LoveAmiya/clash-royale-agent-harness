import json
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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
    }

    function setLoading(loading, text = "") {
      sendBtn.disabled = loading;
      statusEl.textContent = text;
    }

    async function sendMessage() {
      const message = inputBox.value.trim();
      if (!message) return;

      appendMessage("user", message);
      inputBox.value = "";
      setLoading(true, "正在请求后端...");

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

        const data = await resp.json();
        appendMessage("agent", data.answer || "未获取到回答");
        setLoading(false, "");
      } catch (err) {
        appendMessage("agent", "请求失败：" + err.message);
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
    });
  </script>
</body>
</html>
"""


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    user_id: str | None = None


def extract_final_answer(response_event: dict) -> str:
    output = response_event.get("output", [])
    for item in output:
        if item.get("object") == "message" and item.get("role") == "assistant":
            parts = item.get("content") or []
            texts = []
            for part in parts:
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))
            final_text = "".join(texts).strip()
            if final_text:
                return final_text
    return ""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/health")
async def health():
    return {
        "ok": True,
        "backend_url": BACKEND_URL,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
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

    completed_response = None

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", BACKEND_URL, json=backend_payload) as resp:
                if resp.status_code >= 400:
                    error_body = await resp.aread()
                    raise httpx.HTTPStatusError(
                        f"后端返回异常状态码：{resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue

                    raw = line[6:].strip()
                    if not raw:
                        continue

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if event.get("object") == "response" and event.get("status") == "completed":
                        completed_response = event

        if not completed_response:
            raise HTTPException(status_code=500, detail="后端未返回完整 response.completed 事件")

        answer = extract_final_answer(completed_response)
        if not answer:
            raise HTTPException(status_code=500, detail="未能从后端响应中提取最终回答")

        return {
            "session_id": session_id,
            "answer": answer
        }

    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接到后端：{BACKEND_URL}，请先启动 py runtime_multi.py"
        )
    except httpx.HTTPStatusError as e:
        backend_detail = None
        try:
            payload = json.loads(e.response.content.decode("utf-8", errors="replace"))
            backend_detail = payload.get("detail") if isinstance(payload, dict) else None
        except Exception:
            backend_detail = e.response.content.decode("utf-8", errors="replace").strip() or None

        if backend_detail:
            raise HTTPException(
                status_code=502,
                detail=f"后端返回异常状态码：{e.response.status_code}，详情：{backend_detail}",
            )
        raise HTTPException(status_code=502, detail=f"后端返回异常状态码：{e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("web_app:app", host=WEB_HOST, port=WEB_PORT, reload=False)
