# ui/after_goal/zenit.py
import json
import logging
from typing import Optional, List, Callable
from playwright.async_api import Page, WebSocket
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


class ZenitHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id = str(target_match_id) if target_match_id else None
        self.target_teams = list(target_teams or [])
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None

    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        self._page = page
        self._callback = callback
        page.on("websocket", self._on_websocket)
        logger.info(f"Zenit: WS-перехват установлен (target={self.target_match_id})")

    async def stop_listener(self, page: Page):
        try:
            page.remove_listener("websocket", self._on_websocket)
        except Exception:
            pass
        self._callback = None
        logger.info(f"Zenit: listener снят (target={self.target_match_id})")

    def _on_websocket(self, ws: WebSocket):
        if "zenit.win/wss" in ws.url:
            logger.info(f"Zenit: WebSocket подключён {ws.url}")
            ws.on("framereceived", self._on_frame)

    def _on_frame(self, payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="replace")
            if not payload or not payload.startswith("{"):
                return
            data = json.loads(payload)
            if data.get("t") != 21:
                return

            parsed = ZenitHandler.parse_update(data)
            if parsed and self._callback:
                import asyncio
                asyncio.create_task(self._callback(parsed))
        except json.JSONDecodeError:
            return
        except Exception as e:
            logger.debug(f"Zenit WS ошибка: {e}")

    @staticmethod
    def parse_update(data: dict):
        # TODO: реализовать после снятия WS-лога с zenit.win
        return None

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        return {
            "success": False,
            "error": "Zenit: place_bet не реализован (нужен HAR со ставки)",
        }