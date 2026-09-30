# ui/after_goal/match_urls.py
import re
from typing import Optional


# Slug'и спорта для каждой БК (совпадают с core/sport_map.py)
_SPORT_SLUGS = {
    "winline": {
        "table_tennis":     "nastolijnyj_tennis",
        "volleyball":       "volejbol",
        "basketball":       "basketbol",
        "cyber_basketball": "basketbol",
    },
    "leon": {
        "table_tennis":     "table-tennis",
        "volleyball":       "volleyball",
        "basketball":       "basketball",
        "cyber_basketball": "basketball",
    },
    "betcity": {
        "table_tennis":     "table-tennis",
        "volleyball":       "volleyball",
        "basketball":       "basketball",
        "cyber_basketball": "basketball",
    },
    "zenit": {
        "table_tennis":     "134",
        "volleyball":       "41",
        "basketball":       "28",
        "cyber_basketball": "564",
    },
    "sportbet": {
        "table_tennis":     "table-tennis",
        "volleyball":       "volleyball",
        "basketball":       "basketball",
    },
}


def build_match_url(bk: str, payload: dict) -> Optional[str]:
    """
    Fallback URL матча. Учитывает вид спорта из payload['sport'].
    Если вид не определён — возвращает None (фронт кликает по live-списку).
    """
    bk = (bk or "").lower()
    mid = str(payload.get('match_id') or payload.get('event_id') or "")
    if not mid:
        return None

    sport = (payload.get('sport') or 'table_tennis').lower()
    if sport == 'cyber_basketball':
        sport = 'cyber_basketball'

    # Для БК, которые требуют специфичных полей — None (клик)
    if bk in ('fonbet', 'marathon', 'ligastavok', 'olimp'):
        return None

    slugs = _SPORT_SLUGS.get(bk)
    if not slugs:
        return None
    sport_slug = slugs.get(sport)
    if not sport_slug:
        return None

    if bk == "winline":
        return f"https://winline.ru/live/sport/{sport_slug}/{mid}"

    if bk == "leon":
        return f"https://leon.ru/bets/{sport_slug}/x/x/{mid}"

    if bk == "betcity":
        return f"https://betcity.ru/ru/live/{sport_slug}/{mid}"

    if bk == "zenit":
        return f"https://zenit.win/live/{sport_slug}/{mid}"

    if bk == "sportbet":
        return (f"https://sportbet.ru/live/{sport_slug}/x--x/"
                f"x-vs-x--{mid}?isTime=1&h=all&page=main")

    return None