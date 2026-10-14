"""Роутер аналитики КДМ-1 у шлюза (план КДМ1, Task 0): выключена (умолчание) — /pcbk/api/analytics/* отвечает
404, как путь, которого нет вовсе (роутер не подключён, не 503); вход/чат(уроки)/калькуляторы продолжают
работать. Включена — ровно те же заглушки 503 «аналитика ещё не готова», что и у прочих ручек; порядок проверок
(зона → настройка → сессия) — тот же, что у lessons_api (helpers.Gw — та же фикстура)."""
import pytest
from fastapi.testclient import TestClient
from fixtures.kdm1_corpus import build_corpus

from helpers import BASE, FRONT, PLACE_NET, Gw, fail_connect, login, make_gateway
from pcbk_core.analytics import service as service_mod
from pcbk_core.analytics.service import AnalyticsService
from pcbk_core.analytics.settings import AnalyticsSettings
from pcbk_core.gateway.analytics_api import (CAPABILITIES_PATH, CONVERSATION_PATH, CONVERSATIONS_PATH,
                                             EVIDENCE_PATH, QUESTIONS_PATH, REQUEST_CANCEL_PATH, REQUESTS_PATH,
                                             REVIEW_DECISION_PATH, REVIEW_REQUESTS_PATH)
from pcbk_core.gateway.api import COOKIE, GW_MESSAGES
from pcbk_core.gateway.lessons_api import LESSONS_PATH


@pytest.fixture
def gw_off(tmp_path):
    app, role = make_gateway(tmp_path)                                    # analytics_enabled=False (умолчание)
    with TestClient(app, base_url=BASE, client=FRONT):
        yield Gw(app, role)


@pytest.fixture
def gw_on(tmp_path):
    app, role = make_gateway(tmp_path, analytics_enabled=True)
    with TestClient(app, base_url=BASE, client=FRONT):
        yield Gw(app, role)


ALL_PATHS = (CAPABILITIES_PATH, CONVERSATIONS_PATH, REQUESTS_PATH.format(request_id="r1"),
            REQUEST_CANCEL_PATH.format(request_id="r1"), EVIDENCE_PATH.format(evidence_id="e1"),
            REVIEW_REQUESTS_PATH, REVIEW_DECISION_PATH.format(review_id="rv1"))


def test_analytics_disabled_is_404_and_old_routes_work(gw_off):
    s3 = gw_off.as_("stu-03")
    for path in ALL_PATHS:
        r = s3.get(path)
        assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), path

    # старый вход/уроки/калькуляторы продолжают работать при выключенной аналитике (Global Constraints плана)
    assert gw_off.as_("stu-03").get("/pcbk/api/auth/me").status_code == 200
    assert gw_off.as_("stu-03").get(LESSONS_PATH).status_code == 200
    # make_gateway без calc= — служба данных не подключена (503 "недоступны", не 404): маршрут calc сам жив,
    # аналитика на него не влияет; это тот же отказ, что и test_gw_calc.py проверяет без службы
    calc = gw_off.as_("stu-03").post("/pcbk/api/calc/catalog_search", json={"query": "x"},
                                     headers={"Origin": BASE})
    assert (calc.status_code, calc.json()["data"]["message"]) == \
        (503, "Калькуляторы недоступны — служба данных не подключена")


NOT_READY = {"name": "Unavailable", "data": {"message": "Аналитика КДМ-1 ещё не готова"}}


def test_analytics_enabled_stub_503_get(gw_on):
    s3 = gw_on.as_("stu-03")
    for path in (CAPABILITIES_PATH, CONVERSATIONS_PATH, CONVERSATION_PATH.format(conversation_id="c1"),
                REQUESTS_PATH.format(request_id="r1"), EVIDENCE_PATH.format(evidence_id="e1"),
                REVIEW_REQUESTS_PATH):
        r = s3.get(path)
        assert (r.status_code, r.json()) == (503, NOT_READY), path

    teacher = gw_on.as_("teach")                                          # зона/настройка/сессия — как lessons_api:
    assert teacher.get(CAPABILITIES_PATH).status_code == 503              # преподавателю тоже отвечает, не 403


def test_analytics_enabled_stub_503_post_with_origin(gw_on):
    s3 = gw_on.as_("stu-03")
    headers = {"Origin": BASE}
    for path in (CONVERSATIONS_PATH, QUESTIONS_PATH.format(conversation_id="c1"),
                REQUEST_CANCEL_PATH.format(request_id="r1"), REVIEW_REQUESTS_PATH,
                REVIEW_DECISION_PATH.format(review_id="rv1")):
        r = s3.post(path, headers=headers)
        assert (r.status_code, r.json()) == (503, NOT_READY), path


