# test_swap.py
import sys
import os

# чтобы PyQt5 не пытался открыть окно при импорте движка
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

from ui.after_goal_engine import AfterGoalEngine

# ── Тест 1: swap=False, ставим на игрока 2, fast подтверждает ──
# slow: sub1=19 (игрок1), sub2=21 (игрок2)
# fast: sub1=19 (тот же порядок), sub2=21
# Ожидаем: True (игрок2 ведёт)
r1 = AfterGoalEngine._fast_favors('winner', '2', 0, 19, 21, False)
print(f"Тест 1 (swap=False, ставим winner 2): {r1}  → ожидалось True")

# ── Тест 2: swap=True — fast_очки уже ПЕРЕВЁРНУТЫЕ (флип в _choose_live_value_bet) ──
# slow: sub1=19 (игрок1), sub2=21 (игрок2) → игрок 2 ведёт
# fast: sub1=21 (игрок1 в fast), sub2=19 (игрок2 в fast)
# После флипа _fast_sub1=19, _fast_sub2=21 (уже в порядке slow)
# Ставим winner 2 → должны получить True
r2 = AfterGoalEngine._fast_favors('winner', '2', 0, 19, 21, False)
print(f"Тест 2 (после флипа): {r2}  → ожидалось True")

# ── Тест 3: swap=True БЕЗ флипа — это баг ──
# Если бы мы передали сырые fast-очки без флипа: 21, 19
# Ставим winner 2 → False (игрок 2 отстаёт)
r3 = AfterGoalEngine._fast_favors('winner', '2', 0, 21, 19, False)
print(f"Тест 3 (БЕЗ флипа, баг): {r3}  → ожидалось False (это и есть баг)")

# ── Тест 4: winner 1 при swap с флипом ──
# slow: sub1=21 (игрок1), sub2=19 (игрок2) → игрок 1 ведёт
# fast: sub1=19 (игрок1 в fast), sub2=21 (игрок2 в fast)
# После флипа: _fast_sub1=21, _fast_sub2=19 (порядок slow)
# Ставим winner 1 → должны получить True
r4 = AfterGoalEngine._fast_favors('winner', '1', 0, 21, 19, False)
print(f"Тест 4 (swap, ставим winner 1): {r4}  → ожидалось True")

print()
print("Сводка:")
print(f"  Тест 1: {'OK' if r1 is True else 'FAIL'}")
print(f"  Тест 2: {'OK' if r2 is True else 'FAIL'}")
print(f"  Тест 3: {'OK (это баг без флипа)' if r3 is False else 'странно'}")
print(f"  Тест 4: {'OK' if r4 is True else 'FAIL'}")