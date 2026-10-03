"""Тормоз прогона в core на тестовом стенде (Д11а, R117, R118, R123а): прогонщика нет совсем — память падает ниже
порога в двойнике /proc core, тормоз сам отказывает вызовам модели мест прогона и усыпляет их; защёлка переживает
перезапуск core с тем же id прогона; порог — из .env вложенным умолчанием.

Сроки — по независимым источникам (R133), не по журналу тормоза: отказ — вызовом модели токеном места из его сети,
сон — State.FinishedAt мест (docker inspect), строка — снимок /status.json сторожа. Нижняя граница обоих — удержание:
3 проверки подряд по 5 с, защёлка не раньше t0 + 10 с (итог Д11а m-3; 9 — запас на часы).

Места 05–07 — d11-probe-brake-NN (после test_bench, который их освободил, и до test_conversation); в finally —
память core в норме, core без прогона, учётки удалены и места освобождены.
"""
import json
import time

import pytest

from conftest import HOST, PORT, STAND, STAND_HOST, STAND_ORIGIN, iso_s, row_of, shot, wait_for
from pcbk_bench.gateway import GatewayChat, GatewayError
from pcbk_watchdog.checks import RUN_HALT_DETAIL

PLACES = (5, 6, 7)
HOLD_MIN_S = 9                                                              # 3 проверки по 5 с: не раньше t0 + 10 с
LOGIN = "d11-probe-brake-{n:02d}"
RUN = {"LLM_RUN_ID": "d11-brake", "LLM_RUN_USD": "5.0", "LLM_RUN_CALLERS": ",".join(f"student-{n:02d}" for n in PLACES)}
# строка страницы состояния с этим заголовком — в середину экрана (для снимка; %s — JSON)
ROW_INTO_VIEW_JS = ("(() => { const e = [...document.querySelectorAll('#checks li .name')]"
                    ".find((x) => x.textContent.trim() === %s); if (e) e.scrollIntoView({block: 'center'}); "
                    "return !!e; })()")


@pytest.fixture(scope="module")
def brake(stack):
    pw = {n: stack.new_password() for n in PLACES}
    try:
        stack.recreate("core", RUN)
        for n in PLACES:
            stack.add_account(LOGIN.format(n=n), "student", n, pw[n])
        accounts = {n: {"login": LOGIN.format(n=n), "password": pw[n]} for n in PLACES}    # как connect() CLI стенда
        chats = {n: GatewayChat(f"https://{HOST}:{PORT}", STAND_HOST, STAND_ORIGIN, **accounts[n]) for n in PLACES}
        yield chats
    finally:
        stack.set_memory("core", 8000)
        stack.recreate("core", {})                                          # прогона нет — тормоз не активен
        stack.release_probe_places({LOGIN.format(n=n): n for n in PLACES})


def asleep_by(stack, places, t0, limit):
    """Все места остановлены, и остановлены после t0 не позже limit с (State.FinishedAt — docker inspect)."""
    def ok(n):
        st = stack.inspect(f"pcbk-student-{n:02d}")["State"]
        return not st["Running"] and 0 <= iso_s(st["FinishedAt"]) - t0 <= limit
    return all(ok(n) for n in places)


def refused_wake(chat) -> GatewayError:
    """Отказ пробуждения тормозом — именно 503 Unavailable run_halted, а не «принято, но не поднялось за срок»
    (WorkplaceNotReady, итог Д11а m-2)."""
    with pytest.raises(GatewayError) as e:
        chat.wake(10)
    assert type(e.value) is GatewayError and (e.value.status, e.value.name) == (503, "Unavailable")
    return e.value


