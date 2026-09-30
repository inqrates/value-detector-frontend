# ui/after_goal/ligastavok.py

import asyncio
import json
import logging
import re
from typing import Optional, Dict, List, Callable
from playwright.async_api import Page, Response
from .base import BookmakerHandler

logger = logging.getLogger(__name__)


_FETCH_HOOK_JS = r"""
(function() {
    if (window.__ls_fetch_hook_installed) return;
    window.__ls_fetch_hook_installed = true;
    window.__ls_creds = window.__ls_creds || {};

    const origFetch = window.fetch;
    window.fetch = function(url, opts) {
        try {
            if (opts && opts.headers) {
                let cred = null, user = null;
                if (opts.headers instanceof Headers) {
                    cred = opts.headers.get('x-api-cred') || opts.headers.get('X-API-Cred');
                    user = opts.headers.get('x-user') || opts.headers.get('X-User');
                } else if (Array.isArray(opts.headers)) {
                    for (const [k, v] of opts.headers) {
                        const lk = String(k).toLowerCase();
                        if (lk === 'x-api-cred') cred = v;
                        else if (lk === 'x-user') user = v;
                    }
                } else if (typeof opts.headers === 'object') {
                    for (const k of Object.keys(opts.headers)) {
                        const lk = k.toLowerCase();
                        if (lk === 'x-api-cred') cred = opts.headers[k];
                        else if (lk === 'x-user') user = opts.headers[k];
                    }
                }
                if (cred) {
                    window.__ls_creds['x-api-cred'] = cred;
                    if (user) window.__ls_creds['x-user'] = user;
                }
            }
        } catch (e) { /* ignore */ }

        const promise = origFetch.apply(this, arguments);
        try {
            const urlStr = typeof url === 'string' ? url : (url && url.url) || '';
            if (urlStr.indexOf('/auth/v2/getBalance') !== -1) {
                promise.then(resp => {
                    try {
                        resp.clone().json().then(data => {
                            try {
                                const result = data && data.result;
                                if (Array.isArray(result) && result.length > 0) {
                                    const num = result[0].number;
                                    if (num) {
                                        window.__ls_creds['accountNumber'] = num;
                                    }
                                }
                            } catch (e) { /* ignore */ }
                        }).catch(() => {});
                    } catch (e) { /* ignore */ }
                }).catch(() => {});
            }
        } catch (e) { /* ignore */ }

        return promise;
    };
})();
"""


