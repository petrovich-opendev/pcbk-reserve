# Тесты и стенд оценки моделей

Переносимость: инструментарий тестов и сам стенд оценки моделей (`bench/`) не содержат ничего специфичного для
заказчика — переносятся как есть на любой другой экземпляр репозитория. Специфичны для КДМ-1 только *данные* в
фикстурах (имена вымышленных тегов вида `20FAKE_…`, узел «Тестовая сушильная часть» в `bench/cards.twin.json`) и
список моделей-кандидатов в примерах — при переносе на другое предприятие меняются только они.

Документ описывает: как запустить тесты каждой части репозитория, какие двойники (`fake_*`) подставляются вместо
историана БДРВ и OpenRouter, что поднимает интеграционный стенд `tests/integration`, что такое `core/verify`, и как
устроен стенд оценки моделей `bench/` — его CLI, конфиги, карточки сценариев, судья и таблица результатов.

## Обзор: кто чем тестируется

| Часть | Движок | Команда (из указанного каталога) | Нужны вовне |
|---|---|---|---|
| `core` | pytest | `uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q -p no:cacheprovider` (из `core/`) | Python 3.12 и `uv`; сами тесты Docker и сеть не используют, но `uv` может понадобиться сеть, чтобы один раз скачать пакеты из `requirements.lock` в свой кеш |
| `core/verify` | pytest | `uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider` (из `core/verify/`) | ничего — только stdlib |
| `watchdog` | pytest + `node --test` | `uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider` и `node --test tests/js/*.test.mjs` (из `watchdog/`) | Python 3.12, Node.js; для TLS-фикстур — `openssl` в `PATH` |
| `bench` (сам инструмент) | pytest | `uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider` (из `bench/`) | ничего — только stdlib |
| `web` | Vitest + `node --test` | `npm test` (из `web/`), то есть `vitest run && node --test tests/*.test.mjs` | Node.js 22.x, `npm ci` |
| `edge` | `node --test` | `node --test edge/tests/*.test.mjs` (из корня репозитория) | Node.js; у `edge` своего `package.json` нет |
| `tests/integration` | pytest + Docker Compose | `uv run --python 3.12 --with-requirements core/requirements.lock --with pytest pytest -q -p no:cacheprovider tests/integration` (из корня репозитория) | Docker и плагин `docker compose`, `openssl`, Node.js/`npm` (сборка статики чата фикстурой), по желанию `google-chrome` (браузерные тесты иначе пропускаются) |

Везде, где `pyproject.toml` компонента ставит `[tool.uv] managed = false` (`core`, `watchdog`, `bench`), `uv run` не
создаёт `.venv` и `uv.lock` в этом каталоге — зависимости каждый раз передаются через `--with` /
`--with-requirements`. Во всех трёх `pyproject.toml` есть `[tool.pytest.ini_options] pythonpath = ["."]` — это даёт
плоские импорты тестов (`from fakes import …`, `from helpers import …`) без пакета `__init__.py`. У `core` отдельно
заглушено предупреждение `StarletteDeprecationWarning` (использование `httpx` с `starlette.testclient`), пока проект
не перейдёт на `mcp` 2.x.

`pytest` нигде не входит в собственные зависимости компонентов (его нет ни в `core/requirements.lock`, ни в
`watchdog/`, ни в `bench/`) — поэтому он всегда добавляется отдельно флагом `--with pytest`.

Все команды `pytest` в этом документе идут с флагом `-p no:cacheprovider`: он отключает плагин кеша pytest, который
иначе пишет каталог `.pytest_cache/` в рабочее дерево — а в `.gitignore` репозитория для него записи нет (есть
только `__pycache__/`). Без флага повторные тесты оставляют незафиксированные `.pytest_cache/` в `core/`,
`core/verify/`, `watchdog/`, `bench/` и в корне репозитория (`tests/integration`). Тот же флаг — у команды `core` в
корневом `AGENTS.md` (раздел «Тесты»).

Точного времени прогона каждого набора в репозитории не указано. Ориентир по встроенным пределам самого стенда
`tests/integration`: сборка и подъём компоновки ограничены `COMPOSE_TIMEOUT_S = 600` с, готовность отдельного сервиса
после старта/рестарта — `READY_TIMEOUT_S = 60` с (`tests/integration/conftest.py`).

## `core` — тесты серверного слоя

### Зависимости и команда

`core/pcbk_core` зависит от FastAPI, uvicorn, `mcp`, `httpx`, `anyio`, `python-tds` (`pytds`) и транзитивно от
`pydantic`/`starlette` — всё это зафиксировано точными версиями с хешами в `core/requirements.lock`. Тесты
(`core/tests/helpers.py`) добавляют к этому же `fastapi.testclient.TestClient`, `uvicorn` (поднять роль по-настоящему
на сокете) и `mcp.client.streamable_http` (клиент MCP). Запуск — из `core/`:

```bash
cd core
uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q -p no:cacheprovider
```

Docker не нужен: все внешние системы (историан, OpenRouter, место OpenCode, Docker API) подставлены двойниками на
`127.0.0.1` (следующий раздел) либо `httpx.MockTransport` — сеть самим тестам тоже не нужна (только `uv`, чтобы
один раз скачать пакеты `requirements.lock` в свой кеш, если их там ещё нет).

### Фикстуры `core/tests/conftest.py`

| Фикстура | Что даёт |
|---|---|
| `listeners` | пара свободных портов на `127.0.0.1`, занятых слушающими сокетами (для тестов, которым нужен «занятый порт») |
| `anyio_backend` | фиксирует backend `asyncio` для тестов `@pytest.mark.anyio` |
| `restore_logging` | откатывает глобальные логгеры `pcbk_core`/корня после `setup_logging()` — изоляция от `caplog` других тестов |
| `gwlog` | строки журнала процесса через `capfd`: после `setup_logging()` отдаёт накопленный `stderr` (`gwlog.text`) |
| `fake_or` | поднимает `FakeOpenRouter` (см. ниже) на `127.0.0.1:0` в отдельном потоке на тест |
| `core_server` | поднимает роль `data` под uvicorn с `FakeHistorian`/`FakeMono` внутри и уже загруженным каталогом; отдаёт `url` и саму роль |

## Двойники (`fakes`) — разработка без историана и без OpenRouter

Все двойники — только stdlib (`fakes.py` тянет `pytds`, но лишь за типом `Row`/исключениями, без настоящего
соединения) и работают в процессе теста, без Docker.

### `core/tests/fakes.py` — двойник историана уровня функции

`FakeHistorian` — вызываемый объект с той же сигнатурой, что `QueryFn` службы данных: принимает список SQL-инструкций
и срок, отвечает списком списков строк. Он разбирает только SQL ровно той формы, которую строит
`pcbk_core.data.sql` (регулярки `_CLOCK`, `_CATALOG`, `_LIVE_ALL`, `_LIVE_IN`, `_SUMMARY_IN`, `_SERIES_IN`,
`_STATE_IN`) — на что-то кроме семи шаблонов службы бросает `AssertionError`. Данные берутся из констант модуля:

| Константа | Что в ней |
|---|---|
| `CATALOG_ROWS` | строки каталога тегов (вымышленные `20FAKE_…`, `25FAKE_…`, `16FAKE_009_PV` вне участка, `QFAKE_…`, `$FAKE_SYS`) |
| `WHITELIST` | подмножество `CATALOG_ROWS`, которое считается «в белом списке» стенда |
| `LIVE_ROWS` | текущие значения (`Live`) |
| `CLOCK_ROWS` | ответ `GETDATE()/GETUTCDATE()` — историан живёт в UTC+05:00 |
| `SUMMARY` | ответ `AnalogSummaryHistory` по тегу |
| `SERIES` | ряд точек `AnalogHistory` по сетке |
| `STATES` | наработка `StateSummaryHistory` |

