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
    PariHandler,
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
from .config_loader import load_config
from ui.log_bus import log_bus

import requests

ADSPOWER_API_URL = "http://localhost:50325"
AFTER_GOAL_TIMEOUT = 25
MONITOR_MAX_LIFETIME = 20.0
AFTER_GOAL_COOLDOWN = 10

logger = logging.getLogger(__name__)

_aux_logger = logging.getLogger('ui.after_goal_engine.aux')
_aux_logger.setLevel(logging.WARNING)


def _normalize_bk(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


HANDLERS = {
    "fonbet": FonbetHandler,
    "pari": PariHandler,
    "sportbet": SportbetHandler,
    "olimp": OlimpHandler,
    "betcity": BetcityHandler,
    "leon": LeonHandler,
    "zenit": ZenitHandler,
    "ligastavok": LigaStavokHandler,
    "winline": WinlineHandler,
    "marathon": MarathonHandler,
}

INSTANCE_BASED_HANDLERS = {
    BetcityHandler, OlimpHandler, FonbetHandler, SportbetHandler,
    ZenitHandler, LeonHandler, MarathonHandler, WinlineHandler, LigaStavokHandler,
    PariHandler,
}

AUTOBET_ENABLED_SPORTS_BY_BK = {
    "betcity":    {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
    "olimp":      {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
    "sportbet":   {"table_tennis", "volleyball", "basketball"},
    "fonbet":     {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
    "ligastavok": {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
    "marathon":   {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
    "winline":    {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
    "pari":       {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
}

SIGNAL_TTL_BY_BK = {
    "betcity":    15.0,
    "leon":       15.0,
    "winline":    15.0,
    "fonbet":     20.0,
    "pari":       20.0,
    "olimp":      15.0,
    "ligastavok": 25.0,
    "sportbet":   25.0,
    "marathon":   30.0,
    "zenit":      20.0,
}
SIGNAL_TTL_DEFAULT = 25.0

FAST_STATE_STALE_SEC = 6.0
FAST_STATE_POLL_INTERVAL = 1.0

SLOW_DATA_STALE_SEC = 5.0

LOAD_WAIT_BY_BK = {
    "fonbet":     3000,
    "betcity":    3000,
    "marathon":   3000,
    "leon":       2500,
    "winline":    2500,
    "ligastavok": 2500,
    "sportbet":   2500,
    "olimp":      3000,
}
LOAD_WAIT_DEFAULT = 2000
LOAD_WAIT_DEFAULT = 500

MAX_ACCEPTABLE_DELAY = 30.0

PHASE_END_BY_SCORE = {"table_tennis", "volleyball", "beach_volleyball"}
PHASE_END_BY_TIME = {"basketball", "cyber_basketball"}


LIVE_URLS = {
    'fonbet': {
        'table_tennis': 'https://fon.bet/live/table-tennis',
        'volleyball': 'https://fon.bet/live/volleyball',
        'basketball': 'https://fon.bet/live/basketball',
        'cyber_basketball': 'https://fon.bet/live/basketball',
        'any': 'https://fon.bet/live',
    },
    'winline': {
        'table_tennis': 'https://winline.ru/live/sport/nastolijnyj_tennis',
        'volleyball': 'https://winline.ru/live/sport/volejbol',
        'basketball': 'https://winline.ru/live/sport/basketbol',
        'cyber_basketball': 'https://winline.ru/live/sport/basketbol',
        'any': 'https://winline.ru/live',
    },
    'ligastavok': {
        'table_tennis': 'https://www.ligastavok.ru/live/table-tennis',
        'volleyball': 'https://www.ligastavok.ru/live/volleyball',
        'basketball': 'https://www.ligastavok.ru/live/basketball',
        'cyber_basketball': 'https://www.ligastavok.ru/live/basketball',
        'any': 'https://www.ligastavok.ru/live',
    },
    'leon': {
        'table_tennis': 'https://leon.ru/bets/table-tennis',
        'volleyball': 'https://leon.ru/bets/volleyball',
        'basketball': 'https://leon.ru/bets/basketball',
        'cyber_basketball': 'https://leon.ru/bets/basketball',
        'any': 'https://leon.ru/live',
    },
    'olimp': {
        'table_tennis': 'https://www.olimp.bet/live/nastolnyy-tennis-40',
        'volleyball': 'https://www.olimp.bet/live/voleybol-10',
        'basketball': 'https://www.olimp.bet/live/basketbol-5',
        'cyber_basketball': 'https://www.olimp.bet/live/kiberbasketbol-140',
        'any': 'https://www.olimp.bet/live',
    },
    'betcity': {
        'table_tennis': 'https://betcity.ru/ru/live/table-tennis',
        'volleyball': 'https://betcity.ru/ru/live/volleyball',
        'basketball': 'https://betcity.ru/ru/live/basketball',
        'cyber_basketball': 'https://betcity.ru/ru/live/basketball',
        'any': 'https://betcity.ru/ru/live',
    },
    'marathon': {
        'table_tennis': 'https://new.marathonbet.ru/su/live/table-tennis',
        'volleyball': 'https://new.marathonbet.ru/su/live/volleyball',
        'basketball': 'https://new.marathonbet.ru/su/live/basketball',
        'cyber_basketball': 'https://new.marathonbet.ru/su/live/basketball',
        'any': 'https://new.marathonbet.ru/su/live',
    },
    'zenit': {
        'table_tennis': 'https://zenit.win/live/134',
        'volleyball': 'https://zenit.win/live/41',
        'basketball': 'https://zenit.win/live/28',
        'cyber_basketball': 'https://zenit.win/live/564',
        'any': 'https://zenit.win/live',
    },
    'sportbet': {
        'table_tennis': 'https://sportbet.ru/live/table-tennis?isTime=1',
        'volleyball': 'https://sportbet.ru/live/volleyball?isTime=1',
        'basketball': 'https://sportbet.ru/live/basketball?isTime=1',
        'any': 'https://sportbet.ru/live?isTime=1',
    },
    'pari': {
        'table_tennis': 'https://pari.ru/live/table-tennis',
        'volleyball': 'https://pari.ru/live/volleyball',
        'basketball': 'https://pari.ru/live/basketball',
        'cyber_basketball': 'https://pari.ru/live/basketball',
        'any': 'https://pari.ru/live',
    },
}


# ── Маппинг код manual_markets → базовый рынок (для порогов) ──
_MARKET_CODE_TO_BASE = {
    "winner_1": "winner",
    "winner_2": "winner",
    "total_over": "total",
    "total_under": "total",
    "handicap_1": "handicap",
    "handicap_2": "handicap",
    "it1_over": "it",
    "it1_under": "it",
    "it2_over": "it",
    "it2_under": "it",
}


class AfterGoalEngine:
    PROFILE_LAUNCH_COOLDOWN = 30.0
    PREOPEN_COOLDOWN = 15.0

    def __init__(self):
        self._browsers: Dict[str, AdsPowerBrowser] = {}
        self._pages: Dict[str, Page] = {}
        self._handlers: Dict[str, BookmakerHandler] = {}

        self._last_bet_time: Dict[str, float] = {}
        self._monitoring_active: Dict[str, bool] = {}
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_payloads: Dict[str, dict] = {}
        self._monitoring_strategies: Dict[str, dict] = {}
        self._monitoring_profiles: Dict[str, str] = {}
        self._monitoring_signal_time: Dict[str, float] = {}

        self._last_update_time: Dict[str, float] = {}
        self._last_slow_data: Dict[str, dict] = {}

        self._bets_by_match: Dict[str, int] = {}
        self._bets_by_phase: Dict[Tuple[str, int], int] = {}
        self._bets_session_total: int = 0

        self._profile_launch_attempts: Dict[str, float] = {}
        self._profile_locks: Dict[str, asyncio.Lock] = {}
        self._preopen_last_attempt: Dict[str, float] = {}
        self._preopen_in_flight: set = set()

        self._profile_active_matches: Dict[str, int] = {}
        self._bet_queues: Dict[str, asyncio.Queue] = {}
        self._queue_workers: Dict[str, asyncio.Task] = {}
        self._bets_enqueued: set = set()

        self._fast_state: Dict[str, dict] = {}
        self._fast_poll_tasks: Dict[str, asyncio.Task] = {}

        self._last_ui_state: Dict[str, tuple] = {}

    # ============================================================
    # Активация стратегии
    # ============================================================
    async def activate_strategy(self, strategy: dict):
        profile_id = strategy.get('profile_id')
        if not profile_id:
            logger.warning("Стратегия не имеет profile_id, пропускаем активацию")
            return

        headless = strategy.get('headless', False)
        bk = _normalize_bk(strategy.get('bk'))
        sport = (strategy.get('sport') or 'any').lower()

        wrapper = await self._get_browser(profile_id, headless)
        if not wrapper:
            logger.error(f"❌ Не удалось активировать профиль {profile_id}")
            log_bus.error(
                "Стратегия",
                f"Не удалось открыть AdsPower-профиль {profile_id}. "
                f"Проверьте, что AdsPower запущен."
            )
            return

        if bk:
            bk_urls = LIVE_URLS.get(bk, {})
            start_url = bk_urls.get(sport) or bk_urls.get('any')
            if start_url:
                try:
                    await wrapper.page.goto(start_url, wait_until="domcontentloaded",
                                            timeout=30000)
                    logger.info(f"✅ Перешли на лайв-раздел {bk} [{sport}]: {start_url}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось перейти на {start_url}: {e}")
                    log_bus.warning("AdsPower",
                                    f"Не удалось перейти на лайв-раздел {bk}: {e}")
            else:
                logger.warning(f"⚠️ Неизвестная БК {bk}, пропускаем переход")

        # ── Проверка рынков стратегии против возможностей БК ──
        try:
            from ui.strategy_store import missing_markets_for_strategy
            missing = missing_markets_for_strategy(strategy)
            if missing:
                pretty = ", ".join(sorted(missing))
                msg = (
                    f"Стратегия «{strategy.get('name') or bk}» использует "
                    f"рынки, которых нет у {bk}: {pretty}. "
                    f"Сигналы по ним будут молча игнорироваться."
                )
                logger.warning(f"⚠️ {msg}")
                log_bus.warning("Стратегия", msg)
        except Exception as e:
            logger.debug(f"capability check: {e}")

        logger.info(f"✅ Профиль {profile_id} активирован (headless={headless})")
        log_bus.info("Стратегия",
                     f"Активирован профиль {profile_id} ({bk or '—'}, {sport})")

    async def deactivate_profile(self, profile_id: str, strategy_name: str = ""):
        if not profile_id:
            return

        logger.info(
            f"⏹ Deactivate profile {profile_id} "
            f"(strategy={strategy_name!r})"
        )

        bks_to_clear = set()
        try:
            for strat in self._monitoring_strategies.values():
                if strat.get('profile_id') == profile_id:
                    bk = _normalize_bk(strat.get('bk'))
                    if bk:
                        bks_to_clear.add(bk)
        except Exception:
            pass

        matches_to_cleanup = [
            mid for mid, pid in self._monitoring_profiles.items()
            if pid == profile_id
        ]
        for mid in matches_to_cleanup:
            try:
                await self._cleanup_monitoring(mid)
            except Exception as e:
                logger.warning(f"cleanup {mid}: {e}")

        wrapper = self._browsers.pop(profile_id, None)
        if wrapper:
            try:
                await wrapper.stop()
                logger.info(f"✅ Профиль {profile_id} закрыт (AdsPower stop)")
            except Exception as e:
                logger.warning(f"wrapper.stop({profile_id}): {e}")

        self._profile_launch_attempts.pop(profile_id, None)
        self._profile_active_matches.pop(profile_id, None)

        w = self._queue_workers.pop(profile_id, None)
        if w and not w.done():
            try:
                w.cancel()
            except Exception:
                pass
        self._bet_queues.pop(profile_id, None)

        logger.info(f"⏹ Профиль {profile_id} полностью выключен")

        try:
            from ui.balance_bus import balance_bus
            for bk in bks_to_clear:
                balance_bus.clear(bk)
        except Exception:
            pass

        try:
            log_bus.info(
                "Стратегия",
                f"Профиль {profile_id} выключен"
                + (f" ({strategy_name})" if strategy_name else "")
            )
        except Exception:
            pass

    # ============================================================
    # Fast state polling
    # ============================================================
    async def _poll_fast_state(self, match_id: str, sport: str,
                               player1: str, player2: str,
                               fast_bk: str, slow_bk: str):
        config = load_config()
        base = (config.get('backend_url') or 'http://localhost:8000').rstrip('/')
        url = f"{base}/match_state"
        params = {
            "sport": sport or "table_tennis",
            "player1": player1 or "",
            "player2": player2 or "",
        }

        logger.info(
            f"🔄 FAST_STATE poll запущен match={match_id} "
            f"fast_bk={fast_bk} slow_bk={slow_bk} url={url}"
        )

        fail_count = 0
        while self._monitoring_active.get(match_id, False):
            try:
                resp = await asyncio.to_thread(
                    requests.get, url, params=params, timeout=3
                )
                if resp.status_code == 200:
                    data = resp.json()
                    fail_count = 0

                    if data.get("found"):
                        bks = data.get("bks") or {}
                        bks_keys = sorted(bks.keys())

                        prev_entry = self._fast_state.get(match_id) or {}
                        prev_keys = prev_entry.get("_last_logged_keys")

                        self._fast_state[match_id] = {
                            "bks": bks,
                            "fetched_at": time.time(),
                            "now_on_backend": data.get("now"),
                            "_last_logged_keys": prev_keys,
                        }

                        if prev_keys != bks_keys:
                            self._fast_state[match_id]["_last_logged_keys"] = bks_keys
                            logger.info(
                                f"📡 FAST_STATE[{match_id}] bks={bks_keys} "
                                f"fast_present={fast_bk in bks}"
                            )
                        else:
                            logger.debug(
                                f"📡 FAST_STATE[{match_id}] без изменений "
                                f"({len(bks_keys)} БК)"
                            )
                    else:
                        was_not_found = (self._fast_state
                                         .get(match_id, {})
                                         .get("not_found", False))
                        self._fast_state[match_id] = {
                            "bks": {},
                            "fetched_at": time.time(),
                            "now_on_backend": data.get("now"),
                            "not_found": True,
                        }
                        if not was_not_found:
                            logger.warning(
                                f"📡 FAST_STATE[{match_id}] матч пропал из агрегатора"
                            )
                        else:
                            logger.debug(
                                f"📡 FAST_STATE[{match_id}] всё ещё не найден"
                            )
                else:
                    fail_count += 1
                    if fail_count == 3:
                        logger.warning(
                            f"⚠️ FAST_STATE[{match_id}] HTTP {resp.status_code}"
                        )
            except Exception as e:
                fail_count += 1
                if fail_count <= 3:
                    logger.debug(f"FAST_STATE[{match_id}] poll error: {e}")

            await asyncio.sleep(FAST_STATE_POLL_INTERVAL)

        logger.info(f"🛑 FAST_STATE poll остановлен match={match_id}")

    def _get_fast_state(self, match_id: str, fast_bk: str) -> Optional[dict]:
        entry = self._fast_state.get(match_id)
        if not entry:
            return None
        age = time.time() - entry.get("fetched_at", 0)
        if age > FAST_STATE_STALE_SEC:
            return None
        bks = entry.get("bks") or {}
        return bks.get(fast_bk)

    # ============================================================
    # Пороги отставания по рынкам
    # ============================================================
    def _get_market_threshold(self, strategy: dict, market: str) -> int:
        thresholds = strategy.get('market_thresholds') or {}
        val = thresholds.get(market)
        if val is None:
            val = strategy.get('min_score_diff', 2)
        try:
            return max(1, int(val))
        except Exception:
            return 2

    def _get_min_threshold(self, strategy: dict) -> int:
        """
        Минимальный порог по всем рынкам, которые стратегия реально использует.

        manual — берём пороги только по рынкам, отмеченным в manual_markets.
        auto   — минимум по всем четырём базовым рынкам.
        """
        thresholds = strategy.get('market_thresholds') or {}
        default = strategy.get('min_score_diff', 2)

        def _safe_int(v, d):
            try:
                return max(1, int(v))
            except Exception:
                return d

        mode = strategy.get('market_mode', 'auto')

        if mode == 'manual':
            manual = strategy.get('manual_markets') or []
            base_markets = set()
            for code in manual:
                m = _MARKET_CODE_TO_BASE.get(code)
                if m:
                    base_markets.add(m)
            if not base_markets:
                return _safe_int(default, 2)
            values = [_safe_int(thresholds.get(m, default), default)
                      for m in base_markets]
            return min(values)

        values = []
        for m in ('winner', 'total', 'handicap', 'it'):
            values.append(_safe_int(thresholds.get(m, default), default))
        return min(values) if values else _safe_int(default, 2)

    # ============================================================
    # Рынки manual_markets ↔ внутренний формат
    # ============================================================
    @staticmethod
    def _market_code(market: str, side: str) -> Optional[str]:
        """(market, side) → код в manual_markets."""
        if market == 'winner':
            return f'winner_{side}'            # winner_1 / winner_2
        if market == 'total':
            return f'total_{side}'             # total_over / total_under
        if market == 'handicap':
            return f'handicap_{side}'          # handicap_1 / handicap_2
        if market == 'it':
            # side = '1_over' / '2_under' → it1_over / it2_under
            if '_' in side:
                player, dirn = side.split('_', 1)
                return f'it{player}_{dirn}'
            return None
        if market == 'odd':
            return 'odd'                        # одна галочка
        if market == 'point':
            return 'point'                      # одна галочка
        return None

    def _is_market_allowed(self, market: str, side: str,
                           manual: set) -> bool:
        """Разрешён ли исход по настройкам manual_markets."""
        is_race = market.startswith('race_')
        base = market[5:] if is_race else market

        if is_race and 'race' not in manual:
            return False

        code = self._market_code(base, side)
        if not code:
            return False
        return code in manual

    # ============================================================
    # RE-CHECK задержки прямо сейчас
    # ============================================================
    def _verify_delay_now(self, match_id: str, strategy: dict,
                          market: Optional[str] = None) -> Tuple[bool, str]:
        payload = self._monitoring_payloads.get(match_id)
        if not payload:
            return False, "нет payload"

        fast_bk = _normalize_bk(payload.get('fast_bk'))
        fast_state = self._get_fast_state(match_id, fast_bk)
        if not fast_state:
            return False, f"fast_state протух (> {FAST_STATE_STALE_SEC:.0f}с)"

        slow_data = self._last_slow_data.get(match_id)
        if not slow_data:
            return False, "нет slow_data"

        slow_age = time.time() - self._last_update_time.get(match_id, 0)
        if slow_age > SLOW_DATA_STALE_SEC:
            return False, f"slow_data старше {slow_age:.1f}с"

        fast_sub1 = int(fast_state.get("sub_score1") or 0)
        fast_sub2 = int(fast_state.get("sub_score2") or 0)
        slow_sub1 = int(slow_data.get("sub_score1") or 0)
        slow_sub2 = int(slow_data.get("sub_score2") or 0)

        diff = max(fast_sub1 - slow_sub1, fast_sub2 - slow_sub2)
        if market:
            threshold = self._get_market_threshold(strategy, market)
        else:
            threshold = self._get_min_threshold(strategy)

        if diff >= threshold:
            return True, (
                f"fast {fast_sub1}:{fast_sub2} vs slow {slow_sub1}:{slow_sub2} "
                f"(+{diff}, порог={threshold})"
            )
        return False, (
            f"fast {fast_sub1}:{fast_sub2} vs slow {slow_sub1}:{slow_sub2} "
            f"(diff={diff} < {threshold})"
        )

    # ============================================================
    # REFRESH outcome_id для retry
    # ============================================================
    def _refresh_outcome_for_retry(self, match_id: str,
                                   bet_info: dict) -> Optional[dict]:
        payload = self._monitoring_payloads.get(match_id)
        if not payload:
            return None

        set_num = bet_info.get("set_number")
        market = bet_info.get("market")
        side = bet_info.get("side")
        if not (set_num and market and side):
            return None

        set_key = f"set_{set_num}"
        outcome_ids = payload.get("outcome_ids") or {}
        info = (outcome_ids.get(set_key, {})
                            .get(market, {})
                            .get(side))
        if not info:
            logger.debug(
                f"Olimp retry: в payload нет {set_key}/{market}/{side}"
            )
            return None

        market_data = info.get("market_data")
        if not market_data:
            return None

        return {
            "market_data": market_data,
            "outcome_id":  info.get("id"),
            "kf":          info.get("kf"),
        }

    # ============================================================
    # Preopen
    # ============================================================
    async def preopen_match_with_profile(self, payload: dict, strategy: dict):
        match_id = payload.get('match_id')
        if not match_id:
            logger.warning("Нет match_id в payload")
            return
        if match_id in self._preopen_in_flight:
            logger.debug(f"Преopen {match_id} уже выполняется — пропускаем дубль")
            return
        self._preopen_in_flight.add(match_id)
        try:
            await self._preopen_impl(payload, strategy)
        finally:
            self._preopen_in_flight.discard(match_id)

    async def _preopen_impl(self, payload: dict, strategy: dict):
        match_id = payload.get('match_id')
        profile_id = strategy.get('profile_id')

        logger.info(
            f"🔵 PREOPEN START match={match_id} profile={profile_id} "
            f"is_new={payload.get('is_new')} slow_bk={payload.get('slow_bk')} "
            f"sport={payload.get('sport')} "
            f"fast_bk={payload.get('fast_bk')} delay={payload.get('delay')}"
        )

        if not profile_id:
            logger.warning(f"🔴 PREOPEN SKIP {match_id}: нет profile_id")
            return

        max_session = int(strategy.get('max_bets_per_session', 0) or 0)
        if max_session > 0 and self._bets_session_total >= max_session:
            logger.info(
                f"🔴 PREOPEN SKIP {match_id}: лимит сессии достигнут "
                f"({self._bets_session_total}/{max_session})"
            )
            log_bus.info(
                "Стратегия",
                f"Лимит ставок за сессию достигнут ({max_session}) — "
                f"новые сигналы игнорируются"
            )
            return

        delay_in_signal = payload.get('delay', 0) or 0
        if delay_in_signal > MAX_ACCEPTABLE_DELAY:
            logger.info(
                f"🔴 PREOPEN SKIP {match_id}: delay={delay_in_signal:.1f}с "
                f"> {MAX_ACCEPTABLE_DELAY}с — сигнал слишком старый"
            )
            return

        headless = strategy.get('headless', False)

        if strategy.get('ignore_repeats', False) and not payload.get('is_new', False):
            logger.info(f"🔴 PREOPEN SKIP {match_id}: ignore_repeats и не is_new")
            return

        sport = (payload.get('sport') or 'table_tennis').lower()
        slow_bk_early = _normalize_bk(payload.get('slow_bk'))
        allowed = AUTOBET_ENABLED_SPORTS_BY_BK.get(slow_bk_early, set())
        if sport not in allowed:
            logger.info(
                f"🔴 PREOPEN SKIP {match_id}: спорт '{sport}' не включён для "
                f"'{slow_bk_early}' (allowed={allowed})"
            )
            return

        if match_id in self._last_bet_time:
            delta = time.time() - self._last_bet_time[match_id]
            if delta < AFTER_GOAL_COOLDOWN:
                logger.info(f"🔴 PREOPEN SKIP {match_id}: cooldown {delta:.1f}с")
                return

        max_bets = strategy.get('max_bets_per_match', 1)
        if self._bets_by_match.get(match_id, 0) >= max_bets:
            logger.info(f"🔴 PREOPEN SKIP {match_id}: лимит ставок ({max_bets})")
            return

        if match_id in self._monitoring_active and self._monitoring_active[match_id]:
            self._monitoring_payloads[match_id] = payload
            self._monitoring_strategies[match_id] = strategy
            logger.info(f"🔄 PREOPEN UPDATE {match_id}: обновлены параметры мониторинга")
            return

        now = time.time()
        last_attempt = self._preopen_last_attempt.get(match_id, 0.0)
        if now - last_attempt < self.PREOPEN_COOLDOWN:
            logger.info(f"🔴 PREOPEN SKIP {match_id}: "
                        f"PREOPEN_COOLDOWN ({now - last_attempt:.1f}с)")
            return
        self._preopen_last_attempt[match_id] = now

        slow_bk = _normalize_bk(payload.get('slow_bk') or payload.get('bk'))
        handler_cls = HANDLERS.get(slow_bk)
        if not handler_cls:
            logger.error(f"🔴 PREOPEN SKIP {match_id}: нет handler для {slow_bk}")
            return

        max_parallel = 2 if handler_cls in INSTANCE_BASED_HANDLERS else 1
        active = self._profile_active_matches.get(profile_id, 0)
        if active >= max_parallel:
            logger.warning(
                f"🔴 PREOPEN SKIP {match_id}: профиль {profile_id} [{slow_bk}] — "
                f"уже {active} активных (лимит {max_parallel})"
            )
            return

        browser_wrapper = await self._get_existing_browser(profile_id)
        if not browser_wrapper:
            logger.warning(
                f"🔴 PREOPEN SKIP {match_id}: профиль {profile_id} не активен"
            )
            log_bus.warning(
                "Стратегия",
                f"Профиль {profile_id} не открыт. Выключите и включите стратегию заново."
            )
            return

        try:
            page = await browser_wrapper.new_page()
            logger.info(f"📄 Открыта отдельная вкладка для матча {match_id}")
        except Exception as e:
            logger.error(f"Не удалось открыть новую вкладку: {e} — используем главную")
            page = browser_wrapper.page

        player1, player2 = payload.get('match_teams', ['', ''])

        handler = handler_cls(
            target_match_id=match_id,
            target_teams=[player1, player2],
        )
        self._handlers[match_id] = handler

        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.error(f"prepare_page для {slow_bk} упал: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                self._handlers.pop(match_id, None)
                return

        match_url = payload.get('match_url') or build_match_url(slow_bk, payload)
        url_ok = False

        if slow_bk == "ligastavok" and match_url:
            try:
                cookies = await page.context.cookies()
                has_qrator = any(
                    "qrator" in (c.get("name") or "").lower()
                    for c in cookies
                    if "ligastavok" in (c.get("domain") or "")
                )
                if not has_qrator:
                    logger.info("LigaStavok: нет Qrator cookies — прогреваю через /live")
                    try:
                        await page.goto("https://www.ligastavok.ru/live",
                                        wait_until="domcontentloaded",
                                        timeout=20000)
                        await page.wait_for_timeout(2500)
                    except Exception as e:
                        logger.warning(f"LigaStavok: прогрев не удался: {e}")
                else:
                    logger.debug("LigaStavok: Qrator cookies уже есть")
            except Exception as e:
                logger.debug(f"LigaStavok: проверка cookies: {e}")

        if match_url:
            try:
                await page.goto(match_url, wait_until="domcontentloaded",
                                timeout=AFTER_GOAL_TIMEOUT * 1000)

                load_wait_ms = LOAD_WAIT_BY_BK.get(slow_bk, LOAD_WAIT_DEFAULT)
                await page.wait_for_timeout(load_wait_ms)

                found_players = False
                for _ in range(40):
                    if await self._check_players_on_page(page, player1, player2):
                        found_players = True
                        break
                    await page.wait_for_timeout(300)

                if found_players:
                    logger.info(f"✅ Перешли по URL: {match_url}")
                    log_bus.info("Матч", f"Открыт по URL · {player1} vs {player2}")
                    url_ok = True
                else:
                    try:
                        body_full = await page.locator("body").inner_text()
                        body_snippet = body_full[:500].replace("\n", " ")
                        body_len = len(body_full)
                        title = (await page.title())[:150]
                    except Exception:
                        body_snippet = "(body не читается)"
                        body_len = -1
                        title = "(title не читается)"

                    body_low = body_snippet.lower()
                    p1_t = [t.lower() for t in re.split(r"\W+", player1)
                            if len(t) >= 2]
                    p2_t = [t.lower() for t in re.split(r"\W+", player2)
                            if len(t) >= 2]
                    found_p1 = [t for t in p1_t if t in body_low]
                    found_p2 = [t for t in p2_t if t in body_low]

                    logger.warning(
                        f"⚠️ URL открылся, но игроки не найдены — ищем кликом\n"
                        f"   Ищем: '{player1}' / '{player2}'\n"
                        f"   page.url: {page.url[:120]}\n"
                        f"   title: {title}\n"
                        f"   body_len={body_len}\n"
                        f"   В body найдены: p1={found_p1}, p2={found_p2}\n"
                        f"   body[:500]={body_snippet!r}"
                    )
                    log_bus.warning(
                        "Матч",
                        f"Игроки не найдены в DOM (body_len={body_len}) — ищем кликом"
                    )
            except Exception as e:
                logger.warning(f"⚠️ URL не сработал ({e}), пробуем клик")
                log_bus.warning("Матч", f"URL не сработал ({e}), ищем кликом")

        if not url_ok:
            found = await self._click_match_on_page(page, player1, player2)
            if not found:
                logger.error(f"❌ Не удалось открыть матч {player1} vs {player2}")
                log_bus.error("Матч", f"Не удалось открыть {player1} vs {player2}")
                self._handlers.pop(match_id, None)
                try:
                    await page.close()
                except Exception:
                    pass
                return
            else:
                log_bus.info("Матч", f"Найден кликом · {player1} vs {player2}")

        self._pages[match_id] = page
        self._monitoring_active[match_id] = True
        self._monitoring_payloads[match_id] = payload
        self._monitoring_strategies[match_id] = strategy
        self._monitoring_profiles[match_id] = profile_id
        self._monitoring_signal_time[match_id] = time.time()
        self._profile_active_matches[profile_id] = active + 1

        fast_bk = _normalize_bk(payload.get('fast_bk'))
        self._fast_poll_tasks[match_id] = asyncio.create_task(
            self._poll_fast_state(
                match_id=match_id,
                sport=sport,
                player1=player1,
                player2=player2,
                fast_bk=fast_bk,
                slow_bk=slow_bk,
            )
        )

        task = asyncio.create_task(
            self._monitor_loop(match_id, payload, strategy, handler)
        )
        self._monitoring_tasks[match_id] = task

        logger.info(f"✅ PREOPEN DONE match={match_id} профиль={profile_id}")

        log_bus.match_event(
            bk=slow_bk,
            match_id=match_id,
            teams=[player1, player2],
            message=f"👁 Преоткрыт матч · delay {delay_in_signal:.1f}с · {sport}",
            level="info",
        )

    async def stop_monitoring(self, match_id: str):
        await self._cleanup_monitoring(match_id)

    # ============================================================
    # Очередь ставок
    # ============================================================
    def _ensure_queue_worker(self, profile_id: str):
        w = self._queue_workers.get(profile_id)
        if w and not w.done():
            return
        self._queue_workers[profile_id] = asyncio.create_task(
            self._bet_worker(profile_id)
        )

    async def _enqueue_bet(self, profile_id: str, match_id: str,
                           handler, page, bet_data: dict,
                           bk: str, amount: float, bet_info: dict):
        if match_id in self._bets_enqueued:
            logger.info(f"⏭ Матч {match_id} уже в очереди — не дублируем")
            return
        self._bets_enqueued.add(match_id)

        self._ensure_queue_worker(profile_id)
        q = self._bet_queues.get(profile_id)
        if q is None:
            q = asyncio.Queue()
            self._bet_queues[profile_id] = q

        signal_time = self._monitoring_signal_time.get(match_id, time.time())
        teams = (self._monitoring_payloads
                 .get(match_id, {})
                 .get('match_teams', ['?', '?']))

        await q.put({
            "match_id": match_id,
            "handler": handler,
            "page": page,
            "bet_data": bet_data,
            "signal_time": signal_time,
            "bk": bk,
            "amount": amount,
            "bet_info": bet_info,
            "match_teams": teams,
        })
        logger.info(
            f"📥 В очередь [{bk}/{profile_id}] поставлен match={match_id} "
            f"(в очереди: {q.qsize()})"
        )

    async def _bet_worker(self, profile_id: str):
        q = self._bet_queues.setdefault(profile_id, asyncio.Queue())
        logger.info(f"🔄 Bet worker [{profile_id}] запущен")

        while True:
            try:
                item = await q.get()
            except asyncio.CancelledError:
                logger.info(f"🛑 Bet worker [{profile_id}] остановлен")
                break

            match_id = item.get("match_id")
            try:
                await self._execute_bet(item)
            except Exception as e:
                logger.error(f"bet worker [{profile_id}] / {match_id}: {e}",
                             exc_info=True)
            finally:
                page = item.get("page")
                if page and not page.is_closed():
                    try:
                        await page.close()
                        logger.debug(f"Страница матча {match_id} закрыта после ставки")
                    except Exception:
                        pass
                self._bets_enqueued.discard(match_id)
                q.task_done()

    async def _execute_bet(self, item: dict):
        match_id = item["match_id"]
        signal_time = item["signal_time"]
        bk = item["bk"]
        handler = item["handler"]
        page = item["page"]
        bet_data = item["bet_data"]
        amount = item["amount"]
        bet_info = item["bet_info"]
        teams = item.get("match_teams") or ["?", "?"]

        ttl = SIGNAL_TTL_BY_BK.get(bk, SIGNAL_TTL_DEFAULT)
        age = time.time() - signal_time
        if age > ttl:
            logger.warning(
                f"⏰ Сигнал {match_id} [{bk}] устарел ({age:.1f}с > {ttl:.0f}с) — дропаем"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"⏰ Сигнал устарел ({age:.0f}с > {ttl:.0f}с) — пропуск",
                level="warning",
            )
            return

        if not self._monitoring_active.get(match_id, False):
            logger.info(f"⏭ Мониторинг {match_id} неактивен — дропаем ставку")
            return

        if page.is_closed():
            logger.warning(f"⏭ Страница {match_id} закрыта — дропаем ставку")
            return

        strategy = self._monitoring_strategies.get(match_id) or {}
        target_market = bet_info.get('market')
        # RACE-префикс срезаем, чтобы взять порог от базового рынка
        if target_market and target_market.startswith("race_"):
            target_market = target_market.replace("race_", "", 1)
        is_open_now, why = self._verify_delay_now(
            match_id, strategy, market=target_market
        )
        if not is_open_now:
            logger.warning(
                f"⏭ BET EXEC [{bk}] match={match_id} — задержка пропала: {why}"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"⏭ Ставка отменена: задержка пропала ({why})",
                level="warning",
            )
            return

        logger.info(
            f"🎯 BET EXEC [{bk}] match={match_id} "
            f"(возраст сигнала {age:.1f}с, delay: {why})"
        )

        log_bus.match_event(
            bk=bk, match_id=match_id, teams=teams,
            message=f"📤 Отправка: {bet_info.get('market')} "
                    f"{bet_info.get('side')} @ {bet_info.get('odd', 0):.2f} "
                    f"· {amount}₽ · возраст {age:.1f}с",
            level="info",
        )

        try:
            result = await handler.place_bet(page, bet_data)
        except Exception as e:
            logger.error(f"place_bet выбросил: {e}", exc_info=True)
            result = {"success": False, "error": str(e)}

        if (not result.get("success")
                and bk == "olimp"
                and "недоступ" in (result.get("error") or "").lower()):

            is_open_now, why2 = self._verify_delay_now(
                match_id, strategy, market=target_market
            )
            if not is_open_now:
                logger.warning(
                    f"⏭ Olimp retry отменён — задержка пропала: {why2}"
                )
                log_bus.match_event(
                    bk=bk, match_id=match_id, teams=teams,
                    message=f"⏭ Retry отменён: задержка пропала ({why2})",
                    level="warning",
                )
            else:
                fresh = self._refresh_outcome_for_retry(match_id, bet_info)
                if fresh:
                    logger.info(
                        f"🔁 Olimp retry: market_data "
                        f"{bet_data.get('market_data')} → {fresh.get('market_data')} · {why2}"
                    )
                    bet_data['market_data'] = fresh['market_data']
                    bet_data['outcome_id'] = fresh.get('outcome_id')
                    bet_data['kf'] = fresh.get('kf', bet_data.get('kf'))
                    try:
                        result = await handler.place_bet(page, bet_data)
                    except Exception as e:
                        logger.error(f"place_bet retry выбросил: {e}", exc_info=True)
                        result = {"success": False, "error": str(e)}
                else:
                    logger.warning(
                        "⏭ Olimp retry: свежий basketId не найден в payload"
                    )

        if result.get("success"):
            self._bets_by_match[match_id] = self._bets_by_match.get(match_id, 0) + 1
            set_key_num = bet_info.get("set_number", 0)
            phase_key = (match_id, set_key_num)
            self._bets_by_phase[phase_key] = self._bets_by_phase.get(phase_key, 0) + 1
            self._last_bet_time[match_id] = time.time()
            self._bets_session_total += 1
            logger.info(
                f"📊 Ставок за сессию: {self._bets_session_total}"
            )
            try:
                from ui.stats_bus import stats_bus
                stats_bus.on_bet_placed(
                    bk, amount, bet_info.get('odd', 0)
                )
            except Exception as e:
                logger.debug(f"stats_bus: {e}")
            logger.info(
                f"✅ Ставка [{bk}] match={match_id} отправлена! "
                f"ID: {result.get('bet_id', 'N/A')} · {bet_info.get('reason', '')}"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"✅ Ставка принята! ID {result.get('bet_id', 'N/A')} "
                        f"· {bet_info.get('market')} {bet_info.get('side')} "
                        f"@ {bet_info.get('odd', 0):.2f} · {amount}₽",
                level="success",
            )
        else:
            err = result.get("error") or "unknown"
            logger.error(
                f"❌ Ошибка ставки [{bk}] match={match_id}: {err}"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"❌ Ставка не прошла: {err[:160]}",
                level="error",
            )

    # ============================================================
    # Shutdown
    # ============================================================
    async def shutdown(self):
        logger.info("🛑 AfterGoalEngine.shutdown: начало")

        tasks = [t for t in self._monitoring_tasks.values() if t and not t.done()]
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

        for mid, t in list(self._fast_poll_tasks.items()):
            try:
                t.cancel()
            except Exception:
                pass
        if self._fast_poll_tasks:
            try:
                await asyncio.wait(list(self._fast_poll_tasks.values()), timeout=2.0)
            except Exception:
                pass
        self._fast_poll_tasks.clear()
        self._fast_state.clear()

        for pid, w in list(self._queue_workers.items()):
            try:
                w.cancel()
            except Exception:
                pass
        if self._queue_workers:
            try:
                await asyncio.wait(list(self._queue_workers.values()), timeout=2.0)
            except Exception:
                pass
        self._queue_workers.clear()
        self._bet_queues.clear()

        for match_id in list(self._pages.keys()):
            try:
                page = self._pages.pop(match_id, None)
                if page and not page.is_closed():
                    await page.close()
            except Exception as e:
                logger.warning(f"shutdown: закрытие страницы {match_id}: {e}")

        self._monitoring_active.clear()
        self._monitoring_payloads.clear()
        self._monitoring_strategies.clear()
        self._monitoring_profiles.clear()
        self._monitoring_signal_time.clear()
        self._last_update_time.clear()
        self._last_slow_data.clear()
        self._handlers.clear()
        self._profile_active_matches.clear()
        self._bets_enqueued.clear()
        self._last_ui_state.clear()

        for profile_id, wrapper in list(self._browsers.items()):
            try:
                if wrapper:
                    await wrapper.__aexit__(None, None, None)
                    logger.info(f"✅ Закрыт браузер профиля {profile_id}")
            except Exception as e:
                logger.warning(f"shutdown: закрытие браузера {profile_id}: {e}")
        self._browsers.clear()

        logger.info("✅ AfterGoalEngine.shutdown: завершён")

    # ============================================================
    # Cleanup
    # ============================================================
    async def _cleanup_monitoring(self, match_id: str):
        task = self._monitoring_tasks.pop(match_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        poll_t = self._fast_poll_tasks.pop(match_id, None)
        if poll_t and not poll_t.done():
            poll_t.cancel()
            try:
                await poll_t
            except (asyncio.CancelledError, Exception):
                pass
        self._fast_state.pop(match_id, None)

        handler = self._handlers.pop(match_id, None)
        page = self._pages.get(match_id)
        if handler and page and not page.is_closed():
            try:
                await handler.stop_listener(page)
            except Exception as e:
                logger.debug(f"stop_listener({match_id}): {e}")

        if match_id in self._bets_enqueued:
            logger.info(f"⏸ Страница {match_id} не закрыта — ставка в очереди")
        else:
            if match_id in self._pages:
                try:
                    p = self._pages.pop(match_id)
                    if p and not p.is_closed():
                        await p.close()
                except Exception as e:
                    logger.warning(f"Ошибка закрытия страницы {match_id}: {e}")

        profile_id = self._monitoring_profiles.pop(match_id, None)
        if profile_id and profile_id in self._profile_active_matches:
            self._profile_active_matches[profile_id] = max(
                0, self._profile_active_matches[profile_id] - 1
            )

        payload = self._monitoring_payloads.get(match_id) or {}
        start_ts = self._monitoring_signal_time.get(match_id)
        elapsed = (time.time() - start_ts) if start_ts else 0
        bets_count = self._bets_by_match.get(match_id, 0)

        self._monitoring_active[match_id] = False
        self._monitoring_payloads.pop(match_id, None)
        self._monitoring_strategies.pop(match_id, None)
        self._monitoring_signal_time.pop(match_id, None)
        self._last_update_time.pop(match_id, None)
        self._last_slow_data.pop(match_id, None)
        self._bets_enqueued.discard(match_id)
        self._last_ui_state.pop(match_id, None)
        logger.info(f"🛑 CLEANUP match={match_id}")

        if payload:
            slow_bk = _normalize_bk(payload.get('slow_bk'))
            if bets_count > 0:
                msg = f"⏹ Матч закрыт · ставок {bets_count} · {elapsed:.0f}с"
                lvl = "success"
            else:
                msg = f"⏹ Матч закрыт без ставки · {elapsed:.0f}с"
                lvl = "info"
            log_bus.match_event(
                bk=slow_bk, match_id=match_id,
                teams=payload.get('match_teams', ['?', '?']),
                message=msg,
                level=lvl,
            )

    # ============================================================
    # Value / Arbitrage / Corridor
    # ============================================================
    async def place_value_bet(self, payload: dict, strategies: List[dict]):
        bk_raw = payload.get('bk')
        if not bk_raw:
            _aux_logger.debug("Value: нет БК в payload")
            return
        bk = _normalize_bk(bk_raw)

        matching = [s for s in strategies
                    if s.get('type', '').lower() == 'value'
                    and _normalize_bk(s.get('bk')) == bk]
        if not matching:
            _aux_logger.debug(f"Value: нет активной стратегии для БК {bk}")
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
                _aux_logger.debug(f"Value: кулдаун для матча {match_id}")
                return

        handler_cls = HANDLERS.get(bk)
        if not handler_cls:
            logger.error(f"Value: нет обработчика для БК {bk}")
            return

        browser_wrapper = await self._get_existing_browser(profile_id)
        if not browser_wrapper:
            logger.warning(f"Value: профиль {profile_id} не открыт — пропускаем")
            return

        match_url = payload.get('match_url') or build_match_url(bk, payload)
        page = browser_wrapper.page

        handler = handler_cls()

        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.error(f"Value: prepare_page для {bk} упал: {e}")
                return

        player1 = payload.get('player1', '') or (payload.get('match_teams') or ['', ''])[0]
        player2 = payload.get('player2', '') or (payload.get('match_teams') or ['', ''])[1]

        url_ok = False
        if match_url:
            try:
                await page.goto(match_url, wait_until="domcontentloaded",
                                timeout=AFTER_GOAL_TIMEOUT * 1000)
                await page.wait_for_timeout(3000)
                for _ in range(40):
                    if await self._check_players_on_page(page, player1, player2):
                        logger.info(f"Value: перешли по URL {match_url}")
                        url_ok = True
                        break
                    await page.wait_for_timeout(300)
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
            logger.warning(f"Value: не удалось получить идентификаторы для {match_id}")
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
            logger.warning(f"Value: не найден идентификатор для {outcome}")
            await page.close()
            return

        amount = strategy.get('bet_size', 100)
        bet_data = {"amount": amount, "value": payload.get('odd', 0.0)}

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
            log_bus.success("Ставка",
                            f"Value · {bk} · {amount}₽ · ID {result.get('bet_id', 'N/A')}")
        else:
            logger.error(f"❌ Ошибка Value-ставки: {result.get('error')}")
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
        _aux_logger.debug("Arbitrage: пока не реализовано")
        return {"success": False, "error": "Arbitrage not implemented"}

    async def place_corridor_bet(self, payload: dict, strategies: List[dict]):
        _aux_logger.debug("Corridor: пока не реализовано")
        return {"success": False, "error": "Corridor not implemented"}

    # ============================================================
    # AdsPower
    # ============================================================
    async def _get_api_key_for_profile(self, profile_id: str) -> Optional[str]:
        data_dir = get_app_data_dir()
        path = os.path.join(data_dir, "accounts.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    accounts = json.load(f)
                for acc in accounts:
                    if acc.get("ads_power_id") == profile_id:
                        key = acc.get("api_key")
                        if key:
                            return key
                        logger.warning(f"⚠️ Для профиля {profile_id} api_key пуст")
                        return None
                logger.warning(f"⚠️ Профиль {profile_id} не найден в accounts.json")
            else:
                logger.error(f"❌ Файл accounts.json не найден по пути {path}")
        except Exception as e:
            logger.error(f"❌ Ошибка чтения accounts.json: {e}")
        return None

    async def _get_browser(self, profile_id: str, headless: bool = False) \
            -> Optional[AdsPowerBrowser]:
        if profile_id in self._browsers:
            wrapper = self._browsers[profile_id]
            try:
                if wrapper.browser and wrapper.browser.is_connected():
                    return wrapper
            except Exception:
                pass
            logger.warning(f"Профиль {profile_id} отвалился — закрываем wrapper")
            try:
                await wrapper.stop()
            except Exception as e:
                logger.debug(f"wrapper.stop: {e}")
            del self._browsers[profile_id]

        now = time.time()
        last_attempt = self._profile_launch_attempts.get(profile_id, 0.0)
        if now - last_attempt < self.PROFILE_LAUNCH_COOLDOWN:
            remaining = self.PROFILE_LAUNCH_COOLDOWN - (now - last_attempt)
            logger.warning(f"⏳ Профиль {profile_id} в cooldown, {remaining:.0f}с")
            return None

        lock = self._profile_locks.get(profile_id)
        if lock is None:
            lock = asyncio.Lock()
            self._profile_locks[profile_id] = lock

        async with lock:
            if profile_id in self._browsers:
                w = self._browsers[profile_id]
                try:
                    if w.browser and w.browser.is_connected():
                        return w
                except Exception:
                    pass
                try:
                    await w.stop()
                except Exception:
                    pass
                del self._browsers[profile_id]

            self._profile_launch_attempts[profile_id] = time.time()

            api_key = await self._get_api_key_for_profile(profile_id)
            if not api_key:
                logger.error(f"❌ Не найден API-ключ для профиля {profile_id}")
                return None

            try:
                wrapper = AdsPowerBrowser(
                    profile_id=profile_id,
                    api_key=api_key,
                    api_url=ADSPOWER_API_URL,
                    headless=headless,
                )
                await wrapper.__aenter__()
                self._browsers[profile_id] = wrapper
                logger.info(f"✅ Браузер для профиля {profile_id} запущен "
                            f"(headless={headless})")
                log_bus.success("AdsPower", f"Браузер запущен · профиль {profile_id}")
                return wrapper
            except Exception as e:
                err_text = str(e)
                if "Too many request" in err_text or "429" in err_text:
                    logger.warning(f"⏸ AdsPower rate limit [{profile_id}], "
                                   f"cooldown {self.PROFILE_LAUNCH_COOLDOWN}с")
                    log_bus.warning("AdsPower",
                                    f"AdsPower отбивает запросы — пауза "
                                    f"{self.PROFILE_LAUNCH_COOLDOWN:.0f}с")
                else:
                    logger.error(
                        f"❌ Ошибка запуска AdsPower [{profile_id}]: {e}",
                        exc_info=True,
                    )
                    log_bus.error("AdsPower",
                                  f"Не удалось запустить профиль {profile_id}: {e}")
                return None

    async def _get_existing_browser(self, profile_id: str) \
            -> Optional[AdsPowerBrowser]:
        wrapper = self._browsers.get(profile_id)
        if not wrapper:
            return None
        try:
            if wrapper.browser and wrapper.browser.is_connected():
                return wrapper
        except Exception:
            pass
        logger.warning(f"_get_existing_browser: {profile_id} мёртв — закрываем wrapper")
        try:
            await wrapper.stop()
        except Exception as e:
            logger.debug(f"wrapper.stop: {e}")
        del self._browsers[profile_id]
        return None

    # ============================================================
    # Поиск матча
    # ============================================================
    async def _click_match_on_page(self, page: Page, player1: str, player2: str) -> bool:
        if not player1 or not player2:
            return False

        try:
            await page.wait_for_load_state('domcontentloaded', timeout=3000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        def normalize(s: str) -> str:
            s = (s or "").lower()
            return re.sub(r'[_\(\)\.\,\;\:\!\?\-\|]', ' ', s)

        def tokens(name):
            return [t for t in normalize(name).split() if len(t) >= 3]

        p1_t = tokens(player1)
        p2_t = tokens(player2)
        if not p1_t or not p2_t:
            return False

        selectors = [
            "a:visible",
            "[class*='event']:visible",
            "[class*='match']:visible",
            "[class*='Event']:visible",
            "[role='button']:visible",
            "div[onclick]:visible",
        ]

        for sel in selectors:
            try:
                els = await page.locator(sel).all()
            except Exception:
                continue
            for el in els[:200]:
                try:
                    text = (await el.inner_text()).lower()
                except Exception:
                    continue
                if (any(t in text for t in p1_t) and
                        any(t in text for t in p2_t)):
                    try:
                        href = await el.get_attribute("href")
                    except Exception:
                        href = None
                    if href:
                        if href.startswith("/"):
                            from urllib.parse import urlparse
                            u = urlparse(page.url)
                            href = f"{u.scheme}://{u.netloc}{href}"
                        await page.goto(href, wait_until="domcontentloaded",
                                        timeout=15000)
                        logger.info(f"✅ Перешли по ссылке: {href}")
                        return True
                    try:
                        await el.click(timeout=3000)
                        logger.info(f"✅ Клик по матчу: {text[:80]}")
                        return True
                    except Exception:
                        try:
                            await el.click(timeout=3000, force=True)
                            logger.info(f"✅ Force-клик по матчу: {text[:80]}")
                            return True
                        except Exception:
                            continue

        logger.warning(f"⚠️ Матч {player1} vs {player2} не найден на {page.url}")
        return False

    async def _check_players_on_page(self, page: Page,
                                     player1: str, player2: str) -> bool:
        if not player1 or not player2:
            return False

        def normalize(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r'[_\(\)\.\,\;\:\!\?\-\|]', ' ', s)
            return s

        def tokens(name):
            return [t for t in normalize(name).split() if len(t) >= 2]

        p1 = tokens(player1)
        p2 = tokens(player2)
        if not p1 or not p2:
            return False

        try:
            body = await page.locator("body").inner_text()
        except Exception:
            return False

        body_norm = normalize(body)

        def has_any(tkns):
            return any(t in body_norm for t in tkns)

        return has_any(p1) and has_any(p2)

    async def _get_fonbet_outcome_ids(self, page: Page, match_id: str) -> Optional[Dict]:
        try:
            await page.wait_for_function(
                "window.__INITIAL_STATE__ && window.__INITIAL_STATE__.event",
                timeout=10000,
            )
            result = await page.evaluate("""
                () => {
                    const state = window.__INITIAL_STATE__;
                    if (!state || !state.event) return null;
                    const factors = state.event.customFactors || [];
                    const p1 = factors.find(f => f.f === 921);
                    const p2 = factors.find(f => f.f === 923);
                    return { p1: p1 ? p1.v : null, p2: p2 ? p2.v : null,
                             factor1: 921, factor2: 923 };
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
            logger.error(f"Fonbet: ошибка получения идентификаторов: {e}")
            return None

    # ============================================================
    # Фазы / 100% подтверждённые исходы
    # ============================================================
    def _phase_end(self, sport: str, s1: int, s2: int, set_number: int) \
            -> Tuple[bool, Optional[str]]:
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

    def _is_set_done(self, sport: str, a: int, b: int, set_number: int,
                     fast_phase: int) -> bool:
        if sport in ('basketball', 'cyber_basketball'):
            return fast_phase > set_number
        if sport == 'table_tennis':
            return (a >= 11 and a - b >= 2) or (b >= 11 and b - a >= 2)
        if sport in ('volleyball', 'beach_volleyball'):
            thr = 15 if set_number >= 5 else 25
            return (a >= thr and a - b >= 2) or (b >= thr and b - a >= 2)
        return False

    @staticmethod
    def _parse_phase_num(phase_name: str) -> int:
        if not phase_name:
            return 0
        m = re.search(r'(\d+)', str(phase_name))
        if not m:
            return 0
        try:
            return int(m.group(1))
        except ValueError:
            return 0

    def _resolve_phase_context(self, sport: str, payload: dict,
                                data: dict, set_markets: dict,
                                fast_score: List[int],
                                slow_score: List[int]) -> Tuple[int, int]:
        fast_phase_num = self._parse_phase_num(payload.get('fast_phase', ''))

        if not fast_phase_num and sport in PHASE_END_BY_SCORE:
            fast_phase_num = fast_score[0] + fast_score[1] + 1

        slow_phase_num = int(data.get('phase_num') or 0)

        if not slow_phase_num:
            if sport in PHASE_END_BY_SCORE:
                slow_phase_num = slow_score[0] + slow_score[1] + 1
            else:
                nums = []
                for k in set_markets.keys():
                    if isinstance(k, str) and k.startswith('set_'):
                        try:
                            nums.append(int(k.split('_', 1)[1]))
                        except Exception:
                            pass
                slow_phase_num = max(nums) if nums else 1

        return fast_phase_num, slow_phase_num

    def _max_phase_total(self, sport: str, set_number: int) -> int:
        if sport == 'table_tennis':
            return 40
        if sport in ('volleyball', 'beach_volleyball'):
            return 32 if set_number >= 5 else 52
        if sport in ('basketball', 'cyber_basketball'):
            return 80
        return 100

    def _is_phase_line_sane(self, sport: str, market: str,
                            line: float, set_number: int) -> bool:
        if line is None:
            return False
        try:
            abs_line = abs(float(line))
        except (ValueError, TypeError):
            return False

        if market == 'total':
            max_line = self._max_phase_total(sport, set_number)
        elif market in ('handicap', 'it'):
            max_line = self._max_phase_total(sport, set_number) // 2
        else:
            return True

        if abs_line > max_line:
            logger.debug(
                f"[sanity] {sport} {market} line={abs_line} > {max_line} "
                f"(set {set_number}) — не партийная, пропуск"
            )
            return False
        return True

    def _verify_delay_still_open(self, data: dict, payload: dict, strategy: dict,
                                 fast_state: Optional[dict]) -> Tuple[bool, str]:
        sport = payload.get('sport', 'table_tennis')

        if not fast_state:
            return False, "нет live fast_state"

        fast_sub1 = int(fast_state.get("sub_score1") or 0)
        fast_sub2 = int(fast_state.get("sub_score2") or 0)
        slow_sub1 = int(data.get("sub_score1") or 0)
        slow_sub2 = int(data.get("sub_score2") or 0)

        if sport not in PHASE_END_BY_SCORE and sport not in PHASE_END_BY_TIME:
            return False, f"неизвестный sport={sport}"

        diff = max(fast_sub1 - slow_sub1, fast_sub2 - slow_sub2)
        min_t = self._get_min_threshold(strategy)

        if diff >= min_t:
            return True, f"sub fast-slow: +{diff} (min={min_t})"
        return False, f"sub diff={diff} < {min_t}"

    # ============================================================
    # Подтверждённые исходы (послегол)
    # ============================================================
    def _collect_confirmed_bets(self, sport, payload, markets, set_number,
                                 fast_sub, fast_score, slow_score):
        confirmed = []
        a = fast_sub[0] if fast_sub else 0
        b = fast_sub[1] if fast_sub else 0

        fast_set_done = self._is_set_done(sport, a, b, set_number, set_number)

        fast_total = a + b
        total = markets.get('total', {}) or {}
        line = total.get('line', 0) or 0
        it_markets = markets.get('it', {}) or {}

        total_sane = line > 0 and self._is_phase_line_sane(
            sport, 'total', line, set_number
        )

        # ── 1) РЕЖИМ "ПАРТИЯ ИДЁТ": OVER тотала, ИТ OVER ──
        odd_over = total.get('over', 0)
        if odd_over > 0 and total_sane and fast_total > line:
            confirmed.append((
                'total', 'over', odd_over,
                f'set {set_number}: {fast_total} > {line} (100%)',
                set_number,
            ))

        for player in ('1', '2'):
            p_market = it_markets.get(player, {}) or {}
            if not p_market:
                continue
            line_it = p_market.get('line', 0) or 0
            odd_it_over = p_market.get('over', 0)
            ind = a if player == '1' else b
            if (odd_it_over > 0 and line_it > 0
                    and self._is_phase_line_sane(sport, 'it', line_it, set_number)
                    and ind > line_it):
                confirmed.append((
                    'it', f'{player}_over', odd_it_over,
                    f'set {set_number}: ИТ{player} {ind} > {line_it} (100%)',
                    set_number,
                ))

        # ── 2) Партия ещё идёт → стоп ──
        if not fast_set_done:
            return confirmed

        # ── 3) ПАРТИЯ ЗАВЕРШЕНА: under, winner, фора ──
        if total_sane and fast_total < line:
            odd_under = total.get('under', 0)
            if odd_under > 0:
                confirmed.append((
                    'total', 'under', odd_under,
                    f'set {set_number} done: {fast_total} < {line} (100%)',
                    set_number,
                ))

        for player in ('1', '2'):
            p_market = it_markets.get(player, {}) or {}
            if not p_market:
                continue
            line_it = p_market.get('line', 0) or 0
            odd_it_under = p_market.get('under', 0)
            ind = a if player == '1' else b
            if (odd_it_under > 0 and line_it > 0
                    and self._is_phase_line_sane(sport, 'it', line_it, set_number)
                    and ind < line_it):
                confirmed.append((
                    'it', f'{player}_under', odd_it_under,
                    f'set {set_number} done: ИТ{player} {ind} < {line_it} (100%)',
                    set_number,
                ))

        winner_side = None
        if a > b:
            winner_side = '1'
        elif b > a:
            winner_side = '2'
        if winner_side:
            w = markets.get('winner', {}) or {}
            odd = w.get(winner_side, 0)
            if odd > 0:
                confirmed.append((
                    'winner', winner_side, odd,
                    f'set {set_number} done {a}:{b} (100%)',
                    set_number,
                ))

        margin1 = a - b
        margin2 = b - a
        h = markets.get('handicap', {}) or {}
        for side, margin in (('1', margin1), ('2', margin2)):
            h_side = h.get(side, {}) or {}
            if not h_side:
                continue
            line_h = h_side.get('line', 0) or 0
            odd_h = h_side.get('odd', 0)
            if (odd_h > 0
                    and self._is_phase_line_sane(sport, 'handicap', line_h, set_number)
                    and (margin + line_h) > 0):
                confirmed.append((
                    'handicap', side, odd_h,
                    f'set {set_number} done: П{side} margin '
                    f'{margin} + line {line_h} (100%)',
                    set_number,
                ))

        return confirmed

    def _collect_prev_phase_bets(self, sport, payload, set_markets,
                                  fast_prev_sub: List[int], prev_phase_name: str):
        if not prev_phase_name:
            return []
        m = re.search(r'(\d+)', prev_phase_name)
        if not m:
            return []
        prev_num = int(m.group(1))

        prev_s1 = fast_prev_sub[0] if fast_prev_sub else 0
        prev_s2 = fast_prev_sub[1] if fast_prev_sub else 0
        if prev_s1 == 0 and prev_s2 == 0:
            return []

        set_key = f"set_{prev_num}"
        markets = set_markets.get(set_key, {})
        if not markets:
            return []

        confirmed = []
        prev_total = prev_s1 + prev_s2

        total = markets.get('total', {}) or {}
        line = total.get('line', 0) or 0
        if line > 0 and self._is_phase_line_sane(sport, 'total', line, prev_num):
            if prev_total > line:
                odd = total.get('over', 0)
                if odd > 0:
                    confirmed.append((
                        'total', 'over', odd,
                        f'prev {prev_phase_name}: {prev_total} > {line}',
                        prev_num,
                    ))
            elif prev_total < line:
                odd = total.get('under', 0)
                if odd > 0:
                    confirmed.append((
                        'total', 'under', odd,
                        f'prev {prev_phase_name}: {prev_total} < {line}',
                        prev_num,
                    ))

        it_markets = markets.get('it', {}) or {}
        for player in ('1', '2'):
            p_market = it_markets.get(player, {}) or {}
            if not p_market:
                continue
            line_it = p_market.get('line', 0) or 0
            ind = prev_s1 if player == '1' else prev_s2
            if line_it <= 0:
                continue
            if not self._is_phase_line_sane(sport, 'it', line_it, prev_num):
                continue
            if ind > line_it:
                odd = p_market.get('over', 0)
                if odd > 0:
                    confirmed.append((
                        'it', f'{player}_over', odd,
                        f'prev ИТ{player} {ind} > {line_it}',
                        prev_num,
                    ))
            elif ind < line_it:
                odd = p_market.get('under', 0)
                if odd > 0:
                    confirmed.append((
                        'it', f'{player}_under', odd,
                        f'prev ИТ{player} {ind} < {line_it}',
                        prev_num,
                    ))

        winner_side = None
        if prev_s1 > prev_s2:
            winner_side = '1'
        elif prev_s2 > prev_s1:
            winner_side = '2'

        if winner_side:
            w = markets.get('winner', {}) or {}
            odd = w.get(winner_side, 0)
            if odd > 0:
                confirmed.append((
                    'winner', winner_side, odd,
                    f'prev {prev_phase_name} {prev_s1}:{prev_s2}',
                    prev_num,
                ))

        margin1 = prev_s1 - prev_s2
        margin2 = prev_s2 - prev_s1
        h = markets.get('handicap', {}) or {}
        for side, margin in (('1', margin1), ('2', margin2)):
            h_side = h.get(side, {}) or {}
            if not h_side:
                continue
            line_h = h_side.get('line', 0) or 0
            odd_h = h_side.get('odd', 0)
            if (odd_h > 0
                    and self._is_phase_line_sane(sport, 'handicap', line_h, prev_num)
                    and (margin + line_h) > 0):
                confirmed.append((
                    'handicap', side, odd_h,
                    f'prev П{side} margin {margin} + line {line_h}',
                    prev_num,
                ))

        return confirmed

    def _filter_confirmed_by_strategy(self, confirmed, strategy, payload):
        """
        Фильтрация подтверждённых исходов по настройкам стратегии.

        market_mode='manual' — оставляем только рынки, отмеченные в manual_markets.
        market_mode='auto'   — не фильтруем здесь (пороги и odds уже отфильтрованы).

        Legacy-поля (markets_enabled, bet_direction, winner_sides и т.п.)
        больше не используются — они удалены из стратегии.
        """
        mode = strategy.get('market_mode', 'auto')
        if mode != 'manual':
            return confirmed

        manual = set(strategy.get('manual_markets') or [])
        if not manual:
            return confirmed

        result = []
        for c in confirmed:
            market, side, odd, reason, set_number = c
            if self._is_market_allowed(market, side, manual):
                result.append(c)

        if not result:
            logger.debug(
                f"_filter_confirmed_by_strategy: все исходы отсеяны "
                f"manual_markets={sorted(manual)}"
            )
        return result

    def _choose_best_bet(self, data, strategy, payload,
                         fast_sub: List[int],
                         fast_prev_sub: List[int],
                         prev_phase_name: str,
                         fast_score: List[int],
                         slow_score: List[int]):
        set_markets = data.get('set_markets', {})
        race_set_markets = data.get('race_set_markets') or {}
        race_enabled = bool(strategy.get('race_enabled', False))

        if not set_markets and not (race_enabled and race_set_markets):
            return None

        sport = payload.get('sport', 'table_tennis')

        fast_phase_num, slow_phase_num = self._resolve_phase_context(
            sport, payload, data, set_markets, fast_score, slow_score
        )
        if fast_phase_num <= 0 or slow_phase_num <= 0:
            logger.debug(
                f"best_bet: не удалось определить фазы "
                f"fast={fast_phase_num} slow={slow_phase_num}"
            )
            return None

        confirmed = []

        if fast_phase_num == slow_phase_num:
            set_key = f"set_{slow_phase_num}"
            current_markets = set_markets.get(set_key, {})

            logger.info(
                f"best_bet: фазы совпадают, играем set_{slow_phase_num}"
            )

            if current_markets:
                confirmed += self._collect_confirmed_bets(
                    sport, payload, current_markets, slow_phase_num,
                    fast_sub, fast_score, slow_score
                )

            if race_enabled:
                race_markets = race_set_markets.get(set_key, {})
                if race_markets:
                    race_confirmed_raw = self._collect_confirmed_bets(
                        sport, payload, race_markets, slow_phase_num,
                        fast_sub, fast_score, slow_score
                    )
                    for (m, s, o, r, sn) in race_confirmed_raw:
                        confirmed.append((
                            f"race_{m}", s, o,
                            f"[RACE] {r}",
                            sn,
                        ))
                    logger.info(
                        f"best_bet: RACE — собрано {len(race_confirmed_raw)} "
                        f"исходов в {set_key}"
                    )

        elif fast_phase_num == slow_phase_num + 1:
            set_key = f"set_{slow_phase_num}"
            if set_key not in set_markets:
                logger.debug(f"best_bet: нет рынков {set_key} для prev")
                return None

            logger.info(
                f"best_bet: fast на партию впереди "
                f"({fast_phase_num} vs {slow_phase_num}), играем prev "
                f"set_{slow_phase_num}"
            )

            prev_num_from_name = self._parse_phase_num(prev_phase_name)
            if prev_num_from_name != slow_phase_num:
                logger.debug(
                    f"best_bet: prev_phase_name={prev_phase_name!r} "
                    f"не совпадает с slow_phase_num={slow_phase_num}, "
                    f"используем номер из set_markets"
                )
                prev_phase_name = f"{slow_phase_num}-я партия"

            confirmed += self._collect_prev_phase_bets(
                sport, payload, set_markets, fast_prev_sub, prev_phase_name
            )

        else:
            logger.info(
                f"best_bet: расхождение фаз "
                f"(fast={fast_phase_num}, slow={slow_phase_num}) — пропуск"
            )
            return None

        if not confirmed:
            return None

        # ── Фильтр по порогу рынка ──
        slow_s1 = int(data.get("sub_score1") or 0)
        slow_s2 = int(data.get("sub_score2") or 0)
        fast_s1 = fast_sub[0] if fast_sub else 0
        fast_s2 = fast_sub[1] if fast_sub else 0
        diff = max(fast_s1 - slow_s1, fast_s2 - slow_s2)

        confirmed_filtered = []
        for c in confirmed:
            market = c[0]
            base_market = market.replace("race_", "", 1) if market.startswith("race_") else market
            threshold = self._get_market_threshold(strategy, base_market)
            if diff >= threshold:
                confirmed_filtered.append(c)

        if not confirmed_filtered:
            logger.debug(
                f"best_bet: все исходы отсеяны порогами (diff={diff})"
            )
            return None

        confirmed = confirmed_filtered

        # ── Фильтр по manual_markets / auto_criterion ──
        confirmed = self._filter_confirmed_by_strategy(confirmed, strategy, payload)
        if not confirmed:
            return None

        min_odds = strategy.get('min_odds', 1.0)
        max_odds = strategy.get('max_odds', 5.0)
        valid = [c for c in confirmed if min_odds <= c[2] <= max_odds]
        if not valid:
            return None

        PRIORITY = {'winner': 3, 'total': 2, 'handicap': 1, 'it': 0}

        def _priority(market_name: str) -> float:
            base = market_name.replace("race_", "", 1) if market_name.startswith("race_") else market_name
            race_penalty = 0.5 if market_name.startswith("race_") else 0
            return PRIORITY.get(base, 0) - race_penalty

        criterion = strategy.get('auto_criterion', 'reliable')
        if criterion == 'max_odds':
            # Самый высокий кэф; при равенстве — приоритет рынка
            valid.sort(key=lambda c: (c[2], _priority(c[0])), reverse=True)
        else:
            # 'reliable' и 'all_confirmed' (пока fallback) — приоритет рынка
            valid.sort(key=lambda c: (_priority(c[0]), c[2]), reverse=True)

        best = valid[0]
        market, side, odd, reason, set_number = best

        result = {
            'market': market,
            'side': side,
            'odd': odd,
            'set_number': set_number,
            'reason': reason,
        }
        if market.startswith("race_"):
            mk = race_set_markets.get(f"set_{set_number}", {})
        else:
            mk = set_markets.get(f"set_{set_number}", {})

        base_market = market.replace("race_", "", 1) if market.startswith("race_") else market
        if base_market == 'total':
            result['line'] = mk.get('total', {}).get('line', 0)
        elif base_market == 'handicap':
            result['line'] = mk.get('handicap', {}).get(side, {}).get('line', 0)
        elif base_market == 'it':
            parts = side.split('_', 1)
            if len(parts) == 2:
                player, dirn = parts
                result['player'] = player
                result['direction'] = dirn
                it_m = mk.get('it', {}).get(player, {})
                result['line'] = it_m.get('line', 0)
        return result

    # ============================================================
    # Монитор
    # ============================================================
    async def _monitor_loop(self, match_id: str, payload: dict, strategy: dict,
                            handler: BookmakerHandler):
        page = self._pages.get(match_id)
        if not page:
            logger.error(f"❌ MONITOR START: нет страницы для {match_id}")
            await self._cleanup_monitoring(match_id)
            return

        profile_id = strategy.get('profile_id')
        fast_bk = _normalize_bk(payload.get('fast_bk'))
        slow_bk = _normalize_bk(payload.get('slow_bk'))

        logger.info(
            f"▶️ MONITOR START match={match_id} profile={profile_id} "
            f"bk={slow_bk} fast_bk={fast_bk} url={page.url[:100]}"
        )

        verify_seconds = strategy.get('verify_seconds', 3.0)
        max_bets = strategy.get('max_bets_per_match', 1)
        max_bets_per_phase = strategy.get('max_bets_per_phase', 1)
        signal_time = self._monitoring_signal_time.get(match_id, time.time())
        event = asyncio.Event()

        on_update_count = 0

        async def on_update(data: dict):
            nonlocal on_update_count
            on_update_count += 1

            if not self._monitoring_active.get(match_id, False):
                logger.info(f"⏹ on_update: мониторинг {match_id} неактивен")
                return

            data_mid = data.get('match_id')
            if data_mid is not None and str(data_mid) != str(match_id):
                logger.info(f"⏭ on_update: чужой match_id {data_mid} ≠ {match_id}")
                return

            self._last_update_time[match_id] = time.time()
            self._last_slow_data[match_id] = data

            fast_state = self._get_fast_state(match_id, fast_bk)

            if fast_state:
                fast_sub = [int(fast_state.get("sub_score1") or 0),
                            int(fast_state.get("sub_score2") or 0)]
                fast_score = [int(fast_state.get("score1") or 0),
                              int(fast_state.get("score2") or 0)]
            else:
                fast_sub = [0, 0]
                fast_score = [0, 0]

            slow_sub = [data.get("sub_score1", 0) or 0,
                        data.get("sub_score2", 0) or 0]
            slow_score = [data.get("score1", 0) or 0,
                          data.get("score2", 0) or 0]

            logger.info(
                f"🎬 on_update #{on_update_count} match={match_id} "
                f"slow_bk={slow_bk} fast_bk={fast_bk} "
                f"fast_score={fast_score} slow_score={slow_score} "
                f"fast_sub={fast_sub} slow_sub={slow_sub} "
                f"fast_available={bool(fast_state)} "
                f"fast_phase={payload.get('fast_phase')!r} "
                f"fast_prev_phase={payload.get('fast_prev_phase')!r} "
                f"slow_phase_num={data.get('phase_num')} "
                f"set_markets_keys={list((data.get('set_markets') or {}).keys())}"
            )

            if self._bets_by_match.get(match_id, 0) >= max_bets:
                logger.info(f"✅ on_update: лимит ставок исчерпан ({max_bets})")
                event.set()
                return

            elapsed = time.time() - signal_time
            if elapsed < verify_seconds:
                logger.info(
                    f"⏸ on_update: verify_seconds ({elapsed:.1f} из {verify_seconds})"
                )
                return

            current_payload = self._monitoring_payloads.get(match_id, payload)
            current_strategy = self._monitoring_strategies.get(match_id, strategy)

            is_open, reason = self._verify_delay_still_open(
                data, current_payload, current_strategy, fast_state
            )
            logger.info(f"🔍 delay_still_open={is_open} ({reason})")

            teams_ui = current_payload.get('match_teams', ['?', '?'])
            prev_ui = self._last_ui_state.get(match_id)
            cur_ui = (tuple(fast_sub), tuple(slow_sub), is_open)
            if prev_ui != cur_ui:
                self._last_ui_state[match_id] = cur_ui
                if is_open:
                    log_bus.match_event(
                        bk=slow_bk, match_id=match_id, teams=teams_ui,
                        message=f"✅ Задержка подтверждена: fast "
                                f"{fast_sub[0]}:{fast_sub[1]} / slow "
                                f"{slow_sub[0]}:{slow_sub[1]} · {reason}",
                        level="success",
                    )
                else:
                    log_bus.match_event(
                        bk=slow_bk, match_id=match_id, teams=teams_ui,
                        message=f"⏸ Задержки нет: fast {fast_sub[0]}:{fast_sub[1]} "
                                f"/ slow {slow_sub[0]}:{slow_sub[1]} · {reason}",
                        level="info",
                        level3=True,
                    )

            if not is_open:
                return

            prev_phase_name = (current_payload.get('fast_prev_phase') or '').strip()
            fast_prev_sub = current_payload.get('fast_prev_sub_score') or [0, 0]

            best_bet = self._choose_best_bet(
                data, current_strategy, current_payload,
                fast_sub, fast_prev_sub, prev_phase_name,
                fast_score, slow_score
            )
            logger.info(f"🎯 best_bet={best_bet}")
            if not best_bet:
                return

            log_bus.match_event(
                bk=slow_bk, match_id=match_id, teams=teams_ui,
                message=f"🎯 Выбор: {best_bet['market']} {best_bet['side']} "
                        f"@ {best_bet['odd']:.2f} · фаза {best_bet['set_number']} "
                        f"· {best_bet.get('reason', '')}",
                level="success",
            )

            phase_key = (match_id, best_bet['set_number'])
            if self._bets_by_phase.get(phase_key, 0) >= max_bets_per_phase:
                logger.info(f"⏭ on_update: лимит ставок в фазе "
                            f"{best_bet['set_number']}")
                return

            set_key = f"set_{best_bet['set_number']}"
            market_name = best_bet['market']
            is_race = market_name.startswith("race_")
            base_market = market_name.replace("race_", "", 1) if is_race else market_name

            if is_race:
                outcome_source = data.get('race_outcome_ids', {}) or {}
            else:
                outcome_source = data.get('outcome_ids', {})

            if base_market == 'it':
                outcome_info = (outcome_source
                                    .get(set_key, {})
                                    .get('it', {})
                                    .get(best_bet.get('player'), {})
                                    .get(best_bet.get('direction')))
            else:
                outcome_info = (outcome_source
                                    .get(set_key, {})
                                    .get(base_market, {})
                                    .get(best_bet['side']))

            logger.info(
                f"🔑 outcome_info={outcome_info} "
                f"(race={is_race}, market={base_market})"
            )
            if not outcome_info:
                logger.warning(f"⚠️ Нет идентификатора: {best_bet}")
                log_bus.match_event(
                    bk=slow_bk, match_id=match_id, teams=teams_ui,
                    message=f"⚠️ Нет идентификатора для исхода "
                            f"{best_bet['market']} {best_bet['side']}",
                    level="warning",
                )
                return

            amount = current_strategy.get('bet_size', 100)
            bet_data = {
                "amount": amount,
                "value": best_bet['odd'],
            }

            bk = slow_bk
            if bk == 'fonbet':
                bet_data['event_id'] = data.get('match_id')
                bet_data['factor_id'] = outcome_info.get('id')
            elif bk == 'pari':
                bet_data['event_id'] = data.get('match_id')
                bet_data['factor_id'] = outcome_info.get('id')
                bet_data['score'] = f"{data.get('score1', 0)}:{data.get('score2', 0)}"
            elif bk == 'sportbet':
                bet_data['outcome_id'] = outcome_info.get('id')
            elif bk == 'betcity':
                bet_data['event_id'] = data.get('match_id')
                bet_data['pos'] = outcome_info.get('pos')
                bet_data['kf'] = best_bet['odd']
                bet_data['lv'] = outcome_info.get('lv') or 0
                bet_data['sport'] = current_payload.get('sport') or 'table_tennis'
                bet_data['set_num'] = best_bet['set_number']
                bet_data['market'] = best_bet['market']
                bet_data['side'] = best_bet['side']
                token = await page.evaluate(
                    "() => document.cookie.match(/tk=([^;]+)/)?.[1] || ''"
                )
                bet_data['token'] = token
            elif bk == 'olimp':
                bet_data['market_data'] = outcome_info.get('market_data')
                bet_data['kf'] = best_bet['odd']
                bet_data['outcome_id'] = outcome_info.get('id')
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
                bet_data['outcome_id'] = outcome_info.get('id')
                bet_data['kf'] = best_bet['odd']
            else:
                logger.error(f"Отправка для БК {bk} не реализована")
                return

            logger.info(f"📤 bet_data={bet_data}")

            await self._enqueue_bet(
                profile_id=profile_id,
                match_id=match_id,
                handler=handler,
                page=page,
                bet_data=bet_data,
                bk=bk,
                amount=amount,
                bet_info=best_bet,
            )

            if self._bets_by_match.get(match_id, 0) >= max_bets:
                event.set()

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
            timeout = MONITOR_MAX_LIFETIME
            await asyncio.wait_for(event.wait(), timeout=timeout)
            logger.info(f"⏹️ MONITOR END match={match_id} причина=event.set() "
                        f"on_updates={on_update_count}")
        except asyncio.TimeoutError:
            logger.info(f"⏰ MONITOR END match={match_id} "
                        f"причина=timeout({MONITOR_MAX_LIFETIME:.0f}s) "
                        f"on_updates={on_update_count}")
        except asyncio.CancelledError:
            logger.info(f"🛑 MONITOR END match={match_id} причина=cancelled "
                        f"on_updates={on_update_count}")
            raise
        finally:
            await self._cleanup_monitoring(match_id)