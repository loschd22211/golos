"""«Стеклянная» плашка: скруглённая панель и прозрачный фон окна.

Почему так, а не «акрилом»:

* ``SetWindowRgn`` режет окно по форме, но **не умеет сглаживать** край;
* ``SetWindowCompositionAttribute`` (акрил/блюр) **игнорирует форму окна** и
  закрашивает весь прямоугольник — именно поэтому вокруг плашки появлялся
  серый квадрат.

Поэтому путь один и он надёжный: фон окна красится «ключевым» цветом, который
Windows показывает прозрачным (``-transparentcolor`` в Tk). Всё, что нарисовано
панелью, остаётся видимым, а углы и поля — исчезают. Прозрачность всей панели
задаётся ``-alpha``.

Всё, что делает Windows, обёрнуто в try/except: на другой ОС и без Pillow
программа работает как раньше, только без скруглений.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Цвет-ключ: его Windows не рисует вообще. Он тёмный, близкий к панели, чтобы
# сглаженные края не давали заметной светлой каймы.
TRANSPARENT_KEY = "#0a0d12"


def _parse_tint(tint) -> tuple[int, int, int]:
    """Цвет в виде "#141922" или (r, g, b) → три числа."""
    if isinstance(tint, (tuple, list)) and len(tint) >= 3:
        return int(tint[0]), int(tint[1]), int(tint[2])
    text = str(tint or "").strip().lstrip("#")
    if len(text) == 6:
        try:
            return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        except ValueError:  # pragma: no cover - мусор в настройках
            pass
    return 20, 25, 34


# --------------------------------------------------------------------- картинка
def panel_image(
    width: int,
    height: int,
    radius: int = 22,
    tint="#1b2231",
    top_alpha: int = 244,
    bottom_alpha: int = 252,
    border_alpha: int = 60,
):
    """Панель «матовое стекло»: вертикальный градиент, блик и тонкая рамка.

    Возвращает ``PIL.Image`` в RGBA или ``None``, если Pillow недоступен.
    Углы полностью прозрачные — их и «съедает» ключевой цвет окна.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # pragma: no cover - Pillow необязателен
        log.debug("Pillow недоступен, панель не рисуется: %s", exc)
        return None

    width = max(8, int(width))
    height = max(8, int(height))
    radius = max(0, min(int(radius), height // 2))
    red, green, blue = _parse_tint(tint)

    # Градиент сверху вниз: сверху светлее — так панель выглядит объёмной.
    gradient = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(gradient)
    for y in range(height):
        part = y / max(1, height - 1)
        alpha = int(top_alpha + (bottom_alpha - top_alpha) * part)
        draw.line([(0, y), (width, y)], fill=(red, green, blue, alpha))

    # Маска со скруглёнными углами. Рисуем в 4× и уменьшаем — края гладкие.
    scale = 4
    mask = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width * scale - 1, height * scale - 1],
        radius=radius * scale,
        fill=255,
    )
    mask = mask.resize((width, height), Image.LANCZOS)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel.paste(gradient, (0, 0), mask)

    # Блик сверху: плавно гаснет к середине — панель похожа на стекло.
    if height > 6:
        shine = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(shine)
        horizon = max(2, int(height * 0.55))
        for y in range(horizon):
            part = y / max(1, horizon - 1)
            alpha = int(28 * (1.0 - part) ** 2)
            if alpha:
                draw.line([(0, y), (width, y)], fill=(255, 255, 255, alpha))
        panel.alpha_composite(
            Image.composite(shine, Image.new("RGBA", (width, height)), mask)
        )

    # Рамка: светлая линия по краю, без неё панель сливается с фоном.
    frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle(
        [0, 0, width - 1, height - 1],
        radius=radius,
        outline=(255, 255, 255, border_alpha),
        width=1,
    )
    panel.alpha_composite(frame)
    return panel


# ------------------------------------------------------------------ окно (Tk)
def use_transparent_key(root, key: str = TRANSPARENT_KEY) -> bool:
    """Сделать цвет ``key`` полностью прозрачным (Windows).

    Возвращает True, если система это умеет — тогда фон окна и холста нужно
    покрасить ровно в этот цвет.
    """
    try:
        root.attributes("-transparentcolor", key)
        return True
    except Exception as exc:
        log.info("Прозрачный фон окна недоступен: %s", exc)
        return False


def set_opacity(root, opacity: float, minimum: float = 0.55) -> bool:
    """Прозрачность всей панели (1.0 — непрозрачная).

    Ниже 0.55 текст становится плохо читаемым, поэтому ограничиваем.
    """
    value = max(minimum, min(1.0, float(opacity)))
    try:
        root.attributes("-alpha", value)
        return True
    except Exception as exc:  # pragma: no cover - в тестах и на экзотике
        log.debug("Прозрачность не применилась: %s", exc)
        return False


def apply_glass(root, opacity: float = 0.94, key: str = TRANSPARENT_KEY) -> dict:
    """Применить прозрачный фон и общую прозрачность панели."""
    report = {
        "transparent": use_transparent_key(root, key),
        "opacity": set_opacity(root, opacity),
    }
    log.debug("Вид плашки: %s", report)
    return report
