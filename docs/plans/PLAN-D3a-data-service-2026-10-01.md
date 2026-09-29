# Д3а. Служба данных под сторожем — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** на сервере работает серверный слой `pcbk-core` с ролью «служба
данных» в первой половине: соединение с историаном через единственную дверь
`HistorianGate`, каталог тегов в памяти, белый список по правилу `d3-1` и
опрос свежести историана. Страница состояния показывает «Служба данных —
жива» и «Историан БДРВ — последняя метка N с назад» по живому историану.
Учения «служба остановлена» и «метка устарела» показывают сбой. Владелец видит
счётчики нового белого списка против прежнего и подтверждает правило.
Инструментов для агентов в этот день нет — это Д3б.

**Architecture:** `core/` — пакет `pcbk_core`, одно приложение FastAPI на порту
8000. Роли подключаются через протокол `Role`: роутер, здоровье, фоновые
циклы, монтирования. В Д3а роль одна — `data`: каталог, свежесть,
`/health/historian`, `/healthz/data`. Д3б добавит в неё инструменты, Д4 —
роль `llm`, Д5 — `gateway`. К историану ведёт одна дверь — `HistorianGate`:
- у вызовов людей два места, у фоновых опросов — своё третье;
- общий предел частоты считается только для вызовов людей;
- срок вызова 15 с; место берётся, только если после этого остаётся не меньше
  5 с;
- в SQL пускаются только имена из белого списка;
- при отказе учётных данных дверь защёлкивается;
- после обрыва связи 30 с новые попытки сразу получают отказ.

Сторож получает вид `historian`: возраст меток считает служба, пороги держит
сторож. Серверный слой — единственный в сети выхода `pcbk-egress`.

