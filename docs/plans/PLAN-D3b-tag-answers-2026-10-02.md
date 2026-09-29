# Д3б. Ответ по тегу за смену — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** служба данных отвечает агентам и вебу: `catalog_search`, `tag_now`,
`tag_period` по HTTP и MCP, с токеном, бюджетом, кэшем и событием на каждый
вызов. Владелец видит по SSH-туннелю:
- ответ `tag_period` за предыдущую смену;
- независимый вердикт сверки этого ответа с сырым `Delta`;
- список инструментов по MCP;
- проверку журнала событий: в SQL ушли только имена из белого списка, чужое
  имя и попытка инъекции отклонены.

**Architecture:** продолжение Д3а в том же `pcbk_core`. К роли `data`
добавляются:
- `DataService` — три инструмента поверх ворот и каталога Д3а;
- `EventLog` — SQLite, поля `requested` и `sent`;
- HTTP `/api/data/{tool}` и MCP (`mcp==1.30.0`, stateless) — две оболочки над
  одной службой.

Ворота Д3а возвращают имена, реально ушедшие в SQL, и не пускают в SQL имя вне
белого списка. Журнал поэтому доказывает, а не повторяет, что ушло в историан.
В процесс ставится охрана выхода — до того, как у службы появятся вызывающие.

**Tech Stack:** как в Д3а; `mcp==1.30.0` (FastMCP, Streamable HTTP, stateless);
SQLite; pydantic.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(§3 п. 3, §4, §9 «Успех 2», «Успех 4»); [`docs/research/05-d3-historian-facts.md`](../research/05-d3-historian-facts.md)
(рекомендации 2, 6–8, 10, 11); первая половина —
[`PLAN-D3a-data-service-2026-10-01.md`](PLAN-D3a-data-service-2026-10-01.md) —
её имена, Global Constraints и решения Д3а-R1…R5 действуют здесь; решения
этого плана пронумерованы дальше — Д3б-R6…R10. Формат и `stack` — планы Д1
и Д2; поток `/event` OpenCode и `edge` — [`docs/research/07-gateway-whitelist.md`](../research/07-gateway-whitelist.md)
(для Д5).

**Предпосылка.** Д3а влит в `main` с тегом `platform-d3a`; ветка дня —
`d3b/tag-answers`.

**Влезает ли в день — оценка по часам.** Задачи последовательны; в часы задач
с кодом входят 15 минут ревью и правок. Шкала — плановая, по ставкам Д1 и Д2,
без сжатия.

| Задача | Часы | Где |
|---|---|---|
| 0. Хвосты Д3а (второй раунд критика, чистый клон — по черте Д3а) | 0–0,5 | — |
| 1. Эталон сверки и проба: запросы 7–15, режим `verify` | 1,75 | локально, сервер — **нужен владелец** |
| 2. Шаблоны с датами и периоды | 0,75 | локально |
| 3. Охрана выхода, токены, бюджет, кэш, single-flight | 1 | локально |
| 4. Разбор имён, поиск и журнал событий | 1 | локально |
| 5. Инструменты и HTTP | 2 | локально |
| 6. MCP | 0,75 | локально |
| 7. Компоновка и выкладка | 1,5 | локально, сервер |
| 8. Живые проверки | 1 | сервер, **нужен владелец** |
| 8а. Перевыкладка по `AVG_KIND`, если сверка подтвердила другой вид (условно) | (0,5) | локально, сервер |
| 9. Закрытие дня | 1,25 | — |
| **Критический путь по плановой шкале** | **11,0** (с хвостами Д3а и 8а — до 12) | |

**Принятое основание «один день»** — то же, что в Д3а: живой темп Д1 (план в
9,5 ч по этой шкале выполнен примерно за 2 ч 20 мин по часам, с субагентами).
Плановая шкала остаётся честной, а день укладывается в рабочий день по живому
темпу. Если темп Д3а оказался вдвое медленнее Д1 или хуже, план Д3б
пересчитывается до его начала, и владелец получает строку с числами — или его
явное «да» на день длиннее.

**Черта отсечения — конец восьмого часа** плюс время хвостов Д3а. К ней зелёны
задачи 1–6 (по оценке — 7,25 ч). После черты порядок жёсткий: задача 7,
задача 8, при нужде 8а, задача 9. **В Д4 ничего не переносится:** Д4
начинается после тега `platform-d3b`.
- **Задача 5 или 6 не зелёна к черте.** Незелёная задача доделывается в тот же
  день сверх оценки. Если к десятому часу её всё ещё нет — выкладки нет, а
  владельцу в тот же час уходит строка: Д3б кончится завтрашним утром, Д4
  сдвигается на полдня. Решение о сдвиге — его.
- **Утром нет владельца.** Задачи 2–6 идут первыми; проба (задача 1, шаги 5–6)
  — когда он появится. Вечерние шаги 3–6 задачи 8 без него не делаются;
  тогда владельцу — строка о сдвиге, как выше.
- **Сверка подтвердила другой вид среднего** (задача 8, шаг 4) — строка 8а:
  правка одной константы, перевыкладка, повтор шагов 3–4 задачи 8.

## Global Constraints

Действуют Global Constraints Д3а целиком. Д3б добавляет:

- **Проверка на секреты** перед каждым коммитом задач 1, 7, 8, 9 — тем же
  `git diff --cached … | grep -E -i -f <шаблоны>`.
- **Охрана выхода в процессе** (Д3б-R6) ставится при старте службы и
  построителя. Список разрешённого: историан (`BDRV_HOST` на `BDRV_PORT`, а
  если порт не задан из-за экземпляра — на любом TCP-порту) и петля.
  OpenRouter добавит Д4.
- **Токены:** хеши sha256 в `${SECRETS_DIR}/core-tokens`; сами токены — файлы
  `<id>.data-token` только на сервере, по 48 шестнадцатеричных знаков
  (`openssl rand -hex 24`) — их ловит шаблон проверки Д2. В Д3б есть только
  `ops.data-token`, токены `student-NN.data-token` заводит Д4. В `.gitignore`
  — `core-tokens` и `*.data-token`.
- **Контракт:**
  - не больше 16 тегов, окно не длиннее 31 суток, до 288 точек (Д9), до 24
    тего-суток (для одного тега — 31); окно короче часа считается за час;
  - 60 вызовов за 5 минут на вызывающего;
  - строка тега — не длиннее 128 знаков, тело запроса — не больше 64 КиБ
    (считаются прочитанные байты, и тело без длины тоже).
- **Кэш:** закрытый период (конец старше 300 с) — 3600 с, открытый — 30 с,
  текущие значения — 15 с; «сейчас» — вниз до 30 с; до 256 записей, при
  переполнении уходит четверть с самым ранним сроком; single-flight. Ошибки и
  отказы в кэш не идут.
- **Каждый ответ говорит, что посчитано:** источник, вид среднего и σ, пояс,
  допущение смен, качество, возраст, доля достоверных. «Нет тега», «нет
  данных», «не в белом списке», «дискретный» — разными словами.
- **Событие на каждый вызов**, включая отказы, неверные аргументы (HTTP 422,
  любые ошибки аргументов MCP), неожиданные исключения (`outcome=error`) и
  служебные опросы. В событии — `requested` (сырой ввод), `tags`
  (канонические имена после разбора) и `sent` (имена из выполненного SQL, их
  возвращают ворота — и при ошибке после отправки). 401 и 413 пишутся
  строкой журнала без токена.
- **Крючки уровня приложения** (обработчик 422, ограничитель тела) ставятся
  только через `Role.install(app)` из Д3а; `app.py` в Д3б не меняется.
- **MCP:** `mcp==1.30.0`, `FastMCP(..., log_level="WARNING")`, инструменты
  возвращают `dict[str, Any]`, режим stateless с ответом JSON, точный маршрут
  `/mcp`. Аннотации аргументов свободные, их тип проверяет служба теми же
  моделями, что HTTP.

## Решения по умолчанию (Ruling)

**Д3б-R6 — охрана выхода.** Аудит-хук Python на события:
- `socket.connect`: адрес вне списка → `EgressDenied` до системного вызова.
  Имя хоста в кортеже разрешается через кэш, и проверяется каждый адрес; не
  разрешилось — отказ;
- `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`,
  `ctypes.dlopen` → `PermissionError`.

Правила `DOCKER-USER` не ставим: нужен `sudo`, они не переживают перезагрузку,
адреса OpenRouter меняются, рядом работает Dify.

**Остаточный риск:**
- на уровне сети из `pcbk-egress` открыт любой адрес (факт Д3а);
- DNS через 127.0.0.11 и UDP (`sendto`) не охраняются;
- нативный код с тома `/var/lib/pcbk-core` может обойти хук.

**Цена ошибки:** при взломе процесса атакующий получит сеть сервера.
Закрывается правилами `DOCKER-USER` — предложение владельцу на Д12, с его
`sudo`.

**Д3б-R7 — журнал доказывает белый список.**
- Ворота Д3а пускают в SQL только литералы-имена из `TagName IN (…)` белого
  списка; Д3б добавляет даты по `DATE_FMT` и закрытый набор ww-значений. Имена
  из выполненного SQL они возвращают (`GateResult.sent`, а при ошибке —
  `HistorianError.sent`).
- Чужое имя и шаблон с `TagName = '…'` ворота не пускают и считают в
  `refused_unlisted`.
- В событии — `requested`, `tags` и `sent`. Служебные вызовы пишутся с
  `caller="system"`, `channel="system"`; у каталога и полного снимка `Live`
  `sent` пуст, а `summary` — «без имён».
- Живая проверка сравнивает `sent` всех событий с `whitelist.txt` и делает
  отрицательный контроль: существующее имя вне списка и строка-инъекция.

**Д3б-R8 — вид среднего и σ** (О2; тип интерполяции задаётся и на уровне
тега):
1. Проба пробует сводку с `wwInterpolationType = 'STAIRSTEP'` (равенство, не
   `IN`). Если провайдер параметр принял, а `Average` совпал со средним по
   времени (ступенькой) при различимости `d ≥ 0,01`, параметр закрепляется в
   шаблоне: `PIN_INTERPOLATION = "STAIRSTEP"`, `AVG_KIND = "step"` для всех
   тегов.
2. Иначе смотрится распределение `InterpolationType` по `AnalogTag`. Вид
   объявляется, только если он один на всех и сводка с ним сошлась.
3. Иначе — `"unverified"`: вид среднего по тегам — вопрос в журнал долга.

**Цена ошибки:** неверное «взвешено по времени» в ответе. Ловит его вечерняя
сверка, которая печатает различимость.

**Д3б-R9 — мёртвые теги.**
- Строка `Live` с `Value IS NULL` → `no_value` («нет текущего значения»),
  время и качество сохраняются.
- Строка сводки, где `Average`, `Minimum` или `Maximum` — `NULL` или
  `PercentGood == 0`, → `no_data` («за период данных нет»).
- Нет строки — то же.

**Д3б-R10 — прочее.**
- Дискретный тег в `tag_period` → отказ по тегу со словами «наработка — в
  следующих слайсах».
- Имя агента в событии — `NULL`, решается в Д5 или Д7.
- «Последнее» за период выдаётся, только если у сводки есть столбец `Last` и
  он совпал с `Delta`.
