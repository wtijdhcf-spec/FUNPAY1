@echo off
setlocal

title FunPay Auto-Undercut Bot
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ========================================
echo   FunPay Auto-Undercut Bot
echo ========================================
echo.

if not exist venv (
    echo [*] Создание виртуального окружения...
    py -m venv venv
    echo [+] Виртуальное окружение создано
)

echo [*] Активация виртуального окружения...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [!] Ошибка: Не удалось активировать виртуальное окружение
    pause
    exit /b 1
)
echo [+] Виртуальное окружение активировано

echo [*] Установка зависимостей...
venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] Ошибка: Не удалось установить зависимости
    pause
    exit /b 1
)
echo [+] Все зависимости установлены

echo.
echo [*] Запуск бота...
echo.

venv\Scripts\python.exe main.py

pause
