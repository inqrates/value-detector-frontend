# ui/after_goal/leon.py
"""
Leon handler — instance-based.

Перехватывает HTTP-ответы от /api-1 (betSlip) на странице матча.
Состояние slow БК берётся из betSlip.getBatchSlipInfo — это ответ
самого Leon с актуальным рынком «Победитель».
"""
import json
import logging
import re
from typing import Optional, Dict, List, Callable
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


class LeonHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id = str(target_match_id) if target_match_id else None
        self.target_teams = list(target_teams or [])
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._listener = None

    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if self._listener and self._page and not self._page.is_closed():
            try:
                self._page.remove_listener("response", self._listener)
            except Exception:
                pass

        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        # Если match_id не передан — вытаскиваем из URL (числовой id от 15 цифр)
        if not self.target_match_id:
            parts = page.url.split('/')
            for part in parts:
                if part.isdigit() and len(part) >= 15:
                    self.target_match_id = part
                    break

        self._page = page
        self._callback = callback

        async def on_response(response: Response):
            await self._handle_response(response)

        self._listener = on_response
        page.on("response", on_response)
        logger.info(f"Leon: listener установлен (target={self.target_match_id})")

    async def stop_listener(self, page: Page):
        if self._listener:
            try:
                page.remove_listener("response", self._listener)
            except Exception:
                pass
            self._listener = None
        self._callback = None
        logger.info(f"Leon: listener снят (target={self.target_match_id})")

    async def _handle_response(self, response: Response):
        url = response.url

        # ── Баланс Leon ──
        if 'leon.ru/api-1' in url:
            try:
                data = await response.json()

                def _find_balance(obj):
                    if isinstance(obj, dict):
                        if 'balance' in obj and isinstance(
                                obj['balance'], (int, float)):
                            return obj['balance']
                        for v in obj.values():
                            r = _find_balance(v)
                            if r is not None:
                                return r
                    elif isinstance(obj, list):
                        for v in obj:
                            r = _find_balance(v)
                            if r is not None:
                                return r
                    return None

                bal = _find_balance(data)
                if bal is not None:
                    from ui.balance_bus import balance_bus
                    balance_bus.update('leon', bal, 'RUB')
            except Exception:
                pass

        if '/api-1' not in url:
            return
        try:
            data = await response.json()
        except Exception:
            return
        parsed = LeonHandler.parse_update(data)
        if parsed and self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Leon callback: {e}", exc_info=True)

    @staticmethod
    def parse_update(data: dict) -> Optional[dict]:
        """Парсит betSlip-ответ Leon в наш универсальный формат."""
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            queries = value.get('data', {}).get('queries', {})
            bet_slip = queries.get('betSlip', {})
            batch_info = bet_slip.get('getBatchSlipInfo', {})
            if not batch_info:
                continue
            slip_data = batch_info.get('data', {})
            slip_entries = slip_data.get('slipEntries', [])
            if not slip_entries:
                continue
            entry = slip_entries[0]
            if entry.get('status') != 'OK':
                continue
            entries = entry.get('entries', [])
            if not entries:
                continue
            e = entries[0]
            market_name = e.get('marketName', '')
            set_match = re.search(r'(\d+)-й\s+сет', market_name)
            if not set_match:
                return None
            set_num = int(set_match.group(1))
            set_key = f"set_{set_num}"

            if 'Победитель' in market_name:
                market_type = 'winner'
            elif 'Тотал' in market_name:
                market_type = 'total'
            elif 'Фора' in market_name:
                market_type = 'handicap'
            else:
                return None

            runner_name = e.get('runnerName', '')
            odds = e.get('odds', 0.0)
            runner = e.get('runner', 0)
            market = e.get('market', 0)
            event = e.get('event', 0)

            competitors = e.get('competitors', [])
            if len(competitors) == 2:
                if runner_name == competitors[0]:
                    side = '1'
                elif runner_name == competitors[1]:
                    side = '2'
                else:
                    side = runner_name
            else:
                side = runner_name

            set_markets: Dict[str, dict] = {}
            outcome_ids: Dict[str, dict] = {}

            if market_type == 'winner':
                set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = odds
                outcome_ids.setdefault(set_key, {}).setdefault('winner', {})[side] = {
                    'event': event,
                    'market': market,
                    'runner': runner,
                    'odds': odds,
                }
                        # phase_num = номер сета, в котором Leon держит рынки.
            # У нас уже есть set_num из market_name ("N-й сет").
            return {
                "match_id": str(event),
                "set_markets": set_markets,
                "outcome_ids": outcome_ids,
                "phase_num": set_num,      # ← добавили
                "score1": 0,
                "score2": 0,
                "sub_score1": 0,
                "sub_score2": 0,
            }
        return None
        

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        return {"success": False, "error": "Leon: place_bet не реализован"}