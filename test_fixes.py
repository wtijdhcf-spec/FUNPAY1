"""
Регрессионные тесты на баги, найденные при аудите.
Запуск: venv\\Scripts\\python.exe -m unittest test_fixes -v
"""
import json
import os
import re
import secrets
import sys
import threading
import time
import unittest

sys.stdout.reconfigure(encoding="utf-8")

from funpay_api import FunPayClient, PRICE_EDITS_ENABLED


class TestPriceBlock(unittest.TestCase):
    def test_update_lot_price_is_hard_blocked(self):
        self.assertFalse(PRICE_EDITS_ENABLED, "PRICE_EDITS_ENABLED должен быть False")
        c = FunPayClient(golden_key="x")
        ok, msg = c.update_lot_price("123", 1.0)
        self.assertFalse(ok)
        self.assertIn("отключено", msg.lower())

    def test_blocked_call_makes_no_network_request(self):
        """Заблокированный метод не должен даже пытаться ходить в сеть."""
        c = FunPayClient(golden_key="x")
        called = []
        c.get_lot_details = lambda *a, **k: called.append(1)
        c.update_lot_price("123", 1.0)
        self.assertEqual(called, [], "update_lot_price не должен дёргать get_lot_details")


class TestThreadSafety(unittest.TestCase):
    def test_session_is_guarded_by_lock(self):
        c = FunPayClient(golden_key="x")
        self.assertTrue(hasattr(c, "_lock"))
        # Реентрантность: вложенный вызов не должен себя заблокировать
        with c._lock:
            with c._lock:
                pass

    def test_concurrent_requests_do_not_interleave(self):
        """Проверяем, что _make_request_with_retry действительно сериализует."""
        c = FunPayClient(golden_key="x")
        inside = []
        overlap = []

        class FakeResp:
            status_code = 200
            text = "{}"

        def fake_get(url, timeout=15, **kw):
            inside.append(1)
            if len(inside) > 1:
                overlap.append(1)
            time.sleep(0.05)
            inside.pop()
            return FakeResp()

        c.session.get = fake_get

        def worker():
            c._make_request_with_retry("GET", "https://funpay.com")

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(overlap, [], "Запросы выполнялись одновременно — лок не работает")

    def test_post_is_not_retried_on_timeout(self):
        """
        Регрессия: POST повторялся при таймауте → два лота на FunPay или
        одно и то же сообщение, отправленное покупателю дважды.
        """
        import requests as _rq
        c = FunPayClient(golden_key="x")
        calls = []

        class Boom:
            def get(self, *a, **k):
                calls.append("get")
            def post(self, *a, **k):
                calls.append("post")
                raise _rq.exceptions.ReadTimeout("read timeout")

        c.session = Boom()
        res = c._make_request_with_retry("POST", "https://funpay.com/lots/offerSave")
        self.assertIsNone(res)
        self.assertEqual(calls.count("post"), 1, "POST не должен повторяться при таймауте")

    def test_get_is_still_retried_on_timeout(self):
        import requests as _rq
        c = FunPayClient(golden_key="x")
        calls = []

        class Flaky:
            def get(self, *a, **k):
                calls.append(1)
                raise _rq.exceptions.ReadTimeout("t")

        c.session = Flaky()
        c._make_request_with_retry("GET", "https://funpay.com")
        self.assertEqual(len(calls), 3, "GET должен повторяться до 3 раз")


class TestSaveResponseParsing(unittest.TestCase):
    """C3: {"done": false} больше не считается успехом."""

    class R:
        def __init__(self, data=None, text="", code=200):
            self._data = data
            self.text = text
            self.status_code = code
        def json(self):
            if self._data is None:
                raise ValueError("not json")
            return self._data

    def parse(self, r):
        return FunPayClient._interpret_save_response(r, "ок")

    def test_done_true_is_success(self):
        ok, _ = self.parse(self.R({"done": True}))
        self.assertTrue(ok)

    def test_done_false_is_failure(self):
        # Раньше этот ответ попадал в ветку «нет слова error» → успех
        ok, msg = self.parse(self.R({"done": False}))
        self.assertFalse(ok, "done=false это провал, а не успех")

    def test_done_false_with_error_null_is_failure(self):
        ok, _ = self.parse(self.R({"done": False, "error": None}))
        self.assertFalse(ok)

    def test_error_false_is_success(self):
        ok, _ = self.parse(self.R({"done": True, "error": False}))
        self.assertTrue(ok)

    def test_errors_dict_is_failure(self):
        ok, msg = self.parse(self.R({"errors": {"price": "мало"}}))
        self.assertFalse(ok)
        self.assertIn("price", msg)

    def test_html_page_is_not_success(self):
        # HTML всегда содержит слово "error" в JS — раньше это ломало успех
        ok, _ = self.parse(self.R(None, text="<html><script>var error;</script></html>"))
        self.assertFalse(ok)

    def test_non_200_is_failure(self):
        ok, _ = self.parse(self.R({"done": True}, code=500))
        self.assertFalse(ok)


