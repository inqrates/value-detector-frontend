# -*- coding: utf-8 -*-
"""
Тесты на чистые предикаты live-value (ui/after_goal/decisions.py).

Запуск:
    venv\\Scripts\\python.exe -m unittest tests.test_decisions -v

fast_favors / is_outcome_dead определяют, ставим ли мы на исход
или пропускаем — ошибка здесь напрямую стоит денег.
"""
import unittest
from ui.after_goal import decisions as d


class TestFastFavorsWinner(unittest.TestCase):
    def test_p1_leading(self):
        self.assertTrue(d.fast_favors("winner", "1", 0, 5, 3, False))

    def test_p1_not_leading(self):
        self.assertFalse(d.fast_favors("winner", "1", 0, 3, 5, False))

    def test_p2_leading(self):
        self.assertTrue(d.fast_favors("winner", "2", 0, 3, 5, False))

    def test_equal(self):
        self.assertFalse(d.fast_favors("winner", "1", 0, 4, 4, False))


class TestFastFavorsTotal(unittest.TestCase):
    def test_over_already_reached(self):
        self.assertTrue(d.fast_favors("total", "over", 18, 10, 9, False))

    def test_over_tt_reachable(self):
        # table_tennis: (11 + min(5,4)) = 15 >= 14 → True
        self.assertTrue(d.fast_favors("total", "over", 14, 5, 4, False, "table_tennis"))

    def test_over_tt_unreachable(self):
        # (11 + min(2,1)) = 12 < 20 → False
        self.assertFalse(d.fast_favors("total", "over", 20, 2, 1, False, "table_tennis"))

    def test_over_phase_done(self):
        self.assertTrue(d.fast_favors("total", "over", 20, 21, 15, True))
        self.assertFalse(d.fast_favors("total", "over", 40, 21, 15, True))

    def test_under_phase_done(self):
        self.assertTrue(d.fast_favors("total", "under", 40, 15, 10, True))
        self.assertFalse(d.fast_favors("total", "under", 20, 15, 10, True))

    def test_under_not_done_always_false(self):
        self.assertFalse(d.fast_favors("total", "under", 20, 5, 3, False))

    def test_volleyball_set5_threshold(self):
        # set5: thr=15, (15 + min(3,2)) = 17 >= 17 → True
        self.assertTrue(d.fast_favors("total", "over", 17, 3, 2, False, "volleyball", 5))

    def test_unknown_sport_over_not_done(self):
        self.assertFalse(d.fast_favors("total", "over", 10, 3, 2, False, "football"))


class TestFastFavorsIt(unittest.TestCase):
    def test_over(self):
        self.assertTrue(d.fast_favors("it", "1_over", 8, 9, 3, False))
        self.assertFalse(d.fast_favors("it", "1_over", 8, 7, 3, False))

    def test_under_done(self):
        self.assertTrue(d.fast_favors("it", "1_under", 8, 7, 3, True))
        self.assertFalse(d.fast_favors("it", "1_under", 8, 9, 3, True))

    def test_under_not_done(self):
        self.assertFalse(d.fast_favors("it", "1_under", 8, 7, 3, False))

    def test_player2(self):
        self.assertTrue(d.fast_favors("it", "2_over", 5, 3, 6, False))


class TestFastFavorsHandicap(unittest.TestCase):
    def test_done_positive(self):
        # margin = 10-5=5, (5+6)=11 > 0 → True
        self.assertTrue(d.fast_favors("handicap", "1", 6, 10, 5, True))

    def test_done_nonpositive(self):
        # margin = 10-6=4, (4+5)=9 > 0 → True
        self.assertTrue(d.fast_favors("handicap", "1", 5, 10, 6, True))
        # margin = 5-10=-5, (-5+3)=-2 ≤ 0 → False
        self.assertFalse(d.fast_favors("handicap", "1", 3, 5, 10, True))

    def test_not_done_always_false(self):
        self.assertFalse(d.fast_favors("handicap", "1", 0, 10, 5, False))


