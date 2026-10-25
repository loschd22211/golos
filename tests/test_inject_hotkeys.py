from golos.inject import InjectionError, TextInjector
from golos.hotkeys import HotkeyManager, normalize_hotkey


class FakeClipboard:
    def __init__(self, initial: str = "старый буфер"):
        self.text = initial
        self.history: list[str] = []

    def get_text(self) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.history.append(text)
        self.text = text


class FakeKeyboard:
    def __init__(self, fail_send: bool = False):
        self.sent: list[str] = []
        self.typed: list[str] = []
        self.fail_send = fail_send

    def send(self, combination: str) -> None:
        self.sent.append(combination)
        if self.fail_send:
            raise InjectionError("нет доступа")

    def write(self, text: str, delay: float = 0.0) -> None:
        self.typed.append(text)


def make_injector(clipboard=None, keyboard=None, **kwargs) -> TextInjector:
    kwargs.setdefault("paste_delay", 0)
    kwargs.setdefault("restore_delay", 0)
    return TextInjector(
        clipboard=clipboard or FakeClipboard(),
        keyboard=keyboard or FakeKeyboard(),
        sleep=lambda _s: None,
        **kwargs,
    )


def test_paste_puts_text_in_clipboard_and_sends_ctrl_v():
    clipboard = FakeClipboard("прежнее")
    keyboard = FakeKeyboard()
    injector = make_injector(clipboard, keyboard, method="paste", restore_clipboard=False)

    assert injector.insert("Привет мир ") is True
    assert clipboard.text == "Привет мир "
    assert keyboard.sent == ["ctrl+v"]


def test_clipboard_restored():
    clipboard = FakeClipboard("прежнее")
    injector = make_injector(clipboard, FakeKeyboard(), method="paste", restore_clipboard=True)
    injector.insert("новое")
    assert clipboard.text == "прежнее"
    assert clipboard.history == ["новое", "прежнее"]


def test_empty_text_is_not_inserted():
    keyboard = FakeKeyboard()
    injector = make_injector(keyboard=keyboard)
    assert injector.insert("") is False
    assert injector.insert("   ") is False
    assert keyboard.sent == []


def test_type_mode_does_not_touch_clipboard():
    clipboard = FakeClipboard("прежнее")
    keyboard = FakeKeyboard()
    injector = make_injector(clipboard, keyboard, method="type")
    assert injector.insert("Привет") is True
    assert keyboard.typed == ["Привет"]
    assert clipboard.text == "прежнее"
    assert keyboard.sent == []


def test_paste_falls_back_to_typing_when_hotkey_fails():
    keyboard = FakeKeyboard(fail_send=True)
    injector = make_injector(FakeKeyboard() and None, keyboard, method="paste")
    assert injector.insert("Текст") is True
    assert keyboard.typed == ["Текст"]


def test_apply_config_updates_settings():
    injector = make_injector()

    class Cfg:
        insert_method = "type"
        restore_clipboard = False
        paste_delay = 0.5

    injector.apply_config(Cfg())
    assert injector.method == "type"
    assert injector.restore_clipboard is False
    assert injector.paste_delay == 0.5


# ------------------------------------------------------------------ hotkeys
class FakeHotkeyBackend:
    def __init__(self):
        self.registered: dict[str, object] = {}
        self.removed: list[object] = []
        self._next = 0

    def add(self, combination, callback, suppress=False):
        self._next += 1
        self.registered[combination] = callback
        return f"h{self._next}"

    def remove(self, handle):
        self.removed.append(handle)

    def validate(self, combination):
        return bool(combination) and " " not in combination.strip() or "+" in combination


def test_register_and_rebind():
    backend = FakeHotkeyBackend()
    manager = HotkeyManager(backend)
    called = []
    assert manager.register("toggle", "ctrl+alt+space", lambda: called.append(1))
    assert manager.registered == {"toggle": "ctrl+alt+space"}
    backend.registered["ctrl+alt+space"]()
    assert called == [1]

    manager.rebind({"toggle": ("ctrl+shift+d", lambda: None)})
    assert manager.registered == {"toggle": "ctrl+shift+d"}
    assert backend.removed


def test_invalid_combination_rejected():
    manager = HotkeyManager(FakeHotkeyBackend())
    assert manager.register("toggle", "", lambda: None) is False
    assert manager.registered == {}


def test_normalize_hotkey():
    assert normalize_hotkey("Ctrl+Alt+Space") == "ctrl+alt+space"
    assert normalize_hotkey(" Control + Shift + Esc ") == "ctrl+shift+escape"
    assert normalize_hotkey("Win+Spacebar") == "windows+space"
    assert normalize_hotkey("") == ""
