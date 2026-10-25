"""Тесты маленького оверлея на поддельном tkinter."""

import types

import pytest

import fake_tk
from golos.config import Config


@pytest.fixture
def ui(monkeypatch):
    fakes = fake_tk.install(monkeypatch)
    import golos.overlay as overlay_module

    monkeypatch.setattr(overlay_module, "list_input_devices",
                        lambda: ["0: Микрофон (Realtek)", "2: USB Headset"])
    fakes["module"] = overlay_module
    return fakes


class FakePaths:
    data_dir = "/tmp/golos"
    config_file = "/tmp/golos/config.json"
    log_file = "/tmp/golos/golos.log"
    history_file = "/tmp/golos/history.txt"
    models_dir = "/tmp/golos/models"


class FakeApp:
    def __init__(self, config=None):
        self.config = config or Config()
        self.paths = FakePaths()
        self.toggled = 0
        self.applied = []
        self.focus_hook = lambda: None
        self.last_text = ""

    def toggle(self):
        self.toggled += 1

    def is_recording(self):
        return False

    def apply_config(self, cfg, persist=True):
        self.applied.append(cfg)
        self.config = cfg
        return []

    def insert_last(self):
        return False


def make_overlay(ui, config=None):
    app = FakeApp(config)
    root = ui["tk"].Tk()
    overlay = ui["module"].Overlay(root, app)
    return overlay, app, root


def event(x, y, x_root=None, y_root=None):
    return types.SimpleNamespace(
        x=x, y=y, x_root=x_root if x_root is not None else x,
        y_root=y_root if y_root is not None else y,
    )


# ------------------------------------------------------------------ построение
def test_overlay_is_tiny_and_borderless(ui):
    overlay, app, root = make_overlay(ui)
    assert (ui["module"].WIDTH, ui["module"].HEIGHT) == (312, 92)
    assert root.options["overrideredirect"] is True
    assert root.options["attr:-topmost"] is True
    geometry = root.options["geometry"]
    assert geometry.startswith("312x92+")


def test_default_position_is_bottom_right(ui):
    overlay, app, root = make_overlay(ui)
    geometry = root.options["geometry"]
    x = int(geometry.split("+")[1])
    y = int(geometry.split("+")[2])
    assert x > 1000 and y > 500


def test_button_is_drawn(ui):
    overlay, app, root = make_overlay(ui)
    kinds = {item["kind"] for item in overlay.canvas.items}
    assert {"oval", "rectangle", "line", "arc", "text"} <= kinds


# --------------------------------------------------------------------- клики
def test_click_on_microphone_starts_dictation(ui):
    overlay, app, root = make_overlay(ui)
    overlay._on_press(event(46, 46))
    overlay._on_release(event(46, 46))
    assert app.toggled == 1


def test_drag_moves_window_and_does_not_toggle(ui):
    overlay, app, root = make_overlay(ui)
    overlay._on_press(event(200, 20, x_root=500, y_root=500))
    overlay._on_drag(event(240, 40, x_root=540, y_root=520))
    overlay._on_release(event(240, 40, x_root=540, y_root=520))
    assert app.toggled == 0
    assert root.options["geometry"].endswith("+140+220")


def test_close_hides_overlay(ui):
    overlay, app, root = make_overlay(ui)
    overlay._on_press(event(300, 80))
    overlay._on_release(event(300, 80))
    assert root.visible is False


def test_right_click_opens_menu(ui):
    overlay, app, root = make_overlay(ui)
    overlay._on_right_click(event(10, 10, x_root=300, y_root=400))
    assert overlay.menu.options["popup"] == (300, 400)
    labels = [label for kind, label, _ in overlay.menu.entries if kind == "command"]
    assert "Настройки…" in labels and "Выход" in labels


def test_menu_quit_calls_callback(ui):
    app = FakeApp()
    root = ui["tk"].Tk()
    called = []
    overlay = ui["module"].Overlay(root, app, on_quit=lambda: called.append(1))
    command = dict((label, cmd) for kind, label, cmd in overlay.menu.entries if kind == "command")
    command["Выход"]()
    assert called == [1]


# --------------------------------------------------------------------- статусы
def test_recording_state_is_red_and_shows_hint(ui):
    overlay, app, root = make_overlay(ui)
    overlay.set_state("recording")
    texts = [item.get("text") or "" for item in overlay.canvas.items if item["kind"] == "text"]
    assert any("Говорите" in t or "слушаю" in t for t in texts)
    # верхняя полоска окрашена в цвет состояния
    assert overlay.canvas.items[1]["fill"] == ui["module"].RED


def test_partial_text_is_visible(ui):
    overlay, app, root = make_overlay(ui)
    overlay.set_state("recording")
    overlay.set_partial("привет как де")
    texts = [item.get("text") or "" for item in overlay.canvas.items if item["kind"] == "text"]
    assert any("привет как де" in t for t in texts)


def test_error_is_visible_and_survives(ui):
    overlay, app, root = make_overlay(ui)
    overlay.set_state("error", "микрофон не найден")
    texts = [item.get("text") or "" for item in overlay.canvas.items if item["kind"] == "text"]
    assert any("микрофон не найден" in t for t in texts)
    assert overlay.error == "микрофон не найден"
    # «!» в углу — подсказка, что есть ошибка
    assert any(t == "!" for t in texts)


def test_error_cleared_on_next_start(ui):
    overlay, app, root = make_overlay(ui)
    overlay.set_state("error", "нет микрофона")
    overlay.set_state("recording")
    assert overlay.error == ""


def test_level_bar_grows(ui):
    overlay, app, root = make_overlay(ui)
    overlay.set_state("recording")
    overlay.set_level(0.0)
    before = len(overlay.canvas.items)
    overlay.set_level(0.9)
    after = len(overlay.canvas.items)
    assert after > before


def test_show_and_hide(ui):
    overlay, app, root = make_overlay(ui)
    overlay.hide()
    assert root.visible is False
    overlay.show()
    assert root.visible is True


# ------------------------------------------------------------------- настройки
def test_settings_dialog_applies_changes(ui):
    overlay, app, root = make_overlay(ui)
    dialog = ui["module"].SettingsDialog(root, app, overlay)

    dialog.model.set("base — быстро")
    dialog.lang.set("Русский")
    dialog.method.set("Посимвольно")
    dialog.mic.set("2: USB Headset")
    dialog.hotkey.set("ctrl+shift+d")
    dialog.apply()

    cfg = app.config
    assert cfg.model == "base"
    assert cfg.language == "ru"
    assert cfg.insert_method == "type"
    assert cfg.input_device == "2"
    assert cfg.hotkey == "ctrl+shift+d"


def test_settings_dialog_keeps_defaults(ui):
    overlay, app, root = make_overlay(ui)
    dialog = ui["module"].SettingsDialog(root, app, overlay)
    dialog.apply()
    assert app.config.model == Config().model
    assert app.config.input_device == ""


def test_settings_shorten_help_text_exists(ui):
    module = ui["module"]
    short = module._short("a" * 100, 20)
    assert len(short) == 20
    assert short.endswith("…")
    tail = module._tail("a" * 100 + " конец", 20)
    assert len(tail) == 20 and tail.startswith("…") and tail.endswith("конец")
