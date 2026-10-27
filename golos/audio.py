"""Запись с микрофона (sounddevice + numpy).

Модуль не импортирует sounddevice/numpy на уровне файла: приложение должно
запускаться и показывать понятную ошибку, даже если зависимости не стоят.
"""

from __future__ import annotations

import logging
import sys
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


def boost_quiet(samples, target_peak: float = 0.55, max_gain: float = 12.0):
    """Подтянуть тихую речь до нормальной громкости, не трогая громкую.

    Whisper обучен на нормальном уровне записи: при пике 0.02–0.05 (тихий
    микрофон, человек говорит вполголоса) он слышит «шум» и выдаёт пустой
    текст или выдумки. Усиливаем такие куски до ``target_peak``, но не больше
    чем в ``max_gain`` раз — иначе вместе с речью вылезет шум комнаты.
    """
    try:
        import numpy as np
    except Exception:  # pragma: no cover - numpy приходит с sounddevice
        return samples
    if samples is None or getattr(samples, "size", 0) == 0:
        return samples
    audio = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak <= 1e-4 or peak >= target_peak:
        return audio
    gain = min(max_gain, target_peak / peak)
    if gain <= 1.05:
        return audio
    return np.clip(audio * gain, -1.0, 1.0)


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
        self._device_rate = int(config.sample_rate)   # частота микрофона
        self._capture_rate = int(config.sample_rate)  # частота, в которой храним
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
            # Микрофон пишет в своей частоте, а храним уже приведённое к 16 кГц:
            # иначе каждый snapshot() пересчитывал бы весь буфер заново.
            self._capture_rate = int(self.config.sample_rate or 16000)
            self._chunks = []
            self._peak = 0.0
            self._np = np

            extra = _wasapi_settings(sd)
            try:
                # Создание потока колбэки не вызывает — можно делать это под замком.
                stream = _open_stream(sd, device, rate, extra, self._callback)
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
            chunks, rate = self._chunks, self._capture_rate
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

        if getattr(self.config, "normalize_audio", True) and audio.size:
            boosted = boost_quiet(audio)
            if boosted is not audio:
                log.info("Тихая запись: усилена в %.1f раза", _gain_of(audio, boosted))

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
            rate = self._capture_rate
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
        try:
            self._peak = (
                float(self._np.max(self._np.abs(chunk))) if chunk.size else 0.0
            )
        except Exception:
            pass

        chunk = self._to_capture_rate(chunk)
        with self._lock:
            if self._recording:
                self._chunks.append(chunk)

    def _to_capture_rate(self, chunk):
        """Привести блок к частоте хранения (16 кГц), не копя лишнюю память."""
        np = self._np
        audio = np.asarray(chunk, dtype=np.float32).reshape(-1)
        src, dst = self._device_rate, self._capture_rate
        if src != dst and audio.size:
            audio = self._resample(audio, src, dst)
        return audio.reshape(-1, 1)

    def _pick_samplerate(self, sd, device):
        """Писать в «родной» частоте микрофона, а к 16 кГц приводить самим.

        Раньше мы просили у драйвера сразу 16 кГц. Windows (особенно через MME)
        «соглашается» и пересэмплирует звук своим фильтром — речь становится
        глухой, и Whisper выдаёт «Алло!» вместо фразы. Поэтому берём частоту
        устройства (обычно 48 кГц) и ресемплируем сами — заодно это дешевле.
        """
        wanted = int(self.config.sample_rate or 16000)
        native = None
        try:
            info = sd.query_devices(device, "input")
            rate = int(round(float(info.get("default_samplerate") or 0)))
            native = rate if rate >= 8000 else None
        except Exception:
            native = None

        order: list[int] = []
        for rate in (native, 48000, 44100, wanted):
            if rate and rate not in order:
                order.append(rate)

        for rate in order:
            try:
                sd.check_input_settings(
                    device=device, channels=1, samplerate=rate, dtype="float32"
                )
                if rate != wanted:
                    log.info(
                        "Запись в родной частоте микрофона %s Гц (для модели — %s Гц)",
                        rate,
                        wanted,
                    )
                return rate
            except Exception:
                continue
        fallback = native or max(wanted, 48000)
        log.warning("Микрофон не подтвердил частоты, пробую %s Гц", fallback)
        return fallback

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


def _wasapi_settings(sd):
    """WASAPI вместо MME: на Windows звук заметно чище и без лишних задержек.

    ``auto_convert`` разрешает драйверу подстроить частоту, если наша не
    поддерживается напрямую. Если WASAPI недоступен (или падает), просто
    открываем поток как раньше.
    """
    try:
        if sys.platform != "win32":
            return None
        settings = getattr(sd, "WasapiSettings", None)
        return settings(auto_convert=True) if settings else None
    except Exception:  # pragma: no cover - экзотические сборки PortAudio
        return None


def _open_stream(sd, device, rate: int, extra, callback):
    options = {
        "device": device,
        "channels": 1,
        "samplerate": rate,
        "dtype": "float32",
        "blocksize": 0,
        "callback": callback,
    }
    if extra is not None:
        try:
            return sd.InputStream(extra_settings=extra, **options)
        except Exception as exc:
            log.debug("WASAPI не подошёл (%s) — открываю поток без него", exc)
    return sd.InputStream(**options)


def _gain_of(before, after) -> float:
    """Во сколько раз усилили звук (для журнала)."""
    try:
        import numpy as np

        peak_before = float(np.max(np.abs(before)))
        peak_after = float(np.max(np.abs(after)))
        return peak_after / peak_before if peak_before > 0 else 1.0
    except Exception:  # pragma: no cover
        return 1.0


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
