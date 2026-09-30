# probe_fonbet.py
"""
Пробник Fonbet / Pari — все виды спорта.

Схема:
  1. Для каждого вида (НТ, волейбол, баскет, кибер) открываем live-раздел
  2. Ловим listBase — там events + eventMiscs + sports
  3. Строим дерево от root (как бэкенд в _rebuild_tree)
  4. Фильтруем ТОЛЬКО ЖИВЫЕ матчи (eventMiscs со счётом или comment)
  5. Учитываем category_ids (для кибера / баскета)
  6. Идём в каждый матч через URL бэкенда
  7. Сохраняем отдельный дамп на вид

Запуск:
    python probe_fonbet.py --bk fonbet --per-sport 3
    python probe_fonbet.py --bk fonbet --sport table_tennis
    python probe_fonbet.py --bk pari   --per-sport 3
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from core.adspower_browser import AdsPowerBrowser
from ui.paths import get_app_data_dir

OUT = Path("probe_dumps")
OUT.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════
# Настройки БК
# ═══════════════════════════════════════════════════════════
# root_ids — корневой sportId вида (см. core/sport_map.py)
# exclude_category_ids — для отсеивания (пример: кибер от баскета)
# include_category_ids — только эти категории (для кибера)
#   В Fonbet: category 119 = кибер-баскетбол
BK_CONFIG = {
    "fonbet": {
        "account_name": "fonbet",
        "base": "https://fon.bet",
        "sports": {
            "table_tennis": {
                "root_ids": [3088],
                "url_slug": "table-tennis",
                "live_url": "https://fon.bet/live/table-tennis",
            },
            "volleyball": {
                "root_ids": [9],
                "url_slug": "volleyball",
                "live_url": "https://fon.bet/live/volleyball",
            },
            "basketball": {
                "root_ids": [3],
                "url_slug": "basketball",
                "live_url": "https://fon.bet/live/basketball",
                "exclude_category_ids": [119],
            },
            "cyber_basketball": {
                "root_ids": [3],
                "url_slug": "basketball",
                "live_url": "https://fon.bet/live/basketball",
                "include_category_ids": [119],
            },
        },
        "list_filter": ("/ma/events/listBase",),
        "event_filter": ("/ma/events/event", "/events/event"),
    },
    "pari": {
        "account_name": "pari",
        "base": "https://pari.ru",
        "sports": {
            "table_tennis": {
                "root_ids": [3088],
                "url_slug": "table-tennis",
                "live_url": "https://pari.ru/live/table-tennis",
            },
            "volleyball": {
                "root_ids": [9],
                "url_slug": "volleyball",
                "live_url": "https://pari.ru/live/volleyball",
            },
            "basketball": {
                "root_ids": [3],
                "url_slug": "basketball",
                "live_url": "https://pari.ru/live/basketball",
                "exclude_category_ids": [119],
            },
            "cyber_basketball": {
                "root_ids": [3],
                "url_slug": "basketball",
                "live_url": "https://pari.ru/live/basketball",
                "include_category_ids": [119],
            },
        },
        "list_filter": ("/events/listLight", "/events/list"),
        "event_filter": ("/events/event",),
    },
}


def find_account(bk_name: str):
    path = os.path.join(get_app_data_dir(), "accounts.json")
    with open(path, encoding="utf-8") as f:
        accounts = json.load(f)
    norm = bk_name.lower().replace(" ", "")
    for a in accounts:
        raw = (a.get("bk") or "").lower().replace(" ", "")
        if raw == norm:
            return a["ads_power_id"], a["api_key"]
    raise RuntimeError(f"{bk_name} не найден в accounts.json")


# ═══════════════════════════════════════════════════════════
# Дерево sportId (как в бэкенде: parsers/fonbet_api.py)
# ═══════════════════════════════════════════════════════════
def build_tree(all_sports: list, root_ids: list) -> set:
    """Все потомки root'ов (рекурсивно по parentId)."""
    children = {}
    for s in all_sports:
        sid = s.get("id")
        pid = s.get("parentId")
        if sid and pid:
            children.setdefault(pid, []).append(sid)

    desc = set(root_ids)
    stack = list(root_ids)
    while stack:
        cur = stack.pop()
        for child in children.get(cur, []):
            if child not in desc:
                desc.add(child)
                stack.append(child)
    return desc


