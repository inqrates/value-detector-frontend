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
    OnewinHandler,
)
from core.browser_launcher import PatchrightBrowser
from core.fast_client import (
    get_fast_client, has_fast_client, shutdown_all as fast_clients_shutdown,
)
from core.fast_config import (
    is_fast_configured, get_fast_profile_id,
)
from .paths import get_app_data_dir
from .after_goal.match_urls import build_match_url
from .config_loader import load_config
from ui.log_bus import log_bus
from core.names import normalize_bk, normalize_name, transliterate
from .after_goal import sports

import requests


AFTER_GOAL_TIMEOUT = 25
MONITOR_MAX_LIFETIME = 40.0
AFTER_GOAL_COOLDOWN = 10

# ── Максимальная реалистичная задержка fast→slow в очках ──
# Реальная задержка: НТ 1-3 очка, волейбол 2-4, баскетбол 4-8.
# Если diff БОЛЬШЕ этой величины — это не задержка, а устаревший
# кэш slow (WS не обновился, HTTP-снапшот не пришёл).
# После такой "ставки" BK отвечает 51091 "Недоступен исход".
# Значение 5 покрывает 99% валидных задержек в НТ/волейболе;
# для баскетбола можно поставить 8, если будут пропускаться.
MAX_PLAUSIBLE_DELAY_POINTS = 5

logger = logging.getLogger(__name__)

_aux_logger = logging.getLogger('ui.after_goal_engine.aux')
_aux_logger.setLevel(logging.WARNING)


def _normalize_bk(name: str) -> str:
    return normalize_bk(name)


def _make_key(match_id: str, profile_id: str) -> str:
    """Составной ключ стейта: match_id + profile_id."""
    return f"{match_id}__{profile_id}"


def _match_id_from_key(key: str) -> str:
    """Достаёт match_id из составного ключа."""
    if "__" in key:
        return key.split("__", 1)[0]
    return key


def _transliterate_key(name: str) -> str:
    """Оставлено для совместимости — реализация в core.names."""
    return transliterate(name)


def _normalize_key_name(name: str) -> str:
    """Единая нормализация (core.names) — см. шапку модуля names."""
    return normalize_name(name)

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
    "onewin": OnewinHandler,
}

INSTANCE_BASED_HANDLERS = {
    BetcityHandler, OlimpHandler, FonbetHandler, SportbetHandler,
    ZenitHandler, LeonHandler, MarathonHandler, WinlineHandler, LigaStavokHandler,
    PariHandler, OnewinHandler,
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
    "onewin":     {"table_tennis", "volleyball", "basketball", "cyber_basketball"},
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
    "onewin":     20.0,
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
    "onewin":     3000,
}
LOAD_WAIT_DEFAULT = 500

MAX_ACCEPTABLE_DELAY = 30.0

PHASE_END_BY_SCORE = sports.PHASE_END_BY_SCORE
PHASE_END_BY_TIME = sports.PHASE_END_BY_TIME


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
    'onewin': {
        'table_tennis': 'https://one-vv7078.com/betting/live',
        'volleyball': 'https://one-vv7078.com/betting/live',
        'basketball': 'https://one-vv7078.com/betting/live',
        'cyber_basketball': 'https://one-vv7078.com/betting/live',
        'any': 'https://one-vv7078.com/betting/live',
    },
}


# ── Маппинг код manual_markets → базовый рынок (для порогов) ──
_MARKET_CODE_TO_BASE = sports.MARKET_CODE_TO_BASE


