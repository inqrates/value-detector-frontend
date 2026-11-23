# ui/after_goal/sports.py
"""
Чистые функции правил фаз по видам спорта.

Не зависит от I/O, self, браузера, сети.
Поведение 1:1 из ui/after_goal_engine.py.
Кандидат для unit-тестов.
"""
from typing import Optional, Tuple

PHASE_END_BY_SCORE = {"table_tennis", "volleyball", "beach_volleyball"}
PHASE_END_BY_TIME = {"basketball", "cyber_basketball"}

# ── Маппинг код manual_markets → базовый рынок (для порогов) ──
MARKET_CODE_TO_BASE = {
    "winner_1": "winner",
    "winner_2": "winner",
    "total_over": "total",
    "total_under": "total",
    "handicap_1": "handicap",
    "handicap_2": "handicap",
    "it1_over": "it",
    "it1_under": "it",
    "it2_over": "it",
    "it2_under": "it",
}


def safe_int(v: object, d: int) -> int:
    """Безопасный int с гарантией >= 1 (или d, если не удалось)."""
    try:
        return max(1, int(v))
    except (ValueError, TypeError):
        return d


def parse_phase_num(phase_name: str) -> int:
    """Извлекает номер фазы из строки типа '1-я партия' или 'Set 2'."""
    if not phase_name:
        return 0
    import re
    m = re.search(r'(\d+)', str(phase_name))
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def max_phase_total(sport: str, set_number: int) -> int:
    """Максимально возможная сумма очков в партии."""
    if sport == 'table_tennis':
        return 40
    if sport in ('volleyball', 'beach_volleyball'):
        return 32 if set_number >= 5 else 52
    if sport in ('basketball', 'cyber_basketball'):
        return 80
    return 100


def phase_end(sport: str, s1: int, s2: int, set_number: int) \
        -> Tuple[bool, Optional[str]]:
    """
    Закончилась ли текущая партия.
    Возвращает (done, winner) где winner='1' или '2'.
    """
    if sport == "table_tennis":
        if s1 >= 11 and s1 - s2 >= 2:
            return True, '1'
        if s2 >= 11 and s2 - s1 >= 2:
            return True, '2'
        return False, None
    if sport in ("volleyball", "beach_volleyball"):
        threshold = 15 if set_number >= 5 else 25
        if s1 >= threshold and s1 - s2 >= 2:
            return True, '1'
        if s2 >= threshold and s2 - s1 >= 2:
            return True, '2'
        return False, None
    return False, None


def is_set_done(sport: str, a: int, b: int, set_number: int,
                fast_phase: int) -> bool:
    """Закончена ли партия по счёту (без номера фазы из WS)."""
    if sport in ('basketball', 'cyber_basketball'):
        return fast_phase > set_number
    if sport == 'table_tennis':
        return (a >= 11 and a - b >= 2) or (b >= 11 and b - a >= 2)
    if sport in ('volleyball', 'beach_volleyball'):
        thr = 15 if set_number >= 5 else 25
        return (a >= thr and a - b >= 2) or (b >= thr and b - a >= 2)
    return False


def resolve_phase_context(sport: str, payload: dict, data: dict,
                          set_markets: dict,
                          fast_score: list, slow_score: list) \
        -> Tuple[int, int]:
    """
    Определяет текущий номер партии для fast и slow.
    Возвращает (fast_phase_num, slow_phase_num).
    """
    fast_phase_num = parse_phase_num(payload.get('fast_phase', ''))

    if not fast_phase_num and sport in PHASE_END_BY_SCORE:
        fast_phase_num = fast_score[0] + fast_score[1] + 1

    slow_phase_num = int(data.get('phase_num') or 0)

    if not slow_phase_num:
        if sport in PHASE_END_BY_SCORE:
            slow_phase_num = slow_score[0] + slow_score[1] + 1
        else:
            nums = []
            for k in set_markets.keys():
                if isinstance(k, str) and k.startswith('set_'):
                    try:
                        nums.append(int(k.split('_', 1)[1]))
                    except Exception:
                        pass
            slow_phase_num = max(nums) if nums else 1

    return fast_phase_num, slow_phase_num


def is_phase_line_sane(sport: str, market: str,
                       line: float, set_number: int) -> bool:
    """Проверка: линия рынка не превышает реалистичный максимум."""
    if line is None:
        return False
    try:
        abs_line = abs(float(line))
    except (ValueError, TypeError):
        return False

    if market == 'total':
        max_line = max_phase_total(sport, set_number)
    elif market in ('handicap', 'it'):
        max_line = max_phase_total(sport, set_number) // 2
    else:
        return True

    return abs_line <= max_line


def market_code(market: str, side: str) -> Optional[str]:
    """(market, side) → код в manual_markets."""
    if market == 'winner':
        return f'winner_{side}'
    if market == 'total':
        return f'total_{side}'
    if market == 'handicap':
        return f'handicap_{side}'
    if market == 'it':
        if '_' in side:
            player, dirn = side.split('_', 1)
            return f'it{player}_{dirn}'
        return None
    if market == 'odd':
        return 'odd'
    if market == 'point':
        return 'point'
    return None


def is_market_allowed(market: str, side: str, manual: set) -> bool:
    """Разрешён ли исход по настройкам manual_markets."""
    is_race = market.startswith('race_')
    base = market[5:] if is_race else market

    if is_race and 'race' not in manual:
        return False

    code = market_code(base, side)
    if not code:
        return False
    return code in manual


def get_market_threshold(strategy: dict, market: str) -> int:
    """Порог для конкретного рынка из стратегии."""
    thresholds = strategy.get('market_thresholds') or {}
    val = thresholds.get(market)
    if val is None:
        val = strategy.get('min_score_diff', 2)
    try:
        return max(1, int(val))
    except Exception:
        return 2


def get_min_threshold(strategy: dict) -> int:
    """
    Минимальный порог по всем рынкам, которые стратегия реально использует.

    manual — берём пороги только по рынкам, отмеченным в manual_markets.
    auto   — минимум по всем четырём базовым рынкам.
    """
    thresholds = strategy.get('market_thresholds') or {}
    default = strategy.get('min_score_diff', 2)

    def _safe(v, d):
        try:
            return max(1, int(v))
        except Exception:
            return d

    mode = strategy.get('market_mode', 'auto')

    if mode == 'manual':
        manual = strategy.get('manual_markets') or []
        base_markets = set()
        for code in manual:
            m = MARKET_CODE_TO_BASE.get(code)
            if m:
                base_markets.add(m)
        if not base_markets:
            return _safe(default, 2)
        values = [_safe(thresholds.get(m, default), default)
                  for m in base_markets]
        return min(values)

    values = []
    for m in ('winner', 'total', 'handicap', 'it'):
        values.append(_safe(thresholds.get(m, default), default))
    return min(values) if values else _safe(default, 2)
