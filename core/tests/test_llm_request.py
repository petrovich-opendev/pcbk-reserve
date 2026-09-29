"""Политика запроса LLM-прокси: белый список моделей с верхними ценами, тело из белого списка, метка хода."""
import hashlib
import math

import pytest

from pcbk_core.llm.models import CANDIDATES, provider_matches, select_models
from pcbk_core.llm.request import (MAX_MESSAGES, SUMMARY_PROMPT, BodyRefused, TurnMark, body_bytes, rebuild_body,
                                   session_of, turn_mark)
from pcbk_core.llm.turns import COMPACTION_CONTINUE

POLICY = select_models("")["z-ai/glm-5.3"]
SES = "ses_" + "A" * 26
OC_BODY = {   # как шлёт OpenCode 1.18.33 через @ai-sdk/openai-compatible (research/08 §6)
    "model": "z-ai/glm-5.3", "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 32000,
    "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": [{"type": "text", "text": "вопрос"}]}],
    "tools": [{"type": "function", "function": {"name": "pcbk_tag_now", "description": "d", "parameters": {"type": "object"}}}],
}
EVIL = {"models": ["x/y"], "route": "fallback", "plugins": [{"id": "web"}], "preset": "p", "service_tier": "priority",
        "provider": {"allow_fallbacks": True}, "user": "evil", "session_id": "evil", "transforms": ["middle-out"],
        "n": 3, "logprobs": True, "metadata": {"a": 1}, "prompt_cache_key": "k", "cache_control": {"type": "ephemeral"},
        "reasoning": {"effort": "max"}, "reasoning_effort": "max", "include_reasoning": True,
        "web_search_options": {}, "max_completion_tokens": 999999, "debug": {"echo_upstream_body": True}}


def test_rebuild_keeps_only_whitelist_and_sets_our_fields():                    # Review Focus 4
    b = rebuild_body({**OC_BODY, **EVIL}, POLICY, caller="student-01", session=SES)
    assert set(b) == {"model", "messages", "tools", "stream", "stream_options", "max_tokens", "reasoning",
                      "provider", "user", "session_id"}
    assert b["provider"] == {"only": ["fireworks"], "allow_fallbacks": False, "zdr": True,
                             "data_collection": "deny", "require_parameters": True,
                             "max_price": {"prompt": 1.40, "completion": 4.40}}           # У1
    assert (b["max_tokens"], b["reasoning"], b["user"], b["session_id"]) == \
           (16000, {"effort": "high"}, "pcbk-student-01", f"student-01:{SES}")


def test_server_tools_dropped_function_tools_rebuilt():
    tools = OC_BODY["tools"] + [{"type": "openrouter:web_search"}, {"type": "web_search_preview"},
                                {"type": "function", "function": {"name": "x", "parameters": {}}, "strict": True}]
    b = rebuild_body({**OC_BODY, "tools": tools}, POLICY, caller="student-01", session=None)
    assert [t["function"]["name"] for t in b["tools"]] == ["pcbk_tag_now", "x"]
    assert all(set(t) == {"type", "function"} for t in b["tools"]) and b["session_id"] == "student-01:none"


def test_messages_rebuilt_per_role():
    msgs = [{"role": "user", "content": "q", "cache_control": {"type": "ephemeral"}, "name": "n"},
            {"role": "assistant", "content": None, "reasoning_content": "",
             "reasoning_details": [{"type": "reasoning.encrypted", "data": "x"}],
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "pcbk_tag_now", "arguments": "{}"}, "extra": 1}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"status\": \"ok\"}"}]
    b = rebuild_body({**OC_BODY, "messages": msgs}, POLICY, caller="student-01", session=None)
    assert b["messages"][0] == {"role": "user", "content": "q"}
    assert b["messages"][1]["reasoning_details"] == msgs[1]["reasoning_details"]
    assert b["messages"][1]["tool_calls"] == [{"id": "call_1", "type": "function",
                                               "function": {"name": "pcbk_tag_now", "arguments": "{}"}}]
    assert b["messages"][2] == msgs[2]


@pytest.mark.parametrize("bad", [[], "x", [{"role": "developer", "content": "x"}],
    [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}]}],
    [{"role": "user", "content": [{"type": "file", "file": {}}]}]])
def test_bad_messages_refused(bad):
    with pytest.raises(BodyRefused):
        rebuild_body({**OC_BODY, "messages": bad}, POLICY, caller="student-01", session=None)