class LigaStavokHandler(BookmakerHandler):
    def __init__(self, target_match_id: str = None,
                 target_teams: List[str] = None):
        self.target_match_id = str(target_match_id) if target_match_id else None
        self.target_teams = list(target_teams or [])
        self._page: Optional[Page] = None
        self._callback: Optional[Callable] = None

        self._scores_all: List[dict] = []
        self._markets: Dict[str, dict] = {}
        self._outcomes: Dict[str, dict] = {}

        # ── Карты партов: main (SET_N) и race (SET_N-RACE) ──
        self._phase_parts: Dict[int, str] = {}
        self._phase_race_parts: Dict[int, str] = {}

        self._outcomes_types: Dict[str, Dict[str, list]] = {
            "winner":    {},
            "handicap1": {},
            "handicap2": {},
            "total1":    {},
            "total2":    {},
            "total3":    {},
            "odd":       {},
        }

        self._event_status_phase: int = 0
        self._action_line_loaded: bool = False
        self._snapshot_task: Optional[asyncio.Task] = None

    # ════════════════════════════════════════════════════════════
    # prepare_page
    # ════════════════════════════════════════════════════════════
    async def prepare_page(self, page: Page):
        if self._page is page:
            return

        if self._page and not self._page.is_closed():
            try:
                self._page.remove_listener("response", self._on_response)
            except Exception:
                pass
            try:
                self._page.remove_listener("websocket", self._on_websocket_event)
            except Exception:
                pass

        self._page = page

        self._scores_all = []
        self._markets.clear()
        self._outcomes.clear()
        self._phase_parts.clear()
        self._phase_race_parts.clear()
        self._event_status_phase = 0
        self._action_line_loaded = False
        for k in self._outcomes_types:
            self._outcomes_types[k].clear()

        try:
            await page.add_init_script(_FETCH_HOOK_JS)
            try:
                await page.evaluate(_FETCH_HOOK_JS)
            except Exception:
                pass
            logger.debug("LigaStavok: fetch-хук установлен")
        except Exception as e:
            logger.warning(f"LigaStavok: fetch-хук не установлен: {e}")

        page.on("response", self._on_response)
        page.on("websocket", self._on_websocket_event)

        logger.info(
            f"LigaStavok: prepare_page завершён (target={self.target_match_id}) "
            f"— жду перехвата actionLine от фронта"
        )

    # ════════════════════════════════════════════════════════════
    # Разбор actionLine
    # ════════════════════════════════════════════════════════════
    def _parse_action_line(self, data: dict):
        result = data.get("result")
        if not isinstance(result, dict):
            return

        for pid, p in (result.get("parts") or {}).items():
            if isinstance(p, dict):
                self._register_part(pid, p)

        for mid, m in (result.get("markets") or {}).items():
            if not isinstance(m, dict):
                continue
            self._markets[str(mid)] = {
                "type":   (m.get("type") or "").upper(),
                "title":  m.get("title") or "",
                "partId": m.get("partId") or "",
            }

        for oid, o in (result.get("outcomes") or {}).items():
            if not isinstance(o, dict):
                continue
            self._outcomes[oid] = {
                "outcomeId":  o.get("id"),
                "facId":      o.get("facId"),
                "id":         o.get("id"),
                "outcomeKey": (o.get("outcomeKey") or "").strip(),
                "value":      o.get("value"),
                "adValue":    o.get("adValue"),
                "marketId":   str(o.get("marketId") or ""),
                "title":      o.get("title"),
                "locked":     bool(o.get("locked")),
                "corrupted":  bool(o.get("corrupted")),
            }

        for field, key in (
            ("winner",    "outcomesWinner"),
            ("handicap1", "outcomesHandicap1"),
            ("handicap2", "outcomesHandicap2"),
            ("total1",    "outcomesTotal1"),
            ("total2",    "outcomesTotal2"),
            ("total3",    "outcomesTotal3"),
            ("odd",       "outcomesOdd"),
        ):
            m = result.get(key) or {}
            if isinstance(m, dict):
                for pid, mid in m.items():
                    if mid:
                        self._outcomes_types[field] \
                            .setdefault(pid, []) \
                            .append(str(mid))

        scores = result.get("scores") or {}
        if isinstance(scores, dict):
            all_scores = scores.get("all")
            if isinstance(all_scores, list):
                self._scores_all = all_scores

        event = result.get("event") or {}
        status_str = (event.get("status") or "").strip()
        m = re.search(r'(\d+)\s*set', status_str, re.IGNORECASE)
        if m:
            self._event_status_phase = int(m.group(1))

        self._action_line_loaded = True

    # ════════════════════════════════════════════════════════════
    # setup_listener / stop_listener
    # ════════════════════════════════════════════════════════════
    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        if self._page is not page:
            await self.prepare_page(page)

        self._callback = callback

        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except (asyncio.CancelledError, Exception):
                pass

        self._snapshot_task = asyncio.create_task(self._snapshot_loop())

        logger.info(
            f"LigaStavok: listener установлен (target={self.target_match_id})"
        )

    async def stop_listener(self, page: Page):
        if self._page and not self._page.is_closed():
            try:
                self._page.remove_listener("response", self._on_response)
            except Exception:
                pass
            try:
                self._page.remove_listener("websocket", self._on_websocket_event)
            except Exception:
                pass

        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except (asyncio.CancelledError, Exception):
                pass
        self._snapshot_task = None
        self._callback = None
        self._page = None

        logger.info(f"LigaStavok: listener снят (target={self.target_match_id})")

    # ════════════════════════════════════════════════════════════
    # HTTP: баланс + actionLine
    # ════════════════════════════════════════════════════════════
    async def _on_response(self, response: Response):
        url = response.url

        if '/rest/auth/v2/getBalance' in url:
            try:
                data = await response.json()
                result = (data or {}).get('result')
                if isinstance(result, list) and result:
                    bal = result[0].get('balance')
                    if bal is not None:
                        from ui.balance_bus import balance_bus
                        balance_bus.update('ligastavok', bal, 'RUB')
            except Exception:
                pass
            return

        if '/rest/events/v6/actionLine' in url:
            try:
                data = await response.json()
            except Exception:
                return
            if not isinstance(data, dict):
                return
            result = data.get("result")
            if not isinstance(result, dict):
                return
            event = result.get("event") or {}
            event_id = str(event.get("id") or "")
            if event_id and self.target_match_id and event_id != str(self.target_match_id):
                return
            logger.info(
                f"LigaStavok: 📥 actionLine перехвачен "
                f"(target={self.target_match_id})"
            )
            self._parse_action_line(data)
            return

    # ════════════════════════════════════════════════════════════
    # WS
    # ════════════════════════════════════════════════════════════
    def _on_websocket_event(self, ws):
        try:
            if "lds-api-sites.ligastavok.ru/ws" not in ws.url:
                return
            logger.info(f"LigaStavok: WS подключён {ws.url}")
            ws.on(
                "framereceived",
                lambda p: asyncio.create_task(self._on_ws_frame(p)),
            )
        except Exception as e:
            logger.debug(f"LigaStavok: on_ws: {e}")

    async def _on_ws_frame(self, payload):
        target = self.target_match_id
        if not target:
            return
        try:
            if isinstance(payload, (bytes, bytearray)):
                txt = payload.decode("utf-8", errors="replace")
            else:
                txt = str(payload)
        except Exception:
            return
        if target not in txt:
            return
        try:
            data = json.loads(txt)
        except Exception:
            return
        result = data.get("result")
        if not isinstance(result, dict):
            return
        items = result.get("payload")
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict):
                continue
            if str(it.get("id")) != target:
                continue
            itype = it.get("type")
            d = it.get("data") or {}
            if itype == "update":
                self._apply_headers(d.get("headers") or [])
                self._apply_outcomes(d.get("outcomes") or [])
            elif itype == "remove":
                pass

    def _apply_headers(self, headers: list):
        for h in headers:
            if not isinstance(h, dict):
                continue
            op = h.get("op")
            path = h.get("path") or ""
            value = h.get("value")

            if path == "/scores/all" and isinstance(value, list):
                self._scores_all = value
                continue

            if path == "/event/status" and isinstance(value, str):
                m = re.search(r'(\d+)\s*set', value, re.IGNORECASE)
                if m:
                    n = int(m.group(1))
                    if n != self._event_status_phase:
                        self._event_status_phase = n
                        logger.info(
                            f"LigaStavok: WS /event/status = {value!r} "
                            f"→ активная фаза {n}"
                        )
                continue

            if path.startswith("/parts/"):
                part_id = path.split("/", 2)[-1]
                if op in ("add", "replace") and isinstance(value, dict):
                    self._register_part(part_id, value)
                elif op == "remove":
                    for n, pid in list(self._phase_parts.items()):
                        if pid == part_id:
                            del self._phase_parts[n]
                    for n, pid in list(self._phase_race_parts.items()):
                        if pid == part_id:
                            del self._phase_race_parts[n]
                continue

            if path.startswith("/markets/"):
                mid = path.split("/", 2)[-1]
                if op in ("add", "replace") and isinstance(value, dict):
                    self._markets[str(mid)] = {
                        "type":   (value.get("type") or "").upper(),
                        "title":  value.get("title") or "",
                        "partId": value.get("partId") or "",
                    }
                elif op == "remove":
                    self._markets.pop(str(mid), None)
                continue

    def _register_part(self, part_id: str, value: dict):
        """
        Регистрирует partId.

        Разделяет:
          - SET_N         → self._phase_parts[N]       (основной сет)
          - SET_N-RACE    → self._phase_race_parts[N]  (гонка внутри сета)

        Матчевые (OVERALLTIME / POINT-OVERALLTIME / "Весь матч") — пропуск.
        """
        title = (value.get("title") or value.get("name") or "").strip()
        code  = (value.get("code") or "").strip().upper()

        # Матчевые — пропуск
        if "OVERALLTIME" in code or "весь матч" in title.lower():
            return

        # RACE-признак
        is_race = ("RACE" in code) or ("гонка" in title.lower())

        num = None

        if code:
            m = re.search(r'SET[_-]?(\d+)', code, re.IGNORECASE)
            if m:
                num = int(m.group(1))

        if num is None:
            for key in ("partNumber", "number", "num"):
                v = value.get(key)
                if v is not None:
                    try:
                        num = int(v)
                        break
                    except Exception:
                        pass

        if num is None and title:
            m = re.search(r'(\d+)', title)
            if m:
                num = int(m.group(1))

        if num is None or num <= 0:
            return

        if is_race:
            self._phase_race_parts[num] = part_id
            logger.info(
                f"LigaStavok: partId {part_id} = фаза {num} RACE "
                f"(title={title!r} code={code!r})"
            )
        else:
            self._phase_parts[num] = part_id
            logger.info(
                f"LigaStavok: partId {part_id} = фаза {num} "
                f"(title={title!r} code={code!r})"
            )

    def _apply_outcomes(self, outcomes: list):
        for o in outcomes:
            if not isinstance(o, dict):
                continue
            op = o.get("op")
            path = o.get("path") or ""
            value = o.get("value")

            if not path.startswith("/outcomes/"):
                continue

            parts = path.split("/")
            if len(parts) < 3:
                continue
            oid = parts[2]

            if len(parts) == 3:
                if op in ("add", "replace") and isinstance(value, dict):
                    real_outcome_id = value.get("id")
                    fac_id = value.get("facId")
                    self._outcomes[oid] = {
                        "outcomeId":  real_outcome_id,
                        "factorId":   fac_id,
                        "id":         real_outcome_id,
                        "outcomeKey": (value.get("outcomeKey") or "").strip(),
                        "facId":      fac_id,
                        "value":      value.get("value"),
                        "adValue":    value.get("adValue"),
                        "marketId":   str(value.get("marketId") or ""),
                        "title":      value.get("title"),
                        "locked":     bool(value.get("locked")),
                        "corrupted":  bool(value.get("corrupted")),
                    }
                elif op == "remove":
                    self._outcomes.pop(oid, None)
                continue

            field = parts[3]
            entry = self._outcomes.get(oid)
            if entry is None:
                continue
            if op == "remove":
                entry.pop(field, None)
                continue
            if op not in ("add", "replace"):
                continue

            if field == "value":
                entry["value"] = value
            elif field == "facId":
                entry["factorId"] = value
                entry["facId"] = value
            elif field == "adValue":
                entry["adValue"] = value
            elif field == "outcomeKey":
                entry["outcomeKey"] = (str(value) or "").strip()
            elif field == "title":
                entry["title"] = value
            elif field == "id":
                entry["outcomeId"] = value
                entry["id"] = value
            elif field == "locked":
                entry["locked"] = bool(value)
            elif field == "corrupted":
                entry["corrupted"] = bool(value)
            elif field == "marketId":
                entry["marketId"] = str(value or "")

    def _determine_active_phase(self) -> int:
        if self._event_status_phase > 0:
            return self._event_status_phase
        if not self._scores_all:
            return 0
        active_num = 0
        for s in self._scores_all:
            try:
                n = int(s.get("PartNumber") or 0)
                s1 = int(s.get("ScoreTeam1") or 0)
                s2 = int(s.get("ScoreTeam2") or 0)
            except Exception:
                continue
            if n <= 0:
                continue
            if s1 > 0 or s2 > 0:
                active_num = n
            else:
                break
        return active_num

    # ════════════════════════════════════════════════════════════
    # Парсеры рынков (общие — вызываются и для main, и для race)
    # ════════════════════════════════════════════════════════════
    def _parse_market_outcomes(self, mid: str) -> list:
        outs = []
        for oid, o in self._outcomes.items():
            if str(o.get("marketId")) != str(mid):
                continue
            if o.get("locked") or o.get("corrupted"):
                continue
            try:
                kf = float(o.get("value") or 0)
            except Exception:
                continue
            if kf <= 1.01:
                continue
            outs.append((oid, o, kf))
        return outs

    def _classify_side(self, o: dict) -> Optional[str]:
        title = (o.get("title") or "").strip().lower()
        key   = (o.get("outcomeKey") or "").lower().strip("_")

        if title in ("1", "п1", "ком1", "победа 1"):
            return "1"
        if title in ("2", "п2", "ком2", "победа 2"):
            return "2"
        if title in ("мен", "меньше", "тм", "under"):
            return "under"
        if title in ("бол", "больше", "тб", "over"):
            return "over"
        if title in ("чёт", "чет", "even"):
            return "even"
        if title in ("нечет", "нечёт", "odd"):
            return "odd"

        if key == "1":
            return "1"
        if key == "2":
            return "2"
        if key in ("less", "under", "мен", "тм"):
            return "under"
        if key in ("gross", "over", "бол", "тб"):
            return "over"
        if key == "odd":
            return "odd"
        if key == "even":
            return "even"
        return None

    def _parse_winner(self, part_id, set_key, set_markets, outcome_ids):
        for mid in self._outcomes_types["winner"].get(part_id, []):
            for oid, o, kf in self._parse_market_outcomes(mid):
                side = self._classify_side(o)
                if side not in ("1", "2"):
                    continue
                set_markets.setdefault(set_key, {}).setdefault("winner", {})[side] = kf
                outcome_ids.setdefault(set_key, {}).setdefault("winner", {})[side] = {
                    "outcomeId": oid,
                    "factorId":  o.get("facId"),
                    "kf":        kf,
                }

    def _parse_total(self, part_id, set_key, set_markets, outcome_ids):
        for mid in self._outcomes_types["total1"].get(part_id, []):
            over = under = None
            line = 0.0
            for oid, o, kf in self._parse_market_outcomes(mid):
                try:
                    adv = float(o.get("adValue") or 0)
                    if adv > 0:
                        line = adv
                except Exception:
                    pass
                side = self._classify_side(o)
                if side == "over":
                    over = (oid, o, kf)
                elif side == "under":
                    under = (oid, o, kf)
            if not (over and under and line > 0):
                continue
            t = set_markets.setdefault(set_key, {}).setdefault("total", {})
            t["line"] = line
            t["over"] = over[2]
            t["under"] = under[2]
            o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
            o["over"] = {"outcomeId": over[0], "factorId": over[1].get("facId"),
                         "kf": over[2], "line": line}
            o["under"] = {"outcomeId": under[0], "factorId": under[1].get("facId"),
                          "kf": under[2], "line": line}

    def _parse_handicap(self, part_id, set_key, set_markets, outcome_ids):
        for mid in self._outcomes_types["handicap1"].get(part_id, []):
            for oid, o, kf in self._parse_market_outcomes(mid):
                side = self._classify_side(o)
                try:
                    adv = float(o.get("adValue") or 0)
                except Exception:
                    continue
                if side not in ("1", "2"):
                    continue
                h = set_markets.setdefault(set_key, {}) \
                    .setdefault("handicap", {}) \
                    .setdefault(side, {})
                h["line"] = adv
                h["odd"] = kf
                outcome_ids.setdefault(set_key, {}) \
                    .setdefault("handicap", {}) \
                    .setdefault(side, {}) \
                    .update({
                        "outcomeId": oid,
                        "factorId":  o.get("facId"),
                        "kf":        kf,
                        "line":      adv,
                    })

    def _parse_it(self, part_id, set_key, set_markets, outcome_ids):
        for mid, m in self._markets.items():
            mtype = (m.get("type") or "").upper()
            if mtype not in ("ITL1", "ITL2"):
                continue
            if m.get("partId") != part_id:
                continue
            player = "1" if mtype == "ITL1" else "2"
            outs = self._parse_market_outcomes(mid)
            if len(outs) < 2:
                continue
            line = 0.0
            for _, o, _ in outs:
                try:
                    adv = float(o.get("adValue") or 0)
                    if adv > 0:
                        line = adv
                        break
                except Exception:
                    continue
            if line <= 0:
                continue
            over = under = None
            for oid, o, kf in outs:
                side = self._classify_side(o)
                if side == "over":
                    over = (oid, o, kf)
                elif side == "under":
                    under = (oid, o, kf)
            if not (over and under):
                sorted_outs = sorted(outs, key=lambda x: str(x[0]))
                if len(sorted_outs) >= 2:
                    under = sorted_outs[0]
                    over  = sorted_outs[1]
            if not (over and under):
                continue
            it = set_markets.setdefault(set_key, {}) \
                .setdefault("it", {}) \
                .setdefault(player, {})
            it["line"] = line
            it["over"] = over[2]
            it["under"] = under[2]
            o = outcome_ids.setdefault(set_key, {}) \
                .setdefault("it", {}) \
                .setdefault(player, {})
            o["over"] = {"outcomeId": over[0], "factorId": over[1].get("facId"),
                         "kf": over[2], "line": line}
            o["under"] = {"outcomeId": under[0], "factorId": under[1].get("facId"),
                          "kf": under[2], "line": line}

    def _parse_odd(self, part_id, set_key, set_markets, outcome_ids):
        mids = list(self._outcomes_types["odd"].get(part_id, []))
        if not mids:
            for mid, m in self._markets.items():
                if (m.get("type") or "").upper() == "ODD" \
                        and m.get("partId") == part_id:
                    mids.append(mid)
        for mid in mids:
            outs = self._parse_market_outcomes(mid)
            if len(outs) < 2:
                continue
            even_out = odd_out = None
            for oid, o, kf in outs:
                side = self._classify_side(o)
                if side == "even":
                    even_out = (oid, o, kf)
                elif side == "odd":
                    odd_out = (oid, o, kf)
            if not (even_out and odd_out):
                outs_sorted = sorted(outs, key=lambda x: str(x[0]))
                even_out = outs_sorted[0]
                odd_out  = outs_sorted[1]
            odd = set_markets.setdefault(set_key, {}).setdefault("odd", {})
            odd["even"] = even_out[2]
            odd["odd"]  = odd_out[2]
            o = outcome_ids.setdefault(set_key, {}).setdefault("odd", {})
            o["even"] = {"outcomeId": even_out[0],
                         "factorId":  even_out[1].get("facId"),
                         "kf":        even_out[2]}
            o["odd"]  = {"outcomeId": odd_out[0],
                         "factorId":  odd_out[1].get("facId"),
                         "kf":        odd_out[2]}

    def _infer_part_id_for_phase(self, phase_num: int) -> Optional[str]:
        pat = re.compile(rf'\b{phase_num}\b')
        for mid, m in self._markets.items():
            title = (m.get("title") or "").lower()
            if "сет" in title and pat.search(title):
                pid = m.get("partId")
                if pid:
                    return pid
        return None

    # ════════════════════════════════════════════════════════════
    # Универсальная сборка набора рынков для парта
    # ════════════════════════════════════════════════════════════
    def _build_market_set(self, part_id: str, set_key: str) -> tuple:
        """Возвращает (set_markets, outcome_ids) для указанного парта."""
        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        self._parse_winner(part_id, set_key, set_markets, outcome_ids)
        self._parse_total(part_id, set_key, set_markets, outcome_ids)
        self._parse_handicap(part_id, set_key, set_markets, outcome_ids)
        self._parse_it(part_id, set_key, set_markets, outcome_ids)
        self._parse_odd(part_id, set_key, set_markets, outcome_ids)

        return set_markets, outcome_ids

    # ════════════════════════════════════════════════════════════
    # Сборка снапшота
    # ════════════════════════════════════════════════════════════
    def _build_snapshot(self) -> Optional[dict]:
        phase_num = self._determine_active_phase()
        if phase_num <= 0:
            return None

        main_part = self._phase_parts.get(phase_num) \
                    or self._infer_part_id_for_phase(phase_num)

        race_part = self._phase_race_parts.get(phase_num)

        if not main_part and not race_part:
            logger.debug(
                f"LigaStavok: нет ни main, ни race парта для фазы {phase_num}"
            )
            return None

        set_key = f"set_{phase_num}"

        # ── Основной сет ──
        main_markets = {}
        main_ids = {}
        if main_part:
            main_markets, main_ids = self._build_market_set(main_part, set_key)

        # ── RACE внутри сета ──
        race_markets = {}
        race_ids = {}
        if race_part:
            race_markets, race_ids = self._build_market_set(race_part, set_key)

        if not main_markets and not race_markets:
            return None

        # ── Счёт партий и sub_score ──
        score1 = score2 = sub1 = sub2 = 0
        if self._scores_all:
            for s in self._scores_all:
                try:
                    s1 = int(s.get("ScoreTeam1") or 0)
                    s2 = int(s.get("ScoreTeam2") or 0)
                except Exception:
                    continue
                if s1 > s2 and s1 >= 11:
                    score1 += 1
                elif s2 > s1 and s2 >= 11:
                    score2 += 1
            active = next(
                (s for s in self._scores_all
                 if str(s.get("PartNumber")) == str(phase_num)),
                None,
            )
            if active:
                try:
                    sub1 = int(active.get("ScoreTeam1") or 0)
                    sub2 = int(active.get("ScoreTeam2") or 0)
                except Exception:
                    pass

        return {
            "match_id":    str(self.target_match_id),
            "sport":       "any",
            "phase_num":   phase_num,
            "score1":      score1,
            "score2":      score2,
            "sub_score1":  sub1,
            "sub_score2":  sub2,

            "set_markets":       main_markets,
            "outcome_ids":       main_ids,

            # RACE — отдельным ключом, опционально
            "race_set_markets":  race_markets,
            "race_outcome_ids":  race_ids,
        }

    async def _snapshot_loop(self):
        while True:
            try:
                await asyncio.sleep(1.0)
                if not self._callback:
                    continue
                snap = self._build_snapshot()
                if not snap:
                    continue
                try:
                    await self._callback(snap)
                except Exception as e:
                    logger.error(f"LigaStavok callback: {e}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"LigaStavok snapshot loop: {e}")
                await asyncio.sleep(1)

    # ════════════════════════════════════════════════════════════
    # place_bet
    # ════════════════════════════════════════════════════════════
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        script = f"""
        (async function() {{
            const data = {json.dumps(bet_data)};

            const creds = window.__ls_creds || {{}};
            let apiCred = creds['x-api-cred'] || '';
            let xUser = creds['x-user'] || '';
            let accountNumber = parseInt(creds['accountNumber']) || 0;

            if (!apiCred) {{
                const getCookie = (name) => {{
                    const m = document.cookie.match(
                        new RegExp('(^|; )' + name + '=([^;]*)'));
                    return m ? decodeURIComponent(m[2]) : '';
                }};
                const cookieToken = getCookie('token');
                if (cookieToken) apiCred = '|' + cookieToken;
            }}
            if (!apiCred) return {{ success: false, error: 'no x-api-cred' }};

            if (!accountNumber) {{
                try {{
                    const resp = await fetch('https://api.ligastavok.ru/rest/auth/v2/getBalance', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'X-API-CRED': apiCred,
                            'X-Application-Name': 'mobile',
                            'X-Req-Id': crypto.randomUUID(),
                            'Origin': 'https://www.ligastavok.ru',
                            'Referer': window.location.href,
                            ...(xUser ? {{'X-User': String(xUser)}} : {{}}),
                        }},
                        credentials: 'include',
                        body: '{{}}',
                    }});
                    const gdata = await resp.json();
                    const gresult = gdata && gdata.result;
                    if (Array.isArray(gresult) && gresult.length > 0 && gresult[0].number) {{
                        accountNumber = gresult[0].number;
                        window.__ls_creds['accountNumber'] = accountNumber;
                    }}
                }} catch(e) {{ /* ignore */ }}
            }}
            if (!accountNumber) return {{ success: false, error: 'не удалось получить accountNumber' }};
            if (!data.outcomeId || !data.factorId) return {{ success: false, error: 'missing ids' }};

            let eventId = parseInt(data.event_id) || 0;
            if (!eventId) {{
                const m = window.location.href.match(/-id-(\\d+)/);
                if (m) eventId = parseInt(m[1], 10);
            }}
            if (!eventId) return {{ success: false, error: 'не удалось определить eventId' }};

            const requestId = crypto.randomUUID();
            const requestTime = Math.floor(Date.now() / 1000);
            const xReqId = crypto.randomUUID();

            const commonHeaders = {{
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-API-CRED': apiCred,
                'X-Application-Name': 'mobile',
                'Origin': 'https://www.ligastavok.ru',
                'Referer': window.location.href,
                ...(xUser ? {{'X-User': String(xUser)}} : {{}}),
            }};

            try {{
                await fetch('https://api.ligastavok.ru/rest/bets/v1/getLimitsBatch', {{
                    method: 'POST',
                    headers: {{ ...commonHeaders, 'X-Req-Id': crypto.randomUUID() }},
                    credentials: 'include',
                    body: JSON.stringify({{
                        currency: 'RUR',
                        outcomeIds: [parseInt(data.outcomeId, 10)],
                        sbp: true,
                    }}),
                }});
            }} catch(e) {{ /* ignore */ }}

            let checkOk = false, checkFull = null;
            try {{
                const checkResp = await fetch('https://api.ligastavok.ru/api/bets/v1/bet/check', {{
                    method: 'POST',
                    headers: {{ ...commonHeaders, 'X-Req-Id': crypto.randomUUID() }},
                    credentials: 'include',
                    body: JSON.stringify([{{
                        eventId: eventId,
                        outcomeId: parseInt(data.outcomeId, 10),
                    }}]),
                }});
                checkFull = await checkResp.json();
                if (checkFull && checkFull.httpCode === 200 && checkFull.error === null) {{
                    checkOk = true;
                }}
            }} catch(e) {{
                return {{ success: false, error: 'bet/check network: ' + e.message }};
            }}
            if (!checkOk) {{
                return {{ success: false, error: 'bet/check отбит',
                          diag: {{ checkResp: checkFull }} }};
            }}

            try {{
                await fetch('https://api.ligastavok.ru/rest/bets/v1/getLimitsBatch', {{
                    method: 'POST',
                    headers: {{ ...commonHeaders, 'X-Req-Id': crypto.randomUUID() }},
                    credentials: 'include',
                    body: JSON.stringify({{
                        currency: 'RUR',
                        outcomeIds: [parseInt(data.outcomeId, 10)],
                        sbp: true,
                    }}),
                }});
            }} catch(e) {{ /* ignore */ }}

            try {{
                const payload = {{
                    AcceptAdditionalOddChange: 1,
                    accept: 'all',
                    accountNumber: accountNumber,
                    accountType: 'BOOKMAKER',
                    allowBetSharing: false,
                    bets: [{{
                        amount: Math.round(data.amount),
                        dimension: 1,
                        outcomes: [{{
                            factorId: parseInt(data.factorId, 10),
                            outcomeId: parseInt(data.outcomeId, 10),
                        }}]
                    }}],
                    isDraft: false,
                    requestId: requestId,
                    requestTime: requestTime,
                }};

                const resp = await fetch('https://api.ligastavok.ru/rest/bets/v3/makeBet', {{
                    method: 'POST',
                    headers: {{ ...commonHeaders, 'X-Req-Id': xReqId }},
                    credentials: 'include',
                    body: JSON.stringify(payload),
                }});
                const status = resp.status;
                const rawText = await resp.text();
                let result;
                try {{ result = JSON.parse(rawText); }}
                catch(e) {{
                    return {{ success: false, error: 'non-JSON: HTTP ' + status + ' · ' + rawText.slice(0, 300) }};
                }}
                if (!result || result.httpCode !== 200 || result.error !== null) {{
                    return {{ success: false,
                              error: 'makeBet: HTTP=' + status + ' httpCode=' + (result && result.httpCode) }};
                }}
                const betResult = result.result && result.result[0];
                if (betResult && betResult.errorCode === 0) {{
                    return {{ success: true, betId: betResult.betId }};
                }}
                return {{ success: false,
                          error: 'bet rejected: ' + JSON.stringify(betResult).slice(0, 300) }};
            }} catch(e) {{
                return {{ success: false, error: 'network: ' + e.message }};
            }}
        }})();
        """
        return await page.evaluate(script)