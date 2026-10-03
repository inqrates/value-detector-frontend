# ui/after_goal/fonbet.py
import json
import logging
import re
from typing import Optional, Dict, List, Callable
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


class FonbetHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id = str(target_match_id) if target_match_id else None
        self.target_teams = list(target_teams or [])
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._listener = None

    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if self._listener and self._page and not self._page.is_closed():
            try:
                self._page.remove_listener("response", self._listener)
            except Exception:
                pass

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

        async def on_response(response: Response):
            await self._handle_response(response)

        self._listener = on_response
        page.on("response", on_response)
        logger.info(f"Fonbet: listener установлен (target={self.target_match_id})")

    async def stop_listener(self, page: Page):
        if self._listener:
            try:
                page.remove_listener("response", self._listener)
            except Exception:
                pass
            self._listener = None
        self._callback = None
        logger.info(f"Fonbet: listener снят (target={self.target_match_id})")

    async def _handle_response(self, response: Response):
        url = response.url

        # ── Баланс Fonbet ──
        if '/session/info' in url:
            try:
                data = await response.json()
                saldo = data.get('saldo')
                if saldo is not None:
                    from ui.balance_bus import balance_bus
                    balance_bus.update('fonbet', saldo, 'RUB')
            except Exception:
                pass
            return

        # ── Live-парсинг ──
        if not ('/events/event' in url
                or '/ma/events/event' in url
                or '/ma/line/liveEvents' in url
                or '/ma/events/list' in url):
            return

        try:
            data = await response.json()
        except Exception:
            return
        parsed = FonbetHandler.parse_update(data, self.target_match_id)
        if parsed and self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Fonbet callback: {e}", exc_info=True)

    # ============================================================
    # Определение активной фазы
    # ============================================================
    @staticmethod
    def _resolve_active_phase(data: dict, root_id: str):
        """
        Приоритет 1: liveEventInfos.subscores с ненулевым счётом
        Приоритет 2 (fallback): ПЕРВАЯ партия среди children.
            Когда liveEventInfos ещё пуст (SPA не разослала subscores
            для только что стартовавших матчей) — активна первая партия.
        """
        for info in data.get('liveEventInfos', []) or []:
            if str(info.get('eventId')) != str(root_id):
                continue
            subs = info.get('subscores') or []
            for sub in subs:
                kind_id = sub.get('kindId')
                kind_name = sub.get('kindName', '')
                c1 = int(sub.get('c1', 0) or 0)
                c2 = int(sub.get('c2', 0) or 0)
                if c1 > 0 or c2 > 0:
                    m = re.search(r'(\d+)', kind_name)
                    if m:
                        return int(m.group(1)), kind_id, kind_name
        children = [ev for ev in data.get('events', []) or []
                    if str(ev.get('parentId')) == str(root_id)]
        if children:
            children.sort(key=lambda e: e.get('sortOrder', ''))
            # ── Fallback: ПЕРВАЯ партия (а не последняя) ──
            first = children[0]
            name = first.get('name', '')
            m = re.search(r'(\d+)', name)
            if m:
                return int(m.group(1)), str(first.get('id')), name
        return 0, None, ''

    @staticmethod
    def _parse_phase_factors(factors: list, set_key: str,
                             set_markets: dict, outcome_ids: dict):
        # ── WINNER ──
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

        # ── HANDICAP ──
        h1 = next((f for f in factors if f.get('f') == 910), None)
        h2 = next((f for f in factors if f.get('f') == 912), None)
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

        # ── TOTAL партии ──
        # ВАЖНО: 974/976 и 978/980 — это ИТ игроков, а НЕ тотал партии.
        # Их из этого списка убрали, парсим отдельно ниже.
        total_codes = [
            (1696, 1697), (1848, 1849), (3024, 3025), (3030, 3031),
            (930, 931), (1727, 1728), (1730, 1731), (1733, 1734),
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

        # ── Чёт/Нечёт партии (698=Чёт, 699=Нечёт) ──
        odd_yes = next((f for f in factors if f.get('f') == 698), None)
        odd_no  = next((f for f in factors if f.get('f') == 699), None)
        if odd_yes and odd_no:
            om = set_markets.setdefault(set_key, {}).setdefault('odd', {})
            om['even'] = odd_yes.get('v')
            om['odd']  = odd_no.get('v')
            o = outcome_ids.setdefault(set_key, {}).setdefault('odd', {})
            o['even'] = {'id': 698, 'kf': odd_yes.get('v')}
            o['odd']  = {'id': 699, 'kf': odd_no.get('v')}

        # ── Индивидуальные тоталы игроков ──
        # f=974/976 = ИТ1 Больше/Меньше
        # f=978/980 = ИТ2 Больше/Меньше
        it1_over  = next((f for f in factors if f.get('f') == 974), None)
        it1_under = next((f for f in factors if f.get('f') == 976), None)
        it2_over  = next((f for f in factors if f.get('f') == 978), None)
        it2_under = next((f for f in factors if f.get('f') == 980), None)

        for player, f_over, f_under in (
            ('1', it1_over,  it1_under),
            ('2', it2_over,  it2_under),
        ):
            if not (f_over and f_under):
                continue
            try:
                line = float(str(f_over.get('pt') or 0))
            except (ValueError, TypeError):
                continue
            if line <= 0:
                continue
            it = set_markets.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {})
            it['line']  = line
            it['over']  = f_over.get('v')
            it['under'] = f_under.get('v')
            o = outcome_ids.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {})
            o['over'] = {
                'id': 974 if player == '1' else 978,
                'kf': f_over.get('v'),
                'line': line,
            }
            o['under'] = {
                'id': 976 if player == '1' else 980,
                'kf': f_under.get('v'),
                'line': line,
            }

        # ── Следующее очко (f=2393=П1, f=2394=П2) ──
        pt1 = next((f for f in factors if f.get('f') == 2393), None)
        pt2 = next((f for f in factors if f.get('f') == 2394), None)
        if pt1 and pt2:
            pm = set_markets.setdefault(set_key, {}).setdefault('point', {})
            pm['1'] = pt1.get('v')
            pm['2'] = pt2.get('v')
            o = outcome_ids.setdefault(set_key, {}).setdefault('point', {})
            o['1'] = {'id': 2393, 'kf': pt1.get('v')}
            o['2'] = {'id': 2394, 'kf': pt2.get('v')}

    @staticmethod
    def _extract_line(factor: dict) -> float:
        pt = str(factor.get('pt', '')).strip()
        m = re.search(r'([+-]?\d+\.?\d*)', pt)
        if m:
            return float(m.group(1))
        return factor.get('p', 0) / 100.0

    @staticmethod
    def parse_update(data: dict, match_id: str = None) -> dict:
        if not match_id:
            events = data.get('events', [])
            if events:
                match_id = str(events[0].get('id'))
            else:
                return None

        event = None
        for ev in data.get('events', []) or []:
            if str(ev.get('id')) == str(match_id):
                event = ev
                break
        if not event:
            return None

        phase_num, phase_event_id, phase_name = FonbetHandler._resolve_active_phase(data, match_id)

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

        sub1, sub2 = 0, 0
        if comment:
            pairs = re.findall(r'(\d+)[-:](\d+)', comment)
            if pairs:
                last = pairs[-1]
                sub1 = int(last[0])
                sub2 = int(last[1])

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
            "sport": "any",
            "phase_num": phase_num,
            "phase_name": phase_name,
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
                        lang: 'ru', fsid: fsid, sysId: 21, clientId: clientId,
                        CDI: 518, deviceId: deviceId
                    }})
                }});
                const reqData = await reqIdResp.json();
                if (reqData.result !== 'requestId') throw new Error('requestId failed');
                const requestId = reqData.requestId;

                const betResp = await fetch('https://clientsapi-lb61-w.bk6bba-resources.com/coupon/bet', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'text/plain;charset=UTF-8' }},
                    body: JSON.stringify({{
                        requestId: requestId, lang: 'ru', clientId: clientId,
                        fsid: fsid, sysId: 21,
                        coupon: {{
                            amount: data.amount,
                            flexBet: 'any', flexParam: false,
                            mirror: 'https://fon.bet',
                            bets: [{{
                                num: 1, event: data.event_id,
                                factor: data.factor_id, value: data.value,
                                score: '0:0', zone: 'sp'
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
                            requestId: requestId, lang: 'ru', clientId: clientId,
                            fsid: fsid, sysId: 21
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