@pytest.mark.parametrize("given,want", [({"max_tokens": 500}, 500), ({"max_completion_tokens": 1000}, 1000),
                                        ({"max_tokens": "x"}, 16000), ({"max_tokens": -5}, 16000), ({}, 16000)])
def test_max_tokens_cap(given, want):
    body = {k: v for k, v in OC_BODY.items() if k != "max_tokens"} | given
    assert rebuild_body(body, POLICY, caller="ops", session=None)["max_tokens"] == want


def test_claude_gets_auto_cache_others_not():
    claude = select_models("")["anthropic/claude-sonnet-5.5"]
    assert rebuild_body({**OC_BODY, "model": claude.model}, claude, caller="ops", session=None)["cache_control"] == \
           {"type": "ephemeral"}
    assert "cache_control" not in rebuild_body(OC_BODY, POLICY, caller="ops", session=None)


def test_model_list_exact():                                                     # Review Focus 4
    allowed = select_models("")
    assert list(allowed) == ["z-ai/glm-5.3", "deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash",
                             "openai/gpt-6-sol", "z-ai/glm-5.3-flash", "openai/gpt-6-luna",
                             "anthropic/claude-sonnet-5.5"]
    assert [p.provider_only for p in allowed.values()] == ["fireworks", "fireworks", "google-vertex/global", "azure",
                                                         "fireworks", "azure", "google-vertex/global"]
    assert [p.effort for p in allowed.values()] == ["high", "high", "medium", "medium", "low", "medium", "low"]
    assert list(select_models("z-ai/glm-5.3")) == ["z-ai/glm-5.3"]
    for bad in ("z-ai/glm-5.3:nitro", "~z-ai/glm-latest", "openrouter/auto", "z-ai/glm-5.3,z-ai/glm-5.3"):
        with pytest.raises(ValueError):
            select_models(bad)


def test_provider_matches_and_session_of():
    assert provider_matches("fireworks", "Fireworks") and provider_matches("google-vertex/global", "Google Vertex")
    assert provider_matches("azure", "Azure") and not provider_matches("fireworks", "Together")
    assert (session_of(SES), session_of("ses_1"), session_of(None)) == (SES, None, None)


def test_estimate_upper_bound():                                                 # Review Focus 6, Д4а-R13
    claude, glm = select_models("")["anthropic/claude-sonnet-5.5"], select_models("")["z-ai/glm-5.3"]
    assert claude.estimate_usd(1_048_576, 16000) == pytest.approx(349_526 * 2.5e-6 + 0.16)          # ≈ $1,03
    assert claude.estimate_usd(131_072 * 3, 16000) == pytest.approx(131_072 * 2.5e-6 + 0.16)         # ≈ $0,49
    b = rebuild_body(OC_BODY, POLICY, caller="student-01", session=SES)
    assert glm.estimate_usd(body_bytes(b), b["max_tokens"]) == \
           pytest.approx(math.ceil(body_bytes(b) / 3) * 1.4e-6 + 16000 * 4.4e-6)
    assert body_bytes({"a": "я"}) == len('{"a":"я"}'.encode())


