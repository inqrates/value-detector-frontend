# -*- coding: utf-8 -*-
"""
Тесты парсинга WS-дампов Onewin (ui/after_goal/onewin.py).

Запуск:
    venv\\Scripts\\python.exe -m unittest tests.test_onewin -v

Данные — реальные фреймы 42["u",...] из дампов 2026-11-24
(НТ / баскет / волейбол). Проверяем:
  - match-info: счёт матча, sub текущей партии, фазу по status;
  - match-odds-snapshot: разметку групп по названиям;
  - инкременты match-odds (без name в группах) не ломают разметку;
  - place_bet payload (без сети — только сборка).
"""
import asyncio
import json
import unittest

from ui.after_goal.onewin import (
    OnewinHandler,
    _parse_group_name,
)


# ── Реальные фреймы из дампов (сокращены до значимых групп) ──

TT_INFO = {
    "matchId": 43721681,
    "status": "2-й сет",
    "sportId": 24,
    "matchScore": {"t1": "0", "t2": "1"},
    "periodsScore": [{"t1": "9", "t2": "11"}, {"t1": "9", "t2": "10"}],
}

TT_ODDS_SNAPSHOT = {
    "matchId": 43721681,
    "oddsGroups": [
        {
            "id": "18887", "isBase": True,
            "name": "2-й cет. Победитель",
            "renderType": "cols-2", "outcomes": ["1", "2"],
            "oddsList": [
                {"cf": 4.33, "id": "10:199835950121009200:1",
                 "name": "Олег Максимкив", "outcome": "1", "status": 1},
                {"cf": 1.2, "id": "10:159507497421009200:1",
                 "name": "Орест Мельник", "outcome": "2", "status": 1},
            ],
        },
        {
            "id": "18717", "isBase": True,
            "name": "3-й cет. Тотал",
            "renderType": "total-2", "outcomes": ["under", "over"],
            "oddsList": [
                {"cf": 1.91, "id": "10:140719793921009230:1",
                 "name": "Больше 18.5", "outcome": "over", "status": 1,
                 "vars": {"v1": "18.5"}},
                {"cf": 1.8, "id": "10:28810549121009224:1",
                 "name": "Меньше 18.5", "outcome": "under", "status": 1,
                 "vars": {"v1": "18.5"}},
            ],
        },
        {
            "id": "18722", "isBase": True,
            "name": "3-й cет. Фора",
            "renderType": "fora-2", "outcomes": ["1", "2"],
            "oddsList": [
                {"cf": 2.0, "id": "10:27041046921009224:1",
                 "name": "Орест Мельник -2.5", "outcome": "2", "status": 1,
                 "vars": {"v1": "-2.5"}},
                {"cf": 1.73, "id": "10:40761466521009224:1",
                 "name": "Олег Максимкив 2.5", "outcome": "1", "status": 1,
                 "vars": {"v1": "2.5"}},
            ],
        },
        {
            "id": "82674", "isBase": False,
            "name": "3-й сет. Нечет/Чет",
            "renderType": "cols-2",
            "oddsList": [
                {"cf": 1.53, "id": "10:114623935221009230:1",
                 "name": "Четное", "outcome": "even", "status": 1},
                {"cf": 2.38, "id": "10:73670628321009230:1",
                 "name": "Нечетное", "outcome": "odd", "status": 1},
            ],
        },
        {
            "id": "82675", "isBase": False,
            "name": "3-й сет. Первый до",
            "renderType": "race-to",
            "oddsList": [
                {"cf": 2.38, "id": "10:88082116621009230:1",
                 "name": "Олег Максимкив 7", "outcome": "1", "status": 1,
                 "vars": {"v1": "7"}},
                {"cf": 1.53, "id": "10:156849734321009200:1",
                 "name": "Орест Мельник 7", "outcome": "2", "status": 1,
                 "vars": {"v1": "7"}},
                {"cf": 2.1, "id": "10:144547117021009200:1",
                 "name": "Олег Максимкив 3", "outcome": "1", "status": 1,
                 "vars": {"v1": "3"}},
                {"cf": 1.67, "id": "10:213314734721009200:1",
                 "name": "Орест Мельник 3", "outcome": "2", "status": 1,
                 "vars": {"v1": "3"}},
            ],
        },
        {
            "id": "101876", "isBase": False,
            "name": "Ставки на очки",
            "renderType": "cols-2",
            "oddsList": [
                {"cf": 1.69, "id": "10:95881566321006080:1",
                 "name": "Победа Олег В Сете 2, Очко 4",
                 "outcome": "1", "status": 1,
                 "vars": {"v1": "2", "v2": "4"}},
                {"cf": 1.96, "id": "10:59542042621006080:1",
                 "name": "Победа Орест В Сете 2, Очко 4",
                 "outcome": "2", "status": 1,
                 "vars": {"v1": "2", "v2": "4"}},
            ],
        },
        # Матч-рынки (не текущая фаза) — должны быть отброшены
        {
            "id": "18195", "isBase": True,
            "name": "Тотал",
            "renderType": "total-2", "outcomes": ["under", "over"],
            "oddsList": [
                {"cf": 1.83, "id": "10:48626954521009224:1",
                 "name": "Больше 67.5", "outcome": "over", "status": 1,
                 "vars": {"v1": "67.5"}},
                {"cf": 1.83, "id": "10:184206333521009200:1",
                 "name": "Меньше 67.5", "outcome": "under", "status": 1,
                 "vars": {"v1": "67.5"}},
            ],
        },
    ],
}

