@echo off
chcp 65001 >nul
title Кабинет семейного учёта
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Поставьте его с python.org и отметьте галочку
  echo "Add Python to PATH", затем запустите этот файл снова.
  pause
  exit /b 1
)

echo Запускаю кабинет. Окно не закрывать — пока оно открыто, работает учёт.
echo Остановить: Ctrl+C или закрыть это окно.
python server.py --port 8090 --otkryt
pause
