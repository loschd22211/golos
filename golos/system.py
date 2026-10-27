"""Системные мелочи Windows: один экземпляр, автозапуск, звук, уведомления."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "Golos"


# ------------------------------------------------------------------- singleton
class SingleInstance:
    """Не даёт запустить два «Голоса» одновременно (иначе клавиши дублируются)."""

    def __init__(self, name: str = "golos-single-instance"):
        self.path = Path(tempfile.gettempdir()) / f"{name}.lock"
        self._handle = None

    def acquire(self) -> bool:
        try:
            self._handle = open(self.path, "a+")
        except OSError as exc:  # pragma: no cover
            log.warning("Не удалось открыть lock-файл: %s", exc)
            return True

        try:
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - для разработки на Linux/macOS
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(str(os.getpid()))
            self._handle.flush()
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:  # pragma: no cover
            pass
        try:
            self._handle.close()
        except Exception:  # pragma: no cover
            pass
        self._handle = None


def another_instance_running(name: str = "golos-single-instance") -> bool:
    """Проверить, работает ли «Голос» прямо сейчас.

    Пробуем захватить тот же lock-файл, что и основной экземпляр: если не
    получилось — значит, кто-то его уже держит.
    """
    probe = SingleInstance(name)
    if probe.acquire():
        probe.release()
        return False
    return True


# -------------------------------------------------------------------- autostart
def _launch_command() -> str:
    """Команда автозапуска: pythonw.exe + путь к golos_app.py."""
    script = Path(sys.argv[0]).resolve()
    if script.suffix.lower() in (".pyw", ".py") and script.exists():
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        runner = pythonw if pythonw.exists() else exe
        return f'"{runner}" "{script}" --silent'

    # Запущено как собранный .exe (PyInstaller и т.п.)
    return f'"{Path(sys.executable).resolve()}" --silent'


def autostart_supported() -> bool:
    return sys.platform == "win32"


def set_autostart(enabled: bool) -> bool:
    """Включить/выключить автозапуск при входе в Windows."""
    if not autostart_supported():
        log.info("Автозапуск поддерживается только в Windows")
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, AUTOSTART_NAME, 0, winreg.REG_SZ, _launch_command()
                )
                log.info("Автозапуск включён: %s", _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
                log.info("Автозапуск выключен")
        return True
    except OSError as exc:
        log.error("Не удалось настроить автозапуск: %s", exc)
        return False


def is_autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except OSError:
        return False


# --------------------------------------------------------------------- ярлыки
SHORTCUT_NAME = "Голос.lnk"


def _launch_target() -> tuple[str, str, str]:
    """(программа, аргументы, папка) для ярлыка — без консоли."""
    script = Path(sys.argv[0]).resolve()
    if getattr(sys, "frozen", False):  # собранный .exe
        return str(Path(sys.executable).resolve()), "", str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return str(runner), f'"{script}"', str(script.parent)


def shortcut_dirs() -> list[Path]:
    """Куда класть ярлык: рабочий стол и меню «Пуск»."""
    if not autostart_supported():
        return []
    dirs: list[Path] = []
    for env, suffix in (
        ("USERPROFILE", "Desktop"),
        ("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
    ):
        base = os.environ.get(env)
        if not base:
            continue
        path = Path(base) / suffix
        if path.is_dir():
            dirs.append(path)
    return dirs


def shortcut_exists() -> bool:
    return any((folder / SHORTCUT_NAME).exists() for folder in shortcut_dirs())


def create_shortcuts() -> list[Path]:
    """Создать ярлык «Голос» на рабочем столе и в «Пуске».

    Нужно для запуска двойным щелчком: без ярлыка человек каждый раз лезет в
    консоль с ``run.bat``, а это не то, что ждёшь от обычной программы.
    Выполняется через WScript.Shell — тот же способ, что в install.ps1.
    """
    if not autostart_supported():
        return []
    exe, arguments, workdir = _launch_target()
    icon = Path(workdir) / "assets" / "golos.ico"
    created: list[Path] = []
    for folder in shortcut_dirs():
        link = folder / SHORTCUT_NAME
        script = (
            "$ErrorActionPreference='Stop';"
            "$s=New-Object -ComObject WScript.Shell;"
            f"$l=$s.CreateShortcut('{link}');"
            f"$l.TargetPath='{exe}';"
            f"$l.Arguments='{arguments}';"
            f"$l.WorkingDirectory='{workdir}';"
            + (f"$l.IconLocation='{icon},0';" if icon.exists() else "")
            + "$l.Description='Голос — диктовка в любое поле ввода';"
            "$l.Save()"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0 and link.exists():
                created.append(link)
                log.info("Ярлык создан: %s", link)
            else:
                log.warning(
                    "Не удалось создать ярлык %s: %s",
                    link,
                    (result.stderr or result.stdout or "").strip()[:200],
                )
        except Exception as exc:  # pragma: no cover - Windows/PowerShell
            log.warning("Не удалось создать ярлык %s: %s", link, exc)
    return created


def show_message(title: str, message: str) -> None:
    """Показать сообщение даже без консоли (pythonw её не имеет).

    Это единственный способ объяснить пользователю, что происходит, если
    второй экземпляр уже работает: иначе повторный запуск выглядит как
    «щёлкнул — и ничего».
    """
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
            return
    except Exception as exc:  # pragma: no cover
        log.debug("Окно сообщения недоступно: %s", exc)
    print(f"{title}: {message}")


def process_is_running(marker: str = "golos_app.py") -> bool:
    """Запущен ли уже «Голос» (по списку процессов Windows)."""
    if sys.platform != "win32":  # pragma: no cover
        return False
    try:
        import subprocess

        output = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" "
                "| Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return marker in (output or "")
    except Exception as exc:  # pragma: no cover
        log.debug("Не удалось проверить процессы: %s", exc)
        return False


# ------------------------------------------------------------------ звук/трей
def beep(kind: str = "start") -> None:
    """Короткий звуковой сигнал (не критично, если не сработает)."""
    try:
        if sys.platform == "win32":
            import winsound

            if kind == "start":
                winsound.Beep(880, 90)
            elif kind == "stop":
                winsound.Beep(660, 90)
            elif kind == "error":
                winsound.Beep(320, 260)
            else:
                winsound.Beep(520, 70)
            return
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:  # pragma: no cover
        pass


def open_path(path: Path) -> None:
    """Открыть файл или папку системным способом."""
    path = Path(path)
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":  # pragma: no cover
            subprocess.Popen(["open", str(path)])
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        log.warning("Не удалось открыть %s: %s", path, exc)

