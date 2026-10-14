"""
Модуль поддержания вечного статуса «Онлайн» (Auto-Online Keepalive) на FunPay.
Отправляет периодические heartbeat-сигналы через официальный FunPay runner в фоне,
благодаря чему лоты всегда видны покупателям с фильтром «Только онлайн».
"""

import json
import logging
import random
import threading
import time
from typing import Optional

from colorama import Fore, Style
from funpay_api import FunPayClient

logger = logging.getLogger("AutoOnline")


class AutoOnlineKeeper:
    def __init__(self, funpay: FunPayClient, enabled: bool = True):
        self.funpay = funpay
        self.enabled = enabled
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_ping_time: float = 0.0
        self.ping_count: int = 0

    def start(self):
        """
        Запуск фонового поддержания статуса Онлайн.

        Теперь модуль можно включать/выключать ЖИВЫМ тумблером в панели:
        при enabled=True поднимает поток, при False — останавливает.
        Раньше enabled читался только один раз при создании бота, поэтому
        тумблер «Вечный Онлайн» в панели не делал ничего до перезапуска.
        """
        if not self.enabled:
            self.stop()
            return

        # Поток мог ещё завершаться после предыдущего stop() — второй не поднимаем
        if self.thread is not None and self.thread.is_alive():
            if not self.running:
                self.running = True  # воскресить тот же поток
            return
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._keepalive_loop, daemon=True, name="AutoOnlineThread")
        self.thread.start()
        print(f"{Fore.GREEN}[✓] Модуль «Вечный Онлайн» активирован (статус аккаунта 24/7 на FunPay).{Style.RESET_ALL}")

    def stop(self):
        """
        Остановка фонового потока.

        Ждём реального завершения, иначе модуль продолжит слать heartbeat
        ещё до 75 секунд после того, как пользователь его выключил.
        """
        if not self.running and not (self.thread and self.thread.is_alive()):
            return
        self.running = False
        th = self.thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=5)
        if th is not None and not th.is_alive():
            self.thread = None

    def is_active(self) -> bool:
        """Фактическое состояние: поток жив И флаг включён."""
        return bool(self.enabled and self.running
                    and self.thread is not None and self.thread.is_alive())

    def send_heartbeat(self) -> bool:
        """Отправляет один heartbeat-запрос в FunPay runner"""
        if not self.funpay.csrf_token:
            # Получаем csrf_token из авторизации без повторной проверки полной
            auth_ok, _, _, _ = self.funpay.check_auth()
            if not auth_ok:
                logger.debug("Не удалось получить csrf_token для heartbeat")
                return False

        url = f"{self.funpay.BASE_URL}/runner/"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.funpay.BASE_URL}/",
            "Origin": self.funpay.BASE_URL,
        }

        # Эмуляция стандартных объектов отслеживания браузера
        objects_payload = []
        if self.funpay.user_id:
            objects_payload = [
                {"type": "chat_bookmarks", "id": str(self.funpay.user_id), "tag": "00000000"},
                {"type": "orders_counters", "id": str(self.funpay.user_id), "tag": "00000000"}
            ]

        data = {
            "objects": json.dumps(objects_payload),
            "request": "false",
            "csrf_token": self.funpay.csrf_token or "",
        }

        try:
            resp = self.funpay.session.post(url, data=data, headers=headers, timeout=12)
            if resp.status_code == 200:
                self.last_ping_time = time.time()
                self.ping_count += 1
                return True
            elif resp.status_code == 403:
                # Обновляем CSRF токен при истечении
                auth_ok, _, _, _ = self.funpay.check_auth()
                return False
            return False
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")
            return False

    def _keepalive_loop(self):
        """Фоновый цикл пинга FunPay (каждые 45-75 секунд с джиттером)"""
        while self.running:
            try:
                self.send_heartbeat()
            except Exception as e:
                logger.debug(f"Ошибка в цикле авто-онлайн: {e}")

            # Случайная пауза от 45 до 75 секунд (естественное поведение браузера)
            sleep_time = random.uniform(45.0, 75.0)
            
            # Дробный сон для быстрой остановки при завершении программы
            steps = int(sleep_time * 2)
            for _ in range(steps):
                if not self.running:
                    break
                time.sleep(0.5)
