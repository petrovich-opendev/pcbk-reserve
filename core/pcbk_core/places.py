"""Число рабочих мест стенда — одна константа (Д15б), а не повторённый по всему core диапазон мест вручную.

До Д15б пять файлов держали свой собственный диапазон PLACES, пять — свой регэкс номера места (с префиксом
«student-», без него или с «pcbk-» перед ним), а тексты ошибок и docstring-и — отдельный текстовый диапазон:
расхождение между ними ничего не кидало, а тихо создавало разные «правды» о числе мест (ревью `p11-critic.md`).
Модуль верхнего уровня, только stdlib (его берут main.py, teacher_log.py, agents/findings.py и прочие модули с
договором «только stdlib»): PLACES/PLACE_MAX — число мест, PLACE_NN — двузначный номер места для regex (строится
из PLACE_MAX, а не пишется руками), CALLER_RE — вызывающий student-NN, place_text() — диапазон мест текстом, для
сообщений об ошибках.
"""
from __future__ import annotations

import re

PLACE_MAX = 11                         # мест в стенде (Д15б: было 10, 11-е — учётка-образец student_test)
PLACES = range(1, PLACE_MAX + 1)

assert 10 <= PLACE_MAX <= 19, "PLACE_NN рассчитан на 10..19 мест"
# цифры единиц десятка «1X» (для PLACE_MAX = 11 — "01"): 0[1-9] берёт 01…09, 1[…] — 10 и дальше без руками
_TEEN_DIGITS = "".join(str(d) for d in range(0, PLACE_MAX - 9))
PLACE_NN = rf"0[1-9]|1[{_TEEN_DIGITS}]"
CALLER_RE = re.compile(rf"student-({PLACE_NN})")


def place_text() -> str:
    """«01…11» — диапазон мест в текстах ошибок, --help и docstring-ах."""
    return f"01…{PLACE_MAX:02d}"
