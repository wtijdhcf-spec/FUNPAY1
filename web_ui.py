"""
Локальный Web Dashboard для FunPay Auto-Undercut Bot (localhost:5000)
Полнофункциональный веб-интерфейс для управления настройками,
включения/отключения отдельных лотов, мониторинга и тестирования.
"""

import datetime
import http.server
import json
import secrets
import logging
import os
import random
import socket
import socketserver
import threading
import time
import urllib.parse
import webbrowser
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup
from colorama import Fore, Style, init
from funpay_api import FunPayClient
from telegram_bot import TelegramNotifier

init(autoreset=True)

logger = logging.getLogger("WebDashboard")

PORT = 5000

# Популярные игры/категории на FunPay (можно расширить)
FUNPAY_GAMES = [
    {"id": "robux", "name": "Roblox", "node": "robux"},
    {"id": "minecraft", "name": "Minecraft", "node": "minecraft"},
    {"id": "fortnite", "name": "Fortnite", "node": "fortnite"},
    {"id": "steam", "name": "Steam", "node": "steam"},
    {"id": "valorant", "name": "Valorant", "node": "valorant"},
    {"id": "genshin", "name": "Genshin Impact", "node": "genshin"},
    {"id": "brawl_stars", "name": "Brawl Stars", "node": "brawl_stars"},
    {"id": "pubg", "name": "PUBG Mobile", "node": "pubg_mobile"},
    {"id": "freefire", "name": "Free Fire", "node": "freefire"},
    {"id": "csgo", "name": "CS2 / CS:GO", "node": "cs2"},
    {"id": "lol", "name": "League of Legends", "node": "lol"},
    {"id": "dota2", "name": "Dota 2", "node": "dota2"},
    {"id": "warzone", "name": "Call of Duty: Warzone", "node": "warzone"},
    {"id": "apex", "name": "Apex Legends", "node": "apex"},
    {"id": "rust", "name": "Rust", "node": "rust"},
    {"id": "origin", "name": "Origin / EA", "node": "origin"},
    {"id": "uplay", "name": "Ubisoft / Uplay", "node": "uplay"},
    {"id": "epic", "name": "Epic Games", "node": "epic"},
    {"id": "playstation", "name": "PlayStation", "node": "playstation"},
    {"id": "xbox", "name": "Xbox", "node": "xbox"},
    {"id": "nitro", "name": "Discord Nitro", "node": "discord_nitro"},
    {"id": "spotify", "name": "Spotify", "node": "spotify"},
    {"id": "tiktok", "name": "TikTok", "node": "tiktok"},
    {"id": "vk", "name": "VK", "node": "vk"},
    {"id": "other", "name": "Другое", "node": "other"},
]


