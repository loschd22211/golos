"""Единое окно «Голос» — вместо шести вкладок настроек.

Минимум решений для пользователя: большая кнопка записи, живой индикатор
уровня, последний распознанный текст и несколько быстрых переключателей.
Всё остальное спрятано под «Ещё настройки» и применяется сразу, без кнопки
«Сохранить».
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_TITLE, __version__
from .app import (
    ERROR,
    IDLE,
    LOADING,
    PAUSED,
    PROCESSING,
    RECORDING,
    STATE_LABELS,
    DictationApp,
)
from .audio import AudioError, list_input_devices
from .config import Config
from .hotkeys import pretty_hotkey
from .system import open_path

log = logging.getLogger(__name__)

# --------------------------------------------------------------------- палитра
BG = "#11141b"
CARD = "#1a1f2b"
CARD_2 = "#222836"
FIELD = "#262d3d"
BORDER = "#2e3648"
TEXT = "#e9eef8"
MUTED = "#8b96ab"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#6aa1ff"
RED = "#e5484d"
RED_HOVER = "#f06065"
GREEN = "#3ddc84"
AMBER = "#f5a524"
WHITE = "#ffffff"

STATE_COLORS = {
    IDLE: GREEN,
    LOADING: MUTED,
    RECORDING: RED,
    PROCESSING: AMBER,
    PAUSED: MUTED,
    ERROR: RED,
}

STATE_TITLES = {
    IDLE: "Готов",
    LOADING: "Загружаю модель…",
    RECORDING: "Слушаю…",
    PROCESSING: "Распознаю…",
    PAUSED: "Пауза",
    ERROR: "Ошибка",
}

# Дружелюбные названия вместо технических tiny/base/medium/large-v3.
MODEL_CHOICES = [
    ("Очень быстро · tiny", "tiny"),
    ("Быстро · base", "base"),
    ("Быстрый баланс · small", "small"),
    ("Баланс · medium", "medium"),
    ("Максимум точности · large-v3", "large-v3"),
]
MODEL_TO_LABEL = {model: label for label, model in MODEL_CHOICES}
LABEL_TO_MODEL = {label: model for label, model in MODEL_CHOICES}

LANG_CHOICES = [("Авто", "auto"), ("Рус", "ru"), ("Eng", "en")]
METHOD_CHOICES = [("Буфер + Ctrl+V", "paste"), ("Посимвольно", "type")]
DEFAULT_MIC = "Системный по умолчанию"

FONT = "Segoe UI"
PAD = 14


def _font(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


def _lighten(color: str, amount: float = 0.12) -> str:
    """Чуть осветлить цвет для hover-эффекта."""
    try:
        r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return color
    r = min(255, int(r + (255 - r) * amount))
    g = min(255, int(g + (255 - g) * amount))
    b = min(255, int(b + (255 - b) * amount))
    return f"#{r:02x}{g:02x}{b:02x}"


class MainWindow:
    """Главное окно: запись, статус, последний текст и быстрые настройки."""

    def __init__(self, root: tk.Tk, app: DictationApp):
        self.root = root
        self.app = app

        self._state = IDLE
        self._level = 0.0
        self._hover = False
        self._advanced_visible = False
        self._loading = False
        self._pulse = 0

        self._setup_window()
        self._init_vars()
        self._build()
        self.load_from_config(app.config)
        self.update_state(IDLE)

    # ------------------------------------------------------------------ window
    def _setup_window(self) -> None:
        self.root.title(f"{APP_TITLE} — диктовка")
        self.root.configure(bg=BG)
        self.root.geometry("560x700")
        self.root.minsize(520, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])
        style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT,
                        focuscolor=CARD)
        style.map("Card.TCheckbutton",
                  background=[("active", CARD)],
                  foreground=[("active", TEXT)])
        try:
            self.root.option_add("*TCombobox*Listbox.background", FIELD)
            self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
            self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
            self.root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
        except tk.TclError:  # pragma: no cover
            pass

        try:
            from .tray import make_icon_image

            self._icon = tk.PhotoImage(master=self.root, data=_png_data(make_icon_image(IDLE)))
            self.root.iconphoto(True, self._icon)
        except Exception as exc:  # pragma: no cover - иконка не критична
            log.debug("Иконка окна недоступна: %s", exc)

    # -------------------------------------------------------------------- vars
    def _init_vars(self) -> None:
        cfg = self.app.config
        self.model_var = tk.StringVar(value=MODEL_TO_LABEL.get(cfg.model, MODEL_CHOICES[3][0]))
        self.lang_var = tk.StringVar(value=cfg.language)
        self.mic_var = tk.StringVar(value=DEFAULT_MIC)
        self.method_var = tk.StringVar(value=cfg.insert_method)

        self.trailing_var = tk.BooleanVar(value=cfg.trailing_space)
        self.capitalize_var = tk.BooleanVar(value=cfg.capitalize)
        self.sound_var = tk.BooleanVar(value=cfg.sound_feedback)
        self.notify_var = tk.BooleanVar(value=cfg.notifications)
        self.vad_var = tk.BooleanVar(value=cfg.vad_filter)
        self.autostart_var = tk.BooleanVar(value=cfg.autostart)
        self.preload_var = tk.BooleanVar(value=cfg.preload_model)
        self.history_var = tk.BooleanVar(value=cfg.history_enabled)

        self.hotkey_var = tk.StringVar(value=cfg.hotkey)
        self.cancel_var = tk.StringVar(value=cfg.cancel_hotkey)
        self.beam_var = tk.IntVar(value=cfg.beam_size)
        self.prompt_var = tk.StringVar(value=cfg.initial_prompt)
        self.hotwords_var = tk.StringVar(value=cfg.hotwords)

        self.status_var = tk.StringVar(value=STATE_TITLES[IDLE])
        self.hint_var = tk.StringVar(value="Нажмите кнопку или Ctrl+Alt+Space")

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        self._build_header(outer)
        self._build_record_card(outer)
        self._build_text_card(outer)
        self._build_quick_card(outer)
        self._build_advanced(outer)
        self._build_footer(outer)

    def _build_header(self, parent) -> None:
        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x")

        tk.Label(header, text="🎙", font=_font(20), bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(header, text="Голос", font=_font(17, True), bg=BG, fg=TEXT).pack(
            side="left", padx=(8, 0)
        )
        self.status_label = tk.Label(
            header, textvariable=self.status_var, font=_font(10),
            bg=CARD, fg=GREEN, padx=12, pady=5,
        )
        self.status_label.pack(side="right")

    def _build_record_card(self, parent) -> None:
        card = tk.Frame(parent, bg=CARD)
        card.pack(fill="x", pady=(14, 0))

        self.button = tk.Canvas(
            card, width=176, height=176, bg=CARD, highlightthickness=0, cursor="hand2"
        )
        self.button.pack(pady=(16, 4))
        self.button.bind("<Button-1>", lambda _e: self.toggle_record())
        self.button.bind("<Enter>", lambda _e: self._set_hover(True))
        self.button.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw_button()

        self.hint_label = tk.Label(
            card, textvariable=self.hint_var, font=_font(10), bg=CARD, fg=MUTED
        )
        self.hint_label.pack()

        self.level_bar = tk.Canvas(
            card, width=420, height=10, bg=CARD, highlightthickness=0
        )
        self.level_bar.pack(pady=(10, 16))
        self._draw_level()

    def _build_text_card(self, parent) -> None:
        card = tk.Frame(parent, bg=CARD)
        card.pack(fill="x", pady=(PAD, 0))

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=PAD, pady=(12, 6))
        tk.Label(head, text="Последний текст", font=_font(10, True), bg=CARD, fg=MUTED).pack(
            side="left"
        )

        actions = tk.Frame(head, bg=CARD)
        actions.pack(side="right")
        self._flat_button(actions, "Копировать", self.copy_last, small=True).pack(side="left")
        self._flat_button(actions, "Вставить снова", self.paste_last, small=True).pack(
            side="left", padx=(6, 0)
        )

        self.last_text = tk.Text(
            card, height=4, wrap="word", font=_font(11), bg=FIELD, fg=TEXT,
            insertbackground=TEXT, relief="flat", padx=10, pady=8,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER,
        )
        self.last_text.pack(fill="x", padx=PAD, pady=(0, 12))
        self._set_last_text("Здесь появится распознанная речь.")

    def _build_quick_card(self, parent) -> None:
        card = tk.Frame(parent, bg=CARD)
        card.pack(fill="x", pady=(PAD, 0))
        grid = tk.Frame(card, bg=CARD)
        grid.pack(fill="x", padx=PAD, pady=(12, 6))
        grid.columnconfigure(1, weight=1)

        row = 0
        tk.Label(grid, text="Модель", font=_font(10), bg=CARD, fg=MUTED).grid(
            row=row, column=0, sticky="w", pady=4
        )
        self.model_combo = ttk.Combobox(
            grid, textvariable=self.model_var, state="readonly",
            values=[label for label, _ in MODEL_CHOICES], width=24,
        )
        self.model_combo.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=4)
        self.model_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply())

        row += 1
        tk.Label(grid, text="Язык", font=_font(10), bg=CARD, fg=MUTED).grid(
            row=row, column=0, sticky="w", pady=4
        )
        self.lang_frame = tk.Frame(grid, bg=CARD)
        self.lang_frame.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=4)
        self._lang_buttons = {}
        for text, value in LANG_CHOICES:
            btn = tk.Button(
                self.lang_frame, text=text, font=_font(10), bd=0, relief="flat",
                cursor="hand2", padx=12, pady=3,
                command=lambda v=value: self._set_language(v),
            )
            btn.pack(side="left", padx=(0, 6))
            self._lang_buttons[value] = btn

        row += 1
        tk.Label(grid, text="Микрофон", font=_font(10), bg=CARD, fg=MUTED).grid(
            row=row, column=0, sticky="w", pady=4
        )
        mic_row = tk.Frame(grid, bg=CARD)
        mic_row.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=4)
        mic_row.columnconfigure(0, weight=1)
        self.mic_combo = ttk.Combobox(
            mic_row, textvariable=self.mic_var, state="readonly", values=[DEFAULT_MIC]
        )
        self.mic_combo.grid(row=0, column=0, sticky="ew")
        self.mic_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply())
        self._flat_button(mic_row, "↻", self.refresh_devices, small=True).grid(
            row=0, column=1, padx=(6, 0)
        )

        row += 1
        tk.Label(grid, text="Вставка", font=_font(10), bg=CARD, fg=MUTED).grid(
            row=row, column=0, sticky="w", pady=4
        )
        self.method_combo = ttk.Combobox(
            grid, textvariable=self.method_var, state="readonly",
            values=[label for label, _ in METHOD_CHOICES], width=24,
        )
        self.method_combo.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=4)
        self.method_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply())

        row += 1
        toggles = tk.Frame(card, bg=CARD)
        toggles.pack(fill="x", padx=PAD, pady=(4, 14))
        for column in (0, 1):
            toggles.columnconfigure(column, weight=1)
        for index, (text, var) in enumerate(
            (
                ("Пробел в конце", self.trailing_var),
                ("Заглавные буквы", self.capitalize_var),
                ("Звуки", self.sound_var),
                ("Уведомления", self.notify_var),
            )
        ):
            ttk.Checkbutton(
                toggles, text=text, variable=var, style="Card.TCheckbutton",
                command=self._apply,
            ).grid(row=index // 2, column=index % 2, sticky="w", pady=3)

        self._update_lang_buttons()

    def _build_advanced(self, parent) -> None:
        self.advanced_toggle = tk.Button(
            parent, text="⚙  Ещё настройки", font=_font(10), bd=0, relief="flat",
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=TEXT,
            cursor="hand2", command=self.toggle_advanced,
        )
        self.advanced_toggle.pack(anchor="w", pady=(10, 0))

        self.advanced = tk.Frame(parent, bg=CARD)
        grid = tk.Frame(self.advanced, bg=CARD)
        grid.pack(fill="x", padx=PAD, pady=12)
        grid.columnconfigure(1, weight=1)

        def label(text, row):
            tk.Label(grid, text=text, font=_font(10), bg=CARD, fg=MUTED).grid(
                row=row, column=0, sticky="w", pady=4
            )

        label("Горячая клавиша", 0)
        hot_row = tk.Frame(grid, bg=CARD)
        hot_row.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)
        hot_row.columnconfigure(0, weight=1)
        tk.Entry(
            hot_row, textvariable=self.hotkey_var, font=_font(10), bg=FIELD, fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=0, column=0, sticky="ew", ipady=3)
        self._flat_button(
            hot_row, "Записать", lambda: self.capture_hotkey(self.hotkey_var), small=True
        ).grid(row=0, column=1, padx=(6, 0))

        label("Отмена записи", 1)
        tk.Entry(
            grid, textvariable=self.cancel_var, font=_font(10), bg=FIELD, fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4, ipady=3)

        label("beam_size", 2)
        tk.Spinbox(
            grid, from_=1, to=10, textvariable=self.beam_var, width=6, font=_font(10),
            bg=FIELD, fg=TEXT, relief="flat", buttonbackground=FIELD,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
        ).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=4)

        label("Свои слова", 3)
        tk.Entry(
            grid, textvariable=self.hotwords_var, font=_font(10), bg=FIELD, fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=4, ipady=3)

        label("Подсказка модели", 4)
        tk.Entry(
            grid, textvariable=self.prompt_var, font=_font(10), bg=FIELD, fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=4, ipady=3)

        extra = tk.Frame(self.advanced, bg=CARD)
        extra.pack(fill="x", padx=PAD, pady=(0, 6))
        for column in (0, 1):
            extra.columnconfigure(column, weight=1)
        for index, (text, var) in enumerate(
            (
                ("Фильтр тишины (VAD)", self.vad_var),
                ("Автозапуск с Windows", self.autostart_var),
                ("Загружать модель при старте", self.preload_var),
                ("Вести историю", self.history_var),
            )
        ):
            ttk.Checkbutton(
                extra, text=text, variable=var, style="Card.TCheckbutton",
                command=self._apply,
            ).grid(row=index // 2, column=index % 2, sticky="w", pady=3)

        links = tk.Frame(self.advanced, bg=CARD)
        links.pack(fill="x", padx=PAD, pady=(0, 12))
        self._flat_button(links, "Журнал", self.open_log, small=True).pack(side="left")
        self._flat_button(
            links, "История", lambda: open_path(self.app.paths.history_file), small=True
        ).pack(side="left", padx=(6, 0))
        self._flat_button(
            links, "Папка настроек", lambda: open_path(self.app.paths.data_dir), small=True
        ).pack(side="left", padx=(6, 0))
        self._flat_button(links, "Сбросить", self.reset_defaults, small=True).pack(side="right")

    def _build_footer(self, parent) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        tk.Label(
            footer, text=f"v{__version__} · офлайн, ничего не уходит в интернет",
            font=_font(9), bg=BG, fg=MUTED,
        ).pack(side="left")
        self._flat_button(footer, "Скрыть в трей", self.hide, small=True).pack(side="right")

    # ------------------------------------------------------------------ widgets
    def _flat_button(self, parent, text: str, command, small: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, font=_font(9 if small else 10),
            bd=0, relief="flat", bg=CARD_2, fg=TEXT, activebackground=FIELD,
            activeforeground=TEXT, cursor="hand2",
            padx=8 if small else 12, pady=2 if small else 6,
        )

    # --------------------------------------------------------------- отрисовка
    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw_button()

    def _draw_button(self) -> None:
        canvas = self.button
        canvas.delete("all")
        size = 176
        center = size / 2

        if self._state == RECORDING:
            color = RED_HOVER if self._hover else RED
        else:
            color = ACCENT_HOVER if self._hover else ACCENT

        # мягкое «свечение» под кнопкой
        canvas.create_oval(10, 12, size - 10, size - 8, fill=CARD_2, outline="")
        canvas.create_oval(8, 8, size - 8, size - 8, fill=color, outline="")

        if self._state == RECORDING:
            pulse = 6 + (self._pulse % 10)
            canvas.create_oval(
                8 - pulse, 8 - pulse, size - 8 + pulse, size - 8 + pulse,
                outline=RED, width=2,
            )

        white = WHITE
        cx = center
        top = center - 38
        bottom = center + 2
        half = 13
        # капсула микрофона
        canvas.create_rectangle(cx - half, top + half, cx + half, bottom - half,
                                fill=white, outline="")
        canvas.create_oval(cx - half, top, cx + half, top + 2 * half, fill=white, outline="")
        canvas.create_oval(cx - half, bottom - 2 * half, cx + half, bottom,
                           fill=white, outline="")
        # дужка
        canvas.create_arc(cx - 25, center - 20, cx + 25, center + 22,
                          start=180, extent=180, style="arc", width=4, outline=white)
        # стойка и основание
        canvas.create_line(cx, center + 20, cx, center + 34, fill=white, width=4)
        canvas.create_line(cx - 16, center + 36, cx + 16, center + 36, fill=white, width=4)

        if self._state == PROCESSING:
            for offset in (-18, 0, 18):
                canvas.create_oval(
                    cx + offset - 3, center + 52, cx + offset + 3, center + 58,
                    fill=AMBER, outline="",
                )

    def _draw_level(self) -> None:
        bar = self.level_bar
        bar.delete("all")
        width, height = 420, 10
        bar.create_rectangle(0, 0, width, height, fill=CARD_2, outline="")
        level = max(0.0, min(1.0, self._level))
        if level <= 0.01:
            return
        filled = max(4, int(width * level))
        if level < 0.55:
            color = GREEN
        elif level < 0.8:
            color = AMBER
        else:
            color = RED
        bar.create_rectangle(0, 0, filled, height, fill=color, outline="")

    # ------------------------------------------------------------------ actions
    def toggle_record(self) -> None:
        self.app.toggle()

    def toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced.pack(fill="x", pady=(6, 0))
            self.advanced_toggle.configure(text="⚙  Скрыть настройки")
            self.root.geometry("560x900")
        else:
            self.advanced.pack_forget()
            self.advanced_toggle.configure(text="⚙  Ещё настройки")
            self.root.geometry("560x700")

    def copy_last(self) -> None:
        text = (self.app.last_text or "").strip()
        if not text:
            return
        try:
            self.app.injector.clipboard.set_text(text + " ")
        except Exception as exc:  # pragma: no cover
            messagebox.showerror(APP_TITLE, f"Буфер обмена недоступен: {exc}", parent=self.root)

    def paste_last(self) -> None:
        if not self.app.insert_last():
            messagebox.showinfo(APP_TITLE, "Пока нет текста для вставки", parent=self.root)

    def _set_language(self, value: str) -> None:
        self.lang_var.set(value)
        self._update_lang_buttons()
        self._apply()

    def _update_lang_buttons(self) -> None:
        current = self.lang_var.get()
        for value, button in getattr(self, "_lang_buttons", {}).items():
            active = value == current
            button.configure(
                bg=ACCENT if active else CARD_2,
                fg=WHITE if active else MUTED,
                activebackground=ACCENT if active else FIELD,
                activeforeground=WHITE if active else TEXT,
            )

    def refresh_devices(self) -> None:
        values = [DEFAULT_MIC]
        try:
            values.extend(list_input_devices())
        except AudioError as exc:
            log.warning("Микрофоны недоступны: %s", exc)
        self.mic_combo.configure(values=values)
        current = self.mic_var.get()
        if current not in values and current:
            match = None
            for value in values[1:]:
                index, _, name = value.partition(":")
                if current == index.strip() or current.lower() in name.lower():
                    match = value
                    break
            self.mic_var.set(match or DEFAULT_MIC)

    # ------------------------------------------------------------------ config
    def load_from_config(self, cfg: Config) -> None:
        self._loading = True
        try:
            self.model_var.set(MODEL_TO_LABEL.get(cfg.model, MODEL_CHOICES[3][0]))
            self.lang_var.set(cfg.language)
            self.method_var.set(
                next(
                    (label for label, value in METHOD_CHOICES if value == cfg.insert_method),
                    METHOD_CHOICES[0][0],
                )
            )
            self.mic_var.set(cfg.input_device or DEFAULT_MIC)
            self.trailing_var.set(cfg.trailing_space)
            self.capitalize_var.set(cfg.capitalize)
            self.sound_var.set(cfg.sound_feedback)
            self.notify_var.set(cfg.notifications)
            self.vad_var.set(cfg.vad_filter)
            self.autostart_var.set(cfg.autostart)
            self.preload_var.set(cfg.preload_model)
            self.history_var.set(cfg.history_enabled)
            self.hotkey_var.set(cfg.hotkey)
            self.cancel_var.set(cfg.cancel_hotkey)
            self.beam_var.set(cfg.beam_size)
            self.prompt_var.set(cfg.initial_prompt)
            self.hotwords_var.set(cfg.hotwords)
        finally:
            self._loading = False

        self.refresh_devices()
        self._update_lang_buttons()
        self.hint_var.set(f"Нажмите кнопку или {pretty_hotkey(cfg.hotkey)}")

    def collect_config(self) -> Config:
        cfg = self.app.config.copy()

        cfg.model = LABEL_TO_MODEL.get(self.model_var.get(), cfg.model)
        cfg.language = self.lang_var.get()
        cfg.insert_method = next(
            (value for label, value in METHOD_CHOICES if label == self.method_var.get()),
            cfg.insert_method,
        )
        mic = self.mic_var.get().strip()
        cfg.input_device = "" if mic in ("", DEFAULT_MIC) else mic.split(":")[0].strip()

        cfg.trailing_space = bool(self.trailing_var.get())
        cfg.capitalize = bool(self.capitalize_var.get())
        cfg.sound_feedback = bool(self.sound_var.get())
        cfg.notifications = bool(self.notify_var.get())
        cfg.vad_filter = bool(self.vad_var.get())
        cfg.autostart = bool(self.autostart_var.get())
        cfg.preload_model = bool(self.preload_var.get())
        cfg.history_enabled = bool(self.history_var.get())

        cfg.hotkey = self.hotkey_var.get().strip() or cfg.hotkey
        cfg.cancel_hotkey = self.cancel_var.get().strip()
        cfg.beam_size = _safe_int(self.beam_var.get(), cfg.beam_size)
        cfg.initial_prompt = self.prompt_var.get()
        cfg.hotwords = self.hotwords_var.get()
        cfg.normalize()
        return cfg

    def _apply(self) -> None:
        """Применить настройки сразу — без кнопки «Сохранить»."""
        if self._loading:
            return
        try:
            cfg = self.collect_config()
        except Exception as exc:  # pragma: no cover
            log.warning("Не удалось прочитать настройки: %s", exc)
            return

        warnings = self.app.apply_config(cfg)
        self.hint_var.set(f"Нажмите кнопку или {pretty_hotkey(cfg.hotkey)}")
        if warnings:
            messagebox.showwarning(APP_TITLE, "\n".join(warnings), parent=self.root)

    def reset_defaults(self) -> None:
        if not messagebox.askyesno(
            APP_TITLE, "Вернуть настройки по умолчанию?", parent=self.root
        ):
            return
        self.load_from_config(Config())
        self._apply()

    def open_log(self) -> None:
        path = self.app.paths.log_file
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Журнал пока пуст\n", encoding="utf-8")
        open_path(path)

    def capture_hotkey(self, target: tk.StringVar) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Новое сочетание")
        dialog.geometry("420x150")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        try:
            dialog.grab_set()
        except tk.TclError:  # pragma: no cover
            pass

        tk.Label(
            dialog, text="Нажмите сочетание клавиш", font=_font(12, True), bg=BG, fg=TEXT
        ).pack(pady=(24, 4))
        tk.Label(
            dialog, text="например Ctrl+Alt+Space · Esc — отмена",
            font=_font(9), bg=BG, fg=MUTED,
        ).pack()

        def on_key(event) -> None:
            if event.keysym == "Escape":
                dialog.destroy()
                return
            combo = _event_to_hotkey(event)
            if combo:
                target.set(combo)
                dialog.destroy()
                self._apply()

        dialog.bind("<KeyPress>", on_key)
        dialog.focus_force()

    # ------------------------------------------------------------------- state
    def show(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
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

    def update_state(self, state: str, message: str = "") -> None:
        self._state = state
        title = STATE_TITLES.get(state, STATE_LABELS.get(state, state))
        if state == ERROR and message:
            title = "Ошибка"
        self.status_var.set(title)
        color = STATE_COLORS.get(state, MUTED)
        try:
            self.status_label.configure(fg=color)
        except tk.TclError:  # pragma: no cover
            pass

        self.hint_var.set(
            {
                RECORDING: "Говорите… нажмите ещё раз, чтобы вставить текст",
                PROCESSING: "Секунду, распознаю речь…",
                ERROR: message[:120] if message else "Что-то пошло не так",
                PAUSED: "Пауза — нажмите «Продолжить» в трее",
                LOADING: "Загружаю модель Whisper — первый раз это дольше",
            }.get(state, f"Нажмите кнопку или {pretty_hotkey(self.app.config.hotkey)}")
        )
        self._draw_button()
        self._draw_level()

    def update_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))
        if self._state == RECORDING:
            self._pulse += 1
        self._draw_level()
        if self._state == RECORDING:
            self._draw_button()

    def set_last_text(self, text: str) -> None:
        self._set_last_text(text.strip() or "Здесь появится распознанная речь.")

    def _set_last_text(self, text: str) -> None:
        try:
            self.last_text.configure(state="normal")
            self.last_text.delete("1.0", "end")
            self.last_text.insert("1.0", text)
            self.last_text.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass


# ----------------------------------------------------------------------- utils
def _safe_int(value, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _png_data(image) -> str:
    """PNG в base64 — tk.PhotoImage умеет читать из data=."""
    import base64
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


_KEY_ALIASES = {
    "space": "space",
    "Return": "enter",
    "KP_Enter": "enter",
    "Escape": "escape",
    "BackSpace": "backspace",
    "Tab": "tab",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "page up",
    "Next": "page down",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "semicolon": ";",
    "apostrophe": "'",
    "bracketleft": "[",
    "bracketright": "]",
    "minus": "-",
    "equal": "=",
    "grave": "`",
    "backslash": "\\",
}


def _event_to_hotkey(event) -> str:
    """Собрать строку вида ctrl+alt+space из события tkinter."""
    keysym = event.keysym
    if keysym in (
        "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
        "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock",
    ):
        return ""

    state = int(event.state)
    parts: list[str] = []
    if state & 0x4:  # Control
        parts.append("ctrl")
    if state & (0x20000 | 0x8):  # Alt (Windows | X11)
        parts.append("alt")
    if state & 0x1:  # Shift
        parts.append("shift")
    if state & (0x40 | 0x80):  # Windows / Super
        parts.append("windows")

    if not parts:
        return ""

    key = _KEY_ALIASES.get(keysym)
    if key is None:
        key = keysym.lower()
    return "+".join(parts + [key])