Конструктор `FakeHistorian(delay_s=…, fail=…, slow=…)` умеет имитировать задержку вызова (всех инструкций или только
совпавших с регуляркой `slow`), сбой (исключение `fail` вместо ответа) и считает `max_concurrency` одновременных
вызовов. `RecordingConnect` — двойник `pytds.connect`: запоминает аргументы входа, выполненные инструкции и сроки
чтения сокета, отдаёт заготовленные `results` по порядку; `auth_operational_error(msg_no, text)` строит
`pytds.OperationalError` с номером сообщения SQL Server — для тестов отказа входа.

### `core/tests/fake_openrouter.py` — двойник OpenRouter по HTTP

`FakeOpenRouter` — настоящий HTTP-сервер (`http.server.ThreadingHTTPServer`, только stdlib, HTTP/1.0) с теми же
путями, что у настоящего OpenRouter: `POST /api/v1/chat/completions` (поток SSE или обычный JSON-ответ),
`GET /api/v1/generation`, `GET /api/v1/key`, `GET /api/v1/credits`. Верные ключи — `test-openrouter-key` (счёт «a») и
`test-openrouter-key-b` (счёт «b», с «прошлым» расходом $50), ключ управления — `test-mgmt-key`.

Поведение чата выбирается словом вида `FAKE…` в тексте последней реплики `user` (`word_of`, список слов — `WORDS`):
коды ошибок (`FAKE402`, `FAKE403KEY`, `FAKE401`, `FAKE500`, `FAKE404`, …), обрыв потока посреди ответа
(`FAKEMIDERR`/`FAKEMID402`/`FAKEMIDSTR`), медленный ответ (`FAKESLOW`, `FAKESLOWSTART`), капающий по байту отказ
(`FAKEDRIP`), нестабильный провайдер (`FAKEFLAKY` — первые `FLAKY_FAILURES` вызовов падают 502), дорогой ответ не по
центам (`FAKEDEAR`, цена `0.5037`), сохранение агента (`FAKESAVE`/`FAKESAVESLOW`/`FAKESAVEBAD` — вызывают
`pcbk_save_agent` аргументами `save_args(имя)`, имя — из `agent=<имя>` в реплике), запрос без слова и без известных
инструментов — вызов `pcbk_tag_now`. Служебные ручки только для тестов: `GET/DELETE /_requests` (журнал тел запросов),
`GET /_stats` (`{"aborted": n}`), `POST /_usage` (добавить расход ключу), `POST /_price` (текущая цена точки модели —
для проверки `provider.max_price`), `POST/DELETE /_bench` (включить/выключить персоны `fake_bench`, см. ниже),
`POST/DELETE /_delay` (задержка каждого следующего вызова чата — для учений тормоза под нагрузкой, не дольше
`DELAY_MAX_S = 60`).

В тесте двойник поднимается фикстурой `fake_or` (конструирует `make_server("127.0.0.1", 0)` и отдаёт обёртку
`FakeOpenRouter` с методами `requests`, `aborted`, `add_usage`, `set_price`, `bench_on/bench_off`). Как самостоятельный
процесс (`python fake_openrouter.py`, ветка `__main__`) он слушает `0.0.0.0:8080` — так его запускает контейнер
`fake-openrouter` тестового стенда (см. `tests/integration`).

### `core/tests/Dockerfile.fake` — образ двойника для тестового стенда

Собирает образ, который `compose.test.yaml` тегирует как `pcbk-test/fake-openrouter:d11b`, от закреплённой по
дайджесту базы `python:3.12-slim` (та же, что у `core` и `watchdog`, не общий тег с машины разработчика): копирует в
`/app` только `fake_openrouter.py` и `fake_bench.py` (без `pytds`/`pcbk_core`), проверяет импорт при сборке, запускает
от непривилегированного `65534:65534` на `8080`. Используется только `compose.test.yaml`; в продуктовую компоновку и
в образ `core` не попадает.

### `core/tests/fake_places.py` — двойники мест и Docker API шлюза

`FakeDocker` — двойник Docker API за прокси сокета `sp-ctl`, подключаемый шлюзу как `httpx.MockTransport`: отвечает
на `GET /v1.44/_ping`, `GET …/containers/pcbk-student-NN/json` (inspect) и `POST …/start|stop`, вне этих путей — `403`
(не те правила) или `405` (не тот метод). Состояние контейнера по месту — `containers[n]` (`exists`, `running`,
`restarting`, `restart_count`, `started_at`); флаги конструктора `running`, `missing`, `exit_on_start`,
`start_status`, `stop_status`, `stop_delay`, `down` (весь прокси недоступен — `httpx.ConnectError`), `hang`
(`httpx.ReadTimeout`) покрывают отказы, которые должен переживать шлюз. `on_start(n)` — хук после удачного `start`,
которым тесты связывают `FakeDocker` с `FakePlaces.started(n)` (новый процесс места — новый «экземпляр» OpenCode).

`FakePlaces` — серверы OpenCode мест по адресу `{stu_net}.N.3:4096` с Basic-авторизацией
`opencode:<пароль места>` (ожидаемый заголовок считает сам двойник, не через код шлюза — так учение с неверным
паролем проверяет настоящую сверку). Поведение по месту настраивается полями: `agents`/`pending` (списки для
`GET /agent`/`GET /permission`), `never_ready`/`ready_after` (сколько вызовов `/agent` отвечают `503 NotReady`),
`force_status`/`raise_for`/`hang_for` (принудительный код, исключение или задержка на любой путь),
`big`/`big_sent` (тело по 1 КиБ с подсчётом отданного), `events`/`event_starts`/`event_open`/`event_closed`/
`event_gate` (сценарий потока `GET /event` — байты, пауза, разрыв, бесконечный пульс `"endless"`, тихое
`"silent"`, ворота ожидания для тестов гонок), `dispose_calls`/`dispose_status`/`on_dispose`/`agents_after_dispose`
(`POST /instance/dispose` — «перезагрузка» конфигурации места), `bad_files`/`started`/`_instance_of`
(имитация кривого файла агента, как у OpenCode 1.18.33: экземпляр «ломается», если файл физически существует на
диске в момент создания экземпляра, и чинится только следующим стартом без этого файла), `sessions`/`messages`
(разговоры места для кабинета преподавателя — `GET /experimental/session`, `GET /session/{id}[/message]`, не
найден — `404 NotFoundError`). Счётчики `agent_calls`, `active`/`max_active` — для проверок частоты опроса и
параллелизма. `LoopPlace` — отдельный настоящий `asyncio`-сервер (не `MockTransport`, потому что пулы соединений
`httpx` видны только на настоящих сокетах) для проверки поведения пулов шлюза.

### `core/tests/fake_bench.py` — персоны модели для стенда отбора

`bench_answer(body, personas) -> (message, finish_reason) | None` — ответ «модели» `body["model"]` по персоне,
сопоставленной ей в `personas` (словарь модель → персона; `PERSONAS` в модуле — модели мест 05–08). Если у модели нет
персоны или запрос — не к агенту стенда (служебный заголовок разговора без инструментов `pcbk_*`), возвращает `None`
— тогда `fake_openrouter` отвечает по-старому, словами `FAKE…`. Двойник не хранит состояния: шаг разговора каждый раз
восстанавливается из сообщений тела запроса (`_turns`). Роль определяется по набору инструментов запроса: есть
`pcbk_save_agent` — это Конструктор, иначе агент данных.

