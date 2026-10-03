"""
Zenit handler — instance-based.
Транспорт: WebSocket (wss://zenit.win/wss)
Фреймы:
  t=38 — score + sScore (фазы)
  t=36 — все odds + headTable + bodyTables (точная разметка рынков)

Стратегия парсинга: НЕ хардкодим market type IDs (они разные для НТ/волейбола/баскета).
Вместо этого используем headTable и bodyTables как карту:
  - headTable содержит winner/фора/тотал для каждой фазы
  - bodyTables содержит ИТ/очки/чёт-нечет по названиям
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional, Dict, Any, List, Callable

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
        self._last_sent_state: Optional[tuple] = None
        # Состояние: {match_id: {"score": {...}, "tables": {...}}}
        self._match_state: Dict[str, Dict[str, Any]] = {}

    # ============================================================
    # Подключение / отключение
    # ============================================================
    async def setup_listener(self, page: Page, callback,
                             match_id=None, match_teams=None):
        if match_id:
            self.target_match_id = str(match_id)
        if match_teams:
            self.target_teams = list(match_teams)

        if not self.target_match_id:
            m = re.search(r'/(\d{7,})(?:[/?]|$)', page.url)
            if m:
                self.target_match_id = m.group(1)

        self._page = page
        self._callback = callback
        self._last_sent_state = None
        self._match_state = {}

        page.on("websocket", self._on_websocket)
        logger.info(f"Zenit: listener установлен (target={self.target_match_id})")

    async def stop_listener(self, page: Page):
        try:
            page.remove_listener("websocket", self._on_websocket)
        except Exception:
            pass
        self._callback = None
        self.target_match_id = None
        self._last_sent_state = None
        logger.info("Zenit: listener снят")

    # ============================================================
    # WebSocket
    # ============================================================
    def _on_websocket(self, ws: WebSocket):
        if "zenit.win/wss" not in ws.url:
            return
        ws.on("framereceived", self._on_ws_frame)
        logger.info(f"Zenit: WS подключён {ws.url}")

    def _on_ws_frame(self, payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", "ignore")
            data = json.loads(payload)
            if not isinstance(data, dict):
                return

            t = data.get("t")
            d = data.get("d") or {}

            if t == 38:
                self._handle_score_frame(d)
            elif t == 36:
                self._handle_tables_frame(d)

        except Exception as e:
            logger.debug(f"Zenit WS parse error: {e}")

    def _handle_score_frame(self, d: dict):
        """t=38: обновления счёта и фаз."""
        matches = d.get("matches") or {}
        if not isinstance(matches, dict):
            return

        for gid, info in matches.items():
            gid = str(gid)
            if self.target_match_id and gid != self.target_match_id:
                continue
            state = self._match_state.setdefault(gid, {})
            state["score"] = info
            asyncio.create_task(self._emit_if_changed(gid))

    def _handle_tables_frame(self, d: dict):
        """t=36: odds + headTable + bodyTables."""
        match_id = str(d.get("id", ""))
        if not match_id:
            return
        if self.target_match_id and match_id != self.target_match_id:
            return

        state = self._match_state.setdefault(match_id, {})
        state["tables"] = d
        asyncio.create_task(self._emit_if_changed(match_id))

    # ============================================================
    # Сборка и отправка
    # ============================================================
    async def _emit_if_changed(self, match_id: str):
        parsed = self._build_parsed(match_id)
        if not parsed:
            return

        state = self._state_hash(parsed)
        if state == self._last_sent_state:
            return
        self._last_sent_state = state

        if self._callback:
            try:
                await self._callback(parsed)
            except Exception as e:
                logger.error(f"Zenit callback: {e}")

    def _build_parsed(self, match_id: str) -> Optional[dict]:
        state = self._match_state.get(match_id)
        if not state:
            return None

        score_info = state.get("score") or {}
        tables_info = state.get("tables") or {}

        if not score_info:
            return None

        # ── Счёт матча ──
        score_str = score_info.get("score", "0:0") or "0:0"
        m = re.match(r"\*?(\d+):(\d+)", score_str)
        if m:
            score1, score2 = int(m.group(1)), int(m.group(2))
        else:
            score1 = score2 = 0

        # ── Фазы из sScore ──
        sub1, sub2 = 0, 0
        phase_num = 1
        phase_name = "1-я партия"
        sport_hint = "table_tennis"  # по умолчанию

        s_score = score_info.get("sScore") or {}
        s_score_data = s_score.get("sScoreData") or {}
        scs = s_score_data.get("sScoreData") or s_score_data.get("scs") or []

        if scs:
            # Берём последний с ненулевым 'o' и 'd' (названием фазы)
            phase_entries = [s for s in scs if s.get("d")]
            if phase_entries:
                last = max(phase_entries, key=lambda x: x.get("o", 0))
                scv = last.get("scv") or {}
                cur = scv.get("cur") or {}
                try:
                    sub1 = int(cur.get("t1", 0) or 0)
                    sub2 = int(cur.get("t2", 0) or 0)
                except Exception:
                    pass
                phase_name = last.get("d", "") or ""
                mm = re.search(r"(\d+)", phase_name)
                if mm:
                    phase_num = int(mm.group(1))

                # Определяем спорт по названию фазы
                if "четверть" in phase_name:
                    sport_hint = "basketball"
                elif "сет" in phase_name or "партия" in phase_name:
                    sport_hint = "table_tennis"  # или volleyball — уточним

        # ── Рынки из headTable/bodyTables ──
        set_markets: Dict[str, dict] = {}
        outcome_ids: Dict[str, dict] = {}

        if tables_info and phase_num:
            set_key = f"set_{phase_num}"
            odds = tables_info.get("odds") or {}
            head_table = tables_info.get("headTable") or []
            body_tables = tables_info.get("bodyTables") or []

            ZenitHandler._parse_from_head_table(
                head_table, odds, phase_num, set_key,
                set_markets, outcome_ids
            )
            ZenitHandler._parse_from_body_tables(
                body_tables, odds, phase_num, set_key,
                sub1, sub2, set_markets, outcome_ids
            )

        return {
            "match_id": match_id,
            "sport": sport_hint,
            "phase_num": phase_num,
            "phase_name": phase_name,
            "score1": score1,
            "score2": score2,
            "sub_score1": sub1,
            "sub_score2": sub2,
            "set_markets": set_markets,
            "outcome_ids": outcome_ids,
        }

    # ============================================================
    # Парсинг из headTable (winner/фора/тотал текущей фазы)
    # ============================================================
    @staticmethod
    def _parse_from_head_table(head_table: list, odds: dict,
                               phase_num: int, set_key: str,
                               set_markets: dict, outcome_ids: dict):
        """
        headTable — список записей вида:
          [{"txt":"2-я партия"}, {"bet":4008}, {"bet":4010},
           {"txt":"2.5"}, {"bet":4011}, {"txt":"-2.5"}, {"bet":4012},
           {"bet":4013}, {"txt":"46.5"}, {"bet":4014}]
        Ищем запись где txt содержит номер текущей фазы.
        """
        # Ищем запись для текущей фазы
        target_row = None
        for row in head_table:
            if not isinstance(row, list):
                continue
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                txt = cell.get("txt", "")
                if not isinstance(txt, str):
                    continue
                mm = re.search(r"(\d+)", txt)
                if mm and int(mm.group(1)) == phase_num:
                    # Нашли фазу — берём весь row
                    target_row = row
                    break
            if target_row:
                break

        if not target_row:
            return

        # Извлекаем bet IDs в порядке их появления
        bets_in_order = []
        txt_before_bet = None
        for cell in target_row:
            if not isinstance(cell, dict):
                continue
            if "txt" in cell:
                txt_before_bet = str(cell.get("txt", ""))
            if "bet" in cell:
                bet_id = str(cell.get("bet"))
                bets_in_order.append({
                    "bet_id": bet_id,
                    "txt": txt_before_bet,
                })
                txt_before_bet = None

        if len(bets_in_order) < 2:
            return

        # ── WINNER: первые 2 bet'а ──
        w = set_markets.setdefault(set_key, {}).setdefault("winner", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("winner", {})

        bet1 = bets_in_order[0]
        bet2 = bets_in_order[1]
        odd1 = ZenitHandler._get_odd(odds, bet1["bet_id"])
        odd2 = ZenitHandler._get_odd(odds, bet2["bet_id"])
        if odd1:
            w["1"] = odd1["cf"]
            o["1"] = {"id": bet1["bet_id"], "kf": odd1["cf"]}
        if odd2:
            w["2"] = odd2["cf"]
            o["2"] = {"id": bet2["bet_id"], "kf": odd2["cf"]}

        # ── HANDICAP: пары (txt=линия, bet) ──
        # Ищем bet'ы с txt содержащим число (линия форы)
        handicap_pairs = []
        for i, b in enumerate(bets_in_order[2:], 2):
            txt = b.get("txt") or ""
            if re.match(r"^[+-]?\d+\.?\d*$", txt):
                handicap_pairs.append(b)

        if len(handicap_pairs) >= 2:
            hk = set_markets.setdefault(set_key, {}).setdefault("handicap", {})
            o = outcome_ids.setdefault(set_key, {}).setdefault("handicap", {})
            # Первый = Ф1, второй = Ф2
            h1 = handicap_pairs[0]
            h2 = handicap_pairs[1]
            try:
                line1 = float(h1["txt"])
                line2 = float(h2["txt"])
            except ValueError:
                line1 = line2 = 0

            odd_h1 = ZenitHandler._get_odd(odds, h1["bet_id"])
            odd_h2 = ZenitHandler._get_odd(odds, h2["bet_id"])
            if odd_h1:
                hk.setdefault("1", {})["line"] = line1
                hk["1"]["odd"] = odd_h1["cf"]
                o.setdefault("1", {})["id"] = h1["bet_id"]
                o["1"]["kf"] = odd_h1["cf"]
                o["1"]["line"] = line1
            if odd_h2:
                hk.setdefault("2", {})["line"] = line2
                hk["2"]["odd"] = odd_h2["cf"]
                o.setdefault("2", {})["id"] = h2["bet_id"]
                o["2"]["kf"] = odd_h2["cf"]
                o["2"]["line"] = line2

        # ── TOTAL: оставшиеся bet'ы (без txt или с txt=линия) ──
        # Берём последние 2-3 bet'а с линиями
        total_candidates = []
        for b in bets_in_order[2:]:
            if b in handicap_pairs:
                continue
            total_candidates.append(b)

        if len(total_candidates) >= 2:
            t = set_markets.setdefault(set_key, {}).setdefault("total", {})
            o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})

            # Ищем пару с одинаковой линией
            for i in range(len(total_candidates)):
                for j in range(i + 1, len(total_candidates)):
                    b_i = total_candidates[i]
                    b_j = total_candidates[j]
                    line_i = b_i.get("txt")
                    line_j = b_j.get("txt")
                    if line_i and line_j and line_i == line_j:
                        try:
                            line = float(line_i)
                        except ValueError:
                            continue
                        odd_i = ZenitHandler._get_odd(odds, b_i["bet_id"])
                        odd_j = ZenitHandler._get_odd(odds, b_j["bet_id"])
                        if not odd_i or not odd_j:
                            continue
                        # Определяем over/under по cf (меньший cf = over)
                        if odd_i["cf"] < odd_j["cf"]:
                            over_bet, over_cf = b_i["bet_id"], odd_i["cf"]
                            under_bet, under_cf = b_j["bet_id"], odd_j["cf"]
                        else:
                            over_bet, over_cf = b_j["bet_id"], odd_j["cf"]
                            under_bet, under_cf = b_i["bet_id"], odd_i["cf"]
                        t["line"] = line
                        t["over"] = over_cf
                        t["under"] = under_cf
                        o["over"] = {"id": over_bet, "kf": over_cf, "line": line}
                        o["under"] = {"id": under_bet, "kf": under_cf, "line": line}
                        return  # нашли — выходим

    # ============================================================
    # Парсинг из bodyTables (ИТ/очки/чёт-нечет)
    # ============================================================
    @staticmethod
    def _parse_from_body_tables(body_tables: list, odds: dict,
                                 phase_num: int, set_key: str,
                                 sub1: int, sub2: int,
                                 set_markets: dict, outcome_ids: dict):
        """
        bodyTables — список таблиц вида:
          {"id": 1792, "rows": [[...], [{"txt":"46.5"}, {"bet":...}, {"bet":...}]]}
        Ищем таблицы для текущей фазы по номеру в заголовке.
        """
        for table in body_tables:
            if not isinstance(table, dict):
                continue
            rows = table.get("rows") or []
            if not rows:
                continue

            # Заголовок таблицы — первый row
            header = rows[0] if rows else []
            header_txt = " ".join(
                str(c.get("txt", "")) for c in header if isinstance(c, dict)
            ).lower()

            # Проверяем что таблица для текущей фазы
            if not ZenitHandler._is_phase_table(header_txt, phase_num):
                continue

            # ── ТОТАЛ фазы ──
            if "тотал" in header_txt and "индивидуальн" not in header_txt:
                ZenitHandler._parse_total_table(
                    rows, odds, set_key, set_markets, outcome_ids
                )

            # ── ИНДИВИДУАЛЬНЫЕ ТОТАЛЫ ──
            elif "индивидуальн" in header_txt:
                ZenitHandler._parse_it_table(
                    rows, odds, set_key, set_markets, outcome_ids
                )

            # ── ВЫИГРАЕТ ОЧКО (N-е очко) ──
            elif "выиграет очко" in header_txt or "очко" in header_txt:
                ZenitHandler._parse_point_table(
                    rows, odds, set_key, sub1, sub2,
                    set_markets, outcome_ids
                )

            # ── ЧЁТ/НЕЧЕТ ──
            elif "кол-во очков" in header_txt or "чет" in header_txt:
                ZenitHandler._parse_odd_even_table(
                    rows, odds, set_key, set_markets, outcome_ids
                )

    @staticmethod
    def _is_phase_table(header_txt: str, phase_num: int) -> bool:
        """Проверяет что заголовок таблицы относится к текущей фазе."""
        # Ищем номер фазы в заголовке
        mm = re.search(r"(\d+)", header_txt)
        if mm and int(mm.group(1)) == phase_num:
            return True
        # Для "основных" таблиц (без номера) — не фазовая
        return False

    @staticmethod
    def _parse_total_table(rows, odds, set_key, set_markets, outcome_ids):
        """Парсит таблицу тоталов: rows = [[заголовки], [line, bet_M, bet_B], ...]"""
        t = set_markets.setdefault(set_key, {}).setdefault("total", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("total", {})
        if "line" in t:
            return

        for row in rows[1:]:
            cells = [c for c in row if isinstance(c, dict)]
            if len(cells) < 3:
                continue
            txt = str(cells[0].get("txt", ""))
            try:
                line = float(txt)
            except ValueError:
                continue
            # Ищем 2 bet'а
            bets = [c.get("bet") for c in cells[1:] if "bet" in c]
            if len(bets) < 2:
                continue
            odd1 = ZenitHandler._get_odd(odds, str(bets[0]))
            odd2 = ZenitHandler._get_odd(odds, str(bets[1]))
            if not odd1 or not odd2:
                continue
            # Меньший cf = over (больше)
            if odd1["cf"] < odd2["cf"]:
                over_bet, over_cf = str(bets[0]), odd1["cf"]
                under_bet, under_cf = str(bets[1]), odd2["cf"]
            else:
                over_bet, over_cf = str(bets[1]), odd2["cf"]
                under_bet, under_cf = str(bets[0]), odd1["cf"]
            t["line"] = line
            t["over"] = over_cf
            t["under"] = under_cf
            o["over"] = {"id": over_bet, "kf": over_cf, "line": line}
            o["under"] = {"id": under_bet, "kf": under_cf, "line": line}
            break

    @staticmethod
    def _parse_it_table(rows, odds, set_key, set_markets, outcome_ids):
        """Парсит таблицу индивидуальных тоталов."""
        it_dict = set_markets.setdefault(set_key, {}).setdefault("it", {})
        o_dict = outcome_ids.setdefault(set_key, {}).setdefault("it", {})

        current_player = None
        for row in rows[1:]:
            cells = [c for c in row if isinstance(c, dict)]
            if not cells:
                continue
            # Первая ячейка может быть именем игрока (с rs=несколько строк)
            first_txt = str(cells[0].get("txt", ""))
            if first_txt and not re.match(r"^\d", first_txt):
                # Это имя игрока
                if not it_dict.get("1"):
                    current_player = "1"
                else:
                    current_player = "2"
                cells = cells[1:]  # пропускаем имя

            if not current_player or not cells:
                continue

            txt = str(cells[0].get("txt", ""))
            try:
                line = float(txt)
            except ValueError:
                continue
            bets = [c.get("bet") for c in cells[1:] if "bet" in c]
            if len(bets) < 2:
                continue
            odd1 = ZenitHandler._get_odd(odds, str(bets[0]))
            odd2 = ZenitHandler._get_odd(odds, str(bets[1]))
            if not odd1 or not odd2:
                continue

            it = it_dict.setdefault(current_player, {})
            o = o_dict.setdefault(current_player, {})
            if "line" in it:
                continue
            if odd1["cf"] < odd2["cf"]:
                over_bet, over_cf = str(bets[0]), odd1["cf"]
                under_bet, under_cf = str(bets[1]), odd2["cf"]
            else:
                over_bet, over_cf = str(bets[1]), odd2["cf"]
                under_bet, under_cf = str(bets[0]), odd1["cf"]
            it["line"] = line
            it["over"] = over_cf
            it["under"] = under_cf
            o["over"] = {"id": over_bet, "kf": over_cf, "line": line}
            o["under"] = {"id": under_bet, "kf": under_cf, "line": line}

    @staticmethod
    def _parse_point_table(rows, odds, set_key, sub1, sub2,
                           set_markets, outcome_ids):
        """Парсит таблицу очков: rows = [..., [txt="N-е очко", bet_1, bet_2], ...]"""
        points = {}  # N -> {"1": odd, "2": odd}
        for row in rows[1:]:
            cells = [c for c in row if isinstance(c, dict)]
            if len(cells) < 3:
                continue
            txt = str(cells[0].get("txt", ""))
            mm = re.search(r"(\d+)", txt)
            if not mm:
                continue
            n = int(mm.group(1))
            bets = [c.get("bet") for c in cells[1:] if "bet" in c]
            if len(bets) < 2:
                continue
            odd1 = ZenitHandler._get_odd(odds, str(bets[0]))
            odd2 = ZenitHandler._get_odd(odds, str(bets[1]))
            if not odd1 or not odd2:
                continue
            points[n] = {
                "1": {"bet": str(bets[0]), "cf": odd1["cf"]},
                "2": {"bet": str(bets[1]), "cf": odd2["cf"]},
            }

        if not points:
            return

        # Выбираем ближайшее к sub1+sub2+1
        target = sub1 + sub2 + 1
        nums = sorted(points.keys())
        chosen = None
        if target in nums:
            chosen = target
        else:
            for n in nums:
                if n >= target:
                    chosen = n
                    break
            if chosen is None:
                chosen = nums[0]

        pm = set_markets.setdefault(set_key, {}).setdefault("point", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("point", {})
        pt = points[chosen]
        pm["1"] = pt["1"]["cf"]
        pm["2"] = pt["2"]["cf"]
        o["1"] = {"id": pt["1"]["bet"], "kf": pt["1"]["cf"]}
        o["2"] = {"id": pt["2"]["bet"], "kf": pt["2"]["cf"]}

    @staticmethod
    def _parse_odd_even_table(rows, odds, set_key, set_markets, outcome_ids):
        """Парсит таблицу чёт/нечет."""
        om = set_markets.setdefault(set_key, {}).setdefault("odd", {})
        o = outcome_ids.setdefault(set_key, {}).setdefault("odd", {})

        for row in rows[1:]:
            cells = [c for c in row if isinstance(c, dict)]
            for i, cell in enumerate(cells):
                txt = str(cell.get("txt", "")).lower()
                if "чет" in txt and "нечет" not in txt:
                    # Следующий bet = even
                    for c in cells[i+1:]:
                        if "bet" in c:
                            odd = ZenitHandler._get_odd(odds, str(c["bet"]))
                            if odd:
                                om["even"] = odd["cf"]
                                o["even"] = {"id": str(c["bet"]), "kf": odd["cf"]}
                            break
                elif "нечет" in txt:
                    for c in cells[i+1:]:
                        if "bet" in c:
                            odd = ZenitHandler._get_odd(odds, str(c["bet"]))
                            if odd:
                                om["odd"] = odd["cf"]
                                o["odd"] = {"id": str(c["bet"]), "kf": odd["cf"]}
                            break

    # ============================================================
    # Утилиты
    # ============================================================
    @staticmethod
    def _get_odd(odds: dict, bet_id: str) -> Optional[dict]:
        """Извлекает odd по bet_id."""
        odd_info = odds.get(bet_id)
        if not odd_info or not isinstance(odd_info, dict):
            return None
        cf = odd_info.get("cf")
        if cf is None:
            return None
        return {"cf": cf, "oddKey": odd_info.get("oddKey", "")}

    # ============================================================
    # Хэш состояния
    # ============================================================
    @staticmethod
    def _state_hash(parsed: dict) -> tuple:
        sm = parsed.get("set_markets") or {}
        phase = parsed.get("phase_num")
        set_key = f"set_{phase}" if phase else None
        markets = sm.get(set_key, {}) if set_key else {}
        w = markets.get("winner") or {}
        t = markets.get("total") or {}
        hk = markets.get("handicap") or {}
        od = markets.get("odd") or {}
        it = markets.get("it") or {}
        pt = markets.get("point") or {}
        return (
            phase,
            parsed.get("score1"), parsed.get("score2"),
            parsed.get("sub_score1"), parsed.get("sub_score2"),
            w.get("1"), w.get("2"),
            t.get("line"), t.get("over"), t.get("under"),
            hk.get("1", {}).get("odd"), hk.get("2", {}).get("odd"),
            od.get("even"), od.get("odd"),
            it.get("1", {}).get("over"), it.get("2", {}).get("over"),
            pt.get("1"), pt.get("2"),
        )

    # ============================================================
    # Размещение ставки
    # ============================================================
    @staticmethod
    async def place_bet(page: Page, bet_data: dict) -> dict:
        """
        TODO: Реализовать после изучения API ставок Zenit.
        Нужен дамп POST-запроса на размещение ставки из DevTools.
        """
        logger.warning("Zenit place_bet: NOT IMPLEMENTED YET")
        return {"success": False, "error": "Not implemented"}