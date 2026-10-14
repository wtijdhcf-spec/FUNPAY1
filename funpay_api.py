"""
Модуль для взаимодействия с FunPay через HTTP сессию
"""

import json
import logging
import random
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("FunPayAPI")


# ============================================================================
#  ГЛОБАЛЬНЫЙ БЛОКИРАТОР ИЗМЕНЕНИЯ ЦЕН
# ---------------------------------------------------------------------------
#  ВНИМАНИЕ: этот флаг намеренно зафиксирован в False.
#  Бот больше НЕ умеет менять цены ни на одном лоте — ни автоматически,
#  ни по кнопке, ни через API. Флаг существует только как последний рубеж:
#  даже если где-то в коде останется вызов update_lot_price(), он вернёт
#  ошибку и НЕ выполнит запрос к FunPay.
#
#  Чтобы когда-нибудь вернуть демпинг: поставь True здесь.
# ============================================================================
PRICE_EDITS_ENABLED = False


TYPE_HINTS = [
    ("Гайды", ["гайд", "guide"]),
    ("Gamepass", ["gamepass", "геймпасс", "пасс", "pass"]),
    ("Аккаунты", ["аккаунт", "account", "продажа", "аренда"]),
    ("Предметы", ["предмет", "item", "скин", "skin", "робукс", "robux",
                  "монет", "coin", "алмаз", "gem", "донат", "donat",
                  "пак", "бакс", "bucks"]),
    ("Услуги", ["услуг", "service", "буст", "boost", "прокач", "фарм",
                "farm", "промокод", "promo", "подписк", "subscription",
                "экзекутор", "executor", "инжект", "injector", "автовыдача"]),
    ("Прочее", ["прочее", "other", "код", "аватар", "vip"]),
]


def _label_for_control(control, field_name: str = "") -> str:
    """
    Человекочитаемая подпись поля формы FunPay («с подпиской или без»).

    FunPay верстает поля как <div class="form-group"><label>…</label><select …>.
    Подпись может быть и в .form-group > label, и в data-атрибуте, и в
    aria-label. Если ничего не нашли — отдаём имя поля, чтобы в панели
    было видно, что это вообще за настройка.
    """
    for attr in ("data-label", "aria-label", "placeholder", "title"):
        val = (control.get(attr) or "").strip()
        if val:
            return val

    # Подпись внутри общего контейнера поля
    group = control.find_parent(class_=re.compile(r"form-group|form-row|field"))
    if group is not None:
        lab = group.find("label")
        if lab is not None:
            txt = lab.get_text(" ", strip=True)
            if txt:
                return txt

    # Подпись может быть прямо перед контролом
    prev = control.find_previous("label")
    if prev is not None:
        txt = prev.get_text(" ", strip=True)
        if txt:
            return txt

    if field_name:
        pretty = re.sub(r"^fields?\[|\]$", "", field_name)
        pretty = pretty.replace("][", " · ").replace("[", " ").replace("]", "")
        return pretty.strip() or field_name
    return field_name or "Поле"


def detect_lot_category(text: str) -> str:
    """Категория товара по названию (Гайды/Gamepass/Аккаунты/Предметы/Услуги/Прочее)."""
    t = (text or "").lower()
    for cat, kws in TYPE_HINTS:
        if any(k in t for k in kws):
            return cat
    return ""


def match_type_option(opts: List[Tuple[str, str]], wanted: str) -> str:
    """Ищет опцию fields[type] по value ИЛИ тексту, возвращает value или ''."""
    if not wanted:
        return ""
    vl = wanted.strip().lower()
    for v, t in opts:
        if (v or "").strip().lower() == vl or (t or "").strip().lower() == vl:
            return v if (v or "").strip() else t
    # нечёткое совпадение (Gamepass ~ геймпасс, Аккаунт ~ Аккаунты)
    for v, t in opts:
        combined = ((v or "") + " " + (t or "")).strip().lower()
        if combined and vl and (vl in combined or combined in vl):
            return v if (v or "").strip() else t
    return ""


def resolve_offer_type(options: List[Tuple[str, str]], offer_type: str = "",
                       title: str = "", summary: str = "", section_name: str = "") -> str:
    """Подбор типа под раздел. Явный выбор — строго; Авто — только уверенные
    совпадения (название лота/раздела/ключевые слова). '' = не подобрано.
    Угадывания 'первый попавшийся' НЕТ — иначе Услуги уезжают в Монеты."""
    if offer_type:
        return match_type_option(options, offer_type)
    lot_cat = detect_lot_category(f"{title} {summary}") or detect_lot_category(section_name)
    if lot_cat:
        m = match_type_option(options, lot_cat)
        if m:
            return m
        for cat, kws in TYPE_HINTS:
            if cat != lot_cat:
                continue
            for v, t in options:
                blob = ((v or "") + " " + (t or "")).lower()
                if any(k in blob for k in kws):
                    return v if (v or "").strip() else t
    if section_name:
        m = match_type_option(options, section_name)
        if m:
            return m
    return ""


@dataclass
class CompetitorOffer:
    offer_id: str
    seller_name: str
    seller_id: str
    is_online: bool
    title: str
    server: Optional[str] = None
    side: Optional[str] = None
    subcategory: Optional[str] = None
    price: float = 0.0
    amount: int = 1
    unit: str = "шт."
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UserLot:
    lot_id: str
    title: str
    category_id: str
    category_name: str
    category_url: str
    current_price: float
    is_chip: bool = False
    server: Optional[str] = None
    side: Optional[str] = None
    subcategory: Optional[str] = None
    amount: int = 1
    active: bool = True
    raw_node: Optional[Any] = None


@dataclass
class SaleOrder:
    order_id: str
    title: str
    category: str
    price: float
    status: str  # "paid" | "completed" | "refunded"
    buyer_name: str
    buyer_id: str
    date_text: str
    url: str


@dataclass
class ChatMsg:
    msg_id: int
    sender_id: int
    sender_name: str
    text: str
    is_system: bool


@dataclass
class OrderReview:
    rating: Optional[int]
    text: str
    author_id: Optional[int]
    has_reply: bool
    reply_text: str


