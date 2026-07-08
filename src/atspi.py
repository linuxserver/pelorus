import logging
import re

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

from . import kwin_dbus

log = logging.getLogger(__name__)


def _get_app_by_pid(pid: int) -> Atspi.Accessible | None:
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        app_pid = _get_pid(app)
        if app_pid == pid:
            return app
    return None


def _get_pid(obj: Atspi.Accessible) -> int:
    try:
        return obj.get_process_id()
    except Exception:
        return -1


def _get_component_info(obj: Atspi.Accessible) -> dict:
    try:
        if not obj.is_component():
            return {}
        ext = obj.get_extents(Atspi.CoordType.SCREEN)
        return {"x": ext.x, "y": ext.y, "width": ext.width, "height": ext.height}
    except Exception:
        return {}


def _get_name(obj: Atspi.Accessible) -> str:
    try:
        return obj.get_name() or ""
    except Exception:
        return ""


def _get_role(obj: Atspi.Accessible) -> str:
    try:
        return obj.get_role_name()
    except Exception:
        return "?"


def _get_states(obj: Atspi.Accessible) -> list[str]:
    try:
        state_set = obj.get_state_set()
        return [s.name for s in state_set.get_states()]
    except Exception:
        return []


def _is_showing(obj: Atspi.Accessible) -> bool:
    return "SHOWING" in _get_states(obj)


def _format_element(obj: Atspi.Accessible, depth: int = 0, max_depth: int = 100) -> str:
    if depth > max_depth:
        return ""
    prefix = "  " * depth
    name = _get_name(obj)
    role = _get_role(obj)
    component = _get_component_info(obj)
    states = _get_states(obj)

    try:
        child_count = obj.get_child_count()
    except Exception:
        child_count = 0

    children = []
    for i in range(min(child_count, 200)):
        try:
            child = obj.get_child_at_index(i)
            child_str = _format_element(child, depth + 1, max_depth)
            if child_str:
                children.append(child_str)
        except Exception:
            pass

    showing = _is_showing(obj)
    if not showing and not children:
        return ""

    parts = [f"{prefix}[{role}]"]
    if name:
        parts.append(f' "{name}"')
    if component:
        coords = f" ({component['x']}, {component['y']}, {component['width']}, {component['height']})"
        parts.append(coords)
    if states:
        useful = [s for s in states if s not in ("SENSITIVE", "ENABLED", "SHOWING", "VISIBLE", "FOCUSABLE")]
        if useful:
            parts.append(f" [{', '.join(useful)}]")

    line = "".join(parts)
    if children:
        line += "\n" + "\n".join(children)
    return line


def explore_pid(pid: int, max_depth: int = 100) -> str:
    app = _get_app_by_pid(pid)
    if app is None:
        return f"Error: No AT-SPI application found for PID {pid}"
    return _format_element(app, max_depth=max_depth)


def get_element_by_path(pid: int, path: list[int]) -> dict | None:
    desktop = Atspi.get_desktop(0)
    current = None
    for i, idx in enumerate(path):
        if i == 0:
            for j in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(j)
                if _get_pid(app) == pid:
                    current = app
                    break
            if current is None:
                return None
        else:
            try:
                current = current.get_child_at_index(idx)
            except Exception:
                return None
    if current is None:
        return None
    return {
        "name": _get_name(current),
        "role": _get_role(current),
        "component": _get_component_info(current),
        "states": _get_states(current),
    }


# ─── AT-SPI helpers ────────────────

_COORD_RE = re.compile(r"\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)")


def _find_plasmashell_pid() -> int | None:
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if "plasmashell" in (app.get_name() or "").lower():
            return app.get_process_id()
    return None


def _get_frame_extents(pid: int) -> tuple:
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app.get_process_id() != pid:
            continue
        for j in range(app.get_child_count()):
            child = app.get_child_at_index(j)
            role = child.get_role_name()
            if role in ("frame", "dialog", "file chooser"):
                ext = child.get_extents(Atspi.CoordType.WINDOW)
                return ext.x, ext.y, ext.width, ext.height
    return (0, 0, 0, 0)


