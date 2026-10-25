"""Ядро приложения: связывает микрофон, распознавание, горячие клавиши и вставку.

Логика намеренно не зависит от GUI: трей/настройки лишь подписываются на
события через интерфейс ``UiBridge``. Это позволяет тестировать диктовку
без Windows и без реального микрофона.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from . import elevate
from .audio import AudioError, MicrophoneRecorder, Recording
from .config import AppPaths, Config, save_config
from .hotkeys import HotkeyManager, normalize_hotkey
from .inject import InjectionError, TextInjector
from .system import beep, set_autostart
from .streaming import StreamingDictator
from .textproc import clean_text, preview
from .transcribe import TranscriptionError, WhisperTranscriber

log = logging.getLogger(__name__)

IDLE = "idle"
LOADING = "loading"
RECORDING = "recording"
PROCESSING = "processing"
ERROR = "error"
PAUSED = "paused"

STATE_LABELS = {
    IDLE: "Готов",
    LOADING: "Загружаю модель…",
    RECORDING: "Слушаю…",
    PROCESSING: "Распознаю…",
    ERROR: "Ошибка",
    PAUSED: "Пауза",
}


class UiBridge(Protocol):
    """То, что интерфейс (трей, окно настроек) должен уметь."""

    def on_state(self, state: str, message: str = "") -> None: ...
    def on_level(self, level: float) -> None: ...
    def on_text(self, text: str, raw: str = "") -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_notify(self, title: str, message: str) -> None: ...


class NullUi:
    """Пустой интерфейс — удобно для тестов и консольного режима."""

    def on_state(self, state: str, message: str = "") -> None:
        log.info("Состояние: %s %s", state, message)

    def on_level(self, level: float) -> None:
        pass

    def on_text(self, text: str, raw: str = "") -> None:
        print(text, end="", flush=True)

    def on_error(self, message: str) -> None:
        log.error("%s", message)

    def on_notify(self, title: str, message: str) -> None:
        log.info("%s: %s", title, message)


@dataclass
class SessionStats:
    """Мини-статистика для настроек/трея."""

    sessions: int = 0
    words: int = 0
    seconds: float = 0.0
    last_latency: float = 0.0
    recent: list[str] = field(default_factory=list)


class DictationApp:
    """Главный контроллер диктовки."""

    def __init__(
        self,
        config: Config | None = None,
        paths: AppPaths | None = None,
        recorder: MicrophoneRecorder | None = None,
        transcriber: WhisperTranscriber | None = None,
        injector: TextInjector | None = None,
        hotkeys: HotkeyManager | None = None,
        ui: UiBridge | None = None,
        preload: bool = True,
    ):
        self.paths = (paths or AppPaths.default()).ensure()
        self.config = config or Config()
        self.recorder = recorder or MicrophoneRecorder(self.config)
        self.transcriber = transcriber or WhisperTranscriber(self.config, self.paths.models_dir)
        self.injector = injector or TextInjector(
            self.config.insert_method,
            self.config.restore_clipboard,
            self.config.paste_delay,
        )
        # Перед каждой вставкой проверяем, что окно-получатель вообще примет
        # наш ввод, и пишем в журнал, куда уходит текст. Без этого Windows
        # молча выбрасывала нажатия, а плашка бодро писала «вставлено».
        if getattr(self.injector, "guard", None) is None:
            try:
                self.injector.guard = elevate.insertion_guard
            except Exception:  # pragma: no cover - экзотический инжектор
                pass
        self.admin = elevate.is_admin()
        # Фокус возвращаем прямо в момент вставки: к этому времени он мог
        # уехать в оверлей (клик, меню трея), и Ctrl+V пропадал.
        try:
            self.injector.focus = lambda: self.focus_hook()
        except Exception:  # pragma: no cover - экзотический инжектор
            pass
        self.hotkeys = hotkeys or HotkeyManager()
        self.ui = ui or NullUi()

        self.state = IDLE
        self.stats = SessionStats()
        self.last_text = ""
        self.last_raw = ""
        self.last_error = ""
        self.started_at = time.time()
        self.paused = False

        self.focus_hook = lambda: None  # оверлей подставит возврат фокуса
        self.stream = StreamingDictator(
            recorder=self.recorder,
            transcriber=self.transcriber,
            injector=self.injector,
            config=self.config,
            on_text=self._on_stream_text,
            on_state=self._on_stream_state,
            on_error=self._on_stream_error,
            on_partial=self._on_stream_partial,
            focus_restore=lambda: self.focus_hook(),
        )

        self._work_lock = threading.Lock()  # одна обработка за раз
        self._preload_thread: threading.Thread | None = None
        self._preload = preload
        self._stop_event = threading.Event()
        self._config_lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Зарегистрировать клавиши и (опционально) прогреть модель."""
        self._bind_hotkeys()
        self._set_state(IDLE)
        if self._preload:
            self.preload_async()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self.stream.is_active:
                self.stream.cancel()
            if self.recorder.is_recording:
                self.recorder.cancel()
        finally:
            self.hotkeys.unregister_all()
            if self._preload_thread and self._preload_thread.is_alive():
                self._preload_thread.join(timeout=2.0)
        log.info("Приложение остановлено")

    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    def _bind_hotkeys(self) -> None:
        bindings = {"toggle": (self.config.hotkey, self.toggle)}
        result = self.hotkeys.rebind(bindings)
        if not result.get("toggle"):
            self._fail(
                f"Не удалось назначить горячую клавишу «{self.config.hotkey}». "
                "Выберите другое сочетание в настройках."
            )

    # ----------------------------------------------------------------- hotkeys
    def toggle(self) -> None:
        """Одна клавиша: начать запись / остановить и распознать."""
        if self.paused:
            return
        if self.recorder.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> bool:
        if self.paused or self.recorder.is_recording:
            return False
        if self.state == PROCESSING:
            self.ui.on_notify("Голос", "Дождитесь окончания распознавания")
            return False

        try:
            self.recorder.start()
        except AudioError as exc:
            self._fail(str(exc))
            return False

        self._register_cancel_hotkey()
        if self.config.sound_feedback:
            beep("start")
        self._set_state(RECORDING)
        self._start_level_poll()

        if self.config.streaming:
            self.stream.config = self.config
            self.last_text = ""  # каждый сеанс начинается с чистого листа
            self.stream.start()
        return True

    def stop_recording(self) -> None:
        if not self.recorder.is_recording:
            return
        self._unregister_cancel_hotkey()

        if self.config.streaming and self.stream.is_active:
            self._set_state(PROCESSING)
            text = self.stream.stop()
            recording = self.recorder.stop()
            if self.config.sound_feedback:
                beep("stop")
            duration = recording.duration if recording else 0.0
            if text.strip():
                self.last_text = text
                self._append_history(text)
                self._update_stats(text, duration, 0.0)
                if self.config.notifications:
                    self.ui.on_notify("Голос", preview(text))
            elif duration < self.config.min_duration or not recording:
                self.ui.on_notify("Голос", "Слишком короткая запись")
            else:
                self.ui.on_notify(
                    "Голос", "Речь не распознана — проверьте микрофон и модель"
                )
            self._set_state(IDLE)
            return

        recording = self.recorder.stop()
        if self.config.sound_feedback:
            beep("stop")

        if recording is None:
            self._set_state(IDLE)
            return

        if recording.duration < self.config.min_duration:
            self.ui.on_notify("Голос", "Слишком короткая запись — пропускаю")
            self._set_state(IDLE)
            return

        if not recording:
            self.ui.on_notify(
                "Голос",
                "Микрофон молчит (тишина). Проверьте, что он подключён и не "
                "отключён в Windows.",
            )
            self._set_state(IDLE)
            return

        self._set_state(PROCESSING)
        thread = threading.Thread(
            target=self._process, args=(recording,), name="golos-transcribe", daemon=True
        )
        thread.start()

    def cancel_recording(self) -> None:
        if not self.recorder.is_recording:
            return
        self._unregister_cancel_hotkey()
        if self.stream.is_active:
            self.stream.cancel()
        self.recorder.cancel()
        if self.config.sound_feedback:
            beep("stop")
        self._set_state(IDLE)
        self.ui.on_notify("Голос", "Запись отменена")

    def pause(self, value: bool | None = None) -> bool:
        """Включить/выключить паузу (клавиши перестают реагировать)."""
        self.paused = bool(value) if value is not None else not self.paused
        if self.paused:
            if self.recorder.is_recording:
                if self.stream.is_active:
                    self.stream.cancel()
                self.recorder.cancel()
                self._unregister_cancel_hotkey()
            self.hotkeys.unregister_all()
            self._set_state(PAUSED)
        else:
            self._bind_hotkeys()
            self._set_state(IDLE)
        return self.paused

    # --------------------------------------------------------------- processing
    def _process(self, recording: Recording) -> None:
        if not self._work_lock.acquire(blocking=False):
            self.ui.on_notify("Голос", "Предыдущая запись ещё обрабатывается")
            return
        try:
            self.ui.on_notify("Голос", "Распознаю…")
            result = self.transcriber.transcribe(recording.samples, self.config)
            raw = result.text
            text = clean_text(raw, self.config)

            if not text.strip():
                log.info("Речь не распознана")
                self.ui.on_notify("Голос", "Речь не распознана — попробуйте ещё раз")
                self._set_state(IDLE)
                return

            self.last_text = text
            self.last_raw = raw
            self._append_history(text)

            try:
                self.injector.insert(text)
            except InjectionError as exc:
                self.ui.on_error(str(exc))
                self.ui.on_text(text, raw)
                self._update_stats(text, result.audio_duration, result.latency)
                self._set_state(IDLE)
                return

            self.ui.on_text(text, raw)
            # Статистику обновляем после вставки: считаем доставленные диктовки.
            self._update_stats(text, result.audio_duration, result.latency)
            if self.config.notifications:
                self.ui.on_notify("Голос", preview(text))
            self._set_state(IDLE)
        except TranscriptionError as exc:
            self._fail(str(exc))
        except Exception as exc:  # pragma: no cover - неожиданные ошибки
            log.exception("Сбой при обработке записи")
            self._fail(f"Непредвиденная ошибка: {exc}")
        finally:
            self._work_lock.release()

    def _update_stats(self, text: str, duration: float, latency: float) -> None:
        self.stats.sessions += 1
        self.stats.words += len(text.split())
        self.stats.seconds += duration
        self.stats.last_latency = latency
        self.stats.recent.insert(0, text.strip())
        del self.stats.recent[30:]

    def _append_history(self, text: str) -> None:
        if not self.config.history_enabled:
            return
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.paths.history_file, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {text.strip()}\n")
        except OSError as exc:  # pragma: no cover
            log.debug("История недоступна: %s", exc)

    def insert_last(self) -> bool:
        """Вставить последний распознанный текст ещё раз."""
        text = (self.last_text or "").strip()
        if not text:
            return False
        try:
            self.injector.insert(text + (" " if self.config.trailing_space else ""))
            return True
        except InjectionError as exc:
            self._fail(str(exc))
            return False

    # ------------------------------------------------------------------ preload
    def preload_async(self) -> None:
        if self._preload_thread and self._preload_thread.is_alive():
            return
        self._preload_thread = threading.Thread(
            target=self._preload_worker, name="golos-preload", daemon=True
        )
        self._preload_thread.start()

    def _preload_worker(self) -> None:
        self._set_state(LOADING)
        try:
            if not self.transcriber.is_model_downloaded():
                self.ui.on_notify(
                    "Голос",
                    f"Скачиваю модель «{self.config.model}» — это займёт несколько минут "
                    "(интернет нужен только сейчас)",
                )
            started = time.monotonic()
            self.transcriber.preload()
            log.info("Модель загружена за %.1f c", time.monotonic() - started)
            if self.state == LOADING:
                self._set_state(IDLE)
        except TranscriptionError as exc:
            self._fail(str(exc))
        except Exception as exc:  # pragma: no cover
            self._fail(f"Не удалось загрузить модель: {exc}")

    # -------------------------------------------------------------------- config
    def apply_config(self, new_config: Config, persist: bool = True) -> list[str]:
        """Применить настройки. Возвращает список предупреждений для UI."""
        warnings: list[str] = []
        with self._config_lock:
            old = self.config
            new = new_config.copy()
            new.normalize()

            model_changed = (
                old.model != new.model
                or old.device != new.device
                or old.compute_type != new.compute_type
                or old.initial_prompt != new.initial_prompt
                or old.hotwords != new.hotwords
            )
            hotkey_changed = normalize_hotkey(old.hotkey) != normalize_hotkey(new.hotkey)

            self.config = new
            self.recorder.set_config(new)
            self.transcriber.set_config(new)
            self.injector.apply_config(new)
            self.stream.config = new

            if persist:
                try:
                    save_config(new, self.paths.config_file)
                except OSError as exc:
                    warnings.append(f"Не удалось сохранить настройки: {exc}")

            if old.autostart != new.autostart:
                if not set_autostart(new.autostart):
                    warnings.append("Не удалось изменить автозапуск")

            if model_changed and self.transcriber.is_ready:
                self.transcriber.unload()
                if self._preload:
                    self.preload_async()

        if hotkey_changed:
            self._bind_hotkeys()
            if normalize_hotkey(self.config.hotkey) not in self.hotkeys.registered.values():
                warnings.append(
                    f"Клавиша «{self.config.hotkey}» не назначена — возможно, занята"
                )
        return warnings

    def status(self) -> dict:
        return {
            "state": self.state,
            "state_label": STATE_LABELS.get(self.state, self.state),
            "hotkey": self.config.hotkey,
            "cancel_hotkey": self.config.cancel_hotkey,
            "model": self.config.model,
            "model_ready": self.transcriber.is_ready,
            "language": self.config.language,
            "paused": self.paused,
            "recording": self.recorder.is_recording,
            "sessions": self.stats.sessions,
            "words": self.stats.words,
            "minutes": round(self.stats.seconds / 60.0, 1),
            "last_text": self.last_text,
            "last_error": self.last_error,
            "log_file": str(self.paths.log_file),
            "config_file": str(self.paths.config_file),
            "history_file": str(self.paths.history_file),
        }

    # ------------------------------------------------------------------ helpers
    def _register_cancel_hotkey(self) -> None:
        combo = normalize_hotkey(self.config.cancel_hotkey)
        if not combo or combo in self.hotkeys.registered.values():
            return
        self.hotkeys.register("cancel", combo, self.cancel_recording)

    def _unregister_cancel_hotkey(self) -> None:
        self.hotkeys.unregister("cancel")

    def _start_level_poll(self) -> None:
        def poll() -> None:
            while self.recorder.is_recording and not self._stop_event.is_set():
                try:
                    self.ui.on_level(self.recorder.peak)
                except Exception:  # pragma: no cover
                    pass
                time.sleep(0.05)

        threading.Thread(target=poll, name="golos-level", daemon=True).start()

    # ------------------------------------------------------- колбэки потока
    def _on_stream_text(self, text: str) -> None:
        self.last_text += text
        self.ui.on_text(text, text)

    def _on_stream_partial(self, text: str) -> None:
        handler = getattr(self.ui, "on_partial", None)
        if handler:
            handler(text)

    def _on_stream_state(self, state: str, message: str = "") -> None:
        if state == "loading":
            self.ui.on_state(LOADING, message or "Загружаю модель…")

    def _on_stream_error(self, message: str) -> None:
        self._fail(message)

    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        if state != ERROR:
            self.last_error = ""
        try:
            self.ui.on_state(state, message or STATE_LABELS.get(state, state))
        except Exception:  # pragma: no cover
            log.exception("Ошибка в интерфейсе")

    def _fail(self, message: str) -> None:
        self.last_error = message
        log.error("%s", message)
        self.state = ERROR
        try:
            self.ui.on_state(ERROR, message)
            self.ui.on_error(message)
        except Exception:  # pragma: no cover
            pass
        if self.config.sound_feedback:
            beep("error")

