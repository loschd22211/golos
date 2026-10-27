"""Качество распознавания: выбор модели, тихий микрофон, выдумки на паузах."""

import sys
import types

import numpy as np
import pytest

from golos import transcribe
from golos.audio import boost_quiet
from golos.config import Config


# ------------------------------------------------------------ выбор модели
def test_auto_model_is_medium_on_gpu_and_small_on_cpu():
    assert transcribe.resolve_model("auto", "cuda") == "medium"
    assert transcribe.resolve_model("auto", "cpu") == "small"


def test_explicit_model_is_respected():
    assert transcribe.resolve_model("base", "cuda") == "base"
    assert transcribe.resolve_model("large-v3", "cpu") == "large-v3"


def test_candidates_start_best_and_end_compatible():
    chain = transcribe.load_candidates("auto", "cuda", "float16")
    assert chain[0] == ("medium", "cuda", "float16")
    assert chain[-1] == ("small", "cpu", "int8")
    # medium не влез в видеопамять — пробуем small, но всё ещё на GPU
    assert ("small", "cuda", "float16") in chain


def test_candidates_on_cpu_are_short():
    chain = transcribe.load_candidates("auto", "cpu", "int8")
    assert chain == [("small", "cpu", "int8")]


def test_auto_model_is_resolved_on_load(monkeypatch, tmp_path):
    loaded = []
    module = types.ModuleType("faster_whisper")

    class Model:
        def __init__(self, name, **kwargs):
            loaded.append((name, kwargs.get("device")))

        def transcribe(self, audio, **kwargs):  # pragma: no cover - не нужен
            return iter([]), types.SimpleNamespace(language="ru")

    module.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(transcribe, "resolve_device", lambda _d: "cuda")

    transcriber = transcribe.WhisperTranscriber(Config(model="auto"), tmp_path)
    transcriber.preload()

    assert loaded[0] == ("medium", "cuda")
    assert transcriber.active_model == "medium"
    assert transcriber.config.model == "auto", "выбор пользователя не переписываем"


# --------------------------------------------------------- тихий микрофон
def test_quiet_speech_is_boosted():
    quiet = (np.sin(np.linspace(0, 100, 16000)) * 0.03).astype(np.float32)
    boosted = boost_quiet(quiet)
    raw = float(np.max(np.abs(quiet)))
    after = float(np.max(np.abs(boosted)))
    assert after > raw * 5, f"тихий звук должен подняться: {raw:.3f} → {after:.3f}"
    assert after <= 0.55 + 1e-6


def test_normal_level_is_untouched():
    normal = (np.sin(np.linspace(0, 100, 16000)) * 0.6).astype(np.float32)
    assert boost_quiet(normal) is not None
    assert np.allclose(boost_quiet(normal), normal)


def test_silence_is_not_amplified():
    silence = np.zeros(16000, dtype=np.float32)
    assert np.allclose(boost_quiet(silence), silence)


def test_boost_is_capped():
    """Нельзя усилить в 100 раз: вместе с речью вылезет шум комнаты."""
    tiny = (np.ones(1000, dtype=np.float32) * 0.001)
    boosted = boost_quiet(tiny, target_peak=0.55, max_gain=4.0)
    assert float(np.max(np.abs(boosted))) <= 0.004 + 1e-6


def test_normalization_can_be_disabled_in_config():
    assert Config().normalize_audio is True
    assert Config(normalize_audio=False).normalize_audio is False


# -------------------------------------------- выдумки в паузах (1.5)
def test_streaming_requests_hallucination_silence_threshold():
    """faster-whisper умеет «перепрыгивать» паузу, где модель выдумывает."""
    params = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            params.update(kwargs)
            return iter([]), types.SimpleNamespace(language="ru")

    transcriber = transcribe.WhisperTranscriber(Config(), None)
    transcriber._model = Model()
    transcriber._model_key = ("small", "cpu", "int8")
    transcriber.segments(np.full(16000, 0.1, dtype=np.float32), Config())

    assert params["hallucination_silence_threshold"] == pytest.approx(2.0)
    assert params["word_timestamps"] is True, "без таймингов слов параметр не работает"
