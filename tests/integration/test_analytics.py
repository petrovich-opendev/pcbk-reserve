"""Аналитический чат технолога КДМ-1 через edge (Task 9, план `PLAN-KDM1-analytics-2026-10-14`): новый
префикс `/pcbk/api/analytics/` (девять путей, `docs/kdm1/INTERFACES.md`) открыт по образцу `/pcbk/api/lessons`
(Д15а) — та же зона `pcbk_place_rate`, то же тело 64 КиБ, тот же `$pcbk_bad_api_uri`, те же JSON-ошибки edge;
путь без слеша — точный отказ edge, не core (мастер-промпт §10). Корпус — только на чтение в `/srv/analytics`
(как `LESSONS_ROOT`/`LESSONS_DIR`), но, в отличие от уроков, НЕОБЯЗАТЕЛЕН в `.env` — выключатель
`ANALYTICS_ENABLED` по умолчанию пуст, старый `.env` без ANALYTICS_* поднимается как раньше.

ШАГ 1 (контроллер, `task-9-brief.md`): файл написан без подъёма стенда — прогон (`pytest test_analytics.py`)
только шагом 2, после того как Task 4 (service.py/store.py/gateway/__init__.py/analytics_api.py/main.py) и
Task 5/7 (llm_adapter.py, data_bridge.py/reviews.py) закончат реализацию. До тех пор тесты ниже — зафиксированное
намерение, не зелёный прогон.

Какие сценарии плана (`INTEGRATION_CASES.json`) покрыты (важные/критичные для интеграционного уровня):
- I-06 «Новый путь Nginx» — аналитика направлена в core явно, прочий `/pcbk/api/` — 404, нового порта нет
  (`test_core_binds_analytics_corpus_dir_read_only`, вместе с test_edge.py).
- I-07 «Неправильный путь со слешем/кодированными точками» — `$pcbk_bad_api_uri` и точный отказ без слеша
  (`test_analytics_path_without_slash_is_exact_edge_404`, `test_analytics_bad_raw_uri_rejected_before_core`).
- мастер-промпт §10 «тело 64 КиБ» (`test_analytics_body_over_64kib_rejected`).
- I-02 «Зона/настройка/сессия» порядок как у `lessons_api` (`test_analytics_requires_login_same_order_as_lessons`).
- I-03/I-05 «чужой идентификатор не подтверждает существование ресурса», «история привязана к логину»
  (`test_analytics_unknown_conversation_ask_is_404`, `test_analytics_cross_user_conversation_hidden`) — через
  настоящие cookie двух разных логинов edge, не через `TestClient` одного процесса (core/tests это уже покрывают
  изнутри — здесь проверяется проводка edge+сессия+шлюз целиком).
- I-08 «Неисправный корпус при запуске» — аналитика отвечает понятным отказом, вход/уроки/калькуляторы живы
  (`test_analytics_broken_corpus_degrades_without_breaking_others_then_switch_off`); тот же тест демонстрирует
  выключатель (переход обратно в умолчание) и оставляет стенд выключенным для следующих модулей сессии.

Чего здесь НЕТ и почему: полный путь с LLM/данными/согласованием (Task 5/7 ещё не начаты на момент шага 1 —
`docs/kdm1/INTERFACES.md`); частотный отказ 509 отдельно не дублируется — зона `pcbk_place_rate` уже разделена
с `lessons`/`calc`/`teacher`, и этот файл — байт-в-байт та же копия блока лимитов edge; откат ВЕРСИЙ корпуса —
Task 8 (`knowledge.py`, «Дополнение Task 8»), здесь нет версионированных подкаталогов для отката."""
import pytest

from fixtures.kdm1_corpus import build_corpus

