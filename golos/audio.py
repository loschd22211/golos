"""Запись с микрофона (sounddevice + numpy).

Модуль не импортирует sounddevice/numpy на уровне файла: приложение должно
запускаться и показывать понятную ошибку, даже если зависимости не стоят.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .config import Config

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    """Проблема с микрофоном — текст ошибки показывается пользователю."""


@dataclass
class Recording:
    """Результат одной записи."""

    samples: object  # numpy.ndarray float32, mono, 16 kHz
    sample_rate: int
    duration: float
    peak: float

    def __bool__(self) -> bool:
        return self.duration > 0 and self.peak > 0


def list_input_devices() -> list[str]:
    """Список входных устройств для окна настроек (индекс + название)."""
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - зависит от окружения
        raise AudioError(f"Модуль sounddevice не установлен: {exc}") from exc

    result: list[str] = []
    try:
        for index, info in enumerate(sd.query_devices()):
            if int(info.get("max_input_channels", 0)) > 0:
                result.append(f"{index}: {info.get('name', 'устройство')}")
    except Exception as exc:  # pragma: no cover
        raise AudioError(f"Не удалось получить список микрофонов: {exc}") from exc
    return result


def resolve_device(spec: str):
    """Найти устройство ввода: пусто -> по умолчанию, «3» -> индекс, иначе по имени."""
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover
        raise AudioError(f"Модуль sounddevice не установлен: {exc}") from exc

    if spec.isdigit():
        return int(spec)

    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0]
    except Exception as exc:  # pragma: no cover
        raise AudioError(f"Не удалось прочитать список устройств: {exc}") from exc

    needle = spec.lower()
    for index, info in enumerate(devices):
        if int(info.get("max_input_channels", 0)) > 0 and needle in str(info.get("name", "")).lower():
            return index
    log.warning("Микрофон %r не найден, использую устройство по умолчанию", spec)
    return default_in if isinstance(default_in, int) and default_in >= 0 else None


class MicrophoneRecorder:
    """Пишет звук в фоне до вызова stop()."""

    def __init__(self, config: Config):
        self.config = config
        self._stream = None
        self._chunks: list = []
        self._lock = threading.Lock()
        self._recording = False
        self._device_rate = int(config.sample_rate)
        self._peak = 0.0

    # ------------------------------------------------------------------ state
    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def peak(self) -> float:
        """Текущая громкость (0..1), для индикатора в трее."""
        return self._peak

    def set_config(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------ record
    def start(self) -> None:
        import numpy as np

        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover
            raise AudioError(f"Модуль sounddevice не установлен: {exc}") from exc

        with self._lock:
            if self._recording:
                return

            device = resolve_device(self.config.input_device)
            rate = self._pick_samplerate(sd, device)
            self._device_rate = rate
            self._chunks = []
            self._peak = 0.0
            self._np = np

            try:
                # Создание потока колбэки не вызывает — можно делать это под замком.
                stream = sd.InputStream(
                    device=device,
                    channels=1,
                    samplerate=rate,
                    dtype="float32",
                    blocksize=0,
                    callback=self._callback,
                )
            except Exception as exc:
                raise _open_error(exc) from exc

            # Флаг ставим до start(): микрофон может прислать первый блок сразу,
            # а колбэк складывает данные только при self._recording.
            self._stream = stream
            self._recording = True

        try:
            # start() вызываем без замка: колбэк берёт этот же замок, иначе
            # возможна взаимная блокировка.
            stream.start()
        except Exception as exc:
            with self._lock:
                self._recording = False
                self._stream = None
                self._chunks = []
            _close_quietly(stream)
            raise _open_error(exc) from exc

        log.info("Запись начата: устройство=%s, частота=%s Гц", device, rate)

    def stop(self) -> Recording | None:
        """Остановить запись и вернуть аудио 16 кГц mono float32."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            stream, self._stream = self._stream, None
            chunks, rate = self._chunks, self._device_rate
            self._chunks = []

        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:  # pragma: no cover
                log.warning("Ошибка остановки аудиопотока: %s", exc)
            _close_quietly(stream)

        np = getattr(self, "_np", None)
        if np is None or not chunks:
            log.info("Запись остановлена, но данных нет")
            return None

        audio = np.concatenate(chunks, axis=0).reshape(-1).astype(np.float32, copy=False)
        if rate != int(self.config.sample_rate):
            audio = self._resample(audio, rate, int(self.config.sample_rate))

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        self._peak = 0.0

        if self.config.trim_silence and audio.size:
            audio = _trim_silence(audio, int(self.config.sample_rate))

        duration = float(audio.size) / float(self.config.sample_rate or 16000)
        log.info("Запись остановлена: %.2f c, пик %.3f", duration, peak)
        return Recording(audio, int(self.config.sample_rate), duration, peak)

    def snapshot(self):
        """Текущее аудио с начала записи (копия, 16 кГц mono float32).

        Нужно для потокового распознавания: пока идёт запись, движок время от
        времени забирает накопленный звук и распознаёт его по кускам.
        """
        np = getattr(self, "_np", None)
        if np is None:
            return None
        with self._lock:
            chunks = list(self._chunks)
            rate = self._device_rate
        if not chunks:
            return None
        audio = np.concatenate(chunks, axis=0).reshape(-1).astype(np.float32, copy=False)
        target = int(self.config.sample_rate or 16000)
        if rate != target:
            audio = self._resample(audio, rate, target)
        return audio

    def cancel(self) -> None:
        """Прервать запись, ничего не сохраняя."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            stream, self._stream = self._stream, None
            self._chunks = []
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # pragma: no cover
                pass
            _close_quietly(stream)
        self._peak = 0.0
        log.info("Запись отменена")

    # ----------------------------------------------------------------- private
    def _callback(self, indata, frames, time_info, status) -> None:  # pragma: no cover
        if status:
            log.debug("Статус аудиопотока: %s", status)
        try:
            chunk = indata.copy()
        except Exception:
            return
        with self._lock:
            if self._recording:
                self._chunks.append(chunk)
        try:
            self._peak = float(self._np.max(self._np.abs(chunk))) if chunk.size else 0.0
        except Exception:
            pass

    def _pick_samplerate(self, sd, device):
        """16 кГц идеальны для Whisper, но не каждый микрофон их поддерживает."""
        wanted = int(self.config.sample_rate or 16000)
        try:
            sd.check_input_settings(
                device=device, channels=1, samplerate=wanted, dtype="float32"
            )
            return wanted
        except Exception:
            pass
        try:
            info = sd.query_devices(device, "input")
            default_rate = int(info.get("default_samplerate") or 48000)
        except Exception:
            default_rate = 48000
        log.info("Микрофон не поддерживает %s Гц, использую %s Гц", wanted, default_rate)
        return default_rate

    def _resample(self, audio, src_rate: int, dst_rate: int):  # pragma: no cover
        np = self._np
        if src_rate == dst_rate or audio.size == 0:
            return audio
        duration = audio.size / float(src_rate)
        target_size = int(round(duration * dst_rate))
        if target_size <= 0:
            return audio[:0]
        src_index = np.linspace(0.0, audio.size - 1, num=target_size, dtype=np.float64)
        return np.interp(src_index, np.arange(audio.size), audio).astype(np.float32)


def _trim_silence(audio, sample_rate: int, threshold: float = 0.006, margin: float = 0.15):
    """Обрезать тишину по краям, оставив небольшие поля."""
    import numpy as np

    if audio.size == 0:
        return audio
    frame = max(1, int(sample_rate * 0.02))
    count = audio.size // frame
    if count < 2:
        return audio
    frames = audio[: count * frame].reshape(count, frame)
    energy = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
    loud = np.nonzero(energy > threshold)[0]
    if loud.size == 0:
        return audio
    margin_frames = max(1, int(margin * sample_rate / frame))
    start = max(0, int(loud[0]) - margin_frames) * frame
    end = min(count, int(loud[-1]) + 1 + margin_frames) * frame
    return audio[start:end]


def _open_error(exc: Exception) -> AudioError:
    return AudioError(
        "Не удалось открыть микрофон. Проверьте, что он подключён и "
        "разрешения Windows «Параметры → Конфиденциальность → Микрофон» включены.\n"
        f"Детали: {exc}"
    )


def _close_quietly(stream) -> None:
    try:
        stream.close()
    except Exception as exc:  # pragma: no cover
        log.debug("Ошибка закрытия аудиопотока: %s", exc)