def is_live_match(misc: dict) -> bool:
    """
    Проверка, что матч РЕАЛЬНО идёт (не прематч, не завершён).
    """
    if not misc:
        return False
    s1 = int(misc.get("score1") or 0)
    s2 = int(misc.get("score2") or 0)
    comment = (misc.get("comment") or "").strip().lower()

    if "не начался" in comment or "завершен" in comment or "завершён" in comment:
        return False
    if "матч не начался" in comment:
        return False

    # Если есть счёт или осмысленный комментарий (типа "1-0", "2*:5")
    if s1 > 0 or s2 > 0:
        return True
    if comment and comment not in ("-", "0:0"):
        return True
    return False


def find_live_events(list_data: dict, sport_cfg: dict, limit: int) -> list:
    """
    Возвращает [(sportId, eventId, player1, player2, categoryId), ...]
    только для ЖИВЫХ матчей нужного вида.

    Учитывает:
      - root_ids дерева
      - exclude_category_ids (например кибер vs обычный баскет)
      - include_category_ids (только эти категории)
    """
    sports = list_data.get("sports") or []
    tree_ids = build_tree(sports, sport_cfg["root_ids"])
    print(f"  🌳 Дерево от root {sport_cfg['root_ids']}: "
          f"{len(tree_ids)} sportId")

    # sport_category_id для каждого sportId
    cat_by_sid = {}
    for s in sports:
        sid = s.get("id")
        cid = s.get("sportCategoryId")
        if sid is not None:
            cat_by_sid[sid] = cid

    events = list_data.get("events") or []
    miscs_by_id = {m.get("id"): m
                   for m in (list_data.get("eventMiscs") or [])}

    exclude = set(sport_cfg.get("exclude_category_ids") or [])
    include = sport_cfg.get("include_category_ids")

    found = []
    total_in_tree = 0
    live_count = 0

    for ev in events:
        if not isinstance(ev, dict):
            continue
        sid = ev.get("sportId")
        if sid not in tree_ids:
            continue
        total_in_tree += 1

        # Фильтр по category
        cat_id = cat_by_sid.get(sid)
        if exclude and cat_id in exclude:
            continue
        if include and cat_id not in include:
            continue

        eid = ev.get("id")
        if not eid:
            continue

        misc = miscs_by_id.get(eid)
        if not is_live_match(misc):
            continue
        live_count += 1

        p1 = ev.get("team1") or ev.get("participant1") or ""
        p2 = ev.get("team2") or ev.get("participant2") or ""
        if not p1 or not p2:
            continue

        found.append((sid, eid, p1, p2, cat_id))
        if len(found) >= limit:
            break

    print(f"  📊 В дереве {sport_cfg['root_ids']}: "
          f"events={total_in_tree}, живых={live_count}, "
          f"взято={len(found)}")
    return found