async def _enrich_window_tree(pid: int, raw_tree: str) -> str:
    if _find_plasmashell_pid() is None:
        return raw_tree
    try:
        windows = await kwin_dbus.list_windows()
    except Exception:
        return raw_tree

    pid_windows = [w for w in windows if w["pid"] == pid]
    if not pid_windows:
        return raw_tree

    lines = raw_tree.split("\n")
    depths = []
    for tl in lines:
        stripped = tl.strip()
        d = (len(tl) - len(tl.lstrip())) // 2 if stripped else 0
        depths.append(d)

    sections = []
    matched_win_ids = set()
    for i, tl in enumerate(lines):
        if depths[i] != 1:
            continue
        stripped = tl.strip()
        if not stripped.startswith(("[frame]", "[dialog]", "[file chooser]")):
            continue
        m = _COORD_RE.search(stripped)
        if not m:
            continue
        atspi_w, atspi_h = int(m.group(3)), int(m.group(4))
        if atspi_w == 0 or atspi_h == 0:
            continue

        name_m = re.search(r'"(.+?)"', stripped)
        atspi_name = name_m.group(1) if name_m else ""

        matched = None
        if atspi_name:
            for w in pid_windows:
                title = w.get("title", "")
                tn = re.sub(r'\s+', ' ', title).strip()
                an = re.sub(r'\s+', ' ', atspi_name).strip()
                if tn == an or tn.startswith(an):
                    matched = w
                    break

        if matched is None or id(matched) in matched_win_ids:
            continue
        matched_win_ids.add(id(matched))

        titlebar = matched["height"] - atspi_h
        if titlebar < 0:
            titlebar = 0

        section_end = len(lines)
        for s in sections:
            if s["start"] > i:
                section_end = s["start"]
                break
        padding_left = 0
        padding_top = 0
        child_coords = []
        for j in range(i + 1, section_end):
            if depths[j] != 2:
                continue
            cm = _COORD_RE.search(lines[j])
            if cm:
                cx, cy = int(cm.group(1)), int(cm.group(2))
                if cx >= 0 and cy >= 0:
                    child_coords.append((cx, cy))
        if child_coords:
            padding_left = min(c[0] for c in child_coords)
            padding_top = min(c[1] for c in child_coords)

        sections.append({
            "start": i,
            "end": len(lines),
            "win": matched,
            "titlebar": titlebar,
            "padding_left": padding_left,
            "padding_top": padding_top,
        })

    if not sections:
        return raw_tree

    for idx in range(len(sections) - 1):
        sections[idx]["end"] = sections[idx + 1]["start"]

    primary = pid_windows[0]
    primary_titlebar = 0
    primary_padding_left = 0
    primary_padding_top = 0
    for s in sections:
        if s["win"] is primary:
            primary_titlebar = s["titlebar"]
            primary_padding_left = s["padding_left"]
            primary_padding_top = s["padding_top"]
            break
    else:
        for i, tl in enumerate(lines):
            if depths[i] != 1:
                continue
            stripped = tl.strip()
            m = _COORD_RE.search(stripped)
            if not m:
                continue
            atspi_w, atspi_h = int(m.group(3)), int(m.group(4))
            if atspi_w == primary["width"] and atspi_h <= primary["height"]:
                primary_titlebar = max(0, primary["height"] - atspi_h)
                break

    result = []
    for i, tl in enumerate(lines):
        enriched = tl
        section = None
        for s in sections:
            if s["start"] <= i < s["end"]:
                section = s
                break
        m = _COORD_RE.search(enriched)
        if m:
            rx, ry, rw, rh = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            if rw != 0 or rh != 0:
                if section:
                    wx, wy = section["win"]["x"], section["win"]["y"]
                    if depths[i] >= 2:
                        sx = wx + rx - section["padding_left"]
                        sy = wy + section["titlebar"] + ry - section["padding_top"]
                    else:
                        sx = wx + rx
                        sy = wy + section["titlebar"] + ry
                else:
                    wx, wy = primary["x"], primary["y"]
                    if depths[i] >= 2:
                        sx = wx + rx - primary_padding_left
                        sy = wy + primary_titlebar + ry - primary_padding_top
                    else:
                        sx = wx + rx
                        sy = wy + primary_titlebar + ry
                enriched = enriched[:m.start()] + f"\u2192 screen ({sx},{sy}) {rw}x{rh}"
        result.append(enriched)

    return "\n".join(result)


def _get_role_line_info(line: str) -> dict:
    name = ""
    coords = None
    m = re.search(r'"(.+?)"', line)
    if m:
        name = m.group(1)
    m = _COORD_RE.search(line)
    if m:
        coords = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return {"name": name, "coords": coords}


