# Д1. Каркас стенда под наблюдением и живые проверки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** на сервере ПЦБК работает наш прокси на 8443 со страницей состояния
сторожа, две копии узкого прокси сокета Docker и десять рабочих мест OpenCode
v1.18.33 под gVisor; страница честно показывает сбои; живые проверки §11
записаны.

**Architecture:** один `compose.yaml`: `edge` (nginx, TLS на 8443), `watchdog`
(Python без сторонних библиотек: проверки, журнал SQLite, страница), `sp-ro` и
`sp-ctl` (wollomatic/socket-proxy — единственные, у кого есть `docker.sock`;
пропускают только чтение и start/stop рабочих мест по имени) и
`student-01…10` (OpenCode под `runsc`, ФС только на чтение, своя внутренняя
сеть со статическим адресом). Серверный слой, историан и OpenRouter появятся
в Д2–Д3; сторож уже знает о них и показывает «ещё не установлен».

**Tech Stack:** Docker Compose, nginx (alpine), Python 3.12 (stdlib; pytest
только в тестах), OpenCode v1.18.33 (бинарник из выпуска GitHub), ripgrep
15.1.0, gVisor release-20260921.0, wollomatic/socket-proxy 1.13.1, uv для
запуска тестов.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(редакция 3, §1, §2, §6, §7 п. 1, §8, §11, §12 п. 1–2) с уточнениями §13;
проверенные факты с источниками — [`docs/research/04-d1-facts.md`](../research/04-d1-facts.md);
дорожная карта — [`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md).

**Влезает ли в день:** да, если с утра есть SSH-ключ и `sudo` на сервере.
Задачи 1–5 не требуют сервера и идут на локальном Docker. Самое рискованное —
задача 6 (gVisor) и первая живая проверка задачи 7: если OpenCode не
запускается под `runsc`, день заканчивается записью проверки и разговором с
владельцем, а не обходом: изоляция обязательна по вопросу 15, запуск без
gVisor — решение владельца, не исполнителя.

## Global Constraints

- OpenCode — ровно **v1.18.33**, бинарник `opencode-linux-x64.tar.gz`
  (sha256 `e546123213ae47909a4268692aa4b94950d011afe9cac9938753a2194f1c16d5`);
  без AVX2 на сервере — `opencode-linux-x64-baseline.tar.gz`
  (sha256 `440ca65423e99505cf285f8660684503cc54f6f9e6b64cfcd11bf412369be6d5`).
- Все сторонние образы закреплены тегом **и** дайджестом; бинарники — sha256
  или sha512 из [`04-d1-facts.md`](../research/04-d1-facts.md).
- Секреты и производственные данные — **никогда в git**: пароли серверов
  OpenCode, сертификат и ключ TLS, `.env` выкладки, белый список и реестр
  тегов. В git — только `deploy/env.example` без значений.
- Личные учётные записи и имена сотрудников заказчика не пишутся нигде.
- Dify не трогаем: ни одного изменения в `/opt/dify`, его контейнерах и сетях;
  Docker перечитывает настройки только `systemctl reload docker`, **никогда
  `restart`** (restart гасит все контейнеры, включая Dify).
- `docker.sock` смонтирован **только** в `sp-ro` и `sp-ctl`.
- Рабочее место: `runtime: runsc`, `read_only: true`, `cap_drop: [ALL]`,
  `security_opt: [no-new-privileges:true]`, `mem_limit: 1g`, `cpus: 1.0`,
  `pids_limit: 1024` (gVisor считает потоки песочницы, не процессы гостя;
  уточняется замером в задаче 7), `restart: unless-stopped`, своя сеть
  `internal: true`, статический адрес, портов наружу нет.
- Под gVisor встроенный DNS Docker не работает: рабочее место находит соседей
  только по адресу (`extra_hosts`), никогда по имени.
- Наружу публикуется только порт `8443` контейнера `edge`.
- Всё, что видит человек, — по-русски; имена в коде — по-английски,
  комментарии — по-русски и коротко.
- Время на странице — с явным смещением от UTC; пояс задаёт администратор
  (`DISPLAY_TZ`), по умолчанию `UTC`.
- Свои вспомогательные скрипты проверок — в рабочем каталоге задания, не в
  репозитории; в репозитории — продукт, его тесты и журнал проверок.

## Review Focus

1. **Цикл проверок сторожа завис, а HTTP отвечает** — страница обязана
   показать «состояние неизвестно», а не последнее зелёное. Тест — задача 2,
   `test_status_json_marks_stale_snapshot`, `test_healthz_503_when_stale`.
2. **Прокси сокета сторожа не отвечает** — рабочие места не должны остаться
   зелёными по старым данным. Тест — задача 2,
   `test_run_checks_docker_down_marks_containers_unknown`.
3. **Контейнер рабочего места удалён или не создан** — «нет контейнера»,
   сбой, а не «спит». Тест — задача 1, `test_missing_container_is_fail`.
4. **Сторож перезапущен** — журнал не дублирует прежние состояния и не
   теряет перезапуск. Тест — задача 1, `test_journal_restart_keeps_last_states`.
5. **Сторож убит целиком** — вместо страницы nginx по умолчанию человек видит
   «Сторож не отвечает — состояние неизвестно». Тест — задача 5,
   `test_edge_shows_watchdog_down_page`.

---

## Карта файлов

```
compose.yaml                          все службы стенда
compose.test.yaml                     локальные тесты: runc вместо runsc, тестовый сертификат
deploy/env.example                    переменные выкладки без значений
deploy/README.md                      выкладка и откат для администратора
edge/nginx.conf.template              TLS 8443, /status → сторож, 502 → своя страница, :8080/healthz
edge/static/index.html                заглушка «в постройке» + предупреждение о статусе стенда
edge/static/watchdog-down.html        «Сторож не отвечает — состояние неизвестно»
watchdog/Dockerfile
watchdog/pyproject.toml
watchdog/components.json              ожидаемые компоненты и их вид
watchdog/pcbk_watchdog/checks.py      чистые функции проверок
watchdog/pcbk_watchdog/journal.py     журнал переходов, SQLite
watchdog/pcbk_watchdog/docker_api.py  чтение состояния через sp-ro
watchdog/pcbk_watchdog/page.py        снимок, JSON и HTML страницы
watchdog/pcbk_watchdog/main.py        цикл проверок и HTTP-сервер
watchdog/tests/                       модульные тесты
student/Dockerfile                    образ рабочего места
student/config/opencode.json          глобальная конфигурация OpenCode
student/config/.gitignore             обязателен: без него GET /agent → 500
student/config/tools/{bash,edit,write,apply_patch}.ts   заглушки
tests/integration/                    проверки компоновки на локальном Docker
docs/checks/D1.md          журнал живых проверок §11 и учений
docs/checks/D1/*.png                  снимки страницы состояния
```

**Адреса и сети** (одно место правды — `compose.yaml`):

| Сеть | `internal` | Кто в ней |
|---|---|---|
| `pcbk-edge` | нет | `edge`, `watchdog` (позже — серверный слой) |
| `pcbk-ro` | да | `watchdog`, `sp-ro` |
| `pcbk-ctl` | да | `sp-ctl` (с Д4 — серверный слой) |
| `pcbk-stu-NN`, NN = 01…10 | да | `student-NN` на `${STU_NET}.N.3`; серверный слой с Д3 — на `${STU_NET}.N.2` |

Подсеть рабочего места NN — `${STU_NET}.N.0/28`, шлюз `.1`, динамические
адреса только из `ip_range: ${STU_NET}.N.8/29` — чтобы одноразовый
контейнер проверки не занял `.2` или `.3`; по умолчанию `STU_NET=172.31`
(N — номер без ведущего нуля). Имена контейнеров фиксированы:
`pcbk-edge`, `pcbk-watchdog`, `pcbk-sp-ro`, `pcbk-sp-ctl`, `pcbk-student-NN`;
будущий серверный слой — `pcbk-core`.

---

### Task 1: Проверки и журнал сторожа

**Files:**
- Create: `watchdog/pyproject.toml`, `watchdog/pcbk_watchdog/__init__.py`,
  `watchdog/pcbk_watchdog/checks.py`, `watchdog/pcbk_watchdog/journal.py`,
  `watchdog/pcbk_watchdog/docker_api.py`, `watchdog/tests/conftest.py`
- Test: `watchdog/tests/test_checks.py`, `watchdog/tests/test_journal.py`,
  `watchdog/tests/test_docker_api.py`

**Interfaces:**
- Produces:
  - `State = Literal["ok", "warn", "fail", "absent", "unknown"]`
  - `@dataclass(frozen=True) class Check: component: str; title: str; state: State; detail: str`
  - `check_memory(meminfo: str, warn_mib: int = 2048, fail_mib: int = 1024) -> Check` — компонент `memory`, по полю `MemAvailable`
  - `check_container(component: str, title: str, inspect: dict | None, *, sleeping_ok: bool, prev_restarts: int | None) -> Check`
  - `check_http(component: str, title: str, url: str, timeout: float = 3.0) -> Check`
  - `not_installed(component: str, title: str) -> Check` — `state="absent"`, `detail="ещё не установлен"`
  - `class DockerUnavailable(Exception)`
  - `class DockerReader: __init__(self, base_url: str, timeout: float = 3.0); ping(self) -> bool; inspect(self, name: str) -> dict | None` — 404 → `None`; нет связи, 403/405, 5xx → `DockerUnavailable`; имя вне `pcbk-[a-z0-9-]+` → `ValueError` до запроса
  - `@dataclass(frozen=True) class Event: ts: datetime; component: str; state: State; detail: str`
  - `class Journal: __init__(self, path: str); started(self, now: datetime) -> Event; record(self, checks: list[Check], now: datetime) -> list[Event]; recent(self, limit: int = 50) -> list[Event]`

- [ ] **Step 1: Write the failing tests**

```python
# test_checks.py
MEMINFO = "MemTotal: 16384000 kB\nMemAvailable: {} kB\n"

def test_memory_ok_warn_fail():
    assert check_memory(MEMINFO.format(11_700 * 1024)).state == "ok"
    assert check_memory(MEMINFO.format(1_500 * 1024)).state == "warn"
    assert check_memory(MEMINFO.format(900 * 1024)).state == "fail"
    assert "11700 МиБ" in check_memory(MEMINFO.format(11_700 * 1024)).detail

def test_memory_without_field_is_unknown():
    assert check_memory("MemTotal: 1 kB\n").state == "unknown"

def insp(running=False, oom=False, code=0, restarts=0, restarting=False):
    return {"State": {"Running": running, "OOMKilled": oom, "ExitCode": code,
                      "Restarting": restarting}, "RestartCount": restarts}

def test_student_states():
    c = lambda i, prev=0: check_container("student-01", "Рабочее место 01", i,
                                          sleeping_ok=True, prev_restarts=prev)
    assert (c(insp(running=True)).state, c(insp(running=True)).detail) == ("ok", "работает")
    assert (c(insp(code=143)).state, c(insp(code=143)).detail) == ("ok", "спит")
    assert (c(insp(oom=True, code=137)).state, c(insp(oom=True, code=137)).detail) == ("fail", "убит по памяти")
    assert c(insp(running=True, restarts=2), prev=1).state == "warn"
    assert "перезапущен после сбоя" in c(insp(running=True, restarts=2), prev=1).detail
    assert c(insp(restarting=True)).state == "warn"

def test_infra_stopped_is_fail():
    r = check_container("sp-ctl", "Прокси сокета серверного слоя", insp(code=1),
                        sleeping_ok=False, prev_restarts=0)
    assert r.state == "fail" and "код 1" in r.detail

def test_missing_container_is_fail():
    r = check_container("student-03", "Рабочее место 03", None, sleeping_ok=True, prev_restarts=None)
    assert (r.state, r.detail) == ("fail", "нет контейнера")

def test_not_installed_is_absent_not_fail():
    assert not_installed("core", "Серверный слой").state == "absent"

def test_check_http_down_is_fail():
    assert check_http("edge", "Входной прокси", "http://127.0.0.1:9/healthz", timeout=0.5).state == "fail"

# test_docker_api.py — fake_proxy: http.server-двойник sp-ro
def test_inspect_uses_pinned_api_prefix(fake_proxy):
    DockerReader(fake_proxy.url).inspect("pcbk-student-01")
    assert fake_proxy.paths[-1] == "/v1.44/containers/pcbk-student-01/json"

def test_inspect_404_is_none(fake_proxy):
    assert DockerReader(fake_proxy.url).inspect("pcbk-student-09") is None

def test_inspect_403_raises(fake_proxy):          # прокси отказал — не «нет контейнера»
    with pytest.raises(DockerUnavailable):
        DockerReader(fake_proxy.url).inspect("pcbk-forbidden")

def test_inspect_rejects_foreign_name_without_request(fake_proxy):
    with pytest.raises(ValueError):
        DockerReader(fake_proxy.url).inspect("../../containers/create")
    assert fake_proxy.paths == []

def test_unreachable_raises():
    with pytest.raises(DockerUnavailable):
        DockerReader("http://127.0.0.1:9", timeout=0.5).inspect("pcbk-student-01")

# test_journal.py
def test_journal_writes_only_transitions(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    ok, bad = Check("edge", "Входной прокси", "ok", ""), Check("edge", "Входной прокси", "fail", "код 1")
    assert len(j.record([ok], T0)) == 1
    assert j.record([ok], T0 + timedelta(seconds=10)) == []
    assert [e.state for e in j.record([bad], T0 + timedelta(seconds=20))] == ["fail"]
    assert [e.state for e in j.record([ok], T0 + timedelta(seconds=30))] == ["ok"]

def test_journal_restart_keeps_last_states(tmp_path):
    p = str(tmp_path / "j.db")
    Journal(p).record([Check("edge", "Входной прокси", "ok", "")], T0)
    j2 = Journal(p)
    assert j2.started(T0 + timedelta(minutes=1)).component == "watchdog"
    assert j2.record([Check("edge", "Входной прокси", "ok", "")], T0 + timedelta(minutes=1)) == []
    assert [e.component for e in j2.recent()] == ["watchdog", "edge"]   # новые сверху
```

`conftest.py`: `T0` — фиксированное время с поясом UTC; `fake_proxy` —
`http.server` на свободном порту, отдаёт `/v1.44/containers/pcbk-student-01/json`
(Running: true), 404 на `pcbk-student-09`, 403 на `pcbk-forbidden`,
`/v1.44/_ping` → `OK`, запоминает пути в `paths`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — `ModuleNotFoundError: pcbk_watchdog`

- [ ] **Step 3: Implement `checks.py`, `docker_api.py`, `journal.py` по интерфейсам выше**

`docker_api` — `http.client` (он не ходит по перенаправлениям), пути с
префиксом `/v1.44` (его принимают все Docker 29.x), имя проверяется
`re.fullmatch(r"pcbk-[a-z0-9-]+", name)` до запроса. `journal` — таблица
`events(ts TEXT, component TEXT, state TEXT, detail TEXT)`; последние
состояния при открытии поднимаются из журнала; переход — смена `state` или
смена `detail` при `warn`/`fail`. Остановленное рабочее место без
`OOMKilled` — «спит» при любом коде: остановка по простою даёт 143 или 137.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: все тесты задачи PASS

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: проверки памяти, контейнеров и HTTP, журнал переходов"
```

---

### Task 2: Страница состояния и цикл сторожа

**Files:**
- Create: `watchdog/pcbk_watchdog/page.py`, `watchdog/pcbk_watchdog/main.py`,
  `watchdog/components.json`, `watchdog/Dockerfile`
- Test: `watchdog/tests/test_page.py`, `watchdog/tests/test_main.py`

**Interfaces:**
- Consumes: `Check`, `Event`, `Journal`, `DockerReader`, `DockerUnavailable`, функции проверок — задача 1.
- Produces:
  - `@dataclass(frozen=True) class Snapshot: checked_at: datetime; checks: tuple[Check, ...]`
  - `overall(snapshot: Snapshot) -> State` — худшее из не-`absent`: `fail` > `unknown` > `warn` > `ok`
  - `status_json(snapshot: Snapshot, now: datetime, stale_after_s: int) -> dict` — ключи `checked_at` (ISO с поясом), `stale` (bool), `overall`, `checks` (словари `component/title/state/detail`)
  - `render_html(snapshot: Snapshot, events: list[Event], now: datetime, stale_after_s: int, tz: ZoneInfo) -> str`
  - `load_components(path: str) -> list[dict]` — поля `id`, `title`, `kind` ∈ {`memory`, `http`, `docker_ping`, `container`, `absent`}; для `http` — `url`; для `container` — `container`, `sleeping_ok`
  - `run_checks(components: list[dict], docker: DockerReader, meminfo_path: str, prev: dict[str, int]) -> tuple[list[Check], dict[str, int]]` — второй элемент — `RestartCount` для следующего такта
  - HTTP на `:8090`: `GET /status` (HTML), `GET /status.json`, `GET /healthz` — `200` при свежем снимке, `503` при устаревшем или до первого такта
  - Переменные: `TICK_S=10`, `STALE_AFTER_S=30`, `DISPLAY_TZ=UTC`, `DOCKER_URL=http://pcbk-sp-ro:2375`, `JOURNAL_PATH=/var/lib/pcbk-watchdog/journal.db`, `MEMINFO_PATH=/proc/meminfo`, `COMPONENTS_PATH=/app/components.json`

`components.json` на Д1, по порядку: `edge` (`http`,
`http://pcbk-edge:8080/healthz`, «Входной прокси»); `sp-ro` (`docker_ping`,
«Прокси сокета сторожа»); `sp-ctl` (`container`, `pcbk-sp-ctl`,
`sleeping_ok: false`, «Прокси сокета серверного слоя»); `memory` («Память
сервера»); `student-01` … `student-10` (`container`, `pcbk-student-NN`,
`sleeping_ok: true`, «Рабочее место NN»); `core` («Серверный слой»),
`historian` («Историан БДРВ»), `llm` («OpenRouter и бюджет») — `absent`.

- [ ] **Step 1: Write the failing tests**

```python
def test_overall_ignores_absent_and_takes_worst():
    s = Snapshot(T0, (Check("a", "A", "ok", ""), Check("b", "B", "absent", "ещё не установлен"),
                      Check("c", "C", "warn", "")))
    assert overall(s) == "warn"

def test_status_json_marks_stale_snapshot():
    s = Snapshot(T0, (Check("a", "A", "ok", ""),))
    assert status_json(s, T0 + timedelta(seconds=29), 30)["stale"] is False
    assert status_json(s, T0 + timedelta(seconds=31), 30)["stale"] is True

def test_html_shows_absent_as_not_installed_and_offset():
    html = render_html(Snapshot(T0, (not_installed("core", "Серверный слой"),)), [], T0, 30,
                       ZoneInfo("Asia/Yekaterinburg"))
    assert "Серверный слой" in html and "ещё не установлен" in html
    assert "+05:00" in html

def test_html_polls_json_and_flags_silence():
    html = render_html(Snapshot(T0, ()), [], T0, 30, ZoneInfo("UTC"))
    assert "/status.json" in html and "Сторож не отвечает" in html
    assert "состояние неизвестно" in html
    assert "http://" not in html and "https://" not in html   # без внешних ресурсов

def test_run_checks_docker_down_marks_containers_unknown():
    comps = [{"id": "student-01", "title": "Рабочее место 01", "kind": "container",
              "container": "pcbk-student-01", "sleeping_ok": True}]
    checks, _ = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), MEMINFO_FILE, {})
    assert checks[0].state == "unknown" and "прокси сокета" in checks[0].detail

def test_components_file_lists_ten_students_and_three_absent():
    comps = load_components("components.json")
    assert [c["id"] for c in comps if c["kind"] == "container" and c["sleeping_ok"]] == \
           [f"student-{n:02d}" for n in range(1, 11)]
    assert {c["id"] for c in comps if c["kind"] == "absent"} == {"core", "historian", "llm"}

def test_healthz_503_when_stale(served_watchdog):   # сервер с замороженным снимком T0
    assert urlopen_status(served_watchdog + "/healthz") == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q tests/test_page.py tests/test_main.py`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `page.py` и `main.py` по интерфейсам выше**

Страница без внешних ресурсов: встроенные CSS и JS. JS каждые 5 с берёт
`/status.json` и перерисовывает список; запрос не удался или `stale: true` —
красная полоса «Сторож не отвечает с ЧЧ:ММ:СС — состояние неизвестно».
`absent` — серым «ещё не установлен», не красным. Под списком — последние 20
событий журнала. Цикл — отдельный поток; HTTP — `ThreadingHTTPServer`;
`stale` считается в момент запроса. При старте — `journal.started(now)`.

`Dockerfile`: `python:3.12-slim` по дайджесту, пользователь uid 10002 без
root, без pip-зависимостей, `components.json` копируется в `/app`, каталог
`/var/lib/pcbk-watchdog` создан в образе с владельцем 10002 (том журнала
наследует владельца), `CMD ["python", "-m", "pcbk_watchdog.main"]`.

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: страница состояния, JSON, признак устаревания, цикл проверок"
```

---

### Task 3: Сторож и две копии узкого прокси сокета в компоновке

**Files:**
- Create: `compose.yaml` (службы `watchdog`, `sp-ro`, `sp-ctl`, сети
  `pcbk-ro`, `pcbk-ctl`, том `pcbk-watchdog-journal`), `compose.test.yaml`,
  `deploy/env.example`, `tests/integration/conftest.py`
- Test: `tests/integration/test_socket_proxy.py`

**Interfaces:**
- Consumes: образ сторожа — задача 2.
- Produces:
  - `pcbk-sp-ro:2375` — клиент только `pcbk-watchdog`, только GET:
    `(/v1\.[0-9]+)?/(_ping|containers/pcbk-(student-(0[1-9]|10)|sp-ctl|edge|watchdog|core)/json)`
  - `pcbk-sp-ctl:2375` — клиент только `pcbk-core`:
    GET `(/v1\.[0-9]+)?/(_ping|containers/json|containers/pcbk-student-(0[1-9]|10)/json)`,
    POST `(/v1\.[0-9]+)?/containers/pcbk-student-(0[1-9]|10)/(start|stop)`
  - `.env` выкладки: `DOCKER_GID`, `STU_NET`, `TLS_CERT_DIR`, `TLS_CERT_FILE`,
    `TLS_KEY_FILE`, `DISPLAY_TZ`, `SECRETS_DIR`, `AGENTS_DIR`
  - фикстура `stack` (на сессию) в `tests/integration/conftest.py`:
    `docker compose -p pcbk-test -f compose.yaml -f compose.test.yaml up -d --build`
    для инфраструктуры и `create` для рабочих мест (как на сервере), в конце —
    `down -v`. Методы: `http_from_watchdog(method, url) -> int` (запрос
    `http.client` изнутри работающего `pcbk-watchdog`), `http_as(name, network, method, url) -> int`
    (одноразовый `curlimages/curl:8.16.0` с этим именем в этой сети),
    `inspect(name) -> dict`, `start(name)`, `stop(name)`, `containers() -> list[str]`

Образ `wollomatic/socket-proxy:1.13.1@sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002`;
флаги `-listenip=0.0.0.0`, `-allowfrom=<имя клиента>`, `-allowGET=…`,
`-allowPOST=…` (только у `sp-ctl`). Прокси сам дописывает `^` и `$`, поэтому
альтернативы — в скобках. Контейнеры прокси: `user: "65534:${DOCKER_GID}"`,
`read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, сокет `:ro`,
`restart: unless-stopped`, `mem_limit: 32m`. Сторож: сеть `pcbk-ro`,
`DOCKER_URL=http://pcbk-sp-ro:2375`, том журнала, `read_only: true`,
`cap_drop: [ALL]`, `no-new-privileges`, `mem_limit: 128m`,
`restart: unless-stopped`. `compose.test.yaml` добавляет мишень
`pcbk-test-foreign` (busybox `sleep`) и тестовые значения `.env`.

- [ ] **Step 1: Write the failing tests**

```python
RO = "http://pcbk-sp-ro:2375/v1.44"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
PASSED = (200, 204, 304, 404)      # прокси пропустил; ответ уже от Docker

def test_sp_ro_serves_watchdog_reads_only(stack):
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-sp-ctl/json") == 200
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-student-01/json") in PASSED
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-test-foreign/json") == 403
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-sp-ctl/logs") == 403
    assert stack.http_from_watchdog("POST", RO + "/containers/pcbk-student-01/start") == 405

def test_sp_ro_refuses_other_clients(stack):
    assert stack.http_as("pcbk-intruder", "pcbk-ro", "GET", RO + "/containers/pcbk-sp-ctl/json") == 403

def test_sp_ctl_passes_only_student_start_stop(stack):
    for verb in ("start", "stop"):
        assert stack.http_as("pcbk-core", "pcbk-ctl", "POST",
                             CTL + f"/containers/pcbk-student-01/{verb}") in PASSED
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", CTL + "/containers/pcbk-sp-ro/stop") == 403
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", CTL + "/containers/pcbk-test-foreign/json") == 403
    assert stack.http_as("pcbk-intruder", "pcbk-ctl", "GET", CTL + "/containers/json") == 403

@pytest.mark.parametrize("method,path", [
    ("POST", "/containers/create"),
    ("POST", "/containers/pcbk-student-01/exec"),
    ("POST", "/containers/pcbk-student-01/kill"),
    ("POST", "/containers/pcbk-student-01/update"),
    ("POST", "/containers/pcbk-student-01/start/../../create"),
    ("POST", "/volumes/create"),
    ("POST", "/images/create"),
    ("DELETE", "/containers/pcbk-student-01"),
])
def test_sp_ctl_refuses_dangerous_calls(stack, method, path):
    assert stack.http_as("pcbk-core", "pcbk-ctl", method, CTL + path) in (403, 405)

def test_watchdog_page_up_behind_proxy(stack):
    assert stack.http_from_watchdog("GET", "http://127.0.0.1:8090/healthz") == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_socket_proxy.py`
Expected: FAIL — нет `compose.yaml`

- [ ] **Step 3: Implement `watchdog`, `sp-ro`, `sp-ctl` в `compose.yaml`, `compose.test.yaml`, фикстуру `stack`, `deploy/env.example`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_socket_proxy.py`
Expected: PASS; после прогона `docker ps -a --filter name=pcbk-` и
`docker network ls --filter name=pcbk-` пусты

- [ ] **Step 5: Commit**

```bash
git add compose.yaml compose.test.yaml deploy/env.example tests/integration/
git commit -m "Сторож и две копии узкого прокси сокета: сторожу чтение, серверному слою start/stop рабочих мест"
```

---

### Task 4: Образ рабочего места OpenCode v1.18.33

**Files:**
- Create: `student/Dockerfile`, `student/config/opencode.json`,
  `student/config/.gitignore`, `student/config/tools/bash.ts`,
  `student/config/tools/edit.ts`, `student/config/tools/write.ts`,
  `student/config/tools/apply_patch.ts`
- Modify: `compose.yaml` (якорь `x-student`, службы `student-01…10`, сети
  `pcbk-stu-01…10`, тома), `compose.test.yaml`
- Test: `tests/integration/test_student.py`

**Interfaces:**
- Consumes: фикстура `stack` — задача 3.
- Produces:
  - образ `pcbk-reserve/student:d1`; внутри `/usr/local/bin/opencode`,
    `/usr/local/bin/rg`; пользователь `student` uid 10001
  - раскладка: `XDG_CONFIG_HOME=/etc/pcbk-opencode` (только чтение, в образе:
    `opencode/opencode.json`, `opencode/.gitignore`, `opencode/tools/*.ts`,
    пустой `opencode/agents/` — точка монтирования); `XDG_DATA_HOME`,
    `XDG_CACHE_HOME`, `XDG_STATE_HOME` — под `/var/lib/opencode` (том,
    владелец 10001); `TMPDIR=/tmp` — tmpfs **с `exec`** (иначе Bun не
    загрузит нативный модуль); рабочий каталог `/work` — том
  - переменные образа: `OPENCODE_DISABLE_MODELS_FETCH=1`,
    `OPENCODE_DISABLE_AUTOUPDATE=1`, `OPENCODE_DISABLE_SHARE=1`,
    `OPENCODE_DISABLE_LSP_DOWNLOAD=1`, `OPENCODE_DISABLE_PROJECT_CONFIG=1`,
    `OPENCODE_DISABLE_DEFAULT_PLUGINS=1`,
    `OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=true`,
    `BUN_RUNTIME_TRANSPILER_CACHE_PATH=0`, `OPENCODE_SERVER_USERNAME=opencode`;
    `OPENCODE_EXPERIMENTAL_NATIVE_LLM` не задаётся
  - запуск `opencode serve --hostname 0.0.0.0 --port 4096`; Basic
    `opencode:<OPENCODE_SERVER_PASSWORD>` из `${SECRETS_DIR}/student-NN.env`
  - `opencode.json`: `autoupdate: false`, `share: "disabled"`,
    `snapshot: false`, `enabled_providers: ["pcbk"]`, провайдер `pcbk`
    (`npm: "@ai-sdk/openai-compatible"`, `baseURL: "{env:PCBK_LLM_BASE_URL}"`,
    `apiKey: "{env:PCBK_LLM_TOKEN}"`, одна модель `stub`), `model: "pcbk/stub"`,
    `small_model: "pcbk/stub"`; ключей `mcp`, `lsp`, `formatter`, `plugin` нет
    (MCP и модели — Д3)
  - заглушка — `export default { description, args: {}, execute }` без
    импортов (пакет `@opencode-ai/plugin` не нужен и не ставится);
    `description` начинается со слов «Отключено на учебном стенде»,
    `execute` возвращает «Оболочка и правка файлов на учебном стенде
    отключены. Данные — через инструменты службы данных.»
  - каталог агентов рабочего места NN — `${AGENTS_DIR}/student-NN`
    с хоста, в контейнер `:ro` на `/etc/pcbk-opencode/opencode/agents`
  - новые методы фикстуры `stack`: `oc(student: int, method: str, path: str, auth: bool = True) -> tuple[int, bytes]`
    — запрос к OpenCode рабочего места из одноразового контейнера в его сети
    по адресу `${STU_NET}.N.3:4096`; перед первым запросом ждёт готовности
    `GET /global/health` с таймаутом 2 с и повторами до 30 с (запросы в первые
    ~3 с после открытия порта висят); `exec(name, cmd) -> str`,
    `exec_rc(name, cmd) -> int`, `write_agent(student: int, filename: str, text: str)`
    — запись в тестовый `AGENTS_DIR` с хоста
  - `compose.test.yaml`: у рабочих мест `runtime: runc` (на машине
    разработчика `runsc` нет; под `runsc` — задача 7)

Dockerfile: стадия загрузки на `ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02`
качает `opencode-linux-x64.tar.gz` v1.18.33 (или `-baseline` по
`ARG OPENCODE_VARIANT`) и `ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz`
(sha256 `1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599`),
сверяет sha256 и падает при расхождении; итоговая стадия — тот же базовый
образ без curl, только два бинарника и конфигурация.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture(scope="module", autouse=True)
def student_01_up(stack):            # поднимаем так же, как будет поднимать шлюз, — через sp-ctl
    stack.http_as("pcbk-core", "pcbk-ctl", "POST", CTL + "/containers/pcbk-student-01/start")

def test_sp_ctl_really_starts_and_stops_student(stack):
    post = lambda verb: stack.http_as("pcbk-core", "pcbk-ctl", "POST",
                                      CTL + f"/containers/pcbk-student-03/{verb}")
    assert (post("start"), post("start"), post("stop")) == (204, 304, 204)
    assert stack.inspect("pcbk-student-03")["State"]["Running"] is False

def test_student_hardening(stack):
    hc = stack.inspect("pcbk-student-01")["HostConfig"]
    assert hc["ReadonlyRootfs"] is True and hc["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in hc["SecurityOpt"] and hc["Memory"] == 1024 ** 3
    status = stack.exec("pcbk-student-01", "cat /proc/1/status")
    assert "CapEff:\t0000000000000000" in status and "NoNewPrivs:\t1" in status

def test_opencode_requires_password(stack):
    assert stack.oc(1, "GET", "/global/health", auth=False)[0] == 401
    assert stack.oc(1, "GET", "/global/health")[0] == 200

def test_stubs_replace_builtin_tools(stack):
    tools = {t["id"]: t["description"] for t in
             json.loads(stack.oc(1, "GET", "/experimental/tool?provider=pcbk&model=stub")[1])}
    for tid in ("bash", "edit", "write"):
        assert tools[tid].startswith("Отключено на учебном стенде")
    gpt = {t["id"]: t["description"] for t in
           json.loads(stack.oc(1, "GET", "/experimental/tool?provider=pcbk&model=gpt-5")[1])}
    assert gpt["apply_patch"].startswith("Отключено на учебном стенде")

def test_agent_file_visible_after_dispose(stack):
    stack.write_agent(1, "probe.md", "---\ndescription: проверка\nmode: primary\n---\nТест.\n")
    assert "probe" not in stack.oc(1, "GET", "/agent")[1].decode()
    assert stack.oc(1, "POST", "/instance/dispose")[0] == 200
    assert "probe" in stack.oc(1, "GET", "/agent")[1].decode()

def test_readonly_where_it_matters(stack):
    for path in ("/home/student/x", "/etc/pcbk-opencode/opencode/tools/x.ts",
                 "/etc/pcbk-opencode/opencode/agents/x.md", "/usr/local/bin/x"):
        assert stack.exec_rc("pcbk-student-01", f"touch {path}") != 0
    assert stack.exec_rc("pcbk-student-01", "touch /var/lib/opencode/x /work/x") == 0

def test_no_route_out_or_to_neighbour(stack):
    stack.start("pcbk-student-02")
    for host, port in (("1.1.1.1", 443), ("192.168.11.30", 1433), (f"{STU_NET}.2.3", 4096)):
        assert stack.exec_rc("pcbk-student-01",
                             f"timeout 5 bash -c 'echo > /dev/tcp/{host}/{port}'") != 0

def test_env_holds_only_own_secret(stack):
    env = stack.exec("pcbk-student-01", "cat /proc/1/environ | tr '\\0' '\\n'")
    assert "OPENCODE_SERVER_PASSWORD=" in env
    assert not re.search(r"BDRV|OPENROUTER|SSHPASS|_PW=", env)
```

`CTL` — та же константа, что в `test_socket_proxy.py` (вынести в `conftest.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student.py`
Expected: FAIL — нет службы `student-01`

- [ ] **Step 3: Implement образ, конфигурацию, заглушки и службы `student-01…10`**

Рабочие места в `compose.yaml` — через якорь `x-student` и по службе на
номер; у каждой — `container_name`, `env_file`, два тома
(`pcbk-student-NN-state:/var/lib/opencode`, `pcbk-student-NN-work:/work`),
монтирование агентов, сеть `pcbk-stu-NN` с `ipv4_address: ${STU_NET}.N.3`,
`extra_hosts: ["core:${STU_NET}.N.2"]` (понадобится с Д3),
`labels: {pcbk.role: student}`, `stop_grace_period: 10s`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student.py`
Expected: PASS (под runc; под runsc — задача 7)

- [ ] **Step 5: Commit**

```bash
git add student/ compose.yaml compose.test.yaml tests/integration/
git commit -m "Рабочее место: образ OpenCode v1.18.33, заглушки оболочки и правки файлов, десять мест"
```

---

### Task 5: Прокси входа и сборка стенда

**Files:**
- Create: `edge/nginx.conf.template`, `edge/static/index.html`,
  `edge/static/watchdog-down.html`
- Modify: `compose.yaml` (служба `edge`, сеть `pcbk-edge`, сторож — в
  `pcbk-edge`), `compose.test.yaml`
- Test: `tests/integration/test_edge.py`

**Interfaces:**
- Consumes: сторож (`:8090`, задача 2), `sp-ro` (задача 3), рабочие места (задача 4).
- Produces:
  - `https://<хост>:8443/` — заглушка «Резервный контур в постройке» с
    постоянным предупреждением о статусе стенда и ссылкой на `/status`
  - `https://<хост>:8443/status` и `/status.json` — от сторожа; `502/503/504`
    на `/status` → `watchdog-down.html` («Сторож не отвечает — состояние
    неизвестно»), `Cache-Control: no-store`
  - `http://pcbk-edge:8080/healthz` → `200 ok`, порт не публикуется
  - образ nginx — текущая стабильная ветка `nginx:*-alpine`, тег и дайджест
    закрепить при сборке; `read_only: true`, tmpfs на `/var/cache/nginx`,
    `/var/run` и `/etc/nginx/conf.d` (туда входной скрипт образа пишет конфиг
    из `/etc/nginx/templates/`), `cap_drop: [ALL]`,
    `cap_add: [CHOWN, SETUID, SETGID]`, `no-new-privileges`; сертификат
    `${TLS_CERT_DIR}` — `:ro`; `server_tokens off`
  - адрес сторожа разрешается в момент запроса (`resolver 127.0.0.11 valid=10s`
    и `proxy_pass` через переменную) — nginx стартует и работает без живого
    сторожа; `proxy_connect_timeout 3s`, `proxy_read_timeout 5s` — зависший
    сторож за 5 с даёт ту же страницу «Сторож не отвечает»

- [ ] **Step 1: Write the failing tests**

```python
def test_edge_serves_status_from_watchdog(stack):
    code, body = stack.https("/status")
    assert code == 200 and "Рабочее место 01" in body and "ещё не установлен" in body

def test_status_json_lists_ten_students_ok(stack):
    data = json.loads(stack.https("/status.json")[1])
    students = [c for c in data["checks"] if c["component"].startswith("student-")]
    assert len(students) == 10 and all(c["state"] == "ok" for c in students)

def test_root_shows_stand_warning(stack):
    assert "учебный стенд" in stack.https("/")[1].lower()

def test_edge_shows_watchdog_down_page(stack):
    stack.stop("pcbk-watchdog")
    try:
        code, body = stack.https("/status")
        assert code == 502 and "Сторож не отвечает" in body and "nginx" not in body.lower()
    finally:
        stack.start("pcbk-watchdog")

def test_edge_starts_without_watchdog(stack):
    stack.stop("pcbk-watchdog")
    try:
        stack.restart("pcbk-edge")
        assert stack.https("/status")[0] == 502
    finally:
        stack.start("pcbk-watchdog")

def test_hung_watchdog_gives_down_page_within_timeout(stack):
    stack.pause("pcbk-watchdog")
    try:
        started = time.monotonic()
        code, body = stack.https("/status")
        assert code == 504 and "Сторож не отвечает" in body and time.monotonic() - started < 10
    finally:
        stack.unpause("pcbk-watchdog")

def test_watchdog_sees_socket_proxy_loss(stack):
    stack.stop("pcbk-sp-ro")
    try:
        data = stack.wait_status(lambda d: d["overall"] == "unknown", timeout=30)
        assert any(c["component"] == "student-01" and c["state"] == "unknown" for c in data["checks"])
    finally:
        stack.start("pcbk-sp-ro")

def test_only_edge_publishes_ports(stack):
    published = {n: stack.inspect(n)["NetworkSettings"]["Ports"] for n in stack.containers()}
    assert [n for n, p in published.items() if any(p.values())] == ["pcbk-edge"]
```

Новые методы `stack`: `https(path) -> tuple[int, str]` — запрос к
`https://127.0.0.1:18443` без проверки сертификата (тестовый сертификат
фикстура делает через `openssl req -x509`); `wait_status(pred, timeout) -> dict`
— опрос `/status.json`; `restart`, `pause`, `unpause` — по имени контейнера.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_edge.py`
Expected: FAIL — нет службы `edge`

- [ ] **Step 3: Implement `edge` в `compose.yaml`, шаблон nginx и две страницы**

Сторож добавляется в сеть `pcbk-edge`; `/proc/meminfo` в контейнере под
runc показывает память сервера — отдельное монтирование не нужно.
`compose.test.yaml` публикует `18443:8443` и подставляет тестовый сертификат.

- [ ] **Step 4: Run the whole local suite**

Run: `(cd watchdog && uv run --python 3.12 --with pytest pytest -q) && uv run --python 3.12 --with pytest pytest -q tests/integration`
Expected: PASS; после прогона нет контейнеров, сетей и томов `pcbk-test`

- [ ] **Step 5: Commit**

```bash
git add edge/ compose.yaml compose.test.yaml tests/integration/
git commit -m "Прокси входа 8443: страница состояния сторожа, своя страница при его смерти"
```

---

### Task 6: gVisor и выкладка на сервер

Задача без модульных тестов: каждый шаг — команда и вывод, который значит
«прошло». Шаги с `sudo` выполняет владелец или исполнитель с его явного
согласия в этот день. Результаты — в `docs/checks/D1.md`.

**Files:**
- Create: `deploy/README.md` (эти шаги для администратора, с откатом),
  `docs/checks/D1.md`

- [ ] **Step 1: Предпроверки на сервере (без `sudo`, только чтение)**

Run (по SSH): `cat /proc/sys/kernel/yama/ptrace_scope; grep -c avx2 /proc/cpuinfo; docker info --format '{{.CgroupVersion}} {{.CgroupDriver}} {{.ServerVersion}}'; docker compose version; getent group docker; ip -4 route; docker network inspect $(docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'; grep -E '^NGINX_SSL_CERT(_KEY)?_FILENAME|^NGINX_HTTPS_ENABLED' /opt/dify/docker/.env; free -m; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d1-before.txt`
Expected: `ptrace_scope` ≤ 2; `2 systemd 29.7.2`; `avx2` > 0 — иначе
собрать образ с `OPENCODE_VARIANT=x64-baseline`; ни маршрута, ни подсети Docker
внутри `172.31.0.0/16` — иначе выбрать свободный `/16` и записать в
`STU_NET`; имена файлов сертификата записаны в `.env` выкладки.

- [ ] **Step 2: Установить gVisor release-20260921.0 (`sudo`)**

Локально скачать `gvisor.tar.zstd` и `gvisor.tar.zstd.sha512` из
`https://storage.googleapis.com/gvisor/releases/release/20260921.0/x86_64/`,
`sha512sum -c` → `OK` (sha512 `66a5b173…702475c`), скопировать на сервер.
На сервере: `sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.pre-runsc`
(если файл есть), `sudo tar --zstd -xf gvisor.tar.zstd -C /usr/local/bin`
(без `zstd` — вариант `.tar.bz2`), `sudo /usr/local/bin/runsc install -- --platform=systrap`,
`sudo systemctl reload docker`.
Expected: `docker info --format '{{json .Runtimes}}'` содержит `runsc`;
`docker run --rm --runtime=runsc hello-world` печатает «Hello from Docker!»;
`docker ps --format '{{.Names}} {{.Status}}'` — у контейнеров Dify время работы
продолжается от `~/pcbk-d1-before.txt`, ни один не перезапущен.
Откат: вернуть `daemon.json.pre-runsc`, `sudo systemctl reload docker`.

- [ ] **Step 3: Каталоги и секреты стенда (`sudo` один раз)**

`sudo install -d -o expert -g expert /opt/pcbk-reserve`, внутри
`secrets/` (700) и `agents/student-01…10/` (755). Пароли — на сервере,
в чат и git не попадают:
`for n in $(seq -w 1 10); do (umask 077; printf 'OPENCODE_SERVER_PASSWORD=%s\n' "$(openssl rand -hex 24)" > /opt/pcbk-reserve/secrets/student-$n.env); done`.
`.env` выкладки — по `deploy/env.example`.
Expected: `ls -l /opt/pcbk-reserve/secrets` — десять файлов `-rw-------`.

- [ ] **Step 4: Перенести образы и поднять стенд**

Локально: `docker compose build`; все образы стенда и `curlimages/curl:8.16.0`
для проверок — `docker save … | gzip | ssh … 'gunzip | docker load'`
(стенд не зависит от выхода сервера в интернет); `compose.yaml`, `edge/` —
`rsync` в `/opt/pcbk-reserve`. На сервере:
`docker compose up -d --no-build edge watchdog sp-ro sp-ctl && docker compose create --no-build student-01 student-02 student-03 student-04 student-05 student-06 student-07 student-08 student-09 student-10`.
Expected: `docker ps` — четыре службы `Up`; рабочие места `Created`;
`curl -sk https://127.0.0.1:8443/status.json` на сервере — `overall` не `fail`,
десять рабочих мест «спит».

- [ ] **Step 5: Commit**

```bash
git add deploy/README.md docs/checks/D1.md
git commit -m "Выкладка Д1: gVisor через reload без остановки Dify, стенд на сервере"
```

---

### Task 7: Живые проверки §11 и учения

Каждая проверка — команда, вывод, вывод-заключение строкой в
`docs/checks/D1.md` с пометкой [П]. Снимки — по SSH-туннелю
`ssh -N -L 18443:127.0.0.1:8443 …` и
`google-chrome --headless=new --ignore-certificate-errors --window-size=1200,1600 --screenshot=docs/checks/D1/<имя>.png https://127.0.0.1:18443/status`.

- [ ] **Step 1: OpenCode под gVisor с ФС только на чтение (§11 п. 1)**

`docker start pcbk-student-01`; одноразовый `curlimages/curl` в сети
`pcbk-stu-01` → `GET http://${STU_NET}.1.3:4096/global/health` с паролем.
Expected: `200`; `docker inspect -f '{{.HostConfig.Runtime}}' pcbk-student-01` →
`runsc`; `docker exec pcbk-student-01 cat /proc/version` не совпадает с
`/proc/version` хоста (gVisor показывает своё ядро); `/experimental/tool` —
заглушки на месте (как в задаче 4); ФС только на чтение — как в
`test_readonly_where_it_matters`. Если
OpenCode не поднялся — журнал контейнера в проверку, день останавливается,
разговор с владельцем.

- [ ] **Step 2: Прокси сокета на сервере (§11 п. 1)**

Прогнать матрицу задачи 3 на сервере: `sp-ctl` — одноразовым клиентом
`pcbk-core` в `pcbk-ctl`, `sp-ro` — изнутри работающего сторожа. Плюс:
`GET /v1.44/containers/docker-api-1/json` через оба прокси → `403`
(окружение контейнеров Dify закрыто), `POST …/pcbk-student-02/start` через
`sp-ctl` → `204` под `runsc`.
Expected: коды совпадают с локальными; ни одного `2xx` на create/exec/kill/delete.

- [ ] **Step 3: Агент виден после `dispose` под gVisor (§11 п. 2, без живого потока)**

Файл `probe.md` в `/opt/pcbk-reserve/agents/student-01/` с хоста →
`GET /agent` без него → `POST /instance/dispose` → `GET /agent` с ним.
Expected: как в `test_agent_file_visible_after_dispose`; файл удалить.

- [ ] **Step 4: Изоляция рабочего места**

`docker start pcbk-student-02`; из `pcbk-student-01` через
`timeout 5 bash -c 'echo > /dev/tcp/<узел>/<порт>'`: `192.168.11.30:1433`,
`1.1.1.1:443`, `${STU_NET}.2.3:4096`; окружение процесса.
Expected: все три соединения — отказ или таймаут; в окружении только свой
`OPENCODE_SERVER_PASSWORD`.

- [ ] **Step 5: Холодный старт, память, потоки (§11 п. 5)**

Три раза: `docker stop` → `docker start` → время до первого `200` на
`/global/health`. Через 60 с простоя — `docker stats --no-stream` для 01 и 02;
`pids.current` cgroup контейнера на хосте.
Expected: числа записаны; если память в простое > 700 МиБ или потоков > 700 —
отметка в журнале проверок и поправка `mem_limit`/`pids_limit` в `compose.yaml`
перед закрытием дня.

- [ ] **Step 6: Убийство по памяти под gVisor**

`docker run --name pcbk-oomt --runtime=runsc -m 128m --entrypoint python pcbk-reserve/watchdog:d1 -c "b=bytearray(512<<20)"; docker inspect -f '{{.State.OOMKilled}} {{.State.ExitCode}}' pcbk-oomt; docker rm pcbk-oomt`
(образ сторожа уже на сервере — из интернета ничего не тянем)
Expected: записать как есть. `true 137` — сторож опирается на `OOMKilled`;
`false 137` — запись в долг Д9: сторожу нужен другой признак убийства по памяти.

- [ ] **Step 7: Слушатель 3389 (§11 п. 6, только опознание)**

`sudo ss -ltnp 'sport = :3389'; systemctl is-active xrdp; dpkg -s xrdp | grep '^Version'`
Expected: процесс и пакет записаны; закрытие — вопрос владельцу и заказчику.

- [ ] **Step 8: Три учения — страница показывает сбой**

Сначала — снимок исправного состояния.
(а) сторож убит: `docker stop pcbk-watchdog` → снимок `/status`: «Сторож не
отвечает»; `start`;
(б) сторож завис: `docker pause pcbk-watchdog` → снимок `/status` не позже
чем через 10 с: та же страница; `unpause`;
(в) прокси сокета сторожа упал: `docker stop pcbk-sp-ro` → через 30 с снимок:
рабочие места «неизвестно», итог «неизвестно»; `start`.
После всех трёх — снимок восстановления.
Expected: пять снимков в `docs/checks/D1/`; в журнале сторожа — «сторож
запущен» после (а), сбой и восстановление `sp-ro` после (в).

- [ ] **Step 9: Commit**

```bash
git add docs/checks/ compose.yaml
git commit -m "Д1: живые проверки §11 и учения наблюдаемости на сервере"
```

---

### Task 8: Закрытие дня

- [ ] **Step 1:** в `docs/DESIGN-platform-2026-09-29.md` §11 — у проверенных
  пунктов пометка [П] и ссылка на журнал проверок; в `README.md` раздел
  «Состояние» — «Д1 готов», адрес страницы состояния, что дальше.
- [ ] **Step 2:** критик (Opus 5.5) по итогу дня. Блокер — любой пункт
  «Блокер дня» дорожной карты. Петля — до нуля блокеров, не больше двух
  раундов; третий — только после разговора с владельцем.
- [ ] **Step 3:** ветку дня — в `main` (fast-forward), тег `platform-d1`,
  `git push origin main platform-d1`, проверка чистым клоном: тег на месте,
  `git grep -n -I -E 'OPENCODE_SERVER_PASSWORD=[0-9a-f]{8}|BEGIN (RSA|EC|PRIVATE)'`
  пуст.
- [ ] **Step 4:** владельцу — «Д1 готов», снимки и вопросы, которые требуют
  его решения (8443 из сети ПЦБК, 3389, ключ OpenRouter к Д3).
