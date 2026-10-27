"""Тесты на жалобы из журнала 1.0–1.2: долгий стоп, повторы, языки, ярлык."""

import threading
import time

import numpy as np
import pytest

from golos import cuda, system, textproc
from golos.app import IDLE, DictationApp
from golos.config import LANGUAGES, AppPaths, Config, load_config
from golos.streaming import StreamingDictator
from golos.transcribe import WhisperTranscriber
from tests.test_app import FakeHotkeys, FakeInjector, FakeRecorder, RecordingUi
from tests.test_streaming import FakeRecorder as StreamRecorder
from tests.test_streaming import WordTranscriber, speech_like


# --------------------------------------------------------------- повторы кусков
def test_strip_repeated_prefix_removes_overlap():
    assert (
        textproc.strip_repeated_prefix("Всё нормально. Как дела?", "Ну всё нормально.")
        == "Как дела?"
    )


def test_strip_repeated_prefix_keeps_new_text():
    assert textproc.strip_repeated_prefix("Совсем другое", "Ну всё нормально") == (
        "Совсем другое"
    )


def test_strip_repeated_prefix_whole_chunk_is_dropped():
    assert textproc.strip_repeated_prefix("Понимаешь.", "Ну да. Понимаешь.") == ""


def test_repeats_recent_blocks_short_phrase():
    recent = ["Понимаешь.", "другое", "Понимаешь."]
    assert textproc.repeats_recent("понимаешь", recent) is True
    assert textproc.repeats_recent("совсем другая фраза", recent) is False


def test_repeats_recent_ignores_long_phrases():
    """Длинную фразу человек может повторить осознанно."""
    long_text = "нужно проверить ещё раз логи и перезапустить сервис"
    assert textproc.repeats_recent(long_text, [long_text, long_text]) is False


def test_prompt_echo_is_a_hallucination():
    assert textproc.looks_like_hallucination("Смешанная русская и английская речь.")


class AlternatingTranscriber(WordTranscriber):
    """Один и тот же короткий кусок через раз — как в журнале пользователя."""

    def __init__(self):
        super().__init__(words=("Понимаешь.",))
        self.step = 0

    def segments(self, audio, config=None, initial_prompt=None, beam_size=None,
                 want_words=True):
        self.step += 1
        words = ("Понимаешь.",)
        if self.step % 2 == 0:
            words = ("Сегодня", "проверим", "сборку")
        return [
            {
                "text": " ".join(words) + " ",
                "start": 0.0,
                "end": float(len(words)) * 0.5,
                "words": [
                    {"text": word, "start": i * 0.5, "end": (i + 1) * 0.5,
                     "probability": 0.9}
                    for i, word in enumerate(words)
                ],
            }
        ]


def test_alternating_repeat_is_blocked():
    """«Понимаешь.» через раз тоже не должно вставляться десятки раз."""
    cfg = Config()
    injector = FakeInjector()
    stream = StreamingDictator(
        recorder=StreamRecorder(),
        transcriber=AlternatingTranscriber(),
        injector=injector,
        config=cfg,
    )

    audio = speech_like(3, 0.1)
    for _ in range(8):
        stream._process_tail(audio, final=False)

    repeated = [item for item in injector.inserted if "Понимаешь" in item]
    assert len(repeated) <= 2


def test_new_session_forgets_previous_final_audio():
    """Новый сеанс не должен дораспознавать стоп-звук предыдущего."""
    stream = StreamingDictator(
        recorder=StreamRecorder(),
        transcriber=WordTranscriber(),
        injector=FakeInjector(),
        config=Config(min_chunk_seconds=5.0),
    )
    stream._final_audio = np.ones(1600, dtype=np.float32)
    stream._recent = ["Понимаешь."]

    stream.start()
    try:
        assert stream._final_audio is None
        assert stream._recent == []
    finally:
        stream.cancel()


# ------------------------------------------------------------------ автозапуск
def test_start_enables_autostart_from_config(tmp_path, monkeypatch):
    """«Запускать с Windows» в настройках действительно включает автозапуск."""
    from golos import app as app_module

    calls = []
    monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(app_module, "set_autostart", lambda value: calls.append(value) or True)

    app = make_streaming_app(tmp_path)
    app.config.autostart = True
    app.start()

    assert calls == [True]


def test_start_removes_autostart_when_disabled(tmp_path, monkeypatch):
    from golos import app as app_module

    calls = []
    monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: True)
    monkeypatch.setattr(app_module, "set_autostart", lambda value: calls.append(value) or True)

    app = make_streaming_app(tmp_path)
    app.config.autostart = False
    app.start()

    assert calls == [False]


# ------------------------------------------------------------- остановка записи
class SlowStream:
    """Поток, который «думает» долго — как настоящий финальный проход модели."""

    def __init__(self):
        self.is_active = True
        self.config = Config()
        self.text = "привет"
        self.started = threading.Event()
        self.finished = threading.Event()
        self.received_audio = None

    def start(self):
        self.is_active = True

    def stop(self, audio=None):
        self.received_audio = audio
        self.started.set()
        time.sleep(0.4)
        self.is_active = False
        self.finished.set()
        return self.text

    def cancel(self):
        self.is_active = False


def make_streaming_app(tmp_path, stream=None, recorder=None):
    config = Config()
    config.streaming = True
    config.sound_feedback = False
    config.notifications = False
    config.preload_model = False
    app = DictationApp(
        config=config,
        paths=AppPaths(data_dir=tmp_path),
        recorder=recorder or FakeRecorder(),
        transcriber=WordTranscriber(),
        injector=FakeInjector(),
        hotkeys=FakeHotkeys(),
        ui=RecordingUi(),
        preload=False,
    )
    app.stream = stream or SlowStream()
    return app