- Метаданные тегов вне белого списка (описание, единица, шкала) в ответах не
  появляются.
- Аргументы MCP проверяет служба: схема сообщает типы и `maxItems` модели,
  а любой отказ (строка вместо списка, нет тегов, неизвестный период, 17
  тегов) идёт событием с русским текстом.
- `GET /mcp` → 405: клиент TS SDK понимает это как «поток не поддерживается».
- Сервер MCP строится один раз, в конструкторе роли; `session_manager.run()`
  идёт один раз, в `lifespan()` роли.

## Review Focus

1. **Мёртвый тег белого списка приходит строкой с `NULL`.** Ожидание:
   `no_value` / `no_data`, а не `ok` со значением `null`. Тесты — задача 5,
   `test_tag_now_null_value_is_no_value`, `test_tag_period_null_row_is_no_data`.
2. **Журнал пишет, что ушло в SQL, а не что прошло проверку**, — и при сбое
   после отправки. Иначе проверка «успеха 4» тавтологична. Тесты — задача 5,
   `test_events_requested_vs_sent`, `test_timeout_event_keeps_sent`; задача 4,
   `test_freshness_poll_writes_sent`.
3. **Отказ случился до кода службы:** строка вместо списка или 17 тегов по MCP,
   422 и битый JSON без токена по HTTP, тело без длины больше 64 КиБ.
   Ожидание: событие `refused` с русским текстом; без токена — 401 и строка
   журнала, без события; 413 — строкой журнала. Тесты — задача 6,
   `test_mcp_bad_arg_types_are_refused_events`; задача 5,
   `test_http_422_is_refused_event`, `test_bad_json_without_token_is_401`,
   `test_chunked_body_over_limit_is_413`.
4. **Журнал событий не пишется** — диск полон или свежий том принадлежит root.
   Ожидание: инструмент отвечает, строка «Служба данных» красная — «журнал
   событий не пишется»; на выкладке том пишется от uid 10003. Тесты — задача
   4, `test_events_disk_full_turns_health_red`; задача 5,
   `test_tool_answers_when_events_fail`; задача 7,
   `test_core_state_volume_writable`.
5. **MCP не ломает роль и чужие пути:** роль без приложения запускается, MCP
   строится один раз, `/mcp` — точный маршрут, Host `core:8000` проходит.
   Тесты — задача 6, `test_lifespan_without_app_runs`, `test_mcp_auth_host_and_get`.

---

## Карта файлов

```
core/verify/delta_stats.py            эталон сверки; службой не импортируется
core/verify/test_delta_stats.py
core/pcbk_core/egress.py              охрана выхода (аудит-хук)
core/pcbk_core/secrets.py             + TokenTable
core/pcbk_core/settings.py            + TOKENS_FILE, DB_PATH
core/pcbk_core/main.py                + охрана выхода, токены
core/pcbk_core/data/sql.py            + DATE_FMT, PIN_INTERPOLATION, SUMMARY_COLUMNS, HAS_LAST, lit_dt, summary_sql, allowed_literal
core/pcbk_core/data/periods.py        round_now, готовые периоды и смены
core/pcbk_core/data/budget.py         Limits, cost_tag_days, check_budget
core/pcbk_core/data/cache.py          TTLCache, SingleFlight, ttl_for, cache_key (round_now — из periods)
core/pcbk_core/data/catalog.py        + lookup, search, похожие
core/pcbk_core/data/events.py         CallEvent, EventLog
core/pcbk_core/data/service.py        DataService: три инструмента
core/pcbk_core/data/http_api.py       POST /api/data/{tool}; 422, 401, 413
core/pcbk_core/data/mcp_server.py     MCP Streamable HTTP
core/pcbk_core/data/__init__.py       DataRole: + события, служба, роутер, install, MCP
core/pcbk_core/data/gate.py           + clear_pause; ворота пускают даты и ww-значения через allowed_literal
core/pcbk_core/data/build_whitelist.py  + охрана выхода
core/Dockerfile                       + каталог /var/lib/pcbk-core от uid 10003
compose.yaml                          core :d3b, секрет core-tokens, том pcbk-core-data
.gitignore                            + core-tokens, *.data-token
deploy/README.md                      + токены, журнал событий, MCP
tests/integration/conftest.py         + тестовый токен; http_host(..., token=)
tests/integration/test_core.py        + вызовы через egress, 401, журнал
tests/integration/test_edge.py        IMAGES: core :d3b
docs/checks/D3b.md, docs/checks/D3b/*.png  журнал и снимок страницы после выкладки
```

Имена: образ `pcbk-reserve/core:d3b`; том `pcbk-core-data` →
`/var/lib/pcbk-core`; секрет Compose `core-tokens` → `/run/secrets/core-tokens`;
пробный образ `pcbk-probe/tds:d3b`.

---

### Task 0: Хвосты Д3а

- [ ] **Step 1:** Всё, что черта Д3а перенесла сюда: второй раунд критика
  (тогда слияние и тег `platform-d3a` — после него), проверка чистым клоном,
  факты о сети выхода (задача 9 Д3а, шаг 5 — тогда они делаются в задаче 8,
  шаг 6). Expected: у каждого хвоста вердикт в `docs/checks/D3a.md`; ветка Д3б
  — от `main` после тега.

---

### Task 1: Эталон сверки и проба — запросы 7–15, режим `verify`

**Нужен владелец:** шаг 6 читает производственные данные. Проба закрывает О4
(формат даты) и О2 (столбцы сводки, вид среднего и σ, интерполяция). Кроме
того, она выбирает тег для вечерней сверки и имя для отрицательного контроля.
Эталон `delta_stats` — в репозитории с тестами. Скрипт пробы `probe_b.py` —
самостоятельный, в `$JOB` этой сессии: на исходники пробы Д3а он не
опирается.

**Files:**
- Create: `core/verify/delta_stats.py`, `core/verify/test_delta_stats.py`, `docs/checks/D3b.md`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Point: t: datetime; v: float | None; good: bool`
  - `@dataclass(frozen=True) class Stats: n: int; min: float; max: float; last: float; mean_arith: float; mean_step: float; mean_linear: float; std_arith: float; std_step: float; covered_s: float`
  - `delta_stats(points: Sequence[Point], start: datetime, end: datetime) -> Stats` —
    точки обрезаются по `[start, end]`. Ступенька: значение держится до
    следующей точки, у последней — до `end`. Линейная: трапеция между
    соседними хорошими точками, хвост — ступенька. Плохая точка
    (`good=False` или `v is None`) открывает разрыв; разрывы не входят ни в
    средние, ни в `covered_s`. `std_arith` — выборочное (n − 1), `std_step` —
    по времени вокруг `mean_step`. Хороших точек нет → `ValueError`. Только
    stdlib, импорта `pcbk_core` нет.
  - `gap(a: float, b: float, scale: float) -> float` = `|a − b| / max(scale, |b|, 1e-9)`
    — без деления на ноль у ровного сигнала.
  - `discrimination(st: Stats) -> float` = `gap(st.mean_step, st.mean_arith, st.max − st.min)`.
  - Образ `pcbk-probe/tds:d3b` на сервере: режимы `morning-b` и
    `verify --date-fmt A|B`.
  - Файлы `/opt/pcbk-reserve/probe-out/check-tag` и `unlisted-tag` (`0400`,
    имена только там).
  - Решения для задач 2 и 5 — таблица шага 7.

- [ ] **Step 1: Write the failing tests**

```python
# core/verify/test_delta_stats.py
S, H = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 7)

def pts(*pairs):
    return [Point(S + timedelta(minutes=m), v, True) for m, v in pairs]

def test_step_vs_arith_discriminates():
    st = delta_stats(pts((0, 0.0), (50, 10.0)), S, H)
    assert st.mean_step == pytest.approx(10 * 10 / 60) and st.mean_arith == pytest.approx(5.0)
    assert st.mean_linear == pytest.approx(5 * 50 / 60 + 10 * 10 / 60)
    assert (st.min, st.max, st.last, st.n) == (0.0, 10.0, 10.0, 2)
    assert discrimination(st) == pytest.approx((5.0 - 10 / 6) / 10)

def test_constant_signal_and_zero_scale_gap():
    st = delta_stats(pts((0, 7.0), (20, 7.0), (40, 7.0)), S, H)
    assert st.mean_step == st.mean_linear == st.mean_arith == 7.0 and st.std_step == 0.0
    assert discrimination(st) == 0.0 and gap(7.0, 7.0 + 1e-15, 0.0) < 1e-12

def test_bad_points_open_gaps():
    ps = pts((0, 1.0)) + [Point(S + timedelta(minutes=30), None, False)] + pts((45, 3.0))
    st = delta_stats(ps, S, H)
    assert st.covered_s == pytest.approx(45 * 60) and st.mean_step == pytest.approx((30 + 45) / 45)
    assert (st.mean_arith, st.n) == (pytest.approx(2.0), 2)

def test_points_outside_window_ignored_and_empty_raises():
    assert delta_stats(pts((-10, 100.0), (0, 1.0), (70, 100.0)), S, H).max == 1.0
    with pytest.raises(ValueError):
        delta_stats([Point(S, None, False)], S, H)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core/verify && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — `ModuleNotFoundError: delta_stats`

- [ ] **Step 3: Implement `delta_stats.py` по интерфейсу**

- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS (4 теста).

- [ ] **Step 5: Пробный образ `pcbk-probe/tds:d3b` (в `$JOB/probe-b/`)**

`FROM pcbk-probe/tds:1.17.1` — образ Д3а с python-tds; он есть локально и на
сервере. Сверху — копия `core/verify/delta_stats.py` и самостоятельный
`probe_b.py` с режимами `morning-b` и `verify`,
`ENTRYPOINT ["python", "/app/probe_b.py"]`. Правила:
- учётные данные — из `/run/secrets/bdrv.env` (разбор как у `read_env_file`),
  соединение — как у службы;
- печатаются только агрегаты, имён и значений нет;
- литерал даты — собственная функция пробы, не `sql.py`;
- окно — последний полный час по `GETDATE()`; если сегодня день равен месяцу
  (10.10), — тот же час вчера: такой день форматы не различает.

| № | Запрос | Что печатается |
|---|---|---|
| 7–8 | `SELECT DateTime, Value, Quality FROM AnalogHistory WHERE TagName = '<t>' AND DateTime >= '<a>' AND DateTime <= '<b>' AND wwRetrievalMode = 'Delta'` для самого свежего аналогового тега участка — формат **A** `YYYY-MM-DD HH:MM:SS.000` и **B** `YYYYMMDD HH:MM:SS.000` | на формат: «внутри окна» / «вне окна» / «пусто» / «ошибка: <класс>» (О4) |
| 9–12 | тот же `Delta` для ещё четырёх самых свежих аналоговых тегов участка, прошедший формат | `discrimination` каждого; доля `Quality = 0` |
| 13 | `SELECT * FROM AnalogSummaryHistory WHERE TagName = '<лучший по d>' AND StartDateTime >= '<a>' AND EndDateTime <= '<b>' AND wwCycleCount = 1 AND wwInterpolationType = 'STAIRSTEP'` | принят ли параметр; имена столбцов; `gap` у `Average` к трём средним, у `StdDev` — к двум σ; `Minimum`/`Maximum` = min/max; `Last` = последнему, если столбец есть |
| 13б | то же без `wwInterpolationType`, если 13 дал ошибку | то же |
| 14 | `SELECT InterpolationType, COUNT(*) FROM AnalogTag GROUP BY InterpolationType` | распределение (только числа) или класс ошибки |
| 15 | `catalog_sql()` Д3а | первое имя участка вне `whitelist.txt` (смонтирован `:ro`) — в `/out/unlisted-tag`; лучший по `d` тег — в `/out/check-tag`; на экран — ничего |

