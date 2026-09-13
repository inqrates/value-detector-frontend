# ui/after_goal/olimp.py
"""
Olimp handler — мультиспорт (НТ / волейбол / баскетбол / кибербаскет).

Форматы ответа:
  1) LIVE_EVENTS_GET_SOME — массив [{operationId, id, payload}] для конкретного матча.
  2) sports-with-competitions-with-events — агрегат для live-страницы.

Внутри payload конкретного матча:
  sportId: "40" (НТ), "10" (волей), "5" (баск), "140" (кибер)
  comment: "(30:10, 8:18) N-я четверть" (баск) / "(21:25, 8:6) #N ..." (волей)
  mapsScore: [ {team1, team2}, ... ] — очки по фазам
  outcomes: [ {tableType, shortName, basketId, probability, param}, ... ]

Ключ для ставки — basketId (`<match>:<pos>:<marketId>:<param>:<side>:0:0:<spid>`),
он уходит в place_bet как market_data.

Ставка (HAR 2026-09-12):
  1) POST /api/basket/add    → hash позиции
  2) POST /api/basket/save   → betId
Все секреты (x-token, x-guid, user_session) читаются из браузера AdsPower.
"""
import json
import logging
import re
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


_SPORT_ID_TO_KEY = {
    "40":  "table_tennis",
    "10":  "volleyball",
    "5":   "basketball",
    "140": "cyber_basketball",
}


