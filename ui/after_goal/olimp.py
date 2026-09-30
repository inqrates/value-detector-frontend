# ui/after_goal/olimp.py
"""
Olimp handler — instance-based с ЛОКАЛЬНОЙ генерацией x-token.

Алгоритм X-Token (восстановлен из бандла olimp-api.d38ffed06a4e8a2f.js):
    x-token = MD5( sorted_values(body) + ";" + SECRET_CUPIS )
где:
  - sorted_values: ключи body по алфавиту, значения через ";"
  - числа: как JS String(10.0) = "10" (не "10.0")
  - SECRET_CUPIS = decode(bundled_obfuscated, chr(ord-2))

X-GUID = cookie visitor_id.

ВАЖНО ПРО ПАРСИНГ:
  Olimp для НТ/волейбола/баскета отдаёт:
    - матчевые рынки: tableType=RESULT/HANDICAP/TOTAL (П1/П2, Фора 1/2, ТотБ/ТотМ)
    - фазовые рынки:  tableType=OTHER (Ч4П1, П2Ф1К, Ч4ТотЧ4ТотМ)
  В set_markets кладём ТОЛЬКО фазовые (OTHER). Матчевые пропускаем целиком —
  иначе ловим матчевый тотал (84.5) как тотал партии и проигрываем.

КОРЗИНА (КРИТИЧНО):
  Серверная корзина Olimp — это SHARED состояние ПРОФИЛЯ, а не страницы.
  Все вкладки одного AdsPower-профиля работают в одной сессии → одна корзина.
  Если clear не сделать ПЕРЕД add — остатки от предыдущего неудачного
  захода превратят одиночную ставку в экспресс с чужими исходами.
  
  Поэтому place_bet делает СИНХРОННЫЙ clear перед add:
    - clear не удался (2 попытки) → ставку НЕ отправляем
    - clear ок → add → save (можно ретраить из _execute_bet,
      т.к. следующий place_bet снова начнётся с чистого листа)
"""
import asyncio
import hashlib
import json
import logging
import re
from typing import Optional, Dict, List, Callable
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


_SPORT_ID_TO_KEY = {
    "40":  "table_tennis",
    "10":  "volleyball",
    "5":   "basketball",
    "140": "cyber_basketball",
}

# ── Секрет из бандла Olimp (обфусцирован ASCII-2) ──
_SECRET_RAW = ";4f;94;3/44:g/63:f/;c47/c639hchc82g;"
_SECRET_CUPIS = "".join(chr(ord(c) - 2) for c in _SECRET_RAW)


