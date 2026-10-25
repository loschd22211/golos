"""«Стеклянный» вид плашки: скруглённые углы, прозрачность и размытие фона.

Три вещи по отдельности:

* ``panel_image()`` — картинка панели (градиент, блик, тонкая рамка) на Pillow;
* ``round_window()`` — скругление самого окна Windows (``SetWindowRgn``), чтобы
  из-за панели не торчали углы;
* ``enable_blur()`` — настоящее размытие того, что за окном (акрил Windows 10/11
  через ``SetWindowCompositionAttribute``).

Всё необязательное: если Pillow нет или система не Windows, плашка просто
рисуется обычным прямоугольником, а программа работает как раньше.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Значения для SetWindowCompositionAttribute (не документированы, но стабильны
# на Windows 10/11 — на них работают все «акриловые» окна).
_WCA_ACCENT_POLICY = 19
_ACCENT_ENABLE_BLURBEHIND = 3
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4


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
    tint="#1a2130",
    top_alpha: int = 202,
    bottom_alpha: int = 224,
    border_alpha: int = 44,
):
    """Панель «матовое стекло»: вертикальный градиент, блик и тонкая рамка.

    Возвращает ``PIL.Image`` в RGBA или ``None``, если Pillow недоступен.
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

    # Блик сверху: плавно гаснет к середине — так панель похожа на стекло,
    # а не на два прямоугольника.
    if height > 6:
        shine = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(shine)
        horizon = max(2, int(height * 0.55))
        for y in range(horizon):
            part = y / max(1, horizon - 1)
            alpha = int(26 * (1.0 - part) ** 2)
            if alpha:
                draw.line([(0, y), (width, y)], fill=(255, 255, 255, alpha))
        panel.alpha_composite(Image.composite(shine, Image.new("RGBA", (width, height)), mask))

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


# ----------------------------------------------------------------- окно Windows
def _window_handle(root) -> int:
    """HWND окна Tk (для overrideredirect это родитель ``winfo_id``)."""
    if not _IS_WINDOWS:  # pragma: no cover - не Windows
        return 0
    try:
        root.update_idletasks()
        for getter in ("winfo_id", "wm_frame"):
            method = getattr(root, getter, None)
            if method is None:
                continue
            hwnd = int(method() or 0)
            if hwnd:
                # Скруглять и размывать нужно верхнее окно: у Tk с
                # overrideredirect настоящий HWND — родитель.
                try:
                    from .winfocus import root_window

                    return root_window(hwnd) or hwnd
                except Exception:  # pragma: no cover
                    return hwnd
    except Exception as exc:  # pragma: no cover
        log.debug("HWND окна получить не удалось: %s", exc)
    return 0


def round_window(root, radius: int = 20) -> bool:
    """Скруглить углы окна по его фактическому размеру (учитывая DPI)."""
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    hwnd = _window_handle(root)
    if not hwnd:
        return False
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        user32 = ctypes.windll.user32
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:  # pragma: no cover
            return False
        region = user32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, int(radius) * 2, int(radius) * 2
        )
        if not region:  # pragma: no cover
            return False
        return bool(user32.SetWindowRgn(hwnd, region, True))
    except Exception as exc:  # pragma: no cover
        log.debug("Скругление окна не удалось: %s", exc)
        return False


def enable_blur(root, tint="#141a26", opacity: float = 0.55, acrylic: bool = True) -> bool:
    """Включить размытие фона за окном (акрил Windows 10/11)."""
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    hwnd = _window_handle(root)
    if not hwnd:
        return False
    try:
        import ctypes

        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ctypes.c_int)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        user32 = ctypes.windll.user32
        setter = getattr(user32, "SetWindowCompositionAttribute", None)
        if setter is None:  # pragma: no cover - Windows 7
            return False

        accent = ACCENTPOLICY()
        accent.AccentState = (
            _ACCENT_ENABLE_ACRYLICBLURBEHIND if acrylic else _ACCENT_ENABLE_BLURBEHIND
        )
        accent.AccentFlags = 2  # рисовать и рамку, и фон
        red, green, blue = _parse_tint(tint)
        alpha = int(max(0.0, min(1.0, float(opacity))) * 255)
        accent.GradientColor = (alpha << 24) | (blue << 16) | (green << 8) | red

        data = WINCOMPATTRDATA()
        data.Attribute = _WCA_ACCENT_POLICY
        data.SizeOfData = ctypes.sizeof(accent)
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.POINTER(ctypes.c_int))
        setter.restype = ctypes.c_bool
        return bool(setter(hwnd, ctypes.byref(data)))
    except Exception as exc:  # pragma: no cover
        log.debug("Размытие фона недоступно: %s", exc)
        return False


def set_opacity(root, opacity: float) -> bool:
    """Прозрачность всего окна (1.0 — непрозрачное)."""
    value = max(0.35, min(1.0, float(opacity)))
    try:
        root.attributes("-alpha", value)
        return True
    except Exception as exc:  # pragma: no cover - в тестах и на экзотике
        log.debug("Прозрачность не применилась: %s", exc)
        return False


def apply_glass(root, radius: int = 20, opacity: float = 0.93, blur: bool = True) -> dict:
    """Применить весь «стеклянный» набор. Возвращает отчёт (для журнала/тестов)."""
    report = {
        "opacity": set_opacity(root, opacity),
        "rounded": round_window(root, radius),
        "blur": enable_blur(root) if blur else False,
    }
    log.debug("Стекло: %s", report)
    return report
