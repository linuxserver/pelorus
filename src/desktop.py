import os

_labwc = os.path.exists(os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "labwc.sock"))

if _labwc:
    from . import labwc_ipc as _backend
else:
    from . import kwin_dbus as _backend

list_windows = _backend.list_windows
get_window_by_pid = _backend.get_window_by_pid
close_window = _backend.close_window

ENVIRONMENT = "labwc Wayland (Linux)" if _labwc else "KDE Plasma Desktop (Linux)"
