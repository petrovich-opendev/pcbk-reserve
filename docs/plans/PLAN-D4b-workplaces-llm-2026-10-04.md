# Д4б. Места подключены к LLM-прокси и службе данных — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** десять рабочих мест говорят с моделью через LLM-прокси и берут
данные БДРВ через службу данных по MCP. `pcbk-core` стоит в сети каждого места
на `.2`, у места свой токен данных файлом, образ `:d4` знает семь моделей
отбора. Места по-прежнему спят. Владелец видит по туннелю разговор через API
OpenCode: «что сейчас по тегу» — и число совпадает с `tag_now`. Цена этого
разговора видна прокси в потоке (§11 п. 4 через OpenCode). Кроме того:
- замерена память места «в работе» (§11 п. 5);
- учение «`core` перезапущен при работающих местах» проходит без ручных
  действий;
- таблица совместимости семи моделей стоит меньше $1.

**Architecture:** продолжение Д4а. Серверный слой входит в десять сетей мест
статическим адресом `.2` (под gVisor DNS не работает, `extra_hosts: core` есть
с Д2) и не пересылает пакеты между сетями (`net.ipv4.ip_forward=0`). OpenCode
места ходит:
- к модели — провайдером `pcbk` (`@ai-sdk/openai-compatible`), для Gemini и
  Claude ещё и `pcbk-or` (`@openrouter/ai-sdk-provider`), оба на
  `http://core:8000/llm/v1` с `{file:/run/secrets/llm-token}`;
- к данным — удалённым MCP `pcbk` на `http://core:8000/mcp` с заголовком
  `Bearer {file:/run/secrets/data-token}`.

Проверка здоровья места сообщает состояние MCP одним словом. Если MCP упал, а
место простаивает, она переподключает его через `dispose` не чаще раза в
минуту: только у неё есть пароль и путь к OpenCode. Сторож показывает это в
строке места. Перед подключением мест служба данных получает то, что Д3б
отдал Д4: нагрузку историана на странице и поле `returned` в событиях поиска.

**Tech Stack:** как в Д4а; образ места — OpenCode 1.18.33 (Bun из бинарника
исполняет и `converse.js`); `curlimages/curl:8.16.0`; google-chrome.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(§2 — своя сеть на студента; §6 — провайдер и MCP места; §9 «Неуспех 1»,
«Успех 6»; §10; §11 п. 4, 5); первая половина дня —
[`PLAN-D4a-llm-proxy-2026-10-03.md`](PLAN-D4a-llm-proxy-2026-10-03.md). Её
имена, Global Constraints и решения Д4а-R11…R17 действуют здесь. Решения этого
плана нумеруются дальше: Д4б-R18…R23. Предпосылки Д4 из закрытия
[`PLAN-D3b-tag-answers-2026-10-02.md`](PLAN-D3b-tag-answers-2026-10-02.md)
(задача 9) закрываются здесь:
- `"oauth": false` и URL ровно `http://core:8000/mcp`;
- токены `student-NN.data-token`;
- `pcbk-core` на `.2`;
- переподключение MCP мест;
- нагрузка историана;
- строка контейнера `core`.

[`research/08-model-candidates.md`](../research/08-model-candidates.md) §6, §8
(конфигурация модели в образе, шесть пунктов проверки совместимости);
[`research/09-constructor-agent.md`](../research/09-constructor-agent.md) — ключ
MCP `pcbk`, `returned`, О7, О10 (`dispose` и MCP). Места, секреты, помощники
`oc`, `probe`, `ocpid` —
[`PLAN-D2-workplaces-2026-09-30.md`](PLAN-D2-workplaces-2026-09-30.md). Работу
шлюза Д5 ([`research/07-gateway-whitelist.md`](../research/07-gateway-whitelist.md))
здесь не делаем: студенту места не открываются, разговор идёт в обход шлюза
только у администратора.

**Предпосылка.** Д4а влит в `main` с тегом `platform-d4a`, прокси на сервере с
ключом владельца — или хвосты закрываются в задаче 0. Ветка дня —
`d4b/workplaces-llm`.

**Влезает ли в день — оценка по часам.** Задачи последовательны; в часы задач
с кодом входят 15 минут ревью и правок; шкала — плановая.

| Задача | Часы | Где |
|---|---|---|
| 0. Хвосты Д4а (второй раунд критика, слияние, перенесённое окно) | 0–0,5 | — |
| 1. Служба данных перед подключением мест: нагрузка историана, ошибки инструментов, `returned` | 1,5 | локально |
| 2. Образ места `:d4` | 1,25 | локально |
| 3. Сторож: MCP мест и контейнер `core` | 0,75 | локально |
| 4. Компоновка: `core` в сетях мест, токены данных, сквозной разговор | 2,0 | локально |
| 5. Выкладка | 0,75 | сервер |
| 6. Живые проверки (шаги 2–6 — окно владельца) | 1,25 | сервер |
| 7. Проверка совместимости семи моделей | 1,0 | сервер, **окно владельца** |
| 8. Закрытие дня | 1,25 | — |
| **Критический путь по плановой шкале** | **9,75** (с хвостами Д4а — до 10,25) | |

**Окно владельца около 2 ч — вечером**, после задачи 6, шаг 1: задача 6,
шаги 2–6 (разговор с числом из БДРВ, учения), затем задача 7 (тратит деньги,
меньше $1). Производственные данные читаются только при владельце.

**Основание «один день»** — как в Д4а: живой темп Д1/Д2 по git и строка
«план / факт» Д4а. Если Д4а шёл вдвое медленнее Д1 или хуже, план
пересчитывается до начала дня.

**Черта отсечения — конец седьмого часа** плюс время хвостов Д4а. К черте
зелёны задачи 1–4 (5,5 ч) и сделана выкладка (задача 5). После черты порядок
жёсткий: задача 6, шаг 1 → окно владельца → задача 8.
- **Задача 4 не зелёна к черте.** Выкладки нет. Видимый результат —
  зелёные задачи 1–3 на локальном стенде и строка владельцу: Д4б кончится
  завтрашним утром, окно переносится, Д5 сдвигается на полдня. Решение о
  сдвиге — его.
- **Интеграционный тест показал, что OpenCode повторяет наш отказ** (шторм
  повторов на 402/403). Правка кода отказа в `REFUSALS` (Д4а-R12) — в той же
  задаче 4, сверх оценки.
- **Окна владельца нет.** Задачи 1–5 и шаг 1 задачи 6 идут. Разговор и
  совместимость ждут окна, владельцу уходит строка о сдвиге.
- **Модель не прошла п. 1–4 совместимости.** Это итог, а не сбой дня: в
  таблице — «не проходит», в финал Д8 идёт запасная из той же семёрки.

## Global Constraints

Действуют Global Constraints Д1–Д4а целиком. Д4б добавляет:

- **Образ места** собирается только локально (`pcbk-reserve/student:d4`) и
  едет `docker save | docker load` со сверкой `RootFS`. Места пересоздаются
  только `docker compose --profile students create --force-recreate --no-build <имена>`
  и после выкладки спят. `docker compose down` и удаление томов мест запрещены
  (Д2).
- **Токены данных мест** — `${SECRETS_DIR}/student-NN.data-token`: 48
  шестнадцатеричных знаков, `0444` в каталоге `0700`, при повторе не
  перезаписываются, только на сервере. Их хеши вместе с `ops` лежат в
  `core-tokens` (Д3б). В месте токен — `/run/secrets/data-token`. Без этого
  файла ссылка `{file:…}` делает конфигурацию неверной, и место падает в цикле
  (Д2-решение 2), поэтому образ `:d4` без секрета не выкладывается.
- **Производственные данные — только при владельце.** Это текст вопроса с
  именем тега, ответ модели и значение. Они живут только в `$JOB`, на экране
  и во временном `~/pcbk-d4/` на сервере (его удаляет закрытие). В
  `docs/checks` идут только вердикты, число вызовов и токенов, цены, имена
  моделей и провайдеров.