LESSONS_PATH = "/pcbk/api/lessons"
ANALYTICS_PATH = "/pcbk/api/analytics"
CAPABILITIES_PATH = f"{ANALYTICS_PATH}/capabilities"
CONVERSATIONS_PATH = f"{ANALYTICS_PATH}/conversations"
CONVERSATION_PATH = ANALYTICS_PATH + "/conversations/{id}"
QUESTIONS_PATH = ANALYTICS_PATH + "/conversations/{id}/questions"
REQUESTS_PATH = ANALYTICS_PATH + "/requests/{id}"
REQUEST_CANCEL_PATH = ANALYTICS_PATH + "/requests/{id}/cancel"
EVIDENCE_PATH = ANALYTICS_PATH + "/evidence/{id}"
REVIEW_REQUESTS_PATH = f"{ANALYTICS_PATH}/reviews/requests"
REVIEW_DECISION_PATH = ANALYTICS_PATH + "/reviews/requests/{id}/decisions"
LINKS_PATH = f"{ANALYTICS_PATH}/links"
LINK_CANDIDATES_PATH = ANALYTICS_PATH + "/links/{id}/candidates"
# девять путей зафиксированного набора (docs/kdm1/INTERFACES.md); .format(id=...) безвреден и у путей без {id}
ALL_PATHS = (CAPABILITIES_PATH, CONVERSATIONS_PATH, CONVERSATION_PATH, QUESTIONS_PATH, REQUESTS_PATH,
            REQUEST_CANCEL_PATH, EVIDENCE_PATH, REVIEW_REQUESTS_PATH, REVIEW_DECISION_PATH, LINKS_PATH,
            LINK_CANDIDATES_PATH)
NOT_FOUND_CORE = {"name": "NotFound", "data": {"message": "Не найдено"}}       # catch_all_router core (заглавная)
NOT_FOUND_EDGE = {"name": "NotFound", "data": {"message": "не найдено"}}      # точный JSON edge (строчная)

LOGIN_A, PLACE_A = "analyt-a", 4
LOGIN_B, PLACE_B = "analyt-b", 5


def test_core_binds_analytics_corpus_dir_read_only(stack, tmp_path):
    """Ruling (решения контроллера, task-9-brief.md): корпус — только на чтение, без нового тома состояния
    (состояние — файл в уже существующем томе core-data, мастер-промпт §4). Без ANALYTICS_CORPUS_DIR (старый
    .env) — пустой именованный том pcbk-analytics-empty, не каталог репозитория (на сервере его нет); с ним —
    привязка каталога хоста; в обоих случаях только на чтение."""
    core = stack.prod_config()["services"]["core"]                 # compose.yaml сервера, без тестовых правок
    corpus = next(v for v in core["volumes"] if v["target"] == "/srv/analytics")
    assert (corpus["type"], corpus["source"], corpus.get("read_only", False)) == \
        ("volume", "pcbk-analytics-empty", True)
    assert {v["target"] for v in core["volumes"] if v.get("type") == "volume"} == {"/var/lib/pcbk-core",
                                                                                   "/srv/analytics"}
    host_dir = tmp_path / "corpus"
    host_dir.mkdir()
    core = stack.compose_config({"ANALYTICS_CORPUS_DIR": str(host_dir)})["services"]["core"]
    corpus = next(v for v in core["volumes"] if v["target"] == "/srv/analytics")
    assert (corpus["type"], corpus["source"], corpus.get("read_only", False)) == ("bind", str(host_dir), True)


def test_analytics_disabled_by_default_404_and_old_routes_work(stack):
    """I-06/I-08 («выключена»): test.env (как и старый .env) не задаёт ANALYTICS_* — все пути аналитики
    (ALL_PATHS) отвечают 404, будто пути вовсе нет (роутер не подключён core, не edge); вход и уроки продолжают
    работать."""
    gw = stack.gw_client()
    for path in ALL_PATHS:
        code, body, h = gw.call("GET", path.format(id="x1"))
        assert (code, body, h["content-type"]) == (404, NOT_FOUND_CORE, "application/json"), path
    assert gw.call("GET", "/pcbk/api/auth/me")[0] in (200, 401)
    assert gw.call("GET", LESSONS_PATH)[0] in (200, 401)


def test_analytics_path_without_slash_is_exact_edge_404(stack):
    """I-07: `/pcbk/api/analytics` без завершающего слеша не совпадает с префиксом `/pcbk/api/analytics/` —
    точный JSON-отказ самого edge (не core), без HTML-перенаправления (не 301/302)."""
    gw = stack.gw_client()
    for method in ("GET", "POST"):
        code, body, h = gw.call(method, ANALYTICS_PATH)
        assert (code, body, h["content-type"]) == (404, NOT_FOUND_EDGE, "application/json"), method
        assert h.get("Location") is None


