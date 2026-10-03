"""Учётные записи в кабинете преподавателя на стенде (Д10б, Б3; Д10-R108…R110): преподаватель acc-t в браузере заводит
студента d10-probe-n на свободном месте 04 и видит пароль один раз; студент входит выданным паролем (вход будит место
04); преподаватель отключает учётку — следующий вызов чата студента (он пишет дальше) получает 401, и чат уходит на
вход; вход тем же паролем — 401 (строка журнала core — reason=disabled). Чат без разговора потока /event не держит и
шлюз сам не опрашивает: на вход он уходит по первому же действию студента, а не сам. Калькулятор преподавателя считает
за вызывающего teacher, канал web (Д10-R109).

Модуль идёт первым по алфавиту (сверка с кодом, п. 14): место 04 свободно. Учётку acc-t заводит фикстура accounts_t
командой администратора; её finally убирает всё, что оставил модуль (каждый шаг смотрит на стенд и идемпотентен, сбой
шага не обрывает следующих — первый сбой поднимается в конце):
1. d10-probe-n — disable и remove;
2. агенты места 04 — в архив (agents.admin archive --place 4 --yes), пока место работает, и очередь перезагрузки места
   пустеет: архив ставит место в очередь, а снимает её только готовое место. Спящее место без агентов не трогается —
   метку очереди оно не снимет (как в benchstand); спящее место с агентами — в архив всё равно (иначе release-place
   откажет);
3. release-place --place 4 --yes — закрепление места за пробным логином снято;
4. место 04 — в сон;
5. acc-t — disable и remove.
Затем сверка: строк d10-probe-n и acc-t в accounts list нет, место 04 спит. Так test_entry получает место 04 свободным.
Разговоров у пробы нет (она только входит) — место 04 уходит к test_entry пустым.

Пароль студента берётся только из DOM окна пароля и сразу становится Secret (в трассировке pytest «<скрыт>»). Он не
печатается ни в журнал, ни в assert: проверки «пароля нет» — булевы помощники, текст страницы и журналы в переменные
теста не попадают. Снимок (PCBK_SHOTS) — после закрытия окна пароля.
"""
import json
import re
import secrets
from contextlib import contextmanager

import pytest

from conftest import LOGIN_FORM_JS, STAND_ORIGIN, Secret, _tab, login_to_chat, shot, wait_for

PLACE, LOGIN = 4, "d10-probe-n"
TEACHER = "acc-t"
NN = f"{PLACE:02d}"
CONTAINER = f"pcbk-student-{NN}"
ROW = f'tr[data-place="{NN}"]'
ACCOUNTS = ("python", "-m", "pcbk_core.gateway.accounts")
PASSWORD_JS = "document.querySelector('.password-once code')?.textContent"
# строк очереди перезагрузки агентов у места модуля — изнутри pcbk-core, только чтение (как QUEUED_PY test_agents)
QUEUED_PY = ("import sqlite3; "
             "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
             f"print(c.execute('SELECT COUNT(*) FROM agent_reloads WHERE place = {PLACE}').fetchone()[0])")
# живые строки учёта агентов места и любой не-каталог в его каталоге — то, что держит release-place (Д7-R61)
AGENT_ROWS_PY = ("import sqlite3; "
                 "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
                 "print(c.execute(\"SELECT COUNT(*) FROM agents WHERE place = ? AND state != 'archived'\", "
                 f"({PLACE},)).fetchone()[0])")
DRAIN_S = 60                     # перезагрузка места — после простоя (reload.py), с запасом (PENDING_WAIT_S test_bench)
tab = contextmanager(_tab)       # две изолированные вкладки (преподаватель и студент): своя cookie у каждой


# --- учётки и место (команды администратора в pcbk-core) ---

def accounts(stack, *args: str) -> str:
    """Команда администратора учёток в pcbk-core; код не 0 — RuntimeError со stderr (пароля в нём нет)."""
    return stack.exec("pcbk-core", *ACCOUNTS, *args)


def account_state(stack, login: str) -> str | None:
    """Состояние учётки по accounts list: «активна» или «отключена»; учётки нет — None."""
    return next((cols[3] for cols in (r.split("\t") for r in stack.account_rows(login)) if cols[0] == login), None)


def bound(stack, login: str) -> bool:
    """Место закреплено за login (строка «место NN\\t<логин>\\t…» accounts list), в том числе надгробием."""
    return any(r.startswith("место ") for r in stack.account_rows(login))