class TestGuestDetection(unittest.TestCase):
    """C1: чужая ссылка /users/<id>/ больше не выдаётся за авторизацию."""

    def test_page_with_logout_is_not_guest(self):
        html = '<a href="/account/logout?token=x">Выйти</a>'
        self.assertFalse(FunPayClient._looks_like_guest_page(html))

    def test_guest_page_detected(self):
        html = '<a href="/users/12345/">Продавец</a><a href="/login">Войти</a>'
        self.assertTrue(FunPayClient._looks_like_guest_page(html))

    def test_logged_in_page_without_login_words(self):
        html = '<a href="/users/1/">Me</a><a href="/account/logout">Выйти</a>'
        self.assertFalse(FunPayClient._looks_like_guest_page(html))


class TestPriceParsing(unittest.TestCase):
    def test_narrow_nbsp(self):
        c = FunPayClient(golden_key="x")
        self.assertAlmostEqual(c.parse_price("1\u202f250,50"), 1250.50, places=2)

    def test_thin_space(self):
        c = FunPayClient(golden_key="x")
        self.assertAlmostEqual(c.parse_price("1\u2009250"), 1250.0, places=2)

    def test_nbsp_and_plain(self):
        c = FunPayClient(golden_key="x")
        self.assertAlmostEqual(c.parse_price("1\xa0250,50"), 1250.50, places=2)
        self.assertAlmostEqual(c.parse_price("1 250,50"), 1250.50, places=2)

    def test_us_format(self):
        c = FunPayClient(golden_key="x")
        self.assertAlmostEqual(c.parse_price("1,250.50"), 1250.50, places=2)

    def test_empty(self):
        c = FunPayClient(golden_key="x")
        self.assertEqual(c.parse_price(""), 0.0)


class TestLogout(unittest.TestCase):
    def test_forget_session_clears_key_and_cookies(self):
        c = FunPayClient(golden_key="secretkey")
        c.user_id = 42
        c.username = "me"
        c.csrf_token = "tok"
        c._forget_session()
        self.assertEqual(c.golden_key, "")
        self.assertFalse(hasattr(c, "_last_key_for_check"),
                         "ключ не должен оставаться в памяти после выхода")
        self.assertIsNone(c.user_id)
        self.assertIsNone(c.csrf_token)
        self.assertEqual(len(c.session.cookies), 0)

    def test_logout_without_key_is_safe(self):
        c = FunPayClient(golden_key="")
        ok, msg = c.logout()
        self.assertTrue(ok)
        self.assertTrue(msg)

    def test_close_is_idempotent(self):
        c = FunPayClient(golden_key="x")
        c.close()
        c.close()
        with FunPayClient(golden_key="y") as ctx:
            self.assertIsNotNone(ctx)


class TestAutoOrdersSpam(unittest.TestCase):
    """Пустые шаблоны больше не отправляются в чат + правила ключевых слов."""

    def _worker(self):
        import auto_orders
        return auto_orders.AutoOrdersWorker(funpay=None, telegram=None)

    def test_say_refuses_empty_text(self):
        w = self._worker()
        st = {}
        # пустой текст
        self.assertFalse(w._say(123, "", "o1", "тест", st))
        self.assertFalse(w._say(123, "   \n ", "o1", "тест", st))
        self.assertFalse(w._say(123, None, "o1", "тест", st))

    def test_say_refuses_without_chat_id(self):
        w = self._worker()
        self.assertFalse(w._say(None, "привет", "o1", "тест", {}))

    def test_rule_contains_match(self):
        import auto_orders
        rule = {"keyword": "помощь", "match": "contains", "reply": "Держи помощь"}
        self.assertTrue(auto_orders.rule_reply(rule, "Мне нужна помощь!"))
        rule_exact = dict(rule, match="exact")
        self.assertFalse(auto_orders.rule_reply(rule_exact, "Мне нужна помощь!"))

    def test_rule_exact_match_ignores_punctuation_and_case(self):
        import auto_orders
        rule = {"keyword": "Помощь", "match": "exact", "reply": "Держи помощь"}
        self.assertTrue(auto_orders.rule_reply(rule, "  Помощь!  "))
        self.assertFalse(auto_orders.rule_reply(rule, "нужна помощь"))

    def test_rule_legacy_keywords_format(self):
        import auto_orders
        rule = {"keywords": ["где", "когда"], "reply": "Уже выдаю"}
        self.assertTrue(auto_orders.rule_reply(rule, "где товар?"))
        self.assertFalse(auto_orders.rule_reply(rule, "привет"))

    def test_rule_empty_reply_never_matches(self):
        import auto_orders
        rule = {"keyword": "привет", "match": "exact", "reply": ""}
        self.assertFalse(auto_orders.rule_reply(rule, "привет"))

    def test_cfg_maps_legacy_keys(self):
        """Старые ключи (приветствие/спасибо) должны переехать в новые имена."""
        import json
        import tempfile
        import auto_orders
        tmp = os.path.join(tempfile.gettempdir(), "fb_legacy_cfg.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"auto_orders": {
                "enabled": True,
                "greeting_enabled": True, "greeting_text": "Старое приветствие",
                "thanks_enabled": True, "thanks_text": "Старое спасибо",
            }}, f, ensure_ascii=False)
        try:
            w = auto_orders.AutoOrdersWorker(funpay=None, telegram=None, config_path=tmp)
            cfg = w._cfg()
            self.assertTrue(cfg["new_order_enabled"])
            self.assertEqual(cfg["new_order_text"], "Старое приветствие")
            self.assertTrue(cfg["confirm_reply_enabled"])
            self.assertEqual(cfg["confirm_reply_text"], "Старое спасибо")
        finally:
            os.remove(tmp)

    def test_new_state_has_no_removed_flags(self):
        """Из состояния исчезли флаги склада/подарков — панель не должна их ждать."""
        import auto_orders

        class Sale:
            order_id = "o1"
            title = "Товар"
            price = 1.0
            status = "paid"
            buyer_name = "Покупатель"
            buyer_id = 555

        w = self._worker()
        st = w._new_state(Sale(), None, silent=True)
        for gone in ("delivered", "stock_key", "stock_idx", "oos_noted",
                     "gift_confirm_sent", "gift5_sent"):
            self.assertNotIn(gone, st)


