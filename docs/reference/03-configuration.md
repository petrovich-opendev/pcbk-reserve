# Конфигурация: переменные окружения, секреты, тома, сети, конфиг мест

Этот документ — справочник по всем точкам настройки репозитория pcbk-reserve: переменные окружения каждой
роли и службы, файлы секретов, тома и сети `compose.yaml`, прокси сокета Docker, конфигурация рабочих мест
OpenCode и проверки, которые код выполняет при старте. Архитектура служб и ролей — в
[01-architecture.md](01-architecture.md), порядок сборки и выкладки — в
[02-build-and-deploy.md](02-build-and-deploy.md). Поведение самих ролей (служба данных, LLM-прокси, шлюз,
аналитика) описано в своих документах — здесь только их настройки.

## 1. Общая модель настроек

Код не читает `os.environ` в произвольных местах — каждая роль ядра (`core`) собирает свои настройки одним
вызовом `from_env()` неизменяемого (`@dataclass(frozen=True)`) класса:

| Класс | Файл | Роль/служба |
|---|---|---|
| `Settings` | `core/pcbk_core/settings.py` | общий слой `core` (порт, пути, учения свежести/данных) |
| `LlmSettings` | `core/pcbk_core/settings.py` | роль `llm` (LLM-прокси) |
| `GatewaySettings` | `core/pcbk_core/settings.py` | роль `gateway` (шлюз) |
| `AnalyticsSettings` | `core/pcbk_core/analytics/settings.py` | роль аналитики КДМ-1 |
| `Settings` | `watchdog/pcbk_watchdog/main.py` | служба `watchdog` |

Общие правила разбора (действуют одинаково во всех четырёх настройках `core`):

- пустое значение переменной (или её отсутствие) — берётся умолчание поля; разбор видит только непустые строки;
- тип поля по умолчанию решает разбор: поле со строковым умолчанием принимает любую строку как есть, поле с
  `int`/`float` умолчанием — пробует привести `raw` к этому типу и для большинства числовых полей требует
  значение больше нуля (специфичные пределы — в таблицах ниже);
- ошибка — всегда `ValueError` с именем переменной и правилом; у `LlmSettings`, `GatewaySettings` и
  `AnalyticsSettings` текст **не включает само значение** (путь или токен могли попасть в переменную по
  ошибке) — в журнал, `config_error` роли и на страницу `/status`/`/health/*` уходит только имя переменной и
  правило. У общего слоя (`Settings`) это не так: для его числовых и перечислимых полей (`CORE_PORT`,
  `FRESH_POLL_S`, `CATALOG_REFRESH_S`, `CATALOG_DEADLINE_S`, `DRILL_FRESHNESS`, `DRILL_DATA`) `raw`-значение
  или разобранное число **попадает в текст ошибки** (например, `f"{f.name}: нужно число больше нуля, получено
  {raw!r}"`); секретам и путям (`BDRV_ENV_FILE`, `WHITELIST_PATH`, `TOKENS_FILE`, `DB_PATH`, `AGENTS_ROOT`,
  `LESSONS_ROOT`) это не угрожает не из-за скрытия значения, а потому, что это строковые поля без какой-либо
  проверки в `from_env()` — они принимаются как есть и `ValueError` по ним вообще не возникает;
- `LlmSettings`, `GatewaySettings` и `AnalyticsSettings` — отдельные классы именно для того, чтобы ошибка в
  `LLM_*`/`GW_*`/`ANALYTICS_*` не ронял `main()` и не закрывала прочие роли (`Settings` общего слоя и роль
  «данные» живут отдельно от ошибки в LLM-прокси, шлюзе или аналитике);
- при ошибке у роли своя «запасная» настройка, а не падение процесса:
  - `LlmSettings.files_from_env()` — все поля умолчания, но пути к файлам секретов (`OPENROUTER_KEY_FILE`,
    `OPENROUTER_MGMT_KEY_FILE`, `LLM_TOKENS_FILE`) берутся из окружения, чтобы роль с ошибкой настройки всё
    равно читала свои ключи и токены, а не умолчания образа;
  - `GatewaySettings.valid_from_env()` и `AnalyticsSettings.valid_from_env()` — поля, прошедшие проверку по
    отдельности, берутся из окружения, остальные — умолчания: опечатка в одном поле (например, в `GW_ORIGIN`)
    не сбрасывает прочие (`STU_NET`, `SP_CTL_URL`) к умолчаниям, которые могли бы не совпасть с реальной сетью;
  - у `AnalyticsSettings` так же: опечатка, например, в `ANALYTICS_LLM_EXPORT_POLICY` не включает и не
    выключает `ANALYTICS_ENABLED` молча, если оно само прочиталось верно.
- причина ошибки видна на странице состояния и в ответах `/health/*` (роль «с ошибкой настройки» отвечает 503
  с текстом причины), а не только в логе контейнера — подробности у каждой роли в её собственном документе
  ([10-data-service.md](10-data-service.md), [12-llm-proxy.md](12-llm-proxy.md), [13-gateway.md](13-gateway.md),
  [16-analytics.md](16-analytics.md)).

Сторож (`watchdog`) устроен проще: один класс `Settings`, ошибка в любом поле — `ValueError` при старте
процесса (`main()` вызывает `Settings.from_env()` без перехвата), то есть неверная переменная не даёт
контейнеру стартовать вовсе — это намеренно иначе, чем у `core` (сторож не несёт роли с частичной
деградацией).

## 2. Переменные окружения `core`

Это переменные `environment:` сервиса `core` в `compose.yaml` и строки `deploy/env.example`, которые туда
попадают. Колонка «в `.env`/`compose.yaml`» показывает умолчание, которое фактически прокинуто в контейнер
(`${ИМЯ:-умолчание}`); у полей без этой колонки компоновка не передаёт переменную — значение остаётся
умолчанием кода, и само поле всё равно в принципе переопределимо через окружение процесса (строковые поля —
без дополнительной проверки), просто для этого придётся править сам `compose.yaml` (блок `environment:`
сервиса `core`), а не `.env`.