- **Деньги:**
  - разговор задачи 6 идёт от места 01 и оплачивается из его доли — не больше
    $0,10;
  - проверка совместимости идёт от одноразового места `ops` с жёстким
    потолком прокси `LLM_OPS_SHARE_USD` = уже потраченное `ops` + $1;
  - доли студентов на проверку не тратятся.
- **Одноразовые контейнеры** — `converse`, место `ops`, `probe`: на сервере
  `--runtime=runsc`, пароль и токены — только файлами `:ro`, после работы
  удаляются. Каталог с копиями токенов `ops` — `0700`, удаляется в конце
  задачи 7.
- **Проверка здоровья места** печатает код ответа и одно слово состояния MCP
  (`200 mcp=connected`), больше ничего. `dispose` она вызывает только у
  простаивающего места (`GET /session/status` → `{}`) и не чаще раза в 60 с.

## Решения по умолчанию (Ruling)

**Д4б-R18 — переподключение MCP делает проверка здоровья места.** OpenCode
1.18.33 не переподключает удалённый MCP сам. Если при старте места `core`
недоступен, MCP остаётся `failed` (Д3б). У сторожа нет ни пути в сети мест, ни
паролей, поэтому:
- состояние MCP сообщает проверка здоровья места (`200 mcp=<слово>`);
- сторож читает его из `State.Health.Log` через `sp-ro` и ставит «внимание»;
- переподключает сама проверка здоровья: `POST /instance/dispose`, когда место
  простаивает, не чаще раза в 60 с. Что `dispose` заново поднимает MCP, видно
  в research/09, О10.

Уже подключённый MCP после перезапуска `core` работает и так: сервер MCP без
сессий (stateless, Д3б). **Цена ошибки:** место с неверным токеном данных
будет получать `dispose` раз в минуту, поток `/event` к браузеру (с Д6)
переподключается. Сторож при этом показывает «внимание».
Если `POST /mcp/pcbk/connect` в 1.18.33 есть и тест задачи 4 это покажет, им
можно заменить `dispose` одной строкой скрипта.

**Д4б-R19 — токен данных — свой файл места.** Токены
`student-NN.data-token` отдельны от `llm-token` — это имена Д3б. Утёкший токен
данных не тратит денег, утёкший токен LLM не читает историан. Заголовок MCP
берёт файл через `{file:…}`, не через окружение: секреты контейнерам — только
файлами.

**Д4б-R20 — модели в образе.** В провайдере `pcbk` — все семь кандидатов
(`@ai-sdk/openai-compatible`, research/08 §6). В провайдере `pcbk-or` — Gemini
3.8 Flash и Claude Sonnet 5.5 через встроенный `@openrouter/ai-sdk-provider`:
он возвращает `reasoning_details` с подписями, прокси пропускает их как есть
(Д4а, задача 3). Модель по умолчанию — `pcbk/z-ai/glm-5.3` (главный кандидат).
У каждой модели `limit: {context: 131072, output: 16000}`. Окно 131 072, а не
1 млн: каждый шаг пересылает весь разговор, поэтому раннее сжатие разговора —
защита бюджета. Заодно это верхняя граница цены одного вызова в Д4а-R13.
`small_model` не задаётся: после выбора в списке прокси останется одна модель,
и заголовки разговоров идут на неё же. **Цена ошибки:** длинный разговор
сжимается раньше, чем позволила бы модель.

**Д4б-R21 — кто платит за проверки.** Видимый разговор идёт от настоящего
места 01: он доказывает проводку места, а центы из доли 01 — честная цена.
Проверка совместимости идёт от одноразового места `ops`: тот же образ, та же
сеть, свои токены `ops`. Потолок держит сам прокси. По решению research/08
отбор считается в тех же $1000, а студенческие доли не трогаются.

**Д4б-R22 — нагрузка историана на странице** (предпосылка Д4 из Д3б):
- p95 длительности вызовов людей за 5 минут, если вызовов не меньше 5;
  предупреждение при p95 больше 5 с;
- после 3 сроков подряд на полосе людей — пауза этой полосы на 60 с: отказ
  `busy` без SQL, фон работает;
- «инструменты отвечают ошибкой» — если из последних 50 вызовов (не меньше 10)
  ошибка, срок или недоступность у 20 % и больше; «k из N» — в `note`.

**Цена ошибки:** при медленном историане студенты минуту получают «историан
занят», зато историан не забивается повторами.

**Д4б-R23 — `core` в сетях мест.** Адрес `.2` в каждой из десяти сетей,
`sysctls: net.ipv4.ip_forward: "0"`. Маршрута по умолчанию у мест нет (Д2),
так что адреса `core` в чужих сетях и в сети выхода месту недостижимы. Место
видит все ручки `core` на своём `.2`. Без токена ему доступны `/healthz*`,
`/health/historian`, `/health/llm` — там только числа и тексты состояний,
без имён тегов и вызывающих (Д3а, Д4а). Прочие ручки требуют токена.

## Review Focus

1. **Место поднялось, пока `core` лежал** (перезагрузка сервера:
   `unless-stopped` поднимает места в любом порядке) — MCP `failed` навсегда.
   Ожидание:
   - строка места — «внимание — нет связи со службой данных (MCP)»;
   - после подъёма `core` MCP переподключается сам не позже чем через 2
     минуты, вызов инструмента проходит.

   Тесты — задача 3, `test_mcp_state_from_health_output`; задача 4,
   `test_mcp_reconnects_after_core_restart`; живьём — задача 6, шаг 6.
2. **Отказ прокси в чате — один раз, без шторма повторов.** Речь о пределе
   шагов и об исчерпанном бюджете. Ожидание: в ошибке сообщения — русский
   текст прокси, к двойнику ушло ровно 16 запросов с инструментами, новых не
   появляется. Тесты — задача 4,
   `test_turn_limit_shown_in_chat_without_retry_storm`, `test_budget_message_in_chat`.
3. **`core` в десяти сетях становится мостом:** из места А к месту Б, к `core`
   в чужой сети, в сеть выхода, к двойнику OpenRouter. Ожидание: достижим
   только свой `.2:8000` (положительный контроль), остальное — 0. Тесты —
   задача 4, `test_place_reaches_only_core`; живьём — задача 6, шаг 1.
4. **Список моделей расходится между образом и прокси.** Модель есть в
   `opencode.json`, но прокси её не пускает, или модель по умолчанию не из
   списка. Ожидание: семь имён образа — ровно список прокси, модель по
   умолчанию в нём, `pcbk-or` — подмножество. Тест — задача 2,
   `test_workplace_config_lists_same_models`, `test_config_files`.
5. **Токен данных места А попал месту Б или заголовок MCP потерялся.**
   Ожидание: у каждого места свой `data-token`; вызов MCP из места 01 в
   журнале событий — `student-01`, `mcp`; вызов модели в журнале вызовов —
   `student-01`. Тесты — задача 4, `test_each_workplace_uses_own_objects`
   (правка Д2), `test_conversation_tool_via_mcp_and_cost`.

---

## Карта файлов

