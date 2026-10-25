"""Настройка логирования в файл и (опционально) в консоль."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def setup_logging(log_file: Path | None = None, level: str = "INFO", console: bool | None = None) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(file_handler)

    if console is None:
        console = sys.stderr is not None and sys.stderr.isatty()
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(stream)

    if not root.handlers:  # pragma: no cover - pythonw без файла
        root.addHandler(logging.NullHandler())

    # Сторонние библиотеки шумят на INFO.
    for noisy in ("faster_whisper", "ctranslate2", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
