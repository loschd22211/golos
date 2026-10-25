from golos.config import Config
from golos.textproc import apply_replacements, capitalize_sentences, clean_text, preview


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


