"""Белый список вызовов адаптера (Д5-R32…R35, задача Б2): 15 правил, близнецы путей — 403, сырой путь сверяется
до правил, строка запроса и тела собираются заново, отказы — постоянными текстами без ввода клиента (Ш12)."""
import ast
import re
from pathlib import Path

import pytest

from pcbk_core.gateway import rules
from pcbk_core.gateway.rules import (AGENT_NAME, ALLOWED_EVENTS, APPROVABLE, ID_RE, MAX_ANSWER_CHARS, MAX_ANSWER_LISTS,
                                     MAX_ANSWERS, MAX_PARTS, MAX_TEXT, MAX_TITLE, METHODS, RAW_PATH, RULES, Refused,
                                     Rule, build_answers, build_perm_reply, build_prompt, build_update, example_path,
                                     filter_query, match)

SES, MSG, PER, QUE, PRT = ("ses_" + "A1" * 13, "msg_" + "B2" * 13, "per_" + "C3" * 13, "que_" + "D4" * 13,
                           "prt_" + "E5" * 13)


def allowed(method, path):
    return any(r.method == method and r.pattern.fullmatch(path) for r in RULES)


def test_match_table():                                                                 # Review Focus 1
    ok = [("GET", "/experimental/session", "session_list"), ("POST", "/session", "session_create"),
          ("GET", "/session/status", "session_status"), ("GET", f"/session/{SES}", "session_get"),
          ("PATCH", f"/session/{SES}", "session_update"), ("GET", f"/session/{SES}/message", "session_messages"),
          ("POST", f"/session/{SES}/prompt_async", "prompt_async"), ("POST", f"/session/{SES}/abort", "abort"),
          ("GET", "/permission", "permission_list"), ("POST", f"/permission/{PER}/reply", "permission_reply"),
          ("GET", "/question", "question_list"), ("POST", f"/question/{QUE}/reply", "question_reply"),
          ("POST", f"/question/{QUE}/reject", "question_reject"), ("GET", "/event", "event"),
          ("POST", f"/session/{SES}/summarize", "summarize")]
    for method, path, name in ok:
        rule, upstream = match(method, path)
        assert (rule.name, upstream) == (name, path)
    bad = [("GET", "/config"), ("GET", "/global/health"), ("GET", "/api/fs/read/hostname"), ("POST", "/pty"),
           ("POST", f"/session/{SES}/shell"), ("POST", f"/session/{SES}/command"), ("DELETE", f"/session/{SES}"),
           ("POST", f"/session/{SES}/revert"), ("POST", f"/session/{SES}/unrevert"), ("POST", f"/session/{SES}/fork"),
           ("POST", f"/session/{SES}/message"), ("POST", f"/session/{SES}/permissions/{PER}"),
           ("PATCH", f"/session/{SES}/message/{MSG}/part/{PRT}"), ("DELETE", f"/session/{SES}/message/{MSG}/part/{PRT}"),
           ("GET", "/experimental/resource"), ("POST", f"/experimental/session/{SES}/background"),
           ("POST", "/instance/dispose"), ("GET", "/agent"), ("GET", "/mcp"), ("GET", "/session/status/"),
           ("GET", "//session/status"), ("GET", "/session/../global/health"), ("GET", "/session%2F..%2Fglobal%2Fhealth"),
           ("GET", f"/session/{SES}x"), ("GET", f"/session/{SES[:-1]}"), ("GET", "/Session/status"), ("GET", "")]
    for method, path in bad:
        with pytest.raises(Refused) as e:
            match(method, path)
        assert (e.value.status, e.value.message) == (403, "Вызов не разрешён"), (method, path)


def test_other_methods_refused():                                                       # Review Focus 1: близнецы
    twins = [(m, example_path(r)) for r in RULES for m in METHODS
             if m != r.method and not allowed(m, example_path(r))]
    assert ("PATCH", f"/session/{SES}") not in twins and ("GET", f"/session/{SES}") not in twins   # два правила пути
    assert len(twins) == len(RULES) * (len(METHODS) - 1) - 2
    for method, path in twins:
        with pytest.raises(Refused) as e:
            match(method, path)
        assert e.value.status == 403, (method, path)


def test_filter_query_drops_directory_and_auth_token():
    rule, _ = match("GET", "/experimental/session")
    pairs = [("roots", "true"), ("directory", "/etc"), ("archived", "yes"), ("workspace", "w"),
             ("auth_token", "b3BlbmNvZGU6eA=="), ("archived", "true"), ("roots", "false")]
    assert filter_query(rule, pairs) == [("roots", "true"), ("archived", "true")]
    assert filter_query(match("GET", "/session/status")[0], [("directory", "/etc")]) == []


