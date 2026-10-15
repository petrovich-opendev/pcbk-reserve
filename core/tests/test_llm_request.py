"""Политика запроса LLM-прокси: белый список моделей с верхними ценами, тело из белого списка, метка хода."""
import hashlib
import json
import math
from dataclasses import replace

import pytest

from pcbk_core.llm.models import (BYTES_PER_TOKEN, CANDIDATES, PROVIDER_FIXED, ModelPolicy, provider_matches,
                                  select_models)
from pcbk_core.llm.request import (ATTACHMENT_TEXT, MAX_MESSAGES, SUMMARY_PROMPT, SYNTHETIC_ATTACHMENT_PROMPT,
                                   BodyRefused, TurnMark, body_bytes, rebuild_body, session_of, turn_mark)
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
                             "max_price": {"prompt": 1.40, "completion": 4.40, "request": 0}}   # У1; M3
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


@pytest.mark.parametrize("bad", [[], "x", [{"role": "developer", "content": "x"}]])
def test_bad_messages_refused(bad):     # раунд 2: image_url и file — не отказ, см. test_attachments_replaced_in_every_role
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
                             "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5"]
    assert [p.provider_only for p in allowed.values()] == ["fireworks", "deepinfra/fp8", "google-vertex/global",
                                                         "azure", "fireworks", "azure", "google-vertex/global",
                                                         "google-vertex/global"]
    assert [p.effort for p in allowed.values()] == ["high", "high", "medium", "medium", "low", "medium", "low",
                                                    "high"]
    assert list(select_models("z-ai/glm-5.3")) == ["z-ai/glm-5.3"]
    for bad in ("z-ai/glm-5.3:nitro", "~z-ai/glm-latest", "openrouter/auto", "z-ai/glm-5.3,z-ai/glm-5.3"):
        with pytest.raises(ValueError):
            select_models(bad)


def test_provider_matches_and_session_of():
    assert provider_matches("fireworks", "Fireworks") and provider_matches("google-vertex/global", "Google Vertex")
    assert provider_matches("azure", "Azure") and not provider_matches("fireworks", "Together")
    assert provider_matches("deepinfra/fp8", "DeepInfra")        # Д17 Task 6: suffix точки, точка deepinfra/fp8
    assert (session_of(SES), session_of("ses_1"), session_of(None)) == (SES, None, None)


def test_estimate_upper_bound():                                                 # Review Focus 6, Д4а-R13
    claude, glm = select_models("")["anthropic/claude-sonnet-5.5"], select_models("")["z-ai/glm-5.3"]
    # раунд 2: байт — токен у всех моделей
    assert claude.estimate_usd(1_048_576, 16000) == pytest.approx(1_048_576 * 2.5e-6 + 0.16)        # ≈ $2,78
    assert claude.estimate_usd(131_072, 16000) == pytest.approx(131_072 * 2.5e-6 + 0.16)            # ≈ $0,49
    b = rebuild_body(OC_BODY, POLICY, caller="student-01", session=SES)
    assert glm.estimate_usd(body_bytes(b), b["max_tokens"]) == \
           pytest.approx(math.ceil(body_bytes(b) / 1) * 1.4e-6 + 16000 * 4.4e-6)
    assert body_bytes({"a": "я"}) == len('{"a":"я"}'.encode())


