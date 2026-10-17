# Шлюз (роль `gateway`)

Роль `gateway` (класс `GatewayRole`, пакет `core/pcbk_core/gateway/`) — единственная точка входа студентов и
преподавателя на стенд. Как и роли `data` и `llm`, она не отдельный контейнер, а часть одного процесса `core`
(`FastAPI`-приложение, `core/pcbk_core/app.py:create_app`): один `uvicorn` на порту `CORE_PORT`, одна база SQLite
`core.db`. Роль отвечает за:

- учётные записи студентов и преподавателя, вход, сессии, замок входа (`accounts.py`, `api.py`);
- рабочие места OpenCode: пробуждение через `sp-ctl`, уборщик простоя, карантин сломанных агентов (`places.py`);
- прокси вызовов браузера к OpenCode своего места по белому списку (`proxy.py`, `rules.py`), включая поток
  `/event` (SSE);
- перезагрузку места после того, как служба данных сохранила нового агента Конструктора (`reload.py`);
- «тормоз прогона» — принудительную остановку мест LLM-прогона при нехватке памяти сервера (часть `__init__.py`,
  `places.py`, `proxy.py`, `reload.py`);
- ручки вкладок веб-чата: «Мои агенты» и «События» (`agents_api.py`), «Калькуляторы» (`calc_api.py`), «Уроки»
  (`lessons_api.py`, `lessons.py`), опционально — аналитический чат (`analytics_api.py`, см.
  [16-analytics.md](16-analytics.md));
- кабинет преподавателя: обзор мест, чтение разговоров и агентов студента, перевод доли бюджета, учётки студентов
  (`teacher_api.py`, `teacher_log.py`, `transcript.py`);
- отчёты браузера о падениях и здоровье веб-чата (`web.py`);
- счётчики за час и тела ручек `/health/*` для сторожа (`stats.py` и методы `GatewayRole`).

Интерфейс наружу — только HTTP под префиксом `/pcbk/api/` (и четыре ручки `/health/*`); никакого MCP у этой роли
нет. Вход в приложение браузер делает через `edge` (nginx, сеть `pcbk-front`) — подробности маршрутов edge,
буферов и лимитов nginx — в [17-web-chat-and-edge.md](17-web-chat-and-edge.md); здесь описано только то, что видно
и проверяется в самой роли `core`.

**Переносимость.** Роль в основном переносима на другое предприятие: протокол `/pcbk/api/oc/*`, учётки, сессии,
прокси, перезагрузка агентов, кабинет преподавателя не знают, что речь о КДМ-1. Специфика заказчика:

- число рабочих мест — константа `PLACE_MAX = 11` в `core/pcbk_core/places.py` (10 студентов + место-образец
  `student_test`);
- адреса сетей мест (`STU_NET`, `172.31` по умолчанию) и диапазон `pcbk-student-NN` — часть docker-compose,
  не кода роли;
- калькуляторы вкладки чата (`calc_api.py`) зовут `DataService` — она знает про участок КДМ-1 (белый список
  тегов, смены); сам `calc_api.py` от этого не зависит — он просто передаёт аргументы;
- остальное (учётки, вход, прокси, перезагрузка, карантин, кабинет) не содержит ничего специфичного для КДМ-1.

Как перенести роль целиком или частично на другое предприятие — [21-extending.md](21-extending.md).

## 0. Файлы роли

| Файл | Что в нём |
|---|---|
| `gateway/__init__.py` | `GatewayRole`: сборка роутера, крючки приложения, `lifespan`, уборщик (`_reaper_loop`), такт кабинета (`teacher_tick`), тормоз прогона (`_halt_watch`), тела `/health/*` |
| `gateway/accounts.py` | `AccountStore` (учётки, места, сессии, замок входа), scrypt, команда `python -m pcbk_core.gateway.accounts` |
| `gateway/api.py` | ручки `/pcbk/api/auth/*`, `/pcbk/api/workplace*`, cookie, `GatewayRefusal`, `front_only`/`configured`/`current_account`/`origin_checked` |
| `gateway/places.py` | `SpCtl` (клиент Docker API через `sp-ctl`), `Workplaces` (пробуждение, готовность, уборщик, тормоз прогона) |
| `gateway/proxy.py` | прокси `/pcbk/api/oc/*`, поток `/event` (SSE), занятость, журнал |
| `gateway/rules.py` | белый список вызовов OpenCode: таблица правил, сборщики тел, `match()` |
| `gateway/reload.py` | `AgentReloads` — перезагрузка места после сохранения агента, `TurnGates`, карантин |
| `gateway/teacher_api.py` | кабинет преподавателя: обзор, журнал, разговоры, агенты, доли, учётки студентов |
| `gateway/teacher_log.py`\* | журнал действий преподавателя (`teacher_actions`, `teacher_heartbeat`) |
| `gateway/transcript.py` | пересказ разговора места (`sessions_view`, `transcript`) для кабинета |
| `gateway/agents_api.py` | `GET /health/agents`, `GET /pcbk/api/agents` («Мои агенты»), `GET /pcbk/api/events` |
| `gateway/calc_api.py` | `POST /pcbk/api/calc/{tool}` — вкладка «Калькуляторы» |
| `gateway/lessons_api.py` | `GET /pcbk/api/lessons*` — вкладка «Уроки» |
| `gateway/lessons.py` | чтение пакета уроков с диска (без FastAPI) |
| `gateway/web.py` | отчёты браузера (`POST /pcbk/api/web/report`), `GET /health/web` |
| `gateway/stats.py` | `GatewayStats`, `TeacherStats` — счётчики за час |
| `gateway/analytics_api.py` | ручки аналитического чата КДМ-1 (опционально, см. [16-analytics.md](16-analytics.md)) |

\* `teacher_log.py` лежит не в пакете `gateway/`, а в `core/pcbk_core/teacher_log.py` — это модуль верхнего
уровня (его берёт и роль `llm` для долей бюджета, и команда администратора).

Вспомогательные модули вне пакета, которые разбираются здесь же:

| Файл | Что в нём |
|---|---|
| `core/pcbk_core/places.py` | единственное число мест стенда: `PLACE_MAX`, `PLACES`, `PLACE_NN`, `CALLER_RE`, `place_text()` |
| `core/pcbk_core/zones.py` | зоны адресов (`is_front`, `is_admin`) и проверка сети места у вызывающего `student-NN` |
| `core/pcbk_core/hooks.py` | реестр `AppHooks` — обработчики ошибок и пределы тела по префиксу пути, общий для всех ролей |
| `core/pcbk_core/app.py` | протокол `Role`, `create_app` — сборка приложения из ролей |
| `core/pcbk_core/logs.py` | настройка журнала (`setup_logging`) |

## 1. Зоны и общий порядок проверок

Все ручки шлюза существуют только в зоне `pcbk-front`: `front_only` (`api.py`) требует, чтобы **и** адрес
назначения, **и** адрес источника соединения входили в `zones.FRONT_NETS` (`172.31.250.32/28` — сеть edge, плюс
петля `127.0.0.0/8` и `::1/128`); иначе — `404 not_found` без `Allow`, как если бы маршрута не было (из сетей
мест ручки шлюза не видны). Решение по обоим адресам сразу — иначе клиент из сети места, дотянувшийся до адреса
`core` в `pcbk-front`, получил бы ручки шлюза. Проверку делает `core/pcbk_core/zones.py:is_front`.

Общий для большинства ручек порядок зависимостей FastAPI (конкретные отличия — в разделах ниже):

1. **зона** — `front_only` (вне `pcbk-front` — `404`);
2. **ошибка настройки** — `configured(role)` (`role.config_error` не пуст — `503 "Шлюз не настроен: <причина>"`, причина обрезана до `CONFIG_ERROR_SHOWN` = 60 знаков);
3. **источник записи** (только у ручек, меняющих состояние) — `origin_checked(role)`: `403 origin`, если запрос не со своей страницы;
4. **сессия** — `current_account(role)`: `401 need_login`, если нет `cookie` сессии или сессия не найдена;
5. у ручек с пробуждением места и подтверждением предупреждения — **сессия раньше источника**: запрос без учётки получает `401` без строки журнала и без счётчика `origin_refused`.

Ответы об ошибках — всегда вида OpenCode: `{"name": "<Класс>", "data": {"message": "<текст>"}}`, без
`WWW-Authenticate` (`api.py:gw_error`). Отказ зависимости бросает `GatewayRefusal` — подкласс
`starlette.exceptions.HTTPException` с уже готовым `Response`; единый обработчик `api.py:http_exception_handler`
(поставленный в реестр `hooks_of(app)` для каждого пути `/health/*` и для `/pcbk/api/`) отдаёт его как есть, а
`404`/`405` маршрутизации превращает в `404 not_found` без `Allow` (список методов не должен подсказывать, какие
ручки существуют).

Два предела тела, сверяемых до чтения тела (через `AppHooks.body_limit`, `core/pcbk_core/hooks.py`):

| Префикс | Предел | Источник |
|---|---|---|
| `/pcbk/api/` | `BODY_LIMIT` = 65536 байт (64 КиБ) | константа `gateway/__init__.py` |
| `/pcbk/api/oc/` | `gw.GW_MAX_BODY_BYTES` (умолчание 524288 байт = 512 КиБ) | `GatewaySettings.GW_MAX_BODY_BYTES` |

