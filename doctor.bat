@echo off
rem Диагностика «Голоса»: версии, пакеты, микрофоны, git, настройки, журнал.
cd /d "%~dp0"
chcp 65001 >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0doctor.ps1"
