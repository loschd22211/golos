"""Куда печатать: оверлей не должен перехватывать вставку сам у себя.

В журнале пользователя половина вставок уходила в окно «Голос» (pythonw.exe),
а не в браузер — со стороны это «текст не вставляется».
"""

from golos import winfocus
from golos.inject import TextInjector


class Keyboard:
    def __init__(self):
        self.pasted = 0

    def paste(self):
        self.pasted += 1

    def send(self, combination):  # pragma: no cover - резервный путь
        self.pasted += 1

    def write(self, text, delay=0.0):
        pass


class Clipboard:
    def __init__(self):
        self.text = ""

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text


# ------------------------------------------------------------------- трекер
def test_tracker_ignores_our_own_window(monkeypatch):
    monkeypatch.setattr(winfocus, "is_window", lambda hwnd: True)
    monkeypatch.setattr(winfocus, "is_our_window", lambda hwnd: hwnd == 111)

    tracker = winfocus.TargetTracker()
    assert tracker.remember(111) is False
    assert tracker.hwnd == 0

    assert tracker.remember(222) is True
    assert tracker.hwnd == 222

    # фокус вернулся в наш оверлей — цель не должна поменяться
    assert tracker.remember(111) is False
    assert tracker.hwnd == 222


def test_tracker_ignores_dead_windows(monkeypatch):
    monkeypatch.setattr(winfocus, "is_window", lambda hwnd: False)
    monkeypatch.setattr(winfocus, "is_our_window", lambda hwnd: False)

    tracker = winfocus.TargetTracker()
    assert tracker.remember(333) is False
    assert tracker.hwnd == 0


def test_tracker_remembers_current_foreground(monkeypatch):
    monkeypatch.setattr(winfocus, "get_foreground_window", lambda: 555)
    monkeypatch.setattr(winfocus, "is_window", lambda hwnd: True)
    monkeypatch.setattr(winfocus, "is_our_window", lambda hwnd: False)

    tracker = winfocus.TargetTracker()
    assert tracker.remember_current() is True
    assert tracker.hwnd == 555


def test_tracker_activate_forces_focus_when_we_are_foreground(monkeypatch):
    calls = {}

    monkeypatch.setattr(winfocus, "is_window", lambda hwnd: True)
    monkeypatch.setattr(winfocus, "is_our_window", lambda hwnd: hwnd == 111)
    monkeypatch.setattr(winfocus, "get_foreground_window", lambda: 111)

    def fake_activate(hwnd, attempts=3, pause=0.04):
        calls["hwnd"] = hwnd
        return True

    monkeypatch.setattr(winfocus, "activate_window", fake_activate)

    tracker = winfocus.TargetTracker()
    tracker.remember(222)
    assert tracker.activate() is True
    assert calls["hwnd"] == 222


def test_tracker_activate_returns_false_without_target(monkeypatch):
    monkeypatch.setattr(winfocus, "get_foreground_window", lambda: None)
    monkeypatch.setattr(winfocus, "is_window", lambda hwnd: True)
    monkeypatch.setattr(winfocus, "is_our_window", lambda hwnd: False)

    tracker = winfocus.TargetTracker()
    assert tracker.activate() is False


def test_activate_window_skips_dead_window(monkeypatch):
    monkeypatch.setattr(winfocus, "is_window", lambda hwnd: False)
    assert winfocus.activate_window(444) is False


def test_force_foreground_false_outside_windows(monkeypatch):
    monkeypatch.setattr(winfocus, "_IS_WINDOWS", False)
    assert winfocus.force_foreground(123) is False


def test_is_our_window_compares_pid(monkeypatch):
    import os

    monkeypatch.setattr(winfocus, "window_pid", lambda hwnd: os.getpid())
    assert winfocus.is_our_window(10) is True

    monkeypatch.setattr(winfocus, "window_pid", lambda hwnd: os.getpid() + 1)
    assert winfocus.is_our_window(11) is False

    monkeypatch.setattr(winfocus, "window_pid", lambda hwnd: None)
    assert winfocus.is_our_window(None) is False


def test_root_window_outside_windows(monkeypatch):
    monkeypatch.setattr(winfocus, "_IS_WINDOWS", False)
    assert winfocus.root_window(5) is None


def test_force_foreground_attaches_threads(monkeypatch):
    """Классический приём: без AttachThreadInput фокус не отдаётся."""
    log = {"attached": [], "foreground": 0}
    state = {"fg": 111}

    class User32:
        def GetForegroundWindow(self):
            return state["fg"]

        def GetWindowThreadProcessId(self, hwnd, _out):
            return 1111 if hwnd == 111 else 2222

        def AttachThreadInput(self, current, thread, attach):
            log["attached"].append((thread, attach))
            return 1

        def BringWindowToTop(self, hwnd):
            return 1

        def ShowWindow(self, hwnd, cmd):
            return 1

        def SetForegroundWindow(self, hwnd):
            state["fg"] = hwnd
            return 1

        def SetFocus(self, hwnd):
            return 1

    class Kernel32:
        def GetCurrentThreadId(self):
            return 999

    monkeypatch.setattr(winfocus, "_IS_WINDOWS", True)
    monkeypatch.setattr(winfocus, "_user32", lambda: User32())
    monkeypatch.setattr(winfocus, "_kernel32", lambda: Kernel32())
    monkeypatch.setattr(winfocus, "root_window", lambda hwnd: hwnd)

    assert winfocus.force_foreground(222) is True
    assert state["fg"] == 222
    assert (1111, True) in log["attached"]
    assert (2222, True) in log["attached"]
    assert (2222, False) in log["attached"]


# ------------------------------------------------------------------- инжектор
def _injector(**kwargs):
    keyboard = Keyboard()
    injector = TextInjector(
        clipboard=Clipboard(), keyboard=keyboard, sleep=lambda _s: None, **kwargs
    )
    return injector, keyboard


def test_insert_focuses_target_before_paste():
    order = []

    injector, keyboard = _injector()
    injector.focus = lambda: order.append("focus")
    injector.clipboard.set_text = lambda text: order.append("clipboard")

    injector.insert("привет")
    # буфер заполняется после возврата фокуса (и потом возвращается старый текст)
    assert order.index("clipboard") > order.index("focus")
    assert keyboard.pasted == 1


def test_insert_calls_guard_then_focus_then_paste():
    order = []

    injector, keyboard = _injector(guard=lambda: order.append("guard"))
    injector.focus = lambda: order.append("focus")
    injector.clipboard.set_text = lambda text: order.append("clipboard")

    injector.insert("привет")
    assert order[:3] == ["guard", "focus", "clipboard"]
    assert keyboard.pasted == 1


def test_focus_error_does_not_block_insert():
    injector, keyboard = _injector()

    def broken():
        raise RuntimeError("нет окна")

    injector.focus = broken
    assert injector.insert("привет") is True
    assert keyboard.pasted == 1


def test_app_wires_focus_hook_into_injector(monkeypatch):
    from golos.app import DictationApp
    from golos.config import AppPaths, Config

    app = DictationApp(config=Config(), paths=AppPaths.default(), preload=False)
    called = []
    app.focus_hook = lambda: called.append(True)

    # фокус возвращается в момент вставки, а не «когда-нибудь потом»
    assert app.injector.focus is not None
    app.injector.focus()
    assert called
