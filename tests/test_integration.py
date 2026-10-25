"""Сквозной тест: настоящие классы записи/распознавания/вставки с фейковым железом.

Проверяется весь путь «нажали клавишу → записали → распознали → вставили»,
кроме собственно Windows-вызовов (микрофон, буфер, клавиатура, модель).
"""

import sys
import time
import types

import numpy as np
import pytest

from golos.app import IDLE, DictationApp
from golos.audio import MicrophoneRecorder
from golos.config import AppPaths, Config
from golos.hotkeys import HotkeyManager
from golos.inject import TextInjector
from golos.transcribe import WhisperTranscriber


class FakeClipboard:
    def __init__(self):
        self.text = "старый буфер"

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text


class FakeKeyboard:
    def __init__(self):
        self.sent = []
        self.typed = []

    def send(self, combination):
        self.sent.append(combination)

    def write(self, text, delay=0.0):
        self.typed.append(text)


class FakeHotkeyBackend:
    def __init__(self):
        self.handlers = {}

    def add(self, combination, callback, suppress=False):
        self.handlers[combination] = callback
        return combination

    def remove(self, handle):
        self.handlers.pop(handle, None)

    def validate(self, combination):
        return bool(combination)

    def press(self, combination):
        self.handlers[combination]()


class FakeStream:
    def __init__(self, chunks, **options):
        self.chunks = chunks
        self.options = options

    def start(self):
        for chunk in self.chunks:
            self.options["callback"](chunk, len(chunk), None, None)

    def stop(self):
        pass

    def close(self):
        pass


class Ui:
    def __init__(self):
        self.states = []
        self.texts = []
        self.errors = []

    def on_state(self, state, message=""):
        self.states.append(state)

    def on_level(self, level):
        pass

    def on_text(self, text, raw=""):
        self.texts.append(text)

    def on_error(self, message):
        self.errors.append(message)

    def on_notify(self, title, message):
        pass


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Собрать приложение с настоящими классами и фейковым «железом»."""
    speech = (np.random.RandomState(1).randn(16000, 1) * 0.25).astype(np.float32)
    # 1 секунда «речи» при 16 кГц, один блок
    sd = types.ModuleType("sounddevice")
    sd.default = types.SimpleNamespace(device=(0, 1))
    sd.query_devices = lambda *a, **k: [
        {"name": "Fake Mic", "max_input_channels": 1, "default_samplerate": 16000.0}
    ]
    sd.check_input_settings = lambda **k: None
    sd.InputStream = lambda **options: FakeStream([speech], **options)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)

    transcribed = {"text": "привет , это тест на Python"}

    class Model:
        def transcribe(self, audio, **params):
            seg = types.SimpleNamespace(text=transcribed["text"], language="ru")
            info = types.SimpleNamespace(language="ru")
            return iter([seg]), info

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = lambda name, **kwargs: Model()
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    config = Config()
    config.streaming = False  # сквозной тест одиночного режима
    config.sound_feedback = False
    config.notifications = False
    config.preload_model = False
    config.paste_delay = 0
    config.sample_rate = 16000

    clipboard = FakeClipboard()
    keyboard = FakeKeyboard()
    hotkey_backend = FakeHotkeyBackend()
    ui = Ui()

    app = DictationApp(
        config=config,
        paths=AppPaths(data_dir=tmp_path),
        recorder=MicrophoneRecorder(config),
        transcriber=WhisperTranscriber(config, tmp_path / "models"),
        injector=TextInjector(
            method="paste",
            restore_clipboard=True,
            paste_delay=0,
            restore_delay=0,
            clipboard=clipboard,
            keyboard=keyboard,
            sleep=lambda _s: None,
        ),
        hotkeys=HotkeyManager(hotkey_backend),
        ui=ui,
        preload=False,
    )
    app.start()
    return types.SimpleNamespace(
        app=app,
        ui=ui,
        keyboard=keyboard,
        clipboard=clipboard,
        hotkeys=hotkey_backend,
        transcribed=transcribed,
    )


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_full_dictation_cycle(pipeline):
    app = pipeline.app

    pipeline.hotkeys.press("ctrl+alt+space")  # начать запись
    assert app.is_recording

    pipeline.hotkeys.press("ctrl+alt+space")  # остановить
    assert wait_until(lambda: app.state == IDLE and pipeline.keyboard.sent)

    # текст очищен, английское слово сохранено, вставка через Ctrl+V
    assert pipeline.keyboard.sent == ["ctrl+v"]
    assert pipeline.clipboard.text == "старый буфер"  # буфер восстановлен
    assert app.last_text == "Привет, это тест на Python "
    assert pipeline.ui.texts == ["Привет, это тест на Python "]
    assert app.stats.sessions == 1


def test_cancel_hotkey_discards(pipeline):
    app = pipeline.app
    pipeline.hotkeys.press("ctrl+alt+space")
    assert "escape" in pipeline.hotkeys.handlers

    pipeline.hotkeys.press("escape")
    assert not app.is_recording
    assert wait_until(lambda: "escape" not in pipeline.hotkeys.handlers)
    assert pipeline.keyboard.sent == []


def test_language_and_replacements_from_config(pipeline):
    app = pipeline.app
    cfg = app.config.copy()
    cfg.replacements = [["питон", "Python"]]
    cfg.trailing_space = False
    app.apply_config(cfg)

    pipeline.transcribed["text"] = "запусти питон и git push"
    pipeline.hotkeys.press("ctrl+alt+space")
    pipeline.hotkeys.press("ctrl+alt+space")

    assert wait_until(lambda: app.last_text)
    assert app.last_text == "Запусти Python и git push"


def test_second_dictation_appends_new_text(pipeline):
    app = pipeline.app
    for expected in (1, 2):
        assert wait_until(lambda: app.state == IDLE)
        pipeline.hotkeys.press("ctrl+alt+space")
        assert app.is_recording
        pipeline.hotkeys.press("ctrl+alt+space")
        assert wait_until(lambda: app.stats.sessions == expected)
    assert len(pipeline.ui.texts) == 2
    assert len(pipeline.keyboard.sent) == 2
