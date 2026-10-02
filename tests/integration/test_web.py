"""Чат в браузере (Д6а, Д6б): вход ведёт в чат, страница чата под CSP в настоящем Chrome (cdp.py); ответ через шлюз, поток
по HTTP/2, сон места и конец сессии, ошибка модели, преподаватель без чата, баннер и строка «Веб-чат» сторожа. Д6б:
предупреждение при первом входе (место не будится до «Понятно»), список разговоров (имя OpenCode, переименование и архив
через шлюз), четвёртая вкладка («закройте лишние»), повторы модели (живая проверка m3 Б3), отчёты браузеров в строке.
Д7б (Б4): панель «Агенты», первый работающий чат на двойнике (Конструктор сохраняет агента — агент в панели — ответ
агента), разговор Конструктора после перезагрузки страницы (Review Focus 1 Д7б), сломанный свой агент — «не загрузился».

Место модуля — 09: модули Д5 берут 1, 3–8, прежние дни — 01, 02, 03 и 10. Модуль идёт последним по алфавиту и
гасит своё место к концу, в том числе в finally фикстуры web_users. Учётки: web-a (студент, место 9) и web-t
(преподаватель, своя вкладка с отдельными cookie — isolated_page). Вкладка — на тест (фикстура page): шлюз держит не
больше трёх потоков на логин и трёх живых сессий на учётку, а вкладки одного профиля делят cookie. Поток /event есть
только у открытого разговора (Д6-R43), поэтому тесты потока сначала задают вопрос двойнику OpenRouter.

Порядок модуля (Д6б):
1. test_first_login_notice — первым из тестов стенда: web-a ещё не подтверждала предупреждение. Вкладка — со своими
   cookie (isolated_page, preflight Ф3): иначе следующий тест вошёл бы без формы;
2. тесты Д6а и повторы модели; прочие тесты перед входом подтверждают предупреждение мимо шлюза — ensure_notice_acked
   (Ф5: без пароля и без новой сессии, она вытеснила бы сессию вкладок);
3. список разговоров и четвёртая вкладка;
4. Д7б: панель агентов и первый работающий чат, перезагрузка, сломанный агент. Агенты места 09 — web-proverka (его
   сохраняет Конструктор, остаётся до конца прогона) и web-slomannyj (в архив в finally своего теста). Учётку web-b
   Б4 не заводит (preflight П13: свободных мест на стенде нет) — чужие агенты сверяет test_agents;
5. учения Д6а (баннер, строка «Веб-чат»): строка ждёт точную тройку ok — ложный отчёт любого теста выше её ловит;
6. test_reports_reach_row — последним: после него строка «Веб-чат» жёлтая час.

Снимки для владельца (preflight M17): при PCBK_SHOTS=<каталог> тесты кладут туда снимки в своих точках — к концу
прогона стенд уже опущен, снимать потом не на чем: Д6а — 01-chat-answer, 02-banner-unknown, 03-place-asleep,
04-status-web-row; Д6б — 01-notice, 02-web-row-warn, 03-sidebar, 04-fourth-tab; Д7б — 02-agents-panel,
03-konstruktor-saved, 04-own-agent-answer. Данные на снимках — тестовые (тег 20FAKE_…, учётки web-a и web-t).
"""
import html
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import pytest

import cdp
import conftest
from conftest import REPO, STAND_ORIGIN, Secret, failure, leaks, system_text, tool_names_of, turn_bodies, wait_for
from fake_openrouter import save_args                                   # core/tests на пути импорта (conftest, П7)
from gateway_cases import ALLOWED, OC, SES_FAKE, TWINS

STAND = "https://stand.pcbk.test:18443"
assert STAND == STAND_ORIGIN                    # Origin шлюза тестового стенда (GW_ORIGIN) — тот же адрес
PLACE = 9
# предупреждение «учебный стенд» — текст .stand страницы входа: читается оттуда, а не копируется
STAND_WARNING = html.unescape(re.search(r'<div class="stand" role="note">(.*?)</div>',
                                        (REPO / "edge/static/login.html").read_text(encoding="utf-8"), re.S)[1])
Q = "что сейчас по тегу 20FAKE_001_PV?"
ANSWER = r"Готово: .+"                          # ответ двойника OpenRouter: «Готово: » и начало вывода инструмента
TOOL_DONE = r"Запрос к данным: текущее значение — готово"
ANSWER_HEAD = 40                                # столько знаков вывода инструмента сверяется с ответом
# сообщения агента в разговоре (ChatView: один узел на ход — адаптер склеивает шаги хода) и идущий ход («Стоп»)
ASSISTANT_TEXTS_JS = "[...document.querySelectorAll('.msg[data-role=\"assistant\"]')].map((e) => e.innerText)"
RUNNING_JS = "!!document.querySelector('.composer .stop')"
# один круг переподключения адаптера: поток /event и пять GET разговора (сам разговор, сообщения, session/status,
# permission, question), все — разом. Остановленная обёртка дальше в сеть не ходит, так что отказов (401, 503) — не больше
# одного круга, иначе это шторм (Review Focus 2)
RECONNECT_ROUND = 6
# что веб хранит в браузере: [записей localStorage и sessionStorage, баз IndexedDB, document.cookie]; проба кладёт
# по записи в оба хранилища, базу и cookie без HttpOnly, уборка их снимает
STORED_JS = ("(async () => [localStorage.length + sessionStorage.length, (await indexedDB.databases()).length, "
             "document.cookie])()")
STORE_PROBE_JS = ("(async () => { localStorage.setItem('pcbk-probe', '1'); sessionStorage.setItem('pcbk-probe', '1'); "
                  "document.cookie = 'pcbk-probe=1; path=/chat/; SameSite=Strict'; await new Promise((ok, no) => { "
                  "const r = indexedDB.open('pcbk-probe'); r.onsuccess = () => { r.result.close(); ok(1); }; "
                  "r.onerror = () => no(r.error); }); return 1; })()")
STORE_CLEAN_JS = ("(async () => { localStorage.removeItem('pcbk-probe'); sessionStorage.removeItem('pcbk-probe'); "
                  "document.cookie = 'pcbk-probe=; path=/chat/; max-age=0'; await new Promise((ok, no) => { "
                  "const r = indexedDB.deleteDatabase('pcbk-probe'); r.onsuccess = () => ok(1); "
                  "r.onerror = () => no(r.error); }); return 1; })()")
# форма входа видна: /me ответил 401 (без сессии); при живой сессии форма так и остаётся скрытой
LOGIN_FORM_JS = "(() => { const e = document.getElementById('login'); return !!e && e.getClientRects().length > 0; })()"
# строка «Веб-чат» страницы состояния — в середину экрана (для снимка 04)
WEB_ROW_INTO_VIEW_JS = ("[...document.querySelectorAll('body *')].find((e) => e.children.length === 0 "
                        "&& e.textContent.trim() === 'Веб-чат').scrollIntoView({block: 'center'}), 1")
