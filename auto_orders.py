"""
Автоответчик FunBot. Работает по двум потокам данных:

  📦 ЗАКАЗЫ (страница продаж):
     🛒 ответ на НОВЫЙ ЗАКАЗ    — покупатель оплатил заказ;
     ✅ ответ при ПОДТВЕРЖДЕНИИ — покупатель подтвердил заказ;
     ⭐ ответ на ОТЗЫВ          — комментируем отзыв покупателя;
     🤖 АВТООТВЕТЫ по ключевым словам в чате заказа.

  💬 ОБЩИЕ ЧАТЫ (инбокс /chat/):
     👋 АВТО-ПРИВЕТСТВИЕ для новых покупателей («Только совсем новые чаты»,
        «Не приветствовать при системных сообщениях»);
     🤖 АВТООТВЕТЫ по ключевым словам в обычных чатах.

Главного тумблера «модуль включён» больше нет: пока запущен бот, вотчер
работает, а что именно писать — решают тумблеры каждого сообщения.
"""

import json
import logging
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AutoOrders")

STATE_FILE = "orders_state.json"
CHATS_STATE_FILE = "chats_state.json"
STATUS_FILE = "auto_orders_status.json"

DEFAULTS: Dict[str, Any] = {
    "poll_interval_seconds": 60,
    # 👋 Авто-приветствие для новых покупателей (общие чаты)
    "greeting_enabled": False,
    "greeting_text": "",
    "greeting_new_only": True,      # только совсем новые чаты
    "greeting_skip_system": True,   # не приветствовать в чатах заказов/отзывов
    # 🛒 Ответ на новый заказ (покупатель оплатил)
    "new_order_enabled": False,
    "new_order_text": "",
    # ✅ Ответ при подтверждении заказа покупателем
    "confirm_reply_enabled": False,
    "confirm_reply_text": "",
    # ⭐ Ответ на отзыв
    "review_reply_enabled": False,
    "review_reply_5stars": "",
    "review_reply_other": "",
    # 🤖 Автоответы по ключевым словам
    "autoreply_enabled": False,
    "autoreply_cooldown_seconds": 20,
    # Правила: [{"keyword": "...", "match": "exact" | "contains", "reply": "..."}]
    "autoreply_rules": [],
}

# Старые ключи конфига → новые. Настройки из прежних версий не пропадают:
# «спасибо» стало «ответом на подтверждение». greeting_* в СТАРЫХ конфигах
# означало приветствие заказа — но только пока в конфиге нет признака нового
# формата (greeting_new_only), иначе greeting_* — это уже приветствие чатов.
_LEGACY_MAP = {
    "new_order_enabled": "greeting_enabled",
    "new_order_text": "greeting_text",
    "confirm_reply_enabled": "thanks_enabled",
    "confirm_reply_text": "thanks_text",
}


