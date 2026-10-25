#!/usr/bin/env python3
"""«Голос» — офлайн-диктовка в любое поле ввода.

Запуск:
    python golos_app.py            # иконка в трее
    python golos_app.py --settings # сразу открыть настройки
    python golos_app.py --silent   # без консоли (используется автозапуском)

Горячая клавиша по умолчанию: Ctrl+Alt+Space (нажали — говорите, нажали — текст
вставляется туда, где стоит курсор).
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
from pathlib import Path

from golos import APP_NAME, APP_TITLE, __version__
from golos.app import DictationApp, NullUi
from golos.config import AppPaths, load_config
from golos.logsetup import setup_logging
from golos.hotkeys import pretty_hotkey
from golos.system import SingleInstance, open_path, show_message

log = logging.getLogger("golos")


class GuiBridge:
    """Передаёт события ядра в интерфейс, не нарушая потоки tkinter.

    События складываются в очередь, а tkinter разбирает её в своём потоке
    (``root.after``). Это безопасно: tkinter нельзя трогать из других потоков.
    """

    def __init__(self, root=None):
        self.root = root
        self.queue: "queue.Queue[tuple[str, tuple]]" = queue.Queue()
        self.tray = None
        self.window = None

    # ------------------------------------------------------------------ helpers
    def post(self, func, *args) -> None:
        if self.root is None:
            func(*args)
            return
        try:
            self.root.after(0, lambda: func(*args))
        except Exception:  # pragma: no cover - окно уже закрыто
            pass

    def pump(self) -> None:
        """Разобрать очередь событий ядра (вызывается из цикла tkinter)."""
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(kind, payload)
            except Exception:  # pragma: no cover
                log.exception("Ошибка обработки события %s", kind)

    def _dispatch(self, kind: str, payload: tuple) -> None:
        if kind == "state":
            state, message = payload
            if self.tray:
                self.tray.update_state(state, message)
            if self.window:
                self.window.update_state(state, message)
        elif kind == "level":
            if self.tray:
                self.tray.update_level(payload[0])
            if self.window and self.window.is_visible():
                self.window.update_level(payload[0])
        elif kind == "text":
            text, _raw = payload
            if self.window:
                self.window.set_last_text(text)
        elif kind == "error":
            message = payload[0]
            if self.tray:
                self.tray.notify("Голос — ошибка", message[:220])
        elif kind == "notify":
            title, message = payload
            if self.tray:
                self.tray.notify(title, message)

    # ------------------------------------------------------------- UiBridge API
    def on_state(self, state: str, message: str = "") -> None:
        self.queue.put(("state", (state, message)))

    def on_level(self, level: float) -> None:
        self.queue.put(("level", (level,)))

    def on_text(self, text: str, raw: str = "") -> None:
        self.queue.put(("text", (text, raw)))

    def on_error(self, message: str) -> None:
        self.queue.put(("error", (message,)))

    def on_notify(self, title: str, message: str) -> None:
        self.queue.put(("notify", (title, message)))


class ConsoleBridge(NullUi):
    """Режим без трея: текст выводится в консоль, ошибки — в stderr."""

    def on_state(self, state: str, message: str = "") -> None:
        log.debug("Состояние: %s %s", state, message)

    def on_text(self, text: str, raw: str = "") -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_error(self, message: str) -> None:
        sys.stderr.write(f"\n[ошибка] {message}\n")
        sys.stderr.flush()

    def on_notify(self, title: str, message: str) -> None:
        log.info("%s: %s", title, message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="golos",
        description="Офлайн-диктовка: говорите — текст появляется в любом поле ввода.",
    )
    parser.add_argument("--show", "--settings", dest="show", action="store_true",
                        help="сразу открыть окно программы")
    parser.add_argument("--silent", action="store_true",
                        help="запуск в фоне: без консоли и без окна (для автозапуска)")
    parser.add_argument("--no-tray", action="store_true", help="не создавать иконку в трее")
    parser.add_argument("--no-gui", action="store_true", help="полностью без интерфейса")
    parser.add_argument("--no-preload", action="store_true", help="не загружать модель при старте")
    parser.add_argument("--config", type=str, default="", help="путь к своему config.json")
    parser.add_argument("--debug", action="store_true", help="подробный журнал")
    parser.add_argument("--status", action="store_true",
                        help="только проверить, запущен ли «Голос» (для скриптов)")
    parser.add_argument("--version", action="version", version=f"{APP_TITLE} {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.status:
        # Используется run.bat: 0 — уже работает, 1 — не запущен.
        from golos.system import another_instance_running

        running = another_instance_running()
        print("running" if running else "not running")
        return 0 if running else 1

    paths = AppPaths.default().ensure()

    config_path = Path(args.config) if args.config else paths.config_file
    config, broken = load_config(config_path)
    if args.debug:
        config.log_level = "DEBUG"
    setup_logging(paths.log_file, config.log_level, console=not args.silent)
    log.info(
        "Запуск %s %s (python %s, %s)",
        APP_NAME,
        __version__,
        sys.version.split()[0],
        sys.platform,
    )
    if broken:
        log.warning("Повреждённый конфиг сохранён как %s", broken)

    single = SingleInstance()
    if not single.acquire():
        message = (
            "«Голос» уже запущен — второй раз его запускать не нужно.\n\n"
            f"Клавиша записи: {pretty_hotkey(config.hotkey)}\n\n"
            "Иконка микрофона — в трее рядом с часами. Если её не видно, "
            "нажмите стрелку «^» слева от часов и перетащите иконку на панель."
        )
        log.warning("Повторный запуск: приложение уже работает")
        if not args.silent:
            show_message(APP_TITLE, message)
        return 0

    app = DictationApp(
        config=config,
        paths=paths,
        preload=config.preload_model and not args.no_preload,
    )

    try:
        if args.no_gui:
            return _run_console(app, single)

        try:
            return _run_gui(app, args, single)
        except Exception as exc:
            log.exception("Графический интерфейс не запустился")
            print(f"Не удалось запустить интерфейс ({exc}). Работаю без трея.")
            return _run_console(app, single)
    finally:
        single.release()


# ------------------------------------------------------------------------ modes
def _run_console(app: DictationApp, single: SingleInstance) -> int:
    app.ui = ConsoleBridge()
    app.start()
    print(
        f"{APP_TITLE}: {app.config.hotkey} — начать/остановить запись. "
        "Распознанный текст печатается ниже и вставляется в активное окно. "
        "Ctrl+C — выход."
    )
    try:
        while True:
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        print("\nВыход…")
    finally:
        app.stop()
    return 0


def _run_gui(app: DictationApp, args: argparse.Namespace, single: SingleInstance) -> int:
    import tkinter as tk

    from golos.tray import create_tray
    from golos.ui import MainWindow

    root = tk.Tk()
    root.withdraw()

    bridge = GuiBridge(root)
    app.ui = bridge

    window = MainWindow(root, app)
    bridge.window = window

    def quit_app() -> None:
        log.info("Завершение по команде пользователя")
        try:
            app.stop()
        finally:
            try:
                tray.stop()
            except Exception:  # pragma: no cover
                pass
            try:
                root.quit()
                root.destroy()
            except Exception:  # pragma: no cover
                pass
            single.release()

    tray = create_tray(
        app,
        on_open=lambda: bridge.post(window.show),
        on_quit=quit_app,
        on_history=lambda: bridge.post(lambda: open_path(app.paths.history_file)),
        on_show_log=lambda: bridge.post(lambda: open_path(app.paths.log_file)),
        on_open_config=lambda: bridge.post(lambda: open_path(app.paths.data_dir)),
    )
    bridge.tray = tray

    def poll_events() -> None:
        bridge.pump()
        root.after(70, poll_events)

    root.after(70, poll_events)

    app.start()
    tray.start()

    first_run = not app.transcriber.is_model_downloaded()
    # При обычном запуске окно показываем всегда: иначе непонятно, случилось ли
    # что-то вообще (иконка в Windows 11 часто прячется в «^»).
    if not args.silent:
        root.after(300, window.show)
    if first_run:
        log.info("Модель ещё не скачана — она загрузится при первом распознавании")

    log.info("Интерфейс запущен")
    try:
        root.mainloop()
    except KeyboardInterrupt:  # pragma: no cover
        quit_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