def test_analytics_bad_raw_uri_rejected_before_core(stack):
    """I-07: прежняя защита `$pcbk_bad_api_uri` (кодированные точки/слэши, «/.», «//») — 400 JSON edge до core,
    без логина (отказ раньше проверки сессии)."""
    gw = stack.gw_client()
    for path in (f"{ANALYTICS_PATH}/%2e%2e/evidence/e1", f"{ANALYTICS_PATH}//capabilities",
                f"{ANALYTICS_PATH}/./capabilities"):
        code, body, h = gw.call("GET", path)
        assert (code, body["name"], h["content-type"]) == (400, "BadRequest", "application/json"), path


def test_analytics_body_over_64kib_rejected(stack):
    """Мастер-промпт §10 «тело 64 КиБ»: запрос свыше предела — 413 edge, до core не доходит (как
    /pcbk/api/web/report у test_edge.py)."""
    gw = stack.gw_client()
    code, body, h = gw.call("POST", CONVERSATIONS_PATH, {"mode": "x" * (66 * 1024)})
    assert (code, body["name"], h["content-type"]) == (413, "PayloadTooLarge", "application/json")


@pytest.fixture(scope="module")
def analytics_users(stack):
    """Два студента только для входа аналитики (как lesson_student test_lessons.py): места не пробуждаются,
    аналитике место не нужно."""
    pw_a, pw_b = stack.new_password(), stack.new_password()
    stack.add_account(LOGIN_A, "student", PLACE_A, pw_a)
    stack.add_account(LOGIN_B, "student", PLACE_B, pw_b)
    try:
        a, b = stack.gw_client(), stack.gw_client()
        a.login(LOGIN_A, pw_a)
        b.login(LOGIN_B, pw_b)
        yield a, b
    finally:
        for login in (LOGIN_A, LOGIN_B):
            stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "disable", "--login", login)
            stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "remove", "--login", login,
                      "--yes")


@pytest.fixture(scope="module")
def analytics_corpus(tmp_path_factory):
    """Синтетический корпус 20FAKE_* (core/tests/fixtures/kdm1_corpus.py), той же схемы, что настоящий
    `knowledge/kdm1.seed.sqlite3` — вне git, как и настоящий корпус на сервере (бинарный SQLite не коммитится)."""
    d = tmp_path_factory.mktemp("analytics-corpus")
    build_corpus(d / "kdm1.seed.sqlite3")
    # core в контейнере — uid 10003, каталог pytest создаёт 0700 владельцу прогона: без чтения для всех core
    # не откроет корпус (на сервере корпус так же должен читаться 10003 — deploy/README.md)
    d.chmod(0o755)
    (d / "kdm1.seed.sqlite3").chmod(0o644)
    return d


@pytest.fixture(scope="module")
def analytics_enabled(stack, analytics_corpus):
    """Включить аналитику на core с синтетическим корпусом на время модуля (stack.recreate — тот же приём, что
    учения DRILL_*/LLM_STATE); test.env по умолчанию её не меняет (Ruling: ANALYTICS_* по умолчанию выключены,
    как старый .env) — вернуть на выходе, чтобы стенд остался в умолчании для следующих модулей сессии."""
    stack.recreate("core", {"ANALYTICS_ENABLED": "on", "ANALYTICS_CORPUS_DIR": str(analytics_corpus)})
    try:
        yield stack
    finally:
        stack.recreate("core", {})


def test_analytics_requires_login_same_order_as_lessons(analytics_enabled):
    """I-02: без cookie — 401 (как lessons_api — сессия раньше самой ручки), не 503/404."""
    gw = analytics_enabled.gw_client()
    code, body, h = gw.call("GET", CAPABILITIES_PATH)
    assert (code, body["name"], h["content-type"]) == (401, "Unauthorized", "application/json")


def test_analytics_capabilities_shape_with_real_corpus(analytics_enabled, analytics_users):
    """Корпус реально смонтирован и открывается через настоящий стенд (не `TestClient` core/tests):
    `capabilities` отдаёт ровно задокументированные ключи, `reference_available` — True, `corpus_version` —
    непустая строка. `llm_available`/`data_bridge_available` не проверяются строго: Task 5/7 на момент этого
    прогона могут быть ещё не подключены в main.py — это не дефект edge/compose."""
    a, _ = analytics_users
    code, body, h = a.call("GET", CAPABILITIES_PATH)
    assert code == 200 and h["content-type"] == "application/json"
    assert set(body) == {"reference_available", "llm_available", "data_bridge_available", "review_authority",
                         "corpus_version"}
    assert body["reference_available"] is True
    assert isinstance(body["corpus_version"], str) and body["corpus_version"] != ""


