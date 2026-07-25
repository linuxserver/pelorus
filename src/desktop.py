import os


def _is_labwc() -> bool:
    return os.path.exists(os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "labwc.sock"))


def _environment() -> str:
    return "labwc Wayland (Linux)" if _is_labwc() else "KDE Plasma Desktop (Linux)"


def _get_backend():
    from . import labwc_ipc, kwin_dbus
    return labwc_ipc if _is_labwc() else kwin_dbus


async def list_windows(*args, **kwargs):
    return await _get_backend().list_windows(*args, **kwargs)


async def get_window_by_pid(*args, **kwargs):
    return await _get_backend().get_window_by_pid(*args, **kwargs)


async def close_window(*args, **kwargs):
    return await _get_backend().close_window(*args, **kwargs)
