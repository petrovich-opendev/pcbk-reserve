"""Тело запроса к OpenRouter — заново из белого списка — и метка хода для предела шагов.

От клиента не пересылается ничего сверх белого списка: платные и опасные поля (`models`, `route`,
`plugins`, `preset`, `service_tier`, `transforms`, свой `provider`, серверные инструменты) пропадают при
сборке, а вложения (картинки, файлы, звук) заменяются постоянной частью ATTACHMENT_TEXT — ни одного их
байта наверх не уходит. Свои поля — модель, точка, усилие, `max_price`, `user`, `session_id` — ставит
прокси. Тексты `BodyRefused` идут клиенту после «Запрос к модели отклонён: », поэтому они постоянные:
без чисел (OpenCode ищет в тексте ошибки 429|500|502…) и без значений из запроса.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pcbk_core.llm.models import PROVIDER_FIXED, ModelPolicy
from pcbk_core.llm.turns import COMPACTION_CONTINUE

MAX_TOKENS_CAP = 16000
MAX_MESSAGES = 4000
# id сессии OpenCode в X-Session-Id / x-parent-session-id
SESSION_RE = re.compile(r"ses_[0-9A-Za-z]{26}")
# текст сообщения сжатия (OpenCode 1.18.33, session/message-v2.ts:235)
SUMMARY_PROMPT = "What did we do so far?"
# первая часть сообщения `user`, которым OpenCode передаёт вложения из ответа инструмента
# (OpenCode 1.18.33, session/message-v2.ts:46, :382–399): это шаг хода, а не вопрос человека
SYNTHETIC_ATTACHMENT_PROMPT = "Attached media from tool result:"
# чем заменяется часть, не являющаяся текстом (доводка ревью, раунд 2): сессия не застревает на отказе
ATTACHMENT_TEXT = "[вложение не передаётся на учебном стенде]"

_NOT_OBJECT = "тело запроса — не объект JSON"
_NO_MESSAGES = "в запросе нет списка сообщений"
_TOO_MANY = "в разговоре слишком много сообщений — начните новую сессию"
_BAD_MESSAGE = "сообщение разговора — не объект JSON"
_BAD_ROLE = "роль сообщения не поддерживается: нужны system, user, assistant или tool"
_BAD_CONTENT = "содержимое сообщения — не текст"
_BAD_TOOL_CALL = "вызов инструмента в истории разговора собран неверно"
_BAD_TOOL_RESULT = "у ответа инструмента нет id вызова"
_NOT_ENCODABLE = "в запросе есть значения, которые нельзя передать модели (NaN или неверные символы)"

_PLAIN_FORMATS = ("text", "json_object")
# начало частей-напоминаний OpenCode (PROMPT_PLAN, BUILD_SWITCH, PLAN_MODE): в хеш хода не идут
_REMINDER = "<system-reminder>"


class BodyRefused(ValueError):
    """Тело не собрать; текст по-русски, постоянный — идёт в `LLM_MESSAGES["body"]`."""


def session_of(header: str | None) -> str | None:
    """id сессии OpenCode из заголовка: ровно `ses_` и 26 знаков, иначе `None`."""
    if isinstance(header, str) and SESSION_RE.fullmatch(header):
        return header
    return None


def _is_int(value: object) -> bool:
    return type(value) is int              # bool — подкласс int, но не число токенов


def _is_number_in(value: object, lo: float, hi: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi  # NaN — нет


def _text_part(part: object) -> dict:
    """Часть `text` заново; любая другая (`image_url`, `file`, `input_audio`, кривая) — ATTACHMENT_TEXT."""
    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
        return {"type": "text", "text": part["text"]}
    return {"type": "text", "text": ATTACHMENT_TEXT}


def _text_content(content: object) -> str | list[dict]:
    """Строка или список частей: текст остаётся, вложения заменяются; `cache_control` частей пропадает."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_text_part(part) for part in content]
    raise BodyRefused(_BAD_CONTENT)


def _tool_call(call: object) -> dict:
    if isinstance(call, dict) and call.get("type") == "function" and isinstance(call.get("id"), str):
        fn = call.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str) and isinstance(fn.get("arguments"), str):
            return {"id": call["id"], "type": "function",
                    "function": {"name": fn["name"], "arguments": fn["arguments"]}}
    raise BodyRefused(_BAD_TOOL_CALL)


def _assistant(msg: dict) -> dict:
    out: dict = {"role": "assistant"}
    if "content" in msg:
        out["content"] = None if msg["content"] is None else _text_content(msg["content"])
    calls = msg.get("tool_calls")
    if calls is not None:
        if not isinstance(calls, list):
            raise BodyRefused(_BAD_TOOL_CALL)
        if calls:
            out["tool_calls"] = [_tool_call(call) for call in calls]
    for key in ("reasoning", "reasoning_content"):
        if isinstance(msg.get(key), str):
            out[key] = msg[key]
    if isinstance(msg.get("reasoning_details"), list):          # подписи рассуждений Gemini и Claude — как есть
        out["reasoning_details"] = msg["reasoning_details"]
    return out


