"""Библиотеки CUDA из pip-пакетов — видеокарта без установки CUDA Toolkit.

faster-whisper на Windows падает с ``Library cublas64_12.dll is not found``,
если в системе нет cuBLAS/cuDNN. Ставить CUDA Toolkit (несколько гигабайт)
не нужно: те же DLL лежат в пакетах ``nvidia-cublas-cu12`` и
``nvidia-cudnn-cu12``. Достаточно показать их Windows через
``os.add_dll_directory`` + ``PATH`` — этим и занимается модуль.

Так делает большинство проектов на faster-whisper (WhisperX, whisper-ctranslate2
и др.), поэтому решение проверенное.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Пакеты PyPI, внутри которых лежат нужные библиотеки.
PACKAGE_DIRS = (
    ("nvidia", "cublas", "bin"),
    ("nvidia", "cudnn", "bin"),
)
PIP_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")

_enabled = False
_found: list[str] = []


def candidate_dirs() -> list[Path]:
    """Где pip обычно раскладывает nvidia-библиотеки."""
    roots: list[Path] = []
    for entry in sys.path:
        if not entry:
            continue
        base = Path(entry)
        if base.is_dir():
            roots.append(base)
    try:  # site-packages текущего интерпретатора — даже если его нет в sys.path
        import site

        for entry in list(site.getsitepackages()) + [site.getusersitepackages()]:
            base = Path(entry)
            if base.is_dir() and base not in roots:
                roots.append(base)
    except Exception:  # pragma: no cover - экзотические сборки Python
        pass

    dirs: list[Path] = []
    for root in roots:
        for parts in PACKAGE_DIRS:
            path = root.joinpath(*parts)
            if path.is_dir() and path not in dirs:
                dirs.append(path)
    return dirs


def enable_dlls() -> list[str]:
    """Разрешить Windows загружать cuBLAS/cuDNN из pip-пакетов.

    Возвращает список найденных папок (пусто — библиотек нет).
    """
    global _enabled, _found
    if _enabled:
        return list(_found)

    _enabled = True
    if sys.platform != "win32":  # pragma: no cover - не Windows
        return []

    found: list[str] = []
    for path in candidate_dirs():
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(path))
            # PATH нужен старым версиям ctranslate2, которые не используют
            # add_dll_directory.
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
            found.append(str(path))
        except Exception as exc:  # pragma: no cover
            log.debug("Не удалось подключить %s: %s", path, exc)

    _found = found
    if found:
        log.info("Библиотеки CUDA подключены из pip: %s", ", ".join(found))
    else:
        log.debug("CUDA-библиотеки из pip не найдены")
    return list(found)


def available() -> bool:
    """Есть ли уже подключённые библиотеки."""
    return bool(_found)


def pip_hint() -> str:
    """Команда установки — для сообщений об ошибке."""
    return "pip install " + " ".join(PIP_PACKAGES)


def reset() -> None:
    """Сбросить кэш (для тестов)."""
    global _enabled, _found
    _enabled = False
    _found = []