def test_estimate_uses_max_variant_undiscounted_and_tier():                       # утренняя проверка У1–У3
    m = select_models("")
    ds, gem, luna, sol = (m["deepseek/deepseek-v4.1-flash"], m["google/gemini-3.8-flash"],
                          m["openai/gpt-6-luna"], m["openai/gpt-6-sol"])
    assert ds.estimate_usd(3000, 100) == pytest.approx(3000 * 0.20e-6 + 100 * 0.60e-6)    # deepinfra/fp8, без скидки 0,3
    assert gem.estimate_usd(3000, 100) == pytest.approx(3000 * 1.50e-6 + 100 * 7.50e-6)   # без скидки 0,5
    below, at = 271_999, 272_000                                        # граница длинного входа; байт — токен (раунд 2)
    assert luna.estimate_usd(below, 16000) == pytest.approx(271_999 * 0.1375e-6 + 16000 * 0.55e-6)
    assert luna.estimate_usd(at, 16000) == pytest.approx(272_000 * 0.275e-6 + 16000 * 0.825e-6)
    assert sol.estimate_usd(1_048_576, 16000) == pytest.approx(1_048_576 * 5.50e-6 + 16000 * 16.50e-6)   # ≈ $6,03
    # доводка ревью I2, M3: max_price — по той же ступени, что оценка; плата за запрос — 0
    assert [p.max_price(271_999) for p in (ds, gem, luna, sol, m["z-ai/glm-5.3-flash"])] == [
        {"prompt": 0.20, "completion": 0.60, "request": 0}, {"prompt": 1.50, "completion": 7.50, "request": 0},
        {"prompt": 0.11, "completion": 0.55, "request": 0}, {"prompt": 2.20, "completion": 11.00, "request": 0},
        {"prompt": 0.225, "completion": 0.75, "request": 0}]
    assert [p.max_price(272_000) for p in (luna, sol, ds)] == [
        {"prompt": 0.22, "completion": 0.825, "request": 0}, {"prompt": 4.40, "completion": 16.50, "request": 0},
        {"prompt": 0.20, "completion": 0.60, "request": 0}]
    b = rebuild_body({**OC_BODY, "model": luna.model}, luna, caller="ops", session=None)
    assert b["provider"]["max_price"] == {"prompt": 0.11, "completion": 0.55, "request": 0}   # короткое тело — базовая


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def test_turn_mark():
    user = lambda text: {"role": "user", "content": text}
    two = [{"role": "system", "content": "s"}, user("вопрос")]
    assert turn_mark({"messages": two}) == TurnMark("user", h("вопрос"), 2)
    assert turn_mark({"messages": [*two, {"role": "tool", "tool_call_id": "c", "content": "x"}]}).kind == "other"
    for text in (COMPACTION_CONTINUE, "The previous request exceeded … files.\n\n" + COMPACTION_CONTINUE):
        assert turn_mark({"messages": [*two, {"role": "assistant", "content": "итог"}, user(text)]}) == \
               TurnMark("continue", None, 4)          # доводка ревью I1: перед продолжением ответ — посреди хода
        assert turn_mark({"messages": [*two, user(text)]}) == TurnMark("continue", h("вопрос"), 3)
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

def test_table_is_eight_upper_prices():
    """Таблица дословно по брифу: вход / выход / запись, кэш Claude, ступень Azure."""
    assert SUMMARY_PROMPT == "What did we do so far?"                          # session/message-v2.ts:235
    got = [(p.model, p.in_usd_m, p.out_usd_m, p.write_usd_m, p.auto_cache, p.tier_tokens, p.tier_in_usd_m,
            p.tier_out_usd_m, p.tier_write_usd_m) for p in CANDIDATES]
    assert got == [
        ("z-ai/glm-5.3", 1.40, 4.40, 0, False, None, 0.0, 0.0, 0.0),
        ("deepseek/deepseek-v4.1-flash", 0.20, 0.60, 0.20, False, None, 0.0, 0.0, 0.0),
        ("google/gemini-3.8-flash", 1.50, 7.50, 0, False, None, 0.0, 0.0, 0.0),
        ("openai/gpt-6-sol", 2.20, 11.00, 2.75, False, 272_000, 4.40, 16.50, 5.50),
        ("z-ai/glm-5.3-flash", 0.225, 0.75, 0, False, None, 0.0, 0.0, 0.0),
        ("openai/gpt-6-luna", 0.11, 0.55, 0.1375, False, 272_000, 0.22, 0.825, 0.275),
        ("anthropic/claude-sonnet-5.5", 2.00, 10.00, 2.50, True, None, 0.0, 0.0, 0.0),
        ("anthropic/claude-opus-5.5", 4.00, 20.00, 5.00, True, None, 0.0, 0.0, 0.0)]
    # доводка ревью, раунд 2: байт — токен у всех моделей (не больше токена на байт у любого токенизатора)
    assert BYTES_PER_TOKEN == 1
    assert all(p.input_tokens(b) == b for p in CANDIDATES for b in (0, 1, 2, 3, 1_048_576))


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


# --- доводка по ревью задачи 3 ---