**Tech Stack:** Python 3.12; FastAPI, uvicorn, python-tds 1.17.1,
`mcp==1.30.0` (закреплён сразу, в Д3а — только дымовой тест импорта); pytest
с плагином anyio; сторож — stdlib; Docker Compose; google-chrome (снимки).

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(§1, §4, §8, §9 «Наблюдаемость», §13 п. 1–2); факты и рекомендации —
[`docs/research/05-d3-historian-facts.md`](../research/05-d3-historian-facts.md)
(обязательны); формат, фикстура `stack`, виды `components.json`, сети,
`images.lock` — [`PLAN-D1-foundation-2026-09-29.md`](PLAN-D1-foundation-2026-09-29.md)
и код Д1 (`watchdog/pcbk_watchdog/main.py`, `tests/integration/conftest.py`);
сети мест, секреты Compose, `$SSH`, методы `sh`, `prod_config`, `stu_net`,
`probe` — [`PLAN-D2-workplaces-2026-09-30.md`](PLAN-D2-workplaces-2026-09-30.md)
(редакция 2); дорожная карта — [`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md).
Вторая половина дня Д3 — [`PLAN-D3b-tag-answers-2026-10-02.md`](PLAN-D3b-tag-answers-2026-10-02.md).

**Разрез Д3 на два дня** (по первому раунду критики): Д3а — служба под
сторожем, Д3б — ответ по тегу за смену. В дорожной карте становится 13 дней;
Д4 не меняется. Подключение `pcbk-core` к сетям мест остаётся в Д4, как в
дорожной карте и в Д2. Охрана выхода ставится в Д3б, до того как у службы
появятся вызывающие: в Д3а у неё нет ни токенов, ни инструментов, наружу она
отдаёт только ручки здоровья.

**Предпосылка.** Д2 влит в `main` с тегом `platform-d2` — или его хвосты
закрываются в задаче 0. Ветка дня — `d3a/data-service` от `main` после тега.

**Влезает ли в день — оценка по часам.** Задачи идут последовательно: одна
задача — один исполнитель, затем ревью. В часы каждой задачи с кодом входят
15 минут на ревью и правки. Шкала — плановая, по ставкам Д1 и Д2, без сжатия.

| Задача | Часы | Где |
|---|---|---|
| 0. Хвосты Д2 (утренний слот по плану Д2) | 0,5–1,5 | сервер |
| 1. Утренняя проба историана: запросы 1–6 | 0,75 | `$JOB` и сервер, **нужен владелец** |
| 2. Каркас серверного слоя | 0,75 | локально |
| 3. Шаблоны, соединение и ворота к историану | 1,5 | локально |
| 4. Каталог и белый список | 0,75 | локально |
| 5. Свежесть и роль «данные» | 1 | локально |
| 6. Сторож: вид `historian` | 0,75 | локально |
| 7. Серверный слой в компоновке | 1,5 | локально |
| 8. Выкладка и белый список | 0,75 | сервер, **нужен владелец** |
| 9. Живые проверки и учения | 0,75 | сервер |
| 10. Закрытие дня | 1,25 | — |
| **Критический путь по плановой шкале** | **10,25** при хвостах 0,5 ч (задачи 1–10 — 9,75) | |

**Принятое основание «один день».** Живой темп Д1: план в 9,5 ч по той же
шкале выполнен примерно за 2 ч 20 мин по часам, с субагентами. Это основание
записано, а не взято оправданием: плановая шкала остаётся честной и больше
9,5 ч, а день укладывается в рабочий день по живому темпу. Если темп Д3а
окажется вдвое медленнее Д1 или хуже, в тот же час владельцу уходит строка с
пересчётом, до черты.

**Черта отсечения — конец восьмого часа** плюс превышение хвостов Д2 над
0,5 ч: черта сдвигается ровно на это превышение. По оценке задачи 0–7
кончаются на 7,5 ч.
- **Задача 7 зелёна к черте.** Выкладка (задача 8) и задача 9, шаги 1–4
  (исправное состояние и три учения), идут без переноса. Закрытие сжимается:
  один раунд критика, слияние и тег. Второй раунд, если он нужен, и проверка
  чистым клоном уходят в утренний слот Д3б — это около 0,5 ч, строка в его
  шапке. Шаг 5 задачи 9 (факты о сети выхода) — туда же, в задачу 8 Д3б.
- **Хвосты Д2 больше 1,5 ч** (например, выкладка Д2 не сделана): в тот же час
  владельцу уходит одна строка — Д3а не влезает, разрез растягивается ещё на
  день, планы Д3б и Д4 пересчитываются до их начала.
- **Задача 7 не зелёна к черте.** Выкладки нет. Видимый результат — снимки
  локального стенда (`docker compose -p pcbk-local …`, пометка «не на
  сервере»): «Служба данных — жива», учение «служба остановлена» и строка
  историана «нет связи с историаном». Владельцу в тот же час — строка
  «разрез растягивается», как выше. В Д4 ничего не переносится.
- **Утром нет владельца.** Задачи 2–7 от него не зависят и идут первыми, задача
  1 — когда он появится. Без владельца не делаются задача 1 и шаг 4 задачи 8
  (белый список); выкладка останавливается перед этим шагом. Если владельца
  нет весь день — видимый результат как в ветке «задача 7 не зелёна», плюс
  строка владельцу.
- **Историан из контейнера недоступен** (задача 1, шаг 4):
  - не работает ни `bridge`, ни `host` — день идёт локально, владельцу тем же
    часом уходит вопрос о пути к 1433 из контейнеров;
  - работает только `host` — серверный слой в `pcbk-egress` тоже не дойдёт;
    выкладка идёт, строка историана будет красной «нет связи с историаном»,
    вопрос владельцу тем же часом.

## Global Constraints

**Из Д1 и Д2 (коротко):**

- Сторонние образы — только закреплённые (`FROM имя:тег@sha256:…`,
  `deploy/images.lock`, `latest` запрещён). У своих образов в `compose.yaml` —
  `pull_policy: never`. Базовый образ серверного слоя — тот же, что у сторожа:
  `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`.
- Секреты и данные заказчика — **никогда в git и в выводе проверок**. Перед
  каждым коммитом задач 1, 8–10:
  `git diff --cached -U0 -- . ':!docs/plans' | grep -E -i -f <шаблоны>` —
  пусто. В файл шаблонов задания Д3а добавляет: шаблон имён тегов по схеме
  именования историана (номер участка и узла в начале имени) и
  `BDRV_PW=[^$<{ "]`; и префиксы имён прежнего списка без цифры в начале
  (группы качества полотна и общих по машине) — сам файл шаблонов в git не
  идёт.
- **Секреты контейнерам — только файлами** (секреты Compose или bind `:ro`),
  никогда через `environment`, `env_file` или `--env-file` — в том числе у
  пробы. Файлы секретов — `0444` в каталоге `0700` (Д2, решение 5).
- Личные учётные записи не пишутся нигде; доступ к серверу — `$SSH` из Д2.
- Dify не трогаем. `/opt/dify/scripts/.bdrv.env` только читается: один раз
  копируется `install -m 0444` в `/opt/pcbk-reserve/secrets/bdrv.env`.
- `docker.sock` — только у `sp-ro` и `sp-ctl`.
- Сети — с закреплёнными подсетями и `name:` без префикса. Все, кроме
  `pcbk-public` и `pcbk-egress`, — `internal: true` с изолированным шлюзом.
  `pcbk-egress` — только для `pcbk-core`, маскарад включён.
- Наружу — только 8443 `edge`. Серверный слой через 8443 не открывается.
- Людям — по-русски; имена в коде — по-английски; время — со смещением от UTC.
- Свои скрипты, пробный образ и шаблоны — в рабочем каталоге задания: `$JOB` —
  каталог `pcbk-d3` внутри `$CLAUDE_JOB_DIR/tmp` той сессии, что ведёт день.
  Д3б на исходники пробы Д3а не опирается: там свой скрипт поверх образа
  `pcbk-probe/tds:1.17.1`.

**Д3а добавляет:**

- **SQL — только функции `core/pcbk_core/data/sql.py`.** В представлениях
  провайдера (`Live`, `AnalogHistory`, `AnalogSummaryHistory`,
  `StateSummaryHistory`) нет `LIKE` и параметров драйвера. Имя тега попадает в
  SQL литералом через `TagName IN (…)`, не больше 16 имён, и только если прошло
  `fullmatch [A-Za-z0-9_]{1,128}`. `IN`/`OR` не ставятся на ww-столбцы.
- **Соединение:** python-tds 1.17.1, `dsn=` (не устаревший `server=`),
  `autocommit=True`. Перед запросами —
  `SET LOCK_TIMEOUT 5000; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;`.
  `login_timeout` — не больше 10 с, `timeout` — остаток срока вызова (не
  больше 45 с). Так брошенный по сроку запрос не держит историан втрое дольше
  срока. Без `cafile` (О5); одно соединение на вызов. Порт передаётся, только
  если он задан или в адресе нет экземпляра (`хост\ЭКЗЕМПЛЯР`).
- **Дверь к историану — только `HistorianGate`.**
  - Места: у вызовов людей 2, у фоновых опросов 1.
  - Общий предел — 300 запросов за 5 минут, считается только для вызовов
    людей и только после того, как место взято.
  - Место ждут в цикле asyncio, а не в потоке: ожидание не занимает пул
    потоков. Запросы идут в своём пуле ворот на 3 потока.
  - Срок по умолчанию 15 с, у каталога свой — `CATALOG_DEADLINE_S`. Место
    берётся, только если после этого остаётся не меньше `min(5 с, срок / 3)`.
  - Каждый строковый литерал SQL — имя из белого списка внутри
    `TagName IN (…)` (Д3б добавит даты и закрытый набор ww-значений). Иначе —
    отказ без SQL.
  - Защёлка при отказе учётных данных: ни одного входа до перезапуска службы —
    учётка общая с Dify, её блокировка положит курс. После обрыва связи 30 с
    новые попытки сразу получают отказ.
- **Белый список** — файл `/opt/pcbk-reserve/data/whitelist.txt` на сервере, не
  в git; правило `d3-1` (Д3а-R1); `_LMN` исключён до слова владельца.
- **Тесты без историана:** двойник `QueryFn`; имена тегов в тестах — только
  синтетические, с `FAKE`.
- **Производственные данные читаются только при владельце** (задачи 1 и 8).
  Классификатор безопасности Claude Code может спросить его подтверждение —
  это ожидаемо. Исключение: локальный подсчёт по прежнему списку
  `pcbk-ai-lab` без вывода имён (задача 8, шаг 3).
- **Журнал** — INFO в stderr явным обработчиком логгера `pcbk_core`; значений
  тегов в журнале нет. Логгер `pytds` — не ниже WARNING: на INFO он пишет
  адрес историана и начало SQL с именами тегов.
- **Зависимости** — точные версии с хешами в `core/requirements.lock`,
  установка `--require-hashes --no-deps`. Закреплены `python-tds==1.17.1` и
  `mcp==1.30.0` с комментарием «2.x — другой API, переход отдельной задачей».
- Ручки учений — только те, что проходят проверку `Settings`. У сторожа из
  Д1 — правило срока такта: `HIST_*` его не затрагивают, `TICK_S` и
  `STALE_AFTER_S` учения Д3а не меняют.
- Тесты серверного слоя запускаются из каталога `core/` командой
  `uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q`
  (дальше `CORE_PYTEST`). Асинхронные тесты — `@pytest.mark.anyio`, фикстура
  `anyio_backend` → `"asyncio"`.

## Решения по умолчанию (Ruling)

**Д3а-R1 — правило белого списка `d3-1`** (правила от владельца пока нет;
О9, О10). Требования — из прежнего правила 05 §3.6, код не переносится.
1. Имя — только `[A-Za-z0-9_]`: так записаны все 1104 имени прежнего списка.
2. Участок — имя начинается с `20`…`25`.
3. Плюс имена из `whitelist.extra` на сервере. Файл засевается один раз из
   прежнего списка (`pcbk/bdrv/whitelist.json`): все его имена без цифры в
   начале — 321, из них 199 — качество полотна и общие по машине, 122 — узлы
   участка с именами без номера. Имена других участков из прежнего списка (их
   82) в засев не входят: входят ли они в пилот — отдельный вопрос
   владельцу.
4. Аналоговые — без хвостов `_LMN`, `_TH`, `_HMI`, `_SP_HMI`, `_MV1`, `_m3`
   (регистр не важен). **`_LMN` исключён до слова владельца.**
5. Дискретные — только хвосты `_RUN`, `_OPN`, `_CLS`, `_ON`, `_OFF`, `_STOP`,
   `_FLT`, `_ALM`.
6. Правила 4–5 действуют и на имена из `whitelist.extra`.
7. Живость не фильтрует.
8. Пустой результат файл не перезаписывает.

Перед подъёмом `core` владелец видит три числа: N — общих с прежним списком,
M — только в прежнем, K — только в новом. M показывается с разбивкой: другие
участки, отсечено правилами 4–5, прочее. От него нужно явное «да». Если K больше
25 % прежнего, ему показывается ещё разбивка K по хвостам (только числа), и он
выбирает: правило `d3-1` или «прежний список плюс правила 4–6» (ключ
построителя `--base`).

**Цена ошибки:**
- список шире нужного — студент спросит лишний тег участка, и значение уйдёт
  модели;
- список уже нужного — нужный тег получит отказ;
- правка — около 10 минут.

**Д3а-R2 — выход серверного слоя в сеть.** `pcbk-egress` —
`172.31.250.80/28`, серверный слой на `.82`, динамические адреса — только
`.88/29`, маскарад включён. В Д3а в процессе нет охраны выхода: он соединяется
только с историаном, вызывающих у него нет. Охрана (аудит-хук на соединения и
запуск процессов) ставится в Д3б, до токенов и инструментов. **Остаточный риск
Д3а:** на уровне сети из `pcbk-egress` открыт любой адрес, включая сервер через
шлюз моста; задача 9 записывает это фактом. **Цена ошибки:** на один день
ошибка в нашем коде может соединиться не туда. Внешнего входа в процесс нет,
кроме ручек здоровья.

**Д3а-R3 — свежесть историана (О8).** Набор тегов — восемь аналоговых тегов
белого списка с самой свежей меткой `Live` на момент загрузки каталога; он
перевыбирается при каждой загрузке. Возраст — сколько прошло с последнего роста
наибольшей метки набора (по монотонным часам службы), но не меньше возраста
самой метки по часам историана. Метки впереди `GETDATE()` поэтому не дают
вечного «0 с», а подача, замершая до запуска, видна сразу. Пороги держит
сторож: 300 и 900 с, уточняются по пробе. **Цена ошибки:** если все восемь
тегов «тихие», ровный процесс даст ложное «устарело»; лечится порогом.

**Д3а-R4 — фоновые опросы не тонут в нагрузке.** Каталог и свежесть идут
через своё место в двери и в общий предел не считаются. Места ждут в цикле
asyncio, а не в потоках, поэтому очередь людей не выбирает пул потоков, и фон
не ждёт ни мест, ни потоков людей. Всего одновременно — не больше 3 запросов (в
05 — «семафор на 2–3»). Каталог и свежесть делят одно фоновое место; окно без
опроса во время загрузки каталога укладывается в `HIST_STALE_S` (формула в
задаче 1). **Цена ошибки:** при медленном историане фон держит ещё один
запрос сверх двух запросов людей.

**Д3а-R5 — строка сторожа по роли.** Строка `core` проверяет
`/healthz/data` — только роль «данные» — и называется «Служба данных». В Д4 у
LLM-прокси своя строка (`/healthz/llm`), так что учение «LLM-прокси
остановлен» не окрасит службу данных. Общий `/healthz` остаётся для
`HEALTHCHECK` образа. При 503 сторож показывает причину из ответа (до 80
знаков), а не только код.

## Review Focus

1. **Пароль БДРВ сменили в Dify, копию `bdrv.env` не обновили.** Служба не
   должна долбить сервер неудачными входами: учётка общая с плагином Dify, и
   её блокировка положит курс. Ожидание: после первого отказа — ни одного
   входа до перезапуска службы, на странице «историан отклонил учётные данные
   — обновите bdrv.env и перезапустите службу». Тест — задача 3,
   `test_auth_error_latches_no_more_logins`.
2. **Историан завис или очередь длинная.** Вызов заканчивается к сроку, место
   держится до возврата драйвера. Место, взятое на исходе срока, не отправляет
   SQL. Тесты — задача 3, `test_gate_deadline_returns_timeout_and_keeps_slot`,
   `test_gate_late_acquire_is_busy_not_run`.
3. **Нагрузка студентов выбирает общий предел, места и пул потоков.** Строка
   историана не должна стать «неизвестно» при исправном историане. Ожидание:
   фоновый вызов не ждёт мест людей (ответ быстрее 0,3 с при двух занятых
   местах и двадцати ждущих вызовах), всего одновременно — не больше 3
   запросов. Тесты — задача 3, `test_background_lane_not_starved`,
   `test_gate_total_concurrency_is_three`; задача 5,
   `test_poll_updates_under_user_saturation`.
4. **Метки впереди часов историана и учение «метка устарела».** Возраст не
   залипает на «0 с». Учение с замороженными метками даёт предупреждение и
   сбой по настоящему возрасту, а не по порогу 0. Тесты — задача 5,
   `test_future_stamps_use_time_since_advance`, `test_drill_freeze_stamps_ages`.
5. **Каталог грузится дольше срока или падает раз за разом.** Служба не должна
   каждую минуту гонять самый тяжёлый запрос с выброшенным результатом, а
   причина должна быть на странице. Тест — задача 5, `test_catalog_backoff_and_reason`.

---

## Карта файлов

```
core/pyproject.toml                   pytest: pythonpath = ["."]; uv: managed = false
core/requirements.in                  python-tds==1.17.1, mcp==1.30.0, fastapi, uvicorn — точные версии
core/requirements.lock                с хешами
core/Dockerfile                       образ pcbk-reserve/core:d3a, uid 10003
core/.dockerignore
core/pcbk_core/__init__.py
core/pcbk_core/main.py                вход: настройки, журнал, роли, uvicorn; --healthcheck
core/pcbk_core/app.py                 протокол Role, create_app: /healthz и /healthz/{role}
core/pcbk_core/settings.py            Settings.from_env
core/pcbk_core/secrets.py             read_env_file, BdrvConfig
core/pcbk_core/logs.py                setup_logging
core/pcbk_core/data/__init__.py       DataRole: каталог, свежесть, ручки здоровья
core/pcbk_core/data/sql.py            шаблоны без дат, литералы, names_in
core/pcbk_core/data/historian.py      python-tds, QueryFn, HistorianError, часы историана
core/pcbk_core/data/gate.py           HistorianGate, SlidingWindow, GateRefused, GateResult
core/pcbk_core/data/names.py          чтение списков имён
core/pcbk_core/data/catalog.py        каталог в памяти, freshest
core/pcbk_core/data/build_whitelist.py  правило d3-1 → whitelist.txt (CLI)
core/pcbk_core/data/freshness.py      FreshnessTracker, тексты ошибок
core/tests/helpers.py, fakes.py, conftest.py, test_*.py
watchdog/pcbk_watchdog/checks.py      + check_historian; check_http: ok_detail, причина при 503
watchdog/pcbk_watchdog/main.py        + вид historian, HIST_WARN_S, HIST_FAIL_S, HIST_STALE_S
watchdog/components.json              core → /healthz/data «жива»; historian → historian
watchdog/tests/                       + тесты вида historian
compose.yaml                          + core, pcbk-egress, секрет bdrv-env; сторож :d3a
compose.test.yaml                     + core: FRESH_POLL_S=5
deploy/env.example                    + DATA_DIR, HIST_*, CATALOG_DEADLINE_S
deploy/README.md                      + служба данных: bdrv.env, белый список, смена пароля, откат
.gitignore                            + whitelist.txt, whitelist.extra
tests/integration/conftest.py         http_as под псевдонимом; готовность core; http_host, logs; данные core
tests/integration/test_core.py
tests/integration/test_edge.py        DECLARED_ENV, IMAGES: + core, сторож :d3a
tests/integration/test_socket_proxy.py  итог без строки историана; потеря sp-ro — по строке sp-ro
docs/checks/D3a.md, docs/checks/D3a/*.png
```

**Сети** — таблицы Д1 и Д2 плюс:

| Сеть | Подсеть | Вид | Кто в ней |
|---|---|---|---|
| `pcbk-egress` | `172.31.250.80/28`, динамика только `172.31.250.88/29` | обычная, маскарад включён | только `pcbk-core` — `.82` |
| `pcbk-front` | как в Д1 | внутренняя, изолированный шлюз | + `pcbk-core` |

Имена: служба `core`, контейнер `pcbk-core`, образ `pcbk-reserve/core:d3a`,
uid 10003, порт 8000; секрет Compose `bdrv-env` → `/run/secrets/bdrv.env`;
белый список `${DATA_DIR}/whitelist.txt` → `/app/data/whitelist.txt`. Сторож —
`pcbk-reserve/watchdog:d3a`, `:d2` остаётся на сервере для отката. Пробный
образ — `pcbk-probe/tds:1.17.1`.

---

### Task 0: Хвосты Д2

Утренний слот, который держит план Д2: всё, что его черта перенесла в Д3.

**Files:**
- Modify: `docs/checks/D2.md`

- [ ] **Step 1:** По README, раздел «Состояние», и `docs/checks/D2.md` составить
  список хвостов Д2: задача 5, шаги 7–8 (`dispose`, холодный старт и память);
  проверка чистым клоном; второй раунд критика и слияние с тегом
  `platform-d2`, если они перенесены. Отдельная строка: если выкладка Д2 не
  сделана (ветка «задача 3 не зелёна» плана Д2), — сразу ветка «хвосты больше
  1,5 ч» шапки: строка владельцу и пересчёт Д3а. Expected: у каждого хвоста
  вердикт [П] или «ждёт владельца»; ветка Д3а начинается от `main` после тега
  `platform-d2`.
- [ ] **Step 2: Commit** (после проверки на секреты) — `git commit -m "Д2: хвосты закрыты в утреннем слоте Д3а"`.

---

### Task 1: Утренняя проба историана — запросы 1–6

**Нужен владелец:** шаг 4 читает производственные данные. Проба отвечает на
О1, О3, О11 и половину О4 (язык и формат даты сессии). Замеры времени
каталога и снимка `Live` задают срок загрузки каталога. Запросы 7–15 (формат
даты, вид среднего, интерполяция) — утром Д3б. Пробный образ и скрипт — в `$JOB`, в
репозитории их нет.

**Files:**
- Create: `docs/checks/D3a.md`

**Interfaces:**
- Produces: образ `pcbk-probe/tds:1.17.1` на сервере, режим `morning-a`;
  копия `/opt/pcbk-reserve/secrets/bdrv.env` (`0444`); решения для
  `.env`: `HIST_WARN_S`, `HIST_FAIL_S`, `HIST_STALE_S`, `CATALOG_DEADLINE_S`;
  сетевой путь `bridge` или `host`.

- [ ] **Step 1: Пробный образ (в `$JOB/probe/`)**

`FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`;
`req.txt` — `python-tds==1.17.1` по `uv pip compile --generate-hashes`,
установка `--require-hashes --no-deps`; `probe.py`; `ENTRYPOINT ["python", "/app/probe.py"]`.
Правила `probe.py`:
- учётные данные читаются из `/run/secrets/bdrv.env` тем же разбором, что
  `read_env_file` задачи 2: `export`, кавычки, `#`;
- соединение — как у службы (`dsn=`, `autocommit=True`, `PRELUDE`, 10/45 с);
- печатается один объект JSON: числа строк, доли, секунды, название и номер
  версии; ещё — есть ли в `BDRV_HOST` экземпляр (`\`) и задан ли
  `BDRV_PORT` (да/нет, без значений). Имён тегов и значений нет ни в одном
  режиме; ошибки — только класс исключения.

Образ — на сервер: `docker save … | gzip | $SSH 'gunzip | docker load'`,
сверка `RootFS`.

- [ ] **Step 2: Запросы режима `morning-a`**

| № | Запрос | Что печатается |
|---|---|---|
| 1 | `SELECT @@VERSION` | название и номер версии SQL Server (О1) |
| 2 | `SELECT Value FROM SystemParameter WHERE Name = 'HistorianVersion'` | версия историана или «не найдено: <класс>» (О1) |
| 3 | `SELECT GETDATE(), GETUTCDATE(), SYSDATETIMEOFFSET()` | смещение `GETDATE() − GETUTCDATE()` до 15 мин; совпадает ли с поясом `SYSDATETIMEOFFSET` (О3) |
| 4 | `SELECT @@LANGUAGE, (SELECT date_format FROM sys.dm_exec_sessions WHERE session_id = @@SPID)` | язык и формат даты сессии (О4, половина) |
| 5 | текст `catalog_sql()` из задачи 3 | строк, аналоговых, дискретных, секунд |
| 6 | текст `live_all_sql()` из задачи 3 | строк, секунд; по тегам участка (`20`…`25`, без `$`) возраст против п. 3: p50, p95, max, число и минимум отрицательных; возраст самого свежего и 8-го по свежести аналогового; доля `Quality = 0` (О11) |

- [ ] **Step 3: Копия учётных данных (на сервере, без вывода содержимого)**

`test -d /opt/pcbk-reserve/secrets && stat -c '%a' /opt/pcbk-reserve/secrets; install -m 0444 /opt/dify/scripts/.bdrv.env /opt/pcbk-reserve/secrets/bdrv.env; stat -c '%a' /opt/pcbk-reserve/secrets/bdrv.env; grep -c '^BDRV_' /opt/pcbk-reserve/secrets/bdrv.env; docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -c '^172\.31\.250\.80/28$'`
Expected: `700`, `444`, 4 или 5 строк `BDRV_`, подсеть свободна (0).

- [ ] **Step 4: Проба (при владельце)**

`$SSH 'docker run --rm --network bridge --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro pcbk-probe/tds:1.17.1 morning-a' > "$JOB/probe-a.json"`.
Если запрос 1 даёт `connect`, повторить с `--network host`. Какой путь сработал
— записать; ветки на случай неудачи — в шапке. Перед переносом в журнал:
`grep -E -i -f <шаблоны> "$JOB/probe-a.json"` — пусто.
Expected: ответ на каждый из 6 запросов (вердикт или класс ошибки).

- [ ] **Step 5: Решения по пробе**

| Итог | Решение | По умолчанию |
|---|---|---|
| О11: 8-й по свежести аналоговый тег участка моложе 100 с | `HIST_WARN_S=300`, `HIST_FAIL_S=900` | те же |
| О11: он старше 100 с | `HIST_WARN_S` = 3 × его возраст (вверх до минуты), `HIST_FAIL_S` = 3 × `HIST_WARN_S` | — |
| запросы 5 + 6 дольше 10 с | `CATALOG_DEADLINE_S` = 3 × их сумма (вверх до 10 с) | `60` |
| всегда | `HIST_STALE_S` = max(120, `CATALOG_DEADLINE_S` + 2 × `FRESH_POLL_S` + 10): загрузка каталога занимает место фона, и опрос свежести на это время замолкает; драйвер держит место не дольше срока, 10 с — запас на вход | `130` |
| в адресе экземпляр, `BDRV_PORT` не задан | порт не передаётся, `pytds` находит его сам; охрана выхода Д3б пускает историан на любой TCP-порт | — |
| О1, О3, О4 (язык) | только вердикт; пояс служба берёт из `GETDATE() − GETUTCDATE()` | — |

Expected: вердикты и таблица решений — в `docs/checks/D3a.md`.

- [ ] **Step 6: Commit** (после проверки на секреты) — `git add docs/checks/D3a.md && git commit -m "Д3а: утренняя проба — версия, пояс, язык сессии, время каталога, задержка Live"`.

---

### Task 2: Каркас серверного слоя

**Files:**
- Create: `core/pyproject.toml`, `core/requirements.in`, `core/requirements.lock`,
  `core/Dockerfile`, `core/.dockerignore`, `core/pcbk_core/__init__.py`,
  `core/pcbk_core/{main,app,settings,secrets,logs}.py`,
  `core/pcbk_core/data/__init__.py` (пустой), `core/tests/{helpers,conftest}.py`
- Test: `core/tests/test_skeleton.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Settings`:
    - поля: `CORE_PORT: int = 8000`,
      `BDRV_ENV_FILE: str = "/run/secrets/bdrv.env"`,
      `WHITELIST_PATH: str = "/app/data/whitelist.txt"`,
      `FRESH_POLL_S: float = 30.0`, `CATALOG_REFRESH_S: float = 86400.0`,
      `CATALOG_DEADLINE_S: float = 60.0`, `DRILL_FRESHNESS: str = ""`;
    - `from_env(env: Mapping[str, str] | None = None) -> Settings`: пустое
      значение — умолчание, числа > 0, `CATALOG_DEADLINE_S ≥ 10` (два запаса
      ворот по 5 с, иначе каталог не загрузится никогда), `DRILL_FRESHNESS` ∈
      {`""`, `"freeze_stamps"`, `"freeze_poll"`}, иначе `ValueError` с именем
      поля.
  - `read_env_file(path: str) -> dict[str, str]`: `KEY=VALUE`, `#`, пустые
    строки, `export `, одна пара окружающих кавычек. Ошибочная строка →
    `ValueError("<файл>: строка N — не KEY=VALUE")` без содержимого строки.
  - `@dataclass(frozen=True) class BdrvConfig: host: str; port: int | None; database: str; user: str; password: str = field(repr=False)`:
    - `from_env_file(path) -> BdrvConfig`;
    - обязательны `BDRV_HOST`, `BDRV_USER`, `BDRV_PW`; умолчание
      `BDRV_DB=Runtime`;
    - порт: `BDRV_PORT`, если задан; иначе `None`, когда в адресе экземпляр
      (`\`), иначе 1433.
  - `setup_logging() -> logging.Logger`: логгер `pcbk_core`, INFO, свой
    обработчик stderr, `propagate=False`; логгеры `pytds` и `mcp` — WARNING;
    повторный вызов второго обработчика не добавляет.
  - `class Role(Protocol)`: `name: str`; `router() -> APIRouter`;
    `health() -> tuple[bool, str]`;
    `lifespan() -> AbstractAsyncContextManager[None]`;
    `install(app: FastAPI) -> None` — обработчики исключений и промежуточные
    звенья уровня приложения; других путей к ним у ролей нет, Д4 и Д5 ставят
    свои так же;
    `mounts() -> list[tuple[str, ASGIApp]]` — точные маршруты: `create_app`
    ставит их как `Route(path, app)`, не `Mount`, и чужие пути они не
    перехватывают.
  - `class RoleBase` — пустые `install` и `mounts`, от него наследуются роли.
  - `create_app(settings: Settings, roles: Sequence[Role]) -> FastAPI`:
    - `FastAPI(docs_url=None, redoc_url=None, openapi_url=None, …)`: с Д4 порт
      8000 виден из сетей мест, описание API им не нужно;
    - порядок: роутеры ролей → `GET /healthz` → `GET /healthz/{role}` →
      `install(app)` каждой роли → точные маршруты `mounts()` (последними);
    - `/healthz` — `200 {"ok": true, "roles": {имя: пояснение}}`, если все роли
      здоровы, иначе `503` с `"ok": false`;
    - `/healthz/{role}` — `200`/`503` `{"ok": bool, "detail": str}`;
      неизвестная роль — `404`;
    - жизненный цикл — `AsyncExitStack` над `lifespan()` ролей.
  - `main.py`:
    - `build_roles(settings) -> list[Role]` — пока `[]`, задача 5 добавит
      `DataRole`;
    - `main(argv=None) -> int`: `--healthcheck` делает `GET 127.0.0.1:CORE_PORT/healthz`
      и возвращает 0 при 200; без аргументов — `setup_logging` и
      `uvicorn.run(…, host="0.0.0.0", log_config=None, access_log=False)`.
  - Образ `pcbk-reserve/core:d3a`: `pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock`,
    uid/gid 10003, `COPY pcbk_core`,
    `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1`, `EXPOSE 8000`,
    `HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-m", "pcbk_core.main", "--healthcheck"]`,
    `CMD ["python", "-m", "pcbk_core.main"]`.
  - `requirements.in`: `python-tds==1.17.1`,
    `mcp==1.30.0  # последний 1.x: 2.x — другой API (FastMCP → MCPServer), переход отдельной задачей`,
    `fastapi==`, `uvicorn==` — версии на день выполнения.
  - `helpers.py`: `SETTINGS`, `DummyRole(name, health, installs=False)` (при
    `installs=True` ставит в `install` промежуточное звено с заголовком
    `X-Role-Installed: 1` и точный маршрут `/dummy`), `write(tmp_path, text) -> str`.
    `conftest.py`: `anyio_backend`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_skeleton.py
def test_read_env_file_strips_quotes_and_export(tmp_path):
    p = write(tmp_path, "BDRV_HOST=h\nBDRV_PW=\"p w\"\n# к\n\nexport BDRV_USER='u'\n")
    assert read_env_file(p) == {"BDRV_HOST": "h", "BDRV_PW": "p w", "BDRV_USER": "u"}

def test_read_env_file_error_hides_line(tmp_path):
    with pytest.raises(ValueError) as e:
        read_env_file(write(tmp_path, "BDRV_HOST=h\nсекретная-строка\n"))
    assert "строка 2" in str(e.value) and "секрет" not in str(e.value)

def test_bdrv_config_defaults_and_hidden_password(tmp_path):
    cfg = BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=s3cr3t\n"))
    assert (cfg.port, cfg.database) == (1433, "Runtime") and "s3cr3t" not in repr(cfg)
    inst = BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\\INST\nBDRV_USER=u\nBDRV_PW=p\n"))
    assert inst.port is None                       # экземпляр: порт находит pytds
    with pytest.raises(ValueError, match="BDRV_PW"):
        BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\n"))

def test_settings_from_env():
    assert Settings.from_env({"FRESH_POLL_S": "5", "DRILL_FRESHNESS": ""}).FRESH_POLL_S == 5.0
    for bad in ({"FRESH_POLL_S": "0"}, {"DRILL_FRESHNESS": "freeze"}, {"CATALOG_DEADLINE_S": "5"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)

def test_install_hook_exact_mounts_and_no_docs():
    with TestClient(create_app(SETTINGS, [DummyRole("data", (True, "ок"), installs=True)])) as c:
        assert c.get("/healthz").headers["X-Role-Installed"] == "1"
        assert c.get("/dummy").status_code == 200 and c.get("/dummy/x").status_code == 404
        assert c.get("/openapi.json").status_code == 404 and c.get("/docs").status_code == 404

def test_healthz_and_role_health():
    ok, bad = DummyRole("data", (True, "ок")), DummyRole("llm", (False, "нет ключа"))
    with TestClient(create_app(SETTINGS, [ok, bad])) as c:
        assert c.get("/healthz").status_code == 503
        assert c.get("/healthz/data").json() == {"ok": True, "detail": "ок"}
        r = c.get("/healthz/llm")
        assert (r.status_code, r.json()["detail"]) == (503, "нет ключа")
        assert c.get("/healthz/gateway").status_code == 404

def test_logging_info_reaches_stderr_once(capfd):
    logging.getLogger().setLevel(logging.WARNING)
    setup_logging()
    setup_logging().info("pcbk-marker")
    assert capfd.readouterr().err.count("pcbk-marker") == 1
    assert not logging.getLogger("pytds").isEnabledFor(logging.INFO)   # адрес и SQL — не в журнал

def test_lock_pins_python_tds_and_mcp_1x():
    text = Path("requirements.lock").read_text()
    assert re.search(r"^python-tds==1\.17\.1\b", text, re.M) and re.search(r"^mcp==1\.30\.0\b", text, re.M)
    reqs = [l for l in text.splitlines() if re.match(r"^[A-Za-z0-9]", l)]
    assert reqs and all("==" in l for l in reqs) and text.count("--hash=sha256:") >= len(reqs)

def test_mcp_1x_api_importable():                 # несовпадение API всплывает в первый час
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.client.streamable_http import streamablehttp_client
    assert FastMCP and TransportSecuritySettings and streamablehttp_client
```

- [ ] **Step 2: Run tests to verify they fail**

Сначала `requirements.in` и
`uv pip compile core/requirements.in --python-version 3.12 --generate-hashes -o core/requirements.lock`;
затем `cd core && CORE_PYTEST tests/test_skeleton.py`.
Expected: FAIL — `ModuleNotFoundError: pcbk_core`

- [ ] **Step 3: Implement каркас по интерфейсам выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `(cd core && CORE_PYTEST tests/test_skeleton.py) && docker build -t pcbk-reserve/core:d3a core/`
Expected: PASS; образ собран

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Серверный слой: каркас ролей, здоровье по ролям, учётные данные файлом, mcp 1.30.0 закреплён"
```

---

### Task 3: Шаблоны, соединение и ворота к историану

**Files:**
- Create: `core/pcbk_core/data/sql.py`, `core/pcbk_core/data/historian.py`,
  `core/pcbk_core/data/gate.py`, `core/tests/fakes.py`
- Modify: `core/tests/helpers.py` (`FakeMono`)
- Test: `core/tests/test_sql.py`, `core/tests/test_historian.py`, `core/tests/test_gate.py`

**Interfaces:**
- Consumes: `BdrvConfig` — задача 2.
- Produces (`sql.py`):
  - `MAX_NAMES = 16`; `SAFE_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")`;
    `PROVIDER_VIEWS = ("Live", "AnalogHistory", "AnalogSummaryHistory", "StateSummaryHistory")`
  - `lit_name(name: str) -> str` — проверка `SAFE_NAME.fullmatch`, иначе
    `ValueError`; литерал в `'…'` с удвоением `'`
  - `clock_sql()` = `SELECT GETDATE() AS NowLocal, GETUTCDATE() AS NowUtc`
  - `catalog_sql()` = `SELECT t.TagName, t.Description, t.TagType, a.MinEU, a.MaxEU, e.Unit FROM Tag t LEFT JOIN AnalogTag a ON a.TagName = t.TagName LEFT JOIN EngineeringUnit e ON e.EUKey = a.EUKey WHERE t.TagType IN (1, 2)`
  - `live_all_sql()` = `SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL`
  - `live_sql(names: Sequence[str]) -> str` = `SELECT TagName, DateTime, Value, Quality FROM Live WHERE TagName IN (<литералы>)`;
    0 или больше 16 имён → `ValueError`
  - `names_in(sql: str) -> tuple[str, ...]` — литералы из каждого
    `TagName IN (…)` по порядку
  - `literals(sql: str) -> tuple[str, ...]` — все строковые литералы `'…'`
    (с разбором `''`) по порядку
- Produces (`historian.py`):
  - `Row = tuple`; `QueryFn = Callable[[Sequence[str], float], list[list[Row]]]`
    — несколько SQL на одном соединении; второй аргумент — остаток срока в
    секундах
  - `PRELUDE = "SET LOCK_TIMEOUT 5000; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;"`
  - `ErrorCode = Literal["connect", "timeout", "query", "auth"]`;
    `class HistorianError(Exception): code: ErrorCode; detail: str; sent: tuple[str, ...] = ()`
  - `AUTH_MSG_NOS = frozenset({18452, 18456, 18486, 18487, 18488})`
  - `tds_query(cfg: BdrvConfig, connect: Callable[..., Any] = pytds.connect) -> QueryFn`:
    - на вызов одно соединение:
      `connect(dsn=cfg.host, database=cfg.database, user=cfg.user, password=cfg.password, autocommit=True, login_timeout=min(10, t), timeout=min(45, t), appname="pcbk-core")`,
      где `t` — остаток срока; `port=cfg.port` — только если он не `None`;
    - затем `execute(PRELUDE)` и на каждую строку `execute(sql)` одним
      аргументом и `fetchall()`; закрытие — в `finally`;
    - разбор ошибок: `pytds.LoginError` или `pytds.OperationalError` с `msg_no`
      из `AUTH_MSG_NOS` → `auth`; прочее из `connect` → `connect`;
      `socket.timeout`/`TimeoutError` при чтении → `timeout`; `OSError` при
      чтении → `connect`; `pytds.Error` → `query` (`detail` — класс и первые
      200 знаков).
  - `@dataclass(frozen=True) class HistClock: local: datetime; utc: datetime`:
    свойства `offset` (до 15 мин), `tz`, `tz_label` («UTC+05:00»); метод
    `aware(dt) -> datetime`.
  - `parse_clock(rows: list[Row]) -> HistClock`
- Produces (`gate.py`):
  - `class SlidingWindow: __init__(self, limit: int, window_s: float); allow(self, key: str, now: float) -> bool` —
    разрешённый вызов записывается
  - `GLOBAL_LIMIT = 300`, `RATE_WINDOW_S = 300.0`, `USER_SLOTS = 2`,
    `BACKGROUND_SLOTS = 1`, `CALL_DEADLINE_S = 15.0`, `Q_MIN_S = 5.0`,
    `CONNECT_COOLDOWN_S = 30.0`
  - `Lane = Literal["user", "background"]`;
    `class GateRefused(Exception): code: Literal["rate", "busy", "unlisted"]`
  - `@dataclass(frozen=True) class GateResult: rows: list[list[Row]]; sent: tuple[str, ...]` —
    `sent`: имена из всех `names_in` запроса, без повторов, по порядку
  - `class HistorianGate`:
    - `__init__(self, query: QueryFn, *, allowed: frozenset[str], literal_ok: Callable[[str], bool] = lambda text: False, user_slots: int = USER_SLOTS, background_slots: int = BACKGROUND_SLOTS, deadline_s: float = CALL_DEADLINE_S, q_min_s: float = Q_MIN_S, global_window: SlidingWindow | None = None, monotonic: Callable[[], float] = time.monotonic)`.
      Окно по умолчанию — `SlidingWindow(GLOBAL_LIMIT, RATE_WINDOW_S)`.
      Места — `asyncio.Semaphore` на полосу. Запросы — в своём
      `ThreadPoolExecutor(max_workers=user_slots + background_slots)`.
    - `async def run(self, statements: Sequence[str], *, lane: Lane = "user", deadline_s: float | None = None) -> GateResult`,
      по порядку:
      1. каждый литерал из `literals()` должен быть именем из `names_in()` той
         же строки и входить в `allowed` — либо проходить `literal_ok`
         (в Д3а таких нет; Д3б пускает даты и `'STAIRSTEP'`). Иначе — строка
         журнала «ворота: литерал вне белого списка — отказ без SQL»,
         счётчик, `GateRefused("unlisted")`. Шаблон с `TagName = '…'`
         поэтому не обходит ни проверку, ни `sent`;
      2. после ошибки `auth` ворота защёлкнуты до перезапуска службы:
         `HistorianError("auth", "защёлка: …")` без входа. После ошибки
         `connect` в течение `CONNECT_COOLDOWN_S` —
         `HistorianError("connect", "пауза после обрыва")` без входа;
      3. `q_min = min(q_min_s, срок / 3)`; место полосы ждут в цикле asyncio
         не дольше `срок − q_min`, иначе `GateRefused("busy")`. Взяв место при
         остатке срока меньше `q_min`, его отпускают — `GateRefused("busy")`,
         SQL не уходит;
      4. только для полосы `user` и только после взятого места —
         `global_window.allow("historian", now)`, отказ → место отпускается,
         `GateRefused("rate")`;
      5. запрос уходит в пул ворот с остатком срока как `timeout` драйвера.
         Место отпускается колбэком по завершении будущего результата, то
         есть после возврата драйвера;
      6. ожидание результата — не дольше остатка срока; по сроку →
         `HistorianError("timeout", …, sent=…)`, запрос дорабатывает с
         местом. Ошибки драйвера после отправки тоже несут `sent`;
      7. `auth` ставит защёлку; `connect` ставит паузу.
    - `stats(self) -> dict`: `sent_names_total` (число), `refused_unlisted`,
      `in_flight` {`user`, `background`}, `auth_latched` — только числа и
      флаги, без имён
    - публичные атрибуты `monotonic` и `global_window` (тесты подменяют окно)
- Produces (`fakes.py`):
  - `RecordingConnect` — двойник `pytds.connect`: `kwargs`, `connect_count`,
    `executed`, `results`, `closed`, `fail_on_connect`, `fail_on_execute`
  - синтетика — блок ниже и
    `CLOCK_ROWS = [(datetime(2026, 10, 1, 12, 0, 0), datetime(2026, 10, 1, 7, 0, 0))]`;
    ею пользуются и тесты задач 4–5
  - `class FakeHistorian` — вызываемый `QueryFn`:
    - SQL разбирается регулярками: часы, каталог, весь `Live`,
      `Live … IN (…)`; строки отдаются только для имён из **литералов**; иной
      SQL → `AssertionError`;
    - конструктор: `delay_s=0.0`, `fail=None`, `slow: str | None = None` —
      задержка действует только на вызов, где какая-то строка совпала с
      регуляркой `slow` (по умолчанию — на все);
    - поля: `calls: list[list[str]]`, `timeouts: list[float]`,
      `max_concurrency` (по полосам не делится), `delay_s`, `fail`, `slow`,
      `live: dict[str, tuple[datetime, float | None, int]]`,
      `clock: list[Row]`

```python
# core/tests/fakes.py — (TagName, Description, TagType, MinEU, MaxEU, Unit)
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
LIVE_ROWS = [   # TagName, DateTime, Value, Quality
    ("20FAKE_001_PV", datetime(2026, 10, 1, 11, 59, 18), 12.5, 0),
    ("20FAKE_002_SP", datetime(2026, 10, 1, 11, 50, 0), 12.0, 0),
    ("20FAKE_004_PV", datetime(2026, 10, 1, 11, 59, 50), 3.2, 64),
    ("25FAKE_007_CLS", datetime(2026, 10, 1, 11, 40, 0), 1.0, 0),
    ("16FAKE_009_PV", datetime(2026, 10, 1, 11, 59, 59), 0.5, 0),
]
```

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_sql.py
SAMPLES = [clock_sql(), catalog_sql(), live_all_sql(), live_sql(["20FAKE_001_PV", "20FAKE_002_SP"])]

def test_no_driver_parameters_no_like_single_statement():
    for s in SAMPLES:
        assert not re.search(r"\?|%s|%\(|@", s) and ";" not in s
        assert not re.search(r"\bLIKE\b|\bOR\b", s, re.I) and not re.search(r"\bww\w+\s+IN\b", s, re.I)

@pytest.mark.parametrize("bad", ["A'B", "A B", "20FAKE_001_PV\n", "", "ТЕГ_01", "x" * 129, "A;B", "A.B", "$Sys"])
def test_name_literal_rejects(bad):
    with pytest.raises(ValueError):
        lit_name(bad)

def test_live_sql_names_in_and_literals():
    s = live_sql(["20FAKE_001_PV", "20FAKE_002_SP"])
    assert s.endswith("FROM Live WHERE TagName IN ('20FAKE_001_PV', '20FAKE_002_SP')")
    assert names_in(s) == literals(s) == ("20FAKE_001_PV", "20FAKE_002_SP")
    assert names_in(catalog_sql()) == literals(catalog_sql()) == ()
    assert literals("SELECT 1 WHERE a = 'x''y'") == ("x'y",)
    with pytest.raises(ValueError):
        live_sql([f"20FAKE_{i:03d}_PV" for i in range(17)])

# core/tests/test_historian.py
CFG = BdrvConfig("h", 1433, "Runtime", "u", "p")

def test_connect_args_prelude_one_argument_and_deadline_timeout():
    rec = RecordingConnect(results=[[(1,)], [(2,)]])
    assert tds_query(CFG, connect=rec)(["SELECT 1", "SELECT 2"], 12.0) == [[(1,)], [(2,)]]
    kw = rec.kwargs
    assert (kw["dsn"], kw["port"], kw["autocommit"], kw["login_timeout"], kw["timeout"]) == ("h", 1433, True, 10, 12.0)
    assert "server" not in kw and "cafile" not in kw and rec.connect_count == 1
    assert rec.executed == [(PRELUDE,), ("SELECT 1",), ("SELECT 2",)] and rec.closed
    rec2 = RecordingConnect(results=[[(1,)]])
    tds_query(replace(CFG, host="h\\INST", port=None), connect=rec2)(["SELECT 1"], 99.0)
    assert "port" not in rec2.kwargs and rec2.kwargs["timeout"] == 45

@pytest.mark.parametrize("where,exc,code", [
    ("connect", pytds.LoginError("Login failed"), "auth"),
    ("connect", auth_operational_error(18456), "auth"),          # OperationalError с msg_no
    ("connect", auth_operational_error(18452), "auth"),
    ("connect", ConnectionRefusedError(), "connect"),
    ("execute", socket.timeout(), "timeout"),
    ("execute", pytds.ProgrammingError("bad"), "query"),
])
def test_errors_are_classified(where, exc, code):
    rec = RecordingConnect(**{f"fail_on_{where}": exc})
    with pytest.raises(HistorianError) as e:
        tds_query(CFG, connect=rec)(["SELECT 1"], 15.0)
    assert e.value.code == code

def test_parse_clock():
    c = parse_clock([(datetime(2026, 10, 1, 12, 0, 7), datetime(2026, 10, 1, 7, 0, 3))])
    assert c.tz_label == "UTC+05:00"
    assert c.aware(datetime(2026, 10, 1, 11, 59, 18)).isoformat() == "2026-10-01T11:59:18+05:00"

# core/tests/test_gate.py
pytestmark = pytest.mark.anyio
Q = live_sql(["20FAKE_001_PV"])
SLOW_Q = r"IN \('20FAKE_001_PV'\)$"          # медленны только запросы людей по одному тегу

def gate(fake, **kw):
    return HistorianGate(fake, allowed=WHITELIST, **kw)

async def test_gate_caps_user_concurrency_and_returns_sent():
    fake = FakeHistorian(delay_s=0.2)
    g = gate(fake)
    rs = await asyncio.gather(*(g.run([live_sql([n])]) for n in sorted(WHITELIST)[:5]))
    assert fake.max_concurrency == 2 and rs[0].sent == (sorted(WHITELIST)[0],)
    assert g.stats()["sent_names_total"] == 5

async def test_gate_refuses_foreign_literals_without_sql():
    fake = FakeHistorian()
    g = gate(fake)
    for sql in (live_sql(["16FAKE_009_PV"]),                                # имя вне списка
                "SELECT TagName FROM Live WHERE TagName = '20FAKE_001_PV'",  # имя из списка, но мимо IN
                "SELECT TagName FROM Live WHERE TagName = '16FAKE_009_PV'"):
        with pytest.raises(GateRefused) as e:
            await g.run([sql])
        assert e.value.code == "unlisted"
    assert fake.calls == [] and g.stats()["refused_unlisted"] == 3

async def test_gate_deadline_returns_timeout_sent_and_keeps_slot():             # Review Focus 2
    fake = FakeHistorian(delay_s=1.0)
    g = gate(fake, deadline_s=0.3)
    t = time.monotonic()
    with pytest.raises(HistorianError) as e:
        await g.run([Q])
    assert e.value.code == "timeout" and e.value.sent == ("20FAKE_001_PV",) and time.monotonic() - t < 0.5
    assert fake.timeouts[-1] <= 0.3                                         # драйвер ждёт не дольше срока
    assert g.stats()["in_flight"]["user"] == 1
    await anyio.sleep(0.9)
    assert g.stats()["in_flight"]["user"] == 0

async def test_q_min_scales_with_short_deadline():
    g = gate(FakeHistorian(), deadline_s=0.3)                                # q_min = min(5, 0,1)
    assert (await g.run([Q])).sent == ("20FAKE_001_PV",)

async def test_gate_late_acquire_is_busy_not_run():                             # Review Focus 2
    fake = FakeHistorian(delay_s=0.9)
    g = gate(fake, user_slots=1, q_min_s=0.4)
    first = asyncio.ensure_future(g.run([Q]))
    await anyio.sleep(0.05)
    with pytest.raises(GateRefused) as e:          # место свободно только через 0,85 с: с запасом 0,4 не успеть
        await g.run([Q], deadline_s=1.2)
    assert e.value.code == "busy" and len(fake.calls) == 1
    await first

async def test_background_lane_not_starved():                                   # Review Focus 3
    fake = FakeHistorian(delay_s=1.0, slow=SLOW_Q)
    g = gate(fake, global_window=SlidingWindow(2, 300))
    users = [asyncio.ensure_future(g.run([Q])) for _ in range(2)]
    await anyio.sleep(0.05)
    t = time.monotonic()
    r = await g.run([clock_sql()], lane="background")
    assert time.monotonic() - t < 0.3 and g.stats()["in_flight"]["user"] == 2   # фон не ждал мест людей
    assert r.rows == [CLOCK_ROWS]
    await asyncio.gather(*users)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])                                                     # окно людей выбрано
    assert e.value.code == "rate"

async def test_background_survives_twenty_waiting_users():                     # Review Focus 3
    fake = FakeHistorian(delay_s=1.0, slow=SLOW_Q)
    g = gate(fake, deadline_s=2.5)
    users = [asyncio.ensure_future(g.run([Q])) for _ in range(20)]         # 18 ждут места, не потоки
    await anyio.sleep(0.05)
    t = time.monotonic()
    await g.run([clock_sql()], lane="background")
    assert time.monotonic() - t < 0.3
    await asyncio.gather(*users, return_exceptions=True)

async def test_gate_total_concurrency_is_three():                              # Review Focus 3
    fake = FakeHistorian(delay_s=0.3)
    g = gate(fake)
    async def timed(lane):
        t = time.monotonic(); await g.run([clock_sql()] if lane == "background" else [Q], lane=lane)
        return time.monotonic() - t
    users = [asyncio.ensure_future(timed("user")) for _ in range(3)]
    backs = [asyncio.ensure_future(timed("background")) for _ in range(2)]
    await asyncio.gather(*users, *backs)
    assert fake.max_concurrency == 3
    assert max(b.result() for b in backs) >= 0.55                            # второй фоновый ждал своё место

async def test_global_window_counts_only_user_after_acquire():
    fake = FakeHistorian()
    g = gate(fake, global_window=SlidingWindow(2, 300), monotonic=lambda: 1.0)
    for _ in range(3):
        await g.run([clock_sql()], lane="background")
    await g.run([Q]); await g.run([Q])
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "rate" and len(fake.calls) == 5

async def test_auth_error_latches_until_restart():                              # Review Focus 1
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("auth", "18456"))
    g = gate(fake, monotonic=mono)
    for step in range(3):
        with pytest.raises(HistorianError) as e:
            await g.run([clock_sql()], lane="background")
        assert e.value.code == "auth"
        mono.advance(10 ** 6)                                               # время защёлку не снимает
    assert len(fake.calls) == 1 and g.stats()["auth_latched"] is True

async def test_connect_failure_pauses_attempts():
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("connect", "refused"))
    g = gate(fake, monotonic=mono)
    for _ in range(2):
        with pytest.raises(HistorianError):
            await g.run([clock_sql()])
    assert len(fake.calls) == 1
    mono.advance(CONNECT_COOLDOWN_S)
    fake.fail = None
    await g.run([clock_sql()])
    assert len(fake.calls) == 2
```

`FakeMono` — в `helpers.py` (вызываемый, `advance(s)`); `replace` — из
`dataclasses`. `auth_operational_error(n)` — в `fakes.py`:
`pytds.OperationalError` с атрибутом `msg_no = n`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_sql.py tests/test_historian.py tests/test_gate.py`
Expected: FAIL — нет модулей

- [ ] **Step 3: Implement `sql.py`, `historian.py`, `gate.py`, `fakes.py` по интерфейсам выше**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core && CORE_PYTEST tests/test_sql.py tests/test_historian.py tests/test_gate.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Служба данных: шаблоны без LIKE, python-tds с разбором ошибок, ворота — полосы, срок, защёлка входа"
```

---

### Task 4: Каталог и белый список

**Files:**
- Create: `core/pcbk_core/data/names.py`, `core/pcbk_core/data/catalog.py`,
  `core/pcbk_core/data/build_whitelist.py`
- Test: `core/tests/test_catalog.py`, `core/tests/test_build_whitelist.py` (синтетика — из `fakes.py` задачи 3)

**Interfaces:**
- Consumes: `SAFE_NAME`, `catalog_sql`, `Row`, `tds_query`, `BdrvConfig` —
  задачи 2–3.
- Produces:
  - `load_names(path: str) -> frozenset[str]` — `#` и пустые строки
    пропускаются; имя вне `SAFE_NAME` → `ValueError("строка N: недопустимое имя")`;
    нет файла → `FileNotFoundError`
  - `@dataclass(frozen=True) class TagInfo: name: str; description: str; kind: Literal["analog", "discrete"]; unit: str | None; min_eu: float | None; max_eu: float | None; live: bool; live_time: datetime | None` —
    литерал `None` в `Unit` → `None`
  - `class Catalog`:
    - `from_rows(tag_rows: list[Row], live_rows: list[Row], whitelist: frozenset[str]) -> Catalog`;
      `empty() -> Catalog`;
    - счётчики `loaded: bool`, `total`, `whitelisted`, `live`,
      `missing: int` (имена списка, которых нет в каталоге);
    - `freshest(k: int = 8) -> tuple[str, ...]` — аналоговые живые теги белого
      списка по убыванию `live_time`.

    Поиск и разбор имён — Д3б.
  - `build_whitelist.py`: `RULE_VERSION = "d3-1"`,
    `SECTION_PREFIX = re.compile(r"2[0-5]")`,
    `SERVICE_TAILS = ("_LMN", "_TH", "_HMI", "_SP_HMI", "_MV1", "_m3")`,
    `STATE_TAILS = ("_RUN", "_OPN", "_CLS", "_ON", "_OFF", "_STOP", "_FLT", "_ALM")`;
    - `select_whitelist(tag_rows: list[Row], extra: frozenset[str], base: frozenset[str] | None = None) -> list[str]` —
      Д3а-R1; при `base` правило 2 заменяется на «имя есть в `base`»;
      результат отсортирован;
    - `write_list(path: str, names: Sequence[str], header: Mapping[str, str]) -> None` —
      пустой список → `ValueError`, старый файл цел; временный файл и
      `os.replace`; права `0o444`; шапка из строк `# ключ: значение`;
    - `main(argv=None) -> int` — ключи `--out`, `--extra`, `--base`,
      `--env-file`. Выполняет `catalog_sql()` одним соединением
      (`tds_query(cfg)([catalog_sql()], 120.0)`) и печатает только счётчики:
      всего, аналоговых, дискретных, из `extra` найдено и не найдено, по
      хвостам, отсечено правилами 4–5.


- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_build_whitelist.py
def test_select_whitelist_rule_d3_1():
    assert select_whitelist(CATALOG_ROWS, EXTRA) == sorted(WHITELIST)

def test_lmn_excluded_also_from_extra():
    got = select_whitelist(CATALOG_ROWS, EXTRA)
    assert "20FAKE_005_LMN" not in got and "QFAKE_011_LMN" not in got

def test_base_replaces_section_rule():                # выбор владельца: прежний список + правила 4–6
    assert select_whitelist(CATALOG_ROWS, frozenset(), base=frozenset({"20FAKE_001_PV", "20FAKE_005_LMN"})) \
           == ["20FAKE_001_PV"]

def test_write_list_refuses_empty_and_is_atomic(tmp_path):
    p = tmp_path / "whitelist.txt"
    write_list(str(p), ["20FAKE_001_PV"], {"rule": RULE_VERSION})
    assert oct(p.stat().st_mode & 0o777) == "0o444" and "# rule: d3-1" in p.read_text()
    with pytest.raises(ValueError):
        write_list(str(p), [], {"rule": RULE_VERSION})
    assert load_names(str(p)) == {"20FAKE_001_PV"}

def test_load_names_rejects_bad_line(tmp_path):
    p = tmp_path / "w"
    p.write_text("# шапка\n20FAKE_001_PV\n\n20FAKE 002\n")
    with pytest.raises(ValueError, match="строка 4"):
        load_names(str(p))

# core/tests/test_catalog.py
def test_catalog_counts_and_freshest():
    cat = Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST | {"20FAKE_404_PV"})
    assert (cat.loaded, cat.total, cat.whitelisted, cat.missing) == (True, 13, 6, 1)
    assert cat.freshest(8) == ("20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP")   # 16FAKE — не в списке

def test_empty_catalog():
    assert Catalog.empty().loaded is False and Catalog.empty().freshest() == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_catalog.py tests/test_build_whitelist.py`
Expected: FAIL — нет модулей

- [ ] **Step 3: Implement `names.py`, `catalog.py`, `build_whitelist.py` по интерфейсам и Д3а-R1**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core && CORE_PYTEST tests/test_catalog.py tests/test_build_whitelist.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Служба данных: каталог в памяти, правило белого списка d3-1 и построитель"
```

---

### Task 5: Свежесть и роль «данные»

**Files:**
- Create: `core/pcbk_core/data/freshness.py`
- Modify: `core/pcbk_core/data/__init__.py` (`DataRole`), `core/pcbk_core/main.py` (`build_roles`)
- Test: `core/tests/test_freshness.py`, `core/tests/test_role.py`

**Interfaces:**
- Consumes: всё из задач 2–4.
- Produces (`freshness.py`):
  - `ERROR_TEXTS = {"connect": "нет связи с историаном", "timeout": "историан не ответил вовремя", "query": "историан вернул ошибку", "auth": "историан отклонил учётные данные — обновите bdrv.env и перезапустите службу", "catalog": "каталог тегов не загружен", "no_tags": "не выбраны теги свежести", "no_rows": "нет меток по тегам свежести"}`
  - `class FreshnessTracker`:
    - `__init__(self, freeze_stamps: bool = False)`;
    - `observe(self, clock: HistClock, rows: list[Row], tags: int, mono: float, wall: datetime) -> None` —
      берётся наибольшая метка набора; «сырой» возраст = `clock.local − метка`;
      `skew_s = min(0, сырой)`; рост метки запоминает `mono`; при
      `freeze_stamps` метка никогда не «растёт» — держится первая
      наблюдённая; строк нет → ошибка `no_rows`;
    - `fail(self, code: str, tags: int, mono: float, wall: datetime) -> None` —
      прошлые метки сохраняются;
    - `to_json(self, mono: float) -> dict` — ключи `checked_at` (ISO UTC или
      `None`), `age_s`, `skew_s`, `error`, `error_text`, `tags`;
      `age_s = max(max(0, сырой) + (mono − mono опроса), mono − mono роста)`,
      до 0,1 с.
- Produces (`DataRole` в `data/__init__.py`, наследует `RoleBase`, `name = "data"`):
  - `__init__(self, settings: Settings, query: QueryFn, whitelist: frozenset[str], *, monotonic=time.monotonic, wallclock=lambda: datetime.now(timezone.utc))`
  - поля: `monotonic`, `gate: HistorianGate` (с `allowed=whitelist`), `catalog: Catalog`,
    `freshness: FreshnessTracker`, `catalog_error: str | None`,
    `catalog_failures: int`, `catalog_loaded_mono: float | None`,
    `freshness_tags: tuple[str, ...]`, `clock: HistClock | None`,
    `clock_mono: float | None` — часы историана и момент их получения; их
    обновляют и `refresh_catalog`, и `poll_freshness` (раз в 30 с), Д3б
    берёт отсюда «сейчас историана» без лишнего запроса
  - `async def refresh_catalog(self) -> bool` — ворота
    `[clock_sql(), catalog_sql(), live_all_sql()]`, полоса `background`, срок
    `CATALOG_DEADLINE_S`. Удача → каталог, часы и
    `freshness_tags = catalog.freshest(8)`, строка журнала «каталог: N тегов,
    в белом списке M, нет в историане K, живых L», `True`. `HistorianError` или
    `GateRefused` → код в `catalog_error`, `catalog_failures += 1`, `False`;
    прежний каталог остаётся.
  - `next_catalog_delay(self, ok: bool) -> float` — удача → `CATALOG_REFRESH_S`;
    иначе `min(60 × 2^(catalog_failures − 1), 900)`.
  - `async def poll_freshness(self) -> None`:
    - каталога нет → `fail(catalog_error or "catalog")`;
    - пустой набор → `fail("no_tags")`;
    - иначе ворота `[clock_sql(), live_sql(набор)]`, полоса `background` →
      `observe`;
    - `HistorianError` → `fail(code)`; `GateRefused` → без изменений: фоновое
      место занимает только загрузка каталога, и её окно укладывается в
      `HIST_STALE_S` (задача 1, шаг 5).
  - `health(self) -> tuple[bool, str]` — белый список пуст → `(False, "белый список пуст или не найден")`;
    иначе `(True, "каталог: M тегов в белом списке")` или
    `(True, "каталог ещё не загружен")`
  - `router()` — `GET /health/historian` без токена:
    `freshness.to_json(mono)` плюс `catalog_age_s` (или `None`),
    `catalog_error`, `gate` (`stats()` — только числа и флаги). Имён тегов в
    ответе нет: с Д4 ручку видят сети мест.
  - `lifespan()` — два цикла:
    - каталог: сразу, дальше через `next_catalog_delay`;
    - свежесть: после первой попытки каталога, дальше раз в `FRESH_POLL_S`;
      при `DRILL_FRESHNESS=freeze_poll` цикл встаёт после первого опроса;
    - при непустом `DRILL_FRESHNESS` при старте — строка журнала
      «УЧЕНИЯ: DRILL_FRESHNESS=<режим>»; трекер создаётся с
      `freeze_stamps=(режим == "freeze_stamps")`;
    - на выходе оба цикла отменяются.
  - `install`, `mounts` — от `RoleBase` (пусто; Д3б их заполнит)
  - `Settings` для тестов строится напрямую, проверки `from_env` (в том числе
    `CATALOG_DEADLINE_S ≥ 10`) на это не действуют
  - `main.build_roles(settings)` → `[DataRole(settings, tds_query(BdrvConfig.from_env_file(...)), whitelist)]`;
    нет файла списка → пустое множество и строка журнала
  - `helpers.py`: `make_role(settings=SETTINGS, fake=None, whitelist=WHITELIST, mono=None) -> tuple[DataRole, FakeHistorian]`

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_freshness.py
CLOCK = HistClock(local=datetime(2026, 10, 1, 12, 0, 0), utc=datetime(2026, 10, 1, 7, 0, 0))
W = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)

