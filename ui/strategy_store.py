# ui/strategy_store.py
import json
import os
from typing import Optional, List, Dict
from ui.paths import get_app_data_dir


SPORT_ANY = "any"

VALID_SPORTS = {
    "table_tennis", "volleyball", "basketball", "cyber_basketball",
    SPORT_ANY,
}

VALID_MARKET_MODES = {"auto", "manual"}
VALID_AUTO_CRITERIA = {"reliable", "max_odds", "all_confirmed"}
# ── Типы стратегий ──
VALID_TYPES = {"after-goal", "live-value"}

# ── Рынки, которые можно сравнивать в live-value ──
LIVE_VALUE_MARKETS = [
    "winner", "total", "handicap", "it", "odd", "point"
]

# Все возможные рынки (для manual галочек)
ALL_MARKETS = [
    "winner_1", "winner_2",
    "total_over", "total_under",
    "handicap_1", "handicap_2",
    "it1_over", "it1_under", "it2_over", "it2_under",
    "odd", "race", "point",
]

DEAD_FIELDS = (
    'bet_mode', 'auto_bet', 'auto_confirm',
    'stop_after_loss', 'max_loss',
    'bk_fast', 'bk_slow', 'market_type',
    'bet_direction', 'winner_sides', 'total_sides',
    'handicap_sides', 'markets_enabled',
)


# ── Capability: какие рынки реально даёт каждая БК в лайв-ленте ──
# Используется ТОЛЬКО для предупреждений в UI, не блокирует сохранение.
# Ключ — нормализованный bk (без пробелов, дефисов, нижний регистр).
BK_MARKET_CAPABILITIES = {
    "fonbet": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd", "point",
    },
    "pari": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd", "point",
    },
    "betcity": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd",
    },
    "olimp": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd",
    },
    "sportbet": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd", "point",
    },
    "ligastavok": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd", "race",
    },
    "marathon": {
        # По дампу 2026-10-04: нет «N-го очка» и RACE.
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd",
    },
    "winline": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "odd", "point",
    },
    "zenit": {
        "winner_1", "winner_2",
        "total_over", "total_under",
        "handicap_1", "handicap_2",
        "it1_over", "it1_under", "it2_over", "it2_under",
        "odd", "point",
    },
    "leon": {
        # Leon пока парсит только winner.
        "winner_1", "winner_2",
    },
}


# ── Рекомендуемые пороги по видам спорта ──
RECOMMENDED_THRESHOLDS = {
    "table_tennis":     {"winner": 2, "total": 1, "handicap": 3, "it": 2},
    "volleyball":       {"winner": 3, "total": 2, "handicap": 4, "it": 2},
    "basketball":       {"winner": 8, "total": 5, "handicap": 8, "it": 6},
    "cyber_basketball": {"winner": 8, "total": 5, "handicap": 8, "it": 6},
}


