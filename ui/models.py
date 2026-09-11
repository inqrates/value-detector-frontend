"""Модель данных приложения (заглушки для статических данных)."""

USER_DATA = {
    "login": "Admin",
    "plan": "PRO",
    "days_left": 23,
    "days_total": 30,
}

BOOKMAKERS = [
    {"name": "Fonbet", "status": "working", "matches": 21, "delay": 4.8, "profile": "k1fdmfr4"},
    {"name": "Winline", "status": "working", "matches": 22, "delay": 3.1, "profile": "k1fdmfr2"},
    {"name": "Liga Stavok", "status": "working", "matches": 19, "delay": 4.4, "profile": "k1fdmfr5"},
    {"name": "Leon", "status": "working", "matches": 44, "delay": 5.9, "profile": "auto"},
]

ACTIVE_STRATEGIES = [
    {"name": "Валуй", "bks": "Fonbet → Winline", "profit": 1240, "bets": 12, "winrate": 68},
    {"name": "Валуй", "bks": "Leon → Liga Stavok", "profit": 860, "bets": 8, "winrate": 61},
    {"name": "Послегол", "bks": "Winline → Liga Stavok", "profit": 47300, "bets": 2, "winrate": 100},
]