Выбор — по самому длинному подходящему префиксу, так что у `/pcbk/api/oc/` действует свой, больший предел.
У `/pcbk/api/oc/` есть ещё общий бюджет байт тел всех мест разом (`GW_BODY_BUDGET_BYTES` = 16 МиБ или
`GW_MAX_BODY_BYTES`, если он больше) — см. раздел 5.4; превышение бюджета отвечает `503 WorkplaceBusy`, а вне
зоны `pcbk-front` — `404` (чтобы сеть места не узнала о существовании ручки по коду ответа).

## 2. Учётные записи (`accounts.py`)

### 2.1. Таблицы в `core.db`

`core.db` — общий файл SQLite для журнала событий службы данных, журнала вызовов LLM-прокси и таблиц шлюза;
`AccountStore.open()` создаёт свои таблицы `CREATE TABLE IF NOT EXISTS`, ничего не переделывая.

| Таблица | Поля | Назначение |
|---|---|---|
| `gw_accounts` | `login` (PK), `role` (`student`\|`teacher`), `place` (int или `NULL`), `pw_hash`, `disabled`, `created_at` | учётка; место уникально среди **действующих** учёток — частичный уникальный индекс `gw_accounts_active_place` (`WHERE disabled = 0 AND place IS NOT NULL`) |
| `gw_places` | `place` (PK), `login`, `since` | за кем закреплено место; строка пишется при первой выдаче места и не удаляется ни `disable`, ни `remove` — это «надгробие» логина; снимает её только `release_place` |
| `gw_sessions` | `token_sha` (PK, sha256 значения cookie), `login`, `created_at`, `expires_at` | сессии; сам токен в базе не хранится; живых сессий на учётку — не больше `MAX_SESSIONS_PER_LOGIN` = 3 |
| `gw_login_failures` | `login`, `at` | неудачные входы существующих логинов в окне `LOCK_WINDOW_S` = 900 с (15 мин) |
| `gw_heartbeat` | `id` (CHECK `= 1`), `at` | пульс `AccountStore.ping()` — одна перезаписываемая строка |
| `gw_notices` | `login`, `notice`, `acked_at` (PK `login, notice`) | подтверждения предупреждения при первом входе (см. раздел 3.4) |

Неизвестные логины (которых нет в `gw_accounts`) не растят таблицу и не попадают в базу вовсе: их замок —
только в памяти (`AccountStore.unknown_failures`, не больше `UNKNOWN_KEYS` = 10000 ключей, вытесняется самый
старый незапертый). Так перебор чужих логинов не создаёт строк в базе.

### 2.2. Пароли

Хеш — `scrypt$16384$8$1$<соль 16 байт hex>$<хеш 32 байта hex>` (`hash_password`/`verify_password`), параметры
`SCRYPT_N=16384, SCRYPT_R=8, SCRYPT_P=1`, соль `SALT_BYTES=16`, хеш `HASH_BYTES=32`. Пароль кодируется
`UTF-8` с `surrogatepass`, чтобы одиночный суррогат кодировался одинаково при заведении и сверке. Неизвестный
логин сверяется по «хеш-пустышке» (`_dummy_hash()`, считается один раз на процесс, ещё при `open()`) — ответ
идёт то же время, что и у известного логина, не выдавая существование учётки по задержке. Пароль не попадает ни
в базу (кроме хеша), ни в текст исключений, ни в журнал.

Правила: `PASSWORD_MIN=8`, `PASSWORD_MAX=128` знаков; логин — `LOGIN_RE = r"[a-z][a-z0-9._-]{2,31}"` (3…32 знака,
первый — буква `a-z`).

### 2.3. `AccountStore` — методы (все — точка SQLite-соединения `_op`, по одному на операцию)

| Метод | Делает |
|---|---|
| `open()` | файл (`mode=rwc`), `PRAGMA journal_mode=WAL`, таблицы; идемпотентна, не бросает (`False` + `last_error`) |
| `ping()` | настоящая запись (`gw_heartbeat`) для пробы доступности; не бросает |
| `add(login, role, place, password, *, same_person=False)` | новая учётка; место, занятое другой действующей учёткой, не выдаётся; логин «надгробия» — только `same_person=True`, тем же студентом и на то же место |
| `set_password(login, password, *, student_only=False)` | новый пароль; сессии учётки закрываются, замок снимается |
| `disable(login, *, student_only=False)` | учётка отключается, сессии закрываются; закрепление места остаётся |
| `enable(login)` | включает отключённую учётку **студента** на её же место (только если место закреплено за ней и свободно) |
| `remove(login)` | удаляет только **отключённую** учётку; `gw_places` не трогает (логин остаётся за местом); подтверждения предупреждения стираются |
| `check_release(place)` / `release_place(place)` | снимает закрепление места — только у пробной учётки (`PROBE_LOGIN = r"d[0-9]+[a-z]?-probe-[a-z0-9-]+"`) без действующей учётки на месте |
| `list()` | все учётки (в т.ч. отключённые), студенты по местам, затем преподаватели |
| `bindings()` | закрепления мест: `(место, логин, есть ли учётка этого логина на этом месте)` |
| `students()` | число действующих учёток-студентов |
| `notice_acked(login, notice)` / `ack_notice(login, notice)` | предупреждение при первом входе (раздел 3.4) |
| `verify(login, password)` | учётка при верном пароле; неверный/отключённая/неизвестная — `None` |
| `exists(login)` / `status(login)` | есть ли учётка / `"active"`\|`"disabled"`\|`None` |
| `open_session(login)` → токен | новая сессия; лишние сверх `MAX_SESSIONS_PER_LOGIN` — закрываются по порядку записи (самые старые) |
| `session(token)` | учётка сессии; истёкшая удаляется отдельной операцией записи |
| `close_session(token)` | закрывает сессию по токену |
| `locked(login, pending=())` / `failure(login, at=None)` | замок входа (раздел 3.3) |
| `unlock(login, *, student_only=False)` | снимает замок: строки таблицы и счётчик в памяти |

### 2.4. Команда администратора

```
python -m pcbk_core.gateway.accounts <команда>
```

запускается как `docker exec -i pcbk-core python -m pcbk_core.gateway.accounts <команда>` (пароль — одна строка
через `stdin`, не в argv и не в истории оболочки). База — `Settings.from_env().DB_PATH`; `release-place` ещё
сверяет агентов места по `Settings.from_env().AGENTS_ROOT`. Код возврата — `0` удача, `1` отказ (одна строка в
`stderr` без пароля).

| Команда | Аргументы | Делает |
|---|---|---|
| `add` | `--login L --role student\|teacher [--place N] [--generate] [--same-person]` | заводит учётку; `--generate` печатает пароль один раз в stdout |
| `passwd` | `--login L [--generate]` | новый пароль; сессии закрываются, замок снимается |
| `disable` | `--login L` | отключает учётку |
| `enable` | `--login L` | включает отключённую учётку студента на её же место |
| `remove` | `--login L --yes` | удаляет только отключённую учётку |
| `unlock` | `--login L` | снимает замок входа |
| `release-place` | `--place NN --yes` | снимает закрепление места (только у пробной учётки после `disable` и после архива агентов места) |
| `list` | — | учётки (роль, место, статус, «предупреждён»: да/нет/«-» у преподавателя) и закрепления мест, без хешей |
| `journal` | `--last N` (1…1000, умолчание 50) | последние строки журнала действий преподавателя (только чтение, `TeacherLog`) |

Примеры:

```bash
# завести студента на место 05 с порождённым паролем (печатается один раз)
docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login ivanov --role student --place 5 --generate

# завести преподавателя, пароль со своего терминала
docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login prepod --role teacher

# сменить пароль, прочитав его из файла секрета (как заводят места при выкладке)
docker exec -i pcbk-core python -m pcbk_core.gateway.accounts passwd --login ivanov < secrets/ivanov.pw

# снять замок входа после пяти неверных попыток
docker exec -i pcbk-core python -m pcbk_core.gateway.accounts unlock --login ivanov

# список учёток и закреплений мест
docker exec pcbk-core python -m pcbk_core.gateway.accounts list

# последние 20 строк журнала действий преподавателя
docker exec pcbk-core python -m pcbk_core.gateway.accounts journal --last 20
```

Те же операции (`add` только студентом и без `same_person`, `set_password`/`disable`/`unlock` — только с
`student_only=True`, `enable`) зовёт кабинет преподавателя через `/pcbk/api/teacher/accounts*` — см. раздел 9.6.

## 3. Вход, выход, сессии (`api.py`)

### 3.1. Cookie и маршруты

Сессия — cookie `pcbk_session` (`HttpOnly; Secure; SameSite=Strict; Path=/pcbk/api/`), в базе хранится `sha256`
значения. Номер места берётся только из учётки сессии, никогда из параметров запроса.

| Маршрут | Метод | Доступ | Что делает |
|---|---|---|---|
| `/pcbk/api/auth/login` | POST | без сессии, с проверкой источника | логин/пароль → учётка + cookie сессии |
| `/pcbk/api/auth/logout` | POST | без сессии, с проверкой источника | закрывает предъявленные сессии, удаляет cookie |
| `/pcbk/api/auth/me` | GET | сессия | учётка сессии + `notice` (номер непринятого предупреждения или `null`) |
| `/pcbk/api/auth/notice` | POST | сессия раньше источника | подтверждение предупреждения; тело ровно `{"notice": NOTICE}` |

