# core/tests/test_transcript.py — Д10-R102 (чистые функции)
import json

import pytest

from pcbk_core.gateway.transcript import (MESSAGES_MAX, TEXT_MAX, TOOL_TEXT_MAX, TranscriptError, sessions_view,
                                          transcript)

SES = "ses_" + "A" * 26
SESSION = {"id": SES, "title": "Значение 20FAKE_001_PV", "time": {"created": 1, "updated": 3}}


def msg(role, *parts, **info):
    return {"info": {"id": "msg_x", "sessionID": SES, "role": role, "time": {"created": 1}, **info},
            "parts": list(parts)}


def test_transcript_text_tool_and_skips():
    raw = [msg("user", {"type": "text", "text": "что сейчас <img src=x onerror=alert(1)>"}, agent="pomoshnik"),
           msg("assistant",
               {"type": "reasoning", "text": "думаю"},
               {"type": "step-start"},
               {"type": "text", "text": "напоминание", "synthetic": True},
               {"type": "tool", "tool": "pcbk_tag_now", "state": {"status": "completed",
                                                                  "input": {"tags": ["20FAKE_001_PV"]},
                                                                  "output": "12,5 м³/ч"}},
               {"type": "text", "text": "Готово: 12,5"}, agent="pomoshnik"),
           {"info": {"role": "system"}, "parts": []}]
    t = transcript(json.dumps(SESSION).encode(), json.dumps(raw).encode())
    assert t["session"] == {"id": SES, "title": "Значение 20FAKE_001_PV", "created": 1, "updated": 3,
                            "archived": False}
    assert t["messages"][0]["parts"] == [{"kind": "text", "text": "что сейчас <img src=x onerror=alert(1)>"}]
    assert t["messages"][1]["parts"] == [
        {"kind": "tool", "tool": "pcbk_tag_now", "status": "completed", "input": '{"tags": ["20FAKE_001_PV"]}',
         "output": "12,5 м³/ч"},
        {"kind": "text", "text": "Готово: 12,5"}]
    assert len(t["messages"]) == 2 and t["clipped"] is False


def test_transcript_limits_and_errors():
    big = [msg("user", {"type": "text", "text": "x" * (TEXT_MAX + 5)})] + \
          [msg("assistant", {"type": "tool", "tool": "Bad Name!", "state": {"status": "weird",
                                                                            "input": "y" * (TOOL_TEXT_MAX + 5)}}),
           msg("assistant", error={"name": "ProviderError", "data": {"message": "402 нет денег"}})]
    t = transcript(json.dumps(SESSION).encode(), json.dumps(big).encode())
    assert len(t["messages"][0]["parts"][0]["text"]) == TEXT_MAX and t["clipped"] is True
    assert (t["messages"][1]["parts"][0]["tool"], t["messages"][1]["parts"][0]["status"]) == ("?", "unknown")
    assert t["messages"][2]["error"] == "402 нет денег"
    many = [msg("user", {"type": "text", "text": "q"})] * (MESSAGES_MAX + 1)
    assert transcript(json.dumps(SESSION).encode(), json.dumps(many).encode())["clipped"] is True
    assert transcript(json.dumps(SESSION).encode(), b"[1, \"x\"]")["messages"] == []   # чужие элементы — пропуск
    for session_raw, messages_raw in ((json.dumps(SESSION).encode(), b"{"),          # не JSON
                                      (json.dumps(SESSION).encode(), b'{"a": 1}'),   # не список
                                      (b"[]", b"[]")):                               # сессия не объект
        with pytest.raises(TranscriptError):
            transcript(session_raw, messages_raw)


def test_sessions_view_filters_and_orders():
    raw = [{"id": SES, "title": "a", "time": {"created": 1, "updated": 2}},
           {"id": "ses_" + "B" * 26, "title": "b" * 300, "time": {"created": 1, "updated": 5, "archived": 9}},
           {"id": "../x", "title": "чужой вид"}, "мусор"]
    v = sessions_view(json.dumps(raw).encode())
    assert [s["id"] for s in v] == ["ses_" + "B" * 26, SES]
    assert (len(v[0]["title"]), v[0]["archived"], v[1]["archived"]) == (200, True, False)


# --- сверх брифа ---

