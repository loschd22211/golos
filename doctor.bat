@echo off
rem Диагностика «Голоса» в терминале: микрофон, модель, распознавание, вставка.
rem Ничего не меняет без флага --fix.
cd /d "%~dp0"
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" goto noinstall

echo.
echo   Проверяю всё по шагам. Когда появится "Запись N секунд" — говорите!
echo.
".venv\Scripts\python.exe" -m golos.diagnose %*
set RESULT=%errorlevel%
echo.
if "%RESULT%"=="0" (
    echo   Диагностика закончена: критичных проблем нет.
) else (
    echo   Диагностика нашла проблемы — смотрите строки [БЕДА] выше.
    echo   Быстрое лечение настроек: .\doctor.bat --fix
)
echo.
pause
exit /b %RESULT%

:noinstall
echo.
echo   Окружение .venv не найдено.
echo   Сначала запустите install.bat (или run.bat), потом doctor.bat.
echo.
pause
exit /b 1