class AfterGoalEngine:
    PROFILE_LAUNCH_COOLDOWN = 30.0
    PREOPEN_COOLDOWN = 15.0

    def __init__(self, signal_client=None):
        # ── НОВОЕ: WS-клиент для получения match_state пушем ──
        self._signal_client = signal_client

        # ── ВАЖНО: все state-словари (кроме _profile_active_matches,
        # _profile_locks, _profile_launch_attempts, _bet_queues,
        # _queue_workers) ключуются СОСТАВНЫМ ключом
        # f"{match_id}__{profile_id}", чтобы один и тот же матч
        # мог параллельно мониториться несколькими стратегиями/профилями.
        self._browsers: Dict[str, PatchrightBrowser] = {}
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
        # ── ФИКС 2026-10-29: Winline — 1 активный матч на профиль.
        # Один сигнал "уже открыт", второй — "навигация в процессе"
        # (защита от race condition при 2 сигналах подряд).
        self._winline_preopen_in_progress: set = set()

        self._profile_active_matches: Dict[str, int] = {}
        self._bet_queues: Dict[str, asyncio.Queue] = {}
        self._queue_workers: Dict[str, asyncio.Task] = {}
        self._bets_enqueued: set = set()

        self._fast_state: Dict[str, dict] = {}
        self._fast_poll_tasks: Dict[str, asyncio.Task] = {}
        self._ws_key_to_internal: Dict[str, str] = {}
        self._fast_markets_poll_tasks: Dict[str, asyncio.Task] = {}

        self._fast_browser: Optional[PatchrightBrowser] = None
        self._fast_profile_lock = asyncio.Lock()
        self._fast_pages: Dict[str, Page] = {}
        self._fast_handlers: Dict[str, BookmakerHandler] = {}
        self._fast_monitor_tasks: Dict[str, asyncio.Task] = {}

        self._last_ui_state: Dict[str, tuple] = {}
        self._bets_diag_count = 0
        self._recent_bets: Dict[tuple, float] = {}
        self._recent_bets_ttl = 30.0   # секунд — окно защиты от дублей        

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
                f"Не удалось открыть профиль {profile_id}. "
                f"Проверьте, что Patchright установлен."
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
                    log_bus.warning("Браузер",
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
                logger.info(f"✅ Профиль {profile_id} закрыт (Patchright stop)")
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
        # ── SSL: доверяем нашему CA (certs/ca.pem), если есть ──
        try:
            from ui.ssl_cert import build_requests_verify
            _verify = build_requests_verify()
        except Exception:
            _verify = True

        while self._monitoring_active.get(key, False):
            try:
                resp = await asyncio.to_thread(
                    requests.get, url, params=params, timeout=3,
                    verify=_verify,
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

                    new_sub1 = int(result.get("sub_score1", 0) or 0)
                    new_sub2 = int(result.get("sub_score2", 0) or 0)
                    new_score1 = int(result.get("score1", 0) or 0)
                    new_score2 = int(result.get("score2", 0) or 0)
                    new_phase = int(result.get("phase_num", 0) or 0)
                    old_phase = int(state.get("markets_phase", 0) or 0)

                    # ── SANITY: не принимать пару (новая фаза + sub от
                    #    завершённой партии). Это признак того, что
                    #    fast-клиент прислал промежуточный фрейм, где
                    #    parts.length уже N+1, а parts[-1] ещё старый.
                    #    Такой фрейм дал бы: phase=3, sub=13:11 → движок
                    #    поставил бы winner на уже завершённую партию. ──
                    if (new_phase != old_phase and new_phase > 0
                            and old_phase > 0):
                        sport_low = (sport or "").lower()
                        thr = 0
                        if sport_low == "table_tennis":
                            thr = 11
                        elif sport_low in ("volleyball", "beach_volleyball"):
                            thr = 15 if new_phase >= 5 else 25
                        if thr > 0:
                            s1_done = (new_sub1 >= thr
                                       and new_sub1 - new_sub2 >= 2)
                            s2_done = (new_sub2 >= thr
                                       and new_sub2 - new_sub1 >= 2)
                            if s1_done or s2_done:
                                logger.info(
                                    f"⏭ FAST_MARKETS[{key}] {fast_bk}: "
                                    f"смена фазы {old_phase}→{new_phase}, "
                                    f"но sub={new_sub1}:{new_sub2} — "
                                    f"похоже на завершённую партию. "
                                    f"Пропускаю фрейм."
                                )
                                await asyncio.sleep(1)
                                continue

                    # ── СМЕНА ФАЗЫ: обновляем ВСЁ принудительно, даже нулями ──
                    # Если phase_num изменился — значит началась новая партия.
                    # Её sub = 0:0 (или маленький), и это ПРАВИЛЬНО.
                    # Логика "не перетирать нулями" здесь ломала бы картину:
                    # phase=7, sub=11:7 (от завершённой 6-й партии).
                    if new_phase != old_phase and new_phase > 0:
                        state["markets_sub1"] = new_sub1
                        state["markets_sub2"] = new_sub2
                        state["markets_score1"] = new_score1
                        state["markets_score2"] = new_score2
                        logger.info(
                            f"🔄 FAST_MARKETS[{key}] смена фазы "
                            f"{old_phase} → {new_phase}: "
                            f"sub={new_sub1}:{new_sub2} "
                            f"score={new_score1}:{new_score2}"
                        )
                    else:
                        # ── Та же фаза: старая логика с защитой от нулей ──
                        old_sub1 = int(state.get("markets_sub1", 0) or 0)
                        old_sub2 = int(state.get("markets_sub2", 0) or 0)
                        if (new_sub1 or new_sub2) or not (old_sub1 or old_sub2):
                            state["markets_sub1"] = new_sub1
                            state["markets_sub2"] = new_sub2

                        old_score1 = int(state.get("markets_score1", 0) or 0)
                        old_score2 = int(state.get("markets_score2", 0) or 0)
                        if (new_score1 or new_score2) or not (old_score1 or old_score2):
                            state["markets_score1"] = new_score1
                            state["markets_score2"] = new_score2

                    state["markets"] = result.get("set_markets", {})
                    state["markets_phase"] = new_phase
                    state["markets_match_id"] = result.get("match_id")
                    state["markets_fetched_at"] = time.time()
                    # ── НОВОЕ: prev_sub из fast-клиента ──
                    state["markets_prev_sub1"] = int(
                        result.get("prev_sub_score1", 0) or 0)
                    state["markets_prev_sub2"] = int(
                        result.get("prev_sub_score2", 0) or 0)
                    state["markets_prev_phase"] = int(
                        result.get("prev_phase_num", 0) or 0)

                    logger.debug(
                        f"📡 FAST_MARKETS[{key}] {fast_bk} "
                        f"phase={result.get('phase_num')} "
                        f"sets={list(result.get('set_markets', {}).keys())}"
                    )
                else:
                    fail_count += 1

                    # ── ФИКС: fast-клиент вернул None (партия завершена) —
                    #    инвалидируем кэш, чтобы _get_fast_markets не
                    #    отдал устаревшую рассогласованную пару
                    #    (phase=3, sub=13:11 от завершённой 2-й). ──
                    state = self._fast_state.get(key)
                    if state is not None:
                        _old_phase = state.get("markets_phase", 0)
                        _old_sub1 = state.get("markets_sub1", 0)
                        _old_sub2 = state.get("markets_sub2", 0)
                        if _old_phase or _old_sub1 or _old_sub2:
                            logger.info(
                                f"⚠️ FAST_MARKETS[{key}] {fast_bk}: "
                                f"fast-клиент вернул None — сбрасываю кэш "
                                f"(было phase={_old_phase} "
                                f"sub={_old_sub1}:{_old_sub2})"
                            )
                        state["markets"] = {}
                        state["markets_phase"] = 0
                        state["markets_sub1"] = 0
                        state["markets_sub2"] = 0
                        state["markets_score1"] = 0
                        state["markets_score2"] = 0
                        state["markets_fetched_at"] = 0

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
        if time.time() - fetched_at > 2.0:
            return None
        return {
            "set_markets": entry.get("markets") or {},
            "phase_num":   entry.get("markets_phase", 0),
            "score1":      entry.get("markets_score1", 0),
            "score2":      entry.get("markets_score2", 0),
            "sub_score1":  entry.get("markets_sub1", 0),
            "sub_score2":  entry.get("markets_sub2", 0),
            "match_id":    entry.get("markets_match_id"),
            # ── НОВОЕ ──
            "prev_sub_score1": entry.get("markets_prev_sub1", 0),
            "prev_sub_score2": entry.get("markets_prev_sub2", 0),
            "prev_phase_num":  entry.get("markets_prev_phase", 0),
        }


    # ============================================================
    # WS-пуш: подписка на match_state вместо REST polling
    # ============================================================
    def _make_ws_key(self, payload: dict) -> str:
        sport = (payload.get('sport') or 'table_tennis').lower()
        teams = payload.get('match_teams') or ['', '']
        
        # Используем ЛОКАЛЬНУЮ функцию нормализации
        p1 = _normalize_key_name(teams[0])
        p2 = _normalize_key_name(teams[1])
        
        if p1 > p2:
            p1, p2 = p2, p1
        return f"{sport}::{p1}||{p2}"

    def _subscribe_fast_state(self, key: str):
        if self._signal_client is not None:
            payload = self._monitoring_payloads.get(key)
            if not payload:
                return
            ws_key = self._make_ws_key(payload)
            self._ws_key_to_internal[ws_key] = key
            try:
                self._signal_client.subscribe_match(ws_key)
                logger.info(f"🔔 Подписка на WS по ключу: {ws_key}")
            except Exception as e:
                logger.debug(f"subscribe_match({ws_key}): {e}")

    def _monitoring_key_for_match(self, payload: dict) -> Optional[str]:
        mid = payload.get('match_id')
        if not mid:
            return None
        for k, p in self._monitoring_payloads.items():
            if p.get('match_id') == mid:
                return k
        return None

    def _unsubscribe_fast_state(self, key: str):
        if self._signal_client is not None:
            for ws_key, int_key in list(self._ws_key_to_internal.items()):
                if int_key == key:
                    self._ws_key_to_internal.pop(ws_key, None)
                    try:
                        self._signal_client.unsubscribe_match(ws_key)
                    except Exception as e:
                        logger.debug(f"unsubscribe_match({ws_key}): {e}")

    def on_match_state(self, data: dict):
        ws_key = data.get("key")
        if not ws_key:
            return
            
        key = self._ws_key_to_internal.get(ws_key)
        if not key:
            return

        if not self._monitoring_active.get(key, False):
            return
            
        bks = data.get("bks") or {}
        state = self._fast_state.setdefault(key, {})
        
        # ── КЭШИРУЕМ PREV_SUB ДЛЯ AFTER-GOAL ──
        payload = self._monitoring_payloads.get(key, {})
        fast_bk = _normalize_bk(payload.get('fast_bk'))
        
        if fast_bk and fast_bk in bks:
            new_bk_data = bks[fast_bk]
            old_bks = state.get("bks", {})
            old_bk_data = old_bks.get(fast_bk, {})
            
            old_phase = (old_bk_data.get("raw_time") or "").strip()
            new_phase = (new_bk_data.get("raw_time") or "").strip()
            
            # Если фаза изменилась — запоминаем счет СТАРОЙ партии
            if old_phase and new_phase and old_phase != new_phase:
                old_sub1 = int(old_bk_data.get("sub_score1") or 0)
                old_sub2 = int(old_bk_data.get("sub_score2") or 0)
                if old_sub1 > 0 or old_sub2 > 0:
                    state["ws_prev_sub1"] = old_sub1
                    state["ws_prev_sub2"] = old_sub2
                    logger.info(f"📡 WS: Смена фазы '{old_phase}' -> '{new_phase}'. Prev_sub: {old_sub1}:{old_sub2}")

        state["bks"] = bks
        state["fetched_at"] = time.time()
        state["now_on_backend"] = data.get("now")
        state.pop("not_found", None)


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
        Берет данные ТОЛЬКО из WS-пуша (без HTTP клиентов).
        Возвращает: (sub1, sub2, phase_num, prev_sub1, prev_sub2)
        """
        fast_bk = _normalize_bk(payload.get("fast_bk"))
        state = self._fast_state.get(key, {})
        bk_data = (state.get("bks") or {}).get(fast_bk)
        
        if not bk_data:
            return (0, 0, 0, 0, 0)
            
        sub1 = int(bk_data.get("sub_score1") or 0)
        sub2 = int(bk_data.get("sub_score2") or 0)
        
        raw_time = (bk_data.get("raw_time") or "").strip()
        phase_num = self._parse_phase_num(raw_time)
        if phase_num <= 0:
            s1 = int(bk_data.get("score1") or 0)
            s2 = int(bk_data.get("score2") or 0)
            phase_num = s1 + s2 + 1
            
        prev1 = int(state.get("ws_prev_sub1") or 0)
        prev2 = int(state.get("ws_prev_sub2") or 0)
        
        return (sub1, sub2, phase_num, prev1, prev2)

    # ============================================================
    # Пороги отставания по рынкам
    # ============================================================
    def _get_market_threshold(self, strategy: dict, market: str) -> int:
        return sports.get_market_threshold(strategy, market)

    def _get_min_threshold(self, strategy: dict) -> int:
        """
        Минимальный порог по всем рынкам, которые стратегия реально использует.

        manual — берём пороги только по рынкам, отмеченным в manual_markets.
        auto   — минимум по всем четырём базовым рынкам.
        """
        return sports.get_min_threshold(strategy)

    # ============================================================
    # Рынки manual_markets ↔ внутренний формат
    # ============================================================
    @staticmethod
    def _market_code(market: str, side: str) -> Optional[str]:
        """(market, side) → код в manual_markets."""
        return sports.market_code(market, side)

    def _is_market_allowed(self, market: str, side: str,
                           manual: set) -> bool:
        """Разрешён ли исход по настройкам manual_markets."""
        return sports.is_market_allowed(market, side, manual)

    # ============================================================
    # RE-CHECK задержки прямо сейчас
    # ============================================================
    def _verify_delay_now(self, key: str, strategy: dict,
                          market: Optional[str] = None) -> Tuple[bool, str]:
        # Получаем данные ИЗ WS-ПУША (единый источник)
        state = self._fast_state.get(key, {})
        bks = state.get("bks", {})
        
        payload = self._monitoring_payloads.get(key)
        if not payload:
            return False, "нет payload"

        fast_bk = _normalize_bk(payload.get('fast_bk'))
        slow_bk = _normalize_bk(payload.get('slow_bk'))
        
        fast_data = bks.get(fast_bk)
        slow_data = bks.get(slow_bk)

        if not fast_data or not slow_data:
            missing = []
            if not fast_data:
                missing.append(f"fast={fast_bk}")
            if not slow_data:
                missing.append(f"slow={slow_bk}")
            return False, f"retryable: нет WS-данных ({', '.join(missing)})"

        # Берем счет из WS
        f_score1 = int(fast_data.get("score1") or 0)
        f_score2 = int(fast_data.get("score2") or 0)
        f_sub1 = int(fast_data.get("sub_score1") or 0)
        f_sub2 = int(fast_data.get("sub_score2") or 0)
        
        s_score1 = int(slow_data.get("score1") or 0)
        s_score2 = int(slow_data.get("score2") or 0)
        s_sub1 = int(slow_data.get("sub_score1") or 0)
        s_sub2 = int(slow_data.get("sub_score2") or 0)

        # Считаем прогресс партии (Fast vs Slow)
        f_phase = self._parse_phase_num(fast_data.get("raw_time", ""))
        if f_phase <= 0:
            f_phase = f_score1 + f_score2 + 1
            
        s_phase = self._parse_phase_num(slow_data.get("raw_time", ""))
        if s_phase <= 0:
            s_phase = s_score1 + s_score2 + 1

        if f_phase < s_phase:
            return False, f"fast отстает от slow по фазе (f={f_phase}, s={s_phase})"

        # Если партии разные — отличная задержка (смена сета/партии)
        if f_phase > s_phase:
            return True, f"Задержка по фазе: fast={f_phase}, slow={s_phase}"

        # Если партия одна — сравниваем очки
        diff = max(f_sub1 - s_sub1, f_sub2 - s_sub2)
        min_t = self._get_market_threshold(strategy, market) if market else self._get_min_threshold(strategy)
        
        if diff >= min_t:
            return True, f"Задержка по очкам: +{diff} (fast {f_sub1}:{f_sub2} / slow {s_sub1}:{s_sub2})"
            
        return False, f"Задержки нет (diff={diff} < {min_t})"

    def _verify_live_value_now(self, key: str, strategy: dict) -> Tuple[bool, str]:
        payload = self._monitoring_payloads.get(key)
        if not payload:
            return False, "нет payload"

        # 1) Свежие fast_markets
        fast_entry = self._get_fast_markets(key)
        if not fast_entry:
            return False, "fast_markets протухли (>2с)"

        # 2) Свежий slow_data
        slow_data = self._last_slow_data.get(key)
        if not slow_data:
            return False, "нет slow_data"

        slow_age = time.time() - self._last_update_time.get(key, 0)
        if slow_age > SLOW_DATA_STALE_SEC:
            return False, f"slow_data старше {slow_age:.1f}с"

        # 3) Пересчитываем edge
        try:
            fresh_best = self._choose_live_value_bet(key, slow_data, strategy, payload)
        except Exception as e:
            logger.debug(f"_verify_live_value_now: recalc: {e}")
            return False, f"ошибка пересчёта: {e}"

        if not fresh_best:
            return False, "edge пропал (кэфы сошлись)"

        return True, f"edge +{fresh_best['edge']*100:.1f}%"

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
    async def _ensure_fast_browser(self) -> Optional[PatchrightBrowser]:
        if not is_fast_configured():
            logger.warning("[FAST-TAB] fast-профиль не настроен")
            return None

        profile_id = get_fast_profile_id()
        

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
                wrapper = PatchrightBrowser(
                    profile_id=profile_id,
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

        # ── ФИКС 2026-10-19: только LigaStavokHandler принимает sport=.
        # Для всех остальных (Leon, Zenit, Marathon, Winline, …) это
        # был TypeError → fast-tab падал с "unexpected keyword argument 'sport'"
        # и live-value на этих БК вообще не запускался.
        handler_kwargs = {
            "target_match_id": match_id,
            "target_teams": [player1, player2],
        }
        if handler_cls is LigaStavokHandler:
            handler_kwargs["sport"] = sport
        handler = handler_cls(**handler_kwargs)
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
        
        # ── ЖЁСТКАЯ ПРОВЕРКА СПОРТА ИЗ СТРАТЕГИИ ──
        # Если в настройках стратегии выбран конкретный вид спорта
        # (например, "table_tennis"), мы не имеем права открывать
        # матчи других видов (basketball, cyber_basketball и т.д.)
        strat_sport = (strategy.get('sport') or 'any').lower()
        if strat_sport != 'any' and strat_sport != sport:
            logger.info(
                f"🔴 PREOPEN SKIP {key}: спорт сигнала '{sport}' "
                f"не совпадает со спортом стратегии '{strat_sport}'"
            )
            return
        
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

        # ── ФИКС 2026-10-29: Winline разлогинивает при ЛЮБОЙ второй
        # вкладке. Матч открываем в ГЛАВНОЙ вкладке профиля — значит
        # второй одновременный матч её перезапишет → убьёт первый.
        # Держим: 1 активный матч + защиту от race condition (пока
        # первый ещё не установил _monitoring_profiles, второй может
        # проскочить по winline_active==0).
        if slow_bk == "winline":
            winline_active = sum(
                1 for k, pid in self._monitoring_profiles.items()
                if pid == profile_id
                and _normalize_bk(
                    self._monitoring_payloads.get(k, {}).get('slow_bk') or ''
                ) == 'winline'
            )
            if winline_active >= 1:
                logger.warning(
                    f"🔴 PREOPEN SKIP {key}: Winline уже открыт в профиле "
                    f"{profile_id} (1 матч за раз). Новый матч откроется, "
                    f"когда текущий завершится."
                )
                return
            if profile_id in self._winline_preopen_in_progress:
                logger.warning(
                    f"🔴 PREOPEN SKIP {key}: Winline в профиле {profile_id} "
                    f"уже открывается (навигация в процессе)"
                )
                return
            self._winline_preopen_in_progress.add(profile_id)

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
            # ── ФИКС 2026-10-29: снять флаг Winline, иначе при
            # следующем сигнале сработает "уже открывается" навсегда.
            if slow_bk == "winline":
                self._winline_preopen_in_progress.discard(profile_id)
            return

        
        if slow_bk == 'winline':
            page = browser_wrapper.page
            setattr(page, '_is_main_page', True)
            logger.info(
                f"📄 Winline: используем главную вкладку профиля key={key}"
            )
        else:
            try:
                page = await browser_wrapper.new_page()
                logger.info(f"📄 Открыта отдельная вкладка для key={key}")
            except Exception as e:
                logger.error(f"Не удалось открыть новую вкладку: {e} — используем главную")
                page = browser_wrapper.page

        # ── ДОБАВИТЬ ЭТО: резервируем слот СРАЗУ, чтобы бот не открывал новые ──
        self._monitoring_profiles[key] = profile_id
        self._profile_active_matches[profile_id] = active + 1

        player1, player2 = payload.get('match_teams', ['', ''])

        # ── sport принимает ТОЛЬКО LigaStavokHandler ──
        # У остальных handler'ов нет такого параметра — если передать,
        # будет TypeError и вкладка останется на about:blank.
        handler_kwargs = {
            "target_match_id": match_id,
            "target_teams": [player1, player2],
        }
        if handler_cls is LigaStavokHandler:
            handler_kwargs["sport"] = sport
        handler = handler_cls(**handler_kwargs)
        self._handlers[key] = handler

        if hasattr(handler, "prepare_page"):
            try:
                await handler.prepare_page(page)
            except Exception as e:
                logger.error(f"prepare_page для {slow_bk} упал: {e}")
                self._handlers.pop(key, None)
                # ── Winline: не закрываем главную, вернём на live ──
                if getattr(page, '_is_main_page', False):
                    try:
                        _live_url_p = (
                            LIVE_URLS.get(slow_bk, {}).get('any')
                            or LIVE_URLS.get(slow_bk, {}).get('table_tennis')
                        )
                        if _live_url_p and not page.is_closed():
                            await page.goto(
                                _live_url_p,
                                wait_until="domcontentloaded",
                                timeout=15000,
                            )
                            logger.info(
                                f"↩️ [{slow_bk}] prepare_page упал — "
                                f"главная возвращена на live"
                            )
                    except Exception as _e:
                        logger.warning(f"Возврат главной [{slow_bk}]: {_e}")
                else:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if slow_bk == "winline":
                    self._winline_preopen_in_progress.discard(profile_id)
                return

        match_url = payload.get('match_url') or build_match_url(slow_bk, payload)
        url_ok = False

        if slow_bk == "ligastavok" and match_url:
            try:
                cookies = await page.context.cookies()
                cookie_names = {
                    (c.get("name") or "").lower() for c in cookies
                    if "ligastavok" in (c.get("domain") or "")
                }
                has_qrator_jsr = "qrator_jsr" in cookie_names
                if not has_qrator_jsr:
                    logger.info(
                        "LigaStavok: нет qrator_jsr cookie — "
                        "прогреваю через /live (до 24с)"
                    )
                    try:
                        await page.goto(
                            "https://www.ligastavok.ru/live",
                            wait_until="domcontentloaded",
                            timeout=25000,
                        )
                        # ── ФИКС 2026-10-19: ждём не фикс 2.5с, а пока
                        # Qrator реально не выдаст qrator_jsr. Иначе
                        # страница приходит пустой (body_len=0), игроки
                        # не находятся, preopen падает.
                        passed = False
                        for attempt in range(8):  # 8 × 2с = 16с (достаточно, чтобы прогрузился WS)
                            await page.wait_for_timeout(2000)
                            cur = await page.context.cookies()
                            cur_names = {
                                (c.get("name") or "").lower() for c in cur
                                if "ligastavok" in (c.get("domain") or "")
                            }
                            if "qrator_jsr" in cur_names:
                                logger.info(
                                    f"LigaStavok: Qrator пройден "
                                    f"за {(attempt + 1) * 2}с"
                                )
                                passed = True
                                break
                            cur_names = {
                                (c.get("name") or "").lower() for c in cur
                                if "ligastavok" in (c.get("domain") or "")
                            }
                            if "qrator_jsr" in cur_names:
                                logger.info(
                                    f"LigaStavok: Qrator пройден "
                                    f"за {(attempt + 1) * 2}с"
                                )
                                passed = True
                                break
                        if not passed:
                            logger.warning(
                                "LigaStavok: Qrator не пройден за 60с — "
                                "страница может быть пустой"
                            )
                    except Exception as e:
                        logger.warning(f"LigaStavok: прогрев не удался: {e}")
                else:
                    logger.debug("LigaStavok: qrator_jsr уже есть")
            except Exception as e:
                logger.debug(f"LigaStavok: проверка cookies: {e}")

        if match_url:
            try:
                # ── Betcity: прогрев через главную (SPA иногда отдаёт
                # пустое body при прямом заходе по URL) ──
                if slow_bk == "betcity":
                    try:
                        await page.goto(
                            "https://betcity.ru/ru/live/table-tennis",
                            wait_until="domcontentloaded", timeout=15000,
                        )
                        await page.wait_for_timeout(2500)
                    except Exception as e:
                        logger.debug(f"Betcity: прогрев главной: {e}")

                await page.goto(match_url, wait_until="domcontentloaded",
                                timeout=AFTER_GOAL_TIMEOUT * 1000)

                load_wait_ms = LOAD_WAIT_BY_BK.get(slow_bk, LOAD_WAIT_DEFAULT)
                if slow_bk == "betcity":
                    load_wait_ms = max(load_wait_ms, 5000)
                await page.wait_for_timeout(load_wait_ms)

                found_players = False
                max_attempts = 40 if slow_bk != "betcity" else 100
                for _ in range(max_attempts):
                    if page.is_closed():
                        return
                    if await self._check_players_on_page(page, player1, player2):
                        found_players = True
                        break
                    await page.wait_for_timeout(300)

                if found_players:
                    logger.info(f"✅ Перешли по URL: {match_url}")
                    log_bus.match_event(
                        bk=slow_bk, match_id=match_id,
                        teams=[player1, player2],
                        message="Открыт по URL",
                        level="info",
                    )
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
                    log_bus.match_event(
                        bk=slow_bk, match_id=match_id,
                        teams=[player1, player2],
                        message=f"Игроки не найдены в DOM (body_len={body_len}) — ищем кликом",
                        level="warning",
                    )
            except Exception as e:
                logger.warning(f"⚠️ URL не сработал ({e}), пробуем клик")
                log_bus.match_event(
                    bk=slow_bk, match_id=match_id,
                    teams=[player1, player2],
                    message=f"URL не сработал ({e}), ищем кликом",
                    level="warning",
                )

        if not url_ok:
            found = await self._click_match_on_page(page, player1, player2)
            if not found:
                logger.error(f"❌ Не удалось открыть матч {player1} vs {player2}")
                log_bus.match_event(
                    bk=slow_bk, match_id=match_id,
                    teams=[player1, player2],
                    message="Не удалось открыть матч",
                    level="error",
                )
                self._handlers.pop(key, None)
                if getattr(page, '_is_main_page', False):
                    try:
                        _live_url_ret = (
                            LIVE_URLS.get(slow_bk, {}).get('any')
                            or LIVE_URLS.get(slow_bk, {}).get('table_tennis')
                        )
                        if _live_url_ret and not page.is_closed():
                            await page.goto(
                                _live_url_ret,
                                wait_until="domcontentloaded",
                                timeout=15000,
                            )
                            logger.info(
                                f"↩️ [{slow_bk}] матч не найден — "
                                f"главная возвращена на live"
                            )
                    except Exception as _e:
                        logger.warning(f"Возврат главной [{slow_bk}]: {_e}")
                else:
                    try:
                        await page.close()
                    except Exception:
                        pass
                
                # ── ДОБАВИТЬ ЭТО: освобождаем слот при ошибке ──
                self._profile_active_matches[profile_id] = max(0, self._profile_active_matches.get(profile_id, 1) - 1)
                self._monitoring_profiles.pop(key, None)
                # ─────────────────────────────────────────────────
                
                if slow_bk == "winline":
                    self._winline_preopen_in_progress.discard(profile_id)
                return
            else:
                log_bus.match_event(
                    bk=slow_bk, match_id=match_id,
                    teams=[player1, player2],
                    message="Найден кликом",
                    level="info",
                )

        # ═══════════════════════════════════════════════════════
        # Marathon: SPA создаёт SSE-подписку ТОЛЬКО после reload.
        # ═══════════════════════════════════════════════════════
        if slow_bk == "marathon":
            try:
                await page.wait_for_timeout(2500)
                got = False
                if hasattr(handler, "has_received_sse"):
                    got = handler.has_received_sse()
                logger.info(f"Marathon: SSE после первой навигации = {got}")

                if not got:
                    logger.info("Marathon: 🔄 reload для активации SSE")
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=AFTER_GOAL_TIMEOUT * 1000,
                    )
                    await page.wait_for_timeout(2500)
                    if hasattr(handler, "has_received_sse"):
                        got = handler.has_received_sse()
                    logger.info(f"Marathon: SSE после reload = {got}")
                    log_bus.match_event(
                        bk=slow_bk, match_id=match_id,
                        teams=[player1, player2],
                        message=f"Marathon: SSE {'активирован' if got else 'НЕ пришёл'} после reload",
                        level="info",
                    )
            except Exception as e:
                logger.warning(f"Marathon: ошибка SSE (возможно page closed): {e}")

        self._pages[key] = page
        self._monitoring_active[key] = True
        self._monitoring_payloads[key] = payload
        self._monitoring_strategies[key] = strategy
        self._monitoring_signal_time[key] = time.time()

        fast_bk = _normalize_bk(payload.get('fast_bk'))   # ← ДОБАВИТЬ ЭТУ СТРОКУ

        if self._signal_client is not None:
            # ── WS-пуш: подписываемся на матч (быстрее REST polling) ──
            self._subscribe_fast_state(key)
            logger.info(f"🔔 Подписка на match_state: key={key}")
        else:
            # ── Fallback: старый REST polling (dry_run_test и т.п.) ──
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
        # Для After-goal HTTP-клиент НЕ НУЖЕН, данные идут по WS.
        # Для Live-Value HTTP-клиент ОБЯЗАТЕЛЕН (нужны кэфы).
        strategy_type = (strategy.get('type') or '').lower()
        
        if strategy_type == 'live-value':
            if fast_bk and fast_bk != slow_bk and has_fast_client(fast_bk):
                logger.info(f"📊 FAST_MARKETS: key={key} slow={slow_bk} fast={fast_bk} (HTTP для LV)")
                self._fast_markets_poll_tasks[key] = asyncio.create_task(
                    self._poll_fast_markets(
                        key=key, match_id=match_id, sport=sport,
                        player1=player1, player2=player2,
                        fast_bk=fast_bk,
                    )
                )
            elif (fast_bk and fast_bk != slow_bk and fast_bk in HANDLERS and is_fast_configured()):
                logger.info(f"📊 FAST_MARKETS: key={key} slow={slow_bk} fast={fast_bk} (TAB в fast-профиле)")
                try:
                    fast_page = await self._open_fast_match(
                        key=key, match_id=match_id, fast_bk=fast_bk,
                        payload=payload, sport=sport,
                    )
                    if fast_page:
                        task = asyncio.create_task(
                            self._monitor_fast_markets_via_handler(
                                key=key, match_id=match_id, fast_bk=fast_bk,
                                payload=payload, strategy=strategy,
                            )
                        )
                        self._fast_monitor_tasks[key] = task
                except Exception as e:
                    logger.warning(f"[FAST-TAB] {fast_bk}: {e}")
            else:
                logger.info(f"📊 FAST_MARKETS: {fast_bk or '—'} — LV пропущен")
        else:
            # Для After-goal ничего не запускаем, данные придут по WS
            logger.info(f"📊 FAST_MARKETS: key={key} fast={fast_bk} (WS-пуш, polling не нужен)")

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

        # ── Сброс флага "Winline в процессе": матч уже в
        # _monitoring_profiles, дальше будет блокировать
        # winline_active >= 1.
        if slow_bk == "winline":
            self._winline_preopen_in_progress.discard(profile_id)

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

    @staticmethod
    def _bet_signature(bet_data: dict) -> tuple:
        """
        Сигнатура ставки для защиты от дублей.
        Две ставки с одинаковой сигнатурой в течение TTL — это дубль.
        """
        return (
            str(bet_data.get("event_id") or ""),
            str(bet_data.get("coefficient_id")
                or bet_data.get("pos") or ""),
            str(bet_data.get("selection_id") or ""),
            str(bet_data.get("line") or ""),
            str(bet_data.get("side") or ""),
            str(bet_data.get("market") or ""),
            str(bet_data.get("set_num") or bet_data.get("set_number") or ""),
        )

    async def _enqueue_bet(self, profile_id: str, key: str, match_id: str,
                           handler, page, bet_data: dict,
                           bk: str, amount: float, bet_info: dict,
                           signal_time: float = None) -> bool:
        """
        Возвращает True, если ставка реально попала в очередь.
        False — если отбита (уже в очереди / дубль / TTL истёк).
        Это нужно, чтобы _monitor_loop мог отличать в bets_diag.log
        реально отправленные ставки от отбитых.
        """
        if key in self._bets_enqueued:
            logger.info(f"⏭ Матч {key} уже в очереди — не дублируем")
            return False
        self._bets_enqueued.add(key)

        # ── ФИКС 2026-10-14: защита от дублей одинаковых ставок ──
        # Если такая же ставка уже отправлялась < TTL секунд назад —
        # это дубль от повторного on_update (пока ключ был снят,
        # но _bets_by_match ещё не увеличился). Пример: diag 4-5-6
        # в bets_diag.log — 3 одинаковых ставки подряд с разницей 2с.
        try:
            _sig = self._bet_signature(bet_data)
            _now = time.time()
            _last = self._recent_bets.get(_sig, 0.0)
            if _now - _last < self._recent_bets_ttl:
                logger.warning(
                    f"⏭ ДУБЛЬ ставки [{bk}] {match_id}: "
                    f"sig={_sig[:3]} уже слали "
                    f"{_now - _last:.1f}с назад — пропускаю"
                )
                self._bets_enqueued.discard(key)
                return False
            self._recent_bets[_sig] = _now

            # Чистка старых
            if len(self._recent_bets) > 500:
                _cutoff = _now - self._recent_bets_ttl * 2
                self._recent_bets = {
                    k: v for k, v in self._recent_bets.items()
                    if v > _cutoff
                }
        except Exception as _e:
            logger.debug(f"dup-guard: {_e}")

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
            return False

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
        return True

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
                    # ── ФИКС: главную вкладку профиля (Winline) не
                    # закрываем — возвращаем на live-раздел. Иначе
                    # профиль останется без вкладки и следующий матч
                    # будет некуда открыть.
                    if getattr(page, '_is_main_page', False):
                        _payload_bw = self._monitoring_payloads.get(key) or {}
                        _bk_bw = _normalize_bk(_payload_bw.get('slow_bk') or '')
                        _live_url = (
                            LIVE_URLS.get(_bk_bw, {}).get('any')
                            or LIVE_URLS.get(_bk_bw, {}).get('table_tennis')
                        )
                        if _live_url:
                            try:
                                await page.goto(
                                    _live_url,
                                    wait_until="domcontentloaded",
                                    timeout=15000,
                                )
                                logger.info(
                                    f"↩️ [{_bk_bw}] главная вкладка "
                                    f"возвращена на live после ставки"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Возврат главной [{_bk_bw}] после ставки: {e}"
                                )
                    else:
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
            # ── ФИКС: сигнал пропал → больше не пытаемся по этому матчу ──
            _strat = self._monitoring_strategies.get(key) or {}
            max_bets = _strat.get('max_bets_per_match', 1)
            self._bets_by_match[key] = max_bets
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
                "retryable",
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
        self._recent_bets.clear()        

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

        # Отписка от WS-пуша
        self._unsubscribe_fast_state(key)

        # Fallback polling (если был — упадёт в None если использовался WS)
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
                if getattr(page, '_is_main_page', False):
                    _payload_cleanup = self._monitoring_payloads.get(key) or {}
                    _bk_cleanup = _normalize_bk(_payload_cleanup.get('slow_bk') or '')
                    _live_url = (
                        LIVE_URLS.get(_bk_cleanup, {}).get('any')
                        or LIVE_URLS.get(_bk_cleanup, {}).get('table_tennis')
                    )
                    if _live_url:
                        try:
                            await page.goto(
                                _live_url,
                                wait_until="domcontentloaded",
                                timeout=15000,
                            )
                            logger.info(
                                f"↩️ [{_bk_cleanup}] главная вкладка "
                                f"возвращена на {_live_url}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"Возврат главной [{_bk_cleanup}] на live: {e}"
                            )
                else:
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

        # ── Чистим счётчики ставок ТОЛЬКО если по матчу не было ставок ──
        # Если ставка была (_bets_by_match[key] > 0) — счётчик оставляем,
        # чтобы новый сигнал по тому же match_id не открывал монитор
        # повторно (нарушал max_bets_per_match).
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
            log_bus.success(
                "Ставка",
                f"Value · {amount}₽ · ID {result.get('bet_id', 'N/A')}",
                bk=bk, match_id=match_id,
            )
        else:
            logger.error(f"❌ Ошибка Value-ставки: {result.get('error')}")
            log_bus.error(
                "Ставка",
                f"Value · {result.get('error')}",
                bk=bk, match_id=match_id,
            )

        try:
            await page.close()
        except Exception as e:
            logger.warning(f"Ошибка закрытия страницы: {e}")
        try:
            new_page = await browser_wrapper.new_page()
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
    # Браузеры (Patchright)
    # ============================================================

    async def _get_browser(self, profile_id: str, headless: bool = False) \
            -> Optional[PatchrightBrowser]:
        if profile_id in self._browsers:
            wrapper = self._browsers[profile_id]
            try:
                if wrapper.is_connected():
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
            try:
                wrapper = PatchrightBrowser(
                    profile_id=profile_id,
                    headless=headless,
                )

                await wrapper.__aenter__()
                self._browsers[profile_id] = wrapper
                logger.info(f"✅ Браузер для профиля {profile_id} запущен "
                            f"(headless={headless})")
                log_bus.success("Браузер", f"Браузер запущен · профиль {profile_id}")
                return wrapper
            except Exception as e:
                err_text = str(e)
                if "Too many request" in err_text or "429" in err_text:
                    logger.warning(f"⏸ Rate limit [{profile_id}], "
                                   f"cooldown {self.PROFILE_LAUNCH_COOLDOWN}с")
                    log_bus.warning("Браузер",
                                    f"Rate limit — пауза "
                                    f"{self.PROFILE_LAUNCH_COOLDOWN:.0f}с")
                else:
                    logger.error(
                        f"❌ Ошибка запуска браузера [{profile_id}]: {e}",
                        exc_info=True,
                    )
                    log_bus.error("Браузер",
                                  f"Не удалось запустить профиль {profile_id}: {e}")
                return None

    async def _get_existing_browser(self, profile_id: str) \
            -> Optional[PatchrightBrowser]:
        wrapper = self._browsers.get(profile_id)
        if not wrapper:
            return None
        try:
            if wrapper.is_connected():
                return wrapper
        except Exception:
            pass

        logger.warning(
            f"_get_existing_browser: {profile_id} мёртв — "
            f"закрываем wrapper и чистим стейт"
        )
        try:
            await wrapper.stop()
        except Exception as e:
            logger.debug(f"wrapper.stop: {e}")
        del self._browsers[profile_id]

        # ── ФИКС 2026-10-19: чистим все мониторинги этого профиля.
        # Раньше при падении браузера (TargetClosedError) _cleanup_monitoring
        # не успевал отработать, _profile_active_matches[profile_id]
        # застревал на 5, и ВСЕ новые preopen отбивались
        # "уже 5 активных (лимит 5)" — сигналов не было вообще.
        keys = [
            k for k, pid in list(self._monitoring_profiles.items())
            if pid == profile_id
        ]
        for k in keys:
            try:
                await self._cleanup_monitoring(k)
            except Exception as e:
                logger.debug(f"cleanup {k} при мёртвом wrapper: {e}")

        # Счётчики обнуляем принудительно — иначе preopen вечно отбивается
        self._profile_active_matches.pop(profile_id, None)
        self._profile_launch_attempts.pop(profile_id, None)

        return None

    # ============================================================
    # Поиск матча
    # ============================================================
    async def _click_match_on_page(self, page: Page, player1: str, player2: str) -> bool:
        if not player1 or not player2:
            return False

        if page.is_closed():
            return False
        try:
            await page.wait_for_load_state('domcontentloaded', timeout=3000)
        except Exception:
            pass
        if page.is_closed():
            return False
        try:
            await page.wait_for_timeout(1500)
        except Exception:
            return False

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
        if page.is_closed():
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
        return sports.phase_end(sport, s1, s2, set_number)

    def _is_set_done(self, sport: str, a: int, b: int, set_number: int,
                     fast_phase: int) -> bool:
        return sports.is_set_done(sport, a, b, set_number, fast_phase)

    @staticmethod
    def _parse_phase_num(phase_name: str) -> int:
        return sports.parse_phase_num(phase_name)

    def _resolve_phase_context(self, sport: str, payload: dict,
                                data: dict, set_markets: dict,
                                fast_score: List[int],
                                slow_score: List[int]) -> Tuple[int, int]:
        return sports.resolve_phase_context(
            sport, payload, data, set_markets, fast_score, slow_score)

    def _max_phase_total(self, sport: str, set_number: int) -> int:
        return sports.max_phase_total(sport, set_number)

    def _is_phase_line_sane(self, sport: str, market: str,
                            line: float, set_number: int) -> bool:
        return sports.is_phase_line_sane(sport, market, line, set_number)

    def _verify_delay_still_open(self, data: dict, payload: dict, strategy: dict,
                                 fast_state: Optional[dict],
                                 fast_sub_override: Optional[List[int]] = None,
                                 fast_phase_override: int = 0) -> Tuple[bool, str]:
        """
        ЕДИНЫЙ источник fast: fast_sub_override + fast_phase_override
        из ОДНОГО ответа fast-client. Данные согласованы по построению,
        поэтому Guard'ы не нужны.

        Логика:
          - slow не должен опережать fast (аномалия данных)
          - fast не должен быть >1 фазы впереди (пропустили переход)
          - если в одной фазе — сравниваем sub
          - если fast впереди на 1 — валидный after-goal
        """
        sport = payload.get('sport', 'table_tennis')

        if fast_sub_override is None:
            return False, "нет fast данных (fast-client не ответил)"

        fast_sub1 = int(fast_sub_override[0])
        fast_sub2 = int(fast_sub_override[1])

        slow_sub1 = int(data.get("sub_score1") or 0)
        slow_sub2 = int(data.get("sub_score2") or 0)

        if sport not in PHASE_END_BY_SCORE and sport not in PHASE_END_BY_TIME:
            return False, f"неизвестный sport={sport}"

        fast_phase_num = int(fast_phase_override or 0)
        slow_phase_num = int(data.get('phase_num') or 0)

        if fast_phase_num <= 0 or slow_phase_num <= 0:
            return False, (
                f"нет фазы (fast={fast_phase_num}, slow={slow_phase_num})"
            )

        # Slow не должен опережать fast — это аномалия данных
        if slow_phase_num > fast_phase_num:
            return False, (
                f"slow впереди fast "
                f"(fast={fast_phase_num}, slow={slow_phase_num})"
            )

        # Fast слишком далеко (>1 фазы) — пропускаем
        if fast_phase_num > slow_phase_num + 1:
            return False, (
                f"fast слишком далеко "
                f"(fast={fast_phase_num}, slow={slow_phase_num})"
            )

        # Обе в одной фазе — сравниваем sub
        if fast_phase_num == slow_phase_num:
            diff = max(fast_sub1 - slow_sub1, fast_sub2 - slow_sub2)
            min_t = self._get_min_threshold(strategy)
            if diff >= min_t:
                return True, f"sub fast-slow: +{diff} (min={min_t})"
            return False, f"sub diff={diff} < {min_t}"

        # Fast впереди на 1 фазу — валидный after-goal
        return True, (
            f"fast на фазу впереди "
            f"(fast={fast_phase_num}, slow={slow_phase_num})"
        )

    # ============================================================
    # Подтверждённые исходы (послегол)
    # ============================================================
    def _collect_confirmed_bets(self, sport, payload, markets, set_number,
                                 fast_sub, fast_score, slow_score):
        from .after_goal import decisions
        return decisions.collect_confirmed_bets(
            sport, payload, markets, set_number,
            fast_sub, fast_score, slow_score)

    def _collect_prev_phase_bets(self, sport, payload, set_markets,
                                  fast_prev_sub: List[int], prev_phase_name: str):
        from .after_goal import decisions
        return decisions.collect_prev_phase_bets(
            sport, payload, set_markets, fast_prev_sub, prev_phase_name)

    def _filter_confirmed_by_strategy(self, confirmed, strategy, payload):
        from .after_goal import decisions
        return decisions.filter_confirmed_by_strategy(
            confirmed, strategy, payload)

    def _choose_best_bet(self, data, strategy, payload,
                         fast_sub: List[int],
                         fast_prev_sub: List[int],
                         prev_phase_name: str,
                         fast_score: List[int],
                         slow_score: List[int]):
        
        set_markets = data.get('set_markets', {})
        if not set_markets:
            return None

        sport = payload.get('sport', 'table_tennis')
        
        # slow_data (из браузера) содержит phase_num — номер ТЕКУЩЕЙ партии на Slow-БК
        slow_phase_num = int(data.get('phase_num') or 0)
        if slow_phase_num <= 0:
            return None

        # ЖЕЛЕЗНОЕ ПРАВИЛО: Мы играем ТОЛЬКО рынки партии slow_phase_num
        set_key = f"set_{slow_phase_num}"
        current_markets = set_markets.get(set_key)
        
        if not current_markets:
            logger.debug(f"best_bet: нет рынков для {set_key} (там, где висит Slow)")
            return None

        # Получаем фазу Fast (из payload, который заполнил on_update)
        fast_phase_num = self._parse_phase_num(payload.get('fast_phase', ''))
        if fast_phase_num <= 0:
            f_s1 = payload.get("fast_score", [0,0])[0]
            f_s2 = payload.get("fast_score", [0,0])[1]
            fast_phase_num = f_s1 + f_s2 + 1
        
        confirmed = []
        
        # СИТУАЦИЯ 1: Fast и Slow в одной партии (обычная задержка в очках)
        if fast_phase_num == slow_phase_num:
            logger.info(f"best_bet: обе БК в {slow_phase_num}-й партии")
            
            # ── ЗАЩИТА ОТ БАГА FAST-БК ──
            # Если Fast-БК показывает, что партия завершена (11+),
            # но счет по партиям у Fast и Slow одинаковый (fast_score == slow_score),
            # значит Fast-БК отдаёт устаревший fast_sub от ПРОШЛОЙ партии.
            # Ставить на такой "завершенный" исход НЕЛЬЗЯ.
            is_fast_set_done = False
            if sport == 'table_tennis':
                if fast_sub[0] >= 11 or fast_sub[1] >= 11:
                    is_fast_set_done = True
            elif sport in ('volleyball', 'beach_volleyball'):
                thr = 15 if slow_phase_num >= 5 else 25
                if fast_sub[0] >= thr or fast_sub[1] >= thr:
                    is_fast_set_done = True

            if is_fast_set_done:
                if fast_score[0] == slow_score[0] and fast_score[1] == slow_score[1]:
                    _gk = f"__ghost_count_{payload.get('match_id')}"
                    _gcount = self.__dict__.get(_gk, 0) + 1
                    self.__dict__[_gk] = _gcount
                    if _gcount == 1 or _gcount % 20 == 0:
                        logger.warning(
                            f"⚠️ FAST sub={fast_sub} score={fast_score} "
                            f"не сходится со slow={slow_score} "
                            f"(ghost #{_gcount}). Пропускаем."
                        )
                    if _gcount == 5:
                        _key = self._monitoring_key_for_match(payload)
                        if _key:
                            state = self._fast_state.get(_key) or {}
                            state.pop("bks", None)
                            state.pop("fetched_at", None)
                            state.pop("markets", None)
                            state.pop("markets_phase", None)
                            state.pop("markets_fetched_at", None)
                            logger.warning(
                                f"🔄 [{_key}] 5 ghost-фреймов подряд — "
                                f"сброс кэша fast, переподписка"
                            )
                            self._unsubscribe_fast_state(_key)
                            self._subscribe_fast_state(_key)
                    return None
                else:
                    _gk = f"__ghost_count_{payload.get('match_id')}"
                    self.__dict__.pop(_gk, None)

            confirmed = self._collect_confirmed_bets(
                sport, payload, current_markets, slow_phase_num,
                fast_sub, fast_score, slow_score
            )

        # ─────────────────────────────────────────────────────────
        # СИТУАЦИЯ 2: Fast ушёл на 1 партию вперёд.
        # Slow ещё торгует завершившейся партией.
        # ─────────────────────────────────────────────────────────
        elif fast_phase_num == slow_phase_num + 1:
            prev_s1 = fast_prev_sub[0] if fast_prev_sub else 0
            prev_s2 = fast_prev_sub[1] if fast_prev_sub else 0

            # ЩИТ 1: Ghost Frame — score по партиям не изменился
            if fast_score[0] == slow_score[0] and fast_score[1] == slow_score[1]:
                logger.warning(
                    f"👻 GHOST FRAME BLOCKED: fast_phase={fast_phase_num} > "
                    f"slow_phase={slow_phase_num}, но score по партиям "
                    f"не изменился ({fast_score}). Пропуск."
                )
                return None

            # ЩИТ 2: prev_sub должен быть реальным финалом
            is_prev_done = False
            if sport == 'table_tennis':
                is_prev_done = (
                    (prev_s1 >= 11 and prev_s1 - prev_s2 >= 2) or
                    (prev_s2 >= 11 and prev_s2 - prev_s1 >= 2)
                )
            elif sport in ('volleyball', 'beach_volleyball'):
                thr = 15 if slow_phase_num >= 5 else 25
                is_prev_done = (
                    (prev_s1 >= thr and prev_s1 - prev_s2 >= 2) or
                    (prev_s2 >= thr and prev_s2 - prev_s1 >= 2)
                )
            elif sport in ('basketball', 'cyber_basketball'):
                is_prev_done = True

            if not is_prev_done:
                logger.warning(
                    f"🛑 MATH GUARD: prev_sub {prev_s1}:{prev_s2} — "
                    f"не завершённая партия. Пропуск."
                )
                return None

            logger.info(
                f"✅ ПОСЛЕГОЛ: Fast в {fast_phase_num}, Slow в "
                f"{slow_phase_num}. Играем завершившуюся "
                f"{prev_s1}:{prev_s2}."
            )
            actual_prev_phase_name = f"{slow_phase_num}-я партия"
            confirmed = self._collect_prev_phase_bets(
                sport, payload, set_markets, fast_prev_sub,
                actual_prev_phase_name
            )

        # СИТУАЦИЯ 3: Fast ушел на 2+ партии вперед (Slow завис)
        elif fast_phase_num > slow_phase_num + 1:
            logger.info(f"best_bet: Fast ушел далеко ({fast_phase_num} vs {slow_phase_num}). Пропускаем.")
            return None
        else:
            # Slow впереди Fast — аномалия
            return None

        # Фильтр по порогам (min_score_diff)
        slow_s1 = int(data.get("sub_score1") or 0)
        slow_s2 = int(data.get("sub_score2") or 0)
        fast_s1 = fast_sub[0] if fast_sub else 0
        fast_s2 = fast_sub[1] if fast_sub else 0
        
        diff = 0
        if fast_phase_num == slow_phase_num:
            diff = max(fast_s1 - slow_s1, fast_s2 - slow_s2)

        confirmed_filtered = []
        for c in confirmed:
            market = c[0]
            base_market = market.replace("race_", "", 1) if market.startswith("race_") else market
            threshold = self._get_market_threshold(strategy, base_market)
            
            if fast_phase_num > slow_phase_num or diff >= threshold:
                confirmed_filtered.append(c)

        if not confirmed_filtered:
            return None

        # Фильтр по manual_markets / auto_criterion
        confirmed_filtered = self._filter_confirmed_by_strategy(confirmed_filtered, strategy, payload)
        if not confirmed_filtered:
            return None

        # Фильтр по коэффициентам
        min_odds = strategy.get('min_odds', 1.0)
        max_odds = strategy.get('max_odds', 5.0)
        valid = [c for c in confirmed_filtered if min_odds <= c[2] <= max_odds]
        if not valid:
            return None

        # Приоритеты (Виннер > Тотал > Фора)
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
            'set_number': set_number, # Всегда равен slow_phase_num
            'reason': reason,
        }
        
        base_market = market.replace("race_", "", 1) if market.startswith("race_") else market
        if base_market == 'total':
            result['line'] = current_markets.get('total', {}).get('line', 0)
        elif base_market == 'handicap':
            result['line'] = current_markets.get('handicap', {}).get(side, {}).get('line', 0)
        elif base_market == 'it':
            parts = side.split('_', 1)
            if len(parts) == 2:
                player, dirn = parts
                result['player'] = player
                result['direction'] = dirn
                it_m = current_markets.get('it', {}).get(player, {})
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
        from .after_goal import decisions
        return decisions.fast_favors(
            market, side, line, fast_sub1, fast_sub2,
            phase_done, sport, set_number)

    @staticmethod
    def _is_outcome_dead(market: str, side: str, line: float,
                         fast_sub1: int, fast_sub2: int,
                         phase_done: bool) -> bool:
        from .after_goal import decisions
        return decisions.is_outcome_dead(
            market, side, line, fast_sub1, fast_sub2, phase_done)

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

        # ── ЖЕЛЕЗНАЯ СИНХРОНИЗАЦИЯ ФАЗ ──
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
            logger.info(
                f"LV: skip — пустые рынки для {set_key} "
                f"(slow={bool(slow_markets)}, fast={bool(fast_markets)})"
            )
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

        # ── ФИКС swap (2026-10-26): приводим fast-очки к порядку slow.
        # При swap fast.player1 == slow.player2, поэтому «очки игрока 1
        # в терминах slow» — это fast.sub_score2, и наоборот.
        # Без этого _fast_favors / _is_outcome_dead / _ind ниже смотрят
        # не на того игрока и:
        #   - либо отсеивают валидные value (fast подтверждает, а фильтр
        #     говорит «нет»),
        #   - либо пропускают ложные (fast опровергает, а фильтр «да»).
        # Кэфы через _s() уже flipped — их не трогаем.
        _fast_sub1 = int(fast_entry.get("sub_score1") or 0)
        _fast_sub2 = int(fast_entry.get("sub_score2") or 0)
        if swap:
            _fast_sub1, _fast_sub2 = _fast_sub2, _fast_sub1

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

        # ═══════════════════════════════════════════════════════════
        # ЗАЩИТА ОТ "GHOST FRAME" (баг Olimp)
        # Если партия завершена, но счет по партиям не изменился,
        # значит данные устарели. Пропускаем.
        # ═══════════════════════════════════════════════════════════
        if phase_done:
            # ФИКС swap: при swap счёт по партиям у fast перевёрнут
            # относительно slow. Без этого проверка «fast и slow
            # показывают то же состояние» никогда не срабатывает,
            # и мы можем поставить на уже завершённую партию.
            _fp1, _fp2 = (f_p2, f_p1) if swap else (f_p1, f_p2)
            if _fp1 == s_p1 and _fp2 == s_p2:
                logger.warning(
                    f"LV: skip — GHOST FRAME: phase_done=True, "
                    f"но score не изменился "
                    f"(fast={_fp1}:{_fp2}, slow={s_p1}:{s_p2}, swap={swap})"
                )
                return None

        only = strategy.get('only_markets') or ['winner', 'total', 'handicap']
        min_edge = float(strategy.get('min_edge_percent', 5.0)) / 100.0
        max_edge = float(strategy.get('max_edge_percent', 20.0)) / 100.0
        min_odds = float(strategy.get('min_odds', 1.30))
        max_odds = float(strategy.get('max_odds', 5.0))

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
                        logger.info(
                            f"LV: skip winner — маленькая задержка "
                            f"(sub={sub_diff}, parts={parts_diff} < порога {_thr})"
                        )
                        continue

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
            logger.info(
                f"LV: нет кандидатов для ставки (phase={slow_phase_num}, "
                f"min_edge={min_edge*100:.1f}%)"
            )
            return None

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
            best_bet = None

            if not self._monitoring_active.get(key, False):
                logger.info(f"⏹ on_update: мониторинг {key} неактивен")
                return

            data_mid = data.get('match_id')
            if data_mid is not None and str(data_mid) != str(match_id):
                logger.info(f"⏭ on_update: чужой match_id {data_mid} ≠ {match_id}")
                return

            self._last_update_time[key] = time.time()
            self._last_slow_data[key] = data

            # ── ЕДИНЫЙ ИСТОЧНИК FAST ДЛЯ AFTER-GOAL ──
            current_payload = self._monitoring_payloads.get(key, payload)
            current_strategy = self._monitoring_strategies.get(key, strategy)
            strategy_type = (current_strategy.get('type') or '').lower()

            if strategy_type == 'live-value':
                # Для LV используем HTTP-клиент (как раньше)
                fast_entry = self._get_fast_markets(key)
                if fast_entry:
                    fast_sub = [int(fast_entry.get("sub_score1") or 0), int(fast_entry.get("sub_score2") or 0)]
                    fast_phase_from_client = int(fast_entry.get("phase_num") or 0)
                    fast_score = [int(fast_entry.get("score1") or 0), int(fast_entry.get("score2") or 0)]
                else:
                    return
            else:
                # ── ДЛЯ ПОСЛЕГОЛА БЕРЕМ ВСЁ ИЗ WS ──
                s1, s2, ph, pv1, pv2 = self._get_fast_sub_for_after_goal(key, payload)
                fast_sub = [s1, s2]
                fast_phase_from_client = ph
                
                # Корректный score из WS
                # ВАЖНО: НЕ используем имя fast_bk здесь, чтобы не
                # переопределять переменную из внешней области видимости.
                ws_fast_bk = _normalize_bk(current_payload.get('fast_bk'))
                bk_data = (self._fast_state.get(key, {}).get("bks") or {}).get(ws_fast_bk)
                if bk_data:
                    fast_score = [int(bk_data.get("score1") or 0), int(bk_data.get("score2") or 0)]
                else:
                    fast_score = [0, 0]

            slow_sub = [data.get("sub_score1", 0) or 0,
                        data.get("sub_score2", 0) or 0]
            slow_score = [data.get("score1", 0) or 0,
                          data.get("score2", 0) or 0]

            fast_available = (fast_phase_from_client > 0)
            logger.info(
                f"🎬 on_update #{on_update_count} key={key} "
                f"slow_bk={slow_bk} fast_bk={fast_bk} "
                f"fast_score={fast_score} slow_score={slow_score} "
                f"fast_sub={fast_sub} slow_sub={slow_sub} "
                f"fast_available={fast_available} "
                f"fast_phase_num={fast_phase_from_client} "
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
                # ── LIVE-VALUE ──
                best_bet = self._choose_live_value_bet(
                    key, data, current_strategy, current_payload
                )
                if not best_bet:
                    return

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
                # ── AFTER-GOAL ──
                is_open, reason = self._verify_delay_still_open(
                    data, current_payload, current_strategy, None,
                    fast_sub_override=fast_sub,
                    fast_phase_override=fast_phase_from_client,
                )
                logger.info(f"🔍 delay_still_open={is_open} ({reason})")

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

                # ── ФИКС: prev_sub ТОЛЬКО из fast-клиента или WS ──
                slow_phase_num = int(data.get('phase_num') or 0)
                
                if strategy_type == 'live-value':
                    fast_prev_sub = [0, 0]
                    prev_phase_name = ""
                    if fast_entry:
                        _fp1 = int(fast_entry.get("prev_sub_score1") or 0)
                        _fp2 = int(fast_entry.get("prev_sub_score2") or 0)
                        _fpn = int(fast_entry.get("prev_phase_num") or 0)
                        if _fp1 > 0 or _fp2 > 0:
                            fast_prev_sub = [_fp1, _fp2]
                            if _fpn > 0:
                                prev_phase_name = f"{_fpn}-я партия"
                else:
                    fast_prev_sub = [pv1, pv2]
                    prev_phase_name = f"{slow_phase_num - 1}-я партия" if slow_phase_num > 1 else ""

                # ── ФИКС 2026-10-14 v2 ──
                # prev_sub проверяем ВНУТРИ _choose_best_bet — только
                # для случая "fast на фазу впереди". Для "обе в одной
                # фазе" prev_sub не нужен (играем текущую партию).
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

            if not best_bet:
                # Сюда не должны попадать — все ветки выше делают return
                # если best_bet не найден. Но на всякий случай.
                return
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
                if page.is_closed():
                    logger.warning(f"⏭ on_update: страница {key} закрыта — дропаем")
                    event.set()
                    return

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
                
                try:
                    token = await page.evaluate(
                        "() => document.cookie.match(/tk=([^;]+)/)?.[1] || ''"
                    )
                    bet_data['token'] = token
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось получить tk токен (page closed?): {e}")
                    event.set()  # закрываем мониторинг
                    return
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
            elif bk == 'onewin':
                bet_data['outcome_id'] = outcome_info.get('id')
                bet_data['kf'] = best_bet['odd']
                bet_data['event_id'] = data.get('match_id')
            else:
                logger.error(f"Отправка для БК {bk} не реализована")
                return

            logger.info(f"📤 bet_data={bet_data}")

            # ═══════════ ДИАГ-ЛОГ СТАВКИ (2026-10-15) ═══════════
            # ФИКС: пишем diag ТОЛЬКО после _enqueue_bet — с полем
            # enqueued=True/False. Иначе в лог попадают отбитые
            # dup-guard'ом ставки (см. diag 2 и 3 от 2026-10-14 —
            # одна и та же ставка 2 раза подряд).
            import os as _os
            from datetime import datetime as _dt

            fast_entry_diag = self._get_fast_markets(key) or {}
            from ui.log_paths import get_log_path
            _diag_path = get_log_path("bets_diag.log")
            _diag = {
                "ts": _dt.now().isoformat(timespec="milliseconds"),
                "key": key,
                "match_id": match_id,
                "teams": current_payload.get("match_teams"),
                "sport": current_payload.get("sport"),
                "slow_bk": slow_bk,
                "fast_bk": fast_bk,
                "strategy": current_strategy.get("name"),
                "strategy_type": strategy_type,
                # ── ФАЗЫ ──
                "fast_phase_num": fast_phase_from_client,
                "slow_phase_num": data.get("phase_num"),
                # ── SCORE / SUB ──
                "fast_sub": fast_sub,
                "slow_sub": slow_sub,
                "fast_score": fast_score,
                "slow_score": slow_score,
                "fast_prev_sub": current_payload.get("fast_prev_sub_score"),
                "fast_prev_phase": current_payload.get("fast_prev_phase"),
                # ── ЧТО ИЗ FAST-CLIENT ──
                "fast_client_phase": fast_entry_diag.get("phase_num"),
                "fast_client_sub1": fast_entry_diag.get("sub_score1"),
                "fast_client_sub2": fast_entry_diag.get("sub_score2"),
                "fast_client_prev_sub1": fast_entry_diag.get("prev_sub_score1"),
                "fast_client_prev_sub2": fast_entry_diag.get("prev_sub_score2"),
                "fast_client_prev_phase": fast_entry_diag.get("prev_phase_num"),
                "fast_client_score1": fast_entry_diag.get("score1"),
                "fast_client_score2": fast_entry_diag.get("score2"),
                # ── ЧТО ВЫБРАЛИ ──
                "best_bet": best_bet,
                # ── ЧТО ОТПРАВИЛИ ──
                "bet_data": {
                    k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v)
                    for k, v in (bet_data or {}).items()
                },
                # ── КАКИЕ РЫНКИ В SLOW ──
                "slow_set_markets_keys": list(
                    (data.get("set_markets") or {}).keys()
                ),
                "slow_outcome_ids_keys": list(
                    (data.get("outcome_ids") or {}).keys()
                ),
            }

            _enqueued = await self._enqueue_bet(
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
            _diag["enqueued"] = bool(_enqueued)

            try:
                self._bets_diag_count += 1
                _diag["diag_num"] = self._bets_diag_count
                with open(_diag_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(_diag, ensure_ascii=False) + "\n")
                logger.info(
                    f"💾 BET DIAG #{self._bets_diag_count} "
                    f"(enqueued={_enqueued}) → bets_diag.log"
                )
            except Exception as _de:
                logger.debug(f"bet diag: {_de}")
            # ═══════════ КОНЕЦ ДИАГ-ЛОГА ═══════════

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