def test_analytics_post_checks_origin_after_session(gw_on):
    s3 = gw_on.as_("stu-03")
    r = s3.post(CONVERSATIONS_PATH)                                       # учётка есть, Origin — нет — 403
    assert (r.status_code, r.json()) == \
        (403, {"name": "Forbidden", "data": {"message": GW_MESSAGES["origin"]}})
    assert gw_on.anon().post(CONVERSATIONS_PATH).status_code == 401       # без учётки — 401 раньше источника


def test_analytics_enabled_refusals_same_order_as_lessons(gw_on):
    r = gw_on.anon().get(CAPABILITIES_PATH)                                # нет сессии — 401, раньше заглушки 503
    assert (r.status_code, r.json()) == \
        (401, {"name": "Unauthorized", "data": {"message": GW_MESSAGES["need_login"]}})
    # вне pcbk-front — 404 (зона раньше сессии и заглушки)
    assert gw_on.as_("stu-03", client=PLACE_NET).get(CAPABILITIES_PATH).status_code == 404


def test_analytics_enabled_config_error_503(gw_on):                        # m24: при config_error — не заглушка
    s3 = gw_on.as_("stu-03")
    gw_on.role.config_error = "GW_ORIGIN не задан"
    r = s3.get(CAPABILITIES_PATH)
    assert (r.status_code, r.json()) == \
        (503, {"name": "Unavailable", "data": {"message": "Шлюз не настроен: GW_ORIGIN не задан"}})


# --- Task 4: настоящий координатор (role.analytics подключён явно, минуя main.py — как будет после Task 4) ---

def _wire_analytics(role, tmp_path, *, broken_corpus: bool = False, timeout_s: float = 30.0,
                    reviewers: str = "grants") -> AnalyticsService:
    """`reviewers` (F5, решение владельца 14.10.2026) — `ANALYTICS_REVIEWERS`, умолчание `"grants"` — прежнее
    поведение существующих тестов этого файла."""
    corpus_dir = tmp_path / "an_corpus"
    if not broken_corpus:
        corpus_dir.mkdir(exist_ok=True)
        build_corpus(corpus_dir / "fake.seed.sqlite3")
    settings = AnalyticsSettings(ANALYTICS_ENABLED=True, ANALYTICS_CORPUS_DIR=str(corpus_dir),
                                 ANALYTICS_STATE_PATH=str(tmp_path / "analytics.db"),
                                 ANALYTICS_JOB_TIMEOUT_S=timeout_s, ANALYTICS_REVIEWERS=reviewers)
    role.analytics = AnalyticsService(settings)
    return role.analytics


class _AnUser:
    """Один логин поверх ОДНОГО персистентного TestClient (его портал открыт один раз на весь тест) — cookie
    передаётся заголовком на каждый вызов, не через cookie jar клиента (иначе второй логин того же client
    добавил бы вторую pcbk_session и шлюз отверг бы обе, Д5-R25)."""

    def __init__(self, client: TestClient, token: str):
        self._client = client
        self._cookie = f"{COOKIE}={token}"

    def _headers(self, extra: dict | None) -> dict:
        return {**(extra or {}), "Cookie": self._cookie}

    def get(self, path: str, **kw):
        kw["headers"] = self._headers(kw.pop("headers", None))
        return self._client.get(path, **kw)

    def post(self, path: str, **kw):
        kw["headers"] = self._headers(kw.pop("headers", None))
        return self._client.post(path, **kw)


class AnGw:
    """Как helpers.Gw, но все вызовы одного теста идут через ОДИН персистентный TestClient: отдельный
    TestClient без `with` (приём helpers.Gw.as_) открывает и тут же закрывает свой anyio-портал на каждый
    вызов — фоновое задание аналитики (asyncio.ensure_future в service.py), созданное во время одного запроса,
    обрывается раньше, чем следующий опрос успевает его увидеть (проверено отдельным воспроизведением — портал
    без `with` создаётся заново на каждый HTTP-вызов, starlette.testclient._portal_factory). Один персистентный
    портал на весь тест (эта обёртка) — фоновая задача доживает до опроса, как в реальном uvicorn с одним
    циклом событий."""

    def __init__(self, app, role, client: TestClient):
        self.app, self.role, self.client = app, role, client

    def as_(self, name: str) -> _AnUser:
        r = login(TestClient(self.app, base_url=BASE, client=FRONT), name)      # логин — отдельный короткий вызов
        assert r.status_code == 200, (name, r.status_code)
        return _AnUser(self.client, r.cookies[COOKIE])


