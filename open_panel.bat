@echo off
setlocal
title FunPay Panel (без бота)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ========================================
echo   FunPay Panel - только панель
echo   Бот НЕ запускается, только сайт
echo ========================================
echo.

if not exist venv\Scripts\python.exe (
    echo [!] Нет venv. Сначала запустите start.bat один раз.
    pause
    exit /b 1
)

echo [*] Открываю панель в браузере...
echo.
venv\Scripts\python.exe web_ui.py
echo.
pause
