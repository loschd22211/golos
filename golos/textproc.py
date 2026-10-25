"""Постобработка распознанного текста.

Whisper уже расставляет знаки препинания, но выдаёт лишние пробелы перед ними,
артефакты вроде «(музыка)» и иногда склеивает кириллицу с латиницей.
"""

from __future__ import annotations

import re

from .config import Config

# Не-речевые пометки, которые модель иногда вставляет: (музыка), [BLANK_AUDIO] и т.п.
_NON_SPEECH = re.compile(
    r"[\(\[\{]\s*(?:"
    r"музыка|music|аплодисменты|applause|смех|laughter|тишина|silence|"
    r"шум|noise|звук[а-я]*|blank_audio|inaudible|неразборчиво|"
    r"продолжение следует|субтитры[^\)\]\}]*"
    r")[^\)\]\}]*[\)\]\}]",
    re.IGNORECASE,
)

# Служебные токены Whisper, которые не являются словами.
_TOKENS = re.compile(r"</?\|[^>]*\|>|<\|[^|]*\|>")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2190-\u21FF\u2B00-\u2BFF]"
)

_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a]+")
_NEWLINES = re.compile(r"\s*\n+\s*")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?…%\)\]\}])")
_SPACE_AFTER_OPEN = re.compile(r"([\(\[])\s+")
_MULTI_PUNCT = re.compile(r"([,.!?])(?:\s*\1)+")

# Склейка алфавитов: «включиPython» -> «включи Python», «запустиAPI» -> «запусти API».
_CYR_THEN_LAT = re.compile(r"(?<=[а-яё])(?=[A-Za-z])")
_LAT_THEN_CYR = re.compile(r"(?<=[A-Za-z])(?=[а-яё])")

# Слова-паразиты (только если включено в настройках).
_FILLERS = re.compile(
    r"(?<![\w-])(?:э+|эм+|мм+|аа+|uh+|um+|erm+|hmm+)(?![\w-])[,.]?\s*",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def clean_text(text: str, config: Config | None = None, **overrides) -> str:
    """Привести распознанный текст в человеческий вид.

    Параметры можно переопределить через ``**overrides`` (capitalize,
    trailing_space, remove_fillers, replacements).
    """
    cfg = config or Config()
    capitalize = overrides.get("capitalize", cfg.capitalize)
    trailing_space = overrides.get("trailing_space", cfg.trailing_space)
    remove_fillers = overrides.get("remove_fillers", cfg.remove_fillers)
    replacements = overrides.get("replacements", cfg.replacements)

    if not text:
        return ""

    result = str(text)
    result = _TOKENS.sub(" ", result)
    result = _NON_SPEECH.sub(" ", result)
    result = _EMOJI.sub(" ", result)
    result = _NEWLINES.sub(" ", result)
    result = _SPACES.sub(" ", result).strip()

    result = _SPACE_BEFORE_PUNCT.sub(r"\1", result)
    result = _SPACE_AFTER_OPEN.sub(r"\1", result)

    if remove_fillers:
        result = _FILLERS.sub("", result)

    # Разделяем алфавиты, чтобы английские слова не прилипали к русским.
    result = _CYR_THEN_LAT.sub(" ", result)
    result = _LAT_THEN_CYR.sub(" ", result)

    # «?» + «!» оставляем, а «,,,» и «...» схлопываем (многоточие — в «…»).
    result = result.replace("...", "…")
    result = _MULTI_PUNCT.sub(r"\1", result)

    result = apply_replacements(result, replacements)
    result = _SPACES.sub(" ", result).strip()

    if not result:
        return ""

    if capitalize:
        result = capitalize_sentences(result)
        # Первая буква фрагмента — заглавная.
        if result and result[0].isalpha():
            result = result[0].upper() + result[1:]

    if trailing_space and not result.endswith(" "):
        result += " "

    return result


def capitalize_sentences(text: str) -> str:
    """Сделать заглавной первую букву каждого предложения."""
    parts = _SENTENCE_SPLIT.split(text)
    out: list[str] = []
    for part in parts:
        stripped = part.lstrip()
        if stripped and stripped[0].isalpha():
            lead = part[: len(part) - len(stripped)]
            out.append(lead + stripped[0].upper() + stripped[1:])
        else:
            out.append(part)
    return " ".join(p for p in out if p != "")


def apply_replacements(text: str, replacements) -> str:
    """Заменить слова по пользовательскому словарю (регистр сохраняется)."""
    for pair in replacements or []:
        try:
            src, dst = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            continue
        if not src:
            continue
        pattern = re.compile(
            r"(?<![\w-])" + re.escape(str(src)) + r"(?![\w-])", re.IGNORECASE
        )
        text = pattern.sub(lambda _m, d=str(dst): d, text)
    return text


def preview(text: str, limit: int = 60) -> str:
    """Короткая строка для уведомления в трее."""
    flat = _SPACES.sub(" ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


# --------------------------------------------------- галлюцинации распознавания
# Whisper на тишине, шуме и обрывках фраз «вспоминает» титры и заставки из
# обучающих данных. По-русски это чаще всего «Редактор субтитров А.Семкин
# Корректор В.Сухиашвили», «Продолжение следует», «Спасибо за просмотр».
_HALLUCINATION_MARKERS = (
    "редактор субтитров",
    "корректор",
    "субтитры сделал",
    "субтитры подготовил",
    "продолжение следует",
    "спасибо за просмотр",
    "спасибо за внимание",
    "подписывайтесь на канал",
    "подпишись на канал",
    "ставьте лайки",
    "смотрите также",
    "dimatorzok",
    "семкин",
    "сухиашвили",
    "продолжение в следующей серии",
    "редактор:",
    "корректор:",
)

# Целый кусок, состоящий только из такого «слова», — почти наверняка артефакт.
_HALLUCINATION_ONLY = {
    "музыка", "music", "аплодисменты", "applause", "смех", "laughter",
    "тишина", "silence", "шум", "noise", "субтитры", "продолжение",
    "аминь", "amen", "конец", "end", "you", "thank you", "thanks",
    "спасибо", "пока", "bye", "музыка.",
}


def _squash(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def looks_like_hallucination(text: str) -> bool:
    """Похоже ли на «выдумку» модели, а не на речь пользователя."""
    plain = _squash(text).strip(" .,!?…-—:;\"'()[]{}")
    if not plain:
        return True
    if plain in _HALLUCINATION_ONLY:
        return True
    return any(marker in plain for marker in _HALLUCINATION_MARKERS)


def drop_hallucinations(text: str) -> str:
    """Убрать из готового текста куски-выдумки (без изменения остального)."""
    if not text or not text.strip():
        return ""
    if looks_like_hallucination(text):
        return ""
    return text