def rows(*stamps):
    return [(f"20FAKE_{i:03d}_PV", s, 1.0, 0) for i, s in enumerate(stamps)]

def test_age_from_historian_clock():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 18)), 1, mono=100.0, wall=W)
    assert (f.to_json(100.0)["age_s"], f.to_json(110.0)["age_s"]) == (42.0, 52.0)

def test_future_stamps_use_time_since_advance():                               # Review Focus 4
    f, ahead = FreshnessTracker(), datetime(2026, 10, 1, 12, 0, 31)
    f.observe(CLOCK, rows(ahead), 1, mono=100.0, wall=W)
    assert (f.to_json(100.0)["age_s"], f.to_json(100.0)["skew_s"]) == (0.0, -31.0)
    f.observe(CLOCK, rows(ahead), 1, mono=130.0, wall=W)                        # не выросла
    assert f.to_json(160.0)["age_s"] == 60.0
    f.observe(CLOCK, rows(ahead + timedelta(seconds=30)), 1, mono=190.0, wall=W)
    assert f.to_json(191.0)["age_s"] == 1.0

def test_drill_freeze_stamps_ages():                                           # Review Focus 4
    f, t = FreshnessTracker(freeze_stamps=True), datetime(2026, 10, 1, 12, 0, 31)
    for i, mono in enumerate((100.0, 130.0, 160.0)):
        f.observe(CLOCK, rows(t + timedelta(seconds=30 * i)), 1, mono=mono, wall=W)
    assert f.to_json(220.0)["age_s"] == 120.0                                   # как у замершей подачи

