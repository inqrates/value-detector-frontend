# core/fast_client.py
"""
HTTP/WS/SSE клиенты для получения внутриматчевых кэфов с fast БК.

ВАЖНО (2026-10):
  Парсинг делегируется в `ui/after_goal/*.py`. Это единый источник
  правды — те же функции, что уже отлажены в браузерных хендлерах.

  Транспорт у нас свой (HTTP/WS/SSE, без Playwright), но парсинг —
  общий. Если БК завтра поменяет формат, правим в одном месте.

  Для Betcity это автоматически даёт поддержку баскета:
  `BetcityHandler._parse_event` сам выбирает `_parse_nt_markets`
  (НТ, mid 35/905/913) или `_parse_phase_markets` (баскет/волейбол,
  rows с num_per и ext['779'] "Исходы по четвертям").
"""
import asyncio
import json
import logging
import re
import time
import gzip
from typing import Optional, Dict, List, Any, Tuple
import httpx
from curl_cffi.requests import AsyncSession
import websockets

# ════════════════════════════════════════════════════════════
# Делегируемые парсеры (единый источник правды)
# ════════════════════════════════════════════════════════════
from ui.after_goal.betcity import BetcityHandler
from ui.after_goal.olimp import OlimpHandler
from ui.after_goal.marathon import MarathonHandler
from ui.after_goal.sportbet import SportbetHandler
from ui.after_goal.fonbet import FonbetHandler
from ui.after_goal.pari import PariHandler

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# Нормализация имён (fallback-поиск)
# ════════════════════════════════════════════════════════════
def _normalize_name(name: str) -> str:
    if not name:
        return ""
    text = re.sub(r"\(.*?\)", "", name)
    text = re.sub(r"\d+", "", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = text.lower()
    words = [w for w in text.split() if len(w) > 1]
    if not words:
        return text.strip()
    return max(words, key=len)


def _close_token(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return True
    return False


def _players_match(p1a: str, p2a: str, p1b: str, p2b: str) -> bool:
    na1 = _normalize_name(p1a)
    na2 = _normalize_name(p2a)
    nb1 = _normalize_name(p1b)
    nb2 = _normalize_name(p2b)
    if not (na1 and na2 and nb1 and nb2):
        return False

    direct = _close_token(na1, nb1) and _close_token(na2, nb2)
    cross = _close_token(na1, nb2) and _close_token(na2, nb1)
    return direct or cross


def _strip_outcome_ids(parsed: Optional[dict]) -> Optional[dict]:
    """Убирает из результата after_goal то, что fast-клиенту не нужно."""
    if not parsed:
        return None
    parsed.pop("outcome_ids", None)
    parsed.pop("race_outcome_ids", None)
    parsed.pop("race_set_markets", None)
    return parsed


# ════════════════════════════════════════════════════════════
# Базовый класс
# ════════════════════════════════════════════════════════════
class FastOddsClient:
    bk: str = ""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._session: Optional[AsyncSession] = None
        self._last_fetch_at: float = 0.0
        self._last_raw: Optional[Any] = None
        self._fetch_interval: float = 1.5
        self._lock = asyncio.Lock()

    async def start(self):
        if self._session is None:
            self._session = await self._build_session()

    async def stop(self):
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def _build_session(self) -> AsyncSession:
        raise NotImplementedError

    async def _fetch_raw(self) -> Optional[Any]:
        raise NotImplementedError

    def _find_match(self, raw: Any, p1: str, p2: str) -> Optional[dict]:
        raise NotImplementedError

    def _parse(self, raw: Any, event: dict) -> Optional[Dict]:
        raise NotImplementedError

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        """Базовый fallback по именам (переопределяется в наследниках)."""
        await self.start()

        async with self._lock:
            now = time.time()
            if (self._last_raw is None
                    or now - self._last_fetch_at >= self._fetch_interval):
                raw = await self._fetch_raw()
                if raw is None:
                    return None
                self._last_raw = raw
                self._last_fetch_at = now
            else:
                raw = self._last_raw

        event = self._find_match(raw, p1, p2)
        if not event:
            return None

        try:
            return self._parse(raw, event)
        except Exception as e:
            logger.warning(f"[{self.bk}_fast] parse: {e}", exc_info=True)
            return None


# ════════════════════════════════════════════════════════════
# Fonbet — делегирует в FonbetHandler.parse_update
# ════════════════════════════════════════════════════════════
class FonbetFastClient(FastOddsClient):
    bk = "fonbet"

    EVENT_URL = "https://line-lb54-w.bk6bba-resources.com/ma/events/event"
    SCOPE = "1600"

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "origin": "https://fon.bet",
        "referer": "https://fon.bet/",
    }

    def __init__(self):
        super().__init__(timeout=10.0)
        self._fetch_interval = 2.0

    async def _build_session(self) -> AsyncSession:
        return AsyncSession(
            impersonate="chrome150",
            timeout=self.timeout,
            headers=self.HEADERS,
        )

    async def _fetch_raw(self) -> Optional[Any]:
        return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()
        try:
            r = await self._session.get(
                self.EVENT_URL,
                params={
                    "lang": "ru",
                    "version": "0",
                    "eventId": str(native_id),
                    "scopeMarket": self.SCOPE,
                },
            )
        except Exception as e:
            logger.warning(f"[fonbet_fast] event {native_id}: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[fonbet_fast] event {native_id} HTTP {r.status_code}")
            return None
        try:
            data = r.json()
        except Exception:
            return None



        try:
            parsed = FonbetHandler.parse_update(data, str(native_id))
        except Exception as e:
            logger.warning(f"[fonbet_fast] parse: {e}", exc_info=True)
            return None

        if not parsed:
            logger.warning(f"[fonbet_fast] native {native_id}: parse_update → None")
            return None

        logger.debug(
            f"[fonbet_fast] native {native_id}: OK "
            f"phase={parsed.get('phase_num')} "
            f"score={parsed.get('score1')}:{parsed.get('score2')} "
            f"sub={parsed.get('sub_score1')}:{parsed.get('sub_score2')} "
            f"sets={list((parsed.get('set_markets') or {}).keys())}"
        )

        return {
            "match_id": parsed.get("match_id"),
            "phase_num": parsed.get("phase_num", 0),
            "score1": parsed.get("score1", 0),
            "score2": parsed.get("score2", 0),
            "sub_score1": parsed.get("sub_score1", 0),
            "sub_score2": parsed.get("sub_score2", 0),
            "set_markets": parsed.get("set_markets", {}),
        }


# ════════════════════════════════════════════════════════════
# Pari — делегирует в PariHandler.parse_update
# ════════════════════════════════════════════════════════════
class PariFastClient(FastOddsClient):
    bk = "pari"

    EVENT_URL = "https://line-lb01-w.pb06e2-resources.com/events/event"

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "origin": "https://pari.ru",
        "referer": "https://pari.ru/",
    }

    def __init__(self):
        super().__init__(timeout=10.0)
        self._fetch_interval = 1.5

    async def _build_session(self) -> AsyncSession:
        return AsyncSession(
            impersonate="chrome150",
            timeout=self.timeout,
            headers=self.HEADERS,
        )

    async def _fetch_raw(self):
        return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()
        try:
            r = await self._session.get(
                self.EVENT_URL,
                params={
                    "lang": "ru",
                    "version": "0",
                    "eventId": str(native_id),
                    "scopeMarket": "2300",
                },
            )
        except Exception as e:
            logger.warning(f"[pari_fast] event {native_id}: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[pari_fast] event {native_id} HTTP {r.status_code}")
            return None
        try:
            data = r.json()
        except Exception:
            return None

        try:
            parsed = PariHandler.parse_update(data, str(native_id))
        except Exception as e:
            logger.warning(f"[pari_fast] parse: {e}", exc_info=True)
            return None

        if not parsed:
            return None
        return {
            "match_id": parsed.get("match_id"),
            "phase_num": parsed.get("phase_num", 0),
            "score1": parsed.get("score1", 0),
            "score2": parsed.get("score2", 0),
            "sub_score1": parsed.get("sub_score1", 0),
            "sub_score2": parsed.get("sub_score2", 0),
            "set_markets": parsed.get("set_markets", {}),
        }


