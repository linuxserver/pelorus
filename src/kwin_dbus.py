import asyncio
import json
import os
import re
import signal
import subprocess


QDBUS = os.environ.get("QDBUS_BIN", "qdbus6")


def _run_qdbus(service: str, path: str, method: str, *args: str, literal: bool = False) -> str:
    cmd = [QDBUS]
    if literal:
        cmd.insert(1, "--literal")
    cmd += [service, path, method, *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"qdbus error: {result.stderr.strip()}")
    return result.stdout.strip()


def _iter_uuids(text: str) -> list[str]:
    uuids = set()
    for m in re.finditer(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', text, re.I):
        uuids.add(m.group(0))
    return list(uuids)


def _atspi_pid_map() -> dict[str, int]:
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return {}

    mapping: dict[str, int] = {}
    try:
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            try:
                name = (app.get_name() or "").lower()
                pid = app.get_process_id()
                if pid > 0:
                    mapping[name] = pid
                    seg = name.rsplit(".", 1)[-1]
                    if seg != name:
                        mapping[seg] = pid
                    for j in range(app.get_child_count()):
                        child = app.get_child_at_index(j)
                        cname = (child.get_name() or "").lower().strip()
                        if cname:
                            mapping[cname] = pid
            except Exception:
                pass
    except Exception:
        pass
    return mapping


def _match_pid(
    resource_class: str,
    resource_name: str,
    caption: str,
    atspi_map: dict[str, int],
) -> int | None:
    if resource_name:
        rn = resource_name.lower()
        if rn in atspi_map:
            return atspi_map[rn]

    if resource_class:
        rc = resource_class.lower()
        if rc in atspi_map:
            return atspi_map[rc]
        seg = rc.rsplit(".", 1)[-1]
        if seg != rc and seg in atspi_map:
            return atspi_map[seg]

    if caption:
        cap_normalized = re.sub(r'\s+', ' ', caption).strip().lower()
        if cap_normalized in atspi_map:
            return atspi_map[cap_normalized]

    return None


async def list_windows() -> list[dict]:
    raw = await asyncio.to_thread(
        _run_qdbus, "org.kde.KWin", "/WindowsRunner", "org.kde.krunner1.Match", " ", literal=True
    )

    atspi_map = await asyncio.to_thread(_atspi_pid_map)

    windows = []
    for uuid in _iter_uuids(raw):
        info_raw = await asyncio.to_thread(
            _run_qdbus, "org.kde.KWin", "/KWin", "org.kde.KWin.getWindowInfo", uuid, literal=True
        )
        info = _parse_variant_map(info_raw)
        if not info:
            continue
        minimized = info.get("minimized", False)
        if isinstance(minimized, str):
            minimized = minimized.lower() == "true"
        fullscreen = info.get("fullscreen", False)
        if isinstance(fullscreen, str):
            fullscreen = fullscreen.lower() == "true"

        pid = info.get("pid")
        if pid is None:
            pid = _match_pid(
                info.get("resourceClass", ""),
                info.get("resourceName", ""),
                info.get("windowTitle", info.get("caption", "")),
                atspi_map,
            )

        windows.append({
            "uuid": uuid,
            "pid": pid,
            "x": info.get("x"),
            "y": info.get("y"),
            "width": info.get("width"),
            "height": info.get("height"),
            "title": info.get("windowTitle", info.get("caption", "")),
            "minimized": minimized,
            "fullscreen": fullscreen,
            "maximizeVertical": info.get("maximizeVertical", 0),
            "maximizeHorizontal": info.get("maximizeHorizontal", 0),
        })
    return windows


async def get_window_by_pid(pid: int) -> dict | None:
    windows = await list_windows()
    for w in windows:
        if w["pid"] == pid:
            return w
    return None


def _parse_variant_map(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    result = {}
    for m in re.finditer(r'"(\w+)"\s*=\s*\[Variant\(.*?\):\s*(.*?)\]', text):
        key = m.group(1)
        val = m.group(2).strip().rstrip("}")
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        else:
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
        result[key] = val
    return result


async def close_window(pid: int) -> bool:
    """Close a window by PID using SIGTERM."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
