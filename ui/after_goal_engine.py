# ui/after_goal_engine.py
import asyncio
import logging
import time
import json
import os
import re
from typing import Dict, Optional, List, Any, Tuple

from playwright.async_api import Page

from .after_goal import (
    BookmakerHandler,
    FonbetHandler,
    SportbetHandler,
    OlimpHandler,
    BetcityHandler,
    LeonHandler,
    ZenitHandler,
    LigaStavokHandler,
    WinlineHandler,
    MarathonHandler,
)
from core.adspower_browser import AdsPowerBrowser
from .paths import get_app_data_dir
from .after_goal.match_urls import build_match_url
from ui.log_bus import log_bus

ADSPOWER_API_URL = "http://localhost:50325"
AFTER_GOAL_TIMEOUT = 15
AFTER_GOAL_COOLDOWN = 10

logger = logging.getLogger(__name__)

HANDLERS = {
    "fonbet": FonbetHandler,
    "sportbet": SportbetHandler,
    "olimp": OlimpHandler,
    "betcity": BetcityHandler,
    "leon": LeonHandler,
    "zenit": ZenitHandler,
    "ligastavok": LigaStavokHandler,
    "winline": WinlineHandler,
    "marathon": MarathonHandler,
}

# Какие виды спорта можно ставить автоматом для каждой БК.
AUTOBET_ENABLED_SPORTS_BY_BK = {
    "betcity": {
        "table_tennis", "volleyball", "basketball", "cyber_basketball",
    },
    "olimp": {
        "table_tennis", "volleyball", "basketball", "cyber_basketball",
    },
    "sportbet": {
        "table_tennis", "volleyball", "basketball",
    },
    "fonbet": {
        "table_tennis", "volleyball", "basketball", "cyber_basketball",
    },
    "ligastavok": {
        "table_tennis", "volleyball", "basketball", "cyber_basketball",
    },
    "marathon":   {"table_tennis"},
    "winline": {
        "table_tennis", "volleyball", "basketball", "cyber_basketball",
    },
}

PHASE_END_BY_SCORE = {"table_tennis", "volleyball", "beach_volleyball"}
PHASE_END_BY_TIME = {"basketball", "cyber_basketball"}


LIVE_URLS = {
    'fonbet': {
        'table_tennis':     'https://fon.bet/live/table-tennis',
        'volleyball':       'https://fon.bet/live/volleyball',
        'basketball':       'https://fon.bet/live/basketball',
        'cyber_basketball': 'https://fon.bet/live/basketball',
        'any':              'https://fon.bet/live',
    },
    'winline': {
        'table_tennis':     'https://winline.ru/live/sport/nastolijnyj_tennis',
        'volleyball':       'https://winline.ru/live/sport/volleyball',
        'basketball':       'https://winline.ru/live/sport/basketball',
        'cyber_basketball': 'https://winline.ru/live/sport/basketball',
        'any':              'https://winline.ru/live',
    },
    'ligastavok': {
        'table_tennis':     'https://www.ligastavok.ru/live/table-tennis',
        'volleyball':       'https://www.ligastavok.ru/live/volleyball',
        'basketball':       'https://www.ligastavok.ru/live/basketball',
        'cyber_basketball': 'https://www.ligastavok.ru/live/basketball',
        'any':              'https://www.ligastavok.ru/live',
    },
    'leon': {
        'table_tennis':     'https://leon.ru/bets/table-tennis',
        'volleyball':       'https://leon.ru/bets/volleyball',
        'basketball':       'https://leon.ru/bets/basketball',
        'cyber_basketball': 'https://leon.ru/bets/basketball',
        'any':              'https://leon.ru/live',
    },
    'olimp': {
        'table_tennis':     'https://www.olimp.bet/live/nastolnyy-tennis-40',
        'volleyball':       'https://www.olimp.bet/live/voleybol-10',
        'basketball':       'https://www.olimp.bet/live/basketbol-5',
        'cyber_basketball': 'https://www.olimp.bet/live/kiberbasketbol-140',
        'any':              'https://www.olimp.bet/live',
    },
    'betcity': {
        'table_tennis':     'https://betcity.ru/ru/live/table-tennis',
        'volleyball':       'https://betcity.ru/ru/live/volleyball',
        'basketball':       'https://betcity.ru/ru/live/basketball',
        'cyber_basketball': 'https://betcity.ru/ru/live/basketball',
        'any':              'https://betcity.ru/ru/live',
    },
    'marathon': {
        'table_tennis':     'https://new.marathonbet.ru/su/live/table-tennis',
        'volleyball':       'https://new.marathonbet.ru/su/live/volleyball',
        'basketball':       'https://new.marathonbet.ru/su/live/basketball',
        'cyber_basketball': 'https://new.marathonbet.ru/su/live/basketball',
        'any':              'https://new.marathonbet.ru/su/live',
    },
    'zenit': {
        'table_tennis':     'https://zenit.win/live/134',
        'volleyball':       'https://zenit.win/live/41',
        'basketball':       'https://zenit.win/live/28',
        'cyber_basketball': 'https://zenit.win/live/564',
        'any':              'https://zenit.win/live',
    },
    'sportbet': {
        'table_tennis':     'https://sportbet.ru/live/table-tennis?isTime=1',
        'volleyball':       'https://sportbet.ru/live/volleyball?isTime=1',
        'basketball':       'https://sportbet.ru/live/basketball?isTime=1',
        'any':              'https://sportbet.ru/live?isTime=1',
    },
}


