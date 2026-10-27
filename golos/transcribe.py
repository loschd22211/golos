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

from .cuda import enable_dlls, pip_hint
from .config import Config
from .textproc import is_short_phantom, looks_like_hallucination

log = logging.getLogger(__name__)

# Смешанная речь «русский + английский»: язык определяется по каждому куску.
MIXED_LANGUAGE = "ru+en"
MIXED_FALLBACK = "ru"       # не разобрали — пишем, как слышится по-русски
MIXED_MIN_SECONDS = 0.6      # короче повторять бессмысленно


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
        self._model_key: tuple | None = None   # что просили загрузить
        self.active_model = ""       # какая модель реально загружена
        self.active_device = ""      # на чём реально работаем: cpu / cuda
        self.active_compute = ""
        self.fallback_reason = ""    # почему не взлетела видеокарта
        self._session_language = ""  # язык, зафиксированный на текущий сеанс
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

    def begin_session(self) -> None:
        """Начать новый сеанс диктовки.

        При ``language="auto"`` язык определяется один раз на сеанс и дальше не
        переключается: на кусках по 0.35–1 с автоопределение скакало между
        русским и английским, и половина текста выходила «Fiking», «Thank you».
        """
        self._session_language = ""

    def _resolve_language(self, cfg: Config) -> str | None:
        mode = (getattr(cfg, "language", "") or "").strip().lower()
        if mode == MIXED_LANGUAGE:
            # Ничего не фиксируем: в одной фразе могут быть и русские слова, и
            # английские термины, а «замок» на первом языке испортил бы вторую
            # половину записи.
            return None
        if mode != "auto":
            return mode
        return self._session_language or None

    def _russian_retry_needed(self, cfg: Config, keep: str) -> bool:
        """Стоит ли переспросить кусок по-русски (смешанный режим).

        Так выполняется обещание «если не понял — пиши, как слышится
        по-русски»: автоопределение оставило пустоту — не оставляем её в тексте.
        """
        mode = (getattr(cfg, "language", "") or "").strip().lower()
        if mode != MIXED_LANGUAGE:
            return False
        return not (keep or "").strip()

    def is_model_downloaded(self, model: str | None = None) -> bool:
        """Есть ли модель локально (чтобы предупредить о скачивании)."""
        if not self.models_dir or not self.models_dir.exists():
            return False
        # «auto» = medium на GPU или small на CPU: годится любая из скачанных.
        wanted = model or self.config.model
        target = "small" if wanted == "auto" else resolve_model(wanted, "cpu").lower()
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
            self.active_model = ""

    def _ensure_model(self):
        cfg = self.config
        device = resolve_device(cfg.device)
        compute_type = resolve_compute_type(device, cfg.compute_type)
        # Ключ — то, что просили. После удачной загрузки он же и сохраняется,
        # даже если пришлось откатиться на другую модель/устройство: иначе
        # неудачный кандидат заставлял бы перезагружать модель на каждом куске.
        key = (resolve_model(cfg.model, device), device, compute_type)

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

            candidates = load_candidates(cfg.model, device, compute_type)
            if len(candidates) > 1:
                log.info(
                    "План загрузки: %s",
                    ", ".join(f"{m}/{d}/{c}" for m, d, c in candidates),
                )

            cpu_threads = max(1, (os.cpu_count() or 4) - 1)
            model = None
            last_error: Exception | None = None
            for name, dev, compute in candidates:
                kwargs = {
                    "device": dev,
                    "compute_type": compute,
                    "cpu_threads": cpu_threads,
                    "num_workers": 1,
                }
                if self.models_dir:
                    self.models_dir.mkdir(parents=True, exist_ok=True)
                    kwargs["download_root"] = str(self.models_dir)
                try:
                    # Windows: cuBLAS/cuDNN из pip-пакетов, иначе CUDA не поднимется.
                    if dev == "cuda":
                        enable_dlls()
                    log.info("Загрузка модели %s (device=%s, compute_type=%s)", name, dev, compute)
                    model = WhisperModel(name, **kwargs)
                except Exception as exc:
                    last_error = exc
                    log.warning("Модель %s/%s/%s не загрузилась: %s", name, dev, compute, exc)
                    if dev == "cuda":
                        self.fallback_reason = _cuda_hint(exc)
                    continue
                self.active_model = name
                device, compute_type = dev, compute
                break

            if model is None:
                raise _load_error(resolve_model(cfg.model, device), last_error or Exception("—"))

            with self._lock:
                self._model = model
                self._model_key = key
                self.active_model = self.active_model or candidates[0][0]
                self.active_device = device
                self.active_compute = compute_type
            log.info(
                "Модель готова: %s (%s/%s)", self.active_model, device, compute_type
            )
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
        duration = float(getattr(audio, "size", 0)) / float(cfg.sample_rate or 16000)
        language = self._resolve_language(cfg)
        params = {
            "task": "transcribe",
            "language": language,
            "beam_size": int(beam_size or 1),
            # VAD выкидывает тишину и шум: без него модель «додумывает» фразы
            # вроде «Редактор субтитров А.Семкин Корректор В.Сухиашвили».
            "vad_filter": bool(cfg.vad_filter),
            # Порог VAD выше стандартного (0.5): музыка и гул на 0.6 уже не
            # проходят как «речь», а тихая человеческая речь проходит.
            "vad_parameters": {
                "min_silence_duration_ms": 300,
                "threshold": 0.6,
                "min_speech_duration_ms": 200,
            },
            # Не отдавать сегменты, где модель почти уверена, что речи нет.
            "no_speech_threshold": 0.7,
            "log_prob_threshold": -1.0,
            "compression_ratio_threshold": 2.4,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            # Пауза в середине куска, где модель «додумывает» фразу, — самый
            # частый источник выдумок. Работает только вместе с таймингами
            # слов, поэтому включено именно здесь.
            "hallucination_silence_threshold": 2.0,
            "without_timestamps": False,
            # Тайминги по словам нужны, чтобы вставлять текст ровно по границе
            # слова, а не резать его посреди «приве…».
            "word_timestamps": bool(want_words),
            "initial_prompt": initial_prompt or cfg.initial_prompt or None,
            "hotwords": cfg.hotwords or None,
        }

        with self._lock:
            pieces, info = self._run_model(model, audio, params, cfg, duration)
            self._lock_session_language(cfg, info, duration)
            result = []
            for segment in pieces:
                piece = (segment.text or "").strip()
                if not piece:
                    continue
                if _is_noise_segment(segment, piece):
                    log.debug("Поток: отброшен сегмент-выдумка %r", piece[:60])
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
                            # Уверенность модели в конкретном слове: по ней видно
                            # «додуманные» слова, которых человек не говорил.
                            "probability": float(getattr(word, "probability", 1.0) or 0.0),
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

    def _lock_session_language(self, cfg: Config, info, duration: float) -> None:
        """Запомнить язык сеанса, если он определился уверенно."""
        if cfg.language != "auto" or self._session_language:
            return
        detected = (getattr(info, "language", "") or "").strip()
        try:
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        except (TypeError, ValueError):  # pragma: no cover
            probability = 0.0
        if detected and duration >= 2.0 and probability >= 0.7:
            self._session_language = detected
            log.info("Язык сеанса: %s (%.2f)", detected, probability)

    def _run_model(self, model, audio, params: dict, cfg: Config, duration: float):
        """Распознать кусок; в смешанном режиме — с повтором по-русски."""
        pieces, info = model.transcribe(audio, **params)
        pieces = list(pieces)
        keep = " ".join((getattr(item, "text", "") or "") for item in pieces)
        if duration < MIXED_MIN_SECONDS or not self._russian_retry_needed(cfg, keep):
            return pieces, info
        log.info(
            "Смешанный режим: пусто после автоопределения (язык=%s) — повторяю по-русски",
            getattr(info, "language", "") or "?",
        )
        retry_params = dict(params)
        retry_params["language"] = MIXED_FALLBACK
        retry_pieces, retry_info = model.transcribe(audio, **retry_params)
        retry_pieces = list(retry_pieces)
        if retry_pieces:
            return retry_pieces, retry_info
        return pieces, info

    # -------------------------------------------------------------- transcribe
    def transcribe(self, audio, config: Config | None = None) -> TranscriptionResult:
        cfg = config or self.config
        self.set_config(cfg)

        if audio is None or getattr(audio, "size", 0) == 0:
            return TranscriptionResult(text="")

        started = time.monotonic()
        model = self._ensure_model()

        language = self._resolve_language(cfg)
        params = {
            "task": "transcribe",
            "language": language,
            "beam_size": int(cfg.beam_size),
            "vad_filter": bool(cfg.vad_filter),
            "vad_parameters": {
                "min_silence_duration_ms": 500,
                "threshold": 0.6,
                "min_speech_duration_ms": 200,
            },
            "no_speech_threshold": 0.7,
            "log_prob_threshold": -1.0,
            "compression_ratio_threshold": 2.4,
            "condition_on_previous_text": bool(cfg.condition_on_previous_text),
            "without_timestamps": True,
            "word_timestamps": False,
            "initial_prompt": cfg.initial_prompt or None,
            "hotwords": cfg.hotwords or None,
        }

        duration = float(getattr(audio, "size", 0)) / float(cfg.sample_rate or 16000)
        try:
            with self._lock:
                segments, info = self._run_model(model, audio, params, cfg, duration)
                texts: list[str] = []
                detected = ""
                for segment in segments:
                    piece = (segment.text or "").strip()
                    if piece and _is_noise_segment(segment, piece):
                        log.debug("Отброшен сегмент-выдумка %r", piece[:60])
                        continue
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


