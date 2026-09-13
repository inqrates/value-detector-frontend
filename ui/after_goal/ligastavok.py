# ui/after_goal/ligastavok.py
"""
LigaStavok handler — мультиспорт (НТ / волейбол / баскетбол / кибербаскет).

Формат actionLines (v6):
  result[0].event.ids.gameId  → вид спорта
    1246 = table_tennis
    128  = volleyball
    25   = basketball
    23139= cyber_basketball
  event.statusTranslated      → "4-я четверть" / "2-й сет" / "1-я партия"
  parts                       → {partKey: {id, title, code, main, ...}}
    "ot"     — Весь матч
    "main"   — Основное время
    "_NNN"   — конкретная фаза (например "_262145496" = 4-я четверть)
  markets["_NNN"]             → {type: "WIN"/"TTL"/"HAN", partId, ...}
  outcomes["_NNN"]            → {outcomeKey: "_1"/"x"/"gross"/"less"/"1"/"2", marketId, value, adValue}
  outcomesWinner              → {partKey: marketId}
  outcomesHandicap1           → {partKey: marketId}
  outcomesTotal1              → {partKey: marketId}
"""
import json
import logging
import re
from typing import Optional, Dict, List
from playwright.async_api import Page, Response, WebSocket
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


# gameId → sport_key
_GAME_ID_TO_SPORT = {
    1246:  "table_tennis",
    128:   "volleyball",
    25:    "basketball",
    23139: "cyber_basketball",
}


