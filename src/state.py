import asyncio
import logging
import os

from . import atspi
from . import kwin_dbus
from .utils import _get_screen_size

log = logging.getLogger(__name__)


def _desktop_icons_obstructed(
    icons_data: list[dict], windows: list[dict], desktop_pid: int | None
) -> bool:
    if not icons_data or not windows:
        return False
    for icon in icons_data:
        coords = icon.get("coords")
        if not coords:
            return False
        cx = coords[0] + coords[2] // 2
        cy = coords[1] + coords[3] // 2
        covered = False
        for w in windows:
            if w.get("minimized", False):
                continue
            if w.get("pid") == desktop_pid:
                continue
            wx, wy, ww, wh = w["x"], w["y"], w["width"], w["height"]
            if wx <= cx < wx + ww and wy <= cy < wy + wh:
                covered = True
                break
        if not covered:
            return False
    return True


async def _text_screenshot() -> str:
    desktop_pid = atspi._find_plasmashell_pid()
    screen_w, screen_h = await _get_screen_size()

    windows: list[dict] = []
    if desktop_pid is not None:
        try:
            windows = await kwin_dbus.list_windows()
        except Exception:
            windows = []

    env_name = os.getenv("PELORUS_ENVIRONMENT", "KDE Plasma Desktop (Linux)")
    lines = [f"=== {env_name} — Desktop ({len(windows)} windows, {screen_w}x{screen_h}) ==="]

    if desktop_pid is not None:
        raw = await asyncio.to_thread(atspi.explore_pid, desktop_pid, 3)
        if not raw.startswith("Error"):
            icons_data: list[dict] = []
            panel_elements: list[dict] = []
            panel_rect: tuple | None = None

            for l in raw.split("\n"):
                stripped = l.strip()
                info = atspi._get_role_line_info(stripped)
                if "[panel]" in stripped and info["coords"]:
                    panel_rect = info["coords"]
                if "[canvas]" in stripped and info["coords"]:
                    icons_data.append(info)
                if "[button]" in stripped and info["coords"]:
                    bx, by, bw, bh = info["coords"]
                    if panel_rect:
                        px, py, pw, ph = panel_rect
                        if py <= by <= py + ph or py <= by + bh <= py + ph:
                            panel_elements.append(info)
                    else:
                        panel_elements.append(info)

            if icons_data and not _desktop_icons_obstructed(icons_data, windows, desktop_pid):
                lines.append("\nDesktop Icons (double-click):")
                for icon in icons_data:
                    lines.append(f"  {icon['name'] or '(icon)'} at {icon['coords']}")

            if panel_elements:
                lines.append(f"\nTaskbar:")
                for el in panel_elements:
                    x, y, w, h = el["coords"]
                    lines.append(f"  [{el['name'] or '?'}] at ({x},{y},{w},{h})")

    for w in windows:
        pid = w["pid"]
        title = w.get("title", "")
        wx, wy, ww, wh = w["x"], w["y"], w["width"], w["height"]
        lines.append(f"\nPID {pid}: \"{title}\" \u2192 [{wx},{wy}] {ww}x{wh}")

    if desktop_pid:
        popups = await asyncio.to_thread(atspi.get_visible_popups, desktop_pid, screen_w, screen_h)
        if popups:
            for popup in popups:
                children = popup.get("children", [])
                if not children:
                    continue
                cx, cy, cw, ch = popup["coords"]["x"], popup["coords"]["y"], popup["coords"]["width"], popup["coords"]["height"]
                pname = popup["name"] or "Application Launcher"

                def _format_items(items, indent):
                    out = []
                    for item in items:
                        parts = [f"  {'  ' * indent}[{item['role']}]"]
                        if item["name"]:
                            parts.append(f' "{item["name"]}"')
                        if item["coords"]:
                            parts.append(f" ({item['coords']['x']},{item['coords']['y']},{item['coords']['width']},{item['coords']['height']})")
                        istates = [s for s in item.get("states", []) if s not in ("SENSITIVE", "ENABLED", "FOCUSABLE")]
                        if istates:
                            parts.append(f" [{', '.join(istates)}]")
                        out.append("".join(parts))
                        sub = item.get("children")
                        if sub:
                            out.extend(_format_items(sub, indent + 1))
                    return out

                lines.append(f"\nStart Menu: \"{pname}\" at ({cx},{cy},{cw},{ch})")
                lines.extend(_format_items(children, 0))

    return "\n".join(lines)
