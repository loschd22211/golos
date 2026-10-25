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
from . import winfocus

log = logging.getLogger(__name__)

WIDTH = 312
HEIGHT = 92

BG = "#141922"
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
    "tiny": "tiny — очень быстро",
    "base": "base — быстро",
    "small": "small — быстро и точно",
    "medium": "medium — точнее, медленнее",
    "large-v3": "large-v3 — максимум (нужен GPU)",
}
LANGS = {"auto": "Авто (рус + eng)", "ru": "Русский", "en": "English"}
METHODS = {"paste": "Буфер + Ctrl+V", "type": "Посимвольно"}


def _font(size: int, bold: bool = False):
    return (FONT, size, "bold") if bold else (FONT, size)


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
        self._hover = False
        self._pulse = 0
        self._drag = None

        self._setup_window()
        self._draw()
        self.set_state(IDLE)

    # ------------------------------------------------------------------ window
    def _setup_window(self) -> None:
        root = self.root
        root.title(APP_TITLE)
        root.overrideredirect(True)
        root.configure(bg=BG)
        try:
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.97)
        except tk.TclError:  # pragma: no cover
            pass

        self.canvas = tk.Canvas(
            root, width=WIDTH, height=HEIGHT, bg=BG, highlightthickness=0, cursor="hand2"
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
        self.menu.add_separator()
        self.menu.add_command(label="Выход", command=self.on_quit)

        self._place_default()
        root.update_idletasks()
        if not winfocus.make_no_activate(root):
            log.debug("WS_EX_NOACTIVATE недоступен — клик может забирать фокус")
        winfocus.keep_on_top(root, True)

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

        c.create_rectangle(0, 0, WIDTH, HEIGHT, fill=BG, outline="")
        c.create_rectangle(0, 0, WIDTH, 2, fill=color, outline="")

        # кнопка микрофона
        cx, cy, r = 46, HEIGHT // 2, 26
        if self.state == RECORDING:
            c.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill="#2a1a1e", outline="")
            pulse = 2 + (self._pulse % 6)
            ring = r + 4 + pulse
            c.create_oval(cx - ring, cy - ring, cx + ring, cy + ring, outline=RED, width=1)
        else:
            c.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill="#1c2333", outline="")
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
        shown = _tail(title, 34) if (self.partial and self.state != ERROR) else _short(title, 34)
        c.create_text(84, 30, anchor="w", text=shown, fill=title_color, font=_font(10, True))

        hint = f"{pretty_hotkey(self.app.config.hotkey)} · {self.state}" if self.state != ERROR else "нажмите журнал"
        if self.partial and self.state == RECORDING:
            hint = f"слушаю… {self.app.config.model}"
        elif self.last and self.state != ERROR:
            hint = f"вставлено: {_tail(self.last, 30)}"
        c.create_text(84, 52, anchor="w", text=_short(hint, 34), fill=MUTED, font=_font(8))

        # индикатор уровня
        bar_x0, bar_x1, bar_y = 84, WIDTH - 58, 66
        c.create_rectangle(bar_x0, bar_y, bar_x1, bar_y + 4, fill="#232b39", outline="")
        if self.level > 0.01:
            width = max(3, int((bar_x1 - bar_x0) * min(1.0, self.level)))
            bar_color = GREEN if self.level < 0.6 else (AMBER if self.level < 0.85 else RED)
            c.create_rectangle(bar_x0, bar_y, bar_x0 + width, bar_y + 4, fill=bar_color, outline="")

        # кнопки справа: журнал/ошибки и настройки, закрытие
        c.create_text(WIDTH - 44, 28, text="⚙", fill=MUTED, font=_font(11))
        c.create_text(WIDTH - 20, 28, text="✕", fill=MUTED, font=_font(10))
        if self.error:
            c.create_text(WIDTH - 44, 56, text="!", fill=RED, font=_font(11, True))

    # ------------------------------------------------------------------- events
    def _hit(self, event) -> str:
        if event.y > HEIGHT - 30 and event.x > WIDTH - 34:
            return "close"
        if event.y > HEIGHT - 34 and WIDTH - 62 < event.x <= WIDTH - 30:
            return "menu"
        if 84 < event.x < WIDTH - 62:
            return "status" if 40 < event.y < 76 else "drag"
        if event.x < 84:
            return "mic"
        return "drag"

    def _on_press(self, event) -> None:
        self._drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _on_release(self, event) -> None:
        hit = self._hit(event)
        moved = False
        if self._drag:
            moved = (
                abs(event.x_root - self._drag[0]) > 4
                or abs(event.y_root - self._drag[1]) > 4
            )
        self._drag = None
        if moved:
            return
        if hit == "mic":
            self.app.toggle()
        elif hit == "close":
            self.hide()
        elif hit == "menu":
            self.open_menu(event)
        elif hit == "status" and self.error:
            self.open_log()

    def _on_drag(self, event) -> None:
        if not self._drag:
            return
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        self.root.geometry(f"+{self._drag[2] + dx}+{self._drag[3] + dy}")

    def _on_motion(self, event) -> None:
        hover = event.x < 84
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
            winfocus.keep_on_top(self.root, True)
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
        self.win.geometry("460x380")
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
        self.sound = tk.BooleanVar(value=cfg.sound_feedback)

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

        buttons = tk.Frame(self.win, bg=BG)
        buttons.pack(fill="x", padx=14, pady=12)
        tk.Button(buttons, text="Готово", command=self.save, bg=ACCENT, fg=WHITE,
                  relief="flat", bd=0, padx=16, pady=6, cursor="hand2").pack(side="right")
        tk.Button(buttons, text="Журнал", command=self.overlay.open_log, bg="#1c2333",
                  fg=TEXT, relief="flat", bd=0, padx=12, pady=6,
                  cursor="hand2").pack(side="left")
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
        cfg.normalize()
        return cfg

    def apply(self) -> None:
        try:
            cfg = self.collect()
        except Exception as exc:  # pragma: no cover
            log.warning("Настройки не прочитаны: %s", exc)
            return
        warnings = self.app.apply_config(cfg)
        if warnings:
            messagebox.showwarning(APP_TITLE, "\n".join(warnings), parent=self.win)

    def save(self) -> None:
        self.apply()
        self.win.destroy()


def _devices() -> list[str]:
    try:
        return list_input_devices()
    except AudioError as exc:
        log.debug("Микрофоны недоступны: %s", exc)
        return []
