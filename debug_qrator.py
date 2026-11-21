# debug_qrator.py
"""
Отладка Qrator: проверяем, проходит ли профиль AdsPower
в чистом виде vs через Playwright (как в проде).

Запуск:
    python debug_qrator.py <profile_id> <api_key>

Пример:
    python debug_qrator.py k1iofyfk 967075ceeb71e3d88ce243bc2f9a29e7008b5e4ca52f4ae6

Что делает:
  - Запускает профиль AdsPower
  - Сценарий A: использует контекст AdsPower КАК ЕСТЬ (без патчей)
  - Сценарий B: создает НОВЫЙ контекст с патчами из browser_manager.py
  - Ждет qrator_jsr, показывает cookies, IP, fingerprint
  - Дает четкий вердикт
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp
from playwright.async_api import async_playwright

ADSPOWER_API = "http://localhost:50325"
TARGET_URL = "https://www.ligastavok.ru/live"
IP_URL = "https://api.ipify.org?format=json"
WAIT_SEC = 45.0

# Патчи из browser_manager.py (для сценария B)
ANTIDETECT_JS = r"""
try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch (e) {}
try { Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU','ru','en-US','en'] }); } catch (e) {}
try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 }); } catch (e) {}
try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch (e) {}
try {
    Object.defineProperty(navigator, 'plugins', {
        get: () => [{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}]
    });
} catch (e) {}
try { if (!window.chrome) window.chrome = {}; if (!window.chrome.runtime) window.chrome.runtime = {}; } catch (e) {}
try {
    const patchGL = (proto) => {
        const orig = proto.getParameter;
        proto.getParameter = function(p) {
            if (p === 37445) return 'Intel Inc.';
            if (p === 37446) return 'Intel Iris OpenGL Engine';
            return orig.call(this, p);
        };
    };
    patchGL(WebGLRenderingContext.prototype);
    patchGL(WebGL2RenderingContext.prototype);
} catch (e) {}
try {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
        if (params && params.name === 'notifications') return Promise.resolve({state:'default'});
        return origQuery(params);
    };
} catch (e) {}
"""

BROWSER_MANAGER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


async def adspower_start(profile_id: str, api_key: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{ADSPOWER_API}/api/v2/browser-profile/start",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "profile_id": profile_id,
                "headless": "0",
                "last_opened_tabs": "0",
                "proxy_detection": "1",
                "cdp_mask": "1",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"AdsPower start: {data}")
            ws = (data.get("data") or {}).get("ws", {}).get("puppeteer")
            if not ws:
                raise RuntimeError(f"Нет ws.puppeteer: {data}")
            return ws


async def adspower_stop(profile_id: str, api_key: str):
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"{ADSPOWER_API}/api/v2/browser-profile/stop",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"profile_id": profile_id},
                timeout=aiohttp.ClientTimeout(total=20),
            )
    except Exception:
        pass


async def fingerprint(page) -> dict:
    try:
        return await page.evaluate(r"""() => {
            const out = {};
            out.ua = navigator.userAgent;
            out.webdriver = String(navigator.webdriver);
            out.langs = (navigator.languages||[]).slice(0,3);
            try {
                const c = document.createElement('canvas');
                const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
                if (gl) {
                    out.glVendor = gl.getParameter(gl.VENDOR);
                    out.glRenderer = gl.getParameter(gl.RENDERER);
                }
            } catch(e) { out.glErr = String(e); }
            return out;
        }""")
    except Exception as e:
        return {"error": str(e)}


async def wait_qrator(page, label: str, timeout=WAIT_SEC):
    print(f"\n⏳ [{label}] ждем Qrator (до {timeout:.0f}с)...")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        await asyncio.sleep(2.0)
        elapsed = time.monotonic() - t0
        try:
            cookies = await page.context.cookies()
        except Exception:
            cookies = []
        names = {(c.get("name") or "").lower() for c in cookies}
        has_jsr = "qrator_jsr" in names
        try:
            title = await page.title() or ""
        except Exception:
            title = ""
        try:
            body_len = len(await page.locator("body").inner_text())
        except Exception:
            body_len = 0
        blocked = any(m in title.lower() for m in (
            "проверка","qrator","challenge","just a moment","ddos-guard","безопасности"
        ))
        print(f"  [{elapsed:4.1f}с] cookies={len(cookies):3d} jsr={has_jsr} "
              f"blocked={blocked} title={title[:45]!r} body={body_len}")
        if has_jsr and not blocked and body_len > 500:
            print(f"  ✅ [{label}] Qrator пройден за {elapsed:.1f}с")
            return True
    print(f"  ❌ [{label}] Qrator НЕ пройден за {timeout:.0f}с")
    return False


async def run_scenario(
    label: str,
    ws: str,
    with_patches: bool,
    fresh_context: bool,
):
    print("\n" + "=" * 70)
    print(f"СЦЕНАРИЙ: {label}")
    print(f"   fresh_context={fresh_context}, with_patches={with_patches}")
    print("=" * 70)

    pw = await async_playwright().start()
    result = {"label": label, "passed": False, "ip": None,
              "fp_before": None, "fp_after": None}

    try:
        browser = await pw.chromium.connect_over_cdp(ws)

        if fresh_context:
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                no_viewport=False,
                ignore_https_errors=True,
                user_agent=BROWSER_MANAGER_UA if with_patches else None,
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                device_scale_factor=1,
                java_script_enabled=True,
            )
            if with_patches:
                await ctx.add_init_script(ANTIDETECT_JS)
            page = await ctx.new_page()
        else:
            # Используем существующий контекст AdsPower как есть
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()

        # IP
        try:
            r = await page.goto(IP_URL, timeout=15000)
            if r and r.status == 200:
                ip_data = await r.json()
                result["ip"] = ip_data.get("ip")
                print(f"🌐 IP: {result['ip']}")
        except Exception as e:
            print(f"⚠️ IP: {e}")

        # Fingerprint до захода на Ligastavok
        fp = await fingerprint(page)
        result["fp_before"] = fp
        print(f"🔍 Fingerprint ДО Ligastavok:")
        print(f"   UA:        {fp.get('ua')}")
        print(f"   webdriver: {fp.get('webdriver')}")
        print(f"   GL vendor: {fp.get('glVendor')}")
        print(f"   GL render: {fp.get('glRenderer')}")

        # Заход на Ligastavok
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"⚠️ goto: {e}")

        # Ждем Qrator
        passed = await wait_qrator(page, label)
        result["passed"] = passed

        # Fingerprint после
        result["fp_after"] = await fingerprint(page)

        # Cookies Ligastavok
        try:
            cookies = await page.context.cookies()
            lsv = [c for c in cookies if "ligastavok" in (c.get("domain") or "")]
            print(f"\n🍪 Cookies ligastavok ({len(lsv)}):")
            for c in lsv:
                print(f"   {c['name']} = {c['value'][:30]}...")
        except Exception:
            pass

        # Скриншот при провале
        if not passed:
            shot = Path(f"qrator_fail_{label.replace(' ','_')}.png")
            try:
                await page.screenshot(path=str(shot), full_page=False)
                print(f"📸 Скриншот провала: {shot}")
            except Exception:
                pass

        try:
            await browser.close()
        except Exception:
            pass

    finally:
        try:
            await pw.stop()
        except Exception:
            pass

    return result


async def main():
    if len(sys.argv) < 3:
        print("Использование: python debug_qrator.py <profile_id> <api_key>")
        sys.exit(1)

    profile_id = sys.argv[1].strip()
    api_key = sys.argv[2].strip()

    print(f"🔍 Отладка Qrator")
    print(f"   profile: {profile_id}")
    print(f"   api_key: {api_key[:10]}...{api_key[-4:]}")

    results = []

    # ── Сценарий A: чистый AdsPower ──
    try:
        ws = await adspower_start(profile_id, api_key)
        r = await run_scenario(
            "A (чистый AdsPower, без патчей)",
            ws, with_patches=False, fresh_context=False,
        )
        results.append(r)
        await adspower_stop(profile_id, api_key)
    except Exception as e:
        print(f"💥 Сценарий A упал: {e}")
        import traceback; traceback.print_exc()

    await asyncio.sleep(3)

    # ── Сценарий B: как в проде (с патчами, свежий контекст) ──
    try:
        ws = await adspower_start(profile_id, api_key)
        r = await run_scenario(
            "B (с патчами browser_manager.py)",
            ws, with_patches=True, fresh_context=True,
        )
        results.append(r)
        await adspower_stop(profile_id, api_key)
    except Exception as e:
        print(f"💥 Сценарий B упал: {e}")
        import traceback; traceback.print_exc()

    # ── ИТОГ ──
    print("\n" + "=" * 70)
    print("🎯 ИТОГ")
    print("=" * 70)

    a = results[0] if len(results) > 0 else {}
    b = results[1] if len(results) > 1 else {}
    ok_a = a.get("passed", False)
    ok_b = b.get("passed", False)

    print(f"A (чистый AdsPower):      {'✅ ПРОШЁЛ' if ok_a else '❌ НЕ прошёл'}")
    print(f"B (с патчами):            {'✅ ПРОШЁЛ' if ok_b else '❌ НЕ прошёл'}")
    print(f"IP (A): {a.get('ip')}")
    print(f"IP (B): {b.get('ip')}")

    print("\n💡 ВЕРДИКТ:")

    if ok_a and ok_b:
        print("  ✅ ОБА сценария проходят Qrator.")
        print("  → Проблема в продакшене не в Qrator, а в чём-то ещё:")
        print("    - target timing (Qrator не успевает за 24с)")
        print("    - TargetClosedError (AdsPower закрывает профиль)")
        print("    - проверь errors.log на 'Qrator не пройден за 24с'")
    elif ok_a and not ok_b:
        print("  🎯 ПРОБЛЕМА В НАШИХ ПАТЧАХ (browser_manager.py).")
        print("  → AdsPower сам проходит, Playwright его ломает.")
        print("  → Сравни fp_before (A vs B):")
        print(f"     A: UA={a.get('fp_before',{}).get('ua','?')[:60]}")
        print(f"        webdriver={a.get('fp_before',{}).get('webdriver')}")
        print(f"        GL={a.get('fp_before',{}).get('glVendor')}")
        print(f"     B: UA={b.get('fp_before',{}).get('ua','?')[:60]}")
        print(f"        webdriver={b.get('fp_before',{}).get('webdriver')}")
        print(f"        GL={b.get('fp_before',{}).get('glVendor')}")
        print("  → Что делать:")
        print("     1. В browser_manager.py УБРАТЬ патч WebGL (Intel vs AMD)")
        print("     2. В browser_manager.py УБРАТЬ user_agent= (UA конфликт)")
        print("     3. УБРАТЬ линейные движения мыши в after_goal_engine.py")
    elif not ok_a and not ok_b:
        print("  🎯 ПРОБЛЕМА В ПРОФИЛЕ ADSPOWER (прокси или fingerprint).")
        print("  → Даже чистый профиль не проходит Qrator.")
        print("  → Что проверить:")
        print("     1. Прокси 212.18.100.142 — это дата-центр или резидент?")
        print("        Дата-центр → Qrator блокирует. Нужен резидент.")
        print("     2. IP проверь на https://whatismyipaddress.com — не забанен ли.")
        print("     3. В AdsPower профиле: Fingerprint → Canvas/WebGL = Real.")
        print("     4. Timezone = Europe/Moscow, Language = ru-RU.")
    else:
        print("  ⚠️ Странно: A упал, B прошёл.")
        print("  → Скорее всего, IP сменился между тестами. Повтори.")

    # Сохраняем полный отчёт
    try:
        path = Path(f"qrator_debug_{int(time.time())}.json")
        path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n💾 Полный отчёт: {path}")
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())