Режим `verify --date-fmt A|B` читает из stdin ответ `tag_period`. Он
запрашивает свои часы и сырой `Delta` по тегу ответа за его период, с
`AND wwCycleCount = 200000`, и печатает:
- `bounds`: 06/14/22, длина 8 ч, пояс ответа равен своему;
- `d` и `gap` к трём средним и двум σ;
- вид: «подтверждён <вид>», только если `d ≥ 0,01`, расхождение с ним не
  больше `1e-3` и хотя бы в 10 раз меньше, чем со вторым; иначе «вид не
  различим на этом теге» или «не сошёлся»;
- `min`, `max` (`gap ≤ 1e-9`);
- `last`, если поле есть;
- `rows`: порядок числа строк и «обрезано», если их 200000;
- `outside`: число строк `Delta` вне `[start, end]` — ожидание 0.

- [ ] **Step 6: Проба `morning-b` (при владельце)**

`install -d -m 0700 /opt/pcbk-reserve/probe-out`; перенос образа (`docker save … | $SSH …`,
сверка `RootFS`); затем
`$SSH 'docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro -v /opt/pcbk-reserve/data/whitelist.txt:/data/whitelist.txt:ro -v /opt/pcbk-reserve/probe-out:/out pcbk-probe/tds:d3b morning-b' > "$JOB/probe-b.json"`
(сеть — как в Д3а). `grep -E -i -f <шаблоны> "$JOB/probe-b.json"` — пусто.
Expected: ответ на каждый запрос; оба файла в `probe-out` есть (`0400`).

- [ ] **Step 7: Решения по пробе**

| Итог | Решение | По умолчанию |
|---|---|---|
| О4: B «внутри окна» | `DATE_FMT = "%Y%m%d %H:%M:%S.000"` | `"%Y-%m-%d %H:%M:%S.000"` (работал 08.09) |
| О4: только A «внутри окна» | `DATE_FMT = "%Y-%m-%d %H:%M:%S.000"` | — |
| О4: ни один | стоп: периоды не строятся; вопрос владельцу, строка о сдвиге | — |
| 13 принят, `d ≥ 0,01`, `Average` ближе к ступеньке хотя бы в 10 раз и `gap ≤ 1e-3` | `PIN_INTERPOLATION = "STAIRSTEP"`, `AVG_KIND = "step"` | `PIN_INTERPOLATION = None`, `AVG_KIND = "unverified"` |
| 13 не принят; 14 — один тип на всех; 13б сошёлся с ним так же | `AVG_KIND` = этот вид | — |
| иначе | `AVG_KIND = "unverified"`, вопрос в журнал долга | — |
| `StdDev` — так же, порог `1e-2` | `STD_KIND` = `"step"` или `"arith"` | `"unverified"` |
| есть `Last` и `LastDateTime`, `Last` = последнему `Delta` | `HAS_LAST = True`, столбцы — в `SUMMARY_COLUMNS` | `False` |

Expected: вердикты и таблица с принятыми значениями — в `docs/checks/D3b.md`.

- [ ] **Step 8: Commit** (после проверки на секреты) — `git add core/verify/ docs/checks/D3b.md && git commit -m "Д3б: эталон сверки и проба — формат даты, вид среднего, интерполяция"`.

---

### Task 2: Шаблоны с датами и периоды

**Files:**
- Modify: `core/pcbk_core/data/sql.py`, `core/pcbk_core/data/__init__.py`
  (ворота получают `literal_ok=allowed_literal`), `core/tests/fakes.py` (сводка)
- Create: `core/pcbk_core/data/periods.py`
- Test: `core/tests/test_sql.py` (+3), `core/tests/test_gate.py` (+1), `core/tests/test_periods.py`

**Interfaces:**
- Consumes: `lit_name`, `names_in`, `SAFE_NAME`, `MAX_NAMES` — Д3а; решения
  задачи 1.
- Produces (`sql.py`):
  - `DATE_FMT: str`, `PIN_INTERPOLATION: str | None`, `HAS_LAST: bool`;
    `SUMMARY_COLUMNS = ("Minimum", "Maximum", "Average", "StdDev", "PercentGood")`
    плюс `("Last", "LastDateTime")` при `HAS_LAST`
  - `lit_dt(dt: datetime) -> str` — только наивное время, иначе `ValueError`;
    секунды без долей
  - `summary_sql(names, start, end) -> str` =
    `SELECT TagName, <SUMMARY_COLUMNS> FROM AnalogSummaryHistory WHERE TagName IN (…) AND StartDateTime >= <lit_dt> AND EndDateTime <= <lit_dt> AND wwCycleCount = 1`,
    плюс `AND wwInterpolationType = '<PIN_INTERPOLATION>'`, если задан
  - `WW_CONSTANTS = frozenset({"STAIRSTEP"})`;
    `allowed_literal(text: str) -> bool` — дата по `DATE_FMT` или значение из
    `WW_CONSTANTS`. `DataRole` передаёт её воротам как `literal_ok`; прочие
    литералы по-прежнему должны быть именами из `TagName IN (…)`
- Produces (`periods.py`):
  - `round_now(now: datetime) -> datetime` — вниз до 30 с (ею же пользуется
    кэш задачи 3);
  - `SHIFT_STARTS = (6, 14, 22)` (допущение, решение №6);
    `PRESETS = ("last_hour", "last_8h", "last_24h", "current_shift", "prev_shift", "today", "yesterday", "last_7d", "last_30d")`;
    `MIN_PERIOD = timedelta(seconds=60)`; `EARLIEST = datetime(2000, 1, 1)`
  - `@dataclass(frozen=True) class Period: start: datetime; end: datetime; preset: str | None; shift_assumed: bool; clamped: bool`
  - `class PeriodError(ValueError)`
  - `resolve_period(now: datetime, offset: timedelta, preset: str | None = None, start: str | None = None, end: str | None = None) -> Period`:
    - «сейчас» — вниз до 30 с; способ задать период — ровно один;
    - ISO 8601 с поясом переводится в местное через `offset`, без пояса —
      уже местное;
    - конец позже «сейчас» → «сейчас» и `clamped=True`;
    - начало раньше `EARLIEST`, конец не позже начала или период короче
      `MIN_PERIOD` → `PeriodError` со словами;
    - ночная смена переходит через полночь.
- Produces (`fakes.py`): `SUMMARY = {"20FAKE_001_PV": {"Minimum": 10.0, "Maximum": 15.0, "Average": 12.4, "StdDev": 1.1, "PercentGood": 100.0, "Last": 12.5, "LastDateTime": datetime(2026, 10, 1, 5, 59, 40)}, "20FAKE_002_SP": {"Minimum": None, "Maximum": None, "Average": None, "StdDev": None, "PercentGood": 0.0, "Last": None, "LastDateTime": None}}`;
  `FakeHistorian` отвечает на `AnalogSummaryHistory … IN (…)` строками в
  порядке `SUMMARY_COLUMNS`, только для имён из литералов; поле `summary`

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_sql.py (+)
T6, T14 = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 14)

def test_summary_template_rules():
    s = summary_sql(["20FAKE_001_PV"], T6, T14)
    assert not re.search(r"\?|%s|@|;|\bLIKE\b|\bOR\b", s) and not re.search(r"\bww\w+\s+IN\b", s)
    assert "WHERE TagName IN ('20FAKE_001_PV') AND StartDateTime >= " in s and "wwCycleCount = 1" in s
    assert ("wwInterpolationType = 'STAIRSTEP'" in s) is (PIN_INTERPOLATION == "STAIRSTEP")
    assert "wwResolution" not in s and names_in(s) == ("20FAKE_001_PV",)

def test_allowed_literals():
    assert allowed_literal(lit_dt(T6).strip("'")) and allowed_literal("STAIRSTEP")
    assert not allowed_literal("16FAKE_009_PV") and not allowed_literal("Delta")

# core/tests/test_gate.py (+)
@pytest.mark.anyio
async def test_gate_passes_summary_literals_only():
    g = HistorianGate(FakeHistorian(), allowed=WHITELIST, literal_ok=allowed_literal)
    assert (await g.run([summary_sql(["20FAKE_001_PV"], T6, T14)])).sent == ("20FAKE_001_PV",)
    with pytest.raises(GateRefused):
        await g.run(["SELECT TagName FROM AnalogHistory WHERE TagName = '20FAKE_001_PV' AND wwRetrievalMode = 'Delta'"])

def test_date_literal():
    want = {"%Y%m%d %H:%M:%S.000": "'20261001 06:00:00.000'",
            "%Y-%m-%d %H:%M:%S.000": "'2026-10-01 06:00:00.000'"}[DATE_FMT]
    assert lit_dt(datetime(2026, 10, 1, 6, 0, 0, 999_999)) == want
    with pytest.raises(ValueError):
        lit_dt(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))

# core/tests/test_periods.py
N, OFF = datetime(2026, 10, 1, 12, 0, 45), timedelta(hours=5)

def p(now=N, **kw):
    return resolve_period(now, OFF, **kw)

def test_presets_basic():
    assert (p(preset="last_hour").start, p(preset="last_hour").end) == \
           (datetime(2026, 10, 1, 11, 0, 30), datetime(2026, 10, 1, 12, 0, 30))
    assert (p(preset="prev_shift").start, p(preset="prev_shift").end) == (datetime(2026, 9, 30, 22), datetime(2026, 10, 1, 6))
    assert (p(preset="yesterday").start, p(preset="today").start) == (datetime(2026, 9, 30), datetime(2026, 10, 1))
    assert p(preset="prev_shift").shift_assumed and not p(preset="last_hour").shift_assumed

def test_shift_presets_cross_midnight():
    at = lambda h, m, preset: p(now=datetime(2026, 10, 1, h, m), preset=preset)
    assert (at(5, 59, "current_shift").start, at(5, 59, "prev_shift").start, at(5, 59, "prev_shift").end) == \
           (datetime(2026, 9, 30, 22), datetime(2026, 9, 30, 14), datetime(2026, 9, 30, 22))
    assert (at(23, 10, "current_shift").start, at(6, 10, "prev_shift").start) == \
           (datetime(2026, 10, 1, 22), datetime(2026, 9, 30, 22))

def test_period_shorter_than_minute_refused():
    with pytest.raises(PeriodError, match="короче минуты"):
        p(now=datetime(2026, 10, 1, 6, 0, 20), preset="current_shift")

def test_custom_period_offset_and_clamp():
    q = p(start="2026-10-01T01:00:00+00:00", end="2026-10-01T03:00:00+00:00")
    assert (q.start, q.end, q.clamped) == (datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 8), False)
    assert p(start="2026-10-01 10:00", end="2026-10-01 13:00").clamped is True

