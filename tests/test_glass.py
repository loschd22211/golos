"""Стеклянная плашка: скруглённая панель и прозрачный фон окна."""

import sys

import pytest

from golos import glass


class FakeRoot:
    def __init__(self):
        self.attributes_set = {}

    def attributes(self, name, value=None):
        if value is not None:
            self.attributes_set[name] = value


# ----------------------------------------------------------------- панель
def test_panel_image_has_overlay_size():
    pytest.importorskip("PIL")
    image = glass.panel_image(348, 96, radius=22)
    assert image.size == (348, 96)
    assert image.mode == "RGBA"


def test_panel_corners_are_transparent():
    pytest.importorskip("PIL")
    image = glass.panel_image(348, 96, radius=22)
    assert image.getpixel((0, 0))[3] == 0, "углы должны быть прозрачными"
    assert image.getpixel((174, 48))[3] > 200, "середина — плотная"


def test_panel_without_pillow_is_skipped(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "PIL.Image", None)
    assert glass.panel_image(348, 96) is None


def test_parse_tint_accepts_string_and_tuple():
    assert glass._parse_tint("#141922") == (0x14, 0x19, 0x22)
    assert glass._parse_tint((1, 2, 3)) == (1, 2, 3)
    assert glass._parse_tint("мусор") == (20, 25, 34)


# ------------------------------------------------------- прозрачный фон окна
def test_transparent_key_is_set():
    root = FakeRoot()
    assert glass.use_transparent_key(root, "#0a0d12") is True
    assert root.attributes_set["-transparentcolor"] == "#0a0d12"


def test_transparent_key_reports_failure():
    class Broken(FakeRoot):
        def attributes(self, name, value=None):
            raise RuntimeError("нет такой опции")

    assert glass.use_transparent_key(Broken()) is False


def test_opacity_is_clamped():
    root = FakeRoot()
    assert glass.set_opacity(root, 0.94) is True
    assert root.attributes_set["-alpha"] == pytest.approx(0.94)
    glass.set_opacity(root, 5.0)
    assert root.attributes_set["-alpha"] == 1.0
    glass.set_opacity(root, 0.1)
    assert root.attributes_set["-alpha"] == 0.55, "ниже 0.55 текст не читается"


def test_opacity_errors_do_not_crash():
    class Broken(FakeRoot):
        def attributes(self, name, value=None):
            raise RuntimeError("нет окна")

    assert glass.set_opacity(Broken(), 0.9) is False


def test_apply_glass_reports_result():
    report = glass.apply_glass(FakeRoot(), opacity=0.9)
    assert report == {"transparent": True, "opacity": True}


# ---------------------------------------------- никакого акрила (1.5)
def test_acrylic_is_not_used_at_all():
    """Акрил красит весь прямоугольник окна — источник «серого квадрата».

    В коде не должно остаться ни вызовов акрила, ни регионов: работает только
    прозрачный фон по ключевому цвету.
    """
    source = open(glass.__file__, encoding="utf-8").read()
    for forbidden in ("SetWindowCompositionAttribute(", "CreateRoundRectRgn(",
                      "SetWindowRgn("):
        assert forbidden not in source


def test_config_has_no_blur_setting():
    from golos.config import Config

    cfg = Config()
    assert not hasattr(cfg, "overlay_glass"), "акрила больше нет в настройках"
    assert 0.55 <= cfg.overlay_opacity <= 1.0