# ═══════════════════════════════════════════════════════════
# Пробник по одному виду
# ═══════════════════════════════════════════════════════════
class SportProbe:
    def __init__(self, bk: str, cfg: dict, sport: str,
                 sport_cfg: dict, per_sport: int):
        self.bk = bk
        self.cfg = cfg
        self.sport = sport
        self.sport_cfg = sport_cfg
        self.per_sport = per_sport

        self.collected = []
        self.current_match_id = None
        self._current_buffer = []
        self._list_responses = []
        self._resp_listener = None

    async def run(self, page):
        print(f"\n{'═' * 78}")
        print(f"  {self.bk} / {self.sport}")
        print(f"{'═' * 78}")

        self._list_responses = []
        self._current_buffer = []

        # ── Листенер ──
        async def on_resp(r):
            url = r.url

            if any(f in url for f in self.cfg["list_filter"]):
                if "version=" in url:
                    return
                try:
                    data = await r.json()
                except Exception:
                    return
                self._list_responses.append({"url": url, "data": data})
                cnt = len(data.get("events") or [])
                print(f"     📥 LIST events={cnt}")
                return

            if any(f in url for f in self.cfg["event_filter"]):
                try:
                    data = await r.json()
                except Exception:
                    return
                if self.current_match_id is not None:
                    self._current_buffer.append({
                        "match_id": self.current_match_id,
                        "url": url,
                        "data": data,
                    })
                cnt_cf = len(data.get("customFactors") or [])
                cnt_ev = len(data.get("events") or [])
                print(f"     📥 EVENT customFactors={cnt_cf} "
                      f"events={cnt_ev}")

        self._resp_listener = lambda r: asyncio.create_task(on_resp(r))
        page.on("response", self._resp_listener)

        # ── 1. Заходим в live-раздел ──
        print(f"  🔗 {self.sport_cfg['live_url']}")
        try:
            await page.goto(self.sport_cfg["live_url"],
                            wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  ⚠️ goto: {e}")

        print(f"  ⏳ ждём 12 сек пока фронт загрузит listBase...")
        await asyncio.sleep(12)

        # ── 2. Ищем живые матчи ──
        if not self._list_responses:
            print(f"  ❌ listBase не поймали")
            self._remove_listener(page)
            return

        list_data = max(
            self._list_responses,
            key=lambda x: len(x["data"].get("events") or []),
        )["data"]

        live_events = find_live_events(
            list_data, self.sport_cfg, self.per_sport
        )

        if not live_events:
            print(f"  ⚠️ живых матчей {self.sport} нет (все прематч/завершены)")
            self._remove_listener(page)
            return

        for sid, eid, p1, p2, cid in live_events[:5]:
            print(f"     sid={sid} eid={eid} cat={cid} | {p1} vs {p2}")

        # ── 3. Заходим в каждый матч ──
        slug = self.sport_cfg["url_slug"]
        for i, (sid, eid, p1, p2, cid) in enumerate(live_events, 1):
            match_url = (
                f"{self.cfg['base']}/live/{slug}/"
                f"category/x/{sid}/{eid}"
            )

            print(f"\n  [{i}/{len(live_events)}] {p1} vs {p2}")
            print(f"     {match_url[:120]}")

            self.current_match_id = str(eid)
            self._current_buffer = []

            try:
                await page.goto(match_url,
                                wait_until="domcontentloaded",
                                timeout=60000)
            except Exception as e:
                print(f"     ⚠️ goto: {e}")

            await asyncio.sleep(8)

            # Проверка
            try:
                title = await page.title()
                body = await page.locator("body").inner_text()
                tok1 = p1.split()[0].lower() if p1 else ""
                tok2 = p2.split()[0].lower() if p2 else ""
                found_p1 = tok1 in body.lower() if tok1 else False
                found_p2 = tok2 in body.lower() if tok2 else False
                ok = found_p1 and found_p2
                mark = "✅" if ok else "⚠️"
                print(f"     {mark} title={title[:70]!r}")
            except Exception as e:
                print(f"     ⚠️ проверка: {e}")

            if self._current_buffer:
                self.collected.append({
                    "match_id": str(eid),
                    "sport_id": sid,
                    "category_id": cid,
                    "sport": self.sport,
                    "url": match_url,
                    "player1": p1,
                    "player2": p2,
                    "responses": self._current_buffer,
                })
                print(f"     ✅ собрано ({len(self._current_buffer)} ответов)")
            else:
                print(f"     ⚠️ /events/event не поймали")

        # ── 4. Сохраняем ──
        self._remove_listener(page)

        if self.collected:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            f = OUT / f"{self.bk}_multi_{self.sport}_{ts}.json"
            f.write_text(
                json.dumps(self.collected, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n  💾 {f.name} "
                  f"({len(self.collected)} матчей, "
                  f"{f.stat().st_size // 1024} KB)")

    def _remove_listener(self, page):
        try:
            page.remove_listener("response", self._resp_listener)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bk", required=True, choices=list(BK_CONFIG.keys()))
    ap.add_argument("--per-sport", type=int, default=3,
                    help="Сколько матчей на вид (по умолчанию 3)")
    ap.add_argument("--sport",
                    help="Только один вид (иначе все 4)")
    args = ap.parse_args()

    cfg = BK_CONFIG[args.bk]

    sports_items = list(cfg["sports"].items())
    if args.sport:
        sports_items = [(s, c) for s, c in sports_items
                        if s == args.sport]
        if not sports_items:
            print(f"❌ {args.bk}: неизвестный спорт {args.sport!r}")
            return

    pid, key = find_account(cfg["account_name"])
    print(f"🚀 Multi-пробник {args.bk}")
    print(f"   Профиль: {pid}")
    print(f"   Видов: {len(sports_items)} ({[s for s, _ in sports_items]})")
    print(f"   Матчей на вид: {args.per_sport}")
    print(f"💾 Дампы: {OUT.resolve()}\n")

    w = AdsPowerBrowser(
        profile_id=pid, api_key=key,
        api_url="http://localhost:50325", headless=False,
    )

    try:
        await w.__aenter__()
        page = await w.new_page()
        print(f"📄 Одна вкладка\n")

        for sport, sport_cfg in sports_items:
            try:
                await SportProbe(
                    args.bk, cfg, sport, sport_cfg, args.per_sport
                ).run(page)
            except Exception as e:
                print(f"  ❌ {sport}: {e}")
                import traceback
                traceback.print_exc()
                # не падаем, идём в следующий вид

        await page.close()
    finally:
        await w.__aexit__(None, None, None)

    print(f"\n{'=' * 78}")
    print(f"✅ ГОТОВО. Файлы: {OUT.resolve()}")
    print(f"{'=' * 78}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ Прервано")