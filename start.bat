@echo off
rem Запуск «Голоса» без консольного окна (через pythonw).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto noinstall
start "" ".venv\Scripts\pythonw.exe" "golos_app.py"
exit /b 0

:noinstall
echo.
echo   Виртуальное окружение не найдено.
echo   Сначала запустите install.bat (нужен один раз).
echo.
pause
exit /b 1
