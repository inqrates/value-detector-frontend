# dry_run_test.py
"""
DRY-RUN тест ставок без реальной отправки.

- Подключается к бекенду через WS
- Запускает AfterGoalEngine в фоне
- Подменяет place_bet во всех хендлерах на мок — реальных API-вызовов нет
- Всё, что «БЫЛО БЫ отправлено», пишется в консоль и в dry_run.log
- AdsPower API-ключ — глобальный (env или хардкод), не нужен в accounts.json
- Ленивая активация профилей: открываются только при первом сигнале

Запуск:
    python dry_run_test.py                          # стратегии из strategies.json (только enabled)
    python dry_run_test.py --force-all              # все стратегии, даже выключенные
    python dry_run_test.py --all-bks                # авто-стратегии для ВСЕХ БК из accounts.json
    python dry_run_test.py --bks pari,ligastavok    # только эти БК (мок + фильтр стратегий)
    python dry_run_test.py --ws ws://localhost:8000/ws
    python dry_run_test.py --api-key XXX            # AdsPower API-ключ вручную

ВАЖНО: перед запуском отключи в основном софте те стратегии, чьи
профили ты хочешь использовать здесь — AdsPower не отдаст профиль дважды.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# ── Минимальный Qt, чтобы ui.log_bus и прочие шины работали ──
try:
    from PyQt5.QtWidgets import QApplication
    _qt_app = QApplication.instance() or QApplication(sys.argv)
except Exception:
    _qt_app = None


# ── Логирование в файл + консоль ──
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("dry_run.log", encoding="utf-8"),
    ],
)
for name in ("playwright", "qasync", "urllib3", "asyncio"):
    logging.getLogger(name).setLevel(logging.WARNING)

logger = logging.getLogger("dry_run")


# ────────────────────────────────────────────────────────────
# Маппинг "человеческое имя БК" из accounts.json → bk_id для движка
# ────────────────────────────────────────────────────────────
BK_NAME_MAP = {
    "fonbet":       "fonbet",
    "pari":         "pari",
    "winline":      "winline",
    "liga stavok":  "ligastavok",
    "ligastavok":   "ligastavok",
    "leon":         "leon",
    "olimp":        "olimp",
    "betcity":      "betcity",
    "marathon":     "marathon",
    "zenit":        "zenit",
    "sportbet":     "sportbet",
}


def _normalize_bk_name(name: str) -> str:
    return (name or "").strip().lower()


def build_strategies_from_accounts() -> list:
    """
    Читает accounts.json и создаёт по одной временной стратегии на каждую БК.
    Берём profile_id из каждого аккаунта. enabled=True в памяти.
    """
    try:
        from ui.paths import get_app_data_dir
    except Exception as e:
        logger.error(f"Не могу импортировать get_app_data_dir: {e}")
        return []

    path = os.path.join(get_app_data_dir(), "accounts.json")
    if not os.path.exists(path):
        logger.error(f"accounts.json не найден: {path}")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            accounts = json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения accounts.json: {e}")
        return []

    strategies = []
    for acc in accounts:
        raw_bk = _normalize_bk_name(acc.get("bk"))
        bk_id = BK_NAME_MAP.get(raw_bk)
        if not bk_id:
            logger.warning(
                f"⚠️ Пропускаю '{acc.get('bk')}' — не знаю маппинг на bk_id"
            )
            continue
        profile_id = acc.get("ads_power_id")
        if not profile_id:
            logger.warning(
                f"⚠️ Пропускаю '{acc.get('bk')}' — нет ads_power_id"
            )
            continue

        strategies.append({
            "name": f"DRY_{bk_id}",
            "type": "After-goal",
            "enabled": True,                    # в памяти — форсим True
            "profile_id": profile_id,
            "bk": bk_id,
            "sport": "any",
            "min_delay": 0.5,
            "min_score_diff": 2,
            "verify_seconds": 0.5,
            "max_bets_per_match": 1,
            "max_bets_per_phase": 1,
            "ignore_repeats": False,
            "markets_enabled": ["winner", "total", "handicap"],
            "bet_direction": "best_odds",
            "winner_sides": "both",
            "total_sides": "both",
            "handicap_sides": "both",
            "bet_size": 50,
            "min_odds": 1.1,
            "max_odds": 10.0,
            "headless": False,
            "market_thresholds": {
                "winner": 2, "total": 1, "handicap": 3, "it": 2,
            },
            "max_bets_per_session": 0,
            "race_enabled": True,   # ← тест с RACE (можно убрать)
        })

    return strategies


# ============================================================
# 1) Мок place_bet — универсальный для static и instance методов
# ============================================================
def _make_mock_place_bet(bk_id: str):
    async def _mock(self_or_page, bet_data_or_kwargs=None, **kwargs):
        if bet_data_or_kwargs is None:
            bet_data = kwargs.get("bet_data", {})
        else:
            bet_data = bet_data_or_kwargs

        logger.warning("=" * 78)
        logger.warning(f"🧪 DRY_RUN [{bk_id}] СТАВКА БЫЛА БЫ ОТПРАВЛЕНА")
        logger.warning(f"   bet_data={json.dumps(bet_data, ensure_ascii=False)}")
        logger.warning("=" * 78)

        return {"success": True, "bet_id": f"DRY_RUN_{bk_id}_{int(time.time())}"}

    return _mock


def _patch_handler(HandlerClass, bk_id: str):
    orig = HandlerClass.__dict__.get("place_bet")
    if orig is None:
        logger.debug(f"[{bk_id}] place_bet не найден в классе, пропускаю")
        return

    mock_fn = _make_mock_place_bet(bk_id)

    if isinstance(orig, staticmethod):
        HandlerClass.place_bet = staticmethod(mock_fn)
    else:
        async def wrapper(self, page, bet_data):
            return await mock_fn(page, bet_data)
        HandlerClass.place_bet = wrapper

    logger.info(f"🔧 [{bk_id}] place_bet замокан (было: {type(orig).__name__})")


def patch_all_handlers(only_bks: list = None):
    from ui.after_goal import (
        FonbetHandler, PariHandler, OlimpHandler, BetcityHandler,
        LeonHandler, ZenitHandler, LigaStavokHandler, WinlineHandler,
        MarathonHandler, SportbetHandler,
    )
    handlers = {
        "fonbet": FonbetHandler,
        "pari": PariHandler,
        "olimp": OlimpHandler,
        "betcity": BetcityHandler,
        "leon": LeonHandler,
        "zenit": ZenitHandler,
        "ligastavok": LigaStavokHandler,
        "winline": WinlineHandler,
        "marathon": MarathonHandler,
        "sportbet": SportbetHandler,
    }

    patched = []
    for bk, cls in handlers.items():
        if only_bks and bk not in only_bks:
            continue
        _patch_handler(cls, bk)
        patched.append(bk)
    return patched


# ============================================================
# 1.5) Патч _get_api_key_for_profile — глобальный ключ
# ============================================================
def patch_engine_api_key(global_key: str):
    """
    Подменяет AfterGoalEngine._get_api_key_for_profile,
    чтобы он всегда возвращал глобальный ключ из .env (или хардкода).
    Проект НЕ трогаем — патч живёт только в этом процессе.
    """
    from ui.after_goal_engine import AfterGoalEngine

    async def _mock_api_key(self, profile_id: str) -> str:
        return global_key

    AfterGoalEngine._get_api_key_for_profile = _mock_api_key
    logger.info(
        f"🔑 ADSPOWER_API_KEY замокан на глобальный: "
        f"{global_key[:12]}...{global_key[-4:]}"
    )


# ============================================================
# 2) WS-клиент
# ============================================================
class DryRunWsClient:
    def __init__(self, ws_url: str, engine, strategy_store):
        self.ws_url = ws_url
        self.engine = engine
        self.store = strategy_store
        self._running = True

    async def run(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets не установлен: pip install websockets")
            return

        while self._running:
            try:
                logger.info(f"🔌 Подключаюсь к {self.ws_url}")
                async with websockets.connect(
                    self.ws_url,
                    max_size=4 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    logger.info("✅ WS подключён")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        msg_type = data.get("type")
                        payload = data.get("payload", {})

                        if msg_type == "signal":
                            await self._on_signal(payload)
                        elif msg_type == "health_restart":
                            bk = payload.get("bk_id")
                            logger.info(f"[health] 🔄 Рестарт парсера: {bk}")
                        elif msg_type == "health":
                            pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"WS ошибка: {type(e).__name__}: {e}")
                await asyncio.sleep(3)

    async def _on_signal(self, payload: dict):
        try:
            is_relevant = self.store.is_signal_relevant(payload)
        except Exception as e:
            logger.debug(f"is_signal_relevant ошибка: {e}")
            return

        if not is_relevant:
            return

        strategy = self.store.get_matching_strategy(payload)
        if not strategy:
            return

        profile_id = strategy.get("profile_id")
        if not profile_id:
            logger.warning(
                f"⚠️ Стратегия '{strategy.get('name')}' не имеет profile_id"
            )
            return

        slow_bk = payload.get("slow_bk")
        teams = payload.get("match_teams", ["?", "?"])
        delay = payload.get("delay", 0)
        logger.info(
            f"📡 СИГНАЛ: [{slow_bk}] {teams[0]} vs {teams[1]} | "
            f"delay={delay}с | стратегия='{strategy.get('name')}'"
        )

        # ── ЛЕНИВАЯ АКТИВАЦИЯ ПРОФИЛЯ ──
        # Профиль не открывается заранее (иначе 9 AdsPower-окон висят).
        # Открываем его при первом сигнале по этой БК.
        try:
            wrapper = await self.engine._get_existing_browser(profile_id)
            if not wrapper:
                logger.info(
                    f"🚀 Активирую профиль {profile_id} для стратегии "
                    f"'{strategy.get('name')}' (ленивая активация)"
                )
                await self.engine.activate_strategy(strategy)
        except Exception as e:
            logger.warning(f"Активация профиля: {e}")

        # ── PREOPEN ──
        try:
            await self.engine.preopen_match_with_profile(payload, strategy)
        except Exception as e:
            logger.error(f"preopen ошибка: {e}", exc_info=True)


# ============================================================
# 3) main
# ============================================================
async def main_async(args):
    from ui.after_goal_engine import AfterGoalEngine
    from ui.strategy_store import StrategyStore
    from ui.config_loader import load_config

    # ── 0. Глобальный AdsPower API-ключ ──
    # Приоритет: --api-key CLI → env ADSPOWER_API_KEY → хардкод дефолт.
    global_api_key = (
        args.api_key
        or os.getenv("ADSPOWER_API_KEY", "").strip()
        or "967075ceeb71e3d88ce243bc2f9a29e7008b5e4ca52f4ae6"
    )
    patch_engine_api_key(global_api_key)

    # ── 1. Мокаем place_bet ──
    logger.info("🔧 Мокаю place_bet во всех хендлерах...")
    only_bks = None
    if args.bks:
        only_bks = [b.strip().lower() for b in args.bks.split(",")]
    patched = patch_all_handlers(only_bks)
    logger.info(f"✅ Замокано: {', '.join(patched)}")

    # ── 2. Загружаем стратегии ──
    store = StrategyStore()

    # ── Фильтр по --bks (если задан) ──
    # --bks ограничивает и мок place_bet, и список стратегий,
    # иначе при --all-bks откроются ВСЕ профили из accounts.json.
    wanted_bks = None
    if args.bks:
        wanted_bks = {b.strip().lower() for b in args.bks.split(",") if b.strip()}
        if not wanted_bks:
            wanted_bks = None

    if args.all_bks:
        in_mem = build_strategies_from_accounts()
        if not in_mem:
            logger.error("❌ Не удалось создать ни одной стратегии из accounts.json")
            return

        if wanted_bks is not None:
            before = len(in_mem)
            in_mem = [
                s for s in in_mem
                if (s.get('bk') or '').lower() in wanted_bks
            ]
            logger.warning(
                f"🎯 --bks фильтр: {sorted(wanted_bks)} → "
                f"{len(in_mem)}/{before} стратегий"
            )
            if not in_mem:
                logger.error(
                    "❌ После фильтра --bks не осталось ни одной стратегии. "
                    "Проверь accounts.json."
                )
                return

        store.strategies = in_mem
        enabled = in_mem
        logger.warning(
            f"⚡ --all-bks: создал {len(enabled)} стратегий из accounts.json"
        )

    elif args.force_all:
        enabled = list(store.strategies)
        for s in store.strategies:
            s["enabled"] = True

        if wanted_bks is not None:
            before = len(enabled)
            enabled = [
                s for s in enabled
                if (s.get('bk') or '').lower() in wanted_bks
            ]
            logger.warning(
                f"🎯 --bks фильтр: {sorted(wanted_bks)} → "
                f"{len(enabled)}/{before} стратегий"
            )

        logger.warning(
            f"⚡ --force-all: беру ВСЕ стратегии ({len(enabled)}), "
            f"даже выключенные; enabled=True в памяти"
        )

    else:
        enabled = [s for s in store.strategies if s.get("enabled")]

        if wanted_bks is not None:
            before = len(enabled)
            enabled = [
                s for s in enabled
                if (s.get('bk') or '').lower() in wanted_bks
            ]
            logger.warning(
                f"🎯 --bks фильтр: {sorted(wanted_bks)} → "
                f"{len(enabled)}/{before} стратегий"
            )

        logger.info(f"📋 Активных стратегий: {len(enabled)}")

    for s in enabled:
        logger.info(
            f"   • '{s.get('name')}' | bk={s.get('bk')} | "
            f"profile={s.get('profile_id')} | sport={s.get('sport')}"
        )

    if not enabled:
        logger.warning("⚠️ Нет активных стратегий — скрипт будет молчать")

    # ── Если задан --bks — выкидываем чужие стратегии из store,
    #    чтобы is_signal_relevant/get_matching_strategy их не видели ──
    if wanted_bks is not None:
        before = len(store.strategies)
        store.strategies = [
            s for s in store.strategies
            if (s.get('bk') or '').lower() in wanted_bks
        ]
        logger.warning(
            f"🎯 store.strategies срезан: {len(store.strategies)}/{before} "
            f"(оставлены только {sorted(wanted_bks)})"
        )

    # ── 3. WS URL ──
    if args.ws:
        ws_url = args.ws
    else:
        cfg = load_config()
        ws_url = cfg.get("ws_url", "ws://62.113.115.185:8000/ws")
    logger.info(f"🌐 WS URL: {ws_url}")

    # ── 4. Движок ──
    engine = AfterGoalEngine()
    ws_client = DryRunWsClient(ws_url, engine, store)

    logger.warning("=" * 78)
    logger.warning("🧪 DRY-RUN РЕЖИМ ЗАПУЩЕН")
    logger.warning("   Ставки НЕ отправляются. Всё логируется в dry_run.log")
    logger.warning("   Ctrl+C для остановки")
    logger.warning("=" * 78)

    try:
        await ws_client.run()
    except KeyboardInterrupt:
        logger.info("⏹ Остановлено пользователем")
    finally:
        logger.info("🛑 Завершаю, глушу движок...")
        try:
            await engine.shutdown()
        except Exception as e:
            logger.warning(f"shutdown: {e}")
        logger.info("✅ Готово")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--ws",
        help="WebSocket URL бекенда (по умолчанию из config.json)",
        default=None,
    )
    ap.add_argument(
        "--api-key",
        help="AdsPower API-ключ (по умолчанию env ADSPOWER_API_KEY или хардкод)",
        default=None,
    )
    ap.add_argument(
        "--bks",
        help="Замокать и оставить только эти БК через запятую "
             "(например, pari,ligastavok)",
        default=None,
    )
    ap.add_argument(
        "--force-all",
        action="store_true",
        help="Брать все стратегии из strategies.json, даже выключенные",
    )
    ap.add_argument(
        "--all-bks",
        action="store_true",
        help="Создать временные стратегии для ВСЕХ БК из accounts.json",
    )
    args = ap.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()