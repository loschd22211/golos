"""Постобработка распознанного текста.

Whisper уже расставляет знаки препинания, но выдаёт лишние пробелы перед ними,
артефакты вроде «(музыка)» и иногда склеивает кириллицу с латиницей.
"""

from __future__ import annotations

import re
import unicodedata

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

# Символы, которые в тексте имеют смысл (единицы, валюты, кавычки, тире).
_KEEP_SYMBOLS = set(
    "+=$€£¥%№&@#*/\\<>^~|_°±×§«»„“”‘’–—…·•©®™"
)
# Категории Unicode, которые считаем «своими»: буквы, цифры, пунктуация,
# диакритика и пробелы. Всё остальное (значки, эмодзи, CJK-закорючки вроде «〬»,
# управляющие и приватные символы) — мусор из обучающих данных Whisper.
_ALLOWED_CATEGORIES = ("L", "N", "P", "Z")
# Диакритика: только привычные диапазоны (é, й и т.п.). Экзотические пометки
# вроде «〬» (U+302C) приходят из обучающих данных мусором.
_MARK_RANGES = (
    (0x0300, 0x036F), (0x0483, 0x0489), (0x0591, 0x05BD), (0x0610, 0x061A),
    (0x064B, 0x065F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF), (0x20D0, 0x20FF),
    (0xFE20, 0xFE2F),
)


def _is_allowed_mark(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in _MARK_RANGES)

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
    result = strip_junk_chars(result)
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
    result = collapse_repeats(result)

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


# --------------------------------------------------- мусор и зацикливания
# Whisper иногда «залипает»: повторяет один символ («Аллоуууууу…») или слово
# («Приём, приём, приём, приём»). В текст это попадать не должно.
_ELONGATED = re.compile(r"(\w)\1{3,}", re.UNICODE)
_REPEAT_WORD = re.compile(
    r"(?<![\w-])([\w'’-]{1,32})(?:[ \t]*[,;.!?…]?[ \t]+\1(?![\w-])){2,}",
    re.IGNORECASE | re.UNICODE,
)
_REPEAT_PHRASE = re.compile(
    r"(?<![\w-])([\w'’-]{1,32}[ \t]+[\w'’-]{1,32})"
    r"(?:[ \t]*[,;.!?…]?[ \t]+\1(?![\w-])){1,}",
    re.IGNORECASE | re.UNICODE,
)
_LOOP_RUN = re.compile(r"(\w)\1{4,}", re.UNICODE)
_LOOP_WORDS = re.compile(
    r"(?<![\w-])([\w'’-]{1,32})(?:[ \t]*[,;.!?…]?[ \t]+\1(?![\w-])){3,}",
    re.IGNORECASE | re.UNICODE,
)


def strip_junk_chars(text: str) -> str:
    """Убрать символы, которых в человеческой речи не бывает."""
    if not text:
        return ""
    out = []
    for ch in text:
        if ch in _KEEP_SYMBOLS or ch.isspace():
            out.append(ch)
            continue
        category = unicodedata.category(ch)[0]
        if category in _ALLOWED_CATEGORIES:
            out.append(ch)
        elif category == "M" and _is_allowed_mark(ch):
            out.append(ch)
    return "".join(out)


def collapse_repeats(text: str) -> str:
    """Схлопнуть зацикливания: «Аллоуууу» → «Аллоууу», «да, да, да» → «да»."""
    if not text:
        return ""
    result = _ELONGATED.sub(lambda m: m.group(1) * 3, text)
    for _ in range(3):
        new = _REPEAT_PHRASE.sub(r"\1", result)
        new = _REPEAT_WORD.sub(r"\1", new)
        if new == result:
            break
        result = new
    return result


def looks_like_loop(text: str) -> bool:
    """Текст — зацикливание модели (повтор символа/слова), а не речь."""
    if not text:
        return False
    return bool(_LOOP_RUN.search(text) or _LOOP_WORDS.search(text))


# --------------------------------------------------- галлюцинации распознавания
# Хвост подсказки-промпта, который Whisper «эхом» дописывает в текст.
_PROMPT_ECHO = (
    "смешанная русская и английская речь",
    "английские слова записываются латиницей",
    "пример: открой github",
)


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
    # Музыка и «радио» в комнате — самый частый источник фантомных фраз:
    # Whisper слышит аккорды и «подписывает» их как трек.
    "спокойная музыка",
    "играет музыка",
    "музыка играет",
    "фоновая музыка",
    "тихая музыка",
    "классическая музыка",
    "поп музыка",
    "поп-музыка",
    "рок музыка",
    "звучит музыка",
    "музыка в наушниках",
    "музыкальный трек",
    "плейлист",
    # Эхо подсказки (initial_prompt) из версий 1.0–1.2.
    *_PROMPT_ECHO,
)

