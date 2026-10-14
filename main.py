"""
Главный исполняемый модуль FunPay Auto-Undercut Bot
"""

import json
import logging
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from colorama import Fore, Style, init

from funpay_api import FunPayClient, UserLot
from telegram_bot import TelegramNotifier
from lot_raiser import LotRaiser
from auto_online import AutoOnlineKeeper
from auto_orders import AutoOrdersWorker
from tunnel_manager import CloudflareTunnel
from web_ui import start_dashboard_background, PANEL_PASSWORD

init(autoreset=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Main")


class FunPayUndercutBot:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.running = True

        # Telegram
        tg_conf = self.config.get("telegram", {})
        self.telegram = TelegramNotifier(
            token=tg_conf.get("bot_token", ""),
            chat_id=tg_conf.get("chat_id", ""),
            enabled=tg_conf.get("enabled", False),
            proxy=tg_conf.get("proxy", ""),
        )

        # FunPay Client
        golden_key = self.config.get("golden_key", "")
        self.funpay = FunPayClient(golden_key=golden_key)

        # Поддержание вечного статуса «Онлайн» 24/7
        auto_online_enabled = bool(self.config.get("settings", {}).get("auto_online", True))
        self.auto_online = AutoOnlineKeeper(self.funpay, enabled=auto_online_enabled)

        # Онлайн-туннель для доступа с телефона вне дома
        self.tunnel = CloudflareTunnel(local_port=5000)

        # Автоподнятие предложений (лотов)
        auto_raise_enabled = bool(self.config.get("settings", {}).get("auto_raise_lots", True))
        self.raiser = LotRaiser(self.funpay, self.telegram, enabled=auto_raise_enabled)

        # Авто-заказы: приветствия, автоответы, выдача, отзывы
        self.orders_worker = AutoOrdersWorker(self.funpay, self.telegram,
                                              config_path=config_path)

        # Кеш категорий лотов для избежания повторных запросов
        self.lot_details_cache: Dict[str, Dict[str, Any]] = {}

        # Живое применение настроек из панели
        self._settings_watcher: Optional[threading.Thread] = None
        self._applied_state: Dict[str, Any] = {}

    # ---------- ЖИВЫЕ НАСТРОЙКИ ----------
    #
    # Раньше почти все тумблеры читались ОДИН раз при старте бота, поэтому
    # «Вечный онлайн», Telegram и «Авто-заказы» не реагировали на панель —
    # приходилось перезапускать бота. Теперь отдельный поток каждые ~2 сек
    # сверяет config.json с тем, что реально работает, и включает/выключает.

    def apply_live_settings(self) -> Dict[str, Any]:
        """Применяет текущие настройки к живым модулям. Возвращает состояние."""
        try:
            self.config = self.load_config()
        except Exception as e:
            logger.warning(f"Не удалось перечитать конфиг: {e}")
            return self._applied_state

        settings = self.config.get("settings", {}) or {}
        changes: List[str] = []

        # --- Вечный онлайн ---
        want_online = bool(settings.get("auto_online", True))
        was_online = getattr(self, "_applied_online", None)
        if was_online != want_online:
            self.auto_online.enabled = want_online
            if want_online:
                self.auto_online.start()
            else:
                self.auto_online.stop()
            changes.append("вечный онлайн " + ("ВКЛ" if want_online else "ВЫКЛ"))
            self._applied_online = want_online
            self.auto_online.enabled = want_online

        # --- Автоподнятие ---
        want_raise = bool(settings.get("auto_raise_lots", True))
        self.raiser.enabled = want_raise
        self._applied_raise = want_raise

        # --- Авто-ответчик (заказы + общие чаты) ---
        # Поток всегда жив; работают только включённые тумблеры сообщений
        # (приветствие, новый заказ, подтверждение, отзыв, ключевые слова).
        if not self.orders_worker.running:
            self.orders_worker.start()   # ленивый старт потока
        was_orders = getattr(self, "_applied_orders", None)
        now_orders = self.orders_worker.is_active()
        if was_orders != now_orders:
            changes.append("авто-ответчик " + ("ВКЛ" if now_orders else "ВЫКЛ"))
            self._applied_orders = now_orders

        # --- Telegram ---
        tg = self.config.get("telegram", {}) or {}
        want_tg = bool(tg.get("enabled", False) and tg.get("bot_token", "") and tg.get("chat_id", ""))
        if getattr(self, "_applied_tg", None) != want_tg:
            self.telegram.token = (tg.get("bot_token", "") or "").strip()
            self.telegram.chat_id = str(tg.get("chat_id", "") or "").strip()
            self.telegram.enabled = want_tg
            self.telegram.proxy = (tg.get("proxy", "") or "").strip() or None
            self.telegram.base_url = f"https://api.telegram.org/bot{self.telegram.token}"
            self.telegram.proxies = (
                {"http": self.telegram.proxy, "https": self.telegram.proxy}
                if self.telegram.proxy else None)
            self._applied_tg = want_tg
            changes.append("Telegram " + ("ВКЛ" if want_tg else "ВЫКЛ"))

        if changes:
            logger.info("Настройки применены: " + "; ".join(changes))
            print(f"{Fore.YELLOW}[*] Настройки применены: {', '.join(changes)}{Style.RESET_ALL}")

        self._applied_state = self.effective_state()
        return self._applied_state

    def effective_state(self) -> Dict[str, Any]:
        """ЧТО РЕАЛЬНО РАБОТАЕТ ПРЯМО СЕЙЧАС (а не что стоит в тумблерах)."""
        def _safe(fn, default=False):
            try:
                return bool(fn())
            except Exception:
                return default

        return {
            "auto_online": _safe(lambda: self.auto_online.is_active()),
            "auto_raise": _safe(lambda: self.raiser.enabled),
            "auto_orders": _safe(lambda: self.orders_worker.is_active()),
            "telegram": _safe(lambda: self.telegram.enabled),
            "price_edits": False,   # изменение цен заблокировано навсегда
        }

    def _settings_watcher_loop(self):
        """Следит за config.json и применяет тумблеры вживую (раз в 2 сек)."""
        while self.running:
            try:
                self.apply_live_settings()
            except Exception as e:
                logger.warning(f"Ошибка применения настроек: {e}")
            for _ in range(4):
                if not self.running:
                    break
                time.sleep(0.5)

    def start_settings_watcher(self):
        """Стартуем watcher и сразу применяем текущие настройки."""
        if self._settings_watcher is not None and self._settings_watcher.is_alive():
            return
        self.apply_live_settings()
        self._settings_watcher = threading.Thread(
            target=self._settings_watcher_loop, daemon=True, name="SettingsWatcher")
        self._settings_watcher.start()


    def load_config(self) -> Dict[str, Any]:
        """Загрузка конфигурации из файла + overlay из env (для хостинга)"""
        cfg: Dict[str, Any] = {}
        if not os.path.exists(self.config_path):
            # На хостинге config.json нет (он в .gitignore) — берём пример
            example = os.path.join(os.path.dirname(os.path.abspath(self.config_path)), "config.example.json")
            if os.path.exists(example):
                try:
                    with open(example, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception as e:
                    logger.error(f"Ошибка чтения {example}: {e}")
            else:
                print(Fore.RED + f"[✕] Ошибка: Файл конфигурации {self.config_path} не найден!")
                return {}
        else:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                logger.error(f"Ошибка чтения {self.config_path}: {e}")
                return {}
        # Overlay из переменных окружения (Bothost / Render / Docker)
        # GOLDEN_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED
        try:
            env_key = os.getenv("GOLDEN_KEY", "").strip()
            if env_key:
                cfg["golden_key"] = env_key
            tg = cfg.get("telegram", {}) or {}
            env_tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            if env_tok:
                tg["bot_token"] = env_tok
            env_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if env_chat:
                tg["chat_id"] = env_chat
            env_tg_on = os.getenv("TELEGRAM_ENABLED", "").strip().lower()
            if env_tg_on in ("1", "true", "yes", "on"):
                tg["enabled"] = True
            elif env_tg_on in ("0", "false", "no", "off"):
                tg["enabled"] = False
            cfg["telegram"] = tg
        except Exception:
            pass
        return cfg

    def print_banner(self):
        banner = f"""
{Fore.CYAN}+==================================================================+
|               {Fore.YELLOW}FUNPAY BOT v1.0 (без демпинга){Fore.CYAN}            |
|  {Fore.GREEN}Автоподнятие лотов · вечный онлайн · авто-заказы{Fore.CYAN}          |
|  {Fore.RED}Изменение цен ОТКЛЮЧЕНО — бот не трогает цены{Style.RESET_ALL}  |
+==================================================================+{Style.RESET_ALL}
"""
        print(banner)

    def setup_signals(self):
        """Перехват сигналов завершения (в потоке — тихо пропускаем)"""
        try:
            def signal_handler(sig, frame):
                print(f"\n{Fore.YELLOW}[*] Остановка бота по сигналу пользователя...")
                self.running = False
                try:
                    self.auto_online.stop()
                except Exception:
                    pass
                try:
                    self.orders_worker.stop()
                except Exception:
                    pass
                try:
                    self.tunnel.stop()
                except Exception:
                    pass
                try:
                    from web_ui import _bot_lock_release
                    _bot_lock_release()
                except Exception:
                    pass
                sys.exit(0)

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except ValueError:
            # запуск из фонового потока панели — сигналы недоступны, ок
            pass

    def run_check_cycle(self, user_lots: List[UserLot]) -> int:
        """
        Один рабочий цикл бота.

        ⛔ ИЗМЕНЕНИЕ ЦЕН ОТКЛЮЧЕНО. Бот не анализирует конкурентов
        и не трогает цены вообще — ни автоматически, ни по кнопке.

        Что осталось в цикле:
          • автоподнятие предложений (разделы lots + chips);
          • вечный онлайн (отдельный поток);
          • авто-заказы (отдельный поток).

        Возвращает 0 (обновлений цен больше не существует).
        """
        # Динамически перечитываем config.json (чтобы подтягивать изменения из Web Dashboard)
        self.config = self.load_config()
        # Живое обновление флага автоподнятия (без перезапуска бота)
        try:
            self.raiser.enabled = bool(self.config.get("settings", {}).get("auto_raise_lots", True))
        except Exception:
            pass

        updated_count = 0
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n{Fore.CYAN}─── [ {timestamp} ] Рабочий цикл ({len(user_lots)} лотов) ───{Style.RESET_ALL}")
        print(f"  {Fore.LIGHTBLACK_EX}⛔ Изменение цен отключено — цены ваших лотов бот не трогает.{Style.RESET_ALL}")

        # Проверка и автоподнятие ВСЕХ предложений (лотов) во всех разделах
        try:
            # Лоты, выключенные переключателем в панели, из автоподнятия
            # исключаются. Раньше этот переключатель управлял только демпингом
            # и после его отключения стал бессмысленным.
            disabled = set(str(x) for x in self.config.get("disabled_lots", []) or [])
            lots_to_raise = [l for l in (user_lots or [])
                             if str(getattr(l, "lot_id", "")) not in disabled]
            if disabled and len(lots_to_raise) != len(user_lots or []):
                print(f"  {Fore.YELLOW}[i] Исключено из автоподнятия (выключено в панели): "
                      f"{len(user_lots) - len(lots_to_raise)}{Style.RESET_ALL}")
            raise_stats = self.raiser.check_and_raise(lots_to_raise)
            if raise_stats.get("total"):
                logger.info(
                    f"Автоподнятие: разделов {raise_stats['total']}, "
                    f"поднято {raise_stats.get('raised', 0)}, "
                    f"кулдаун {raise_stats.get('cooldown', 0)}, "
                    f"ошибок {raise_stats.get('errors', 0)}"
                )
        except Exception as e:
            logger.warning(f"Ошибка автоподнятия: {e}")

        return updated_count

    def start(self, autostart: Optional[bool] = None):
        """
        Запуск основного цикла работы бота.

        autostart=None — решает сам: берёт settings.autostart из config.json.
        autostart=True  — принудительный старт (нажата кнопка «▶ Старт»).
        autostart=False — только панель, бот НЕ заходит в аккаунт FunPay.
        """
        self.print_banner()

        if autostart is None:
            autostart = bool(self.config.get("settings", {}).get("autostart", False))
        self.autostart = bool(autostart)

        try:
            from web_ui import _bot_lock_active, _bot_lock_acquire
            locked, pid = _bot_lock_active()
            if locked:
                print(f"{Fore.RED}[✕] Бот уже запущен в другом окне (PID {pid}). Второй запуск запрещён.")
                return
            if not _bot_lock_acquire():
                _, pid2 = _bot_lock_active()
                print(f"{Fore.RED}[✕] Бот уже запускается в другом окне (PID {pid2}). Второй запуск запрещён.")
                return
        except Exception:
            pass
        self.setup_signals()

        # Хостинг-режим: на Bothost/Render нет браузера и не нужен cloudflared.exe.
        # DISABLE_DASHBOARD=1 — без панели, DISABLE_TUNNEL=1 — без туннеля.
        hosting = os.getenv("HOSTING", "").strip() == "1" or os.getenv("BOTHOST", "").strip() == "1"
        disable_dashboard = os.getenv("DISABLE_DASHBOARD", "").strip() == "1"
        # Туннель публикует панель (с доступом к аккаунту FunPay) в интернет.
        # Теперь он включается ТОЛЬКО явно: ENABLE_TUNNEL=1. Раньше он
        # поднимался молча при каждом запуске на Windows.
        tunnel_env = os.getenv("ENABLE_TUNNEL", "").strip().lower()
        tunnel_wanted = tunnel_env in ("1", "true", "yes", "on")
        disable_tunnel = (not tunnel_wanted) or os.getenv("DISABLE_TUNNEL", "").strip() == "1" \
            or hosting or sys.platform != "win32"
        try:
            dash_port = int(os.getenv("PORT", "5000") or "5000")
        except Exception:
            dash_port = 5000

        # Запуск Web Dashboard в фоновом потоке (на хостинге — без открытия браузера)
        if not disable_dashboard:
            try:
                start_dashboard_background(port=dash_port, open_browser=(not hosting and sys.platform == "win32"))
            except Exception as e:
                logger.warning(f"Не удалось запустить Web Dashboard: {e}")

        # Синхронизируем туннель с реальным портом панели (если порт был занят)
        # На хостинге туннель отключён — пропускаем.
        if not disable_tunnel:
            try:
                import web_ui as _web_ui
                time.sleep(2)
                actual_port = int(getattr(_web_ui, "LAST_PORT", dash_port))
                if actual_port != dash_port:
                    self.tunnel.local_port = actual_port
                    print(f"{Fore.YELLOW}[*] Порт {dash_port} был занят, панель на порту {actual_port}.{Style.RESET_ALL}")
            except Exception:
                pass

        # Запуск Cloudflare Tunnel — только по явному ENABLE_TUNNEL=1
        public_url = None
        if disable_tunnel:
            if not tunnel_wanted:
                print(f"{Fore.CYAN}[*] Онлайн-доступ (туннель) выключен. Включается переменной ENABLE_TUNNEL=1.{Style.RESET_ALL}")
            else:
                print(f"{Fore.CYAN}[*] Хостинг-режим: туннель отключён.{Style.RESET_ALL}")
        else:
            try:
                if not PANEL_PASSWORD:
                    print(f"{Fore.RED}[!] ВНИМАНИЕ: туннель публикует панель в интернет, но PANEL_PASSWORD не задан —{Style.RESET_ALL}")
                    print(f"{Fore.RED}[!] внешние запросы будут отклоняться. Задайте PANEL_PASSWORD, чтобы пользоваться.{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[*] Запуск защищенного онлайн-доступа Cloudflare Tunnel...")
                public_url = self.tunnel.start(timeout=12)
                if public_url:
                    suffix = f"?password={PANEL_PASSWORD}" if PANEL_PASSWORD else ""
                    print(f"{Fore.GREEN}[✓] Онлайн-панель для телефона: {Fore.YELLOW}{public_url}{suffix}{Style.RESET_ALL}")
            except Exception as e:
                logger.warning(f"Не удалось запустить онлайн-туннель: {e}")

        # ⛔ Ручной режим: панель работает, но бот НЕ заходит в аккаунт FunPay.
        # Раньше start.bat сразу лез в аккаунт сохранённым ключом — поэтому бот
        # «сам заходил» в аккаунт, даже если его никто не запускал.
        if not self.autostart:
            print(f"\n{Fore.YELLOW}[*] Автозапуск выключен (settings.autostart = false).{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}[*] Бот НЕ подключается к FunPay и НЕ входит в аккаунт.{Style.RESET_ALL}")
            print(f"{Fore.CYAN}[*] Панель открыта — нажмите «▶ Старт», когда реально понадобится бот.{Style.RESET_ALL}")
            print(f"{Fore.CYAN}[*] «🚪 Выйти из аккаунта» — закрыть сессию FunPay и стереть ключ.{Style.RESET_ALL}\n")
            while self.running:
                try:
                    from web_ui import _stop_requested
                    if _stop_requested():
                        break
                except Exception:
                    pass
                time.sleep(1)
            self.shutdown()
            return

        # 1. Проверка авторизации
        print(f"{Fore.CYAN}[*] Проверка авторизации FunPay через golden_key...")
        auth_ok, user_id, username, err = self.funpay.check_auth()
        if not auth_ok or not user_id:
            print(f"{Fore.RED}[✕] Ошибка авторизации: {err}")
            print(f"{Fore.YELLOW}[!] Инструкция:")
            print("  1. Откройте сайт funpay.com в браузере и войдите в аккаунт.")
            print("  2. Нажмите F12 -> Вкладка 'Application' (или 'Хранилище') -> Cookies -> https://funpay.com")
            print("  3. Вставьте golden_key в панели: http://localhost:5000 -> Настройки -> Golden Key -> Проверить и сохранить,")
            print("     затем нажмите ▶ Старт в шапке панели или перезапустите start.bat.")
            self.telegram.notify_error("Ошибка авторизации FunPay", str(err))
            # ВАЖНО: не выходим — панель должна остаться доступной, чтобы вставить ключ через UI
            print(f"{Fore.CYAN}[*] Панель остаётся открытой. Вставьте ключ и нажмите Старт. Ctrl+C для выхода.{Style.RESET_ALL}")
            while self.running:
                time.sleep(1)
            self.shutdown()
            return

        print(f"{Fore.GREEN}[✓] Авторизован как: {Fore.WHITE}{username} (ID: {user_id})")

        # Живое применение тумблеров из панели.
        # Всё включение/выключение модулей теперь делает он — раз в 2 секунды
        # сверяет config.json с тем, что реально работает. Раньше модули
        # запускались один раз при старте, и тумблеры в панели были мёртвыми.
        self.start_settings_watcher()
        st = self.effective_state()
        print(f"{Fore.CYAN}[*] Модули: вечный онлайн={'ВКЛ' if st['auto_online'] else 'ВЫКЛ'}, "
              f"автоподнятие={'ВКЛ' if st['auto_raise'] else 'ВЫКЛ'}, "
              f"авто-ответчик={'ВКЛ' if st['auto_orders'] else 'ВЫКЛ'}.{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[*] Тумблеры в панели применяются сразу, перезапуск не нужен.{Style.RESET_ALL}\n")

        # 2. Получение списка активных лотов
        print(f"{Fore.CYAN}[*] Загрузка активных лотов пользователя...")
        user_lots = self.funpay.get_user_active_lots()
        if user_lots is None:
            # БЛОКИРОВКА FunPay, а не пустой профиль. Раньше это выглядело как
            # «лотов нет» — и бот продолжал работать вслепую.
            print(f"{Fore.RED}[!] FunPay вернул защитную страницу (CAPTCHA/лимит запросов).")
            print(f"{Fore.RED}[!] НЕ трогаем аккаунт и ждём 10–15 минут — иначе можно поймать бан.")
            while self.running:
                try:
                    from web_ui import _stop_requested
                    if _stop_requested():
                        break
                except Exception:
                    pass
                time.sleep(10)
            self.shutdown()
            return
        if not user_lots:
            print(f"{Fore.YELLOW}[!] Активных лотов в профиле не найдено. Убедитесь, что у вас созданы лоты на FunPay.")
        else:
            print(f"{Fore.GREEN}[✓] Найдено активных лотов: {len(user_lots)}")

        settings = self.config.get("settings", {})
        random_mode = bool(settings.get("random_interval_mode", True))
        fixed_interval = int(settings.get("check_interval_seconds", 120))
        min_interval = int(settings.get("check_interval_min_seconds", 120))
        max_interval = int(settings.get("check_interval_max_seconds", 300))

        interval_text = f"🎲 Плавающий от {min_interval // 60} до {max_interval // 60} мин" if random_mode else f"⏱ Каждые {fixed_interval} сек ({fixed_interval // 60} мин)"

        # Уведомление в Telegram о старте (с отправкой ссылки на панель)
        self.telegram.notify_startup(
            username=username or "User",
            user_id=user_id,
            lots_count=len(user_lots),
            interval_text=interval_text,
            dashboard_url=public_url,
        )

        # 3. Бесконечный рабочий цикл
        if random_mode:
            print(f"{Fore.GREEN}[✓] Бот переходит в фоновый режим. {Fore.YELLOW}Режим: Плавающий интервал {min_interval // 60}–{max_interval // 60} мин ({min_interval}–{max_interval} сек).{Style.RESET_ALL}\n")
        else:
            print(f"{Fore.GREEN}[✓] Бот переходит в фоновый режим. {Fore.CYAN}Режим: Фиксированный интервал {fixed_interval} сек ({fixed_interval // 60} мин).{Style.RESET_ALL}\n")

        cycle_num = 1
        while self.running:
            # Панель могла попросить остановиться, пока бот был в другом окне
            try:
                from web_ui import _stop_requested
                if _stop_requested():
                    print(f"\n{Fore.YELLOW}[*] Получена команда «Стоп» из панели — останавливаюсь.{Style.RESET_ALL}")
                    self.running = False
                    break
            except Exception:
                pass
            try:
                # Раз в 10 циклов обновляем список лотов (вдруг появились новые или продались)
                if cycle_num % 10 == 0:
                    print(f"{Fore.CYAN}[*] Периодическое обновление списка лотов из профиля...")
                    fresh_lots = self.funpay.get_user_active_lots()
                    if fresh_lots is None:
                        # Блокировка FunPay. Если подставить [] — бот решил бы,
                        # что лотов нет, и потерял бы их из виду навсегда.
                        print(f"{Fore.YELLOW}[!] Обновление пропущено: FunPay вернул защитную страницу.{Style.RESET_ALL}")
                        fresh_lots = []
                    if fresh_lots:
                        user_lots = fresh_lots

                self.run_check_cycle(user_lots)

                # Динамически обновляем параметры интервалов из config.json
                settings = self.config.get("settings", {})
                random_mode = bool(settings.get("random_interval_mode", True))
                fixed_interval = int(settings.get("check_interval_seconds", 120))
                min_interval = int(settings.get("check_interval_min_seconds", 120))
                max_interval = int(settings.get("check_interval_max_seconds", 300))

                # Расчет времени ожидания в зависимости от настроек
                if random_mode:
                    sleep_duration = random.randint(min_interval, max_interval)
                    mode_info = f"рандом: {sleep_duration // 60} мин {sleep_duration % 60} сек"
                else:
                    sleep_duration = fixed_interval
                    mode_info = f"фикс: {sleep_duration} сек"

                next_run_str = (datetime.now() + timedelta(seconds=sleep_duration)).strftime("%H:%M:%S")

                print(f"{Fore.LIGHTBLACK_EX}Цикл #{cycle_num} завершен. Проверено лотов: {len(user_lots)}. Следующий цикл в {next_run_str} ({mode_info})...{Style.RESET_ALL}")
                cycle_num += 1

                # Ожидание с проверкой флага running и стоп-файла из панели
                for _ in range(sleep_duration):
                    if not self.running:
                        break
                    try:
                        from web_ui import _stop_requested
                        if _stop_requested():
                            print(f"\n{Fore.YELLOW}[*] Получена команда «Стоп» из панели.{Style.RESET_ALL}")
                            self.running = False
                            break
                    except Exception:
                        pass
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Непредвиденная ошибка в основном цикле: {e}", exc_info=True)
                self.telegram.notify_error("Критическая ошибка цикла", str(e))
                time.sleep(30)

        # Цикл завершён (running = False) — гасим фоновые модули
        self.shutdown()

    def shutdown(self):
        """Аккуратное завершение: гасим онлайн-кикер, авто-заказы, туннель, лок."""
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        self.running = False
        print(f"\n{Fore.YELLOW}[*] Остановка бота: гасим фоновые модули...{Style.RESET_ALL}")
        # Наблюдатель настроек мог бы воскресить модули уже после stop() —
        # гасим его первым.
        w = self._settings_watcher
        if w is not None and w.is_alive():
            w.join(timeout=3)
        self._settings_watcher = None
        for name, obj in (("Вечный онлайн", self.auto_online),
                          ("Авто-заказы", self.orders_worker),
                          ("Туннель", self.tunnel)):
            try:
                obj.stop()
            except Exception:
                pass
        try:
            from web_ui import _bot_lock_release
            _bot_lock_release()
        except Exception:
            pass
        print(f"{Fore.GREEN}[✓] Бот остановлен. Сессия FunPay больше не используется.{Style.RESET_ALL}")


if __name__ == "__main__":
    bot = FunPayUndercutBot()
    bot.start()
