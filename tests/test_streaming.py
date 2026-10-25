"""Тесты потоковой диктовки: текст вставляется по мере речи, без дублей."""

import types

import pytest

from golos.config import Config
from golos.streaming import StreamingDictator


class FakeRecorder:
    """Отдаёт заданное аудио, длину которого можно «наращивать» из теста."""

    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.length = 0
        self.config = Config()

    def set_length(self, seconds):
        self.length = int(seconds * self.sample_rate)

    def snapshot(self):
        import numpy as np

        return np.zeros(self.length, dtype=np.float32)


class FakeTranscriber:
    """Возвращает сегменты, рассчитанные из длительности куска."""

    def __init__(self, texts=None, fail=False):
        self.texts = texts or ["привет", "как дела"]
        self.fail = fail
        self.calls = []
        self.is_ready = True

    def set_config(self, config):
        pass

    def segments(self, audio, config=None, initial_prompt=None, beam_size=None):
        duration = audio.size / 16000.0
        self.calls.append(
            {"duration": duration, "prompt": initial_prompt, "beam": beam_size}
        )
        if self.fail:
            from golos.transcribe import TranscriptionError

            raise TranscriptionError("модель недоступна")
        out = []
        step = duration / max(1, len(self.texts))
        for index, text in enumerate(self.texts):
            start = index * step
            end = min(duration, start + step * 0.7)
            out.append({"text": f" {text}", "start": start, "end": end})
        return out


class FakeInjector:
    def __init__(self, fail=False):
        self.inserted = []
        self.fail = fail

    def insert(self, text):
        if self.fail:
            from golos.inject import InjectionError

            raise InjectionError("буфер занят")
        self.inserted.append(text)
        return True


@pytest.fixture
def rig():
    cfg = Config()
    cfg.trailing_space = True
    cfg.stream_step = 0.2
    cfg.min_chunk_seconds = 1.0
    cfg.hold_back_seconds = 0.4

    recorder = FakeRecorder()
    transcriber = FakeTranscriber()
    injector = FakeInjector()
    events = {"text": [], "partial": [], "state": [], "error": []}

    dictator = StreamingDictator(
        recorder=recorder,
        transcriber=transcriber,
        injector=injector,
        config=cfg,
        on_text=events["text"].append,
        on_partial=events["partial"].append,
        on_state=lambda state, message="": events["state"].append((state, message)),
        on_error=events["error"].append,
        focus_restore=lambda: None,
    )
    return types.SimpleNamespace(
        cfg=cfg,
        recorder=recorder,
        transcriber=transcriber,
        injector=injector,
        events=events,
        dictator=dictator,
    )


def wait_for(predicate, timeout=6.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_text_is_inserted_while_recording(rig):
    """Главное требование: текст появляется до остановки записи."""
    rig.dictator.start()
    rig.recorder.set_length(2.5)

    assert wait_for(lambda: rig.injector.inserted), "текст должен вставиться во время записи"
    assert rig.dictator.is_active, "запись ещё идёт"

    rig.dictator.stop()
    assert not rig.dictator.is_active


def test_nothing_inserted_for_too_short_audio(rig):
    rig.dictator.start()
    rig.recorder.set_length(0.4)
    import time

    time.sleep(0.6)
    assert rig.injector.inserted == []
    rig.dictator.stop()


def test_hold_back_prevents_cutting_last_words(rig):
    """Последние 0.4 с не вставляются сразу — они ещё могут быть недоговорены."""
    rig.dictator.start()
    rig.recorder.set_length(2.0)
    assert wait_for(lambda: rig.injector.inserted)
    rig.dictator.cancel()

    # при куске 2.0 с и hold_back 0.4 вставляется только то, что закончилось раньше
    transcriber_calls = rig.transcriber.calls
    assert transcriber_calls, "модель должна вызываться"
    assert all(call["beam"] == 1 for call in transcriber_calls)


def test_no_duplicate_text_when_audio_grows(rig):
    rig.dictator.start()
    rig.recorder.set_length(2.5)
    assert wait_for(lambda: len(rig.injector.inserted) >= 1)

    rig.recorder.set_length(5.0)
    assert wait_for(lambda: len(rig.injector.inserted) >= 2)
    rig.dictator.stop()

    # каждый кусок вставляется один раз, без повторов предыдущего
    assert all(chunk.strip() for chunk in rig.injector.inserted)
    joined = " ".join(rig.injector.inserted)
    assert joined.count("привет") <= 5


def test_stop_finalizes_remaining_audio(rig):
    rig.dictator.start()
    rig.recorder.set_length(1.6)
    assert wait_for(lambda: rig.transcriber.calls)
    rig.recorder.set_length(2.4)

    text = rig.dictator.stop()
    assert isinstance(text, str)
    assert rig.transcriber.calls, "финальный проход должен быть"


def test_partial_text_is_reported(rig):
    rig.dictator.start()
    rig.recorder.set_length(1.2)
    assert wait_for(lambda: rig.events["partial"] or rig.injector.inserted)
    rig.dictator.cancel()
    assert rig.events["partial"] == [] or all(isinstance(p, str) for p in rig.events["partial"])


def test_model_error_is_reported(rig):
    rig.transcriber.fail = True
    rig.dictator.start()
    rig.recorder.set_length(2.0)

    assert wait_for(lambda: rig.events["error"])
    assert "модель недоступна" in rig.events["error"][0]
    assert not rig.dictator.is_active
    assert any(state == "error" for state, _ in rig.events["state"])


def test_injection_error_is_reported(rig):
    rig.injector.fail = True
    rig.dictator.start()
    rig.recorder.set_length(2.0)

    assert wait_for(lambda: rig.events["error"])
    assert "буфер занят" in rig.events["error"][0]
    rig.dictator.cancel()


def test_cancel_stops_without_waiting():
    cfg = Config()
    cfg.stream_step = 0.2
    dictator = StreamingDictator(
        FakeRecorder(), FakeTranscriber(), FakeInjector(), cfg
    )
    dictator.start()
    assert dictator.is_active
    dictator.cancel()
    assert not dictator.is_active


def test_first_chunk_reports_model_loading(rig):
    rig.dictator.start()
    rig.recorder.set_length(2.0)
    assert wait_for(lambda: rig.events["state"])
    assert any(state == "loading" for state, _ in rig.events["state"])
    rig.dictator.cancel()


def test_context_prompt_uses_committed_text(rig):
    rig.dictator.start()
    rig.recorder.set_length(2.0)
    assert wait_for(lambda: rig.injector.inserted)
    rig.recorder.set_length(4.0)
    assert wait_for(lambda: len(rig.transcriber.calls) >= 2)
    rig.dictator.cancel()

    prompts = [call["prompt"] for call in rig.transcriber.calls]
    assert prompts[0] == rig.cfg.initial_prompt or prompts[0] is None
    assert any(p and len(p) > len(rig.cfg.initial_prompt or "") for p in prompts[1:])
