"""Сквозные тесты диктовки с поддельными микрофоном и моделью."""

import time

from golos.app import ERROR, IDLE, PAUSED, RECORDING, DictationApp
from golos.audio import AudioError, Recording
from golos.config import AppPaths, Config
from golos.transcribe import TranscriptionResult


class FakeSamples:
    def __init__(self, size=16000):
        self.size = size


class FakeRecorder:
    def __init__(self, duration=2.0, peak=0.2, fail=False):
        self.config = Config()
        self._recording = False
        self.duration = duration
        self._peak = 0.0
        self.peak_value = peak
        self.fail = fail
        self.stopped = 0
        self.cancelled = 0

    @property
    def is_recording(self):
        return self._recording

    @property
    def peak(self):
        return self._peak

    def set_config(self, config):
        self.config = config

    def start(self):
        if self.fail:
            raise AudioError("микрофон не найден")
        self._recording = True
        self._peak = self.peak_value

    def stop(self):
        if not self._recording:
            return None
        self._recording = False
        self.stopped += 1
        self._peak = 0.0
        return Recording(FakeSamples(), 16000, self.duration, self.peak_value)

    def cancel(self):
        self._recording = False
        self.cancelled += 1


class FakeTranscriber:
    def __init__(self, text="привет , как дела ?", fail=False):
        self.config = Config()
        self.text = text
        self.fail = fail
        self.is_ready = False
        self.calls = 0
        self.preloaded = 0

    def set_config(self, config):
        self.config = config

    def is_model_downloaded(self, model=None):
        return True

    def preload(self):
        self.preloaded += 1
        self.is_ready = True

    def unload(self):
        self.is_ready = False

    def transcribe(self, audio, config=None):
        self.calls += 1
        if self.fail:
            from golos.transcribe import TranscriptionError

            raise TranscriptionError("модель сломалась")
        return TranscriptionResult(text=self.text, language="ru", audio_duration=2.0, latency=0.3)


class FakeInjector:
    def __init__(self):
        self.inserted: list[str] = []
        self.fail = False

    def apply_config(self, config):
        pass

    def insert(self, text):
        if self.fail:
            from golos.inject import InjectionError

            raise InjectionError("нет доступа к буферу")
        self.inserted.append(text)
        return True


class FakeHotkeys:
    def __init__(self):
        self._active = {}
        self._handles = []

    @property
    def registered(self):
        return dict(self._active)

    def register(self, name, combination, callback):
        self._active[name] = combination
        self._handles.append((name, callback))
        return True

    def unregister(self, name):
        self._active.pop(name, None)

    def unregister_all(self):
        self._active.clear()

    def rebind(self, bindings):
        self.unregister_all()
        return {n: self.register(n, c, f) for n, (c, f) in bindings.items()}

    def fire(self, name):
        for n, cb in self._handles:
            if n == name:
                cb()


class RecordingUi:
    def __init__(self):
        self.states = []
        self.texts = []
        self.errors = []
        self.notifications = []

    def on_state(self, state, message=""):
        self.states.append(state)

    def on_level(self, level):
        pass

    def on_text(self, text, raw=""):
        self.texts.append(text)

    def on_error(self, message):
        self.errors.append(message)

    def on_notify(self, title, message):
        self.notifications.append(message)


def make_app(tmp_path, recorder=None, transcriber=None, injector=None, ui=None, **cfg):
    config = Config(**cfg)
    config.trailing_space = True
    config.sound_feedback = False
    config.notifications = False
    config.preload_model = False
    app = DictationApp(
        config=config,
        paths=AppPaths(data_dir=tmp_path),
        recorder=recorder or FakeRecorder(),
        transcriber=transcriber or FakeTranscriber(),
        injector=injector or FakeInjector(),
        hotkeys=FakeHotkeys(),
        ui=ui or RecordingUi(),
        preload=False,
    )
    return app


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_toggle_records_and_inserts(tmp_path):
    recorder = FakeRecorder()
    injector = FakeInjector()
    ui = RecordingUi()
    app = make_app(tmp_path, recorder=recorder, injector=injector, ui=ui)

    app.start()
    app.toggle()
    assert recorder.is_recording
    assert RECORDING in ui.states

    app.toggle()
    assert wait_for(lambda: injector.inserted)

    assert injector.inserted == ["Привет, как дела? "]
    assert ui.texts == ["Привет, как дела? "]
    assert app.state == IDLE
    assert app.stats.sessions == 1
    assert app.stats.words == 3


def test_hotkey_registered_and_fires(tmp_path):
    app = make_app(tmp_path)
    app.start()
    assert app.hotkeys.registered["toggle"] == "ctrl+alt+space"
    app.hotkeys.fire("toggle")
    assert app.is_recording


