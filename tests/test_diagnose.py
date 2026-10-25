"""Тесты диагностики: она должна не падать на неполном окружении и честно
называть проблемы, а --fix — записывать рабочие настройки."""

import json
import sys
import types


import pytest

from golos import diagnose


@pytest.fixture
def no_deps(monkeypatch):
    """Окружение без sounddevice/faster-whisper/keyboard/pyperclip."""
    for name in ("sounddevice", "faster_whisper", "keyboard", "pyperclip"):
        monkeypatch.setitem(sys.modules, name, None)


def run(monkeypatch, tmp_path, *args):
    """Запустить диагностику без записи и вернуть код возврата."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return diagnose.main(["--root", str(tmp_path), "--no-record", *args])


def test_reports_missing_packages(monkeypatch, tmp_path, capsys, no_deps):
    code = run(monkeypatch, tmp_path)
    out = capsys.readouterr().out

    assert code == 1, "при отсутствии зависимостей код должен быть 1"
    assert "[БЕДА]" in out
    assert "sounddevice" in out and "faster_whisper" in out
    assert "файлы программы на месте" not in out or "overlay.py" in out


def test_creates_project_files_check(monkeypatch, tmp_path, capsys, no_deps):
    run(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "в проекте нет файлов" in out  # в tmp_path пусто


def test_config_defaults_are_printed(monkeypatch, tmp_path, capsys, no_deps):
    run(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "модель=small" in out
    assert "streaming=True" in out


def test_fix_writes_workable_settings(monkeypatch, tmp_path, capsys, no_deps):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(
        json.dumps({"model": "large-v3", "language": "ru", "streaming": None}),
        encoding="utf-8",
    )

    code = run(monkeypatch, tmp_path, "--fix")
    out = capsys.readouterr().out

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["model"] == "small"
    assert data["streaming"] is True
    assert data["beam_size"] == 3
    assert data["hold_back_seconds"] == 0.3
    assert data["force_commit_seconds"] == 2.5
    assert "настройки обновлены" in out
    assert (appdata / "config.json.bak").exists(), "старый конфиг должен сохраниться"
    assert code == 1  # зависимости всё ещё отсутствуют


def test_fix_keeps_valid_language(monkeypatch, tmp_path, capsys, no_deps):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(json.dumps({"language": "en"}), encoding="utf-8")
    run(monkeypatch, tmp_path, "--fix")
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["language"] == "en"


def test_corrupted_config_is_reported(monkeypatch, tmp_path, capsys, no_deps):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text("{ не json", encoding="utf-8")
    run(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "повреждён" in out


def test_utf8_stdout_does_not_crash(monkeypatch):
    stream = types.SimpleNamespace(
        reconfigure=lambda **kwargs: None,
        buffer=None,
    )
    monkeypatch.setattr(sys, "stdout", stream)
    diagnose._utf8_stdout()  # не должно падать


def test_say_accumulates_problems(capsys):
    diagnose.failures.clear()
    diagnose.warnings.clear()
    diagnose.say(diagnose.OK, "всё хорошо")
    diagnose.say(diagnose.WARN, "мелочь", "детали")
    diagnose.say(diagnose.BAD, "серьёзно", "детали")
    out = capsys.readouterr().out
    assert "[ OK ] всё хорошо" in out
    assert diagnose.failures == ["серьёзно: детали"]
    assert diagnose.warnings == ["мелочь: детали"]


def test_hotkey_check_reports_busy_combination(monkeypatch, capsys):
    module = types.ModuleType("keyboard")

    def add_hotkey(*args, **kwargs):
        raise ValueError("уже занято")

    module.add_hotkey = add_hotkey
    module.remove_hotkey = lambda handle: None
    monkeypatch.setitem(sys.modules, "keyboard", module)
    diagnose.failures.clear()

    diagnose.check_hotkeys(types.SimpleNamespace(hotkey="ctrl+alt+space"))
    out = capsys.readouterr().out
    assert "не удалось назначить" in out
    assert diagnose.failures


def test_recording_silence_is_reported(monkeypatch, capsys):
    """Тихий микрофон должен давать понятную ошибку, а не «всё ок»."""
    diagnose.failures.clear()

    class SilentRecorder:
        def __init__(self, config):
            self.peak = 0.0

        def start(self):
            return None

        def stop(self):
            class Samples:
                size = 16000

            return types.SimpleNamespace(samples=Samples(), sample_rate=16000,
                                         duration=1.0, peak=0.0)

    audio_module = types.ModuleType("golos.audio")
    audio_module.MicrophoneRecorder = SilentRecorder
    monkeypatch.setitem(sys.modules, "golos.audio", audio_module)

    diagnose.check_recording(types.SimpleNamespace(sample_rate=16000), 0.2)
    out = capsys.readouterr().out
    assert "молчит" in out
    assert diagnose.failures


# ------------------------------------------------------- CUDA и версия кода
def test_gpu_check_without_ctranslate2(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    diagnose.warnings.clear()
    assert diagnose.check_gpu(types.SimpleNamespace()) is False
    out = capsys.readouterr().out
    assert "ctranslate2 не отвечает" in out
    assert diagnose.warnings


def test_gpu_check_reports_missing_cublas(monkeypatch, capsys):
    module = types.ModuleType("ctranslate2")
    module.get_cuda_device_count = lambda: 1
    monkeypatch.setitem(sys.modules, "ctranslate2", module)

    import ctypes

    def fake_windll(name):
        raise OSError(f"{name} not found")

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    diagnose.warnings.clear()

    assert diagnose.check_gpu(types.SimpleNamespace()) is False
    out = capsys.readouterr().out
    assert "cublas64_12.dll" in out
    assert any("cublas" in w for w in diagnose.warnings)


def test_gpu_check_ok(monkeypatch, capsys):
    module = types.ModuleType("ctranslate2")
    module.get_cuda_device_count = lambda: 1
    monkeypatch.setitem(sys.modules, "ctranslate2", module)

    import ctypes

    monkeypatch.setattr(ctypes, "WinDLL", lambda name: object(), raising=False)

    assert diagnose.check_gpu(types.SimpleNamespace()) is True
    assert "библиотеки CUDA на месте" in capsys.readouterr().out


def test_fix_switches_to_cpu_without_cuda(monkeypatch, tmp_path, capsys):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(json.dumps({"model": "large-v3"}), encoding="utf-8")

    diagnose.fix_config(appdata, cuda_ok=False)
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["device"] == "cpu"
    assert data["compute_type"] == "int8"


def test_fix_keeps_auto_with_working_cuda(monkeypatch, tmp_path, capsys):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(json.dumps({}), encoding="utf-8")

    diagnose.fix_config(appdata, cuda_ok=True)
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert "device" not in data or data["device"] == "auto"


def test_code_version_detects_missing_modules(monkeypatch, tmp_path, capsys):
    diagnose.failures.clear()
    diagnose.check_code_version(tmp_path)
    out = capsys.readouterr().out
    assert "нет новых модулей" in out
    assert "git checkout main" in out
    assert diagnose.failures


def test_code_version_reads_git(monkeypatch, tmp_path, capsys):
    (tmp_path / "golos").mkdir()
    for name in ("diagnose.py", "streaming.py", "overlay.py"):
        (tmp_path / "golos" / name).write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    import subprocess

    def fake_run(cmd, **kwargs):
        args = cmd[1:]
        if args[:1] == ["rev-parse"]:
            output = "main"
        elif args[:1] == ["log"]:
            output = "abc123 обновление"
        elif args[:1] == ["rev-list"]:
            output = "2"
        else:
            output = ""
        return types.SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    diagnose.warnings.clear()
    diagnose.check_code_version(tmp_path)
    out = capsys.readouterr().out
    assert "ветка: main" in out
    assert "код отстаёт от main" in out


def test_fix_enables_streaming_and_lighter_model(monkeypatch, tmp_path, capsys):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(
        json.dumps({"streaming": False, "model": "large-v3"}), encoding="utf-8"
    )

    diagnose.fix_config(appdata, cuda_ok=False)
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["streaming"] is True
    assert data["model"] == "small"


def test_fix_keeps_light_model(monkeypatch, tmp_path, capsys):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(json.dumps({"model": "small"}), encoding="utf-8")

    diagnose.fix_config(appdata, cuda_ok=False)
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["model"] == "small"
