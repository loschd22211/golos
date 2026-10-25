@echo off
rem Запуск с консолью и подробным журналом — удобно для диагностики.
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" goto noinstall
".venv\Scripts\python.exe" "golos_app.py" --debug %*
pause
exit /b 0

:noinstall
echo   Сначала запустите install.bat
pause
exit /b 1
