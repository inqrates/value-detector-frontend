# analyze_fonbet.py
"""
Анализатор дампов Fonbet / Pari от probe_fonbet.py.

Читает: probe_dumps/{bk}_multi_{sport}_{ts}.json

Для каждого матча показывает:
  1. Что в eventMiscs — score1/score2/comment/liveDelay
  2. Что в customFactors — ВСЕ f= коды (это ключ к расширению)
  3. Что в events — parentId, kind (для фазы)
  4. Что в liveEventInfos — subscores (какая фаза активна)
  5. Сравнение с кодами из бэкенда (921/923/930/931/927/928)

Запуск:
    python analyze_fonbet.py probe_dumps/fonbet_multi_table_tennis_XXX.json
"""
import json
import sys
from pathlib import Path
from collections import Counter, defaultdict


# ── Коды из бэкенда (parsers/fonbet_api.py) ──
KNOWN_CODES = {
    921: "WIN_1 (П1)",
    922: "DRAW (X)",
    923: "WIN_2 (П2)",
    927: "HANDICAP_1 (Ф1)",
    928: "HANDICAP_2 (Ф2)",
    930: "TOTAL_OVER (ТБ)",
    931: "TOTAL_UNDER (ТМ)",
    910: "MATCH_HCP_1",
    912: "MATCH_HCP_2",
    1696: "TOTAL_OVER (вариант)",
    1697: "TOTAL_UNDER (вариант)",
    1848: "TOTAL_OVER (вариант)",
    1849: "TOTAL_UNDER (вариант)",
    3024: "TOTAL_OVER (вариант)",
    3025: "TOTAL_UNDER (вариант)",
    3030: "TOTAL_OVER (вариант)",
    3031: "TOTAL_UNDER (вариант)",
    974: "TOTAL_OVER (вариант)",
    976: "TOTAL_UNDER (вариант)",
    978: "TOTAL_OVER (вариант)",
    980: "TOTAL_UNDER (вариант)",
}


