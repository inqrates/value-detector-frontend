# ui/after_goal/betcity.py
import json
import logging
import re
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


class BetcityHandler(BookmakerHandler):
    _callback = None
    _page = None

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
        if 'on_air/bets' in url:
            try:
                data = await response.json()
                parsed = BetcityHandler.parse_update(data)
                if parsed and BetcityHandler._callback:
                    await BetcityHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Betcity ошибка: {e}")

    @staticmethod
    def parse_update(data: dict) -> dict:
        """
        Извлекает из ответа on_air/bets:
          - счёт, суб-счёт
          - внутрисетовые рынки (set_markets)
          - идентификаторы исходов (outcome_ids) с pos (ps), lv, kf, id_ev
        """
        reply = data.get('reply', {})
        sports = reply.get('sports', {})
        tt_sport = sports.get('46')
        if not tt_sport:
            return None

        championships = tt_sport.get('chmps', {})
        for chmp_id, chmp_data in championships.items():
            events = chmp_data.get('evts', {})
            for ev_id, ev_data in events.items():
                match_id = ev_id
                # Счёт
                score_str = ev_data.get('sc_ev', '0:0')
                try:
                    score1, score2 = map(int, score_str.split(':'))
                except Exception:
                    score1, score2 = 0, 0

                # Сеты
                sc_inter = ev_data.get('sc_inter', '')
                sub1, sub2 = 0, 0
                if sc_inter:
                    parts = sc_inter.split(',')
                    if parts:
                        last_set = parts[-1].strip()
                        try:
                            sub1, sub2 = map(int, last_set.split(':'))
                        except Exception:
                            pass

                player1 = ev_data.get('name_ht', '')
                player2 = ev_data.get('name_at', '')

                ext = ev_data.get('ext', {})
                set_markets = {}
                outcome_ids = {}

                # ---- Парсинг рынков в ext ----
                # Рынки: 35 – победы по партиям, 905 – форы по партиям, 913 – ИТ по партиям
                for market_id, market_data in ext.items():
                    if market_id not in ('35', '905', '913'):
                        continue
                    rows = market_data.get('rows', {})
                    for row_id, row in rows.items():
                        row_name = row.get('name', '')
                        match = re.search(r'(\d+)-я партия', row_name)
                        if not match:
                            continue
                        set_num = match.group(1)
                        set_key = f"set_{set_num}"
                        data_block = row.get('data', {})
                        for block_key, block in data_block.items():
                            blocks = block.get('blocks', {})

                            # --- Победы в партии (market_id=35) ---
                            if market_id == '35':
                                w_block = blocks.get('W', {})
                                if 'P1' in w_block:
                                    p1 = w_block['P1']
                                    odd = float(p1.get('kf', 0))
                                    ps = p1.get('ps')
                                    set_markets.setdefault(set_key, {}).setdefault('winner', {})['1'] = odd
                                    # ✅ ИСПРАВЛЕНО
                                    outcome_ids.setdefault(set_key, {}).setdefault('winner', {})['1'] = {
                                        'id': ev_id,
                                        'pos': ps,
                                        'kf': odd,
                                    }
                                if 'P2' in w_block:
                                    p2 = w_block['P2']
                                    odd = float(p2.get('kf', 0))
                                    ps = p2.get('ps')
                                    set_markets.setdefault(set_key, {}).setdefault('winner', {})['2'] = odd
                                    # ✅ ИСПРАВЛЕНО
                                    outcome_ids.setdefault(set_key, {}).setdefault('winner', {})['2'] = {
                                        'id': ev_id,
                                        'pos': ps,
                                        'kf': odd,
                                    }

                            # --- Форы по партиям (market_id=905) ---
                            elif market_id == '905':
                                for f_key, f_block in blocks.items():
                                    if f_key.startswith('F') and 'Kf_F1' in f_block and 'Kf_F2' in f_block:
                                        line1 = float(f_block.get('F1', 0))
                                        odd1 = float(f_block['Kf_F1'].get('kf', 0))
                                        ps1 = f_block['Kf_F1'].get('ps')
                                        lv1 = f_block['Kf_F1'].get('lv', 0)

                                        line2 = float(f_block.get('F2', 0))
                                        odd2 = float(f_block['Kf_F2'].get('kf', 0))
                                        ps2 = f_block['Kf_F2'].get('ps')
                                        lv2 = f_block['Kf_F2'].get('lv', 0)

                                        # set_markets
                                        set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('1', {})
                                        set_markets[set_key]['handicap']['1']['line'] = line1
                                        set_markets[set_key]['handicap']['1']['odd'] = odd1

                                        set_markets.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('2', {})
                                        set_markets[set_key]['handicap']['2']['line'] = line2
                                        set_markets[set_key]['handicap']['2']['odd'] = odd2

                                        # outcome_ids
                                        outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('1', {})
                                        outcome_ids[set_key]['handicap']['1'] = {
                                            'id': ev_id,
                                            'pos': ps1,
                                            'kf': odd1,
                                            'lv': lv1,
                                        }
                                        outcome_ids.setdefault(set_key, {}).setdefault('handicap', {}).setdefault('2', {})
                                        outcome_ids[set_key]['handicap']['2'] = {
                                            'id': ev_id,
                                            'pos': ps2,
                                            'kf': odd2,
                                            'lv': lv2,
                                        }

                            # --- Индивидуальные тоталы (market_id=913) ---
                            elif market_id == '913':
                                for it_key, it_block in blocks.items():
                                    if 'Tm' in it_block and 'Tb' in it_block:
                                        line = float(it_block.get('Tot', 0))
                                        odd_under = float(it_block['Tm'].get('kf', 0))
                                        ps_under = it_block['Tm'].get('ps')
                                        lv_under = it_block['Tm'].get('lv', line)
                                        odd_over = float(it_block['Tb'].get('kf', 0))
                                        ps_over = it_block['Tb'].get('ps')
                                        lv_over = it_block['Tb'].get('lv', line)

                                        if 'IT1_T1' in it_key:
                                            player = '1'
                                        elif 'IT1_T2' in it_key:
                                            player = '2'
                                        else:
                                            if '_T1' in it_key:
                                                player = '1'
                                            elif '_T2' in it_key:
                                                player = '2'
                                            else:
                                                player = 'unknown'

                                        if player in ('1', '2'):
                                            set_markets.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {})
                                            set_markets[set_key]['it'][player]['line'] = line
                                            set_markets[set_key]['it'][player]['over'] = odd_over
                                            set_markets[set_key]['it'][player]['under'] = odd_under

                                            outcome_ids.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {}).setdefault('over', {})
                                            outcome_ids[set_key]['it'][player]['over'] = {
                                                'id': ev_id,
                                                'pos': ps_over,
                                                'kf': odd_over,
                                                'lv': lv_over,
                                            }
                                            outcome_ids.setdefault(set_key, {}).setdefault('it', {}).setdefault(player, {}).setdefault('under', {})
                                            outcome_ids[set_key]['it'][player]['under'] = {
                                                'id': ev_id,
                                                'pos': ps_under,
                                                'kf': odd_under,
                                                'lv': lv_under,
                                            }

                return {
                    "match_id": match_id,
                    "player1": player1,
                    "player2": player2,
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
        """
        bet_data ожидает:
          - event_id (id_ev)
          - pos (ps)
          - kf (коэффициент)
          - lv (линия, опционально)
          - token (из куки tk)
          - amount (сумма ставки)
        """
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};
            try {{
                let addUrl = `https://hdr.betcity.ru/d/basket/add?sys=1&id=${{data.event_id}}&pos=${{data.pos}}&k=${{data.kf}}&ts=0&is_live=1`;
                if (data.lv !== undefined && data.lv !== null) {{
                    addUrl += `&lv=${{data.lv}}`;
                }}
                addUrl += `&token=${{data.token}}&tum=${{Date.now()}}_221925&ver=88&csn=ooca9s`;
                const addResp = await fetch(addUrl, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://betcity.ru',
                        'Referer': 'https://betcity.ru/'
                    }},
                    body: `cart=%7B%7D&settings=%7B%22remember_bet%22%3Atrue%2C%22clear_cart%22%3Atrue%2C%22auto_vip_tg%22%3Afalse%2C%22fsum_1%22%3A100%2C%22fsum_2%22%3A500%2C%22bets_kf_type%22%3A%22koeff%22%2C%22show_warning%22%3Afalse%2C%22cart_kf_type%22%3A%226%22%7D`
                }});
                const addResult = await addResp.json();
                if (!addResult.ok) throw new Error('Basket add failed: ' + JSON.stringify(addResult));
                const ts = addResult.reply.ts;

                const checkoutUrl = `https://hdr.betcity.ru/d/basket/checkout?type=6&ts=${{ts}}&token=${{data.token}}&tum=${{Date.now()}}_221925&ver=88&csn=ooca9s`;
                const checkoutResp = await fetch(checkoutUrl, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://betcity.ru',
                        'Referer': 'https://betcity.ru/'
                    }},
                    body: `data=%7B%22${{data.event_id}}_${{data.pos}}%22%3A%7B%22id_ev%22%3A${{data.event_id}}%2C%22ps%22%3A${{data.pos}}%2C%22kf%22%3A${{data.kf}}%2C%22is_live%22%3A1%2C%22t%22%3A%22${{ts}}.159850%22%2C%22s%22%3A%220BK96cqToL5ZXylw7JpKSg%3D%3D%22%7D%7D&settings=%7B%22remember_bet%22%3Atrue%2C%22clear_cart%22%3Afalse%2C%22show_warning%22%3Afalse%2C%22auto_vip_tg%22%3Afalse%2C%22bets_kf_type%22%3A%22koeff%22%2C%22cart_kf_type%22%3A6%2C%22fsum_1%22%3A100%2C%22fsum_2%22%3A500%2C%22fsum_3%22%3A1000%2C%22show_rec_sum%22%3Afalse%7D&context=%7B%22api_method%22%3A%22checkout%22%2C%22max_bet%22%3A19.4%2C%22shown_balance%22%3A19.4%2C%22max_vip_bet%22%3A19.4%2C%22rec_sum%22%3A-1%2C%22scr_orientation%22%3A%22horizontal%22%7D&uuid=01a086dc-cd7e-734d-8a47-8580bcab8872&bets[${{data.event_id}}_${{data.pos}}]=${{data.amount}}`
                }});
                const checkoutResult = await checkoutResp.json();
                if (checkoutResult.ok && checkoutResult.reply.status === 0) {{
                    return {{ success: true, betId: checkoutResult.reply.bsks_out_bets?.[0]?.ids || 'N/A' }};
                }} else {{
                    throw new Error('Checkout failed: ' + JSON.stringify(checkoutResult));
                }}
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)