### 2.1 Общий слой (`Settings`, `core/pcbk_core/settings.py`)

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `CORE_PORT` | `8000` | целое, `0 < CORE_PORT ≤ 65535` | `compose.yaml`: жёстко `"8000"` | порт uvicorn внутри контейнера; сервис `core` в сеть `pcbk-public` не публикуется, порт не виден снаружи стенда |
| `BDRV_ENV_FILE` | `/run/secrets/bdrv.env` | строка (без проверки) | нет — путь фиксирован монтированием секрета `bdrv-env` | путь к файлу учётных данных историана БДРВ (читает `secrets.BdrvConfig.from_env_file`) |
| `WHITELIST_PATH` | `/app/data/whitelist.txt` | строка | нет — путь фиксирован bind-мантом `DATA_DIR/whitelist.txt` | белый список тегов историана, читает роль «данные» при старте |
| `TOKENS_FILE` | `/run/secrets/core-tokens` | строка | нет — путь фиксирован монтированием секрета `core-tokens` | таблица хешей токенов вызывающих службы данных и MCP (`secrets.TokenTable`) |
| `DB_PATH` | `/var/lib/pcbk-core/core.db` | строка | нет — путь на томе `pcbk-core-data` | SQLite: журнал событий, доли LLM-бюджета, учётные записи, журнал действий кабинета преподавателя |
| `FRESH_POLL_S` | `30.0` | число `> 0` | `${FRESH_POLL_S:-30}` | период опроса свежести историана ролью «данные» |
| `CATALOG_REFRESH_S` | `86400.0` | число `> 0` | нет | период полного обновления каталога тегов |
| `CATALOG_DEADLINE_S` | `60.0` | число `≥ 10.0` (`MIN_CATALOG_DEADLINE_S`) | `${CATALOG_DEADLINE_S:-60}` | общий срок загрузки каталога тегов при старте; меньше 10 с каталог не успел бы загрузиться никогда (два запаса ворот по 5 с) |
| `DRILL_FRESHNESS` | `""` | `""`, `freeze_stamps` или `freeze_poll` | `${DRILL_FRESHNESS:-}` | режим учений свежести: `freeze_stamps` — метки свежести не растут, `freeze_poll` — опрос останавливается после первого цикла |
| `DRILL_DATA` | `""` | `""` или `tool_error` | `${DRILL_DATA:-}` | учения службы данных: `tool_error` — все инструменты чтения отвечают ошибкой без SQL-запроса и без обращения к историану |
| `AGENTS_ROOT` | `/srv/agents` | строка | нет — путь на bind-манте `AGENTS_DIR` | корень каталога агентов студентов на запись (quarantine/archive — в корне, вне каталогов мест) |
| `LESSONS_ROOT` | `/srv/lessons` | строка | нет — путь на bind-манте `LESSONS_DIR`, только чтение | корень пакета уроков вкладки «Уроки» |

### 2.2 LLM-прокси (`LlmSettings`, `core/pcbk_core/settings.py`)

Подробное описание логики каждого поля — в [12-llm-proxy.md](12-llm-proxy.md); здесь только контракт
настройки.

**Точка OpenRouter и файлы секретов**

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | `http://` или `https://` с хостом (`urlsplit`, порт в допустимых пределах) | нет (в `compose.test.yaml` — на двойник) | базовый адрес OpenRouter |
| `OPENROUTER_KEY_FILE` | `/run/secrets/openrouter.key` | строка | нет — путь секрета `openrouter-key` | основной ключ OpenRouter; пустой файл — «ключа нет» |
| `OPENROUTER_MGMT_KEY_FILE` | `/run/secrets/openrouter-mgmt.key` | строка | нет — путь секрета `openrouter-mgmt-key` | ключ управления (опрос остатка счёта `/credits`); нечитаемый — опрос просто выключается, прокси работает |
| `LLM_TOKENS_FILE` | `/run/secrets/llm-tokens` | строка | нет — путь секрета `llm-tokens` | таблица хешей токенов вызывающих LLM-прокси (своя, отдельная от `core-tokens`) |

**Модели и запасная модель**

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `LLM_MODELS` | `""` (все кандидаты) | подмножество имён `CANDIDATES` из `llm/models.py` через запятую (`select_models`) | `${LLM_MODELS:-}` | список моделей, которыми отвечает прокси; пусто — разрешены все кандидаты кода |
| `LLM_FALLBACK` | `""` (нет) | пары `<основная>=<запасная>` через запятую, оба имени — точные имена `LLM_MODELS`, основная ≠ запасная, без повторов (`parse_fallback`) | `${LLM_FALLBACK:-}` | при отказе основной модели вида «подождите/недоступен/перегружен» до первого байта ответа прокси один раз за вызов молча повторяет запрос на запасной модели |

**Выключатель администратора**

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `LLM_STATE` | `"on"` | `on` или `stopped` | `${LLM_STATE:-on}` | `stopped` — маршруты `/llm/` отказывают всем вызовам (свойство `LlmRole.stopped`), строка страницы состояния красная; `on` — прокси работает |

**Бюджет и доли**

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `LLM_TOTAL_USD` | `1000.0` | число `> 0` | `${LLM_TOTAL_USD:-}` | общий бюджет LLM-прокси на все места и `ops`, $ |
| `LLM_SHARE_USD` | `100.0` | число `> 0` | нет | доля бюджета места по умолчанию, $ (больше общего бюджета — допустимо, тогда держит общий) |
| `LLM_OPS_SHARE_USD` | `100.0` | число `> 0` | `${LLM_OPS_SHARE_USD:-100}` | доля бюджета учётной записи `ops` (администратор/боты), $ |
| `LLM_KEY_DEDICATED` | `"on"` | `on` или `off` | `${LLM_KEY_DEDICATED:-on}` | `on` — ключ OpenRouter только у этого стенда, расход сверяется с `/key`; `off` — ключ общий с другой службой, сверки нет |

**Пределы вызова**

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `LLM_MAX_TOKENS` | `16000` | целое `1…16000` | нет | предел токенов ответа модели |
| `LLM_TURN_CALLS` | `16` | целое `1…100` | нет | предел числа шагов (вызовов инструментов) в одном ходе |
| `LLM_POLL_S` | `60.0` | число `≥ 5.0` | нет (в `compose.test.yaml` — `5`) | период опроса OpenRouter (не чаще раза в 5 с) |
| `LLM_MAX_BODY_BYTES` | `1048576` | целое `65536…4194304` | нет | предел размера тела запроса к модели |
| `LLM_CALLER_CONCURRENCY` | `3` | целое `1…16` | нет | предел одновременных вызовов на одного вызывающего |

**Учения**

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `DRILL_LLM` | `""` | `""` или `402` | `${DRILL_LLM:-}` | `402` — учение «на счёте OpenRouter кончились деньги»: отказ без обращения к OpenRouter |

**Прогон отбора модели и его тормоз** (все три `LLM_RUN_*` задаются вместе или не задаются вовсе)

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `LLM_RUN_ID` | `""` | `[a-z0-9-]{1,32}` | `${LLM_RUN_ID:-}` | идентификатор прогона отбора модели; новый id — новая точка отсчёта бюджета прогона |
| `LLM_RUN_USD` | `0.0` | число `0 < value ≤ LLM_TOTAL_USD` | `${LLM_RUN_USD:-}` | потолок расхода прогона, $, поверх общего бюджета и долей |
| `LLM_RUN_CALLERS` | `""` | список `student-NN`/`ops` через запятую, без повторов (`parse_run_callers`) | `${LLM_RUN_CALLERS:-}` | кто участвует в прогоне отбора |
| `LLM_RUN_MEM_FAIL_MIB` | `1024` | целое `1…1048576` | `${LLM_RUN_MEM_FAIL_MIB:-${MEM_FAIL_MIB:-1024}}` | тормоз прогона: свободной памяти сервера меньше этого — причина `memory` |
| `LLM_RUN_SWAP_GROW_MIB` | `512` | целое `1…1048576` | `${LLM_RUN_SWAP_GROW_MIB:-${SWAP_GROW_WARN_MIB:-512}}` | тормоз: swap вырос от базы или за 10 мин на столько — причина `swap` |
| `LLM_RUN_SWAP_BASE_MIB` | `-1` | целое `-1…1048576` | `${LLM_RUN_SWAP_BASE_MIB:--1}` | база swap прогона, МиБ; `-1` — берётся с первой проверки, хранится в `llm_meta` |
| `LLM_RUN_PSI_FULL_MAX` | `10.0` | число `0 < value ≤ 100.0` | `${LLM_RUN_PSI_FULL_MAX:-${PSI_FULL_MAX:-10}}` | тормоз: давление памяти `full avg10` выше этого, % — причина `psi` |
| `LLM_RUN_MEMINFO` | `/proc/meminfo` | строка | нет (в `compose.test.yaml` — файл-двойник) | путь для тормоза прогона: сведения о памяти |
| `LLM_RUN_VMSTAT` | `/proc/vmstat` | строка | нет (двойник в тестах) | путь для тормоза прогона: статистика ядра |
| `LLM_RUN_PRESSURE` | `/proc/pressure/memory` | строка | нет (двойник в тестах) | путь для тормоза прогона: давление памяти (PSI) |

