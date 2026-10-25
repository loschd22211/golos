"""Стеклянный вид плашки: панель, прозрачность, скругление, размытие."""

import sys

import pytest

from golos import glass


class FakeRoot:
    def __init__(self):
        self.attributes_set = {}
        self.idletasks = 0

    def attributes(self, name, value=None):
        if value is not None:
            self.attributes_set[name] = value

    def update_idletasks(self):
        self.idletasks += 1

    def winfo_id(self):
        return 0


# ----------------------------------------------------------------- панель
def test_panel_image_has_overlay_size():
    pytest.importorskip("PIL")
    image = glass.panel_image(348, 96, radius=22)
    assert image is not None
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


# ------------------------------------------------------------------ окно
def test_windows_only_functions_are_safe_elsewhere(monkeypatch):
    monkeypatch.setattr(glass, "_IS_WINDOWS", False)
    root = FakeRoot()
    assert glass.round_window(root, 20) is False
    assert glass.enable_blur(root) is False
    report = glass.apply_glass(root, opacity=0.9)
    assert report["rounded"] is False and report["blur"] is False


def test_opacity_is_clamped():
    root = FakeRoot()
    assert glass.set_opacity(root, 0.93) is True
    assert root.attributes_set["-alpha"] == pytest.approx(0.93)
    glass.set_opacity(root, 5.0)
    assert root.attributes_set["-alpha"] == 1.0
    glass.set_opacity(root, 0.0)
    assert root.attributes_set["-alpha"] == 0.35


def test_opacity_errors_do_not_crash():
    class Broken(FakeRoot):
        def attributes(self, name, value=None):
            raise RuntimeError("нет окна")

    assert glass.set_opacity(Broken(), 0.9) is False


def test_apply_glass_reports_result(monkeypatch):
    monkeypatch.setattr(glass, "_IS_WINDOWS", False)
    report = glass.apply_glass(FakeRoot(), radius=22, opacity=0.8, blur=False)
    assert set(report) == {"opacity", "rounded", "region", "blur"}
    assert report["opacity"] is True


# ------------------------------- серый квадрат: акрил без скругления (1.4.1)
def _fake_root(_window_handle=1234):
    return FakeRoot()


def test_blur_is_not_enabled_when_rounding_fails(monkeypatch):
    """Акрил красит весь прямоугольник — без скругления это «серый квадрат»."""
    monkeypatch.setattr(glass, "_IS_WINDOWS", True)
    monkeypatch.setattr(glass, "round_window", lambda root, radius=20: False)
    enabled = []
    disabled = []
    monkeypatch.setattr(glass, "enable_blur", lambda *a, **k: enabled.append(1) or True)
    monkeypatch.setattr(glass, "disable_blur", lambda *a, **k: disabled.append(1) or True)

    report = glass.apply_glass(FakeRoot(), blur=True)

    assert report["rounded"] is False
    assert report["blur"] is False
    assert enabled == [], "размытие нельзя включать, если углы не срезаны"
    assert disabled == [1], "прежнее размытие нужно снять"


def test_blur_is_enabled_after_rounding(monkeypatch):
    monkeypatch.setattr(glass, "_IS_WINDOWS", True)
    monkeypatch.setattr(glass, "round_window", lambda root, radius=20: True)
    monkeypatch.setattr(glass, "enable_blur", lambda *a, **k: True)
    monkeypatch.setattr(glass, "has_region", lambda root: True)
    report = glass.apply_glass(FakeRoot(), blur=True)
    assert report == {"opacity": True, "rounded": True, "region": True, "blur": True}


def test_blur_is_off_by_default(monkeypatch):
    monkeypatch.setattr(glass, "_IS_WINDOWS", True)
    monkeypatch.setattr(glass, "round_window", lambda root, radius=20: True)
    called = []
    monkeypatch.setattr(glass, "enable_blur", lambda *a, **k: called.append(1) or True)
    monkeypatch.setattr(glass, "disable_blur", lambda *a, **k: True)
    report = glass.apply_glass(FakeRoot())
    assert report["blur"] is False and called == []


def test_has_region_is_safe_outside_windows(monkeypatch):
    monkeypatch.setattr(glass, "_IS_WINDOWS", False)
    assert glass.has_region(FakeRoot()) is False


def test_config_defaults_to_no_blur():
    from golos.config import Config

    cfg = Config()
    assert cfg.overlay_glass is False, "акрил по умолчанию выключен"
    assert 0.35 <= cfg.overlay_opacity <= 1.0
