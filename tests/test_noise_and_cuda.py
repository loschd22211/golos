"""Скорость (CUDA из pip) и защита от «фантазий» модели."""

import sys
import types

import numpy as np
import pytest

from golos import cuda
from golos.config import Config
from golos.textproc import drop_hallucinations, looks_like_hallucination
from golos.transcribe import WhisperTranscriber, _is_noise_segment


# --------------------------------------------------------------------- CUDA
def test_candidate_dirs_find_pip_packages(monkeypatch, tmp_path):
    cuda.reset()
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    dirs = [str(path) for path in cuda.candidate_dirs()]

    assert dirs == [str(bin_dir)]


def test_candidate_dirs_empty_without_packages(monkeypatch, tmp_path):
    cuda.reset()
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    assert cuda.candidate_dirs() == []


def test_enable_dlls_outside_windows_is_noop(monkeypatch):
    cuda.reset()
    monkeypatch.setattr(sys, "platform", "linux")
    assert cuda.enable_dlls() == []


def test_enable_dlls_is_cached(monkeypatch, tmp_path):
    cuda.reset()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "/usr/bin")
    calls = []

    def fake_dirs():
        calls.append(1)
        return []

    monkeypatch.setattr(cuda, "candidate_dirs", fake_dirs)

    cuda.enable_dlls()
    cuda.enable_dlls()
    assert calls == [1]
    cuda.reset()


def test_enable_dlls_adds_path_on_windows(monkeypatch, tmp_path):
    cuda.reset()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "/usr/bin")
    lib = tmp_path / "nvidia" / "cudnn" / "bin"
    lib.mkdir(parents=True)
    monkeypatch.setattr(cuda, "candidate_dirs", lambda: [lib])

    found = cuda.enable_dlls()

    assert found == [str(lib)]
    assert cuda.available() is True
    import os

    assert os.environ["PATH"].startswith(str(lib))
    cuda.reset()


def test_pip_hint_mentions_both_packages():
    hint = cuda.pip_hint()
    assert "nvidia-cublas-cu12" in hint and "nvidia-cudnn-cu12" in hint


# ---------------------------------------------------------------- галлюцинации
@pytest.mark.parametrize(
    "text",
    [
        "Редактор субтитров А.Семкин Корректор В.Сухиашвили",
        "Продолжение следует...",
        "Спасибо за просмотр!",
        "Подписывайтесь на канал",
        "музыка",
        "DimaTorzok",
        "   ",
    ],
)
def test_known_hallucinations_are_detected(text):
    assert looks_like_hallucination(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Привет, как дела?",
        "Открой GitHub и запусти Docker",
        "Понимаешь.",
        "Спасибо, что помог",
        "Сделай commit и push",
    ],
)
def test_normal_speech_is_kept(text):
    assert looks_like_hallucination(text) is False
    assert drop_hallucinations(text) == text


def test_drop_hallucinations_clears_junk():
    assert drop_hallucinations("Редактор субтитров А.Семкин") == ""


class Segment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=-0.2):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


def test_noise_segment_uses_speech_probabilities():
    assert _is_noise_segment(Segment("привет", 0.1, -0.2), "привет") is False
    # модель уверена, что речи нет, и в тексте не уверена
    assert _is_noise_segment(Segment("что-то", 0.9, -1.5), "что-то") is True
    # титры из обучающих данных
    assert _is_noise_segment(Segment("Корректор В.Сухиашвили"), "Корректор В.Сухиашвили") is True


# ------------------------------------------------------- параметры распознавания
class FakeInfo:
    language = "ru"


class FakeSegment:
    def __init__(self, text):
        self.text = text
        self.start = 0.0
        self.end = 1.0
        self.words = []
        self.no_speech_prob = 0.0
        self.avg_logprob = -0.2


def install_engine(monkeypatch, segments):
    module = types.ModuleType("faster_whisper")
    captured = {}

    class Model:
        def transcribe(self, audio, **params):
            captured.update(params)
            return iter(segments), FakeInfo()

    def WhisperModel(name, **kwargs):
        return Model()

    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return captured


def test_streaming_params_have_anti_hallucination_filters(monkeypatch, tmp_path):
    captured = install_engine(monkeypatch, [FakeSegment("привет")])
    transcriber = WhisperTranscriber(Config(), tmp_path)

    transcriber.segments(np.full(16000, 0.1, dtype=np.float32))

    assert captured["vad_filter"] is True
    assert captured["no_speech_threshold"] == 0.7
    assert captured["log_prob_threshold"] == -1.0
    assert captured["compression_ratio_threshold"] == 2.4
    assert captured["temperature"] == 0.0
    assert captured["vad_parameters"]["min_silence_duration_ms"] == 300
    # Музыка/гул не должны проходить как речь
    assert captured["vad_parameters"]["threshold"] >= 0.6


def test_streaming_drops_hallucinated_segments(monkeypatch, tmp_path):
    install_engine(
        monkeypatch,
        [FakeSegment("Редактор субтитров А.Семкин Корректор В.Сухиашвили")],
    )
    transcriber = WhisperTranscriber(Config(), tmp_path)

    assert transcriber.segments(np.full(16000, 0.1, dtype=np.float32)) == []


def test_silence_threshold_configurable():
    assert Config().silence_threshold == pytest.approx(0.004)
    assert Config.from_dict({"silence_threshold": 0.05}).silence_threshold == pytest.approx(0.05)
    # мусор в конфиге не ломает приложение
    assert Config.from_dict({"silence_threshold": "abc"}).silence_threshold == pytest.approx(0.004)
    assert Config.from_dict({"silence_threshold": -1}).silence_threshold == pytest.approx(0.0)
