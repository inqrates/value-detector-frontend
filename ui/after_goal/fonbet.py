# ui/after_goal/fonbet.py
import json
import logging
import re
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

class FonbetHandler(BookmakerHandler):
    _callback = None
    _page = None
    _match_id = None

    @staticmethod
    async def setup_listener(page: Page, callback, match_id: int = None):
        FonbetHandler._page = page
        FonbetHandler._callback = callback
        if match_id:
            FonbetHandler._match_id = match_id
        else:
            # Попытаться извлечь из URL
            url = page.url
            match = re.search(r'/event/(\d+)', url)
            if match:
                FonbetHandler._match_id = int(match.group(1))
            else:
                logger.warning("Не удалось определить match_id для Fonbet")
        page.on("response", FonbetHandler._on_response)
        logger.info(f"Fonbet: перехват установлен для match_id={FonbetHandler._match_id}")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", FonbetHandler._on_response)
        except:
            pass
        FonbetHandler._callback = None
        logger.info("Fonbet: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if '/ma/line/liveEvents' in url or '/ma/events/list' in url:
            try:
                data = await response.json()
                parsed = FonbetHandler.parse_update(data)
                if parsed and FonbetHandler._callback:
                    await FonbetHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Fonbet ошибка: {e}")

    @staticmethod
    def parse_update(data: dict) -> dict:
        match_id = FonbetHandler._match_id
        if not match_id:
            # Попробовать найти событие в данных
            events = data.get('events', [])
            if events:
                match_id = events[0].get('id')
            else:
                return None

        # Ищем событие в events
        event = None
        for ev in data.get('events', []):
            if ev.get('id') == match_id:
                event = ev
                break

        # Ищем в eventMiscs
        misc = None
        for m in data.get('eventMiscs', []):
            if m.get('id') == match_id:
                misc = m
                break

        # Извлекаем счёт
        score1, score2 = 0, 0
        if misc:
            score1 = misc.get('score1', 0)
            score2 = misc.get('score2', 0)
        elif event and 'score' in event:
            score_str = event.get('score', '0:0')
            try:
                score1, score2 = map(int, score_str.split(':'))
            except:
                pass

        # Суб-счёт из комментария
        comment = ''
        if misc:
            comment = misc.get('comment', '')
        elif event:
            comment = event.get('comment', '')
        sub1, sub2 = 0, 0
        if comment:
            sets = re.findall(r'(\d+)[:*](\d+)', comment)
            if sets:
                last = sets[-1]
                sub1 = int(last[0])
                sub2 = int(last[1])

        # Коэффициенты (customFactors)
        factors = data.get('customFactors', [])
        set_markets = {}
        outcome_ids = {}

        set_codes = {
            1: {"winner": [922, 924, 925], "total": [930, 931], "handicap": [927, 928]},
            2: {"winner": [989, 991, 992], "total": [974, 976], "handicap": [989, 991]},
            3: {"winner": [1569, 1572, 1573], "total": [978, 980], "handicap": [1569, 1572]},
            4: {"winner": [1730, 1731, 1732], "total": [1733, 1734], "handicap": [1736, 1737]},
            5: {"winner": [1796, 1797, 1798], "total": [1802, 1803], "handicap": [1804, 1805]},
            6: {"winner": [1815, 1816, 1817], "total": [1824, 1825], "handicap": [1826, 1827]},
        }

        for f in factors:
            fid = f.get('f')
            val = f.get('v')
            pt = f.get('pt', '')
            p = f.get('p', 0)

            for set_num, codes in set_codes.items():
                if fid in codes['winner']:
                    side = '1' if fid == codes['winner'][0] else '2' if fid == codes['winner'][1] else 'draw'
                    set_key = f"set_{set_num}"
                    set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = val
                    outcome_ids.setdefault(set_key, {}).setdefault('winner', {}).setdefault(side, {})['id'] = fid
                    break
                elif fid in codes['total']:
                    side = 'over' if fid == codes['total'][0] else 'under'
                    line = float(pt) or p / 100
                    set_key = f"set_{set_num}"
                    set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                    set_markets.setdefault(set_key, {}).setdefault('total', {})[side] = val
                    outcome_ids.setdefault(set_key, {}).setdefault('total', {}).setdefault(side, {})['id'] = fid
                    break
                elif fid in codes['handicap']:
                    side = '1' if fid == codes['handicap'][0] else '2'
                    line = float(pt) or p / 100
                    set_key = f"set_{set_num}"
                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})['line'] = line
                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})['odd'] = val
                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})['id'] = fid
                    break

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
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            try {{
                const slipInfo = await fetch('https://clientsapi-lb61-w.bk6bba-resources.com/coupon/betSlipInfo', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'text/plain;charset=UTF-8' }},
                    body: JSON.stringify({{
                        bets: [{{
                            event: data.event_id,
                            factor: data.factor_id,
                            value: data.value
                        }}]
                    }})
                }});
                const slip = await slipInfo.json();
                if (slip.result !== 'betSlipInfo') throw new Error('betSlipInfo failed');

                const fsid = localStorage.getItem('fsid') || '';
                const clientId = parseInt(localStorage.getItem('clientId')) || 0;
                const deviceId = localStorage.getItem('deviceId') || '';

                const reqIdResp = await fetch('https://clientsapi-lb61-w.bk6bba-resources.com/coupon/betRequestId', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'text/plain;charset=UTF-8' }},
                    body: JSON.stringify({{
                        lang: 'ru',
                        fsid: fsid,
                        sysId: 21,
                        clientId: clientId,
                        CDI: 518,
                        deviceId: deviceId
                    }})
                }});
                const reqData = await reqIdResp.json();
                if (reqData.result !== 'requestId') throw new Error('requestId failed');
                const requestId = reqData.requestId;

                const betResp = await fetch('https://clientsapi-lb61-w.bk6bba-resources.com/coupon/bet', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'text/plain;charset=UTF-8' }},
                    body: JSON.stringify({{
                        requestId: requestId,
                        lang: 'ru',
                        clientId: clientId,
                        fsid: fsid,
                        sysId: 21,
                        coupon: {{
                            amount: data.amount,
                            flexBet: 'any',
                            flexParam: false,
                            mirror: 'https://fon.bet',
                            bets: [{{
                                num: 1,
                                event: data.event_id,
                                factor: data.factor_id,
                                value: data.value,
                                score: '0:0',
                                zone: 'sp'
                            }}]
                        }}
                    }})
                }});
                const betResult = await betResp.json();
                if (betResult.result === 'betDelay') {{
                    const resultResp = await fetch('https://clientsapi-lb54-w.bk6bba-resources.com/coupon/betResult', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'text/plain;charset=UTF-8' }},
                        body: JSON.stringify({{
                            requestId: requestId,
                            lang: 'ru',
                            clientId: clientId,
                            fsid: fsid,
                            sysId: 21
                        }})
                    }});
                    const final = await resultResp.json();
                    if (final.result === 'couponResult' && final.coupon.resultCode === 0) {{
                        return {{ success: true, betId: final.coupon.regId }};
                    }}
                }}
                throw new Error('bet failed');
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)