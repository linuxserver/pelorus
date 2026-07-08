import json
import logging
from typing import Any
from uuid import uuid4

import httpx

from .base import LLMProvider, MessageResult

log = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
    ):
        base = endpoint.rstrip("/") if endpoint else "https://generativelanguage.googleapis.com"
        self.api_base = base
        self.model = (model or "gemini-2.5-flash").removeprefix("models/")
        self.api_key = api_key or ""
        self._client = httpx.AsyncClient(timeout=300.0)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["x-goog-api-key"] = self.api_key
        return h

    def _tool_to_gemini(self, tool: dict[str, Any]) -> dict[str, Any]:
        fn = tool.get("function", tool)
        return {
            "name": fn.get("name", "unknown"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        }

    def _contents_from_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            raw_content = msg.get("content", "")

            if role == "assistant":
                parts: list[dict[str, Any]] = []
                if isinstance(raw_content, list):
                    for block in raw_content:
                        btype = block.get("type")
                        if btype == "text":
                            parts.append({"text": block["text"]})
                        elif btype == "thinking":
                            parts.append({"text": f"[thinking] {block.get('thinking', '')}"})
                        elif btype == "tool_use":
                            fc_call: dict[str, Any] = {
                                "name": block["name"],
                                "args": block.get("input", {}),
                            }
                            if block.get("id"):
                                fc_call["id"] = block["id"]
                            part_entry: dict[str, Any] = {"functionCall": fc_call}
                            if block.get("thought_signature"):
                                part_entry["thoughtSignature"] = block["thought_signature"]
                            parts.append(part_entry)
                elif isinstance(raw_content, str) and raw_content:
                    parts.append({"text": raw_content})
                if parts:
                    contents.append({"role": "model", "parts": parts})

            elif role == "user":
                if isinstance(raw_content, list):
                    tool_result_blocks = [b for b in raw_content if isinstance(b, dict) and b.get("type") == "tool_result"]
                    other_blocks = [b for b in raw_content if isinstance(b, dict) and b.get("type") != "tool_result"]

                    if other_blocks:
                        parts = self._user_blocks_to_parts(other_blocks)
                        if parts:
                            contents.append({"role": "user", "parts": parts})

                    for tr in tool_result_blocks:
                        self._append_tool_result(contents, tr)
                elif isinstance(raw_content, str) and raw_content:
                    contents.append({"role": "user", "parts": [{"text": raw_content}]})

            elif role == "tool":
                parts: list[dict[str, Any]] = []
                text = ""
                if isinstance(raw_content, str):
                    text = raw_content
                elif isinstance(raw_content, list):
                    for block in raw_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block["text"]
                if text:
                    parts.append({"text": text})
                if parts:
                    contents.append({"role": "user", "parts": parts})

        return contents

    def _user_blocks_to_parts(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for block in blocks:
            if block.get("type") == "text":
                parts.append({"text": block["text"]})
            elif block.get("type") == "image":
                source = block.get("source", {})
                parts.append({
                    "inline_data": {
                        "mime_type": source.get("media_type", "image/png"),
                        "data": source.get("data", ""),
                    }
                })
        return parts

    def _append_tool_result(self, contents: list[dict[str, Any]], tr: dict[str, Any]):
        inner = tr.get("content", [])
        text_parts: list[str] = []
        images: list[str] = []
        for item in inner:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item["text"])
                elif item.get("type") == "image":
                    source = item.get("source", {})
                    images.append(source.get("data", ""))
        combined_text = "\n".join(text_parts) if text_parts else ""

        fn_name = tr.get("name", "computer")
        fn_response: dict[str, Any] = {
            "name": fn_name,
            "response": {"result": combined_text or ""},
        }
        call_id = tr.get("tool_use_id")
        if call_id:
            fn_response["id"] = call_id
        parts: list[dict[str, Any]] = [{"functionResponse": fn_response}]
        contents.append({"role": "user", "parts": parts})

        for img_b64 in images:
            contents.append({
                "role": "user",
                "parts": [{"inline_data": {"mime_type": "image/png", "data": img_b64}}],
            })

    def _from_gemini_response(
        self, response_data: dict[str, Any]
    ) -> MessageResult:
        candidates = response_data.get("candidates", [])
        if not candidates:
            return MessageResult(content=[], stop_reason="end_turn")

        choice = candidates[0]
        content_obj = choice.get("content", {})
        parts = content_obj.get("parts", [])
        finish_reason = choice.get("finishReason", "STOP")

        content_blocks: list[dict[str, Any]] = []
        has_tool_calls = False

        for part in parts:
            if "text" in part:
                content_blocks.append({"type": "text", "text": part["text"]})
            if "functionCall" in part:
                has_tool_calls = True
                fc = part["functionCall"]
                arguments = fc.get("args", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                tool_entry: dict[str, Any] = {
                    "type": "tool_use",
                    "id": fc.get("id") or f"toolu_{uuid4().hex[:24]}",
                    "name": fc.get("name", "computer"),
                    "input": arguments,
                }
                if "thoughtSignature" in part:
                    tool_entry["thought_signature"] = part["thoughtSignature"]
                content_blocks.append(tool_entry)

        if has_tool_calls and not any(b.get("type") == "text" for b in content_blocks):
            desc = "; ".join(
                f"{b['name']}({json.dumps(b.get('input', {}))})"
                for b in content_blocks if b.get("type") == "tool_use"
            )
            content_blocks.insert(0, {"type": "text", "text": f"[Calling: {desc}]"})

        stop_reason = "tool_use" if has_tool_calls else "end_turn"
        return MessageResult(
            content=content_blocks,
            stop_reason=stop_reason,
            usage=response_data.get("usageMetadata"),
        )

    async def create_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> MessageResult:
        contents = self._contents_from_messages(messages)

        body: dict[str, Any] = {
            "model": self.model,
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }

        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        if tools:
            body["tools"] = [
                {"functionDeclarations": [self._tool_to_gemini(t) for t in tools]}
            ]
            if tool_choice == "any":
                body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
            elif tool_choice == "auto":
                body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        url = f"{self.api_base}/v1beta/models/{self.model}:generateContent"
        headers = self._headers()

        try:
            resp = await self._client.post(url, json=body, headers=headers)
            if resp.is_error:
                log.error("Gemini API error %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
            return self._from_gemini_response(resp.json())
        except httpx.RequestError as e:
            raise RuntimeError(f"Gemini request failed: {e}")

    async def create_message_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ):
        contents = self._contents_from_messages(messages)

        body: dict[str, Any] = {
            "model": self.model,
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }

        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        if tools:
            body["tools"] = [
                {"functionDeclarations": [self._tool_to_gemini(t) for t in tools]}
            ]
            if tool_choice == "any":
                body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
            elif tool_choice == "auto":
                body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        url = f"{self.api_base}/v1beta/models/{self.model}:streamGenerateContent?alt=sse"
        headers = self._headers()

        text_buf = ""
        tool_call_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST", url, json=body, headers=headers
            ) as resp:
                if resp.is_error:
                    error_body = await resp.aread()
                    log.error("Gemini stream error %s: %s", resp.status_code, error_body.decode())
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

                    candidates = chunk.get("candidates", [])
                    if not candidates:
                        continue
                    delta = candidates[0].get("content", {}).get("parts", [])
                    finish_reason = candidates[0].get("finishReason")

                    for part in delta:
                        if "text" in part:
                            text_buf += part["text"]
                            yield {"type": "text", "delta": part["text"]}
                        if "functionCall" in part:
                            idx = len(tool_call_acc)
                            fc = part["functionCall"]
                            entry: dict[str, Any] = {
                                "name": fc.get("name", "computer"),
                                "args": fc.get("args", {}),
                                "id": fc.get("id"),
                            }
                            if "thoughtSignature" in part:
                                entry["thought_signature"] = part["thoughtSignature"]
                            tool_call_acc[idx] = entry

        content_blocks: list[dict[str, Any]] = []
        if text_buf:
            content_blocks.append({"type": "text", "text": text_buf})

        has_tool_calls = bool(tool_call_acc)
        if has_tool_calls:
            for idx in sorted(tool_call_acc.keys()):
                tc = tool_call_acc[idx]
                arguments = tc["args"]
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                tool_block: dict[str, Any] = {
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{uuid4().hex[:24]}",
                    "name": tc["name"],
                    "input": arguments,
                }
                if "thought_signature" in tc:
                    tool_block["thought_signature"] = tc["thought_signature"]
                content_blocks.append(tool_block)
                yield {
                    "type": "tool_use",
                    "name": tool_block["name"],
                    "input": arguments,
                    "id": tool_block["id"],
                }

        if has_tool_calls and not text_buf:
            desc = "; ".join(
                f"{b['name']}({json.dumps(b.get('input', {}))})"
                for b in content_blocks if b.get("type") == "tool_use"
            )
            fallback = f"[Calling: {desc}]"
            content_blocks.insert(0, {"type": "text", "text": fallback})
            yield {"type": "text", "delta": fallback}

        stop_reason = "tool_use" if has_tool_calls else "end_turn"
        yield {"type": "done", "content": content_blocks, "stop_reason": stop_reason}

    async def cleanup(self):
        await self._client.aclose()