def test_max_price_follows_estimate_tier_at_boundary():                          # I2
    """У границы 272 000 токенов max_price в теле — ступень оценки этого же тела, с обеих сторон границы."""
    luna = select_models("")["openai/gpt-6-luna"]
    raw = lambda k: {**OC_BODY, "model": luna.model, "messages": [{"role": "user", "content": "x" * k}]}
    empty = body_bytes(rebuild_body(raw(0), luna, caller="ops", session=None))
    first_tier = BYTES_PER_TOKEN * (luna.tier_tokens - 1) + 1 - empty   # столько «x» — и вход ровно 272 000 токенов
    tiers = []
    for k in range(first_tier - 2, first_tier + 2):
        b = rebuild_body(raw(k), luna, caller="ops", session=None)
        n = luna.input_tokens(body_bytes(b))
        assert b["provider"]["max_price"] == luna.max_price(n)
        tier = n >= luna.tier_tokens
        tiers.append(tier)
        write, out = (0.275, 0.825) if tier else (0.1375, 0.55)
        assert luna.estimate_usd(body_bytes(b), b["max_tokens"]) == pytest.approx(n * write / 1e6 + 16000 * out / 1e6)
    assert tiers == [False, False, True, True]


def test_max_price_at_boundary_never_above_estimate_tier():                      # I2, запасная ветка
    """Если цена длинного входа в JSON короче базовой, у границы ступени не сходятся: тогда в теле остаётся
    базовая, а оценка — по длинному входу. Цена длинного входа при оценке по базовой — никогда."""
    plain = ModelPolicy("x/y", "azure", "medium", 2.25, 11.25, 0.0)
    raw = lambda k: {"messages": [{"role": "user", "content": "x" * k}]}
    empty = body_bytes(rebuild_body(raw(0), plain, caller="ops", session=None))
    tiered = replace(plain, tier_tokens=plain.input_tokens(empty) + 10, tier_in_usd_m=4.0,
                     tier_out_usd_m=15.0)                                 # «4.0» короче «2.25»
    first_tier = BYTES_PER_TOKEN * (tiered.tier_tokens - 1) + 1 - empty
    kept_base = 0
    for k in range(first_tier - 3, first_tier + 4):
        b = rebuild_body(raw(k), tiered, caller="ops", session=None)
        n, price = tiered.input_tokens(body_bytes(b)), b["provider"]["max_price"]
        assert price in (tiered.max_price(0), tiered.max_price(n))
        assert not (price == tiered.max_price(10**9) and n < tiered.tier_tokens)
        kept_base += price == tiered.max_price(0) and n >= tiered.tier_tokens
    assert kept_base                                                      # запасная ветка сработала


