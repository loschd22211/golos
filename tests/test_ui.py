"""Тесты единого окна «Голос» на поддельном tkinter."""

import types

import pytest

import fake_tk
from golos.app import IDLE, PAUSED, PROCESSING, RECORDING, ERROR
from golos.config import Config


@pytest.fixture
def ui(monkeypatch):
    fakes = fake_tk.install(monkeypatch)
    import golos.ui as ui_module

    monkeypatch.setattr(
        ui_module, "list_input_devices", lambda: ["0: Микрофон (Realtek)", "2: USB Headset"]
    )
    fakes["module"] = ui_module
    return fakes


class FakePaths:
    data_dir = "/tmp/golos"
    config_file = "/tmp/golos/config.json"
    log_file = "/tmp/golos/golos.log"
    history_file = "/tmp/golos/history.txt"
    models_dir = "/tmp/golos/models"


class FakeRecorder:
    is_recording = False


class FakeInjector:
    def __init__(self):
        self.inserted = []
        self.clipboard = types.SimpleNamespace(set_text=lambda text: setattr(self, "copied", text))

    def apply_config(self, config):
        pass

    def insert(self, text):
        self.inserted.append(text)
        return True


class FakeTranscriber:
    def is_model_downloaded(self, model=None):
        return True


class FakeApp:
    def __init__(self, config=None):
        self.config = config or Config()
        self.paths = FakePaths()
        self.recorder = FakeRecorder()
        self.injector = FakeInjector()
        self.transcriber = FakeTranscriber()
        self.last_text = ""
        self.applied = []

    def toggle(self):
        self.toggled = getattr(self, "toggled", 0) + 1

    def is_recording(self):
        return False

    def apply_config(self, cfg, persist=True):
        self.applied.append(cfg)
        self.config = cfg
        return []

    def insert_last(self):
        if not self.last_text:
            return False
        self.injector.insert(self.last_text)
        return True


def make_window(ui, config=None):
    app = FakeApp(config)
    root = ui["tk"].Tk()
    window = ui["module"].MainWindow(root, app)
    return window, app, root


# ------------------------------------------------------------------ построение
def test_window_builds_with_defaults(ui):
    window, app, root = make_window(ui)
    assert root.options["title"].startswith("Голос")
    assert root.options["geometry"] == "560x700"
    assert window.model_var.get().endswith("medium")
    assert window.lang_var.get() == "auto"
    assert window.mic_var.get() == "Системный по умолчанию"
    assert window.method_var.get() == "Буфер + Ctrl+V"
    assert "Ctrl+Alt+Space" in window.hint_var.get()
    assert window.status_var.get() == "Готов"


def test_record_button_is_drawn(ui):
    window, app, _ = make_window(ui)
    assert window.button.items, "на кнопке должна быть нарисована иконка микрофона"
    kinds = {item["kind"] for item in window.button.items}
    assert {"oval", "rectangle", "line", "arc"} <= kinds


def test_click_on_button_toggles_recording(ui):
    window, app, _ = make_window(ui)
    window.button.event_generate("<Button-1>")
    assert app.toggled == 1


def test_status_colors_survive_all_states(ui):
    window, app, _ = make_window(ui)
    for state in (IDLE, RECORDING, PROCESSING, PAUSED, ERROR):
        window.update_state(state)
        assert window.status_var.get()
        assert window.button.items
    window.update_state(ERROR, "нет микрофона")
    assert "нет микрофона" in window.hint_var.get()


def test_level_draws_only_while_recording(ui):
    window, app, _ = make_window(ui)
    window.update_state(RECORDING)
    window.update_level(0.72)
    filled = [item for item in window.level_bar.items if item["kind"] == "rectangle"]
    assert len(filled) >= 2, "фон + заполнение"
    window.update_level(0.0)
    assert len(window.level_bar.items) == 1


def test_last_text_is_set_and_read_only(ui):
    window, app, _ = make_window(ui)
    window.set_last_text("Привет, это тест на Python")
    assert "Привет" in window.last_text.get("1.0", "end")
    assert window.last_text.options["state"] == "disabled"


def test_copy_and_paste_last(ui):
    window, app, _ = make_window(ui)
    app.last_text = "привет"
    window.copy_last()
    assert app.injector.copied == "привет "
    window.paste_last()
    assert app.injector.inserted == ["привет"]


# ------------------------------------------------------------------- настройки
def test_language_buttons_apply_immediately(ui):
    window, app, _ = make_window(ui)
    window._set_language("en")
    assert window.lang_var.get() == "en"
    assert app.applied and app.applied[-1].language == "en"


def test_model_change_applies(ui):
    window, app, _ = make_window(ui)
    window.model_var.set("Максимум точности · large-v3")
    window._apply()
    assert app.config.model == "large-v3"


def test_toggles_apply(ui):
    window, app, _ = make_window(ui)
    window.trailing_var.set(False)
    window.sound_var.set(False)
    window._apply()
    assert app.config.trailing_space is False
    assert app.config.sound_feedback is False