class AfterGoalEngine:
    def __init__(self):
        self._browsers: Dict[str, AdsPowerBrowser] = {}
        self._pages: Dict[str, Page] = {}
        self._last_bet_time: Dict[str, float] = {}
        self._monitoring_active: Dict[str, bool] = {}
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_payloads: Dict[str, dict] = {}
        self._monitoring_strategies: Dict[str, dict] = {}
        self._bets_by_match: Dict[str, int] = {}
        # (match_id, set_number) → количество успешных ставок в этой фазе
        self._bets_by_phase: Dict[Tuple[str, int], int] = {}

    # ---------- Публичные методы ----------

    async def activate_strategy(self, strategy: dict):
        profile_id = strategy.get('profile_id')
        if not profile_id:
            logger.warning("Стратегия не имеет profile_id, пропускаем активацию")
            return

        headless = strategy.get('headless', False)
        bk = (strategy.get('bk') or '').lower()
        sport = (strategy.get('sport') or 'any').lower()

        wrapper = await self._get_browser(profile_id, headless)
        if not wrapper:
            logger.error(f"❌ Не удалось активировать профиль {profile_id}")
            return

        if bk:
            bk_urls = LIVE_URLS.get(bk, {})
            start_url = bk_urls.get(sport) or bk_urls.get('any')
            if start_url:
                try:
                    await wrapper.page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                    logger.info(f"✅ Перешли на лайв-раздел {bk} [{sport}]: {start_url}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось перейти на {start_url}: {e}")
                    log_bus.warning("AdsPower", f"Не удалось перейти на лайв-раздел {bk}: {e}")
            else:
                logger.warning(f"⚠️ Неизвестная БК {bk}, пропускаем переход")

        logger.info(f"✅ Профиль {profile_id} активирован (headless={headless})")
        log_bus.info("Стратегия", f"Активирован профиль {profile_id} ({bk or '—'}, {sport})")

    async def preopen_match_with_profile(self, payload: dict, strategy: dict):
        match_id = payload.get('match_id')
        if not match_id:
            logger.warning("Нет match_id в payload")
            return

        profile_id = strategy.get('profile_id')
        if not profile_id:
            logger.warning("Стратегия без profile_id")
            return

        headless = strategy.get('headless', False)

        # ---- Игнор повторных сигналов ----
        if strategy.get('ignore_repeats', False) and not payload.get('is_new', False):
            logger.info(f"⏭ Повторный сигнал {match_id} — игнорируем (ignore_repeats)")
            return

        # ---- Проверка вида спорта для конкретной БК ----
        sport = (payload.get('sport') or 'table_tennis').lower()
        slow_bk_early = (payload.get('slow_bk') or '').lower()
        allowed = AUTOBET_ENABLED_SPORTS_BY_BK.get(slow_bk_early, set())
        if sport not in allowed:
            logger.info(
                f"⏸ Автоставки для '{sport}' в БК '{slow_bk_early}' пока не включены. "
                f"Пропускаем {match_id}"
            )
            log_bus.info(
                "Автоставки",
                f"{payload.get('match_teams', ['', ''])[0]} vs "
                f"{payload.get('match_teams', ['', ''])[1]} — "
                f"'{sport}' в '{slow_bk_early}' без автоставок"
            )
            return

        if match_id in self._last_bet_time:
            if time.time() - self._last_bet_time[match_id] < AFTER_GOAL_COOLDOWN:
                logger.info(f"⏳ Cooldown для матча {match_id}, пропускаем преоткрытие")
                return

        max_bets = strategy.get('max_bets_per_match', 1)
        if self._bets_by_match.get(match_id, 0) >= max_bets:
            logger.info(f"🎯 Достигнут лимит ставок ({max_bets}) для {match_id}")
            return

        if match_id in self._monitoring_active and self._monitoring_active[match_id]:
            self._monitoring_payloads[match_id] = payload
            self._monitoring_strategies[match_id] = strategy
            logger.info(f"🔄 Обновлены параметры мониторинга для матча {match_id}")
            return

        browser_wrapper = await self._get_browser(profile_id, headless)
        if not browser_wrapper:
            logger.error(f"❌ Не удалось получить браузер для профиля {profile_id}")
            return

        page = browser_wrapper.page
        player1, player2 = payload.get('match_teams', ['', ''])
        slow_bk = payload.get('slow_bk') or payload.get('bk') or ''

        handler = HANDLERS.get(slow_bk)
        if not handler:
            logger.error(f"Нет обработчика для БК {slow_bk}")
            return

        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.error(f"prepare_page для {slow_bk} упал: {e}")
                return

        match_url = payload.get('match_url') or build_match_url(slow_bk, payload)
        url_ok = False

        if match_url:
            try:
                await page.goto(match_url, wait_until="domcontentloaded",
                                timeout=AFTER_GOAL_TIMEOUT * 1000)
                logger.info(f"✅ Перешли по URL: {match_url}")
                log_bus.info("Матч", f"Открыт по URL · {player1} vs {player2}")
                url_ok = True
            except Exception as e:
                logger.warning(f"⚠️ URL не сработал ({e}), пробуем клик по лайв-списку")
                log_bus.warning("Матч", f"URL не сработал ({e}), ищем кликом")

        if not url_ok:
            found = await self._click_match_on_page(page, player1, player2)
            if not found:
                logger.error(f"❌ Не удалось открыть матч {player1} vs {player2}")
                log_bus.error("Матч", f"Не удалось открыть {player1} vs {player2}")
                return
            else:
                log_bus.info("Матч", f"Найден кликом · {player1} vs {player2}")

        self._pages[match_id] = page
        self._monitoring_active[match_id] = True
        self._monitoring_payloads[match_id] = payload
        self._monitoring_strategies[match_id] = strategy

        task = asyncio.create_task(self._monitor_loop(match_id, payload, strategy, handler))
        self._monitoring_tasks[match_id] = task

        logger.info(f"✅ Преоткрыт матч {match_id} с профилем {profile_id} (headless={headless})")

    async def stop_monitoring(self, match_id: str):
        await self._cleanup_monitoring(match_id)

    async def shutdown(self):
        """
        Полная остановка движка: отменяет задачи, закрывает страницы
        и все AdsPower-браузеры.
        """
        logger.info("🛑 AfterGoalEngine.shutdown: начало")

        tasks = [t for t in self._monitoring_tasks.values()
                 if t and not t.done()]
        for t in tasks:
            try:
                t.cancel()
            except Exception:
                pass

        if tasks:
            try:
                await asyncio.wait(tasks, timeout=2.0)
            except Exception:
                pass

        self._monitoring_tasks.clear()

        for match_id in list(self._pages.keys()):
            try:
                page = self._pages.pop(match_id, None)
                if page and not page.is_closed():
                    await page.close()
            except Exception as e:
                logger.warning(f"shutdown: ошибка закрытия страницы {match_id}: {e}")

        self._monitoring_active.clear()
        self._monitoring_payloads.clear()
        self._monitoring_strategies.clear()

        for profile_id, wrapper in list(self._browsers.items()):
            try:
                if wrapper:
                    await wrapper.__aexit__(None, None, None)
                    logger.info(f"✅ Закрыт браузер профиля {profile_id}")
            except Exception as e:
                logger.warning(f"shutdown: ошибка закрытия браузера {profile_id}: {e}")
        self._browsers.clear()

        logger.info("✅ AfterGoalEngine.shutdown: завершён")

    async def _cleanup_monitoring(self, match_id: str):
        task = self._monitoring_tasks.pop(match_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if match_id in self._pages:
            try:
                await self._pages[match_id].close()
            except Exception as e:
                logger.warning(f"Ошибка закрытия страницы {match_id}: {e}")
            del self._pages[match_id]

        self._monitoring_active[match_id] = False
        self._monitoring_payloads.pop(match_id, None)
        self._monitoring_strategies.pop(match_id, None)
        logger.info(f"🛑 Мониторинг для матча {match_id} остановлен")

    # ---------- Value / Arbitrage / Corridor ----------

    async def place_value_bet(self, payload: dict, strategies: List[dict]):
        bk = payload.get('bk')
        if not bk:
            logger.warning("Value: нет БК в payload")
            return
        matching = [s for s in strategies
                    if s.get('type', '').lower() == 'value'
                    and s.get('bk', '').lower() == bk.lower()]
        if not matching:
            logger.info(f"Value: нет активной стратегии для БК {bk}")
            return
        strategy = matching[0]
        profile_id = strategy.get('profile_id')
        headless = strategy.get('headless', False)
        if not profile_id:
            logger.warning(f"Value: в стратегии не указан profile_id для БК {bk}")
            return
        match_id = payload.get('match_id')
        if not match_id:
            logger.warning("Value: нет match_id в payload")
            return
        if match_id in self._last_bet_time:
            if time.time() - self._last_bet_time[match_id] < AFTER_GOAL_COOLDOWN:
                logger.info(f"Value: кулдаун для матча {match_id}, пропускаем")
                return

        handler = HANDLERS.get(bk)
        if not handler:
            logger.error(f"Value: нет обработчика для БК {bk}")
            return

        browser_wrapper = await self._get_browser(profile_id, headless)
        if not browser_wrapper:
            logger.error(f"Value: не удалось запустить браузер для профиля {profile_id}")
            return

        match_url = payload.get('match_url') or build_match_url(bk, payload)
        if not match_url:
            logger.error(f"Value: не удалось построить URL для БК {bk}")
            return

        page = browser_wrapper.page

        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.error(f"Value: prepare_page для {bk} упал: {e}")
                return

        player1 = payload.get('player1', '') or (payload.get('match_teams') or ['', ''])[0]
        player2 = payload.get('player2', '') or (payload.get('match_teams') or ['', ''])[1]

        url_ok = False
        try:
            await page.goto(match_url, wait_until="domcontentloaded",
                            timeout=AFTER_GOAL_TIMEOUT * 1000)
            logger.info(f"Value: перешли по URL {match_url}")
            url_ok = True
        except Exception as e:
            logger.warning(f"Value: URL не сработал ({e}), пробуем клик")

        if not url_ok:
            found = await self._click_match_on_page(page, player1, player2)
            if not found:
                logger.error(f"Value: не удалось открыть матч {player1} vs {player2}")
                return

        try:
            if bk == 'fonbet':
                outcome_ids = await self._get_fonbet_outcome_ids(page, match_id)
            else:
                outcome_ids = None
        except Exception as e:
            logger.error(f"Value: ошибка получения идентификаторов: {e}")
            await page.close()
            return

        if not outcome_ids:
            logger.warning(f"Value: не удалось получить идентификаторы для матча {match_id}")
            await page.close()
            return

        outcome = payload.get('outcome')
        if outcome == 'П1':
            outcome_id = outcome_ids.get('p1')
        elif outcome == 'П2':
            outcome_id = outcome_ids.get('p2')
        else:
            logger.warning(f"Value: неизвестный исход {outcome}")
            await page.close()
            return

        if not outcome_id:
            logger.warning(f"Value: не найден идентификатор для исхода {outcome}")
            await page.close()
            return

        amount = strategy.get('bet_size', 100)
        bet_data = {
            "amount": amount,
            "value": payload.get('odd', 0.0),
        }

        if bk == 'fonbet':
            bet_data['event_id'] = match_id
            bet_data['factor_id'] = outcome_id
        else:
            logger.error(f"Value: отправка для БК {bk} не реализована")
            await page.close()
            return

        result = await handler.place_bet(page, bet_data)
        if result.get('success'):
            self._last_bet_time[match_id] = time.time()
            logger.info(f"✅ Value-ставка отправлена! ID: {result.get('bet_id', 'N/A')}")
            log_bus.success(
                "Ставка",
                f"Value · {bk} · {amount}₽ · ID {result.get('bet_id', 'N/A')}"
            )
        else:
            logger.error(f"❌ Ошибка отправки Value-ставки: {result.get('error')}")
            log_bus.error("Ставка", f"Value · {bk} · {result.get('error')}")

        try:
            await page.close()
        except Exception as e:
            logger.warning(f"Ошибка закрытия страницы: {e}")

        try:
            new_page = await browser_wrapper._context.new_page()
            browser_wrapper._page = new_page
        except Exception as e:
            logger.error(f"Не удалось пересоздать страницу: {e}")

    async def place_arbitrage_bet(self, payload: dict, strategies: List[dict]):
        logger.info("Arbitrage: пока не реализовано")
        return {"success": False, "error": "Arbitrage bets not fully implemented yet"}

    async def place_corridor_bet(self, payload: dict, strategies: List[dict]):
        logger.info("Corridor: пока не реализовано")
        return {"success": False, "error": "Corridor bets not fully implemented yet"}

    # ---------- AdsPower ----------

    async def _get_api_key_for_profile(self, profile_id: str) -> Optional[str]:
        data_dir = get_app_data_dir()
        path = os.path.join(data_dir, "accounts.json")
        logger.info(f"🔍 Поиск accounts.json по пути: {path}")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    accounts = json.load(f)
                for acc in accounts:
                    if acc.get("ads_power_id") == profile_id:
                        key = acc.get("api_key")
                        if key:
                            logger.info(f"✅ Найден api_key для профиля {profile_id}")
                            return key
                        else:
                            logger.warning(f"⚠️ Для профиля {profile_id} api_key пуст")
                            return None
                logger.warning(f"⚠️ Профиль {profile_id} не найден в accounts.json")
            else:
                logger.error(f"❌ Файл accounts.json не найден по пути {path}")
        except Exception as e:
            logger.error(f"❌ Ошибка чтения accounts.json: {e}")
        return None

    async def _get_browser(self, profile_id: str, headless: bool = False) -> Optional[AdsPowerBrowser]:
        if profile_id in self._browsers:
            wrapper = self._browsers[profile_id]
            try:
                if wrapper.browser and wrapper.browser.is_connected():
                    return wrapper
            except Exception:
                pass
            del self._browsers[profile_id]

        api_key = await self._get_api_key_for_profile(profile_id)
        if not api_key:
            logger.error(f"❌ Не найден API-ключ для профиля {profile_id}")
            return None

        try:
            wrapper = AdsPowerBrowser(
                profile_id=profile_id,
                api_key=api_key,
                api_url=ADSPOWER_API_URL,
                headless=headless
            )
            await wrapper.__aenter__()
            self._browsers[profile_id] = wrapper
            logger.info(f"✅ Браузер для профиля {profile_id} запущен (headless={headless})")
            log_bus.success("AdsPower", f"Браузер запущен · профиль {profile_id}")
            return wrapper
        except Exception as e:
            logger.error(f"❌ Ошибка запуска AdsPower для профиля {profile_id}: {e}", exc_info=True)
            log_bus.error("AdsPower", f"Не удалось запустить профиль {profile_id}: {e}")
            return None

    # ---------- Поиск матча ----------

    async def _click_match_on_page(self, page: Page, player1: str, player2: str) -> bool:
        if not player1 or not player2:
            return False

        p1_tokens = [t.lower() for t in re.split(r"\W+", player1) if len(t) >= 3]
        p2_tokens = [t.lower() for t in re.split(r"\W+", player2) if len(t) >= 3]
        if not p1_tokens or not p2_tokens:
            return False

        def has_token(text: str, tokens: list) -> bool:
            for t in tokens:
                if re.search(rf"\b{re.escape(t)}\b", text):
                    return True
            return False

        try:
            locator = page.locator("a:visible")
            count = await locator.count()
            for i in range(count):
                el = locator.nth(i)
                try:
                    text = (await el.inner_text()).lower()
                except Exception:
                    continue
                if has_token(text, p1_tokens) and has_token(text, p2_tokens):
                    href = await el.get_attribute("href")
                    if not href:
                        continue
                    if href.startswith("/"):
                        from urllib.parse import urlparse
                        u = urlparse(page.url)
                        href = f"{u.scheme}://{u.netloc}{href}"
                    await page.goto(href, wait_until="domcontentloaded", timeout=15000)
                    logger.info(f"✅ Перешли по ссылке на матч: {href}")
                    return True

            logger.warning(f"⚠️ Матч {player1} vs {player2} не найден на {page.url}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка поиска матча: {e}")
            return False

    async def _get_fonbet_outcome_ids(self, page: Page, match_id: str) -> Optional[Dict]:
        try:
            await page.wait_for_function(
                "window.__INITIAL_STATE__ && window.__INITIAL_STATE__.event",
                timeout=10000
            )
            result = await page.evaluate("""
                () => {
                    const state = window.__INITIAL_STATE__;
                    if (!state || !state.event) return null;
                    const factors = state.event.customFactors || [];
                    const p1 = factors.find(f => f.f === 921);
                    const p2 = factors.find(f => f.f === 923);
                    return { p1: p1 ? p1.v : null, p2: p2 ? p2.v : null, factor1: 921, factor2: 923 };
                }
            """)
            if result and result.p1 is not None and result.p2 is not None:
                return {
                    'p1': result.factor1,
                    'p2': result.factor2,
                    'odd1': result.p1,
                    'odd2': result.p2,
                }
            return None
        except Exception as e:
            logger.error(f"Fonbet ошибка получения идентификаторов: {e}")
            return None

    # ---------- Работа с фазами ----------

    def _phase_end(self, sport: str, s1: int, s2: int, set_number: int) -> Tuple[bool, Optional[str]]:
        if sport == "table_tennis":
            if s1 >= 11 and s1 - s2 >= 2:
                return True, '1'
            if s2 >= 11 and s2 - s1 >= 2:
                return True, '2'
            return False, None

        if sport in ("volleyball", "beach_volleyball"):
            threshold = 15 if set_number >= 5 else 25
            if s1 >= threshold and s1 - s2 >= 2:
                return True, '1'
            if s2 >= threshold and s2 - s1 >= 2:
                return True, '2'
            return False, None

        return False, None

    def _verify_delay_still_open(self, data: dict, payload: dict, strategy: dict) -> bool:
        min_diff = strategy.get('min_score_diff', 2) or 2
        sport = payload.get('sport', 'table_tennis')

        if sport in PHASE_END_BY_SCORE:
            fast_sub = payload.get('fast_sub_score', [0, 0])
            s_sub1 = data.get('sub_score1', 0) or 0
            s_sub2 = data.get('sub_score2', 0) or 0
            return max(fast_sub[0] - s_sub1, fast_sub[1] - s_sub2) >= min_diff

        if sport in PHASE_END_BY_TIME:
            fast_score = payload.get('fast_score', [0, 0])
            slow_s1 = data.get('score1', 0) or 0
            slow_s2 = data.get('score2', 0) or 0
            fast_total = (fast_score[0] or 0) + (fast_score[1] or 0)
            slow_total = slow_s1 + slow_s2
            return (fast_total - slow_total) >= min_diff

        return False

    # ---------- Сборка подтверждённых исходов ----------

    def _collect_confirmed_bets(self, sport: str, payload: dict,
                                markets: dict,
                                set_number: int) -> List[Tuple[str, str, float, str, int]]:
        confirmed: List[Tuple[str, str, float, str, int]] = []
        fast_sub = payload.get('fast_sub_score', [0, 0])

        # --- winner ---
        if sport in PHASE_END_BY_SCORE:
            finished, winner_side = self._phase_end(sport, fast_sub[0], fast_sub[1], set_number)
            if finished and winner_side:
                w = markets.get('winner', {})
                odd = w.get(winner_side, 0)
                if odd > 0:
                    confirmed.append((
                        'winner', winner_side, odd,
                        f'phase done {fast_sub[0]}:{fast_sub[1]}',
                        set_number,
                    ))

        # --- total ---
        total = markets.get('total', {})
        if total:
            line = total.get('line', 0) or 0
            fast_sub_total = (fast_sub[0] or 0) + (fast_sub[1] or 0)

            if fast_sub_total > line:
                odd = total.get('over', 0)
                if odd > 0:
                    confirmed.append((
                        'total', 'over', odd,
                        f'sub total {fast_sub_total} > {line}',
                        set_number,
                    ))

            if sport in PHASE_END_BY_SCORE:
                finished, _ = self._phase_end(sport, fast_sub[0], fast_sub[1], set_number)
                if finished and fast_sub_total < line:
                    odd = total.get('under', 0)
                    if odd > 0:
                        confirmed.append((
                            'total', 'under', odd,
                            f'phase done, total {fast_sub_total} < {line}',
                            set_number,
                        ))

        # --- handicap ---
        h = markets.get('handicap', {})
        fast_margin = (fast_sub[0] or 0) - (fast_sub[1] or 0)

        if sport in PHASE_END_BY_SCORE:
            finished, _ = self._phase_end(sport, fast_sub[0], fast_sub[1], set_number)
            if finished:
                for side, margin in [('1', fast_margin), ('2', -fast_margin)]:
                    h_side = h.get(side, {})
                    if not h_side:
                        continue
                    line = h_side.get('line', 0)
                    odd = h_side.get('odd', 0)
                    if odd > 0 and (margin + line) > 0:
                        confirmed.append((
                            'handicap', side, odd,
                            f'margin {margin} + line {line} > 0',
                            set_number,
                        ))
        elif sport in PHASE_END_BY_TIME:
            for side, margin in [('1', fast_margin), ('2', -fast_margin)]:
                h_side = h.get(side, {})
                if not h_side:
                    continue
                line = h_side.get('line', 0)
                odd = h_side.get('odd', 0)
                if odd > 0 and (margin + line) > 0:
                    confirmed.append((
                        'handicap', side, odd,
                        f'margin {margin} + line {line} > 0',
                        set_number,
                    ))

        return confirmed

    def _collect_prev_phase_bets(self, sport: str, payload: dict,
                                 set_markets: dict) -> List[Tuple[str, str, float, str, int]]:
        prev_phase_name = (payload.get('fast_prev_phase') or '').strip()
        if not prev_phase_name:
            return []

        m = re.search(r'(\d+)', prev_phase_name)
        if not m:
            return []
        prev_num = int(m.group(1))

        prev_sub = payload.get('fast_prev_sub_score', [0, 0]) or [0, 0]
        prev_s1 = prev_sub[0] or 0
        prev_s2 = prev_sub[1] or 0

        if prev_s1 == 0 and prev_s2 == 0:
            return []

        set_key = f"set_{prev_num}"
        markets = set_markets.get(set_key, {})
        if not markets:
            return []

        confirmed: List[Tuple[str, str, float, str, int]] = []

        if prev_s1 > prev_s2:
            winner_side = '1'
        elif prev_s2 > prev_s1:
            winner_side = '2'
        else:
            winner_side = None

        if winner_side:
            w = markets.get('winner', {})
            odd = w.get(winner_side, 0)
            if odd > 0:
                confirmed.append((
                    'winner', winner_side, odd,
                    f'prev {prev_phase_name} {prev_s1}:{prev_s2}',
                    prev_num,
                ))

        total = markets.get('total', {})
        if total:
            line = total.get('line', 0) or 0
            prev_total = prev_s1 + prev_s2
            if prev_total > line:
                odd = total.get('over', 0)
                if odd > 0:
                    confirmed.append((
                        'total', 'over', odd,
                        f'prev total {prev_total} > {line}',
                        prev_num,
                    ))
            if prev_total < line:
                odd = total.get('under', 0)
                if odd > 0:
                    confirmed.append((
                        'total', 'under', odd,
                        f'prev total {prev_total} < {line}',
                        prev_num,
                    ))

        h = markets.get('handicap', {})
        prev_margin = prev_s1 - prev_s2
        for side, margin in [('1', prev_margin), ('2', -prev_margin)]:
            h_side = h.get(side, {})
            if not h_side:
                continue
            line = h_side.get('line', 0)
            odd = h_side.get('odd', 0)
            if odd > 0 and (margin + line) > 0:
                confirmed.append((
                    'handicap', side, odd,
                    f'prev margin {margin} + line {line} > 0',
                    prev_num,
                ))

        return confirmed

    # ---------- Фильтр confirmed по параметрам стратегии ----------

    def _filter_confirmed_by_strategy(
        self,
        confirmed: List[Tuple[str, str, float, str, int]],
        strategy: dict,
        payload: dict,
    ) -> List[Tuple[str, str, float, str, int]]:
        markets_enabled = strategy.get('markets_enabled') or ["winner", "total", "handicap"]
        if isinstance(markets_enabled, str):
            markets_enabled = [markets_enabled]

        bet_direction = strategy.get('bet_direction', 'best_odds')
        winner_sides = strategy.get('winner_sides', 'both')
        total_sides = strategy.get('total_sides', 'both')
        handicap_sides = strategy.get('handicap_sides', 'both')

        fast_sub = payload.get('fast_sub_score', [0, 0]) or [0, 0]
        if fast_sub[0] > fast_sub[1]:
            fast_leader = '1'
            fast_laggard = '2'
        elif fast_sub[1] > fast_sub[0]:
            fast_leader = '2'
            fast_laggard = '1'
        else:
            fast_leader = fast_laggard = None

        result = []
        for c in confirmed:
            market, side, odd, reason, set_number = c

            if market not in markets_enabled:
                continue

            if market == 'winner':
                if winner_sides != 'both' and side != winner_sides:
                    continue
                if bet_direction == 'leader' and side != fast_leader:
                    continue
                if bet_direction == 'laggard' and side != fast_laggard:
                    continue
                if bet_direction == 'same_as_fast' and side != fast_leader:
                    continue
            elif market == 'total':
                if total_sides != 'both' and side != total_sides:
                    continue
            elif market == 'handicap':
                if handicap_sides != 'both' and side != handicap_sides:
                    continue

            result.append(c)
        return result

    # ---------- Выбор ставки ----------

    def _choose_best_bet(self, data: dict, strategy: dict, payload: dict) -> Optional[dict]:
        set_markets = data.get('set_markets', {})
        if not set_markets:
            return None

        sport = payload.get('sport', 'table_tennis')

        if sport in PHASE_END_BY_SCORE:
            slow_score = payload.get('slow_score', [0, 0])
            current_num = (slow_score[0] or 0) + (slow_score[1] or 0) + 1
        else:
            nums = []
            for k in set_markets.keys():
                if isinstance(k, str) and k.startswith('set_'):
                    try:
                        nums.append(int(k.split('_', 1)[1]))
                    except Exception:
                        pass
            current_num = max(nums) if nums else 1

        current_key = f"set_{current_num}"
        current_markets = set_markets.get(current_key, {})

        confirmed: List[Tuple[str, str, float, str, int]] = []
        if current_markets:
            confirmed += self._collect_confirmed_bets(
                sport, payload, current_markets, current_num
            )
        confirmed += self._collect_prev_phase_bets(sport, payload, set_markets)

        if not confirmed:
            return None

        confirmed = self._filter_confirmed_by_strategy(confirmed, strategy, payload)
        if not confirmed:
            return None

        min_odds = strategy.get('min_odds', 1.0)
        max_odds = strategy.get('max_odds', 5.0)

        valid = [c for c in confirmed if min_odds <= c[2] <= max_odds]
        if not valid:
            return None

        best = max(valid, key=lambda x: x[2])
        market, side, odd, reason, set_number = best

        result = {
            'market': market,
            'side': side,
            'odd': odd,
            'set_number': set_number,
            'reason': reason,
        }
        mk = set_markets.get(f"set_{set_number}", {})
        if market == 'total':
            result['line'] = mk.get('total', {}).get('line', 0)
        elif market == 'handicap':
            result['line'] = mk.get('handicap', {}).get(side, {}).get('line', 0)
        return result

    # ---------- Основной цикл ----------

    async def _monitor_loop(self, match_id: str, payload: dict, strategy: dict,
                            handler: BookmakerHandler):
        page = self._pages.get(match_id)
        if not page:
            logger.error(f"Нет страницы для матча {match_id}")
            return

        signal_time = time.time()
        verify_seconds = strategy.get('verify_seconds', 3.0)
        max_bets = strategy.get('max_bets_per_match', 1)
        max_bets_per_phase = strategy.get('max_bets_per_phase', 1)
        event = asyncio.Event()

        async def on_update(data: dict):
            if not self._monitoring_active.get(match_id, False):
                return

            if self._bets_by_match.get(match_id, 0) >= max_bets:
                event.set()
                return

            if time.time() - signal_time < verify_seconds:
                return

            current_payload = self._monitoring_payloads.get(match_id, payload)
            current_strategy = self._monitoring_strategies.get(match_id, strategy)

            if not self._verify_delay_still_open(data, current_payload, current_strategy):
                return

            best_bet = self._choose_best_bet(data, current_strategy, current_payload)
            if not best_bet:
                return

            phase_key = (match_id, best_bet['set_number'])
            if self._bets_by_phase.get(phase_key, 0) >= max_bets_per_phase:
                logger.debug(
                    f"⏭ Лимит ставок в фазе {best_bet['set_number']} для {match_id} "
                    f"({max_bets_per_phase}) достигнут"
                )
                return

            set_key = f"set_{best_bet['set_number']}"
            outcome_info = (data.get('outcome_ids', {})
                                .get(set_key, {})
                                .get(best_bet['market'], {})
                                .get(best_bet['side']))
            if not outcome_info:
                logger.warning(f"Нет идентификатора для исхода: {best_bet}")
                return

            amount = current_strategy.get('bet_size', 100)
            bet_data = {
                "amount": amount,
                "value": best_bet['odd'],
            }

            bk = current_payload.get('slow_bk')
            if bk == 'fonbet':
                bet_data['event_id'] = data.get('match_id')
                bet_data['factor_id'] = outcome_info.get('id')
            elif bk == 'sportbet':
                bet_data['outcome_id'] = outcome_info.get('id')
            elif bk == 'betcity':
                bet_data['event_id'] = data.get('match_id')
                bet_data['pos'] = outcome_info.get('pos')
                bet_data['kf'] = best_bet['odd']
                bet_data['lv'] = outcome_info.get('lv')
                token = await page.evaluate("() => document.cookie.match(/tk=([^;]+)/)?.[1] || ''")
                bet_data['token'] = token
            elif bk == 'olimp':
                bet_data['market_data'] = outcome_info.get('market_data')
                bet_data['kf'] = best_bet['odd']
            elif bk == 'ligastavok':
                bet_data['outcomeId'] = outcome_info.get('outcomeId')
                bet_data['factorId'] = outcome_info.get('factorId')
            elif bk == 'marathon':
                bet_data['coefficient_id'] = outcome_info.get('coefficient_id')
                bet_data['event_id'] = int(
                    data.get('event_id')
                    or data.get('match_id')
                    or current_payload.get('match_id')
                )
                bet_data['selection_id'] = outcome_info.get('selection_id')
                bet_data['odds'] = best_bet['odd']
            elif bk == 'winline':
                # outcome_ids[set_N][market][side]['id'] = idLine (int)
                bet_data['outcome_id'] = outcome_info.get('id')
                bet_data['kf'] = best_bet['odd']
                # amount уже есть    
            else:
                logger.error(f"Отправка для БК {bk} не реализована")
                return

            result = await handler.place_bet(page, bet_data)
            if result.get('success'):
                self._bets_by_match[match_id] = self._bets_by_match.get(match_id, 0) + 1
                self._bets_by_phase[phase_key] = self._bets_by_phase.get(phase_key, 0) + 1
                self._last_bet_time[match_id] = time.time()
                logger.info(
                    f"✅ Ставка #{self._bets_by_match[match_id]} "
                    f"(фаза {best_bet['set_number']}, "
                    f"#{self._bets_by_phase[phase_key]} в фазе) отправлена! "
                    f"ID: {result.get('bet_id', 'N/A')} · {best_bet.get('reason', '')}"
                )
                log_bus.success(
                    "Ставка",
                    f"{bk} · {amount}₽ · {best_bet['market']} {best_bet['side']} "
                    f"@ {best_bet['odd']:.2f} · ID {result.get('bet_id', 'N/A')}"
                )
                if self._bets_by_match[match_id] >= max_bets:
                    event.set()
            else:
                logger.error(f"❌ Ошибка отправки ставки: {result.get('error')}")
                log_bus.error("Ставка", f"{bk} · {result.get('error')}")

        try:
            await handler.setup_listener(
                page, on_update,
                match_id=match_id,
                match_teams=payload.get("match_teams"),
            )
        except TypeError:
            try:
                await handler.setup_listener(page, on_update, match_id=match_id)
            except TypeError:
                await handler.setup_listener(page, on_update)

        try:
            timeout = AFTER_GOAL_TIMEOUT * 4
            await asyncio.wait_for(event.wait(), timeout=timeout)
            logger.info(f"⏹️ Мониторинг матча {match_id} завершён")
        except asyncio.TimeoutError:
            logger.info(f"⏰ Мониторинг матча {match_id} завершён по таймауту")
        except asyncio.CancelledError:
            logger.info(f"Мониторинг матча {match_id} отменён")
            raise
        finally:
            try:
                await handler.stop_listener(page)
            except Exception:
                pass
            await self._cleanup_monitoring(match_id)