@pytest.fixture
def gw_an(tmp_path):
    app, role = make_gateway(tmp_path, analytics_enabled=True)
    _wire_analytics(role, tmp_path)
    with TestClient(app, base_url=BASE, client=FRONT) as client:
        yield AnGw(app, role, client)


@pytest.fixture
def gw_an_broken_corpus(tmp_path):
    app, role = make_gateway(tmp_path, analytics_enabled=True)
    _wire_analytics(role, tmp_path, broken_corpus=True)
    with TestClient(app, base_url=BASE, client=FRONT) as client:
        yield AnGw(app, role, client)


@pytest.fixture
def gw_an_all_students(tmp_path):
    """F5 (решение владельца, 14.10.2026): ANALYTICS_REVIEWERS=all_students — любой student решает любую
    заявку области без назначения, в одно действие."""
    app, role = make_gateway(tmp_path, analytics_enabled=True)
    _wire_analytics(role, tmp_path, reviewers="all_students")
    with TestClient(app, base_url=BASE, client=FRONT) as client:
        yield AnGw(app, role, client)


def _fake_clarification_pipeline(monkeypatch, *, decision="needs_clarification", missing=("unit",)):
    """Подменяет preanalysis.analyze/rendering.* на простой детерминированный путь, без ожидания Task 3/5."""
    from pcbk_core.analytics.models import JobResponse, Preanalysis, Section

    def fake_analyze(identity, request, handle, corpus_version):
        pre = Preanalysis(scope="KDM1_PCBK", intent="definition", decision=decision, resolved_terms=[],
                          process_ids=[], conflict_ids=[], missing_fields=list(missing), risk="reference",
                          corpus_version=corpus_version, evidence_pack_id=None, allowed_claim_ids=[],
                          allowed_mechanism_ids=[], data_access_ready=False, llm_export_policy="public_only")
        return pre, ()

    def fake_clarification(keys, candidate_labels=()):
        # candidate_labels — правка F4a (хвост F4b, H-26): service._build_outcome теперь всегда передаёт этот
        # необязательный параметр (пустой тут — fake_analyze не заполняет resolved_terms), сигнатура фейка
        # должна совпадать с rendering.render_clarification_section, иначе TypeError на каждый needs_clarification
        del candidate_labels
        return Section(kind="clarification", text="уточните единицу измерения", evidence_refs=[],
                      rendered_by="server", claim_id=None)

    def fake_render_response(request_id, conversation_id, state, corpus_version, stage_label, outcome, sections):
        return JobResponse(request_id=request_id, conversation_id=conversation_id, state=state,
                           corpus_version=corpus_version, stage_label=stage_label, sections=list(sections),
                           clarification_keys=list(outcome.clarification_keys), error_code=outcome.error_code)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_clarification_section", fake_clarification)
    monkeypatch.setattr(service_mod.rendering, "render_response", fake_render_response)


def _poll_terminal(client, request_id: str, timeout: float = 3.0) -> dict:
    import time
    terminal = {"completed", "needs_clarification", "limited_answer", "cancelled", "failed"}
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(REQUESTS_PATH.format(request_id=request_id))
        assert r.status_code == 200, r.text
        body = r.json()
        if body["state"] in terminal:
            return body
        assert time.monotonic() < deadline, f"задание не завершилось: {body}"
        time.sleep(0.02)


def test_real_capabilities_shape_and_flags(gw_an):
    r = gw_an.as_("stu-03").get(CAPABILITIES_PATH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"reference_available", "llm_available", "data_bridge_available", "review_authority",
                         "corpus_version"}
    assert body == {"reference_available": True, "llm_available": False, "data_bridge_available": False,
                    "review_authority": False, "corpus_version": body["corpus_version"]}
    assert isinstance(body["corpus_version"], str) and body["corpus_version"] != ""


