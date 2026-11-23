# -*- coding: utf-8 -*-
"""
Сверка новой единой нормализации (core/names.py) со старыми реализациями.

Запуск:
    venv\\Scripts\\python.exe tools_check_names.py

Проверяет, что перенос в core/names.py не изменил поведение:
  * engine:   старое _normalize_key_name  ==  names.normalize_name()
  * fast:     старое _normalize_name      ==  names.normalize_name(без транслита)
  * server:   старый normalizer           ==  names.normalize_name()
"""
import re
import sys
from difflib import SequenceMatcher

from core import names

# ══════════════════════════════════════════════════════════
# Эталон 1: старая реализация движка (ui/after_goal_engine.py)
# ══════════════════════════════════════════════════════════
_ALIASES = {
    "адам": "адам", "лукас": "адам",
    "хецко": "гечко", "гечко": "гечко",
}
_TRANSLIT = names.TRANSLIT


def _old_engine(name: str) -> str:
    if not name:
        return ""
    text = re.sub(r'\(.*?\)', '', name)
    text = re.sub(r'\d+', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = text.lower()
    text = names.transliterate(text)
    words = [w for w in text.split() if len(w) > 1]
    if not words:
        words = text.split()
    words = [_ALIASES.get(w, w) for w in words]
    if not words:
        return ""
    lastname = words[-1]
    for w in words:
        if len(w) > len(lastname):
            lastname = w
    return lastname


# ══════════════════════════════════════════════════════════
# Эталон 2: старая реализация fast_client
# ══════════════════════════════════════════════════════════
def _old_fast(name: str) -> str:
    if not name:
        return ""
    text = re.sub(r"\(.*?\)", "", name)
    text = re.sub(r"\d+", "", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = text.lower()
    words = [w for w in text.split() if len(w) > 1]
    if not words:
        return text.strip()
    return max(words, key=len)


# ══════════════════════════════════════════════════════════
# Эталон 3: старая серверная реализация (core/normalizer.py)
# ══════════════════════════════════════════════════════════
class _OldNormalizer:
    MAX_CACHE = 10_000

    def __init__(self):
        self._cache = {}

    def normalize_name(self, name: str) -> str:
        if not name:
            return ""
        cache_key = name.strip()
        if cache_key in self._cache:
            return self._cache[cache_key]
        text = re.sub(r'\(.*?\)', '', name)
        text = re.sub(r'\d+', '', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        text = text.lower()
        text = names.transliterate(text)
        words = [w for w in text.split() if len(w) > 1]
        if not words:
            words = text.split()
        words = [_ALIASES.get(w, w) for w in words]
        if words:
            lastname = words[-1]
            for w in words:
                if len(w) > len(lastname):
                    lastname = w
        else:
            lastname = ""
        self._cache[cache_key] = lastname
        return lastname


# ══════════════════════════════════════════════════════════
# Набор тестовых имён — реальные варианты из БК
# ══════════════════════════════════════════════════════════
CASES = [
    # кириллица
    "Иван Петров", "Петров Иван", "ПЕТРОВ И.", "Петров И.А.",
    "Сидоров", "Кузнецов Алексей", "Алексей Кузнецов",
    # латиница
    "Ivan Petrov", "Petrov Ivan", "PETROV I.", "Sidorov",
    "Lukas Adam", "Adam Lukas", "Hecko Lukas",
    # смешанное
    "Петров Ivan", "Ivan Петров",
    # со скобками и цифрами
    "Петров (мл.)", "Петров (1964)", "Petrov (1985)",
    "Сидоров (огран. возм-ти)",
    # дефисы и пунктуация
    "Петров-Водкин", "О'Брайен", "Жан-Поль Сидоров",
    # короткие / равной длины
    "Ли Ван", "Ван Ли", "Ан Ли", "Kim Lee",
    # пустые / мусор
    "", "   ", "123", "(мл.)", ".", "-",
    # инициалы
    "И. Петров", "И П", "A B",
    # китайские/корейские
    "Wang Li", "Li Wang", "Ма Лун", "Лун Ма",
    # длинные
    "Александр Александрович Кузнецов",
]

# Пары для проверки players_match / match_players
PAIRS = [
    ("Иван Петров", "Сидоров", "Петров И.", "Сидоров А."),
    ("Ivan Petrov", "Sidorov", "Петров Иван", "Сидоров Алексей"),
    ("Петров", "Сидоров", "Сидоров", "Петров"),
    ("Lukas Adam", "Kim Lee", "Адам Лукас", "Ли Ким"),
    ("Петров", "Сидоров", "Кузнецов", "Орлов"),
]


def main() -> int:
    old_norm = _OldNormalizer()
    fails = []

    # ── engine ──
    for c in CASES:
        old = _old_engine(c)
        new = names.normalize_name(c)
        if old != new:
            fails.append(("engine", c, old, new))

    # ── fast_client ──
    for c in CASES:
        old = _old_fast(c)
        new = names.normalize_name(
            c, transliterate_=False, aliases=False,
            prefer_last_on_tie=False, fallback_to_raw=True
        )
        if old != new:
            fails.append(("fast", c, old, new))

    # ── сервер ──
    for c in CASES:
        old = old_norm.normalize_name(c)
        new = names.normalize_name(c)
        if old != new:
            fails.append(("server", c, old, new))

    # ── similarity (только серверная) ──
    def old_similarity(a, b):
        ln1 = old_norm.normalize_name(a)
        ln2 = old_norm.normalize_name(b)
        if not ln1 or not ln2:
            return 0.0
        if ln1 == ln2:
            return 1.0
        if ln1 in ln2 or ln2 in ln1:
            return 0.95
        score = SequenceMatcher(None, ln1, ln2).ratio()
        if len(ln1) <= 6 and len(ln2) <= 6 and score >= 0.75:
            score = 0.85
        return score

    for a, b in [(x, y) for x in CASES[:20] for y in CASES[:20]]:
        old = round(old_similarity(a, b), 10)
        new = round(names.similarity(a, b), 10)
        if old != new:
            fails.append(("similarity", f"{a!r}/{b!r}", old, new))

    # ── players_match ──
    def old_players_match(p1a, p2a, p1b, p2b):
        na1, na2 = _old_fast(p1a), _old_fast(p2a)
        nb1, nb2 = _old_fast(p1b), _old_fast(p2b)
        if not (na1 and na2 and nb1 and nb2):
            return False

        def close(a, b):
            if not a or not b:
                return False
            if a == b:
                return True
            if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
                return True
            return False

        return (close(na1, nb1) and close(na2, nb2)) or \
               (close(na1, nb2) and close(na2, nb1))

    for p in PAIRS:
        old = old_players_match(*p)
        new = names.players_match(*p)
        if old != new:
            fails.append(("players_match", str(p), old, new))

    # ── match_players ──
    for p in PAIRS:
        old_ok, old_score = names.match_players(*p)
        ln1 = old_norm.normalize_name(p[0])
        ln2 = old_norm.normalize_name(p[1])
        ln3 = old_norm.normalize_name(p[2])
        ln4 = old_norm.normalize_name(p[3])
        if all([ln1, ln2, ln3, ln4]):
            s1 = old_similarity(p[0], p[2])
            s2 = old_similarity(p[1], p[3])
            direct = (s1 + s2) / 2
            cs1 = old_similarity(p[0], p[3])
            cs2 = old_similarity(p[1], p[2])
            cross = (cs1 + cs2) / 2
            best = max(direct, cross)
            old_ok2 = best >= 0.85
            if old_ok2 != old_ok or abs(best - old_score) > 1e-9:
                fails.append(("match_players", str(p), (old_ok2, best),
                              (old_ok, old_score)))

    # ── отчёт ──
    print(f"Проверено имён: {len(CASES)} × 3 реализации")
    print(f"Проверено пар: {len(PAIRS)}")
    if not fails:
        print("\n✅ Поведение ИДЕНТИЧНО старому. Перенос безопасен.")
        return 0

    print(f"\n❌ Расхождений: {len(fails)}\n")
    for kind, case, old, new in fails[:40]:
        print(f"  [{kind}] {case!r}\n      было: {old!r}\n      стало: {new!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