def test_old_data_at_start_is_old_at_once():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 0)), 1, mono=5.0, wall=W)
    assert f.to_json(5.0)["age_s"] == 3600.0

def test_error_texts():
    f = FreshnessTracker()
    assert f.to_json(0.0)["checked_at"] is None
    f.fail("auth", 8, mono=1.0, wall=W)
    assert f.to_json(1.0)["error_text"] == "историан отклонил учётные данные — обновите bdrv.env и перезапустите службу"
    f.observe(CLOCK, [], 8, mono=2.0, wall=W)
    assert f.to_json(2.0)["error"] == "no_rows"

# core/tests/test_role.py
pytestmark = pytest.mark.anyio

async def test_refresh_catalog_loads_and_picks_freshest():
    role, fake = make_role()
    assert await role.refresh_catalog() is True
    assert role.freshness_tags == ("20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP")
    await role.poll_freshness()
    j = role.freshness.to_json(role.monotonic())
    assert (j["age_s"], j["tags"], j["error"]) == (10.0, 3, None)
    assert set(names_in(fake.calls[-1][1])) <= WHITELIST
    assert role.clock.tz_label == "UTC+05:00" and role.clock_mono == role.monotonic()

async def test_catalog_backoff_and_reason():                                   # Review Focus 5
    role, fake = make_role()
    fake.fail = HistorianError("timeout", "долго")
    delays = []
    for _ in range(6):
        ok = await role.refresh_catalog()
        delays.append(role.next_catalog_delay(ok))
    assert delays == [60, 120, 240, 480, 900, 900]
    await role.poll_freshness()
    assert role.freshness.to_json(0.0)["error_text"] == "историан не ответил вовремя"

