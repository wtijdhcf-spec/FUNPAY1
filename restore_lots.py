"""
Скрипт для автоматического восстановления/импорта лотов из lots_backup.json
на новый (или текущий) аккаунт FunPay.
"""

import json
import os
import sys
import time
from bs4 import BeautifulSoup
from colorama import Fore, init

init(autoreset=True)

from funpay_api import FunPayClient


def restore_lots_to_account(backup_json_path: str = "lots_backup.json", config_path: str = "config.json", dry_run: bool = False):
    if not os.path.exists(backup_json_path):
        print(Fore.RED + f"[✕] Файл резервной копии {backup_json_path} не найден! Сначала запустите backup_lots.py.")
        return

    with open(backup_json_path, "r", encoding="utf-8") as f:
        lots_data = json.load(f)

    if not lots_data:
        print(Fore.RED + "[✕] В файле резервной копии нет лотов.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    golden_key = config.get("golden_key")
    if not golden_key:
        print(Fore.RED + "[✕] golden_key не указан в config.json!")
        return

    client = FunPayClient(golden_key=golden_key)
    
    print(Fore.CYAN + "[*] Проверка авторизации целевого аккаунта...")
    auth_ok, user_id, username, err = client.check_auth()
    if not auth_ok:
        print(Fore.RED + f"[✕] Ошибка авторизации: {err}")
        return

    print(Fore.GREEN + f"[✓] Авторизован целевой аккаунт: {username} (ID: {user_id})")
    print(Fore.CYAN + f"[*] Найдено в резервной копии: {len(lots_data)} лотов.")

    if dry_run:
        print(Fore.YELLOW + "\n[!] РЕЖИМ ТЕСТОВОГО ПРОСМОТРА (dry_run=True, лоты не будут отправлены на сайт):")
        for i, item in enumerate(lots_data, 1):
            print(f"  {i}. [Раздел #{item['node_id']}] {item['summary_ru']} | Цена: {item['price']} ₽ | Кол-во: {item['amount']}")
        return

    print(Fore.YELLOW + f"\n[!] Начинаем создание {len(lots_data)} лотов на аккаунте {username}...")
    
    created_count = 0
    errors_count = 0

    for idx, item in enumerate(lots_data, 1):
        node_id = item.get("node_id", "402")
        title = item.get("summary_ru", "")
        print(f"\n[{idx}/{len(lots_data)}] Создание лота в разделе #{node_id}: {Fore.WHITE}{title[:40]}...")

        # 1. Открываем страницу создания лота для получения fresh CSRF и формы
        create_page_url = f"{client.BASE_URL}/lots/offerEdit?node={node_id}"
        time.sleep(2.0)  # Естественная пауза между созданием лотов

        try:
            resp = client.session.get(create_page_url, timeout=15)
            if resp.status_code != 200:
                print(Fore.RED + f"  ✗ Не удалось открыть страницу создания: код {resp.status_code}")
                errors_count += 1
                continue

            client._extract_csrf(resp.text)
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form", class_="form-offer-editor") or soup.find("form", action=lambda x: x and "offerSave" in x)
            if not form:
                print(Fore.RED + "  ✗ Не найдена форма на странице создания лота")
                errors_count += 1
                continue

            # Копируем сохраненные поля лота
            form_payload = dict(item.get("form_fields", {}))
            
            # При создании нового лота id offer должен быть пустым
            form_payload["offer"] = ""
            form_payload["node_id"] = node_id
            form_payload["deleted"] = ""
            form_payload["active"] = "on"
            
            if client.csrf_token:
                form_payload["csrf_token"] = client.csrf_token

            headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": create_page_url,
                "Origin": client.BASE_URL,
            }

            save_resp = client.session.post(f"{client.BASE_URL}/lots/offerSave", data=form_payload, headers=headers, timeout=15)

            # БАГ БЫЛ: `res_json.get("done") or not res_json.get("error")` —
            # FunPay при ошибке отвечает {"done": false} без поля error,
            # поэтому `not None` == True и операция рапортовалась УСПЕШНОЙ,
            # хотя лот не создан. Используем общий строгий разбор.
            ok, msg = client._interpret_save_response(save_resp, "лот создан")
            if ok:
                print(Fore.GREEN + "  ✓ Лот успешно создан на новом аккаунте!")
                created_count += 1
            else:
                print(Fore.RED + f"  ✗ Ошибка создания: {msg}")
                errors_count += 1

        except Exception as e:
            print(Fore.RED + f"  ✗ Исключение: {e}")
            errors_count += 1

    print("\n" + Fore.GREEN + "=" * 60)
    print(Fore.GREEN + "[✓] ВОССТАНОВЛЕНИЕ ЗАВЕРШЕНО!")
    print(Fore.GREEN + f"  Успешно создано: {created_count}")
    if errors_count > 0:
        print(Fore.YELLOW + f"  Ошибок/пропущено: {errors_count}")
    print(Fore.GREEN + "=" * 60 + "\n")


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    restore_lots_to_account(dry_run=is_dry)
