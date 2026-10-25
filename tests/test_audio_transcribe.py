"""Тесты аудио-утилит и обёртки над faster-whisper (с поддельным движком)."""

import sys
import types

import numpy as np
import pytest

from golos.audio import _trim_silence
from golos.config import Config
from golos.transcribe import (
    TranscriptionError,
    WhisperTranscriber,
    resolve_compute_type,
    resolve_device,
)


# ----------------------------------------------------------------------- audio
def test_trim_silence_cuts_edges():
    sr = 16000
    silence = np.zeros(sr, dtype=np.float32)
    speech = (np.random.RandomState(0).randn(sr, ) * 0.2).astype(np.float32)
    audio = np.concatenate([silence, speech, silence])
    trimmed = _trim_silence(audio, sr)
    assert trimmed.size < audio.size
    # поля по 0.15 c с каждой стороны
    assert audio.size - trimmed.size >= int(sr * 0.5)


def test_trim_silence_keeps_noise_free_signal():
    audio = np.zeros(16000, dtype=np.float32)
    assert _trim_silence(audio, 16000).size == audio.size


def test_resample_changes_length():
    from golos.audio import MicrophoneRecorder

    sr_src, sr_dst = 48000, 16000
    audio = np.sin(np.linspace(0, 100, sr_src)).astype(np.float32)
    recorder = MicrophoneRecorder(Config())
    recorder._np = np
    out = recorder._resample(audio, sr_src, sr_dst)
    assert abs(out.size - sr_dst) <= 2
    assert out.dtype == np.float32


# -------------------------------------------------------------------- запись
class FakeStream:
    """Поддельный sounddevice.InputStream: выдаёт заданные блоки в callback."""

    def __init__(self, chunks, **options):
        self.chunks = chunks
        self.options = options
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True
        for chunk in self.chunks:
            self.options["callback"](chunk, len(chunk), None, None)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def install_fake_sounddevice(monkeypatch, chunks, samplerate_ok=True, error=None):
    module = types.ModuleType("sounddevice")
    module.default = types.SimpleNamespace(device=(1, 2))

    def query_devices(device=None, kind=None):
        if device is not None:
            return {"name": "USB Mic", "default_samplerate": 48000.0, "max_input_channels": 1}
        return [
            {"name": "Микрофон (Realtek)", "max_input_channels": 2},
            {"name": "Динамики", "max_input_channels": 0},
            {"name": "USB Mic", "max_input_channels": 1},
        ]

    def check_input_settings(**kwargs):
        if not samplerate_ok:
            raise RuntimeError("unsupported samplerate")

    def InputStream(**options):
        if error:
            raise error
        return FakeStream(chunks, **options)

    module.query_devices = query_devices
    module.check_input_settings = check_input_settings
    module.InputStream = InputStream
    module.last_stream = None
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


def test_list_input_devices_filters_outputs(monkeypatch):
    from golos.audio import list_input_devices

    install_fake_sounddevice(monkeypatch, [])
    devices = list_input_devices()
    assert devices == ["0: Микрофон (Realtek)", "2: USB Mic"]


def test_resolve_device_variants(monkeypatch):
    from golos.audio import resolve_device

    install_fake_sounddevice(monkeypatch, [])
    assert resolve_device("") is None
    assert resolve_device("2") == 2
    assert resolve_device("usb mic") == 2
    assert resolve_device("нет такого") == 1  # устройство по умолчанию


def test_recorder_records_and_converts(monkeypatch):
    from golos.audio import MicrophoneRecorder

    chunk = (np.ones((1600, 1), dtype=np.float32) * 0.3)
    install_fake_sounddevice(monkeypatch, [chunk.copy() for _ in range(5)])
    cfg = Config(sample_rate=16000, trim_silence=False)
    recorder = MicrophoneRecorder(cfg)

    recorder.start()
    assert recorder.is_recording
    assert recorder.peak > 0.2
    recording = recorder.stop()

    assert recording is not None
    assert not recorder.is_recording
    assert recording.sample_rate == 16000
    assert abs(recording.duration - 0.5) < 1e-6
    assert recording.samples.shape == (8000,)
    assert recording


def test_recorder_resamples_when_device_rate_differs(monkeypatch):
    from golos.audio import MicrophoneRecorder

    chunk = np.ones((4800, 1), dtype=np.float32) * 0.2  # 0.1 c при 48 кГц
    install_fake_sounddevice(monkeypatch, [chunk.copy() for _ in range(10)], samplerate_ok=False)
    recorder = MicrophoneRecorder(Config(sample_rate=16000, trim_silence=False))
    recorder.start()
    recording = recorder.stop()
    assert recording is not None
    assert abs(recording.duration - 1.0) < 0.01
    assert recording.samples.size == 16000


def test_recorder_reports_open_error(monkeypatch):
    from golos.audio import AudioError, MicrophoneRecorder

    install_fake_sounddevice(monkeypatch, [], error=RuntimeError("device busy"))
    recorder = MicrophoneRecorder(Config())
    with pytest.raises(AudioError) as info:
        recorder.start()
    assert "device busy" in str(info.value)