async def test_catalog_uses_own_deadline():
    role, fake = make_role(settings=replace(SETTINGS, CATALOG_DEADLINE_S=0.3))
    fake.delay_s = 0.6
    assert await role.refresh_catalog() is False and role.catalog_error == "timeout"

async def test_poll_updates_under_user_saturation():                           # Review Focus 3
    role, fake = make_role()
    await role.refresh_catalog()
    role.gate.global_window = SlidingWindow(2, 300)
    fake.delay_s, fake.slow = 1.0, r"IN \('20FAKE_001_PV'\)$"          # медленны только запросы людей
    users = [asyncio.ensure_future(role.gate.run([live_sql(["20FAKE_001_PV"])])) for _ in range(2)]
    await anyio.sleep(0.05)                          # оба места людей заняты, окно выбрано
    t = time.monotonic()
    await role.poll_freshness()
    assert time.monotonic() - t < 0.3 and role.gate.stats()["in_flight"]["user"] == 2
    assert role.freshness.to_json(role.monotonic())["checked_at"] is not None
    await asyncio.gather(*users)

async def test_drill_freeze_poll_stops_loop():
    role, fake = make_role(settings=replace(SETTINGS, FRESH_POLL_S=0.05, DRILL_FRESHNESS="freeze_poll"))
    async with role.lifespan():
        await anyio.sleep(0.4)
    polls = sum(1 for call in fake.calls if call[0] == clock_sql() and len(call) == 2)
    assert polls == 1

