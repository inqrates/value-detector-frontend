# ui/after_goal/betcity.py
"""
Betcity handler с поддержкой мультиспорта:
  - Настольный теннис (sport=46)
  - Волейбол          (sport=12)
  - Баскетбол         (sport=3, is_cyber=0)
  - Кибербаскетбол    (sport=3, is_cyber=1)

Структура on_air/bets — общая для всех видов:
  reply.sports["<id>"].chmps[].evts[] — события
  event.sc_ev  — общий счёт
  event.sc_inter — счёт по фазам
  event.ext    — дополнительные рынки (победа/фора/тотал по партиям/четвертям)
  event.main   — основные матчевые рынки

Для НТ: конкретные ID маркетов (35=победы, 905=форы, 913=ИТ).
Для волейбола/баскетбола: ищем маркеты по форме — у них внутри `rows` с
`num_per` (номер партии/четверти). Так парсер не зависит от того, какой
именно market_id используется под конкретный вид.
"""
import json
import logging
import re
from typing import Optional, Dict, Any, List
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


# Спорт-ID Betcity → наш sport_key
_SPORT_IDS = {
    "46": "table_tennis",
    "12": "volleyball",
    "3":  "basketball",   # уточняется is_cyber → cyber_basketball
}

# НТ: явные ID маркетов (структура плоская, без rows)
_NT_WINNER   = "35"    # победы по партиям
_NT_HANDICAP = "905"   # форы по партиям
_NT_IT       = "913"   # индивидуальные тоталы


