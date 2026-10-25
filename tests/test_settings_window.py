"""Тесты окна настроек с поддельным tkinter.

Настоящий tkinter требует графической среды, поэтому подменяем модуль простыми
заглушками: это проверяет логику построения окна, чтения и сохранения настроек.
"""

import sys
import types

import pytest

from golos.config import Config


# ------------------------------------------------------------------ fake tkinter
class FakeVar:
    def __init__(self, value=None, master=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeWidget:
    def __init__(self, master=None, **options):
        self.master = master
        self.options = dict(options)
        self.packed = False
        self.grid_options = {}
        self.bindings = {}

    # геометрия
    def pack(self, **options):
        self.packed = True
        return self

    def grid(self, **options):
        self.grid_options = options
        return self

    def place(self, **options):
        return self

    def pack_forget(self):
        return None

    # прочее
    def configure(self, **options):
        self.options.update(options)
        return self

    config = configure

    def bind(self, sequence, handler=None, **kwargs):
        if handler is not None:
            self.bindings[sequence] = handler
        return None

    def focus_force(self):
        return None

    def columnconfigure(self, *args, **kwargs):
        return None

    def winfo_viewable(self):
        return True

    def __setitem__(self, key, value):
        self.options[key] = value

    def __getitem__(self, key):
        return self.options[key]


class FakeText(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.content = ""

    def insert(self, index, text):
        self.content += text

    def delete(self, start, end=None):
        self.content = ""

    def get(self, start, end=None):
        return self.content

    def see(self, index):
        return None


class FakeWindow(FakeWidget):
    created = []

    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.visible = False
        self.after_calls = []
        FakeWindow.created.append(self)

    def title(self, text):
        self.options["title"] = text

    def geometry(self, value):
        self.options["geometry"] = value

    def minsize(self, w, h):
        self.options["minsize"] = (w, h)

    def protocol(self, name, handler):
        self.options[f"protocol:{name}"] = handler

    def withdraw(self):
        self.visible = False

    def deiconify(self):
        self.visible = True

    def lift(self):
        return None

    def transient(self, master=None):
        return None

    def grab_set(self):
        return None

    def focus_set(self):
        return None

    def after(self, delay, func=None, *args):
        self.after_calls.append((delay, func))
        if delay == 0 and callable(func):
            func(*args)
        return "after-id"

    def destroy(self):
        self.visible = False

    def mainloop(self):
        return None

    def quit(self):
        return None


def build_fake_tkinter():
    tk = types.ModuleType("tkinter")
    tk.Tk = FakeWindow
    tk.Toplevel = FakeWindow
    tk.Text = FakeText
    tk.StringVar = FakeVar
    tk.BooleanVar = FakeVar
    tk.IntVar = FakeVar
    tk.DoubleVar = FakeVar
    tk.END = "end"

    ttk = types.ModuleType("tkinter.ttk")
    for name in (
        "Frame",
        "Label",
        "LabelFrame",
        "Button",
        "Entry",
        "Combobox",
        "Checkbutton",
        "Spinbox",
        "Notebook",
        "Separator",
    ):
        setattr(ttk, name, type(name, (FakeWidget,), {}))

    class Notebook(FakeWidget):
        def __init__(self, master=None, **options):
            super().__init__(master, **options)
            self.tabs = []

        def add(self, child, text="", **options):
            self.tabs.append((child, text))

        def select(self, index=0):
            return None

    ttk.Notebook = Notebook
    tk.ttk = ttk

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.calls = []
    messagebox.showinfo = lambda *a, **k: messagebox.calls.append(("info", a))
    messagebox.showwarning = lambda *a, **k: messagebox.calls.append(("warning", a))
    messagebox.showerror = lambda *a, **k: messagebox.calls.append(("error", a))
    messagebox.askyesno = lambda *a, **k: True
    tk.messagebox = messagebox

    return tk, ttk, messagebox


@pytest.fixture
def ui(monkeypatch):
    tk, ttk, messagebox = build_fake_tkinter()
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", messagebox)
    monkeypatch.delitem(sys.modules, "golos.settings_window", raising=False)
    import golos.settings_window as settings_window

    monkeypatch.setattr(
        settings_window,
        "list_input_devices",
        lambda: ["0: Микрофон (Realtek)", "2: USB Headset"],
    )
    return settings_window


class FakePaths:
    data_dir = "/tmp/golos"
    config_file = "/tmp/golos/config.json"
    log_file = "/tmp/golos/golos.log"
    history_file = "/tmp/golos/history.txt"
    models_dir = "/tmp/golos/models"


class FakeTranscriber:
    def __init__(self):
        self.config = Config()

    def preload(self):
        return True


class FakeApp:
    def __init__(self, config=None):
        self.config = config or Config()
        self.paths = FakePaths()
        self.transcriber = FakeTranscriber()
        self.last_text = "привет мир"
        self.applied = []
        self.preloads = 0

    def status(self):
        return {
            "state_label": "Готов",
            "hotkey": self.config.hotkey,
            "model": self.config.model,
            "model_ready": False,
            "sessions": 2,
            "words": 10,
            "minutes": 0.5,
            "last_text": self.last_text,
        }

    def apply_config(self, cfg, persist=True):
        self.applied.append(cfg)
        self.config = cfg
        return []

    def preload_async(self):
        self.preloads += 1

    def is_recording(self):
        return False


def make_window(ui, app=None):
    app = app or FakeApp()
    root = ui.tk.Tk()
    window = ui.SettingsWindow(root, app)
    return window, app


def test_window_builds_and_loads(ui):
    cfg = Config(model="small", hotkey="ctrl+shift+d", language="ru")
    window, app = make_window(ui, FakeApp(cfg))
    assert window.hotkey_var.get() == "ctrl+shift+d"
    assert window.model_var.get() == "small"
    assert window.language_var.get() == "ru"
    assert window.status_var.get() == "Готов"


def test_collect_config_roundtrip(ui):
    window, app = make_window(ui)
    collected = window.collect_config()
    assert collected.hotkey == app.config.hotkey
    assert collected.model == app.config.model
    assert collected.language == app.config.language
    assert collected.insert_method == app.config.insert_method
    assert collected.sample_rate == app.config.sample_rate


def test_collect_config_picks_up_changes(ui):
    window, app = make_window(ui)
    window.model_var.set("large-v3")
    window.language_var.set("en")
    window.beam_var.set(7)
    window.capitalize_var.set(False)
    window.delay_var.set(0.4)
    window.mic_var.set("2: USB Headset")

    cfg = window.collect_config()
    assert cfg.model == "large-v3"
    assert cfg.language == "en"
    assert cfg.beam_size == 7
    assert cfg.capitalize is False
    assert abs(cfg.paste_delay - 0.4) < 1e-9
    assert cfg.input_device == "2"


def test_dictionary_parsing(ui):
    window, app = make_window(ui)
    window.dict_text.delete("1.0", "end")
    window.dict_text.insert(
        "1.0",
        "питон = Python\nгитхаб => GitHub\n# комментарий\nмусор без разделителя\n = пусто",
    )
    cfg = window.collect_config()
    assert cfg.replacements == [["питон", "Python"], ["гитхаб", "GitHub"]]


def test_save_applies_and_persists(ui):
    window, app = make_window(ui)
    window.hotkey_var.set("ctrl+shift+space")
    window.save()
    assert app.applied, "настройки должны применяться"
    assert app.applied[-1].hotkey == "ctrl+shift+space"
    assert app.config.hotkey == "ctrl+shift+space"


def test_save_reports_warnings(ui, monkeypatch):
    window, app = make_window(ui)
    app.apply_config = lambda cfg, persist=True: ["Клавиша занята"]
    window.save()
    assert ("warning",) == (ui.messagebox.calls[-1][0],)


def test_reset_defaults_loads_default_values(ui):
    cfg = Config(model="large-v3", hotkey="f9")
    window, app = make_window(ui, FakeApp(cfg))
    window.reset_defaults()
    assert window.model_var.get() == "medium"
    assert window.hotkey_var.get() == "ctrl+alt+space"


def test_saved_microphone_index_is_restored(ui):
    cfg = Config(input_device="2")
    window, app = make_window(ui, FakeApp(cfg))
    assert window.mic_var.get() == "2: USB Headset"


def test_unknown_microphone_falls_back_to_default(ui):
    cfg = Config(input_device="77")
    window, app = make_window(ui, FakeApp(cfg))
    assert window.mic_var.get() == "(по умолчанию)"
    assert window.collect_config().input_device == ""


def test_update_status_refreshes_stats(ui):
    window, app = make_window(ui)
    window.update_status("Запись", "Слушаю…")
    assert window.status_var.get() == "Слушаю…"
    assert "Диктовок: 2" in window.stats_var.get()


# ------------------------------------------------------------------ хоткеи
class FakeEvent:
    def __init__(self, keysym, state):
        self.keysym = keysym
        self.state = state


def test_event_to_hotkey(ui):
    convert = ui._event_to_hotkey
    assert convert(FakeEvent("space", 0x4 | 0x20000)) == "ctrl+alt+space"
    assert convert(FakeEvent("d", 0x4)) == "ctrl+d"
    assert convert(FakeEvent("F9", 0)) == ""  # без модификатора — не сочетание
    assert convert(FakeEvent("Control_L", 0x4)) == ""
    assert convert(FakeEvent("Return", 0x4 | 0x1)) == "ctrl+shift+enter"
    assert convert(FakeEvent("Escape", 0x4)) == "ctrl+escape"


def test_capture_hotkey_sets_value(ui):
    window, app = make_window(ui)
    FakeWindow.created.clear()
    window.capture_hotkey(window.hotkey_var)

    dialog = FakeWindow.created[-1]
    handler = dialog.bindings["<KeyPress>"]
    handler(FakeEvent("space", 0x4 | 0x20000))
    assert window.hotkey_var.get() == "ctrl+alt+space"


def test_capture_hotkey_escape_cancels(ui):
    window, app = make_window(ui)
    window.hotkey_var.set("ctrl+alt+space")
    FakeWindow.created.clear()
    window.capture_hotkey(window.hotkey_var)

    handler = FakeWindow.created[-1].bindings["<KeyPress>"]
    handler(FakeEvent("Escape", 0))
    assert window.hotkey_var.get() == "ctrl+alt+space"
