"""Потоковая диктовка: текст появляется у курсора по мере речи.

Как это работает:

* пока идёт запись, каждые ``stream_step`` секунд берём звук, накопленный
  **после последней вставки**, и распознаём его;
* вставляем ту часть, которая уже точно сказана: слова, закончившиеся до
  ``длина_куска − hold_back_seconds`` (небольшой запас, чтобы не разрезать
  слово на середине);
* если паузы нет и границу найти не удаётся, но кусок уже длиннее
  ``force_commit_seconds`` — вставляем всё, кроме последнего слова. Иначе при
  непрерывной речи Whisper возвращал один сегмент до самого конца записи,
  и текст не появлялся вообще до остановки;
* на остановке дораспознаём остаток и дописываем его.

Позиция в аудио запоминается в сэмплах: всё, что левее, уже вставлено, поэтому
один и тот же текст не появляется дважды.
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
        self._cancelled = False
        self._committed_samples = 0
        self._committed_text = ""
        self._lock = threading.Lock()
        self._first_chunk = True
        self._last_error = ""
        self.chunks_inserted = 0

    # ------------------------------------------------------------------ state
    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def committed_text(self) -> str:
        with self._lock:
            return self._committed_text

    @property
    def last_error(self) -> str:
        return self._last_error

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._cancelled = False
        self._stop.clear()
        self._committed_samples = 0
        self._committed_text = ""
        self.chunks_inserted = 0
        self._first_chunk = True
        self._last_error = ""
        self._thread = threading.Thread(target=self._run, name="golos-stream", daemon=True)
        self._thread.start()
        log.info(
            "Поток: старт (шаг %.2f c, минимум %.2f c, придержка %.2f c)",
            self.config.stream_step,
            self.config.min_chunk_seconds,
            self.config.hold_back_seconds,
        )

    def stop(self) -> str:
        """Завершить: дораспознать остаток и вставить его."""
        if not self._active:
            return self.committed_text
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=25)
        self._active = False
        log.info(
            "Поток: стоп, вставлено кусков %d, символов %d",
            self.chunks_inserted,
            len(self.committed_text),
        )
        return self.committed_text

    def cancel(self) -> None:
        self._cancelled = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._active = False
        log.info("Поток: отменено пользователем")

    # ------------------------------------------------------------------- loop
    def _run(self) -> None:
        cfg = self.config
        step = max(0.15, float(getattr(cfg, "stream_step", 0.35)))
        min_chunk = max(0.4, float(getattr(cfg, "min_chunk_seconds", 0.8)))

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

    # -------------------------------------------------------------- обработка
    def _process_tail(self, tail, final: bool) -> None:
        cfg = self.config
        rate = int(cfg.sample_rate or 16000)
        duration = tail.size / float(rate)

        if self._first_chunk:
            self.on_state(STATUS_LOADING, "Слушаю…")
            self._first_chunk = False

        hold_back = 0.15 if final else max(0.1, float(cfg.hold_back_seconds))
        force_commit = float(getattr(cfg, "force_commit_seconds", 2.5))

        try:
            segments = self.transcriber.segments(
                tail,
                cfg,
                initial_prompt=self._context_prompt(),
                beam_size=1 if not final else None,
            )
        except TranscriptionError:
            raise
        if not segments:
            log.info("Поток: %.1f c аудио — модель не нашла речи", duration)
            if final:
                self._commit_all(rate, tail.size)
            return

        items = _items_from_segments(segments)
        if not items:
            log.info("Поток: %.1f c аудио — пустые сегменты", duration)
            return

        limit = duration - hold_back
        ready = [item for item in items if item["end"] <= limit]
        forced = False

        if not ready and not final:
            if duration >= force_commit and len(items) > 1:
                # Паузы нет, но говорить дальше без текста нельзя: забираем всё,
                # кроме последнего слова (оно может быть недоговорено).
                ready = items[:-1]
                forced = True
            elif duration >= force_commit * 2:
                # Одно длинное слово/сегмент — вставляем и его, иначе текст
                # не появится вообще никогда.
                ready = items
                forced = True
            else:
                partial = _join(item["text"] for item in items)
                if partial:
                    self.on_partial(partial)
                log.info(
                    "Поток: %.1f c аудио — ждём паузу, хвост %r",
                    duration,
                    partial[-40:],
                )
                return

        if final and not ready:
            ready = items

        raw = _join(item["text"] for item in ready)
        if not raw:
            return

        text = clean_text(raw, cfg)
        end = ready[-1]["end"]
        log.info(
            "Поток: вставляю %.2f c текста (%s): %r",
            end,
            "финал" if final else ("принудительно" if forced else "по паузе"),
            text[:80],
        )

        if text.strip():
            try:
                self.focus_restore()
                self.injector.insert(text)
            except InjectionError as exc:
                self._fail(str(exc))
                return
            with self._lock:
                self._committed_text += text
            self.chunks_inserted += 1
            self.on_text(text)

        self._commit(rate, end)

        rest = [item for item in items if item["end"] > end]
        if rest and not final:
            self.on_partial(_join(item["text"] for item in rest))
        elif not rest:
            self.on_partial("")

    def _commit(self, rate: int, end_seconds: float) -> None:
        advance = int(max(0.0, end_seconds) * rate)
        if advance <= 0:
            return
        self._committed_samples += advance
        log.debug(
            "Поток: зафиксировано %.2f c, всего %.1f c",
            advance / rate,
            self._committed_samples / rate,
        )

    def _commit_all(self, rate: int, samples: int) -> None:
        self._committed_samples = max(self._committed_samples, samples)

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


# --------------------------------------------------------------------- helpers
def _items_from_segments(segments) -> list[dict]:
    """Разложить сегменты на минимальные единицы вставки — слова, если есть."""
    items: list[dict] = []
    for segment in segments:
        words = segment.get("words") or []
        if words:
            for word in words:
                text = (word.get("word") or "").strip()
                if not text:
                    continue
                items.append({"text": text, "end": float(word.get("end", 0.0) or 0.0)})
        else:
            text = (segment.get("text") or "").strip()
            if text:
                items.append({"text": text, "end": float(segment.get("end", 0.0) or 0.0)})
    return items


def _join(parts) -> str:
    """Склеить слова/сегменты в строку без двойных пробелов."""
    text = " ".join(str(part).strip() for part in parts if str(part).strip())
    return " ".join(text.split())
