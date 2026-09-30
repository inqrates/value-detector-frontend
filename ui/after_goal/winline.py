# ui/after_goal/winline.py
"""
Winline handler — instance-based через route_web_socket.

Ключевое:
  - Каждый матч = свой экземпляр WinlineHandler со своим WS-route
  - Линии и events кэшируются в self._lines / self._live_events
  - place_bet идёт через перехваченный WS (server.send)
"""
import asyncio
import base64
import logging
import re
import struct
import time
from typing import Optional, Dict, Any, List, Tuple, Callable

from playwright.async_api import Page

from .base import BookmakerHandler

try:
    from .decoder import DataNgDecoder
except ImportError:
    from after_goal.decoder import DataNgDecoder

logger = logging.getLogger(__name__)


MARKET_TYPE_WINNER   = 51
MARKET_TYPE_TOTAL    = 71
MARKET_TYPE_HANDICAP = 61
MARKET_TYPE_WINNER_2WAY = 151


def _parse_coefficient(coeff: str) -> Tuple[Optional[int], Optional[float]]:
    if not coeff:
        return None, None
    if '/' in coeff:
        parts = coeff.split('/')
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


def _build_bet_packet(id_line: str, kf: float, amount: float) -> str:
    buf = bytearray()
    buf += b'\x01\x01'
    buf += b'\x00\x01'
    buf += struct.pack('<d', amount)
    buf += b'\x00\x00'
    buf += b'\x01\x03'
    id_bytes = id_line.encode('ascii')
    buf += struct.pack('<H', len(id_bytes))
    buf += id_bytes
    buf += b'\x03\xa8'
    buf += b'\xfe\x00\x01'
    buf += struct.pack('<d', kf)
    buf += struct.pack('<d', amount)
    return base64.b64encode(bytes(buf)).decode('ascii')


