"""«Мои агенты» (Д7б, Д7-R64): GET /pcbk/api/agents — встроенные агенты образа и агенты своего места с состояниями,
own и limit. Место — только из учётки сессии; зона, ошибка настройки (у маршрута свои front_only и configured, m24),
сессия, преподаватель и сбой учёта — отказы JSON вида OpenCode. Строки учёта тест пишет напрямую (файл агента ручке не
нужен); воркер перезагрузки припаркован — его проход менял бы состояния строк посреди теста."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import BASE, FRONT, PLACE_NET, Gw, fail_connect, make_gateway, park_reload_worker   # Gw — общий с Д9а
from pcbk_core.agents.store import MAX_AGENTS
from pcbk_core.gateway.agents_api import AGENTS_PATH, IMAGE_AGENTS
from pcbk_core.gateway.api import GW_MESSAGES

REPO = Path(__file__).resolve().parents[2]      # m11: в helpers его нет; чистая копия — с student (и web)
EGRESS = ("172.31.250.81", 40000)               # pcbk-egress — не front
TEACHER_TEXT = "Агенты студентов — в кабинете преподавателя, только на чтение"      # Д10-R100


@pytest.fixture
def gw(tmp_path):
    app, role = make_gateway(tmp_path)
    park_reload_worker(role)
    assert role.agents.open(), role.agents.last_error              # таблица agents — до первой строки теста
    with TestClient(app, base_url=BASE, client=FRONT):              # lifespan роли — на весь тест
        yield Gw(app, role)


def names(body: dict) -> list[str]:
    return [a["name"] for a in body["agents"]]


def test_agents_api_states(gw):                                                      # Review Focus 3
    gw.store_save(3, "a-visible", state="visible"); gw.store_save(3, "a-pending", state="saved")
    gw.store_save(3, "a-broken", state="quarantined"); gw.store_save(3, "a-gone", state="archived")
    body = gw.as_("stu-03").get(AGENTS_PATH).json()
    assert [(a["name"], a["kind"], a["state"]) for a in body["agents"]] == [
        ("pomoshnik", "builtin", "ready"), ("konstruktor", "builtin", "ready"), ("a-visible", "own", "ready"),
        ("a-pending", "own", "pending"), ("a-broken", "own", "broken")]
    assert (body["own"], body["limit"]) == (3, 20)


def test_agents_api_own_place_only(gw):                                              # Review Focus 2
    gw.store_save(3, "mine", state="visible")
    assert "mine" not in {a["name"] for a in gw.as_("stu-05").get(AGENTS_PATH).json()["agents"]}


def test_agents_api_refusals(gw):
    r = gw.anon().get(AGENTS_PATH)                                                   # тело 401 — JSON шлюза (ревью Б2)
    assert (r.status_code, r.headers["content-type"], r.json()) == \
        (401, "application/json", {"name": "Unauthorized", "data": {"message": GW_MESSAGES["need_login"]}})
    assert GW_MESSAGES["need_login"] == "Нужно войти"
    r = gw.as_("teach").get(AGENTS_PATH)
    assert (r.status_code, r.json()["data"]["message"]) == (403, TEACHER_TEXT)
    assert gw.as_("stu-03", client=PLACE_NET).get(AGENTS_PATH).status_code == 404


def test_image_agents_match_image():
    assert {n for n, _, _ in IMAGE_AGENTS} == {p.stem for p in (REPO / "student/config/agent").glob("*.md")}


def test_image_permission_denies_all_first():                           # итоговое ревью Д7б, I-1: без Docker
    perm = json.loads((REPO / "student/config/opencode.json").read_text("utf-8"))["permission"]
    assert list(perm)[0] == "*" and perm["*"] == "deny"            # агент без своих правил — только правила стенда


# --- сверх брифа ---

def test_agents_api_contract(gw):                                                   # R64 побайтно; строгий разбор веба
    gw.store_save(3, "sushka-bdm2", state="visible", title="Сушка БДМ-2", purpose="Текущие значения и сводка за смену")
    r = gw.as_("stu-03").get(AGENTS_PATH)
    assert (r.status_code, r.headers["content-type"]) == (200, "application/json")
    assert r.json() == {"agents": [
        {"name": "pomoshnik", "title": "Помощник по данным", "purpose": "текущие значения и сводки тегов БДРВ",
         "kind": "builtin", "state": "ready"},
        {"name": "konstruktor", "title": "Конструктор агентов",
         "purpose": "за один разговор собирает вашего агента по узлу", "kind": "builtin", "state": "ready"},
        {"name": "sushka-bdm2", "title": "Сушка БДМ-2", "purpose": "Текущие значения и сводка за смену", "kind": "own",
         "state": "ready"}], "own": 1, "limit": MAX_AGENTS}
    assert MAX_AGENTS == 20


def test_agents_api_empty_place(gw):                                                 # своих нет — только образ
    body = gw.as_("stu-03").get(AGENTS_PATH).json()
    assert (names(body), body["own"], body["limit"]) == (["pomoshnik", "konstruktor"], 0, 20)


def test_agents_api_not_visible_and_unknown_state_broken(gw):                       # R64: not_visible → broken
    gw.store_save(3, "nv", state="not_visible"); gw.store_save(3, "odd", state="чужое")   # иное — не «готов»
    body = gw.as_("stu-03").get(AGENTS_PATH).json()
    assert [(a["name"], a["state"]) for a in body["agents"][2:]] == [("nv", "broken"), ("odd", "broken")]
    assert body["own"] == 2


def test_agents_api_order_by_record_not_name(gw):                                   # старые первыми, не по алфавиту
    for name in ("zeta", "alpha", "mid"):
        gw.store_save(3, name, state="visible")
    assert names(gw.as_("stu-03").get(AGENTS_PATH).json())[2:] == ["zeta", "alpha", "mid"]


def test_agents_api_place_from_session_only(gw):                                    # параметров у ручки нет
    gw.store_save(3, "mine", state="visible"); gw.store_save(5, "theirs", state="visible")
    gw.store_save(5, "theirs-2", state="saved")
    s3, s5 = gw.as_("stu-03"), gw.as_("stu-05")
    assert names(s3.get(AGENTS_PATH).json())[2:] == ["mine"]                         # положительный контроль
    body = s5.get(AGENTS_PATH).json()
    assert (names(body)[2:], body["own"]) == (["theirs", "theirs-2"], 2)
    for query in ("?place=03", "?place=3&login=stu-03"):
        assert names(s5.get(AGENTS_PATH + query, headers={"X-Pcbk-Place": "03"}).json())[2:] == ["theirs", "theirs-2"]
    assert names(s3.get(AGENTS_PATH + "?place=05").json())[2:] == ["mine"]


def test_agents_api_teacher_403_shape_and_no_store_read(gw, monkeypatch):        # у преподавателя места нет — и чтения
    monkeypatch.setattr(gw.role.agents, "list_place", lambda place: pytest.fail("учёт читается для преподавателя"))
    r = gw.as_("teach").get(AGENTS_PATH)
    assert (r.status_code, r.json()) == (403, {"name": "Forbidden", "data": {"message": TEACHER_TEXT}})
    assert "www-authenticate" not in r.headers


def test_agents_api_store_failure_503(gw, monkeypatch):                             # m11: сбой учёта — 503 store
    gw.store_save(3, "mine", state="visible")
    s3 = gw.as_("stu-03")
    assert s3.get(AGENTS_PATH).status_code == 200                                    # положительный контроль
    monkeypatch.setattr(gw.role.agents, "_connect", fail_connect)                    # SQLite заперт → StoreError
    r = s3.get(AGENTS_PATH)
    assert (r.status_code, r.json()) == (503, {"name": "Unavailable", "data": {"message": GW_MESSAGES["store"]}})
    assert "mine" not in r.text
    r = gw.as_("teach").get(AGENTS_PATH)                                             # преподавателю — по-прежнему 403
    assert (r.status_code, r.json()["data"]["message"]) == (403, TEACHER_TEXT)


def test_agents_api_store_never_opened_503(tmp_path):                  # учёт не открылся — 503, а вход жив (П13)
    app, role = make_gateway(tmp_path)
    park_reload_worker(role)
    role.agents.db_path = str(tmp_path / "missing" / "core.db")                      # open() не создаст: каталога нет
    with TestClient(app, base_url=BASE, client=FRONT):
        gw = Gw(app, role)
        r = gw.as_("stu-03").get(AGENTS_PATH)
    assert (r.status_code, r.json()["data"]["message"]) == (503, GW_MESSAGES["store"])


def test_agents_api_config_error_503(gw):                                  # m24: при config_error ручка не отвечает
    gw.store_save(3, "mine", state="visible")
    s3, t = gw.as_("stu-03"), gw.as_("teach")                                        # сессии — до ошибки настройки
    assert s3.get(AGENTS_PATH).status_code == 200                                    # положительный контроль
    gw.role.config_error = "GW_ORIGIN не задан"
    for who, c in (("student", s3), ("teacher", t), ("anon", gw.anon())):            # и без сессии — 503, не 401
        r = c.get(AGENTS_PATH)
        assert (r.status_code, r.json()) == (503, {"name": "Unavailable",
                                                   "data": {"message": "Шлюз не настроен: GW_ORIGIN не задан"}}), who
        assert "mine" not in r.text, who


def test_agents_api_zone_before_session(gw):                                # вне pcbk-front — 404 раньше 401 и 503
    assert gw.anon().get(AGENTS_PATH).status_code == 401                             # положительный контроль: front
    for client in (PLACE_NET, EGRESS):
        for c in (gw.anon(client), gw.as_("stu-03", client=client)):
            r = c.get(AGENTS_PATH)
            assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), client
    gw.role.config_error = "GW_ORIGIN не задан"                                      # зона — раньше ошибки настройки
    assert gw.anon(PLACE_NET).get(AGENTS_PATH).status_code == 404


def test_agents_api_other_methods_404_without_allow(gw):                           # отказ — обработчиком шлюза
    s3 = gw.as_("stu-03")
    assert s3.get(AGENTS_PATH).status_code == 200
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        r = s3.request(method, AGENTS_PATH, headers={"Origin": BASE})
        assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), method
        assert "allow" not in r.headers, method
    assert s3.get(AGENTS_PATH + "/x").status_code == 404                             # подпутей нет


def test_agents_path():
    assert AGENTS_PATH == "/pcbk/api/agents"


def image_description(name: str) -> str:
    """description фронтматтера файла агента образа."""
    front = (REPO / "student/config/agent" / f"{name}.md").read_text("utf-8").split("---\n", 2)[1]
    return re.search(r'^description: "([^"\n]+)"$', front, re.M).group(1)


def test_image_agent_titles_match_image_descriptions():                          # названия — те же, что в образе
    for name, title, _ in IMAGE_AGENTS:
        assert image_description(name).startswith(f"{title}: "), name
    _, title, purpose = IMAGE_AGENTS[0]                                              # у помощника и назначение — оттуда
    assert image_description("pomoshnik") == f"{title}: {purpose}"


def web_builtin_titles(texts: str) -> dict[str, str]:
    """BUILTIN_AGENT_TITLES из web/src/texts.ts: пары ["имя", "название"] внутри new Map([...]) — простым разбором."""
    block = re.search(r"export const BUILTIN_AGENT_TITLES\b[^=]*= new Map\(\[(.*?)\]\);", texts, re.S)
    assert block is not None, "BUILTIN_AGENT_TITLES не найден в web/src/texts.ts"
    pairs = re.findall(r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\]', block.group(1))
    assert len(pairs) == block.group(1).count("["), "запись BUILTIN_AGENT_TITLES не того вида"
    return dict(pairs)


def test_web_parser_self_check():                                                  # положительный контроль разборщика
    sample = ('export const BUILTIN_AGENT_TITLES: ReadonlyMap<string, string> = new Map([\n  ["a", "Б в"],\n'
              '  ["c", "Д"],\n]);\n')
    assert web_builtin_titles(sample) == {"a": "Б в", "c": "Д"}


def test_image_agent_titles_match_web():                                           # Д7-R65: веб и шлюз — одни названия
    web = REPO / "web"
    if not web.is_dir():
        pytest.skip("в копии нет web/ — сверка с вебом идёт в копии с web")
    assert web_builtin_titles((web / "src/texts.ts").read_text("utf-8")) == {n: t for n, t, _ in IMAGE_AGENTS}