def test_build_prompt():                                                                # Review Focus 2
    body = {"parts": [{"type": "text", "text": "привет", "id": "prt_x", "synthetic": True, "time": {}, "metadata": {}}],
            "agent": "probe", "system": "ты root", "tools": {"bash": True}, "model": {"providerID": "x", "modelID": "y"},
            "messageID": MSG, "noReply": True, "variant": "max", "format": {"type": "json"}}
    assert build_prompt(body) == {"parts": [{"type": "text", "text": "привет"}], "agent": "probe"}
    bad_parts = [{"type": "file", "url": "file:///proc/self/environ", "mime": "text/plain"},
                 {"type": "file", "url": "data:text/plain;base64,eA==", "mime": "text/plain"},
                 {"type": "subtask", "prompt": "x", "agent": "build"}, {"type": "agent", "name": "build"},
                 {"type": "text", "text": ""}, {"type": "text", "text": "я" * 20001}, "строка"]
    for part in bad_parts:
        with pytest.raises(Refused) as e:
            build_prompt({"parts": [part]})
        assert e.value.status == 400, part
    for body in ({}, {"parts": []}, {"parts": [{"type": "text", "text": "x"}] * 21}, [], "x",
                 {"parts": [{"type": "text", "text": "x"}], "agent": "a" * 65},              # Ш12: имя агента — по шаблону
                 {"parts": [{"type": "text", "text": "x"}], "agent": "про бу"}):
        with pytest.raises(Refused) as e:
            build_prompt(body)
        assert "a" * 65 not in e.value.message


def test_build_update_perm_answers():
    assert build_update({"title": "Новое", "permission": [{"permission": "*", "action": "allow"}], "metadata": {}}) == \
           {"title": "Новое"}
    assert build_update({"time": {"archived": None}}) == {"time": {"archived": None}}
    for body in ({}, {"permission": []}, {"title": ""}, {"title": "x" * 201}, {"time": {"archived": "вчера"}}):
        with pytest.raises(Refused):
            build_update(body)
    assert build_perm_reply({"reply": "once", "message": "ок"}) == {"reply": "once"}
    with pytest.raises(Refused) as e:
        build_perm_reply({"reply": "always"})
    assert e.value.status == 400
    assert build_answers({"answers": [["Варка"]], "extra": 1}) == {"answers": [["Варка"]]}
    for body in ({"answers": [["x"]] * 11}, {"answers": [["x"] * 21]}, {"answers": [["я" * 2001]]}, {"answers": "x"}):
        with pytest.raises(Refused):
            build_answers(body)


def test_allowed_events_exact():
    assert len(ALLOWED_EVENTS) == 20 and "plugin.added" not in ALLOWED_EVENTS and "catalog.updated" not in ALLOWED_EVENTS


# --- сверх брифа: закрепление таблицы, причин отказа, границ пределов и «ввод клиента не эхом» ---

EXPECTED_RULES = {
    # имя: (метод, путь с id-примерами, ключи запроса, сборщик тела, вид); session_create — отдельно
    "session_list": ("GET", "/experimental/session", {"roots", "archived"}, None, "forward"),
    "session_status": ("GET", "/session/status", set(), None, "forward"),
    "session_get": ("GET", f"/session/{SES}", set(), None, "forward"),
    "session_update": ("PATCH", f"/session/{SES}", set(), build_update, "forward"),
    "session_messages": ("GET", f"/session/{SES}/message", set(), None, "forward"),
    "prompt_async": ("POST", f"/session/{SES}/prompt_async", set(), build_prompt, "forward"),
    "abort": ("POST", f"/session/{SES}/abort", set(), None, "forward"),
    "permission_list": ("GET", "/permission", set(), None, "forward"),
    "permission_reply": ("POST", f"/permission/{PER}/reply", set(), build_perm_reply, "forward"),
    "question_list": ("GET", "/question", set(), None, "forward"),
    "question_reply": ("POST", f"/question/{QUE}/reply", set(), build_answers, "forward"),
    "question_reject": ("POST", f"/question/{QUE}/reject", set(), None, "forward"),
    "event": ("GET", "/event", set(), None, "event"),
    "summarize": ("POST", f"/session/{SES}/summarize", set(), None, "summarize"),
}

MARK = "zqMARKqz"                                                   # метка ввода клиента: её не должно быть в отказе
REASONS = {"not_whitelisted", "bad_path", "bad_body", "bad_part", "agent", "permission", "origin"}
TEXT = {"type": "text", "text": "x"}