EVENT = OC + "/event"                           # поток событий вкладки (только у открытого разговора)
# предупреждение при первом входе (Д6-R49, текст — web/src/texts.ts)
NOTICE_RE = r"Преподаватель видит ваши разговоры"
DEFAULT_TITLE = "New session - "               # имя разговора OpenCode до самозаголовка
TOO_MANY_TABS = r"Открыто слишком много вкладок чата — закройте лишние"
# подтверждение предупреждения мимо шлюза (preflight Ф5): без пароля и без новой сессии — вход вытеснил бы сессию вкладок
# (живых сессий у учётки не больше трёх), а пароль web-a меняет test_signed_out_goes_to_login. Путь через шлюз
# проверяют test_first_login_notice и test_notice_ack_rules (Б2). Та же база и тот же open(), что у команды
# администратора; argv — логин
NOTICE_ACK_PY = """\
import sys
from pcbk_core.gateway.accounts import NOTICE, AccountStore
from pcbk_core.settings import Settings
store = AccountStore(Settings.from_env().DB_PATH)
if not store.open():
    sys.exit("база учётных записей не открывается")
store.ack_notice(sys.argv[1], NOTICE)
"""
NO_REPORTS = {"boot_failed_1h": 0, "ui_crash_1h": 0, "turn_silent_1h": 0, "stream_down_1h": 0}
PROMPT_PATH = re.compile(rf"{re.escape(OC)}/session/(ses_[0-9A-Za-z]{{26}})/prompt_async")
# сверка путей браузера с правилами шлюза в образе core (preflight F1: gateway_cases.py — без импорта pcbk_core, на хосте
# нет fastapi): argv — пары [метод, путь после /pcbk/api/oc без «?…»], ответ — имя правила или null на каждую
RULES_PY = """\
import json, sys
from pcbk_core.gateway.rules import Refused, match
names = []
for method, path in json.loads(sys.argv[1]):
    try:
        names.append(match(method, path)[0].name)
    except Refused:
        names.append(None)
print(json.dumps(names))
"""
# контроль сверки: 15 разрешённых пар — правило есть; нет — у опасной ручки (GET /config: ключи провайдеров), удаления
# разговора и «близнеца» каждого разрешённого пути (первый чужой метод на этом пути)
RULES_CONTROL_OK = [[m, p[len(OC):]] for m, p in ALLOWED]
RULES_CONTROL_NO = [["GET", "/config"], ["DELETE", f"/session/{SES_FAKE}"],
                    *({p: [m, p[len(OC):]] for m, p in reversed(TWINS)}.values())]
# --- Д7б: панель «Агенты» и агент разговора (Д7-R64, R65) ---
BUILTIN_TITLES = ["Помощник по данным", "Конструктор агентов"]       # встроенные агенты — первыми, в порядке R64
MY_AGENTS, NO_AGENTS = "Мои агенты", "Пока нет — соберите агента вместе с Конструктором"
AGENT_PENDING, AGENT_BROKEN = "появится после ответа Конструктора", "не загрузился — сообщите преподавателю"
OWN_AGENT = "web-proverka"                      # его сохраняет Конструктор на двойнике (FAKESAVE agent=…)
OWN_TITLE = save_args(OWN_AGENT)["title"]       # «Проверка Д7» — черновик двойника (save_args)
OWN_TOOLS = {"pcbk_catalog_search", "pcbk_tag_now"}                  # инструменты черновика двойника
BROKEN_AGENT = "web-slomannyj"                  # агент, чей файл портится мимо службы (not_visible → broken)
# файл агента, который место 1.18.33 при загрузке пропускает молча — строка учёта становится not_visible. Frontmatter не
# разбирается ни как есть, ни после sanitize OpenCode (описание с «: » sanitize переписывает в блок, незакрытая кавычка
# остаётся): ConfigAgent.load ловит ошибку и идёт дальше. Пропуск — только при первой загрузке этого текста в процессе
# OpenCode: gray-matter кладёт текст в кэш до разбора, и следующая загрузка (новый dispose) берёт его из кэша без
# ошибки — агент с пустым frontmatter. Текст, который sanitize не меняет, загружается уже первой загрузкой: второй
# разбор того же текста — из кэша (прогон sub1 Б4)
BROKEN_YAML = '---\ndescription: a: b\nmode: "x\n---\nx\n'
# первая строка промпта Конструктора в образе (Б1; preflight Д7б, п. 12); в его ответе ok — панель «Мои агенты» (R63)
KONSTRUKTOR_MD = REPO / "student/config/agent/konstruktor.md"
KONSTRUKTOR_HEAD = "Ты — Конструктор агентов учебного стенда ПЦБК"
# кнопка агента панели: [data-state, disabled, текст DOM] или null — договор разметки Б3 ↔ Б4 (preflight П12)
AGENT_ROW_JS = ("(() => { const e = document.querySelector(%s); "
                "return e ? [e.dataset.state, e.disabled, e.textContent.trim()] : null; })()")
# состояние агента в ответе ручки — fetch страницы чата (та же cookie, через edge и шлюз): [код, state или null]
AGENT_API_STATE_JS = ("fetch('/pcbk/api/agents').then(async (r) => { "
                      "const b = r.status === 200 ? await r.json() : null; "
                      "const a = b && b.agents.find((x) => x.name === %s); return [r.status, a ? a.state : null]; })")
FOCUS_JS = "document.activeElement === document.querySelector('textarea')"   # поле ввода в фокусе (m2 ревью Б3)
# m2 ревью Б3 — что было с полем ввода на первой отправке: смены пары [закрыто, в фокусе] по изменениям DOM, до 50
FIELD_PROBE_JS = """(() => {
  const seen = [];
  const look = () => {
    const f = document.querySelector('textarea');
    const now = [!!f && f.disabled, !!f && document.activeElement === f];
    const last = seen[seen.length - 1];
    if ((!last || last[0] !== now[0] || last[1] !== now[1]) && seen.length < 50) seen.push(now);
  };
  look();
  new MutationObserver(look).observe(document.body,
    { subtree: true, childList: true, attributes: true, characterData: true });
  globalThis.__pcbkFieldProbe = seen;
  return 1;
})()"""
# панель «Агенты» при длинном списке разговоров (итоговая доводка Д7б, замечание Б4): в список разговоров на время
# замера — вставка высотой 4000 px (стиль — через CSSOM, CSP его не запрещает; потом убирается). Ответ — [высота панели
# до, после, прокрутка панели после (scrollHeight − clientHeight), переполнен ли список разговоров]: панель не сжимается
# и видна целиком, прокручивается список
PANEL_SQUEEZE_JS = """(() => {
  const panel = document.querySelector('.agents'), items = document.querySelector('.thread-items');
  if (!panel || !items) return null;
  const before = panel.getBoundingClientRect().height;
  const filler = document.createElement('div');
  filler.style.height = '4000px';
  filler.style.flexShrink = '0';                  // строки списка не сжимаются ниже своего текста
  items.appendChild(filler);
  const after = panel.getBoundingClientRect().height, hidden = panel.scrollHeight - panel.clientHeight;
  const listScrolls = items.scrollHeight > items.clientHeight + 1000;
  filler.remove();
  return [before, after, hidden, listScrolls];
})()"""
# M10 ревью Б3 живьём (useLayoutEffect у ThreadAgentWatch): с этого момента каждое изменение DOM (MutationObserver —
# после коммита React, до отрисовки) и каждый кадр (requestAnimationFrame), пока открыт разговор sid, сверяют: поле
# ввода открыто только вместе со строкой «Агент: Конструктор агентов». С useEffect агент разговора доходил бы до Chat
# после отрисовки — был бы коммит, где разговор уже открыт, а поле открыто ходу помощника (выбор панели по умолчанию).
# Счётчики: open — проверок с открытым разговором sid, locked — из них с закрытым полем, bad — строки при открытом поле
OPEN_PROBE_JS = """(() => {
  const want = %s, line = %s;
  const probe = { checks: 0, open: 0, locked: 0, bad: [] };
  const look = () => {
    probe.checks += 1;
    const row = document.querySelector('.thread-item[data-active="true"]');
    if (!row || row.dataset.sessionId !== want) return;
    probe.open += 1;
    const field = document.querySelector('textarea');
    if (!field || field.disabled) { probe.locked += 1; return; }
    const shown = document.querySelector('.agent-line');
    const text = shown ? shown.innerText.trim() : '';
    if (text !== line && probe.bad.length < 20) probe.bad.push(text || '(строки нет)');
  };
  new MutationObserver(look).observe(document.body,
    { subtree: true, childList: true, attributes: true, characterData: true });
  const frame = () => { look(); requestAnimationFrame(frame); };
  requestAnimationFrame(frame);
  globalThis.__pcbkOpenProbe = probe;
  return 1;
})()"""