def resolve_model(preferred: str, device: str) -> str:
    """Какую модель реально брать.

    ``auto`` — самый разумный выбор «из коробки»: на видеокарте ``medium``
    (русский он знает заметно лучше, а GPU его тянет), на процессоре ``small``
    (иначе ожидание текста станет невыносимым).
    """
    name = (preferred or "auto").strip().lower()
    if name == "auto":
        return "medium" if device == "cuda" else "small"
    return name


def load_candidates(preferred: str, device: str, compute_type: str) -> list[tuple[str, str, str]]:
    """Порядок попыток загрузки: от лучшего качества к самому совместимому."""
    model = resolve_model(preferred, device)
    attempts: list[tuple[str, str, str]] = [(model, device, compute_type)]
    if device == "cuda":
        if compute_type != "int8_float16":
            attempts.append((model, "cuda", "int8_float16"))
        if model not in ("small", "base", "tiny"):
            # Не хватило видеопамяти на medium — берём small, но всё ещё на GPU.
            attempts.append(("small", "cuda", compute_type))
    attempts.append(("small", "cpu", "int8"))
    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for item in attempts:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def resolve_compute_type(device: str, preferred: str) -> str:
    preferred = (preferred or "auto").lower()
    if preferred != "auto":
        return preferred
    return "float16" if device == "cuda" else "int8"