def _normalize_bk(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def bk_capabilities(bk: str) -> set:
    """Возвращает множество доступных рынков для БК или пустое, если неизвестна."""
    return BK_MARKET_CAPABILITIES.get(_normalize_bk(bk), set())


def missing_markets_for_strategy(strategy: dict) -> list:
    """
    Рынки, выбранные в стратегии, но недоступные у её БК.

    Возвращает список кодов (winner_1, total_over, it2_under, ...).
    Пустой список = всё ок / нечего проверять.
    Только для market_mode='manual'.
    """
    bk = _normalize_bk(strategy.get("bk"))
    if not bk:
        return []
    caps = BK_MARKET_CAPABILITIES.get(bk)
    if caps is None:
        return []  # БК неизвестна — не пугаем пользователя
    if (strategy.get("market_mode") or "auto") != "manual":
        return []

    selected = set(strategy.get("manual_markets") or [])
    # 'race' — это префикс (race_winner_1), его нет в capability.
    # Он регулируется отдельным флагом race_enabled.
    selected.discard("race")
    return sorted(selected - caps)


def recommended_thresholds_for_sport(sport: str) -> dict:
    """Рекомендуемые пороги для вида спорта (копия)."""
    return dict(RECOMMENDED_THRESHOLDS.get(
        (sport or "table_tennis").lower(),
        RECOMMENDED_THRESHOLDS["table_tennis"],
    ))


def _default(name, stype, enabled, profile_id="", bk="", sport="table_tennis"):
    return {
        "name": name,
        "type": stype,
        "enabled": enabled,
        "profile_id": profile_id,
        "bk": bk,
        "sport": sport,
        "min_delay": 2.0,
        "min_score_diff": 2,
        "market_thresholds": {
            "winner": 2, "total": 1,
            "handicap": 3, "it": 2,
        },
        "verify_seconds": 3.0,
        "max_bets_per_match": 1,
        "max_bets_per_phase": 1,
        "ignore_repeats": False,

        # ── Что ставить ──
        "market_mode": "auto",
        "auto_criterion": "reliable",
        "manual_markets": list(ALL_MARKETS),

        # ── RACE (гонка внутри сета) ──
        "race_enabled": False,

        # ── Ставка ──
        "bet_size": 100,
        "min_odds": 1.30,
        "max_odds": 5.0,

        # ── Автоматизация ──
        "max_bets_per_session": 0,
        "headless": False,

        # ── Live-value ──
        "min_edge_percent": 5.0,
        "only_markets": ["winner", "total", "handicap"],
    }


DEFAULT_STRATEGIES = [
    _default("Послегол", "After-goal", True, "", ""),
    _default("Валуй", "Value", True, "", ""),
    _default("Вилки", "Arbitrage", False, "", ""),
    _default("Коридоры", "Corridor", False, "", ""),
]


def _migrate_old_markets(s: dict) -> list:
    """
    Миграция старых полей (markets_enabled + sides) → manual_markets.
    Возвращает список конкретных рынков.
    """
    markets = []
    enabled = s.get("markets_enabled") or ["winner", "total", "handicap"]
    if isinstance(enabled, str):
        enabled = [enabled]

    winner_sides = s.get("winner_sides", "both")
    total_sides = s.get("total_sides", "both")
    handicap_sides = s.get("handicap_sides", "both")

    if "winner" in enabled:
        if winner_sides in ("1", "both"):
            markets.append("winner_1")
        if winner_sides in ("2", "both"):
            markets.append("winner_2")

    if "total" in enabled:
        if total_sides in ("over", "both"):
            markets.append("total_over")
        if total_sides in ("under", "both"):
            markets.append("total_under")

    if "handicap" in enabled:
        if handicap_sides in ("1", "both"):
            markets.append("handicap_1")
        if handicap_sides in ("2", "both"):
            markets.append("handicap_2")

    if not markets:
        markets = ["winner_1", "winner_2", "total_over", "total_under"]

    return markets


class StrategyStore:
    def __init__(self):
        data_dir = get_app_data_dir()
        self.path = os.path.join(data_dir, "strategies.json")
        self.strategies = []
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.strategies = json.load(f)
        except Exception:
            self.strategies = []

        if not self.strategies:
            self.strategies = [dict(s) for s in DEFAULT_STRATEGIES]
            self.save()
            return

        for s in self.strategies:
            # sport
            if 'sport' not in s or not s.get('sport'):
                s['sport'] = SPORT_ANY
            elif s['sport'] not in VALID_SPORTS:
                s['sport'] = SPORT_ANY

            # числа
            for k, default in (("verify_seconds", 3.0),
                                ("max_bets_per_match", 1),
                                ("max_bets_per_phase", 1),
                                ("min_delay", 2.0),
                                ("min_score_diff", 2),
                                ("bet_size", 100),
                                ("min_odds", 1.30),
                                ("max_odds", 5.0),
                                ("max_bets_per_session", 0)):
                if k not in s:
                    s[k] = default
            for k in ("verify_seconds", "min_delay"):
                try:
                    s[k] = float(s[k])
                except Exception:
                    s[k] = 3.0
            for k in ("max_bets_per_match", "max_bets_per_phase",
                      "min_score_diff", "max_bets_per_session"):
                try:
                    s[k] = int(s[k])
                except Exception:
                    s[k] = 1

            if 'ignore_repeats' not in s:
                s['ignore_repeats'] = False
            if 'headless' not in s:
                s['headless'] = False

            if 'race_enabled' not in s:
                s['race_enabled'] = False
            else:
                s['race_enabled'] = bool(s['race_enabled'])

            # market_thresholds
            ND = {"winner": 2, "total": 1, "handicap": 3, "it": 2}
            if 'market_thresholds' not in s or \
                    not isinstance(s['market_thresholds'], dict):
                s['market_thresholds'] = dict(ND)
            else:
                for mk in ("winner", "total", "handicap", "it"):
                    try:
                        s['market_thresholds'][mk] = int(
                            s['market_thresholds'].get(mk, ND[mk]))
                    except Exception:
                        s['market_thresholds'][mk] = ND[mk]

            # ── МИГРАЦИЯ рынков ──
            is_new_format = (
                'market_mode' in s and
                'manual_markets' in s and
                isinstance(s['manual_markets'], list)
            )
            if not is_new_format:
                s['manual_markets'] = _migrate_old_markets(s)
                s['market_mode'] = 'manual'
                old_dir = s.get('bet_direction', 'same_as_fast')
                s['auto_criterion'] = 'max_odds' if old_dir == 'best_odds' else 'reliable'
            else:
                if s['market_mode'] not in VALID_MARKET_MODES:
                    s['market_mode'] = 'auto'
                if s.get('auto_criterion') not in VALID_AUTO_CRITERIA:
                    s['auto_criterion'] = 'reliable'
                s['manual_markets'] = [
                    m for m in s['manual_markets'] if m in ALL_MARKETS
                ] or ["winner_1", "winner_2"]

            # Убираем мёртвые поля
            for dead in DEAD_FIELDS:
                s.pop(dead, None)

            # ── Live-value поля ──
            if 'min_edge_percent' not in s:
                s['min_edge_percent'] = 5.0
            else:
                try:
                    s['min_edge_percent'] = max(0.5, min(50.0,
                        float(s['min_edge_percent'])))
                except (ValueError, TypeError):
                    s['min_edge_percent'] = 5.0

            if 'only_markets' not in s or not isinstance(s['only_markets'], list):
                s['only_markets'] = ["winner", "total", "handicap"]
            else:
                s['only_markets'] = [
                    m for m in s['only_markets'] if m in LIVE_VALUE_MARKETS
                ] or ["winner"]

            # Проверка типа
            t = (s.get('type') or '').lower()
            if t not in VALID_TYPES:
                # Невалидный тип (Value, Arbitrage, Corridor из дефолтов) —
                # приводим к After-goal, чтобы стратегия работала.
                s['type'] = 'After-goal'
                t = 'after-goal'

            if t == 'after-goal':
                s.setdefault('profile_id', '')

        self.save()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.strategies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Не удалось сохранить стратегии: {e}")

    def set_enabled(self, index, on):
        if 0 <= index < len(self.strategies):
            self.strategies[index]["enabled"] = bool(on)

    def enabled_list(self):
        return [s for s in self.strategies if s.get("enabled")]

    def add(self):
        self.strategies.append(_default(
            f"Стратегия {len(self.strategies) + 1}",
            "After-goal", False, "", "", sport="table_tennis",
        ))
        return len(self.strategies) - 1

    def remove(self, index):
        if 0 <= index < len(self.strategies):
            self.strategies.pop(index)

    @staticmethod
    def _sport_matches(strategy: dict, payload: dict) -> bool:
        strat_sport = (strategy.get('sport') or SPORT_ANY).lower()
        if strat_sport == SPORT_ANY:
            return True
        payload_sport = (payload.get('sport') or 'table_tennis').lower()
        return strat_sport == payload_sport

    def is_signal_relevant(self, payload: dict) -> bool:
        for s in self.strategies:
            if not s.get('enabled'):
                continue
            # Сигнал релевантен и для Послегола, и для Лайв-Валуя
            if (s.get('type') or '').lower() not in ('after-goal', 'live-value'):
                continue
            if _normalize_bk(s.get('bk', '')) != _normalize_bk(payload.get('slow_bk', '')):
                continue
            if not self._sport_matches(s, payload):
                continue
            if payload.get('delay', 0) < s.get('min_delay', 2.0):
                continue
            if s.get('ignore_repeats', False) and not payload.get('is_new', False):
                continue
            return True
        return False

    def get_matching_strategy(self, payload: dict) -> Optional[dict]:
        for s in self.strategies:
            if not s.get('enabled'):
                continue
            # Сигнал релевантен и для Послегола, и для Лайв-Валуя
            if (s.get('type') or '').lower() not in ('after-goal', 'live-value'):
                continue
            if _normalize_bk(s.get('bk', '')) != _normalize_bk(payload.get('slow_bk', '')):
                continue
            if not self._sport_matches(s, payload):
                continue
            delay = payload.get('delay', 0)
            if delay > 0 and delay < s.get('min_delay', 2.0):
                continue
            if s.get('ignore_repeats', False) and not payload.get('is_new', False):
                continue
            return s
        return None

    def get_matching_strategies_by_type(self, stype: str, bk: str = None) -> List[Dict]:
        result = []
        for s in self.strategies:
            if not s.get('enabled'):
                continue
            if s.get('type', '').lower() != stype.lower():
                continue
            if bk and _normalize_bk(s.get('bk', '')) != _normalize_bk(bk):
                continue
            result.append(s)
        return result


def get_live_value_strategy_for_bk(store, slow_bk: str) -> Optional[dict]:
    """
    Возвращает первую включённую live-value стратегию под указанную slow_bk.
    """
    for s in store.strategies:
        if not s.get('enabled'):
            continue
        if (s.get('type') or '').lower() != 'live-value':
            continue
        if _normalize_bk(s.get('bk', '')) == _normalize_bk(slow_bk):
            return s
    return None