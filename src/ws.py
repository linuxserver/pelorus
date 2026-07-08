import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .agent import SYSTEM_PROMPT, build_provider, _extract_final_text
from .state import _text_screenshot
from .desktop_tools import DesktopTool, TaskQueue, CreateTaskTool, SetTaskStatusTool
from .tools.base import ToolResult

log = logging.getLogger(__name__)

router = APIRouter()


async def _run_agent_ws(ws: WebSocket, config: dict, user_text: str, settings: dict, stop_event: asyncio.Event):
    t_start = time.monotonic()
    log.info("=== _run_agent_ws START ===")

    provider = build_provider(config)
    vision_supported = config.get("vision", False)
    task_queue = TaskQueue()
    tools_dict: dict[str, Any] = {
        "computer": DesktopTool(vision_supported=vision_supported),
        "create_task": CreateTaskTool(task_queue),
        "set_task_status": SetTaskStatusTool(task_queue),
    }

    system = SYSTEM_PROMPT
    suffix = (settings.get("system_prompt_suffix") or "").strip()
    if suffix:
        system = f"{system} {suffix}"
    max_steps = min(int(settings.get("max_steps", 20)), 50)

    try:
        await ws.send_json({"type": "status", "message": "Taking initial screenshot\u2026"})
        ss = await tools_dict["computer"](action="screenshot")
        if not ss or not ss.base64_image:
            await ws.send_json({"type": "error", "message": "Screenshot failed"})
            return
        await ws.send_json({"type": "init_screenshot", "data": ss.base64_image})

        state = await _text_screenshot()
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Desktop state:\n{state}\n\nTask: {user_text}"},
                ],
            }
        ]

        steps: list[dict] = []
        iteration = 0

        while iteration < max_steps:
            if stop_event.is_set():
                log.info("Stop requested — breaking out of agent loop")
                break
            iteration += 1
            log.info("--- WS Step %d/%d ---", iteration, max_steps)
            await ws.send_json({"type": "step_start", "iteration": iteration})

            content_blocks: list[dict[str, Any]] = []
            has_tool_calls = False

            async for event in provider.create_message_stream(
                messages=messages,
                tools=[t.to_params() for t in tools_dict.values()],
                tool_choice="auto",
                system=system,
                max_tokens=4096,
            ):
                etype = event["type"]
                if etype == "done":
                    content_blocks = event.get("content", [])
                    has_tool_calls = any(
                        b.get("type") == "tool_use" for b in content_blocks
                    )
                elif etype == "thinking":
                    await ws.send_json({"type": "think", "delta": event["delta"]})
                elif etype == "text":
                    await ws.send_json({"type": "text", "delta": event["delta"]})
                elif etype == "tool_use":
                    await ws.send_json({
                        "type": "tool_call",
                        "name": event["name"],
                        "input": event.get("input", {}),
                    })

            if not content_blocks:
                log.warning("Step %d produced no content", iteration)
                await ws.send_json({"type": "error", "message": "Empty response from model"})
                break

            messages.append({"role": "assistant", "content": content_blocks})

            tool_result_content: list[dict] = []
            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                tc = block
                handler = tools_dict.get(tc["name"])
                if not handler:
                    tr = ToolResult(error=f"Unknown tool: {tc['name']}")
                else:
                    tr = await handler(**tc.get("input", {}))
                if tr.base64_image:
                    await ws.send_json({"type": "tool_screenshot", "data": tr.base64_image})
                if tr.output:
                    await ws.send_json({"type": "tool_output", "text": tr.output})
                if tr.error:
                    await ws.send_json({"type": "tool_error", "text": tr.error})
                content: list[dict] = [{"type": "text", "text": tr.output or tr.error or "[done]"}]
                if vision_supported and tr.base64_image:
                    content.append({
                        "type": "image",
                        "source": {"media_type": "image/png", "data": tr.base64_image},
                    })
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "name": tc["name"],
                    "content": content,
                    "is_error": bool(tr.error),
                })

            if tool_result_content:
                messages.append({"role": "user", "content": tool_result_content})

            await ws.send_json({
                "type": "step_end",
                "iteration": iteration,
                "has_tool_calls": has_tool_calls,
                "tool_calls_count": len(tool_result_content),
            })

            if not has_tool_calls:
                log.info("No more tool calls \u2014 exiting loop")
                break

        await ws.send_json({"type": "status", "message": "Taking final screenshot\u2026"})
        final_ss = await tools_dict["computer"](action="screenshot")
        final_screenshot = final_ss.base64_image if final_ss else None

        final_text = _extract_final_text(messages) or "(no response)"

        await ws.send_json({
            "type": "done",
            "final_text": final_text,
            "final_screenshot": final_screenshot,
        })

        log.info("=== _run_agent_ws FINISHED in %.2fs ===", time.monotonic() - t_start)

    except WebSocketDisconnect:
        log.warning("Client disconnected during _run_agent_ws")
    except Exception as e:
        log.exception("Unhandled exception in _run_agent_ws: %s", e)
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except WebSocketDisconnect:
            pass
    finally:
        await provider.cleanup()


@router.websocket("/ws")
async def agent_ws(ws: WebSocket):
    log.info("WebSocket connect")
    await ws.accept()
    stop_event = asyncio.Event()
    task = None
    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type", "?")
            log.info("WS recv: type=%s  text=%s",
                     msg_type,
                     (msg.get("text", "")[:60] if msg_type == "run" else ""))
            if msg_type == "run":
                stop_event.set()
                if task and not task.done():
                    task.cancel()
                stop_event.clear()
                config = msg.get("config", {})
                user_text = msg.get("text", "")
                settings = msg.get("settings", {})
                task = asyncio.create_task(
                    _run_agent_ws(ws, config, user_text, settings, stop_event)
                )
            elif msg_type == "stop":
                stop_event.set()
                if task and not task.done():
                    task.cancel()
                await ws.send_json({"type": "done", "final_text": "(stopped by user)"})
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})
            else:
                log.warning("Unknown WS message type: %s", msg_type)
    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("WebSocket handler error: %s", e)
    finally:
        stop_event.set()
        if task and not task.done():
            task.cancel()