# Целый кусок, состоящий только из такого «слова», — почти наверняка артефакт.
_HALLUCINATION_ONLY = {
    "музыка", "music", "аплодисменты", "applause", "смех", "laughter",
    "тишина", "silence", "шум", "noise", "субтитры", "продолжение",
    "аминь", "amen", "конец", "end", "you", "thank you", "thanks",
    "спасибо", "пока", "bye", "музыка.",
    # Жанры и радио-вставки: на музыке модель выдаёт именно их.
    "поп", "рок", "джаз", "рэп", "хип-хоп", "блюз", "кантри",
    "pop", "rock", "jazz", "rap", "blues", "country",
    "мелодия", "трек", "песня", "song", "radio", "радио",
    "спокойная музыка", "лёгкая музыка", "легкая музыка",
}


def _squash(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def looks_like_hallucination(text: str) -> bool:
    """Похоже ли на «выдумку» модели, а не на речь пользователя."""
    if looks_like_loop(text or ""):
        return True
    plain = _squash(text).strip(" .,!?…-—:;\"'()[]{}")
    if not plain:
        return True
    if plain in _HALLUCINATION_ONLY:
        return True
    return any(marker in plain for marker in _HALLUCINATION_MARKERS)


def is_short_phantom(text: str) -> bool:
    """Короткий кусок без содержания: одно-два слова-жанра или междометия.

    Отдельный «Поп.» на записи с музыкой — не речь, а подпись к аккордам.
    """
    plain = _squash(text).strip(" .,!?…-—:;\"'()[]{}«»")
    if not plain:
        return True
    if plain in _HALLUCINATION_ONLY:
        return True
    words = [word.strip(" .,!?…-—:;\"'()[]{}«»") for word in plain.split()]
    words = [word for word in words if word]
    return bool(words) and len(words) <= 2 and all(
        word in _HALLUCINATION_ONLY for word in words
    )


_WORD_STRIP = " \t\n.,!?…-—:;\"'()[]{}«»"


def _plain_words(text: str) -> list[str]:
    return [
        word.strip(_WORD_STRIP).lower().replace("ё", "е")
        for word in (text or "").replace("\n", " ").split()
        if word.strip(_WORD_STRIP)
    ]


def strip_repeated_prefix(text: str, committed: str, max_words: int = 12) -> str:
    """Убрать начало куска, если оно повторяет конец уже вставленного текста.

    Поток режет запись на куски, и куски перекрываются: хвост предыдущего
    попадает в следующий («Всё нормально. | Всё нормально. Как дела?»). Без
    этой чистки одна и та же фраза уходила в поле ввода десятки раз.
    """
    if not text or not committed:
        return text
    new_words = (text or "").replace("\n", " ").split()
    old_words = (committed or "").replace("\n", " ").split()
    if not new_words or not old_words:
        return text
    new_plain = _plain_words(text)
    old_plain = _plain_words(committed)
    limit = min(len(new_plain), len(old_plain), max(1, int(max_words)))
    for size in range(limit, 0, -1):
        if old_plain[-size:] == new_plain[:size]:
            return " ".join(new_words[size:])
    return text


def _repeat_key(text: str) -> str:
    return _squash(text).strip(" .,!?…-—:;\"'()[]{}«»")


def repeats_recent(text: str, recent: list[str], limit: int = 2) -> bool:
    """Повторялся ли такой кусок в недавних вставках слишком часто.

    Ловит не только повторы подряд («Понимаешь.» каждый кусок), но и «качели»,
    когда модель вставляет одну и ту же фразу через раз.
    """
    key = _repeat_key(text)
    if not key:
        return False
    if len(key.split()) > 6:  # длинные фразы повторяются законно
        return False
    return sum(1 for item in recent if _repeat_key(item) == key) >= limit


def drop_hallucinations(text: str) -> str:
    """Убрать из готового текста куски-выдумки (без изменения остального)."""
    if not text or not text.strip():
        return ""
    if looks_like_hallucination(text):
        return ""
    return text