def test_real_full_reference_flow_matches_web_shapes(gw_an, monkeypatch):
    """Полный путь справки без LLM через настоящий API (создание диалога → вопрос → опрос → история) — формы
    ответов ровно те, что `web/src/analytics.ts` разбирает строгим `hasExactKeys` (Task 6, f6c0c95)."""
    _fake_clarification_pipeline(monkeypatch)
    s3 = gw_an.as_("stu-03")
    create = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE})
    assert create.status_code == 201
    assert set(create.json()) == {"id"}
    conv_id = create.json()["id"]

    ask = s3.post(QUESTIONS_PATH.format(conversation_id=conv_id), headers={"Origin": BASE},
                 json={"client_request_id": "cr-1", "question": "что такое выдуманный термин 1?", "mode": "reference"})
    assert ask.status_code == 202
    assert set(ask.json()) == {"request_id"}
    request_id = ask.json()["request_id"]

    body = _poll_terminal(s3, request_id)
    assert body["state"] == "needs_clarification"
    assert set(body) == {"request_id", "conversation_id", "state", "corpus_version", "stage_label", "sections",
                         "clarification_keys", "error_code"}
    assert body["sections"][0]["kind"] == "clarification"
    assert set(body["sections"][0]) == {"kind", "text", "evidence_refs", "rendered_by", "claim_id"}

    detail = s3.get(CONVERSATION_PATH.format(conversation_id=conv_id))
    assert detail.status_code == 200
    detail_body = detail.json()
    assert set(detail_body) == {"conversation", "requests"}
    assert set(detail_body["conversation"]) == {"id", "mode", "created_at", "last_activity_at", "corpus_version"}
    # {"question", "response"} на задание (Правка, раунд 1, Minor «д»): владельцу виден и исходный текст вопроса
    assert set(detail_body["requests"][0]) == {"question", "response"}
    assert detail_body["requests"][0]["question"] == "что такое выдуманный термин 1?"
    assert [r["response"]["request_id"] for r in detail_body["requests"]] == [request_id]

    listed = s3.get(CONVERSATIONS_PATH)
    assert listed.status_code == 200
    assert set(listed.json()) == {"conversations"}
    assert conv_id in [c["id"] for c in listed.json()["conversations"]]


def test_real_idempotent_submit_same_client_request_id(gw_an, monkeypatch):
    """ОСОБЫЙ ФОКУС ПРОВЕРКИ №3: повторный POST с тем же client_request_id — тот же request_id, не новое
    задание."""
    _fake_clarification_pipeline(monkeypatch, decision="refused")
    s3 = gw_an.as_("stu-03")
    conv_id = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE}).json()["id"]
    body = {"client_request_id": "cr-dup", "question": "вопрос", "mode": "reference"}
    first = s3.post(QUESTIONS_PATH.format(conversation_id=conv_id), json=body, headers={"Origin": BASE})
    second = s3.post(QUESTIONS_PATH.format(conversation_id=conv_id), json=body, headers={"Origin": BASE})
    assert first.status_code == second.status_code == 202
    assert first.json()["request_id"] == second.json()["request_id"]
    _poll_terminal(s3, first.json()["request_id"])


def test_real_cross_user_conversation_request_evidence_404(gw_an, monkeypatch):
    """ОСОБЫЙ ФОКУС ПРОВЕРКИ №1: чужой идентификатор не подтверждает существование ресурса."""
    _fake_clarification_pipeline(monkeypatch, decision="refused")
    s3, s5 = gw_an.as_("stu-03"), gw_an.as_("stu-05")
    conv_id = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE}).json()["id"]
    ask = s3.post(QUESTIONS_PATH.format(conversation_id=conv_id), headers={"Origin": BASE},
                 json={"client_request_id": "cr-1", "question": "вопрос", "mode": "reference"})
    request_id = ask.json()["request_id"]
    _poll_terminal(s3, request_id)

    assert s5.get(CONVERSATION_PATH.format(conversation_id=conv_id)).status_code == 404
    assert s5.get(REQUESTS_PATH.format(request_id=request_id)).status_code == 404
    assert s5.post(REQUEST_CANCEL_PATH.format(request_id=request_id), headers={"Origin": BASE}).status_code == 404
    assert conv_id not in [c["id"] for c in s5.get(CONVERSATIONS_PATH).json()["conversations"]]
    # свой диалог/задание остаются видимы владельцу
    assert s3.get(CONVERSATION_PATH.format(conversation_id=conv_id)).status_code == 200


