"""Запись прогона отбора (Д8-R72–R74): ключ, ходы, вызовы инструментов, сохранённый агент, мост, сверка, журнал LLM.

Пишет прогонщик (А5, parse_turn — тоже он), читают check_run (А4), пакеты судьи (Б1) и таблица (Б2). Одна строка
OUT/runs.jsonl — RunRecord.to_json(); всё в записи — JSON-типы (списки, а не кортежи), поэтому
from_json(to_json(r)) == r.

- Turn — один ход: реплика студента (бота или скрипта), тексты ассистента по порядку, вызовы инструментов хода, ошибка
  сообщения (класс ошибки OpenCode, message и код из responseBody — m21; None — без ошибки), начало и конец (секунды
  эпохи, time.time()).
- ToolCall — часть-инструмент OpenCode: tool — имя части как есть (pcbk_tag_now, bash …), input — аргументы, output —
  ответ службы (разобранный JSON-объект, а не разобралось — строка), status — состояние части (completed, error …).
- RunRecord:
  - turns — ходы разговора (у A — разговор с Конструктором; мост — отдельно, bridge);
  - sessions — id сессий прогона (у A вторая — мост);
  - saved — удачные save_agent (status ok, без already) по порядку: аргументы вызова и, если прогонщик их прочёл,
    "flags" (столбец flags строки учёта) и "visible_at"; «сохранённая инструкция» — saved[0]["prompt"];
  - agent_file — текст файла сохранённого агента (None — не сохранён или не прочитан);
  - visible_delay_s — agents.visible_at минус конец хода сохранения (None — не виден);
  - calc — сверка калькулятором по аргументам вызова хода: {"tag_now": item | ответ, "tag_period": item | ответ};
  - llm_rows — строки llm_calls сессий прогона (столбцы журнала прокси); notes — заметки прогонщика (без данных);
  - saw_draft — бот видел черновик постоянного вида (ScriptedStudent.saw_draft).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunKey:
    place: int
    scenario: str
    rep: int

    def to_json(self) -> dict[str, Any]:
        return {"place": self.place, "scenario": self.scenario, "rep": self.rep}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> RunKey:
        return cls(place=int(obj["place"]), scenario=str(obj["scenario"]), rep=int(obj["rep"]))


@dataclass
class ToolCall:
    tool: str
    input: dict[str, Any]
    output: Any
    status: str

    def to_json(self) -> dict[str, Any]:
        return {"tool": self.tool, "input": self.input, "output": self.output, "status": self.status}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> ToolCall:
        return cls(tool=obj["tool"], input=dict(obj.get("input") or {}), output=obj.get("output"),
                   status=obj.get("status", ""))


@dataclass
class Turn:
    student: str
    assistant: list[str]
    tools: list[ToolCall]
    error: str | None
    started: float
    ended: float

    def to_json(self) -> dict[str, Any]:
        return {"student": self.student, "assistant": list(self.assistant), "tools": [t.to_json() for t in self.tools],
                "error": self.error, "started": self.started, "ended": self.ended}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> Turn:
        return cls(student=obj["student"], assistant=list(obj.get("assistant") or []),
                   tools=[ToolCall.from_json(t) for t in obj.get("tools") or []], error=obj.get("error"),
                   started=float(obj.get("started", 0.0)), ended=float(obj.get("ended", 0.0)))


@dataclass
class RunRecord:
    key: RunKey
    model: str
    status: str
    turns: list[Turn]
    sessions: list[str]
    saved: list[dict]
    agent_file: str | None
    visible_delay_s: float | None
    bridge: Turn | None
    calc: dict
    llm_rows: list[dict]
    notes: list[str] = field(default_factory=list)
    saw_draft: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key.to_json(), "model": self.model, "status": self.status,
                "turns": [t.to_json() for t in self.turns], "sessions": list(self.sessions),
                "saved": [dict(s) for s in self.saved], "agent_file": self.agent_file,
                "visible_delay_s": self.visible_delay_s, "bridge": self.bridge.to_json() if self.bridge else None,
                "calc": dict(self.calc), "llm_rows": [dict(r) for r in self.llm_rows], "notes": list(self.notes),
                "saw_draft": self.saw_draft}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> RunRecord:
        bridge = obj.get("bridge")
        delay = obj.get("visible_delay_s")
        return cls(key=RunKey.from_json(obj["key"]), model=obj["model"], status=obj["status"],
                   turns=[Turn.from_json(t) for t in obj.get("turns") or []], sessions=list(obj.get("sessions") or []),
                   saved=[dict(s) for s in obj.get("saved") or []], agent_file=obj.get("agent_file"),
                   visible_delay_s=None if delay is None else float(delay),
                   bridge=Turn.from_json(bridge) if bridge else None, calc=dict(obj.get("calc") or {}),
                   llm_rows=[dict(r) for r in obj.get("llm_rows") or []], notes=list(obj.get("notes") or []),
                   saw_draft=bool(obj.get("saw_draft", False)))
