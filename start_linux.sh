#!/bin/bash
echo "========================================================"
echo "         FunPay Auto-Undercut Bot (Linux/VPS)          "
echo "========================================================"

if [ ! -d "venv" ]; then
    echo "[*] Создание виртуального окружения Python..."
    python3 -m venv venv
fi

echo "[*] Активация venv..."
source venv/bin/activate

echo "[*] Установка зависимостей..."
pip install -r requirements.txt --quiet

echo "[*] Запуск бота..."
python3 main.py