### 2.3 Шлюз (`GatewaySettings`, `core/pcbk_core/settings.py`)

Подробности поведения — [13-gateway.md](13-gateway.md). Проверка полей идёт **по отдельности**
(`GatewaySettings._parse`): опечатка в одном поле не сбрасывает прочие к умолчаниям, а собирается в список
ошибок; есть дополнительное правило, зависящее от двух полей сразу (`GW_REAPER_TICK_S ≤ GW_IDLE_STOP_S / 2`).

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `GW_ORIGIN` | `""` | `https://хост[:порт]` строчными, без пути; порт `≤ 65535` и не `443` (регэкс `_GW_ORIGIN`) | `${GW_ORIGIN:-}` | публичный `Origin` страницы входа; пусто — запись (`POST`) проходит только с `Sec-Fetch-Site: same-origin`, по заголовку `Origin` — никогда |
| `GW_IDLE_STOP_S` | `1800.0` | число `≥ 60` | `${GW_IDLE_STOP_S:-1800}` | простой места до автоматического сна, с |
| `GW_REAPER_TICK_S` | `30.0` | число `> 0` и `≤ GW_IDLE_STOP_S / 2` | `${GW_REAPER_TICK_S:-30}` | такт уборщика простоя мест |
| `GW_WAKE_TIMEOUT_S` | `60.0` | число `10…300` | нет | срок ожидания готовности места после `start` |
| `GW_SESSION_TTL_S` | `43200.0` | число `≥ 300` | нет | срок жизни сессии входа |
| `GW_PLACE_PW_DIR` | `/run/secrets` | строка (без отдельной проверки) | нет — каталог секретов мест фиксирован монтированием | каталог, где шлюз ищет `student-NN.pw` — пароли мест, которые знает только шлюз |
| `SP_CTL_URL` | `http://pcbk-sp-ctl:2375` | `http://хост[:порт]` (`urlsplit`) | нет | адрес прокси сокета Docker на запись (`sp-ctl`) — им шлюз будит и останавливает места |
| `STU_NET` | `172.31` | два октета без ведущих нулей, каждый `≤ 255` (`_STU_NET`) | `${STU_NET:-172.31}` | первые два октета сетей мест: место `N` — подсеть `{STU_NET}.N.0/28`; по нему же шлюз сверяет токен места с его сетью |
| `GW_MAX_BODY_BYTES` | `524288` | целое, только цифры, `65536…4194304` | нет | предел тела запроса `/pcbk/api/oc/` на стороне `core` (у `edge` — свой предел `client_max_body_size 512k`) |
| `DRILL_GATEWAY` | `""` | `""`, `freeze_reaper` или `bad_place_pw` | `${DRILL_GATEWAY:-}` | учения: `freeze_reaper` — уборщик простоя замирает после первого такта; `bad_place_pw` — шлюз портит пароль места |
| `DRILL_AGENTS` | `""` | `""` или `freeze_reload` | `${DRILL_AGENTS:-}` | учения воркера перезагрузки агентов студентов: замирает после первого прохода |
| `DRILL_TEACHER` | `""` | `""`, `shares_stale` или `journal_down` | `${DRILL_TEACHER:-}` | учения кабинета преподавателя: `shares_stale` — перевод доли пишет диск/журнал действий, но не обновляет память LLM-прокси; `journal_down` — любая запись журнала действий падает до SQL (кабинет закрыт) |

### 2.4 Аналитика КДМ-1 (`AnalyticsSettings`, `core/pcbk_core/analytics/settings.py`)

Подробности — [16-analytics.md](16-analytics.md). Все поля — несекретные; у подсистемы своих секретов нет
(ключ LLM и токены вызывающих остаются у ролей `llm`/`gateway`). Проверка полей — по отдельности, тем же
приёмом, что у `GatewaySettings`.

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `ANALYTICS_ENABLED` | `False` | `1/true/on/yes` или `0/false/off/no`, без учёта регистра | `${ANALYTICS_ENABLED:-}` | выключатель всей подсистемы; по умолчанию выключена — старый вход/чат/калькуляторы/уроки не затрагиваются |
| `ANALYTICS_CORPUS_DIR` | `/srv/analytics` | абсолютный путь (начинается с `/`) | **нет** (см. предупреждение ниже) | путь к корпусу аналитики **внутри контейнера** |
| `ANALYTICS_STATE_PATH` | `/var/lib/pcbk-core/analytics.db` | абсолютный путь | нет — файл на томе `pcbk-core-data` | файл состояния диалогов и заданий аналитики, отдельный от `core.db` |
| `ANALYTICS_LLM_MODELS` | `""` (без доп. ограничения) | подмножество имён `CANDIDATES` через запятую (`select_models`) | `${ANALYTICS_LLM_MODELS:-}` | дополнительное сужение моделей для аналитических заданий сверх `LLM_MODELS` активной роли `llm` |
| `ANALYTICS_LLM_EXPORT_POLICY` | `public_only` | `public_only`, `approved_subset` или `none` (регистр/пробелы по краям нормализуются) | `${ANALYTICS_LLM_EXPORT_POLICY:-}` | что уходит в исходящий запрос к модели: только публичный контекст / полный контент пакета (включая внутренний и данные БДРВ) / модель не вызывается вовсе |
| `ANALYTICS_REVIEWERS` | `grants` | `grants` или `all_students` (точное совпадение) | `${ANALYTICS_REVIEWERS:-}` | кто решает заявки согласования: только по явному назначению, или любой вошедший `student` без назначения |
| `ANALYTICS_TEACHER_PAYER` | `""` | произвольная строка (обрезаются пробелы) | `${ANALYTICS_TEACHER_PAYER:-}` | непустая строка — флаг «платящий за преподавателя настроен»; пусто — платный разбор для преподавателя недоступен |
| `ANALYTICS_HISTORY_RETENTION_DAYS` | `365.0` | число `≥ 1.0` | `${ANALYTICS_HISTORY_RETENTION_DAYS:-}` | срок хранения истории диалогов/сообщений, дни |
| `ANALYTICS_EVIDENCE_RETENTION_DAYS` | `365.0` | число `≥ 1.0` | `${ANALYTICS_EVIDENCE_RETENTION_DAYS:-}` | срок хранения неизменяемых пакетов доказательств, дни (отдельно от истории) |
| `ANALYTICS_JOB_TIMEOUT_S` | `120.0` | число `≥ 5.0` (`MIN_JOB_TIMEOUT_S`) | `${ANALYTICS_JOB_TIMEOUT_S:-}` | срок одной стадии задания (`queued → … → completed`) до `failed` по таймауту |

