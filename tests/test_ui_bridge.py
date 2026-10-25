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


class FakeSettings:
    def __init__(self, visible=True):
        self.visible = visible
        self.statuses = []
        self.refreshes = 0

    def is_visible(self):
        return self.visible

    def update_status(self, state, message=""):
        self.statuses.append((state, message))

    def _refresh_stats(self):
        self.refreshes += 1


def make_bridge():
    bridge = golos_app.GuiBridge(FakeRoot())
    bridge.tray = FakeTray()
    bridge.settings = FakeSettings()
    return bridge


def test_events_reach_tray_and_settings():
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
    assert any("Вставлено" in s[1] for s in bridge.settings.statuses)
    assert any(s[0] == "Ошибка" for s in bridge.settings.statuses)


def test_hidden_settings_not_updated():
    bridge = make_bridge()
    bridge.settings.visible = False
    bridge.on_state(IDLE, "Готов")
    bridge.on_text("x")
    bridge.pump()
    assert bridge.settings.statuses == []
    assert bridge.tray.states == [(IDLE, "Готов")]


def test_no_tray_does_not_crash():
    bridge = golos_app.GuiBridge(FakeRoot())
    bridge.tray = None
    bridge.settings = None
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
    assert args.settings is False
    assert args.no_gui is False
    assert args.silent is False
    assert args.no_preload is False


def test_parse_args_flags():
    args = golos_app.parse_args(["--settings", "--debug", "--no-gui", "--config", "x.json"])
    assert args.settings and args.debug and args.no_gui
    assert args.config == "x.json"


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