class OlimpHandler(BookmakerHandler):
    _callback = None
    _page = None

    # ============================================================
    # Перехват
    # ============================================================
    @staticmethod
    async def setup_listener(page: Page, callback):
        OlimpHandler._page = page
        OlimpHandler._callback = callback
        page.on("response", OlimpHandler._on_response)
        logger.info("Olimp: перехват установлен")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", OlimpHandler._on_response)
        except Exception:
            pass
        OlimpHandler._callback = None
        logger.info("Olimp: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if 'api/v4/0/live/' not in url:
            return

        try:
            data = await response.json()
        except Exception:
            return

        if not isinstance(data, list):
            return

        for item in data:
            if not isinstance(item, dict):
                continue
            payload = item.get('payload')
            if not isinstance(payload, dict):
                continue

            parsed = OlimpHandler._parse_payload(payload)
            if parsed and OlimpHandler._callback:
                try:
                    await OlimpHandler._callback(parsed)
                except Exception as e:
                    logger.error(f"Olimp callback error: {e}")

    # ============================================================
    # Разбор payload — конкретный матч или агрегат
    # ============================================================
    @staticmethod
    def _parse_payload(payload: dict):
        # Вариант 1: конкретный матч (broadcast/LIVE_EVENTS_GET_SOME)
        if 'sportId' in payload and 'outcomes' in payload:
            return OlimpHandler._parse_event(payload)

        # Вариант 2: агрегат (sports-with-competitions-with-events)
        comps = payload.get('competitionsWithEvents')
        if isinstance(comps, list):
            for block in comps:
                if not isinstance(block, dict):
                    continue
                for event in (block.get('events') or []):
                    if not isinstance(event, dict):
                        continue
                    parsed = OlimpHandler._parse_event(event)
                    if parsed:
                        return parsed
        return None

    # ============================================================
    # Разбор одного матча
    # ============================================================
    @staticmethod
    def _parse_event(event: dict):
        sport_id = str(event.get('sportId') or '')
        sport_key = _SPORT_ID_TO_KEY.get(sport_id)
        if not sport_key:
            return None

        match_id = str(event.get('id') or '')
        if not match_id:
            return None

        player1 = event.get('team1Name', '') or ''
        player2 = event.get('team2Name', '') or ''

        # --- Общий счёт ---
        score_str = event.get('score', '') or '0:0'
        try:
            score1, score2 = map(int, score_str.split(':'))
        except Exception:
            score1 = score2 = 0

        # --- Счёт по фазам ---
        maps = event.get('mapsScore') or []
        pairs = []
        for m in maps:
            if isinstance(m, dict):
                try:
                    pairs.append((int(m.get('team1', 0) or 0),
                                  int(m.get('team2', 0) or 0)))
                except Exception:
                    pairs.append((0, 0))

        sub1 = sub2 = 0
        if pairs:
            sub1, sub2 = pairs[-1]

        # --- Номер фазы ---
        comment = event.get('comment', '') or ''
        phase_num = 0

        if sport_key in ('basketball', 'cyber_basketball'):
            mm = re.search(r'(\d+)-я\s+четверть', comment)
            if mm:
                phase_num = int(mm.group(1))
            else:
                phase_num = len(pairs) if pairs else 1
        elif sport_key == 'volleyball':
            mm = re.search(r'#(\d+)', comment)
            if mm:
                phase_num = int(mm.group(1))
            else:
                phase_num = score1 + score2 + 1
        else:  # НТ
            mm = re.search(r'#(\d+)', comment)
            if mm:
                phase_num = int(mm.group(1))
            else:
                phase_num = score1 + score2 + 1

        set_key = f"set_{phase_num}"
        set_markets: dict = {}
        outcome_ids: dict = {}

        outcomes = event.get('outcomes', []) or []

        # ---- НТ: старая логика (RESULT/HANDICAP/TOTAL без фазовых OTHER) ----
        if sport_key == 'table_tennis':
            for out in outcomes:
                if not isinstance(out, dict):
                    continue
                table_type = out.get('tableType', '')
                short_name = out.get('shortName', '')
                basket_id = out.get('basketId', '') or ''
                if not basket_id:
                    continue
                try:
                    prob = float(str(out.get('probability', '0')).replace(',', '.'))
                except Exception:
                    continue
                if prob <= 0:
                    continue
                try:
                    param = float(str(out.get('param', '0')).replace(',', '.'))
                except Exception:
                    param = 0.0

                if table_type == 'RESULT' and short_name in ('П1', 'П2'):
                    side = '1' if short_name == 'П1' else '2'
                    set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = prob
                    outcome_ids.setdefault(set_key, {}).setdefault('winner', {})[side] = {
                        'market_data': basket_id, 'kf': prob,
                    }
                elif table_type == 'HANDICAP' and short_name in ('Фора 1', 'Фора 2'):
                    side = '1' if short_name == 'Фора 1' else '2'
                    h = set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})
                    h['line'] = param
                    h['odd'] = prob
                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})
                    outcome_ids[set_key]['handicap'][side] = {
                        'market_data': basket_id, 'kf': prob, 'line': param,
                    }
                elif table_type == 'TOTAL' and short_name in ('ТотМ', 'ТотБ'):
                    side = 'under' if short_name == 'ТотМ' else 'over'
                    t = set_markets.setdefault(set_key, {}).setdefault('total', {})
                    t['line'] = param
                    t[side] = prob
                    outcome_ids.setdefault(set_key, {}).setdefault('total', {}).setdefault(side, {})
                    outcome_ids[set_key]['total'][side] = {
                        'market_data': basket_id, 'kf': prob, 'line': param,
                    }

        # ---- Волей / Баскет / Кибер: фазовые рынки OTHER ----
        else:
            for out in outcomes:
                if not isinstance(out, dict):
                    continue
                if out.get('tableType') != 'OTHER':
                    continue

                short_name = (out.get('shortName') or '').strip()
                if not short_name:
                    continue

                try:
                    prob = float(str(out.get('probability', '0')).replace(',', '.'))
                except Exception:
                    continue
                if prob <= 0:
                    continue

                try:
                    param = float(str(out.get('param', '0')).replace(',', '.'))
                except Exception:
                    param = 0.0

                basket_id = out.get('basketId', '') or ''
                if not basket_id:
                    continue

                # --- Победа в фазе: Ч4П1, П2П1 ---
                m = re.match(r'^[ЧП](\d+)П([12])$', short_name)
                if m:
                    n = int(m.group(1))
                    if n != phase_num:
                        continue
                    side = m.group(2)
                    set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = prob
                    outcome_ids.setdefault(set_key, {}).setdefault('winner', {})[side] = {
                        'market_data': basket_id, 'kf': prob,
                    }
                    continue

                # --- Фора в фазе: Ч4Ф1К, П2Ф1К ---
                m = re.match(r'^[ЧП](\d+)Ф([12])К$', short_name)
                if m:
                    n = int(m.group(1))
                    if n != phase_num:
                        continue
                    side = m.group(2)
                    h = set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})
                    # не перезаписываем (первая фора в списке = основная)
                    if 'line' not in h:
                        h['line'] = param
                        h['odd'] = prob
                        outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})
                        outcome_ids[set_key]['handicap'][side] = {
                            'market_data': basket_id, 'kf': prob, 'line': param,
                        }
                    continue

                # --- Тотал в фазе: Ч4ТотЧ4ТотМ, П2ТотП2ТотМ ---
                m = re.match(r'^[ЧП](\d+)Тот[ЧП]\d+Тот([МБ])$', short_name)
                if m:
                    n = int(m.group(1))
                    if n != phase_num:
                        continue
                    side = 'under' if m.group(2) == 'М' else 'over'
                    t = set_markets.setdefault(set_key, {}).setdefault('total', {})
                    if 'line' not in t:
                        t['line'] = param
                    t[side] = prob
                    outcome_ids.setdefault(set_key, {}).setdefault('total', {}).setdefault(side, {})
                    outcome_ids[set_key]['total'][side] = {
                        'market_data': basket_id, 'kf': prob, 'line': param,
                    }
                    continue

        if not set_markets:
            return None

        return {
            "match_id": match_id,
            "player1": player1,
            "player2": player2,
            "sport": sport_key,
            "phase_num": phase_num,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    # ============================================================
    # Отправка ставки — basket/add + basket/save
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """
        bet_data:
          - market_data (str)  — basketId
          - kf          (float)
          - amount      (float)
        """
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            try {{
                const getCookie = (name) => {{
                    const m = document.cookie.match(new RegExp('(^|; )' + name + '=([^;]*)'));
                    return m ? decodeURIComponent(m[2]) : '';
                }};

                const session = getCookie('user_session');
                if (!session) {{
                    return {{ success: false, error: 'Olimp: cookie user_session не найден (не залогинен?)' }};
                }}

                const xToken = getCookie('x-token') || localStorage.getItem('x-token') || '';
                const xGuid = getCookie('x-guid') || getCookie('visitor_id') || '';

                const mdParts = String(data.market_data).split(':');
                const sportId = parseInt(mdParts[mdParts.length - 1]) || 0;

                // --- 1) basket/add ---
                const coefsIds = JSON.stringify([[data.market_data, String(data.kf), 1]]);
                const addBody = {{
                    coefs_ids: coefsIds,
                    sport_id: sportId,
                    time_shift: 0,
                    lang_id: '0',
                    platforma: 'SITE_CUPIS',
                    session: session
                }};

                const addResp = await fetch('https://www.olimp.bet/api/basket/add', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Token': xToken,
                        'X-Guid': xGuid,
                        'X-Cupis': '1',
                        'X-Olimp': 'cupis-desktop',
                        'Origin': 'https://www.olimp.bet',
                        'Referer': window.location.href
                    }},
                    credentials: 'include',
                    body: JSON.stringify(addBody)
                }});
                const addResult = await addResp.json();

                if (!addResult.error || addResult.error.err_code !== 0) {{
                    return {{ success: false, error: 'Olimp basket/add: ' + JSON.stringify(addResult) }};
                }}
                const stakes = addResult.data && addResult.data.stakes_list;
                if (!stakes || Object.keys(stakes).length === 0) {{
                    return {{ success: false, error: 'Olimp basket/add: empty stakes_list' }};
                }}
                const hash = Object.keys(stakes)[0];

                // --- 2) basket/save ---
                const uniqueHash = Array.from(crypto.getRandomValues(new Uint8Array(16)))
                    .map(b => b.toString(16).padStart(2, '0')).join('');

                const saveBody = {{
                    sum: {{ [hash]: data.amount }},
                    bet_type: 1,
                    any_handicap: 1,
                    details: 1,
                    lang_id: '0',
                    platforma: 'SITE_CUPIS',
                    save_any: 1,
                    session: session,
                    time_shift: 0,
                    unique_bet_hash: uniqueHash
                }};

                const saveResp = await fetch('https://www.olimp.bet/api/basket/save', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Token': xToken,
                        'X-Guid': xGuid,
                        'X-Cupis': '1',
                        'X-Olimp': 'cupis-desktop',
                        'Origin': 'https://www.olimp.bet',
                        'Referer': window.location.href
                    }},
                    credentials: 'include',
                    body: JSON.stringify(saveBody)
                }});
                const saveResult = await saveResp.json();

                if (saveResult.error && saveResult.error.err_code === 0) {{
                    const betId = (saveResult.ids && saveResult.ids[0]) || null;
                    return {{ success: true, betId: betId }};
                }}
                return {{ success: false, error: 'Olimp basket/save: ' + JSON.stringify(saveResult) }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)