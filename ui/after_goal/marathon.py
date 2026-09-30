# ui/after_goal/marathon.py
"""
Marathon handler — instance-based.

Использует SSE через page.expose_function + add_init_script:
  - prepare_page() вызывается ДО page.goto — вешает JS-хук на EventSource
  - setup_listener() регистрирует callback и парсит кадры
  - _handle_sse_from_browser вызывается из JS-контекста при каждом SSE-кадре
"""
import asyncio
import json
import logging
import re
from typing import Optional, List, Callable
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


MARATHON_SSE_HOOK = r"""
(function() {
    if (window.__marathon_hook_installed) return;
    window.__marathon_hook_installed = true;

    const OrigES = window.EventSource;
    if (!OrigES) return;

    function send(type, raw) {
        try {
            if (typeof window.__marathon_on_sse === 'function') {
                window.__marathon_on_sse(String(type || 'message'), String(raw));
            }
        } catch (e) { /* ignore */ }
    }

    function HookedES(url, config) {
        const es = new OrigES(url, config);
        try {
            ['message', 'snapshot', 'update', 'json', 'ping', 'error'].forEach(name => {
                es.addEventListener(name, ev => send(ev.type || name, ev.data));
            });
        } catch (e) { /* ignore */ }
        return es;
    }
    HookedES.prototype  = OrigES.prototype;
    HookedES.CONNECTING = OrigES.CONNECTING;
    HookedES.OPEN       = OrigES.OPEN;
    HookedES.CLOSING    = OrigES.CLOSING;
    HookedES.CLOSED     = OrigES.CLOSED;
    try { window.EventSource = HookedES; } catch (e) {}
})();
"""


class MarathonHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id = str(target_match_id) if target_match_id else None
        self.target_teams = list(target_teams or [])
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None

    async def prepare_page(self, page: Page):
        """Регистрирует Python-bridge и вешает JS-хук. Вызывать ДО page.goto()."""
        try:
            # Привязываем bound-метод экземпляра, чтобы callback был свой
            await page.expose_function(
                "__marathon_on_sse",
                self._handle_sse_from_browser,
            )
            await page.add_init_script(MARATHON_SSE_HOOK)
            try:
                await page.evaluate(MARATHON_SSE_HOOK)
            except Exception:
                pass
            logger.info("Marathon: SSE-хук установлен")
        except Exception as e:
            logger.error(f"Marathon prepare_page: {e}")

    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        self._page = page
        self._callback = callback

        # ── Listener для баланса ──
        async def on_resp_balance(response: Response):
            url = response.url
            if '/client-gate/heartbeat' not in url:
                return
            try:
                data = await response.json()
                payload = (data or {}).get('payload') or {}
                user = payload.get('user') or {}
                bal = user.get('mainBalance')
                if bal is None:
                    bal = user.get('availableBalance')
                if bal is not None:
                    from ui.balance_bus import balance_bus
                    balance_bus.update('marathon', bal, 'RUB')
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_resp_balance(r)))

        logger.info(f"Marathon: listener готов (target={self.target_match_id})")

    async def stop_listener(self, page: Page):
        self._callback = None
        logger.info(f"Marathon: listener снят (target={self.target_match_id})")

    async def _handle_sse_from_browser(self, event_type: str, raw: str):
        """Вызывается из браузера при каждом SSE-кадре."""
        try:
            if not raw or not raw.startswith(("{", "[")):
                return
            data = json.loads(raw)

            # Вариант А: snapshot одного события
            if isinstance(data, dict) and "treeId" in data and "markets" in data:
                if self.target_match_id and str(data.get("treeId")) != self.target_match_id:
                    return
                parsed = self.parse_update(data)
                if parsed and self._callback:
                    await self._callback(parsed)
                return

            # Вариант Б: snapshot общего фида (itemMap)
            if isinstance(data, dict) and "itemMap" in data:
                for _, t in (data.get("itemMap") or {}).items():
                    for event in (t.get("liveEvents") or []):
                        if not isinstance(event, dict):
                            continue
                        if (self.target_match_id
                                and str(event.get("treeId")) != self.target_match_id):
                            continue
                        parsed = self.parse_update(event)
                        if parsed and self._callback:
                            await self._callback(parsed)
                return

            # Вариант В: update — список изменений
            if isinstance(data, list):
                for change in data:
                    if not isinstance(change, dict):
                        continue
                    value = change.get("value")
                    if not isinstance(value, dict):
                        continue
                    if "treeId" in value and "markets" in value:
                        if (self.target_match_id
                                and str(value.get("treeId")) != self.target_match_id):
                            continue
                        parsed = self.parse_update(value)
                        if parsed and self._callback:
                            await self._callback(parsed)
                return
        except json.JSONDecodeError:
            return
        except Exception as e:
            logger.debug(f"Marathon SSE hook: {e}")

    @staticmethod
    def parse_update(data: dict) -> Optional[dict]:
        try:
            tree_id = data.get("treeId")
            event_id = data.get("eventId")
            if not tree_id or not event_id:
                return None

            player1 = ""
            player2 = ""
            try:
                ht = data.get("homeTeam", {}).get("members", [])
                at = data.get("awayTeam", {}).get("members", [])
                if ht:
                    player1 = ht[0].get("name", "")
                if at:
                    player2 = at[0].get("name", "")
            except Exception:
                pass
            if not player1:
                nm = data.get("name", "")
                if " - " in nm:
                    player1, player2 = nm.split(" - ", 1)
                    player1 = player1.strip()
                    player2 = player2.strip()

            ms = data.get("matchScore", {}) or {}
            main = ms.get("main", {}) or {}
            try:
                score1 = int(main.get("home", 0))
                score2 = int(main.get("away", 0))
            except Exception:
                score1 = score2 = 0

            phase = data.get("phase", {}) or {}
            set_num = int(phase.get("partNumber") or 1)

            parts = ms.get("parts", []) or []
            sub1 = sub2 = 0
            if parts and len(parts) >= set_num:
                p = parts[set_num - 1] or {}
                try:
                    sub1 = int(p.get("home", 0))
                    sub2 = int(p.get("away", 0))
                except Exception:
                    pass

            markets = data.get("markets", {}) or {}
            set_markets = {}
            outcome_ids = {}
            num_re = re.compile(r"([+-]?\d+\.?\d*)")

            for m_id, market in markets.items():
                if not isinstance(market, dict):
                    continue
                if market.get("state") not in (None, "ACTIVE"):
                    continue

                model = market.get("model", "") or ""
                selections = market.get("selections", {}) or {}

                kind = None
                m_set = None

                mm = re.match(r"^MTCH_R(\d+)$", model)
                if mm:
                    kind = "winner"
                    m_set = int(mm.group(1))
                elif re.match(r"^MTCH_TTLG\d+$", model):
                    mm2 = re.search(r"MTCH_TTLG(\d+)", model)
                    if mm2:
                        kind = "total"
                        m_set = int(mm2.group(1))
                elif re.match(r"^MTCH_HB\d+$", model):
                    mm3 = re.search(r"MTCH_HB(\d+)", model)
                    if mm3:
                        kind = "handicap"
                        m_set = int(mm3.group(1))

                if not kind or not m_set:
                    continue

                set_key = f"set_{m_set}"

                for sel_id, sel in selections.items():
                    if not isinstance(sel, dict):
                        continue
                    coeff = sel.get("coeff", {}) or {}
                    coeff_id = coeff.get("id")
                    price = coeff.get("price", {}) or {}
                    n = price.get("n", 0)
                    d = price.get("d", 1)
                    if not coeff_id or not d:
                        continue
                    try:
                        odds = (float(n) + float(d)) / float(d)
                    except Exception:
                        continue
                    if odds <= 1.0:
                        continue

                    sel_name = (sel.get("name", "") or "").strip()
                    outcome = {
                        "selection_id":   int(sel_id),
                        "coefficient_id": int(coeff_id),
                        "odds":           odds,
                    }

                    if kind == "winner":
                        side = None
                        if player1 and sel_name == player1:
                            side = "1"
                        elif player2 and sel_name == player2:
                            side = "2"
                        if side is None:
                            if player1 and player1.lower() in sel_name.lower():
                                side = "1"
                            elif player2 and player2.lower() in sel_name.lower():
                                side = "2"
                        if side:
                            set_markets.setdefault(set_key, {}).setdefault("winner", {})[side] = odds
                            outcome_ids.setdefault(set_key, {}).setdefault("winner", {})[side] = outcome

                    elif kind == "total":
                        lower = sel_name.lower()
                        side = None
                        if "больше" in lower:
                            side = "over"
                        elif "меньше" in lower:
                            side = "under"
                        if side:
                            mline = num_re.search(sel_name)
                            line = float(mline.group(1)) if mline else 0.0
                            set_markets.setdefault(set_key, {}).setdefault("total", {})["line"] = line
                            set_markets[set_key]["total"][side] = odds
                            outcome_ids.setdefault(set_key, {}).setdefault("total", {})[side] = {
                                **outcome, "line": line,
                            }

                    elif kind == "handicap":
                        mline = num_re.search(sel_name)
                        if not mline:
                            continue
                        try:
                            line = float(mline.group(1))
                        except Exception:
                            continue
                        side = None
                        if player1 and player1 in sel_name:
                            side = "1"
                        elif player2 and player2 in sel_name:
                            side = "2"
                        if side:
                            set_markets.setdefault(set_key, {}).setdefault("handicap", {}).setdefault(side, {})
                            set_markets[set_key]["handicap"][side]["line"] = line
                            set_markets[set_key]["handicap"][side]["odd"] = odds
                            outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})[side] = {
                                **outcome, "line": line,
                            }

            if not set_markets:
                return None

            return {
                "match_id":   tree_id,
                "event_id":   event_id,
                "player1":    player1,
                "player2":    player2,
                "phase_num":  set_num,     # ← добавили
                "score1":     score1,
                "score2":     score2,
                "sub_score1": sub1,
                "sub_score2": sub2,
                "set_markets": set_markets,
                "outcome_ids": outcome_ids,
            }
        except Exception as e:
            logger.error(f"Marathon parse_update: {e}", exc_info=True)
            return None

    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """POST /client-gate/betting/place-bets + (если LIVE_DELAY) complete-bet-ticket."""
        script = f"""
        (async function() {{
            const d = {json.dumps(bet_data)};

            const getCookie = (n) => {{
                const m = document.cookie.match(new RegExp('(^|; )' + n + '=([^;]*)'));
                return m ? decodeURIComponent(m[2]) : '';
            }};

            const punterHash = getCookie('punter-session-hash');

            const den = 10000;
            const num = Math.round(den * (d.odds - 1));

            const headers = {{
                'Content-Type': 'application/json',
                'Accept': '*/*',
                'Origin': 'https://new.marathonbet.ru',
                'Referer': location.href,
                'x-pan-source': 'REDESIGN_WEB',
                'x-pan-target': 'BROWSER',
                'x-pan-version': 'MOBILE-SSR-2.7.1'
            }};
            if (punterHash) headers['punter-session-hash'] = punterHash;

            const placeBody = {{
                bets: [{{
                    type: 'SINGLE',
                    betStake: d.amount,
                    choices: [{{
                        coefficientId: d.coefficient_id,
                        effectivePrice: {{ denominator: den, numerator: num }},
                        selectionRef: {{ eventId: d.event_id, id: d.selection_id }}
                    }}]
                }}],
                betPlacingMode: 'EqualsToCurrent',
                oneClick: false
            }};

            try {{
                const placeResp = await fetch(
                    'https://new.marathonbet.ru/client-gate/betting/place-bets',
                    {{ method: 'POST', headers, credentials: 'include', body: JSON.stringify(placeBody) }}
                );
                const placeJson = await placeResp.json();
                if (placeJson.status !== 'OK') {{
                    return {{ success: false, error: 'place-bets: ' + JSON.stringify(placeJson).slice(0,400) }};
                }}

                const payload = placeJson.payload || {{}};
                const status = payload.status;
                const code = payload.code;
                const delay = payload.liveDelayMillis || 0;

                if (status !== 'LIVE_DELAY') {{
                    const results = payload.betPlacingResults || [];
                    if (results.length > 0) {{
                        const r = results[0];
                        if (r.status === 'OK' || r.status === 'ACCEPTED') {{
                            return {{ success: true, betId: r.betId || r.ticketId || code || null }};
                        }}
                        return {{ success: false, error: JSON.stringify(r).slice(0,400) }};
                    }}
                    return {{ success: false, error: 'unknown status: ' + status }};
                }}

                if (!code) {{
                    return {{ success: false, error: 'LIVE_DELAY без code' }};
                }}

                await new Promise(r => setTimeout(r, delay + 500));

                const confirmResp = await fetch(
                    'https://new.marathonbet.ru/client-gate/betting/complete-bet-ticket',
                    {{ method: 'POST', headers, credentials: 'include', body: JSON.stringify({{ code }}) }}
                );
                const confirmJson = await confirmResp.json();
                const cPayload = confirmJson.payload || {{}};
                const cResults = cPayload.betPlacingResults || [];

                if (cResults.length > 0) {{
                    const r = cResults[0];
                    if (r.status === 'OK' || r.status === 'ACCEPTED') {{
                        return {{ success: true, betId: r.betId || r.ticketId || code }};
                    }}
                    return {{ success: false, error: 'confirm: ' + JSON.stringify(r).slice(0,400) }};
                }}
                if (cPayload.status === 'OK' || cPayload.status === 'ACCEPTED') {{
                    return {{ success: true, betId: code }};
                }}
                return {{ success: false, error: 'confirm unknown: ' + JSON.stringify(confirmJson).slice(0,400) }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)