Тело `POST /pcbk/api/auth/login` — `{"login": str, "password": str}`; неверный вид — `400 bad_body`. Удачный
вход отвечает `{"login", "role", "place"}` (`Account.to_json()`, место — строка `"NN"` или `null`) с заголовком
`Set-Cookie`.

**Две и больше `pcbk_session` в одном запросе** (например, остаток старой сессии после смены пароля в другой
вкладке) — все предъявленные сессии (первые `MAX_SESSION_COOKIES` = 8) закрываются, ответ `401` и набор
`Set-Cookie` на удаление (`clear_cookies`): строки удаления собираются под все сочетания путей-предков запроса,
host-only и до `COOKIE_MAX_DOMAINS` = 3 родительских доменов, с/без `Partitioned`; если сумма байт превышает
`COOKIE_HEADERS_BUDGET` = 16 КиБ — только host-only.

### 3.2. Источник записи (CSRF)

`same_origin(request, origin)`: запись принимается, если `Sec-Fetch-Site: same-origin` **или** заголовок
`Origin` в точности равен `role.gw.GW_ORIGIN`. Пустой `GW_ORIGIN` не совпадает ни с чем. Отказ — `403 origin`,
счётчик `origin_refused`; строка журнала `gw origin refused path=… n=… since_s=…` — не на каждый отказ, а
свёрнутая, одна за `ORIGIN_LOG_S` = 60 с (класс `_RolledLines`).

### 3.3. Замок входа

Замок — `LOCK_FAILURES` = 5 неудачных входов за `LOCK_WINDOW_S` = 900 с (15 минут), пока самая старая из пяти
попыток не выйдет из окна; снять раньше — `unlock`. Сверка идёт через `LoginGate` (`gateway/__init__.py`):

- не больше `LOGIN_PARALLEL` = 2 сверок разом (`scrypt` — тяжёлая операция в потоке), ждущих — не больше
  `LOGIN_QUEUE` = 8, незаписанных неудач — не больше `LOGIN_BACKLOG` = 64 (иначе `429 busy` без `scrypt`);
- неудача сверки сразу попадает в память (`pending`) до освобождения места очереди — так соседняя попытка того
  же логина видит её ещё до записи в таблицу; запись в `gw_login_failures` и строка журнала `gw login refused
  login=… reason=…` идут потом, в отдельной задаче-писателе, вне места очереди — ни ответ, ни освобождение места
  не ждут записи.

Неверный пароль, замок и перегрузка очереди не отличаются по времени ответа от «неизвестный логин»: неизвестный
сверяется по хешу-пустышке столько же.

### 3.4. Предупреждение при первом входе

`NOTICE = "teacher-reads-1"` — номер текущей редакции текста (сам текст — на стороне веба,
`web/src/texts.ts`, см. [17-web-chat-and-edge.md](17-web-chat-and-edge.md)). `GET /pcbk/api/auth/me` отдаёт
`"notice": NOTICE`, если студент эту редакцию не подтвердил, иначе (и у преподавателя всегда) `null`.
`POST /pcbk/api/auth/notice` с телом ровно `{"notice": NOTICE}` отвечает `204` (повтор — тоже `204`, без
повторной строки журнала); другой номер или форма тела — `400 bad_body`. Место предупреждение не будит.

### 3.5. Рабочее место (ручки `/pcbk/api/workplace*`)

| Маршрут | Метод | Доступ | Что делает |
|---|---|---|---|
| `/pcbk/api/workplace` | GET | сессия | состояние места учётки (без пробуждения, из памяти) |
| `/pcbk/api/workplace/wake` | POST | сессия раньше источника | будит место учётки |

У преподавателя места нет — `403 no_place`. Пробуждение места, остановленного тормозом прогона (раздел 7), —
`503 run_halted`. Удачное пробуждение отвечает `202` с `PlaceView.to_json()` (`{"place", "state", "detail"}`).

## 4. Рабочие места (`places.py`, `core/pcbk_core/places.py`)

### 4.1. Число мест и сеть

`core/pcbk_core/places.py` — единственная константа `PLACE_MAX = 11` (мест в стенде: студенты 01…10 плюс
учётка-образец `student_test` на месте 11); `PLACES = range(1, 12)`, `PLACE_NN` — regex двузначного номера,
`place_text()` — диапазон текстом («01…11») для сообщений об ошибках.

Шлюз будит и усыпляет места **только** через `sp-ctl` (контейнер-прокси сокета Docker, см. раздел 4.5) — у
шлюза нет списка контейнеров, имя контейнера (`pcbk-student-NN`) сверяется с `PLACE_NAME` перед подстановкой в
путь Docker API. У каждого места свой `httpx.AsyncClient` (свой пул соединений, своя пустая банка cookie —
`allowed_domains=[]`): соединений на место — не больше `PLACE_CONNECTIONS` = `MAX_STREAMS_PER_LOGIN(3) +
PROXY_PARALLEL(2) + 2 + TEACHER_PARALLEL(1)` = 8 (потоки учётки, вызовы прокси, два служебных — уборщик/
пробуждение и воркер перезагрузки, и чтения кабинета). Пароль места — файл `{GW_PLACE_PW_DIR}/student-NN.pw`
(первая строка, `GW_PLACE_PW_DIR` по умолчанию `/run/secrets`), читается один раз и кешируется; к месту он
уходит заголовком `Authorization: Basic base64("opencode:" + пароль)`.

### 4.2. Жизненный цикл места

| Состояние (`PlaceView.state`) | Когда |
|---|---|
| `sleeping` | ни готово, ни идёт пробуждение, ни есть запомненная неудача |
| `starting` | идёт задача пробуждения (`_wake`) |
| `ready` | место готово (флаг `_ready`, привязанный к `StartedAt` контейнера) |
| `failed` | последнее пробуждение не удалось; `detail` — текст из `PLACE_TEXTS` |

Пробуждение (`Workplaces.wake(n)`) — `touch` (считается активностью) и задача **одна на место** (single-flight);
ответ отдаётся сразу, без ожидания готовности. Если место уже готово и это подтверждено не дольше такта уборщика
назад (`_fresh_ready`) — ответ «готово» без единого запроса к месту и без строки журнала (событие `wake_ok`
только в памяти). Иначе — `_wake_locked(n)` под `asyncio.Lock` места:

1. пароль места (`basic(n)`) — без него `no_password`, `start` не зовётся;
2. `SpCtl.inspect(n)` — контейнера нет → `missing`; уже `running` с тем же `StartedAt`, что флаг готовности → без
   `start`;
3. `SpCtl.start(n)` (`204` запущено / `304` уже шло), затем `_await_ready` — `GET /agent` места до `200`:
   попытка `READY_ATTEMPT_S` = 2 с, пауза `READY_PAUSE_S` = 0.5 с, общий срок `GW_WAKE_TIMEOUT_S` (умолчание
   60 с). `401` → `place_auth`; инспект показывает `Restarting`/новый `StartedAt`/не `running` → `crashed`;
   `400` с ошибкой конфигурации OpenCode и заданным `repair` — карантин (раздел 6.3); срок вышел → `timeout`.

Виды неудачи (`PLACE_TEXTS`, тексты студенту): `down` (нет связи с `sp-ctl`), `refused`, `missing`, `no_password`,
`place_auth`, `crashed`, `timeout`, `config` (сломана настройка места), `run_halted` (тормоз прогона).

### 4.3. Уборщик простоя

Такт (`Workplaces.tick`, вызывается из `GatewayRole._reaper_loop` каждые `GW_REAPER_TICK_S` секунд, умолчание
30 с): `ping` → если `sp-ctl` жив, `inspect` всех мест параллельно (ни один не ответил → `sp_ctl = "down"`, как
без `ping`) → в первом успешном проходе — одна попытка `GET /agent` каждому работающему месту без флага
(`_restore`, чтобы открытые до перезапуска `core` вкладки не требовали повторного пробуждения) → остановка
простаивающих мест (`_reap`): простой `idle_s(n)` ≥ `GW_IDLE_STOP_S` (умолчание 1800 с = 30 мин) и сессия места
не занята (`GET /session/status`, иначе ждёт до `2×` предела).

Простой считается по монотонным часам: от последней активности (`touch`, которую ставит пробуждение и любой
не-`GET` вызов прокси), а без неё — с момента, когда шлюз впервые увидел место работающим. Неудавшийся `stop`
оставляет место в `running`; повтор — не раньше паузы, растущей вдвое от `2×GW_REAPER_TICK_S` до
`GW_IDLE_STOP_S`. Молчание такта дольше `SILENT_TICKS(4) × GW_REAPER_TICK_S` — строка `health()` «уборщик
простоя молчит».

### 4.4. Доступ к месту из кода роли

| Метод `Workplaces` | Для чего |
|---|---|
| `request(n, method, path, …)` | обычный вызов (ответ `httpx.Response` как есть) — для внутренних проверок (готовность, занятость) |
| `fetch(n, method, path, …, max_bytes)` | вызов с ответом, прочитанным не больше `max_bytes` — для прокси и кабинета; `PlaceAnswerTooLarge`/`PlaceAnswerEncoded` при превышении/сжатии |
| `stream(n, path)` | поток `/event` (SSE), сроки `STREAM_TIMEOUT`, заголовки — не дольше `STREAM_OPEN_S` = 10 с |

Клиент места не берёт прокси окружения (`trust_env=False`), не следует перенаправлениям, cookie места не
хранятся и не возвращаются (пустая банка с `allowed_domains=[]`).

### 4.5. `SpCtl` — клиент прокси сокета Docker