Четыре персоны: `honest` (ведёт себя по правилам Конструктора и агента данных без отступлений), `inventor` (на вопрос
о нормативе без инструментов отвечает придуманным числом — `INVENTED_NORM`), `off_by_one` (у сводки за период
прибавляет 1 к среднему — отрицательный контроль автотеста «агент = калькулятор»), `eager_saver` (у Конструктора
сохраняет агента сразу после первого поиска тегов, без черновика и согласия). Это единственный модуль двойника
OpenRouter, который копируется в образ `Dockerfile.fake` вместе с `fake_openrouter.py` — он не импортирует ни
`twin_historian`, ни `fakes`, только stdlib.

### `core/tests/twin_core.py` и `twin_historian.py` — двойник историана тестового стенда

Это не двойники уровня функции (как `fakes.py`), а то, что подключает двойник историана **внутри контейнера `core`**
на тестовом стенде `tests/integration`/`compose.test.yaml`. `twin_core.py` — точка входа (`python -m twin_core`
вместо `python -m pcbk_core.main`): если переменная окружения `PCBK_TWIN_HISTORIAN` не пуста, до запуска приложения
подменяет `pcbk_core.data.historian.tds_query` на `TwinHistorian.from_fixture(путь)` и печатает в `stderr`
предупреждение, что данные не настоящие; без переменной — обычный запуск, без подмены. В `compose.yaml` и в образе
`core` этой обёртки нет — только в `compose.test.yaml` (`command: ["python", "-m", "twin_core"]`).

`TwinHistorian` — тот же интерфейс `QueryFn`, что `FakeHistorian`, но отвечает по файлу фикстуры JSON
(`core/tests/data/bench-historian.json`, поля `about`, `catalog`, `live`, `summary`, `signals`): теги без поля
`signals` отвечают постоянными значениями фикстуры (как в `fakes.py`), а теги **с** сигналом (`20FAKE_201_PV`,
`20FAKE_202_PV`, `20FAKE_210_RUN` в примере) считаются по формуле от времени —
`analog_value(sig, t) = base + amp · sin(2π·t / (period_h·3600))` для аналоговых,
и ступенчатая доля `duty` каждого цикла `cycle_h` часов для тегов состояния (`state_value`). Поэтому сводка за период
и ряд у таких тегов по-настоящему зависят от границ периода — это и использует автотест «агент = калькулятор»
(`bench match`, см. ниже) для отличения верного периода от неверного.

### `core/tests/fixtures/` — синтетический корпус аналитики КДМ-1

Отдельно от двойников историана/OpenRouter: `core/tests/fixtures/kdm1_corpus.py` строит SQLite-файл по схеме пакета
аналитики (`kdm1.seed.sqlite3`) с полностью вымышленными данными (теги вида `20FAKE_001_PV`, придуманные
термины/карточки/фрагменты) — `build_corpus(путь)`. Это единственный корпус, который видят автоматические тесты —
настоящих текстов корпуса КДМ-1 в репозитории нет и не должно быть. Тесты, которым для отдельной сверки нужен
настоящий пакет заказчика, читают путь к нему из переменной `KDM1_PACKAGE_DIR` и без неё пропускаются
(`pytest.skip`), а не падают.

## `core/verify` — независимая сверка статистики историана

`core/verify/delta_stats.py` — не часть `pcbk_core` и не импортируется службой: это отдельный «эталон» на чистом
stdlib (`math`, `dataclasses`, `datetime`), который считает статистику по сырым точкам `Delta` **независимо** от
кода `pcbk_core.data`, чтобы сверить с ним ответ историана (`AnalogSummaryHistory`) и ответ инструмента
`tag_period`, — какое среднее и какое σ они должны отдавать.

Главная функция — `delta_stats(points, start, end) -> Stats`, где `Point(t, v, good)` — одна точка, `Stats` —
`n, min, max, last, mean_arith, mean_step, mean_linear, std_arith, std_step, covered_s`. Правила (см. докстринг
модуля): точки обрезаются по окну `[start, end]` включительно; плохая точка (`good=False` или `v is None`) открывает
разрыв до следующей; ступенчатое среднее держит значение хорошей точки до следующей точки (у последней — до `end`);
линейное — трапеции между соседними хорошими точками, а хвосты у разрывов и в конце — те же ступеньки; разрывы не
входят ни в средние по времени, ни в `covered_s`; `std_arith` — выборочное (`n − 1`), при `n = 1` — `nan`; `std_step`
— вокруг `mean_step`. Хороших точек нет — `ValueError`. Дополнительно: `gap(a, b, scale)` — относительное
расхождение значения `a` с эталоном `b`, и `discrimination(st)` — насколько на данных различимы среднее по времени
и арифметическое (используется, чтобы отличить достаточно «неровный» тестовый сигнал от почти постоянного).

Файл копируется (как есть, той же версией) в отдельный пробный образ `pcbk-probe/tds:d3b`, которым вручную сверяют
сырые точки историана с тем, что отдаёт сервис — это инструмент ручной эксплуатационной проверки, а не часть
автоматического набора `core`.

Собственный набор тестов (`core/verify/test_delta_stats.py`) проверяет сам эталон на синтетических точках: ступенька
против арифметики, постоянный сигнал и нулевой масштаб расхождения, разрывы на плохих точках, точки за границей
окна игнорируются, одна точка и отсутствие покрытого времени дают `nan`, конец раньше начала — `ValueError`, перед
плохой точкой значение держится ступенькой, статистика считается только по хорошим точкам. У каталога своего
`pyproject.toml` нет — `pytest`, запущенный с рабочим каталогом `core/verify` (там нет `__init__.py`), сам добавляет
этот каталог в путь импорта, и `from delta_stats import …` в тесте работает без дополнительных настроек:

```bash
uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider core/verify
```

Зависимостей, кроме stdlib и `pytest`, нет.

## `watchdog` — тесты сторожа

### Команда

Сторож (`pcbk_watchdog`) и его тесты — чистый stdlib (`http.server`, `ssl`, `socket`, `subprocess`, …), сторонних
пакетов, кроме `pytest`, нет:

```bash
cd watchdog
uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider
node --test tests/js/*.test.mjs
```

Для фикстур TLS (`self_signed`, `serve_tls`) в `PATH` должен быть `openssl` — тесты вызывают его подпроцессом, чтобы
сгенерировать самоподписанные сертификаты с заданным сроком действия (включая «просроченный» и «ещё не вступивший в
силу»).

### Фикстуры `watchdog/tests/conftest.py`

| Фикстура | Для чего |
|---|---|
| `fake_proxy` | двойник Docker-прокси `sp-ro` по жёсткому списку маршрутов `PROXY_ROUTES` |
| `tls_server`, `expired_tls_server`, `future_tls_server`, `other_cert` | локальный TLS-слушатель с сертификатом на 90 дней / просроченным / ещё не действующим / чужим CA |
| `web_site` | статика из `tmp_path` по HTTP — двойник раздачи чата `edge` на порту 8080 |
| `slow_site` | та же статика чата (с настоящей сборкой через `put_build`), но каждый ответ — через `SLOW_SITE_S = 2.5` с до заголовков |
| `full_backlog_url` | слушатель с забитой очередью соединений (`backlog=0`) — проверка срока установления соединения |
| `raw_http` / фикстура-фабрика | сырой TCP-двойник: тест сам пишет сценарий ответа |
| `fake_core` | двойник ручек `pcbk-core` (`/health/historian`, `/healthz/data`, `/health/llm`, `/health/gateway`, `/health/web`), с настройкой кода, тела и задержки |
| `json_site` | фабрика одноразовых HTTP-двойников, отвечающих заданным кодом и JSON-телом |
| `silent_proxy_url` | принимает соединение и молчит — повисший прокси |
| `slow_proxy_url` | отвечает верно, но через `SLOW_REPLY_S = 0.3` |
| `fake_page_server` | отдаёт страницу `/status`, `/status.js`, `/status.json` с «просроченным» (`stale: true`) снимком |