def test_health_and_routes(tmp_path):
    role, _ = make_role(whitelist=frozenset())
    assert role.health() == (False, "белый список пуст или не найден")
    role, _ = make_role()
    with TestClient(create_app(SETTINGS, [role])) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.get("/health/historian")
        j = r.json()
        assert {"checked_at", "age_s", "skew_s", "error", "error_text", "tags",
                "catalog_age_s", "catalog_error", "gate"} <= set(j)
        assert not any(n in r.text for n in WHITELIST)                   # имён тегов мимо токена нет
        assert c.get("/healthz/data").json()["ok"] is True
```

`wait_until(pred, timeout=5)` — в `helpers.py`; `make_role` по умолчанию
берёт `FakeMono(1000.0)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST tests/test_freshness.py tests/test_role.py`
Expected: FAIL — нет `freshness.py`, у `DataRole` нет полей

- [ ] **Step 3: Implement `freshness.py`, `DataRole`, `build_roles` по интерфейсам выше**

- [ ] **Step 4: Run the whole core suite**

Run: `cd core && CORE_PYTEST`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/
git commit -m "Служба данных: свежесть по росту меток, каталог со своим сроком и паузой, ручки учений"
```

---

### Task 6: Сторож — вид `historian`

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/pcbk_watchdog/main.py`,
  `watchdog/components.json`, `watchdog/tests/{conftest,helpers}.py`
- Test: `watchdog/tests/test_checks.py`, `watchdog/tests/test_main.py`
  (`test_components_file_d2` → `test_components_file_d3a`)

**Interfaces:**
- Consumes: `Check`, `check_http`, `NET_CHECK_TIMEOUT_S`, `KINDS`, `Settings`
  (с правилом срока такта), `run_checks`, `load_components` — код Д1 и Д2.
- Produces:
  - `check_http(component, title, url, timeout=3.0, ok_detail: str = "отвечает") -> Check`:
    при 2xx деталь — `ok_detail`; при другом коде, если тело — объект JSON с
    строкой `detail`, → `fail` с ней (первые 80 знаков), иначе «отвечает
    ошибкой HTTP N»; вид `http` берёт необязательное `ok_detail` из
    `components.json`.
  - `check_historian(component: str, title: str, url: str, now: datetime, warn_s: int, fail_s: int, stale_s: int, timeout: float = NET_CHECK_TIMEOUT_S) -> Check` —
    таблица ниже, первая подходящая сверху.
  - `KINDS["historian"] = ("url",)`; `_check_one` для этого вида вызывает
    `check_historian(cid, title, comp["url"], now, settings.HIST_WARN_S, settings.HIST_FAIL_S, settings.HIST_STALE_S)`.
  - `Settings`: `HIST_WARN_S: int = 300`, `HIST_FAIL_S: int = 900`,
    `HIST_STALE_S: int = 130` (формула задачи 1, шаг 5); `0 < HIST_WARN_S < HIST_FAIL_S`,
    `HIST_STALE_S > 0`, иначе `ValueError`.
  - `components.json`:
    - `core` →
      `{"id": "core", "title": "Служба данных", "kind": "http", "url": "http://pcbk-core:8000/healthz/data", "ok_detail": "жива"}`;
    - `historian` →
      `{"id": "historian", "title": "Историан БДРВ", "kind": "historian", "url": "http://pcbk-core:8000/health/historian"}`;
    - остальное — как оставил Д2.
  - `fake_core` (фикстура): `/health/historian` отдаёт JSON из `set(obj)` или
    текст из `set_raw(text)`; `/healthz/data` — код и тело из `set_health(code, obj)`;
    атрибуты `url`, `base`.

| Ответ службы | Итог |
|---|---|
| нет соединения, таймаут, HTTP ≠ 200, не JSON | `unknown` «служба данных не отвечает — свежесть неизвестна» |
| `checked_at` — `null` | `unknown` «служба ещё не опрашивала историан» |
| `checked_at` старше `stale_s` | `unknown` «служба давно не опрашивала историан» |
| `error` задан | `fail` — `error_text` (первые 80 знаков) |
| `age_s ≥ fail_s` | `fail` «последняя метка старше {fail_s} с» |
| `age_s ≥ warn_s` | `warn` «последняя метка старше {warn_s} с» |
| `catalog_age_s` больше 172800 | `warn` «каталог тегов старше двух суток» |
| иначе | `ok` «последняя метка {round(age_s)} с назад» |

- [ ] **Step 1: Write the failing tests**

```python
# watchdog/tests/test_checks.py
def fresh(age, **kw):
    return {"checked_at": (T0 - timedelta(seconds=10)).isoformat(), "age_s": age, "skew_s": 0.0,
            "error": None, "error_text": None, "tags": 8, "catalog_age_s": 3600.0, **kw}

