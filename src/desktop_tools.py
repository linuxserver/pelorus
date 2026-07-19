import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from . import atspi
from . import kwin_dbus
from .state import _text_screenshot
from .utils import _cu_action, _get_screen_size
from .tools.base import ToolResult

log = logging.getLogger(__name__)

_ACTION_ENUM = [
    "desktop_state", "explore_window",
    "key", "type", "mouse_move", "left_click", "right_click",
    "double_click", "middle_click", "triple_click",
    "left_click_drag", "left_mouse_down", "left_mouse_up",
    "scroll", "cursor_position", "hold_key", "wait",
    "close_window",
]


class DesktopTool:
    name = "computer"

    def __init__(self, vision_supported: bool = False):
        self.vision_supported = vision_supported

    async def _capture_window_region(self, pid: int) -> ToolResult | None:
        try:
            windows = await kwin_dbus.list_windows()
        except Exception:
            return None
        pid_windows = [w for w in windows if w["pid"] == pid]
        if not pid_windows:
            return None
        w = pid_windows[0]

        frame = atspi._get_frame_extents(pid)
        if frame and frame[2] > 0 and frame[3] > 0:
            fx, fy, fw, fh = frame
            titlebar = w["height"] - fh
            if titlebar < 0:
                sx, sy = w["x"], w["y"]
                sw, sh = fw, fh
            else:
                sx, sy = w["x"] + fx, w["y"] + titlebar
                sw, sh = fw, fh
        else:
            sx, sy, sw, sh = w["x"], w["y"], w["width"], w["height"]

        try:
            resp = await _cu_action({"action": "zoom", "region": [sx, sy, sx + max(sw, 1), sy + max(sh, 1)]})
            data = resp.get("data")
            if data:
                title = w.get("title", "")
                screen_w, screen_h = await _get_screen_size()
                return ToolResult(
                    output=f"[Screenshot of PID {pid} window '{title}' starts at screen ({sx},{sy}) size {sw}x{sh}. Total screen: {screen_w}x{screen_h}. Click coordinates must be relative to screenshot origin ({sx},{sy}).]",
                    base64_image=data,
                )
        except Exception as e:
            log.error("Failed to capture window region for PID %d: %s", pid, e)
        return None

    def to_params(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Desktop control: mouse, keyboard, screen state, and window drill-in.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": _ACTION_ENUM,
                            "description": "The action to perform",
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to type or key name for key/hold_key actions",
                        },
                        "coordinate": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": "[x, y] screen coordinate for click/move actions",
                        },
                        "pid": {
                            "type": "integer",
                            "description": "Process ID for explore_window and close_window actions",
                        },
                        "force_screenshot": {
                            "type": "boolean",
                            "description": "explore_window: skip accessibility tree, return screenshot instead (vision models only, last resort)",
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
                        "scroll_amount": {"type": "integer", "minimum": 0},
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
        }

    async def __call__(self, **kwargs) -> ToolResult:
        action = kwargs.get("action", "")
        if action == "desktop_state":
            return ToolResult(output=await _text_screenshot())
        if action == "screenshot":
            resp = await _cu_action({"action": "screenshot"})
            data = resp.get("data")
            if data:
                return ToolResult(base64_image=data)
            return ToolResult(output=await _text_screenshot())
        if action == "explore_window":
            pid = kwargs.get("pid")
            if not pid:
                return ToolResult(error="pid required for explore_window")
            force_ss = kwargs.get("force_screenshot", False)
            if force_ss:
                if self.vision_supported:
                    result = await self._capture_window_region(int(pid))
                    if result:
                        return result
                return ToolResult(output=f"force_screenshot requested but model does not support vision for PID {pid}.")
            raw = await asyncio.to_thread(atspi.explore_pid, int(pid))
            if raw.startswith("Error"):
                if self.vision_supported:
                    result = await self._capture_window_region(int(pid))
                    if result:
                        return result
                return ToolResult(output=f"This window (PID {pid}) uses non-text rendering (canvas, graphics, etc.) that the accessibility system cannot read. Your model does not support vision, so it cannot visually inspect this window. It is impossible to access the contents of this window. Stop making tool calls — this task cannot be completed.")
            enriched = await atspi._enrich_window_tree(int(pid), raw)
            return ToolResult(output=enriched)
        if action == "close_window":
            pid = kwargs.get("pid")
            if not pid:
                return ToolResult(error="pid required for close_window")
            ok = await kwin_dbus.close_window(int(pid))
            if ok:
                return ToolResult(output=f"Window PID {pid} closed.")
            return ToolResult(output=f"Failed to close window for PID {pid}")
        try:
            resp = await _cu_action(kwargs)
            err = resp.get("error")
            if err:
                return ToolResult(error=err)
            data = resp.get("data")
            text = resp.get("text")
            if data:
                return ToolResult(base64_image=data)
            if text:
                return ToolResult(output=text)
            return ToolResult(output=f"Action '{action}' completed.")
        except Exception as e:
            log.error("CU action failed for %s: %s", action, e)
            return ToolResult(error=str(e))

    async def cleanup(self):
        pass


# ─── Task Queue ─────────────────────────────────────────────────────────


@dataclass
class TaskQueue:
    tasks: list[dict] = field(default_factory=list)
    index: int = 0

    def add(self, description: str, priority: str = "NORMAL"):
        self.tasks.append({"description": description, "status": "pending"})

    def complete_current(self) -> bool:
        if self.index < len(self.tasks):
            self.tasks[self.index]["status"] = "completed"
            self.index += 1
        return self.index < len(self.tasks)

    @property
    def current(self) -> dict | None:
        if self.index < len(self.tasks):
            return self.tasks[self.index]
        return None

    @property
    def is_done(self) -> bool:
        return len(self.tasks) > 0 and self.index >= len(self.tasks)


class CreateTaskTool:
    name = "create_task"

    def __init__(self, task_queue: TaskQueue):
        self.task_queue = task_queue

    def to_params(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Break a complex request into ordered subtasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Description of the subtask",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["LOW", "NORMAL", "HIGH"],
                        },
                    },
                    "required": ["description"],
                },
            },
        }

    async def __call__(self, **kwargs) -> ToolResult:
        description = kwargs.get("description", "")
        if not description:
            return ToolResult(error="description is required")
        self.task_queue.add(description, kwargs.get("priority", "NORMAL"))
        return ToolResult(output=f"Subtask queued: {description}")

    async def cleanup(self):
        pass


class SetTaskStatusTool:
    name = "set_task_status"

    def __init__(self, task_queue: TaskQueue):
        self.task_queue = task_queue

    def to_params(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Mark the current subtask as completed and advance.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["completed", "failed"],
                            "description": "Status for the current subtask",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Optional summary of what was done",
                        },
                    },
                    "required": ["status"],
                },
            },
        }

    async def __call__(self, **kwargs) -> ToolResult:
        status = kwargs.get("status", "")
        if status not in ("completed", "failed"):
            return ToolResult(error="status must be 'completed' or 'failed'")
        has_more = self.task_queue.complete_current()
        if has_more:
            return ToolResult(
                output=f"Task marked {status}. Next: {self.task_queue.current['description']}"
            )
        return ToolResult(output=f"Task marked {status}. All tasks complete.")

    async def cleanup(self):
        pass