### 2.5 Переменные-пути `compose.yaml` (не параметр `Settings`, но обязательны для `docker compose config`)

Эти переменные не попадают в `environment:` контейнера — `docker compose` использует их сам, как источники
bind-монтов и секретов, или подставляет в адрес сети. Все они записаны в `compose.yaml` с оператором
`${ИМЯ:?сообщение}`: если переменная не задана (пустая строка — тоже не задана), `docker compose config` и
любой `up`/`create` отказывают с этим сообщением, контейнеры не создаются вовсе.

| Переменная | Умолчание в `deploy/env.example` | Используется | Что задаёт |
|---|---|---|---|
| `DOCKER_GID` | пусто — обязательно заполнить | `user:` сервисов `sp-ro`/`sp-ctl` | gid группы `docker` хоста (`getent group docker \| cut -d: -f3`) |
| `TLS_DIR` | пусто — обязательно заполнить | тома `edge`, `watchdog` | хостовый каталог с `cert.pem`/`key.pem` |
| `DATA_DIR` | пусто — обязательно заполнить | том `core` (`whitelist.txt`) | хостовый каталог белого списка тегов |
| `SECRETS_DIR` | `/opt/pcbk-reserve/secrets` | все записи `secrets:` в самом низу `compose.yaml` | хостовый каталог файлов-секретов (§6) |
| `AGENTS_DIR` | `/opt/pcbk-reserve/agents` | том `core` (rw) и тома чтения каждого `student-NN` | хостовый корень каталога агентов студентов |
| `LESSONS_DIR` | `/opt/pcbk-reserve/lessons` | том `core` (`ro`) | хостовый каталог пакета уроков |

Отдельно от этих шести: `ANALYTICS_CORPUS_DIR` и `KDM1_CHAT_KNOWLEDGE_DIR` — такие же хостовые пути для
томов (`core`/`student-NN` соответственно), но **не обязательны** (без оператора `:?`): пустое значение даёт
пустой именованный том `pcbk-analytics-empty`, и `docker compose config` не падает — см. предупреждение ниже
и §2.4.

**ВНИМАНИЕ — одноимённая, но разная переменная.** `ANALYTICS_CORPUS_DIR` в `deploy/env.example` — это **путь
на хосте** (источник bind-монта для тома в `compose.yaml`: `"${ANALYTICS_CORPUS_DIR:-pcbk-analytics-empty}:/srv/analytics:ro"`),
и в сам контейнер как переменная среды она **не передаётся**. Поле `ANALYTICS_CORPUS_DIR` настроек внутри
контейнера всегда равно умолчанию кода `/srv/analytics` — это ровно то, во что смонтирован хостовый каталог.
То же самое с `KDM1_CHAT_KNOWLEDGE_DIR` (ниже) — хостовый путь для тома `/work/kdm1-knowledge` у рабочих мест,
в окружение контейнеров не идёт.

## 3. Переменные окружения сторожа (`watchdog`)

Класс `Settings` — `watchdog/pcbk_watchdog/main.py`. Часть переменных в `compose.yaml` жёстко привязана к
структуре самого стенда (порт, пути томов) и не параметр `.env`; часть читает `.env` через `${ИМЯ:-умолчание}`.

| Переменная | Умолчание | Проверка | В `.env`/`compose.yaml` | Что делает |
|---|---|---|---|---|
| `LISTEN_PORT` | `8090` | целое (без явного диапазона в коде) | `compose.yaml`: жёстко `"8090"` | порт HTTP-сервера сторожа (`/status`, `/status.json`, `/status.js`, `/healthz`) |
| `DOCKER_URL` | `http://pcbk-sp-ro:2375` | `http://` с хостом (`DockerReader.__init__`) | `compose.yaml`: жёстко адрес `sp-ro` | адрес прокси сокета Docker на чтение |
| `DOCKER_TIMEOUT_S` | `3.0` | число `> 0` | `${DOCKER_TIMEOUT_S:-3}` | срок HTTP-запроса к прокси сокета |
| `TLS_CAFILE` | `/app/tls/cert.pem` | строка | `compose.yaml`: жёстко, том — копия `cert.pem` | сертификат, которым сторож проверяет, что `edge` отдаёт именно его (вид проверки `tls`) |
| `JOURNAL_PATH` | `/var/lib/pcbk-watchdog/journal.db` | строка | `compose.yaml`: жёстко, том `pcbk-watchdog-journal` | SQLite-журнал переходов состояний (события «стало хуже»/«стало лучше») |
| `MEMINFO_PATH` | `/proc/meminfo` | строка | `compose.yaml`: жёстко (в `compose.test.yaml` — файл-двойник) | источник сведений о памяти хоста |
| `VMSTAT_PATH` | `/proc/vmstat` | строка | `compose.yaml`: жёстко (двойник в тестах) | счётчик `oom_kill` и статистика ядра — причины перезапуска контейнеров по памяти |
| `PRESSURE_PATH` | `/proc/pressure/memory` | строка | `compose.yaml`: жёстко (двойник в тестах) | давление памяти (PSI, `full avg10`) |
| `COMPONENTS_PATH` | `/app/components.json` | строка | `compose.yaml`: жёстко | список проверяемых компонентов страницы состояния (встроен в образ) |
| `TICK_S` | `10.0` | число `> 0` | `${TICK_S:-10}` | период такта проверок |
| `STALE_AFTER_S` | `30` | целое; плюс производное правило через `tick_budget_s` (должно быть `> 0`) | `${STALE_AFTER_S:-30}` | возраст снимка, после которого страница считает его устаревшим |
| `DISPLAY_TZ` | `UTC` | имя пояса, принимаемое `zoneinfo.ZoneInfo` | `${DISPLAY_TZ:-UTC}` | пояс времени на странице состояния (например, `Europe/Moscow`) |
| `MEM_WARN_MIB` | `2048` | целое | `${MEM_WARN_MIB:-2048}` | свободная память сервера ниже — строка «внимание» |
| `MEM_FAIL_MIB` | `1024` | целое | `${MEM_FAIL_MIB:-1024}` | свободная память сервера ниже — строка «сбой» |
| `SWAP_GROW_WARN_MIB` | `512` | целое `1…1048576` | `${SWAP_GROW_WARN_MIB:-512}` | swap вырос за 10 минут на столько — «сервер вытесняет память в swap» |
| `PSI_FULL_MAX` | `10.0` | число `0 < value ≤ 100` | `${PSI_FULL_MAX:-10}` | давление `full avg10` выше этого, % — «давление выше порога» |
| `DRILL_FREEZE_LOOP` | `False` | строго `"0"` или `"1"` | `${DRILL_FREEZE_LOOP:-0}` | учение «цикл молчит при живом HTTP»: `1` — цикл проверок замирает после первого такта |
| `HIST_WARN_S` | `300` | целое, `0 < HIST_WARN_S < HIST_FAIL_S` | `${HIST_WARN_S:-300}` | возраст метки свежести историана — «внимание» |
| `HIST_FAIL_S` | `900` | целое, `0 < HIST_WARN_S < HIST_FAIL_S` | `${HIST_FAIL_S:-900}` | возраст метки свежести историана — «сбой» |
| `HIST_STALE_S` | `140` | целое `> 0` | `${HIST_STALE_S:-140}` | опрос службы данных старше этого — «неизвестно»; формула: `max(120, CATALOG_DEADLINE_S + 2×FRESH_POLL_S + 20)` |
| `GW_STALE_S` | `120` | целое `> 0` | `${GW_STALE_S:-120}` | метка такта уборщика простоя шлюза старше этого — «уборщик простоя молчит»; не меньше `4 × GW_REAPER_TICK_S` |
| `AGENTS_STALE_S` | `60` | целое `> 0` | `${AGENTS_STALE_S:-60}` | метка прохода воркера перезагрузки агентов старше этого — «перезагрузка агентов молчит» |
| `LLM_RUN_SILENT_S` | `300` | целое `> 0` | `${LLM_RUN_SILENT_S:-300}` | к модели прогона отбора не обращались дольше этого — «прогон отбора молчит» |
| `TEACHER_STALE_S` | `120` | целое `> 0` | `${TEACHER_STALE_S:-120}` | сверка долей мест (такт уборщика шлюза) старше этого — «сверка долей молчит»; не меньше `4 × GW_REAPER_TICK_S` |