def hist(fake_core, obj):
    fake_core.set(obj)
    return check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, timeout=0.5)

def test_historian_verdicts(fake_core):
    cases = [(fresh(42.4), ("ok", "последняя метка 42 с назад")),
             (fresh(300), ("warn", "последняя метка старше 300 с")),
             (fresh(901), ("fail", "последняя метка старше 900 с")),
             (fresh(None, error="auth", error_text="историан отклонил учётные данные — обновите bdrv.env и перезапустите службу"),
              ("fail", "историан отклонил учётные данные — обновите bdrv.env и перезапустите службу")),
             (fresh(10, catalog_age_s=200000.0), ("warn", "каталог тегов старше двух суток")),
             (fresh(1, checked_at=(T0 - timedelta(seconds=121)).isoformat()),
              ("unknown", "служба давно не опрашивала историан")),
             (fresh(None, checked_at=None), ("unknown", "служба ещё не опрашивала историан"))]
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail) == want

def test_historian_core_down_or_garbage_is_unknown(fake_core):
    r = check_historian("historian", "Историан БДРВ", "http://127.0.0.1:9/x", T0, 300, 900, 120, 0.5)
    assert (r.state, r.detail) == ("unknown", "служба данных не отвечает — свежесть неизвестна")
    fake_core.set_raw("не json")
    assert check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, 0.5).state == "unknown"

def test_check_http_ok_detail_and_reason(fake_core):
    url = fake_core.base + "/healthz/data"
    fake_core.set_health(200, {"ok": True, "detail": "каталог: 6"})
    assert check_http("core", "Служба данных", url, ok_detail="жива").detail == "жива"
    fake_core.set_health(503, {"ok": False, "detail": "белый список пуст или не найден"})
    r = check_http("core", "Служба данных", url, ok_detail="жива")
    assert (r.state, r.detail) == ("fail", "белый список пуст или не найден")

# watchdog/tests/test_main.py
def test_components_file_d3a():
    comps = {c["id"]: c for c in load_components("components.json")}
    assert (comps["core"]["url"], comps["core"]["ok_detail"], comps["core"]["title"]) == \
           ("http://pcbk-core:8000/healthz/data", "жива", "Служба данных")
    assert comps["historian"]["kind"] == "historian"
    assert {i for i, c in comps.items() if c["kind"] == "absent"} == {"llm"}

def test_hist_settings_validation():
    assert Settings.from_env({"HIST_WARN_S": "60", "HIST_FAIL_S": "120"}).HIST_FAIL_S == 120   # ручка учения
    for warn, fail in ((0, 900), (900, 300), (300, 300)):
        with pytest.raises(ValueError):
            replace(SETTINGS, HIST_WARN_S=warn, HIST_FAIL_S=fail)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — нет `check_historian`

- [ ] **Step 3: Implement по интерфейсам и таблице** (`http.client`, без перенаправлений и прокси)

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: вид historian, строка службы данных по своей роли и с причиной при 503"
```

---

### Task 7: Серверный слой в компоновке

**Files:**
- Modify: `compose.yaml`, `compose.test.yaml`, `deploy/env.example`, `.gitignore`,
  `tests/integration/conftest.py`, `tests/integration/test_edge.py`,
  `tests/integration/test_socket_proxy.py`
- Test: `tests/integration/test_core.py`

**Interfaces:**
- Consumes: образы задач 2–6; класс `Stack` из кода Д1 с правками Д2
  (`compose`, `config`, `prod_config`, `exec`, `sh`, `inspect`, `network`,
  `https`, `wait_status`, `ready`, `wait_ready`, `start`, `stop`, `http_as`,
  `stu_net`, `probe`, `STACK_VARS`, `ONESHOT_LABEL`).
- Produces:

```yaml
  core:
    build: ./core
    image: pcbk-reserve/core:d3a
    pull_policy: never              # свой образ: без never compose ищет имя на Docker Hub
    container_name: pcbk-core
    user: "10003:10003"
    read_only: true
    tmpfs: ["/tmp"]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 256m
    pids_limit: 128
    restart: unless-stopped
    environment:                    # только несекретное
      CORE_PORT: "8000"
      FRESH_POLL_S: ${FRESH_POLL_S:-30}
      CATALOG_DEADLINE_S: ${CATALOG_DEADLINE_S:-60}
      DRILL_FRESHNESS: ${DRILL_FRESHNESS:-}
    volumes:
      - {type: bind, source: "${DATA_DIR:?DATA_DIR не задан}/whitelist.txt", target: /app/data/whitelist.txt,
         read_only: true, bind: {create_host_path: false}}
    secrets: [{source: bdrv-env, target: bdrv.env}]
    networks:
      pcbk-front: {}
      pcbk-egress: {ipv4_address: 172.31.250.82}
# networks:
  pcbk-egress:
    name: pcbk-egress
    driver: bridge
    ipam: {config: [{subnet: 172.31.250.80/28, ip_range: 172.31.250.88/29}]}
# secrets:
  bdrv-env: {file: "${SECRETS_DIR:?SECRETS_DIR не задан}/bdrv.env"}