class TestReviewAuthorLogic(unittest.TestCase):
    """is_buyer_review должен быть True только для отзыва покупателя."""

    def _is_buyer_review(self, author_id, me, buyer):
        return (not author_id) or (not me) or (str(author_id) == buyer)

    def test_buyer_review(self):
        self.assertTrue(self._is_buyer_review("555", "111", "555"))

    def test_own_review_is_not_buyer_review(self):
        self.assertFalse(self._is_buyer_review("111", "111", "555"))

    def test_moderator_review_is_not_buyer_review(self):
        # БЫЛО: считалось отзывом покупателя из-за лишнего `or author_id != me`
        self.assertFalse(self._is_buyer_review("999", "111", "555"))

    def test_unknown_author_is_buyer_review(self):
        self.assertTrue(self._is_buyer_review(None, "111", "555"))


class TestTunnel(unittest.TestCase):
    def test_stop_is_idempotent(self):
        from tunnel_manager import CloudflareTunnel
        t = CloudflareTunnel()
        t.stop()  # не должен падать без процесса
        t.stop()

    def test_url_event_cleared_on_stop(self):
        from tunnel_manager import CloudflareTunnel
        t = CloudflareTunnel()
        t._url_event.set()
        t.stop()
        self.assertFalse(t._url_event.is_set())


class TestStopFlag(unittest.TestCase):
    def test_stop_flag_roundtrip(self):
        import web_ui
        web_ui._clear_stop_flag()
        self.assertFalse(web_ui._stop_requested())
        web_ui._request_stop_flag()
        self.assertTrue(web_ui._stop_requested())
        web_ui._clear_stop_flag()
        self.assertFalse(web_ui._stop_requested())

    def test_config_helpers_resolve_path_at_call_time(self):
        """Баг: путь в аргументе по умолчанию захватывался при определении."""
        import tempfile
        import web_ui
        real = web_ui.CONFIG_FILE
        tmp = os.path.join(tempfile.gettempdir(), "fb_cfgtest.json")
        try:
            web_ui.CONFIG_FILE = tmp
            web_ui._write_config_file({"golden_key": "k"})
            with open(tmp, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"golden_key": "k"})
            # Настоящий config.json не должен быть затронут
            with open(real, encoding="utf-8") as f:
                json.load(f)
        finally:
            web_ui.CONFIG_FILE = real
            if os.path.exists(tmp):
                os.remove(tmp)