def _message(msg: object) -> dict:
    """Сообщение заново по роли; `name`, `cache_control` и прочие поля пропадают."""
    if not isinstance(msg, dict):
        raise BodyRefused(_BAD_MESSAGE)
    role = msg.get("role")
    if role in ("system", "user"):
        return {"role": role, "content": _text_content(msg.get("content"))}
    if role == "assistant":
        return _assistant(msg)
    if role == "tool":
        if not isinstance(msg.get("tool_call_id"), str):
            raise BodyRefused(_BAD_TOOL_RESULT)
        return {"role": "tool", "tool_call_id": msg["tool_call_id"], "content": _text_content(msg.get("content"))}
    raise BodyRefused(_BAD_ROLE)


def _messages(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise BodyRefused(_NO_MESSAGES)
    if len(raw) > MAX_MESSAGES:
        raise BodyRefused(_TOO_MANY)
    return [_message(msg) for msg in raw]


def _tools(raw: object) -> list[dict]:
    """Только `function`, ровно имя, описание и параметры; серверные и кривые инструменты пропадают."""
    if not isinstance(raw, list):
        return []
    tools = []
    for tool in raw:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            continue
        rebuilt: dict = {"name": fn["name"]}
        if isinstance(fn.get("description"), str):
            rebuilt["description"] = fn["description"]
        if isinstance(fn.get("parameters"), dict):
            rebuilt["parameters"] = fn["parameters"]
        tools.append({"type": "function", "function": rebuilt})
    return tools


def _tool_choice(raw: object) -> str | dict | None:
    if isinstance(raw, str) and raw in ("auto", "none", "required"):
        return raw
    if isinstance(raw, dict) and raw.get("type") == "function":
        fn = raw.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            return {"type": "function", "function": {"name": fn["name"]}}
    return None


def _max_tokens(raw: dict, cap: int) -> int:
    value = raw.get("max_tokens") or raw.get("max_completion_tokens")
    return min(value, cap) if _is_int(value) and value > 0 else cap


def _stop(raw: object) -> str | list[str] | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and 1 <= len(raw) <= 4 and all(isinstance(s, str) for s in raw):
        return list(raw)
    return None


def _response_format(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind in _PLAIN_FORMATS:
        return {"type": kind}
    if kind == "json_schema" and isinstance(raw.get("json_schema"), dict):
        return {"type": "json_schema", "json_schema": raw["json_schema"]}
    return None


def _optional(raw: dict) -> dict:
    """stop, seed, response_format, temperature, top_p — только годные значения."""
    out: dict = {}
    if (stop := _stop(raw.get("stop"))) is not None:
        out["stop"] = stop
    if _is_int(raw.get("seed")):
        out["seed"] = raw["seed"]
    if (fmt := _response_format(raw.get("response_format"))) is not None:
        out["response_format"] = fmt
    if _is_number_in(raw.get("temperature"), 0, 2):
        out["temperature"] = raw["temperature"]
    if _is_number_in(raw.get("top_p"), 0, 1):
        out["top_p"] = raw["top_p"]
    return out


def rebuild_body(raw: object, policy: ModelPolicy, *, caller: str, session: str | None,
                 max_tokens_cap: int = MAX_TOKENS_CAP) -> dict:
    """Новое тело для OpenRouter: только поля белого списка и свои поля прокси.

    Не собрать (не объект, плохие сообщения, значение не в JSON/UTF-8) — `BodyRefused`; вложения не
    отказ — они заменяются ATTACHMENT_TEXT.
    """
    if not isinstance(raw, dict):
        raise BodyRefused(_NOT_OBJECT)
    body: dict = {"model": policy.model, "messages": _messages(raw.get("messages"))}
    if tools := _tools(raw.get("tools")):
        body["tools"] = tools
        if (choice := _tool_choice(raw.get("tool_choice"))) is not None:   # без инструментов выбирать нечего
            body["tool_choice"] = choice
    if isinstance(raw.get("stream"), bool):
        body["stream"] = raw["stream"]
        if raw["stream"]:
            body["stream_options"] = {"include_usage": True}
    body["max_tokens"] = _max_tokens(raw, max_tokens_cap)
    body.update(_optional(raw))
    body["reasoning"] = {"effort": policy.effort}
    body["provider"] = {"only": [policy.provider_only], **PROVIDER_FIXED, "zdr": policy.zdr,
                        "max_price": policy.max_price(0)}
    body["user"] = f"pcbk-{caller}"
    body["session_id"] = f"{caller}:{session or 'none'}"
    if policy.auto_cache:
        body["cache_control"] = {"type": "ephemeral"}
    _set_max_price(body, policy)
    return body


def body_bytes(body: dict) -> int:
    """Длина тела в UTF-8, как его отправит прокси, — вход верхней оценки (Д4а-R13)."""
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _checked_bytes(body: dict) -> int:
    """`body_bytes`, но NaN и одиночные суррогаты (их пропускает json.loads) — `BodyRefused`: такое тело
    не отправить и не оценить."""
    try:
        return len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except ValueError:                                  # UnicodeEncodeError — тоже ValueError
        raise BodyRefused(_NOT_ENCODABLE) from None


def _set_max_price(body: dict, policy: ModelPolicy) -> None:
    """`provider.max_price` — по той же ступени, что оценка собранного тела:
    `policy.max_price(policy.input_tokens(body_bytes(body)))`.

    Цена сама меняет длину тела, поэтому после смены ступени она сверяется ещё раз. Не сошлось (цена
    длинного входа в JSON короче базовой, тело на границе) — остаётся базовая: оценка тогда по ступени
    длинного входа, не ниже цены точки, которую пустит OpenRouter.
    """
    provider = body["provider"]
    base = provider["max_price"]
    tiered = policy.max_price(policy.input_tokens(_checked_bytes(body)))
    if tiered != base:
        provider["max_price"] = tiered
        if policy.max_price(policy.input_tokens(body_bytes(body))) != tiered:
            provider["max_price"] = base


@dataclass(frozen=True)
class TurnMark:
    kind: Literal["user", "continue", "other"]
    text_hash: str | None
    n: int


def _texts(msg: dict) -> list[str]:
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [part["text"] for part in content
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]
    return []


def _own_text(texts: list[str]) -> str:
    """Текст человека: части через `\\n`, без частей `<system-reminder>` — PROMPT_PLAN / BUILD_SWITCH
    OpenCode дописывает к последнему `user` на каждом шаге и не хранит (session/reminders.ts)."""
    return "\n".join(text for text in texts if not text.lstrip().startswith(_REMINDER))


def _text_hash(texts: list[str]) -> str:
    return hashlib.sha256(_own_text(texts).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _is_user(msg: object) -> bool:
    return isinstance(msg, dict) and msg.get("role") == "user"


def _only_media(texts: list[str]) -> bool:
    """Не вопрос человека, а шаг хода: сообщение OpenCode с вложениями из ответа инструмента (первая часть —
    SYNTHETIC_ATTACHMENT_PROMPT) или сообщение, где кроме заглушек вложений и `<system-reminder>` ничего нет
    (решение контроллера, раунд 2): иначе инструмент с вложением на каждом шаге обнулял бы счётчик хода."""
    if texts and texts[0] == SYNTHETIC_ATTACHMENT_PROMPT:
        return True
    return all(text.strip() in ("", ATTACHMENT_TEXT) for text in texts if not text.lstrip().startswith(_REMINDER))


def _is_continue(texts: list[str]) -> bool:
    """Продолжение после сжатия (session/compaction.ts:529–531), в том числе с префиксом о вложениях
    и с соседними частями PROMPT_PLAN / BUILD_SWITCH."""
    return any(text.rstrip().endswith(COMPACTION_CONTINUE) for text in texts)


def turn_mark(body: dict) -> TurnMark:
    """Метка хода по собранному телу (Global Constraints, «Ход и предел шагов»; доводка ревью, I1, раунд 2).

    - последнее сообщение не `user`, или `user` с одними вложениями (сообщение OpenCode с вложениями из
      ответа инструмента; только заглушки вложений, напоминания или пусто) — `other`;
    - последнее `user` — продолжение после сжатия: `continue` с хешем «настоящего» `user` (не
      продолжение, не ровно SUMMARY_PROMPT, не одни вложения), если он стоит **прямо перед**
      продолжением, иначе `None`.
      После сжатия модели уходит `[сжатие, сводка, …хвост…, продолжение]` (session/message-v2.ts:582):
      на первом шаге нового вопроса хвост кончается этим вопросом, посреди хода — ответом модели или
      инструмента;
    - иначе `user` с хешем его текста.

    Хеш — sha256 частей `text` через `\\n` без частей `<system-reminder>`, первые 16 знаков.
    """
    messages = body.get("messages")
    messages = messages if isinstance(messages, list) else []
    n = len(messages)
    if not messages or not _is_user(messages[-1]):
        return TurnMark("other", None, n)
    texts = _texts(messages[-1])
    if _only_media(texts):
        return TurnMark("other", None, n)
    if not _is_continue(texts):
        return TurnMark("user", _text_hash(texts), n)
    before = messages[-2] if n >= 2 else None
    if _is_user(before):
        earlier = _texts(before)
        if not (_is_continue(earlier) or _only_media(earlier) or _own_text(earlier) == SUMMARY_PROMPT):
            return TurnMark("continue", _text_hash(earlier), n)
    return TurnMark("continue", None, n)