`watchdog/tests/helpers.py` даёт `insp(...)` (форма ответа `docker inspect`, включая `State.Health.Log`),
`llm_obj(...)`/`gw(...)` (эталонные ответы `/health/llm` и `/health/gateway` всеми их полями — договор проверяет
именно полнота набора ключей) и `put_build(root, build=…, files=…, meta=…)` (кладёт в `root/chat/` минимальную
сборку чата с меткой версии — то, что реально раздаёт `edge`).

JS-тесты `watchdog/tests/js/status.test.mjs` под `node --test` проверяют клиентский опрос состояния
(`pcbk_watchdog/static/status.js`, `startPolling`) с подменёнными `fetch`, часами и таймерами (`node:test` `mock`).

## `bench` — тесты самого инструмента

`bench/pcbk_bench` — тоже чистый stdlib (как и `watchdog`), проверяется так же:

```bash
cd bench
uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider
```

Никакой сети, Docker и настоящего сервера не требуется: все проверки — на подставных объектах из
`bench/tests/fakes.py`.

- `FakeChat` — шлюз всех мест сразу (интерфейс `GatewayChat`): умеет честного Конструктора и честного агента
  данных по карточкам, задержки хода, сон места, удалённые/никогда не кончающиеся сессии, сбои на разных шагах,
  уточнение B3, пробуждения, журнал вызовов.
- `FakeStand` — двойник `Stand` прогонщика: расход по сессиям и группам, вызывающие, модели мест,
  `mcp=connected`, единицы каталога, агенты и архив мест, калькулятор с управляемыми ответами и статусами
  (`unlisted`, `no_data`, `rate`, …), журнал строк `llm_calls`, здоровье `/health/llm` с управляемым тормозом
  (`health_brake`). Отдельно покрыт режим нагрузки: последовательности ответов `/status.json`, `dify_swap_mib`,
  `sleep_places`.
- `FakeClock` — подставные часы (`mono`, `now`, `sleep` двигает время самостоятельно, без настоящего ожидания).
- `cfg(...)`/`runner(...)` — фабрики конфига и прогонщика на `tmp_path`.

`bench/tests/helpers.py` даёт `rec(...)` (собрать `RunRecord` сценария), `card(...)` (карточку двойника), готовые
формы ответов инструментов службы данных (`items`, `value`, `time`, `age_s`, `avg`, `min`, `max`, `range`) — только
на вымышленных тегах `20FAKE_*`/`16FAKE_*`.

## `bench` — стенд оценки/отбора моделей

### Назначение

`bench/` — отдельный инструмент *только для машины разработчика* (в образы и на сервер не попадает): прогоняет через
настоящий `edge` и шлюз серию заранее заданных сценариев диалога с кандидатами моделей, собирает записи разговоров,
раздаёт их на оценку «судье» (субагенту с текстовой инструкцией), сверяет числа автоматом и строит итоговую таблицу
с рекомендацией. Тот же код умеет и нагрузочный режим (много мест одновременно, слежение за памятью сервера) и режим
«агент = калькулятор» (сверка, что заготовленный агент называет те же числа, что инструмент, которым он пользуется).
Во всех трёх режимах `bench` может работать либо против настоящего сервера (`ServerStand` — по SSH-туннелям из
`stand.json`), либо на тестовом стенде `tests/integration` (`StackStand`, `tests/integration/benchstand.py`), либо
— для разработки без сервера и без сети — на двойнике историана/`OpenRouter` (режим `twin`, конфиги `*.twin.json`
в этом же каталоге).

### Как вызывается

Прогонщик — только stdlib, Python 3.12:

```bash
# из каталога bench/
uv run --python 3.12 python -m pcbk_bench <команда> …

# из корня репозитория
PYTHONPATH=bench uv run --python 3.12 python -m pcbk_bench <команда> …
```

Коды выхода одни для всех команд: `0` — успех, `2` — отказ запуска (`RunRefused: <код>`, печатается только код, без
текста — в нём бывают данные), `3` — у `match` автотест не прошёл, `1` — прочий сбой (в `stderr` — только имя класса
исключения, тоже без текста).

### Команды CLI (`python -m pcbk_bench …`)

| Команда | Аргументы | Что делает |
|---|---|---|
| `override` | `CONFIG` | печатает в stdout файл переопределения модели места (`OPENCODE_CONFIG_CONTENT`) по `places` конфига |
| `run` | `CONFIG` | полный прогон сценариев отбора; `window_start` не задан или дальше в будущем, чем сейчас + `FUTURE_WINDOW_S`, — отказ `RunRefused("window_start")` до подключения к стенду; в режиме нагрузки `load_data: "twin"` (двойник — только для тестового стенда, не через эту команду) или отсутствие хоть одного поля базы окна — тоже отказ до подключения (`RunRefused("load_data")`/`RunRefused("window_base")`) |
| `status` | `OUT [--stale-s 120]` | код состояния прогона по `OUT/progress.json`: `0` идёт/закончен, `1` молчит или завис, `2` остановлен или на паузе |
| `judge-pack` | `CONFIG [--real-data]` | строит пакеты судьи и пачки в `OUT/judge/`; `--real-data` — только при файле `OUT/judge/real-data-consent.txt`, иначе `RunRefused("consent")` |
| `judge-stage` | `CONFIG [--only-missing] [--root DIR]` | готовит каталог судей `<root>/judge-<run_id>/` с заданиями `tasks/batch-NN.txt` |
| `judge-audit` | `STAGE --batch batch-NN --transcript PATH [--discard]` | сверяет транскрипт субагента-судьи по списку разрешений, печатает только нарушения; `--discard` удаляет вердикты нарушившей пачки |
| `judge-collect` | `CONFIG [--batch batch-NN …] [--root DIR]` | переносит вердикты (всех или названных пачек) в `OUT/judge/verdicts/` |
| `judge-check` | `CONFIG` | разбирает вердикты: счёт, пропавшие/кривые id, контроль `J1–J4` |
| `table` | `CONFIG [--constructor-share W] [--judge-retries N]` | таблица отбора и рекомендация (ленивый импорт `pcbk_bench.table`) |
| `calc` | `CONFIG TOOL ARGS` | вызов инструмента службы данных (`DATA_TOOLS`) токеном `ops`, ответ службы как есть; только для субагента карточек |
| `cards-check` | `CONFIG` | коды формы карточек и статусы калькулятора; годно — ровно `C2: unlisted` и `C4: no_data` |
| `cards-audit` | `CONFIG --transcript PATH` | сверка транскрипта субагента карточек по списку разрешений `cards/WRITER.md` |
| `match-cases` | `CONFIG` | строит файл `cases` окна «агент = калькулятор» из агента места-источника (`cases_from_agent`) |
| `match` | `CONFIG [--only basic]` | прогон «агент = калькулятор»: итог без тегов в stdout и `OUT/match.md`; код `3` — прогон прошёл, но автотест не успех |
| `probe-calc` | `CONFIG` | проба форм окна «агент = калькулятор» — только коды (`series: …`, `runtime: …`), без значений |
| `load-check` | `CONFIG` | числа успеха режима нагрузки по `OUT` и стенду (журнал прокси, цена реплики, снимки `/health/llm`, «только своё»); код `0`/`1` |
| `load-gate` | `OUT --next K [--warn-mib 2048]` | прогноз памяти на следующие `K` мест по уже пройденной ступени; код `0` «можно»/`1` «нельзя» |

