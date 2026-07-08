import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import kwin_dbus
from .agent import (
    SYSTEM_PROMPT,
    build_provider,
    _extract_final_text,
    RunRequest,
    StepInfo,
    RunResponse,
    _resolve_run_config,
)
from .state import _text_screenshot
from .desktop_tools import DesktopTool, TaskQueue, CreateTaskTool, SetTaskStatusTool
from .tools.base import ToolResult
from .utils import (
    _ensure_config,
    _load_servers,
    _save_servers,
    _resolve_server,
    _cu_action,
    _fetch_models_from_provider,
    STATIC,
)

log = logging.getLogger(__name__)

router = APIRouter()


# ─── Run Agent (programmatic) ──────────────────────────────────────────


@router.post("/api/run")
async def run_agent(req: RunRequest):
    log.info("run prompt=%s format=%s stream=%s", req.prompt[:80], req.response_format, req.stream)

    if req.stream:
        return StreamingResponse(
            _run_agent_sse(req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    config = _resolve_run_config(req)
    provider = build_provider(config)
    vision_supported = config.get("vision", False)
    task_queue = TaskQueue()
    tools_dict: dict[str, Any] = {
        "computer": DesktopTool(vision_supported=vision_supported),
        "create_task": CreateTaskTool(task_queue),
        "set_task_status": SetTaskStatusTool(task_queue),
    }

    messages: list[dict] = []
    steps: list[StepInfo] = []

    initial_ss = None
    if req.response_format == "verbose":
        try:
            resp = await _cu_action({"action": "screenshot"})
            initial_ss = resp.get("data")
        except Exception:
            pass

    state = await _text_screenshot()
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": f"Desktop state:\n{state}\n\nTask: {req.prompt}"},
        ],
    })

    iteration = 0
    final_text = ""
    steps_exhausted = False

    while iteration < req.max_steps:
        iteration += 1
        log.info("--- Step %d/%d ---", iteration, req.max_steps)

        result = await provider.create_message(
            messages=messages,
            tools=[t.to_params() for t in tools_dict.values()],
            tool_choice="auto",
            system=SYSTEM_PROMPT,
            max_tokens=4096,
        )

        if not result.content:
            log.info("Empty response \u2014 stopping")
            break

        messages.append({"role": "assistant", "content": result.content})

        tool_result_content: list[dict] = []
        step_tool_calls: list[dict] = []
        step_text = ""

        for block in result.content:
            if block.get("type") == "tool_use":
                tool_call = block
                log.info("Tool: %s(%s)", tool_call["name"], tool_call.get("input", {}))
                handler = tools_dict.get(tool_call["name"])
                if not handler:
                    tr = ToolResult(error=f"Unknown tool: {tool_call['name']}")
                else:
                    tr = await handler(**tool_call.get("input", {}))
                content: list[dict] = [{"type": "text", "text": tr.output or tr.error or ""}]
                if vision_supported and tr.base64_image:
                    content.append({
                        "type": "image",
                        "source": {"media_type": "image/png", "data": tr.base64_image},
                    })
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call["id"],
                    "name": tool_call["name"],
                    "content": content,
                    "is_error": bool(tr.error),
                })
                step_tool_calls.append({
                    "name": tool_call["name"],
                    "input": tool_call.get("input", {}),
                    "result": tr.output or tr.error or "",
                    "screenshot": tr.base64_image,
                })
            elif block.get("type") == "text":
                step_text = block.get("text", "")
                final_text = step_text

        if tool_result_content:
            messages.append({"role": "user", "content": tool_result_content})
            for s in step_tool_calls:
                steps.append(StepInfo(
                    text=step_text,
                    tool_call={"name": s["name"], "input": s["input"]},
                    tool_result=s["result"][:500],
                    tool_screenshot=s.get("screenshot"),
                ))
            if task_queue.is_done or iteration >= req.max_steps:
                steps_exhausted = True
        else:
            log.info("No tool calls \u2014 done")
            steps.append(StepInfo(text=step_text))
            break

    await provider.cleanup()

    success = not steps_exhausted
    final_ss = None
    if req.response_format == "verbose":
        try:
            resp = await _cu_action({"action": "screenshot"})
            final_ss = resp.get("data")
        except Exception:
            pass

    if req.response_format == "minimal":
        return RunResponse(success=success)
    if req.response_format == "verbose":
        return RunResponse(
            success=success,
            final_text=final_text or "(no response)",
            steps=steps,
            initial_screenshot=initial_ss,
            final_screenshot=final_ss,
        )
    if req.response_format == "thoughts":
        clean = [StepInfo(text=s.text, tool_call=s.tool_call, tool_result=s.tool_result, thinking=s.thinking) for s in steps]
        return RunResponse(
            success=success,
            final_text=final_text or "(no response)",
            steps=clean,
        )
    return RunResponse(
        success=success,
        final_text=final_text or "(no response)",
        steps=steps,
    )