```

  - сторож: `image: pcbk-reserve/watchdog:d3a`, `HIST_WARN_S: ${HIST_WARN_S:-300}`,
    `HIST_FAIL_S: ${HIST_FAIL_S:-900}`, `HIST_STALE_S: ${HIST_STALE_S:-130}`
  - `compose.test.yaml`: `core` — `environment: {FRESH_POLL_S: "5"}`
  - `deploy/env.example`: `DATA_DIR=`, `HIST_WARN_S=`, `HIST_FAIL_S=`,
    `HIST_STALE_S=`, `CATALOG_DEADLINE_S=` — без значений;
    `.gitignore`: `whitelist.txt`, `whitelist.extra`
  - `conftest.py`:
    - `STACK_VARS` дополняется `DATA_DIR`, `FRESH_POLL_S`,
      `CATALOG_DEADLINE_S`, `DRILL_FRESHNESS`, `HIST_WARN_S`, `HIST_FAIL_S`,
      `HIST_STALE_S`;
    - фикстура пишет в тестовый `SECRETS_DIR` файл `bdrv.env`
      (`BDRV_HOST=192.0.2.10`, `BDRV_PORT=1433`, `BDRV_USER=test`,
      `BDRV_PW=test-not-a-secret` — TEST-NET, недостижим), в тестовый
      `DATA_DIR` — `whitelist.txt` из `20FAKE_001_PV`; файлы `0444`, каталоги
      `0700`; `DATA_DIR` — в `test.env`;
    - `ready["pcbk-core"]` — `docker exec pcbk-core python -m pcbk_core.main --healthcheck`
      с кодом 0.
  - `http_as(name, network, method, url)`: контейнер получает
    `--name pcbk-oneshot-<8 hex> --network-alias <name>`. Иначе одноразовый
    `pcbk-core` из тестов прокси сокета упирается в настоящий `pcbk-core`
    («name already in use»). `-allowfrom` у `sp-ctl` разрешает имя через DNS
    сети — псевдоним проходит, это подтверждают положительные контроли.
  - новые методы `stack`:
    - `http_host(method: str, url: str, body: dict | None = None) -> tuple[int, str]` —
      с хоста теста на адрес серверного слоя в `pcbk-egress` (`urllib`, без
      прокси);
    - `logs(name: str) -> str`.
  - `test_edge.py`: `DECLARED_ENV["pcbk-core"] = {"CORE_PORT", "FRESH_POLL_S", "CATALOG_DEADLINE_S", "DRILL_FRESHNESS"}`;
    у сторожа плюс `HIST_WARN_S`, `HIST_FAIL_S`, `HIST_STALE_S`; `IMAGES`:
    `"pcbk-core": "pcbk-reserve/core:d3a"`, сторож `:d3a`.
  - `test_socket_proxy.py`:
    - `test_status_json_overall_ok` ждёт, что все строки, кроме `historian`
      (в тестовом стенде историана нет), — `ok`; `absent` — только `llm`;
    - `test_watchdog_sees_socket_proxy_loss` ждёт
      `states["sp-ro"] == "fail"`, а не итог страницы: итог и так `fail`
      из-за строки историана.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_core.py
CORE = "http://172.31.250.82:8000"

def row(data, cid):
    return next((c["state"], c["detail"]) for c in data["checks"] if c["component"] == cid)

def test_status_shows_core_alive_and_historian_down(stack):
    data = stack.wait_status(lambda d: row(d, "historian")[0] == "fail", timeout=90)
    assert row(data, "core") == ("ok", "жива") and row(data, "historian") == ("fail", "нет связи с историаном")

def test_core_health_routes_on_egress_address(stack):
    assert stack.http_host("GET", CORE + "/healthz")[0] == 200
    code, body = stack.http_host("GET", CORE + "/healthz/data")
    assert code == 200 and json.loads(body)["ok"] is True
    j = json.loads(stack.http_host("GET", CORE + "/health/historian")[1])
    assert j["catalog_error"] == "connect" and j["gate"]["refused_unlisted"] == 0

def test_edge_does_not_expose_core(stack):
    for path in ("/healthz/data", "/health/historian", "/api/data/tag_now", "/mcp"):
        assert stack.https(path)[0] == 404

def test_egress_network_only_core(stack):
    n = stack.network("pcbk-egress")
    assert n["Internal"] is False
    assert n["Options"].get("com.docker.network.bridge.enable_ip_masquerade", "true") == "true"
    assert [c["Name"] for c in n["Containers"].values()] == ["pcbk-core"]
    nets = stack.inspect("pcbk-core")["NetworkSettings"]["Networks"]
    assert set(nets) == {"pcbk-front", "pcbk-egress"} and nets["pcbk-egress"]["IPAddress"] == "172.31.250.82"

def test_core_secret_and_list_are_readonly_files(stack):
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    for dest in ("/run/secrets/bdrv.env", "/app/data/whitelist.txt"):
        assert (mounts[dest]["Type"], mounts[dest]["RW"]) == ("bind", False), dest
    prod = stack.prod_config()["services"]["core"]
    assert "env_file" not in prod and "BDRV" not in json.dumps(prod.get("environment", {}))
    assert prod["pull_policy"] == "never"

def test_oneshot_alias_keeps_sp_ctl_controls(stack):               # настоящий pcbk-core уже работает
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", "http://pcbk-sp-ctl:2375/v1.44/_ping") == 200
    assert stack.http_as("pcbk-intruder", "pcbk-ctl", "GET", "http://pcbk-sp-ctl:2375/v1.44/_ping") == 403

def test_stop_core_turns_row_red_historian_unknown(stack):
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

- [ ] **Step 3: Implement службу, сеть, секрет, тестовые данные и правки `stack` по интерфейсам выше**

- [ ] **Step 4: Run the whole local suite**

Run: `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/) && uv run --python 3.12 --with pytest pytest -q tests/integration && docker network ls --filter name=pcbk- -q | wc -l`
Expected: PASS; сетей `pcbk-` после прогона — 0

- [ ] **Step 5: Commit**

```bash
git add compose.yaml compose.test.yaml deploy/env.example .gitignore tests/integration/
git commit -m "Серверный слой в компоновке: сеть выхода только у него, учётные данные файлом, одноразовые клиенты под псевдонимом"
```

---

### Task 8: Выкладка и белый список

Шаги — команды и вывод, который значит «прошло». Итоги — в `docs/checks/D3a.md`,
только вердикты и числа. `sudo` не нужен. **Шаг 4 — при владельце.**

**Files:**
- Modify: `deploy/README.md` — раздел «Служба данных»:
  - копия `bdrv.env`; при смене пароля в Dify — повторить `install` и
    `docker compose restart core`, иначе защёлка входа держит службу красной;
  - белый список: правило `d3-1`, `whitelist.extra`, построитель, `--base`;
  - сеть выхода и остаточный риск Д3а;
  - откат.
- Modify: `docs/checks/D3a.md`

- [ ] **Step 1: Предпроверки** — `cd /opt/pcbk-reserve; docker compose ps --format '{{.Name}} {{.State}}'; stat -c '%a' secrets secrets/bdrv.env; [ -d data ] || install -d -m 0700 data; stat -c '%a' data; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d3a-before.txt`.
  Expected: службы Д1 и Д2 на месте; `700`, `444`, `700`.

- [ ] **Step 2: Образы** — локально `docker compose build core watchdog`;
  `docker save pcbk-reserve/core:d3a pcbk-reserve/watchdog:d3a | gzip | $SSH 'gunzip | docker load'`;
  сверка `RootFS`. Expected: совпало; `:d2` сторожа на сервере остался.

- [ ] **Step 3: Засев `whitelist.extra`** (локально, по прежнему списку)

`python3 -c 'import json,re,sys; d=json.load(open(sys.argv[1])); prior=sorted({r["tag"] for recs in d["nodes"].values() for r in recs}); extra=[t for t in prior if not re.match(r"\d", t)]; qc=len({r["tag"] for k in ("qcs","common") for r in d["nodes"].get(k, [])}); other=sum(1 for t in prior if re.match(r"\d", t) and not re.match(r"2[0-5]", t)); open(sys.argv[2],"w").write("\n".join(extra)+"\n"); open(sys.argv[3],"w").write("\n".join(prior)+"\n"); print(len(extra), qc, len(prior), other)' <путь к pcbk/bdrv/whitelist.json> "$JOB/whitelist.extra" "$JOB/prior.txt"`,
затем `scp "$JOB/whitelist.extra" …:/opt/pcbk-reserve/data/whitelist.extra`.
Expected: около `321 199 1104 82`: в засев идут все имена без цифры в начале,
то есть 199 из групп качества и общих по машине и 122 имени узлов участка.
Подсчёт локальный и без вывода имён, поэтому владелец для него не нужен
(исключение из Global Constraints). Число имён других участков (82) — отдельный
вопрос владельцу.

- [ ] **Step 4: Построитель и решение владельца (при владельце)**

На сервере, тем же сетевым путём, что проба:
`docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro -v /opt/pcbk-reserve/data:/data pcbk-reserve/core:d3a python -m pcbk_core.data.build_whitelist --out /data/whitelist.txt --extra /data/whitelist.extra`.
Затем `scp …:/opt/pcbk-reserve/data/whitelist.txt "$JOB/"` и
`comm -12`/`-23`/`-13` с `$JOB/prior.txt` (оба отсортированы) → N общих, M
только в прежнем, K только в новом. M раскладывается локальным
однострочником, только числами: другие участки (цифра в начале, не
`20`…`25`), отсечено хвостами правил 4–5, прочее — например, тега уже нет в
историане. Если K больше 25 % от 1104, считается разбивка K по хвостам (тоже
только числа).
Expected:
- владелец видит N, M с разбивкой и K (с разбивкой, если нужна) и отвечает
  «да» — или выбирает
  «прежний список плюс правила 4–6»; тогда повтор с
  `--base /data/prior.txt` (`prior.txt` копируется на сервер тем же `scp`);
- ответ и числа — в журнал; `stat -c '%a' data/whitelist.txt` → `444`;
- без «да» `core` не поднимается.

- [ ] **Step 5: Поднять серверный слой**

`.env`: `DATA_DIR=/opt/pcbk-reserve/data`, `HIST_*` и `CATALOG_DEADLINE_S` — по
задаче 1. `cp -p compose.yaml compose.yaml.d2`; `rsync -a compose.yaml …:/opt/pcbk-reserve/`;
на сервере `docker compose up -d --no-build core`, затем
`docker compose up -d --no-build watchdog`.
Expected — не позже 2 минут:
- `docker inspect -f '{{.State.Health.Status}}' pcbk-core` → `healthy`;
- на странице «Служба данных — жива», «Историан БДРВ — последняя метка N с
  назад» (`status.json`, разбор через `python3`);
- строка журнала `core` «каталог: …» — в журнал только числа;
- `docker stats --no-stream --format '{{.MemUsage}}' pcbk-core` — меньше 70 %
  от 256 МиБ;
- время работы контейнеров Dify и мест продолжает `~/pcbk-d3a-before.txt`.

Откат: `docker compose rm -sf core`; `docker network rm pcbk-egress`; вернуть
`compose.yaml.d2`; `docker compose up -d --no-build watchdog` (образ `:d2`).

- [ ] **Step 6: Commit** (после проверки на секреты)

```bash
git add deploy/README.md docs/checks/D3a.md
git commit -m "Выкладка Д3а: служба данных под сторожем, белый список d3-1 подтверждён владельцем"
```

---

### Task 9: Живые проверки и учения

Итог каждого шага — вердиктом [П] в `docs/checks/D3a.md`, без имён тегов,
значений и адресов. Снимки — через `$SSH -N -L 18443:127.0.0.1:8443` и
`google-chrome --headless=new --ignore-certificate-errors --virtual-time-budget=8000 --window-size=1200,2000 --screenshot=docs/checks/D3a/<имя>.png https://127.0.0.1:18443/status`.

- [ ] **Step 1: Исправное состояние.** Expected: «Служба данных — норма — жива»,
  «Историан БДРВ — норма — последняя метка N с назад». Снимок `01-norm.png`.

- [ ] **Step 2: Учение «служба остановлена».** `docker stop pcbk-core`.
  Expected: не позже 20 с «Служба данных — сбой — не отвечает», «Историан БДРВ
  — неизвестно — служба данных не отвечает — свежесть неизвестна», снимок
  `02-core-stopped.png`. `docker start pcbk-core` → не позже `CATALOG_DEADLINE_S`
  + 40 с обе строки в норме, снимок `03-core-back.png`.

- [ ] **Step 3: Учение «метка устарела» — метки заморожены, историан читается как обычно.**

1. `HIST_WARN_S=60 HIST_FAIL_S=120 docker compose up -d --no-build watchdog`.
2. `DRILL_FRESHNESS=freeze_stamps docker compose up -d --no-build core`, время
   `t0`.

Expected:
- в журнале `core` — «УЧЕНИЯ: DRILL_FRESHNESS=freeze_stamps»;
- не позже `t0` + 60 + `FRESH_POLL_S` + 10 с — «внимание — последняя метка
  старше 60 с», снимок `04-stamps-warn.png`;
- не позже `t0` + 120 + `FRESH_POLL_S` + 10 с — «сбой — последняя метка
  старше 120 с», снимок `05-stamps-fail.png`;
- время каждого перехода — в журнал: это проверяет подсчёт возраста в службе,
  а не только порог у сторожа.

- [ ] **Step 4: Учение «служба не опрашивает историан».** `DRILL_FRESHNESS=freeze_poll docker compose up -d --no-build core`.
  Expected: не позже `HIST_STALE_S` + 40 с — «неизвестно — служба давно не
  опрашивала историан», снимок `06-poll-frozen.png`. Затем
  `docker compose up -d --no-build core watchdog` (значения из `.env`) → норма,
  снимок `07-recovered.png`; в журнале сторожа — все переходы.

- [ ] **Step 5: Сеть выхода — факты.** `docker network inspect pcbk-egress` → в
  `Containers` только `pcbk-core`. Одноразовый
  `curlimages/curl:8.16.0 --network container:pcbk-core` способом Д1 до
  `1.1.1.1:443` и до шлюза моста `.81:22` → записать как есть (ожидается 1:
  на уровне сети выход открыт, охрана процесса — Д3б).

- [ ] **Step 6: Commit** (после проверки на секреты) — `git add docs/checks/ && git commit -m "Д3а: служба и историан на странице, три учения со снимками"`.

---

### Task 10: Закрытие дня

- [ ] **Step 1: Документы.** Разрез и строки Д3а/Д3б в дорожную карту уже
  внесены коммитом 848ff36 — здесь только недостающее:
  - `docs/DESIGN-platform-2026-09-29.md`: §13 — новый п. 7 «Д3а» (решения
    Д3а-R1…R5, ссылка на `docs/checks/D3a.md`); §4 — «пароль — файлом `:ro`».
  - `docs/PLAN-platform-2026-09-29.md`:
    - строка Д3а: «бюджет» заменить на «общий предел частоты», а «бюджет»
      (16 тегов, 31 сутки, 24 тего-суток, 60 вызовов на вызывающего)
      перенести в строку Д3б — его строит Д3б;
    - в «Отклонениях» — строка «сети мест для `pcbk-core` — в Д4, как в
      дорожной карте и в Д2»;
    - таблица владельца, строка Д3 — «правило `d3-1` подтверждено» или его
      выбор, с числами N, M, K.
  - `README.md`, раздел «Состояние» — «Д3а готов», дальше Д3б.
- [ ] **Step 2: Критик** (Opus 5.5). Блокер — пункт «Блокер дня» дорожной карты.
  Петля — до нуля блокеров, не больше двух раундов; третий — только после
  разговора с владельцем. Второй раунд, если черта сработала (шапка), — в
  утренний слот Д3б, и слияние с тегом — после него.
- [ ] **Step 3: Слияние.** Ветку — в `main`, тег `platform-d3a`. Перед
  пушем — `git log -p main..HEAD -- . ':!docs/plans' | grep -E -i -f <шаблоны>`
  пусто, `git ls-files | grep -cE 'whitelist\.(txt|extra)$|\.env$'` → `0`.
  Затем `git push origin main platform-d3a`. Чистый клон: снимки на месте,
  `(cd core && CORE_PYTEST)` проходит. На сервере удалить
  `~/pcbk-d3a-before.txt` и `rm -f /opt/pcbk-reserve/data/prior.txt` (если
  владелец выбирал `--base`); в `$JOB` удалить `whitelist.*`, `prior.txt`.
- [ ] **Step 4: Владельцу** — «Д3а готов», снимки, числа белого списка.
  Вопросы: 82 имени других участков из прежнего списка — в пилоте или нет;
  `_LMN` (О9); TLS до историана (О5).
