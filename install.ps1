# Голос — установка (Windows)
#
# Запускается из install.bat, но можно запустить и вручную:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Что делает:
#   1. находит Python 3.9+;
#   2. создаёт виртуальное окружение .venv рядом с программой;
#   3. ставит зависимости (faster-whisper, sounddevice, keyboard, pystray…);
#   4. создаёт ярлык «Голос» на рабочем столе и в меню «Пуск».

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "   $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "   $text" -ForegroundColor Yellow }

Write-Host "===============================================" -ForegroundColor DarkCyan
Write-Host "  Голос — офлайн-диктовка с микрофона в текст" -ForegroundColor White
Write-Host "===============================================" -ForegroundColor DarkCyan

# ------------------------------------------------------------------ 1. Python
Write-Step "Проверяю Python"

$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyCmd = 'py -3' }
}
if (-not $pyCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyCmd = 'python' }
}

if (-not $pyCmd) {
    Write-Warn "Не найден Python 3.9 или новее."
    Write-Host ""
    Write-Host "Скачайте Python с https://www.python.org/downloads/windows/" -ForegroundColor Yellow
    Write-Host "При установке обязательно отметьте галочку 'Add python.exe to PATH'." -ForegroundColor Yellow
    Write-Host "Затем запустите install.bat заново." -ForegroundColor Yellow
    Read-Host "`nНажмите Enter для выхода"
    exit 1
}
& cmd /c "$pyCmd --version"
Write-Ok "Python найден ($pyCmd)"

# ------------------------------------------------------------------ 2. venv
Write-Step "Создаю виртуальное окружение .venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & cmd /c "$pyCmd -m venv `".venv`""
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Не удалось создать venv. Попробуйте: $pyCmd -m venv .venv"
        Read-Host "Нажмите Enter для выхода"
        exit 1
    }
    Write-Ok "Окружение создано"
} else {
    Write-Ok "Окружение уже есть — использую его"
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"

# ------------------------------------------------------------------ 3. зависимости
Write-Step "Устанавливаю зависимости (это займёт несколько минут)"
& cmd /c "`"$venvPython`" -m pip install --upgrade pip --quiet"
& cmd /c "`"$venvPython`" -m pip install -r `"$root\requirements.txt`""
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Установка зависимостей не удалась. Проверьте интернет и запустите install.bat снова."
    Read-Host "Нажмите Enter для выхода"
    exit 1
}
Write-Ok "Зависимости установлены"

# ------------------------------------------------------------------ 4. ярлыки
Write-Step "Создаю ярлык «Голос»"

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$script = Join-Path $root "golos_app.py"
$icon = Join-Path $root "assets\golos.ico"

$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "Голос.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "Голос.lnk")
)

foreach ($lnk in $targets) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($lnk)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = "`"$script`""
        $shortcut.WorkingDirectory = $root
        if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
        $shortcut.Description = "Голос — диктовка в любое поле ввода"
        $shortcut.Save()
        Write-Ok $lnk
    } catch {
        Write-Warn "Не удалось создать ярлык $lnk : $($_.Exception.Message)"
    }
}

# ------------------------------------------------------------------ 5. итог
Write-Host "`nГотово!" -ForegroundColor Green
Write-Host @"

Как пользоваться:
  1. Запустите «Голос» (ярлык на рабочем столе). В трее появится иконка микрофона.
  2. Поставьте курсор в любое поле ввода (браузер, Word, Telegram, VS Code…).
  3. Нажмите Ctrl+Alt+Space и говорите. Нажмите ещё раз — текст появится.
     Esc во время записи — отмена.

Первый запуск: модель Whisper скачается один раз (medium ≈ 1.5 ГБ).
Дальше интернет не нужен — всё распознаётся на вашем компьютере.

Настройки: правый клик по иконке в трее → «Настройки…»
Журнал:  %APPDATA%\Golos\golos.log

"@ -ForegroundColor Gray

Read-Host "Нажмите Enter для выхода"