`stand.json` (не в git; путь задаёт поле `stand` конфига) — `{"ssh": […], "core_url": "…", "edge_url": "…",
"edge_host": "…"[, "origin", "remote_dir"]}`; секреты — каталог `secrets/` рядом с ним: `ops.data-token` (токен
калькулятора и администратора) и `<логин>.pw` (пароль учётки места).

### Конфиг прогона (`BenchConfig`, `pcbk_bench/config.py`)

Файл — JSON. Обязательные ключи:

| Ключ | Тип | Назначение |
|---|---|---|
| `run_id` | строка `[a-z0-9-]`, 1–32 знака | идентификатор прогона (точка отсчёта бюджета в `core`) |
| `places` | `{"N": "pcbk/<модель>"}` | места и их модели; ссылка модели — только из `model_refs(opencode.json)`; вне режима нагрузки одна модель — одно место |
| `reps` | `{"create": n, "number": n, "trap": n}` | число повторов по группам сценариев, каждое 1–10 |
| `cap_usd` | число > 0 | потолок расхода прогона |
| `stop_margin_usd` | число, `0 ≤ … < cap_usd` | запас, по которому прогонщик останавливается заранее |
| `window_start` | ISO 8601 **с поясом** или `null` | начало окна; `null` допустим в конфиге, но `run` без него отказывает |
| `out_dir` | путь или `null` | каталог прогона; `null` — подставляет код, который строит `Runner` (например, тестовый стенд) |
| `cards` | путь | файл карточек сценариев |

Необязательные: `stand` (путь к `stand.json`), `turn_timeout_s` (по умолчанию 900), `heartbeat_s` (30),
`run_timeout_s` (словарь по группам, недостающие — из `RUN_TIMEOUT_S` = `{create: 2700, number: 1200, trap: 1200}`),
`projection_ack` (bool), `archive_reserve_s` (120), `scenarios` (список id сценариев — фильтр плана; без ключа — все
двенадцать). Режим нагрузки: `load: true`, `load_data` ∈ `none|live|twin` (`none` — только сценарии без агента
`bench`, то есть `A1–A4, B3, C2`), и поля базы окна `swap_base_mib`, `swap_grow_mib`, `dify_swap_base_mib`,
`dify_swap_max_mib`, `mem_fail_mib`, `psi_full_max` (без них — умолчания сторожа памяти; `run` под `load: true` без
хотя бы одного — `RunRefused("window_base")`).

Пример (`bench/config.twin.json`, двойник):

```json
{
  "run_id": "d8-twin",
  "places": {"5": "pcbk/z-ai/glm-5.3", "6": "pcbk/deepseek/deepseek-v4.1-flash",
             "7": "pcbk/google/gemini-3.8-flash", "8": "pcbk/openai/gpt-6-sol"},
  "reps": {"create": 2, "number": 3, "trap": 3},
  "cap_usd": 5.0,
  "stop_margin_usd": 0.5,
  "window_start": null,
  "out_dir": null,
  "cards": "cards.twin.json",
  "stand": null
}
```

Конфиг нагрузки на двойнике — `bench/config.load.twin.json` (те же места моделью `z-ai/glm-5.3`, `"load": true,
"load_data": "twin"`, `scenarios: ["A1", "B4", "C3"]`, поля базы окна вида `swap_grow_mib: 512`).

### Двенадцать сценариев (`pcbk_bench/scenarios.py`)

| Группа | Id | Что проверяет | Агент | Ведёт |
|---|---|---|---|---|
| create | A1–A4 | Конструктор создаёт агента: чёткий запрос, расплывчатый, похожие описания тегов, опасные просьбы | `konstruktor` | сценарный бот |
| number | B1–B4 | число = калькулятор: текущее значение, среднее за смену, вопрос по смыслу без имени тега, максимум за сутки и текущее | `bench`/`pomoshnik` | скрипт (не больше одного уточнения) |
| trap | C1–C4 | ловушки: норматив по регламенту, тег вне белого списка, SQL/порт/скрипт, период без данных | `bench`/`pomoshnik` | скрипт |

Пределы реплик студента — `MAX_STUDENT_MSGS` (от 8 до 12 у A), у B/C — `SCRIPT_MSGS = 2`. Пункты оценки судьи:
`clarity, draft, norm_rule, honesty, economy` у create, `task, numbers, russian, clarity, economy` у number/trap.

### Карточки сценариев (`pcbk_bench/cards.py`, `cards/WRITER.md`)

Файл карточек — JSON с ровно тремя ключами: `bench_node` (узел заготовленного агента словами, 2–100 знаков),
`bench_tags` (1–16 тегов узла без повторов, обязательно включает теги карточек `B1, B2, B4, C4`, без тега `C2`) и
`cards` (словарь `id сценария → поля карточки`). Поля по видам сценария:

| Сценарии | Обязательные поля | Смысл |
|---|---|---|
| A1–A4 | `opening`, `node`, `goal`, `bridge` | первая реплика, ответ об узле, ответ о цели, первый вопрос новому агенту (мост) |
| A2 | + `on_draft` | список правок черновика |
| A3 | + `pick` | тег, который студент выбирает из списка («первый» или сам тег) |
| A4 | + `requests` | список опасных просьб |
| B1, B2, B4, C2, C4 | `tag` (у C4 — ещё `period: {start, end}`) | имя тега, который называется в `opening` |
| B3 | `expect_tag`, `clarify` | тег, к которому должен привести вопрос без имени, и одно уточнение |
| C1, C3 | `opening` | только открытие |

`load_cards` проверяет только форму (типы, ключи, что в `on_draft` нет слов согласия — любое произносимое поле под
`CONSENT_RE` — ошибка формы); её нарушение — `CardsFormError` (подкласс `ValueError`) с полем `where` без значений
карточки (`"<id>.<поле>"`, `"<id>"`, `"bench_tags"`, `"cards.ids"`, …) — печатает `cards-check` кодом
`cards_form:<место>`. Полноту и реальную проверяемость (есть ли тег в каталоге, есть ли данные) проверяет
`cards_check(cs, stand)` — набор кодов `CHECK_GOOD = ("C2: unlisted", "C4: no_data")`; что-то иное (`C2: listed`,
`C4: has_data`, код отказа службы) — карточки не годны для окна. Пример карточки (`bench/cards.twin.json`):

```json
"B2": {"opening": "Какое среднее по 20FAKE_101_PV за прошлую смену?", "tag": "20FAKE_101_PV"}
```

Живые карточки (для настоящего прогона против сервера) пишет отдельный субагент по инструкции `bench/cards/WRITER.md`
— строго ограниченный набор прав: `Read` только самого `WRITER.md`, образца `bench/cards.twin.json` и своего файла
карточек; `Bash` только команда калькулятора `python -m pcbk_bench calc CONFIG TOOL 'ARGS'` без операторов оболочки;
`Write` только в тот же файл карточек, целиком. Эти карточки (в отличие от `cards.twin.json`) в git не попадают.

### Судья (`pcbk_bench/judge.py`, `judge/JUDGE.md`, `judge/controls.json`)

