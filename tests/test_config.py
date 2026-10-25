import json
from pathlib import Path

from golos.config import AppPaths, Config, load_config, save_config


def test_defaults_are_valid():
    cfg = Config()
    assert cfg.hotkey == "ctrl+alt+space"
    assert cfg.language == "auto"
    assert cfg.model in ("tiny", "base", "small", "medium", "large-v3")
    assert cfg.insert_method == "paste"


def test_normalize_fixes_bad_values():
    cfg = Config(model="huge", device="tpu", language="fr", insert_method="x",
                 beam_size=99, sample_rate=1, paste_delay=-5)
    assert cfg.model == "medium"
    assert cfg.device == "auto"
    assert cfg.language == "auto"
    assert cfg.insert_method == "paste"
    assert cfg.beam_size == 10
    assert cfg.sample_rate == 8000
    assert cfg.paste_delay == 0.0


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = Config(model="small", hotkey="ctrl+shift+d", replacements=[["питон", "Python"]])
    save_config(cfg, path)
    assert path.exists()

    loaded, broken = load_config(path)
    assert broken is None
    assert loaded.model == "small"
    assert loaded.hotkey == "ctrl+shift+d"
    assert loaded.replacements == [["питон", "Python"]]


def test_json_contains_cyrillic_not_escaped(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(Config(initial_prompt="Привет"), path)
    raw = path.read_text(encoding="utf-8")
    assert "Привет" in raw


def test_missing_file_returns_defaults(tmp_path: Path):
    cfg, broken = load_config(tmp_path / "nope.json")
    assert broken is None
    assert cfg.model == "medium"


def test_broken_file_is_backed_up(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{ это не json", encoding="utf-8")
    cfg, broken = load_config(path)
    assert isinstance(cfg, Config)
    assert broken is not None and Path(broken).exists()
    assert not path.exists()


def test_unknown_keys_are_ignored(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "small", "неизвестное": 1}), encoding="utf-8")
    cfg, broken = load_config(path)
    assert broken is None
    assert cfg.model == "small"


def test_paths_created(tmp_path: Path):
    paths = AppPaths(data_dir=tmp_path / "golos").ensure()
    assert paths.models_dir.is_dir()
    assert paths.config_file.name == "config.json"


def test_replacements_cleaned():
    cfg = Config(replacements=[["a", "b"], ["", "c"], "bad", ["x"]])
    assert cfg.replacements == [["a", "b"]]


def test_status_flag_reports_not_running():
    """--status используется run.bat, чтобы не запускать второй экземпляр."""
    import golos_app
    from golos.system import SingleInstance

    name = "golos-single-instance"
    probe = SingleInstance(name)
    assert probe.acquire(), "в тестах lock должен быть свободен"
    probe.release()

    assert golos_app.parse_args([]).status is False
    assert golos_app.parse_args(["--status"]).status is True
