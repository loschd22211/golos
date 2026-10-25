"""Права администратора: главная причина «текст есть, а в поле пусто».

Windows выбрасывает нажатия из программы без прав, если окно-получатель
запущено от администратора. Значит:

* программу надо уметь поднимать до админских прав;
* нельзя врать «вставлено», если ввод заведомо заблокирован;
* в журнале должно быть видно, в какое окно шла вставка.
"""

import sys
import types

import pytest


from golos import elevate
from golos.inject import InjectionError, TextInjector


class FakeKeyboard:
    def __init__(self):
        self.pasted = 0
        self.sent = []
        self.typed = []

    def send(self, combination):
        self.sent.append(combination)

    def paste(self):
        self.pasted += 1

    def write(self, text, delay=0.0):
        self.typed.append(text)


class FakeClipboard:
    def __init__(self, text=""):
        self.text = text

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text


def make_injector(**kwargs):
    keyboard = kwargs.pop("keyboard", None) or FakeKeyboard()
    clipboard = kwargs.pop("clipboard", None) or FakeClipboard()
    injector = TextInjector(
        clipboard=clipboard, keyboard=keyboard, sleep=lambda _s: None, **kwargs
    )
    return injector, keyboard, clipboard


# ------------------------------------------------------------ определение прав
def test_is_admin_false_outside_windows(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", False)
    assert elevate.is_admin() is False


def test_pid_is_elevated_access_denied_means_admin(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    kernel32 = types.SimpleNamespace(OpenProcess=lambda *a: 0, CloseHandle=lambda h: None)
    monkeypatch.setattr(elevate, "_kernel32", lambda: kernel32)
    monkeypatch.setattr(elevate, "_last_error", lambda: 5)
    assert elevate.pid_is_elevated(4321) is True


def test_pid_is_elevated_reads_token(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)

    elevation_value = {"value": 0}

    class FakeULong:
        def __init__(self, value=0):
            self.value = value

        def __call__(self, value=0):
            return FakeULong(value)

    class Token:
        pass

    def open_process(*args):
        return 1

    def open_process_token(handle, access, out):
        out._obj = Token()
        return 1

    def get_token_information(token, cls, elevation, size, needed):
        elevation._obj.value = elevation_value["value"]
        return 1

    kernel32 = types.SimpleNamespace(OpenProcess=open_process, CloseHandle=lambda h: None)
    advapi32 = types.SimpleNamespace(
        OpenProcessToken=open_process_token,
        GetTokenInformation=get_token_information,
    )
    monkeypatch.setattr(elevate, "_kernel32", lambda: kernel32)
    monkeypatch.setattr(elevate, "_advapi32", lambda: advapi32)
    monkeypatch.setattr(
        elevate.ctypes,
        "c_ulong",
        FakeULong,
    )
    monkeypatch.setattr(
        elevate.ctypes, "c_void_p", lambda value=None: types.SimpleNamespace(value=value)
    )
    monkeypatch.setattr(elevate.ctypes, "byref", lambda obj: types.SimpleNamespace(_obj=obj))
    monkeypatch.setattr(elevate.ctypes, "sizeof", lambda obj: 4)

    assert elevate.pid_is_elevated(1) is False
    elevation_value["value"] = 1
    assert elevate.pid_is_elevated(1) is True


def test_pid_is_elevated_unknown_without_pid(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    assert elevate.pid_is_elevated(None) is None


# --------------------------------------------------------------- команда запуска
def test_relaunch_command_adds_attempt_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["golos_app.py", "--show"])
    exe, params = elevate.relaunch_command()
    assert "python" in exe.lower()
    assert "--show" in params
    assert elevate.ATTEMPT_FLAG in params


def test_relaunch_command_does_not_duplicate_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["golos_app.py", elevate.ATTEMPT_FLAG])
    _exe, params = elevate.relaunch_command()
    assert params.count(elevate.ATTEMPT_FLAG) == 1


def test_already_attempted_reads_flag():
    assert elevate.already_attempted([elevate.ATTEMPT_FLAG]) is True
    assert elevate.already_attempted(["--show"]) is False
    assert elevate.already_attempted(None) is False


def test_relaunch_as_admin_outside_windows(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", False)
    assert elevate.relaunch_as_admin(["--show"]) is False


def test_relaunch_as_admin_reports_uac_cancel(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)

    def shell():
        class ShellExecuteW:
            restype = None
            argtypes = None

            def __call__(self, *args):
                return 1223  # пользователь отказался

        return types.SimpleNamespace(ShellExecuteW=ShellExecuteW())

    monkeypatch.setattr(elevate, "_shell32", shell)
    assert elevate.relaunch_as_admin(["--show"]) is False


def test_relaunch_as_admin_starts_new_process(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)

    def shell():
        class ShellExecuteW:
            restype = None
            argtypes = None

            def __call__(self, *args):
                return 42

        return types.SimpleNamespace(ShellExecuteW=ShellExecuteW())

    monkeypatch.setattr(elevate, "_shell32", shell)
    assert elevate.relaunch_as_admin(["--show"]) is True


# ------------------------------------------------------------------- защита вставки
def test_guard_blocks_when_target_is_admin(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "foreground_is_elevated", lambda: True)
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    monkeypatch.setattr(
        elevate, "describe_window",
        lambda hwnd=None: "«Chrome» (chrome.exe, от администратора)",
    )

    with pytest.raises(InjectionError) as excinfo:
        elevate.insertion_guard()

    message = str(excinfo.value)
    assert "администратора" in message
    assert "Перезапустить от администратора" in message


def test_guard_allows_when_we_are_admin(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "foreground_is_elevated", lambda: True)
    monkeypatch.setattr(elevate, "is_admin", lambda: True)
    monkeypatch.setattr(elevate, "describe_window", lambda hwnd=None: "«Chrome»")

    elevate.insertion_guard()  # не должно бросить


def test_guard_allows_regular_window(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "foreground_is_elevated", lambda: False)
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    monkeypatch.setattr(
        elevate, "describe_window", lambda hwnd=None: "«Блокнот» (notepad.exe)"
    )

    elevate.insertion_guard()


def test_guard_logs_target_window(monkeypatch, caplog):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "foreground_is_elevated", lambda: False)
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    monkeypatch.setattr(
        elevate, "describe_window", lambda hwnd=None: "«Заметки» (notepad.exe)"
    )

    with caplog.at_level("INFO", logger="golos.elevate"):
        elevate.insertion_guard()

    assert "Заметки" in caplog.text


def test_injector_calls_guard_before_paste():
    calls = []

    def guard():
        calls.append("guard")

    injector, keyboard, clipboard = make_injector(guard=guard)
    assert injector.insert("привет") is True
    assert calls == ["guard"]
    assert keyboard.pasted == 1


def test_injector_does_not_paste_when_guard_raises():
    def guard():
        raise InjectionError("нет прав")

    injector, keyboard, clipboard = make_injector(guard=guard)
    with pytest.raises(InjectionError):
        injector.insert("привет")
    assert keyboard.pasted == 0
    assert keyboard.sent == []


def test_injector_uses_stepwise_paste():
    injector, keyboard, _clipboard = make_injector()
    injector.insert("текст")
    assert keyboard.pasted == 1
    assert keyboard.sent == []  # send("ctrl+v") больше не используется


def test_injector_type_method_also_guarded():
    calls = []

    def guard():
        calls.append(1)

    injector, keyboard, _clipboard = make_injector(guard=guard, method="type")
    injector.insert("текст")
    assert calls == [1]
    assert keyboard.typed == ["текст"]


# ------------------------------------------------------------- настройки и запуск
def test_config_defaults_to_admin_rights():
    from golos.config import Config

    assert Config().run_as_admin is True


def test_config_reads_admin_flag_from_text():
    from golos.config import Config

    assert Config.from_dict({"run_as_admin": "false"}).run_as_admin is False
    assert Config.from_dict({"run_as_admin": "да"}).run_as_admin is True
    assert Config.from_dict({"run_as_admin": None}).run_as_admin is True


def test_should_elevate_logic(monkeypatch):
    import argparse

    import golos_app
    from golos.config import Config

    monkeypatch.setattr(golos_app.elevate, "is_admin", lambda: False)
    monkeypatch.setattr(sys, "argv", ["golos_app.py"])
    config = Config()

    def args(**kwargs):
        base = dict(silent=False, no_gui=False, no_admin=False)
        base.update(kwargs)
        return argparse.Namespace(**base)

    assert golos_app._should_elevate(args(), config) is True
    assert golos_app._should_elevate(args(silent=True), config) is False
    assert golos_app._should_elevate(args(no_gui=True), config) is False
    assert golos_app._should_elevate(args(no_admin=True), config) is False

    config.run_as_admin = False
    assert golos_app._should_elevate(args(), config) is False

    config.run_as_admin = True
    monkeypatch.setattr(golos_app.elevate, "is_admin", lambda: True)
    assert golos_app._should_elevate(args(), config) is False


def test_should_elevate_does_not_loop_after_attempt(monkeypatch):
    import argparse

    import golos_app
    from golos.config import Config

    monkeypatch.setattr(golos_app.elevate, "is_admin", lambda: False)
    monkeypatch.setattr(sys, "argv", ["golos_app.py", golos_app.elevate.ATTEMPT_FLAG])
    args = argparse.Namespace(silent=False, no_gui=False, no_admin=False)

    assert golos_app._should_elevate(args, Config()) is False


def test_diagnose_reports_missing_admin_rights(monkeypatch, capsys):
    from golos import diagnose

    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    diagnose.warnings.clear()

    assert diagnose.check_rights() is False
    out = capsys.readouterr().out
    assert "без прав администратора" in out
    assert "Перезапустить от администратора" in out
    assert diagnose.warnings


def test_diagnose_reports_admin_rights(monkeypatch, capsys):
    from golos import diagnose

    monkeypatch.setattr(elevate, "is_admin", lambda: True)
    diagnose.warnings.clear()

    assert diagnose.check_rights() is True
    assert "запущено от администратора" in capsys.readouterr().out
    assert not diagnose.warnings


def test_app_sets_guard_on_injector(monkeypatch):
    """Ядро обязано проверять права окна-получателя само, а не по просьбе."""
    from golos.app import DictationApp
    from golos.config import Config, AppPaths

    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    app = DictationApp(config=Config(), paths=AppPaths.default(), preload=False)
    assert app.injector.guard is elevate.insertion_guard


# ------------------------------------------------ права проверяем у окна-цели
def test_guard_checks_target_window(monkeypatch):
    """Активным мог оказаться наш оверлей — смотреть надо на цель вставки."""
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    monkeypatch.setattr(elevate, "window_is_elevated", lambda hwnd: hwnd == 777)
    monkeypatch.setattr(
        elevate, "describe_window", lambda hwnd=None: f"«Chrome» ({hwnd})"
    )

    with pytest.raises(InjectionError):
        elevate.insertion_guard(777)

    elevate.insertion_guard(888)  # обычное окно — можно вставлять


def test_guard_uses_foreground_without_target(monkeypatch):
    monkeypatch.setattr(elevate, "_IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    monkeypatch.setattr(elevate, "foreground_is_elevated", lambda: True)
    monkeypatch.setattr(elevate, "describe_window", lambda hwnd=None: "«Окно»")

    with pytest.raises(InjectionError):
        elevate.insertion_guard(None)


def test_window_is_elevated_uses_window_pid(monkeypatch):
    from golos import winfocus

    monkeypatch.setattr(winfocus, "window_pid", lambda hwnd: 4242)
    monkeypatch.setattr(elevate, "pid_is_elevated", lambda pid: pid == 4242)

    assert elevate.window_is_elevated(10) is True