def test_advanced_is_optional_and_toggles(ui):
    window, app, root = make_window(ui)
    assert window.advanced.packed is False
    window.toggle_advanced()
    assert window.advanced.packed is True
    assert root.options["geometry"] == "560x900"
    window.toggle_advanced()
    assert window.advanced.packed is False
    assert root.options["geometry"] == "560x700"


def test_advanced_fields_are_collected(ui):
    window, app, _ = make_window(ui)
    window.hotkey_var.set("ctrl+shift+d")
    window.cancel_var.set("escape")
    window.beam_var.set(3)
    window.hotwords_var.set("Kubernetes, Docker")
    window._apply()
    cfg = app.config
    assert cfg.hotkey == "ctrl+shift+d"
    assert cfg.cancel_hotkey == "escape"
    assert cfg.beam_size == 3
    assert cfg.hotwords == "Kubernetes, Docker"


def test_bad_number_does_not_crash(ui):
    window, app, _ = make_window(ui)
    window.beam_var.set("мусор")
    window._apply()
    assert app.config.beam_size == 5


def test_loading_does_not_trigger_apply(ui):
    window, app, _ = make_window(ui)
    window._loading = True
    window._apply()
    assert app.applied == []
    window._loading = False


def test_warnings_are_shown(ui):
    window, app, _ = make_window(ui)
    app.apply_config = lambda cfg, persist=True: ["Клавиша занята"]
    window._apply()
    assert ui["messagebox"].calls[-1][0] == "warning"


def test_load_from_config_fills_fields(ui):
    cfg = Config(model="small", language="en", hotkey="f9", insert_method="type",
                 trailing_space=False, insert_device_placeholder=None) if False else Config(
        model="small", language="en", hotkey="f9", insert_method="type", trailing_space=False
    )
    window, app, _ = make_window(ui, cfg)
    assert window.model_var.get() == "Быстрый баланс · small"
    assert window.lang_var.get() == "en"
    assert window.hotkey_var.get() == "f9"
    assert window.method_var.get() == "Посимвольно"
    assert window.trailing_var.get() is False


def test_microphone_choice_saved_as_index(ui):
    window, app, _ = make_window(ui)
    assert "2: USB Headset" in window.mic_combo.values
    window.mic_var.set("2: USB Headset")
    window._apply()
    assert app.config.input_device == "2"


def test_saved_microphone_restored(ui):
    cfg = Config(input_device="2")
    window, app, _ = make_window(ui, cfg)
    assert window.mic_var.get() == "2: USB Headset"


def test_unknown_microphone_falls_back(ui):
    cfg = Config(input_device="77")
    window, app, _ = make_window(ui, cfg)
    assert window.mic_var.get() == "Системный по умолчанию"
    assert window.collect_config().input_device == ""


def test_reset_defaults(ui):
    cfg = Config(model="large-v3", hotkey="f9", language="en")
    window, app, _ = make_window(ui, cfg)
    window.reset_defaults()
    assert window.model_var.get().endswith("medium")
    assert window.lang_var.get() == "auto"
    assert app.config.model == "medium"


# -------------------------------------------------------------- окно и хоткеи
def test_hide_and_show(ui):
    window, app, root = make_window(ui)
    window.hide()
    assert root.visible is False
    window.show()
    assert root.visible is True


def test_capture_hotkey_sets_value(ui):
    window, app, _ = make_window(ui)
    window.capture_hotkey(window.hotkey_var)

    dialogs = [w for w in ui["tk"].Toplevel.created if isinstance(w, ui["tk"].Toplevel)]
    handler = dialogs[-1].bindings["<KeyPress>"]
    handler(types.SimpleNamespace(keysym="d", state=0x4))
    assert window.hotkey_var.get() == "ctrl+d"
    assert app.config.hotkey == "ctrl+d"


def test_capture_hotkey_escape_cancels(ui):
    window, app, _ = make_window(ui)
    window.hotkey_var.set("ctrl+alt+space")
    window.capture_hotkey(window.hotkey_var)
    dialogs = [w for w in ui["tk"].Toplevel.created if isinstance(w, ui["tk"].Toplevel)]
    dialogs[-1].bindings["<KeyPress>"](types.SimpleNamespace(keysym="Escape", state=0))
    assert window.hotkey_var.get() == "ctrl+alt+space"


def test_event_to_hotkey(ui):
    convert = ui["module"]._event_to_hotkey
    event = lambda keysym, state: types.SimpleNamespace(keysym=keysym, state=state)
    assert convert(event("space", 0x4 | 0x20000)) == "ctrl+alt+space"
    assert convert(event("d", 0x4)) == "ctrl+d"
    assert convert(event("F9", 0)) == ""
    assert convert(event("Control_L", 0x4)) == ""
    assert convert(event("Return", 0x4 | 0x1)) == "ctrl+shift+enter"


def test_lighten_helper(ui):
    lighten = ui["module"]._lighten
    assert lighten("#000000", 0.5) == "#7f7f7f"
    assert lighten("#ffffff") == "#ffffff"
    assert lighten("не цвет") == "не цвет"
