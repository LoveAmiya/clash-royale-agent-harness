import json
import uuid
import httpx

API_URL = "http://localhost:8091/process"


def build_payload(session_id: str, user_id: str, text: str) -> dict:
    return {
        "session_id": session_id,
        "user_id": user_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                    }
                ],
            }
        ],
    }


def ask(api_url: str, session_id: str, user_id: str, text: str) -> None:
    payload = build_payload(session_id, user_id, text)

    final_msg_id = None
    started_printing = False
    fallback_answer = None

    with httpx.stream(
        "POST",
        api_url,
        json=payload,
        timeout=120.0,
    ) as response:
        response.raise_for_status()

        for line in response.iter_lines():
            if not line:
                continue

            if not line.startswith("data: "):
                continue

            raw = line[len("data: "):]

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # 只关注最终 assistant 消息，不显示 reasoning
            if (
                event.get("object") == "message"
                and event.get("status") == "in_progress"
                and event.get("type") == "message"
                and event.get("role") == "assistant"
            ):
                final_msg_id = event.get("id")
                if not started_printing:
                    print("Agent：", end="", flush=True)
                    started_printing = True
                continue

            # 流式打印最终 assistant message 的文本 delta
            if (
                event.get("object") == "content"
                and event.get("status") == "in_progress"
                and event.get("type") == "text"
                and event.get("msg_id") == final_msg_id
            ):
                text_delta = event.get("text", "")
                print(text_delta, end="", flush=True)
                continue

            # 兜底：如果流式阶段没抓到，就从 completed message 里取完整答案
            if (
                event.get("object") == "message"
                and event.get("status") == "completed"
                and event.get("type") == "message"
                and event.get("role") == "assistant"
            ):
                content = event.get("content", [])
                texts = []
                for item in content:
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                fallback_answer = "".join(texts)

    if started_printing:
        print("\n")
    elif fallback_answer:
        print(f"Agent：{fallback_answer}\n")
    else:
        print("Agent：没有获取到有效回答。\n")


def main():
    user_id = "local-user"
    session_id = f"session-{uuid.uuid4().hex[:8]}"

    print("皇室战争战队赛统筹 Agent 客户端已启动。")
    print(f"当前 session_id: {session_id}")
    print("输入 /new 可创建新会话，输入 exit 退出。\n")

    while True:
        user_input = input("你：").strip()

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit", "bye"]:
            print("已退出。")
            break

        if user_input == "/new":
            session_id = f"session-{uuid.uuid4().hex[:8]}"
            print(f"已创建新会话，当前 session_id: {session_id}\n")
            continue

        try:
            ask(API_URL, session_id, user_id, user_input)
        except Exception as e:
            print(f"请求失败：{e}\n")


if __name__ == "__main__":
    main()
