# ui/after_goal/winline.py
"""
Winline handler через перехват родного WS (route_web_socket).

Схема:
  1. При preopen: Playwright.route_web_socket("**/data_ng*") → F5 → WS перехвачен.
  2. Все кадры от сервера декодируются через DataNgDecoder (уже готов).
  3. Кэшируем линии: (event_id, market_id, coefficient) → {id, values, ...}.
  4. Находим наш матч по participants в decoder.live_events.
  5. На ставке: находим idLine + kf в кэше → отправляем bet-пакет через
     перехваченное WS (server.send). Сессия не рвётся, задержка <50 мс.

Рынки (из menu-кадра, type):
  51  → 1X2 фазы (3 значения: П1, Х, П2)
  71  → Тотал фазы (2 значения: Больше, Меньше)
  61  → Фора фазы (2 значения: Ф1, Ф2)

Coefficient:
  "3"      → фаза 3 (для 1X2)
  "3/42.5" → фаза 3, линия 42.5 (для тоталов/фор)
  "174.5"  → матчевый (без фазы)

Ставка (проверено живой ставкой 13.09):
  server.send("bet_ng")
  server.send(<base64 bet-пакет>)
"""
import asyncio
import base64
import logging
import re
import struct
import time
from typing import Optional, Dict, Any, List, Tuple

from playwright.async_api import Page

from .base import BookmakerHandler

try:
    from .decoder import DataNgDecoder
except ImportError:
    from after_goal.decoder import DataNgDecoder

logger = logging.getLogger(__name__)


# market_type (из menu) → категория
MARKET_TYPE_WINNER   = 51     # 1X2 (3 значения)
MARKET_TYPE_TOTAL    = 71     # Тотал фазы (2)
MARKET_TYPE_HANDICAP = 61     # Фора фазы (2)
MARKET_TYPE_WINNER_2WAY = 151    # НТ/волей: П1/П2 партии (2 значения)


def _parse_coefficient(coeff: str) -> Tuple[Optional[int], Optional[float]]:
    """
    "3"       → (3, None)
    "3/42.5"  → (3, 42.5)
    "174.5"   → (None, 174.5)
    ""        → (None, None)
    """
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
    """
    Собирает bet-пакет (проверено живой ставкой 13.09.2026).
    Возвращает base64-строку.
    """
    buf = bytearray()
    buf += b'\x01\x01'                              # тип 257
    buf += b'\x00\x01'                              # флаг
    buf += struct.pack('<d', amount)                # сумма
    buf += b'\x00\x00'                              # флаг
    buf += b'\x01\x03'                              # флаг
    id_bytes = id_line.encode('ascii')
    buf += struct.pack('<H', len(id_bytes))         # длина idLine
    buf += id_bytes                                  # idLine
    buf += b'\x03\xa8'                              # флаг
    buf += b'\xfe\x00\x01'                          # флаг
    buf += struct.pack('<d', kf)                    # кэф
    buf += struct.pack('<d', amount)                # сумма ещё раз
    return base64.b64encode(bytes(buf)).decode('ascii')


