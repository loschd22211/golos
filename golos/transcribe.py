"""Распознавание речи офлайн через faster-whisper (CTranslate2).

Модель многоязычная и определяет язык сама (``language="auto"``), поэтому
русские и английские слова в одной фразе распознаются как есть: кириллица
остаётся кириллицей, английские термины — латиницей.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Ошибка распознавания; текст показывается пользователю."""


@dataclass
class TranscriptionResult:
    text: str
    language: str = ""
    audio_duration: float = 0.0
    latency: float = 0.0
    segments: int = 0


class WhisperTranscriber:
    """Ленивая загрузка модели + потокобезопасное распознавание."""

    def __init__(self, config: Config, models_dir: Path | None = None):
        self.config = config
        self.models_dir = Path(models_dir) if models_dir else None
        self._model = None
        self._model_key: tuple | None = None
        self._lock = threading.RLock()
        self._load_lock = threading.Lock()

    # ------------------------------------------------------------------ status
    @property
    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def model_key(self) -> tuple | None:
        return self._model_key

    def set_config(self, config: Config) -> None:
        self.config = config

    def is_model_downloaded(self, model: str | None = None) -> bool:
        """Есть ли модель локально (чтобы предупредить о скачивании)."""
        if not self.models_dir or not self.models_dir.exists():
            return False
        target = (model or self.config.model).lower()
        try:
            return any(
                target in entry.name.lower() for entry in self.models_dir.iterdir()
            )
        except OSError:
            return False

    # ------------------------------------------------------------------- model
    def preload(self) -> None:
        """Загрузить (и при необходимости скачать) модель."""
        self._ensure_model()

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._model_key = None

    def _ensure_model(self):
        cfg = self.config
        device = resolve_device(cfg.device)
        compute_type = resolve_compute_type(device, cfg.compute_type)
        key = (cfg.model, device, compute_type)

        with self._lock:
            if self._model is not None and self._model_key == key:
                return self._model

        with self._load_lock:
            with self._lock:
                if self._model is not None and self._model_key == key:
                    return self._model

            try:
                from faster_whisper import WhisperModel
            except Exception as exc:  # pragma: no cover
                raise TranscriptionError(
                    "Не установлен faster-whisper. Запустите install.bat "
                    "или выполните: pip install faster-whisper\n"
                    f"Детали: {exc}"
                ) from exc

            kwargs = {
                "device": device,
                "compute_type": compute_type,
                "cpu_threads": max(1, (os.cpu_count() or 4) - 1),
                "num_workers": 1,
            }
            if self.models_dir:
                self.models_dir.mkdir(parents=True, exist_ok=True)
                kwargs["download_root"] = str(self.models_dir)

            log.info(
                "Загрузка модели %s (device=%s, compute_type=%s)",
                cfg.model,
                device,
                compute_type,
            )
            try:
                model = WhisperModel(cfg.model, **kwargs)
            except Exception as exc:
                # Видеокарта/тип вычислений могут не подойти — откатываемся на CPU int8.
                if device != "cpu" or compute_type != "int8":
                    log.warning("Не удалось загрузить модель (%s), пробую CPU/int8", exc)
                    try:
                        model = WhisperModel(
                            cfg.model,
                            device="cpu",
                            compute_type="int8",
                            cpu_threads=kwargs["cpu_threads"],
                            num_workers=1,
                            **({"download_root": kwargs["download_root"]} if "download_root" in kwargs else {}),
                        )
                        device, compute_type = "cpu", "int8"
                    except Exception as exc2:
                        raise _load_error(cfg.model, exc2) from exc2
                else:
                    raise _load_error(cfg.model, exc) from exc

            with self._lock:
                self._model = model
                self._model_key = (cfg.model, device, compute_type)
            log.info("Модель готова: %s/%s", device, compute_type)
            return model

    def segments(
        self,
        audio,
        config: Config | None = None,
        initial_prompt: str | None = None,
        beam_size: int | None = None,
        want_words: bool = True,
    ) -> list[dict]:
        """Распознать кусок аудио и вернуть сегменты с таймингами.

        Нужно потоковому режиму: по времени окончания сегмента решаем, что уже
        можно вставлять в поле ввода, а что ещё рано (слово может недоговориться).
        """
        cfg = config or self.config
        if audio is None or getattr(audio, "size", 0) == 0:
            return []

        model = self._ensure_model()
        language = None if cfg.language == "auto" else cfg.language
        params = {
            "task": "transcribe",
            "language": language,
            "beam_size": int(beam_size or 1),
            "vad_filter": False,
            "condition_on_previous_text": False,
            "without_timestamps": False,
            # Тайминги по словам нужны, чтобы вставлять текст ровно по границе
            # слова, а не резать его посреди «приве…».
            "word_timestamps": bool(want_words),
            "initial_prompt": initial_prompt or cfg.initial_prompt or None,
            "hotwords": cfg.hotwords or None,
        }

        with self._lock:
            pieces, _info = model.transcribe(audio, **params)
            result = []
            for segment in pieces:
                piece = (segment.text or "").strip()
                if not piece:
                    continue
                words = []
                for word in getattr(segment, "words", None) or []:
                    text = (getattr(word, "word", "") or "").strip()
                    if not text:
                        continue
                    words.append(
                        {
                            "word": text,
                            "start": float(getattr(word, "start", 0.0) or 0.0),
                            "end": float(getattr(word, "end", 0.0) or 0.0),
                        }
                    )
                result.append(
                    {
                        "text": piece,
                        "start": float(getattr(segment, "start", 0.0) or 0.0),
                        "end": float(getattr(segment, "end", 0.0) or 0.0),
                        "words": words,
                    }
                )
        return result

    # -------------------------------------------------------------- transcribe
    def transcribe(self, audio, config: Config | None = None) -> TranscriptionResult:
        cfg = config or self.config
        self.set_config(cfg)

        if audio is None or getattr(audio, "size", 0) == 0:
            return TranscriptionResult(text="")

        started = time.monotonic()
        model = self._ensure_model()

        language = None if cfg.language == "auto" else cfg.language
        params = {
            "task": "transcribe",
            "language": language,
            "beam_size": int(cfg.beam_size),
            "vad_filter": bool(cfg.vad_filter),
            "vad_parameters": {"min_silence_duration_ms": 500},
            "condition_on_previous_text": bool(cfg.condition_on_previous_text),
            "without_timestamps": True,
            "word_timestamps": False,
            "initial_prompt": cfg.initial_prompt or None,
            "hotwords": cfg.hotwords or None,
        }

        try:
            with self._lock:
                segments, info = model.transcribe(audio, **params)
                texts: list[str] = []
                detected = ""
                for segment in segments:
                    piece = (segment.text or "").strip()
                    if piece:
                        texts.append(piece)
                    if not detected:
                        detected = getattr(segment, "language", "") or ""
        except Exception as exc:
            raise TranscriptionError(
                "Ошибка распознавания речи. Попробуйте модель поменьше "
                f"или перезапустите приложение.\nДетали: {exc}"
            ) from exc

        latency = time.monotonic() - started
        text = " ".join(texts).strip()
        duration = float(getattr(audio, "size", 0)) / float(cfg.sample_rate or 16000)
        result = TranscriptionResult(
            text=text,
            language=detected or (getattr(info, "language", "") or ""),
            audio_duration=duration,
            latency=latency,
            segments=len(texts),
        )
        log.info(
            "Распознано за %.2f c (аудио %.2f c, язык=%s): %r",
            latency,
            duration,
            result.language or "?",
            text[:200],
        )
        return result


# --------------------------------------------------------------------- helpers
def resolve_device(preferred: str) -> str:
    preferred = (preferred or "auto").lower()
    if preferred in ("cpu", "cuda"):
        return preferred
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def resolve_compute_type(device: str, preferred: str) -> str:
    preferred = (preferred or "auto").lower()
    if preferred != "auto":
        return preferred
    return "float16" if device == "cuda" else "int8"


def _load_error(model: str, exc: Exception) -> TranscriptionError:
    details = str(exc)
    hint = ""
    if "connect" in details.lower() or "http" in details.lower() or "download" in details.lower():
        hint = (
            "Похоже, не удалось скачать модель. Нужен интернет при первом запуске "
            "(модель скачивается один раз).\n"
        )
    return TranscriptionError(
        f"Не удалось загрузить модель «{model}».\n{hint}Детали: {details}"
    )
