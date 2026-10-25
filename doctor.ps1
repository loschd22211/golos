# Голос — диагностика (Windows)
#
# Запускается из doctor.bat. Собирает всё, что нужно, чтобы понять, почему
# программа не работает: версии Python, состояние окружения, ветка git,
# запущенные процессы, настройки, микрофоны и последние строки журнала.

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$report = Join-Path $root "diagnostic.txt"
$lines = New-Object System.Collections.Generic.List[string]

function Add-Line($text = "") { $lines.Add([string]$text); Write-Host $text }
function Add-Section($title) {
    Add-Line ""
    Add-Line ("=== " + $title + " " + ("=" * [Math]::Max(0, 40 - $title.Length)))
}

Add-Line "Голос — диагностика"
Add-Line ("Дата: " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"))
Add-Line ("Папка: " + $root)

Add-Section "Система"
Add-Line ("Windows: " + [System.Environment]::OSVersion.VersionString)
Add-Line ("PowerShell: " + $PSVersionTable.PSVersion.ToString())

Add-Section "Python"
$pyCmd = $null
foreach ($candidate in @('py -3', 'python')) {
    try {
        $version = & cmd /c "$candidate --version" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Add-Line ("$candidate -> " + $version)
            if (-not $pyCmd) { $pyCmd = $candidate }
        }
    } catch { }
}
if (-not $pyCmd) { Add-Line "Python 3 не найден в PATH (это и есть причина)" }

Add-Section "Виртуальное окружение"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvPythonW = Join-Path $root ".venv\Scripts\pythonw.exe"
if (Test-Path $venvPython) {
    Add-Line (".venv найден: " + (& $venvPython --version 2>&1))
    Add-Line ("pythonw: " + (Test-Path $venvPythonW))
    Add-Line ""
    Add-Line "Установленные пакеты:"
    & $venvPython -m pip list --disable-pip-version-check 2>&1 |
        Where-Object { $_ -match "faster-whisper|ctranslate2|sounddevice|numpy|keyboard|pyperclip|pystray|Pillow|av|huggingface" } |
        ForEach-Object { Add-Line ("  " + $_) }

    Add-Line ""
    Add-Line "Проверка импортов:"
    & $venvPython -c @"
import importlib
for name in ('faster_whisper','sounddevice','numpy','keyboard','pyperclip','pystray','PIL','tkinter'):
    try:
        importlib.import_module(name)
        print('  ok   ', name)
    except Exception as exc:
        print('  FAIL ', name, '->', exc)
"@ | ForEach-Object { Add-Line $_ }

    Add-Line ""
    Add-Line "Микрофоны (sounddevice):"
    & $venvPython -c @"
try:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d.get('max_input_channels', 0) > 0:
            print('  %d: %s' % (i, d['name']))
except Exception as exc:
    print('  ошибка:', exc)
"@ | ForEach-Object { Add-Line $_ }
} else {
    Add-Line ".venv не найден — запустите .\run.bat (он установит зависимости)"
}

Add-Section "Git"
if (Test-Path (Join-Path $root ".git")) {
    Add-Line ("Ветка: " + (& git rev-parse --abbrev-ref HEAD 2>&1))
    Add-Line ("Коммит: " + (& git log -1 --oneline 2>&1))
    Add-Line ("Файл golos/ui.py присутствует: " + (Test-Path (Join-Path $root "golos\ui.py")))
    Add-Line ("Файл golos/settings_window.py присутствует: " + (Test-Path (Join-Path $root "golos\settings_window.py")))
    & git status --short 2>&1 | ForEach-Object { Add-Line ("  " + $_) }
} else {
    Add-Line "Папка не является git-репозиторием (распакован ZIP)"
    Add-Line ("Файл golos/ui.py присутствует: " + (Test-Path (Join-Path $root "golos\ui.py")))
}

Add-Section "Процессы"
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*golos_app.py*" }
if ($procs) {
    foreach ($p in $procs) { Add-Line ("  запущен PID " + $p.ProcessId) }
} else {
    Add-Line "  «Голос» не запущен"
}

Add-Section "Настройки"
$configPath = Join-Path $env:APPDATA "Golos\config.json"
if (Test-Path $configPath) {
    Get-Content $configPath -Encoding UTF8 | ForEach-Object { Add-Line ("  " + $_) }
} else {
    Add-Line ("  нет файла " + $configPath)
}

Add-Section "Журнал (последние 40 строк)"
$logPath = Join-Path $env:APPDATA "Golos\golos.log"
if (Test-Path $logPath) {
    Get-Content $logPath -Tail 40 -Encoding UTF8 | ForEach-Object { Add-Line ("  " + $_) }
} else {
    Add-Line ("  нет файла " + $logPath)
}

try {
    $lines -join [Environment]::NewLine | Out-File -FilePath $report -Encoding UTF8
    Write-Host ""
    Write-Host ("Отчёт сохранён: " + $report) -ForegroundColor Green
} catch {
    Write-Host ("Не удалось сохранить отчёт: " + $_.Exception.Message) -ForegroundColor Yellow
}

Read-Host "`nНажмите Enter для выхода"
