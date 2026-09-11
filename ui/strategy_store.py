# ui/strategy_store.py
import json
import os
import sys
from typing import Optional, List, Dict
from ui.paths import get_app_data_dir

def _default(name, stype, enabled, profile_id="", bk=""):
    return {
        "name": name,
        "type": stype,
        "enabled": enabled,
        "profile_id": profile_id,
        "bk": bk,
        "min_delay": 2.0,
        "min_score_diff": 2,
        "bet_mode": "Фиксированная ставка",
        "bet_size": 100,
        "min_odds": 1.30,
        "max_odds": 5.0,
        "market_type": "auto",
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
            if s.get('type', '').lower() == 'after-goal':
                if 'bk_slow' in s and not s.get('bk'):
                    s['bk'] = s['bk_slow']
                if 'profile_id' not in s:
                    s['profile_id'] = ''
                for old in ['bk_fast', 'bk_slow']:
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
            ""
        ))
        return len(self.strategies) - 1

    def remove(self, index):
        if 0 <= index < len(self.strategies):
            self.strategies.pop(index)

    def is_signal_relevant(self, payload: dict) -> bool:
        for s in self.strategies:
            if not s.get('enabled'):
                continue
            if s.get('type', '').lower() != 'after-goal':
                continue
            if s.get('bk', '').lower() != payload.get('slow_bk', '').lower():
                continue
            if payload.get('delay', 0) < s.get('min_delay', 2.0):
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
            delay = payload.get('delay', 0)
            if delay > 0 and delay < s.get('min_delay', 2.0):
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