@pytest.mark.parametrize("policy", CANDIDATES, ids=lambda p: p.model)
def test_estimate_covers_digit_per_token(policy):                                # I3, раунд 2
    """Ответы историана — почти одни цифры: Gemini режет числа по цифре, cl100k/o200k/DeepSeek — до ≈1,33
    байта на токен. Оценка любой модели не ниже «токен на знак» × цена входа."""
    digits = "20.5;21.75;19.0;" * 20_000
    b = rebuild_body({**OC_BODY, "model": policy.model, "messages": [
        {"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "c", "content": digits}]},
        policy, caller="ops", session=None)
    count = sum(ch.isdigit() for ch in digits)
    assert policy.estimate_usd(body_bytes(b), b["max_tokens"]) >= \
           count * policy.in_usd_m / 1e6 + b["max_tokens"] * policy.out_usd_m / 1e6
    assert policy.input_tokens(body_bytes(b)) == body_bytes(b)


def test_tool_choice_only_with_tools():                                          # M1
    server_only = [{"type": "openrouter:web_search"}]
    for choice in ("auto", "required", {"type": "function", "function": {"name": "pcbk_tag_now"}}):
        b = rebuild_body({**OC_BODY, "tools": server_only, "tool_choice": choice}, POLICY, caller="ops", session=None)
        assert "tools" not in b and "tool_choice" not in b
        assert rebuild_body({**OC_BODY, "tool_choice": choice}, POLICY, caller="ops", session=None)["tool_choice"] == choice


def test_provider_fixed_is_read_only():                                          # M2
    with pytest.raises(TypeError):
        PROVIDER_FIXED["zdr"] = False
    b = rebuild_body(OC_BODY, POLICY, caller="ops", session=None)
    b["provider"]["zdr"] = False                                                  # тело — своя копия
    assert PROVIDER_FIXED["zdr"] is True and rebuild_body(OC_BODY, POLICY, caller="ops", session=None)["provider"]["zdr"]


SECRET = "SECRETBYTES0123"                       # байты вложения: их не должно быть в собранном теле
ATTACHMENTS = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{SECRET}"}},
               {"type": "file", "file": {"filename": "a.pdf", "file_data": f"data:application/pdf;base64,{SECRET}"}},
               {"type": "input_audio", "input_audio": {"data": SECRET, "format": "wav"}},
               {"type": "video_url", "video_url": {"url": SECRET}}, SECRET, {"type": "text", "text": 5, "x": SECRET}]
PLACEHOLDER = {"type": "text", "text": ATTACHMENT_TEXT}


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
def test_attachments_replaced_in_every_role(role):                               # M4, раунд 2
    """Часть не-текст заменяется постоянной частью ATTACHMENT_TEXT: сессия не застревает, байты наверх не идут."""
    msg = {"role": role, "content": [{"type": "text", "text": "q"}, *ATTACHMENTS]}
    if role == "tool":
        msg["tool_call_id"] = "c"
    b = rebuild_body({**OC_BODY, "messages": [{"role": "user", "content": "q"}, msg]}, POLICY, caller="ops", session=None)
    assert b["messages"][1]["content"] == [{"type": "text", "text": "q"}] + [PLACEHOLDER] * len(ATTACHMENTS)
    assert SECRET not in json.dumps(b) and "base64" not in json.dumps(b)


def test_opencode_tool_media_message_passes_as_turn_step():                     # раунд 2
    """OpenCode кладёт вложения ответа инструмента отдельным `user` (session/message-v2.ts:382–399): тело не
    отказ, а сообщение — шаг хода (`other`), не новый вопрос; перед продолжением — не «настоящий» user."""
    media = {"role": "user", "content": [{"type": "text", "text": SYNTHETIC_ATTACHMENT_PROMPT}, ATTACHMENTS[0]]}
    call = {"role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "вопрос"}, call,
            {"role": "tool", "tool_call_id": "c1", "content": "{}"}, media]
    b = rebuild_body({**OC_BODY, "messages": msgs}, POLICY, caller="ops", session=None)
    assert b["messages"][-1]["content"] == [{"type": "text", "text": SYNTHETIC_ATTACHMENT_PROMPT}, PLACEHOLDER]
    assert turn_mark(b) == TurnMark("other", None, 5)
    cont = {**b, "messages": [*b["messages"], {"role": "user", "content": COMPACTION_CONTINUE}]}
    assert turn_mark(cont) == TurnMark("continue", None, 6)


def test_turn_mark_hash_ignores_system_reminders():                              # I1
    plan = "<system-reminder>\n# Plan Mode - System Reminder\n…\n</system-reminder>\n"
    build = "<system-reminder>\nYour operational mode has changed from plan to build.\n</system-reminder>\n\nA plan…"
    for extra in ([plan], [build], [plan, build]):
        msg = {"role": "user", "content": [{"type": "text", "text": "вопрос"}] +
               [{"type": "text", "text": t} for t in extra]}
        assert turn_mark({"messages": [{"role": "system", "content": "s"}, msg]}) == TurnMark("user", h("вопрос"), 2)


def test_attachment_only_user_is_not_a_question():                               # решение контроллера, раунд 2
    """`user` из одних заглушек вложений (и напоминаний, и пустоты) — шаг хода: `other`, а перед продолжением
    — не «настоящий» вопрос. Текст рядом с вложением — вопрос."""
    reminder = {"type": "text", "text": "<system-reminder>\nplan\n</system-reminder>"}
    head = [{"role": "system", "content": "s"}, {"role": "user", "content": "вопрос"}, {"role": "assistant", "content": "a"}]
    for content in ([ATTACHMENTS[0]], [ATTACHMENTS[0], ATTACHMENTS[1]], [ATTACHMENTS[0], reminder],
                    [{"type": "text", "text": " "}, ATTACHMENTS[2]], "", []):
        b = rebuild_body({**OC_BODY, "messages": [*head, {"role": "user", "content": content}]}, POLICY,
                         caller="ops", session=None)
        assert turn_mark(b) == TurnMark("other", None, 4), content
        cont = [*b["messages"], {"role": "user", "content": COMPACTION_CONTINUE}]
        assert turn_mark({"messages": cont}) == TurnMark("continue", None, 5), content
    b = rebuild_body({**OC_BODY, "messages": [*head, {"role": "user", "content": [
        {"type": "text", "text": "что на картинке?"}, ATTACHMENTS[0]]}]}, POLICY, caller="ops", session=None)
    assert turn_mark(b) == TurnMark("user", h("что на картинке?\n" + ATTACHMENT_TEXT), 4)