`SpCtl` (`places.py`) ходит к `sp-ctl` (`SP_CTL_URL`, умолчание `http://pcbk-sp-ctl:2375`) по Docker API
`/v1.44`. Контейнер `sp-ctl` (`wollomatic/socket-proxy`, сеть `pcbk-ctl` отдельно от прочих — на ней только
`sp-ctl` и `core`, см. `compose.yaml`) принимает только от хоста `pcbk-core` (`-allowfrom=pcbk-core`) и только
`GET /_ping`, `GET .../containers/pcbk-student-NN/json` и `POST .../containers/pcbk-student-NN/{start,stop}` —
больше ничего (нет `exec`, нет удаления, нет доступа к другим контейнерам). Таймауты: `PING_TIMEOUT_S` = 3 с
(`ping`/`inspect`), `ACTION_TIMEOUT_S` = 20 с (`start`/`stop`). `SpCtlError.kind`: `down` (нет связи/срок),
`missing` (`404` — контейнера нет), `refused` (прочий код).

## 5. Прокси к OpenCode (`proxy.py`, `rules.py`)

### 5.1. Порядок проверок

1. **зона** `front_only` → **настройка** `configured` → **вход** `current_account` — зависимости роутера,
   до обработчика;
2. в обработчике: преподаватель или нет места у учётки — `403 teacher` (писать в разговоры студента
   преподаватель не может — это только у кабинета, на чтение);
3. **источник**: не-`GET` — `same_origin`; у `GET` — `Sec-Fetch-Site` должен быть `same-origin` или `none` (или
   заголовка нет вовсе) — иначе `403 origin`;
4. **сырой путь**: `scope["raw_path"]` должен начинаться ровно с байт `/pcbk/api/oc`, далее `/` или конец —
   иначе `403 bad_path` (защита от `/pcbk/api/o%63/…`, который Starlette привёл бы сюда по раскодированному
   пути);
5. **правило белого списка** — `rules.match(method, rest)`: нет правила для пары (метод, путь) — `403
   not_whitelisted`;
6. **готовность места** — не готово → `503 WorkplaceNotReady` (не будит: будит только страница входа; у
   `event` — свой порядок, раздел 5.3; у `summarize` — ответ `200 true` без обращения к месту);
7. **тело** — сборщик правила (раздел 5.2); не JSON или не разобралось — `400 bad_body`; не-`GET` отмечает
   активность места (`touch`) до пересылки;
8. только для `prompt_async` — ворота перезагрузки места (`reloads.gates.turn(n)`, раздел 6.2) оборачивают весь
   следующий шаг целиком; не дождался — `503 WorkplaceBusy "reloading"` без счётчика `refused`;
9. внутри `_forward` (под общим сроком `PROXY_DEADLINE_S`): **занятость места** — слот `_PlaceGate` (раздел
   5.4), затем для `prompt_async` с полем `agent` — сверка «агент есть у места», для `permission_reply` с
   `reply: "once"` — сверка висящего разрешения (раздел 5.2), и только потом — сама пересылка (`fetch`).

### 5.2. Белый список вызовов (`rules.py`)

Путь к месту собирается только из шаблона правила и id, уже проверенных regex `ID_RE` (`ses_`/`msg_`/`per_`/
`que_` + 26 знаков `[0-9A-Za-z]` — формат id OpenCode); как есть к месту не уходит ничего, тело — из сборщика
правила, строка запроса — только ключи правила со значениями `true`/`false` (`filter_query`).

| Имя | Метод | Шаблон пути | Сборщик тела | Что делает |
|---|---|---|---|---|
| `session_list` | GET | `/experimental/session` | — | список разговоров (ключи `roots`, `archived`) |
| `session_create` | POST | `/session` | `_always_empty` → `{}` | новый разговор |
| `session_status` | GET | `/session/status` | — | карта занятости сессий места |
| `session_get` | GET | `/session/{SES}` | — | разговор |
| `session_update` | PATCH | `/session/{SES}` | `build_update` | название (1…200 знаков) и/или `time.archived` (целое 0…2⁵³−1 или `null`) |
| `session_messages` | GET | `/session/{SES}/message` | — | сообщения разговора |
| `prompt_async` | POST | `/session/{SES}/prompt_async` | `build_prompt` | ход: только текстовые части (до 20, по 20000 знаков) и необязательное имя агента |
| `abort` | POST | `/session/{SES}/abort` | — | прервать ход |
| `permission_list` | GET | `/permission` | — | список висящих разрешений |
| `permission_reply` | POST | `/permission/{PER}/reply` | `build_perm_reply` | `{"reply": "once"\|"reject"}` |
| `question_list` | GET | `/question` | — | список вопросов |
| `question_reply` | POST | `/question/{QUE}/reply` | `build_answers` | до 10 списков по 20 строк, по 2000 знаков |
| `question_reject` | POST | `/question/{QUE}/reject` | — | отклонить вопрос |
| `event` | GET | `/event` | — | поток SSE (раздел 5.3, свой порядок проверок) |
| `summarize` | POST | `/session/{SES}/summarize` | — | заглушка: `200 true` без обращения к месту (сжатие разговора на стенде не нужно) |

Особые правила:
- **`prompt_async` с полем `agent`** — сначала `GET /agent` своего места: имя должно быть среди ответа с
  `native: false`, `mode` из `AGENT_MODES = ("primary",)` (`_check_agent`), иначе `400 "Агент «…» недоступен"`;
- **`permission_reply` с `reply: "once"`** — сначала `GET /permission`: разрешение должно висеть и быть из
  `APPROVABLE = {"doom_loop"}` (`_check_permission`), иначе `403`.

Отказы по правилам несут постоянный текст без чисел и без ввода клиента (`RULE_MESSAGES`); имя агента в текст
отказа не идёт как эхо, только проверенное `AGENT_NAME = r"[A-Za-z0-9._-]{1,64}"`.

### 5.3. Поток `/event` (SSE)

`GET /pcbk/api/oc/event` — отдельный путь без тела прокси-вызова:

1. у учётки уже `MAX_STREAMS_PER_LOGIN` = 3 открытых потока — `429 streams` (не чаще одной свёрнутой строки
   журнала за `NOT_READY_LOG_S` = 60 с);
2. место не готово — `503 not_ready` (не будит);
3. у запроса есть тело — `400 bad_body`;
4. открывается поток места (`Workplaces.stream`); `401` → `502 place_auth`; не `200` → `502 BadGateway no_answer`
   (не `503`, чтобы кнопка «Разбудить» не предлагалась готовому месту); сжатый ответ — как нет ответа;
5. занимается слот учётки (`GatewayStats.stream_opened`) — только после `200` места, перед
   `StreamingResponse`.

Дальше `_EventStream`: задача-читатель прогоняет сырые куски через `filter_sse` в очередь на `STREAM_QUEUE_MAX`
= 1 событие (замер памяти) — браузер не читает, читатель ждёт `put`, давление уходит к месту, а не в память
шлюза. `filter_sse` пропускает только события, чей `type` — в `rules.ALLOWED_EVENTS` (17 типов + 3 служебных —
`server.connected`, `server.heartbeat`, `server.instance.disposed`), и целиком собранные кадром `data:
…\n\n`; событие больше `SSE_MAX_EVENT_BYTES` = 512 КиБ — обрыв потока (`oversize`). Отброшенные по типу события
журналируются по типу, не по содержимому: первый раз за тип в потоке — строка `gw event place=NN
dropped_type=<тип>` (не больше `STREAM_DROP_TYPES_MAX` = 8 разных ключей за поток, дальше — общий счётчик без
строки), при закрытии — итог `gw event place=NN dropped=<всего> types=…`.

Концы потока (`closed_by`): `client` (браузер ушёл), `place` (конец/обрыв места), `session` (сессия кончилась
или место другое — перепроверка раз в `role.stream_recheck_s`, умолчание 10 с), `store` (`STREAM_STORE_FAILURES`
= 3 сбоя базы подряд при перепроверке), `timeout` (тишина места дольше `places.SSE_READ_TIMEOUT_S` = 60 с),
`oversize`, `lifetime` (предел жизни потока `STREAM_MAX_S` = 1800 с = 30 мин — зависшая вкладка не держит слот
часами). `timeout` и `oversize` считаются в счётчик `stream_broken`.

### 5.4. Занятость, очереди, пределы

| Параметр | Значение | Смысл |
|---|---|---|
| `PROXY_PARALLEL` | 2 | вызовов прокси к одному месту разом |
| `PROXY_QUEUE` | 8 | ждущих сверх `PROXY_PARALLEL` — дальше `503 WorkplaceBusy "busy"` |
| `PROXY_DEADLINE_S` | 25.0 | весь ход вызова (edge ждёт 45 с) |
| `PROXY_QUEUE_WAIT_S` | 5.0 (`PROXY_DEADLINE_S(25) − PLACE_READ_S(20)`) | ожидание слота внутри срока хода — месту после слота остаётся не меньше `PLACE_READ_S` = 20 с |
| `UPSTREAM_MAX_BYTES` | 4 МиБ | ответ места в памяти шлюза (прокси-вызов) |
| `GW_BODY_BUDGET_BYTES` | 16 МиБ | общий бюджет байт тел `/pcbk/api/oc/` всех мест разом (превышение — `503 busy`, вне зоны — `404`) |
| `SEND_CHUNK` | 64 КиБ | кусок отправки ответа места браузеру |
| `PLACE_LIST_MAX_BYTES` | 1 МиБ | ответ сверки `/agent`/`/permission` |

