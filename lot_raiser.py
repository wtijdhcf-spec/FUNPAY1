"""
Модуль безопасного автоподнятия ВСЕХ предложений (лотов) на FunPay
с защитой от обнаружения (анти-детект: кулдауны + случайный джиттер).
"""

import datetime
import json
import logging
import os
import random
import re
import time
from typing import Dict, List, Tuple

from colorama import Fore, Style

logger = logging.getLogger("LotRaiser")

STATE_FILE = "raise_state.json"


class LotRaiser:
    def __init__(self, funpay, telegram, enabled: bool = True):
        self.funpay = funpay
        self.telegram = telegram
        self.enabled = enabled

        # Ключ "lots:123" / "chips:456" -> unix timestamp когда можно следующий запрос
        self.next_raise_timestamps: Dict[str, float] = {}
        # Множество успешно поднятых разделов (ключи вида "lots:123")
        self.raised_nodes = set()
        # Последний результат по каждому разделу для панели
        self.last_results: Dict[str, Dict] = {}
        self._load_state()

    # ---------- persistence ----------

    def _load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.next_raise_timestamps = {
                    str(k): float(v) for k, v in (data.get("next_raise", {}) or {}).items()
                }
                self.raised_nodes = set(str(x) for x in (data.get("raised", []) or []))
                self.last_results = dict(data.get("last_results", {}) or {})
        except Exception as e:
            logger.warning(f"Не удалось прочитать {STATE_FILE}: {e}")

    def _save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "next_raise": self.next_raise_timestamps,
                    "raised": sorted(list(self.raised_nodes)),
                    "last_results": self.last_results,
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Не удалось сохранить {STATE_FILE}: {e}")

    # ---------- helpers ----------

    @staticmethod
    def _node_key(kind: str, node_id: str) -> str:
        k = (kind or "lots").strip().lower()
        if k not in ("lots", "chips"):
            k = "lots"
        return f"{k}:{str(node_id).strip()}"

    def is_node_raised(self, node_id: str, kind: str = "lots") -> bool:
        """Проверяет, поднят ли указанный раздел"""
        return self._node_key(kind, node_id) in self.raised_nodes

    def mark_node_raised(self, node_id: str, kind: str = "lots"):
        """Отмечает раздел как поднятый"""
        self.raised_nodes.add(self._node_key(kind, node_id))
        self._save_state()

    def extract_node_ids(self, user_lots) -> List[Tuple[str, str]]:
        """Извлекает уникальные (kind, node_id) из списка активных лотов.

        Возвращает отсортированный список кортежей (kind, node_id),
        где kind = 'lots' | 'chips'. Лоты без известного раздела
        пропускаются (никакого fallback на 402 — иначе поднимался бы
        чужой раздел).
        """
        found: Dict[str, Tuple[str, str]] = {}
        for lot in user_lots or []:
            kind = "chips" if getattr(lot, "is_chip", False) else "lots"
            cat_url = (getattr(lot, "category_url", "") or "").strip()
            cat_id = str(getattr(lot, "category_id", "") or "").strip()

            # 1. kind + id из category_url (надёжнее всего)
            if cat_url:
                m = re.search(r"/(lots|chips)/(\d+)", cat_url)
                if m:
                    k = m.group(1).lower()
                    nid = m.group(2)
                    # is_chip лота уточняет kind только если url без kind
                    found[self._node_key(k, nid)] = (k, nid)
                    continue

            # 2. category_id + is_chip
            if cat_id and cat_id.isdigit():
                found[self._node_key(kind, cat_id)] = (kind, cat_id)
                continue

            # 3. category_url вида /lots/123 без хоста
            if cat_url:
                m = re.search(r"/(\d+)/?", cat_url)
                if m:
                    nid = m.group(1)
                    found[self._node_key(kind, nid)] = (kind, nid)

        return sorted(found.values(), key=lambda x: (x[0], int(x[1]) if x[1].isdigit() else x[1]))

    def get_status(self) -> Dict:
        """Статус для Web-панели: по каждому разделу когда следующее поднятие."""
        now = time.time()
        items = []
        for key in sorted(set(list(self.next_raise_timestamps.keys()) + list(self.raised_nodes))):
            nxt = float(self.next_raise_timestamps.get(key, 0) or 0)
            items.append({
                "node": key,
                "raised": key in self.raised_nodes,
                "next_in_sec": max(0, int(nxt - now)) if nxt > now else 0,
                "next_at": (datetime.datetime.now() + datetime.timedelta(seconds=max(0, nxt - now))).strftime("%H:%M:%S") if nxt > now else "готово",
                "last": self.last_results.get(key, {}),
            })
        return {
            "enabled": self.enabled,
            "nodes": items,
            "count": len(items),
        }

    # ---------- main ----------

    def check_and_raise(self, user_lots, force: bool = False) -> Dict:
        """
        Проверяет и безопасно поднимает предложения ВО ВСЕХ разделах пользователя.

        force=True — игнорировать локальные кулдауны (сам FunPay всё равно
        ответит wait, если рано). Используется кнопкой «Поднять все сейчас».

        Возвращает статистику: {"total": N, "raised": R, "cooldown": C, "errors": E, "details": [...]}
        """
        stats = {"total": 0, "raised": 0, "cooldown": 0, "errors": 0, "details": []}
        if not self.enabled and not force:
            return stats

        nodes = self.extract_node_ids(user_lots)
        stats["total"] = len(nodes)
        if not nodes:
            print(f"{Fore.YELLOW}[Автоподнятие] Нет известных разделов (лоты без category). Пропуск.{Style.RESET_ALL}")
            return stats

        now = time.time()
        for kind, node_id in nodes:
            key = self._node_key(kind, node_id)
            next_time = float(self.next_raise_timestamps.get(key, 0.0) or 0.0)

            if not force and now < next_time:
                remaining_sec = int(next_time - now)
                stats["cooldown"] += 1
                stats["details"].append({"node": key, "status": "cooldown", "wait_sec": remaining_sec})
                continue

            print(f"\n{Fore.MAGENTA}⚡ [Автоподнятие] Поднятие ВСЕХ предложений раздела {kind} #{node_id}...{Style.RESET_ALL}")

            # Естественная пауза перед действием (как человек)
            time.sleep(random.uniform(1.5, 3.0))

            try:
                success, msg, wait_sec = self.funpay.raise_lots_for_node(node_id, kind=kind)
            except TypeError:
                # старый funpay_api без kind — вызываем как раньше
                success, msg, wait_sec = self.funpay.raise_lots_for_node(node_id)
            except Exception as e:
                success, msg, wait_sec = False, f"Исключение: {e}", 600

            # Человеческий джиттер (+1-4 мин), чтобы интервал не был ровно одинаковым
            jitter = random.randint(60, 240)
            try:
                target_wait = max(int(wait_sec or 0), 300) + jitter
            except Exception:
                target_wait = 300 + jitter

            self.next_raise_timestamps[key] = time.time() + target_wait
            self.last_results[key] = {"ok": bool(success), "msg": str(msg)[:200], "at": datetime.datetime.now().strftime("%H:%M:%S")}
            self._save_state()

            unlock_time_str = (datetime.datetime.now() + datetime.timedelta(seconds=target_wait)).strftime("%H:%M:%S")

            if success:
                self.raised_nodes.add(key)
                self._save_state()
                stats["raised"] += 1
                stats["details"].append({"node": key, "status": "raised", "msg": msg})
                print(f"  {Fore.GREEN}[✓] Раздел {kind} #{node_id} поднят!{Style.RESET_ALL}")
                print(f"  {Fore.LIGHTBLACK_EX}└ Следующее поднятие ~ {unlock_time_str} (через {target_wait // 60} мин. с анти-детект задержкой){Style.RESET_ALL}")

                if getattr(self.telegram, "enabled", False):
                    try:
                        self.telegram.send_message(
                            f"🚀 <b>Предложения на FunPay подняты!</b>\n\n"
                            f"📁 <b>Раздел:</b> <code>{kind} #{node_id}</code>\n"
                            f"⏱ <b>Следующее автоподнятие:</b> в {unlock_time_str}"
                        )
                    except Exception:
                        pass
            else:
                mins_left = int(wait_sec or 0) // 60
                stats["details"].append({"node": key, "status": "cooldown" if "кулдаун" in str(msg).lower() or (wait_sec or 0) > 60 else "error", "msg": msg})
                if "кулдаун" in str(msg).lower() or (wait_sec or 0) > 60:
                    stats["cooldown"] += 1
                else:
                    stats["errors"] += 1
                print(f"  {Fore.YELLOW}[i] Раздел {kind} #{node_id}: {msg} ({mins_left} мин.).")
                print(f"  {Fore.LIGHTBLACK_EX}└ Следующая попытка после {unlock_time_str}.{Style.RESET_ALL}")

            # Пауза между разделами, чтобы не слать пачку запросов подряд
            time.sleep(random.uniform(2.0, 4.0))

        return stats

    def raise_all_now(self, user_lots) -> Dict:
        """Ручное поднятие ВСЕХ разделов (кнопка в панели) — без ожидания локальных кулдаунов."""
        return self.check_and_raise(user_lots, force=True)
