# ui/after_goal/sportbet.py
"""
Sportbet handler с поддержкой мультиспорта (НТ / волейбол / баскетбол).

Ключевые отличия от НТ-логики:
  1. WS-кадр `events:update` содержит ТОЛЬКО маркеты страницы `main`
     (матчевые рынки). Рынки фаз (сет/четверть) приходят через HTTP:
       GET /events.markets?eventId=<id>&page=set_1
       GET /events.markets?eventId=<id>&page=quarter_4
  2. ID маркетов различаются по фазам:
       main:      winner=186, total=238, handicap=237
       set_N:     winner=202, total=310, handicap=309
       quarter_N: свои (пока не видели)
     Поэтому тип маркета определяется по ИМЕНИ ГРУППЫ из pages:
       "Исход" / "Победитель" → winner
       "Тотал"                → total
       "Фора"                 → handicap
  3. Страницы фаз подгружаются lazy load — нужен отдельный HTTP-запрос.

Дедупликация:
  - HTTP-запросы к page-эндпоинту throttle'ятся (не чаще 1 раза/сек на матч).
  - Принудительный рефетч при смене фазы.
  - Callback вызывается только если state_hash изменился.

Ставка (HAR 2026-09-12):
  POST https://bthm-server.sportbet.ru/pari.stake?lang=ru
  Headers: content-type + idempotency-key: <UUIDv4>
  Body:    {"outcomes": [<outcome_id>], "amount": <amount>}
  Ответ OK: {"status":"ok","data":{"success":true},"uuid":"..."}
"""
import asyncio
import json
import logging
import re
import time
import uuid as _uuid
from typing import Optional, Dict, Any, List

from playwright.async_api import Page, WebSocket, Response

from .base import BookmakerHandler

logger = logging.getLogger(__name__)


# ID маркетов оставлены на будущее — но основная логика определяет тип
# по имени группы (см. _resolve_group_kind ниже).
_WIN_IDS   = (186, 202, 219)
_TOTAL_IDS = (238, 310, 225)
_HCP_IDS   = (237, 309, 223)

_WS_SILENCE_THRESHOLD = 8.0
_POLL_INTERVAL = 3.0
_MARKETS_FETCH_INTERVAL = 1.0    # сек между HTTP-запросами рынков одной фазы


