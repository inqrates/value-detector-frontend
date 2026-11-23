# ui/after_goal/decisions.py
"""
Чистые предикаты live-value и сбор подтверждённых исходов.

Поведение 1:1 из ui/after_goal_engine.py
(_fast_favors, _is_outcome_dead, _collect_confirmed_bets,
 _collect_prev_phase_bets, _filter_confirmed_by_strategy).
Без self, без I/O, без состояния — чисто арифметика счёта партии
и работа с переданными рынками.
"""
import logging
import re
from typing import List, Optional

from . import sports

logger = logging.getLogger(__name__)


def fast_favors(market: str, side: str, line: float,
                fast_sub1: int, fast_sub2: int,
                phase_done: bool,
                sport: str = "table_tennis",
                set_number: int = 1) -> bool:
    """
    True, если fast уже показывает, что наш исход выигрывает по счёту.
    Используется в live-value, чтобы не ставить «лотерейные» value,
    где кэф у slow просто устарел, но fast не подтверждает наш исход.

    Логика по рынкам:
      winner 1/2   — тот, у кого больше очков на fast
      total over   — fast-сумма уже >= line
      total under  — fast-сумма + max-возможное-до-конца < line
      it_over      — fast-очки игрока >= line
      it_under     — fast-очки игрока + max < line
      handicap     — margin + line > 0 на fast
      odd/even     — fast_total чётный/нечётный И партия завершена
      point        — не гарантируется, всегда False
    """
    try:
        if market == 'winner':
            if side == '1':
                return fast_sub1 > fast_sub2
            else:
                return fast_sub2 > fast_sub1

        if market == 'total':
            total = fast_sub1 + fast_sub2
            if side == 'over':
                if total >= line:
                    return True
                if phase_done:
                    return total >= line
                # оценим минимально-возможный финал
                if sport == 'table_tennis':
                    return (11 + min(fast_sub1, fast_sub2)) >= line
                if sport in ('volleyball', 'beach_volleyball'):
                    thr = 15 if set_number >= 5 else 25
                    return (thr + min(fast_sub1, fast_sub2)) >= line
                return False
            else:  # under
                if phase_done:
                    return total < line
                return False

        if market == 'it':
            player, direction = side.split('_', 1)
            ind = fast_sub1 if player == '1' else fast_sub2
            if direction == 'over':
                return ind >= line
            else:  # under
                if phase_done:
                    return ind < line
                return False

        if market == 'handicap':
            margin = fast_sub1 - fast_sub2 if side == '1' \
                else fast_sub2 - fast_sub1
            if phase_done:
                return (margin + line) > 0
            # партия идёт — не гарантируем
            return False

        if market == 'odd':
            if not phase_done:
                return False
            total = fast_sub1 + fast_sub2
            if side == 'odd':
                return total % 2 == 1
            return total % 2 == 0

        if market == 'point':
            # следующее очко не гарантируется
            return False

    except Exception:
        return False
    return False


def is_outcome_dead(market: str, side: str, line: float,
                    fast_sub1: int, fast_sub2: int,
                    phase_done: bool) -> bool:
    """Исход УЖЕ проиграл на fast? (ставить нельзя)."""
    try:
        if market == 'it':
            player, direction = str(side).split('_', 1)
            ind = fast_sub1 if player == '1' else fast_sub2
            if direction == 'under':
                if ind >= line:
                    return True
                if phase_done and ind >= line:
                    return True
            else:  # over
                if phase_done and ind < line:
                    return True
            return False

        if market == 'total':
            total = fast_sub1 + fast_sub2
            if side == 'under':
                if total >= line:
                    return True
                if phase_done and total >= line:
                    return True
            else:  # over
                if phase_done and total < line:
                    return True
            return False

        if market == 'handicap':
            margin = fast_sub1 - fast_sub2 if side == '1' \
                else fast_sub2 - fast_sub1
            if phase_done and (margin + line) <= 0:
                return True
            return False

        if market == 'winner':
            if phase_done:
                winner = '1' if fast_sub1 > fast_sub2 else '2'
                return side != winner
            return False

    except Exception:
        return False
    return False


