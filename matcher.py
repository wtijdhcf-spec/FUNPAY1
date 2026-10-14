"""
Модуль сопоставления лотов и расчета цен (Matcher v4)
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from funpay_api import UserLot, CompetitorOffer

logger = logging.getLogger("matcher")

KNOWN_GAMES = [
    "lineage piece", "sailor piece", "anime defenders", "lumber tycoon",
    "haze seas", "archived", "tower of hell", "car dealership tycoon",
    "blox fruits", "king legacy", "grand piece online", "all star tower defense",
    "anime expeditions", "peroxide", "anime vanguards", "jujutsu infinite",
    "deepwoken", "type soul", "shindo life", "anime card farm", "anime ball",
    "anime clash", "anime dimensions simulator", "anime eternal", "anime paradox",
    "anime rangers", "anime squadron", "arcane lineage", "azure latch",
    "broken blade", "violence district", "saber simulator", "murder duels"
]

ITEM_PATTERNS = [
    {
        "name": "hollow_core",
        "matches_terms": ["ядро пустого", "hollow core"],
        "must_exclude": ["pack", "пак", "x1000", "x3000"],
    },
    {
        "name": "summon_core",
        "matches_terms": ["ядро призыва", "summon core"],
        "must_exclude": ["pack", "пак", "x1000", "x3000"],
    },
    {
        "name": "odd_tom",
        "matches_terms": ["одд том", "odd tom", "odd tome"],
        "must_exclude": ["pack", "пак"],
    },
    {
        "name": "armor_scrap",
        "matches_terms": ["армор скрап", "armor scrap"],
        "must_exclude": ["pack", "пак"],
    },
    {
        "name": "trait_reroll",
        "matches_terms": [
            "реролл трейта", "трейт реролл", "реролл трейт", "трейты",
            "trait reroll", "trait rerolls", "trait rr",
            "trait / race", "race / trait", "trait/race", "race/trait",
            "реролл расы / трейта", "реролл трейта / расы", "расы / трейта", "трейта / расы"
        ],
        "must_exclude": ["pack", "пак", "x1000", "x3000", "x6000"],
    },
    {
        "name": "race_reroll",
        "matches_terms": [
            "реролл расы", "расовые рерол", "реролл рас",
            "race reroll", "race rerolls", "race rr", "stats rr", "stats rerrol", "stats reroll",
            "trait / race", "race / trait", "trait/race", "race/trait",
            "реролл расы / трейта", "реролл трейта / расы", "расы / трейта", "трейта / расы"
        ],
        "must_exclude": ["pack", "пак", "x1000", "x3000", "x6000", "mastery"],
    },
    {
        "name": "reforger",
        "matches_terms": ["рефордж", "reforger", "модификации", "реролл модификации"],
        "must_exclude": ["pack", "пак", "x1000", "x3000"],
    },
    {
        "name": "ulquiorra_set",
        "matches_terms": ["улькиорр", "ulquiorra", "ulq"],
        "must_exclude": [
            "hollow core", "ядро", "core", "pill", "пилюл", "pack", "пак",
            "spawn", "peroxide", "для улькиорр", "для спавна"
        ],
    },
    {
        "name": "excalibur_morgan",
        "matches_terms": ["экскалибур", "excalibur", "морган", "morgan", "saber alter", "saber alt", "saber", "сейбер", "сабер"],
        "must_exclude": [
            "core", "ядро", "pill", "pack", "пак", "simulator", "симулятор",
            "murder duels", "dust", "пыль", "scroll", "для спавна"
        ],
    },
    {
        "name": "yhwach_set",
        "matches_terms": ["яхве", "yhwach"],
        "must_exclude": [
            "core", "ядро", "pill", "pack", "пак", "dust", "пыль",
            "scroll", "свиток", "для спавна", "для яхве"
        ],
    },
    {
        "name": "solemn_lament",
        "matches_terms": ["solemn lament", "salemn lament", "yi sang", "солемн", "салемн", "solemn", "salemn"],
        "must_exclude": [
            "core", "ядро", "pill", "pack", "пак", "part", "часть", "pistol",
            "verse piece", "butterfly", "бабочк", "soulweaver", "для солемн", "для салемн", "для спавна"
        ],
    },
    {
        "name": "shadow_dagger",
        "matches_terms": ["shadow's dagger", "shadows dagger", "sung jin woo", "сон джин ву", "теневой монарх"],
        "must_exclude": [
            "core", "ядро", "pill", "pack", "пак", "fruit", "фрукт", "scroll", "для спавна"
        ],
    },
    {
        "name": "vasto_lorde",
        "matches_terms": ["васто лорде", "vasto lorde", "ichigoat", "inner hueco mundo"],
        "must_exclude": [
            "pill", "пилюл", "pack", "пак", "badge", "key", "ключ", "значок", "бейдж", "для спавна", "instinct"
        ],
    },
    {
        "name": "any_set",
        "matches_terms": ["любой сет", "any set"],
        "must_exclude": ["excalibur", "yhwach", "ulquiorra", "solemn", "saber"],
    },
    {
        "name": "any_item",
        "matches_terms": ["любой предмет", "any item"],
        "must_exclude": ["excalibur", "yhwach", "ulquiorra", "solemn", "saber"],
    },
    {
        "name": "any_ore",
        "matches_terms": ["любая руда", "any ore"],
        "must_exclude": [],
    },
    {
        "name": "any_service",
        "matches_terms": ["любая услуга", "any service"],
        "must_exclude": [],
    },
    {
        "name": "farm_bosses",
        "matches_terms": ["фарм боссов", "farming bosses"],
        "must_exclude": [],
    },
]

GENERIC_STOP_WORDS = {
    "быстро", "дешево", "безопасно", "fast", "cheap", "safe", "delivery",
    "трейд", "предметы", "услуги", "прочие", "piece", "lineage",
    "скидки", "бонус", "топ", "лучший", "low", "best", "автовыдача", "онлайн"
}


class LotMatcher:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.settings = config.get("settings", {})
        self.undercut_rules = self.settings.get("undercut_rules", {})
        self.match_params = self.settings.get("match_parameters", {})
        self.ignored_categories = set(self.settings.get("ignored_categories", [
            "any_ore", "any_service", "any_item", "any_set", "farm_bosses"
        ]))
        self.default_seller_prices = config.get("default_seller_prices", {})
        self.custom_rules = config.get("custom_lot_rules", {})
        self.safety_floor = float(self.settings.get("min_price_safety_floor", 0.001))
        self.only_online = self.settings.get("only_online_sellers", True)

    def is_prochie_section(self, text: str) -> bool:
        """Определяет, относится ли лот к подразделу [Прочие]"""
        t = text.lower()
        return "[прочие]" in t or "[other]" in t

    def extract_game_name(self, text: str) -> Optional[str]:
        t = text.lower()
        for game in KNOWN_GAMES:
            if game in t:
                return game
        return None

    def get_item_identities(self, text: str) -> Set[str]:
        """
        Возвращает множество всех категорий предметов, найденных в тексте лота
        """
        t = text.lower()
        matched = set()
        for pattern in ITEM_PATTERNS:
            has_term = any(term in t for term in pattern["matches_terms"])
            if has_term:
                has_exclude = any(exc in t for exc in pattern["must_exclude"])
                if not has_exclude:
                    matched.add(pattern["name"])
        return matched

    def extract_keywords(self, text: str) -> Set[str]:
        clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
        words = clean.split()
        keywords = {w for w in words if len(w) >= 3 and w not in GENERIC_STOP_WORDS}
        return keywords

    def matches_parameters(
        self,
        user_lot: UserLot,
        competitor: CompetitorOffer,
        custom_rule: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Проверяет совпадение подраздела, игры, сервера, стороны и типа предмета.
        """
        # 1. Разделение подразделов: [Lineage Piece] vs [Прочие]
        user_is_prochie = self.is_prochie_section(user_lot.title)
        comp_is_prochie = self.is_prochie_section(competitor.title)
        if user_is_prochie != comp_is_prochie:
            return False

        # 2. Проверка на Mastery / Прокачку (сет без мастери не должен сравниваться с сет+мастери)
        user_has_mastery = "mastery" in user_lot.title.lower() or "мастери" in user_lot.title.lower()
        comp_has_mastery = "mastery" in competitor.title.lower() or "мастери" in competitor.title.lower()
        if user_has_mastery != comp_has_mastery:
            return False

        # 3. Проверка игры
        user_game = self.extract_game_name(user_lot.title)
        comp_game = self.extract_game_name(competitor.title)
        if user_game and comp_game and user_game != comp_game:
            return False
        if user_game and not comp_game and user_lot.category_id == "402":
            return False

        # 4. Проверка сервера
        if self.match_params.get("match_server", True):
            if user_lot.server and competitor.server:
                if user_lot.server.strip().lower() != competitor.server.strip().lower():
                    return False

        # 5. Проверка стороны
        if self.match_params.get("match_side", True):
            if user_lot.side and competitor.side:
                if user_lot.side.strip().lower() != competitor.side.strip().lower():
                    return False

        # 6. Кастомные правила
        if custom_rule:
            explicit_keywords = custom_rule.get("keywords", [])
            if explicit_keywords:
                comp_text = f"{competitor.title} {competitor.server or ''}".lower()
                for kw in explicit_keywords:
                    if kw.lower() not in comp_text:
                        return False
                return True

        # 7. Проверка по сущностям предметов (с поддержкой комбо-лотов)
        user_identities = self.get_item_identities(user_lot.title)
        if user_identities:
            comp_identities = self.get_item_identities(competitor.title)
            if user_identities.intersection(comp_identities):
                return True
            return False

        # 7. Fallback по ключевым словам
        if self.match_params.get("match_keywords", True):
            user_keywords = self.extract_keywords(user_lot.title)
            comp_keywords = self.extract_keywords(competitor.title)

            if user_game:
                user_keywords -= set(user_game.split())
                comp_keywords -= set(user_game.split())

            if user_keywords and comp_keywords:
                common = user_keywords.intersection(comp_keywords)
                if not common:
                    return False
                if len(user_keywords) <= 2 and len(common) < 1:
                    return False
                elif len(user_keywords) > 2 and len(common) < 2:
                    return False
            else:
                return False

        return True

    def calculate_undercut_price(
        self,
        min_competitor_price: float,
        custom_rule: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Вычисляет целевую цену покупателю (максимум 3 знака после точки, минимум 0.001 ₽):
        - Если цена >= 1.0 ₽ -> шаг 0.01 ₽ (2 знака)
        - Если цена < 1.0 ₽ -> шаг 0.001 ₽ (до 3 знаков)
        """
        if custom_rule and custom_rule.get("custom_step"):
            step = float(custom_rule["custom_step"])
        else:
            if min_competitor_price >= 1.0:
                step = 0.01
            else:
                price_str = f"{min_competitor_price:.3f}".rstrip("0")
                decimals = len(price_str.split(".")[1]) if "." in price_str else 0
                if decimals >= 3:
                    step = 0.001
                else:
                    step = 0.01

        new_price = min_competitor_price - step

        # Минимальный порог безопасности (не ниже 0.001 ₽)
        min_allowed = max(self.safety_floor, 0.001)
        if custom_rule and custom_rule.get("min_price"):
            min_allowed = max(min_allowed, float(custom_rule["min_price"]))

        if new_price < min_allowed:
            new_price = min_allowed

        if new_price >= 1.0:
            return round(new_price, 2)
        else:
            return round(new_price, 3)

    def analyze_lot(
        self,
        user_lot: UserLot,
        category_offers: List[CompetitorOffer],
        my_user_id: Optional[int]
    ) -> Tuple[bool, Optional[float], Optional[CompetitorOffer], str]:
        """
        Анализирует раздел и вычисляет целевую цену:
        - Ищет конкурентов строго внутри своего подраздела ([Lineage Piece] или [Прочие])
        - Если конкурентов нет -> выставляет базовую цену по умолчанию
        - Если есть -> занижает/повышает до P_comp - шаг
        """
        custom_rule = self.custom_rules.get(user_lot.lot_id)
        if custom_rule and custom_rule.get("enabled") is False:
            return False, None, None, "Лот отключен в кастомных правилах"

        # Игнорируем фиксированные заглушки (руда, услуги, любой предмет/сет, фарм боссов)
        user_identities = self.get_item_identities(user_lot.title)
        ignored_matches = user_identities.intersection(self.ignored_categories)
        if ignored_matches:
            return False, None, None, f"Лот-заглушка ({list(ignored_matches)[0]}), зафиксирован на 0.001 ₽."

        valid_competitors: List[CompetitorOffer] = []

        for offer in category_offers:
            # Исключаем свои предложения
            if str(offer.offer_id) == str(user_lot.lot_id):
                continue
            if my_user_id and str(offer.seller_id) == str(my_user_id):
                continue

            # Фильтр онлайн продавцов
            if self.only_online and not offer.is_online:
                continue

            # Исключаем нулевые цены (< 0.0005 руб)
            if offer.price < 0.0005:
                continue

            # Проверяем совпадение (включая подраздел [Прочие] vs [Lineage Piece])
            if self.matches_parameters(user_lot, offer, custom_rule):
                valid_competitors.append(offer)

        if not valid_competitors:
            # Если конкурентов в этом подразделе нет, выставляем цену по умолчанию
            user_identities = self.get_item_identities(user_lot.title)
            default_seller_price = None
            matched_key = None
            for item_name in user_identities:
                if item_name in self.default_seller_prices:
                    default_seller_price = float(self.default_seller_prices[item_name])
                    matched_key = item_name
                    break

            if default_seller_price is not None:
                if default_seller_price >= 1.0:
                    target_buyer_price = round(default_seller_price * 1.223577, 2)
                else:
                    target_buyer_price = round(default_seller_price * 1.223577, 3)

                # Если текущая цена ВЫШЕ базовой (например, 0.34 ₽ > 0.15 ₽) -> сохраняем выгодную цену, не занижаем
                if user_lot.current_price > target_buyer_price:
                    return False, None, None, (
                        f"Конкурентов в подразделе нет. Текущая цена ({user_lot.current_price:.3f} ₽) выше базовой ({target_buyer_price:.3f} ₽), сохраняем более выгодную цену."
                    )

                # Если текущая цена МЕНЬШЕ базовой (была сбита демпингом) -> возвращаем цену вверх к базовой
                diff = target_buyer_price - user_lot.current_price
                if diff >= 0.003:
                    return True, target_buyer_price, None, (
                        f"Конкурентов в подразделе нет. Текущая цена ({user_lot.current_price:.3f} ₽) была ниже базовой, поднимаем цену для {matched_key}: "
                        f"{user_lot.current_price:.3f} ₽ -> {target_buyer_price:.3f} ₽ (чистыми: {default_seller_price} ₽)"
                    )

                return False, None, None, f"Конкурентов нет, цена оптимальна ({user_lot.current_price:.3f} ₽)"

            return False, None, None, "Конкурентов с совпадающими параметрами не найдено"

        # Находим минимальную цену конкурента внутри своего подраздела
        cheapest_competitor = min(valid_competitors, key=lambda x: x.price)
        min_comp_price = cheapest_competitor.price

        # Рассчитываем целевую цену
        target_price = self.calculate_undercut_price(min_comp_price, custom_rule)
        diff = target_price - user_lot.current_price

        # Если целевая цена отличается от текущей
        if abs(diff) >= 0.0005:
            sec_name = "[Прочие]" if self.is_prochie_section(user_lot.title) else "[Lineage Piece]"
            if target_price < user_lot.current_price:
                return True, target_price, cheapest_competitor, (
                    f"Конкурент {cheapest_competitor.seller_name} в {sec_name} продает за {min_comp_price:.3f} ₽. "
                    f"Снижаем цену: {user_lot.current_price:.3f} ₽ -> {target_price:.3f} ₽ (-{abs(diff):.3f} ₽)"
                )
            else:
                return True, target_price, cheapest_competitor, (
                    f"Наш лот самый дешевый в {sec_name}! Ближайший конкурент {cheapest_competitor.seller_name} продает за {min_comp_price:.3f} ₽. "
                    f"Повышаем цену: {user_lot.current_price:.3f} ₽ -> {target_price:.3f} ₽ (+{diff:.3f} ₽)"
                )

        return False, None, cheapest_competitor, (
            f"Цена оптимальна ({user_lot.current_price:.3f} ₽, ниже конкурента {cheapest_competitor.seller_name}: {min_comp_price:.3f} ₽)"
        )