class TestFastFavorsOddEven(unittest.TestCase):
    def test_odd_done(self):
        self.assertTrue(d.fast_favors("odd", "odd", 0, 11, 8, True))   # 19 odd
        self.assertFalse(d.fast_favors("odd", "odd", 0, 11, 7, True))  # 18 even

    def test_even_done(self):
        self.assertTrue(d.fast_favors("odd", "even", 0, 11, 7, True))  # 18 even

    def test_not_done(self):
        self.assertFalse(d.fast_favors("odd", "odd", 0, 11, 8, False))


class TestFastFavorsPoint(unittest.TestCase):
    def test_point_always_false(self):
        self.assertFalse(d.fast_favors("point", "1", 0, 10, 0, True))


class TestFastFavorsEdge(unittest.TestCase):
    def test_bad_side_it(self):
        # side без '_' → split падает в except → False
        self.assertFalse(d.fast_favors("it", "x", 5, 5, 5, True))

    def test_unknown_market(self):
        self.assertFalse(d.fast_favors("bogus", "1", 5, 5, 5, True))


class TestIsOutcomeDeadIt(unittest.TestCase):
    def test_under_already_met(self):
        self.assertTrue(d.is_outcome_dead("it", "1_under", 8, 9, 3, False))

    def test_over_done_below(self):
        self.assertTrue(d.is_outcome_dead("it", "1_over", 8, 7, 3, True))

    def test_over_not_done(self):
        self.assertFalse(d.is_outcome_dead("it", "1_over", 8, 7, 3, False))

    def test_under_not_met(self):
        self.assertFalse(d.is_outcome_dead("it", "1_under", 8, 7, 3, False))


class TestIsOutcomeDeadTotal(unittest.TestCase):
    def test_under_already_met(self):
        self.assertTrue(d.is_outcome_dead("total", "under", 20, 12, 9, False))

    def test_over_done_below(self):
        self.assertTrue(d.is_outcome_dead("total", "over", 20, 10, 8, True))

    def test_over_not_done(self):
        self.assertFalse(d.is_outcome_dead("total", "over", 20, 10, 8, False))


class TestIsOutcomeDeadHandicap(unittest.TestCase):
    def test_done_negative(self):
        # margin = 3-10 = -7, (-7 + 3) = -4 ≤ 0 → dead
        self.assertTrue(d.is_outcome_dead("handicap", "1", 3, 3, 10, True))

    def test_done_positive(self):
        # margin = 10-5 = 5, (5 + 6) = 11 > 0 → alive
        self.assertFalse(d.is_outcome_dead("handicap", "1", 6, 10, 5, True))

    def test_not_done(self):
        self.assertFalse(d.is_outcome_dead("handicap", "1", 6, 10, 5, False))


class TestIsOutcomeDeadWinner(unittest.TestCase):
    def test_done_wrong_side(self):
        self.assertTrue(d.is_outcome_dead("winner", "1", 0, 3, 11, True))

    def test_done_right_side(self):
        self.assertFalse(d.is_outcome_dead("winner", "2", 0, 3, 11, True))

    def test_not_done(self):
        self.assertFalse(d.is_outcome_dead("winner", "1", 0, 3, 11, False))


class TestIsOutcomeDeadEdge(unittest.TestCase):
    def test_bad_side(self):
        self.assertFalse(d.is_outcome_dead("it", "x", 5, 5, 5, True))

    def test_unknown_market(self):
        self.assertFalse(d.is_outcome_dead("bogus", "1", 5, 5, 5, True))


