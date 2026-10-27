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
    assert "модель=auto" in out
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
    # «auto» = medium на видеокарте, small на процессоре: тяжёлую модель не
    # навязываем, но и не отбираем возможность быть точнее на GPU.
    assert data["model"] == "auto"
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
    assert data["model"] == "auto"


def test_fix_keeps_light_model(monkeypatch, tmp_path, capsys):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(json.dumps({"model": "base"}), encoding="utf-8")

    diagnose.fix_config(appdata, cuda_ok=False)
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["model"] == "base", "лёгкий выбор пользователя не переписываем"


# ------------------------------------- прогресс при загрузке и переиспользование
class FakeTranscriber:
    def __init__(self, config, models_dir=None):
        self.config = config
        self.models_dir = models_dir
        self.active_device = "cpu"
        self.active_compute = "int8"
        self.fallback_reason = ""
        self.preloads = 0
        self.downloaded = False

    def is_model_downloaded(self, model=None):
        return self.downloaded

    def preload(self):
        self.preloads += 1

    def segments(self, samples, config, **kwargs):
        return [
            {"start": 0.0, "end": 0.6, "text": "привет",
             "words": [{"start": 0.0, "end": 0.6, "word": "привет"}]},
        ]

    def transcribe(self, samples, config):
        return types.SimpleNamespace(text="привет", language="ru")


class FakeSamples:
    """Замена numpy-массива: нужны только size и срез."""

    def __init__(self, size):
        self.size = size

    def __getitem__(self, item):
        start, stop = item.start or 0, item.stop if item.stop is not None else self.size
        return FakeSamples(max(0, min(stop, self.size) - start))


def _patch_transcriber(monkeypatch, transcriber):
    module = types.ModuleType("golos.transcribe")
    module.WhisperTranscriber = lambda config, models_dir=None: transcriber
    module.TranscriptionError = RuntimeError
    monkeypatch.setitem(sys.modules, "golos.transcribe", module)


def test_check_model_warns_about_download(monkeypatch, tmp_path, capsys):
    from golos.config import Config

    transcriber = FakeTranscriber(Config())
    _patch_transcriber(monkeypatch, transcriber)
    diagnose.warnings.clear()

    result = diagnose.check_model(Config(), tmp_path, None)

    out = capsys.readouterr().out
    assert "ещё не скачана" in out
    assert "470" in out
    assert result is transcriber
    assert any("скачана" in w for w in diagnose.warnings)


def test_streaming_check_reuses_loaded_model(monkeypatch, tmp_path, capsys):
    from golos.config import Config

    config = Config()
    transcriber = FakeTranscriber(config)
    _patch_transcriber(monkeypatch, transcriber)
    recording = types.SimpleNamespace(
        samples=FakeSamples(int(config.sample_rate * 3)),
        sample_rate=config.sample_rate,
        duration=3.0,
        peak=0.5,
    )

    diagnose.check_streaming_logic(config, tmp_path, recording, transcriber)

    assert "вставится" in capsys.readouterr().out
    assert transcriber.preloads == 0  # модель уже загружена выше в отчёте


def test_run_with_progress_reports_long_work(capsys):
    import time as _time

    value = diagnose._run_with_progress("модель", lambda: (_time.sleep(0.05), "готово")[1])
    assert value == "готово"


def test_run_with_progress_raises(capsys):
    def boom():
        raise RuntimeError("нет интернета")

    with pytest.raises(RuntimeError, match="интернет"):
        diagnose._run_with_progress("модель", boom)


def test_target_window_check_outside_windows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    diagnose.warnings.clear()
    diagnose.check_target_window()
    out = capsys.readouterr().out
    assert "только для Windows" in out
    assert diagnose.warnings


