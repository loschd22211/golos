# Голос — диагностика (PowerShell).
#
#   .\doctor.ps1              проверить всё и записать отчёт
#   .\doctor.ps1 -Fix         привести config.json к рабочим значениям
#   .\doctor.ps1 -Record 8    писать микрофон 8 секунд
#
# Отчёт дублируется в diagnostic.txt.

param(
    [switch]$Fix,
    [int]$Record = 5
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Окружение .venv не найдено. Сначала запустите install.bat" -ForegroundColor Yellow
    Read-Host "Нажмите Enter для выхода"
    exit 1
}

$argsList = @('-m', 'golos.diagnose', '--record', "$Record")
if ($Fix) { $argsList += '--fix' }

& $python @argsList 2>&1 | Tee-Object -FilePath (Join-Path $root "diagnostic.txt")
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "Отчёт сохранён: diagnostic.txt" -ForegroundColor Green
} else {
    Write-Host "Есть проблемы — см. строки [БЕДА]. Отчёт: diagnostic.txt" -ForegroundColor Yellow
    Write-Host "Быстрое лечение настроек: .\doctor.ps1 -Fix" -ForegroundColor Yellow
}
Read-Host "Нажмите Enter для выхода"
exit $code