def analyze_match(m: dict):
    print(f"\n{'━' * 78}")
    print(f"  match_id: {m.get('match_id')} sport_id={m.get('sport_id')}")
    print(f"  {m.get('player1')} vs {m.get('player2')}")
    print(f"  URL: {m.get('url')}")
    print(f"{'━' * 78}")

    responses = m.get("responses") or []
    print(f"\n  Responses: {len(responses)}")
    for i, r in enumerate(responses):
        keys = list((r.get("data") or {}).keys())
        print(f"    [{i}] {r['url'].split('?')[0].split('/')[-1]} "
              f"keys={keys[:12]}")

    # ── Собираем все данные из всех ответов ──
    all_events = {}
    all_miscs = {}
    all_factors = {}  # eid → list[factors]
    all_live_infos = {}

    for r in responses:
        data = r.get("data") or {}
        for ev in data.get("events") or []:
            eid = ev.get("id")
            if eid:
                all_events[eid] = ev
        for msc in data.get("eventMiscs") or []:
            eid = msc.get("id")
            if eid:
                all_miscs[eid] = msc
        for cf in data.get("customFactors") or []:
            eid = cf.get("e")
            if eid:
                all_factors.setdefault(eid, []).extend(cf.get("factors") or [])
        for li in data.get("liveEventInfos") or []:
            eid = li.get("eventId")
            if eid:
                all_live_infos[eid] = li

    target = int(m.get("match_id"))

    # ── 1. event ──
    print(f"\n  ── event (матч) ──")
    ev = all_events.get(target)
    if ev:
        for k in ("id", "sportId", "team1", "team2", "tournamentName",
                  "parentId", "kind", "level", "isLive", "score1", "score2"):
            if k in ev:
                print(f"    {k}: {ev[k]!r}")
    else:
        print(f"    ❌ нет event с id={target}")

    # ── 2. eventMiscs (счёт) ──
    print(f"\n  ── eventMiscs (счёт) ──")
    msc = all_miscs.get(target)
    if msc:
        for k in ("id", "score1", "score2", "comment", "liveDelay",
                  "timer", "team1", "team2"):
            if k in msc:
                print(f"    {k}: {msc[k]!r}")
    else:
        print(f"    ❌ нет eventMiscs с id={target}")

    # ── 3. customFactors (все f= коды) ──
    print(f"\n  ── customFactors для {target} ──")
    factors = all_factors.get(target) or []
    if not factors:
        # Может быть, факторы на дочерние eventId?
        print(f"    ⚠️ нет customFactors для {target}")
        if all_factors:
            print(f"    Есть CF для других eid:")
            for eid, fs in list(all_factors.items())[:5]:
                print(f"      eid={eid}: {len(fs)} факторов")
    else:
        print(f"    Всего факторов: {len(factors)}")
        cnt = Counter(f.get("f") for f in factors)
        print(f"    Распределение по f:")
        for fc, n in sorted(cnt.items()):
            label = KNOWN_CODES.get(fc, "❓ НЕИЗВЕСТНЫЙ")
            print(f"      f={fc:>6} ({n} шт.) — {label}")

        print(f"\n    Все факторы (первые 40):")
        for f in factors[:40]:
            fc = f.get("f")
            label = KNOWN_CODES.get(fc, "")
            print(f"      f={fc:>6} v={f.get('v')!r:>10} "
                  f"pt={f.get('pt')!r:>10} p={f.get('p')!r} "
                  f"{label}")

    # ── 4. liveEventInfos (фаза, subscores) ──
    print(f"\n  ── liveEventInfos для {target} ──")
    li = all_live_infos.get(target)
    if li:
        subs = li.get("subscores") or []
        print(f"    subscores: {len(subs)}")
        for s in subs[:5]:
            print(f"      kindId={s.get('kindId')} "
                  f"kindName={s.get('kindName')!r} "
                  f"c1={s.get('c1')} c2={s.get('c2')}")
    else:
        print(f"    ❌ нет liveEventInfo с eventId={target}")

    # ── 5. Дочерние events (для баскета/волейбола) ──
    children = [
        ev for ev in all_events.values()
        if ev.get("parentId") == target
    ]
    if children:
        print(f"\n  ── дочерние events ({len(children)}) ──")
        for ch in children[:5]:
            print(f"    id={ch.get('id')} kind={ch.get('kind')} "
                  f"name={ch.get('name')!r}")
            # Есть ли customFactors для ребёнка?
            ch_factors = all_factors.get(ch.get("id"))
            if ch_factors:
                print(f"      ⭐ есть customFactors для {ch.get('id')}: "
                      f"{len(ch_factors)} шт")
                for f in ch_factors[:10]:
                    fc = f.get("f")
                    label = KNOWN_CODES.get(fc, "")
                    print(f"         f={fc} v={f.get('v')!r} "
                          f"pt={f.get('pt')!r} {label}")


def main():
    if len(sys.argv) < 2:
        print("usage: python analyze_fonbet.py <dump.json>")
        print("       python analyze_fonbet.py probe_dumps/fonbet_multi_*.json")
        return

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"❌ файл не найден: {path}")
        return

    print(f"📖 {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ {e}")
        return

    if not isinstance(data, list):
        print(f"❌ ожидался список, получен {type(data).__name__}")
        return

    print(f"📊 Матчей в дампе: {len(data)}")

    # Итоговая статистика по f= кодам
    total_f_codes = Counter()
    unknown_codes = set()

    for m in data:
        analyze_match(m)

        # Собираем все f= для статистики
        for r in m.get("responses") or []:
            resp_data = r.get("data") or {}
            for cf in resp_data.get("customFactors") or []:
                for f in cf.get("factors") or []:
                    fc = f.get("f")
                    if fc is not None:
                        total_f_codes[fc] += 1
                        if fc not in KNOWN_CODES:
                            unknown_codes.add(fc)

    # ── Итоговая сводка ──
    print(f"\n{'=' * 78}")
    print(f"📊 ИТОГОВАЯ СВОДКА")
    print(f"{'=' * 78}")
    print(f"\nВсе f= коды во всех матчах:")
    for fc, n in sorted(total_f_codes.items()):
        label = KNOWN_CODES.get(fc, "❓ НЕИЗВЕСТНЫЙ")
        mark = "" if fc in KNOWN_CODES else " ⬅ НОВЫЙ"
        print(f"  f={fc:>6} ({n:>4} шт.) — {label}{mark}")

    if unknown_codes:
        print(f"\n🆕 Неизвестные коды (надо разобрать):")
        for fc in sorted(unknown_codes):
            print(f"  f={fc}")
    else:
        print(f"\n✅ Все коды известны из бэкенда")

    print(f"\n{'=' * 78}")


if __name__ == "__main__":
    main()