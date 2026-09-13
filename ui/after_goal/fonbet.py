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
    async def setup_listener(page: Page, callback, match_id=None):
        FonbetHandler._page = page
        FonbetHandler._callback = callback
        if match_id:
            FonbetHandler._match_id = str(match_id)
        else:
            url = page.url
            match = re.search(r'/event/(\d+)', url)
            if match:
                FonbetHandler._match_id = match.group(1)
            else:
                logger.warning("Не удалось определить match_id для Fonbet")
        page.on("response", FonbetHandler._on_response)
        logger.info(f"Fonbet: перехват установлен для match_id={FonbetHandler._match_id}")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", FonbetHandler._on_response)
        except Exception:
            pass
        FonbetHandler._callback = None
        logger.info("Fonbet: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        # Слушаем event (страница матча) и liveEvents (общий список)
        if '/events/event' in url or '/ma/events/event' in url or '/ma/line/liveEvents' in url or '/ma/events/list' in url:
            try:
                data = await response.json()
                parsed = FonbetHandler.parse_update(data)
                if parsed and FonbetHandler._callback:
                    await FonbetHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Fonbet ошибка: {e}")

    # ============================================================
    # Определение активной фазы и её ID
    # ============================================================
    @staticmethod
    def _resolve_active_phase(data: dict, root_id: str):
        """
        Возвращает (phase_num, phase_event_id, phase_name).
        Ищем активную фазу через liveEventInfos → subscores.
        Fallback — парсим comment.
        """
        # 1) Через liveEventInfos
        for info in data.get('liveEventInfos', []) or []:
            if str(info.get('eventId')) != str(root_id):
                continue
            subs = info.get('subscores') or []
            for sub in subs:
                kind_id = sub.get('kindId')
                kind_name = sub.get('kindName', '')
                c1 = int(sub.get('c1', 0) or 0)
                c2 = int(sub.get('c2', 0) or 0)
                # Активная фаза — где счёт не 0:0, либо последняя в списке
                if c1 > 0 or c2 > 0:
                    # Извлекаем номер из name: "3-й сет" → 3
                    m = re.search(r'(\d+)', kind_name)
                    if m:
                        return int(m.group(1)), kind_id, kind_name

        # 2) Fallback — по дочерним events
        children = [ev for ev in data.get('events', []) or []
                    if str(ev.get('parentId')) == str(root_id)]
        if children:
            # Берём последний по sortOrder
            children.sort(key=lambda e: e.get('sortOrder', ''))
            last = children[-1]
            name = last.get('name', '')
            m = re.search(r'(\d+)', name)
            if m:
                return int(m.group(1)), str(last.get('id')), name

        return 0, None, ''

    # ============================================================
    # Разбор факторов активной фазы
    # ============================================================
    @staticmethod
    def _parse_phase_factors(factors: list, set_key: str,
                             set_markets: dict, outcome_ids: dict):
        """
        Раскладывает факторы фазы по рынкам winner/total/handicap.
        Базовые коды (работают для всех видов):
          921/923 — П1/П2 фазы
          910/912 — фора фазы
          1696/1697, 1848/1849, 3024/3025, 3030/3031 — тоталы фазы
          1845/1846 — фора фазы (альтернативный код)
        """
        # --- Winner ---
        p1 = next((f for f in factors if f.get('f') == 921), None)
        p2 = next((f for f in factors if f.get('f') == 923), None)
        if p1 or p2:
            w = set_markets.setdefault(set_key, {}).setdefault('winner', {})
            o = outcome_ids.setdefault(set_key, {}).setdefault('winner', {})
            if p1:
                w['1'] = p1.get('v')
                o['1'] = {'id': 921, 'kf': p1.get('v')}
            if p2:
                w['2'] = p2.get('v')
                o['2'] = {'id': 923, 'kf': p2.get('v')}

        # --- Handicap ---
        # 910/912 — основная фора
        h1 = next((f for f in factors if f.get('f') == 910), None)
        h2 = next((f for f in factors if f.get('f') == 912), None)
        # 1845/1846 — альтернативная фора (для НТ/волейбола)
        if not h1:
            h1 = next((f for f in factors if f.get('f') == 1845), None)
        if not h2:
            h2 = next((f for f in factors if f.get('f') == 1846), None)

        if h1 or h2:
            hk = set_markets.setdefault(set_key, {}).setdefault('handicap', {})
            o = outcome_ids.setdefault(set_key, {}).setdefault('handicap', {})
            if h1:
                line1 = FonbetHandler._extract_line(h1)
                hk.setdefault('1', {})['line'] = line1
                hk['1']['odd'] = h1.get('v')
                o.setdefault('1', {})['id'] = 910
                o['1']['kf'] = h1.get('v')
                o['1']['line'] = line1
            if h2:
                line2 = FonbetHandler._extract_line(h2)
                hk.setdefault('2', {})['line'] = line2
                hk['2']['odd'] = h2.get('v')
                o.setdefault('2', {})['id'] = 912
                o['2']['kf'] = h2.get('v')
                o['2']['line'] = line2

        # --- Total ---
        # Ищем любую пару тоталов из известных кодов
        total_codes = [
            (1696, 1697), (1848, 1849), (3024, 3025), (3030, 3031),
            (930, 931), (974, 976), (978, 980),
        ]
        for over_code, under_code in total_codes:
            to = next((f for f in factors if f.get('f') == over_code), None)
            tu = next((f for f in factors if f.get('f') == under_code), None)
            if to and tu:
                line = FonbetHandler._extract_line(to)
                t = set_markets.setdefault(set_key, {}).setdefault('total', {})
                if 'line' not in t:
                    t['line'] = line
                    t['over'] = to.get('v')
                    t['under'] = tu.get('v')
                    o = outcome_ids.setdefault(set_key, {}).setdefault('total', {})
                    o['over'] = {'id': over_code, 'kf': to.get('v'), 'line': line}
                    o['under'] = {'id': under_code, 'kf': tu.get('v'), 'line': line}
                break

    @staticmethod
    def _extract_line(factor: dict) -> float:
        """Извлекает линию из pt или p/100."""
        pt = str(factor.get('pt', '')).strip()
        m = re.search(r'([+-]?\d+\.?\d*)', pt)
        if m:
            return float(m.group(1))
        return factor.get('p', 0) / 100.0

    # ============================================================
    # Основной парсер
    # ============================================================
    @staticmethod
    def parse_update(data: dict) -> dict:
        match_id = FonbetHandler._match_id
        if not match_id:
            events = data.get('events', [])
            if events:
                match_id = str(events[0].get('id'))
            else:
                return None

        # --- Находим корневое событие ---
        event = None
        for ev in data.get('events', []) or []:
            if str(ev.get('id')) == str(match_id):
                event = ev
                break
        if not event:
            return None

        # --- Находим активную фазу ---
        phase_num, phase_event_id, phase_name = FonbetHandler._resolve_active_phase(data, match_id)

        # --- Счёт и comment ---
        misc = None
        for m in data.get('eventMiscs', []) or []:
            if str(m.get('id')) == str(match_id):
                misc = m
                break

        score1, score2 = 0, 0
        comment = ''
        if misc:
            score1 = int(misc.get('score1', 0) or 0)
            score2 = int(misc.get('score2', 0) or 0)
            comment = misc.get('comment', '') or ''
        elif event:
            comment = event.get('comment', '') or ''

        # sub_score из comment (последняя пара)
        sub1, sub2 = 0, 0
        if comment:
            pairs = re.findall(r'(\d+)[-:](\d+)', comment)
            if pairs:
                last = pairs[-1]
                sub1 = int(last[0])
                sub2 = int(last[1])

        # --- Собираем факторы активной фазы ---
        set_markets = {}
        outcome_ids = {}
        set_key = f"set_{phase_num}" if phase_num else None

        if phase_event_id:
            for block in data.get('customFactors', []) or []:
                if str(block.get('e')) == str(phase_event_id):
                    factors = block.get('factors', []) or []
                    FonbetHandler._parse_phase_factors(factors, set_key, set_markets, outcome_ids)
                    break

        return {
            "match_id": match_id,
            "sport": "any",          # engine определит по payload
            "phase_num": phase_num,
            "phase_name": phase_name,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    # ============================================================
    # Отправка ставки (без изменений)
    # ============================================================
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