class BetcityHandler(BookmakerHandler):
    _callback = None
    _page = None

    # ============================================================
    # Перехват
    # ============================================================
    @staticmethod
    async def setup_listener(page: Page, callback):
        BetcityHandler._page = page
        BetcityHandler._callback = callback
        page.on("response", BetcityHandler._on_response)
        logger.info("Betcity: перехват установлен")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", BetcityHandler._on_response)
        except Exception:
            pass
        BetcityHandler._callback = None
        logger.info("Betcity: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if 'on_air/bets' not in url:
            return
        try:
            data = await response.json()
            parsed = BetcityHandler.parse_update(data)
            if parsed and BetcityHandler._callback:
                await BetcityHandler._callback(parsed)
        except Exception as e:
            logger.error(f"Betcity ошибка: {e}")

    # ============================================================
    # Парсер on_air/bets — мультиспорт
    # ============================================================
    @staticmethod
    def parse_update(data: dict) -> Optional[dict]:
        reply = data.get('reply') or {}
        sports = reply.get('sports') or {}
        if not sports:
            return None

        # Проходим по всем видам спорта, которые есть в ответе
        for sport_id_str, sport_block in sports.items():
            sport_key = _SPORT_IDS.get(str(sport_id_str))
            if not sport_key:
                continue

            championships = sport_block.get('chmps') or {}
            for chmp_id, chmp_data in championships.items():
                is_cyber = chmp_data.get('is_cyber', 0)
                effective_sport = sport_key
                if sport_key == "basketball" and is_cyber == 1:
                    effective_sport = "cyber_basketball"

                events = chmp_data.get('evts') or {}
                for ev_id, ev_data in events.items():
                    if ev_data.get('team_type_f', 0) != 0:
                        continue
                    if ev_data.get('is_dep', 0) == 1:
                        continue

                    parsed = BetcityHandler._parse_event(
                        str(ev_id), ev_data, effective_sport
                    )
                    if parsed:
                        return parsed  # берём первое событие из ответа
        return None

    # ============================================================
    # Разбор одного события
    # ============================================================
    @staticmethod
    def _parse_event(match_id: str, ev_data: dict, sport_key: str) -> Optional[dict]:
        # --- Игроки ---
        player1 = ev_data.get('name_ht', '') or ''
        player2 = ev_data.get('name_at', '') or ''

        # --- Общий счёт ---
        score_str = ev_data.get('sc_ev', '0:0') or '0:0'
        try:
            score1, score2 = map(int, score_str.split(':'))
        except Exception:
            score1 = score2 = 0

        # --- Счёт по фазам ---
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

        # ---- НТ: явные ID маркетов ----
        if sport_key == "table_tennis":
            BetcityHandler._parse_nt_markets(
                match_id, ev_data, set_markets, outcome_ids
            )

        # ---- Волейбол / Баскетбол / Кибер: универсальный обход ----
        else:
            BetcityHandler._parse_phase_markets(
                match_id, ev_data, set_markets, outcome_ids
            )

        if not set_markets:
            return None

        return {
            "match_id": match_id,
            "player1": player1,
            "player2": player2,
            "sport": sport_key,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    # ============================================================
    # НТ-маркеты (сохранена старая логика)
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
                    continue
                set_num = mm.group(1)
                set_key = f"set_{set_num}"
                data_block = row.get('data') or {}
                for block_key, block in data_block.items():
                    blocks = block.get('blocks') or {}

                    # Победы в партии
                    if market_id == _NT_WINNER:
                        w = blocks.get('W') or {}
                        for side, key in (('1', 'P1'), ('2', 'P2')):
                            if key in w:
                                info = w[key]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = odd
                                    outcome_ids.setdefault(set_key, {}).setdefault('winner', {})[side] = {
                                        'id': match_id, 'pos': ps, 'kf': odd,
                                    }

                    # Формы
                    elif market_id == _NT_HANDICAP:
                        for f_key, f_block in blocks.items():
                            if not f_key.startswith('F'):
                                continue
                            if 'Kf_F1' not in f_block or 'Kf_F2' not in f_block:
                                continue
                            for side, lkey, kkey in (('1', 'F1', 'Kf_F1'), ('2', 'F2', 'Kf_F2')):
                                line = float(f_block.get(lkey, 0) or 0)
                                info = f_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', 0)
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})
                                    set_markets[set_key]['handicap'][side]['line'] = line
                                    set_markets[set_key]['handicap'][side]['odd'] = odd
                                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {})[side] = {
                                        'id': match_id, 'pos': ps, 'kf': odd, 'lv': lv,
                                    }

                    # ИТ
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
                                    player = '1' if '_T1' in it_key or 'IT1_T1' in it_key else '2'
                                    set_markets.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {})[side] = odd
                                    set_markets[set_key]['it'][player]['line'] = line
                                    outcome_ids.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {}).setdefault(side, {})
                                    outcome_ids[set_key]['it'][player][side] = {
                                        'id': match_id, 'pos': ps, 'kf': odd, 'lv': lv,
                                    }

    # ============================================================
    # Волейбол / Баскетбол / Кибер — универсальный обход
    # ============================================================
    @staticmethod
    def _parse_phase_markets(match_id: str, ev_data: dict,
                             set_markets: dict, outcome_ids: dict):
        """
        Ищем в ext и main все маркеты, у которых внутри rows есть num_per.
        Из каждого блока извлекаем Форму (F1/F2), Тотал (T), ИТ (IT).
        """
        sources = []
        ext = ev_data.get('ext') or {}
        main = ev_data.get('main') or {}
        for src in (ext, main):
            for m_id, m_data in src.items():
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

                    # --- Победа (если есть в виде W с P1/P2) ---
                    w = blocks.get('W') or {}
                    for side, k in (('1', 'P1'), ('2', 'P2')):
                        if k in w:
                            info = w[k]
                            odd = float(info.get('kf', 0) or 0)
                            ps = info.get('ps')
                            if odd > 0:
                                set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = odd
                                outcome_ids.setdefault(set_key, {}).setdefault('winner', {})[side] = {
                                    'id': match_id, 'pos': ps, 'kf': odd,
                                }

                    # --- Фора ---
                    for f_key, f_block in blocks.items():
                        if not isinstance(f_block, dict):
                            continue
                        if 'Kf_F1' in f_block and 'Kf_F2' in f_block:
                            for side, lkey, kkey in (('1', 'F1', 'Kf_F1'), ('2', 'F2', 'Kf_F2')):
                                line = float(f_block.get(lkey, 0) or 0)
                                info = f_block[kkey]
                                odd = float(info.get('kf', 0) or 0)
                                ps = info.get('ps')
                                lv = info.get('lv', 0)
                                if odd > 0:
                                    set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault(side, {})
                                    set_markets[set_key]['handicap'][side]['line'] = line
                                    set_markets[set_key]['handicap'][side]['odd'] = odd
                                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {})[side] = {
                                        'id': match_id, 'pos': ps, 'kf': odd, 'lv': lv,
                                    }

                    # --- Тотал ---
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
                                    set_markets.setdefault(set_key, {}).setdefault('total', {})['line'] = line
                                    set_markets[set_key]['total'][side] = odd
                                    outcome_ids.setdefault(set_key, {}).setdefault('total', {})[side] = {
                                        'id': match_id, 'pos': ps, 'kf': odd, 'lv': lv,
                                    }

                    # --- ИТ ---
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
                                # определить игрока по ключу it_key
                                if 'IT1_T1' in it_key or '_T1' in it_key:
                                    player = '1'
                                elif 'IT1_T2' in it_key or '_T2' in it_key:
                                    player = '2'
                                else:
                                    continue
                                set_markets.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {})[side] = odd
                                set_markets[set_key]['it'][player]['line'] = line
                                outcome_ids.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {}).setdefault(side, {})
                                outcome_ids[set_key]['it'][player][side] = {
                                    'id': match_id, 'pos': ps, 'kf': odd, 'lv': lv,
                                }

    # ============================================================
    # Ставка (без изменений)
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """
        bet_data:
          - event_id, pos, kf, lv, amount
        """
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
                    return {{ success: false, error: 'Betcity add failed: ' + JSON.stringify(addResult) }};
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
                    return {{ success: true, betId: info.ids || info.item || 'N/A' }};
                }}

                return {{ success: false, error: 'Betcity checkout failed: ' + JSON.stringify(checkoutResult) }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)