# ════════════════════════════════════════════════════════════
# Olimp — делегирует в OlimpHandler._extract_all_events / _parse_event
# ════════════════════════════════════════════════════════════
class OlimpFastClient(FastOddsClient):
    bk = "olimp"

    # Bulk — отдаёт только базовые рынки (RESULT/HANDICAP/TOTAL).
    # Используется как fallback для поиска по именам.
    URL = "https://www.olimp.bet/api/v4/0/live/sports-with-competitions-with-events"

    # Точечный — отдаёт ВСЕ рынки матча, включая фазовые OTHER
    # (корзина партий, ИТ партий, форы партий). Основной источник.
    EVENTS_URL = "https://www.olimp.bet/api/v4/0/live/events"

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "X-Cupis": "1",
        "X-Olimp": "cupis-desktop",
        "Origin": "https://www.olimp.bet",
        "Referer": "https://www.olimp.bet/live",
    }

    def __init__(self):
        super().__init__(timeout=15.0)
        self._fetch_interval = 1.5

    async def _build_session(self) -> AsyncSession:
        return AsyncSession(
            impersonate="chrome150",
            timeout=self.timeout,
            headers=self.HEADERS,
        )

    async def _fetch_raw(self) -> Optional[Any]:
        try:
            r = await self._session.get(self.URL)
        except Exception as e:
            logger.warning(f"[olimp_fast] fetch: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[olimp_fast] HTTP {r.status_code}")
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    @staticmethod
    def _extract_all_from_raw(raw) -> Dict[str, dict]:
        """
        Olimp отдаёт МАССИВ из 20 payload'ов (по одному на вид спорта).
        Внутри каждого — payload.competitionsWithEvents[].events[].

        ВАЖНО: OlimpHandler._extract_all_events ожидает СПИСОК
        верхнеуровневых items, каждый из которых имеет ключ `payload`.
        Поэтому НЕ разворачиваем payload сами — передаём raw как есть.
        """
        if not raw:
            return {}

        # Если прилетел одиночный payload вместо массива — оборачиваем
        # в один item так, как его увидел бы _extract_all_events.
        if isinstance(raw, dict):
            if "competitionsWithEvents" in raw:
                raw = [{"payload": raw}]
            elif isinstance(raw.get("payload"), dict):
                raw = [raw]
            else:
                return {}

        if not isinstance(raw, list):
            return {}

        try:
            return OlimpHandler._extract_all_events(raw) or {}
        except Exception as e:
            logger.warning(f"[olimp_fast] extract: {e}", exc_info=True)
            return {}

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()

        # ── Точечный запрос по event_id ──
        # Bulk (URL) отдаёт только RESULT/HANDICAP/TOTAL — фазовые OTHER
        # приходят отдельно, через /live/events?vids[]=<id>:&main=false.
        try:
            r = await self._session.get(
                self.EVENTS_URL,
                params={"vids[]": f"{native_id}:", "main": "false"},
            )
        except Exception as e:
            logger.warning(f"[olimp_fast] native {native_id}: {e}")
            return None

        if r.status_code != 200:
            logger.warning(
                f"[olimp_fast] native {native_id} HTTP {r.status_code}"
            )
            return None

        try:
            data = r.json()
        except Exception:
            return None

        # Ответ: list[{operationId, id, version, payload: {event...}}]
        if not isinstance(data, list) or not data:
            logger.debug(f"[olimp_fast] native {native_id}: пустой ответ")
            return None

        item = data[0]
        if not isinstance(item, dict):
            return None
        payload = item.get("payload")
        if not isinstance(payload, dict):
            return None

        # Парсим тем же парсером, что after-goal
        try:
            parsed = OlimpHandler._parse_event(payload)
        except Exception as e:
            logger.warning(
                f"[olimp_fast] native {native_id} parse: {e}",
                exc_info=True,
            )
            return None

        if not parsed:
            logger.debug(
                f"[olimp_fast] native {native_id}: "
                f"_parse_event вернул None (нет фазовых рынков?)"
            )
            return None

        logger.info(
            f"[olimp_fast] native {native_id}: OK "
            f"phase={parsed.get('phase_num')} "
            f"score={parsed.get('score1')}:{parsed.get('score2')} "
            f"sub={parsed.get('sub_score1')}:{parsed.get('sub_score2')} "
            f"sets={list((parsed.get('set_markets') or {}).keys())}"
        )

        return {
            "match_id": parsed.get("match_id"),
            "phase_num": parsed.get("phase_num", 0),
            "score1": parsed.get("score1", 0),
            "score2": parsed.get("score2", 0),
            "sub_score1": parsed.get("sub_score1", 0),
            "sub_score2": parsed.get("sub_score2", 0),
            "set_markets": parsed.get("set_markets", {}),
        }

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        await self.start()

        async with self._lock:
            now = time.time()
            if (self._last_raw is None
                    or now - self._last_fetch_at >= self._fetch_interval):
                raw = await self._fetch_raw()
                if raw is None:
                    return None
                self._last_raw = raw
                self._last_fetch_at = now
            else:
                raw = self._last_raw

        all_events = self._extract_all_from_raw(raw)

        for ev_id, parsed in all_events.items():
            t1 = parsed.get("player1", "")
            t2 = parsed.get("player2", "")
            if _players_match(p1, p2, t1, t2):
                return {
                    "match_id": parsed.get("match_id"),
                    "phase_num": parsed.get("phase_num", 0),
                    "score1": parsed.get("score1", 0),
                    "score2": parsed.get("score2", 0),
                    "sub_score1": parsed.get("sub_score1", 0),
                    "sub_score2": parsed.get("sub_score2", 0),
                    "set_markets": parsed.get("set_markets", {}),
                }
        return None


