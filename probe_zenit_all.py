"""
Пробник Zenit v2 — кликает по матчу в списке, сохраняя все WS фреймы.
"""
import asyncio
import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from core.adspower_browser import AdsPowerBrowser

OUT = Path("probe_dumps")
OUT.mkdir(exist_ok=True)

API_KEY = "967075ceeb71e3d88ce243bc2f9a29e7008b5e4ca52f4ae6"
PROFILES = {"zenit": "k1h5id2u"}

SPORTS = {
    "table_tennis": {"url": "https://zenit.win/live/134", "sid": "134"},
    "volleyball":   {"url": "https://zenit.win/live/41",  "sid": "41"},
    "basketball":   {"url": "https://zenit.win/live/28",  "sid": "28"},
}

CAPTURE_SECONDS = 40


async def capture_sport(page, sport_key, cfg):
    print(f"\n{'═'*90}")
    print(f"🏆 {sport_key}  ({cfg['url']})")
    print(f"{'═'*90}")

    sport_ws = []

    def on_websocket(ws):
        if "zenit.win/wss" not in ws.url:
            return
        def on_frame(payload):
            try:
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8", "ignore")
                sport_ws.append(payload)
            except Exception:
                pass
        ws.on("framereceived", on_frame)

    page.on("websocket", on_websocket)

    print(f"  🔗 {cfg['url']}")
    try:
        await page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  ⚠️ goto: {e}")

    await asyncio.sleep(5)

    # Кликаем по первому матчу в списке через DOM
    print("  🔍 Ищу матч в DOM...")
    try:
        # Ищем ссылки на матчи (обычно это <a> с href содержащим /live/)
        match_link = await page.query_selector(f'a[href*="/live/{cfg["sid"]}/"]')
        if match_link:
            href = await match_link.get_attribute("href")
            if href:
                full_url = href if href.startswith("http") else f"https://zenit.win{href}"
                print(f"  🎯 Кликнул по матчу: {full_url}")
                await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
        else:
            print("  ⚠️ Не нашёл ссылку на матч, пробуем JS...")
            # Пытаемся извлечь URL через JS
            match_url = await page.evaluate("""
                () => {
                    const links = document.querySelectorAll('a[href*="/live/"]');
                    for (const link of links) {
                        const href = link.getAttribute('href');
                        if (href && href.split('/').length >= 4) {
                            return href;
                        }
                    }
                    return null;
                }
            """)
            if match_url:
                full_url = match_url if match_url.startswith("http") else f"https://zenit.win{match_url}"
                print(f"  🎯 JS нашёл: {full_url}")
                await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
            else:
                print("  ⚠️ Не удалось найти матч")
    except Exception as e:
        print(f"  ⚠️ Ошибка клика: {e}")

    print(f"  ⏳ Ждём {CAPTURE_SECONDS} сек, листай вкладки маркетов...")
    await asyncio.sleep(CAPTURE_SECONDS)

    return sport_ws


def analyze_sport(sport_key, ws_frames):
    print(f"\n{'═'*90}")
    print(f"📊 АНАЛИЗ: {sport_key}  (WS фреймов: {len(ws_frames)})")
    print(f"{'═'*90}")

    # Группируем по t
    by_t = {}
    samples_by_t = {}
    for raw in ws_frames:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        t = data.get("t")
        by_t[t] = by_t.get(t, 0) + 1
        if t not in samples_by_t:
            samples_by_t[t] = data

    print(f"\n📊 Распределение по 't':")
    for t, cnt in sorted(by_t.items(), key=lambda x: -x[1]):
        print(f"  t={t!r}: {cnt}")

    # Показываем примеры для основных типов
    for t in sorted(samples_by_t.keys()):
        sample = samples_by_t[t]
        print(f"\n⚡ SAMPLE t={t}:")
        print(f"   keys: {list(sample.keys())[:10]}")
        d = sample.get("d") or {}
        if isinstance(d, dict):
            print(f"   d.keys: {list(d.keys())[:15]}")
            if "matches" in d:
                matches = d.get("matches") or {}
                if isinstance(matches, dict):
                    print(f"   matches: {len(matches)} items")
                    for gid, info in list(matches.items())[:1]:
                        print(f"     gid={gid}")
                        print(f"     keys: {list(info.keys())[:20]}")
                        if "score" in info:
                            print(f"     score: {info.get('score')}")
                        if "sScore" in info:
                            ss = info.get("sScore") or {}
                            sdata = ss.get("sScoreData") or {}
                            scs = sdata.get("scs") or []
                            print(f"     sScoreData.scs: {len(scs)} items")
                            for sc in scs[:3]:
                                scv = sc.get("scv") or {}
                                cur = scv.get("cur") or {}
                                print(f"       d={sc.get('d')!r} t1={cur.get('t1')} t2={cur.get('t2')}")
                        if "odds" in info:
                            odds = info.get("odds") or {}
                            print(f"     odds: {len(odds)} bets")
                            # Показываем первые 10 oddKey
                            for bet_id, odd_info in list(odds.items())[:10]:
                                print(f"       {bet_id}: {odd_info.get('oddKey')} → {odd_info.get('cf')}")


async def main():
    pid = PROFILES["zenit"]
    print(f"🚀 Zenit мульти-пробник | профиль {pid}")

    w = AdsPowerBrowser(
        profile_id=pid, api_key=API_KEY,
        api_url="http://localhost:50325", headless=False,
    )
    await w.__aenter__()
    page = await w.new_page()

    all_ws = {}

    for sport_key in ["volleyball", "basketball"]:
        if sport_key not in SPORTS:
            continue
        all_ws[sport_key] = await capture_sport(page, sport_key, SPORTS[sport_key])

    # Сохраняем всё
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = OUT / f"zenit_all_{ts}.json"
    out_file.write_text(
        json.dumps({"ws": all_ws}, ensure_ascii=False, indent=1),
        "utf-8"
    )
    print(f"\n💾 Общий дамп: {out_file.name}")

    # Анализ
    for sport_key, frames in all_ws.items():
        analyze_sport(sport_key, frames)

    await page.close()
    await w.__aexit__(None, None, None)
    print("\n✅ Готово!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ Прервано")