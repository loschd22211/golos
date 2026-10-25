@echo off
rem Обновление «Голоса» до последней версии из main и перезапуск.
rem Решает типичную путаницу: рабочая копия осталась на старой ветке,
rem git pull ничего не находит, и программа выглядит «неисправленной».
cd /d "%~dp0"
chcp 65001 >nul 2>&1

where git >nul 2>&1
if errorlevel 1 goto nogit

if not exist ".git" goto nogit

echo   Останавливаю запущенный «Голос»...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*golos_app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo   Переключаюсь на main и обновляю код...
git checkout main
if errorlevel 1 goto dirty
git pull
if errorlevel 1 goto fail

echo.
git log -1 --oneline
echo.

if exist ".venv\Scripts\python.exe" (
    echo   Проверяю зависимости и библиотеки CUDA...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Quiet
)

echo.
echo   Готово. Запускаю.
echo.
call "%~dp0run.bat" %*
exit /b 0

:dirty
echo.
echo   В рабочей папке есть локальные изменения, они мешают переключению.
echo   Если они не нужны, выполните:
echo       git reset --hard
echo   и запустите update.bat снова.
echo.
pause
exit /b 1

:fail
echo.
echo   Не удалось обновить код (нет интернета или нет доступа к репозиторию).
echo   Подробности — выше. Текущая версия осталась рабочей: .\run.bat
echo.
pause
exit /b 1

:nogit
echo.
echo   Git или папка .git не найдены — обновление вручную:
echo     1. скачайте ZIP с https://github.com/loschd22211/golos
echo     2. распакуйте поверх этой папки
echo     3. запустите run.bat
echo.
pause
exit /b 1