Ответ места браузеру: `401` → `502 place_auth`; нет ответа (любая `httpx.HTTPError`/`OSError`/срок) → `503
no_answer`, место помечается неготовым (`mark_unready`), счётчик `upstream_failed`; больше `UPSTREAM_MAX_BYTES`
→ `502 too_big`; иначе тот же код места и тело как есть, `Content-Type` — только `application/json` и
`text/plain` проходят как есть, прочий (включая отсутствующий у непустого тела) — `application/octet-stream`
(чтобы HTML или SVG места не стали страницей в браузере).

## 6. Перезагрузка места после сохранения агента (`reload.py`)

### 6.1. Зачем и когда

Служба данных пишет файл нового/изменённого агента Конструктора на диск места (сам файл агента, его формат
`frontmatter` и правила — предмет образа места, [14-workplaces-and-agents.md](14-workplaces-and-agents.md));
OpenCode должен перечитать конфигурацию (`POST /instance/dispose`). Чтобы не оборвать ход студента, `dispose`
идёт только когда:

- `GET /session/status` места пуст (нет занятых сессий) — своя проверка `_idle`, отдельная от уборщика;
- пустая карта продержалась `IDLE_SETTLE_S` = 3 с (фоновый заголовок разговора мог ещё не дописаться);
- прошло не меньше `TURN_START_GRACE_S` = 5 с с конца последней пересылки `prompt_async` (её фиксирует
  `note_turn_start`, вызываемый прокси при выходе из ворот);
- место прошло **ворота** `TurnGates` как писатель (`try_reload`) — значит, в этот момент нет ни идущего, ни
  ждущего `prompt_async`.

### 6.2. Ворота (`TurnGates`)

`prompt_async` — читатель (`gates.turn(n)`): если место перезагружается, ждёт не дольше `RELOAD_WAIT_S` = 10 с
(иначе `GateTimeout` → прокси отвечает `503 WorkplaceBusy "reloading"`, без счётчика `refused` — место живо).
Ожидание ворот стоит **снаружи** срока хода прокси (`PROXY_DEADLINE_S`): худший случай `10 + 25 = 35` с < 45 с
edge. Воркер — писатель (`try_reload`/`end_reload`) без ожидания: отказ, если место уже перезагружается, идёт
`prompt_async` или кто-то его ждёт.

### 6.3. Цикл воркера (`AgentReloads.run`, такт `RELOAD_TICK_S` = 2 с)

1. дождаться висящих задач крючка готовности; раз в `DIR_CHECK_S` = 10 с — проверить каталог агентов
   (`store.dir_problem()` → `stats.dir_error`);
2. `store.pending()` — очередь мест, ждущих перезагрузки (строки `agent_reloads` учёта, `AgentStore`); сбой —
   `worker_store_failed`, пульса (`checked_at`) нет, проход кончается;
3. по каждому месту очереди — `_reload_place`: неготовое место (спит/будится/`sp-ctl` молчит) пропускается, и
   **метка остаётся навсегда** (переживает сон места и перезапуск `core`); иначе — проверка простоя, `try_reload`,
   `POST /instance/dispose`, затем до `VISIBLE_TRIES` = 3 попыток `GET /agent` (пауза `VISIBLE_PAUSE_S` = 0.5 с),
   пока место не увидит всех ожидаемых агентов (`own_agent_loaded`: `mode: primary` и непустое `description` —
   агент с битым YAML, которого OpenCode грузит `mode: all`, не «увиден»);
4. неудача (`dispose` не `200`, `GET /agent` не ответил) — `reload_failed`, повтор не раньше `RELOAD_RETRY_S` =
   30 с; ожидание дольше `RELOAD_DELAYED_S` = 900 с (15 мин) — один раз `reload_delayed` и строка журнала.

Крючок готовности (`place_ready`, его ставит `GatewayRole` как `workplaces.on_ready`) ставит место в очередь
заново, если оно уже там или у него есть несохранённые строки (`has_saved`) — так пробуждение места не теряет
отложенную перезагрузку.

### 6.4. Карантин файла агента

Файл агента, который OpenCode не может разобрать (`Config…Error` в ответе `400`), ломает всё место. Функция
`repair(n, тело)` (общая для воркера после `dispose` и для `Workplaces._await_ready` при пробуждении):

1. `config_error_path(body)` — тело не похоже на ошибку конфигурации → `not_config` (прежнее поведение: `400`
   как есть);
2. `agents_rel_path(path)` — путь вне каталога агентов места
   (`/etc/pcbk-opencode/opencode/agents/`) → `image` (файл образа, ничего не двигается);
3. у места уже `QUARANTINE_MAX_PER_H` = 3 карантина за `QUARANTINE_WINDOW_S` = 3600 с (час) → `capped` (файл
   остаётся, нужен человек — счётчик `image_config`);
4. иначе — `AgentStore.quarantine` переносит файл в `/srv/agents/quarantine/student-NN/<путь с «/»→«__»>.
   <время UTC>-<6 hex>`, строка агента получает состояние `quarantined` (счётчик `quarantined`).

После любого исхода, кроме `not_config`, воркер или пробуждение зовёт `Workplaces.restart(n, broken=…)`: после
карантина — обычный перезапуск (stop → start), при `image`/`capped` — только `stop`, место гаснет с текстом
`config` (повторных попыток `dispose` раз в `RELOAD_RETRY_S` нет — студент видит текст места, не цикл перезапуска).
Где искать файл на диске и как с ним работать руками — `deploy/README.md`, раздел «Карантин».

## 7. Тормоз прогона

Защищает память сервера во время прогона отбора модели (роль `llm`, `LLM_RUN_*`): когда `llm.brake.event`
(устанавливается потоком проверки памяти роли `llm`) взведён, **места прогона** (`student-NN` из
`llm.run.callers`, без `ops`) принудительно гасятся и больше не будятся.

- `GatewayRole._halt_watch` — фоновая задача, раз в `HALT_WATCH_S` = 1.0 с смотрит флаг защёлки; на новом
  взведении — `_halt_once()`: каждому месту прогона — `Workplaces.halt_place(n)` (снять задачу пробуждения,
  `stop_now` под замком места без проверки простоя, `304` считается удачей);
- `Workplaces.wake(n)` места под защёлкой — `WakeRefused("run_halted")`, без `touch` и без задачи; ответ
  студенту и кабинету — `503 run_halted` (текст — `PLACE_TEXTS["run_halted"]` = «Прогон остановлен: сервер на
  пределе памяти — место не будится.»);
- `Workplaces.restart(n)` под защёлкой — тихий отказ (строка журнала, «готово» снято, без задачи и без
  исключения в воркер перезагрузки);
- каждый такт уборщика, пока защёлка стоит, гасит **любое** место прогона, которое `inspect` видит
  работающим — даже если его подняли мимо шлюза (`docker start` вручную, отменённая задача и т.п.);
- задача `_halt_watch` работает и при `config_error` шлюза (ей нужен только `sp-ctl`), в отличие от уборщика.

Эксплуатационные подробности прогона (какие переменные `LLM_RUN_*` ставить, как проверять) — `deploy/README.md`,
раздел «Прогон отбора модели».

## 8. Ручки вкладок веб-чата

### 8.1. «Мои агенты» и «События» (`agents_api.py`)

| Маршрут | Метод | Доступ | Что делает |
|---|---|---|---|
| `/health/agents` | GET | зона `front` | тело для сторожа (раздел 11) |
| `/pcbk/api/agents` | GET | зона, настройка, сессия | панель «Агенты»: встроенные + свои |
| `/pcbk/api/events` | GET | зона, настройка, сессия | вкладка «События»: находки агентов места |

У преподавателя (нет `account.place`) — `403` «Агенты студентов — в кабинете преподавателя, только на чтение».
Ответ `GET /pcbk/api/agents` — `{"agents": […], "own": k, "limit": MAX_AGENTS}` (`MAX_AGENTS` = 20, из
`agents/store.py`): сначала `IMAGE_AGENTS` (встроенные агенты образа места — `pomoshnik` «Помощник технолога
КДМ-1» и `konstruktor` «Конструктор агентов», оба `state: ready`; сами агенты и их правила — часть образа
места, [14-workplaces-and-agents.md](14-workplaces-and-agents.md)), затем строки учёта места
(`AgentStore.list_place`) в состояниях `visible→ready`, `saved→pending`, `not_visible`/`quarantined`→`broken`.
`own` считает только живые строки учёта (включая `quarantined`).

`GET /pcbk/api/events` — `{"events": […], "limit": EVENTS_LIMIT}` (`EVENTS_LIMIT` = 100, новые первыми), источник
— `AgentFindings.recent` (`role.findings`, таблица `agent_findings` в `core.db`, её пишет служба данных
`report_event` — см. [10-data-service.md](10-data-service.md)); `agent_title` — название из учёта места
(`AgentStore.title_of`), архивный агент — прежнее название, не найденный вовсе — имя агента как есть.

### 8.2. «Калькуляторы» (`calc_api.py`)

