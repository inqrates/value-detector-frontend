# ui/after_goal/betcity.py
"""
Betcity handler — instance-based.

Особенности:
  - НЕТ class-level `_callback`/`_page` → у каждого матча свой инстанс.
  - `setup_listener` снимает старый listener ЭТОГО инстанса.
  - `_handle_response` фильтрует payload по `self.target_match_id`.
  - `_extract_all_events` возвращает ВСЕ события из payload.
  - `place_bet` перед add делает refresh `pos/kf/lv` через on_air/bets.

ФИЛЬТР МАТЧЕВЫХ РЫНКОВ:
  - `_parse_nt_markets` — только `ext` + row_name "N-я партия".
    Матчевые рынки в `main`, туда не попадают.
  - `_parse_phase_markets` — только `rows` с `num_per` (номер сета).
    Матчевые тоталы без num_per отсеиваются.

РЫНКИ В mid=35 (Исходы по партиям):
  - block W:   победитель партии (P1, P2)
  - block T1..T5: тоталы партии (5 линий: Tm=меньше, Tot=линия, Tb=больше)
  - block TEO: чёт/нечёт партии (E=Even=Чёт, O=Odd=Нечёт)
"""
import json
import logging
import re
from typing import Optional, Dict, List, Callable
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


_SPORT_IDS = {
    "46": "table_tennis",
    "12": "volleyball",
    "3":  "basketball",
}

_NT_WINNER   = "35"
_NT_HANDICAP = "905"
_NT_IT       = "913"


class BetcityHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id: Optional[str] = (
            str(target_match_id) if target_match_id else None
        )
        self.target_teams: List[str] = list(target_teams or [])

        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._listener = None

    # ============================================================
    # setup / stop
    # ============================================================
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

        self._page = page
        self._callback = callback

        async def on_response(response: Response):
            await self._handle_response(response)

        self._listener = on_response
        page.on("response", on_response)
        logger.info(
            f"Betcity: listener установлен (target={self.target_match_id})"
        )

    async def stop_listener(self, page: Page):
        if self._listener:
            try:
                page.remove_listener("response", self._listener)
            except Exception:
                pass
            self._listener = None
        self._callback = None
        logger.info(
            f"Betcity: listener снят (target={self.target_match_id})"
        )

    # ============================================================
    # Обработка ответа on_air/bets + баланс
    # ============================================================
    async def _handle_response(self, response: Response):
        url = response.url

        # ── Баланс Betcity ──
        if 'hdr.betcity.ru/d/user/info' in url:
            try:
                data = await response.json()
                avail = (((data or {}).get('reply') or {})
                         .get('money') or {}).get('avail')
                if avail is not None:
                    from ui.balance_bus import balance_bus
                    balance_bus.update('betcity', avail, 'RUB')
            except Exception:
                pass

        if 'on_air/bets' not in url:
            return
        try:
            data = await response.json()
        except Exception:
            return

        target = self.target_match_id
        if not target:
            return

        all_events = BetcityHandler._extract_all_events(data)

        if target not in all_events:
            logger.debug(
                f"📭 Betcity[{target}]: on_air response, "
                f"events={len(all_events)}, наш match_id НЕ найден"
            )
            return

        parsed = all_events.get(target)
        logger.debug(
            f"📬 Betcity[{target}]: наш match_id найден, "
            f"set_markets={list(parsed.get('set_markets', {}).keys())}"
        )

        if self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Betcity callback: {e}", exc_info=True)

    @staticmethod
    def _extract_all_events(data: dict) -> Dict[str, dict]:
        result: Dict[str, dict] = {}
        reply = data.get('reply') or {}
        sports = reply.get('sports') or {}
        if not sports:
            return result

        for sport_id_str, sport_block in sports.items():
            if not isinstance(sport_block, dict):
                continue
            sport_key = _SPORT_IDS.get(str(sport_id_str))
            if not sport_key:
                continue

            for chmp_id, chmp_data in (sport_block.get('chmps') or {}).items():
                if not isinstance(chmp_data, dict):
                    continue
                is_cyber = chmp_data.get('is_cyber', 0)
                eff_sport = sport_key
                if sport_key == "basketball" and is_cyber == 1:
                    eff_sport = "cyber_basketball"

                for ev_id, ev_data in (chmp_data.get('evts') or {}).items():
                    if not isinstance(ev_data, dict):
                        continue
                    if ev_data.get('team_type_f', 0) != 0:
                        continue
                    if ev_data.get('is_dep', 0) == 1:
                        continue

                    parsed = BetcityHandler._parse_event(
                        str(ev_id), ev_data, eff_sport
                    )
                    if parsed:
                        result[str(ev_id)] = parsed
        return result

    # ============================================================
    # Разбор одного события
    # ============================================================
    @staticmethod
    def _parse_event(match_id: str, ev_data: dict,
                     sport_key: str) -> Optional[dict]:
        player1 = ev_data.get('name_ht', '') or ''
        player2 = ev_data.get('name_at', '') or ''

        score_str = ev_data.get('sc_ev', '0:0') or '0:0'
        try:
            score1, score2 = map(int, score_str.split(':'))
        except Exception:
            score1 = score2 = 0

        sc_inter = ev_data.get('sc_inter', '') or ''
        sub1 = sub2 = 0
        if sc_inter:
            parts = [p.strip() for p in sc_inter.split(',') if p.strip()]
            if parts:
                try:
                    sub1, sub2 = map(int, parts[-1].split(':'))
                except Exception:
                    pass

        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        if sport_key == "table_tennis":
            BetcityHandler._parse_nt_markets(
                match_id, ev_data, set_markets, outcome_ids
            )
        else:
            BetcityHandler._parse_phase_markets(
                match_id, ev_data, set_markets, outcome_ids
            )

        if not set_markets:
            return None

        # phase_num = номер текущей партии/четверти.
        # Приоритет — длина sc_inter (сыгранные партии).
        # Fallback — арифметика score для НТ/волейбола.
        phase_num = 0
        if sport_key == "table_tennis":
            phase_num = score1 + score2 + 1
        elif sport_key == "volleyball":
            phase_num = score1 + score2 + 1
        elif sport_key == "basketball":
            phase_num = (len(sc_inter.split(',')) if sc_inter else 0) or 1
        else:
            phase_num = max(1, len(sc_inter.split(',')) if sc_inter else 1)

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
    # НТ-маркеты (mid=35, mid=905, mid=913)
    # ============================================================
    @staticmethod
    def _parse_nt_markets(match_id: str, ev_data: dict,
                          set_markets: dict, outcome_ids: dict):
        ext = ev_data.get('ext') or {}
        for market_id, market_data in ext.items():
            if market_id not in (_NT_WINNER, _NT_HANDICAP, _NT_IT):
                continue
            rows = market_data.get('rows') or {}
            for row_id, row in rows.items():
                row_name = row.get('name', '') or ''
                mm = re.search(r'(\d+)-я партия', row_name)
                if not mm:
                    logger.debug(
                        f"Betcity НТ: skip market={market_id} "
                        f"row_name={row_name!r} (не партия)"
                    )
                    continue
                set_num = mm.group(1)
                set_key = f"set_{set_num}"
                data_block = row.get('data') or {}
                for block_key, block in data_block.items():
                    blocks = block.get('blocks') or {}

                    # ── mid=35: Исходы по партиям ──
                    # Содержит W (winner), T1..T5 (тоталы), TEO (чёт/нечёт)
                    if market_id == _NT_WINNER:
                        # ── W: победитель партии ──
                        w = blocks.get('W') or {}
                        for side, key in (('1', 'P1'), ('2', 'P2')):
                            if key in w:
                                info = w[key]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}) \
                                        .setdefault('winner', {})[side] = odd
                                    outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('winner', {})[side] = {
                                            'id': match_id, 'pos': ps, 'kf': odd,
                                        }

                        # ── T1: тотал партии (первая линия) ──
                        t1 = blocks.get('T1') or {}
                        if t1:
                            try:
                                line = float(t1.get('Tot', 0) or 0)
                            except (ValueError, TypeError):
                                line = 0.0
                            tm_info = t1.get('Tm') or {}
                            tb_info = t1.get('Tb') or {}
                            odd_under = float(tm_info.get('kf', 0) or 0)
                            odd_over  = float(tb_info.get('kf', 0) or 0)
                            if line > 0 and (odd_over > 0 or odd_under > 0):
                                t = set_markets.setdefault(set_key, {}) \
                                    .setdefault('total', {})
                                t['line']  = line
                                t['over']  = odd_over
                                t['under'] = odd_under
                                o = outcome_ids.setdefault(set_key, {}) \
                                    .setdefault('total', {})
                                o['over'] = {
                                    'id': match_id,
                                    'pos': tb_info.get('ps'),
                                    'kf': odd_over,
                                    'lv': tb_info.get('lv', line),
                                }
                                o['under'] = {
                                    'id': match_id,
                                    'pos': tm_info.get('ps'),
                                    'kf': odd_under,
                                    'lv': tm_info.get('lv', line),
                                }

                        # ── TEO: чёт/нечёт партии (E=Even=Чёт, O=Odd=Нечёт) ──
                        teo = blocks.get('TEO') or {}
                        if teo:
                            e_info = teo.get('E') or {}
                            o_info = teo.get('O') or {}
                            odd_even = float(e_info.get('kf', 0) or 0)
                            odd_odd  = float(o_info.get('kf', 0) or 0)
                            if odd_even > 0 and odd_odd > 0:
                                om = set_markets.setdefault(set_key, {}) \
                                    .setdefault('odd', {})
                                om['even'] = odd_even
                                om['odd']  = odd_odd
                                oi = outcome_ids.setdefault(set_key, {}) \
                                    .setdefault('odd', {})
                                oi['even'] = {
                                    'id': match_id,
                                    'pos': e_info.get('ps'),
                                    'kf': odd_even,
                                }
                                oi['odd'] = {
                                    'id': match_id,
                                    'pos': o_info.get('ps'),
                                    'kf': odd_odd,
                                }

                    # ── mid=905: форы партии (4 линии) ──
                    elif market_id == _NT_HANDICAP:
                        for f_key, f_block in blocks.items():
                            if not f_key.startswith('F'):
                                continue
                            if 'Kf_F1' not in f_block or 'Kf_F2' not in f_block:
                                continue
                            for side, lkey, kkey in (
                                ('1', 'F1', 'Kf_F1'),
                                ('2', 'F2', 'Kf_F2'),
                            ):
                                line = float(f_block.get(lkey, 0) or 0)
                                info = f_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', 0)
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}) \
                                        .setdefault('handicap', {}) \
                                        .setdefault(side, {})
                                    set_markets[set_key]['handicap'][side]['line'] = line
                                    set_markets[set_key]['handicap'][side]['odd'] = odd
                                    outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('handicap', {})[side] = {
                                            'id': match_id, 'pos': ps,
                                            'kf': odd, 'lv': lv,
                                        }

                    # ── mid=913: ИТ партии (IT1, IT2 для игроков 1/2) ──
                    elif market_id == _NT_IT:
                        for it_key, it_block in blocks.items():
                            if 'Tm' not in it_block or 'Tb' not in it_block:
                                continue
                            line = float(it_block.get('Tot', 0) or 0)
                            for side, kkey in (('under', 'Tm'), ('over', 'Tb')):
                                info = it_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', line)
                                if odd > 0:
                                    player = ('1' if '_T1' in it_key
                                              or 'IT1_T1' in it_key else '2')
                                    set_markets.setdefault(set_key, {}) \
                                        .setdefault('it', {}) \
                                        .setdefault(player, {})[side] = odd
                                    set_markets[set_key]['it'][player]['line'] = line
                                    outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('it', {}) \
                                        .setdefault(player, {}) \
                                        .setdefault(side, {})
                                    outcome_ids[set_key]['it'][player][side] = {
                                        'id': match_id, 'pos': ps,
                                        'kf': odd, 'lv': lv,
                                    }

    # ============================================================
    # Волейбол / баскетбол / кибер (ext rows с num_per)
    # ============================================================
    @staticmethod
    def _parse_phase_markets(match_id: str, ev_data: dict,
                             set_markets: dict, outcome_ids: dict):
        # Только ext — фазовые рынки.
        # main содержит матчевые тоталы/форы — они нам не нужны.
        sources = []
        ext = ev_data.get('ext') or {}
        for m_id, m_data in ext.items():
            sources.append(m_data)

        for m_data in sources:
            if not isinstance(m_data, dict):
                continue
            rows = m_data.get('rows')
            if not isinstance(rows, dict):
                continue

            for row_id, row in rows.items():
                if not isinstance(row, dict):
                    continue
                num_per = row.get('num_per')
                if not num_per:
                    logger.debug(
                        f"Betcity: skip row_name={row.get('name')!r} "
                        f"(нет num_per)"
                    )
                    continue
                try:
                    set_num = int(num_per)
                except Exception:
                    continue

                set_key = f"set_{set_num}"
                data_block = row.get('data') or {}
                if not isinstance(data_block, dict):
                    continue

                for block_key, block in data_block.items():
                    if not isinstance(block, dict):
                        continue
                    blocks = block.get('blocks') or {}
                    if not isinstance(blocks, dict):
                        continue

                    w = blocks.get('W') or {}
                    for side, k in (('1', 'P1'), ('2', 'P2')):
                        if k in w:
                            info = w[k]
                            odd = float(info.get('kf', 0) or 0)
                            ps = info.get('ps')
                            if odd > 0:
                                set_markets.setdefault(set_key, {}) \
                                    .setdefault('winner', {})[side] = odd
                                outcome_ids.setdefault(set_key, {}) \
                                    .setdefault('winner', {})[side] = {
                                        'id': match_id, 'pos': ps, 'kf': odd,
                                    }

                    for f_key, f_block in blocks.items():
                        if not isinstance(f_block, dict):
                            continue
                        if 'Kf_F1' in f_block and 'Kf_F2' in f_block:
                            for side, lkey, kkey in (
                                ('1', 'F1', 'Kf_F1'),
                                ('2', 'F2', 'Kf_F2'),
                            ):
                                line = float(f_block.get(lkey, 0) or 0)
                                info = f_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', 0)
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}) \
                                        .setdefault('handicap', {}) \
                                        .setdefault(side, {})
                                    set_markets[set_key]['handicap'][side]['line'] = line
                                    set_markets[set_key]['handicap'][side]['odd'] = odd
                                    outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('handicap', {})[side] = {
                                            'id': match_id, 'pos': ps,
                                            'kf': odd, 'lv': lv,
                                        }

                    for t_key, t_block in blocks.items():
                        if not isinstance(t_block, dict):
                            continue
                        if 'Tm' in t_block and 'Tb' in t_block:
                            line = float(t_block.get('Tot', 0) or 0)
                            for side, kkey in (('under', 'Tm'), ('over', 'Tb')):
                                info = t_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', line)
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}) \
                                        .setdefault('total', {})['line'] = line
                                    set_markets[set_key]['total'][side] = odd
                                    outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('total', {})[side] = {
                                            'id': match_id, 'pos': ps,
                                            'kf': odd, 'lv': lv,
                                        }

                    for it_key, it_block in blocks.items():
                        if not isinstance(it_block, dict):
                            continue
                        if 'Tm' not in it_block or 'Tb' not in it_block:
                            continue
                        line = float(it_block.get('Tot', 0) or 0)
                        for side, kkey in (('under', 'Tm'), ('over', 'Tb')):
                            info = it_block[kkey]
                            odd = float(info.get('kf', 0) or 0)
                            ps = info.get('ps')
                            lv = info.get('lv', line)
                            if odd > 0:
                                if 'IT1_T1' in it_key or '_T1' in it_key:
                                    player = '1'
                                elif 'IT1_T2' in it_key or '_T2' in it_key:
                                    player = '2'
                                else:
                                    continue
                                set_markets.setdefault(set_key, {}) \
                                    .setdefault('it', {}) \
                                    .setdefault(player, {})[side] = odd
                                set_markets[set_key]['it'][player]['line'] = line
                                outcome_ids.setdefault(set_key, {}) \
                                    .setdefault('it', {}) \
                                    .setdefault(player, {}) \
                                    .setdefault(side, {})
                                outcome_ids[set_key]['it'][player][side] = {
                                    'id': match_id, 'pos': ps,
                                    'kf': odd, 'lv': lv,
                                }

    # ============================================================
    # Refresh pos/kf/lv перед ставкой
    # ============================================================
    async def _refresh_outcome(self, bet_data: dict) -> Optional[dict]:
        page = self._page
        event_id = str(bet_data.get('event_id') or '')
        if not page or page.is_closed() or not event_id:
            return None

        try:
            data = await page.evaluate("""
                (async () => {
                    try {
                        const r = await fetch(
                            'https://ad.betcity.ru/d/on_air/bets'
                            + '?rev=8&add=dep_events&ver=88&csn=ooca9s&lng=0',
                            { credentials: 'include' }
                        );
                        return await r.json();
                    } catch(e) { return null; }
                })()
            """)
        except Exception as e:
            logger.debug(f"Betcity refresh fetch: {e}")
            return None

        if not isinstance(data, dict):
            return None

        all_events = BetcityHandler._extract_all_events(data)
        parsed = all_events.get(event_id)
        if not parsed:
            logger.debug(f"Betcity refresh: матч {event_id} не найден в payload")
            return None

        outcome_ids = parsed.get('outcome_ids') or {}

        set_num = bet_data.get('set_num')
        market = bet_data.get('market')
        side = bet_data.get('side')

        if set_num and market and side:
            set_key = f"set_{set_num}"
            info = (outcome_ids.get(set_key, {})
                    .get(market, {})
                    .get(side))
            if info and info.get('pos') is not None:
                return info

        old_pos = bet_data.get('pos')
        if old_pos is not None:
            for set_key, sets in outcome_ids.items():
                for market_name, sides in sets.items():
                    for side_name, info in sides.items():
                        if not isinstance(info, dict):
                            continue
                        if str(info.get('pos')) == str(old_pos):
                            return info

        logger.debug(
            f"Betcity refresh: не нашли исход (event={event_id} "
            f"set={set_num} market={market} side={side} pos={old_pos})"
        )
        return None

    # ============================================================
    # Отправка ставки
    # ============================================================
    async def place_bet(self, page: Page, bet_data: dict) -> dict:
        # ── 1) Refresh pos/kf/lv ──
        fresh = await self._refresh_outcome(bet_data)
        if fresh:
            old_pos = bet_data.get('pos')
            old_kf = bet_data.get('kf')
            old_lv = bet_data.get('lv')
            if (str(fresh.get('pos')) != str(old_pos)
                    or abs(float(fresh.get('kf', 0) or 0) - float(old_kf or 0)) > 1e-6
                    or float(fresh.get('lv', 0) or 0) != float(old_lv or 0)):
                logger.warning(
                    f"Betcity: refresh pos {old_pos}→{fresh.get('pos')} "
                    f"kf {old_kf}→{fresh.get('kf')} "
                    f"lv {old_lv}→{fresh.get('lv')}"
                )
            bet_data['pos'] = fresh.get('pos')
            bet_data['kf'] = fresh.get('kf')
            bet_data['lv'] = fresh.get('lv') or 0
        else:
            logger.warning(
                "Betcity: свежая позиция не найдена — используем кэш "
                f"(event={bet_data.get('event_id')} pos={bet_data.get('pos')} "
                f"kf={bet_data.get('kf')})"
            )

        logger.warning(
            f"BETCITY BET_REQUEST: event_id={bet_data.get('event_id')} "
            f"pos={bet_data.get('pos')} kf={bet_data.get('kf')} "
            f"lv={bet_data.get('lv')} amount={bet_data.get('amount')}"
        )

        # ── 2) basket/add → basket/checkout ──
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            try {{
                const getCookie = (name) => {{
                    const m = document.cookie.match(new RegExp('(^|; )' + name + '=([^;]*)'));
                    return m ? decodeURIComponent(m[2]) : '';
                }};
                const token = getCookie('tk');
                if (!token) {{
                    return {{ success: false, error: 'Betcity: cookie tk не найден' }};
                }}

                const tum = Date.now() + '_' + Math.floor(Math.random() * 1000000);
                let addUrl = 'https://hdr.betcity.ru/d/basket/add'
                    + '?sys=1'
                    + '&id=' + encodeURIComponent(data.event_id)
                    + '&pos=' + encodeURIComponent(data.pos)
                    + '&k=' + encodeURIComponent(data.kf)
                    + '&ts=0'
                    + '&is_live=1';
                if (data.lv !== undefined && data.lv !== null && data.lv > 0) {{
                    addUrl += '&lv=' + encodeURIComponent(data.lv);
                }}
                addUrl += '&token=' + encodeURIComponent(token)
                       + '&tum=' + tum
                       + '&ver=88&csn=ooca9s';

                const addResp = await fetch(addUrl, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://betcity.ru',
                        'Referer': 'https://betcity.ru/'
                    }},
                    credentials: 'include',
                    body: 'cart=%7B%7D&settings=%7B%7D'
                }});
                const addResult = await addResp.json();

                if (!addResult.ok
                    || !addResult.reply
                    || !addResult.reply.bsks
                    || !addResult.reply.bsks[0]) {{
                    return {{
                        success: false,
                        error: 'Betcity basket/add: '
                            + JSON.stringify(addResult).slice(0, 400)
                    }};
                }}

                const bsk = addResult.reply.bsks[0];
                const ts = addResult.reply.ts;
                const s = bsk.s;
                const t = bsk.t;
                const maxBet = bsk.max || 100000;

                const itemKey = data.event_id + '_' + data.pos;
                const checkoutData = {{}};
                checkoutData[itemKey] = {{
                    id_ev: data.event_id,
                    ps: data.pos,
                    kf: data.kf,
                    is_live: 1,
                    t: t,
                    s: s
                }};

                const settings = {{
                    remember_bet: true,
                    clear_cart: false,
                    show_warning: false,
                    auto_vip_tg: false,
                    bets_kf_type: 'koeff',
                    cart_kf_type: 6,
                    fsum_1: 100,
                    fsum_2: 500,
                    fsum_3: 1000,
                    show_rec_sum: false
                }};

                const context = {{
                    api_method: 'checkout',
                    max_bet: maxBet,
                    shown_balance: maxBet,
                    max_vip_bet: maxBet,
                    rec_sum: -1,
                    scr_orientation: 'horizontal'
                }};

                const uuid = (crypto && crypto.randomUUID)
                    ? crypto.randomUUID()
                    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {{
                        const r = Math.random() * 16 | 0;
                        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
                    }});

                const body = [
                    'data=' + encodeURIComponent(JSON.stringify(checkoutData)),
                    'settings=' + encodeURIComponent(JSON.stringify(settings)),
                    'context=' + encodeURIComponent(JSON.stringify(context)),
                    'uuid=' + encodeURIComponent(uuid),
                    'bets[' + itemKey + ']=' + encodeURIComponent(data.amount)
                ].join('&');

                const checkoutUrl = 'https://hdr.betcity.ru/d/basket/checkout'
                    + '?type=6'
                    + '&ts=' + encodeURIComponent(ts)
                    + '&token=' + encodeURIComponent(token)
                    + '&tum=' + tum
                    + '&ver=88&csn=ooca9s';

                const checkoutResp = await fetch(checkoutUrl, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://betcity.ru',
                        'Referer': 'https://betcity.ru/'
                    }},
                    credentials: 'include',
                    body: body
                }});
                const checkoutResult = await checkoutResp.json();

                if (checkoutResult.ok
                    && checkoutResult.reply
                    && checkoutResult.reply.status === 0) {{
                    const info = (checkoutResult.reply.bsks_out_bets || [])[0] || {{}};
                    return {{
                        success: true,
                        betId: info.ids || info.item || 'N/A'
                    }};
                }}

                return {{
                    success: false,
                    error: 'Betcity checkout: '
                        + JSON.stringify(checkoutResult).slice(0, 400)
                }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)