def test_rules_table_pinned():
    assert len(RULES) == 15 and len({r.name for r in RULES}) == 15
    assert all(isinstance(r, Rule) for r in RULES)
    rows = {r.name: (r.method, example_path(r), set(r.query), r.body, r.kind)
            for r in RULES if r.name != "session_create"}
    assert rows == EXPECTED_RULES
    create = next(r for r in RULES if r.name == "session_create")
    assert (create.method, example_path(create), create.query, create.kind) == \
           ("POST", "/session", frozenset(), "forward")
    dangerous = {"permission": [{"permission": "*", "action": "allow"}], "parentID": SES, "workspaceID": "w"}
    assert create.body(dangerous) == {}
    assert create.body(None) == {} and create.body(MARK) == {}                          # всегда {}, что бы ни пришло
    assert all(isinstance(r.query, frozenset) for r in RULES)
    assert {r.method for r in RULES} == {"GET", "POST", "PATCH"} and set(METHODS) >= {"GET", "POST", "PATCH"}


def test_each_example_path_matches_only_its_rule():
    for rule in RULES:
        path = example_path(rule)
        assert RAW_PATH.fullmatch(path), rule.name
        assert [r.name for r in RULES if r.method == rule.method and r.pattern.fullmatch(path)] == [rule.name]
        assert match(rule.method, path) == (rule, path)


def test_constants_pinned():
    assert APPROVABLE == frozenset({"doom_loop"})                                        # Д5-R34
    assert (MAX_PARTS, MAX_TEXT, MAX_TITLE, MAX_ANSWER_LISTS, MAX_ANSWERS, MAX_ANSWER_CHARS) == \
           (20, 20000, 200, 10, 20, 2000)
    assert METHODS == ("GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS")
    assert set(ID_RE) == {"ses", "msg", "per", "que"}
    for prefix, rx in ID_RE.items():
        assert rx.fullmatch(f"{prefix}_" + "aZ09" * 6 + "xY")
        for bad in (f"{prefix}_" + "a" * 25, f"{prefix}_" + "a" * 27, f"{prefix}_" + "a" * 25 + "-",
                    f"{prefix}_" + "a" * 25 + "я", f"{prefix}_" + "a" * 25 + "\n", "xxx_" + "a" * 26,
                    f"{prefix.upper()}_" + "a" * 26, f"{prefix.capitalize()}_" + "a" * 26):   # Б2-m2: префикс строчными
            assert not rx.fullmatch(bad), (prefix, bad)
    assert ALLOWED_EVENTS == frozenset({
        "session.created", "session.updated", "session.deleted", "session.status", "session.idle",
        "session.compacted", "session.error", "message.updated", "message.removed", "message.part.updated",
        "message.part.delta", "message.part.removed", "permission.asked", "permission.replied", "question.asked",
        "question.replied", "question.rejected", "server.connected", "server.heartbeat", "server.instance.disposed"})


def test_raw_path_checked_before_rules():                                               # Review Focus 1
    for path in ("", "/", "/session/status/", "//session/status", "/session//status", "/session/../global/health",
                 "/session/./status", "/session%2F..%2Fglobal%2Fhealth", "/session%2fstatus", "/session/status?x=1",
                 "/session/status#x", "/session/stat us", "/session/statuś", "session/status", "/session/status\n",
                 "/session\\status", f"/session/{SES}/message;x", "/event.json"):
        for method in ("GET", "POST"):
            with pytest.raises(Refused) as e:
                match(method, path)
            assert (e.value.status, e.value.name, e.value.reason) == (403, "Forbidden", "bad_path"), (method, path)
    for method, path in (("GET", "/Session/status"), ("GET", "/config"), ("get", "/session/status"),
                         ("TRACE", "/session/status"), ("POST", f"/session/{SES}/message"), ("GET", "/session/x")):
        with pytest.raises(Refused) as e:
            match(method, path)
        assert (e.value.status, e.value.name, e.value.reason) == (403, "Forbidden", "not_whitelisted"), (method, path)


def test_filter_query_values_and_rules():
    rule, _ = match("GET", "/experimental/session")
    assert filter_query(rule, [("roots", "TRUE"), ("roots", "1"), ("roots", ""), ("archived", "false "),
                               ("Archived", "true"), ("archived", "false"), ("roots", "true")]) == \
           [("archived", "false"), ("roots", "true")]
    assert filter_query(rule, []) == []
    for r in RULES:
        if r.name != "session_list":
            assert filter_query(r, [("roots", "true"), ("archived", "true"), ("directory", "/")]) == [], r.name


