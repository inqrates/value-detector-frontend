# core/fast_client.py
"""
HTTP-клиенты для получения внутриматчевых кэфов с fast БК.

Работают по аналогии с бэкенд-парсерами, но в лёгком режиме:
  - один AsyncSession на БК
  - кэш raw-ответа N секунд (чтобы 5 матчей не дёргали API 5 раз)
  - поиск матча по именам игроков
  - возвращают set_markets в том же формате, что slow-хендлеры

"""
import asyncio
import json
import logging
import re
import time
import gzip
from typing import Optional, Dict, List, Any, Tuple
import httpx  # в начале файла, если ещё нет
from curl_cffi.requests import AsyncSession
import websockets
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# Нормализация имён (упрощённый аналог core/normalizer.py)
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

    # ── Override в наследниках ──
    async def _build_session(self) -> AsyncSession:
        raise NotImplementedError

    async def _fetch_raw(self) -> Optional[Any]:
        raise NotImplementedError

    def _find_match(self, raw: Any, p1: str, p2: str) -> Optional[dict]:
        """Возвращает найденное событие (dict) или None."""
        raise NotImplementedError

    def _parse(self, raw: Any, event: dict) -> Optional[Dict]:
        """Собирает итоговый set_markets."""
        raise NotImplementedError

    # ── Публичный API ──
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

        event = self._find_match(raw, p1, p2)
        if not event:
            return None

        try:
            return self._parse(raw, event)
        except Exception as e:
            logger.warning(f"[{self.bk}_fast] parse: {e}", exc_info=True)
            return None