`POST /pcbk/api/calc/{tool}` зовёт тот же `DataService`, что HTTP `/api/data/` и MCP, **в процессе**, без HTTP к
самому себе. `CALC_TOOLS = ("catalog_search", "tag_now", "tag_period", "tag_series", "calc_runtime")` —
пять инструментов чтения (без `save_agent`). Проверки: зона → настройка → сессия → источник (после сессии) →
инструмент из списка (иначе `404`) → вызывающий по роли учётки, явно (`caller_for`): преподаватель —
`caller = "teacher"` (`TEACHER_CALLER`), студент с местом — `caller = "student-NN"` (`caller_of`), прочее — `403
"Калькуляторы — только для студента с рабочим местом и преподавателя"` → `role.calc is None` (служба данных не
подключена) — `503 "Калькуляторы недоступны — служба данных не подключена"` → тело — объект JSON → модель
аргументов (та же, что у `/api/data/<tool>`, из `data/http_api.py`) → инструмент службы с каналом `web`.

Аргументы и сами расчёты (наработка, карта Шухарта и т.д.) — предмет службы данных, не шлюза: см.
[10-data-service.md](10-data-service.md) и [11-calculations.md](11-calculations.md). Предел частоты
(`PER_CALLER_LIMIT` = 60 вызовов за скользящее окно 5 минут, `data/budget.py`) — общий у вкладки «Калькуляторы» и
у агентов того же места/у преподавателя как единого вызывающего `teacher`, поскольку оба идут через один
`caller`.

### 8.3. «Уроки» (`lessons_api.py`, `lessons.py`)

| Маршрут | Метод | Доступ | Что делает |
|---|---|---|---|
| `/pcbk/api/lessons` | GET | зона, настройка, **любая** вошедшая учётка | список уроков |
| `/pcbk/api/lessons/{lesson_id}` | GET | то же | текст урока (markdown) |
| `/pcbk/api/lessons/img/{name}` | GET | то же | картинка урока |

В отличие от «Агентов» и «Событий», здесь **нет** отказа по месту — учётка нужна только как проверка входа
(пропускает и студента, и преподавателя). Чтение — `lessons.py`, без FastAPI, в потоке
(`asyncio.to_thread`), с диска **на каждый запрос**, без кеша: правка файла на сервере видна без перезапуска
`core`. Формат пакета, пределы размеров (`MAX_LESSON_BYTES` = 256 КиБ, `MAX_IMAGE_BYTES` = 2 МиБ), защита от
выхода за каталог (`os.lstat` на каждом сегменте + `resolve()` внутри корня) — подробно в
[15-knowledge-and-lessons.md](15-knowledge-and-lessons.md). Каталога/`lessons.json` нет → `{"lessons": []}`, не
ошибка; битый `lessons.json` → `503 "Пакет уроков не читается: …"`; неизвестный `id`/имя картинки → `404`.

### 8.4. Аналитический чат (`analytics_api.py`, опционально)

Подключается только при `role.analytics_enabled = True` (переменная `ANALYTICS_ENABLED`, умолчание — выключено,
старое поведение без аргумента не меняется): роутер `/pcbk/api/analytics/*` не подключается вовсе, путь отвечает
`404` (через `catch_all_router`), не `503` — так существование подсистемы не видно по коду ответа выключенным
клиентам. `role.analytics` (`AnalyticsService | None`) — `None`, если роутер подключён, но координатор не передан
(ручки отвечают заглушкой); сбой корпуса/базы аналитики — не `config_error` шлюза, виден только маршрутам
`/pcbk/api/analytics/*`. Подробности — [16-analytics.md](16-analytics.md).

## 9. Кабинет преподавателя (`teacher_api.py`, `teacher_log.py`, `transcript.py`)

### 9.1. Общий порядок проверок каждой ручки

Зона (`front_only`) → настройка (`configured`) → сессия (`current_account`) → **роль**: не преподаватель → `403
teacher_only`, событие `role_refused`, строка `gw teacher refused login=… reason=role` (свёрнутая, одна на логин
за `ORIGIN_LOG_S`), без обращения к месту и журналу → у POST — **источник** (`origin_checked`) → части пути по
шаблонам (номер места `01…11`, id разговора `ID_RE["ses"]`, имя агента по правилам Конструктора) — не совпало →
`404` без запроса к месту → у ручек места — **на месте есть учётка студента** (действующая или отключённая),
иначе `404 no_student` → **строка журнала действий** `requested` до доступа (не записана → `503 journal`, без
проверки готовности места и без доступа) → у чтения разговоров — **готовность места** (`503 WorkplaceNotReady
asleep`, место не будится) → доступ. Исключение — голое состояние места `GET places/{nn}` (опрос, пока место
просыпается): оно проверяет наличие студента на месте, но не пишет строку `requested` и не трогает журнал —
ответ только из памяти (`Workplaces.view`), без побочных эффектов.

### 9.2. Health и обзор

| Маршрут | Метод | Доступ | Что делает |
|---|---|---|---|
| `/health/teacher` | GET | зона `front` | тело для сторожа (раздел 11); читается и при `config_error` шлюза |
| `/pcbk/api/teacher/overview` | GET | роль преподаватель | обзор 11 мест: логин/свобода, пометки учётки, состояние, доля, расход, число агентов; общий бюджет |
| `/pcbk/api/teacher/journal` | GET | роль преподаватель | последние `JOURNAL_ROWS` = 50 строк журнала действий |

### 9.3. Места: состояние, пробуждение, разговоры, агенты

| Маршрут | Метод | Что делает |
|---|---|---|
| `/pcbk/api/teacher/places/{nn}` | GET | состояние места (из памяти, не будит) |
| `/pcbk/api/teacher/places/{nn}/wake` | POST | пробуждение для просмотра — **тот же код**, что у входа студента (`Workplaces.wake`): сохранённые агенты перезагружаются, кривой файл уходит в карантин |
| `/pcbk/api/teacher/places/{nn}/sessions` | GET | список разговоров места (через само место) |
| `/pcbk/api/teacher/places/{nn}/sessions/{ses}` | GET | стенограмма разговора (раздел 9.4) |
| `/pcbk/api/teacher/places/{nn}/agents` | GET | агенты места из учёта (без обращения к месту) |
| `/pcbk/api/teacher/places/{nn}/agents/{name}` | GET | текст файла одного агента |

Чтение разговоров и агентов **не продлевает простой места** (`touch` не зовётся) и не будит спящее место (кнопка
«Разбудить для просмотра» — отдельный вызов). Запись преподавателя в разговор студента запрещена на уровне
прокси (раздел 5.1, пункт 2), не здесь.

**Ворота кабинета** (`TeacherGate`, `role.teacher_gate`): к одному месту не больше `TEACHER_PARALLEL` = 1 чтения
разом (константа объявлена в `places.py`, кабинет берёт её оттуда — чтобы `places.py` не импортировал ручки
шлюза), ждущих — не больше `TEACHER_QUEUE` = 2, ожидание — не дольше `TEACHER_WAIT_S` = 5 с внутри общего срока
`PROXY_DEADLINE_S`; сверх — `503 busy`. У кабинета свой бюджет соединений (входит в `PLACE_CONNECTIONS`), он не
делит слоты со студентом.

### 9.4. Чтение разговора (`transcript.py`)

Три `GET` к месту (`TEACHER_PLACE_PATHS`: `sessions` → `/experimental/session?roots=true&archived=true`,
`session` → `/session/{ses}`, `messages` → `/session/{ses}/message`) собираются функцией `_teacher_fetch`
только из шаблона и проверенного id — ввод клиента в путь к месту не идёт. Ответ места пересказывается
(`transcript.transcript`), не отдаётся как есть:

- сообщения только `user` и `assistant`, части только `text` (без `synthetic`) и `tool` — прочие виды частей
  пропускаются;
- пределы: `TEXT_MAX` = 20000, `TOOL_TEXT_MAX` = 2000, `ERROR_MAX` = 500, `TITLE_MAX` = 200, `PARTS_MAX` = 200,
  `MESSAGES_MAX` = 2000, `SESSIONS_MAX` = 500 знаков/штук — срабатывание любого предела даёт `"clipped": true`
  в ответе;
- ответ места не по договору (не JSON, разговор без годного id, сообщение другого разговора) —
  `TranscriptError` → `502 bad_answer`;
- одиночные суррогаты (обрезанные эмодзи) заменяются на `U+FFFD`, чтобы ответ закодировался в UTF-8.

Модуль не пишет в журнал — только данные.

### 9.5. Доли бюджета

| Маршрут | Метод | Что делает |
|---|---|---|
| `/pcbk/api/teacher/shares` | POST | перевод доли бюджета между местами, `{"changes": [{"place","from_cents","to_cents"}, …]}` |
| `/pcbk/api/teacher/shares/floor` | POST | «Остановить место»: доля места сводится к полу (оплаченному), разница уходит указанному месту |

Роли `llm` нет — `503 no_llm`; доли не загружены или журнал вызовов не открыт — `503 shares_down` без записи
(иначе «пол» был бы 0). `ShareRefused` → `400` (или `409 Conflict` при конфликте параллельной правки) с текстом
дословно; `SharesStoreError` → `503`. Подробности формулы долей, пола и общего бюджета — в разделе о роли `llm`
(переменные `LLM_SHARE_USD`, `LLM_TOTAL_USD`, `LLM_OPS_SHARE_USD` — [12-llm-proxy.md](12-llm-proxy.md)).

### 9.6. Учётки студентов (только их, не преподавателей)