def new_passwords() -> dict[str, Secret]:
    """Пароли учёток модуля — Secret: pytest печатает аргументы кадров (фикстуру теста, password входа, text ввода)
    через repr, а у Secret он — «<скрыт>»."""
    return {"web-a": Secret(secrets.token_urlsafe(12)), "web-t": Secret(secrets.token_urlsafe(12))}


@pytest.fixture(scope="module")
def web_users(stack):
    pw = new_passwords()
    try:
        stack.add_account("web-a", "student", PLACE, pw["web-a"])
        stack.add_account("web-t", "teacher", None, pw["web-t"])
        yield pw                                 # test_signed_out_goes_to_login меняет пароль web-a здесь же
    finally:                                     # место модуля — в сон, даже при сбое
        name = f"pcbk-student-{PLACE:02d}"
        if stack.inspect(name)["State"]["Running"]:
            stack.stop(name)


def login_to_chat(page, login, password) -> str:
    """Вход на /login; «Рабочее место готово» ведёт в чат сама страница входа. «Вы вошли как» не годится признаком
    чата: этот текст есть и на странице входа, и в шапке чата на экранах места и предупреждения (раунд 2, Minor 7) —
    он сверяет только учётку, когда поле ввода чата уже видно.

    Сессия этой вкладки уже есть (cookie вкладок одного контекста общие, браузер — на модуль) — страница входа сама
    будит место и ведёт в чат, формы нет: второй вход не нужен. Каждый вход — новая сессия, а их у учётки не больше
    трёх (новая вытесняет старую), так что тесты входят как можно реже. Возвращает "form" (вошли формой) или "chat"
    (сессия уже была)."""
    page.goto(STAND + "/login")
    how = page.wait(lambda: "chat" if page.url().split("?")[0].endswith("/chat/")
                    else "form" if page.eval(LOGIN_FORM_JS) else None, 90, "форма входа или переход в чат")
    if how == "form":
        page.type("#login", login)
        page.type("#password", password)
        page.press_enter("#password")
    page.wait_url("/chat/", 90)
    page.wait_selector("textarea", 90)
    page.wait_text(rf"Вы вошли как {re.escape(login)}\b", 10)
    return how


def ask(page, text: str, timeout: float = 120) -> str:
    """Вопрос в поле ввода и Enter; ждёт новое сообщение агента — их стало на одно больше, чем до отправки (preflight
    F4: иначе второй вопрос вернул бы уже показанный ответ), в нём «Готово: …», и ход кончился. Текст этого сообщения
    (innerText: строка вызова инструмента и ответ; свёрнутый вывод под строкой в него не входит)."""
    before = len(page.eval(ASSISTANT_TEXTS_JS))
    page.type("textarea", text)
    page.press_enter("textarea")

    def answered():
        texts = page.eval(ASSISTANT_TEXTS_JS)
        if len(texts) == before + 1 and re.search(ANSWER, texts[-1]) and not page.eval(RUNNING_JS):
            return texts[-1]
        return None
    return page.wait(answered, timeout, f"новое сообщение агента с /{ANSWER}/ (до вопроса их было {before})")


def norm(s: str) -> str:
    """Пробелы схлопнуты (preflight F2): FastMCP пишет вывод JSON с отступами, markdown в браузере склеивает строки."""
    return " ".join(s.split())


def page_sessions(page) -> list[str]:
    """Разговоры, в которые вкладка отправляла вопросы (prompt_async), по порядку отправки."""
    return [m[1] for r in page.requests(OC + "/session/") if (m := PROMPT_PATH.fullmatch(r["path"]))]


def last_session(stack, n: int) -> str:
    """Разговор места n, изменённый последним, — из сети места мимо шлюза (истина о месте)."""
    code, body = stack.oc(n, "GET", "/session", n)
    assert code == 200, code
    return max(json.loads(body), key=lambda s: s["time"]["updated"])["id"]


def tool_output(stack, n: int, sid: str, tool: str) -> str:
    """Вывод последнего завершённого вызова инструмента tool в разговоре sid места n — из места мимо шлюза."""
    code, body = stack.oc(n, "GET", f"/session/{sid}/message", n)
    assert code == 200, code
    outs = [p["state"]["output"] for m in json.loads(body) for p in m["parts"]
            if p.get("type") == "tool" and p.get("tool") == tool and p["state"].get("status") == "completed"]
    assert outs, f"в разговоре нет завершённого вызова {tool}"
    assert len(norm(outs[-1])) >= ANSWER_HEAD, len(norm(outs[-1]))                       # иначе сверка с ответом пуста
    return outs[-1]