## 4. Переменные окружения рабочих мест (`student`)

Образ `student` не читает `.env` стенда — его переменные делятся на три группы.

**Фиксированные в `student/Dockerfile` (`ENV`, не параметр развёртывания):**

| Переменная | Значение | Что делает |
|---|---|---|
| `HOME` | `/home/pcbk` | домашний каталог пользователя `pcbk` (uid 10001) |
| `XDG_CONFIG_HOME` | `/etc/pcbk-opencode` | каталог конфигурации OpenCode (`opencode.json`, агенты, инструменты — все из `student/config/`) |
| `XDG_DATA_HOME` | `/var/lib/opencode/data` | данные OpenCode на томе `pcbk-student-NN-state` |
| `XDG_CACHE_HOME` | `/var/lib/opencode/cache` | кэш OpenCode на том же томе |
| `XDG_STATE_HOME` | `/var/lib/opencode/state` | состояние OpenCode на том же томе |
| `TMPDIR` | `/tmp` | временные файлы — в `tmpfs` места |
| `OPENCODE_DISABLE_MODELS_FETCH` | `1` | не опрашивать список моделей по сети |
| `OPENCODE_DISABLE_AUTOUPDATE` | `1` | не проверять обновления |
| `OPENCODE_DISABLE_SHARE` | `1` | функция «поделиться разговором» выключена |
| `OPENCODE_DISABLE_LSP_DOWNLOAD` | `1` | не скачивать LSP-серверы |
| `OPENCODE_DISABLE_PROJECT_CONFIG` | `1` | не читать конфиг из рабочего каталога проекта (`/work`) |
| `OPENCODE_DISABLE_DEFAULT_PLUGINS` | `1` | встроенные плагины OpenCode выключены |
| `OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER` | `true` | слежка за файлами выключена |
| `BUN_RUNTIME_TRANSPILER_CACHE_PATH` | `0` | кэш транспайлера Bun выключен |
| `OPENCODE_SERVER_USERNAME` | `opencode` | имя пользователя Basic-авторизации сервера OpenCode (его же использует `pcbk-health`) |

**Устанавливается точкой входа из секрета (`student/pcbk-entrypoint`):**

| Переменная | Источник | Что делает |
|---|---|---|
| `OPENCODE_SERVER_PASSWORD` | первая строка `/run/secrets/opencode-pw` | пароль Basic-авторизации сервера OpenCode; файла нет или он пуст — точка входа печатает ошибку в stderr и завершается кодом `78`, контейнер не поднимается |

**Необязательный оверрайд модели места (`compose.override.yaml`, читает сам бинарник OpenCode, не код
репозитория):**

| Переменная | Умолчание | Что делает |
|---|---|---|
| `OPENCODE_CONFIG_CONTENT` | не задана | JSON-строка, которую OpenCode читает последней — после файла `/etc/pcbk-opencode/opencode/opencode.json` и прочих источников — и слитно применяет поверх него. В конфиге места (`student/config/opencode.json`, раздел 4.1) модель по умолчанию и модель агента `konstruktor` — те же ключи (`model`, `agent.<имя>.model`), так что одной этой переменной можно переопределить модель места или модель отдельного агента без пересборки образа |

### 4.1 Конфиг места (`student/config/opencode.json`)

Файл встроен в образ (`COPY config/` в `student/Dockerfile`), не читается из `.env` стенда. Ключевые поля:

| Поле | Значение в репозитории | Что делает |
|---|---|---|
| `autoupdate` | `false` | то же, что `OPENCODE_DISABLE_AUTOUPDATE` — явный дубль в конфиге |
| `share` | `"disabled"` | запрет «поделиться разговором» |
| `snapshot` | `false` | снимки состояния репозитория выключены |
| `enabled_providers` | `["pcbk", "pcbk-or"]` | только два провайдера модели — оба указывают на LLM-прокси `core`, прямого выхода в интернет у места нет |
| `model` | `"pcbk/z-ai/glm-5.3"` | модель по умолчанию (переопределяется `OPENCODE_CONFIG_CONTENT` выше) |
| `default_agent` | `"pomoshnik"` | агент, открытый студенту при старте разговора |
| `agent.build/plan/general/explore.disable` | `true` | встроенные агенты OpenCode выключены — доступны только `pomoshnik` и `konstruktor` (см. [14-workplaces-and-agents.md](14-workplaces-and-agents.md)) |
| `permission.*` | `"deny"` по умолчанию | политика прав по умолчанию — запрет; `external_directory`, `task`, `webfetch` — явно запрещены |
| `permission.read."*.env"`/`"*.env.*"` | `"deny"` | агенту запрещено читать файлы `.env` в рабочем каталоге места |
| `permission.pcbk_save_agent` | `"deny"` | инструмент сохранения агента студента требует отдельного подтверждения (не входит в общий `read: allow`) |
| `provider.pcbk.options.baseURL` | `http://core:8000/llm/v1` | адрес LLM-прокси внутри сети места (имя `core` берётся из `extra_hosts`, см. §8) |
| `provider.*.options.apiKey` | `{file:/run/secrets/llm-token}` | токен LLM-прокси места — секрет `student-NN.llm-token` |
| `provider.pcbk.models.*` | таблица моделей-кандидатов с `limit.context`/`limit.output` | модели, доступные месту через провайдер `pcbk` (`@ai-sdk/openai-compatible`) |
| `provider.pcbk-or.models.*` | подмножество моделей | те же модели через провайдер `pcbk-or` (`@openrouter/ai-sdk-provider`) — нужен некоторым моделям, возвращающим `reasoning_details` |
| `mcp.pcbk.url` | `http://core:8000/mcp` | MCP служба данных (инструменты историана, расчётов, уроков) |
| `mcp.kb.url` | `http://core:8000/mcp-kb` | MCP поправок базы знаний (роль `kb`) |
| `mcp.*.headers.Authorization` | `Bearer {file:/run/secrets/data-token}` | токен службы данных места — секрет `student-NN.data-token` |

## 5. Входной прокси (`edge`)