async def _run_agent_sse(req: RunRequest):
    config = _resolve_run_config(req)
    provider = build_provider(config)
    vision_supported = config.get("vision", False)
    task_queue = TaskQueue()
    tools_dict: dict[str, Any] = {
        "computer": DesktopTool(vision_supported=vision_supported),
        "create_task": CreateTaskTool(task_queue),
        "set_task_status": SetTaskStatusTool(task_queue),
    }
    steps_exhausted = False

    messages: list[dict] = []

    show_screenshots = req.response_format == "verbose"

    if show_screenshots:
        try:
            resp = await _cu_action({"action": "screenshot"})
            data = resp.get("data")
            if data:
                yield f"event: screenshot\ndata: {json.dumps({'which': 'initial', 'data': data})}\n\n"
        except Exception:
            pass

    state = await _text_screenshot()
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": f"Desktop state:\n{state}\n\nTask: {req.prompt}"},
        ],
    })

    iteration = 0
    final_text = ""

    try:
        while iteration < req.max_steps:
            iteration += 1
            log.info("--- SSE Step %d/%d ---", iteration, req.max_steps)
            yield f"event: step_start\ndata: {json.dumps({'iteration': iteration})}\n\n"

            result = await provider.create_message(
                messages=messages,
                tools=[t.to_params() for t in tools_dict.values()],
                tool_choice="auto",
                system=SYSTEM_PROMPT,
                max_tokens=4096,
            )

            if not result.content:
                yield f"event: error\ndata: {json.dumps({'message': 'Empty response from model'})}\n\n"
                break

            messages.append({"role": "assistant", "content": result.content})

            tool_result_content: list[dict] = []
            step_text = ""

            for block in result.content:
                if block.get("type") == "tool_use":
                    tool_call = block
                    tc_input = tool_call.get("input", {})
                    yield f"event: tool_call\ndata: {json.dumps({'name': tool_call['name'], 'input': tc_input})}\n\n"
                    handler = tools_dict.get(tool_call["name"])
                    if not handler:
                        tr = ToolResult(error=f"Unknown tool: {tool_call['name']}")
                    else:
                        tr = await handler(**tc_input)
                    if tr.base64_image and show_screenshots:
                        yield f"event: tool_screenshot\ndata: {json.dumps({'data': tr.base64_image})}\n\n"
                    if tr.output:
                        yield f"event: tool_output\ndata: {json.dumps({'text': tr.output})}\n\n"
                    if tr.error:
                        yield f"event: tool_error\ndata: {json.dumps({'text': tr.error})}\n\n"
                    content: list[dict] = [{"type": "text", "text": tr.output or tr.error or ""}]
                    if vision_supported and tr.base64_image:
                        content.append({
                            "type": "image",
                            "source": {"media_type": "image/png", "data": tr.base64_image},
                        })
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "name": tool_call["name"],
                        "content": content,
                        "is_error": bool(tr.error),
                    })
                elif block.get("type") == "thinking":
                    think = block.get("thinking", "")
                    if think:
                        yield f"event: think\ndata: {json.dumps({'delta': think})}\n\n"
                elif block.get("type") == "text":
                    step_text = block.get("text", "")
                    final_text = step_text

            if tool_result_content:
                messages.append({"role": "user", "content": tool_result_content})
                yield f"event: step_end\ndata: {json.dumps({'iteration': iteration, 'has_tool_calls': True, 'tool_calls_count': len(tool_result_content), 'text': step_text})}\n\n"
                if task_queue.is_done or iteration >= req.max_steps:
                    steps_exhausted = True
            else:
                yield f"event: step_end\ndata: {json.dumps({'iteration': iteration, 'has_tool_calls': False, 'tool_calls_count': 0, 'text': step_text})}\n\n"
                break

        success = not steps_exhausted

        final_ss = None
        if show_screenshots:
            try:
                resp = await _cu_action({"action": "screenshot"})
                final_ss = resp.get("data")
                if final_ss:
                    yield f"event: screenshot\ndata: {json.dumps({'which': 'final', 'data': final_ss})}\n\n"
            except Exception:
                pass

        done_data = {"success": success, "final_text": final_text or "(no response)"}
        if show_screenshots and final_ss:
            done_data["final_screenshot"] = final_ss
        yield f"event: done\ndata: {json.dumps(done_data)}\n\n"

    except Exception as e:
        log.exception("SSE agent error: %s", e)
        yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
    finally:
        await provider.cleanup()