def _get_categories(self, client: FunPayClient) -> List[Dict]:
    """Получает список категорий с FunPay"""
    categories = []
    try:
        # Получаем главную страницу и парсим игры
        resp = client.session.get(client.BASE_URL + "/lots/", timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Ищем ссылки на игры в навигации
            game_links = soup.find_all("a", href=re.compile(r"/lots/\d+"))
            seen_nodes = set()
            for link in game_links[:30]:  # Ограничиваем 30 категориями
                href = link.get("href", "")
                match = re.search(r"/lots/(\d+)", href)
                if match:
                    node_id = match.group(1)
                    if node_id not in seen_nodes:
                        seen_nodes.add(node_id)
                        name = link.get_text(strip=True) or link.get("title", "")
                        if name and len(name) < 50:
                            categories.append({
                                "node_id": node_id,
                                "name": name,
                                "url": f"{client.BASE_URL}/lots/{node_id}/"
                            })
    except Exception as e:
        logger.warning(f"Ошибка получения категорий с FunPay: {e}")

    # Если не удалось получить, возвращаем популярные игры
    if not categories:
        categories = [
            {"node_id": "402", "name": "Roblox", "url": "https://funpay.com/lots/402/"},
            {"node_id": "459", "name": "Minecraft", "url": "https://funpay.com/lots/459/"},
            {"node_id": "491", "name": "Steam", "url": "https://funpay.com/lots/491/"},
            {"node_id": "526", "name": "Genshin Impact", "url": "https://funpay.com/lots/526/"},
            {"node_id": "503", "name": "Valorant", "url": "https://funpay.com/lots/503/"},
            {"node_id": "big_categories", "name": "Прочее (другие игры)", "url": "https://funpay.com/lots/"},
        ]

    return categories


def _get_category_offers(self, client: FunPayClient, node_id: str) -> List[Dict]:
    """Получает офферы категории"""
    offers = []
    try:
        # Пробуем разные форматы URL
        url = f"{client.BASE_URL}/lots/{node_id}/"
        resp = client.session.get(url, timeout=15)
        if resp.status_code != 200:
            # Пробуем с chips
            url = f"{client.BASE_URL}/chips/{node_id}/"
            resp = client.session.get(url, timeout=15)

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("a", class_=re.compile(r"tc-item"))

            for item in items[:50]:  # Ограничиваем 50 офферами для производительности
                href = item.get("href", "")
                offer_id_match = re.search(r"[?&]id=(\d+)", href)
                if not offer_id_match:
                    continue
                offer_id = offer_id_match.group(1)

                # Проверяем, не наш ли это оффер
                user_block = item.find(class_=re.compile(r"media-user"))
                if user_block:
                    data_user = user_block.get("data-user")
                    if data_user and str(data_user) == str(client.user_id):
                        continue  # Пропускаем свои офферы

                # Название
                desc_el = item.find(class_=re.compile(r"tc-desc-text|tc-desc"))
                title = desc_el.get_text(strip=True) if desc_el else item.get_text(strip=True)[:100]

                if not title:
                    continue

                # Цена
                price_el = item.find(class_=re.compile(r"tc-price"))
                price = 0.0
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    price = client.parse_price(price_text)

                # Автовыдача / автоответ
                server_el = item.find(class_=re.compile(r"tc-server"))
                server = server_el.get_text(strip=True) if server_el else ""

                side_el = item.find(class_=re.compile(r"tc-side"))
                side = side_el.get_text(strip=True) if side_el else ""

                sub_el = item.find(class_=re.compile(r"tc-subcategory"))
                subcategory = sub_el.get_text(strip=True) if sub_el else ""

                offers.append({
                    "offer_id": offer_id,
                    "title": title,
                    "price": price,
                    "server": server,
                    "side": side,
                    "subcategory": subcategory,
                })

    except Exception as e:
        logger.warning(f"Ошибка получения офферов категории {node_id}: {e}")

    return offers


def _create_lots_from_selection(self, req_data: Dict, config: Dict) -> Dict:
    """Создает лоты из выбранных офферов"""
    selected = req_data.get("selected_offers", [])
    prompt_auto = req_data.get("prompt_auto", "")
    prompt_response = req_data.get("prompt_response", "")
    category_name = req_data.get("category_name", "")
    node_id = req_data.get("node_id", "")

    if not selected:
        return {"ok": False, "error": "Не выбраны товары для создания лотов"}

    # Создаем клиента FunPay
    golden_key = config.get("golden_key", "")
    client = FunPayClient(golden_key=golden_key)
    auth_ok, user_id, username, err = client.check_auth()

    if not auth_ok:
        return {"ok": False, "error": f"Ошибка авторизации: {err}"}

    created_count = 0
    errors = []

    # Получаем цены по умолчанию для расчета
    default_prices = config.get("default_seller_prices", {})
    match_parameters = config.get("settings", {}).get("match_parameters", {})

    # Ищем совпадения в названиях для определения базовой цены
    def find_price_for_item(title: str) -> float:
        title_lower = title.lower()
        for key, price in default_prices.items():
            key_lower = key.lower()
            if key_lower in title_lower or title_lower in key_lower:
                return price
        # Если не нашли, возвращаем базовую цену
        return 0.15

    for offer in selected:
        title = offer.get("title", "")
        offer_price = offer.get("price", 0.0)

        # Извлекаем параметры из названия
        server = ""
        side = ""
        subcategory = ""

        # Пытаемся вытащить сервер, сторону и т.д. из названия
        # Формат на FunPay: "Lineage Piece, ⚙️ Одд Том / Odd Tom | Быстро • Дешево • Безопасно ⚙️"
        # server_el = item.find(class_=re.compile(r"tc-server"))
        # side_el = item.find(class_=re.compile(r"tc-side"))
        # sub_el = item.find(class_=re.compile(r"tc-subcategory"))

        # Если есть данные о сервере/стороне из оффера
        if offer.get("server"):
            server = offer.get("server", "")
        if offer.get("side"):
            side = offer.get("side", "")
        if offer.get("subcategory"):
            subcategory = offer.get("subcategory", "")

        # Вычисляем цену продавца (чистыми)
        # FunPay берет комиссию ~22%,所以: seller_price = buyer_price / 1.2236
        # Создаем лот с ценой покупателя = offer_price (занижаем на 0.01)
        buyer_price = max(0.01, offer_price - 0.01)

        # Расчет чистой цены продавца
        commission_ratio = 1.223577
        seller_price = buyer_price / commission_ratio

        # Формируем описания
        # Русское описание
        summary_ru = f"{title}"
        if server or side:
            summary_ru += f"\n\nСервер: {server if server else 'Любой'}"
            summary_ru += f"\nСторона: {side if side else 'Любая'}"
        if subcategory:
            summary_ru += f"\nКатегория: {subcategory}"

        # Английское описание (упрощенное)
        summary_en = title
        if server or side:
            summary_en += f"\nServer: {server if server else 'Any'}"
            summary_en += f"\nSide: {side if side else 'Any'}"
        if subcategory:
            summary_en += f"\nCategory: {subcategory}"

        # Создаем лот
        success, msg = client.create_lot(
            node_id=node_id,
            title=title,
            seller_price=seller_price,
            summary_ru=summary_ru,
            summary_en=summary_en,
            server=server,
            side=side,
            subcategory=subcategory,
            prompt_auto=prompt_auto,
            prompt_response=prompt_response,
            amount=1,
        )

        if success:
            created_count += 1
            logger.info(f"✓ Создан лот: {title[:50]}... цена: {seller_price:.3f}₽")
        else:
            errors.append(f"{title[:50]}: {msg}".replace("§SKIP§", ""))
            logger.warning(f"✗ Не удалось создать лот {title[:50]}: {msg}")

    # Формируем результат
    if created_count > 0:
        msg = f"Успешно создано {created_count} лотов"
        if errors:
            msg += f". Ошибки: {', '.join(errors[:3])}"
        return {"ok": True, "created": created_count, "message": msg}
    else:
        return {"ok": False, "error": "; ".join(errors) if errors else "Не удалось создать ни одного лота"}


CONFIG_FILE = "config.json"
BACKUP_FILE = "lots_backup.json"

# --- Bot process controller (Start/Stop from web UI) ---
_BOT_THREAD = None
_BOT_RUNNING = False
_BOT_ERROR = ""

# --- Фоновая задача массового выставления (с живым прогрессом) ---
import threading as _threading
_BULK_LOCK = _threading.Lock()
_BULK_TASK: Dict[str, Any] = {
    "running": False, "total": 0, "done": 0, "created": 0,
    "skipped": 0, "errors": [], "message": "", "finished": True,
    "current": "", "stop": False,
}
# --- Ручное поднятие ВСЕХ разделов (кнопка «Поднять все сейчас») ---
_RAISE_TASK: Dict[str, Any] = {
    "running": False, "total": 0, "raised": 0, "cooldown": 0,
    "errors": 0, "message": "", "finished": True, "current": "",
    "details": [],
}
RAISE_STATE_FILE = "raise_state.json"


def _read_raise_state() -> Dict[str, Any]:
    """Состояние автоподнятия для панели: по каждому разделу когда следующий подъём."""
    nodes: List[Dict[str, Any]] = []
    try:
        if os.path.exists(RAISE_STATE_FILE):
            with open(RAISE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            nxt = data.get("next_raise", {}) or {}
            raised = set(str(x) for x in (data.get("raised", []) or []))
            last = data.get("last_results", {}) or {}
            now = time.time()
            for key in sorted(set(list(nxt.keys()) + list(raised))):
                t = 0.0
                try:
                    t = float(nxt.get(key, 0) or 0)
                except Exception:
                    t = 0.0
                left = max(0, int(t - now)) if t > now else 0
                nodes.append({
                    "node": key,
                    "raised": key in raised,
                    "next_in_sec": left,
                    "next_at": (datetime.datetime.now() + datetime.timedelta(seconds=left)).strftime("%H:%M:%S") if left else "готово",
                    "last": last.get(key, {}),
                })
    except Exception as e:
        logger.warning(f"Не удалось прочитать {RAISE_STATE_FILE}: {e}")
    return {"ok": True, "nodes": nodes, "count": len(nodes)}


def _run_raise_all_task(config: Dict[str, Any]):
    """Фон: поднять ВСЕ разделы пользователя (force — без локальных кулдаунов)."""
    global _RAISE_TASK
    try:
        with _BULK_LOCK:
            _RAISE_TASK.update({"running": True, "finished": False, "message": "Загружаю лоты...",
                                "total": 0, "raised": 0, "cooldown": 0, "errors": 0,
                                "current": "", "details": []})
        client = FunPayClient(golden_key=config.get("golden_key", ""))
        auth_ok, uid, _, err = client.check_auth()
        if not auth_ok:
            with _BULK_LOCK:
                _RAISE_TASK.update({"running": False, "finished": True,
                                    "message": f"Ошибка авторизации: {err}"})
            return
        lots = client.get_user_active_lots()
        if not lots:
            with _BULK_LOCK:
                _RAISE_TASK.update({"running": False, "finished": True,
                                    "message": "Активных лотов не найдено"})
            return
        # Локальный импорт — чтобы не тащить LotRaiser в верхний уровень
        from lot_raiser import LotRaiser
        tg_conf = (config.get("telegram", {}) or {})
        tg = TelegramNotifier(token=tg_conf.get("bot_token", ""),
                              chat_id=tg_conf.get("chat_id", ""),
                              enabled=tg_conf.get("enabled", False),
                              proxy=tg_conf.get("proxy", ""))
        raiser = LotRaiser(client, tg, enabled=True)
        nodes = raiser.extract_node_ids(lots)
        with _BULK_LOCK:
            _RAISE_TASK.update({"total": len(nodes), "message": f"Разделов: {len(nodes)}"})

        # Поднимаем через тот же LotRaiser (он сам сохраняет raise_state.json)
        stats = {"raised": 0, "cooldown": 0, "errors": 0, "details": []}
        try:
            stats = raiser.check_and_raise(lots, force=True)
        except Exception as e:
            stats = {"raised": 0, "cooldown": 0, "errors": 1,
                     "details": [{"status": "error", "msg": str(e)}]}
        with _BULK_LOCK:
            _RAISE_TASK.update({
                "running": False, "finished": True,
                "raised": int(stats.get("raised", 0)),
                "cooldown": int(stats.get("cooldown", 0)),
                "errors": int(stats.get("errors", 0)),
                "details": list(stats.get("details", []))[:50],
                "message": (f"Готово: поднято {stats.get('raised', 0)}/{stats.get('total', len(nodes))}, "
                            f"кулдаун {stats.get('cooldown', 0)}"),
            })
    except Exception as e:
        logger.warning(f"Ошибка фонового поднятия: {e}")
        with _BULK_LOCK:
            _RAISE_TASK.update({"running": False, "finished": True,
                                "message": f"Ошибка: {e}"})
BULK_HISTORY_FILE = "bulk_created.json"
DEACTIVATED_FILE = "deactivated.json"


def _registry_load() -> List[Dict]:
    try:
        if os.path.exists(DEACTIVATED_FILE):
            with open(DEACTIVATED_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or []
    except Exception:
        pass
    return []


def _registry_add(entries: List[Dict]):
    try:
        reg = _registry_load()
        have = set((str(r.get("lot_id", "")), str(r.get("node", ""))) for r in reg)
        for e in entries:
            key = (str(e.get("lot_id", "")), str(e.get("node", "")))
            if key[0] and key not in have:
                have.add(key)
                reg.append(e)
        with open(DEACTIVATED_FILE, "w", encoding="utf-8") as f:
            json.dump(reg[-1000:], f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Не удалось записать реестр: {e}")


def _log_deactivated_ids() -> List[str]:
    """ID из bot.log (старые чистки без реестра)."""
    ids: List[str] = []
    seen: Set[str] = set()
    if os.path.exists("bot.log"):
        try:
            with open("bot.log", "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.search(r"\[DUPES\] #(\d+): снят с продажи", line)
                    if m and m.group(1) not in seen:
                        seen.add(m.group(1))
                        ids.append(m.group(1))
        except Exception:
            pass
    return ids


def _collect_purge_nodes() -> List[str]:
    """Ноды для живого сканирования trade-страниц (новейшие — первыми)."""
    nodes: List[str] = []
    seen: Set[str] = set()

    def _push(nid: str):
        nid = str(nid or "").strip()
        if nid and nid.isdigit() and nid not in seen:
            seen.add(nid)
            nodes.append(nid)

    # 1) история массовых выставлений — главный источник разделов
    try:
        if os.path.exists(BULK_HISTORY_FILE):
            with open(BULK_HISTORY_FILE, "r", encoding="utf-8") as f:
                hist = json.load(f) or []
            for h in reversed(hist):  # новейшие первыми
                _push(h.get("node", ""))
    except Exception:
        pass
    # 2) реестр выключенных (там лежит node)
    try:
        for r in _registry_load():
            _push(r.get("node", ""))
    except Exception:
        pass
    return nodes


def _purge_inactive_core(client, stop_check=None, progress_cb=None,
                         max_n: int = 40) -> Dict[str, Any]:
    """Сносит выключенные лоты: СНАЧАЛА живые trade-страницы, потом реестр + bot.log.
    Активные не трогает. Возвращает статистику. Безвозвратно!"""
    cands: List[str] = []
    seen: Set[str] = set()
    live_count = 0

    # --- 0) ЖИВОЕ сканирование: trade-страницы известных разделов ---
    # Это чинит главный баг: реестр пуст / лог протух, а 2 выключенных
    # реально висят на FunPay (розовые ряды, класс warning) и жрут лимит.
    try:
        nodes = _collect_purge_nodes()
    except Exception:
        nodes = []
    for ni, node in enumerate(nodes):
        if stop_check and stop_check():
            break
        if len(cands) >= max_n:
            break
        if progress_cb:
            try:
                progress_cb(done=0, current=f"скан раздела {node} ({ni + 1}/{len(nodes)})")
            except Exception:
                pass
        offers = []
        try:
            if hasattr(client, "get_my_trade_offers"):
                offers = client.get_my_trade_offers(node, kind="lots") or []
                if not offers:
                    offers = client.get_my_trade_offers(node, kind="chips") or []
        except Exception:
            offers = []
        for o in offers or []:
            try:
                if not o.get("disabled"):
                    continue
                lid = str(o.get("offer_id", ""))
                if lid and lid not in seen:
                    seen.add(lid)
                    cands.append(lid)
                    if len(cands) >= max_n:
                        break
            except Exception:
                continue
    live_count = len(cands)

    # --- 1) реестр + лог — только как довесок (не вытесняют живые!) ---
    reg_ids: List[str] = []
    try:
        for r in _registry_load():
            lid = str(r.get("lot_id", ""))
            if lid and lid not in seen:
                seen.add(lid)
                reg_ids.append(lid)
    except Exception:
        pass
    reg_count = len(reg_ids)
    log_ids: List[str] = []
    try:
        for lid in _log_deactivated_ids():
            lid = str(lid)
            if lid and lid not in seen:
                seen.add(lid)
                log_ids.append(lid)
    except Exception:
        pass
    log_count = len(log_ids)
    # живые — первыми, затем реестр/лог, всё режем до max_n
    cands = (cands + reg_ids + log_ids)[:max_n]
    total_all = live_count + reg_count + log_count
    res = {"total": len(cands), "deleted": 0, "gone": 0,
           "active_skip": 0, "fail": [], "stopped": False,
           "reg": reg_count, "log": log_count, "live": live_count,
           "total_all": total_all}
    if progress_cb:
        try:
            progress_cb(done=0, current="найдено: " + str(len(cands)),
                        total=len(cands), deleted=0)
        except Exception:
            pass
    for i, lid in enumerate(cands, 1):
        if stop_check and stop_check():
            res["stopped"] = True
            break
        if progress_cb:
            try:
                progress_cb(done=i, current=f"#{lid}",
                            total=len(cands), deleted=res["deleted"])
            except Exception:
                pass
        try:
            d = client.get_lot_details(lid)
        except Exception:
            d = None
        if not d:
            res["gone"] += 1
            continue
        if ((d.get("form_data", {}) or {}).get("active", "") or ""):
            res["active_skip"] += 1
            continue
        ok, msg = client.delete_lot(lid)
        if ok:
            res["deleted"] += 1
            logger.info(f"[PURGE] #{lid}: выключенный удалён")
            if progress_cb:
                try:
                    progress_cb(done=i, current=f"#{lid} ✓",
                                total=len(cands), deleted=res["deleted"])
                except Exception:
                    pass
        else:
            res["fail"].append(f"#{lid}: {msg}")
        time.sleep(2)
    return res

# --- Фоновая задача удаления лотов ---
_DEL_TASK: Dict[str, Any] = {
    "running": False, "total": 0, "done": 0, "deleted": 0,
    "errors": [], "message": "", "finished": True, "current": "", "stop": False,
}

# --- Фоновая задача чистки дубликатов ---
_DUP_TASK: Dict[str, Any] = {
    "running": False, "total": 0, "done": 0, "removed": 0,
    "errors": [], "message": "", "finished": True, "current": "", "stop": False,
}

# --- Фоновая задача массового редактирования выбранных лотов ---
_EDIT_TASK: Dict[str, Any] = {
    "running": False, "total": 0, "done": 0, "edited": 0,
    "errors": [], "message": "", "finished": True, "current": "", "stop": False,
}

def _is_bot_running() -> bool:
    return bool(_BOT_RUNNING) and _BOT_THREAD is not None and _BOT_THREAD.is_alive()

_BOT_INSTANCE = None
BOT_LOCK_FILE = ".bot_running.lock"


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс (кроссплатформенно).
    Важно: os.kill(pid, 0) на Windows врёт про мёртвые PID,
    поэтому там проверяем строго через tasklist."""
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5).stdout or ""
            return str(pid) in out
        except Exception:
            return True  # не смогли проверить — считаем живым
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _bot_lock_active():
    """Проверяет, запущен ли уже бот в другом процессе (защита от двойного запуска)."""
    try:
        if os.path.exists(BOT_LOCK_FILE):
            try:
                pid = int((open(BOT_LOCK_FILE, encoding="utf-8").read() or "0").strip() or 0)
            except Exception:
                pid = 0
            if pid > 0 and pid != os.getpid() and _pid_alive(pid):
                return True, pid
    except Exception:
        pass
    return False, None


def _bot_lock_acquire() -> bool:
    """Атомарно захватывает лок (O_EXCL спасает от двойного клика в одну секунду).
    Возвращает True, если лок наш."""
    try:
        if os.path.exists(BOT_LOCK_FILE):
            try:
                pid = int((open(BOT_LOCK_FILE, encoding="utf-8").read() or "0").strip() or 0)
            except Exception:
                pid = 0
            if not pid or pid == os.getpid() or not _pid_alive(pid):
                try:
                    os.remove(BOT_LOCK_FILE)  # протухший лок — сносим
                except Exception:
                    pass
    except Exception:
        pass
    try:
        fd = os.open(BOT_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        try:
            with open(BOT_LOCK_FILE, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return True
        except Exception:
            return False


def _bot_lock_release():
    try:
        if os.path.exists(BOT_LOCK_FILE):
            os.remove(BOT_LOCK_FILE)
    except Exception:
        pass


def _start_bot_thread() -> Dict[str, Any]:
    global _BOT_THREAD, _BOT_RUNNING, _BOT_ERROR, _BOT_INSTANCE
    if _is_bot_running():
        return {"ok": True, "message": "Бот уже запущен"}
    locked, pid = _bot_lock_active()
    if locked:
        return {"ok": False, "error": f"Бот уже запущен в другом окне (PID {pid}). Закрой его или жми Стоп там."}
    if not _bot_lock_acquire():
        _, pid2 = _bot_lock_active()
        return {"ok": False, "error": f"Бот уже запускается в другом окне (PID {pid2}). Подожди пару секунд."}
    try:
        from main import FunPayUndercutBot
    except Exception as e:
        _BOT_ERROR = str(e)
        return {"ok": False, "error": f"Не удалось загрузить main.py: {e}"}
    try:
        bot = FunPayUndercutBot()
        # быстрая проверка ключа до запуска потока
        auth_ok, uid, uname, err = bot.funpay.check_auth()
        if not auth_ok:
            return {"ok": False, "error": f"Ключ не работает: {err}"}

        _BOT_INSTANCE = bot
        _bot_lock_acquire()

        def _run():
            global _BOT_RUNNING, _BOT_INSTANCE
            try:
                bot.start()
            except Exception as e:
                logger.error(f"Ошибка бота: {e}")
            finally:
                _BOT_RUNNING = False
                _BOT_INSTANCE = None
                _bot_lock_release()

        import threading as _th
        _BOT_THREAD = _th.Thread(target=_run, daemon=True)
        _BOT_RUNNING = True
        _BOT_ERROR = ""
        _BOT_THREAD.start()
        return {"ok": True, "message": f"Бот запущен ({uname}, ID {uid})"}
    except Exception as e:
        _BOT_RUNNING = False
        _BOT_INSTANCE = None
        _bot_lock_release()
        _BOT_ERROR = str(e)
        return {"ok": False, "error": str(e)}

def _stop_bot_thread() -> Dict[str, Any]:
    global _BOT_RUNNING, _BOT_INSTANCE
    inst = _BOT_INSTANCE
    if inst is not None:
        try:
            inst.running = False
        except Exception:
            pass
        try:
            inst.auto_online.stop()
        except Exception:
            pass
        try:
            inst.orders_worker.stop()
        except Exception:
            pass
        _BOT_RUNNING = False
        _BOT_INSTANCE = None
        _bot_lock_release()
        return {"ok": True, "message": "Бот остановлен из панели"}
    _BOT_RUNNING = False
    _bot_lock_release()
    if _BOT_THREAD is None:
        return {"ok": True, "message": "Бот не запущен"}
    return {"ok": True, "message": "Сигнал остановки отправлен"}

# --- Games cache (чтобы не дёргать FunPay при каждом клике) ---
_GAMES_CACHE: Dict[str, Any] = {"ts": 0.0, "games": []}

def _parse_games_with_chips(html: str) -> List[Dict]:
    """Парсит главную FunPay: promo-game-item -> игра + её разделы (chips/lots)."""
    games: List[Dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        items = soup.find_all("div", class_="promo-game-item")
        for it in items:
            title_el = it.find("div", class_="game-title")
            if not title_el:
                continue
            link = title_el.find("a")
            if not link:
                continue
            name = link.get_text(strip=True)
            href = link.get("href", "")
            if not name or not href:
                continue
            data_id = title_el.get("data-id", "") or href
            chips: List[Dict] = []
            seen = set()
            title_links = set(id(a) for a in title_el.find_all("a"))
            for a in it.find_all("a", href=True):
                if id(a) in title_links:
                    continue  # сама игра, не подраздел
                h = a["href"]
                m = re.search(r"/(chips|lots)/(\d+)/?$", h)
                if not m:
                    continue
                kind, cid = m.group(1), m.group(2)
                key = f"{kind}:{cid}"
                if key in seen:
                    continue
                seen.add(key)
                cname = a.get_text(strip=True) or cid
                full = h if h.startswith("http") else f"https://funpay.com/{h.lstrip('/')}"
                chips.append({"chip_id": f"{kind}_{cid}", "kind": kind, "node": cid, "name": cname, "url": full})
            full_game = href if href.startswith("http") else f"https://funpay.com/{href.lstrip('/')}"
            games.append({"game_id": data_id, "name": name, "url": full_game, "chips": chips})
        # Склеиваем дубли (например, два «Роблокса» из разных букв/регионов) в одну карточку
        merged: Dict[str, Dict] = {}
        order: List[str] = []
        for g in games:
            key = g["name"].strip().lower()
            if key not in merged:
                merged[key] = g
                order.append(key)
            else:
                have = set(c["chip_id"] for c in merged[key]["chips"])
                for c in g["chips"]:
                    if c["chip_id"] not in have:
                        have.add(c["chip_id"])
                        merged[key]["chips"].append(c)
        games = [merged[k] for k in order]
    except Exception as e:
        logger.warning(f"Ошибка парсинга игр: {e}")
    return games

def _get_games_cached(client: FunPayClient, force: bool = False) -> List[Dict]:
    import time as _t
    now = _t.time()
    if not force and _GAMES_CACHE["games"] and (now - _GAMES_CACHE["ts"] < 3600):
        return _GAMES_CACHE["games"]
    games: List[Dict] = []
    try:
        resp = client.session.get(client.BASE_URL + "/", timeout=20)
        if resp.status_code == 200:
            games = _parse_games_with_chips(resp.text)
    except Exception as e:
        logger.warning(f"Ошибка загрузки игр: {e}")
    if not games and _GAMES_CACHE["games"]:
        return _GAMES_CACHE["games"]
    if games:
        _GAMES_CACHE["ts"] = now
        _GAMES_CACHE["games"] = games
    return games


# ============================================================================
#  ШАБЛОНЫ ЛОТОВ (saved_lots.json — живут на диске, переживают выход из акка)
# ============================================================================
SAVED_LOTS_FILE = "saved_lots.json"


def _saved_lots_backup_file() -> str:
    return SAVED_LOTS_FILE + ".bak"


def _read_saved_lots() -> list:
    """Читает шаблоны лотов из saved_lots.json (формат: {"templates": [...]}).
    Самовосстановление: если файл пуст, а бэкап нет — поднимаем из бэкапа
    (шаблоны должны переживать что угодно, включая выход из аккаунта)."""
    data = None
    try:
        if os.path.exists(SAVED_LOTS_FILE):
            with open(SAVED_LOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать {SAVED_LOTS_FILE}: {e}")
        data = None

    def _as_list(d):
        if isinstance(d, list):
            items = d
        elif isinstance(d, dict):
            t = d.get("templates")
            items = t if isinstance(t, list) else []
        else:
            items = []
        for t in items:
            if isinstance(t, dict):
                # старый формат полей — приводим к новому
                if not t.get("summary_ru") and t.get("short_ru"):
                    t["summary_ru"] = t["short_ru"]
                if not t.get("summary_en") and t.get("short_en"):
                    t["summary_en"] = t["short_en"]
                if t.get("buyer_price") is None and t.get("price") is not None:
                    try:
                        t["buyer_price"] = float(t.get("price") or 0)
                    except Exception:
                        t["buyer_price"] = 0
                t.setdefault("amount", 1)
        return items

    got = _as_list(data)
    if got:
        return got

    # пусто/битое — пробуем бэкап
    try:
        bak = _saved_lots_backup_file()
        if os.path.exists(bak):
            with open(bak, "r", encoding="utf-8") as f:
                bak_list = _as_list(json.load(f))
            if bak_list:
                logger.warning(f"{SAVED_LOTS_FILE} пуст — восстанавливаю шаблоны из {bak}")
                try:
                    with open(SAVED_LOTS_FILE, "w", encoding="utf-8") as f:
                        json.dump({"templates": bak_list}, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                return bak_list
    except Exception as e:
        logger.warning(f"Бэкап шаблонов не прочитался: {e}")
    return []


def _write_saved_lots(templates: list) -> None:
    try:
        # держим бэкап последней НЕпустой версии
        try:
            if os.path.exists(SAVED_LOTS_FILE):
                with open(SAVED_LOTS_FILE, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                cur_t = cur.get("templates") if isinstance(cur, dict) else cur
                if cur_t:
                    with open(_saved_lots_backup_file(), "w", encoding="utf-8") as f:
                        json.dump({"templates": cur_t}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        with open(SAVED_LOTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"templates": templates}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка записи {SAVED_LOTS_FILE}: {e}")


def _gen_id() -> str:
    return secrets.token_hex(6)


# ============================================================================
#  ТЕКСТ: УЛУЧШЕНИЕ + ПЕРЕВОД (для ИИ-генератора лотов)
# ============================================================================
_TRANSLATE_TIMEOUT = 10


def _extract_style_emojis(style: str) -> List[str]:
    """Достаёт эмодзи (и другие не-буквенные символы) из строки стиля."""
    emojis = []
    for ch in (style or ""):
        if ch.strip() and not ch.isalnum() and not ch.isspace() and ch not in ".,!?;:-()[]{}\"'/\\|":
            if not emojis or emojis[-1] != ch:
                emojis.append(ch)
    return emojis or ["✅", "🔥", "⚡"]


def _cleanup_line(line: str) -> str:
    line = re.sub(r"\s+", " ", (line or "").strip())
    line = re.sub(r"([!?.,])\1+", r"\1", line)            # «!!!»/«,,» -> «!»/«,»
    line = re.sub(r"([.,!?])(?=[^\s\d])", r"\1 ", line)     # пробел после знака
    if line and line[0].isalpha() and line[0].islower():
        line = line[0].upper() + line[1:]
    return line


def _improve_text(text: str, style: str = "") -> str:
    """
    Локальное улучшение текста: чистит пробелы/повторы, строит аккуратные
    строки и оформляет их эмодзи из поля «стиль» (например 🟦🟥🟩🟧).
    Ничего не выдумывает — работает только с тем, что написал пользователь.
    """
    emojis = _extract_style_emojis(style)
    raw_lines = [l for l in (text or "").replace("\r", "").split("\n")]
    # Если текст в одну строку — пробуем разбить на пункты по запяым/точкам
    parts: List[str] = []
    for l in raw_lines:
        l = l.strip()
        if not l:
            continue
        chunks = [c.strip() for c in re.split(r"[;•·]+", l) if c.strip()]
        if len(chunks) == 1 and "," in chunks[0]:
            chunks = [c.strip() for c in chunks[0].split(",") if c.strip()]
        if len(chunks) > 1:
            parts.extend(chunks)
        else:
            parts.append(l)
    cleaned = [_cleanup_line(p) for p in parts]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return ""

    # Убираем полные дубли строк (сохраняя порядок)
    seen, uniq = set(), []
    for c in cleaned:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(c)

    header = uniq[0]
    body = uniq[1:]
    out = [f"{emojis[0]} {header}"]
    for i, line in enumerate(body):
        e = emojis[(i + 1) % len(emojis)]
        out.append(f"{e} {line}")
    return "\n".join(out)


def _translate_text(text: str, to: str = "en") -> Tuple[bool, str, str]:
    """
    Бесплатный перевод без API-ключей.
    1) translate.googleapis.com (gtx)  2) MyMemory как запасной.
    Возвращает (ok, перевод, ошибка).
    """
    text = (text or "").strip()
    if not text:
        return True, "", ""
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "ru", "tl": to, "dt": "t", "q": text},
            timeout=_TRANSLATE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200:
            data = r.json()
            translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
            if translated.strip():
                return True, translated.strip(), ""
    except Exception:
        pass
    try:
        pair = f"ru|{to}"
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": pair},
            timeout=_TRANSLATE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200:
            data = r.json()
            translated = (data.get("responseData") or {}).get("translatedText", "")
            if translated and "MYMEMORY WARNING" not in translated.upper():
                return True, translated.strip(), ""
    except Exception:
        pass
    return False, "", "Переводчик недоступен (сеть). Текст оставлен как был."


_CYR = re.compile(r"[\u0400-\u04FF]")


def _translate_block(text: str, to: str = "en") -> Tuple[bool, str, str]:
    """Построчный перевод: разделители ➖ и строки без русских букв уходят
    как есть, эмодзи-префиксы сохраняются на своём месте. Так структура
    описания не ломается (Google любит переставлять строки)."""
    lines = (text or "").replace("\r", "").split("\n")
    out: List[str] = []
    cache: Dict[str, str] = {}
    ok_all = True
    err_first = ""
    for line in lines:
        core = line.strip()
        # отделяем эмодзи-префикс
        prefix = ""
        m = re.match(r"^([^\w\s«»][^\wа-яА-ЯёЁ]*)\s*", core)
        if m and not _CYR.search(m.group(1)):
            prefix = m.group(1) + " "
            core = core[m.end():]
        if not core or not _CYR.search(core):
            out.append(line)
            continue
        key = core.lower()
        if key in cache:
            out.append((prefix + cache[key]).strip())
            continue
        ok, tr, err = _translate_text(core, to)
        if ok and tr:
            tr = re.sub(r"\s*\n\s*", " ", tr)
            cache[key] = re.sub(r"\s+", " ", tr).strip()
            out.append((prefix.rstrip() + " " + cache[key]).strip())
        else:
            ok_all = False
            if not err_first:
                err_first = err or "перевод"
            out.append(line)
    return ok_all, "\n".join(out), err_first


# Частые опечатки и «как слышится, так пишется» — чиним автоматически
_COMMON_FIXES = [
    (r"\bвалл\s+паперинжект\b", "Wallpaper Engine"),
    (r"\bвапер\s+инжект\b", "Wallpaper Engine"),
    (r"\bпаперинжект\b", "Wallpaper Engine"),
    (r"\bакаунт\b", "аккаунт"),
    (r"\bстим\b", "Steam"),
    (r"\bроблокс\b", "Roblox"),
    (r"\bробукс\b", "Robux"),
    (r"\bробакс\b", "Robux"),
    (r"\bмайнкрафт\b", "Minecraft"),
    (r"\bтелеграмм?\b", "Telegram"),
    (r"\bтг\b", "Telegram"),
    (r"\bдискорд\b", "Discord"),
    (r"\bспотифай\b", "Spotify"),
    (r"\bтикток\b", "TikTok"),
    (r"\bстендофф\b", "Standoff 2"),
    (r"\bгта\b", "GTA"),
    (r"\bютуб\b", "YouTube"),
    (r"\bджейлбрейк\b", "Jailbreak"),
    (r"\bгеймпасс\b", "Gamepass"),
    (r"\bне\s+куда\b", "никуда"),
    (r"\bне\s+когда\b", "никогда"),
    (r"\bв\s+течении\b", "в течение"),
]

# Слова-«продажники» — в заголовке идут после названия товара через «•»
_AD_WORDS = {
    "дешево", "дешёво", "дешевый", "дешёвый", "быстро", "личный", "приватный",
    "официальный", "официально", "гарантия", "гарантию", "моментально",
    "мгновенно", "онлайн", "качественно", "надежно", "надёжно", "безопасно",
    "топ", "выгодно", "навсегда", "пожизненно",
}

# Шаблоны секций по типу товара
_CATEGORY_BLOCKS = {
    "Аккаунты": {
        "what": "📌 ПОКУПАЯ, ВЫ ПОЛУЧАЕТЕ:",
        "important": [
            "❗ Сразу после покупки смените пароль и почту на свои",
            "❗ Гарантия на аккаунт — 24 часа",
            "❗ Я никуда не захожу — аккаунт только ваш",
        ],
    },
    "Ключи и гифты": {
        "what": "📌 ЧТО ВХОДИТ:",
        "important": [
            "❗ Ключ приходит моментально после оплаты",
            "❗ Проверьте ключ сразу после получения",
            "❗ Гарантия на ключ — 24 часа",
        ],
    },
    "Валюта и предметы": {
        "what": "📌 ЧТО ПОЛУЧАЕТЕ:",
        "important": [
            "❗ После оплаты напишите в чат свой игровой ник",
            "❗ Выдача проверяется на месте",
            "❗ Гарантия — до подтверждения получения",
        ],
    },
    "Услуги": {
        "what": "📌 ЧТО ВХОДИТ В УСЛУГУ:",
        "important": [
            "❗ Детали согласуем в чате — напишите после оплаты",
            "❗ Выполняю быстро, отчитываюсь по ходу работы",
            "❗ Гарантия результата",
        ],
    },
    "Другое": {
        "what": "📌 ПОКУПАЯ, ВЫ ПОЛУЧАЕТЕ:",
        "important": [
            "❗ Проверьте товар сразу после получения",
            "❗ Гарантия — 24 часа",
            "❗ Любые вопросы решаем в чате",
        ],
    },
}

_SPLIT_KEYWORDS = r"\s+(?=(?:я\b|автовыдач\w*|гаранти\w*|мгновен\w*|моментальн\w*|пишит\w*))"


def _fix_common_words(text: str) -> str:
    """Авто-исправление частых опечаток/названий (стим → Steam и т.п.)."""
    t = text or ""
    for pat, repl in _COMMON_FIXES:
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)
    return t


def _sep() -> str:
    return "➖" * 15


def _split_features(features: str) -> List[str]:
    """Превращает поток мысли пользователя в аккуратные пункты.
    Переносы строк и «;» — главные разделители. Строки с «:» не режем
    (там перечисление через запятую — это один пункт). Остальное режем
    по запятым и по ключевым словам («я …», «ваш …», «гарантия …»)."""
    raw = (features or "").replace("\r", "")
    parts: List[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        for chunk in re.split(r"[;]+", line):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" in chunk:
                parts.append(chunk)
                continue
            for c in chunk.split(","):
                c = c.strip()
                if not c:
                    continue
                # умное разбиение по ключевым словам («я …», «ваш …», «гарантия …»)
                pieces = re.split(_SPLIT_KEYWORDS, c, flags=re.IGNORECASE)
                clauses = [x.strip() for x in pieces if x.strip()]
                # совсем короткие огрызки приклеиваем к предыдущему пункту
                for cl in clauses:
                    cl = cl.strip()
                    while True:
                        cl2 = re.sub(r"\s+(ваше|вас|все|всё|ок)\.?$", "", cl, flags=re.I).strip()
                        if cl2 == cl:
                            break
                        cl = cl2
                    if not cl:
                        continue
                    if len(cl) < 4 and parts + clauses:
                        continue
                    parts.append(cl)
    # дубли убираем
    seen, uniq = set(), []
    for p_ in parts:
        k = re.sub(r"\s+", " ", p_.lower()).strip()
        if k and k not in seen:
            seen.add(k)
            uniq.append(p_)
    return uniq[:14]


def _build_title(idea: str, emojis: List[str]) -> str:
    """КАПС-заголовок: НАЗВАНИЕ • ДЕШЕВО • БЫСТРО + эмодзи по 2 в начале и в конце."""
    t = _fix_common_words(idea or "")
    t = re.sub(r"\s+", " ", t).strip(" .,!?-–—•")
    words = [w.strip(",.") for w in t.split() if w.strip(",.")]
    prod = [w for w in words if w.lower() not in _AD_WORDS]
    ads = [w.upper() for w in words if w.lower() in _AD_WORDS]
    head = " ".join(prod).upper()
    title = head if not ads else f"{head} • {' • '.join(ads)}"
    if not title:
        title = " ".join(words).upper()
    title = title[:120].strip(" •-")
    e0, e1 = emojis[0], emojis[1 % len(emojis)]
    eL, eP = emojis[-1], emojis[-2 if len(emojis) > 1 else -1]
    return f"{e0}{e1} {title} {eP}{eL}"


def _generate_lot_texts(idea: str, features: str, style: str,
                        buyer_msg: bool = False,
                        category: str = "Другое") -> Dict[str, str]:
    """
    «ИИ-генератор лота»: собирает продающий заголовок (КАПС + «•» + эмодзи
    по 2 в начале/конце) и структурированное описание с разделителями и
    секцией «ВАЖНО» под тип товара — в стиле из эмодзи пользователя.
    """
    emojis = _extract_style_emojis(style)
    block = _CATEGORY_BLOCKS.get((category or "Другое").strip(), _CATEGORY_BLOCKS["Другое"])

    title_ru = _build_title(idea, emojis)
    title_plain = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]", "",
                         title_ru).strip()

    feature_lines = [_cleanup_line(_fix_common_words(x)) for x in _split_features(features)]

    desc: List[str] = [f"{emojis[0]} {title_plain} {emojis[-1]}".replace("  ", " ")]
    desc.append(_sep())
    desc.append("")
    for i, fl in enumerate(feature_lines):
        desc.append(f"{emojis[i % len(emojis)]} {fl}")
    if feature_lines:
        desc.append("")
    desc.append(_sep())
    for line in block["important"]:
        desc.append(line)
    desc.append(_sep())
    closers = ["Отвечаю быстро — выдаю моментально",
               "Всё в наличии, работаю каждый день",
               "Пишите в чат — отвечу без проблем"]
    for i, cl in enumerate(closers):
        desc.append(f"{emojis[(i + 1) % len(emojis)]} {cl}")
    desc_ru = "\n".join(desc)

    pay_ru = ""
    if buyer_msg:
        pay_ru = (
            f"{emojis[0]} Спасибо за покупку!\n"
            "Твой товар ниже 👇\n"
        )
        if (category or "").strip() == "Аккаунты":
            pay_ru += f"{emojis[1 % len(emojis)]} Сразу смени пароль и почту на свои.\n"
        pay_ru += f"{emojis[-1]} Если что-то не так — напиши в чат, решим быстро."

    return {"title_ru": title_ru, "desc_ru": desc_ru, "pay_ru": pay_ru}


def _lot_payload_from_details(details: Dict[str, Any], lot_id: str) -> Dict[str, Any]:
    """Разворачивает ответ get_lot_details() в плоский словарь лота
    (используется и для копирования лота, и для шаблонов)."""
    fd = details.get("form_data", {}) or {}
    auto_d = fd.get("fields[payment_msg][ru]", "") or next(
        (v for k, v in fd.items() if "deliver" in k.lower() and v), "")
    auto_r = fd.get("fields[payment_msg][en]", "") or next(
        (v for k, v in fd.items() if "response" in k.lower() and v), "")
    try:
        amount = max(1, int(float(fd.get("amount", fd.get("fields[amount]", 1)) or 1)))
    except Exception:
        amount = 1
    return {
        "lot_id": lot_id,
        "summary_ru": fd.get("fields[summary][ru]", "") or details.get("summary", ""),
        "summary_en": fd.get("fields[summary][en]", ""),
        "desc_ru": fd.get("fields[desc][ru]", ""),
        "desc_en": fd.get("fields[desc][en]", ""),
        "offer_type": fd.get("fields[type]", ""),
        "seller_price": details.get("seller_price", 0) or 0,
        "buyer_price": details.get("buyer_price", 0) or 0,
        "amount": amount,
        "server": fd.get("fields[server]", "") or "",
        "side": fd.get("fields[side]", "") or "",
        "subcategory": fd.get("fields[subcategory]", "") or "",
        "prompt_auto": auto_d,
        "prompt_response": auto_r,
        "secrets": fd.get("secrets", "") or "",
        "auto_delivery": fd.get("auto_delivery", "") or "",
        "node_id": details.get("node_id", "") or "",
    }


def _diff_patches(old: str, new: str) -> List[Tuple[str, str]]:
    """Умная замена: пары (старый_кусок → новый_кусок) между образцами.

    Сравниваем образцы «было» и «стало» пословно (LCS) и выносим только
    изменённые места с небольшим контекстом-якорем. Затем этот же кусок
    меняется в тексте КАЖДОГО выбранного лота — его собственные отличия
    (другое название, цена в тексте и т.п.) сохраняются.
    """
    import re as _re

    a = [t for t in _re.split(r"(\s+)", old or "") if t]
    b = [t for t in _re.split(r"(\s+)", new or "") if t]
    n, m = len(a), len(b)
    if not n and not m:
        return []
    if n * m > 1_500_000:
        # очень длинные тексты — тупо полная замена
        return [(old or "", new or "")] if (old or "") != (new or "") else []

    W = m + 1
    dp = [0] * ((n + 1) * W)
    for i in range(n - 1, -1, -1):
        ai = a[i]
        row, row_below = i * W, (i + 1) * W
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                dp[row + j] = dp[row_below + j + 1] + 1
            else:
                x, y = dp[row_below + j], dp[row + j + 1]
                dp[row + j] = x if x >= y else y

    # шагаем по DP: eq/del/ins
    ops: List[Tuple[str, str]] = []  # ("eq"|"del"|"ins", токен)
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            ops.append(("eq", a[i])); i += 1; j += 1
        elif dp[(i + 1) * W + j] >= dp[i * W + j + 1]:
            ops.append(("del", a[i])); i += 1
        else:
            ops.append(("ins", b[j])); j += 1
    while i < n:
        ops.append(("del", a[i])); i += 1
    while j < m:
        ops.append(("ins", b[j])); j += 1

    # склейка подряд идущих изменений + якорь из соседних неизменённых токенов
    patches: List[Tuple[str, str]] = []
    k, total = 0, len(ops)
    while k < total:
        if ops[k][0] == "eq":
            k += 1
            continue
        k1 = k
        dels: List[str] = []
        inss: List[str] = []
        while k < total and ops[k][0] != "eq":
            (dels if ops[k][0] == "del" else inss).append(ops[k][1])
            k += 1
        # якорь: до 2 неизменённых токенов слева и справа
        pre = "".join(t[1] for t in ops[max(0, k1 - 2):k1] if t[0] == "eq")
        post = "".join(t[1] for t in ops[k:k + 2] if t[0] == "eq")
        old_frag = pre + "".join(dels) + post
        new_frag = pre + "".join(inss) + post
        if old_frag != new_frag:
            patches.append((old_frag, new_frag))
    return patches


def _fuzzy_find(text: str, frag: str, min_ratio: float = 0.68):
    """Ищет в тексте кусок, ПОХОЖИЙ на frag (описания лотов почти одинаковы,
    но не 1-в-1). Возвращает (начало, длина) лучшего совпадения или None.

    Окно = столько же слов, сколько в frag (±20%), похожесть —
    difflib.SequenceMatcher.ratio() >= min_ratio.
    """
    import difflib
    import re as _re

    frag = (frag or "").strip()
    if not frag:
        return None
    ftoks = [t for t in _re.split(r"\s+", frag) if t]
    if not ftoks:
        return None
    ttoks = [(t.group(), t.start(), t.end())
             for t in _re.finditer(r"\S+", text)]
    if not ttoks:
        return None
    fstr = " ".join(ftoks)
    target = max(1, len(ftoks))
    lo = max(1, int(target * 0.8))
    hi = min(len(ttoks), int(target * 1.25) + 1)

    best = None  # (ratio, start_char, end_char)
    for wlen in range(lo, hi + 1):
        for s in range(0, len(ttoks) - wlen + 1):
            start = ttoks[s][1]
            end = ttoks[s + wlen - 1][2]
            window = text[start:end]
            if abs(len(window) - len(fstr)) > max(60, len(fstr)):
                continue
            r = difflib.SequenceMatcher(None, fstr, window).ratio()
            if r >= min_ratio and (best is None or r > best[0]):
                best = (r, start, end)
                if r > 0.97:
                    return (best[1], best[2])
    return (best[1], best[2]) if best else None


def _smart_patcher(old_sample: str, new_sample: str):
    """Возвращает функцию: текст конкретного лота → новый текст.
    None = лот слишком отличается, менять нельзя (пропустить).

    Умная замена: из образцов «было/стало» вычисляются изменённые куски,
    затем этот же кусок меняется в тексте каждого лота. Если у лота текст
    немного другой (не 1-в-1) — кусок ищется нечётко (до ~32% различий),
    его собственные формулировки сохраняются.
    """
    old_s = old_sample or ""
    new_s = new_sample or ""

    if old_s == new_s:
        return lambda cur: cur

    if not old_s.strip():
        # у образца поле было пустым — просто ставим новый текст всем
        return lambda cur: new_s

    if not new_s.strip():
        # очистить поле у всех
        return lambda cur: ""

    patches = _diff_patches(old_s, new_s)
    if not patches:
        return lambda cur: cur

    def _apply(cur: str):
        cur = cur or ""
        if cur == old_s:
            return new_s
        out = cur
        for old_frag, new_frag in patches:
            if not old_frag:
                continue
            if old_frag in out:
                out = out.replace(old_frag, new_frag)
                continue
            # точного куска нет (текст немного другой) — ищем похожий
            hit = _fuzzy_find(out, old_frag)
            if hit is None:
                return None  # лот отличается сильнее, чем можем починить
            start, end = hit
            out = out[:start] + new_frag + out[end:]
        return out

    return _apply


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FunPay Undercut Bot Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            /* Black theme (soft) */
            --bg-base: #14141b;
            --bg-surface: #1e1e29;
            --bg-card: #20202c;
            --border-card: #32323f;
            --accent-primary: #8b5cf6;
            --accent-primary-hover: #7c3aed;
            --accent-success: #10b981;
            --accent-warning: #f59e0b;
            --accent-danger: #ef4444;
            --text-main: #e8e8ef;
            --text-muted: #9a9ab0;
            --font-main: 'Inter', -apple-system, sans-serif;
            --font-title: 'Outfit', sans-serif;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-main);
            font-family: var(--font-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        /* HEADER - Modern style */
        header {
            background: var(--bg-surface);
            border-bottom: 1px solid var(--border-card);
            padding: 12px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-icon {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #6366f1, #4f46e5);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            box-shadow: 0 2px 8px rgba(99, 102, 241, 0.25);
        }

        .brand-title {
            font-family: var(--font-title);
            font-size: 18px;
            font-weight: 700;
            color: var(--text-main);
            letter-spacing: -0.3px;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 500;
            background: #ecfdf5;
            border: 1px solid #10b981;
            color: #065f46;
        }

        .status-badge.error {
            background: #fef2f2;
            border: 1px solid #ef4444;
            color: #991b1b;
        }

        .status-badge.warning {
            background: #fffbeb;
            border: 1px solid #f59e0b;
            color: #92400e;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #10b981;
            box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);
        }
        .status-dot.error {
            background-color: #ef4444;
            box-shadow: 0 0 8px rgba(239, 68, 68, 0.4);
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }

        /* NAVIGATION TABS - Modern style */
        .tabs-nav {
            display: flex;
            gap: 4px;
            padding: 0 32px;
            max-width: 1300px;
            width: 100%;
            margin: 0 auto;
        }

        .tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 12px 24px;
            font-size: 14px;
            font-weight: 500;
            font-family: var(--font-main);
            border-radius: 8px 8px 0 0;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 8px;
            border-bottom: 2px solid transparent;
        }

        .tab-btn:hover {
            color: var(--accent-primary);
            background: #f3f4f6;
        }

        .tab-btn.active {
            color: var(--accent-primary);
            background: var(--bg-surface);
            border-bottom: 2px solid var(--accent-primary);
        }

        /* MAIN CONTENT */
        main {
            flex: 1;
            padding: 24px 32px 40px 32px;
            max-width: 1300px;
            width: 100%;
            margin: 0 auto;
        }

        .tab-pane {
            display: none;
        }

        .tab-pane.active {
            display: block;
            animation: fadeIn 0.25s ease-out;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* STATS CARDS - Modern style */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-card);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            align-items: center;
            gap: 16px;
            transition: all 0.2s;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }

        .stat-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }

        .stat-icon {
            width: 48px;
            height: 48px;
            border-radius: 10px;
            background: #eef2ff;
            color: #6366f1;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }

        .stat-icon.green { background: #ecfdf5; color: var(--accent-success); }
        .stat-icon.orange { background: #fffbeb; color: var(--accent-warning); }
        .stat-icon.purple { background: #f5f3ff; color: #8b5cf6; }

        .stat-info h4 {
            font-size: 11px;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .stat-info .stat-value {
            font-size: 24px;
            font-family: var(--font-title);
            font-weight: 700;
            color: var(--text-main);
            margin-top: 4px;
        }

        /* CARD CONTAINERS - Modern style */
        .card {
            background: var(--bg-surface);
            border: 1px solid var(--border-card);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }

        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 2px solid #f3f4f6;
        }

        .card-title {
            font-family: var(--font-title);
            font-size: 18px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
            color: var(--text-main);
        }

        /* LOTS TABLE & SEARCH - Modern style */
        .lots-controls {
            display: flex;
            gap: 12px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }

        .search-input {
            flex: 1;
            min-width: 250px;
            background: var(--bg-surface);
            border: 1px solid var(--border-card);
            color: var(--text-main);
            padding: 10px 16px;
            border-radius: 8px;
            font-size: 14px;
            outline: none;
            transition: all 0.2s;
        }

        .search-input:focus {
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }

        .lot-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 14px 18px;
            border-radius: 8px;
            background: var(--bg-surface);
            border: 1px solid var(--border-card);
            margin-bottom: 10px;
            transition: all 0.2s ease;
        }

        /* === Массовое изменение лотов: чипы, табы, живой diff === */
        .be-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 14px; }
        .be-chip {
            display: inline-flex; align-items: center; gap: 7px;
            background: #15151e; border: 1px solid var(--border-card);
            border-radius: 9px; padding: 6px 11px; font-size: 12px;
            color: var(--text); max-width: 260px; transition: border-color .15s;
        }
        .be-chip:hover { border-color: #3b82f6; }
        .be-chip-id { color: #60a5fa; font-weight: 700; }
        .be-chip-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); }
        .be-chip-x { cursor: pointer; color: var(--text-muted); font-weight: 700; padding: 0 2px; }
        .be-chip-x:hover { color: #ef4444; }
        .be-chip-more { color: var(--text-muted); font-size: 12px; align-self: center; }

        .be-editor {
            background: #15151e; border: 1px solid #3b82f6; border-radius: 14px;
            padding: 14px; margin: 0 auto; max-width: 980px;
        }
        .be-tabs { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .be-tab {
            background: transparent; border: 1px solid var(--border-card); color: var(--text-muted);
            border-radius: 9px; padding: 8px 14px; font-size: 13px; cursor: pointer; font-weight: 600;
        }
        .be-tab.active { border-color: #3b82f6; color: #93c5fd; background: rgba(59,130,246,.08); }
        .be-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #34d399; margin-left: 6px; vertical-align: middle; opacity: 0; transition: opacity .15s; }
        .be-dot.on { opacity: 1; }
        .be-usecheck { margin-left: auto; font-size: 12.5px; color: var(--text-muted); display: flex; align-items: center; gap: 7px; cursor: pointer; }
        .be-usecheck input { accent-color: #3b82f6; width: 15px; height: 15px; cursor: pointer; }

        .be-editor textarea {
            width: 100%; margin-top: 12px; resize: vertical;
            font-size: 13.5px; line-height: 1.55;
        }
        .be-preview {
            margin-top: 12px; border: 1px dashed var(--border-card);
            border-radius: 10px; padding: 11px 13px; background: #101018;
        }
        .be-preview-caption { font-size: 11.5px; color: var(--text-muted); margin-bottom: 8px; }
        .be-preview-row { font-size: 13px; line-height: 1.65; white-space: pre-wrap; word-break: break-word; margin-bottom: 8px; }
        .be-preview-label { display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: .4px; color: var(--text-muted); text-transform: uppercase; margin-right: 6px; }
        .be-ins { background: rgba(34,197,94,.14); color: #4ade80; border-bottom: 1px solid #4ade80; border-radius: 3px; }
        .be-del { background: rgba(239,68,68,.10); color: #f87171; text-decoration: line-through; border-radius: 3px; }
        .be-muted { color: var(--text-muted); }

        .lot-item:hover {
            border-color: #d1d5db;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        }

        .lot-item.disabled-lot {
            opacity: 0.6;
            background: #f9fafb;
            border-left: 3px solid var(--accent-warning);
        }

        .lot-left {
            display: flex;
            align-items: center;
            gap: 16px;
            flex: 1;
            min-width: 0;
        }

        .lot-id-badge {
            font-size: 11px;
            font-family: monospace;
            background: #f3f4f6;
            padding: 4px 8px;
            border-radius: 6px;
            color: #4b5563;
            white-space: nowrap;
        }

        .lot-title-box {
            min-width: 0;
            flex: 1;
        }

        .lot-title {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .lot-meta {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 2px;
            display: flex;
            gap: 12px;
        }

        .lot-right {
            display: flex;
            align-items: center;
            gap: 20px;
            margin-left: 16px;
        }

        .lot-price {
            font-family: var(--font-title);
            font-size: 16px;
            font-weight: 700;
            color: var(--accent-primary);
            white-space: nowrap;
        }

        /* TOGGLE SWITCH - Modern style */
        .switch-row {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-top: 8px;
        }

        .switch-label-text {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-main);
            line-height: 1.4;
        }

        .switch {
            position: relative;
            display: inline-block;
            width: 44px;
            height: 24px;
            min-width: 44px;
            flex-shrink: 0;
        }

        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }

        .slider {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: #d1d5db;
            transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
            border-radius: 24px;
        }

        .slider:before {
            position: absolute;
            content: "";
            height: 18px;
            width: 18px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
            border-radius: 50%;
        }

        input:checked + .slider {
            background-color: var(--accent-primary);
        }

        input:checked + .slider:before {
            transform: translateX(20px);
        }

        /* FORMS & SETTINGS - Modern style */
        .form-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }

        .form-group {
            margin-bottom: 16px;
        }

        .form-group label {
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 6px;
        }

        .form-group .form-hint {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .form-control {
            width: 100%;
            background: var(--bg-surface);
            border: 1px solid var(--border-card);
            color: var(--text-main);
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 14px;
            font-family: var(--font-main);
            outline: none;
            transition: border-color 0.2s;
        }

        .form-control:focus {
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
        }

        /* BUTTONS - Modern style */
        .btn {
            background: var(--accent-primary);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            text-decoration: none;
            box-shadow: 0 2px 4px rgba(99, 102, 241, 0.2);
        }

        .btn:hover {
            background: var(--accent-primary-hover);
            box-shadow: 0 4px 8px rgba(99, 102, 241, 0.3);
        }

        .btn-success {
            background: var(--accent-success);
        }
        .btn-success:hover {
            background: #059669;
            box-shadow: 0 4px 8px rgba(16, 185, 129, 0.3);
        }

        .btn-outline {
            background: transparent;
            border: 1px solid var(--border-card);
            color: var(--text-main);
        }
        .btn-outline:hover {
            background: #f3f4f6;
            border-color: #d1d5db;
        }

        /* TOAST NOTIFICATION - Modern style */
        #toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #ffffff;
            border-left: 4px solid var(--accent-success);
            color: var(--text-main);
            padding: 16px 24px;
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.15);
            display: none;
            z-index: 1000;
            font-size: 14px;
            font-weight: 500;
            animation: slideUp 0.3s ease-out;
        }
        #toast.error {
            border-left-color: var(--accent-danger);
        }
        #toast.warning {
            border-left-color: var(--accent-warning);
        }

        @keyframes slideUp {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        /* LOGS CONSOLE - Modern style */
        .logs-box {
            background: #f8f9fa;
            border: 1px solid var(--border-card);
            border-radius: 8px;
            padding: 16px;
            font-family: 'Consolas', monospace;
            font-size: 12px;
            color: #4b5563;
            max-height: 420px;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.5;
        }

        /* MODAL STYLES - Modern style */
        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            padding: 16px;
        }
        .modal-content {
            background: #ffffff;
            border: 1px solid var(--border-card);
            border-radius: 12px;
            max-width: 500px;
            width: 100%;
            padding: 24px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.15);
            animation: modalPop 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        }
        @keyframes modalPop {
            from { transform: scale(0.92); opacity: 0; }
            to { transform: scale(1); opacity: 1; }
        }
        .modal-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 16px;
            border-bottom: 2px solid #f3f4f6;
        }
        .btn-close {
            background: #f3f4f6;
            border: 1px solid #d1d5db;
            color: var(--text-main);
            font-size: 16px;
            width: 32px;
            height: 32px;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
        }
        .btn-close:hover {
            background: #e5e7eb;
        }

        /* OFFERS GRID (CREATE LOTS TAB) - Modern style */
        .offers-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 12px;
            margin-top: 16px;
            max-height: 600px;
            overflow-y: auto;
            padding: 8px;
        }

        .offer-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-card);
            border-radius: 8px;
            padding: 14px;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }

        .offer-card:hover {
            border-color: var(--accent-primary);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.15);
        }

        .offer-card.selected {
            border-color: var(--accent-success);
            background: #ecfdf5;
            box-shadow: 0 0 20px rgba(16, 185, 129, 0.2);
        }

        .offer-card .check-indicator {
            position: absolute;
            top: 10px;
            right: 10px;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: #f3f4f6;
            border: 2px solid #d1d5db;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            transition: all 0.2s ease;
        }

        .offer-card.selected .check-indicator {
            background: var(--accent-success);
            border-color: var(--accent-success);
            color: white;
        }

        .offer-card .offer-title {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 8px;
            padding-right: 30px;
            line-height: 1.4;
        }

        .offer-card .offer-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            font-size: 12px;
            color: var(--text-muted);
        }

        .offer-card .offer-meta span {
            background: #f0f0f0;
            padding: 2px 8px;
            border-radius: 3px;
        }

        .offer-card .offer-price {
            font-family: var(--font-title);
            font-size: 18px;
            font-weight: 700;
            color: var(--accent-primary);
            margin-top: 10px;
        }

        .loading-spinner {
            text-align: center;
            padding: 60px;
            color: var(--text-muted);
        }

        .loading-spinner::after {
            content: '';
            display: inline-block;
            width: 24px;
            height: 24px;
            border: 3px solid var(--accent-primary);
            border-top-color: #ffffff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-left: 10px;
            vertical-align: middle;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* BLACK THEME OVERRIDES */
        header {
            background: #1b1b26;
            border-bottom: 1px solid var(--border-card);
            box-shadow: 0 1px 12px rgba(0,0,0,0.5);
        }
        .brand-icon {
            background: linear-gradient(135deg, #8b5cf6, #6d28d9);
            box-shadow: 0 2px 12px rgba(139, 92, 246, 0.4);
        }
        .status-badge {
            background: #0c1f17;
        }
        .status-badge.error {
            background: #221114;
        }
        .status-badge.warning {
            background: #221a0c;
        }
        .tab-btn:hover {
            background: #1c1c28;
        }
        .tab-btn.active {
            background: var(--bg-surface);
        }
        .stat-card, .card {
            background: var(--bg-surface);
            box-shadow: 0 2px 16px rgba(0,0,0,0.4);
        }
        .stat-card:hover {
            box-shadow: 0 4px 20px rgba(0,0,0,0.6);
        }
        .stat-icon { background: #221d3d; color: #a78bfa; }
        .stat-icon.green { background: #0c1f17; }
        .stat-icon.orange { background: #221a0c; }
        .stat-icon.purple { background: #221d3d; }
        .card-header { border-bottom: 2px solid #2e2e3d; }
        .search-input { background: #15151e; }
        .lot-item { background: #1b1b26; }
        .lot-item:hover { border-color: #3d3d55; box-shadow: 0 2px 12px rgba(0,0,0,0.5); }
        .lot-item.disabled-lot { background: #232330; }
        .lot-id-badge { background: #2e2e3d; color: #a1a1b5; }
        .form-control { background: #15151e; }
        .form-control:disabled { opacity: 0.5; }
        .btn-outline { border: 1px solid #333344; color: var(--text-main); background: transparent; }
        .btn-outline:hover { background: #1e1e2a; border-color: #4d4d66; }
        #toast { background: #16161f; border: 1px solid var(--border-card); box-shadow: 0 8px 24px rgba(0,0,0,0.6); }
        .logs-box { background: #121219; color: #9d9db5; }
        .modal-content { background: #1e1e29; box-shadow: 0 20px 60px rgba(0,0,0,0.7); }
        .modal-header { border-bottom: 2px solid #2e2e3d; }
        .btn-close { background: #2e2e3d; border: 1px solid #333344; }
        .btn-close:hover { background: #2e2e3e; }
        .offer-card { background: #1b1b26; box-shadow: 0 1px 6px rgba(0,0,0,0.4); }
        .offer-card.selected { background: #0c1f17; }
        .offer-card .check-indicator { background: #2e2e3d; border: 2px solid #44445a; }
        .offer-card .offer-meta span { background: #2e2e3d; color: #a1a1b5; }
        .game-card { background: #1b1b26; border: 1px solid var(--border-card); border-radius: 12px; padding: 16px; cursor: pointer; transition: all 0.2s ease; text-align: center; }
        .game-card:hover { border-color: var(--accent-primary); transform: translateY(-2px); box-shadow: 0 4px 20px rgba(139,92,246,0.25); }
        .game-card .game-icon { font-size: 34px; margin-bottom: 8px; }
        .game-card .game-name { font-size: 14px; font-weight: 600; color: var(--text-main); }
        .game-card .game-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
        .game-card.selected { border-color: var(--accent-success); background: #12352a; box-shadow: 0 0 16px rgba(16,185,129,0.25); }
        .chip-filter-btn.active-filter, .chip-mode-btn.active-filter, .slot-tab.active-filter, .lot-filter-btn.active-filter { border-color: var(--accent-primary); color: #c4b5fd; background: #26233d; }
        .games-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; margin-top: 16px; max-height: 380px; overflow-y: auto; padding: 8px; }
        .chip-card { background: #1b1b26; border: 1px solid var(--border-card); border-radius: 8px; padding: 12px; cursor: pointer; transition: all 0.2s ease; position: relative; }
        .chip-card:hover { border-color: var(--accent-primary); }
        .chip-card.selected { border-color: var(--accent-success); background: #0c1f17; box-shadow: 0 0 16px rgba(16,185,129,0.2); }
        .chip-card .check-indicator { position: absolute; top: 8px; right: 8px; width: 22px; height: 22px; border-radius: 50%; background: #2e2e3d; border: 2px solid #44445a; display: flex; align-items: center; justify-content: center; font-size: 12px; }
        .chip-card.selected .check-indicator { background: var(--accent-success); border-color: var(--accent-success); color: white; }
        .chip-card .chip-name { font-size: 13px; font-weight: 600; padding-right: 26px; }
        .chip-card .chip-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
        .chips-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; margin-top: 12px; max-height: 300px; overflow-y: auto; padding: 8px; }
        .bot-dot { width: 8px; height: 8px; border-radius: 50%; background: #555; }
        .bot-dot.running { background: #10b981; box-shadow: 0 0 8px rgba(16,185,129,0.6); animation: pulse 1.5s infinite; }

        /* MOBILE RESPONSIVE MEDIA QUERIES */
        @media (max-width: 768px) {
            header {
                padding: 12px 16px;
                flex-wrap: wrap;
                gap: 10px;
            }
            .brand-title {
                font-size: 17px;
            }
            .header-actions {
                width: 100%;
                justify-content: flex-end;
                flex-wrap: wrap;
                gap: 8px;
            }
            .status-badge {
                font-size: 11px;
                padding: 4px 10px;
            }
            .tabs-nav {
                padding: 12px 16px 0 16px;
                overflow-x: auto;
                flex-wrap: nowrap;
                -webkit-overflow-scrolling: touch;
            }
            .tab-btn {
                padding: 8px 14px;
                font-size: 13px;
                white-space: nowrap;
            }
            main {
                padding: 16px 14px 40px 14px;
            }
            .stats-grid {
                grid-template-columns: 1fr 1fr;
                gap: 10px;
            }
            .stat-card {
                padding: 14px;
                gap: 10px;
            }
            .stat-icon {
                width: 38px;
                height: 38px;
                font-size: 18px;
            }
            .stat-info .stat-value {
                font-size: 18px;
            }
            .card {
                padding: 16px;
                border-radius: 12px;
            }
            .card-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 6px;
            }
            .lot-item {
                flex-direction: column;
                align-items: stretch;
                gap: 12px;
            }
            .lot-right {
                margin-left: 0;
                padding-top: 10px;
                border-top: 1px solid var(--border-card);
                justify-content: space-between;
                width: 100%;
            }
            .form-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>

    <header>
        <div class="brand">
            <div class="brand-icon">⚡</div>
            <div class="brand-title">FunPay Auto-Undercut Bot</div>
        </div>
        <div class="header-actions">
            <div class="status-badge" id="botBadge" title="Статус бота">
                <span class="bot-dot" id="botDot"></span>
                <span id="botStatus">Бот: ...</span>
            </div>
            <button class="btn btn-success" id="btnStartBot" onclick="botStart()">▶ Старт</button>
            <button class="btn btn-outline" id="btnStopBot" onclick="botStop()">⏹ Стоп</button>
            <div class="status-badge" id="accountBadge">
                <span class="status-dot"></span>
                <span id="accountName">Загрузка...</span>
            </div>
            <button class="btn btn-outline" onclick="openPhoneModal()">📱 На телефон</button>
            <button class="btn" onclick="saveSettings()">Сохранить настройки</button>
            <button class="btn btn-outline" onclick="location.href='/api/config/export'" title="Скачай config.json и загрузи его в репозиторий GitHub — тогда настройки на хостинге не слетят при рестарте">⬇️ Скачать настройки для хостинга</button>
        </div>
    </header>

    <div class="tabs-nav">
        <button class="tab-btn active" onclick="switchTab('tab-lots')">📦 Мои лоты (<span id="lotsCount">0</span>)</button>
        <button class="tab-btn" onclick="switchTab('tab-create')">✨ Создать лоты</button>
        <button class="tab-btn" onclick="switchTab('tab-templates')">📋 Шаблоны</button>
        <button class="tab-btn" onclick="switchTab('tab-text')">📝 Текст</button>
        <button class="tab-btn" onclick="switchTab('tab-orders')">💬 Авто-заказы</button>
        <button class="tab-btn" onclick="switchTab('tab-settings')">⚙️ Настройки бота</button>
        <button class="tab-btn" onclick="switchTab('tab-logs')">📜 Логи работы</button>
    </div>

    <main>
        <!-- STATS OVERVIEW -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon purple">📦</div>
                <div class="stat-info">
                    <h4>Всего лотов</h4>
                    <div class="stat-value" id="statTotalLots">-</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon green">⚡</div>
                <div class="stat-info">
                    <h4>В автодемпинге</h4>
                    <div class="stat-value" id="statActiveLots">-</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon orange">⏸</div>
                <div class="stat-info">
                    <h4>Отключено</h4>
                    <div class="stat-value" id="statDisabledLots">-</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">🎲</div>
                <div class="stat-info">
                    <h4>Режим интервала</h4>
                    <div class="stat-value" id="statMode">-</div>
                </div>
            </div>
        </div>

        <!-- TAB 1: LOTS MANAGEMENT -->
        <div id="tab-lots" class="tab-pane active">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📦 Управление лотами и отключение автозамены</div>
                    <div style="font-size: 13px; color: var(--text-muted);">
                        Переключатель <b>ВЫКЛ</b> полностью защищает лот от смены цен ботом.
                    </div>
                </div>

                <div class="lots-controls">
                    <input type="text" class="search-input" id="lotSearch" placeholder="🔍 Быстрый поиск по названию или ID лота..." oninput="filterLots()">
                    <button class="btn btn-outline" onclick="loadLots(true)">🔄 Обновить список</button>
                    <button class="btn btn-outline" onclick="findDuplicates()">🔍 Найти дубликаты</button>
                    <button class="btn btn-outline" onclick="cleanupDuplicates()">🧹 Убрать дубликаты</button>
                    <button class="btn btn-outline" onclick="dupStop()">⏹</button>
                    <button class="btn btn-outline" onclick="purgeInactive()">🗑 Удалить выключенные</button>
                    <button class="btn btn-outline" onclick="reactivateDeactivated()">↩️ Вернуть снятые</button>
                </div>
                <div class="lots-controls" style="margin-top:8px;">
                    <button class="btn btn-outline lot-filter-btn active-filter" onclick="setLotFilter('all', this)">🔄 Все</button>
                    <button class="btn btn-outline lot-filter-btn" onclick="setLotFilter('accounts', this)">🎭 Аккаунты</button>
                    <button class="btn btn-outline lot-filter-btn" onclick="setLotFilter('items', this)">💰 Предметы/валюта</button>
                    <button class="btn btn-outline lot-filter-btn" onclick="setLotFilter('services', this)">🛠 Услуги</button>
                    <button class="btn btn-outline lot-filter-btn" onclick="setLotFilter('guides', this)">📖 Гайды</button>
                    <button class="btn btn-outline lot-filter-btn" onclick="setLotFilter('gamepass', this)">🎫 Gamepass</button>
                    <button class="btn btn-outline lot-filter-btn" onclick="setLotFilter('other', this)">📦 Прочее</button>
                </div>
                <div class="lots-controls" style="margin-top:8px;">
                    <button class="btn btn-outline" onclick="selectVisibleLots()">✅ Выбрать видимые</button>
                    <button class="btn btn-outline" onclick="clearLotSelection()">❌ Снять выбор</button>
                    <button class="btn" style="border-color:#3b82f6;color:#93c5fd;" onclick="openBulkEdit()">✏ Изменить выбранные (<span id="editCount">0</span>)</button>
                    <button class="btn btn-outline" style="border-color:#ef4444;color:#fca5a5;" onclick="deleteSelectedLots()">🗑 Удалить выбранные (<span id="delCount">0</span>)</button>
                    <button class="btn btn-outline" onclick="delStop()">⏹ Стоп</button>
                    <span id="delStatus" style="color: var(--text-muted); font-size: 13px;"></span>
                </div>

                <!-- Массовое изменение выбранных лотов: чипы лотов + центральный редактор с живой подсветкой -->
                <div id="bulkEditBox" style="display:none; margin:10px 0; background:#101018; border:1px solid #3b82f6; border-radius:14px; padding:16px;">
                    <div style="display:flex; align-items:center; gap:10px; margin-bottom:2px;">
                        <div style="font-weight:700; font-size:15px;">✏️ Массовое изменение лотов: <span id="bulkEditCount" style="color:#60a5fa;">0</span> шт.</div>
                        <button class="btn btn-outline" style="margin-left:auto; padding:4px 10px; font-size:12px;" onclick="closeBulkEdit()">✕ Свернуть</button>
                    </div>
                    <div class="form-hint" style="margin:4px 0 4px;">Тексты лота-образца подтягиваются в редактор — <b>меняй только что нужно</b> (например ссылку). У остальных лотов заменится тот же кусок, их собственный текст сохранится (небольшие отличия понимаются). Печатаешь — добавленное <span class="be-ins">зелёным</span>, удалённое <span class="be-del">красным</span>. Цена, тип, наличие и автовыдача лотов не трогаются.</div>

                    <div id="beChips" class="be-chips"></div>

                    <div class="be-editor">
                        <div class="be-tabs">
                            <button class="be-tab" id="be_tab_summary" onclick="beTab('summary')">Краткое описание<span class="be-dot" id="be_dot_summary"></span></button>
                            <button class="be-tab active" id="be_tab_desc" onclick="beTab('desc')">📖 Подробное описание<span class="be-dot" id="be_dot_desc"></span></button>
                            <button class="be-tab" id="be_tab_payment" onclick="beTab('payment')">✉️ Автоответ после покупки<span class="be-dot" id="be_dot_payment"></span></button>
                            <label class="be-usecheck"><input type="checkbox" id="be_use" onchange="beUseToggle()"> Изменять это поле</label>
                        </div>

                        <textarea id="be_summary" rows="2" style="display:none;" placeholder="Новое краткое описание для всех выбранных лотов (видно в таблице)" oninput="beInput('summary')"></textarea>
                        <textarea id="be_desc" rows="10" placeholder="Новое подробное описание для всех выбранных лотов — вставь сюда свою ссылку" oninput="beInput('desc')"></textarea>
                        <textarea id="be_payment" rows="6" style="display:none;" placeholder="Сообщение, которое получит каждый покупатель после оплаты" oninput="beInput('payment')"></textarea>

                        <div class="be-preview" id="bePreview">
                            <div class="be-preview-caption">Живой предпросмотр · сравнение с лотом <b id="be_old_src">—</b></div>
                            <div class="be-preview-row"><span class="be-preview-label">Было</span><span id="be_old_view"><span class="be-muted">—</span></span></div>
                            <div class="be-preview-row" style="margin-bottom:0;"><span class="be-preview-label">Станет</span><span id="be_new_view"><span class="be-muted">—</span></span></div>
                        </div>
                    </div>

                <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center; justify-content:center; margin-top:14px;">
                        <button class="btn btn-success" id="beApplyBtn" onclick="applyBulkEdit()">💾 Сохранить и изменить на FunPay</button>
                        <button class="btn btn-outline" id="beStopBtn" style="display:none;" onclick="beStop()">⏹ Стоп</button>
                        <span id="beStatus" style="color: var(--text-muted); font-size: 13px;"></span>
                    </div>
                    <div id="beProgress" style="display:none; margin-top:10px; height:8px; border-radius:6px; background:#1b1b26; overflow:hidden;">
                        <div id="beProgressBar" style="height:100%; width:0%; background:linear-gradient(90deg,#3b82f6,#22d3ee); transition:width .4s;"></div>
                    </div>
                    <div id="beErrors" style="display:none; margin-top:10px; font-size:12px; color:#fca5a5; white-space:pre-wrap; max-height:120px; overflow-y:auto;"></div>
                </div>

                <div id="lotsStatus" style="display:none; margin:10px 0; padding:10px 14px; border-radius:10px; font-size:13px; border:1px solid var(--border-card); background:#15151e;"></div>

                <div id="lotsList">
                    <div style="text-align: center; padding: 40px; color: var(--text-muted);">Загрузка лотов...</div>
                </div>
            </div>
        </div>

        <!-- TAB CREATE LOTS -->
        <div id="tab-create" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">✨ Создание лотов: 1 → игры → разделы → форма</div>
                    <div style="font-size: 13px; color: var(--text-muted);">
                        Шаг 1 — найдите игру. Шаг 2 — отметьте разделы (можно много). Шаг 3 — заполните форму как на FunPay и нажмите «Выставить».
                    </div>
                </div>

                <!-- STEP 1: API KEY CHECK -->
                <div class="form-group" style="background:#15151e;border:1px solid var(--border-card);border-radius:10px;padding:14px;">
                    <label>🔑 Шаг 1. API-ключ (golden_key) — проверка, что ключ рабочий</label>
                    <div style="display: flex; gap: 10px;">
                        <input type="password" class="form-control" id="createApiKey" placeholder="Вставьте golden_key с FunPay">
                        <button class="btn" onclick="checkApiKeyForCreate()">✅ Проверить ключ</button>
                    </div>
                    <div class="form-hint"><span id="createApiStatus" style="font-size: 12px;">Ключ подтянется из настроек. Проверка покажет ваш ник и ID.</span></div>
                </div>

                <!-- STEP 2: GAMES -->
                <div class="form-group" style="margin-top:16px;">
                    <label>🎮 Шаг 2. Выберите игры (можно несколько — разделы суммируются)</label>
                    <div class="lots-controls">
                        <input type="text" class="search-input" id="gameSearch" placeholder="🔍 Поиск игры: роблокс, minecraft, steam..." oninput="filterGames()">
                        <button class="btn btn-outline" onclick="loadGames(true)">🔄 Обновить игры</button>
                    </div>
                    <div id="gamesGrid" class="games-grid">
                        <div style="text-align: center; padding: 30px; color: var(--text-muted);">Загрузка игр...</div>
                    </div>
                </div>

                <!-- STEP 3: CHIPS -->
                <div class="form-group" style="margin-top:8px;">
                    <label>🗂 Шаг 3. Разделы <span id="chipsGameName" style="color:var(--accent-primary);"></span> <span style="font-weight:400;font-size:12px;color:var(--text-muted);">— одинаковые из разных игр склеены в одну карточку (×N), выставится везде</span></label>
                    <div class="lots-controls" style="margin-top:8px;">
                        <button class="btn btn-outline chip-mode-btn" onclick="setChipsMode('single', this)">📦 Одна игра (как было)</button>
                        <button class="btn btn-outline chip-mode-btn active-filter" onclick="setChipsMode('multi', this)">➕ Несколько игр (суммировать)</button>
                    </div>
                    <div class="lots-controls" style="margin-top:8px;">
                        <button class="btn btn-outline chip-filter-btn active-filter" onclick="setChipFilter('all', this)">🔄 Все</button>
                        <button class="btn btn-outline chip-filter-btn" onclick="setChipFilter('accounts', this)">🎭 Аккаунты</button>
                        <button class="btn btn-outline chip-filter-btn" onclick="setChipFilter('currency', this)">💰 Валюта</button>
                        <button class="btn btn-outline chip-filter-btn" onclick="setChipFilter('services', this)">🛠 Услуги</button>
                        <button class="btn btn-outline chip-filter-btn" onclick="setChipFilter('other', this)">📦 Прочее</button>
                        <input type="text" class="search-input" id="chipSearch" placeholder="🔍 Фильтр разделов..." oninput="filterChips()" style="min-width:160px;flex:1;">
                    </div>
                    <div class="lots-controls" style="margin-top:8px;">
                        <button class="btn btn-outline" onclick="selectAllChips()">✅ Выбрать видимые</button>
                        <button class="btn btn-outline" onclick="deselectAllChips()">❌ Убрать все</button>
                        <button class="btn btn-outline" onclick="selectSimilarChips()">🔗 Такие же в других играх</button>
                        <span id="chipsCount" style="margin-left: auto; color: var(--text-muted); font-size: 14px;">Выбрано разделов: 0</span>
                    </div>
                    <div id="chipsGrid" class="chips-grid">
                        <div style="text-align: center; padding: 20px; color: var(--text-muted);">Сначала выберите игру выше (или несколько — включи режим ➕)</div>
                    </div>
                </div>

                <!-- STEP 4: FORM -->
                <div style="margin-top:16px; background:#15151e;border:1px solid var(--border-card);border-radius:10px;padding:16px;">
                    <div style="font-weight:700;margin-bottom:6px;">📝 Шаг 4. Заполните как на FunPay — бот выставит во все выбранные разделы</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">💡 Точный дубликат: во вкладке «Мои лоты» нажми 📋 на лоте — сюда подставятся цена, наличие и автовыдача 1-в-1. Если в разделе нет поля (например, наличия) — лот всё равно выставится.</div>
                    <div class="lots-controls" style="margin:10px 0;">
                        <input type="text" class="form-control" id="textStyleInput" placeholder="🎨 Стиль текста (эмодзи): 🟦🟥🟩🟧" style="max-width:270px;" title="Эмодзи, которыми генератор оформит текст">
                        <button class="btn" style="padding:8px 14px;" onclick="openGenModal()">🤖 ИИ-генератор лота</button>
                        <button class="btn btn-outline" onclick="improveField('bulkShortRu')">✨ Улучшить краткое</button>
                        <button class="btn btn-outline" onclick="improveField('bulkDescRu')">✨ Улучшить описание</button>
                        <button class="btn btn-outline" onclick="translatePair('bulkShortRu','bulkShortEn')">🌐 Краткое → EN</button>
                        <button class="btn btn-outline" onclick="translatePair('bulkDescRu','bulkDescEn')">🌐 Описание → EN</button>
                    </div>
                    <div id="bulkPreview" style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">К выставлению: ничего не выбрано</div>
                    <details id="bulkPreviewDetails" style="font-size:12px;color:var(--text-muted);margin-bottom:12px;display:none;">
                        <summary style="cursor:pointer;">Показать все выбранные разделы</summary>
                        <div id="bulkPreviewFull" style="margin-top:6px;line-height:1.7;"></div>
                    </details>
                    <div class="lots-controls" style="margin-top:4px;">
                        <button class="btn btn-outline slot-tab active-filter" id="slotTab0" onclick="switchSlot(0)">Лот 1</button>
                        <button class="btn btn-outline slot-tab" id="slotTab1" onclick="switchSlot(1)">Лот 2</button>
                        <button class="btn btn-outline slot-tab" id="slotTab2" onclick="switchSlot(2)">Лот 3</button>
                        <span style="font-size:12px;color:var(--text-muted);">Можно заполнить до 3 лотов — выставятся все заполненные (●). Дубликат подставляет в открытый слот.</span>
                    </div>
                    <div class="form-grid">
                        <div class="form-group">
                            <label>🏷 Тип предложения * (как на FunPay)</label>
                            <select class="form-control" id="bulkOfferType" onchange="offerTypeManual=true">
                                <option value="" selected>🤖 Авто (подобрать под раздел)</option>
                                <option value="Аккаунты">Аккаунты</option>
                                <option value="Предметы">Предметы</option>
                                <option value="Услуги">Услуги</option>
                                <option value="Гайды">Гайды</option>
                                <option value="Gamepass">Gamepass</option>
                                <option value="Прочее">Прочее</option>
                                <option value="Коды">Коды</option>
                                <option value="Баксы">Баксы</option>
                                <option value="Продажа">Продажа</option>
                                <option value="Аренда">Аренда</option>
                                <option value="VIP-сервер">VIP-сервер</option>
                                <option value="Аватарки">Аватарки</option>
                            </select>
                            <div class="form-hint">Авто подберёт тип под каждый раздел по названию лота (гайд — только в гайды). Можно выбрать вручную — тогда несовпавшие разделы пропустятся с пояснением.</div>
                        </div>
                        <div class="form-group">
                            <label>Цена для покупателя, ₽ *</label>
                            <input type="number" step="0.01" min="0.01" class="form-control" id="bulkPrice" placeholder="Например: 99.99" oninput="bulkExtra.seller_price=0;updateSlotTabs()">
                            <div class="form-hint">Бот сам пересчитает «чистыми» с комиссией FunPay (~22%). При дублировании цена точная 1-в-1, пока не поменяешь вручную.</div>
                        </div>
                        <div class="form-group">
                            <label>Количество</label>
                            <input type="number" min="1" class="form-control" id="bulkAmount" value="1">
                        </div>
                        <div class="form-group">
                            <label>Сервер (если нужен)</label>
                            <input type="text" class="form-control" id="bulkServer" placeholder="Необязательно">
                        </div>
                    </div>
                    <div class="form-grid">
                        <div class="form-group">
                            <label>🇷🇺 Краткое описание RU * (видно в таблице)</label>
                            <input type="text" class="form-control" id="bulkShortRu" placeholder="Короткое название для таблицы" oninput="updateSlotTabs();suggestOfferType()">
                        </div>
                        <div class="form-group">
                            <label>🇬🇧 Short description EN</label>
                            <input type="text" class="form-control" id="bulkShortEn" placeholder="Short title for the table">
                        </div>
                        <div class="form-group">
                            <label>🇷🇺 Подробное описание RU</label>
                            <textarea class="form-control" id="bulkDescRu" rows="2" placeholder="Подробно про товар (можно не заполнять)"></textarea>
                        </div>
                        <div class="form-group">
                            <label>🇬🇧 Detailed description EN</label>
                            <textarea class="form-control" id="bulkDescEn" rows="2" placeholder="Detailed description (optional)"></textarea>
                        </div>
                        <div class="form-group">
                            <label>✉️ Сообщение после оплаты RU</label>
                            <textarea class="form-control" id="bulkPayRu" rows="2" placeholder="Получит каждый покупатель (можно не заполнять)"></textarea>
                        </div>
                        <div class="form-group">
                            <label>✉️ Message after payment EN</label>
                            <textarea class="form-control" id="bulkPayEn" rows="2" placeholder="Will be sent to each buyer (optional)"></textarea>
                        </div>
                        <div class="form-group">
                            <label>🔑 Секреты автовыдачи (необязательно)</label>
                            <textarea class="form-control" id="bulkSecrets" rows="2" placeholder="Коды/ключи, каждый с новой строки — включат автовыдачу"></textarea>
                            <div class="form-hint">Если заполнить — включится автовыдача секретов после оплаты.</div>
                        </div>
                    </div>
                    <div class="lots-controls" style="margin-top:12px;">
                        <select class="form-control" id="bulkLotSelect" style="max-width:300px;" onchange="if(this.value)duplicateLot(this.value)">
                            <option value="">📋 Выбрать свой лот для выставления...</option>
                        </select>
                        <label style="font-size:13px;display:flex;gap:8px;align-items:center;color:var(--text-muted);">
                            <input type="checkbox" id="bulkSkipExisting" checked> ⏭ Пропускать разделы, где лот уже выставлен
                        </label>
                        <label style="font-size:13px;display:flex;gap:8px;align-items:center;color:var(--text-muted);">
                            <input type="checkbox" id="bulkAutoClean" checked> 🧹 При выставлении удалять выключенные копии
                        </label>
                        <button class="btn btn-success" onclick="createBulkLots()">🚀 Выставить во все выбранные разделы</button>
                        <button class="btn btn-outline" onclick="bulkStop()">⏹ Стоп</button>
                        <span id="bulkStatus" style="color: var(--text-muted); font-size: 13px;"></span>
                    </div>
                </div>

                <!-- LEGACY: offers preview (оставлен для совместимости) -->
                <details style="margin-top:16px;">
                    <summary style="cursor:pointer;color:var(--text-muted);font-size:13px;">Показать товары конкурентов в разделе (необязательно)</summary>
                    <div class="lots-controls" style="margin-top: 12px;">
                        <button class="btn btn-outline" onclick="selectAllOffers()">✅ Выбрать все товары</button>
                        <button class="btn btn-outline" onclick="deselectAllOffers()">❌ Убрать все</button>
                        <span id="selectedCount" style="margin-left: auto; color: var(--text-muted); font-size: 14px;">Выбрано: 0</span>
                    </div>
                    <div id="offersGrid" class="offers-grid"></div>
                </details>
            </div>
        </div>

        <!-- TAB 2: SETTINGS -->
    <div id="tab-orders" class="tab-pane">
        <div class="card">
            <div class="card-header">
                <div class="card-title">💬 Авто-заказы: статус</div>
                <button class="btn btn-outline" onclick="loadOrdersTab()">↻ Обновить</button>
            </div>
            <div id="ordersStatusBox" style="font-size: 13px; color: var(--text-muted);">Загрузка...</div>
            <div id="ordersTableBox" style="margin-top: 12px; max-height: 300px; overflow-y: auto;"></div>
            <div class="form-hint">Вотчер работает, пока запущен бот (▶ Старт в шапке). Первый проход тихо подхватывает уже висящие заказы и чаты — старым покупателям сообщения задним числом не шлются. Включайте тумблеры сообщений в карточках ниже.</div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">👋 АВТО-ПРИВЕТСТВИЕ ДЛЯ НОВЫХ ПОКУПАТЕЛЕЙ</div>
            </div>
            <div class="switch-row">
                <label class="switch"><input type="checkbox" id="ord_greet_en"><span class="slider"></span></label>
                <span class="switch-label-text">Приветствовать новых покупателей в чате</span>
            </div>
            <textarea class="form-control" id="ord_greet" rows="5" style="margin-top: 10px;" placeholder="Приветствую вас в моём магазине!..."></textarea>
            <div class="switch-row" style="margin-top: 10px;">
                <label class="switch"><input type="checkbox" id="ord_greet_new_only"><span class="slider"></span></label>
                <span class="switch-label-text">Только совсем новые чаты</span>
            </div>
            <div class="switch-row" style="margin-top: 6px;">
                <label class="switch"><input type="checkbox" id="ord_greet_skip_system"><span class="slider"></span></label>
                <span class="switch-label-text">Не приветствовать при системных сообщениях (заказы, отзывы)</span>
            </div>
            <div class="form-hint">Приветствие отправляется один раз в чат. Старые чаты, которые были до запуска бота, не приветствуются задним числом. Переменная: {buyername}.</div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">🛒 ОТВЕТ НА НОВЫЙ ЗАКАЗ</div>
            </div>
            <div class="switch-row">
                <label class="switch"><input type="checkbox" id="ord_new_order_en"><span class="slider"></span></label>
                <span class="switch-label-text">Отправлять сообщение, когда покупатель оплачивает заказ</span>
            </div>
            <textarea class="form-control" id="ord_new_order" rows="4" style="margin-top: 10px;" placeholder="Ваше сообщение после покупки..."></textarea>
            <div class="form-hint">Переменные: {buyername}, {orderid}, {lotname}, {orderlink}.</div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">✅ ОТВЕТ ПРИ ПОДТВЕРЖДЕНИИ ЗАКАЗА</div>
            </div>
            <div class="switch-row">
                <label class="switch"><input type="checkbox" id="ord_confirm_en"><span class="slider"></span></label>
                <span class="switch-label-text">Отправлять сообщение при подтверждении заказа покупателем</span>
            </div>
            <textarea class="form-control" id="ord_confirm" rows="4" style="margin-top: 10px;" placeholder="Ваше сообщение после подтверждения..."></textarea>
            <div class="form-hint">Переменные: {buyername}, {orderid}, {lotname}, {orderlink}.</div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">⭐ ОТВЕТ НА ОТЗЫВ</div>
            </div>
            <div class="switch-row">
                <label class="switch"><input type="checkbox" id="ord_review_en"><span class="slider"></span></label>
                <span class="switch-label-text">Отвечать на отзыв покупателя</span>
            </div>
            <textarea class="form-control" id="ord_review5" rows="3" style="margin-top: 10px;" placeholder="Ответ на отзыв с 5 звёздами"></textarea>
            <textarea class="form-control" id="ord_review_other" rows="3" style="margin-top: 8px;" placeholder="Ответ на остальные оценки"></textarea>
            <div class="form-hint">Переменные: {buyername}, {orderid}, {lotname}, {stars}.</div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">🤖 Авто-ответы по ключевым словам</div>
            </div>
            <div class="switch-row">
                <label class="switch"><input type="checkbox" id="ord_reply_en"><span class="slider"></span></label>
                <span class="switch-label-text">Отвечать на сообщения покупателей по правилам ниже</span>
            </div>
            <div id="rulesList" style="display:flex;flex-direction:column;gap:8px;margin-top:12px;"></div>
            <div id="rulesEmpty" style="font-size:13px;color:var(--text-muted);margin-top:12px;">Правил пока нет — добавьте первое ниже.</div>

            <div style="border-top:1px solid var(--border-card);margin-top:14px;padding-top:14px;">
                <div class="form-group">
                    <label>Ключевое слово или фраза</label>
                    <input type="text" class="form-control" id="rule_keyword" placeholder="Например: помощь" style="max-width:420px;">
                </div>
                <div class="form-group">
                    <label>Тип совпадения</label>
                    <div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:4px;">
                        <label style="display:flex;align-items:center;gap:7px;cursor:pointer;font-size:13px;">
                            <input type="radio" name="ruleMatch" value="exact"> Точное совпадение
                        </label>
                        <label style="display:flex;align-items:center;gap:7px;cursor:pointer;font-size:13px;">
                            <input type="radio" name="ruleMatch" value="contains" checked> Содержит
                        </label>
                    </div>
                    <div class="form-hint">«Точное совпадение» — сообщение целиком равно ключевому слову (знаки !?., не учитываются). «Содержит» — слово встречается в любом месте сообщения.</div>
                </div>
                <div class="form-group">
                    <label>Текст ответа (можно использовать {buyername})</label>
                    <textarea class="form-control" id="rule_reply" rows="3" placeholder="Что должен ответить бот"></textarea>
                </div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;">
                    <button class="btn" id="ruleSaveBtn" onclick="addRule()">➕ Добавить правило</button>
                    <button class="btn btn-outline" id="ruleCancelBtn" onclick="cancelRuleEdit()" style="display:none;">✖ Отмена</button>
                </div>
            </div>

            <div class="form-group" style="margin-top:14px;">
                <label>Пауза между ответами в одном чате (сек)</label>
                <input type="number" class="form-control" id="ord_reply_cooldown" value="20" style="max-width:140px;">
                <div class="form-hint">Правила сохраняются автоматически после «Добавить правило». Переменные в ответах: {buyername}, {orderid}, {lotname}.</div>
            </div>
        </div>

        <div style="text-align:center;margin:4px 0 24px;">
            <button class="btn" onclick="saveSettings()">💾 Сохранить всё</button>
        </div>
    </div>

        <div id="tab-settings" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">⏱ Настройки интервалов и защиты от антифрода</div>
                </div>

                <div class="form-grid">
                    <div class="form-group">
                        <label>Режим плавающего интервала (Anti-Detect)</label>
                        <div class="switch-row">
                            <label class="switch">
                                <input type="checkbox" id="cfg_random_mode" onchange="toggleRandomModeInputs()">
                                <span class="slider"></span>
                            </label>
                            <span id="randomModeLabel" class="switch-label-text" style="font-weight: 600; color: #34d399;">Включен (2–5 мин)</span>
                        </div>
                        <div class="form-hint">Случайные задержки между проверками для защиты от обнаружения.</div>
                    </div>

                    <div class="form-group" id="randomRangeBox">
                        <label>Диапазон плавающего интервала (мин / макс секунд)</label>
                        <div style="display: flex; gap: 10px;">
                            <input type="number" class="form-control" id="cfg_min_interval" placeholder="Мин (сек)" value="120">
                            <input type="number" class="form-control" id="cfg_max_interval" placeholder="Макс (сек)" value="300">
                        </div>
                        <div class="form-hint">По умолчанию: от 120 (2 мин) до 300 (5 мин) секунд.</div>
                    </div>

                    <div class="form-group" id="fixedRangeBox" style="display: none;">
                        <label>Фиксированный интервал проверки (секунды)</label>
                        <input type="number" class="form-control" id="cfg_fixed_interval" value="120">
                        <div class="form-hint">Бот будет проверять рынок строго через указанное число секунд.</div>
                    </div>

                    <div class="form-group">
                        <label>Человеческие паузы перед изменением лота (сек)</label>
                        <div style="display: flex; gap: 10px;">
                            <input type="number" step="0.5" class="form-control" id="cfg_edit_delay_min" value="2.0">
                            <input type="number" step="0.5" class="form-control" id="cfg_edit_delay_max" value="5.0">
                        </div>
                        <div class="form-hint">Случайная задержка перед нажатием кнопки сохранения.</div>
                    </div>

                    <div class="form-group">
                        <label>Автоподнятие лотов (Lots Auto-Raise)</label>
                        <div class="switch-row">
                            <label class="switch">
                                <input type="checkbox" id="cfg_auto_raise">
                                <span class="slider"></span>
                            </label>
                            <span class="switch-label-text">Автоподнятие ВСЕХ предложений после кулдауна</span>
                        </div>
                        <div class="form-hint">Автоматически нажимает «Поднять предложения» во ВСЕХ ваших разделах (lots + chips).</div>
                        <div style="display: flex; gap: 10px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
                            <button class="btn" onclick="raiseAllNow()">🚀 Поднять все сейчас</button>
                            <span id="raiseStatus" style="font-size: 12px; color: var(--text-muted);">Статус: —</span>
                        </div>
                    </div>

                    <div class="form-group">
                        <label>Вечный Онлайн (Auto-Online 24/7)</label>
                        <div class="switch-row">
                            <label class="switch">
                                <input type="checkbox" id="cfg_auto_online" checked>
                                <span class="slider"></span>
                            </label>
                            <span class="switch-label-text">Поддерживать статус «Онлайн» 24/7</span>
                        </div>
                        <div class="form-hint">Лоты всегда видны покупателям с фильтром «Только онлайн».</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">📱 Настройки Telegram Уведомлений</div>
                </div>

                <div class="form-grid">
                    <div class="form-group">
                        <label>Уведомления в Telegram</label>
                        <div class="switch-row">
                            <label class="switch">
                                <input type="checkbox" id="cfg_tg_enabled" onchange="toggleTelegramInputs()">
                                <span class="slider"></span>
                            </label>
                            <span class="switch-label-text">Отправка сводок и тревожных алертов</span>
                        </div>
                    </div>

                    <div class="form-group">
                        <label>Токен бота (Bot Token)</label>
                        <div style="display: flex; gap: 10px;">
                            <input type="text" class="form-control" id="cfg_tg_token" placeholder="123456:ABC-DEF...">
                            <button class="btn" onclick="testTelegramToken()">✅ Проверить</button>
                        </div>
                        <div class="form-hint"><span id="tgTokenStatus" style="font-size: 12px;">Ожидание...</span></div>
                    </div>

                    <div class="form-group">
                        <label>ID вашего чата (Chat ID)</label>
                        <div style="display: flex; gap: 10px;">
                            <input type="text" class="form-control" id="cfg_tg_chat_id" placeholder="5638558209">
                            <button class="btn" onclick="testTelegramConnection()">✅ Проверить</button>
                        </div>
                        <div class="form-hint"><span id="tgConnectionStatus" style="font-size: 12px;">Ожидание...</span></div>
                    </div>

                    <div class="form-group">
                        <label>HTTP/SOCKS5 Прокси для Telegram (опционально)</label>
                        <input type="text" class="form-control" id="cfg_tg_proxy" placeholder="http://user:pass@ip:port">
                        <div class="form-hint">Используйте, если api.telegram.org блокируется провайдером.</div>
                    </div>
                </div>

                <div style="margin-top: 10px;">
                    <button class="btn btn-outline" onclick="testTelegramFull()">📩 Отправить тестовое сообщение в Telegram</button>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">🔑 Авторизация FunPay (Golden Key)</div>
                </div>
                <div class="form-group">
                    <label>Cookie Golden Key</label>
                    <div style="display: flex; gap: 10px;">
                        <input type="password" class="form-control" id="cfg_golden_key" placeholder="9hy8kbfj3vgl14gwj0swlrdyete9pggf">
                        <button class="btn" onclick="testGoldenKey()">✅ Проверить и сохранить</button>
                    </div>
                    <div class="form-hint"><span id="goldenKeyStatus" style="font-size: 12px;">Вставьте ключ с FunPay — проверим, что он рабочий, и покажем ваш ник.</span></div>
                </div>
            </div>
        </div>

        <!-- TAB 4: LOGS -->
        <div id="tab-logs" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📜 Логи работы бота в реальном времени</div>
                    <button class="btn btn-outline" onclick="loadLogs()">🔄 Обновить логи</button>
                </div>
                <div class="logs-box" id="logsConsole">Загрузка логов...</div>
            </div>
        </div>

        <!-- TEMPLATES TAB -->
        <div id="tab-templates" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📋 Шаблоны лотов</div>
                    <div style="font-size: 13px; color: var(--text-muted);">
                        Шаблон — это слепок лота: название, цены, описания, автовыдача. Хранится на диске и
                        <b>переживает выход из аккаунта</b>. Сохрани лот кнопкой 📥 во вкладке «Мои лоты»,
                        а потом в один клик выстави его заново в любом разделе.
                    </div>
                </div>

                <div class="lots-controls" style="margin-top:10px;">
                    <input type="text" class="search-input" id="templateName" placeholder="Название нового шаблона (например: Основной лот)">
                    <button class="btn btn-outline" onclick="saveFormAsTemplate()">💾 Из заполненной формы</button>
                    <button class="btn btn-outline" onclick="toggleLotPicker()">📥 Из моего лота ▾</button>
                    <button class="btn btn-outline" onclick="loadTemplates(true)">🔄 Обновить</button>
                </div>
                <div id="lotPicker" style="display:none; margin:8px 0 14px; background:#15151e; border:1px solid var(--border-card); border-radius:10px; padding:12px;">
                    <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">
                        Выбери любой свой лот — он сохранится как шаблон целиком: название, цены, описания RU/EN, автовыдача, секреты.
                    </div>
                    <div class="lots-controls" style="margin:0;">
                        <select id="templateLotSelect" class="form-control" style="flex:1;min-width:240px;">
                            <option value="">Загрузка лотов...</option>
                        </select>
                        <button class="btn" onclick="saveLotFromPicker()">💾 Сохранить как шаблон</button>
                    </div>
                </div>
                <div style="font-size:12px;color:var(--text-muted);margin:6px 0 14px;">
                    «Из заполненной формы» — то, что сейчас стоит в полях на вкладке «✨ Создать лоты». Нажми 👁 на шаблоне — увидишь всё его содержимое.
                </div>

                <div id="templatesGrid" style="display:flex;flex-direction:column;gap:10px;">
                    <div style="text-align:center;padding:24px;color:var(--text-muted);">Загрузка шаблонов...</div>
                </div>
            </div>
        </div>

        <!-- TEXT TAB -->
        <div id="tab-text" class="tab-pane">
            <div class="card">
                <h3>📝 Шаблоны текста</h3>
                <p style="color:var(--text-muted);margin-bottom:16px;">Быстрые шаблоны для авто-заказов, приветствий, выдачи и отзывов.</p>
                <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
                    <select id="textTemplateSelect" class="form-control" style="flex:1;min-width:200px;">
                        <option value="">Выберите шаблон...</option>
                    </select>
                    <button class="btn btn-outline" onclick="loadTextTemplate()">📥 Загрузить</button>
                    <button class="btn btn-outline" onclick="saveAsTextTemplate()">💾 Сохранить</button>
                    <button class="btn btn-outline" onclick="deleteTextTemplate()">🗑 Удалить</button>
                </div>
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="textTemplateName" class="form-control" placeholder="Например: Приветствие">
                </div>
                <div class="form-group">
                    <label>Тип</label>
                    <select id="textTemplateType" class="form-control">
                        <option value="greeting">Приветствие</option>
                        <option value="delivery">Выдача</option>
                        <option value="autoreply">Автоответ</option>
                        <option value="thanks">Благодарность</option>
                        <option value="custom">Свой текст</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Текст шаблона</label>
                    <textarea id="textTemplateContent" class="form-control" rows="6" placeholder="Переменные: {lot}, {order}, {buyer}"></textarea>
                </div>
            </div>
        </div>

    </main>

    <!-- MOBILE ACCESS MODAL -->
    <div id="phoneModal" class="modal-overlay" onclick="closePhoneModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div style="font-family: var(--font-title); font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 8px;">
                    📱 <span>Доступ с телефона</span>
                </div>
                <button class="btn-close" onclick="closePhoneModal()">✕</button>
            </div>
            <div style="text-align: center; margin: 18px 0;">
                <div style="font-size: 13px; color: var(--text-muted); line-height: 1.5; margin-bottom: 14px;">
                    1. Подключите телефон к <b>тому же Wi-Fi роутеру</b>.<br>
                    2. Отсканируйте QR-код камерой или перейдите по ссылке:
                </div>
                <div style="display: inline-block; background: white; padding: 12px; border-radius: 14px; box-shadow: 0 6px 20px rgba(0,0,0,0.35);">
                    <img id="qrCodeImg" src="" width="160" height="160" alt="QR Code" style="display: block; border-radius: 6px;">
                </div>
                <div style="margin-top: 14px;">
                    <a id="phoneLinkText" href="#" target="_blank" style="font-family: monospace; font-size: 16px; color: #38bdf8; font-weight: 700; text-decoration: underline;">Загрузка адреса...</a>
                </div>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn" style="flex: 1; justify-content: center;" onclick="copyPhoneLink()">📋 Скопировать ссылку</button>
                <button class="btn btn-outline" onclick="closePhoneModal()">Закрыть</button>
            </div>
        </div>
    </div>

    <!-- AI LOT GENERATOR MODAL -->
    <div id="genModal" class="modal-overlay" onclick="closeGenModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()" style="max-width:560px;">
            <div class="modal-header">
                <div style="font-family: var(--font-title); font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 8px;">
                    🤖 <span>ИИ-генератор лотов</span>
                </div>
                <button class="btn-close" onclick="closeGenModal()">✕</button>
            </div>
            <div style="margin: 14px 0;">
                <div style="font-size: 13px; color: var(--text-muted); line-height: 1.5; margin-bottom: 14px;">
                    Составит краткое описание (заголовок) и подробное описание в твоём стиле.
                    Стиль задаётся эмодзи — например 🟦🟥🟩🟧 — текст будет построен на этих значках.
                </div>
                <div class="form-group">
                    <label>🎨 Стиль оформления (эмодзи) — заголовок и пункты построятся на них</label>
                    <input type="text" class="form-control" id="genStyle" placeholder="Например: 🟦🟥🟩🟧">
                    <div class="lots-controls" style="margin-top:6px; gap:6px;">
                        <button class="btn btn-outline" style="padding:4px 10px;font-size:12px;" onclick="document.getElementById('genStyle').value='🔥⚡️💎'">🔥⚡️💎</button>
                        <button class="btn btn-outline" style="padding:4px 10px;font-size:12px;" onclick="document.getElementById('genStyle').value='🟦🟥🟩🟧'">🟦🟥🟩🟧</button>
                        <button class="btn btn-outline" style="padding:4px 10px;font-size:12px;" onclick="document.getElementById('genStyle').value='✅📌🛒'">✅📌🛒</button>
                        <button class="btn btn-outline" style="padding:4px 10px;font-size:12px;" onclick="document.getElementById('genStyle').value='💜💙💚'">💜💙💚</button>
                        <span style="font-size:11px;color:var(--text-muted);align-self:center;">или впиши свои</span>
                    </div>
                </div>
                <div class="form-group">
                    <label>Что продаём? (краткая идея для заголовка)</label>
                    <input type="text" class="form-control" id="genIdea" placeholder="Например: Пак аватарок на тему аниме">
                </div>
                <div class="form-group">
                    <label>О чём написать в описании? (ключевые особенности)</label>
                    <textarea class="form-control" id="genFeatures" rows="3" placeholder="Каждая фишка с новой строки (или сплошняком — сам разобьёт):&#10;аккаунт личный, даю доступ логин пароль почта, я никуда не лезу, автовыдача"></textarea>
                    <div class="form-hint">Частые слова чинятся сами: акаунт → аккаунт, стим → Steam и т.д.</div>
                </div>
                <div class="form-group">
                    <label>🏷 Тематика товара (от неё зависят секции «ВАЖНО»)</label>
                    <select class="form-control" id="genCategory">
                        <option value="Аккаунты">Аккаунты</option>
                        <option value="Ключи и гифты">Ключи и гифты</option>
                        <option value="Валюта и предметы">Валюта и предметы</option>
                        <option value="Услуги">Услуги</option>
                        <option value="Другое" selected>Другое</option>
                    </select>
                </div>
                <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:8px;">
                    <input type="checkbox" id="genBuyerMsg"> Сгенерировать сообщение для покупателя
                </label>
                <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:12px;">
                    <input type="checkbox" id="genTranslate" checked> Автоматически перевести на английский
                </label>
                <div id="genStatus" style="font-size:12px;color:var(--text-muted);margin-bottom:10px;"></div>
                <div style="display:flex;gap:10px;">
                    <button class="btn" id="genBtn" style="flex:1;justify-content:center;" onclick="runGenerator()">✨ Сгенерировать</button>
                    <button class="btn btn-outline" onclick="closeGenModal()">Закрыть</button>
                </div>
            </div>
        </div>
    </div>

    <div id="toast">Настройки успешно сохранены!</div>

    <script>
        // ═══ Гейт доступа: пароль панели (для хостинга) ═══
        // Если API отвечает 401 — панель спрашивает пароль (PANEL_PASSWORD
        // на Render) и дальше подставляет его во все запросы сама.
        (function () {
            const origFetch = window.fetch.bind(window);
            window.fpToken = () => localStorage.getItem('fp_token') || '';
            window.fetch = function (url, opts) {
                if (typeof url === 'string' && url.startsWith('/api/') && window.fpToken()) {
                    url += (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(window.fpToken());
                }
                return origFetch(url, opts);
            };
            window.fpAskPassword = async function () {
                for (let i = 0; i < 5; i++) {
                    const p = prompt('Панель защищена паролем. Введи PANEL_PASSWORD:');
                    if (p === null) return false;
                    try {
                        const r = await origFetch('/api/bot/status?token=' + encodeURIComponent(p));
                        if (r.status === 200) {
                            localStorage.setItem('fp_token', p);
                            location.reload();
                            return true;
                        }
                    } catch (e) { break; }
                }
                return false;
            };
            (async function fpAuthGate() {
                try {
                    const r = await origFetch('/api/bot/status');
                    if (r.status === 401) await window.fpAskPassword();
                } catch (e) { /* локально без сети — не мешаем */ }
            })();
        })();

        let allLots = [];
        let disabledLotsSet = new Set();
        let currentConfig = {};
        let currentPhoneUrl = '';

        function openPhoneModal() {
            document.getElementById('phoneModal').style.display = 'flex';
        }

        function closePhoneModal(e) {
            document.getElementById('phoneModal').style.display = 'none';
        }

        function copyPhoneLink() {
            if (currentPhoneUrl) {
                navigator.clipboard.writeText(currentPhoneUrl).then(() => {
                    showToast('✓ Ссылка скопирована в буфер обмена!');
                }).catch(() => {
                    showToast(currentPhoneUrl);
                });
            }
        }

        function showToast(text, color="#10b981") {
            const t = document.getElementById('toast');
            t.innerText = text;
            t.style.borderColor = color;
            t.style.display = 'block';
            setTimeout(() => { t.style.display = 'none'; }, 3000);
        }

        function switchTab(tabId, btn) {
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));

            document.getElementById(tabId).classList.add('active');
            let el = btn || null;
            if (!el) { try { el = event.currentTarget; } catch (e) { el = null; } }
            if (!el) {
                document.querySelectorAll('.tab-btn').forEach(b => {
                    if ((b.getAttribute('onclick') || '').includes(tabId)) el = b;
                });
            }
            if (el) el.classList.add('active');

            if (tabId === 'tab-logs') {
                loadLogs();
            }
            if (tabId === 'tab-orders') {
                loadOrdersTab();
            }
            if (tabId === 'tab-templates') {
                loadTemplates();
            }
        }

        function toggleRandomModeInputs() {
            const isRandom = document.getElementById('cfg_random_mode').checked;
            document.getElementById('randomRangeBox').style.display = isRandom ? 'block' : 'none';
            document.getElementById('fixedRangeBox').style.display = isRandom ? 'none' : 'block';
            document.getElementById('randomModeLabel').innerText = isRandom ? 'Включен (2–5 мин)' : 'Выключен (Фикс)';
            document.getElementById('randomModeLabel').style.color = isRandom ? '#34d399' : '#94a3b8';
        }

        
        async function loadStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                currentConfig = data.config || {};
                
                document.getElementById('accountName').innerText = data.username ? `${data.username} (ID: ${data.user_id})` : 'FunPay Бот';
                disabledLotsSet = new Set((data.config.disabled_lots || []).map(String));

                // Установка ссылки и QR-кода для телефона
                currentPhoneUrl = data.phone_url || `http://${window.location.hostname}:5000`;
                const phoneLink = document.getElementById('phoneLinkText');
                if (phoneLink) {
                    phoneLink.innerText = currentPhoneUrl;
                    phoneLink.href = currentPhoneUrl;
                }
                const qrImg = document.getElementById('qrCodeImg');
                if (qrImg) {
                    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(currentPhoneUrl)}`;
                }

                // Заполнение настроек
                const s = data.config.settings || {};
                document.getElementById('cfg_random_mode').checked = s.random_interval_mode !== false;
                document.getElementById('cfg_min_interval').value = s.check_interval_min_seconds || 120;
                document.getElementById('cfg_max_interval').value = s.check_interval_max_seconds || 300;
                document.getElementById('cfg_fixed_interval').value = s.check_interval_seconds || 120;
                document.getElementById('cfg_edit_delay_min').value = s.edit_delay_min_seconds || 2.0;
                document.getElementById('cfg_edit_delay_max').value = s.edit_delay_max_seconds || 5.0;
                document.getElementById('cfg_auto_raise').checked = s.auto_raise_lots !== false;
                document.getElementById('cfg_auto_online').checked = s.auto_online !== false;
                

                // Telegram
                const tg = data.config.telegram || {};
                document.getElementById('cfg_tg_enabled').checked = tg.enabled || false;
                document.getElementById('cfg_tg_token').value = tg.bot_token || '';
                document.getElementById('cfg_tg_chat_id').value = tg.chat_id || '';
                document.getElementById('cfg_tg_proxy').value = tg.proxy || '';

                // Авто-заказы
                fillAutoOrders(data.config.auto_orders || {});

                // Golden Key
                document.getElementById('cfg_golden_key').value = data.config.golden_key || '';
                const createKey = document.getElementById('createApiKey');
                if (createKey && !createKey.value) createKey.value = data.config.golden_key || '';

                if (data.auth_ok) {
                    document.getElementById('accountName').innerText = `${data.username} (ID: ${data.user_id})`;
                } else {
                    document.getElementById('accountName').innerText = 'Ключ не задан / неверный';
                    document.getElementById('accountBadge').classList.add('error');
                }

                toggleRandomModeInputs();
                updateStats();
                loadBotStatus();
                loadRaiseStatus();
            } catch (e) {
                console.error('Ошибка загрузки статуса:', e);
            }
        }

        // === BOT START/STOP ===
        async function loadBotStatus() {
            try {
                const res = await fetch('/api/bot/status');
                const data = await res.json();
                const dot = document.getElementById('botDot');
                const txt = document.getElementById('botStatus');
                if (data.running) {
                    dot.classList.add('running');
                    txt.innerText = 'Бот: работает';
                } else {
                    dot.classList.remove('running');
                    txt.innerText = 'Бот: остановлен';
                }
            } catch (e) { /* ignore */ }
        }

        async function botStart() {
            showToast('Запуск бота...', '#3b82f6');
            try {
                const res = await fetch('/api/bot/start', { method: 'POST' });
                const data = await res.json();
                if (data.ok) showToast('✓ ' + (data.message || 'Бот запущен!'));
                else showToast('✗ ' + (data.error || 'Не запустился'), '#ef4444');
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
            setTimeout(loadBotStatus, 1500);
        }

        async function botStop() {
            try {
                const res = await fetch('/api/bot/stop', { method: 'POST' });
                const data = await res.json();
                if (data.ok) showToast('Бот остановлен', '#f59e0b');
                else showToast('✗ ' + (data.error || 'Ошибка'), '#ef4444');
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
            setTimeout(loadBotStatus, 1000);
        }

        // === AUTO-RAISE ALL ===
        async function loadRaiseStatus() {
            try {
                const res = await fetch('/api/raise/status');
                const data = await res.json();
                const el = document.getElementById('raiseStatus');
                if (!el) return;
                if (!data.ok) { el.innerText = 'Статус: —'; return; }
                const nodes = data.nodes || [];
                if (!nodes.length) { el.innerText = 'Статус: разделы пока неизвестны (бот соберёт их при проверке)'; return; }
                const ready = nodes.filter(n => !n.next_in_sec).length;
                el.innerText = `Статус: разделов ${nodes.length}, готовы ${ready}`;
                el.title = nodes.map(n => `${n.node}: ${n.next_at}`).join('\n');
            } catch (e) { /* ignore */ }
        }

        async function raiseAllNow() {
            const el = document.getElementById('raiseStatus');
            if (el) el.innerText = 'Поднимаю все разделы... ⏳ (может занять 1-2 мин)';
            showToast('Поднимаю ВСЕ разделы...', '#3b82f6');
            try {
                const res = await fetch('/api/raise/now', { method: 'POST' });
                const data = await res.json();
                if (data.ok) {
                    if (data.started) {
                        showToast('⏳ ' + (data.message || 'Поднятие запущено в фоне'));
                        if (el) el.innerText = 'В работе... обновлю статус через 10 сек';
                        setTimeout(loadRaiseStatus, 10000);
                    } else {
                        showToast(`✓ Поднято: ${data.raised || 0} из ${data.total || 0} (кулдаун: ${data.cooldown || 0})`);
                        if (el) el.innerText = `Готово: поднято ${data.raised || 0}/${data.total || 0}` + (data.message ? ' — ' + data.message : '');
                    }
                } else {
                    showToast('✗ ' + (data.error || 'Не удалось поднять'), '#ef4444');
                    if (el) el.innerText = 'Ошибка поднятия';
                }
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
            setTimeout(loadRaiseStatus, 2000);
        }
        setInterval(loadRaiseStatus, 30000);

        // === GAMES + CHIPS (FunPay categories browser) ===
        let allGames = [];
        let lastGamesList = [];
        let gamesLimit = 60;
        let gameSearchTimer = null;
        let selectedGames = new Set();
        let chipsMode = 'multi';
        let currentChips = [];
        let selectedChips = new Set();
        let chipTypeFilters = new Set(['all']);
        let chipSearchQuery = '';
        let chipSearchTimer = null;

        const CHIP_FILTERS = {
            all: () => true,
            accounts: c => /аккаунт|account/i.test(c.name || ''),
            currency: c => /робукс|robux|валют|currency|coin|монет|донат|donat|пак|голд|gold|гем|gem|алмаз|crystal|кредит|token/i.test(c.name || ''),
            services: c => /услуг|service|буст|boost|прокач|фарм|farm|рейд|raid|помощь|заказ|промокод|promo|gamepass|геймпасс|подписк|subscription/i.test(c.name || ''),
            other: c => /прочее|other/i.test(c.name || '')
        };

        function gameIcon(name) {
            const n = (name || '').toLowerCase();
            if (n.includes('roblox') || n.includes('роблокс')) return '🧱';
            if (n.includes('minecraft') || n.includes('майнкрафт')) return '⛏️';
            if (n.includes('fortnite') || n.includes('фортнайт')) return '🔫';
            if (n.includes('steam')) return '🎮';
            if (n.includes('valorant') || n.includes('валорант')) return '🎯';
            if (n.includes('genshin') || n.includes('геншин')) return '✨';
            if (n.includes('brawl')) return '⭐';
            if (n.includes('pubg') || n.includes('пабг')) return '🪂';
            if (n.includes('cs') || n.includes('кс ') || n.includes('counter')) return '💣';
            if (n.includes('dota') || n.includes('дота')) return '🛡';
            if (n.includes('league') || n.includes('лига легенд')) return '⚔️';
            if (n.includes('gta')) return '🚗';
            if (n.includes('warcraft') || n.includes('варкрафт')) return '🐉';
            if (n.includes('discord') || n.includes('дискорд')) return '💬';
            if (n.includes('spotify') || n.includes('спотифай')) return '🎵';
            if (n.includes('tiktok') || n.includes('тикток')) return '🎶';
            if (n.includes('telegram')) return '✈️';
            return '🎲';
        }

        async function loadGames(force) {
            const grid = document.getElementById('gamesGrid');
            grid.innerHTML = '<div class="loading-spinner">Загрузка игр...</div>';
            try {
                const data = await fetchJson('/api/games' + (force ? '?refresh=1' : ''), 45000);
                allGames = data.games || [];
                if (!allGames.length) {
                    const msg = data.error || 'Игры не загрузились. Проверь API-ключ выше.';
                    grid.innerHTML = `<div style="text-align:center;padding:30px;">
                        <div style="color:#f59e0b;margin-bottom:10px;">⚠ ${escHtml(msg)}</div>
                        <button class="btn btn-outline" onclick="loadGames(true)">↻ Повторить</button>
                    </div>`;
                    return;
                }
                restoreSel();
                rebuildChips();
                renderGamesGrid(allGames);
            } catch (e) {
                grid.innerHTML = `<div style="text-align:center;padding:30px;">
                    <div style="color:var(--accent-danger);margin-bottom:10px;">⚠ Ошибка загрузки: ${escHtml(String(e))}</div>
                    <button class="btn btn-outline" onclick="loadGames(true)">↻ Повторить</button>
                </div>`;
            }
        }

        function renderGamesGrid(games) {
            lastGamesList = games;
            const grid = document.getElementById('gamesGrid');
            grid.innerHTML = '';
            games.slice(0, gamesLimit).forEach(g => {
                const card = document.createElement('div');
                card.className = 'game-card' + (selectedGames.has(String(g.game_id)) ? ' selected' : '');
                card.innerHTML = `<div class="game-icon">${gameIcon(g.name)}</div>
                    <div class="game-name">${escHtml(g.name)}</div>
                    <div class="game-sub">${(g.chips || []).length} разделов</div>`;
                card.onclick = () => toggleGame(g.game_id);
                grid.appendChild(card);
            });
            if (games.length > gamesLimit) {
                const more = document.createElement('button');
                more.className = 'btn btn-outline';
                more.style.cssText = 'grid-column:1/-1;justify-content:center;';
                more.textContent = `Показать ещё (${games.length - gamesLimit})`;
                more.onclick = () => { gamesLimit += 60; renderGamesGrid(lastGamesList); };
                grid.appendChild(more);
            }
        }

        function filterGames() {
            clearTimeout(gameSearchTimer);
            gameSearchTimer = setTimeout(() => {
                gamesLimit = 60;
                const q = document.getElementById('gameSearch').value.toLowerCase().trim();
                renderGamesGrid(!q ? allGames : allGames.filter(g => (g.name || '').toLowerCase().includes(q)));
            }, 200);
        }

        function setChipsMode(mode, btn) {
            chipsMode = mode;
            document.querySelectorAll('.chip-mode-btn').forEach(b => b.classList.remove('active-filter'));
            if (btn) btn.classList.add('active-filter');
            selectedGames.clear();
            selectedChips.clear();
            currentChips = [];
            offerTypeManual = false;
            document.getElementById('chipsGameName').textContent = '';
            renderGamesGrid(lastGamesList.length ? lastGamesList : allGames);
            renderChipsGrid();
        }

        function toggleGame(gameId) {
            gameId = String(gameId);
            if (chipsMode === 'single') {
                // как было раньше: клик = только эта игра со всеми её разделами
                if (selectedGames.size === 1 && selectedGames.has(gameId)) {
                    selectedGames.clear();
                } else {
                    selectedGames = new Set([gameId]);
                }
            } else {
                // новый режим: игры суммируются
                if (selectedGames.has(gameId)) selectedGames.delete(gameId);
                else selectedGames.add(gameId);
            }
            rebuildChips();
            renderGamesGrid(lastGamesList.length ? lastGamesList : allGames);
            const g = allGames.find(x => String(x.game_id) === gameId);
            if (g && selectedGames.has(gameId)) loadCategoryOffers(g.url || g.node_id);
        }

        function rebuildChips() {
            const seen = new Set();
            const kept = new Set();
            currentChips = [];
            const names = [];
            selectedGames.forEach(gid => {
                const g = allGames.find(x => String(x.game_id) === String(gid));
                if (!g) return;
                names.push(g.name);
                (g.chips || []).forEach(c => {
                    const id = String(c.chip_id);
                    if (seen.has(id)) return;
                    seen.add(id);
                    currentChips.push(Object.assign({}, c, { game_name: g.name }));
                    if (selectedChips.has(id)) kept.add(id);
                });
            });
            selectedChips = kept;
            document.getElementById('chipsGameName').textContent =
                names.length ? '— ' + names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3}` : '') : '';
            renderChipsGrid();
            suggestOfferType();
        }

        function chipMatches(c) {
            // мультивыбор типов: подходит, если совпал ХОТЯ БЫ один включённый тип
            if (!chipTypeFilters.has('all')) {
                let ok = false;
                chipTypeFilters.forEach(f => { if ((CHIP_FILTERS[f] || (() => false))(c)) ok = true; });
                if (!ok) return false;
            }
            if (chipSearchQuery && !((c.name || '').toLowerCase().includes(chipSearchQuery))) return false;
            return true;
        }

        function setChipFilter(f, btn) {
            if (f === 'all') {
                chipTypeFilters = new Set(['all']);
            } else {
                chipTypeFilters.delete('all');
                if (chipTypeFilters.has(f)) chipTypeFilters.delete(f);
                else chipTypeFilters.add(f);
                if (!chipTypeFilters.size) chipTypeFilters.add('all');
            }
            document.querySelectorAll('.chip-filter-btn').forEach(b => {
                const m = (b.getAttribute('onclick') || '').match(/setChipFilter\('(\w+)'/);
                b.classList.toggle('active-filter', !!(m && chipTypeFilters.has(m[1])));
            });
            renderChipsGrid();
        }

        function filterChips() {
            clearTimeout(chipSearchTimer);
            chipSearchTimer = setTimeout(() => {
                chipSearchQuery = document.getElementById('chipSearch').value.toLowerCase().trim();
                renderChipsGrid();
            }, 200);
        }

        let lastShownGroups = 0, lastTotalGroups = 0;

        function chipGroupKey(c) { return (c.name || '').trim().toLowerCase(); }

        function renderChipsGrid() {
            const grid = document.getElementById('chipsGrid');
            const totalGroups = new Set(currentChips.map(chipGroupKey)).size;
            if (!currentChips.length) {
                grid.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">Сначала выберите игру выше (или несколько — включи режим ➕)</div>';
                updateChipsCount(0, 0);
                return;
            }
            const visible = currentChips.filter(chipMatches);
            if (!visible.length) {
                grid.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">По фильтру ничего нет — смените фильтр</div>';
                updateChipsCount(0, totalGroups);
                return;
            }
            // одинаковые названия из разных игр — одна карточка, выставится везде
            const groups = new Map();
            visible.forEach(c => {
                const k = chipGroupKey(c);
                if (!groups.has(k)) groups.set(k, { name: c.name, items: [] });
                groups.get(k).items.push(c);
            });
            grid.innerHTML = '';
            const lotsByNode = {};
            (typeof allLots !== 'undefined' ? allLots : []).forEach(lot => {
                const nid = String(lot.node_id || '');
                if (!nid) return;
                (lotsByNode[nid] = lotsByNode[nid] || []).push(lot);
            });
            groups.forEach(g => {
                const ids = g.items.map(c => String(c.chip_id));
                const allSel = ids.every(id => selectedChips.has(id));
                const games = [...new Set(g.items.map(c => c.game_name || ''))].filter(Boolean);
                const card = document.createElement('div');
                card.className = 'chip-card' + (allSel ? ' selected' : '');
                card.dataset.chipIds = ids.join(',');
                const more = g.items.length > 1 ? ` ×${g.items.length}` : '';
                const occSeen = new Set();
                let occCount = 0;
                g.items.forEach(c => {
                    ((lotsByNode[String(c.node || '')] || [])).forEach(lot => {
                        const lid = String(lot.lot_id);
                        if (!occSeen.has(lid)) { occSeen.add(lid); occCount++; }
                    });
                });
                const occTxt = occCount ? ` • уже есть мои: ${occCount}` : '';
                card.innerHTML = `<div class="check-indicator">✓</div>
                    <div class="chip-name">${escHtml(g.name)}${more}</div>
                    <div class="chip-sub">${escHtml(games.slice(0, 3).join(', ') + (games.length > 3 ? ` +${games.length - 3}` : '') + occTxt)}</div>`;
                card.onclick = () => toggleChipGroup(ids, card);
                grid.appendChild(card);
            });
            updateChipsCount(groups.size, totalGroups);
        }

        function toggleChipGroup(ids, cardEl) {
            const allSel = ids.every(id => selectedChips.has(id));
            ids.forEach(id => { if (allSel) selectedChips.delete(id); else selectedChips.add(id); });
            cardEl.classList.toggle('selected', !allSel);
            updateChipsCount();
        }

        function updateChipsCount(visible, total) {
            if (visible !== undefined) lastShownGroups = visible;
            if (total !== undefined) lastTotalGroups = total;
            document.getElementById('chipsCount').textContent =
                `Выбрано разделов: ${selectedChips.size} (показано ${lastShownGroups} из ${lastTotalGroups})`;
            refreshBulkPreview();
            saveSel();
        }

        function saveSel() {
            try {
                localStorage.setItem('fp_sel_games', JSON.stringify([...selectedGames]));
                localStorage.setItem('fp_sel_chips', JSON.stringify([...selectedChips]));
                localStorage.setItem('fp_chips_mode', chipsMode);
            } catch (e) {}
        }

        function restoreSel() {
            try {
                const g = JSON.parse(localStorage.getItem('fp_sel_games') || '[]');
                const c = JSON.parse(localStorage.getItem('fp_sel_chips') || '[]');
                selectedGames = new Set(g.map(String));
                selectedChips = new Set(c.map(String));
                const m = localStorage.getItem('fp_chips_mode');
                if (m === 'single' || m === 'multi') {
                    chipsMode = m;
                    document.querySelectorAll('.chip-mode-btn').forEach(b => {
                        const mm = (b.getAttribute('onclick') || '').match(/setChipsMode\('(\w+)'/);
                        b.classList.toggle('active-filter', !!(mm && mm[1] === m));
                    });
                }
            } catch (e) {}
        }

        function refreshBulkPreview() {
            const el = document.getElementById('bulkPreview');
            const full = document.getElementById('bulkPreviewFull');
            const det = document.getElementById('bulkPreviewDetails');
            const sel = currentChips.filter(c => selectedChips.has(String(c.chip_id)));
            if (!sel.length) {
                if (el) el.textContent = 'К выставлению: ничего не выбрано';
                if (full) full.textContent = '';
                if (det) det.style.display = 'none';
                return;
            }
            const names = sel.slice(0, 8).map(c => `${c.name} (${c.game_name || ''})`);
            const occNodes = new Set((typeof allLots !== 'undefined' ? allLots : []).map(l => String(l.node_id || '')));
            const occSel = sel.filter(c => occNodes.has(String(c.node || ''))).length;
            if (el) el.textContent = `К выставлению: ${sel.length} разделов (уже есть мои: ${occSel}) — ` + names.join(' • ') + (sel.length > 8 ? ` +${sel.length - 8} (все — ниже)` : '');
            if (full) full.textContent = sel.map(c => `${c.name} (${c.game_name || ''})`).join(' • ');
            if (det) {
                det.style.display = 'block';
                const sum = det.querySelector('summary');
                if (sum) sum.textContent = `Показать все выбранные разделы (${sel.length})`;
            }
        }

        function normName(s) {
            return (s || '').toLowerCase().replace(/[^a-zа-яё0-9]/gi, '').replace(/(ы|и|es|s)$/, '');
        }

        function namesSimilar(a, b) {
            const x = normName(a), y = normName(b);
            if (!x || !y) return false;
            if (x === y) return true;
            if (x.length < 4 || y.length < 4) return false;
            return x.includes(y) || y.includes(x);
        }

        // Выбрал «Промокоды» в одной игре — подберёт «Промокод/Промокоды/Gamepass» в остальных.
        // Где такого нет — просто пропускает.
        function selectSimilarChips() {
            if (!selectedChips.size) { showToast('Сначала выбери хотя бы один раздел', '#f59e0b'); return; }
            const base = currentChips.filter(c => selectedChips.has(String(c.chip_id)));
            let added = 0;
            currentChips.forEach(c => {
                const id = String(c.chip_id);
                if (selectedChips.has(id)) return;
                if (base.some(b => namesSimilar(b.name, c.name))) { selectedChips.add(id); added++; }
            });
            renderChipsGrid();
            if (added) showToast(`✓ Подобрано таких же в других играх: ${added}`, '#10b981');
            else showToast('Таких же в других играх нет — пропущено', '#f59e0b');
        }

        function selectAllChips() {
            document.querySelectorAll('#chipsGrid .chip-card').forEach(card => {
                const ids = (card.dataset.chipIds || card.dataset.chipId || '').split(',').filter(Boolean);
                ids.forEach(id => selectedChips.add(String(id)));
                card.classList.add('selected');
            });
            updateChipsCount();
        }

        function deselectAllChips() {
            selectedChips.clear();
            document.querySelectorAll('#chipsGrid .chip-card.selected').forEach(c => c.classList.remove('selected'));
            updateChipsCount();
        }

        async function bulkStop() {
            try {
                const res = await fetch('/api/lots/bulk-stop', { method: 'POST' });
                const data = await res.json();
                showToast(data.ok ? '⏹ ' + data.message : '✗ ' + (data.error || 'Не запущено'), data.ok ? '#f59e0b' : '#ef4444');
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
        }

        let dupeGroups = [];

        async function scanDuplicates() {
            const res = await fetch('/api/lots/duplicates');
            const data = await res.json();
            if (!data.ok) { showToast('✗ ' + (data.error || 'Ошибка'), '#ef4444'); return null; }
            dupeGroups = data.groups || [];
            return data;
        }

        async function findDuplicates() {
            showToast('Ищу дубликаты (одинаковый раздел + название)...', '#3b82f6');
            try {
                const data = await scanDuplicates();
                if (!data) return;
                if (!dupeGroups.length) { showToast('✓ Дубликатов нет', '#10b981'); return; }
                showToast(`Найдено групп: ${dupeGroups.length}, лишних лотов: ${data.extra_count}. Жми «Убрать дубликаты».`, '#f59e0b');
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
        }

        async function cleanupDuplicates() {
            try {
                let data = null;
                if (!dupeGroups.length) {
                    showToast('Ищу дубликаты...', '#3b82f6');
                    data = await scanDuplicates();
                    if (!data) return;
                    if (!dupeGroups.length) { showToast('✓ Дубликатов нет', '#10b981'); return; }
                }
                const lines = [];
                let total = 0;
                dupeGroups.forEach(g => {
                    total += g.length - 1;
                    g.slice(1).forEach(l => {
                        if (lines.length < 10) lines.push(`• #${l.lot_id} ${(l.title || '').slice(0, 40)} — ${l.price} ₽`);
                    });
                });
                if (!total) { showToast('✓ Дубликатов нет', '#10b981'); return; }
                if (!confirm(`Проверить ${dupeGroups.length} групп-кандидатов (${total} лишних)? Точный дубль = тот же раздел + название + цена + ТИП (Услуги и Гайды с одним названием — разные лоты, не трону). Непроверенные не трогаю.\n\n${lines.join('\n')}${total > 10 ? `\n...и ещё ${total - 10}` : ''}\n\nСами лоты НЕ удаляются — их можно вернуть кнопкой «Вернуть снятые».`)) return;
                showToast('Чистка запущена в фоне — панель остаётся живой, следи за прогрессом ниже', '#3b82f6');
                const res = await fetch('/api/lots/duplicates/cleanup', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ groups: dupeGroups })
                });
                const r2 = await res.json();
                if (!r2.ok || !r2.started) {
                    showToast('✗ ' + (r2.message || r2.error), '#ef4444');
                    return;
                }
                const statusEl = document.getElementById('delStatus');
                const poll = setInterval(async () => {
                    try {
                        const r3 = await fetch('/api/lots/duplicates/status');
                        const st = await r3.json();
                        statusEl.textContent = `🧹 ${st.done}/${st.total}: снято ${st.removed}` + (st.current ? ` · ${st.current}` : '');
                        if (st.finished) {
                            clearInterval(poll);
                            statusEl.textContent = '✓ ' + (st.message || 'Готово');
                            showToast('✓ ' + (st.message || 'Готово'), '#10b981');
                            dupeGroups = [];
                            loadLots();
                        }
                    } catch (e) { /* ждём следующий тик */ }
                }, 3000);
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
        }

        async function dupStop() {
            try {
                const res = await fetch('/api/lots/duplicates/stop', { method: 'POST' });
                const data = await res.json();
                showToast(data.ok ? '⏹ ' + data.message : '✗ ' + (data.error || ''), data.ok ? '#f59e0b' : '#ef4444');
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
        }

        async function purgeInactive() {
            if (!confirm('УДАЛИТЬ выключенные лоты БЕЗВОЗВРАТНО? Снятые с продажи копии пропадут навсегда. Активные не трону.')) return;
            const statusEl = document.getElementById('delStatus');
            statusEl.textContent = '⏳ Ищу выключенные...';
            try {
                const res = await fetch('/api/lots/delete-inactive', { method: 'POST' });
                const data = await res.json();
                if (!data.ok || !data.started) {
                    statusEl.textContent = '✗ ' + (data.error || data.message || 'Ошибка');
                    return;
                }
                const poll = setInterval(async () => {
                    try {
                        const r2 = await fetch('/api/lots/delete-status');
                        const st = await r2.json();
                        statusEl.textContent = `🗑 ${st.done}/${st.total}: удалено ${st.deleted}` + (st.current ? ` · ${st.current}` : '');
                        if (st.finished) {
                            clearInterval(poll);
                            statusEl.textContent = '✓ ' + (st.message || 'Готово');
                            showToast('✓ ' + (st.message || 'Готово'), '#10b981');
                            loadLots();
                        }
                    } catch (e) { /* ждём следующий тик */ }
                }, 3000);
            } catch (e) { statusEl.textContent = '✗ Ошибка сети: ' + e; }
        }

        async function reactivateDeactivated() {
            try {
                showToast('Смотрю, что было снято...', '#3b82f6');
                const r0 = await fetch('/api/lots/deactivated');
                const d0 = await r0.json();
                const ids = d0.lot_ids || [];
                if (!ids.length) { showToast('В логах нет снятых лотов', '#f59e0b'); return; }
                if (!confirm(`Вернуть в продажу ${ids.length} ранее снятых лотов?\n${ids.slice(0, 15).map(id => '#' + id).join(', ')}${ids.length > 15 ? '...' : ''}`)) return;
                showToast('Возврат запущен в фоне — следи за прогрессом ниже', '#3b82f6');
                const res = await fetch('/api/lots/reactivate', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lot_ids: ids })
                });
                const data = await res.json();
                if (!data.ok || !data.started) {
                    showToast('✗ ' + (data.message || data.error), '#ef4444');
                    return;
                }
                const statusEl = document.getElementById('delStatus');
                const poll = setInterval(async () => {
                    try {
                        const r2 = await fetch('/api/lots/duplicates/status');
                        const st = await r2.json();
                        statusEl.textContent = `↩️ ${st.done}/${st.total}: возвращено ${st.removed}` + (st.current ? ` · ${st.current}` : '');
                        if (st.finished) {
                            clearInterval(poll);
                            statusEl.textContent = '✓ ' + (st.message || 'Готово');
                            showToast('✓ ' + (st.message || 'Готово'), '#10b981');
                            loadLots();
                        }
                    } catch (e) { /* ждём следующий тик */ }
                }, 3000);
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
        }

        async function checkApiKeyForCreate() {
            const key = document.getElementById('createApiKey').value.trim();
            const statusEl = document.getElementById('createApiStatus');
            if (!key) { showToast('Вставьте API-ключ', '#f59e0b'); return; }
            statusEl.textContent = '⏳ Проверяю ключ...';
            try {
                const res = await fetch('/api/golden_key/test', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ golden_key: key })
                });
                const data = await res.json();
                if (data.ok) {
                    statusEl.innerHTML = `<span style="color:var(--accent-success);font-weight:600;">✓ Ключ рабочий! Вы: ${escHtml(data.username)} (ID: ${data.user_id})</span>`;
                    showToast('✓ Ключ рабочий!');
                    document.getElementById('cfg_golden_key').value = key;
                    await saveSettings();
                    await loadGames(true);
                } else {
                    statusEl.innerHTML = `<span style="color:var(--accent-danger);font-weight:600;">✗ Ключ не работает: ${escHtml(data.error || '')}</span>`;
                    showToast('✗ Ключ неверный', '#ef4444');
                }
            } catch (e) {
                statusEl.textContent = '✗ Ошибка сети: ' + e;
            }
        }

        // Слоты лотов: до 3 штук за раз (Лот 1/2/3)
        const SLOT_FIELDS = ['bulkOfferType', 'bulkPrice', 'bulkAmount', 'bulkServer', 'bulkShortRu', 'bulkShortEn', 'bulkDescRu', 'bulkDescEn', 'bulkPayRu', 'bulkPayEn', 'bulkSecrets'];
        function _newSlot() { return { fields: {}, extra: { seller_price: 0, side: '', subcategory: '' }, offerManual: false, lotId: '' }; }
        let bulkSlots = [_newSlot(), _newSlot(), _newSlot()];
        let activeSlot = 0;

        function saveSlot(i) {
            const s = bulkSlots[i];
            if (!s) return;
            s.fields = {};
            SLOT_FIELDS.forEach(id => { const el = document.getElementById(id); s.fields[id] = el ? el.value : ''; });
            s.extra = { seller_price: bulkExtra.seller_price || 0, side: bulkExtra.side || '', subcategory: bulkExtra.subcategory || '' };
            s.offerManual = offerTypeManual;
            const sel = document.getElementById('bulkLotSelect');
            s.lotId = sel ? sel.value : '';
        }

        function loadSlot(i) {
            const s = bulkSlots[i];
            if (!s) return;
            SLOT_FIELDS.forEach(id => { const el = document.getElementById(id); if (el) el.value = s.fields[id] || ''; });
            bulkExtra = { seller_price: s.extra.seller_price || 0, side: s.extra.side || '', subcategory: s.extra.subcategory || '' };
            offerTypeManual = !!s.offerManual;
            const sel = document.getElementById('bulkLotSelect');
            if (sel) sel.value = s.lotId || '';
            updateSlotTabs();
        }

        function switchSlot(i) {
            if (i === activeSlot) return;
            saveSlot(activeSlot);
            activeSlot = i;
            loadSlot(i);
        }

        function updateSlotTabs() {
            saveSlot(activeSlot);
            for (let i = 0; i < 3; i++) {
                const b = document.getElementById('slotTab' + i);
                if (!b) continue;
                const f = bulkSlots[i].fields || {};
                const filled = (f['bulkShortRu'] || '').trim() && parseFloat(f['bulkPrice']) > 0;
                b.classList.toggle('active-filter', i === activeSlot);
                b.textContent = `Лот ${i + 1}` + (filled ? ' ●' : '');
            }
        }

        // Точный дубликат своего лота (цена/наличие/автовыдача 1-в-1)
        let bulkExtra = { seller_price: 0, side: '', subcategory: '' };
        let offerTypeManual = false;

        // Автотип: смотрим НАЗВАНИЕ ЛОТА (не разделы!) — гайд никогда не уйдёт в аккаунты
        function detectLotType(text) {
            const t = (text || '').toLowerCase();
            if (/гайд|guide/.test(t)) return 'Гайды';
            if (/gamepass|геймпасс/.test(t)) return 'Gamepass';
            if (/аккаунт|account/.test(t)) return 'Аккаунты';
            if (/услуг|service|буст|boost|прокач|фарм|промокод|promo|подписк|экзекутор|executor|инжект|injector|автовыдача/.test(t)) return 'Услуги';
            if (/прочее|other/.test(t)) return 'Прочее';
            if (/предмет|item|скин|skin|робукс|robux|монет|coin|алмаз|gem|донат|бакс/.test(t)) return 'Предметы';
            return '';
        }

        function suggestOfferType() {
            if (offerTypeManual) return;
            const sel = document.getElementById('bulkOfferType');
            if (!sel) return;
            const titleEl = document.getElementById('bulkShortRu');
            sel.value = detectLotType(titleEl ? titleEl.value : '');
        }

        async function fetchJson(url, timeoutMs) {
            const ctrl = new AbortController();
            const t = setTimeout(() => ctrl.abort(), timeoutMs || 25000);
            try {
                const res = await fetch(url, { signal: ctrl.signal });
                return await res.json();
            } finally { clearTimeout(t); }
        }

        async function postJson(url, payload, timeoutMs) {
            const ctrl = new AbortController();
            const t = setTimeout(() => ctrl.abort(), timeoutMs || 30000);
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    signal: ctrl.signal
                });
                return await res.json();
            } finally { clearTimeout(t); }
        }

        async function duplicateLot(lotId, attempt) {
            attempt = attempt || 1;
            showToast('Копирую лот #' + lotId + (attempt > 1 ? ` (попытка ${attempt}/3)...` : '...'), '#3b82f6');
            try {
                const data = await fetchJson('/api/lot/details?lot_id=' + encodeURIComponent(lotId), 25000);
                if (!data.ok) {
                    if (attempt < 3) {
                        await new Promise(r => setTimeout(r, 2000));
                        return duplicateLot(lotId, attempt + 1);
                    }
                    showToast('✗ ' + (data.error || 'Не скопировался') + ' — пришли мне этот текст', '#ef4444');
                    return;
                }
                const l = data.lot;
                document.getElementById('bulkShortRu').value = (l.summary_ru || '').slice(0, 120);
                document.getElementById('bulkShortEn').value = (l.summary_en || '').slice(0, 120);
                document.getElementById('bulkPrice').value = l.buyer_price || '';
                document.getElementById('bulkAmount').value = l.amount || 1;
                document.getElementById('bulkServer').value = l.server || '';
                document.getElementById('bulkDescRu').value = l.desc_ru || '';
                document.getElementById('bulkDescEn').value = l.desc_en || '';
                document.getElementById('bulkPayRu').value = l.prompt_auto || '';
                document.getElementById('bulkPayEn').value = l.prompt_response || '';
                document.getElementById('bulkSecrets').value = l.secrets || '';
                document.getElementById('bulkOfferType').value = l.offer_type || '';
                offerTypeManual = !!l.offer_type;
                bulkExtra = { seller_price: l.seller_price || 0, side: l.side || '', subcategory: l.subcategory || '' };
                const bulkSel = document.getElementById('bulkLotSelect');
                if (bulkSel) bulkSel.value = String(lotId);
                saveSlot(activeSlot);
                updateSlotTabs();
                showToast('✓ Лот скопирован 1-в-1! Выбери разделы и жми Выставить.', '#10b981');
                switchTab('tab-create');
            } catch (e) {
                if (attempt < 3) {
                    await new Promise(r => setTimeout(r, 2000));
                    return duplicateLot(lotId, attempt + 1);
                }
                showToast('✗ Не дождался FunPay (таймаут). Пришли мне этот текст', '#ef4444');
            }
        }

        async function createBulkLots() {
            const statusEl = document.getElementById('bulkStatus');
            if (selectedChips.size === 0) { showToast('Выберите хотя бы один раздел!', '#f59e0b'); return; }
            saveSlot(activeSlot);
            updateSlotTabs();
            const lots = [];
            bulkSlots.forEach(s => {
                const f = s.fields || {};
                const price = parseFloat(f['bulkPrice']);
                const shortRu = (f['bulkShortRu'] || '').trim();
                if (shortRu && price > 0) {
                    lots.push({
                        title: shortRu,
                        buyer_price: price,
                        seller_price: s.extra.seller_price || 0,
                        side: s.extra.side || '',
                        subcategory: s.extra.subcategory || '',
                        amount: parseInt(f['bulkAmount']) || 1,
                        server: (f['bulkServer'] || '').trim(),
                        offer_type: f['bulkOfferType'] || '',
                        summary_ru: shortRu,
                        summary_en: (f['bulkShortEn'] || '').trim() || shortRu,
                        desc_ru: (f['bulkDescRu'] || '').trim(),
                        desc_en: (f['bulkDescEn'] || '').trim(),
                        pay_ru: (f['bulkPayRu'] || '').trim(),
                        pay_en: (f['bulkPayEn'] || '').trim(),
                        secrets: (f['bulkSecrets'] || '').trim()
                    });
                }
            });
            if (!lots.length) { showToast('Заполни хотя бы один лот (краткое RU + цена)', '#f59e0b'); return; }
            const chips = currentChips.filter(c => selectedChips.has(String(c.chip_id)));
            statusEl.textContent = `⏳ Запускаю: ${lots.length} лота × ${chips.length} разделов...`;
            showToast('Выставление запущено, следи за прогрессом ниже', '#3b82f6');
            try {
                const res = await fetch('/api/lots/create-bulk', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        chips: chips,
                        lots: lots,
                        skip_existing: document.getElementById('bulkSkipExisting').checked,
                        auto_clean: document.getElementById('bulkAutoClean').checked
                    })
                });
                const data = await res.json();
                if (!data.ok || !data.started) {
                    statusEl.textContent = '✗ ' + (data.error || 'Ошибка запуска');
                    showToast('✗ ' + (data.error || 'Ошибка запуска'), '#ef4444');
                    return;
                }
                statusEl.textContent = `⏳ Запущено: 0/${data.total}...`;
                const poll = setInterval(async () => {
                    try {
                        const r2 = await fetch('/api/lots/bulk-status');
                        const st = await r2.json();
                        const errs = (st.errors || []).slice(-2).join(' | ');
                        statusEl.textContent = `⏳ ${st.done}/${st.total}: выставлено ${st.created}, пропущено ${st.skipped}` + (st.current ? ` · ${st.current}` : '') + (errs ? ` | ✗ ${errs}` : '');
                        if (st.finished) {
                            clearInterval(poll);
                            statusEl.textContent = (st.created || st.skipped ? '✓ ' : '✗ ') + (st.message || 'Готово');
                            showToast((st.created || st.skipped ? '✓ ' : '✗ ') + (st.message || 'Готово'), (st.created || st.skipped) ? '#10b981' : '#ef4444');
                            loadLots();
                        }
                    } catch (e) { /* ждём следующий тик */ }
                }, 2000);
            } catch (e) {
                statusEl.textContent = '✗ Ошибка сети: ' + e;
            }
        }

        // Legacy categories (для предпросмотра товаров конкурентов)
        async function loadLots(force) {
            const listBox = document.getElementById('lotsList');
            const statusBox = document.getElementById('lotsStatus');
            try {
                listBox.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">Загрузка лотов...</div>';
                statusBox.style.display = 'none';
                const data = await fetchJson('/api/lots' + (force ? '?force=1' : ''), 45000);
                allLots = data.lots || [];
                // чистим выбор от лотов, которых больше нет
                const alive = new Set(allLots.map(l => String(l.lot_id)));
                selectedDeleteIds.forEach(id => { if (!alive.has(String(id))) selectedDeleteIds.delete(id); });
                renderLotsList();
                updateStats();
                updateDelCount();
                if (data.error) {
                    statusBox.innerHTML = `<span style="color:var(--accent-danger);">⚠ ${escHtml(data.error)}</span> <button class="btn btn-outline" style="padding:4px 10px;font-size:12px;margin-left:8px;" onclick="loadLots(true)">↻ Повторить</button>`;
                    statusBox.style.display = 'block';
                } else if (data.stale) {
                    statusBox.innerHTML = '<span style="color:#f59e0b;">🗄 Показан архив лотов (не живые данные с FunPay).</span>';
                    statusBox.style.display = 'block';
                }
            } catch (e) {
                listBox.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);">
                    <div style="font-size:15px;margin-bottom:10px;">⚠ Не удалось загрузить лоты: ${escHtml(String(e))}</div>
                    <button class="btn btn-outline" onclick="loadLots(true)">↻ Повторить</button>
                </div>`;
            }
        }

        // Фильтры «Мои лоты» по смыслу названия + выбор для удаления одной кнопкой
        let lotTypeFilter = 'all';
        let selectedDeleteIds = new Set();

        const LOT_TYPE_FILTERS = {
            all: () => true,
            accounts: l => /аккаунт|account/i.test(l.title || ''),
            items: l => /предмет|item|скин|skin|робукс|robux|монет|coin|алмаз|gem|гем|донат|бакс|букс/i.test(l.title || ''),
            services: l => /услуг|service|буст|boost|прокач|фарм|экзекутор|executor|инжект|injector|промокод|promo|подписк/i.test(l.title || ''),
            guides: l => /гайд|guide/i.test(l.title || ''),
            gamepass: l => /gamepass|геймпасс/i.test(l.title || ''),
            other: l => /прочее|other/i.test(l.title || '')
        };

        function setLotFilter(f, btn) {
            lotTypeFilter = f;
            document.querySelectorAll('.lot-filter-btn').forEach(b => {
                const m = (b.getAttribute('onclick') || '').match(/setLotFilter\('(\w+)'/);
                b.classList.toggle('active-filter', !!(m && m[1] === f));
            });
            renderLotsList();
        }

        function getVisibleLots() {
            const query = document.getElementById('lotSearch').value.toLowerCase().trim();
            const pred = LOT_TYPE_FILTERS[lotTypeFilter] || LOT_TYPE_FILTERS.all;
            return allLots.filter(l => {
                if (!pred(l)) return false;
                if (!query) return true;
                return (l.title && l.title.toLowerCase().includes(query)) ||
                    String(l.lot_id).includes(query);
            });
        }

        function toggleLotSelect(lotId, checked) {
            lotId = String(lotId);
            if (checked) selectedDeleteIds.add(lotId);
            else selectedDeleteIds.delete(lotId);
            updateDelCount();
        }

        function selectVisibleLots() {
            getVisibleLots().forEach(l => selectedDeleteIds.add(String(l.lot_id)));
            renderLotsList();
            updateDelCount();
        }

        function clearLotSelection() {
            selectedDeleteIds.clear();
            renderLotsList();
            updateDelCount();
        }

        function updateDelCount() {
            const n = selectedDeleteIds.size;
            const del = document.getElementById('delCount');
            if (del) del.textContent = n;
            const ed = document.getElementById('editCount');
            if (ed) ed.textContent = n;
            const be = document.getElementById('bulkEditCount');
            if (be) be.textContent = n;
        }

        // === МАССОВОЕ ИЗМЕНЕНИЕ ВЫБРАННЫХ ЛОТОВ ===
        let bePollTimer = null;
        let beActiveField = 'desc';
        let beOld = { summary: '', desc: '', payment: '' };   // текущие тексты лота-образца
        let beOldLot = null;
        let beUse = { summary: false, desc: false, payment: false };
        const BE_FIELDS = ['summary', 'desc', 'payment'];
        const BE_NAMES = { summary: 'краткое описание', desc: 'подробное описание', payment: 'автоответ после покупки' };

        function openBulkEdit() {
            if (!selectedDeleteIds.size) {
                showToast('Сначала выбери лоты галочками или «Выбрать видимые»', '#f59e0b');
                return;
            }
            const box = document.getElementById('bulkEditBox');
            box.style.display = 'block';
            updateDelCount();
            beRenderChips();
            beTab(beActiveField);
            beLoadOld();
            box.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function closeBulkEdit() {
            document.getElementById('bulkEditBox').style.display = 'none';
        }

        // Маленькие прямоугольники — выбранные лоты
        function beRenderChips() {
            const box = document.getElementById('beChips');
            if (!box) return;
            box.innerHTML = '';
            const ids = [...selectedDeleteIds];
            const shown = ids.slice(0, 24);
            shown.forEach(id => {
                const lot = allLots.find(l => String(l.lot_id) === String(id));
                const chip = document.createElement('div');
                chip.className = 'be-chip';
                const idSpan = document.createElement('span');
                idSpan.className = 'be-chip-id';
                idSpan.textContent = '#' + id;
                const t = document.createElement('span');
                t.className = 'be-chip-title';
                t.textContent = (lot && lot.title) ? lot.title.slice(0, 40) : '';
                t.title = (lot && lot.title) ? lot.title : '';
                const x = document.createElement('span');
                x.className = 'be-chip-x';
                x.textContent = '✕';
                x.title = 'Убрать из выбранных';
                x.onclick = () => {
                    toggleLotSelect(String(id), false);
                    beRenderChips();
                    if (!selectedDeleteIds.size) { closeBulkEdit(); showToast('Выбранных лотов не осталось', '#f59e0b'); }
                };
                chip.append(idSpan, t, x);
                box.appendChild(chip);
            });
            if (ids.length > shown.length) {
                const more = document.createElement('span');
                more.className = 'be-chip-more';
                more.textContent = `… и ещё ${ids.length - shown.length}`;
                box.appendChild(more);
            }
        }

        // Загрузка текущих текстов лота-образца («Было») + подтягивание в редактор
        async function beLoadOld() {
            const ids = [...selectedDeleteIds];
            beOld = { summary: '', desc: '', payment: '' };
            beOldLot = null;
            document.getElementById('be_old_src').textContent = '— загружаю...';
            if (!ids.length) return;
            try {
                const data = await fetchJson('/api/lot/details?lot_id=' + encodeURIComponent(ids[0]), 25000);
                if (data.ok && data.lot) {
                    beOld = {
                        summary: data.lot.summary_ru || '',
                        desc: data.lot.desc_ru || '',
                        payment: data.lot.prompt_auto || ''
                    };
                    beOldLot = String(ids[0]);
                    document.getElementById('be_old_src').textContent = 'лот #' + ids[0];
                    // ТЕКСТ ПОДТЯГИВАЕТСЯ В РЕДАКТОР: меняешь только то, что нужно
                    // (например ссылку), у всех лотов заменится тот же кусок
                    BE_FIELDS.forEach(f => {
                        const ta = document.getElementById('be_' + f);
                        if (ta && !beUse[f]) ta.value = beOld[f];
                    });
                } else {
                    document.getElementById('be_old_src').textContent = 'не загрузилось (' + (data.error || 'ошибка') + ')';
                }
            } catch (e) {
                document.getElementById('be_old_src').textContent = 'не загрузилось (сеть)';
            }
            beDots();
            beRenderDiff();
        }

        // Переключение таба центрального редактора
        function beTab(field) {
            beActiveField = field;
            BE_FIELDS.forEach(f => {
                const tab = document.getElementById('be_tab_' + f);
                if (tab) tab.classList.toggle('active', f === field);
                const ta = document.getElementById('be_' + f);
                if (ta) ta.style.display = (f === field) ? 'block' : 'none';
            });
            document.getElementById('be_use').checked = !!beUse[field];
            beRenderDiff();
        }

        function beUseToggle() {
            beUse[beActiveField] = document.getElementById('be_use').checked;
            beDots();
        }

        // Печать в редакторе: галочка «изменять» включается сама + живой diff
        function beInput(field) {
            const val = document.getElementById('be_' + field).value;
            beUse[field] = val.trim().length > 0;
            if (field === beActiveField) document.getElementById('be_use').checked = beUse[field];
            beDots();
            beRenderDiff();
        }

        function beDots() {
            BE_FIELDS.forEach(f => {
                const dot = document.getElementById('be_dot_' + f);
                if (dot) dot.classList.toggle('on', !!beUse[f]);
            });
        }

        // --- Живой diff: добавленное — зелёным, удалённое — красным ---
        function beTokens(s) { return String(s || '').split(/(\s+)/).filter(t => t.length); }

        function beDiff(oldS, newS) {
            const a = beTokens(oldS), b = beTokens(newS);
            const n = a.length, m = b.length;
            if (n * m > 1200000) {
                // совсем длинные тексты — без пословной подсветки
                return { oldHtml: escHtml(oldS), newHtml: escHtml(newS) };
            }
            const W = m + 1;
            const dp = new Array((n + 1) * W).fill(0);
            for (let i = n - 1; i >= 0; i--) {
                for (let j = m - 1; j >= 0; j--) {
                    dp[i * W + j] = a[i] === b[j]
                        ? dp[(i + 1) * W + j + 1] + 1
                        : Math.max(dp[(i + 1) * W + j], dp[i * W + j + 1]);
                }
            }
            const ins = t => '<span class="be-ins">' + escHtml(t) + '</span>';
            const del = t => '<span class="be-del">' + escHtml(t) + '</span>';
            let i = 0, j = 0, oldHtml = '', newHtml = '';
            while (i < n && j < m) {
                if (a[i] === b[j]) { oldHtml += escHtml(a[i]); newHtml += escHtml(b[j]); i++; j++; }
                else if (dp[(i + 1) * W + j] >= dp[i * W + j + 1]) { oldHtml += del(a[i]); i++; }
                else { newHtml += ins(b[j]); j++; }
            }
            while (i < n) { oldHtml += del(a[i]); i++; }
            while (j < m) { newHtml += ins(b[j]); j++; }
            return { oldHtml: oldHtml, newHtml: newHtml };
        }

        function beRenderDiff() {
            const oldBox = document.getElementById('be_old_view');
            const newBox = document.getElementById('be_new_view');
            if (!oldBox || !newBox) return;
            if (!beOldLot) {
                oldBox.innerHTML = '<span class="be-muted">—</span>';
                newBox.innerHTML = '<span class="be-muted">—</span>';
                return;
            }
            const val = document.getElementById('be_' + beActiveField).value;
            const d = beDiff(beOld[beActiveField], val);
            oldBox.innerHTML = d.oldHtml || '<span class="be-muted">(пусто)</span>';
            newBox.innerHTML = d.newHtml || '<span class="be-muted">(пусто — поле будет очищено у всех)</span>';
        }

                async function applyBulkEdit() {
            if (!selectedDeleteIds.size) { showToast('Лоты не выбраны', '#f59e0b'); return; }
            const payload = { lot_ids: [...selectedDeleteIds] };
            BE_FIELDS.forEach(f => {
                if (beUse[f]) {
                    const key = f === 'payment' ? 'payment_msg' : f;
                    payload[key] = document.getElementById('be_' + f).value;
                    payload['base_' + key] = beOld[f] || '';  // текст до правки — для умной замены
                }
            });
            const fieldsCount = Object.keys(payload).length - 1;
            if (!fieldsCount) { showToast('Включи «Изменять это поле» хотя бы для одного поля', '#f59e0b'); return; }
            const n = selectedDeleteIds.size;
            const what = BE_FIELDS.filter(f => beUse[f]).map(f => BE_NAMES[f]).join(', ');
            if (!confirm(`Изменить ${n} лотов на FunPay?\n\nПоля: ${what}\n\nУмная замена: у каждого лота заменится только изменённый кусок (ссылка и т.п.), его собственный текст останется. Цена и остальное — как были.`)) return;

            const btn = document.getElementById('beApplyBtn');
            btn.disabled = true;
            btn.innerHTML = '⏳ Меняю на FunPay...';
            document.getElementById('beStopBtn').style.display = 'inline-block';
            try {
                const res = await fetch('/api/lots/bulk-edit', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!data.ok || !data.started) {
                    showToast('✗ ' + (data.error || 'Ошибка запуска'), '#ef4444');
                    btn.disabled = false;
                    btn.innerHTML = '💾 Сохранить и изменить на FunPay';
                    return;
                }
                pollBulkEdit();
            } catch (e) {
                showToast('Сетевая ошибка', '#ef4444');
                btn.disabled = false;
                btn.innerHTML = '💾 Сохранить и изменить на FunPay';
            }
        }

                function pollBulkEdit() {
            if (bePollTimer) clearInterval(bePollTimer);
            const bar = document.getElementById('beProgressBar');
            const wrap = document.getElementById('beProgress');
            const errs = document.getElementById('beErrors');
            wrap.style.display = 'block';
            bePollTimer = setInterval(async () => {
                try {
                    const st = await (await fetch('/api/lots/bulkedit-status')).json();
                    const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
                    bar.style.width = pct + '%';
                    document.getElementById('beStatus').textContent =
                        (st.message || '') + (st.current ? ' · сейчас: ' + st.current : '');
                    if (st.errors && st.errors.length) {
                        errs.style.display = 'block';
                        errs.textContent = 'Ошибки:\n' + st.errors.slice(0, 15).join('\n') +
                            (st.errors.length > 15 ? `\n... и ещё ${st.errors.length - 15}` : '');
                    }
                    if (!st.running) {
                        clearInterval(bePollTimer);
                        bePollTimer = null;
                        const btn = document.getElementById('beApplyBtn');
                        btn.disabled = false;
                        btn.innerHTML = '💾 Сохранить и изменить на FunPay';
                        document.getElementById('beStopBtn').style.display = 'none';
                        showToast('✓ ' + (st.message || 'Готово'), '#10b981');
                        loadLots(true); // обновить список лотов (минуя кэш)
                    }
                } catch (e) { /* сеть мигнула — продолжаем опрос */ }
            }, 1500);
        }

        async function beStop() {
            try {
                const res = await fetch('/api/lots/bulkedit-stop', { method: 'POST' });
                const data = await res.json();
                showToast(data.ok ? '⏹ Останавливаю после текущего лота...' : (data.error || 'Не запущено'), '#f59e0b');
            } catch (e) {
                showToast('Сетевая ошибка', '#ef4444');
            }
        }

        function deleteOneLot(lotId) {
            selectedDeleteIds = new Set([String(lotId)]);
            updateDelCount();
            deleteSelectedLots();
        }

        async function deleteSelectedLots() {
            const statusEl = document.getElementById('delStatus');
            if (!selectedDeleteIds.size) { showToast('Выбери лоты галочками или «Выбрать видимые»', '#f59e0b'); return; }
            const n = selectedDeleteIds.size;
            if (!confirm(`УДАЛИТЬ ${n} лотов БЕЗВОЗВРАТНО? Это нельзя отменить!`)) return;
            const ids = [...selectedDeleteIds].map(id => ({ lot_id: id }));
            statusEl.textContent = '⏳ Удаляю...';
            try {
                const res = await fetch('/api/lots/delete', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lot_ids: ids })
                });
                const data = await res.json();
                if (!data.ok || !data.started) {
                    statusEl.textContent = '✗ ' + (data.error || 'Ошибка запуска');
                    return;
                }
                const poll = setInterval(async () => {
                    try {
                        const r2 = await fetch('/api/lots/delete-status');
                        const st = await r2.json();
                        statusEl.textContent = `⏳ ${st.done}/${st.total}: удалено ${st.deleted}` + (st.current ? ` · ${st.current}` : '');
                        if (st.finished) {
                            clearInterval(poll);
                            statusEl.textContent = '✓ ' + (st.message || 'Готово');
                            selectedDeleteIds.clear();
                            updateDelCount();
                            loadLots();
                        }
                    } catch (e) { /* ждём следующий тик */ }
                }, 2000);
            } catch (e) { statusEl.textContent = '✗ Ошибка сети: ' + e; }
        }

        async function delStop() {
            try {
                const res = await fetch('/api/lots/delete-stop', { method: 'POST' });
                const data = await res.json();
                showToast(data.ok ? '⏹ ' + data.message : '✗ ' + (data.error || ''), data.ok ? '#f59e0b' : '#ef4444');
            } catch (e) { showToast('Ошибка сети: ' + e, '#ef4444'); }
        }

        function renderLotsList() {
            const lots = getVisibleLots();
            const container = document.getElementById('lotsList');
            if (!lots.length) {
                container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">Лоты не найдены.</div>';
                refreshBulkLotSelect();
                return;
            }

            container.innerHTML = '';
            lots.forEach(lot => {
                const isDisabled = disabledLotsSet.has(String(lot.lot_id));
                const checked = selectedDeleteIds.has(String(lot.lot_id));
                const item = document.createElement('div');
                item.className = `lot-item ${isDisabled ? 'disabled-lot' : ''}`;
                item.id = `lot-card-${lot.lot_id}`;
                item.innerHTML = `
                    <div class="lot-left">
                        <input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleLotSelect('${lot.lot_id}', this.checked)" title="Выбрать лот (массовое изменение / удаление)" style="width:18px;height:18px;accent-color:#3b82f6;flex-shrink:0;cursor:pointer;">
                        <span class="lot-id-badge">#${lot.lot_id}</span>
                        <div class="lot-title-box">
                            <div class="lot-title" title="${lot.title}">${lot.title}</div>
                            <div class="lot-meta">
                                <span>📁 ${lot.category_name || 'Раздел #' + lot.node_id}</span>
                                <span>📦 Наличие: ${lot.amount || 1} шт.</span>
                            </div>
                        </div>
                    </div>
                    <div class="lot-right">
                        <div class="lot-price">${lot.buyer_price ? lot.buyer_price.toFixed(3) + ' ₽' : lot.price + ' ₽'}</div>
                        <div style="display:flex; align-items:center; gap:8px;">
                            <button class="btn btn-outline" style="padding:6px 10px;font-size:12px;" title="Сохранить лот как шаблон (переживёт выход из аккаунта)" onclick="saveLotAsTemplate('${lot.lot_id}')">📥</button>
                            <button class="btn btn-outline" style="padding:6px 10px;font-size:12px;" title="Дублировать лот 1-в-1 в другие разделы" onclick="duplicateLot('${lot.lot_id}')">📋</button>
                            <button class="btn btn-outline" style="padding:6px 10px;font-size:12px;border-color:#ef4444;color:#fca5a5;" title="Удалить лот безвозвратно" onclick="deleteOneLot('${lot.lot_id}')">🗑</button>
                            <label class="switch">
                                <input type="checkbox" ${!isDisabled ? 'checked' : ''} onchange="toggleLot('${lot.lot_id}', this.checked)">
                                <span class="slider"></span>
                            </label>
                            <span id="label-${lot.lot_id}" style="font-size:12px; font-weight:600; width:65px; color:${!isDisabled ? '#34d399' : '#f59e0b'}">
                                ${!isDisabled ? 'ВКЛ' : 'ВЫКЛ'}
                            </span>
                        </div>
                    </div>
                `;
                container.appendChild(item);
            });
            refreshBulkLotSelect();
        }

        function refreshBulkLotSelect() {
            const sel = document.getElementById('bulkLotSelect');
            if (!sel) return;
            const cur = sel.value;
            sel.innerHTML = '<option value="">📋 Выбрать свой лот для выставления...</option>';
            allLots.forEach(lot => {
                const opt = document.createElement('option');
                opt.value = String(lot.lot_id);
                const price = lot.buyer_price ? Number(lot.buyer_price).toFixed(2) + ' ₽' : '';
                opt.textContent = `#${lot.lot_id} ${(lot.title || '').slice(0, 50)} ${price}`;
                sel.appendChild(opt);
            });
            if (cur && allLots.some(l => String(l.lot_id) === String(cur))) sel.value = cur;
        }

        async function toggleLot(lotId, isEnabled) {
            if (!isEnabled) {
                disabledLotsSet.add(String(lotId));
            } else {
                disabledLotsSet.delete(String(lotId));
            }

            const card = document.getElementById(`lot-card-${lotId}`);
            const lbl = document.getElementById(`label-${lotId}`);
            if (card) {
                card.classList.toggle('disabled-lot', !isEnabled);
            }
            if (lbl) {
                lbl.innerText = isEnabled ? 'ВКЛ' : 'ВЫКЛ';
                lbl.style.color = isEnabled ? '#34d399' : '#f59e0b';
            }

            updateStats();

            try {
                const res = await fetch('/api/lots/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lot_id: String(lotId), enabled: isEnabled })
                });
                const data = await res.json();
                if (data.ok) {
                    showToast(isEnabled ? `Лот #${lotId} включен в автодемпинг` : `Лот #${lotId} отключен от проверки`, isEnabled ? '#10b981' : '#f59e0b');
                }
            } catch (e) {
                showToast('Ошибка сохранения статуса лота', '#ef4444');
            }
        }

        function filterLots() {
            renderLotsList();
        }

        // === CREATE LOTS TAB FUNCTIONS ===
        let allOffers = [];
        let selectedOffers = new Set();

        async function loadCategoryOffers(gameUrlOrNode) {
            // Предпросмотр товаров конкурентов для выбранной игры (необязательно)
            const grid = document.getElementById('offersGrid');
            if (!grid) return;
            let url = gameUrlOrNode || '';
            if (!url) return;
            // если передан node_id вида "123" — строим URL
            if (/^\d+$/.test(String(url).trim())) url = 'https://funpay.com/lots/' + String(url).trim() + '/';
            grid.innerHTML = '<div class="loading-spinner">Загрузка товаров...</div>';
            allOffers = [];
            selectedOffers.clear();
            updateSelectedCount();
            try {
                const res = await fetch('/api/category/offers?url=' + encodeURIComponent(url));
                const data = await res.json();
                allOffers = data.offers || [];
                renderOffersGrid(allOffers);
            } catch (e) {
                grid.innerHTML = '<div style="color: var(--accent-danger); text-align: center;">Ошибка загрузки: ' + e + '</div>';
            }
        }

        function renderOffersGrid(offers) {
            const grid = document.getElementById('offersGrid');
            if (!offers.length) {
                grid.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted);">Товары не найдены в этой категории</div>';
                return;
            }

            grid.innerHTML = '';
            offers.forEach((offer, idx) => {
                const card = document.createElement('div');
                card.className = 'offer-card';
                card.dataset.offerId = offer.offer_id;
                card.dataset.idx = idx;

                const metaParts = [];
                if (offer.server) metaParts.push(`<span>${escHtml(offer.server)}</span>`);
                if (offer.side) metaParts.push(`<span>${escHtml(offer.side)}</span>`);
                if (offer.subcategory) metaParts.push(`<span>${escHtml(offer.subcategory)}</span>`);

                card.innerHTML = `
                    <div class="check-indicator">✓</div>
                    <div class="offer-title">${escHtml(offer.title)}</div>
                    <div class="offer-meta">${metaParts.join('')}</div>
                    <div class="offer-price">${offer.price.toFixed(2)} ₽</div>
                `;

                card.onclick = () => toggleOffer(offer.offer_id, idx, card);
                grid.appendChild(card);
            });
        }

        function toggleOffer(offerId, idx, cardEl) {
            if (selectedOffers.has(offerId)) {
                selectedOffers.delete(offerId);
                cardEl.classList.remove('selected');
            } else {
                selectedOffers.add(offerId);
                cardEl.classList.add('selected');
            }
            updateSelectedCount();
        }

        function updateSelectedCount() {
            document.getElementById('selectedCount').textContent = 'Выбрано: ' + selectedOffers.size;
        }

        function selectAllOffers() {
            const grid = document.getElementById('offersGrid');
            const cards = grid.querySelectorAll('.offer-card');
            cards.forEach(card => {
                const offerId = card.dataset.offerId;
                selectedOffers.add(offerId);
                card.classList.add('selected');
            });
            updateSelectedCount();
        }

        function deselectAllOffers() {
            selectedOffers.clear();
            document.querySelectorAll('.offer-card.selected').forEach(card => {
                card.classList.remove('selected');
            });
            updateSelectedCount();
        }

        function _removed_escHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function updateStats() {
            const total = allLots.length;
            const disabled = disabledLotsSet.size;
            const active = Math.max(0, total - disabled);
            const isRandom = document.getElementById('cfg_random_mode') ? document.getElementById('cfg_random_mode').checked : true;

            document.getElementById('lotsCount').innerText = total;
            document.getElementById('statTotalLots').innerText = total;
            document.getElementById('statActiveLots').innerText = active;
            document.getElementById('statDisabledLots').innerText = disabled;
            document.getElementById('statMode').innerText = isRandom ? '🎲 2–5 мин' : '⏱ Фикс';
        }

        function escHtml(s) {
            return String(s === undefined || s === null ? '' : s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }


        // ─── Правила автоответов по ключевым словам ───
        let autoRules = [];
        let editingRule = -1;

        function normRule(r) {
            r = r || {};
            let kw = (r.keyword !== undefined && r.keyword !== null) ? String(r.keyword) : '';
            if (!kw && Array.isArray(r.keywords)) kw = r.keywords.join(', '); // старый формат
            return { keyword: kw, match: (r.match === 'exact' ? 'exact' : 'contains'), reply: r.reply || '' };
        }

        function renderRules() {
            const list = document.getElementById('rulesList');
            const empty = document.getElementById('rulesEmpty');
            if (!list) return;
            list.innerHTML = '';
            if (!autoRules.length) { if (empty) empty.style.display = 'block'; return; }
            if (empty) empty.style.display = 'none';
            autoRules.forEach((r, i) => {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;border:1px solid var(--border-card);border-radius:10px;padding:10px 12px;background:#15151e;';
                const kw = document.createElement('span');
                kw.textContent = r.keyword;
                kw.title = r.keyword;
                kw.style.cssText = 'background:rgba(59,130,246,.15);color:#60a5fa;border-radius:6px;padding:4px 10px;font-size:12px;font-weight:600;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
                const tag = document.createElement('span');
                tag.textContent = r.match === 'exact' ? 'точно' : 'содержит';
                tag.style.cssText = 'font-size:11px;color:var(--text-muted);border:1px solid var(--border-card);border-radius:6px;padding:3px 8px;white-space:nowrap;';
                const arrow = document.createElement('span');
                arrow.textContent = '→';
                arrow.style.cssText = 'color:var(--text-muted);';
                const rep = document.createElement('span');
                rep.textContent = r.reply;
                rep.title = r.reply;
                rep.style.cssText = 'flex:1;min-width:140px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
                const btnEdit = document.createElement('button');
                btnEdit.className = 'btn btn-outline';
                btnEdit.style.cssText = 'padding:5px 10px;font-size:12px;line-height:1;';
                btnEdit.innerHTML = '✏️';
                btnEdit.title = 'Редактировать';
                btnEdit.onclick = () => editRule(i);
                const btnDel = document.createElement('button');
                btnDel.className = 'btn btn-outline';
                btnDel.style.cssText = 'padding:5px 10px;font-size:12px;line-height:1;';
                btnDel.innerHTML = '🗑';
                btnDel.title = 'Удалить';
                btnDel.onclick = () => deleteRule(i);
                row.append(kw, tag, arrow, rep, btnEdit, btnDel);
                list.appendChild(row);
            });
        }

        function readRuleForm() {
            const kwEl = document.getElementById('rule_keyword');
            const repEl = document.getElementById('rule_reply');
            const checked = document.querySelector('input[name="ruleMatch"]:checked');
            return { keyword: kwEl ? kwEl.value.trim() : '',
                     match: (checked && checked.value === 'exact') ? 'exact' : 'contains',
                     reply: repEl ? repEl.value.trim() : '' };
        }

        function clearRuleForm() {
            const kwEl = document.getElementById('rule_keyword');
            const repEl = document.getElementById('rule_reply');
            if (kwEl) kwEl.value = '';
            if (repEl) repEl.value = '';
            const c = document.querySelector('input[name="ruleMatch"][value="contains"]');
            if (c) c.checked = true;
            editingRule = -1;
            const btn = document.getElementById('ruleSaveBtn');
            if (btn) btn.innerHTML = '➕ Добавить правило';
            const cancel = document.getElementById('ruleCancelBtn');
            if (cancel) cancel.style.display = 'none';
        }

        function addRule() {
            const r = readRuleForm();
            if (!r.keyword) { showToast('Введите ключевое слово или фразу', '#f59e0b'); return; }
            if (!r.reply) { showToast('Введите текст ответа', '#f59e0b'); return; }
            if (editingRule >= 0 && editingRule < autoRules.length) {
                autoRules[editingRule] = r;
                showToast('✓ Правило обновлено');
            } else {
                autoRules.push(r);
                showToast('✓ Правило добавлено');
            }
            clearRuleForm();
            renderRules();
            saveSettings(); // правила сохраняются сразу
        }

        function editRule(i) {
            const r = normRule(autoRules[i]);
            const kwEl = document.getElementById('rule_keyword');
            const repEl = document.getElementById('rule_reply');
            if (kwEl) kwEl.value = r.keyword;
            if (repEl) repEl.value = r.reply;
            const m = document.querySelector('input[name="ruleMatch"][value="' + (r.match === 'exact' ? 'exact' : 'contains') + '"]');
            if (m) m.checked = true;
            editingRule = i;
            const btn = document.getElementById('ruleSaveBtn');
            if (btn) btn.innerHTML = '💾 Сохранить правило';
            const cancel = document.getElementById('ruleCancelBtn');
            if (cancel) cancel.style.display = 'inline-block';
            if (kwEl) kwEl.focus();
        }

        function cancelRuleEdit() { clearRuleForm(); }

        function deleteRule(i) {
            autoRules.splice(i, 1);
            if (editingRule === i) clearRuleForm();
            else if (editingRule > i) editingRule--;
            renderRules();
            saveSettings(); // правила сохраняются сразу
        }

        function collectAutoOrders() {
            const v = id => { const el = document.getElementById(id); return el ? el.value : ''; };
            const c = id => { const el = document.getElementById(id); return el ? el.checked : false; };
            if (!document.getElementById('ord_new_order_en')) return (currentConfig.auto_orders || {});
            return {
                greeting_enabled: c('ord_greet_en'),
                greeting_text: v('ord_greet'),
                greeting_new_only: c('ord_greet_new_only'),
                greeting_skip_system: c('ord_greet_skip_system'),
                new_order_enabled: c('ord_new_order_en'),
                new_order_text: v('ord_new_order'),
                confirm_reply_enabled: c('ord_confirm_en'),
                confirm_reply_text: v('ord_confirm'),
                review_reply_enabled: c('ord_review_en'),
                review_reply_5stars: v('ord_review5'),
                review_reply_other: v('ord_review_other'),
                autoreply_enabled: c('ord_reply_en'),
                autoreply_cooldown_seconds: parseInt(v('ord_reply_cooldown')) || 20,
                autoreply_rules: autoRules.map(r => ({ keyword: r.keyword, match: r.match, reply: r.reply }))
            };
        }

        function fillAutoOrders(ao) {
            if (!document.getElementById('ord_new_order_en') || !ao) return;
            const v = (id, val) => { const el = document.getElementById(id); if (el) el.value = (val === undefined || val === null) ? '' : val; };
            const c = (id, val) => { const el = document.getElementById(id); if (el) el.checked = val === true; };
            // приветствие чатов; в СТАРЫХ конфигах greeting_* означало приветствие заказа
            const oldGreetingFormat = ao.greeting_new_only === undefined && ao.greeting_text !== undefined;
            c('ord_greet_en', oldGreetingFormat ? false : ao.greeting_enabled);
            v('ord_greet', oldGreetingFormat ? '' : ao.greeting_text);
            c('ord_greet_new_only', ao.greeting_new_only !== false);   // по умолчанию включено
            c('ord_greet_skip_system', ao.greeting_skip_system !== false);
            c('ord_new_order_en', ao.new_order_enabled !== undefined ? ao.new_order_enabled
                : (oldGreetingFormat ? ao.greeting_enabled : false));
            v('ord_new_order', ao.new_order_text !== undefined ? ao.new_order_text
                : (oldGreetingFormat ? (ao.greeting_text || '') : ''));
            c('ord_confirm_en', ao.confirm_reply_enabled !== undefined ? ao.confirm_reply_enabled : ao.thanks_enabled);
            v('ord_confirm', ao.confirm_reply_text !== undefined ? ao.confirm_reply_text : (ao.thanks_text || ''));
            c('ord_review_en', ao.review_reply_enabled);
            v('ord_review5', ao.review_reply_5stars);
            v('ord_review_other', ao.review_reply_other);
            c('ord_reply_en', ao.autoreply_enabled);
            v('ord_reply_cooldown', ao.autoreply_cooldown_seconds || 20);
            autoRules = (ao.autoreply_rules || []).map(normRule);
            renderRules();
        }

        async function loadOrdersTab() {
            try {
                const rs = await fetch('/api/orders/auto-status');
                const ds = await rs.json();
                const box = document.getElementById('ordersStatusBox');
                if (ds.ok) {
                    const st = ds.status || {};
                    box.innerHTML =
                        'Бот: ' + (ds.bot_running ? '<b style="color:#34d399;">запущен</b>' : '<b style="color:#f59e0b;">остановлен</b>') +
                        ' · Последний опрос: ' + escHtml(st.last_poll || '—') +
                        ' · Заказов: ' + ds.orders_total + ' (оплачено: ' + ds.orders_paid + ', выполнено: ' + ds.orders_completed + ')' +
                        (st.error ? '<br><span style="color:#f59e0b;">' + escHtml(st.error) + '</span>' : '');
                }
                const ro = await fetch('/api/orders/state');
                const dto = await ro.json();
                const tbox = document.getElementById('ordersTableBox');
                if (dto.ok && dto.orders && dto.orders.length) {
                    let h = '<table style="width:100%;font-size:12px;border-collapse:collapse;">';
                    h += '<tr style="color:var(--text-muted);text-align:left;"><th>Заказ</th><th>Покупатель</th><th>Лот</th><th>Статус</th><th>Флаги</th></tr>';
                    dto.orders.forEach(o => {
                        const fl = (o.greeted ? '🛒' : '') + (o.thanked ? '🙏' : '') +
                            (o.review ? ('★' + o.review + (o.review_replied ? '💬' : '')) : '');
                        h += '<tr style="border-top:1px solid var(--border-card);"><td>#' + escHtml(o.order_id) + '</td><td>' +
                            escHtml(o.buyer) + '</td><td>' + escHtml((o.title || '').slice(0, 40)) + '</td><td>' +
                            escHtml(o.status) + '</td><td>' + fl + '</td></tr>';
                    });
                    tbox.innerHTML = h + '</table>';
                } else {
                    tbox.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">Заказов пока нет.</span>';
                }
            } catch (e) {
                document.getElementById('ordersStatusBox').innerText = 'Ошибка загрузки: ' + e.message;
            }
        }

        async function saveSettings() {
            const payload = {
                golden_key: document.getElementById('cfg_golden_key').value.trim(),
                telegram: {
                    enabled: document.getElementById('cfg_tg_enabled').checked,
                    bot_token: document.getElementById('cfg_tg_token').value.trim(),
                    chat_id: document.getElementById('cfg_tg_chat_id').value.trim(),
                    proxy: document.getElementById('cfg_tg_proxy').value.trim()
                },
                settings: {
                    random_interval_mode: document.getElementById('cfg_random_mode').checked,
                    check_interval_seconds: parseInt(document.getElementById('cfg_fixed_interval').value) || 120,
                    check_interval_min_seconds: parseInt(document.getElementById('cfg_min_interval').value) || 120,
                    check_interval_max_seconds: parseInt(document.getElementById('cfg_max_interval').value) || 300,
                    edit_delay_min_seconds: parseFloat(document.getElementById('cfg_edit_delay_min').value) || 2.0,
                    edit_delay_max_seconds: parseFloat(document.getElementById('cfg_edit_delay_max').value) || 5.0,
                    auto_raise_lots: document.getElementById('cfg_auto_raise').checked,
                    auto_online: document.getElementById('cfg_auto_online').checked,
                    only_online_sellers: currentConfig.settings ? currentConfig.settings.only_online_sellers : true,
                    min_price_safety_floor: currentConfig.settings ? currentConfig.settings.min_price_safety_floor : 0.001,
                    undercut_rules: currentConfig.settings ? currentConfig.settings.undercut_rules : { step_default: 0.01 },
                    match_parameters: currentConfig.settings ? currentConfig.settings.match_parameters : { match_server: true, match_side: true, match_subcategory: true, match_keywords: true },
                    auto_detect_all_user_lots: true,
                    ignored_categories: currentConfig.settings ? currentConfig.settings.ignored_categories : ["any_ore", "any_service", "any_item", "any_set", "farm_bosses"]
                },
                default_seller_prices: (currentConfig.default_seller_prices || {}),
                auto_orders: collectAutoOrders(),
                disabled_lots: Array.from(disabledLotsSet)
            };

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.ok) {
                    showToast('✓ Настройки успешно сохранены!');
                    updateStats();
                } else {
                    showToast('Ошибка сохранения: ' + data.error, '#ef4444');
                }
            } catch (e) {
                showToast('Сетевая ошибка при сохранении', '#ef4444');
            }
        }

        async function testTelegramFull() {
            showToast('Отправка тестового сообщения...', '#3b82f6');
            try {
                const res = await fetch('/api/telegram/test', { method: 'POST' });
                const data = await res.json();
                if (data.ok) {
                    showToast('✓ Тестовое сообщение отправлено в Telegram!');
                } else {
                    showToast('✗ Ошибка: ' + (data.error || 'Проверьте токен и chat_id'), '#ef4444');
                }
            } catch (e) {
                showToast('Ошибка подключения', '#ef4444');
            }
        }

        async function testGoldenKey() {
            const btn = event.target;
            const originalText = btn.innerText;
            btn.innerText = '⏳ Проверка...';
            btn.disabled = true;

            try {
                const goldenKey = document.getElementById('cfg_golden_key').value.trim();
                if (!goldenKey) {
                    showToast('Введите Golden Key для проверки', '#f59e0b');
                    return;
                }

                const res = await fetch('/api/golden_key/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ golden_key: goldenKey })
                });
                const data = await res.json();

                const statusEl = document.getElementById('goldenKeyStatus');
                if (data.ok) {
                    statusEl.innerHTML = `<span style="color: var(--accent-success); font-weight: 600;">✓ Ключ рабочий! Вы: ${data.username} (ID: ${data.user_id})</span>`;
                    showToast('✓ Ключ рабочий! Сохраняю...');
                    await saveSettings();
                    document.getElementById('accountName').innerText = `${data.username} (ID: ${data.user_id})`;
                    const createKey = document.getElementById('createApiKey');
                    if (createKey) createKey.value = document.getElementById('cfg_golden_key').value;
                    await loadGames(true);
                } else {
                    statusEl.innerHTML = `<span style="color: var(--accent-danger); font-weight: 600;">✗ Ошибка: ${data.error}</span>`;
                    showToast('✗ Golden Key неверный: ' + data.error, '#ef4444');
                }
            } catch (e) {
                const statusEl = document.getElementById('goldenKeyStatus');
                statusEl.innerHTML = `<span style="color: var(--accent-danger); font-weight: 600;">✗ Ошибка: ${e.message}</span>`;
                showToast('Ошибка проверки Golden Key', '#ef4444');
            } finally {
                btn.innerText = originalText;
                btn.disabled = false;
            }
        }

        async function testTelegramToken() {
            const btn = event.target;
            const originalText = btn.innerText;
            btn.innerText = '⏳ Проверка...';
            btn.disabled = true;

            try {
                const token = document.getElementById('cfg_tg_token').value.trim();
                if (!token) {
                    showToast('Введите токен для проверки', '#f59e0b');
                    return;
                }

                // Сохраняем перед проверкой
                await saveSettings();

                const res = await fetch('/api/telegram/test', { method: 'POST' });
                const data = await res.json();

                const statusEl = document.getElementById('tgTokenStatus');
                if (data.ok) {
                    statusEl.innerHTML = `<span style="color: var(--accent-success); font-weight: 600;">✓ Токен валиден!</span>`;
                    showToast('✓ Токен Telegram подтвержден!', '#4caf50');
                } else {
                    statusEl.innerHTML = `<span style="color: var(--accent-danger); font-weight: 600;">✗ Ошибка: ${data.error || 'Неверный токен'}</span>`;
                    showToast('✗ Токен Telegram неверный', '#ef4444');
                }
            } catch (e) {
                const statusEl = document.getElementById('tgTokenStatus');
                statusEl.innerHTML = `<span style="color: var(--accent-danger); font-weight: 600;">✗ Ошибка: ${e.message}</span>`;
                showToast('Ошибка проверки токена', '#ef4444');
            } finally {
                btn.innerText = originalText;
                btn.disabled = false;
            }
        }

        async function testTelegramConnection() {
            const btn = event.target;
            const originalText = btn.innerText;
            btn.innerText = '⏳ Проверка...';
            btn.disabled = true;

            try {
                const token = document.getElementById('cfg_tg_token').value.trim();
                const chatId = document.getElementById('cfg_tg_chat_id').value.trim();

                if (!token || !chatId) {
                    showToast('Введите токен и chat_id', '#f59e0b');
                    return;
                }

                const res = await fetch('/api/telegram/test', { method: 'POST' });
                const data = await res.json();

                const statusEl = document.getElementById('tgConnectionStatus');
                if (data.ok) {
                    statusEl.innerHTML = `<span style="color: var(--accent-success); font-weight: 600;">✓ Подключение работает!</span>`;
                    showToast('✓ Telegram подтвержден!', '#4caf50');
                } else {
                    statusEl.innerHTML = `<span style="color: var(--accent-danger); font-weight: 600;">✗ Ошибка: ${data.error || 'Неверные данные'}</span>`;
                    showToast('✗ Проверьте токен и chat_id', '#ef4444');
                }
            } catch (e) {
                const statusEl = document.getElementById('tgConnectionStatus');
                statusEl.innerHTML = `<span style="color: var(--accent-danger); font-weight: 600;">✗ Ошибка: ${e.message}</span>`;
                showToast('Ошибка проверки Telegram', '#ef4444');
            } finally {
                btn.innerText = originalText;
                btn.disabled = false;
            }
        }

        function toggleTelegramInputs() {
            const enabled = document.getElementById('cfg_tg_enabled').checked;
            document.getElementById('cfg_tg_token').disabled = !enabled;
            document.getElementById('cfg_tg_chat_id').disabled = !enabled;
            document.getElementById('cfg_tg_proxy').disabled = !enabled;
        }

        async function loadLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                const box = document.getElementById('logsConsole');
                box.innerText = data.logs || 'Логи пусты.';
                box.scrollTop = box.scrollHeight;
            } catch (e) {
                console.error(e);
            }
        }

        // INIT (параллельно — быстрее)
        window.onload = async () => {
            loadTemplates();
            loadTextTemplates();
            await Promise.all([loadStatus(), loadLots(), loadGames(false)]);
            setInterval(loadBotStatus, 10000);
        };
    
        // === TEMPLATES ===
        async function loadTemplates(force) {
            const grid = document.getElementById('templatesGrid');
            if (!grid) return;
            try {
                const data = await fetchJson('/api/templates', 15000);
                const list = data.templates || [];
                if (!list.length) {
                    grid.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);">Шаблонов пока нет.<br>Сохрани лот кнопкой 📥 во вкладке «📦 Мои лоты» — или заполни форму на вкладке «✨ Создать лоты» и нажми «💾 Сохранить заполненную форму».</div>';
                    return;
                }
                grid.innerHTML = '';
                list.forEach(t => {
                    const card = document.createElement('div');
                    card.className = 'lot-item';
                    card.innerHTML = `
                        <div class="lot-left">
                            <span class="lot-id-badge">📋</span>
                            <div class="lot-title-box">
                                <div class="lot-title">${escHtml(t.name || t.id)}</div>
                                <div class="lot-meta">
                                    <span>💰 ${t.buyer_price ? Number(t.buyer_price).toFixed(2) + ' ₽' : 'цена не задана'}</span>
                                    <span>📦 ${t.amount || 1} шт.</span>
                                    ${t.summary_ru ? '<span>🏷 ' + escHtml(String(t.summary_ru).slice(0, 60)) + '</span>' : ''}
                                    ${t.source_lot_id ? '<span>из лота #' + escHtml(String(t.source_lot_id)) + '</span>' : ''}
                                    ${t.created_at ? '<span>' + new Date(t.created_at * 1000).toLocaleDateString() + '</span>' : ''}
                                </div>
                            </div>
                        </div>
                        <div class="lot-right">
                            <div style="display:flex; align-items:center; gap:8px;">
                                <button class="btn" style="padding:6px 12px;font-size:12px;" onclick="applyTemplate('${t.id}', false)">▶ Выставить</button>
                                <button class="btn btn-outline" style="padding:6px 10px;font-size:12px;" title="Подставить в форму без перехода" onclick="applyTemplate('${t.id}', true)">📝</button>
                                <button class="btn btn-outline" style="padding:6px 10px;font-size:12px;" title="Показать всё содержимое шаблона" onclick="toggleTplDetails('${t.id}')">👁</button>
                                <button class="btn btn-outline" style="padding:6px 10px;font-size:12px;border-color:#ef4444;color:#fca5a5;" title="Удалить шаблон" onclick="deleteTemplate('${t.id}')">🗑</button>
                            </div>
                        </div>
                        <div id="tplDetails-${t.id}" style="display:none; width:100%; flex-basis:100%;"></div>`;
                    card.style.flexWrap = 'wrap';
                    grid.appendChild(card);
                });
            } catch (e) {
                grid.innerHTML = `<div style="text-align:center;padding:24px;color:var(--accent-danger);">⚠ Не удалось загрузить шаблоны: ${escHtml(String(e))}</div>`;
            }
        }

        async function applyTemplate(id, stayHere) {
            try {
                const data = await fetchJson('/api/templates/' + encodeURIComponent(id), 15000);
                const t = data.template || {};
                document.getElementById('bulkOfferType').value = t.offer_type || '';
                if (t.offer_type) offerTypeManual = true;
                document.getElementById('bulkShortRu').value = t.summary_ru || t.name || '';
                document.getElementById('bulkShortEn').value = t.summary_en || '';
                document.getElementById('bulkPrice').value = t.buyer_price || '';
                document.getElementById('bulkAmount').value = t.amount || 1;
                document.getElementById('bulkServer').value = t.server || '';
                document.getElementById('bulkDescRu').value = t.desc_ru || '';
                document.getElementById('bulkDescEn').value = t.desc_en || '';
                document.getElementById('bulkPayRu').value = t.pay_ru || '';
                document.getElementById('bulkPayEn').value = t.pay_en || '';
                document.getElementById('bulkSecrets').value = t.secrets || '';
                bulkExtra = { seller_price: 0, side: '', subcategory: '' };
                saveSlot(activeSlot);
                updateSlotTabs();
                refreshBulkPreview();
                if (!stayHere) switchTab('tab-create');
                showToast('✓ Шаблон подставлен в форму — выбери разделы и жми «Выставить»', '#10b981');
            } catch (e) {
                showToast('✗ Не удалось применить шаблон: ' + e, '#ef4444');
            }
        }

        async function saveFormAsTemplate() {
            const name = document.getElementById('templateName').value.trim();
            if (!name) { showToast('Введи название шаблона', '#f59e0b'); return; }
            const payload = {
                name: name,
                offer_type: document.getElementById('bulkOfferType').value,
                buyer_price: parseFloat(document.getElementById('bulkPrice').value) || 0,
                amount: parseInt(document.getElementById('bulkAmount').value) || 1,
                server: document.getElementById('bulkServer').value,
                summary_ru: document.getElementById('bulkShortRu').value,
                summary_en: document.getElementById('bulkShortEn').value,
                desc_ru: document.getElementById('bulkDescRu').value,
                desc_en: document.getElementById('bulkDescEn').value,
                pay_ru: document.getElementById('bulkPayRu').value,
                pay_en: document.getElementById('bulkPayEn').value,
                secrets: document.getElementById('bulkSecrets').value
            };
            if (!payload.summary_ru && !payload.desc_ru) {
                showToast('Сначала заполни форму лота на вкладке «✨ Создать лоты»', '#f59e0b');
                return;
            }
            try {
                const data = await postJson('/api/templates', payload, 20000);
                if (data.ok) {
                    document.getElementById('templateName').value = '';
                    showToast('✓ Шаблон «' + name + '» сохранён (переживёт выход из аккаунта)', '#10b981');
                    loadTemplates();
                } else showToast('✗ ' + (data.error || 'Не удалось сохранить'), '#ef4444');
            } catch (e) { showToast('✗ ' + e, '#ef4444'); }
        }

        async function saveLotAsTemplate(lotId) {
            showToast('Сохраняю лот #' + lotId + ' как шаблон...', '#3b82f6');
            try {
                const data = await postJson('/api/templates', { from_lot: String(lotId) }, 45000);
                if (data.ok) {
                    showToast('✓ Шаблон сохранён: ' + (data.name || ('лот #' + lotId)), '#10b981');
                    loadTemplates();
                } else showToast('✗ ' + (data.error || 'Не удалось сохранить'), '#ef4444');
            } catch (e) { showToast('✗ Не дождался ответа: ' + e, '#ef4444'); }
        }

        let tplLotsLoaded = false;
        function toggleLotPicker() {
            const box = document.getElementById('lotPicker');
            if (!box) return;
            box.style.display = box.style.display === 'none' ? 'block' : 'none';
            if (box.style.display === 'block' && !tplLotsLoaded) loadTemplateLots();
        }

        async function loadTemplateLots() {
            const sel = document.getElementById('templateLotSelect');
            if (!sel) return;
            sel.innerHTML = '<option value="">Загрузка лотов...</option>';
            try {
                const data = await fetchJson('/api/lots', 45000);
                const lots = data.lots || [];
                if (!lots.length) {
                    sel.innerHTML = '<option value="">' + escHtml(data.error || 'Лоты не загрузились — обнови позже') + '</option>';
                    return;
                }
                sel.innerHTML = '';
                lots.forEach(l => {
                    const o = document.createElement('option');
                    o.value = l.lot_id;
                    o.textContent = '#' + l.lot_id + ' — ' + (l.title || '').slice(0, 70) + (l.buyer_price ? ' • ' + Number(l.buyer_price).toFixed(2) + ' ₽' : '');
                    sel.appendChild(o);
                });
                tplLotsLoaded = true;
            } catch (e) {
                sel.innerHTML = '<option value="">Ошибка: ' + escHtml(String(e)) + '</option>';
            }
        }

        async function saveLotFromPicker() {
            const sel = document.getElementById('templateLotSelect');
            const lotId = sel ? sel.value : '';
            if (!lotId) { showToast('Сначала выбери лот из списка', '#f59e0b'); return; }
            const name = (document.getElementById('templateName').value || '').trim();
            showToast('Сохраняю лот #' + lotId + ' как шаблон...', '#3b82f6');
            try {
                const data = await postJson('/api/templates', { from_lot: String(lotId), name: name }, 45000);
                if (data.ok) {
                    document.getElementById('templateName').value = '';
                    document.getElementById('lotPicker').style.display = 'none';
                    showToast('✓ Шаблон сохранён: ' + (data.name || ('лот #' + lotId)), '#10b981');
                    loadTemplates(true);
                } else showToast('✗ ' + (data.error || 'Не удалось сохранить'), '#ef4444');
            } catch (e) { showToast('✗ Не дождался ответа: ' + e, '#ef4444'); }
        }

        async function toggleTplDetails(id) {
            const box = document.getElementById('tplDetails-' + id);
            if (!box) return;
            if (box.style.display !== 'none') { box.style.display = 'none'; return; }
            box.style.display = 'block';
            if (box.dataset.loaded === '1') return;
            box.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px;">Загрузка...</div>';
            try {
                const data = await fetchJson('/api/templates/' + encodeURIComponent(id), 15000);
                const t = data.template || {};
                const row = (label, val) => `<div style="flex:1;min-width:45%;">
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:2px;">${label}</div>
                    <div style="font-size:13px;white-space:pre-wrap;background:#15151e;border:1px solid var(--border-card);border-radius:8px;padding:8px;min-height:34px;">${escHtml(val || '—')}</div>
                </div>`;
                box.innerHTML = `
                    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;background:#1b1b26;border:1px solid var(--border-card);border-radius:10px;padding:12px;">
                        <div style="display:flex;gap:14px;flex-wrap:wrap;font-size:13px;width:100%;margin-bottom:6px;">
                            <span>💰 Цена: <b>${t.buyer_price ? Number(t.buyer_price).toFixed(2) + ' ₽' : '—'}</b></span>
                            <span>📦 Наличие: <b>${t.amount || 1} шт.</b></span>
                            <span>🏷 Тип: <b>${escHtml(t.offer_type || 'авто')}</b></span>
                            ${t.server ? '<span>🖥 Сервер: <b>' + escHtml(t.server) + '</b></span>' : ''}
                            ${t.secrets ? '<span>🔑 Секреты: <b>' + String(t.secrets).split('\n').filter(x => x.trim()).length + ' шт.</b></span>' : ''}
                        </div>
                        ${row('🇷🇺 Краткое описание RU', t.summary_ru)}
                        ${row('🇬🇧 Short description EN', t.summary_en)}
                        ${row('🇷🇺 Подробное описание RU', t.desc_ru)}
                        ${row('🇬🇧 Detailed description EN', t.desc_en)}
                        ${row('✉️ Автовыдача RU', t.pay_ru)}
                        ${row('✉️ Auto-delivery EN', t.pay_en)}
                    </div>`;
                box.dataset.loaded = '1';
            } catch (e) {
                box.innerHTML = '<div style="color:var(--accent-danger);font-size:12px;">Не удалось загрузить: ' + escHtml(String(e)) + '</div>';
            }
        }

        async function deleteTemplate(id) {
            if (!confirm('Удалить шаблон?')) return;
            try {
                const ctrl = new AbortController();
                const t = setTimeout(() => ctrl.abort(), 15000);
                const res = await fetch('/api/templates/' + encodeURIComponent(id), { method: 'DELETE', signal: ctrl.signal });
                clearTimeout(t);
                const data = await res.json();
                if (data.ok) { showToast('Удалено', '#10b981'); loadTemplates(); }
                else showToast('Ошибка: ' + (data.error || ''), '#ef4444');
            } catch (e) { showToast('Ошибка: ' + e, '#ef4444'); }
        }

        // === TEXT: ГЕНЕРАТОР / УЛУЧШЕНИЕ / ПЕРЕВОД ===
        function openGenModal() {
            const main = document.getElementById('textStyleInput');
            const modal = document.getElementById('genStyle');
            if (main && modal && !modal.value) modal.value = main.value;
            const gc = document.getElementById('genCategory');
            const bt = (document.getElementById('bulkOfferType') || {}).value || '';
            if (gc && bt) {
                const map = {'Аккаунты': 'Аккаунты', 'Предметы': 'Валюта и предметы',
                             'Услуги': 'Услуги', 'Гайды': 'Другое', 'Gamepass': 'Другое',
                             'Прочее': 'Другое'};
                if (map[bt]) gc.value = map[bt];
            }
            document.getElementById('genModal').style.display = 'flex';
        }
        function closeGenModal(e) { document.getElementById('genModal').style.display = 'none'; }
        function textStyle() {
            const el = document.getElementById('textStyleInput');
            return el ? el.value.trim() : '';
        }

        async function runGenerator() {
            const statusEl = document.getElementById('genStatus');
            const btn = document.getElementById('genBtn');
            const idea = document.getElementById('genIdea').value.trim();
            const features = document.getElementById('genFeatures').value.trim();
            if (!idea || !features) { statusEl.textContent = '✗ Заполни «Что продаём» и «Ключевые особенности».'; return; }
            btn.disabled = true;
            statusEl.textContent = '⏳ Составляю текст...';
            try {
                const genStyleEl = document.getElementById('genStyle');
                const mainStyleEl = document.getElementById('textStyleInput');
                const styleVal = (genStyleEl && genStyleEl.value.trim()) || textStyle();
                if (mainStyleEl && styleVal) mainStyleEl.value = styleVal;
                const data = await postJson('/api/text/generate', {
                    idea: idea,
                    features: features,
                    style: styleVal,
                    category: (document.getElementById('genCategory') || {}).value || 'Другое',
                    buyer_msg: document.getElementById('genBuyerMsg').checked,
                    translate: document.getElementById('genTranslate').checked
                }, 60000);
                if (!data.ok) { statusEl.textContent = '✗ ' + (data.error || 'Ошибка'); return; }
                document.getElementById('bulkShortRu').value = data.title_ru || '';
                document.getElementById('bulkDescRu').value = data.desc_ru || '';
                if (data.pay_ru) document.getElementById('bulkPayRu').value = data.pay_ru;
                if (document.getElementById('genTranslate').checked) {
                    document.getElementById('bulkShortEn').value = data.title_en || '';
                    document.getElementById('bulkDescEn').value = data.desc_en || '';
                    if (data.pay_en) document.getElementById('bulkPayEn').value = data.pay_en;
                }
                saveSlot(activeSlot);
                updateSlotTabs();
                refreshBulkPreview();
                statusEl.textContent = '';
                closeGenModal();
                if (data.warning) showToast('⚠ ' + data.warning, '#f59e0b');
                else showToast('✓ Текст готов и подставлен в форму!', '#10b981');
            } catch (e) {
                statusEl.textContent = '✗ ' + e;
            } finally { btn.disabled = false; }
        }

        async function improveField(fieldId) {
            const el = document.getElementById(fieldId);
            const text = el.value.trim();
            if (!text) { showToast('Поле пустое — нечего улучшать', '#f59e0b'); return; }
            showToast('Причёсываю текст...', '#3b82f6');
            try {
                const data = await postJson('/api/text/improve', { text: text, style: textStyle() }, 20000);
                if (data.ok) {
                    el.value = data.text;
                    saveSlot(activeSlot);
                    updateSlotTabs();
                    showToast('✓ Текст улучшен в твоём стиле', '#10b981');
                } else showToast('✗ ' + (data.error || ''), '#ef4444');
            } catch (e) { showToast('✗ ' + e, '#ef4444'); }
        }

        async function translatePair(ruId, enId) {
            const ru = document.getElementById(ruId);
            const en = document.getElementById(enId);
            const text = ru.value.trim();
            if (!text) { showToast('Нечего переводить — поле пустое', '#f59e0b'); return; }
            showToast('Перевожу на английский...', '#3b82f6');
            try {
                const data = await postJson('/api/text/translate', { text: text, to: 'en' }, 30000);
                if (data.ok) {
                    en.value = data.text;
                    saveSlot(activeSlot);
                    showToast('✓ Переведено', '#10b981');
                } else showToast('⚠ ' + (data.error || 'Перевод не удался'), '#f59e0b');
            } catch (e) { showToast('✗ ' + e, '#ef4444'); }
        }

// === TEXT TEMPLATES ===
        async function loadTextTemplates() {
            try {
                const res = await fetch('/api/text-templates');
                const data = await res.json();
                const sel = document.getElementById('textTemplateSelect');
                sel.innerHTML = '<option value="">Выберите шаблон...</option>';
                (data.templates || []).forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t.id;
                    opt.textContent = t.name || t.id;
                    sel.appendChild(opt);
                });
            } catch (e) { console.error(e); }
        }

        async function loadTextTemplate() {
            const id = document.getElementById('textTemplateSelect').value;
            if (!id) return;
            try {
                const res = await fetch('/api/text-templates/' + id);
                const data = await res.json();
                const t = data.template || {};
                document.getElementById('textTemplateName').value = t.name || '';
                document.getElementById('textTemplateType').value = t.type || 'custom';
                document.getElementById('textTemplateContent').value = t.content || '';
                showToast('Загружено', '#10b981');
            } catch (e) { showToast('Ошибка: ' + e, '#ef4444'); }
        }

        async function saveAsTextTemplate() {
            const name = document.getElementById('textTemplateName').value.trim();
            if (!name) { showToast('Введите название', '#f59e0b'); return; }
            const payload = {
                name: name,
                type: document.getElementById('textTemplateType').value,
                content: document.getElementById('textTemplateContent').value
            };
            try {
                const res = await fetch('/api/text-templates', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.ok) { showToast('Сохранено', '#10b981'); loadTextTemplates(); }
                else showToast('Ошибка: ' + data.error, '#ef4444');
            } catch (e) { showToast('Ошибка: ' + e, '#ef4444'); }
        }

        async function deleteTextTemplate() {
            const id = document.getElementById('textTemplateSelect').value;
            if (!id) return;
            if (!confirm('Удалить?')) return;
            try {
                const res = await fetch('/api/text-templates/' + id, { method: 'DELETE' });
                const data = await res.json();
                if (data.ok) { showToast('Удалено', '#10b981'); loadTextTemplates(); }
            } catch (e) { showToast('Ошибка: ' + e, '#ef4444'); }
        }

</script>
</body>
</html>

"""


def get_all_local_ips() -> List[str]:
    """Возвращает список всех доступных локальных IP адресов компьютера"""
    ips = []
    try:
        host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in host_ips:
            if not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass

    def ip_priority(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            return 2
        return 3

    return sorted(ips, key=ip_priority)


def get_best_local_ip() -> str:
    """Возвращает основной локальный IP (Wi-Fi/LAN)"""
    ips = get_all_local_ips()
    return ips[0] if ips else "127.0.0.1"


class DashboardHTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Отключаем спам стандартного HTTP логгера
        return

    def handle_one_request(self):
        # Любая ошибка обработчика — JSON 500, а не смерть соединения/сервера
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error(f"HTTP error: {e}", exc_info=True)
            try:
                self._send_json({"ok": False, "error": f"Внутренняя ошибка: {e}"}, status=500)
            except Exception:
                pass

    def _send_json(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        if path == "/api/status":
            config = self._read_config()
            # Кэшированная проверка ключа (60 сек) — панель открывается мгновенно,
            # а не ждёт полный anti-detect цикл FunPay при каждом обновлении.
            auth_ok, uid, uname = _cached_auth((config.get("golden_key") or "").strip())
            best_ip = get_best_local_ip()
            phone_url = f"http://{best_ip}:{PORT}"
            self._send_json({
                "auth_ok": auth_ok,
                "user_id": uid,
                "username": uname,
                "config": config,
                "phone_url": phone_url,
                "local_ips": get_all_local_ips(),
            })
            return

        if path == "/api/lots":
            qs = urllib.parse.parse_qs(parsed.query)
            _flag = lambda name: (qs.get(name, ["0"])[0] or "0").lower() in ("1", "true", "yes")
            want_archive = _flag("archive")
            force = _flag("force")
            config = self._read_config()
            key = (config.get("golden_key") or "").strip()

            # Архив — только по явному ?archive=1 (иначе пользователь видит
            # чужие/старые лоты и не понимает, почему).
            if want_archive:
                lots_list = []
                if os.path.exists(BACKUP_FILE):
                    try:
                        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                            backup_data = json.load(f)
                            for item in backup_data:
                                lots_list.append({
                                    "lot_id": str(item.get("lot_id")),
                                    "title": item.get("summary_ru") or item.get("category_name", ""),
                                    "price": float(item.get("price") or 0.0),
                                    "buyer_price": float(item.get("buyer_price") or 0.0),
                                    "category_name": item.get("category_name", ""),
                                    "node_id": item.get("node_id", "402"),
                                    "amount": item.get("amount", 1),
                                })
                    except Exception:
                        pass
                self._send_json({"lots": lots_list, "source": "archive", "stale": True,
                                 "auth_ok": None, "error": None})
                return

            if not key:
                self._send_json({"lots": [], "source": "none", "stale": False,
                                 "auth_ok": False,
                                 "error": "Не задан golden_key — открой «⚙ Настройки бота» и вставь ключ"})
                return

            # Кэш на 30 сек: повторные клики не бьют FunPay (иначе 429/CAPTCHA)
            with _LOTS_CACHE_LOCK:
                fresh = (not force
                         and (time.time() - _LOTS_CACHE["ts"]) < _LOTS_CACHE_TTL
                         and _LOTS_CACHE["key"] == key
                         and _LOTS_CACHE["error"] is None)
                cached_payload = _LOTS_CACHE["payload"]
            if fresh and cached_payload:
                self._send_json(dict(cached_payload))
                return

            lots_list, error, auth_ok = [], None, False
            try:
                auth_ok, uid, uname = _cached_auth(key)
                client = FunPayClient(golden_key=key)
                if auth_ok and uid:
                    client.user_id = uid
                    client.username = uname
                active_lots = client.get_user_active_lots(delay=(0.1, 0.3))
                if active_lots is None:
                    error = "FunPay вернул защитную страницу (CAPTCHA/лимит). Подожди пару минут и нажми 🔄 Обновить."
                elif not auth_ok:
                    error = "Ключ не работает — FunPay не пускает в аккаунт. Вставь свежий golden_key в настройках."
                else:
                    for lot in active_lots or []:
                        lots_list.append({
                            "lot_id": str(lot.lot_id),
                            "title": lot.title,
                            "price": lot.current_price,
                            "buyer_price": lot.current_price,
                            "category_name": lot.category_name or "Раздел",
                            "node_id": lot.category_id or "",
                            "amount": lot.amount or 1,
                        })
            except Exception as e:
                error = f"Ошибка загрузки: {e}"

            payload = {"lots": lots_list, "source": "live", "stale": False,
                       "auth_ok": bool(auth_ok), "error": error}
            with _LOTS_CACHE_LOCK:
                _LOTS_CACHE.update({"key": key, "ts": time.time(),
                                    "payload": payload, "error": error})
            self._send_json(payload)
            return

        if path == "/api/logs":
            logs_text = ""
            if os.path.exists("bot.log"):
                try:
                    with open("bot.log", "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        logs_text = "".join(lines[-150:])
                except Exception:
                    logs_text = "Не удалось прочитать bot.log"
            self._send_json({"logs": logs_text})
            return

        # API: Список игр с разделами (новый браузер категорий)
        if path == "/api/games":
            qs = urllib.parse.parse_qs(parsed.query)
            force = (qs.get("refresh", ["0"])[0] == "1")
            config = self._read_config()
            client = FunPayClient(golden_key=config.get("golden_key", ""))
            games = _get_games_cached(client, force=force)
            error = None
            if not games:
                if not (config.get("golden_key") or "").strip():
                    error = "Не задан golden_key — открой «⚙ Настройки бота» и вставь ключ."
                else:
                    error = ("FunPay не отдал список игр (защита/лимит запросов). "
                             "Подожди минуту и нажми 🔄 Обновить игры.")
            self._send_json({"games": games, "count": len(games), "error": error})
            return

        # API: Получить список категорий (legacy, для совместимости)
        if path == "/api/categories":
            config = self._read_config()
            client = FunPayClient(golden_key=config.get("golden_key", ""))
            games = _get_games_cached(client)
            # плоский список для старого фронта: игры как категории
            categories = [{"node_id": g["game_id"], "name": g["name"], "url": g["url"]} for g in games[:400]]
            if not categories:
                categories = self._get_categories(client)
            self._send_json({"categories": categories})
            return

        # API: Получить офферы категории (чинит парсинг query: ?url=... или ?node_id=...)
        if path == "/api/category/offers":
            qs = urllib.parse.parse_qs(parsed.query)
            node_id = (qs.get("node_id", [""])[0] or "").strip()
            url = (qs.get("url", [""])[0] or "").strip()
            if url:
                target_url = url
            elif node_id:
                if node_id.startswith("http"):
                    target_url = node_id
                elif "_" in node_id:
                    kind, cid = node_id.split("_", 1)
                    target_url = f"https://funpay.com/{kind}/{cid}/"
                else:
                    target_url = f"https://funpay.com/lots/{node_id}/"
            else:
                self._send_json({"offers": [], "error": "Не указан раздел"})
                return
            config = self._read_config()
            client = FunPayClient(golden_key=config.get("golden_key", ""))
            offers = self._get_category_offers_by_url(client, target_url)
            self._send_json({"offers": offers, "url": target_url})
            return

        # API: Лоты, снятые чисткой с продажи (читаем bot.log) — чтобы вернуть одной кнопкой
        if path == "/api/lots/deactivated":
            ids: List[str] = []
            seen: Set[str] = set()
            if os.path.exists("bot.log"):
                try:
                    with open("bot.log", "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            m = re.search(r"\[DUPES\] #(\d+): снят с продажи", line)
                            if m and m.group(1) not in seen:
                                seen.add(m.group(1))
                                ids.append(m.group(1))
                except Exception:
                    pass
            self._send_json({"ok": True, "lot_ids": ids, "count": len(ids)})
            return

        # API: Статус бота
        if path == "/api/bot/status":
            self._send_json({"running": _is_bot_running()})
            return

        # API: Скачать настройки для хостинга (config.json без секретов).
        # Файл кладёшь в репозиторий GitHub — на хостинге настройки
        # переживают рестарты и не слетают.
        if path == "/api/config/export":
            cfg = self._read_config()
            cfg.pop("golden_key", None)                       # ключ — только в переменных окружения
            tg = cfg.get("telegram") or {}
            if isinstance(tg, dict):
                tg.pop("bot_token", None)                     # секреты не уезжают в репозиторий
            cfg.pop("panel_password", None)
            data = json.dumps(cfg, ensure_ascii=False, indent=2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="config.json"')
            self.send_header("Content-Length", str(len(data.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))
            return

        # API: Статус автоподнятия ВСЕХ разделов
        if path == "/api/raise/status":
            state = _read_raise_state()
            with _BULK_LOCK:
                task = dict(_RAISE_TASK)
            state.update({"task": task})
            self._send_json(state)
            return

        # API: Поиск дубликатов своих лотов.
        # Кандидаты: один раздел + одинаковое название.
        # Точная проверка по типу — на этапе чистки (цены у копий разъезжаются
        # из-за автодемпинга, поэтому цена НЕ критерий).
        if path == "/api/lots/duplicates":
            config = self._read_config()
            client = FunPayClient(golden_key=config.get("golden_key", ""))
            try:
                lots = client.get_user_active_lots()
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
                return
            groups: Dict[str, List[Dict]] = {}
            for lot in lots:
                nid = str(lot.category_id or "").strip()
                if not nid and lot.category_url:
                    m = re.search(r"/(chips|lots)/(\d+)", lot.category_url)
                    if m:
                        nid = m.group(2)
                if not nid:
                    continue  # раздел неизвестен — такие лоты никогда не трогаем
                key = nid + "|" + re.sub(r"[^a-zа-яё0-9]", "", (lot.title or "").lower())
                groups.setdefault(key, []).append({
                    "lot_id": str(lot.lot_id), "title": lot.title,
                    "price": lot.current_price, "node": nid,
                    "category_name": lot.category_name,
                    "is_chip": bool(lot.is_chip),
                })
            dupes = [v for v in groups.values() if len(v) > 1]
            for g in dupes:
                g.sort(key=lambda x: x["price"] or 0)
            self._send_json({"ok": True, "groups": dupes,
                             "extra_count": sum(len(g) - 1 for g in dupes)})
            return

        # API: Прогресс массового выставления
        if path == "/api/lots/bulk-status":
            with _BULK_LOCK:
                self._send_json(dict(_BULK_TASK))
            return

        # API: Прогресс удаления лотов
        if path == "/api/lots/delete-status":
            with _BULK_LOCK:
                self._send_json(dict(_DEL_TASK))
            return

        # API: Прогресс массового редактирования лотов
        if path == "/api/lots/bulkedit-status":
            with _BULK_LOCK:
                self._send_json(dict(_EDIT_TASK))
            return

        # API: Прогресс чистки дубликатов
        if path == "/api/lots/duplicates/status":
            with _BULK_LOCK:
                self._send_json(dict(_DUP_TASK))
            return

        # API: Детали своего лота для полного дублирования 1-в-1
        if path == "/api/lot/details":
            qs = urllib.parse.parse_qs(parsed.query)
            lot_id = (qs.get("lot_id", [""])[0] or "").strip()
            if not lot_id:
                self._send_json({"ok": False, "error": "Не указан lot_id"})
                return
            config = self._read_config()
            client = FunPayClient(golden_key=config.get("golden_key", ""))
            details = client.get_lot_details(lot_id)
            if not details:
                self._send_json({"ok": False, "error": "Не удалось открыть лот (проверьте ключ)"})
                return
            self._send_json({"ok": True, "lot": _lot_payload_from_details(details, lot_id)})
            return

        # API: Авто-заказы — статус воркера
        if path == "/api/orders/auto-status":
            st = {}
            try:
                if os.path.exists("auto_orders_status.json"):
                    with open("auto_orders_status.json", "r", encoding="utf-8") as f:
                        st = json.load(f) or {}
            except Exception:
                st = {}
            state = {}
            try:
                if os.path.exists("orders_state.json"):
                    with open("orders_state.json", "r", encoding="utf-8") as f:
                        state = json.load(f) or {}
            except Exception:
                state = {}
            stock = {"lots": {}, "gifts": []}
            try:
                if os.path.exists("stock.json"):
                    with open("stock.json", "r", encoding="utf-8") as f:
                        stock = json.load(f) or stock
            except Exception:
                pass
            stock_left = {k: len(v or []) for k, v in (stock.get("lots") or {}).items()}
            self._send_json({
                "ok": True,
                "bot_running": _is_bot_running(),
                "status": st,
                "orders_total": len(state),
                "orders_paid": sum(1 for s in state.values() if (s or {}).get("status") == "paid"),
                "orders_completed": sum(1 for s in state.values() if (s or {}).get("status") == "completed"),
                "delivered": sum(1 for s in state.values() if (s or {}).get("delivered")),
                "stock_lots": stock_left,
                "stock_gifts": len(stock.get("gifts") or []),
            })
            return

        # API: Авто-заказы — последние заказы
        if path == "/api/orders/state":
            state = {}
            try:
                if os.path.exists("orders_state.json"):
                    with open("orders_state.json", "r", encoding="utf-8") as f:
                        state = json.load(f) or {}
            except Exception:
                state = {}
            items = []
            for oid, s in (state or {}).items():
                s = s or {}
                items.append({"order_id": oid, "title": s.get("title", ""),
                              "buyer": s.get("buyer", ""), "price": s.get("price", 0),
                              "status": s.get("status", ""), "greeted": bool(s.get("greeted")),
                              "delivered": bool(s.get("delivered")),
                              "stock_key": s.get("stock_key", ""),
                              "thanked": bool(s.get("thanked")),
                              "gift": bool(s.get("gift_confirm_sent") or s.get("gift5_sent")),
                              "review": s.get("review_rating"),
                              "review_replied": bool(s.get("review_replied"))})
            items.reverse()
            self._send_json({"ok": True, "orders": items[:30]})
            return

        # API: Склад — чтение
        if path == "/api/orders/stock":
            stock = {"lots": {}, "gifts": []}
            try:
                if os.path.exists("stock.json"):
                    with open("stock.json", "r", encoding="utf-8") as f:
                        stock = json.load(f) or stock
            except Exception:
                pass
            self._send_json({"ok": True, "stock": stock})
            return

        # Шаблоны/текст обрабатываются и на GET (списки, чтение)
        if self._templates_api(path, {}):
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        
        try:
            req_data = json.loads(body) if body else {}
        except Exception:
            req_data = {}

        if path == "/api/lots/toggle":
            lot_id = str(req_data.get("lot_id", ""))
            enabled = bool(req_data.get("enabled", True))
            config = self._read_config()
            disabled = set(str(x) for x in config.get("disabled_lots", []))

            if not enabled:
                disabled.add(lot_id)
            else:
                disabled.discard(lot_id)

            config["disabled_lots"] = sorted(list(disabled))
            self._save_config(config)
            self._send_json({"ok": True, "disabled_lots": config["disabled_lots"]})
            return

        if path == "/api/settings":
            config = self._read_config()
            for k, v in req_data.items():
                config[k] = v
            self._save_config(config)
            self._send_json({"ok": True})
            return

        if path == "/api/telegram/test":
            config = self._read_config()
            tg = config.get("telegram", {})
            notifier = TelegramNotifier(
                token=tg.get("bot_token", ""),
                chat_id=tg.get("chat_id", ""),
                enabled=True,
                proxy=tg.get("proxy", ""),
            )
            success = notifier.send_message(
                "🔔 <b>Тестовое уведомление из Web Dashboard!</b>\n\n"
                "Подключение к Telegram работает стабильно и готово к отправке отчетов."
            )
            self._send_json({"ok": success, "error": None if success else "Не удалось доставить сообщение"})
            return

        if path == "/api/export":
            try:
                from backup_lots import export_all_lots
                export_all_lots()
                count = 0
                if os.path.exists(BACKUP_FILE):
                    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                        count = len(json.load(f))
                self._send_json({"ok": True, "count": count})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if path == "/api/golden_key/test":
            # Тестирование Golden Key
            golden_key = req_data.get("golden_key", "").strip()
            if not golden_key:
                self._send_json({"ok": False, "error": "Golden Key не указан"})
                return

            client = FunPayClient(golden_key=golden_key)
            auth_ok, uid, uname, err = client.check_auth()

            if auth_ok:
                self._send_json({
                    "ok": True,
                    "user_id": uid,
                    "username": uname,
                    "message": f"✓ Golden Key валиден! Вы вошли как: {uname} (ID: {uid})"
                })
            else:
                self._send_json({"ok": False, "error": f"Ошибка авторизации: {err}"})
            return

        if path == "/api/bot/start":
            self._send_json(_start_bot_thread())
            return

        if path == "/api/bot/stop":
            self._send_json(_stop_bot_thread())
            return

        # API: Поднять ВСЕ разделы сейчас (фоном — не блокирует панель)
        if path == "/api/raise/now":
            with _BULK_LOCK:
                if _RAISE_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Поднятие уже идёт, дождитесь завершения"})
                    return
                _RAISE_TASK.update({"running": True, "finished": False, "total": 0,
                                    "raised": 0, "cooldown": 0, "errors": 0,
                                    "message": "Запуск...", "current": "", "details": []})
            t = _threading.Thread(target=_run_raise_all_task,
                                  args=(self._read_config(),), daemon=True)
            t.start()
            # Ждём до 90 сек результата, панель при этом жива (поток уже работает)
            import time as _t
            for _ in range(90):
                _t.sleep(1)
                with _BULK_LOCK:
                    snap = dict(_RAISE_TASK)
                if snap.get("finished"):
                    self._send_json({"ok": True, "total": snap.get("total", 0),
                                     "raised": snap.get("raised", 0),
                                     "cooldown": snap.get("cooldown", 0),
                                     "errors": snap.get("errors", 0),
                                     "message": snap.get("message", ""),
                                     "details": snap.get("details", [])})
                    return
            with _BULK_LOCK:
                snap = dict(_RAISE_TASK)
            self._send_json({"ok": True, "started": True,
                             "total": snap.get("total", 0),
                             "message": "Поднятие запущено в фоне. Статус — /api/raise/status."})
            return

        if path == "/api/lots/bulk-stop":
            with _BULK_LOCK:
                if not _BULK_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Выставление не запущено"})
                else:
                    _BULK_TASK["stop"] = True
                    self._send_json({"ok": True, "message": "Останавливаю после текущего раздела..."})
            return

        if path == "/api/lots/delete-stop":
            with _BULK_LOCK:
                if not _DEL_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Удаление не запущено"})
                else:
                    _DEL_TASK["stop"] = True
                    self._send_json({"ok": True, "message": "Останавливаю после текущего лота..."})
            return

        if path == "/api/lots/bulkedit-stop":
            with _BULK_LOCK:
                if not _EDIT_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Редактирование не запущено"})
                else:
                    _EDIT_TASK["stop"] = True
                    self._send_json({"ok": True, "message": "Останавливаю после текущего лота..."})
            return

        # Удалить выключенные лоты (реестр + лог). Безвозвратно!
        if path == "/api/lots/delete-inactive":
            with _BULK_LOCK:
                if _DEL_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Удаление уже идёт"})
                    return
                _DEL_TASK.update({"running": True, "total": 0, "done": 0,
                                  "deleted": 0, "errors": [], "message": "",
                                  "finished": False, "current": "", "stop": False})
            t = _threading.Thread(target=self._run_purge_task,
                                  args=(self._read_config(),), daemon=True)
            t.start()
            self._send_json({"ok": True, "started": True,
                             "message": "Чистка выключенных запущена. Панель остаётся живой."})
            return

        # Массовое редактирование выбранных лотов (фоном, с прогрессом):
        # краткое описание / подробное описание / сообщение после оплаты.
        if path == "/api/lots/bulk-edit":
            items = req_data.get("lot_ids", []) or []
            targets = []
            for it in items[:300]:
                if isinstance(it, dict):
                    targets.append((str(it.get("lot_id", "")), bool(it.get("is_chip", False))))
                else:
                    targets.append((str(it), False))
            targets = [(lid, chip) for lid, chip in targets if lid]
            if not targets:
                self._send_json({"ok": False, "error": "Нет лотов для изменения"})
                return
            fields = {
                "summary_ru": req_data.get("summary"),
                "desc_ru": req_data.get("desc"),
                "payment_msg_ru": req_data.get("payment_msg"),
            }
            # base_* — текст лота-образца «до» (для умной замены: у каждого
            # лота меняется только изменённый кусок, остальное остаётся его)
            bases = {
                "summary_ru": req_data.get("base_summary"),
                "desc_ru": req_data.get("base_desc"),
                "payment_msg_ru": req_data.get("base_payment_msg"),
            }
            fields = {k: ("" if v is None else str(v)) for k, v in fields.items()
                      if v is not None}
            bases = {k: ("" if v is None else str(v)) for k, v in bases.items()
                     if v is not None and k in fields}
            if not fields:
                self._send_json({"ok": False,
                                 "error": "Не выбрано ни одного поля для изменения"})
                return
            with _BULK_LOCK:
                if _EDIT_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Редактирование уже идёт"})
                    return
                _EDIT_TASK.update({"running": True, "total": len(targets), "done": 0,
                                   "edited": 0, "errors": [], "message": "",
                                   "finished": False, "current": "", "stop": False})
            t = _threading.Thread(target=self._safe_run_edit_task,
                                  args=(targets, fields, bases, self._read_config()), daemon=True)
            t.start()
            self._send_json({"ok": True, "started": True, "total": len(targets),
                             "message": f"Изменение запущено: {len(targets)} лотов"})
            return

        # Удаление лотов (фоном, с прогрессом) — БЕЗВОЗВРАТНО
        if path == "/api/lots/delete":
            items = req_data.get("lot_ids", []) or []
            targets = []
            for it in items[:200]:
                if isinstance(it, dict):
                    targets.append((str(it.get("lot_id", "")), bool(it.get("is_chip", False))))
                else:
                    targets.append((str(it), False))
            targets = [(lid, chip) for lid, chip in targets if lid]
            if not targets:
                self._send_json({"ok": False, "error": "Нет лотов для удаления"})
                return
            with _BULK_LOCK:
                if _DEL_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Удаление уже идёт"})
                    return
                _DEL_TASK.update({"running": True, "total": len(targets), "done": 0,
                                  "deleted": 0, "errors": [], "message": "",
                                  "finished": False, "current": "", "stop": False})
            t = _threading.Thread(target=self._safe_run_delete_task,
                                  args=(targets, self._read_config()), daemon=True)
            t.start()
            self._send_json({"ok": True, "started": True, "total": len(targets),
                             "message": f"Удаление запущено: {len(targets)} лотов"})
            return

        # Legacy: создание лотов из выбранных офферов (чинит 404 — раньше было только в do_GET)
        if path == "/api/lots/create":
            config = self._read_config()
            result = self._create_lots_from_selection(req_data, config)
            self._send_json(result)
            return

        # Новое: массовое выставление одного лота во много разделов
        if path == "/api/lots/create-bulk":
            config = self._read_config()
            result = self._create_bulk_lots(req_data, config)
            self._send_json(result)
            return

        # Вернуть снятые с продажи лоты обратно (фоном — их может быть сотня)
        if path == "/api/lots/reactivate":
            items = req_data.get("lot_ids", []) or []
            targets = []
            for it in items[:500]:
                if isinstance(it, dict):
                    targets.append((str(it.get("lot_id", "")), bool(it.get("is_chip", False))))
                else:
                    targets.append((str(it), False))
            targets = [(lid, chip) for lid, chip in targets if lid]
            if not targets:
                self._send_json({"ok": False, "error": "Нет лотов для возврата"})
                return
            with _BULK_LOCK:
                if _DUP_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Чистка уже идёт, дождитесь"})
                    return
                _DUP_TASK.update({"running": True, "total": len(targets), "done": 0,
                                  "removed": 0, "errors": [], "message": "",
                                  "finished": False, "current": "", "stop": False})
            t = _threading.Thread(target=self._run_reac_task,
                                  args=(targets, self._read_config()), daemon=True)
            t.start()
            self._send_json({"ok": True, "started": True, "total": len(targets),
                             "message": f"Возврат запущен: {len(targets)} лотов. Панель остаётся живой."})
            return

        # Уборка дубликатов: точная проверка по типу перед снятием.
        # Дубль = один раздел + одинаковое название + одинаковая цена + одинаковый тип.
        # Похожие, но разные — не трогаем. Сами лоты не удаляются.
        if path == "/api/lots/duplicates/cleanup":
            groups = req_data.get("groups", []) or []
            if not groups and isinstance(req_data.get("lot_ids", []), list) and req_data.get("lot_ids"):
                groups = [{"items": req_data["lot_ids"]}]
            if not groups:
                self._send_json({"ok": False, "error": "Нет лотов для проверки"})
                return
            with _BULK_LOCK:
                if _DUP_TASK.get("running"):
                    self._send_json({"ok": False, "error": "Чистка уже идёт"})
                    return
                total_members = 0
                for g in groups:
                    items = g.get("items", g) if isinstance(g, dict) else g
                    total_members += len(items or [])
                _DUP_TASK.update({"running": True, "total": total_members, "done": 0,
                                  "removed": 0, "errors": [], "message": "",
                                  "finished": False, "current": "", "stop": False})
            t = _threading.Thread(target=self._safe_run_dup_task,
                                  args=(groups, self._read_config()), daemon=True)
            t.start()
            self._send_json({"ok": True, "started": True, "total": total_members,
                             "message": f"Чистка запущена: {total_members} лотов. Панель остаётся живой."})
            return

        if path == "/api/lots/duplicates/stop":
            with _BULK_LOCK:
                if not _DUP_TASK.get("stop"):
                    self._send_json({"ok": False, "error": "Чистка не запущена"})
                else:
                    _DUP_TASK["stop"] = True
                    self._send_json({"ok": True, "message": "Останавливаю после текущего лота..."})
            return

        # API: Склад — сохранение (текстовый формат из панели)
        if path == "/api/orders/stock":
            lots_text = str(req_data.get("lots_text", "") or "")
            gifts_text = str(req_data.get("gifts_text", "") or "")
            lots: Dict[str, List[str]] = {}
            cur: Optional[str] = None
            for line in lots_text.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    m = re.match(r"#\s*ключ\s*:\s*(.+)", s, re.I)
                    if m and m.group(1).strip():
                        cur = m.group(1).strip()
                        lots.setdefault(cur, [])
                    continue
                if cur is None:
                    cur = "default"
                    lots.setdefault(cur, [])
                lots[cur].append(s)
            gifts = [l.strip() for l in gifts_text.splitlines() if l.strip()]
            stock = {"lots": lots, "gifts": gifts}
            try:
                with open("stock.json", "w", encoding="utf-8") as f:
                    json.dump(stock, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self._send_json({"ok": False, "error": f"Не сохранилось: {e}"})
                return
            self._send_json({"ok": True, "lots": {k: len(v) for k, v in lots.items()},
                             "gifts": len(gifts)})
            return


        # Шаблоны лотов / шаблоны текста / текстовые инструменты (POST и DELETE)
        if self._templates_api(path, req_data):
            return
        self.send_error(404, "Not Found")

    # DELETE-запросы (удаление шаблонов) обрабатывает та же логика, что и POST
    do_DELETE = do_POST

    def _templates_api(self, path: str, req_data: dict) -> bool:
        """Шаблоны лотов + шаблоны текста + текстовые инструменты.
        Вызывается из do_GET, do_POST и do_DELETE. Возвращает True,
        если запрос обработан."""
        # === TEXT: ГЕНЕРАТОР / УЛУЧШЕНИЕ / ПЕРЕВОД ===
        if path == "/api/text/generate":
            if self._require_auth():
                return True
            idea = (req_data.get("idea") or "").strip()
            features = (req_data.get("features") or "").strip()
            style = (req_data.get("style") or "").strip()
            if not idea:
                self._send_json({"ok": False, "error": "Напиши, что продаём (краткая идея заголовка)"})
                return True
            if not features:
                self._send_json({"ok": False, "error": "Напиши ключевые особенности для описания"})
                return True
            out = _generate_lot_texts(idea, features, style,
                                      buyer_msg=bool(req_data.get("buyer_msg")),
                                      category=(req_data.get("category") or "Другое").strip())
            result = {"ok": True, **out, "warning": ""}
            if req_data.get("translate"):
                ok_t, title_en, err = _translate_block(out["title_ru"], "en")
                ok_d, desc_en, _ = _translate_block(out["desc_ru"], "en")
                result["title_en"] = title_en
                result["desc_en"] = desc_en
                if out["pay_ru"]:
                    ok_p, pay_en, _ = _translate_block(out["pay_ru"], "en")
                    result["pay_en"] = pay_en if ok_p else ""
                else:
                    result["pay_en"] = ""
                if not (ok_t and ok_d):
                    result["warning"] = (err or "Перевод не удался") + ". Английские поля заполнены русским текстом — поправь вручную."
                    if not title_en:
                        result["title_en"] = out["title_ru"]
                    if not desc_en:
                        result["desc_en"] = out["desc_ru"]
            self._send_json(result)
            return True

        if path == "/api/text/improve":
            if self._require_auth():
                return True
            text = (req_data.get("text") or "").strip()
            style = (req_data.get("style") or "").strip()
            if not text:
                self._send_json({"ok": False, "error": "Пустой текст"})
                return True
            improved = _improve_text(text, style)
            self._send_json({"ok": True, "text": improved})
            return True

        if path == "/api/text/translate":
            if self._require_auth():
                return True
            text = (req_data.get("text") or "").strip()
            to = (req_data.get("to") or "en").lower()
            if not text:
                self._send_json({"ok": False, "error": "Пустой текст"})
                return True
            ok, translated, err = _translate_text(text, to)
            self._send_json({"ok": ok, "text": translated if ok else text, "error": err})
            return True

        # === TEMPLATES API ===
        if path == "/api/templates":
            if self._require_auth():
                return True
            method = self.command
            if method == "GET":
                templates = _read_saved_lots()
                self._send_json({"templates": templates})
                return True
            if method == "POST":
                data = req_data  # тело уже прочитано в do_POST/do_DELETE

                # Вариант 1: сохранить СУЩЕСТВУЮЩИЙ лот с FunPay как шаблон
                lot_id = str(data.get("from_lot") or "").strip()
                if lot_id:
                    config = self._read_config()
                    client = FunPayClient(golden_key=config.get("golden_key", ""))
                    details = client.get_lot_details(lot_id)
                    if not details:
                        self._send_json({"ok": False,
                                         "error": "Не удалось открыть лот (проверь ключ или попробуй позже)"})
                        return True
                    lot = _lot_payload_from_details(details, lot_id)
                    templates = _read_saved_lots()
                    template = {
                        "id": _gen_id(),
                        "name": (data.get("name") or "").strip() or lot["summary_ru"] or f"Лот #{lot_id}",
                        "offer_type": lot["offer_type"],
                        "buyer_price": lot["buyer_price"],
                        "amount": lot["amount"],
                        "server": lot["server"],
                        "summary_ru": lot["summary_ru"],
                        "summary_en": lot["summary_en"],
                        "desc_ru": lot["desc_ru"],
                        "desc_en": lot["desc_en"],
                        "pay_ru": lot["prompt_auto"],
                        "pay_en": lot["prompt_response"],
                        "secrets": lot["secrets"],
                        "source_lot_id": lot_id,
                        "created_at": time.time(),
                    }
                    templates.append(template)
                    _write_saved_lots(templates)
                    self._send_json({"ok": True, "id": template["id"], "name": template["name"]})
                    return True

                # Вариант 2: сохранить шаблон из заполненной формы
                name = (data.get("name") or "").strip()
                if not name:
                    self._send_json({"ok": False, "error": "Укажите название шаблона"})
                    return True
                templates = _read_saved_lots()
                template = {
                    "id": _gen_id(),
                    "name": name,
                    "offer_type": data.get("offer_type", ""),
                    "buyer_price": float(data.get("buyer_price", 0) or 0),
                    "amount": int(data.get("amount", 1) or 1),
                    "server": data.get("server", ""),
                    "summary_ru": data.get("summary_ru", ""),
                    "summary_en": data.get("summary_en", ""),
                    "desc_ru": data.get("desc_ru", ""),
                    "desc_en": data.get("desc_en", ""),
                    "pay_ru": data.get("pay_ru", ""),
                    "pay_en": data.get("pay_en", ""),
                    "secrets": data.get("secrets", ""),
                    "created_at": time.time(),
                }
                templates.append(template)
                _write_saved_lots(templates)
                self._send_json({"ok": True, "id": template["id"]})
                return True

        if path.startswith("/api/templates/"):
            if self._require_auth():
                return True
            tid = path[len("/api/templates/"):]
            if self.command == "GET":
                templates = _read_saved_lots()
                for t in templates:
                    if t.get("id") == tid:
                        self._send_json({"template": t})
                        return True
                self._send_json({"ok": False, "error": "Шаблон не найден"}, status=404)
                return True
            if self.command == "DELETE":
                templates = _read_saved_lots()
                templates = [t for t in templates if t.get("id") != tid]
                _write_saved_lots(templates)
                self._send_json({"ok": True})
                return True

        # === TEXT TEMPLATES API ===
        if path == "/api/text-templates":
            if self._require_auth():
                return True
            if self.command == "GET":
                templates = _read_text_templates()
                self._send_json({"templates": templates})
                return True
            if self.command == "POST":
                data = req_data  # тело уже прочитано в do_POST/do_DELETE
                name = (data.get("name") or "").strip()
                if not name:
                    self._send_json({"ok": False, "error": "Укажите название"})
                    return True
                templates = _read_text_templates()
                template = {
                    "id": _gen_id(),
                    "name": name,
                    "type": data.get("type", "custom"),
                    "content": data.get("content", ""),
                }
                templates.append(template)
                _write_text_templates(templates)
                self._send_json({"ok": True, "id": template["id"]})
                return True

        if path.startswith("/api/text-templates/"):
            if self._require_auth():
                return True
            tid = path[len("/api/text-templates/"):]
            if self.command == "GET":
                templates = _read_text_templates()
                for t in templates:
                    if t.get("id") == tid:
                        self._send_json({"template": t})
                        return True
                self._send_json({"ok": False, "error": "Не найден"}, status=404)
                return True
            if self.command == "DELETE":
                templates = _read_text_templates()
                templates = [t for t in templates if t.get("id") != tid]
                _write_text_templates(templates)
                self._send_json({"ok": True})
                return True
        return False

    def _safe_run_dup_task(self, groups: List, config: Dict):
        try:
            self._run_dup_task(groups, config)
        except Exception as e:
            logger.error(f"[DUPES] Крах задачи: {e}", exc_info=True)
            with _BULK_LOCK:
                _DUP_TASK.update({"running": False, "finished": True,
                                  "message": f"Внутренняя ошибка: {e}", "errors": [str(e)]})

    def _run_reac_task(self, targets: List[Tuple[str, bool]], config: Dict):
        """Фоновый возврат снятых лотов в продажу."""
        def _progress(**kw):
            with _BULK_LOCK:
                _DUP_TASK.update(kw)

        client = FunPayClient(golden_key=config.get("golden_key", ""))
        auth_ok, _, _, err = client.check_auth()
        if not auth_ok:
            _progress(running=False, finished=True, message=f"Ключ не работает: {err}",
                      errors=[f"Ключ не работает: {err}"])
            return
        ok_n = 0
        done = 0
        stopped = False
        fail = []
        total = len(targets)
        logger.info(f"[REAC] Старт: {total} лотов")
        for lid, is_chip in targets:
            with _BULK_LOCK:
                if _DUP_TASK.get("stop"):
                    stopped = True
                    break
            done += 1
            _progress(done=done, removed=ok_n, errors=list(fail), current=f"#{lid}")
            ok, msg = client.activate_lot(lid, is_chip=is_chip)
            if ok:
                ok_n += 1
                logger.info(f"[DUPES] #{lid}: возвращён в продажу")
            else:
                fail.append(f"#{lid}: {msg}")
            _progress(done=done, removed=ok_n, errors=list(fail))
            time.sleep(2)
        if stopped:
            msg = f"Остановлено: возвращено {ok_n} из {total}"
        else:
            msg = f"Возвращено в продажу: {ok_n} из {total}"
        if fail:
            msg += ". Ошибки: " + "; ".join(fail[:5])
        logger.info(f"[REAC] Финиш: {msg}")
        _progress(running=False, finished=True, message=msg, current="",
                  done=total, removed=ok_n, errors=list(fail))

    def _run_dup_task(self, groups: List, config: Dict):
        """Фоновая чистка дубликатов. Сервер остаётся отзывчивым."""
        def _progress(**kw):
            with _BULK_LOCK:
                _DUP_TASK.update(kw)

        def _norm_title(t: str) -> str:
            return re.sub(r"[^a-zа-яё0-9]", "", (t or "").lower())

        client = FunPayClient(golden_key=config.get("golden_key", ""))
        auth_ok, _, _, err = client.check_auth()
        if not auth_ok:
            _progress(running=False, finished=True, message=f"Ключ не работает: {err}",
                      errors=[f"Ключ не работает: {err}"])
            return

        fetched = 0
        done = 0
        ok_n, unverified, already_off = 0, 0, 0
        stopped = False
        fail = []
        total = 0
        for g in groups:
            items = g.get("items", g) if isinstance(g, dict) else g
            total += len(items or [])
        logger.info(f"[DUPES] Старт: {total} кандидатов")
        for g in groups:
            if stopped:
                break
            items = g.get("items", g) if isinstance(g, dict) else g
            gnode = ""
            for it in items or []:
                if isinstance(it, dict) and str(it.get("node", "")):
                    gnode = str(it.get("node", ""))
                    break
            members = []
            for it in items or []:
                if isinstance(it, dict):
                    members.append((str(it.get("lot_id", "")),
                                    bool(it.get("is_chip", False)),
                                    str(it.get("title", "")),
                                    it.get("price", 0)))
                else:
                    members.append((str(it), False, "", 0))
            members = [(lid, chip, t, p) for lid, chip, t, p in members if lid]
            if len(members) < 2:
                done += len(members)
                _progress(done=done)
                continue
            exact: Dict[str, List] = {}
            for lid, chip, t, p in members:
                with _BULK_LOCK:
                    if _DUP_TASK.get("stop"):
                        stopped = True
                        break
                done += 1
                _progress(done=done, removed=ok_n, errors=list(fail),
                          current=f"#{lid}")
                if fetched >= 60:
                    unverified += 1
                    continue
                fetched += 1
                try:
                    d = client.get_lot_details(lid)
                except Exception:
                    d = None
                if not d:
                    unverified += 1  # не смогли проверить — не трогаем
                    continue
                etype = (((d.get("form_data", {}) or {}).get("fields[type]", "")) or "").strip().lower()
                is_active = bool(((d.get("form_data", {}) or {}).get("active", "") or ""))
                key = _norm_title(t) + "|" + etype
                exact.setdefault(key, []).append((lid, chip, t, p, is_active))
            if stopped:
                break
            for key, lst in exact.items():
                actives = [m for m in lst if m[4]]
                already_off += len(lst) - len(actives)
                if len(actives) < 2:
                    continue
                lst_sorted = sorted(actives, key=lambda x: float(x[3] or 0))
                for lid, chip, t, p, _ in lst_sorted[1:]:
                    with _BULK_LOCK:
                        if _DUP_TASK.get("stop"):
                            stopped = True
                            break
                    ok, msg = client.deactivate_lot(lid, is_chip=chip)
                    if ok:
                        ok_n += 1
                        logger.info(f"[DUPES] #{lid}: снят с продажи (точный дубль)")
                        _registry_add([{"lot_id": lid, "node": gnode, "title": t,
                                        "type": key.split("|")[-1], "ts": time.time()}])
                    else:
                        fail.append(f"#{lid}: {msg}")
                    _progress(done=done, removed=ok_n, errors=list(fail))
                    time.sleep(2)
        if stopped:
            msg = f"Остановлено: точных дублей снято {ok_n}"
        else:
            msg = f"Точных дублей снято: {ok_n}"
        if already_off:
            msg += f". Уже сняты ранее: {already_off}"
        if unverified:
            msg += f". Не проверено (лимит): {unverified} — повтори кнопку"
        if fail:
            msg += ". Ошибки: " + "; ".join(fail[:5])
        logger.info(f"[DUPES] Финиш: {msg}")
        _progress(running=False, finished=True, message=msg, current="",
                  done=total, removed=ok_n, errors=list(fail))

    def _read_config(self) -> Dict[str, Any]:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self, config: Dict[str, Any]):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения {CONFIG_FILE}: {e}")

    def _require_auth(self) -> bool:
        """Необязательная защита панели паролем.
        Пароль берётся из config.panel_password ИЛИ из переменной окружения
        PANEL_PASSWORD (важно на хостинге — Render/Bothost, где config.json
        нет, а панель открыта в интернет). Пароль не задан — панель открыта
        (локальная сеть). Возвращает True, если доступ ЗАПРЕЩЁН
        (ответ уже отправлен)."""
        try:
            cfg = self._read_config()
        except Exception:
            cfg = {}
        pwd = (cfg.get("panel_password") or "").strip()
        if not pwd:
            try:
                pwd = (PANEL_PASSWORD or "").strip()
            except Exception:
                pwd = ""
        if not pwd:
            return False
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = (qs.get("token", [""])[0] or self.headers.get("X-Panel-Token") or "").strip()
        if token == pwd:
            return False
        self._send_json({"ok": False, "error": "Требуется пароль панели (?token=...)"},
                        status=401)
        return True

    def _get_categories(self, client: FunPayClient) -> List[Dict]:
        """Получает список категорий с FunPay"""
        categories = []
        try:
            resp = client.session.get(client.BASE_URL + "/lots/", timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                game_links = soup.find_all("a", href=re.compile(r"/lots/\d+"))
                seen_nodes = set()
                for link in game_links[:30]:
                    href = link.get("href", "")
                    match = re.search(r"/lots/(\d+)", href)
                    if match:
                        node_id = match.group(1)
                        if node_id not in seen_nodes:
                            seen_nodes.add(node_id)
                            name = link.get_text(strip=True) or link.get("title", "")
                            if name and len(name) < 50:
                                categories.append({
                                    "node_id": node_id,
                                    "name": name,
                                    "url": f"{client.BASE_URL}/lots/{node_id}/"
                                })
        except Exception as e:
            logger.warning(f"Ошибка получения категорий с FunPay: {e}")

        if not categories:
            categories = [
                {"node_id": "402", "name": "Roblox", "url": "https://funpay.com/lots/402/"},
                {"node_id": "459", "name": "Minecraft", "url": "https://funpay.com/lots/459/"},
                {"node_id": "491", "name": "Steam", "url": "https://funpay.com/lots/491/"},
                {"node_id": "526", "name": "Genshin Impact", "url": "https://funpay.com/lots/526/"},
                {"node_id": "503", "name": "Valorant", "url": "https://funpay.com/lots/503/"},
            ]
        return categories

    def _get_category_offers(self, client: FunPayClient, node_id: str) -> List[Dict]:
        """Получает офферы категории"""
        offers = []
        try:
            url = f"{client.BASE_URL}/lots/{node_id}/"
            resp = client.session.get(url, timeout=15)
            if resp.status_code != 200:
                url = f"{client.BASE_URL}/chips/{node_id}/"
                resp = client.session.get(url, timeout=15)

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.find_all("a", class_=re.compile(r"tc-item"))

                for item in items[:50]:
                    href = item.get("href", "")
                    offer_id_match = re.search(r"[?&]id=(\d+)", href)
                    if not offer_id_match:
                        continue
                    offer_id = offer_id_match.group(1)

                    user_block = item.find(class_=re.compile(r"media-user"))
                    if user_block:
                        data_user = user_block.get("data-user")
                        if data_user and str(data_user) == str(client.user_id):
                            continue

                    desc_el = item.find(class_=re.compile(r"tc-desc-text|tc-desc"))
                    title = desc_el.get_text(strip=True) if desc_el else item.get_text(strip=True)[:100]

                    if not title:
                        continue

                    price_el = item.find(class_=re.compile(r"tc-price"))
                    price = 0.0
                    if price_el:
                        price_text = price_el.get_text(strip=True)
                        price = client.parse_price(price_text)

                    server_el = item.find(class_=re.compile(r"tc-server"))
                    server = server_el.get_text(strip=True) if server_el else ""

                    side_el = item.find(class_=re.compile(r"tc-side"))
                    side = side_el.get_text(strip=True) if side_el else ""

                    sub_el = item.find(class_=re.compile(r"tc-subcategory"))
                    subcategory = sub_el.get_text(strip=True) if sub_el else ""

                    offers.append({
                        "offer_id": offer_id,
                        "title": title,
                        "price": price,
                        "server": server,
                        "side": side,
                        "subcategory": subcategory,
                    })
        except Exception as e:
            logger.warning(f"Ошибка получения офферов категории {node_id}: {e}")
        return offers

    def _get_category_offers_by_url(self, client: FunPayClient, url: str) -> List[Dict]:
        """Офферы по прямому URL раздела (lots/xxx или chips/xxx)."""
        offers: List[Dict] = []
        try:
            resp = client.session.get(url, timeout=15)
            if resp.status_code != 200:
                return offers
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("a", class_=re.compile(r"tc-item"))
            for item in items[:50]:
                href = item.get("href", "")
                m = re.search(r"[?&]id=(\d+)", href)
                if not m:
                    continue
                offer_id = m.group(1)
                desc_el = item.find(class_=re.compile(r"tc-desc-text|tc-desc"))
                title = desc_el.get_text(strip=True) if desc_el else item.get_text(strip=True)[:100]
                if not title:
                    continue
                price_el = item.find(class_=re.compile(r"tc-price"))
                price = client.parse_price(price_el.get_text(strip=True)) if price_el else 0.0
                server_el = item.find(class_=re.compile(r"tc-server"))
                side_el = item.find(class_=re.compile(r"tc-side"))
                sub_el = item.find(class_=re.compile(r"tc-subcategory"))
                offers.append({
                    "offer_id": offer_id,
                    "title": title,
                    "price": price,
                    "server": server_el.get_text(strip=True) if server_el else "",
                    "side": side_el.get_text(strip=True) if side_el else "",
                    "subcategory": sub_el.get_text(strip=True) if sub_el else "",
                })
        except Exception as e:
            logger.warning(f"Ошибка получения офферов {url}: {e}")
        return offers

    @staticmethod
    def _normalize_lots(req_data: Dict) -> List[Dict]:
        """Приводит payload к списку лотов: новый формат 'lots' или legacy плоские поля."""
        def _norm(it: Dict) -> Dict:
            title = str(it.get("title") or it.get("summary_ru") or "").strip()
            try:
                bp = float(it.get("buyer_price", 0) or 0)
            except Exception:
                bp = 0
            try:
                sp = float(it.get("seller_price", 0) or 0)
            except Exception:
                sp = 0
            try:
                amount = int(it.get("amount", 1) or 1)
            except Exception:
                amount = 1
            return {
                "title": title, "buyer_price": bp, "seller_price": sp,
                "side": str(it.get("side", "") or "").strip(),
                "subcategory": str(it.get("subcategory", "") or "").strip(),
                "amount": amount,
                "server": str(it.get("server", "") or "").strip(),
                "summary_ru": str(it.get("summary_ru", "") or "").strip() or title,
                "summary_en": str(it.get("summary_en", "") or "").strip() or title,
                "desc_ru": str(it.get("desc_ru", "") or "").strip(),
                "desc_en": str(it.get("desc_en", "") or "").strip(),
                "pay_ru": str(it.get("pay_ru", "") or "").strip(),
                "pay_en": str(it.get("pay_en", "") or "").strip(),
                "offer_type": str(it.get("offer_type", "") or "").strip(),
                "secrets": str(it.get("secrets", "") or "").strip(),
                "prompt_auto": it.get("prompt_auto", "") or "",
                "prompt_response": it.get("prompt_response", "") or "",
            }

        raw = req_data.get("lots")
        if isinstance(raw, list) and raw:
            lots = [_norm(it) for it in raw if isinstance(it, dict)]
            return [l for l in lots if l["title"] and l["buyer_price"] > 0]
        single = _norm(req_data)
        return [single] if (single["title"] and single["buyer_price"] > 0) else []

    def _create_bulk_lots(self, req_data: Dict, config: Dict) -> Dict:
        """Запускает массовое выставление в фоне и сразу возвращает (прогресс — /api/lots/bulk-status)."""
        chips = req_data.get("chips", []) or []
        lots = self._normalize_lots(req_data)

        if not chips:
            return {"ok": False, "error": "Не выбраны разделы"}
        if not lots:
            return {"ok": False, "error": "Заполни хотя бы один лот (краткое RU + цена)"}

        total = len(lots) * len(chips)
        with _BULK_LOCK:
            if _BULK_TASK.get("running"):
                return {"ok": False, "error": "Выставление уже идёт, дождитесь конца"}
            _BULK_TASK.update({
                "running": True, "total": total, "done": 0, "created": 0,
                "skipped": 0, "errors": [], "message": "", "finished": False,
                "current": "", "stop": False,
            })

        payload = {"chips": chips, "lots": lots, "skip_existing": req_data.get("skip_existing", True)}
        t = _threading.Thread(target=self._safe_run_bulk_task, args=(payload, dict(config)), daemon=True)
        t.start()
        return {"ok": True, "started": True, "total": total,
                "message": f"Выставление запущено: {len(lots)} лота × {len(chips)} разделов"}

    def _safe_run_bulk_task(self, req_data: Dict, config: Dict):
        try:
            self._run_bulk_task(req_data, config)
        except Exception as e:
            logger.error(f"[BULK] Крах задачи: {e}", exc_info=True)
            with _BULK_LOCK:
                _BULK_TASK.update({"running": False, "finished": True,
                                   "message": f"Внутренняя ошибка: {e}", "errors": [str(e)]})

    def _run_bulk_task(self, req_data: Dict, config: Dict):
        """Фоновый воркер массового выставления (несколько лотов × разделы)."""
        chips = req_data.get("chips", []) or []
        lots = self._normalize_lots(req_data)
        if not lots or not chips:
            with _BULK_LOCK:
                _BULK_TASK.update({"running": False, "finished": True,
                                   "message": "Нет лотов или разделов"})
            return

        def _progress(**kw):
            with _BULK_LOCK:
                _BULK_TASK.update(kw)

        golden_key = config.get("golden_key", "")
        client = FunPayClient(golden_key=golden_key)
        auth_ok, user_id, username, err = client.check_auth()
        if not auth_ok:
            _progress(running=False, finished=True, message=f"Ключ не работает: {err}",
                      errors=[f"Ключ не работает: {err}"])
            return

        commission_ratio = 1.223577

        # Пропуск разделов, где такой лот уже выставлен
        skip_existing = req_data.get("skip_existing", True)
        if isinstance(skip_existing, str):
            skip_existing = skip_existing.lower() not in ("0", "false", "no")
        skip_existing = bool(skip_existing)
        auto_clean = req_data.get("auto_clean", True)
        if isinstance(auto_clean, str):
            auto_clean = auto_clean.lower() not in ("0", "false", "no")
        auto_clean = bool(auto_clean)
        # Свои офферы в разделе — истина со страницы раздела.
        # (Профиль группирует всё в одну ноду игры и для проверки дублей не годится.)
        section_own_cache: Dict[str, Optional[List[Tuple[str, str]]]] = {}

        def _section_page_url(node: str, url: str, kind: str) -> str:
            if url and re.search(r"/(chips|lots)/\d+", url):
                return url
            k = kind if kind in ("lots", "chips") else "lots"
            return f"{client.BASE_URL}/{k}/{node}/"

        def _section_own(node: str, url: str, kind: str) -> Optional[List[Tuple[str, str]]]:
            """Свои офферы раздела [(offer_id, title)]. None = не загрузилось."""
            key = f"{kind}:{node}"
            if key not in section_own_cache:
                try:
                    section_own_cache[key] = client.get_section_own_offers(
                        _section_page_url(node, url, kind))
                except Exception as e:
                    logger.warning(f"Не удалось проверить раздел {node}: {e}")
                    section_own_cache[key] = None
            return section_own_cache[key]
        # локальная история выставлений (надёжнее парсинга профиля)
        history: List[Dict] = []
        if skip_existing and os.path.exists(BULK_HISTORY_FILE):
            try:
                with open(BULK_HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f) or []
            except Exception:
                history = []

        def _history_match(node: str, tokens: List[str], offer_type: str = "") -> bool:
            # Быстрый префильтр по истории прошлых запусков.
            # Решает страница раздела (она истина): совпадение истории
            # всё равно перепроверяется живой страницей ниже.
            if not tokens:
                return False
            for h in history:
                if str(h.get("node", "")) != str(node):
                    continue
                ht = str(h.get("title", "")).lower()
                if not any(tok in ht for tok in tokens):
                    continue
                ht_type = str(h.get("offer_type", "") or "").strip().lower()
                ot = (offer_type or "").strip().lower()
                if ot and ht_type and ot != ht_type:
                    continue  # такой же текст, но другой тип — не дубль
                return True
            return False

        def _history_add(node: str, title: str, offer_type: str = ""):
            try:
                hist = []
                if os.path.exists(BULK_HISTORY_FILE):
                    with open(BULK_HISTORY_FILE, "r", encoding="utf-8") as f:
                        hist = json.load(f) or []
                tkey = str(title).strip().lower()
                hist = [h for h in hist
                        if not (str(h.get("node", "")) == str(node)
                                and str(h.get("title", "")).strip().lower() == tkey)]
                hist.append({"node": str(node), "title": title,
                             "offer_type": offer_type, "ts": time.time()})
                with open(BULK_HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(hist[-500:], f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Не удалось записать историю: {e}")

        _etype_cache: Dict[str, Optional[str]] = {}

        def _lot_etype(lid: str) -> Optional[str]:
            """Тип живого лота с кэшем (None = не смогли узнать)."""
            if lid not in _etype_cache:
                try:
                    d = client.get_lot_details(lid)
                except Exception:
                    d = None
                if not d:
                    _etype_cache[lid] = None
                else:
                    _etype_cache[lid] = ((((d.get("form_data", {}) or {})
                                           .get("fields[type]", "")) or "").strip().lower() or "")
            return _etype_cache[lid]

        def _norm_text(s: str) -> str:
            """Нормализация для сравнения: нижний регистр, без эмодзи/пунктуации."""
            s = re.sub(r"[^\w\s]", " ", (s or "").lower(), flags=re.U)
            return re.sub(r"\s+", " ", s).strip()

        def _tok_set(s: str) -> Set[str]:
            return set(w for w in re.findall(r"[a-zа-яё0-9]{4,}", (s or "").lower()))

        def _same_offer(lot_texts: List[str], page_text: str) -> bool:
            """Тот же лот: точное совпадение видимого описания
            или высокое пересечение слов (одно общее слово вроде
            «роблокс» — не повод)."""
            norm_page = _norm_text(page_text)
            if not norm_page:
                return False
            for t in lot_texts:
                if _norm_text(t) == norm_page:
                    return True
            ta: Set[str] = set()
            for t in lot_texts:
                ta |= _tok_set(t)
            tb = _tok_set(page_text)
            if not ta or not tb:
                return False
            common = ta & tb
            if len(common) < 2:
                return False
            return len(common) / min(len(ta), len(tb)) >= 0.5

        def _already_listed(node: str, url: str, kind: str,
                            lot: Dict[str, Any], offer_type: str = "") -> bool:
            """Проверка по живой странице раздела: тот же раздел + то же
            видимое описание (название/краткие RU/EN) + тот же тип.
            Авто (без типа) — по описанию."""
            own = _section_own(node, url, kind)
            if not own:
                return False
            texts = [lot.get("title", ""), lot.get("summary_ru", ""),
                     lot.get("summary_en", "")]
            cands = [(t, lid) for (lid, t) in own if _same_offer(texts, t)]
            if not cands:
                return False
            ot = (offer_type or "").strip().lower()
            if not ot:
                return True
            for _, lid in cands:
                et = _lot_etype(lid)
                if et is None or et == ot:
                    return True  # не смогли проверить — консервативно скипаем
            return False

        def _type_occupied(node: str, url: str, kind: str, new_type: str) -> bool:
            """Правило занятости: в разделе уже есть такой же ТИП (любое название).
            Неизвестные типы считаем совпадением (консервативно)."""
            if not new_type:
                return False
            nt = new_type.strip().lower()
            own = _section_own(node, url, kind)
            if not own:
                return False
            for lid, _t in own:
                et = _lot_etype(lid)
                if et is None or et == nt:
                    return True
            return False

        created = 0
        skipped = 0
        cleaned_total = 0
        skip_reasons: Dict[str, int] = {}
        pause_base = 5.0
        captcha_hits = 0
        limit_purged = False
        errors = []
        done = 0
        stopped = False
        seen_run: Set[Tuple[str, str]] = set()
        total = len(lots) * len(chips)
        per_lot_msgs = []
        logger.info(f"[BULK] Старт: {len(lots)} лота × {len(chips)} разделов")
        for li, lot in enumerate(lots):
            if stopped:
                break
            title = lot["title"]
            buyer_price = lot["buyer_price"]
            seller_price = lot["seller_price"] if lot["seller_price"] > 0 else buyer_price / commission_ratio
            tokens = [w for w in re.findall(r"[a-zа-яё0-9]{4,}", title.lower())]
            title_key = title.strip().lower()
            lot_created = 0
            lot_skipped = 0
            for chip in chips:
                with _BULK_LOCK:
                    if _BULK_TASK.get("stop"):
                        stopped = True
                        break
                node = str(chip.get("node", "") or "").strip()
                url = str(chip.get("url", "") or "")
                cname = str(chip.get("name", "") or node)
                kind = str(chip.get("kind", "") or "").strip().lower()
                if not node and url:
                    m = re.search(r"/(chips|lots)/(\d+)", url)
                    if m:
                        kind = kind or m.group(1)
                        node = m.group(2)
                if kind not in ("lots", "chips"):
                    kind = "chips" if "/chips/" in url else ("lots" if "/lots/" in url else "")
                done += 1
                _progress(done=done, created=created, skipped=skipped, errors=list(errors),
                          current=f"Лот {li + 1}/{len(lots)} · {cname}")
                if not node:
                    errors.append(f"[Лот {li + 1}] {cname}: нет node_id")
                    _progress(done=done, created=created, skipped=skipped, errors=list(errors))
                    continue
                if (node, title_key) in seen_run:
                    skipped += 1
                    lot_skipped += 1
                    skip_reasons["дубль в этом запуске"] = skip_reasons.get("дубль в этом запуске", 0) + 1
                    logger.info(f"[BULK] [Лот {li + 1}] {cname}: пропуск (дубль в этом запуске)")
                    _progress(done=done, created=created, skipped=skipped, errors=list(errors))
                    continue
                seen_run.add((node, title_key))
                if auto_clean:
                    n_clean = self._autoclean_node(client, node, title, tokens, lot["offer_type"])
                    if n_clean:
                        cleaned_total += n_clean
                skip_reason = ""
                if skip_existing:
                    # история — быстрый префильтр, страница раздела — истина
                    if _history_match(node, tokens, lot["offer_type"]):
                        skip_reason = "уже выставлен"
                    elif _already_listed(node, url, kind, lot, lot["offer_type"]):
                        skip_reason = "уже выставлен"
                    else:
                        # правило занятости: такой же ТИП в разделе уже есть (название любое).
                        # Для Авто тип заранее не резолвим (лишние запросы = капча) —
                        # там решает совпадение названия + сама форма при создании.
                        ot = (lot["offer_type"] or "").strip()
                        if ot and _type_occupied(node, url, kind, ot):
                            skip_reason = f"там уже есть «{ot}»"
                if skip_reason:
                    skipped += 1
                    lot_skipped += 1
                    skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
                    logger.info(f"[BULK] [Лот {li + 1}] {cname}: пропуск ({skip_reason})")
                    _progress(done=done, created=created, skipped=skipped, errors=list(errors))
                    continue
                ok, msg = client.create_lot(
                    node_id=node,
                    title=title,
                    seller_price=seller_price,
                    summary_ru=lot["summary_ru"],
                    summary_en=lot["summary_en"],
                    desc_ru=lot["desc_ru"],
                    desc_en=lot["desc_en"],
                    payment_msg_ru=lot["pay_ru"],
                    payment_msg_en=lot["pay_en"],
                    offer_type=lot["offer_type"],
                    server=lot["server"],
                    side=lot["side"],
                    subcategory=lot["subcategory"],
                    prompt_auto=lot["prompt_auto"],
                    prompt_response=lot["prompt_response"],
                    amount=lot["amount"],
                    secrets=lot["secrets"],
                    section_name=cname,
                    section_kind=kind,
                )
                if not ok and msg.startswith("CAPTCHA"):
                    # Защита FunPay от частых запросов. Долбить раздел каждые 90с
                    # бесполезно: бан только продлевается, а 72 раздела
                    # превращаются в 2 часа ожидания. Поэтому: пауза с бэкоффом
                    # (прерываемая кнопкой Стоп) + одна повторная попытка.
                    # Три капчи подряд = стоп задачи; продолжение — повторным
                    # запуском (выставленное пропустится автоматом).
                    captcha_hits += 1
                    if captcha_hits >= 3:
                        stopped = True
                        msg = (f"Стоп: FunPay включил защиту от частых запросов "
                               f"({captcha_hits} подряд). Выставлено: {created}, "
                               f"пропущено: {skipped}. Подожди 15–20 минут и запусти "
                               f"снова — выставленное пропустится автоматом.")
                        errors.append(f"[Лот {li + 1}] {cname}: {msg}")
                        logger.warning(f"[BULK] {msg}")
                        _progress(done=done, created=created, skipped=skipped, errors=list(errors))
                        break
                    wait_s = 180 if captcha_hits == 1 else 300
                    logger.warning(f"[BULK] [Лот {li + 1}] {cname}: защита FunPay #{captcha_hits}, "
                                   f"пауза {wait_s // 60} мин (Стоп срабатывает сразу), затем 1 повтор")
                    _progress(done=done, created=created, skipped=skipped, errors=list(errors),
                              current=f"Лот {li + 1}/{len(lots)} · {cname} (защита FunPay, пауза {wait_s // 60} мин)")
                    for _ in range(wait_s):
                        with _BULK_LOCK:
                            if _BULK_TASK.get("stop"):
                                stopped = True
                                break
                        time.sleep(1)
                    if stopped:
                        break
                    ok, msg = client.create_lot(
                        node_id=node,
                        title=title,
                        seller_price=seller_price,
                        summary_ru=lot["summary_ru"],
                        summary_en=lot["summary_en"],
                        desc_ru=lot["desc_ru"],
                        desc_en=lot["desc_en"],
                        payment_msg_ru=lot["pay_ru"],
                        payment_msg_en=lot["pay_en"],
                        offer_type=lot["offer_type"],
                        server=lot["server"],
                        side=lot["side"],
                        subcategory=lot["subcategory"],
                        prompt_auto=lot["prompt_auto"],
                        prompt_response=lot["prompt_response"],
                        amount=lot["amount"],
                        secrets=lot["secrets"],
                        section_name=cname,
                        section_kind=kind,
                    )
                    if not ok and msg.startswith("CAPTCHA"):
                        captcha_hits += 1
                    else:
                        captcha_hits = 0
                else:
                    captcha_hits = 0
                if not ok and "максимум предложений" in msg and auto_clean and not limit_purged:
                    # Лимит игры: сносим ВСЕ известные выключенные (реестр+лог, все разделы),
                    # затем одна повторная попытка в этот раздел.
                    limit_purged = True
                    logger.warning(f"[BULK] Лимит игры у {cname}: чищу выключенные по всем разделам")
                    _progress(done=done, created=created, skipped=skipped, errors=list(errors),
                              current=f"Лот {li + 1}/{len(lots)} · {cname} (чистка выключенных...)")
                    with _BULK_LOCK:
                        bulk_stopped = bool(_BULK_TASK.get("stop"))
                    if not bulk_stopped:
                        res = _purge_inactive_core(client)
                        cleaned_total += res["deleted"]
                        logger.warning(f"[BULK] Чистка: удалено {res['deleted']} из {res['total']} "
                                       f"(активны: {res['active_skip']}) — повторяю {cname}")
                        _progress(done=done, created=created, skipped=skipped, errors=list(errors),
                                  current=f"Лот {li + 1}/{len(lots)} · {cname} (повтор после чистки)")
                        ok, msg = client.create_lot(
                            node_id=node,
                            title=title,
                            seller_price=seller_price,
                            summary_ru=lot["summary_ru"],
                            summary_en=lot["summary_en"],
                            desc_ru=lot["desc_ru"],
                            desc_en=lot["desc_en"],
                            payment_msg_ru=lot["pay_ru"],
                            payment_msg_en=lot["pay_en"],
                            offer_type=lot["offer_type"],
                            server=lot["server"],
                            side=lot["side"],
                            subcategory=lot["subcategory"],
                            prompt_auto=lot["prompt_auto"],
                            prompt_response=lot["prompt_response"],
                            amount=lot["amount"],
                            secrets=lot["secrets"],
                            section_name=cname,
                            section_kind=kind,
                        )
                        if not ok and "максимум предложений" in msg:
                            if res["deleted"]:
                                msg += (f" (выключенные снёс: {res['deleted']}, но мест всё равно нет — "
                                        f"удали лишние АКТИВНЫЕ вручную)")
                            else:
                                msg += (" (выключенных не нашёл — все известные активны, "
                                        "удали лишние вручную)")
                    else:
                        stopped = True
                if ok:
                    created += 1
                    lot_created += 1
                    logger.info(f"[BULK] [Лот {li + 1}] {cname}: ✓ выставлен")
                    _history_add(node, title, lot["offer_type"])
                elif msg.startswith("§SKIP§"):
                    skipped += 1
                    lot_skipped += 1
                    reason = msg.replace("§SKIP§", "").strip()
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    logger.info(f"[BULK] [Лот {li + 1}] {cname}: пропуск ({reason})")
                else:
                    errors.append(f"[Лот {li + 1}] {cname}: {msg}")
                    logger.warning(f"[BULK] [Лот {li + 1}] {cname}: ✗ {msg}")
                _progress(done=done, created=created, skipped=skipped, errors=list(errors))
                time.sleep(max(2.5, pause_base) + random.uniform(0, 3.0))
            per_lot_msgs.append(
                f"Лот {li + 1} («{title[:40]}»): выставлено {lot_created}, пропущено {lot_skipped}")

        if stopped:
            msg = f"Остановлено пользователем: выставлено {created}, пропущено {skipped}"
            if errors:
                msg += f". Ошибок: {len(errors)}: " + "; ".join(errors[:5])
        elif created or skipped:
            msg = " | ".join(per_lot_msgs)
            if errors:
                msg += f". Ошибок: {len(errors)}: " + "; ".join(errors[:5])
        else:
            msg = "; ".join(errors[:5]) if errors else "Не выставлено ни в один раздел"
            if errors and len(errors) > 5:
                msg += f" (и ещё {len(errors) - 5})"
        if skip_reasons:
            msg += ". Причины пропусков: " + ", ".join(f"{k} — {v}" for k, v in skip_reasons.items())
        if cleaned_total:
            msg += f". Очищено выключенных: {cleaned_total}"
        logger.info(f"[BULK] Финиш: {msg}")
        _progress(running=False, finished=True, message=msg, current="",
                  done=total, created=created, skipped=skipped, errors=list(errors))

    def _safe_run_delete_task(self, targets: List[Tuple[str, bool]], config: Dict):
        try:
            self._run_delete_task(targets, config)
        except Exception as e:
            logger.error(f"[DEL] Крах задачи: {e}", exc_info=True)
            with _BULK_LOCK:
                _DEL_TASK.update({"running": False, "finished": True,
                                  "message": f"Внутренняя ошибка: {e}", "errors": [str(e)]})

    def _safe_run_edit_task(self, targets: List[Tuple[str, bool]],
                            fields: Dict[str, str], bases: Dict[str, str],
                            config: Dict):
        try:
            self._run_edit_task(targets, fields, bases, config)
        except Exception as e:
            logger.error(f"[EDIT] Крах задачи: {e}", exc_info=True)
            with _BULK_LOCK:
                _EDIT_TASK.update({"running": False, "finished": True,
                                   "message": f"Внутренняя ошибка: {e}", "errors": [str(e)]})

    def _run_edit_task(self, targets: List[Tuple[str, bool]],
                       fields: Dict[str, str], bases: Dict[str, str],
                       config: Dict):
        """Массовое изменение текстовых полей у выбранных лотов.

        Умная замена: если передан «base» (текст лота-образца до правки),
        у каждого лота меняется только изменённый кусок — его собственный
        текст (названия, цены, формулировки) сохраняется. Небольшие отличия
        текста лота прощаются (нечёткий поиск куска).
        """
        def _progress(**kw):
            with _BULK_LOCK:
                _EDIT_TASK.update(kw)

        client = FunPayClient(golden_key=config.get("golden_key", ""))
        auth_ok, _, _, err = client.check_auth()
        if not auth_ok:
            _progress(running=False, finished=True,
                      message=f"Ключ не работает: {err}", errors=[f"Ключ не работает: {err}"])
            return
        logger.info(f"[EDIT] Старт: {len(targets)} лотов, поля: {list(fields.keys())}, "
                    f"умная замена: {bool(bases)}")
        _progress(message="Изменяю...", started=time.time())

        what = []
        if "summary_ru" in fields:
            what.append("краткое")
        if "desc_ru" in fields:
            what.append("описание")
        if "payment_msg_ru" in fields:
            what.append("сообщение после оплаты")

        # Строим функции умной замены для полей, у которых есть «base»
        args: Dict[str, Any] = {}
        for f, new_text in fields.items():
            base = bases.get(f)
            args[f] = _smart_patcher(base, new_text) if base is not None else new_text

        done = edited = 0
        errors: List[str] = []
        for lot_id, is_chip in targets:
            with _BULK_LOCK:
                if _EDIT_TASK.get("stop"):
                    break
            try:
                ok, msg = client.update_lot_fields(
                    lot_id, is_chip=is_chip,
                    summary_ru=args.get("summary_ru"),
                    desc_ru=args.get("desc_ru"),
                    payment_msg_ru=args.get("payment_msg_ru"))
                if ok:
                    edited += 1
                    logger.info(f"[EDIT] #{lot_id}: {msg}")
                else:
                    errors.append(f"#{lot_id}: {msg}")
                    logger.warning(f"[EDIT] #{lot_id}: {msg}")
            except Exception as e:
                errors.append(f"#{lot_id}: {e}")
                logger.error(f"[EDIT] #{lot_id}: {e}")
            done += 1
            _progress(done=done, edited=edited,
                      current=f"#{lot_id}",
                      message=f"Изменено {edited} из {len(targets)} ({', '.join(what)})")
            # Пауза между лотами — защита от антифрода FunPay
            time.sleep(random.uniform(2.0, 4.0))

        stopped = False
        with _BULK_LOCK:
            stopped = bool(_EDIT_TASK.get("stop"))
        final_msg = (f"Остановлено: изменено {edited} из {len(targets)}"
                     if stopped else
                     (f"Готово: изменено {edited} из {len(targets)}"
                      if edited == len(targets) else
                      f"Готово с ошибками: изменено {edited} из {len(targets)}"))
        _progress(running=False, finished=True, message=final_msg,
                  errors=errors[:30], current="")
        logger.info(f"[EDIT] Финиш: {final_msg}; ошибок: {len(errors)}")


    def _autoclean_node(self, client: FunPayClient, node: str, title: str,
                          tokens: List[str], offer_type: str = "") -> int:
        """Перед выставлением удаляет выключенные копии в разделе. Возвращает кол-во."""
        removed = 0
        seen_live: Set[str] = set()
        ot = (offer_type or "").strip().lower()
        # 0) ЖИВЫЕ выключенные с trade-страницы раздела (главный фикс лимита).
        # Реестр часто пуст, а розовые ряды (warning) реально жрут лимит FunPay.
        try:
            live_offers = []
            if hasattr(client, "get_my_trade_offers"):
                live_offers = client.get_my_trade_offers(str(node), kind="lots") or []
                if not live_offers:
                    live_offers = client.get_my_trade_offers(str(node), kind="chips") or []
            for o in live_offers or []:
                try:
                    if not o.get("disabled"):
                        continue
                    lid = str(o.get("offer_id", ""))
                    if not lid or lid in seen_live:
                        continue
                    seen_live.add(lid)
                    t_live = str(o.get("title", "") or "").lower()
                    if tokens and not any(tok in t_live for tok in tokens):
                        continue
                    try:
                        d = client.get_lot_details(lid)
                    except Exception:
                        continue
                    if not d:
                        continue
                    if ((d.get("form_data", {}) or {}).get("active", "") or ""):
                        continue  # активен — не трогаем
                    if ot:
                        et = str((((d.get("form_data", {}) or {})
                                   .get("fields[type]", "")) or "").strip().lower() or "")
                        if et and et != ot:
                            continue  # другой тип — не наша копия
                    ok, _ = client.delete_lot(lid)
                    if ok:
                        removed += 1
                        logger.info(f"[BULK] [{node}] #{lid}: выключенная копия удалена (trade)")
                    time.sleep(2)
                except Exception:
                    continue
        except Exception:
            pass
        for r in _registry_load():
            if str(r.get("node", "")) != str(node):
                continue
            if tokens and not any(tok in str(r.get("title", "")).lower() for tok in tokens):
                continue
            rt = str(r.get("type", "") or "").strip().lower()
            if ot and rt and ot != rt:
                continue
            lid = str(r.get("lot_id", ""))
            if not lid or lid in seen_live:
                continue
            try:
                d = client.get_lot_details(lid)
            except Exception:
                continue
            if not d:
                continue  # уже нет — и ладно
            if ((d.get("form_data", {}) or {}).get("active", "") or ""):
                continue  # активен — не трогаем
            ok, _ = client.delete_lot(lid)
            if ok:
                removed += 1
                logger.info(f"[BULK] [{node}] #{lid}: выключенная копия удалена")
            time.sleep(2)
        return removed

    def _run_purge_task(self, config: Dict):
        """Удаление выключенных лотов (живые trade + реестр + bot.log). Безвозвратно!"""
        def _progress(**kw):
            with _BULK_LOCK:
                _DEL_TASK.update(kw)

        client = FunPayClient(golden_key=config.get("golden_key", ""))
        auth_ok, _, _, err = client.check_auth()
        if not auth_ok:
            _progress(running=False, finished=True, message=f"Ключ не работает: {err}",
                      errors=[f"Ключ не работает: {err}"])
            return
        logger.info("[PURGE] Старт")

        def _stop():
            with _BULK_LOCK:
                return bool(_DEL_TASK.get("stop"))

        deleted = [0]
        deleted_wrap = deleted

        def _cb(done=0, current="", total=None, deleted=None):
            with _BULK_LOCK:
                upd = {"done": done, "current": current}
                if total is not None:
                    upd["total"] = total
                if deleted is not None:
                    upd["deleted"] = deleted
                    deleted_wrap[0] = deleted
                else:
                    upd["deleted"] = deleted_wrap[0]
                _DEL_TASK.update(upd)

        res = _purge_inactive_core(client, stop_check=_stop, progress_cb=_cb, max_n=200)
        deleted[0] = res["deleted"]
        total = res["total"]
        if res["stopped"]:
            msg = f"Остановлено: удалено выключенных {res['deleted']} из {total}"
        else:
            msg = (f"Удалено выключенных: {res['deleted']} из {total} "
                   f"(живые: {res.get('live', 0)}, реестр: {res['reg']}, лог: {res['log']})")
            if res.get("total_all", total) > total:
                msg += f". Всего найдено: {res['total_all']} — жми ещё раз для остатка"
        if res["gone"]:
            msg += f". Уже нет: {res['gone']}"
        if res["active_skip"]:
            msg += f". Активны (не тронуты): {res['active_skip']}"
        if res["fail"]:
            msg += ". Ошибки: " + "; ".join(res["fail"][:5])
        logger.info(f"[PURGE] Финиш: {msg}")
        _progress(running=False, finished=True, message=msg, current="",
                  done=total, deleted=res["deleted"], errors=list(res["fail"]))

    def _run_delete_task(self, targets: List[Tuple[str, bool]], config: Dict):
        """Фоновое удаление выбранных лотов. Безвозвратно!"""
        def _progress(**kw):
            with _BULK_LOCK:
                _DEL_TASK.update(kw)

        golden_key = config.get("golden_key", "")
        client = FunPayClient(golden_key=golden_key)
        auth_ok, _, _, err = client.check_auth()
        if not auth_ok:
            _progress(running=False, finished=True, message=f"Ключ не работает: {err}",
                      errors=[f"Ключ не работает: {err}"])
            return

        deleted = 0
        errors = []
        done = 0
        stopped = False
        total = len(targets)
        logger.info(f"[DEL] Старт: {total} лотов")
        for lid, is_chip in targets:
            with _BULK_LOCK:
                if _DEL_TASK.get("stop"):
                    stopped = True
                    break
            done += 1
            _progress(done=done, deleted=deleted, errors=list(errors), current=f"#{lid}")
            ok, msg = client.delete_lot(lid, is_chip=is_chip)
            if ok:
                deleted += 1
                logger.info(f"[DEL] [{done}/{total}] #{lid}: ✓ удалён")
            else:
                errors.append(f"#{lid}: {msg}")
                logger.warning(f"[DEL] [{done}/{total}] #{lid}: ✗ {msg}")
            _progress(done=done, deleted=deleted, errors=list(errors))
            time.sleep(2)

        if stopped:
            msg = f"Остановлено: удалено {deleted} из {total}"
        else:
            msg = f"Удалено лотов: {deleted} из {total}"
        if errors:
            msg += ". Ошибки: " + "; ".join(errors[:3])
        logger.info(f"[DEL] Финиш: {msg}")
        _progress(running=False, finished=True, message=msg, current="",
                  done=total, deleted=deleted, errors=list(errors))

    def _create_lots_from_selection(self, req_data: Dict, config: Dict) -> Dict:
        """Создает лоты из выбранных офферов (legacy)"""
        selected = req_data.get("selected_offers", [])
        prompt_auto = req_data.get("prompt_auto", "")
        prompt_response = req_data.get("prompt_response", "")
        category_name = req_data.get("category_name", "")
        node_id = str(req_data.get("node_id", "") or "").strip()
        # чинит пустой node_id: пробуем вытащить из первого оффера
        if not node_id and selected:
            first = selected[0] if isinstance(selected[0], dict) else {}
            for key in ("url", "category_url", "node_id", "node"):
                val = str(first.get(key, "") or "")
                m = re.search(r"/(chips|lots)/(\d+)", val) or re.search(r"^(\d+)$", val)
                if m:
                    node_id = m.group(2) if "/" in val else m.group(1)
                    break

        if not selected:
            return {"ok": False, "error": "Не выбраны товары для создания лотов"}
        if not node_id:
            return {"ok": False, "error": "Не выбран раздел (node_id). Используйте новую форму «Выставить во все выбранные разделы»."}

        # Создаем клиента FunPay
        golden_key = config.get("golden_key", "")
        client = FunPayClient(golden_key=golden_key)
        auth_ok, user_id, username, err = client.check_auth()

        if not auth_ok:
            return {"ok": False, "error": f"Ошибка авторизации: {err}"}

        created_count = 0
        errors = []

        # Получаем цены по умолчанию для расчета
        default_prices = config.get("default_seller_prices", {})

        # Ищем совпадения в названиях для определения базовой цены
        def find_price_for_item(title: str) -> float:
            title_lower = title.lower()
            for key, price in default_prices.items():
                key_lower = key.lower()
                if key_lower in title_lower or title_lower in key_lower:
                    return price
            # Если не нашли, возвращаем базовую цену
            return 0.15

        for offer in selected:
            title = offer.get("title", "")
            offer_price = offer.get("price", 0.0)

            # Извлекаем параметры из названия
            server = offer.get("server", "")
            side = offer.get("side", "")
            subcategory = offer.get("subcategory", "")

            # Вычисляем цену продавца (чистыми)
            # FunPay берет комиссию ~22%,所以: seller_price = buyer_price / 1.2236
            buyer_price = max(0.01, offer_price - 0.01)

            # Расчет чистой цены продавца
            commission_ratio = 1.223577
            seller_price = buyer_price / commission_ratio

            # Формируем описания
            summary_ru = f"{title}"
            if server or side:
                summary_ru += f"\n\nСервер: {server if server else 'Любой'}"
                summary_ru += f"\nСторона: {side if side else 'Любая'}"
            if subcategory:
                summary_ru += f"\nКатегория: {subcategory}"

            summary_en = title
            if server or side:
                summary_en += f"\nServer: {server if server else 'Any'}"
                summary_en += f"\nSide: {side if side else 'Any'}"
            if subcategory:
                summary_en += f"\nCategory: {subcategory}"

            # Создаем лот
            success, msg = client.create_lot(
                node_id=node_id,
                title=title,
                seller_price=seller_price,
                summary_ru=summary_ru,
                summary_en=summary_en,
                server=server,
                side=side,
                subcategory=subcategory,
                prompt_auto=prompt_auto,
                prompt_response=prompt_response,
                amount=1,
            )

            if success:
                created_count += 1
                logger.info(f"✓ Создан лот: {title[:50]}... цена: {seller_price:.3f}₽")
            else:
                errors.append(f"{title[:50]}: {msg}".replace("§SKIP§", ""))
                logger.warning(f"✗ Не удалось создать лот {title[:50]}: {msg}")

        # Формируем результат
        if created_count > 0:
            msg = f"Успешно создано {created_count} лотов"
            if errors:
                msg += f". Ошибки: {', '.join(errors[:3])}"
            return {"ok": True, "created": created_count, "message": msg}
        else:
            return {"ok": False, "error": "; ".join(errors) if errors else "Не удалось создать ни одного лота"}


LAST_PORT = PORT


TEXT_TEMPLATES_FILE = "text_templates.json"


def _read_text_templates() -> list:
    try:
        if os.path.exists(TEXT_TEMPLATES_FILE):
            with open(TEXT_TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _write_text_templates(templates: list) -> None:
    try:
        with open(TEXT_TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка записи текстовых шаблонов: {e}")


def run_dashboard_server(port: int = PORT, open_browser: bool = True):
    """Запускает веб-сервер панели управления (доступен и на ПК, и на телефоне)"""
    global LAST_PORT
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    httpd = None
    actual_port = port
    for candidate in range(port, port + 11):
        # На Windows SO_REUSEADDR может дать bind на занятый порт,
        # поэтому сначала проверяем: отвечает ли уже что-то на этом порту
        try:
            probe = socket.create_connection(("127.0.0.1", candidate), timeout=1)
            probe.close()
            continue  # порт занят живым сервером — пробуем следующий
        except OSError:
            pass
        try:
            # Многопоточный сервер: один долгий запрос не вешает всю панель
            httpd = socketserver.ThreadingTCPServer(("", candidate), DashboardHTTPHandler)
            actual_port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print(f"\n[WEB] Не удалось занять порт {port}-{port + 10}. Закройте программу, занявшую порт 5000, и запустите снова.\n")
        return
    LAST_PORT = actual_port
    local_url = f"http://localhost:{actual_port}"
    best_ip = get_best_local_ip()
    phone_url = f"http://{best_ip}:{actual_port}"

    print(f"\n{Fore.CYAN}+==================================================================+")
    print(f"|   [WEB] Панель на ПК:       {Fore.WHITE}{local_url:<35}{Fore.CYAN}|")
    print(f"|   [TEL] С телефона (Wi-Fi): {Fore.YELLOW}{phone_url:<35}{Fore.CYAN}|")
    print(f"+==================================================================+{Style.RESET_ALL}\n")

    if open_browser:
        try:
            threading.Timer(1.5, lambda: webbrowser.open(local_url)).start()
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass


# ============================================================================
# Авторизация панели (пароль)
# ============================================================================
PANEL_PASSWORD = (os.getenv("PANEL_PASSWORD", "") or "").strip()
_AUTH_COOKIE = "fp_panel_auth"
_AUTH_TOKEN = secrets.token_urlsafe(32)
_AUTH_TOKEN_BOUND = ""


def _refresh_panel_password() -> str:
    global PANEL_PASSWORD, _AUTH_TOKEN, _AUTH_TOKEN_BOUND
    env = (os.getenv("PANEL_PASSWORD", "") or "").strip()
    if env:
        PANEL_PASSWORD = env
    else:
        try:
            cfg = _read_config_file()
            st = cfg.get("settings")
            if isinstance(st, dict):
                PANEL_PASSWORD = str(st.get("panel_password") or "").strip()
        except Exception:
            pass
    if _AUTH_TOKEN_BOUND != PANEL_PASSWORD:
        _AUTH_TOKEN_BOUND = PANEL_PASSWORD
        _AUTH_TOKEN = secrets.token_urlsafe(32)
    return PANEL_PASSWORD


def _read_config_file(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or CONFIG_FILE
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_config_file(config: Dict[str, Any], path: Optional[str] = None) -> None:
    path = path or CONFIG_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


_STOP_FLAG = False


def _clear_stop_flag() -> None:
    global _STOP_FLAG
    _STOP_FLAG = False


def _request_stop_flag() -> None:
    global _STOP_FLAG
    _STOP_FLAG = True


def _stop_requested() -> bool:
    return _STOP_FLAG


_AUTH_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "ok": False,
                                "uid": None, "uname": None}
_AUTH_CACHE_TTL = 60.0
_AUTH_CACHE_LOCK = threading.Lock()
_LOTS_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "payload": None, "error": None}
_LOTS_CACHE_TTL = 30.0
_LOTS_CACHE_LOCK = threading.Lock()


def _invalidate_auth_cache() -> None:
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE["ts"] = 0.0
    with _LOTS_CACHE_LOCK:
        _LOTS_CACHE.update({"key": None, "ts": 0.0, "payload": None, "error": None})


def _cached_auth(golden_key: str) -> Tuple[bool, Optional[int], Optional[str]]:
    key = (golden_key or "").strip()
    with _AUTH_CACHE_LOCK:
        fresh = (time.time() - _AUTH_CACHE["ts"]) < _AUTH_CACHE_TTL
        if fresh and _AUTH_CACHE["key"] == key:
            return bool(_AUTH_CACHE["ok"]), _AUTH_CACHE["uid"], _AUTH_CACHE["uname"]
    if not key:
        with _AUTH_CACHE_LOCK:
            _AUTH_CACHE.update({"key": "", "ts": time.time(), "ok": False,
                                "uid": None, "uname": None})
        return False, None, None
    try:
        ok, uid, uname, _err = FunPayClient(golden_key=key).check_auth(
            delay=(0.1, 0.3))
    except Exception as e:
        logger.warning(f"Ошибка проверки авторизации: {e}")
        ok, uid, uname = False, None, None
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE.update({"key": key, "ts": time.time(), "ok": bool(ok),
                            "uid": uid, "uname": uname})
    return bool(ok), uid, uname


def start_dashboard_background(port: int = PORT, open_browser: bool = True) -> threading.Thread:
    """Запуск Web Dashboard в фоновом потоке вместе с ботом"""
    t = threading.Thread(target=run_dashboard_server, args=(port, open_browser), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    from colorama import Fore, Style, init
    init(autoreset=True)
    run_dashboard_server(PORT, open_browser=True)