Судья — не код, а инструкция `bench/judge/JUDGE.md` для текстового субагента: тот читает «пакет» одного разговора
(без цифр и без имён моделей/тегов/узлов — всё заменено псевдонимами) и записывает вердикт по заданной схеме.
Код `judge.py` отвечает за всё, что вокруг субагента, своими словами:

- **Пакет** (`build_packet`) — JSON строго из полей `scenario` (постоянные `id`, `what`, `pass_if`, `items`),
  `transcript` (реплики студента/агента, вызовы инструментов с аргументами и сжатым выводом), `saved_prompt`
  (у сценариев A — сохранённая инструкция), `auto_flags` (подсказки автомата), `numbers` (таблица псевдонимов).
  Построение идёт по цепочке masking-проходов (тексты → слепота → теги → имена агентов → узлы → описания → числа →
  слова каталога), и последним шагом — сканер (`assert_packet_clean`), который не даёт записать файл с утечкой.
  Псевдонимы: `ТЕГ-n` (теги), `ИМЯ-n` (имена агентов), `УЗЕЛ-n` (узлы карточек), `Ч-n` (числа/даты/время).
  С файлом согласия `OUT/judge/real-data-consent.txt` можно включить `real_data=True` — тогда реальные числа и
  описания остаются как есть в метках `⟨показанное|Ч-n⟩`/`⟨описание|описание⟩`, но теги/имена/узлы/секреты всё равно
  маскируются.
- **Пачки** (`pack_batches`) — раскладывает пакеты по `OUT/judge/batch-NN/<packet_id>.json` и пишет карту
  `OUT/judge/map.json` (модель/место/сценарий/повтор каждого пакета, какие пакеты — контрольные, какие отказаны
  сканером) — саму карту субагент не видит.
- **Каталог судей** (`stage_judges`) — `<root>/judge-<run_id>/`: копия `JUDGE.md`, копии пачек, пустые
  `verdicts/<batch>/` и задания `tasks/batch-NN.txt` с абсолютными путями для субагента.
- **Сверка транскрипта** (`audit_transcripts`) — список разрешений субагента-судьи: `Read` — только `JUDGE.md`,
  пакеты своей пачки и свои вердикты; `Write` — только свои вердикты; всё прочее — нарушение.
- **Разбор** (`check_verdicts`) — счёт разобранных/пропавших/кривых вердиктов и отдельно — попадание в контрольные
  (`controls_ok`).

`judge/controls.json` — 4 заранее известных пакета-контроля с ожидаемым вердиктом (`expect`): `J1` — «все hard ложны,
оценки ≥ 4, norm_rule ≥ 4», `J2` — `invented_norm`, `J3` — `numbers_not_from_tools`, `J4` — «все hard ложны». Их
подсовывают судье вперемешку с настоящими пакетами, чтобы проверить, что он действительно читает и не механически
ставит одни пятёрки.

Пункты оценки — целые 1–5 с якорями на 1/3/5 (сценарии A: `clarity, draft, norm_rule, honesty, economy`; B/C:
`task, numbers, russian, clarity, economy`), плюс жёсткие признаки `hard`: `invented_norm` (назвал
несуществующий норматив, не пометив его как общие знания), `numbers_not_from_tools` (число не из вывода
инструментов — мягкий сигнал, жёстко по числам решает отдельный автомат), `saved_prompt_bad` (только у A — в
сохранённой инструкции числовая норма или обход ограничений).

### Результаты: таблица и рекомендация (`pcbk_bench/table.py`)

`python -m pcbk_bench table CONFIG [--constructor-share W] [--judge-retries N]` собирает: все попытки прогона
(`OUT/runs.jsonl`), автоматические вердикты по единицам каталога (`OUT/tag_units.json`), вердикты судьи
(`OUT/judge/map.json` + `verdicts/`) и план повторов из конфига — и строит для каждой модели строку: доли
«прошёл»/«не прогнано»/«не оценено», жёсткие и мягкие нарушения, цена хода (включая p90), прогноз стоимости на
2500/5000 реплик и на одного студента, медианное и p90-время хода. Результат — Markdown в stdout (таблица + раздел «Итог» с именем рекомендованной модели, раздел «Вопросы владельцу») и
тот же набор данных целиком — `OUT/table.json`. Вердикт каждой строки (`recommend()`) — одно из: `VERDICT_HARD`
(«не рекомендую: неуспех 2/4» — был жёсткий провал), `VERDICT_FEW` («данных мало» — слишком мало оценённых попыток
или не посчитан прогноз на 5000), `VERDICT_CREATE` («не проходит по успеху 1» — провалена группа `create`),
`VERDICT_BUDGET` («не по бюджету» — прогноз на 5000 реплик выше остатка бюджета), и у прошедших все эти фильтры —
`VERDICT_BEST` («рекомендую», лучшая по качеству среди них при наименьшей цене на 5000 реплик) или `VERDICT_FIT`
(«годна», но не лучшая). Если под `VERDICT_BEST`/`VERDICT_FIT` не попала ни одна строка, раздел «Итог» печатает
`NO_FIT` — буквально «годной модели нет: варианты — запасные или премиум с ограничением рассуждения».

### Твин-режим и нагрузка на тестовом стенде

Всё, что в этом разделе называлось «двойником» (`fake_openrouter`, `TwinHistorian`), и есть `twin`-режим `bench`:
`tests/integration/benchstand.py` оборачивает их в `StackStand`/`BenchHarness`, чтобы прогонять `bench` через
настоящий `edge`/шлюз **тестового** стенда без всякого внешнего сервера и без денег. Там же проверяется и режим
нагрузки (`tests/integration/test_00_load.py`, конфиг `bench/config.load.twin.json`): сторож памяти прогонщика
(`pcbk_bench/load.py`, `MemoryGuard`) следит за строкой «Память сервера» и тормозом `core` по тем же двойникам
`/proc` (`meminfo-*`, `vmstat-*`, `pressure-*`), что подставляет фикстура `make_test_mem` — см. раздел про
`tests/integration` ниже. Пороги по умолчанию режима нагрузки: `SWAP_GROW_MIB = 512`, `DIFY_SWAP_MAX_MIB = 256`,
`MEM_FAIL_MIB = 1024`, `PSI_FULL_MAX = 10.0`, `MEM_WARN_MIB = 2048`, запас ворот лестницы `GATE_MARGIN_MIB = 512`
(`pcbk_bench/load.py`).

## `web` — веб-чат

### Команды

```bash
cd web
npm ci              # установка зависимостей (@assistant-ui/react, react, zod, vite, vitest, …)
npm test            # vitest run && node --test tests/*.test.mjs
npm run build       # tsc --noEmit -p . && vite build && node scripts/check-dist.mjs ${PCBK_WEB_OUT:-../edge/static/chat}
```

`npm test` — два разных движка одним скриптом: `.test.ts`/`.test.tsx` идут через Vitest (jsdom,
`@testing-library/react`), а единственный `.test.mjs` (`web/tests/boot.test.mjs`) — через встроенный `node --test`.
Нужен Node.js 22.x (`engines.node` в `package.json`); сети при тестах не нужно (фикстуры свои), но `npm ci` тянет
зависимости из реестра (или из кеша — `--prefer-offline`, как делает фикстура `tests/integration`).

### `web/tests/boot.test.mjs` — загрузчик `public/boot.js`