class FunPayClient:
    BASE_URL = "https://funpay.com"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, golden_key: str):
        self.golden_key = golden_key.strip()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self.session.cookies.set("golden_key", self.golden_key, domain=".funpay.com")
        self.session.cookies.set("cy", "rub", domain=".funpay.com")  # Принудительно рубли
        self.session.cookies.set("locale", "ru", domain=".funpay.com")

        self.user_id: Optional[int] = None
        self.username: Optional[str] = None
        self.csrf_token: Optional[str] = None

        # requests.Session НЕ потокобезопасна, а этот клиент используется сразу
        # из нескольких потоков: основной цикл, «вечный онлайн», «авто-заказы».
        # Без блокировки они гоняют один и тот же пул соединений и cookie-jar
        # одновременно — это источник подвисаний и порчи сессии.
        self._lock = threading.RLock()

        # Секрет после logout() в памяти НЕ хранится: ключ нужен только внутри
        # logout(), где живёт в локальной переменной. Так требовал пользователь.

    def _extract_csrf(self, html: str) -> Optional[str]:
        """Извлекает csrf_token из HTML или app-data"""
        # Поиск в data-app-data
        app_data_match = re.search(r'data-app-data=["\'](.*?)["\']', html)
        if app_data_match:
            try:
                decoded = html_unescape(app_data_match.group(1))
                data = json.loads(decoded)
                if "csrf-token" in data:
                    self.csrf_token = data["csrf-token"]
                    return self.csrf_token
            except Exception:
                pass

        # Поиск мета-тега csrf
        soup = BeautifulSoup(html, "html.parser")
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        if csrf_meta and csrf_meta.get("content"):
            self.csrf_token = csrf_meta["content"]
            return self.csrf_token

        # Поиск input csrf_token
        csrf_input = soup.find("input", {"name": "csrf_token"})
        if csrf_input and csrf_input.get("value"):
            self.csrf_token = csrf_input["value"]
            return self.csrf_token

        return self.csrf_token

    def check_auth(self, delay: Optional[Tuple[float, float]] = None
                   ) -> Tuple[bool, Optional[int], Optional[str], Optional[str]]:
        """
        Проверяет валидность golden_key и возвращает (успех, user_id, username, ошибка)

        delay=(min, max) — пауза перед запросом. Раньше тут стоял ЖЁСТКИЙ
        sleep 2–4 сек перед ОДНИМ-ЕДИНСТВЕННЫМ запросом. Из-за этого открытие
        панели занимало 4–5 секунд, хотя сама проверка идёт ~0.2 сек.
        Пауза нужна для частых повторов, а не для первого запроса, поэтому
        здесь стоит короткая. Кто хочет «человечный» темп — передаёт своё.
        """
        if not self.golden_key:
            return False, None, None, "golden_key не указан в настройках"

        lo, hi = delay if delay else (0.3, 0.9)
        if lo or hi:
            time.sleep(random.uniform(lo, hi))

        try:
            resp = self._make_request_with_retry("GET", self.BASE_URL)
            if resp is None:
                return False, None, None, "Ошибка при запросе к FunPay"

            if resp.status_code != 200:
                return False, None, None, f"Ошибка ответа FunPay (код {resp.status_code})"

            self._extract_csrf(resp.text)

            soup = BeautifulSoup(resp.text, "html.parser")

            # НЕ авторизован — проверяем ДО любых эвристик.
            # Важно: раньше эта проверка стояла в самом конце и была
            # недостижимой, т.к. код выходил раньше по чужой ссылке /users/<id>/.
            if self._looks_like_guest_page(resp.text):
                return False, None, None, "Недействительный golden_key (FunPay требует авторизацию)"

            # Проверяем авторизацию
            # Способ 1: app-data — единственный ДОКАЗУЕМЫЙ признак входа
            app_data_el = soup.find(attrs={"data-app-data": True})
            if app_data_el:
                try:
                    data = json.loads(app_data_el["data-app-data"])
                    if data.get("userId"):
                        self.user_id = int(data["userId"])
                        # Попробуем найти имя
                        user_link = soup.find("a", href=re.compile(rf"/users/{self.user_id}/"))
                        if user_link:
                            self.username = user_link.get_text(strip=True)
                        if not self.username:
                            # БАГ БЫЛ: имя возвращалось, но НЕ записывалось.
                            # Дальше код, сравнивающий продавца по username,
                            # видел пустую строку и считал лоты чужими.
                            self.username = f"User_{self.user_id}"
                        return True, self.user_id, self.username, None
                except Exception:
                    pass

            # Способ 2: ссылка «Выйти» в меню — она есть только у авторизованных.
            # Раньше здесь искалась ПЕРВАЯ ссылка /users/<id>/, а на главной
            # FunPay их сотни (карточки продавцов). Из-за этого гостю с чужим
            # ключом возвращалось «авторизован» с ID случайного конкурента,
            # а потом бот работал с ЧУЖИМИ лотами.
            if soup.find("a", href=re.compile(r"/account/logout")):
                user_link = soup.find("a", href=re.compile(r"/users/\d+/"))
                if user_link and user_link.get("href"):
                    match = re.search(r"/users/(\d+)/", user_link["href"])
                    if match:
                        self.user_id = int(match.group(1))
                        self.username = user_link.get_text(strip=True)
                return True, self.user_id, self.username, None

            return False, None, None, "Не удалось определить ID пользователя из страницы"
        except Exception as e:
            logger.error(f"Ошибка при проверке авторизации: {e}")
            return False, None, None, str(e)

    @staticmethod
    def _looks_like_guest_page(html: str) -> bool:
        """
        Страница для гостя: есть вход/регистрация и НЕТ пункта «Выйти».
        Раньше проверка была `'login' in resp.text` — слово «login» встречается
        в разметке почти всегда, поэтому проверка по сути не работала.
        """
        if "/account/logout" in (html or ""):
            return False
        low = (html or "").lower()
        return ("вход" in low or "войти" in low or "регистраци" in low
                or "sign in" in low or "log in" in low)

    def logout(self) -> Tuple[bool, str]:
        """
        Настоящий выход из аккаунта FunPay.

        1) Загружает главную страницу и достаёт токен из ссылки «Выйти».
        2) Делает GET по /account/logout?token=... — FunPay убивает сессию
           на своей стороне (golden_key становится невалидным ВЕЗДЕ, а не только
           у нас).
        3) Чистит локальные куки и обнуляет данные авторизации.

        Возвращает (успех, сообщение).
        """
        if not self.golden_key:
            # Даже без ключа стоит почистить локальную сессию
            self._forget_session()
            return True, "Ключ не был сохранён — нечего завершать, локальная сессия очищена"

        # Ключ держим в ЛОКАЛЬНОЙ переменной только на время проверки.
        # Раньше он сохранялся в self._last_key_for_check — то есть оставался
        # в памяти процесса навсегда, хотя пользователь просил его убрать.
        key_before_logout = self.golden_key

        logout_url = ""
        try:
            resp = self._make_request_with_retry("GET", self.BASE_URL)
            if resp is not None and resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                link = soup.find("a", href=re.compile(r"/account/logout"))
                if link and link.get("href"):
                    logout_url = urllib.parse.urljoin(self.BASE_URL, link["href"])
        except Exception as e:
            logger.warning(f"Не удалось получить ссылку выхода: {e}")

        msg = ""
        if logout_url:
            try:
                resp = self._make_request_with_retry("GET", logout_url)
                if resp is not None and resp.status_code in (200, 302, 303):
                    msg = "Сессия FunPay завершена на стороне сервера"
                else:
                    code = getattr(resp, "status_code", "нет ответа")
                    msg = f"FunPay ответил кодом {code} — возможно, сессия уже была закрыта"
            except Exception as e:
                msg = f"Ошибка при выходе: {e}"
        else:
            msg = "Ссылка выхода не найдена на странице (сессия уже не активна?)"

        self._forget_session()

        # Проверка: остался ли доступ. Это главный критерий успеха —
        # ответ страницы может быть 200, а сессия могла не умереть.
        still_alive = False
        probe_error = ""
        probe = None
        try:
            probe = FunPayClient(golden_key=key_before_logout)
            auth_ok, _, _, probe_err = probe.check_auth()
            still_alive = bool(auth_ok)
            if not auth_ok:
                # check_auth возвращает False в двух разных случаях:
                #  • авторитетно «сервер сказал: не авторизован» — это УСПЕХ выхода;
                #  • сетевая/HTTP-ошибка — результат НЕИЗВЕСТЕН.
                # Раньше оба случая шли в probe_error, из-за чего самый частый
                # сценарий (сессия уже была мертва) возвращал ошибку.
                probe_error = str(probe_err or "")
                if not probe_err or "Недействительный golden_key" in probe_error \
                        or "не указан" in probe_error:
                    probe_error = ""  # авторитетный отказ = выход подтверждён
        except Exception as e:
            probe_error = str(e)
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

        if still_alive:
            return False, (f"FunPay всё ещё пускает по этому ключу ({msg}). "
                           "Смените пароль в настройках FunPay — тогда сессия умрёт везде.")
        if probe_error:
            return False, (f"{msg} Не удалось проверить результат: {probe_error}. "
                           "Локально ключ стёрт, но на стороне FunPay он может быть жив.")
        return True, msg or "Выход выполнен"

    def _forget_session(self):
        """
        Стереть ключ, куки и данные авторизации.

        Всё под блокировкой: клиент общий для нескольких потоков, и раньше
        cookie-jar мог очищаться прямо во время записи ответа, а параллельные
        запросы уходили без golden_key (сервер отвечал страницей логина,
        которую парсеры принимали за валидный контент).
        """
        with self._lock:
            self.golden_key = ""
            try:
                self.session.cookies.clear()
            except Exception:
                pass
            self.user_id = None
            self.username = None
            self.csrf_token = None

    def close(self):
        """Закрыть сессию и освободить пул соединений."""
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @staticmethod
    def _interpret_save_response(resp, ok_message: str) -> Tuple[bool, str]:
        """
        Разбирает ответ FunPay на сохранение лота.

        Раньше в 5 местах стояла одинаковая и НЕВЕРНАЯ логика:
            if "error" not in resp.text.lower(): return True, "готово"
        Она ломалась дважды:
          • FunPay отвечает {"done": false} при ошибке — такого ответа нет
            ни слова "error", и операция рапортовалась УСПЕШНОЙ (лот на деле
            не создан / не удалён);
          • любая HTML-страница FunPay содержит слово "error" в JS, поэтому
            успешный ответ попадал в ветку "ошибка".
        Теперь успех должен быть ЯВНО подтверждён (done=true или error=false).
        """
        if resp is None:
            return False, "Нет ответа от FunPay"
        if resp.status_code != 200:
            return False, f"Ошибка HTTP {resp.status_code}"

        try:
            data = resp.json()
        except Exception:
            low = (resp.text or "").lower()
            if "error" in low or "ошибка" in low:
                return False, f"Ответ FunPay содержит ошибки: {(resp.text or '')[:200]}"
            return False, f"Неизвестный ответ FunPay (не JSON): {(resp.text or '')[:200]}"

        if not isinstance(data, dict):
            return False, f"Неизвестный ответ FunPay: {str(data)[:200]}"

        done = data.get("done")
        err = data.get("error")
        errors = data.get("errors")

        if errors:
            if isinstance(errors, dict):
                err_msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
            elif isinstance(errors, (list, tuple)):
                err_msg = ", ".join(str(e) for e in errors)
            else:
                err_msg = str(errors)
            return False, f"Ошибка FunPay: {err_msg}"

        # Явное подтверждение успеха
        if done is True or err is False:
            return True, ok_message

        if err:
            return False, f"Ошибка FunPay: {err}"

        if done is False:
            return False, "FunPay отклонил операцию (done=false)"

        msg = data.get("msg")
        if msg and not done:
            return False, f"Ошибка FunPay: {msg}"

        # Ничего не распознано — считаем провалом, а не успехом
        return False, f"Неизвестный ответ FunPay: {str(data)[:200]}"

    def parse_price(self, price_str: str) -> float:
        """Очищает строку с ценой и преобразует в float с поддержкой пробелов, запятых и валют"""
        if not price_str:
            return 0.0
        # Заменяем неразрывные пробелы и другие пробельные символы
        text = str(price_str).replace("\xa0", " ").replace("\u200b", "").strip()

        # Ищем числовой паттерн с возможными разделителями тысяч и десятичной частью
        # Примеры: '1 250.50', '1,250.50', '1.250,50', '15.50', '100', '0,05'
        match = re.search(r"(\d+(?:[\s.,]\d+)*(?:[.,]\d+)?)", text)
        if not match:
            return 0.0

        # ВАЖНО: раньше удалялся только обычный пробел " ", хотя регулярка
        # [\s.,] ловит ЛЮБОЙ пробельный символ. FunPay/браузеры отдают разделитель
        # тысяч как U+202F (узкий NBSP), U+2009 (thin space), неразрывный U+00A0.
        # Из-за этого float() падал и цена молча становилась 0.0 ₽.
        raw_num = re.sub(r"\s", "", match.group(1))

        # Если есть и запятая и точка (например 1,250.50 или 1.250,50)
        if "," in raw_num and "." in raw_num:
            if raw_num.rfind(".") > raw_num.rfind(","):
                # 1,250.50 -> убираем запятую
                raw_num = raw_num.replace(",", "")
            else:
                # 1.250,50 -> убираем точку, меняем запятую на точку
                raw_num = raw_num.replace(".", "").replace(",", ".")
        elif "," in raw_num:
            raw_num = raw_num.replace(",", ".")

        try:
            return float(raw_num)
        except ValueError:
            logger.warning(f"Не удалось разобрать цену: {price_str!r}")
            return 0.0

    def _make_request_with_retry(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """
        Делает HTTP запрос с автоматическим retry.

        ВАЖНО: повторяются ТОЛЬКО GET-запросы. Раньше при таймауте/обрыве
        повторялся и POST, а сервер к этому моменту мог уже всё выполнить.
        Итог: два одинаковых лота на FunPay или одно и то же сообщение,
        отправленное покупателю дважды. 429 повторять безопасно — сервер
        запрос отверг.

        Возвращает response или None при фатальной ошибке.
        """
        max_retries = 3
        retry_delay = 60  # начальная задержка при 429 (в секундах)
        retry_safe = method.upper() == "GET"

        for attempt in range(max_retries):
            last_attempt = (attempt == max_retries - 1)
            try:
                with self._lock:  # Session не потокобезопасна
                    if method.upper() == "GET":
                        resp = self.session.get(url, timeout=15, **kwargs)
                    elif method.upper() == "POST":
                        resp = self.session.post(url, timeout=15, **kwargs)
                    else:
                        return None

                if resp.status_code == 429:
                    if last_attempt:
                        # Раньше тут ещё и СПАЛИ 180 сек на последней попытке
                        # и только потом выходили из цикла — чистая потеря времени.
                        logger.error(f"FunPay вернул 429 и не отпускает: {url}")
                        return None
                    wait_time = retry_delay * (attempt + 1)
                    logger.warning(f"Ошибка 429 (Too Many Requests). Ожидание {wait_time} сек перед повтором...")
                    time.sleep(wait_time)  # спим ВНЕ блокировки
                    continue

                return resp

            except requests.exceptions.Timeout:
                if not retry_safe:
                    logger.error(f"Таймаут на POST {url} — результат неизвестен, "
                                 "повтор НЕ делаем (иначе будет дубль)")
                    return None
                if last_attempt:
                    break
                logger.warning(f"Таймаут запроса к {url} (попытка {attempt + 1}/{max_retries})")
                time.sleep(10 * (attempt + 1))
            except requests.exceptions.ConnectionError as e:
                if not retry_safe:
                    logger.error(f"Обрыв соединения на POST {url} — результат неизвестен, "
                                 "повтор НЕ делаем (иначе будет дубль): {e}")
                    return None
                if last_attempt:
                    break
                logger.warning(f"Ошибка соединения к {url} (попытка {attempt + 1}/{max_retries}): {e}")
                time.sleep(10 * (attempt + 1))
            except Exception as e:
                logger.error(f"Непредвиденная ошибка при запросе к {url}: {e}")
                return None

        logger.error(f"Не удалось выполнить запрос к {url} после {max_retries} попыток")
        return None

    def _request_with_retry_locked(self, method: str, url: str,
                                    max_retries: int, retry_delay: int, **kwargs) -> Optional[requests.Response]:
        """Совместимость со старыми вызовами (в т.ч. из тестов)."""
        for attempt in range(max_retries):
            try:
                if method.upper() == "GET":
                    resp = self.session.get(url, timeout=15, **kwargs)
                elif method.upper() == "POST":
                    resp = self.session.post(url, timeout=15, **kwargs)
                else:
                    return None

                # Обработка 429 Too Many Requests
                if resp.status_code == 429:
                    wait_time = retry_delay * (attempt + 1)  # увеличиваем задержку каждый раз
                    logger.warning(f"Получена ошибка 429 (Too Many Requests). Ожидание {wait_time} сек перед повтором...")
                    time.sleep(wait_time)
                    continue

                return resp

            except requests.exceptions.Timeout:
                logger.warning(f"Таймаут запроса к {url} (попытка {attempt + 1}/{max_retries})")
                time.sleep(10 * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Ошибка соединения к {url} (попытка {attempt + 1}/{max_retries}): {e}")
                time.sleep(10 * (attempt + 1))
                continue
            except Exception as e:
                logger.error(f"Непредвиденная ошибка при запросе к {url}: {e}")
                return None

        logger.error(f"Не удалось выполнить запрос к {url} после {max_retries} попыток")
        return None

    def get_user_active_lots(self, delay: Optional[Tuple[float, float]] = None
                             ) -> Optional[List[UserLot]]:
        """
        Получает список всех активных лотов пользователя со страницы профиля.

        ВОЗВРАЩАЕТ None, если FunPay ответил защитной страницей (CAPTCHA/лимит).
        None — это НЕ «пустой список»: раньше подменой был [], и вызывающий
        код принимал блокировку за «у пользователя нет лотов».

        delay=(min, max) — пауза перед запросом. По умолчанию anti-detect пауза
        2–4 сек. Панель передаёт меньшую: профиль дёргается часто, и полная
        пауза делала открытие панели мучительным.
        """
        if not self.user_id:
            auth_ok, uid, uname, _err = self.check_auth()
            if not auth_ok or not uid:
                logger.error("Не удалось получить лоты: пользователь не авторизован")
                return []
            self.user_id = uid
            if uname and not self.username:
                self.username = uname

        # Задержка перед запросом для избежания 429 ошибок
        lo, hi = delay if delay else (2.0, 4.0)
        if lo or hi:
            time.sleep(random.uniform(lo, hi))

        profile_url = f"{self.BASE_URL}/users/{self.user_id}/"
        resp = self._make_request_with_retry("GET", profile_url)
        if resp is None:
            logger.error("Не удалось загрузить профиль пользователя")
            return []
        if resp.status_code != 200:
            logger.error(f"Ошибка загрузки профиля: {resp.status_code}")
            return []

        self._extract_csrf(resp.text)

        # БАГ БЫЛ: страница-заглушка FunPay (CAPTCHA / «слишком много запросов»)
        # приходит с кодом 200. Она парсилась в пустой список, и вызывающий
        # код видел «у вас 0 лотов». В панели это выглядело как пустой профиль,
        # и массовое выставление лотов в такой момент создавало ДУБЛИ.
        if is_block_page(resp.text):
            logger.error("FunPay вернул защитную страницу (CAPTCHA/лимит) вместо профиля")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        user_lots = []

        # В FunPay предложения на странице профиля сгруппированы по блокам .offer или .mb20
        offer_blocks = soup.find_all("div", class_=re.compile(r"offer|mb20"))

        # Если блоков нет, берем всю страницу как один блок
        if not offer_blocks:
            offer_blocks = [soup]

        for block in offer_blocks:
            # 1. Извлекаем ссылку на категорию для этого блока
            cat_url = ""
            cat_name = "Раздел"
            cat_id = ""

            # Ищем заголовок категории
            cat_link = block.find("a", href=re.compile(r"/(?:lots|chips)/\d+/?$"))
            if not cat_link:
                for a in block.find_all("a"):
                    h = a.get("href", "")
                    if re.search(r"/(?:lots|chips)/\d+", h) and "trade" not in h and "offer" not in h:
                        cat_link = a
                        break

            if cat_link:
                raw_href = cat_link.get("href", "")
                cat_name = cat_link.get_text(" ", strip=True)
                m = re.search(r"/(?:lots|chips)/(\d+)", raw_href)
                if m:
                    cat_id = m.group(1)
                    is_chip_cat = "/chips/" in raw_href
                    cat_url = f"https://funpay.com/chips/{cat_id}/" if is_chip_cat else f"https://funpay.com/lots/{cat_id}/"

            # 2. Ищем все лоты внутри данного блока
            items = block.find_all("a", class_=re.compile(r"tc-item"))
            if not items:
                items = block.find_all("a", href=re.compile(r"/lots/offer\?id=\d+|/lots/offerEdit\?offer=\d+"))

            for item in items:
                href = item.get("href", "")

                # Извлекаем lot_id
                id_match = re.search(r"[?&](?:offer|id)=(\d+)", href)
                if not id_match:
                    continue
                lot_id = id_match.group(1)

                # Заголовок/описание лота
                desc_el = item.find(class_=re.compile(r"tc-desc-text|tc-desc|offer-desc"))
                title = desc_el.get_text(strip=True) if desc_el else item.get_text(" ", strip=True)

                # Сервер / сторона / подкатегория
                server_el = item.find(class_=re.compile(r"tc-server"))
                server = server_el.get_text(strip=True) if server_el else None

                side_el = item.find(class_=re.compile(r"tc-side"))
                side = side_el.get_text(strip=True) if side_el else None

                # Цена
                price_el = item.find(class_=re.compile(r"tc-price|offer-price"))
                current_price = 0.0
                if price_el:
                    price_text = price_el.get_text(" ", strip=True)
                    current_price = self.parse_price(price_text)

                is_chip = "/chips/" in href or "/chips/" in cat_url

                user_lot = UserLot(
                    lot_id=lot_id,
                    title=title,
                    category_id=cat_id,
                    category_name=cat_name,
                    category_url=cat_url,
                    current_price=current_price,
                    is_chip=is_chip,
                    server=server,
                    side=side,
                )

                if not any(l.lot_id == lot_id for l in user_lots):
                    user_lots.append(user_lot)

        logger.info(f"Найдено {len(user_lots)} лотов в профиле пользователя")
        return user_lots

    def get_category_offers(self, category_url: str) -> List[CompetitorOffer]:
        """
        Загружает страницу раздела/категории и парсит все предложения конкурентов
        """
        if not category_url:
            return []

        # Задержка перед запросом для избежания 429 ошибок
        time.sleep(random.uniform(2.0, 4.0))

        resp = self._make_request_with_retry("GET", category_url)
        if resp is None:
            logger.error(f"Не удалось загрузить категорию {category_url}")
            return []
        if resp.status_code != 200:
            logger.error(f"Ошибка загрузки категории {category_url}: {resp.status_code}")
            return []

        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")

        offers = []
        items = soup.find_all("a", class_=re.compile(r"tc-item"))

        for item in items:
            href = item.get("href", "")

            # Извлекаем offer_id
            id_match = re.search(r"[?&]id=(\d+)", href)
            # БЕЗ ID предложение бесполезно: все сравнения в панели идут по
            # offer_id, и строка с offer_id="" ломала сопоставление разделов.
            # Раньше такие строки молча попадали в результат.
            if not id_match:
                continue
            offer_id = id_match.group(1)

            # Продавец
            user_el = item.find(class_=re.compile(r"media-user-name|tc-user"))
            seller_name = user_el.get_text(strip=True) if user_el else ""

            # Проверка онлайн-статуса
            # FunPay ставит класс .online или .offline на блок пользователя или статус
            is_online = False
            status_el = item.find(class_=re.compile(r"media-user-status|user-status"))
            if status_el:
                classes = status_el.get("class", [])
                is_online = "online" in classes
            else:
                user_block = item.find(class_=re.compile(r"media-user"))
                if user_block and "online" in user_block.get("class", []):
                    is_online = True

            # Seller ID
            seller_id = ""
            user_link_el = item.find("div", class_=re.compile(r"media-user")) or item
            data_user = user_link_el.get("data-user") or item.get("data-user")
            if data_user:
                seller_id = str(data_user)
            elif user_el and user_el.find_parent("a"):
                u_href = user_el.find_parent("a").get("href", "")
                u_match = re.search(r"/users/(\d+)/", u_href)
                if u_match:
                    seller_id = u_match.group(1)

            # Описание/заголовок
            desc_el = item.find(class_=re.compile(r"tc-desc-text|tc-desc"))
            desc = desc_el.get_text(" ", strip=True) if desc_el else ""

            # Сервер / сторона / автовыдача
            server_el = item.find(class_=re.compile(r"tc-server"))
            server = server_el.get_text(strip=True) if server_el else None

            side_el = item.find(class_=re.compile(r"tc-side"))
            side = side_el.get_text(strip=True) if side_el else None

            subcategory_el = item.find(class_=re.compile(r"tc-subcategory"))
            subcategory = subcategory_el.get_text(strip=True) if subcategory_el else None

            # Цена
            price_el = item.find(class_=re.compile(r"tc-price"))
            price = 0.0
            if price_el:
                # В FunPay .tc-price может содержать <div>100 <span>₽</span></div>
                price_text = price_el.get_text(" ", strip=True)
                price = self.parse_price(price_text)

            # Количество
            amount = 1
            amount_el = item.find(class_=re.compile(r"tc-amount"))
            if amount_el:
                amount_text = re.sub(r"\D", "", amount_el.get_text(strip=True))
                if amount_text.isdigit():
                    amount = int(amount_text)

            offer = CompetitorOffer(
                offer_id=offer_id,
                seller_name=seller_name,
                seller_id=seller_id,
                is_online=is_online,
                title=desc,
                server=server,
                side=side,
                subcategory=subcategory,
                price=price,
                amount=amount,
            )
            offers.append(offer)

        return offers

    def get_lot_details(self, lot_id: str, is_chip: bool = False) -> Optional[Dict[str, Any]]:
        """
        Загружает форму редактирования лота (offerEdit) и парсит все поля

        ВАЖНО: параметр is_chip РАНЬШЕ ПРИНИМАЛСЯ, НО НЕ ИСПОЛЬЗОВАЛСЯ — URL
        всегда собирался через /lots/. Для лотов из разделов валют
        (/chips/<node>/) форма лежит по другому пути, поэтому такие лоты
        не открывались вообще («Форма редактирования не найдена»).
        """
        # Задержка перед запросом для избежания 429 ошибок
        time.sleep(random.uniform(2.0, 4.0))

        prefix = "chips" if is_chip else "lots"
        edit_url = f"{self.BASE_URL}/{prefix}/offerEdit?offer={lot_id}"
        resp = self._make_request_with_retry("GET", edit_url)
        if resp is None:
            logger.error(f"Не удалось загрузить форму редактирования лота {lot_id}")
            return None
        if resp.status_code != 200:
            logger.error(f"Не удалось открыть страницу редактирования лота {lot_id}: код {resp.status_code}")
            return None

        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Точный поиск формы редактирования лота
        form = soup.find("form", class_="form-offer-editor") or soup.find("form", action=re.compile(r"offerSave|saveOffer"))
        if not form:
            logger.error(f"Форма редактирования не найдена на странице лота {lot_id}")
            return None

        form_data = {}
        for input_tag in form.find_all(["input", "textarea", "select"]):
            name = input_tag.get("name")
            if not name:
                continue

            if input_tag.name == "textarea":
                form_data[name] = input_tag.get_text()
            elif input_tag.name == "select":
                selected = input_tag.find("option", selected=True)
                form_data[name] = selected.get("value", "") if selected else ""
            elif input_tag.get("type") == "checkbox":
                if input_tag.has_attr("checked"):
                    form_data[name] = "on"
            else:
                form_data[name] = input_tag.get("value", "")

        # Активен ли лот на FunPay
        # ВАЖНО: раньше тут было `form.find("input", {"type":"checkbox","checked":True})`
        # — ЛЮБОЙ отмеченный чекбокс. В форме есть ещё auto_delivery, поэтому
        # снятый с продажи лот с включённой автовыдачей считался активным.
        # Смотрим только на чекбокс активности.
        is_active = bool(
            form_data.get("active") == "on"
            or form_data.get("fields[active]") == "on"
            or form.find("input", {"name": re.compile(r"^fields?\[active\]$"),
                                   "type": "checkbox", "checked": True}) is not None
        )

        # Извлечем категорию / node_id
        node_id = form_data.get("node_id", "")

        # Ссылка на категорию из хлебных крошек
        category_url = ""
        category_name = ""
        breadcrumbs = soup.find("ol", class_=re.compile(r"breadcrumb"))
        if breadcrumbs:
            links = breadcrumbs.find_all("a")
            if links:
                last_link = links[-1]
                category_url = urllib.parse.urljoin(self.BASE_URL, last_link.get("href", ""))
                category_name = last_link.get_text(strip=True)

        # Извлечем текущую чистую цену продавца
        seller_price = self.parse_price(str(form_data.get("price", "0")))

        # Извлечем цену для покупателя из таблицы калькулятора (СБП или первая цена)
        buyer_price = 0.0
        calc_table = soup.find(class_=re.compile(r"js-calc-table|table-buyers-prices"))
        if calc_table:
            for row in calc_table.find_all("tr"):
                row_text = row.get_text(" ", strip=True)
                if "СБП" in row_text or "QR" in row_text or not buyer_price:
                    val = self.parse_price(row.find_all("td")[-1].get_text() if row.find_all("td") else "")
                    if val > 0:
                        buyer_price = val
                        if "СБП" in row_text:
                            break

        # Коэффициент комиссии площадки (Цена покупателю / Цена продавца)
        # Обычно около 1.2236 (комиссия FunPay ~18-22%)
        commission_ratio = 1.223577
        if seller_price > 0 and buyer_price > 0:
            commission_ratio = buyer_price / seller_price

        return {
            "lot_id": lot_id,
            "node_id": node_id,
            "is_active": is_active,
            "category_url": category_url or (f"{self.BASE_URL}/{prefix}/{node_id}/" if node_id else ""),
            "category_name": category_name,
            "form_data": form_data,
            "action_url": urllib.parse.urljoin(self.BASE_URL, form.get("action", f"/{prefix}/offerSave")),
            "seller_price": seller_price,
            "buyer_price": buyer_price or (seller_price * commission_ratio),
            "commission_ratio": commission_ratio,
            "summary": form_data.get("fields[summary][ru]", "") or form_data.get("fields[summary][en]", "") or form_data.get("summary", ""),
        }

    def update_lot_price(self, lot_id: str, new_buyer_price: float, is_chip: bool = False) -> Tuple[bool, str]:
        """
        Обновляет цену лота на FunPay с учетом комиссии сайта

        ⛔ ИЗМЕНЕНИЕ ЦЕН ОТКЛЮЧЕНО (см. PRICE_EDITS_ENABLED).
        Метод всегда возвращает ошибку и НИКОГДА не ходит на сервер FunPay.
        """
        if not PRICE_EDITS_ENABLED:
            return False, ("⛔ Изменение цен отключено. Бот не имеет права менять "
                           "цены лотов — цена остаётся ровно такой, как вы её выставили.")

        # Задержка перед запросом для избежания 429 ошибок
        time.sleep(random.uniform(2.0, 4.0))

        details = self.get_lot_details(lot_id, is_chip=is_chip)
        if not details:
            return False, "Не удалось получить текущие параметры лота"

        form_data = details["form_data"]
        action_url = details["action_url"]
        comm_ratio = details.get("commission_ratio", 1.223577)

        # Вычисляем чистую цену продавца (сколько ввести в поле price формы)
        # target_buyer_price = seller_price * comm_ratio  =>  seller_price = target_buyer_price / comm_ratio
        new_seller_price = new_buyer_price / comm_ratio

        # Форматирование цены: максимум 3 знака после запятой, минимум 0.001 ₽
        if new_seller_price >= 1.0:
            price_str = f"{new_seller_price:.2f}"
        else:
            price_str = f"{new_seller_price:.3f}".rstrip("0").rstrip(".")
            if not price_str or price_str == "0" or float(price_str) < 0.001:
                price_str = "0.001"

        # Обновляем поле цены
        form_data["price"] = price_str

        # Проверяем наличие CSRF токена
        if "csrf_token" not in form_data and self.csrf_token:
            form_data["csrf_token"] = self.csrf_token

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/lots/offerEdit?offer={lot_id}",
            "Origin": self.BASE_URL,
        }

        # Отправляем POST запрос с обработкой 429
        resp = self._make_request_with_retry("POST", action_url, data=form_data, headers=headers)
        if resp is None:
            logger.error(f"Не удалось обновить цену лота {lot_id}")
            return False, "Ошибка при отправке запроса на обновление цены"

        return self._interpret_save_response(
            resp, f"Цена покупателю: {new_buyer_price:.2f} ₽ (чистыми продавцу: {price_str} ₽)")

    def deactivate_lot(self, lot_id: str, is_chip: bool = False) -> Tuple[bool, str]:
        """
        Снимает лот с продажи (active=off через тот же offerSave).
        Лот не удаляется — его можно вернуть вручную.
        """
        time.sleep(random.uniform(2.0, 4.0))

        details = self.get_lot_details(lot_id, is_chip=is_chip)
        if not details:
            return False, "Не удалось получить параметры лота"

        form_data = details["form_data"]
        action_url = details["action_url"]
        form_data.pop("active", None)  # снятый чекбокс не отправляется
        form_data.pop("fields[active]", None)

        if "csrf_token" not in form_data and self.csrf_token:
            form_data["csrf_token"] = self.csrf_token

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/lots/offerEdit?offer={lot_id}",
            "Origin": self.BASE_URL,
        }

        resp = self._make_request_with_retry("POST", action_url, data=form_data, headers=headers)
        if resp is None:
            return False, "Ошибка при отправке запроса"
        return self._interpret_save_response(resp, "Лот снят с продажи")

    def activate_lot(self, lot_id: str, is_chip: bool = False) -> Tuple[bool, str]:
        """
        Возвращает лот в продажу (active=on через тот же offerSave).
        """
        time.sleep(random.uniform(2.0, 4.0))

        details = self.get_lot_details(lot_id, is_chip=is_chip)
        if not details:
            return False, "Не удалось получить параметры лота"

        form_data = details["form_data"]
        action_url = details["action_url"]
        form_data["active"] = "on"

        if "csrf_token" not in form_data and self.csrf_token:
            form_data["csrf_token"] = self.csrf_token

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/lots/offerEdit?offer={lot_id}",
            "Origin": self.BASE_URL,
        }

        resp = self._make_request_with_retry("POST", action_url, data=form_data, headers=headers)
        if resp is None:
            return False, "Ошибка при отправке запроса"
        return self._interpret_save_response(resp, "Лот снова в продаже")

    def update_lot_fields(self, lot_id: str, is_chip: bool = False,
                          summary_ru=None,
                          desc_ru=None,
                          payment_msg_ru=None,
                          summary_en=None,
                          desc_en=None,
                          payment_msg_en=None,
                          ) -> Tuple[bool, str]:
        """
        Точечная правка текстовых полей лота (массовое редактирование):
          summary_ru/en      — краткое описание (видно в таблице);
          desc_ru/en         — подробное описание;
          payment_msg_ru/en  — сообщение покупателю после оплаты.

        Значение поля — строка (жёсткая замена) ИЛИ функция
        callable(текущий_текст_лота) -> новый_текст | None.
        Функция нужна для «умной замены»: у каждого лота меняется только
        изменённый кусок (например ссылка), остальной его текст остаётся.
        Функция вернула None → лот отличается слишком сильно, пропускаем.

        None = поле НЕ трогаем. Пустая строка = очистить поле.
        Цена, тип, наличие, автовыдача и раздел остаются как были.
        """
        time.sleep(random.uniform(2.0, 4.0))

        details = self.get_lot_details(lot_id, is_chip=is_chip)
        if not details:
            return False, "Не удалось получить параметры лота"

        form_data = details["form_data"]
        action_url = details["action_url"]

        field_map = {
            "fields[summary][ru]": summary_ru, "fields[summary][en]": summary_en,
            "fields[desc][ru]": desc_ru, "fields[desc][en]": desc_en,
            "fields[payment_msg][ru]": payment_msg_ru,
            "fields[payment_msg][en]": payment_msg_en,
        }
        changed: List[str] = []
        labels = {
            "fields[summary][ru]": "краткое RU", "fields[summary][en]": "краткое EN",
            "fields[desc][ru]": "описание RU", "fields[desc][en]": "описание EN",
            "fields[payment_msg][ru]": "сообщение после оплаты RU",
            "fields[payment_msg][en]": "сообщение после оплаты EN",
        }
        for name, value in field_map.items():
            if value is None:
                continue
            if callable(value):
                cur = form_data.get(name, "") or ""
                new_text = value(cur)
                if new_text is None:
                    return False, ("текст лота отличается от образца сильнее, "
                                   "чем ожидалось — изменять не стал (обнови этот лот отдельно)")
                form_data[name] = str(new_text)
            else:
                form_data[name] = str(value)
            changed.append(labels[name])

        if not changed:
            return False, "Не выбрано ни одного поля для изменения"

        if "csrf_token" not in form_data and self.csrf_token:
            form_data["csrf_token"] = self.csrf_token

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/lots/offerEdit?offer={lot_id}",
            "Origin": self.BASE_URL,
        }

        resp = self._make_request_with_retry("POST", action_url, data=form_data, headers=headers)
        if resp is None:
            logger.error(f"Не удалось обновить поля лота {lot_id}")
            return False, "Ошибка при отправке запроса на сохранение"
        return self._interpret_save_response(resp, "Изменено: " + ", ".join(changed))

    def get_section_form_fields(self, node_id: str,
                                kind: str = "lots") -> List[Dict[str, Any]]:
        """
        Все выпадающие списки (<select>) формы создания лота в разделе.

        Нужно для пане��и: у FunPay в каждом разделе свои дополнительные поля —
        «с подпиской или без», «Шрифт», сервер/сторона и т.д. Раньше бот их
        молча игнорировал и оставлял значение по умолчанию.

        Возвращает список:
            {"name": "fields[...]", "label": "с подпиской или без",
             "value": "<текущее значение>", "options": [{"value","text"}...]}

        Пустой список, если раздел/форма не открылась.
        """
        prefix = "chips" if str(kind).lower() == "chips" else "lots"
        try:
            resp = self._make_request_with_retry(
                "GET", f"{self.BASE_URL}/{prefix}/offerEdit?node={node_id}")
            if resp is None or resp.status_code != 200:
                return []
            if is_block_page(resp.text):
                logger.warning("get_section_form_fields: FunPay вернул защитную страницу")
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            form = (soup.find("form", class_=re.compile(r"form-offer-editor|js-offer-form"))
                    or soup.find("form", action=re.compile(r"offerSave", re.I)))
            if not form:
                return []

            result: List[Dict[str, Any]] = []
            for sel in form.find_all("select"):
                name = sel.get("name")
                if not name:
                    continue
                options = []
                for o in sel.find_all("option"):
                    val = (o.get("value") or "").strip()
                    txt = o.get_text(strip=True)
                    options.append({"value": val, "text": txt or val})
                if not options:
                    continue
                chosen = sel.find("option", selected=True)
                current = (chosen.get("value", "") if chosen is not None
                           else options[0]["value"])
                result.append({
                    "name": name,
                    "label": _label_for_control(sel, name),
                    "value": current,
                    "options": options,
                })
            return result
        except Exception as e:
            logger.warning(f"Не удалось получить поля формы раздела {node_id}: {e}")
            return []

    def get_section_type_options(self, node_id: str) -> List[Tuple[str, str]]:
        """Опции fields[type] формы создания раздела (для Авто-подбора без создания)."""
        for f in self.get_section_form_fields(node_id):
            if f.get("name") == "fields[type]":
                return [(o["value"], o["text"]) for o in f.get("options", [])]
        return []

    def get_lot_offer_type(self, lot_id: str) -> str:
        """Возвращает ТИП предложения лота (fields[type]) или ''."""
        try:
            d = self.get_lot_details(lot_id)
            if d:
                return ((d.get("form_data", {}) or {}).get("fields[type]", "") or "").strip()
        except Exception:
            pass
        return ""

    def delete_lot(self, lot_id: str, is_chip: bool = False) -> Tuple[bool, str]:
        """
        Удаляет лот навсегда через offerSave (deleted=1). Безвозвратно!
        """
        time.sleep(random.uniform(2.0, 4.0))

        details = self.get_lot_details(lot_id, is_chip=is_chip)
        if not details:
            return False, "Не удалось получить параметры лота"

        form_data = details["form_data"]
        action_url = details["action_url"]
        form_data["deleted"] = "1"

        if "csrf_token" not in form_data and self.csrf_token:
            form_data["csrf_token"] = self.csrf_token

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/lots/offerEdit?offer={lot_id}",
            "Origin": self.BASE_URL,
        }

        resp = self._make_request_with_retry("POST", action_url, data=form_data, headers=headers)
        if resp is None:
            return False, "Ошибка при отправке запроса"
        return self._interpret_save_response(resp, "Лот удалён")

    def raise_lots_for_node(self, node_id: str, kind: str = "lots") -> Tuple[bool, str, int]:
        """
        Поднимает ВСЕ лоты указанного раздела (node_id) на FunPay.
        kind: 'lots' | 'chips' (валютные разделы живут на /chips/.../trade).
        Возвращает:
            (успех: bool, сообщение: str, время_ожидания_сек: int)
        """
        node_id = str(node_id or "").strip()
        if not node_id or not node_id.isdigit():
            return False, "Некорректный node_id", 300
        k = (kind or "lots").strip().lower()
        if k not in ("lots", "chips"):
            k = "lots"

        # Задержка перед запросом для избежания 429 ошибок
        time.sleep(random.uniform(2.0, 4.0))

        # Пробуем сначала свой kind, затем второй (лот мог быть определён
        # как lots, а на деле лежать в chips, и наоборот)
        kinds_to_try = [k] + (["chips"] if k == "lots" else ["lots"])
        resp = None
        trade_url = ""
        last_status = 0
        for kk in kinds_to_try:
            trade_url = f"{self.BASE_URL}/{kk}/{node_id}/trade"
            resp = self._make_request_with_retry("GET", trade_url)
            if resp is None:
                continue
            if resp.status_code != 200:
                last_status = resp.status_code
                resp = None
                continue
            # trade без кнопки поднятия = чужой kind — пробуем следующий
            tmp_soup = BeautifulSoup(resp.text, "html.parser")
            if not tmp_soup.find(class_=re.compile(r"js-lot-raise")):
                # но если страница явно trade со списком своих лотов — оставляем
                if "js-lot-raise" not in resp.text and kk != kinds_to_try[-1]:
                    last_status = 200
                    resp = None
                    continue
            break
        if resp is None:
            logger.error(f"Не удалось загрузить страницу раздела {node_id}")
            return False, f"Ошибка открытия раздела (HTTP {last_status or 'нет ответа'})", 300

        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        raise_btn = soup.find(class_=re.compile(r"js-lot-raise"))
        if not raise_btn:
            return False, "Кнопка поднятия не найдена", 1800

        game_id = str(raise_btn.get("data-game", ""))
        btn_node_id = str(raise_btn.get("data-node", node_id))

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": trade_url,
            "Origin": self.BASE_URL,
        }
        post_url = f"{self.BASE_URL}/lots/raise"
        # БЕЗ CSRF-токена FunPay отклоняет POST. Раньше поле уходило со
        # значением None, requests молча его ВЫБРАСЫВАЛ, и мы получали
        # невнятную «Ошибка при поднятии» без указания причины.
        if not self.csrf_token:
            return False, "Нет CSRF-токена — обновите сессию и повторите", 300

        data = {
            "game_id": game_id,
            "node_id": btn_node_id,
            "csrf_token": self.csrf_token,
        }

        raise_resp = self._make_request_with_retry("POST", post_url, data=data, headers=headers)
        if raise_resp is None:
            return False, "Ошибка при поднятии предложений", 600

        try:
            res_json = raise_resp.json()
        except Exception:
            logger.error(f"Не удалось распарсить JSON при поднятии: {raise_resp.text[:200]}")
            return False, "Ошибка при поднятии предложений", 600

        # Если требуется выбрать подкатегории в модальном окне
        if res_json.get("modal"):
            m_soup = BeautifulSoup(res_json["modal"], "html.parser")
            sub_node_ids = [c.get("value") for c in m_soup.find_all("input", {"type": "checkbox"}) if c.get("value")]
            if sub_node_ids:
                data["node_ids[]"] = sub_node_ids
                raise_resp = self._make_request_with_retry("POST", post_url, data=data, headers=headers)
                if raise_resp is None:
                    return False, "Ошибка при подтверждении подкатегорий", 600
                try:
                    res_json = raise_resp.json()
                except Exception:
                    return False, "Ошибка при подтверждении подкатегорий", 600

        try:
            wait_raw = res_json.get("wait") or 0
            wait_sec = int(str(wait_raw).strip() or 0)
        except (TypeError, ValueError):
            wait_sec = 0
        msg = str(res_json.get("msg", "") or "")
        is_error = bool(res_json.get("error", False))

        # ВАЖНО: раньше здесь стояло `or "поднят" in msg.lower()`.
        # «поднят» — это префикс «поднять», «подняты», «поднято»,
        # поэтому отказ вида «Подождите 2 ч 59 мин, чтобы ПОДНЯТЬ лоты»
        # или «Лоты уже подняты» рапортовался как УСПЕХ, хотя FunPay
        # ничего не поднял. Ориентируемся только на явный признак ошибки.
        if not is_error:
            return True, msg or "Предложения успешно подняты!", wait_sec or 14400
        # Кулдаун FunPay — обычно 4 часа; если сервер не прислал wait, ждём 1 час
        return False, msg or "Ожидание кулдауна (поднятие на перезарядке)", wait_sec or 3600

    def create_lot(
        self,
        node_id: str,
        title: str,
        seller_price: float,
        summary_ru: str = "",
        summary_en: str = "",
        desc_ru: str = "",
        desc_en: str = "",
        payment_msg_ru: str = "",
        payment_msg_en: str = "",
        offer_type: str = "",
        server: str = "",
        side: str = "",
        subcategory: str = "",
        prompt_auto: str = "",
        prompt_response: str = "",
        amount: int = 1,
        method: str = "",
        secrets: str = "",
        section_name: str = "",
        section_kind: str = "",
        extra_fields: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, str]:
        """
        Создает новый лот на FunPay.
        Поля как в форме сайта: ТИП ПРЕДЛОЖЕНИЯ (fields[type]),
        краткое описание (fields[summary]), подробное (fields[desc]),
        сообщение после оплаты (fields[payment_msg]), количество (amount).
        section_kind: 'lots' | 'chips' | '' (авто: сначала свой, потом второй).
        """
        # Задержка перед запросом для избежания 429 ошибок
        time.sleep(random.uniform(2.0, 4.0))

        # Создание — через форму «Добавить предложение» раздела.
        # /lots/new НЕ существует (404), правильные адреса:
        # /lots/offerEdit?node=<id> для обычных лотов,
        # /chips/offerEdit?node=<id> для разделов-валют (chips).
        kind = (section_kind or "").strip().lower()
        kinds = [kind] if kind in ("lots", "chips") else ["lots", "chips"]
        create_url = ""
        resp = None
        soup = None
        form = None
        last_status = 0
        for k in kinds:
            create_url = f"{self.BASE_URL}/{k}/offerEdit?node={node_id}"
            resp = self._make_request_with_retry("GET", create_url)
            if resp is None:
                # БЫЛО: return здесь прерывал цикл по kinds. Смысл цикла —
                # попробовать /lots/, потом /chips/. Из-за одного сетевого сбоя
                # на первом варианте второй (а лоты валют живут именно там)
                # даже не пробовали. Нужно continue.
                logger.warning(f"Не удалось загрузить {create_url}, пробуем следующий раздел")
                continue

            if resp.status_code != 200:
                last_status = resp.status_code
                continue

            if is_block_page(resp.text):
                return False, ("CAPTCHA: FunPay включил защиту от частых запросов. "
                               "Подожди 10–15 минут и повтори.")

            self._extract_csrf(resp.text)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Находим форму создания лота
            form = soup.find("form", class_=re.compile(r"form-offer-editor|js-offer-form"))
            if not form:
                # Попробуем найти форму по action (lots и chips)
                form = soup.find("form", action=re.compile(r"offerSave|saveOffers|/lots/|/chips/"))
            if form:
                break

        if not form or resp is None or soup is None:
            code = last_status or (resp.status_code if resp else 0)
            page_text = ""
            try:
                page_text = soup.get_text(" ", strip=True).lower() if soup else ""
            except Exception:
                pass
            # Лимит FunPay: в игре уже максимум предложений
            if "много предложений в игре" in page_text:
                logger.warning(f"create_lot node={node_id}: лимит предложений в игре")
                return False, (f"В игре (раздел {node_id}) уже максимум предложений — "
                               f"лимит FunPay. Удали ненужные лоты в этом разделе и повтори.")
            # Разбираемся, что за страница: родитель с подразделами?
            kids: List[Tuple[str, str]] = []
            seen = set()
            try:
                for a in soup.find_all("a", href=re.compile(r"offerEdit\?node=\d+")) if soup else []:
                    m = re.search(r"offerEdit\?node=(\d+)", a.get("href", ""))
                    if m and m.group(1) != str(node_id) and m.group(1) not in seen:
                        seen.add(m.group(1))
                        kids.append((m.group(1), a.get_text(" ", strip=True)[:60]))
            except Exception:
                pass
            # Дамп страницы для разбора (что именно отдал FunPay)
            try:
                with open(f"debug_noform_{node_id}.html", "w", encoding="utf-8") as f:
                    f.write(resp.text if resp else "")
            except Exception:
                pass
            title = ""
            try:
                title = soup.title.get_text(strip=True) if soup and soup.title else ""
            except Exception:
                pass
            logger.warning(f"create_lot node={node_id}: нет формы (HTTP {code}, "
                           f"title={title!r}, подразделов: {len(kids)})")
            if kids:
                kid_list = ", ".join(f"{n} ({i})" for i, n in kids[:8])
                return False, (f"Раздел {node_id} — родительский, лот создаётся в подразделе. "
                               f"Выбери в панели: {kid_list}.")
            return False, (f"Форма создания лота не найдена (раздел {node_id}, "
                           f"HTTP {code}). Раздел не принимает предложения "
                           f"напрямую — выбери подраздел.")

        # Собираем данные формы + запоминаем опции select'ов раздела (пары value/text)
        form_data = {}
        select_options: Dict[str, List[Tuple[str, str]]] = {}
        for input_tag in form.find_all(["input", "textarea", "select"]):
            name = input_tag.get("name")
            if not name:
                continue

            if input_tag.name == "textarea":
                form_data[name] = input_tag.get_text()
            elif input_tag.name == "select":
                selected = input_tag.find("option", selected=True)
                form_data[name] = selected.get("value", "") if selected else ""
                select_options[name] = [
                    (o.get("value", ""), o.get_text(strip=True))
                    for o in input_tag.find_all("option")
                ]
            elif input_tag.get("type") == "checkbox":
                if input_tag.has_attr("checked"):
                    form_data[name] = "on"
            else:
                form_data[name] = input_tag.get("value", "")

        # Обязательные поля — всегда
        form_data["node_id"] = node_id
        form_data["price"] = f"{seller_price:.3f}"
        form_data["active"] = "on"

        # Краткое описание (видно в таблице)
        if summary_ru:
            form_data["fields[summary][ru]"] = summary_ru
        if summary_en:
            form_data["fields[summary][en]"] = summary_en

        # Подробное описание
        if desc_ru:
            form_data["fields[desc][ru]"] = desc_ru
        if desc_en:
            form_data["fields[desc][en]"] = desc_en

        # Сообщение покупателю после оплаты
        pay_ru = payment_msg_ru or prompt_auto
        pay_en = payment_msg_en or prompt_auto or prompt_response
        if not pay_en and pay_ru:
            pay_en = pay_ru
        if pay_ru:
            form_data["fields[payment_msg][ru]"] = pay_ru
        if pay_en:
            form_data["fields[payment_msg][en]"] = pay_en

        def _set_select(name: str, value: str):
            """Select раздела: подставляем только существующую опцию,
            иначе оставляем дефолт формы — лот всё равно выставится без ошибки."""
            if not value:
                return
            opts = select_options.get(name, [])
            if not opts:
                form_data[name] = value
                return
            m = match_type_option(opts, value)
            if m:
                form_data[name] = m
            # совпадения нет — не трогаем поле

        # ТИП ПРЕДЛОЖЕНИЯ: у каждого раздела свои типы.
        # Строго: явный выбор — только точное совпадение, иначе ПРОПУСК
        # раздела (никакой тихой подмены в «Услуги» и т.п.).
        # Авто — по названию лота/раздела, иначе тоже пропуск.
        # Пропуск помечается префиксом §SKIP§ — бот засчитает как пропуск,
        # а не как ошибку.
        type_opts = select_options.get("fields[type]", [])
        chosen_type = ""
        if type_opts:
            if offer_type:
                chosen_type = match_type_option(type_opts, offer_type)
                if not chosen_type:
                    have = ", ".join((t or v) for v, t in type_opts
                                     if ((v or "") + (t or "")).strip()) or "—"
                    return False, (
                        f"§SKIP§В разделе нет типа «{offer_type}» (есть: {have}).")
            else:
                chosen_type = resolve_offer_type(
                    type_opts, "", title, summary_ru, section_name)
                if not chosen_type:
                    return False, (
                        "§SKIP§Авто не подобрало тип под раздел. "
                        "Выбери тип вручную.")
                lot_cat = detect_lot_category(f"{title} {summary_ru}") or detect_lot_category(section_name)
                logger.info(f"create_lot node={node_id}: авто-тип '{chosen_type}' (лот: '{lot_cat or '?'}')")
        if chosen_type:
            form_data["fields[type]"] = chosen_type
        if method:
            _set_select("fields[method]", method)
        _set_select("fields[server]", server)
        _set_select("fields[side]", side)
        _set_select("fields[subcategory]", subcategory)

        # Прочие выпадающие списки раздела («с подпиской или без», «Шрифт»
        # и любые другие поля, которые FunPay добавляет в конкретный раздел).
        # Значение проверяется по реальным опциям формы: выдуманный value
        # FunPay примет молча, но сохранит не то, что ты выбрал.
        #
        # fields[type] здесь пропускаем — им уже управляет логика выше
        # со строгим подбором типа.
        if extra_fields:
            for raw_name, raw_value in (extra_fields or {}).items():
                name = str(raw_name or "").strip()
                value = str(raw_value if raw_value is not None else "").strip()
                if not name or not value:
                    continue
                if name == "fields[type]":
                    logger.info("create_lot: fields[type] берётся из логики подбора, "
                                "значение из extra_fields игнорируется")
                    continue
                if name not in select_options:
                    logger.warning(f"create_lot: поля «{name}» нет в форме раздела — пропуск")
                    continue
                matched = None
                for val, txt in select_options.get(name, []):
                    if val == value or txt == value:
                        matched = val
                        break
                if matched is None:
                    m = match_type_option(select_options.get(name, []), value)
                    matched = m or None
                if matched is None:
                    logger.warning(f"create_lot: значения «{value}» нет среди опций «{name}» — "
                                   "оставлено значение по умолчанию FunPay")
                    continue
                if matched != form_data.get(name):
                    form_data[name] = matched

        # Автовыдача секретами: чекбокс + текст
        if secrets:
            form_data["auto_delivery"] = "on"
            form_data["secrets"] = secrets

        # Количество: в форме это обычное поле amount (не fields[amount]).
        # Если в разделе такого поля нет — лот всё равно создаётся.
        try:
            form_data["amount"] = str(max(1, int(amount)))
        except Exception:
            form_data["amount"] = "1"
        form_data.pop("fields[amount]", None)

        # CSRF токен
        if self.csrf_token:
            form_data["csrf_token"] = self.csrf_token

        # Action URL для отправки
        action_url = urllib.parse.urljoin(self.BASE_URL, form.get("action", "/lots/offerSave"))

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": create_url,
            "Origin": self.BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        # Отправляем POST запрос
        resp = self._make_request_with_retry("POST", action_url, data=form_data, headers=headers)
        if resp is None:
            logger.error("Не удалось создать лот")
            return False, "Ошибка при отправке запроса на создание лота"

        try:
            res_json = resp.json()
        except Exception:
            res_json = None

        if res_json is not None:
            # Единый строгий разбор: успех должен быть подтверждён ЯВНО.
            # Раньше здесь стояло `res_json.get("done") or res_json.get("error") is False`
            # — FunPay при ошибке отвечает {"done": false} без поля error,
            # поэтому `not None` == True и несуществующий лот считался созданным.
            ok_msg = (f"Лот успешно создан! (тип: {chosen_type})" if chosen_type
                      else "Лот успешно создан!")
            return self._interpret_save_response(resp, ok_msg)

        # Не-JSON: доверяем только при явном признаке успеха на странице.
        if resp.status_code != 200:
            return False, f"Ошибка HTTP {resp.status_code}"
        if "offerEdit" in resp.url and "error" not in (resp.text or "").lower():
            return True, "Лот успешно создан!"
        # Раньше здесь стояло `else: return True, "Лот создан (проверьте профиль)"`
        # — то есть ЛЮБОЙ непонятный ответ считался успехом.
        return False, (f"Не удалось подтвердить создание лота. Ответ FunPay: "
                       f"{(resp.text or '')[:200]}")

    # ---------- Авто-заказы: продажи, чат, отзывы ----------

    def get_sales(self) -> List[SaleOrder]:
        """Список проданных заказов (страница /orders/trade)."""
        time.sleep(random.uniform(2.0, 4.0))

        resp = self._make_request_with_retry("GET", f"{self.BASE_URL}/orders/trade")
        if resp is None or resp.status_code != 200:
            logger.warning("Не удалось загрузить список продаж")
            return []

        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        sales: List[SaleOrder] = []
        for item in soup.find_all("a", class_=re.compile(r"tc-item")):
            href = item.get("href", "")
            m = re.search(r"/orders/([A-Za-z0-9]+)/?", href)
            if not m:
                continue
            order_id = m.group(1)

            status = "paid"
            st_el = item.find("div", class_=re.compile(r"tc-status"))
            if st_el:
                classes = " ".join(st_el.get("class", []))
                if "text-success" in classes:
                    status = "completed"
                elif "text-warning" in classes:
                    status = "refunded"
                elif "text-primary" in classes:
                    status = "paid"

            price = 0.0
            price_el = item.find("div", class_=re.compile(r"tc-price"))
            if price_el:
                price = self.parse_price(price_el.get_text(" ", strip=True))

            date_el = item.find("div", class_=re.compile(r"tc-date-time"))
            date_text = date_el.get_text(" ", strip=True) if date_el else ""

            title = ""
            desc_el = item.find("div", class_=re.compile(r"order-desc"))
            if desc_el:
                first = desc_el.find("div")
                title = (first.get_text(" ", strip=True) if first
                         else desc_el.get_text(" ", strip=True))

            cat_el = item.find("div", class_=re.compile(r"text-muted"))
            category = cat_el.get_text(" ", strip=True) if cat_el else ""

            buyer_name, buyer_id = "", ""
            user_block = item.find("div", class_=re.compile(r"media-user"))
            if user_block:
                buyer_id = str(user_block.get("data-user") or "")
                name_el = user_block.find(class_=re.compile(r"media-user-name"))
                buyer_name = name_el.get_text(strip=True) if name_el else ""
                if not buyer_id:
                    u_link = user_block.find("a", href=re.compile(r"/users/\d+/"))
                    if u_link:
                        um = re.search(r"/users/(\d+)/", u_link.get("href", ""))
                        if um:
                            buyer_id = um.group(1)

            sales.append(SaleOrder(
                order_id=order_id, title=title, category=category,
                price=price, status=status, buyer_name=buyer_name,
                buyer_id=buyer_id, date_text=date_text,
                url=f"{self.BASE_URL}/orders/{order_id}/",
            ))
        return sales

    def get_section_own_offers(self, section_url: str) -> List[Tuple[str, str]]:
        """Свои офферы в разделе: [(offer_id, title_lower)].
        Используется как истина при проверке дублей (профиль группирует
        всё в одну ноду и для этого не годится)."""
        out: List[Tuple[str, str]] = []
        if not section_url or not self.user_id:
            return out
        time.sleep(random.uniform(1.0, 2.0))

        resp = self._make_request_with_retry("GET", section_url)
        if resp is None or resp.status_code != 200:
            return out
        soup = BeautifulSoup(resp.text, "html.parser")
        me = str(self.user_id)
        for item in soup.find_all("a", class_=re.compile(r"tc-item")):
            href = item.get("href", "")
            m = re.search(r"[?&]id=(\d+)", href)
            if not m:
                continue
            oid = m.group(1)
            own = False
            for blk in [item] + (item.find_all(class_=re.compile(r"media-user")) or []):
                try:
                    if str(blk.get("data-user") or "") == me:
                        own = True
                        break
                except Exception:
                    pass
            if not own:
                for a in item.find_all("a", href=re.compile(r"/users/\d+/")):
                    um = re.search(r"/users/(\d+)/", a.get("href", ""))
                    if um and um.group(1) == me:
                        own = True
                        break
            if not own and self.username:
                nm = item.find(class_=re.compile(r"media-user-name"))
                if nm and nm.get_text(strip=True) == self.username:
                    own = True
            if not own:
                continue
            desc = item.find(class_=re.compile(r"tc-desc-text|tc-desc"))
            title = ((desc.get_text(" ", strip=True) if desc
                      else item.get_text(" ", strip=True)) or "").lower()
            out.append((oid, title))
        return out

    def get_my_trade_offers(self, node_id: str, kind: str = "lots") -> List[Dict[str, Any]]:
        """Свои предложения со страницы trade (включая ВЫКЛЮЧЕННЫЕ).
        Возвращает [{offer_id, title, disabled}].
        disabled = розовый ряд (класс warning) на странице 'Ваши предложения'.
        Пустой список = раздел пуст / нет доступа / ошибка сети (не фатально)."""
        out: List[Dict[str, Any]] = []
        node = str(node_id or "").strip()
        if not node or not node.isdigit():
            return out
        k = (kind or "lots").strip().lower()
        if k not in ("lots", "chips"):
            k = "lots"
        # trade-страница — уже только свои, user_id для неё не обязателен,
        # но без авторизации там делать нечего — выходим быстро (и для тестов).
        if not getattr(self, "user_id", None):
            return out
        time.sleep(random.uniform(1.0, 2.0))
        url = f"{self.BASE_URL}/{k}/{node}/trade"
        try:
            resp = self._make_request_with_retry("GET", url)
        except Exception:
            return out
        if resp is None or resp.status_code != 200:
            return out
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            return out
        for item in soup.find_all("a", class_=re.compile(r"tc-item")):
            try:
                classes = item.get("class", []) or []
                flat = " ".join(classes)
                # offer id: data-offer приоритетно, иначе из href offer=
                oid = str(item.get("data-offer") or "")
                href = item.get("href", "") or ""
                if not oid:
                    m = re.search(r"offer=(\d+)", href)
                    if m:
                        oid = m.group(1)
                if not oid:
                    m2 = re.search(r"[?&]id=(\d+)", href)
                    if m2:
                        oid = m2.group(1)
                if not oid:
                    continue
                desc = item.find(class_=re.compile(r"tc-desc-text|tc-desc"))
                title = ((desc.get_text(" ", strip=True) if desc
                          else item.get_text(" ", strip=True)) or "").strip()
                disabled = ("warning" in classes) or ("warning" in flat)
                out.append({"offer_id": oid, "title": title, "disabled": bool(disabled)})
            except Exception:
                continue
        # дедуп по offer_id, выключенные — первыми
        seen: Dict[str, Dict[str, Any]] = {}
        for o in out:
            seen[str(o.get("offer_id"))] = o
        res = list(seen.values())
        res.sort(key=lambda o: (not o.get("disabled"), str(o.get("offer_id"))))
        return res

    def get_order_details(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Страница заказа: статус, чат, сообщения, отзыв, параметры лота."""
        time.sleep(random.uniform(2.0, 4.0))

        resp = self._make_request_with_retry(
            "GET", f"{self.BASE_URL}/orders/{order_id}/")
        if resp is None or resp.status_code != 200:
            return None

        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")

        status = "paid"
        header = soup.find("h1", class_=re.compile(r"page-header"))
        if header:
            classes = header.get("class", []) + [
                c for s in header.find_all("span") for c in s.get("class", [])]
            flat = " ".join(classes)
            if "text-warning" in flat:
                status = "refunded"
            elif "text-success" in flat:
                status = "completed"

        chat_id: Optional[int] = None
        buyer_name, buyer_id = "", ""
        messages: List[ChatMsg] = []
        chat_div = soup.find("div", class_="chat")
        if chat_div:
            try:
                chat_id = int(chat_div.get("data-id") or 0) or None
            except (TypeError, ValueError):
                chat_id = None
            head = chat_div.find("div", class_=re.compile(r"chat-header"))
            ublock = head.find("div", class_=re.compile(r"media-user")) if head else None
            if ublock is None:
                ublock = chat_div.find("div", class_=re.compile(r"media-user"))
            if ublock:
                buyer_id = str(ublock.get("data-user") or "")
                nm = ublock.find(class_=re.compile(r"media-user-name"))
                buyer_name = nm.get_text(strip=True) if nm else ""
                if not buyer_id:
                    ul = ublock.find("a", href=re.compile(r"/users/\d+/"))
                    if ul:
                        um = re.search(r"/users/(\d+)/", ul.get("href", ""))
                        if um:
                            buyer_id = um.group(1)
            for msg_div in chat_div.find_all("div", class_=re.compile(r"chat-msg-item")):
                mid_m = re.search(r"message-(\d+)", msg_div.get("id", ""))
                if not mid_m:
                    continue
                # ВАЖНО: обнуляем автора НА КАЖДОЕ сообщение.
                # Раньше last_sender/last_name инициализировались один раз ДО цикла
                # и менялись только при наличии ссылки на автора. Системное
                # сообщение FunPay («Покупатель оплатил заказ») такой ссылки не
                # имеет — и наследовало автора ПРЕДЫДУЩЕГО сообщения. Из-за
                # этого is_system становился False, и автоответчик мог ответить
                # на системное уведомление вместо сообщения покупателя.
                last_sender = 0
                last_name = "FunPay"
                author = msg_div.find("a", class_=re.compile(r"chat-msg-author-link"))
                if author:
                    am = re.search(r"/users/(\d+)/", author.get("href", ""))
                    last_sender = int(am.group(1)) if am else 0
                    last_name = author.get_text(strip=True)
                txt_el = msg_div.find("div", class_=re.compile(r"chat-msg-text"))
                text = txt_el.get_text("\n", strip=True) if txt_el else ""
                if msg_div.find("a", class_=re.compile(r"chat-img-link")) and not text:
                    continue  # картинка без текста — отвечать не на что
                messages.append(ChatMsg(
                    msg_id=int(mid_m.group(1)), sender_id=last_sender,
                    sender_name=last_name, text=text,
                    is_system=(last_sender == 0)))
        messages.sort(key=lambda m: m.msg_id)

        review: Optional[OrderReview] = None
        rev_div = soup.find("div", class_=re.compile(r"review-container"))
        if rev_div:
            try:
                rating = int(rev_div.get("data-rating")) if rev_div.get("data-rating") else None
            except (TypeError, ValueError):
                rating = None
            author_id: Optional[int] = None
            row = rev_div.find("div", attrs={"data-row": "review"})
            if row and row.get("data-author"):
                try:
                    author_id = int(row.get("data-author"))
                except (TypeError, ValueError):
                    author_id = None
            txt_el = rev_div.find("div", class_=re.compile(r"review-item-text"))
            text = txt_el.get_text(" ", strip=True) if txt_el else ""
            reply_text, has_reply = "", False
            comp = rev_div.find("div", class_=re.compile(r"review-compiled-reply"))
            if comp:
                for d in comp.find_all("div", recursive=False):
                    if not d.get("class") and d.get_text(strip=True):
                        reply_text = d.get_text(" ", strip=True)
                        has_reply = True
                        break
            review = OrderReview(rating=rating, text=text, author_id=author_id,
                                 has_reply=has_reply, reply_text=reply_text)

        params: Dict[str, str] = {}
        subcat_url = ""
        for pi in soup.find_all("div", class_=re.compile(r"param-item")):
            h5 = pi.find("h5")
            if not h5:
                continue
            val_divs = pi.find_all("div")
            if val_divs:
                params[h5.get_text(strip=True).lower()] = val_divs[-1].get_text(" ", strip=True)
            a = pi.find("a", href=re.compile(r"/(lots|chips)/\d+"))
            if a and not subcat_url:
                subcat_url = a.get("href", "")

        return {"order_id": order_id, "status": status, "chat_id": chat_id,
                "buyer_name": buyer_name, "buyer_id": buyer_id,
                "messages": messages, "review": review,
                "params": params, "subcategory_url": subcat_url}

    def send_chat_message(self, chat_id: int, text: str) -> Tuple[bool, str]:
        """Сообщение в чат заказа через runner (как шлёт сам сайт)."""
        text = (text or "").strip()
        if not text:
            return False, "Пустой текст"
        if not self.csrf_token:
            auth_ok, _, _, _ = self.check_auth()
            if not auth_ok:
                return False, "Нет CSRF (авторизация слетела)"
        time.sleep(random.uniform(1.5, 3.0))

        payload = {
            "objects": json.dumps([{
                "type": "chat_node", "id": chat_id, "tag": "00000000",
                "data": {"node": chat_id, "last_message": -1, "content": ""}}]),
            "request": json.dumps({
                "action": "chat_message",
                "data": {"node": chat_id, "last_message": -1, "content": text}}),
            "csrf_token": self.csrf_token or "",
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/chat/?node={chat_id}",
            "Origin": self.BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = self._make_request_with_retry(
            "POST", f"{self.BASE_URL}/runner/", data=payload, headers=headers)
        if resp is None:
            return False, "Нет ответа FunPay"
        try:
            data = resp.json()
        except Exception:
            return False, f"Не JSON ответ (HTTP {resp.status_code})"
        r = data.get("response") or {}
        if r.get("error"):
            return False, f"FunPay отклонил: {r.get('error')}"
        if not data.get("response"):
            return False, "Сообщение не доставлено (нет response)"
        return True, "Сообщение отправлено"

    def reply_review(self, order_id: str, text: str) -> Tuple[bool, str]:
        """Ответ продавца на отзыв (POST /orders/review с пустым рейтингом)."""
        text = (text or "").strip()
        if not text:
            return False, "Пустой текст"
        if not self.user_id:
            auth_ok, uid, _, _ = self.check_auth()
            if not auth_ok:
                return False, "Нет авторизации"
        if not self.csrf_token:
            self.check_auth()
        time.sleep(random.uniform(1.5, 3.0))

        payload = {"orderId": order_id, "rating": "",
                   "text": text, "authorId": self.user_id or ""}
        if self.csrf_token:
            payload["csrf_token"] = self.csrf_token
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/orders/{order_id}/",
            "Origin": self.BASE_URL,
        }
        resp = self._make_request_with_retry(
            "POST", f"{self.BASE_URL}/orders/review", data=payload, headers=headers)
        if resp is None:
            return False, "Нет ответа FunPay"
        return self._interpret_save_response(resp, "Ответ на отзыв опубликован")

    # ------------------------------------------------------------------
    # Общие чаты (инбокс /chat/) — для авто-приветствия и автоответов
    # ------------------------------------------------------------------

    def get_chat_contacts(self) -> List[Dict[str, Any]]:
        """Список чатов со страницы /chat/.

        Каждый элемент: node_id (id чата), last_msg_id (id последнего
        сообщения из data-node-msg — позволяет заметить новое сообщение,
        НЕ открывая сам чат), name (собеседник), last_text, time_text.
        """
        time.sleep(random.uniform(2.0, 4.0))
        resp = self._make_request_with_retry("GET", f"{self.BASE_URL}/chat/")
        if resp is None or resp.status_code != 200:
            logger.warning("Не удалось загрузить список чатов")
            return []
        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        contacts: List[Dict[str, Any]] = []
        for a in soup.find_all("a", class_="contact-item"):
            node = str(a.get("data-id") or "")
            if not node:
                continue
            try:
                last_msg = int(a.get("data-node-msg") or 0)
            except (TypeError, ValueError):
                last_msg = 0
            name_el = a.find(class_=re.compile(r"media-user-name"))
            msg_el = a.find(class_=re.compile(r"contact-item-message"))
            time_el = a.find(class_=re.compile(r"contact-item-time"))
            contacts.append({
                "node_id": node,
                "last_msg_id": last_msg,
                "name": name_el.get_text(strip=True) if name_el else "",
                "last_text": msg_el.get_text(" ", strip=True) if msg_el else "",
                "time_text": time_el.get_text(" ", strip=True) if time_el else "",
            })
        return contacts

    def get_chat_details(self, node_id) -> Optional[Dict[str, Any]]:
        """Открытый чат /chat/?node=: собеседник и сообщения.

        ВАЖНО: FunPay группирует подряд идущие сообщения одного автора —
        у продолжений нет ссылки на автора. Поэтому сообщение без автора
        наследует АВТОРА ПРЕДЫДУЩЕГО сообщения (в обычных чатах системных
        сообщений не бывает, они есть только на странице заказа).
        """
        time.sleep(random.uniform(2.0, 4.0))
        resp = self._make_request_with_retry(
            "GET", f"{self.BASE_URL}/chat/?node={node_id}")
        if resp is None or resp.status_code != 200:
            logger.warning(f"Чат {node_id}: не открылся")
            return None
        self._extract_csrf(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        chat_div = soup.find("div", class_="chat")
        if not chat_div:
            return None

        # Собеседник: ссылка на пользователя в шапке чата.
        user_id, user_name = "", ""
        head = chat_div.find("div", class_=re.compile(r"chat-header"))
        ublock = head.find("div", class_=re.compile(r"media-user")) if head else None
        if ublock is None:
            ublock = chat_div.find("div", class_=re.compile(r"media-user"))
        if ublock:
            nm = ublock.find(class_=re.compile(r"media-user-name"))
            if nm:
                user_name = nm.get_text(strip=True)
            ul = nm.find("a", href=re.compile(r"/users/\d+/")) if nm else None
            if ul is None:
                ul = ublock.find("a", href=re.compile(r"/users/\d+/"))
            if ul:
                um = re.search(r"/users/(\d+)/", ul.get("href", ""))
                if um:
                    user_id = um.group(1)
        if not user_id:
            # запасной вариант: data-name="users-<id1>-<id2>" на контейнере
            dn = chat_div.get("data-name") or ""
            ids = re.findall(r"\d+", dn)
            me = str(self.user_id or "")
            other = [x for x in ids if x != me]
            if other:
                user_id = other[0]

        messages: List[ChatMsg] = []
        last_sender = 0
        last_name = "FunPay"
        for msg_div in chat_div.find_all("div", class_=re.compile(r"chat-msg-item")):
            mid_m = re.search(r"message-(\d+)", msg_div.get("id", ""))
            if not mid_m:
                continue
            author = msg_div.find("a", class_=re.compile(r"chat-msg-author-link"))
            if author:
                am = re.search(r"/users/(\d+)/", author.get("href", ""))
                last_sender = int(am.group(1)) if am else 0
                last_name = author.get_text(strip=True)
            # если ссылки нет — это продолжение предыдущего автора:
            # last_sender НЕ сбрасываем (в отличие от страницы заказа).
            txt_el = msg_div.find("div", class_=re.compile(r"chat-msg-text"))
            text = txt_el.get_text("\n", strip=True) if txt_el else ""
            if msg_div.find("a", class_=re.compile(r"chat-img-link")) and not text:
                continue  # картинка без текста — отвечать не на что
            messages.append(ChatMsg(
                msg_id=int(mid_m.group(1)), sender_id=last_sender,
                sender_name=last_name, text=text,
                is_system=(last_sender == 0)))
        messages.sort(key=lambda m: m.msg_id)

        return {"node_id": str(node_id), "chat_id": chat_div.get("data-id"),
                "user_id": user_id, "user_name": user_name,
                "messages": messages}


def html_unescape(s: str) -> str:
    """Безопасное декодирование HTML сущностей"""
    import html
    return html.unescape(s)


def is_block_page(html: str) -> bool:
    """Строгий детект страницы-заглушки FunPay (капча / лимиты вместо контента).

    Осторожно: обычные страницы FunPay содержат слово 'captcha' в футере
    про куки — это НЕ блок. Блок = нет полезной страницы + явный виджет
    проверки или текст лимита.
    """
    t = (html or "")
    tl = t.lower()
    if not t:
        return True
    # Нормальная страница редактора/сайта: есть форма или контент лотов —
    # не блок, даже если в футере упоминается капча.
    has_form = ("form-offer-editor" in tl or "offersave" in tl
                or 'name="csrf_token"' in tl or 'name="csrf-token"' in tl)
    has_content = ("/lots/" in tl and ("/users/" in tl or "tc-item" in tl
                                        or "promo-game-item" in tl))
    if has_form or has_content:
        # Поверх нормальной страницы почти не бывает челленджа, но явные
        # маркеры Cloudflare всё равно считаем блоком.
        if ("cf-challenge" in tl or "cf-turnstile" in tl
                or "just a moment" in tl or "checking your browser" in tl
                or "attention required" in tl):
            return True
        return False
    strong_markers = (
        "cf-challenge", "cf-turnstile", "g-recaptcha", "recaptcha/api",
        "hcaptcha", "just a moment", "checking your browser",
        "attention required", "подтвердите, что вы не робот",
        "too many requests", "слишком много запросов",
        "пройдите проверку", "prove you are human",
    )
    if any(m in tl for m in strong_markers):
        return True
    # Упоминание капчи на короткой странице без контента — заглушка.
    # Длинная страница с контентом — просто футер, не блок.
    if "captcha" in tl or "капч" in tl:
        return len(t) < 15000
    return False