```
core/pcbk_core/data/gate.py           + p95, сроки и busy за 5 мин, пауза полосы людей после 3 сроков
core/pcbk_core/data/service.py        + tools_stats
core/pcbk_core/data/events.py         + CallEvent.returned (catalog_search), столбец returned
core/pcbk_core/data/__init__.py       /health/historian: + tools
core/tests/test_gate.py, test_service.py, test_events.py (+), test_llm_models.py
student/config/opencode.json          семь моделей pcbk, две pcbk-or, MCP pcbk с токеном файлом
student/pcbk-health                   + состояние MCP и переподключение простаивающего места
watchdog/pcbk_watchdog/checks.py      + строки нагрузки историана; MCP места
watchdog/components.json              + core-container
watchdog/tests/                       + нагрузка историана, MCP места, core-container
compose.yaml                          core :d4b в сетях мест на .2, ip_forward 0; места :d4 + data-token;
                                      sp-ro видит pcbk-core; сторож :d4b
compose.test.yaml                     + LLM_TOTAL_USD у core (только тесты)
tests/integration/converse.js         разговор через API OpenCode из сети места (Bun из образа места)
tests/integration/conftest.py         data-token мест, core-tokens, converse, health_output, data_events, wait_for
tests/integration/workplace.py        IMAGE :d4, MOUNTS + data-token, MODELS, OR_MODELS
tests/integration/test_student_image.py   конфигурация, вывод проверки здоровья, data-token в запусках
tests/integration/test_students.py    шесть источников, три секрета; адрес core — не в списке нулей
tests/integration/test_socket_proxy.py    sp-ro пускает inspect pcbk-core
tests/integration/test_edge.py        IMAGES; DECLARED_ENV core + LLM_TOTAL_USD (тесты)
tests/integration/test_conversation.py
deploy/README.md                      + токены данных мест, пересоздание мест, откат Д4б
docs/checks/D4b.md, docs/checks/D4b/*.png
```

**Сети** — таблицы Д1–Д3а; меняется только состав сетей мест:

| Сеть | Подсеть | Вид | Кто в ней |
|---|---|---|---|
| `pcbk-stu-NN` | `${STU_NET}.N.0/28`, динамика только `.8/29` | внутренняя, изолированный шлюз | `pcbk-student-NN` — `.3`; **`pcbk-core` — `.2`** |

Имена: образы `pcbk-reserve/core:d4b`, `pcbk-reserve/watchdog:d4b`,
`pcbk-reserve/student:d4` (`:d4a`, `:d2` остаются на сервере для отката);
секреты Compose `student-NN-data` → `/run/secrets/data-token`; одноразовое
место проверки — контейнер `pcbk-ops-place` на `${STU_NET}.1.9`.

---

### Task 0: Хвосты Д4а

- [ ] **Step 1:** Всё, что черта Д4а перенесла сюда:
  - второй раунд критика, слияние и тег `platform-d4a`;
  - перенесённое окно владельца. Тогда первыми идут задача 1 и задачи 9–10
    Д4а, их шагами и коммитами.

  Expected: у каждого хвоста есть вердикт в `docs/checks/D4a.md`; прокси на
  сервере, «LLM-прокси и бюджет» в норме; ветка Д4б — от `main` после тега.

---

### Task 1: Служба данных перед подключением мест

**Files:**
- Modify: `core/pcbk_core/data/gate.py`, `core/pcbk_core/data/service.py`,
  `core/pcbk_core/data/events.py`, `core/pcbk_core/data/__init__.py`,
  `watchdog/pcbk_watchdog/checks.py`
- Test: `core/tests/test_gate.py` (+2), `core/tests/test_service.py` (+2),
  `core/tests/test_events.py` (+1), `watchdog/tests/test_checks.py` (+1)

**Interfaces:**
- Consumes: `HistorianGate`, `GateRefused`, `HistorianError`, `FakeHistorian`,
  `FakeMono`, `gate()`, `Q`, `DataService`, `CallEvent`, `EventLog`, `svc`,
  `check_historian`, `fresh()`, `Check.note` — Д3а, Д3б, Д4а.
- Produces (`gate.py`):
  - `STATS_WINDOW_S = 300.0`, `P95_MIN_CALLS = 5`, `PAUSE_AFTER_TIMEOUTS = 3`,
    `USER_PAUSE_S = 60.0`
  - `stats()` дополнительно:
    - `p95_ms: int | None` — вызовы людей, завершённые за `STATS_WINDOW_S`,
      при их числе не меньше `P95_MIN_CALLS`;
    - `timeouts_5m: int`, `busy_5m: int`, `user_paused: bool`.
  - Пауза:
    - `HistorianError("timeout")` три раза подряд на полосе людей → до
      `monotonic() + USER_PAUSE_S` новые вызовы людей сразу получают
      `GateRefused("busy")`, без места и SQL;
    - любой другой исход вызова людей обнуляет счётчик;
    - фоновая полоса паузы не знает.
- Produces (`service.py`): `TOOL_WINDOW = 50`;
  `DataService.tools_stats(self) -> dict` — `{"recent": n, "errors": k}` по
  последним `TOOL_WINDOW` вызовам каналов `http` и `mcp`. Ошибка — исход
  `error`, `timeout` или `unavailable`.
- Produces (`events.py`):
  - `CallEvent.returned: tuple[str, ...] = ()` — у `catalog_search` имена из
    `matches`, до 50; у прочих пусто;
  - столбец `returned` (JSON); если таблица Д3б без него, `EventLog`
    добавляет его при открытии (`ALTER TABLE agent_events ADD COLUMN returned TEXT`).

  По этому полю Д7 ловит `tag_not_seen` (research/09 §9 п. 5).
- `/health/historian` — плюс ключ `tools` (`tools_stats()`), `gate` — с новыми
  полями.
- Produces (`checks.py`): `SLOW_P95_MS = 5000`, `TOOL_ERRORS_SHARE = 0.2`,
  `TOOL_ERRORS_MIN_CALLS = 10`. В таблицу `check_historian` Д3а после строк
  «последняя метка старше …» и перед «каталог тегов старше двух суток» встают:

| Ответ службы | Итог |
|---|---|
| `gate.user_paused` | `warn` «запросы людей к историану на паузе после сроков подряд» |
| `gate.p95_ms` больше `SLOW_P95_MS` | `warn` «историан отвечает медленно: p95 больше 5 с» |
| `tools.recent ≥ 10` и `tools.errors / tools.recent ≥ 0,2` | `warn` «инструменты отвечают ошибкой», `note` «k из N» |

  Нет ключей `tools` или новых полей `gate` (служба `:d4a`) — строки не
  срабатывают.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_gate.py (+)
async def test_three_timeouts_pause_user_lane_not_background():
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("timeout", "долго"))
    g = gate(fake, monotonic=mono)
    for _ in range(PAUSE_AFTER_TIMEOUTS):
        with pytest.raises(HistorianError):
            await g.run([Q])
    sent = len(fake.calls)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "busy" and len(fake.calls) == sent and g.stats()["user_paused"] is True
    fake.fail = None
    await g.run([clock_sql()], lane="background")                              # фон на паузе не стоит
    mono.advance(USER_PAUSE_S)
    await g.run([Q])
    assert g.stats()["user_paused"] is False and len(fake.calls) == sent + 2

async def test_p95_needs_five_calls():
    g = gate(FakeHistorian(delay_s=0.05))
    for _ in range(4):
        await g.run([Q])
    assert g.stats()["p95_ms"] is None
    await g.run([Q])
    assert 40 <= g.stats()["p95_ms"] <= 1000

# core/tests/test_service.py (+)
async def test_tools_stats_window(svc):
    s, fake = svc
    fake.fail = HistorianError("connect", "x")
    for n in range(1, 4):
        await s.tag_now(f"student-0{n}", "http", ["20FAKE_004_PV"])
        s.role.gate.clear_pause()
    fake.fail = None
    await s.tag_now("student-09", "mcp", ["20FAKE_001_PV"])
    assert s.tools_stats() == {"recent": 4, "errors": 3}

async def test_catalog_search_event_has_returned(svc):
    s, _ = svc
    await s.catalog_search("student-01", "http", "расход массы")
    assert s.role.events.recent()[0].returned == ("20FAKE_001_PV", "20FAKE_002_SP")

# core/tests/test_events.py (+)
def test_old_table_gets_returned_column(tmp_path):
    db = str(tmp_path / "core.db")
    EventLog(db)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE agent_events DROP COLUMN returned"); con.commit(); con.close()   # таблица, как у Д3б
    log = EventLog(db)
    log.write(replace(E, returned=("20FAKE_001_PV",)))
    assert log.recent()[0].returned == ("20FAKE_001_PV",)