def _load_json(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать {path}: {e}")
    return default


def _save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Не удалось записать {path}: {e}")


def render(template: str, **kw: Any) -> str:
    """Подстановка {плейсхолдеров} без падения на чужих скобках."""
    out = str(template or "")
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


_WS_RE = re.compile(r"\s+")
_EDGE_PUNCT = " \t\r\n.,!?;:«»\"'`()[]{}…~*+–—=-"


def _norm_text(s: Any) -> str:
    """Нормализация для сравнения: нижний регистр, один пробел,
    пунктуация по краям отброшена («помощь!» == «Помощь»)."""
    s = _WS_RE.sub(" ", str(s or "").strip().lower())
    return s.strip(_EDGE_PUNCT)


def rule_reply(rule: Dict[str, Any], text: str) -> bool:
    """Совпало ли правило автоответа с текстом сообщения.

    Поддерживаются оба формата:
      новый: {"keyword": "...", "match": "exact" | "contains", "reply": "..."}
      старый: {"keywords": ["...", ...], "reply": "..."} (всегда «содержит»)
    """
    if not (str(rule.get("reply") or "").strip()):
        return False
    mode = str(rule.get("match") or "contains").strip().lower()
    t = _norm_text(text)
    if not t:
        return False
    kws: List[str] = []
    if rule.get("keyword"):
        kws.append(str(rule.get("keyword")))
    for k in rule.get("keywords") or []:   # старый формат
        kws.append(str(k))
    for k in kws:
        kl = _norm_text(k)
        if not kl:
            continue
        if mode == "exact":
            if t == kl:
                return True
        elif kl in t:
            return True
    return False


class AutoOrdersWorker:
    def __init__(self, funpay, telegram, config_path: str = "config.json"):
        self.funpay = funpay
        self.telegram = telegram
        self.config_path = config_path
        self.running = False
        self.thread: Optional[threading.Thread] = None

    # ---------- управление ----------

    def start(self) -> None:
        """
        Запуск фонового потока.

        Поток поднимается ВСЕГДА, а работает только при enabled=True в конфиге —
        иначе тумблер в панели не работал бы без перезапуска бота.
        """
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="AutoOrdersThread")
        self.thread.start()
        logger.info("Поток «Авто-заказы» запущен")

    def stop(self) -> None:
        """Остановка фонового потока с ожиданием реального завершения."""
        if not self.running:
            return
        self.running = False
        th = self.thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=10)
        if th is not None and not th.is_alive():
            self.thread = None

    def is_active(self) -> bool:
        """Поток жив И включён хотя бы один тумблер сообщений."""
        try:
            on = self._features_on(self._cfg())
        except Exception:
            on = False
        return bool(on and self.running
                    and self.thread is not None and self.thread.is_alive())

    def _features_on(self, cfg: Dict[str, Any]) -> bool:
        """Хотя бы одно сообщение/правило включено — иначе опрос не нужен."""
        return bool(cfg.get("greeting_enabled") or cfg.get("new_order_enabled")
                    or cfg.get("confirm_reply_enabled")
                    or cfg.get("review_reply_enabled")
                    or cfg.get("autoreply_enabled"))

    def _cfg(self) -> Dict[str, Any]:
        raw = _load_json(self.config_path, {})
        ao = raw.get("auto_orders", {}) or {}
        merged = dict(DEFAULTS)
        merged.update(ao)
        # Совместимость со старыми именами ключей.
        old_format = "greeting_new_only" not in ao
        for new, old in _LEGACY_MAP.items():
            if new in ao or old not in ao:
                continue
            if new.startswith("new_order") and not old_format:
                continue  # greeting_* уже значит «приветствие чатов»
            merged[new] = ao.get(old)
        # Правила из старого формата (keywords: [...]) приводим к новому виду.
        rules: List[Dict[str, Any]] = []
        for r in merged.get("autoreply_rules") or []:
            if not isinstance(r, dict):
                continue
            nr = {"reply": r.get("reply", ""),
                  "match": r.get("match") or "contains",
                  "keyword": r.get("keyword") or ""}
            if not nr["keyword"] and r.get("keywords"):
                nr["keyword"] = ", ".join(str(k) for k in r.get("keywords") if k)
                nr["match"] = "contains"
            rules.append(nr)
        merged["autoreply_rules"] = rules
        return merged

    def _write_status(self, **kw: Any) -> None:
        try:
            st = _load_json(STATUS_FILE, {})
            st.update(kw)
            _save_json(STATUS_FILE, st)
        except Exception:
            pass

    # ---------- главный цикл ----------

    def _loop(self) -> None:
        first = True
        while self.running:
            try:
                cfg = self._cfg()
                if self._features_on(cfg):
                    if first:
                        # Первый проход после запуска: все текущие заказы и
                        # чаты помечаются как уже обработанные — иначе бот
                        # завалил бы старых покупателей сообщениями задним
                        # числом (приветствиями/автоответами).
                        self._adopt_existing(cfg)
                        self._adopt_chats()
                        first = False
                    else:
                        self._poll(cfg)
                        self._poll_chats(cfg)
                else:
                    self._write_status(running=True, enabled=False,
                                       last_poll=time.strftime("%H:%M:%S"))
            except Exception as e:
                logger.error(f"Ошибка цикла авто-заказов: {e}", exc_info=True)
            interval = 60
            try:
                interval = max(20, int(self._cfg().get("poll_interval_seconds", 60)))
            except Exception:
                pass
            for _ in range(interval * 2):
                if not self.running:
                    break
                time.sleep(0.5)

    def _adopt_existing(self, cfg: Dict[str, Any]) -> None:
        """Первый проход: помечаем текущие заказы как уже виденные (без спама)."""
        try:
            sales = self.funpay.get_sales()
        except Exception as e:
            logger.warning(f"Не удалось загрузить продажи: {e}")
            return
        state = _load_json(STATE_FILE, {})
        changed = False
        for s in sales:
            oid = str(s.order_id)
            if oid in state:
                continue
            det = self._details(oid)
            state[oid] = self._new_state(s, det, silent=True)
            changed = True
            time.sleep(random.uniform(1.0, 2.5))
        if changed:
            _save_json(STATE_FILE, state)
        self._write_status(running=True, enabled=True, adopted=len(sales),
                           last_poll=time.strftime("%H:%M:%S"))

    def _new_state(self, sale, det: Optional[Dict[str, Any]], silent: bool) -> Dict[str, Any]:
        last_buyer = 0
        chat_id = None
        if det:
            chat_id = det.get("chat_id")
            msgs = det.get("messages") or []
            me = getattr(self.funpay, "user_id", None)
            for m in msgs:
                if m.sender_id and m.sender_id != me and not m.is_system:
                    last_buyer = max(last_buyer, m.msg_id)
        # Имена флагов «greeted»/«thanked» оставлены старыми НАРОЧНО:
        # существующий orders_state.json тогда не сбрасывается, и бот не
        # пишёт задним числом тем, кому уже писал раньше.
        return {
            "title": sale.title, "price": sale.price, "status": sale.status,
            "buyer": sale.buyer_name or (det or {}).get("buyer_name", ""),
            "buyer_id": str(sale.buyer_id or (det or {}).get("buyer_id", "")),
            "chat_id": chat_id,
            "greeted": silent,        # 🛒 ответ на новый заказ отправлен
            "thanked": silent,        # ✅ ответ на подтверждение отправлен
            "last_buyer_msg": last_buyer, "last_bot_reply_ts": 0.0,
            "review_rating": None, "review_replied": silent,
        }

    def _details(self, order_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.funpay.get_order_details(order_id)
        except Exception as e:
            logger.warning(f"Заказ {order_id}: не открылся ({e})")
            return None

    def _needs_details(self, st: Dict[str, Any], cfg: Dict[str, Any],
                       status: str) -> bool:
        """Открывать страницу заказа нужно только когда есть работа.
        Раньше детали грузились для КАЖДОГО заказа каждый цикл — лишние
        запросы к FunPay и лишний риск блокировки."""
        if status == "paid":
            return bool(cfg.get("autoreply_enabled"))
        if status == "completed":
            return bool(
                cfg.get("autoreply_enabled")
                or (cfg.get("confirm_reply_enabled") and not st.get("thanked"))
                or (cfg.get("review_reply_enabled") and not st.get("review_replied")))
        return False

    def _poll(self, cfg: Dict[str, Any]) -> None:
        try:
            sales = self.funpay.get_sales()
        except Exception as e:
            logger.warning(f"Не удалось загрузить продажи: {e}")
            self._write_status(running=True, enabled=True,
                               last_poll=time.strftime("%H:%M:%S"), error=str(e)[:120])
            return
        state = _load_json(STATE_FILE, {})
        counts = {"new": 0, "new_order_msgs": 0, "replied": 0,
                  "confirmed": 0, "reviews": 0}
        for s in sales:
            if not self.running:
                break
            oid = str(s.order_id)
            st = state.get(oid)
            if st is None:
                det = self._details(oid)
                st = self._new_state(s, det, silent=False)
                state[oid] = st
                counts["new"] += 1
                self._handle_new_order(oid, s, det, st, cfg, counts)
            else:
                st["status"] = s.status
                if s.status == "completed":
                    det = self._details(oid) if self._needs_details(st, cfg, s.status) else None
                    self._handle_completed(oid, s, det, st, cfg, counts)
                    self._handle_chat(oid, s, det, st, cfg, counts)
                elif s.status == "paid":
                    det = self._details(oid) if self._needs_details(st, cfg, s.status) else None
                    self._handle_chat(oid, s, det, st, cfg, counts)
                # refunded — ничего не делаем
            state[oid] = st
            _save_json(STATE_FILE, state)
            time.sleep(random.uniform(1.0, 2.5))
        _save_json(STATE_FILE, state)
        self._write_status(running=True, enabled=True,
                           last_poll=time.strftime("%H:%M:%S"),
                           sales=len(sales), counts=counts, error="")

    # ---------- общие чаты: приветствие + автоответы ----------

    def _chat_details(self, node_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.funpay.get_chat_details(node_id)
        except Exception as e:
            logger.warning(f"Чат {node_id}: не открылся ({e})")
            return None

    def _adopt_chats(self) -> None:
        """Первый проход: существующие чаты помечаются тихо — без приветствий
        и автоответов задним числом. Дальше обрабатывается только новое."""
        try:
            contacts = self.funpay.get_chat_contacts()
        except Exception as e:
            logger.warning(f"Не удалось загрузить список чатов: {e}")
            return
        state = _load_json(CHATS_STATE_FILE, {})
        for contact in contacts:
            node = str(contact.get("node_id") or "")
            if not node or node in state:
                continue
            last = int(contact.get("last_msg_id", 0) or 0)
            state[node] = {"greeted": True, "greet_suppressed": False,
                           "last_seen_msg": last, "last_buyer_msg": last,
                           "last_bot_reply_ts": 0.0}
        _save_json(CHATS_STATE_FILE, state)

    def _poll_chats(self, cfg: Dict[str, Any]) -> None:
        """Вотчер инбокса /chat/: 👋 приветствие + 🤖 автоответы по правилам.

        Чаты заказов пропускаются — их обслуживает заказной поток,
        чтобы покупатель не получил два разных сообщения.
        """
        greet_on = bool(cfg.get("greeting_enabled"))
        reply_on = bool(cfg.get("autoreply_enabled"))
        if not greet_on and not reply_on:
            return
        try:
            contacts = self.funpay.get_chat_contacts()
        except Exception as e:
            logger.warning(f"Не удалось загрузить список чатов: {e}")
            self._write_status(chats_error=str(e)[:120])
            return
        if not contacts:
            return
        state = _load_json(CHATS_STATE_FILE, {})
        me = getattr(self.funpay, "user_id", None)
        order_state = _load_json(STATE_FILE, {})
        order_chats = {str(v.get("chat_id")) for v in order_state.values()
                       if v.get("chat_id")}
        order_buyers = {str(v.get("buyer_id")) for v in order_state.values()
                        if v.get("buyer_id")}
        greeted_now = replied_now = 0
        for contact in contacts:
            if not self.running:
                break
            node = str(contact.get("node_id") or "")
            if not node or node in order_chats:
                continue
            st = state.get(node) or {}
            last_seen = int(st.get("last_seen_msg", 0) or 0)
            last_msg_id = int(contact.get("last_msg_id", 0) or 0)
            greeted = bool(st.get("greeted"))
            # Быстрый путь: приветствие уже было и новых сообщений нет —
            # страницу чата даже не открываем.
            if greeted and last_msg_id and last_msg_id <= last_seen:
                state[node] = st
                continue
            det = self._chat_details(node)
            if not det:
                state[node] = st
                continue
            if det.get("chat_id"):
                node = str(det.get("chat_id") or node)
            msgs = det.get("messages") or []
            interlocutor = str(det.get("user_id") or "")
            name = det.get("user_name") or contact.get("name") or "друг"
            my_msgs = [m for m in msgs if me and m.sender_id == me]
            buyer_msgs = [m for m in msgs
                          if interlocutor and str(m.sender_id) == interlocutor
                          and not m.is_system]
            has_system = any(m.is_system for m in msgs)

            # 👋 АВТО-ПРИВЕТСТВИЕ ДЛЯ НОВЫХ ПОКУПАТЕЛЕЙ
            if greet_on and not greeted and not st.get("greet_suppressed"):
                ok_to_greet = True
                if cfg.get("greeting_skip_system", True) and has_system:
                    ok_to_greet = False
                if interlocutor and interlocutor in order_buyers:
                    ok_to_greet = False  # недавний покупатель — приветствие в чате заказа
                if ok_to_greet and my_msgs:
                    ok_to_greet = False  # разговор уже начат (мои сообщения есть)
                if ok_to_greet and cfg.get("greeting_new_only", True) \
                        and len(buyer_msgs) != 1:
                    ok_to_greet = False  # «совсем новый чат» = одно сообщение покупателя
                text = render(cfg.get("greeting_text", ""),
                              **self._ctx("", "", "", name))
                if not text.strip():
                    ok_to_greet = False
                if ok_to_greet:
                    if self._say(int(node), text, f"чат {node}", "приветствие покупателя", st):
                        greeted = True
                        greeted_now += 1
                        # Одно сообщение — один ответ: правило ключевых слов
                        # не должно отвечать второй раз на то же сообщение.
                        if buyer_msgs:
                            st["last_buyer_msg"] = max(m.msg_id for m in buyer_msgs)
                else:
                    # Больше не пытаемся приветствовать этот чат (иначе
                    # бот будет открывать его заново в каждом цикле).
                    greeted = True

            # 🤖 АВТООТВЕТЫ ПО КЛЮЧЕВЫМ СЛОВАМ В ОБЫЧНЫХ ЧАТАХ
            if reply_on and interlocutor:
                fresh = [m for m in msgs
                         if m.msg_id > int(st.get("last_buyer_msg", 0) or 0)
                         and str(m.sender_id) == interlocutor
                         and not m.is_system and m.text.strip()]
                if fresh:
                    reply = ""
                    for m in reversed(fresh):
                        for rule in cfg.get("autoreply_rules") or []:
                            if rule_reply(rule, m.text):
                                reply = str(rule.get("reply") or "").strip()
                                break
                        if reply:
                            break
                    if reply:
                        try:
                            cooldown = max(0, int(cfg.get("autoreply_cooldown_seconds", 20)))
                        except (TypeError, ValueError):
                            cooldown = 20
                        if time.time() - float(st.get("last_bot_reply_ts", 0) or 0) >= cooldown:
                            ctx = self._ctx("", "", "", name)
                            if self._say(int(node), render(reply, **ctx),
                                         f"чат {node}", "автоответ по ключевым словам", st):
                                st["last_buyer_msg"] = max(m.msg_id for m in fresh)
                                replied_now += 1
                    else:
                        st["last_buyer_msg"] = max(m.msg_id for m in fresh)

            if msgs:
                st["last_seen_msg"] = max(int(last_msg_id or 0),
                                          max(m.msg_id for m in msgs))
            st["greeted"] = greeted
            st["user_id"] = interlocutor
            st["name"] = name
            state[node] = st
        _save_json(CHATS_STATE_FILE, state)
        if greeted_now or replied_now:
            self._write_status(chats=len(contacts), greeted=greeted_now,
                               replied=replied_now,
                               last_poll=time.strftime("%H:%M:%S"))

    # ---------- обработчики ----------

    def _say(self, chat_id: Optional[int], text: str, order_id: str,
             action: str, st: Dict[str, Any]) -> bool:
        if not chat_id:
            logger.warning(f"Заказ {order_id}: нет chat_id, {action} не отправлено")
            return False
        # Пустой/пробельный текст отправлять НЕЛЬЗЯ — иначе бот заспамит
        # покупателя пустыми сообщениями.
        if not str(text or "").strip():
            logger.info(f"Заказ {order_id}: {action} пропущен — текст сообщения пустой")
            return False
        time.sleep(random.uniform(2.0, 5.0))
        try:
            ok, msg = self.funpay.send_chat_message(chat_id, text)
        except Exception as e:
            ok, msg = False, str(e)
        if ok:
            st["last_bot_reply_ts"] = time.time()
            logger.info(f"Заказ {order_id}: {action} — отправлено")
        else:
            logger.warning(f"Заказ {order_id}: {action} — НЕ отправлено ({msg})")
        return ok

    def _notify(self, text: str) -> None:
        try:
            if self.telegram:
                self.telegram.send_message(text)
        except Exception:
            pass

    def _ctx(self, oid: str, title: str, price: Any, buyer: str = "",
             extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Переменные для шаблонов. Поддерживаются и короткие имена
        ({buyer}, {order}, {lot}), и полные ({buyername}, {orderid},
        {lotname}, {orderlink}) — как в других автоответчиках."""
        b = buyer or "друг"
        ctx: Dict[str, Any] = {
            "buyer": b, "buyername": b,
            "order": oid, "orderid": oid,
            "lot": title, "lotname": title,
            "price": price,
            "orderlink": f"https://funpay.com/orders/{oid}/",
        }
        if extra:
            ctx.update(extra)
        return ctx

    def _handle_new_order(self, oid: str, sale, det: Optional[Dict[str, Any]],
                          st: Dict[str, Any], cfg: Dict[str, Any],
                          counts: Dict[str, int]) -> None:
        if sale.status == "refunded":
            st.update({"greeted": True, "thanked": True})
            return
        if det and det.get("chat_id"):
            st["chat_id"] = det["chat_id"]
        if det and det.get("buyer_name"):
            st["buyer"] = det["buyer_name"]
            st["buyer_id"] = str(det.get("buyer_id") or "")
        ctx = self._ctx(oid, sale.title, sale.price, st.get("buyer"))
        # 🛒 Ответ на новый заказ — отправляется, когда покупатель ОПЛАТИЛ.
        if (cfg.get("new_order_enabled") and not st.get("greeted")
                and sale.status in ("paid", "completed")):
            if self._say(st.get("chat_id"),
                         render(cfg.get("new_order_text", ""), **ctx),
                         oid, "ответ на новый заказ", st):
                st["greeted"] = True
                counts["new_order_msgs"] += 1
        if sale.status == "paid":
            self._handle_chat(oid, sale, det, st, cfg, counts)
        elif sale.status == "completed":
            self._handle_completed(oid, sale, det, st, cfg, counts)
        self._notify(f"🛒 <b>Новый заказ {oid}</b>\n{st.get('buyer')} · {sale.title} · {sale.price} ₽")

    def _handle_chat(self, oid: str, sale, det: Optional[Dict[str, Any]],
                     st: Dict[str, Any], cfg: Dict[str, Any],
                     counts: Dict[str, int]) -> None:
        """🤖 Автоответы по ключевым словам."""
        if not cfg.get("autoreply_enabled") or not det:
            return
        if det.get("chat_id"):
            st["chat_id"] = det["chat_id"]
        me = getattr(self.funpay, "user_id", None)
        fresh: List[Any] = []
        for m in det.get("messages") or []:
            if m.msg_id <= int(st.get("last_buyer_msg", 0)):
                continue
            if m.is_system or not m.text.strip():
                continue
            if me and m.sender_id == me:
                continue  # своё — не трогаем
            if not st.get("buyer_id"):
                continue  # покупатель неизвестен — отвечать рискованно (спам в чужие чаты)
            if str(m.sender_id) != str(st.get("buyer_id")):
                continue  # чужой (модер, второй участник) — не отвечаем
            fresh.append(m)
        if not fresh:
            return
        # Ищем совпадение по правилам, свежие сообщения приоритетнее.
        reply = ""
        for m in reversed(fresh):
            for rule in cfg.get("autoreply_rules") or []:
                if rule_reply(rule, m.text):
                    reply = str(rule.get("reply") or "").strip()
                    break
            if reply:
                break
        if not reply:
            # Ни одно правило не подошло — помечаем сообщения обработанными,
            # чтобы не проверять их заново в каждом цикле.
            st["last_buyer_msg"] = max(m.msg_id for m in fresh)
            return
        # Кулдаун между ответами в одном чате. ВАЖНО: сообщения при этом
        # НЕ помечаются прочитанными — ответ уйдёт на следующем опросе,
        # когда кулдаун истечёт (раньше сообщение «съедалось» навсегда).
        try:
            cooldown = max(0, int(cfg.get("autoreply_cooldown_seconds", 20)))
        except (TypeError, ValueError):
            cooldown = 20
        if time.time() - float(st.get("last_bot_reply_ts", 0) or 0) < cooldown:
            return
        ctx = self._ctx(oid, sale.title, sale.price, st.get("buyer"))
        if self._say(st.get("chat_id"), render(reply, **ctx),
                     oid, "автоответ по ключевым словам", st):
            st["last_buyer_msg"] = max(m.msg_id for m in fresh)
            counts["replied"] += 1

    def _handle_completed(self, oid: str, sale, det: Optional[Dict[str, Any]],
                          st: Dict[str, Any], cfg: Dict[str, Any],
                          counts: Dict[str, int]) -> None:
        if det and det.get("chat_id"):
            st["chat_id"] = det["chat_id"]
        ctx = self._ctx(oid, sale.title, sale.price, st.get("buyer"))
        # ✅ Ответ при подтверждении заказа покупателем.
        if cfg.get("confirm_reply_enabled") and not st.get("thanked"):
            if self._say(st.get("chat_id"),
                         render(cfg.get("confirm_reply_text", ""), **ctx),
                         oid, "ответ на подтверждение заказа", st):
                st["thanked"] = True
                counts["confirmed"] += 1
        # ⭐ Ответ на отзыв покупателя.
        rev = det.get("review") if det else None
        if rev and rev.rating:
            st["review_rating"] = rev.rating
            me = str(getattr(self.funpay, "user_id", "") or "")
            buyer = str(st.get("buyer_id") or "")
            # Отвечать можно только на отзыв ПОКУПАТЕЛЯ, не модератора и не свой.
            is_buyer_review = (not rev.author_id) or (not me) or (str(rev.author_id) == buyer)
            if is_buyer_review and rev.has_reply:
                st["review_replied"] = True  # ответ уже есть (руками) — не дублируем
            if is_buyer_review and cfg.get("review_reply_enabled") and not st.get("review_replied"):
                tpl = cfg.get("review_reply_5stars") if rev.rating == 5 else cfg.get("review_reply_other")
                try:
                    ok, _msg = self.funpay.reply_review(
                        oid, render(tpl, stars=rev.rating, **ctx))
                except Exception as e:
                    ok = False
                    logger.warning(f"Заказ {oid}: ответ на отзыв не ушёл ({e})")
                if ok:
                    st["review_replied"] = True
                    counts["reviews"] += 1
                    logger.info(f"Заказ {oid}: ответ на отзыв ({rev.rating}★) опубликован")