def test_recorder_cancel_discards_audio(monkeypatch):
    from golos.audio import MicrophoneRecorder

    chunk = np.ones((1600, 1), dtype=np.float32) * 0.2
    install_fake_sounddevice(monkeypatch, [chunk.copy() for _ in range(5)])
    recorder = MicrophoneRecorder(Config())
    recorder.start()
    recorder.cancel()
    assert not recorder.is_recording
    assert recorder.stop() is None


def test_missing_sounddevice_message(monkeypatch):
    from golos.audio import AudioError, MicrophoneRecorder

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    recorder = MicrophoneRecorder(Config())
    with pytest.raises(AudioError) as info:
        recorder.start()
    assert "sounddevice" in str(info.value)


# ------------------------------------------------------------------ transcribe
class FakeSegment:
    def __init__(self, text, language="ru"):
        self.text = text
        self.language = language


class FakeInfo:
    language = "ru"


class FakeModel:
    def __init__(self, segments, captured=None, error=None):
        self.segments = segments
        self.captured = captured if captured is not None else {}
        self.error = error

    def transcribe(self, audio, **params):
        self.captured.update(params)
        self.captured["audio"] = audio
        if self.error:
            raise RuntimeError(self.error)
        return iter(self.segments), FakeInfo()


def install_fake_engine(monkeypatch, model):
    module = types.ModuleType("faster_whisper")

    def WhisperModel(name, **kwargs):
        module.last_model_args = dict(kwargs, name=name)
        return model

    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return module


def test_transcribe_joins_segments_and_passes_parameters(monkeypatch, tmp_path):
    model = FakeModel([FakeSegment(" привет"), FakeSegment(" мир")])
    install_fake_engine(monkeypatch, model)
    cfg = Config(language="auto", beam_size=3, vad_filter=True, initial_prompt="тест")
    transcriber = WhisperTranscriber(cfg, tmp_path)

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(audio)

    assert result.text == "привет мир"
    assert result.language == "ru"
    assert abs(result.audio_duration - 1.0) < 1e-6
    assert model.captured["language"] is None  # auto
    assert model.captured["beam_size"] == 3
    assert model.captured["vad_filter"] is True
    assert model.captured["initial_prompt"] == "тест"
    assert model.captured["task"] == "transcribe"


def test_transcribe_fixed_language(monkeypatch, tmp_path):
    model = FakeModel([FakeSegment("hello")])
    install_fake_engine(monkeypatch, model)
    transcriber = WhisperTranscriber(Config(language="en"), tmp_path)
    transcriber.transcribe(np.zeros(8000, dtype=np.float32))
    assert model.captured["language"] == "en"


def test_empty_audio_returns_empty_text(tmp_path):
    transcriber = WhisperTranscriber(Config(), tmp_path)
    assert transcriber.transcribe(np.zeros(0, dtype=np.float32)).text == ""
    assert transcriber.transcribe(None).text == ""


def test_engine_errors_are_wrapped(monkeypatch, tmp_path):
    install_fake_engine(monkeypatch, FakeModel([], error="boom"))
    transcriber = WhisperTranscriber(Config(), tmp_path)
    with pytest.raises(TranscriptionError) as info:
        transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert "boom" in str(info.value)


def test_missing_engine_gives_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    transcriber = WhisperTranscriber(Config(), tmp_path)
    with pytest.raises(TranscriptionError) as info:
        transcriber.preload()
    assert "faster-whisper" in str(info.value)


def test_model_loaded_once_and_cached(monkeypatch, tmp_path):
    module = install_fake_engine(monkeypatch, FakeModel([FakeSegment("ок")]))
    calls = []
    original = module.WhisperModel

    def counting(name, **kwargs):
        calls.append(name)
        return original(name, **kwargs)

    module.WhisperModel = counting
    transcriber = WhisperTranscriber(Config(model="tiny"), tmp_path)
    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(audio)
    transcriber.transcribe(audio)
    assert calls == ["tiny"]
    assert transcriber.is_ready


def test_download_root_passed(monkeypatch, tmp_path):
    module = install_fake_engine(monkeypatch, FakeModel([]))
    transcriber = WhisperTranscriber(Config(model="small"), tmp_path)
    transcriber.preload()
    assert module.last_model_args["download_root"] == str(tmp_path)
    assert module.last_model_args["name"] == "small"


def test_model_downloaded_detection(tmp_path):
    transcriber = WhisperTranscriber(Config(model="small"), tmp_path / "models")
    assert transcriber.is_model_downloaded() is False
    (tmp_path / "models").mkdir(parents=True)
    (tmp_path / "models" / "models--Systran--faster-whisper-small").mkdir()
    assert transcriber.is_model_downloaded() is True
    assert transcriber.is_model_downloaded("medium") is False


