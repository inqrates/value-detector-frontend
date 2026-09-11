# ui/after_goal/baltbet.py
import logging
from playwright.async_api import Page
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

class BaltbetHandler(BookmakerHandler):
    @staticmethod
    async def setup_listener(page: Page, callback):
        logger.warning("BaltbetHandler: перехват не реализован")
        pass

    @staticmethod
    async def stop_listener(page: Page):
        pass

    @staticmethod
    def parse_update(data):
        return None

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        return {"success": False, "error": "Baltbet не реализован"}