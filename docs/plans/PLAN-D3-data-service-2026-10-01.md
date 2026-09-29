# Д3. Служба данных над историаном — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** на сервере работает серверный слой `pcbk-core` в роли службы данных:
`catalog_search`, `tag_now` и `tag_period` по HTTP и MCP. К историану он ходит
только через фиксированные шаблоны SQL, белый список, общий бюджет, кэш и
общий семафор и на каждый вызов пишет событие. Страница состояния показывает
«Служба данных — жива» и «Историан БДРВ — последняя метка N с назад». Два
учения показывают сбой. Ответ службы по тегу за смену сходится с независимым
расчётом по сырому `Delta`.

**Architecture:** `core/` — пакет `pcbk_core`, одно приложение FastAPI на
порту 8000. Роли подключаются к нему через общий протокол `Role`: роутер,
здоровье, фоновые циклы и ASGI-монтирования. В Д3 роль одна — `data`, служба
данных. Д4 добавит `llm` (`/llm/v1`, адрес уже вшит в образ места Д2), Д5 —
`gateway`, в тот же процесс. Как отделить ручки шлюза от сетей мест, решает
Д5. К историану ведёт одна дверь — `HistorianGate`: общий семафор на 2, общий
предел частоты, срок 15 с. За ней стоит функция `QueryFn`; в тестах её
подменяет двойник. Каталог держится в памяти службы, в SQL уходят только имена
из пересечения каталога и белого списка. У сторожа появляется вид `historian`:
свежесть считает служба (`/health/historian`), пороги держит сторож. В сети
выхода `pcbk-egress` — только серверный слой; куда он может соединяться,
ограничивает охрана внутри его процесса.