class TestCollectConfirmedBets(unittest.TestCase):
    def _mk(self, line=20.5, over=1.9, under=1.9, it1=11.5, it2=9.5,
            win1=2.1, win2=1.7, h1=-3.5, h2=3.5):
        return {
            'total': {'line': line, 'over': over, 'under': under},
            'it': {'1': {'line': it1, 'over': 2.0, 'under': 2.0},
                   '2': {'line': it2, 'over': 2.0, 'under': 2.0}},
            'winner': {'1': win1, '2': win2},
            'handicap': {'1': {'line': h1, 'odd': win1},
                         '2': {'line': h2, 'odd': win2}},
        }

    def test_phase_running_over_confirmed(self):
        # партия идёт (10:9 < 11+2), total 19 > 18.5 → over подтверждён
        markets = self._mk(line=18.5)
        res = d.collect_confirmed_bets(
            "table_tennis", {}, markets, 1, [10, 9], [0, 0], [0, 0])
        sides = [(m, s) for m, s, *_ in res]
        self.assertIn(("total", "over"), sides)

    def test_tt_done_winner_confirmed(self):
        # 11:9 — партия НТ завершена, winner 1 подтверждён
        res = d.collect_confirmed_bets(
            "table_tennis", {}, self._mk(), 1, [11, 9], [1, 0], [0, 1])
        sides = [(m, s) for m, s, *_ in res]
        self.assertIn(("winner", "1"), sides)

    def test_tt_running_no_winner(self):
        # 10:9 — партия не завершена, winner НЕ подтверждён
        res = d.collect_confirmed_bets(
            "table_tennis", {}, self._mk(), 1, [10, 9], [1, 0], [0, 1])
        sides = [(m, s) for m, s, *_ in res]
        self.assertNotIn(("winner", "1"), sides)

    def test_empty_markets(self):
        res = d.collect_confirmed_bets(
            "table_tennis", {}, {}, 1, [11, 9], [1, 0], [0, 1])
        # winner из пустого markets → odd 0 → не подтверждён
        self.assertEqual(res, [])

    def test_it_over_confirmed(self):
        markets = self._mk(it1=8.5)
        res = d.collect_confirmed_bets(
            "table_tennis", {}, markets, 1, [10, 5], [0, 0], [0, 0])
        sides = [(m, s) for m, s, *_ in res]
        self.assertIn(("it", "1_over"), sides)


class TestCollectPrevPhaseBets(unittest.TestCase):
    def _sm(self, prev_num=1, line=18.5):
        return {f"set_{prev_num}": {
            'total': {'line': line, 'over': 1.9, 'under': 1.9},
            'it': {'1': {'line': 10.5, 'over': 2.0, 'under': 2.0}},
            'winner': {'1': 2.0, '2': 1.8},
            'handicap': {'1': {'line': -3.5, 'odd': 2.0}},
        }}

    def test_empty_phase_name(self):
        self.assertEqual(d.collect_prev_phase_bets("table_tennis", {}, {}, [11, 9], ""), [])

    def test_no_number(self):
        self.assertEqual(d.collect_prev_phase_bets("table_tennis", {}, {}, [11, 9], "партия"), [])

    def test_zero_prev(self):
        self.assertEqual(d.collect_prev_phase_bets("table_tennis", {}, self._sm(), [0, 0], "1-я"), [])

    def test_missing_set(self):
        self.assertEqual(d.collect_prev_phase_bets("table_tennis", {}, {}, [11, 9], "1-я партия"), [])

    def test_prev_total_over(self):
        res = d.collect_prev_phase_bets(
            "table_tennis", {}, self._sm(line=18.5), [11, 9], "1-я партия")
        sides = [(m, s) for m, s, *_ in res]
        self.assertIn(("total", "over"), sides)  # 20 > 18.5

    def test_prev_winner(self):
        res = d.collect_prev_phase_bets(
            "table_tennis", {}, self._sm(), [11, 9], "1-я партия")
        sides = [(m, s) for m, s, *_ in res]
        self.assertIn(("winner", "1"), sides)


class TestFilterConfirmedByStrategy(unittest.TestCase):
    def _confirmed(self):
        return [
            ('total', 'over', 1.9, 'r', 1),
            ('winner', '1', 2.1, 'r', 1),
            ('it', '1_under', 1.8, 'r', 2),
        ]

    def test_auto_mode_passthrough(self):
        res = d.filter_confirmed_by_strategy(self._confirmed(), {}, {})
        self.assertEqual(len(res), 3)

    def test_manual_empty_markets_passthrough(self):
        res = d.filter_confirmed_by_strategy(
            self._confirmed(), {'market_mode': 'manual', 'manual_markets': []}, {})
        self.assertEqual(len(res), 3)

    def test_manual_filter(self):
        st = {'market_mode': 'manual', 'manual_markets': ['total_over']}
        res = d.filter_confirmed_by_strategy(self._confirmed(), st, {})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0][0:2], ('total', 'over'))

    def test_manual_all_filtered_out(self):
        st = {'market_mode': 'manual', 'manual_markets': ['point']}
        res = d.filter_confirmed_by_strategy(self._confirmed(), st, {})
        self.assertEqual(res, [])


if __name__ == "__main__":
    unittest.main()