| Маршрут | Метод | Что делает |
|---|---|---|
| `/pcbk/api/teacher/accounts` | POST | заводит студента: `{"login", "place": "NN"}`, пароль порождается и отдаётся один раз |
| `/pcbk/api/teacher/accounts/{login}/password` | POST | новый порождённый пароль |
| `/pcbk/api/teacher/accounts/{login}/disable` | POST | отключает учётку |
| `/pcbk/api/teacher/accounts/{login}/enable` | POST | включает отключённую учётку на её же место |
| `/pcbk/api/teacher/accounts/{login}/unlock` | POST | снимает замок входа |

Цель всегда — существующая учётка **студента**; учётка преподавателя или её отсутствие — `404 no_account` до
записи в журнал. Пароль — `secrets.token_urlsafe(GENERATED_PW_BYTES)` (как `--generate` команды), отдаётся только
в теле ответа с заголовками `Cache-Control: no-store` и `Pragma: no-cache` — ни в журнал действий, ни в журнал
службы он не идёт. Кабинет не может удалять учётки и не передаёт `--same-person` — это только команда
администратора (`python -m pcbk_core.gateway.accounts remove`/`add --same-person`). Смена студента в занятом
месте ни кабинетом, ни командой `accounts add` не поддерживается (оба отвечают «место NN закреплено за учёткой …
— смена студента в месте только по процедуре владельца») — только ручная процедура администратора вне команды
`accounts` (раздел «Новый студент», `deploy/README.md`).

### 9.7. Журнал действий (`teacher_log.py`)

Таблицы `teacher_actions`/`teacher_heartbeat` в `core.db` (отдельный модуль верхнего уровня, его же читает
команда `accounts journal`). Строка `requested` пишется **до** доступа к данным студента (чтение, пробуждение);
нет строки — нет действия (раздел 9.1). Два разных способа писать изменения:

- **доли** (`shares`/`shares_floor`) — `llm.shares.apply`/`apply_floor` сами вставляют одну строку
  `action="shares", outcome="ok"` в своей же транзакции SQLite (`llm/shares.py`, через `teacher_log.insert_action`);
  отказ по правилам (`ShareRefused`) — отдельная строка `outcome="refused"` через `teacher_log.refused` (без
  гарантии записи — отказ уже случился независимо от неё);
- **учётки студентов** (раздел 9.6) — пара `started` (до операции) → `finish(id, "ok"|"refused"|"error")`
  (после неё), через `_Cabinet.started`/`finish` в `teacher_api.py`.

В строке — только коды, номера мест, id разговоров, имена агентов, логины и числа (центы), никаких текстов
разговоров или паролей. `store_ok` — журнал открыт и подряд нет неудачных записей; любая удачная запись (включая
пульс `ping()` в такте кабинета) обнуляет счёт. Учение `DRILL_TEACHER=journal_down` — ни одна запись (включая
пульс) не доходит до SQL.

### 9.8. Такт кабинета (`GatewayRole.teacher_tick`)

Вызывается из `_reaper_loop` после `store.ping()`: сверка долей (`llm.shares.verify`, по центам, которыми
отказывает прокси — `budget.share_of`) и пульс журнала действий (`teacher_log.ping()`, в `finally` — он
восстанавливается даже после сбоя сверки). Строка журнала службы — только на **смену** итога сверки, не на
каждый такт.

## 10. Отчёты браузера и здоровье веб-чата (`web.py`)

Этот модуль — не раздача статики (статика веб-чата и её сборка описаны в
[17-web-chat-and-edge.md](17-web-chat-and-edge.md)), а приёмник отчётов браузера о собственных сбоях.

| Маршрут | Метод | Доступ | Что делает |
|---|---|---|---|
| `/pcbk/api/web/report` | POST | зона, настройка, сессия, источник | отчёт браузера: `{"kind": K}` |
| `/health/web` | GET | зона `front` | счётчики по видам за час, для сторожа |

`REPORT_KINDS = ("boot_failed", "ui_crash", "turn_silent", "stream_down")`. Ответ — всегда `204`, принят отчёт в
счёт или нет; засчитывается не чаще раза в `WEB_REPORT_EVERY_S` = 60 с на пару (логин, вид) — засчитанный даёт
одну строку `INFO "gw web report login=… kind=…"`, отброшенный — ни строки, ни счёта. Тело читается не больше
`WEB_REPORT_MAX_BYTES` = 256 байт.

## 11. Статистика и здоровье для сторожа

### 11.1. Счётчики (`stats.py`)

`GatewayStats` (окно часа, `WINDOW_S` = 3600 с, из `core/pcbk_core/hourly.py`) — виды: `wake_ok`, `wake_failed`,
`stop_failed`, `place_auth_failed`, `login_failed`, `login_busy`, `origin_refused`, `cookies_duplicate`,
`refused`, `stream_broken`, `upstream_failed`; плюс слоты потоков `/event` по учётке (`MAX_STREAMS_PER_LOGIN` =
3). `TeacherStats` — отдельный объект, виды: `read`, `read_failed`, `change`, `change_failed`, `role_refused`.

### 11.2. `GET /health/gateway`

Тело — `GatewayRole.health_json(now)`, ровно эти ключи (договор со сторожем: лишний ключ — отказ разбора ответа;
`refused_1h`, `streams`, `stream_broken_1h`, `upstream_failed_1h` сторож принимает только все четыре сразу или ни
одного):

| Ключ | Смысл |
|---|---|
| `checked_at`, `config_error`, `store_ok`, `sp_ctl` | состояние роли и учётных записей |
| `running_n`, `ready_n` | мест работает / мест готово |
| `wake_ok_1h`, `wake_failed_1h`, `stop_failed_1h`, `place_auth_failed_1h` | события мест за час |
| `logins_failed_1h`, `origin_refused_1h`, `login_busy_1h`, `cookies_duplicate_1h`, `refused_1h` | события входа/прокси за час |
| `drill` | текущее `DRILL_GATEWAY` |
| `streams`, `stream_broken_1h`, `upstream_failed_1h` | потоки `/event` |

`health()` (быстрая проверка без ввода-вывода, зовётся в цикле событий `/healthz`): `config_error` → «ошибка
настройки: …»; `store.probe_failures/op_failures > 0` → «учётные записи недоступны»; `sp_ctl == "down"` → «нет
связи с прокси сокета — места не будятся»; такт молчит дольше `SILENT_TICKS(4) × GW_REAPER_TICK_S` → «уборщик
простоя молчит»; иначе «принимает вход».

### 11.3. `GET /health/agents`

`agents_health_json` — ровно 15 ключей: `checked_at` (пульс воркера перезагрузки, `null` до первого прохода),
`store_ok`, `dir_error`, `pending`/`pending_oldest_s` (только по **готовым** местам очереди), счётчики за час
`saved_1h`, `refused_1h`, `unavailable_1h`, `visible_1h`, `not_visible_1h`, `quarantined_1h`, `reload_failed_1h`,
`reload_delayed_1h`, `image_config_1h`, и `drill` (`DRILL_AGENTS`).

### 11.4. `GET /health/teacher`

`teacher_health_json` — ровно 13 ключей: `checked_at`/`shares_loaded`/`shares_ok`/`shares_drift`/
`shares_sum_usd`/`total_usd` (доли роли `llm`), `store_ok` (журнал действий), счётчики за час `reads_1h`,
`read_failed_1h`, `changes_1h`, `change_failed_1h`, `role_refused_1h`, и `drill` (`DRILL_TEACHER`). Читается и при
`config_error` шлюза — тогда сверки долей нет, и значение честно «неизвестно» (`null`/`false`/`0`), причина
видна в строке «Шлюз и вход».

## 12. Все маршруты — сводная таблица

| Маршрут | Метод | Кто может | Что делает |
|---|---|---|---|
| `/health/gateway` | GET | зона front | здоровье шлюза (раздел 11.2) |
| `/health/web` | GET | зона front | счётчики отчётов браузера за час |
| `/health/agents` | GET | зона front | здоровье перезагрузки агентов (раздел 11.3) |
| `/health/teacher` | GET | зона front | здоровье кабинета и долей (раздел 11.4) |
| `/pcbk/api/auth/login` | POST | без сессии | вход |
| `/pcbk/api/auth/logout` | POST | без сессии | выход |
| `/pcbk/api/auth/me` | GET | сессия | учётка + предупреждение |
| `/pcbk/api/auth/notice` | POST | сессия | подтверждение предупреждения |
| `/pcbk/api/workplace` | GET | сессия, студент | состояние своего места |
| `/pcbk/api/workplace/wake` | POST | сессия, студент | пробуждение своего места |
| `/pcbk/api/oc/{rest}` | GET/POST/PATCH/PUT/DELETE/HEAD/OPTIONS | сессия, студент | прокси к OpenCode своего места (раздел 5) |
| `/pcbk/api/oc/event` | GET | сессия, студент | поток SSE своего места (раздел 5.3) |
| `/pcbk/api/web/report` | POST | сессия | отчёт браузера о сбое |
| `/pcbk/api/agents` | GET | сессия, студент | «Мои агенты» |
| `/pcbk/api/events` | GET | сессия, студент | «События» |
| `/pcbk/api/lessons` | GET | сессия | список уроков |
| `/pcbk/api/lessons/{id}` | GET | сессия | текст урока |
| `/pcbk/api/lessons/img/{name}` | GET | сессия | картинка урока |
| `/pcbk/api/calc/{tool}` | POST | сессия, студент или преподаватель | калькуляторы (раздел 8.2) |
| `/pcbk/api/teacher/overview` | GET | преподаватель | обзор мест |
| `/pcbk/api/teacher/journal` | GET | преподаватель | журнал действий |
| `/pcbk/api/teacher/places/{nn}` | GET | преподаватель | состояние места |
| `/pcbk/api/teacher/places/{nn}/wake` | POST | преподаватель | пробуждение для просмотра |
| `/pcbk/api/teacher/places/{nn}/sessions` | GET | преподаватель | список разговоров |
| `/pcbk/api/teacher/places/{nn}/sessions/{ses}` | GET | преподаватель | стенограмма разговора |
| `/pcbk/api/teacher/places/{nn}/agents` | GET | преподаватель | агенты места |
| `/pcbk/api/teacher/places/{nn}/agents/{name}` | GET | преподаватель | файл агента |
| `/pcbk/api/teacher/shares` | POST | преподаватель | перевод доли |
| `/pcbk/api/teacher/shares/floor` | POST | преподаватель | «Остановить место» |
| `/pcbk/api/teacher/accounts` | POST | преподаватель | завести студента |
| `/pcbk/api/teacher/accounts/{login}/password` | POST | преподаватель | новый пароль студента |
| `/pcbk/api/teacher/accounts/{login}/disable` | POST | преподаватель | отключить студента |
| `/pcbk/api/teacher/accounts/{login}/enable` | POST | преподаватель | включить студента |
| `/pcbk/api/teacher/accounts/{login}/unlock` | POST | преподаватель | снять замок входа студента |
| `/pcbk/api/analytics/*` | — | сессия (см. [16-analytics.md](16-analytics.md)) | аналитический чат (опционально) |
| `/pcbk/api/{rest}` | любой | — | `404` (ловящий маршрут, ничего не найдено) |