**Tech Stack:** Python 3.12; FastAPI, uvicorn, MCP Python SDK (`mcp`: FastMCP,
Streamable HTTP) — последние выпуски на день выполнения, с хешами;
python-tds 1.17.1; pydantic; SQLite; pytest с плагином anyio (идёт с `mcp`);
сторож — stdlib; Docker Compose; google-chrome (снимки).

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(§1 — три роли в одном процессе; §3 п. 3; §4 — служба данных и её
инструменты; §8 — тишина и метки историана; §9 — строки «Успех 2», «Успех 4»,
«Наблюдаемость»; §13 п. 1–2). Факты об историане, открытые вопросы О1–О13 и
11 рекомендаций — [`docs/research/05-d3-historian-facts.md`](../research/05-d3-historian-facts.md):
рекомендации здесь обязательны. Формат, фикстура `stack`, виды
`components.json`, таблица сетей и `images.lock` —
[`PLAN-D1-foundation-2026-09-29.md`](PLAN-D1-foundation-2026-09-29.md); сети
мест, секреты Compose, `$SSH`, `stack.probe` —
[`PLAN-D2-workplaces-2026-09-30.md`](PLAN-D2-workplaces-2026-09-30.md);
дорожная карта, строка Д3 и таблица «что нужно от владельца» —
[`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md).

**Предпосылка.** Д2 влит в `main` с тегом `platform-d2`: в `compose.yaml` есть
сети `pcbk-stu-01…10` с зарезервированным адресом `.2`, каталог
`${SECRETS_DIR}` (`0700`) и секреты Compose. Ветка дня — `d3/data-service` от
`main`.

**Влезает ли в день — оценка по часам.** Задача 0 идёт параллельно задачам
1–4. Её итоги нужны задачам 2, 6 и 7. Если итогов к их началу нет, ставятся
значения по умолчанию из таблицы задачи 0; правка по итогам — одна константа и
одна строка теста.

| Задача | Часы | Где |
|---|---|---|
| 0. Утренняя проба историана: эталон сверки, пробный образ, 13 читающих запросов | (1 параллельно) | сервер, **нужен владелец** |
| 1. Каркас серверного слоя | 0,75 | локально |
| 2. Шаблоны SQL и соединение | 0,5 | локально |
| 3. Каталог, белый список и его построитель | 0,75 | локально |
| 4. Бюджет, частота, кэш, single-flight, семафор | 0,75 | локально |
| 5. Периоды, свежесть, журнал событий | 0,5 | локально |
| 6. Инструменты и HTTP | 1 | локально |
| 7. Сторож: вид `historian` | 0,5 | локально |
| 8. Серверный слой в компоновке | 1 | локально |
| 9. MCP Streamable HTTP | 0,75 | локально |
| 10. Выкладка и белый список на сервере | 0,75 | сервер, **нужен владелец** |
| 11. Живые проверки, учения, сверка | 1 | сервер, **нужен владелец** |
| 12. Закрытие дня | 1 | — |
| **Критический путь** | **9,25** | |

**Черта отсечения — седьмой час.** К ней зелёны задачи 1–8: служба с HTTP и
сторож в компоновке. Без них выкладывать нечего.

- **Задача 9 (MCP) не зелёна к седьмому часу.** Выкладка идёт без MCP.
  Видимый результат дня не меняется: туннель работает по HTTP. Задача 9 уходит
  первым делом в утро Д4 — Д4 и так начинается с подключения мест к службе по
  MCP.
- **Задача 8 не зелёна к седьмому часу.** Выкладки нет. Видимый результат —
  снимки локального стенда (`docker compose -p pcbk-local …`, пометка «не на
  сервере»): учение «служба остановлена» и строка «Историан БДРВ — нет связи с
  историаном» (локально историана нет). К ним — итоги пробы. Живые проверки
  переходят в утро Д4 и идут раньше его задач.
- **Утром нет владельца.** Задача 0 идёт, когда он появится; до того константы
  стоят по умолчанию. Без владельца не делаются: задача 0; шаг 4 задачи 10
  (построитель читает каталог историана); шаги 4–6 задачи 11 (ответ по
  туннелю, сверка, журнал событий). Выкладка тогда останавливается перед шагом 4 задачи 10:
  без белого списка серверный слой по правилу `/healthz` не здоров.
  Видимый результат — локальные снимки, как в ветке выше; владельцу — одна
  строка, что именно не сделано.
- **Историан из контейнера на мосту Docker недоступен** (задача 0, шаг 8, оба
  пути). Серверный слой к нему тоже не пройдёт. День продолжается локально,
  владельцу тем же часом уходит вопрос о сетевом пути к 1433 из контейнеров.

## Global Constraints

Действуют все ограничения Д1 и Д2 (коротко повторены ниже) и ограничения Д3.

**Из Д1 и Д2:**

- Сторонние образы — только закреплённые: в `Dockerfile` —
  `FROM имя:тег@sha256:…`, в `compose.yaml` — `имя:тег` с
  `pull_policy: never`, привязка — `deploy/images.lock`, `latest` запрещён.
  Базовый образ серверного слоя — тот же, что у сторожа:
  `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`.
- Секреты и данные заказчика — **никогда в git и в выводе проверок**. Перед
  каждым коммитом задач 0 и 10–12:
  `git diff --cached -U0 -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто.
  Д3 дописывает в файл шаблонов задания: шаблон имён тегов по схеме
  именования историана (номер участка и узла в начале имени), имена тегов
  пробы и сверки, `BDRV_PW=[^$<{ "]`.
- **Секреты контейнерам — только файлами** (секреты Compose или bind `:ro`),
  никогда через `environment`/`env_file`. Файлы секретов — `0444` в каталоге
  `0700` (Д2, решение 5).
- Личные учётные записи и имена сотрудников заказчика не пишутся нигде.
  Доступ к серверу — переменная `$SSH` из Д2; учётка ОС — `$(id -un)`.
- Dify не трогаем. `/opt/dify/scripts/.bdrv.env` только читается:
  копируется `install`, в задачах 0 и 11 подаётся `--env-file`. Ни одного
  изменения в `/opt/dify`, его контейнерах и сетях.
- `docker.sock` — только у `sp-ro` и `sp-ctl`.
- Сети — с закреплёнными подсетями и `name:` без префикса. Все, кроме
  `pcbk-public` и `pcbk-egress`, — `internal: true` с изолированным шлюзом.
  У `pcbk-public` маскарад выключен; `pcbk-egress` — только для `pcbk-core`,
  маскарад включён.
- Наружу публикуется только 8443 контейнера `edge`. Серверный слой через 8443
  не открывается до шлюза (Д5).
- Всё, что видит человек, — по-русски; имена в коде — по-английски;
  комментарии — по-русски и коротко. Время — с явным смещением от UTC.
- Свои вспомогательные скрипты, пробный образ и шаблоны — в рабочем каталоге
  задания (дальше `$JOB`), не в репозитории.

**Д3 добавляет:**

- **SQL — только функции `core/pcbk_core/data/sql.py`**, свободного SQL нет
  нигде. В представлениях провайдера (`Live`, `AnalogHistory`,
  `AnalogSummaryHistory`, `StateSummaryHistory`) нет `LIKE` и нет параметров
  драйвера. Имя тега — литерал из каталога ∩ белого списка через
  `TagName IN (…)`, не больше 16 имён. Вторая линия защиты —
  `fullmatch [A-Za-z0-9_]{1,128}` и удвоение `'`. `IN` и `OR` не стоят на
  ww-столбцах; `wwResolution` не сочетается с `wwCycleCount`.
- **Соединение:** python-tds 1.17.1, `autocommit=True`, перед запросами
  `SET LOCK_TIMEOUT 5000; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;`,
  `login_timeout=10`, `timeout=45`, без `cafile` (TLS — О5, вопрос владельцу).
  Одно соединение на вызов, пула нет.
- **Контракт** (замер 08.09): не больше 16 тегов; окно не длиннее 31 суток;
  до 288 точек (ряды и профиль — Д9); до 24 тего-суток, для одного тега — 31;
  окно короче часа считается за час. Частота: 60 вызовов за 5 минут на
  вызывающего (скользящее окно) и 300 запросов к историану за 5 минут на всю
  службу. Одновременно — не больше 2 запросов к историану. Срок вызова — 15 с.
- **Кэш:** закрытый период (конец старше 300 с) живёт 3600 с, открытый — 30 с,
  текущие значения — 15 с. «Сейчас» округляется вниз до 30 с. До 256 записей;
  при переполнении выбрасывается четверть с самым ранним сроком. Одинаковые
  параллельные запросы — один запрос (single-flight). Ошибки и отказы в кэш не
  идут.
- **Каждый ответ говорит, что посчитано:** источник, вид среднего и σ, пояс
  историана, допущение смен, качество, возраст, доля достоверных. «Нет тега»,
  «нет данных», «не в белом списке» и «дискретный» — разными словами.
  «Сейчас» — по часам историана, пояс — из `GETDATE() − GETUTCDATE()`.
- **Белый список** — файл на сервере `/opt/pcbk-reserve/data/whitelist.txt`, не
  в git. Всё, что вне него, служба отклоняет без SQL. `_LMN` исключён до слова
  владельца (Ruling 1).
- **Учётные данные БДРВ** — файлом `${SECRETS_DIR}/bdrv.env` с прежними
  именами `BDRV_HOST`, `BDRV_DB`, `BDRV_USER`, `BDRV_PW`, `BDRV_PORT`.
  Токены службы — хешами sha256 в `${SECRETS_DIR}/core-tokens`, сами токены
  (`data-token.<id>`) — только на сервере.
- **Тесты — без историана:** двойник `QueryFn`. Имена тегов в тестах только
  синтетические, в каждом есть `FAKE`.
- **Производственные данные читаются только при владельце** (задачи 0, 10,
  11). Классификатор безопасности Claude Code может потребовать его
  подтверждения — это ожидаемо, обходить его нельзя.
- **Журнал вызовов** — INFO в stderr через явный обработчик логгера
  `pcbk_core` (у корневого — WARNING, 05 §4.4). В строке — инструмент,
  вызывающий, канал, число тегов, период, стоимость, кэш, строки, мс, исход.
  **Значений тегов в логе нет.**
- **Зависимости Python** — точными версиями с хешами в
  `core/requirements.lock`, установка `--require-hashes --no-deps`;
  `python-tds==1.17.1`.
- Тесты серверного слоя запускаются из каталога `core/` командой
  `uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q`
  (дальше — `CORE_PYTEST`). Асинхронные тесты — `@pytest.mark.anyio`,
  фикстура `anyio_backend` → `"asyncio"`.

## Решения по умолчанию (Ruling)

**Ruling 1 — правило белого списка (правила от владельца пока нет; О9, О10).**
Требования берутся из прежнего правила (05 §3.6); кода оттуда нет. Правило
`d3-1`:

1. Имя — только `[A-Za-z0-9_]`: так записаны все 1104 имени прежнего списка.
   Системные `$…` и диагностика с точкой или `\` отпадают сами.
2. Участок — имя начинается с номера участка `20`…`25` (05 §3.6 п. 6).
   16xx, 17xx, 18xx, 29xx, 33xx — вне пилота.
3. Добавляются имена из файла `whitelist.extra` на сервере. Это группы вне
   номеров участка, которые прежний список уже пускал: качество полотна и
   общие по машине. Файл засевается один раз из прежнего списка
   `pcbk-ai-lab` (`pcbk/bdrv/whitelist.json`, все имена не на `20`…`25`),
   дальше правится руками по слову владельца.
4. Аналоговые (`TagType = 1`) — без служебных хвостов `_LMN`, `_TH`, `_HMI`,
   `_SP_HMI`, `_MV1`, `_m3`; регистр хвоста не важен. **`_LMN` исключён, пока
   владелец не скажет иначе.**
5. Дискретные (`TagType = 2`) — только хвосты состояний `_RUN`, `_OPN`, `_CLS`,
   `_ON`, `_OFF`, `_STOP`, `_FLT`, `_ALM`.
6. Правила 4 и 5 действуют и на имена из `whitelist.extra`.
7. Живость не фильтрует: мёртвый тег в списке отвечает «нет текущего
   значения».
8. Пустой результат файл не перезаписывает. В шапке файла — версия правила,
   время сборки UTC и счётчики.

Отнесение к узлам по описанию из прежнего правила не повторяется: оно служило
формам калькуляторов и потеряло 125 живых тегов участка на обрезанных
описаниях, а поиску службы узлы не нужны. **Цена ошибки:** если список шире
нужного, студент может спросить тег участка, который владелец не хотел
показывать. Это только чтение и в пределах бюджета, но значение уйдёт модели
в разговоре. Если уже нужного — нужный тег получит «не входит в белый список».
Лечится правкой `whitelist.extra` или правила, повтором построителя и
перезапуском `core`, около 10 минут. С прежним списком правило сверяется
счётчиками в задаче 10.

**Ruling 2 — выход серверного слоя в сеть.** `pcbk-egress` — обычная сеть
Docker с маскарадом: `172.31.250.80/28`, серверный слой на `.82`, динамические
адреса — только `.88/29`. В ней нет никого, кроме `pcbk-core`. Правило «ходить
только к историану на 1433, а с Д4 — к OpenRouter на 443» держит **сам
процесс**. Аудит-хук Python на событие `socket.connect` пропускает адреса из
списка и петлю, остальное прерывает `EgressDenied` до системного вызова.
Имена из списка разрешаются при старте, а при промахе — не чаще раза в минуту.
Правила `DOCKER-USER` в iptables не ставим: нужен `sudo`, без отдельной
настройки они не переживают перезагрузку, адреса OpenRouter (Cloudflare)
меняются, а на том же сервере работает Dify.

- **Что держится:** код службы, python-tds, SDK MCP и будущий клиент
  OpenRouter не откроют соединение ни с чем вне списка. Это проверяет тест, а
  при старте в журнале появляется строка охраны.
- **Остаточный риск 1:** на уровне сети из `pcbk-egress` открыт любой адрес —
  ЛВС, интернет и сам сервер через шлюз моста (SSH и всё, что пускает ufw).
  Задача 11 проверяет это живьём и записывает как факт.
- **Остаточный риск 2:** охрана работает внутри процесса. Если в `pcbk-core`
  выполнится чужой код (уязвимость зависимости), он её обойдёт — через
  `ctypes` или дочерний процесс.
- **Остаточный риск 3:** до историана идёт открытый текст через шлюз ИТ (О5).
- **Цена ошибки:** при взломе серверного слоя атакующий получит сеть сервера —
  ту же, что у Dify-стенда сегодня. Закрывается правилами `DOCKER-USER` для
  моста `pcbk-egress`: историан 1433 и OpenRouter 443 — пропустить, остальное —
  `DROP`. Это предложение владельцу на Д12, с его `sudo`.

**Ruling 3 — по каким тегам судить о свежести историана (О8).** Набор — восемь
аналоговых тегов белого списка с самой свежей меткой `Live` на момент загрузки
каталога. Набор перевыбирается при каждой загрузке, то есть раз в сутки.
Возраст считает служба: сколько прошло с тех пор, как наибольшая метка набора
в последний раз выросла (по монотонным часам службы). Возраст не меньше
возраста самой метки по часам историана. Так метки `Live`, опережающие
`GETDATE()` на десятки секунд (05, ловушка 5), не дают вечного «0 с», а подача,
замершая ещё до запуска службы, видна сразу. Пороги держит сторож: по
умолчанию 300 и 900 с, уточняются по итогам О11. **Цена ошибки:** если все
восемь тегов окажутся «тихими» (зона нечувствительности), ровный процесс даст
ложное «устарело». Лечится порогом или явным списком тегов свежести — полчаса
правки.

**Ruling 4 — имя агента в событии.** В Д3 — `NULL`. OpenCode не сообщает
службе, какой агент вызвал инструмент, а слова модели — не доказательство.
Вызывающий известен по токену (`student-NN` или `ops`). **Цена ошибки:**
преподаватель (Д10) и слой событий мнемосхем (М4) пока не видят имени агента.
Шлюз (Д5) знает агента текущей сессии и может сопоставить его по студенту и
времени; решается в Д5 или Д7.

**Ruling 5 — дискретный тег в `tag_period`.** Отказ по этому тегу со словами
«наработка — в следующих слайсах». Сводку по 0 и 1 служба не считает: среднее
0,37 технолога запутает. Наработка по `StateSummaryHistory` — Д9.

## Review Focus

1. **Смена через полночь и смена, которая только началась.** В 05:59 «текущая
   смена» — с 22:00 вчерашних суток, в 06:10 «предыдущая» — 22:00–06:00. В
   06:00:20 «текущая смена» короче минуты — служба должна отказать словами, а
   не прислать пустую сводку. Тесты — задача 5,
   `test_shift_presets_cross_midnight`, `test_period_shorter_than_minute_refused`.
2. **Нет тега, нет данных, не в списке, дискретный — четыре разных ответа.**
   `AnalogSummaryHistory` по чужому имени молча отдаёт 0 строк; без каталога
   «нет данных» и «нет тега» слились бы. Тест — задача 6,
   `test_tag_period_distinguishes_missing_unlisted_nodata_discrete`.
3. **Метки `Live` впереди часов историана.** Возраст значения должен
   приводиться к нулю, а расхождение больше 60 с — объясняться словами.
   Свежесть не должна навсегда застрять на «0 с» и должна замечать
   остановившуюся подачу. Тесты — задача 5,
   `test_future_stamps_use_time_since_advance`; задача 6,
   `test_tag_now_future_timestamp_clamped`.
4. **Историан завис или отвечает минуты.** Вызов должен закончиться за 15 с
   словами «не ответил». Место в семафоре держится, пока драйвер не вернётся,
   а новые запросы к историану не наваливаются. Тесты — задача 4,
   `test_gate_deadline_returns_timeout_and_keeps_slot`, `test_gate_busy_when_slots_held`.
5. **Рабочее место (Д4) стучится в MCP как `core:8000` или по адресу `.2`.**
   SDK с защитой от DNS rebinding отвечает 421, а при неверном монтировании —
   307 на `/mcp/`. Агент тогда молча остаётся без данных. Тест — задача 9,
   `test_mcp_accepts_core_host_header`.

---

## Карта файлов

```
core/pyproject.toml                   pytest: pythonpath = ["."]; uv: managed = false
core/requirements.in                  python-tds==1.17.1, fastapi, uvicorn, mcp — точные версии на день
core/requirements.lock                с хешами (uv pip compile --generate-hashes)
core/Dockerfile                       образ pcbk-reserve/core:d3, uid 10003
core/.dockerignore                    tests/, verify/, __pycache__/
core/pcbk_core/__init__.py
core/pcbk_core/main.py                вход: настройки, журнал, охрана выхода, роли, uvicorn; --healthcheck
core/pcbk_core/app.py                 протокол Role и create_app: /healthz по ролям (Д4 — llm, Д5 — gateway)
core/pcbk_core/settings.py            Settings.from_env — только несекретное
core/pcbk_core/secrets.py             read_env_file, BdrvConfig, TokenTable
core/pcbk_core/egress.py              охрана исходящих соединений (аудит-хук)
core/pcbk_core/logs.py                setup_logging: INFO в stderr явным обработчиком
core/pcbk_core/data/__init__.py       DataRole — роль «служба данных»
core/pcbk_core/data/sql.py            шаблоны SQL и литералы
core/pcbk_core/data/historian.py      python-tds, QueryFn, HistorianError, часы историана
core/pcbk_core/data/names.py          чтение списков имён
core/pcbk_core/data/catalog.py        каталог в памяти, поиск по словам, похожие
core/pcbk_core/data/build_whitelist.py  правило d3-1 → whitelist.txt (CLI)
core/pcbk_core/data/budget.py         контракт, стоимость, скользящее окно, HistorianGate
core/pcbk_core/data/cache.py          TTL-кэш, single-flight, округление «сейчас»
core/pcbk_core/data/periods.py        готовые периоды и смены
core/pcbk_core/data/freshness.py      свежесть историана для сторожа
core/pcbk_core/data/events.py         журнал событий агента (SQLite)
core/pcbk_core/data/service.py        DataService: три инструмента, фоновые загрузки
core/pcbk_core/data/http_api.py       POST /api/data/{tool}, GET /health/historian
core/pcbk_core/data/mcp_server.py     MCP Streamable HTTP: те же три инструмента
core/verify/delta_stats.py            эталон сверки по сырому Delta — службой не импортируется
core/verify/test_delta_stats.py
core/tests/helpers.py                 SETTINGS, DummyRole, run_python, make_service, sent_names, FakeMono
core/tests/fakes.py                   синтетический каталог, FakeHistorian, RecordingConnect
core/tests/conftest.py                anyio_backend, listeners, core_server
core/tests/test_*.py
watchdog/pcbk_watchdog/checks.py      + check_historian; check_http(ok_detail=)
watchdog/pcbk_watchdog/main.py        + вид historian, HIST_WARN_S/HIST_FAIL_S/HIST_STALE_S
watchdog/components.json              core → http «жива»; historian → historian
compose.yaml                          + core, сеть pcbk-egress, core в pcbk-front и pcbk-stu-NN, секреты; сторож :d3
compose.test.yaml                     + core: FRESH_POLL_S=5
deploy/env.example                    + DATA_DIR, HIST_WARN_S, HIST_FAIL_S
deploy/README.md                      + служба данных: секреты, токены, белый список, откат
.gitignore                            + core-tokens, data-token.*, whitelist.txt, whitelist.extra
tests/integration/conftest.py         + тестовые секреты и список core, методы stack
tests/integration/test_core.py
tests/integration/test_edge.py        DECLARED_ENV: + pcbk-core, HIST_* у сторожа; образы :d3
tests/integration/test_socket_proxy.py  core и historian больше не absent
docs/checks/D3.md                     журнал живых проверок — только вердикты
docs/checks/D3/*.png                  снимки страницы состояния
```

Д4 добавит `core/pcbk_core/llm/`, Д5 — `core/pcbk_core/gateway/`; в Д3 их нет.

**Сети** — таблицы Д1 и Д2 плюс:

| Сеть | Подсеть | Вид | Кто в ней |
|---|---|---|---|
| `pcbk-egress` | `172.31.250.80/28`, динамика только `172.31.250.88/29` | обычная, маскарад включён | только `pcbk-core` — `.82` |
| `pcbk-front` | как в Д1 | внутренняя, изолированный шлюз | + `pcbk-core` (сторож ходит к нему по имени) |
| `pcbk-stu-NN` | как в Д2 | внутренняя, изолированный шлюз | + `pcbk-core` — `${STU_NET}.N.2` |

Имена: образ `pcbk-reserve/core:d3`, контейнер `pcbk-core`, служба `core`,
uid 10003, порт 8000, том `pcbk-core-data` (→ `/var/lib/pcbk-core`), секреты
Compose `bdrv-env` (→ `/run/secrets/bdrv.env`) и `core-tokens`
(→ `/run/secrets/core-tokens`), белый список `${DATA_DIR}/whitelist.txt`
(→ `/app/data/whitelist.txt`). Образ сторожа — `pcbk-reserve/watchdog:d3`;
`:d2` остаётся на сервере для отката.

---

### Task 0: Утренняя проба историана

**Нужен владелец:** шаг 8 читает производственные данные. Классификатор
безопасности Claude Code может спросить его подтверждение. Проба отвечает на
О1–О4 и О11 и заодно на О2: как взвешено `Average` против сырого `Delta`. Шаги
1–6 — локально, параллельно задаче 1. Итоги — в `docs/checks/D3.md`
вердиктами с пометкой [П]. В журнал не попадают адреса, имена тегов,
значения и выдержки из `@@VERSION` сверх названия и номера версии.

**Files:**
- Create: `core/verify/delta_stats.py`, `core/verify/test_delta_stats.py`, `docs/checks/D3.md`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Point: t: datetime; v: float | None; good: bool`
  - `@dataclass(frozen=True) class Stats: n: int; min: float; max: float; last: float; mean_arith: float; mean_step: float; mean_linear: float; std_arith: float; std_step: float; covered_s: float`
  - `delta_stats(points: Sequence[Point], start: datetime, end: datetime) -> Stats` —
    точки сортируются и обрезаются по `[start, end]`. Ступенька: значение
    держится до следующей точки, у последней — до `end`. Линейная:
    трапеция между соседними хорошими точками, хвост после последней —
    ступенька. Плохая точка (`good=False` или `v is None`) открывает разрыв до
    следующей точки; разрывы не входят ни в одно среднее и ни в `covered_s`.
    `std_arith` — выборочное (n − 1), `std_step` — взвешенное по времени
    вокруг `mean_step`. Хороших точек нет → `ValueError`. Только stdlib, ни
    одного импорта `pcbk_core`.
  - `gap(a: float, b: float, scale: float) -> float` — `|a − b| / scale`; при
    `scale == 0` — `0.0`, если `a == b`, иначе `inf`
  - пробный образ `pcbk-probe/tds:1.17.1` на сервере, режимы `morning` и
    `verify` (задача 11)
  - вердикты для задач 2, 6, 7 — таблица «Решения по пробе» ниже

- [ ] **Step 1: Write the failing tests**

```python
# core/verify/test_delta_stats.py
S = datetime(2026, 10, 1, 6)
H = S + timedelta(hours=1)

def pts(*pairs):
    return [Point(S + timedelta(minutes=m), v, True) for m, v in pairs]

def test_step_vs_arith_discriminates():
    st = delta_stats(pts((0, 0.0), (50, 10.0)), S, H)
    assert st.mean_step == pytest.approx(10 * 10 / 60)
    assert st.mean_arith == pytest.approx(5.0)
    assert st.mean_linear == pytest.approx((0 + 10) / 2 * 50 / 60 + 10 * 10 / 60)
    assert (st.min, st.max, st.last, st.n) == (0.0, 10.0, 10.0, 2)

def test_constant_signal():
    st = delta_stats(pts((0, 7.0), (20, 7.0), (40, 7.0)), S, H)
    assert st.mean_step == st.mean_linear == st.mean_arith == 7.0 and st.std_step == 0.0

def test_bad_points_open_gaps():
    ps = pts((0, 1.0)) + [Point(S + timedelta(minutes=30), None, False)] + pts((45, 3.0))
    st = delta_stats(ps, S, H)
    assert st.covered_s == pytest.approx((30 + 15) * 60)
    assert st.mean_step == pytest.approx((1 * 30 + 3 * 15) / 45)
    assert st.mean_arith == pytest.approx(2.0) and st.n == 2

def test_points_outside_window_ignored():
    st = delta_stats(pts((-10, 100.0), (0, 1.0), (70, 100.0)), S, H)
    assert (st.min, st.max, st.n) == (1.0, 1.0, 1)

def test_no_good_points_raises():
    with pytest.raises(ValueError):
        delta_stats([Point(S, None, False)], S, H)

def test_gap():
    assert gap(10.0, 10.001, 10.0) == pytest.approx(1e-4)
    assert gap(1.0, 1.0, 0.0) == 0.0 and gap(1.0, 2.0, 0.0) == float("inf")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core/verify && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — `ModuleNotFoundError: delta_stats`

- [ ] **Step 3: Implement `delta_stats`, `gap`, `Point`, `Stats` in `core/verify/delta_stats.py` по интерфейсу выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core/verify && uv run --python 3.12 --with pytest pytest -q`
Expected: PASS (6 тестов)

- [ ] **Step 5: Commit**

```bash
git add core/verify/
git commit -m "Эталон сверки: среднее по времени и арифметическое по сырому Delta"
```

- [ ] **Step 6: Пробный образ (локально, в `$JOB/probe/`, не в репозитории)**

`Dockerfile`: `FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`.
`req.txt` — `python-tds==1.17.1` с зависимостями, по
`uv pip compile --generate-hashes --python-version 3.12`; ставится через
`pip install --no-cache-dir --require-hashes --no-deps -r req.txt`. Дальше
`COPY` копии `core/verify/delta_stats.py` и `probe.py`, `USER 65534`,
`ENTRYPOINT ["python", "/app/probe.py"]`. Тег — `pcbk-probe/tds:1.17.1`.
Образ едет на сервер через `docker save … | gzip | $SSH 'gunzip | docker load'`.

Как устроен `probe.py`:
- `BDRV_*` читаются из окружения, одна пара окружающих кавычек снимается:
  `--env-file` Docker кавычки не убирает.
- Соединение — как у службы: `autocommit=True`, `PRELUDE`, `login_timeout=10`,
  `timeout=45`.
- Печатается один объект JSON. В нём только агрегаты: числа строк, доли,
  секунды, относительные расхождения, имена столбцов схемы, название и
  номер версии. Имён тегов и значений нет ни в одном режиме. Ошибки — только
  класс исключения.
- Литерал даты — собственная функция пробы, а не `sql.py`: это независимость
  сверки.

Запросы режима `morning` по порядку. H — начало текущего часа по
`GETDATE()`, окно — `[H − 1 ч, H]`. «Участок» — имена на `20`…`25` без `$`.

| № | Запрос | Что печатается |
|---|---|---|
| 1 | `SELECT @@VERSION` | `sql_server`: название и номер версии (О1) |
| 2 | `SELECT Value FROM SystemParameter WHERE Name = 'HistorianVersion'` | `historian_version` или «не найдено: <класс>» (О1) |
| 3 | `SELECT GETDATE(), GETUTCDATE(), SYSDATETIMEOFFSET()` | `offset` (1 − 2, до 15 мин), совпадает ли с поясом п. 3 (О3) |
| 4 | `SELECT @@LANGUAGE, (SELECT date_format FROM sys.dm_exec_sessions WHERE session_id = @@SPID)` | `language`, `dateformat` (О4) |
| 5 | каталог — текст `catalog_sql()` из задачи 2 | строк, аналоговых, дискретных, секунд |
| 6 | `SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL` | по тегам участка: строк, секунд, возраст против п. 3 — p50, p95, max, число и минимум отрицательных, возраст самого свежего и 8-го по свежести аналогового; доля `Quality = 0` (О11) |
| 7–8 | сырой `Delta` за окно для самого свежего аналогового тега участка — формат **A** `YYYY-MM-DD HH:MM:SS.000` и формат **B** `YYYYMMDD HH:MM:SS.000` | на каждый формат: «внутри окна» / «вне окна» / «пусто» / «ошибка: <класс>» (О4). 01.10 различает форматы: при чтении «гггг-дд-мм» литерал станет 10 января |
| 9–12 | `SELECT DateTime, Value, Quality FROM AnalogHistory WHERE TagName = '<t>' AND DateTime >= '<a>' AND DateTime <= '<b>' AND wwRetrievalMode = 'Delta'` для ещё четырёх самых свежих аналоговых тегов участка, формат — прошедший в п. 7–8 | различимость `d = gap(mean_step, mean_arith, max − min)` на каждого кандидата |
| 13 | `SELECT * FROM AnalogSummaryHistory WHERE TagName = '<t>' AND StartDateTime >= '<a>' AND EndDateTime <= '<b>' AND wwCycleCount = 1` для кандидата с наибольшей `d` | `summary_columns` (имена столбцов); `gap` у `Average` к `mean_step`, `mean_linear`, `mean_arith`; у `StdDev` — к `std_step` и `std_arith`; `Minimum`/`Maximum` = min/max `Delta` (да/нет при `gap ≤ 1e-9`); `Last` = последнее `Delta`, если столбец есть; первая точка `Delta` ровно на `a` (да/нет); порядок числа строк `Delta` (десятки, сотни, тысячи); `d` выбранного (О2) |

Режим `verify` (задача 11) читает из stdin ответ службы `tag_period`.
Запросы: `SELECT GETDATE(), GETUTCDATE()` (свой пояс) и сырой `Delta` по тегу
ответа за его период, переведённый в местное время историана своим поясом.
Печатает вердикты:
- `bounds`: границы на 06/14/22, длина 8 ч, пояс ответа равен своему;
- `min`, `max`: `gap ≤ 1e-9` к размаху;
- `avg`: к виду из `avg_kind.code` ответа, `gap ≤ 1e-3`;
- `std`: к виду из `std_kind.code`, `gap ≤ 1e-2`;
- `last`: если поле есть — равенство;
- `delta_rows`: порядок числа строк.

- [ ] **Step 7: Предпроверки на сервере (только чтение, без владельца)**

`test -r /opt/dify/scripts/.bdrv.env && echo читается; grep -c '^BDRV_' /opt/dify/scripts/.bdrv.env; grep -cE "^BDRV_[A-Z]+=[\"']" /opt/dify/scripts/.bdrv.env; docker network inspect bridge --format '{{.Name}}'; docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -c '^172\.31\.250\.80/28$'; docker image inspect -f '{{json .RootFS}}' pcbk-probe/tds:1.17.1 | sha256sum`
Expected: «читается»; строк `BDRV_` — 4 или 5; число строк в кавычках
записать (проба кавычки снимает); сеть `bridge` есть; подсеть
`172.31.250.80/28` свободна (0); `RootFS` образа совпал с локальным.

- [ ] **Step 8: Проба `morning` (при владельце)**

`$SSH 'docker run --rm --network bridge --env-file /opt/dify/scripts/.bdrv.env --read-only --cap-drop ALL --security-opt no-new-privileges:true pcbk-probe/tds:1.17.1 morning' > "$JOB/probe-morning.json"`.
Если запрос 1 даёт `connect`, пробу повторить с `--network host` и записать,
какой путь сработал: тем же путём ходят построитель (задача 10) и сверка
(задача 11). Если не сработал ни один — вопрос владельцу (шапка).
Прежде чем переносить вывод в журнал: `grep -E -i -f <шаблоны> "$JOB/probe-morning.json"` — пусто.
Expected: на каждый из 13 запросов есть ответ — вердикт или класс ошибки;
вердикты перенесены в `docs/checks/D3.md`.

- [ ] **Step 9: Решения по пробе**

| Итог пробы | Решение | По умолчанию, пока итога нет |
|---|---|---|
| О4: формат B «внутри окна» | `DATE_FMT = "%Y%m%d %H:%M:%S.000"` — не зависит от языка сессии | `"%Y-%m-%d %H:%M:%S.000"` (работал 08.09 [П]) |
| О4: B нет, A «внутри окна» | `DATE_FMT = "%Y-%m-%d %H:%M:%S.000"` | — |
| О4: ни один не «внутри окна» | стоп: служба не может строить периоды; вопрос владельцу, день идёт локально | — |
| О2: есть `Last` и `LastDateTime`, и `Last` = последнему `Delta` | `SUMMARY_COLUMNS` + `("Last", "LastDateTime")`, `HAS_LAST = True` | `HAS_LAST = False`: поля `last` в ответе нет, в `notes` — «последнее значение за период пока не считается» |
| О2: наименьший `gap` у `Average` ≤ 1e-3 и хотя бы в 10 раз меньше второго | `AVG_KIND` = `"step"`, `"linear"` или `"arith"` | `"unverified"` |
| О2: то же для `StdDev`, порог 1e-2 | `STD_KIND` = `"step"` или `"arith"` | `"unverified"` |
| О2: у выбранного `d < 0.01` | виды не различимы: `AVG_KIND`/`STD_KIND` = `"unverified"`; повтор в задаче 11 на теге сверки | — |
| О11: 8-й по свежести аналоговый тег участка моложе 100 с | `HIST_WARN_S = 300`, `HIST_FAIL_S = 900` | те же |
| О11: он старше 100 с | `HIST_WARN_S` = 3 × его возраст, округлённое вверх до целой минуты, `HIST_FAIL_S` = 3 × `HIST_WARN_S` | — |
| О1, О3 | только вердикт: пояс служба берёт из `GETDATE() − GETUTCDATE()` на каждый опрос | — |

Expected: таблица с принятыми значениями — в `docs/checks/D3.md`, раздел
«Решения по пробе». Задачи 2, 6 и 7 берут значения оттуда.

- [ ] **Step 10: Commit** (после проверки на секреты)

```bash
git add docs/checks/D3.md
git commit -m "Д3: утренняя проба историана — версия, пояс, формат даты, вид среднего, задержка Live"
```

---

### Task 1: Каркас серверного слоя

**Files:**
- Create: `core/pyproject.toml`, `core/requirements.in`, `core/requirements.lock`,
  `core/Dockerfile`, `core/.dockerignore`, `core/pcbk_core/__init__.py`,
  `core/pcbk_core/main.py`, `core/pcbk_core/app.py`,
  `core/pcbk_core/settings.py`, `core/pcbk_core/secrets.py`,
  `core/pcbk_core/egress.py`, `core/pcbk_core/logs.py`,
  `core/pcbk_core/data/__init__.py` (пока пустой), `core/tests/helpers.py`,
  `core/tests/conftest.py`
- Test: `core/tests/test_skeleton.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Settings` — `CORE_PORT: int = 8000`,
    `BDRV_ENV_FILE: str = "/run/secrets/bdrv.env"`,
    `TOKENS_FILE: str = "/run/secrets/core-tokens"`,
    `WHITELIST_PATH: str = "/app/data/whitelist.txt"`,
    `DB_PATH: str = "/var/lib/pcbk-core/core.db"`, `FRESH_POLL_S: float = 30.0`,
    `CATALOG_REFRESH_S: float = 86400.0`, `CATALOG_RETRY_S: float = 60.0`;
    `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings`
    (пустое значение — умолчание, числа > 0, иначе `ValueError`)
  - `read_env_file(path: str) -> dict[str, str]` — `KEY=VALUE`, `#`-комментарии,
    пустые строки, необязательный `export `, одна пара окружающих кавычек
    снимается. Ошибочная строка → `ValueError("<файл>: строка N — не KEY=VALUE")`,
    **без содержимого строки**
  - `@dataclass(frozen=True) class BdrvConfig: host: str; port: int; database: str; user: str; password: str = field(repr=False)`;
    `BdrvConfig.from_env_file(path: str) -> BdrvConfig` — обязательны
    `BDRV_HOST`, `BDRV_USER`, `BDRV_PW`; `BDRV_DB` по умолчанию `Runtime`,
    `BDRV_PORT` — 1433; нет обязательного → `ValueError` с именем переменной
  - `class TokenTable: from_file(path: str) -> TokenTable; caller(self, bearer: str | None) -> str | None; ids: frozenset[str]` —
    строка файла `<id> <sha256 hex>`, id — `[a-z0-9-]{1,32}`; сверка —
    `hmac.compare_digest` по всем записям; иная строка → `ValueError`
  - `class EgressDenied(PermissionError)`;
    `install_egress_guard(allowed: Sequence[tuple[str, int]], allowed_nets: Sequence[str] = ("127.0.0.0/8", "::1/128")) -> None` —
    `sys.addaudithook` на `socket.connect` для AF_INET/AF_INET6. Адрес не из
    `allowed` (имена разрешаются при установке, при промахе — повторно, не
    чаще раза в 60 с) и не из `allowed_nets` → `EgressDenied("выход запрещён: <адрес>:<порт>")`;
    повторный вызов — `RuntimeError`; `egress_guard_installed() -> bool`
  - `setup_logging() -> logging.Logger` — логгер `pcbk_core`, INFO, свой
    `StreamHandler(sys.stderr)`, `propagate = False`, повторный вызов не
    добавляет второй обработчик
  - `class Role(Protocol)`: `name: str`; `router(self) -> APIRouter`;
    `health(self) -> tuple[bool, str]`; `lifespan(self) -> AbstractAsyncContextManager[None]`;
    `mounts(self) -> list[tuple[str, ASGIApp]]`
  - `create_app(settings: Settings, roles: Sequence[Role]) -> FastAPI` —
    роутеры ролей, затем `GET /healthz`: `200 {"ok": true, "roles": {имя: пояснение}}`,
    если все роли здоровы, иначе `503` с тем же телом и `"ok": false`.
    Жизненный цикл входит во все `lifespan()` ролей. Монтирования ролей
    ставятся **последними**, после всех маршрутов
  - `main.py`: `build_roles(settings: Settings) -> list[Role]` (в задаче 1 —
    `[]`, задача 6 добавит `DataRole`);
    `main(argv: list[str] | None = None) -> int`. `--healthcheck` делает
    `GET 127.0.0.1:CORE_PORT/healthz` и возвращает 0 при 200. Без аргументов:
    `setup_logging`, затем `install_egress_guard([(cfg.host, cfg.port)])` —
    строка журнала «охрана выхода: разрешено 1 направление», затем
    `uvicorn.run(create_app(...), host="0.0.0.0", port=CORE_PORT, log_config=None, access_log=False)`
  - образ `pcbk-reserve/core:d3`: базовый образ по дайджесту;
    `pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock`;
    uid и gid 10003; `/var/lib/pcbk-core` — владелец 10003, `0750`; `COPY pcbk_core`
    (без `tests/` и `verify/`); `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1`;
    `EXPOSE 8000`;
    `HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-m", "pcbk_core.main", "--healthcheck"]`;
    `CMD ["python", "-m", "pcbk_core.main"]`
  - `requirements.in` — точные версии: `python-tds==1.17.1`, `fastapi==`,
    `uvicorn==`, `mcp==` — последние выпуски на день выполнения
  - `helpers.py`: `SETTINGS` (пути во временный каталог), `sha(text) -> str`,
    `DummyRole(name, health)`, `run_python(code: str) -> str` (подпроцесс
    `sys.executable -c`, `cwd=core`, возвращает stdout, падает при коде ≠ 0);
    `conftest.py`: `anyio_backend`, `listeners` (два слушающих сокета на
    127.0.0.1, отдаёт их порты)

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_skeleton.py
def test_read_env_file_strips_quotes_and_export(tmp_path):
    p = tmp_path / "b.env"
    p.write_text("BDRV_HOST=h\nBDRV_PW=\"p w\"\n# комментарий\n\nexport BDRV_USER='u'\n")
    assert read_env_file(str(p)) == {"BDRV_HOST": "h", "BDRV_PW": "p w", "BDRV_USER": "u"}

def test_read_env_file_error_hides_line(tmp_path):
    p = tmp_path / "b.env"
    p.write_text("BDRV_HOST=h\nсекретная-строка\n")
    with pytest.raises(ValueError) as e:
        read_env_file(str(p))
    assert "строка 2" in str(e.value) and "секрет" not in str(e.value)

def test_bdrv_config_defaults_and_hidden_password(tmp_path):
    p = tmp_path / "b.env"
    p.write_text("BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=s3cr3t\n")
    cfg = BdrvConfig.from_env_file(str(p))
    assert (cfg.host, cfg.port, cfg.database, cfg.user) == ("h", 1433, "Runtime", "u")
    assert "s3cr3t" not in repr(cfg)
    p.write_text("BDRV_HOST=h\nBDRV_USER=u\n")
    with pytest.raises(ValueError, match="BDRV_PW"):
        BdrvConfig.from_env_file(str(p))

def test_token_table(tmp_path):
    p = tmp_path / "t"
    p.write_text(f"student-01 {sha('tok-a')}\nops {sha('tok-b')}\n")
    t = TokenTable.from_file(str(p))
    assert (t.caller("tok-a"), t.caller("tok-b")) == ("student-01", "ops")
    assert t.caller("tok-c") is None and t.caller("") is None and t.caller(None) is None
    assert t.ids == frozenset({"student-01", "ops"})

@pytest.mark.parametrize("line", ["Student_01 " + "0" * 64, "ops abc", "ops"])
def test_token_table_rejects_bad_lines(tmp_path, line):
    p = tmp_path / "t"
    p.write_text(line + "\n")
    with pytest.raises(ValueError):
        TokenTable.from_file(str(p))

def test_egress_guard_blocks_other_destinations(listeners):
    a, b = listeners
    out = run_python(f"""
import socket, time
from pcbk_core.egress import install_egress_guard, EgressDenied, egress_guard_installed
install_egress_guard([("localhost", {a})], allowed_nets=())
assert egress_guard_installed()
socket.create_connection(("127.0.0.1", {a}), 1).close()
for dest in (("127.0.0.1", {b}), ("192.0.2.1", 443)):
    t = time.monotonic()
    try:
        socket.create_connection(dest, 5); print("OPEN")
    except EgressDenied:
        print("DENIED", time.monotonic() - t < 0.5)
""")
    assert out.split() == ["DENIED", "True", "DENIED", "True"]

def test_egress_guard_allows_nets(listeners):
    a, b = listeners
    out = run_python(f"""
import socket
from pcbk_core.egress import install_egress_guard
install_egress_guard([], allowed_nets=("127.0.0.0/8",))
socket.create_connection(("127.0.0.1", {b}), 1).close(); print("OPEN")
""")
    assert out.strip() == "OPEN"

def test_healthz_aggregates_roles():
    ok, bad = DummyRole("data", (True, "ок")), DummyRole("llm", (False, "нет ключа"))
    with TestClient(create_app(SETTINGS, [ok])) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"ok": True, "roles": {"data": "ок"}}
    with TestClient(create_app(SETTINGS, [ok, bad])) as c:
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["roles"]["llm"] == "нет ключа"

def test_settings_from_env():
    assert Settings.from_env({"CORE_PORT": "", "FRESH_POLL_S": "5"}).FRESH_POLL_S == 5.0
    with pytest.raises(ValueError):
        Settings.from_env({"FRESH_POLL_S": "0"})

def test_logging_info_reaches_stderr_once(capfd):
    logging.getLogger().setLevel(logging.WARNING)
    setup_logging()
    setup_logging().info("pcbk-marker")                    # второй вызов не добавил обработчик
    assert capfd.readouterr().err.count("pcbk-marker") == 1

def test_lock_pins_python_tds_and_hashes():
    text = Path("requirements.lock").read_text()
    assert re.search(r"^python-tds==1\.17\.1\b", text, re.M)
    reqs = [line for line in text.splitlines() if re.match(r"^[A-Za-z0-9]", line)]
    assert reqs and all("==" in line for line in reqs)
    assert text.count("--hash=sha256:") >= len(reqs)
```

- [ ] **Step 2: Run tests to verify they fail**

Сначала `requirements.in` и
`uv pip compile core/requirements.in --python-version 3.12 --generate-hashes -o core/requirements.lock`,
затем `cd core && CORE_PYTEST tests/test_skeleton.py`.
Expected: FAIL — `ModuleNotFoundError: pcbk_core`

- [ ] **Step 3: Implement каркас по интерфейсам выше**

Охрана выхода — только `sys.addaudithook`. Аудит-хук снять нельзя, поэтому
тесты ставят его в подпроцессе. Семейства, кроме AF_INET и AF_INET6
(сокеты-пары asyncio — AF_UNIX), пропускаются. `create_app` — `FastAPI(lifespan=…)`,
где жизненный цикл — `AsyncExitStack` над `lifespan()` ролей.

- [ ] **Step 4: Run tests to verify they pass**

Run: `(cd core && CORE_PYTEST tests/test_skeleton.py) && docker build -t pcbk-reserve/core:d3 core/`
Expected: PASS; образ собран

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Серверный слой: каркас ролей, секреты файлами, токены хешами, охрана выхода, образ"
```

---

### Task 2: Шаблоны SQL и соединение

**Files:**
- Create: `core/pcbk_core/data/sql.py`, `core/pcbk_core/data/historian.py`,
  `core/tests/fakes.py` (`RecordingConnect`)
- Test: `core/tests/test_sql.py`, `core/tests/test_historian.py`

**Interfaces:**
- Consumes: `BdrvConfig` — задача 1; `DATE_FMT`, `SUMMARY_COLUMNS`, `HAS_LAST` —
  решения задачи 0.
- Produces (`sql.py`):
  - `MAX_NAMES = 16`; `SAFE_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")`;
    `PROVIDER_VIEWS = ("Live", "AnalogHistory", "AnalogSummaryHistory", "StateSummaryHistory")`
  - `DATE_FMT: str` — по задаче 0; `HAS_LAST: bool`;
    `SUMMARY_COLUMNS = ("Minimum", "Maximum", "Average", "StdDev", "PercentGood")`
    плюс `("Last", "LastDateTime")` при `HAS_LAST`
  - `lit_name(name: str) -> str` — `SAFE_NAME.fullmatch`, иначе `ValueError`;
    возвращает `'имя'` с удвоением `'`
  - `lit_dt(dt: datetime) -> str` — только наивное время (местное историана),
    иначе `ValueError`; секунды без долей, `'…'` по `DATE_FMT`
  - `clock_sql() -> str` = `SELECT GETDATE() AS NowLocal, GETUTCDATE() AS NowUtc`
  - `catalog_sql() -> str` = `SELECT t.TagName, t.Description, t.TagType, a.MinEU, a.MaxEU, e.Unit FROM Tag t LEFT JOIN AnalogTag a ON a.TagName = t.TagName LEFT JOIN EngineeringUnit e ON e.EUKey = a.EUKey WHERE t.TagType IN (1, 2)`
  - `live_all_sql() -> str` = `SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL`
  - `live_sql(names: Sequence[str]) -> str` = `SELECT TagName, DateTime, Value, Quality FROM Live WHERE TagName IN (<литералы>)`
  - `summary_sql(names: Sequence[str], start: datetime, end: datetime) -> str` =
    `SELECT TagName, <SUMMARY_COLUMNS через запятую> FROM AnalogSummaryHistory WHERE TagName IN (<литералы>) AND StartDateTime >= <lit_dt(start)> AND EndDateTime <= <lit_dt(end)> AND wwCycleCount = 1`
  - `live_sql` и `summary_sql`: 0 или больше 16 имён → `ValueError`
- Produces (`historian.py`):
  - `Row = tuple`; `QueryFn = Callable[[Sequence[str]], list[list[Row]]]` —
    несколько SQL на одном соединении, по списку строк на каждый
  - `PRELUDE = "SET LOCK_TIMEOUT 5000; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;"`
  - `class HistorianError(Exception): code: Literal["connect", "timeout", "query"]; detail: str` —
    `__init__(self, code, detail)`
  - `tds_query(cfg: BdrvConfig, connect: Callable[..., Any] = pytds.connect) -> QueryFn` —
    на каждый вызов одно соединение
    `connect(server=cfg.host, port=cfg.port, database=cfg.database, user=cfg.user, password=cfg.password, autocommit=True, login_timeout=10, timeout=45, appname="pcbk-core")`,
    затем `cursor.execute(PRELUDE)` и на каждую строку `cursor.execute(sql)`
    **одним аргументом** и `fetchall()`; закрытие в `finally`. Ошибки:
    исключение из `connect` → `connect`; `socket.timeout`/`TimeoutError` в
    чтении → `timeout`; `OSError` в чтении → `connect`; `pytds.Error` →
    `query` (`detail` — класс и первые 200 знаков)
  - `@dataclass(frozen=True) class HistClock: local: datetime; utc: datetime` со
    свойствами `offset: timedelta` (`local − utc`, округлено до 15 мин),
    `tz: timezone`, `tz_label: str` («UTC+05:00») и методом
    `aware(dt: datetime) -> datetime` (наивное местное → с поясом историана)
  - `parse_clock(rows: list[Row]) -> HistClock`

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_sql.py
T6, T14 = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 14)
SAMPLES = [clock_sql(), catalog_sql(), live_all_sql(), live_sql(["20FAKE_001_PV", "20FAKE_002_SP"]),
           summary_sql(["20FAKE_001_PV"], T6, T14)]

def provider(sql):
    return any(re.search(rf"\bFROM\s+{v}\b", sql) for v in PROVIDER_VIEWS)

def test_no_driver_parameters_single_statement():
    for s in SAMPLES:
        assert not re.search(r"\?|%s|%\(|@", s) and ";" not in s, s

def test_like_limits():
    for s in SAMPLES:
        n = len(re.findall(r"\bLIKE\b", s, re.I))
        assert n <= 1 and (n == 0 or not provider(s)), s

def test_ww_columns_rules():
    for s in SAMPLES:
        assert not re.search(r"\bww\w+\s+IN\b", s, re.I) and not re.search(r"\bOR\b", s, re.I)
        assert not ("wwResolution" in s and "wwCycleCount" in s)

def test_history_views_always_name_tags():
    assert "WHERE TagName IN ('20FAKE_001_PV') AND StartDateTime >= " in summary_sql(["20FAKE_001_PV"], T6, T14)
    assert summary_sql(["20FAKE_001_PV"], T6, T14).endswith("AND wwCycleCount = 1")
    assert live_sql(["20FAKE_001_PV"]).endswith("FROM Live WHERE TagName IN ('20FAKE_001_PV')")

@pytest.mark.parametrize("bad", ["A'B", "A B", "20FAKE_001_PV\n", "", "ТЕГ_01", "x" * 129,
                                 "A;B", "A.B", "$Sys", "A--"])
def test_name_literal_rejects(bad):
    with pytest.raises(ValueError):
        lit_name(bad)

def test_name_literal_ok():
    assert lit_name("20FAKE_001_PV") == "'20FAKE_001_PV'"

def test_in_list_bounds():
    with pytest.raises(ValueError):
        live_sql([])
    with pytest.raises(ValueError):
        live_sql([f"20FAKE_{i:03d}_PV" for i in range(17)])

def test_date_literal():
    expected = {"%Y%m%d %H:%M:%S.000": "'20261001 06:00:00.000'",
                "%Y-%m-%d %H:%M:%S.000": "'2026-10-01 06:00:00.000'"}[DATE_FMT]
    assert lit_dt(datetime(2026, 10, 1, 6, 0, 0, 999_999)) == expected
    with pytest.raises(ValueError):
        lit_dt(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))

# core/tests/test_historian.py
CFG = BdrvConfig("h", 1433, "Runtime", "u", "p")

def test_tds_query_connect_args_and_prelude():
    rec = RecordingConnect(results=[[("20FAKE_001_PV",)]])
    assert tds_query(CFG, connect=rec)(["SELECT 1"]) == [[("20FAKE_001_PV",)]]
    kw = rec.kwargs
    assert (kw["autocommit"], kw["login_timeout"], kw["timeout"], kw["database"]) == (True, 10, 45, "Runtime")
    assert "cafile" not in kw
    assert rec.executed == [(PRELUDE,), ("SELECT 1",)]          # один аргумент — без параметров драйвера
    assert rec.closed

def test_one_connection_for_many_statements():
    rec = RecordingConnect(results=[[(1,)], [(2,)]])
    assert tds_query(CFG, connect=rec)(["SELECT 1", "SELECT 2"]) == [[(1,)], [(2,)]]
    assert rec.connect_count == 1

@pytest.mark.parametrize("where,exc,code", [
    ("connect", ConnectionRefusedError(), "connect"),
    ("execute", socket.timeout(), "timeout"),
    ("execute", pytds.ProgrammingError("bad"), "query"),
])
def test_errors_are_mapped_and_connection_closed(where, exc, code):
    rec = RecordingConnect(**{f"fail_on_{where}": exc})
    with pytest.raises(HistorianError) as e:
        tds_query(CFG, connect=rec)(["SELECT 1"])
    assert e.value.code == code and (rec.closed or where == "connect")

def test_parse_clock():
    c = parse_clock([(datetime(2026, 10, 1, 12, 0, 7), datetime(2026, 10, 1, 7, 0, 3))])
    assert (c.offset, c.tz_label) == (timedelta(hours=5), "UTC+05:00")
    assert c.aware(datetime(2026, 10, 1, 11, 59, 18)).isoformat() == "2026-10-01T11:59:18+05:00"
```

`RecordingConnect` в `fakes.py` — двойник `pytds.connect`: запоминает
`kwargs`, `connect_count`, кортежи аргументов `execute` в `executed`, отдаёт
`results` по очереди из `fetchall`; флаг `closed`; `fail_on_connect` и
`fail_on_execute` бросают заданное исключение.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_sql.py tests/test_historian.py`
Expected: FAIL — `ModuleNotFoundError: pcbk_core.data.sql`

- [ ] **Step 3: Implement `sql.py` и `historian.py` по интерфейсам выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core && CORE_PYTEST tests/test_sql.py tests/test_historian.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/pcbk_core/data/sql.py core/pcbk_core/data/historian.py core/tests/
git commit -m "Служба данных: фиксированные шаблоны SQL без LIKE и параметров, соединение python-tds"
```

---

### Task 3: Каталог, белый список и его построитель

**Files:**
- Create: `core/pcbk_core/data/names.py`, `core/pcbk_core/data/catalog.py`,
  `core/pcbk_core/data/build_whitelist.py`
- Modify: `core/tests/fakes.py` (синтетический каталог)
- Test: `core/tests/test_catalog.py`, `core/tests/test_build_whitelist.py`

**Interfaces:**
- Consumes: `SAFE_NAME`, `catalog_sql` — задача 2; `Row`, `tds_query`,
  `HistorianError` — задача 2; `BdrvConfig`, `install_egress_guard` — задача 1.
- Produces:
  - `load_names(path: str) -> frozenset[str]` — строка на имя, `#`-комментарии
    и пустые строки пропускаются; имя вне `SAFE_NAME` → `ValueError("строка N: недопустимое имя")`;
    нет файла → `FileNotFoundError`
  - `norm(text: str) -> str` — нижний регистр, «ё» → «е», пробелы схлопнуты
  - `Kind = Literal["analog", "discrete"]`
  - `@dataclass(frozen=True) class TagInfo: name: str; description: str; kind: Kind; unit: str | None; min_eu: float | None; max_eu: float | None; live: bool; live_time: datetime | None` —
    литерал `None` в `Unit` → `None`; описание обрезано по краям
  - `@dataclass(frozen=True) class Lookup: status: Literal["ok", "unlisted", "unknown"]; info: TagInfo | None; similar: tuple[str, ...]`
  - `@dataclass(frozen=True) class SearchResult: matches: tuple[TagInfo, ...]; similar: bool; total: int`
  - `class Catalog`:
    - `from_rows(tag_rows: list[Row], live_rows: list[Row], whitelist: frozenset[str]) -> Catalog`
      (строки `catalog_sql` и `live_all_sql`); `empty() -> Catalog`; `loaded: bool`;
      `whitelisted: int`; `total: int`; `live: int`
    - `lookup(raw: str) -> Lookup` — `strip()`, без учёта регистра.
      `ok` — в белом списке, `info` несёт каноническое имя каталога. `unlisted` —
      в историане есть, в списке нет. `unknown` — нигде; тогда `similar` — до 5
      имён белого списка с той же основой (имя без последнего `_ХВОСТ`) или
      содержащих введённое
    - `search(query: str, limit: int = 10) -> SearchResult` — только теги
      белого списка. Слова запроса после `norm` должны все найтись в
      `norm(имя + " " + описание)`. Порядок: точное имя, имя с начала, затем
      по имени. Если ничего не нашлось — `similar=True`: теги, где нашлось
      хотя бы одно слово, по убыванию числа слов; если запрос похож на имя
      (`SAFE_NAME` и есть `_`) — ещё и теги с той же основой имени. `total` —
      число найденных до `limit`
    - `freshest(k: int = 8) -> tuple[str, ...]` — аналоговые живые теги белого
      списка по убыванию `live_time`
  - `build_whitelist.py`: `RULE_VERSION = "d3-1"`,
    `SECTION_PREFIX = re.compile(r"2[0-5]")` (с начала имени),
    `SERVICE_TAILS = ("_LMN", "_TH", "_HMI", "_SP_HMI", "_MV1", "_m3")`,
    `STATE_TAILS = ("_RUN", "_OPN", "_CLS", "_ON", "_OFF", "_STOP", "_FLT", "_ALM")`;
    `select_whitelist(tag_rows: list[Row], extra: frozenset[str]) -> list[str]` —
    правило Ruling 1, результат отсортирован и без повторов;
    `write_list(path: str, names: Sequence[str], header: Mapping[str, str]) -> None` —
    пустой список → `ValueError("пустой результат не записывается")`, старый
    файл цел; запись через временный файл и `os.replace`, права `0o444`,
    шапка — строки `# ключ: значение`;
    `main(argv: list[str] | None = None) -> int` — `--out`, `--extra`,
    `--env-file` (по умолчанию `/run/secrets/bdrv.env`). Ставит охрану выхода
    на `(BDRV_HOST, BDRV_PORT)`, одним соединением выполняет `catalog_sql()`,
    пишет файл. Печатает только счётчики: всего, аналоговых, дискретных, из
    `extra` найдено и не найдено, версия правила

```python
# core/tests/fakes.py — синтетический каталог (TagName, Description, TagType, MinEU, MaxEU, Unit)
CATALOG_ROWS = [
    ("20FAKE_001_PV", "Факт. знач. - расход массы", 1, 0.0, 100.0, "л/час"),
    ("20FAKE_002_SP", "Задание - расход массы", 1, 0.0, 100.0, "None"),
    ("20FAKE_003_PV", "Факт. знач. - уровень в ёмкости", 1, 0.0, 100.0, "None"),
    ("20FAKE_004_PV", "Факт. знач. - давление", 1, 0.0, 10.0, "мбар"),
    ("20FAKE_005_LMN", "Выход - регулятор расхода", 1, 0.0, 100.0, "%"),
    ("20FAKE_006_SP_HMI", "служебный", 1, 0.0, 1.0, "None"),
    ("25FAKE_007_CLS", "Клапан подачи закрыт", 2, None, None, None),
    ("25FAKE_008_DIAG", "диагностика привода", 2, None, None, None),
    ("16FAKE_009_PV", "вне участка", 1, 0.0, 1.0, "None"),
    ("QFAKE_010", "качество полотна", 1, 0.0, 1.0, "г/м2"),
    ("QFAKE_011_LMN", "качество полотна, выход", 1, 0.0, 1.0, "None"),
    ("$FAKE_SYS", "системный", 1, 0.0, 1.0, "None"),
    ("20FAKE.012.DIAG", "диагностика", 2, None, None, None),
]
EXTRA = frozenset({"QFAKE_010", "QFAKE_011_LMN", "QFAKE_GONE"})
WHITELIST = frozenset({"20FAKE_001_PV", "20FAKE_002_SP", "20FAKE_003_PV", "20FAKE_004_PV",
                       "25FAKE_007_CLS", "QFAKE_010"})
LIVE_ROWS = [   # TagName, DateTime, Value, Quality — время историана, наивное
    ("20FAKE_001_PV", datetime(2026, 10, 1, 11, 59, 18), 12.5, 0),
    ("20FAKE_002_SP", datetime(2026, 10, 1, 11, 50, 0), 12.0, 0),
    ("20FAKE_004_PV", datetime(2026, 10, 1, 11, 59, 50), 3.2, 64),
    ("25FAKE_007_CLS", datetime(2026, 10, 1, 11, 40, 0), 1.0, 0),
    ("16FAKE_009_PV", datetime(2026, 10, 1, 11, 59, 59), 0.5, 0),
]
```

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_build_whitelist.py
def test_select_whitelist_rule_d3_1():
    assert select_whitelist(CATALOG_ROWS, EXTRA) == sorted(WHITELIST)

def test_lmn_excluded_until_owner_decides():
    got = select_whitelist(CATALOG_ROWS, EXTRA)
    assert "20FAKE_005_LMN" not in got and "QFAKE_011_LMN" not in got     # и из extra

def test_write_list_refuses_empty_and_is_atomic(tmp_path):
    p = tmp_path / "whitelist.txt"
    write_list(str(p), ["20FAKE_001_PV"], {"rule": RULE_VERSION})
    assert oct(p.stat().st_mode & 0o777) == "0o444"
    assert load_names(str(p)) == {"20FAKE_001_PV"} and "# rule: d3-1" in p.read_text()
    with pytest.raises(ValueError):
        write_list(str(p), [], {"rule": RULE_VERSION})
    assert load_names(str(p)) == {"20FAKE_001_PV"}

def test_load_names_rejects_bad_line(tmp_path):
    p = tmp_path / "w"
    p.write_text("# шапка\n20FAKE_001_PV\n\n20FAKE 002\n")
    with pytest.raises(ValueError, match="строка 4"):
        load_names(str(p))

# core/tests/test_catalog.py
@pytest.fixture
def cat():
    return Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST)

def test_lookup_case_insensitive_and_strips(cat):
    r = cat.lookup("  20fake_001_pv ")
    assert (r.status, r.info.name, r.info.unit) == ("ok", "20FAKE_001_PV", "л/час")

def test_lookup_unlisted_vs_unknown(cat):
    assert cat.lookup("16FAKE_009_PV").status == "unlisted"
    assert cat.lookup("20FAKE_005_LMN").status == "unlisted"
    miss = cat.lookup("20FAKE_001")
    assert miss.status == "unknown" and "20FAKE_001_PV" in miss.similar

def test_unit_none_literal_is_null(cat):
    assert cat.lookup("20FAKE_002_SP").info.unit is None

def test_search_words_yo_and_all_words(cat):
    assert [t.name for t in cat.search("РАСХОД массы").matches] == ["20FAKE_001_PV", "20FAKE_002_SP"]
    assert [t.name for t in cat.search("емкости").matches] == ["20FAKE_003_PV"]   # в описании — «ёмкости»

def test_search_similar_when_nothing_matches_all_words(cat):
    r = cat.search("расход клапан")
    assert r.similar is True and {"20FAKE_001_PV", "25FAKE_007_CLS"} <= {t.name for t in r.matches}

def test_search_only_whitelisted(cat):
    assert cat.search("вне участка").matches == () and cat.search("системный").matches == ()

def test_freshest_whitelisted_analog_live(cat):
    assert cat.freshest(8) == ("20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP")

def test_empty_catalog():
    assert Catalog.empty().loaded is False and Catalog.empty().lookup("20FAKE_001_PV").status == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_catalog.py tests/test_build_whitelist.py`
Expected: FAIL — нет модулей

- [ ] **Step 3: Implement `names.py`, `catalog.py`, `build_whitelist.py` по интерфейсам и Ruling 1**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core && CORE_PYTEST tests/test_catalog.py tests/test_build_whitelist.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/pcbk_core/data/ core/tests/
git commit -m "Служба данных: каталог в памяти, поиск по словам и похожие, правило белого списка d3-1"
```

---

### Task 4: Бюджет, частота, кэш, single-flight, семафор

**Files:**
- Create: `core/pcbk_core/data/budget.py`, `core/pcbk_core/data/cache.py`
- Modify: `core/tests/fakes.py` (`FakeHistorian`, `CLOCK_ROWS`, `SUMMARY`)
- Test: `core/tests/test_budget.py`, `core/tests/test_cache.py`, `core/tests/test_gate.py`

**Interfaces:**
- Consumes: `QueryFn`, `Row`, `HistorianError` — задача 2.
- Produces (`budget.py`):
  - `@dataclass(frozen=True) class Limits: max_tags: int = 16; max_window_days: float = 31.0; max_points: int = 288; max_tag_days: float = 24.0; single_tag_days: float = 31.0; min_window: timedelta = timedelta(hours=1)`;
    `LIMITS = Limits()`; `max_points` — для рядов и профиля Д9
  - `cost_tag_days(n_tags: int, start: datetime, end: datetime) -> float`
  - `check_budget(n_tags: int, start: datetime, end: datetime, limits: Limits = LIMITS) -> str | None` —
    `None`, если в пределах; иначе текст отказа по первой нарушенной
    проверке, в таком порядке: «за один вызов — не больше 16 тегов»; «период
    длиннее 31 суток»; «запрос стоит X тего-суток при пределе 24 (для одного
    тега — 31): сократите период или число тегов» (X — два знака после
    запятой)
  - `class SlidingWindow: __init__(self, limit: int, window_s: float); allow(self, key: str, now: float) -> bool` —
    разрешённый вызов записывается, отказ — нет
  - `PER_CALLER_LIMIT = 60`, `GLOBAL_LIMIT = 300`, `RATE_WINDOW_S = 300.0`,
    `HIST_CONCURRENCY = 2`, `CALL_DEADLINE_S = 15.0`
  - `class GateRefused(Exception): code: Literal["rate", "busy"]`
  - `class HistorianGate: __init__(self, query: QueryFn, *, concurrency: int = HIST_CONCURRENCY, deadline_s: float = CALL_DEADLINE_S, global_window: SlidingWindow | None = None, monotonic: Callable[[], float] = time.monotonic)`;
    `async def run(self, statements: Sequence[str]) -> list[list[Row]]`;
    `in_flight: int`. Сначала общий предел (`allow("historian", …)`, отказ →
    `GateRefused("rate")`). Затем поток `asyncio.to_thread`: он берёт
    `threading.BoundedSemaphore` с ожиданием `deadline_s − 0.5` (не взял →
    `GateRefused("busy")`), выполняет запрос и отпускает место только после
    возврата драйвера. Снаружи — `asyncio.wait_for(…, deadline_s)`; по сроку →
    `HistorianError("timeout", …)`, поток при этом дорабатывает, держа место
- Produces (`cache.py`):
  - `TTL_NOW_S = 15`, `TTL_OPEN_S = 30`, `TTL_CLOSED_S = 3600`,
    `CLOSED_AFTER_S = 300`, `NOW_ROUND_S = 30`, `MAX_ENTRIES = 256`
  - `round_now(now: datetime) -> datetime` — вниз до 30 с
  - `ttl_for(tool: Literal["tag_now", "tag_period"], end: datetime | None, now: datetime) -> float`
  - `cache_key(tool: str, tags: Sequence[str], start: datetime | None, end: datetime | None) -> str` —
    sha256 от `[tool, sorted(tags), start, end]`
  - `class TTLCache: __init__(self, max_entries: int = MAX_ENTRIES); get(self, key: str, now: float) -> Any | None; put(self, key: str, value: Any, ttl: float, now: float) -> None; __len__`
    — при вставке в полный кэш сначала уходят протухшие, затем ⌈n/4⌉ записей
    с самым ранним сроком
  - `class SingleFlight: async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, bool]` —
    `(результат, shared)`; ошибка доходит до всех ждущих; ключ снимается по
    завершении
- Produces (`fakes.py`):
  - `CLOCK_ROWS = [(datetime(2026, 10, 1, 12, 0, 0), datetime(2026, 10, 1, 7, 0, 0))]`
  - `SUMMARY = {"20FAKE_001_PV": {"Minimum": 10.0, "Maximum": 15.0, "Average": 12.4, "StdDev": 1.1, "PercentGood": 100.0, "Last": 12.5, "LastDateTime": datetime(2026, 10, 1, 5, 59, 40)}}`
  - `class FakeHistorian` — вызываемый `QueryFn`. Разбирает SQL
    регулярками: `^SELECT GETDATE\(\)` → `CLOCK_ROWS`; `^SELECT t\.TagName` →
    `CATALOG_ROWS`; `FROM Live WHERE Value IS NOT NULL$` → `LIVE_ROWS`;
    `FROM Live WHERE TagName IN \((.*)\)$` и
    `FROM AnalogSummaryHistory WHERE TagName IN \((.*?)\) AND` → строки только
    для имён из **литералов** (`'([^']*)'`), столбцы сводки — в порядке
    `SUMMARY_COLUMNS`. Иной SQL → `AssertionError("двойник не знает запроса")`.
    Поля: `calls: list[list[str]]`, `max_concurrency: int`, `delay_s: float`,
    `fail: HistorianError | None`,
    `live: dict[str, tuple[datetime, float, int]]` (имя → DateTime, Value,
    Quality), `summary: dict[str, dict[str, Any]]` (имя → столбцы),
    `clock: list[Row]` — копии данных выше, их можно менять в тесте

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_budget.py
T6 = datetime(2026, 10, 1, 6)

def test_budget_contract():
    assert check_budget(16, T6, T6 + timedelta(days=1)) is None                  # 16 тего-суток
    assert "16 тегов" in check_budget(17, T6, T6 + timedelta(hours=1))
    assert check_budget(1, T6, T6 + timedelta(days=31)) is None                  # один тег — 31
    assert "31 суток" in check_budget(1, T6, T6 + timedelta(days=31, seconds=1))
    assert check_budget(2, T6, T6 + timedelta(days=12)) is None                  # ровно 24
    assert "24,08" in check_budget(2, T6, T6 + timedelta(days=12, hours=1)).replace(".", ",")

def test_short_window_costs_one_hour():
    assert cost_tag_days(3, T6, T6 + timedelta(minutes=10)) == pytest.approx(3 / 24)

def test_sliding_window():
    w = SlidingWindow(60, 300)
    assert all(w.allow("student-01", float(t)) for t in range(60))
    assert w.allow("student-01", 100.0) is False and w.allow("student-02", 100.0) is True
    assert w.allow("student-01", 300.5) is True                                  # первый вызов ушёл из окна

# core/tests/test_cache.py
def test_round_now_and_ttl():
    now = datetime(2026, 10, 1, 12, 0)
    assert round_now(datetime(2026, 10, 1, 12, 0, 59)) == datetime(2026, 10, 1, 12, 0, 30)
    assert ttl_for("tag_now", None, now) == 15
    assert ttl_for("tag_period", now - timedelta(seconds=301), now) == 3600
    assert ttl_for("tag_period", now - timedelta(seconds=299), now) == 30

def test_cache_key_ignores_tag_order():
    assert cache_key("tag_now", ["B", "A"], None, None) == cache_key("tag_now", ["A", "B"], None, None)

def test_ttl_cache_expiry():
    c = TTLCache()
    c.put("k", 1, ttl=10, now=0.0)
    assert c.get("k", now=9.0) == 1 and c.get("k", now=10.5) is None

def test_ttl_cache_evicts_quarter_soonest():
    c = TTLCache(max_entries=8)
    for i in range(8):
        c.put(f"k{i}", i, ttl=10 + i, now=0.0)
    c.put("k8", 8, ttl=100, now=1.0)                       # полный: уходят k0 и k1 — самые ранние
    assert len(c) == 7 and c.get("k0", 1.0) is None and c.get("k1", 1.0) is None
    assert c.get("k2", 1.0) == 2 and c.get("k8", 1.0) == 8

@pytest.mark.anyio
async def test_single_flight_one_call_for_identical():
    sf, calls = SingleFlight(), []
    async def factory():
        calls.append(1); await anyio.sleep(0.05); return "r"
    results = await asyncio.gather(*(sf.run("k", factory) for _ in range(10)))
    assert len(calls) == 1 and sorted(shared for _, shared in results) == [False] + [True] * 9

@pytest.mark.anyio
async def test_single_flight_error_reaches_all_and_clears():
    sf, calls = SingleFlight(), []
    async def boom():
        calls.append(1); await anyio.sleep(0.01); raise HistorianError("connect", "x")
    rs = await asyncio.gather(*(sf.run("k", boom) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, HistorianError) for r in rs) and len(calls) == 1
    async def ok():
        calls.append(1); return "r"
    assert await sf.run("k", ok) == ("r", False) and len(calls) == 2    # ключ снят — новый вызов идёт

# core/tests/test_gate.py
@pytest.mark.anyio
async def test_gate_caps_concurrency():
    fake = FakeHistorian(delay_s=0.2)
    gate = HistorianGate(fake)
    await asyncio.gather(*(gate.run([live_sql([f"20FAKE_00{i}_PV"])]) for i in range(1, 6)))
    assert fake.max_concurrency == 2 and len(fake.calls) == 5

@pytest.mark.anyio
async def test_gate_deadline_returns_timeout_and_keeps_slot():
    fake = FakeHistorian(delay_s=1.0)
    gate = HistorianGate(fake, deadline_s=0.3)
    t = time.monotonic()
    with pytest.raises(HistorianError) as e:
        await gate.run([clock_sql()])
    assert e.value.code == "timeout" and time.monotonic() - t < 0.5
    assert gate.in_flight == 1                              # драйвер ещё держит место
    await anyio.sleep(0.9)
    assert gate.in_flight == 0

@pytest.mark.anyio
async def test_gate_busy_when_slots_held():
    fake = FakeHistorian(delay_s=1.0)
    gate = HistorianGate(fake, concurrency=1, deadline_s=0.8)
    first = asyncio.ensure_future(gate.run([clock_sql()]))
    await anyio.sleep(0.05)
    with pytest.raises(GateRefused) as e:
        await gate.run([clock_sql()])
    assert e.value.code == "busy" and len(fake.calls) == 1
    with pytest.raises(HistorianError):
        await first

@pytest.mark.anyio
async def test_gate_global_rate():
    fake = FakeHistorian()
    gate = HistorianGate(fake, global_window=SlidingWindow(3, 300), monotonic=lambda: 1.0)
    for _ in range(3):
        await gate.run([clock_sql()])
    with pytest.raises(GateRefused) as e:
        await gate.run([clock_sql()])
    assert e.value.code == "rate" and len(fake.calls) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_budget.py tests/test_cache.py tests/test_gate.py`
Expected: FAIL — нет модулей

- [ ] **Step 3: Implement `budget.py`, `cache.py`, `FakeHistorian` по интерфейсам выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core && CORE_PYTEST tests/test_budget.py tests/test_cache.py tests/test_gate.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/pcbk_core/data/budget.py core/pcbk_core/data/cache.py core/tests/
git commit -m "Служба данных: контракт 16/31/24, скользящие окна, общий семафор со сроком, кэш и single-flight"
```

---

### Task 5: Периоды, свежесть, журнал событий

**Files:**
- Create: `core/pcbk_core/data/periods.py`, `core/pcbk_core/data/freshness.py`,
  `core/pcbk_core/data/events.py`
- Test: `core/tests/test_periods.py`, `core/tests/test_freshness.py`, `core/tests/test_events.py`

**Interfaces:**
- Consumes: `round_now` — задача 4; `HistClock`, `Row` — задача 2.
- Produces (`periods.py`):
  - `SHIFT_STARTS = (6, 14, 22)` — допущение, решение владельца №6;
    `PRESETS = ("last_hour", "last_8h", "last_24h", "current_shift", "prev_shift", "today", "yesterday", "last_7d", "last_30d")`;
    `MIN_PERIOD = timedelta(seconds=60)`; `EARLIEST = datetime(2000, 1, 1)`
  - `@dataclass(frozen=True) class Period: start: datetime; end: datetime; preset: str | None; shift_assumed: bool; clamped: bool` —
    наивное местное время историана
  - `class PeriodError(ValueError)`
  - `resolve_period(now: datetime, offset: timedelta, preset: str | None = None, start: str | None = None, end: str | None = None) -> Period`:
    - «сейчас» = `round_now(now)`; нужен ровно один способ задать период:
      `preset` или пара `start` и `end`, иначе `PeriodError`;
    - `start`/`end` — ISO 8601 (`T` или пробел); с поясом — переводятся в
      местное через `offset`, без пояса — уже местное;
    - конец позже «сейчас» → «сейчас» и `clamped=True`;
    - начало раньше `EARLIEST`, конец не позже начала или период короче
      `MIN_PERIOD` → `PeriodError` со словами («период короче минуты» и т. п.);
    - у смен `shift_assumed=True`; ночная смена переходит через полночь

- Produces (`freshness.py`):
  - `ERROR_TEXTS = {"connect": "нет связи с историаном", "timeout": "историан не ответил вовремя", "query": "историан вернул ошибку", "catalog": "каталог тегов не загружен", "no_tags": "не выбраны теги свежести", "no_rows": "нет меток по тегам свежести"}`
  - `class FreshnessTracker`:
    - `observe(self, clock: HistClock, rows: list[Row], tags: int, mono: float, wall: datetime) -> None` —
      `rows` из `live_sql`; наибольшая метка набора; «сырой» возраст =
      `clock.local − метка`; `skew_s = min(0, сырой)`; если метка выросла —
      запоминается `mono` роста; строк нет → ошибка `no_rows`;
    - `fail(self, code: str, tags: int, mono: float, wall: datetime) -> None` —
      прошлые метки сохраняются;
    - `to_json(self, mono: float) -> dict` — ключи `checked_at` (ISO UTC или
      `None` до первого опроса), `age_s`, `skew_s`, `error`, `error_text`,
      `tags`. `age_s = max(max(0, сырой) + (mono − mono опроса), mono − mono роста)`,
      округление до 0,1 с; при ошибке `age_s` — по последним меткам или `None`
- Produces (`events.py`):
  - `Outcome = Literal["ok", "partial", "refused", "budget", "rate", "busy", "timeout", "unavailable", "error"]`;
    `Channel = Literal["mcp", "http"]`; `CacheState = Literal["hit", "miss", "shared", "none"]`
  - `@dataclass(frozen=True) class CallEvent: ts: datetime; caller: str; agent: str | None; channel: Channel; tool: str; tags: tuple[str, ...]; start: datetime | None; end: datetime | None; outcome: Outcome; summary: str; cost: float; cache: CacheState; rows: int; duration_ms: int` —
    `ts`, `start`, `end` — с поясом
  - `class EventLog: __init__(self, path: str); write(self, e: CallEvent) -> None; writable(self) -> bool; recent(self, limit: int = 50) -> list[CallEvent]` —
    SQLite (WAL), таблица `agent_events` с полями `CallEvent` (`tags` — JSON,
    время — ISO). `writable()` пробует `BEGIN IMMEDIATE; ROLLBACK`; `recent`
    — новые сверху. Д4 и Д5 добавят свои таблицы в тот же файл

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_periods.py
N = datetime(2026, 10, 1, 12, 0, 45)
OFF = timedelta(hours=5)

def p(now=N, **kw):
    return resolve_period(now, OFF, **kw)

def test_presets_basic():
    assert (p(preset="last_hour").start, p(preset="last_hour").end) == \
           (datetime(2026, 10, 1, 11, 0, 30), datetime(2026, 10, 1, 12, 0, 30))
    assert p(preset="current_shift").start == datetime(2026, 10, 1, 6)
    assert (p(preset="prev_shift").start, p(preset="prev_shift").end) == \
           (datetime(2026, 9, 30, 22), datetime(2026, 10, 1, 6))
    assert (p(preset="yesterday").start, p(preset="yesterday").end) == (datetime(2026, 9, 30), datetime(2026, 10, 1))
    assert p(preset="today").start == datetime(2026, 10, 1)
    assert p(preset="prev_shift").shift_assumed and not p(preset="last_hour").shift_assumed

def test_shift_presets_cross_midnight():                                        # Review Focus 1
    at = lambda h, m, preset: p(now=datetime(2026, 10, 1, h, m), preset=preset)
    assert (at(5, 59, "current_shift").start, at(5, 59, "current_shift").end) == \
           (datetime(2026, 9, 30, 22), datetime(2026, 10, 1, 5, 59))
    assert (at(5, 59, "prev_shift").start, at(5, 59, "prev_shift").end) == \
           (datetime(2026, 9, 30, 14), datetime(2026, 9, 30, 22))
    assert (at(23, 10, "current_shift").start, at(23, 10, "prev_shift").start) == \
           (datetime(2026, 10, 1, 22), datetime(2026, 10, 1, 14))
    assert (at(6, 10, "prev_shift").start, at(6, 10, "prev_shift").end) == \
           (datetime(2026, 9, 30, 22), datetime(2026, 10, 1, 6))

def test_period_shorter_than_minute_refused():                                  # Review Focus 1
    with pytest.raises(PeriodError, match="короче минуты"):
        p(now=datetime(2026, 10, 1, 6, 0, 20), preset="current_shift")

def test_custom_period_offset_and_clamp():
    q = p(start="2026-10-01T01:00:00+00:00", end="2026-10-01T03:00:00+00:00")
    assert (q.start, q.end, q.preset, q.clamped) == (datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 8), None, False)
    q = p(start="2026-10-01 10:00", end="2026-10-01 13:00")
    assert (q.end, q.clamped) == (datetime(2026, 10, 1, 12, 0, 30), True)

@pytest.mark.parametrize("kw", [
    {"start": "2026-10-01 10:00", "end": "2026-10-01 09:00"},
    {"start": "1999-12-31 00:00", "end": "2000-01-02 00:00"},
    {"start": "вчера", "end": "сегодня"},
    {"preset": "prev_shift", "start": "2026-10-01 10:00", "end": "2026-10-01 11:00"},
    {"preset": "last_year"},
    {},
])
def test_bad_periods(kw):
    with pytest.raises(PeriodError):
        p(**kw)

# core/tests/test_freshness.py
CLOCK = HistClock(local=datetime(2026, 10, 1, 12, 0, 0), utc=datetime(2026, 10, 1, 7, 0, 0))
W = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)

def rows(*stamps):
    return [(f"20FAKE_{i:03d}_PV", s, 1.0, 0) for i, s in enumerate(stamps)]

def test_age_from_historian_clock():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 18), datetime(2026, 10, 1, 11, 58)), 2, mono=100.0, wall=W)
    assert f.to_json(100.0)["age_s"] == 42.0 and f.to_json(110.0)["age_s"] == 52.0
    assert f.to_json(100.0)["error"] is None and f.to_json(100.0)["tags"] == 2

def test_future_stamps_use_time_since_advance():                               # Review Focus 3
    f = FreshnessTracker()
    ahead = datetime(2026, 10, 1, 12, 0, 31)
    f.observe(CLOCK, rows(ahead), 1, mono=100.0, wall=W)
    assert (f.to_json(100.0)["age_s"], f.to_json(100.0)["skew_s"]) == (0.0, -31.0)
    f.observe(CLOCK, rows(ahead), 1, mono=130.0, wall=W)                        # метка не выросла
    assert f.to_json(160.0)["age_s"] == 60.0
    f.observe(CLOCK, rows(ahead + timedelta(seconds=30)), 1, mono=190.0, wall=W)
    assert f.to_json(191.0)["age_s"] == 1.0

def test_old_data_at_start_is_old_at_once():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 0)), 1, mono=5.0, wall=W)
    assert f.to_json(5.0)["age_s"] == 3600.0

def test_errors_have_constant_texts():
    f = FreshnessTracker()
    assert f.to_json(0.0)["checked_at"] is None
    f.fail("connect", 8, mono=1.0, wall=W)
    j = f.to_json(1.0)
    assert (j["error"], j["error_text"], j["checked_at"]) == ("connect", "нет связи с историаном", W.isoformat())
    f.observe(CLOCK, [], 8, mono=2.0, wall=W)
    assert f.to_json(2.0)["error"] == "no_rows"

# core/tests/test_events.py
E = CallEvent(ts=datetime(2026, 10, 1, 7, tzinfo=timezone.utc), caller="student-01", agent=None,
              channel="http", tool="tag_now", tags=("20FAKE_001_PV",), start=None, end=None,
              outcome="ok", summary="20FAKE_001_PV = 12.5 л/час", cost=0.0, cache="miss", rows=1,
              duration_ms=12)

def test_event_roundtrip_newest_first(tmp_path):
    log = EventLog(str(tmp_path / "core.db"))
    log.write(E)
    log.write(replace(E, outcome="refused", tags=()))
    assert [e.outcome for e in log.recent()] == ["refused", "ok"]
    assert log.recent()[1] == E and log.writable() is True

def test_unwritable_log(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    log = EventLog(str(d / "core.db"))
    (d / "core.db").chmod(0o400)
    d.chmod(0o500)
    assert log.writable() is False
    with pytest.raises(sqlite3.Error):
        log.write(E)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_periods.py tests/test_freshness.py tests/test_events.py`
Expected: FAIL — нет модулей

- [ ] **Step 3: Implement `periods.py`, `freshness.py`, `events.py` по интерфейсам выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core && CORE_PYTEST tests/test_periods.py tests/test_freshness.py tests/test_events.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/pcbk_core/data/ core/tests/
git commit -m "Служба данных: смены 06/14/22 через полночь, свежесть по росту меток, журнал событий агента"
```

---

### Task 6: Инструменты и HTTP

**Files:**
- Create: `core/pcbk_core/data/service.py`, `core/pcbk_core/data/http_api.py`
- Modify: `core/pcbk_core/data/__init__.py` (`DataRole`), `core/pcbk_core/main.py`
  (`build_roles`), `core/tests/helpers.py` (`FakeMono`, `make_service`,
  `sent_names`, `make_app`)
- Test: `core/tests/test_service.py`, `core/tests/test_http.py`

**Interfaces:**
- Consumes: всё из задач 1–5; `AVG_KIND`, `STD_KIND` — решения задачи 0.
- Produces (`service.py`):
  - `AVG_KIND: Literal["step", "linear", "arith", "unverified"]`, `STD_KIND: Literal["step", "arith", "unverified"]`;
    `KIND_TEXTS = {"step": "взвешено по времени (ступенчатая интерполяция)", "linear": "взвешено по времени (линейная интерполяция)", "arith": "арифметическое по сырым значениям", "unverified": "вид не подтверждён сверкой"}`
  - постоянные тексты:
    - `SOURCE_LIVE = "текущее значение из таблицы Live историана"`
    - `SOURCE_SUMMARY = "сводка историана по сырым данным: AnalogSummaryHistory, одна корзина на весь период"`
    - `SHIFT_NOTE = "границы смен 06:00, 14:00, 22:00 — допущение, технологом не подтверждено"`
    - `OPEN_NOTE = "период ещё идёт — числа изменятся"`
    - `CLAMP_NOTE = "конец периода ограничен текущим временем историана"`
    - `NO_LAST_NOTE = "последнее значение за период пока не считается"`
    - `TRUNCATED_NOTE = "описания в историане обрезаны на 50 символах"`
    - `SIMILAR_NOTE = "точного совпадения нет — вот похожие, выберите вместе со студентом"`
    - `ITEM_TEXTS = {"no_value": "нет текущего значения", "no_data": "за период данных нет", "unlisted": "тег не входит в белый список стенда", "unknown": "такого тега нет в каталоге историана", "discrete": "дискретный тег: сводка не считается, наработка — в следующих слайсах"}`
    - `MESSAGES = {"rate": "слишком часто: не больше 60 вызовов за 5 минут — подождите минуту", "global_rate": "общий предел запросов к историану исчерпан — повторите через несколько минут", "busy": "историан занят другими запросами — повторите через минуту", "timeout": "историан не ответил за 15 с — сократите период или число тегов", "catalog": "каталог тегов ещё не загружен — историан недоступен с запуска службы", "connect": "нет связи с историаном", "query": "историан вернул ошибку — вызов записан в журнал", "none": "ни один тег нельзя запросить"}`
    - `STATE_LABELS = {"_RUN": ("работает", "стоит"), "_OPN": ("открыт", "не открыт"), "_CLS": ("закрыт", "не закрыт"), "_ON": ("включён", "не включён"), "_OFF": ("выключен", "не выключен"), "_STOP": ("остановлен", "не остановлен"), "_FLT": ("неисправность", "исправен"), "_ALM": ("авария", "нет аварии")}` —
      подпись для значения 1 и для 0; в ответе с пометкой «по суффиксу»
  - `class DataService`:
    - `__init__(self, gate: HistorianGate, events: EventLog, whitelist: frozenset[str], *, cache: TTLCache | None = None, flight: SingleFlight | None = None, per_caller: SlidingWindow | None = None, monotonic: Callable[[], float] = time.monotonic, wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc))`;
      публичные поля `catalog: Catalog`, `freshness: FreshnessTracker`,
      `events: EventLog`, `monotonic: Callable[[], float]`
    - `async def refresh_catalog(self) -> bool` — один вызов ворот:
      `[clock_sql(), catalog_sql(), live_all_sql()]`. Строит каталог, запоминает
      часы историана и набор свежести `catalog.freshest(8)`; в журнал —
      «каталог: N тегов, в белом списке M, живых K»; возвращает `True`.
      `HistorianError` или `GateRefused` → код запоминается в
      `catalog_error: str | None`, строка журнала, `False`; прежний каталог
      остаётся
    - `async def poll_freshness(self) -> None` — нет каталога →
      `fail(catalog_error or "catalog")`, то есть при недоступном с запуска
      историане строка сторожа — «нет связи с историаном»; пустой набор →
      `fail("no_tags")`; иначе ворота
      `[clock_sql(), live_sql(набор)]` → `observe`. `HistorianError` →
      `fail(code)`; `GateRefused` → без изменений (через 120 с сторож скажет
      «давно не опрашивала»)
    - «сейчас историана» = последние часы + прошедшее по `monotonic`. Если
      часы старше 300 с, перед запросом идёт `[clock_sql()]`
    - `async def catalog_search(self, caller: str, channel: Channel, query: str, limit: int = 10) -> dict`
    - `async def tag_now(self, caller: str, channel: Channel, tags: list[str]) -> dict`
    - `async def tag_period(self, caller: str, channel: Channel, tags: list[str], period: str | None = None, start: str | None = None, end: str | None = None) -> dict`
    - `def health(self) -> tuple[bool, str]` — `False`, если белый список пуст
      («белый список пуст или не найден») или журнал событий не пишется
      («журнал событий не пишется»); иначе `True` и «каталог: M тегов в
      белом списке» или «каталог ещё не загружен»
  - порядок в `tag_now` и `tag_period`: частота вызывающего → каталог
    загружен → разбор имён через `lookup` (повторы схлопываются, порядок
    `items` — как во вводе) → период и бюджет (`tag_period`) →
    кэш → single-flight → ворота → сборка ответа → кэш → событие → строка
    журнала. **Любой отказ до ворот не шлёт SQL.** Событие пишется на каждый
    вызов, включая отказы; ошибка записи логируется и не ломает ответ
  - строка журнала: `tool=… caller=… channel=… tags=N period=<начало>..<конец> cost=0.333 cache=miss rows=N ms=N outcome=ok` —
    без значений; `summary` события — до 200 знаков, значения там есть
    (журнал на сервере)
- Контракт ответов (JSON; только str, float, int, bool, null, списки, словари):
  - общие ключи: `tool`, `status` (`ok` | `partial` | `refused` | `budget` |
    `rate` | `busy` | `timeout` | `unavailable` | `error`), `message` (только
    при статусе не `ok`/`partial`), `notes: list[str]`, `cache`
  - `tag_now`: `asof` (сейчас историана с поясом), `tz` («UTC+05:00»), `source = SOURCE_LIVE`,
    `items[]`: `tag`, `status` (`ok` | `no_value` | `unlisted` | `unknown`),
    `text` (из `ITEM_TEXTS`, у `ok` нет), `similar` (у `unknown`),
    `description`, `unit`, `kind`, `value`, `time`, `age_s` (целое, не меньше
    0), `quality` («хорошее» при 0, иначе «недостоверное (код N)»),
    `state_label` (у дискретных с известным хвостом), `item_notes`: метка
    впереди часов больше чем на 60 с → «метка тега впереди часов историана на
    N с»; возраст больше 600 с → «значение не менялось N мин: это и ровный
    процесс, и возможное залипание — смотрите качество»
  - `tag_period`: `period` {`start`, `end` (ISO с поясом), `tz`, `preset`,
    `shift_grid_assumed`, `clamped`, `open`}, `source = SOURCE_SUMMARY`,
    `avg_kind` {`code`, `text`}, `std_kind` {`code`, `text`},
    `cost_tag_days` (3 знака), `items[]`: `tag`, `status` (`ok` | `no_data` |
    `discrete` | `unlisted` | `unknown`), `text`, `description`, `unit`, `avg`,
    `min`, `max`, `range` (= `max − min` той же сводки), `std`,
    `percent_good`, `last` и `last_time` (только при `HAS_LAST`),
    `quality_note` («доля достоверных N % — ниже 99 %»). `notes`: `SHIFT_NOTE`
    у смен, `OPEN_NOTE` у открытого периода, `CLAMP_NOTE`, `NO_LAST_NOTE`
  - `catalog_search`: `query`, `matches[]` {`tag`, `description`, `unit`,
    `kind`, `min_eu`, `max_eu`, `live`, `description_truncated`}, `similar`,
    `total`; `notes`: `SIMILAR_NOTE` при `similar`, `TRUNCATED_NOTE`, если
    среди найденных есть описание ровно в 50 знаков
  - `partial` — часть тегов отклонена, остальные запрошены; `refused` — SQL не
    было (`message`: текст `PeriodError` или `MESSAGES["none"]`); `budget` —
    текст `check_budget`; `rate` у вызывающего — `MESSAGES["rate"]`
  - ошибки ворот → статус: `HistorianError` `connect` → `unavailable` с
    `MESSAGES["connect"]`, `timeout` → `timeout`, `query` → `error`;
    `GateRefused` `busy` → `busy`, `rate` → `rate` с `MESSAGES["global_rate"]`;
    каталог не загружен → `unavailable` с `MESSAGES["catalog"]`
- Produces (`http_api.py`):
  - `class CatalogSearchArgs(BaseModel): query: str = Field(min_length=1, max_length=200); limit: int = Field(10, ge=1, le=50)`
  - `class TagNowArgs(BaseModel): tags: list[str] = Field(min_length=1, max_length=16)`
  - `class TagPeriodArgs(BaseModel): tags: list[str] = Field(min_length=1, max_length=16); period: Literal[<PRESETS>] | None = None; start: str | None = None; end: str | None = None`
  - `data_router(service: DataService, tokens: TokenTable) -> APIRouter` —
    `POST /api/data/catalog_search`, `/api/data/tag_now`, `/api/data/tag_period`
    с телом-моделью; `Authorization: Bearer` → вызывающий, иначе `401`
    `{"detail": "нужен токен"}`; ответ инструмента — всегда `200`, отказы
    внутри JSON. `GET /health/historian` — без токена,
    `service.freshness.to_json(service.monotonic())`
- Produces (`data/__init__.py`):
  - `class DataRole` (реализует `Role`, `name = "data"`):
    `__init__(self, settings: Settings, query: QueryFn, tokens: TokenTable, whitelist: frozenset[str])`;
    поле `service`. `lifespan()` запускает два цикла: каталог — сразу; при
    `False` повтор через `CATALOG_RETRY_S`, дальше раз в `CATALOG_REFRESH_S`;
    свежесть — сразу после первой попытки каталога, дальше раз в
    `FRESH_POLL_S`. На выходе оба цикла отменяются. `mounts()` → `[]` (MCP —
    задача 9)
  - `main.build_roles(settings)` → `[DataRole(settings, tds_query(BdrvConfig.from_env_file(...)), TokenTable.from_file(...), whitelist)]`.
    Нет файла белого списка → пустое множество и строка журнала: служба
    живёт, `/healthz` — 503
- Produces (`helpers.py`): `FakeMono` (вызываемый, `advance(s)`),
  `make_service(tmp_path, fake=None, whitelist=WHITELIST, mono=None) -> tuple[DataService, FakeHistorian]`
  (вызывает `setup_logging()`; `mono` по умолчанию — `FakeMono(1000.0)`,
  им же пользуются ворота и окна частоты),
  `sent_names(fake) -> list[str]` (литералы из всех `TagName IN (…)`),
  `make_app(tmp_path, fake=None) -> tuple[FastAPI, DataRole]`, `TOKEN`
  (токен `student-01` из тестового файла)

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_service.py
pytestmark = pytest.mark.anyio

@pytest.fixture
async def svc(tmp_path):
    s, fake = make_service(tmp_path)
    await s.refresh_catalog()
    fake.calls.clear()
    return s, fake

async def test_tag_now_answer_says_what_and_when(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20fake_001_pv"])
    it = r["items"][0]
    assert (r["status"], r["tz"], r["source"], r["cache"]) == ("ok", "UTC+05:00", SOURCE_LIVE, "miss")
    assert (it["tag"], it["value"], it["unit"], it["age_s"], it["quality"]) == \
           ("20FAKE_001_PV", 12.5, "л/час", 42, "хорошее")
    assert it["time"] == "2026-10-01T11:59:18+05:00" and r["asof"].endswith("+05:00")

async def test_tool_never_sends_unlisted_name(svc):
    s, fake = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV", "16FAKE_009_PV", "20FAKE_001_PV' OR 1=1--",
                                           "20FAKE_005_LMN", "20FAKE_001_PV\n"])
    assert sent_names(fake) == ["20FAKE_001_PV"]

async def test_tag_now_statuses_distinct(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20FAKE_003_PV", "16FAKE_009_PV", "20FAKE_404_PV"])
    assert r["status"] == "partial"
    assert [i["status"] for i in r["items"]] == ["no_value", "unlisted", "unknown"]
    assert len({i["text"] for i in r["items"]}) == 3

async def test_tag_now_future_timestamp_clamped(svc):                          # Review Focus 3
    s, fake = svc
    fake.live["20FAKE_001_PV"] = (datetime(2026, 10, 1, 12, 0, 31), 12.5, 0)
    fake.live["20FAKE_002_SP"] = (datetime(2026, 10, 1, 12, 1, 30), 12.0, 0)
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"])
    a, b = r["items"]
    assert (a["age_s"], a.get("item_notes", [])) == (0, [])                    # 31 с — без оговорки
    assert b["age_s"] == 0 and "впереди часов историана на 90 с" in b["item_notes"][0]

async def test_quality_and_discrete_labels(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20FAKE_004_PV", "25FAKE_007_CLS"])
    assert r["items"][0]["quality"] == "недостоверное (код 64)"
    assert r["items"][1]["state_label"].startswith("закрыт")                   # у _CLS 1 — «закрыт»

async def test_tag_period_answer_says_what_was_computed(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    it = r["items"][0]
    assert r["status"] == "ok" and it["range"] == pytest.approx(5.0) == it["max"] - it["min"]
    assert (r["source"], r["avg_kind"]["code"], r["std_kind"]["code"]) == (SOURCE_SUMMARY, AVG_KIND, STD_KIND)
    assert r["period"] == {"start": "2026-09-30T22:00:00+05:00", "end": "2026-10-01T06:00:00+05:00",
                           "tz": "UTC+05:00", "preset": "prev_shift", "shift_grid_assumed": True,
                           "clamped": False, "open": False}
    assert SHIFT_NOTE in r["notes"] and r["cost_tag_days"] == pytest.approx(0.333, abs=1e-3)
    assert ("last" in it) is HAS_LAST and (NO_LAST_NOTE in r["notes"]) is (not HAS_LAST)

async def test_tag_period_distinguishes_missing_unlisted_nodata_discrete(svc):  # Review Focus 2
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV", "16FAKE_009_PV",
                                                  "20FAKE_404_PV", "25FAKE_007_CLS"], period="prev_shift")
    assert [i["status"] for i in r["items"]] == ["ok", "no_data", "unlisted", "unknown", "discrete"]
    assert len({i["text"] for i in r["items"][1:]}) == 4 and r["status"] == "partial"

async def test_refusals_send_no_sql(svc):
    s, fake = svc
    budget = await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"],
                                start="2026-09-01 00:00", end="2026-09-14 00:00")
    bad = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], start="2026-10-01 10:00", end="2026-10-01 09:00")
    none = await s.tag_now("student-01", "http", ["16FAKE_009_PV"])
    assert (budget["status"], bad["status"], none["status"]) == ("budget", "refused", "refused")
    assert fake.calls == []

async def test_rate_limit_per_caller(svc):
    s, _ = svc
    for _ in range(60):
        assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "ok"
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "rate"
    assert (await s.tag_now("student-02", "http", ["20FAKE_001_PV"]))["status"] == "ok"

async def test_identical_calls_share_one_query(svc):
    s, fake = svc
    fake.delay_s = 0.1
    rs = await asyncio.gather(*(s.tag_period(f"student-{n:02d}", "http", ["20FAKE_001_PV"], period="prev_shift")
                                for n in range(1, 11)))
    assert sum("AnalogSummaryHistory" in sql for call in fake.calls for sql in call) == 1
    assert sorted(r["cache"] for r in rs) == ["miss"] + ["shared"] * 9
    again = await s.tag_period("student-11", "http", ["20FAKE_001_PV"], period="prev_shift")
    assert again["cache"] == "hit"

async def test_catalog_not_loaded_is_unavailable(tmp_path):
    s, fake = make_service(tmp_path)
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert (r["status"], r["message"], fake.calls) == ("unavailable", MESSAGES["catalog"], [])

async def test_historian_down_is_named(svc):
    s, fake = svc
    fake.fail = HistorianError("connect", "ConnectionRefusedError")
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert (r["status"], r["message"]) == ("unavailable", "нет связи с историаном")
    assert s.events.recent()[0].outcome == "unavailable"

async def test_event_per_call_including_refusals(svc):
    s, _ = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    await s.tag_now("student-01", "http", ["16FAKE_009_PV"])
    await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="last_30d")      # 30 × 1 — в пределах
    await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"], period="last_30d")
    ev = s.events.recent()
    assert [e.outcome for e in ev] == ["budget", "ok", "refused", "ok"]
    assert all(e.caller == "student-01" and e.channel == "http" and e.agent is None for e in ev)
    assert ev[1].tags == ("20FAKE_001_PV",) and ev[1].start is not None and ev[1].cost == pytest.approx(30.0)

async def test_call_log_line_without_values(svc, capfd):
    s, _ = svc
    await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    err = capfd.readouterr().err
    assert "tool=tag_period" in err and "cache=miss" in err and "rows=1" in err and "cost=0.333" in err
    assert "12.4" not in err and "12.5" not in err

async def test_freshness_poll_uses_freshest_whitelisted(svc):
    s, fake = svc
    await s.poll_freshness()
    assert set(sent_names(fake)) == {"20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP"}
    j = s.freshness.to_json(s.monotonic())
    assert (j["age_s"], j["tags"], j["error"]) == (10.0, 3, None)

async def test_freshness_poll_errors(tmp_path):
    s, fake = make_service(tmp_path)
    await s.poll_freshness()
    assert s.freshness.to_json(0.0)["error"] == "catalog"                      # загрузки ещё не было
    fake.fail = HistorianError("connect", "x")
    assert await s.refresh_catalog() is False
    await s.poll_freshness()
    assert s.freshness.to_json(0.0)["error_text"] == "нет связи с историаном"   # причина, а не «каталог»
    fake.fail = None
    assert await s.refresh_catalog() is True
    fake.fail = HistorianError("timeout", "x")
    await s.poll_freshness()
    assert s.freshness.to_json(0.0)["error"] == "timeout"

def test_health(tmp_path):
    assert make_service(tmp_path, whitelist=frozenset())[0].health() == (False, "белый список пуст или не найден")
    assert make_service(tmp_path)[0].health()[0] is True

# core/tests/test_http.py
def test_data_routes_require_token(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}).status_code == 401
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]},
                      headers={"Authorization": "Bearer nope"}).status_code == 401

def test_tag_now_over_http_and_validation(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        wait_until(lambda: role.service.catalog.loaded)
        r = c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}, headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert c.post("/api/data/tag_now", json={"tags": []}, headers=auth).status_code == 422
        assert c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers=auth).status_code == 422
        assert c.post("/api/data/sql", json={}, headers=auth).status_code == 404
        assert role.service.events.recent()[0].caller == "student-01"

def test_health_historian_is_public(tmp_path):
    app, role = make_app(tmp_path)
    with TestClient(app) as c:
        wait_until(lambda: role.service.freshness.to_json(0.0)["checked_at"] is not None)
        j = c.get("/health/historian").json()
        assert set(j) == {"checked_at", "age_s", "skew_s", "error", "error_text", "tags"}

def test_healthz_503_when_events_unwritable(tmp_path):
    app, role = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        make_readonly(tmp_path)                            # каталог и файл базы — только чтение
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["roles"]["data"] == "журнал событий не пишется"
```

`wait_until(pred, timeout=5)` и `make_readonly(path)` — в `helpers.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_service.py tests/test_http.py`
Expected: FAIL — нет `service.py`

- [ ] **Step 3: Implement `service.py`, `http_api.py`, `DataRole`, `build_roles` по интерфейсам выше**

- [ ] **Step 4: Run the whole core suite**

Run: `cd core && CORE_PYTEST && (cd verify && uv run --python 3.12 --with pytest pytest -q)`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Служба данных: tag_now, tag_period, catalog_search по HTTP — отказы без SQL, событие на каждый вызов"
```

---

### Task 7: Сторож — вид `historian`

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/pcbk_watchdog/main.py`,
  `watchdog/components.json`, `watchdog/tests/conftest.py` (фикстура `fake_core`),
  `watchdog/tests/helpers.py` (`fresh_now`, `NO_DOCKER`)
- Test: `watchdog/tests/test_checks.py` (+4 теста), `watchdog/tests/test_main.py`
  (`test_components_file_d2` → `test_components_file_d3`, +2 теста)

**Interfaces:**
- Consumes: `Check`, `check_http`, `NET_CHECK_TIMEOUT_S`, `KINDS`, `Settings`,
  `run_checks`, `load_components` — Д1 и Д2; ответ `/health/historian` —
  задача 6.
- Produces:
  - `check_http(component, title, url, timeout=3.0, ok_detail: str = "отвечает") -> Check` —
    при 2xx деталь равна `ok_detail`; вид `http` берёт необязательное поле
    `ok_detail` из `components.json`
  - `check_historian(component: str, title: str, url: str, now: datetime, warn_s: int, fail_s: int, stale_s: int, timeout: float = 3.0) -> Check` —
    таблица ниже, первая подходящая сверху
  - `KINDS["historian"] = ("url",)` — сетевой вид, срок такта на него действует
  - `Settings`: `HIST_WARN_S: int = 300`, `HIST_FAIL_S: int = 900` (по задаче 0),
    `HIST_STALE_S: int = 120`; `0 ≤ HIST_WARN_S ≤ HIST_FAIL_S`,
    `HIST_STALE_S > 0`, иначе `ValueError`. Ноль — ручка учения: «любой
    возраст — предупреждение/сбой», историан не трогается
  - `components.json`: `core` →
    `{"id": "core", "title": "Служба данных", "kind": "http", "url": "http://pcbk-core:8000/healthz", "ok_detail": "жива"}`;
    `historian` →
    `{"id": "historian", "title": "Историан БДРВ", "kind": "historian", "url": "http://pcbk-core:8000/health/historian"}`;
    места, порядок и `llm` (`absent`) — как оставил Д2
  - фикстура `fake_core`: `http.server` на свободном порту, `/health/historian`
    отдаёт JSON, заданный через `set(obj)`, или сырой текст через `set_raw(text)`;
    `url`

| Ответ службы | Итог |
|---|---|
| нет соединения, таймаут, HTTP ≠ 200, не JSON | `unknown` «служба данных не отвечает — свежесть неизвестна» |
| `checked_at` равен `null` | `unknown` «служба ещё не опрашивала историан» |
| `checked_at` старше `stale_s` | `unknown` «служба давно не опрашивала историан» |
| `error` задан | `fail` — `error_text` (первые 80 знаков) |
| `age_s ≥ fail_s` | `fail` «последняя метка старше {fail_s} с» |
| `age_s ≥ warn_s` | `warn` «последняя метка старше {warn_s} с» |
| иначе | `ok` «последняя метка {round(age_s)} с назад» |

Тексты `warn` и `fail` постоянные: журнал сторожа пишет одно событие на
переход (правило Д1).

- [ ] **Step 1: Write the failing tests**

```python
# watchdog/tests/test_checks.py
def hist(fake_core, obj, warn=300, fail=900, stale=120):
    fake_core.set(obj)
    return check_historian("historian", "Историан БДРВ", fake_core.url, T0, warn, fail, stale, timeout=0.5)

def fresh(age, **kw):
    return {"checked_at": (T0 - timedelta(seconds=10)).isoformat(), "age_s": age, "skew_s": 0.0,
            "error": None, "error_text": None, "tags": 8, **kw}

def test_historian_verdicts(fake_core):
    assert (hist(fake_core, fresh(42.4)).state, hist(fake_core, fresh(42.4)).detail) == ("ok", "последняя метка 42 с назад")
    assert (hist(fake_core, fresh(300)).state, hist(fake_core, fresh(300)).detail) == ("warn", "последняя метка старше 300 с")
    assert (hist(fake_core, fresh(901)).state, hist(fake_core, fresh(901)).detail) == ("fail", "последняя метка старше 900 с")
    err = fresh(None, error="connect", error_text="нет связи с историаном")
    assert (hist(fake_core, err).state, hist(fake_core, err).detail) == ("fail", "нет связи с историаном")

def test_historian_stale_or_never_polled_is_unknown(fake_core):
    old = fresh(1.0, checked_at=(T0 - timedelta(seconds=121)).isoformat())
    assert hist(fake_core, old).detail == "служба давно не опрашивала историан"
    assert hist(fake_core, fresh(None, checked_at=None)).detail == "служба ещё не опрашивала историан"

def test_historian_core_down_or_garbage_is_unknown(fake_core):
    r = check_historian("historian", "Историан БДРВ", "http://127.0.0.1:9/health/historian", T0, 300, 900, 120, 0.5)
    assert (r.state, r.detail) == ("unknown", "служба данных не отвечает — свежесть неизвестна")
    fake_core.set_raw("не json")
    assert check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, 0.5).state == "unknown"

def test_check_http_ok_detail(fake_core):
    assert check_http("core", "Служба данных", fake_core.base + "/healthz", ok_detail="жива").detail == "жива"

# watchdog/tests/test_main.py
def test_components_file_d3():
    comps = {c["id"]: c for c in load_components("components.json")}
    assert (comps["core"]["kind"], comps["core"]["title"], comps["core"]["ok_detail"]) == ("http", "Служба данных", "жива")
    assert comps["core"]["url"] == "http://pcbk-core:8000/healthz"
    assert (comps["historian"]["kind"], comps["historian"]["url"]) == ("historian", "http://pcbk-core:8000/health/historian")
    assert {i for i, c in comps.items() if c["kind"] == "absent"} == {"llm"}

def test_hist_threshold_knob(fake_core):                  # ручка учения «метка устарела»
    comps = [{"id": "historian", "title": "Историан БДРВ", "kind": "historian", "url": fake_core.url}]
    fake_core.set(fresh_now(age=5.0))                     # checked_at = сейчас
    warn = run_checks(comps, NO_DOCKER, replace(SETTINGS, HIST_WARN_S=0, HIST_FAIL_S=100000), now_utc())
    fail = run_checks(comps, NO_DOCKER, replace(SETTINGS, HIST_WARN_S=0, HIST_FAIL_S=0), now_utc())
    assert (warn[0].state, fail[0].state) == ("warn", "fail")
    with pytest.raises(ValueError):
        replace(SETTINGS, HIST_WARN_S=10, HIST_FAIL_S=5)
    assert Settings.from_env({"HIST_WARN_S": "0", "HIST_FAIL_S": "0"}).HIST_FAIL_S == 0
```

`fresh_now` и `NO_DOCKER` (`DockerReader("http://127.0.0.1:9", 0.5)`) —
в `watchdog/tests/helpers.py`; у `fake_core` есть ещё `base` — адрес без пути,
`/healthz` отвечает 200.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — нет `check_historian`, `components.json` ещё с `absent`

- [ ] **Step 3: Implement `check_historian`, `ok_detail`, вид `historian`, `HIST_*`, `components.json` по интерфейсам и таблице выше**

`check_historian` — `http.client`, как `check_http`: без перенаправлений и
без прокси из окружения. `checked_at` сравнивается с `now` с учётом пояса.

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: вид historian — свежесть от службы, пороги у сторожа, служба данных под наблюдением"
```

---

### Task 8: Серверный слой в компоновке

**Files:**
- Modify: `compose.yaml` (служба `core`, сеть `pcbk-egress`, секреты
  `bdrv-env` и `core-tokens`, том `pcbk-core-data`; у сторожа
  `image: pcbk-reserve/watchdog:d3` и `HIST_WARN_S`, `HIST_FAIL_S`,
  `HIST_STALE_S` через `${VAR:-умолчание}`), `compose.test.yaml`,
  `deploy/env.example`, `.gitignore`, `tests/integration/conftest.py`,
  `tests/integration/test_edge.py`, `tests/integration/test_socket_proxy.py`
- Test: `tests/integration/test_core.py`

**Interfaces:**
- Consumes: образ `pcbk-reserve/core:d3` — задачи 1–6; сторож — задача 7;
  фикстура `stack` и её методы — Д1 и Д2 (`https`, `wait_status`, `inspect`,
  `network`, `containers`, `env_names`, `image_env_names`, `start`, `stop`,
  `probe`, `prod_config`).
- Produces:

```yaml
services:
  core:
    build: ./core
    image: pcbk-reserve/core:d3
    container_name: pcbk-core
    user: "10003:10003"
    read_only: true
    tmpfs: ["/tmp"]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 256m
    pids_limit: 128
    restart: unless-stopped
    environment: {CORE_PORT: "8000", FRESH_POLL_S: "${FRESH_POLL_S:-30}"}   # только несекретное
    volumes:
      - pcbk-core-data:/var/lib/pcbk-core
      - {type: bind, source: "${DATA_DIR}/whitelist.txt", target: /app/data/whitelist.txt,
         read_only: true, bind: {create_host_path: false}}
    secrets:
      - {source: bdrv-env, target: bdrv.env}
      - {source: core-tokens, target: core-tokens}
    networks:
      pcbk-front: {}
      pcbk-egress: {ipv4_address: 172.31.250.82}
      pcbk-stu-01: {ipv4_address: "${STU_NET:-172.31}.1.2"}
      # … pcbk-stu-02 … pcbk-stu-10 — так же, .N.2

networks:
  pcbk-egress:
    name: pcbk-egress
    ipam: {config: [{subnet: 172.31.250.80/28, ip_range: 172.31.250.88/29}]}

volumes:
  pcbk-core-data: {name: pcbk-core-data}

secrets:
  bdrv-env: {file: "${SECRETS_DIR}/bdrv.env"}
  core-tokens: {file: "${SECRETS_DIR}/core-tokens"}
```

  - у `pcbk-egress` нет `internal` и `driver_opts`: маскарад по умолчанию
    включён, шлюз обычный
  - `bind.create_host_path: false`: без файла списка контейнер не создаётся, и
    Docker не заводит на его месте каталог от root
  - `compose.test.yaml`: `core` — `environment: {FRESH_POLL_S: "5"}`
  - `deploy/env.example`: `DATA_DIR=`, `HIST_WARN_S=`, `HIST_FAIL_S=` —
    без значений
  - `.gitignore`: `core-tokens`, `data-token.*`, `whitelist.txt`,
    `whitelist.extra`
  - фикстура `stack`: в тестовом `SECRETS_DIR` пишет `bdrv.env`
    (`BDRV_HOST=192.0.2.10`, `BDRV_PORT=1433`, `BDRV_USER=test`,
    `BDRV_PW=test-not-a-secret` — адрес TEST-NET, недостижимый) и
    `core-tokens` с токеном `student-01`; в тестовом `DATA_DIR` —
    `whitelist.txt` из `20FAKE_001_PV` (файлы `0444`, каталоги `0700`);
    `DATA_DIR` — в `test.env`
  - новые методы `stack`:
    `http_host(method: str, url: str, token: str | None = None, body: dict | None = None) -> tuple[int, str]`
    (с хоста теста к адресу серверного слоя в `pcbk-egress`, `urllib`, без
    прокси); `logs(name: str) -> str`; `core_token: str`; `stu_net: str`.
    `start`/`stop` для `pcbk-core`: готовность — `GET http://172.31.250.82:8000/healthz` → 200
  - `test_edge.py`: `DECLARED_ENV["pcbk-core"] = {"CORE_PORT", "FRESH_POLL_S"}`;
    у сторожа + `HIST_WARN_S`, `HIST_FAIL_S`, `HIST_STALE_S`; в
    `test_env_holds_only_known_names` — `("pcbk-core", "pcbk-reserve/core:d3")`
    и сторож `:d3`
  - `test_socket_proxy.py`, `test_status_json_overall_ok`: `absent` — только
    `llm`; `core` — `ok`; `historian` в тесте — `fail`, поэтому ожидание
    итога `ok` заменяется на «все строки, кроме `historian`, — `ok`»

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_core.py
CORE = "http://172.31.250.82:8000"

def row(data, cid):
    return next((c["state"], c["detail"]) for c in data["checks"] if c["component"] == cid)

def test_status_shows_core_alive_and_historian_down(stack):
    data = stack.wait_status(lambda d: row(d, "historian")[0] == "fail", timeout=90)
    assert row(data, "core") == ("ok", "жива")
    assert row(data, "historian") == ("fail", "нет связи с историаном")

def test_core_answers_on_egress_address(stack):
    code, body = stack.http_host("GET", CORE + "/healthz")
    assert code == 200 and json.loads(body)["ok"] is True
    code, body = stack.http_host("POST", CORE + "/api/data/tag_now", token=stack.core_token,
                                 body={"tags": ["20FAKE_001_PV"]})
    assert code == 200 and json.loads(body)["status"] == "unavailable"          # историана нет — словами
    assert stack.http_host("POST", CORE + "/api/data/tag_now", body={"tags": ["20FAKE_001_PV"]})[0] == 401

def test_edge_does_not_expose_core(stack):
    for path in ("/api/data/tag_now", "/mcp", "/health/historian", "/healthz"):
        assert stack.https(path)[0] == 404

def test_egress_network_only_core(stack):
    n = stack.network("pcbk-egress")
    assert n["Internal"] is False
    assert n["Options"].get("com.docker.network.bridge.enable_ip_masquerade", "true") == "true"
    assert [c["Name"] for c in n["Containers"].values()] == ["pcbk-core"]
    assert [(c["Subnet"], c["IPRange"]) for c in n["IPAM"]["Config"]] == [("172.31.250.80/28", "172.31.250.88/29")]

def test_core_networks_and_addresses(stack):
    nets = stack.inspect("pcbk-core")["NetworkSettings"]["Networks"]
    assert set(nets) == {"pcbk-front", "pcbk-egress"} | {f"pcbk-stu-{n:02d}" for n in range(1, 11)}
    assert nets["pcbk-egress"]["IPAddress"] == "172.31.250.82"
    assert [nets[f"pcbk-stu-{n:02d}"]["IPAddress"] for n in range(1, 11)] == \
           [f"{stack.stu_net}.{n}.2" for n in range(1, 11)]

def test_core_secrets_are_readonly_files(stack):
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    for dest in ("/run/secrets/bdrv.env", "/run/secrets/core-tokens", "/app/data/whitelist.txt"):
        assert (mounts[dest]["Type"], mounts[dest]["RW"]) == ("bind", False), dest
    prod = stack.prod_config()["services"]["core"]
    assert not {"env_file"} & set(prod) and "BDRV" not in json.dumps(prod.get("environment", {}))
    hc = stack.inspect("pcbk-core")["HostConfig"]
    assert (hc["ReadonlyRootfs"], hc["CapDrop"], hc["Privileged"]) == (True, ["ALL"], False)

def test_core_logs_egress_guard(stack):
    assert "охрана выхода: разрешено 1 направление" in stack.logs("pcbk-core")

def test_workplace_reaches_core_not_beyond(stack):
    assert stack.probe("pcbk-stu-01", f"{stack.stu_net}.1.2", 8000) == 1        # положительный контроль
    for addr, port in (("192.0.2.10", 1433), ("1.1.1.1", 443), ("172.31.250.81", 22),
                       ("172.31.250.82", 8000)):
        assert stack.probe("pcbk-stu-01", addr, port) == 0, (addr, port)

def test_stop_core_turns_core_red_historian_unknown(stack):
    stack.stop("pcbk-core")
    try:
        data = stack.wait_status(lambda d: row(d, "core")[0] == "fail", timeout=40)
        assert row(data, "historian") == ("unknown", "служба данных не отвечает — свежесть неизвестна")
    finally:
        stack.start("pcbk-core")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_core.py`
Expected: FAIL — нет службы `core`

- [ ] **Step 3: Implement службу, сеть, секреты, тестовые данные и методы `stack` по интерфейсам выше**

- [ ] **Step 4: Run the whole local suite**

Run: `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/) && uv run --python 3.12 --with pytest pytest -q tests/integration && docker network ls --filter name=pcbk- -q | wc -l`
Expected: PASS; после прогона сетей `pcbk-` — 0

- [ ] **Step 5: Commit**

```bash
git add compose.yaml compose.test.yaml deploy/env.example .gitignore tests/integration/
git commit -m "Серверный слой в компоновке: сеть выхода только у него, секреты файлами, адрес .2 в сетях мест"
```

---

### Task 9: MCP Streamable HTTP

**Files:**
- Create: `core/pcbk_core/data/mcp_server.py`
- Modify: `core/pcbk_core/data/__init__.py` (`DataRole.mounts`, `lifespan`),
  `core/tests/conftest.py` (`core_server`), `core/tests/helpers.py` (`mcp_session`)
- Test: `core/tests/test_mcp.py`

**Interfaces:**
- Consumes: `DataService`, `CatalogSearchArgs`, `TagNowArgs`, `TagPeriodArgs`,
  `TokenTable` — задачи 1 и 6; `create_app` ставит монтирования последними —
  задача 1.
- Produces:
  - `build_mcp(service: DataService, tokens: TokenTable) -> FastMCP` —
    `FastMCP("pcbk-data", stateless_http=True, json_response=True, streamable_http_path="/mcp", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`.
    Защита от DNS rebinding выключена: имя хоста разное (`core:8000`,
    `${STU_NET}.N.2:8000`, туннель), а защищает токен на каждом запросе. Три
    инструмента с теми же именами, полями и ограничениями, что модели HTTP;
    аннотации `readOnlyHint=True`, `openWorldHint=False`; ответ — словарь
    `DataService`; канал `"mcp"`; вызывающий — по заголовку `Authorization`
    из `ctx.request_context.request` через `tokens.caller`. Контекстная
    переменная из промежуточного звена сюда не годится: в режиме stateless SDK
    запускает обработчик в группе задач жизненного цикла, контекст запроса туда
    не доходит
  - `mcp_auth(app: ASGIApp, tokens: TokenTable) -> ASGIApp` — чистое ASGI
    (не `BaseHTTPMiddleware`: тот ломает потоковые ответы). Для
    `scope["path"]`, начинающегося с `/mcp`, без верного `Bearer` →
    `401 {"detail": "нужен токен"}`
  - `DataRole.mounts()` → `[("/", mcp_auth(mcp.streamable_http_app(), tokens))]`;
    `DataRole.lifespan()` входит ещё и в `mcp.session_manager.run()`
  - `TOOL_DESCRIPTIONS` (их читает модель, текст постоянный):
    - `catalog_search`: «Поиск тегов БДРВ участка по словам в имени и описании. Описания в историане обрезаны на 50 символах: если точного совпадения нет, вернутся похожие — покажите их студенту на выбор, не угадывайте.»
    - `tag_now`: «Текущее значение тегов БДРВ (до 16 за вызов): значение, единица, время с поясом, возраст и качество. Имена берите точно из catalog_search.»
    - `tag_period`: «Сводка по тегам БДРВ за период (до 16 тегов, окно до 31 суток, бюджет 24 тего-суток): среднее, минимум, максимум, размах, σ — посчитаны историаном по сырым данным. Период — готовый (period) или начало и конец (start, end, ISO 8601). В ответе сказано, какое это среднее, в каком поясе время и что смены 06/14/22 — допущение.»
  - фикстура `core_server`: `create_app(SETTINGS, [DataRole(… FakeHistorian …)])`
    под `uvicorn.Server` в потоке на `127.0.0.1:0`; ждёт `/healthz` → 200 и
    загрузки каталога; отдаёт `url`, `role`
  - `mcp_session(url: str, token: str)` — асинхронный контекст:
    `streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"})` →
    `ClientSession` → `initialize()`
  - имена модулей SDK (`mcp.server.fastmcp.FastMCP`,
    `mcp.server.transport_security.TransportSecuritySettings`,
    `mcp.client.streamable_http.streamablehttp_client`) — по README SDK
    закреплённой версии; если они там другие, меняется импорт, а не тесты

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_mcp.py
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}}}
ACCEPT = "application/json, text/event-stream"

def post(url, body, **headers):
    req = urllib.request.Request(url, json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": ACCEPT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

@pytest.mark.anyio
async def test_mcp_lists_three_readonly_tools(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"catalog_search", "tag_now", "tag_period"}
    assert all(t.annotations.readOnlyHint for t in tools.values())
    assert tools["tag_now"].inputSchema["properties"]["tags"]["maxItems"] == 16
    assert set(tools["tag_period"].inputSchema["properties"]) == set(TagPeriodArgs.model_fields)
    assert tools["tag_now"].description == TOOL_DESCRIPTIONS["tag_now"]

@pytest.mark.anyio
async def test_mcp_and_http_give_same_answer(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        sc = (await s.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})).structuredContent
    sc = sc.get("result", sc)
    code, body = http_post(core_server.url + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, TOKEN)
    drop = lambda d: {k: v for k, v in d.items() if k != "cache"}
    assert code == 200 and drop(sc) == drop(json.loads(body))

def test_mcp_requires_token(core_server):
    assert post(core_server.url + "/mcp", INIT) == 401
    assert post(core_server.url + "/mcp", INIT, Authorization="Bearer nope") == 401

def test_mcp_accepts_core_host_header(core_server):                              # Review Focus 5
    for host in ("core:8000", "172.31.1.2:8000"):
        assert post(core_server.url + "/mcp", INIT, Authorization=f"Bearer {TOKEN}", Host=host) == 200

@pytest.mark.anyio
async def test_mcp_event_records_caller_and_channel(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        await s.call_tool("catalog_search", {"query": "расход"})
    e = core_server.role.service.events.recent()[0]
    assert (e.caller, e.channel, e.tool, e.agent) == ("student-01", "mcp", "catalog_search", None)
```

`http_post(url, body, token) -> tuple[int, str]` — в `helpers.py`.
`urllib` не ходит по перенаправлениям для POST, поэтому 307 на `/mcp/` даст
не 200.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_mcp.py`
Expected: FAIL — нет `mcp_server.py`

- [ ] **Step 3: Implement `build_mcp`, `mcp_auth`, монтирование и жизненный цикл по интерфейсам выше**

- [ ] **Step 4: Run the whole core suite and the integration suite**

Run: `(cd core && CORE_PYTEST) && uv run --python 3.12 --with pytest pytest -q tests/integration/test_core.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Служба данных по MCP Streamable HTTP: те же три инструмента, токен на каждый запрос"
```

---

### Task 10: Выкладка и белый список на сервере

Шаги — команды и вывод, который значит «прошло». Итоги — в
`docs/checks/D3.md`, только вердикты и счётчики. `sudo` не нужен. **Шаг 4
читает каталог историана — при владельце.**

**Files:**
- Modify: `deploy/README.md` — раздел «Служба данных»: копия `bdrv.env` из
  Dify (`install -m 0444`, при смене пароля — повторить и перезапустить
  `core`); токены (порождение, не перезаписывать, `ops` — для проверок
  владельца); белый список (правило `d3-1`, `whitelist.extra`, построитель,
  перезапуск `core`); что `pcbk-egress` — единственная сеть с выходом, охрана
  выхода — в процессе, правила `DOCKER-USER` — предложение на Д12; откат
- Modify: `docs/checks/D3.md`

- [ ] **Step 1: Предпроверки**

На сервере (`cd /opt/pcbk-reserve`):
`docker compose ps --format '{{.Name}} {{.State}}'; stat -c '%a' secrets; ls data 2>/dev/null | wc -l; docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -c '^172\.31\.250\.80/28$'; grep MemAvailable /proc/meminfo; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d3-before.txt`
Expected: службы Д1 и Д2 на месте; `secrets` — `700`; подсеть свободна (0);
`MemAvailable` записать числом.

- [ ] **Step 2: Секреты и токены (на сервере, содержимое на экран не выводится)**

```bash
cd /opt/pcbk-reserve && umask 077
[ -d data ] || install -d -m 0700 data
install -m 0444 /opt/dify/scripts/.bdrv.env secrets/bdrv.env
if [ ! -e secrets/core-tokens ]; then
  : > secrets/core-tokens.new
  for id in ops $(printf 'student-%02d ' $(seq 1 10)); do
    t=$(openssl rand -hex 32); printf '%s' "$t" > "secrets/data-token.$id"
    printf '%s %s\n' "$id" "$(printf '%s' "$t" | sha256sum | cut -d' ' -f1)" >> secrets/core-tokens.new
  done
  chmod 0400 secrets/data-token.*; chmod 0444 secrets/core-tokens.new; mv secrets/core-tokens.new secrets/core-tokens
fi
stat -c '%a %n' data secrets/bdrv.env secrets/core-tokens; wc -l < secrets/core-tokens
```
Expected: `700 data`, `444` у двух файлов, 11 строк токенов. Токен `ops` — в
рабочий каталог задания без вывода на экран:
`$SSH 'cat /opt/pcbk-reserve/secrets/data-token.ops' > "$JOB/ops.token"`, затем
`printf 'Authorization: Bearer %s\n' "$(cat "$JOB/ops.token")" > "$JOB/ops.hdr"; chmod 600 "$JOB"/ops.*`.
Токены мест Д4 смонтирует местам (тогда они станут `0444`).

- [ ] **Step 3: Образы**

Локально: `docker compose build core watchdog`;
`docker save pcbk-reserve/core:d3 pcbk-reserve/watchdog:d3 | gzip | $SSH 'gunzip | docker load'`.
Сверка на обеих сторонах: `docker image inspect -f '{{json .RootFS}}'`.
Expected: `RootFS` у обоих образов совпал; `:d2` сторожа на сервере остался.

- [ ] **Step 4: Белый список (при владельце)**

Локально из прежнего списка `pcbk-ai-lab` — имена не на `20`…`25`:
`python3 -c 'import json,re,sys; names=sorted({r["tag"] for r in json.load(open(sys.argv[1])) if not re.match(r"2[0-5]", r["tag"])}); open(sys.argv[2],"w").write("\n".join(names)+"\n"); print(len(names))' <путь к pcbk/bdrv/whitelist.json> "$JOB/whitelist.extra"`
(если прежний файл устроен иначе — поле имени взять из 05 §3.6 п. 9), затем
`scp "$JOB/whitelist.extra" …:/opt/pcbk-reserve/data/whitelist.extra`.
На сервере, тем же сетевым путём, что проба в задаче 0 (`bridge` или `host`):
`docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro -v /opt/pcbk-reserve/data:/data pcbk-reserve/core:d3 python -m pcbk_core.data.build_whitelist --out /data/whitelist.txt --extra /data/whitelist.extra`.
Expected: счётчики напечатаны; `stat -c '%a' data/whitelist.txt` → `444`.
Сверка с прежним списком — только числа: `whitelist.txt` едет в `$JOB`
(`scp`), затем `comm -12`, `-23`, `-13` отсортированных списков → «общих N,
только в прежнем M, только в новом K». Если «только в прежнем» больше 5 % —
записать и отдельно спросить владельца: правило уже прежнего.

- [ ] **Step 5: Поднять серверный слой**

`.env`: `DATA_DIR=/opt/pcbk-reserve/data`, `HIST_WARN_S`, `HIST_FAIL_S` — по
решениям задачи 0. `cp -p compose.yaml compose.yaml.d2` на сервере; локально
`rsync -a compose.yaml …:/opt/pcbk-reserve/` (без `--delete`). На сервере:
`docker compose up -d --no-build core`, затем `docker compose up -d --no-build watchdog`.
Expected — не позже 2 минут:
- `docker inspect -f '{{.State.Health.Status}}' pcbk-core` → `healthy`;
- `curl -sk https://127.0.0.1:8443/status.json | python3 -c 'import json,sys; d={c["component"]:(c["state"],c["detail"]) for c in json.load(sys.stdin)["checks"]}; print(d["core"], d["historian"])'` →
  `('ok', 'жива') ('ok', 'последняя метка N с назад')`;
- `docker logs pcbk-core 2>&1 | grep -c 'охрана выхода: разрешено 1 направление'` → `1`;
- строка «каталог: …» в журнале `core` — в журнал Д3 только числа;
- `docker stats --no-stream --format '{{.MemUsage}}' pcbk-core` — меньше 70 %
  от 256 МиБ, иначе поднять `mem_limit` одним коммитом и записать;
- у контейнеров Dify и мест время работы продолжает `~/pcbk-d3-before.txt`.

Откат: `docker compose rm -sf core`; `docker network rm pcbk-egress`; вернуть
`compose.yaml.d2` и `docker compose up -d --no-build watchdog` (образ `:d2`).
Том `pcbk-core-data` (журнал событий) при откате не удаляется.

- [ ] **Step 6: Commit** (после проверки на секреты)

```bash
git add deploy/README.md docs/checks/D3.md
git commit -m "Выкладка Д3: служба данных на сервере, белый список по правилу d3-1, сторож видит службу и историан"
```

---

### Task 11: Живые проверки, учения, сверка

Итог каждого шага — вердиктом с пометкой [П] в `docs/checks/D3.md`, без имён
тегов, значений и адресов. Снимки — через `$SSH -N -L 18443:127.0.0.1:8443` и
`google-chrome --headless=new --ignore-certificate-errors --virtual-time-budget=8000 --window-size=1200,2000 --screenshot=docs/checks/D3/<имя>.png https://127.0.0.1:18443/status`.
**Шаги 4–6 читают производственные данные — при владельце.**

- [ ] **Step 1: Исправное состояние**

Expected: «Служба данных — норма — жива», «Историан БДРВ — норма — последняя
метка N с назад». Снимок `01-norm.png`.

- [ ] **Step 2: Учение «служба остановлена»**

`docker stop pcbk-core`.
Expected: не позже 20 с «Служба данных — сбой — не отвечает», «Историан БДРВ —
неизвестно — служба данных не отвечает — свежесть неизвестна». Снимок
`02-core-stopped.png`. Затем `docker start pcbk-core` → не позже 90 с обе
строки в норме, в журнале сторожа — сбой и восстановление. Снимок
`03-core-back.png`.

- [ ] **Step 3: Учение «метка историана устарела» — историан не трогается**

`HIST_WARN_S=0 docker compose up -d --no-build watchdog` → «Историан БДРВ —
внимание — последняя метка старше 0 с», снимок `04-historian-warn.png`.
`HIST_WARN_S=0 HIST_FAIL_S=0 docker compose up -d --no-build watchdog` →
«сбой — последняя метка старше 0 с», снимок `05-historian-fail.png`.
`docker compose up -d --no-build watchdog` → норма, снимок `06-recovered.png`.
Expected: три снимка; в журнале сторожа после каждого перезапуска — «сторож
запущен» и переходы строки историана. Если свежесть на сервере — жёлтая
(возраст ≥ `HIST_WARN_S` без учения), снимок — вердиктом, пороги задачи 0
пересмотреть.

- [ ] **Step 4: Ответ службы за смену по туннелю (при владельце)**

`$SSH -N -L 18000:172.31.250.82:8000` в фоне. Тег — тот, что назовёт
владелец. Если не назовёт — первый живой аналоговый из
`catalog_search` по слову «расход», у которого в ответе `tag_now` качество
«хорошее». Имя — только в `$JOB/check-tag`. Запрос — в `$JOB/req.json`:
`{"tags": ["<тег>"], "period": "prev_shift"}`.
`curl -s -H @"$JOB/ops.hdr" -H 'Content-Type: application/json' --data @"$JOB/req.json" http://127.0.0.1:18000/api/data/tag_period | tee "$JOB/answer.json"`
Expected: владелец видит ответ. В журнал Д3 вердикты: `status: ok`; период —
предыдущая смена, 8 ч, с поясом; `source`, `avg_kind`, `std_kind`,
`SHIFT_NOTE` на месте; `duration_ms` вызова из журнала событий — не больше
5000 (критерий p95 ≤ 5 с, 05 §4.2). Повторный запрос даёт `cache: hit`.

- [ ] **Step 5: Независимая сверка с сырым `Delta` (при владельце)**

`$SSH 'docker run --rm -i --network bridge --env-file /opt/dify/scripts/.bdrv.env --read-only --cap-drop ALL --security-opt no-new-privileges:true pcbk-probe/tds:1.17.1 verify' < "$JOB/answer.json" > "$JOB/verify.json"`
(сеть — как в задаче 0). Затем
`grep -E -i -f <шаблоны> "$JOB/verify.json"` — пусто.
Expected: `bounds`, `min`, `max`, `avg`, `std` (и `last`, если есть) —
«сошлось». Если `avg` не сошёлся с заявленным видом, но сошёлся с другим:
`AVG_KIND` правится одним коммитом, служба перевыкладывается, шаги 4–5
повторяются. Если не сошёлся ни с одним — вердикт как есть, `AVG_KIND =
"unverified"`, вопрос в отчёт владельцу. Успехом дня расхождение не
прикрывается.

- [ ] **Step 6: MCP, белый список и журнал событий (при владельце)**

- `initialize`, затем `tools/list` через туннель — `curl` с `-H @"$JOB/ops.hdr"`,
  `-H 'Accept: application/json, text/event-stream'` и телом JSON-RPC →
  `200`, три имени инструментов (если задача 9 ушла в Д4 — «MCP — утром Д4»).
- Все теги в событиях — из белого списка:
  `docker exec pcbk-core python -c "import json,sqlite3; w={l.strip() for l in open('/app/data/whitelist.txt') if l.strip() and not l.startswith('#')}; t=[x for (j,) in sqlite3.connect('/var/lib/pcbk-core/core.db').execute('select tags from agent_events') for x in json.loads(j)]; print(len(t), sum(x not in w for x in t))"` →
  `N 0`. Основа успеха 4 по журналу службы.
- Отказ без SQL: `tag_now` по имени, которого нет в каталоге →
  `status: refused`, в событии `outcome: refused`, `rows: 0`.

- [ ] **Step 7: Сеть выхода — что держит сеть, а что процесс**

- Структура: `docker network inspect pcbk-egress` → в `Containers` только
  `pcbk-core`, `Internal: false`.
- Остаточный риск 1 — факт: одноразовый
  `curlimages/curl:8.16.0 --network container:pcbk-core` способом Д1 до
  `1.1.1.1:443` и до шлюза моста `.81:22` → записать как есть (ожидается 1).
  Выход на уровне сети открыт, держит его процесс (Ruling 2).
- Места (если выложены в Д2) — помощником `probe` из Д2 (задача 5; копия в
  `~/pcbk-d3/`): `probe pcbk-stu-01 $STU.1.2 8000` → **1** (положительный
  контроль); `$BDRV_HOST:1433`, `1.1.1.1:443`, `172.31.250.82:8000` из
  `pcbk-stu-01` → 0.

Expected: как указано; без положительного контроля набор не засчитывается.

- [ ] **Step 8: Commit** (после проверки на секреты)

```bash
git add docs/checks/
git commit -m "Д3: живые проверки — ответ за смену сошёлся с сырым Delta, два учения, сеть выхода"
```

---

### Task 12: Закрытие дня

- [ ] **Step 1:** Правки документов.
  - `docs/DESIGN-platform-2026-09-29.md`, §4: «пароль — в переменных службы»
    → «пароль — файлом `:ro` (§13 п. 1)».
  - Там же — новый §13 п. 7 «Д3»: Ruling 1–5 одной строкой каждый и ссылка на
    `docs/checks/D3.md`.
  - `README.md`, раздел «Состояние»: «Д3 готов», что дальше (Д4), карта
    документов с этим планом.
  - `docs/PLAN-platform-2026-09-29.md`, таблица «что нужно от владельца»,
    строка Д3: «правило по умолчанию `d3-1` действует, ждёт подтверждения».
    В «Отклонениях» — только то, что расходится с проектом: MCP в Д4, если
    ушёл; `last` не считается, если О2 так решил.
- [ ] **Step 2:** Критик (Opus 5.5) по итогу дня. Блокер — любой пункт «Блокер
  дня» дорожной карты:
  - результат не виден;
  - достижим неуспех постановки — запрос к БДРВ мимо белого списка или
    бюджета, пароль БДРВ доступен месту;
  - компонент выложен без наблюдения сторожа;
  - секрет или производственные данные в git.

  Петля — до нуля блокеров, не больше двух раундов; третий — только после
  разговора с владельцем.
- [ ] **Step 3:** Ветку `d3/data-service` — в `main` (fast-forward), тег
  `platform-d3`. Перед пушем:
  - `git log -p main..HEAD -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто;
  - `git ls-files | grep -cE '(core-tokens|data-token|whitelist\.(txt|extra)|\.env)$'` → `0`.

  Затем `git push origin main platform-d3`. Проверка чистым клоном (тег
  `platform-d3`): есть `core/requirements.lock`, `docs/checks/D3/*.png`;
  проходят `(cd core && CORE_PYTEST)` и
  `cd watchdog && uv run --python 3.12 --with pytest pytest -q`.
  Удалить на сервере `~/pcbk-d3-before.txt` и `~/pcbk-d3/`; образ
  `pcbk-probe/tds:1.17.1` оставить для Д9. Локальные `$JOB/answer.json`,
  `$JOB/verify.json`, `$JOB/whitelist.*`, `$JOB/ops.*` удалить.
- [ ] **Step 4:** Владельцу — «Д3 готов», снимки, ответ за смену и итог сверки
  одной строкой. Вопросы — только те, что требуют его решения:
  - правило белого списка `d3-1` и `_LMN` (О9, О10);
  - TLS до историана или принятый открытый текст (О5);
  - границы смен (О6, к технологу);
  - правила `DOCKER-USER` для сети выхода на Д12 (его `sudo`);
  - вид среднего, если сверка его не подтвердила.