def test_real_cancel_returns_full_job_response(gw_an, monkeypatch):
    """POST .../cancel отвечает 200 телом JobResponse (веб `AnalyticsPage.tsx` разбирает его parseJobResponse),
    не отдельным {"cancelled": bool}."""
    import threading
    started, blocked = threading.Event(), threading.Event()

    def sync_slow(identity, request, handle, corpus_version):
        started.set()
        blocked.wait(timeout=5)
        from pcbk_core.analytics.models import Preanalysis
        return (Preanalysis(scope="KDM1_PCBK", intent="definition", decision="limited_answer", resolved_terms=[],
                            process_ids=[], conflict_ids=[], missing_fields=[], risk="reference",
                            corpus_version=corpus_version, evidence_pack_id=None, allowed_claim_ids=[],
                            allowed_mechanism_ids=[], data_access_ready=False, llm_export_policy="public_only"),
                ())

    def fake_render_response(request_id, conversation_id, state, corpus_version, stage_label, outcome, sections):
        from pcbk_core.analytics.models import JobResponse
        return JobResponse(request_id=request_id, conversation_id=conversation_id, state=state,
                           corpus_version=corpus_version, stage_label=stage_label, sections=[],
                           clarification_keys=[], error_code=outcome.error_code)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", sync_slow)
    monkeypatch.setattr(service_mod.rendering, "render_response", fake_render_response)

    s3 = gw_an.as_("stu-03")
    conv_id = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE}).json()["id"]
    ask = s3.post(QUESTIONS_PATH.format(conversation_id=conv_id), headers={"Origin": BASE},
                 json={"client_request_id": "cr-1", "question": "вопрос", "mode": "reference"})
    request_id = ask.json()["request_id"]
    assert started.wait(timeout=2.0)
    cancel = s3.post(REQUEST_CANCEL_PATH.format(request_id=request_id), headers={"Origin": BASE})
    assert cancel.status_code == 200
    body = cancel.json()
    assert set(body) == {"request_id", "conversation_id", "state", "corpus_version", "stage_label", "sections",
                         "clarification_keys", "error_code"}
    assert body["state"] == "cancelled"
    blocked.set()


def test_real_broken_corpus_degrades_analytics_but_not_other_routes(gw_an_broken_corpus):
    """ОСОБЫЙ ФОКУС ПРОВЕРКИ №5: неисправный корпус — аналитика отвечает понятным 503, вход/уроки/калькуляторы
    живы."""
    s3 = gw_an_broken_corpus.as_("stu-03")
    caps = s3.get(CAPABILITIES_PATH)
    assert caps.status_code == 200
    assert caps.json()["reference_available"] is False
    create = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE})
    assert create.status_code == 503
    assert create.json()["name"] == "Unavailable"

    assert s3.get("/pcbk/api/auth/me").status_code == 200
    assert s3.get(LESSONS_PATH).status_code == 200
    calc = s3.post("/pcbk/api/calc/catalog_search", json={"query": "x"}, headers={"Origin": BASE})
    assert calc.status_code == 503       # служба данных не подключена в этой фикстуре — не падение, прежний отказ


def test_real_create_conversation_rejects_unknown_mode_and_extra_field(gw_an):
    s3 = gw_an.as_("stu-03")
    bad_mode = s3.post(CONVERSATIONS_PATH, json={"mode": "bogus"}, headers={"Origin": BASE})
    assert bad_mode.status_code == 400
    extra_field = s3.post(CONVERSATIONS_PATH, json={"mode": "reference", "owner": "someone-else"},
                          headers={"Origin": BASE})
    assert extra_field.status_code == 400


def test_real_ask_question_unknown_conversation_is_404(gw_an):
    s3 = gw_an.as_("stu-03")
    r = s3.post(QUESTIONS_PATH.format(conversation_id="no-such-conversation"), headers={"Origin": BASE},
               json={"client_request_id": "cr-1", "question": "вопрос", "mode": "reference"})
    assert r.status_code == 404


# --- правка F4b, итоговое ревью (H-18): сбой хранилища (не найдено/чужое ≠ хранилище недоступно) ---

NOT_READY_STORE = {"name": "Unavailable", "data": {"message": "Аналитика КДМ-1 временно недоступна"}}


def test_real_store_write_failure_on_new_question_returns_503_not_404(gw_an, monkeypatch):
    """H-18: запись в analytics.db невозможна (хранилище недоступно, не чужой/несуществующий диалог) — новый
    вопрос отвечает 503 «временно недоступна», не 404 «не найдено»."""
    s3 = gw_an.as_("stu-03")
    conv_id = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE}).json()["id"]

    monkeypatch.setattr(gw_an.role.analytics.store, "_connect", fail_connect)
    r = s3.post(QUESTIONS_PATH.format(conversation_id=conv_id), headers={"Origin": BASE},
               json={"client_request_id": "cr-1", "question": "Что такое сухость?", "mode": "reference"})
    assert (r.status_code, r.json()) == (503, NOT_READY_STORE)


