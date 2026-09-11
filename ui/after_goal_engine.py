# ui/after_goal_engine.py
import asyncio
import logging
import time
import json
import os
import re
from typing import Dict, Optional, List, Any

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


class AfterGoalEngine:
    def __init__(self):
        self._browsers: Dict[str, AdsPowerBrowser] = {}
        self._pages: Dict[str, Page] = {}
        self._last_bet_time: Dict[str, float] = {}
        self._monitoring_active: Dict[str, bool] = {}
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_payloads: Dict[str, dict] = {}

    # ---------- Публичные методы ----------

    async def activate_strategy(self, strategy: dict):
        profile_id = strategy.get('profile_id')
        if not profile_id:
            logger.warning("Стратегия не имеет profile_id, пропускаем активацию")
            return

        headless = strategy.get('headless', False)
        bk = strategy.get('bk', '').lower()

        wrapper = await self._get_browser(profile_id, headless)
        if not wrapper:
            logger.error(f"❌ Не удалось активировать профиль {profile_id}")
            return

        if bk:
            LIVE_URLS = {
                'fonbet': 'https://fon.bet/live/table-tennis',
                'winline': 'https://winline.ru/live/sport/nastolijnyj_tennis',
                'ligastavok': 'https://www.ligastavok.ru/live/table-tennis',
                'leon': 'https://leon.ru/bets/table-tennis',
                'olimp': 'https://www.olimp.bet/live/nastolnyy-tennis-40',
                'betcity': 'https://betcity.ru/ru/live/table-tennis',
                'marathon': 'https://new.marathonbet.ru/su/sport/live/382549',
                'zenit': 'https://zenit.win/live/134',
                'sportbet': 'https://sportbet.ru/live/table-tennis?isTime=1',
            }
            start_url = LIVE_URLS.get(bk)
            if start_url:
                try:
                    await wrapper.page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                    logger.info(f"✅ Перешли на лайв-раздел {bk}: {start_url}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось перейти на {start_url}: {e}")
                    log_bus.warning("AdsPower", f"Не удалось перейти на лайв-раздел {bk}: {e}")
            else:
                logger.warning(f"⚠️ Неизвестная БК {bk}, пропускаем переход")

        logger.info(f"✅ Профиль {profile_id} активирован (headless={headless})")
        log_bus.info("Стратегия", f"Активирован профиль {profile_id} ({bk or '—'})")

    async def preopen_match_with_profile(self, payload: dict, profile_id: str, headless: bool = False):
        match_id = payload.get('match_id')
        if not match_id:
            logger.warning("Нет match_id в payload")
            return

        if match_id in self._last_bet_time:
            if time.time() - self._last_bet_time[match_id] < AFTER_GOAL_COOLDOWN:
                logger.info(f"⏳ Cooldown для матча {match_id}, пропускаем преоткрытие")
                return

        if match_id in self._monitoring_active and self._monitoring_active[match_id]:
            await self._update_monitoring_params(match_id, payload)
            return

        browser_wrapper = await self._get_browser(profile_id, headless)
        if not browser_wrapper:
            logger.error(f"❌ Не удалось получить браузер для профиля {profile_id}")
            return

        page = browser_wrapper.page
        player1, player2 = payload.get('match_teams', ['', ''])
        slow_bk = payload.get('slow_bk') or payload.get('bk') or ''

        # ---- 1) Handler и prepare_page ДО goto ----
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

        # ---- 2) Переход на матч ----
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
                logger.error(f"❌ Не удалось открыть матч {player1} vs {player2} ни по URL, ни кликом")
                log_bus.error("Матч", f"Не удалось открыть {player1} vs {player2}")
                return
            else:
                log_bus.info("Матч", f"Найден кликом · {player1} vs {player2}")

        # ---- 3) Регистрация мониторинга ----
        self._pages[match_id] = page
        self._monitoring_active[match_id] = True
        self._monitoring_payloads[match_id] = payload

        task = asyncio.create_task(self._monitor_loop(match_id, payload, handler))
        self._monitoring_tasks[match_id] = task

        logger.info(f"✅ Преоткрыт матч {match_id} с профилем {profile_id} (headless={headless})")

    async def stop_monitoring(self, match_id: str):
        if match_id in self._monitoring_tasks:
            self._monitoring_tasks[match_id].cancel()
            try:
                await self._monitoring_tasks[match_id]
            except asyncio.CancelledError:
                pass
            del self._monitoring_tasks[match_id]

        if match_id in self._pages:
            try:
                await self._pages[match_id].close()
            except Exception as e:
                logger.warning(f"Ошибка закрытия страницы {match_id}: {e}")
            del self._pages[match_id]

        self._monitoring_active[match_id] = False
        self._monitoring_payloads.pop(match_id, None)
        logger.info(f"🛑 Мониторинг для матча {match_id} остановлен")

    # ---------- Ставки ----------

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

        # prepare_page тоже нужен в Value-потоке (для Marathon SSE)
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
            logger.warning(f"Value: не удалось получить идентификаторы для матча {match_id} в БК {bk}")
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

        # ---- ИСПРАВЛЕНО: пересоздаём страницу, чтобы wrapper не держал мёртвый объект ----
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

    # ---------- Работа со страницами ----------

    async def _click_match_on_page(self, page: Page, player1: str, player2: str) -> bool:
        """Поиск матча в лайв-списке по токенам имён (с учётом границ слов)."""
        if not player1 or not player2:
            return False

        p1_tokens = [t.lower() for t in re.split(r"\W+", player1) if len(t) >= 3]
        p2_tokens = [t.lower() for t in re.split(r"\W+", player2) if len(t) >= 3]
        if not p1_tokens or not p2_tokens:
            return False

        # ---- ИСПРАВЛЕНО: сравнение по границам слов ----
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

    async def _update_monitoring_params(self, match_id: str, payload: dict):
        if match_id in self._monitoring_payloads:
            self._monitoring_payloads[match_id] = payload
            logger.info(f"🔄 Обновлены параметры мониторинга для матча {match_id}")

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

    # ---------- Мониторинг ----------

    async def _monitor_loop(self, match_id: str, payload: dict, handler: BookmakerHandler):
        page = self._pages.get(match_id)
        if not page:
            logger.error(f"Нет страницы для матча {match_id}")
            return

        event = asyncio.Event()
        bet_sent = False

        async def on_update(data: dict):
            nonlocal bet_sent
            if bet_sent:
                return
            if not self._monitoring_active.get(match_id, False):
                return

            # ---- ИСПРАВЛЕНО: берём актуальный payload, а не из замыкания ----
            current_payload = self._monitoring_payloads.get(match_id, payload)

            best_bet = self._choose_best_bet(data, current_payload)
            if not best_bet:
                return

            set_key = f"set_{best_bet['set_number']}"
            outcome_info = (data.get('outcome_ids', {})
                                .get(set_key, {})
                                .get(best_bet['market'], {})
                                .get(best_bet['side']))
            if not outcome_info:
                logger.warning(f"Нет идентификатора для исхода: {best_bet}")
                return

            bet_data = {
                "amount": current_payload.get('bet_size', 100),
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
                bet_data['matchid'] = outcome_info.get('matchid')
                bet_data['market_data'] = outcome_info.get('market_data')
                teams = current_payload.get('match_teams', ['', ''])
                bet_data['event_name'] = f"{teams[0]} - {teams[1]}"
            elif bk == 'ligastavok':
                bet_data['outcomeId'] = outcome_info.get('outcomeId')
                bet_data['factorId'] = outcome_info.get('factorId')
            elif bk == 'marathon':
                # event_id — это eventId Marathon (не treeId!)
                bet_data['coefficient_id'] = outcome_info.get('coefficient_id')
                bet_data['event_id'] = int(
                    data.get('event_id')
                    or data.get('match_id')
                    or current_payload.get('match_id')
                )
                bet_data['selection_id'] = outcome_info.get('selection_id')
                bet_data['odds'] = best_bet['odd']
            else:
                logger.error(f"Отправка для БК {bk} не реализована")
                return

            result = await handler.place_bet(page, bet_data)
            if result.get('success'):
                self._last_bet_time[match_id] = time.time()
                logger.info(f"✅ Ставка отправлена! ID: {result.get('bet_id', 'N/A')}")
                log_bus.success(
                    "Ставка",
                    f"{bk} · {current_payload.get('bet_size', 100)}₽ · "
                    f"{best_bet['market']} {best_bet['side']} @ {best_bet['odd']:.2f} · "
                    f"ID {result.get('bet_id', 'N/A')}"
                )
                bet_sent = True
                event.set()
                await self.stop_monitoring(match_id)
            else:
                logger.error(f"❌ Ошибка отправки ставки: {result.get('error')}")
                log_bus.error("Ставка", f"{bk} · {result.get('error')}")

        # setup_listener с match_id для Fonbet/Leon, без — для остальных
        try:
            await handler.setup_listener(page, on_update, match_id=match_id)
        except TypeError:
            await handler.setup_listener(page, on_update)

        try:
            timeout = AFTER_GOAL_TIMEOUT * 2
            await asyncio.wait_for(event.wait(), timeout=timeout)
            logger.info(f"⏹️ Мониторинг матча {match_id} завершён (ставка отправлена)")
        except asyncio.TimeoutError:
            logger.info(f"⏰ Мониторинг матча {match_id} завершён по таймауту")
        except asyncio.CancelledError:
            logger.info(f"Мониторинг матча {match_id} отменён")
        finally:
            await handler.stop_listener(page)
            if not bet_sent:
                await self.stop_monitoring(match_id)

    def _choose_best_bet(self, data: dict, payload: dict) -> Optional[dict]:
        set_markets = data.get('set_markets', {})
        if not set_markets:
            return None

        fast_sub = payload.get('fast_sub_score', [0, 0])
        current_set = fast_sub[0] + fast_sub[1] + 1
        set_key = f"set_{current_set}"
        markets = set_markets.get(set_key, {})
        if not markets:
            return None

        market_type = payload.get('market_type', 'auto')
        min_odds = payload.get('min_odds', 1.0)
        max_odds = payload.get('max_odds', 5.0)

        best = None
        best_odd = -1

        if market_type in ('total', 'auto'):
            total = markets.get('total', {})
            if total:
                for side in ['over', 'under']:
                    odd = total.get(side, 0)
                    if min_odds <= odd <= max_odds and odd > best_odd:
                        best_odd = odd
                        best = {
                            "market": "total",
                            "side": side,
                            "line": total.get('line', 0),
                            "odd": odd,
                            "set_number": current_set,
                        }

        if market_type in ('winner', 'auto'):
            winner = markets.get('winner', {})
            if winner:
                for side in ['1', '2']:
                    odd = winner.get(side, 0)
                    if min_odds <= odd <= max_odds and odd > best_odd:
                        best_odd = odd
                        best = {
                            "market": "winner",
                            "side": side,
                            "odd": odd,
                            "set_number": current_set,
                        }

        if market_type in ('handicap', 'auto'):
            handicap = markets.get('handicap', {})
            if handicap:
                for side in ['1', '2']:
                    odd = handicap.get(side, {}).get('odd', 0)
                    if min_odds <= odd <= max_odds and odd > best_odd:
                        best_odd = odd
                        best = {
                            "market": "handicap",
                            "side": side,
                            "line": handicap.get(side, {}).get('line', 0),
                            "odd": odd,
                            "set_number": current_set,
                        }

        return best