# ════════════════════════════════════════════════════════════
# Fonbet
# ════════════════════════════════════════════════════════════
class FonbetFastClient(FastOddsClient):
    bk = "fonbet"

    URL = "https://line-lb54-w.bk6bba-resources.com/ma/events/listBase"

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "origin": "https://fon.bet",
        "referer": "https://fon.bet/",
    }

    # Коды факторов (см. бэкенд fonbet_api.py и фронт fonbet.py)
    F_WIN1, F_WIN2 = 921, 923
    F_TOTAL_OVER, F_TOTAL_UNDER = 930, 931
    F_HCP1, F_HCP2 = 910, 912
    F_HCP1_ALT, F_HCP2_ALT = 1845, 1846
    F_IT1_OVER, F_IT1_UNDER = 974, 976
    F_IT2_OVER, F_IT2_UNDER = 978, 980
    F_EVEN, F_ODD = 698, 699
    F_PT1, F_PT2 = 2393, 2394

    def __init__(self):
        super().__init__(timeout=10.0)
        self._fetch_interval = 2.0   # listBase тяжёлый (~200 KB)

    async def _build_session(self) -> AsyncSession:
        return AsyncSession(
            impersonate="chrome150",
            timeout=self.timeout,
            headers=self.HEADERS,
        )

    async def _fetch_raw(self) -> Optional[Any]:
        try:
            r = await self._session.get(
                self.URL,
                params={"lang": "ru", "scopeMarket": "1600"},
            )
        except Exception as e:
            logger.warning(f"[fonbet_fast] fetch: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[fonbet_fast] HTTP {r.status_code}")
            return None
        try:
            return r.json()
        except Exception:
            return None

    # ── Запрос по конкретному матчу (eventId из /match_state) ──
    EVENT_URL = "https://line-lb54-w.bk6bba-resources.com/ma/events/event"
    SCOPE = "1600"

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
            logger.warning(
                f"[fonbet_fast] event {native_id} HTTP {r.status_code}"
            )
            return None
        try:
            data = r.json()
        except Exception:
            return None

        try:
            return self._parse_event_response(data, native_id)
        except Exception as e:
            logger.warning(f"[fonbet_fast] parse event: {e}", exc_info=True)
            return None

    def _parse_event_response(self, data: dict, native_id) -> Optional[Dict]:
        """
        Разбирает ответ /ma/events/event для конкретного матча.
        Логика 1-в-1 как у Pari (_parse_event_response), но с
        factor-кодами Fonbet (910/912, 930/931, 1845/1846, ...).
        """
        events = data.get("events") or []
        miscs = {m.get("id"): m for m in (data.get("eventMiscs") or [])}

        root = None
        for ev in events:
            if str(ev.get("id")) == str(native_id):
                root = ev
                break
        if not root:
            return None

        children = [ev for ev in events
                    if str(ev.get("parentId")) == str(native_id)]
        children.sort(key=lambda e: e.get("sortOrder", ""))

        active_child = children[-1] if children else None
        for info in data.get("liveEventInfos") or []:
            if str(info.get("eventId")) != str(native_id):
                continue
            for sub in info.get("subscores") or []:
                c1 = int(sub.get("c1", 0) or 0)
                c2 = int(sub.get("c2", 0) or 0)
                if c1 == 0 and c2 == 0:
                    continue
                mm = re.search(r"(\d+)", sub.get("kindName", "") or "")
                if not mm:
                    continue
                target = int(mm.group(1))
                for ch in children:
                    mm2 = re.search(r"(\d+)", ch.get("name", "") or "")
                    if mm2 and int(mm2.group(1)) == target:
                        active_child = ch
                        break
                break
            break

        if not active_child:
            return None

        child_id = str(active_child.get("id"))
        child_name = active_child.get("name", "") or ""
        mm = re.search(r"(\d+)", child_name)
        phase_num = int(mm.group(1)) if mm else 1
        set_key = f"set_{phase_num}"

        try:
            native_id_int = int(native_id)
        except Exception:
            native_id_int = None
        misc_root = miscs.get(native_id_int) or {}
        score1 = int(misc_root.get("score1", 0) or 0)
        score2 = int(misc_root.get("score2", 0) or 0)
        comment = misc_root.get("comment", "") or ""
        sub1, sub2 = self._parse_sub(comment)

        factors: List[dict] = []
        for block in data.get("customFactors") or []:
            if str(block.get("e")) == child_id:
                factors = block.get("factors") or []
                break
        if not factors:
            return None

        by_code = {f.get("f"): f for f in factors if f.get("f") is not None}
        markets = self._build_markets(by_code)
        if not markets:
            return None

        return {
            "match_id": str(native_id),
            "phase_num": phase_num,
            "score1": score1, "score2": score2,
            "sub_score1": sub1, "sub_score2": sub2,
            "set_markets": {set_key: markets},
        }

    def _find_match(self, raw: Any, p1: str, p2: str) -> Optional[dict]:
        # Ищем корневое событие (без parentId)
        for ev in raw.get("events") or []:
            if ev.get("parentId"):
                continue
            t1 = ev.get("team1", "") or ev.get("participant1", "")
            t2 = ev.get("team2", "") or ev.get("participant2", "")
            if _players_match(p1, p2, t1, t2):
                return ev
        return None

    def _parse(self, raw: Any, root: dict) -> Optional[Dict]:
        root_id = str(root.get("id"))

        # ── Дочерние события (партии) ──
        children = [ev for ev in (raw.get("events") or [])
                    if str(ev.get("parentId")) == root_id]
        if not children:
            return None
        children.sort(key=lambda e: e.get("sortOrder", ""))

        # ── Активная партия: из liveEventInfos, иначе последняя ──
        active = children[-1]
        for info in raw.get("liveEventInfos") or []:
            if str(info.get("eventId")) != root_id:
                continue
            for sub in info.get("subscores") or []:
                c1 = int(sub.get("c1", 0) or 0)
                c2 = int(sub.get("c2", 0) or 0)
                if c1 == 0 and c2 == 0:
                    continue
                mm = re.search(r"(\d+)", sub.get("kindName", "") or "")
                if not mm:
                    continue
                target_num = int(mm.group(1))
                for ch in children:
                    mm2 = re.search(r"(\d+)", ch.get("name", "") or "")
                    if mm2 and int(mm2.group(1)) == target_num:
                        active = ch
                        break
                break
            break

        child_id = str(active.get("id"))
        child_name = active.get("name", "") or ""
        mm = re.search(r"(\d+)", child_name)
        phase_num = int(mm.group(1)) if mm else 1
        set_key = f"set_{phase_num}"

        # ── Факторы ──
        factors: List[dict] = []
        for block in raw.get("customFactors") or []:
            if str(block.get("e")) == child_id:
                factors = block.get("factors") or []
                break
        if not factors:
            return None

        # ── Счёт ──
        misc = {}
        for m in raw.get("eventMiscs") or []:
            if str(m.get("id")) == root_id:
                misc = m
                break
        score1 = int(misc.get("score1", 0) or 0)
        score2 = int(misc.get("score2", 0) or 0)
        sub1, sub2 = self._parse_sub(misc.get("comment", "") or "")

        # ── Рынки ──
        by_code = {f.get("f"): f for f in factors if f.get("f") is not None}
        markets = self._build_markets(by_code)

        if not markets:
            return None

        return {
            "match_id": root_id,
            "phase_num": phase_num,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": {set_key: markets},
        }

    @staticmethod
    def _parse_sub(comment: str) -> Tuple[int, int]:
        if not comment:
            return 0, 0
        pairs = re.findall(r"(\d+)[-:](\d+)", comment)
        if pairs:
            return int(pairs[-1][0]), int(pairs[-1][1])
        return 0, 0

    def _build_markets(self, by_code: Dict) -> Dict:
        result: Dict[str, Any] = {}

        # WINNER
        w1 = by_code.get(self.F_WIN1)
        w2 = by_code.get(self.F_WIN2)
        if w1 or w2:
            w = {}
            if w1: w["1"] = float(w1.get("v", 0) or 0)
            if w2: w["2"] = float(w2.get("v", 0) or 0)
            if w:
                result["winner"] = w

        # TOTAL
        to = by_code.get(self.F_TOTAL_OVER)
        tu = by_code.get(self.F_TOTAL_UNDER)
        if to and tu:
            line = self._line(to) or self._line(tu)
            result["total"] = {
                "line": line,
                "over": float(to.get("v", 0) or 0),
                "under": float(tu.get("v", 0) or 0),
            }

        # HANDICAP
        h1 = by_code.get(self.F_HCP1) or by_code.get(self.F_HCP1_ALT)
        h2 = by_code.get(self.F_HCP2) or by_code.get(self.F_HCP2_ALT)
        if h1 or h2:
            hk = {}
            if h1:
                hk["1"] = {"line": self._line(h1), "odd": float(h1.get("v", 0) or 0)}
            if h2:
                hk["2"] = {"line": self._line(h2), "odd": float(h2.get("v", 0) or 0)}
            if hk:
                result["handicap"] = hk

        # IT (индивидуальные тоталы)
        it = {}
        for player, (oc, uc) in (
            ("1", (self.F_IT1_OVER, self.F_IT1_UNDER)),
            ("2", (self.F_IT2_OVER, self.F_IT2_UNDER)),
        ):
            fo = by_code.get(oc)
            fu = by_code.get(uc)
            if fo and fu:
                line = self._line_pos(fo)
                if line > 0:
                    it[player] = {
                        "line": line,
                        "over": float(fo.get("v", 0) or 0),
                        "under": float(fu.get("v", 0) or 0),
                    }
        if it:
            result["it"] = it

        # ODD (чёт/нечёт)
        fe = by_code.get(self.F_EVEN)
        fo = by_code.get(self.F_ODD)
        if fe and fo:
            result["odd"] = {
                "even": float(fe.get("v", 0) or 0),
                "odd":  float(fo.get("v", 0) or 0),
            }

        # POINT (следующее очко)
        p1 = by_code.get(self.F_PT1)
        p2 = by_code.get(self.F_PT2)
        if p1 and p2:
            result["point"] = {
                "1": float(p1.get("v", 0) or 0),
                "2": float(p2.get("v", 0) or 0),
            }

        return result

    @staticmethod
    def _line(factor: dict) -> float:
        pt = str(factor.get("pt", "")).strip()
        m = re.search(r"([+-]?\d+\.?\d*)", pt)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        try:
            return float(factor.get("p", 0)) / 100.0
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _line_pos(factor: dict) -> float:
        pt = str(factor.get("pt", "")).strip()
        m = re.search(r"(\d+\.?\d*)", pt)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 0.0


