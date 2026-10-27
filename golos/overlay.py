"""Маленький оверлей: кнопка «микрофон» и текст сразу у курсора.

Окно размером с плашку (300×88), без рамки, всегда сверху, можно перетащить
мышью. Клик по кнопке — начать/остановить диктовку; распознанный текст
вставляется в то окно, где стоит курсор, по мере речи.

Окно создаётся с ``WS_EX_NOACTIVATE``: клик по нему не уводит каретку из
вашего документа, поэтому текст попадает куда надо.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_TITLE, __version__
from .app import ERROR, IDLE, LOADING, PAUSED, PROCESSING, RECORDING, DictationApp
from .audio import AudioError, list_input_devices
from .config import Config
from .hotkeys import pretty_hotkey
from .system import open_path
from . import glass, winfocus

log = logging.getLogger(__name__)

WIDTH = 348
HEIGHT = 96
PANEL_RADIUS = 22          # радиус скругления плашки

# Геометрия кнопок — один источник правды: по этим прямоугольникам и рисуем,
# и ловим клики. Раньше крестик рисовался вверху справа, а клик искали внизу
# справа, поэтому кнопки «не реагировали».
MIC_CX, MIC_CY, MIC_R = 46, HEIGHT // 2, 26
BTN = 26          # сторона квадратных кнопок
BTN_TOP = 14
BTN_GAP = 6


def button_rects(recording: bool) -> dict:
    """Прямоугольники кнопок справа: (x0, y0, x1, y1)."""
    right = WIDTH - 8
    rects = {}
    for name in ("close", "settings") + (("stop",) if recording else ()):
        x1 = right
        x0 = right - BTN
        rects[name] = (x0, BTN_TOP, x1, BTN_TOP + BTN)
        right = x0 - BTN_GAP
    return rects


BUTTON_GLYPHS = {"close": "✕", "settings": "⚙", "stop": "■"}
BUTTON_TITLES = {
    "close": "Закрыть «Голос» (Alt+F4 / меню → Выход)",
    "settings": "Настройки",
    "stop": "Остановить запись (микрофон выключится)",
}

BG = "#141922"          # цвет панели (для текста и запасного пути)
SURFACE = "#1c2333"
# Фон окна: этот цвет Windows показывает полностью прозрачным, поэтому видны
# только скруглённая панель и то, что на ней нарисовано. Акрил для этого не
# годится: он закрашивает весь прямоугольник окна (отсюда был «серый квадрат»).
KEY = glass.TRANSPARENT_KEY
BORDER = "#2b3342"
TEXT = "#e9eef8"
MUTED = "#8b96ab"
ACCENT = "#4c8dff"
RED = "#e5484d"
GREEN = "#3ddc84"
AMBER = "#f5a524"
WHITE = "#ffffff"
FONT = "Segoe UI"

STATE_COLORS = {
    IDLE: ACCENT,
    RECORDING: RED,
    PROCESSING: AMBER,
    LOADING: MUTED,
    PAUSED: MUTED,
    ERROR: RED,
}

STATE_HINTS = {
    IDLE: "Нажмите и говорите",
    RECORDING: "Говорите — текст появится у курсора",
    PROCESSING: "Досказываю…",
    LOADING: "Загружаю модель…",
    PAUSED: "Пауза",
    ERROR: "Ошибка",
}

MODEL_LABELS = {
    "auto": "auto — подобрать под компьютер",
    "tiny": "tiny — очень быстро",
    "base": "base — быстро",
    "small": "small — быстро и точно",
    "medium": "medium — точнее (нужна видеокарта)",
    "large-v3": "large-v3 — максимум (нужен GPU)",
}
LANGS = {
    "ru": "Русский (English слова — латиницей)",
    "en": "Только English",
    "ru+en": "Смешанная речь (язык по куску)",
    "auto": "Авто — по каждой фразе",
}
GLASS_LEVELS = {
    "Непрозрачная": 1.0,
    "Стекло 94%": 0.94,
    "Легче 85%": 0.85,
    "Лёгкая 75%": 0.75,
}
METHODS = {"paste": "Буфер + Ctrl+V", "type": "Посимвольно"}


def _font(size: int, bold: bool = False):
    return (FONT, size, "bold") if bold else (FONT, size)


def _round_rect(canvas, x0, y0, x1, y1, radius, **kwargs):
    """Прямоугольник со скруглёнными углами (canvas не умеет их сам)."""
    radius = max(0, min(radius, (x1 - x0) // 2, (y1 - y0) // 2))
    points = [
        x0 + radius, y0, x1 - radius, y0,
        x1, y0, x1, y0 + radius,
        x1, y1 - radius, x1, y1,
        x1 - radius, y1, x0 + radius, y1,
        x0, y1, x0, y1 - radius,
        x0, y0 + radius, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _short(text: str, limit: int = 38) -> str:
    """Обрезать длинный текст, оставив начало и многоточие в конце."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tail(text: str, limit: int = 38) -> str:
    """Оставить конец текста — для «живой» расшифровки, где важно последнее."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


class Overlay:
    """Плашка с кнопкой записи."""

    def __init__(self, root: tk.Tk, app: DictationApp, on_quit=None):
        self.root = root
        self.app = app
        self.on_quit = on_quit or (lambda: None)

        self.state = IDLE
        self.level = 0.0
        self.partial = ""
        self.last = ""
        self.error = ""
        self._hover = ""       # какая кнопка под курсором
        self._pressed = ""     # какая кнопка нажата
        self._pulse = 0
        self._drag = None
        self._panel_photo = None

        self._setup_window()
        self._draw()
        self.set_state(IDLE)

    # ------------------------------------------------------------------ window
    def _setup_window(self) -> None:
        root = self.root
        root.title(APP_TITLE)
        root.overrideredirect(True)
        # Фон холста — цвет-ключ. Если система не умеет прозрачность, ниже
        # подставим обычный тёмный фон, чтобы не было «светлой рамки».
        self._key = KEY
        self._transparent = False
        root.configure(bg=KEY)
        try:
            root.attributes("-topmost", True)
        except tk.TclError:  # pragma: no cover
            pass

        self.canvas = tk.Canvas(
            root, width=WIDTH, height=HEIGHT, bg=KEY, highlightthickness=0, cursor="hand2"
        )
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-3>", self._on_right_click)

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Настройки…", command=self.open_settings)
        self.menu.add_command(label="Открыть журнал", command=self.open_log)
        self.menu.add_command(label="Скрыть плашку", command=self.hide)
        self.menu.add_separator()
        self.menu.add_command(label="Выход", command=self.on_quit)

        self._place_default()
        root.update_idletasks()
        if not winfocus.make_no_activate(root):
            log.debug("WS_EX_NOACTIVATE недоступен — клик может забирать фокус")
        winfocus.keep_on_top(root, True)
        self.apply_look()

    # -------------------------------------------------------------------- вид
    def apply_look(self) -> None:
        """Применить «стекло»: прозрачный фон окна и общую прозрачность."""
        cfg = self.app.config
        try:
            opacity = float(getattr(cfg, "overlay_opacity", 0.94) or 0.94)
        except (TypeError, ValueError):  # pragma: no cover
            opacity = 0.94

        try:
            report = glass.apply_glass(self.root, opacity=opacity, key=KEY)
        except Exception as exc:  # pragma: no cover - экзотическая система
            log.debug("Стекло недоступно: %s", exc)
            report = {"transparent": False, "opacity": False}

        self._transparent = bool(report.get("transparent"))
        if not self._transparent:
            # Система не умеет прозрачный фон — красим окно в цвет панели,
            # чтобы вокруг плашки не было светлой каймы.
            self._key = BG
            for widget in (self.root, self.canvas):
                try:
                    widget.configure(bg=BG)
                except tk.TclError:  # pragma: no cover
                    pass

        log.info(
            "Вид плашки: прозрачный фон=%s, прозрачность=%.2f",
            self._transparent,
            opacity,
        )
        self._build_panel()
        self._draw()

    def _build_panel(self) -> None:
        """Собрать картинку панели (градиент, блик, рамка) один раз."""
        self._panel_photo = None
        image = glass.panel_image(WIDTH, HEIGHT, radius=PANEL_RADIUS)
        if image is None:
            return
        try:
            from PIL import ImageTk

            self._panel_photo = ImageTk.PhotoImage(image, master=self.root)
        except Exception as exc:  # pragma: no cover - нет Pillow/ImageTk
            log.debug("Картинка панели недоступна: %s", exc)
            self._panel_photo = None

    def _place_default(self) -> None:
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(20, screen_w - WIDTH - 40)
            y = max(20, screen_h - HEIGHT - 120)
            self.root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        except tk.TclError:  # pragma: no cover
            self.root.geometry(f"{WIDTH}x{HEIGHT}")

    # ------------------------------------------------------------------ drawing
    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        color = STATE_COLORS.get(self.state, ACCENT)

        if self._panel_photo is not None:
            # Панель «матовое стекло»: градиент, блик и светлая рамка.
            c.create_image(0, 0, anchor="nw", image=self._panel_photo)
        else:  # pragma: no cover - без Pillow: обычная панель
            c.create_rectangle(0, 0, WIDTH, HEIGHT, fill=BG, outline="")
        c.create_rectangle(PANEL_RADIUS, 0, WIDTH - PANEL_RADIUS, 2, fill=color, outline="")

        # кнопка микрофона
        cx, cy, r = 46, HEIGHT // 2, 26
        if self.state == RECORDING:
            c.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill="#3a1d22", outline="")
            pulse = 2 + (self._pulse % 6)
            ring = r + 4 + pulse
            c.create_oval(cx - ring, cy - ring, cx + ring, cy + ring, outline=RED, width=1)
        else:
            c.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill=SURFACE, outline="")
        fill = color if (self.state == RECORDING or self._hover) else ACCENT
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline="")

        # микрофон внутри кнопки
        mw, top, bottom = 6, cy - 11, cy + 1
        c.create_rectangle(cx - mw, top + mw, cx + mw, bottom - mw, fill=WHITE, outline="")
        c.create_oval(cx - mw, top, cx + mw, top + 2 * mw, fill=WHITE, outline="")
        c.create_oval(cx - mw, bottom - 2 * mw, cx + mw, bottom, fill=WHITE, outline="")
        c.create_arc(cx - 10, cy - 7, cx + 10, cy + 8, start=180, extent=180,
                     style="arc", width=2, outline=WHITE)
        c.create_line(cx, cy + 8, cx, cy + 13, fill=WHITE, width=2)
        c.create_line(cx - 6, cy + 14, cx + 6, cy + 14, fill=WHITE, width=2)

        # текст: что происходит и что уже распознано
        title = self.error if self.state == ERROR and self.error else (
            self.partial if self.partial else STATE_HINTS.get(self.state, "")
        )
        title_color = RED if self.state == ERROR else (TEXT if self.partial else MUTED)
        shown = _tail(title, 36) if (self.partial and self.state != ERROR) else _short(title, 36)
        text_right = WIDTH - 116  # не залезаем под кнопки справа
        c.create_text(84, 32, anchor="w", text=shown, fill=title_color, font=_font(10, True))

        hint = f"{pretty_hotkey(self.app.config.hotkey)} · {self.state}" if self.state != ERROR else "нажмите журнал"
        if self.partial and self.state == RECORDING:
            hint = f"слушаю… {self.app.config.model}"
        elif self.last and self.state != ERROR:
            hint = f"вставлено: {_tail(self.last, 30)}"
        c.create_text(84, 54, anchor="w", text=_short(hint, 38), fill=MUTED, font=_font(8))

        # индикатор уровня
        bar_x0, bar_x1, bar_y = 84, text_right, 72
        _round_rect(c, bar_x0, bar_y, bar_x1, bar_y + 4, 2, fill="#2a3346", outline="")
        if self.level > 0.01:
            width = max(3, int((bar_x1 - bar_x0) * min(1.0, self.level)))
            bar_color = GREEN if self.level < 0.6 else (AMBER if self.level < 0.85 else RED)
            _round_rect(c, bar_x0, bar_y, bar_x0 + width, bar_y + 4, 2,
                        fill=bar_color, outline="")

        # кнопки справа: стоп (во время записи), настройки, закрыть.
        # Рисуем ровно в тех же прямоугольниках, по которым ловим клики.
        for name, (x0, y0, x1, y1) in self._buttons().items():
            hovered = self._hover == name
            pressed = self._pressed == name
            fill = "#2a3a52" if hovered or pressed else SURFACE
            if pressed:
                fill = "#364a68"
            if name == "stop":
                fill = "#3a1d22" if not hovered else "#4a2529"
            _round_rect(c, x0, y0, x1, y1, 9, fill=fill, outline="")
            glyph_color = WHITE if hovered or pressed else MUTED
            if name == "stop":
                glyph_color = "#ffb3b6" if not hovered else WHITE
            c.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text=BUTTON_GLYPHS[name],
                fill=glyph_color,
                font=_font(10 if name != "settings" else 11),
            )

        if self.error:
            # Красная «!» у кнопки микрофона: видно, что есть ошибка, и по клику
            # по строке статуса открывается журнал.
            c.create_text(MIC_CX + MIC_R + 2, 14, text="!", fill=RED, font=_font(11, True))

        # во время записи дополнительно подсказываем, что микрофон можно выключить
        if self.state == RECORDING:
            c.create_text(text_right, 54, anchor="e", text="стоп", fill=MUTED, font=_font(7))

    # ------------------------------------------------------------------- events
    def _buttons(self) -> dict:
        """Кнопки, доступные в текущем состоянии."""
        return button_rects(self.state == RECORDING)

    def _hit(self, event) -> str:
        """Что под курсором: имя кнопки, «mic», «status» или «drag»."""
        for name, (x0, y0, x1, y1) in self._buttons().items():
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                return name

        if (event.x - MIC_CX) ** 2 + (event.y - MIC_CY) ** 2 <= (MIC_R + 8) ** 2:
            return "mic"

        if self.error and 84 <= event.x <= WIDTH - 120 and 44 <= event.y <= 74:
            return "status"
        return "drag"

    def _on_press(self, event) -> None:
        self._drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        hit = self._hit(event)
        if hit != "drag":
            self._pressed = hit
            self._draw()

    def _on_release(self, event) -> None:
        hit = self._hit(event)
        pressed = self._pressed
        self._pressed = ""
        moved = False
        if self._drag:
            moved = (
                abs(event.x_root - self._drag[0]) > 4
                or abs(event.y_root - self._drag[1]) > 4
            )
        self._drag = None
        self._draw()
        if moved:
            return
        # Действие выполняем, только если нажали и отпустили на одной кнопке.
        if pressed and pressed != hit:
            return
        self.activate(hit)

    def activate(self, hit: str) -> None:
        """Выполнить действие кнопки (отдельно — удобно и для тестов)."""
        if hit == "mic":
            self.app.toggle()
        elif hit == "stop":
            self._stop_recording()
        elif hit == "close":
            self.close()
        elif hit == "settings":
            self.open_settings()
        elif hit == "status" and self.error:
            self.open_log()

    def _stop_recording(self) -> None:
        """Выключить микрофон: остановить запись и вставить остаток."""
        stopper = getattr(self.app, "stop_recording", None)
        try:
            if callable(stopper):
                stopper()
            else:  # pragma: no cover - на всякий случай
                self.app.toggle()
        except Exception as exc:  # pragma: no cover
            log.warning("Не удалось остановить запись: %s", exc)

    def close(self) -> None:
        """Крестик закрывает программу полностью."""
        log.info("Закрытие по крестику")
        try:
            self.on_quit()
        except Exception as exc:  # pragma: no cover
            log.warning("Выход не удался: %s", exc)
            self.hide()

    def _on_drag(self, event) -> None:
        if not self._drag:
            return
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        self.root.geometry(f"+{self._drag[2] + dx}+{self._drag[3] + dy}")

    def _on_motion(self, event) -> None:
        hit = self._hit(event)
        hover = "" if hit == "drag" else hit
        if hover != self._hover:
            self._hover = hover
            self._draw()

    def _on_right_click(self, event) -> None:
        self.open_menu(event)

    def open_menu(self, event=None) -> None:
        try:
            x = event.x_root if event else self.root.winfo_x()
            y = event.y_root if event else self.root.winfo_y()
            self.menu.tk_popup(x, y)
        except tk.TclError:  # pragma: no cover
            pass
        finally:
            try:
                self.menu.grab_release()
            except tk.TclError:  # pragma: no cover
                pass

    # ------------------------------------------------------------------ actions
    def toggle(self) -> None:
        self.app.toggle()

    def show(self) -> None:
        try:
            self.root.deiconify()
            self.root.overrideredirect(True)
            # Tk может пересоздать окно — стиль «не забирать фокус» ставим снова,
            # иначе клик по плашке уводит фокус и Ctrl+V пропадает.
            winfocus.make_no_activate(self.root)
            winfocus.keep_on_top(self.root, True)
            # Окно могли пересоздать — стекло и скругление ставим заново.
            self.root.update_idletasks()
            self.apply_look()
        except tk.TclError:  # pragma: no cover
            pass

    def hide(self) -> None:
        try:
            self.root.withdraw()
        except tk.TclError:  # pragma: no cover
            pass

    def is_visible(self) -> bool:
        try:
            return bool(self.root.winfo_viewable())
        except tk.TclError:  # pragma: no cover
            return False

    def open_log(self) -> None:
        path = self.app.paths.log_file
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Журнал пока пуст\n", encoding="utf-8")
        open_path(path)

    def open_settings(self) -> None:
        try:
            SettingsDialog(self.root, self.app, self)
        except Exception as exc:  # pragma: no cover
            log.exception("Не удалось открыть настройки")
            messagebox.showerror(APP_TITLE, f"Настройки не открылись: {exc}")

    # -------------------------------------------------------------------- state
    def set_state(self, state: str, message: str = "") -> None:
        self.state = state
        if state == ERROR:
            self.error = message
            log.error("Оверлей: %s", message)
        else:
            self.error = ""
            self.partial = ""
        if state not in (RECORDING,):
            self.level = 0.0
        self._draw()

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, float(level)))
        if self.state == RECORDING:
            self._pulse += 1
        self._draw()

    def set_partial(self, text: str) -> None:
        self.partial = text or ""
        self._draw()

    def set_last(self, text: str) -> None:
        """Показать последний вставленный кусок — видно, что текст реально ушёл."""
        self.last = (text or "").strip()
        self.partial = ""
        self._draw()


class SettingsDialog:
    """Небольшой диалог: только то, что реально нужно менять."""

    def __init__(self, parent: tk.Misc, app: DictationApp, overlay: Overlay):
        self.app = app
        self.overlay = overlay
        cfg = app.config

        self.win = tk.Toplevel(parent)
        self.win.title(f"{APP_TITLE} — настройки")
        self.win.configure(bg=BG)
        self.win.geometry("500x440")
        self.win.resizable(False, False)
        try:
            winfocus.clear_no_activate(self.win)
        except Exception:  # pragma: no cover
            pass

        self.model = tk.StringVar(value=MODEL_LABELS.get(cfg.model, cfg.model))
        self.lang = tk.StringVar(value=LANGS.get(cfg.language, cfg.language))
        self.method = tk.StringVar(value=METHODS.get(cfg.insert_method, cfg.insert_method))
        self.mic = tk.StringVar(value=cfg.input_device or "Системный по умолчанию")
        self.hotkey = tk.StringVar(value=cfg.hotkey)
        self.autostart = tk.BooleanVar(value=cfg.autostart)
        self.admin_rights = tk.BooleanVar(value=getattr(cfg, "run_as_admin", True))
        self.sound = tk.BooleanVar(value=cfg.sound_feedback)
        opacity = float(getattr(cfg, "overlay_opacity", 0.94) or 0.94)
        self.opacity = tk.StringVar(
            value=min(GLASS_LEVELS, key=lambda name: abs(GLASS_LEVELS[name] - opacity))
        )

        self._build()

    def _build(self) -> None:
        pad = {"padx": 14, "pady": 6}
        tk.Label(self.win, text="Настройки", bg=BG, fg=TEXT, font=_font(13, True)).pack(
            anchor="w", padx=14, pady=(14, 4)
        )
        form = tk.Frame(self.win, bg=BG)
        form.pack(fill="both", expand=True, padx=8, pady=4)
        form.columnconfigure(1, weight=1)

        rows = [
            ("Модель", self.model, list(MODEL_LABELS.values())),
            ("Язык", self.lang, list(LANGS.values())),
            ("Вставка", self.method, list(METHODS.values())),
            ("Микрофон", self.mic, ["Системный по умолчанию"] + _devices()),
        ]
        for index, (label, var, values) in enumerate(rows):
            tk.Label(form, text=label, bg=BG, fg=MUTED, font=_font(9)).grid(
                row=index, column=0, sticky="w", **pad
            )
            combo = ttk.Combobox(form, textvariable=var, values=values, state="readonly")
            combo.grid(row=index, column=1, sticky="ew", **pad)
            combo.bind("<<ComboboxSelected>>", lambda _e: self.apply())

        tk.Label(form, text="Горячая клавиша", bg=BG, fg=MUTED, font=_font(9)).grid(
            row=4, column=0, sticky="w", **pad
        )
        hot = tk.Entry(form, textvariable=self.hotkey, bg="#1c2333", fg=TEXT,
                       insertbackground=TEXT, relief="flat")
        hot.grid(row=4, column=1, sticky="ew", **pad)

        for index, (label, var) in enumerate(
            (("Звуки", self.sound), ("Запускать с Windows", self.autostart))
        ):
            tk.Checkbutton(
                form, text=label, variable=var, bg=BG, fg=TEXT, selectcolor="#1c2333",
                activebackground=BG, activeforeground=TEXT, font=_font(9),
                command=self.apply,
            ).grid(row=5, column=index, sticky="w", padx=14, pady=4)

        tk.Label(form, text="Прозрачность", bg=BG, fg=MUTED, font=_font(9)).grid(
            row=6, column=0, sticky="w", **pad
        )
        opacity = ttk.Combobox(
            form, textvariable=self.opacity, values=list(GLASS_LEVELS), state="readonly"
        )
        opacity.grid(row=6, column=1, sticky="ew", **pad)
        opacity.bind("<<ComboboxSelected>>", lambda _e: self.apply())

        # Без прав администратора Windows не пускает текст в админские окна.
        tk.Checkbutton(
            form,
            text="Права администратора (нужны для окон, запущенных от админа)",
            variable=self.admin_rights,
            bg=BG,
            fg=TEXT,
            selectcolor="#1c2333",
            activebackground=BG,
            activeforeground=TEXT,
            font=_font(9),
            command=self.apply,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=14, pady=4)

        buttons = tk.Frame(self.win, bg=BG)
        buttons.pack(fill="x", padx=14, pady=12)
        tk.Button(buttons, text="Готово", command=self.save, bg=ACCENT, fg=WHITE,
                  relief="flat", bd=0, padx=16, pady=6, cursor="hand2").pack(side="right")
        tk.Button(buttons, text="Журнал", command=self.overlay.open_log, bg="#1c2333",
                  fg=TEXT, relief="flat", bd=0, padx=12, pady=6,
                  cursor="hand2").pack(side="left")
        tk.Button(buttons, text="Ярлык на рабочий стол", command=self.make_shortcut,
                  bg="#1c2333", fg=TEXT, relief="flat", bd=0, padx=10, pady=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Label(buttons, text=f"v{__version__}", bg=BG, fg=MUTED,
                 font=_font(8)).pack(side="right", padx=10)

    # ------------------------------------------------------------------ config
    def collect(self) -> Config:
        cfg = self.app.config.copy()
        cfg.model = next((k for k, v in MODEL_LABELS.items() if v == self.model.get()), cfg.model)
        cfg.language = next((k for k, v in LANGS.items() if v == self.lang.get()), cfg.language)
        cfg.insert_method = next(
            (k for k, v in METHODS.items() if v == self.method.get()), cfg.insert_method
        )
        mic = self.mic.get().strip()
        cfg.input_device = "" if mic in ("", "Системный по умолчанию") else mic.split(":")[0]
        cfg.hotkey = self.hotkey.get().strip() or cfg.hotkey
        cfg.autostart = bool(self.autostart.get())
        cfg.sound_feedback = bool(self.sound.get())
        cfg.run_as_admin = bool(self.admin_rights.get())
        cfg.overlay_opacity = GLASS_LEVELS.get(self.opacity.get(), cfg.overlay_opacity)
        cfg.normalize()
        return cfg

    def apply(self) -> None:
        try:
            cfg = self.collect()
        except Exception as exc:  # pragma: no cover
            log.warning("Настройки не прочитаны: %s", exc)
            return
        warnings = self.app.apply_config(cfg)
        try:
            # Стекло применяем сразу, без перезапуска.
            self.overlay.apply_look()
        except Exception as exc:  # pragma: no cover
            log.debug("Вид плашки не обновился: %s", exc)
        if warnings:
            messagebox.showwarning(APP_TITLE, "\n".join(warnings), parent=self.win)

    def make_shortcut(self) -> None:
        """Создать ярлык для запуска двойным щелчком (без run.bat)."""
        from golos.system import create_shortcuts

        try:
            created = create_shortcuts()
        except Exception as exc:  # pragma: no cover - Windows/PowerShell
            log.warning("Ярлык не создан: %s", exc)
            created = []
        if created:
            messagebox.showinfo(
                APP_TITLE,
                "Ярлык «Голос» готов:\n" + "\n".join(str(item) for item in created),
                parent=self.win,
            )
        else:
            messagebox.showwarning(
                APP_TITLE,
                "Не удалось создать ярлык. Запустите install.bat — он сделает его сам.",
                parent=self.win,
            )

    def save(self) -> None:
        self.apply()
        self.win.destroy()


def _devices() -> list[str]:
    try:
        return list_input_devices()
    except AudioError as exc:
        log.debug("Микрофоны недоступны: %s", exc)
        return []