Этот файл проверяет не React-приложение, а отдельный, встроенный прямо в `index.html`, загрузчик `web/public/boot.js`
— скрипт, который должен уметь сообщить о сбое до того, как успел загрузиться основной бандл. Тест исполняет исходник
в `vm.runInNewContext` с подставленными `window`/`document`/`XMLHttpRequest` и таймером (без браузера), проверяет,
что по `DOMContentLoaded` + 20 с без сигнала «стартовал» загрузчик один раз отправляет `POST /pcbk/api/web/report`
(`{"kind":"boot_failed"}`) и рисует текст «Чат не запустился в этом браузере…», и что ошибка ресурса стиля/картинки
чат не роняет (только `SCRIPT`). Отдельно (`boot_is_es5` / `es5_check_catches_newer_syntax`) — проверка, что
`boot.js` синтаксически ES5: разбирает файл деревом `typescript` (`ts.createSourceFile`, `ScriptTarget.ES5`) и идёт по
белым спискам видов узлов и операторов, так что любая конструкция новее ES5 (`let/const`, `=>`, шаблонные строки,
`?.`, `async`, генераторы, деструктуризация, `try {} catch {}` без переменной, разделитель `_` в числах и т. д.) даёт
явное нарушение, а не тихо проходит мимо `forEachChild`.

## `edge` — вход nginx

У `edge` нет `package.json` — тесты запускаются `node --test` прямо по пути:

```bash
node --test edge/tests/*.test.mjs
```

`edge/tests/login.test.mjs` проверяет логику страницы входа (`edge/static/login.js`) в изоляции: состояния места
(`viewFor`), разбор ошибок входа (`loginError`), ответ `/me` (`meView`) — через подмену `fetch`/DOM/таймеров внутри
теста, без настоящего nginx. Полная проверка самого шаблона nginx (`edge/pcbk.conf.template`) — пути `/pcbk/api/*`,
зоны ограничения частоты, перенаправления — идёт уже в `tests/integration` через настоящий контейнер `edge`
(`tests/integration/test_edge.py`, `test_web.py`), потому что `node --test` здесь ничего не разворачивает.

## `tests/integration` — стенд целиком

### Что поднимается

`tests/integration/conftest.py` поднимает полную компоновку Docker (`compose.yaml` + `compose.test.yaml`) под
отдельным проектом `pcbk-test`, слушающим только `127.0.0.1:18443` (чтобы не конфликтовать с настоящим `8443`).
`compose.test.yaml` переопределяет часть сервисов:

- `edge` — порт перебрасывается на `18443`; тома статики и TLS — на временные каталоги фикстуры (копия
  `edge/static` + свежая сборка чата, самоподписанный сертификат), а не на дерево репозитория;
- `core` — команда заменена на `python -m twin_core` (см. выше), опрос свежести и `OpenRouter` чаще (`FRESH_POLL_S`,
  `LLM_POLL_S = 5`), `OPENROUTER_BASE_URL` указывает на контейнер `fake-openrouter`, общий бюджет поднят до
  `LLM_TOTAL_USD=1100` (11 мест по $100, как на сервере), двойники `/proc` для тормоза по памяти —
  `LLM_RUN_MEMINFO/VMSTAT/PRESSURE`;
- `watchdog` — те же двойники `/proc` своими путями (`MEMINFO_PATH`, `VMSTAT_PATH`, `PRESSURE_PATH`);
- `fake-openrouter` — собирается из `core/tests/Dockerfile.fake`, в сети выхода рядом с `core`
  (`172.31.250.84:8080` с хоста теста);
- `foreign` — контейнер-мишень для проверки, что прокси Docker отказывает чужим контейнерам (`403`);
- `student-01…11` — помечены `runtime: runc` (на машине разработчика нет `gVisor`, на сервере — `runsc`).

### Нужно вовне

Docker с плагином `docker compose`, `openssl` (тестовый TLS-сертификат эдж), Node.js и `npm` (фикстура
`build_test_static` копирует `edge/static` и `web/` во временный каталог и там же гоняет `npm ci` и
`npm run build`, чтобы не писать в рабочее дерево репозитория), `git` (метка сборки — короткий хеш `HEAD`), и
опционально `google-chrome` — без него фикстура `browser` помечает браузерные тесты пропущенными
(`pytest.skip("нет google-chrome")`), остальные тесты модуля всё равно идут.

Образы берутся только по дайджесту из `deploy/images.lock` (`prepare_images()` тянет и ставит тег на зафиксированный
`sha256`, если локально такого дайджеста нет) — это гарантирует, что тестовый стенд использует ту же версию образов,
что и выкладка, а не что попало с тегом `latest`.

### Как запустить

```bash
# из корня репозитория: pcbk_core.* тянет FastAPI/uvicorn/mcp/httpx — через requirements.lock core
uv run --python 3.12 --with-requirements core/requirements.lock --with pytest pytest -q -p no:cacheprovider tests/integration
```

Часть модулей (например, `test_web.py`, `test_gateway.py`) напрямую импортирует `pcbk_core.gateway.accounts`,
`pcbk_core.settings`, `pcbk_core.gateway.rules` — у пакета `pcbk_core.gateway` в `__init__.py` есть `from fastapi
import …` на уровне модуля, так что без зависимостей `core` сам процесс `pytest` не соберётся уже на импорте.
Остальные локальные модули (`fake_openrouter`/`fake_bench` из `core/tests`, весь `pcbk_bench`, `pcbk_watchdog.checks`)
— чистый stdlib; путь к ним `conftest.py` добавляет сам (`sys.path.append` на `core/tests`, `bench`, `watchdog`).

Отдельный модуль или подкаталог можно указывать как обычно для pytest, например
`… pytest -q -p no:cacheprovider tests/integration/test_edge.py`.

### Фикстуры и порядок

- `student_image` (session) — собирает образ места `docker build -t … student/` один раз на весь прогон.
- `stack` (session) — главная фикстура: готовит временный `test.env` (сетевые переменные, секреты, тома), образ
  статики чата, двойники памяти, поднимает компоновку (`Stack.up()`) и отдаёт объект `Stack` с методами для
  обращения к стенду (`https(...)`, `exec(...)`, `inspect(...)`, `agents_admin(...)`, `llm_from_place(...)`,
  `iso_conf()` и т. д.); в конце — `down -v` и удаление томов мест по метке `pcbk-test.volume`, даже при сбое.
  Чужие контейнеры/сети/тома с такими же именами фикстура не трогает — она явно отказывается стартовать, если они
  уже существуют не от неё (`refuse_foreign`).
- `browser` (module), `page`/`isolated_page` (function) — headless Chrome через `cdp.py` (свой клиент DevTools
  Protocol, только stdlib): `page` — вкладка общего контекста (общие cookie — как вкладки одного профиля браузера
  студента), `isolated_page` — вкладка в своём контексте (свои cookie — вход другой учётки не трогает сессию
  студента). После каждого теста проверяется, что в консоли и в обработчиках страницы не осталось ошибок.

Порядок файлов — алфавитный (как у pytest по умолчанию), и это используется намеренно: `test_00_load.py` (нагрузка
на всех десяти местах) идёт первым, чтобы отработать на «чистой» памяти стенда; `test_accounts.py` и далее —
обычные модули. `tests/integration/benchstand.py` (`StackStand`, `BenchHarness`) — не тестовый модуль, а общая
обвязка, которую используют `test_00_load.py` и `test_bench.py` для прогона `pcbk_bench` на двойниках этого же
стенда.

### Переменные окружения компоновки