У `edge/pcbk.conf.template` **нет собственных переменных окружения** — шаблон не использует `envsubst` для
подстановки имён из окружения (сам файл это явно оговаривает в комментарии наверху). Всё поведение — в
server-блоках (порты 8443 снаружи, 8080 только внутри для сторожа), картах nginx (`map`) и зонах
`limit_req_zone`/`limit_conn_zone`, зашитых в шаблон буквально. Настраиваемая часть `edge` в `compose.yaml` —
не переменные среды контейнера, а пути томов (`TLS_DIR` — bind-монт сертификата) и ресурсы контейнера
(`mem_limit`, `pids_limit`). Подробности маршрутов — [17-web-chat-and-edge.md](17-web-chat-and-edge.md).

## 6. Файлы секретов

Все пароли, ключи и токены — только файлами (секции `secrets:` Compose и bind-монты файлов), ни один не
передаётся переменной окружения. Хост хранит их в каталоге `SECRETS_DIR` (по умолчанию
`/opt/pcbk-reserve/secrets`, права каталога `0700`, файлы `0444`, кроме отмеченных `0400`/`0600`).

| Путь в контейнере | Контейнер | Источник на хосте (`compose.yaml secrets:`) | Формат | Кто читает |
|---|---|---|---|---|
| `/run/secrets/bdrv.env` | `core` | `${SECRETS_DIR}/bdrv.env` | строки `KEY=VALUE` (`export` и кавычки по краям снимаются); обязательные ключи `BDRV_HOST`, `BDRV_USER`, `BDRV_PW`, необязательные `BDRV_PORT`, `BDRV_DB` | `core/pcbk_core/secrets.py::BdrvConfig.from_env_file` |
| `/run/secrets/core-tokens` | `core` | `${SECRETS_DIR}/core-tokens` | строки `<id> <sha256-hex>`, `id` — `[a-z0-9-]{1,32}` | `secrets.py::TokenTable.from_file`, роль «данные» и MCP |
| `/run/secrets/llm-tokens` | `core` | `${SECRETS_DIR}/llm-tokens` | тот же формат, что `core-tokens`, отдельная таблица | `secrets.py::TokenTable.from_file`, роль `llm` |
| `/run/secrets/openrouter.key` | `core` | `${SECRETS_DIR}/openrouter.key` | ключ OpenRouter, обрезаются пробелы/переводы строк/BOM; пустой файл — «ключа нет»; не более 4096 байт | `secrets.py::read_key_file`, роль `llm` (основной ключ) |
| `/run/secrets/openrouter-mgmt.key` | `core` | `${SECRETS_DIR}/openrouter-mgmt.key` | тот же формат | `secrets.py::read_key_file`, роль `llm` (ключ управления, опрос `/credits`) |
| `/run/secrets/student-NN.pw` | `core` (цель `student-NN.pw`) | `${SECRETS_DIR}/student-NN.pw` | 48 шестнадцатеричных знаков, без перевода строки | `gateway/places.py` (`GW_PLACE_PW_DIR`) — шлюз ставит Basic-авторизацию к месту |
| `/run/secrets/opencode-pw` | `student-NN` (цель `opencode-pw`, источник — тот же файл `student-NN.pw`) | `${SECRETS_DIR}/student-NN.pw` | тот же файл | `student/pcbk-entrypoint`, `student/pcbk-health` |
| `/run/secrets/llm-token` | `student-NN` (цель `llm-token`) | `${SECRETS_DIR}/student-NN.llm-token` | 48 шестнадцатеричных знаков | OpenCode места, поле `apiKey` провайдеров `pcbk`/`pcbk-or` |
| `/run/secrets/data-token` | `student-NN` (цель `data-token`) | `${SECRETS_DIR}/student-NN.data-token` | 48 шестнадцатеричных знаков | OpenCode места, заголовок `Authorization` MCP `pcbk`/`kb` |

Хеш-таблицы `core-tokens` и `llm-tokens` собираются на хосте из отдельных файлов токенов (`sha256sum`) и
монтируются `core` только на чтение; сами токены на сервере не хранятся централизованно — каждый токен живёт
только в своём файле `student-NN.*-token` и в месте, которому он выдан. Процедура сборки, ротации и выдачи
токенов новому студенту — `deploy/README.md` (здесь не повторяется, это процедуры эксплуатации, не контракт
кода).

## 7. Службы `compose.yaml`

Имя проекта Compose закреплено (`name: pcbk-reserve`) — не зависит от каталога запуска, поэтому том журнала и
метки те же при любом `cwd`. Наружу публикуется только порт `8443` контейнера `edge`; `docker.sock` хоста
доступен только контейнерам `sp-ro`/`sp-ctl`; выход в сеть за пределы стенда (историан, OpenRouter) — только у
`core` через сеть `pcbk-egress`. Явного `depends_on` в файле нет — порядок запуска описан в
[02-build-and-deploy.md](02-build-and-deploy.md).

| Служба | Образ | Профиль | Ключевые тома | Сети | Secrets | Healthcheck |
|---|---|---|---|---|---|---|
| `edge` | `nginx:1.30.5-alpine` (сторонний, без сборки) | — (всегда) | шаблон `pcbk.conf.template`, статика `edge/static` (оба `ro`), `TLS_DIR` (`ro`), том `pcbk-edge-temp` (временные файлы тел/ответов) | `pcbk-public`, `pcbk-front` | нет | задан в `compose.yaml`: `wget` на `http://127.0.0.1:8080/healthz` |
| `watchdog` | собственная сборка `./watchdog` | — (всегда) | bind сертификата `cert.pem` (`ro`), том `pcbk-watchdog-journal` | `pcbk-front`, `pcbk-ro` | нет | встроен в образ (`Dockerfile`, `HEALTHCHECK`) |
| `sp-ro` | `wollomatic/socket-proxy:1.13.1` (сторонний) | — (всегда) | bind `/var/run/docker.sock` (`ro`) | `pcbk-ro` | нет | нет своего `HEALTHCHECK`; живость проверяет сторож (`docker_ping` в `watchdog/components.json`, см. [18-watchdog-and-ops.md](18-watchdog-and-ops.md)) |
| `sp-ctl` | `wollomatic/socket-proxy:1.13.1` | — (всегда) | bind `/var/run/docker.sock` (`ro`) | `pcbk-ctl` | нет | нет своего `HEALTHCHECK`; сторож проверяет его как контейнер (`inspect` через `sp-ro`, kind `container` в `watchdog/components.json`) |
| `core` | собственная сборка `./core` | — (всегда) | `whitelist.txt` (`ro`), `AGENTS_DIR` (rw, для `save_agent`), `LESSONS_DIR` (`ro`), `ANALYTICS_CORPUS_DIR` или пустой том (`ro`), том `pcbk-core-data` | `pcbk-front`, `pcbk-egress` (адрес `.82`), `pcbk-ctl`, одна сеть на каждое место (`.2`) | `bdrv-env`, `core-tokens`, `llm-tokens`, `openrouter-key`, `openrouter-mgmt-key`, 11× `student-NN-pw` | встроен в образ |
| `student-01`…`student-11` | собственная сборка `./student`, общий шаблон `x-student` | `students` | том состояния OpenCode, рабочий том `/work`, `KDM1_CHAT_KNOWLEDGE_DIR` или пустой том (`ro`), каталог агентов `AGENTS_DIR/student-NN` (`ro`) | своя сеть `pcbk-stu-NN` (адрес `.3`), в `extra_hosts` — `core` на `.2` | `student-NN-pw`→`opencode-pw`, `student-NN-llm`→`llm-token`, `student-NN-data`→`data-token` | встроен в образ (проверка `GET /agent` и слово `mcp=connected`) |