def running(stack) -> bool:
    return bool(stack.inspect(CONTAINER)["State"]["Running"])


def queued(stack) -> int:
    return int(stack.exec("pcbk-core", "python", "-c", QUEUED_PY))


def has_agents(stack) -> bool:
    """Живые строки учёта агентов места 04 или файлы в его каталоге."""
    if int(stack.exec("pcbk-core", "python", "-c", AGENT_ROWS_PY)):
        return True
    code, out, _ = stack.sh("pcbk-core", f"find /srv/agents/student-{NN} -mindepth 1 ! -type d -print -quit")
    return code != 0 or bool(out.strip())


def drop_account(stack, login: str) -> None:
    """disable (если учётка действует) и remove; учётки нет — ничего."""
    state = account_state(stack, login)
    if state is None:
        return
    if state == "активна":
        accounts(stack, "disable", "--login", login)
    accounts(stack, "remove", "--login", login, "--yes")


def archive_place(stack) -> None:
    """Агенты места 04 — в архив, пока место работает, затем очередь перезагрузки места пуста (≤ DRAIN_S)."""
    if running(stack):
        stack.agents_admin("archive", "--place", str(PLACE), "--yes")
        wait_for(lambda: queued(stack) == 0, DRAIN_S)
    elif has_agents(stack):                     # спящее место метку не снимет, но без архива release-place откажет
        stack.agents_admin("archive", "--place", str(PLACE), "--yes")


def release_place(stack) -> None:
    """Закрепление места 04 за пробным логином — снять; место чужого логина не трогается."""
    if bound(stack, LOGIN):
        accounts(stack, "release-place", "--place", str(PLACE), "--yes")


def place_asleep(stack) -> None:
    if running(stack):
        stack.stop(CONTAINER)


def cleanup(stack) -> None:
    """Уборка модуля по брифу Б3 (порядок — шапка модуля); сбой шага не обрывает следующих, первый — в конце."""
    errors: list[Exception] = []
    for step in (lambda: drop_account(stack, LOGIN), lambda: archive_place(stack), lambda: release_place(stack),
                 lambda: place_asleep(stack), lambda: drop_account(stack, TEACHER)):
        try:
            step()
        except Exception as exc:                # noqa: BLE001 — уборка идёт дальше, первый сбой — в конце
            errors.append(exc)
    if errors:
        raise errors[0]
    assert (stack.account_rows(LOGIN), stack.account_rows(TEACHER), running(stack)) == ([], [], False)


@pytest.fixture(scope="module")
def accounts_t(stack):
    """Учётка преподавателя acc-t (командой администратора, пароль — stdin): её пароль (Secret). В finally — уборка
    модуля: место 04 свободно и спит, учёток модуля нет."""
    password = Secret(secrets.token_urlsafe(12))
    try:
        stack.add_account(TEACHER, "teacher", None, password)
        yield password
    finally:
        cleanup(stack)


# --- браузер ---

def login_teacher(page, login: str, password: str) -> None:
    """Вход преподавателя формой /login (вкладка без сессии): страница входа сама ведёт в кабинет (/chat/, Д10а B-2) —
    таблица мест и раздел «Учётные записи» с формой «Завести студента»."""
    page.goto(STAND_ORIGIN + "/login")
    page.wait(lambda: page.eval(LOGIN_FORM_JS), 90, "форма входа")
    page.type("#login", login)
    page.type("#password", password)
    page.press_enter("#password")
    page.wait_url("/chat/", 60)
    page.wait_selector("table.places", 60)
    page.wait_selector("#acc-login", 30)


def on_page(page, secret: str) -> bool:
    """secret есть в разметке страницы (outerHTML, со скрытыми элементами) — только ответ «да/нет»."""
    return str(secret) in (page.eval("document.documentElement.outerHTML") or "")


def in_core_log(stack, text: str) -> bool:
    """text есть в журнале pcbk-core — только ответ «да/нет»: журнал в переменные теста не попадает."""
    return str(text) in stack.logs("pcbk-core")


def in_teacher_journal(stack, text: str) -> bool:
    """text есть в журнале действий преподавателя (команда journal) — только ответ «да/нет»."""
    return str(text) in accounts(stack, "journal", "--last", "1000")