# Инкремент match-odds: группы без name (только id), кэфы обновились;
# группа 18737 (Точный счет) закрылась — все исходы status=2
TT_ODDS_INCREMENT = {
    "matchId": 43721681,
    "oddsGroups": [
        {
            "id": "18887", "isBase": True,
            "oddsList": [
                {"id": "10:199835950121009200:1", "cf": 5.5, "status": 1},
                {"id": "10:159507497421009200:1", "cf": 1.12, "status": 1},
            ],
        },
        {
            "id": "18737", "isBase": False,
            "oddsList": [
                {"id": "10:14145143921009226:1", "status": 2},
                {"id": "10:54473596621009224:1", "status": 2},
            ],
        },
    ],
}

BASKET_INFO = {
    "matchId": 43741532,
    "status": "2-я четверть",
    "sportId": 23,
    "matchScore": {"t1": "24", "t2": "38"},
    "periodsScore": [{"t1": "9", "t2": "16"}, {"t1": "15", "t2": "22"}],
}

BASKET_ODDS_SNAPSHOT = {
    "matchId": 43741532,
    "oddsGroups": [
        {
            "id": "10297", "isBase": True,
            "name": "3-я четверть. Тотал",
            "renderType": "total-2", "outcomes": ["under", "over"],
            "oddsList": [
                {"cf": 1.82, "id": "10:152411051821013200:1",
                 "name": "Больше 34.5", "outcome": "over", "status": 1,
                 "vars": {"v1": "34.5"}},
                {"cf": 1.85, "id": "10:141035662421013200:1",
                 "name": "Меньше 34.5", "outcome": "under", "status": 1,
                 "vars": {"v1": "34.5"}},
            ],
        },
        {
            "id": "153949", "isBase": False,
            "name": "3-я четверть. Индивидуальный тотал Спартак Ногинск (ж)",
            "renderType": "total-2",
            "oddsList": [
                {"cf": 1.72, "id": "10:155293050021013200:1",
                 "name": "Больше 21.5", "outcome": "over", "status": 1,
                 "vars": {"v1": "21.5"}},
                {"cf": 1.92, "id": "10:149621848621013200:1",
                 "name": "Меньше 21.5", "outcome": "under", "status": 1,
                 "vars": {"v1": "21.5"}},
            ],
        },
        {
            "id": "153950", "isBase": False,
            "name": "3-я четверть. Индивидуальный тотал Надежда Оренбург-2 (ж)",
            "renderType": "total-2",
            "oddsList": [
                {"cf": 1.99, "id": "10:17356777321013194:1",
                 "name": "Меньше 12.5", "outcome": "under", "status": 1,
                 "vars": {"v1": "12.5"}},
                {"cf": 1.67, "id": "10:208622416321013200:1",
                 "name": "Больше 12.5", "outcome": "over", "status": 1,
                 "vars": {"v1": "12.5"}},
            ],
        },
    ],
}


