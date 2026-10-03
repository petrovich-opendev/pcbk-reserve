"""Стенограмма разговора студента для кабинета преподавателя (Д10а, Д10-R102): чистые функции над ответами места.

Кабинет читает разговор через само место тремя GET (teacher_api.TEACHER_PLACE_PATHS) и отдаёт вебу не ответ OpenCode
как есть, а свой пересказ — только то, что нужно для чтения:
- sessions_view(raw) — список разговоров места (GET /experimental/session?roots=true&archived=true): только элементы с
  id по ID_RE["ses"] правил шлюза, название до TITLE_MAX знаков, created/updated — числа или null, archived —
  time.archived не null; по updated убыванием, не больше SESSIONS_MAX;
- transcript(session_raw, messages_raw) — разговор (GET /session/{id}) и его сообщения (GET /session/{id}/message):
  сообщения user и assistant, части text (без synthetic) и tool, прочие виды частей пропускаются; ошибка ассистента —
  info.error.data.message или info.error.name. Пределы TEXT_MAX, TOOL_TEXT_MAX, ERROR_MAX, PARTS_MAX, MESSAGES_MAX:
  сработал любой предел или обрезка — clipped.

Ответ места не по договору (не JSON, список разговоров или сообщений — не список, разговор — не объект или без годного
id) — TranscriptError (шлюз отвечает 502 bad_answer). Чужие элементы и чужие виды частей не ломают ответ — пропускаются:
новые виды частей при смене версии OpenCode не покажутся, но и не уронят чтение (README: сверить виды частей).

Тексты отдаются как есть: экранирует веб, он показывает их только текстом (Д10-R106). Одно исключение (доводка р1,
I-1): одиночные суррогаты (json.loads пропускает "\\ud83d" — строку, обрезанную посреди пары эмодзи) в каждой строке
наружу — названии, тексте, вводе и выводе инструмента, ошибке, имени агента — заменяются на U+FFFD: иначе ответ не
закодировался бы в UTF-8, и шлюз отдал бы 500 вместо разговора. Сюда и отсюда — только данные: модуль ничего не пишет в
журнал службы. Только stdlib.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from .rules import ID_RE

TEXT_MAX = 20000                        # знаков текста части
TOOL_TEXT_MAX = 2000                    # знаков ввода и вывода инструмента
ERROR_MAX = 500                         # знаков ошибки ассистента
TITLE_MAX = 200                         # знаков названия разговора
AGENT_MAX = 64                          # знаков имени агента сообщения (длиннее — null)
PARTS_MAX = 200                         # частей на сообщение
MESSAGES_MAX = 2000                     # сообщений разговора
SESSIONS_MAX = 500                      # разговоров в списке места
SAFE_INT = 2 ** 53                      # время — только числа, которые JSON.parse браузера прочтёт точно

ROLES = ("user", "assistant")
TOOL_NAME = re.compile(r"[a-z0-9_]{1,64}")
TOOL_STATUSES = frozenset({"pending", "running", "completed", "error"})
_SES = ID_RE["ses"]
_SURROGATE = re.compile(r"[\ud800-\udfff]")         # как rules._SURROGATE и draft._SURROGATE
REPLACEMENT = "�"


def _clean(text: str) -> str:
    """Одиночные суррогаты — U+FFFD (доводка р1, I-1): строка всегда кодируется в UTF-8; длина не меняется."""
    return _SURROGATE.sub(REPLACEMENT, text)


class TranscriptError(ValueError):
    """Ответ места не по договору: не JSON, не список, разговор не объект или без годного id. Текст — без значений."""


def _load(raw: bytes | bytearray | str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):       # в том числе UnicodeDecodeError и целое длиннее предела разбора
        raise TranscriptError("ответ места — не JSON") from None


def _time(value: object) -> int | float | None:
    """Метка времени OpenCode (мс от эпохи) — конечное число не больше SAFE_INT по модулю, иначе None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if abs(value) <= SAFE_INT else None


def _times(obj: dict) -> dict:
    times = obj.get("time")
    return times if isinstance(times, dict) else {}


def _session_row(obj: object) -> dict | None:
    """Разговор в виде кабинета; не объект или id не по ID_RE["ses"] — None."""
    if not isinstance(obj, dict):
        return None
    sid = obj.get("id")
    if not isinstance(sid, str) or _SES.fullmatch(sid) is None:
        return None
    title = obj.get("title")
    times = _times(obj)
    return {"id": sid, "title": _clean(title[:TITLE_MAX]) if isinstance(title, str) else "",
            "created": _time(times.get("created")), "updated": _time(times.get("updated")),
            "archived": times.get("archived") is not None}