# ─── Server Management ─────────────────────────────────────────────────


@router.get("/api/servers")
async def list_servers():
    _ensure_config()
    servers = _load_servers()
    resolved = [_resolve_server(s) for s in servers]
    return JSONResponse({"servers": resolved})


@router.post("/api/servers")
async def add_server(data: dict):
    _ensure_config()
    servers = _load_servers()
    server = {
        "id": f"svr_{uuid.uuid4().hex[:12]}",
        "name": str(data.get("name", "New Server")),
        "provider": str(data.get("provider", "ollama")),
        "endpoint": str(data.get("endpoint", "http://localhost:11434")),
        "model": str(data.get("model", "gemma4:12b")),
        "api_key": str(data.get("api_key", "")),
        "vision": bool(data.get("vision", False)),
        "default": bool(data.get("default", False)),
    }
    servers.append(server)
    _save_servers(servers)
    log.info("Added server: %s (%s)", server["id"], server["name"])
    return JSONResponse({"server": _resolve_server(server)})


@router.get("/api/servers/{server_id}")
async def get_server(server_id: str):
    servers = _load_servers()
    for s in servers:
        if s["id"] == server_id:
            return JSONResponse({"server": _resolve_server(s)})
    return JSONResponse({"error": "Server not found"}, status_code=404)


@router.put("/api/servers/{server_id}")
async def update_server(server_id: str, data: dict):
    servers = _load_servers()
    for s in servers:
        if s["id"] == server_id:
            if "name" in data:
                s["name"] = str(data["name"])
            if "provider" in data:
                s["provider"] = str(data["provider"])
            if "endpoint" in data:
                s["endpoint"] = str(data["endpoint"])
            if "model" in data:
                s["model"] = str(data["model"])
            if "api_key" in data:
                s["api_key"] = str(data["api_key"])
            if "vision" in data:
                s["vision"] = bool(data["vision"])
            if "default" in data:
                s["default"] = bool(data["default"])
            _save_servers(servers)
            log.info("Updated server: %s", server_id)
            return JSONResponse({"server": _resolve_server(s)})
    return JSONResponse({"error": "Server not found"}, status_code=404)


@router.delete("/api/servers/{server_id}")
async def delete_server(server_id: str):
    servers = _load_servers()
    new_servers = [s for s in servers if s["id"] != server_id]
    if len(new_servers) == len(servers):
        return JSONResponse({"error": "Server not found"}, status_code=404)
    _save_servers(new_servers)
    log.info("Deleted server: %s", server_id)
    return JSONResponse({"ok": True})


@router.get("/api/servers/{server_id}/models")
async def list_models(server_id: str):
    servers = _load_servers()
    server = None
    for s in servers:
        if s["id"] == server_id:
            server = _resolve_server(s)
            break
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    models = await _fetch_models_from_provider(
        provider=server.get("provider", "ollama"),
        endpoint=server.get("endpoint", "http://localhost:11434"),
        api_key=server.get("api_key", ""),
    )
    return JSONResponse({"models": models})


@router.post("/api/models/fetch")
async def fetch_models(data: dict):
    models = await _fetch_models_from_provider(
        provider=str(data.get("provider", "ollama")),
        endpoint=str(data.get("endpoint", "")),
        api_key=str(data.get("api_key", "")),
    )
    return JSONResponse({"models": models})


# ─── Environment / Debug ──────────────────────────────────────────────


@router.get("/api/env")
async def env_defaults():
    _ensure_config()
    servers = _load_servers()
    if servers:
        s = _resolve_server(servers[0])
        return JSONResponse({
            "endpoint": s.get("endpoint", "http://localhost:11434"),
            "model": s.get("model", "gemma4:12b"),
            "provider": s.get("provider", "ollama"),
        })
    return JSONResponse({
        "endpoint": os.getenv("PELORUS_ENDPOINT", "http://localhost:11434"),
        "model": os.getenv("PELORUS_MODEL", "gemma4:12b"),
        "provider": os.getenv("PELORUS_PROVIDER", "ollama"),
    })


@router.get("/api/windows")
async def list_windows():
    return {"windows": await kwin_dbus.list_windows()}


@router.get("/api/state")
async def desktop_state():
    return {"state": await _text_screenshot()}


@router.get("/")
async def index():
    if STATIC.is_dir() and (STATIC / "index.html").exists():
        return FileResponse(str(STATIC / "index.html"))
    return JSONResponse({"status": "pelorus", "docs": "/docs"})