def test_estimate_uses_max_variant_undiscounted_and_tier():                       # утренняя проверка У1–У3
    m = select_models("")
    ds, gem, luna, sol = (m["deepseek/deepseek-v4.1-flash"], m["google/gemini-3.8-flash"],
                          m["openai/gpt-6-luna"], m["openai/gpt-6-sol"])
    assert ds.estimate_usd(3000, 100) == pytest.approx(1000 * 0.45e-6 + 100 * 1.80e-6)    # fireworks/us, не 0.22/0.66
    assert gem.estimate_usd(3000, 100) == pytest.approx(1000 * 1.50e-6 + 100 * 7.50e-6)   # без скидки 0,5
    below, at = 271_999 * 3, 272_000 * 3                                                    # граница длинного входа
    assert luna.estimate_usd(below, 16000) == pytest.approx(271_999 * 0.1375e-6 + 16000 * 0.55e-6)
    assert luna.estimate_usd(at, 16000) == pytest.approx(272_000 * 0.275e-6 + 16000 * 0.825e-6)
    assert sol.estimate_usd(1_048_576, 16000) == pytest.approx(349_526 * 5.50e-6 + 16000 * 16.50e-6)   # ≈ $2,19
    assert [p.max_price() for p in (ds, gem, luna, sol, m["z-ai/glm-5.3-flash"])] == [
        {"prompt": 0.45, "completion": 1.80}, {"prompt": 1.50, "completion": 7.50},
        {"prompt": 0.22, "completion": 0.825}, {"prompt": 4.40, "completion": 16.50},
        {"prompt": 0.225, "completion": 0.75}]
    b = rebuild_body({**OC_BODY, "model": luna.model}, luna, caller="ops", session=None)
    assert b["provider"]["max_price"] == {"prompt": 0.22, "completion": 0.825}             # и в теле — цена длинного входа


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def test_turn_mark():
    user = lambda text: {"role": "user", "content": text}
    two = [{"role": "system", "content": "s"}, user("вопрос")]
    assert turn_mark({"messages": two}) == TurnMark("user", h("вопрос"), 2)
    assert turn_mark({"messages": [*two, {"role": "tool", "tool_call_id": "c", "content": "x"}]}).kind == "other"
    for text in (COMPACTION_CONTINUE, "The previous request exceeded … files.\n\n" + COMPACTION_CONTINUE):
        assert turn_mark({"messages": [*two, {"role": "assistant", "content": "итог"}, user(text)]}) == \
               TurnMark("continue", h("вопрос"), 4)
    # после сжатия: [сжатие, сводка, хвост с новым вопросом, продолжение]
    after = [user(SUMMARY_PROMPT), {"role": "assistant", "content": "сводка"}, user("вопрос 2"), user(COMPACTION_CONTINUE)]
    assert turn_mark({"messages": after}) == TurnMark("continue", h("вопрос 2"), 4)
    only = [user(SUMMARY_PROMPT), {"role": "assistant", "content": "сводка"}, user(COMPACTION_CONTINUE)]
    assert turn_mark({"messages": only}) == TurnMark("continue", None, 3)
    plan = {"role": "user", "content": [{"type": "text", "text": COMPACTION_CONTINUE}, {"type": "text", "text": "<plan>"}]}
    assert turn_mark({"messages": [*two, plan]}).kind == "continue"          # часть PROMPT_PLAN рядом
    parts = {"role": "user", "content": [{"type": "text", "text": "воп"}, {"type": "text", "text": "рос"}]}
    assert turn_mark({"messages": [two[0], parts]}).text_hash == h("воп\nрос")


# --- сверх брифа: развилки реализации, закреплённые тестами ---

def test_table_is_seven_upper_prices():
    """Таблица дословно по брифу: вход / выход / запись, кэш Claude, ступень Azure."""
    assert SUMMARY_PROMPT == "What did we do so far?"                          # session/message-v2.ts:235
    got = [(p.model, p.in_usd_m, p.out_usd_m, p.write_usd_m, p.auto_cache,
            p.tier_tokens, p.tier_in_usd_m, p.tier_out_usd_m, p.tier_write_usd_m) for p in CANDIDATES]
    assert got == [
        ("z-ai/glm-5.3", 1.40, 4.40, 0, False, None, 0.0, 0.0, 0.0),
        ("deepseek/deepseek-v4.1-flash", 0.45, 1.80, 0.45, False, None, 0.0, 0.0, 0.0),
        ("google/gemini-3.8-flash", 1.50, 7.50, 0, False, None, 0.0, 0.0, 0.0),
        ("openai/gpt-6-sol", 2.20, 11.00, 2.75, False, 272_000, 4.40, 16.50, 5.50),
        ("z-ai/glm-5.3-flash", 0.225, 0.75, 0, False, None, 0.0, 0.0, 0.0),
        ("openai/gpt-6-luna", 0.11, 0.55, 0.1375, False, 272_000, 0.22, 0.825, 0.275),
        ("anthropic/claude-sonnet-5.5", 2.00, 10.00, 2.50, True, None, 0.0, 0.0, 0.0)]


def test_select_models_order_spaces_and_errors():
    assert list(select_models("openai/gpt-6-luna, z-ai/glm-5.3")) == ["openai/gpt-6-luna", "z-ai/glm-5.3"]
    assert list(select_models("  ")) == [p.model for p in CANDIDATES]
    for bad in ("openrouter/auto", "z-ai/glm-5.3,,openai/gpt-6-luna", "z-ai/glm-5.3,"):
        with pytest.raises(ValueError) as e:
            select_models(bad)
        assert str(e.value).startswith("LLM_MODELS: ")        # задача 6: «настройки: LLM_MODELS…» на странице