def sessions_view(raw: bytes | bytearray | str) -> list[dict]:
    """Список разговоров места: годные элементы, по updated убыванием (без updated — в конце), не больше SESSIONS_MAX.
    Ответ — не список: TranscriptError."""
    items = _load(raw)
    if not isinstance(items, list):
        raise TranscriptError("список разговоров места — не список")
    rows = [row for row in map(_session_row, items) if row is not None]
    rows.sort(key=lambda row: (row["updated"] is not None, row["updated"] or 0), reverse=True)
    return rows[:SESSIONS_MAX]


class _Clip:
    """Обрезка с отметкой: сработал любой предел — clipped. Одиночные суррогаты — U+FFFD (это не обрезка)."""

    def __init__(self) -> None:
        self.clipped = False

    def text(self, value: str, limit: int) -> str:
        if len(value) > limit:
            self.clipped = True
            value = value[:limit]
        return _clean(value)


def _tool_part(part: dict, clip: _Clip) -> dict:
    tool = part.get("tool")
    state = part.get("state")
    state = state if isinstance(state, dict) else {}
    status = state.get("status")
    text_in: str | None = None
    if "input" in state:
        try:
            text_in = clip.text(json.dumps(state["input"], ensure_ascii=False), TOOL_TEXT_MAX)
        except (ValueError, RecursionError):        # разобранный JSON обратно не собрался — не показываем
            text_in, clip.clipped = None, True
    out = state.get("output")
    if not isinstance(out, str):
        out = state.get("error")
    return {"kind": "tool",
            "tool": tool if isinstance(tool, str) and TOOL_NAME.fullmatch(tool) else "?",
            "status": status if isinstance(status, str) and status in TOOL_STATUSES else "unknown",
            "input": text_in,
            "output": clip.text(out, TOOL_TEXT_MAX) if isinstance(out, str) else None}


def _parts(raw: object, clip: _Clip) -> list[dict]:
    parts: list[dict] = []
    if not isinstance(raw, list):
        return parts
    for part in raw:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            text = part.get("text")
            if part.get("synthetic") is True or not isinstance(text, str):
                continue
            shown = {"kind": "text", "text": clip.text(text, TEXT_MAX)}
        elif kind == "tool":
            shown = _tool_part(part, clip)
        else:                                       # reasoning, step-*, file, snapshot, patch, agent, subtask, …
            continue
        if len(parts) >= PARTS_MAX:
            clip.clipped = True
            break
        parts.append(shown)
    return parts


def _error(info: dict, clip: _Clip) -> str | None:
    error = info.get("error")
    if not isinstance(error, dict):
        return None
    data = error.get("data")
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str):
        message = error.get("name")
    return clip.text(message, ERROR_MAX) if isinstance(message, str) else None


def _message(item: object, clip: _Clip) -> dict | None:
    if not isinstance(item, dict):
        return None
    info = item.get("info")
    if not isinstance(info, dict) or info.get("role") not in ROLES:
        return None
    role = info["role"]
    agent = info.get("agent")
    if role == "assistant" and not isinstance(agent, str):
        agent = info.get("mode")
    return {"role": role, "at": _time(_times(info).get("created")),
            "agent": _clean(agent) if isinstance(agent, str) and 0 < len(agent) <= AGENT_MAX else None,
            "parts": _parts(item.get("parts"), clip),
            "error": _error(info, clip) if role == "assistant" else None}


def transcript(session_raw: bytes | bytearray | str, messages_raw: bytes | bytearray | str) -> dict:
    """{"session", "messages", "clipped"} (Д10-R102). Разговор — не объект или без годного id, сообщения — не список,
    любой ответ — не JSON: TranscriptError. Чужие элементы списка сообщений пропускаются."""
    session = _session_row(_load(session_raw))
    if session is None:
        raise TranscriptError("разговор места — не объект с годным id")
    items = _load(messages_raw)
    if not isinstance(items, list):
        raise TranscriptError("сообщения разговора — не список")
    clip = _Clip()
    messages: list[dict] = []
    for item in items:
        message = _message(item, clip)
        if message is None:
            continue
        if len(messages) >= MESSAGES_MAX:
            clip.clipped = True
            break
        messages.append(message)
    return {"session": session, "messages": messages, "clipped": clip.clipped}
