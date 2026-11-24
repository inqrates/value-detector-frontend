# ui/after_goal/onewin.py
"""
Onewin (1win) handler — instance-based, перехват Socket.IO WS.

Транспорт (проверено по дампам 2026-11-24):
  - Engine.IO v4 / Socket.IO на api-gateway.top-parser.com/push-server-v2
  - handshake: сервер шлёт "0{...}", клиент — "40", сервер — "40{...sid...}"
  - heartbeat: сервер шлёт "2", клиент отвечает "3" (pingInterval=25000)
  - подписка:  42["subscribe",{"messageType":"subscribe-match-info",
               "data":{"matchIds":[...]}}] + subscribe-match-odds
  - данные:    42["u",{"data":{...},"messageType":"match-info-snapshot"|
               "match-info"|"match-odds-snapshot"|"match-odds"}]

Структура данных (одинакова для snapshot и инкрементов):
  match-info:  matchScore {t1,t2} — счёт МАТЧА (сеты),
               periodsScore [{t1,t2},...] — партии,
               status "3-й сет"/"2-я четверть" — имя фазы,
               sportId 24=НТ, 27=волейбол, 23=баскет
  match-odds:  oddsGroups [{name, isBase, renderType, outcomes, oddsList}]:
               "Победитель"              cols-2  → winner сета N (subgameIds=[N])
               "N-й cет. Победитель"     cols-2  → winner фазы N
               "Тотал"                   total-2 → total матча
               "N-й cет. Тотал"          total-2 → total фазы N
               "Фора"/"N-й cет. Фора"    fora-2  → handicap (матч/фаза)
               "Индивидуальный тотал …"  total-2 → it (матч)
               "N-я четверть. Индивидуальный тотал …" total-2 → it фазы N
               "Чет/Нечет"/"N-й сет. Нечет/Чет" cols-2 → odd (матч/фаза)
               "Первый до"/"N-й сет. Первый до" race-to → race (Р1/Р2 до N)
               "Ставки на очки"          cols-2  → point (сет N, очко M)

  ВАЖНО: названия групп могут прийти БЕЗ name (инкремент odds после
  snapshot) — кэшируем name/renderType/outcomes/id группы по её group id
  и матчим инкременты по id.

Ставка: POST https://api-gateway.top-parser.com/bets/make-bet-v2
  headers: token <JWT> (перехватываем из браузерных fetch),
           x-external-partner-id, x-lang, x-user-location
  payload: {currencyCode:"RUB", idempotencyKey:<uuid4>, amount,
            coupon:{amount, selections:[{oddId, cf, matchId}]},
            meta:{isDirtyAmountField:true, flags:["new_express_flow_v2"]},
            wallet:{type:"real"}}
  ответ:   {result:{success:{betId, cf, amount, ...}}}
"""
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Optional, Dict, Any, List, Callable, Tuple

from playwright.async_api import Page, WebSocket, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

# ── sportId OneWin → sport_key ──
SPORT_MAP = {
    24: "table_tennis",
    27: "volleyball",
    23: "basketball",
    18: "football",
}

# Подстрока WS-URL для фильтра перехвата
_WS_URL_MARKER = "push-server-v2"

# Домены (зеркала меняются — совпадают с parsers/onewin_api.py бекенда)
SITE_ORIGIN = "https://one-vv7078.com"
BET_API = "https://api-gateway.top-parser.com/bets/make-bet-v2"

