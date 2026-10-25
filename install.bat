@echo off
rem Голос — установка. Запускает install.ps1 с обходом политики выполнения скриптов.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
