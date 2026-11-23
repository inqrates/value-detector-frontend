# -*- coding: utf-8 -*-
"""
Тесты на чистую логику фаз и рынков (ui/after_goal/sports.py).

Запуск:
    venv\\Scripts\\python.exe -m unittest tests.test_sports -v

Цель: доказать идентичность с оригинальной логикой
из ui/after_goal_engine.py.
"""
import unittest
from ui.after_goal import sports


class TestParsePhaseNum(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(sports.parse_phase_num(""), 0)
        self.assertEqual(sports.parse_phase_num(None), 0)

    def test_numbers(self):
        self.assertEqual(sports.parse_phase_num("1-я партия"), 1)
        self.assertEqual(sports.parse_phase_num("Set 2"), 2)
        self.assertEqual(sports.parse_phase_num("Set 5"), 5)

    def test_no_number(self):
        self.assertEqual(sports.parse_phase_num("Тайм"), 0)


class TestMaxPhaseTotal(unittest.TestCase):
    def test_table_tennis(self):
        self.assertEqual(sports.max_phase_total("table_tennis", 1), 40)
        self.assertEqual(sports.max_phase_total("table_tennis", 5), 40)

    def test_volleyball_set1(self):
        self.assertEqual(sports.max_phase_total("volleyball", 1), 52)

    def test_volleyball_set5(self):
        self.assertEqual(sports.max_phase_total("volleyball", 5), 32)
        self.assertEqual(sports.max_phase_total("volleyball", 3), 52)

    def test_beach_volleyball(self):
        self.assertEqual(sports.max_phase_total("beach_volleyball", 1), 52)

    def test_basketball(self):
        self.assertEqual(sports.max_phase_total("basketball", 1), 80)
        self.assertEqual(sports.max_phase_total("cyber_basketball", 1), 80)

    def test_unknown_sport(self):
        self.assertEqual(sports.max_phase_total("football", 1), 100)


class TestPhaseEnd(unittest.TestCase):
    """Правила окончания партии по видам спорта."""

    def test_tt_win_11_9(self):
        done, winner = sports.phase_end("table_tennis", 11, 9, 1)
        self.assertTrue(done)
        self.assertEqual(winner, '1')

    def test_tt_not_done_10_10(self):
        done, winner = sports.phase_end("table_tennis", 10, 10, 1)
        self.assertFalse(done)
        self.assertIsNone(winner)

    def test_tt_deuce(self):
        done, winner = sports.phase_end("table_tennis", 14, 12, 1)
        self.assertTrue(done)
        self.assertEqual(winner, '1')

    def test_tt_win_2(self):
        done, winner = sports.phase_end("table_tennis", 9, 11, 1)
        self.assertTrue(done)
        self.assertEqual(winner, '2')

    def test_volleyball_set1_25_23(self):
        done, winner = sports.phase_end("volleyball", 25, 23, 1)
        self.assertTrue(done)
        self.assertEqual(winner, '1')

    def test_volleyball_set1_24_24(self):
        done, winner = sports.phase_end("volleyball", 24, 24, 1)
        self.assertFalse(done)

    def test_volleyball_set5_15_13(self):
        done, winner = sports.phase_end("volleyball", 15, 13, 5)
        self.assertTrue(done)
        self.assertEqual(winner, '1')

    def test_volleyball_set5_14_13(self):
        done, winner = sports.phase_end("volleyball", 14, 13, 5)
        self.assertFalse(done)

    def test_beach_volleyball(self):
        done, winner = sports.phase_end("beach_volleyball", 25, 20, 1)
        self.assertTrue(done)
        self.assertEqual(winner, '1')

    def test_unknown_sport(self):
        done, winner = sports.phase_end("basketball", 100, 90, 1)
        self.assertFalse(done)
        self.assertIsNone(winner)


class TestIsSetDone(unittest.TestCase):
    def test_tt_11_9(self):
        self.assertTrue(sports.is_set_done("table_tennis", 11, 9, 1, 1))

    def test_tt_not_done(self):
        self.assertFalse(sports.is_set_done("table_tennis", 10, 10, 1, 1))

    def test_basketball_phase(self):
        self.assertTrue(sports.is_set_done("basketball", 20, 20, 1, 2))
        self.assertFalse(sports.is_set_done("basketball", 20, 20, 1, 1))

    def test_volleyball_set5(self):
        self.assertTrue(sports.is_set_done("volleyball", 15, 13, 5, 5))
        self.assertFalse(sports.is_set_done("volleyball", 14, 13, 5, 5))


class TestResolvePhaseContext(unittest.TestCase):
    def test_fast_from_phase_string(self):
        payload = {'fast_phase': '1-я партия'}
        data = {'phase_num': 2}
        result = sports.resolve_phase_context(
            "table_tennis", payload, data, {}, [5, 4], [3, 2])
        self.assertEqual(result, (1, 2))

    def test_fast_from_score(self):
        payload = {'fast_phase': ''}  # пустая — берём из счёта
        data = {'phase_num': 3}
        result = sports.resolve_phase_context(
            "table_tennis", payload, data, {}, [5, 4], [3, 2])
        # fast = 5+4+1 = 10
        self.assertEqual(result, (10, 3))

    def test_slow_from_set_markets(self):
        payload = {'fast_phase': ''}
        data = {'phase_num': 0}  # 0 — значит "не определён"
        set_markets = {'set_1': {}, 'set_3': {}, 'set_2': {}}
        result = sports.resolve_phase_context(
            "basketball", payload, data, set_markets, [20, 20], [15, 15])
        # basketball НЕ в PHASE_END_BY_SCORE, поэтому fast остаётся 0
        # (сохранённое поведение оригинала), slow=max(set)=3
        self.assertEqual(result, (0, 3))


class TestIsPhaseLineSane(unittest.TestCase):
    def test_winner_always_sane(self):
        self.assertTrue(sports.is_phase_line_sane("table_tennis", "winner", 1.5, 1))

    def test_total_over_max(self):
        self.assertTrue(sports.is_phase_line_sane("table_tennis", "total", 40, 1))
        self.assertFalse(sports.is_phase_line_sane("table_tennis", "total", 41, 1))

    def test_handicap_over_max(self):
        self.assertTrue(sports.is_phase_line_sane("table_tennis", "handicap", 20, 1))
        self.assertFalse(sports.is_phase_line_sane("table_tennis", "handicap", 21, 1))

    def test_it_over_max(self):
        self.assertTrue(sports.is_phase_line_sane("table_tennis", "it", 20, 1))
        self.assertFalse(sports.is_phase_line_sane("table_tennis", "it", 21, 1))

    def test_none_line(self):
        self.assertFalse(sports.is_phase_line_sane("table_tennis", "total", None, 1))

    def test_negative_line(self):
        self.assertTrue(sports.is_phase_line_sane("table_tennis", "total", -30, 1))


class TestMarketCode(unittest.TestCase):
    def test_winner(self):
        self.assertEqual(sports.market_code("winner", "1"), "winner_1")
        self.assertEqual(sports.market_code("winner", "2"), "winner_2")

    def test_total(self):
        self.assertEqual(sports.market_code("total", "over"), "total_over")
        self.assertEqual(sports.market_code("total", "under"), "total_under")

    def test_handicap(self):
        self.assertEqual(sports.market_code("handicap", "1"), "handicap_1")

    def test_it(self):
        self.assertEqual(sports.market_code("it", "1_over"), "it1_over")
        self.assertEqual(sports.market_code("it", "2_under"), "it2_under")

    def test_other(self):
        self.assertEqual(sports.market_code("odd", ""), "odd")
        self.assertEqual(sports.market_code("point", ""), "point")

    def test_unknown(self):
        self.assertIsNone(sports.market_code("unknown", "1"))

    def test_it_no_side(self):
        self.assertIsNone(sports.market_code("it", ""))


class TestIsMarketAllowed(unittest.TestCase):
    def test_winner_in_manual(self):
        self.assertTrue(sports.is_market_allowed("winner", "1", {"winner_1"}))

    def test_winner_not_in_manual(self):
        self.assertFalse(sports.is_market_allowed("winner", "1", set()))

    def test_race_allowed(self):
        # race пройдёт только если base-рынок тоже в manual
        self.assertTrue(sports.is_market_allowed("race_total", "over", {"race", "total_over"}))
        self.assertTrue(sports.is_market_allowed("race_winner", "1", {"race", "winner_1"}))

    def test_race_not_allowed(self):
        # race не пройдёт без 'race' в manual
        self.assertFalse(sports.is_market_allowed("race_total", "over", {"total_over"}))
        # race с невалидным base тоже не пройдёт
        self.assertFalse(sports.is_market_allowed("race_1", "1", {"race"}))


class TestMarketThresholds(unittest.TestCase):
    def test_get_market_threshold_from_strategy(self):
        strategy = {'market_thresholds': {'winner': 3}}
        self.assertEqual(sports.get_market_threshold(strategy, "winner"), 3)

    def test_get_market_threshold_default(self):
        strategy = {'min_score_diff': 2}
        self.assertEqual(sports.get_market_threshold(strategy, "winner"), 2)

    def test_get_min_threshold_auto(self):
        strategy = {'min_score_diff': 2, 'market_thresholds': {}}
        self.assertEqual(sports.get_min_threshold(strategy), 2)

    def test_get_min_threshold_manual(self):
        strategy = {
            'market_mode': 'manual',
            'manual_markets': ['winner_1', 'total_over'],
            'market_thresholds': {'winner': 2, 'total': 3},
        }
        self.assertEqual(sports.get_min_threshold(strategy), 2)


class TestConstants(unittest.TestCase):
    def test_phase_end_by_score(self):
        self.assertIn("table_tennis", sports.PHASE_END_BY_SCORE)
        self.assertIn("volleyball", sports.PHASE_END_BY_SCORE)

    def test_phase_end_by_time(self):
        self.assertIn("basketball", sports.PHASE_END_BY_TIME)
        self.assertIn("cyber_basketball", sports.PHASE_END_BY_TIME)

    def test_market_code_to_base(self):
        self.assertEqual(sports.MARKET_CODE_TO_BASE["winner_1"], "winner")
        self.assertEqual(sports.MARKET_CODE_TO_BASE["it1_over"], "it")


class TestBetSignature(unittest.TestCase):
    """_bet_signature — чистая функция из AfterGoalEngine."""

    @staticmethod
    def _signature(bet_data):
        return (
            str(bet_data.get("event_id") or ""),
            str(bet_data.get("coefficient_id") or bet_data.get("pos") or ""),
            str(bet_data.get("selection_id") or ""),
            str(bet_data.get("line") or ""),
            str(bet_data.get("side") or ""),
            str(bet_data.get("market") or ""),
            str(bet_data.get("set_num") or bet_data.get("set_number") or ""),
        )

    def test_full_bet(self):
        data = {
            "event_id": "12345",
            "coefficient_id": "789",
            "selection_id": "100",
            "line": 3.5,
            "side": "over",
            "market": "total",
            "set_num": 1,
        }
        sig = self._signature(data)
        self.assertEqual(sig[0], "12345")
        self.assertEqual(sig[6], "1")

    def test_fallback_pos(self):
        data = {"pos": "111", "coefficient_id": None}
        sig = self._signature(data)
        self.assertEqual(sig[1], "111")

    def test_fallback_set_number(self):
        data = {"set_number": 2}
        sig = self._signature(data)
        self.assertEqual(sig[6], "2")

    def test_empty(self):
        data = {}
        sig = self._signature(data)
        self.assertEqual(sig, ("", "", "", "", "", "", ""))


if __name__ == "__main__":
    unittest.main()