## 13. Настройки, специфичные для шлюза

Полная сводная таблица переменных окружения всех ролей — в [03-configuration.md](03-configuration.md); здесь —
только `GatewaySettings` (`core/pcbk_core/settings.py`), читаемые при старте `core` (`gateway_settings`,
`main.py`). Ошибка в любом поле **не** валит процесс: `build_gateway_role` строит роль с «запасными» значениями
(`GatewaySettings.valid_from_env` — поля, прошедшие проверку по отдельности, прочие — умолчания) и
`config_error = "настройки шлюза: <поле>: <правило>"` — служба данных и LLM-прокси продолжают работать.

| Переменная | Умолчание | Проверка | Что делает |
|---|---|---|---|
| `GW_ORIGIN` | `""` (ни с чем не совпадает) | `https://хост[:порт]`, строчными, без пути | значение `Origin`, которому разрешена запись (раздел 3.2) |
| `GW_IDLE_STOP_S` | `1800.0` | ≥ 60 | простой до сна места |
| `GW_REAPER_TICK_S` | `30.0` | > 0 и ≤ `GW_IDLE_STOP_S / 2` | такт уборщика |
| `GW_WAKE_TIMEOUT_S` | `60.0` | 10…300 | срок готовности места после `start` |
| `GW_SESSION_TTL_S` | `43200.0` (12 ч) | ≥ 300 | срок жизни сессии входа |
| `GW_PLACE_PW_DIR` | `/run/secrets` | — (строка) | каталог файлов `student-NN.pw` |
| `SP_CTL_URL` | `http://pcbk-sp-ctl:2375` | `http://хост[:порт]` | адрес прокси сокета Docker |
| `STU_NET` | `172.31` | два октета `0…255` без ведущих нулей | первые два октета сетей мест (место N — `{STU_NET}.N.0/28`) |
| `GW_MAX_BODY_BYTES` | `524288` (512 КиБ) | целое `65536…4194304` | предел тела `/pcbk/api/oc/` |
| `DRILL_GATEWAY` | `""` | `""`, `freeze_reaper`, `bad_place_pw` | учения уборщика/пароля места |
| `DRILL_AGENTS` | `""` | `""`, `freeze_reload` | учения воркера перезагрузки |
| `DRILL_TEACHER` | `""` | `""`, `shares_stale`, `journal_down` | учения кабинета |

Опечатка в одном поле не сбрасывает прочие к умолчаниям (`valid_from_env`) — так ошибка в `GW_ORIGIN` не
обнуляет `STU_NET`/`SP_CTL_URL`, по которым идёт сверка токенов мест и охрана выхода.

Смежные переменные, которые читает **сторож**, не сама роль (`GW_STALE_S` — порог молчания уборщика для строки
сторожа) — [18-watchdog-and-ops.md](18-watchdog-and-ops.md). Переменные ролей `data` и `llm`, от которых зависит
шлюз (`LESSONS_ROOT`, `AGENTS_ROOT`, `DB_PATH`, `LLM_SHARE_USD`, `LLM_TOTAL_USD` и т.д.) —
[03-configuration.md](03-configuration.md).

## 14. Зависимости и как взять компонент отдельно

Команды compose для чата со шлюзом, местами и LLM-прокси без историана — рецепт (C) в
[02-build-and-deploy.md](02-build-and-deploy.md), §6.2.

`GatewayRole` создаётся `main.py:build_gateway_role` и получает извне (единственный способ достать роль из
кода — через `build_roles`/`build_gateway_role`, см. [01-architecture.md](01-architecture.md)):

- `settings: Settings` — общие пути (`DB_PATH`, `AGENTS_ROOT`, `LESSONS_ROOT`);
- `gw: GatewaySettings` — свои переменные (раздел 13);
- `store: AccountStore` на `settings.DB_PATH`;
- `workplaces: Workplaces` с `SpCtl(gw.SP_CTL_URL)`;
- `stats: GatewayStats`;
- необязательно: `agent_stats` (общий с ролью `data`, иначе роль создаёт свой), `calc: DataService | None`
  (без неё `/pcbk/api/calc/*` отвечает `503`), `llm: LlmRole | None` (без неё — нет долей в кабинете и нет
  тормоза прогона), `analytics`/`analytics_enabled` (см. [16-analytics.md](16-analytics.md)).

Чтобы запустить **только** шлюз (без историана и без LLM-прокси): нужен рабочий `core.db` (хотя бы файл,
`AccountStore.open()`/`TeacherLog.open()` создадут таблицы сами), контейнер `sp-ctl` с доступом к
`docker.sock` и хотя бы один контейнер `pcbk-student-NN` в сети `{STU_NET}.N.0/28`, да файл пароля места
`student-NN.pw`. `calc`/`llm` можно не передавать — роль поднимется, калькуляторы и доли ответят `503`,
остальное (вход, места, прокси, перезагрузка, карантин, кабинет без долей) работает. Ничего из пакета `gateway/`
не тянет исторический драйвер `pytds` или `httpx`-клиент OpenRouter. Пакет `httpx` импортируют четыре модуля
(`places.py`, `proxy.py`, `reload.py`, `teacher_api.py`), но свой `httpx.AsyncClient` — то есть настоящий сетевой
клиент места и `sp-ctl` — создаёт только `places.py` (`Workplaces`/`SpCtl`); прочие три берут его типы
(`httpx.Response`, `httpx.HTTPError`, `httpx.PoolTimeout`) лишь для разбора ответа, который им отдаёт
`Workplaces`, своего клиента не держат.

Спецификацией поведения (помимо самого кода) служат тесты `core/tests/test_gw_*.py` — 19 файлов, по одному на
основные модули (`test_gw_accounts.py`, `test_gw_api.py`, `test_gw_places.py`, `test_gw_proxy.py`,
`test_gw_rules.py`, `test_gw_stream.py`, `test_gw_reload.py`, `test_gw_repair.py`, `test_gw_teacher.py`,
`test_gw_teacher_accounts.py` и т.д.) — там же зафиксированы граничные случаи (например, `test_gw_halt.py` —
поведение тормоза прогона), которые не всегда укладываются в одну строку докстринга. Как запускать тесты —
[19-tests-and-bench.md](19-tests-and-bench.md).

## 15. Что специфично для заказчика

| Компонент | Переносимый как есть | Специфика КДМ-1 |
|---|---|---|
| Учётки, вход, сессии, замок (`accounts.py`, `api.py`) | да | — |
| Места, пробуждение, уборщик (`places.py`) | да | число мест `PLACE_MAX=11` и сети `STU_NET` — не код, а `core/pcbk_core/places.py` + `compose.yaml` |
| Прокси и белый список OpenCode (`proxy.py`, `rules.py`) | да | — |
| Перезагрузка агентов, карантин (`reload.py`) | да | путь каталога агентов внутри места (`PLACE_AGENTS_DIR`) завязан на образ `student/config`, но не на предприятие |
| Тормоз прогона (раздел 7) | да | связан с ролью `llm` и прогоном отбора модели — специфика самого стенда обучения, не предприятия |
| «Мои агенты», «События» (`agents_api.py`) | да | `IMAGE_AGENTS` (названия встроенных агентов) — из образа места `student/config/agent/*.md`, при другом образе — другой список |
| «Калькуляторы» (`calc_api.py`) | модуль да | аргументы и смысл инструментов — служба данных, знающая про КДМ-1 (белый список тегов, смены) |
| «Уроки» (`lessons_api.py`, `lessons.py`) | да | содержимое пакета уроков — учебный материал КДМ-1 |
| Кабинет преподавателя (`teacher_api.py`, `teacher_log.py`, `transcript.py`) | да | доли бюджета — предмет роли `llm`, не привязаны к предприятию |
| Отчёты браузера (`web.py`) | да | — |

Подробная методика переноса (что заменить, в каком порядке) — [21-extending.md](21-extending.md).