# ════════════════════════════════════════════════════════════
# Betcity — прямой поиск по native_id + делегирование парсинга
# в BetcityHandler (НТ / баскет / волейбол — автоматически)
# ════════════════════════════════════════════════════════════
class BetcityFastClient(FastOddsClient):
    bk = "betcity"

    # Общий снапшот (fallback)
    URL = ("https://ad.betcity.ru/d/on_air/bets"
           "?rev=8&add=dep_events&ver=88&csn=ooca9s&lng=0")

    # Точечный запрос по native_id (как делает фронт Betcity)
    EVENT_URL_TPL = ("https://ad.betcity.ru/d/on_air/bets"
                     "?rev=8&add=dep_event&ids={eid}&stat=1&ver=91&csn=ooca9s")

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "referer": "https://betcity.ru/",
        "origin": "https://betcity.ru",
    }

    # ID спортов Betcity → наши sport_key
    SPORT_IDS = {
        "46": "table_tennis",
        "12": "volleyball",
        "3":  "basketball",
    }

    def __init__(self):
        super().__init__(timeout=10.0)
        self._fetch_interval = 1.5

    async def _build_session(self) -> AsyncSession:
        from core.fast_config import load_cookies

        session = AsyncSession(
            impersonate="chrome150",
            timeout=self.timeout,
            headers=self.HEADERS,
        )

        cookies = load_cookies("betcity")
        for c in cookies:
            try:
                session.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain"),
                    path=c.get("path", "/"),
                )
            except Exception:
                pass

        if cookies:
            logger.info(f"[betcity_fast] 🍪 {len(cookies)} cookies")
        else:
            logger.warning("[betcity_fast] нет cookies — будет 403")

        return session

    async def _fetch_raw(self) -> Optional[Any]:
        try:
            r = await self._session.get(self.URL)
        except Exception as e:
            logger.warning(f"[betcity_fast] fetch: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[betcity_fast] HTTP {r.status_code}")
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    @staticmethod
    def _extract_one(parsed: Optional[dict]) -> Optional[Dict]:
        """Приводит результат after_goal к формату fast-клиента."""
        if not parsed:
            return None
        return {
            "match_id": parsed.get("match_id"),
            "phase_num": parsed.get("phase_num", 0),
            "score1": parsed.get("score1", 0),
            "score2": parsed.get("score2", 0),
            "sub_score1": parsed.get("sub_score1", 0),
            "sub_score2": parsed.get("sub_score2", 0),
            "set_markets": parsed.get("set_markets", {}),
        }

    @staticmethod
    def _empty_result(ev_id) -> Dict:
        """
        Результат, когда событие НАЙДЕНО, но рынки ещё не пришли
        (например, только что началась новая четверть).
        Возвращаем пустой set_markets, чтобы polling не считал
        это промахом и продолжал ждать появления рынков.
        """
        return {
            "match_id": str(ev_id),
            "phase_num": 0,
            "score1": 0,
            "score2": 0,
            "sub_score1": 0,
            "sub_score2": 0,
            "set_markets": {},
        }

    def _find_event_raw(self, data: dict, native_id) -> Optional[tuple]:
        """
        Прямой поиск события по ev_id в raw-ответе on_air/bets.
        Возвращает (ev_id, ev_dict, sport_key) или None.

        ВАЖНО: не использует BetcityHandler._extract_all_events —
        тот отбрасывает события без рынков (пустые set_markets).
        А нам нужно уметь найти событие, даже если рынки ещё не
        подгрузились (например, при смене четверти Betcity
        кратковременно убирает фазовые рынки).
        """
        reply = data.get("reply") or {}
        sports = reply.get("sports") or {}
        if not isinstance(sports, dict):
            return None

        for sid_str, sport_block in sports.items():
            if not isinstance(sport_block, dict):
                continue
            sport_key = self.SPORT_IDS.get(str(sid_str))
            if not sport_key:
                continue

            for chmp in (sport_block.get("chmps") or {}).values():
                if not isinstance(chmp, dict):
                    continue
                is_cyber = chmp.get("is_cyber", 0)
                eff_sport = sport_key
                if sport_key == "basketball" and is_cyber == 1:
                    eff_sport = "cyber_basketball"

                for ev_id, ev in (chmp.get("evts") or {}).items():
                    if str(ev_id) != str(native_id):
                        continue
                    if not isinstance(ev, dict):
                        continue
                    if ev.get("team_type_f", 0) != 0:
                        continue
                    if ev.get("is_dep", 0) == 1:
                        continue
                    return str(ev_id), ev, eff_sport
        return None

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()

        # ── 1) Точечный запрос по ids=<native_id> (23 KB вместо 3.4 MB) ──
        url = self.EVENT_URL_TPL.format(eid=native_id)
        try:
            r = await self._session.get(url)
        except Exception as e:
            logger.warning(f"[betcity_fast] native {native_id}: {e}")
            r = None

        if r is not None and r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                data = None

            if isinstance(data, dict):
                found = self._find_event_raw(data, native_id)
                if found:
                    ev_id, ev, sport_key = found
                    try:
                        parsed = BetcityHandler._parse_event(
                            ev_id, ev, sport_key
                        )
                    except Exception as e:
                        logger.warning(
                            f"[betcity_fast] native {native_id} parse: {e}",
                            exc_info=True,
                        )
                        parsed = None

                    if parsed:
                        return self._extract_one(parsed)

                    # Событие нашли, но рынков ещё нет — возвращаем
                    # пустой результат, чтобы polling не считал это
                    # промахом и продолжал ждать появления рынков.
                    logger.debug(
                        f"[betcity_fast] native {native_id}: "
                        f"событие найдено, но _parse_event вернул None "
                        f"(рынки ещё не пришли)"
                    )
                    return self._empty_result(ev_id)

                logger.debug(
                    f"[betcity_fast] native {native_id}: "
                    f"точечный ответ без нашего eid"
                )
        elif r is not None:
            logger.debug(
                f"[betcity_fast] native {native_id}: "
                f"точечный запрос HTTP {r.status_code}"
            )

        # ── 2) Fallback: общий снапшот ──
        logger.debug(
            f"[betcity_fast] native {native_id}: fallback на общий снапшот"
        )
        try:
            r2 = await self._session.get(self.URL)
        except Exception as e:
            logger.warning(f"[betcity_fast] snapshot {native_id}: {e}")
            return None
        if r2.status_code != 200:
            logger.warning(
                f"[betcity_fast] snapshot {native_id} HTTP {r2.status_code}"
            )
            return None
        try:
            data2 = r2.json()
        except Exception:
            return None

        found = self._find_event_raw(data2, native_id)
        if not found:
            logger.warning(
                f"[betcity_fast] native {native_id} не найден "
                f"в снапшоте (raw)"
            )
            return None

        ev_id, ev, sport_key = found
        try:
            parsed = BetcityHandler._parse_event(ev_id, ev, sport_key)
        except Exception as e:
            logger.warning(
                f"[betcity_fast] snapshot parse {native_id}: {e}",
                exc_info=True,
            )
            return None

        if not parsed:
            logger.debug(
                f"[betcity_fast] native {native_id}: найден в снапшоте, "
                f"но рынков нет — возвращаю пустой"
            )
            return self._empty_result(ev_id)

        return self._extract_one(parsed)

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        """Fallback по именам, если native_id нет в /match_state."""
        await self.start()

        async with self._lock:
            now = time.time()
            if (self._last_raw is None
                    or now - self._last_fetch_at >= self._fetch_interval):
                raw = await self._fetch_raw()
                if raw is None:
                    return None
                self._last_raw = raw
                self._last_fetch_at = now
            else:
                raw = self._last_raw

        try:
            all_events = BetcityHandler._extract_all_events(raw)
        except Exception as e:
            logger.warning(f"[betcity_fast] extract: {e}", exc_info=True)
            return None

        for ev_id, parsed in all_events.items():
            t1 = parsed.get("player1", "")
            t2 = parsed.get("player2", "")
            if _players_match(p1, p2, t1, t2):
                return self._extract_one(parsed)
        return None