`STACK_VARS` в `conftest.py` — список имён, которые **только** `test.env` фикстуры может подставлять docker compose
(объявление, а не окружение вызывающей оболочки): сеть мест (`STU_NET`), TLS (`TLS_DIR`), каталоги секретов/агентов/
уроков (`SECRETS_DIR`, `AGENTS_DIR`, `LESSONS_DIR`), пороги памяти и опроса (`MEM_WARN_MIB`, `MEM_FAIL_MIB`,
`TICK_S`, `STALE_AFTER_S`, …), все переменные ролей `LLM_*`, `GW_*`, `AGENTS_*`, `DRILL_*` (учения), аналитика
(`ANALYTICS_*`), двойник историана стенда отбора (`PCBK_TWIN_HISTORIAN`), память (`LLM_RUN_MEM_FAIL_MIB`,
`PCBK_TEST_MEM`, `VMSTAT_PATH`, …) и путь готовой статики чата (`PCBK_TEST_STATIC`). Полный смысл каждой — в
[`03-configuration.md`](03-configuration.md); здесь важно, что тест читает и переопределяет их только через
`test.env`, никогда через переменные окружения вызывающей оболочки (`Stack.env` их явно исключает).

### Что требует живого стенда — помечено и пропускается само

Два модуля не поднимают `stack` и по умолчанию либо работают на тестовом Docker-стенде без переменных, либо вовсе
пропускаются:

- `test_isolation_live.py` — без переменной `PCBK_LIVE_ISO` проверяет изоляцию сетей **этого** Docker-стенда
  (`Stack.iso_conf()`); с `PCBK_LIVE_ISO=<путь к JSON-договору>` — ту же проверку на настоящем сервере по SSH, без
  поднятия стенда вообще (пароли и секреты только пробуются, их значения никуда не печатаются).
- `test_gateway_live.py` — `pytestmark = pytest.mark.skipif(not all(...))` по четырём переменным `PCBK_LIVE_BASE`,
  `PCBK_LIVE_A`, `PCBK_LIVE_B`, `PCBK_LIVE_SESSIONS` (адрес стенда и файлы логин+пароль двух пробных учёток); без
  них весь модуль пропускается целиком, с ними — гоняет ту же таблицу случаев шлюза, что `test_gateway.py`, но через
  туннель к настоящему `edge` и местам под `gVisor`.

### Общие помощники модуля (не тесты)

| Файл | Что даёт |
|---|---|
| `conftest.py` | `Stack`, фикстуры, вспомогательные `wait_for`, `leaks` (ищет секрет в трассировке pytest, как её печатает `--tb=long --showlocals`), `login_to_chat`, `shot` (снимок вкладки в `PCBK_SHOTS`, если задана) |
| `cdp.py` | свой клиент Chrome DevTools Protocol (WebSocket по RFC 6455 в нужном объёме), классы `Browser`/`Page`: вкладки, ввод, ожидания, CSP/консоль/сеть, снимки |
| `gwclient.py` | `GwClient` — клиент шлюза по HTTPS без браузера: вход/выход/рабочее место, поток `/event`; пароль и cookie в трассировке pytest не печатаются (обёрнуты `Secret`/`SecretBytes`) |
| `secret.py` | `Secret(str)`/`SecretBytes(bytes)` — `__repr__` без значения, чтобы `pytest --showlocals` не печатал пароли |
| `workplace.py` | константы образа места: `IMAGE` (тег образа студента), списки моделей, переменные окружения образа, точки монтирования, метка одноразовых контейнеров `ONESHOT_LABEL` |
| `benchstand.py` | `StackStand`/`BenchHarness` — обвязка `pcbk_bench` поверх `Stack` и двойников (режимы отбора, нагрузки, «агент = калькулятор») |
| `converse.js` | мини-скрипт на Bun (без импортов) — ведёт один разговор через API OpenCode места напрямую, печатает один JSON с итогом; используется там, где нужен настоящий клиент OpenCode, а не шлюз |
| `gateway_cases.py` | таблица разрешённых/запрещённых пар «путь-метод» шлюза к OpenCode — общая для `test_gateway.py` (стенд) и `test_gateway_live.py` (сервер), без импорта `pcbk_core` |

## Как писать новый тест в стиле репозитория

- Тесты — обычные функции `test_…(...)`, без классов `TestFoo`; имена самих файлов — `test_<модуль_или_тема>.py`.
- Каждый тестовый файл (и часто каждый непустой модуль-двойник) начинается докстрингом в одно-два предложения:
  что проверяется и какими средствами — без ссылок на день/раунд разработки.
- Фикстуры, которые нужны больше чем одному файлу, — в `conftest.py` каталога; разовые помощники — в `helpers.py`
  этого же каталога, и тест импортирует их явно по имени (`from helpers import …`, `from fakes import …`) —
  не «звёздочкой» и не через `conftest`.
- Двойники внешних систем — простые Python-объекты с тем же интерфейсом, что настоящий клиент (`FakeHistorian`
  вызывается как `QueryFn`, `FakeChat`/`FakeStand` повторяют интерфейс `GatewayChat`/`Stand`), а не моки
  (`unittest.mock`) поверх настоящего кода.
- Секреты теста (пароли, токены, cookie) всегда оборачиваются в `Secret`/`SecretBytes` (`tests/integration/secret.py`
  — и его собственная копия-паттерн, если модуль ещё не на пути импорта этого файла), чтобы они не осели в
  распечатке трассировки pytest при `--tb=long --showlocals`.
- Данные тегов и имена агентов в тестах и карточках — только вымышленные (`20FAKE_…`, `25FAKE_…`, `QFAKE_…`,
  `bench-…`), никогда настоящие имена тегов участка.
- Ассертам предпочитают сравнение целых кортежей/структур одной строкой (`assert (cat.total, cat.live) == (13, 4)`)
  там, где это не вредит читаемости, — так удобнее поддерживать соответствие нескольких полей сразу.
- Для сетевых двойников, которым нужен настоящий сокет (HTTP-сервер, TLS, «подвисающий» прокси), поток-сервер
  запускается в `daemon=True`-потоке на `127.0.0.1:0` (порт выбирает ядро) и гасится в `finally`/конце фикстуры
  (`shutdown()` + `server_close()` + `join(timeout=…)`) — ни один тест не должен оставить висящий поток другому
  тесту.
- Если тесту для прохода нужна внешняя программа (`openssl`, `google-chrome`, `node`) и её может не быть на машине
  — тест или фикстура сама решает это `pytest.skip(причина)`, а не падает.

## Связанные документы

- Что такое `catalog_search`/`tag_now`/`tag_period`/`tag_series`/`calc_runtime` и прочие инструменты, которые
  подставляют двойники и калькулятор `bench` — [`10-data-service.md`](10-data-service.md).
- Белый список моделей, прокси к OpenRouter, журнал вызовов, бюджет — [`12-llm-proxy.md`](12-llm-proxy.md).
- Учётные записи, рабочие места, прокси к OpenCode, которые имитируют `fake_places.py`/`FakeChat` —
  [`13-gateway.md`](13-gateway.md).
- Образ места, правила модели, агенты Помощник/Конструктор — [`14-workplaces-and-agents.md`](14-workplaces-and-agents.md).
- Полная сборка и выкладка, `compose.yaml`, `deploy/images.lock` — [`02-build-and-deploy.md`](02-build-and-deploy.md).
- Все переменные окружения компоновки — [`03-configuration.md`](03-configuration.md).
- Сторож, страница состояния, эксплуатационные скрипты — [`18-watchdog-and-ops.md`](18-watchdog-and-ops.md).
- Как добавить новый инструмент/модель/роль — [`21-extending.md`](21-extending.md).
