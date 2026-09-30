"""Таблица случаев шлюза к OpenCode (Д5б, задача Б5) — общая для стенда (test_gateway) и живого прогона
(test_gateway_live): разрешённые пары, «близнецы», опасные ручки и искажённые пути.

Без импорта pcbk_core: живой прогон идёт с машины разработчика через туннель, серверного слоя рядом нет. Сверку
таблицы с кодом шлюза делает test_gateway (test_event_types_match_core, test_cases_match_core_rules).
Клиент — gwclient.GwClient или любой объект с тем же call(method, path, body, headers=).
"""
from __future__ import annotations

from typing import Any

OC = "/pcbk/api/oc"
# id вида OpenCode 1.18.33: префикс и 26 знаков [0-9A-Za-z] (шлюз сверяет форму, не наличие)
SES_FAKE = "ses_" + "Q7" * 13
MSG_FAKE = "msg_" + "R8" * 13
PER_FAKE = "per_" + "S9" * 13
QUE_FAKE = "que_" + "T1" * 13
PRT_FAKE = "prt_" + "U2" * 13
METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS")      # как rules.METHODS (Б2)
REFUSED = "Вызов не разрешён"

# копия rules.ALLOWED_EVENTS (Д5-R35): 17 типов research/07 §3 и три служебных
EVENT_TYPES = frozenset({
    "session.created", "session.updated", "session.deleted", "session.status", "session.idle",
    "session.compacted", "session.error", "message.updated", "message.removed", "message.part.updated",
    "message.part.delta", "message.part.removed", "permission.asked", "permission.replied", "question.asked",
    "question.replied", "question.rejected", "server.connected", "server.heartbeat", "server.instance.disposed",
})

# 15 правил белого списка Б2 (rules._TABLE) — метод и путь с id-заглушками
ALLOWED: list[tuple[str, str]] = [
    ("GET", f"{OC}/experimental/session"),
    ("POST", f"{OC}/session"),
    ("GET", f"{OC}/session/status"),
    ("GET", f"{OC}/session/{SES_FAKE}"),
    ("PATCH", f"{OC}/session/{SES_FAKE}"),
    ("GET", f"{OC}/session/{SES_FAKE}/message"),
    ("POST", f"{OC}/session/{SES_FAKE}/prompt_async"),
    ("POST", f"{OC}/session/{SES_FAKE}/abort"),
    ("GET", f"{OC}/permission"),
    ("POST", f"{OC}/permission/{PER_FAKE}/reply"),
    ("GET", f"{OC}/question"),
    ("POST", f"{OC}/question/{QUE_FAKE}/reply"),
    ("POST", f"{OC}/question/{QUE_FAKE}/reject"),
    ("GET", f"{OC}/event"),
    ("POST", f"{OC}/session/{SES_FAKE}/summarize"),
]


def _twins() -> list[tuple[str, str]]:
    """Прочие методы на путях разрешённых пар, кроме пар, которые сами разрешены (у /session/{SES} — GET и PATCH)."""
    allowed = set(ALLOWED)
    out: list[tuple[str, str]] = []
    for _, path in ALLOWED:
        for method in METHODS:
            pair = (method, path)
            if pair not in allowed and pair not in out:
                out.append(pair)
    return out


TWINS: list[tuple[str, str]] = _twins()
assert not set(TWINS) & set(ALLOWED)

