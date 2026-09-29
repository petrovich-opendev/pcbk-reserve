# Д1. Страница состояния на сервере и проба gVisor — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** утром на сервере ПЦБК установлен gVisor и проба подтвердила, что
OpenCode v1.18.33 работает под `runsc` в изолированной сети; к вечеру там же
работает наш прокси на 8443 со страницей состояния сторожа и две копии узкого
прокси сокета Docker, и шесть учений показывают, что страница честно
сообщает о сбоях.

**Architecture:** один `compose.yaml`: `edge` (nginx, TLS на 8443), `watchdog`
(Python без сторонних библиотек: проверки, журнал SQLite, страница), `sp-ro` и
`sp-ctl` (wollomatic/socket-proxy — единственные, у кого есть `docker.sock`).
Все сети, кроме публичной сети `edge`, — внутренние и с изолированным шлюзом:
у контейнеров нет пути ни наружу, ни к службам самого сервера. Рабочие места
(Д2), серверный слой, историан и OpenRouter (Д3–Д4) сторож уже знает и
показывает «ещё не установлен».

**Tech Stack:** Docker Compose, nginx 1.30.5 (alpine), Python 3.12 (stdlib;
pytest только в тестах), wollomatic/socket-proxy 1.13.1, gVisor
release-20260921.0, официальный образ OpenCode 1.18.33 — только для пробы,
uv для запуска тестов, google-chrome для снимков и проверки страницы.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(редакция 3: §1, §2, §7 п. 1, §8, §11, §12 п. 1–2; §13 — уточнения по
проверке фактов); факты с источниками —
[`docs/research/04-d1-facts.md`](../research/04-d1-facts.md); дорожная
карта — [`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md).
Рабочие места — следующий день:
[`DRAFT-D2-workplaces.md`](DRAFT-D2-workplaces.md).

**Влезает ли в день — оценка по часам:**

| Задача | Часы | Где |
|---|---|---|
| 0. Утренняя проба: предпроверки, gVisor, OpenCode под `runsc` | 0,75 | сервер, нужен `sudo` владельца |
| 1. Проверки и журнал сторожа | 1 | локально |
| 2. Страница и цикл сторожа | 1,5 | локально |
| 3. Сторож и прокси сокета в компоновке | 1,5 | локально |
| 4. Прокси входа | 1,5 | локально |
| 5. Выкладка | 0,75 | сервер |
| 6. Живые проверки и учения | 1 | сервер |
| 7. Закрытие дня | 0,75 | — |
| **Итого** | **8,75** | |

**Черта отсечения.** Если к шестому часу задачи 1–4 не зелёные — выкладывается
то, что зелёное; остальное в `components.json` помечено `absent`, страница
честно показывает «ещё не установлен»; недоделанное — первым делом утром Д2.
Если утром нет SSH или `sudo` — задача 0 уходит в конец дня, задачи 1–4 идут
локально, и видимый результат дня — снимки локального стенда с теми же
учениями (под runc), с пометкой «не на сервере».
Если в задаче 0 OpenCode не работает под `runsc` — день продолжается по
задачам 1–7 (страница нужна в любом случае), а владельцу в тот же час уходит
вопрос: изоляция обязательна по вопросу 15, запуск без gVisor — его решение,
не исполнителя.

## Global Constraints

- Сторонние образы — только закреплённые: в `Dockerfile` — `FROM имя:тег@sha256:…`;
  в `compose.yaml` — `имя:тег` с `pull_policy: never`, а тег к дайджесту
  привязывает `deploy/images.lock` (строка `имя:тег sha256:<дайджест индекса>`);
  тесты и выкладка берут образы только через него. Бинарники — по sha256 или
  sha512 из [`04-d1-facts.md`](../research/04-d1-facts.md).
- Секреты и производственные данные — **никогда в git и в выводе проверок**:
  ключ TLS, `.env` выкладки, адреса и учётные записи заказчика. В git —
  `deploy/env.example` без значений. Перед каждым коммитом задач 0 и 5–7:
  `git diff --cached | grep -E -i 'BEGIN [A-Z ]*PRIVATE KEY|Basic [A-Za-z0-9+/=]{16,}|-u opencode:|PASSWORD=[^$<{ ]|192\.168\.'`
  — пусто.
- **Секреты контейнерам — только файлами** с монтированием `:ro`, никогда
  через `environment`/`env_file`: `sp-ro` отдаёт сторожу inspect, а в нём
  `Config.Env`.
- Личные учётные записи и имена сотрудников заказчика не пишутся нигде.
- Dify не трогаем: ни одного изменения в `/opt/dify`, его контейнерах и сетях;
  из каталога Dify только копируются сертификат и ключ. Docker перечитывает
  настройки только `systemctl reload docker`, **никогда `restart`**.
- `docker.sock` смонтирован **только** в `sp-ro` и `sp-ctl`.
- Все сети стенда, кроме `pcbk-public`, — `internal: true` и
  `driver_opts: {com.docker.network.bridge.gateway_mode_ipv4: isolated}`
  (без этого у внутренней сети остаётся адрес на мосту хоста, и контейнер
  достаёт SSH, xrdp и nginx Dify); `gateway` не задаётся; у каждой сети
  `name:` без префикса проекта.
- Наружу публикуется только `8443` контейнера `edge`.
- Всё, что видит человек, — по-русски; имена в коде — по-английски,
  комментарии — по-русски и коротко.
- Время на странице — с явным смещением от UTC; пояс — `DISPLAY_TZ`, по
  умолчанию `UTC`.
- Свои вспомогательные скрипты проверок — в рабочем каталоге задания, не в
  репозитории; в репозитории — продукт, его тесты и журнал проверок.

## Review Focus

1. **Цикл проверок сторожа завис, а HTTP отвечает** — открытая и заново
   загруженная страница показывает красную полосу «Сторож не отвечает —
   состояние неизвестно». Тесты — задача 2 `test_html_stale_snapshot_shows_banner`,
   задача 4 `test_browser_flags_silent_loop`.
2. **Прокси сокета сторожа упал или завис** — сам прокси красный, всё, что
   видно только через него, «неизвестно», такт не длиннее срока устаревания.
   Тесты — задача 2 `test_run_checks_docker_down_marks_containers_unknown`,
   `test_tick_bounded_when_proxy_hangs`.
3. **Контейнер не может стартовать** (сломан runtime, ошибка OCI) — сбой
   «не запускается», а не «спит». Тест — задача 1 `test_failed_start_is_fail`.
4. **Сторож перезапущен** — журнал не дублирует состояния и не теряет
   перезапуск. Тест — задача 1 `test_journal_restart_keeps_last_states`.
5. **Сторож убит целиком или nginx стартует без него** — человек видит
   «Сторож не отвечает — состояние неизвестно», а не страницу nginx.
   Тесты — задача 4 `test_edge_shows_watchdog_down_page`,
   `test_edge_starts_without_watchdog`.

---

## Карта файлов

```
compose.yaml                          службы стенда Д1
compose.test.yaml                     локальные тесты: порт 18443, тестовые пути
deploy/env.example                    переменные выкладки без значений
deploy/images.lock                    имя:тег → дайджест для сторонних образов
deploy/README.md                      выкладка, откат, что открывается наружу
edge/pcbk.conf.template               только server-блоки: 8443 TLS и 8080 healthz
edge/static/index.html                заглушка «в постройке» + предупреждение о статусе стенда
edge/static/watchdog-down.html        «Сторож не отвечает — состояние неизвестно»
watchdog/Dockerfile
watchdog/pyproject.toml               pytest: pythonpath = ["."]
watchdog/components.json              ожидаемые компоненты и их вид
watchdog/pcbk_watchdog/checks.py      чистые функции проверок
watchdog/pcbk_watchdog/journal.py     журнал переходов, SQLite
watchdog/pcbk_watchdog/docker_api.py  чтение состояния через sp-ro
watchdog/pcbk_watchdog/page.py        снимок, JSON и HTML страницы
watchdog/pcbk_watchdog/main.py        цикл проверок и HTTP-сервер
watchdog/tests/                       модульные тесты
tests/integration/                    проверки компоновки на локальном Docker
docs/checks/D1.md                     журнал живых проверок
docs/checks/D1/*.png                  снимки страницы состояния
```

**Сети** (одно место правды — `compose.yaml`; рабочие места добавит Д2):

| Сеть | Вид | Кто в ней |
|---|---|---|
| `pcbk-public` | обычная | только `edge` — ради публикации 8443 |
| `pcbk-front` | внутренняя, изолированный шлюз | `edge`, `watchdog` (с Д3 — `pcbk-core`) |
| `pcbk-ro` | внутренняя, изолированный шлюз | `watchdog`, `sp-ro` |
| `pcbk-ctl` | внутренняя, изолированный шлюз | `sp-ctl` (с Д5 — `pcbk-core`) |

Имена контейнеров фиксированы: `pcbk-edge`, `pcbk-watchdog`, `pcbk-sp-ro`,
`pcbk-sp-ctl`; рабочие места — `pcbk-student-01…10` (Д2); серверный слой —
`pcbk-core` (Д3). Образ сторожа — `pcbk-reserve/watchdog:d1`.

---

### Task 0: Утренняя проба на сервере

Первым делом, пока остальное пишется локально: снимает главный риск дня.
Команды — по SSH; шаги с `sudo` выполняет владелец или исполнитель с его
явного согласия в этот день. Итоги — в `docs/checks/D1.md` строками с
пометкой [П]; адреса заказчика, имена учётных записей и содержимое секретов
в журнал не пишутся.

**Files:**
- Create: `docs/checks/D1.md`, `deploy/images.lock`

- [ ] **Step 1: Предпроверки (без `sudo`, только чтение)**

Run: `cat /proc/sys/kernel/yama/ptrace_scope; docker info --format '{{.CgroupVersion}} {{.CgroupDriver}} {{.ServerVersion}} {{.SecurityOptions}}'; systemctl show -p ExecReload -p ActiveEnterTimestamp docker; stat -c '%y' /etc/docker/daemon.json; docker compose version; getent group docker | cut -d: -f3; ss -ltn 'sport = :8443'; ip -4 route; docker network inspect $(docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'; grep -E '^NGINX_SSL_CERT(_KEY)?_FILENAME=' /opt/dify/docker/.env; free -m; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d1-before.txt`
Expected: `ptrace_scope` ≤ 2; `2 systemd 29.7.2`, в `SecurityOptions` нет
`userns`; у `ExecReload` есть `kill -s HUP`; `daemon.json` не менялся после
`ActiveEnterTimestamp` (иначе reload применит и чужие отложенные правки —
стоп, вопрос владельцу); 8443 свободен; подсеть пробы `172.31.250.0/28` ни
с чем не пересекается.

- [ ] **Step 2: Установить gVisor release-20260921.0 (`sudo`)**

Локально: скачать `gvisor.tar.zstd` и `.sha512` из
`https://storage.googleapis.com/gvisor/releases/release/20260921.0/x86_64/`,
`sha512sum -c` → `OK`, скопировать на сервер. На сервере:
`sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.pre-runsc` (если
файла нет — записать, что откат = удаление), `sudo tar --zstd -xf gvisor.tar.zstd -C /usr/local/bin`
(без `zstd` — `.tar.bz2`, sha512 `7c899979…df563e`),
`sudo /usr/local/bin/runsc install -- --platform=systrap`,
`sudo dockerd --validate --config-file /etc/docker/daemon.json`,
`sudo systemctl reload docker`.
Expected: `validate` — `configuration OK`; `docker info --format '{{json .Runtimes}}'`
содержит `runsc`; `docker ps --format '{{.Names}} {{.Status}}'` — у каждого
контейнера Dify время работы продолжает `~/pcbk-d1-before.txt`.
Откат: убрать контейнеры с `runsc`, вернуть или удалить `daemon.json`,
`sudo systemctl reload docker`.

- [ ] **Step 3: Проба OpenCode под `runsc` в изолированной сети**

Локально: `ghcr.io/anomalyco/opencode:1.18.33@sha256:ee31dff80f8347b4705317de808b01fce1cfbf35cff560ddc573cda9718be0f7`
и `curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6`
— `pull` по дайджесту, `tag` на имя:тег, `docker save имя:тег | gzip | ssh … 'gunzip | docker load'`;
обе строки — в `deploy/images.lock`. На сервере:
сеть `pcbk-probe` (`--internal`, `-o com.docker.network.bridge.gateway_mode_ipv4=isolated`,
`--subnet 172.31.250.0/28`); контейнер `pcbk-probe-oc` — `--runtime=runsc`
`--read-only --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001`
`--tmpfs /tmp:exec,mode=1777`, `HOME`/`XDG_*` на `/tmp`,
`OPENCODE_DISABLE_MODELS_FETCH=1`, `OPENCODE_SERVER_PASSWORD=probe`,
`--ip 172.31.250.3`, команда `serve --hostname 0.0.0.0 --port 4096`.
Проверки: из одноразового `curl` в той же сети — `GET /global/health` с
`opencode:probe`, до 30 с с повторами; `docker exec pcbk-probe-oc cat /proc/version`
против `/proc/version` хоста; порты — одноразовым `curlimages/curl` тоже под
`--runtime=runsc` в сети `pcbk-probe`:
`curl -sv --connect-timeout 3 -m 4 telnet://<адрес>:<порт>` на адрес хоста в
ЛВС (`hostname -I`) — 22, 80, 443, 3389; адрес историана (только `BDRV_HOST`
из `/opt/dify/scripts/.bdrv.env` через `grep`, пароль в оболочку не
читается) — 1433; `1.1.1.1:443`.
Expected: `200`; `/proc/version` у пробы — ядро gVisor, не 5.15 хоста; ни в
одном выводе `curl` нет строки `Connected to`. Затем `docker rm -f pcbk-probe-oc`,
`docker network rm pcbk-probe`.

- [ ] **Step 4: Убийство по памяти под `runsc` (сведения для Д2 и Д11)**

`docker run --name pcbk-oomt --runtime=runsc -m 64m --entrypoint sh ghcr.io/anomalyco/opencode:1.18.33 -c 'tail /dev/zero'; docker inspect -f '{{.State.OOMKilled}} {{.State.ExitCode}}' pcbk-oomt; docker rm pcbk-oomt`
Expected: записать как есть (`true 137` или иное) — от этого зависит, как
сторож в Д11 назовёт причину перезапуска.

- [ ] **Step 5: Commit** (после проверки на секреты из Global Constraints)

```bash
git add docs/checks/D1.md deploy/images.lock
git commit -m "Д1: утренняя проба — gVisor через reload, OpenCode под runsc в изолированной сети"
```

---

### Task 1: Проверки и журнал сторожа

**Files:**
- Create: `watchdog/pyproject.toml` (`[tool.pytest.ini_options] pythonpath = ["."]`),
  `watchdog/pcbk_watchdog/__init__.py`, `watchdog/pcbk_watchdog/checks.py`,
  `watchdog/pcbk_watchdog/journal.py`, `watchdog/pcbk_watchdog/docker_api.py`,
  `watchdog/tests/conftest.py`
- Test: `watchdog/tests/test_checks.py`, `watchdog/tests/test_journal.py`,
  `watchdog/tests/test_docker_api.py`

**Interfaces:**
- Produces:
  - `State = Literal["ok", "warn", "fail", "absent", "unknown"]`
  - `@dataclass(frozen=True) class Check: component: str; title: str; state: State; detail: str`
  - `check_memory(meminfo: str, warn_mib: int = 2048, fail_mib: int = 1024) -> Check` — компонент `memory`, по полю `MemAvailable`
  - `check_container(component: str, title: str, inspect: dict | None, *, sleeping_ok: bool, now: datetime) -> Check`
  - `check_http(component: str, title: str, url: str, timeout: float = 3.0) -> Check`
  - `not_installed(component: str, title: str) -> Check` — `state="absent"`, `detail="ещё не установлен"`
  - `RECENT_RESTART = timedelta(minutes=15)`
  - `class DockerUnavailable(Exception)`
  - `class DockerReader: __init__(self, base_url: str, timeout: float = 3.0); ping(self) -> None; inspect(self, name: str) -> dict | None` —
    `ping` без ответа `OK` бросает `DockerUnavailable`; `inspect`: 404 → `None`;
    нет связи, таймаут, 403/405, 5xx → `DockerUnavailable`; имя вне
    `pcbk-[a-z0-9-]+` → `ValueError` до запроса
  - `@dataclass(frozen=True) class Event: ts: datetime; component: str; state: State; detail: str`
  - `class Journal: __init__(self, path: str); started(self, now: datetime) -> Event; record(self, checks: list[Check], now: datetime) -> list[Event]; recent(self, limit: int = 50) -> list[Event]`

Правило `check_container` (решает, что человек увидит на странице):

| Состояние Docker | Итог |
|---|---|
| нет контейнера (`None`) | `fail` «нет контейнера» |
| `Restarting` | `warn` «перезапускается» |
| `Running`, `RestartCount` > 0 и `StartedAt` моложе `RECENT_RESTART` | `warn` «перезапущен после сбоя в ЧЧ:ММ (N с последнего запуска)» |
| `Running`, иначе | `ok` «работает» (при `RestartCount` > 0 — «работает, сбоев с последнего запуска: N») |
| остановлен, `OOMKilled` | `fail` «убит по памяти» |
| остановлен, `State.Error` не пуст | `fail` «не запускается: <первые 80 знаков ошибки>» |
| остановлен, `sleeping_ok`, код ∈ {0, 137, 143} | `ok` «спит» |
| остановлен, иначе | `fail` «остановлен, код N» |

`RestartCount` Docker обнуляет при ручном запуске (`daemon/start.go:177`
в moby v29.7.2) и увеличивает только при перезапуске по политике — отдельная
память сторожу не нужна.

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

def insp(running=False, oom=False, code=0, restarts=0, restarting=False, error="",
         started=T0 - timedelta(hours=1)):
    return {"State": {"Running": running, "OOMKilled": oom, "ExitCode": code,
                      "Restarting": restarting, "Error": error,
                      "StartedAt": started.isoformat()}, "RestartCount": restarts}

def stu(i):
    return check_container("student-01", "Рабочее место 01", i, sleeping_ok=True, now=T0)

def test_student_states():
    assert (stu(insp(running=True)).state, stu(insp(running=True)).detail) == ("ok", "работает")
    for code in (0, 137, 143):
        assert (stu(insp(code=code)).state, stu(insp(code=code)).detail) == ("ok", "спит")
    assert (stu(insp(oom=True, code=137)).state, stu(insp(oom=True, code=137)).detail) == ("fail", "убит по памяти")
    assert stu(insp(restarting=True)).state == "warn"

def test_recent_policy_restart_is_warn_then_ok():
    fresh = stu(insp(running=True, restarts=2, started=T0 - timedelta(minutes=3)))
    assert fresh.state == "warn" and "перезапущен после сбоя" in fresh.detail
    old = stu(insp(running=True, restarts=2, started=T0 - timedelta(minutes=30)))
    assert old.state == "ok" and "сбоев с последнего запуска: 2" in old.detail

def test_failed_start_is_fail():
    r = stu(insp(code=127, error="OCI runtime create failed: unknown runtime runsc"))
    assert r.state == "fail" and r.detail.startswith("не запускается: OCI runtime create failed")

def test_odd_exit_code_is_fail_even_for_student():
    assert stu(insp(code=1)).state == "fail" and "код 1" in stu(insp(code=1)).detail

def test_infra_stopped_is_fail():
    r = check_container("sp-ctl", "Прокси сокета серверного слоя", insp(code=0),
                        sleeping_ok=False, now=T0)
    assert r.state == "fail" and "код 0" in r.detail

def test_missing_container_is_fail():
    r = check_container("student-03", "Рабочее место 03", None, sleeping_ok=True, now=T0)
    assert (r.state, r.detail) == ("fail", "нет контейнера")

def test_not_installed_is_absent_not_fail():
    assert not_installed("core", "Серверный слой").state == "absent"

def test_check_http_down_is_fail():
    assert check_http("edge", "Входной прокси", "http://127.0.0.1:9/healthz", timeout=0.5).state == "fail"

# test_docker_api.py — fake_proxy: http.server-двойник sp-ro
def test_inspect_uses_pinned_api_prefix(fake_proxy):
    DockerReader(fake_proxy.url).inspect("pcbk-sp-ctl")
    assert fake_proxy.paths[-1] == "/v1.44/containers/pcbk-sp-ctl/json"

def test_inspect_404_is_none(fake_proxy):
    assert DockerReader(fake_proxy.url).inspect("pcbk-student-09") is None

def test_inspect_403_raises(fake_proxy):          # прокси отказал — это не «нет контейнера»
    with pytest.raises(DockerUnavailable):
        DockerReader(fake_proxy.url).inspect("pcbk-forbidden")

def test_inspect_rejects_foreign_name_without_request(fake_proxy):
    with pytest.raises(ValueError):
        DockerReader(fake_proxy.url).inspect("../../containers/create")
    assert fake_proxy.paths == []

def test_ping_and_unreachable(fake_proxy):
    DockerReader(fake_proxy.url).ping()                 # не бросает
    with pytest.raises(DockerUnavailable):
        DockerReader("http://127.0.0.1:9", timeout=0.5).ping()

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
`http.server` на свободном порту: `/v1.44/containers/pcbk-sp-ctl/json` →
inspect работающего контейнера, `pcbk-student-09` → 404, `pcbk-forbidden` →
403, `/v1.44/_ping` → `OK`; пишет пути в `paths`, адрес — в `url`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — `ModuleNotFoundError: pcbk_watchdog`

- [ ] **Step 3: Implement `checks.py`, `docker_api.py`, `journal.py` по интерфейсам и таблице выше**

`docker_api` — `http.client` (не ходит по перенаправлениям), префикс `/v1.44`
(его принимают все Docker 29.x). `journal` — таблица
`events(ts TEXT, component TEXT, state TEXT, detail TEXT)`; последние
состояния поднимаются из журнала при открытии; переход — смена `state` или
смена `detail` при `warn`/`fail`.

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
- Consumes: всё из задачи 1.
- Produces:
  - `@dataclass(frozen=True) class Snapshot: checked_at: datetime; checks: tuple[Check, ...]`
  - `overall(snapshot: Snapshot) -> State` — худшее из не-`absent`: `fail` > `unknown` > `warn` > `ok`
  - `status_json(snapshot: Snapshot, now: datetime, stale_after_s: int) -> dict` — ключи `checked_at` (ISO с поясом), `stale` (bool), `overall`, `checks` (словари `component/title/state/detail`)
  - `render_html(snapshot: Snapshot | None, events: list[Event], now: datetime, stale_after_s: int, tz: ZoneInfo) -> str` —
    полоса «Сторож не отвечает — состояние неизвестно» с `id="silence"`
    выводится **видимой**, если снимка нет или он устарел, и с атрибутом
    `hidden` — если свеж; JS каждые 5 с берёт `/status.json` и показывает
    полосу при ошибке запроса или `stale: true`
  - `load_components(path: str) -> list[dict]` — поля `id`, `title`, `kind` ∈ {`memory`, `http`, `docker_ping`, `container`, `absent`}; для `http` — `url`; для `container` — `container`, `sleeping_ok`
  - `run_checks(components: list[dict], docker: DockerReader, meminfo_path: str, now: datetime) -> list[Check]` —
    `docker_ping` без ответа → `fail` «не отвечает»; после первого
    `DockerUnavailable` за такт все проверки вида `container` получают
    `unknown` «нет связи с прокси сокета» без запросов к Docker; исключение в
    одной проверке → её `unknown` «ошибка проверки: <тип>», такт продолжается
  - `class WatchState: snapshot: Snapshot | None` (под замком) и
    `make_server(state: WatchState, journal: Journal, port: int, stale_after_s: int, tz: ZoneInfo) -> ThreadingHTTPServer` —
    `GET /status` (HTML), `GET /status.json`, `GET /healthz` — `200` при
    свежем снимке, `503` при устаревшем или до первого такта
  - Переменные: `TICK_S=10`, `STALE_AFTER_S=30`, `DISPLAY_TZ=UTC`,
    `DOCKER_URL=http://pcbk-sp-ro:2375`, `DOCKER_TIMEOUT_S=3`,
    `MEM_WARN_MIB=2048`, `MEM_FAIL_MIB=1024`,
    `JOURNAL_PATH=/var/lib/pcbk-watchdog/journal.db`, `MEMINFO_PATH=/proc/meminfo`,
    `COMPONENTS_PATH=/app/components.json`
  - образ `pcbk-reserve/watchdog:d1`: `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`,
    uid 10002, каталог `/var/lib/pcbk-watchdog` с владельцем 10002 (том
    журнала наследует владельца), `HEALTHCHECK` на `/healthz` раз в 30 с,
    `CMD ["python", "-m", "pcbk_watchdog.main"]`

`components.json` на Д1, по порядку: `edge` (`http`,
`http://pcbk-edge:8080/healthz`, «Входной прокси»); `sp-ro` (`docker_ping`,
«Прокси сокета сторожа»); `sp-ctl` (`container`, `pcbk-sp-ctl`,
`sleeping_ok: false`, «Прокси сокета серверного слоя»); `memory` («Память
сервера»); `student-01` … `student-10` («Рабочее место NN»), `core`
(«Серверный слой»), `historian` («Историан БДРВ»), `llm` («OpenRouter и
бюджет») — все `absent`. Д2 переводит рабочие места в `container`.

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

def banner(html):
    return re.search(r'<[^>]*id="silence"[^>]*>', html).group(0)

def test_html_stale_snapshot_shows_banner():
    s = Snapshot(T0, (Check("a", "A", "ok", ""),))
    assert "hidden" in banner(render_html(s, [], T0 + timedelta(seconds=29), 30, ZoneInfo("UTC")))
    assert "hidden" not in banner(render_html(s, [], T0 + timedelta(seconds=31), 30, ZoneInfo("UTC")))
    assert "hidden" not in banner(render_html(None, [], T0, 30, ZoneInfo("UTC")))

def test_html_shows_absent_as_not_installed_and_offset():
    html = render_html(Snapshot(T0, (not_installed("core", "Серверный слой"),)), [], T0, 30,
                       ZoneInfo("Asia/Yekaterinburg"))
    assert "Серверный слой" in html and "ещё не установлен" in html and "+05:00" in html

def test_html_polls_json_without_external_resources():
    html = render_html(Snapshot(T0, ()), [], T0, 30, ZoneInfo("UTC"))
    assert "/status.json" in html and "состояние неизвестно" in html
    assert "http://" not in html and "https://" not in html

def test_run_checks_docker_down_marks_containers_unknown():
    comps = [{"id": "sp-ro", "title": "Прокси сокета сторожа", "kind": "docker_ping"},
             {"id": "sp-ctl", "title": "Прокси сокета серверного слоя", "kind": "container",
              "container": "pcbk-sp-ctl", "sleeping_ok": False}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), MEMINFO_FILE, T0)
    assert [(c.component, c.state) for c in checks] == [("sp-ro", "fail"), ("sp-ctl", "unknown")]
    assert "прокси сокета" in checks[1].detail

def test_tick_bounded_when_proxy_hangs(silent_proxy):     # принимает соединение и молчит
    comps = [{"id": f"s{n}", "title": "x", "kind": "container", "container": f"pcbk-student-{n:02d}",
              "sleeping_ok": True} for n in range(1, 11)]
    started = time.monotonic()
    run_checks(comps, DockerReader(silent_proxy, timeout=0.5), MEMINFO_FILE, T0)
    assert time.monotonic() - started < 1.5              # один таймаут и запас

def test_run_checks_survives_check_exception(monkeypatch):
    monkeypatch.setattr(checks_mod, "check_memory", lambda *a, **k: 1 / 0)
    out = run_checks([{"id": "memory", "title": "Память сервера", "kind": "memory"}],
                     DockerReader("http://127.0.0.1:9", 0.5), MEMINFO_FILE, T0)
    assert out[0].state == "unknown" and "ZeroDivisionError" in out[0].detail

def test_components_file_d1():
    comps = load_components("components.json")
    assert [c["id"] for c in comps][:4] == ["edge", "sp-ro", "sp-ctl", "memory"]
    assert {c["id"] for c in comps if c["kind"] == "absent"} == \
           {f"student-{n:02d}" for n in range(1, 11)} | {"core", "historian", "llm"}

def test_healthz_follows_freshness():
    state = WatchState(); srv = start_test_server(state)   # make_server на свободном порту
    assert http_status(srv, "/healthz") == 503            # до первого такта
    state.snapshot = Snapshot(now_utc(), ())
    assert http_status(srv, "/healthz") == 200
    state.snapshot = Snapshot(now_utc() - timedelta(seconds=60), ())
    assert http_status(srv, "/healthz") == 503
```

`conftest.py` дополняется: `MEMINFO_FILE` (временный файл), `silent_proxy`
(сокет, который принимает соединения и не отвечает), `start_test_server`,
`http_status`, `now_utc`, `checks_mod` — модуль, из которого `run_checks`
берёт `check_memory`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q tests/test_page.py tests/test_main.py`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `page.py`, `main.py`, `components.json`, `Dockerfile` по интерфейсам выше**

Страница без внешних ресурсов: встроенные CSS и JS. `absent` — серым,
`unknown` — жёлтым с пометкой «неизвестно», `fail` — красным. Под списком —
последние 20 событий журнала. Цикл — отдельный поток, `try/except` на весь
такт с записью «ошибка такта» в журнал; `stale` считается в момент запроса.
При старте — `journal.started(now)`.

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: страница состояния с полосой молчания, ограниченный такт, образ"
```

---

### Task 3: Сторож и две копии узкого прокси сокета в компоновке

**Files:**
- Create: `compose.yaml` (службы `watchdog`, `sp-ro`, `sp-ctl`, сети
  `pcbk-front`, `pcbk-ro`, `pcbk-ctl`, том `pcbk-watchdog-journal`),
  `compose.test.yaml`, `deploy/env.example`, `tests/integration/conftest.py`
- Modify: `deploy/images.lock` (+ `wollomatic/socket-proxy:1.13.1
  sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002`,
  `busybox` для мишени тестов — тег и дайджест на день выполнения)
- Test: `tests/integration/test_socket_proxy.py`

**Interfaces:**
- Consumes: образ сторожа — задача 2; `deploy/images.lock` — задача 0.
- Produces:
  - `pcbk-sp-ro:2375` — клиент только `pcbk-watchdog`, только GET:
    `(/v1\.[0-9]+)?/(_ping|containers/pcbk-(student-(0[1-9]|10)|sp-ctl)/json)`
  - `pcbk-sp-ctl:2375` — клиент только `pcbk-core`:
    GET `(/v1\.[0-9]+)?/(_ping|containers/pcbk-student-(0[1-9]|10)/json)`,
    POST `(/v1\.[0-9]+)?/containers/pcbk-student-(0[1-9]|10)/(start|stop)`;
    **списка контейнеров нет**: `GET /containers/json` отдаёт все контейнеры
    сервера вместе с `Command` (у Redis в Dify там пароль), а строку запроса
    прокси не фильтрует
  - `.env` выкладки: `DOCKER_GID`, `STU_NET`, `TLS_DIR`, `DISPLAY_TZ`,
    `SECRETS_DIR`, `AGENTS_DIR`
  - фикстура `stack` (на сессию) в `tests/integration/conftest.py`: готовит
    образы из `deploy/images.lock` (`pull` по дайджесту, `tag`), пишет
    `test.env` во временный каталог (`DOCKER_GID` из
    `getent group docker | cut -d: -f3`, тестовые пути) и во всех вызовах
    использует `docker compose -p pcbk-test --env-file <test.env> -f compose.yaml -f compose.test.yaml`;
    `up -d --build`, в конце `down -v`. Методы:
    `http_from_watchdog(method, url) -> int` (запрос `http.client` изнутри
    `pcbk-watchdog`), `http_as(name, network, method, url) -> int`
    (одноразовый `curlimages/curl` с этим именем в этой сети, `--path-as-is`),
    `inspect(name) -> dict`, `network(name) -> dict`,
    `host_bridge_addresses(*names) -> list[str]` (адреса IPv4 на мостах этих
    сетей на хосте), `containers() -> list[str]`; `start`, `stop`, `restart`,
    `unpause` возвращаются **только после готовности** (сторож — `/healthz`
    200 изнутри; `edge` — ответ на `:8080/healthz`)

Контейнеры прокси: образ `wollomatic/socket-proxy:1.13.1`, флаги
`-listenip=0.0.0.0`, `-allowfrom=<имя клиента>`, `-allowGET=…`, у `sp-ctl`
ещё `-allowPOST=…` (прокси сам дописывает `^…$` — альтернативы в скобках);
`user: "65534:${DOCKER_GID}"`, `read_only: true`, `cap_drop: [ALL]`,
`no-new-privileges`, сокет `:ro`, `restart: unless-stopped`, `mem_limit: 32m`.
Сторож: сети `pcbk-front` и `pcbk-ro`, том журнала, `read_only: true`,
`cap_drop: [ALL]`, `no-new-privileges`, `mem_limit: 128m`,
`restart: unless-stopped`, переменные задачи 2 через `${VAR:-умолчание}`
(учения меняют `TICK_S` и `MEM_WARN_MIB`). `compose.test.yaml` добавляет
мишень `pcbk-test-foreign` (busybox `sleep`).

- [ ] **Step 1: Write the failing tests**

```python
RO = "http://pcbk-sp-ro:2375/v1.44"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
PASSED = (200, 204, 304, 404)      # прокси пропустил; ответ уже от Docker

def test_sp_ro_serves_watchdog_reads_only(stack):
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-sp-ctl/json") == 200
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-student-01/json") in PASSED
    for path in ("/containers/pcbk-test-foreign/json", "/containers/pcbk-watchdog/json",
                 "/containers/pcbk-sp-ctl/logs", "/containers/json", "/events"):
        assert stack.http_from_watchdog("GET", RO + path) == 403
    assert stack.http_from_watchdog("POST", RO + "/containers/pcbk-student-01/start") == 405

def test_sp_ro_refuses_other_clients(stack):
    assert stack.http_as("pcbk-intruder", "pcbk-ro", "GET", RO + "/containers/pcbk-sp-ctl/json") == 403

def test_sp_ctl_passes_only_student_start_stop(stack):
    for verb in ("start", "stop"):
        assert stack.http_as("pcbk-core", "pcbk-ctl", "POST",
                             CTL + f"/containers/pcbk-student-01/{verb}") in PASSED
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", CTL + "/containers/pcbk-sp-ro/stop") == 403
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", CTL + "/containers/pcbk-test-foreign/json") == 403
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", CTL + "/containers/json") == 403
    assert stack.http_as("pcbk-intruder", "pcbk-ctl", "GET", CTL + "/_ping") == 403

@pytest.mark.parametrize("method,path", [
    ("POST", "/containers/create"),
    ("POST", "/containers/pcbk-student-01/exec"),
    ("POST", "/containers/pcbk-student-01/kill"),
    ("POST", "/containers/pcbk-student-01/update"),
    ("POST", "/containers/pcbk-student-01/start/../../create"),
    ("POST", "/containers/pcbk-student-01%2F..%2F..%2Fcreate"),
    ("POST", "/volumes/create"),
    ("POST", "/images/create"),
    ("DELETE", "/containers/pcbk-student-01"),
])
def test_sp_ctl_refuses_dangerous_calls(stack, method, path):
    assert stack.http_as("pcbk-core", "pcbk-ctl", method, CTL + path) in (403, 405)

def test_stack_networks_cut_off_host(stack):
    for net in ("pcbk-front", "pcbk-ro", "pcbk-ctl"):
        n = stack.network(net)
        assert n["Internal"] is True
        assert n["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert stack.host_bridge_addresses("pcbk-front", "pcbk-ro", "pcbk-ctl") == []

def test_secrets_not_in_container_env(stack):
    for name in ("pcbk-watchdog", "pcbk-sp-ro", "pcbk-sp-ctl"):
        env = "\n".join(stack.inspect(name)["Config"]["Env"] or [])
        assert not re.search(r"PASSWORD|SECRET|TOKEN|KEY=", env)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_socket_proxy.py`
Expected: FAIL — нет `compose.yaml`

- [ ] **Step 3: Implement `compose.yaml`, `compose.test.yaml`, фикстуру `stack`, `deploy/env.example` по интерфейсам выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_socket_proxy.py`
Expected: PASS; после прогона `docker ps -a --filter name=pcbk-` и
`docker network ls --filter name=pcbk-` пусты

- [ ] **Step 5: Commit**

```bash
git add compose.yaml compose.test.yaml deploy/ tests/integration/
git commit -m "Сторож и две копии узкого прокси сокета в изолированных сетях"
```

---

### Task 4: Прокси входа

**Files:**
- Create: `edge/pcbk.conf.template`, `edge/static/index.html`,
  `edge/static/watchdog-down.html`
- Modify: `compose.yaml` (служба `edge`, сеть `pcbk-public`),
  `compose.test.yaml`, `deploy/images.lock` (+ `nginx:1.30.5-alpine
  sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94`)
- Test: `tests/integration/test_edge.py`

**Interfaces:**
- Consumes: сторож (`:8090`, задачи 2–3), фикстура `stack` — задача 3.
- Produces:
  - `https://<хост>:8443/` — заглушка «Резервный контур в постройке» с
    постоянным предупреждением о статусе стенда и ссылкой на `/status`
  - `https://<хост>:8443/status` и `/status.json` — от сторожа;
    `502/503/504` на `/status` → `watchdog-down.html` («Сторож не отвечает —
    состояние неизвестно») с тем же кодом; `Cache-Control: no-store`
  - `http://pcbk-edge:8080/healthz` → `200 ok`, не публикуется
  - `edge/pcbk.conf.template` — только server-блоки (образ кладёт шаблон в
    `conf.d` внутри `http{}`); сторож разрешается в момент запроса
    (`resolver 127.0.0.11 valid=10s`, `proxy_pass` через переменную) — nginx
    стартует без сторожа; `proxy_connect_timeout 3s`, `proxy_read_timeout 5s`
  - контейнер: `read_only: true`, tmpfs на `/var/cache/nginx`, `/var/run`,
    `/etc/nginx/conf.d`; `cap_drop: [ALL]`, `cap_add: [CHOWN, SETUID, SETGID]`,
    `no-new-privileges`; `${TLS_DIR}` (`cert.pem`, `key.pem`) — `:ro`. Ключ
    читает мастер nginx от root без `DAC_OVERRIDE`, поэтому ключ должен
    принадлежать root (на сервере — задача 5) или быть `0644` (только
    тестовый ключ фикстуры)
  - `compose.test.yaml`: `ports: !override ["127.0.0.1:18443:8443"]`
  - новые методы `stack`: `https(path) -> tuple[int, str]` — запрос к
    `https://127.0.0.1:18443` без проверки сертификата (тестовый сертификат —
    `openssl req -x509`, ключ `chmod 0644`); `wait_status(pred, timeout) -> dict`
    — опрос `/status.json`; `pause(name)`; `run_frozen_watchdog() -> str` —
    `docker run` образа сторожа в сети `pcbk-ro` с `TICK_S=3600`,
    `STALE_AFTER_S=5`, публикацией `127.0.0.1:18090:8090`, удаляется в конце
    сессии; `chrome_dom(url, virtual_time_ms) -> str` —
    `google-chrome --headless=new --dump-dom --virtual-time-budget=<мс>`

- [ ] **Step 1: Write the failing tests**

```python
def test_edge_serves_status_from_watchdog(stack):
    code, body = stack.https("/status")
    assert code == 200 and "Прокси сокета сторожа" in body and "ещё не установлен" in body

def test_status_json_overall_ok(stack):
    data = stack.wait_status(lambda d: d["overall"] == "ok", timeout=30)
    assert {c["component"] for c in data["checks"] if c["state"] == "absent"} >= {"core", "student-01"}

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

def test_browser_flags_silent_loop(stack):
    url = stack.run_frozen_watchdog()        # первый такт прошёл, дальше тишина при живом HTTP
    time.sleep(7)
    dom = stack.chrome_dom(url + "/status", virtual_time_ms=8000)
    assert re.search(r'id="silence"(?![^>]*hidden)', dom) and "Сторож не отвечает" in dom

def test_watchdog_sees_socket_proxy_loss(stack):
    stack.stop("pcbk-sp-ro")
    try:
        data = stack.wait_status(lambda d: d["overall"] == "fail", timeout=30)
        states = {c["component"]: c["state"] for c in data["checks"]}
        assert states["sp-ro"] == "fail" and states["sp-ctl"] == "unknown"
    finally:
        stack.start("pcbk-sp-ro")

def test_only_edge_publishes_one_port(stack):
    published = {n: [p for p in (stack.inspect(n)["NetworkSettings"]["Ports"] or {}).values() if p]
                 for n in stack.containers()}
    assert {n: len(p) for n, p in published.items() if p} == {"pcbk-edge": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_edge.py`
Expected: FAIL — нет службы `edge`

- [ ] **Step 3: Implement `edge` в `compose.yaml`, шаблон nginx и две страницы**

- [ ] **Step 4: Run the whole local suite**

Run: `(cd watchdog && uv run --python 3.12 --with pytest pytest -q) && uv run --python 3.12 --with pytest pytest -q tests/integration`
Expected: PASS; после прогона нет контейнеров, сетей и томов `pcbk-test`

- [ ] **Step 5: Commit**

```bash
git add edge/ compose.yaml compose.test.yaml deploy/images.lock tests/integration/
git commit -m "Прокси входа 8443: страница сторожа, своя страница при его смерти и зависании"
```

---

### Task 5: Выкладка на сервер

Шаги — команды и вывод, который значит «прошло». Итоги — в `docs/checks/D1.md`.

**Files:**
- Create: `deploy/README.md` — эти шаги для администратора: выкладка, откат,
  что `8443` открывается в обход ufw и закрывается `docker compose stop edge`,
  что при обновлении сертификата Dify копию надо повторить
- Modify: `docs/checks/D1.md`

- [ ] **Step 1: Каталоги и сертификат (`sudo` один раз)**

`sudo install -d -o expert -g expert /opt/pcbk-reserve`;
`sudo install -d -o root -g root -m 0755 /opt/pcbk-reserve/tls`;
сертификат и ключ Dify (имена из задачи 0, шаг 1) —
`sudo install -o root -g root -m 0644 <сертификат> /opt/pcbk-reserve/tls/cert.pem`,
`sudo install -o root -g root -m 0600 <ключ> /opt/pcbk-reserve/tls/key.pem`.
`.env` выкладки — по `deploy/env.example`, `chmod 600`.
Expected: `sudo ls -l /opt/pcbk-reserve/tls` — `root root`, ключ `-rw-------`.

- [ ] **Step 2: Перенести образы**

Локально `docker compose build`; для образа сторожа и каждой строки
`deploy/images.lock`: `docker save имя:тег | gzip | ssh … 'gunzip | docker load'`.
На сервере для каждого образа `docker image inspect -f '{{.Id}}' имя:тег`
равен локальному.
Expected: все ID совпали.

- [ ] **Step 3: Поднять стенд**

`rsync -a compose.yaml edge …:/opt/pcbk-reserve/` (без `--delete`: `tls/`,
`.env` и будущие `secrets/`, `agents/` не трогаются). На сервере:
`docker compose up -d --no-build`.
Expected: `docker ps` — `pcbk-edge`, `pcbk-watchdog`, `pcbk-sp-ro`,
`pcbk-sp-ctl` в `Up`; `curl -sk https://127.0.0.1:8443/status.json` на
сервере — `overall: ok`; у контейнеров Dify время работы не сбросилось.

- [ ] **Step 4: Commit** (после проверки на секреты из Global Constraints)

```bash
git add deploy/README.md docs/checks/D1.md
git commit -m "Выкладка Д1: страница состояния на сервере"
```

---

### Task 6: Живые проверки и учения

Снимки — по SSH-туннелю `ssh -N -L 18443:127.0.0.1:8443 …` и
`google-chrome --headless=new --ignore-certificate-errors --virtual-time-budget=8000 --window-size=1200,1600 --screenshot=docs/checks/D1/<имя>.png https://127.0.0.1:18443/status`.
Итог каждой проверки — строкой в `docs/checks/D1.md` с пометкой [П].

- [ ] **Step 1: Прокси сокета на сервере**

Матрица задачи 3: `sp-ctl` — одноразовым клиентом `pcbk-core` в `pcbk-ctl`,
`sp-ro` — изнутри сторожа. Плюс через оба прокси:
`GET /v1.44/containers/docker-api-1/json` → `403`, `GET /v1.44/containers/json` → `403`.
Expected: коды совпадают с локальными; ни одного `2xx` на create, exec, kill,
delete и на контейнеры Dify.

- [ ] **Step 2: Сети стенда не видят сервер**

`docker network inspect pcbk-front pcbk-ro pcbk-ctl` — `Internal: true`,
`gateway_mode_ipv4: isolated`; у их мостов на хосте нет адресов; из
одноразового `curl` в `pcbk-ctl` тем же способом, что в задаче 0, — адрес
хоста в ЛВС на 22 и 443, `1.1.1.1:443`.
Expected: ни в одном выводе нет `Connected to`.

- [ ] **Step 3: Слушатель 3389 (§11 п. 6, только опознание)**

`sudo ss -ltnp 'sport = :3389'; systemctl is-active xrdp; dpkg -s xrdp | grep '^Version'`
Expected: процесс и пакет записаны; закрытие — вопрос владельцу и заказчику.

- [ ] **Step 4: 8443 из сети ПЦБК (§11 п. 3)**

Владелец просит человека в сети ПЦБК открыть `https://ai-lab.pcbk.ru:8443/status`.
Expected: [П] «открылась» — вход А подтверждён; «не открылась» — вход Б,
вопрос владельцу; нет человека до конца дня — строка «не проверено, ждёт
владельца» и перенос в «Отклонения» дорожной карты.

- [ ] **Step 5: Шесть учений — страница показывает сбой**

Сначала — снимок исправного состояния. Затем, со снимком после каждого:
(а) `docker stop pcbk-watchdog` → «Сторож не отвечает»; `start`;
(б) `docker pause pcbk-watchdog` → через ≤ 10 с та же страница; `unpause`;
(в) цикл молчит: `TICK_S=3600 docker compose up -d --no-build watchdog`, через
40 с — красная полоса «состояние неизвестно»; `docker compose up -d --no-build watchdog`;
(г) `docker stop pcbk-sp-ro` → «Прокси сокета сторожа — не отвечает»,
«Прокси сокета серверного слоя — неизвестно», итог — сбой; `start`;
(д) `docker stop pcbk-sp-ctl` → «Прокси сокета серверного слоя — остановлен»; `start`;
(е) `MEM_WARN_MIB=1000000 docker compose up -d --no-build watchdog` →
«Память сервера — предупреждение»; вернуть.
В конце — снимок восстановления.
Expected: восемь снимков в `docs/checks/D1/`; в журнале сторожа — «сторож
запущен» после (а), (в), (е), сбой и восстановление после (г) и (д).

- [ ] **Step 6: Commit** (после проверки на секреты)

```bash
git add docs/checks/
git commit -m "Д1: живые проверки и шесть учений наблюдаемости на сервере"
```

---

### Task 7: Закрытие дня

- [ ] **Step 1:** `docs/DESIGN-platform-2026-09-29.md` §11 — у проверенных
  пунктов пометка [П] и ссылка на `docs/checks/D1.md`; `README.md`, раздел
  «Состояние» — «Д1 готов», адрес страницы состояния, что дальше;
  `docs/plans/DRAFT-D2-workplaces.md` — поправки по итогам пробы.
- [ ] **Step 2:** критик (Opus 5.5) по итогу дня. Блокер — любой пункт
  «Блокер дня» дорожной карты. Петля — до нуля блокеров, не больше двух
  раундов; третий — только после разговора с владельцем.
- [ ] **Step 3:** ветку дня — в `main` (fast-forward), тег `platform-d1`,
  проверка на секреты по всей истории ветки, `git push origin main platform-d1`,
  чистый клон: тег на месте, `deploy/images.lock` и снимки на месте.
- [ ] **Step 4:** владельцу — «Д1 готов», снимки и только вопросы, которые
  требуют его решения (8443 из сети ПЦБК, если не проверено; 3389; итог
  пробы gVisor, если он плохой).
