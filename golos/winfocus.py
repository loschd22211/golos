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
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
GA_ROOT = 2
SW_RESTORE = 9

_IS_WINDOWS = sys.platform == "win32"


def _user32():
    import ctypes

    return ctypes.windll.user32


def _kernel32():
    import ctypes

    return ctypes.windll.kernel32


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
            # Стиль нужен и самому окну, и корневому: у Tk с overrideredirect
            # настоящий HWND — родитель, иначе клик забирает фокус.
            for target in {hwnd, root_window(hwnd) or hwnd}:
                user32.SetWindowLongW(
                    target, GWL_EXSTYLE,
                    (user32.GetWindowLongW(target, GWL_EXSTYLE) | flags) & ~remove,
                )
                # Без SetWindowPos Windows не всегда применяет новый стиль.
                user32.SetWindowPos(
                    target, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                    | SWP_NOACTIVATE,
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


def root_window(hwnd: int | None) -> int | None:
    """Корневое окно (верхний уровень) для данного HWND."""
    if not _IS_WINDOWS or not hwnd:  # pragma: no cover
        return None
    try:
        root = _user32().GetAncestor(int(hwnd), GA_ROOT)
        return int(root) if root else int(hwnd)
    except Exception as exc:  # pragma: no cover
        log.debug("GetAncestor не сработал: %s", exc)
        return None


def is_window(hwnd: int | None) -> bool:
    """Существует ли ещё такое окно (пользователь мог его закрыть)."""
    if not _IS_WINDOWS or not hwnd:  # pragma: no cover
        return False
    try:
        return bool(_user32().IsWindow(int(hwnd)))
    except Exception:  # pragma: no cover
        return False


def is_our_window(hwnd: int | None) -> bool:
    """Окно принадлежит нам (оверлей, настройки, диалоги)?"""
    pid = window_pid(hwnd)
    if pid is None:  # pragma: no cover
        return False
    import os

    return int(pid) == os.getpid()


def force_foreground(hwnd: int | None) -> bool:
    """Сделать окно активным, даже если фокус сейчас у нас в оверлее.

    SetForegroundWindow сам по себе часто игнорируется, поэтому используем
    общепринятый приём: AttachThreadInput к потоку активного окна и к потоку
    нужного, BringWindowToTop + SetForegroundWindow + SetFocus, затем отцепка.
    """
    if not _IS_WINDOWS or not hwnd:  # pragma: no cover
        return False
    target = root_window(hwnd) or int(hwnd)
    user32 = _user32()
    try:
        kernel32 = _kernel32()
        current = int(kernel32.GetCurrentThreadId())
        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground == target:
            return True

        target_thread = int(user32.GetWindowThreadProcessId(target, None) or 0)
        foreground_thread = (
            int(user32.GetWindowThreadProcessId(foreground, None) or 0)
            if foreground
            else 0
        )

        attached = []
        for thread in (foreground_thread, target_thread):
            if thread and thread != current:
                if user32.AttachThreadInput(current, thread, True):
                    attached.append(thread)
        try:
            user32.BringWindowToTop(target)
            user32.ShowWindow(target, SW_RESTORE)
            user32.SetForegroundWindow(target)
            user32.SetFocus(target)
        finally:
            for thread in attached:
                user32.AttachThreadInput(current, thread, False)

        if int(user32.GetForegroundWindow() or 0) == target:
            return True

        # Запасной путь, которым пользуются многие диктовки: недокументированный,
        # но работает там, где SetForegroundWindow молча отказывает.
        try:
            user32.SwitchToThisWindow(target, True)
        except Exception:  # pragma: no cover
            pass
        return int(user32.GetForegroundWindow() or 0) == target
    except Exception as exc:  # pragma: no cover
        log.debug("Не удалось вернуть фокус окну %s: %s", hwnd, exc)
        return False


def activate_window(hwnd: int | None, attempts: int = 3, pause: float = 0.04) -> bool:
    """Несколько попыток вернуть фокус: Windows иногда «проглатывает» первую."""
    import time

    if not hwnd:
        return False
    if not is_window(hwnd):
        return False
    for attempt in range(max(1, attempts)):
        if force_foreground(hwnd):
            return True
        time.sleep(pause * (attempt + 1))
    return False


class TargetTracker:
    """Помнит последнее чужое активное окно — туда и печатаем.

    Без этого оверлей мог сам оказаться «активным окном» (клик по нему, меню
    трея, диалог), и текст уходил в наше же окно: снаружи это выглядит как
    «ничего не вставляется».
    """

    def __init__(self, interval: float = 0.15):
        self.interval = interval
        self._hwnd = 0
        self._pinned = 0      # окно, закреплённое на время диктовки
        self._thread = None
        self._stop = False

    # ------------------------------------------------------------------ трекер
    def start(self) -> None:
        import threading

        if self._thread is not None:
            return
        self.remember_current()
        self._stop = False
        self._thread = threading.Thread(
            target=self._run, name="golos-target", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        thread, self._thread = self._thread, None
        if thread is not None:
            try:
                thread.join(timeout=1.0)
            except Exception:  # pragma: no cover
                pass

    def _run(self) -> None:
        import time

        while not self._stop:
            try:
                self.remember_current()
            except Exception:  # pragma: no cover
                pass
            time.sleep(self.interval)

    # ------------------------------------------------------------------- цель
    def remember_current(self) -> bool:
        return self.remember(get_foreground_window())

    def remember(self, hwnd: int | None) -> bool:
        """Запомнить окно, если оно чужое и живое.

        Пока цель закреплена (идёт диктовка), новые окна не запоминаются:
        иначе переключение на другую вкладку/окно во время речи уводило
        текст не туда.
        """
        if self._pinned:
            return False
        if not hwnd:
            return False
        if not is_window(hwnd):
            return False
        if is_our_window(hwnd):
            return False
        self._hwnd = int(hwnd)
        return True

    @property
    def hwnd(self) -> int:
        return self._pinned or self._hwnd

    @property
    def pinned(self) -> int:
        """Окно, закреплённое под текущую диктовку (0 — не закреплено)."""
        return self._pinned

    # ------------------------------------------------------------------- закрепление
    def pin(self) -> int:
        """Закрепить текущее чужое окно: дальше пишем только в него."""
        self._pinned = 0
        self.remember_current()
        self._pinned = self._hwnd
        if self._pinned:
            log.info("Цель вставки закреплена: hwnd=%s «%s»",
                     self._pinned, window_title(self._pinned))
        else:
            log.warning("Цель вставки не определена: активное окно наше или пустое")
        return self._pinned

    def unpin(self) -> None:
        """Снять закрепление (диктовка закончилась)."""
        if self._pinned:
            log.debug("Закрепление цели снято (hwnd=%s)", self._pinned)
        self._pinned = 0

    def activate(self) -> bool:
        """Сделать запомненное (или закреплённое) окно активным перед вставкой."""
        if self._pinned and is_window(self._pinned):
            # Закреплённая цель: возвращаем фокус именно ей, даже если человек
            # успел переключиться на другое окно.
            ok = activate_window(self._pinned)
            if ok:
                return True
            log.warning("Закреплённое окно %s не принимает фокус", self._pinned)
        if not self._hwnd:
            self.remember_current()
        if not self._hwnd:
            return False
        if is_our_window(get_foreground_window()):
            # Фокус у нас — обязательно уводим его в нужное окно.
            ok = activate_window(self._hwnd)
            if not ok:
                log.warning("Не получилось вернуть фокус окну %s", self._hwnd)
            return ok
        return force_foreground(self._hwnd)
