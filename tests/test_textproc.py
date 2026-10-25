from golos.config import Config
from golos.textproc import (
    apply_replacements,
    capitalize_sentences,
    clean_text,
    collapse_repeats,
    looks_like_hallucination,
    looks_like_loop,
    preview,
    strip_junk_chars,
)


def make_config(**kwargs) -> Config:
    cfg = Config()
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def test_empty_text():
    assert clean_text("", Config()) == ""
    assert clean_text("   ", Config()) == ""
    assert clean_text(None, Config()) == ""  # type: ignore[arg-type]


def test_removes_whisper_tokens_and_noise():
    cfg = make_config(trailing_space=False)
    assert clean_text("<|startoftranscript|> привет", cfg) == "Привет"
    assert clean_text("привет (музыка)", cfg) == "Привет"
    assert clean_text("[BLANK_AUDIO]", cfg) == ""
    assert clean_text("текст 🎤 ещё", cfg) == "Текст ещё"


def test_spaces_around_punctuation():
    cfg = make_config(trailing_space=False, capitalize=False)
    assert clean_text("привет , как дела ?", cfg) == "привет, как дела?"


def test_splits_mixed_alphabet():
    cfg = make_config(trailing_space=False, capitalize=False)
    assert clean_text("включиPython и зайдинаGitHub", cfg) == "включи Python и зайдина GitHub"


def test_trailing_space_and_capitalization():
    cfg = make_config(trailing_space=True, capitalize=True)
    result = clean_text("привет. как дела? хорошо", cfg)
    assert result == "Привет. Как дела? Хорошо "
    assert result.endswith(" ")


def test_trailing_space_can_be_disabled():
    cfg = make_config(trailing_space=False)
    assert clean_text("привет", cfg) == "Привет"


def test_mixed_russian_english_kept_as_is():
    cfg = make_config(trailing_space=False)
    text = clean_text("Сделай commit и push в GitHub репозиторий", cfg)
    assert "commit" in text and "push" in text and "GitHub" in text
    assert "Сделай" in text


def test_fillers_optional():
    cfg = make_config(trailing_space=False, capitalize=False)
    assert "э" not in clean_text("э привет", make_config(trailing_space=False, capitalize=False, remove_fillers=True))
    with_fillers = clean_text("э привет", cfg)
    assert with_fillers == "э привет"


def test_replacements_case_insensitive_whole_words():
    pairs = [["питон", "Python"], ["гитхаб", "GitHub"]]
    assert apply_replacements("питон и гитхаб", pairs) == "Python и GitHub"
    assert apply_replacements("Питон", pairs) == "Python"
    # часть слова не меняется
    assert apply_replacements("питончик", pairs) == "питончик"


def test_multiple_punctuation_collapsed():
    cfg = make_config(trailing_space=False, capitalize=False)
    assert clean_text("да ,,, нет", cfg) == "да, нет"
    assert clean_text("ну ...", cfg) == "ну…"


def test_capitalize_sentences():
    assert capitalize_sentences("привет. как дела? хорошо!") == "Привет. Как дела? Хорошо!"


def test_preview_truncates():
    assert preview("короткий текст") == "короткий текст"
    long = "слово " * 40
    assert len(preview(long, 20)) <= 20
    assert preview(long, 20).endswith("…")




# ------------------------------------------- мусор и зацикливания (1.3.1)
def test_junk_characters_are_removed():
    """«〬Ok. It's good.» — в журнале пользователя такие значки шли в текст."""
    cfg = make_config(trailing_space=False)
    assert clean_text("〬Ok. It's good. 〬", cfg) == "Ok. It's good."
    assert strip_junk_chars("привет\u200b мир") == "привет мир"


def test_useful_symbols_survive():
    cfg = make_config(trailing_space=False, capitalize=False)
    assert clean_text("цена 100$ +5% и 30°", cfg) == "цена 100$ +5% и 30°"


def test_elongated_letters_are_collapsed():
    cfg = make_config(trailing_space=False)
    assert clean_text("Аллоууууууууу", cfg) == "Аллоууу"
    assert collapse_repeats("да") == "да"          # обычное слово не трогаем
    assert collapse_repeats("нууу") == "нууу"      # три буквы — это речь


def test_repeated_words_are_collapsed():
    cfg = make_config(trailing_space=False, capitalize=False)
    assert clean_text("Приём, приём, приём, приём!", cfg) == "Приём!"
    assert collapse_repeats("очень приятно") == "очень приятно"


def test_loop_is_treated_as_hallucination():
    """Модель «залипает»: это не слова пользователя, вставлять нельзя."""
    assert looks_like_loop("Аллоуууууууууууу")
    assert looks_like_loop("Приём, приём, приём, приём")
    assert looks_like_hallucination("Аллоуууууууууууу")
    assert not looks_like_loop("Привет, как дела?")
    assert not looks_like_hallucination("Привет, как дела?")
