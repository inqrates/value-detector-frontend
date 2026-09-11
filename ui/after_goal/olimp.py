# ui/after_goal/olimp.py
import json
import logging
import re
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

class OlimpHandler(BookmakerHandler):
    _callback = None
    _page = None

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
        except:
            pass
        OlimpHandler._callback = None
        logger.info("Olimp: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if 'api/v4/0/live/broadcast' in url or 'api/v4/0/live/sports-with-competitions-with-events' in url:
            try:
                data = await response.json()
                parsed = OlimpHandler.parse_update(data)
                if parsed and OlimpHandler._callback:
                    await OlimpHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Olimp ошибка: {e}")

    @staticmethod
    def parse_update(data: dict) -> dict:
        # Структура Olimp: data[0].payload.competitionsWithEvents[].events[]
        if not isinstance(data, list) or not data:
            return None

        first = data[0]
        payload = first.get('payload', {})
        competitions = payload.get('competitionsWithEvents', [])
        for comp in competitions:
            events = comp.get('events', [])
            for ev in events:
                # Проверяем sportId (40 = настольный теннис)
                if ev.get('sportId') != 40:
                    continue
                match_id = ev.get('id')
                score_str = ev.get('score', '0:0')
                try:
                    score1, score2 = map(int, score_str.split(':'))
                except:
                    score1, score2 = 0, 0

                # Сеты из комментария
                comment = ev.get('comment', '')
                sub1, sub2 = 0, 0
                if comment:
                    sets = re.findall(r'(\d+)[:*](\d+)', comment)
                    if sets:
                        last = sets[-1]
                        sub1 = int(last[0])
                        sub2 = int(last[1])

                # Коэффициенты из outcomes
                outcomes = ev.get('outcomes', [])
                set_markets = {}
                outcome_ids = {}
                # Определяем текущий сет
                set_num = 1
                if comment:
                    match = re.search(r'#(\d+)', comment)
                    if match:
                        set_num = int(match.group(1))
                set_key = f"set_{set_num}"

                for out in outcomes:
                    table_type = out.get('tableType', '')
                    short_name = out.get('shortName', '')
                    probability = float(out.get('probability', 0))
                    param = out.get('param', '')
                    # Для ставки нужны matchid и market_data
                    # Их можно получить из basketId или сформировать
                    basket_id = out.get('basketId', '')
                    # Пример basketId: "85771965:1:22:-9999.0:1:0:0:40"
                    if basket_id:
                        parts = basket_id.split(':')
                        if len(parts) >= 8:
                            outcome_ids.setdefault(set_key, {}).setdefault('_raw', {})[short_name] = {
                                'matchid': parts[0],
                                'market_data': basket_id,
                            }

                    if table_type == 'RESULT' and short_name in ('П1', 'П2'):
                        side = '1' if short_name == 'П1' else '2'
                        set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = probability
                    elif table_type == 'HANDICAP' and short_name in ('Фора 1', 'Фора 2'):
                        side = '1' if short_name == 'Фора 1' else '2'
                        line = float(param) if param else 0
                        set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})['line'] = line
                        set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})['odd'] = probability
                    elif table_type == 'TOTAL' and short_name in ('ТотМ', 'ТотБ'):
                        side = 'under' if short_name == 'ТотМ' else 'over'
                        line = float(param) if param else 0
                        set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                        set_markets.setdefault(set_key, {}).setdefault('total', {})[side] = probability

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
        # bet_data: matchid, market_data, value, amount
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            const getCookie = (name) => {{
                const value = `; ${{document.cookie}}`;
                const parts = value.split(`; ${{name}}=`);
                if (parts.length === 2) return parts.pop().split(';').shift();
                return '';
            }};
            const xToken = getCookie('x-token') || localStorage.getItem('x-token') || '';
            const xGuid = getCookie('x-guid') || localStorage.getItem('x-guid') || '';

            try {{
                const addPayload = {{
                    sid: 1,
                    value: data.value,
                    matchid: data.matchid,
                    market_data: data.market_data,
                    event_name: data.event_name || '',
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
                    body: JSON.stringify(addPayload)
                }});
                const addResult = await addResp.json();
                if (addResult.error.err_code !== 0) throw new Error('basket/add failed');

                const basketData = addResult.data || {{}};

                const savePayload = {{
                    ...basketData,
                    amount: data.amount,
                    sum: data.amount,
                }};
                delete savePayload.light;
                delete savePayload.isVip;

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
                    body: JSON.stringify(savePayload)
                }});
                const saveResult = await saveResp.json();
                if (saveResult.error.err_code === 0) {{
                    return {{ success: true, betId: saveResult.ids?.[0] || null }};
                }} else {{
                    throw new Error('basket/save failed');
                }}
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)