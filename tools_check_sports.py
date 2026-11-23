# -*- coding: utf-8 -*-
"""
Сверка ui/after_goal/sports.py с оригинальной логикой движка.

Оригинал берётся из HEAD (файл _orig_engine.py), тела методов
извлекаются через AST — НИКАКОГО ручного копирования, исключена
ошибка переписывания. Скрипт проверяет идентичность на
исчерпывающем наборе входов (все виды спорта × граничные значения).

Запуск:
    venv\\Scripts\\python.exe tools_check_sports.py [path/to/orig.py]

Если _orig_engine.py не передан, берётся по умолчанию.
"""
import ast
import sys
import textwrap

from ui.after_goal import sports
from ui.after_goal import decisions

ORIG_PATH = sys.argv[1] if len(sys.argv) > 1 else "_orig_engine.py"

# Методы движка, которые вынесены в sports.py
METHODS = {
    "_phase_end":            sports.phase_end,
    "_is_set_done":          sports.is_set_done,
    "_parse_phase_num":      sports.parse_phase_num,
    "_resolve_phase_context": sports.resolve_phase_context,
    "_max_phase_total":      sports.max_phase_total,
    "_is_phase_line_sane":   sports.is_phase_line_sane,
    "_market_code":          sports.market_code,
    "_is_market_allowed":    sports.is_market_allowed,
    "_get_market_threshold": sports.get_market_threshold,
    "_get_min_threshold":    sports.get_min_threshold,
    # вынесены в decisions.py
    "_fast_favors":          decisions.fast_favors,
    "_is_outcome_dead":      decisions.is_outcome_dead,
    "_collect_confirmed_bets": decisions.collect_confirmed_bets,
    "_collect_prev_phase_bets": decisions.collect_prev_phase_bets,
    "_filter_confirmed_by_strategy": decisions.filter_confirmed_by_strategy,
}


