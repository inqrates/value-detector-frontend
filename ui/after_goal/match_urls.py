# ui/after_goal/match_urls.py
"""
Фолбэк-конструктор URL на матч.
Используется ТОЛЬКО если backend не прислал payload['match_url'].
"""
import re
from typing import Optional


_TRANSLIT = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z',
    'и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
    'с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch',
    'ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
}


def _slug(s: str, translit: bool = True) -> str:
    if not s:
        return ""
    s = s.lower()
    if translit:
        s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return re.sub(r"-+", "-", s)


def build_match_url(bk: str, payload: dict) -> Optional[str]:
    """Возвращает URL матча или None."""
    bk = (bk or "").lower()
    mid = str(payload.get('match_id') or payload.get('event_id') or "")
    if not mid:
        return None

    teams = payload.get('match_teams') or []
    p1 = payload.get('player1') or (teams[0] if len(teams) > 0 else "")
    p2 = payload.get('player2') or (teams[1] if len(teams) > 1 else "")
    tour = payload.get('tournament', '')

    simple = {
        "fonbet":     f"https://fon.bet/live/table-tennis/{mid}",
        "winline":    f"https://winline.ru/live/sport/nastolijnyj_tennis/{mid}",
        "ligastavok": f"https://www.ligastavok.ru/sports/table-tennis/x-p-id-0-service-id-27-ext-id-{mid}",
        "leon":       f"https://leon.ru/bets/table-tennis/{mid}",
        "olimp":      f"https://www.olimp.bet/live/nastolnyy-tennis-40/x/x-{mid}",
        "betcity":    f"https://betcity.ru/ru/live/table-tennis/{mid}",
        "marathon":   f"https://new.marathonbet.ru/su/betting/event/table-tennis/"
                      f"{_slug(tour) or 'x'}/{_slug(p1)}-vs-{_slug(p2)}",
        "zenit":      f"https://zenit.win/live/134/{mid}",
        "sportbet":   f"https://sportbet.ru/live/table-tennis/x--x/x-vs-x--{mid}"
                      f"?isTime=1&h=all&page=main",
    }
    return simple.get(bk)