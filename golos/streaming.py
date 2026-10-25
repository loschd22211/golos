"""Потоковая диктовка: текст появляется у курсора по мере речи.

Идея: пока идёт запись, каждые ``stream_step`` секунд берём накопленный звук,
распознаём «хвост» и сразу вставляем в активное окно те куски, которые уже
закончились (последние ``hold_back`` секунд придерживаем, чтобы не разрезать
слово). Так пользователь видит текст почти сразу, а не после остановки.

Ограничения честные: это не фонемный стриминг Whisper, а скользящее окно,
поэтому на границах кусков возможны мелкие шероховатости пунктуации.
"""

from __future__ import annotations

import logging
import threading

from .audio import MicrophoneRecorder
from .config import Config
from .inject import InjectionError, TextInjector
from .textproc import clean_text
from .transcribe import TranscriptionError, WhisperTranscriber

log = logging.getLogger(__name__)

STATUS_LOADING = "loading"
STATUS_LISTENING = "listening"
STATUS_TEXT = "text"
STATUS_ERROR = "error"
STATUS_DONE = "done"


class StreamingDictator:
    """Скользящее распознавание + вставка по кускам."""

    def __init__(
        self,
        recorder: MicrophoneRecorder,
        transcriber: WhisperTranscriber,
        injector: TextInjector,
        config: Config,
        on_text=None,
        on_state=None,
        on_error=None,
        on_partial=None,
        focus_restore=None,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.injector = injector
        self.config = config
        self.on_text = on_text or (lambda text: None)
        self.on_state = on_state or (lambda state, message="": None)
        self.on_error = on_error or (lambda message: None)
        self.on_partial = on_partial or (lambda text: None)
        # Функция, возвращающая фокус нужному окну перед вставкой.
        self.focus_restore = focus_restore or (lambda: None)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = False
        self._committed_samples = 0
        self._committed_text = ""
        self._lock = threading.Lock()
        self._first_chunk = True
        self._last_error = ""
        self._cancelled = False

    # ------------------------------------------------------------------ state
    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def committed_text(self) -> str:
        with self._lock:
            return self._committed_text

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._stop.clear()
        self._committed_samples = 0
        self._committed_text = ""
        self._first_chunk = True
        self._last_error = ""
        self._cancelled = False
        self._thread = threading.Thread(target=self._run, name="golos-stream", daemon=True)
        self._thread.start()

    def stop(self) -> str:
        """Завершить: дораспознать остаток и вставить его."""
        if not self._active:
            return self._committed_text
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=20)
        self._active = False
        return self.committed_text

    def cancel(self) -> None:
        self._cancelled = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._active = False

    # ------------------------------------------------------------------- loop
    def _run(self) -> None:
        cfg = self.config
        step = max(0.2, float(getattr(cfg, "stream_step", 0.45)))
        min_chunk = max(0.6, float(getattr(cfg, "min_chunk_seconds", 1.2)))

        try:
            while not self._stop.is_set():
                if self._stop.wait(step):
                    break
                audio = self.recorder.snapshot()
                if audio is None:
                    continue
                tail = audio[self._committed_samples :]
                if tail.size < int(cfg.sample_rate * min_chunk):
                    continue
                self._process_tail(tail, final=False)

            if self._cancelled:
                return

            # финальный проход по остатку
            audio = self.recorder.snapshot()
            if audio is not None and audio.size > self._committed_samples:
                tail = audio[self._committed_samples :]
                if tail.size >= int(cfg.sample_rate * 0.25):
                    self._process_tail(tail, final=True)
        except TranscriptionError as exc:
            self._fail(str(exc))
        except InjectionError as exc:
            self._fail(str(exc))
        except Exception as exc:  # pragma: no cover - неожиданное
            log.exception("Сбой потокового распознавания")
            self._fail(f"Сбой распознавания: {exc}")
        finally:
            self._active = False

    def _process_tail(self, tail, final: bool) -> None:
        cfg = self.config
        rate = int(cfg.sample_rate or 16000)
        hold_back = 0.35 if final else max(0.2, float(getattr(cfg, "hold_back_seconds", 0.5)))
        duration = tail.size / float(rate)

        if self._first_chunk:
            self.on_state(STATUS_LOADING, "Загружаю модель…")
            self._first_chunk = False

        segments = self.transcriber.segments(
            tail,
            cfg,
            initial_prompt=self._context_prompt(),
            beam_size=1 if not final else None,
        )
        if not segments:
            if final and duration > 0.5:
                log.info("Поток: остаток без текста (%.1f c)", duration)
            return

        limit = duration - hold_back
        ready = [s for s in segments if float(s.get("end", 0.0)) <= limit]
        if not ready:
            # ничего не закончилось — показываем хвост как «промежуточный» текст
            if not final:
                partial = " ".join(s.get("text", "") for s in segments).strip()
                if partial:
                    self.on_partial(partial)
                return
            ready = segments
        elif not final:
            partial = " ".join(s.get("text", "") for s in segments[len(ready) :]).strip()
            if partial:
                self.on_partial(partial)

        raw = " ".join(s.get("text", "") for s in ready).strip()
        if not raw:
            self._commit(rate, float(ready[-1].get("end", duration)), "")
            return

        text = clean_text(raw, cfg)
        if text.strip():
            self.on_text(text)
            try:
                self.focus_restore()
                self.injector.insert(text)
            except InjectionError as exc:
                self._fail(str(exc))
                return
            with self._lock:
                self._committed_text += text
        self._commit(rate, float(ready[-1].get("end", duration)), text)

    def _commit(self, rate: int, end_seconds: float, text: str) -> None:
        advance = int(max(0.0, end_seconds) * rate)
        if advance <= 0:
            return
        self._committed_samples += advance
        log.debug(
            "Поток: зафиксировано %.2f c (текст %r), всего %.1f c",
            advance / rate,
            text[:40],
            self._committed_samples / rate,
        )

    def _context_prompt(self) -> str:
        """Контекст для модели: подсказка + последние слова, чтобы не терять смысл."""
        base = (self.config.initial_prompt or "").strip()
        tail = self.committed_text.strip()[-120:]
        if tail:
            return f"{base} {tail}".strip()
        return base

    def _fail(self, message: str) -> None:
        self._last_error = message
        log.error("Поток: %s", message)
        self.on_state(STATUS_ERROR, message)
        self.on_error(message)
