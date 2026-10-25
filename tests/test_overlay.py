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


class FakeApp:  # noqa: D101 - тестовая заглушка приложения
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

    def stop_recording(self):
        self.stopped = getattr(self, "stopped", 0) + 1

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
    assert (ui["module"].WIDTH, ui["module"].HEIGHT) == (348, 96)
    assert root.options["overrideredirect"] is True
    assert root.options["attr:-topmost"] is True
    geometry = root.options["geometry"]
    assert geometry.startswith("348x96+")


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


def test_close_button_quits(ui):
    app = FakeApp()
    root = ui["tk"].Tk()
    called = []
    overlay = ui["module"].Overlay(root, app, on_quit=lambda: called.append(1))

    x0, y0, x1, y1 = ui["module"].button_rects(False)["close"]
    overlay._on_press(event((x0 + x1) // 2, (y0 + y1) // 2))
    overlay._on_release(event((x0 + x1) // 2, (y0 + y1) // 2))

    assert called == [1]


def test_right_click_opens_menu(ui):
    overlay, app, root = make_overlay(ui)
    overlay._on_right_click(event(10, 10, x_root=300, y_root=400))
    assert overlay.menu.options["popup"] == (300, 400)
    labels = [label for kind, label, _ in overlay.menu.entries if kind == "command"]
    assert "Настройки…" in labels and "Выход" in labels
    assert "Скрыть плашку" in labels


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


# --------------------------------------------------- кнопки: попадание и реакции
def test_all_visible_buttons_are_clickable(ui):
    """Рисуем и ловим клики по одним и тем же прямоугольникам.

    Раньше крестик рисовался вверху справа, а зона клика была внизу справа —
    кнопки «не реагировали».
    """
    overlay, app, root = make_overlay(ui)
    for name, rect in ui["module"].button_rects(False).items():
        x0, y0, x1, y1 = rect
        assert overlay._hit(event(x0 + 1, y0 + 1)) == name
        assert overlay._hit(event(x1 - 1, y1 - 1)) == name


def test_stop_button_appears_only_while_recording(ui):
    overlay, app, root = make_overlay(ui)
    assert "stop" not in ui["module"].button_rects(False)
    assert "stop" in ui["module"].button_rects(True)

    overlay.set_state("recording")
    assert "stop" in overlay._buttons()
    texts = [item.get("text") or "" for item in overlay.canvas.items if item["kind"] == "text"]
    assert "■" in texts


def test_stop_button_turns_microphone_off(ui):
    overlay, app, root = make_overlay(ui)
    overlay.set_state("recording")

    x0, y0, x1, y1 = ui["module"].button_rects(True)["stop"]
    overlay._on_press(event((x0 + x1) // 2, (y0 + y1) // 2))
    overlay._on_release(event((x0 + x1) // 2, (y0 + y1) // 2))

    assert getattr(app, "stopped", 0) == 1
    assert app.toggled == 0  # микрофон выключаем именно «стопом», а не toggle


def test_settings_button_opens_settings(ui, monkeypatch):
    overlay, app, root = make_overlay(ui)
    opened = []
    monkeypatch.setattr(ui["module"], "SettingsDialog", lambda *a, **k: opened.append(1))

    x0, y0, x1, y1 = ui["module"].button_rects(False)["settings"]
    overlay._on_press(event((x0 + x1) // 2, (y0 + y1) // 2))
    overlay._on_release(event((x0 + x1) // 2, (y0 + y1) // 2))

    assert opened == [1]


def test_hover_highlights_button(ui):
    overlay, app, root = make_overlay(ui)
    x0, y0, x1, y1 = ui["module"].button_rects(False)["close"]
    overlay._on_motion(event((x0 + x1) // 2, (y0 + y1) // 2))
    assert overlay._hover == "close"

    overlay._on_motion(event(160, 80))
    assert overlay._hover == ""


def test_press_and_release_on_different_buttons_does_nothing(ui):
    overlay, app, root = make_overlay(ui)
    close = ui["module"].button_rects(False)["close"]
    gear = ui["module"].button_rects(False)["settings"]

    overlay._on_press(event((close[0] + close[2]) // 2, (close[1] + close[3]) // 2))
    overlay._on_release(event((gear[0] + gear[2]) // 2, (gear[1] + gear[3]) // 2))

    assert app.toggled == 0


def test_pressed_button_is_drawn_differently(ui):
    overlay, app, root = make_overlay(ui)
    x0, y0, x1, y1 = ui["module"].button_rects(False)["settings"]
    before = len(overlay.canvas.items)
    overlay._on_press(event((x0 + x1) // 2, (y0 + y1) // 2))
    assert overlay._pressed == "settings"
    assert len(overlay.canvas.items) >= before
