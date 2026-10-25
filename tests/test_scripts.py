"""Проверки Windows-скриптов: их легко сломать незаметно."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATS = ["run.bat", "update.bat", "doctor.bat", "install.bat"]


def test_bat_files_use_crlf():
    for name in BATS:
        path = ROOT / name
        assert path.exists(), f"нет {name}"
        data = path.read_bytes()
        assert b"\r\n" in data, f"{name}: нет CRLF, cmd.exe сломает goto"
        assert data.count(b"\n") == data.count(b"\r\n"), f"{name}: смешаны LF и CRLF"


def test_doctor_bat_uses_python_module():
    text = (ROOT / "doctor.bat").read_text(encoding="utf-8")
    assert "golos.diagnose" in text
    assert "PYTHONIOENCODING" in text.lower() or "65001" in text


def test_update_bat_switches_to_main_and_runs():
    text = (ROOT / "update.bat").read_text(encoding="utf-8")
    assert "git checkout main" in text
    assert "git pull" in text
    assert "run.bat" in text


def test_run_bat_starts_app():
    text = (ROOT / "run.bat").read_text(encoding="utf-8")
    assert "golos_app.py" in text
