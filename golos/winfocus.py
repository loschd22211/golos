"""Работа с активным окном Windows: не воровать фокус и возвращать его.

Когда вы нажимаете кнопку в оверлее, Windows переключает фокус на оверлей, и
текст мог бы уйти не туда. Поэтому:

* окно оверлея создаётся с ``WS_EX_NOACTIVATE`` — клик по нему не забирает фокус;
* на всякий случай запоминаем окно, которое было активным, и перед вставкой
  возвращаем фокус ему (``SetForegroundWindow``).

Всё на ctypes, поэтому лишних зависимостей нет. Вне Windows функции — заглушки.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SW_RESTORE = 9

_IS_WINDOWS = sys.platform == "win32"


def _user32():
    import ctypes

    return ctypes.windll.user32


def get_foreground_window() -> int | None:
    """HWND окна, которое сейчас активно (куда пойдёт текст)."""
    if not _IS_WINDOWS:  # pragma: no cover
        return None
    try:
        hwnd = _user32().GetForegroundWindow()
        return int(hwnd) if hwnd else None
    except Exception as exc:  # pragma: no cover
        log.debug("GetForegroundWindow недоступен: %s", exc)
        return None


def restore_foreground_window(hwnd: int | None) -> bool:
    """Сделать окно ``hwnd`` активным. True — если получилось."""
    if not _IS_WINDOWS or not hwnd:  # pragma: no cover
        return False
    try:
        user32 = _user32()
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return bool(user32.GetForegroundWindow() == hwnd)
    except Exception as exc:  # pragma: no cover
        log.debug("SetForegroundWindow не сработал: %s", exc)
        return False


def make_no_activate(root) -> bool:
    """Сделать окно tkinter не забирающим фокус (Win32, WS_EX_NOACTIVATE)."""
    return set_extended_style(root, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def clear_no_activate(root) -> bool:
    """Вернуть обычное поведение (нужно для диалога настроек)."""
    return set_extended_style(root, 0, remove=WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def set_extended_style(root, flags: int, remove: int = 0) -> bool:
    """Применить расширенный стиль к окну и его корневому владельцу.

    У Tk с ``overrideredirect`` настоящий HWND — родитель ``winfo_id()``,
    поэтому стиль ставим на оба, иначе клик всё равно забирает фокус.
    """
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    ok = False
    for getter in ("winfo_id", "wm_frame"):
        try:
            root.update_idletasks()
            method = getattr(root, getter, None)
            if method is None:
                continue
            hwnd = int(method())
            if not hwnd:
                continue
            user32 = _user32()
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | flags) & ~remove,
            )
            ok = True
        except Exception as exc:  # pragma: no cover
            log.debug("Стиль окна (%s) не применён: %s", getter, exc)
    return ok


def keep_on_top(root, topmost: bool = True) -> None:
    """Закрепить окно поверх остальных, не активируя его."""
    if not _IS_WINDOWS:  # pragma: no cover
        try:
            root.attributes("-topmost", topmost)
        except Exception:
            pass
        return
    try:
        hwnd = int(root.winfo_id())
        _user32().SetWindowPos(
            hwnd,
            HWND_TOPMOST if topmost else 0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception as exc:  # pragma: no cover
        log.debug("SetWindowPos не сработал: %s", exc)


def top_window_at_cursor() -> int | None:  # pragma: no cover - только Windows
    """Окно под курсором (не используется, но полезно для отладки)."""
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        _user32().GetCursorPos(ctypes.byref(point))
        return int(_user32().WindowFromPoint(point))
    except Exception:
        return None


def window_pid(hwnd: int | None) -> int | None:
    """PID процесса, которому принадлежит окно."""
    if not _IS_WINDOWS or not hwnd:  # pragma: no cover
        return None
    try:
        import ctypes

        pid = ctypes.c_ulong(0)
        _user32().GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value) or None
    except Exception as exc:  # pragma: no cover
        log.debug("GetWindowThreadProcessId не сработал: %s", exc)
        return None


def window_title(hwnd: int | None) -> str:
    """Заголовок окна (для журнала — куда именно уходит текст)."""
    if not _IS_WINDOWS or not hwnd:  # pragma: no cover
        return ""
    try:
        import ctypes

        length = _user32().GetWindowTextLengthW(int(hwnd))
        buffer = ctypes.create_unicode_buffer(length + 2)
        _user32().GetWindowTextW(int(hwnd), buffer, length + 2)
        return buffer.value
    except Exception as exc:  # pragma: no cover
        log.debug("GetWindowTextW не сработал: %s", exc)
        return ""


def process_exe(pid: int | None) -> str:
    """Имя exe процесса по PID (например, chrome.exe)."""
    if not _IS_WINDOWS or not pid:  # pragma: no cover
        return ""
    handle = None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        size = ctypes.c_ulong(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return ""
        return buffer.value.rsplit("\\", 1)[-1]
    except Exception as exc:  # pragma: no cover
        log.debug("QueryFullProcessImageNameW не сработал: %s", exc)
        return ""
    finally:
        if handle:  # pragma: no cover
            try:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass


def foreground_info() -> dict | None:
    """Всё про активное окно: hwnd, pid, заголовок, exe."""
    hwnd = get_foreground_window()
    if not hwnd:
        return None
    pid = window_pid(hwnd)
    return {
        "hwnd": hwnd,
        "pid": pid,
        "title": window_title(hwnd),
        "exe": process_exe(pid),
    }