def _wrap_socket_frame(payload: dict, msg_type: str) -> str:
    """Эмулирует Socket.IO 42["u",{...}] фрейм (как у реального сервера)."""
    return '42' + json.dumps(
        ["u", {"data": payload, "messageType": msg_type}],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class TestParseGroupName(unittest.TestCase):
    def test_set_winner(self):
        kind, phase, _ = _parse_group_name("2-й cет. Победитель")
        self.assertEqual((kind, phase), ("winner", 2))

    def test_set_winner_latin_c(self):
        # Латинская «c» в слове «cет» — реальный кейс из дампов
        kind, phase, _ = _parse_group_name("3-й cет. Тотал")
        self.assertEqual((kind, phase), ("total", 3))

    def test_quarter_handicap(self):
        kind, phase, _ = _parse_group_name("3-я четверть. Фора")
        self.assertEqual((kind, phase), ("handicap", 3))

    def test_quarter_it(self):
        kind, phase, _ = _parse_group_name(
            "3-я четверть. Индивидуальный тотал Спартак Ногинск (ж)"
        )
        self.assertEqual((kind, phase), ("it", 3))

    def test_match_total_no_phase(self):
        kind, phase, _ = _parse_group_name("Тотал (вкл. ОТ)")
        self.assertEqual((kind, phase), ("total", 0))

    def test_odd(self):
        kind, phase, _ = _parse_group_name("3-й сет. Нечет/Чет")
        self.assertEqual((kind, phase), ("odd", 3))

    def test_race(self):
        kind, phase, _ = _parse_group_name("3-й сет. Первый до")
        self.assertEqual((kind, phase), ("race", 0))

    def test_point(self):
        kind, phase, _ = _parse_group_name("Ставки на очки")
        self.assertEqual((kind, phase), ("point", 0))

    def test_correct_score_other(self):
        kind, phase, _ = _parse_group_name("Точный счет")
        self.assertEqual(kind, "other")


class TestOnewinHandlerParsing(unittest.TestCase):
    def setUp(self):
        self.h = OnewinHandler(target_match_id="43721681")

    def _feed(self, h, payload, msg_type):
        h._process_frame(_wrap_socket_frame(payload, msg_type))

    def _drain(self, h):
        """Прогоняем event loop, чтобы async-задачи выполнились."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()

    def test_tt_full_parse(self):
        self._feed(self.h, TT_INFO, "match-info-snapshot")
        self._feed(self.h, TT_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(self.h)

        parsed = self.h._build_parsed()
        self.assertIsNotNone(parsed)
        # match-info: счёт 0:1 по сетам, фаза 2 (status "2-й сет")
        self.assertEqual(parsed["score1"], 0)
        self.assertEqual(parsed["score2"], 1)
        self.assertEqual(parsed["phase_num"], 2)
        # periodsScore[-1] = 9:10 — активная партия
        self.assertEqual(parsed["sub_score1"], 9)
        self.assertEqual(parsed["sub_score2"], 10)

        sm = parsed["set_markets"]
        # Рынки фазы 2
        set2 = sm.get("set_2", {})
        self.assertEqual(set2.get("winner", {}).get("1"), 4.33)
        self.assertEqual(set2.get("winner", {}).get("2"), 1.2)
        # odd фазы 2 (группа "3-й сет..." — другая фаза, не попадает)
        self.assertNotIn("total", set2)
        # Тотал/фора — фаза 3, не текущая → нет
        self.assertNotIn("set_3", sm)

    def test_increment_without_name_uses_cache(self):
        self._feed(self.h, TT_INFO, "match-info-snapshot")
        self._feed(self.h, TT_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(self.h)
        # Инкремент обновляет winner фазы 2 (group 18887 закэширован)
        self._feed(self.h, TT_ODDS_INCREMENT, "match-odds")
        self._drain(self.h)

        parsed = self.h._build_parsed()
        set2 = parsed["set_markets"].get("set_2", {})
        self.assertEqual(set2.get("winner", {}).get("1"), 5.5)
        self.assertEqual(set2.get("winner", {}).get("2"), 1.12)

    def test_status_change_updates_phase(self):
        self._feed(self.h, TT_INFO, "match-info-snapshot")
        self._feed(self.h, TT_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(self.h)

        # Матч перешёл в 3-й сет
        info3 = dict(TT_INFO)
        info3["status"] = "3-й сет"
        info3["matchScore"] = {"t1": "0", "t2": "2"}
        info3["periodsScore"] = [
            {"t1": "9", "t2": "11"}, {"t1": "9", "t2": "11"},
            {"t1": "0", "t2": "0"},
        ]
        self._feed(self.h, info3, "match-info")
        self._drain(self.h)

        parsed = self.h._build_parsed()
        self.assertEqual(parsed["phase_num"], 3)
        self.assertEqual(parsed["score2"], 2)
        self.assertEqual(parsed["sub_score1"], 0)
        # prev_sub — завершённая 2-я партия
        self.assertEqual(parsed["prev_sub_score1"], 9)
        self.assertEqual(parsed["prev_sub_score2"], 11)

    def test_closed_outcome_removed(self):
        self._feed(self.h, TT_INFO, "match-info-snapshot")
        self._feed(self.h, TT_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(self.h)
        # Все исходы winner фазы 2 закрылись (status=2)
        self._feed(self.h, {
            "matchId": 43721681,
            "oddsGroups": [{
                "id": "18887", "isBase": True,
                "oddsList": [
                    {"id": "10:199835950121009200:1", "status": 2},
                    {"id": "10:159507497421009200:1", "status": 2},
                ],
            }],
        }, "match-odds")
        self._drain(self.h)

        parsed = self.h._build_parsed()
        set2 = parsed["set_markets"].get("set_2", {})
        self.assertNotIn("1", set2.get("winner", {}))
        self.assertNotIn("2", set2.get("winner", {}))

    def test_basket_it_players(self):
        h = OnewinHandler(target_match_id="43741532")
        self._feed(h, BASKET_INFO, "match-info-snapshot")
        # Фаза 2, но группы фазы 3 — должны быть отброшены
        self._feed(h, BASKET_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(h)

        parsed = h._build_parsed()
        self.assertEqual(parsed["sport"], "basketball")
        self.assertEqual(parsed["phase_num"], 2)
        # Группы фазы 3 не попали в текущую фазу
        self.assertEqual(parsed["set_markets"], {})

    def test_basket_current_phase_it(self):
        h = OnewinHandler(target_match_id="43741532")
        self._feed(h, BASKET_INFO, "match-info-snapshot")
        info = dict(BASKET_INFO)
        info["status"] = "3-я четверть"
        info["periodsScore"] = [
            {"t1": "9", "t2": "16"}, {"t1": "15", "t2": "22"},
            {"t1": "5", "t2": "3"},
        ]
        self._feed(h, info, "match-info")
        self._feed(h, BASKET_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(h)

        parsed = h._build_parsed()
        set3 = parsed["set_markets"].get("set_3", {})
        self.assertEqual(set3.get("total", {}).get("line"), 34.5)
        self.assertEqual(set3.get("total", {}).get("over"), 1.82)
        self.assertEqual(set3.get("total", {}).get("under"), 1.85)
        # ИТ — по порядку групп: Спартак (153949) = ИТ1,
        # Надежда (153950) = ИТ2 (порядок появления в снапшоте)
        it1 = set3.get("it", {}).get("1", {})
        it2 = set3.get("it", {}).get("2", {})
        self.assertEqual(it1.get("line"), 21.5)
        self.assertEqual(it1.get("over"), 1.72)
        self.assertEqual(it2.get("line"), 12.5)
        self.assertEqual(it2.get("under"), 1.99)

    def test_race_nearest_target(self):
        self._feed(self.h, TT_INFO, "match-info-snapshot")
        self._feed(self.h, TT_ODDS_SNAPSHOT, "match-odds-snapshot")
        self._drain(self.h)

        parsed = self.h._build_parsed()
        # sub = 9:10 → max=10, race-цели 7 (уже достигнута) и 3 —
        # должна остаться цель 7? Нет: 7 и 3 ≤ 10 → обе отброшены.
        set2 = parsed["set_markets"].get("set_2", {})
        # race-группа "3-й сет. Первый до" — фаза 3, не текущая → нет
        self.assertNotIn("race", set2)


class TestOnewinHandlerInit(unittest.TestCase):
    """
        Атрибуты обязаны появляться в __init__, а не по пути кода:
        fast-клиент трогает хендлер до всякого setup_listener.
    """

    def test_attributes_after_ctor(self):
        h = OnewinHandler(target_match_id="43721681")
        for attr in ("_match_info", "_groups", "_odds", "_token",
                     "_emit_enabled", "_last_frame_at", "_it_group_order",
                     "_last_sent_state", "_resolved", "_sport_key",
                     "_page", "_callback", "_token_task"):
            self.assertTrue(hasattr(h, attr), f"нет {attr}")
        self.assertTrue(h._emit_enabled)
        self.assertEqual(h._last_frame_at, 0.0)

    def test_reset_caches_keeps_token(self):
        """JWT живёт в странице — сброс WS-кэшей не должен его трогать."""
        h = OnewinHandler(target_match_id="43721681")
        h._token = "jwt-abc"
        h._match_info = {"matchId": 43721681}
        h._odds = {"1": {"cf": 1.5}}
        h.reset_caches()
        self.assertEqual(h._token, "jwt-abc")
        self.assertEqual(h._match_info, {})
        self.assertEqual(h._odds, {})
        self.assertEqual(h._last_frame_at, 0.0)

    def test_feed_frame_and_snapshot_public_api(self):
        h = OnewinHandler(target_match_id="43721681")
        h._emit_enabled = False        # режим fast-клиента
        h.feed_frame(_wrap_socket_frame(TT_INFO, "match-info-snapshot"))
        h.feed_frame(_wrap_socket_frame(
            TT_ODDS_SNAPSHOT, "match-odds-snapshot"))
        self.assertGreater(h._last_frame_at, 0.0)
        parsed = h.snapshot()
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["set_markets"]["set_2"]["winner"]["2"], 1.2)


class TestOnewinBetPacket(unittest.TestCase):
    def test_place_bet_validates_input(self):
        h = OnewinHandler(target_match_id="43721681")
        h._token = "test-jwt-token"

        # Нет oddId (валидация до проверки страницы)
        res = asyncio.run(h.place_bet(page=None, bet_data={
            "amount": 10, "value": 1.5,
        }))
        self.assertFalse(res["success"])

        # Нет токена (валидация до проверки страницы)
        h2 = OnewinHandler(target_match_id="43721681")
        res = asyncio.run(h2.place_bet(page=None, bet_data={
            "amount": 10, "value": 1.5, "outcome_id": "10:1:1",
            "event_id": "43721681",
        }))
        self.assertFalse(res["success"])
        self.assertIn("token", res["error"].lower())

        # Закрытая страница
        h3 = OnewinHandler(target_match_id="43721681")
        h3._token = "test-jwt-token"
        res = asyncio.run(h3.place_bet(page=None, bet_data={
            "amount": 10, "value": 1.5, "outcome_id": "10:1:1",
            "event_id": "43721681",
        }))
        self.assertFalse(res["success"])
        self.assertIn("закрыта", res["error"].lower())


if __name__ == "__main__":
    unittest.main()