class SportbetHandler(BookmakerHandler):

    _page: Optional[Page] = None
    _callback = None
    _target_match_id: Optional[str] = None
    _poll_task: Optional[asyncio.Task] = None
    _last_ws_frame_time: float = 0.0
    _last_sent_state: Optional[tuple] = None

    # match_id → время последнего HTTP-запроса рынков
    _last_fetch_time: Dict[str, float] = {}
    # match_id → номер текущей фазы (для принудительного рефетча при смене)
    _last_phase: Dict[str, int] = {}

    # ============================================================
    # Установка / снятие перехвата
    # ============================================================
    @staticmethod
    async def setup_listener(page: Page, callback, match_id=None):
        SportbetHandler._page = page
        SportbetHandler._callback = callback
        SportbetHandler._target_match_id = str(match_id) if match_id else None
        SportbetHandler._last_ws_frame_time = time.time()
        SportbetHandler._last_sent_state = None
        SportbetHandler._last_fetch_time = {}
        SportbetHandler._last_phase = {}

        page.on("websocket", SportbetHandler._on_websocket)
        page.on("response", SportbetHandler._on_response)

        logger.info(f"Sportbet: перехват установлен (match_id={match_id})")

        asyncio.create_task(SportbetHandler._fetch_snapshot_and_emit(page))
        SportbetHandler._poll_task = asyncio.create_task(
            SportbetHandler._poll_loop(page)
        )

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("websocket", SportbetHandler._on_websocket)
            page.remove_listener("response", SportbetHandler._on_response)
        except Exception:
            pass

        if SportbetHandler._poll_task:
            SportbetHandler._poll_task.cancel()
            SportbetHandler._poll_task = None

        SportbetHandler._callback = None
        SportbetHandler._target_match_id = None
        SportbetHandler._last_sent_state = None
        logger.info("Sportbet: перехват остановлен")

    # ============================================================
    # WS-перехват
    # ============================================================
    @staticmethod
    def _on_websocket(ws: WebSocket):
        if "bthm-server.sportbet.ru" in ws.url:
            ws.on("framereceived", SportbetHandler._on_ws_frame)
            logger.info(f"Sportbet: WS подключён {ws.url}")

    @staticmethod
    def _on_ws_frame(payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")

            SportbetHandler._last_ws_frame_time = time.time()

            # Socket.IO: "42[...]" — находим JSON-часть
            start = payload.find("[")
            if start == -1:
                return
            data = json.loads(payload[start:])
            if not (isinstance(data, list) and len(data) == 2):
                return

            # Принимаем оба типа: events:update и table:update
            event_type = data[0]
            if event_type not in ("events:update", "table:update"):
                return

            SportbetHandler._handle_payload(data[1])
        except Exception as e:
            logger.debug(f"Sportbet WS parse error: {e}")

    # ============================================================
    # HTTP-перехват (events.table)
    # ============================================================
    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if "events.table" not in url:
            return
        try:
            data = await response.json()
            SportbetHandler._handle_payload(data)
        except Exception as e:
            logger.debug(f"Sportbet HTTP response parse error: {e}")

    # ============================================================
    # Snapshot при старте
    # ============================================================
    @staticmethod
    async def _fetch_snapshot_and_emit(page: Page):
        try:
            await asyncio.sleep(0.5)
            data = await SportbetHandler._fetch_events_table(page)
            if data:
                SportbetHandler._handle_payload(data)
        except Exception as e:
            logger.debug(f"Sportbet initial snapshot: {e}")

    @staticmethod
    async def _fetch_events_table(page: Page) -> Optional[dict]:
        url = ("https://bthm-server.sportbet.ru/events.table"
               "?status=live&lang=ru&isTime=true")
        try:
            resp = await page.request.get(url, timeout=8000)
            if resp.status != 200:
                return None
            return await resp.json()
        except Exception as e:
            logger.debug(f"Sportbet events.table error: {e}")
            return None

    # ============================================================
    # HTTP: рынки конкретной страницы матча
    # ============================================================
    @staticmethod
    async def _fetch_page_markets(page: Page, event_id: str,
                                  page_key: str) -> List[dict]:
        """
        GET /events.markets?eventId=X&page=<page_key>&lang=ru
        Возвращает список маркетов (raw dicts).
        """
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

    @staticmethod
    async def _fetch_phase_markets(page: Page, event_id: str,
                                   pages: List[dict]) -> List[dict]:
        """
        Для каждой фазовой страницы (set_N / quarter_N / period_N / inning_N)
        дёргаем HTTP и собираем все маркеты.
        """
        result: List[dict] = []
        for p in pages:
            key = (p.get("key") or "").lower()
            if not re.match(r"^(set|quarter|period|inning)_\d+$", key):
                continue
            markets = await SportbetHandler._fetch_page_markets(page, event_id, key)
            result.extend(markets)
        return result

    # ============================================================
    # Polling WS liveness
    # ============================================================
    @staticmethod
    async def _poll_loop(page: Page):
        while True:
            try:
                await asyncio.sleep(_POLL_INTERVAL)
                silence = time.time() - SportbetHandler._last_ws_frame_time
                if silence < _WS_SILENCE_THRESHOLD:
                    continue
                data = await SportbetHandler._fetch_events_table(page)
                if data:
                    SportbetHandler._handle_payload(data)
                    SportbetHandler._last_ws_frame_time = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Sportbet poll: {e}")

    # ============================================================
    # Универсальный обработчик payload
    # ============================================================
    @staticmethod
    def _handle_payload(raw: Any):
        events = SportbetHandler._extract_events(raw)
        if not events:
            return
        target_id = SportbetHandler._target_match_id
        for event in events:
            ev_id = event.get("id")
            if not ev_id:
                continue
            if target_id and str(ev_id) != target_id:
                continue
            # Обрабатываем асинхронно — нужен await для HTTP fetch фаз
            asyncio.create_task(SportbetHandler._process_event(event))

    @staticmethod
    async def _process_event(event: dict):
        match_id = str(event.get("id") or "")
        if not match_id:
            return

        # --- Фаза (для триггера рефетча) ---
        match_status = (event.get("matchStatus") or "").strip()
        mm = re.search(r"(\d+)", match_status)
        phase_hint = int(mm.group(1)) if mm else 0

        # --- Throttle HTTP fetch ---
        now = time.time()
        last = SportbetHandler._last_fetch_time.get(match_id, 0.0)
        last_phase = SportbetHandler._last_phase.get(match_id, 0)
        phase_changed = (phase_hint != last_phase)
        need_fetch = phase_changed or (now - last >= _MARKETS_FETCH_INTERVAL)

        enriched = dict(event)
        if need_fetch and SportbetHandler._page:
            pages = event.get("pages") or []
            phase_markets = await SportbetHandler._fetch_phase_markets(
                SportbetHandler._page, match_id, pages
            )
            SportbetHandler._last_fetch_time[match_id] = now
            SportbetHandler._last_phase[match_id] = phase_hint

            if phase_markets:
                # мёржим фазовые маркеты к тем, что уже пришли через WS (main)
                enriched["markets"] = (event.get("markets") or []) + phase_markets

        parsed = SportbetHandler.parse_update({"events": [enriched]})
        if not parsed:
            return

        state = SportbetHandler._state_hash(parsed)
        if state == SportbetHandler._last_sent_state:
            return
        SportbetHandler._last_sent_state = state

        if SportbetHandler._callback:
            try:
                await SportbetHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Sportbet callback error: {e}")

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

    @staticmethod
    def _state_hash(parsed: dict) -> tuple:
        sm = parsed.get("set_markets") or {}
        phase = parsed.get("phase_num")
        set_key = f"set_{phase}" if phase else None
        markets = sm.get(set_key, {}) if set_key else {}
        w = markets.get("winner") or {}
        t = markets.get("total") or {}
        return (
            phase,
            parsed.get("score1"), parsed.get("score2"),
            parsed.get("sub_score1"), parsed.get("sub_score2"),
            w.get("1"), w.get("2"),
            t.get("line"), t.get("over"), t.get("under"),
        )

    # ============================================================
    # Парсинг события
    # ============================================================
    @staticmethod
    def _resolve_group_kind(group_name: str) -> Optional[str]:
        """
        Определяет тип рынка по имени группы.
        Работает для всех фаз и видов: main, set_1, quarter_4 и т.д.
        """
        if not group_name:
            return None
        g = group_name.strip().lower()
        if "исход" in g or "побед" in g:
            return "winner"
        if "тотал" in g:
            return "total"
        if "фора" in g:
            return "handicap"
        return None

    @staticmethod
    def parse_update(data: dict) -> Optional[dict]:
        if not isinstance(data, dict):
            return None

        event = None
        if "events" in data and isinstance(data["events"], list) and data["events"]:
            event = data["events"][0]
        elif "id" in data and "teams" in data:
            event = data
        else:
            return None

        match_id = event.get("id")
        if not match_id:
            return None

        # --- Вид спорта: строка или dict ---
        sport_raw = event.get("sport")
        sport_slug = ""
        if isinstance(sport_raw, dict):
            sport_slug = (sport_raw.get("slug") or "").strip()
            if not sport_slug:
                sid = sport_raw.get("id")
                if sid == 20:
                    sport_slug = "table_tennis"
                elif sid == 23:
                    sport_slug = "volleyball"
                elif sid == 2:
                    sport_slug = "basketball"
        elif isinstance(sport_raw, str):
            sport_slug = sport_raw.strip().replace("-", "_")

        # --- Общий счёт ---
        score_str = event.get("score", "0:0") or "0:0"
        try:
            score1, score2 = map(int, score_str.split(":"))
        except Exception:
            score1 = score2 = 0

        # --- Sub_score (последний сет/четверть из scores) ---
        scores_str = event.get("scores", "") or ""
        parts = [p.strip() for p in scores_str.split() if p.strip()]
        sub1 = sub2 = 0
        if parts:
            last = parts[-1]
            if ":" in last:
                try:
                    sub1, sub2 = map(int, last.split(":"))
                except Exception:
                    pass

        # --- Номер текущей фазы ---
        current_phase = 0
        match_status = (event.get("matchStatus") or "").strip()
        mm = re.search(r"(\d+)", match_status)
        if mm:
            current_phase = int(mm.group(1))
        if not current_phase:
            if sport_slug == "basketball":
                current_phase = len(parts) if parts else 1
            else:
                current_phase = score1 + score2 + 1

        # --- Карта groupId → (phase_num, kind) ---
        # kind ∈ {"winner", "total", "handicap"} — по имени группы.
        # Это устойчиво к разным ID маркетов у разных фаз.
        # Учитываем ТОЛЬКО фазовые страницы: set_N / quarter_N / period_N / inning_N.
        # main, half_N — игнорируем (матчевые рынки).
        pages = event.get("pages") or []
        page_groups: Dict[int, tuple] = {}   # groupId → (phase_num, kind)
        for p in pages:
            key = (p.get("key") or "").lower()
            mm2 = re.match(r"^(set|quarter|period|inning)_(\d+)$", key)
            if not mm2:
                continue
            page_phase = int(mm2.group(2))
            for g in p.get("groups") or []:
                gid = g.get("id")
                if gid is None:
                    continue
                kind = SportbetHandler._resolve_group_kind(g.get("name") or "")
                if kind:
                    page_groups[gid] = (page_phase, kind)

        # --- Маркеты ---
        markets = event.get("markets") or []
        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        for market in markets:
            group_id = market.get("groupId")
            info = page_groups.get(group_id)
            if not info:
                continue   # main, доп. рынки без фазы, чет/нечет — пропускаем

            phase_num, kind = info
            set_key = f"set_{phase_num}"
            outcomes = market.get("outcomes") or []

            # === Победитель фазы ===
            if kind == "winner":
                for out in outcomes:
                    if out.get("active") is False:
                        continue
                    name = out.get("name", "")
                    odd = out.get("odd")
                    if not odd:
                        continue
                    if name == "Поб 1":
                        set_markets.setdefault(set_key, {}).setdefault("winner", {})["1"] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault("winner", {}).setdefault("1", {})["id"] = out.get("id")
                    elif name == "Поб 2":
                        set_markets.setdefault(set_key, {}).setdefault("winner", {})["2"] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault("winner", {}).setdefault("2", {})["id"] = out.get("id")

            # === Тотал фазы ===
            elif kind == "total":
                for out in outcomes:
                    if out.get("active") is False:
                        continue
                    name = out.get("name", "") or ""
                    full_name = out.get("fullName", "") or ""
                    mm3 = re.search(r"(\d+\.?\d*)", full_name or name)
                    if not mm3:
                        continue
                    try:
                        line = float(mm3.group(1))
                    except ValueError:
                        continue
                    odd = out.get("odd")
                    if not odd:
                        continue
                    if "Больше" in full_name or name.startswith("ТБ"):
                        set_markets.setdefault(set_key, {}).setdefault("total", {})["line"] = line
                        set_markets[set_key]["total"]["over"] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault("total", {}).setdefault("over", {})["id"] = out.get("id")
                    elif "Меньше" in full_name or name.startswith("ТМ"):
                        set_markets.setdefault(set_key, {}).setdefault("total", {})["line"] = line
                        set_markets[set_key]["total"]["under"] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault("total", {}).setdefault("under", {})["id"] = out.get("id")

            # === Фора фазы ===
            elif kind == "handicap":
                for out in outcomes:
                    if out.get("active") is False:
                        continue
                    name = out.get("name", "") or ""
                    full_name = out.get("fullName", "") or ""
                    mm4 = re.search(r"\(([+-]?\d+\.?\d*)\)", full_name)
                    if not mm4:
                        continue
                    try:
                        line = float(mm4.group(1))
                    except ValueError:
                        continue
                    odd = out.get("odd")
                    if not odd:
                        continue
                    if "Фора 1" in name:
                        set_markets.setdefault(set_key, {}).setdefault("handicap", {}).setdefault("1", {})["line"] = line
                        set_markets[set_key]["handicap"]["1"]["odd"] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault("handicap", {}).setdefault("1", {})["id"] = out.get("id")
                    elif "Фора 2" in name:
                        set_markets.setdefault(set_key, {}).setdefault("handicap", {}).setdefault("2", {})["line"] = line
                        set_markets[set_key]["handicap"]["2"]["odd"] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault("handicap", {}).setdefault("2", {})["id"] = out.get("id")

        return {
            "match_id":    match_id,
            "sport":       sport_slug,
            "phase_num":   current_phase,
            "score1":      score1,
            "score2":      score2,
            "sub_score1":  sub1,
            "sub_score2":  sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    # ============================================================
    # Отправка ставки
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """
        bet_data:
          - outcome_id (int)   — ID исхода из outcome_ids
          - amount     (float) — сумма ставки

        POST https://bthm-server.sportbet.ru/pari.stake?lang=ru
        Headers:
          content-type: application/json
          idempotency-key: <UUIDv4>
        Body:
          {"outcomes": [<outcome_id>], "amount": <amount>}
        """
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
                    return {{
                        success: true,
                        betId: result.uuid || null
                    }};
                }}

                return {{
                    success: false,
                    error: 'Sportbet: ' + JSON.stringify(result)
                }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)