def test_target_window_check_reports_target(monkeypatch, capsys):
    from golos import winfocus

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winfocus.TargetTracker, "remember_current", lambda self: True)
    monkeypatch.setattr(winfocus.TargetTracker, "hwnd", property(lambda self: 4242))
    monkeypatch.setattr(winfocus.TargetTracker, "resolve_target", lambda self: 4242)
    monkeypatch.setattr(winfocus, "get_foreground_window", lambda: 4242)
    monkeypatch.setattr(winfocus, "is_our_window", lambda hwnd: False)

    from golos import elevate

    monkeypatch.setattr(
        elevate, "describe_window", lambda hwnd=None: "«Chrome» (chrome.exe)"
    )

    diagnose.check_target_window()
    out = capsys.readouterr().out
    assert "цель вставки" in out
    assert "окно-получатель определено" in out


def test_noise_filters_check(capsys):
    diagnose.warnings.clear()
    diagnose.failures.clear()
    diagnose.check_noise_filters()
    out = capsys.readouterr().out
    assert "известные выдумки отбрасываются" in out
    assert "тишина не уходит в модель" in out
    assert not diagnose.failures


def test_fix_enables_gpu_when_cuda_works(tmp_path, capsys):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(
        json.dumps({"device": "cpu", "compute_type": "int8"}), encoding="utf-8"
    )

    diagnose.fix_config(appdata, cuda_ok=True)
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["device"] == "auto"
    assert data["compute_type"] == "auto"


def test_fix_pins_russian_language(monkeypatch, tmp_path, capsys, no_deps):
    """--fix ставит русский: автоопределение по кускам давало иероглифы."""
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(json.dumps({"language": "auto"}), encoding="utf-8")

    run(monkeypatch, tmp_path, "--fix")
    capsys.readouterr()

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["language"] == "ru"


def test_verdict_is_printed_by_python(monkeypatch, tmp_path, capsys, no_deps):
    """Итог печатает сама диагностика, чтобы вердикт был один."""
    run(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "Итог:" in out


def test_test_phrase_checks_mixed_speech():
    """Образец для чтения содержит и русские слова, и английские термины."""
    assert "Проверка" in diagnose.TEST_PHRASE
    assert "commit" in diagnose.TEST_PHRASE


def test_phrase_similarity():
    assert diagnose.phrase_similarity(diagnose.TEST_PHRASE, diagnose.TEST_PHRASE) == 1.0
    assert diagnose.phrase_similarity(diagnose.TEST_PHRASE, "") == 0.0
    assert diagnose.phrase_similarity(diagnose.TEST_PHRASE, "もう ガ サ 助手") < 0.2


def test_performance_warns_when_gpu_unused(monkeypatch, capsys):
    """Видеокарта есть, а в настройках процессор — самая частая причина «медленно»."""
    from golos.config import Config

    diagnose.warnings.clear()
    diagnose.failures.clear()
    diagnose.check_performance(Config(model="base", device="cpu"), cuda_ok=True)
    out = capsys.readouterr().out

    assert "выбран процессор" in out
    assert "base" in out          # слабая модель тоже названа
    assert diagnose.warnings


def test_performance_ok_on_gpu(capsys):
    from golos.config import Config

    diagnose.warnings.clear()
    diagnose.check_performance(Config(model="small", device="cuda"), cuda_ok=True)
    assert "считаем на видеокарте" in capsys.readouterr().out


def test_fix_moves_weak_model_to_small(monkeypatch, tmp_path, capsys, no_deps):
    appdata = tmp_path / "appdata" / "Golos"
    appdata.mkdir(parents=True)
    (appdata / "config.json").write_text(
        json.dumps({"model": "base", "device": "cpu"}), encoding="utf-8"
    )

    diagnose.fix_config(appdata, cuda_ok=True)

    data = json.loads((appdata / "config.json").read_text(encoding="utf-8"))
    assert data["model"] == "small"
    assert data["device"] == "auto"


def test_noise_filters_check_reports_music_and_confidence(monkeypatch, capsys):
    """Диагностика должна подтверждать новые фильтры фантомов."""
    diagnose.failures.clear()
    diagnose.warnings.clear()

    diagnose.check_noise_filters()
    out = capsys.readouterr().out

    assert "музыка и гул не уходят в модель" in out
    assert "неуверенные слова отбрасываются" in out
    assert not diagnose.failures