def test_stop_recording_returns_immediately(tmp_path):
    """После стопа интерфейс свободен: финальный проход идёт в фоне."""
    app = make_streaming_app(tmp_path)
    app.recorder.start()

    started = time.monotonic()
    app.stop_recording()
    elapsed = time.monotonic() - started

    assert elapsed < 0.3, "остановка не должна ждать распознавание"
    assert app.state == IDLE
    app._finish_thread.join(2.0)
    assert app._finishing.is_set() is False


def test_stop_recording_passes_audio_to_stream(tmp_path):
    """Звук снимается до выключения микрофона и уходит в поток."""
    recorder = FakeRecorder()
    recorder.snapshot = lambda: np.zeros(16000, dtype=np.float32)
    stream = SlowStream()
    app = make_streaming_app(tmp_path, stream=stream, recorder=recorder)
    app.recorder.start()

    app.stop_recording()
    stream.finished.wait(2.0)

    assert stream.received_audio is not None
    assert stream.received_audio.size == 16000


class FakeTracker:
    def __init__(self):
        self.pins = 0
        self.unpins = 0

    def pin(self):
        self.pins += 1
        return 1

    def unpin(self):
        self.unpins += 1


def test_target_stays_pinned_until_final_chunk_inserted(tmp_path):
    """Пока хвост вставляется, окно-получатель не переключается."""
    stream = SlowStream()
    app = make_streaming_app(tmp_path, stream=stream)
    tracker = FakeTracker()
    app.tracker = tracker
    app.recorder.start()

    app.stop_recording()
    assert tracker.unpins == 0, "окно нельзя отпускать до конца вставки"

    app._finish_thread.join(2.0)
    assert tracker.unpins == 1


def test_new_recording_waits_for_final_chunk(tmp_path):
    """Пока хвост дораспознаётся, второй сеанс не начинаем (иначе теряется текст)."""
    stream = SlowStream()
    app = make_streaming_app(tmp_path, stream=stream)
    app.recorder.start()
    app.stop_recording()

    assert app.start_recording() is False
    assert any("Договариваю" in item for item in app.ui.notifications)
    app._finish_thread.join(2.0)
    assert app.start_recording() is True


# ---------------------------------------------------------------------- языки
def test_mixed_language_is_default_and_listed():
    assert Config().language == "ru+en"
    assert "ru+en" in LANGUAGES


def test_mixed_language_does_not_lock_one_language():
    cfg = Config()
    transcriber = WhisperTranscriber(cfg)
    assert transcriber._resolve_language(cfg) is None

    cfg.language = "en"
    assert transcriber._resolve_language(cfg) == "en"


def test_legacy_russian_config_becomes_mixed(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"language": "ru", "config_version": 4}', encoding="utf-8")
    cfg, _ = load_config(path)
    assert cfg.language == "ru+en"


class FakeWord:
    def __init__(self, text):
        self.word = text


class FakeSegment:
    def __init__(self, text):
        self.text = text
        self.start = 0.0
        self.end = 1.0
        self.words = [FakeWord(text)]
        self.language = "en"


class FakeInfo:
    def __init__(self, language="en", probability=0.3):
        self.language = language
        self.language_probability = probability


class FakeModel:
    """Модель, которая на «auto» молчит, а по-русски — слышит."""

    def __init__(self):
        self.languages = []

    def transcribe(self, audio, **params):
        language = params.get("language")
        self.languages.append(language)
        if language is None:
            return iter([]), FakeInfo()
        return iter([FakeSegment("привет")]), FakeInfo("ru", 0.9)


def test_mixed_mode_retries_in_russian_when_empty():
    cfg = Config()
    transcriber = WhisperTranscriber(cfg)
    model = FakeModel()
    params = {"language": None}

    pieces, info = transcriber._run_model(
        model, np.zeros(16000, dtype=np.float32), params, cfg, 1.0
    )

    assert model.languages == [None, "ru"]
    assert pieces and pieces[0].text == "привет"
    assert info.language == "ru"


def test_no_russian_retry_when_text_found():
    cfg = Config()
    transcriber = WhisperTranscriber(cfg)

    class Model(FakeModel):
        def transcribe(self, audio, **params):
            self.languages.append(params.get("language"))
            return iter([FakeSegment("hello")]), FakeInfo("en", 0.95)

    model = Model()
    pieces, _ = transcriber._run_model(
        model, np.zeros(16000, dtype=np.float32), {"language": None}, cfg, 1.0
    )

    assert model.languages == [None]
    assert pieces[0].text == "hello"


def test_short_audio_is_not_retried():
    cfg = Config()
    transcriber = WhisperTranscriber(cfg)
    model = FakeModel()
    transcriber._run_model(
        model, np.zeros(4000, dtype=np.float32), {"language": None}, cfg, 0.2
    )
    assert model.languages == [None]


# ----------------------------------------------------------------------- CUDA
def test_has_cublas_detects_dll(tmp_path):
    (tmp_path / "cublas64_12.dll").write_bytes(b"")
    assert cuda.has_cublas([tmp_path]) is True


def test_has_cublas_reports_missing(tmp_path):
    assert cuda.has_cublas([tmp_path]) is False


def test_cuda_hint_mentions_pip_packages():
    from golos.transcribe import _cuda_hint

    hint = _cuda_hint(Exception("Library cublas64_12.dll is not found or cannot be loaded"))
    assert "nvidia-cublas-cu12" in hint


# --------------------------------------------------------------------- ярлыки
def test_shortcuts_are_windows_only():
    if system.autostart_supported():  # pragma: no cover - проверка на Windows
        pytest.skip("на Windows ярлык создаётся по-настоящему")
    assert system.create_shortcuts() == []
    assert system.shortcut_exists() is False
