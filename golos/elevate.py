"""Права администратора: без них Windows не даёт печатать в чужие окна.

Windows (UIPI) выбрасывает искусственные нажатия клавиш, если программа
работает с обычными правами, а окно-получатель запущено от администратора.
Самая частая жертва — браузер, запущенный «от имени администратора»: текст
распознаётся, плашка пишет «вставлено», а в поле ничего нет.

Поэтому «Голос» по умолчанию перезапускает себя с правами администратора,
а если не получилось — честно говорит об этом вместо ложного успеха.

Модуль целиком на ctypes, вне Windows все функции безопасны и возвращают
«не знаю»/False.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from pathlib import Path

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
ERROR_CANCELLED = 1223

# Аргумент-метка: по нему видно, что запуск с правами уже пробовали, и второй
# раз спрашивать UAC не нужно (иначе можно попасть в цикл перезапусков).
ATTEMPT_FLAG = "--elevated-attempt"

ADMIN_HINT = (
    "Перезапустите «Голос» от администратора: правый клик по иконке микрофона "
    "в трее → «Перезапустить от администратора»"
)


def _shell32():
    return ctypes.windll.shell32


def _kernel32():
    return ctypes.windll.kernel32


def _advapi32():
    return ctypes.windll.advapi32


def _last_error() -> int:
    """Код последней ошибки WinAPI (на не-Windows — 0)."""
    getter = getattr(ctypes, "GetLastError", None)
    if getter is None:  # pragma: no cover - Linux/macOS
        return 0
    try:
        return int(getter())
    except Exception:  # pragma: no cover
        return 0


def is_admin() -> bool:
    """Запущены ли мы с правами администратора."""
    if not _IS_WINDOWS:  # pragma: no cover - не Windows
        return False
    try:
        return bool(_shell32().IsUserAnAdmin())
    except Exception as exc:  # pragma: no cover
        log.debug("IsUserAnAdmin недоступен: %s", exc)
        return False


def pid_is_elevated(pid) -> bool | None:
    """Окно этого процесса примет наш ввод? True/False, None — не выяснили."""
    if not _IS_WINDOWS or not pid:  # pragma: no cover - не Windows
        return None
    handle = None
    kernel32 = _kernel32()
    try:
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            # Ключевой признак UIPI: чужой процесс от администратора даже не
            # открывается на чтение.
            denied = int(_last_error()) == ERROR_ACCESS_DENIED
            return True if denied else None

        token = ctypes.c_void_p()
        if not _advapi32().OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevation = ctypes.c_ulong(0)
            size = ctypes.c_ulong(0)
            ok = _advapi32().GetTokenInformation(
                token,
                TOKEN_ELEVATION,
                ctypes.byref(elevation),
                ctypes.sizeof(elevation),
                ctypes.byref(size),
            )
            if not ok:
                return None
            return bool(elevation.value)
        finally:
            kernel32.CloseHandle(token)
    except Exception as exc:  # pragma: no cover
        log.debug("Не удалось проверить права процесса %s: %s", pid, exc)
        return None
    finally:
        if handle:  # pragma: no cover
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass


def foreground_is_elevated() -> bool | None:  # pragma: no cover - Windows
    """Запущено ли от администратора окно, в которое сейчас пойдёт текст."""
    if not _IS_WINDOWS:
        return None
    from . import winfocus

    info = winfocus.foreground_info()
    if not info:
        return None
    return pid_is_elevated(info.get("pid"))


def describe_window(hwnd=None) -> str:
    """Строка для журнала: что за окно и с какими правами."""
    from . import winfocus

    hwnd = hwnd or winfocus.get_foreground_window()
    if not hwnd:  # pragma: no cover - Windows
        return "активное окно не определено"
    pid = winfocus.window_pid(hwnd)
    title = (winfocus.window_title(hwnd) or "")[:60]
    exe = winfocus.process_exe(pid) or "?"
    elevated = pid_is_elevated(pid)
    mark = {True: ", от администратора", False: "", None: ", права неизвестны"}[elevated]
    return f"«{title}» ({exe}{mark})"


def describe_foreground() -> str:  # pragma: no cover - Windows
    return describe_window()


def window_is_elevated(hwnd) -> bool | None:
    """Права конкретного окна (не того, что случайно активно сейчас)."""
    from . import winfocus

    return pid_is_elevated(winfocus.window_pid(hwnd))


def insertion_guard(hwnd=None) -> None:
    """Вызывается перед каждой вставкой.

    ``hwnd`` — окно, в которое реально пойдёт текст (его помнит трекер цели).
    Без него берём текущее активное: активным мог оказаться наш же оверлей,
    и тогда проверка прав была бы бессмысленной.

    Пишет в журнал, куда именно уйдёт текст, и не даёт соврать «вставлено»,
    если Windows всё равно заблокирует ввод.
    """
    if not _IS_WINDOWS:  # pragma: no cover - не Windows
        return

    if not hwnd:
        # Трекер не знает цели. Активное окно годится, только если это не
        # наша плашка: иначе текст уйдёт в собственный оверлей и потеряется.
        from . import winfocus

        current = winfocus.get_foreground_window()
        if current and not winfocus.is_our_window(current):
            hwnd = current
        else:
            log.warning(
                "Окно-получатель неизвестно: активное окно — наше («Голос»). "
                "Щёлкните в поле ввода и повторите диктовку."
            )

    elevated = window_is_elevated(hwnd) if hwnd else foreground_is_elevated()
    log.info("Вставка в окно: %s", describe_window(hwnd) if hwnd else "окно не выбрано")

    if elevated and not is_admin():
        from .inject import InjectionError

        raise InjectionError(
            "Текст не вставлен: активное окно запущено от имени администратора, "
            "и Windows блокирует ввод из программы без прав.\n" + ADMIN_HINT
        )
    if elevated is None and not is_admin():
        # Не смогли определить права — не мешаем, но в журнале будет запись.
        log.debug("Права окна-получателя определить не удалось")


def relaunch_command(args=None) -> tuple[str, str]:
    """Команда запуска себя заново (exe + строка параметров)."""
    passed = list(sys.argv[1:] if args is None else args)
    if ATTEMPT_FLAG not in passed:
        passed.append(ATTEMPT_FLAG)

    if getattr(sys, "frozen", False):  # собранный .exe
        return str(Path(sys.executable).resolve()), _join(passed)

    script = Path(sys.argv[0]).resolve()
    exe = Path(sys.executable)
    # pythonw хорош для окна без консоли, но ошибки запуска тогда не видно —
    # берём то же, чем нас запустили.
    if exe.name.lower() == "python.exe":
        windowed = exe.with_name("pythonw.exe")
        exe = windowed if windowed.exists() else exe
    return str(exe), _join([str(script), *passed])


def _join(parts) -> str:
    import subprocess

    return subprocess.list2cmdline([str(part) for part in parts])


def relaunch_as_admin(args=None) -> bool:
    """Запустить себя заново с правами администратора (Windows покажет UAC).

    True — новый процесс стартовал, и текущему пора выходить.
    """
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    exe, params = relaunch_command(args)
    try:
        shell = _shell32()
        shell.ShellExecuteW.restype = ctypes.c_void_p
        shell.ShellExecuteW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int,
        ]
        result = shell.ShellExecuteW(
            None, "runas", exe, params, str(Path.cwd()), 1  # SW_SHOWNORMAL
        )
        code = int(result or 0)
    except Exception as exc:  # pragma: no cover
        log.warning("Не удалось запросить права администратора: %s", exc)
        return False

    # ShellExecuteW возвращает HINSTANCE: <= 32 — код ошибки.
    # 1223 (ERROR_CANCELLED) — пользователь нажал «Нет» в UAC,
    # 5 (ERROR_ACCESS_DENIED) — Windows не дала поднять права.
    if code > 32 and code not in (ERROR_ACCESS_DENIED, ERROR_CANCELLED):
        log.info("Перезапуск с правами администратора: %s %s", exe, params)
        return True
    log.warning("Запуск с правами администратора отклонён (код %s)", code)
    return False


def already_attempted(args) -> bool:
    """Пробовали ли уже поднять права в этом запуске."""
    return ATTEMPT_FLAG in list(args or [])
