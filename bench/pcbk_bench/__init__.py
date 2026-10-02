"""Стенд отбора модели (Д8): сценарии и карточки, сценарный бот-студент, одно правило чисел, проверки без судьи.

Только stdlib и только машина разработчика: в образы и на сервер пакет не едет (Global Constraints Д8). Тесты —
`(cd bench && uv run --python 3.12 --with pytest pytest -q)`.

Модули: scenarios (12 сценариев Д8-R72), cards (карточки, bench_agent_args), student (ScriptedStudent Д8-R73,
CONSENT_RE — общее правило согласия П8, образец черновика честного Конструктора П9), numbers (разбор чисел и
foreign_numbers), record (RunRecord и его JSON), checks (check_run), secret (Secret).
"""