# ════════════════════════════════════════════════════════════
# Pari (копия Fonbet, другой домен и scopeMarket)
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
            logger.warning(f"[pari_fast] fetch event {native_id}: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[pari_fast] event {native_id} HTTP {r.status_code}")
            return None
        try:
            data = r.json()
        except Exception:
            return None

        try:
            return self._parse_event_response(data, native_id)
        except Exception as e:
            logger.warning(f"[pari_fast] parse: {e}", exc_info=True)
            return None

    def _parse_event_response(self, data: dict, native_id) -> Optional[Dict]:
        events = data.get("events") or []
        miscs = {m.get("id"): m for m in (data.get("eventMiscs") or [])}

        root = None
        for ev in events:
            if str(ev.get("id")) == str(native_id):
                root = ev
                break
        if not root:
            return None

        children = [ev for ev in events
                    if str(ev.get("parentId")) == str(native_id)]
        children.sort(key=lambda e: e.get("sortOrder", ""))

        active_child = children[-1] if children else None
        for info in data.get("liveEventInfos") or []:
            if str(info.get("eventId")) != str(native_id):
                continue
            for sub in info.get("subscores") or []:
                c1 = int(sub.get("c1", 0) or 0)
                c2 = int(sub.get("c2", 0) or 0)
                if c1 == 0 and c2 == 0:
                    continue
                mm = re.search(r"(\d+)", sub.get("kindName", "") or "")
                if not mm:
                    continue
                target = int(mm.group(1))
                for ch in children:
                    mm2 = re.search(r"(\d+)", ch.get("name", "") or "")
                    if mm2 and int(mm2.group(1)) == target:
                        active_child = ch
                        break
                break
            break

        if not active_child:
            return None

        child_id = str(active_child.get("id"))
        child_name = active_child.get("name", "") or ""
        mm = re.search(r"(\d+)", child_name)
        phase_num = int(mm.group(1)) if mm else 1
        set_key = f"set_{phase_num}"

        misc_root = miscs.get(int(native_id)) or {}
        score1 = int(misc_root.get("score1", 0) or 0)
        score2 = int(misc_root.get("score2", 0) or 0)
        comment = misc_root.get("comment", "") or ""
        sub1, sub2 = self._parse_sub(comment)

        factors = []
        for block in data.get("customFactors") or []:
            if str(block.get("e")) == child_id:
                factors = block.get("factors") or []
                break
        if not factors:
            return None

        markets = self._build_markets(factors)
        if not markets:
            return None

        return {
            "match_id": str(native_id),
            "phase_num": phase_num,
            "score1": score1, "score2": score2,
            "sub_score1": sub1, "sub_score2": sub2,
            "set_markets": {set_key: markets},
        }

    @staticmethod
    def _parse_sub(comment: str) -> Tuple[int, int]:
        if not comment:
            return 0, 0
        pairs = re.findall(r"(\d+)[-:](\d+)", comment)
        if pairs:
            return int(pairs[-1][0]), int(pairs[-1][1])
        return 0, 0

    def _build_markets(self, factors: list) -> Dict:
        by_code = {f.get("f"): f for f in factors if f.get("f") is not None}
        result: Dict[str, Any] = {}

        w1, w2 = by_code.get(921), by_code.get(923)
        if w1 or w2:
            w = {}
            if w1: w["1"] = float(w1.get("v", 0) or 0)
            if w2: w["2"] = float(w2.get("v", 0) or 0)
            if w:
                result["winner"] = w

        to, tu = by_code.get(1696), by_code.get(1697)
        if to and tu:
            line = self._line(to) or self._line(tu)
            result["total"] = {
                "line": line,
                "over": float(to.get("v", 0) or 0),
                "under": float(tu.get("v", 0) or 0),
            }

        h1, h2 = by_code.get(910), by_code.get(912)
        if h1 or h2:
            hk = {}
            if h1: hk["1"] = {"line": self._line(h1),
                              "odd": float(h1.get("v", 0) or 0)}
            if h2: hk["2"] = {"line": self._line(h2),
                              "odd": float(h2.get("v", 0) or 0)}
            if hk:
                result["handicap"] = hk

        it = {}
        for player, (oc, uc) in (("1", (974, 976)), ("2", (978, 980))):
            fo, fu = by_code.get(oc), by_code.get(uc)
            if fo and fu:
                line = self._line_pos(fo)
                if line > 0:
                    it[player] = {
                        "line": line,
                        "over": float(fo.get("v", 0) or 0),
                        "under": float(fu.get("v", 0) or 0),
                    }
        if it:
            result["it"] = it

        fe, fo = by_code.get(698), by_code.get(699)
        if fe and fo:
            result["odd"] = {
                "even": float(fe.get("v", 0) or 0),
                "odd":  float(fo.get("v", 0) or 0),
            }

        p1, p2 = by_code.get(2393), by_code.get(2394)
        if p1 and p2:
            result["point"] = {
                "1": float(p1.get("v", 0) or 0),
                "2": float(p2.get("v", 0) or 0),
            }

        return result

    @staticmethod
    def _line(factor: dict) -> float:
        pt = str(factor.get("pt", "")).strip()
        m = re.search(r"([+-]?\d+\.?\d*)", pt)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        try:
            return float(factor.get("p", 0)) / 100.0
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _line_pos(factor: dict) -> float:
        pt = str(factor.get("pt", "")).strip()
        m = re.search(r"(\d+\.?\d*)", pt)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 0.0
    