def test_real_store_write_failure_on_create_conversation_returns_503(gw_an, monkeypatch):
    s3 = gw_an.as_("stu-03")
    monkeypatch.setattr(gw_an.role.analytics.store, "_connect", fail_connect)
    r = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE})
    assert (r.status_code, r.json()) == (503, NOT_READY_STORE)


def test_real_store_read_failure_returns_503_not_404_or_crash(gw_an, monkeypatch):
    """H-18: чтение существующей истории/задания при сбое хранилища — 503, не необработанный 500 (раньше эти
    маршруты вообще не перехватывали `StoreError`) и не 404 «не найдено» (ресурс существует, просто сейчас
    нечитаем)."""
    s3 = gw_an.as_("stu-03")
    conv_id = s3.post(CONVERSATIONS_PATH, json={"mode": "reference"}, headers={"Origin": BASE}).json()["id"]

    monkeypatch.setattr(gw_an.role.analytics.store, "_connect", fail_connect)
    for path in (CONVERSATIONS_PATH, CONVERSATION_PATH.format(conversation_id=conv_id),
                REQUESTS_PATH.format(request_id="r1"), EVIDENCE_PATH.format(evidence_id="a:b:c")):
        r = s3.get(path)
        assert (r.status_code, r.json()) == (503, NOT_READY_STORE), path

    r = s3.post(REQUEST_CANCEL_PATH.format(request_id="r1"), headers={"Origin": BASE})
    assert (r.status_code, r.json()) == (503, NOT_READY_STORE)


# --- Task 7, шаг 2: reviews/* — подключение настоящего ReviewStore через AnalyticsService ---

_REVIEW_KEYS = {"id", "kind", "subject_id", "old_value", "new_value", "scope", "period_start", "period_end",
                "rationale", "status", "created_by", "created_at", "decided_by", "decided_at", "comment"}


def test_real_reviews_empty_list_and_create_shape_matches_web(gw_an):
    s3 = gw_an.as_("stu-03")
    empty = s3.get(REVIEW_REQUESTS_PATH)
    assert (empty.status_code, empty.json()) == (200, {"requests": []})

    create = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                     json={"kind": "conflict", "subject_id": "CONF-FAKE-001",
                           "new_value": "рассмотреть конфликт источников", "scope": "KDM1_PCBK",
                           "rationale": "расхождение источников в ответе"})
    assert create.status_code == 201
    body = create.json()
    assert set(body) == _REVIEW_KEYS
    assert (body["status"], body["created_by"], body["decided_by"]) == ("pending", "stu-03", None)

    listed = s3.get(REVIEW_REQUESTS_PATH)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["requests"]] == [body["id"]]      # автор видит свою заявку


def test_real_create_review_rejects_missing_field_and_unknown_kind(gw_an):
    s3 = gw_an.as_("stu-03")
    missing = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE}, json={"kind": "conflict"})
    assert missing.status_code == 400
    bad_kind = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                       json={"kind": "bogus", "subject_id": "X", "new_value": "Y", "scope": "KDM1_PCBK",
                             "rationale": "Z"})
    assert bad_kind.status_code == 400


def test_real_decide_review_stranger_without_visibility_gets_404_for_any_status(gw_an):
    """Правка контроллера, 2026-10-14 (Important): посторонний без видимости (не автор, нет действующего
    полномочия на kind/scope заявки) получает ОДИНАКОВЫЙ 404 для pending/решённой/несуществующей заявки — по
    ответу нельзя различить существование или статус чужой заявки."""
    s3, teach, stranger = gw_an.as_("stu-03"), gw_an.as_("teach"), gw_an.as_("stu-05")

    pending_id = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                        json={"kind": "conflict", "subject_id": "CONF-FAKE-020", "new_value": "значение",
                              "scope": "KDM1_PCBK", "rationale": "основание"}).json()["id"]

    gw_an.role.analytics.reviews.bootstrap_authority("teach", "KDM1_PCBK", ("conflict",), operator="operator")
    decided_id = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                        json={"kind": "conflict", "subject_id": "CONF-FAKE-021", "new_value": "значение",
                              "scope": "KDM1_PCBK", "rationale": "основание"}).json()["id"]
    assert teach.post(REVIEW_DECISION_PATH.format(review_id=decided_id), headers={"Origin": BASE},
                      json={"decision": "accepted", "comment": ""}).status_code == 200

    for rid in (pending_id, decided_id, "no-such-review"):
        r = stranger.post(REVIEW_DECISION_PATH.format(review_id=rid), headers={"Origin": BASE},
                          json={"decision": "accepted", "comment": ""})
        assert r.status_code == 404, rid