Общий шаблон `x-student` (якорь `&student`) задаёт для всех одиннадцати мест одинаковую изоляцию:
`runtime: runsc` (gVisor), `user: "10001:10001"`, `read_only: true`, `cap_drop: [ALL]`,
`security_opt: ["no-new-privileges:true"]`, `tmpfs: ["/tmp:exec,mode=1777,size=256m"]`, `mem_limit: 1g`,
`memswap_limit: 1g` (swap фактически выключен), `cpus: 1.0`, `pids_limit: 512`, `ipc: private`. Блоки
`student-02`…`student-11` — копии `student-01` с точностью до номера (адреса сети, имя тома, подкаталог
агентов, набор секретов).

## 8. Сети

Все сети, кроме `pcbk-public`, — изолированные мосты (`internal: true`, `gateway_mode_ipv4: isolated`), то
есть без выхода к хосту и друг к другу напрямую.

| Сеть | Подсеть | Участники | Назначение |
|---|---|---|---|
| `pcbk-public` | `172.31.250.16/28` | `edge` | единственная сеть с публикацией порта (`8443:8443`); маскарад выключен |
| `pcbk-front` | `172.31.250.32/28` | `edge`, `watchdog`, `core` | `edge` ходит к `core` и `watchdog`; изнутри хоста не виден (без адреса хоста на мосту) |
| `pcbk-ro` | `172.31.250.48/28` | `watchdog`, `sp-ro` | сторож читает состояние контейнеров через `sp-ro` |
| `pcbk-ctl` | `172.31.250.64/28` | `core`, `sp-ctl` | шлюз (внутри `core`) управляет местами через `sp-ctl` |
| `pcbk-egress` | `172.31.250.80/28`, динамические адреса `172.31.250.88/29` | `core` (адрес `.82`, фиксированный) | выход `core` к историану БДРВ и OpenRouter; маскарад включён; имя моста на хосте постоянное (`pcbk-egress`) — задел под будущий сетевой экран |
| `pcbk-stu-01` … `pcbk-stu-11` | `{STU_NET}.N.0/28`, динамические адреса `{STU_NET}.N.8/29` | `core` (адрес `.2`), `student-NN` (адрес `.3`) | отдельная изолированная сеть на каждое место; место видит только `core` (по имени через `extra_hosts`, DNS Docker под gVisor не работает) |

Направление трафика внутри `pcbk-egress` ограничено не сетью (на уровне сети открыт любой адрес, включая
хост через шлюз моста), а «охраной выхода» в процессе `core` — она разрешает только обращения к историану,
к хосту `OPENROUTER_BASE_URL` и к `SP_CTL_URL`, остальное закрыто программно (подробности —
[20-security.md](20-security.md)). Пересылка пакетов у `core` выключена (`sysctls: net.ipv4.ip_forward: "0"`,
`net.ipv4.tcp_fastopen: "0"`) — контейнер `core` состоит сразу в четырнадцати сетях (`pcbk-front`,
`pcbk-egress`, `pcbk-ctl` и по одной сети на каждое из 11 мест) и без этой настройки мог бы стать мостом
между местами и выходом.

## 9. Прокси сокета Docker (`sp-ro`, `sp-ctl`)

Оба — тот же образ `wollomatic/socket-proxy:1.13.1`, но с разными флагами `command:` и в разных сетях; списка
контейнеров ни один не отдаёт (`GET /containers/json` раскрыл бы `Command` всех контейнеров сервера, включая
соседние службы).

| Прокси | Кто обращается (`-allowfrom`) | Разрешённые GET | Разрешённые POST | Для чего |
|---|---|---|---|---|
| `sp-ro` | `pcbk-watchdog` | `/_ping`, `/containers/pcbk-(student-01…11\|sp-ctl\|core)/json` | нет | сторож проверяет живость и состояние мест, `sp-ctl` и `core` (только чтение) |
| `sp-ctl` | `pcbk-core` | `/_ping`, `/containers/pcbk-student-(01…11)/json` | `/containers/pcbk-student-(01…11)/(start\|stop)` | шлюз (внутри `core`) будит и останавливает места при входе/простое |

Пользователь обоих контейнеров — `65534:${DOCKER_GID}` (пользователь образа + группа `docker` хоста, узнаётся
`getent group docker | cut -d: -f3`); `docker.sock` смонтирован `read_only: true` — это защищает только от
записи в файл самого сокета, реальную границу прав держит именно фильтр `-allowfrom`/`-allowGET`/`-allowPOST`.

## 10. `compose.override.yaml` — модели мест по ролям

`docker compose` подхватывает `compose.override.yaml` рядом с `compose.yaml` сам, без указания `-f`. На
сервере в нём держат переопределение модели по месту (и, отдельно, модели конкретного агента внутри места) —
единственный ключ, который сюда пишут, это `OPENCODE_CONFIG_CONTENT` блока `environment` нужного сервиса
`student-NN` (см. §4). Пример с вымышленными именами моделей (настоящие имена — таблица `CANDIDATES` в
`core/pcbk_core/llm/models.py`, документ [12-llm-proxy.md](12-llm-proxy.md)):

```yaml
services:
  student-01:
    environment:
      OPENCODE_CONFIG_CONTENT: '{"model": "pcbk/vendor-x/model-fast", "agent": {"konstruktor": {"model": "pcbk/vendor-x/model-strong"}}}'
  student-02:
    environment:
      OPENCODE_CONFIG_CONTENT: '{"model": "pcbk/vendor-x/model-fast"}'
```

Значение `model` — строка `<провайдер>/<имя модели>`, провайдер — один из `pcbk`/`pcbk-or` конфига места
(§4.1), имя модели должно присутствовать в `LLM_MODELS` переменной среды `core` (иначе прокси откажет вызову
по модели, которой не разрешал `select_models`). Применение правки — пересоздание изменённых мест
(`docker compose --profile students create --force-recreate --no-build student-NN …`), а не их простой
перезапуск: OpenCode читает `OPENCODE_CONFIG_CONTENT` при старте процесса.

## 11. `compose.test.yaml` — тестовый стенд

Файл накладывается поверх `compose.yaml` только в локальных интеграционных тестах (`tests/integration`),
под отдельным именем проекта Compose (`-p pcbk-test`), и не едет на сервер. Ключевые отличия, все — только
тестовые пути и переменные:

- `edge` публикуется на `127.0.0.1:18443` (не `8443`), статика — из временного каталога фикстуры
  (`PCBK_TEST_STATIC`);
- у всех мест `runtime: runc` (на машине разработчика нет gVisor) через якорь `&runc`;
- `core` запускается командой `python -m twin_core`: при непустой `PCBK_TWIN_HISTORIAN` — двойник историана
  вместо настоящего запроса БДРВ; `OPENROUTER_BASE_URL` указывает на контейнер-двойник `fake-openrouter`
  (его код — `core/tests/fake_openrouter.py`, только stdlib); `LLM_RUN_MEMINFO`/`VMSTAT`/`PRESSURE` —
  файлы-двойники каталога `PCBK_TEST_MEM`, которые фикстура подменяет через `os.replace` для проверки тормоза
  прогона и памяти без реальной нагрузки на машину разработчика;