# watchdog/tests/test_checks.py (+)
def test_historian_load_rows(fake_core):
    gate = {"user_paused": False, "p95_ms": 100}
    cases = [(fresh(10, gate={**gate, "user_paused": True}),
              ("warn", "запросы людей к историану на паузе после сроков подряд", "")),
             (fresh(10, gate={**gate, "p95_ms": 6200}), ("warn", "историан отвечает медленно: p95 больше 5 с", "")),
             (fresh(10, gate=gate, tools={"recent": 50, "errors": 12}), ("warn", "инструменты отвечают ошибкой", "12 из 50")),
             (fresh(10, gate=gate, tools={"recent": 5, "errors": 5}), ("ok", "последняя метка 10 с назад", "")),
             (fresh(10), ("ok", "последняя метка 10 с назад", ""))]             # служба :d4a — без новых полей
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail, r.note) == want
```

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_gate.py tests/test_service.py tests/test_events.py; cd ../watchdog && uv run --python 3.12 --with pytest pytest -q tests/test_checks.py`. Expected: FAIL.
- [ ] **Step 3: Implement по интерфейсам и таблице.**
- [ ] **Step 4: Run both suites** — `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/)`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add core/ watchdog/ && git commit -m "Служба данных перед местами: p95 и пауза после сроков подряд, ошибки инструментов на странице, returned в событиях поиска"`.

---

### Task 2: Образ места `:d4`

**Files:**
- Modify: `student/config/opencode.json`, `student/pcbk-health`,
  `tests/integration/workplace.py` (`IMAGE = "pcbk-reserve/student:d4"`,
  `MOUNTS` + `/run/secrets/data-token`, `MODELS`, `OR_MODELS`),
  `tests/integration/test_student_image.py`. Все тесты образа, где стартует
  OpenCode, монтируют ещё и `/run/secrets/data-token`: без файла конфигурация
  неверна.
- Create: `core/tests/test_llm_models.py`
- Test: `tests/integration/test_student_image.py`, `core/tests/test_llm_models.py`

**Interfaces:**
- Consumes: `select_models` — Д4а; `PERMISSION`, `IMAGE_ENV`, `WORKPLACE_ENV`,
  `ONESHOT_LABEL`, `docker()`, `ok()` — Д2.
- Produces (`student/config/opencode.json`) — ровно это (ключи и значения
  проверяет тест):

```json
{
  "autoupdate": false,
  "share": "disabled",
  "snapshot": false,
  "enabled_providers": ["pcbk", "pcbk-or"],
  "model": "pcbk/z-ai/glm-5.3",
  "permission": {"external_directory": "deny", "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"}},
  "provider": {
    "pcbk": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {"baseURL": "http://core:8000/llm/v1", "apiKey": "{file:/run/secrets/llm-token}"},
      "models": {
        "z-ai/glm-5.3": {"limit": {"context": 131072, "output": 16000}},
        "deepseek/deepseek-v4.1-flash": {"limit": {"context": 131072, "output": 16000}},
        "google/gemini-3.8-flash": {"limit": {"context": 131072, "output": 16000}},
        "openai/gpt-6-sol": {"limit": {"context": 131072, "output": 16000}},
        "z-ai/glm-5.3-flash": {"limit": {"context": 131072, "output": 16000}},
        "openai/gpt-6-luna": {"limit": {"context": 131072, "output": 16000}},
        "anthropic/claude-sonnet-5.5": {"limit": {"context": 131072, "output": 16000}}
      }
    },
    "pcbk-or": {
      "npm": "@openrouter/ai-sdk-provider",
      "options": {"baseURL": "http://core:8000/llm/v1", "apiKey": "{file:/run/secrets/llm-token}"},
      "models": {
        "google/gemini-3.8-flash": {"limit": {"context": 131072, "output": 16000}},
        "anthropic/claude-sonnet-5.5": {"limit": {"context": 131072, "output": 16000}}
      }
    }
  },
  "mcp": {
    "pcbk": {
      "type": "remote",
      "url": "http://core:8000/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {"Authorization": "Bearer {file:/run/secrets/data-token}"}
    }
  }
}
```

  `baseURL` Д2 не меняется. Глобальный `opencode.json` OpenCode читает один раз
  (Д2), поэтому места пересоздаются, а не перечитывают его. Разрешение
  `pcbk_save_agent: deny` добавит Д7 вместе с самим инструментом.
- Produces (`student/pcbk-health`) — порядок и сроки:
  1. `GET /agent`, как в Д2 (`read -t 2`); не 200 — печатается код, выход 1.
  2. `GET /mcp` (`read -t 1`, ответ целиком). Слово — `status` у `pcbk` по
     регулярке `"pcbk":\{"status":"([a-z_]+)"`, иначе `unknown`.
  3. Слово не `connected`, не `unknown` и не `disabled`,
     `/tmp/pcbk-mcp-retry` нет или он старше 60 с, а `GET /session/status`
     (`read -t 0.5`) отдал ровно `{}` → `touch /tmp/pcbk-mcp-retry` и
     `POST /instance/dispose` (`read -t 0.4`, ответ дальше не ждётся).
  4. Печатается `200 mcp=<слово>`, выход 0.

  Весь прогон — не дольше 4 с, stderr пуст, пароля в выводе нет. Новый
  договор вывода: код и одно слово, а не только код (Д2).
- `workplace.py`: `MODELS` — семь имён в порядке `select_models("")`;
  `OR_MODELS = ["google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_student_image.py (правки)
MCP = {"pcbk": {"type": "remote", "url": "http://core:8000/mcp", "enabled": True, "oauth": False,
                "headers": {"Authorization": "Bearer {file:/run/secrets/data-token}"}}}
LIMIT = {"limit": {"context": 131072, "output": 16000}}

def test_config_files():
    cfg = json.loads((CONFIG / "opencode.json").read_text())
    assert set(cfg) == {"autoupdate", "share", "snapshot", "enabled_providers", "model", "permission", "provider", "mcp"}
    assert (cfg["enabled_providers"], cfg["model"], cfg["permission"], cfg["mcp"]) == \
           (["pcbk", "pcbk-or"], "pcbk/z-ai/glm-5.3", PERMISSION, MCP)
    for pid, npm, names in (("pcbk", "@ai-sdk/openai-compatible", MODELS),
                            ("pcbk-or", "@openrouter/ai-sdk-provider", OR_MODELS)):
        p = cfg["provider"][pid]
        assert (p["npm"], list(p["models"])) == (npm, names)
        assert p["options"] == {"baseURL": "http://core:8000/llm/v1", "apiKey": "{file:/run/secrets/llm-token}"}
        assert all(m == LIMIT for m in p["models"].values())
    # .gitignore и заглушки — как в Д2

def test_health_reports_mcp_without_core(student_image, tmp_path):             # Review Focus 1
    cid = run_place(tmp_path, network="none")          # помощник: пароль, llm- и data-token файлами, tmpfs как в Д2
    try:
        outs, deadline = [], time.monotonic() + 45
        while "200 mcp=failed" not in outs and time.monotonic() < deadline:
            outs.append(docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip())
            time.sleep(2)
        assert "200 mcp=failed" in outs and all(re.fullmatch(r"000|\d{3}|200 mcp=[a-z_]+", o) for o in outs)
        first = docker("exec", cid, "stat", "-c", "%Y", "/tmp/pcbk-mcp-retry").stdout.strip()
        docker("exec", cid, "/usr/local/bin/pcbk-health")
        assert docker("exec", cid, "stat", "-c", "%Y", "/tmp/pcbk-mcp-retry").stdout.strip() == first   # не чаще раза в 60 с
    finally:
        docker("rm", "-f", cid)

# test_health_needs_working_instance Д2: коды — по регулярке r"\d{3}( mcp=[a-z_]+)?", последний — «200 mcp=…»

# core/tests/test_llm_models.py
def test_workplace_config_lists_same_models():                                  # Review Focus 4
    cfg = json.loads((Path(__file__).resolve().parents[2] / "student" / "config" / "opencode.json").read_text())
    assert list(cfg["provider"]["pcbk"]["models"]) == list(select_models(""))
    assert set(cfg["provider"]["pcbk-or"]["models"]) <= set(select_models(""))
    assert cfg["model"].split("/", 1)[1] in select_models("")
```

`run_place(tmp_path, network, config=None) -> str` — помощник в
`test_student_image.py`. Это запуск из `test_health_needs_working_instance`
Д2, вынесенный в функцию и дополненный файлом `data-token`.

- [ ] **Step 2: Run tests to verify they fail** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py; (cd core && CORE_PYTEST tests/test_llm_models.py)`. Expected: FAIL — старая конфигурация, нет слова MCP в выводе.
- [ ] **Step 3: Implement конфигурацию, `pcbk-health`, `workplace.py`, помощник `run_place` и правки тестов образа.**
- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS, включая `test_health_survives_reset_connection` и `test_external_read_refused_without_asking` Д2.
- [ ] **Step 5: Commit** — `git add student/ tests/integration/ core/tests/test_llm_models.py && git commit -m "Образ места :d4: семь моделей через прокси, MCP pcbk с токеном файлом, проверка здоровья сообщает и чинит MCP"`.

---

### Task 3: Сторож — MCP мест и контейнер `core`

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/components.json`,
  `watchdog/tests/helpers.py` (`insp(..., output="200\n")`), `compose.yaml`
  (правило `sp-ro`), `tests/integration/test_socket_proxy.py`
- Test: `watchdog/tests/test_checks.py` (+1), `watchdog/tests/test_main.py`
  (`test_components_file_d4a` → `test_components_file_d4b`)

**Interfaces:**
- Consumes: `check_container` и её таблица Д1/Д2, `insp`, `stu`,
  `load_components`, правило `sp-ro` Д1.
- Produces:
  - `MCP_DOWN_DETAIL = "нет связи со службой данных (MCP)"`;
    `MCP_WORD = re.compile(r"\bmcp=([a-z_]+)")`.
  - В таблицу `check_container` после строки «`unhealthy` → OpenCode не
    отвечает» встаёт строка: **`Running`, `Health.Status == "healthy"`, в
    `Output` последней записи `Health.Log` есть `mcp=<слово>`, и слово не
    `connected`, не `unknown`, не `disabled` (Д4б)** → `warn`
    `MCP_DOWN_DETAIL`. Вывод без слова (образ `:d2`) строку не включает.
  - `components.json`: сразу после `core` встаёт
    `{"id": "core-container", "title": "Серверный слой — контейнер", "kind": "container", "container": "pcbk-core", "sleeping_ok": false}`.
    «Убит по памяти» и «падает в цикле» видны на странице, а не только
    «не отвечает» у строк ролей.
  - `sp-ro` в `compose.yaml`:
    `-allowGET=(/v1\.[0-9]+)?/(_ping|containers/pcbk-(student-(0[1-9]|10)|sp-ctl|core)/json)`.

- [ ] **Step 1: Write the failing tests**

```python
# watchdog/tests/test_checks.py (+)
def test_mcp_state_from_health_output():                                       # Review Focus 1
    seen = {out: stu(insp(running=True, health="healthy", output=out))
            for out in ("200 mcp=connected\n", "200 mcp=failed\n", "200\n", "200 mcp=unknown\n")}
    assert [c.state for c in seen.values()] == ["ok", "warn", "ok", "ok"]
    assert seen["200 mcp=failed\n"].detail == "нет связи со службой данных (MCP)"

# watchdog/tests/test_main.py
def test_components_file_d4b():
    comps = load_components("components.json")
    ids = [c["id"] for c in comps]
    assert ids[ids.index("core") + 1] == "core-container"
    cc = comps[ids.index("core-container")]
    assert (cc["kind"], cc["container"], cc["sleeping_ok"]) == ("container", "pcbk-core", False)

# tests/integration/test_socket_proxy.py (правка test_sp_ro_serves_watchdog_reads_only)
#   stack.http_from_watchdog("GET", RO + "/containers/pcbk-core/json") in PASSED
```

- [ ] **Step 2: Run tests to verify they fail** — `cd watchdog && uv run --python 3.12 --with pytest pytest -q`. Expected: FAIL.
- [ ] **Step 3: Implement строку, компонент и правило `sp-ro`.**
- [ ] **Step 4: Run tests to verify they pass** — `cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add watchdog/ compose.yaml tests/integration/test_socket_proxy.py && git commit -m "Сторож: MCP места по выводу проверки здоровья, строка контейнера core через sp-ro"`.

---

### Task 4: Компоновка — `core` в сетях мест, токены данных, сквозной разговор

**Files:**
- Modify: `compose.yaml`, `compose.test.yaml`, `tests/integration/conftest.py`,
  `tests/integration/workplace.py`, `tests/integration/test_students.py`,
  `tests/integration/test_edge.py`, `tests/integration/test_core.py`,
  `deploy/README.md`
- Create: `tests/integration/converse.js`
- Test: `tests/integration/test_conversation.py`

**Interfaces:**
- Consumes: образы задач 1–3; `Stack` Д1–Д4а (`probe`, `start`, `stop`,
  `recreate`, `fake_requests`, `llm_calls`, `inspect`, `prod_config`,
  `wait_status`, `password`, `llm_token`, `secrets_dir`, `stu_net`,
  фикстура `running` Д2).
- Produces (`compose.yaml`):

```yaml
x-student: &student
  image: pcbk-reserve/student:d4          # остальное — как в Д2
  core:
    image: pcbk-reserve/core:d4b
    sysctls: {net.ipv4.tcp_fastopen: "0", net.ipv4.ip_forward: "0"}   # Д3б-R6, Д4б-R23
    networks:
      pcbk-front: {}
      pcbk-egress: {ipv4_address: 172.31.250.82}
      pcbk-stu-01: {ipv4_address: "${STU_NET:-172.31}.1.2"}
      # … то же для 02–10: "${STU_NET:-172.31}.N.2"
  student-01:                            # и 02–10 с точностью до номера
    secrets:
      - {source: student-01-pw, target: opencode-pw}
      - {source: student-01-llm, target: llm-token}
      - {source: student-01-data, target: data-token}
# secrets:
  student-01-data: {file: "${SECRETS_DIR:?SECRETS_DIR не задан}/student-01.data-token"}   # и 02–10
```

  Сторож — `pcbk-reserve/watchdog:d4b`.
- `compose.test.yaml`: у `core` — `LLM_TOTAL_USD: ${LLM_TOTAL_USD:-1000}`
  (только тесты).
- `conftest.py`:
  - `make_test_places` пишет ещё `student-NN.data-token` (48 hex, `0444`);
  - `core-tokens` фикстура собирает из `ops` и всех `student-NN.data-token`;
    `stack.core_token` теперь равен `data_token(1)`;
  - `STACK_VARS` + `LLM_TOTAL_USD`;
  - модульная `wait_for(pred: Callable[[], bool], timeout: float, step: float = 2.0) -> None`;
  - новые методы `stack`:
    - `data_token(n) -> str`;
    - `health_output(name) -> str` — `Output` последней записи
      `State.Health.Log`;
    - `data_events(last: int = 5) -> list[dict]` — `docker exec pcbk-core python -c …`,
      столбцы `caller`, `channel`, `tool`, `outcome` таблицы `agent_events`;
    - `converse(n: int, question: str, *, model: str | None = None, timeout: float = 180.0, host: str | None = None, pw_file: Path | None = None) -> dict`.

  Как работает `converse`:
  - одноразовый контейнер образа места в `pcbk-stu-NN`, метка
    `ONESHOT_LABEL`, `--read-only`, `--tmpfs /tmp:exec`;
  - `--entrypoint /usr/local/bin/opencode`, `-e BUN_BE_BUN=1`, аргумент
    `/app/converse.js`;
  - смонтированы `converse.js`, `/pw` (пароль места NN или `pw_file`) и
    `/req.json` = `{"url": "http://<host или STU.N.3>:4096", "question", "model", "timeout_s"}`;
  - `model` `"pcbk/z-ai/glm-5.3"` → `{"providerID": "pcbk", "modelID": "z-ai/glm-5.3"}`;
  - возвращает JSON из stdout.
- Produces (`converse.js`, Bun, без импортов):
  1. Basic из `/pw`.
  2. `POST /session` `{}` → `id`.
  3. `POST /session/{id}/prompt_async` с `{"parts": [{"type": "text", "text": question}], "model"?}`.
  4. Раз в секунду до `timeout_s`: `GET /session/status` и
     `GET /session/{id}/message`. Конец — сессии нет в статусе, а у
     последнего сообщения ассистента есть `time.completed` или `error`.
  5. Печатает один JSON: `session`, `status` (`done` | `timeout`),
     `final_text` (текст последнего сообщения ассистента), `error` (его
     `info.error` или `null`), `tools` (`[{tool, status, output}]` по всем
     частям-инструментам хода), `assistant_messages`.
- `workplace.py`: `MOUNTS` — плюс `/run/secrets/data-token`.
- `test_students.py`:
  - `test_each_workplace_uses_own_objects` — шесть источников, секреты
    `data-token`, `llm-token`, `opencode-pw`, в каждом `student-NN`;
  - `ls -A /run/secrets` → `data-token llm-token opencode-pw`;
  - `test_env_holds_only_own_secret` проверяет и чужие `data-token`;
  - из списка «должно быть 0» в `test_no_route_anywhere` убирается адрес `core`
    своей сети, если он там был. Его положительный контроль — в
    `test_place_reaches_only_core`.
- `test_edge.py`: `IMAGES` — `core`, сторож `:d4b`, место `:d4`;
  `DECLARED_ENV["pcbk-core"]` + `LLM_TOTAL_USD` (только тестовый стенд).
- `test_core.py`: у `pcbk-core` в `prod_config()` двенадцать сетей, в
  `pcbk-stu-NN` — адрес `.2`.
- `deploy/README.md`:
  - токены данных мест и `core-tokens`;
  - пересоздание мест с сохранением томов;
  - откат Д4б;
  - «новый студент»: к трём секретам места добавился `data-token`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_conversation.py
Q = "что сейчас по тегу 20FAKE_001_PV?"

def tool_requests(stack, session):
    return [r for r in stack.fake_requests() if r["session"] == session and r["body"].get("tools")]

def test_place_reaches_only_core(stack, running):                               # Review Focus 3
    stu = stack.stu_net
    assert stack.probe("pcbk-stu-01", f"{stu}.1.2", 8000) == 1                   # положительный контроль
    for addr, port in ((f"{stu}.2.2", 8000), (f"{stu}.2.3", 4096), ("172.31.250.82", 8000), ("172.31.250.84", 8080)):
        assert stack.probe("pcbk-stu-01", addr, port) == 0, addr
    assert stack.inspect("pcbk-core")["HostConfig"]["Sysctls"]["net.ipv4.ip_forward"] == "0"

def test_conversation_tool_via_mcp_and_cost(stack, running):                     # Review Focus 5
    wait_for(lambda: "mcp=connected" in stack.health_output("pcbk-student-01"), timeout=90)
    out = stack.converse(1, Q)
    assert (out["status"], out["error"]) == ("done", None) and out["final_text"].startswith("Готово:")
    tool = next(t for t in out["tools"] if t["tool"] == "pcbk_tag_now")
    assert tool["status"] == "completed" and json.loads(tool["output"])["status"] == "unavailable"   # историана нет — словами
    calls = [c for c in stack.llm_calls(20) if c["session"] == out["session"]]
    assert len(calls) >= 2 and {c["caller"] for c in calls} == {"student-01"}
    assert {c["cost_source"] for c in calls} == {"stream"}
    e = stack.data_events(1)[0]
    assert (e["caller"], e["channel"], e["tool"]) == ("student-01", "mcp", "tag_now")

def test_turn_limit_shown_in_chat_without_retry_storm(stack, running):           # Review Focus 2
    out = stack.converse(1, "FAKELOOP собери всё", timeout=240)
    assert len(tool_requests(stack, out["session"])) == 16
    assert "Предел шагов на ответ" in json.dumps(out["error"], ensure_ascii=False)
    time.sleep(15)
    assert len(tool_requests(stack, out["session"])) == 16                        # повторов нет

def test_budget_message_in_chat(stack, running):                                 # Review Focus 2
    stack.recreate("core", {"LLM_TOTAL_USD": "0.001"})                            # в бюджет упрётся первый или второй вызов хода
    try:
        wait_for(lambda: "mcp=connected" in stack.health_output("pcbk-student-01"), timeout=90)
        out = stack.converse(1, Q)
        assert "Общий бюджет стенда на модель" in json.dumps(out["error"], ensure_ascii=False)
    finally:
        stack.recreate("core", {})

def test_mcp_reconnects_after_core_restart(stack):                               # Review Focus 1; последний в модуле
    stack.stop("pcbk-core")
    try:
        stack.start("pcbk-student-02")                                            # место встаёт без core
        wait_for(lambda: "mcp=failed" in stack.health_output("pcbk-student-02"), timeout=90)
        data = stack.wait_status(lambda d: any(c["component"] == "student-02" and c["state"] == "warn"
                                               for c in d["checks"]), timeout=60)
        assert next(c["detail"] for c in data["checks"] if c["component"] == "student-02") == \
               "нет связи со службой данных (MCP)"
    finally:
        stack.start("pcbk-core")
    try:
        wait_for(lambda: "mcp=connected" in stack.health_output("pcbk-student-02"), timeout=150)
        out = stack.converse(2, Q)
        assert any(t["tool"] == "pcbk_tag_now" and t["status"] == "completed" for t in out["tools"])
    finally:
        stack.stop("pcbk-student-02")
```

- [ ] **Step 2: Run tests to verify they fail** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_conversation.py`. Expected: FAIL — `core` не в сетях мест, нет `data-token`, нет `converse`.
- [ ] **Step 3: Implement компоновку, `converse.js`, методы `stack`, правки тестов Д2–Д4а, README.**
- [ ] **Step 4: Run the whole local suite** — `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/) && uv run --python 3.12 --with pytest pytest -q tests/integration && docker network ls --filter name=pcbk- -q | wc -l`. Expected: PASS; сетей `pcbk-` после прогона — 0.
- [ ] **Step 5: Commit** — `git add compose.yaml compose.test.yaml deploy/README.md tests/integration/ && git commit -m "Места подключены: core на .2 в каждой сети мест, токен данных файлом, разговор через OpenCode и MCP, отказы прокси в чате без повторов"`.

---

### Task 5: Выкладка

Шаги — команды и вывод, который значит «прошло». `sudo` не нужен. Итоги — в
`docs/checks/D4b.md`.

**Files:**
- Create: `docs/checks/D4b.md`

- [ ] **Step 1: Предпроверки (на сервере).**
  - `docker ps -a --filter label=pcbk.role=student --format '{{.State}}' | sort | uniq -c`
    → все десять не `running`;
  - `docker inspect -f '{{.State.Health.Status}}' pcbk-core` → `healthy`;
  - строка «LLM-прокси и бюджет» в норме;
  - `docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d4b-before.txt`.

  Если место работает — выяснить, кто его поднял. Пересоздание работающего
  места рвёт разговор.
- [ ] **Step 2: Токены данных мест (на сервере, без вывода содержимого)**

```bash
cd /opt/pcbk-reserve/secrets && umask 077
for n in $(seq -w 1 10); do f=student-$n.data-token
  [ -e "$f" ] || { openssl rand -hex 24 | tr -d '\n' > "$f"; chmod 0444 "$f"; }
done
for f in ops.data-token student-*.data-token; do printf '%s %s\n' "${f%.data-token}" "$(sha256sum < "$f" | cut -c1-64)"; done > core-tokens.new
chmod 0444 core-tokens.new && mv core-tokens.new core-tokens
wc -l < core-tokens; stat -c '%a %s' student-*.data-token | sort -u
cat student-*.data-token student-*.llm-token student-*.pw ops.*-token | fold -w48 | sort -u | wc -l
```

Expected: `11`; `444 48`; `32` различных значения (у каждого места три
разных секрета, плюс два токена `ops`).
- [ ] **Step 3: Образы.** Локально `docker compose build core watchdog` и
  `docker build -t pcbk-reserve/student:d4 student/`, затем
  `docker save pcbk-reserve/core:d4b pcbk-reserve/watchdog:d4b pcbk-reserve/student:d4 | gzip | $SSH 'gunzip | docker load'`.
  Сверка `RootFS`. Expected: совпало; `:d4a` и `:d2` на сервере остались.
- [ ] **Step 4: Поднять.**
  1. На сервере `cp -p compose.yaml compose.yaml.d4a`; локально `rsync -a compose.yaml …:/opt/pcbk-reserve/`.
  2. `docker compose up -d --no-build core`: `core` входит в сети мест и
     перечитывает `core-tokens`.
  3. `docker compose up -d --no-build sp-ro watchdog`.
  4. `docker compose --profile students create --force-recreate --no-build $(printf 'student-%02d ' $(seq 1 10))`.

  Expected:
  - `pcbk-core` `healthy`,
    `docker inspect -f '{{len .NetworkSettings.Networks}}' pcbk-core` → `12`,
    в `pcbk-stu-NN` адрес `.2`;
  - десять мест `created`, на странице — 10 × «спит»;
  - «Серверный слой — контейнер — работает», «Служба данных — жива»,
    «LLM-прокси и бюджет — норма»;
  - у каждого места шесть источников монтирования, в каждом `student-NN`:
    цикл задачи 4 Д2 с `$# -eq 6`, ответ «10 из 10»;
  - время работы контейнеров Dify продолжает `~/pcbk-d4b-before.txt`.

  Откат:
  - вернуть `compose.yaml.d4a`;
  - `docker compose up -d --no-build core sp-ro watchdog`;
  - `docker compose --profile students create --force-recreate --no-build …` —
    места на `:d2`, тома сохраняются;
  - токены данных не удаляются.
- [ ] **Step 5: Commit** (после проверки на секреты) — `git add docs/checks/D4b.md && git commit -m "Выкладка Д4б: core в сетях мест, места :d4 спят, у каждого свой токен данных"`.

---

### Task 6: Живые проверки (шаги 2–6 — окно владельца)

Итог каждого шага — вердиктом [П] в `docs/checks/D4b.md`.

Помощники — в `~/pcbk-d4/` на сервере:
- `probe`, `oc`, `ocpid` (Д2) копируются из `$JOB` в начале задачи: Д2
  удалил свой каталог;
- `llmrows` (Д4а) уже там;
- новый `converse NN REQ.json [PWFILE]` запускает одноразовый
  `pcbk-reserve/student:d4` под `runsc`, как `stack.converse`; файл
  `converse.js` копируется из `tests/integration/`.

Файлы запросов пишутся в `$JOB` и копируются `scp` в `~/pcbk-d4/`, ответы
возвращаются в `$JOB`; на сервере всё удаляет закрытие.

Туннель к `core` — `$SSH -N -L 18000:172.31.250.82:8000`. Заголовки токенов
`ops` — `$JOB/ops.hdr` и `$JOB/ops-llm.hdr` (Д4а, задача 9). Снимки — способом
Д3а, в `docs/checks/D4b/`.

- [ ] **Step 1: Место 01 проснулось, изоляция, ключа в месте нет (без
  владельца).** `docker start pcbk-student-01`, ждать `healthy` и
  `mcp=connected` в `Output` последней записи здоровья.
  - Положительные контроли: `probe pcbk-stu-01 $STU.1.3 4096` → 1;
    `probe pcbk-stu-01 $STU.1.2 8000` → 1.
  - Нули: `$STU.2.2 8000`, `$STU.2.3 4096`, `172.31.250.82 8000`,
    `$HOST_LAN 22`, `$BDRV_HOST 1433`, `1.1.1.1 443`.
  - `docker exec pcbk-student-01 ls -A /run/secrets` → `data-token llm-token opencode-pw`.
  - Ключа OpenRouter в месте нет:
    `docker exec pcbk-student-01 sh -c 'cat /proc/'"$(ocpid 01)"'/environ /run/secrets/*' | grep -c -F -f /opt/pcbk-reserve/secrets/openrouter.key` → `0`.
    Чужих токенов данных тоже нет: тот же `grep` по файлам
    `student-0[2-9].data-token student-10.data-token` → `0`.

  Expected: как указано; без положительных контролей набор не засчитывается.
- [ ] **Step 2: Разговор (при владельце).**
  1. Тег называет владелец — например, тот же, что в сверке Д3б. Каталог
     `probe-out/` закрытие Д3б удалило.
  2. Вопрос `{"question": "Что сейчас по тегу <тег>?"}` — в `$JOB/q.json`,
     копия — в `~/pcbk-d4/`.
  3. `converse 01 ~/pcbk-d4/q.json > ~/pcbk-d4/conv.json`, копия — в `$JOB`.
     Сразу за ним — `tag_now` того же тега через туннель токеном `ops`:
     `$JOB/direct.json`, не позже 15 с — срок кэша `tag_now` в Д3б.

  Сверка — `python3` в `$JOB`. Правило «число совпало»: в `final_text` есть
  число (разделитель — точка или запятая), которое после округления до
  показанных знаков равно значению из ответа инструмента `pcbk_tag_now` в этом
  же ходе и значению `direct.json`.
  Expected:
  - владелец видит ответ;
  - в журнал — только «совпало / не совпало», статус инструмента, число
    обращений к модели;
  - снимок страницы с расходом — `01-conversation-spend.png`.

  Не совпало — это не прикрывается: вердикт, ответ владельцу и разбор по
  `conv.json` (что вернул инструмент, что написала модель).
- [ ] **Step 3: §11 п. 4 через OpenCode.** `llmrows` по `session` из
  `conv.json` → у всех строк `cost_source stream`, `cost > 0`,
  `provider_ok 1`, `caller student-01`; `spent_usd` на `/health/llm` вырос на
  их сумму. Expected: так; цена разговора — в журнал (не больше $0,10).
- [ ] **Step 4: Память «в работе» (§11 п. 5).** Сразу после хода
  `docker stats --no-stream --format '{{.MemUsage}}' pcbk-student-01` и
  `pids.current` cgroup места. Expected: числа в журнал; пороги Д2 — 700 МиБ
  и 358 потоков. Превышение — вопрос о лимитах в закрытие, а не правка сейчас.
- [ ] **Step 5: Учение «`core` перезапущен при работающих местах».**
  `docker restart pcbk-core` при работающем месте 01 — снимок
  `02-core-restarting.png`. После `healthy` — тот же вопрос через
  `converse 01`.
  Expected:
  - инструмент `completed` без ручных действий;
  - в выводе здоровья места — `mcp=connected`;
  - строки в норме, снимок `03-core-back.png`.
- [ ] **Step 6: Учение «место поднято без `core`».**
  1. `docker stop pcbk-core; docker start pcbk-student-02`.
  2. Не позже 90 с — «Рабочее место 02 — внимание — нет связи со службой данных
     (MCP)», снимок `04-mcp-down.png`.
  3. `docker start pcbk-core`.

  Expected: не позже 3 минут — `mcp=connected`, строка места в норме, снимок
  `05-mcp-back.png`. Затем `docker stop pcbk-student-02` → «спит».
- [ ] **Step 7: Место 01 — спать** (если дальше не нужно задаче 7). `docker stop pcbk-student-01` → «спит».
- [ ] **Step 8: Commit** (после проверки на секреты) — `git add docs/checks/ && git commit -m "Д4б: разговор через OpenCode — число совпало с tag_now, цена в потоке, память в работе, два учения MCP"`.

---

### Task 7: Проверка совместимости семи моделей (окно владельца, меньше $1)

Шесть пунктов research/08 §8 по каждой модели — на настоящем OpenCode через
наш прокси и MCP. Прогоны идут от одноразового места `ops` (Д4б-R21). Место
01 спит.

**Files:**
- Modify: `docs/checks/D4b.md`

- [ ] **Step 1: Потолок.** `ops_usd` с `/health/llm` →
  `LLM_OPS_SHARE_USD` = `ops_usd + 1`, округлённое вверх до цента →
  `LLM_OPS_SHARE_USD=<x> docker compose up -d --no-build core`. Expected:
  `core` `healthy`; сверх $1 прокси сам ответит `pcbk_share_spent`.
- [ ] **Step 2: Место `ops`.**
  1. Каталог `~/pcbk-d4/opsplace` (`0700`), в нём файлы `0444`:
     - копии `ops.llm-token` → `llm-token` и `ops.data-token` → `data-token`;
     - новый одноразовый пароль `pw` (`openssl rand -hex 24 | tr -d '\n'`).
  2. `docker run -d --rm --name pcbk-ops-place --runtime=runsc --network pcbk-stu-01 --ip $STU.1.9 --add-host core:$STU.1.2 --read-only --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:exec,mode=1777 --tmpfs /var/lib/opencode:exec,uid=10001,gid=10001 --tmpfs /work:uid=10001,gid=10001 -v ~/pcbk-d4/opsplace/pw:/run/secrets/opencode-pw:ro -v ~/pcbk-d4/opsplace/llm-token:/run/secrets/llm-token:ro -v ~/pcbk-d4/opsplace/data-token:/run/secrets/data-token:ro pcbk-reserve/student:d4`.

  Expected: `healthy`, `mcp=connected`.
- [ ] **Step 3: Девять прогонов.** Семь раз `pcbk/<модель>` в порядке
  `select_models("")`, затем `pcbk-or/google/gemini-3.8-flash` и
  `pcbk-or/anthropic/claude-sonnet-5.5`.
  - Каждый прогон — своя сессия: `converse` к
    `http://$STU.1.9:4096` с `pw` места `ops`.
  - Вопрос одинаковый: «Найди в каталоге тег по словам «<слова>» и скажи его
    текущее значение с единицей». Слова из описания тега задачи 6 называет
    владелец; файл — в `$JOB/compat.json`, копия — в `~/pcbk-d4/`.
  - После каждого прогона — `ops_usd`. Если сумма подошла к потолку, прокси
    остановит сам, и оставшиеся прогоны отмечаются «не прогнано: потолок».
- [ ] **Step 4: Таблица.** По каждому прогону — вывод `converse` и `llmrows`
  по его `session`:
  1. цикл инструментов `pcbk_catalog_search` → `pcbk_tag_now` завершён, в
     ошибке нет 400;
  2. у всех вызовов `cost_source stream`;
  3. на втором обращении `cached_tokens > 0`;
  4. у всех `provider_ok 1`;
  5. доля кириллицы в `final_text` не меньше 50 %, и число совпало с
     `pcbk_tag_now` по правилу задачи 6;
  6. `prompt_tokens` первого обращения — в тысячах; больше 15 тыс. —
     пометка «урезать встроенные инструменты через `permission`» для Д7/Д8.

  В `docs/checks/D4b.md` — таблица без имён тегов и значений:

  | Модель | Провайдер OpenCode | 1 цикл | 2 цена в потоке | 3 кэш | 4 провайдер | 5 русский и число | 6 вход, тыс. | обращений | цена прогона | вердикт |
  |---|---|---|---|---|---|---|---|---|---|---|

  Вердикт по research/08 §8: «проходит» — пункты 1–4 выполнены; «не проходит
  (п. N)»; для Gemini и Claude — какой провайдер OpenCode годится. Решение о
  финалистах Д8 — за владельцем. Не покрыто этой проверкой и остаётся [?] для
  Д7/Д8: примет ли модель сообщение ассистента последним (подсказка OpenCode о
  шагах, research/09 §9 п. 4) — у агента по умолчанию предела шагов нет.
- [ ] **Step 5: Уборка.**
  - `docker rm -f pcbk-ops-place`;
  - `rm -rf ~/pcbk-d4/opsplace ~/pcbk-d4/conv.json`;
  - `docker compose up -d --no-build core` — `LLM_OPS_SHARE_USD` по умолчанию;
  - в `$JOB` — `q.json`, `compat.json`, `direct.json`, вывод прогонов;
  - строка «потрачено на проверку: $X» — разница `ops_usd`.

  Expected: X меньше $1; `docker ps -a --filter name=pcbk-ops` пусто.
- [ ] **Step 6: Commit** (после проверки на секреты) — `git add docs/checks/D4b.md && git commit -m "Д4б: совместимость семи моделей через OpenCode и прокси — таблица, меньше \$1"`.

---

### Task 8: Закрытие дня

- [ ] **Step 1: Документы.**
  - `docs/DESIGN-platform-2026-09-29.md`:
    - §6 — провайдер и MCP места как построены (`{file:…}`, `data-token`,
      `oauth: false`, семь моделей, окно 131 072);
    - §11 п. 4 — [П] «через OpenCode»; п. 5 — [П] «в работе»;
    - §13 — п. «Д4б» (решения Д4б-R18…R23);
    - §10 — ссылка на таблицу совместимости как вход Д8.
  - `docs/PLAN-platform-2026-09-29.md`: строка Д4б — «готово»; строка Д8 —
    «финалисты — по таблице Д4б».
  - `README.md`: «Д4 готов (Д4а и Д4б)», дальше Д5.
- [ ] **Step 2: Критик** (Opus 5.5); петля — до нуля блокеров, не больше двух раундов; третий — после разговора с владельцем.
- [ ] **Step 3: Слияние.** В `main`, тег `platform-d4b`. Перед пушем:
  - проверка на секреты по ветке;
  - `git ls-files | grep -cE '(core-tokens|llm-tokens|\.(pw|llm-token|data-token)|openrouter.*\.key)$'` → `0`.

  Затем `git push origin main platform-d4b`, чистый клон. На сервере удалить
  `~/pcbk-d4/` и `~/pcbk-d4b-before.txt`; в `$JOB` — `ops-llm.*`, `ops.hdr`,
  `q.json`, `compat.json`, `direct.json`, `conv.json` и вывод прогонов.
- [ ] **Step 4: План и факт.** Строка «план 9,75 ч / факт Y ч по git» в
  `docs/checks/D4b.md`
  (`git log --reverse --format=%cI platform-d4a..platform-d4b | sed -n '1p;$p'`).
- [ ] **Step 5: Владельцу** — «Д4 готов», снимки, строка «число совпало»,
  таблица совместимости, потрачено за Д4. Вопросы:
  - финалисты Д8 по таблице (research/08 §8 предлагает GLM-5.3, DeepSeek-V4.1-Flash,
    Gemini 3.8 Flash, GPT-6 Sol);
  - Gemini через флекс-точку;
  - лимиты места, если память «в работе» выше порога.