def test_provider_matches_needs_both_names():
    assert not provider_matches("fireworks", "") and not provider_matches("fireworks", "—")
    assert provider_matches("fireworks", "Fireworks (US)")      # вариант базового имени не различается (У1)


def test_body_refused_texts():
    with pytest.raises(BodyRefused) as e:
        rebuild_body(["не объект"], POLICY, caller="ops", session=None)
    assert str(e.value) == "тело запроса — не объект JSON"
    bad_bodies = [
        {**OC_BODY, "messages": []},
        {**OC_BODY, "messages": [{"role": "user", "content": "q"}] * (MAX_MESSAGES + 1)},
        {**OC_BODY, "messages": ["строка"]},
        {**OC_BODY, "messages": [{"role": "developer502", "content": "x"}]},
        {**OC_BODY, "messages": [{"role": "user", "content": 502}]},
        {**OC_BODY, "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}]},
        {**OC_BODY, "messages": [{"role": "assistant", "tool_calls": [{"id": "c", "type": "function",
                                                                       "function": {"name": "f"}}]}]},
        {**OC_BODY, "messages": [{"role": "tool", "content": "x"}]},
        {**OC_BODY, "messages": [{"role": "user", "content": "\ud800"}]},        # одиночный суррогат не в UTF-8
        {**OC_BODY, "tools": [{"type": "function", "function": {"name": "f", "parameters": {"x": float("nan")}}}]},
    ]
    texts = set()
    for body in bad_bodies:
        with pytest.raises(BodyRefused) as e:
            rebuild_body(body, POLICY, caller="ops", session=None)
        texts.add(str(e.value))
    # текст идёт клиенту после «Запрос к модели отклонён: »: без чисел (OpenCode ищет в нём 429|500|502…)
    assert not any(ch.isdigit() for t in texts for ch in t), texts
    assert all("502" not in t and "developer" not in t for t in texts)


def test_optional_fields_only_when_valid():
    good = {"stop": ["a", "b"], "seed": 7, "response_format": {"type": "json_object", "extra": 1},
            "temperature": 0.2, "top_p": 1, "tool_choice": {"type": "function", "function": {"name": "f", "x": 1}}}
    b = rebuild_body({**OC_BODY, **good}, POLICY, caller="ops", session=None)
    assert (b["stop"], b["seed"], b["response_format"], b["temperature"], b["top_p"], b["tool_choice"]) == \
           (["a", "b"], 7, {"type": "json_object"}, 0.2, 1, {"type": "function", "function": {"name": "f"}})
    schema = {"type": "json_schema", "json_schema": {"name": "n", "schema": {"type": "object"}}}
    assert rebuild_body({**OC_BODY, "response_format": schema, "stop": "x", "tool_choice": "required"}, POLICY,
                        caller="ops", session=None)["response_format"] == schema
    bad = {"stop": ["a"] * 5, "seed": True, "response_format": {"type": "json_schema"}, "temperature": 2.5,
           "top_p": float("nan"), "tool_choice": "any", "stream": "yes"}
    b = rebuild_body({**OC_BODY, **bad}, POLICY, caller="ops", session=None)
    assert not set(bad) & set(b) and "stream_options" not in b
    b = rebuild_body({**OC_BODY, "stream": False, "tools": [{"type": "web_search_preview"}]}, POLICY,
                     caller="ops", session=None)
    assert b["stream"] is False and "stream_options" not in b and "tools" not in b


def test_rebuild_does_not_touch_raw_and_parts_lose_cache_control():
    raw = {**OC_BODY, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "q", "cache_control": {"type": "ephemeral"}}]}]}
    b = rebuild_body(raw, POLICY, caller="ops", session=None)
    assert b["messages"] == [{"role": "user", "content": [{"type": "text", "text": "q"}]}]
    assert raw["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"} and raw["max_tokens"] == 32000


@pytest.mark.parametrize("value", [True, 500.0, "500", None, 0])
def test_max_tokens_only_positive_json_integer(value):
    body = {k: v for k, v in OC_BODY.items() if k != "max_tokens"} | {"max_tokens": value}
    assert rebuild_body(body, POLICY, caller="ops", session=None, max_tokens_cap=400)["max_tokens"] == 400
