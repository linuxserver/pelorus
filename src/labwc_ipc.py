import asyncio
import json
import os
import signal


SOCKET = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "labwc.sock")


async def _send_command(command: str) -> str:
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(SOCKET), timeout=5
    )
    try:
        writer.write((command + "\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(-1), timeout=5)
        return data.decode().strip()
    finally:
        writer.close()
        await writer.wait_closed()


def _parse_windows(text: str) -> list[dict]:
    obj = json.loads(text)
    if not isinstance(obj, list):
        return []
    windows = []
    for w in obj:
        if not isinstance(w, dict):
            continue
        windows.append({
            "pid": w.get("pid"),
            "x": w.get("x"),
            "y": w.get("y"),
            "width": w.get("width"),
            "height": w.get("height"),
            "title": w.get("title", ""),
            "minimized": bool(w.get("minimized", False)),
            "fullscreen": bool(w.get("fullscreen", False)),
            "maximized": bool(w.get("maximized", False)),
            "app_id": w.get("app_id", ""),
            "focused": bool(w.get("focused", False)),
            "tiled": bool(w.get("tiled", False)),
        })
    return windows


def _parse_window(text: str) -> dict | None:
    obj = json.loads(text)
    if not isinstance(obj, dict) or not obj:
        return None
    return {
        "pid": obj.get("pid"),
        "x": obj.get("x"),
        "y": obj.get("y"),
        "width": obj.get("width"),
        "height": obj.get("height"),
        "title": obj.get("title", ""),
        "minimized": bool(obj.get("minimized", False)),
        "fullscreen": bool(obj.get("fullscreen", False)),
        "maximized": bool(obj.get("maximized", False)),
        "app_id": obj.get("app_id", ""),
        "focused": bool(obj.get("focused", False)),
        "tiled": bool(obj.get("tiled", False)),
    }


async def list_windows() -> list[dict]:
    raw = await asyncio.wait_for(_send_command("GET_WINDOWS"), timeout=10)
    return _parse_windows(raw)


async def get_window_by_pid(pid: int) -> dict | None:
    raw = await asyncio.wait_for(
        _send_command(f"GET_WINDOW_BY_PID {pid}"), timeout=10
    )
    return _parse_window(raw)


async def close_window(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