def test_analytics_unknown_conversation_ask_is_404(analytics_enabled, analytics_users):
    """I-03: чужой/несуществующий идентификатор не подтверждает существование ресурса — через настоящий edge и
    сессию (не `TestClient` одного процесса)."""
    a, _ = analytics_users
    code, _, _ = a.call("POST", QUESTIONS_PATH.format(id="no-such-conversation"),
                        {"client_request_id": "cr-1", "question": "вопрос", "mode": "reference"})
    assert code == 404


def test_analytics_cross_user_conversation_hidden(analytics_enabled, analytics_users):
    """I-03/I-05: диалог одного студента не виден другому ни по id, ни в списке — через реальные cookie двух
    разных логинов edge."""
    a, b = analytics_users
    create = a.call("POST", CONVERSATIONS_PATH, {"mode": "reference"})
    assert create[0] == 201
    conv_id = create[1]["id"]
    assert b.call("GET", CONVERSATION_PATH.format(id=conv_id))[0] == 404
    listed = b.call("GET", CONVERSATIONS_PATH)[1]
    assert conv_id not in [c["id"] for c in listed["conversations"]]
    assert a.call("GET", CONVERSATION_PATH.format(id=conv_id))[0] == 200


def test_analytics_links_through_edge(analytics_enabled, analytics_users):
    """Д17: «Связи с историаном» через настоящий edge и сессию — список строк `equipment_links` синтетического
    корпуса (без согласования все «unbound»), неизвестная связь — 404 на подбор тега, заявка на связь с чужим id
    или с именем тега не по `SAFE_NAME` — 400 (до записи заявки)."""
    a, _ = analytics_users
    code, body, h = a.call("GET", LINKS_PATH)
    assert code == 200 and h["content-type"] == "application/json"
    assert body["links"] and {link["state"] for link in body["links"]} == {"unbound"}
    assert all(link["id"].startswith("EQ-FAKE-") for link in body["links"])
    assert a.call("GET", LINK_CANDIDATES_PATH.format(id="no-such-link"))[0] == 404
    bad_subject = {"kind": "equipment_link", "subject_id": "no-such-link", "new_value": "20FAKE_001_PV",
                   "scope": "КДМ-1", "rationale": "проверка"}
    assert a.call("POST", REVIEW_REQUESTS_PATH, bad_subject)[0] == 400
    bad_tag = dict(bad_subject, subject_id=body["links"][0]["id"], new_value="20FAKE_001_PV; DROP")
    assert a.call("POST", REVIEW_REQUESTS_PATH, bad_tag)[0] == 400


def test_analytics_broken_corpus_degrades_without_breaking_others_then_switch_off(
        analytics_enabled, analytics_users, tmp_path_factory):
    """I-08: каталог без `*.seed.sqlite3` — аналитика отвечает понятным отказом, вход/уроки живы. Последним шагом
    — выключатель (`ANALYTICS_ENABLED` пусто) возвращает 404 и оставляет стенд в умолчании для следующих модулей
    сессии (запись сильнее того, что сделает teardown `analytics_enabled`, — проверяем явно, а не полагаемся)."""
    broken = tmp_path_factory.mktemp("analytics-broken")   # пустой каталог — ни одного *.seed.sqlite3
    stack = analytics_enabled
    a, _ = analytics_users
    stack.recreate("core", {"ANALYTICS_ENABLED": "on", "ANALYTICS_CORPUS_DIR": str(broken)})

    caps = a.call("GET", CAPABILITIES_PATH)
    assert caps[0] == 200 and caps[1]["reference_available"] is False
    create = a.call("POST", CONVERSATIONS_PATH, {"mode": "reference"})
    assert create[0] == 503 and create[1]["name"] == "Unavailable"
    assert a.call("GET", "/pcbk/api/auth/me")[0] == 200
    assert a.call("GET", LESSONS_PATH)[0] == 200

    stack.recreate("core", {})                         # выключатель: вернуть умолчание для прочих модулей
    off = a.call("GET", CAPABILITIES_PATH)
    assert off[0] == 404