def _get_frame_screen_offset(app_pid: int) -> tuple[int, int] | None:
    S = Atspi.StateType
    try:
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app.get_process_id() != app_pid:
                continue
            for j in range(app.get_child_count()):
                child = app.get_child_at_index(j)
                if child.get_role_name() != "frame":
                    continue
                def _has_apps_tab(obj) -> bool:
                    try:
                        if obj.get_role_name() == "page tab" and "Applications" in (obj.get_name() or ""):
                            return True
                        for k in range(obj.get_child_count()):
                            if _has_apps_tab(obj.get_child_at_index(k)):
                                return True
                    except Exception:
                        pass
                    return False
                if _has_apps_tab(child):
                    ss = child.get_state_set()
                    for s in ss.get_states():
                        if int(s) == int(S.SHOWING):
                            ext = child.get_extents(Atspi.CoordType.SCREEN)
                            return (ext.x, ext.y)
                    return None
        return None
    except Exception:
        return None


def _popup_children(obj, max_depth: int = 3, depth: int = 0) -> list[dict]:
    """Recursively collect accessible children of a popup frame.

    Returns children with names or SHOWING state, flattened into levels
    so the caller can display them with proper indentation.
    """
    results = []
    if depth >= max_depth:
        return results
    try:
        for i in range(obj.get_child_count()):
            try:
                child = obj.get_child_at_index(i)
                child_name = _get_name(child)
                child_role = _get_role(child)
                child_ext = _get_component_info(child)
                child_states = _get_states(child)
                if not child_name and "SHOWING" not in child_states:
                    continue
                entry: dict = {
                    "name": child_name,
                    "role": child_role,
                    "coords": child_ext if child_ext else None,
                    "states": child_states,
                }
                sub = _popup_children(child, max_depth, depth + 1)
                if sub:
                    entry["children"] = sub
                results.append(entry)
            except Exception:
                pass
    except Exception:
        pass
    return results


def get_visible_popups(pid: int, screen_width: int = 2560, screen_height: int = 1280) -> list[dict] | None:
    """Find visible popup frames in the given app (e.g. start menu) and return
    their children recursively (up to 3 levels deep).

    This bypasses _format_element filtering so it can see elements that may
    not have SHOWING state or children. Returns None if no popup is open.
    """
    app = _get_app_by_pid(pid)
    if app is None:
        return None
    try:
        popups = []
        for i in range(app.get_child_count()):
            child = app.get_child_at_index(i)
            role = _get_role(child)
            if role not in ("frame", "window", "dialog", "pop-up menu", "menu"):
                continue
            ext = _get_component_info(child)
            if not ext:
                continue
            cx, cy, cw, ch = ext["x"], ext["y"], ext["width"], ext["height"]

            # Skip the main desktop frame (covers full screen)
            if cx == 0 and cy == 0 and cw >= screen_width and ch >= screen_height:
                continue

            # Skip panels (narrow bar along edge of screen)
            if ch < 100 and cw >= screen_width * 0.85:
                continue

            child_states = _get_states(child)
            if "SHOWING" not in child_states:
                continue

            children = _popup_children(child, max_depth=3)
            popups.append({
                "name": _get_name(child),
                "role": role,
                "coords": ext,
                "states": child_states,
                "children": children,
            })
        return popups if popups else None
    except Exception:
        return None


async def screen_coords(pid: int, element: dict) -> tuple[int, int] | None:
    window = await kwin_dbus.get_window_by_pid(pid)
    if window is None:
        log.error("No KWin window for PID %d", pid)
        return None

    wx, wy, wh = window["x"], window["y"], window["height"]
    comp = element.get("component", {})
    ax, ay = comp.get("x", 0), comp.get("y", 0)

    frame = _get_frame_extents(pid)
    if frame:
        _, _, _, frame_h = frame
        titlebar = wh - frame_h if frame_h > 0 else 0
    else:
        titlebar = 36

    sx, sy = wx + ax, wy + titlebar + ay

    log.debug(
        "screen_coords PID=%d element=(%d,%d) window=(%d,%d) wh=%d frame_h=%d titlebar=%d -> (%d,%d)",
        pid, ax, ay, wx, wy, wh,
        frame[3] if frame else 0,
        titlebar, sx, sy,
    )
    return sx, sy
