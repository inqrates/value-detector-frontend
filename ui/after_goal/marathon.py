# ui/after_goal/marathon.py
"""
Marathon handler — instance-based.
Транспорт: SSE через EventSource (JS-хук цепляется ДО page.goto).

Рынки по model суффиксу (проверено по дампу 2026-10-04 на НТ):
  MTCH_R{N}      → winner (победитель N-й партии)
  MTCH_HB{N}     → handicap (фора по очкам, N-я партия)
  MTCH_TTLG{N}   → total (тотал очков N-й партии)
  MTCH_T1TTLG{N} → it.1 (ИТ игрока 1, N-я партия)
  MTCH_T2TTLG{N} → it.2 (ИТ игрока 2, N-я партия)
  MTCH_TTLGOE{N} → odd (чёт/нечёт тотала N-й партии)

Матчевые модели (MTCH_R, MTCH_HB, MTCH_HBP, MTCH_TTLP, MTCH_TTLPOE,
MTCH_CS) игнорируются.

У Marathon в лайв-ленте НТ НЕТ рынка «N-е очко» и НЕТ RACE.
"""
import asyncio
import json
import logging
import re
from typing import Optional, Dict, List, Callable

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
        self._warned_missing = set()
        self._warned_phase = None
        self._snapshot: Optional[dict] = None

    # ============================================================
    # prepare_page — вызвать ДО page.goto()
    # ============================================================
    async def prepare_page(self, page: Page):
        try:
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

    # ============================================================
    # setup / stop
    # ============================================================
    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        self._page = page
        self._callback = callback
        self._warned_missing = set()
        self._warned_phase = None
        self._snapshot = None

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
        self._warned_missing = set()
        self._warned_phase = None
        self._snapshot = None
        logger.info(f"Marathon: listener снят (target={self.target_match_id})")

    # ============================================================
    # SSE: разбор кадров
    # ============================================================
    async def _handle_sse_from_browser(self, event_type: str, raw: str):
        try:
            if not raw or not raw.startswith(("{", "[")):
                return
            data = json.loads(raw)

            # ── A: полный снапшот одного события ──
            if isinstance(data, dict) and "treeId" in data and "markets" in data:
                if (self.target_match_id
                        and str(data.get("treeId")) != self.target_match_id):
                    return
                self._snapshot = data
                parsed = self._parse_event(data)
                if parsed and self._callback:
                    self._check_missing_markets(parsed)
                    await self._callback(parsed)
                return

            # ── Б: снапшот общего фида (itemMap) ──
            if isinstance(data, dict) and "itemMap" in data:
                for _, t in (data.get("itemMap") or {}).items():
                    for event in (t.get("liveEvents") or []):
                        if not isinstance(event, dict):
                            continue
                        if (self.target_match_id
                                and str(event.get("treeId")) != self.target_match_id):
                            continue
                        self._snapshot = event
                        parsed = self._parse_event(event)
                        if parsed and self._callback:
                            self._check_missing_markets(parsed)
                            await self._callback(parsed)
                return

            # ── В: список изменений ──
            if isinstance(data, list):
                score_changed = False
                for change in data:
                    if not isinstance(change, dict):
                        continue

                    path = change.get("path") or []
                    value = change.get("value")

                    # В.1 — полный снапшот внутри списка
                    if (isinstance(value, dict)
                            and "treeId" in value and "markets" in value):
                        if (self.target_match_id
                                and str(value.get("treeId")) != self.target_match_id):
                            continue
                        self._snapshot = value
                        parsed = self._parse_event(value)
                        if parsed and self._callback:
                            self._check_missing_markets(parsed)
                            await self._callback(parsed)
                        continue

                    # В.2 — diff по счёту: обновляем matchScore в кэше
                    if (isinstance(path, list) and path
                            and isinstance(path[0], dict)
                            and path[0].get("inObj") == "matchScore"
                            and isinstance(value, dict)
                            and self._snapshot is not None):
                        self._snapshot["matchScore"] = value
                        score_changed = True

                # После всех диффов — один колбэк со свежим счётом
                if score_changed and self._snapshot is not None:
                    parsed = self._parse_event(self._snapshot)
                    if parsed and self._callback:
                        await self._callback(parsed)
                return

        except json.JSONDecodeError:
            return
        except Exception as e:
            logger.debug(f"Marathon SSE hook: {e}")

    # ============================================================
    # Диагностика отсутствующих рынков
    # ============================================================
    def _check_missing_markets(self, parsed: dict):
        sm = parsed.get("set_markets") or {}
        phase = parsed.get("phase_num")
        set_key = f"set_{phase}" if phase else None
        if not set_key:
            return

        if phase != self._warned_phase:
            self._warned_missing = set()
            self._warned_phase = phase

        markets = sm.get(set_key, {})
        expected = ["winner", "total", "handicap", "odd", "it"]
        missing = [k for k in expected if k not in markets]

        if missing:
            new_missing = set(missing) - self._warned_missing
            if new_missing:
                logger.warning(
                    f"Marathon: отсутствуют рынки для фазы {phase}: "
                    f"{', '.join(sorted(new_missing))}. "
                    f"У Marathon может не быть этих рынков в стриме."
                )
                self._warned_missing.update(new_missing)

    # ============================================================
    # Парсер события
    # ============================================================
    def _parse_event(self, data: dict) -> Optional[dict]:
        try:
            tree_id = data.get("treeId")
            event_id = data.get("eventId")
            if not tree_id or not event_id:
                return None

            # ── Игроки ──
            player1, player2 = "", ""
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

            # ── Счёт ──
            ms = data.get("matchScore", {}) or {}
            main = ms.get("main", {}) or {}
            try:
                score1 = int(main.get("home", 0))
                score2 = int(main.get("away", 0))
            except Exception:
                score1 = score2 = 0

            phase = data.get("phase", {}) or {}
            set_num = int(phase.get("partNumber") or 1)

            # Фоллбэк: если partNumber нет — считаем по сыгранным частям + 1
            parts = ms.get("parts", []) or []
            if not set_num and parts:
                for i in range(len(parts) - 1, -1, -1):
                    p = parts[i] or {}
                    try:
                        if int(p.get("home", 0)) > 0 or int(p.get("away", 0)) > 0:
                            set_num = i + 1
                            break
                    except Exception:
                        pass
            if not set_num:
                set_num = 1

            sub1, sub2 = 0, 0
            if parts and len(parts) >= set_num:
                p = parts[set_num - 1] or {}
                try:
                    sub1 = int(p.get("home", 0))
                    sub2 = int(p.get("away", 0))
                except Exception:
                    pass

            # ── Рынки ──
            markets = data.get("markets", {}) or {}
            set_markets: Dict[str, dict] = {}
            outcome_ids: Dict[str, dict] = {}
            num_re = re.compile(r"([+-]?\d+\.?\d*)")

            for form_id, market in markets.items():
                if not isinstance(market, dict):
                    continue
                if market.get("state") not in (None, "ACTIVE"):
                    continue

                model = market.get("model", "") or ""
                selections = market.get("selections", {}) or {}

                kind = None
                m_set = None
                player = None

                # WINNER: MTCH_R{N}
                mm = re.match(r"^MTCH_R(\d+)$", model)
                if mm:
                    kind = "winner"
                    m_set = int(mm.group(1))

                # TOTAL: MTCH_TTLG{N}
                elif re.match(r"^MTCH_TTLG\d+$", model):
                    mm2 = re.search(r"MTCH_TTLG(\d+)", model)
                    if mm2:
                        kind = "total"
                        m_set = int(mm2.group(1))

                # HANDICAP: MTCH_HB{N}
                elif re.match(r"^MTCH_HB\d+$", model):
                    mm3 = re.search(r"MTCH_HB(\d+)", model)
                    if mm3:
                        kind = "handicap"
                        m_set = int(mm3.group(1))

                # IT1: MTCH_T1TTLG{N}
                elif re.match(r"^MTCH_T1TTLG\d+$", model):
                    mm4 = re.search(r"MTCH_T1TTLG(\d+)", model)
                    if mm4:
                        kind = "it"
                        player = "1"
                        m_set = int(mm4.group(1))

                # IT2: MTCH_T2TTLG{N}
                elif re.match(r"^MTCH_T2TTLG\d+$", model):
                    mm5 = re.search(r"MTCH_T2TTLG(\d+)", model)
                    if mm5:
                        kind = "it"
                        player = "2"
                        m_set = int(mm5.group(1))

                # ODD/EVEN: MTCH_TTLGOE{N}
                elif re.match(r"^MTCH_TTLGOE\d+$", model):
                    mm6 = re.search(r"MTCH_TTLGOE(\d+)", model)
                    if mm6:
                        kind = "odd"
                        m_set = int(mm6.group(1))

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

                    # ── WINNER ──
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
                            set_markets.setdefault(set_key, {}) \
                                .setdefault("winner", {})[side] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("winner", {})[side] = outcome

                    # ── TOTAL ──
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
                            t = set_markets.setdefault(set_key, {}) \
                                .setdefault("total", {})
                            if "line" not in t:
                                t["line"] = line
                            t[side] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("total", {})[side] = {
                                    **outcome, "line": line,
                                }

                    # ── HANDICAP ──
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
                            hk = (set_markets.setdefault(set_key, {})
                                  .setdefault("handicap", {})
                                  .setdefault(side, {}))
                            hk["line"] = line
                            hk["odd"] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("handicap", {})[side] = {
                                    **outcome, "line": line,
                                }

                    # ── IT ──
                    elif kind == "it" and player:
                        lower = sel_name.lower()
                        side = None
                        if "больше" in lower:
                            side = "over"
                        elif "меньше" in lower:
                            side = "under"
                        if side:
                            mline = num_re.search(sel_name)
                            line = float(mline.group(1)) if mline else 0.0
                            it = (set_markets.setdefault(set_key, {})
                                  .setdefault("it", {})
                                  .setdefault(player, {}))
                            if "line" not in it:
                                it["line"] = line
                            it[side] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("it", {}) \
                                .setdefault(player, {})[side] = {
                                    **outcome, "line": line,
                                }

                    # ── Чёт / Нечёт ──
                    elif kind == "odd":
                        lower = sel_name.lower()
                        om = set_markets.setdefault(set_key, {}) \
                            .setdefault("odd", {})
                        oi = outcome_ids.setdefault(set_key, {}) \
                            .setdefault("odd", {})
                        if "нечет" in lower or "нечёт" in lower:
                            om["odd"] = odds
                            oi["odd"] = outcome
                        elif "чет" in lower or "чёт" in lower:
                            om["even"] = odds
                            oi["even"] = outcome

            if not set_markets:
                return None

            return {
                "match_id":   str(tree_id),
                "event_id":   event_id,
                "player1":    player1,
                "player2":    player2,
                "sport":      "any",
                "phase_num":  set_num,
                "phase_name": phase.get("partName", f"{set_num}-я партия"),
                "score1":     score1,
                "score2":     score2,
                "sub_score1": sub1,
                "sub_score2": sub2,
                "set_markets": set_markets,
                "outcome_ids": outcome_ids,
            }

        except Exception as e:
            logger.error(f"Marathon parse: {e}", exc_info=True)
            return None

    # ============================================================
    # Размещение ставки
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        script = f"""
        (async function() {{
            const d = {json.dumps(bet_data)};
            const getCookie = (n) => {{
                const m = document.cookie.match(
                    new RegExp('(^|; )' + n + '=([^;]*)')
                );
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
                    {{ method: 'POST', headers, credentials: 'include',
                       body: JSON.stringify(placeBody) }}
                );
                const placeJson = await placeResp.json();
                if (placeJson.status !== 'OK') {{
                    return {{ success: false,
                              error: 'place-bets: ' + JSON.stringify(placeJson).slice(0,400) }};
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
                            return {{ success: true,
                                      betId: r.betId || r.ticketId || code || null }};
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
                    {{ method: 'POST', headers, credentials: 'include',
                       body: JSON.stringify({{ code }}) }}
                );
                const confirmJson = await confirmResp.json();
                const cPayload = confirmJson.payload || {{}};
                const cResults = cPayload.betPlacingResults || [];
                if (cResults.length > 0) {{
                    const r = cResults[0];
                    if (r.status === 'OK' || r.status === 'ACCEPTED') {{
                        return {{ success: true,
                                  betId: r.betId || r.ticketId || code }};
                    }}
                    return {{ success: false,
                              error: 'confirm: ' + JSON.stringify(r).slice(0,400) }};
                }}
                if (cPayload.status === 'OK' || cPayload.status === 'ACCEPTED') {{
                    return {{ success: true, betId: code }};
                }}
                return {{ success: false,
                          error: 'confirm unknown: ' + JSON.stringify(confirmJson).slice(0,400) }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)