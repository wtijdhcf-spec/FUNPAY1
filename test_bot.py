"""
Тестовый скрипт для валидации логики бота
"""

import unittest
from funpay_api import FunPayClient, CompetitorOffer, UserLot
from matcher import LotMatcher


class TestFunPayBot(unittest.TestCase):

    def setUp(self):
        self.config = {
            "settings": {
                "check_interval_seconds": 120,
                "only_online_sellers": True,
                "min_price_safety_floor": 0.01,
                "undercut_rules": {
                    "step_default": 0.01,
                },
                "match_parameters": {
                    "match_server": True,
                    "match_side": True,
                    "match_subcategory": True,
                    "match_keywords": True,
                },
            },
            "custom_lot_rules": {},
        }
        self.matcher = LotMatcher(self.config)
        self.client = FunPayClient("dummy_golden_key")

    def test_price_parsing(self):
        """Проверка парсинга различных форматов цен FunPay"""
        self.assertEqual(self.client.parse_price("15.50 ₽"), 15.50)
        self.assertEqual(self.client.parse_price("100 ₽"), 100.0)
        self.assertEqual(self.client.parse_price("0.05 ₽"), 0.05)
        self.assertEqual(self.client.parse_price("0,02 ₽"), 0.02)
        self.assertEqual(self.client.parse_price("1 250.00 руб."), 1250.00)
        self.assertEqual(self.client.parse_price("0.005 ₽"), 0.005)

    def test_undercut_step_0_01(self):
        """Проверка шага занижения ровно на 0.01 руб"""
        # Конкурент продает за 100.00 ₽ -> наша цена 99.99 ₽
        self.assertAlmostEqual(self.matcher.calculate_undercut_price(100.0), 99.99, places=2)
        # Конкурент продает за 2.44 ₽ -> наша цена 2.43 ₽
        self.assertAlmostEqual(self.matcher.calculate_undercut_price(2.44), 2.43, places=2)
        # Конкурент продает за 0.08 ₽ -> наша цена 0.07 ₽
        self.assertAlmostEqual(self.matcher.calculate_undercut_price(0.08), 0.07, places=2)
        # Защита от цены < 0.01 ₽ (safety floor)
        self.assertAlmostEqual(self.matcher.calculate_undercut_price(0.01), 0.01, places=2)

    def test_price_lowering(self):
        """Проверка СНИЖЕНИЯ цены, если наш лот дороже конкурента"""
        my_lot = UserLot(
            lot_id="75824555",
            title="Lineage Piece, ⚙️ Одд Том / Odd Tom | Быстро • Дешево • Безопасно ⚙️",
            category_id="402",
            category_name="Прочие игры Roblox",
            category_url="https://funpay.com/lots/402/",
            current_price=3.01,
        )

        comp = CompetitorOffer(
            offer_id="888001",
            seller_name="CompetitorA",
            seller_id="2222",
            is_online=True,
            title="odd tome для спавна фрирен, Lineage Piece, Предметы, Трейд",
            price=2.44,
        )

        should_update, new_price, best_comp, reason = self.matcher.analyze_lot(
            user_lot=my_lot,
            category_offers=[comp],
            my_user_id=11111,
        )

        self.assertTrue(should_update)
        # 2.44 - 0.01 = 2.43 ₽ (снижение с 3.01 ₽)
        self.assertAlmostEqual(new_price, 2.43, places=2)
        self.assertIn("Снижаем", reason)

    def test_price_raising(self):
        """Проверка ПОВЫШЕНИЯ цены, если наш лот самый дешевый на рынке"""
        my_lot = UserLot(
            lot_id="75824555",
            title="Lineage Piece, ⚙️ Одд Том / Odd Tom | Быстро • Дешево • Безопасно ⚙️",
            category_id="402",
            category_name="Прочие игры Roblox",
            category_url="https://funpay.com/lots/402/",
            current_price=1.00,  # У нас стоит всего 1.00 ₽ (слишком дешево)
        )

        comp = CompetitorOffer(
            offer_id="888001",
            seller_name="CompetitorA",
            seller_id="2222",
            is_online=True,
            title="odd tome для спавна фрирен, Lineage Piece, Предметы, Трейд",
            price=2.44,  # Конкурент продает за 2.44 ₽
        )

        should_update, new_price, best_comp, reason = self.matcher.analyze_lot(
            user_lot=my_lot,
            category_offers=[comp],
            my_user_id=11111,
        )

        self.assertTrue(should_update)
        # Бот должен подтянуть нашу цену вверх: 1.00 ₽ -> 2.43 ₽ (2.44 - 0.01)
        self.assertAlmostEqual(new_price, 2.43, places=2)
        self.assertIn("Повышаем", reason)

    def test_optimal_price_no_change(self):
        """Проверка отсутствия изменений, если наша цена уже идеальна (ровно на 0.01 ₽ ниже)"""
        my_lot = UserLot(
            lot_id="75824555",
            title="Lineage Piece, ⚙️ Одд Том / Odd Tom | Быстро • Дешево • Безопасно ⚙️",
            category_id="402",
            category_name="Прочие игры Roblox",
            category_url="https://funpay.com/lots/402/",
            current_price=2.43,  # Ровно на 0.01 ₽ ниже конкурента
        )

        comp = CompetitorOffer(
            offer_id="888001",
            seller_name="CompetitorA",
            seller_id="2222",
            is_online=True,
            title="odd tome для спавна фрирен, Lineage Piece, Предметы, Трейд",
            price=2.44,
        )

        should_update, new_price, best_comp, reason = self.matcher.analyze_lot(
            user_lot=my_lot,
            category_offers=[comp],
            my_user_id=11111,
        )

        self.assertFalse(should_update)
        self.assertIn("оптимальна", reason)

    def test_keep_higher_price_when_competitor_leaves(self):
        """Проверка СОХРАНЕНИЯ более высокой цены (0.34 ₽), если она выше базовой (0.15 ₽) при уходе конкурента"""
        self.matcher.default_seller_prices = {
            "trait_reroll": 0.12,  # Базовая цена ~0.15 ₽
        }

        my_lot = UserLot(
            lot_id="75824555",
            title="Lineage Piece, Трейт реролл / Trait Reroll | Быстро • Дешево",
            category_id="402",
            category_name="Прочие игры Roblox",
            category_url="https://funpay.com/lots/402/",
            current_price=0.34,  # Текущая цена 0.34 ₽ (выше базовой 0.15 ₽)
        )

        # Конкурент ушел (список пуст)
        should_update, new_price, best_comp, reason = self.matcher.analyze_lot(
            user_lot=my_lot,
            category_offers=[],
            my_user_id=11111,
        )

        # Бот НЕ должен сбрасывать цену вниз до 0.15 ₽, а должен сохранить 0.34 ₽!
        self.assertFalse(should_update)
        self.assertIn("сохраняем более выгодную цену", reason)

    def test_restore_dumped_price_when_competitor_leaves(self):
        """Проверка ПОДНЯТИЯ сбитой цены (0.08 ₽) до базовой (0.15 ₽) при уходе конкурента"""
        self.matcher.default_seller_prices = {
            "trait_reroll": 0.12,  # Базовая цена покупателю: 0.12 * 1.223577 = 0.147 ₽
        }

        my_lot = UserLot(
            lot_id="75824555",
            title="Lineage Piece, Трейт реролл / Trait Reroll | Быстро • Дешево",
            category_id="402",
            category_name="Прочие игры Roblox",
            category_url="https://funpay.com/lots/402/",
            current_price=0.08,  # Цена была сбита демпингом до 0.08 ₽
        )

        # Конкурент ушел
        should_update, new_price, best_comp, reason = self.matcher.analyze_lot(
            user_lot=my_lot,
            category_offers=[],
            my_user_id=11111,
        )

        # Бот должен вернуть цену вверх до базовой (0.147 ₽)
        self.assertTrue(should_update)
        self.assertAlmostEqual(new_price, 0.147, places=3)
        self.assertIn("поднимаем цену", reason)

    def test_lot_raiser_tracking(self):
        """Проверка отслеживания состояния поднятых разделов в LotRaiser"""
        import os
        import tempfile

        import lot_raiser
        from lot_raiser import LotRaiser
        from telegram_bot import TelegramNotifier

        # БЫЛО: тест зависел от настоящего raise_state.json и падал,
        # если раздел 402 уже поднимал реальный бот. Теперь изолирован.
        tmp_state = os.path.join(tempfile.gettempdir(), "fb_raise_state_test.json")
        if os.path.exists(tmp_state):
            os.remove(tmp_state)
        original_state_file = lot_raiser.STATE_FILE
        lot_raiser.STATE_FILE = tmp_state
        try:
            raiser = LotRaiser(self.client, TelegramNotifier("", "", enabled=False), enabled=True)
            self.assertFalse(raiser.is_node_raised("402"))
            raiser.mark_node_raised("402")
            self.assertTrue(raiser.is_node_raised("402"))
        finally:
            lot_raiser.STATE_FILE = original_state_file
            if os.path.exists(tmp_state):
                os.remove(tmp_state)


if __name__ == "__main__":
    unittest.main()

