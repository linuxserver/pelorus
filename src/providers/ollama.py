import json
from typing import Any
from uuid import uuid4

import httpx

from .base import LLMProvider, MessageResult


def _is_b64_like(text: str) -> bool:
    if len(text) < 80:
        return False
    safe = sum(1 for c in text if c.isalnum() or c in "+/=\n")
    return safe > len(text) * 0.85


class OllamaProvider(LLMProvider):

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self._client = httpx.AsyncClient(timeout=300.0)

    async def _ensure_model_ready(self):
        try:
            resp = await self._client.get(f"{self.endpoint}/api/ps")
            resp.raise_for_status()
            loaded = resp.json().get("models", [])
        except Exception:
            return

        for m in loaded:
            name = m.get("name", "")
            if name and name != self.model:
                try:
                    await self._client.post(
                        f"{self.endpoint}/api/generate",
                        json={"model": name, "keep_alive": 0, "prompt": ""},
                    )
                except Exception:
                    pass

    def _to_ollama_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "assistant":
                ollama_msg: dict[str, Any] = {"role": "assistant"}
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            arguments = block.get("input", {})
                            tool_calls.append({
                                "function": {
                                    "name": block["name"],
                                    "arguments": arguments,
                                },
                            })
                elif isinstance(content, str):
                    text_parts.append(content)
                ollama_msg["content"] = "\n".join(text_parts) if text_parts else ""
                if tool_calls:
                    ollama_msg["tool_calls"] = tool_calls
                result.append(ollama_msg)

            elif role == "user":
                text_parts: list[str] = []
                images: list[str] = []
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "image":
                            source = block.get("source", {})
                            data = source.get("data", "")
                            images.append(data)
                        elif block.get("type") == "tool_result":
                            tc = block.get("content", [])
                            tc_text = ""
                            for tc_block in tc:
                                if isinstance(tc_block, dict):
                                    if tc_block.get("type") == "text":
                                        tc_text = tc_block["text"]
                                    elif tc_block.get("type") == "image":
                                        src = tc_block.get("source", {})
                                        images.append(src.get("data", ""))
                                elif isinstance(tc_block, str):
                                    tc_text = tc_block
                            tool_msg: dict[str, Any] = {
                                "role": "tool",
                                "content": tc_text,
                            }
                            if images:
                                tool_msg["images"] = images
                            tool_name = block.get("tool_name", "")
                            if tool_name:
                                tool_msg["tool_name"] = tool_name
                            result.append(tool_msg)
                            images = []
                            continue
                elif isinstance(content, str):
                    text_parts.append(content)

                user_msg: dict[str, Any] = {
                    "role": "user",
                    "content": "\n".join(text_parts) if text_parts else "",
                }
                if images:
                    user_msg["images"] = images
                result.append(user_msg)

            elif role == "tool":
                text = ""
                images: list[str] = []
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text = block["text"]
                            elif block.get("type") == "image":
                                source = block.get("source", {})
                                images.append(source.get("data", ""))
                tool_msg: dict[str, Any] = {"role": "tool", "content": text}
                if images:
                    tool_msg["images"] = images
                tool_name = msg.get("tool_name", "")
                if tool_name:
                    tool_msg["tool_name"] = tool_name
                result.append(tool_msg)

            else:
                result.append(msg)

        return result

    def _to_ollama_tools(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            if tool.get("type", "").startswith("computer_") or (
                tool.get("type") == "function"
                and tool.get("function", {}).get("name") == "computer"
            ):
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name")
                        or tool.get("function", {}).get("name", "computer"),
                        "description": "Tool for controlling the computer desktop: mouse, keyboard, screenshot, and scroll actions.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "description": "The action to perform",
                                    "enum": [
                                        "key", "type", "mouse_move",
                                        "left_click", "left_click_drag",
                                        "right_click", "middle_click",
                                        "double_click", "triple_click",
                                        "cursor_position",
                                        "left_mouse_down", "left_mouse_up",
                                        "scroll", "hold_key", "wait", "zoom",
                                        "desktop_state", "explore_window",
                                        "close_window",
                                    ],
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Text to type or key to press",
                                },
                                "coordinate": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "description": "[x, y] coordinate",
                                },
                                "pid": {
                                    "type": "integer",
                                    "description": "Process ID for explore_window/close_window",
                                },
                                "start_coordinate": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                },
                                "scroll_direction": {
                                    "type": "string",
                                    "enum": ["up", "down", "left", "right"],
                                },
                                "scroll_amount": {
                                    "type": "integer",
                                    "minimum": 0,
                                },
                                "duration": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 100,
                                },
                                "region": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                            },
                            "required": ["action"],
                        },
                    },
                })
            else:
                result.append(tool)
        return result

    def _from_ollama_response(
        self, response_data: dict[str, Any]
    ) -> MessageResult:
        message = response_data.get("message", {})

        content: list[dict[str, Any]] = []
        msg_content = message.get("content")
        if msg_content:
            content.append({"type": "text", "text": msg_content})

        thinking = message.get("thinking")
        if thinking:
            if isinstance(thinking, str) and _is_b64_like(thinking):
                thinking = f"[vision data: {len(thinking)} chars]"
            content.append({"type": "thinking", "thinking": thinking})

        tool_calls = message.get("tool_calls", [])
        has_tool_calls = bool(tool_calls)
        for tc in tool_calls:
            function = tc.get("function", {})
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
            content.append({
                "type": "tool_use",
                "id": f"toolu_{uuid4().hex[:24]}",
                "name": function.get("name", "computer"),
                "input": arguments,
            })

        stop_reason = "tool_use" if has_tool_calls else "end_turn"

        return MessageResult(
            content=content,
            stop_reason=stop_reason,
            usage=response_data,
        )

    async def create_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> MessageResult:
        await self._ensure_model_ready()
        ollama_messages = self._to_ollama_messages(messages)
        if system:
            ollama_messages.insert(0, {"role": "system", "content": system})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
            },
        }

        if tools:
            body["tools"] = self._to_ollama_tools(tools)
            body["tool_choice"] = tool_choice

        headers = {"Content-Type": "application/json"}

        try:
            resp = await self._client.post(
                f"{self.endpoint}/api/chat",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            return self._from_ollama_response(resp.json())
        except httpx.RequestError as e:
            raise RuntimeError(f"Ollama request failed: {e}")

    async def create_message_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ):
        await self._ensure_model_ready()
        ollama_messages = self._to_ollama_messages(messages)
        if system:
            ollama_messages.insert(0, {"role": "system", "content": system})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": True,
            "options": {"num_predict": max_tokens},
        }
        if tools:
            body["tools"] = self._to_ollama_tools(tools)
            body["tool_choice"] = tool_choice

        headers = {"Content-Type": "application/json"}

        thinking_buf = ""
        text_buf = ""
        tool_calls_acc: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST", f"{self.endpoint}/api/chat", json=body, headers=headers
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get("message", {})

                    think = msg.get("thinking", "") or ""
                    if think:
                        thinking_buf += think
                        yield {"type": "thinking", "delta": think}

                    content = msg.get("content", "") or ""
                    if content:
                        text_buf += content
                        yield {"type": "text", "delta": content}

                    raw_tool_calls = msg.get("tool_calls", [])
                    if not raw_tool_calls:
                        raw_tool_calls = chunk.get("tool_calls", [])
                    if raw_tool_calls:
                        for tc in raw_tool_calls:
                            fn = tc.get("function", {})
                            sig = (fn.get("name", ""), str(fn.get("arguments", "")))
                            if sig not in {(t.get("function", {}).get("name", ""), str(t.get("function", {}).get("arguments", ""))) for t in tool_calls_acc}:
                                tool_calls_acc.append(tc)

                    if not chunk.get("done", False):
                        continue

                    content_blocks: list[dict[str, Any]] = []
                    if thinking_buf:
                        t = thinking_buf
                        if _is_b64_like(t):
                            t = f"[vision data: {len(t)} chars]"
                        content_blocks.append({"type": "thinking", "thinking": t})
                    if text_buf:
                        content_blocks.append({"type": "text", "text": text_buf})

                    for tc in tool_calls_acc:
                        fn = tc.get("function", {})
                        arguments = fn.get("arguments", {})
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except (json.JSONDecodeError, TypeError):
                                arguments = {}
                        tool_block = {
                            "type": "tool_use",
                            "id": f"toolu_{uuid4().hex[:24]}",
                            "name": fn.get("name", "computer"),
                            "input": arguments,
                        }
                        content_blocks.append(tool_block)
                        yield {"type": "tool_use", "name": tool_block["name"], "input": arguments, "id": tool_block["id"]}

                    stop_reason = "tool_use" if tool_calls_acc else "end_turn"
                    yield {"type": "done", "content": content_blocks, "stop_reason": stop_reason}

    async def cleanup(self):
        await self._client.aclose()
