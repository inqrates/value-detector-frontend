# ui/after_goal/betcity.py
"""
Betcity handler — instance-based.

Особенности:
  - НЕТ class-level `_callback`/`_page` → у каждого матча свой инстанс.
  - `setup_listener` снимает старый listener ЭТОГО инстанса.
  - `_handle_response` фильтрует payload по `self.target_match_id`.
  - `_extract_all_events` возвращает ВСЕ события из payload.
  - `place_bet` перед add делает refresh `pos/kf/lv` через on_air/bets.
  - _poll_loop: активный pull данных каждые 1.5с (чтобы SPA не залипал в headless).

ФИЛЬТР МАТЧЕВЫХ РЫНКОВ:
  - `_parse_nt_markets` — только `ext` + row_name "N-я партия".
    Матчевые рынки в `main`, туда не попадают.
  - `_parse_phase_markets` — только `rows` с `num_per` (номер сета).
    Матчевые тоталы без num_per отсеиваются.

РЫНКИ в mid=35 (Исходы по партиям):
  - block W:   победитель партии (P1, P2)
  - block T1..T5: тоталы партии (5 линий: Tm=меньше, Tot=линия, Tb=больше)
  - block TEO: чёт/нечёт партии (E=Even=Чёт, O=Odd=Нечёт)

ИТ (mid=913) — ключи блоков:
  - IT1_T1, IT1_T2 → ИТ ИГРОКА 1
  - IT2_T1, IT2_T2 → ИТ ИГРОКА 2

ФИКС 2026-10-14 (баскет Betcity, путаница тотала и ИТ):
  - При обработке `ext[787]` (ИТ по четвертям) блоки IT1_T1..IT2_T2
    имеют те же ключи Tm/Tb/Tot, что и тоталы четверти из `ext[779]`.
    Без фильтра `total` перезаписывался последним ИТ-блоком —
    движок ставил ИТ игрока (pos=4029), думая что ставит тотал
    четверти. Пример: движок хотел "ТБ 22.5 3-я четверть",
    в купоне Betcity оказалось "ИТБ2(23.5)".
  - РЕШЕНИЕ: раздельная обработка по market_id (779 vs 787),
    плюс фильтр `t_key.startswith('IT')` в общем цикле тоталов.

ФИКС 2026-10-22:
  - Добавлен активный `_poll_loop`. Браузер в headless-режиме
    перестаёт обновлять данные через пассивный listener
    (`page.on("response")`), из-за чего `slow_data` залипает
    на старом счёте (например, 0:6, когда реально 3:11).
    Теперь мы принудительно дергаем API каждые 1.5 сек.
"""
import asyncio
import json
import logging
import re
import time
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