# ════════════════════════════════════════════════════════════
# Olimp (открытое API, tableType == "OTHER" — фазовые рынки)
# ════════════════════════════════════════════════════════════
class OlimpFastClient(FastOddsClient):
    bk = "olimp"

    URL = "https://www.olimp.bet/api/v4/0/live/sports-with-competitions-with-events"

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "X-Cupis": "1",
        "X-Olimp": "cupis-desktop",
        "Origin": "https://www.olimp.bet",
        "Referer": "https://www.olimp.bet/live",
    }

    _P = r"[СЧПCQPсчпcqp]"    # префикс фазы: Сет / Четверть / Период

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

    def _find_match(self, raw: Any, p1: str, p2: str) -> Optional[dict]:
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            comps = payload.get("competitionsWithEvents")
            if not isinstance(comps, list):
                continue
            for block in comps:
                for event in (block.get("events") or []):
                    if not isinstance(event, dict):
                        continue
                    t1 = event.get("team1Name", "")
                    t2 = event.get("team2Name", "")
                    if _players_match(p1, p2, t1, t2):
                        return event
        return None

    def _parse(self, raw: Any, event: dict) -> Optional[Dict]:
        match_id = str(event.get("id") or "")
        if not match_id:
            return None

        score_str = event.get("score", "0:0") or "0:0"
        try:
            score1, score2 = map(int, score_str.split(":"))
        except Exception:
            score1 = score2 = 0

        # sub_score из mapsScore
        pairs = []
        for m in event.get("mapsScore") or []:
            if isinstance(m, dict):
                try:
                    pairs.append((int(m.get("team1", 0) or 0),
                                  int(m.get("team2", 0) or 0)))
                except Exception:
                    pairs.append((0, 0))
        sub1 = sub2 = 0
        if pairs:
            sub1, sub2 = pairs[-1]

        # phase_num
        comment = event.get("comment", "") or ""
        phase_num = self._resolve_phase_num(
            event.get("sportId"), score1, score2, comment, pairs, event
        )

        set_key = f"set_{phase_num}"
        markets = self._build_markets(event, phase_num)
        if not markets:
            return None

        return {
            "match_id": match_id,
            "phase_num": phase_num,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": {set_key: markets},
        }

    @staticmethod
    def _resolve_phase_num(sport_id, score1, score2, comment, pairs, event):
        sid = str(sport_id or "")
        if sid in ("5", "140"):    # баскет / кибербаскет
            mm = re.search(r"(\d+)-я\s+четверть", comment)
            if mm:
                return int(mm.group(1))
            return len(pairs) if pairs else 1

        # НТ и волейбол — из "#N" в comment или score1 + score2 + 1
        mm = re.search(r"#(\d+)", comment)
        if mm:
            return int(mm.group(1))
        return score1 + score2 + 1

    def _build_markets(self, event: dict, phase_num: int) -> Dict:
        result: Dict[str, Any] = {}

        for out in event.get("outcomes", []) or []:
            if not isinstance(out, dict):
                continue
            if (out.get("tableType") or "").upper() != "OTHER":
                continue

            short = (out.get("shortName") or "").strip()
            if not short:
                continue

            try:
                prob = float(str(out.get("probability", "0")).replace(",", "."))
            except Exception:
                continue
            if prob <= 1.01:
                continue
            try:
                param = float(str(out.get("param", "0")).replace(",", "."))
            except Exception:
                param = 0.0

            P = self._P

            # Winner партии: С5П1 / Ч4П2
            m = re.match(rf"^{P}(\d+)П([12])$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                result.setdefault("winner", {})[m.group(2)] = prob
                continue

            # Фора: С5Ф1К
            m = re.match(rf"^{P}(\d+)Ф([12])К$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                h = result.setdefault("handicap", {}).setdefault(m.group(2), {})
                if "line" not in h:
                    h["line"] = param
                    h["odd"] = prob
                continue

            # Тотал: С5ТотС5ТотМ / С5ТотС5ТотБ
            m = re.match(rf"^{P}(\d+)Тот{P}\d+Тот([МБ])$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                side = "under" if m.group(2) == "М" else "over"
                t = result.setdefault("total", {})
                t.setdefault("line", param)
                t[side] = prob
                continue

            # Чёт/Нечёт: 5СТотЧет / 5СТотНечет
            m = re.match(rf"^(?:{P})?(\d+)(?:{P})?ТотЧет$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                result.setdefault("odd", {})["even"] = prob
                continue
            m = re.match(rf"^(?:{P})?(\d+)(?:{P})?ТотНечет$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                result.setdefault("odd", {})["odd"] = prob
                continue

            # ИТ игрока (старый формат): Ч4ИТ1Б / Ч4ИТ2М
            m = re.match(rf"^{P}(\d+)ИТ([12])([БМ])$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                side = "over" if m.group(3) == "Б" else "under"
                it = result.setdefault("it", {}).setdefault(m.group(2), {})
                it.setdefault("line", param)
                it[side] = prob
                continue

            # ИТ игрока (новый формат): С5ТотК1С5ТотК1М
            m = re.match(rf"^{P}(\d+)ТотК([12]){P}\d+ТотК\2([МБ])$", short)
            if m:
                if int(m.group(1)) != phase_num:
                    continue
                side = "over" if m.group(3) == "Б" else "under"
                it = result.setdefault("it", {}).setdefault(m.group(2), {})
                it.setdefault("line", param)
                it[side] = prob
                continue

        return result

# ════════════════════════════════════════════════════════════
# Betcity (HTTP + cookies из fast_cookies/betcity.json)
# ════════════════════════════════════════════════════════════
class BetcityFastClient(FastOddsClient):
    bk = "betcity"

    URL = ("https://ad.betcity.ru/d/on_air/bets"
           "?rev=8&add=dep_events&ver=88&csn=ooca9s&lng=0")

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "referer": "https://betcity.ru/",
        "origin": "https://betcity.ru",
    }

    # ID спортов Betcity
    SPORT_IDS = {
        "46": "table_tennis",
        "12": "volleyball",
        "3":  "basketball",
    }

    # Коды market_id в блоке ext (по дампу бэкенда)
    M_WINNER   = "35"
    M_HANDICAP = "905"
    M_IT       = "913"

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

    def _find_match(self, raw: Any, p1: str, p2: str) -> Optional[dict]:
        reply = raw.get("reply") or {}
        sports = reply.get("sports") or {}
        if not isinstance(sports, dict):
            return None

        for sid_str, sport_block in sports.items():
            sport_key = self.SPORT_IDS.get(str(sid_str))
            if not sport_key:
                continue
            if not isinstance(sport_block, dict):
                continue

            for chmp in (sport_block.get("chmps") or {}).values():
                if not isinstance(chmp, dict):
                    continue
                is_cyber = chmp.get("is_cyber", 0)
                eff_sport = sport_key
                if sport_key == "basketball" and is_cyber == 1:
                    eff_sport = "cyber_basketball"

                for ev_id, ev in (chmp.get("evts") or {}).items():
                    if not isinstance(ev, dict):
                        continue
                    if ev.get("team_type_f", 0) != 0:
                        continue
                    if ev.get("is_dep", 0) == 1:
                        continue

                    t1 = ev.get("name_ht", "")
                    t2 = ev.get("name_at", "")
                    if _players_match(p1, p2, t1, t2):
                        return {
                            "ev_id": str(ev_id),
                            "ev": ev,
                            "sport_key": eff_sport,
                        }
        return None

    def _parse(self, raw: Any, found: dict) -> Optional[Dict]:
        ev_id = found["ev_id"]
        ev = found["ev"]
        sport_key = found.get("sport_key", "table_tennis")

        # Счёт партий
        score_str = ev.get("sc_ev", "0:0") or "0:0"
        try:
            s1, s2 = map(int, score_str.split(":"))
        except Exception:
            s1 = s2 = 0

        # История партий из sc_inter: "11:8,9:11,7:5"
        pairs = []
        for part in (ev.get("sc_inter") or "").split(","):
            part = part.strip()
            if ":" in part:
                try:
                    a, b = part.split(":", 1)
                    pairs.append((int(a), int(b)))
                except Exception:
                    pass

        if sport_key == "table_tennis":
            phase_num = s1 + s2 + 1
        elif sport_key == "volleyball":
            phase_num = s1 + s2 + 1
        else:
            phase_num = len(pairs) if pairs else 1

        sub1 = sub2 = 0
        if pairs:
            sub1, sub2 = pairs[-1]

        # Фазовые рынки
        markets = self._parse_ext(ev, phase_num)

        if not markets:
            return None

        set_key = f"set_{phase_num}"
        return {
            "match_id": ev_id,
            "phase_num": phase_num,
            "score1": s1, "score2": s2,
            "sub_score1": sub1, "sub_score2": sub2,
            "set_markets": {set_key: markets},
        }

    def _parse_ext(self, ev: dict, phase_num: int) -> Dict:
        """Парсит блок ext для активной партии."""
        ext = ev.get("ext") or {}
        if not isinstance(ext, dict):
            return {}

        result: Dict[str, Any] = {}
        num_re = re.compile(r"(\d+\.?\d*)")

        for market_id, market_data in ext.items():
            mid = str(market_id)
            if mid not in (self.M_WINNER, self.M_HANDICAP, self.M_IT):
                continue
            if not isinstance(market_data, dict):
                continue

            rows = market_data.get("rows") or {}
            for row in rows.values():
                if not isinstance(row, dict):
                    continue
                row_name = row.get("name", "") or ""
                mm = re.search(r"(\d+)-я партия", row_name)
                if not mm:
                    continue
                if int(mm.group(1)) != phase_num:
                    continue

                data_block = row.get("data") or {}
                for block in data_block.values():
                    if not isinstance(block, dict):
                        continue
                    blocks = block.get("blocks") or {}
                    if not isinstance(blocks, dict):
                        continue

                    # mid=35: W (winner), T1..T5 (totals), TEO (odd/even)
                    if mid == self.M_WINNER:
                        w = blocks.get("W") or {}
                        for side, key in (("1", "P1"), ("2", "P2")):
                            info = w.get(key) or {}
                            try:
                                odd = float(info.get("kf", 0) or 0)
                            except Exception:
                                continue
                            if odd > 1.01:
                                result.setdefault("winner", {})[side] = odd

                        t1 = blocks.get("T1") or {}
                        if t1:
                            try:
                                line = float(t1.get("Tot", 0) or 0)
                            except Exception:
                                line = 0.0
                            if line > 0:
                                try:
                                    o_over = float((t1.get("Tb") or {}).get("kf", 0) or 0)
                                    o_under = float((t1.get("Tm") or {}).get("kf", 0) or 0)
                                except Exception:
                                    o_over = o_under = 0.0
                                if o_over > 1.01 and o_under > 1.01:
                                    result["total"] = {
                                        "line": line,
                                        "over": o_over,
                                        "under": o_under,
                                    }

                        teo = blocks.get("TEO") or {}
                        if teo:
                            try:
                                o_even = float((teo.get("E") or {}).get("kf", 0) or 0)
                                o_odd = float((teo.get("O") or {}).get("kf", 0) or 0)
                            except Exception:
                                o_even = o_odd = 0.0
                            if o_even > 1.01 and o_odd > 1.01:
                                result["odd"] = {"even": o_even, "odd": o_odd}

                    # mid=905: форы
                    elif mid == self.M_HANDICAP:
                        for f_key, f_block in blocks.items():
                            if not f_key.startswith("F"):
                                continue
                            if "Kf_F1" not in f_block or "Kf_F2" not in f_block:
                                continue
                            for side, lkey, kkey in (
                                ("1", "F1", "Kf_F1"),
                                ("2", "F2", "Kf_F2"),
                            ):
                                try:
                                    line = float(f_block.get(lkey, 0) or 0)
                                    odd = float((f_block[kkey] or {}).get("kf", 0) or 0)
                                except Exception:
                                    continue
                                if odd > 1.01:
                                    result.setdefault("handicap", {})[side] = {
                                        "line": line,
                                        "odd": odd,
                                    }

                    # mid=913: ИТ
                    elif mid == self.M_IT:
                        for it_key, it_block in blocks.items():
                            if "Tm" not in it_block or "Tb" not in it_block:
                                continue
                            try:
                                line = float(it_block.get("Tot", 0) or 0)
                            except Exception:
                                continue
                            if line <= 0:
                                continue
                            player = "1" if ("IT1" in it_key or "_T1" in it_key) else "2"
                            try:
                                o_over = float((it_block["Tb"] or {}).get("kf", 0) or 0)
                                o_under = float((it_block["Tm"] or {}).get("kf", 0) or 0)
                            except Exception:
                                continue
                            if o_over > 1.01 or o_under > 1.01:
                                it = result.setdefault("it", {}).setdefault(player, {})
                                it["line"] = line
                                if o_over > 1.01:
                                    it["over"] = o_over
                                if o_under > 1.01:
                                    it["under"] = o_under

        return result

# ════════════════════════════════════════════════════════════
# Sportbet (HTTP без cookies)
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

    def _find_match(self, raw: Any, p1: str, p2: str) -> Optional[dict]:
        data = (raw or {}).get("data") or {}
        sports = data.get("sports") or []
        if not isinstance(sports, list):
            return None

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
                    teams = event.get("teams") or {}
                    t1 = (teams.get("team1") or {}).get("name", "")
                    t2 = (teams.get("team2") or {}).get("name", "")
                    if _players_match(p1, p2, t1, t2):
                        return {
                            "event": event,
                            "sport_key": sport_key,
                        }
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

    async def _fetch_page_markets(self, eid: str, page_key: str) -> list:
        """Запрашивает рынки для конкретной страницы (set_N)."""
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

    def _build_page_key(self, sport_key: str, phase_num: int) -> str:
        if sport_key in ("basketball", "cyber_basketball"):
            return f"quarter_{phase_num}"
        return f"set_{phase_num}"

    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        """Override: Sportbet требует 2 запроса — снапшот + markets для партии."""
        await self.start()

        raw = await self._fetch_raw()
        if raw is None:
            return None

        found = self._find_match(raw, p1, p2)
        if not found:
            return None

        event = found["event"]
        sport_key = found["sport_key"]
        eid = str(event.get("id", ""))
        if not eid:
            return None

        # Счёт
        score_str = event.get("score", "0:0") or "0:0"
        try:
            s1, s2 = map(int, score_str.split(":"))
        except Exception:
            s1 = s2 = 0

        # sub_score из scores ("11:8 9:11 7:5")
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

        markets = self._parse_markets(markets_raw, event, sport_key)
        if not markets:
            return None

        set_key = f"set_{phase_num}"
        return {
            "match_id": eid,
            "phase_num": phase_num,
            "score1": s1, "score2": s2,
            "sub_score1": sub1, "sub_score2": sub2,
            "set_markets": {set_key: markets},
        }

    def _parse_markets(self, markets_raw: list, event: dict, sport_key: str) -> Dict:
        """markets_raw — список маркетов для партии."""
        result: Dict[str, Any] = {}
        teams = event.get("teams") or {}
        t1l = ((teams.get("team1") or {}).get("name") or "").lower()
        t2l = ((teams.get("team2") or {}).get("name") or "").lower()

        for market in markets_raw:
            if not isinstance(market, dict):
                continue
            if market.get("status") != "active":
                continue
            name = (market.get("name") or "").strip().lower()
            outcomes = market.get("outcomes") or []
            if not outcomes:
                continue

            # Классификация по имени (порядок критичен)
            if "победитель" in name or "исход" in name:
                self._parse_winner(outcomes, result)

            elif "чет/нечет" in name or "чёт/нечёт" in name:
                self._parse_odd_even(outcomes, result)

            elif "фора" in name:
                self._parse_handicap(outcomes, result)

            elif "гонка" in name or "разниц" in name:
                continue

            elif "тотал" in name:
                if t1l and t1l in name:
                    self._parse_it(outcomes, result, "1")
                elif t2l and t2l in name:
                    self._parse_it(outcomes, result, "2")
                else:
                    self._parse_total(outcomes, result)

            elif "очко" in name:
                # для fast БК следующее очко не критично, можно пропустить
                continue

        return result

    @staticmethod
    def _parse_winner(outcomes: list, result: dict):
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            if oname == "Поб 1":
                result.setdefault("winner", {})["1"] = odd
            elif oname == "Поб 2":
                result.setdefault("winner", {})["2"] = odd

    @staticmethod
    def _parse_odd_even(outcomes: list, result: dict):
        om = result.setdefault("odd", {})
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            if oname in ("Чет", "Чёт"):
                om["even"] = odd
            elif oname in ("Нечет", "Нечёт"):
                om["odd"] = odd

    @staticmethod
    def _extract_line_from_spec(out: dict) -> float:
        spec = out.get("specifiers") or ""
        if isinstance(spec, str) and "total=" in spec:
            try:
                val = spec.split("total=", 1)[1]
                val = re.split(r"[|&;]", val, 1)[0]
                return float(val)
            except (ValueError, IndexError):
                pass
        text = out.get("fullName") or out.get("name") or ""
        m = re.search(r"(\d+\.?\d*)", text)
        return float(m.group(1)) if m else 0.0

    @classmethod
    def _collect_lines(cls, outcomes: list) -> dict:
        lines: Dict[float, dict] = {}
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            ofull = out.get("fullName") or ""
            line = cls._extract_line_from_spec(out)
            if not line:
                continue
            is_over = oname.startswith("ТБ") or "Больше" in ofull
            is_under = oname.startswith("ТМ") or "Меньше" in ofull
            if not (is_over or is_under):
                continue
            lines.setdefault(line, {})
            lines[line]["over" if is_over else "under"] = out
        return lines

    @classmethod
    def _parse_total(cls, outcomes: list, result: dict):
        t = result.setdefault("total", {})
        if "line" in t:
            return
        for line, pair in sorted(cls._collect_lines(outcomes).items()):
            if "over" in pair and "under" in pair:
                t["line"] = line
                t["over"] = pair["over"].get("odd")
                t["under"] = pair["under"].get("odd")
                return

    @classmethod
    def _parse_it(cls, outcomes: list, result: dict, player: str):
        it = result.setdefault("it", {}).setdefault(player, {})
        if "line" in it:
            return
        for line, pair in sorted(cls._collect_lines(outcomes).items()):
            if "over" in pair and "under" in pair:
                it["line"] = line
                it["over"] = pair["over"].get("odd")
                it["under"] = pair["under"].get("odd")
                return

    @staticmethod
    def _parse_handicap(outcomes: list, result: dict):
        hk = result.setdefault("handicap", {})
        if hk:
            return
        cand: Dict[float, dict] = {}
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            m = re.search(r"\(([+-]?\d+\.?\d*)\)", oname)
            if not m:
                continue
            line = float(m.group(1))
            if "Фора 1" in oname:
                cand.setdefault(abs(line), {})["1"] = (out, line)
            elif "Фора 2" in oname:
                cand.setdefault(abs(line), {})["2"] = (out, line)
        for key in sorted(cand.keys()):
            pair = cand[key]
            if "1" in pair and "2" in pair:
                out1, line1 = pair["1"]
                out2, line2 = pair["2"]
                hk["1"] = {"line": line1, "odd": out1.get("odd")}
                hk["2"] = {"line": line2, "odd": out2.get("odd")}
                return

# ════════════════════════════════════════════════════════════
# Marathon (SSE через httpx)
# ════════════════════════════════════════════════════════════



# ════════════════════════════════════════════════════════════
# Marathon (SSE конкретного матча)
# ════════════════════════════════════════════════════════════
class MarathonFastClient(FastOddsClient):
    """
    Marathon:
      1. JSON-снапшот all-tournaments → находим матч, забираем SEO-слаги.
      2. SSE на events/by-slug конкретного матча → real-time.
    """
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

    # ── start / stop ──
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

    # ── Шаг 1: JSON-снапшот для поиска матча ──
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
            logger.warning(
                f"[marathon_fast] snapshot {sport} HTTP {r.status_code}"
            )
            return None
        try:
            data = r.json()
        except Exception:
            return None
        self._snapshot_cache[sport] = (data, now)
        return data

    @staticmethod
    def _find_match_in_snapshot(snapshot: dict, p1: str, p2: str) -> Optional[dict]:
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

    # ── Шаг 2: SSE на конкретный матч ──
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
        logger.info(f"[marathon_fast] SSE start {tree_id}: {url}")

    async def _sse_loop(self, tree_id: str, url: str):
        retry = 1
        while True:
            try:
                async with self._client.stream(
                    "GET", url, headers=self.HEADERS
                ) as response:
                    if response.status_code != 200:
                        logger.warning(
                            f"[marathon_fast] SSE {tree_id} "
                            f"HTTP {response.status_code}"
                        )
                        await asyncio.sleep(retry)
                        retry = min(retry * 2, 30)
                        continue

                    logger.info(f"[marathon_fast] SSE {tree_id} connected")
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
                            except Exception as e:
                                logger.debug(
                                    f"[marathon_fast] part: {e}"
                                )
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
                except Exception as e:
                    logger.debug(f"[marathon_fast] change: {e}")

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
                    if cache:
                        self._apply_match_score(cache, value)
                return

        if (isinstance(value, dict)
                and "selId" in value
                and "coeff" in value):
            cache = self._events_cache.get(expected_tree_id)
            if not cache:
                return
            sel_id = value.get("selId")
            new_coeff = value.get("coeff")
            for m in (cache.get("markets") or {}).values():
                sel = (m.get("selections") or {}).get(sel_id)
                if sel is not None:
                    sel["coeff"] = new_coeff
                    break

    def _upsert_event(self, event: dict):
        tid = str(event.get("treeId", ""))
        if not tid:
            return

        ht = (event.get("homeTeam") or {}).get("members") or []
        at = (event.get("awayTeam") or {}).get("members") or []
        home = ht[0].get("name", "") if ht else ""
        away = at[0].get("name", "") if at else ""

        phase = event.get("phase") or {}
        try:
            phase_num = int(phase.get("partNumber") or 1)
        except Exception:
            phase_num = 1

        ms = event.get("matchScore") or {}
        main = ms.get("main") or {}
        try:
            s1 = int(main.get("home", 0) or 0)
            s2 = int(main.get("away", 0) or 0)
        except Exception:
            s1 = s2 = 0

        sub1 = sub2 = 0
        parts = ms.get("parts") or []
        if parts:
            idx = min(max(phase_num - 1, 0), len(parts) - 1)
            p = parts[idx] or {}
            try:
                sub1 = int(p.get("home", 0) or 0)
                sub2 = int(p.get("away", 0) or 0)
            except Exception:
                pass

        cache = self._events_cache.setdefault(tid, {})
        cache.update({
            "treeId": tid,
            "homeTeam": home,
            "awayTeam": away,
            "phase_num": phase_num,
            "score1": s1, "score2": s2,
            "sub1": sub1, "sub2": sub2,
            "markets": event.get("markets") or {},
            "updated_at": time.time(),
        })

    @staticmethod
    def _apply_match_score(cache: dict, ms: dict):
        main = ms.get("main") or {}
        try:
            cache["score1"] = int(main.get("home", 0) or 0)
            cache["score2"] = int(main.get("away", 0) or 0)
        except Exception:
            pass
        parts = ms.get("parts") or []
        if parts:
            phase_num = cache.get("phase_num", 1)
            idx = min(max(phase_num - 1, 0), len(parts) - 1)
            p = parts[idx] or {}
            try:
                cache["sub1"] = int(p.get("home", 0) or 0)
                cache["sub2"] = int(p.get("away", 0) or 0)
            except Exception:
                pass

    # ── Публичный метод ──
    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        await self.start()

        snapshot = await self._fetch_snapshot(sport)
        if not snapshot:
            return None

        event = self._find_match_in_snapshot(snapshot, p1, p2)
        if not event:
            return None

        tree_id = str(event.get("treeId", ""))
        if not tree_id:
            return None

        if tree_id not in self._slugs:
            slugs = self._extract_slugs(event)
            if not slugs:
                logger.warning(
                    f"[marathon_fast] {tree_id}: нет SEO-слагов в снапшоте"
                )
                return None
            self._slugs[tree_id] = slugs
            self._upsert_event(event)

        self._ensure_sse(tree_id, self._slugs[tree_id])

        for _ in range(10):
            if tree_id in self._last_sse_at:
                break
            await asyncio.sleep(0.5)

        cache = self._events_cache.get(tree_id)
        if not cache:
            return None

        try:
            return self._build_result(cache, p1, p2)
        except Exception as e:
            logger.warning(f"[marathon_fast] build: {e}", exc_info=True)
            return None

    def _build_result(self, cache: dict, p1: str, p2: str) -> Dict:
        phase_num = int(cache.get("phase_num", 1))
        markets_raw = cache.get("markets") or {}
        markets = self._parse_markets(
            markets_raw, phase_num,
            cache.get("homeTeam", ""),
            cache.get("awayTeam", ""),
        )
        set_key = f"set_{phase_num}"
        return {
            "match_id": cache.get("treeId"),
            "phase_num": phase_num,
            "score1": cache.get("score1", 0),
            "score2": cache.get("score2", 0),
            "sub_score1": cache.get("sub1", 0),
            "sub_score2": cache.get("sub2", 0),
            "set_markets": {set_key: markets},
        }

    @staticmethod
    def _side_for_winner(name: str, p1: str, p2: str) -> Optional[str]:
        n = _normalize_name(name)
        n1 = _normalize_name(p1)
        n2 = _normalize_name(p2)
        if not (n and n1 and n2):
            return None

        def close(a: str, b: str) -> bool:
            return a == b or (len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4])

        if close(n, n1):
            return "1"
        if close(n, n2):
            return "2"
        return None

    def _parse_markets(self, markets_raw: dict, phase_num: int,
                       home: str, away: str) -> Dict:
        result: Dict[str, Any] = {}
        num_re = re.compile(r"([+-]?\d+\.?\d*)")

        for m in markets_raw.values():
            if not isinstance(m, dict):
                continue
            if m.get("state") not in (None, "ACTIVE"):
                continue

            model = m.get("model", "") or ""
            selections = m.get("selections") or {}

            kind = None
            player = None
            m_set = None

            # ── Матчевые модели ──
            if model in ("MTCH_R", "MTCH_DNB"):
                kind = "winner"
            elif model == "MTCH_TTLP":
                kind = "total"
            elif model in ("MTCH_HB", "MTCH_HBP"):
                kind = "handicap"
            elif model == "MTCH_TTLPOE":
                kind = "odd"
            else:
                # ── Фазовые модели ──
                mm = re.match(r"^MTCH_R(\d+)$", model)
                if mm:
                    kind = "winner"; m_set = int(mm.group(1))
                elif (mm := re.match(r"^MTCH_TTLG(\d+)$", model)):
                    kind = "total"; m_set = int(mm.group(1))
                elif (mm := re.match(r"^MTCH_HB(\d+)$", model)):
                    kind = "handicap"; m_set = int(mm.group(1))
                elif (mm := re.match(r"^MTCH_T1TTLG(\d+)$", model)):
                    kind = "it"; player = "1"; m_set = int(mm.group(1))
                elif (mm := re.match(r"^MTCH_T2TTLG(\d+)$", model)):
                    kind = "it"; player = "2"; m_set = int(mm.group(1))
                elif (mm := re.match(r"^MTCH_TTLGOE(\d+)$", model)):
                    kind = "odd"; m_set = int(mm.group(1))
                else:
                    continue

                if m_set != phase_num:
                    continue

            for sel in selections.values():
                if not isinstance(sel, dict):
                    continue

                coeff = sel.get("coeff") or {}
                price = coeff.get("price") or {}
                n = price.get("n", 0)
                d = price.get("d", 1)
                if not d:
                    continue
                try:
                    odd = (float(n) + float(d)) / float(d)
                except Exception:
                    continue
                if odd <= 1.0:
                    continue

                name = (sel.get("name") or "").strip()
                lower = name.lower()

                if kind == "winner":
                    side = self._side_for_winner(name, home, away)
                    if side:
                        result.setdefault("winner", {})[side] = odd

                elif kind == "total":
                    mline = num_re.search(name)
                    line = float(mline.group(1)) if mline else 0.0
                    if line <= 0:
                        continue
                    t = result.setdefault("total", {})
                    t.setdefault("line", line)
                    if "больше" in lower:
                        t["over"] = odd
                    elif "меньше" in lower:
                        t["under"] = odd

                elif kind == "handicap":
                    side = self._side_for_winner(name, home, away)
                    if not side:
                        continue
                    mline = num_re.search(name)
                    if not mline:
                        continue
                    line = float(mline.group(1))
                    h = result.setdefault("handicap", {}).setdefault(side, {})
                    h["line"] = line
                    h["odd"] = odd

                elif kind == "it" and player:
                    mline = num_re.search(name)
                    line = float(mline.group(1)) if mline else 0.0
                    if line <= 0:
                        continue
                    it = result.setdefault("it", {}).setdefault(player, {})
                    it.setdefault("line", line)
                    if "больше" in lower:
                        it["over"] = odd
                    elif "меньше" in lower:
                        it["under"] = odd

                elif kind == "odd":
                    om = result.setdefault("odd", {})
                    if "нечет" in lower or "нечёт" in lower:
                        om["odd"] = odd
                    elif "чет" in lower or "чёт" in lower:
                        om["even"] = odd

        return result


 # ════════════════════════════════════════════════════════════
# Winline (WebSocket + бинарный декодер)
# Портировано из ui/after_goal/winline.py + parsers/winline_api.py
# ════════════════════════════════════════════════════════════
_MARKET_TYPE_WINNER      = 51
_MARKET_TYPE_TOTAL       = 71
_MARKET_TYPE_HANDICAP    = 61
_MARKET_TYPE_WINNER_2WAY = 151

_MID_ODD_NT   = 934
_MID_POINT_NT = 952


def _parse_winline_coefficient(coeff: str) -> Tuple[Optional[int], Optional[float]]:
    """'1' → (1, None); '1/-2.5' → (1, -2.5); '1/1' → (1, 1.0)."""
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


def _winline_teams_match(t1_tokens: List[str], t2_tokens: List[str],
                          a: str, b: str) -> bool:
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

        # Ключ — кортеж (event_id, market_id, coeff), как во фронт-хендлере
        self._lines: Dict[Tuple[int, int, str], dict] = {}
        # event_id → {participants, score1, score2, sub_score1, sub_score2, phase_num, ...}
        self._live_events: Dict[int, dict] = {}

        self._cookie_header = ""

    # ── Жизненный цикл ──
    async def start(self):
        if self._ws_task and not self._ws_task.done():
            return

        from core.fast_config import load_cookies
        cookies = load_cookies("winline")

        # Оставляем ТОЛЬКО cookies доменов winline.ru.
        # Иначе из общего профиля тянутся чужие (Betcity, Leon, Marathon)
        # и WS-handshake отбивается HTTP 400 из-за раздутого Cookie.
        cookies = [
            c for c in cookies
            if "winline" in (c.get("domain") or "").lower()
        ]

        self._cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
            if c.get("name") and c.get("value")
        )
        logger.info(f"[winline_fast] 🍪 отобрано {len(cookies)} cookies")

        self._ws_task = asyncio.create_task(self._ws_loop())

        # Ждём первой пачки данных
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
        logger.info("[winline_fast] остановлен")

    async def _build_session(self):
        return None

    async def _fetch_raw(self):
        return None

    # ── WS-цикл (по образцу parsers/winline_api.py) ──
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
                    compression=None,       # ← отключаем permessage-deflate
                ) as ws:
                    self._ws = ws
                    logger.info("[winline_fast] ✅ WS подключён")

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

    # ── Обработка кадра ──
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
        """
        Один-в-один по ui/after_goal/winline.py::_ingest_live.
        """
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

    # ── Публичный API ──
    async def get_markets(self, sport: str, p1: str, p2: str) -> Optional[Dict]:
        await self.start()

        if not self._live_events:
            return None

        # Ожидаемый sport_id
        expected_sid = None
        for sid, name in self.SPORT_IDS.items():
            if name == sport:
                expected_sid = sid
                break

        # Отфильтруем события по спорту
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
            # Показать первые 5 событий ИМЕННО этого спорта
            sample = [
                (data.get("participants") or [])[:2]
                for _, data in sport_events[:5]
            ]
            logger.warning(
                f"[winline_fast] матч '{p1}' / '{p2}' не найден. "
                f"всего={len(self._live_events)} "
                f"спорт={sport}({expected_sid})={len(sport_events)} "
                f"lines={len(self._lines)} "
                f"menu={len(self._decoder.markets)}"
            )
            if sample:
                logger.warning(f"[winline_fast] этого спорта: {sample}")
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
        """
        Один-в-один по ui/after_goal/winline.py::_build_snapshot.
        Возвращает set_markets (без outcome_ids).
        """
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
    """Возвращает (и кэширует) клиент для указанной БК. None — если не реализован."""
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
    """Закрыть все сессии при выходе приложения."""
    for client in list(_CLIENTS.values()):
        try:
            await client.stop()
        except Exception:
            pass
    _CLIENTS.clear()