def test_core_brake_without_runner(stack, brake, page):                    # прогонщика нет совсем
    for chat in brake.values():
        chat.login(); chat.wake(120)
        chat.send(chat.new_session(), "FAKESLOW расскажи", "pomoshnik")    # персон нет: поток 10 с
    assert stack.llm_health()["run_brake"]["state"] == "watching"           # до t0 защёлки нет (m-3)
    assert stack.llm_from_place(5)[1] != "pcbk_run_halted"
    t0 = time.time()
    stack.set_memory("core", 16)                                            # ниже порога стенда (MEM_FAIL_MIB 32)
    wait_for(lambda: stack.llm_from_place(5)[1] == "pcbk_run_halted", 20)   # независимый вызов токеном места
    refused_s = time.time() - t0                                            # верхняя граница: конец вызова с 402
    assert HOLD_MIN_S <= refused_s <= 20                                    # 3 проверки подряд по 5 с
    wait_for(lambda: asleep_by(stack, PLACES, t0, 35), 35)
    stops = [iso_s(stack.inspect(f"pcbk-student-{n:02d}")["State"]["FinishedAt"]) - t0 for n in PLACES]
    slept_s = max(stops)
    assert HOLD_MIN_S <= min(stops) and slept_s <= 35                       # не раньше удержания, не позже 35 с
    print(f"Д11а журнал: тормоз core без прогонщика — 402 pcbk_run_halted через {refused_s:.1f} с, места "
          f"{', '.join(f'{n:02d}' for n in PLACES)} остановлены через {min(stops):.1f}–{slept_s:.1f} с "
          f"(State.FinishedAt)")
    refused_wake(brake[5])
    s = stack.wait_status(lambda d: row_of(d, "llm")["detail"] == RUN_HALT_DETAIL, 30)
    assert "мало памяти" in row_of(s, "llm")["note"]
    page.goto(f"{STAND}/status"); page.wait_text("прогон остановлен тормозом памяти", 20, css="#checks")
    assert page.eval(ROW_INTO_VIEW_JS % json.dumps("LLM-прокси и бюджет", ensure_ascii=False))
    shot(page, "d11a-04-core-brake.png")


def test_core_brake_survives_core_restart(stack, brake):                   # m-5 р2: защёлка — в llm_meta
    stack.set_memory("core", 8000)                                          # память вернулась
    stack.recreate("core", RUN)                                             # тот же id прогона
    assert stack.llm_health()["run_brake"]["state"] == "halted"
    assert stack.llm_from_place(6)[1] == "pcbk_run_halted"
    refused_wake(brake[6])                                                  # места не будятся и после перезапуска (m-7)
    assert not any(stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"] for n in PLACES)
    stack.exec("pcbk-core", "python", "-m", "pcbk_core.llm.brake", "clear", "--run", "d11-brake", "--yes")
    stack.recreate("core", RUN)
    assert stack.llm_health()["run_brake"]["state"] == "watching"


THRESHOLDS = ("LLM_RUN_MEM_FAIL_MIB", "LLM_RUN_SWAP_GROW_MIB", "LLM_RUN_SWAP_BASE_MIB", "LLM_RUN_PSI_FULL_MAX")


def core_thresholds(stack, env: dict) -> tuple:
    """Пороги тормоза core в компоновке стенда с переменными env поверх test.env."""
    got = stack.compose_config(env=env)["services"]["core"]["environment"]
    return tuple(got[name] for name in THRESHOLDS)


def test_brake_threshold_from_env(stack):                                   # B-3 р1
    env = stack.compose_config(env={"LLM_RUN_MEM_FAIL_MIB": "777"})["services"]["core"]["environment"]
    assert env["LLM_RUN_MEM_FAIL_MIB"] == "777"
    env = stack.compose_config(env={})["services"]["core"]["environment"]
    assert env["LLM_RUN_MEM_FAIL_MIB"] == stack.test_env()["MEM_FAIL_MIB"]
    own = dict.fromkeys(THRESHOLDS, "")                                     # свои пороги пусты — вложенные умолчания (m-6)
    assert core_thresholds(stack, {**own, "MEM_FAIL_MIB": "333", "SWAP_GROW_WARN_MIB": "444", "PSI_FULL_MAX": "7.5"}) \
        == ("333", "444", "-1", "7.5")                                      # пороги сторожа
    assert core_thresholds(stack, {**own, "MEM_FAIL_MIB": "", "SWAP_GROW_WARN_MIB": "", "PSI_FULL_MAX": ""}) \
        == ("1024", "512", "-1", "10")                                      # и их умолчания