class WinlineHandler(BookmakerHandler):

    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id: Optional[str] = (
            str(target_match_id) if target_match_id else None
        )
        self.target_teams: List[str] = list(target_teams or [])

        self._decoder = DataNgDecoder()
        self._server = None
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._target_event_id: Optional[int] = None

        self._lines: Dict[Tuple[int, int, str], dict] = {}
        self._live_events: Dict[int, dict] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._bet_ack: Optional[dict] = None

    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        self._page = page
        self._callback = callback
        self._target_event_id = None
        self._lines.clear()
        self._live_events.clear()
        self._bet_ack = None

        await page.route_web_socket("**/data_ng*", self._handle_ws)
        logger.info(f"Winline: route_web_socket установлен (target={self.target_match_id})")

        try:
            current_url = page.url
            await page.goto(current_url, wait_until="domcontentloaded", timeout=20000)
            logger.info("Winline: страница перезагружена, WS должен перехватиться")
        except Exception as e:
            logger.warning(f"Winline: ошибка перезагрузки: {e}")

        asyncio.create_task(self._resolve_target_event_loop())
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop_listener(self, page: Page):
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        self._callback = None
        self._server = None
        logger.info(f"Winline: listener снят (target={self.target_match_id})")

    async def _handle_ws(self, ws):
        logger.info(f"Winline: WS перехвачен {ws.url}")
        server = ws.connect_to_server()
        self._server = server

        def from_client(msg):
            try:
                server.send(msg)
            except Exception as e:
                logger.debug(f"Winline client→server: {e}")

        def from_server(msg):
            try:
                ws.send(msg)
                if isinstance(msg, (bytes, bytearray)):
                    self._process_frame(bytes(msg))
            except Exception as e:
                logger.debug(f"Winline server→client: {e}")

        ws.on_message(from_client)
        server.on_message(from_server)

    def _process_frame(self, data: bytes):
        # ── Баланс Winline (step=75) ──
        import gzip as _gzip
        raw = data
        if raw.startswith(b"\x1f\x8b"):
            try:
                raw = _gzip.decompress(raw)
            except Exception:
                pass

        if len(raw) >= 3 and int.from_bytes(raw[:2], "little") == 75:
            try:
                payload = raw[2:]
                bal_kop = int.from_bytes(payload, "little")
                bal_rub = bal_kop / 100.0
                from ui.balance_bus import balance_bus
                balance_bus.update('winline', bal_rub, 'RUB')
                logger.debug(f"Winline: balance={bal_rub}₽")
            except Exception as e:
                logger.debug(f"Winline balance parse: {e}")
            return

        # ── Существующая логика ──
        try:
            result = self._decoder.decode(data)
        except Exception:
            return

        items = result if isinstance(result, list) else ([result] if result else [])
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "live":
                self._ingest_live(item)

    def _ingest_live(self, item: dict):
        for ev in item.get("events", []) or []:
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

            # Очки текущей партии — из последнего куска setScores.
            # Формат: "11:8 - 9:11 - 7:5" → sub [7, 5].
            sub1 = sub2 = 0
            parts_list = [p.strip() for p in set_scores_raw.split(" - ")
                          if p.strip()] if set_scores_raw else []
            if parts_list:
                try:
                    a, b = parts_list[-1].split(":")
                    sub1, sub2 = int(a), int(b)
                except Exception:
                    pass

            # Счёт партий — из score "2:1".
            score1 = score2 = 0
            if score_raw and ":" in score_raw:
                try:
                    a, b = score_raw.split(":", 1)
                    score1, score2 = int(a), int(b)
                except Exception:
                    pass

            # phase_num = номер текущей партии/сета/четверти.
            # Приоритет — длина setScores (сколько партий идёт).
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

        for line in item.get("lines", []) or []:
            if not isinstance(line, dict):
                continue
            ev_id = line.get("eventId")
            market_id = line.get("marketId")
            coeff = line.get("coefficient", "") or ""
            if not ev_id or market_id is None:
                continue
            key = (int(ev_id), int(market_id), str(coeff))
            self._lines[key] = line

    async def _resolve_target_event_loop(self):
        for _ in range(15):
            await asyncio.sleep(1)
            if self._target_event_id:
                return
            if not self.target_teams or len(self.target_teams) < 2:
                continue

            p1, p2 = self.target_teams[0], self.target_teams[1]
            t1_tokens = _tokenize(p1)
            t2_tokens = _tokenize(p2)

            for ev_id, data in self._live_events.items():
                parts = data.get("participants") or []
                if len(parts) < 2:
                    continue
                if _teams_match(t1_tokens, t2_tokens, parts[0], parts[1]):
                    self._target_event_id = ev_id
                    logger.info(
                        f"Winline: наш матч найден — event_id={ev_id} "
                        f"({parts[0]} vs {parts[1]})"
                    )
                    return

        logger.warning(
            f"Winline: матч {self.target_teams} не найден за 15 сек"
        )

    async def _poll_loop(self):
        while True:
            try:
                await asyncio.sleep(0.5)
                if not self._callback:
                    continue
                if not self._target_event_id:
                    continue

                set_markets, outcome_ids = self._build_snapshot()
                if not set_markets:
                    continue

                ev_data = self._live_events.get(self._target_event_id) or {}
                sport_id = ev_data.get("sport_id")
                # Маппинг Winline sportId → sport_key
                sport_key = {
                    20:  "table_tennis",
                    23:  "volleyball",
                    2:   "basketball",
                    193: "cyber_basketball",
                    153: "cyber_basketball",
                }.get(sport_id, "table_tennis")

                await self._callback({
                    "match_id":   str(self._target_event_id),
                    "sport":      sport_key,
                    "phase_num":  ev_data.get("phase_num", 0),
                    "score1":     ev_data.get("score1", 0),
                    "score2":     ev_data.get("score2", 0),
                    "sub_score1": ev_data.get("sub_score1", 0),
                    "sub_score2": ev_data.get("sub_score2", 0),
                    "set_markets": set_markets,
                    "outcome_ids": outcome_ids,
                })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Winline poll: {e}")

    def _build_snapshot(self):
        if not self._target_event_id:
            return {}, {}

        event_id = self._target_event_id
        menu = self._decoder.markets

        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        for (ev_id, market_id, coeff), line in list(self._lines.items()):
            if ev_id != event_id:
                continue
            if market_id not in menu:
                continue
            mtype, _ = menu[market_id]

            phase, line_val = _parse_coefficient(coeff)
            if phase is None:
                continue

            set_key = f"set_{phase}"
            values = line.get("values") or []
            line_id = line.get("id")

            if mtype == MARKET_TYPE_WINNER and len(values) >= 3:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
                if "1" not in w:
                    w["1"] = values[0]; o["1"] = {"id": line_id, "kf": values[0]}
                if "X" not in w:
                    w["X"] = values[1]; o["X"] = {"id": line_id, "kf": values[1]}
                if "2" not in w:
                    w["2"] = values[2]; o["2"] = {"id": line_id, "kf": values[2]}

            elif mtype == MARKET_TYPE_WINNER_2WAY and len(values) >= 2:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
                if "1" not in w:
                    w["1"] = values[0]; o["1"] = {"id": line_id, "kf": values[0]}
                if "2" not in w:
                    w["2"] = values[1]; o["2"] = {"id": line_id, "kf": values[1]}

            elif mtype == MARKET_TYPE_TOTAL and len(values) >= 2 and line_val is not None:
                t = set_markets.setdefault(set_key, {}).setdefault("total", {})
                if "line" not in t:
                    t["line"] = line_val
                    t["over"] = values[0]
                    t["under"] = values[1]
                    o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
                    o["over"] = {"id": line_id, "kf": values[0], "line": line_val}
                    o["under"] = {"id": line_id, "kf": values[1], "line": line_val}

            elif mtype == MARKET_TYPE_HANDICAP and len(values) >= 2 and line_val is not None:
                h = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
                if "1" not in h:
                    h["1"] = {"line": line_val, "odd": values[0]}
                    h["2"] = {"line": -line_val, "odd": values[1]}
                    o = outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})
                    o["1"] = {"id": line_id, "kf": values[0], "line": line_val}
                    o["2"] = {"id": line_id, "kf": values[1], "line": -line_val}

        return set_markets, outcome_ids

    async def place_bet(self, page: Page, bet_data: dict) -> dict:
        server = self._server
        if server is None:
            return {"success": False, "error": "Winline: WS не перехвачен"}

        id_line = str(bet_data.get("outcome_id") or "")
        if not id_line:
            return {"success": False, "error": "Winline: нет idLine"}
        kf = float(bet_data.get("value") or 0)
        amount = float(bet_data.get("amount") or 0)

        if kf <= 1.01 or amount <= 0:
            return {"success": False, "error": f"Winline: невалидные kf={kf} amount={amount}"}

        packet = _build_bet_packet(id_line, kf, amount)
        self._bet_ack = None

        try:
            server.send("bet_ng")
            server.send(packet)
            logger.info(f"Winline: bet отправлен idLine={id_line} kf={kf} amount={amount}")
        except Exception as e:
            return {"success": False, "error": f"Winline send: {e}"}

        for _ in range(25):
            await asyncio.sleep(0.2)
            if self._bet_ack:
                return self._bet_ack

        try:
            coupon = await page.evaluate(
                "() => JSON.parse(localStorage.getItem('desktop-appsavedCoupon') || '[]')"
            )
            if not coupon:
                return {"success": True, "betId": None}
        except Exception:
            pass

        return {"success": False, "error": "Winline: нет ответа от сервера"}

    def _set_ack(self, success: bool, bet_id=None, error: str = None):
        self._bet_ack = {
            "success": success,
            "betId": bet_id,
            "error": error,
        }


def _tokenize(name: str) -> List[str]:
    if not name:
        return []
    tokens = re.split(r"\W+", name.lower())
    return [t for t in tokens if len(t) >= 3]


def _teams_match(t1_tokens: List[str], t2_tokens: List[str],
                 a: str, b: str) -> bool:
    a_tokens = _tokenize(a)
    b_tokens = _tokenize(b)

    def hit(tokens_src: List[str], tokens_dst: List[str]) -> bool:
        return any(t in tokens_dst for t in tokens_src)

    direct = hit(t1_tokens, a_tokens) and hit(t2_tokens, b_tokens)
    cross = hit(t1_tokens, b_tokens) and hit(t2_tokens, a_tokens)
    return direct or cross