"""
Sportbet handler — instance-based.

Архитектура парсинга (после рефакторинга):
  1. События приходят через WebSocket (bthm-server.sportbet.ru) или HTTP
     events.table. В событии ЕСТЬ счёт и фаза (matchStatus), но НЕТ
     фазовых маркетов (pages всегда пустой).
  2. Фазовые маркеты запрашиваются отдельно через:
        GET /events.markets?eventId={id}&page={ключ}
     где ключ строится из вида спорта и номера фазы:
        table_tennis / volleyball  →  set_{N}
        basketball                 →  quarter_{N}
  3. Маркеты классифицируются ПО ИМЕНИ (groupName в ответе отсутствует):
        "...Победитель" / "...Исход"      → winner
        "...Чет/Нечет"                    → odd
        "...Фора очков" / "...Фора"       → handicap
        "...Тотал очков" / "...Тотал"     → total
        "...{Игрок} Тотал"                → it (инд. тотал)
        "...N-е очко" / "...N очко"       → point
        "...Гонка до..." / "...Разница..." → пропускаем

Особенности по видам:
  - Баскетбол: победитель называется "Исход" (есть "Ничья" — игнорируем).
  - Волейбол: индивидуальные тоталы ОТСУТСТВУЮТ.
  - Очки: у НТ/баскета номер в исходе, у волейбола — в имени маркета.
"""
import asyncio
import json
import logging
import re
import time
import uuid as _uuid
from typing import Optional, Dict, Any, List, Callable

from playwright.async_api import Page, WebSocket, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

_WS_SILENCE_THRESHOLD = 8.0
_POLL_INTERVAL = 3.0
_MARKETS_FETCH_INTERVAL = 1.0


