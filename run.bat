@echo off
rem Голос — единая точка входа: при первом запуске ставит зависимости, потом просто запускает.
rem
rem   .\run.bat            — обычный запуск (без консоли, иконка в трее)
rem   .\run.bat --show     — сразу показать окно программы
rem   .\run.bat --debug    — запуск с консолью и подробным журналом
rem   .\run.bat --no-gui   — без интерфейса, текст печатается в консоль
cd /d "%~dp0"
chcp 65001 >nul 2>&1

if exist ".venv\Scripts\pythonw.exe" goto run

echo.
echo   Первый запуск: устанавливаю зависимости (несколько минут)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Quiet
if errorlevel 1 goto fail
if not exist ".venv\Scripts\pythonw.exe" goto fail
echo.
echo   Установка завершена, запускаю «Голос».
echo.

:run
if /i "%~1"=="--debug" goto run_debug
if /i "%~1"=="--console" goto run_debug
if /i "%~1"=="--no-gui" goto run_console
start "" ".venv\Scripts\pythonw.exe" "golos_app.py" %*
exit /b 0

:run_debug
".venv\Scripts\python.exe" "golos_app.py" --debug
pause
exit /b 0

:run_console
".venv\Scripts\python.exe" "golos_app.py" --no-gui --debug
pause
exit /b 0

:fail
echo.
echo   Не удалось установить зависимости.
echo   Проверьте, что установлен Python 3.10+ (галочка "Add python.exe to PATH"),
echo   и что есть интернет. Подробности — выше в этом окне.
echo.
pause
exit /b 1
