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
from .textproc import clean_text, looks_like_hallucination
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
        self._fatal = False          # вставка невозможна: сеанс прекращаем
        self._last_chunk = ""        # предыдущий вставленный кусок
        self._same_chunk = 0         # сколько раз подряд он повторился
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
        self._fatal = False
        self._last_chunk = ""
        self._same_chunk = 0
        begin = getattr(self.transcriber, "begin_session", None)
        if callable(begin):  # язык «auto» фиксируется один раз на сеанс
            begin()
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
            while not self._stop.is_set() and not self._fatal:
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

        # Тишину и шум в модель не отправляем: процессор не тратится, а
        # «фантомные» фразы (титры из обучающих данных) не появляются.
        threshold = float(getattr(cfg, "silence_threshold", 0.004) or 0.0)
        if threshold > 0 and _rms(tail) < threshold:
            self._commit_all(rate, tail.size)
            log.debug("Поток: %.1f c тишины — пропущено", duration)
            return

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

        # Проверяем ДО чистки: «Аллоуууууу…» и «Приём, приём, приём» после
        # схлопывания выглядят прилично, но это всё равно зацикливание модели.
        raw_loop = looks_like_hallucination(raw)
        text = clean_text(raw, cfg)

        if text.strip() and (raw_loop or looks_like_hallucination(text)):
            log.info("Поток: отброшена выдумка модели %r", (text or raw)[:60])
            self._commit(rate, ready[-1]["end"])
            return

        # Whisper иногда зацикливается и выдаёт один и тот же кусок снова и снова
        # (тишина, шум, обрывок фразы). Третий одинаковый подряд — почти всегда
        # галлюцинация, и вставлять его в текст нельзя.
        key = text.strip().lower()
        if key and key == self._last_chunk:
            self._same_chunk += 1
        else:
            self._last_chunk = key
            self._same_chunk = 1
        if self._same_chunk > 2:
            log.info(
                "Поток: пропускаю повтор куска %r (%d-й раз подряд)",
                text[:40],
                self._same_chunk,
            )
            self._commit(rate, ready[-1]["end"])
            return

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
                # Нет прав / ввод заблокирован: повторять каждые 0.35 с
                # бессмысленно — иначе лавина одинаковых ошибок.
                self._fatal = True
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
        """Подсказка для модели (словарь терминов), без обратной связи.

        Раньше сюда подмешивался уже вставленный текст. Модель начинала его
        повторять: в журнале пользователя одна вставка шла за другой с тем же
        «Понимаешь.» — классический цикл самоподтверждения.
        """
        return (self.config.initial_prompt or "").strip()

    def _fail(self, message: str) -> None:
        self._last_error = message
        log.error("Поток: %s", message)
        self.on_state(STATUS_ERROR, message)
        self.on_error(message)


# --------------------------------------------------------------------- helpers
def _rms(samples) -> float:
    """Среднеквадратичная громкость куска (0 — тишина)."""
    try:
        # numpy приходит вместе со sounddevice — отдельный импорт не нужен,
        # работаем с тем, что уже есть.
        array = samples
        if hasattr(array, "astype"):
            array = array.astype("float32", copy=False)
        else:  # pragma: no cover - на всякий случай
            return 0.0
        if array.size == 0:
            return 0.0
        return float((array * array).mean() ** 0.5)
    except Exception:  # pragma: no cover
        return 1.0  # не мешаем работе, если numpy недоступен


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
