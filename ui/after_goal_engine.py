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
from core.fast_client import (
    get_fast_client, has_fast_client, shutdown_all as fast_clients_shutdown,
)
from core.fast_config import (
    is_fast_configured, get_fast_profile_id, get_fast_api_key,
)
from .paths import get_app_data_dir
from .after_goal.match_urls import build_match_url
from .config_loader import load_config
from ui.log_bus import log_bus

import requests

ADSPOWER_API_URL = "http://localhost:50325"
AFTER_GOAL_TIMEOUT = 25
MONITOR_MAX_LIFETIME = 40.0
AFTER_GOAL_COOLDOWN = 10

logger = logging.getLogger(__name__)

_aux_logger = logging.getLogger('ui.after_goal_engine.aux')
_aux_logger.setLevel(logging.WARNING)


def _normalize_bk(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def _make_key(match_id: str, profile_id: str) -> str:
    """Составной ключ стейта: match_id + profile_id."""
    return f"{match_id}__{profile_id}"


def _match_id_from_key(key: str) -> str:
    """Достаёт match_id из составного ключа."""
    if "__" in key:
        return key.split("__", 1)[0]
    return key


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
    "olimp":      25.0,
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
        # ── ВАЖНО: все state-словари (кроме _profile_active_matches,
        # _profile_locks, _profile_launch_attempts, _bet_queues,
        # _queue_workers) ключуются СОСТАВНЫМ ключом
        # f"{match_id}__{profile_id}", чтобы один и тот же матч
        # мог параллельно мониториться несколькими стратегиями/профилями.
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
        self._fast_markets_poll_tasks: Dict[str, asyncio.Task] = {}

        # ── Fast-профиль AdsPower (для БК без HTTP-клиента: LigaStavok, Zenit) ──
        self._fast_browser: Optional[AdsPowerBrowser] = None
        self._fast_profile_lock = asyncio.Lock()
        self._fast_pages: Dict[str, Page] = {}
        self._fast_handlers: Dict[str, BookmakerHandler] = {}
        self._fast_monitor_tasks: Dict[str, asyncio.Task] = {}

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
            for k, strat in self._monitoring_strategies.items():
                if self._monitoring_profiles.get(k) == profile_id:
                    bk = _normalize_bk(strat.get('bk'))
                    if bk:
                        bks_to_clear.add(bk)
        except Exception:
            pass

        keys_to_cleanup = [
            k for k, pid in self._monitoring_profiles.items()
            if pid == profile_id
        ]
        for k in keys_to_cleanup:
            try:
                await self._cleanup_monitoring(k)
            except Exception as e:
                logger.warning(f"cleanup {k}: {e}")

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
    async def _poll_fast_state(self, key: str, match_id: str, sport: str,
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
            f"🔄 FAST_STATE poll запущен key={key} "
            f"fast_bk={fast_bk} slow_bk={slow_bk} url={url}"
        )

        fail_count = 0
        while self._monitoring_active.get(key, False):
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

                        prev_entry = self._fast_state.get(key) or {}
                        prev_keys = prev_entry.get("_last_logged_keys")

                        state = self._fast_state.setdefault(key, {})
                        state["bks"] = bks
                        state["fetched_at"] = time.time()
                        state["now_on_backend"] = data.get("now")
                        state["_last_logged_keys"] = prev_keys
                        state.pop("not_found", None)

                        if prev_keys != bks_keys:
                            state["_last_logged_keys"] = bks_keys
                            logger.info(
                                f"📡 FAST_STATE[{key}] bks={bks_keys} "
                                f"fast_present={fast_bk in bks}"
                            )
                        else:
                            logger.debug(
                                f"📡 FAST_STATE[{key}] без изменений "
                                f"({len(bks_keys)} БК)"
                            )
                    else:
                        was_not_found = (self._fast_state
                                         .get(key, {})
                                         .get("not_found", False))
                        state = self._fast_state.setdefault(key, {})
                        state["bks"] = {}
                        state["fetched_at"] = time.time()
                        state["now_on_backend"] = data.get("now")
                        state["not_found"] = True
                        if not was_not_found:
                            logger.warning(
                                f"📡 FAST_STATE[{key}] матч пропал из агрегатора"
                            )
                        else:
                            logger.debug(
                                f"📡 FAST_STATE[{key}] всё ещё не найден"
                            )
                else:
                    fail_count += 1
                    if fail_count == 3:
                        logger.warning(
                            f"⚠️ FAST_STATE[{key}] HTTP {resp.status_code}"
                        )
            except Exception as e:
                fail_count += 1
                if fail_count <= 3:
                    logger.debug(f"FAST_STATE[{key}] poll error: {e}")

            await asyncio.sleep(FAST_STATE_POLL_INTERVAL)

        logger.info(f"🛑 FAST_STATE poll остановлен key={key}")

    async def _poll_fast_markets(self, key: str, match_id: str, sport: str,
                                 player1: str, player2: str,
                                 fast_bk: str):
        """
        Каждые 2 сек дёргает fast-клиент и кладёт внутриматчевые
        рынки в _fast_state[key]['markets'].

        Работает параллельно с _poll_fast_state (счёт) — не мешает ему.
        """
        if not fast_bk:
            logger.info(f"🛑 FAST_MARKETS poll: fast_bk пустой, стоп")
            return

        client = get_fast_client(fast_bk)
        if client is None:
            logger.warning(
                f"⚠️ FAST_MARKETS: нет клиента для {fast_bk}, "
                f"останавливаю polling"
            )
            return

        logger.info(
            f"🔄 FAST_MARKETS poll запущен key={key} "
            f"fast_bk={fast_bk} teams='{player1}' / '{player2}'"
        )

        fail_count = 0
        while self._monitoring_active.get(key, False):
            try:
                # ── Если у клиента есть метод get_markets_by_native_id ──
                # (Pari — ему нужен eventId от Pari из /match_state,
                #  а не имена игроков), сначала пробуем его.
                result = None
                if hasattr(client, "get_markets_by_native_id"):
                    state = self._fast_state.get(key) or {}
                    bks = state.get("bks") or {}
                    bk_info = bks.get(fast_bk) or {}
                    native_id = bk_info.get("match_id")
                    if native_id:
                        result = await client.get_markets_by_native_id(
                            native_id
                        )

                # ── Fallback: старый путь по именам ──
                if result is None:
                    result = await client.get_markets(sport, player1, player2)

                if result:
                    fail_count = 0

                    # Мержим в _fast_state, не перетирая данные счёта
                    state = self._fast_state.setdefault(key, {})

                    # ── НЕ перетираем ненулевые sub/score нулями ──
                    new_sub1 = int(result.get("sub_score1", 0) or 0)
                    new_sub2 = int(result.get("sub_score2", 0) or 0)
                    old_sub1 = int(state.get("markets_sub1", 0) or 0)
                    old_sub2 = int(state.get("markets_sub2", 0) or 0)
                    if (new_sub1 or new_sub2) or not (old_sub1 or old_sub2):
                        state["markets_sub1"] = new_sub1
                        state["markets_sub2"] = new_sub2

                    new_score1 = int(result.get("score1", 0) or 0)
                    new_score2 = int(result.get("score2", 0) or 0)
                    old_score1 = int(state.get("markets_score1", 0) or 0)
                    old_score2 = int(state.get("markets_score2", 0) or 0)
                    if (new_score1 or new_score2) or not (old_score1 or old_score2):
                        state["markets_score1"] = new_score1
                        state["markets_score2"] = new_score2

                    state["markets"] = result.get("set_markets", {})
                    state["markets_phase"] = result.get("phase_num", 0)
                    state["markets_match_id"] = result.get("match_id")
                    state["markets_fetched_at"] = time.time()

                    logger.debug(
                        f"📡 FAST_MARKETS[{key}] {fast_bk} "
                        f"phase={result.get('phase_num')} "
                        f"sets={list(result.get('set_markets', {}).keys())}"
                    )
                else:
                    fail_count += 1
                    if fail_count == 5:
                        logger.warning(
                            f"⚠️ FAST_MARKETS[{key}] {fast_bk}: "
                            f"5 промахов подряд — матч не находится"
                        )

            except Exception as e:
                fail_count += 1
                if fail_count <= 3:
                    logger.debug(
                        f"FAST_MARKETS[{key}] poll error: {e}"
                    )

            await asyncio.sleep(1)

        logger.info(f"🛑 FAST_MARKETS poll остановлен key={key}")

    def _get_fast_markets(self, key: str) -> Optional[Dict]:
        """
        Возвращает markets из _fast_state, если свежие (< 5 сек).
        Иначе None.
        """
        entry = self._fast_state.get(key)
        if not entry:
            return None
        fetched_at = entry.get("markets_fetched_at", 0)
        if not fetched_at:
            return None
        if time.time() - fetched_at > 5.0:
            return None
        return {
            "set_markets": entry.get("markets") or {},
            "phase_num":   entry.get("markets_phase", 0),
            "score1":      entry.get("markets_score1", 0),
            "score2":      entry.get("markets_score2", 0),
            "sub_score1":  entry.get("markets_sub1", 0),
            "sub_score2":  entry.get("markets_sub2", 0),
            "match_id":    entry.get("markets_match_id"),
        }

    def _get_fast_state(self, key: str, fast_bk: str) -> Optional[dict]:
        entry = self._fast_state.get(key)
        if not entry:
            return None
        age = time.time() - entry.get("fetched_at", 0)
        if age > FAST_STATE_STALE_SEC:
            return None
        bks = entry.get("bks") or {}
        return bks.get(fast_bk)

    def _get_fast_sub_for_after_goal(self, key: str, payload: dict):
        """
        Возвращает (sub1, sub2, phase_num, source) для after-goal логики.

        Приоритет:
          1) fast-клиент (_get_fast_markets)      — свежие, ~100-500 мс
          2) бэкенд (_get_fast_state)             — отстаёт на 0.5-2 сек
          3) payload.fast_sub_score               — snapshot на момент сигнала
        """
        # 1) fast-клиент
        fast_entry = self._get_fast_markets(key)
        if fast_entry:
            f_s1 = int(fast_entry.get("sub_score1", 0) or 0)
            f_s2 = int(fast_entry.get("sub_score2", 0) or 0)
            if f_s1 or f_s2:
                return (
                    f_s1, f_s2,
                    int(fast_entry.get("phase_num", 0) or 0),
                    "fast_client",
                )

        # 2) бэкенд
        fast_bk = _normalize_bk(payload.get('fast_bk'))
        fast_state = self._get_fast_state(key, fast_bk)
        if fast_state:
            return (
                int(fast_state.get("sub_score1", 0) or 0),
                int(fast_state.get("sub_score2", 0) or 0),
                self._parse_phase_num(payload.get('fast_phase', '')),
                "backend",
            )

        # 3) payload
        ps = payload.get('fast_sub_score') or [0, 0]
        return (
            int(ps[0] or 0) if len(ps) > 0 else 0,
            int(ps[1] or 0) if len(ps) > 1 else 0,
            self._parse_phase_num(payload.get('fast_phase', '')),
            "payload",
        )

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
    def _verify_delay_now(self, key: str, strategy: dict,
                          market: Optional[str] = None) -> Tuple[bool, str]:
        payload = self._monitoring_payloads.get(key)
        if not payload:
            return False, "нет payload"

        slow_data = self._last_slow_data.get(key)
        if not slow_data:
            return False, "нет slow_data"

        slow_age = time.time() - self._last_update_time.get(key, 0)
        if slow_age > SLOW_DATA_STALE_SEC:
            return False, f"slow_data старше {slow_age:.1f}с"

        sport = payload.get('sport', 'table_tennis')

        # ── Fast-счёт: fast-клиент → бэкенд → payload ──
        fast_sub1, fast_sub2, _, _src = \
            self._get_fast_sub_for_after_goal(key, payload)

        # Логируем источник — для отладки и сравнения
        backend_bk = _normalize_bk(payload.get('fast_bk'))
        backend_s = self._get_fast_state(key, backend_bk)
        if backend_s:
            b_s1 = int(backend_s.get("sub_score1") or 0)
            b_s2 = int(backend_s.get("sub_score2") or 0)
            if (b_s1 != fast_sub1 or b_s2 != fast_sub2):
                logger.info(
                    f"📊 FAST_SRC key={key}: "
                    f"used={_src} {fast_sub1}:{fast_sub2} "
                    f"| backend {b_s1}:{b_s2} "
                    f"| diff={fast_sub1-b_s1:+d}:{fast_sub2-b_s2:+d}"
                )

        slow_sub1 = int(slow_data.get("sub_score1") or 0)
        slow_sub2 = int(slow_data.get("sub_score2") or 0)

        # Счёт партий берём из бэкенда (он ок для определения фазы)
        fast_score1 = 0
        fast_score2 = 0
        if backend_s := self._get_fast_state(key, _normalize_bk(payload.get('fast_bk'))):
            fast_score1 = int(backend_s.get("score1") or 0)
            fast_score2 = int(backend_s.get("score2") or 0)
        slow_score1 = int(slow_data.get("score1") or 0)
        slow_score2 = int(slow_data.get("score2") or 0)

        # ── Определяем фазу ──
        if sport in PHASE_END_BY_SCORE:
            fast_phase_num = fast_score1 + fast_score2 + 1
            slow_phase_num = slow_score1 + slow_score2 + 1
        else:
            fast_phase_num = self._parse_phase_num(
                payload.get('fast_phase', '')
            )
            slow_phase_num = int(slow_data.get('phase_num') or 0)

        # ── Случай 1: fast на партию впереди (валидный after-goal) ──
        if (fast_phase_num > 0 and slow_phase_num > 0
                and fast_phase_num == slow_phase_num + 1):
            logger.debug(
                f"verify_delay_now: fast на партию впереди "
                f"(fast={fast_phase_num}, slow={slow_phase_num}) — delay OK"
            )
            return True, (
                f"fast на партию впереди "
                f"(fast {fast_score1}:{fast_score2}, "
                f"slow {slow_score1}:{slow_score2})"
            )

        # ── Случай 2: рассинхрон фаз — не сравниваем sub_score ──
        if (fast_phase_num > 0 and slow_phase_num > 0
                and fast_phase_num != slow_phase_num):
            return False, (
                f"фазы рассинхронизированы "
                f"(fast={fast_phase_num}, slow={slow_phase_num}) — "
                f"sub_score несравним"
            )

        # ── Случай 3: обе в одной фазе — сравниваем sub_score ──
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

    def _verify_live_value_now(self, key: str,
                                strategy: dict) -> Tuple[bool, str]:
        """
        Проверка для live-value перед ставкой.
        """
        payload = self._monitoring_payloads.get(key)
        if not payload:
            return False, "нет payload"

        # 1) Свежие fast_markets
        fast_entry = self._get_fast_markets(key)
        if not fast_entry:
            return False, "fast_markets протухли (>5с)"

        # 2) Свежий slow_data
        slow_data = self._last_slow_data.get(key)
        if not slow_data:
            return False, "нет slow_data"

        slow_age = time.time() - self._last_update_time.get(key, 0)
        if slow_age > SLOW_DATA_STALE_SEC:
            return False, f"slow_data старше {slow_age:.1f}с"

        # 3) Пересчитываем edge на свежих данных
        try:
            fresh_best = self._choose_live_value_bet(
                key, slow_data, strategy, payload
            )
        except Exception as e:
            logger.debug(f"_verify_live_value_now: recalc: {e}")
            return False, f"ошибка пересчёта: {e}"

        if not fresh_best:
            return False, "edge пропал (кэфы сошлись)"

        return True, (
            f"edge +{fresh_best['edge']*100:.1f}% "
            f"({fresh_best['market']} {fresh_best['side']}, "
            f"slow {fresh_best['odd']:.2f} / fast {fresh_best['fast_odd']:.2f})"
        )

    # ============================================================
    # REFRESH outcome_id для retry
    # ============================================================
    def _refresh_outcome_for_retry(self, key: str,
                                   bet_info: dict) -> Optional[dict]:
        payload = self._monitoring_payloads.get(key)
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
        profile_id = strategy.get('profile_id')

        if not match_id:
            logger.warning("Нет match_id в payload")
            return
        if not profile_id:
            logger.warning("Нет profile_id в strategy — пропускаем преопен")
            return

        key = _make_key(match_id, profile_id)

        if key in self._preopen_in_flight:
            logger.debug(f"Преopen {key} уже выполняется — пропускаем дубль")
            return
        self._preopen_in_flight.add(key)
        try:
            await self._preopen_impl(payload, strategy, key)
        finally:
            self._preopen_in_flight.discard(key)

    # ============================================================
    # Fast-профиль (вкладка) для БК без HTTP-клиента
    # ============================================================
    async def _ensure_fast_browser(self) -> Optional[AdsPowerBrowser]:
        """Запускает fast-профиль AdsPower (из fast_config.json)."""
        if not is_fast_configured():
            logger.warning("[FAST-TAB] fast-профиль не настроен")
            return None

        profile_id = get_fast_profile_id()
        api_key = get_fast_api_key()

        async with self._fast_profile_lock:
            if self._fast_browser is not None:
                try:
                    if (self._fast_browser.browser
                            and self._fast_browser.browser.is_connected()):
                        return self._fast_browser
                except Exception:
                    pass
                try:
                    await self._fast_browser.stop()
                except Exception:
                    pass
                self._fast_browser = None

            try:
                wrapper = AdsPowerBrowser(
                    profile_id=profile_id,
                    api_key=api_key,
                    api_url=ADSPOWER_API_URL,
                    headless=True,
                )
                await wrapper.__aenter__()
                self._fast_browser = wrapper
                logger.info(
                    f"[FAST-TAB] ✅ Fast-профиль {profile_id} запущен (headless)"
                )
                log_bus.success(
                    "Fast БК", f"Fast-профиль {profile_id} запущен"
                )
                return wrapper
            except Exception as e:
                logger.error(
                    f"[FAST-TAB] ❌ Не удалось запустить fast-профиль: {e}",
                    exc_info=True,
                )
                log_bus.error(
                    "Fast БК", f"Fast-профиль не запущен: {e}"
                )
                return None

    async def _open_fast_match(self, key: str, match_id: str, fast_bk: str,
                                payload: dict, sport: str) -> Optional[Page]:
        """
        Открывает вкладку fast БК на конкретном матче в fast-профиле.
        Используется ТОЛЬКО для БК без HTTP-клиента (LigaStavok, Zenit).
        """
        fast_browser = await self._ensure_fast_browser()
        if not fast_browser:
            return None

        existing = self._fast_pages.get(key)
        if existing and not existing.is_closed():
            return existing

        player1, player2 = payload.get('match_teams', ['', ''])

        handler_cls = HANDLERS.get(fast_bk)
        if not handler_cls:
            logger.error(f"[FAST-TAB] нет хендлера для {fast_bk}")
            return None

        try:
            page = await fast_browser.new_page()
        except Exception as e:
            logger.error(f"[FAST-TAB] не удалось открыть вкладку: {e}")
            return None

        handler = handler_cls(
            target_match_id=match_id,
            target_teams=[player1, player2],
        )
        self._fast_handlers[key] = handler

        # prepare_page ДО goto (для LigaStavok — установка fetch-хука)
        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.warning(f"[FAST-TAB] prepare_page ({fast_bk}): {e}")

        # Прогрев cookies Qrator для LigaStavok
        if fast_bk == "ligastavok":
            try:
                cookies = await page.context.cookies()
                has_qrator = any(
                    "qrator" in (c.get("name") or "").lower()
                    for c in cookies
                    if "ligastavok" in (c.get("domain") or "")
                )
                if not has_qrator:
                    logger.info("[FAST-TAB] LigaStavok: прогрев через /live")
                    try:
                        await page.goto(
                            "https://www.ligastavok.ru/live",
                            wait_until="domcontentloaded", timeout=20000,
                        )
                        await page.wait_for_timeout(2500)
                    except Exception as e:
                        logger.warning(f"[FAST-TAB] прогрев: {e}")
            except Exception as e:
                logger.debug(f"[FAST-TAB] cookies check: {e}")

        # Идём на live-раздел
        bk_urls = LIVE_URLS.get(fast_bk, {})
        start_url = bk_urls.get(sport) or bk_urls.get('any')
        if start_url:
            try:
                await page.goto(start_url, wait_until="domcontentloaded",
                                timeout=30000)
                await page.wait_for_timeout(2500)
            except Exception as e:
                logger.warning(f"[FAST-TAB] goto ({fast_bk}): {e}")

        # Пытаемся открыть конкретный матч по URL
        match_url = payload.get('match_url') or build_match_url(fast_bk, payload)
        url_ok = False
        if match_url:
            try:
                await page.goto(match_url, wait_until="domcontentloaded",
                                timeout=25000)
                await page.wait_for_timeout(2000)
                for _ in range(20):
                    if await self._check_players_on_page(page, player1, player2):
                        url_ok = True
                        break
                    await page.wait_for_timeout(300)
            except Exception as e:
                logger.debug(f"[FAST-TAB] URL: {e}")

        if not url_ok:
            found = await self._click_match_on_page(page, player1, player2)
            if not found:
                logger.warning(
                    f"[FAST-TAB] матч '{player1}' vs '{player2}' "
                    f"не найден на {fast_bk}"
                )
                try:
                    await page.close()
                except Exception:
                    pass
                self._fast_handlers.pop(key, None)
                return None

        self._fast_pages[key] = page
        logger.info(f"[FAST-TAB] ✅ вкладка открыта: key={key} bk={fast_bk}")
        return page

    async def _monitor_fast_markets_via_handler(
        self, key: str, match_id: str, fast_bk: str,
        payload: dict, strategy: dict,
    ):
        """
        Хендлер fast БК слушает матч и пишет set_markets
        в _fast_state[key] в том же формате, что HTTP-клиент.
        """
        page = self._fast_pages.get(key)
        handler = self._fast_handlers.get(key)
        if not page or not handler:
            return

        fast_bk_norm = _normalize_bk(fast_bk)

        async def on_update(data: dict):
            if not self._monitoring_active.get(key, False):
                return
            state = self._fast_state.setdefault(key, {})

            new_sub1 = int(data.get('sub_score1', 0) or 0)
            new_sub2 = int(data.get('sub_score2', 0) or 0)
            old_sub1 = int(state.get("markets_sub1", 0) or 0)
            old_sub2 = int(state.get("markets_sub2", 0) or 0)
            if (new_sub1 or new_sub2) or not (old_sub1 or old_sub2):
                state["markets_sub1"] = new_sub1
                state["markets_sub2"] = new_sub2

            new_score1 = int(data.get('score1', 0) or 0)
            new_score2 = int(data.get('score2', 0) or 0)
            old_score1 = int(state.get("markets_score1", 0) or 0)
            old_score2 = int(state.get("markets_score2", 0) or 0)
            if (new_score1 or new_score2) or not (old_score1 or old_score2):
                state["markets_score1"] = new_score1
                state["markets_score2"] = new_score2

            state["markets"] = data.get('set_markets', {}) or {}
            state["markets_phase"] = data.get('phase_num', 0)
            state["markets_match_id"] = data.get('match_id')
            state["markets_fetched_at"] = time.time()

        try:
            await handler.setup_listener(
                page, on_update,
                match_id=match_id,
                match_teams=payload.get('match_teams'),
            )
            logger.info(
                f"[FAST-TAB] ▶️ monitor запущен key={key} bk={fast_bk_norm}"
            )
        except TypeError:
            try:
                await handler.setup_listener(page, on_update, match_id=match_id)
            except TypeError:
                await handler.setup_listener(page, on_update)
        except Exception as e:
            logger.error(f"[FAST-TAB] setup_listener: {e}", exc_info=True)
            return

        while self._monitoring_active.get(key, False):
            await asyncio.sleep(1.0)

        logger.info(f"[FAST-TAB] 🛑 monitor остановлен key={key}")

    async def _preopen_impl(self, payload: dict, strategy: dict, key: str):
        match_id = payload.get('match_id')
        profile_id = strategy.get('profile_id')

        logger.info(
            f"🔵 PREOPEN START key={key} "
            f"match={match_id} profile={profile_id} "
            f"is_new={payload.get('is_new')} slow_bk={payload.get('slow_bk')} "
            f"sport={payload.get('sport')} "
            f"fast_bk={payload.get('fast_bk')} delay={payload.get('delay')}"
        )

        if not profile_id or not match_id:
            logger.warning(f"🔴 PREOPEN SKIP {key}: нет profile_id/match_id")
            return

        max_session = int(strategy.get('max_bets_per_session', 0) or 0)
        if max_session > 0 and self._bets_session_total >= max_session:
            logger.info(
                f"🔴 PREOPEN SKIP {key}: лимит сессии достигнут "
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
                f"🔴 PREOPEN SKIP {key}: delay={delay_in_signal:.1f}с "
                f"> {MAX_ACCEPTABLE_DELAY}с — сигнал слишком старый"
            )
            return

        headless = strategy.get('headless', False)

        if strategy.get('ignore_repeats', False) and not payload.get('is_new', False):
            logger.info(f"🔴 PREOPEN SKIP {key}: ignore_repeats и не is_new")
            return

        sport = (payload.get('sport') or 'table_tennis').lower()
        slow_bk_early = _normalize_bk(payload.get('slow_bk'))
        allowed = AUTOBET_ENABLED_SPORTS_BY_BK.get(slow_bk_early, set())
        if sport not in allowed:
            logger.info(
                f"🔴 PREOPEN SKIP {key}: спорт '{sport}' не включён для "
                f"'{slow_bk_early}' (allowed={allowed})"
            )
            return

        if key in self._last_bet_time:
            delta = time.time() - self._last_bet_time[key]
            if delta < AFTER_GOAL_COOLDOWN:
                logger.info(f"🔴 PREOPEN SKIP {key}: cooldown {delta:.1f}с")
                return

        max_bets = strategy.get('max_bets_per_match', 1)
        if self._bets_by_match.get(key, 0) >= max_bets:
            logger.info(f"🔴 PREOPEN SKIP {key}: лимит ставок ({max_bets})")
            return

        if key in self._monitoring_active and self._monitoring_active[key]:
            self._monitoring_payloads[key] = payload
            self._monitoring_strategies[key] = strategy
            logger.info(f"🔄 PREOPEN UPDATE {key}: обновлены параметры мониторинга")
            return

        now = time.time()
        last_attempt = self._preopen_last_attempt.get(key, 0.0)
        if now - last_attempt < self.PREOPEN_COOLDOWN:
            logger.info(f"🔴 PREOPEN SKIP {key}: "
                        f"PREOPEN_COOLDOWN ({now - last_attempt:.1f}с)")
            return
        self._preopen_last_attempt[key] = now

        slow_bk = _normalize_bk(payload.get('slow_bk') or payload.get('bk'))
        handler_cls = HANDLERS.get(slow_bk)
        if not handler_cls:
            logger.error(f"🔴 PREOPEN SKIP {key}: нет handler для {slow_bk}")
            return

        max_parallel = 5 if handler_cls in INSTANCE_BASED_HANDLERS else 1
        active = self._profile_active_matches.get(profile_id, 0)
        if active >= max_parallel:
            logger.warning(
                f"🔴 PREOPEN SKIP {key}: профиль {profile_id} [{slow_bk}] — "
                f"уже {active} активных (лимит {max_parallel})"
            )
            return

        browser_wrapper = await self._get_existing_browser(profile_id)
        if not browser_wrapper:
            logger.warning(
                f"🔴 PREOPEN SKIP {key}: профиль {profile_id} не активен"
            )
            log_bus.warning(
                "Стратегия",
                f"Профиль {profile_id} не открыт. Выключите и включите стратегию заново."
            )
            return

        try:
            page = await browser_wrapper.new_page()
            logger.info(f"📄 Открыта отдельная вкладка для key={key}")
        except Exception as e:
            logger.error(f"Не удалось открыть новую вкладку: {e} — используем главную")
            page = browser_wrapper.page

        player1, player2 = payload.get('match_teams', ['', ''])

        handler = handler_cls(
            target_match_id=match_id,
            target_teams=[player1, player2],
        )
        self._handlers[key] = handler

        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.error(f"prepare_page для {slow_bk} упал: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                self._handlers.pop(key, None)
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
                self._handlers.pop(key, None)
                try:
                    await page.close()
                except Exception:
                    pass
                return
            else:
                log_bus.info("Матч", f"Найден кликом · {player1} vs {player2}")

        # ═══════════════════════════════════════════════════════
        # Marathon: SPA создаёт SSE-подписку ТОЛЬКО после reload.
        # ═══════════════════════════════════════════════════════
        if slow_bk == "marathon":
            await page.wait_for_timeout(2500)
            got = False
            try:
                if hasattr(handler, "has_received_sse"):
                    got = handler.has_received_sse()
            except Exception as e:
                logger.debug(f"Marathon has_received_sse: {e}")
            logger.info(f"Marathon: SSE после первой навигации = {got}")

            if not got:
                logger.info("Marathon: 🔄 reload для активации SSE")
                try:
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=AFTER_GOAL_TIMEOUT * 1000,
                    )
                    await page.wait_for_timeout(2500)
                    if hasattr(handler, "has_received_sse"):
                        got = handler.has_received_sse()
                    logger.info(f"Marathon: SSE после reload = {got}")
                    log_bus.info(
                        "Матч",
                        f"Marathon: SSE {'активирован' if got else 'НЕ пришёл'} "
                        f"после reload",
                    )
                except Exception as e:
                    logger.warning(f"Marathon reload: {e}")

        self._pages[key] = page
        self._monitoring_active[key] = True
        self._monitoring_payloads[key] = payload
        self._monitoring_strategies[key] = strategy
        self._monitoring_profiles[key] = profile_id
        self._monitoring_signal_time[key] = time.time()
        self._profile_active_matches[profile_id] = active + 1

        fast_bk = _normalize_bk(payload.get('fast_bk'))
        self._fast_poll_tasks[key] = asyncio.create_task(
            self._poll_fast_state(
                key=key,
                match_id=match_id,
                sport=sport,
                player1=player1,
                player2=player2,
                fast_bk=fast_bk,
                slow_bk=slow_bk,
            )
        )

        # ── Fast-кэфы ──
        #   Есть HTTP-клиент → быстрый polling, без вкладок.
        #   Нет клиента, но есть хендлер (LigaStavok, Zenit) → вкладка в fast-профиле.
        if fast_bk and fast_bk != slow_bk and has_fast_client(fast_bk):
            logger.info(
                f"📊 FAST_MARKETS: key={key} "
                f"slow={slow_bk} fast={fast_bk} (HTTP)"
            )
            self._fast_markets_poll_tasks[key] = asyncio.create_task(
                self._poll_fast_markets(
                    key=key, match_id=match_id, sport=sport,
                    player1=player1, player2=player2,
                    fast_bk=fast_bk,
                )
            )
        elif (fast_bk and fast_bk != slow_bk
                and fast_bk in HANDLERS
                and is_fast_configured()
                and (strategy.get('type') or '').lower() == 'live-value'):
            logger.info(
                f"📊 FAST_MARKETS: key={key} "
                f"slow={slow_bk} fast={fast_bk} (TAB в fast-профиле)"
            )
            try:
                fast_page = await self._open_fast_match(
                    key=key,
                    match_id=match_id,
                    fast_bk=fast_bk,
                    payload=payload,
                    sport=sport,
                )
                if fast_page:
                    task = asyncio.create_task(
                        self._monitor_fast_markets_via_handler(
                            key=key,
                            match_id=match_id,
                            fast_bk=fast_bk,
                            payload=payload,
                            strategy=strategy,
                        )
                    )
                    self._fast_monitor_tasks[key] = task
            except Exception as e:
                logger.warning(f"[FAST-TAB] {fast_bk}: {e}")
        else:
            logger.info(
                f"📊 FAST_MARKETS: {fast_bk or '—'} — live-value пропущен "
                f"(нет HTTP-клиента и вкладка не открыта)"
            )

        task = asyncio.create_task(
            self._monitor_loop(key, match_id, payload, strategy, handler)
        )
        self._monitoring_tasks[key] = task

        logger.info(f"✅ PREOPEN DONE key={key} профиль={profile_id}")

        log_bus.match_event(
            bk=slow_bk,
            match_id=match_id,
            teams=[player1, player2],
            message=f"👁 Преоткрыт матч · delay {delay_in_signal:.1f}с · {sport}",
            level="info",
        )

    async def stop_monitoring(self, key_or_match_id: str):
        """
        Останавливает мониторинг.
        Может принимать как составной key, так и просто match_id
        (тогда остановит все профили для этого матча).
        """
        if key_or_match_id in self._monitoring_active:
            await self._cleanup_monitoring(key_or_match_id)
            return
        # Иначе — как match_id
        keys = [k for k in list(self._monitoring_active.keys())
                if _match_id_from_key(k) == key_or_match_id]
        for k in keys:
            await self._cleanup_monitoring(k)

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

    async def _enqueue_bet(self, profile_id: str, key: str, match_id: str,
                           handler, page, bet_data: dict,
                           bk: str, amount: float, bet_info: dict,
                           signal_time: float = None):
        if key in self._bets_enqueued:
            logger.info(f"⏭ Матч {key} уже в очереди — не дублируем")
            return
        self._bets_enqueued.add(key)

        self._ensure_queue_worker(profile_id)
        q = self._bet_queues.get(profile_id)
        if q is None:
            q = asyncio.Queue()
            self._bet_queues[profile_id] = q

        # ── TTL от момента обнаружения edge ──
        if signal_time is None:
            signal_time = time.time()

        ttl = SIGNAL_TTL_BY_BK.get(bk, SIGNAL_TTL_DEFAULT)
        if time.time() - signal_time > ttl:
            logger.info(
                f"⏭ Матч {key} не поставлен в очередь — "
                f"сигнал уже стар ({time.time() - signal_time:.0f}с)"
            )
            self._bets_enqueued.discard(key)
            return

        teams = (self._monitoring_payloads
                 .get(key, {})
                 .get('match_teams', ['?', '?']))

        await q.put({
            "key": key,
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
            f"📥 В очередь [{bk}/{profile_id}] поставлен key={key} "
            f"(в очереди: {q.qsize()}, возраст {time.time() - signal_time:.1f}с)"
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

            key = item.get("key")
            match_id = item.get("match_id")
            try:
                await self._execute_bet(item)
            except Exception as e:
                logger.error(f"bet worker [{profile_id}] / {key}: {e}",
                             exc_info=True)
            finally:
                page = item.get("page")
                handler = item.get("handler")
                retryable = item.get("_retryable", False)

                # ВАЖНО: сначала останавливаем listener хендлера —
                # у LigaStavok/Winline/Marathon есть свои фоновые
                # задачи (_snapshot_task / _poll_task / SSE),
                # которые надо гасить ДО page.close(). Иначе они
                # продолжают дёргать callback после закрытия страницы.
                #
                # НО: если ошибка retryable — listener НЕ снимаем,
                # иначе мониторинг потеряет handler и следующий
                # on_update не сможет повторить ставку.
                if handler and not retryable:
                    try:
                        await handler.stop_listener(page)
                    except Exception as e:
                        logger.debug(
                            f"worker stop_listener({key}): {e}"
                        )

                # ── ПАТЧ 5, шаг 2: не закрываем при retryable ──
                if retryable:
                    logger.info(
                        f"⏸ Страница {key} НЕ закрыта (retryable-ошибка) "
                        f"— следующая попытка при следующем on_update"
                    )
                elif page and not page.is_closed():
                    try:
                        await page.close()
                        logger.debug(
                            f"Страница матча {key} закрыта после ставки"
                        )
                    except Exception:
                        pass

                if key:
                    self._bets_enqueued.discard(key)
                q.task_done()

    async def _execute_bet(self, item: dict):
        key = item["key"]
        match_id = item.get("match_id") or _match_id_from_key(key)
        signal_time = item["signal_time"]
        bk = item["bk"]
        handler = item["handler"]
        page = item["page"]
        bet_data = item["bet_data"]
        amount = item["amount"]
        bet_info = item["bet_info"]
        teams = item.get("match_teams") or ["?", "?"]

        # По умолчанию — ставку НЕ ретраим (после ошибки страницу закрываем).
        # Если ошибка "retryable" — выставим True и не будем закрывать
        # страницу, чтобы следующий on_update смог повторить.
        item["_retryable"] = False

        ttl = SIGNAL_TTL_BY_BK.get(bk, SIGNAL_TTL_DEFAULT)
        age = time.time() - signal_time
        if age > ttl:
            logger.warning(
                f"⏰ Сигнал {key} [{bk}] устарел ({age:.1f}с > {ttl:.0f}с) — дропаем"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"⏰ Сигнал устарел ({age:.0f}с > {ttl:.0f}с) — пропуск",
                level="warning",
            )
            # ── Больше не пытаемся ставить по этому матчу ──
            _strat = self._monitoring_strategies.get(key) or {}
            max_bets = _strat.get('max_bets_per_match', 1)
            self._bets_by_match[key] = max_bets
            return

        if not self._monitoring_active.get(key, False):
            logger.info(f"⏭ Мониторинг {key} неактивен — дропаем ставку")
            return

        if page.is_closed():
            logger.warning(f"⏭ Страница {key} закрыта — дропаем ставку")
            # Страница уже закрыта, дальше ставить бессмысленно.
            # Ставим _bets_by_match = max_bets → мониторинг перестанет
            # пытаться, закроется по MONITOR_MAX_LIFETIME.
            strategy = self._monitoring_strategies.get(key) or {}
            max_bets = strategy.get('max_bets_per_match', 1)
            self._bets_by_match[key] = max_bets
            return

        strategy = self._monitoring_strategies.get(key) or {}
        strategy_type = (strategy.get('type') or '').lower()

        target_market = bet_info.get('market')
        # RACE-префикс срезаем, чтобы взять порог от базового рынка
        if target_market and target_market.startswith("race_"):
            target_market = target_market.replace("race_", "", 1)

        if strategy_type == 'live-value':
            is_open_now, why = self._verify_live_value_now(key, strategy)
        else:
            is_open_now, why = self._verify_delay_now(
                key, strategy, market=target_market
            )

        if not is_open_now:
            logger.warning(
                f"⏭ BET EXEC [{bk}] key={key} — сигнал пропал: {why}"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"⏭ Ставка отменена: {why}",
                level="warning",
            )
            return

        logger.info(
            f"🎯 BET EXEC [{bk}] key={key} "
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

        # ── Olimp retry ──
        if (not result.get("success")
                and bk == "olimp"
                and "недоступ" in (result.get("error") or "").lower()):

            if strategy_type == 'live-value':
                is_open_now, why2 = self._verify_live_value_now(
                    key, strategy
                )
            else:
                is_open_now, why2 = self._verify_delay_now(
                    key, strategy, market=target_market
                )

            if not is_open_now:
                logger.warning(
                    f"⏭ Olimp retry отменён — условие пропало: {why2}"
                )
                log_bus.match_event(
                    bk=bk, match_id=match_id, teams=teams,
                    message=f"⏭ Retry отменён: условие пропало ({why2})",
                    level="warning",
                )
            else:
                fresh = self._refresh_outcome_for_retry(key, bet_info)
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
            self._bets_by_match[key] = self._bets_by_match.get(key, 0) + 1
            set_key_num = bet_info.get("set_number", 0)
            phase_key = (key, set_key_num)
            self._bets_by_phase[phase_key] = self._bets_by_phase.get(phase_key, 0) + 1
            self._last_bet_time[key] = time.time()
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
                f"✅ Ставка [{bk}] key={key} отправлена! "
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
            err_lower = err.lower()
            diag = result.get("diag")

            retryable_keywords = (
                "ukey",
                "user_ukey",
                "no user_session",
                "user_info",
                "корзин",
                "clear_basket",
                "not ready",
            )
            if any(kw in err_lower for kw in retryable_keywords):
                item["_retryable"] = True
                logger.warning(
                    f"🔁 Retryable-ошибка [{bk}] key={key}: "
                    f"{err[:120]} — страница остаётся открытой"
                )

            logger.error(
                f"❌ Ошибка ставки [{bk}] key={key}: {err} "
                f"diag={diag}"
            )
            log_bus.match_event(
                bk=bk, match_id=match_id, teams=teams,
                message=f"❌ Ставка не прошла: {err[:160]}",
                level="error",
            )

            if item.get("_retryable", False):
                logger.warning(
                    f"🔁 Retryable-ошибка [{bk}] match={match_id} — "
                    f"страница остаётся открытой, повторим при след. on_update"
                )
            else:
                # Не-retryable → матч исчерпан.
                # Ставим _bets_by_match = max_bets, чтобы мониторинг
                # не пытался ставить по этому сигналу снова каждые 2 сек.
                strategy = self._monitoring_strategies.get(key) or {}
                max_bets = strategy.get('max_bets_per_match', 1)
                self._bets_by_match[key] = max_bets
                logger.info(
                    f"🚫 Ставок по key={key} больше не будет "
                    f"(ошибка не-retryable)"
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

        for _k, t in list(self._fast_poll_tasks.items()):
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

        # Отменяем polling fast-маркетов
        for _k, t in list(self._fast_markets_poll_tasks.items()):
            try:
                t.cancel()
            except Exception:
                pass
        if self._fast_markets_poll_tasks:
            try:
                await asyncio.wait(
                    list(self._fast_markets_poll_tasks.values()), timeout=2.0
                )
            except Exception:
                pass
        self._fast_markets_poll_tasks.clear()

        # ── Fast-вкладки ──
        for _k, t in list(self._fast_monitor_tasks.items()):
            try:
                t.cancel()
            except Exception:
                pass
        if self._fast_monitor_tasks:
            try:
                await asyncio.wait(
                    list(self._fast_monitor_tasks.values()), timeout=2.0
                )
            except Exception:
                pass
        self._fast_monitor_tasks.clear()

        for _k, page in list(self._fast_pages.items()):
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass
        self._fast_pages.clear()
        self._fast_handlers.clear()

        # Останавливаем fast-профиль
        if self._fast_browser is not None:
            try:
                await self._fast_browser.stop()
                logger.info("[FAST-TAB] ✅ Fast-профиль закрыт")
            except Exception as e:
                logger.warning(f"[FAST-TAB] fast_browser.stop: {e}")
            self._fast_browser = None

        # Закрываем HTTP-сессии fast-клиентов
        try:
            await fast_clients_shutdown()
        except Exception as e:
            logger.debug(f"fast_clients_shutdown: {e}")

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

        for k in list(self._pages.keys()):
            try:
                page = self._pages.pop(k, None)
                if page and not page.is_closed():
                    await page.close()
            except Exception as e:
                logger.warning(f"shutdown: закрытие страницы {k}: {e}")

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
    async def _cleanup_monitoring(self, key: str):
        match_id = _match_id_from_key(key)

        task = self._monitoring_tasks.pop(key, None)
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        poll_t = self._fast_poll_tasks.pop(key, None)
        if poll_t and not poll_t.done():
            poll_t.cancel()
            try:
                await poll_t
            except (asyncio.CancelledError, Exception):
                pass

        # Отменяем polling fast-маркетов
        poll_m = self._fast_markets_poll_tasks.pop(key, None)
        if poll_m and not poll_m.done():
            poll_m.cancel()
            try:
                await poll_m
            except (asyncio.CancelledError, Exception):
                pass

        # ── Fast-вкладка (для LigaStavok/Zenit) ──
        fast_task = self._fast_monitor_tasks.pop(key, None)
        if fast_task and not fast_task.done():
            fast_task.cancel()
            try:
                await fast_task
            except (asyncio.CancelledError, Exception):
                pass

        fast_page = self._fast_pages.pop(key, None)
        fast_handler = self._fast_handlers.pop(key, None)
        if fast_handler and fast_page and not fast_page.is_closed():
            try:
                await fast_handler.stop_listener(fast_page)
            except Exception as e:
                logger.debug(f"[FAST-TAB] stop_listener({key}): {e}")
        if fast_page and not fast_page.is_closed():
            try:
                await fast_page.close()
            except Exception:
                pass

        self._fast_state.pop(key, None)

        handler = self._handlers.pop(key, None)
        page = self._pages.get(key)

        # ВАЖНО: stop_listener вызываем ВСЕГДА, даже если страница
        # уже закрыта.
        if handler:
            try:
                await handler.stop_listener(page)
            except Exception as e:
                logger.debug(f"stop_listener({key}): {e}")

        # ── Гонка cleanup vs. bet worker ──
        # Если ставка уже в очереди — страницу НЕ закрываем.
        # Иначе bet worker получит Target closed, когда попробует
        # вызвать page.evaluate() в place_bet.
        # Воркер сам закроет страницу после обработки ставки
        # (см. _bet_worker → finally).
        if key in self._bets_enqueued:
            logger.info(
                f"⏸ Страница {key} не закрыта — ставка в очереди, "
                f"закроет bet worker"
            )
        else:
            if page and not page.is_closed():
                try:
                    await page.close()
                except Exception as e:
                    logger.warning(f"close page {key}: {e}")

        # Гарантированно убираем страницу из _pages — запись больше
        # не нужна, а в мультипрофиле такие «повисшие» ссылки
        # накапливаются.
        self._pages.pop(key, None)

        profile_id = self._monitoring_profiles.pop(key, None)
        if profile_id and profile_id in self._profile_active_matches:
            self._profile_active_matches[profile_id] = max(
                0, self._profile_active_matches[profile_id] - 1
            )

        payload = self._monitoring_payloads.get(key) or {}
        start_ts = self._monitoring_signal_time.get(key)
        elapsed = (time.time() - start_ts) if start_ts else 0
        bets_count = self._bets_by_match.get(key, 0)

        self._monitoring_active[key] = False
        self._monitoring_payloads.pop(key, None)
        self._monitoring_strategies.pop(key, None)
        self._monitoring_signal_time.pop(key, None)
        self._last_update_time.pop(key, None)
        self._last_slow_data.pop(key, None)
        self._bets_enqueued.discard(key)
        self._last_ui_state.pop(key, None)

        # ── Чистим счётчики ставок этого матча ──
        # ВАЖНО: чистим ТОЛЬКО если по матчу НЕ было успешных ставок.
        # Иначе новый сигнал по тому же match_id откроет новый монитор
        # и поставит снова — а это нарушает max_bets_per_match.
        # Баг «фантомное закрытие за 6с» относился к случаям, когда
        # ставок не было — там счётчик действительно надо обнулять.
        if bets_count == 0:
            self._bets_by_match.pop(key, None)
            for pk in [k for k in list(self._bets_by_phase.keys())
                       if k[0] == key]:
                self._bets_by_phase.pop(pk, None)
        # Если были ставки — счётчик остаётся, лимит работает.

        logger.info(f"🛑 CLEANUP key={key}")

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

        key = _make_key(match_id, profile_id)
        if key in self._last_bet_time:
            if time.time() - self._last_bet_time[key] < AFTER_GOAL_COOLDOWN:
                _aux_logger.debug(f"Value: кулдаун для матча {key}")
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
            self._last_bet_time[key] = time.time()
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
                                 fast_state: Optional[dict],
                                 fast_sub_override: Optional[List[int]] = None,
                                 fast_phase_override: int = 0) -> Tuple[bool, str]:
        sport = payload.get('sport', 'table_tennis')

        # ── ЕДИНЫЙ источник fast-данных: fast-client (override приоритетнее) ──
        # fast_state из бэкенда отстаёт на 1-2 сек. В момент смены партии
        # он даёт phase=4 + sub=12:10 (рассинхрон внутри пакета).
        # _monitor_loop уже получает fast_sub из fast-client — передаём
        # его сюда через fast_sub_override.
        if fast_sub_override is not None:
            fast_sub1 = int(fast_sub_override[0])
            fast_sub2 = int(fast_sub_override[1])
        elif fast_state:
            fast_sub1 = int(fast_state.get("sub_score1") or 0)
            fast_sub2 = int(fast_state.get("sub_score2") or 0)
        else:
            return False, "fast_sub отсутствует"

        slow_sub1 = int(data.get("sub_score1") or 0)
        slow_sub2 = int(data.get("sub_score2") or 0)

        if sport not in PHASE_END_BY_SCORE and sport not in PHASE_END_BY_TIME:
            return False, f"неизвестный sport={sport}"

        # ── Защита: slow впереди fast по фазе ──
        # Пример ложного сигнала из логов:
        #   fast_phase=4, slow_phase_num=5, fast_sub=[4,10], slow_sub=[0,0]
        #   diff = +10 → "Задержка подтверждена". Но это рассинхрон:
        #   slow уже в 5-й партии, а fast ещё в 4-й. Такой сигнал
        #   глушим здесь, чтобы не мусорить в UI и не гонять monitor
        #   до таймаута на пустом best_bet.
        # ── Единый источник фазы (согласовано с _choose_best_bet) ──
        # Раньше для НТ фаза считалась из score, а _choose_best_bet
        # брала fast_phase из payload — это давало расхождение.
        if fast_phase_override > 0:
            fast_phase_num = fast_phase_override
        else:
            fast_phase_num = self._parse_phase_num(payload.get('fast_phase', ''))
        slow_phase_num = int(data.get('phase_num') or 0)

        # Fallback: если у fast нет phase (бывает у некоторых БК)
        if not fast_phase_num and sport in PHASE_END_BY_SCORE and fast_state:
            fast_phase_num = (
                int(fast_state.get("score1") or 0)
                + int(fast_state.get("score2") or 0)
                + 1
            )
        if not slow_phase_num and sport in PHASE_END_BY_SCORE:
            slow_phase_num = (
                int(data.get("score1") or 0)
                + int(data.get("score2") or 0)
                + 1
            )

        if fast_phase_num > 0 and slow_phase_num > 0 \
                and slow_phase_num > fast_phase_num:
            return False, (
                f"slow впереди fast "
                f"(fast={fast_phase_num}, slow={slow_phase_num})"
            )

        # ── Guard: slow_sub = 0:0, но fast уже много набрал ──
        # Marathon часто отдаёт sub=0:0 после перехода на новую
        # партию — данные parts[] ещё не подгрузились. Это не задержка,
        # а потеря данных. Иначе бот ставит winner/under по очкам
        # fast, а slow вообще не знает про эту партию.
        # Пример: fast 12:10 (4-я партия), slow 0:0 (всё ещё 4-я
        # по счёту, но очки не пришли). Ставка ставилась на "4-ю партию",
        # а по факту только что закончилась 3-я.
        slow_sub_sum = slow_sub1 + slow_sub2
        fast_sub_sum = fast_sub1 + fast_sub2
        if slow_sub_sum == 0 and fast_sub_sum >= 5:
            return False, (
                f"slow_sub=0:0 при fast_sub={fast_sub1}:{fast_sub2} "
                f"— потеря данных, не задержка"
            )

        # ── Guard 2: переходный момент между партиями ──
        # Завершённость партии проверяем ПО ПРАВИЛАМ ВИДА СПОРТА,
        # а не по сумме очков (сумма >= 11 глушит 6:5, 7:4 — это баг).
        def _phase_is_done(s1, s2, pnum):
            if sport == 'table_tennis':
                return (s1 >= 11 and s1 - s2 >= 2) or \
                       (s2 >= 11 and s2 - s1 >= 2)
            if sport in ('volleyball', 'beach_volleyball'):
                thr = 15 if (pnum or 1) >= 5 else 25
                return (s1 >= thr and s1 - s2 >= 2) or \
                       (s2 >= thr and s2 - s1 >= 2)
            return False

        if fast_phase_num > 0 and slow_phase_num > 0 \
                and fast_phase_num == slow_phase_num:
            fast_done = _phase_is_done(fast_sub1, fast_sub2, fast_phase_num)
            slow_done = _phase_is_done(slow_sub1, slow_sub2, slow_phase_num)

            if fast_done and not slow_done and slow_sub_sum < 4:
                return False, (
                    f"переходный момент: fast завершил партию "
                    f"({fast_sub1}:{fast_sub2}) vs slow "
                    f"({slow_sub1}:{slow_sub2}) — разные партии"
                )
            if slow_done and not fast_done and fast_sub_sum < 4:
                return False, (
                    f"переходный момент: slow завершил партию "
                    f"({slow_sub1}:{slow_sub2}) vs fast "
                    f"({fast_sub1}:{fast_sub2}) — разные партии"
                )

        # ── Guard 3: целостность fast-пакета ──
        # Fast показал sub завершённой партии, но phase_num при этом
        # равен текущей (не следующей). Это внутреннее противоречие:
        #   phase=4, sub=12:10
        # Возможно, sub от 3-й партии, а phase уже переключился на 4-ю.
        # Проверяем: если fast завершил партию по очкам И у slow < 4 очков —
        # это явный рассинхрон fast-пакета, не валидный сигнал.
        if fast_phase_num > 0 and _phase_is_done(fast_sub1, fast_sub2,
                                                  fast_phase_num) \
                and slow_sub_sum < 4:
            return False, (
                f"fast-пакет нецелостный: fast_sub={fast_sub1}:{fast_sub2} "
                f"(партия {fast_phase_num} завершена), но "
                f"slow_sub={slow_sub1}:{slow_sub2} (<4) — "
                f"это данные предыдущей партии"
            )

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

        fast_phase_for_done = self._parse_phase_num(payload.get('fast_phase', ''))
        fast_set_done = self._is_set_done(sport, a, b, set_number, fast_phase_for_done)

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
            valid.sort(key=lambda c: (c[2], _priority(c[0])), reverse=True)
        else:
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
    # Проверка: fast уже показывает преимущество в НАШУ сторону?
    # (live-value: ставим только если fast прав по счёту)
    # ============================================================
    @staticmethod
    def _fast_favors(market: str, side: str, line: float,
                     fast_sub1: int, fast_sub2: int,
                     phase_done: bool,
                     sport: str = "table_tennis",
                     set_number: int = 1) -> bool:
        """
        True, если fast уже показывает, что наш исход выигрывает по счёту.
        Используется в live-value, чтобы не ставить «лотерейные» value,
        где кэф у slow просто устарел, но fast не подтверждает наш исход.

        Логика по рынкам:
          winner 1/2   — тот, у кого больше очков на fast
          total over   — fast-сумма уже >= line
          total under  — fast-сумма + max-возможное-до-конца < line
          it_over      — fast-очки игрока >= line
          it_under     — fast-очки игрока + max < line
          handicap     — margin + line > 0 на fast
          odd/even     — fast_total чётный/нечётный И партия завершена
          point        — не гарантируется, всегда False
        """
        try:
            if market == 'winner':
                if side == '1':
                    return fast_sub1 > fast_sub2
                else:
                    return fast_sub2 > fast_sub1

            if market == 'total':
                total = fast_sub1 + fast_sub2
                if side == 'over':
                    if total >= line:
                        return True
                    if phase_done:
                        return total >= line
                    # оценим минимально-возможный финал
                    if sport == 'table_tennis':
                        return (11 + min(fast_sub1, fast_sub2)) >= line
                    if sport in ('volleyball', 'beach_volleyball'):
                        thr = 15 if set_number >= 5 else 25
                        return (thr + min(fast_sub1, fast_sub2)) >= line
                    return False
                else:  # under
                    if phase_done:
                        return total < line
                    return False

            if market == 'it':
                player, direction = side.split('_', 1)
                ind = fast_sub1 if player == '1' else fast_sub2
                if direction == 'over':
                    return ind >= line
                else:  # under
                    if phase_done:
                        return ind < line
                    return False

            if market == 'handicap':
                margin = fast_sub1 - fast_sub2 if side == '1' \
                    else fast_sub2 - fast_sub1
                if phase_done:
                    return (margin + line) > 0
                # партия идёт — не гарантируем
                return False

            if market == 'odd':
                if not phase_done:
                    return False
                total = fast_sub1 + fast_sub2
                if side == 'odd':
                    return total % 2 == 1
                return total % 2 == 0

            if market == 'point':
                # следующее очко не гарантируется
                return False

        except Exception:
            return False
        return False
    # ============================================================
    # Проверка: исход УЖЕ проиграл на fast? (ставить нельзя)
    # ============================================================
    @staticmethod
    def _is_outcome_dead(market: str, side: str, line: float,
                         fast_sub1: int, fast_sub2: int,
                         phase_done: bool) -> bool:
        try:
            if market == 'it':
                player, direction = str(side).split('_', 1)
                ind = fast_sub1 if player == '1' else fast_sub2
                if direction == 'under':
                    if ind >= line:
                        return True
                    if phase_done and ind >= line:
                        return True
                else:  # over
                    if phase_done and ind < line:
                        return True
                return False

            if market == 'total':
                total = fast_sub1 + fast_sub2
                if side == 'under':
                    if total >= line:
                        return True
                    if phase_done and total >= line:
                        return True
                else:  # over
                    if phase_done and total < line:
                        return True
                return False

            if market == 'handicap':
                margin = fast_sub1 - fast_sub2 if side == '1' \
                    else fast_sub2 - fast_sub1
                if phase_done and (margin + line) <= 0:
                    return True
                return False

            if market == 'winner':
                if phase_done:
                    winner = '1' if fast_sub1 > fast_sub2 else '2'
                    return side != winner
                return False

        except Exception:
            return False
        return False

    # ============================================================
    # Live-value: сравнение кэфов fast и slow
    # ============================================================
    def _choose_live_value_bet(self, key: str, slow_data: dict, strategy: dict,
                                payload: dict) -> Optional[dict]:
        """
        Сравнивает внутриматчевые кэфы fast (HTTP) и slow (хендлер).
        """
        match_id = payload.get('match_id')

        # ── Свежие fast-кэфы ──
        fast_entry = self._get_fast_markets(key)
        if not fast_entry:
            logger.debug("live_value: нет свежих fast_markets")
            return None

        sport = (payload.get('sport') or 'table_tennis').lower()

        fast_set_markets = fast_entry.get("set_markets") or {}
        slow_set_markets = slow_data.get('set_markets') or {}

        fast_phase_num = int(fast_entry.get("phase_num") or 0)
        slow_phase_num = int(slow_data.get('phase_num') or 0)

        # ── Для НТ/волейбола фазу считаем из счёта партий ──
        if sport in PHASE_END_BY_SCORE:
            fsc1 = int(fast_entry.get("score1") or 0)
            fsc2 = int(fast_entry.get("score2") or 0)
            ssc1 = int(slow_data.get("score1") or 0)
            ssc2 = int(slow_data.get("score2") or 0)
            fast_phase_num = fsc1 + fsc2 + 1
            slow_phase_num = ssc1 + ssc2 + 1

        if fast_phase_num <= 0 or slow_phase_num <= 0:
            return None

        if fast_phase_num != slow_phase_num:
            logger.debug(
                f"live_value: фазы разные fast={fast_phase_num} "
                f"slow={slow_phase_num}"
            )
            return None

        set_key = f"set_{slow_phase_num}"
        slow_markets = slow_set_markets.get(set_key, {}) or {}
        fast_markets = fast_set_markets.get(set_key, {}) or {}

        if not slow_markets or not fast_markets:
            return None

        # ═══════════════════════════════════════════════════════════
        # ОПРЕДЕЛЕНИЕ SWAP (home/away у fast относительно slow)
        # ═══════════════════════════════════════════════════════════
        slow_is_reversed = payload.get('slow_is_reversed')

        if slow_is_reversed is not None:
            swap = bool(slow_is_reversed)
            s_s1 = int(slow_data.get("sub_score1") or 0)
            s_s2 = int(slow_data.get("sub_score2") or 0)
            f_s1 = int(fast_entry.get("sub_score1") or 0)
            f_s2 = int(fast_entry.get("sub_score2") or 0)
            s_p1 = int(slow_data.get("score1") or 0)
            s_p2 = int(slow_data.get("score2") or 0)
            f_p1 = int(fast_entry.get("score1") or 0)
            f_p2 = int(fast_entry.get("score2") or 0)
            logger.info(
                f"LV_SWAP match={match_id} phase={slow_phase_num} "
                f"swap={swap} (from backend) "
                f"slow_score={s_p1}:{s_p2} sub={s_s1}:{s_s2} "
                f"fast_score={f_p1}:{f_p2} sub={f_s1}:{f_s2}"
            )
        else:
            s_s1 = int(slow_data.get("sub_score1") or 0)
            s_s2 = int(slow_data.get("sub_score2") or 0)
            f_s1 = int(fast_entry.get("sub_score1") or 0)
            f_s2 = int(fast_entry.get("sub_score2") or 0)

            s_p1 = int(slow_data.get("score1") or 0)
            s_p2 = int(slow_data.get("score2") or 0)
            f_p1 = int(fast_entry.get("score1") or 0)
            f_p2 = int(fast_entry.get("score2") or 0)

            swap = None

            if abs(s_s1 - s_s2) >= 2 and abs(f_s1 - f_s2) >= 2:
                slow_leader = '1' if s_s1 > s_s2 else '2'
                fast_leader = '1' if f_s1 > f_s2 else '2'
                swap = (slow_leader != fast_leader)

            if swap is None and abs(s_p1 - s_p2) >= 1 and abs(f_p1 - f_p2) >= 1:
                slow_leader = '1' if s_p1 > s_p2 else '2'
                fast_leader = '1' if f_p1 > f_p2 else '2'
                swap = (slow_leader != fast_leader)

            logger.info(
                f"LV_SWAP match={match_id} phase={slow_phase_num} "
                f"swap={swap} (fallback by score) "
                f"slow_score={s_p1}:{s_p2} sub={s_s1}:{s_s2} "
                f"fast_score={f_p1}:{f_p2} sub={f_s1}:{f_s2}"
            )

        # ═══════════════════════════════════════════════════════════
        # GUARD: fast и slow должны показывать СХОЖИЙ счёт.
        # ═══════════════════════════════════════════════════════════
        s_sum = s_s1 + s_s2
        f_sum = f_s1 + f_s2

        if s_sum > f_sum:
            logger.info(
                f"LV: skip — SLOW впереди FAST (неверный live-value) "
                f"slow_sub={s_s1}:{s_s2} ({s_sum}) > "
                f"fast_sub={f_s1}:{f_s2} ({f_sum})"
            )
            return None

        if f_sum == 0 and s_sum > 0:
            logger.info(
                f"LV: skip — fast не загрузился "
                f"(slow_sub={s_s1}:{s_s2}, fast_sub={f_s1}:{f_s2})"
            )
            return None

        sub_diff = f_sum - s_sum
        parts_diff = f_p1 + f_p2 - s_p1 - s_p2

        sides_known = (swap is not None)
        if not sides_known:
            logger.debug(
                f"live_value: стороны не определены "
                f"(s_sub={s_s1}:{s_s2}, f_sub={f_s1}:{f_s2}) — "
                f"играем только over/under"
            )

        def _s(k: str) -> str:
            if swap:
                return '2' if k == '1' else '1'
            return k

        _fast_sub1 = int(fast_entry.get("sub_score1") or 0)
        _fast_sub2 = int(fast_entry.get("sub_score2") or 0)

        phase_done = False
        if sport == 'table_tennis':
            if (_fast_sub1 >= 11 and _fast_sub1 - _fast_sub2 >= 2) or \
               (_fast_sub2 >= 11 and _fast_sub2 - _fast_sub1 >= 2):
                phase_done = True
        elif sport == 'volleyball':
            _thr = 15 if slow_phase_num >= 5 else 25
            if (_fast_sub1 >= _thr and _fast_sub1 - _fast_sub2 >= 2) or \
               (_fast_sub2 >= _thr and _fast_sub2 - _fast_sub1 >= 2):
                phase_done = True

        only = strategy.get('only_markets') or ['winner', 'total', 'handicap']
        min_edge = float(strategy.get('min_edge_percent', 5.0)) / 100.0
        max_edge = float(strategy.get('max_edge_percent', 20.0)) / 100.0
        min_odds = float(strategy.get('min_odds', 1.30))
        max_odds = float(strategy.get('max_odds', 5.0))

        # ── LV режим: требуем ли задержку по счёту ──
        # "odds_with_score" — старая логика (консервативно)
        # "odds_only"       — только сравнение кэфов
        lv_mode = strategy.get('lv_mode', 'odds_with_score')
        require_score_lag = (lv_mode == 'odds_with_score')
        if not require_score_lag:
            logger.info(
                f"LV mode=odds_only — задержка по счёту отключена "
                f"(sub_diff={sub_diff}, parts_diff={parts_diff})"
            )

        candidates = []

        # ── WINNER ──
        if 'winner' in only and sides_known:
            sw = slow_markets.get('winner', {}) or {}
            fw = fast_markets.get('winner', {}) or {}
            for side in ('1', '2'):
                so = sw.get(side)
                fo = fw.get(_s(side))
                if not so or not fo or fo <= 1.01:
                    continue
                if not (min_odds <= so <= max_odds):
                    continue

                if require_score_lag:
                    _thr = self._get_market_threshold(strategy, 'winner')
                    if sub_diff < _thr and parts_diff < _thr:
                        logger.debug(
                            f"LV: skip winner — delay sub={sub_diff} "
                            f"parts={parts_diff} < thr={_thr}"
                        )
                        continue

                # ── Guard D: партия завершена и победил НЕ наш игрок ──
                if self._is_outcome_dead(
                    'winner', side, 0.0,
                    _fast_sub1, _fast_sub2, phase_done
                ):
                    logger.info(
                        f"LV: skip dead winner {side} — "
                        f"phase_done={phase_done} "
                        f"fast_sub={_fast_sub1}:{_fast_sub2}"
                    )
                    continue

                # ── Guard E: fast должен подтверждать наш исход ──
                if not self._fast_favors(
                    'winner', side, 0.0,
                    _fast_sub1, _fast_sub2, phase_done,
                    sport=sport, set_number=slow_phase_num
                ):
                    logger.info(
                        f"LV: skip winner {side} — "
                        f"fast НЕ подтверждает (sub={_fast_sub1}:{_fast_sub2}, "
                        f"phase_done={phase_done})"
                    )
                    continue

                edge = so / fo - 1
                if edge >= min_edge:
                    candidates.append({
                        'market': 'winner',
                        'side': side,
                        'odd': float(so),
                        'fast_odd': float(fo),
                        'edge': edge,
                        'set_number': slow_phase_num,
                        'reason': (f"LV winner {side}: "
                                   f"slow {so:.2f} / fast {fo:.2f} "
                                   f"= +{edge*100:.1f}%"
                                   f"{' [swapped]' if swap else ''}"),
                    })

        # ── TOTAL ──
        if 'total' in only:
            st = slow_markets.get('total', {}) or {}
            ft = fast_markets.get('total', {}) or {}
            slow_line = float(st.get('line', 0) or 0)
            fast_line = float(ft.get('line', 0) or 0)
            if slow_line > 0 and abs(slow_line - fast_line) < 0.01:
                for side in ('over', 'under'):
                    so = st.get(side)
                    fo = ft.get(side)
                    if not so or not fo or fo <= 1.01:
                        continue
                    if not (min_odds <= so <= max_odds):
                        continue

                    if require_score_lag:
                        _thr = self._get_market_threshold(strategy, 'total')
                        if sub_diff < _thr and parts_diff < _thr:
                            logger.debug(
                                f"LV: skip total — delay sub={sub_diff} "
                                f"parts={parts_diff} < thr={_thr}"
                            )
                            continue

                    if self._is_outcome_dead(
                        'total', side, slow_line,
                        _fast_sub1, _fast_sub2, phase_done
                    ):
                        logger.info(
                            f"LV: skip dead total {side} {slow_line} — "
                            f"sum={_fast_sub1+_fast_sub2} "
                            f"phase_done={phase_done}"
                        )
                        continue

                    if not self._fast_favors(
                        'total', side, slow_line,
                        _fast_sub1, _fast_sub2, phase_done,
                        sport=sport, set_number=slow_phase_num
                    ):
                        logger.info(
                            f"LV: skip total {side} {slow_line} — "
                            f"fast НЕ подтверждает "
                            f"(sum={_fast_sub1+_fast_sub2}, "
                            f"phase_done={phase_done})"
                        )
                        continue

                    edge = so / fo - 1
                    if edge >= min_edge:
                        candidates.append({
                            'market': 'total',
                            'side': side,
                            'odd': float(so),
                            'fast_odd': float(fo),
                            'edge': edge,
                            'set_number': slow_phase_num,
                            'line': slow_line,
                            'reason': (f"LV total {side} {slow_line}: "
                                       f"slow {so:.2f} / fast {fo:.2f} "
                                       f"= +{edge*100:.1f}%"),
                        })

        # ── HANDICAP ──
        if 'handicap' in only and sides_known:
            sh = slow_markets.get('handicap', {}) or {}
            fh = fast_markets.get('handicap', {}) or {}
            for side in ('1', '2'):
                ss = sh.get(side, {}) or {}
                fs = fh.get(_s(side), {}) or {}
                so = ss.get('odd')
                fo = fs.get('odd')
                sl = float(ss.get('line', 0) or 0)
                fl = float(fs.get('line', 0) or 0)
                if swap:
                    fl = -fl
                if not so or not fo or fo <= 1.01:
                    continue
                if abs(sl - fl) > 0.01:
                    continue
                if not (min_odds <= so <= max_odds):
                    continue

                if require_score_lag:
                    _thr = self._get_market_threshold(strategy, 'handicap')
                    if sub_diff < _thr and parts_diff < _thr:
                        logger.debug(
                            f"LV: skip handicap — delay sub={sub_diff} "
                            f"parts={parts_diff} < thr={_thr}"
                        )
                        continue

                if self._is_outcome_dead(
                    'handicap', side, sl,
                    _fast_sub1, _fast_sub2, phase_done
                ):
                    logger.info(
                        f"LV: skip dead handicap {side} {sl} — "
                        f"fast_sub={_fast_sub1}:{_fast_sub2} "
                        f"phase_done={phase_done}"
                    )
                    continue

                if not self._fast_favors(
                    'handicap', side, sl,
                    _fast_sub1, _fast_sub2, phase_done,
                    sport=sport, set_number=slow_phase_num
                ):
                    logger.info(
                        f"LV: skip handicap {side} {sl} — "
                        f"fast НЕ подтверждает "
                        f"(sub={_fast_sub1}:{_fast_sub2}, "
                        f"phase_done={phase_done})"
                    )
                    continue

                edge = so / fo - 1
                if edge >= min_edge:
                    candidates.append({
                        'market': 'handicap',
                        'side': side,
                        'odd': float(so),
                        'fast_odd': float(fo),
                        'edge': edge,
                        'set_number': slow_phase_num,
                        'line': sl,
                        'reason': (f"LV handicap {side} {sl}: "
                                   f"slow {so:.2f} / fast {fo:.2f} "
                                   f"= +{edge*100:.1f}%"
                                   f"{' [swapped]' if swap else ''}"),
                    })

        # ── IT ──
        if 'it' in only and sides_known:
            sit = slow_markets.get('it', {}) or {}
            fit = fast_markets.get('it', {}) or {}
            for player in ('1', '2'):
                sp = sit.get(player, {}) or {}
                fp = fit.get(_s(player), {}) or {}
                sl = float(sp.get('line', 0) or 0)
                fl = float(fp.get('line', 0) or 0)
                if sl <= 0 or abs(sl - fl) > 0.01:
                    continue
                for side in ('over', 'under'):
                    so = sp.get(side)
                    fo = fp.get(side)
                    if not so or not fo or fo <= 1.01:
                        continue
                    if not (min_odds <= so <= max_odds):
                        continue

                    if require_score_lag:
                        _thr = self._get_market_threshold(strategy, 'it')
                        if sub_diff < _thr and parts_diff < _thr:
                            logger.debug(
                                f"LV: skip it — delay sub={sub_diff} "
                                f"parts={parts_diff} < thr={_thr}"
                            )
                            continue

                    _ind = _fast_sub1 if player == '1' else _fast_sub2
                    if self._is_outcome_dead(
                        'it', f'{player}_{side}', sl,
                        _fast_sub1, _fast_sub2, phase_done
                    ):
                        logger.info(
                            f"LV: skip dead it{player}_{side} {sl} — "
                            f"fast_ind={_ind} "
                            f"phase_done={phase_done}"
                        )
                        continue

                    if not self._fast_favors(
                        'it', f'{player}_{side}', sl,
                        _fast_sub1, _fast_sub2, phase_done,
                        sport=sport, set_number=slow_phase_num
                    ):
                        logger.info(
                            f"LV: skip it{player}_{side} {sl} — "
                            f"fast НЕ подтверждает "
                            f"(ind={_ind}, phase_done={phase_done})"
                        )
                        continue

                    edge = so / fo - 1
                    if edge >= min_edge:
                        candidates.append({
                            'market': 'it',
                            'side': f'{player}_{side}',
                            'player': player,
                            'direction': side,
                            'odd': float(so),
                            'fast_odd': float(fo),
                            'edge': edge,
                            'set_number': slow_phase_num,
                            'line': sl,
                            'reason': (f"LV it{player} {side} {sl}: "
                                       f"slow {so:.2f} / fast {fo:.2f} "
                                       f"= +{edge*100:.1f}%"
                                       f"{' [swapped]' if swap else ''}"),
                        })

        # ── ODD ──
        if 'odd' in only:
            so_m = slow_markets.get('odd', {}) or {}
            fo_m = fast_markets.get('odd', {}) or {}
            for side in ('even', 'odd'):
                so = so_m.get(side)
                fo = fo_m.get(side)
                if not so or not fo or fo <= 1.01:
                    continue
                if not (min_odds <= so <= max_odds):
                    continue

                # ── Guard E: odd/even гарантируется только при
                #    завершённой партии. Иначе следующее очко
                #    перевернёт чётность — ставка «на угад».
                if not self._fast_favors(
                    'odd', side, 0.0,
                    _fast_sub1, _fast_sub2, phase_done,
                    sport=sport, set_number=slow_phase_num
                ):
                    logger.info(
                        f"LV: skip odd {side} — "
                        f"fast НЕ подтверждает "
                        f"(sum={_fast_sub1+_fast_sub2}, "
                        f"phase_done={phase_done})"
                    )
                    continue

                edge = so / fo - 1
                if edge >= min_edge:
                    candidates.append({
                        'market': 'odd',
                        'side': side,
                        'odd': float(so),
                        'fast_odd': float(fo),
                        'edge': edge,
                        'set_number': slow_phase_num,
                        'reason': (f"LV odd {side}: "
                                   f"slow {so:.2f} / fast {fo:.2f} "
                                   f"= +{edge*100:.1f}%"),
                    })

        # ── POINT ──
        if 'point' in only and sides_known:
            sp_m = slow_markets.get('point', {}) or {}
            fp_m = fast_markets.get('point', {}) or {}
            for side in ('1', '2'):
                so = sp_m.get(side)
                fo = fp_m.get(_s(side))
                if not so or not fo or fo <= 1.01:
                    continue
                if not (min_odds <= so <= max_odds):
                    continue

                # ── Guard E: point — «следующее очко» не гарантируется.
                #    _fast_favors всегда False → point не будет ставиться.
                if not self._fast_favors(
                    'point', side, 0.0,
                    _fast_sub1, _fast_sub2, phase_done,
                    sport=sport, set_number=slow_phase_num
                ):
                    logger.info(
                        f"LV: skip point {side} — "
                        f"fast НЕ подтверждает "
                        f"(следующее очко — лотерея)"
                    )
                    continue

                edge = so / fo - 1
                if edge >= min_edge:
                    candidates.append({
                        'market': 'point',
                        'side': side,
                        'odd': float(so),
                        'fast_odd': float(fo),
                        'edge': edge,
                        'set_number': slow_phase_num,
                        'reason': (f"LV point {side}: "
                                   f"slow {so:.2f} / fast {fo:.2f} "
                                   f"= +{edge*100:.1f}%"
                                   f"{' [swapped]' if swap else ''}"),
                    })

        if not candidates:
            logger.debug(
                f"live_value: нет кандидатов (phase={slow_phase_num}, "
                f"only={only}, swap={swap}, "
                f"min_edge={min_edge*100:.1f}%)"
            )
            return None

        # ── Верхний порог edge ──
        # Всё выше max_edge — не value, а:
        #   - slow догнал fast, но ещё не прислал апдейт
        #   - рынок закрывается
        #   - рассинхрон данных
        clean = [c for c in candidates if c['edge'] <= max_edge]
        if not clean:
            logger.info(
                f"LV: skip — все edge > {max_edge*100:.0f}% "
                f"(кэф устарел, не value): "
                + ", ".join(
                    f"{c['market']} {c['side']} +{c['edge']*100:.0f}%"
                    for c in candidates[:3]
                )
            )
            return None

        best = max(clean, key=lambda c: c['edge'])
        logger.info(
            f"💎 live_value best: {best['market']} {best['side']} "
            f"@ {best['odd']:.2f} (fast {best['fast_odd']:.2f}) "
            f"edge={best['edge']*100:.1f}% "
            f"[swap={swap}]"
        )
        return best

    # ============================================================
    # Монитор
    # ============================================================
    async def _monitor_loop(self, key: str, match_id: str, payload: dict,
                            strategy: dict, handler: BookmakerHandler):
        page = self._pages.get(key)
        if not page:
            logger.error(f"❌ MONITOR START: нет страницы для {key}")
            await self._cleanup_monitoring(key)
            return

        profile_id = strategy.get('profile_id')
        fast_bk = _normalize_bk(payload.get('fast_bk'))
        slow_bk = _normalize_bk(payload.get('slow_bk'))

        logger.info(
            f"▶️ MONITOR START key={key} profile={profile_id} "
            f"bk={slow_bk} fast_bk={fast_bk} url={page.url[:100]}"
        )

        verify_seconds = strategy.get('verify_seconds', 3.0)
        max_bets = strategy.get('max_bets_per_match', 1)
        max_bets_per_phase = strategy.get('max_bets_per_phase', 1)
        signal_time = self._monitoring_signal_time.get(key, time.time())
        event = asyncio.Event()

        on_update_count = 0
        last_callback_at = time.time()

        # ── Плавающий таймаут монитора ──
        # Отсчитывается не от открытия, а от последнего on_update,
        # в котором сигнал был валиден. Иначе на активном матче
        # монитор закроется через 40с, хотя задержка ещё есть.
        monitor_deadline = time.time() + MONITOR_MAX_LIFETIME

        async def on_update(data: dict):
            nonlocal on_update_count, last_callback_at, monitor_deadline
            on_update_count += 1
            last_callback_at = time.time()

            if not self._monitoring_active.get(key, False):
                logger.info(f"⏹ on_update: мониторинг {key} неактивен")
                return

            data_mid = data.get('match_id')
            if data_mid is not None and str(data_mid) != str(match_id):
                logger.info(f"⏭ on_update: чужой match_id {data_mid} ≠ {match_id}")
                return

            self._last_update_time[key] = time.time()
            self._last_slow_data[key] = data

            # ── Единый источник fast-данных: fast-client (приоритет), бэкенд (fallback) ──
            # Раньше тут был только _get_fast_state (бэкенд, отстаёт на 1-2 сек).
            # В момент смены партии бэкенд ещё отдавал sub=12:10 (3-я),
            # а fast-client уже 0:0 (4-я). _choose_best_bet на бэкенде
            # видел "партия завершена" → ставил неправильно.
            fast_sub_tuple = self._get_fast_sub_for_after_goal(key, payload)
            fast_sub = [fast_sub_tuple[0], fast_sub_tuple[1]]
            fast_phase_from_client = int(fast_sub_tuple[2] or 0)

            # fast_score (счёт партий) пока берём из бэкенда — fast-client
            # не всегда его отдаёт корректно. После парсинга sub из fast-client,
            # score можно тоже уточнить (в _fast_markets есть score1/score2).
            fast_entry = self._get_fast_markets(key)
            if fast_entry:
                fast_score = [int(fast_entry.get("score1") or 0),
                              int(fast_entry.get("score2") or 0)]
            else:
                fast_state = self._get_fast_state(key, fast_bk)
                if fast_state:
                    fast_score = [int(fast_state.get("score1") or 0),
                                  int(fast_state.get("score2") or 0)]
                else:
                    fast_score = [0, 0]

            # Для передачи в _verify_delay_still_open оставляем fast_state
            # (там тоже нужно будет обновить — но это следующая итерация)
            fast_state = self._get_fast_state(key, fast_bk)

            slow_sub = [data.get("sub_score1", 0) or 0,
                        data.get("sub_score2", 0) or 0]
            slow_score = [data.get("score1", 0) or 0,
                          data.get("score2", 0) or 0]

            logger.info(
                f"🎬 on_update #{on_update_count} key={key} "
                f"slow_bk={slow_bk} fast_bk={fast_bk} "
                f"fast_score={fast_score} slow_score={slow_score} "
                f"fast_sub={fast_sub} slow_sub={slow_sub} "
                f"fast_available={bool(fast_state)} "
                f"fast_phase={payload.get('fast_phase')!r} "
                f"fast_prev_phase={payload.get('fast_prev_phase')!r} "
                f"slow_phase_num={data.get('phase_num')} "
                f"set_markets_keys={list((data.get('set_markets') or {}).keys())}"
            )

            if self._bets_by_match.get(key, 0) >= max_bets:
                logger.info(f"✅ on_update: лимит ставок исчерпан ({max_bets})")
                event.set()
                return

            elapsed = time.time() - signal_time
            if elapsed < verify_seconds:
                logger.info(
                    f"⏸ on_update: verify_seconds ({elapsed:.1f} из {verify_seconds})"
                )
                return

            current_payload = self._monitoring_payloads.get(key, payload)
            current_strategy = self._monitoring_strategies.get(key, strategy)
            strategy_type = (current_strategy.get('type') or '').lower()

            # ── Синхронизация фазы: если fast-client дал свежую фазу — используем её ──
            # Иначе _choose_best_bet сравнивает sub из fast-client с phase
            # из бэкенда (устаревшая). Например: sub=0:0 (4-я началась),
            # phase=3 (бэкенд отстал) → _resolve_phase_context даст
            # fast=3, slow=3, sub=0:0 vs slow_sub — сравнение несогласованно.
            if fast_phase_from_client > 0:
                current_payload = dict(current_payload)
                current_payload['fast_phase'] = f"{fast_phase_from_client}-я партия"

            teams_ui = current_payload.get('match_teams', ['?', '?'])

            if strategy_type == 'live-value':
                best_bet = self._choose_live_value_bet(
                    key, data, current_strategy, current_payload
                )
                if not best_bet:
                    return

                # Продлеваем монитор, пока есть валидный edge
                monitor_deadline = time.time() + MONITOR_MAX_LIFETIME

                log_key = (
                    best_bet['market'],
                    best_bet['side'],
                    round(best_bet['odd'], 2),
                )
                prev_log_key = self._last_ui_state.get(key)
                if prev_log_key != log_key:
                    self._last_ui_state[key] = log_key
                    log_bus.match_event(
                        bk=slow_bk, match_id=match_id, teams=teams_ui,
                        message=f"💎 LV: {best_bet['market']} {best_bet['side']} "
                                f"@ {best_bet['odd']:.2f} "
                                f"(fast {best_bet.get('fast_odd', 0):.2f}) "
                                f"edge +{best_bet['edge']*100:.1f}%",
                        level="success",
                    )
            else:
                is_open, reason = self._verify_delay_still_open(
                    data, current_payload, current_strategy, fast_state,
                    fast_sub_override=fast_sub,
                    fast_phase_override=fast_phase_from_client,
                )
                logger.info(f"🔍 delay_still_open={is_open} ({reason})")

                # Продлеваем монитор, пока задержка жива
                if is_open:
                    monitor_deadline = time.time() + MONITOR_MAX_LIFETIME

                prev_ui = self._last_ui_state.get(key)
                cur_ui = (tuple(fast_sub), tuple(slow_sub), is_open)
                if prev_ui != cur_ui:
                    self._last_ui_state[key] = cur_ui
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

            phase_key = (key, best_bet['set_number'])
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
                bet_data['fast_odd'] = best_bet.get('fast_odd', 0.0)
                bet_data['min_edge'] = float(
                    current_strategy.get('min_edge_percent', 5.0)
                ) / 100.0
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
                # ── Передаём линию, чтобы place_bet мог проверить,
                #    не переставил ли Marathon селект на соседнюю линию ──
                bet_data['line'] = best_bet.get('line', 0) or 0
                # ── Guard: сохраняем fast_odd и min_edge для проверки
                #    edge после refresh в Marathon.place_bet ──
                if strategy_type == 'live-value':
                    bet_data['fast_odd'] = best_bet.get('fast_odd', 0.0)
                    bet_data['min_edge'] = float(
                        current_strategy.get('min_edge_percent', 5.0)
                    ) / 100.0
            elif bk == 'winline':
                bet_data['outcome_id'] = outcome_info.get('id')
                bet_data['kf'] = best_bet['odd']
            else:
                logger.error(f"Отправка для БК {bk} не реализована")
                return

            logger.info(f"📤 bet_data={bet_data}")

            await self._enqueue_bet(
                profile_id=profile_id,
                key=key,
                match_id=match_id,
                handler=handler,
                page=page,
                bet_data=bet_data,
                bk=bk,
                amount=amount,
                bet_info=best_bet,
                signal_time=time.time(),
            )

            if self._bets_by_match.get(key, 0) >= max_bets:
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

        # ── Периодический re-eval с закэшированным slow_data ──
        async def periodic_reeval():
            while self._monitoring_active.get(key, False):
                try:
                    await asyncio.sleep(2.0)
                    if not self._monitoring_active.get(key, False):
                        break
                    if time.time() - last_callback_at < 1.5:
                        continue
                    last_slow = self._last_slow_data.get(key)
                    if not last_slow:
                        continue
                    await on_update(last_slow)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"periodic_reeval({key}): {e}")

        reeval_task = asyncio.create_task(periodic_reeval())

        try:
            # Ждём либо event.set(), либо истечения monitor_deadline
            # (продлевается на каждом валидном on_update).
            while not event.is_set():
                remaining = monitor_deadline - time.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    await asyncio.wait_for(
                        event.wait(), timeout=min(remaining, 2.0)
                    )
                    break
                except asyncio.TimeoutError:
                    continue
            logger.info(f"⏹️ MONITOR END key={key} причина=event.set() "
                        f"on_updates={on_update_count}")
        except asyncio.TimeoutError:
            logger.info(f"⏰ MONITOR END key={key} "
                        f"причина=timeout({MONITOR_MAX_LIFETIME:.0f}s) "
                        f"on_updates={on_update_count}")
        except asyncio.CancelledError:
            logger.info(f"🛑 MONITOR END key={key} причина=cancelled "
                        f"on_updates={on_update_count}")
            raise
        finally:
            if reeval_task and not reeval_task.done():
                reeval_task.cancel()
                try:
                    await reeval_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self._cleanup_monitoring(key)