def test_agent_name_charset():                                                          # Ш12
    for name in ("probe", "a", "a" * 64, "my-agent_1.0", "Probe.V2", "...", "0"):
        assert AGENT_NAME.fullmatch(name)
        assert build_prompt({"parts": [TEXT], "agent": name}) == {"parts": [TEXT], "agent": name}
    for name in ("", "a" * 65, "probe x", "probe\n", "\nprobe", "probe/../x", "pro%be", "probe«",
                 "prоbe",                                                               # «о» — кириллица
                 "probe\t", "pro:be", "pro@be", "pro+be", "про бу", None, 1, True, ["probe"], {"name": "probe"}):
        with pytest.raises(Refused) as e:
            build_prompt({"parts": [TEXT], "agent": name})
        assert (e.value.status, e.value.name, e.value.reason) == (400, "BadRequest", "bad_body"), repr(name)
    assert build_prompt({"parts": [TEXT]}) == {"parts": [TEXT]}                          # без поля — агент по умолчанию


def test_limits_at_boundary():
    assert build_prompt({"parts": [TEXT] * MAX_PARTS}) == {"parts": [TEXT] * MAX_PARTS}
    long_text = {"type": "text", "text": "я" * MAX_TEXT}
    assert build_prompt({"parts": [long_text]}) == {"parts": [long_text]}
    assert build_update({"title": "я" * MAX_TITLE}) == {"title": "я" * MAX_TITLE}
    assert build_update({"title": "я"}) == {"title": "я"}
    full = [["я" * MAX_ANSWER_CHARS] * MAX_ANSWERS] * MAX_ANSWER_LISTS
    assert build_answers({"answers": full}) == {"answers": full}
    assert build_answers({"answers": []}) == {"answers": []}
    assert build_answers({"answers": [[""]]}) == {"answers": [[""]]}
    for archived in (0, 1, 1_790_000_000_000, 2**53 - 1):                              # Б2-m1: граница числа JS
        assert build_update({"time": {"archived": archived}, "title": "t"}) == \
               {"title": "t", "time": {"archived": archived}}
    for archived in (-1, 1.5, 1.0, True, False, "1", [], {}, 2**53, 10**30):
        with pytest.raises(Refused):
            build_update({"time": {"archived": archived}})
    for body in ({"time": {}}, {"time": None}, {"time": "x"}, {"title": None}, {"title": 5}, [], None, "x"):
        with pytest.raises(Refused):
            build_update(body)