def collect_confirmed_bets(sport, payload, markets, set_number,
                           fast_sub, fast_score, slow_score):
    """Подтверждённые исходы по завершённой/идущей партии (послегол)."""
    confirmed = []
    a = fast_sub[0] if fast_sub else 0
    b = fast_sub[1] if fast_sub else 0

    fast_phase_for_done = sports.parse_phase_num(payload.get('fast_phase', ''))
    fast_set_done = sports.is_set_done(sport, a, b, set_number, fast_phase_for_done)

    fast_total = a + b
    total = markets.get('total', {}) or {}
    line = total.get('line', 0) or 0
    it_markets = markets.get('it', {}) or {}

    total_sane = line > 0 and sports.is_phase_line_sane(
        sport, 'total', line, set_number
    )

    odd_over = total.get('over', 0)
    if odd_over > 0 and total_sane and fast_total > line:
        confirmed.append((
            'total', 'over', odd_over,
            f'set {set_number}: {fast_total} > {line} (100%)',
            set_number,
        ))

    for player in ('1', '2'):
        p_market = it_markets.get(player, {}) or {}
        if not p_market:
            continue
        line_it = p_market.get('line', 0) or 0
        odd_it_over = p_market.get('over', 0)
        ind = a if player == '1' else b
        if (odd_it_over > 0 and line_it > 0
                and sports.is_phase_line_sane(sport, 'it', line_it, set_number)
                and ind > line_it):
            confirmed.append((
                'it', f'{player}_over', odd_it_over,
                f'set {set_number}: ИТ{player} {ind} > {line_it} (100%)',
                set_number,
            ))

    if not fast_set_done:
        return confirmed

    if total_sane and fast_total < line:
        odd_under = total.get('under', 0)
        if odd_under > 0:
            confirmed.append((
                'total', 'under', odd_under,
                f'set {set_number} done: {fast_total} < {line} (100%)',
                set_number,
            ))

    for player in ('1', '2'):
        p_market = it_markets.get(player, {}) or {}
        if not p_market:
            continue
        line_it = p_market.get('line', 0) or 0
        odd_it_under = p_market.get('under', 0)
        ind = a if player == '1' else b
        if (odd_it_under > 0 and line_it > 0
                and sports.is_phase_line_sane(sport, 'it', line_it, set_number)
                and ind < line_it):
            confirmed.append((
                'it', f'{player}_under', odd_it_under,
                f'set {set_number} done: ИТ{player} {ind} < {line_it} (100%)',
                set_number,
            ))

    winner_side = None
    if a > b:
        winner_side = '1'
    elif b > a:
        winner_side = '2'
    if winner_side:
        w = markets.get('winner', {}) or {}
        odd = w.get(winner_side, 0)
        if odd > 0:
            confirmed.append((
                'winner', winner_side, odd,
                f'set {set_number} done {a}:{b} (100%)',
                set_number,
            ))

    margin1 = a - b
    margin2 = b - a
    h = markets.get('handicap', {}) or {}
    for side, margin in (('1', margin1), ('2', margin2)):
        h_side = h.get(side, {}) or {}
        if not h_side:
            continue
        line_h = h_side.get('line', 0) or 0
        odd_h = h_side.get('odd', 0)
        if (odd_h > 0
                and sports.is_phase_line_sane(sport, 'handicap', line_h, set_number)
                and (margin + line_h) > 0):
            confirmed.append((
                'handicap', side, odd_h,
                f'set {set_number} done: П{side} margin '
                f'{margin} + line {line_h} (100%)',
                set_number,
            ))

    return confirmed


