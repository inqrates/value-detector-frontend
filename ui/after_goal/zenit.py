# ui/after_goal/zenit.py
import json
import logging
from playwright.async_api import Page, WebSocket
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


class ZenitHandler(BookmakerHandler):
    _callback = None
    _page = None
    _match_id = None

    # ==========================================================
    # Перехват WS-кадров со страницы матча.
    # Из backend-парсера zenit_api.py известно:
    #   - WS endpoint: wss://zenit.win/wss
    #   - формат:      {"t": 21, "d": {"matches": {gid: {...}}}}
    #   - базовые рынки в odds: "1" (П1), "3" (П2), "7"/"8" (форы), "9"/"10" (тоталы)
    # ==========================================================
    @staticmethod
    async def setup_listener(page: Page, callback, match_id: int = None):
        ZenitHandler._page = page
        ZenitHandler._callback = callback
        ZenitHandler._match_id = match_id
        page.on("websocket", ZenitHandler._on_websocket)
        logger.info(f"Zenit: WS-перехват установлен (match_id={match_id})")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("websocket", ZenitHandler._on_websocket)
        except Exception:
            pass
        ZenitHandler._callback = None

    @staticmethod
    def _on_websocket(ws: WebSocket):
        if "zenit.win/wss" in ws.url:
            logger.info(f"Zenit: WebSocket подключён {ws.url}")
            ws.on("framereceived", ZenitHandler._on_frame)

    @staticmethod
    def _on_frame(payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="replace")
            if not payload or not payload.startswith("{"):
                return
            data = json.loads(payload)
            if data.get("t") != 21:
                return

            parsed = ZenitHandler.parse_update(data)
            if parsed and ZenitHandler._callback:
                import asyncio
                asyncio.create_task(ZenitHandler._callback(parsed))
        except json.JSONDecodeError:
            return
        except Exception as e:
            logger.debug(f"Zenit WS ошибка: {e}")

    # ==========================================================
    # Парсер пакета t=21.
    #
    # TODO: заполнить, когда будет снят WS-кадр с непустым odds.
    # Что известно:
    #   - data["d"]["matches"][gid] содержит:
    #       team1, team2, score, sScore, odds
    #   - odds — словарь вида {"1": {...}, "3": {...}, "7": {...}, ...}
    #     где "1"/"3" — П1/П2, "7"/"8" — форы, "9"/"10" — тоталы
    #     каждый элемент: {"cf": float, "oddKey": "..."} 
    #
    # Что НЕ известно (нужно из HAR/WS-лога):
    #   - какие ключи correspond базовым рынкам партии (не матча)
    #   - где лежит ID исхода для place_bet
    #
    # Формат возврата — как у Marathon:
    # {
    #     "match_id": str,
    #     "event_id": int,
    #     "player1": str, "player2": str,
    #     "score1": int, "score2": int,
    #     "sub_score1": int, "sub_score2": int,
    #     "set_markets": {"set_N": {"winner": {...}, "total": {...}, "handicap": {...}}},
    #     "outcome_ids": {"set_N": {"winner": {"1": {...}, "2": {...}}, ...}},
    # }
    # ==========================================================
    @staticmethod
    def parse_update(data: dict):
        """
        Пока возвращает None — ставки не будут отправляться,
        но система продолжит работать (в логе будет 'Нет идентификатора для исхода').

        После получения WS-кадра с рынками замени тело на реальный парсинг.
        """
        # TODO: реализовать после снятия WS-лога с zenit.win
        return None

    # ==========================================================
    # Отправка ставки.
    #
    # TODO: заменить endpoint, headers и body после HAR со ставки.
    # Что нужно снять (F12 → Network → Fetch/XHR → Copy as fetch):
    #   - точный URL POST (вероятно /ajax/bet/make или /api/bet/place)
    #   - Content-Type
    #   - обязательные поля body (bet, amount, accept_odds, csrf?)
    #   - структура response (где betId / status / error)
    #
    # Пока bet_data пустой, функция честно возвращает ошибку.
    # ==========================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """
        bet_data (после реализации parse_update будет содержать):
          - outcome_id (str|int) — идентификатор исхода
          - amount     (float)   — сумма ставки
        """
        # TODO: раскомментировать и заполнить после HAR.
        # Пока — заглушка, чтобы пайплайн не падал.
        #
        # script = f"""
        # (async function() {{
        #     const d = {json.dumps(bet_data)};
        #     try {{
        #         const resp = await fetch('https://zenit.win/ajax/bet/make', {{    // TODO: URL
        #             method: 'POST',
        #             headers: {{
        #                 'Content-Type': 'application/json',                        // TODO: точный CT
        #                 'X-Requested-With': 'XMLHttpRequest',
        #                 'Origin': 'https://zenit.win',
        #                 'Referer': location.href
        #                 // TODO: возможно X-CSRF-Token, X-Api-Key и т.п.
        #             }},
        #             credentials: 'include',
        #             body: JSON.stringify({{
        #                 // TODO: точная схема по HAR
        #                 bet: d.outcome_id,
        #                 amount: d.amount,
        #                 accept_odds: true
        #             }})
        #         }});
        #         const j = await resp.json();
        #         if (j && (j.success || j.status === 'ok')) {{
        #             return {{ success: true, betId: j.ticket_id || j.id || null }};
        #         }}
        #         return {{ success: false, error: JSON.stringify(j) }};
        #     }} catch(e) {{
        #         return {{ success: false, error: e.message }};
        #     }}
        # }})();
        # """
        # return await page.evaluate(script)

        return {
            "success": False,
            "error": "Zenit: place_bet не реализован (нужен HAR со ставки)",
        }