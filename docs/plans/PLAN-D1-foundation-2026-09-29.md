# Д1. Страница состояния на сервере и проба gVisor — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** утром на сервере ПЦБК установлен gVisor и проба подтвердила, что
OpenCode v1.18.33 — та сборка, которую повезёт Д2, — работает под `runsc` в
изолированной сети; к вечеру там же работает наш прокси на 8443 со страницей
состояния сторожа и две копии узкого прокси сокета Docker, и семь учений
показывают, что страница честно сообщает о сбоях.

**Architecture:** один `compose.yaml`: `edge` (nginx, TLS на 8443), `watchdog`
(Python без сторонних библиотек: проверки, журнал SQLite, страница и
`status.js`), `sp-ro` и `sp-ctl` (wollomatic/socket-proxy — единственные, у
кого есть `docker.sock`). Все сети, кроме публичной сети `edge`, —
внутренние и с изолированным шлюзом; у публичной сети выключен маскарад:
единственный контейнер с путём к хосту — `edge`, и наружу он не ходит.
Рабочие места (Д2), серверный слой, историан и OpenRouter (Д3–Д4) сторож
уже знает и показывает «ещё не установлен».

**Tech Stack:** Docker Compose, nginx 1.30.5 (alpine), Python 3.12 (stdlib;
pytest только в тестах), JavaScript без библиотек (тесты — `node --test`,
Node 22), wollomatic/socket-proxy 1.13.1, gVisor release-20260921.0,
OpenCode 1.18.33 (бинарник выпуска — только для пробы), uv, google-chrome.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(редакция 3: §1, §2, §7 п. 1, §8, §11, §12 п. 1–2; §13 — уточнения по
проверке фактов); факты с источниками —
[`docs/research/04-d1-facts.md`](../research/04-d1-facts.md); дорожная
карта — [`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md).
Рабочие места — следующий день:
[`DRAFT-D2-workplaces.md`](DRAFT-D2-workplaces.md).

**Влезает ли в день — оценка по часам.** Задача 0 идёт параллельно
задачам 1–2 и в критический путь не входит.

| Задача | Часы | Где |
|---|---|---|
| 0. Утренняя проба: предпроверки, gVisor, OpenCode под `runsc` | (0,75 параллельно) | сервер, нужен `sudo` владельца |
| 1. Проверки и журнал сторожа | 1 | локально |
| 2. Страница, `status.js` и цикл сторожа | 2 | локально |
| 3. Сторож и прокси входа в компоновке | 1,5 | локально |
| 4. Две копии узкого прокси сокета | 1,5 | локально |
| 5. Выкладка | 0,75 | сервер |
| 6. Живые проверки и учения | 1,25 | сервер |
| 7. Закрытие дня | 1,5 | — |
| **Критический путь** | **9,5** | |

**Черта отсечения — седьмой час.** К ней первыми зеленеют сторож и `edge`
(задачи 1–3): без `edge` страницу на сервере не открыть. Если к седьмому часу
не зелёна задача 4 — выкладываются сторож и `edge`, `sp-ro` и `sp-ctl` в
`components.json` помечены `absent`, задача 4 — первым делом утром Д2. Если
к седьмому часу не зелёна задача 3 — выкладки нет, видимый результат —
снимки локального стенда с учениями (ветка ниже). После черты в утро Д2
первыми переносятся учения (д)–(е) и опознание 3389.
Если утром нет SSH или `sudo` — задача 0 уходит в конец дня, задачи 1–4 идут
локально, видимый результат — снимки локального стенда
(`docker compose -p pcbk-local --env-file <постоянный env в рабочем каталоге задания> up -d`)
с теми же учениями под runc, с пометкой «не на сервере».
Если в задаче 0 OpenCode не работает под `runsc` — день продолжается по
задачам 1–7, а владельцу в тот же час уходит вопрос: изоляция обязательна по
вопросу 15, запуск без gVisor — его решение, не исполнителя.

## Global Constraints

- Сторонние образы — только закреплённые: в `Dockerfile` — `FROM имя:тег@sha256:…`;
  в `compose.yaml` — `имя:тег` с `pull_policy: never`; тег к дайджесту
  привязывает `deploy/images.lock` (строка `имя:тег sha256:<дайджест индекса>`,
  тестовые образы помечены `# test`, на сервер не возятся), тесты и выкладка
  берут образы только через него; `latest` запрещён. Бинарники — по sha256
  или sha512 из [`04-d1-facts.md`](../research/04-d1-facts.md).
- Секреты и данные заказчика — **никогда в git и в выводе проверок**: ключ
  TLS, `.env` выкладки, адреса, имена хостов и учётных записей заказчика.
  Адреса в командах — только в переменных оболочки, в журнал — только
  вердикт. Перед каждым коммитом задач 0 и 5–7:
  `git diff --cached -U0 -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто,
  где `<шаблоны>` — файл в рабочем каталоге задания (не в git): адреса
  сервера и историана, имя хоста, учётные записи заказчика и общие шаблоны
  `BEGIN [A-Z ]*PRIVATE KEY`, `Basic [A-Za-z0-9+/=]{16,}`, `PASSWORD=[^$<{ "]`.
- **Секреты контейнерам — только файлами** с монтированием `:ro`, никогда
  через `environment`/`env_file`: `sp-ro` отдаёт сторожу inspect, а в нём
  `Config.Env`.
- Личные учётные записи и имена сотрудников заказчика не пишутся нигде;
  учётная запись ОС в командах — `$(id -un)`.
- Dify не трогаем: ни одного изменения в `/opt/dify`, его контейнерах и сетях;
  из каталога Dify только копируются сертификат и ключ. Docker перечитывает
  настройки только `systemctl reload docker`, **никогда `restart`**.
- `docker.sock` смонтирован **только** в `sp-ro` и `sp-ctl`.
- Сети стенда — с закреплёнными подсетями (таблица ниже) и `name:` без
  префикса проекта. Все, кроме `pcbk-public`, — `internal: true` и
  `driver_opts: {com.docker.network.bridge.gateway_mode_ipv4: isolated}`,
  `gateway` не задаётся; у `pcbk-public` —
  `com.docker.network.bridge.enable_ip_masquerade: "false"`. Исключение с Д3:
  сеть выхода `pcbk-egress` только для `pcbk-core` (историан и OpenRouter).
- Наружу публикуется только `8443` контейнера `edge`.
- Всё, что видит человек, — по-русски; имена в коде — по-английски,
  комментарии — по-русски и коротко.
- Время на странице — с явным смещением от UTC; пояс — `DISPLAY_TZ`, по
  умолчанию `UTC`.
- Свои вспомогательные скрипты, пробный образ и шаблоны проверки на секреты —
  в рабочем каталоге задания, не в репозитории.

## Review Focus

1. **Страница открыта, а сторож замолчал** (цикл завис, сторож убит, запрос
   повис из-за сети) — открытая страница сама показывает «Сторож не
   отвечает — состояние неизвестно» по часам браузера. Тесты — задача 2,
   `status.test.mjs`: `banner when fetch hangs`, `banner on stale json`,
   `banner on http error`; серверная отрисовка — `test_html_stale_snapshot_shows_banner`.
2. **Служба падает в цикле перезапусков** — красная «падает в цикле», а не
   вечно жёлтая. Тест — задача 1, `test_crash_loop_is_fail`.
3. **Сертификат 8443 истёк или `edge` отдаёт не тот** — люди видят ошибку
   браузера, страница должна сказать это раньше. Тесты — задача 1,
   `test_tls_verdict_*`; задача 3, `test_watchdog_checks_edge_tls`.
4. **Прокси сокета сторожа упал или завис** — сам прокси красный, всё, что
   видно через него, «неизвестно», такт не длиннее срока устаревания.
   Тесты — задача 2, `test_run_checks_docker_down_marks_containers_unknown`,
   `test_tick_bounded_when_proxy_hangs`.
5. **Контейнер не может стартовать или приостановлен** — сбой, а не «спит» и
   не «работает». Тесты — задача 1, `test_failed_start_is_fail`,
   `test_paused_is_fail`.

---

## Карта файлов

```
compose.yaml                          службы и сети стенда Д1
compose.test.yaml                     локальные тесты: порт 18443, тестовые пути и пороги
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
watchdog/pcbk_watchdog/static/status.js   опрос /status.json и полоса молчания
watchdog/pcbk_watchdog/main.py        настройки, цикл проверок и HTTP-сервер
watchdog/tests/helpers.py             общие константы и помощники тестов (импорт явный)
watchdog/tests/                       модульные тесты Python
watchdog/tests/js/status.test.mjs     модульные тесты status.js (node --test)
tests/integration/                    проверки компоновки на локальном Docker
docs/checks/D1.md                     журнал живых проверок
docs/checks/D1/*.png                  снимки страницы состояния
```

**Сети** (одно место правды — `compose.yaml`; рабочие места добавит Д2 в
`${STU_NET}.N.0/28`, по умолчанию `172.31.N.0/28`, N = 1…10):

| Сеть | Подсеть | Вид | Кто в ней |
|---|---|---|---|
| `pcbk-public` | `172.31.250.16/28` | обычная, без маскарада | только `edge` — ради публикации 8443 |
| `pcbk-front` | `172.31.250.32/28` | внутренняя, изолированный шлюз | `edge`, `watchdog` (с Д3 — `pcbk-core`) |
| `pcbk-ro` | `172.31.250.48/28` | внутренняя, изолированный шлюз | `watchdog`, `sp-ro` |
| `pcbk-ctl` | `172.31.250.64/28` | внутренняя, изолированный шлюз | `sp-ctl` (с Д5 — `pcbk-core`) |
| `pcbk-probe` (только задача 0) | `172.31.250.0/28` | внутренняя, изолированный шлюз | проба OpenCode и `curl` |

Имена контейнеров фиксированы: `pcbk-edge`, `pcbk-watchdog`, `pcbk-sp-ro`,
`pcbk-sp-ctl`; рабочие места — `pcbk-student-01…10` (Д2); серверный слой —
`pcbk-core` (Д3). Образ сторожа — `pcbk-reserve/watchdog:d1`.

---

### Task 0: Утренняя проба на сервере

Первым делом и параллельно задачам 1–2: снимает главный риск дня. Команды —
по SSH; шаги с `sudo` выполняет владелец или исполнитель с его явного
согласия в этот день. Итоги — в `docs/checks/D1.md` вердиктами с пометкой
[П]; адреса, имена хостов, учётные записи и секреты в журнал не пишутся —
в командах они живут в переменных оболочки (`HOST_LAN`, `BDRV_HOST`).

**Files:**
- Create: `docs/checks/D1.md`

- [ ] **Step 1: Предпроверки (без `sudo`, только чтение)**

Run: `cat /proc/sys/kernel/yama/ptrace_scope; grep -cw avx2 /proc/cpuinfo; docker info --format '{{.CgroupVersion}} {{.CgroupDriver}} {{.ServerVersion}} {{.Driver}} {{json .DriverStatus}} {{.SecurityOptions}}'; systemctl show -p ExecReload -p ActiveEnterTimestamp docker; stat -c '%y' /etc/docker/daemon.json; docker compose version; ss -ltn 'sport = :8443'; docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | grep -c '172\.31\.'; BDRV_HOST=$(grep '^BDRV_HOST=' /opt/dify/scripts/.bdrv.env | cut -d= -f2); ip route get "$BDRV_HOST" | grep -o ' via [^ ]*' | wc -l; grep -E '^NGINX_SSL_CERT(_KEY)?_FILENAME=' /opt/dify/docker/.env; free -m; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d1-before.txt`
Expected: `ptrace_scope` ≤ 2; `avx2` > 0 — иначе пробный образ и образ Д2
на сборке `-baseline`; `2 systemd 29.7.2`, хранилище образов записано
(классическое или containerd — от этого зависит сверка в задаче 5), в
`SecurityOptions` нет `userns`; у `ExecReload` есть `kill -s HUP`;
`daemon.json` не менялся после `ActiveEnterTimestamp` (иначе reload применит
и чужие отложенные правки — стоп, вопрос владельцу); 8443 свободен; сетей
Docker в `172.31.0.0/16` — 0; маршрут к историану идёт через шлюз (1).
В журнал — только вердикты.

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
содержит `runsc`; у каждого контейнера Dify время работы продолжает
`~/pcbk-d1-before.txt`.
Откат: убрать контейнеры с `runsc`; вернуть или удалить `daemon.json`;
`sudo systemctl reload docker`; `sudo rm -rf /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin`.

- [ ] **Step 3: Проба OpenCode под `runsc` в изолированной сети**

Локально, в рабочем каталоге задания (не в репозитории): пробный образ
`pcbk-probe/opencode:1.18.33` — `ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02`
+ `opencode-linux-x64.tar.gz` v1.18.33 (glibc, та же сборка, что в Д2; без AVX2 —
`-baseline`) + ripgrep 15.1.0, суммы из фактов §6. Его и
`curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6`
(тег `8.16.0` после `pull` по дайджесту) — `docker save … | gzip | ssh … 'gunzip | docker load'`.
На сервере: сеть `pcbk-probe` (`--internal`,
`-o com.docker.network.bridge.gateway_mode_ipv4=isolated`,
`--subnet 172.31.250.0/28`); контейнер `pcbk-probe-oc` — `--runtime=runsc`
`--read-only --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001`
`--tmpfs /tmp:exec,mode=1777`, `HOME`/`XDG_*` на `/tmp`,
`OPENCODE_DISABLE_MODELS_FETCH=1`, `OPENCODE_SERVER_PASSWORD=probe`,
`--ip 172.31.250.3`, `opencode serve --hostname 0.0.0.0 --port 4096`.
Проверка портов — одноразовым `curl` под `--runtime=runsc` в `pcbk-probe` с
`--ip 172.31.250.4`: `curl -sv --connect-timeout 3 -m 4 telnet://<адрес>:<порт> 2>&1 | grep -cE 'Established connection|Connected to'`
(0 — закрыто, 1 — открыто). Набор:
- положительный контроль: `172.31.250.3:4096` → **1** (иначе проверка слепа, результат не засчитывается);
- мост своей сети: `172.31.250.1` на 22, 80, 443, 3389 → 0;
- `$HOST_LAN` (из `hostname -I`) на 22, 443; `$BDRV_HOST:1433`; `1.1.1.1:443` → 0;
- отрицательный контроль: временная сеть `pcbk-probe-ctl` `--internal` **без**
  isolated, `172.31.250.240/28`: адрес её моста на 22 → **1** (проверка видит
  путь, который закрывает isolated); сеть удалить. Если здесь 0 — вход с
  мостов на сервере режет ufw; записать это: тогда путь к хосту закрыт
  дважды, а изоляцию подтверждает отсутствие адреса на мосту;
Плюс: `ip -4 addr show dev br-<id сети pcbk-probe>` — пусто;
`GET /global/health` с `opencode:probe` из `curl` → `200` (повторы до 30 с);
`docker exec pcbk-probe-oc cat /proc/version` — не ядро 5.15 хоста; через 60 с
простоя — `docker stats --no-stream pcbk-probe-oc` и `pids.current` cgroup
контейнера (для `mem_limit` и `pids_limit` Д2).
В конце — `docker rm -f pcbk-probe-oc`, `docker network rm pcbk-probe`.

- [ ] **Step 4: Убийство по памяти под `runsc` (сведения для Д2 и Д11)**

`docker run --name pcbk-oomt --runtime=runsc --network none -m 64m --entrypoint sh pcbk-probe/opencode:1.18.33 -c 'tail /dev/zero'; docker inspect -f '{{.State.OOMKilled}} {{.State.ExitCode}}' pcbk-oomt; docker rm pcbk-oomt`
Expected: записать как есть (`true 137` или иное).

- [ ] **Step 5: Commit** (после проверки на секреты)

```bash
git add docs/checks/D1.md
git commit -m "Д1: утренняя проба — gVisor через reload, OpenCode под runsc в изолированной сети"
```

---

### Task 1: Проверки и журнал сторожа

**Files:**
- Create: `watchdog/pyproject.toml` (`[tool.pytest.ini_options] pythonpath = ["."]`),
  `watchdog/pcbk_watchdog/__init__.py`, `watchdog/pcbk_watchdog/checks.py`,
  `watchdog/pcbk_watchdog/journal.py`, `watchdog/pcbk_watchdog/docker_api.py`,
  `watchdog/tests/helpers.py`, `watchdog/tests/conftest.py`
- Test: `watchdog/tests/test_checks.py`, `watchdog/tests/test_journal.py`,
  `watchdog/tests/test_docker_api.py`

**Interfaces:**
- Produces:
  - `State = Literal["ok", "warn", "fail", "absent", "unknown"]`
  - `@dataclass(frozen=True) class Check: component: str; title: str; state: State; detail: str`
  - `check_memory(meminfo: str, warn_mib: int = 2048, fail_mib: int = 1024) -> Check` — компонент `memory`, по полю `MemAvailable`; `ok` — «свободно N МиБ», `warn`/`fail` — постоянный текст «свободно меньше <порог> МиБ» (иначе журнал пишет событие каждый такт)
  - `check_container(component: str, title: str, inspect: dict | None, *, sleeping_ok: bool, now: datetime) -> Check`
  - `check_http(component: str, title: str, url: str, timeout: float = 3.0) -> Check`
  - `tls_verdict(not_after: datetime, now: datetime, warn_days: int = 14) -> tuple[State, str]` — `ok` «сертификат до ДД.ММ.ГГГГ (N дн.)», `warn` при N < 14, `fail` «сертификат истёк ДД.ММ.ГГГГ»
  - `check_tls(component: str, title: str, host: str, port: int, cafile: str, now: datetime, timeout: float = 3.0) -> Check` —
    рукопожатие с `CERT_REQUIRED`, доверие — только `cafile` (тот же
    `cert.pem`, что у `edge`), `VERIFY_X509_PARTIAL_CHAIN`, без проверки
    имени; удалось → `tls_verdict` по `notAfter`; ошибка проверки с кодом
    10 → `fail` «сертификат истёк»; код 9 → `fail` «сертификат ещё не
    действителен»; иная ошибка проверки → `fail`
    «отдаёт не тот сертификат»; нет соединения → `fail` «не отвечает»
  - `not_installed(component: str, title: str) -> Check` — `state="absent"`, `detail="ещё не установлен"`
  - `RECENT_RESTART = timedelta(minutes=15)`, `CRASH_LOOP_RESTARTS = 3`
  - `class DockerUnavailable(Exception)`
  - `class DockerReader: __init__(self, base_url: str, timeout: float = 3.0); ping(self) -> None; inspect(self, name: str) -> dict | None` —
    `ping` без ответа `OK` бросает `DockerUnavailable`; `inspect`: 404 → `None`;
    нет связи, таймаут, 403/405, 5xx → `DockerUnavailable`; имя вне
    `pcbk-[a-z0-9-]+` → `ValueError` до запроса
  - `@dataclass(frozen=True) class Event: ts: datetime; component: str; state: State; detail: str`
  - `class Journal: __init__(self, path: str); started(self, now: datetime) -> Event; record(self, checks: list[Check], now: datetime) -> list[Event]; recent(self, limit: int = 50) -> list[Event]`

Правило `check_container` — строки сверху вниз, первая подходящая:

| Состояние Docker | Итог |
|---|---|
| нет контейнера (`None`) | `fail` «нет контейнера» |
| `Paused` | `fail` «приостановлен» |
| `Running` или `Restarting`, `RestartCount` ≥ 3 и `StartedAt` моложе `RECENT_RESTART` | `fail` «падает в цикле (перезапуски подряд)» |
| `Restarting` (Docker ставит и `Running`) | `warn` «перезапускается» |
| `Running`, `RestartCount` > 0 и `StartedAt` моложе `RECENT_RESTART` | `warn` «перезапущен после сбоя в ЧЧ:ММ UTC±ЧЧ:ММ (N с последнего запуска)» — время в поясе `now` |
| `Running` | `ok` «работает» (при `RestartCount` > 0 — «работает, сбоев с последнего запуска: N») |
| остановлен, `OOMKilled` | `fail` «убит по памяти» |
| остановлен, `State.Error` не пуст | `fail` «не запускается: <первые 80 знаков ошибки>» |
| остановлен, `sleeping_ok`, код ∈ {0, 137, 143} | `ok` «спит» |
| остановлен, иначе | `fail` «остановлен, код N» |

`RestartCount` Docker обнуляет при ручном запуске (`daemon/start.go:177` в
moby v29.7.2) и увеличивает только при перезапуске по политике — своя
память сторожу не нужна.

- [ ] **Step 1: Write the failing tests**

```python
# test_checks.py — T0, insp и прочие помощники импортируются из helpers.py
MEMINFO = "MemTotal: 16384000 kB\nMemAvailable: {} kB\n"

def test_memory_ok_warn_fail():
    assert check_memory(MEMINFO.format(11_700 * 1024)).state == "ok"
    assert check_memory(MEMINFO.format(1_500 * 1024)).state == "warn"
    assert check_memory(MEMINFO.format(900 * 1024)).state == "fail"
    assert "11700 МиБ" in check_memory(MEMINFO.format(11_700 * 1024)).detail

def test_memory_without_field_is_unknown():
    assert check_memory("MemTotal: 1 kB\n").state == "unknown"

def insp(running=False, oom=False, code=0, restarts=0, restarting=False, paused=False,
         error="", started=T0 - timedelta(hours=1)):
    return {"State": {"Running": running, "Paused": paused, "OOMKilled": oom, "ExitCode": code,
                      "Restarting": restarting, "Error": error,
                      "StartedAt": started.isoformat()}, "RestartCount": restarts}

def stu(i):
    return check_container("student-01", "Рабочее место 01", i, sleeping_ok=True, now=T0)

def test_student_states():
    assert (stu(insp(running=True)).state, stu(insp(running=True)).detail) == ("ok", "работает")
    for code in (0, 137, 143):
        assert (stu(insp(code=code)).state, stu(insp(code=code)).detail) == ("ok", "спит")
    assert (stu(insp(oom=True, code=137)).state, stu(insp(oom=True, code=137)).detail) == ("fail", "убит по памяти")
    assert stu(insp(running=True, restarting=True, restarts=1)).state == "warn"   # так ставит Docker

def test_recent_policy_restart_is_warn_then_ok():
    fresh = stu(insp(running=True, restarts=2, started=T0 - timedelta(minutes=3)))
    assert fresh.state == "warn" and "перезапущен после сбоя" in fresh.detail
    old = stu(insp(running=True, restarts=2, started=T0 - timedelta(minutes=30)))
    assert old.state == "ok" and "сбоев с последнего запуска: 2" in old.detail

def test_crash_loop_is_fail():
    for i in (insp(running=True, restarts=3, started=T0 - timedelta(seconds=20)),
              insp(running=True, restarting=True, restarts=5, started=T0 - timedelta(minutes=2))):
        assert stu(i).state == "fail" and "падает в цикле" in stu(i).detail

def test_paused_is_fail():
    r = check_container("sp-ctl", "Прокси сокета серверного слоя", insp(running=True, paused=True),
                        sleeping_ok=False, now=T0)
    assert (r.state, r.detail) == ("fail", "приостановлен")

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

def test_tls_verdict_ok_warn_fail():
    assert tls_verdict(T0 + timedelta(days=90), T0)[0] == "ok"
    state, detail = tls_verdict(T0 + timedelta(days=10), T0)
    assert state == "warn" and "10 дн." in detail
    assert tls_verdict(T0 - timedelta(days=1), T0)[0] == "fail"

def test_tls_wrong_certificate_is_fail(tls_server, other_cert):   # сервер отдаёт один, доверяем другому
    r = check_tls("edge", "Входной прокси", "127.0.0.1", tls_server.port, other_cert, T0)
    assert (r.state, r.detail) == ("fail", "отдаёт не тот сертификат")

def test_tls_matching_certificate_reports_days(tls_server):
    r = check_tls("edge", "Входной прокси", "127.0.0.1", tls_server.port, tls_server.cafile, now_utc())
    assert r.state == "ok" and "дн." in r.detail

# test_docker_api.py — fake_proxy (фикстура на функцию): http.server-двойник sp-ro
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

`helpers.py`: `T0` (фиксированное время UTC), `now_utc()`, `insp` можно
держать здесь же. `conftest.py` — фикстуры на функцию: `fake_proxy`
(`http.server` на свободном порту: `/v1.44/containers/pcbk-sp-ctl/json` →
inspect работающего контейнера, `pcbk-student-09` → 404, `pcbk-forbidden` →
403, `/v1.44/_ping` → `OK`; `url`, `paths`), `tls_server` (локальный TLS на
свободном порту с самоподписанным сертификатом из `openssl req -x509`,
`port`, `cafile`), `other_cert` (второй самоподписанный сертификат).

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
git commit -m "Сторож: проверки памяти, контейнеров, HTTP и сертификата, журнал переходов"
```

---

### Task 2: Страница, `status.js` и цикл сторожа

**Files:**
- Create: `watchdog/pcbk_watchdog/page.py`, `watchdog/pcbk_watchdog/static/status.js`,
  `watchdog/pcbk_watchdog/main.py`, `watchdog/components.json`, `watchdog/Dockerfile`
- Test: `watchdog/tests/test_page.py`, `watchdog/tests/test_main.py`,
  `watchdog/tests/js/status.test.mjs`

**Interfaces:**
- Consumes: всё из задачи 1.
- Produces:
  - `@dataclass(frozen=True) class Snapshot: checked_at: datetime; checks: tuple[Check, ...]`
  - `overall(snapshot: Snapshot) -> State` — худшее из не-`absent`: `fail` > `unknown` > `warn` > `ok`
  - `status_json(snapshot: Snapshot | None, now: datetime, stale_after_s: int) -> dict` — ключи `checked_at` (ISO с поясом или `null`), `stale` (bool; `true` при `None`), `stale_after_s`, `overall` (`unknown` при `None`), `checks` (словари `component/title/state/detail`)
  - `render_html(snapshot: Snapshot | None, events: list[Event], now: datetime, stale_after_s: int, tz: ZoneInfo) -> str` —
    полоса «Сторож не отвечает — состояние неизвестно» с `id="silence"`
    выводится **видимой**, если снимка нет или он устарел, и с атрибутом
    `hidden` — если свеж; строка «обновлено N с назад» с `id="age"`;
    подключает `<script type="module" src="/status.js">`; внешних ресурсов нет
  - `status.js` — ES-модуль: `export function startPolling({ fetchImpl, doc, nowMs, setIntervalImpl, staleAfterS, pollMs = 5000, timeoutMs = 4000 })`;
    каждые `pollMs` запрашивает `/status.json` с `AbortController` на
    `timeoutMs`; удачный ответ без `stale` запоминает время по `nowMs()` и
    перерисовывает список; полоса видима, если ответ `stale: true`, ошибка
    или HTTP ≠ 200 **или с последнего удачного ответа по часам браузера
    прошло больше `staleAfterS`** (запрос повис); в браузере модуль сам
    вызывает `startPolling` со значениями страницы, в Node — нет
  - в `main.py`: `@dataclass(frozen=True) class Settings` — все переменные ниже, `Settings.from_env() -> Settings`;
    `run_checks`, `tick`, `WatchState`, `make_server` — ниже; проверки
    импортируются по имени (`from .checks import check_memory, …`)
  - `tick(state: WatchState, journal: Journal, components: list[dict], docker: DockerReader, settings: Settings, now: datetime) -> None` — одна итерация цикла
  - `run_checks(components: list[dict], docker: DockerReader, settings: Settings, now: datetime) -> list[Check]` —
    `docker_ping` без ответа → `fail` «не отвечает»; после первого
    `DockerUnavailable` за такт все проверки вида `container` получают
    `unknown` «нет связи с прокси сокета» без запросов к Docker; исключение в
    одной проверке → её `unknown` «ошибка проверки: <тип>», такт продолжается
  - `class WatchState: snapshot: Snapshot | None` (под замком) и
    `make_server(state: WatchState, journal: Journal, settings: Settings) -> ThreadingHTTPServer` —
    `GET /status` (HTML), `GET /status.json` (`503` и `stale: true` до первого
    такта), `GET /status.js`, `GET /healthz` — `200` при свежем снимке,
    `503` при устаревшем или до первого такта
  - Переменные (`Settings`): `LISTEN_PORT=8090`, `TICK_S=10`,
    `STALE_AFTER_S=30`, `DISPLAY_TZ=UTC`, `DOCKER_URL=http://pcbk-sp-ro:2375`,
    `DOCKER_TIMEOUT_S=3`, `MEM_WARN_MIB=2048`, `MEM_FAIL_MIB=1024`,
    `TLS_CAFILE=/app/tls/cert.pem`, `JOURNAL_PATH=/var/lib/pcbk-watchdog/journal.db`,
    `MEMINFO_PATH=/proc/meminfo`, `COMPONENTS_PATH=/app/components.json`
  - Такт: снимок обновляется **до** записи в журнал; ошибка журнала не
    останавливает цикл и сама становится строкой «Журнал сторожа — fail:
    <тип>» в снимке
  - образ `pcbk-reserve/watchdog:d1`: `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`,
    uid 10002, `/var/lib/pcbk-watchdog` с владельцем 10002, `SIGTERM` —
    быстрый выход, `HEALTHCHECK` на `/healthz` раз в 30 с,
    `CMD ["python", "-m", "pcbk_watchdog.main"]`

`components.json` на Д1, по порядку: `edge` (`tls`, `pcbk-edge:8443`,
«Входной прокси»); `sp-ro` (`docker_ping`, «Прокси сокета сторожа»);
`sp-ctl` (`container`, `pcbk-sp-ctl`, `sleeping_ok: false`, «Прокси сокета
серверного слоя»); `memory` («Память сервера»); `student-01` … `student-10`
(«Рабочее место NN»), `core` («Серверный слой»), `historian` («Историан
БДРВ»), `llm` («OpenRouter и бюджет») — `absent`. Виды: `memory`, `http`,
`tls`, `docker_ping`, `container`, `absent`. Д2 переводит рабочие места в
`container`.

- [ ] **Step 1: Write the failing tests**

```python
def test_overall_ignores_absent_and_takes_worst():
    s = Snapshot(T0, (Check("a", "A", "ok", ""), Check("b", "B", "absent", "ещё не установлен"),
                      Check("c", "C", "warn", "")))
    assert overall(s) == "warn"

def test_status_json_marks_stale_and_missing_snapshot():
    s = Snapshot(T0, (Check("a", "A", "ok", ""),))
    assert status_json(s, T0 + timedelta(seconds=29), 30)["stale"] is False
    assert status_json(s, T0 + timedelta(seconds=31), 30)["stale"] is True
    assert status_json(None, T0, 30)["stale"] is True and status_json(None, T0, 30)["overall"] == "unknown"

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

def test_html_loads_local_module_only():
    html = render_html(Snapshot(T0, ()), [], T0, 30, ZoneInfo("UTC"))
    assert 'src="/status.js"' in html and "http://" not in html and "https://" not in html

def test_run_checks_docker_down_marks_containers_unknown():
    comps = [{"id": "sp-ro", "title": "Прокси сокета сторожа", "kind": "docker_ping"},
             {"id": "sp-ctl", "title": "Прокси сокета серверного слоя", "kind": "container",
              "container": "pcbk-sp-ctl", "sleeping_ok": False}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.state) for c in checks] == [("sp-ro", "fail"), ("sp-ctl", "unknown")]
    assert "прокси сокета" in checks[1].detail

def test_tick_bounded_when_proxy_hangs(silent_proxy_url):   # принимает соединение и молчит
    comps = [{"id": f"s{n}", "title": "x", "kind": "container", "container": f"pcbk-student-{n:02d}",
              "sleeping_ok": True} for n in range(1, 11)]
    started = time.monotonic()
    run_checks(comps, DockerReader(silent_proxy_url, timeout=0.5), SETTINGS, T0)
    assert time.monotonic() - started < 1.5

def test_run_checks_survives_check_exception(monkeypatch):
    monkeypatch.setattr(pcbk_watchdog.main, "check_memory", lambda *a, **k: 1 / 0)
    out = run_checks([{"id": "memory", "title": "Память сервера", "kind": "memory"}],
                     DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert out[0].state == "unknown" and "ZeroDivisionError" in out[0].detail

def test_journal_failure_shows_on_page(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); journal = Journal(str(ro / "j.db"))   # таблица создана
    (ro / "j.db").chmod(0o400); ro.chmod(0o500)                                # дальше запись невозможна
    state = WatchState()
    tick(state, journal, [], DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert any(c.component == "journal" and c.state == "fail" for c in state.snapshot.checks)

def test_components_file_d1():
    comps = load_components("components.json")
    assert [c["id"] for c in comps][:4] == ["edge", "sp-ro", "sp-ctl", "memory"]
    assert comps[0]["kind"] == "tls"
    assert {c["id"] for c in comps if c["kind"] == "absent"} == \
           {f"student-{n:02d}" for n in range(1, 11)} | {"core", "historian", "llm"}

def test_healthz_follows_freshness():
    state = WatchState(); srv = start_test_server(state)
    assert http_status(srv, "/healthz") == 503 and http_status(srv, "/status.json") == 503
    state.snapshot = Snapshot(now_utc(), ())
    assert http_status(srv, "/healthz") == 200
    state.snapshot = Snapshot(now_utc() - timedelta(seconds=60), ())
    assert http_status(srv, "/healthz") == 503

def test_browser_runs_status_js_on_stale_json(fake_page_server):
    # свежий HTML (полоса hidden), а /status.json отвечает stale: true
    dom = chrome_dom(fake_page_server.url + "/status", virtual_time_ms=8000)
    assert re.search(r'id="silence"(?![^>]*hidden)', dom)
```

```js
// status.test.mjs — node --test; fetch, часы и таймеры поддельные (mock.timers)
test('no banner while fresh', ...)        // ответы {stale:false} каждые 5 с → silence.hidden === true
test('banner on stale json', ...)         // первый ответ {stale:true} → silence.hidden === false
test('banner on http error', ...)         // ответ 502 → silence.hidden === false
test('banner when fetch hangs', ...)      // fetch не завершается; через staleAfterS+1 с по часам → hidden === false
test('banner hides after recovery', ...)  // после сбоя удачный свежий ответ → hidden === true
```

`helpers.py` дополняется: `SETTINGS` (`Settings` с `MEMINFO_PATH` на
временный файл), `start_test_server`, `http_status`, `tick` — одна итерация
цикла из `main.py`, `chrome_dom(url, virtual_time_ms) -> str`
(`google-chrome --headless=new --dump-dom --virtual-time-budget=<мс>`).
`conftest.py`: `silent_proxy_url` (URL сокета, который принимает соединения
и не отвечает), `fake_page_server` (отдаёт `render_html` свежего снимка,
`status.js` и `/status.json` = `{"stale": true, …}`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q tests/test_page.py tests/test_main.py; node --test tests/js/*.test.mjs`
Expected: FAIL — `ImportError` и нет `status.js`

- [ ] **Step 3: Implement `page.py`, `status.js`, `main.py`, `components.json`, `Dockerfile` по интерфейсам выше**

`absent` — серым, `unknown` — жёлтым с пометкой «неизвестно», `fail` —
красным. Под списком — последние 20 событий журнала. Цикл — отдельный поток,
`try/except` на весь такт; `stale` считается в момент запроса. При старте —
`journal.started(now)`.

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: страница с полосой молчания по часам браузера, ограниченный такт, образ"
```

---

### Task 3: Сторож и прокси входа в компоновке

**Files:**
- Create: `compose.yaml` (службы `edge`, `watchdog`, сети `pcbk-public`,
  `pcbk-front`, том `pcbk-watchdog-journal`), `compose.test.yaml`,
  `deploy/env.example`, `deploy/images.lock`, `edge/pcbk.conf.template`,
  `edge/static/index.html`, `edge/static/watchdog-down.html`,
  `tests/integration/conftest.py`
- Test: `tests/integration/test_edge.py`

**Interfaces:**
- Consumes: образ сторожа — задача 2.
- Produces:
  - `deploy/images.lock`: `nginx:1.30.5-alpine sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94`,
    `curlimages/curl:8.16.0 sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6 # test`
  - `https://<хост>:8443/` — заглушка «Резервный контур в постройке» с
    постоянным предупреждением о статусе стенда и ссылкой на `/status`
  - `https://<хост>:8443/status`, `/status.json`, `/status.js` — от сторожа;
    `502/503/504` на `/status` → `watchdog-down.html` («Сторож не отвечает —
    состояние неизвестно»); `Cache-Control: no-store`
  - `http://pcbk-edge:8080/healthz` → `200 ok`, не публикуется
  - `edge/pcbk.conf.template` — только server-блоки (образ кладёт шаблон в
    `conf.d` внутри `http{}`); сторож разрешается в момент запроса
    (`resolver 127.0.0.11 valid=10s`, `proxy_pass` через переменную) — nginx
    стартует без сторожа; `proxy_connect_timeout 3s`, `proxy_read_timeout 5s`
  - контейнер `edge`: `read_only: true`, tmpfs на `/var/cache/nginx`,
    `/var/run`, `/etc/nginx/conf.d`; `cap_drop: [ALL]`,
    `cap_add: [CHOWN, SETUID, SETGID]`, `no-new-privileges`; `${TLS_DIR}`
    (`cert.pem`, `key.pem`) — `:ro`. Мастер nginx от root без `DAC_*` читает
    ключ, только если каталог проходим (`0755`), а ключ принадлежит root или
    открыт на чтение (`0644` — только у тестового ключа)
  - сторож: сеть `pcbk-front`, `${TLS_DIR}/cert.pem` → `:ro` в `/app/tls/cert.pem`,
    том журнала, `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`,
    `mem_limit: 128m`, `restart: unless-stopped`, переменные задачи 2 через
    `${VAR:-умолчание}` (учения меняют `TICK_S` и `MEM_WARN_MIB`)
  - `.env` выкладки: `DOCKER_GID`, `STU_NET`, `TLS_DIR`, `DISPLAY_TZ`,
    `SECRETS_DIR`, `AGENTS_DIR`, `MEM_WARN_MIB`, `MEM_FAIL_MIB`
  - `compose.test.yaml`: `edge` — `ports: !override ["127.0.0.1:18443:8443"]`
  - фикстура `stack` (на сессию): готовит образы из `deploy/images.lock`
    (`pull` по дайджесту, `tag`); во временном каталоге пишет `test.env`
    (`DOCKER_GID` из `getent group docker | cut -d: -f3`, `MEM_WARN_MIB=64`,
    `MEM_FAIL_MIB=32`, тестовые пути) и тестовый TLS (каталог `0755`,
    `cert.pem` и `key.pem` — `0644`, `openssl req -x509`); во всех вызовах —
    `docker compose -p pcbk-test --env-file <test.env> -f compose.yaml -f compose.test.yaml`;
    `up -d --build`, ждёт `/healthz` сторожа и `/status` через `edge`; в конце
    `down -v`. Методы: `https(path) -> tuple[int, str]` (к
    `https://127.0.0.1:18443` без проверки сертификата), `wait_status(pred, timeout) -> dict`,
    `inspect(name) -> dict`, `network(name) -> dict`,
    `host_bridge_addresses(*names) -> list[str]` (адреса IPv4 на мостах этих
    сетей на хосте), `containers() -> list[str]` (`docker compose … ps -a --format '{{.Name}}'`),
    `env_names(name) -> set[str]` и `image_env_names(image) -> set[str]`;
    `start`, `stop`, `restart`, `pause`, `unpause` возвращаются только после
    готовности или явного отказа (сторож — `/healthz` 200 изнутри; `edge` —
    ответ на `:8080/healthz`)

- [ ] **Step 1: Write the failing tests**

```python
DECLARED_ENV = {                   # наши переменные сверх переменных базового образа
    "pcbk-watchdog": {"LISTEN_PORT", "TICK_S", "STALE_AFTER_S", "DISPLAY_TZ", "DOCKER_URL",
                      "DOCKER_TIMEOUT_S", "MEM_WARN_MIB", "MEM_FAIL_MIB", "TLS_CAFILE",
                      "JOURNAL_PATH", "MEMINFO_PATH", "COMPONENTS_PATH"},
    "pcbk-edge": set(),
}

def test_edge_serves_status_from_watchdog(stack):
    code, body = stack.https("/status")
    assert code == 200 and "Входной прокси" in body and "ещё не установлен" in body
    assert stack.https("/status.js")[0] == 200

def test_root_shows_stand_warning(stack):
    assert "учебный стенд" in stack.https("/")[1].lower()

def test_watchdog_checks_edge_tls(stack):
    data = stack.wait_status(lambda d: any(c["component"] == "edge" and c["state"] != "unknown"
                                           for c in d["checks"]), timeout=30)
    edge = next(c for c in data["checks"] if c["component"] == "edge")
    assert edge["state"] == "ok" and "дн." in edge["detail"]

def test_edge_shows_watchdog_down_page(stack):
    stack.stop("pcbk-watchdog")
    try:
        code, body = stack.https("/status")
        assert code in (502, 504) and "Сторож не отвечает" in body and "nginx" not in body.lower()
    finally:
        stack.start("pcbk-watchdog")

def test_edge_starts_without_watchdog(stack):
    stack.stop("pcbk-watchdog")
    try:
        stack.restart("pcbk-edge")
        assert stack.https("/status")[0] in (502, 504)
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

def test_only_edge_publishes_one_port(stack):
    published = {n: [p for p in (stack.inspect(n)["NetworkSettings"]["Ports"] or {}).values() if p]
                 for n in stack.containers()}
    assert {n: len(p) for n, p in published.items() if p} == {"pcbk-edge": 1}

def test_networks_d1_front(stack):
    assert set(stack.inspect("pcbk-edge")["NetworkSettings"]["Networks"]) == {"pcbk-public", "pcbk-front"}
    front = stack.network("pcbk-front")
    assert front["Internal"] is True
    assert front["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert stack.network("pcbk-public")["Options"]["com.docker.network.bridge.enable_ip_masquerade"] == "false"
    assert stack.host_bridge_addresses("pcbk-front") == []

def test_env_holds_only_known_names(stack):
    for name, image in (("pcbk-watchdog", "pcbk-reserve/watchdog:d1"), ("pcbk-edge", "nginx:1.30.5-alpine")):
        extra = stack.env_names(name) - stack.image_env_names(image) - DECLARED_ENV[name]
        assert extra == set(), f"{name}: лишние переменные {extra}"
        assert not {n for n in stack.env_names(name) if re.search(r"PASSWORD|SECRET|TOKEN|APIKEY", n)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_edge.py`
Expected: FAIL — нет `compose.yaml`

- [ ] **Step 3: Implement `edge`, сторожа в `compose.yaml`, `compose.test.yaml`, шаблон nginx, две страницы, фикстуру `stack`, `deploy/env.example`, `deploy/images.lock`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_edge.py`
Expected: PASS; после прогона `docker compose -p pcbk-test ps -a` пуст, сетей
`pcbk-` нет

- [ ] **Step 5: Commit**

```bash
git add compose.yaml compose.test.yaml deploy/ edge/ tests/integration/
git commit -m "Прокси входа 8443 и сторож в компоновке: своя страница при смерти и зависании сторожа"
```

---

### Task 4: Две копии узкого прокси сокета

**Files:**
- Modify: `compose.yaml` (службы `sp-ro`, `sp-ctl`, сети `pcbk-ro`,
  `pcbk-ctl`; сторож — ещё и в `pcbk-ro`), `compose.test.yaml` (мишень
  `pcbk-test-foreign`), `deploy/images.lock` (+ `wollomatic/socket-proxy:1.13.1
  sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002`,
  `busybox:<версия>` по дайджесту на день выполнения `# test`),
  `tests/integration/conftest.py` (`http_from_watchdog`, `http_as`),
  `tests/integration/test_edge.py` (`DECLARED_ENV` и список образов — + `sp-ro`, `sp-ctl`)
- Test: `tests/integration/test_socket_proxy.py`

**Interfaces:**
- Consumes: фикстура `stack` и сторож — задача 3.
- Produces:
  - `pcbk-sp-ro:2375` — клиент только `pcbk-watchdog`, только GET:
    `(/v1\.[0-9]+)?/(_ping|containers/pcbk-(student-(0[1-9]|10)|sp-ctl)/json)`
  - `pcbk-sp-ctl:2375` — клиент только `pcbk-core`:
    GET `(/v1\.[0-9]+)?/(_ping|containers/pcbk-student-(0[1-9]|10)/json)`,
    POST `(/v1\.[0-9]+)?/containers/pcbk-student-(0[1-9]|10)/(start|stop)`;
    **списка контейнеров нет**: `GET /containers/json` отдаёт все контейнеры
    сервера вместе с `Command` (у Redis в Dify там пароль), а строку запроса
    прокси не фильтрует
  - контейнеры прокси: образ `wollomatic/socket-proxy:1.13.1`, флаги
    `-listenip=0.0.0.0`, `-allowfrom=<имя клиента>`, `-allowGET=…`, у `sp-ctl`
    ещё `-allowPOST=…` (прокси сам дописывает `^…$` — альтернативы в скобках);
    `user: "65534:${DOCKER_GID}"`, `read_only: true`, `cap_drop: [ALL]`,
    `no-new-privileges`, сокет `:ro`, `restart: unless-stopped`, `mem_limit: 32m`
  - мишень `pcbk-test-foreign` — busybox `sleep`, `network_mode: none`
  - методы `stack`: `http_from_watchdog(method, url) -> int` (запрос
    `http.client` изнутри `pcbk-watchdog`), `http_as(name, network, method, url) -> int`
    (одноразовый `curlimages/curl` с этим именем в этой сети, `--path-as-is`)

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

def test_container_networks_exact(stack):
    expected = {"pcbk-edge": {"pcbk-public", "pcbk-front"}, "pcbk-watchdog": {"pcbk-front", "pcbk-ro"},
                "pcbk-sp-ro": {"pcbk-ro"}, "pcbk-sp-ctl": {"pcbk-ctl"}}
    for name, nets in expected.items():
        assert set(stack.inspect(name)["NetworkSettings"]["Networks"]) == nets
    for net in ("pcbk-front", "pcbk-ro", "pcbk-ctl"):
        n = stack.network(net)
        assert n["Internal"] is True
        assert n["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert stack.host_bridge_addresses("pcbk-front", "pcbk-ro", "pcbk-ctl") == []

def test_status_json_overall_ok(stack):
    data = stack.wait_status(lambda d: d["overall"] == "ok", timeout=30)
    assert {c["component"] for c in data["checks"] if c["state"] == "absent"} >= {"core", "student-01"}

def test_watchdog_sees_socket_proxy_loss(stack):
    stack.stop("pcbk-sp-ro")
    try:
        data = stack.wait_status(lambda d: d["overall"] == "fail", timeout=30)
        states = {c["component"]: c["state"] for c in data["checks"]}
        assert states["sp-ro"] == "fail" and states["sp-ctl"] == "unknown"
    finally:
        stack.start("pcbk-sp-ro")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_socket_proxy.py`
Expected: FAIL — нет служб `sp-ro`, `sp-ctl`

- [ ] **Step 3: Implement `sp-ro`, `sp-ctl`, сети, мишень и методы фикстуры по интерфейсам выше**

- [ ] **Step 4: Run the whole local suite**

Run: `(cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs) && uv run --python 3.12 --with pytest pytest -q tests/integration`
Expected: PASS; после прогона нет контейнеров, сетей и томов `pcbk-test`

- [ ] **Step 5: Commit**

```bash
git add compose.yaml compose.test.yaml deploy/images.lock tests/integration/
git commit -m "Две копии узкого прокси сокета в изолированных сетях"
```

---

### Task 5: Выкладка на сервер

Шаги — команды и вывод, который значит «прошло». Итоги — в `docs/checks/D1.md`.

**Files:**
- Create: `deploy/README.md` — эти шаги для администратора: выкладка, откат,
  что `8443` открывается в обход ufw и закрывается `docker compose stop edge`,
  что `edge` — единственный контейнер с путём к хосту, что при обновлении
  сертификата Dify копию надо повторить (сторож предупредит за 14 дней)
- Modify: `docs/checks/D1.md`

- [ ] **Step 1: Каталоги и сертификат (`sudo` один раз)**

`sudo install -d -o "$(id -un)" -g "$(id -gn)" /opt/pcbk-reserve`;
`sudo install -d -o root -g root -m 0755 /opt/pcbk-reserve/tls`;
сертификат и ключ Dify (имена из задачи 0, шаг 1) —
`sudo install -o root -g root -m 0644 <сертификат> /opt/pcbk-reserve/tls/cert.pem`,
`sudo install -o root -g root -m 0600 <ключ> /opt/pcbk-reserve/tls/key.pem`.
`.env` выкладки — по `deploy/env.example`, `chmod 600`.
Expected: `sudo ls -l /opt/pcbk-reserve/tls` — `root root`, ключ `-rw-------`.
Откат: `docker compose down`; `sudo rm -rf /opt/pcbk-reserve`.

- [ ] **Step 2: Перенести образы стенда**

Локально `docker compose build`; образ сторожа, `nginx:1.30.5-alpine`,
`wollomatic/socket-proxy:1.13.1` (строки `# test` не везём) —
`docker save имя:тег | gzip | ssh … 'gunzip | docker load'`. Сверка — дайджест
конфигурации образа: локально поле `Config` из `manifest.json` архива
`docker save`; на сервере `docker image inspect -f '{{.Id}}'` при классическом
хранилище или `docker image inspect -f '{{json .RootFS}}'` на обеих сторонах
при containerd (хранилище — из задачи 0, шаг 1).
Expected: все совпали.

- [ ] **Step 3: Поднять стенд**

`rsync -a compose.yaml edge …:/opt/pcbk-reserve/` (без `--delete`: `tls/`,
`.env` и будущие `secrets/`, `agents/` не трогаются). На сервере:
`docker compose up -d --no-build edge watchdog sp-ro sp-ctl`.
Expected: четыре контейнера в `Up`; `curl -sk https://127.0.0.1:8443/status.json`
на сервере — `overall: ok`; `ip route get "$BDRV_HOST"` по-прежнему через
шлюз (вердикт); у контейнеров Dify время работы не сбросилось.

- [ ] **Step 4: Commit** (после проверки на секреты)

```bash
git add deploy/README.md docs/checks/D1.md
git commit -m "Выкладка Д1: страница состояния на сервере"
```

---

### Task 6: Живые проверки и учения

Снимки — по SSH-туннелю `ssh -N -L 18443:127.0.0.1:8443 …` и
`google-chrome --headless=new --ignore-certificate-errors --virtual-time-budget=8000 --window-size=1200,1600 --screenshot=docs/checks/D1/<имя>.png https://127.0.0.1:18443/status`.
Итог каждой проверки — вердиктом в `docs/checks/D1.md` с пометкой [П].

- [ ] **Step 1: Прокси сокета на сервере**

Матрица задачи 4: `sp-ctl` — одноразовым клиентом `pcbk-core` в `pcbk-ctl`,
`sp-ro` — изнутри сторожа. Плюс через оба прокси:
`GET /v1.44/containers/docker-api-1/json` → `403`, `GET /v1.44/containers/json` → `403`.
Expected: коды совпадают с локальными; ни одного `2xx` на create, exec, kill,
delete и на контейнеры Dify.

- [ ] **Step 2: Сети стенда не видят сервер**

`docker network inspect pcbk-front pcbk-ro pcbk-ctl` — `Internal: true`,
`isolated`; у их мостов на хосте нет адресов. Из одноразового `curl` в
`pcbk-ctl` способом задачи 0: положительный контроль `pcbk-sp-ctl:2375` → 1;
`$HOST_LAN` на 22, 443 → 0; `1.1.1.1:443` → 0. Путь к хосту через мост
закрывает именно отсутствие адреса на мосту — его и проверяем структурно;
что такая проверка видит путь, показал отрицательный контроль задачи 0.
Expected: как указано; без положительного контроля не засчитывается.

- [ ] **Step 3: Слушатель 3389 (§11 п. 6, только опознание)**

`sudo ss -ltnp 'sport = :3389' | grep -o 'users:(("[^"]*"' ; systemctl is-active xrdp; dpkg -s xrdp 2>/dev/null | grep '^Version'`
Expected: имя процесса и пакет записаны; закрытие — вопрос владельцу и заказчику.

- [ ] **Step 4: 8443 из сети ПЦБК (§11 п. 3)**

Владелец просит человека в сети ПЦБК открыть `https://ai-lab.pcbk.ru:8443/status`.
Записать, как именно: открылась; отказ соединения; ошибка корпоративного
прокси; ошибка сертификата. По памяти проекта домен стоит за корпоративным
прокси ПЦБК, который подставляет свой сертификат, — неудача вероятнее всего
значит, что прокси не пропускает 8443: тогда вопрос владельцу — просить ИТ
ПЦБК пропустить порт или вход Б (наш прокси перед Dify, с коротким
перерывом Dify). Нет человека до конца дня — «не проверено, ждёт
владельца», хвост в Д2.

- [ ] **Step 5: Семь учений — страница показывает сбой**

Сначала — снимок исправного состояния. Затем, со снимком после каждого:
(а) сторож убит: `docker stop pcbk-watchdog` → «Сторож не отвечает»; `start`;
(б) сторож завис: `docker pause pcbk-watchdog` → через ≤ 10 с та же страница; `unpause`;
(в) цикл молчит при живом HTTP: `DRILL_FREEZE_LOOP=1 docker compose up -d --no-build watchdog`
(цикл останавливается после первого такта), через 40 с — красная полоса «состояние
неизвестно»; `docker compose up -d --no-build watchdog`;
(г) `docker stop pcbk-sp-ro` → «Прокси сокета сторожа — не отвечает»,
«Прокси сокета серверного слоя — неизвестно», итог — сбой; `start`;
(д) `docker stop pcbk-sp-ctl` → «Прокси сокета серверного слоя — остановлен»; `start`;
(е) `MEM_WARN_MIB=1000000 docker compose up -d --no-build watchdog` →
«Память сервера — предупреждение»; вернуть;
(ж) `docker stop pcbk-edge` → снимок: браузер не открывает страницу; `start` →
в журнале сторожа «Входной прокси — не отвечает» и восстановление.
В конце — снимок восстановления.
Expected: девять снимков в `docs/checks/D1/`; в журнале сторожа — «сторож
запущен» после (а), (в), (е), сбой и восстановление после (г), (д), (ж).

- [ ] **Step 6: Commit** (после проверки на секреты)

```bash
git add docs/checks/
git commit -m "Д1: живые проверки и семь учений наблюдаемости на сервере"
```

---

### Task 7: Закрытие дня

- [ ] **Step 1:** `docs/DESIGN-platform-2026-09-29.md` §11 — у проверенных
  пунктов пометка [П] и ссылка на `docs/checks/D1.md`; `README.md`, раздел
  «Состояние» — «Д1 готов», адрес страницы состояния, что дальше;
  `docs/plans/DRAFT-D2-workplaces.md` — поправки по итогам пробы (сборка,
  `mem_limit`, `pids_limit`, признак убийства по памяти).
- [ ] **Step 2:** критик (Opus 5.5) по итогу дня. Блокер — любой пункт
  «Блокер дня» дорожной карты. Петля — до нуля блокеров, не больше двух
  раундов; третий — только после разговора с владельцем.
- [ ] **Step 3:** ветку дня — в `main` (fast-forward), тег `platform-d1`;
  проверка на секреты по всей ветке:
  `git log -p main..HEAD -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто;
  `git push origin main platform-d1`; чистый клон: тег, `deploy/images.lock`
  и снимки на месте.
- [ ] **Step 4:** владельцу — «Д1 готов», снимки и только вопросы, которые
  требуют его решения (8443 из сети ПЦБК, если не открылась или не
  проверена; 3389; итог пробы gVisor, если он плохой).
