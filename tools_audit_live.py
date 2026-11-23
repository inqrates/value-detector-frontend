# -*- coding: utf-8 -*-
"""
Аудит: сверка live-данных сервера с логикой определения партии
в клиентском движке (ui/after_goal_engine.py).

Только ЧИТАЕТ сервер, ничего не меняет.

Запуск:
    venv\\Scripts\\python.exe tools_audit_live.py
"""
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

BASE = "https://62.113.115.185:8000"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# Пороги движка (ui/after_goal_engine.py)
SLOW_DATA_STALE_SEC = 5.0
FAST_STATE_STALE_SEC = 6.0
MAX_ACCEPTABLE_DELAY = 30.0

# PHASE_END_BY_SCORE / BY_TIME (ui/after_goal/sports.py)
PHASE_END_BY_SCORE = {"table_tennis", "volleyball", "beach_volleyball"}
PHASE_END_BY_TIME = {"basketball", "cyber_basketball"}


def get(path, params=None, timeout=15):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "audit"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_phase_num(phase_name):
    """Ровно как движок: ui/after_goal/sports.py:parse_phase_num"""
    if not phase_name:
        return 0
    m = re.search(r'(\d+)', str(phase_name))
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def main():
    print("=" * 70)
    print("АУДИТ LIVE-ДАННЫХ vs ЛОГИКА ПАРТИЙ ДВИЖКА")
    print("=" * 70)

    matches = get("/matches")
    print(f"\nМатчей в /matches: {len(matches)}")

    # ── Собираем match_state по каждому матчу ──
    states = []
    errs = 0
    for m in matches:
        key = m.get("key", "")
        if "::" not in key:
            continue
        sport, rest = key.split("::", 1)
        if "||" not in rest:
            continue
        p1, p2 = rest.split("||", 1)
        try:
            st = get("/match_state", {"sport": sport, "player1": p1, "player2": p2})
            if st.get("found"):
                st["_sport"] = sport
                st["_key"] = key
                st["_orig_p1"] = m.get("player1")
                st["_orig_p2"] = m.get("player2")
                states.append(st)
        except Exception as e:
            errs += 1
        time.sleep(0.05)   # не душим сервер

    print(f"Получено состояний: {len(states)}  (ошибок: {errs})")

    # ══════════════════════════════════════════════════════════
    # 1. Формат raw_time по видам спорта и БК
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("1. ФОРМАТ raw_time (движок парсит ЦИФРУ из него)")
    print("=" * 70)

    raw_variants = defaultdict(Counter)
    empty_raw = Counter()
    non_numeric_raw = defaultdict(list)

    for st in states:
        sport = st["_sport"]
        now = st.get("now", time.time())
        for bk, b in st["bks"].items():
            rt = (b.get("raw_time") or "").strip()
            if not rt:
                empty_raw[(sport, bk)] += 1
                continue
            # нормализуем: убираем цифры, смотрим шаблон
            shape = re.sub(r'\d+', 'N', rt)
            raw_variants[(sport, bk)][shape] += 1
            if parse_phase_num(rt) == 0:
                non_numeric_raw[sport].append((bk, rt))

    for (sport, bk), cnt in sorted(raw_variants.items()):
        print(f"  {sport:18s} {bk:12s} {dict(cnt)}")

    if empty_raw:
        print("\n  ПУСТОЙ raw_time (движок тогда считает партию ИЗ СЧЁТА):")
        for (sport, bk), c in sorted(empty_raw.items()):
            print(f"    {sport:18s} {bk:12s} x{c}")

    if non_numeric_raw:
        print("\n  ⚠️ raw_time БЕЗ цифры (parse_phase_num даст 0):")
        for sport, lst in non_numeric_raw.items():
            for bk, rt in lst[:10]:
                print(f"    {sport:18s} {bk:12s} raw_time={rt!r}")

    # ══════════════════════════════════════════════════════════
    # 2. Согласованность фаз между БК в одном матче
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("2. ФАЗА: raw_time vs score1+score2+1 (движок подставляет второе,")
    print("   если первое 0). Расхождение = риск ставки не в ту партию.")
    print("=" * 70)

    phase_mismatch = []
    score_from_total = []
    multi_bk = 0

    for st in states:
        sport = st["_sport"]
        bks = st["bks"]
        if len(bks) < 2:
            continue
        multi_bk += 1

        computed = {}
        for bk, b in bks.items():
            rt = b.get("raw_time") or ""
            f1 = parse_phase_num(rt)
            s1 = int(b.get("score1") or 0)
            s2 = int(b.get("score2") or 0)
            from_score = s1 + s2 + 1
            computed[bk] = {
                "rt_phase": f1,
                "score_phase": from_score,
                "score": (s1, s2),
                "sub": (int(b.get("sub_score1") or 0),
                        int(b.get("sub_score2") or 0)),
                "raw": rt,
            }

        # (a) внутри одной БК: raw_time-фаза != score-фаза
        for bk, c in computed.items():
            if c["rt_phase"] and c["rt_phase"] != c["score_phase"]:
                phase_mismatch.append(
                    (st["_key"], sport, bk, c["raw"],
                     c["rt_phase"], c["score_phase"], c["score"], c["sub"]))

        # (b) между БК: разные фазы
        phases = {bk: c["rt_phase"] for bk, c in computed.items() if c["rt_phase"]}
        if len(set(phases.values())) > 1:
            print(f"\n  РАЗНЫЕ ФАЗЫ {sport} {st['_key']}:")
            for bk, c in computed.items():
                print(f"      {bk:12s} rt={c['rt_phase']:3d} "
                      f"score-фаза={c['score_phase']:3d} "
                      f"score={c['score']} sub={c['sub']} raw={c['raw']!r}")

    print(f"\nМатчей с 2+ БК: {multi_bk}")
    if phase_mismatch:
        print(f"\n  ⚠️ raw_time vs score+1 расходятся: {len(phase_mismatch)}")
        for x in phase_mismatch[:25]:
            k, sp, bk, raw, rp, scp, sc, sb = x
            print(f"    [{sp}] {k} {bk}: raw={raw!r}→{rp} но score{sc}→{scp} sub={sb}")
    else:
        print("  ✅ raw_time и score+1 согласованы везде")

    # ══════════════════════════════════════════════════════════
    # 3. Баскетбол: score = очки, а НЕ партии → формула движка ломается
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("3. PHASE_END_BY_TIME (баскетбол): score1+score2+1 = НЕ номер фазы")
    print("=" * 70)

    for st in states:
        sport = st["_sport"]
        if sport not in PHASE_END_BY_TIME:
            continue
        for bk, b in st["bks"].items():
            s1 = int(b.get("score1") or 0)
            s2 = int(b.get("score2") or 0)
            rt = (b.get("raw_time") or "").strip()
            rp = parse_phase_num(rt)
            fake = s1 + s2 + 1
            if rp == 0:
                print(f"  ⚠️ [{sport}] {st['_key'][:32]} {bk:10s} raw={rt!r} → 0, "
                      f"движок подставит score1+score2+1 = {fake} (абсурд)")
            elif fake > 20:
                print(f"  ℹ️ [{sport}] {st['_key'][:32]} {bk:10s} raw={rt!r}→{rp}, "
                      f"score-формула дала бы {fake}")

    # ══════════════════════════════════════════════════════════
    # 4. Реальные задержки по очкам — что движок бы поймал
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("4. ЗАДЕРЖКИ МЕЖДУ БК (движок: diff = max(f_sub-s_sub по каждому)")
    print("=" * 70)

    gaps = []
    for st in states:
        sport = st["_sport"]
        now = st.get("now", time.time())
        bks = st["bks"]
        if len(bks) < 2:
            continue

        fresh = {bk: b for bk, b in bks.items()
                 if now - float(b.get("timestamp") or 0) <= MAX_ACCEPTABLE_DELAY}
        if len(fresh) < 2:
            continue

        for bk_a, a in fresh.items():
            for bk_b, b in fresh.items():
                if bk_a >= bk_b:
                    continue
                pa = parse_phase_num(a.get("raw_time") or "")
                pb = parse_phase_num(b.get("raw_time") or "")
                if pa == 0 or pb == 0:
                    continue
                if pa != pb:
                    continue   # разные партии — отдельная логика
                sa1 = int(a.get("sub_score1") or 0); sa2 = int(a.get("sub_score2") or 0)
                sb1 = int(b.get("sub_score1") or 0); sb2 = int(b.get("sub_score2") or 0)
                d = max(sa1 - sb1, sa2 - sb2)
                d2 = max(sb1 - sa1, sb2 - sa2)
                if d >= 2:
                    gaps.append((sport, st["_key"], bk_a, bk_b, pa,
                                 (sa1, sa2), (sb1, sb2), d, "a→b"))
                elif d2 >= 2:
                    gaps.append((sport, st["_key"], bk_b, bk_a, pa,
                                 (sb1, sb2), (sa1, sa2), d2, "b→a"))

    gaps.sort(key=lambda x: -x[7])
    print(f"\nЗадержек >=2 очка в ОДНОЙ партии: {len(gaps)}")
    for g in gaps[:25]:
        sp, k, fast, slow, ph, fs, ss, d, _ = g
        print(f"  [{sp:13s}] фаза{ph} {fast:10s} {fs} → {slow:10s} {ss}  +{d}")

    # ══════════════════════════════════════════════════════════
    # 5. Свежесть данных (SLOW_DATA_STALE_SEC=5, FAST=6)
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("5. СВЕЖЕСТЬ: движок считает stale если now-ts > 5с(slow)/6с(fast)")
    print("=" * 70)

    stale_stats = defaultdict(list)
    for st in states:
        now = st.get("now", time.time())
        for bk, b in st["bks"].items():
            age = now - float(b.get("timestamp") or 0)
            stale_stats[bk].append(age)

    print(f"{'bk':14s} {'n':>5s} {'median':>8s} {'p90':>8s} {'max':>8s}  stale>5с")
    for bk, ages in sorted(stale_stats.items()):
        ages.sort()
        n = len(ages)
        med = ages[n // 2]
        p90 = ages[min(n - 1, int(n * 0.9))]
        mx = ages[-1]
        st5 = sum(1 for a in ages if a > 5)
        flag = "  ⚠️" if st5 / n > 0.3 else ""
        print(f"{bk:14s} {n:5d} {med:8.1f} {p90:8.1f} {mx:8.1f} {st5:5d}/{n}{flag}")

    # ══════════════════════════════════════════════════════════
    # 6. Тотал-линии: sanity-проверка движка
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("6. ТОТАЛ-ЛИНИИ vs max_phase_total движка (is_phase_line_sane)")
    print("=" * 70)
    print("   table_tennis max=40, volleyball max=52/32, basketball max=80")
    print("   Если линия больше — движок ОТВЕРГНЕТ рынок как 'не партийный'.")

    over = defaultdict(list)
    for st in states:
        sport = st["_sport"]
        for bk, b in st["bks"].items():
            line = float(b.get("total_line") or 0)
            if line <= 0:
                continue
            s1 = int(b.get("score1") or 0); s2 = int(b.get("score2") or 0)
            phase = max(parse_phase_num(b.get("raw_time") or ""), s1 + s2 + 1)
            if sport == "table_tennis":
                mx = 40
            elif sport in ("volleyball", "beach_volleyball"):
                mx = 32 if phase >= 5 else 52
            elif sport in ("basketball", "cyber_basketball"):
                mx = 80
            else:
                mx = 100
            if abs(line) > mx:
                over[(sport, bk)].append((line, mx, phase, st["_key"][:34]))

    if over:
        print(f"\n  ⚠️ Линий, ОТВЕРГНУТЫХ sanity-фильтром: "
              f"{sum(len(v) for v in over.values())}")
        for (sp, bk), lst in sorted(over.items()):
            print(f"    [{sp}] {bk}: {len(lst)} шт, напр "
                  f"line={lst[0][0]} max={lst[0][1]} phase={lst[0][2]} {lst[0][3]}")
    else:
        print("\n  ✅ Все линии проходят sanity-фильтр")

    print("\n" + "=" * 70)
    print("АУДИТ ЗАВЕРШЁН")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
