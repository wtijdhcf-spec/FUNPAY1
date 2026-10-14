"""
Модуль для отправки уведомлений в Telegram
"""

import html
import logging
import time
import requests
from typing import Optional, Dict

logger = logging.getLogger("TelegramNotifier")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, enabled: bool = True, proxy: Optional[str] = None):
        self.token = token.strip() if token else ""
        self.chat_id = str(chat_id).strip() if chat_id else ""
        self.enabled = bool(enabled and self.token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.proxy = proxy.strip() if proxy else None
        
        self.proxies: Optional[Dict[str, str]] = None
        if self.proxy:
            self.proxies = {
                "http": self.proxy,
                "https": self.proxy,
            }

    def send_message(self, text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = True) -> bool:
        """Отправка сообщения в Telegram чат с автоповторами при сбоях сети"""
        if not self.enabled:
            return False

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }

        # Механизм до 3 повторных попыток с задержкой при просадках сети
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, json=payload, timeout=15, proxies=self.proxies)
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(f"Ошибка отправки сообщения в Telegram (попытка {attempt}/3): {data.get('description')}")
                    return False
                return True
            except requests.exceptions.RequestException as e:
                if attempt < 3:
                    time.sleep(2.0)
                else:
                    logger.error(f"Не удалось отправить уведомление в Telegram после 3 попыток: {e}")
                    return False
            except Exception as e:
                logger.error(f"Непредвиденная ошибка при отправке в Telegram: {e}")
                return False

        return False

    def notify_startup(self, username: str, user_id: int, lots_count: int, interval_text: str, dashboard_url: Optional[str] = None):
        """Уведомление о запуске бота"""
        link_block = ""
        if dashboard_url:
            link_block = (
                f"\n🌐 <b>Панель управления (с любого устройства):</b>\n"
                f"👉 <a href=\"{dashboard_url}\">{dashboard_url}</a>\n"
            )

        msg = (
            f"🚀 <b>FunPay Undercut Bot Запущен!</b>\n\n"
            f"👤 <b>Аккаунт:</b> {html.escape(str(username))} (ID: <code>{user_id}</code>)\n"
            f"📦 <b>Активных лотов найдено:</b> {lots_count}\n"
            f"⏱ <b>Интервал проверки:</b> {html.escape(interval_text)}\n"
            f"{link_block}\n"
            f"⚡ <i>Бот начал анализ разделов и мониторинг конкурентов.</i>"
        )
        self.send_message(msg)

    def notify_error(self, error_title: str, error_details: str):
        """Уведомление об ошибке в работе"""
        msg = (
            f"⚠️ <b>Внимание: Ошибка FunPay Бота!</b>\n\n"
            f"<b>{html.escape(error_title)}</b>\n"
            f"<code>{html.escape(str(error_details))}</code>\n\n"
            f"<i>Проверьте логи или обновите golden_key при необходимости.</i>"
        )
        self.send_message(msg)