def test_fallback_to_cpu_when_cuda_fails(monkeypatch, tmp_path):
    module = types.ModuleType("faster_whisper")
    attempts = []

    def WhisperModel(name, **kwargs):
        attempts.append(kwargs.get("device"))
        if kwargs.get("device") == "cuda":
            raise RuntimeError("CUDA not available")
        return FakeModel([FakeSegment("ок")])

    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr("golos.transcribe.resolve_device", lambda _d: "cuda")

    transcriber = WhisperTranscriber(Config(device="cuda"), tmp_path)
    transcriber.preload()
    assert attempts == ["cuda", "cpu"]
    assert transcriber.model_key[1] == "cpu"


def test_resolve_device_and_compute_type(monkeypatch):
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("cuda") == "cuda"
    monkeypatch.setattr("golos.transcribe.resolve_device", lambda _d: "cpu")
    assert resolve_compute_type("cpu", "auto") == "int8"
    assert resolve_compute_type("cuda", "auto") == "float16"
    assert resolve_compute_type("cpu", "float32") == "float32"


# ------------------------------------------------- откат с CUDA на процессор
def test_cuda_fallback_reason_is_explained(monkeypatch, tmp_path):
    """cublas64_12.dll отсутствует — должно быть понятное объяснение."""
    module = types.ModuleType("faster_whisper")

    def WhisperModel(name, **kwargs):
        if kwargs.get("device") == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return FakeModel([FakeSegment("ок")])

    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr("golos.transcribe.resolve_device", lambda _d: "cuda")

    transcriber = WhisperTranscriber(Config(device="cuda"), tmp_path)
    transcriber.preload()

    assert transcriber.active_device == "cpu"
    assert transcriber.active_compute == "int8"
    assert "cublas64_12.dll" in transcriber.fallback_reason
    assert transcriber.is_ready


def test_cuda_hint_variants():
    from golos.transcribe import _cuda_hint

    assert "видеопамяти" in _cuda_hint(RuntimeError("CUDA out of memory"))
    assert "cublas" in _cuda_hint(RuntimeError("cublas64_12.dll not found"))
    assert _cuda_hint(RuntimeError("что-то ещё")).startswith("Причина:")


# ------------------------------------------------ язык сеанса при "auto" (1.4)
class LangModel(FakeModel):
    """Модель, которая сообщает определённый язык и уверенность."""

    def __init__(self, language, probability, segments=None):
        super().__init__(segments or [FakeSegment("привет")])
        self.info = types.SimpleNamespace(
            language=language, language_probability=probability
        )

    def transcribe(self, audio, **params):
        self.captured.update(params)
        return iter(self.segments), self.info


def make_auto_transcriber(monkeypatch, tmp_path, language, probability):
    from golos.transcribe import WhisperTranscriber

    model = LangModel(language, probability)
    transcriber = WhisperTranscriber(Config(language="auto"), tmp_path)
    monkeypatch.setattr(transcriber, "_ensure_model", lambda: model)
    transcriber.begin_session()
    return transcriber, model


def test_auto_language_is_locked_per_session(monkeypatch, tmp_path):
    """«auto» больше не скачет между русским и английским внутри диктовки."""
    transcriber, model = make_auto_transcriber(monkeypatch, tmp_path, "ru", 0.95)
    audio = np.full(int(16000 * 3.0), 0.1, dtype=np.float32)

    transcriber.segments(audio, transcriber.config)
    assert transcriber._session_language == "ru"
    assert model.captured["language"] is None, "первый кусок — автоопределение"

    transcriber.segments(audio, transcriber.config)
    assert model.captured["language"] == "ru", "дальше язык сеанса фиксирован"


def test_begin_session_resets_language(monkeypatch, tmp_path):
    from golos.transcribe import WhisperTranscriber

    transcriber = WhisperTranscriber(Config(language="auto"), tmp_path)
    transcriber._session_language = "en"
    transcriber.begin_session()
    assert transcriber._session_language == ""


def test_low_confidence_does_not_lock_language(monkeypatch, tmp_path):
    transcriber, _model = make_auto_transcriber(monkeypatch, tmp_path, "en", 0.4)
    audio = np.full(int(16000 * 3.0), 0.1, dtype=np.float32)

    transcriber.segments(audio, transcriber.config)
    assert transcriber._session_language == "", "неуверенное определение не фиксируем"


def test_language_is_not_locked_in_fixed_mode(monkeypatch, tmp_path):
    from golos.transcribe import WhisperTranscriber

    model = LangModel("en", 0.99)
    transcriber = WhisperTranscriber(Config(language="ru"), tmp_path)
    monkeypatch.setattr(transcriber, "_ensure_model", lambda: model)
    transcriber.begin_session()
    transcriber.segments(np.full(int(16000 * 2.0), 0.1, dtype=np.float32),
                         transcriber.config)

    assert model.captured["language"] == "ru"
    assert transcriber._session_language == ""