# ════════════════════════════════════════════════════════════
# Sportbet — делегирует в SportbetHandler._parse_phase_markets
# ════════════════════════════════════════════════════════════
class SportbetFastClient(FastOddsClient):
    bk = "sportbet"

    EVENTS_URL = ("https://bthm-server.sportbet.ru/events.table"
                  "?status=live&lang=ru&isTime=true")
    MARKETS_URL = ("https://bthm-server.sportbet.ru/events.markets"
                   "?eventId={eid}&page={page}&lang=ru")

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "origin": "https://sportbet.ru",
        "referer": "https://sportbet.ru/",
    }

    def __init__(self):
        super().__init__(timeout=12.0)
        self._fetch_interval = 1.5
        self._markets_cache: Dict[str, dict] = {}
        self._markets_at: Dict[str, float] = {}

    async def _build_session(self) -> AsyncSession:
        return AsyncSession(
            impersonate="chrome150",
            timeout=self.timeout,
            headers=self.HEADERS,
        )

    async def _fetch_raw(self) -> Optional[Any]:
        try:
            r = await self._session.get(self.EVENTS_URL)
        except Exception as e:
            logger.warning(f"[sportbet_fast] fetch: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[sportbet_fast] HTTP {r.status_code}")
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    @staticmethod
    def _resolve_sport(sport: dict) -> Optional[str]:
        sid = sport.get("id")
        slug = (sport.get("slug") or "").strip().lower()
        if sid == 20 or "table-tennis" in slug:
            return "table_tennis"
        if sid == 23 or "volleyball" in slug:
            return "volleyball"
        if sid == 2 or "basketball" in slug:
            return "basketball"
        return None

    @staticmethod
    def _build_page_key(sport_key: str, phase_num: int) -> str:
        if sport_key in ("basketball", "cyber_basketball"):
            return f"quarter_{phase_num}"
        return f"set_{phase_num}"

    async def _fetch_page_markets(self, eid: str, page_key: str) -> list:
        now = time.time()
        cache_key = f"{eid}::{page_key}"
        cached = self._markets_cache.get(cache_key)
        cached_at = self._markets_at.get(cache_key, 0)
        if cached is not None and now - cached_at < 1.0:
            return cached

        url = self.MARKETS_URL.format(eid=eid, page=page_key)
        try:
            r = await self._session.get(url)
            if r.status_code != 200:
                return []
            data = r.json()
            markets = data.get("data") if isinstance(data, dict) else None
            if not isinstance(markets, list):
                markets = []
            self._markets_cache[cache_key] = markets
            self._markets_at[cache_key] = now
            return markets
        except Exception as e:
            logger.debug(f"[sportbet_fast] markets {page_key}: {e}")
            return []

    async def _build_result_from_event(self, event: dict, sport_key: str) -> Optional[Dict]:
        eid = str(event.get("id", ""))
        if not eid:
            return None

        score_str = event.get("score", "0:0") or "0:0"
        try:
            s1, s2 = map(int, score_str.split(":"))
        except Exception:
            s1 = s2 = 0

        sub1 = sub2 = 0
        parts = [p for p in (event.get("scores") or "").split() if p.strip()]
        if parts and ":" in parts[-1]:
            try:
                sub1, sub2 = map(int, parts[-1].split(":"))
            except Exception:
                pass

        if sport_key in ("basketball", "cyber_basketball"):
            phase_num = len(parts) if parts else 1
        else:
            phase_num = s1 + s2 + 1

        page_key = self._build_page_key(sport_key, phase_num)
        markets_raw = await self._fetch_page_markets(eid, page_key)
        if not markets_raw:
            return None

        teams = event.get("teams") or {}
        t1 = ((teams.get("team1") or {}).get("name") or "").strip()
        t2 = ((teams.get("team2") or {}).get("name") or "").strip()

        set_key = f"set_{phase_num}"
        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        # ── Делегируем парсинг в after_goal ──
        try:
            SportbetHandler._parse_phase_markets(
                markets_raw, set_key, t1, t2, set_markets, outcome_ids,
            )
        except Exception as e:
            logger.warning(f"[sportbet_fast] parse: {e}", exc_info=True)
            return None

        if not set_markets:
            return None

        return {
            "match_id": eid,
            "phase_num": phase_num,
            "score1": s1, "score2": s2,
            "sub_score1": sub1, "sub_score2": sub2,
            "set_markets": set_markets,
        }

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()

        raw = await self._fetch_raw()
        if raw is None:
            return None

        data = (raw or {}).get("data") or {}
        sports = data.get("sports") or []
        if not isinstance(sports, list):
            return None

        target = str(native_id)
        for sport in sports:
            if not isinstance(sport, dict):
                continue
            sport_key = self._resolve_sport(sport)
            if not sport_key:
                continue
            for tournament in sport.get("tournaments", []) or []:
                if not isinstance(tournament, dict):
                    continue
                for event in tournament.get("events", []) or []:
                    if not isinstance(event, dict):
                        continue
                    if str(event.get("id", "")) != target:
                        continue
                    return await self._build_result_from_event(event, sport_key)
        return None

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        await self.start()

        raw = await self._fetch_raw()
        if raw is None:
            return None

        data = (raw or {}).get("data") or {}
        sports = data.get("sports") or []
        if not isinstance(sports, list):
            return None

        for sport_block in sports:
            if not isinstance(sport_block, dict):
                continue
            sport_key = self._resolve_sport(sport_block)
            if not sport_key:
                continue
            for tournament in sport_block.get("tournaments", []) or []:
                if not isinstance(tournament, dict):
                    continue
                for event in tournament.get("events", []) or []:
                    if not isinstance(event, dict):
                        continue
                    teams = event.get("teams") or {}
                    t1 = (teams.get("team1") or {}).get("name", "")
                    t2 = (teams.get("team2") or {}).get("name", "")
                    if _players_match(p1, p2, t1, t2):
                        return await self._build_result_from_event(event, sport_key)
        return None


