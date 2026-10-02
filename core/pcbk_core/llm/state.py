"""Состояние OpenRouter для страницы: что говорят ответы на вызовы и опрос /key, /credits (Д4а-R14).

«Кончились деньги» не защёлкивается: первый удачный вызов снимает любой признак, опрос — только свой
(/key — лимит ключа, /credits — деньги на счёте). Признак учения DRILL_LLM=402 опрос не снимает вовсе: его
снимает только пересоздание core без учения. Состояние живёт в памяти и меняется в цикле событий.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pcbk_core.llm.upstream import UpstreamKind

# тексты страницы — постоянные, не длиннее 80 знаков и с « (учения)» (П20)
UPSTREAM_TEXTS = {"unknown": "OpenRouter ещё не опрашивался", "ok": "OpenRouter отвечает",
                  "errors": "OpenRouter отвечает ошибками подряд", "unreachable": "OpenRouter недоступен",
                  "key_rejected": "OpenRouter отклонил ключ стенда",
                  "money_out": "на счёте OpenRouter кончились деньги",
                  "key_limit": "исчерпан лимит ключа OpenRouter", "region": "OpenRouter отказывает по региону"}
FAIL_STATES = frozenset({"unreachable", "key_rejected", "money_out", "key_limit", "region"})
ERRORS_IN_ROW = 3
DRILL_SUFFIX = " (учения)"
_SIGNS = frozenset({"money_out", "key_limit", "key_rejected", "region"})     # вид отказа — само состояние
_POLL_CLEARS = frozenset({"unknown", "unreachable", "key_rejected"})        # что снимает удачный опрос /key
_MONEY = frozenset({"money_out", "key_limit"})     # неудачный опрос их не подменяет (Д4а-R14)


class UpstreamState:
    """state — ключ UPSTREAM_TEXTS; since — момент последней смены состояния (None — не менялось)."""

    def __init__(self, drill: bool = False) -> None:
        self.drill = drill
        self.state = "unknown"
        self.since: datetime | None = None
        self.errors_in_row = 0

    @property
    def text(self) -> str:
        return UPSTREAM_TEXTS[self.state] + (DRILL_SUFFIX if self.drill else "")

    def _set(self, state: str, now: datetime) -> None:
        if state != self.state:
            self.state, self.since = state, now

    def record(self, kind: "UpstreamKind | None", now: datetime, *, source: Literal["chat", "poll"]) -> None:
        """Итог вызова (chat) или опроса /key (poll): kind None — удача, иначе вид отказа upstream.classify.

        - удача вызова — ok и счётчик ошибок подряд 0 (снимает и money_out, и key_limit);
        - удача опроса — ok, только если было unknown, unreachable или key_rejected;
        - money_out, key_limit, key_rejected, region — это же состояние;
        - server и unreachable вызова — счётчик + 1, на ERRORS_IN_ROW — errors, если не сбой из FAIL_STATES;
        - unreachable опроса — unreachable, но не поверх money_out и key_limit: опрос снимает только свой
          признак, а связь с OpenRouter о деньгах ничего не говорит (Д4а-R14);
        - rate, in_flight, rejected (и server опроса) — без изменений;
        - при учениях опрос признак учения (money_out) не трогает вовсе — его снимает только пересоздание core.
        """
        if source == "poll" and self.drill and self.state == "money_out":
            return
        if kind is None:
            if source == "chat":
                self.errors_in_row = 0
                self._set("ok", now)
            elif self.state in _POLL_CLEARS:
                self._set("ok", now)
            return
        if kind in _SIGNS:
            self._set(kind, now)
        elif kind in ("server", "unreachable") and source == "chat":
            self.errors_in_row += 1
            if self.errors_in_row >= ERRORS_IN_ROW and self.state not in FAIL_STATES:
                self._set("errors", now)
        elif kind == "unreachable" and self.state not in _MONEY:
            self._set("unreachable", now)

    def clear_money(self, now: datetime, kind: Literal["money_out", "key_limit"]) -> None:
        """Опрос снимает только названный признак (/key — key_limit, /credits — money_out); при учениях —
        ничего: признак учения снимает только пересоздание core."""
        if not self.drill and self.state == kind:
            self._set("ok", now)

    def to_json(self) -> dict:
        """Ровно state, text, since; время опроса — одно, верхнее checked_at у health_json."""
        return {"state": self.state, "text": self.text, "since": None if self.since is None else self.since.isoformat()}