class SportbetHandler(BookmakerHandler):

    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id = str(target_match_id) if target_match_id else None
        self.target_teams = list(target_teams or [])
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._last_ws_frame_time: float = 0.0
        self._last_sent_state: Optional[tuple] = None
        self._last_fetch_time: Dict[str, float] = {}
        self._last_phase: Dict[str, int] = {}

    # ============================================================
    # Подключение / отключение
    # ============================================================
    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        self._page = page
        self._callback = callback
        self._last_ws_frame_time = time.time()
        self._last_sent_state = None
        self._last_fetch_time = {}
        self._last_phase = {}

        page.on("websocket", self._on_websocket)
        page.on("response", self._on_response)
        logger.info(f"Sportbet: listener установлен (target={self.target_match_id})")

        asyncio.create_task(self._fetch_snapshot_and_emit(page))
        self._poll_task = asyncio.create_task(self._poll_loop(page))

    async def stop_listener(self, page: Page):
        try:
            page.remove_listener("websocket", self._on_websocket)
            page.remove_listener("response", self._on_response)
        except Exception:
            pass
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        self._callback = None
        self.target_match_id = None
        self._last_sent_state = None
        logger.info("Sportbet: listener снят")

    # ============================================================
    # WebSocket + HTTP
    # ============================================================
    def _on_websocket(self, ws: WebSocket):
        if "bthm-server.sportbet.ru" in ws.url:
            ws.on("framereceived", self._on_ws_frame)
            logger.info(f"Sportbet: WS подключён {ws.url}")

    def _on_ws_frame(self, payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            self._last_ws_frame_time = time.time()
            start = payload.find("[")
            if start == -1:
                return
            data = json.loads(payload[start:])
            if not (isinstance(data, list) and len(data) == 2):
                return
            event_type = data[0]
            if event_type not in ("events:update", "table:update"):
                return
            self._handle_payload(data[1])
        except Exception as e:
            logger.debug(f"Sportbet WS parse error: {e}")

    async def _on_response(self, response: Response):
        url = response.url

        # ── Баланс Sportbet ──
        if 'nhm-account.sportbet.ru/accounts/me' in url:
            try:
                data = await response.json()
                bal = (((data or {}).get('data') or {})
                       .get('account') or {}).get('balance')
                if bal is not None:
                    from ui.balance_bus import balance_bus
                    balance_bus.update('sportbet', bal, 'RUB')
            except Exception:
                pass

        if "events.table" not in url:
            return
        try:
            data = await response.json()
            self._handle_payload(data)
        except Exception as e:
            logger.debug(f"Sportbet HTTP parse: {e}")

    # ============================================================
    # Снапшот и поллинг (запасной канал при тишине WS)
    # ============================================================
    async def _fetch_snapshot_and_emit(self, page: Page):
        try:
            await asyncio.sleep(0.5)
            data = await self._fetch_events_table(page)
            if data:
                self._handle_payload(data)
        except Exception as e:
            logger.debug(f"Sportbet initial snapshot: {e}")

    async def _fetch_events_table(self, page: Page) -> Optional[dict]:
        url = ("https://bthm-server.sportbet.ru/events.table"
               "?status=live&lang=ru&isTime=true")
        try:
            resp = await page.request.get(url, timeout=8000)
            if resp.status != 200:
                return None
            return await resp.json()
        except Exception as e:
            logger.debug(f"Sportbet events.table: {e}")
            return None

    async def _fetch_page_markets(self, page: Page, event_id: str,
                                  page_key: str) -> List[dict]:
        if not page or page.is_closed():
            return []
        url = (f"https://bthm-server.sportbet.ru/events.markets"
               f"?eventId={event_id}&page={page_key}&lang=ru")
        try:
            resp = await page.request.get(url, timeout=5000)
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, dict):
                return []
            markets = data.get("data")
            if not isinstance(markets, list):
                return []
            return markets
        except Exception as e:
            logger.debug(f"Sportbet markets {page_key}: {e}")
            return []

    async def _poll_loop(self, page: Page):
        while True:
            try:
                await asyncio.sleep(_POLL_INTERVAL)
                silence = time.time() - self._last_ws_frame_time
                if silence < _WS_SILENCE_THRESHOLD:
                    continue
                data = await self._fetch_events_table(page)
                if data:
                    self._handle_payload(data)
                    self._last_ws_frame_time = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Sportbet poll: {e}")

    # ============================================================
    # Обработка событий
    # ============================================================
    def _handle_payload(self, raw: Any):
        events = self._extract_events(raw)
        if not events:
            return
        target_id = self.target_match_id
        for event in events:
            ev_id = event.get("id")
            if not ev_id:
                continue
            if target_id and str(ev_id) != target_id:
                continue
            asyncio.create_task(self._process_event(event))

    async def _process_event(self, event: dict):
        match_id = str(event.get("id") or "")
        if not match_id:
            return

        # ── Спорт ──
        sport_slug = SportbetHandler._resolve_sport_slug(event)

        # ── Фаза из matchStatus ──
        match_status = (event.get("matchStatus") or "").strip()
        mm = re.search(r"(\d+)", match_status)
        phase_num = int(mm.group(1)) if mm else 0
        if not phase_num:
            phase_num = 1

        # ── Счёт ──
        score1, score2, sub1, sub2 = SportbetHandler._parse_scores(event)

        # ── Нужно ли запрашивать маркеты ──
        now = time.time()
        last = self._last_fetch_time.get(match_id, 0.0)
        last_phase = self._last_phase.get(match_id, 0)
        phase_changed = (phase_num != last_phase)
        need_fetch = phase_changed or (now - last >= _MARKETS_FETCH_INTERVAL)

        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        if need_fetch and self._page:
            page_key = SportbetHandler._build_page_key(sport_slug, phase_num)
            markets = await self._fetch_page_markets(
                self._page, match_id, page_key
            )
            self._last_fetch_time[match_id] = now
            self._last_phase[match_id] = phase_num

            if markets:
                teams = event.get("teams") or {}
                t1 = ((teams.get("team1") or {}).get("name") or "").strip()
                t2 = ((teams.get("team2") or {}).get("name") or "").strip()
                set_key = f"set_{phase_num}"
                SportbetHandler._parse_phase_markets(
                    markets, set_key, t1, t2, set_markets, outcome_ids
                )
                # ближайшее доступное очко → 'point' (аналог 2393/2394)
                SportbetHandler._pick_next_point(
                    set_key, sub1, sub2, set_markets, outcome_ids
                )

        parsed = {
            "match_id":    match_id,
            "sport":       sport_slug,
            "phase_num":   phase_num,
            "phase_name":  match_status,
            "score1":      score1,
            "score2":      score2,
            "sub_score1":  sub1,
            "sub_score2":  sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

        state = self._state_hash(parsed)
        if state == self._last_sent_state:
            return
        self._last_sent_state = state

        if self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Sportbet callback: {e}")

    # ============================================================
    # Извлечение списка событий из сырых данных
    # ============================================================
    @staticmethod
    def _extract_events(raw: Any) -> List[dict]:
        if not isinstance(raw, dict):
            return []
        if "events" in raw and isinstance(raw["events"], list):
            return raw["events"]
        data = raw.get("data")
        if isinstance(data, dict):
            sports = data.get("sports") or []
            out: List[dict] = []
            for sport in sports:
                sport_meta = {
                    "id": sport.get("id"),
                    "slug": sport.get("slug"),
                    "name": sport.get("name"),
                }
                for t in sport.get("tournaments") or []:
                    for e in t.get("events") or []:
                        if "sport" not in e:
                            e["sport"] = sport_meta
                        out.append(e)
            return out
        if "id" in raw and "teams" in raw:
            return [raw]
        return []

    # ============================================================
    # Вспомогательные: спорт / страница / счёт
    # ============================================================
    @staticmethod
    def _resolve_sport_slug(event: dict) -> str:
        sport_raw = event.get("sport")
        if isinstance(sport_raw, dict):
            slug = (sport_raw.get("slug") or "").strip().replace("-", "_")
            if slug:
                return slug
            sid = sport_raw.get("id")
            return {20: "table_tennis", 23: "volleyball",
                    2: "basketball"}.get(sid, "")
        if isinstance(sport_raw, str):
            return sport_raw.strip().replace("-", "_")
        return ""

    @staticmethod
    def _build_page_key(sport_slug: str, phase_num: int) -> str:
        if sport_slug in ("basketball", "cyber_basketball"):
            return f"quarter_{phase_num}"
        if sport_slug == "hockey":
            return f"period_{phase_num}"
        # table_tennis / volleyball / beach_volleyball / tennis → set
        return f"set_{phase_num}"

    @staticmethod
    def _parse_scores(event: dict):
        score_str = event.get("score", "0:0") or "0:0"
        try:
            score1, score2 = map(int, score_str.split(":"))
        except Exception:
            score1 = score2 = 0
        sub1 = sub2 = 0
        scores_str = event.get("scores", "") or ""
        parts = [p.strip() for p in scores_str.split() if p.strip()]
        if parts and ":" in parts[-1]:
            try:
                sub1, sub2 = map(int, parts[-1].split(":"))
            except Exception:
                pass
        return score1, score2, sub1, sub2

    @staticmethod
    def _extract_total_line(out: dict) -> float:
        # 1) из specifiers: "total=17.5" или "total=8.5|gamenr=3"
        spec = out.get("specifiers") or ""
        if isinstance(spec, str) and "total=" in spec:
            try:
                val = spec.split("total=", 1)[1]
                val = re.split(r"[|&;]", val, 1)[0]
                return float(val)
            except (ValueError, IndexError):
                pass
        # 2) из имени исхода
        text = out.get("fullName") or out.get("name") or ""
        m = re.search(r"(\d+\.?\d*)", text)
        return float(m.group(1)) if m else 0.0

    # ============================================================
    # Классификация и парсинг маркетов (по имени)
    # ============================================================
    @staticmethod
    def _parse_phase_markets(markets: list, set_key: str,
                             team1: str, team2: str,
                             set_markets: dict, outcome_ids: dict):
        t1l = (team1 or "").lower()
        t2l = (team2 or "").lower()

        for market in markets:
            if market.get("status") != "active":
                continue
            name = (market.get("name") or "").strip()
            nl = name.lower()
            outcomes = market.get("outcomes") or []
            if not outcomes:
                continue

            # ── ПОРЯДОК КРИТИЧЕН ──
            # 1) Победитель: "Победитель" (НТ/волей) ИЛИ "Исход" (баскет)
            if "победитель" in nl or "исход" in nl:
                SportbetHandler._parse_winner(outcomes, set_key,
                                              set_markets, outcome_ids)
            # 2) Чёт/Нечёт
            elif "чет/нечет" in nl or "чёт/нечёт" in nl:
                SportbetHandler._parse_odd_even(outcomes, set_key,
                                                set_markets, outcome_ids)
            # 3) Фора
            elif "фора" in nl:
                SportbetHandler._parse_handicap(outcomes, set_key,
                                                set_markets, outcome_ids)
            # 4) Гонка/Разница — пропускаем (до проверок "очко"/"тотал")
            elif "гонка" in nl or "разниц" in nl:
                continue
            # 5) ТОТАЛ раньше "очко" ("Тотал очков" содержит "очко")
            elif "тотал" in nl:
                if t1l and t1l in nl:
                    SportbetHandler._parse_it(outcomes, set_key, "1",
                                              set_markets, outcome_ids)
                elif t2l and t2l in nl:
                    SportbetHandler._parse_it(outcomes, set_key, "2",
                                              set_markets, outcome_ids)
                else:
                    SportbetHandler._parse_total(outcomes, set_key,
                                                 set_markets, outcome_ids)
            # 6) Очко (гонка и тотал уже исключены)
            elif "очко" in nl:
                SportbetHandler._parse_point(outcomes, set_key, name,
                                             t1l, t2l,
                                             set_markets, outcome_ids)

    @staticmethod
    def _parse_winner(outcomes, set_key, set_markets, outcome_ids):
        w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            oid = out.get("id")
            # "Ничья" (баскетбол) игнорируется — только Поб1/Поб2
            if oname == "Поб 1":
                w["1"] = odd
                o["1"] = {"id": oid, "kf": odd}
            elif oname == "Поб 2":
                w["2"] = odd
                o["2"] = {"id": oid, "kf": odd}

    @staticmethod
    def _parse_odd_even(outcomes, set_key, set_markets, outcome_ids):
        om = set_markets.setdefault(set_key, {}).setdefault("odd", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("odd", {})
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            oid = out.get("id")
            if oname in ("Чет", "Чёт"):
                om["even"] = odd
                o["even"] = {"id": oid, "kf": odd}
            elif oname in ("Нечет", "Нечёт"):
                om["odd"] = odd
                o["odd"] = {"id": oid, "kf": odd}

    @staticmethod
    def _collect_lines(outcomes):
        """Группирует исходы по линии: {line: {'over': out, 'under': out}}"""
        lines = {}
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            ofull = out.get("fullName") or ""
            line = SportbetHandler._extract_total_line(out)
            if not line:
                continue
            is_over = oname.startswith("ТБ") or "Больше" in ofull
            is_under = oname.startswith("ТМ") or "Меньше" in ofull
            if not (is_over or is_under):
                continue
            lines.setdefault(line, {})
            lines[line]["over" if is_over else "under"] = out
        return lines

    @staticmethod
    def _parse_total(outcomes, set_key, set_markets, outcome_ids):
        t = set_markets.setdefault(set_key, {}).setdefault("total", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
        if "line" in t:
            return
        for line, pair in sorted(SportbetHandler._collect_lines(outcomes).items()):
            if "over" in pair and "under" in pair:
                t["line"] = line
                t["over"] = pair["over"].get("odd")
                t["under"] = pair["under"].get("odd")
                o["over"] = {"id": pair["over"].get("id"),
                             "kf": pair["over"].get("odd"), "line": line}
                o["under"] = {"id": pair["under"].get("id"),
                              "kf": pair["under"].get("odd"), "line": line}
                break

    @staticmethod
    def _parse_it(outcomes, set_key, player, set_markets, outcome_ids):
        it = (set_markets.setdefault(set_key, {})
              .setdefault("it", {}).setdefault(player, {}))
        o = (outcome_ids.setdefault(set_key, {})
             .setdefault("it", {}).setdefault(player, {}))
        if "line" in it:
            return
        for line, pair in sorted(SportbetHandler._collect_lines(outcomes).items()):
            if "over" in pair and "under" in pair:
                it["line"] = line
                it["over"] = pair["over"].get("odd")
                it["under"] = pair["under"].get("odd")
                o["over"] = {"id": pair["over"].get("id"),
                             "kf": pair["over"].get("odd"), "line": line}
                o["under"] = {"id": pair["under"].get("id"),
                              "kf": pair["under"].get("odd"), "line": line}
                break

    @staticmethod
    def _parse_handicap(outcomes, set_key, set_markets, outcome_ids):
        hk = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})
        if hk:
            return
        cand = {}  # abs(line) -> {'1': (out,line), '2': (out,line)}
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
                hk.setdefault("1", {})["line"] = line1
                hk["1"]["odd"] = out1.get("odd")
                hk.setdefault("2", {})["line"] = line2
                hk["2"]["odd"] = out2.get("odd")
                o.setdefault("1", {})["id"] = out1.get("id")
                o["1"]["kf"] = out1.get("odd")
                o["1"]["line"] = line1
                o.setdefault("2", {})["id"] = out2.get("id")
                o["2"]["kf"] = out2.get("odd")
                o["2"]["line"] = line2
                break

    @staticmethod
    def _parse_point(outcomes, set_key, market_name, t1l, t2l,
                     set_markets, outcome_ids):
        """
        Унифицированно под НТ/волейбол/баскетбол:
          НТ:       маркет "N-е очко",  исходы "11-е - Игрок"
          Волейбол: маркет "30-е очко", исходы "Команда"
          Баскет:   маркет "N очко",    исходы "Команда - 25 очко"
        """
        # номер может быть в имени маркета (волейбол: "30-е очко")
        market_n = None
        m = re.search(r"(\d+)\s*-?\s*е?\s*очк", market_name)
        if m:
            market_n = int(m.group(1))

        points = {}  # N -> {'1': out, '2': out}
        for out in outcomes:
            if out.get("active") is False:
                continue
            odd = out.get("odd")
            if not odd:
                continue
            oname = (out.get("name") or "").strip()
            ofull = out.get("fullName") or ""

            # номер: из исхода, иначе из имени маркета
            n = market_n
            mm = re.search(r"(\d+)\s*-?\s*е", oname)
            if mm:
                n = int(mm.group(1))
            else:
                mm = re.search(r"(\d+)\s*очк", oname)
                if mm:
                    n = int(mm.group(1))
            if n is None:
                continue

            # игрок: по имени команды в исходе
            onl = oname.lower()
            ofl = ofull.lower()
            player = None
            if t1l and (t1l in onl or t1l in ofl):
                player = "1"
            elif t2l and (t2l in onl or t2l in ofl):
                player = "2"
            if player is None:
                continue

            points.setdefault(n, {})[player] = out

        pm = set_markets.setdefault(set_key, {}).setdefault("points", {})
        om = outcome_ids.setdefault(set_key, {}).setdefault("points", {})
        for n, pair in points.items():
            if "1" in pair and "2" in pair:
                pm[str(n)] = {"1": pair["1"].get("odd"),
                              "2": pair["2"].get("odd")}
                om[str(n)] = {
                    "1": {"id": pair["1"].get("id"), "kf": pair["1"].get("odd")},
                    "2": {"id": pair["2"].get("id"), "kf": pair["2"].get("odd")},
                }

    @staticmethod
    def _pick_next_point(set_key, sub1, sub2, set_markets, outcome_ids):
        """Заполняет 'point' ближайшим доступным очком (аналог 2393/2394)."""
        pm = set_markets.get(set_key, {}).get("points") or {}
        om = outcome_ids.get(set_key, {}).get("points") or {}
        if not pm:
            return
        target = sub1 + sub2 + 1
        nums = sorted(int(k) for k in pm.keys())
        chosen = None
        if str(target) in pm:
            chosen = target
        else:
            for n in nums:
                if n >= target:
                    chosen = n
                    break
            if chosen is None and nums:
                chosen = nums[0]
        if chosen is None:
            return
        ck = str(chosen)
        set_markets[set_key]["point"] = pm[ck]
        outcome_ids[set_key]["point"] = om[ck]

    # ============================================================
    # Хэш состояния (для дедупликации колбэков)
    # ============================================================
    @staticmethod
    def _state_hash(parsed: dict) -> tuple:
        sm = parsed.get("set_markets") or {}
        phase = parsed.get("phase_num")
        set_key = f"set_{phase}" if phase else None
        markets = sm.get(set_key, {}) if set_key else {}
        w = markets.get("winner") or {}
        t = markets.get("total") or {}
        hk = markets.get("handicap") or {}
        od = markets.get("odd") or {}
        it = markets.get("it") or {}
        pt = markets.get("point") or {}
        return (
            phase,
            parsed.get("score1"), parsed.get("score2"),
            parsed.get("sub_score1"), parsed.get("sub_score2"),
            w.get("1"), w.get("2"),
            t.get("line"), t.get("over"), t.get("under"),
            hk.get("1", {}).get("odd"), hk.get("2", {}).get("odd"),
            od.get("even"), od.get("odd"),
            it.get("1", {}).get("over"), it.get("2", {}).get("over"),
            pt.get("1"), pt.get("2"),
        )

    # ============================================================
    # Размещение ставки
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        idem_key = str(_uuid.uuid4())
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            const idempotencyKey = {json.dumps(idem_key)};
            try {{
                const resp = await fetch(
                    'https://bthm-server.sportbet.ru/pari.stake?lang=ru',
                    {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json, text/plain, */*',
                            'idempotency-key': idempotencyKey,
                            'Origin': 'https://sportbet.ru',
                            'Referer': 'https://sportbet.ru/'
                        }},
                        credentials: 'include',
                        body: JSON.stringify({{
                            outcomes: [data.outcome_id],
                            amount: data.amount
                        }})
                    }}
                );
                const result = await resp.json();
                if (result.status === 'ok'
                    && result.data
                    && result.data.success === true) {{
                    return {{ success: true, betId: result.uuid || null }};
                }}
                return {{ success: false, error: 'Sportbet: ' + JSON.stringify(result) }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)