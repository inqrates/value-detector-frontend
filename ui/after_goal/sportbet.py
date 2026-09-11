# ui/after_goal/sportbet.py
import json
import logging
import re
from playwright.async_api import Page, WebSocket
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

class SportbetHandler(BookmakerHandler):
    _callback = None
    _page = None

    @staticmethod
    async def setup_listener(page: Page, callback):
        SportbetHandler._page = page
        SportbetHandler._callback = callback
        page.on("websocket", SportbetHandler._on_websocket)
        logger.info("Sportbet: перехват WS установлен")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("websocket", SportbetHandler._on_websocket)
        except:
            pass
        SportbetHandler._callback = None
        logger.info("Sportbet: перехват WS остановлен")

    @staticmethod
    def _on_websocket(ws: WebSocket):
        if 'bthm-server.sportbet.ru' in ws.url:
            ws.on("framereceived", SportbetHandler._on_frame)

    @staticmethod
    def _on_frame(payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode('utf-8')
            # Sportbet использует Socket.IO, но для простоты ищем "table:update"
            if 'table:update' in payload:
                # Извлекаем JSON-часть после префикса
                start = payload.find('[')
                if start != -1:
                    data = json.loads(payload[start:])
                    if isinstance(data, list) and len(data) == 2 and data[0] == "table:update":
                        parsed = SportbetHandler.parse_update(data[1])
                        if parsed and SportbetHandler._callback:
                            import asyncio
                            asyncio.create_task(SportbetHandler._callback(parsed))
        except Exception as e:
            logger.error(f"Sportbet WS ошибка: {e}")

    @staticmethod
    def parse_update(data: dict) -> dict:
        events = data.get('events', [])
        if not events:
            return None
        event = events[0]
        match_id = event.get('id')
        score_str = event.get('score', '0:0')
        try:
            score1, score2 = map(int, score_str.split(':'))
        except:
            score1, score2 = 0, 0

        # Сеты из scores
        scores_str = event.get('scores', '')
        sub1, sub2 = 0, 0
        if scores_str:
            parts = scores_str.split()
            if parts:
                last_set = parts[-1]
                try:
                    sub1, sub2 = map(int, last_set.split(':'))
                except:
                    pass

        markets = event.get('markets', [])
        set_markets = {}
        outcome_ids = {}

        # Группируем по страницам (pages) – из WebSocket приходят страницы с key="set_1" и т.д.
        pages = event.get('pages', [])
        if pages:
            # Карта groupId -> set_number
            group_to_set = {}
            for p in pages:
                if p.get('key', '').startswith('set_'):
                    set_num = p['key'].split('_')[1]
                    for g in p.get('groups', []):
                        group_to_set[g['id']] = set_num

            for market in markets:
                group_id = market.get('groupId')
                if group_id and group_id in group_to_set:
                    set_num = group_to_set[group_id]
                    set_key = f"set_{set_num}"
                    market_name = market.get('name', '')
                    outcomes = market.get('outcomes', [])
                    # Определяем тип рынка по groupId или по имени
                    # В выжимке: для set_1 группы: 102=Исход, 103=Тотал, 104=Фора
                    if group_id in (102, 112, 117, 122):  # Исход
                        for out in outcomes:
                            if out.get('name') == 'Поб 1':
                                set_markets.setdefault(set_key, {}).setdefault('winner', {})['1'] = out.get('odd')
                                outcome_ids.setdefault(set_key, {}).setdefault('winner', {}).setdefault('1', {})['id'] = out.get('id')
                            elif out.get('name') == 'Поб 2':
                                set_markets.setdefault(set_key, {}).setdefault('winner', {})['2'] = out.get('odd')
                                outcome_ids.setdefault(set_key, {}).setdefault('winner', {}).setdefault('2', {})['id'] = out.get('id')
                    elif group_id in (103, 113, 118, 123):  # Тотал
                        # Нужно извлечь линию из fullName
                        for out in outcomes:
                            line = None
                            full_name = out.get('fullName', '')
                            match = re.search(r'(\d+\.?\d*)', full_name)
                            if match:
                                line = float(match.group(1))
                            if 'Бол' in out.get('name', ''):
                                set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                                set_markets.setdefault(set_key, {}).setdefault('total', {})['over'] = out.get('odd')
                                outcome_ids.setdefault(set_key, {}).setdefault('total', {}).setdefault('over', {})['id'] = out.get('id')
                            elif 'Мен' in out.get('name', ''):
                                set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                                set_markets.setdefault(set_key, {}).setdefault('total', {})['under'] = out.get('odd')
                                outcome_ids.setdefault(set_key, {}).setdefault('total', {}).setdefault('under', {})['id'] = out.get('id')
                    elif group_id in (104, 114, 119, 124):  # Фора
                        for out in outcomes:
                            full_name = out.get('fullName', '')
                            # Извлекаем линию из fullName
                            match = re.search(r'\(([+-]?\d+\.?\d*)\)', full_name)
                            if match:
                                line = float(match.group(1))
                                if 'Фора 1' in out.get('name', ''):
                                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('1', {})['line'] = line
                                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('1', {})['odd'] = out.get('odd')
                                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('1', {})['id'] = out.get('id')
                                elif 'Фора 2' in out.get('name', ''):
                                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('2', {})['line'] = line
                                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('2', {})['odd'] = out.get('odd')
                                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('2', {})['id'] = out.get('id')

        return {
            "match_id": match_id,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        # bet_data содержит outcome_id, amount
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            try {{
                const resp = await fetch('https://bthm-server.sportbet.ru/pari.stake?lang=ru', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'Accept': 'application/json, text/plain, */*',
                        'Origin': 'https://sportbet.ru',
                        'Referer': 'https://sportbet.ru/'
                    }},
                    body: JSON.stringify({{
                        betslip: {{
                            bets: [{{
                                id: data.outcome_id
                            }}]
                        }},
                        stake: data.amount
                    }})
                }});
                const result = await resp.json();
                if (result.status === 'ok' && result.data.success) {{
                    return {{ success: true }};
                }} else {{
                    throw new Error('Ставка не прошла');
                }}
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)