def test_cancel_hotkey_appears_while_recording(tmp_path):
    app = make_app(tmp_path)
    app.start()
    app.toggle()
    assert app.hotkeys.registered.get("cancel") == "escape"
    app.hotkeys.fire("cancel")
    assert not app.is_recording
    assert "cancel" not in app.hotkeys.registered


def test_short_recording_ignored(tmp_path):
    recorder = FakeRecorder(duration=0.1)
    injector = FakeInjector()
    app = make_app(tmp_path, recorder=recorder, injector=injector, min_duration=0.35)
    app.start()
    app.toggle()
    app.toggle()
    assert wait_for(lambda: app.state == IDLE)
    assert injector.inserted == []


def test_silent_recording_notifies(tmp_path):
    ui = RecordingUi()
    recorder = FakeRecorder(peak=0.0)
    app = make_app(tmp_path, recorder=recorder, ui=ui)
    app.start()
    app.toggle()
    app.toggle()
    assert wait_for(lambda: any("молчит" in n for n in ui.notifications))


def test_microphone_error_sets_error_state(tmp_path):
    ui = RecordingUi()
    app = make_app(tmp_path, recorder=FakeRecorder(fail=True), ui=ui)
    app.start()
    app.toggle()
    assert app.state == ERROR
    assert ui.errors and "микрофон" in ui.errors[0]


def test_transcription_error_sets_error_state(tmp_path):
    ui = RecordingUi()
    transcriber = FakeTranscriber(fail=True)
    app = make_app(tmp_path, transcriber=transcriber, ui=ui)
    app.start()
    app.toggle()
    app.toggle()
    assert wait_for(lambda: app.state == ERROR)
    assert any("модель сломалась" in e for e in ui.errors)


def test_empty_transcription_is_skipped(tmp_path):
    transcriber = FakeTranscriber(text="")
    injector = FakeInjector()
    ui = RecordingUi()
    app = make_app(tmp_path, transcriber=transcriber, injector=injector, ui=ui)
    app.start()
    app.toggle()
    app.toggle()
    assert wait_for(lambda: any("не распознана" in n for n in ui.notifications))
    assert injector.inserted == []


def test_pause_blocks_hotkey(tmp_path):
    app = make_app(tmp_path)
    app.start()
    app.pause(True)
    assert app.state == PAUSED
    assert app.hotkeys.registered == {}
    app.toggle()
    assert not app.is_recording
    app.pause(False)
    assert app.hotkeys.registered["toggle"] == "ctrl+alt+space"


def test_history_written(tmp_path):
    app = make_app(tmp_path)
    app.start()
    app.toggle()
    app.toggle()
    assert wait_for(lambda: app.paths.history_file.exists() and app.paths.history_file.read_text(encoding="utf-8").strip())
    assert "Привет" in app.paths.history_file.read_text(encoding="utf-8")


def test_apply_config_persists(tmp_path):
    app = make_app(tmp_path)
    app.start()
    new_cfg = app.config.copy()
    new_cfg.hotkey = "ctrl+shift+d"
    new_cfg.language = "ru"
    warnings = app.apply_config(new_cfg)
    assert warnings == []
    assert app.paths.config_file.exists()
    assert app.hotkeys.registered["toggle"] == "ctrl+shift+d"
    assert app.config.language == "ru"


def test_apply_config_reloads_model_and_rebinds(tmp_path):
    transcriber = FakeTranscriber()
    config = Config()
    config.sound_feedback = False
    config.notifications = False
    app = DictationApp(
        config=config,
        paths=AppPaths(data_dir=tmp_path),
        recorder=FakeRecorder(),
        transcriber=transcriber,
        injector=FakeInjector(),
        hotkeys=FakeHotkeys(),
        ui=RecordingUi(),
        preload=True,
    )
    app.start()
    assert wait_for(lambda: transcriber.preloaded == 1)
    assert transcriber.is_ready

    new_cfg = app.config.copy()
    new_cfg.model = "small"
    app.apply_config(new_cfg)
    assert wait_for(lambda: transcriber.preloaded == 2)
    app.stop()


def test_injection_failure_reports_error_but_keeps_text(tmp_path):
    ui = RecordingUi()
    injector = FakeInjector()
    injector.fail = True
    app = make_app(tmp_path, injector=injector, ui=ui)
    app.start()
    app.toggle()
    app.toggle()
    assert wait_for(lambda: bool(ui.errors))
    assert ui.texts and "Привет" in ui.texts[0]


def test_status_dict(tmp_path):
    app = make_app(tmp_path)
    app.start()
    status = app.status()
    assert status["hotkey"] == "ctrl+alt+space"
    assert status["state"] == IDLE
    assert status["model"] == "medium"
    assert status["config_file"].endswith("config.json")


def test_stop_cancels_active_recording(tmp_path):
    recorder = FakeRecorder()
    app = make_app(tmp_path, recorder=recorder)
    app.start()
    app.toggle()
    app.stop()
    assert recorder.cancelled == 1
    assert app.hotkeys.registered == {}