def test_message_fields_agent_error_and_odd_values():
    """Поля сообщения по договору R102: at, agent (у ассистента — и mode), error только у ассистента; время, которое
    браузер не прочтёт точно, и чужие типы — null; ввода инструмента нет — null, ошибка инструмента — вместо вывода."""
    raw = [msg("user", agent="pomoshnik", error={"name": "X"}),                      # error у студента не показывается
           msg("assistant", {"type": "tool", "tool": "pcbk_tag_now", "state": {"status": "error", "error": "нет тега"}},
               mode="konstruktor"),
           {"info": {"role": "assistant", "time": {"created": 2 ** 60}, "agent": "x" * 65, "error": {"name": "Abort"}},
            "parts": "не список"},
           {"info": {"role": "user", "time": {"created": True}}, "parts": [{"type": "text", "text": 5}, "мусор"]}]
    t = transcript(json.dumps(SESSION).encode(), json.dumps(raw).encode())
    assert [(m["role"], m["at"], m["agent"], m["error"]) for m in t["messages"]] == [
        ("user", 1, "pomoshnik", None), ("assistant", 1, "konstruktor", None), ("assistant", None, None, "Abort"),
        ("user", None, None, None)]
    assert t["messages"][1]["parts"] == [{"kind": "tool", "tool": "pcbk_tag_now", "status": "error", "input": None,
                                          "output": "нет тега"}]
    assert t["messages"][2]["parts"] == [] and t["messages"][3]["parts"] == [] and t["clipped"] is False


def test_parts_limit_and_session_id_rules():
    from pcbk_core.gateway.transcript import PARTS_MAX
    parts = [{"type": "text", "text": str(i)} for i in range(PARTS_MAX + 1)]
    t = transcript(json.dumps(SESSION).encode(), json.dumps([msg("user", *parts)]).encode())
    assert len(t["messages"][0]["parts"]) == PARTS_MAX and t["clipped"] is True
    for bad in ({**SESSION, "id": "ses_short"}, {**SESSION, "id": None}, {"title": "без id"}):
        with pytest.raises(TranscriptError):                                         # разговор без годного id
            transcript(json.dumps(bad).encode(), b"[]")
    for raw in (b"{", b'{"a": 1}', b"\xff\xfe"):
        with pytest.raises(TranscriptError):
            sessions_view(raw)
    v = sessions_view(json.dumps([{"id": SES, "title": None, "time": {"updated": "вчера"}},
                                  {"id": "ses_" + "C" * 26, "time": {"updated": 1}}]).encode())
    assert [(s["id"], s["title"], s["updated"]) for s in v] == [("ses_" + "C" * 26, "", 1), (SES, "", None)]


# --- доводка р1, I-1: одиночный суррогат из ответа места — U+FFFD, ответ кодируется в UTF-8 ---

def test_lone_surrogates_become_replacement_char():
    bad = "\ud83d"                                                           # половина пары эмодзи
    session = {**SESSION, "title": "Значение " + bad}
    raw = [msg("user", {"type": "text", "text": "вопрос " + bad}, agent="pomo" + bad),
           msg("assistant",
               {"type": "tool", "tool": "pcbk_tag_now", "state": {"status": "completed", "input": {"q": bad},
                                                                  "output": "вывод " + bad}},
               {"type": "tool", "tool": "pcbk_tag_now", "state": {"status": "error", "error": "сбой " + bad}},
               mode="konstruktor" + bad, error={"name": "ProviderError", "data": {"message": "ошибка " + bad}})]
    session_raw, messages_raw = json.dumps(session).encode(), json.dumps(raw).encode()   # \ud83d — экраном, как у места
    assert b"\\ud83d" in messages_raw                                         # контроль: суррогат дошёл до разбора
    t = transcript(session_raw, messages_raw)
    json.dumps(t, ensure_ascii=False).encode("utf-8")                       # не падает: суррогатов в ответе нет
    assert t["session"]["title"] == "Значение �" and t["clipped"] is False
    user, assistant = t["messages"]
    assert (user["parts"], user["agent"]) == ([{"kind": "text", "text": "вопрос �"}], "pomo�")
    assert assistant["parts"] == [
        {"kind": "tool", "tool": "pcbk_tag_now", "status": "completed", "input": '{"q": "�"}',
         "output": "вывод �"},
        {"kind": "tool", "tool": "pcbk_tag_now", "status": "error", "input": None, "output": "сбой �"}]
    assert (assistant["agent"], assistant["error"]) == ("konstruktor�", "ошибка �")
    v = sessions_view(json.dumps([session, {"id": "ses_" + "B" * 26, "title": bad * 300}]).encode())
    json.dumps(v, ensure_ascii=False).encode("utf-8")
    assert [s["title"] for s in v] == ["Значение �", "�" * 200]
