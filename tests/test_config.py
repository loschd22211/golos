import json
from pathlib import Path

from golos.config import AppPaths, Config, load_config, save_config


def test_defaults_are_valid():
    cfg = Config()
    assert cfg.hotkey == "ctrl+alt+space"
    assert cfg.language == "ru"
    assert cfg.model in ("tiny", "base", "small", "medium", "large-v3")
    assert cfg.insert_method == "paste"
    assert cfg.streaming is True
    assert cfg.model == "small"


def test_normalize_fixes_bad_values():
    cfg = Config(model="huge", device="tpu", language="fr", insert_method="x",
                 beam_size=99, sample_rate=1, paste_delay=-5)
    assert cfg.model == "small"
    assert cfg.device == "auto"
    assert cfg.language == "ru"
    assert cfg.insert_method == "paste"
    assert cfg.streaming is True
    assert cfg.model == "small"
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
    assert cfg.model == "small"


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


# ----------------------------------------------------- промпт и обратная связь
def test_default_prompt_is_empty():
    """Длинная подсказка вылезала в текст: модель дописывала её как реплику."""
    from golos.config import Config

    assert Config().initial_prompt == ""


def test_legacy_prompt_is_dropped_on_load(tmp_path):
    import json

    from golos.config import LEGACY_INITIAL_PROMPTS, load_config

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"initial_prompt": LEGACY_INITIAL_PROMPTS[0]}), encoding="utf-8"
    )

    config, broken = load_config(path)

    assert broken is None
    assert config.initial_prompt == ""


def test_custom_prompt_is_kept(tmp_path):
    import json

    from golos.config import load_config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"initial_prompt": "GitHub, Docker"}), encoding="utf-8")

    config, _broken = load_config(path)
    assert config.initial_prompt == "GitHub, Docker"


def test_legacy_auto_language_becomes_russian(tmp_path):
    """Старые конфиги писали "auto" — он путал русское с английским."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"language": "auto"}), encoding="utf-8")
    cfg, broken = load_config(path)
    assert broken is None
    assert cfg.language == "ru"


def test_explicit_auto_language_is_kept(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"language": "auto", "config_version": 2}), encoding="utf-8")
    cfg, _ = load_config(path)
    assert cfg.language == "auto"


def test_saved_config_remembers_new_version(tmp_path):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["config_version"] >= 2
    assert data["language"] == "ru"


def test_blur_is_switched_off_for_old_configs(tmp_path):
    """1.4.0 включал акрил по умолчанию — из-за него был «серый квадрат»."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"overlay_glass": True, "overlay_opacity": 0.93, "config_version": 2}),
        encoding="utf-8",
    )
    cfg, _ = load_config(path)
    assert cfg.overlay_glass is False
    assert cfg.overlay_opacity == 0.94


def test_new_config_keeps_explicit_blur(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"overlay_glass": True, "config_version": 3}), encoding="utf-8"
    )
    cfg, _ = load_config(path)
    assert cfg.overlay_glass is True
