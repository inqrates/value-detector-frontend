# ui/after_goal/leon.py
import json
import logging
import re
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)

class LeonHandler(BookmakerHandler):
    _callback = None
    _page = None
    _match_id = None

    @staticmethod
    async def setup_listener(page: Page, callback, match_id: int = None):
        LeonHandler._page = page
        LeonHandler._callback = callback
        if match_id:
            LeonHandler._match_id = match_id
        else:
            url = page.url
            parts = url.split('/')
            for part in parts:
                if part.isdigit() and len(part) >= 15:
                    LeonHandler._match_id = int(part)
                    break
            if not LeonHandler._match_id:
                logger.warning("Не удалось определить match_id для Leon")
        page.on("response", LeonHandler._on_response)
        logger.info(f"Leon: перехват установлен для match_id={LeonHandler._match_id}")

    @staticmethod
    async def stop_listener(page: Page):
        try:
            page.remove_listener("response", LeonHandler._on_response)
        except:
            pass
        LeonHandler._callback = None
        logger.info("Leon: перехват остановлен")

    @staticmethod
    async def _on_response(response: Response):
        url = response.url
        if '/api-1' in url:
            try:
                data = await response.json()
                parsed = LeonHandler.parse_update(data)
                if parsed and LeonHandler._callback:
                    await LeonHandler._callback(parsed)
            except Exception as e:
                logger.error(f"Leon ошибка: {e}")

    @staticmethod
    def parse_update(data: dict) -> dict:
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

            set_markets = {}
            outcome_ids = {}

            if market_type == 'winner':
                # Создаём структуру с проверкой
                if set_key not in set_markets:
                    set_markets[set_key] = {}
                if 'winner' not in set_markets[set_key]:
                    set_markets[set_key]['winner'] = {}
                set_markets[set_key]['winner'][side] = odds

                if set_key not in outcome_ids:
                    outcome_ids[set_key] = {}
                if 'winner' not in outcome_ids[set_key]:
                    outcome_ids[set_key]['winner'] = {}
                outcome_ids[set_key]['winner'][side] = {
                    'event': event,
                    'market': market,
                    'runner': runner,
                    'odds': odds,
                }
            elif market_type == 'total':
                # Для тотала нужно два исхода: over и under. Здесь только один, пропускаем.
                pass
            elif market_type == 'handicap':
                # Аналогично
                pass

            return {
                "match_id": event,
                "set_markets": set_markets,
                "outcome_ids": outcome_ids,
                "score1": 0,
                "score2": 0,
                "sub_score1": 0,
                "sub_score2": 0,
            }
        return None

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        # TODO: доделать после получения doBet
        return {"success": False, "error": "Leon: place_bet не реализован"}