@pytest.mark.parametrize("kw", [{"start": "2026-10-01 10:00", "end": "2026-10-01 09:00"},
                                {"start": "1999-12-31 00:00", "end": "2000-01-02 00:00"},
                                {"start": "вчера", "end": "сегодня"}, {"preset": "last_year"}, {},
                                {"preset": "prev_shift", "start": "2026-10-01 10:00", "end": "2026-10-01 11:00"}])
def test_bad_periods(kw):
    with pytest.raises(PeriodError):
        p(**kw)
```

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_sql.py tests/test_periods.py`. Expected: FAIL — нет `summary_sql`, `periods`.

- [ ] **Step 3: Implement по интерфейсам**

- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных: сводка с закреплённой интерполяцией, формат даты по пробе, смены через полночь"`.

---

### Task 3: Охрана выхода, токены, бюджет, кэш, single-flight

**Files:**
- Create: `core/pcbk_core/egress.py`, `core/pcbk_core/data/budget.py`, `core/pcbk_core/data/cache.py`
- Modify: `core/pcbk_core/secrets.py` (`TokenTable`), `core/pcbk_core/settings.py`
  (`TOKENS_FILE="/run/secrets/core-tokens"`, `DB_PATH="/var/lib/pcbk-core/core.db"`),
  `core/pcbk_core/main.py` (охрана при старте), `core/pcbk_core/data/build_whitelist.py`
  (охрана при старте), `core/tests/{helpers,conftest}.py` (`run_python`, `listeners`)
- Test: `core/tests/test_egress.py`, `core/tests/test_budget.py`, `core/tests/test_cache.py`,
  `core/tests/test_skeleton.py` (+ токены)

**Interfaces:**
- Consumes: `SlidingWindow` — Д3а; `BdrvConfig`.
- Produces:
  - `class EgressDenied(PermissionError)`;
    `install_egress_guard(allowed: Sequence[tuple[str, int | None]], allowed_nets: Sequence[str] = ("127.0.0.0/8", "::1/128")) -> None`
    — порт `None` значит «любой TCP-порт этого хоста» (адрес с экземпляром,
    Д3а):
    - `sys.addaudithook`; повторная установка → `RuntimeError`;
    - `socket.connect` (AF_INET/AF_INET6): имя хоста в кортеже разрешается
      через кэш (повтор при промахе — не чаще раза в 60 с), проверяется
      каждый адрес; вне `allowed` и `allowed_nets` или не разрешилось →
      `EgressDenied("выход запрещён: <адрес>:<порт>")`;
    - `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`,
      `ctypes.dlopen` → `PermissionError("запуск процессов и нативный код запрещены")`;
    - `egress_guard_installed() -> bool`.
  - `main`: после `setup_logging` — `install_egress_guard([(cfg.host.split("\\")[0], cfg.port)])` и строка
    журнала «охрана выхода: разрешено 1 направление»; то же в `build_whitelist.main`.
  - `class TokenTable`: `from_file(path) -> TokenTable`;
    `caller(bearer: str | None) -> str | None`; `ids: frozenset[str]`. Строка
    файла — `<id> <sha256 hex>`, id — `[a-z0-9-]{1,32}`; сверка
    `hmac.compare_digest` по всем записям.
  - `budget.py`:
    - `@dataclass(frozen=True) class Limits: max_tags=16; max_window_days=31.0; max_points=288; max_tag_days=24.0; single_tag_days=31.0; min_window=timedelta(hours=1)`;
      `LIMITS`;
    - `cost_tag_days(n, start, end) -> float`;
    - `check_budget(n, start, end, limits=LIMITS) -> str | None` — первая
      нарушенная проверка по порядку: «за один вызов — не больше 16 тегов»;
      «период длиннее 31 суток»; «запрос стоит X тего-суток при пределе 24 (для
      одного тега — 31): сократите период или число тегов» (X — два знака
      после запятой);
    - `PER_CALLER_LIMIT = 60`.
  - `cache.py`:
    - `TTL_NOW_S = 15`, `TTL_OPEN_S = 30`, `TTL_CLOSED_S = 3600`,
      `CLOSED_AFTER_S = 300`, `NOW_ROUND_S = 30`, `MAX_ENTRIES = 256`;
    - `round_now` — из `periods.py` (задача 2);
      `ttl_for(tool: Literal["tag_now", "tag_period"], end: datetime | None, now: datetime) -> float`;
      `cache_key(tool, tags, start, end) -> str` (sha256; порядок тегов не
      важен);
    - `class TTLCache: get(key, now) / put(key, value, ttl, now) / __len__` —
      при переполнении сначала протухшие, затем ⌈n/4⌉ с самым ранним сроком;
    - `class SingleFlight: async run(key, factory) -> tuple[T, bool]` —
      `(результат, shared)`; ошибка доходит до всех; ключ снимается по
      завершении.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_egress.py — хук снять нельзя, поэтому каждый случай — в подпроцессе
def guard_py(body, a, allowed_nets="()"):
    return run_python(f"import socket, subprocess, time\n"
                      f"from pcbk_core.egress import install_egress_guard, EgressDenied\n"
                      f"install_egress_guard([('localhost', {a})], allowed_nets={allowed_nets})\n" + body)

def test_guard_blocks_other_destinations_fast(listeners):
    a, b = listeners
    out = guard_py(f"""
socket.create_connection(('127.0.0.1', {a}), 1).close(); print('OK')
for dest in (('127.0.0.1', {b}), ('192.0.2.1', 443)):
    t = time.monotonic()
    try: socket.create_connection(dest, 5); print('OPEN')
    except EgressDenied: print('DENIED', time.monotonic() - t < 0.5)
""", a)
    assert out.split() == ["OK", "DENIED", "True", "DENIED", "True"]

