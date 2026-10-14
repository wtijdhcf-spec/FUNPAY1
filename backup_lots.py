"""
Скрипт для полного резервного копирования всех активных лотов пользователя с FunPay
(Заголовки, описания, текст после оплаты, цены, количество, категории и параметры).
"""

import json
import logging
import os
import time
from typing import Any, Dict, List

from colorama import Fore, init

from funpay_api import FunPayClient

init(autoreset=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BackupLots")


def export_all_lots(config_path: str = "config.json", output_json: str = "lots_backup.json", output_txt: str = "lots_backup.txt"):
    if not os.path.exists(config_path):
        print(Fore.RED + f"Файл {config_path} не найден!")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    golden_key = config.get("golden_key")
    if not golden_key:
        print(Fore.RED + "golden_key не указан в config.json!")
        return

    client = FunPayClient(golden_key=golden_key)
    
    print(Fore.CYAN + "[*] Проверка авторизации на FunPay...")
    auth_ok, user_id, username, err = client.check_auth()
    if not auth_ok:
        print(Fore.RED + f"[✕] Ошибка авторизации: {err}")
        return

    print(Fore.GREEN + f"[✓] Успешно авторизован как {username} (ID: {user_id})")
    print(Fore.CYAN + "[*] Получение списка активных лотов...")

    user_lots = client.get_user_active_lots()
    if user_lots is None:
        print(Fore.RED + "[!] FunPay вернул защитную страницу (CAPTCHA/лимит). Повторите позже.")
        return
    if not user_lots:
        print(Fore.YELLOW + "[!] Активных лотов не найдено.")
        return

    print(Fore.GREEN + f"[✓] Найдено лотов: {len(user_lots)}. Начинаем детальный экспорт всех полей...\n")

    full_lots_data: List[Dict[str, Any]] = []

    for idx, lot in enumerate(user_lots, 1):
        print(f"[{idx}/{len(user_lots)}] Сохранение лота #{lot.lot_id}: {Fore.WHITE}{lot.title[:45]}...")
        time.sleep(1.0)  # Защита от лимитов

        details = client.get_lot_details(lot.lot_id, is_chip=lot.is_chip)
        if not details:
            print(Fore.RED + f"  ✗ Не удалось получить детали лота #{lot.lot_id}")
            continue

        form_data = details.get("form_data", {})
        
        lot_entry = {
            "lot_id": lot.lot_id,
            "node_id": details.get("node_id", ""),
            "category_name": details.get("category_name", lot.category_name),
            "category_url": details.get("category_url", lot.category_url),
            "seller_price": details.get("seller_price", 0.0),
            "buyer_price": details.get("buyer_price", lot.current_price),
            "is_chip": lot.is_chip,
            "form_fields": form_data,
            # Извлекаем основные читаемые поля для удобства
            "summary_ru": form_data.get("fields[summary][ru]", ""),
            "summary_en": form_data.get("fields[summary][en]", ""),
            "desc_ru": form_data.get("fields[desc][ru]", ""),
            "desc_en": form_data.get("fields[desc][en]", ""),
            "payment_msg_ru": form_data.get("fields[payment_msg][ru]", ""),
            "payment_msg_en": form_data.get("fields[payment_msg][en]", ""),
            "amount": form_data.get("amount", ""),
            "price": form_data.get("price", ""),
            "active": form_data.get("active", "on"),
            "auto_delivery": form_data.get("auto_delivery", ""),
        }
        
        full_lots_data.append(lot_entry)
        print(Fore.GREEN + f"  ✓ Сохранено (Раздел #{lot_entry['node_id']} | Цена: {lot_entry['price']} ₽)")

    # 1. Сохранение в JSON
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(full_lots_data, f, indent=2, ensure_ascii=False)

    # 2. Сохранение в текстовый человекочитаемый отчет
    with open(output_txt, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"  РЕЗЕРВНАЯ КОПИЯ ЛОТОВ FUNPAY ({username} | ID: {user_id})\n")
        f.write(f"  Всего лотов: {len(full_lots_data)}\n")
        f.write("=" * 80 + "\n\n")

        for idx, item in enumerate(full_lots_data, 1):
            f.write(f"--- [Лот #{idx}] ID: {item['lot_id']} | Раздел: {item['category_name']} (Node ID: {item['node_id']}) ---\n")
            f.write(f"Заголовок (RU): {item['summary_ru']}\n")
            if item['summary_en']:
                f.write(f"Заголовок (EN): {item['summary_en']}\n")
            f.write(f"Цена продавца: {item['price']} ₽ (Цена покупателя: {item['buyer_price']:.2f} ₽)\n")
            f.write(f"Наличие (кол-во): {item['amount']}\n")
            if item['desc_ru']:
                f.write(f"Описание (RU):\n{item['desc_ru']}\n")
            if item['payment_msg_ru']:
                f.write(f"Текст после оплаты (RU):\n{item['payment_msg_ru']}\n")
            f.write("-" * 80 + "\n\n")

    print("\n" + Fore.GREEN + "=" * 60)
    print(Fore.GREEN + "[✓] ЭКСПОРТ УСПЕШНО ЗАВЕРШЕН!")
    print(Fore.GREEN + f"  📁 JSON-база для восстановления: {os.path.abspath(output_json)}")
    print(Fore.GREEN + f"  📄 Читаемый текстовый файл: {os.path.abspath(output_txt)}")
    print(Fore.GREEN + "=" * 60 + "\n")


if __name__ == "__main__":
    export_all_lots()