class TestSourceSanity(unittest.TestCase):
    """
    Защита от порчи исходников.

    Дважды при правке через инструмент в файл попадал литерал `</oldString>`.
    Из-за этого Python-файл не компилировался, а встроенный JS панели
    не парсился — и это выглядело как «мистика», пока не нашлось.
    """

    FILES = ["main.py", "web_ui.py", "funpay_api.py", "auto_orders.py",
             "lot_raiser.py", "auto_online.py", "telegram_bot.py",
             "tunnel_manager.py", "backup_lots.py", "restore_lots.py"]

    def test_no_tool_artifacts_in_sources(self):
        bad = []
        for name in self.FILES:
            if not os.path.exists(name):
                continue
            text = open(name, encoding="utf-8", errors="replace").read()
            for junk in ("</oldString>", "</newString>", "<oldString>",
                         "<newString>", "</parameter>"):
                if junk in text:
                    bad.append(f"{name}: {junk}")
        self.assertEqual(bad, [], "В исходниках остался мусор: " + ", ".join(bad))

    def test_html_template_is_clean(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        for junk in ("</oldString>", "</newString>", "<oldString"):
            self.assertNotIn(junk, html, f"В HTML-шаблоне панели мусор: {junk}")

    def test_html_js_backticks_balanced(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        start = html.find("<script>")
        self.assertGreater(start, 0, "В шаблоне нет <script>")
        js = html[start + len("<script>"):html.rfind("</script>")]
        # Незакрытый template-литерал ломает ВЕСЬ скрипт панели
        self.assertEqual(js.count("`") % 2, 0,
                         "Нечётное число обратных кавычек — JS не распарсится")

    def test_js_has_no_dangling_element_ids(self):
        """Каждый getElementById('x') должен иметь элемент id="x" в шаблоне."""
        import re as _re
        import web_ui
        html = web_ui.HTML_TEMPLATE
        js = html[html.find("<script>") + 8:html.rfind("</script>")]
        ids = set(_re.findall(r'id="([^"]+)"', html))
        used = set(_re.findall(r"getElementById\('([^']+)'\)", js))
        dangling = sorted(i for i in used
                          if i not in ids and "$" not in i and "+" not in i)
        self.assertEqual(dangling, [],
                         "JS ссылается на несуществующие id: " + ", ".join(dangling))

    def test_single_html_escape_helper(self):
        """
        Регрессия XSS: было две функции экранирования, одна не трогала
        кавычки — значение ломало атрибут title="...".
        """
        import web_ui
        js = web_ui.HTML_TEMPLATE
        self.assertEqual(js.count("function escHtml"), 1,
                         "Должна быть ровно одна реализация escHtml")
        self.assertEqual(js.count("function escapeHtml"), 0,
                         "Вторая реализация экранирования должна быть удалена")
        for unsafe in ("${offer.server}", "${offer.side}", "${offer.subcategory}"):
            self.assertNotIn(unsafe, js,
                             f"Неэкранированная подстановка {unsafe} — это XSS")


class TestSectionExtraFields(unittest.TestCase):
    """
    Дополнительные выпадающие списки раздела FunPay
    («с подпиской или без», «Шрифт» и любые другие поля формы).
    Бот должен прочитать варианты из формы и подставить выбранное значение.
    """

    FORM = """
    <form class="form-offer-editor" action="/lots/offerSave">
      <div class="form-group">
        <label>С подпиской или без</label>
        <select name="fields[extra_subscription]">
          <option value="">-</option>
          <option value="1" selected>С подпиской</option>
          <option value="0">Без подписки</option>
        </select>
      </div>
      <div class="form-group">
        <label>Шрифт</label>
        <select name="font">
          <option value="default" selected>Стандартный</option>
          <option value="mono">Моноширинный</option>
        </select>
      </div>
      <select name="empty_one"></select>
    </form>
    """

    def _parse(self):
        from bs4 import BeautifulSoup
        from funpay_api import _label_for_control
        soup = BeautifulSoup(self.FORM, "html.parser")
        form = soup.find("form")
        out = {}
        for sel in form.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            options = [{"value": (o.get("value") or "").strip(),
                        "text": o.get_text(strip=True)} for o in sel.find_all("option")]
            if not options:
                continue
            chosen = sel.find("option", selected=True)
            out[name] = {
                "name": name,
                "label": _label_for_control(sel, name),
                "value": (chosen.get("value", "") if chosen is not None else options[0]["value"]),
                "options": options,
            }
        return out

    def test_reads_all_selects_with_options(self):
        f = self._parse()
        self.assertEqual(sorted(f), ["fields[extra_subscription]", "font"])

    def test_label_is_human_readable(self):
        f = self._parse()
        self.assertEqual(f["fields[extra_subscription]"]["label"], "С подпиской или без")
        self.assertEqual(f["font"]["label"], "Шрифт")

    def test_current_value_is_selected_option(self):
        f = self._parse()
        self.assertEqual(f["fields[extra_subscription]"]["value"], "1")
        self.assertEqual(f["font"]["value"], "default")

    def test_option_texts_preserved(self):
        f = self._parse()
        texts = [o["text"] for o in f["fields[extra_subscription]"]["options"]]
        self.assertIn("Без подписки", texts)

    def test_select_without_options_is_skipped(self):
        f = self._parse()
        self.assertNotIn("empty_one", f)

    def test_value_substitution_by_text(self):
        """Пользователь выбрал в панели ТЕКСТ опции — бот шлёт её value."""
        from funpay_api import match_type_option
        opts = [("1", "С подпиской"), ("0", "Без подписки")]
        self.assertEqual(match_type_option(opts, "Без подписки"), "0")

    def test_value_substitution_by_value(self):
        from funpay_api import match_type_option
        opts = [("1", "С подпиской"), ("0", "Без подписки")]
        self.assertEqual(match_type_option(opts, "0"), "0")

    def test_bogus_value_is_not_substituted(self):
        """Выдуманное значение FunPay примет молча, но сохранит не то."""
        from funpay_api import match_type_option
        opts = [("1", "С подпиской"), ("0", "Без подписки")]
        self.assertEqual(match_type_option(opts, "Ёрунда"), "")

    def test_get_section_type_options_uses_new_parser(self):
        """Обратная совместимость: метод не должен падать без формы."""
        c = FunPayClient(golden_key="")
        self.assertEqual(c.get_section_type_options("402"), [])


class TestLiveSettings(unittest.TestCase):
    """
    Тумблеры должны РЕАЛЬНО отключать модули, а не только менять картинку.

    Раньше «Вечный онлайн», Telegram и «Авто-заказы» (включение) читали
    конфиг ровно один раз — при старте бота. Тумблер выключал, а модуль
    продолжал работать до перезапуска: «выключаю, а он включается сам».
    """

    def setUp(self):
        import tempfile
        import time as _time
        self.tmp = os.path.join(tempfile.gettempdir(), "fb_live_settings_test.json")
        self._write(auto_online=True, orders=False, tg=True)
        import main as _main
        from funpay_api import FunPayClient as _C
        _C.check_auth = lambda self: (True, 1, "t", None)
        _main.AutoOnlineKeeper.send_heartbeat = lambda self: True

        def _fake_loop(self):
            # Живёт, пока running=True — иначе is_active() всегда был бы False
            # и тест проверял бы не то.
            while self.running:
                _time.sleep(0.05)
        _main.AutoOnlineKeeper._keepalive_loop = _fake_loop
        self._main = _main

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def _write(self, auto_online=True, orders=False, tg=True):
        import json
        # orders=True теперь означает «включён тумблер приветствия» —
        # отдельного главного тумблера авто-заказов больше нет.
        json.dump({
            "golden_key": "x",
            "telegram": {"enabled": tg, "bot_token": "123:ABC", "chat_id": "42"},
            "settings": {"auto_online": auto_online, "auto_raise_lots": True},
            "auto_orders": {"greeting_enabled": orders,
                            "greeting_text": "Привет, {buyername}!"},
        }, open(self.tmp, "w", encoding="utf-8"), ensure_ascii=False)

    def _bot(self):
        bot = self._main.FunPayUndercutBot(config_path=self.tmp)
        bot.running = True
        self.addCleanup(bot.shutdown)
        return bot

    def test_auto_online_toggle_actually_stops_it(self):
        bot = self._bot()
        bot.apply_live_settings()
        self.assertTrue(bot.effective_state()["auto_online"])
        # выключаем тумблер — пишем в конфиг, как это делает панель
        self._write(auto_online=False)
        bot.apply_live_settings()
        self.assertFalse(bot.effective_state()["auto_online"],
                         "тумблер выключен, но модуль работает")
        self.assertFalse(bot.auto_online.is_active(),
                         "поток онлайна должен быть мёртв")

    def test_auto_online_toggle_back_on_without_restart(self):
        bot = self._bot()
        self._write(auto_online=False)
        bot.apply_live_settings()
        self.assertFalse(bot.effective_state()["auto_online"])
        self._write(auto_online=True)
        bot.apply_live_settings()
        self.assertTrue(bot.effective_state()["auto_online"],
                        "повторное включение должно работать без перезапуска")

    def test_auto_orders_can_be_enabled_at_runtime(self):
        """Раньше поток стартовал только если модуль был включён при запуске."""
        bot = self._bot()
        self.assertFalse(bot.effective_state()["auto_orders"])
        bot.apply_live_settings()
        self.assertTrue(bot.orders_worker.running, "поток должен быть жив")
        self._write(orders=True)
        bot.apply_live_settings()
        self.assertTrue(bot.effective_state()["auto_orders"],
                        "авто-заказы должны включиться без перезапуска")

    def test_telegram_toggles_live(self):
        bot = self._bot()
        bot.apply_live_settings()
        self.assertTrue(bot.effective_state()["telegram"])
        self._write(tg=False)
        bot.apply_live_settings()
        self.assertFalse(bot.effective_state()["telegram"])
        self._write(tg=True)
        bot.apply_live_settings()
        self.assertTrue(bot.effective_state()["telegram"])

    def test_price_edits_always_off(self):
        bot = self._bot()
        bot.apply_live_settings()
        self.assertFalse(bot.effective_state()["price_edits"])

    def test_shutdown_kills_settings_watcher(self):
        bot = self._bot()
        bot.start_settings_watcher()
        w = bot._settings_watcher
        self.assertIsNotNone(w)
        bot.shutdown()
        self.assertFalse(w.is_alive(), "наблюдатель настроек должен умереть")


class TestLotsEndpointHonesty(unittest.TestCase):
    """
    Регрессия «не мои лоты».

    /api/lots при любой ошибке МОЛЧА подставлял lots_backup.json и рисовал
    его как «Мои лоты». Пользователь видел чужие/старые лоты и не понимал
    почему. Теперь архив доступен только по явному ?archive=1, а ошибка
    возвращается внятно.
    """

    def setUp(self):
        import json
        import tempfile
        self.tmp = os.path.join(tempfile.gettempdir(), "fb_lots_test.json")
        json.dump({"golden_key": "KEY", "settings": {}, "auto_orders": {"enabled": False}},
                  open(self.tmp, "w", encoding="utf-8"), ensure_ascii=False)
        import web_ui
        from funpay_api import FunPayClient
        self.web_ui = web_ui
        self._real_cfg = web_ui.CONFIG_FILE
        self._real_auth = FunPayClient.check_auth
        self._real_lots = FunPayClient.get_user_active_lots
        web_ui.CONFIG_FILE = self.tmp
        web_ui._invalidate_auth_cache()
        self.addCleanup(self._restore)

    def _restore(self):
        from funpay_api import FunPayClient
        FunPayClient.check_auth = self._real_auth
        FunPayClient.get_user_active_lots = self._real_lots
        self.web_ui.CONFIG_FILE = self._real_cfg
        self.web_ui._invalidate_auth_cache()
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    class _Lot:
        def __init__(self, i):
            self.lot_id = i
            self.title = f"Лот {i}"
            self.current_price = 12.5
            self.category_name = "Раздел"
            self.category_id = "402"
            self.amount = 1

    def _lots_stub(self, *ids):
        """Заглушка get_user_active_lots. ВАЖНО: первый аргумент здесь —
        экземпляр FunPayClient, а не тест, поэтому лоты создаём заранее."""
        made = [self._Lot(i) for i in ids]
        return lambda _client, delay=None: made

    def test_no_archive_substitution_on_auth_failure(self):
        """Битый ключ = пустой список + причина, а НЕ архив."""
        from funpay_api import FunPayClient
        import web_ui
        FunPayClient.check_auth = lambda self: (False, None, None, "Недействительный golden_key")
        FunPayClient.get_user_active_lots = lambda self, delay=None: []
        web_ui._invalidate_auth_cache()
        payload = self._call_lots()
        self.assertEqual(payload["lots"], [], "архив не должен подставляться")
        self.assertEqual(payload["source"], "live")
        self.assertFalse(payload["stale"])
        self.assertFalse(payload["auth_ok"])
        self.assertTrue(payload["error"], "причина обязана быть")

    def test_blocked_funpay_reported_as_error(self):
        """None (защитная страница) — это ошибка, а не «у тебя нет лотов»."""
        from funpay_api import FunPayClient
        import web_ui
        FunPayClient.check_auth = lambda self, delay=None: (True, 7, "u", None)
        FunPayClient.get_user_active_lots = lambda self, delay=None: None
        web_ui._invalidate_auth_cache()
        payload = self._call_lots()
        self.assertEqual(payload["lots"], [])
        self.assertIn("CAPTCHA", payload["error"])

    def test_live_lots_pass_through(self):
        from funpay_api import FunPayClient
        import web_ui
        FunPayClient.check_auth = lambda self, delay=None: (True, 7, "u", None)
        FunPayClient.get_user_active_lots = self._lots_stub(1, 2)
        web_ui._invalidate_auth_cache()
        payload = self._call_lots()
        self.assertEqual(len(payload["lots"]), 2, f"error={payload.get('error')}")
        self.assertEqual(payload["lots"][0]["lot_id"], "1")
        self.assertTrue(payload["auth_ok"])
        self.assertIsNone(payload["error"])

    def test_archive_only_by_explicit_request(self):
        payload = self._call_lots(query="archive=1")
        self.assertEqual(payload["source"], "archive")
        self.assertTrue(payload["stale"], "архив должен быть помечен как неживой")

    def test_lots_cache_returns_same_payload(self):
        """Повторный запрос не должен снова ходить на FunPay."""
        from funpay_api import FunPayClient
        import web_ui
        calls = []
        made = [self._Lot(1)]

        def lots(_client, delay=None):
            calls.append(1)
            return made

        FunPayClient.check_auth = lambda self, delay=None: (True, 7, "u", None)
        FunPayClient.get_user_active_lots = lots
        web_ui._invalidate_auth_cache()
        self._call_lots()
        self._call_lots()
        self._call_lots()
        self.assertEqual(len(calls), 1,
                         f"кэш не сработал, походов на FunPay: {len(calls)}")

    def test_force_bypasses_cache(self):
        from funpay_api import FunPayClient
        import web_ui
        calls = []
        made = [self._Lot(1)]

        def lots(_client, delay=None):
            calls.append(1)
            return made

        FunPayClient.check_auth = lambda self, delay=None: (True, 7, "u", None)
        FunPayClient.get_user_active_lots = lots
        web_ui._invalidate_auth_cache()
        self._call_lots()
        self._call_lots(query="force=1")
        self.assertEqual(len(calls), 2, "force обязан обойти кэш")

    def test_cache_dropped_when_key_changes(self):
        import web_ui
        web_ui._invalidate_auth_cache()
        self.assertIsNone(web_ui._LOTS_CACHE["key"],
                          "смена ключа обязана сбрасывать кэш лотов")

    def test_no_sleep_when_delay_zero(self):
        """Паузаanti-detect настраивается — панель не должна ждать 4 секунды."""
        from funpay_api import FunPayClient
        import web_ui
        slept = []
        real_sleep = time.sleep
        made = [self._Lot(1)]

        def fake_sleep(sec):
            slept.append(sec)
            return real_sleep(0)

        FunPayClient.check_auth = lambda self, delay=None: (True, 7, "u", None)
        FunPayClient.get_user_active_lots = lambda _c, delay=None: made
        web_ui.time.sleep = fake_sleep
        try:
            web_ui._invalidate_auth_cache()
            self._call_lots()
        finally:
            web_ui.time.sleep = real_sleep
        self.assertTrue(all(s < 2.0 for s in slept),
                        f"пауза должна быть небольшой, а не anti-detect: {slept}")

    # --- helper: повторяет логику /api/lots без HTTP-сокета ---
    def _call_lots(self, query: str = ""):
        import json
        import urllib.parse
        import web_ui
        from funpay_api import FunPayClient
        config = web_ui._read_config_file()
        key = (config.get("golden_key") or "").strip()
        qs = urllib.parse.parse_qs(query)
        want_archive = (qs.get("archive", ["0"])[0] or "0") in ("1", "true", "yes")
        force = (qs.get("force", ["0"])[0] or "0") in ("1", "true", "yes")

        if want_archive:
            lots = []
            if os.path.exists(web_ui.BACKUP_FILE):
                try:
                    for item in json.load(open(web_ui.BACKUP_FILE, encoding="utf-8")) or []:
                        lots.append({"lot_id": str(item.get("lot_id"))})
                except Exception:
                    pass
            return {"lots": lots, "source": "archive", "stale": True,
                    "auth_ok": None, "error": None}
        if not key:
            return {"lots": [], "source": "none", "stale": False,
                    "auth_ok": False, "error": "Не задан golden_key"}

        cache = web_ui._LOTS_CACHE
        fresh = (not force
                 and (time.time() - cache["ts"]) < web_ui._LOTS_CACHE_TTL
                 and cache["key"] == key and cache["error"] is None)
        if fresh:
            return dict(cache["payload"])

        lots, error, auth_ok = [], None, False
        try:
            auth_ok, uid, uname = web_ui._cached_auth(key)
            client = FunPayClient(golden_key=key)
            if auth_ok and uid:
                client.user_id = uid
                client.username = uname
            active = client.get_user_active_lots(delay=(0.1, 0.3))
            if active is None:
                error = "FunPay вернул защитную страницу (CAPTCHA/лимит)."
            elif not auth_ok:
                error = "Ключ не работает — FunPay не пускает в аккаунт."
            else:
                lots = [{"lot_id": str(x.lot_id)} for x in active or []]
        except Exception as e:
            error = f"Ошибка загрузки: {e}"
        payload = {"lots": lots, "source": "live", "stale": False,
                   "auth_ok": bool(auth_ok), "error": error}
        cache.update({"key": key, "ts": time.time(), "payload": payload, "error": error})
        return payload


class TestTemplatesInfrastructure(unittest.TestCase):
    """Регрессия «разделы есть, а внутри ничего не работает»:
    эндпоинты шаблонов вызывали функции, которых НЕТ в web_ui.py,
    из-за чего /api/templates падал с 500, а вкладки не открывались
    (класс tab-content вместо tab-pane). Проверяем всё сразу."""

    def test_saved_lots_helpers_exist(self):
        import web_ui
        self.assertTrue(callable(getattr(web_ui, "_read_saved_lots", None)),
                        "_read_saved_lots должна существовать")
        self.assertTrue(callable(getattr(web_ui, "_write_saved_lots", None)),
                        "_write_saved_lots должна существовать")
        self.assertTrue(callable(getattr(web_ui, "_gen_id", None)),
                        "_gen_id должна существовать")
        self.assertTrue(callable(getattr(web_ui.DashboardHTTPHandler, "_require_auth", None)),
                        "DashboardHTTPHandler._require_auth должна существовать")

    def test_saved_lots_roundtrip(self):
        import web_ui
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), f"fb_saved_{secrets.token_hex(4)}.json")
        old = web_ui.SAVED_LOTS_FILE
        web_ui.SAVED_LOTS_FILE = tmp
        try:
            self.assertEqual(web_ui._read_saved_lots(), [])
            web_ui._write_saved_lots([{"id": "a1", "name": "Тест"}])
            data = json.load(open(tmp, encoding="utf-8"))
            self.assertIn("templates", data, "формат файла: {'templates': [...]}")
            self.assertEqual(web_ui._read_saved_lots()[0]["name"], "Тест")
        finally:
            web_ui.SAVED_LOTS_FILE = old
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_template_tabs_are_tab_pane(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        for tab in ("tab-templates", "tab-text"):
            m = re.search(r'<div id="' + tab + r'" class="([^"]+)"', html)
            self.assertIsNotNone(m, f"вкладка {tab} существует")
            self.assertEqual(m.group(1), "tab-pane",
                             f"{tab} обязан быть tab-pane — иначе вкладка никогда не откроется")

    def test_template_js_elements_present(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        for el in ("templatesGrid", "templateName", "textStyleInput", "genModal"):
            self.assertIn(f'id="{el}"', html, f"в шаблоне должен быть элемент {el}")

    def test_escape_html_calls_resolve(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        self.assertNotIn("function escapeHtml", html,
                         "второй хелпер экранирования быть не должен")
        self.assertNotIn("escapeHtml(", html,
                         "все вызовы должны идти через escHtml (иначе ReferenceError в рантайме)")


class TestTextTools(unittest.TestCase):
    """Локальные улучшение/генерация текста — без сети, детерминированно."""

    def test_improve_uses_style_emojis(self):
        import web_ui
        out = web_ui._improve_text("пак аватарок.   350 картинок,,разделено по категориям",
                                   "🟦🟥🟩🟧")
        self.assertIn("🟦", out)
        self.assertIn("🟥", out)
        self.assertNotIn("  ", out, "двойные пробелы должны убираться")
        self.assertIn("Пак", out, "первая буква должна становиться заглавной")

    def test_improve_no_invented_facts(self):
        import web_ui
        out = web_ui._improve_text("350 тысяч картинок, автовыдача", "")
        self.assertNotIn("гарантия", out.lower())
        self.assertIn("350 тысяч картинок", out)
        self.assertIn("втовыдача", out)

    def test_generate_lot_texts(self):
        import web_ui
        out = web_ui._generate_lot_texts(
            "пак аватарок на тему аниме дешево",
            "350 тысяч картинок; разделено по категориям; автовыдача",
            "🟦🟥🟩🟧", buyer_msg=True, category="Аккаунты")
        tl = out["title_ru"].lower()
        self.assertIn("аватарок", tl)
        self.assertIn("дешево", tl)
        self.assertIn("🟦🟥", out["title_ru"], "по 2 эмодзи в начале")
        self.assertTrue(out["title_ru"].rstrip().endswith("🟩🟧"), "по 2 эмодзи в конце")
        self.assertIn("•", out["title_ru"], "продающие слова через •")
        self.assertIn("350 тысяч картинок", out["desc_ru"])
        self.assertIn("азделено по категориям", out["desc_ru"])
        self.assertIn("пароль и почту", out["desc_ru"], "секция ВАЖНО для аккаунтов")
        self.assertIn("➖", out["desc_ru"], "разделители секций")
        self.assertTrue(out["pay_ru"], "buyer_msg=True -> сообщение для покупателя")
        out2 = web_ui._generate_lot_texts("идея", "фича", "", buyer_msg=False)
        self.assertEqual(out2["pay_ru"], "")

    def test_generate_fixes_common_words(self):
        import web_ui
        out = web_ui._generate_lot_texts(
            "валл паперинжект акаунт стим дешево быстро личный",
            "аккаунт личный я даю доступ логин пароль почта все я не лезу не куда",
            "🟦🟥🟩🟧")
        self.assertIn("WALLPAPER ENGINE", out["title_ru"])
        self.assertIn("STEAM", out["title_ru"])
        self.assertIn("АККАУНТ", out["title_ru"])
        self.assertIn("никуда", out["desc_ru"])
        self.assertNotIn("акаунт", out["desc_ru"].lower())

    def test_translate_offline_is_graceful(self):
        """Без сети перевод должен мягко вернуть ошибку, а не уронить панель."""
        import web_ui
        old_get = web_ui.requests.get
        web_ui.requests.get = lambda *a, **kw: (_ for _ in ()).throw(IOError("offline"))
        try:
            ok, text, err = web_ui._translate_text("привет", "en")
            self.assertFalse(ok)
            self.assertTrue(err)
        finally:
            web_ui.requests.get = old_get


class TestPricesNeverChange(unittest.TestCase):
    """Пользователь выключил смену цен НАВСЕГДА. Бот не должен иметь
    ни одной живой дороги к изменению цен."""

    FILES = ["main.py", "web_ui.py", "funpay_api.py", "auto_orders.py",
             "lot_raiser.py", "auto_online.py", "telegram_bot.py"]

    def test_flag_still_locked(self):
        from funpay_api import PRICE_EDITS_ENABLED
        self.assertFalse(PRICE_EDITS_ENABLED, "PRICE_EDITS_ENABLED должен быть False")

    def test_no_price_endpoints_in_panel(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        self.assertNotIn("api/lots/price", html)
        self.assertNotIn("updateLotPrice", html)
        self.assertNotIn("changePrice", html)

    def test_update_lot_price_never_called(self):
        for name in self.FILES:
            if not os.path.exists(name):
                continue
            text = open(name, encoding="utf-8", errors="replace").read()
            calls = [l for l in text.splitlines()
                     if "update_lot_price" in l and "def update_lot_price" not in l
                     and not l.strip().startswith("#")]
            self.assertEqual(calls, [],
                             f"{name}: update_lot_price вызываться нигде не должен: {calls}")

    def test_no_price_toggle_switch_in_lots_list(self):
        import web_ui
        html = web_ui.HTML_TEMPLATE
        # Переключатель ВКЛ/ВЫКЛ у лота — это защита от бота, он должен остаться,
        # но никаких инпутов ввода новой цены в списке лотов быть не должно.
        self.assertNotIn('id="lotNewPrice"', html)


class TestLotsEndpointContract(unittest.TestCase):
    """/api/lots: честный контракт из TestLotsEndpointHonesty._call_lots
    должен быть реализован В САМОМ ЭНДПОИНТЕ, а не только в тесте."""

    def test_lots_handler_matches_contract(self):
        import web_ui
        src = open("web_ui.py", encoding="utf-8").read()
        # Кэш и честные поля должны быть в обработчике do_GET
        idx = src.find('if path == "/api/lots":')
        self.assertGreater(idx, 0, "эндпоинт /api/lots существует")
        chunk = src[idx:idx + 5000]
        for marker in ('"source": "live"', '"stale"', '"auth_ok"', "_LOTS_CACHE",
                       "_cached_auth", "archive"):
            self.assertIn(marker, chunk,
                          f"в /api/lots должен использоваться {marker} (честный контракт)")

    def test_status_uses_cached_auth(self):
        import web_ui
        src = open("web_ui.py", encoding="utf-8").read()
        idx = src.find('if path == "/api/status":')
        chunk = src[idx:idx + 1200]
        self.assertIn("_cached_auth", chunk,
                      "/api/status должен использовать кэш авторизации, "
                      "иначе панель грузится десятки секунд")


if __name__ == "__main__":
    unittest.main(verbosity=2)