def account_journal(stack) -> set[tuple[str, ...]]:
    """Строки действий учёток acc-t в журнале действий: (действие, место, логин учётки, итог)."""
    rows = [line.split("\t") for line in accounts(stack, "journal", "--last", "1000").splitlines()]
    return {(r[2], r[3], r[4], r[5]) for r in rows if r[1] == TEACHER and r[2].startswith("account_")}


def test_teacher_creates_and_disables_student(stack, browser, accounts_t):      # Review Focus 1, 2
    with tab(browser, True) as t, tab(browser, True) as s:
        login_teacher(t, TEACHER, accounts_t)
        assert running(stack) is False                                          # место 04 свободно и спит
        t.type("#acc-login", LOGIN); t.select("#acc-place", NN); t.click("button.acc-add")
        pw = Secret(t.wait(lambda: t.eval(PASSWORD_JS), 30, "пароль в окне .password-once"))
        t.wait_text("Пароль показан один раз — запишите его и передайте студенту", 10, css=".password-once")
        t.wait_text(rf"Учётка {re.escape(LOGIN)}, место {NN}", 10, css=".password-once")
        t.click("button.acc-done")
        t.wait(lambda: t.eval("document.querySelector('.password-once') === null"), 10, "окно пароля закрыто")
        assert t.eval("document.body.innerText.length") > 0 and not on_page(t, pw)   # пароля на странице нет
        assert account_state(stack, LOGIN) == "активна"
        login_to_chat(s, LOGIN, pw, ack_notice=True)                              # вход выданным паролем, место 04
        assert running(stack) is True                                             # вход разбудил место 04
        t.wait_selector(f"{ROW} button.acc-disable", 30)                          # m-8: обзор заново — строка уже есть
        t.click(f"{ROW} button.acc-disable")
        t.wait_text(rf"Отключить учётку {re.escape(LOGIN)}\? Студент не сможет продолжить: чат закроется не позже его"
                    r" следующего действия", 10, css=".acc-confirm")                   # итоговое ревью Д10б, m-1
        t.click_text("Да, отключить")
        t.wait_text(rf"Учётка {re.escape(LOGIN)} отключена", 30, css=".tch-accounts")   # ответ 204 ручки
        t.wait_selector(f"{ROW} button.acc-enable", 30)                           # обзор заново: учётка отключена
        assert account_state(stack, LOGIN) == "отключена"
        # студент пишет дальше: чат без разговора потока /event не держит и сам шлюз не спрашивает — его следующий же
        # вызов получает 401, и чат уходит на вход; в место запрос не доходит
        s.type("textarea", "вопрос после отключения"); s.press_enter("textarea")
        s.wait_url("/login", 60)                                                  # сессия закрыта — чат уходит на вход
        assert {r["status"] for r in s.requests("/pcbk/api/oc/") if r["method"] == "POST"} == {401}
        t.eval(f"document.querySelector('{ROW}').scrollIntoView({{block: 'start'}}), 1")
        shot(t, "d10b-01-accounts.png")                                           # снимок — после закрытия окна пароля
        assert t.csp() == []
        assert [e.replace(str(pw), "<скрыт>") for e in t.console_errors()] == []   # текст ошибок — без пароля
    code = stack.gw_client().call("POST", "/pcbk/api/auth/login", {"login": LOGIN, "password": pw})[0]
    assert code == 401                                                            # тот же пароль больше не входит
    assert in_core_log(stack, f"gw login refused login={LOGIN} reason=disabled")
    assert account_journal(stack) == {("account_add", NN, LOGIN, "ok"), ("account_disable", NN, LOGIN, "ok")}
    assert (in_core_log(stack, pw), in_teacher_journal(stack, pw)) == (False, False)   # пароля нет в журналах
    code, body = stack.oc(PLACE, "GET", "/session", PLACE)
    assert (code, json.loads(body)) == (200, [])                                  # разговоров у пробы нет


def test_teacher_calc_is_teacher_caller(stack, accounts_t):                 # Д10-R109; Review Focus 3
    before = stack.data_event_count("teacher", "web")
    place_before = stack.data_event_count(f"student-{NN}", "web")
    c = stack.gw_client(); c.login(TEACHER, accounts_t)
    status, _, _ = c.call("POST", "/pcbk/api/calc/catalog_search", {"query": "тест", "place": NN})
    assert status == 200 and stack.data_event_count("teacher", "web") == before + 1
    assert stack.data_events("teacher", "catalog_search", 1)[0]["channel"] == "web"
    assert stack.data_event_count(f"student-{NN}", "web") == place_before            # место из тела не действует
