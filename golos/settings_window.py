"""Окно настроек на tkinter.

Все изменения применяются по кнопке «Сохранить»: собирается новый Config,
проверяется через ``DictationApp.apply_config`` и сохраняется в JSON.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from . import APP_TITLE, __version__
from .app import DictationApp
from .audio import AudioError, MicrophoneRecorder, list_input_devices
from .config import (
    COMPUTE_TYPES,
    DEVICES,
    INSERT_METHODS,
    LANGUAGES,
    MODEL_LABELS,
    MODELS,
    Config,
)
from .system import open_path, set_autostart

log = logging.getLogger(__name__)

PAD = {"padx": 6, "pady": 3}


class SettingsWindow:
    """Главное (и единственное) окно: статус сверху, вкладки с настройками."""

    def __init__(self, root: tk.Tk, app: DictationApp, on_close: Callable[[], None] | None = None):
        self.root = root
        self.app = app
        self.on_close = on_close
        self._syncing = False

        self.win = tk.Toplevel(root)
        self.win.title(f"{APP_TITLE} — настройки")
        self.win.geometry("760x680")
        self.win.minsize(700, 560)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.withdraw()

        self._build_vars()
        self._build_layout()
        self.load_from_config(self.app.config)

    # ------------------------------------------------------------------- public
    def show(self) -> None:
        self.load_from_config(self.app.config)
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def hide(self) -> None:
        self.win.withdraw()
        if self.on_close:
            self.on_close()

    def is_visible(self) -> bool:
        try:
            return bool(self.win.winfo_viewable())
        except tk.TclError:  # pragma: no cover
            return False

    def update_status(self, state: str, message: str = "") -> None:
        try:
            self.status_var.set(message or state)
            self.win.after(0, self._refresh_stats)
        except tk.TclError:  # pragma: no cover
            pass

    # -------------------------------------------------------------------- build
    def _build_vars(self) -> None:
        self.hotkey_var = tk.StringVar()
        self.cancel_hotkey_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.compute_var = tk.StringVar()
        self.language_var = tk.StringVar()
        self.beam_var = tk.IntVar(value=5)
        self.vad_var = tk.BooleanVar()
        self.condition_var = tk.BooleanVar()
        self.prompt_var = tk.StringVar()
        self.hotwords_var = tk.StringVar()

        self.mic_var = tk.StringVar()
        self.samplerate_var = tk.IntVar(value=16000)
        self.trim_var = tk.BooleanVar()
        self.mindur_var = tk.DoubleVar(value=0.35)

        self.method_var = tk.StringVar()
        self.restore_var = tk.BooleanVar()
        self.delay_var = tk.DoubleVar(value=0.15)
        self.trailing_var = tk.BooleanVar()
        self.capitalize_var = tk.BooleanVar()
        self.fillers_var = tk.BooleanVar()

        self.sound_var = tk.BooleanVar()
        self.notify_var = tk.BooleanVar()
        self.autostart_var = tk.BooleanVar()
        self.preload_var = tk.BooleanVar()
        self.history_var = tk.BooleanVar()
        self.loglevel_var = tk.StringVar(value="INFO")

        self.status_var = tk.StringVar(value="Готов")
        self.stats_var = tk.StringVar(value="")
        self.last_var = tk.StringVar(value="")
        self.mic_test_var = tk.StringVar(value="")

    def _build_layout(self) -> None:
        header = ttk.Frame(self.win, padding=(10, 8))
        header.pack(fill="x")
        ttk.Label(header, text="🎙 Голос", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(
            header,
            text=f"v{__version__} · офлайн-диктовка (Whisper)",
            foreground="#666",
        ).pack(side="left", padx=10)
        self.state_label = ttk.Label(header, textvariable=self.status_var, foreground="#0a7")
        self.state_label.pack(side="right")

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self._tab_main(notebook)
        self._tab_recognition(notebook)
        self._tab_microphone(notebook)
        self._tab_insert(notebook)
        self._tab_dictionary(notebook)
        self._tab_misc(notebook)

        bottom = ttk.Frame(self.win, padding=(10, 6))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Сохранить", command=self.save, width=16).pack(side="right", padx=4)
        ttk.Button(bottom, text="Закрыть", command=self.hide, width=12).pack(side="right", padx=4)
        ttk.Button(bottom, text="Сбросить", command=self.reset_defaults, width=12).pack(
            side="left", padx=4
        )
        ttk.Button(bottom, text="Открыть журнал", command=self.open_log).pack(side="left", padx=4)
        ttk.Button(
            bottom, text="История", command=lambda: open_path(self.app.paths.history_file)
        ).pack(side="left", padx=4)

    def _tab_main(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Основное")

        box = ttk.LabelFrame(tab, text="Горячие клавиши", padding=10)
        box.pack(fill="x", pady=(0, 10))

        ttk.Label(box, text="Начать / остановить запись:").grid(row=0, column=0, sticky="w", **PAD)
        entry = ttk.Entry(box, textvariable=self.hotkey_var, width=28)
        entry.grid(row=0, column=1, sticky="w", **PAD)
        ttk.Button(
            box, text="Записать сочетание", command=lambda: self.capture_hotkey(self.hotkey_var)
        ).grid(row=0, column=2, sticky="w", **PAD)

        ttk.Label(box, text="Отменить запись:").grid(row=1, column=0, sticky="w", **PAD)
        ttk.Entry(box, textvariable=self.cancel_hotkey_var, width=28).grid(
            row=1, column=1, sticky="w", **PAD
        )
        ttk.Button(
            box,
            text="Записать сочетание",
            command=lambda: self.capture_hotkey(self.cancel_hotkey_var),
        ).grid(row=1, column=2, sticky="w", **PAD)

        ttk.Label(
            box,
            text="Клавиши работают в любой программе. Ctrl+Alt+Space по умолчанию.",
            foreground="#666",
        ).grid(row=2, column=0, columnspan=3, sticky="w", **PAD)

        info = ttk.LabelFrame(tab, text="Статистика", padding=10)
        info.pack(fill="both", expand=True)
        ttk.Label(info, textvariable=self.stats_var, justify="left").pack(anchor="w")
        ttk.Label(info, text="Последний распознанный текст:", foreground="#666").pack(
            anchor="w", pady=(8, 0)
        )
        self.last_label = ttk.Label(info, textvariable=self.last_var, wraplength=650, justify="left")
        self.last_label.pack(anchor="w")

        ttk.Button(info, text="Обновить", command=self._refresh_stats, width=14).pack(
            anchor="w", pady=(10, 0)
        )

    def _tab_recognition(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Распознавание")

        ttk.Label(tab, text="Модель Whisper:").grid(row=0, column=0, sticky="w", **PAD)
        combo = ttk.Combobox(
            tab,
            textvariable=self.model_var,
            values=[f"{m}" for m in MODELS],
            state="readonly",
            width=14,
        )
        combo.grid(row=0, column=1, sticky="w", **PAD)
        self.model_hint = ttk.Label(tab, text="", foreground="#666")
        self.model_hint.grid(row=0, column=2, sticky="w", **PAD)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._update_model_hint())

        ttk.Label(tab, text="Язык:").grid(row=1, column=0, sticky="w", **PAD)
        lang = ttk.Combobox(
            tab,
            textvariable=self.language_var,
            values=list(LANGUAGES),
            state="readonly",
            width=14,
        )
        lang.grid(row=1, column=1, sticky="w", **PAD)
        ttk.Label(
            tab,
            text="«Авто» — русский и английский в одной фразе (модель определяет язык сама)",
            foreground="#666",
        ).grid(row=1, column=2, sticky="w", **PAD)

        ttk.Label(tab, text="Устройство:").grid(row=2, column=0, sticky="w", **PAD)
        ttk.Combobox(
            tab,
            textvariable=self.device_var,
            values=list(DEVICES),
            state="readonly",
            width=14,
        ).grid(row=2, column=1, sticky="w", **PAD)
        ttk.Label(tab, text="auto — CUDA, если есть, иначе CPU", foreground="#666").grid(
            row=2, column=2, sticky="w", **PAD
        )

        ttk.Label(tab, text="Точность вычислений:").grid(row=3, column=0, sticky="w", **PAD)
        ttk.Combobox(
            tab,
            textvariable=self.compute_var,
            values=list(COMPUTE_TYPES),
            state="readonly",
            width=14,
        ).grid(row=3, column=1, sticky="w", **PAD)

        ttk.Label(tab, text="beam_size:").grid(row=4, column=0, sticky="w", **PAD)
        ttk.Spinbox(tab, from_=1, to=10, textvariable=self.beam_var, width=6).grid(
            row=4, column=1, sticky="w", **PAD
        )
        ttk.Label(tab, text="больше — точнее, но медленнее (5 — оптимум)", foreground="#666").grid(
            row=4, column=2, sticky="w", **PAD
        )

        ttk.Checkbutton(
            tab, text="Фильтр тишины (VAD) — не распознавать паузы", variable=self.vad_var
        ).grid(row=5, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Checkbutton(
            tab,
            text="Учитывать предыдущий текст (может «зацикливаться» на повторах)",
            variable=self.condition_var,
        ).grid(row=6, column=0, columnspan=3, sticky="w", **PAD)

        prompt_box = ttk.LabelFrame(tab, text="Подсказка модели (initial prompt)", padding=8)
        prompt_box.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        tab.columnconfigure(2, weight=1)
        ttk.Entry(prompt_box, textvariable=self.prompt_var).pack(fill="x")
        ttk.Label(
            prompt_box,
            text="Помогает сохранять английские слова латиницей и узнавать ваши термины.",
            foreground="#666",
        ).pack(anchor="w", pady=(4, 0))

        hot_box = ttk.LabelFrame(tab, text="Свои слова (hotwords)", padding=8)
        hot_box.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(6, 4))
        ttk.Entry(hot_box, textvariable=self.hotwords_var).pack(fill="x")
        ttk.Label(
            hot_box,
            text="Через запятую: Kubernetes, Голанг, Сбер, тайпскрипт…",
            foreground="#666",
        ).pack(anchor="w", pady=(4, 0))

        ttk.Button(tab, text="Скачать/проверить модель", command=self.check_model).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

    def _tab_microphone(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Микрофон")

        ttk.Label(tab, text="Устройство ввода:").grid(row=0, column=0, sticky="w", **PAD)
        self.mic_combo = ttk.Combobox(tab, textvariable=self.mic_var, width=52, state="readonly")
        self.mic_combo.grid(row=0, column=1, sticky="w", **PAD)
        ttk.Button(tab, text="Обновить список", command=self.refresh_devices).grid(
            row=0, column=2, sticky="w", **PAD
        )

        ttk.Label(tab, text="Частота дискретизации:").grid(row=1, column=0, sticky="w", **PAD)
        ttk.Combobox(
            tab,
            textvariable=self.samplerate_var,
            values=(16000, 22050, 32000, 44100, 48000),
            width=10,
            state="readonly",
        ).grid(row=1, column=1, sticky="w", **PAD)
        ttk.Label(tab, text="16 000 Гц — стандарт для Whisper", foreground="#666").grid(
            row=1, column=2, sticky="w", **PAD
        )

        ttk.Checkbutton(tab, text="Обрезать тишину в начале и в конце", variable=self.trim_var).grid(
            row=2, column=0, columnspan=3, sticky="w", **PAD
        )

        ttk.Label(tab, text="Минимальная длина записи, сек:").grid(
            row=3, column=0, sticky="w", **PAD
        )
        ttk.Spinbox(
            tab, from_=0.1, to=10.0, increment=0.05, textvariable=self.mindur_var, width=8
        ).grid(row=3, column=1, sticky="w", **PAD)

        test_box = ttk.LabelFrame(tab, text="Проверка микрофона", padding=10)
        test_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(test_box, text="Записать 4 секунды и показать уровень", command=self.test_mic).pack(
            anchor="w"
        )
        ttk.Label(test_box, textvariable=self.mic_test_var, wraplength=620, justify="left").pack(
            anchor="w", pady=(6, 0)
        )

        ttk.Label(
            tab,
            text="Если микрофон молчит: Параметры Windows → Конфиденциальность → Микрофон → "
            "разрешить приложениям доступ.",
            foreground="#666",
            wraplength=640,
            justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _tab_insert(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Вставка текста")

        ttk.Label(tab, text="Способ вставки:").grid(row=0, column=0, sticky="w", **PAD)
        ttk.Combobox(
            tab,
            textvariable=self.method_var,
            values=list(INSERT_METHODS),
            state="readonly",
            width=40,
        ).grid(row=0, column=1, columnspan=2, sticky="w", **PAD)

        ttk.Checkbutton(
            tab, text="Возвращать прежнее содержимое буфера обмена", variable=self.restore_var
        ).grid(row=1, column=0, columnspan=3, sticky="w", **PAD)

        ttk.Label(tab, text="Задержка перед вставкой, сек:").grid(row=2, column=0, sticky="w", **PAD)
        ttk.Spinbox(
            tab, from_=0.0, to=1.0, increment=0.05, textvariable=self.delay_var, width=8
        ).grid(row=2, column=1, sticky="w", **PAD)
        ttk.Label(tab, text="увеличьте, если текст не успевает вставиться", foreground="#666").grid(
            row=2, column=2, sticky="w", **PAD
        )

        ttk.Checkbutton(tab, text="Добавлять пробел в конце фрагмента", variable=self.trailing_var).grid(
            row=3, column=0, columnspan=3, sticky="w", **PAD
        )
        ttk.Checkbutton(
            tab, text="Начинать предложения с заглавной буквы", variable=self.capitalize_var
        ).grid(row=4, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Checkbutton(
            tab, text="Удалять слова-паразиты (э-э, мм, uh, um)", variable=self.fillers_var
        ).grid(row=5, column=0, columnspan=3, sticky="w", **PAD)

        ttk.Label(
            tab,
            text="Если текст не вставляется в «запущенное от администратора» окно — "
            "запустите «Голос» тоже от администратора.",
            foreground="#666",
            wraplength=640,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _tab_dictionary(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Словарь замен")

        ttk.Label(
            tab,
            text="По одной паре в строке: «как распозналось = как нужно». "
            "Например: питон = Python, гитхаб = GitHub, докер = Docker.",
            foreground="#666",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self.dict_text = tk.Text(tab, height=18, wrap="word", font=("Consolas", 10))
        self.dict_text.pack(fill="both", expand=True)

    def _tab_misc(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Прочее")

        ttk.Checkbutton(tab, text="Звуковые сигналы начала/конца записи", variable=self.sound_var).grid(
            row=0, column=0, columnspan=2, sticky="w", **PAD
        )
        ttk.Checkbutton(tab, text="Показывать уведомления", variable=self.notify_var).grid(
            row=1, column=0, columnspan=2, sticky="w", **PAD
        )
        ttk.Checkbutton(tab, text="Запускать при входе в Windows", variable=self.autostart_var).grid(
            row=2, column=0, columnspan=2, sticky="w", **PAD
        )
        ttk.Checkbutton(
            tab, text="Загружать модель сразу при запуске (быстрее первая диктовка)", variable=self.preload_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", **PAD)
        ttk.Checkbutton(tab, text="Сохранять историю диктовок в файл", variable=self.history_var).grid(
            row=4, column=0, columnspan=2, sticky="w", **PAD
        )

        ttk.Label(tab, text="Уровень журнала:").grid(row=5, column=0, sticky="w", **PAD)
        ttk.Combobox(
            tab,
            textvariable=self.loglevel_var,
            values=("DEBUG", "INFO", "WARNING", "ERROR"),
            width=12,
            state="readonly",
        ).grid(row=5, column=1, sticky="w", **PAD)

        paths = tk.Text(tab, height=7, wrap="word", font=("Consolas", 9))
        paths.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        paths.insert(
            "1.0",
            "Файлы приложения:\n"
            f"  настройки: {self.app.paths.config_file}\n"
            f"  журнал:    {self.app.paths.log_file}\n"
            f"  история:   {self.app.paths.history_file}\n"
            f"  модели:    {self.app.paths.models_dir}",
        )
        paths.configure(state="disabled")
        tab.columnconfigure(1, weight=1)

        ttk.Button(tab, text="Открыть папку настроек", command=lambda: open_path(self.app.paths.data_dir)).grid(
            row=7, column=0, sticky="w", pady=(10, 0)
        )

    # ------------------------------------------------------------------- actions
    def load_from_config(self, cfg: Config) -> None:
        self._syncing = True
        try:
            self.hotkey_var.set(cfg.hotkey)
            self.cancel_hotkey_var.set(cfg.cancel_hotkey)
            self.model_var.set(cfg.model)
            self.device_var.set(cfg.device)
            self.compute_var.set(cfg.compute_type)
            self.language_var.set(cfg.language)
            self.beam_var.set(cfg.beam_size)
            self.vad_var.set(cfg.vad_filter)
            self.condition_var.set(cfg.condition_on_previous_text)
            self.prompt_var.set(cfg.initial_prompt)
            self.hotwords_var.set(cfg.hotwords)

            self.mic_var.set(cfg.input_device or "(по умолчанию)")
            self.samplerate_var.set(cfg.sample_rate)
            self.trim_var.set(cfg.trim_silence)
            self.mindur_var.set(cfg.min_duration)

            self.method_var.set(cfg.insert_method)
            self.restore_var.set(cfg.restore_clipboard)
            self.delay_var.set(cfg.paste_delay)
            self.trailing_var.set(cfg.trailing_space)
            self.capitalize_var.set(cfg.capitalize)
            self.fillers_var.set(cfg.remove_fillers)

            self.sound_var.set(cfg.sound_feedback)
            self.notify_var.set(cfg.notifications)
            self.autostart_var.set(cfg.autostart)
            self.preload_var.set(cfg.preload_model)
            self.history_var.set(cfg.history_enabled)
            self.loglevel_var.set(cfg.log_level)

            self.dict_text.delete("1.0", "end")
            self.dict_text.insert(
                "1.0", "\n".join(f"{src} = {dst}" for src, dst in cfg.replacements)
            )
        finally:
            self._syncing = False

        self.refresh_devices()
        self._update_model_hint()
        self._refresh_stats()

    def collect_config(self) -> Config:
        cfg = self.app.config.copy()

        cfg.hotkey = self.hotkey_var.get().strip() or "ctrl+alt+space"
        cfg.cancel_hotkey = self.cancel_hotkey_var.get().strip()
        cfg.model = self.model_var.get()
        cfg.device = self.device_var.get()
        cfg.compute_type = self.compute_var.get()
        cfg.language = self.language_var.get()
        cfg.beam_size = _safe_int(self.beam_var.get(), 5)
        cfg.vad_filter = bool(self.vad_var.get())
        cfg.condition_on_previous_text = bool(self.condition_var.get())
        cfg.initial_prompt = self.prompt_var.get()
        cfg.hotwords = self.hotwords_var.get()

        mic = self.mic_var.get().strip()
        cfg.input_device = "" if mic.startswith("(по умолчанию)") else mic.split(":")[0].strip()
        cfg.sample_rate = _safe_int(self.samplerate_var.get(), 16000)
        cfg.trim_silence = bool(self.trim_var.get())
        cfg.min_duration = _safe_float(self.mindur_var.get(), 0.35)

        cfg.insert_method = self.method_var.get()
        cfg.restore_clipboard = bool(self.restore_var.get())
        cfg.paste_delay = _safe_float(self.delay_var.get(), 0.15)
        cfg.trailing_space = bool(self.trailing_var.get())
        cfg.capitalize = bool(self.capitalize_var.get())
        cfg.remove_fillers = bool(self.fillers_var.get())

        cfg.sound_feedback = bool(self.sound_var.get())
        cfg.notifications = bool(self.notify_var.get())
        cfg.autostart = bool(self.autostart_var.get())
        cfg.preload_model = bool(self.preload_var.get())
        cfg.history_enabled = bool(self.history_var.get())
        cfg.log_level = self.loglevel_var.get()

        cfg.replacements = self._parse_dictionary(self.dict_text.get("1.0", "end"))
        cfg.normalize()
        return cfg

    def save(self) -> None:
        try:
            cfg = self.collect_config()
        except Exception as exc:  # pragma: no cover
            messagebox.showerror(APP_TITLE, f"Не удалось прочитать настройки: {exc}", parent=self.win)
            return

        warnings = self.app.apply_config(cfg)
        self.app.config = cfg
        set_autostart(cfg.autostart)

        if cfg.preload_model:
            self.app.preload_async()

        if warnings:
            messagebox.showwarning(APP_TITLE, "\n".join(warnings), parent=self.win)
        self.update_status("Готов", "Настройки сохранены")
        self._refresh_stats()

    def reset_defaults(self) -> None:
        if not messagebox.askyesno(
            APP_TITLE, "Вернуть все настройки к значениям по умолчанию?", parent=self.win
        ):
            return
        self.load_from_config(Config())

    def refresh_devices(self) -> None:
        values = ["(по умолчанию)"]
        try:
            values.extend(list_input_devices())
        except AudioError as exc:
            log.warning("Список микрофонов недоступен: %s", exc)

        current = self.mic_var.get().strip()
        self.mic_combo["values"] = values
        if current in values:
            return

        # Сохранён может быть индекс («3») или часть названия — ищем совпадение.
        match = None
        for value in values[1:]:
            index, _, name = value.partition(":")
            if current and (current == index.strip() or current.lower() in name.lower()):
                match = value
                break
        self.mic_var.set(match or values[0])

    def check_model(self) -> None:
        cfg = self.collect_config()
        self.model_hint.configure(text="Проверяю…")

        def worker() -> None:
            try:
                self.app.transcriber.config = cfg
                self.app.transcriber.preload()
                message = "Модель готова ✓"
            except Exception as exc:
                message = f"Ошибка: {exc}"
            self.win.after(0, lambda: self.model_hint.configure(text=message))

        threading.Thread(target=worker, daemon=True).start()

    def test_mic(self) -> None:
        if self.app.is_recording:
            messagebox.showinfo(APP_TITLE, "Сначала остановите запись", parent=self.win)
            return
        cfg = self.collect_config()
        self.mic_test_var.set("Идёт запись 4 секунды… говорите что-нибудь")
        recorder = MicrophoneRecorder(cfg)

        def worker() -> None:
            try:
                recorder.start()
                import time as _time

                peak = 0.0
                for _ in range(40):
                    _time.sleep(0.1)
                    peak = max(peak, recorder.peak)
                rec = recorder.stop()
                if rec is None:
                    message = "Данных нет — микрофон не отдал звук"
                else:
                    level = max(peak, rec.peak)
                    if level < 0.01:
                        message = (
                            f"Записано {rec.duration:.1f} с, но уровень очень низкий "
                            f"({level:.3f}). Проверьте микрофон и его разрешения в Windows."
                        )
                    else:
                        message = f"Микрофон работает ✓ записано {rec.duration:.1f} с, уровень {level:.2f}"
            except Exception as exc:
                message = f"Ошибка: {exc}"
            self.win.after(0, lambda: self.mic_test_var.set(message))

        threading.Thread(target=worker, daemon=True).start()

    def capture_hotkey(self, target: tk.StringVar) -> None:
        """Маленькое окно: нажмите сочетание — оно попадёт в поле."""
        dialog = tk.Toplevel(self.win)
        dialog.title("Новое сочетание клавиш")
        dialog.geometry("420x150")
        dialog.transient(self.win)
        dialog.grab_set()
        ttk.Label(
            dialog,
            text="Нажмите нужное сочетание клавиш\n(например: Ctrl+Alt+Space)",
            justify="center",
            font=("Segoe UI", 11),
        ).pack(expand=True, pady=(18, 6))
        hint = ttk.Label(dialog, text="Esc — отмена", foreground="#666")
        hint.pack()

        captured: list[str] = []

        def on_key(event) -> None:
            if event.keysym == "Escape" and not (event.state & 0x4):
                dialog.destroy()
                return
            combo = _event_to_hotkey(event)
            if combo:
                captured.append(combo)
                target.set(combo)
                dialog.destroy()

        dialog.bind("<KeyPress>", on_key)
        dialog.focus_force()

    def open_log(self) -> None:
        path = self.app.paths.log_file
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Журнал пока пуст\n", encoding="utf-8")
        open_path(path)

    # ------------------------------------------------------------------ helpers
    def _refresh_stats(self) -> None:
        try:
            status = self.app.status()
        except Exception:  # pragma: no cover
            return
        self.stats_var.set(
            f"Состояние: {status['state_label']}\n"
            f"Горячая клавиша: {status['hotkey']}\n"
            f"Модель: {status['model']} "
            f"({'загружена' if status['model_ready'] else 'ещё не загружена'})\n"
            f"Диктовок: {status['sessions']} · слов: {status['words']} · "
            f"минут речи: {status['minutes']}"
        )
        self.last_var.set(status["last_text"].strip() or "—")

    def _update_model_hint(self) -> None:
        self.model_hint.configure(text=MODEL_LABELS.get(self.model_var.get(), ""))

    @staticmethod
    def _parse_dictionary(raw: str) -> list[list[str]]:
        pairs: list[list[str]] = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            separator = "=>" if "=>" in line else "="
            if separator not in line:
                continue
            src, dst = line.split(separator, 1)
            src, dst = src.strip(), dst.strip()
            if src:
                pairs.append([src, dst])
        return pairs


# ----------------------------------------------------------------------- utils
def _safe_int(value, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _safe_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


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
    if keysym in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
                  "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock"):
        return ""

    state = int(event.state)
    parts: list[str] = []
    if state & 0x4:  # Control
        parts.append("ctrl")
    if state & (0x20000 | 0x8):  # Alt (Windows | X11)
        parts.append("alt")
    if state & 0x1:
        parts.append("shift")
    if state & 0x40 or state & 0x80:
        parts.append("windows")

    if not parts:
        return ""

    key = _KEY_ALIASES.get(keysym)
    if key is None:
        if len(keysym) == 1:
            key = keysym.lower()
        elif keysym.startswith("F") and keysym[1:].isdigit():
            key = keysym.lower()
        else:
            key = keysym.lower()
    return "+".join(parts + [key])