def _fmt_num(v) -> str:
    """Как JS String(): 10.0 → '10', 10.5 → '10.5'."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _a_body(body: dict) -> str:
    """Аналог JS-функции a() из модуля 69205 бандла Olimp."""
    items = sorted(body.items(), key=lambda kv: kv[0])
    parts = []
    for k, v in items:
        if isinstance(v, dict):
            parts.append(_a_body(v))
        elif isinstance(v, bool):
            parts.append("true" if v else "false")
        elif v is None:
            parts.append("null")
        else:
            parts.append(_fmt_num(v))
    return ";".join(parts)


def generate_x_token(body: dict) -> str:
    """x-token = MD5(a_body(body) + ';' + SECRET_CUPIS)."""
    payload = f"{_a_body(body)};{_SECRET_CUPIS}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


class OlimpHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id: Optional[str] = (
            str(target_match_id) if target_match_id else None
        )
        self.target_teams: List[str] = list(target_teams or [])

        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None
        self._listener = None

    # ============================================================
    # setup / stop
    # ============================================================
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

        self._page = page
        self._callback = callback

        async def on_response(response: Response):
            await self._handle_response(response)

        self._listener = on_response
        page.on("response", on_response)
        logger.info(f"Olimp: listener установлен (target={self.target_match_id})")

    async def stop_listener(self, page: Page):
        if self._listener:
            try:
                page.remove_listener("response", self._listener)
            except Exception:
                pass
            self._listener = None
        self._callback = None
        logger.info(f"Olimp: listener снят (target={self.target_match_id})")

    # ============================================================
    # Парсинг live-данных
    # ============================================================
    async def _handle_response(self, response: Response):
        url = response.url

        # ── Баланс Olimp ──
        if '/api/user/balance' in url:
            try:
                data = await response.json()
                bal = data.get('data') if isinstance(data, dict) else None
                if isinstance(bal, (int, float)):
                    from ui.balance_bus import balance_bus
                    balance_bus.update('olimp', bal, 'RUB')
            except Exception:
                pass
            return

        if 'api/v4/0/live/' not in response.url:
            return
        try:
            data = await response.json()
        except Exception:
            return
        target = self.target_match_id
        if not target:
            return
        all_events = OlimpHandler._extract_all_events(data)
        parsed = all_events.get(target)
        if not parsed:
            return
        if self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Olimp callback: {e}", exc_info=True)

    @staticmethod
    def _extract_all_events(data) -> Dict[str, dict]:
        result: Dict[str, dict] = {}
        if not isinstance(data, list):
            return result
        for item in data:
            if not isinstance(item, dict):
                continue
            payload = item.get('payload')
            if not isinstance(payload, dict):
                continue
            if 'sportId' in payload and 'outcomes' in payload:
                parsed = OlimpHandler._parse_event(payload)
                if parsed and parsed.get('match_id'):
                    result[str(parsed['match_id'])] = parsed
                continue
            comps = payload.get('competitionsWithEvents')
            if isinstance(comps, list):
                for block in comps:
                    if not isinstance(block, dict):
                        continue
                    for event in (block.get('events') or []):
                        if not isinstance(event, dict):
                            continue
                        parsed = OlimpHandler._parse_event(event)
                        if parsed and parsed.get('match_id'):
                            result[str(parsed['match_id'])] = parsed
        return result

    @staticmethod
    def _parse_event(event: dict) -> Optional[dict]:
        sport_id = str(event.get('sportId') or '')
        sport_key = _SPORT_ID_TO_KEY.get(sport_id)
        if not sport_key:
            return None
        match_id = str(event.get('id') or '')
        if not match_id:
            return None

        player1 = event.get('team1Name', '') or ''
        player2 = event.get('team2Name', '') or ''

        score_str = event.get('score', '') or '0:0'
        try:
            score1, score2 = map(int, score_str.split(':'))
        except Exception:
            score1 = score2 = 0

        maps = event.get('mapsScore') or []
        pairs = []
        for m in maps:
            if isinstance(m, dict):
                try:
                    pairs.append((int(m.get('team1', 0) or 0),
                                  int(m.get('team2', 0) or 0)))
                except Exception:
                    pairs.append((0, 0))
        sub1 = sub2 = 0
        if pairs:
            sub1, sub2 = pairs[-1]

        comment = event.get('comment', '') or ''
        phase_num = 0
        if sport_key in ('basketball', 'cyber_basketball'):
            mm = re.search(r'(\d+)-я\s+четверть', comment)
            if mm:
                phase_num = int(mm.group(1))
            else:
                phase_num = len(pairs) if pairs else 1
        elif sport_key == 'volleyball':
            mm = re.search(r'#(\d+)', comment)
            if mm:
                phase_num = int(mm.group(1))
            else:
                phase_num = score1 + score2 + 1
        else:
            mm = re.search(r'#(\d+)', comment)
            if mm:
                phase_num = int(mm.group(1))
            else:
                phase_num = score1 + score2 + 1

        set_key = f"set_{phase_num}"
        set_markets: dict = {}
        outcome_ids: dict = {}
        outcomes = event.get('outcomes', []) or []

        # ВАЖНО: берём ТОЛЬКО tableType == 'OTHER' (фазовые рынки).
        # RESULT/HANDICAP/TOTAL — матчевые, игнорируем.
        for out in outcomes:
            if not isinstance(out, dict):
                continue

            table_type = (out.get('tableType') or '').upper()
            short_name = (out.get('shortName') or '').strip()

            if table_type != 'OTHER':
                continue
            if not short_name:
                continue

            try:
                prob = float(str(out.get('probability', '0')).replace(',', '.'))
            except Exception:
                continue
            if prob <= 1.01:
                continue
            try:
                param = float(str(out.get('param', '0')).replace(',', '.'))
            except Exception:
                param = 0.0

            basket_id = out.get('basketId', '') or ''
            if not basket_id:
                continue
            out_id = out.get('originalId') or out.get('id')

            # ── Победа в сете: Ч4П1 / П2П1 ──
            m = re.match(r'^[ЧП](\d+)П([12])$', short_name)
            if m:
                n = int(m.group(1))
                if n != phase_num:
                    continue
                side = m.group(2)
                set_markets.setdefault(set_key, {}).setdefault('winner', {})[side] = prob
                outcome_ids.setdefault(set_key, {}).setdefault('winner', {})[side] = {
                    'market_data': basket_id, 'kf': prob, 'id': out_id,
                }
                continue

            # ── Фора в сете: Ч4Ф1К / П2Ф1К ──
            m = re.match(r'^[ЧП](\d+)Ф([12])К$', short_name)
            if m:
                n = int(m.group(1))
                if n != phase_num:
                    continue
                side = m.group(2)
                h = set_markets.setdefault(set_key, {}).setdefault('handicap', {}) \
                    .setdefault(side, {})
                if 'line' not in h:
                    h['line'] = param
                    h['odd'] = prob
                    outcome_ids.setdefault(set_key, {}).setdefault('handicap', {})[side] = {
                        'market_data': basket_id, 'kf': prob,
                        'line': param, 'id': out_id,
                    }
                continue

            # ── Тотал сета: Ч4ТотЧ4ТотМ / П2ТотП2ТотМ ──
            m = re.match(r'^[ЧП](\d+)Тот[ЧП]\d+Тот([МБ])$', short_name)
            if m:
                n = int(m.group(1))
                if n != phase_num:
                    continue
                side = 'under' if m.group(2) == 'М' else 'over'
                t = set_markets.setdefault(set_key, {}).setdefault('total', {})
                if 'line' not in t:
                    t['line'] = param
                t[side] = prob
                outcome_ids.setdefault(set_key, {}).setdefault('total', {})[side] = {
                    'market_data': basket_id, 'kf': prob, 'line': param, 'id': out_id,
                }
                continue

            # ── ИТ игрока в сете: Ч4ИТ1Б / П2ИТ1М / Ч4ИТ2Б / П2ИТ2М ──
            m = re.match(r'^[ЧП](\d+)ИТ([12])([БМ])$', short_name)
            if m:
                n = int(m.group(1))
                if n != phase_num:
                    continue
                player = m.group(2)
                side = 'over' if m.group(3) == 'Б' else 'under'
                it = set_markets.setdefault(set_key, {}).setdefault('it', {}) \
                    .setdefault(player, {})
                if 'line' not in it:
                    it['line'] = param
                it[side] = prob
                outcome_ids.setdefault(set_key, {}).setdefault('it', {}) \
                    .setdefault(player, {}).setdefault(side, {})
                outcome_ids[set_key]['it'][player][side] = {
                    'market_data': basket_id, 'kf': prob, 'line': param, 'id': out_id,
                }
                continue

            logger.debug(
                f"Olimp: не распознан OTHER shortName={short_name!r} "
                f"param={param} prob={prob}"
            )

        if not set_markets:
            return None
        return {
            "match_id": match_id,
            "player1": player1, "player2": player2,
            "sport": sport_key,
            "phase_num": phase_num,
            "score1": score1, "score2": score2,
            "sub_score1": sub1, "sub_score2": sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    # ============================================================
    # Clear basket — СИНХРОННЫЙ, ДВЕ ПОПЫТКИ
    # ============================================================
    async def _clear_basket(self, page: Page, ukey: str, x_guid: str) -> bool:
        """
        Синхронная очистка корзины Olimp.

        Критично: корзина — shared state профиля, а не страницы.
        Если clear не сделать перед add — остатки от предыдущего
        неудачного захода превратят одиночную ставку в экспресс.

        Две попытки: первая может упасть из-за сетевого блипа,
        вторая обычно проходит. Если обе упали — возвращаем False,
        и place_bet НЕ будет отправлять ставку.
        """
        if not ukey or page.is_closed():
            return False

        body = {
            "time_shift": 0,
            "ukey": ukey,
            "lang_id": "0",
            "platforma": "SITE_CUPIS",
        }
        token = generate_x_token(body)
        body_json = json.dumps(json.dumps(body, ensure_ascii=False))

        for attempt in (1, 2):
            try:
                result = await page.evaluate(f"""
                    async () => {{
                        try {{
                            const r = await fetch('https://www.olimp.bet/api/basket/clear', {{
                                method: 'POST',
                                headers: {{
                                    'Content-Type': 'application/json',
                                    'Accept': 'application/json, text/plain, */*',
                                    'x-token': {json.dumps(token)},
                                    'x-guid': {json.dumps(x_guid)},
                                    'x-cupis': '1',
                                    'origin': 'https://www.olimp.bet',
                                    'referer': location.href,
                                }},
                                credentials: 'include',
                                body: {body_json}
                            }});
                            return await r.json();
                        }} catch(e) {{
                            return {{ error: {{ err_code: -1, err_desc: 'fetch: ' + e.message }} }};
                        }}
                    }}
                """)
            except Exception as e:
                logger.debug(f"Olimp clear_basket attempt {attempt}: {e}")
                if attempt == 1:
                    await asyncio.sleep(0.3)
                continue

            if not isinstance(result, dict):
                logger.debug(
                    f"Olimp clear_basket attempt {attempt}: bad response "
                    f"({type(result).__name__})"
                )
                if attempt == 1:
                    await asyncio.sleep(0.3)
                continue

            err = result.get("error") or {}
            if err.get("err_code") == 0:
                logger.debug("Olimp: корзина очищена")
                return True

            logger.debug(
                f"Olimp clear_basket attempt {attempt}: "
                f"err={err.get('err_code')} {err.get('err_desc')}"
            )
            if attempt == 1:
                await asyncio.sleep(0.3)

        logger.warning("Olimp: не удалось очистить корзину за 2 попытки")
        return False

    # ============================================================
    # Place bet
    # ============================================================
    async def place_bet(self, page: Page, bet_data: dict) -> dict:
        """
        bet_data:
          - market_data (str) — basketId
          - kf (float)
          - amount (int/float) — ставим int
          - outcome_id (str|int|None) — originalId

        Порядок:
          1. Читаем cookies (session, x_guid, ukey)
          2. СИНХРОННО чистим корзину (2 попытки)
          3. Если clear не удался — НЕ ставим
          4. basket/add — один исход
          5. basket/save — фиксация ставки

        Ретрай в _execute_bet безопасен: следующий place_bet снова
        начнётся с синхронного clear и снесёт мусор от предыдущего.
        """
        market_data = bet_data.get("market_data") or ""
        out_id = bet_data.get("outcome_id")
        if out_id is not None and market_data:
            oid = str(out_id).strip()
            if not oid.startswith("-"):
                oid = f"-{oid}"
            market_full = f"{oid}:{market_data}"
        else:
            market_full = market_data

        kf = float(bet_data.get("kf") or 0)
        amount = int(bet_data.get("amount") or 0)
        if not market_full or kf <= 1.01 or amount <= 0:
            return {"success": False, "error": "Olimp: invalid bet_data"}

        # ── Читаем cookies ──
        try:
            session = await page.evaluate(
                "() => { const m=document.cookie.match(/user_session=([^;]+)/); "
                "return m ? decodeURIComponent(m[1]) : ''; }"
            )
            x_guid = await page.evaluate(
                "() => { const m=document.cookie.match(/visitor_id=([^;]+)/); "
                "return m ? decodeURIComponent(m[1]) : ''; }"
            )
            ukey = await page.evaluate(
                "() => { const m=document.cookie.match(/user_ukey=([^;]+)/); "
                "return m ? decodeURIComponent(m[1]) : ''; }"
            )
        except Exception as e:
            return {"success": False, "error": f"Olimp cookie: {e}"}

        if not session:
            return {"success": False, "error": "Olimp: no user_session"}

        # ── СИНХРОННЫЙ CLEAR ПЕРЕД ADD ──
        # Не убирать и не делать асинхронным. Гарантия чистоты корзины —
        # единственная защита от превращения ставки в экспресс.
        cleared = await self._clear_basket(page, ukey, x_guid)
        if not cleared:
            return {
                "success": False,
                "error": "Olimp: не удалось очистить корзину (риск экспресса)",
            }

        sport_id = int(market_full.split(":")[-1] or 0)

        # ── ADD ──
        add_body = {
            "coefs_ids": json.dumps(
                [[market_full, str(kf), 1]],
                ensure_ascii=False, separators=(",", ":"),
            ),
            "sport_id": sport_id,
            "time_shift": 0,
            "lang_id": "0",
            "platforma": "SITE_CUPIS",
            "session": session,
        }
        add_token = generate_x_token(add_body)

        try:
            add_result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch('https://www.olimp.bet/api/basket/add', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Accept': 'application/json, text/plain, */*',
                                'x-token': {json.dumps(add_token)},
                                'x-guid': {json.dumps(x_guid)},
                                'x-cupis': '1',
                                'origin': 'https://www.olimp.bet',
                                'referer': location.href,
                            }},
                            credentials: 'include',
                            body: {json.dumps(json.dumps(add_body, ensure_ascii=False))}
                        }});
                        return await r.json();
                    }} catch(e) {{
                        return {{ error: {{ err_code: -1, err_desc: 'fetch: ' + e.message }} }};
                    }}
                }}
            """)
        except Exception as e:
            return {"success": False, "error": f"Olimp basket/add: {e}"}

        if not isinstance(add_result, dict):
            return {"success": False, "error": "Olimp basket/add bad response"}
        err = add_result.get("error") or {}
        if err.get("err_code") != 0:
            return {"success": False,
                    "error": f"Olimp basket/add: {err.get('err_code')} {err.get('err_desc')}"}

        stakes = ((add_result.get("data") or {}).get("stakes_list") or {})
        if not stakes:
            return {"success": False, "error": "Olimp basket/add: empty stakes_list"}
        hash_key = list(stakes.keys())[0]

        # ── SAVE ──
        unique_hash = (await page.evaluate(
            "() => Array.from(crypto.getRandomValues(new Uint8Array(16)))"
            ".map(b => b.toString(16).padStart(2, '0')).join('')"
        )) or ""

        save_body = {
            "sum": {hash_key: amount},
            "bet_type": 1,
            "any_handicap": 1,
            "details": 1,
            "lang_id": "0",
            "platforma": "SITE_CUPIS",
            "save_any": 1,
            "session": session,
            "time_shift": 0,
            "unique_bet_hash": unique_hash,
        }
        save_token = generate_x_token(save_body)

        try:
            save_result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch('https://www.olimp.bet/api/basket/save', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Accept': 'application/json, text/plain, */*',
                                'x-token': {json.dumps(save_token)},
                                'x-guid': {json.dumps(x_guid)},
                                'x-cupis': '1',
                                'origin': 'https://www.olimp.bet',
                                'referer': location.href,
                            }},
                            credentials: 'include',
                            body: {json.dumps(json.dumps(save_body, ensure_ascii=False))}
                        }});
                        return await r.json();
                    }} catch(e) {{
                        return {{ error: {{ err_code: -1, err_desc: 'fetch: ' + e.message }} }};
                    }}
                }}
            """)
        except Exception as e:
            return {"success": False, "error": f"Olimp basket/save: {e}"}

        if not isinstance(save_result, dict):
            return {"success": False, "error": "Olimp basket/save bad response"}

        err = save_result.get("error") or {}
        if err.get("err_code") != 0:
            return {"success": False,
                    "error": f"Olimp basket/save: {err.get('err_code')} {err.get('err_desc')}"}

        bet_id = None
        ids = save_result.get("ids")
        if isinstance(ids, list) and ids:
            bet_id = ids[0]

        logger.info(f"Olimp: ставка принята, bet_id={bet_id}")
        return {"success": True, "bet_id": bet_id}