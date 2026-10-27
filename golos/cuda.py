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

# Пакеты PyPI, внутри которых лежат нужные библиотеки. Точки входа пишем
# списком: у разных версий пакета DLL лежат то в ``bin``, то в ``lib``.
PACKAGE_DIRS = (
    ("nvidia", "cublas", "bin"),
    ("nvidia", "cublas", "lib"),
    ("nvidia", "cudnn", "bin"),
    ("nvidia", "cudnn", "lib"),
)
# Что именно ищем: без этих файлов ctranslate2 не поднимет CUDA.
NEEDED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
DLL_GLOBS = ("nvidia/*/bin", "nvidia/*/lib", "nvidia/*/*/bin", "nvidia/*/*/lib")
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
        # Страховка от нестандартной раскладки пакетов: ищем каталоги с DLL.
        for pattern in DLL_GLOBS:
            for path in root.glob(pattern):
                if path.is_dir() and path not in dirs:
                    dirs.append(path)
    return dirs


def has_cublas(dirs: list[Path] | None = None) -> bool:
    """Лежит ли cublas64_12.dll в найденных папках (или рядом с Python)."""
    checked = dirs if dirs is not None else candidate_dirs()
    for path in checked:
        try:
            names = {item.name.lower() for item in path.iterdir()}
        except OSError:  # pragma: no cover
            continue
        if any(name in names for name in (d.lower() for d in NEEDED_DLLS)):
            return True
    return False


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
        if has_cublas([Path(item) for item in found]):
            log.info("Библиотеки CUDA подключены из pip: %s", ", ".join(found))
        else:
            log.warning(
                "Папки nvidia найдены, но cublas64_12.dll в них нет: %s. "
                "Поставьте библиотеки заново: %s",
                ", ".join(found),
                pip_hint(),
            )
    else:
        log.info(
            "Библиотеки CUDA из pip не найдены (видеокарта останется без "
            "ускорения). Установить: %s",
            pip_hint(),
        )
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
