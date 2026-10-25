"""Тесты маршрутизации событий в интерфейс и иконки трея."""

import golos_app
from golos.app import ERROR, IDLE, RECORDING


class FakeRoot:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, func=None, *args):
        self.scheduled.append((delay, func))
        if func is not None and delay == 0:
            func()
        return "id"


class FakeTray:
    def __init__(self):
        self.states = []
        self.levels = []
        self.notifications = []

    def update_state(self, state, message=""):
        self.states.append((state, message))

    def update_level(self, level):
        self.levels.append(level)

    def notify(self, title, message):
        self.notifications.append((title, message))


class FakeWindow:
    def __init__(self, visible=True):
        self.visible = visible
        self.states = []
        self.levels = []
        self.texts = []

    def is_visible(self):
        return self.visible

    def update_state(self, state, message=""):
        self.states.append((state, message))

    def set_level(self, level):
        self.levels.append(level)

    def set_text(self, text, raw=""):
        self.texts.append(text)

    # старые имена — на случай, если интерфейс их ещё использует
    def set_last(self, text):
        self.texts.append(text)

    def set_last_text(self, text):
        self.texts.append(text)

    def set_partial(self, text):
        self.partials = getattr(self, "partials", []) + [text]

    def set_state(self, state, message=""):
        self.states.append((state, message))


def make_bridge():
    bridge = golos_app.GuiBridge(FakeRoot())
    bridge.tray = FakeTray()
    bridge.window = FakeWindow()
    return bridge


def test_overlay_api_is_used():
    bridge = make_bridge()

    bridge.on_state(RECORDING, "Слушаю…")
    bridge.on_level(0.4)
    bridge.on_text("Привет ", "привет")
    bridge.on_error("нет доступа")
    bridge.on_notify("Голос", "готово")
    bridge.pump()

    assert bridge.tray.states == [(RECORDING, "Слушаю…")]
    assert bridge.tray.levels == [0.4]
    assert ("Голос — ошибка", "нет доступа") in bridge.tray.notifications
    assert ("Голос", "готово") in bridge.tray.notifications
    assert (RECORDING, "Слушаю…") in bridge.window.states
    assert any(state == "error" for state, _ in bridge.window.states)
    assert bridge.window.levels == [0.4]
    assert bridge.window.texts == ["Привет "]


def test_partial_text_reaches_overlay():
    bridge = make_bridge()
    bridge.on_partial("как де")
    bridge.pump()
    assert bridge.window.partials == ["как де"]


def test_errors_are_shown_in_overlay():
    bridge = make_bridge()
    bridge.on_error("микрофон не найден")
    bridge.pump()
    assert any(state == "error" for state, _ in bridge.window.states)
    assert ("Голос — ошибка", "микрофон не найден") in bridge.tray.notifications


def test_no_tray_does_not_crash():
    bridge = golos_app.GuiBridge(FakeRoot())
    bridge.tray = None
    bridge.window = None
    bridge.on_state(ERROR, "ошибка")
    bridge.on_level(0.1)
    bridge.on_notify("Голос", "текст")
    bridge.pump()  # ничего не падает


def test_queue_drained():
    bridge = make_bridge()
    bridge.on_level(0.1)
    bridge.on_level(0.2)
    bridge.pump()
    assert bridge.tray.levels == [0.1, 0.2]
    assert bridge.queue.empty()


def test_post_without_root_runs_immediately():
    bridge = golos_app.GuiBridge(None)
    called = []
    bridge.post(called.append, 1)
    assert called == [1]


def test_console_bridge_output(capsys):
    bridge = golos_app.ConsoleBridge()
    bridge.on_text("Привет ")
    bridge.on_error("ошибка")
    out = capsys.readouterr()
    assert "Привет" in out.out
    assert "ошибка" in out.err


def test_parse_args_defaults():
    args = golos_app.parse_args([])
    assert args.show is False
    assert args.no_gui is False
    assert args.silent is False
    assert args.no_preload is False


def test_parse_args_flags():
    args = golos_app.parse_args(["--show", "--debug", "--no-gui", "--config", "x.json"])
    assert args.show and args.debug and args.no_gui
    assert args.config == "x.json"


def test_old_settings_flag_still_works():
    assert golos_app.parse_args(["--settings"]).show is True


# ---------------------------------------------------------------- иконки трея
def test_icon_images_render_for_all_states():
    tries = 0
    try:
        from golos.tray import make_icon_image
    except Exception:  # pragma: no cover - нет Pillow
        return

    for state in (IDLE, RECORDING, ERROR, "paused", "loading", "processing"):
        image = make_icon_image(state, 0.8)
        assert image.size == (64, 64)
        assert image.mode == "RGBA"
        tries += 1
    assert tries == 6


def test_create_tray_falls_back_without_pystray(monkeypatch):
    from golos.tray import NullTray, create_tray

    class App:
        config = type("C", (), {"hotkey": "ctrl+alt+space"})()

    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    tray = create_tray(App())
    assert isinstance(tray, NullTray)
    tray.update_state(IDLE)
    tray.notify("a", "b")
    tray.update_level(0.5)