def test_real_decide_review_self_decide_403_and_already_decided_409(gw_an):
    from pcbk_core.analytics.models import RequestIdentity
    svc = gw_an.role.analytics
    s3, teach = gw_an.as_("stu-03"), gw_an.as_("teach")

    create = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                     json={"kind": "conflict", "subject_id": "CONF-FAKE-022", "new_value": "значение",
                           "scope": "KDM1_PCBK", "rationale": "основание"})
    review_id = create.json()["id"]

    # оператор (офлайн, вне API) назначает teach полномочие на conflict в этой области — первое назначение
    svc.reviews.bootstrap_authority("teach", "KDM1_PCBK", ("conflict",), operator="operator")

    # автор (stu-03) не может решить свою же заявку, даже если ему тоже дать право
    teach_identity = RequestIdentity(login="teach", role="teacher", place=None, caller="teacher")
    svc.reviews.grant_authority("stu-03", "KDM1_PCBK", ("conflict",), granted_by=teach_identity)
    self_decide = s3.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                          json={"decision": "accepted", "comment": ""})
    assert self_decide.status_code == 403

    # teach (не автор, держит право) решает — 200, форма ответа та же, что у create
    decide = teach.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                        json={"decision": "accepted", "comment": "подтверждено по руководству"})
    assert decide.status_code == 200
    decided_body = decide.json()
    assert set(decided_body) == _REVIEW_KEYS
    assert (decided_body["status"], decided_body["decided_by"]) == ("accepted", "teach")

    # повторное решение уже решённой заявки держателем права — 409 Conflict
    again = teach.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                       json={"decision": "rejected", "comment": ""})
    assert again.status_code == 409


def test_real_decide_review_rejects_unknown_decision_value(gw_an):
    s3 = gw_an.as_("stu-03")
    create = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                     json={"kind": "conflict", "subject_id": "CONF-FAKE-011", "new_value": "значение",
                           "scope": "KDM1_PCBK", "rationale": "основание"})
    review_id = create.json()["id"]
    bad = s3.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                 json={"decision": "maybe", "comment": ""})
    assert bad.status_code == 400          # лишний/неверный enum — отказ pydantic (_DecideReview), не стора


def test_real_capabilities_reflects_review_authority_and_data_bridge(gw_an):
    """review_authority: действующее полномочие ЭТОГО логина, в любой области (не чужое) — поведение не менялось.
    data_bridge_available: решение контроллера (итоговая правка F1, п.2, 14.10) — ВСЕГДА False; вывод
    телеметрии (`data_bridge.py`) в ответ ждёт решения владельца по полномочиям и формату секции ответа.
    Согласованная связь оборудования и подключённый `DataService` больше НЕ включают это поле — тест явно
    проверяет, что обе прежние предпосылки одновременно выполнены, а поле всё равно False."""
    from pcbk_core.analytics.models import RequestIdentity
    svc = gw_an.role.analytics
    s3 = gw_an.as_("stu-03")

    before = s3.get(CAPABILITIES_PATH).json()
    assert (before["review_authority"], before["data_bridge_available"]) == (False, False)

    svc.reviews.bootstrap_authority("teach", "KDM1_PCBK", ("equipment_link",), operator="operator")
    assert s3.get(CAPABILITIES_PATH).json()["review_authority"] is False     # чужое полномочие — не моё право

    teach_identity = RequestIdentity(login="teach", role="teacher", place=None, caller="teacher")
    svc.reviews.grant_authority("stu-03", "KDM1_PCBK", ("local_claim",), granted_by=teach_identity)
    assert s3.get(CAPABILITIES_PATH).json()["review_authority"] is True

    stu_identity = RequestIdentity(login="stu-03", role="student", place=3, caller="student-03")
    req = svc.reviews.create_request("equipment_link", "EQ-FAKE-900", "20FAKE_001_PV", "KDM1_PCBK",
                                     "основание", created_by=stu_identity)
    svc.reviews.decide(req.id, "accepted", "", decided_by=teach_identity)
    # согласованная связь оборудования теперь есть, но calc всё ещё не подключён — False по обеим причинам
    assert s3.get(CAPABILITIES_PATH).json()["data_bridge_available"] is False
    svc.calc = object()                                                          # двойник DataService — не None
    # ОБЕ прежние предпосылки выполнены (calc подключён И согласованная связь есть) — поле всё равно False
    assert s3.get(CAPABILITIES_PATH).json()["data_bridge_available"] is False


