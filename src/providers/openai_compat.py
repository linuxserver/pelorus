import json
from typing import Any
from uuid import uuid4

import httpx

from .base import LLMProvider, MessageResult


class OpenAICompatProvider(LLMProvider):

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self._client = httpx.AsyncClient(timeout=120.0)

    def _to_openai_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "assistant":
                openai_msg: dict[str, Any] = {"role": "assistant"}
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tc = {
                                "id": block.get("id", f"toolu_{uuid4().hex[:24]}"),
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            }
                            tool_calls.append(tc)
                        elif block.get("type") == "thinking":
                            pass
                elif isinstance(content, str):
                    text_parts.append(content)
                if text_parts:
                    openai_msg["content"] = "\n".join(text_parts)
                else:
                    openai_msg["content"] = None
                if tool_calls:
                    openai_msg["tool_calls"] = tool_calls
                result.append(openai_msg)

            elif role == "user":
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if block.get("type") == "text":
                            parts.append({"type": "text", "text": block["text"]})
                        elif block.get("type") == "image":
                            source = block.get("source", {})
                            data = source.get("data", "")
                            media_type = source.get("media_type", "image/png")
                            parts.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{data}"
                                },
                            })
                        elif block.get("type") == "tool_result":
                            tool_content = block.get("content", [])
                            tool_use_id = block.get("tool_use_id", "")
                            tc_text = ""
                            for tc_block in tool_content:
                                if isinstance(tc_block, dict):
                                    if tc_block.get("type") == "text":
                                        tc_text = tc_block["text"]
                                    elif tc_block.get("type") == "image":
                                        source = tc_block.get("source", {})
                                        data = source.get("data", "")
                                        mt = source.get("media_type", "image/png")
                                        parts.append({
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:{mt};base64,{data}"
                                            },
                                        })
                                elif isinstance(tc_block, str):
                                    tc_text = tc_block
                            result.append({
                                "role": "tool",
                                "tool_call_id": tool_use_id,
                                "content": tc_text,
                            })
                            continue
                    if parts:
                        result.append({"role": "user", "content": parts})
                    else:
                        result.append({"role": "user", "content": ""})
                elif isinstance(content, str):
                    result.append({"role": "user", "content": content})

            elif role == "tool":
                result.append(msg)

            else:
                result.append(msg)

        return result

    def _to_openai_tools(
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
                        "description": (
                            "Tool for controlling the computer desktop: mouse, "
                            "keyboard, screenshot, and scroll actions."
                        ),
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

    def _from_openai_response(
        self, response_data: dict[str, Any]
    ) -> MessageResult:
        choice = response_data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")
        usage = response_data.get("usage")

        content: list[dict[str, Any]] = []
        msg_content = message.get("content")
        if msg_content:
            content.append({"type": "text", "text": msg_content})

        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    arguments = {}
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"toolu_{uuid4().hex[:24]}"),
                    "name": tc["function"]["name"],
                    "input": arguments,
                })

        if finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif finish_reason == "stop":
            stop_reason = "end_turn"
        else:
            stop_reason = finish_reason

        return MessageResult(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
        )

    async def create_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> MessageResult:
        openai_messages = self._to_openai_messages(messages)
        if system:
            openai_messages.insert(0, {"role": "system", "content": system})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }

        if tools:
            body["tools"] = self._to_openai_tools(tools)
            body["tool_choice"] = tool_choice

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = await self._client.post(
                f"{self.endpoint}/v1/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            return self._from_openai_response(resp.json())
        except httpx.RequestError as e:
            raise RuntimeError(f"LLM request failed: {e}")

    async def create_message_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ):
        openai_messages = self._to_openai_messages(messages)
        if system:
            openai_messages.insert(0, {"role": "system", "content": system})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = self._to_openai_tools(tools)
            body["tool_choice"] = tool_choice

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        text_buf = ""
        tool_call_acc: dict[int, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST", f"{self.endpoint}/v1/chat/completions", json=body, headers=headers
            ) as resp:
                resp.raise_for_status()
                async for raw in resp.aiter_lines():
                    if not raw.startswith("data: "):
                        continue
                    payload = raw[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason")

                    content = delta.get("content", "") or ""
                    if content:
                        text_buf += content
                        yield {"type": "text", "delta": content}

                    raw_tool_calls = delta.get("tool_calls")
                    if raw_tool_calls:
                        for tc_delta in raw_tool_calls:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_call_acc:
                                tool_call_acc[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "function": {"name": "", "arguments": ""},
                                }
                            acc = tool_call_acc[idx]

                            if tid := tc_delta.get("id"):
                                acc["id"] = tid
                            if fn := tc_delta.get("function"):
                                if name := fn.get("name"):
                                    acc["function"]["name"] = name
                                if args := fn.get("arguments"):
                                    acc["function"]["arguments"] += args

                done_reason = finish_reason if finish_reason else "stop"

        content_blocks: list[dict[str, Any]] = []
        if text_buf:
            content_blocks.append({"type": "text", "text": text_buf})

        has_tool_calls = bool(tool_call_acc)
        if has_tool_calls:
            for idx in sorted(tool_call_acc.keys()):
                tc = tool_call_acc[idx]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    arguments = {}
                tid = tc.get("id") or f"toolu_{uuid4().hex[:24]}"
                tool_block = {
                    "type": "tool_use",
                    "id": tid,
                    "name": tc["function"]["name"],
                    "input": arguments,
                }
                content_blocks.append(tool_block)
                yield {"type": "tool_use", "name": tool_block["name"], "input": arguments, "id": tid}

        stop_reason = "tool_use" if has_tool_calls else "end_turn"
        yield {"type": "done", "content": content_blocks, "stop_reason": stop_reason}

    async def cleanup(self):
        await self._client.aclose()