# ════════════════════════════════════════════════════════════
# Marathon — делегирует в MarathonHandler._parse_event
# ════════════════════════════════════════════════════════════
class MarathonFastClient(FastOddsClient):
    bk = "marathon"

    SNAPSHOT_URL_TPL = (
        "https://new.marathonbet.ru/eag/event-line/api/v1/"
        "sports/by-slug/all-tournaments/live?sportSlug={slug}"
    )
    SSE_URL_TPL = (
        "https://new.marathonbet.ru/eag/event-line/api/v1/"
        "events/by-slug?sportSlug={sportSlug}"
        "&champSlug={champSlug}&matchSlug={matchSlug}"
    )

    HEADERS = {
        "Accept": "text/event-stream",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://new.marathonbet.ru/su/live/table-tennis",
        "Origin": "https://new.marathonbet.ru",
        "x-pan-source": "REDESIGN_WEB",
        "x-pan-target": "BROWSER",
        "x-pan-version": "MOBILE-SSR-2.7.1",
    }

    SLUG_BY_SPORT = {
        "table_tennis":     "table-tennis",
        "volleyball":       "volleyball",
        "basketball":       "basketball",
        "cyber_basketball": "cyber-basketball",
    }

    def __init__(self):
        super().__init__(timeout=15.0)
        self._client: Optional[httpx.AsyncClient] = None
        self._sse_tasks: Dict[str, asyncio.Task] = {}
        self._events_cache: Dict[str, dict] = {}
        self._slugs: Dict[str, dict] = {}
        self._snapshot_cache: Dict[str, tuple] = {}
        self._last_sse_at: Dict[str, float] = {}
        self._parser = MarathonHandler()  # для вызова instance-методов

    async def start(self):
        if self._client is not None:
            return
        from core.fast_config import load_cookies

        cookies = load_cookies("marathon")
        cookies_dict = {c["name"]: c["value"] for c in cookies if c.get("name")}
        if not cookies_dict:
            logger.warning("[marathon_fast] нет cookies")

        self._client = httpx.AsyncClient(
            cookies=cookies_dict,
            headers=self.HEADERS,
            timeout=httpx.Timeout(300.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=8),
        )
        logger.info(f"[marathon_fast] 🍪 {len(cookies_dict)} cookies, httpx готов")

    async def stop(self):
        for task in list(self._sse_tasks.values()):
            if not task.done():
                task.cancel()
        if self._sse_tasks:
            try:
                await asyncio.gather(*self._sse_tasks.values(),
                                     return_exceptions=True)
            except Exception:
                pass
        self._sse_tasks.clear()

        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

        self._events_cache.clear()
        self._slugs.clear()
        self._snapshot_cache.clear()
        self._last_sse_at.clear()
        logger.info("[marathon_fast] остановлен")

    async def _build_session(self):
        return None

    async def _fetch_raw(self):
        return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    async def _fetch_snapshot(self, sport: str) -> Optional[dict]:
        now = time.time()
        cached = self._snapshot_cache.get(sport)
        if cached and now - cached[1] < 5.0:
            return cached[0]

        slug = self.SLUG_BY_SPORT.get(sport)
        if not slug:
            return None

        url = self.SNAPSHOT_URL_TPL.format(slug=slug)
        headers = {**self.HEADERS, "Accept": "application/json"}
        try:
            r = await self._client.get(url, headers=headers, timeout=15.0)
        except Exception as e:
            logger.warning(f"[marathon_fast] snapshot {sport}: {e}")
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None
        self._snapshot_cache[sport] = (data, now)
        return data

    @staticmethod
    def _find_in_snapshot_by_tree(snapshot: dict, tree_id) -> Optional[dict]:
        item_map = (snapshot or {}).get("itemMap") or {}
        if not isinstance(item_map, dict):
            return None
        for tournament in item_map.values():
            if not isinstance(tournament, dict):
                continue
            for event in (tournament.get("liveEvents") or []):
                if not isinstance(event, dict):
                    continue
                if str(event.get("treeId")) == str(tree_id):
                    return event
        return None

    @staticmethod
    def _find_in_snapshot_by_names(snapshot: dict, p1: str, p2: str) -> Optional[dict]:
        item_map = (snapshot or {}).get("itemMap") or {}
        if not isinstance(item_map, dict):
            return None
        for tournament in item_map.values():
            if not isinstance(tournament, dict):
                continue
            for event in (tournament.get("liveEvents") or []):
                if not isinstance(event, dict):
                    continue
                ht = (event.get("homeTeam") or {}).get("members") or []
                at = (event.get("awayTeam") or {}).get("members") or []
                if not ht or not at:
                    continue
                t1 = ht[0].get("name", "")
                t2 = at[0].get("name", "")
                if _players_match(p1, p2, t1, t2):
                    return event
        return None

    @staticmethod
    def _extract_slugs(event: dict) -> Optional[dict]:
        seo = event.get("seo") or {}
        sport_slug = seo.get("sportSlug", "")
        champ_slug = seo.get("champSlug", "")
        match_slug = seo.get("matchSlug", "")
        if not (sport_slug and champ_slug and match_slug):
            return None
        return {
            "sportSlug": sport_slug,
            "champSlug": champ_slug,
            "matchSlug": match_slug,
        }

    def _ensure_sse(self, tree_id: str, slugs: dict):
        existing = self._sse_tasks.get(tree_id)
        if existing and not existing.done():
            return
        try:
            url = self.SSE_URL_TPL.format(**slugs)
        except KeyError:
            return
        task = asyncio.create_task(self._sse_loop(tree_id, url))
        self._sse_tasks[tree_id] = task
        logger.info(f"[marathon_fast] SSE start {tree_id}")

    async def _sse_loop(self, tree_id: str, url: str):
        retry = 1
        while True:
            try:
                async with self._client.stream(
                    "GET", url, headers=self.HEADERS
                ) as response:
                    if response.status_code != 200:
                        await asyncio.sleep(retry)
                        retry = min(retry * 2, 30)
                        continue
                    retry = 1
                    buffer = b""
                    async for chunk in response.aiter_raw():
                        if not chunk:
                            break
                        if chunk.startswith(b"\x1f\x8b"):
                            try:
                                chunk = gzip.decompress(chunk)
                            except Exception:
                                pass
                        buffer += chunk
                        while b"\n\n" in buffer:
                            part, buffer = buffer.split(b"\n\n", 1)
                            try:
                                self._process_sse_part(part, tree_id)
                            except Exception:
                                pass
                            self._last_sse_at[tree_id] = time.time()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"[marathon_fast] SSE {tree_id}: {e}")
                await asyncio.sleep(retry)
                retry = min(retry * 2, 30)

    def _process_sse_part(self, part_bytes: bytes, tree_id: str):
        try:
            text = part_bytes.decode("utf-8", "ignore")
        except Exception:
            return
        data_line = ""
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                if data_line:
                    data_line += "\n"
                data_line += line[5:].lstrip()
        if not data_line:
            return
        try:
            data = json.loads(data_line)
        except Exception:
            return
        if not isinstance(data, list):
            return
        for change in data:
            if isinstance(change, dict):
                try:
                    self._apply_change(change, tree_id)
                except Exception:
                    pass

    def _apply_change(self, change: dict, expected_tree_id: str):
        value = change.get("value")
        if value is None:
            return

        if isinstance(value, dict) and "treeId" in value:
            tid = str(value.get("treeId"))
            if tid == expected_tree_id:
                self._upsert_event(value)
            return

        path = change.get("path") or []
        for p in path:
            if isinstance(p, dict) and p.get("inObj") == "matchScore":
                if isinstance(value, dict):
                    cache = self._events_cache.get(expected_tree_id)
                    if cache and cache.get("_raw"):
                        cache["_raw"]["matchScore"] = value
                return

    def _upsert_event(self, event: dict):
        tid = str(event.get("treeId", ""))
        if not tid:
            return
        cache = self._events_cache.setdefault(tid, {})
        cache["_raw"] = event
        cache["updated_at"] = time.time()

    async def _wait_sse_first_frame(self, tree_id: str, timeout_s: float = 5.0):
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if tree_id in self._last_sse_at:
                return True
            await asyncio.sleep(0.3)
        return False

    async def _build_result(self, cache: dict) -> Optional[Dict]:
        raw = cache.get("_raw")
        if not raw:
            return None
        try:
            parsed = self._parser._parse_event(raw)
        except Exception as e:
            logger.warning(f"[marathon_fast] parse: {e}", exc_info=True)
            return None

        if not parsed:
            return None

        return {
            "match_id": parsed.get("match_id"),
            "phase_num": parsed.get("phase_num", 0),
            "score1": parsed.get("score1", 0),
            "score2": parsed.get("score2", 0),
            "sub_score1": parsed.get("sub_score1", 0),
            "sub_score2": parsed.get("sub_score2", 0),
            "set_markets": parsed.get("set_markets", {}),
        }

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()
        target = str(native_id)

        # Если уже есть в кэше и SSE свежий — сразу
        cache = self._events_cache.get(target)
        if cache and cache.get("_raw") and target in self._last_sse_at:
            return await self._build_result(cache)

        # Иначе — ищем в снапшотах всех наших видов спорта
        for sport in self.SLUG_BY_SPORT.keys():
            snapshot = await self._fetch_snapshot(sport)
            if not snapshot:
                continue
            event = self._find_in_snapshot_by_tree(snapshot, target)
            if not event:
                continue

            if target not in self._slugs:
                slugs = self._extract_slugs(event)
                if not slugs:
                    return None
                self._slugs[target] = slugs
                self._upsert_event(event)

            self._ensure_sse(target, self._slugs[target])
            await self._wait_sse_first_frame(target, timeout_s=5.0)

            cache = self._events_cache.get(target)
            if not cache:
                return None
            return await self._build_result(cache)

        return None

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        await self.start()

        snapshot = await self._fetch_snapshot(sport)
        if not snapshot:
            return None

        event = self._find_in_snapshot_by_names(snapshot, p1, p2)
        if not event:
            return None

        tree_id = str(event.get("treeId", ""))
        if not tree_id:
            return None

        if tree_id not in self._slugs:
            slugs = self._extract_slugs(event)
            if not slugs:
                return None
            self._slugs[tree_id] = slugs
            self._upsert_event(event)

        self._ensure_sse(tree_id, self._slugs[tree_id])
        await self._wait_sse_first_frame(tree_id, timeout_s=5.0)

        cache = self._events_cache.get(tree_id)
        if not cache:
            return None
        return await self._build_result(cache)


