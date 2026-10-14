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
    echo [*] Creating virtual environment...
    py -m venv venv 2>nul
    if errorlevel 1 (
        python -m venv venv 2>nul
    )
    if not exist venv\Scripts\python.exe (
        echo [!] Error: neither 'py' nor 'python' found. Install Python 3.10+ from python.org
        pause
        exit /b 1
    )
    echo [+] Virtual environment created
)

if not exist venv\Scripts\python.exe (
    echo [!] Error: venv broken. Delete 'venv' folder and run again.
    pause
    exit /b 1
)
echo [*] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [!] Error: Failed to activate virtual environment
    pause
    exit /b 1
)
echo [+] Virtual environment activated

echo [*] Installing dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] Error: Failed to install dependencies
    pause
    exit /b 1
)
echo [+] All dependencies installed

echo.
echo [*] Starting bot... If window closes, read error below.
echo.
venv\Scripts\python.exe main.py
echo.
echo [!] Bot stopped with code %errorlevel%. Check bot.log and config.json (golden_key).
pause