def not_allowed(stack, calls: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Пары (метод, путь без «?…») запросов вкладки под /pcbk/api/oc/, которым нет правила белого списка шлюза, —
    одной сверкой в образе core (rules.match, те же 15 правил; путь без /pcbk/api/oc). Контроль в той же сверке:
    разрешённые пары проходят, «близнецы» — нет, иначе пустой ответ ничего бы не значил. На входе — пары, а не записи
    запросов (итоговое ревью Д6а, P6)."""
    pairs = [[m, p[len(OC):]] for m, p in calls]
    assert pairs, "вкладка не ходила в /pcbk/api/oc/ — сверять нечего"
    names = json.loads(stack._docker("exec", "pcbk-core", "python", "-c", RULES_PY,
                                     json.dumps(pairs + RULES_CONTROL_OK + RULES_CONTROL_NO)).stdout)
    ok, no = names[len(pairs):len(pairs) + len(RULES_CONTROL_OK)], names[len(pairs) + len(RULES_CONTROL_OK):]
    assert all(ok) and len(set(ok)) == 15 and not any(no), (ok, no)
    return [(m, p) for (m, p), name in zip(pairs, names) if name is None]


def refusals(page) -> list[tuple[str, int]]:
    """Ответы-отказы (4xx, 5xx) на запросы вкладки под /pcbk/api/oc/: путь и код."""
    return [(r["path"], r["status"]) for r in page.requests(OC + "/") if (r["status"] or 0) >= 400]


def row(d: dict, component: str) -> tuple[str, str, str]:
    return next((c["state"], c["detail"], c.get("note", "")) for c in d["checks"] if c["component"] == component)


def noticed(stack, login: str) -> str:
    """Столбец «предупреждён» строки accounts list — целиком (preflight Н3): да, нет или «-» у преподавателя."""
    return stack.accounts_list_row(login).split("\t")[-1]


def ensure_notice_acked(stack, login: str) -> None:
    """Предупреждение login подтверждено — мимо шлюза, если ещё нет; после — сверка строки accounts list."""
    if noticed(stack, login) == "нет":
        stack.exec("pcbk-core", "python", "-c", NOTICE_ACK_PY, login)
    assert noticed(stack, login) == "да"


def session_of(stack, n: int, sid: str) -> dict:
    """Разговор sid места n (GET /session/<sid>) — из сети места мимо шлюза (истина о месте)."""
    code, body = stack.oc(n, "GET", f"/session/{sid}", n)
    assert code == 200, code
    return json.loads(body)


def web_reports(stack) -> dict:
    """/health/web серверного слоя изнутри сторожа (pcbk-front): счётчики отчётов браузеров за час."""
    return json.loads(stack.body_from_watchdog("http://pcbk-core:8000/health/web"))


def shot(page, name: str) -> None:
    """Снимок вкладки в PCBK_SHOTS (снимки для владельца, M17); без переменной — ничего."""
    where = os.environ.get("PCBK_SHOTS")
    if where:
        page.screenshot(Path(where) / name)


def open_chat(stack, page, password) -> None:
    """Чат web-a (предупреждение подтверждено мимо шлюза) с загруженной панелью «Агенты»: встроенные агенты — первыми,
    в порядке Д7-R64. До ответа ручки в панели только заголовок."""
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", password)
    page.wait(lambda: page.texts(".agents .agent-title")[:2] == BUILTIN_TITLES, 30, "встроенные агенты в панели")


def agent_row(page, name: str) -> list | None:
    """Кнопка агента name в панели: [data-state, disabled, текст DOM]; нет кнопки — None."""
    return page.eval(AGENT_ROW_JS % json.dumps(f'.agents button[data-agent="{name}"]'))


def user_agents(stack, n: int, sid: str) -> list[str | None]:
    """info.agent сообщений студента разговора sid места n по порядку — из места мимо шлюза: кому ушёл каждый ход."""
    code, body = stack.oc(n, "GET", f"/session/{sid}/message", n)
    assert code == 200, code
    return [m["info"].get("agent") for m in json.loads(body) if m["info"]["role"] == "user"]


def place_agent_state(stack, n: int, name: str) -> str | None:
    """Состояние строки учёта агента name места n по `admin list --place n` (столбцы через табуляцию)."""
    rows = [line.split("\t") for line in stack.agents_admin("list", "--place", str(n)).splitlines()]
    return next((r[3] for r in rows if len(r) == 6 and r[1] == name), None)


class _BrokenSocket:
    """Сокет браузера, на котором отправка рвётся: отказ CDP посреди ввода пароля. recv ждёт закрытия."""

    def __init__(self):
        self._closed = threading.Event()

    def sendall(self, data: bytes) -> None:
        raise BrokenPipeError("браузер закрыл соединение")

    def recv(self, n: int) -> bytes:
        self._closed.wait(5)
        return b""

    def shutdown(self, how: int) -> None:
        self._closed.set()

    def close(self) -> None:
        self._closed.set()


def test_passwords_hidden_in_tracebacks_run(monkeypatch):                               # без стенда: ревью А7, I1
    probe = Secret("pcbk-probe-" + secrets.token_hex(8))
    assert leaks(failure(conftest._run, ["false"], input=str(probe) + "\n"), probe)       # контроль: открытый виден
    assert not leaks(failure(conftest._run, ["false"], input=Secret(probe + "\n")), probe)
    assert {type(v) for v in new_passwords().values()} == {Secret}                        # пароли фикстуры
    calls = []                                                                            # пароли — Secret в stdin
    monkeypatch.setattr(conftest, "_run", lambda cmd, **kw: calls.append(kw))
    stack = conftest.Stack.__new__(conftest.Stack)
    stack.add_account("x", "student", PLACE, probe)
    new = stack.passwd("x")
    assert [type(kw["input"]) for kw in calls] == [Secret, Secret] and type(new) is Secret
    assert [str(kw["input"]) for kw in calls] == [probe + "\n", new + "\n"]


def test_passwords_hidden_in_tracebacks_cdp():                                            # без стенда: ревью А7, I1
    probe = Secret("pcbk-probe-" + secrets.token_hex(8))
    ws = cdp._WebSocket.__new__(cdp._WebSocket)          # настоящие send/_frame, сокет — рвущийся
    ws._sock, ws._buf, ws._send_lock = _BrokenSocket(), bytearray(), threading.Lock()
    conn = cdp._Connection(ws)
    try:
        page = cdp.Page.__new__(cdp.Page)
        page._conn, page.session_id = conn, "probe-session"
        page._focus = lambda selector, timeout: None     # фокус — не предмет проверки
        open_exc = failure(page.type, "#password", str(probe))
        assert isinstance(open_exc, cdp.CDPError) and leaks(open_exc, probe)              # контроль: открытый виден
        exc = failure(page.type, "#password", probe)
        assert isinstance(exc, cdp.CDPError) and isinstance(exc.__cause__, OSError)       # цепочка до кадров провода
        assert not leaks(exc, probe)
    finally:
        conn.close()
    assert json.loads(json.dumps({"text": probe})) == {"text": str(probe)}               # в браузер уходит значение


def test_cookie_hidden_in_tracebacks():                                                  # без стенда: итоговое ревью Д6а, P6
    cookie = Secret("pcbk_session=" + secrets.token_hex(16))
    page = cdp.Page.__new__(cdp.Page)
    page._lock = threading.Lock()
    page._order = [{"method": "GET", "url": STAND + OC + "/event", "path": OC + "/event", "status": 200, "protocol": "h2",
                    "headers": {"cookie": str(cookie), "origin": STAND}, "chunks": 3}]

    def check(reqs):
        raise AssertionError("сбой проверки запросов")
    assert leaks(failure(check, page._order), cookie)                                     # контроль: сырая запись видна
    plain = page.requests(OC + "/")
    assert "headers" not in plain[0] and not leaks(failure(check, plain), cookie)          # по умолчанию — без заголовков
    full = page.requests(OC + "/", headers=True)
    assert full[0]["headers"] == {"cookie": str(cookie), "origin": STAND}                 # значения сравниваются как есть
    assert not leaks(failure(check, full), cookie)                                         # cookie — «<скрыт>»


def test_first_login_notice(stack, isolated_page, web_users):                          # Review Focus 1 Д6б
    """Первый вход web-a: экран предупреждения в чате, ни вход, ни чат не будят место и не ходят в /pcbk/api/oc/, пока
    нет «Понятно»; подтверждение — POST через edge и шлюз (204), после него чат и «да» в accounts list. Вкладка — со
    своими cookie (preflight Ф3): сессия этого входа не достаётся следующему тесту, тот входит формой."""
    page = isolated_page
    assert noticed(stack, "web-a") == "нет"                                               # отрицательный контроль (Н3)
    page.goto(f"{STAND}/login")
    page.type("#login", "web-a"); page.type("#password", web_users["web-a"]); page.press_enter("#password")
    page.wait_url("/chat/", 60); page.wait_text(NOTICE_RE, 30)
    assert [r for r in page.requests("/pcbk/api/workplace") if r["method"] == "POST"] == []   # ни вход, ни чат не будили
    assert page.requests("/pcbk/api/oc/") == []
    assert page.requests("/pcbk/api/auth/notice") == [] and noticed(stack, "web-a") == "нет"
    shot(page, "01-notice.png")
    page.click_text("Понятно"); page.wait_gone(NOTICE_RE, 30)
    page.wait_selector("textarea", 90)                                                    # поле чата, не шапка (Minor 7)
    assert [(r["method"], r["status"]) for r in page.requests("/pcbk/api/auth/notice")] == [("POST", 204)]
    page.wait(lambda: page.requests(OC + "/"), 10, "запросы чата к месту")               # список разговоров — после поля
    seq = page.requests("/pcbk/api/")                                                     # все записи вкладки по порядку
    ack = next(i for i, r in enumerate(seq) if r["path"] == "/pcbk/api/auth/notice")
    wake = [i for i, r in enumerate(seq) if r["method"] == "POST" and r["path"].startswith("/pcbk/api/workplace")]
    oc = [i for i, r in enumerate(seq) if r["path"].startswith(OC + "/")]
    assert oc and all(i > ack for i in wake + oc), (ack, wake, oc)                        # место и чат — после «Понятно»
    assert noticed(stack, "web-a") == "да"
    assert page.csp() == [] and page.console_errors() == []


def test_login_leads_to_chat(stack, page, web_users):                                  # Review Focus 1
    ensure_notice_acked(stack, "web-a")
    assert login_to_chat(page, "web-a", web_users["web-a"]) == "form"                    # первый тест: вход формой
    assert page.wait_text(re.escape(STAND_WARNING), 5) and page.wait_text(r"Стенд работает|Внимание|Сбой", 20)
    assert page.wait_text(r"Вы вошли как web-a", 5)
    assert page.csp() == [] and page.console_errors() == []
    assets = [(r["path"], r["status"]) for r in page.requests("/chat/assets/")]
    assert assets and all(status == 200 for _, status in assets), assets
    assert [r["status"] for r in page.requests("/chat/") if r["path"] == "/chat/"] == [200]
    # положительный контроль наблюдателей (доводка 1, I1): без него тихо сломанный слушатель CSP или разбор консоли
    # (обновление Chrome, правка cdp.py) оставил бы проверки выше зелёными навсегда. Пробы — после них
    page.eval("fetch('https://example.invalid/').catch(() => 0), 1")               # connect-src 'self' — отказ
    page.wait(lambda: page.csp(), 5, "отчёт CSP о пробном нарушении")
    assert [v["effective"] for v in page.csp()] == ["connect-src"], page.csp()
    assert page.console_errors() == []
    page.eval("console.error('pcbk-probe'), 1")
    page.wait(lambda: page.console_errors(), 5, "console.error пробы")
    assert page.console_errors() == ["pcbk-probe"]


def test_chat_answers_through_gateway(stack, page, web_users):                            # Review Focus 1, 4
    ensure_notice_acked(stack, "web-a")
    before = stack.gw_health()["refused_1h"]
    login_to_chat(page, "web-a", web_users["web-a"])
    answer = ask(page, Q)
    assert page.wait_text(TOOL_DONE, 5)
    sid = last_session(stack, PLACE)
    assert page_sessions(page) == [sid]                                                    # вопрос ушёл в этот разговор
    tool = tool_output(stack, PLACE, sid, "pcbk_tag_now")                                   # вывод инструмента из места
    assert norm(tool)[:ANSWER_HEAD] in norm(answer), (norm(tool)[:ANSWER_HEAD], norm(answer))  # ответ построен на выводе
    shot(page, "01-chat-answer.png")
    assert page.csp() == [] and page.console_errors() == []
    assert stack.gw_health()["refused_1h"] == before
    bad = not_allowed(stack, [(r["method"], r["path"]) for r in page.requests("/pcbk/api/oc/")])
    assert bad == [], bad                                                                  # те же 15 правил
    # веб ничего не хранит (Global Constraints Д6): ни localStorage, ни sessionStorage, ни IndexedDB; cookie сессии —
    # HttpOnly, скрипту её не видно (итоговое ревью Д6а, P5)
    assert page.eval(STORED_JS) == [0, 0, ""]
    page.eval(STORE_PROBE_JS)                                                              # положительный контроль
    try:
        assert page.eval(STORED_JS) == [2, 1, "pcbk-probe=1"]
    finally:
        page.eval(STORE_CLEAN_JS)
    assert page.eval(STORED_JS) == [0, 0, ""]


def test_browser_headers_and_h2_stream(stack, page, web_users):                           # хвост Д5б [?]
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)                         # разговор открыт — поток есть
    posts = [(r["path"], r["headers"].get("sec-fetch-site"), r["headers"].get("origin"))  # заголовки — по запросу (P6)
             for r in page.requests("/pcbk/api/", headers=True) if r["method"] == "POST"]
    assert posts and all(site == "same-origin" for _, site, _ in posts), posts
    assert all(origin == STAND for _, _, origin in posts), posts                            # при Referrer-Policy no-referrer
    ev = [r["protocol"] for r in page.requests("/pcbk/api/oc/event") if r["status"] == 200]
    assert ev and ev[-1] == "h2", ev
    n0 = page.chunks("/pcbk/api/oc/event")
    page.wait(lambda: page.chunks("/pcbk/api/oc/event") - n0 >= 3, 35, "три пульса потока")  # пульс 10 с идёт через edge
    assert page.csp() == []


def test_place_asleep_shows_wake_and_goes_quiet(stack, page, web_users):                  # Review Focus 2
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)
    sid = last_session(stack, PLACE)
    stack.stop("pcbk-student-09")
    page.wait_text(r"Рабочее место спит", 90)
    shot(page, "03-place-asleep.png")
    n = len(page.requests("/pcbk/api/")); time.sleep(30)
    assert [r["path"] for r in page.requests("/pcbk/api/")[n:]] == []                      # остановлен до действия студента
    assert 1 <= len(refusals(page)) <= RECONNECT_ROUND, refusals(page)                     # один круг, не шторм
    page.click_text("Разбудить"); page.wait_gone(r"Рабочее место спит", 90)
    # тот же разговор (initialSessionId): его ответ — среди сообщений агента, а не где угодно на странице — в списке
    # слева самозаголовок двойника «Готово: нет инструмента» тоже совпадает с ANSWER (доводка Б4, I1)
    page.wait(lambda: any(re.search(ANSWER, t) for t in page.eval(ASSISTANT_TEXTS_JS)), 30,
              "ответ восстановленного разговора")
    assert len(page.eval(ASSISTANT_TEXTS_JS)) == 1                                         # история загружена до вопроса
    ask(page, Q)
    assert page_sessions(page) == [sid, sid] and last_session(stack, PLACE) == sid
    # консоль не сверяется: на отказ списка разговоров (сон, 401) адаптер пишет console.error сам — это не ошибка чата
    assert page.csp() == []


def test_signed_out_goes_to_login(stack, page, web_users):                                # Review Focus 2
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)
    assert [r["status"] for r in page.requests("/pcbk/api/oc/event") if r["status"] == 200]  # поток был (M18)
    web_users["web-a"] = stack.passwd("web-a")                                             # passwd закрывает сессии
    page.wait_url("/login", 45)
    # после ухода чата нет (страница входа в /pcbk/api/oc/ не ходит), так что шторм виден только до ухода: 401 — не
    # больше одного круга переподключения (M18, доводка 1: проверка «после /login тихо» убрана как вакуумная)
    unauth = [f for f in refusals(page) if f[1] == 401]
    assert unauth and len(unauth) <= RECONNECT_ROUND, unauth
    assert page.csp() == []                     # консоль не сверяется — как у сна места: console.error самого адаптера


def test_model_error_visible_in_chat(stack, page, web_users):
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", web_users["web-a"])
    page.type("textarea", "FAKE402 что сейчас?"); page.press_enter("textarea")
    page.wait_text(r"На счёте OpenRouter кончились деньги", 90)
    assert page.csp() == [] and page.console_errors() == []


def test_model_retries_recorded(stack, page, web_users, record_property):              # живая проверка m3 ревью Б3
    """m3 ревью Б3: пишет ли место (OpenCode 1.18.33) часть retry на каждую попытку модели. Отпечаток хода веба (watch.ts)
    берёт число частей последнего ответа и его последнюю часть: будь часть retry на попытку — ход, который только
    повторяет запрос к модели, сбрасывал бы отсчёт и turn_silent не дал бы никогда. Двойник отвечает на FAKE500 ошибкой
    500, LLM-прокси отдаёт месту 502, OpenCode повторяет. Во время повторов опрашиваются статус разговора и части
    ответа в месте (мимо шлюза), после трёх попыток ход снимается «Стопом». Факты — свойство m3 отчёта junit.

    Как ведёт себя 1.18.33 (прогоны Б4): повтор виден только статусом разговора (session/status — retry с номером
    попытки), частей у ответа нет ни одной — отпечаток стоит, и молчание дольше 180 с ход с одними повторами ловит.
    Тест закрепляет это: место, начавшее писать части retry, его уронит — тогда отпечаток надо править (m3). Разговор —
    с готовым именем: запрос самозаголовка не смешан с повторами хода; отчётов браузера за это время нет."""
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)
    sid = last_session(stack, PLACE)
    wait_for(lambda: not session_of(stack, PLACE, sid)["title"].startswith(DEFAULT_TITLE), timeout=60)
    first = max((c["id"] for c in stack.llm_calls(1)), default=0)

    def calls() -> list[dict]:                      # вызовы LLM-прокси этого разговора после вопроса (новые — первыми)
        return [c for c in stack.llm_calls(50) if c["session"] == sid and c["id"] > first]

    def attempts() -> int:
        return sum(1 for c in calls() if c["code"] == "pcbk_upstream_down")

    def answer_parts() -> list[dict]:               # части ответов места на последний вопрос
        code, body = stack.oc(PLACE, "GET", f"/session/{sid}/message", PLACE)
        assert code == 200, code
        msgs = json.loads(body)
        asked = max(i for i, m in enumerate(msgs) if m["info"]["role"] == "user")
        return [p for m in msgs[asked + 1:] if m["info"]["role"] == "assistant" for p in m["parts"]]

    def running() -> bool:
        return bool(page.eval(RUNNING_JS))

    seen: list[list] = []                           # во время хода: [вид статуса места, попытка, частей ответа]

    def retried_enough() -> bool:
        code, body = stack.oc(PLACE, "GET", "/session/status", PLACE)
        st = (json.loads(body) if code == 200 else {}).get(sid) or {}
        sample = [st.get("type"), st.get("attempt"), len(answer_parts())]
        if not seen or seen[-1] != sample:
            seen.append(sample)
        n = attempts()
        return (n >= 3 and any(t == "retry" for t, _, _ in seen)) or (n >= 1 and not running())

    reports = web_reports(stack)
    started = time.monotonic()
    page.type("textarea", "FAKE500 что сейчас?"); page.press_enter("textarea")
    wait_for(retried_enough, timeout=150, step=0.5)
    stopped = running()
    if stopped:
        page.click_text("Стоп")
        page.wait(lambda: not running(), 30, "ход снят «Стопом»")
    parts = answer_parts()
    facts = {"attempts": attempts(), "seen": seen, "stopped": stopped, "turn_s": round(time.monotonic() - started, 1),
             "part_types": [p.get("type") for p in parts],
             "calls": [[c["step"], c["status"], c["code"], c["http_status"]] for c in reversed(calls())]}
    record_property("m3", json.dumps(facts, ensure_ascii=False))
    assert facts["attempts"] >= 3 and stopped, facts                                     # повторы были, ход шёл
    assert [t for t, _, _ in seen if t == "retry"], facts                                # повтор виден статусом
    assert all(n == 0 for _, _, n in seen) and "retry" not in facts["part_types"], facts  # частей на попытку нет
    assert web_reports(stack) == reports                                                   # повторы — не «ответа нет»
    assert page.csp() == []


def test_teacher_sees_no_chat(stack, isolated_page, web_users):                           # свои cookie: web-a не задет
    page = isolated_page
    page.goto(f"{STAND}/login")
    page.type("#login", "web-t"); page.type("#password", web_users["web-t"]); page.press_enter("#password")
    page.wait_text(r"У преподавателя нет рабочего места", 30)                              # страница входа, не чат
    page.goto(f"{STAND}/chat/"); page.wait_url("/chat/", 10); page.wait_text(r"У преподавателя пока нет чата", 20)
    assert [r["path"] for r in page.requests("/pcbk/api/oc/")] == []
    assert page.csp() == [] and page.console_errors() == []


def test_rename_and_archive_through_gateway(stack, page, web_users):                    # Review Focus 4 Д6б
    """Список разговоров через шлюз: имя от OpenCode доходит до строки (Ф13), переименование и «В архив» строки по id
    (не «первой», Minor 9) меняют разговор в месте; отказов шлюза нет."""
    ensure_notice_acked(stack, "web-a")
    before = stack.gw_health()["refused_1h"]
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)
    sid = last_session(stack, PLACE)
    row_css = cdp.Page.thread_row(sid)
    # самозаголовок — до ручного
    wait_for(lambda: not session_of(stack, PLACE, sid)["title"].startswith(DEFAULT_TITLE), timeout=60)
    page.wait_text(re.escape(session_of(stack, PLACE, sid)["title"]), 30, css=row_css)   # Ф13: имя OpenCode в строке
    page.rename_thread(sid, "Проверка Д6б")
    wait_for(lambda: session_of(stack, PLACE, sid)["title"] == "Проверка Д6б", timeout=20)
    page.wait_text(r"Проверка Д6б", 10, css=row_css)
    shot(page, "03-sidebar.png")
    page.click_text_in(row_css, "В архив")
    wait_for(lambda: session_of(stack, PLACE, sid).get("time", {}).get("archived") is not None, timeout=20)
    page.wait_gone(r"Проверка Д6б", 10)
    assert stack.gw_health()["refused_1h"] == before
    assert page.csp() == [] and page.console_errors() == []


def test_fourth_tab_told_to_close(stack, browser, page, web_users):                    # хвост Д5б, 429 streams; Б-2
    """Четыре вкладки одной учётки в одном разговоре: поток — у трёх, четвёртой — «закройте лишние» (и только ей);
    закрыли одну — следующая попытка четвёртой по паузе проходит. Вкладки сверх фикстуры page закрываются в finally
    (preflight Ф4): их потоки не достаются следующему тесту."""
    ensure_notice_acked(stack, "web-a")
    pages = [page]
    try:
        for _ in range(3):
            pages.append(browser.page())
        login_to_chat(pages[0], "web-a", web_users["web-a"]); ask(pages[0], Q)       # разговор открыт — поток есть
        sid = last_session(stack, PLACE)
        for i, p in enumerate(pages[1:], 1):
            p.goto(f"{STAND}/chat/"); p.wait_url("/chat/", 30); p.wait_selector(cdp.Page.thread_row(sid), 60)
            p.open_thread(sid)                                              # тот же разговор — свой поток у вкладки
            if i < 3:                               # поток второй и третьей — до следующей: четвёртая по счёту — pages[3]
                p.wait(lambda p=p: any(r["status"] == 200 for r in p.requests(EVENT)), 30, f"поток вкладки {i + 1}")
        pages[3].wait_text(TOO_MANY_TABS, 60)
        assert [p.has_text(r"закройте лишние") for p in pages[:3]] == [False] * 3        # сказано только лишней
        shot(pages[3], "04-fourth-tab.png")
        n = len(pages[3].requests(EVENT))
        assert n and all(r["status"] != 200 for r in pages[3].requests(EVENT)), pages[3].requests(EVENT)  # потока не было
        pages[1].close()
        pages[3].wait_gone(r"закройте лишние", 45)                                     # следующая попытка по паузе прошла
        assert any(r["status"] == 200 for r in pages[3].requests(EVENT)[n:])            # поток — после закрытия второй
        # 509/503 WorkplaceBusy на вспомогательных GET при открытии четырёх вкладок допустимы: у edge 10 на cookie
        assert all(p.csp() == [] for p in pages)
    finally:
        for p in pages[1:]:
            p.close()


def test_first_working_chat_on_twin(stack, page, web_users, record_property):            # Д7б: первый работающий чат
    """Первый работающий чат на двойнике (Б4). Панель «Агенты» — встроенные агенты и пустые «Мои агенты». Конструктор
    сохраняет агента (двойник OpenRouter по слову FAKESAVE), и тот появляется в панели сам — опросом после конца хода,
    без перезагрузки страницы. Разговор с ним уходит в место с его инструкцией и только его инструментами — тело запроса
    к модели по сессии разговора (I4). Ручку «Мои агенты» вкладка зовёт через edge с cookie — только 200 (П13).

    Сохранение перезагружает место 09 (dispose), пока поток /event вкладки открыт (m18): консоль страницы сверяется и
    после этого. Поле ввода после первой отправки в новом разговоре — в фокусе (m2 ревью Б3: блокировка на время
    загрузки истории его не снимает насовсем). Панель при длинном списке разговоров не сжимается (итоговая доводка
    Д7б, замечание Б4). Факты (фокус, состояния агента в панели, потоки) — в свойстве d7b отчёта junit."""
    konstruktor_md = KONSTRUKTOR_MD.read_text(encoding="utf-8")
    assert KONSTRUKTOR_HEAD in konstruktor_md and f"„{MY_AGENTS}“" in konstruktor_md   # тексты образа (Б1) = панель
    open_chat(stack, page, web_users["web-a"])
    assert page.texts(".agents .agents-sub") == [MY_AGENTS] and page.texts(".agents .agents-note") == [NO_AGENTS]
    assert page.ready_agents() == {"pomoshnik", "konstruktor"}
    before, after, hidden, list_scrolls = page.eval(PANEL_SQUEEZE_JS)                  # замечание Б4: длинный список
    assert list_scrolls and abs(after - before) < 1 and hidden <= 1, (before, after, hidden)   # панель не сжата
    shot(page, "02-agents-panel.png")
    page.click_agent("konstruktor")
    assert page.agent_line() == "Агент: Конструктор агентов"
    events = len(page.requests(EVENT))
    page.eval(FIELD_PROBE_JS)
    ask(page, f"FAKESAVE agent={OWN_AGENT}")
    turn_end = time.monotonic()
    focus_first = page.eval(FOCUS_JS)                                                     # m2: первая отправка
    field = page.eval("globalThis.__pcbkFieldProbe")
    sid = page.current_thread_id()
    assert page_sessions(page) == [sid] and user_agents(stack, PLACE, sid) == ["konstruktor"]
    assert re.search(r'"status":\s*"ok"', tool_output(stack, PLACE, sid, "pcbk_save_agent"))   # служба сохранила
    assert page.agent_line() == "Агент: Конструктор агентов"
    seen: list = []                                 # кнопка своего агента, пока ждём ready: [state, disabled, текст]

    def ready() -> bool:
        row = agent_row(page, OWN_AGENT)
        if not seen or seen[-1] != row:
            seen.append(row)
        return OWN_AGENT in page.ready_agents()
    page.wait(ready, 15, f"{OWN_AGENT} готов в панели (без перезагрузки страницы)")       # панель обновилась сама
    to_ready = round(time.monotonic() - turn_end, 1)
    events_ready = [r["status"] for r in page.requests(EVENT)]    # поток разговора Конструктора и его переподключения
    assert all(r[1] and AGENT_PENDING in r[2] for r in seen if r and r[0] == "pending"), seen   # pending — неактивна
    state, disabled, text = agent_row(page, OWN_AGENT)
    assert (state, disabled) == ("ready", False) and OWN_TITLE in text and save_args(OWN_AGENT)["purpose"] in text
    assert page.texts(".agents .agents-note") == []                                       # «Пока нет» ушло
    shot(page, "03-konstruktor-saved.png")
    page.click_agent(OWN_AGENT)
    assert page.agent_line() == f"Агент: {OWN_TITLE}"
    ask(page, "что сейчас?")
    focus_own = page.eval(FOCUS_JS)
    own = page.current_thread_id()
    assert own != sid and page_sessions(page) == [sid, own]
    assert user_agents(stack, PLACE, own) == [OWN_AGENT]                                   # истина места: ход — агенту
    body = turn_bodies(stack, PLACE, own)[0]                                               # I4: по сессии разговора
    assert f"Ты — агент «{OWN_TITLE}»" in system_text(body) and KONSTRUKTOR_HEAD not in system_text(body)
    assert tool_names_of(body) - {"invalid"} == OWN_TOOLS
    calls = [(r["method"], r["status"]) for r in page.requests("/pcbk/api/agents")]
    assert calls and set(calls) == {("GET", 200)}, calls                                   # с cookie — 200 (П13)
    facts = {"focus_after_first_send": [focus_first, focus_own], "field_first_send": field, "own_agent_rows": seen,
             "turn_end_to_ready_s": to_ready, "event_before_save": events, "event_at_ready": events_ready,
             "event_at_end": len(page.requests(EVENT)), "agents_calls": len(calls)}
    record_property("d7b", json.dumps(facts, ensure_ascii=False))
    print(f"Б4 журнал: {json.dumps(facts, ensure_ascii=False)}")
    assert focus_first and focus_own, facts                                                 # m2: поле не теряет фокус
    assert page.csp() == [] and page.console_errors() == []                                # и после dispose места (m18)
    shot(page, "04-own-agent-answer.png")


def test_konstruktor_thread_survives_reload(stack, page, web_users, record_property):    # Review Focus 1 Д7б
    """Разговор Конструктора переживает перезагрузку страницы: после неё чат открывает новый разговор с помощником
    (контроль: агент — не память страницы), разговор Конструктора открывается из списка, и следующий ход уходит
    Конструктору — истина места (info.agent) и системный текст запроса к модели. Перед отправкой — текст прежнего
    ответа (П14). С открытия разговора до ответа поле ввода ни в одном коммите не открыто ходу другого агента (M10
    ревью Б3)."""
    open_chat(stack, page, web_users["web-a"])
    page.click_agent("konstruktor")
    first = ask(page, "привет")
    focus_first = page.eval(FOCUS_JS)                                                     # m2: первая отправка
    sid = page.current_thread_id()
    assert user_agents(stack, PLACE, sid) == ["konstruktor"]
    page.reload()
    page.wait_selector("textarea", 90)
    page.wait(lambda: page.texts(".agents .agent-title")[:2] == BUILTIN_TITLES, 30, "панель после перезагрузки")
    page.wait(lambda: page.agent_line() == "Агент: Помощник по данным", 10, "новый разговор — помощник (контроль)")
    page.wait_selector(cdp.Page.thread_row(sid), 30)
    page.eval(OPEN_PROBE_JS % (json.dumps(sid), json.dumps("Агент: Конструктор агентов")))
    page.open_thread(sid)
    done = re.search(ANSWER, first)[0]
    page.wait(lambda: any(done in t for t in page.eval(ASSISTANT_TEXTS_JS)), 30, "прежний ответ Конструктора")  # П14
    page.wait(lambda: page.agent_line() == "Агент: Конструктор агентов", 10, "агент разговора — Конструктор")
    ask(page, "ещё")
    assert page.current_thread_id() == sid and page_sessions(page) == [sid, sid]
    assert user_agents(stack, PLACE, sid) == ["konstruktor", "konstruktor"]               # истина места
    bodies = turn_bodies(stack, PLACE, sid)
    assert KONSTRUKTOR_HEAD in system_text(bodies[0]) and KONSTRUKTOR_HEAD in system_text(bodies[-1])
    probe = page.eval("globalThis.__pcbkOpenProbe")
    record_property("d7b", json.dumps({"focus_after_first_send": focus_first, "open_probe": probe}, ensure_ascii=False))
    print(f"Б4 журнал: перезагрузка — фокус после первой отправки {focus_first}; проба M10 {probe}")
    # M10; locked > 0 — проба застала окно загрузки истории (разговор открыт, поле закрыто), а не только время после
    assert probe["locked"] > 0 and probe["open"] > probe["locked"] and probe["bad"] == [], probe
    assert focus_first                                                                      # m2
    assert page.csp() == [] and page.console_errors() == []


def test_broken_own_agent_not_loaded(stack, page, web_users):                         # ревью Б2: not_visible → broken
    """Свой агент, которого место после перезагрузки не видит, — в панели «не загрузился — сообщите преподавателю»,
    кнопка неактивна (R64, R65). Агента сохраняет служба (save_agent с токеном места), место его показывает; потом его
    файл портится мимо службы (BROKEN_YAML: первая загрузка места его пропускает) и место перезагружается командой
    администратора — строка учёта not_visible, ручка — broken. В конце агент уходит в архив (файла больше нет — кэш
    gray-matter места ничего не вернёт), очередь перезагрузки пустеет."""
    open_chat(stack, page, web_users["web-a"])
    assert stack.mcp_save(PLACE, save_args(BROKEN_AGENT))["status"] == "ok"
    try:
        page.wait(lambda: page.eval(AGENT_API_STATE_JS % json.dumps(BROKEN_AGENT)) == [200, "ready"], 30,
                  f"{BROKEN_AGENT} готов в ответе ручки")                                   # положительный контроль
        stack.put_agent_file(PLACE, BROKEN_AGENT, BROKEN_YAML)
        stack.agents_admin("reload", "--place", str(PLACE))
        page.wait(lambda: page.eval(AGENT_API_STATE_JS % json.dumps(BROKEN_AGENT)) == [200, "broken"], 60,
                  f"{BROKEN_AGENT} broken в ответе ручки")
        assert place_agent_state(stack, PLACE, BROKEN_AGENT) == "not_visible"             # не карантин: файл на месте
        page.reload()
        page.wait(lambda: agent_row(page, BROKEN_AGENT), 30, f"{BROKEN_AGENT} в панели после перезагрузки")
        state, disabled, text = agent_row(page, BROKEN_AGENT)
        assert (state, disabled) == ("broken", True) and AGENT_BROKEN in text and OWN_TITLE in text
        assert BROKEN_AGENT not in page.ready_agents()
        assert page.csp() == [] and page.console_errors() == []
    finally:
        stack.agents_admin("archive", "--place", str(PLACE), "--name", BROKEN_AGENT, "--yes")
        wait_for(lambda: stack.agents_health()["pending"] == 0, 30)                         # место перезагружено


def test_banner_follows_watchdog(stack, page, web_users):                                 # Review Focus 5; учение — ближе к концу
    ensure_notice_acked(stack, "web-a")
    login_to_chat(page, "web-a", web_users["web-a"])
    stack.stop("pcbk-watchdog")                                                            # preflight M14: stop/start стенда
    try:
        page.wait_text(r"Состояние стенда неизвестно — сторож не отвечает", 30 + 15)
        shot(page, "02-banner-unknown.png")
    finally:
        stack.start("pcbk-watchdog")
    page.wait_gone(r"Состояние стенда неизвестно", 60)
    assert page.csp() == [] and page.console_errors() == []


def test_web_row_and_drill(stack, page):                                                  # учение строки «Веб-чат» — до отчётов
    build = json.loads((stack.static_dir / "chat/build.json").read_text())
    stack.wait_status(lambda d: row(d, "web") == ("ok", "страница чата отдаётся", f"сборка {build['build']}"), timeout=60)
    asset = stack.static_dir / "chat" / next(p for p in build["files"] if p.endswith(".css"))
    hidden = asset.with_suffix(".hidden")
    asset.rename(hidden)
    try:
        stack.wait_status(lambda d: row(d, "web")[:2] == ("fail", "сборка чата неполная"), timeout=60)
    finally:
        hidden.rename(asset)
    stack.wait_status(lambda d: row(d, "web")[:2] == ("ok", "страница чата отдаётся"), timeout=60)
    page.goto(f"{STAND}/status")                                                           # строка на странице владельца
    page.wait_text(r"Веб-чат", 20); page.wait_text(r"страница чата отдаётся", 20)
    page.eval(WEB_ROW_INTO_VIEW_JS)                                                        # строка — последняя, ниже экрана
    shot(page, "04-status-web-row.png")


def test_reports_reach_row(stack, page, web_users):                                      # последним в модуле
    """Отчёт браузера через edge и шлюз (fetch страницы чата: та же cookie, Origin и Content-Type, что у api.report)
    красит строку «Веб-чат». До него отчётов нет: ложный отчёт любого теста выше виден здесь и в учении строки."""
    ensure_notice_acked(stack, "web-a")
    assert web_reports(stack) == NO_REPORTS
    login_to_chat(page, "web-a", web_users["web-a"])
    code = page.eval("fetch('/pcbk/api/web/report',{method:'POST',headers:{'Content-Type':'application/json'},"
                     "body:JSON.stringify({kind:'turn_silent'})}).then(r=>r.status)")
    assert code == 204
    assert web_reports(stack) == {**NO_REPORTS, "turn_silent_1h": 1}
    stack.wait_status(lambda d: row(d, "web") == ("warn", "ответ модели не пришёл вовремя", "1 за час"), timeout=60)
    page.goto(f"{STAND}/status")                                                           # строка на странице владельца
    page.wait_text(r"ответ модели не пришёл вовремя", 20)
    page.eval(WEB_ROW_INTO_VIEW_JS)
    shot(page, "02-web-row-warn.png")
    assert page.csp() == [] and page.console_errors() == []
