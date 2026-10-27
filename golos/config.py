"""Настройки приложения и пути к пользовательским данным."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import APP_NAME

# Модели Whisper (чем больше — тем точнее и медленнее).
MODELS = ("auto", "tiny", "base", "small", "medium", "large-v3")
MODEL_LABELS = {
    "tiny": "tiny — очень быстро, низкая точность",
    "base": "base — быстро, слабая точность",
    "small": "small — быстрый баланс",
    "medium": "medium — точнее, рекомендуется",
    "large-v3": "large-v3 — максимум точности, нужен мощный ПК/GPU",
}

LANGUAGES = {
    "ru": "Русский (по умолчанию)",
    "auto": "Авто — определять по речи",
    "en": "Только английский",
}

INSERT_METHODS = {
    "paste": "Вставка через буфер обмена (быстро)",
    "type": "Печать посимвольно (буква за буквой)",
}

CONFIG_VERSION = 4

DEVICES = ("auto", "cpu", "cuda")
COMPUTE_TYPES = ("auto", "int8", "int8_float16", "float16", "float32")

# Промпт по умолчанию пустой. Длинная подсказка-предложение рано или поздно
# «эхом» вылезает в текст (Whisper дописывает её как реплику) — в журнале
# пользователя так и было: «Смешанная русская и английская …» в каждой вставке.
# Смешанную речь модель и так понимает при language="auto", а нужные термины
# можно перечислить в hotwords.
DEFAULT_INITIAL_PROMPT = ""

# Такой промпт ставили версии 1.0–1.2; при загрузке он вычищается.
LEGACY_INITIAL_PROMPTS = (
    "Смешанная русская и английская речь. Пример: «Открой GitHub, запусти Docker "
    "и проверь Python-скрипт, потом сделай commit и push в репозиторий». "
    "Английские слова записываются латиницей, русские — кириллицей.",
)


@dataclass
class AppPaths:
    """Куда складывать конфиг, логи, историю и модели."""

    data_dir: Path

    @classmethod
    def default(cls) -> "AppPaths":
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return cls(data_dir=base / APP_NAME)

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "golos.log"

    @property
    def history_file(self) -> Path:
        return self.data_dir / "history.txt"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure(self) -> "AppPaths":
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        return self


@dataclass
class Config:
    """Пользовательские настройки. Все значения сериализуются в JSON."""

    # --- горячие клавиши ---
    hotkey: str = "ctrl+alt+space"
    cancel_hotkey: str = "esc"

    # --- потоковый режим (текст появляется по мере речи) ---
    streaming: bool = True
    stream_step: float = 0.35        # как часто проверять новый звук
    min_chunk_seconds: float = 0.8   # минимум звука для очередного куска
    hold_back_seconds: float = 0.3   # сколько придерживать, чтобы не резать слово
    force_commit_seconds: float = 2.5  # после стольких секунд вставляем, не дожидаясь паузы
    # Ниже этой громкости (среднеквадратичной) кусок считается тишиной и в
    # модель не уходит: экономит процессор и убирает «фантомные» фразы.
    silence_threshold: float = 0.004
    # Подтягивать тихую речь до нормальной громкости перед распознаванием:
    # с тихим микрофоном (пик 0.02-0.05) модель почти ничего не слышит.
    normalize_audio: bool = True

    # --- распознавание ---
    # "auto": на видеокарте — medium (точнее), на процессоре — small (быстрее).
    # Конкретное имя фиксирует модель навсегда.
    model: str = "auto"
    device: str = "auto"
    compute_type: str = "auto"
    # Русский по умолчанию. С «auto» на коротких кусках Whisper постоянно
    # принимал русскую речь за английскую и писал «Fiking», «Thank you»,
    # «Ok. It's good.» вместо сказанного. Английские термины («commit», «push»)
    # при language="ru" всё равно распознаются — они в словаре модели.
    language: str = "ru"
    beam_size: int = 3
    vad_filter: bool = True
    condition_on_previous_text: bool = False
    initial_prompt: str = DEFAULT_INITIAL_PROMPT
    hotwords: str = ""
    replacements: list = field(default_factory=list)  # [["питон", "Python"], ...]

    # --- микрофон ---
    input_device: str = ""  # "" = системный по умолчанию, либо часть названия/индекс
    sample_rate: int = 16000
    trim_silence: bool = True
    min_duration: float = 0.35

    # --- вставка текста ---
    insert_method: str = "paste"
    restore_clipboard: bool = True
    paste_delay: float = 0.15
    trailing_space: bool = True
    capitalize: bool = True
    remove_fillers: bool = False

    # --- интерфейс / прочее ---
    # Прозрачность плашки. Фон окна делается прозрачным по ключевому цвету,
    # поэтому углы скруглённые и «квадрата» вокруг нет.
    overlay_opacity: float = 0.94
    # Держать окно-получатель закреплённым на время диктовки: переключение
    # вкладок не уводит текст. Выключите, если мешает возврат фокуса.
    pin_target: bool = True
    sound_feedback: bool = True
    notifications: bool = True
    autostart: bool = False
    # Права администратора: без них Windows не пускает текст в окна, которые
    # запущены от администратора (частая история с браузером). Приложение
    # само попросит UAC при запуске — отключается здесь или ключом --no-admin.
    run_as_admin: bool = True
    preload_model: bool = True
    history_enabled: bool = True
    log_level: str = "INFO"

    # Версия схемы: по ней понятно, какие значения человек выбрал сам, а какие
    # остались от старых версий (см. миграцию языка в load_config).
    config_version: int = CONFIG_VERSION

    def __post_init__(self) -> None:
        self.normalize()

    # ------------------------------------------------------------------ utils
    def normalize(self) -> None:
        """Привести значения к допустимым (после ручной правки JSON)."""
        self.model = _pick(self.model, MODELS, "auto")
        self.device = _pick(self.device, DEVICES, "auto")
        self.compute_type = _pick(self.compute_type, COMPUTE_TYPES, "auto")
        self.language = _pick(self.language, tuple(LANGUAGES), "ru")
        self.insert_method = _pick(self.insert_method, tuple(INSERT_METHODS), "paste")

        self.hotkey = str(self.hotkey or "").strip() or "ctrl+alt+space"
        self.cancel_hotkey = str(self.cancel_hotkey or "").strip()
        self.initial_prompt = str(self.initial_prompt or "")
        self.hotwords = str(self.hotwords or "")
        self.input_device = str(self.input_device or "").strip()

        self.beam_size = _clamp_int(self.beam_size, 1, 10, 3)
        self.stream_step = _clamp_float(self.stream_step, 0.15, 2.0, 0.35)
        self.min_chunk_seconds = _clamp_float(self.min_chunk_seconds, 0.4, 5.0, 0.8)
        self.hold_back_seconds = _clamp_float(self.hold_back_seconds, 0.0, 2.0, 0.3)
        self.force_commit_seconds = _clamp_float(self.force_commit_seconds, 1.0, 15.0, 2.5)
        self.silence_threshold = _clamp_float(self.silence_threshold, 0.0, 0.2, 0.004)
        self.normalize_audio = _as_bool(self.normalize_audio, True)
        self.sample_rate = _clamp_int(self.sample_rate, 8000, 48000, 16000)
        self.min_duration = _clamp_float(self.min_duration, 0.1, 10.0, 0.35)
        self.paste_delay = _clamp_float(self.paste_delay, 0.0, 2.0, 0.15)
        self.overlay_opacity = _clamp_float(self.overlay_opacity, 0.55, 1.0, 0.94)
        self.pin_target = _as_bool(self.pin_target, True)
        self.run_as_admin = _as_bool(self.run_as_admin, True)
        self.replacements = _clean_replacements(self.replacements)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in (data or {}).items():
            if key in known:
                kwargs[key] = value
        return cls(**kwargs)

    def copy(self) -> "Config":
        return Config.from_dict(self.to_dict())


# ---------------------------------------------------------------------- files
def load_config(path: Path | None = None) -> tuple[Config, Path | None]:
    """Прочитать конфиг. Возвращает (config, путь_к_битому_файлу_или_None)."""
    path = Path(path) if path else AppPaths.default().config_file
    if not path.exists():
        return Config(), None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("корневой элемент должен быть объектом")
        config = Config.from_dict(raw)
        if (config.initial_prompt or "").strip() in LEGACY_INITIAL_PROMPTS:
            # Старый промпт подмешивался в текст: чистим.
            config.initial_prompt = ""
        try:
            saved_version = int(raw.get("config_version") or 1)
        except (TypeError, ValueError):
            saved_version = 1
        if saved_version < 3:
            # 1.4.0 включал акрил по умолчанию — он и давал «серый квадрат».
            if abs(float(getattr(config, "overlay_opacity", 0.0)) - 0.93) < 1e-6:
                config.overlay_opacity = 0.94
        if raw.get("language") == "auto" and saved_version < 2:
            # Версии до 1.3.1 всегда писали "auto": на коротких кусках это
            # принимало русскую речь за английскую. Меняем один раз; осознанный
            # выбор "auto" в настройках сохраняется вместе с config_version.
            config.language = "ru"
        if "streaming" not in raw:
            # Конфиг от версии без потокового режима: там по умолчанию стояла
            # тяжёлая модель medium, из-за неё запуск занимал десятки секунд.
            config.streaming = True
            config.model = "small"
            config.beam_size = min(config.beam_size, 3)
        return config, None
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        broken = path.with_name(f"config.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            path.replace(broken)
        except OSError:
            broken = None  # type: ignore[assignment]
        return Config(), broken


def save_config(config: Config, path: Path | None = None) -> Path:
    path = Path(path) if path else AppPaths.default().config_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


# -------------------------------------------------------------------- helpers
def _pick(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in allowed else fallback


def _as_bool(value: Any, fallback: bool) -> bool:
    """Из JSON приходит true/false, но люди пишут и "да"/"0"/"false"."""
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "да", "истина"):
        return True
    if text in ("0", "false", "no", "off", "нет", "ложь", ""):
        return False
    return fallback


def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _clamp_float(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


def _clean_replacements(value: Any) -> list[list[str]]:
    result: list[list[str]] = []
    if not isinstance(value, (list, tuple)):
        return result
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            src, dst = str(item[0]), str(item[1])
            if src:
                result.append([src, dst])
    return result