def test_bodies_rebuilt_not_forwarded():                                                # Review Focus 2
    parts = [{"type": "text", "text": "a", "id": "prt_x"}, {"type": "text", "text": "b", "ignored": True}]
    body = {"parts": parts}
    out = build_prompt(body)
    assert out == {"parts": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert out["parts"] is not parts and all(p is not q for p, q in zip(out["parts"], parts))
    answers = [["a", "b"]]
    got = build_answers({"answers": answers})
    assert got == {"answers": [["a", "b"]]} and got["answers"] is not answers and got["answers"][0] is not answers[0]
    assert build_update({"title": "t", "time": {"archived": 5, "created": 1}}) == \
           {"title": "t", "time": {"archived": 5}}
    assert build_perm_reply({"reply": "reject", "message": "x"}) == {"reply": "reject"}
    for body in ({"reply": "always"}, {"reply": "ONCE"}, {"reply": None}, {}, {"message": "once"}, [], "once", None):
        with pytest.raises(Refused) as e:
            build_perm_reply(body)
        assert (e.value.status, e.value.reason) == (400, "bad_body"), body
    for body in ({}, {"answers": None}, {"answers": ["x"]}, {"answers": [[1]]}, {"answers": [[None]]},
                 {"answers": [["x"], "y"]}, [], None):
        with pytest.raises(Refused) as e:
            build_answers(body)
        assert (e.value.status, e.value.reason) == (400, "bad_body"), body


def test_part_and_body_reasons():
    for part in ({"type": "file", "url": "file:///etc/passwd", "mime": "text/plain"}, {"type": "agent", "name": "x"},
                 {"type": "subtask", "prompt": "x", "description": "x", "agent": "build"}, {"text": "x"},
                 {"type": "TEXT", "text": "x"}, {"type": "text"}, {"type": "text", "text": None},
                 {"type": "text", "text": ["x"]}, {"type": "text", "text": ""},
                 {"type": "text", "text": "я" * (MAX_TEXT + 1)},
                 "строка", None, ["text"]):
        with pytest.raises(Refused) as e:
            build_prompt({"parts": [TEXT, part]})
        assert (e.value.status, e.value.name, e.value.reason) == (400, "BadRequest", "bad_part"), part
    for body in ({}, {"parts": None}, {"parts": {}}, {"parts": "x"}, {"parts": []}, {"parts": [TEXT] * (MAX_PARTS + 1)},
                 [], "x", None, 1):
        with pytest.raises(Refused) as e:
            build_prompt(body)
        assert (e.value.status, e.value.name, e.value.reason) == (400, "BadRequest", "bad_body"), body


def test_lone_surrogate_refused():
    # httpx 0.28 кодирует тело json= в UTF-8 (ensure_ascii=False): одиночный суррогат дал бы
    # UnicodeEncodeError в прокси — 500 не вида OpenCode; сборщики отказывают заранее
    for s in ("\ud800", "a\udfffb"):
        with pytest.raises(Refused) as e:
            build_prompt({"parts": [{"type": "text", "text": s}]})
        assert (e.value.status, e.value.reason) == (400, "bad_part")
        with pytest.raises(Refused):
            build_update({"title": s})
        with pytest.raises(Refused):
            build_answers({"answers": [[s]]})


def _refusals():
    """Все пути отказа модуля; в каждом вводе — метка MARK (в теле, пути или имени)."""
    big = MARK * (MAX_TEXT // len(MARK) + 1)
    text = {"type": "text", "text": MARK}
    calls = [(match, ("GET", f"/{MARK}%2F")), (match, ("GET", f"/{MARK}/")), (match, ("GET", f"/{MARK}/..")),
             (match, ("GET", f"/{MARK}")), (match, (MARK, "/session/status")),
             (match, ("POST", f"/session/{SES}/{MARK}")), (match, ("GET", f"/session/{MARK}"))]
    for body in (MARK, [MARK], {"parts": MARK}, {"parts": [], "x": MARK}, {"parts": [text] * (MAX_PARTS + 1)},
                 {"parts": [MARK]}, {"parts": [{"type": MARK, "text": MARK}]},
                 {"parts": [{"type": "file", "url": MARK}]},
                 {"parts": [{"type": "text", "text": [MARK]}]}, {"parts": [{"type": "text", "text": big}]},
                 {"parts": [{"type": "text", "text": MARK + "\ud800"}]},
                 {"parts": [text], "agent": MARK + " x"}, {"parts": [text], "agent": MARK * 10},
                 {"parts": [text], "agent": [MARK]}):
        calls.append((build_prompt, (body,)))
    for body in (MARK, {"title": MARK * 30}, {"title": [MARK]}, {"time": MARK}, {"time": {"archived": MARK}},
                 {"permission": MARK}, {"metadata": {MARK: MARK}}):
        calls.append((build_update, (body,)))
    for body in (MARK, {"reply": MARK}, {"reply": "always", "message": MARK}):
        calls.append((build_perm_reply, (body,)))
    for body in (MARK, {"answers": MARK}, {"answers": [MARK]}, {"answers": [[MARK] * (MAX_ANSWERS + 1)]},
                 {"answers": [[MARK * 300]]}, {"answers": [[MARK]] * (MAX_ANSWER_LISTS + 1)}, {"x": MARK}):
        calls.append((build_answers, (body,)))
    return calls


def test_refusals_never_echo_client_input():                                           # Ш12, решение Д4а
    constant = set(rules.RULE_MESSAGES.values())
    seen = set()
    for fn, args in _refusals():
        with pytest.raises(Refused) as e:
            fn(*args)
        err = e.value
        for field in (err.message, err.reason, err.name, str(err), repr(err)):
            assert MARK not in field, (fn.__name__, args)
        assert err.message in constant and not re.search(r"[0-9]", err.message), (fn.__name__, err.message)
        assert err.reason in REASONS and (err.status, err.name) in ((400, "BadRequest"), (403, "Forbidden"))
        assert err.__cause__ is None and err.__context__ is None                        # нет цепочки с вводом
        seen.add((fn.__name__, err.reason))
    assert seen >= {("match", "bad_path"), ("match", "not_whitelisted"), ("build_prompt", "bad_body"),
                    ("build_prompt", "bad_part"), ("build_update", "bad_body"), ("build_perm_reply", "bad_body"),
                    ("build_answers", "bad_body")}


def test_rules_module_is_pure():                                                        # решение контроллера 1
    tree = ast.parse(Path(rules.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "относительный импорт из шлюза"
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "re", "dataclasses", "typing", "collections"}, imported
