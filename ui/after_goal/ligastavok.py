# ui/after_goal/ligastavok.py
import json
import logging
import re
import time
import uuid
from typing import Dict, Optional
from playwright.async_api import Page, Response, WebSocket
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


class LigaStavokHandler(BookmakerHandler):
    _callback = None
    _page = None
    _ws = None

    @staticmethod
    async def setup_listener(page: Page, callback):
        LigaStavokHandler._page = page
        LigaStavokHandler._callback = callback
        page.on("response", LigaStavokHandler._on_response)
        page.on("websocket", LigaStavokHandler._on_websocket)
        logger.info("Liga Stavok: перехват установлен (HTTP + WS)")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", LigaStavokHandler._on_response)
            page.remove_listener("websocket", LigaStavokHandler._on_websocket)
        except Exception:
            pass
        LigaStavokHandler._callback = None
        logger.info("Liga Stavok: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if '/rest/events/v8/eventsList' in url:
            try:
                data = await response.json()
                parsed = LigaStavokHandler.parse_update(data)
                if parsed and LigaStavokHandler._callback:
                    await LigaStavokHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Liga Stavok HTTP ошибка: {e}")

    @staticmethod
    def _on_websocket(ws: WebSocket):
        if 'lds-api-sites.ligastavok.ru/ws' in ws.url:
            LigaStavokHandler._ws = ws
            ws.on("framereceived", LigaStavokHandler._on_ws_frame)
            logger.info("Liga Stavok: WebSocket подключён")

    @staticmethod
    def _on_ws_frame(frame):
        try:
            payload = frame if isinstance(frame, str) else (frame.payload if hasattr(frame, 'payload') else str(frame))
            data = json.loads(payload)
            if not isinstance(data, dict):
                return
            if data.get("id") is not None:
                return
            result = data.get("result", {})
            payload_data = result.get("payload")
            if not isinstance(payload_data, list):
                return
            parsed = LigaStavokHandler.parse_update(payload_data, is_ws=True)
            if parsed and LigaStavokHandler._callback:
                import asyncio
                asyncio.create_task(LigaStavokHandler._callback(parsed))
        except Exception as e:
            logger.error(f"Liga Stavok WS ошибка: {e}")

    @staticmethod
    def parse_update(data, is_ws=False) -> Optional[Dict]:
        """Парсит данные из HTTP (eventsList) или WebSocket (обновления)."""
        if not is_ws:
            # HTTP ответ eventsList
            result = data.get('result', {})
            events_data = result.get('data', [])
            for ev in events_data:
                if ev.get('gameId') != 1246:
                    continue
                event_id = ev.get('id')
                event = ev.get('event', {})
                scores = ev.get('scores', {})
                outcomes = ev.get('outcomes', {})

                competitors = event.get('competitors', [])
                if len(competitors) >= 2:
                    player1 = competitors[0].get('name', '')
                    player2 = competitors[1].get('name', '')
                else:
                    player1 = player2 = ''

                total = scores.get('total', {})
                score1 = int(total.get('ScoreTeam1', 0))
                score2 = int(total.get('ScoreTeam2', 0))
                current = scores.get('current', {})
                sub1 = int(current.get('ScoreTeam1', 0))
                sub2 = int(current.get('ScoreTeam2', 0))
                if sub1 == 0 and sub2 == 0:
                    all_sets = scores.get('all', [])
                    if all_sets:
                        last = all_sets[-1]
                        sub1 = int(last.get('ScoreTeam1', 0))
                        sub2 = int(last.get('ScoreTeam2', 0))

                set_markets = {}
                outcome_ids = {}

                for out_key, out_val in outcomes.items():
                    outcome_key = out_val.get('outcomeKey')
                    odd = float(out_val.get('value', 0) or 0)
                    line = float(out_val.get('adValue', 0) or 0)

                    set_num = None
                    if outcome_key and '_' in outcome_key:
                        parts = outcome_key.split('_')
                        if len(parts) == 2 and parts[0].isdigit():
                            set_num = int(parts[0])
                    if set_num is None:
                        continue

                    set_key = f"set_{set_num}"

                    # ---- Победа игрока 1 ----
                    if outcome_key in ('_1', '1'):
                        set_markets.setdefault(set_key, {}).setdefault('winner', {})['1'] = odd
                        # ✅ ИСПРАВЛЕНО: сначала setdefault, потом присваивание через ['1']
                        outcome_ids.setdefault(set_key, {}).setdefault('winner', {})['1'] = {
                            'outcomeId': out_key,
                            'factorId': None,
                            'odd': odd,
                        }

                    # ---- Победа игрока 2 ----
                    elif outcome_key in ('_2', '2'):
                        set_markets.setdefault(set_key, {}).setdefault('winner', {})['2'] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault('winner', {})['2'] = {
                            'outcomeId': out_key,
                            'factorId': None,
                            'odd': odd,
                        }

                    # ---- Тотал больше ----
                    elif outcome_key == 'gross':
                        set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                        set_markets[set_key]['total']['over'] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault('total', {})['over'] = {
                            'outcomeId': out_key,
                            'factorId': None,
                            'odd': odd,
                        }

                    # ---- Тотал меньше ----
                    elif outcome_key == 'less':
                        set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                        set_markets[set_key]['total']['under'] = odd
                        outcome_ids.setdefault(set_key, {}).setdefault('total', {})['under'] = {
                            'outcomeId': out_key,
                            'factorId': None,
                            'odd': odd,
                        }

                return {
                    "match_id": event_id,
                    "player1": player1,
                    "player2": player2,
                    "score1": score1,
                    "score2": score2,
                    "sub_score1": sub1,
                    "sub_score2": sub2,
                    "set_markets": set_markets,
                    "outcome_ids": outcome_ids,
                }

        else:
            # WebSocket обновления — более точные
            set_markets = {}
            outcome_ids = {}
            match_id = None

            for item in data:
                if not isinstance(item, dict):
                    continue
                ev_id = item.get('id')
                if ev_id:
                    match_id = ev_id
                ws_data = item.get('data', {})
                if not isinstance(ws_data, dict):
                    continue

                headers = ws_data.get('headers', [])
                score1 = score2 = sub1 = sub2 = None
                for h in headers:
                    path = h.get('path')
                    val = h.get('value')
                    if path == '/scores/current/ScoreTeam1':
                        sub1 = int(val)
                    elif path == '/scores/current/ScoreTeam2':
                        sub2 = int(val)
                    elif path == '/scores/total/ScoreTeam1':
                        score1 = int(val)
                    elif path == '/scores/total/ScoreTeam2':
                        score2 = int(val)

                outcomes = ws_data.get('outcomes', [])
                for op in outcomes:
                    op_type = op.get('op')
                    path = op.get('path')
                    val = op.get('value')
                    if op_type in ('add', 'replace') and isinstance(val, dict):
                        # Здесь можно было бы обновлять outcome_ids,
                        # но пока оставим без изменений — HTTP-ответ главный.
                        pass

            return {
                "match_id": match_id,
                "score1": score1,
                "score2": score2,
                "sub_score1": sub1,
                "sub_score2": sub2,
                "set_markets": set_markets,
                "outcome_ids": outcome_ids,
            }

        return None

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """Отправляет ставку через API Лиги Ставок."""
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};

            const accountNumber = parseInt(localStorage.getItem('accountNumber')) || 0;
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
                        if (userObj.accountNumber) {{
                            accountNumber = userObj.accountNumber;
                        }}
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