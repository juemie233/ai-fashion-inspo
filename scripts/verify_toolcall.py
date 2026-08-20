"""工具调用验证脚本：测试 Ollama 模型的 function calling 是否返回结构化 tool_calls。

用法: python scripts/verify_toolcall.py <model_name>
"""

import json
import sys

import httpx


def test_toolcall(model: str) -> None:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "你好，请用 send_message 工具回复我"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "向用户发送一条消息",
                    "parameters": {
                        "type": "object",
                        "properties": {"content": {"type": "string", "description": "消息内容"}},
                        "required": ["content"],
                    },
                },
            }
        ],
        "stream": False,
    }
    r = httpx.post("http://localhost:11434/api/chat", json=payload, timeout=300)
    r.raise_for_status()
    msg = r.json()["message"]
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []
    print(f"model: {model}")
    print(f"content: {content[:200]!r}")
    print(f"tool_calls: {json.dumps(tool_calls, ensure_ascii=False)}")
    if tool_calls:
        print("RESULT: PASS - 结构化工具调用正常")
    else:
        print("RESULT: FAIL - 未返回结构化 tool_calls（无法做 agent）")


if __name__ == "__main__":
    test_toolcall(sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:9b")
