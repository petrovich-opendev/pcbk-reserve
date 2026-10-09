"""LLM-прокси в компоновке: вызов через двойника OpenRouter, число на странице, отказы, выключатель, учения,
ключи и токены файлами, OpenRouter недоступен."""
import json
import math

import pytest

from conftest import TEST_OPENROUTER_KEY, wait_for

CORE = "http://172.31.250.82:8000"
FAKE_CALL_USD = 0.0012                          # цена вызова двойника OpenRouter (USAGE fake_openrouter.py)
CHAT = {"model": "z-ai/glm-5.3", "stream": True, "messages": [{"role": "user", "content": "вопрос"}]}


def row(data, cid):
    return next((c["state"], c["detail"], c.get("note", "")) for c in data["checks"] if c["component"] == cid)


def chat(stack, token, body=CHAT):
    code, text = stack.http_host("POST", CORE + "/llm/v1/chat/completions", body, token=token)
    return code, text


def usd(x: float) -> str:                       # правило fmt_usd сторожа своей копией — независимая сверка
    return "$0.00" if x == 0 else (f"${x:.4f}" if x < 1 else f"${x:.2f}")


def test_llm_call_counted_on_status_page(stack):                                    # блокер раунда 1
    before = stack.llm_health()["spent_usd"]
    # меньше $1 — на странице четыре знака, хватает одного вызова. Расход стенда копится по модулям сессии (отбор Д8
    # на двойниках — больше доллара): от $1 на странице два знака, и вызовов столько, чтобы прирост был больше цента —
    # округлённая до цента сумма тогда обязательно сменится
    calls = 1 if before < 0.99 else math.ceil(0.01 / FAKE_CALL_USD)
    for _ in range(calls):
        code, text = chat(stack, stack.llm_token(1))
        assert code == 200 and text.rstrip().endswith("data: [DONE]")
    added = calls * FAKE_CALL_USD
    wait_for(lambda: stack.llm_health()["spent_usd"] == pytest.approx(before + added), timeout=15)
    want = f"расход {usd(before + added)} из $1000"
    assert want != f"расход {usd(before)} из $1000"                                 # иначе сверка пустая
    stack.wait_status(lambda d: row(d, "llm")[:2] == ("ok", "OpenRouter отвечает")
                      and row(d, "llm")[2].split(" · ")[0] == want, timeout=40)
    sent = stack.fake_requests()[-1]["body"]
    assert sent["provider"]["zdr"] is True and sent["user"] == "pcbk-student-01"
    assert stack.llm_health()["egress"]["alive"] is True                        # serve() запустил поток адресов
    assert stack.llm_calls(1)[0]["cost_source"] == "stream"


def test_llm_refusals_without_upstream(stack):
    before = len(stack.fake_requests())
    assert chat(stack, None)[0] == 401
    assert chat(stack, stack.core_token)[0] == 401                                  # токен данных
    assert chat(stack, stack.ops_llm_token, {**CHAT, "model": "openrouter/auto"})[0] == 403
    assert len(stack.fake_requests()) == before


def test_stop_switch_keeps_data_row_green(stack):                                   # Д4а-R15
    stack.recreate("core", {"LLM_STATE": "stopped"})
    try:
        stack.wait_status(lambda d: row(d, "llm")[:2] == ("fail", "LLM-прокси остановлен администратором")
                          and row(d, "core")[0] == "ok", timeout=90)
        code, text = chat(stack, stack.llm_token(1))
        assert code == 403 and "остановлен" in json.loads(text)["error"]["message"]
    finally:
        stack.recreate("core", {})


def test_drill_402_row_and_upstream_untouched(stack):
    stack.recreate("core", {"DRILL_LLM": "402"})
    try:
        before = len(stack.fake_requests())
        code, text = chat(stack, stack.ops_llm_token)
        assert code == 402 and json.loads(text)["error"]["message"].endswith("(учения)")
        stack.wait_status(lambda d: row(d, "llm")[:2] == ("fail", "на счёте OpenRouter кончились деньги (учения)"),
                          timeout=60)
        assert len(stack.fake_requests()) == before                                 # ни одного запроса к двойнику
    finally:
        stack.recreate("core", {})


def test_core_healthy_without_key(stack):
    key = stack.secrets_dir / "openrouter.key"
    saved = key.read_text()
    key.chmod(0o644); key.write_text(""); key.chmod(0o444)
    stack.recreate("core", {})
    try:
        assert stack.inspect("pcbk-core")["State"]["Health"]["Status"] in ("starting", "healthy")
        stack.wait_healthy("pcbk-core")
        stack.wait_status(lambda d: row(d, "llm")[:2] == ("fail", "нет ключа OpenRouter")
                          and row(d, "core")[0] == "ok", timeout=60)
    finally:
        key.chmod(0o644); key.write_text(saved); key.chmod(0o444)
        stack.recreate("core", {})


def test_keys_and_tokens_are_files_not_env(stack):
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    for dest in ("/run/secrets/openrouter.key", "/run/secrets/openrouter-mgmt.key", "/run/secrets/llm-tokens"):
        assert (mounts[dest]["Type"], mounts[dest]["RW"]) == ("bind", False), dest
    dump = json.dumps(stack.inspect("pcbk-core"))
    assert "test-openrouter-key" not in dump and "test-mgmt-key" not in dump
    logs = stack.logs("pcbk-core")
    for secret in ("test-openrouter-key", "test-mgmt-key", stack.ops_llm_token, stack.llm_token(1)):
        assert secret not in logs


def test_openrouter_down_row(stack):                    # Review Focus 2; последний в модуле: двойник теряет счётчики
    # своя подготовка, а не finally прошлых тестов: ключ на месте, core с умолчаниями, строка в норме
    assert (stack.secrets_dir / "openrouter.key").read_text() == TEST_OPENROUTER_KEY
    stack.recreate("core", {})
    stack.wait_status(lambda d: row(d, "llm")[:2] == ("ok", "OpenRouter отвечает"), timeout=40)
    stack.stop("pcbk-test-openrouter")
    try:                                                # ≤ 2 опроса по 5 с и такт сторожа 10 с
        stack.wait_status(lambda d: row(d, "llm")[:2] == ("fail", "OpenRouter недоступен")
                          and row(d, "core")[0] == "ok", timeout=40)
    finally:
        stack.start("pcbk-test-openrouter")
    stack.wait_status(lambda d: row(d, "llm")[:2] == ("ok", "OpenRouter отвечает"), timeout=40)