def _extract_method_src(tree, class_name, method_name):
    """Возвращает dedent'нутый исходник метода из класса."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    start = item.lineno
                    if item.decorator_list:
                        start = min(d.lineno for d in item.decorator_list)
                    end = item.end_lineno
                    return start, end
    return None, None


def build_reference(src_lines, tree):
    """
    Собирает класс Ref с оригинальными телами методов
    и исполняет его в изолированном namespace.
    """
    chunks = []
    for mname in METHODS:
        s, e = _extract_method_src(tree, "AfterGoalEngine", mname)
        if s is None:
            raise RuntimeError(f"метод {mname} не найден в оригинале")
        block = "".join(src_lines[s - 1:e])
        chunks.append(textwrap.dedent(block))

    code = "class Ref:\n" + "\n".join(
        textwrap.indent(c, "    ") for c in chunks)

    import logging
    import re as _re
    ns = {
        "re": _re,
        "logging": logging,
        "logger": logging.getLogger("ref"),
        "Optional": object, "List": list, "Tuple": tuple, "Dict": dict,
        "PHASE_END_BY_SCORE": sports.PHASE_END_BY_SCORE,
        "PHASE_END_BY_TIME": sports.PHASE_END_BY_TIME,
        "_MARKET_CODE_TO_BASE": sports.MARKET_CODE_TO_BASE,
    }
    exec(code, ns)
    return ns["Ref"]()


# ══════════════════════════════════════════════════════════
# Исчерпывающие наборы входов
# ══════════════════════════════════════════════════════════
SPORTS = ["table_tennis", "volleyball", "beach_volleyball",
          "basketball", "cyber_basketball", "football", "hockey", ""]
MARKETS = ["winner", "total", "handicap", "it", "odd", "point",
           "race_1", "race_total", "unknown"]
SIDES = ["1", "2", "over", "under", "1_over", "2_under", ""]


def score_cases():
    """Граничные счета для фаз."""
    vals = [0, 1, 9, 10, 11, 12, 13, 14, 15, 16, 20, 23, 24, 25, 26, 27, 30, 40, 52]
    for s in SPORTS:
        for a in vals:
            for b in vals:
                for setn in (1, 2, 4, 5, 6):
                    yield (s, a, b, setn)


def line_cases():
    vals = [None, 0, 1, 3.5, 10, 15, 16, 20, 25, 26, 32, 40, 41, 52, 53, 80, 100, 101, -30, "x"]
    for s in SPORTS:
        for m in MARKETS:
            for ln in vals:
                for setn in (1, 5):
                    yield (s, m, ln, setn)


def strategy_cases():
    base = [
        {},
        {"min_score_diff": 2},
        {"min_score_diff": 5},
        {"market_thresholds": {"winner": 3}},
        {"market_thresholds": {"winner": 3, "total": 4, "handicap": 2, "it": 6}},
        {"min_score_diff": 3, "market_thresholds": {"total": 1}},
        {"market_thresholds": {"winner": "x"}},
        {"market_mode": "manual", "manual_markets": ["winner_1"]},
        {"market_mode": "manual", "manual_markets": ["winner_1", "total_over"]},
        {"market_mode": "manual", "manual_markets": []},
        {"market_mode": "manual", "manual_markets": ["it1_over", "handicap_2"],
         "market_thresholds": {"it": 4, "handicap": 3}},
        {"market_mode": "auto", "min_score_diff": 2,
         "market_thresholds": {"winner": 1, "total": 2}},
        {"market_mode": "manual", "manual_markets": ["bogus"]},
    ]
    return base


def run(ref):
    fails = []

    # phase_end / is_set_done / max_phase_total
    for s, a, b, setn in score_cases():
        if ref._phase_end(s, a, b, setn) != sports.phase_end(s, a, b, setn):
            fails.append(("phase_end", s, a, b, setn,
                          ref._phase_end(s, a, b, setn),
                          sports.phase_end(s, a, b, setn)))
        for fp in (0, 1, 2, 5, 6):
            r1 = ref._is_set_done(s, a, b, setn, fp)
            r2 = sports.is_set_done(s, a, b, setn, fp)
            if r1 != r2:
                fails.append(("is_set_done", s, a, b, setn, fp, r1, r2))
        if ref._max_phase_total(s, setn) != sports.max_phase_total(s, setn):
            fails.append(("max_phase_total", s, setn,
                          ref._max_phase_total(s, setn),
                          sports.max_phase_total(s, setn)))

    # parse_phase_num
    for txt in ["", None, "1-я партия", "Set 2", "Тайм", "5", "abc 12 def",
                "0", "10-я", "Четверть 3", 7]:
        r1 = ref._parse_phase_num(txt)
        r2 = sports.parse_phase_num(txt)
        if r1 != r2:
            fails.append(("parse_phase_num", txt, r1, r2))

    # resolve_phase_context
    for s in SPORTS:
        for fp in ["", "2-я партия", None]:
            for pnum in [0, None, 3]:
                payload = {"fast_phase": fp}
                data = {"phase_num": pnum}
                sm = {"set_1": {}, "set_3": {}, "set_2": {}}
                r1 = ref._resolve_phase_context(s, payload, data, sm, [5, 4], [3, 2])
                r2 = sports.resolve_phase_context(s, payload, data, sm, [5, 4], [3, 2])
                if r1 != r2:
                    fails.append(("resolve_phase", s, fp, pnum, r1, r2))

    # is_phase_line_sane
    for s, m, ln, setn in line_cases():
        r1 = ref._is_phase_line_sane(s, m, ln, setn)
        r2 = sports.is_phase_line_sane(s, m, ln, setn)
        if r1 != r2:
            fails.append(("line_sane", s, m, ln, setn, r1, r2))

    # market_code / is_market_allowed
    for m in MARKETS:
        for side in SIDES:
            r1 = ref._market_code(m, side)
            r2 = sports.market_code(m, side)
            if r1 != r2:
                fails.append(("market_code", m, side, r1, r2))
    manuals = [set(), {"winner_1"}, {"race"}, {"race", "total_over"},
               {"winner_1", "it1_over"}, {"point"}, {"odd"}]
    for m in MARKETS:
        for side in SIDES:
            for man in manuals:
                r1 = ref._is_market_allowed(m, side, man)
                r2 = sports.is_market_allowed(m, side, man)
                if r1 != r2:
                    fails.append(("mkt_allowed", m, side, man, r1, r2))

    # thresholds
    for st in strategy_cases():
        for m in ["winner", "total", "handicap", "it", "bogus"]:
            r1 = ref._get_market_threshold(st, m)
            r2 = sports.get_market_threshold(st, m)
            if r1 != r2:
                fails.append(("mkt_threshold", st, m, r1, r2))
        r1 = ref._get_min_threshold(st)
        r2 = sports.get_min_threshold(st)
        if r1 != r2:
            fails.append(("min_threshold", st, r1, r2))

    # fast_favors / is_outcome_dead (decisions.py)
    sub_vals = [0, 1, 10, 11, 12, 15, 25]
    line_vals = [0, 5.5, 11, 15, 20, 25]
    sides_ff = ["1", "2", "over", "under", "1_over", "1_under", "2_over", "2_under", "odd", "even"]
    mkts_ff = ["winner", "total", "it", "handicap", "odd", "point", "race_total"]
    for m in mkts_ff:
        for side in sides_ff:
            for ln in line_vals:
                for f1 in sub_vals:
                    for f2 in sub_vals:
                        for pd in (False, True):
                            for sp in ("table_tennis", "volleyball", "basketball"):
                                for sn in (1, 5):
                                    r1 = ref._fast_favors(m, side, ln, f1, f2, pd, sp, sn)
                                    r2 = decisions.fast_favors(m, side, ln, f1, f2, pd, sp, sn)
                                    if r1 != r2:
                                        fails.append(("fast_favors", m, side, ln, f1, f2, pd, sp, sn, r1, r2))
                            r1 = ref._is_outcome_dead(m, side, ln, f1, f2, pd)
                            r2 = decisions.is_outcome_dead(m, side, ln, f1, f2, pd)
                            if r1 != r2:
                                fails.append(("outcome_dead", m, side, ln, f1, f2, pd, r1, r2))

    # collect_confirmed_bets / collect_prev_phase_bets / filter_confirmed
    def make_markets(line, over, under, it1, it2, win1, win2, h1, h2):
        return {
            'total': {'line': line, 'over': over, 'under': under},
            'it': {
                '1': {'line': it1, 'over': over + 1, 'under': under + 1},
                '2': {'line': it2, 'over': over + 2, 'under': under + 2},
            },
            'winner': {'1': win1, '2': win2},
            'handicap': {
                '1': {'line': h1, 'odd': win1},
                '2': {'line': h2, 'odd': win2},
            },
        }

    mkts_grid = [
        make_markets(0, 0, 0, 0, 0, 0, 0, 0, 0),
        make_markets(20.5, 1.9, 1.9, 11.5, 9.5, 2.1, 1.7, -3.5, 3.5),
        make_markets(40, 1.5, 2.5, 20, 18, 1.2, 3.0, -10, 10),
        make_markets(15, 0, 0, 0, 0, 0, 0, 0, 0),
        make_markets(-5, 1.8, 1.8, 7.5, 6.5, 1.9, 1.9, -1.5, 1.5),
    ]
    sub_grid = [None, [], [0, 0], [5, 3], [11, 9], [10, 10], [15, 13], [25, 23], [3, 11]]
    payload_grid = [{}, {'fast_phase': '1-я партия'}, {'fast_phase': 'Set 5'}, {'fast_phase': ''}]

    for sp in SPORTS:
        for setn in (1, 2, 5):
            for mk in mkts_grid:
                for fs in sub_grid:
                    for pl in payload_grid:
                        r1 = ref._collect_confirmed_bets(sp, pl, mk, setn, fs, [1, 0], [0, 1])
                        r2 = decisions.collect_confirmed_bets(sp, pl, mk, setn, fs, [1, 0], [0, 1])
                        if r1 != r2:
                            fails.append(("collect_confirmed", sp, setn, fs, pl, r1, r2))

    sm_grid = [
        {},
        {'set_1': make_markets(20.5, 1.9, 1.9, 11.5, 9.5, 2.1, 1.7, -3.5, 3.5),
         'set_2': make_markets(18.5, 1.8, 1.8, 10.5, 8.5, 2.0, 1.8, -2.5, 2.5)},
        {'set_5': make_markets(15, 1.9, 1.9, 8, 7, 2, 2, -1, 1)},
    ]
    ppn_grid = ['', '1-я партия', 'Set 2', 'партия 5', 'abc', '5', 'Четверть 3']
    for sp in SPORTS:
        for sm in sm_grid:
            for fps in sub_grid:
                for ppn in ppn_grid:
                    r1 = ref._collect_prev_phase_bets(sp, {}, sm, fps, ppn)
                    r2 = decisions.collect_prev_phase_bets(sp, {}, sm, fps, ppn)
                    if r1 != r2:
                        fails.append(("collect_prev", sp, ppn, fps, r1, r2))

    confirmed_grid = [
        [],
        [('total', 'over', 1.9, 'r', 1)],
        [('total', 'over', 1.9, 'r', 1), ('winner', '1', 2.1, 'r', 1),
         ('it', '1_under', 1.8, 'r', 2), ('handicap', '2', 1.7, 'r', 1)],
        [('race_total', 'over', 1.9, 'r', 1), ('point', '1', 1.5, 'r', 1)],
    ]
    strat_grid = [
        {}, {'market_mode': 'auto'},
        {'market_mode': 'manual', 'manual_markets': []},
        {'market_mode': 'manual', 'manual_markets': ['total_over']},
        {'market_mode': 'manual', 'manual_markets': ['winner_1', 'it1_under']},
        {'market_mode': 'manual', 'manual_markets': ['race', 'total_over', 'point']},
    ]
    for cf in confirmed_grid:
        for st in strat_grid:
            r1 = ref._filter_confirmed_by_strategy(cf, st, {})
            r2 = decisions.filter_confirmed_by_strategy(cf, st, {})
            if r1 != r2:
                fails.append(("filter_confirmed", cf, st, r1, r2))

    return fails


def main():
    with open(ORIG_PATH, encoding="utf-8-sig") as f:
        src = f.read()
    src_lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    ref = build_reference(src_lines, tree)

    fails = run(ref)

    print(f"Оригинал: {ORIG_PATH} (HEAD)")
    print(f"Эталон собран из {len(METHODS)} методов через AST (без копирования)")
    if not fails:
        print("\n✅ sports.py ПОВЕДЕНИЕННО ИДЕНТИЧЕН оригиналу движка.")
        return 0
    print(f"\n❌ Расхождений: {len(fails)}\n")
    seen = set()
    for f in fails:
        key = f[0]
        if key in seen:
            continue
        seen.add(key)
        print(f"  {f!r}")
    print(f"\n  всего расхождений: {len(fails)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
