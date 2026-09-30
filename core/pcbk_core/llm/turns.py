"""Предел обращений к модели на один ход разговора (research/09 §2.4 с поправками по OpenCode 1.18.33).

Ход — ответ на один вопрос человека. Ключ хода — `(вызывающий, x-parent-session-id или X-Session-Id)`,
у хода запомнены метка его начала `(text_hash, n)` и число шагов. Новый ход (шаг 1) — только без
`parent`:
- `user` с меткой, не равной запомненной. Повтор того же запроса (OpenCode повторяет до 5 раз,
  `session/retry.ts:31`) метку не меняет и ход не сбрасывает; тот же вопрос, заданный снова, —
  новый ход: сообщений в теле больше;
- продолжение после сжатия, прямо перед которым стоит «настоящий» вопрос (`text_hash` метки не `None`),
  с меткой `(text_hash, n)`, не равной запомненной, — сжатие на первом шаге нового вопроса
  (`session/prompt.ts:1164–1166`), даже если текст тот же, что у текущего хода («да», «продолжай»):
  тело после сжатия другой длины. Повтор той же метки хода не сбрасывает. Посреди хода перед
  продолжением стоит ответ модели или инструмента — хода оно не начинает, какой бы вопрос ни лежал в
  хвосте (доводка ревью, I1 и раунд 2).

Подагент (`x-parent-session-id`) считается в ход родителя. Остаток: если сжатие убрало новый вопрос из
хвоста, он считается в прошлый ход — OpenCode оставляет в хвосте последние сообщения, это маловероятно.
"""
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # request.py импортирует этот модуль ради COMPACTION_CONTINUE
    from pcbk_core.llm.request import TurnMark

TURN_CALLS = 16
TURN_IDLE_S = 3600.0
# дословно из OpenCode 1.18.33 (session/compaction.ts:531)
COMPACTION_CONTINUE = ("Continue if you have next steps, or stop and ask for clarification if you are unsure "
                       "how to proceed.")


class TurnLimitReached(Exception):
    """Шаг больше предела; `step` — номер шага, который был бы сделан. Текст без чисел."""

    def __init__(self, step: int) -> None:
        super().__init__("шагов за ход больше предела")
        self.step = step


@dataclass
class _Turn:
    start: tuple[str | None, int]       # (text_hash, n) метки начала хода
    steps: int
    touched: float


class TurnLimiter:
    """Счётчик шагов по ходам; ключи, не тронутые `idle_s`, забываются. Вызывается из цикла событий."""

    def __init__(self, limit: int = TURN_CALLS, idle_s: float = TURN_IDLE_S, monotonic=time.monotonic):
        self.limit = limit
        self.idle_s = idle_s
        self._mono = monotonic
        self._turns: dict[tuple[str, str], _Turn] = {}

    def _forget_idle(self, now: float) -> None:
        stale = [key for key, turn in self._turns.items() if now - turn.touched >= self.idle_s]
        for key in stale:
            del self._turns[key]

    def step(self, caller: str, session: str | None, mark: "TurnMark", *, parent: str | None = None) -> int:
        """Номер шага этого запроса в ходе; больше `limit` — `TurnLimitReached`, счётчик не растёт."""
        now = self._mono()
        self._forget_idle(now)
        key = (caller, parent or session or "none")
        turn = self._turns.get(key)
        start = (mark.text_hash, mark.n)
        if turn is None:                                    # первый увиденный запрос по ключу — шаг 1
            self._turns[key] = _Turn(start, 1, now)
            return 1
        turn.touched = now
        if parent is None and self._starts_turn(turn, mark):
            turn.start, turn.steps = start, 1
            return 1
        if turn.steps + 1 > self.limit:
            raise TurnLimitReached(turn.steps + 1)
        turn.steps += 1
        return turn.steps

    @staticmethod
    def _starts_turn(turn: _Turn, mark: "TurnMark") -> bool:
        if mark.kind == "user":
            return (mark.text_hash, mark.n) != turn.start
        if mark.kind == "continue":                         # сжатие на первом шаге нового вопроса
            return mark.text_hash is not None and (mark.text_hash, mark.n) != turn.start
        return False

    def refund(self, caller: str, session: str | None, *, parent: str | None = None) -> None:
        """Шаг − 1, не ниже 0: отказ OpenRouter до начала ответа шага не стоит. Нет хода — ничего."""
        turn = self._turns.get((caller, parent or session or "none"))
        if turn is not None:
            turn.steps = max(0, turn.steps - 1)
            turn.touched = self._mono()
