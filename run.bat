@echo off
rem Голос — единая точка входа: при первом запуске ставит зависимости, потом запускает.
rem
rem   .\run.bat            — обычный запуск (с окном программы)
rem
rem Ежедневный запуск — ярлык «Голос» (рабочий стол / «Пуск») или двойной
rem щелчок по golos.vbs: они запускают программу без окна консоли.
rem   .\run.bat --silent   — только трей, без окна (так работает автозапуск)
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

rem Если программа уже работает — второй раз не запускаем.
".venv\Scripts\python.exe" "golos_app.py" --status >nul 2>&1
if not errorlevel 1 goto already_running

start "" ".venv\Scripts\pythonw.exe" "golos_app.py" %*
echo   Запускаю...
echo   Если Windows спросит разрешение (UAC) - нажмите "Да": с правами
echo   администратора текст вставляется в любые окна, включая браузер.
echo.
set tries=0
:wait_start
ping -n 2 127.0.0.1 >nul 2>&1
".venv\Scripts\python.exe" "golos_app.py" --status >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% lss 20 goto wait_start
goto launch_failed

:ready
echo.
echo   Готово: «Голос» работает.
echo     - плашка с микрофоном — внизу справа;
echo     - если плашки не видно: стрелка ^ у часов -^> правый клик по микрофону -^>
echo     - запись: Ctrl+Alt+Space (нажали, сказали, нажали ещё раз);
echo     - дальше запускайте ярлык «Голос»: консоль больше не нужна.
exit /b 0

:already_running
echo.
echo   «Голос» уже запущен — второй раз не нужно.
echo     Иконка микрофона в трее рядом с часами (если не видно: стрелка ^).
echo     Клавиша записи: Ctrl+Alt+Space
echo     Плашка: правый клик по иконке -^> "Открыть «Голос»"
echo.
echo   Диагностика в терминале: .\doctor.bat
exit /b 0

:launch_failed
echo.
echo   Программа не смогла запуститься. Запускаю в режиме с консолью,
echo   чтобы показать ошибку:
echo.
goto run_debug

:run_debug
".venv\Scripts\python.exe" "golos_app.py" --debug --show
echo.
echo   Ошибка выше также записана в журнал: %APPDATA%\Golos\golos.log
echo   Полная диагностика: .\doctor.bat
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
