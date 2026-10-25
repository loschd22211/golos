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
_ACCENT_DISABLED = 0
_ACCENT_ENABLE_BLURBEHIND = 3
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

# Флаги SetWindowPos
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


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
    top_alpha: int = 232,
    bottom_alpha: int = 248,
    border_alpha: int = 56,
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


def _rect_size(hwnd: int, root) -> tuple[int, int]:
    """Размер окна в тех же координатах, в которых работает SetWindowRgn."""
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width > 1 and height > 1:
                return width, height
    except Exception as exc:  # pragma: no cover
        log.debug("GetWindowRect не сработал: %s", exc)

    # Окно ещё не показано — берём размер из Tk.
    try:
        return int(root.winfo_width()), int(root.winfo_height())
    except Exception:  # pragma: no cover
        return 0, 0


def round_window(root, radius: int = 20) -> bool:
    """Скруглить углы окна.

    ``CreateRoundRectRgn`` возвращает HANDLE (указатель). Если не задать
    ``restype``, ctypes обрежет его до 32 бит, ``SetWindowRgn`` получит мусор и
    вернёт ошибку — окно останется квадратным (ровно это и видел пользователь:
    «серый квадрат» вокруг плашки).
    """
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    hwnd = _window_handle(root)
    if not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        width, height = _rect_size(hwnd, root)
        if width <= 1 or height <= 1:  # pragma: no cover - окно ещё без размера
            return False

        radius = max(0, int(radius))
        user32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        user32.CreateRoundRectRgn.restype = wintypes.HANDLE
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int

        # +1 к правому/нижнему краю: так рекомендуют в документации WinAPI,
        # иначе крайняя строка пикселей не попадает в регион.
        region = user32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2
        )
        if not region:  # pragma: no cover
            log.debug("CreateRoundRectRgn вернул пустой регион")
            return False

        if not user32.SetWindowRgn(hwnd, region, True):
            log.debug("SetWindowRgn не применился")
            return False

        # Без SetWindowPos со SWP_FRAMECHANGED Windows иногда показывает окно
        # со старым (прямоугольным) регионом до следующего изменения размера.
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE,
        )
        return True
    except Exception as exc:  # pragma: no cover
        log.debug("Скругление окна не удалось: %s", exc)
        return False


def has_region(root) -> bool:
    """Проверить, что у окна действительно стоит скруглённый регион.

    ``GetWindowRgn`` возвращает тип региона: 0 — ошибка, 1 — пусто, 2/3 —
    простой/сложный регион. Нужен для журнала: если регион не встал, вокруг
    плашки будет виден прямоугольник.
    """
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    hwnd = _window_handle(root)
    if not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        user32.CreateRectRgn.restype = wintypes.HANDLE
        user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
        user32.GetWindowRgn.restype = ctypes.c_int
        user32.DeleteObject.argtypes = [wintypes.HANDLE]
        user32.DeleteObject.restype = wintypes.BOOL

        region = user32.CreateRectRgn(0, 0, 0, 0)
        if not region:  # pragma: no cover
            return False
        try:
            kind = int(user32.GetWindowRgn(hwnd, region))
        finally:
            user32.DeleteObject(region)
        return kind in (2, 3)
    except Exception as exc:  # pragma: no cover
        log.debug("GetWindowRgn не сработал: %s", exc)
        return False


def clear_region(root) -> bool:
    """Снять скругление (прямоугольное окно) — нужно при смене вида."""
    if not _IS_WINDOWS:  # pragma: no cover
        return False
    hwnd = _window_handle(root)
    if not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        return bool(user32.SetWindowRgn(hwnd, None, True))
    except Exception as exc:  # pragma: no cover
        log.debug("Снять регион не удалось: %s", exc)
        return False


def enable_blur(root, tint="#141a26", opacity: float = 0.42, acrylic: bool = True) -> bool:
    """Включить размытие фона за окном (акрил Windows 10/11).

    Работает только вместе со скруглением: акрил красит **весь прямоугольник**
    окна, и если углы не срезаны регионом, вокруг плашки появляется серый
    квадрат. Поэтому ``apply_glass`` вызывает эту функцию лишь после успешного
    ``round_window``.
    """
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

        setter.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINCOMPATTRDATA)]
        setter.restype = ctypes.c_bool
        return bool(setter(ctypes.c_void_p(hwnd), ctypes.byref(data)))
    except Exception as exc:  # pragma: no cover
        log.debug("Размытие фона недоступно: %s", exc)
        return False


def disable_blur(root) -> bool:
    """Выключить размытие (AccentState = 0), чтобы вернуть обычное окно."""
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
        if setter is None:  # pragma: no cover
            return False

        accent = ACCENTPOLICY()
        accent.AccentState = _ACCENT_DISABLED
        data = WINCOMPATTRDATA()
        data.Attribute = _WCA_ACCENT_POLICY
        data.SizeOfData = ctypes.sizeof(accent)
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.POINTER(ctypes.c_int))
        setter.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINCOMPATTRDATA)]
        setter.restype = ctypes.c_bool
        return bool(setter(ctypes.c_void_p(hwnd), ctypes.byref(data)))
    except Exception as exc:  # pragma: no cover
        log.debug("Выключить размытие не удалось: %s", exc)
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


def apply_glass(root, radius: int = 20, opacity: float = 0.94, blur: bool = False) -> dict:
    """Применить весь «стеклянный» набор. Возвращает отчёт (для журнала/тестов).

    Порядок важен: сначала скругляем окно, и только потом включаем акрил.
    Иначе прозрачные углы панели закрашиваются серым фоном акрила — то самое
    «просто серый квадрат».
    """
    rounded = round_window(root, radius)
    blurred = False
    if blur:
        if rounded:
            blurred = enable_blur(root)
        else:
            log.info(
                "Скругление не сработало — размытие выключено, чтобы не было "
                "серого квадрата вокруг плашки"
            )
            disable_blur(root)
    else:
        disable_blur(root)

    report = {
        "opacity": set_opacity(root, opacity),
        "rounded": rounded,
        "region": has_region(root),
        "blur": blurred,
    }
    log.debug("Стекло: %s", report)
    return report
