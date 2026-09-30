"""Белый список вызовов адаптера OpenCode (Д5-R32…R35, задача Б2): правила, сверка пути, сборка тел заново.

Чистые функции без сети, базы и Starlette: их зовёт прокси (задача Б3). Путь — сырой путь после
/pcbk/api/oc (scope["raw_path"]). Сначала он целиком сверяется с RAW_PATH: %2F, «..», «//», хвостовой «/»,
строка запроса и не-ASCII до правил не доходят (reason=bad_path). Затем — точный метод и шаблон правила
с id вида ses_/per_/que_ и 26 знаками [0-9A-Za-z] (OpenCode 1.18.33, id/id.ts). Нет правила с этой парой —
отказ (reason=not_whitelisted): так закрыты «близнецы» разрешённых путей, например POST /session/{SES}/message
— синхронный prompt с полным телом. Путь к месту собирается из шаблона правила и проверенных id.

Строка запроса — только ключи правила со значениями true/false, по одному разу, в исходном порядке
(directory, workspace, auth_token и прочие выбрасываются молча). Тело к месту собирает сборщик правила из
разрешённых полей; как есть не пересылается ничего. Правила без сборщика тела к месту не шлют.

Отказы — Refused с постоянным текстом без чисел и без ввода клиента (решение Д4а; Ш12): ни часть тела,
ни путь, ни строка запроса не попадают ни в message, ни в reason. Имя агента проходит, только если оно
целиком совпало с AGENT_NAME; сверку со списком агентов места делает прокси (Д5-R33), APPROVABLE для «once»
— тоже прокси (Д5-R34). time.archived = null шлюз пропускает как есть (Ш13): место 1.18.33
(ArchivedTimestamp = Schema.Finite) ответит 400 само — OpenCode шлюз не чинит.

Строки к месту — без одиночных суррогатов: httpx кодирует тело json= в UTF-8, и такой текст дал бы
UnicodeEncodeError в прокси вместо ответа вида OpenCode.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ID_RE = {p: re.compile(rf"{p}_[0-9A-Za-z]{{26}}") for p in ("ses", "msg", "per", "que")}
RAW_PATH = re.compile(r"(/[A-Za-z0-9_]+)+")
METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS")

MAX_PARTS = 20
MAX_TEXT = 20000
MAX_TITLE = 200
MAX_ANSWER_LISTS = 10
MAX_ANSWERS = 20
MAX_ANSWER_CHARS = 2000
APPROVABLE = frozenset({"doom_loop"})         # разрешения, которые студент одобряет сам «once» (Д5-R34)

AGENT_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")

# Д5-R35: 17 типов research/07 §3 и три служебных типа потока
ALLOWED_EVENTS: frozenset[str] = frozenset({
    "session.created", "session.updated", "session.deleted", "session.status", "session.idle",
    "session.compacted", "session.error", "message.updated", "message.removed", "message.part.updated",
    "message.part.delta", "message.part.removed", "permission.asked", "permission.replied", "question.asked",
    "question.replied", "question.rejected", "server.connected", "server.heartbeat", "server.instance.disposed",
})

# тексты отказов — постоянные, без чисел и без ввода клиента (Ш12)
RULE_MESSAGES = {
    "not_whitelisted": "Вызов не разрешён",
    "bad_body": "Неверный запрос",
    "parts": "Сообщение пустое или в нём слишком много частей",
    "part_type": "На учебном стенде в сообщении допустим только текст",
    "text": "Текст сообщения пустой, слишком длинный или неверный",
    "agent_name": "Неверное имя агента",
    "title": "Название разговора пустое, слишком длинное или неверное",
    "archived": "Неверная отметка архива",
    "reply": "Разрешение можно дать один раз или отклонить",
    "answers": "Ответ на вопрос неверного вида или слишком большой",
}

_SURROGATE = re.compile(r"[\ud800-\udfff]")


class Refused(Exception):
    """Отказ по правилам: status и name — для ответа вида OpenCode, message — постоянный текст для браузера,
    reason — вид для журнала (not_whitelisted, bad_path, bad_body, bad_part, agent, permission, origin)."""

    def __init__(self, status: int, name: str, message: str, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.name = name
        self.message = message
        self.reason = reason


def _bad(key: str, reason: str = "bad_body") -> Refused:
    return Refused(400, "BadRequest", RULE_MESSAGES[key], reason)


def _forbidden(reason: str) -> Refused:
    return Refused(403, "Forbidden", RULE_MESSAGES["not_whitelisted"], reason)


def _string_ok(value: Any, min_len: int, max_len: int) -> bool:
    """Строка в пределах длины (в знаках) и без одиночных суррогатов."""
    return (isinstance(value, str) and min_len <= len(value) <= max_len
            and _SURROGATE.search(value) is None)


# --- сборщики тел: всё, чего нет в графе «Тело», вырезается ---

def _always_empty(_body: Any) -> dict:
    """POST /session: всегда {} — parentID, permission, workspaceID, agent, model и прочее не доходят."""
    return {}


def build_prompt(body: Any) -> dict:
    """prompt_async: только текстовые части {"type": "text", "text"} и имя агента по шаблону AGENT_NAME."""
    if not isinstance(body, dict):
        raise _bad("bad_body")
    parts = body.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise _bad("parts")
    out = []
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "text":       # file, agent, subtask и прочее
            raise _bad("part_type", "bad_part")
        text = part.get("text")
        if not _string_ok(text, 1, MAX_TEXT):
            raise _bad("text", "bad_part")
        out.append({"type": "text", "text": text})
    result: dict = {"parts": out}
    if "agent" in body:
        agent = body["agent"]
        if not isinstance(agent, str) or AGENT_NAME.fullmatch(agent) is None:
            raise _bad("agent_name")                                        # имя не эхом (Ш12)
        result["agent"] = agent
    return result


def build_update(body: Any) -> dict:
    """PATCH /session/{SES}: title (1–200 знаков) и/или time.archived (целое ≥ 0 или null); пусто — 400."""
    if not isinstance(body, dict):
        raise _bad("bad_body")
    out: dict = {}
    if "title" in body:
        if not _string_ok(body["title"], 1, MAX_TITLE):
            raise _bad("title")
        out["title"] = body["title"]
    if "time" in body:
        time = body["time"]
        if not isinstance(time, dict):
            raise _bad("archived")
        if "archived" in time:
            archived = time["archived"]
            # bool — подкласс int в Python, он не отметка времени; null — как есть (Ш13)
            if archived is not None and not (type(archived) is int and archived >= 0):
                raise _bad("archived")
            out["time"] = {"archived": archived}
    if not out:
        raise _bad("bad_body")
    return out


def build_perm_reply(body: Any) -> dict:
    """POST /permission/{PER}/reply: только {"reply": "once" | "reject"}; always и прочее — 400, message вырезан."""
    if not isinstance(body, dict):
        raise _bad("reply")
    reply = body.get("reply")
    if not isinstance(reply, str) or reply not in ("once", "reject"):
        raise _bad("reply")
    return {"reply": reply}


def build_answers(body: Any) -> dict:
    """POST /question/{QUE}/reply: {"answers": [[str, …], …]} — до 10 списков по 20 строк по 2 000 знаков."""
    if not isinstance(body, dict):
        raise _bad("answers")
    answers = body.get("answers")
    if not isinstance(answers, list) or len(answers) > MAX_ANSWER_LISTS:
        raise _bad("answers")
    out = []
    for answer in answers:
        if not isinstance(answer, list) or len(answer) > MAX_ANSWERS:
            raise _bad("answers")
        if not all(_string_ok(item, 0, MAX_ANSWER_CHARS) for item in answer):
            raise _bad("answers")
        out.append(list(answer))
    return {"answers": out}


# --- правила ---

@dataclass(frozen=True)
class Rule:
    name: str
    method: str
    pattern: re.Pattern
    query: frozenset[str]
    body: Callable[[Any], dict | None] | None
    kind: Literal["forward", "event", "summarize"]


# подстановки шаблонов пути → префикс id
_PLACEHOLDERS = {"SES": "ses", "PER": "per", "QUE": "que"}
_PLACEHOLDER = re.compile(r"\{(SES|PER|QUE)\}")
# id-примеры для example_path — те же, что в тестах шлюза
EXAMPLE_IDS = {"ses": "ses_" + "A1" * 13, "msg": "msg_" + "B2" * 13, "per": "per_" + "C3" * 13,
               "que": "que_" + "D4" * 13}

# research/07 §5 (строки 1–12 и 14), поток /event (13) и заглушка summarize (15); OpenCode 1.18.33:
# server/routes/instance/httpapi/groups/{session,experimental,permission,question}.ts
_TABLE: tuple[tuple[str, str, str, tuple[str, ...], Callable[[Any], dict | None] | None, str], ...] = (
    # имя               метод    шаблон пути                    запрос                 тело к месту      вид
    ("session_list",     "GET",   "/experimental/session",       ("roots", "archived"), None,             "forward"),
    ("session_create",   "POST",  "/session",                    (),                    _always_empty,    "forward"),
    ("session_status",   "GET",   "/session/status",             (),                    None,             "forward"),
    ("session_get",      "GET",   "/session/{SES}",              (),                    None,             "forward"),
    ("session_update",   "PATCH", "/session/{SES}",              (),                    build_update,     "forward"),
    ("session_messages", "GET",   "/session/{SES}/message",      (),                    None,             "forward"),
    ("prompt_async",     "POST",  "/session/{SES}/prompt_async", (),                    build_prompt,     "forward"),
    ("abort",            "POST",  "/session/{SES}/abort",        (),                    None,             "forward"),
    ("permission_list",  "GET",   "/permission",                 (),                    None,             "forward"),
    ("permission_reply", "POST",  "/permission/{PER}/reply",     (),                    build_perm_reply, "forward"),
    ("question_list",    "GET",   "/question",                   (),                    None,             "forward"),
    ("question_reply",   "POST",  "/question/{QUE}/reply",       (),                    build_answers,    "forward"),
    ("question_reject",  "POST",  "/question/{QUE}/reject",      (),                    None,             "forward"),
    ("event",            "GET",   "/event",                      (),                    None,             "event"),
    ("summarize",        "POST",  "/session/{SES}/summarize",    (),                    None,             "summarize"),
)


def _compile(template: str) -> re.Pattern:
    """Шаблон пути → выражение: литералы экранированы, {SES}/{PER}/{QUE} — именованные группы id."""
    out, pos = [], 0
    for m in _PLACEHOLDER.finditer(template):
        out.append(re.escape(template[pos:m.start()]))
        out.append(f"(?P<{m[1]}>{ID_RE[_PLACEHOLDERS[m[1]]].pattern})")
        pos = m.end()
    out.append(re.escape(template[pos:]))
    return re.compile("".join(out))


_TEMPLATES: dict[str, str] = {name: template for name, _, template, _, _, _ in _TABLE}
RULES: tuple[Rule, ...] = tuple(
    Rule(name=name, method=method, pattern=_compile(template), query=frozenset(query), body=body, kind=kind)
    for name, method, template, query, body, kind in _TABLE
)


def match(method: str, raw_path: str) -> tuple[Rule, str]:
    """Правило для метода и сырого пути и путь к месту, собранный из шаблона и проверенных id; иначе Refused 403."""
    if not isinstance(raw_path, str) or RAW_PATH.fullmatch(raw_path) is None:
        raise _forbidden("bad_path")
    for rule in RULES:
        if rule.method != method:
            continue
        m = rule.pattern.fullmatch(raw_path)
        if m is not None:
            return rule, _TEMPLATES[rule.name].format_map(m.groupdict())
    raise _forbidden("not_whitelisted")


def example_path(rule: Rule) -> str:
    """Путь правила с id-примерами EXAMPLE_IDS (для порождённых тестов)."""
    return _TEMPLATES[rule.name].format_map({k: EXAMPLE_IDS[p] for k, p in _PLACEHOLDERS.items()})


def filter_query(rule: Rule, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Только ключи правила со значениями true/false, каждый ключ — один раз (первое верное), в исходном порядке."""
    seen: set[str] = set()
    out = []
    for key, value in pairs:
        if key in rule.query and key not in seen and value in ("true", "false"):
            seen.add(key)
            out.append((key, value))
    return out
