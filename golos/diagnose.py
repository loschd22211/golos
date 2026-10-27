"""Диагностика «Голоса» прямо в терминале.

Запуск (PowerShell):

    cd C:\\Users\\user\\Downloads\\golos
    .\\doctor.bat

или напрямую:

    .venv\\Scripts\\python.exe -m golos.diagnose
    .venv\\Scripts\\python.exe -m golos.diagnose --record 6
    .venv\\Scripts\\python.exe -m golos.diagnose --fix

Скрипт ничего не меняет без флага ``--fix``. Он проверяет по шагам: окружение,
конфиг, пакеты, микрофоны, запись и уровень, загрузку модели, распознавание,
поведение потоковой вставки, буфер обмена — и печатает ``ОК``/``ПРОБЛЕМА``
по каждому пункту, чтобы было видно, где именно ломается.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

OK = "[ OK ]"
BAD = "[БЕДА]"
WARN = "[ ?? ]"

failures: list[str] = []
warnings: list[str] = []


def _utf8_stdout() -> None:
    """Windows-консоль по умолчанию cp866 и падает на русских названиях устройств."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if sys.stdout is not None and not isinstance(sys.stdout, io.TextIOWrapper):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass


def say(status: str, text: str, detail: str = "") -> None:
    line = f"{status} {text}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)
    if status == BAD:
        failures.append(text + (f": {detail}" if detail else ""))
    elif status == WARN:
        warnings.append(text + (f": {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 46 - len(title)), flush=True)


# --------------------------------------------------------------------- checks
def check_environment(root: Path) -> None:
    section("Окружение")
    print(f"     python: {sys.version.split()[0]} ({sys.executable})")
    print(f"     проект: {root}")

    missing = [
        name
        for name in ("overlay.py", "streaming.py", "winfocus.py", "app.py")
        if not (root / "golos" / name).exists()
    ]
    if missing:
        say(BAD, "в проекте нет файлов", ", ".join(missing))
    else:
        say(OK, "файлы программы на месте")

    if sys.version_info < (3, 9):
        say(BAD, "слишком старый Python", "нужен 3.9 или новее")
    else:
        say(OK, "версия Python подходит")


def _run_with_progress(label: str, func, every: float = 5.0):
    """Выполнить долгую операцию, показывая, что программа жива.

    Загрузка модели на процессоре и её скачивание занимают минуты, а без
    вывода консоль выглядит зависшей — пользователь закрывает диагностику.
    """
    import threading

    result = {}

    def target() -> None:
        try:
            result["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - пробрасываем наверх
            result["error"] = exc

    started = time.time()
    thread = threading.Thread(target=target, name="golos-diagnose", daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(every)
        if thread.is_alive():
            print(f"     … {label}: идёт {time.time() - started:.0f} c", flush=True)
    if "error" in result:
        raise result["error"]
    return result.get("value")


def check_code_version(root: Path) -> None:
    """Не запущена ли старая копия кода — из-за этого «фиксы не работают»."""
    section("Версия кода")
    try:
        from golos import __version__

        print(f"     версия: {__version__}")
    except Exception as exc:
        say(BAD, "не удалось определить версию", str(exc))

    missing = [
        name for name in ("diagnose.py", "streaming.py", "overlay.py")
        if not (root / "golos" / name).exists()
    ]
    if missing:
        say(BAD, "в коде нет новых модулей", ", ".join(missing))
        print("     Обновитесь: git checkout main && git pull")
        return

    git_dir = root / ".git"
    if not git_dir.exists():
        say(WARN, "папка не git-репозиторий", "обновление вручную: скачать ZIP из main")
        return

    try:
        import subprocess

        def git(*args) -> str:
            return subprocess.run(
                ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=10
            ).stdout.strip()

        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        commit = git("log", "-1", "--oneline")
        print(f"     ветка: {branch}")
        print(f"     коммит: {commit}")
        behind = git("rev-list", "--count", "HEAD..origin/main")
        if behind.isdigit() and int(behind) > 0:
            say(WARN, "код отстаёт от main", f"{behind} коммит(ов) — сделайте git pull")
        else:
            say(OK, "код актуален", branch)
    except Exception as exc:
        say(WARN, "git недоступен", str(exc))


def check_gpu(config) -> bool:
    """Есть ли рабочая CUDA. Возвращает True, если видеокарта пригодна."""
    section("Видеокарта (CUDA)")
    count = 0
    try:
        import ctranslate2

        count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        say(WARN, "ctranslate2 не отвечает", str(exc))
        return False

    if count <= 0:
        say(WARN, "CUDA не найдена — распознавание пойдёт на процессоре", "это нормально, но медленнее")
        return False

    print(f"     устройств CUDA: {count}")

    try:
        from golos.cuda import enable_dlls, pip_hint

        dirs = enable_dlls()
        if dirs:
            print(f"     библиотеки CUDA из pip: {len(dirs)} папк(и)")
    except Exception:  # pragma: no cover
        def pip_hint() -> str:  # type: ignore[misc]
            return "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"

    try:
        import ctypes

        ctypes.WinDLL("cublas64_12.dll")
        ctypes.WinDLL("cublasLt64_12.dll")
        say(OK, "библиотеки CUDA на месте", "распознавание пойдёт на видеокарте")
        return True
    except Exception:
        say(
            WARN,
            "видеокарта есть, но не хватает cublas64_12.dll",
            "модель уйдёт на CPU — это в разы медленнее",
        )
        print(f"     Быстрое решение без CUDA Toolkit: .venv\\Scripts\\python.exe -m {pip_hint()}")
        print("     (или запустите install.bat заново — он поставит их сам).")
        print("     Если видеокарта не нужна: \"device\": \"cpu\" (так сделает --fix).")
        return False


def check_rights() -> bool:
    """Права администратора: от них зависит, куда вообще попадёт текст."""
    section("Права администратора")
    try:
        from golos import elevate

        admin = elevate.is_admin()
    except Exception as exc:  # pragma: no cover
        say(WARN, "не удалось проверить права", str(exc))
        return False

    if admin:
        say(OK, "запущено от администратора", "текст вставляется в любые окна")
        return True

    say(
        WARN,
        "запущено без прав администратора",
        "в окна от администратора (часто браузер) Windows ввод не пропустит",
    )
    print("     Тогда плашка писала «вставлено», а текст не появлялся.")
    print("     Решение: правый клик по иконке в трее → "
          "«Перезапустить от администратора»,")
    print("     либо запустить .\\run.bat и согласиться на запрос UAC.")
    return False


def check_target_window() -> None:
    """Куда уйдёт текст прямо сейчас: видно окно и его права."""
    section("Окно для вставки")
    import sys

    if sys.platform != "win32":
        say(WARN, "проверка только для Windows", sys.platform)
        return
    try:
        from golos import elevate, winfocus

        tracker = winfocus.TargetTracker()
        tracker.remember_current()
        print(f"     сейчас активно: {elevate.describe_window()}")
        if tracker.hwnd:
            print(f"     цель вставки:   {elevate.describe_window(tracker.hwnd)}")
            say(OK, "окно-получатель определено", "текст пойдёт туда")
        else:
            say(
                WARN,
                "чужое активное окно не найдено",
                "щёлкните в поле, куда нужен текст, и запустите диагностику снова",
            )
        if winfocus.is_our_window(winfocus.get_foreground_window()):
            print("     (активно наше окно — так бывает, если щёлкнуть по плашке;")
            print("      трекер цели всё равно печатает в последнее чужое окно)")
    except Exception as exc:
        say(WARN, "проверка окна не удалась", str(exc))


def check_packages() -> None:
    section("Пакеты")
    required = ("faster_whisper", "sounddevice", "numpy", "keyboard", "pyperclip")
    optional = ("pystray", "PIL")
    for name in required:
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "")
            say(OK, name, version)
        except Exception as exc:
            say(BAD, f"нет пакета {name}", str(exc))
    for name in optional:
        try:
            __import__(name)
            say(OK, name, "необязательный")
        except Exception as exc:
            say(WARN, f"нет пакета {name}", str(exc))


def check_config(appdata: Path) -> tuple:
    section("Настройки")
    path = appdata / "config.json"
    raw = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            say(OK, "config.json прочитан", str(path))
        except Exception as exc:
            say(BAD, "config.json повреждён", str(exc))
    else:
        say(WARN, "config.json ещё не создан", "будут значения по умолчанию")

    for key in ("model", "streaming", "stream_step", "hold_back_seconds",
                "force_commit_seconds", "language"):
        print(f"     {key}: {raw.get(key, '— (по умолчанию)')}")

    if raw.get("model") in ("medium", "large-v3") and "streaming" not in raw:
        say(WARN, "старый конфиг с тяжёлой моделью",
            "миграция переведёт на small; можно ускорить через --fix")

    try:
        from golos.config import Config, load_config

        config, broken = load_config(path)
        print(f"     итог: модель={config.model}, streaming={config.streaming}, "
              f"язык={config.language}, beam={config.beam_size}")
        if not config.streaming:
            say(BAD, "потоковая вставка выключена",
                "текст появится только после остановки записи — doctor.bat --fix включит её")
        if config.language == "ru":
            say(WARN, "язык жёстко русский",
                "английские слова будут записаны кириллицей — для смешанной речи поставьте auto")
        if config.model in ("medium", "large-v3"):
            say(WARN, f"модель {config.model} тяжёлая",
                "на процессоре первый текст появится через минуты — поставьте small")
        return config, raw
    except Exception:
        traceback.print_exc()
        say(BAD, "не удалось загрузить настройки")
        return Config(), raw


def check_devices() -> None:
    section("Микрофоны")
    try:
        import sounddevice as sd
    except Exception as exc:
        say(BAD, "sounddevice не работает", str(exc))
        return

    try:
        devices = sd.query_devices()
    except Exception as exc:
        say(BAD, "не удалось получить список устройств", str(exc))
        return

    inputs = [(i, d) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]
    if not inputs:
        say(BAD, "нет ни одного микрофона", "проверьте подключение и разрешения Windows")
        return
    for index, device in inputs:
        print(f"     {index}: {device['name']}")
    try:
        default_in = sd.default.device[0]
        print(f"     по умолчанию: {default_in}")
    except Exception:
        pass
    say(OK, "микрофоны найдены", f"{len(inputs)} устройств(а)")


def check_hotkeys(config) -> None:
    section("Горячие клавиши")
    try:
        import keyboard
    except Exception as exc:
        say(BAD, "keyboard не установлен", str(exc))
        return
    try:
        handle = keyboard.add_hotkey(config.hotkey, lambda: None)
        keyboard.remove_hotkey(handle)
        say(OK, f"сочетание {config.hotkey} свободно")
    except Exception as exc:
        say(BAD, f"не удалось назначить {config.hotkey}",
            f"{exc} (возможно, занято другой программой)")


def check_clipboard() -> None:
    section("Буфер обмена")
    try:
        import pyperclip

        pyperclip.copy("голос-тест")
        back = pyperclip.paste()
        if back == "голос-тест":
            say(OK, "чтение и запись работают")
        else:
            say(BAD, "буфер вернул другое содержимое", repr(back))
    except Exception as exc:
        say(BAD, "буфер обмена недоступен", str(exc))


def check_recording(config, seconds: float):
    section(f"Запись {seconds:g} секунд — ГОВОРИТЕ СЕЙЧАС")
    try:
        from golos.audio import MicrophoneRecorder
    except Exception:
        traceback.print_exc()
        say(BAD, "модуль записи не загрузился")
        return None

    recorder = MicrophoneRecorder(config)
    try:
        recorder.start()
    except Exception as exc:
        say(BAD, "микрофон не открылся", str(exc))
        return None

    print("     ...", end="", flush=True)
    peak = 0.0
    started = time.time()
    while time.time() - started < seconds:
        time.sleep(0.1)
        peak = max(peak, recorder.peak)
        print(".", end="", flush=True)
        # Услышали голос и уже есть что распознавать — не тянем время зря.
        if peak >= 0.03 and time.time() - started >= 3.0:
            break
    print()
    recording = recorder.stop()

    if recording is None:
        say(BAD, "микрофон не отдал звук", "проверьте разрешения Windows")
        return None

    print(f"     длительность: {recording.duration:.2f} c, пик: {recording.peak:.3f}")
    # Иногда устройство открылось, но звук почти не шёл: микрофон занят другой
    # программой. Распознавать такие полсекунды бессмысленно — предупреждаем.
    if recording.duration < seconds * 0.5:
        say(WARN, "микрофон отдал слишком мало звука",
            f"{recording.duration:.1f} c из {seconds:g} c — закройте «Голос» "
            "и другие программы с микрофоном, повторите")
        return None
    if recording.peak < 0.01:
        say(BAD, "микрофон молчит (тишина)",
            "говорить нужно сразу после этой строки; если не помогло — "
            "Параметры → Конфиденциальность → Микрофон → разрешить приложениям")
        return recording
    say(OK, "звук с микрофона есть")
    return recording


def _expected_model(config) -> str:
    """Какая модель реально загрузится (для понятных сообщений).

    «auto» — это medium на видеокарте и small на процессоре; человек должен
    видеть конкретное имя и объём загрузки, а не слово auto.
    """
    try:
        from golos.transcribe import resolve_device, resolve_model

        return resolve_model(config.model, resolve_device(config.device))
    except Exception:  # pragma: no cover - тесты с подменённым модулем
        name = (getattr(config, "model", "") or "auto").lower()
        return "small" if name == "auto" else name


def check_model(config, appdata: Path, recording):
    """Загрузить модель и распознать запись. Возвращает транслятор для проверок."""
    section("Модель и распознавание")
    try:
        from golos.transcribe import WhisperTranscriber

        transcriber = WhisperTranscriber(config, appdata / "models")
        expected_model = _expected_model(config)
        if config.model == "auto":
            print(f"     модель auto → для этого компьютера: {expected_model}")
        if transcriber.is_model_downloaded():
            print(f"     загружаю модель {expected_model} с диска…", flush=True)
        else:
            size = "~1.5 ГБ" if expected_model == "medium" else "~470 МБ"
            say(
                WARN,
                f"модель {expected_model} ещё не скачана",
                f"скачается один раз ({size}): это минуты, нужен интернет",
            )
            print(f"     качаю модель {expected_model}…", flush=True)
        started = time.time()
        _run_with_progress(f"модель {expected_model}", transcriber.preload)
        load_time = time.time() - started
        device = getattr(transcriber, "active_device", "") or "?"
        compute = getattr(transcriber, "active_compute", "") or "?"
        loaded = getattr(transcriber, "active_model", "") or expected_model
        say(OK, f"модель {loaded} загружена", f"{load_time:.1f} c")
        print(f"     реально работает на: {device}/{compute}")
        reason = getattr(transcriber, "fallback_reason", "")
        if reason:
            say(WARN, "видеокарта не использована", reason)

        if recording is None:
            say(WARN, "распознавание не проверено", "не было записи")
            return transcriber

        started = time.time()
        result = transcriber.transcribe(recording.samples, config)
        elapsed = time.time() - started
        print(f"     распознавание: {elapsed:.1f} c, язык: {result.language or '—'}")
        print(f"     текст: {result.text!r}")
        if result.text.strip():
            ratio = elapsed / max(0.1, recording.duration)
            say(OK, "речь распознаётся", f"{ratio:.2f}× от длительности")
        elif recording.peak < 0.01:
            say(WARN, "распознавать было нечего",
                "в записи тишина — модель проверена, дело не в ней")
        else:
            say(BAD, "модель вернула пустой текст",
                "проверьте микрофон и язык в настройках")
        return transcriber
    except Exception as exc:
        say(BAD, "распознавание не работает", str(exc))
        traceback.print_exc()
        return None


def check_streaming_logic(config, appdata: Path, recording, transcriber=None) -> None:
    """Проверить, что потоковый режим вообще способен что-то вставить."""
    section("Потоковая вставка (главная проверка)")
    if recording is None:
        say(WARN, "не проверено", "не было записи")
        return

    try:
        if transcriber is None:
            from golos.transcribe import WhisperTranscriber

            transcriber = WhisperTranscriber(config, appdata / "models")
        piece = recording.samples[: int(min(4.0, recording.duration) * config.sample_rate)]
        duration = piece.size / config.sample_rate
        segments = transcriber.segments(piece, config)

        limit = duration - config.hold_back_seconds
        print(f"     кусок {duration:.1f} c, порог «точно сказано» {limit:.1f} c")
        words = 0
        ready = 0
        for segment in segments:
            seg_words = segment.get("words") or []
            words += len(seg_words)
            for word in seg_words:
                mark = "вставится" if word["end"] <= limit else "придержим"
                print(f"       {word['start']:.2f}-{word['end']:.2f} "
                      f"{word['word']!r} → {mark}")
                if word["end"] <= limit:
                    ready += 1
            if not seg_words:
                print(f"       сегмент {segment['start']:.2f}-{segment['end']:.2f}: "
                      f"{segment['text']!r} (без таймингов слов)")

        if not segments:
            quiet = recording.peak < 0.01
            say(WARN if quiet else BAD,
                "модель не вернула сегментов на куске речи",
                "в записи тишина — потоковый режим не проверить" if quiet else "")
        elif ready:
            say(OK, "текст будет вставляться по ходу речи", f"готово слов: {ready}")
        else:
            forced = duration >= config.force_commit_seconds
            say(WARN if not forced else OK,
                "слов до порога нет",
                "сработает принудительная вставка" if forced else "проверьте hold_back_seconds")
    except Exception as exc:
        say(BAD, "потоковая проверка не удалась", str(exc))
        traceback.print_exc()


def check_noise_filters() -> None:
    """Проверить, что фильтры «фантомных» фраз работают."""
    section("Фильтры выдумок и тишины")
    try:
        from golos.textproc import looks_like_hallucination

        samples = [
            "Редактор субтитров А.Семкин Корректор В.Сухиашвили",
            "Продолжение следует...",
            "музыка",
        ]
        missed = [text for text in samples if not looks_like_hallucination(text)]
        if missed:
            say(BAD, "фильтр пропускает известные выдумки", ", ".join(missed))
        else:
            say(OK, "известные выдумки отбрасываются", "титры, «музыка», «продолжение»")

        from golos.config import Config

        cfg = Config()
        if cfg.vad_filter and getattr(cfg, "silence_threshold", 0) > 0:
            say(OK, "тишина не уходит в модель", f"порог {cfg.silence_threshold}")
        else:
            say(WARN, "фильтр тишины выключен", "будет медленнее и больше выдумок")
    except Exception as exc:  # pragma: no cover
        say(WARN, "проверка фильтров не удалась", str(exc))


def check_log(appdata: Path) -> None:
    section("Журнал")
    path = appdata / "golos.log"
    if not path.exists():
        say(WARN, "журнала пока нет", str(path))
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        say(BAD, "журнал не читается", str(exc))
        return

    interesting = [
        line for line in lines
        if any(key in line for key in ("ERROR", "Traceback", "Поток", "вставляю", "Запись"))
    ]
    for line in interesting[-15:]:
        print("     " + line)
    errors = [line for line in lines[-200:] if " ERROR " in line]
    if errors:
        say(WARN, "в журнале есть ошибки", f"{len(errors)} за последнее время")
    else:
        say(OK, "ошибок в журнале нет", str(path))


def fix_config(appdata: Path, cuda_ok: bool | None = None) -> None:
    section("Исправление настроек (--fix)")
    path = appdata / "config.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        backup = path.with_suffix(".json.bak")
        try:
            backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"     копия: {backup}")
        except Exception:
            pass

    data.update(
        {
            "streaming": True,
            "stream_step": 0.35,
            "min_chunk_seconds": 0.8,
            "hold_back_seconds": 0.3,
            "force_commit_seconds": 2.5,
            "beam_size": 3,
        }
    )
    # «auto» на коротких кусках путал русскую речь с английской
    # («Fiking», «Thank you») — фиксируем русский, английские термины
    # модель всё равно знает. Кому нужно иначе — «Только английский».
    if data.get("language") not in ("ru", "en"):
        data["language"] = "ru"
    if data.get("streaming") is not True:
        data["streaming"] = True
    if cuda_ok:
        # Видеокарта рабочая — незачем сидеть на процессоре.
        data["device"] = "auto"
        data["compute_type"] = "auto"
    if cuda_ok is False:
        # Незачем каждый запуск пытаться поднять CUDA, если библиотек нет:
        # это лишние секунды и запись в журнал.
        data["device"] = "cpu"
        data["compute_type"] = "int8"
        if data.get("model") in ("medium", "large", "large-v2", "large-v3"):
            # Без видеокарты большая модель превратит каждую фразу в ожидание.
            data["model"] = "auto"
    # Модель не трогаем, если человек выбрал что-то разумное: «auto» ставим
    # только когда в конфиге мусор или неподъёмная для этого компьютера модель.
    if data.get("model") not in ("auto", "tiny", "base", "small", "medium", "large-v3"):
        data["model"] = "auto"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        say(OK, "настройки обновлены", str(path))
        print(f"     модель {data.get('model')}, язык {data.get('language')}, "
              "потоковая вставка включена, beam 3")
        if cuda_ok:
            print("     устройство auto — видеокарта работает, будет быстро")
            if data.get("model") == "auto":
                print("     модель auto → на видеокарте возьмётся medium "
                      "(скачается ~1.5 ГБ один раз, точность выше)")
        print("     перезапустите «Голос» (плашка → ✕, затем ярлык), "
              "чтобы настройки применились")
        if data.get("device") == "cpu":
            print("     устройство cpu/int8 (CUDA недоступна), язык: "
                  f"{data.get('language', 'ru')}")
    except Exception as exc:
        say(BAD, "не удалось записать настройки", str(exc))


# ----------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golos.diagnose", description="Диагностика «Голоса»")
    parser.add_argument("--root", default="", help="папка проекта (по умолчанию текущая)")
    parser.add_argument("--record", type=float, default=5.0, help="сколько секунд писать микрофон")
    parser.add_argument("--fix", action="store_true", help="записать рабочие настройки в config.json")
    parser.add_argument("--no-record", action="store_true", help="не записывать микрофон")
    args = parser.parse_args(argv)

    _utf8_stdout()
    root = Path(args.root).resolve() if args.root else Path.cwd()
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Golos"

    print("Диагностика «Голоса»")
    print(f"Дата: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    check_environment(root)
    check_code_version(root)
    config, raw = check_config(appdata)
    check_rights()
    check_target_window()
    check_packages()
    cuda_ok = check_gpu(config)
    if args.fix:
        fix_config(appdata, cuda_ok=cuda_ok)
        config, raw = check_config(appdata)
    check_devices()
    check_hotkeys(config)
    check_clipboard()

    recording = None
    if not args.no_record:
        recording = check_recording(config, max(2.0, args.record))
    transcriber = check_model(config, appdata, recording)
    check_streaming_logic(config, appdata, recording, transcriber)
    check_noise_filters()
    check_log(appdata)

    section("Итог")
    if failures:
        print(f"{BAD} проблем: {len(failures)}")
        for item in failures:
            print(f"     - {item}")
        print(f"\nИтог: проблем {len(failures)} — смотрите строки [БЕДА] выше.")
    else:
        print(f"{OK} критичных проблем не найдено")
        print("\nИтог: всё в порядке. Запускайте .\\run.bat и диктуйте.")
    if warnings:
        print(f"{WARN} замечаний: {len(warnings)}")
        for item in warnings:
            print(f"     - {item}")

    print(
        "\nЧто делать дальше:\n"
        "  1. Если есть проблемы — исправьте их и запустите диагностику снова.\n"
        "  2. Настройки можно привести к рабочим: .venv\\Scripts\\python.exe -m golos.diagnose --fix\n"
        "  3. Запуск: .\\run.bat   Подробный журнал: .\\run.bat --debug"
    )
    print(f"\nЖурнал: {appdata / 'golos.log'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