# --- F5 (итоговое ревью, 14.10.2026, решение владельца): ANALYTICS_REVIEWERS=all_students ---

def test_real_reviews_store_failure_returns_503_not_404_or_crash(gw_an, monkeypatch):
    """F5, п.6: сбой самого SQLite хранилища согласования (`reviews.ReviewStoreUnavailable`, НЕ «заявка не
    найдена») — 503 на ВСЕХ трёх ручках `reviews/*`, не 404/необработанный 500."""
    s3 = gw_an.as_("stu-03")
    create = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                     json={"kind": "conflict", "subject_id": "CONF-FAKE-500", "new_value": "значение",
                           "scope": "KDM1_PCBK", "rationale": "основание"})
    review_id = create.json()["id"]

    monkeypatch.setattr(gw_an.role.analytics.reviews, "_connect", fail_connect)

    r_list = s3.get(REVIEW_REQUESTS_PATH)
    assert (r_list.status_code, r_list.json()) == (503, NOT_READY_STORE)

    r_create = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                       json={"kind": "conflict", "subject_id": "CONF-FAKE-501", "new_value": "значение",
                             "scope": "KDM1_PCBK", "rationale": "основание"})
    assert (r_create.status_code, r_create.json()) == (503, NOT_READY_STORE)

    r_decide = s3.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                       json={"decision": "accepted", "comment": ""})
    assert (r_decide.status_code, r_decide.json()) == (503, NOT_READY_STORE)


def test_real_all_students_mode_student_decides_any_request_including_own_no_second_approver(gw_an_all_students):
    """F5-brief, настоящий сквозной путь через HTTP: любой student без назначения решает любую заявку области,
    включая свою, в одно действие (без второго согласующего для local_claim); teacher остаётся исключением —
    без назначения получает ту же невидимость (404), что посторонний в grants."""
    s3 = gw_an_all_students.as_("stu-03")
    s5 = gw_an_all_students.as_("stu-05")
    teach = gw_an_all_students.as_("teach")

    assert s3.get(CAPABILITIES_PATH).json()["review_authority"] is True     # student — право без назначения
    assert teach.get(CAPABILITIES_PATH).json()["review_authority"] is False  # teacher — исключение

    create = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                     json={"kind": "local_claim", "subject_id": "CLAIM-FAKE-510", "new_value": "значение",
                           "scope": "KDM1_PCBK", "rationale": "основание"})
    review_id = create.json()["id"]

    # автор (s3) запрашивает сведения по СВОЕЙ заявке — в grants это было бы 403 (самозапрет), здесь 200
    info = s3.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                  json={"decision": "info_requested", "comment": "уточняю"})
    assert info.status_code == 200

    # тот же автор сам потом выносит accepted по local_claim — второй независимый согласующий (F3) не требуется
    accept = s3.post(REVIEW_DECISION_PATH.format(review_id=review_id), headers={"Origin": BASE},
                     json={"decision": "accepted", "comment": "подтверждаю"})
    assert accept.status_code == 200
    assert accept.json()["decided_by"] == "stu-03"

    # другой student без какого-либо назначения видит ЛЮБУЮ заявку области (видимость — все заявки, не только
    # свои) и решает её сам, хотя его это не касается и права ему никто не выдавал
    create2 = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                      json={"kind": "conflict", "subject_id": "CONF-FAKE-510", "new_value": "значение",
                            "scope": "KDM1_PCBK", "rationale": "основание"})
    review_id2 = create2.json()["id"]
    listed = s5.get(REVIEW_REQUESTS_PATH).json()["requests"]
    assert review_id2 in {r["id"] for r in listed}
    decide2 = s5.post(REVIEW_DECISION_PATH.format(review_id=review_id2), headers={"Origin": BASE},
                      json={"decision": "accepted", "comment": ""})
    assert (decide2.status_code, decide2.json()["decided_by"]) == (200, "stu-05")

    # teacher БЕЗ назначения — нет права даже в all_students (мастер-промпт §6 это решение владельца не меняет)
    create3 = s3.post(REVIEW_REQUESTS_PATH, headers={"Origin": BASE},
                      json={"kind": "conflict", "subject_id": "CONF-FAKE-511", "new_value": "значение",
                            "scope": "KDM1_PCBK", "rationale": "основание"})
    review_id3 = create3.json()["id"]
    teach_decide = teach.post(REVIEW_DECISION_PATH.format(review_id=review_id3), headers={"Origin": BASE},
                              json={"decision": "accepted", "comment": ""})
    assert teach_decide.status_code == 404      # та же невидимость, что у постороннего — не 403