class LigaStavokHandler(BookmakerHandler):
    _callback = None
    _page = None

    # ============================================================
    # Перехват
    # ============================================================
    @staticmethod
    async def setup_listener(page: Page, callback):
        LigaStavokHandler._page = page
        LigaStavokHandler._callback = callback
        page.on("response", LigaStavokHandler._on_response)
        logger.info("LigaStavok: перехват установлен")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", LigaStavokHandler._on_response)
        except Exception:
            pass
        LigaStavokHandler._callback = None
        logger.info("LigaStavok: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        # v6/actionLines (новый) и v8/eventsList (старый) — оба слушаем
        if ('actionLines' in url
                or 'eventsList' in url
                or '/rest/events/' in url):
            try:
                data = await response.json()
                parsed = LigaStavokHandler.parse_update(data)
                if parsed and LigaStavokHandler._callback:
                    await LigaStavokHandler._callback(parsed)
            except Exception as e:
                logger.debug(f"LigaStavok parse error: {e}")

    # ============================================================
    # Определение активной фазы
    # ============================================================
    @staticmethod
    def _find_active_phase(event: dict, parts: dict):
        """
        Возвращает (phase_num, phase_key, phase_title).
        phase_key — ключ в parts, например "_262145496".
        """
        # 1) Из statusTranslated
        status = (event.get('statusTranslated') or '').strip()
        m = re.search(r'(\d+)', status)
        phase_num = int(m.group(1)) if m else 0

        if phase_num == 0:
            return 0, None, ''

        # 2) Ищем в parts часть с этим номером
        # parts: {"ot": ..., "main": ..., "_262145496": {id, title, code, ...}}
        for key, info in (parts or {}).items():
            if not isinstance(info, dict):
                continue
            title = info.get('title', '')
            code = info.get('code', '')
            # совпадение по номеру в title или в code (QUARTER_4, SET_4, ...)
            if (str(phase_num) in title and ('четверт' in title.lower()
                                             or 'сет' in title.lower()
                                             or 'партия' in title.lower())
                    or re.search(rf'_{phase_num}$', code)):
                return phase_num, key, title

        return phase_num, None, ''

    # ============================================================
    # Разбор outcomes фазы
    # ============================================================
    @staticmethod
    def _parse_phase_outcomes(
        phase_num: int,
        outcomes: dict,
        market_ids: Dict[str, int],
        set_markets: dict,
        outcome_ids: dict,
    ):
        """
        market_ids: {"winner": 833343809, "handicap": 833343810, "total": 833349118}
        """
        set_key = f"set_{phase_num}"
        winner_mid = market_ids.get("winner")
        handicap_mid = market_ids.get("handicap")
        total_mid = market_ids.get("total")

        for out_key, out in (outcomes or {}).items():
            if not isinstance(out, dict):
                continue
            mid = out.get("marketId")
            okey = (out.get("outcomeKey") or "").strip()
            val = out.get("value")
            adv = out.get("adValue", "0")
            fac_id = out.get("facId")

            if val is None:
                continue

            # --- WINNER ---
            if mid == winner_mid:
                w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})
                if okey in ("_1", "1"):
                    w["1"] = val
                    o["1"] = {"id": fac_id, "kf": val}
                elif okey == "x":
                    w["X"] = val
                    o["X"] = {"id": fac_id, "kf": val}
                elif okey in ("_2", "2"):
                    w["2"] = val
                    o["2"] = {"id": fac_id, "kf": val}

            # --- HANDICAP ---
            elif mid == handicap_mid:
                h = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})
                try:
                    line = float(str(adv).replace(',', '.'))
                except Exception:
                    line = 0.0
                if okey == "1":
                    h.setdefault("1", {})["line"] = line
                    h["1"]["odd"] = val
                    o["1"] = {"id": fac_id, "kf": val, "line": line}
                elif okey == "2":
                    h.setdefault("2", {})["line"] = line
                    h["2"]["odd"] = val
                    o["2"] = {"id": fac_id, "kf": val, "line": line}

            # --- TOTAL ---
            elif mid == total_mid:
                t = set_markets.setdefault(set_key, {}).setdefault("total", {})
                o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
                try:
                    line = float(str(adv).replace(',', '.'))
                except Exception:
                    line = 0.0
                if okey == "gross":
                    t["line"] = line
                    t["over"] = val
                    o["over"] = {"id": fac_id, "kf": val, "line": line}
                elif okey == "less":
                    t["line"] = line
                    t["under"] = val
                    o["under"] = {"id": fac_id, "kf": val, "line": line}

    # ============================================================
    # Основной парсер
    # ============================================================
    @staticmethod
    def parse_update(data) -> Optional[dict]:
        """
        Принимает ответ /v6/actionLines (dict с result[]) или старый /v8/eventsList.
        Возвращает {match_id, sport, phase_num, score, sub_score, set_markets, outcome_ids}.
        """
        if not isinstance(data, dict):
            return None

        # --- Достаём список событий ---
        # 1) actionLines: result = [...]  (список)
        # 2) actionLine:  result = {event, outcomes, ids, ...}  (одиночное)
        # 3) eventsList:  result = {data: [...]}
        result = data.get('result')
        events_list = []
        if isinstance(result, list):
            events_list = result
        elif isinstance(result, dict):
            if isinstance(result.get('data'), list):
                events_list = result['data']
            elif 'event' in result and 'ids' in result:
                # Одиночное событие из actionLine
                events_list = [result]
            else:
                return None
        else:
            return None

        for ev in events_list:
            if not isinstance(ev, dict):
                continue

            # --- Определяем вид спорта ---
            game_id = (ev.get('ids') or {}).get('gameId') or ev.get('gameId')
            try:
                game_id = int(game_id) if game_id is not None else 0
            except Exception:
                game_id = 0
            sport_key = _GAME_ID_TO_SPORT.get(game_id)
            if not sport_key:
                continue

            event = ev.get('event')
            if not isinstance(event, dict):
                continue

            match_id = ev.get('id') or event.get('extId')
            if not match_id:
                continue

            # --- Счёт ---
            scores = ev.get('scores') or {}
            total = scores.get('total') or {}
            current = scores.get('current') or {}
            try:
                score1 = int(total.get('ScoreTeam1') or 0)
                score2 = int(total.get('ScoreTeam2') or 0)
            except Exception:
                score1 = score2 = 0
            try:
                sub1 = int(current.get('ScoreTeam1') or 0)
                sub2 = int(current.get('ScoreTeam2') or 0)
            except Exception:
                sub1 = sub2 = 0

            # --- Активная фаза ---
            parts = ev.get('parts') or {}
            phase_num, phase_key, phase_title = LigaStavokHandler._find_active_phase(event, parts)

            set_markets: Dict[str, dict] = {}
            outcome_ids: Dict[str, dict] = {}

            # --- marketId для каждого рынка в активной фазе ---
            if phase_key:
                winner_map = ev.get('outcomesWinner') or {}
                handicap_map = ev.get('outcomesHandicap1') or {}
                total_map = ev.get('outcomesTotal1') or {}

                market_ids = {
                    "winner":   winner_map.get(phase_key),
                    "handicap": handicap_map.get(phase_key),
                    "total":    total_map.get(phase_key),
                }

                if any(market_ids.values()):
                    LigaStavokHandler._parse_phase_outcomes(
                        phase_num, ev.get('outcomes') or {}, market_ids,
                        set_markets, outcome_ids,
                    )

            return {
                "match_id": str(match_id),
                "sport": sport_key,
                "phase_num": phase_num,
                "score1": score1,
                "score2": score2,
                "sub_score1": sub1,
                "sub_score2": sub2,
                "set_markets": set_markets,
                "outcome_ids": outcome_ids,
            }

        return None

    # ============================================================
    # Отправка ставки (без изменений)
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};

            let accountNumber = parseInt(localStorage.getItem('accountNumber')) || 0;
            if (!accountNumber) {{
                const getCookie = (name) => {{
                    const value = `; ${{document.cookie}}`;
                    const parts = value.split(`; ${{name}}=`);
                    if (parts.length === 2) return parts.pop().split(';').shift();
                    return '';
                }};
                const xUser = getCookie('x-user');
                if (xUser) {{
                    try {{
                        const userObj = JSON.parse(decodeURIComponent(xUser));
                        if (userObj.accountNumber) accountNumber = userObj.accountNumber;
                    }} catch(e) {{}}
                }}
            }}

            const requestId = '{{' + Date.now() + '-' + Math.random().toString(36).substr(2, 9) + '}}';
            const requestTime = Math.floor(Date.now() / 1000);

            const apiCred = localStorage.getItem('apiCred') || '';
            const xUser = localStorage.getItem('x-user') || '';

            try {{
                const payload = {{
                    AcceptAdditionalOddChange: 1,
                    accept: 'all',
                    accountNumber: accountNumber,
                    accountType: 'BOOKMAKER',
                    allowBetSharing: false,
                    bets: [{{
                        amount: data.amount,
                        dimension: 1,
                        outcomes: [{{
                            factorId: data.factorId,
                            outcomeId: data.outcomeId
                        }}]
                    }}],
                    isDraft: false,
                    requestId: requestId,
                    requestTime: requestTime
                }};

                const resp = await fetch('https://api.ligastavok.ru/rest/bets/v3/makeBet', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-API-CRED': apiCred,
                        'X-User': xUser,
                        'X-Application-Name': 'mobile',
                        'Origin': 'https://www.ligastavok.ru',
                        'Referer': window.location.href
                    }},
                    body: JSON.stringify(payload)
                }});
                const result = await resp.json();
                if (result.httpCode === 200 && result.error === null) {{
                    const betResult = result.result && result.result[0];
                    if (betResult && betResult.errorCode === 0) {{
                        return {{ success: true, betId: betResult.betId }};
                    }} else {{
                        throw new Error(betResult?.errorMessage || 'Bet failed');
                    }}
                }} else {{
                    throw new Error('Request failed');
                }}
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)