def test_guard_checks_hostname_tuple_in_plain_connect(listeners):   # имя приходит в хук неразрешённым
    a, b = listeners
    out = guard_py(f"""
s = socket.socket(); s.connect(('localhost', {a})); s.close(); print('OK')
for dest in (('localhost', {b}), ('nonexistent.invalid', 443)):   # не разрешилось — тоже отказ
    s = socket.socket()
    try: s.connect(dest); print('OPEN')
    except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["OK", "DENIED", "DENIED"]

def test_guard_blocks_processes(listeners):
    out = guard_py("""
try: subprocess.run(['true']); print('RAN')
except PermissionError: print('BLOCKED')
""", listeners[0])
    assert out.strip() == "BLOCKED"

def test_token_table(tmp_path):
    t = TokenTable.from_file(write(tmp_path, f"ops {sha('tok-b')}\nstudent-01 {sha('tok-a')}\n"))
    assert (t.caller("tok-a"), t.caller("tok-b"), t.caller("x"), t.caller(None)) == ("student-01", "ops", None, None)
    with pytest.raises(ValueError):
        TokenTable.from_file(write(tmp_path, "Student_01 abc\n"))

# core/tests/test_budget.py
T6 = datetime(2026, 10, 1, 6)
def test_budget_contract():
    assert check_budget(16, T6, T6 + timedelta(days=1)) is None
    assert "16 тегов" in check_budget(17, T6, T6 + timedelta(hours=1))
    assert check_budget(1, T6, T6 + timedelta(days=31)) is None
    assert "31 суток" in check_budget(1, T6, T6 + timedelta(days=31, seconds=1))
    assert check_budget(2, T6, T6 + timedelta(days=12)) is None
    assert "24,08" in check_budget(2, T6, T6 + timedelta(days=12, hours=1))
    assert cost_tag_days(3, T6, T6 + timedelta(minutes=10)) == pytest.approx(3 / 24)

# core/tests/test_cache.py
def test_round_now_and_ttl():
    now = datetime(2026, 10, 1, 12, 0)
    assert round_now(datetime(2026, 10, 1, 12, 0, 59)) == datetime(2026, 10, 1, 12, 0, 30)
    assert (ttl_for("tag_now", None, now), ttl_for("tag_period", now - timedelta(seconds=301), now),
            ttl_for("tag_period", now - timedelta(seconds=299), now)) == (15, 3600, 30)
    assert cache_key("tag_now", ["B", "A"], None, None) == cache_key("tag_now", ["A", "B"], None, None)

def test_ttl_cache_evicts_quarter_soonest():
    c = TTLCache(max_entries=8)
    for i in range(8):
        c.put(f"k{i}", i, ttl=10 + i, now=0.0)
    c.put("k8", 8, ttl=100, now=1.0)
    assert len(c) == 7 and c.get("k1", 1.0) is None and c.get("k2", 1.0) == 2
    assert c.get("k2", 12.5) is None

@pytest.mark.anyio
async def test_single_flight_shares_and_clears_on_error():
    sf, calls = SingleFlight(), []
    async def ok():
        calls.append(1); await anyio.sleep(0.05); return "r"
    rs = await asyncio.gather(*(sf.run("k", ok) for _ in range(10)))
    assert len(calls) == 1 and sorted(s for _, s in rs) == [False] + [True] * 9
    async def boom():
        calls.append(1); await anyio.sleep(0.01); raise HistorianError("connect", "x")
    rs = await asyncio.gather(*(sf.run("e", boom) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, HistorianError) for r in rs) and len(calls) == 2
    assert (await sf.run("e", ok))[1] is False and len(calls) == 3
```

`run_python(code) -> str` (подпроцесс `sys.executable -c`, `cwd=core`) и
фикстура `listeners` (два слушающих сокета на 127.0.0.1) — в `helpers.py` и
`conftest.py`; `sha`, `write` — там же.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_egress.py tests/test_budget.py tests/test_cache.py tests/test_skeleton.py`. Expected: FAIL.

- [ ] **Step 3: Implement по интерфейсам.** Семейства, кроме AF_INET и AF_INET6
  (сокеты-пары asyncio — AF_UNIX), хук пропускает.

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Серверный слой: охрана выхода и запуска процессов, токены хешами, бюджет 16/31/24, кэш и single-flight"`.

---

### Task 4: Разбор имён, поиск и журнал событий

**Files:**
- Modify: `core/pcbk_core/data/catalog.py`, `core/pcbk_core/data/__init__.py`
  (`DataRole`: события, здоровье), `core/tests/helpers.py`
- Create: `core/pcbk_core/data/events.py`
- Test: `core/tests/test_catalog.py` (+), `core/tests/test_events.py`, `core/tests/test_role.py` (+)

**Interfaces:**
- Consumes: `Catalog`, `DataRole`, `GateResult.sent` — Д3а.
- Produces (`catalog.py`):
  - `norm(text) -> str` — нижний регистр, «ё» → «е», пробелы схлопнуты
  - `@dataclass(frozen=True) class Lookup: status: Literal["ok", "unlisted", "unknown"]; info: TagInfo | None; similar: tuple[str, ...]`:
    `info` есть **только** при `ok`; у `unknown` `similar` — до 5 имён
    белого списка с той же основой (имя без последнего `_ХВОСТА`) или
    содержащих введённое
  - `Catalog.lookup(raw: str) -> Lookup` — `strip()`, регистр не важен;
    каноническое имя берётся из каталога
  - `@dataclass(frozen=True) class SearchResult: matches: tuple[TagInfo, ...]; similar: bool; total: int`;
    `Catalog.search(query: str, limit: int = 10) -> SearchResult`:
    - только теги белого списка; все слова запроса (после `norm`) должны
      найтись в `norm(имя + " " + описание)`;
    - порядок: точное имя, имя с начала, затем по имени;
    - ничего не нашлось → `similar=True`: теги хотя бы с одним словом, по
      убыванию числа слов; если запрос похож на имя (`SAFE_NAME` и есть `_`),
      — ещё теги с той же основой.
- Produces (`events.py`):
  - `Outcome = Literal["ok", "partial", "refused", "budget", "rate", "busy", "timeout", "unavailable", "error"]`;
    `Channel = Literal["mcp", "http", "system"]`; `CacheState = Literal["hit", "miss", "shared", "none"]`
  - `@dataclass(frozen=True) class CallEvent: ts: datetime; caller: str; agent: str | None; channel: Channel; tool: str; requested: tuple[str, ...]; tags: tuple[str, ...]; sent: tuple[str, ...]; start: datetime | None; end: datetime | None; outcome: Outcome; summary: str; cost: float; cache: CacheState; rows: int; duration_ms: int`:
    `requested` — сырой ввод, обрезается до 16 строк по 128 знаков; `tags` —
    канонические имена после разбора со статусами `ok`, `no_value`,
    `no_data` (их слой событий мнемосхем М4 ставит «у своего тега»; есть и
    у попадания в кэш); `sent` — имена из выполненного SQL
  - `class EventLog`:
    - `__init__(self, path: str)` — SQLite; соединение открывается на каждую
      операцию, как журнал сторожа Д1: из потоков TestClient и uvicorn нет
      ошибки `check_same_thread`. WAL включается при создании; таблица
      `agent_events` (`requested`, `tags`, `sent` — JSON);
    - `write(self, e: CallEvent) -> None` — при ошибке `consecutive_failures += 1`,
      `last_error = <класс и текст>`, исключение дальше; удача сбрасывает
      счётчик;
    - `writable(self) -> bool` — на новом соединении, как у журнала сторожа
      Д1: `BEGIN IMMEDIATE`, `INSERT`, `ROLLBACK`;
    - `recent(self, limit: int = 50) -> list[CallEvent]` — новые сверху.
- `DataRole` (правки):
  - конструктор получает `tokens: TokenTable | None = None`; поле
    `events: EventLog(settings.DB_PATH)`;
  - `refresh_catalog` и `poll_freshness` пишут событие: `caller="system"`,
    `channel="system"`, `tool="catalog"`/`"freshness"`, `sent` из
    `GateResult`. У каталога `summary` начинается с «без имён»; исход — `ok`
    или код ошибки. Ошибка записи события опрос не ломает;
  - `health()` дополнительно: `not events.writable()` или
    `consecutive_failures > 0` → `(False, "журнал событий не пишется: " + last_error[:60])`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_catalog.py (+)
@pytest.fixture
def cat():
    return Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST)

def test_lookup_statuses_and_no_metadata_outside_list(cat):
    assert (cat.lookup("  20fake_001_pv ").status, cat.lookup("  20fake_001_pv ").info.name) == ("ok", "20FAKE_001_PV")
    unl = cat.lookup("20FAKE_005_LMN")
    assert (unl.status, unl.info) == ("unlisted", None)
    miss = cat.lookup("20FAKE_001")
    assert miss.status == "unknown" and miss.info is None and "20FAKE_001_PV" in miss.similar

def test_search_words_yo_all_words_and_similar(cat):
    assert [t.name for t in cat.search("РАСХОД массы").matches] == ["20FAKE_001_PV", "20FAKE_002_SP"]
    assert [t.name for t in cat.search("емкости").matches] == ["20FAKE_003_PV"]    # в описании — «ёмкости»
    r = cat.search("расход клапан")
    assert r.similar and {"20FAKE_001_PV", "25FAKE_007_CLS"} <= {t.name for t in r.matches}
    assert cat.search("вне участка").matches == () and cat.search("системный").matches == ()

# core/tests/test_events.py
E = CallEvent(ts=datetime(2026, 10, 1, 7, tzinfo=timezone.utc), caller="student-01", agent=None, channel="http",
              tool="tag_now", requested=("20FAKE_001_PV", "X' OR 1=1--"), tags=("20FAKE_001_PV",),
              sent=("20FAKE_001_PV",),
              start=None, end=None, outcome="partial", summary="…", cost=0.0, cache="miss", rows=1, duration_ms=12)

def test_event_roundtrip_newest_first(tmp_path):
    log = EventLog(str(tmp_path / "core.db"))
    log.write(E); log.write(replace(E, outcome="refused", sent=()))
    assert [e.outcome for e in log.recent()] == ["refused", "partial"] and log.recent()[1] == E
    assert log.writable() is True

def test_readonly_db_is_not_writable(tmp_path):
    d = tmp_path / "ro"; d.mkdir()
    log = EventLog(str(d / "core.db"))
    (d / "core.db").chmod(0o400); d.chmod(0o500)
    assert log.writable() is False

# core/tests/test_role.py (+)
async def test_freshness_poll_writes_sent():                                    # Review Focus 2
    role, _ = make_role()
    await role.refresh_catalog()
    await role.poll_freshness()
    fresh, catalog = role.events.recent()[:2]
    assert (fresh.caller, fresh.channel, fresh.tool) == ("system", "system", "freshness")
    assert set(fresh.sent) == {"20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP"} <= WHITELIST
    assert (catalog.tool, catalog.sent) == ("catalog", ()) and catalog.summary.startswith("без имён")

def test_events_disk_full_turns_health_red(monkeypatch):                       # Review Focus 4
    role, _ = make_role()
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(role.events, "_insert", full)
    with pytest.raises(sqlite3.OperationalError):
        role.events.write(E)
    ok, detail = role.health()
    assert ok is False and detail.startswith("журнал событий не пишется") and "disk is full" in detail
```

`_insert` — внутренний метод `EventLog`, которым пользуется `write`; тест
подменяет его, `write` остаётся настоящим. `make_role` получает `db_dir`
(по умолчанию — новый временный каталог) для `DB_PATH` и тестовый
`TokenTable` с токеном `student-01`.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_catalog.py tests/test_events.py tests/test_role.py`. Expected: FAIL.

- [ ] **Step 3: Implement по интерфейсам**

- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных: разбор имён и поиск по белому списку, журнал событий с requested и sent, служебные опросы в журнале"`.

---

### Task 5: Инструменты и HTTP

**Files:**
- Create: `core/pcbk_core/data/service.py`, `core/pcbk_core/data/http_api.py`
- Modify: `core/pcbk_core/data/__init__.py` (`DataRole.service`, `router`,
  `install` — обработчик 422 и ограничитель тела), `core/pcbk_core/data/gate.py` (`clear_pause`),
  `core/pcbk_core/main.py` (`build_roles` с `TokenTable`), `core/tests/helpers.py`
  (`make_service`, `make_app`, `TOKEN`, `sent_of`)
- Test: `core/tests/test_service.py`, `core/tests/test_http.py`

**Interfaces:**
- Consumes: всё из задач 2–4 и Д3а; `AVG_KIND`, `STD_KIND` — задача 1.
- Produces (`service.py`):
  - `AVG_KIND: Literal["step", "linear", "arith", "unverified"]`,
    `STD_KIND: Literal["step", "arith", "unverified"]`
  - `KIND_TEXTS = {"step": "взвешено по времени (ступенчатая интерполяция)", "linear": "взвешено по времени (линейная интерполяция)", "arith": "арифметическое по сырым значениям", "unverified": "вид не подтверждён сверкой"}`
  - постоянные тексты:
    - `SOURCE_LIVE = "текущее значение из таблицы Live историана"`;
    - `SOURCE_SUMMARY = "сводка историана по сырым данным: AnalogSummaryHistory, одна корзина на весь период"`;
    - `SHIFT_NOTE = "границы смен 06:00, 14:00, 22:00 — допущение, технологом не подтверждено"`;
    - `OPEN_NOTE = "период ещё идёт — числа изменятся"`;
    - `CLAMP_NOTE = "конец периода ограничен текущим временем историана"`;
    - `NO_LAST_NOTE = "последнее значение за период пока не считается"`;
    - `TRUNCATED_NOTE = "описания в историане обрезаны на 50 символах"`;
    - `SIMILAR_NOTE = "точного совпадения нет — вот похожие, выберите вместе со студентом"`;
    - `ITEM_TEXTS = {"no_value": "нет текущего значения", "no_data": "за период данных нет", "unlisted": "тег не входит в белый список стенда", "unknown": "такого тега нет в каталоге историана", "discrete": "дискретный тег: сводка не считается, наработка — в следующих слайсах"}`;
    - `MESSAGES = {"rate": "слишком часто: не больше 60 вызовов за 5 минут — подождите минуту", "global_rate": "общий предел запросов к историану исчерпан — повторите через несколько минут", "busy": "историан занят другими запросами — повторите через минуту", "timeout": "историан не ответил за 15 с — сократите период или число тегов", "catalog": "каталог тегов ещё не загружен", "connect": "нет связи с историаном", "auth": "историан отклонил учётные данные — сообщите преподавателю", "query": "историан вернул ошибку — вызов записан в журнал", "none": "ни один тег нельзя запросить", "args": "неверные аргументы: "}`;
    - `STATE_LABELS = {"_RUN": ("работает", "стоит"), "_OPN": ("открыт", "не открыт"), "_CLS": ("закрыт", "не закрыт"), "_ON": ("включён", "не включён"), "_OFF": ("выключен", "не выключен"), "_STOP": ("остановлен", "не остановлен"), "_FLT": ("неисправность", "исправен"), "_ALM": ("авария", "нет аварии")}` —
      подпись для 1 и для 0, в ответе — с пометкой «по суффиксу».
  - `class DataService`:
    - `__init__(self, role: DataRole, *, cache: TTLCache | None = None, flight: SingleFlight | None = None, per_caller: SlidingWindow | None = None)` —
      ворота, каталог, часы, события и `monotonic` берутся из роли; «сейчас
      историана» — последние часы плюс прошедшее по `monotonic`, при часах
      старше 300 с — сначала `[clock_sql()]`;
    - `async def catalog_search(self, caller: str, channel: Channel, query: str, limit: int = 10) -> dict[str, Any]`;
    - `async def tag_now(self, caller: str, channel: Channel, tags: list[str]) -> dict[str, Any]`;
    - `async def tag_period(self, caller: str, channel: Channel, tags: list[str], period: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]`;
    - `def refuse_args(self, caller: str, channel: Channel, tool: str, requested: Sequence[str], reason: str) -> dict[str, Any]` —
      ответ `refused` с `MESSAGES["args"] + reason` и событие.
  - Порядок в инструментах:
    1. частота вызывающего;
    2. проверка аргументов: больше 16 тегов, строка длиннее 128, неизвестный
       `period` → `refuse_args`;
    3. каталог загружен;
    4. `lookup`: повторы схлопываются, порядок `items` — как во вводе;
    5. период и бюджет (у `tag_period`);
    6. кэш → single-flight → ворота;
    7. сборка ответа → кэш → событие → строка журнала.

    «Сейчас историана» — `role.clock.local` плюс прошедшее по `monotonic` с
    `role.clock_mono`; опрос свежести обновляет их раз в 30 с, отдельный
    `[clock_sql()]` идёт, только если часы старше 300 с.

    **Любой отказ до ворот не шлёт SQL.** Событие пишется на каждый вызов:
    - `requested` — сырой ввод;
    - `tags` — канонические имена со статусами `ok`/`no_value`/`no_data`, в
      том числе при попадании в кэш;
    - `sent` — из `GateResult` или `HistorianError.sent`: и при сроке или сбое
      после отправки; у попадания в кэш и у ожидающего single-flight — пусто.

    Неожиданное исключение в инструменте → ответ `status: error`, событие
    `outcome=error` и строка журнала с классом ошибки. Ошибка записи события
    ответ не ломает. Строка журнала —
    `tool=… caller=… channel=… tags=N period=…..… cost=0.333 cache=miss rows=N ms=N outcome=ok`,
    без значений.
  - Контракт ответов (JSON: только str, float, int, bool, null, списки,
    словари):
    - общие ключи: `tool`, `status` (`ok` | `partial` | `refused` | `budget` |
      `rate` | `busy` | `timeout` | `unavailable` | `error`), `message` (кроме
      `ok`/`partial`), `notes`, `cache`;
    - `tag_now`: `asof`, `tz` («UTC+05:00»), `source = SOURCE_LIVE`,
      `items[]`:
      - `tag`, `status` (`ok` | `no_value` | `unlisted` | `unknown`), `text`
        (кроме `ok`), `similar` (у `unknown`);
      - только у `ok` и `no_value`: `description`, `unit`, `kind`, `value`,
        `time`, `age_s` (целое, не меньше 0), `quality` («хорошее» при 0,
        иначе «недостоверное (код N)»), `state_label` (у дискретных);
      - `item_notes`: метка впереди больше чем на 60 с → «метка тега впереди
        часов историана на N с»; возраст больше 600 с → «значение не
        менялось N мин: это и ровный процесс, и возможное залипание —
        смотрите качество»;
      - строка `Live` с `Value IS NULL` → `no_value` с `time` и `quality`;
    - `tag_period`: `period` {`start`, `end`, `tz`, `preset`,
      `shift_grid_assumed`, `clamped`, `open`}, `source = SOURCE_SUMMARY`,
      `avg_kind` {`code`, `text`}, `std_kind` {`code`, `text`},
      `cost_tag_days`, `items[]`:
      - `tag`, `status` (`ok` | `no_data` | `discrete` | `unlisted` |
        `unknown`), `text`;
      - только у `ok`: `description`, `unit`, `avg`, `min`, `max`,
        `range = max − min`, `std`, `percent_good`, `last` и `last_time`
        (при `HAS_LAST`), `quality_note` («доля достоверных N % — ниже 99 %»);
      - строки нет, либо `NULL` в `Average`/`Minimum`/`Maximum`, либо
        `PercentGood == 0` → `no_data`;
      - `notes`: `SHIFT_NOTE`, `OPEN_NOTE`, `CLAMP_NOTE`, `NO_LAST_NOTE`;
    - `catalog_search`: `query`, `matches[]` {`tag`, `description`, `unit`,
      `kind`, `min_eu`, `max_eu`, `live`, `description_truncated`},
      `similar`, `total`; `notes`: `SIMILAR_NOTE`, `TRUNCATED_NOTE`;
    - ошибки ворот → статус:
      - `HistorianError`: `connect` → `unavailable` с `MESSAGES["connect"]`,
        `auth` → `unavailable` с `MESSAGES["auth"]`, `timeout` → `timeout`,
        `query` → `error`;
      - `GateRefused`: `busy` → `busy`, `rate` → `rate` с
        `MESSAGES["global_rate"]`, `unlisted` → `error`;
      - каталог не загружен → `unavailable` с `MESSAGES["catalog"]`.
- Produces (`http_api.py`):
  - `Tag = Annotated[str, StringConstraints(max_length=128)]`;
    `CatalogSearchArgs(query: str (1..200), limit: int = 10 (1..50))`,
    `TagNowArgs(tags: list[Tag] (1..16))`,
    `TagPeriodArgs(tags: list[Tag] (1..16), period: Literal[<PRESETS>] | None, start: str | None, end: str | None)`
  - `data_router(role: DataRole) -> APIRouter`:
    - `POST /api/data/catalog_search`, `/api/data/tag_now`, `/api/data/tag_period`;
      `Authorization: Bearer` → вызывающий;
    - нет или неверный токен → `401 {"detail": "нужен токен"}` и строка
      журнала `outcome=unauthorized channel=http path=…` (без токена);
    - ответ инструмента — `200`.
  - `DataRole.install(app)` — единственное место крючков уровня приложения
    (протокол `Role` из Д3а; `app.py` не меняется):
    - `app.add_exception_handler(RequestValidationError, …)`: для путей
      `/api/data/*` сначала проверяется токен — нет или неверный → `401` и
      строка журнала, без события (битый JSON без токена не пишет мусор в
      журнал). С токеном → `422` с телом `service.refuse_args(...)` (русская
      причина: поля и короткий текст) и событием. Прочие пути — обработчик
      FastAPI по умолчанию;
    - `app.add_middleware(BodyLimit, max_bytes=65536)` — чистое ASGI-звено.
      Оно считает прочитанные из `receive` байты, поэтому ловит и тело без
      `Content-Length`. Больше предела → `413` и строка журнала
      `outcome=too_large path=…`; стоит на всём приложении, в том числе на
      `/mcp`.
  - `DataRole.router()` — роутер Д3а плюс `data_router`;
    `main.build_roles` передаёт `TokenTable.from_file(TOKENS_FILE)`

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_service.py
pytestmark = pytest.mark.anyio

@pytest.fixture
async def svc(tmp_path):
    s, fake = make_service(tmp_path)          # роль с загруженным каталогом; fake.calls очищены
    return s, fake

async def test_tag_now_answer_says_what_and_when(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20fake_001_pv"])
    it = r["items"][0]
    assert (r["status"], r["tz"], r["source"], r["cache"]) == ("ok", "UTC+05:00", SOURCE_LIVE, "miss")
    assert (it["tag"], it["value"], it["age_s"], it["quality"], it["time"]) == \
           ("20FAKE_001_PV", 12.5, 42, "хорошее", "2026-10-01T11:59:18+05:00")

async def test_events_requested_vs_sent(svc):                                  # Review Focus 2
    s, fake = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV", "16FAKE_009_PV", "20FAKE_001_PV' OR 1=1--",
                                           "20FAKE_005_LMN", "20FAKE_001_PV\n"])
    e = s.role.events.recent()[0]
    assert "16FAKE_009_PV" in e.requested and "20FAKE_001_PV' OR 1=1--" in e.requested
    assert e.sent == ("20FAKE_001_PV",) and sent_of(fake) == ["20FAKE_001_PV"]

async def test_tag_now_statuses_distinct_and_no_foreign_metadata(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20FAKE_003_PV", "16FAKE_009_PV", "20FAKE_404_PV"])
    assert r["status"] == "partial" and [i["status"] for i in r["items"]] == ["no_value", "unlisted", "unknown"]
    assert not {"description", "unit", "kind", "min_eu", "max_eu"} & set(r["items"][1])

async def test_tag_now_null_value_is_no_value(svc):                             # Review Focus 1
    s, fake = svc
    fake.live["20FAKE_002_SP"] = (datetime(2026, 10, 1, 11, 0), None, 0)
    it = (await s.tag_now("student-01", "http", ["20FAKE_002_SP"]))["items"][0]
    assert (it["status"], it["text"], it["time"]) == ("no_value", "нет текущего значения", "2026-10-01T11:00:00+05:00")

async def test_tag_now_future_timestamp_clamped(svc):
    s, fake = svc
    fake.live["20FAKE_001_PV"] = (datetime(2026, 10, 1, 12, 0, 31), 12.5, 0)
    fake.live["20FAKE_004_PV"] = (datetime(2026, 10, 1, 12, 1, 30), 3.2, 0)
    a, b = (await s.tag_now("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV"]))["items"]
    assert (a["age_s"], a.get("item_notes", [])) == (0, [])
    assert b["age_s"] == 0 and "впереди часов историана на 90 с" in b["item_notes"][0]

async def test_quality_and_discrete_labels(svc):
    s, _ = svc
    a, b = (await s.tag_now("student-01", "http", ["20FAKE_004_PV", "25FAKE_007_CLS"]))["items"]
    assert a["quality"] == "недостоверное (код 64)" and b["state_label"].startswith("закрыт")

async def test_tag_period_answer_says_what_was_computed(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    it = r["items"][0]
    assert r["status"] == "ok" and it["range"] == pytest.approx(5.0) == it["max"] - it["min"]
    assert (r["source"], r["avg_kind"]["code"], r["std_kind"]["code"]) == (SOURCE_SUMMARY, AVG_KIND, STD_KIND)
    assert r["period"]["start"] == "2026-09-30T22:00:00+05:00" and r["period"]["shift_grid_assumed"] is True
    assert SHIFT_NOTE in r["notes"] and ("last" in it) is HAS_LAST

async def test_tag_period_null_row_is_no_data(svc):                             # Review Focus 1
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_002_SP"], period="prev_shift")
    assert (r["items"][0]["status"], r["items"][0]["text"]) == ("no_data", "за период данных нет")

async def test_tag_period_four_distinct_refusals(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV", "16FAKE_009_PV",
                                                  "20FAKE_404_PV", "25FAKE_007_CLS"], period="prev_shift")
    assert [i["status"] for i in r["items"]] == ["ok", "no_data", "unlisted", "unknown", "discrete"]
    assert len({i["text"] for i in r["items"][1:]}) == 4

async def test_refusals_send_no_sql_and_are_events(svc):
    s, fake = svc
    outs = [(await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"],
                                start="2026-09-01 00:00", end="2026-09-14 00:00"))["status"],
            (await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift",
                                start="2026-10-01 10:00", end="2026-10-01 11:00"))["status"],
            (await s.tag_now("student-01", "http", ["16FAKE_009_PV"]))["status"],
            (await s.tag_now("student-01", "http", [f"20FAKE_{i:03d}_PV" for i in range(17)]))["status"]]
    assert outs == ["budget", "refused", "refused", "refused"] and fake.calls == []
    assert [e.outcome for e in s.role.events.recent()[:4]] == ["refused", "refused", "refused", "budget"]

async def test_rate_and_single_flight(svc):
    s, fake = svc
    fake.delay_s = 0.1
    rs = await asyncio.gather(*(s.tag_period(f"student-{n:02d}", "http", ["20FAKE_001_PV"], period="prev_shift")
                                for n in range(1, 11)))
    assert sum("AnalogSummaryHistory" in q for c in fake.calls for q in c) == 1
    assert sorted(r["cache"] for r in rs) == ["miss"] + ["shared"] * 9
    for _ in range(59):
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "rate"

async def test_historian_errors_are_named(svc):
    s, fake = svc
    for code, want in (("connect", "нет связи с историаном"), ("auth", MESSAGES["auth"])):
        fake.fail = HistorianError(code, "x")
        r = await s.tag_now(f"student-{code}", "http", ["20FAKE_004_PV"])
        assert (r["status"], r["message"]) == ("unavailable", want)
        s.role.gate.clear_pause()          # сброс паузы и защёлки между случаями

async def test_timeout_event_keeps_sent(svc):                                   # Review Focus 2
    s, fake = svc
    fake.fail = HistorianError("timeout", "долго")      # ворота добавят sent — SQL уже ушёл
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    e = s.role.events.recent()[0]
    assert (r["status"], e.outcome, e.sent) == ("timeout", "timeout", ("20FAKE_001_PV",))

async def test_cache_hit_event_has_tags(svc):
    s, _ = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    await s.tag_now("student-02", "http", ["20fake_001_pv"])
    e = s.role.events.recent()[0]
    assert (e.cache, e.tags, e.sent) == ("hit", ("20FAKE_001_PV",), ())

async def test_unexpected_error_is_error_event(svc):
    s, fake = svc
    fake.fail = RuntimeError("boom")
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert r["status"] == "error" and s.role.events.recent()[0].outcome == "error"

async def test_tool_answers_when_events_fail(svc, monkeypatch):                 # Review Focus 4
    s, _ = svc
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(s.role.events, "_insert", full)
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "ok"
    assert s.role.health()[0] is False

async def test_call_log_line_without_values(svc, capfd):
    s, _ = svc
    await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    err = capfd.readouterr().err
    assert "tool=tag_period" in err and "cost=0.333" in err and "12.4" not in err

# core/tests/test_http.py
def test_401_is_logged(tmp_path, capfd):                                        # Review Focus 3
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}).status_code == 401
        assert c.post("/api/data/tag_now", json={"tags": ["x"]}, headers={"Authorization": "Bearer nope"}).status_code == 401
    err = capfd.readouterr().err
    assert err.count("outcome=unauthorized channel=http") == 2 and "nope" not in err

def test_http_422_is_refused_event(tmp_path):                                   # Review Focus 3
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers=auth)
        assert r.status_code == 422 and r.json()["status"] == "refused" and "неверные аргументы" in r.json()["message"]
        e = role.events.recent()[0]
        assert (e.outcome, e.channel, e.tool) == ("refused", "http", "tag_now")
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}, headers=auth).json()["status"] == "ok"

def test_bad_json_without_token_is_401(tmp_path):                               # Review Focus 3
    app, role = make_app(tmp_path)
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        before = len(role.events.recent())
        r = c.post("/api/data/tag_now", content=b"{bad", headers={"Content-Type": "application/json"})
        assert r.status_code == 401 and len(role.events.recent()) == before   # мусора в журнале нет

def test_chunked_body_over_limit_is_413(tmp_path, capfd):                       # Review Focus 3
    app, _ = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    with TestClient(app) as c:
        chunks = (b"x" * 10_000 for _ in range(7))          # без Content-Length
        assert c.post("/api/data/tag_now", content=chunks, headers=auth).status_code == 413
        assert c.post("/mcp", content=b"x" * 70_000, headers=auth).status_code == 413
    assert "outcome=too_large" in capfd.readouterr().err
```

`make_service(tmp_path)` строит `DataRole` с `FakeHistorian` и `FakeMono`,
загружает каталог, чистит `fake.calls` и возвращает `(role.service, fake)`;
у `DataService` есть поле `role`. `sent_of(fake)` — имена из `names_in` всех
отправленных SQL. `gate.clear_pause()` — метод ворот, дописывается здесь:
снимает защёлку и паузу; им пользуются только тесты, у людей защёлку снимает
перезапуск службы. У `FakeHistorian` поле `fail` принимает любое исключение.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_service.py tests/test_http.py`. Expected: FAIL.

- [ ] **Step 3: Implement по интерфейсам**

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных: tag_now, tag_period, catalog_search по HTTP — мёртвые теги, отказы событиями, 401 и 413 в журнале"`.

---

### Task 6: MCP

**Files:**
- Create: `core/pcbk_core/data/mcp_server.py`
- Modify: `core/pcbk_core/data/__init__.py` (конструктор строит MCP; `mounts`, `lifespan`),
  `core/tests/{conftest,helpers}.py` (`core_server`, `mcp_session`, `http_post`)
- Test: `core/tests/test_mcp.py`

**Interfaces:**
- Consumes: `DataService`, `refuse_args`, `TokenTable`, модели
  `CatalogSearchArgs`, `TagNowArgs`, `TagPeriodArgs` — задача 5; `Role`,
  `RoleBase`, точные маршруты `mounts()` — Д3а.
- Produces:
  - `build_mcp(role: DataRole) -> FastMCP` —
    `FastMCP("pcbk-data", stateless_http=True, json_response=True, streamable_http_path="/mcp", log_level="WARNING", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`:
    - `log_level="WARNING"`: иначе FastMCP включает INFO на корневом логгере,
      и `pytds` пишет в журнал адрес историана и начало SQL;
    - защита от DNS rebinding выключена: хост разный (`core:8000`,
      `${STU_NET}.N.2:8000`, туннель), защищает токен на каждом запросе;
    - три инструмента с аннотацией `-> dict[str, Any]` (с `-> dict`
      `structuredContent` пуст), `readOnlyHint=True`, `openWorldHint=False`;
    - аргументы свободные, схему для модели задаёт `json_schema_extra`:
      `tags: Annotated[Any, Field(json_schema_extra={"type": "array", "items": {"type": "string", "maxLength": 128}, "minItems": 1, "maxItems": 16})] = None`,
      `period: Annotated[Any, Field(json_schema_extra={"type": "string", "enum": list(PRESETS)})] = None`,
      `start`, `end`, `query` — `Any` со схемой строки, `limit: Any = 10`.
      Внутри — `TagNowArgs.model_validate(...)` и т. д. Ошибка проверки →
      `role.service.refuse_args(...)` с событием. Строка вместо списка, нет
      тегов, неизвестный период и 17 тегов дают событие, а не английский
      отказ FastMCP до функции;
    - канал `"mcp"`; вызывающий — `role.tokens.caller` по заголовку
      `Authorization` из `ctx.request_context.request`.
  - `mcp_auth(app: ASGIApp, role: DataRole) -> ASGIApp` — чистое ASGI (не
    `BaseHTTPMiddleware`: тот ломает потоковые ответы). Для `/mcp`: `GET` →
    `405`; без верного `Bearer` → `401 {"detail": "нужен токен"}` и строка
    журнала `outcome=unauthorized channel=mcp`.
  - `DataRole.__init__` строит `self.mcp = build_mcp(self)` и
    `self.mcp_app = mcp_auth(self.mcp.streamable_http_app(), self)` один раз.
    `mounts()` → `[("/mcp", self.mcp_app)]` — точный маршрут, без `Mount("/")`,
    чужие пути ролей Д4/Д5 он не перехватит. `lifespan()` входит в
    `self.mcp.session_manager.run()` ровно один раз; `run()` второй раз SDK не
    даёт.
  - `TOOL_DESCRIPTIONS` (их читает модель; текст постоянный):
    - `catalog_search`: «Поиск тегов БДРВ участка по словам в имени и описании. Описания в историане обрезаны на 50 символах: если точного совпадения нет, вернутся похожие — покажите их студенту на выбор, не угадывайте.»
    - `tag_now`: «Текущее значение тегов БДРВ (до 16 за вызов): значение, единица, время с поясом, возраст и качество. Имена берите точно из catalog_search.»
    - `tag_period`: «Сводка по тегам БДРВ за период (до 16 тегов, окно до 31 суток, бюджет 24 тего-суток): среднее, минимум, максимум, размах, σ — посчитаны историаном по сырым данным. Период — готовый (period) или начало и конец (start, end, ISO 8601). В ответе сказано, какое это среднее, в каком поясе время и что смены 06/14/22 — допущение.»
  - фикстура `core_server`: `create_app` с `DataRole` на `FakeHistorian` и
    `FakeMono`, `uvicorn.Server` в потоке на `127.0.0.1:0`; ждёт загрузки
    каталога; отдаёт `url`, `role`.
    `mcp_session(url, token)` — `streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"})`
    → `ClientSession` → `initialize()`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_mcp.py
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
ACCEPT = "application/json, text/event-stream"

def raw(url, method="POST", body=INIT, **headers):
    req = urllib.request.Request(url, json.dumps(body).encode() if method == "POST" else None, method=method,
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
    tags = tools["tag_now"].inputSchema["properties"]["tags"]
    assert (tags["type"], tags["maxItems"], tags["items"]["maxLength"]) == ("array", 16, 128)
    assert tools["tag_now"].description == TOOL_DESCRIPTIONS["tag_now"]

@pytest.mark.anyio
async def test_mcp_and_http_give_same_answer(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        sc = (await s.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})).structuredContent
    code, body = http_post(core_server.url + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, TOKEN)
    drop = lambda d: {k: v for k, v in d.items() if k not in ("cache", "asof")}
    assert code == 200 and sc is not None and drop(sc) == drop(json.loads(body))

@pytest.mark.anyio
async def test_mcp_bad_arg_types_are_refused_events(core_server):              # Review Focus 3
    bad = [("tag_now", {"tags": "20FAKE_001_PV"}), ("tag_now", {}),
           ("tag_period", {"tags": ["20FAKE_001_PV"], "period": "last_year"}),
           ("tag_now", {"tags": [f"20FAKE_{i:03d}_PV" for i in range(17)]})]
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        for tool, args in bad:
            sc = (await s.call_tool(tool, args)).structuredContent
            assert sc["status"] == "refused" and "неверные аргументы" in sc["message"], args
            e = core_server.role.events.recent()[0]
            assert (e.outcome, e.channel, e.caller, e.sent) == ("refused", "mcp", "student-01", ())

def test_mcp_auth_host_and_get(core_server):                                    # Review Focus 5
    url = core_server.url + "/mcp"
    assert raw(url) == 401 and raw(url, Authorization="Bearer nope") == 401
    for host in ("core:8000", "172.31.1.2:8000"):
        assert raw(url, Authorization=f"Bearer {TOKEN}", Host=host) == 200
    assert raw(url, method="GET", Authorization=f"Bearer {TOKEN}") == 405
    assert raw(url + "/", Authorization=f"Bearer {TOKEN}") == 404                  # точный маршрут

@pytest.mark.anyio
async def test_lifespan_without_app_runs(tmp_path):                             # Review Focus 5
    role, _ = make_role(db_dir=tmp_path)
    async with role.lifespan():                       # MCP построен в конструкторе, run() — один раз
        pass
    assert not logging.getLogger("pytds").isEnabledFor(logging.INFO)
    assert not logging.getLogger().isEnabledFor(logging.INFO)          # FastMCP не поднял корневой журнал
```

`make_role(db_dir=…)` — вариант помощника Д3а с `DB_PATH` во временном
каталоге и тестовым `TokenTable`.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_mcp.py`. Expected: FAIL.

- [ ] **Step 3: Implement `build_mcp`, `mcp_auth`, построение в конструкторе, точный маршрут и жизненный цикл**

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS (включая `test_drill_freeze_poll_stops_loop` Д3а — роль без приложения).

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных по MCP 1.30.0: точный маршрут /mcp, любые ошибки аргументов — событиями, журнал без INFO SDK"`.

---

### Task 7: Компоновка и выкладка

**Files:**
- Modify: `core/Dockerfile` — до `USER`:
  `RUN install -d -o 10003 -g 10003 -m 0750 /var/lib/pcbk-core`, как у
  сторожа Д1. Новый именованный том наследует владельца каталога образа; без
  каталога том получит `root:root 0755`, `EventLog` не откроет `core.db`, и
  служба уйдёт в цикл перезапусков.
- Modify: `compose.yaml` (у `core` — `image: pcbk-reserve/core:d3b`, секрет
  `core-tokens`, том `pcbk-core-data:/var/lib/pcbk-core`), `.gitignore`
  (`core-tokens`, `*.data-token`), `tests/integration/conftest.py` (тестовый
  `core-tokens` с токеном `student-01`; `stack.core_token`;
  `http_host(method, url, body=None, token=None)`), `tests/integration/test_core.py`,
  `tests/integration/test_edge.py` (`IMAGES["pcbk-core"]` → `:d3b`),
  `deploy/README.md` (токены, журнал событий, MCP), `docs/checks/D3b.md`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_core.py (+)
def test_tool_over_egress_answers_and_journals(stack):
    code, body = stack.http_host("POST", CORE + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, token=stack.core_token)
    assert code == 200 and json.loads(body)["status"] == "unavailable"          # историана нет — словами
    assert stack.http_host("POST", CORE + "/api/data/tag_now", {"tags": ["x"]})[0] == 401
    assert "outcome=unauthorized channel=http" in stack.logs("pcbk-core")

def test_core_state_volume_writable(stack):                                     # Review Focus 4
    assert "охрана выхода: разрешено 1 направление" in stack.logs("pcbk-core")
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    assert mounts["/var/lib/pcbk-core"]["Type"] == "volume"
    assert (mounts["/run/secrets/core-tokens"]["Type"], mounts["/run/secrets/core-tokens"]["RW"]) == ("bind", False)
    stack.exec("pcbk-core", "test", "-w", "/var/lib/pcbk-core")               # при отказе exec бросит исключение
    assert stack.exec("pcbk-core", "stat", "-c", "%u", "/var/lib/pcbk-core/core.db") == "10003"
    code, body = stack.http_host("GET", CORE + "/healthz/data")
    assert code == 200 and json.loads(body)["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_core.py`. Expected: FAIL.

- [ ] **Step 3: Implement правки компоновки и фикстуры**

- [ ] **Step 4: Run the whole local suite** — `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q) && uv run --python 3.12 --with pytest pytest -q tests/integration`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/Dockerfile compose.yaml .gitignore tests/integration/ && git commit -m "Серверный слой: каталог журнала от uid 10003, токены и журнал событий в компоновке"`.

- [ ] **Step 6: Выкладка**

На сервере (содержимое на экран не выводится):

```bash
cd /opt/pcbk-reserve/secrets && umask 077
if [ ! -e core-tokens ]; then
  t=$(openssl rand -hex 24); printf '%s' "$t" > ops.data-token && chmod 0400 ops.data-token   # 48 знаков — под шаблоном Д2
  printf 'ops %s\n' "$(printf '%s' "$t" | sha256sum | cut -d' ' -f1)" > core-tokens && chmod 0444 core-tokens
fi
stat -c '%a %n' core-tokens ops.data-token
```

Токен `ops` — в `$JOB` без вывода на экран:
`$SSH 'cat /opt/pcbk-reserve/secrets/ops.data-token' > "$JOB/ops.token"`, затем
`printf 'Authorization: Bearer %s\n' "$(cat "$JOB/ops.token")" > "$JOB/ops.hdr"; chmod 600 "$JOB"/ops.*`.
Образ `:d3b` — `docker save … | $SSH …`, сверка `RootFS`;
`cp -p compose.yaml compose.yaml.d3a`; `rsync compose.yaml`;
`docker compose up -d --no-build core`.
Expected:
- `healthy`; на странице «Служба данных — жива», историан в норме — снимок
  `docs/checks/D3b/01-after-deploy.png` способом Д3а;
- `docker logs pcbk-core 2>&1 | grep -c 'охрана выхода'` → 1;
- `docker exec pcbk-core stat -c '%u %a' /var/lib/pcbk-core` → `10003 750`;
- память меньше 70 % от 256 МиБ.

Откат: `compose.yaml.d3a` и `docker compose up -d --no-build core` (образ
`:d3a`); том `pcbk-core-data` не удаляется.

- [ ] **Step 7: Commit** (после проверки на секреты) — `git add deploy/README.md docs/checks/D3b.md docs/checks/D3b/ && git commit -m "Выкладка Д3б: инструменты службы данных на сервере"`.

---

### Task 8: Живые проверки

Итог каждого шага — вердиктом [П] в `docs/checks/D3b.md`, без имён тегов,
значений и адресов. **Шаги 3–6 — при владельце.** Туннель —
`$SSH -N -L 18000:172.31.250.82:8000` в фоне. Имена для проверки лежат на
сервере в `probe-out/`, в `$JOB` едут через `scp` и на экран не выводятся.

- [ ] **Step 1: Здоровье через туннель.** `curl -s http://127.0.0.1:18000/healthz/data`
  → `{"ok": true, …}`; `/health/historian` → `error: null`,
  `gate.refused_unlisted: 0`.

- [ ] **Step 2: MCP.** `initialize`, затем `tools/list` — оба запроса `curl`
  с `-H @"$JOB/ops.hdr"`, `-H 'Accept: application/json, text/event-stream'`,
  телом JSON-RPC, на `http://127.0.0.1:18000/mcp` → `200`, три имени
  инструментов. `GET /mcp` → `405`; без токена → `401`.

- [ ] **Step 3: Ответ за смену.** Тег — `check-tag` пробы (лучший по
  различимости) или тот, что назовёт владелец. Запрос `$JOB/req.json` —
  `{"tags": ["<тег>"], "period": "prev_shift"}`.
  `curl -s -H @"$JOB/ops.hdr" -H 'Content-Type: application/json' --data @"$JOB/req.json" http://127.0.0.1:18000/api/data/tag_period | tee "$JOB/answer.json"`.
  Expected:
  - владелец видит ответ;
  - в журнал: `status: ok`, период — предыдущая смена, 8 ч, с поясом;
    `source`, `avg_kind`, `std_kind` и `SHIFT_NOTE` на месте;
  - `duration_ms` вызова из журнала событий — не больше 5000 (критерий p95
    ≤ 5 с);
  - повтор даёт `cache: hit`.

- [ ] **Step 4: Независимая сверка.**
  `$SSH 'docker run --rm -i --network bridge --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro pcbk-probe/tds:d3b verify --date-fmt <A|B>' < "$JOB/answer.json" > "$JOB/verify.json"`;
  `grep -E -i -f <шаблоны> "$JOB/verify.json"` — пусто.
  Expected: `bounds`, `min`, `max`, `last` (если есть) — «сошлось»; печатается
  `d` и вердикт вида. Дальше по вердикту:
  - вид «подтверждён», но не тот, что в `avg_kind`: строка 8а таблицы —
    `AVG_KIND` правится одним коммитом, перевыкладка, шаги 3–4 повторяются;
  - «не различим на этом теге»: так и записать; вид остаётся по пробе;
  - «не сошёлся»: `AVG_KIND = "unverified"`, вопрос владельцу.

  Успехом дня расхождение не прикрывается.

- [ ] **Step 5: Журнал событий доказывает белый список (основа успеха 4).**
  1. Отрицательный контроль: `tag_now` с `unlisted-tag` пробы и со строкой
     `X' OR 1=1--` → `status: refused`.
  2. Затем на сервере:
     `docker exec pcbk-core python -c "import json,sqlite3; w={l.strip() for l in open('/app/data/whitelist.txt') if l.strip() and not l.startswith('#')}; rows=list(sqlite3.connect('/var/lib/pcbk-core/core.db').execute('select requested, sent from agent_events')); sent=[x for _,s in rows for x in json.loads(s)]; req=[x for r,_ in rows for x in json.loads(r)]; print(len(rows), len(sent), sum(x not in w for x in sent), sum(x not in w for x in req))"`.

  Expected:
  - `N S 0 R`: имён вне списка в `sent` — 0, в `requested` — не меньше 2
    (отрицательный контроль виден — проверка не холостая);
  - `/health/historian` → `gate.refused_unlisted: 0`: служба и не пыталась
    отправить чужое.

- [ ] **Step 6: Охрана выхода.** Строка «охрана выхода: разрешено 1
  направление» есть. Если черта Д3а перенесла сюда факты о сети выхода —
  они делаются тут (задача 9 Д3а, шаг 5).

- [ ] **Step 7: Commit** (после проверки на секреты) — `git add docs/checks/ && git commit -m "Д3б: ответ за смену по туннелю, сверка с сырым Delta, MCP, журнал событий против белого списка"`.

---

### Task 9: Закрытие дня

- [ ] **Step 1: Документы.**
  - `docs/DESIGN-platform-2026-09-29.md`: §4 — инструменты как построены
    (имена, контракт ответа, ссылка на план); §13 — п. 8 «Д3б» (решения
    Д3б-R6…R10).
  - Предпосылки Д4 — в §13 и в строке Д4 `docs/PLAN-platform-2026-09-29.md`:
    - у MCP места `"oauth": false` и URL ровно `http://core:8000/mcp`;
    - токены `student-NN.data-token` (`openssl rand -hex 24`) и перезапуск
      `core`;
    - `pcbk-core` в сетях мест на `.2`;
    - `core` поднимается раньше мест. OpenCode v1.18.33 не переподключает
      remote MCP сам: если при старте места `core` недоступен или токен
      неверен, сервер MCP остаётся в `failed` до перезапуска OpenCode.
      Шлюз или сторож проверяет состояние MCP каждого места и при `failed`
      вызывает переподключение. Учение «`core` перезапущен при работающих
      местах» — вызов после перезапуска проходит без ручных действий;
    - крючки уровня приложения ролей `llm` и `gateway` — только через
      `Role.install(app)`, свои маршруты — точные, как `/mcp`.
  - Предпосылки Д5 — там же:
    - конфликт псевдонима `pcbk-core` в `http_as` для `sp-ctl`, когда сам
      `core` войдёт в `pcbk-ctl`;
    - поток `/event` OpenCode `edge` не должен рвать: у него свой `location`
      без `proxy_read_timeout 5s`, который сейчас стоит у ручек сторожа
      (`docs/research/07-gateway-whitelist.md`).
  - `README.md` — «Д3 готов (Д3а и Д3б)».
- [ ] **Step 2: Критик** (Opus 5.5); петля — до нуля блокеров, не больше двух
  раундов; третий — после разговора с владельцем.
- [ ] **Step 3: Слияние.** В `main`, тег `platform-d3b`. Перед пушем
  проверка на секреты по ветке и
  `git ls-files | grep -cE '(core-tokens|\.data-token|whitelist\.(txt|extra))$'` → `0`.
  Затем `git push origin main platform-d3b`, чистый клон. Удалить
  `$JOB/answer.json`, `verify.json`, `ops.*`, `check-tag`, `unlisted-tag`; на
  сервере — `probe-out/`.
- [ ] **Step 4: Владельцу** — «Д3 готов», ответ за смену и вердикт сверки
  одной строкой. Вопросы: вид среднего, если не подтверждён; правила
  `DOCKER-USER` на Д12; границы смен (О6).