# ════════════════════════════════════════════════════════════
# Winline — своя логика (WS + decoder + instance state)
# Дублирует ui/after_goal/winline.py::WinlineHandler._build_snapshot
# 1-в-1, потому что там нужен доступ к decoder и _lines.
# ════════════════════════════════════════════════════════════
_MARKET_TYPE_WINNER      = 51
_MARKET_TYPE_TOTAL       = 71
_MARKET_TYPE_HANDICAP    = 61
_MARKET_TYPE_WINNER_2WAY = 151

_MID_ODD_NT   = 934
_MID_POINT_NT = 952


def _parse_winline_coefficient(coeff: str) -> Tuple[Optional[int], Optional[float]]:
    if not coeff:
        return None, None
    if "/" in coeff:
        parts = coeff.split("/")
        try:
            return int(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            pass
    try:
        val = float(coeff)
    except ValueError:
        return None, None
    if val == int(val) and val <= 10:
        return int(val), None
    return None, val


def _winline_tokenize(name: str) -> List[str]:
    if not name:
        return []
    tokens = re.split(r"\W+", name.lower())
    return [t for t in tokens if len(t) >= 3]


def _winline_teams_match(t1_tokens, t2_tokens, a: str, b: str) -> bool:
    a_tokens = _winline_tokenize(a)
    b_tokens = _winline_tokenize(b)

    def hit(src, dst):
        return any(t in dst for t in src)

    return ((hit(t1_tokens, a_tokens) and hit(t2_tokens, b_tokens)) or
            (hit(t1_tokens, b_tokens) and hit(t2_tokens, a_tokens)))


class WinlineFastClient(FastOddsClient):
    bk = "winline"

    WS_URL = "wss://wss.winline.ru/data_ng?client=newsite&nb=true"

    HANDSHAKE_FRAMES = ["lang", "AA==", "data", "WINLINE", "getdate"]

    HEARTBEAT_INTERVAL = 20.0
    WS_RECONNECT_DELAY = 2.0

    EXTRA_HEADERS = {
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "websocket",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Site": "same-site",
    }

    SPORT_IDS = {
        20:  "table_tennis",
        23:  "volleyball",
        2:   "basketball",
        193: "cyber_basketball",
        153: "cyber_basketball",
    }

    USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/150.0.0.0 Safari/537.36")

    def __init__(self):
        super().__init__(timeout=15.0)
        from ui.after_goal.decoder import DataNgDecoder
        self._decoder = DataNgDecoder()
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lines: Dict[Tuple[int, int, str], dict] = {}
        self._live_events: Dict[int, dict] = {}
        self._cookie_header = ""

    async def start(self):
        if self._ws_task and not self._ws_task.done():
            return
        from core.fast_config import load_cookies
        cookies = load_cookies("winline")
        cookies = [c for c in cookies
                   if "winline" in (c.get("domain") or "").lower()]
        self._cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
            if c.get("name") and c.get("value")
        )
        logger.info(f"[winline_fast] 🍪 отобрано {len(cookies)} cookies")
        self._ws_task = asyncio.create_task(self._ws_loop())
        for _ in range(20):
            if self._live_events:
                break
            await asyncio.sleep(0.5)

    async def stop(self):
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._ws_task = None
        self._heartbeat_task = None
        self._lines.clear()
        self._live_events.clear()

    async def _build_session(self):
        return None

    async def _fetch_raw(self):
        return None

    def _find_match(self, raw, p1, p2):
        return None

    def _parse(self, raw, event):
        return None

    async def _ws_loop(self):
        delay = self.WS_RECONNECT_DELAY
        while True:
            try:
                headers = [
                    ("User-Agent", self.USER_AGENT),
                    ("Origin", "https://winline.ru"),
                ]
                for k, v in self.EXTRA_HEADERS.items():
                    headers.append((k, v))
                if self._cookie_header:
                    headers.append(("Cookie", self._cookie_header))

                async with websockets.connect(
                    self.WS_URL,
                    additional_headers=headers,
                    max_size=50 * 1024 * 1024,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    compression=None,
                ) as ws:
                    self._ws = ws
                    # При новом подключении кэш от прошлой сессии невалиден —
                    # чистим, чтобы _build_snapshot не отдал устаревшие кэфы.
                    self._lines.clear()
                    self._live_events.clear()
                    logger.info("[winline_fast] ✅ WS подключён (кеш сброшен)")
                    for cmd in self.HANDSHAKE_FRAMES:
                        await ws.send(cmd)
                        await asyncio.sleep(0.05)
                    if self._heartbeat_task and not self._heartbeat_task.done():
                        self._heartbeat_task.cancel()
                    self._heartbeat_task = asyncio.create_task(
                        self._heartbeat(ws)
                    )
                    async for frame in ws:
                        try:
                            self._on_frame(frame)
                        except Exception as e:
                            logger.debug(f"[winline_fast] frame: {e}")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"[winline_fast] WS: {type(e).__name__}: {e}")
            self._ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _heartbeat(self, ws):
        while True:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                if ws.closed:
                    break
                await ws.send("getdate")
            except asyncio.CancelledError:
                break
            except Exception:
                break

    def _on_frame(self, payload):
        if isinstance(payload, str):
            return
        try:
            data = bytes(payload) if isinstance(payload, (bytes, bytearray)) else None
            if not data:
                return
            decoded = self._decoder.decode(data)
            if decoded is None:
                return
            items = decoded if isinstance(decoded, list) else [decoded]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in ("prematch", "live"):
                    self._ingest(item)
        except Exception as e:
            logger.debug(f"[winline_fast] decode: {e}")

    def _ingest(self, item: dict):
        for ev in item.get("events") or []:
            if not isinstance(ev, dict):
                continue
            ev_id = ev.get("id")
            if not ev_id:
                continue
            parts = ev.get("participants") or []
            if not parts:
                continue
            score_raw = (ev.get("score") or "").strip()
            set_scores_raw = (ev.get("setScores") or "").strip()
            sub1 = sub2 = 0
            parts_list = []
            if set_scores_raw:
                parts_list = [p.strip() for p in set_scores_raw.split(" - ")
                              if p.strip()]
            if parts_list and ":" in parts_list[-1]:
                try:
                    a, b = parts_list[-1].split(":", 1)
                    sub1, sub2 = int(a), int(b)
                except Exception:
                    pass
            score1 = score2 = 0
            if score_raw and ":" in score_raw:
                try:
                    a, b = score_raw.split(":", 1)
                    score1, score2 = int(a), int(b)
                except Exception:
                    pass
            phase_num = len(parts_list) if parts_list else (score1 + score2 + 1)
            self._live_events[ev_id] = {
                "participants": [str(p) for p in parts],
                "score1": score1,
                "score2": score2,
                "sub_score1": sub1,
                "sub_score2": sub2,
                "phase_num": phase_num,
                "sport_id": ev.get("sportId"),
                "ts": time.time(),
            }
        for line in item.get("lines") or []:
            if not isinstance(line, dict):
                continue
            ev_id = line.get("eventId")
            market_id = line.get("marketId")
            coeff = line.get("coefficient", "") or ""
            if not ev_id or market_id is None:
                continue
            key = (int(ev_id), int(market_id), str(coeff))
            self._lines[key] = line

    async def get_markets_by_native_id(self, native_id) -> Optional[Dict]:
        await self.start()
        try:
            target_id = int(native_id)
        except (ValueError, TypeError):
            return None
        if target_id not in self._live_events:
            for _ in range(10):
                if target_id in self._live_events:
                    break
                await asyncio.sleep(0.3)
        if target_id not in self._live_events:
            return None
        set_markets = self._build_snapshot(target_id)
        if not set_markets:
            return None
        ev_data = self._live_events.get(target_id) or {}
        return {
            "match_id": str(target_id),
            "phase_num": ev_data.get("phase_num", 0) or 0,
            "score1": ev_data.get("score1", 0),
            "score2": ev_data.get("score2", 0),
            "sub_score1": ev_data.get("sub_score1", 0),
            "sub_score2": ev_data.get("sub_score2", 0),
            "set_markets": set_markets,
        }

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        await self.start()
        if not self._live_events:
            return None
        expected_sid = None
        for sid, name in self.SPORT_IDS.items():
            if name == sport:
                expected_sid = sid
                break
        sport_events = [
            (eid, data) for eid, data in self._live_events.items()
            if expected_sid is None or data.get("sport_id") == expected_sid
        ]
        t1_tokens = _winline_tokenize(p1)
        t2_tokens = _winline_tokenize(p2)
        target_id = None
        for eid, data in sport_events:
            parts = data.get("participants") or []
            if len(parts) < 2:
                continue
            if _winline_teams_match(t1_tokens, t2_tokens, parts[0], parts[1]):
                target_id = eid
                break
        if target_id is None:
            return None
        set_markets = self._build_snapshot(target_id)
        ev_data = self._live_events.get(target_id) or {}
        return {
            "match_id": str(target_id),
            "phase_num": ev_data.get("phase_num", 0) or 0,
            "score1": ev_data.get("score1", 0),
            "score2": ev_data.get("score2", 0),
            "sub_score1": ev_data.get("sub_score1", 0),
            "sub_score2": ev_data.get("sub_score2", 0),
            "set_markets": set_markets,
        }

    def _build_snapshot(self, event_id: int) -> Dict[str, dict]:
        menu = self._decoder.markets
        set_markets: Dict[str, dict] = {}
        for (ev_id, market_id, coeff), line in list(self._lines.items()):
            if ev_id != event_id:
                continue
            if market_id not in menu:
                continue
            mtype, mname = menu[market_id]
            phase, line_val = _parse_winline_coefficient(coeff)
            if phase is None:
                continue
            set_key = f"set_{phase}"
            values = line.get("values") or []

            if mtype == _MARKET_TYPE_WINNER and len(values) >= 3:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                if "1" not in w:
                    w["1"] = values[0]
                if "X" not in w:
                    w["X"] = values[1]
                if "2" not in w:
                    w["2"] = values[2]
            elif mtype == _MARKET_TYPE_WINNER_2WAY and len(values) >= 2:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                if "1" not in w:
                    w["1"] = values[0]
                if "2" not in w:
                    w["2"] = values[1]
            elif (mtype == _MARKET_TYPE_TOTAL
                    and len(values) >= 2
                    and line_val is not None):
                t = set_markets.setdefault(set_key, {}).setdefault("total", {})
                if "line" not in t:
                    t["line"] = line_val
                    t["over"] = values[0]
                    t["under"] = values[1]
            elif (mtype == _MARKET_TYPE_HANDICAP
                    and len(values) >= 2
                    and line_val is not None):
                h = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
                if "1" not in h:
                    h["1"] = {"line": line_val, "odd": values[0]}
                    h["2"] = {"line": -line_val, "odd": values[1]}
            elif market_id == _MID_ODD_NT and len(values) >= 2:
                om = set_markets.setdefault(set_key, {}).setdefault("odd", {})
                om["odd"] = values[0]
                om["even"] = values[1]
            elif market_id == _MID_POINT_NT and len(values) >= 2:
                pm = set_markets.setdefault(set_key, {}).setdefault("point", {})
                pm["1"] = values[0]
                pm["2"] = values[1]
                pm["line"] = int(line_val) if line_val is not None else 0
        return set_markets


# ════════════════════════════════════════════════════════════
# Реестр
# ════════════════════════════════════════════════════════════
_CLIENTS: Dict[str, FastOddsClient] = {}

_CLASSES = {
    "fonbet":   FonbetFastClient,
    "pari":     PariFastClient,
    "olimp":    OlimpFastClient,
    "betcity":  BetcityFastClient,
    "sportbet": SportbetFastClient,
    "marathon": MarathonFastClient,
    "winline":  WinlineFastClient,
}


def get_fast_client(bk: str) -> Optional[FastOddsClient]:
    bk = (bk or "").lower().strip()
    if bk in _CLIENTS:
        return _CLIENTS[bk]
    cls = _CLASSES.get(bk)
    if not cls:
        return None
    _CLIENTS[bk] = cls()
    return _CLIENTS[bk]


def has_fast_client(bk: str) -> bool:
    return (bk or "").lower().strip() in _CLASSES


async def shutdown_all():
    for client in list(_CLIENTS.values()):
        try:
            await client.stop()
        except Exception:
            pass
    _CLIENTS.clear()