"""
Модуль защиты от байтов/ловушек демпинга конкурентов (Anti-Bait Protection)
Если конкурент меняет цену второй раз в течение 5 минут,
лот замораживается на случайное время от 5 до 12 минут, и отправляется алерт в Telegram.
"""

import datetime
import io
import logging
import random
import sys
import time
from typing import Any, Dict, Optional, Tuple

from colorama import Fore, Style
from telegram_bot import TelegramNotifier

logger = logging.getLogger("AntiBaitGuard")


class AntiBaitGuard:
    def __init__(self, telegram: TelegramNotifier, config: Dict[str, Any]):
        self.telegram = telegram
        self.config = config
        
        # Состояние по лотам пользователя:
        # lot_id -> {
        #   "comp_name": str,
        #   "comp_price": float,
        #   "first_change_time": float,
        #   "change_count": int,
        #   "frozen_until": float,
        # }
        self.lot_states: Dict[str, Dict[str, Any]] = {}

    def is_lot_frozen(self, lot_id: str) -> Tuple[bool, int, str]:
        """
        Проверяет, заморожен ли лот в данный момент.
        Возвращает: (is_frozen, remaining_seconds, unlock_time_str)
        """
        state = self.lot_states.get(str(lot_id))
        if not state:
            return False, 0, ""

        frozen_until = state.get("frozen_until", 0.0)
        now = time.time()
        if now < frozen_until:
            rem_sec = int(frozen_until - now)
            unlock_time = datetime.datetime.fromtimestamp(frozen_until).strftime("%H:%M:%S")
            return True, rem_sec, unlock_time

        return False, 0, ""

    def check_competitor_change(
        self,
        lot_id: str,
        lot_title: str,
        category_name: str,
        competitor_name: str,
        current_comp_price: float,
    ) -> Tuple[bool, str]:
        """
        Анализирует поведение конкурента.
        Если конкурент меняет цену 2-й раз за 5 минут — активирует заморозку на 5-12 мин
        и шлет алерт в Telegram.
        
        Возвращает:
            (is_triggered: bool, reason_message: str)
        """
        settings = self.config.get("settings", {}).get("anti_bait_protection", {})
        if settings.get("enabled", True) is False:
            return False, ""

        window_sec = int(settings.get("trigger_window_seconds", 300))  # 5 минут (300 сек)
        min_freeze_min = int(settings.get("freeze_min_minutes", 5))    # 5 минут
        max_freeze_min = int(settings.get("freeze_max_minutes", 12))   # 12 минут

        now = time.time()
        lot_key = str(lot_id)

        # 1. Проверяем, не заморожен ли лот прямо сейчас
        is_frozen, rem_sec, unlock_time = self.is_lot_frozen(lot_key)
        if is_frozen:
            mins = rem_sec // 60
            secs = rem_sec % 60
            return True, f"Защита от байта активна: лот заморожен еще на {mins} мин {secs} сек (до {unlock_time})"

        # 2. Получаем историю по лоту
        state = self.lot_states.get(lot_key)
        if not state:
            # Первая фиксация конкурента
            self.lot_states[lot_key] = {
                "comp_name": competitor_name,
                "comp_price": current_comp_price,
                "first_change_time": now,
                "change_count": 0,
                "frozen_until": 0.0,
            }
            return False, ""

        last_name = state.get("comp_name", "")
        last_price = state.get("comp_price", 0.0)
        first_time = state.get("first_change_time", now)
        change_count = state.get("change_count", 0)

        # Если тот же конкурент изменил цену
        if last_name == competitor_name and abs(current_comp_price - last_price) > 0.0001:
            time_diff = now - first_time

            # Если изменение произошло в пределах окна 5 минут
            if time_diff <= window_sec:
                change_count += 1
                state["change_count"] = change_count
                state["comp_price"] = current_comp_price

                if change_count >= 2:
                    # СРАБОТАЛА ЗАЩИТА: 2-е изменение за 5 минут!
                    freeze_seconds = random.randint(min_freeze_min * 60, max_freeze_min * 60)
                    frozen_until = now + freeze_seconds
                    state["frozen_until"] = frozen_until
                    state["change_count"] = 0  # сбрасываем счетчик после срабатывания

                    freeze_mins = freeze_seconds // 60
                    freeze_secs = freeze_seconds % 60
                    unlock_time_str = datetime.datetime.fromtimestamp(frozen_until).strftime("%H:%M:%S")

                    print(f"\n{Fore.RED}[!] [ВНИМАНИЕ] Сработала защита от байта конкурента для лота #{lot_id}!")
                    print(f"  | Конкурент: {Fore.YELLOW}{competitor_name}{Fore.RED} изменил цену 2-й раз за 5 минут ({last_price:.3f} руб. -> {current_comp_price:.3f} руб.)")
                    print(f"  | Авторегулировка цены лота заморожена на {freeze_mins} мин {freeze_secs} сек (до {unlock_time_str}){Style.RESET_ALL}\n")

                    # ОБЯЗАТЕЛЬНОЕ УВЕДОМЛЕНИЕ В TELEGRAM
                    if self.telegram and self.telegram.enabled:
                        tg_msg = (
                            f"🚨 <b>ВНИМАНИЕ: Сработала защита от байта!</b>\n\n"
                            f"🏷 <b>Лот:</b> {lot_title} (ID: <code>{lot_id}</code>)\n"
                            f"📁 <b>Раздел:</b> {category_name}\n"
                            f"👤 <b>Конкурент:</b> <b>{competitor_name}</b>\n\n"
                            f"⚠️ <i>Конкурент изменил цену 2-й раз за 5 минут:</i>\n"
                            f"<s>{last_price:.3f} ₽</s> ➔ <b>{current_comp_price:.3f} ₽</b>\n\n"
                            f"⏳ <b>Авторегулировка этого лота отключена на {freeze_mins} мин {freeze_secs} сек.</b>\n"
                            f"🔓 Разморозка в: <b>{unlock_time_str}</b>"
                        )
                        self.telegram.send_message(tg_msg)

                    return True, f"Сработала защита от байта! Авторегулировка заморожена на {freeze_mins} мин (до {unlock_time_str})"
            else:
                # Прошло больше 5 минут — начинаем новое окно наблюдения
                state["first_change_time"] = now
                state["change_count"] = 1
                state["comp_price"] = current_comp_price
        else:
            # Сменился конкурент или цена не менялась
            state["comp_name"] = competitor_name
            state["comp_price"] = current_comp_price
            if last_name != competitor_name:
                state["first_change_time"] = now
                state["change_count"] = 0

        return False, ""
