# ui/after_goal/marathon.py
"""
Marathon handler — instance-based.
Транспорт: SSE через XMLHttpRequest (подтверждено 2026-10-08 по логу).
Хук цепляется в prepare_page ДО page.goto.

Ставки: place-bets → (LIVE_DELAY) → complete-bet-ticket.
ВАЖНО: effectivePrice = {denominator: 100, numerator: (odds-1)*100}.
Раньше было denominator=10000 — Marathon отвечал HTTP 500.
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional, Dict, List, Callable

from playwright.async_api import Page, Response

from .base import BookmakerHandler

logger = logging.getLogger(__name__)


MARATHON_SSE_HOOK = r"""
(function() {
    if (window.__marathon_hook_installed) return;
    window.__marathon_hook_installed = true;
    console.log('MARATHON_HOOK: installed');

    // ── Хук №1: EventSource ──
    const OrigES = window.EventSource;
    if (OrigES) {
        function HookedES(url, config) {
            console.log('MARATHON_ES_CREATED', url);
            const es = new OrigES(url, config);
            try {
                ['message', 'snapshot', 'update', 'json', 'ping', 'error'].forEach(name => {
                    es.addEventListener(name, ev => {
                        try {
                            if (typeof window.__marathon_on_sse === 'function') {
                                window.__marathon_on_sse(String(ev.type || name),
                                                         String(ev.data));
                            }
                        } catch (e) {}
                    });
                });
            } catch (e) {}
            return es;
        }
        HookedES.prototype  = OrigES.prototype;
        HookedES.CONNECTING = OrigES.CONNECTING;
        HookedES.OPEN       = OrigES.OPEN;
        HookedES.CLOSING    = OrigES.CLOSING;
        HookedES.CLOSED     = OrigES.CLOSED;
        try { window.EventSource = HookedES; } catch (e) {}
    }

    // ── Хук №2: fetch() с Accept: text/event-stream ──
    const origFetch = window.fetch;
    window.fetch = function(input, init) {
        const promise = origFetch.apply(this, arguments);
        try {
            const url = (typeof input === 'string') ? input
                        : (input && input.url) ? input.url : '';
            let accept = '';
            const hdrs = (init && init.headers) || (input && input.headers);
            if (hdrs) {
                if (hdrs instanceof Headers) {
                    accept = hdrs.get('accept') || hdrs.get('Accept') || '';
                } else if (Array.isArray(hdrs)) {
                    for (const [k, v] of hdrs) {
                        if (String(k).toLowerCase() === 'accept') accept = v;
                    }
                } else if (typeof hdrs === 'object') {
                    for (const k of Object.keys(hdrs)) {
                        if (k.toLowerCase() === 'accept') accept = hdrs[k];
                    }
                }
            }
            if (String(accept).indexOf('text/event-stream') !== -1
                && url.indexOf('/betslip/choices') === -1
                && url.indexOf('/check-bet-slip') === -1) {
                console.log('MARATHON_FETCH_SSE', url, 'accept=' + accept);
                promise.then(resp => {
                    if (!resp.body || !resp.body.getReader) return;
                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder('utf-8');
                    let buffer = '';
                    function pump() {
                        reader.read().then(({done, value}) => {
                            if (done) return;
                            try {
                                buffer += decoder.decode(value, {stream: true});
                            } catch (e) { return pump(); }
                            let idx;
                            while ((idx = buffer.indexOf('\n\n')) !== -1) {
                                const part = buffer.slice(0, idx);
                                buffer = buffer.slice(idx + 2);
                                try {
                                    if (typeof window.__marathon_on_sse === 'function') {
                                        window.__marathon_on_sse('fetch', part);
                                    }
                                } catch (e) {}
                            }
                            pump();
                        }).catch(() => {});
                    }
                    pump();
                }).catch(() => {});
            }
        } catch (e) {}
        return promise;
    };

    // ── Хук №3: XMLHttpRequest с Accept: text/event-stream ──
    const OrigXHR = window.XMLHttpRequest;
    if (OrigXHR) {
        const origOpen = OrigXHR.prototype.open;
        const origSend = OrigXHR.prototype.send;
        const origSetHeader = OrigXHR.prototype.setRequestHeader;

        OrigXHR.prototype.open = function(method, url) {
            this.__m_url = url;
            this.__m_headers = {};
            return origOpen.apply(this, arguments);
        };
        OrigXHR.prototype.setRequestHeader = function(name, value) {
            try { this.__m_headers[String(name).toLowerCase()] = value; }
            catch (e) {}
            return origSetHeader.apply(this, arguments);
        };
        OrigXHR.prototype.send = function() {
            const url = this.__m_url || '';
            const accept = (this.__m_headers && this.__m_headers['accept']) || '';
            if (String(accept).indexOf('text/event-stream') !== -1) {
                console.log('MARATHON_XHR_SSE', url, 'accept=' + accept);
                const xhr = this;
                let lastLen = 0;
                let buffer = '';
                function processChunk() {
                    if (xhr.readyState !== 3 && xhr.readyState !== 4) return;
                    let full;
                    try { full = xhr.responseText || ''; }
                    catch (e) { return; }
                    if (full.length <= lastLen) return;
                    const newPart = full.slice(lastLen);
                    lastLen = full.length;
                    buffer += newPart;
                    let idx;
                    while ((idx = buffer.indexOf('\n\n')) !== -1) {
                        const part = buffer.slice(0, idx);
                        buffer = buffer.slice(idx + 2);
                        try {
                            if (typeof window.__marathon_on_sse === 'function') {
                                window.__marathon_on_sse('xhr', part);
                            }
                        } catch (e) {}
                    }
                }
                try {
                    xhr.addEventListener('readystatechange', processChunk);
                    xhr.addEventListener('progress', processChunk);
                } catch (e) {}
            }
            return origSend.apply(this, arguments);
        };
        console.log('MARATHON_HOOK: xhr wrapped');
    }
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

        self._sse_raw_count = 0
        self._sse_json_count = 0
        self._last_sse_at: float = 0.0

    # ============================================================
    # prepare_page
    # ============================================================
    async def prepare_page(self, page: Page):
        try:
            await page.expose_function(
                "__marathon_on_sse",
                self._handle_sse_from_browser,
            )
            logger.info("Marathon: expose_function установлен")

            # ── Console listener: только важные MARATHON_* строки ──
            #  Логика не меняется, только наблюдаем за JS.
            def _marathon_console(msg):
                t = msg.text or ""
                if any(k in t for k in (
                    "MARATHON_FRESH_COEFF",
                    "MARATHON_EDGE_CHECK",
                    "MARATHON_LINE_CHECK",
                    "MARATHON_LINE_CHANGED",
                    "MARATHON_PLACE_BET_REQ",
                    "MARATHON_PLACE_BET_RESP",
                    "MARATHON_CONFIRM_RESP",
                    "MARATHON_CONFIRM2_RESP",
                )):
                    logger.warning(f"Marathon JS: {t}")

            page.on("console", _marathon_console)
            logger.info("Marathon: console listener включён (фильтр MARATHON_*)")

            await page.add_init_script(MARATHON_SSE_HOOK)
            try:
                await page.evaluate(MARATHON_SSE_HOOK)
            except Exception as e:
                logger.debug(f"Marathon evaluate hook: {e}")

            try:
                installed = await page.evaluate(
                    "() => !!(window.__marathon_hook_installed)"
                )
                logger.info(f"Marathon: hook installed = {installed}")
            except Exception as e:
                logger.warning(f"Marathon: hook check failed: {e}")

            logger.info("Marathon: SSE-хук установлен")
        except Exception as e:
            logger.error(f"Marathon prepare_page: {e}", exc_info=True)

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
        self._last_sse_at = 0.0

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

    def has_received_sse(self) -> bool:
        return self._sse_json_count > 0

    # ============================================================
    # SSE: разбор кадров
    # ============================================================
    async def _handle_sse_from_browser(self, event_type: str, raw: str):
        self._sse_raw_count += 1
        if self._sse_raw_count <= 3:
            logger.debug(
                f"Marathon SSE hook #{self._sse_raw_count}: "
                f"event={event_type!r} "
                f"raw_len={len(raw) if raw else 0} "
                f"raw[:150]={raw[:150]!r}"
            )

        try:
            if not raw:
                return

            payload = raw
            if raw.lstrip().startswith(("data:", "event:", "id:", "retry:")):
                data_lines = []
                for line in raw.split("\n"):
                    line = line.rstrip("\r")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if not data_lines:
                    return
                payload = "\n".join(data_lines)

            payload = payload.strip()
            if not payload or not payload.startswith(("{", "[")):
                if self._sse_raw_count <= 3:
                    logger.info(
                        f"Marathon SSE hook: skip non-JSON "
                        f"(event={event_type!r}, raw[:80]={raw[:80]!r})"
                    )
                return

            data = json.loads(payload)
            self._sse_json_count += 1
            self._last_sse_at = time.time()

            if self._sse_json_count <= 3:
                logger.info(
                    f"Marathon SSE hook: JSON OK #{self._sse_json_count} "
                    f"type={type(data).__name__} "
                    f"keys={list(data.keys())[:8] if isinstance(data, dict) else 'n/a'}"
                )

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

                    if (isinstance(path, list) and path
                            and isinstance(path[0], dict)
                            and path[0].get("inObj") == "matchScore"
                            and isinstance(value, dict)
                            and self._snapshot is not None):
                        self._snapshot["matchScore"] = value
                        score_changed = True

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

            # У Marathon для баскетбола модели нумеруются со сдвигом +1:
            #   MTCH_R1 = «весь матч»
            #   MTCH_R2 = Q1
            #   MTCH_R3 = Q2
            # Для НТ такого сдвига нет — там MTCH_R1 = 1-я партия.
            sport_code = (data.get("sportCode") or "").lower()
            basket_shift = 1 if "basket" in sport_code else 0

            if self._sse_json_count <= 8:
                raw_markets = data.get("markets", {}) or {}
                models_dump = []
                for _mid, _m in raw_markets.items():
                    if isinstance(_m, dict):
                        models_dump.append({
                            "model": _m.get("model"),
                            "name": _m.get("name"),
                            "state": _m.get("state"),
                        })
                logger.debug(
                    f"Marathon MODELS_DUMP #{self._sse_json_count}: "
                    f"sport={data.get('sportCode')} "
                    f"phase_partNumber={(data.get('phase') or {}).get('partNumber')} "
                    f"count={len(models_dump)} "
                    f"models={[m['model'] for m in models_dump]}"
                )

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

                mm = re.match(r"^MTCH_R(\d{1,2})$", model)
                if mm:
                    kind = "winner"
                    m_set = int(mm.group(1))
                elif re.match(r"^MTCH_TTLG\d{1,2}$", model):
                    mm2 = re.match(r"^MTCH_TTLG(\d{1,2})$", model)
                    if mm2:
                        kind = "total"
                        m_set = int(mm2.group(1))
                elif re.match(r"^MTCH_HB\d{1,2}$", model):
                    mm3 = re.match(r"^MTCH_HB(\d{1,2})$", model)
                    if mm3:
                        kind = "handicap"
                        m_set = int(mm3.group(1))
                elif re.match(r"^MTCH_T1TTLG\d{1,2}$", model):
                    mm4 = re.match(r"^MTCH_T1TTLG(\d{1,2})$", model)
                    if mm4:
                        kind = "it"
                        player = "1"
                        m_set = int(mm4.group(1))
                elif re.match(r"^MTCH_T2TTLG\d{1,2}$", model):
                    mm5 = re.match(r"^MTCH_T2TTLG(\d{1,2})$", model)
                    if mm5:
                        kind = "it"
                        player = "2"
                        m_set = int(mm5.group(1))
                elif re.match(r"^MTCH_TTLGOE\d{1,2}$", model):
                    mm6 = re.match(r"^MTCH_TTLGOE(\d{1,2})$", model)
                    if mm6:
                        kind = "odd"
                        m_set = int(mm6.group(1))

                # ← Сдвиг для баскетбола
                if kind and m_set and basket_shift:
                    m_set -= basket_shift

                if not kind or not m_set:
                    continue
                if m_set < 1 or m_set > 10:
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
                            set_markets.setdefault(set_key, {}) \
                                .setdefault("winner", {})[side] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("winner", {})[side] = outcome

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

                            # ── ФИКС: одна линия для set_markets и outcome_ids ──
                            existing_line = t.get("line")
                            if existing_line is not None and abs(existing_line - line) > 0.01:
                                continue

                            t["line"] = line
                            t[side] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("total", {})[side] = {
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
                            hk = (set_markets.setdefault(set_key, {})
                                  .setdefault("handicap", {})
                                  .setdefault(side, {}))
                            hk["line"] = line
                            hk["odd"] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("handicap", {})[side] = {
                                    **outcome, "line": line,
                                }

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

                            # ── ФИКС: одна линия для set_markets и outcome_ids ──
                            # Marathon присылает несколько линий (9.5, 10.5, 11.5).
                            # Раньше: it['line'] = первая, outcome_ids[...] = последняя.
                            # Результат: best_bet.line ≠ outcome_info.line → ставим
                            # на неправильную линию, думая что ставим на правильную.
                            # Теперь: если линия уже выбрана — игнорируем селекты
                            # других линий.
                            existing_line = it.get("line")
                            if existing_line is not None and abs(existing_line - line) > 0.01:
                                continue

                            it["line"] = line
                            it[side] = odds
                            outcome_ids.setdefault(set_key, {}) \
                                .setdefault("it", {}) \
                                .setdefault(player, {})[side] = {
                                    **outcome, "line": line,
                                }

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

            const den = 100;

            const getCookie = (n) => {{
                const m = document.cookie.match(
                    new RegExp('(^|; )' + n + '=([^;]*)')
                );
                return m ? decodeURIComponent(m[2]) : '';
            }};

            // ═══════════════════════════════════════════════════════
            // ШАГ 0: свежий coefficientId через /betslip/choices
            // Между SSE-кадром и place-bets прошло 200-500 мс.
            // За это время Marathon мог пересчитать кэф. Тогда
            // EqualsToCurrent отбивает ставку с
            // COEFFICIENT_DEDUCTION_ERROR. Свежий ID решает.
            // ═══════════════════════════════════════════════════════
            let freshCoeffId = d.coefficient_id;
            let freshOdds = d.odds;
            try {{
                // ── AbortController с жёстким таймаутом на fetch ──
                // Без него: если betslip/choices не отдал ответ —
                // fetch висит 5-30 сек (таймаут браузера).
                const _controller = new AbortController();
                const _timeoutId = setTimeout(() => {{
                    try {{ _controller.abort(); }} catch (e) {{}}
                }}, 800);

                let choicesResp;
                try {{
                    choicesResp = await fetch(
                        'https://new.marathonbet.ru/eag/event-line/api/v1/betslip/choices',
                        {{
                            method: 'POST',
                            headers: {{
                                'accept': 'text/event-stream',
                                'content-type': 'application/json',
                                'x-pan-source': 'REDESIGN_WEB',
                                'x-pan-target': 'BROWSER',
                                'x-pan-version': 'MOBILE-SSR-2.7.8',
                                'origin': 'https://new.marathonbet.ru',
                                'referer': location.href
                            }},
                            credentials: 'include',
                            signal: _controller.signal,
                            body: JSON.stringify({{
                                selRefs: [{{
                                    eventId: d.event_id,
                                    selId: d.selection_id
                                }}]
                            }})
                        }}
                    );
                }} catch (fetchErr) {{
                    clearTimeout(_timeoutId);
                    console.log('MARATHON_CHOICES_TIMEOUT', String(fetchErr));
                    throw fetchErr;
                }}

                const reader = choicesResp.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let parsedChoice = null;
                const startMs = Date.now();

                // ── Чтение чанков с таймаутом на каждый read() ──
                // Без Promise.race: reader.read() может зависнуть
                // на 5-30 сек (SSE-поток открыт, чанков нет).
                while (Date.now() - startMs < 800) {{
                    let readResult;
                    try {{
                        readResult = await Promise.race([
                            reader.read(),
                            new Promise((_, reject) =>
                                setTimeout(() => reject(new Error('read_timeout')), 200)
                            )
                        ]);
                    }} catch (readErr) {{
                        // Один чанк не пришёл за 200мс — выходим
                        break;
                    }}

                    const {{done, value}} = readResult;
                    if (done) break;
                    buffer += decoder.decode(value, {{stream: true}});

                    const lines = buffer.split('\\n');
                    for (const line of lines) {{
                        if (line.startsWith('data:')) {{
                            const jsonStr = line.slice(5).trim();
                            try {{
                                const obj = JSON.parse(jsonStr);
                                const choices = obj.choices || {{}};
                                const key = String(d.selection_id);
                                if (choices[key] && choices[key].coeff) {{
                                    parsedChoice = choices[key];
                                    break;
                                }}
                            }} catch (e) {{}}
                        }}
                    }}
                    if (parsedChoice) break;
                }}

                clearTimeout(_timeoutId);
                try {{ reader.cancel(); }} catch (e) {{}}

                if (parsedChoice && parsedChoice.coeff) {{
                    freshCoeffId = parsedChoice.coeff.id;
                    const price = parsedChoice.coeff.price || {{}};
                    const n = price.n || 0;
                    const dd = price.d || 1;
                    if (dd > 0) {{
                        freshOdds = (n + dd) / dd;
                    }}

                    // ── ДИАГНОСТИКА: полный parsedChoice ──
                    // Уберём после того, как увидим структуру
                    try {{
                        console.log(
                            'MARATHON_FRESH_COEFF_FULL',
                            JSON.stringify(parsedChoice).slice(0, 800)
                        );
                    }} catch (e) {{}}

                    console.log(
                        'MARATHON_FRESH_COEFF',
                        'old=' + d.coefficient_id + '@' + d.odds,
                        'new=' + freshCoeffId + '@' + freshOdds
                    );

                    // ── ПРОВЕРКА ЛИНИИ: не переставил ли Marathon
                    //    селект на соседнюю линию ──
                    // Пробуем все возможные имена полей, где может
                    // лежать линия. Первое ненулевое — наше.
                    const _lineOrig = Number(d.line || 0);
                    let _lineFresh = 0;
                    const _lineCandidates = [
                        parsedChoice.handicap,
                        parsedChoice.line,
                        parsedChoice.param,
                        parsedChoice.value,
                        parsedChoice.coeff && parsedChoice.coeff.handicap,
                        parsedChoice.coeff && parsedChoice.coeff.line,
                        parsedChoice.coeff && parsedChoice.coeff.param,
                    ];
                    for (const c of _lineCandidates) {{
                        const n = Number(c);
                        if (!isNaN(n) && n !== 0) {{
                            _lineFresh = n;
                            break;
                        }}
                    }}
                    // ── Fallback: линия в selName как текст "Меньше 9.5" ──
                    if (_lineFresh === 0 && parsedChoice.selName) {{
                        const _m = String(parsedChoice.selName).match(/(\\d+\\.?\\d*)/);
                        if (_m) _lineFresh = Number(_m[1]);
                    }}
                    if (_lineOrig > 0 && _lineFresh > 0) {{
                        const _lineDiff = Math.abs(_lineOrig - _lineFresh);
                        if (_lineDiff > 0.01) {{
                            console.log(
                                'MARATHON_LINE_CHANGED',
                                'orig=' + _lineOrig + ' fresh=' + _lineFresh
                            );
                            return {{
                                success: false,
                                error: 'line_changed (' + _lineOrig +
                                       ' → ' + _lineFresh + ')',
                                diag: 'line_changed'
                            }};
                        }} else {{
                            console.log(
                                'MARATHON_LINE_CHECK',
                                'OK orig=' + _lineOrig +
                                ' fresh=' + _lineFresh
                            );
                        }}
                    }} else {{
                        console.log(
                            'MARATHON_LINE_CHECK',
                            'skip — orig=' + _lineOrig +
                            ' fresh=' + _lineFresh
                        );
                    }}

                    // ── Guard: edge пропал после refresh ──
                    const _fastOdd = Number(d.fast_odd || 0);
                    const _minEdge = Number(d.min_edge || 0.05);
                    if (_fastOdd > 1.01 && freshOdds > 1.01) {{
                        const _edgeNow = freshOdds / _fastOdd - 1.0;
                        console.log(
                            'MARATHON_EDGE_CHECK',
                            'orig=' + ((d.odds / _fastOdd - 1) * 100).toFixed(1) + '%',
                            'fresh=' + (_edgeNow * 100).toFixed(1) + '%',
                            'min=' + (_minEdge * 100).toFixed(1) + '%'
                        );
                        if (_edgeNow < _minEdge) {{
                            return {{
                                success: false,
                                error: 'edge_gone_after_refresh (' +
                                    (_edgeNow * 100).toFixed(1) + '% < ' +
                                    (_minEdge * 100).toFixed(1) + '%)',
                                diag: 'edge_gone'
                            }};
                        }}
                    }}
                }} else {{
                    console.log('MARATHON_FRESH_COEFF', 'no fresh data, using old');
                }}
            }} catch (e) {{
                console.log('MARATHON_FRESH_COEFF_ERR', String(e));
            }}

            const num = Math.round(den * (freshOdds - 1));

            const punterHash = getCookie('punter-session-hash');

            const headers = {{
                'accept': '*/*',
                'accept-language': 'ru-RU',
                'content-type': 'application/json',
                'origin': 'https://new.marathonbet.ru',
                'referer': location.href,
                'x-pan-source': 'REDESIGN_WEB',
                'x-pan-target': 'BROWSER',
                'x-pan-version': 'MOBILE-SSR-2.7.8'
            }};
            if (punterHash) headers['punter-session-hash'] = punterHash;

            const placeBody = {{
                bets: [{{
                    type: 'SINGLE',
                    choices: [{{
                        coefficientId: freshCoeffId,
                        effectivePrice: {{ denominator: den, numerator: num }},
                        selectionRef: {{ eventId: d.event_id, id: d.selection_id }}
                    }}],
                    betStake: d.amount
                }}],
                betPlacingMode: 'Any',
                oneClick: false
            }};

            try {{
                console.log('MARATHON_PLACE_BET_REQ',
                            JSON.stringify(placeBody));
            }} catch (e) {{}}

            async function confirmTicket(code) {{
                const resp = await fetch(
                    'https://new.marathonbet.ru/client-gate/betting/complete-bet-ticket',
                    {{ method: 'POST', headers, credentials: 'include',
                       body: JSON.stringify({{ code }}) }}
                );
                return await resp.json();
            }}

            try {{
                const placeResp = await fetch(
                    'https://new.marathonbet.ru/client-gate/betting/place-bets',
                    {{ method: 'POST', headers, credentials: 'include',
                       body: JSON.stringify(placeBody) }}
                );
                const placeJson = await placeResp.json();

                try {{
                    console.log('MARATHON_PLACE_BET_RESP',
                                'status=' + placeResp.status,
                                JSON.stringify(placeJson).slice(0, 500));
                }} catch (e) {{}}

                if (placeJson.status !== 'OK') {{
                    return {{ success: false,
                              error: 'place-bets: ' + JSON.stringify(placeJson).slice(0,400) }};
                }}

                let payload = placeJson.payload || {{}};

                // Если сразу OK (без LIVE_DELAY)
                if (payload.status !== 'LIVE_DELAY') {{
                    const results = payload.betPlacingResults || [];
                    if (results.length > 0) {{
                        const r = results[0];
                        if (r.status === 'OK' || r.status === 'ACCEPTED') {{
                            return {{ success: true,
                                      betId: r.id
                                          || r.betId
                                          || r.ticketId
                                          || payload.punterTicketNumber
                                          || null }};
                        }}
                        return {{ success: false,
                                  error: JSON.stringify(r).slice(0,400) }};
                    }}
                    return {{ success: false,
                              error: 'unknown status: ' + payload.status }};
                }}

                // LIVE_DELAY — ждём и подтверждаем
                let code = payload.code;
                if (!code) {{
                    return {{ success: false, error: 'LIVE_DELAY без code' }};
                }}

                // Первый confirm
                let delay = payload.liveDelayMillis || 2000;
                await new Promise(r => setTimeout(r, delay + 50));
                let cJson = await confirmTicket(code);

                try {{
                    console.log('MARATHON_CONFIRM_RESP',
                                JSON.stringify(cJson).slice(0, 500));
                }} catch (e) {{}}

                let cPayload = cJson.payload || {{}};

                // Может быть ещё один LIVE_DELAY — второй confirm
                if (cPayload.status === 'LIVE_DELAY') {{
                    const code2 = cPayload.code || code;
                    const delay2 = cPayload.liveDelayMillis || 2000;
                    await new Promise(r => setTimeout(r, delay2 + 500));
                    cJson = await confirmTicket(code2);
                    cPayload = cJson.payload || {{}};
                    try {{
                        console.log('MARATHON_CONFIRM2_RESP',
                                    JSON.stringify(cJson).slice(0, 500));
                    }} catch (e) {{}}
                    code = code2;
                }}

                const cResults = cPayload.betPlacingResults || [];
                if (cResults.length > 0) {{
                    const r = cResults[0];
                    if (r.status === 'OK' || r.status === 'ACCEPTED') {{
                        return {{ success: true,
                                  betId: r.id
                                      || r.betId
                                      || r.ticketId
                                      || cPayload.punterTicketNumber
                                      || code }};
                    }}
                    return {{ success: false,
                              error: 'confirm: ' + JSON.stringify(r).slice(0,400) }};
                }}

                if (cPayload.status === 'OK' || cPayload.status === 'ACCEPTED') {{
                    return {{ success: true,
                              betId: cPayload.punterTicketNumber || code }};
                }}

                return {{ success: false,
                          error: 'confirm unknown: ' + JSON.stringify(cJson).slice(0,400) }};
            }} catch(e) {{
                return {{ success: false, error: 'network: ' + e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)