- `watchdog` получает те же двойники памяти своими путями (`MEMINFO_PATH`/`PRESSURE_PATH` — файлы `…-watchdog`
  каталога `PCBK_TEST_MEM`; `VMSTAT_PATH` по умолчанию — тоже файл-двойник `…-watchdog` того же каталога,
  настоящий `/proc/vmstat` подставляется явным оверрайдом только в отдельном учении переполнения памяти места,
  пересоздающем `watchdog` с `VMSTAT_PATH=/proc/vmstat`);
- добавлены служебные контейнеры `fake-openrouter` и `foreign` (мишень для проверки, что прокси сокета
  отказывает чужому контейнеру).

Тестовый `core` и образ `student` эти пути и команды не знают в бою — `compose.yaml`/`student/Dockerfile` их
не содержат (это специально проверяется тестами, например `test_prod_compose_has_no_twin`). Подробнее о
тестовом стенде — [19-tests-and-bench.md](19-tests-and-bench.md).

## 12. Проверки при старте и ошибки настройки

| Что проверяется | Когда | Результат ошибки |
|---|---|---|
| `Settings.from_env()` (общий слой `core`) | в `main()`, до вызова `serve()` и создания ролей | `ValueError` перехватывается самим `main()`: в stderr уходит строка `pcbk-core: настройки: …`, `main()` возвращает код 1 (процесс завершается, роли не создаются и страница состояния не поднимается — но это управляемый выход, а не необработанное исключение); единственный способ попасть сюда с опечаткой — поломать `CORE_PORT`, `FRESH_POLL_S`, `CATALOG_DEADLINE_S`, `DRILL_FRESHNESS`/`DRILL_DATA` |
| `LlmSettings.from_env()` | `build_llm_role()` при сборке ролей | ValueError перехватывается: роль `llm` поднимается с `LlmSettings.files_from_env()` (умолчания + свои файлы ключей/токенов) и `config_error` `"настройки: …"`; процесс и прочие роли живы |
| `GatewaySettings.from_env()` / `GatewaySettings._parse()` | `build_gateway_role()`, а также отдельно — доли LLM (`DRILL_TEACHER`) в `build_roles()` | роль `gateway` поднимается с `GatewaySettings.valid_from_env()` и `config_error` `"настройки шлюза: …"`; опечатка именно в `DRILL_TEACHER` дополнительно закрывает доли LLM-прокси (`"учения DRILL_TEACHER с ошибкой — доли закрыты"`), чтобы молчаливая опечатка не работала как пустое значение |
| `AnalyticsSettings.from_env()` | `analytics_settings_from_env()` в `build_roles()` | роль аналитики создаётся из `AnalyticsSettings.valid_from_env()`; при ошибке `ANALYTICS_ENABLED` отдельно гарантированно остаётся `False`, если сам он читается верно |
| Учётные данные историана (`BDRV_ENV_FILE`) | `build_roles()`, функция `read_bdrv` | не падение: роль «данные» получает причину (`CONFIG_ERRORS["bdrv_unreadable"]`/`"bdrv_invalid"`), историан недоступен, остальное работает |
| Белый список тегов (`WHITELIST_PATH`) | `build_roles()` | файла нет — роль «данные» с пустым списком (ворота не пускают ни одного имени); прочая ошибка чтения — причина `whitelist_unreadable` |
| Ключ OpenRouter (`OPENROUTER_KEY_FILE`) | `build_llm_role()` | нечитаемый файл или ключ не из печатных ASCII-знаков — `config_error` роли `llm`, прокси отказывает всем вызовам |
| Ключ управления (`OPENROUTER_MGMT_KEY_FILE`) | `build_llm_role()` | не `config_error` — только отключается опрос остатка счёта, прокси работает |
| Таблицы токенов (`core-tokens`, `llm-tokens`) | `build_roles()`/`build_llm_role()` | не `config_error` — пустая таблица токенов (всем вызовам 401), отдельная причина `tokens_error` на странице |
| `watchdog.Settings.from_env()` + `__post_init__` | при старте процесса сторожа | исключение не перехватывается — контейнер `watchdog` не поднимается; сюда же относится производное правило `tick_budget_s > 0` (слишком малый `STALE_AFTER_S` относительно `TICK_S`/`DOCKER_TIMEOUT_S`) |
| `load_components(COMPONENTS_PATH)` | при старте сторожа | неизвестный вид проверки, отсутствующие поля, повтор `id`, чужое имя контейнера — исключение, процесс не поднимается (файл встроен в образ, редактируется только пересборкой) |

Во всех случаях, где ошибка не роняет процесс, причина видна студенту/преподавателю только как неработающая
функция (например, красная строка на `/status` или 503 у конкретной ручки), а текст причины — в журнале
контейнера (`docker compose logs core`) и на самой странице состояния — без значения переменной, только имя
и правило.

## 13. Что переносимо, что специфично для стенда ПЦБК

**Переносимо как есть** (общий механизм настроек, не привязан к участку/предприятию):
- вся модель `from_env`/`valid_from_env`/`files_from_env` и разбор переменных — у всех четырёх классов
  `core` и у сторожа;
- структура секретов (файлы, не переменные), формат `core-tokens`/`llm-tokens`, формат `bdrv.env`;
- сети, прокси сокета Docker, шаблон `x-student`, конфиг места `opencode.json` (кроме имён моделей и
  `baseURL`, которые и так указывают внутрь стенда, а не на заказчика);
- `compose.override.yaml` как механизм переопределения модели места/агента.

**Специфично для участка КДМ-1 / сервера ПЦБК и что заменить при переносе:**
- `BDRV_ENV_FILE`/формат `bdrv.env` — рассчитан на историан БДРВ (Dify-совместимый формат учётных данных);
  под другой историан нужен новый класс конфигурации и новая функция запроса вместо `tds_query`
  (см. [21-extending.md](21-extending.md));
- `WHITELIST_PATH`/`DATA_DIR` — белый список тегов конкретного участка; при переносе на другое предприятие —
  свой список тегов этого предприятия, формат файла не меняется;
- `ANALYTICS_CORPUS_DIR`, `ANALYTICS_LLM_EXPORT_POLICY=approved_subset`, `ANALYTICS_REVIEWERS=all_students` —
  значения, завязанные на корпус и решения владельца именно этого стенда; подсистема аналитики в целом
  (§2.4) переносима, но её включение и политика передачи контента в LLM — решение каждого нового заказчика
  отдельно, умолчания кода (`ANALYTICS_ENABLED=false`, `public_only`, `grants`) безопасны «из коробки»;
  `KDM1_CHAT_KNOWLEDGE_DIR` — точно так же: подготовленные материалы участка, при переносе — свои или пусто;
- `STU_NET`, количество мест (`PLACE_MAX` в `core/pcbk_core/places.py`) — число посадочных мест стенда;
  увеличение числа мест требует правки регэкспов `sp-ro`/`sp-ctl`, добавления сетей/блоков в `compose.yaml`
  и записи в `watchdog/components.json`, а не только переменной окружения;
- модели в `LLM_MODELS`/`LLM_FALLBACK`/`compose.override.yaml` и имена в `student/config/opencode.json` —
  текущий выбор моделей для этого стенда; список кандидатов и их цены — таблица `CANDIDATES` в
  `core/pcbk_core/llm/models.py`, под другого провайдера моделей нужна правка этой таблицы.
