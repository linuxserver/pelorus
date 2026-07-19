import logging
import os
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from .providers import (
    LLMProvider,
    OllamaProvider,
    OpenAICompatProvider,
    GeminiProvider,
)
from .utils import _load_servers, _resolve_server, is_b64_blob

log = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""You are a desktop automation agent controlling a Linux desktop.

CORE PRINCIPLES
1. Observe First — check what's on screen before acting
2. Verify — confirm each action changed the screen as expected using desktop_state or explore_window
3. Use coordinates as-is — the data you receive has the exact click point, do not adjust or center

TOOLS
Call `computer(action="action_name", ...)` with the parameters listed below.

Common actions:

  type(text)                         — type a blob of text into the focused field
  key(text)                          — send single keystroke: "Enter", "Tab", "Ctrl+C", etc.
  left_click(coordinate)             — click at screen [x, y]
  right_click(coordinate)            — right-click at screen [x, y]
  double_click(coordinate)           — double-click at screen [x, y]
  middle_click(coordinate)           — middle-click at screen [x, y]
  triple_click(coordinate)           — triple-click at screen [x, y]
  mouse_move(coordinate)             — move cursor to screen [x, y]
  left_click_drag(start_coordinate, coordinate)  — drag from start to end
  left_mouse_down / left_mouse_up    — hold or release mouse button
  hold_key(text)                     — hold a key down (e.g. "Shift")
  scroll(coordinate, scroll_direction, scroll_amount)  — scroll at position
  desktop_state                      — brief overview of visible windows and taskbar
  cursor_position                    — get current mouse position
  wait(duration)                     — pause for N seconds
  close_window(pid)                  — close a window

--- explore_window ---

This is your primary way to inspect application contents.

explore_window(pid) returns structured accessibility data with
*absolute screen coordinates* for every clickable element.

If an app lacks accessibility data, it falls back to returning an image
of the window along with its screen position and size for you to analyze.

Use explore_window after every click or type to see what changed.

When the accessibility data seems incomplete or the window appears as a
blank canvas, add force_screenshot=true to force image-only output
(only available when your model supports vision — use as last resort).

The current date is {datetime.today().strftime("%A, %B %-d, %Y")}."""


def build_provider(config: dict[str, str]) -> LLMProvider:
    ptype = config.get("provider", "ollama")
    endpoint = config.get("endpoint", "http://localhost:11434")
    model = config.get("model", "unknown")
    api_key = config.get("api_key", "")
    log.info("build_provider type=%s endpoint=%s model=%s", ptype, endpoint, model)
    if ptype == "ollama":
        return OllamaProvider(endpoint=endpoint, model=model)
    if ptype == "gemini":
        return GeminiProvider(endpoint=endpoint, model=model, api_key=api_key or None)
    return OpenAICompatProvider(endpoint=endpoint, model=model, api_key=api_key or None)


def _extract_final_text(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            blocks = msg.get("content", [])
            if isinstance(blocks, list):
                texts = [b for b in blocks if b.get("type") == "text"]
                if texts:
                    t = texts[0]["text"]
                    if t.strip() and not is_b64_blob(t):
                        return t
                thinks = [b for b in blocks if b.get("type") == "thinking"]
                if thinks:
                    t = thinks[0].get("thinking", "")
                    if t.strip() and not is_b64_blob(t):
                        return t
    return None


# ─── Pydantic models ──────────────────────────────────────────────────


class RunRequest(BaseModel):
    prompt: str
    server_id: str | None = None
    provider: str | None = None
    endpoint: str | None = None
    model: str | None = None
    api_key: str | None = None
    vision: bool | None = None
    max_steps: int = 50
    response_format: str = "default"
    stream: bool = False

    @field_validator("response_format")
    @classmethod
    def check_format(cls, v: str) -> str:
        if v not in ("minimal", "default", "verbose", "thoughts"):
            raise ValueError("must be 'minimal', 'default', 'verbose', or 'thoughts'")
        return v


class StepInfo(BaseModel):
    text: str = ""
    tool_call: dict | None = None
    tool_result: str = ""
    tool_screenshot: str | None = None
    thinking: str = ""


class RunResponse(BaseModel):
    success: bool
    final_text: str = ""
    steps: list[StepInfo] = []
    initial_screenshot: str | None = None
    final_screenshot: str | None = None


def _resolve_run_config(req: RunRequest) -> dict:
    servers = _load_servers()
    server_config = {}
    if req.server_id:
        for s in servers:
            if s["id"] == req.server_id:
                server_config = _resolve_server(s)
                break
    elif servers:
        default = next((s for s in servers if s.get("default")), None)
        server_config = _resolve_server(default or servers[0])

    config = dict(server_config)
    if req.provider is not None:
        config["provider"] = req.provider
    if req.endpoint is not None:
        config["endpoint"] = req.endpoint
    if req.model is not None:
        config["model"] = req.model
    if req.api_key is not None:
        config["api_key"] = req.api_key
    if req.vision is not None:
        config["vision"] = req.vision
    config.setdefault("provider", "ollama")
    config.setdefault("endpoint", "http://localhost:11434")
    config.setdefault("model", "gemma4:12b")
    config.setdefault("api_key", "")
    config.setdefault("vision", False)
    log.info("resolved config provider=%s endpoint=%s model=%s vision=%s", config["provider"], config["endpoint"], config["model"], config["vision"])
    return config