class WinlineHandler(BookmakerHandler):

    _decoder = DataNgDecoder()
    _server = None                                  # ServerWebSocketRoute
    _page: Optional[Page] = None
    _callback = None
    _target_event_id: Optional[int] = None
    _target_teams: List[str] = []

    # (event_id, market_id, coefficient) → line dict
    _lines: Dict[Tuple[int, int, str], dict] = {}
    # event_id → {participants, timestamp}
    _live_events: Dict[int, dict] = {}

    # Управление callback в engine
    _poll_task: Optional[asyncio.Task] = None
    _last_callback_at: float = 0.0

    # Ack от сервера после ставки
    _bet_ack: Optional[dict] = None

    # ============================================================
    # setup / stop
    # ============================================================
    @staticmethod
    async def setup_listener(page: Page, callback, match_id=None, match_teams=None):
        WinlineHandler._page = page
        WinlineHandler._callback = callback
        WinlineHandler._target_teams = list(match_teams or [])
        WinlineHandler._target_event_id = None
        WinlineHandler._lines.clear()
        WinlineHandler._live_events.clear()
        WinlineHandler._bet_ack = None

        # Устанавливаем перехват WS — обязательно ДО навигации
        await page.route_web_socket("**/data_ng*", WinlineHandler._handle_ws)
        logger.info("Winline: route_web_socket установлен")

        # Перезагружаем страницу, чтобы WS пересоздался и попал под перехват
        try:
            current_url = page.url
            await page.goto(current_url, wait_until="domcontentloaded", timeout=20000)
            logger.info(f"Winline: страница перезагружена, WS должен перехватиться")
        except Exception as e:
            logger.warning(f"Winline: ошибка перезагрузки: {e}")

        # Ждём, пока декодер накопит live_events (3 сек), затем ищем наш матч
        asyncio.create_task(WinlineHandler._resolve_target_event_loop())

        # Запускаем poll-loop, который дёргает callback в engine
        WinlineHandler._poll_task = asyncio.create_task(
            WinlineHandler._poll_loop()
        )

    @staticmethod
    async def stop_listener(page: Page):
        if WinlineHandler._poll_task:
            WinlineHandler._poll_task.cancel()
            WinlineHandler._poll_task = None
        WinlineHandler._callback = None
        WinlineHandler._server = None
        logger.info("Winline: перехват остановлен")

    # ============================================================
    # Перехват WS
    # ============================================================
    @staticmethod
    async def _handle_ws(ws):
        logger.info(f"Winline: WS перехвачен {ws.url}")
        server = ws.connect_to_server()
        WinlineHandler._server = server

        def from_client(msg):
            try:
                server.send(msg)
            except Exception as e:
                logger.debug(f"Winline client→server: {e}")

        def from_server(msg):
            try:
                ws.send(msg)
                if isinstance(msg, (bytes, bytearray)):
                    WinlineHandler._process_frame(bytes(msg))
            except Exception as e:
                logger.debug(f"Winline server→client: {e}")

        ws.on_message(from_client)
        server.on_message(from_server)

    @staticmethod
    def _process_frame(data: bytes):
        """Синхронная обработка кадра от сервера."""
        try:
            result = WinlineHandler._decoder.decode(data)
        except Exception:
            return

        items = result if isinstance(result, list) else ([result] if result else [])
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "live":
                WinlineHandler._ingest_live(item)

    @staticmethod
    def _ingest_live(item: dict):
        # 1) Live events (для поиска нашего матча)
        for ev in item.get("events", []) or []:
            if not isinstance(ev, dict):
                continue
            ev_id = ev.get("id")
            if not ev_id:
                continue
            parts = ev.get("participants") or []
            if parts:
                WinlineHandler._live_events[ev_id] = {
                    "participants": [str(p) for p in parts],
                    "ts": time.time(),
                }

        # 2) Lines (котировки)
        for line in item.get("lines", []) or []:
            if not isinstance(line, dict):
                continue
            ev_id = line.get("eventId")
            market_id = line.get("marketId")
            coeff = line.get("coefficient", "") or ""
            if not ev_id or market_id is None:
                continue
            key = (int(ev_id), int(market_id), str(coeff))
            WinlineHandler._lines[key] = line

    # ============================================================
    # Поиск нашего матча по teams
    # ============================================================
    @staticmethod
    async def _resolve_target_event_loop():
        """Раз в 1 сек проверяем, появился ли наш матч в live_events."""
        for _ in range(15):                         # максимум 15 секунд
            await asyncio.sleep(1)
            if WinlineHandler._target_event_id:
                return
            if not WinlineHandler._target_teams or len(WinlineHandler._target_teams) < 2:
                continue

            p1, p2 = WinlineHandler._target_teams[0], WinlineHandler._target_teams[1]
            t1_tokens = _tokenize(p1)
            t2_tokens = _tokenize(p2)

            for ev_id, data in WinlineHandler._live_events.items():
                parts = data.get("participants") or []
                if len(parts) < 2:
                    continue
                if _teams_match(t1_tokens, t2_tokens, parts[0], parts[1]):
                    WinlineHandler._target_event_id = ev_id
                    logger.info(
                        f"Winline: наш матч найден — event_id={ev_id} "
                        f"({parts[0]} vs {parts[1]})"
                    )
                    return

        logger.warning(
            f"Winline: матч {WinlineHandler._target_teams} не найден за 15 сек"
        )

    # ============================================================
    # Poll-loop: дергает callback в engine раз в 500 мс
    # ============================================================
    @staticmethod
    async def _poll_loop():
        while True:
            try:
                await asyncio.sleep(0.5)

                if not WinlineHandler._callback:
                    continue
                if not WinlineHandler._target_event_id:
                    continue

                set_markets, outcome_ids = WinlineHandler._build_snapshot()
                if not set_markets:
                    continue

                await WinlineHandler._callback({
                    "match_id": str(WinlineHandler._target_event_id),
                    "sport": "any",         # уточнит engine по payload
                    "set_markets": set_markets,
                    "outcome_ids": outcome_ids,
                    "score1": 0, "score2": 0,
                    "sub_score1": 0, "sub_score2": 0,
                    "phase_num": 0,
                })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Winline poll: {e}")

    @staticmethod
    def _build_snapshot():
        """
        Возвращает (set_markets, outcome_ids) для нашего матча.
        Поддерживает:
          - type=51  (1X2, 3 значения) — баскетбол/кибер
          - type=151 (П1/П2, 2 значения) — НТ/волейбол
          - type=71  (Тотал, 2 значения)
          - type=61  (Фора, 2 значения)
        """
        if not WinlineHandler._target_event_id:
            return {}, {}

        event_id = WinlineHandler._target_event_id
        menu = WinlineHandler._decoder.markets

        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        for (ev_id, market_id, coeff), line in list(WinlineHandler._lines.items()):
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

            # --- 1X2 (3-way): баскетбол/кибер ---
            if mtype == MARKET_TYPE_WINNER and len(values) >= 3:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
                if "1" not in w:
                    w["1"] = values[0]; o["1"] = {"id": line_id, "kf": values[0]}
                if "X" not in w:
                    w["X"] = values[1]; o["X"] = {"id": line_id, "kf": values[1]}
                if "2" not in w:
                    w["2"] = values[2]; o["2"] = {"id": line_id, "kf": values[2]}

            # --- П1/П2 (2-way): НТ/волей — победитель партии/сета ---
            elif mtype == MARKET_TYPE_WINNER_2WAY and len(values) >= 2:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
                if "1" not in w:
                    w["1"] = values[0]; o["1"] = {"id": line_id, "kf": values[0]}
                if "2" not in w:
                    w["2"] = values[1]; o["2"] = {"id": line_id, "kf": values[1]}

            # --- Тотал фазы ---
            elif mtype == MARKET_TYPE_TOTAL and len(values) >= 2 and line_val is not None:
                t = set_markets.setdefault(set_key, {}).setdefault("total", {})
                if "line" not in t:
                    t["line"] = line_val
                    t["over"] = values[0]
                    t["under"] = values[1]
                    o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
                    o["over"] = {"id": line_id, "kf": values[0], "line": line_val}
                    o["under"] = {"id": line_id, "kf": values[1], "line": line_val}

            # --- Фора фазы ---
            elif mtype == MARKET_TYPE_HANDICAP and len(values) >= 2 and line_val is not None:
                h = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
                if "1" not in h:
                    h["1"] = {"line": line_val, "odd": values[0]}
                    h["2"] = {"line": -line_val, "odd": values[1]}
                    o = outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})
                    o["1"] = {"id": line_id, "kf": values[0], "line": line_val}
                    o["2"] = {"id": line_id, "kf": values[1], "line": -line_val}

        return set_markets, outcome_ids
    # ============================================================
    # Отправка ставки
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """
        bet_data ожидает:
          - outcome_id (int)  — idLine (мы его кладём как outcome_id в engine)
          - amount    (float)
          - value     (float) — кэф (на случай, если обновился)
        """
        server = WinlineHandler._server
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
        WinlineHandler._bet_ack = None

        try:
            server.send("bet_ng")
            server.send(packet)
            logger.info(f"Winline: bet отправлен idLine={id_line} kf={kf} amount={amount}")
        except Exception as e:
            return {"success": False, "error": f"Winline send: {e}"}

        # Ждём 5 секунд — обычно ack приходит через 300-800мс
        for _ in range(25):
            await asyncio.sleep(0.2)
            if WinlineHandler._bet_ack:
                return WinlineHandler._bet_ack

        # Ответ не пришёл — но ставка могла пройти. Проверим купон.
        try:
            coupon = await page.evaluate(
                "() => JSON.parse(localStorage.getItem('desktop-appsavedCoupon') || '[]')"
            )
            if not coupon:
                return {"success": True, "betId": None}
        except Exception:
            pass

        # Купон не пуст — ставка не прошла
        return {"success": False, "error": "Winline: нет ответа от сервера"}

    # ============================================================
    # Ack (можно расширить после тестов)
    # ============================================================
    @staticmethod
    def _set_ack(success: bool, bet_id=None, error: str = None):
        WinlineHandler._bet_ack = {
            "success": success,
            "betId": bet_id,
            "error": error,
        }


# ============================================================
# Хелперы для матчинга команд
# ============================================================
def _tokenize(name: str) -> List[str]:
    if not name:
        return []
    tokens = re.split(r"\W+", name.lower())
    return [t for t in tokens if len(t) >= 3]


def _teams_match(t1_tokens: List[str], t2_tokens: List[str],
                 a: str, b: str) -> bool:
    """
    Сравниваем наш матч (t1_tokens, t2_tokens) с парой имён (a, b).
    Учитываем оба порядка (A-B или B-A). Достаточно ≥1 совпадения токенов.
    """
    a_tokens = _tokenize(a)
    b_tokens = _tokenize(b)

    def hit(tokens_src: List[str], tokens_dst: List[str]) -> bool:
        return any(t in tokens_dst for t in tokens_src)

    direct = hit(t1_tokens, a_tokens) and hit(t2_tokens, b_tokens)
    cross = hit(t1_tokens, b_tokens) and hit(t2_tokens, a_tokens)
    return direct or cross