_S = f"{OC}/session/{SES_FAKE}"
# через edge — 403 Forbidden «Вызов не разрешён»: метод, путь (со строкой запроса), тело
_DANGEROUS: list[tuple[str, str, dict | None]] = [
    ("GET", f"{OC}/config", None), ("PATCH", f"{OC}/config", None),
    ("GET", f"{OC}/config/providers", None),                   # Ш14: apiKey из {file:/run/secrets/llm-token}
    ("GET", f"{OC}/global/config", None), ("GET", f"{OC}/global/health", None),
    ("GET", f"{OC}/global/event", None), ("POST", f"{OC}/global/upgrade", None),
    ("GET", f"{OC}/api/fs/read/hostname?location[directory]=/etc", None),    # «[», «]», «/» в строке запроса — до шлюза
    ("GET", f"{OC}/api/session", None),
    ("GET", f"{OC}/pty", None), ("POST", f"{OC}/pty", None),
    ("GET", f"{OC}/file/content?path=/etc/passwd", None), ("GET", f"{OC}/find?pattern=x", None),
    ("GET", f"{OC}/mcp", None), ("POST", f"{OC}/mcp/pcbk/connect", None),
    ("PUT", f"{OC}/auth/openrouter", None), ("GET", f"{OC}/provider", None),
    ("POST", f"{OC}/instance/dispose", None), ("GET", f"{OC}/agent", None),
    ("GET", f"{OC}/experimental/tool?provider=pcbk&model=x", None),
    ("GET", f"{OC}/experimental/resource", None),
    ("POST", f"{OC}/experimental/session/{SES_FAKE}/background", None),
    ("GET", f"{OC}/doc", None),
    ("POST", f"{_S}/message", {"parts": [{"type": "file", "url": "file:///proc/self/environ", "mime": "text/plain"}],
                               "tools": {"bash": True}, "system": "x"}),        # синхронный prompt с полным телом
    ("POST", f"{_S}/permissions/{PER_FAKE}", {"response": "always"}),           # старая ручка разрешений
    ("PATCH", f"{_S}/message/{MSG_FAKE}/part/{PRT_FAKE}", {}),
    ("DELETE", f"{_S}/message/{MSG_FAKE}/part/{PRT_FAKE}", {}),
    *(("POST", f"{_S}/{action}", None) for action in ("shell", "command", "init", "share", "revert", "unrevert", "fork")),
    ("GET", f"{_S}/diff", None), ("GET", f"{_S}/children", None),
    ("GET", f"{OC}/tui/x", None), ("GET", f"{OC}/vcs", None), ("GET", f"{OC}/path", None), ("GET", f"{OC}/lsp", None),
    ("GET", f"{OC}/session/status/", None),                    # хвостовой «/» — шлюз, bad_path
    ("GET", OC, None),                                         # без «/» — точный location edge, тот же ответ
]


def _forbidden() -> list[tuple[str, str, dict | None]]:
    """Опасные ручки и все близнецы без тела; дубль по (метод, путь) снимается — остаётся первый (с телом)."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, dict | None]] = []
    for method, path, body in [*_DANGEROUS, *((m, p, None) for m, p in TWINS)]:
        if (method, path) not in seen:
            seen.add((method, path))
            out.append((method, path, body))
    return out


FORBIDDEN: list[tuple[str, str, dict | None]] = _forbidden()
assert not {(m, p) for m, p, _ in FORBIDDEN} & set(ALLOWED)

# через edge — 400 BadRequest (сырой путь до «?»); напрямую в core из pcbk-front — 403 шлюза (bad_path)
MANGLED: list[str] = [
    f"{OC}/session/../global/health",
    f"{OC}/session%2F..%2Fglobal%2Fhealth",
    f"{OC}//session/status",
    f"{OC}/session/%2e%2e/config",
    f"{OC}/./config",
]

ALLOW_PERM = [{"permission": "*", "pattern": "*", "action": "allow"}]


def check_forbidden(client) -> list[tuple[str, str, int, Any]]:
    """Расхождения FORBIDDEN: код не 403 или не Forbidden «Вызов не разрешён». Отказ по источнику (другой текст) не
    засчитывается. У HEAD тела нет — сверяется код; источник у него подтверждается заголовком Sec-Fetch-Site:
    same-origin (у HEAD клиент Origin не ставит), чтобы 403 был отказом правила, а не источника."""
    out = []
    for method, path, body in FORBIDDEN:
        headers = {"Sec-Fetch-Site": "same-origin"} if method == "HEAD" else None
        code, resp, _ = client.call(method, path, body, headers=headers)
        if method == "HEAD":
            if code != 403:
                out.append((method, path, code, None))
            continue
        name = resp.get("name") if isinstance(resp, dict) else None
        data = resp.get("data") if isinstance(resp, dict) else None
        message = data.get("message") if isinstance(data, dict) else resp
        if (code, name, message) != (403, "Forbidden", REFUSED):
            out.append((method, path, code, message))
    return out


def check_mangled(client) -> list[tuple[str, int, Any]]:
    """Расхождения MANGLED: через edge — не 400 BadRequest."""
    out = []
    for path in MANGLED:
        code, resp, _ = client.call("GET", path)
        name = resp.get("name") if isinstance(resp, dict) else resp
        if (code, name) != (400, "BadRequest"):
            out.append((path, code, name))
    return out


def expected_refusals() -> int:
    """Сколько строк «gw refused» (not_whitelisted или bad_path) даёт один check_forbidden: FORBIDDEN без случаев, на
    которые отвечает сам edge (/pcbk/api/oc без косой черты)."""
    return len(FORBIDDEN) - sum(1 for _, path, _ in FORBIDDEN if path == OC)