def _is_noise_segment(segment, text: str) -> bool:
    """Сегмент — «выдумка» модели: тишина, шум, музыка или титры из обучения."""
    if looks_like_hallucination(text) or is_short_phantom(text):
        return True
    try:
        no_speech = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
        avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
    except (TypeError, ValueError):  # pragma: no cover
        return False
    # Модель «почти уверена», что речи нет, и при этом не уверена в тексте.
    # Порог мягче прежнего (0.6/-0.8): на музыке и шуме именно такие сегменты
    # и превращались в «Спокойная музыка», «Поп».
    if no_speech > 0.45 and avg_logprob < -0.6:
        return True
    # Совсем неуверенное распознавание короткого куска — тоже выдумка.
    return no_speech > 0.3 and avg_logprob < -1.1


def _cuda_hint(exc: Exception) -> str:
    """Человеческое объяснение, почему CUDA не завелась."""
    text = str(exc).lower()
    if "cublas" in text or "cudnn" in text or "dll" in text:
        return (
            "Не хватает библиотек CUDA (cublas64_12.dll / cudnn). "
            f"Поставьте их без CUDA Toolkit: {pip_hint()} — "
            "«Голос» подхватит их сам. Либо оставьте процессор: "
            "в config.json \"device\": \"cpu\"."
        )
    if "out of memory" in text:
        return "Не хватило видеопамяти — возьмите модель поменьше."
    return "Причина: " + str(exc)[:200]


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