# Прямое WS-подключение (использует core.fast_client.OnewinFastClient)
PARTNER_ID = "44ba10e5-7df2-47ab-a44d-dc93803c7a6e"
WS_URL = (
    "wss://api-gateway.top-parser.com/push-server-v2/"
    f"?Language=ru-RU&externalPartnerId={PARTNER_ID}"
    "&EIO=4&transport=websocket"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_PHASE_WORD = {
    "table_tennis": "сет",
    "volleyball":   "сет",
    "basketball":   "четверть",
}

# ── Регекспы фазы в названии группы ──
# ВАЖНО: в дампах встречается ЛАТИНСКАЯ «c» в слове «cет»
# ("3-й cет. Тотал") — покрываем оба варианта.
_PHASE_RE = re.compile(
    r"(\d+)\s*[-‑]?\s*(?:й|я|о|е|го)\s*(?:[cс]ет|четверть|парт[ия]|партия|период)"
)
_PHASE_PREFIX_RE = re.compile(
    r"\d+\s*[-‑]?\s*(?:й|я|о|е|го)\s*(?:[cс]ет|четверть|парт[ия]|партия|период)\.?\s*"
)


def _parse_group_name(name: str, sport: str = "table_tennis") -> Tuple[str, int, int]:
    """
    Разбирает название группы рынков.
    Возвращает (kind, phase_num, extra) где kind ∈
      winner|total|handicap|it|odd|race|point|other.
    phase_num=0 — рынок всего матча.
    """
    name_l = (name or "").lower().strip()

    # "Ставки на очки" (победа в N-м очко сета) — point, vars: v1=сет v2=очко
    if "ставки на очки" in name_l or ("очко" in name_l and "победа" in name_l):
        return "point", 0, 0

    if "первый до" in name_l:
        return "race", 0, 0

    # Номер фазы из названия: "3-й cет", "3-я четверть"
    m = _PHASE_RE.search(name_l)
    phase = int(m.group(1)) if m else 0

    base_name = _PHASE_PREFIX_RE.sub("", name_l).strip()

    if "индивидуальн" in base_name:
        return "it", phase, 0
    if "фора" in base_name:
        return "handicap", phase, 0
    if "тотал" in base_name:
        return "total", phase, 0
    # "Нечет/Чет" через границы слов: "точный счет" содержит "чет"!
    if re.search(r"нечет|нечёт|\bчет\b|\bчетн", base_name):
        return "odd", phase, 0
    if "победитель" in base_name:
        return "winner", phase, 0

    return "other", 0, 0


class OnewinHandler(BookmakerHandler):

    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id: Optional[str] = (
            str(target_match_id) if target_match_id else None
        )
        self.target_teams: List[str] = list(target_teams or [])

        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._sent_ws: Optional[WebSocket] = None
        self._token_task: Optional[asyncio.Task] = None

        # ── Состояние матча ──
        self._sport_key: str = "table_tennis"
        self._match_info: Dict[str, Any] = {}
        # {group_id: {"name","renderType","outcomes","subgameIds"}}
        self._groups: Dict[str, dict] = {}
        # {odd_id: {"cf","outcome","vars","group_id"}}
        self._odds: Dict[str, dict] = {}
        self._last_sent_state: Optional[tuple] = None
        self._it_group_order: List[str] = []
        self._resolved: bool = False

        # False — не эмитить snapshot на каждом odds-фрейме.
        # Ставит core.fast_client.OnewinFastClient: там снапшот
        # собирается по запросу, а не пушем.
        self._emit_enabled: bool = True

        # Когда по ЭТОМУ матчу пришёл последний пакет (match-info или
        # match-odds). Нужен fast-клиенту, чтобы не выдавать за свежие
        # данные матча, который завершился и молчит.
        self._last_frame_at: float = 0.0

        # ── JWT для make-bet-v2 (перехват fetch/XHR) ──
        self._token: str = ""

    # ============================================================
    # Публичный API для транспорта без браузера (core.fast_client)
    # ============================================================
    def feed_frame(self, msg: str) -> None:
        """
        Скормить хендлеру один сырой Engine.IO/Socket.IO пакет.
        Фильтрация по target_match_id — внутри.
        """
        self._process_frame(msg)

    def snapshot(self) -> Optional[dict]:
        """Текущий снапшот рынков (формат after_goal)."""
        return self._build_parsed()

    def reset_caches(self) -> None:
        """
        Сброс состояния. Нужен после реконнекта WS: сервер пришлёт
        новый snapshot, а старые (возможно закрытые) исходы из
        прошлой сессии не должны в него попасть.
        """
        self._match_info = {}
        self._groups = {}
        self._odds = {}
        self._it_group_order = []
        self._last_sent_state = None
        self._resolved = False
        self._last_frame_at = 0.0
        # ВАЖНО: self._token НЕ сбрасываем — JWT живёт в странице и
        # к реконнекту WS отношения не имеет.

    # ============================================================
    # Перехват WS
    # ============================================================
    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        if not self.target_match_id:
            m = re.search(r'/event/(\d+)', page.url)
            if m:
                self.target_match_id = m.group(1)

        self._page = page
        self._callback = callback
        self._emit_enabled = True
        self.reset_caches()

        await page.route_web_socket(f"**{_WS_URL_MARKER}**", self._handle_ws)
        logger.info(
            f"Onewin: route_web_socket установлен (target={self.target_match_id})"
        )

        # Fetch/XHR-хук — до reload, чтобы поймать token первого запроса.
        try:
            await self.setup_token_hook(page)
        except Exception as e:
            logger.debug(f"Onewin: pre-reload token hook: {e}")

        # reload — WS устанавливается при загрузке страницы,
        # перехват должен успеть до подписок сайта.
        try:
            current_url = page.url
            await page.goto(current_url, wait_until="domcontentloaded",
                            timeout=20000)
            logger.info("Onewin: страница перезагружена, WS перехвачен")
        except Exception as e:
            logger.warning(f"Onewin: ошибка перезагрузки: {e}")

        # Периодический опрос токена (хук пишет в window.__onewin_token)
        self._token_task = asyncio.create_task(self._token_poll_loop())

    async def _token_poll_loop(self):
        """Раз в 3с читает window.__onewin_token в self._token."""
        while True:
            try:
                await asyncio.sleep(3.0)
                page = self._page
                if not page or page.is_closed():
                    continue
                token = await page.evaluate("window.__onewin_token || ''")
                if token and token != self._token:
                    self._token = token
                    logger.info(
                        f"Onewin: JWT token перехвачен "
                        f"({token[:16]}…{token[-8:]})"
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    # ============================================================
    # Proxy WS (client ↔ server)
    # ============================================================
    def _handle_ws(self, ws: WebSocket):
        logger.info(f"Onewin: WS перехвачен {ws.url}")
        server = ws.connect_to_server()
        self._sent_ws = ws

        def from_client(msg):
            try:
                server.send(msg)
            except Exception as e:
                logger.debug(f"Onewin client→server: {e}")

        def from_server(msg):
            try:
                ws.send(msg)
                text = msg if isinstance(msg, str) else (
                    bytes(msg).decode("utf-8", "ignore")
                    if isinstance(msg, (bytes, bytearray)) else str(msg)
                )
                self._process_frame(text)
            except Exception as e:
                logger.debug(f"Onewin server→client: {e}")

        ws.on_message(from_client)
        server.on_message(from_server)

    def _process_frame(self, msg: str):
        if not msg.startswith("42"):
            return  # 0/40/2/3/44 — служебные Engine.IO пакеты
        try:
            data = json.loads(msg[2:])
        except json.JSONDecodeError:
            return
        if not isinstance(data, list) or len(data) < 2:
            return
        inner = data[1] if isinstance(data[1], dict) else {}
        payload = inner.get("data") or {}
        if not isinstance(payload, dict):
            return
        msg_type = inner.get("messageType") or ""

        if "oddsGroups" in payload:
            self._handle_odds(payload)
        elif payload.get("matchScore") or payload.get("periodsScore") or msg_type.startswith("match-info"):
            self._handle_info(payload)

    # ============================================================
    # match-info: счёт/фазы
    # ============================================================
    def _handle_info(self, payload: dict):
        match_id = str(payload.get("matchId", ""))
        if self.target_match_id and match_id != self.target_match_id:
            return
        self._last_frame_at = time.time()
        self._match_info = dict(payload)

        sport_id = payload.get("sportId")
        self._sport_key = SPORT_MAP.get(sport_id, "table_tennis")

        # Первый полный пакет — можно резолвить матч
        if not self._resolved and payload.get("matchScore"):
            self._resolved = True

    def _score_from_info(self) -> Tuple[int, int, int, int, int]:
        """
        (score1, score2, sub1, sub2, phase_num) из match-info.
        matchScore — счёт МАТЧА (сеты), periodsScore — список партий.
        """
        info = self._match_info
        try:
            score1 = int(float(info.get("matchScore", {}).get("t1", 0) or 0))
            score2 = int(float(info.get("matchScore", {}).get("t2", 0) or 0))
        except (ValueError, TypeError):
            score1 = score2 = 0

        periods = [p for p in (info.get("periodsScore") or [])
                   if isinstance(p, dict)]
        sub1 = sub2 = 0
        if periods:
            try:
                sub1 = int(float(periods[-1].get("t1", 0) or 0))
                sub2 = int(float(periods[-1].get("t2", 0) or 0))
            except (ValueError, TypeError):
                pass

        # phase_num: из status ("3-й сет"/"2-я четверть"), fallback — число партий
        status = (info.get("status") or "").lower()
        phase_num = 0
        m = re.search(r"(\d+)", status)
        if m:
            phase_num = int(m.group(1))
        if not (1 <= phase_num <= 10):
            phase_num = len(periods) or 1

        return score1, score2, sub1, sub2, phase_num

    def _prev_sub_from_info(self) -> Tuple[int, int]:
        """Счёт ЗАВЕРШЁННОЙ партии (periodsScore[-2]) — для after-goal."""
        periods = [p for p in (self._match_info.get("periodsScore") or [])
                   if isinstance(p, dict)]
        if len(periods) >= 2:
            try:
                return (int(float(periods[-2].get("t1", 0) or 0)),
                        int(float(periods[-2].get("t2", 0) or 0)))
            except (ValueError, TypeError):
                pass
        return 0, 0

    # ============================================================
    # match-odds: разбор групп
    # ============================================================
    def _handle_odds(self, payload: dict):
        match_id = str(payload.get("matchId", ""))
        if self.target_match_id and match_id != self.target_match_id:
            return
        self._last_frame_at = time.time()

        for group in payload.get("oddsGroups", []) or []:
            if not isinstance(group, dict):
                continue
            gid = str(group.get("id") or "")
            if not gid:
                continue

            # Кэш метаданных группы (в инкрементах name может отсутствовать)
            meta = self._groups.setdefault(gid, {})
            if group.get("name"):
                meta["name"] = group["name"]
            if group.get("renderType"):
                meta["renderType"] = group["renderType"]
            if group.get("outcomes"):
                meta["outcomes"] = group["outcomes"]
            if group.get("subgameIds"):
                meta["subgameIds"] = group["subgameIds"]

            for odd in group.get("oddsList", []) or []:
                if not isinstance(odd, dict):
                    continue
                oid = str(odd.get("id") or "")
                if not oid:
                    continue
                # status != 1 → исход недоступен (закрыт/снят)
                if odd.get("status", 1) != 1:
                    self._odds.pop(oid, None)
                    continue
                try:
                    cf = float(odd.get("cf", 0) or 0)
                except (ValueError, TypeError):
                    continue
                if cf <= 1.0:
                    continue

                prev = self._odds.get(oid) or {}
                # ── MERGE, не замещение ──
                # Инкременты match-odds приходят БЕЗ outcome/name/vars —
                # только {id, cf, status}. Сохраняем прежние метаданные.
                self._odds[oid] = {
                    "cf": cf,
                    "outcome": str(odd.get("outcome", "") or
                                   prev.get("outcome", "") or ""),
                    "name": str(odd.get("name", "") or
                                prev.get("name", "") or ""),
                    "vars": odd.get("vars") or prev.get("vars") or {},
                    "group_id": str(odd.get("group_id") or
                                    group.get("id") or
                                    prev.get("group_id", "")),
                    "ts": time.time(),
                }

        if not self._emit_enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._emit_snapshot())
        else:
            # Вне loop (тесты/синхронный разбор) — не эмитим:
            # snapshot будет собран по следующему пакету в loop.
            pass

    # ============================================================
    # Снапшот рынков
    # ============================================================
    async def _emit_snapshot(self):
        parsed = self._build_parsed()
        if not parsed:
            return

        state_hash = self._state_hash(parsed)
        if state_hash == getattr(self, "_last_sent_state", None):
            return
        self._last_sent_state = state_hash

        if self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Onewin callback: {e}")

    def _build_parsed(self) -> Optional[dict]:
        if not self._match_info:
            return None

        (score1, score2, sub1, sub2,
         phase_num) = self._score_from_info()
        prev_sub1, prev_sub2 = self._prev_sub_from_info()

        sport_key = getattr(self, "_sport_key", "table_tennis")

        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        # Группируем odds по group_id
        by_group: Dict[str, List[dict]] = {}
        for oid, odd in self._odds.items():
            by_group.setdefault(odd.get("group_id", ""), []).append(
                {**odd, "oid": oid}
            )

        for gid, odds_list in by_group.items():
            meta = self._groups.get(gid, {})
            name = meta.get("name", "")
            kind, g_phase, _extra = _parse_group_name(name, sport_key)

            if kind in ("other",):
                continue  # точный счёт, разница в очках и т.п.

            # Только рынки ТЕКУЩЕЙ фазы (группы без фазы = весь матч)
            if g_phase != phase_num:
                continue

            for odd in odds_list:
                self._classify_odd(
                    kind, odd, f"set_{phase_num}", set_markets, outcome_ids
                )

        return {
            "match_id": str(self.target_match_id or ""),
            "sport": sport_key,
            "phase_num": phase_num,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "prev_sub_score1": prev_sub1,
            "prev_sub_score2": prev_sub2,
            "prev_phase_num": (phase_num - 1) if phase_num > 1 else 0,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    def _classify_odd(self, kind: str, odd: dict, set_key: str,
                      set_markets: dict, outcome_ids: dict):
        """Кладёт один odd в нужный рынок по kind/outcome/vars."""
        outcome = odd.get("outcome", "")
        cf = odd.get("cf", 0)
        oid = odd.get("oid", "")
        vars_ = odd.get("vars") or {}
        name = odd.get("name", "")

        def _line_from_vars() -> float:
            try:
                return float(vars_.get("v1", 0) or 0)
            except (ValueError, TypeError):
                return 0.0

        if kind == "winner":
            if outcome in ("1", "2"):
                m = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
                # Приоритет — рынку текущей фазы (приходит одним блоком)
                m[outcome] = cf
                o[outcome] = {"id": oid, "kf": cf}

        elif kind == "handicap":
            if outcome not in ("1", "2"):
                return
            line = _line_from_vars()
            m = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
            o = outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})
            entry = m.setdefault(outcome, {})
            entry["line"] = line
            entry["odd"] = cf
            oe = o.setdefault(outcome, {})
            oe["id"] = oid
            oe["kf"] = cf
            oe["line"] = line

        elif kind == "total":
            if outcome not in ("over", "under"):
                return
            line = _line_from_vars()
            t = set_markets.setdefault(set_key, {}).setdefault("total", {})
            o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
            # Несколько линий тотала — берём первую валидную пару
            if "line" not in t:
                t["line"] = line
                o["over"] = {"id": oid, "kf": cf, "line": line}
                o["under"] = {"id": oid, "kf": cf, "line": line}
                t["over"] = None
                t["under"] = None
            # Заполняем over/under пары (могут прийти в одном пакете)
            if t.get("line") == line:
                t[outcome] = cf
                o[outcome] = {"id": oid, "kf": cf, "line": line}

        elif kind == "it":
            if outcome not in ("over", "under"):
                return
            line = _line_from_vars()
            player = self._resolve_it_player(odd.get("group_id", ""))
            if not player:
                return
            it = set_markets.setdefault(set_key, {}) \
                .setdefault("it", {}).setdefault(player, {})
            o = outcome_ids.setdefault(set_key, {}) \
                .setdefault("it", {}).setdefault(player, {})
            if "line" not in it:
                it["line"] = line
                it["over"] = None
                it["under"] = None
                o["over"] = None
                o["under"] = None
            if it.get("line") == line:
                it[outcome] = cf
                o[outcome] = {"id": oid, "kf": cf, "line": line}

        elif kind == "odd":
            if outcome in ("even", "odd"):
                m = set_markets.setdefault(set_key, {}).setdefault("odd", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("odd", {})
                m[outcome] = cf
                o[outcome] = {"id": oid, "kf": cf}

        elif kind == "point":
            # "Победа <Имя> В Сете N, Очко M": vars v1=сет, v2=очко
            try:
                set_n = int(float(vars_.get("v1", 0) or 0))
                point_n = int(float(vars_.get("v2", 0) or 0))
            except (ValueError, TypeError):
                return
            info = self._match_info
            periods = [p for p in (info.get("periodsScore") or [])
                       if isinstance(p, dict)]
            sub_now = 0
            if periods:
                try:
                    sub_now = (int(float(periods[-1].get("t1", 0) or 0))
                               + int(float(periods[-1].get("t2", 0) or 0)))
                except (ValueError, TypeError):
                    sub_now = 0
            if set_n != self._phase_from_match() or point_n <= sub_now:
                return  # очко уже сыграло / чужая фаза
            pm = set_markets.setdefault(set_key, {}).setdefault("point", {})
            o = outcome_ids.setdefault(set_key, {}).setdefault("point", {})
            # Ближайшее будущее очко — приоритет
            existing = pm.get("line")
            if existing is None or point_n < existing:
                pm.clear()
                o.clear()
                pm["line"] = point_n
            if pm.get("line") == point_n and outcome in ("1", "2"):
                pm[outcome] = cf
                o[outcome] = {"id": oid, "kf": cf}

        elif kind == "race":
            # "Первый до N": vars v1=N. Берём минимальную цель
            # больше текущего максимума очков фазы.
            if outcome not in ("1", "2"):
                return
            try:
                target = int(float(vars_.get("v1", 0) or 0))
            except (ValueError, TypeError):
                return
            info = self._match_info
            periods = [p for p in (info.get("periodsScore") or [])
                       if isinstance(p, dict)]
            cur_max = 0
            if periods:
                try:
                    cur_max = max(
                        int(float(periods[-1].get("t1", 0) or 0)),
                        int(float(periods[-1].get("t2", 0) or 0)),
                    )
                except (ValueError, TypeError):
                    cur_max = 0
            if target <= cur_max:
                return  # цель уже достигнута
            r = set_markets.setdefault(set_key, {}).setdefault("race", {})
            o = outcome_ids.setdefault(set_key, {}).setdefault("race", {})
            existing = r.get("target")
            if existing is None or target < existing:
                r.clear()
                o.clear()
                r["target"] = target
            if r.get("target") == target:
                r[outcome] = cf
                o[outcome] = {"id": oid, "kf": cf, "target": target}

    # ============================================================
    # Вспомогательные
    # ============================================================
    def _phase_from_match(self) -> int:
        return self._score_from_info()[4]

    def _resolve_it_player(self, group_id: str) -> Optional[str]:
        """
        ИТ-группы приходят парами в порядке [ИТ1, ИТ2] (по дампу:
        "Индивидуальный тотал Надежда…", "…Спартак…").
        Запоминаем порядок появления ИТ-групп.
        """
        if not hasattr(self, "_it_group_order"):
            self._it_group_order: List[str] = []
        if group_id not in self._it_group_order:
            self._it_group_order.append(group_id)
        try:
            idx = self._it_group_order.index(group_id)
        except ValueError:
            return None
        # idx 0 → ИТ1, idx 1 → ИТ2 (пары могут повторяться по фазам,
        # поэтому берём по модулю 2)
        player = str((idx % 2) + 1)
        return player

    @staticmethod
    def _state_hash(parsed: dict) -> tuple:
        sm = parsed.get("set_markets") or {}
        phase = parsed.get("phase_num")
        markets = sm.get(f"set_{phase}", {}) if phase else {}
        w = markets.get("winner") or {}
        t = markets.get("total") or {}
        hk = markets.get("handicap") or {}
        od = markets.get("odd") or {}
        it = markets.get("it") or {}
        pt = markets.get("point") or {}
        rc = markets.get("race") or {}
        return (
            phase,
            parsed.get("score1"), parsed.get("score2"),
            parsed.get("sub_score1"), parsed.get("sub_score2"),
            w.get("1"), w.get("2"),
            t.get("line"), t.get("over"), t.get("under"),
            hk.get("1", {}).get("odd"), hk.get("2", {}).get("odd"),
            od.get("even"), od.get("odd"),
            it.get("1", {}).get("over"), it.get("1", {}).get("under"),
            it.get("2", {}).get("over"), it.get("2", {}).get("under"),
            pt.get("1"), pt.get("2"),
            rc.get("target"), rc.get("1"), rc.get("2"),
        )

    # ============================================================
    # stop_listener
    # ============================================================
    async def stop_listener(self, page: Page):
        if self._token_task:
            self._token_task.cancel()
            self._token_task = None
        self._callback = None
        self._sent_ws = None
        logger.info(f"Onewin: listener снят (target={self.target_match_id})")

    # ============================================================
    # Ставка: make-bet-v2
    # ============================================================
    async def setup_token_hook(self, page: Page):
        """Вешает fetch/XHR-хук для перехвата JWT-токена 1win."""
        try:
            await page.evaluate(_TOKEN_HOOK_JS)
        except Exception as e:
            logger.warning(f"Onewin: token hook: {e}")

    async def place_bet(self, page: Page, bet_data: dict) -> dict:
        odd_id = str(bet_data.get("outcome_id") or "")
        if not odd_id:
            return {"success": False, "error": "Onewin: нет outcome_id (oddId)"}
        kf = float(bet_data.get("value") or 0)
        amount = float(bet_data.get("amount") or 0)
        if kf <= 1.0 or amount <= 0:
            return {"success": False,
                    "error": f"Onewin: невалидные kf={kf} amount={amount}"}

        match_id = bet_data.get("event_id") or self.target_match_id
        if not match_id:
            return {"success": False, "error": "Onewin: нет match_id"}

        # Токен: сначала кэш хука (без I/O), затем window.
        # Проверяем ДО page — ошибка токена не зависит от страницы.
        token = self._token or ""
        if not token and page is not None and not page.is_closed():
            try:
                token = await page.evaluate(
                    "window.__onewin_token || ''"
                ) or ""
            except Exception:
                token = ""
        if token:
            self._token = token
        if not token:
            return {"success": False,
                    "error": "Onewin: JWT token не перехвачен "
                             "(не залогинен или hook не установлен)"}

        if page is None or page.is_closed():
            return {"success": False, "error": "Onewin: страница закрыта"}

        payload = {
            "currencyCode": "RUB",
            "idempotencyKey": str(uuid.uuid4()),
            "amount": amount,
            "coupon": {
                "amount": amount,
                "selections": [{
                    "oddId": odd_id,
                    "cf": kf,
                    "matchId": int(match_id) if str(match_id).isdigit()
                    else match_id,
                }],
            },
            "meta": {
                "isDirtyAmountField": True,
                "flags": ["new_express_flow_v2"],
            },
            "wallet": {"type": "real"},
        }

        js = """
        async (args) => {
            try {
                const resp = await fetch(args.url, {
                    method: 'POST',
                    headers: {
                        'accept': 'application/json',
                        'content-type': 'application/json',
                        'token': args.token,
                        'x-external-partner-id': args.partnerId,
                        'x-lang': 'ru-RU',
                        'x-user-location': 'RU ',
                    },
                    credentials: 'include',
                    body: JSON.stringify(args.payload),
                });
                const text = await resp.text();
                let data = null;
                try { data = JSON.parse(text); } catch (e) {}
                return { status: resp.status, data: data, raw: text.slice(0, 500) };
            } catch (e) {
                return { status: 0, error: String(e) };
            }
        }
        """
        args = {
            "url": BET_API,
            "token": token,
            "partnerId": "44ba10e5-7df2-47ab-a44d-dc93803c7a6e",
            "payload": payload,
        }
        try:
            resp = await page.evaluate(js, args)
        except Exception as e:
            return {"success": False, "error": f"Onewin evaluate: {e}"}

        status = resp.get("status")
        data = resp.get("data") or {}
        success = (data.get("result") or {}).get("success") or {}

        if status == 200 and success.get("betId"):
            logger.info(
                f"Onewin: ✅ ставка betId={success.get('betId')} "
                f"cf={success.get('cf')} amount={success.get('amount')}"
            )
            return {
                "success": True,
                "betId": success.get("betId"),
                "cf": success.get("cf"),
            }

        err = (data.get("result") or {}).get("error") or data.get("error") \
            or resp.get("error") or f"HTTP {status}"
        # Ошибки 1win приходят в result.error или {error:{code,message}}
        if isinstance(err, dict):
            err = err.get("message") or err.get("code") or json.dumps(err)
        logger.warning(f"Onewin: ❌ ставка отклонена: {err}")
        return {"success": False, "error": str(err)}


# ════════════════════════════════════════════════════════════
# Fetch-хук: перехват JWT token из заголовков запросов сайта
# ════════════════════════════════════════════════════════════
_TOKEN_HOOK_JS = r"""
(function() {
    if (window.__onewin_token_hook) return;
    window.__onewin_token_hook = true;
    window.__onewin_token = window.__onewin_token || '';

    // 1) XHR
    const origOpen = XMLHttpRequest.prototype.open;
    const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
        try {
            if (String(name).toLowerCase() === 'token' && value) {
                window.__onewin_token = value;
            }
        } catch (e) {}
        return origSetHeader.apply(this, arguments);
    };

    // 2) fetch
    const origFetch = window.fetch;
    window.fetch = function(input, init) {
        try {
            let headers = (init && init.headers) || null;
            if (input && input.headers) headers = input.headers;
            if (headers) {
                if (typeof headers.forEach === 'function') {
                    headers.forEach((v, k) => {
                        if (String(k).toLowerCase() === 'token' && v) {
                            window.__onewin_token = v;
                        }
                    });
                } else if (Array.isArray(headers)) {
                    for (const [k, v] of headers) {
                        if (String(k).toLowerCase() === 'token' && v) {
                            window.__onewin_token = v;
                        }
                    }
                } else {
                    for (const k of Object.keys(headers)) {
                        if (String(k).toLowerCase() === 'token' && headers[k]) {
                            window.__onewin_token = headers[k];
                        }
                    }
                }
            }
        } catch (e) {}
        return origFetch.apply(this, arguments);
    };
})();
"""
