# ui/after_goal/base.py
from playwright.async_api import Page
from typing import Dict, Any, Optional, Callable, Awaitable

class BookmakerHandler:
    """Базовый класс для обработки ставок на конкретной БК."""

    @staticmethod
    async def setup_listener(page: Page, callback: Callable[[Dict], Awaitable[None]]) -> None:
        """Устанавливает перехват обновлений на странице матча."""
        raise NotImplementedError

    @staticmethod
    async def stop_listener(page: Page) -> None:
        """Останавливает перехват обновлений."""
        raise NotImplementedError

    @staticmethod
    def parse_update(data: Any) -> Optional[Dict]:
        """
        Преобразует сырые данные от перехвата в универсальный формат:
        {
            "match_id": int,
            "score1": int,
            "score2": int,
            "sub_score1": int,
            "sub_score2": int,
            "set_markets": {
                "set_1": {"winner": {"1": odd, "2": odd}, ...},
                ...
            },
            "outcome_ids": {
                "set_1": {"winner": {"1": {"id": int, "market_data": str}, ...}, ...},
                ...
            }
        }
        """
        raise NotImplementedError

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """Отправляет ставку, используя сессию браузера."""
        raise NotImplementedError