# ui/strategy_store.py
import json
import os
import sys
from typing import Optional, List, Dict
from ui.paths import get_app_data_dir


SPORT_ANY = "any"

VALID_SPORTS = {
    "table_tennis",
    "volleyball",
    "basketball",
    "cyber_basketball",
    SPORT_ANY,
}

VALID_DIRECTIONS = {"best_odds", "leader", "laggard", "same_as_fast"}
VALID_SIDES_WIN = {"1", "2", "both"}
VALID_SIDES_TOTAL = {"over", "under", "both"}
VALID_MARKETS = {"winner", "total", "handicap"}


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
        "verify_seconds": 3.0,
        "max_bets_per_match": 1,
        "max_bets_per_phase": 1,
        "ignore_repeats": False,
        # ---- Что ставить ----
        "markets_enabled": ["winner", "total", "handicap"],
        "bet_direction": "best_odds",
        "winner_sides": "both",
        "total_sides": "both",
        "handicap_sides": "both",
        # ---- Банкролл ----
        "bet_mode": "Фиксированная ставка",
        "bet_size": 100,
        "min_odds": 1.30,
        "max_odds": 5.0,
        # ---- Автоматизация ----
        "auto_bet": False,
        "auto_confirm": True,
        "stop_after_loss": False,
        "max_loss": 3,
        "headless": False,
    }


DEFAULT_STRATEGIES = [
    _default("Послегол", "After-goal", True, "", ""),
    _default("Валуй", "Value", True, "", ""),
    _default("Вилки", "Arbitrage", False, "", ""),
    _default("Коридоры", "Corridor", False, "", ""),
]


# Старый market_type → markets_enabled
_MARKET_TYPE_MAP = {
    "auto":     ["winner", "total", "handicap"],
    "winner":   ["winner"],
    "total":    ["total"],
    "handicap": ["handicap"],
}


def _migrate_market_type(s: dict) -> None:
    """Если в записи есть market_type и нет markets_enabled — конвертнуть."""
    if 'markets_enabled' in s and isinstance(s['markets_enabled'], list):
        # Убедимся, что значения валидны
        s['markets_enabled'] = [
            m for m in s['markets_enabled'] if m in VALID_MARKETS
        ] or ["winner", "total", "handicap"]
        return

    old = (s.get('market_type') or 'auto').lower()
    s['markets_enabled'] = _MARKET_TYPE_MAP.get(old, ["winner", "total", "handicap"])


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
            if 'headless' not in s:
                s['headless'] = False

            if 'sport' not in s or not s.get('sport'):
                s['sport'] = SPORT_ANY
            elif s['sport'] not in VALID_SPORTS:
                s['sport'] = SPORT_ANY

            # Новые поля — дефолты если нет
            if 'verify_seconds' not in s:
                s['verify_seconds'] = 3.0
            if 'max_bets_per_match' not in s:
                s['max_bets_per_match'] = 1
            if 'max_bets_per_phase' not in s:
                s['max_bets_per_phase'] = 1
            if 'ignore_repeats' not in s:
                s['ignore_repeats'] = False
            if 'bet_direction' not in s or s['bet_direction'] not in VALID_DIRECTIONS:
                s['bet_direction'] = 'best_odds'
            if 'winner_sides' not in s or s['winner_sides'] not in VALID_SIDES_WIN:
                s['winner_sides'] = 'both'
            if 'total_sides' not in s or s['total_sides'] not in VALID_SIDES_TOTAL:
                s['total_sides'] = 'both'
            if 'handicap_sides' not in s or s['handicap_sides'] not in VALID_SIDES_WIN:
                s['handicap_sides'] = 'both'

            _migrate_market_type(s)

            if s.get('type', '').lower() == 'after-goal':
                if 'bk_slow' in s and not s.get('bk'):
                    s['bk'] = s['bk_slow']
                if 'profile_id' not in s:
                    s['profile_id'] = ''
                for old in ['bk_fast', 'bk_slow', 'market_type']:
                    if old in s:
                        del s[old]
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
            "After-goal",
            False,
            "",
            "",
            sport="table_tennis",
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
            if s.get('type', '').lower() != 'after-goal':
                continue
            if s.get('bk', '').lower() != payload.get('slow_bk', '').lower():
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
            if s.get('type', '').lower() != 'after-goal':
                continue
            if s.get('bk', '').lower() != payload.get('slow_bk', '').lower():
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
            if bk and s.get('bk', '').lower() != bk.lower():
                continue
            result.append(s)
        return result