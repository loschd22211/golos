"""Иконка в системном трее: статус, меню, уведомления.

Если pystray/Pillow не установлены (или нет графической среды), используется
«пустой» трей: приложение продолжает работать и писать в журнал.
"""

from __future__ import annotations

import logging
import threading

from .hotkeys import pretty_hotkey
from .app import ERROR, IDLE, LOADING, PAUSED, RECORDING, STATE_LABELS, DictationApp

log = logging.getLogger(__name__)

COLORS = {
    IDLE: (86, 156, 214),
    "processing": (240, 173, 78),
    LOADING: (140, 140, 140),
    RECORDING: (226, 74, 74),
    ERROR: (200, 60, 60),
    PAUSED: (120, 120, 120),
}


def make_icon_image(state: str = IDLE, level: float = 0.0):
    """Нарисовать иконку: микрофон на цветном круге + индикатор громкости."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    base = COLORS.get(state, COLORS[IDLE])

    # круг-статус
    draw.ellipse((2, 2, size - 3, size - 3), fill=base + (255,))

    # индикатор громкости (кольцо вокруг микрофона при записи)
    if state == RECORDING:
        radius = int(16 + min(1.0, max(0.0, level)) * 13)
        draw.ellipse(
            (32 - radius, 32 - radius, 32 + radius, 32 + radius),
            outline=(255, 255, 255, 190),
            width=2,
        )

    white = (255, 255, 255, 255)

    if state == PAUSED:
        # пауза — две вертикальные полосы
        draw.rounded_rectangle((22, 20, 28, 44), radius=3, fill=white)
        draw.rounded_rectangle((36, 20, 42, 44), radius=3, fill=white)
        return img

    # капсула микрофона
    draw.rounded_rectangle((25, 13, 39, 36), radius=7, fill=white)
    # дужка
    draw.arc((19, 20, 45, 42), start=0, end=180, fill=white, width=3)
    # стойка
    draw.line((32, 42, 32, 48), fill=white, width=3)
    draw.line((25, 49, 39, 49), fill=white, width=3)

    if state == LOADING:
        draw.line((20, 32, 44, 32), fill=base + (255,), width=3)
    if state == ERROR:
        draw.line((24, 24, 40, 40), fill=(255, 220, 220, 255), width=3)
        draw.line((40, 24, 24, 40), fill=(255, 220, 220, 255), width=3)

    return img


class NullTray:
    """Трей-заглушка (без pystray/Pillow или без графической среды)."""

    available = False

    def __init__(self, app: DictationApp | None = None, **kwargs):
        self.app = app
        self.state = IDLE

    def start(self) -> None:
        log.info("Трей недоступен — работаю без иконки")

    def stop(self) -> None:
        pass

    def update_state(self, state: str, message: str = "") -> None:
        self.state = state

    def update_level(self, level: float) -> None:
        pass

    def notify(self, title: str, message: str) -> None:
        log.info("%s: %s", title, message)


class TrayIcon(NullTray):
    """Иконка в трее с меню и подсказкой статуса."""

    available = True

    def __init__(
        self,
        app: DictationApp,
        on_open=None,
        on_settings=None,
        on_quit=None,
        on_history=None,
        on_show_log=None,
        on_open_config=None,
    ):
        import pystray

        self._pystray = pystray
        self.app = app
        self.on_open = on_open or on_settings or (lambda: None)
        self.on_settings = self.on_open
        self.on_quit = on_quit or (lambda: None)
        self.on_history = on_history or (lambda: None)
        self.on_show_log = on_show_log or (lambda: None)
        self.on_open_config = on_open_config or (lambda: None)

        self.state = IDLE
        self._level = 0.0
        self._message = STATE_LABELS[IDLE]
        self._lock = threading.Lock()
        self._last_icon_level = -1.0

        self._icon = pystray.Icon(
            "golos",
            make_icon_image(IDLE),
            self._tooltip(),
            menu=self._build_menu(),
        )

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        pystray = self._pystray
        Menu, MenuItem = pystray.Menu, pystray.MenuItem

        return Menu(
            MenuItem("Открыть «Голос»", self._menu_open, default=True),
            MenuItem(
                lambda item: "Остановить запись" if self.app.is_recording else "Начать запись",
                self._menu_toggle,
            ),
            MenuItem(self._pause_label, self._menu_pause),
            Menu.SEPARATOR,
            MenuItem("Открыть историю", self._menu_history),
            MenuItem("Открыть журнал", self._menu_log),
            MenuItem("Папка настроек", self._menu_folder),
            Menu.SEPARATOR,
            MenuItem("Выход", self._menu_quit),
        )

    def _pause_label(self, item) -> str:
        return "Продолжить работу" if self.app.paused else "Пауза"

    def _menu_open(self, icon, item) -> None:
        self.on_open()

    def _menu_toggle(self, icon, item) -> None:
        self.app.toggle()

    def _menu_pause(self, icon, item) -> None:
        self.app.pause()
        self.update_menu()


    def _menu_history(self, icon, item) -> None:
        self.on_history()

    def _menu_log(self, icon, item) -> None:
        self.on_show_log()

    def _menu_folder(self, icon, item) -> None:
        self.on_open_config()

    def _menu_quit(self, icon, item) -> None:
        self.on_quit()
        try:
            icon.stop()
        except Exception:  # pragma: no cover
            pass

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        thread = threading.Thread(target=self._run, name="golos-tray", daemon=True)
        thread.start()

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception as exc:  # pragma: no cover
            log.warning("Трей не запустился: %s", exc)

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------ update
    def _tooltip(self) -> str:
        cfg = self.app.config
        label = STATE_LABELS.get(self.state, self.state)
        hint = f"Голос — {label}\n{pretty_hotkey(cfg.hotkey)}: начать/остановить запись"
        if self._message and self._message not in (label,):
            hint += f"\n{self._message}"
        return hint[:120]

    def update_state(self, state: str, message: str = "") -> None:
        self.state = state
        self._message = message or STATE_LABELS.get(state, state)
        if state != RECORDING:
            self._level = 0.0
        self._refresh(force=True)
        self.update_menu()

    def update_menu(self) -> None:
        try:
            self._icon.update_menu()
        except Exception:  # pragma: no cover
            pass

    def update_level(self, level: float) -> None:
        """Обновлять иконку не чаще, чем заметно меняется громкость."""
        if self.state != RECORDING:
            return
        self._level = max(0.0, min(1.0, float(level)))
        if abs(self._level - self._last_icon_level) >= 0.12:
            self._refresh()

    def _refresh(self, force: bool = False) -> None:
        try:
            with self._lock:
                self._last_icon_level = self._level
                self._icon.icon = make_icon_image(self.state, self._level)
                self._icon.title = self._tooltip()
        except Exception as exc:  # pragma: no cover
            if force:
                log.debug("Не удалось обновить иконку: %s", exc)

    def notify(self, title: str, message: str) -> None:
        try:
            self._icon.notify(message, title)
        except Exception as exc:  # pragma: no cover
            log.debug("Системное уведомление недоступно: %s", exc)
            log.info("%s: %s", title, message)


def create_tray(app: DictationApp, **kwargs):
    """Создать трей, если есть зависимости, иначе — заглушку."""
    try:
        from importlib.util import find_spec

        if find_spec("pystray") is None or find_spec("PIL") is None:
            raise ImportError("не установлены pystray/Pillow")
        return TrayIcon(app, **kwargs)
    except Exception as exc:
        log.info("Трей недоступен (%s) — работаю без иконки", exc)
        return NullTray(app, **kwargs)