def collect_prev_phase_bets(sport, payload, set_markets,
                            fast_prev_sub: list, prev_phase_name: str):
    """Исходы завершившейся предыдущей партии (fast ушёл вперёд)."""
    if not prev_phase_name:
        return []
    m = re.search(r'(\d+)', prev_phase_name)
    if not m:
        return []
    prev_num = int(m.group(1))

    prev_s1 = fast_prev_sub[0] if fast_prev_sub else 0
    prev_s2 = fast_prev_sub[1] if fast_prev_sub else 0
    if prev_s1 == 0 and prev_s2 == 0:
        return []

    set_key = f"set_{prev_num}"
    markets = set_markets.get(set_key, {})
    if not markets:
        return []

    confirmed = []
    prev_total = prev_s1 + prev_s2

    total = markets.get('total', {}) or {}
    line = total.get('line', 0) or 0
    if line > 0 and sports.is_phase_line_sane(sport, 'total', line, prev_num):
        if prev_total > line:
            odd = total.get('over', 0)
            if odd > 0:
                confirmed.append((
                    'total', 'over', odd,
                    f'prev {prev_phase_name}: {prev_total} > {line}',
                    prev_num,
                ))
        elif prev_total < line:
            odd = total.get('under', 0)
            if odd > 0:
                confirmed.append((
                    'total', 'under', odd,
                    f'prev {prev_phase_name}: {prev_total} < {line}',
                    prev_num,
                ))

    it_markets = markets.get('it', {}) or {}
    for player in ('1', '2'):
        p_market = it_markets.get(player, {}) or {}
        if not p_market:
            continue
        line_it = p_market.get('line', 0) or 0
        ind = prev_s1 if player == '1' else prev_s2
        if line_it <= 0:
            continue
        if not sports.is_phase_line_sane(sport, 'it', line_it, prev_num):
            continue
        if ind > line_it:
            odd = p_market.get('over', 0)
            if odd > 0:
                confirmed.append((
                    'it', f'{player}_over', odd,
                    f'prev ИТ{player} {ind} > {line_it}',
                    prev_num,
                ))
        elif ind < line_it:
            odd = p_market.get('under', 0)
            if odd > 0:
                confirmed.append((
                    'it', f'{player}_under', odd,
                    f'prev ИТ{player} {ind} < {line_it}',
                    prev_num,
                ))

    winner_side = None
    if prev_s1 > prev_s2:
        winner_side = '1'
    elif prev_s2 > prev_s1:
        winner_side = '2'

    if winner_side:
        w = markets.get('winner', {}) or {}
        odd = w.get(winner_side, 0)
        if odd > 0:
            confirmed.append((
                'winner', winner_side, odd,
                f'prev {prev_phase_name} {prev_s1}:{prev_s2}',
                prev_num,
            ))

    margin1 = prev_s1 - prev_s2
    margin2 = prev_s2 - prev_s1
    h = markets.get('handicap', {}) or {}
    for side, margin in (('1', margin1), ('2', margin2)):
        h_side = h.get(side, {}) or {}
        if not h_side:
            continue
        line_h = h_side.get('line', 0) or 0
        odd_h = h_side.get('odd', 0)
        if (odd_h > 0
                and sports.is_phase_line_sane(sport, 'handicap', line_h, prev_num)
                and (margin + line_h) > 0):
            confirmed.append((
                'handicap', side, odd_h,
                f'prev П{side} margin {margin} + line {line_h}',
                prev_num,
            ))

    return confirmed


def filter_confirmed_by_strategy(confirmed, strategy, payload):
    """Фильтр подтверждённых исходов по manual_markets стратегии."""
    mode = strategy.get('market_mode', 'auto')
    if mode != 'manual':
        return confirmed

    manual = set(strategy.get('manual_markets') or [])
    if not manual:
        return confirmed

    result = []
    for c in confirmed:
        market, side, odd, reason, set_number = c
        if sports.is_market_allowed(market, side, manual):
            result.append(c)

    if not result:
        logger.debug(
            f"filter_confirmed_by_strategy: все исходы отсеяны "
            f"manual_markets={sorted(manual)}"
        )
    return result