# ── Betcity market_id для баскета/волейбола (фазовые) ──
_BASKET_WINNER_TOTAL = "779"   # Исходы по четвертям: W, WX, F1/F2, T1..T5, TEO
_BASKET_IT           = "787"   # ИТ по четвертям: IT1_T1..IT2_T2, TEO1/TEO2


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
        
        # ── НОВОЕ: фоновая задача для принудительного опроса ──
        self._poll_task: Optional[asyncio.Task] = None

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
        
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

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
        
        # ── Запуск фонового polling ──
        self._poll_task = asyncio.create_task(self._poll_loop())
        
        logger.info(
            f"Betcity: listener установлен (target={self.target_match_id}), "
            f"poll_loop запущен"
        )

    async def stop_listener(self, page: Page):
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

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
    # Принудительный polling (чтобы SPA не залипал в headless)
    # ============================================================
    async def _poll_loop(self):
        # Даём странице 3 секунды на первичную загрузку
        await asyncio.sleep(3.0)
        
        target = self.target_match_id
        if not target:
            return

        while self._callback is not None:
            try:
                page = self._page
                if not page or page.is_closed():
                    logger.debug("Betcity poll: страница закрыта, стоп")
                    break

                js = f"""
                (async () => {{
                    try {{
                        const r = await fetch(
                            'https://ad.betcity.ru/d/on_air/bets'
                            + '?rev=8&add=dep_event&ids={target}'
                            + '&stat=1&ver=98&csn=ooca9s&lng=0',
                            {{ credentials: 'include' }}
                        );
                        return await r.json();
                    }} catch(e) {{ return null; }}
                }})()
                """
                data = await page.evaluate(js)
                
                if isinstance(data, dict):
                    all_events = BetcityHandler._extract_all_events(data)
                    parsed = all_events.get(target)
                    if parsed and self._callback:
                        await self._callback(parsed)
                        logger.debug(
                            f"📡 Betcity poll: {target} → "
                            f"sub={parsed.get('sub_score1')}:{parsed.get('sub_score2')}"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Betcity poll error: {e}")

            await asyncio.sleep(1.5)

    # ============================================================
    # Обработка ответа on_air/bets + баланс (пассивный listener)
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
        prev_sub1 = prev_sub2 = 0
        prev_phase_num = 0
        if sc_inter:
            parts = [p.strip() for p in sc_inter.split(',') if p.strip()]
            if parts:
                try:
                    sub1, sub2 = map(int, parts[-1].split(':'))
                except Exception:
                    pass
                # ── ФИКС 2026-10-14: prev_sub для after-goal ──
                # sc_inter = "11:8, 9:11, 7:5" — массив партий.
                # Предпоследний элемент = завершённая партия.
                if len(parts) >= 2:
                    try:
                        a, b = parts[-2].split(':')
                        prev_sub1 = int(a)
                        prev_sub2 = int(b)
                        prev_phase_num = len(parts) - 1
                    except (ValueError, IndexError):
                        pass

        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        # ── ФИКС 2026-10-14: волейбол использует те же market_id,
        #    что и НТ (35/905/913), не 779/787. Значит он должен
        #    идти через _parse_nt_markets, а не _parse_phase_markets.
        #    Подтверждено дампом волейбола: ext[35], ext[905], ext[913]. ──
        if sport_key in ("table_tennis", "volleyball"):
            BetcityHandler._parse_nt_markets(
                match_id, ev_data, set_markets, outcome_ids
            )
        else:
            BetcityHandler._parse_phase_markets(
                match_id, ev_data, set_markets, outcome_ids
            )

        if not set_markets:
            return None

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
            # ── НОВОЕ ──
            "prev_sub_score1": prev_sub1,
            "prev_sub_score2": prev_sub2,
            "prev_phase_num":  prev_phase_num,
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
                    if market_id == _NT_WINNER:
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

                    # ── mid=905: Форы партии ──
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

                    # ── mid=913: ИТ партии ──
                    # Формат ключа: IT{линия}_T{игрок}
                    #   1-е число = НОМЕР ЛИНИИ (1, 2, 3...) — не важно
                    #   2-е число = ИГРОК (1 = хозяин, 2 = гость)
                    # Подтверждено дампом 2026-10-14: три блока
                    # IT1_T1/IT2_T1/IT3_T1 (Tot 6.5/7.5/8.5) все
                    # соответствуют UI «ИТ1 (X.5)» для хозяина.
                    elif market_id == _NT_IT:
                        # Собираем по игроку ТОЛЬКО первую линию
                        it_per_player = {}
                        for it_key, it_block in blocks.items():
                            if not isinstance(it_block, dict):
                                continue
                            if 'Tm' not in it_block or 'Tb' not in it_block:
                                continue
                            m = re.match(r'^IT(\d+)_T(\d+)$', it_key)
                            if not m:
                                continue
                            line_num = int(m.group(1))
                            player = m.group(2)
                            if player not in ('1', '2'):
                                continue
                            if player in it_per_player:
                                continue
                            line = float(it_block.get('Tot', 0) or 0)
                            info_u = it_block['Tm']
                            info_o = it_block['Tb']
                            odd_u = float(info_u.get('kf', 0) or 0)
                            odd_o = float(info_o.get('kf', 0) or 0)
                            ps_u = info_u.get('ps')
                            ps_o = info_o.get('ps')
                            lv_u = info_u.get('lv', line)
                            lv_o = info_o.get('lv', line)
                            if line > 0 and odd_u > 0 and odd_o > 0:
                                it_per_player[player] = {
                                    'line': line,
                                    'odd_u': odd_u, 'odd_o': odd_o,
                                    'ps_u': ps_u, 'ps_o': ps_o,
                                    'lv_u': lv_u, 'lv_o': lv_o,
                                }

                        for player, data in it_per_player.items():
                            it = (set_markets.setdefault(set_key, {})
                                  .setdefault('it', {})
                                  .setdefault(player, {}))
                            it['line'] = data['line']
                            it['over'] = data['odd_o']
                            it['under'] = data['odd_u']
                            o = (outcome_ids.setdefault(set_key, {})
                                 .setdefault('it', {})
                                 .setdefault(player, {}))
                            o['over'] = {
                                'id': match_id, 'pos': data['ps_o'],
                                'kf': data['odd_o'], 'lv': data['lv_o'],
                            }
                            o['under'] = {
                                'id': match_id, 'pos': data['ps_u'],
                                'kf': data['odd_u'], 'lv': data['lv_u'],
                            }

    # ============================================================
    # Фазовые маркеты для волейбола / баскета
    # (Betcity: ext[779]=Исходы по четвертям, ext[787]=ИТ по четвертям)
    # ============================================================
    @staticmethod
    def _parse_phase_markets(match_id: str, ev_data: dict,
                             set_markets: dict, outcome_ids: dict):
        sources = []
        ext = ev_data.get('ext') or {}

        for m_id, m_data in ext.items():
            sources.append((str(m_id), m_data))

        for _mid, m_data in sources:
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

                    # ═══════════════════════════════════════════
                    # market_id 779: Исходы по четвертям (W, F, T, TEO)
                    # ═══════════════════════════════════════════
                    if _mid == _BASKET_WINNER_TOTAL:
                        # ── W (победитель четверти) ──
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

                        # ── F1, F2 (форы четверти, берём первую) ──
                        hk = set_markets.setdefault(set_key, {}) \
                            .setdefault('handicap', {})
                        for f_key in ('F1', 'F2'):
                            f_block = blocks.get(f_key)
                            if not isinstance(f_block, dict):
                                continue
                            if 'Kf_F1' not in f_block or 'Kf_F2' not in f_block:
                                continue
                            for side, lkey, kkey in (
                                ('1', 'F1', 'Kf_F1'),
                                ('2', 'F2', 'Kf_F2'),
                            ):
                                if side in hk:
                                    continue
                                line = float(f_block.get(lkey, 0) or 0)
                                info = f_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', line)
                                if odd > 0:
                                    hk.setdefault(side, {})['line'] = line
                                    hk[side]['odd'] = odd
                                    outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('handicap', {})[side] = {
                                            'id': match_id, 'pos': ps,
                                            'kf': odd, 'lv': lv,
                                        }

                        # ── T1..T5 (тотал четверти, берём ПЕРВЫЙ непустой) ──
                        t = set_markets.setdefault(set_key, {}) \
                            .setdefault('total', {})
                        if 'line' not in t:
                            for t_key in ('T1', 'T2', 'T3', 'T4', 'T5'):
                                t_block = blocks.get(t_key)
                                if not isinstance(t_block, dict):
                                    continue
                                if 'Tm' not in t_block or 'Tb' not in t_block:
                                    continue
                                line = float(t_block.get('Tot', 0) or 0)
                                info_u = t_block['Tm']
                                info_o = t_block['Tb']
                                odd_u = float(info_u.get('kf', 0) or 0)
                                odd_o = float(info_o.get('kf', 0) or 0)
                                ps_u = info_u.get('ps')
                                ps_o = info_o.get('ps')
                                lv_u = info_u.get('lv', line)
                                lv_o = info_o.get('lv', line)
                                if line > 0 and odd_u > 0 and odd_o > 0:
                                    t['line'] = line
                                    t['over'] = odd_o
                                    t['under'] = odd_u
                                    o = outcome_ids.setdefault(set_key, {}) \
                                        .setdefault('total', {})
                                    o['over'] = {
                                        'id': match_id, 'pos': ps_o,
                                        'kf': odd_o, 'lv': lv_o,
                                    }
                                    o['under'] = {
                                        'id': match_id, 'pos': ps_u,
                                        'kf': odd_u, 'lv': lv_u,
                                    }
                                    break

                        # ── TEO (чёт/нечёт четверти) ──
                        teo = blocks.get('TEO') or {}
                        if teo:
                            e_info = teo.get('E') or {}
                            o_info = teo.get('O') or {}
                            odd_e = float(e_info.get('kf', 0) or 0)
                            odd_o = float(o_info.get('kf', 0) or 0)
                            if odd_e > 0 and odd_o > 0:
                                om = set_markets.setdefault(set_key, {}) \
                                    .setdefault('odd', {})
                                om['even'] = odd_e
                                om['odd'] = odd_o
                                oi = outcome_ids.setdefault(set_key, {}) \
                                    .setdefault('odd', {})
                                oi['even'] = {
                                    'id': match_id, 'pos': e_info.get('ps'),
                                    'kf': odd_e,
                                }
                                oi['odd'] = {
                                    'id': match_id, 'pos': o_info.get('ps'),
                                    'kf': odd_o,
                                }
                        continue  # ← 779 обработан

                    # ═══════════════════════════════════════════
                    # market_id 787: ИТ по четвертям
                    #   IT1_T1, IT1_T2 → ИТ игрока 1 (первая линия)
                    #   IT2_T1, IT2_T2 → ИТ игрока 2 (первая линия)
                    # ═══════════════════════════════════════════
                    if _mid == _BASKET_IT:
                        it_per_player = {}
                        for it_key, it_block in blocks.items():
                            if not isinstance(it_block, dict):
                                continue
                            if not it_key.startswith('IT'):
                                continue
                            # ── ФИКС 2026-10-14: IT{линия}_T{игрок} ──
                            m = re.match(r'^IT(\d+)_T(\d+)$', it_key)
                            if not m:
                                continue
                            line_num = int(m.group(1))
                            player = m.group(2)
                            if player not in ('1', '2'):
                                continue
                            # Берём только первую линию на игрока
                            if line_num != 1:
                                continue
                            if player in it_per_player:
                                continue
                            if 'Tm' not in it_block or 'Tb' not in it_block:
                                continue
                            line = float(it_block.get('Tot', 0) or 0)
                            info_u = it_block['Tm']
                            info_o = it_block['Tb']
                            odd_u = float(info_u.get('kf', 0) or 0)
                            odd_o = float(info_o.get('kf', 0) or 0)
                            ps_u = info_u.get('ps')
                            ps_o = info_o.get('ps')
                            lv_u = info_u.get('lv', line)
                            lv_o = info_o.get('lv', line)
                            if line > 0 and odd_u > 0 and odd_o > 0:
                                it_per_player[player] = {
                                    'line': line,
                                    'odd_u': odd_u, 'odd_o': odd_o,
                                    'ps_u': ps_u, 'ps_o': ps_o,
                                    'lv_u': lv_u, 'lv_o': lv_o,
                                }

                        for player, data in it_per_player.items():
                            it = (set_markets.setdefault(set_key, {})
                                  .setdefault('it', {})
                                  .setdefault(player, {}))
                            it['line'] = data['line']
                            it['over'] = data['odd_o']
                            it['under'] = data['odd_u']
                            o = (outcome_ids.setdefault(set_key, {})
                                 .setdefault('it', {})
                                 .setdefault(player, {}))
                            o['over'] = {
                                'id': match_id, 'pos': data['ps_o'],
                                'kf': data['odd_o'], 'lv': data['lv_o'],
                            }
                            o['under'] = {
                                'id': match_id, 'pos': data['ps_u'],
                                'kf': data['odd_u'], 'lv': data['lv_u'],
                            }
                        continue  # ← 787 обработан

                    # ═══════════════════════════════════════════
                    # Резервная ветка — для market_id, которые
                    # не 779 и не 787 (например волейбол, если у него
                    # свои mid). Обработка как в старом коде, но с
                    # фильтром ИТ из total.
                    # ═══════════════════════════════════════════

                    # ── W ──
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

                    # ── F (но НЕ IT-блоки) ──
                    for f_key, f_block in blocks.items():
                        if not isinstance(f_block, dict):
                            continue
                        if f_key.startswith('IT'):
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

                    # ── T (но НЕ IT-блоки, и не перезаписывать уже выбранный) ──
                    for t_key, t_block in blocks.items():
                        if not isinstance(t_block, dict):
                            continue
                        if t_key.startswith('IT'):
                            continue  # ← ФИКС
                        if 'Tm' in t_block and 'Tb' in t_block:
                            t_existing = set_markets.get(set_key, {}).get('total', {})
                            if 'line' in t_existing:
                                continue  # ← ФИКС
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

                    # ── IT (в резервной ветке тоже обрабатываем) ──
                    for it_key, it_block in blocks.items():
                        if not isinstance(it_block, dict):
                            continue
                        if 'Tm' not in it_block or 'Tb' not in it_block:
                            continue
                        if it_key.startswith('IT1'):
                            player = '1'
                        elif it_key.startswith('IT2'):
                            player = '2'
                        else:
                            continue
                        existing_it = (set_markets.get(set_key, {})
                                       .get('it', {})
                                       .get(player, {}))
                        if 'line' in existing_it:
                            continue
                        line = float(it_block.get('Tot', 0) or 0)
                        for side, kkey in (('under', 'Tm'), ('over', 'Tb')):
                            info = it_block[kkey]
                            odd = float(info.get('kf', 0) or 0)
                            ps = info.get('ps')
                            lv = info.get('lv', line)
                            if odd > 0:
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

        js_targeted = f"""
            (async () => {{
                try {{
                    const r = await fetch(
                        'https://ad.betcity.ru/d/on_air/bets'
                        + '?rev=8&add=dep_event&ids={event_id}'
                        + '&stat=1&ver=98&csn=ooca9s&lng=0',
                        {{ credentials: 'include' }}
                    );
                    return await r.json();
                }} catch(e) {{ return null; }}
            }})()
        """
        try:
            data = await page.evaluate(js_targeted)
        except Exception as e:
            logger.debug(f"Betcity refresh (targeted): {e}")
            data = None

        parsed = None
        if isinstance(data, dict):
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

            if market == 'it':
                parts = str(side).split('_', 1)
                if len(parts) == 2:
                    player, direction = parts
                    info = (outcome_ids.get(set_key, {})
                            .get('it', {})
                            .get(player, {})
                            .get(direction))
                    if info and info.get('pos') is not None:
                        return info
            else:
                info = (outcome_ids.get(set_key, {})
                        .get(market, {})
                        .get(side))
                if info and info.get('pos') is not None:
                    return info

        # ── ФИКС: если мы четко искали set_N, но не нашли — отменяем ставку.
        # НЕ ИЩЕМ old_pos в других четвертях (это приведет к ставке на чужую четверть).
        if set_num:
            logger.warning(f"Betcity refresh: {set_key} исчез из линии (переход четверти?)")
            return None

        old_pos = bet_data.get('pos')
        if old_pos is not None:
            for set_key, sets in outcome_ids.items():
                if not isinstance(sets, dict):
                    continue
                for market_name, market_val in sets.items():
                    if not isinstance(market_val, dict):
                        continue
                    if market_name == 'it':
                        for player_name, dirs in market_val.items():
                            if not isinstance(dirs, dict):
                                continue
                            for side_name, info in dirs.items():
                                if not isinstance(info, dict):
                                    continue
                                if str(info.get('pos')) == str(old_pos):
                                    return info
                    else:
                        for side_name, info in market_val.items():
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
        # 1) Refresh pos/kf/lv
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
                f"Betcity: исход исчез из свежего фрейма — отменяю ставку "
                f"(event={bet_data.get('event_id')} pos={bet_data.get('pos')})"
            )
            return {
                "success": False,
                "error": "Betcity: pos_gone_retryable",
                "diag": "pos_gone_retryable",
            }

        # ═══════════════════════════════════════════════════════════
        # GUARD: edge пропал после refresh — отменяем
        # ═══════════════════════════════════════════════════════════
        fast_odd = float(bet_data.get('fast_odd') or 0.0)
        min_edge = float(bet_data.get('min_edge') or 0.05)
        current_kf = float(bet_data.get('kf') or 0.0)

        if fast_odd > 1.01 and current_kf > 1.01:
            edge_now = current_kf / fast_odd - 1.0
            if edge_now < min_edge:
                logger.warning(
                    f"Betcity: ❌ EDGE GONE after refresh — "
                    f"current_kf={current_kf:.2f} / fast_odd={fast_odd:.2f} "
                    f"= {edge_now*100:.1f}% < min_edge={min_edge*100:.1f}% "
                    f"— отменяем"
                )
                return {
                    "success": False,
                    "error": (
                        f"Betcity: edge gone after refresh "
                        f"({edge_now*100:.1f}% < {min_edge*100:.1f}%)"
                    ),
                    "diag": "edge_gone_after_refresh",
                }
            else:
                logger.info(
                    f"Betcity: ✓ edge OK после refresh "
                    f"({edge_now*100:.1f}% ≥ {min_edge*100:.1f}%)"
                )

        logger.warning(
            f"BETCITY BET_REQUEST: event_id={bet_data.get('event_id')} "
            f"pos={bet_data.get('pos')} kf={bet_data.get('kf')} "
            f"lv={bet_data.get('lv')} amount={bet_data.get('amount')}"
        )

        # 2) add → checkout
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

                const nowSec = Math.floor(Date.now() / 1000);
                const tum = Date.now() + '_' + Math.floor(Math.random() * 1000000);

                const addSettings = {{
                    remember_bet: true,
                    clear_cart: true,
                    auto_vip_tg: false,
                    fsum_1: 100,
                    fsum_2: 500,
                    bets_kf_type: 'koeff',
                    show_warning: false,
                    cart_kf_type: '6'
                }};

                let addUrl = 'https://hdr.betcity.ru/d/basket/add'
                    + '?sys=1'
                    + '&id=' + encodeURIComponent(data.event_id)
                    + '&pos=' + encodeURIComponent(data.pos)
                    + '&k=' + encodeURIComponent(data.kf)
                    + '&ts=' + nowSec
                    + '&is_live=1';
                if (data.lv !== undefined && data.lv !== null && Number(data.lv) > 0) {{
                    addUrl += '&lv=' + encodeURIComponent(data.lv);
                }}
                addUrl += '&token=' + encodeURIComponent(token)
                       + '&tum=' + tum
                       + '&ver=96'
                       + '&csn=ooca9s';

                const addBody = 'cart=%7B%7D&settings='
                    + encodeURIComponent(JSON.stringify(addSettings));

                const addResp = await fetch(addUrl, {{
                    method: 'POST',
                    headers: {{
                        'accept': 'application/json, text/plain, */*',
                        'content-type': 'application/x-www-form-urlencoded'
                    }},
                    credentials: 'include',
                    referrer: 'https://betcity.ru/',
                    body: addBody
                }});
                const addResult = await addResp.json();

                if (!addResult.ok
                    || !addResult.reply
                    || !addResult.reply.bsks
                    || !addResult.reply.bsks[0]) {{
                    return {{
                        success: false,
                        error: 'Betcity basket/add: '
                            + JSON.stringify(addResult).slice(0, 500)
                    }};
                }}

                const bsk = addResult.reply.bsks[0];
                const serverTs = addResult.reply.ts;

                // ── ФИКС: проверяем, на ту ли четверть Betcity добавил исход ──
                if (bsk.num_per !== undefined && Number(bsk.num_per) !== Number(data.set_num)) {{
                    console.log('BETCITY_SET_CHANGED_ON_ADD', 'sent=' + data.set_num + ' got=' + bsk.num_per);
                    return {{ success: false, error: 'Betcity: смена четверти при add (' + data.set_num + ' -> ' + bsk.num_per + ')', diag: 'set_changed' }};
                }}

                // ── УМНАЯ ПРОВЕРКА (2026-11-04) ──
                // Если кэф изменился, проверяем, остался ли еще валуй
                // (edge) выше нашего минимального порога. Если да —
                // это всё еще выгодная ставка, берем её. Если нет — отменяем.
                const _fastOdd = Number(data.fast_odd || 0);
                const _minEdge = Number(data.min_edge || 0.05);
                const _freshKf = Number(bsk.kf || 0);

                if (_fastOdd > 1.01 && _freshKf > 1.01) {{
                    const _edgeNow = _freshKf / _fastOdd - 1.0;
                    if (_edgeNow < _minEdge) {{
                        console.log('BETCITY_EDGE_GONE', 
                                    'orig=' + data.kf + 
                                    ' fresh=' + _freshKf + 
                                    ' edge=' + (_edgeNow * 100).toFixed(1) + '%');
                        return {{ 
                            success: false, 
                            error: 'Betcity: edge gone (' + 
                                   (_edgeNow * 100).toFixed(1) + '% < ' + 
                                   (_minEdge * 100).toFixed(1) + '%)',
                            diag: 'edge_gone'
                        }};
                    }}
                }}

                const freshPos = bsk.id_pos || data.pos;
                const freshKf  = bsk.kf    || data.kf;
                const itemKey  = String(data.event_id) + '_' + String(freshPos);

                const itemData = {{
                    id_ev: bsk.id_ev || data.event_id,
                    ps: freshPos,
                    kf: freshKf,
                    is_live: 1,
                    t: bsk.t,
                    s: bsk.s
                }};
                if (bsk.lv !== undefined && bsk.lv !== null && Number(bsk.lv) > 0) {{
                    itemData.fora = String(bsk.lv);
                }}

                const checkoutData = {{}};
                checkoutData[itemKey] = itemData;

                const checkoutSettings = {{
                    remember_bet: true,
                    clear_cart: false,
                    show_warning: true,
                    auto_vip_tg: false,
                    bets_kf_type: 'koeff',
                    cart_kf_type: 2,
                    fsum_1: 100,
                    fsum_2: 500,
                    fsum_3: 1000,
                    show_rec_sum: false
                }};

                const maxBet = bsk.max || 100000;
                const checkoutContext = {{
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

                const checkoutBody = [
                    'data=' + encodeURIComponent(JSON.stringify(checkoutData)),
                    'settings=' + encodeURIComponent(JSON.stringify(checkoutSettings)),
                    'context=' + encodeURIComponent(JSON.stringify(checkoutContext)),
                    'uuid=' + encodeURIComponent(uuid),
                    'bets[' + itemKey + ']=' + encodeURIComponent(data.amount)
                ].join('&');

                const checkoutUrl = 'https://hdr.betcity.ru/d/basket/checkout'
                    + '?type=2'
                    + '&ts=' + encodeURIComponent(serverTs)
                    + '&token=' + encodeURIComponent(token)
                    + '&tum=' + tum
                    + '&ver=96'
                    + '&csn=ooca9s';

                const checkoutResp = await fetch(checkoutUrl, {{
                    method: 'POST',
                    headers: {{
                        'accept': 'application/json, text/plain, */*',
                        'content-type': 'application/x-www-form-urlencoded'
                    }},
                    credentials: 'include',
                    referrer: 'https://betcity.ru/',
                    body: checkoutBody
                }});
                const checkoutResult = await checkoutResp.json();

                if (checkoutResult.ok
                    && checkoutResult.reply
                    && checkoutResult.reply.status === 0
                    && checkoutResult.reply.bsks_out_bets
                    && checkoutResult.reply.bsks_out_bets.length > 0) {{
                    const info = checkoutResult.reply.bsks_out_bets[0];
                    
                    // ── ФИКС: проверяем, на ту ли четверть Betcity принял ставку ──
                    if (info.num_per !== undefined && Number(info.num_per) !== Number(data.set_num)) {{
                        console.log('BETCITY_SET_CHANGED_ON_CHECKOUT', 'sent=' + data.set_num + ' got=' + info.num_per);
                        return {{ success: false, error: 'Betcity: смена четверти при checkout (' + data.set_num + ' -> ' + info.num_per + ')', diag: 'set_changed' }};
                    }}
                    
                    // ── ФИКС 2026-10-30: ФИНАЛЬНАЯ проверка кэфа ──
                    // Даже если add вернул старый кэф, checkout мог принять новый.
                    // Проверяем реальный кэф из bsks_out_bets.
                    const acceptedKf = info.kf || info.kf_real || 0;
                    if (acceptedKf > 0 && Number(acceptedKf) < Number(data.kf) - 0.01) {{
                        console.log('BETCITY_CHECKOUT_ODDS_DROPPED', 'sent=' + data.kf + ' accepted=' + acceptedKf);
                        return {{
                            success: false,
                            error: 'Betcity: checkout odds dropped ' + data.kf + ' -> ' + acceptedKf,
                            diag: 'odds_dropped'
                        }};
                    }}
                    
                    return {{
                        success: true,
                        betId: info.ids || info.item || 'N/A',
                        accepted_odds: Number(acceptedKf || data.kf)
                    }};
                }}

                return {{
                    success: false,
                    error: 'Betcity checkout: '
                        + JSON.stringify(checkoutResult).slice(0, 500)
                }};
            }} catch(e) {{
                return {{ success: false, error: 'network: ' + e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)