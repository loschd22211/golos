"""Фантомные слова: «Спокойная музыка», «Поп» и всё, чего человек не говорил."""

import numpy as np
import pytest

from golos.config import Config
from golos.streaming import StreamingDictator, _drop_uncertain, _speech_stats
from golos.textproc import is_short_phantom, looks_like_hallucination


# --------------------------------------------------------------- словари
@pytest.mark.parametrize(
    "text",
    [
        "Спокойная музыка",
        "спокойная музыка играет",
        "Поп",
        "Поп, рок.",
        "Играет музыка",
        "тихая музыка",
        "Продолжение следует",
        "Редактор субтитров А.Семкин",
        "музыка",
        "Music",
    ],
)
def test_music_and_credits_are_phantoms(text):
    assert looks_like_hallucination(text) or is_short_phantom(text)


@pytest.mark.parametrize(
    "text",
    ["Привет, как дела?", "Открой GitHub", "Ну это было", "Попробуй ещё раз", "почему"],
)
def test_real_speech_is_kept(text):
    assert not looks_like_hallucination(text)


def test_short_phantom_only_for_junk_words():
    assert is_short_phantom("Поп.")
    assert not is_short_phantom("Попробуй")
    assert not is_short_phantom("Ну это было")


# ------------------------------------------------- «это вообще речь?»
def test_steady_tone_is_not_speech():
    """Ровный звук (музыка-подушка, гул) — не речь: в модель не отправляем."""
    tone = (np.sin(np.linspace(0, 800, 16000 * 2)) * 0.2).astype(np.float32)
    stats = _speech_stats(tone)
    assert stats["steady"] is True
    assert stats["cv"] < 0.35


def test_speech_with_pauses_is_speech():
    rate = 16000
    audio = np.zeros(rate * 2, dtype=np.float32)
    for start in range(0, audio.size, int(0.25 * rate)):
        end = min(audio.size, start + int(0.16 * rate))
        audio[start:end] = (np.sin(np.linspace(0, 20, end - start)) * 0.2).astype(np.float32)

    stats = _speech_stats(audio)
    assert stats["steady"] is False


def test_noise_fan_is_not_speech():
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(16000 * 2) * 0.02).astype(np.float32)
    assert _speech_stats(noise)["steady"] is True


def test_short_chunk_is_not_judged():
    assert _speech_stats(np.zeros(100, dtype=np.float32))["steady"] is False


# --------------------------------------- фантомы не доходят до вставки
class WordTranscriber:
    def __init__(self, words):
        self.words = words

    def begin_session(self):
        pass

    def segments(self, audio, config=None, initial_prompt=None, beam_size=None,
                 want_words=True):
        duration = audio.size / 16000.0
        return [{
            "text": " ".join(text for text, _prob in self.words),
            "start": 0.0,
            "end": duration,
            "words": [
                {"word": text, "start": 0.0, "end": duration, "probability": prob}
                for text, prob in self.words
            ],
        }]


class FakeInjector:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)
        return True


class FakeRecorder:
    is_recording = False

    def __init__(self):
        self.config = Config()

    def set_config(self, config):
        pass

    def snapshot(self):
        return None


def speech_like(seconds=2.0, amplitude=0.15):
    rate = 16000
    audio = np.zeros(int(seconds * rate), dtype=np.float32)
    for start in range(0, audio.size, int(0.25 * rate)):
        end = min(audio.size, start + int(0.16 * rate))
        audio[start:end] = (np.sin(np.linspace(0, 20, end - start)) * amplitude)
    return audio


def make_stream(words, **cfg):
    config = Config(**cfg)
    injector = FakeInjector()
    stream = StreamingDictator(
        recorder=FakeRecorder(),
        transcriber=WordTranscriber(words),
        injector=injector,
        config=config,
    )
    return stream, injector


def test_uncertain_words_are_dropped():
    """«Поп» на музыке модель выдаёт с низкой уверенностью — его не вставляем."""
    stream, injector = make_stream([("Поп", 0.12), ("привет", 0.93)])

    stream._process_tail(speech_like(2.0), final=True)

    assert injector.inserted == ["Привет "]


def test_everything_uncertain_means_silence_not_text():
    stream, injector = make_stream([("Поп", 0.1), ("музыка", 0.15)])

    stream._process_tail(speech_like(2.0), final=True)

    assert injector.inserted == []


def test_confident_speech_passes():
    stream, injector = make_stream([("Открой", 0.9), ("GitHub", 0.88)])

    stream._process_tail(speech_like(2.0), final=True)

    assert injector.inserted == ["Открой GitHub "]


def test_music_audio_is_not_sent_to_model():
    """Ровный тон: модель не вызывается вообще."""
    calls = []

    class Peek(WordTranscriber):
        def segments(self, audio, config=None, initial_prompt=None, beam_size=None,
                     want_words=True):
            calls.append(1)
            return []

    config = Config()
    stream = StreamingDictator(
        recorder=FakeRecorder(), transcriber=Peek([]),
        injector=FakeInjector(), config=config,
    )
    tone = (np.sin(np.linspace(0, 4000, 16000 * 3)) * 0.2).astype(np.float32)
    stream._process_tail(tone, final=False)

    assert calls == [], "музыку/гул в модель не отправляем"


def test_music_gate_can_be_disabled():
    calls = []

    class Peek(WordTranscriber):
        def segments(self, audio, config=None, initial_prompt=None, beam_size=None,
                     want_words=True):
            calls.append(1)
            return []

    config = Config(skip_non_speech=False)
    stream = StreamingDictator(
        recorder=FakeRecorder(), transcriber=Peek([]),
        injector=FakeInjector(), config=config,
    )
    tone = (np.sin(np.linspace(0, 4000, 16000 * 3)) * 0.2).astype(np.float32)
    stream._process_tail(tone, final=False)

    assert calls == [1]


def test_drop_uncertain_keeps_missing_probability():
    items = [{"text": "раз", "end": 1.0},
             {"text": "два", "end": 2.0, "probability": 0.1}]
    kept, dropped = _drop_uncertain(items, 0.35)
    assert [item["text"] for item in kept] == ["раз"]
    assert dropped == 1
