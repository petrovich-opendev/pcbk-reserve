# Д4а. LLM-прокси под сторожем — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** серверный слой `pcbk-core` получает вторую роль — LLM-прокси. Это
OpenAI-совместимая точка `/llm/v1/chat/completions`: токен места, точный
список моделей, тело запроса собирается заново, провайдер закреплён, предел
шагов на ход, цена каждого вызова — в журнале SQLite, доли $100 и общий
бюджет $1000 с жёстким стопом. Владелец видит на странице состояния строку
«LLM-прокси и бюджет» с «расход $X из $1000». Учения «LLM-прокси остановлен» и
«402 от OpenRouter» (заглушкой, без денег) показывают сбой снимками. Живая
проверка §11 п. 4 отвечает, приходит ли цена в последнем чанке потока и видит
ли её прокси. Рабочие места к прокси подключает Д4б.

**Architecture:** продолжение `pcbk_core` из Д3а/Д3б. Добавляются:
- роль `llm` (`LlmRole`) — на протоколе `Role`, её крючки уровня приложения
  ставятся только через `install(app)`;
- общий реестр крючков приложения (`hooks.py`): один обработчик 422 с
  разбором по префиксу пути и один `BodyLimit` с пределом по префиксу. Без него
  предел тела Д3б в 64 КиБ отказывал бы каждому запросу к модели;
- проход к OpenRouter на `httpx`. Поток идёт клиенту байт в байт, рядом его
  читает наблюдатель SSE: берёт цену из последнего чанка, иначе дозапрашивает
  `/generation` по `X-Generation-Id`;
- журнал вызовов `llm_calls` в той же SQLite, что журнал событий Д3б.

Пороги бюджета держит сторож (как пороги свежести историана в Д3а). Текст
строки в «внимании» и «сбое» постоянный, а число расхода уходит в новое поле
`note` — его журнал сторожа не сравнивает.

**Tech Stack:** Python 3.12; FastAPI, uvicorn, `httpx` (уже в lock через
`mcp==1.30.0`, закрепляется явной строкой), SQLite; сторож — stdlib; JS без
библиотек; двойник OpenRouter — stdlib на `python:3.12-slim`; Docker Compose;
`curlimages/curl:8.16.0` для живых проверок; google-chrome для снимков.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(§1 п. 3 — LLM-прокси как роль; §3 п. 5; §5; §8 — 402 и пороги 50/80/95 %; §9
«Успех 6»; §10; §13 п. 6); факты —
[`research/04-d1-facts.md`](../research/04-d1-facts.md) §4 (цена в последнем
чанке, `X-Generation-Id`, `/generation`, признаки «кончились деньги», белый
список тела, потолок `max_tokens`),
[`research/08-model-candidates.md`](../research/08-model-candidates.md) §6, §7,
§9 и раздел «Что это значит для плана Д8» (семь кандидатов, закреплённые
точки, `zdr`, уровни рассуждения),
[`research/09-constructor-agent.md`](../research/09-constructor-agent.md) §2.4 и
§9 п. 6 (жёсткий предел шагов в прокси по `X-Session-Id`, 16 обращений на ход);
[`research/07-gateway-whitelist.md`](../research/07-gateway-whitelist.md) —
только чтобы не делать работу шлюза Д5 (поля `prompt_async`, `?directory=`,
пароли мест здесь не трогаются). Имена, Global Constraints и решения
Д3а-R1…R5, Д3б-R6…R10 —
[`PLAN-D3a-data-service-2026-10-01.md`](PLAN-D3a-data-service-2026-10-01.md) и
[`PLAN-D3b-tag-answers-2026-10-02.md`](PLAN-D3b-tag-answers-2026-10-02.md). Их
задача 9, шаг 1 («Предпосылки Д4») — обязательный список для этого плана.
Решения этого плана нумеруются дальше: Д4а-R11…R17. Места, секреты,
помощники `oc` и `probe` —
[`PLAN-D2-workplaces-2026-09-30.md`](PLAN-D2-workplaces-2026-09-30.md). Формат
плана — [`PLAN-D1-foundation-2026-09-29.md`](PLAN-D1-foundation-2026-09-29.md);
дорожная карта — [`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md),
строка Д4. Вторая половина дня Д4 —
[`PLAN-D4b-workplaces-llm-2026-10-04.md`](PLAN-D4b-workplaces-llm-2026-10-04.md).

**Разрез Д4 на два дня.** Строка Д4 дорожной карты по плановой шкале стоит
около 21 ч. В неё входят:
- сам прокси;
- предпосылки Д4 из закрытия Д3б: общий обработчик 422, живость
  `/healthz/live`, охрана выхода к OpenRouter, нагрузка историана на
  странице, переподключение MCP мест, строка контейнера `core`;
- подключение десяти мест;
- проверка совместимости семи моделей.

Как и Д3, строка делится надвое:
- **Д4а** (этот план) — прокси под сторожем на сервере, без мест;
- **Д4б** — места подключены к прокси и службе, разговор через OpenCode,
  таблица совместимости.

В дорожной карте становится 14 дней, нумерация Д5–Д12 прежняя. У каждой
половины свой видимый результат и своё окно владельца.

**Предпосылка.** Д3б влит в `main` с тегом `platform-d3b` — или его хвосты
закрываются в задаче 0. Ветка дня — `d4a/llm-proxy` от `main` после тега.

**Влезает ли в день — оценка по часам.** Задачи идут последовательно. В часы
каждой задачи с кодом входят 15 минут на ревью и правки. Шкала — плановая, по
ставкам Д1–Д3, без сжатия.

| Задача | Часы | Где |
|---|---|---|
| 0. Хвосты Д3б; утро: сверка имён с кодом Д3, связь сервера с OpenRouter без ключа | 0,25–0,75 | локально, сервер |
| 1. Живая проверка §11 п. 4 — сырой поток с ключом владельца | 0,5 | сервер, **окно владельца** (после задачи 8) |
| 2. Общие крючки приложения и живость | 0,75 | локально |
| 3. Политика запроса: модели, тело, шаги | 1,0 | локально |
| 4. Журнал вызовов и доли | 0,75 | локально |
| 5. Проход к OpenRouter: двойник, клиент, поток, признаки «кончились деньги» | 1,5 | локально |
| 6. Роль `llm`: ручка, отказы, опрос, ключи файлами, учения | 1,5 | локально |
| 7. Сторож: вид `llm` и число в строке | 0,75 | локально |
| 8. Компоновка и интеграционные тесты | 1,5 | локально |
| 9. Выкладка (шаг 1 — окно владельца) | 0,75 | сервер |
| 10. Живые проверки и учения | 1,0 | сервер |
| 11. Закрытие дня | 1,25 | — |
| **Критический путь по плановой шкале** | **11,5** при хвостах 0,25 ч | |

**Порядок исполнения и окно владельца.** Сначала задача 0, затем код и
компоновка: 2 → 3 → 4 → 5 → 6 → 7 → 8. **Одно окно владельца около 0,75 ч
вечером**, сразу после задачи 8:
1. владелец сам кладёт ключ OpenRouter (и, если хочет видеть остаток счёта,
   ключ управления) в файлы на сервере — задача 9, шаг 1;
2. задача 1 — сырой поток с этим ключом: первый ответ на §11 п. 4 до выкладки
   нашего кода;
3. задача 9, шаги 2–5 — выкладка;
4. задача 10, шаги 1–3 — цена в потоке уже через наш прокси.

Учения (задача 10, шаги 4–6) денег не тратят и присутствия владельца не
требуют. Код не зависит от итога сырой проверки: цена из потока, из тела и из
`/generation` поддержаны все три.

**Принятое основание «один день».** Живой темп по git:
- Д1: план в 9,5 ч выполнен примерно за 2 ч 20 мин — от одобрения плана
  `f4fb938` (14:23) до «Д1 закрыт» `ae0ad6c` (16:42);
- Д2: план в 10,25 ч — примерно за 1 ч 25 мин, от утренней проверки `968ad64`
  (17:15) до «Д2 закрыт» `0bb5fc1` (18:39).

Плановая шкала остаётся честной: Д4а — 11,5 ч, Д4б — 9,75 ч, в сумме больше
одного дня, поэтому разрез. Каждая половина укладывается в рабочий день по
живому темпу. По живому темпу обе половины прошли бы и за один календарный
день, но их делят окна владельца и правило «день = видимый результат». Задача
11 пишет строку «план / факт по git». Если темп Д3б окажется вдвое медленнее
Д1 или хуже, план пересчитывается до начала дня, и владелец получает строку с
числами.

**Черта отсечения — конец восьмого часа** плюс превышение хвостов Д3б над
0,25 ч. К черте зелёны задачи 0 и 2–8 (по оценке ровно 8,0 ч). После черты
порядок жёсткий: окно владельца (задача 9, шаг 1 → задача 1 → задача 9, шаги
2–5 → задача 10, шаги 1–3), затем учения и задача 11.
- **Задача 8 зелёна к черте.** Всё по порядку выше. Если закрытие не
  помещается в день, оно сжимается: один раунд критика; при нуле блокеров —
  слияние и тег. Иначе второй раунд, слияние и тег переходят в утренний слот
  Д4б (строка в его шапке).
- **Задача 8 не зелёна к черте.** Выкладки нет. Видимый результат — снимки
  локального стенда с двойником OpenRouter (пометка «не на сервере»): строка
  «расход $0.0012 из $1000», оба учения. Окно владельца переносится на утро
  Д4б, Д4б сдвигается на половину дня. Владельцу в тот же час уходит строка —
  решение о сдвиге за ним.
- **Ключа сегодня нет** (окна владельца нет). Задача 1 не делается. Выкладка
  идёт с пустым файлом ключа, строка честно красная: «нет ключа OpenRouter».
  Учение «LLM-прокси остановлен» — живьём. Учение «402» — только локально:
  без ключа прокси отказывает раньше, чем доходит до заглушки. §11 п. 4
  переходит в окно Д4б.
- **Сырой поток дал 403 «not available in your region» или 401.** Денег больше
  не тратить. Выкладка идёт, строка красная с этой причиной. Владельцу тем же
  часом уходит вопрос: путь к OpenRouter с сервера закрыт, а резерва провайдера
  постановка не предусматривает. Д4б без его ответа не начинается.
- **Цены в потоке нет.** Код не меняется: работает запасной путь через
  `/generation`. §11 п. 4 получает ответ «нет, только дозапросом», это
  записывается в §5 проекта.

## Global Constraints

Действуют Global Constraints Д1, Д2, Д3а и Д3б целиком: закреплённые образы
через `deploy/images.lock`; секреты и данные заказчика никогда не попадают в
git и в вывод проверок; секреты контейнерам — только файлами; `docker.sock` —
только у `sp-ro`/`sp-ctl`; сети с закреплёнными подсетями; наружу — только
8443 `edge`; людям — по-русски, в коде — английские имена; Dify не трогаем;
свои скрипты — в `$JOB` (каталог `pcbk-d4` внутри `$CLAUDE_JOB_DIR/tmp`);
`CORE_PYTEST`, охрана выхода, крючки только через `Role.install(app)`. Д4а
добавляет:

- **Ключи OpenRouter — только файлами** `${SECRETS_DIR}/openrouter.key` и
  `${SECRETS_DIR}/openrouter-mgmt.key` (`0444` в каталоге `0700`, Д2-решение 5).
  Кладёт их владелец. Пустой файл ключа управления означает «нет ключа». Сессия
  ключ не читает: ни `cat` на экран, ни в `$JOB`; для проверок — только
  `stat`, `grep -c`, `grep -c -F -f <файл ключа>`. Ключ не появляется ни в
  окружении контейнера, ни в журнале процесса, ни в журнале вызовов, ни в
  `docs/checks`. Файл шаблонов проверки на секреты дополняется строкой
  `sk-or-[A-Za-z0-9-]{16,}`. Положительный контроль:
  `printf 'sk-or-v1-%s\n' 0123456789abcdef0123 | grep -c -E -f <шаблоны>` → `1`.
- **Токены LLM** — файлы `student-NN.llm-token` из Д2 и новый `ops.llm-token`
  (48 шестнадцатеричных знаков, `0400`, только на сервере). Хеши sha256 лежат в
  `${SECRETS_DIR}/llm-tokens` (формат `TokenTable` Д3б: `<id> <sha256>`, файл
  `0444`). Это отдельная таблица: токен данных к `/llm` не подходит, токен LLM к
  `/api/data` и `/mcp` — тоже. Тестовые токены — не 48 hex
  (`"test-llm-token-student-01"`).
- **Деньги.** У каждой живой проверки с моделью записан потолок: весь Д4а
  тратит не больше $0,05 (сырой поток и 3–4 вызова через прокси; модель
  `openai/gpt-6-luna`, `max_tokens` ≤ 400). Учения до OpenRouter не доходят:
  `DRILL_LLM=402` подменяет ответ внутри процесса. Все вызовы идут на счёт
  `ops`, доли студентов не трогаются.
- **Содержимого разговоров нет нигде, кроме потока клиенту.** В журнале
  процесса, журнале вызовов, журнале сторожа и `docs/checks` — только числа,
  коды, имена моделей и провайдеров, `ses_…` (id сессии OpenCode).
- **Запрос к OpenRouter собирает прокси.** Тело строится заново из белого
  списка, заголовки — свои (`Authorization`, `Content-Type`, `Accept`), от
  клиента не пересылается ничего.
- **Отказы прокси** — HTTP-ошибки с телом OpenAI вида
  `{"error": {"message": <русский текст>, "type": "pcbk", "code": "pcbk_…"}}`.
  Повторяемые коды (429, 502) — только для временного; отказы по бюджету,
  модели, шагам и выключателю — 400/402/403. В `code` нет слов `exhausted`,
  `unavailable`, `overloaded`, `rate`: по ним OpenCode может счесть ошибку
  повторяемой. Как OpenCode показывает отказ в чате, проверяет интеграционный
  тест Д4б.
- **Строки сторожа:** в `warn`/`fail` текст `detail` постоянный, меняющиеся
  числа — только в `note` (правило журнала Д1: переход — смена `state` или
  смена `detail` в `warn`/`fail`).
- **Охрана выхода** (Д3б-R6) пускает историан и хост OpenRouter из
  `OPENROUTER_BASE_URL` — по имени, через кэш разрешения.

## Решения по умолчанию (Ruling)

**Д4а-R11 — пороги и число на странице.** Служба отдаёт числа
(`/health/llm`). Пороги 50/80/95 % и стоп на 100 % считает сторож. «Внимание»
на 50, 80 и 95 %, «сбой» на 100 %. Текст полосы постоянный («израсходовано
больше 80 % бюджета»), а «расход $X из $1000» — в `note`. Журнал сторожа
записывает смену полосы, но не каждый цент. Строка называется «LLM-прокси и
бюджет» — это своя строка роли, как обещал Д3а-R5: учение с прокси не
окрашивает «Службу данных». **Цена ошибки:** переход порога виден с задержкой
не больше такта сторожа.

**Д4а-R12 — как прокси отказывает.** Отказ — ошибка HTTP с русским текстом, а
не поддельный ответ ассистента: преподаватель не должен принять текст прокси за
слова модели. Коды:

| Случай | Статус | `code` |
|---|---|---|
| нет или неверный токен | 401 | `pcbk_unauthorized` |
| выключатель `LLM_STATE=stopped` | 403 | `pcbk_stopped` |
| нет ключа OpenRouter | 403 | `pcbk_key_missing` |
| тело не объект, плохие сообщения | 400 | `pcbk_body_refused` |
| модель не из списка | 403 | `pcbk_model_refused` |
| доля вызывающего израсходована | 402 | `pcbk_share_spent` |
| общий бюджет израсходован | 402 | `pcbk_budget_spent` |
| больше 16 обращений за ход | 403 | `pcbk_turn_limit` |
| OpenRouter: кончились деньги / лимит ключа | 402 | `pcbk_money_out` / `pcbk_key_limit` |
| OpenRouter: ключ отклонён / отказ по региону | 403 | `pcbk_key_rejected` / `pcbk_region` |
| OpenRouter: 429 или 402 `in_flight_budget` | 429 (+ `Retry-After`, если был) | `pcbk_upstream_wait` |
| OpenRouter: прочие 4xx | тот же статус, текст OpenRouter до 300 знаков | `pcbk_upstream_rejected` |
| OpenRouter: 5xx, 408, нет связи | 502 | `pcbk_upstream_down` |

Ошибка посреди потока (событие SSE `error` при статусе 200) идёт клиенту как
есть. Прокси её классифицирует и учитывает, но текст не переписывает.
**Цена ошибки:** если OpenCode всё же повторяет 402/403, в Д4б коды меняются
одной строкой таблицы `REFUSALS`.

**Д4а-R13 — цена вызова.** Источник по порядку: `usage.cost` из последнего чанка
потока (`stream`), затем из тела ответа без потока (`body`), затем
`GET /generation?id=` (`generation`, до пяти попыток через 1, 2, 4, 8 и 15 с
после 404). Если и так нет — `unknown`, и строка сторожа предупреждает.
Оценки «на глаз» нет. Бюджет проверяется до вызова, резерва под идущие вызовы
нет. Перерасход вызывающего поэтому ограничен ценой его одновременных
вызовов: один вызов — не больше 16 000 токенов выхода и 131 072 токенов входа
(окно из образа Д4б), у премиума это около $0,42. **Цена ошибки:** доля
студента может закончиться с перерасходом в несколько десятков центов.

**Д4а-R14 — «кончились деньги» не защёлкивается.** Каждый вызов идёт в
OpenRouter: отказ 402 бесплатный. Первый удачный вызов снимает признак, как и
положительный остаток по ключу управления. Состояние живёт в памяти. После
перезапуска `core` признак вернётся с первым же вызовом, а с ключом
управления — с первым опросом. **Цена ошибки:** до первого вызова после
перезапуска страница может показывать «норму» при пустом счёте.

**Д4а-R15 — выключатель и учения.** `LLM_STATE=stopped` — настоящий выключатель
администратора: вызовов к модели нет, опросов OpenRouter нет, строка — «сбой».
Учение «LLM-прокси остановлен» пользуется им, а не отдельной ручкой.
`DRILL_LLM=402` отвечает «кончились деньги (учения)» внутри процесса, опрос
`/key` при этом настоящий — он и доказывает, что денег не потрачено. Оба
режима включаются пересозданием `core` (`docker compose up -d`). **Цена
ошибки:** около 20 с на время пересоздания мигают и строки службы данных.

**Д4а-R16 — ключ управления необязателен.** Без него страница не показывает
остаток счёта, а «кончились деньги» видно только по ответам на вызовы. С ним
строка предупреждает при остатке меньше $20. Счёт общий с Dify-стендом — это
принятый риск постановки.

**Д4а-R17 — список моделей — константы кода, выбор — переменная.** Семь
кандидатов research/08 с закреплённой точкой и уровнем рассуждения лежат в
`llm/models.py`. `LLM_MODELS` в `.env` сужает список: пусто — все семь (на
время отбора), после Д8 — одно имя. Смена — `.env` и пересоздание `core`, без
пересборки образа. Имя сверяется точным совпадением, поэтому суффиксы
(`:nitro`, `:online`, `:floor` и другие), алиасы `~…` и роутеры `openrouter/*`
отсекаются сами.

## Review Focus

1. **Предел тела Д3б в 64 КиБ стоит на всём приложении.** Настоящий запрос
   OpenCode к модели — сотни килобайт: без предела по префиксу каждый ход
   получил бы 413. Вторая роль к тому же перетёрла бы обработчик 422 службы
   данных. Ожидание: у `/llm/` — 4 МиБ, у остального — 64 КиБ; 422 на
   `/api/data` по-прежнему даёт событие; обрыв клиента виден и за буфером
   тела. Тесты — задача 2, `test_body_limit_per_prefix`,
   `test_validation_dispatch_by_prefix_two_roles`; задача 6,
   `test_client_disconnect_closes_upstream_and_costs_later`.
2. **Поток оборвался посредине** — студент нажал «стоп», OpenRouter прислал
   событие ошибки, соединение сброшено. Деньги потрачены, а чанка с ценой нет.
   Ожидание:
   - прокси закрывает поток к OpenRouter;
   - вызов записан, цена дозапрошена по `X-Generation-Id` с повторами после
     404;
   - если цена так и не нашлась — предупреждение на странице, а не молчаливый
     ноль.

   Тесты — задача 6, `test_no_usage_falls_back_to_generation`,
   `test_client_disconnect_closes_upstream_and_costs_later`; задача 4,
   `test_open_call_after_crash_is_unknown`.
3. **OpenRouter по-разному говорит «кончились деньги»:** 402; 403 с текстом о
   лимите ключа или кредитах; 402 `in_flight_budget` с `Retry-After`; событие
   402 посреди потока. Ожидание:
   - понятный текст в чате и «сбой» строки;
   - `in_flight_budget` — это «подождите», а не «денег нет».

   Тесты — задача 5, `test_classify_table`, `test_errors_classified`; задача 6,
   `test_mid_stream_402_is_money_out`, `test_in_flight_is_wait_not_money`.
4. **Тело клиента пытается сменить маршрут или цену.** Например: `models`,
   `route`, `provider`, `plugins`, `:nitro`, `openrouter/auto`, `max_tokens`
   32 000, серверные инструменты `openrouter:*`, свои `user` и `session_id`.
   То же приходит из фронтматтера агента (research/08 §6). Ожидание:
   - тело собрано заново только из белого списка;
   - чужая модель отказана до OpenRouter.

   Тесты — задача 3, `test_rebuild_keeps_only_whitelist_and_sets_our_fields`,
   `test_model_list_exact`; задача 6, `test_model_refused_before_upstream`.
5. **Число расхода меняется на каждом вызове.** В `detail` оно залило бы журнал
   сторожа событиями, а спрятанное в «внимании» — пропало бы со страницы.
   Ожидание: полоса — постоянным текстом, число — в `note`, в журнале — только
   переходы полос. Тесты — задача 7, `test_llm_verdicts`,
   `test_note_does_not_make_transition`.

---

## Карта файлов

```
core/requirements.in                  + httpx== (та же версия, что уже в lock через mcp)
core/requirements.lock                пересобран с хешами
core/pcbk_core/hooks.py               AppHooks, hooks_of, BodyLimit: предел тела и разбор 422 по префиксу
core/pcbk_core/app.py                 + реестр крючков, /healthz/live, один обработчик 422, один BodyLimit
core/pcbk_core/main.py                + --healthcheck по /healthz/live, LlmRole, egress_targets
core/pcbk_core/settings.py            + OPENROUTER_*, LLM_*, DRILL_LLM
core/pcbk_core/secrets.py             + read_key_file
core/pcbk_core/data/__init__.py       install — через реестр крючков
core/pcbk_core/data/http_api.py       − BodyLimit (переехал в hooks.py)
core/pcbk_core/llm/__init__.py        LlmRole
core/pcbk_core/llm/models.py          ModelPolicy, CANDIDATES, select_models, provider_matches
core/pcbk_core/llm/request.py         rebuild_body, BodyRefused, session_of, last_role
core/pcbk_core/llm/turns.py           TurnLimiter, TurnLimitReached
core/pcbk_core/llm/ledger.py          Usage, CallRow, Ledger, Budget
core/pcbk_core/llm/sse.py             SseObserver
core/pcbk_core/llm/upstream.py        Upstream, UpstreamError, classify
core/pcbk_core/llm/state.py           UpstreamState, UPSTREAM_TEXTS
core/pcbk_core/llm/api.py             llm_router, LLM_MESSAGES, REFUSALS
core/tests/fake_openrouter.py         двойник OpenRouter на stdlib; он же — служба тестового стенда
core/tests/{helpers,conftest}.py      + serve_app, fake_or, make_llm_role, make_llm_app, LLM_TOKEN, SES, chat_body
core/tests/test_hooks.py, test_llm_request.py, test_llm_turns.py, test_llm_ledger.py,
           test_llm_sse.py, test_llm_upstream.py, test_llm_role.py
watchdog/pcbk_watchdog/checks.py      Check.note, check_llm, fmt_usd, fmt_total
watchdog/pcbk_watchdog/page.py        note в JSON и HTML
watchdog/pcbk_watchdog/static/status.js   note
watchdog/pcbk_watchdog/main.py        вид llm
watchdog/components.json              llm → вид llm, «LLM-прокси и бюджет»
watchdog/tests/                       + вид llm, note; fake_core — /health/llm
compose.yaml                          core :d4a, 512m, секреты ключей и llm-tokens, LLM_*; сторож :d4a
compose.test.yaml                     + fake-openrouter; core → двойник, опрос раз в 5 с
deploy/images.lock                    + python:3.12-slim # test
deploy/env.example                    + LLM_MODELS, LLM_STATE, DRILL_LLM, LLM_OPS_SHARE_USD
deploy/README.md                      + LLM-прокси: ключи, токены, выключатель, учения, откат
.gitignore                            + llm-tokens
tests/integration/conftest.py         тестовые ключи и llm-tokens, recreate, fake_requests, llm_calls
tests/integration/test_llm.py
tests/integration/test_edge.py        DECLARED_ENV, IMAGES
tests/integration/test_core.py        строка охраны выхода; сеть выхода — по prod_config
tests/integration/test_socket_proxy.py    absent больше нет
docs/checks/D4a.md, docs/checks/D4a/*.png
```

**Сети** — без изменений (таблицы Д1–Д3а). В тестовом стенде в `pcbk-egress`
на `172.31.250.84` стоит ещё двойник OpenRouter `pcbk-test-openrouter`; в
`compose.yaml` его нет.

Имена: роль `llm`, образ `pcbk-reserve/core:d4a`, сторож
`pcbk-reserve/watchdog:d4a` (`:d3b`/`:d3a` остаются на сервере для отката);
секреты Compose `openrouter-key` → `/run/secrets/openrouter.key`,
`openrouter-mgmt-key` → `/run/secrets/openrouter-mgmt.key`, `llm-tokens` →
`/run/secrets/llm-tokens`; таблица `llm_calls` в `/var/lib/pcbk-core/core.db`.

---

### Task 0: Хвосты Д3б и утро

**Files:**
- Create: `docs/checks/D4a.md`

- [ ] **Step 1: Хвосты Д3б.** Всё, что черта Д3б перенесла сюда: второй раунд
  критика, слияние и тег `platform-d3b`, чистый клон.
  Expected: у каждого хвоста есть вердикт; ветка `d4a/llm-proxy` начинается от
  `main` после тега.
- [ ] **Step 2: Сверка имён с кодом Д3.** План написан до кода Д3а/Д3б.
  `git grep -n -E 'def create_app|class RoleBase|def install|def mounts|class TokenTable|class EventLog|def install_egress_guard|class BodyLimit|DB_PATH|TOKENS_FILE|def make_app|def make_role|def http_host|core_token' -- core tests`
  Expected: все имена из разделов Interfaces этого плана на месте. Если что-то
  расходится — правка плана одним коммитом до задачи 2 («План Д4а: имена по
  коду Д3»).
- [ ] **Step 3: Связь сервера с OpenRouter без ключа (владелец не нужен).**
  На сервере одноразовый `curlimages/curl:8.16.0` в `pcbk-egress` (адрес
  динамический, из `.88/29`):
  - `GET https://openrouter.ai/api/v1/models` — код и число моделей;
  - `GET https://openrouter.ai/api/v1/endpoints/zdr` — есть ли каждая из семи
    закреплённых пар «модель — точка» в списке ZDR.

  Разбор — `python3` на сервере, в журнал идут только числа.
  Expected: `200`; «7 из 7 моделей есть в каталоге»; «7 из 7 закреплённых точек —
  ZDR». Если точка пропала из ZDR — в задаче 3 меняется её константа (запасная
  точка из research/08 §8), и это записывается.
- [ ] **Step 4: Commit** (после проверки на секреты) — `git add docs/checks/D4a.md && git commit -m "Д4а: утро — имена Д3 сверены, OpenRouter с сервера доступен, закреплённые точки ZDR"`.

---

### Task 1: Живая проверка §11 п. 4 — сырой поток (окно владельца)

Идёт в окне владельца, после того как он положил ключ (задача 9, шаг 1), и до
выкладки нашего кода. Отвечает на §11 п. 4 на стороне OpenRouter. Та же
проверка через наш прокси — задача 10.

**Files:**
- Modify: `docs/checks/D4a.md`

**Interfaces:**
- Consumes: `${SECRETS_DIR}/openrouter.key` (задача 9, шаг 1);
  `curlimages/curl:8.16.0` на сервере.
- Produces: вердикты [П] для Д4а-R13 и строки §11 п. 4 проекта.

- [ ] **Step 1: Заголовок и запрос (на сервере, без вывода ключа)**

```bash
install -d -m 0700 ~/pcbk-d4/raw && cd ~/pcbk-d4/raw && umask 077
printf 'Authorization: Bearer %s\n' "$(cat /opt/pcbk-reserve/secrets/openrouter.key)" > or.hdr
[ -s /opt/pcbk-reserve/secrets/openrouter-mgmt.key ] && \
  printf 'Authorization: Bearer %s\n' "$(cat /opt/pcbk-reserve/secrets/openrouter-mgmt.key)" > mgmt.hdr
cat > req.json <<'EOF'
{"model": "openai/gpt-6-luna", "stream": true, "max_tokens": 64,
 "messages": [{"role": "user", "content": "Ответь одним словом: да"}],
 "reasoning": {"effort": "low"},
 "provider": {"only": ["azure"], "allow_fallbacks": false, "zdr": true, "data_collection": "deny"}}
EOF
```

- [ ] **Step 2: Поток и дозапрос цены**

`docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v ~/pcbk-d4/raw:/w curlimages/curl:8.16.0 -sS -N --max-time 60 -D /w/headers -H @/w/or.hdr -H 'Content-Type: application/json' --data @/w/req.json -o /w/stream https://openrouter.ai/api/v1/chat/completions`.
Затем тем же способом `GET /api/v1/generation?id=<id>` через 5 с и через 30 с
(`-o /w/gen5`, `-o /w/gen30`) и `GET /api/v1/key` (`-o /w/key`). Разбор —
`python3` на сервере. Печатаются только:
- код ответа;
- число событий `data:`;
- есть ли `usage.cost` в последнем событии перед `[DONE]` и его значение;
- не пуст ли `choices` в этом событии;
- `provider`;
- есть ли заголовок `x-generation-id`;
- ответ `/generation`: 404 или `total_cost`, совпадает ли `total_cost` с ценой
  из потока;
- имена полей `data` у `/key` (без значений, кроме `usage` и `limit`).

Если ключ управления не пуст — ещё `GET /api/v1/credits` его заголовком:
печатается, есть ли поля `total_credits` и `total_usage`.
Expected: код 200; цена в последнем чанке есть; `choices` не пуст;
провайдер — Azure; `x-generation-id` есть; `/generation` отдаёт ту же цену
(через 5 с или только через 30 с — записать). Потрачено меньше $0,001.
Ветки «403 регион / 401» и «цены в потоке нет» — в шапке.

- [ ] **Step 3: Уборка.** `rm -rf ~/pcbk-d4/raw`. Expected: каталога нет.

- [ ] **Step 4: Commit** (после проверки на секреты) — `git add docs/checks/D4a.md && git commit -m "Д4а: §11 п. 4 — цена в последнем чанке потока и /generation, сырой поток с сервера"`.

---

### Task 2: Общие крючки приложения и живость

Предпосылки Д4 из закрытия Д3б: один обработчик 422 на приложение с разбором
по префиксу, предел тела по префиксу и `HEALTHCHECK` по живости процесса.

**Files:**
- Create: `core/pcbk_core/hooks.py`
- Modify: `core/pcbk_core/app.py`, `core/pcbk_core/main.py` (`--healthcheck`),
  `core/pcbk_core/data/__init__.py` (`DataRole.install`),
  `core/pcbk_core/data/http_api.py` (без `BodyLimit`),
  `core/tests/helpers.py` (`DummyRole(..., validates=None, body_limit=None)`, `serve_app`)
- Test: `core/tests/test_hooks.py`; `core/tests/test_http.py` Д3б остаётся
  зелёным без правок

**Interfaces:**
- Consumes: `create_app`, `Role`, `RoleBase`, `_Asgi`, `DummyRole`,
  `make_app(tmp_path)` → `(app, role)`, `TOKEN`, `wait_until` — Д3а/Д3б;
  `BodyLimit` и обработчик 422 из `DataRole.install` Д3б.
- Produces (`hooks.py`):
  - `DEFAULT_BODY_LIMIT = 65536`
  - `ValidationHandler = Callable[[Request, RequestValidationError], Awaitable[Response]]`
  - `class AppHooks`:
    - `validation(prefix: str, handler: ValidationHandler) -> None`;
    - `body_limit(prefix: str, max_bytes: int) -> None`;
    - `handler_for(path: str) -> ValidationHandler | None` и
      `limit_for(path: str) -> int` — по самому длинному подходящему префиксу,
      иначе `None` / `DEFAULT_BODY_LIMIT`;
    - повтор префикса → `ValueError`.
  - `hooks_of(app: FastAPI) -> AppHooks` — `app.state.pcbk_hooks`.
  - `class BodyLimit` — чистое ASGI-звено с поведением Д3б:
    - сверка `Content-Length`, затем чтение не больше `предел + 1` байт;
    - 413 `{"detail": "тело запроса больше N байт"}` и строка журнала
      `outcome=too_large path=…`;
    - предел — `hooks.limit_for(scope["path"])`;
    - после выдачи буфера приложению `receive` передаёт вызовы исходному
      `receive`. Иначе потоковый ответ не узнает об обрыве клиента
      (`http.disconnect`).
- Produces (`app.py`):
  - `create_app` ставит `app.state.pcbk_hooks = AppHooks()` до роутеров. После
    `install(app)` всех ролей:
    - один `app.add_exception_handler(RequestValidationError, …)`: он зовёт
      `handler_for(path)`, иначе `request_validation_exception_handler`
      FastAPI;
    - один `app.add_middleware(BodyLimit, hooks=…)`.
  - `GET /healthz/live` → `200 {"ok": true}` всегда. Стоит раньше
    `/healthz/{role}`; роль с именем `live` → `ValueError`.
  - `main.main(["--healthcheck"])` ходит в `/healthz/live`: здоровье ролей
    видит только сторож, а 402 от OpenRouter не делает `core` `unhealthy`.
- `DataRole.install(app)` — `hooks_of(app).validation("/api/data/", …)` вместо
  прямого `add_exception_handler`; `add_middleware` роль больше не зовёт.
  Предел `/api/data/` и `/mcp` — по умолчанию, 64 КиБ.
- `helpers.py`:
  - `DummyRole(name, health, installs=False, validates: str | None = None, body_limit: tuple[str, int] | None = None)`.
    При `validates` роль регистрирует обработчик 422 на префикс, отвечающий
    `422 {"handled_by": name}`, и маршрут `POST {prefix}typed` с телом-моделью
    `{"n": int}`;
  - `serve_app(app) -> ContextManager[str]` — `uvicorn.Server` в потоке на
    `127.0.0.1:0`, отдаёт базовый URL. Вынесен из `core_server` Д3б, тот
    пользуется им же.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_hooks.py
def test_validation_dispatch_by_prefix_two_roles(tmp_path):                    # Review Focus 1
    app, role = make_app(tmp_path, extra_roles=[DummyRole("llm", (True, "ок"), validates="/dummy-llm/")])
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 422 and r.json()["status"] == "refused"
        assert role.events.recent(channel="http")[0].outcome == "refused"
        assert c.post("/dummy-llm/typed", json={"n": "x"}).json() == {"handled_by": "llm"}

def test_body_limit_per_prefix():                                                # Review Focus 1
    roles = [DummyRole("llm", (True, "ок"), installs=True, body_limit=("/dummy", 200_000))]
    with TestClient(create_app(SETTINGS, roles)) as c:
        assert c.post("/dummy", content=b"x" * 150_000).status_code == 200
        assert c.post("/dummy", content=b"x" * 250_000).status_code == 413
        assert c.post("/healthz", content=b"x" * 70_000).status_code == 413     # по умолчанию 64 КиБ

def test_hooks_longest_prefix_and_duplicates():
    h = AppHooks()
    h.body_limit("/llm/", 10); h.body_limit("/llm/v1/", 20)
    assert (h.limit_for("/llm/v1/chat/completions"), h.limit_for("/llm/x"), h.limit_for("/mcp")) == \
           (20, 10, DEFAULT_BODY_LIMIT)
    with pytest.raises(ValueError):
        h.body_limit("/llm/", 30)

def test_healthz_live_ignores_role_health():
    with TestClient(create_app(SETTINGS, [DummyRole("llm", (False, "нет ключа"))])) as c:
        assert c.get("/healthz").status_code == 503
        assert (c.get("/healthz/live").status_code, c.get("/healthz/live").json()) == (200, {"ok": True})
    with pytest.raises(ValueError):
        create_app(SETTINGS, [DummyRole("live", (True, ""))])

def test_healthcheck_passes_with_unhealthy_role(monkeypatch):
    with serve_app(create_app(SETTINGS, [DummyRole("llm", (False, "нет ключа"))])) as url:
        monkeypatch.setenv("CORE_PORT", url.rsplit(":", 1)[1])
        assert main(["--healthcheck"]) == 0
```

`make_app(tmp_path, extra_roles=())` — помощник Д3б с дополнительными ролями в
`create_app`.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_hooks.py`. Expected: FAIL — нет `pcbk_core.hooks`.
- [ ] **Step 3: Implement `hooks.py`, правки `create_app`, `DataRole.install`, `main` по интерфейсам.**
- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS (включая `test_http.py` и `test_mcp.py` Д3б).
- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Серверный слой: один обработчик 422 и предел тела по префиксу, живость /healthz/live"`.

---

### Task 3: Политика запроса — модели, тело, шаги

**Files:**
- Create: `core/pcbk_core/llm/__init__.py` (пока пустой), `core/pcbk_core/llm/models.py`,
  `core/pcbk_core/llm/request.py`, `core/pcbk_core/llm/turns.py`
- Test: `core/tests/test_llm_request.py`, `core/tests/test_llm_turns.py`

**Interfaces:**
- Consumes: `FakeMono` — Д3а.
- Produces (`models.py`):
  - `@dataclass(frozen=True) class ModelPolicy: model: str; provider_only: str; effort: Literal["low", "medium", "high"]; auto_cache: bool = False`
  - `CANDIDATES: tuple[ModelPolicy, ...]` — ровно семь, по порядку research/08:

    | `model` | `provider_only` | `effort` | `auto_cache` |
    |---|---|---|---|
    | `z-ai/glm-5.3` | `fireworks` | `high` | — |
    | `deepseek/deepseek-v4.1-flash` | `fireworks` | `high` | — |
    | `google/gemini-3.8-flash` | `google-vertex/global` | `medium` | — |
    | `openai/gpt-6-sol` | `azure` | `medium` | — |
    | `z-ai/glm-5.3-flash` | `fireworks` | `low` | — |
    | `openai/gpt-6-luna` | `azure` | `medium` | — |
    | `anthropic/claude-sonnet-5.5` | `google-vertex/global` | `low` | да |

  - `PROVIDER_FIXED = {"allow_fallbacks": False, "zdr": True, "data_collection": "deny", "require_parameters": True}`
  - `select_models(names: str) -> dict[str, ModelPolicy]`:
    - `""` → все семь по порядку;
    - иначе список через запятую, порядок — как в строке;
    - неизвестное имя или повтор → `ValueError("LLM_MODELS: …")`.
  - `provider_matches(pinned: str, reported: str) -> bool` — обе строки в
    нижнем регистре и без знаков, кроме букв и цифр; одна — начало другой
    (`google-vertex/global` ~ `Google Vertex`, `fireworks` ~ `Fireworks`).
- Produces (`request.py`):
  - `MAX_TOKENS_CAP = 16000`, `MAX_MESSAGES = 4000`
  - `class BodyRefused(ValueError)` — текст по-русски (`str(e)`), идёт в
    `LLM_MESSAGES["body"]`
  - `SESSION_RE = re.compile(r"ses_[0-9A-Za-z]{26}")`;
    `session_of(header: str | None) -> str | None` — `fullmatch`, иначе `None`
  - `rebuild_body(raw: object, policy: ModelPolicy, *, caller: str, session: str | None, max_tokens_cap: int = MAX_TOKENS_CAP) -> dict` —
    новое тело, только эти ключи:
    - `model` = `policy.model`;
    - `messages` — 1…`MAX_MESSAGES`, каждое собирается заново по роли:
      - `system`, `user`: `role`, `content` — строка или список частей
        `{"type": "text", "text": str}`; часть другого вида (`image_url`,
        `file`, `input_audio` и прочие) → `BodyRefused`;
      - `assistant`: `role`, `content` (строка, `None` или части текста),
        `tool_calls` (каждый — ровно
        `{"id", "type": "function", "function": {"name", "arguments"}}`),
        `reasoning`, `reasoning_content` (строки), `reasoning_details` —
        список как есть: подписи рассуждений Gemini и Claude (research/08 §6);
      - `tool`: `role`, `tool_call_id`, `content`;
      - другая роль, не список, пустой список → `BodyRefused`; `name` и
        `cache_control` в сообщениях и частях выбрасываются;
    - `tools` — только `type == "function"`, каждый ровно
      `{"type": "function", "function": {"name", "description"?, "parameters"?}}`;
      прочие (`openrouter:*`, `web_search_preview` и т. п.) выбрасываются;
      пустой итог — ключа нет;
    - `tool_choice` — `"auto" | "none" | "required"` или
      `{"type": "function", "function": {"name": str}}`, иначе ключа нет;
    - `stream` — `bool`; при потоке `stream_options = {"include_usage": True}`;
    - `max_tokens` — `min(int(max_tokens or max_completion_tokens), cap)`;
      не целое или ≤ 0 → `cap`; нет обоих → `cap`;
    - `stop` (строка или до 4 строк), `seed` (целое), `response_format`
      (`type` ∈ `text | json_object | json_schema`), `temperature` (0…2),
      `top_p` (0…1) — только если значение годно, иначе ключа нет;
    - `reasoning` = `{"effort": policy.effort}`;
    - `provider` = `{"only": [policy.provider_only], **PROVIDER_FIXED}`;
    - `user` = `f"pcbk-{caller}"`; `session_id` = `f"{caller}:{session or 'none'}"`;
    - `cache_control` = `{"type": "ephemeral"}` — только при `policy.auto_cache`.

    Не объект → `BodyRefused("тело запроса — не объект JSON")`.
  - `last_role(body: dict) -> str` — роль последнего сообщения.
- Produces (`turns.py`):
  - `TURN_CALLS = 16`, `TURN_IDLE_S = 3600.0`
  - `class TurnLimitReached(Exception): step: int`
  - `class TurnLimiter`:
    - `__init__(self, limit: int = TURN_CALLS, idle_s: float = TURN_IDLE_S, monotonic=time.monotonic)`;
    - `step(self, caller: str, session: str | None, last_role: str) -> int` —
      ключ `(caller, session or "none")`;
    - `last_role == "user"` начинает ход (шаг 1), иначе шаг + 1; первый
      увиденный запрос без хода — тоже шаг 1;
    - шаг больше `limit` → `TurnLimitReached`, счётчик не растёт;
    - ключи, не тронутые `idle_s`, забываются.

    Так research/09 §2.4: ход начинается запросом, у которого последнее
    сообщение — `user`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_llm_request.py
POLICY = select_models("")["z-ai/glm-5.3"]
SES = "ses_" + "A" * 26
OC_BODY = {   # как шлёт OpenCode 1.18.33 через @ai-sdk/openai-compatible (research/08 §6)
    "model": "z-ai/glm-5.3", "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 32000,
    "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": [{"type": "text", "text": "вопрос"}]}],
    "tools": [{"type": "function", "function": {"name": "pcbk_tag_now", "description": "d", "parameters": {"type": "object"}}}],
}
EVIL = {"models": ["x/y"], "route": "fallback", "plugins": [{"id": "web"}], "preset": "p", "service_tier": "priority",
        "provider": {"allow_fallbacks": True}, "user": "evil", "session_id": "evil", "transforms": ["middle-out"],
        "n": 3, "logprobs": True, "metadata": {"a": 1}, "prompt_cache_key": "k", "cache_control": {"type": "ephemeral"},
        "reasoning": {"effort": "max"}, "reasoning_effort": "max", "include_reasoning": True,
        "web_search_options": {}, "max_completion_tokens": 999999, "debug": {"echo_upstream_body": True}}

def test_rebuild_keeps_only_whitelist_and_sets_our_fields():                    # Review Focus 4
    b = rebuild_body({**OC_BODY, **EVIL}, POLICY, caller="student-01", session=SES)
    assert set(b) == {"model", "messages", "tools", "stream", "stream_options", "max_tokens", "reasoning",
                      "provider", "user", "session_id"}
    assert b["provider"] == {"only": ["fireworks"], "allow_fallbacks": False, "zdr": True,
                             "data_collection": "deny", "require_parameters": True}
    assert (b["max_tokens"], b["reasoning"], b["user"], b["session_id"]) == \
           (16000, {"effort": "high"}, "pcbk-student-01", f"student-01:{SES}")

def test_server_tools_dropped_function_tools_rebuilt():
    tools = OC_BODY["tools"] + [{"type": "openrouter:web_search"}, {"type": "web_search_preview"},
                                {"type": "function", "function": {"name": "x", "parameters": {}}, "strict": True}]
    b = rebuild_body({**OC_BODY, "tools": tools}, POLICY, caller="student-01", session=None)
    assert [t["function"]["name"] for t in b["tools"]] == ["pcbk_tag_now", "x"]
    assert all(set(t) == {"type", "function"} for t in b["tools"]) and b["session_id"] == "student-01:none"

def test_messages_rebuilt_per_role():
    msgs = [{"role": "user", "content": "q", "cache_control": {"type": "ephemeral"}, "name": "n"},
            {"role": "assistant", "content": None, "reasoning_content": "",
             "reasoning_details": [{"type": "reasoning.encrypted", "data": "x"}],
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "pcbk_tag_now", "arguments": "{}"}, "extra": 1}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"status\": \"ok\"}"}]
    b = rebuild_body({**OC_BODY, "messages": msgs}, POLICY, caller="student-01", session=None)
    assert b["messages"][0] == {"role": "user", "content": "q"}
    assert b["messages"][1]["reasoning_details"] == msgs[1]["reasoning_details"]
    assert b["messages"][1]["tool_calls"] == [{"id": "call_1", "type": "function",
                                               "function": {"name": "pcbk_tag_now", "arguments": "{}"}}]
    assert b["messages"][2] == msgs[2]

@pytest.mark.parametrize("bad", [[], "x", [{"role": "developer", "content": "x"}],
    [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}]}],
    [{"role": "user", "content": [{"type": "file", "file": {}}]}]])
def test_bad_messages_refused(bad):
    with pytest.raises(BodyRefused):
        rebuild_body({**OC_BODY, "messages": bad}, POLICY, caller="student-01", session=None)

@pytest.mark.parametrize("given,want", [({"max_tokens": 500}, 500), ({"max_completion_tokens": 1000}, 1000),
                                        ({"max_tokens": "x"}, 16000), ({"max_tokens": -5}, 16000), ({}, 16000)])
def test_max_tokens_cap(given, want):
    body = {k: v for k, v in OC_BODY.items() if k != "max_tokens"} | given
    assert rebuild_body(body, POLICY, caller="ops", session=None)["max_tokens"] == want

def test_claude_gets_auto_cache_others_not():
    claude = select_models("")["anthropic/claude-sonnet-5.5"]
    assert rebuild_body({**OC_BODY, "model": claude.model}, claude, caller="ops", session=None)["cache_control"] == \
           {"type": "ephemeral"}
    assert "cache_control" not in rebuild_body(OC_BODY, POLICY, caller="ops", session=None)

def test_model_list_exact():                                                     # Review Focus 4
    allowed = select_models("")
    assert list(allowed) == ["z-ai/glm-5.3", "deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash",
                             "openai/gpt-6-sol", "z-ai/glm-5.3-flash", "openai/gpt-6-luna",
                             "anthropic/claude-sonnet-5.5"]
    assert [p.provider_only for p in allowed.values()] == ["fireworks", "fireworks", "google-vertex/global", "azure",
                                                         "fireworks", "azure", "google-vertex/global"]
    assert [p.effort for p in allowed.values()] == ["high", "high", "medium", "medium", "low", "medium", "low"]
    assert list(select_models("z-ai/glm-5.3")) == ["z-ai/glm-5.3"]
    for bad in ("z-ai/glm-5.3:nitro", "~z-ai/glm-latest", "openrouter/auto", "z-ai/glm-5.3,z-ai/glm-5.3"):
        with pytest.raises(ValueError):
            select_models(bad)

def test_provider_matches_and_session_of():
    assert provider_matches("fireworks", "Fireworks") and provider_matches("google-vertex/global", "Google Vertex")
    assert provider_matches("azure", "Azure") and not provider_matches("fireworks", "Together")
    assert (session_of(SES), session_of("ses_1"), session_of(None)) == (SES, None, None)

# core/tests/test_llm_turns.py
def test_turn_counts_steps_and_resets_on_user():
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    assert [t.step("student-01", "ses_a", r) for r in ("user", "tool", "tool")] == [1, 2, 3]
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", "assistant")          # подсказка OpenCode о шагах — тоже шаг
    assert t.step("student-01", "ses_a", "user") == 1

def test_turns_are_per_caller_and_session():
    t = TurnLimiter(limit=2, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", "user"); t.step("student-01", "ses_a", "tool")
    assert t.step("student-02", "ses_a", "tool") == 1 and t.step("student-01", "ses_b", "tool") == 1

def test_idle_turn_forgotten():
    mono = FakeMono(0.0)
    t = TurnLimiter(limit=2, idle_s=60, monotonic=mono)
    t.step("ops", None, "user"); t.step("ops", None, "tool"); mono.advance(61)
    assert t.step("ops", None, "tool") == 1
```

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_llm_request.py tests/test_llm_turns.py`. Expected: FAIL — нет `pcbk_core.llm`.
- [ ] **Step 3: Implement `models.py`, `request.py`, `turns.py` по интерфейсам и таблице.**
- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS.
- [ ] **Step 5: Commit** — `git add core/ && git commit -m "LLM-прокси: семь кандидатов с закреплённой точкой, тело из белого списка, предел 16 обращений на ход"`.

---

### Task 4: Журнал вызовов и доли

**Files:**
- Create: `core/pcbk_core/llm/ledger.py`
- Test: `core/tests/test_llm_ledger.py`

**Interfaces:**
- Consumes: `DB_PATH` — Д3б (та же `core.db`, соединение на операцию, как `EventLog`).
- Produces:
  - `@dataclass(frozen=True) class Usage: cost: float | None; prompt_tokens: int; completion_tokens: int; cached_tokens: int; cache_write_tokens: int; reasoning_tokens: int`;
    `Usage.from_openrouter(usage: dict) -> Usage` — `cost`;
    `prompt_tokens_details.cached_tokens`, `.cache_write_tokens`;
    `completion_tokens_details.reasoning_tokens`; нет поля → 0, нет цены →
    `None`.
  - Таблица `llm_calls` — ровно столбцы `id` (первичный ключ), `ts`,
    `caller`, `session`, `model`, `step`, `status`, `code`, `http_status`,
    `provider`, `provider_ok`, `generation_id`, `cost`, `cost_source`,
    `prompt_tokens`, `completion_tokens`, `cached_tokens`,
    `cache_write_tokens`, `reasoning_tokens`, `duration_ms`. Индекс по `ts`.
    Столбцов с содержимым нет.
    - `status`: `open`, `ok`, `upstream_error`, `money`, `refused`,
      `cancelled`, `lost`;
    - `cost_source`: `stream`, `body`, `generation`, `pending`, `unknown`,
      `none`.
  - `@dataclass(frozen=True) class CallRow` — те же поля.
  - `class Ledger`:
    - `__init__(self, path: str)` — таблица, суммы по вызывающим в память;
      строки `open` от прошлого запуска → `lost` и `unknown`;
    - `open_call(self, *, ts: datetime, caller: str, session: str | None, model: str, step: int) -> int`;
    - `close_call(self, call_id: int, *, status: str, code: str, http_status: int, provider: str | None, provider_ok: bool | None, generation_id: str | None, usage: Usage | None, cost_source: str, duration_ms: int) -> None`;
    - `refused(self, *, ts: datetime, caller: str, session: str | None, model: str, code: str, http_status: int) -> None` —
      строка с ценой 0 и `cost_source="none"`;
    - `set_cost(self, call_id: int, cost: float | None, source: str) -> None` —
      `pending` → `generation` или `unknown`;
    - `spent(self, caller: str | None = None) -> float` — из памяти;
    - `totals(self, now: datetime) -> dict` — ключи `spent_usd`, `by_caller`,
      `pending`, `cost_unknown_24h`, `provider_mismatch_24h` (`provider_ok = 0`
      за 24 ч);
    - `recent(self, limit: int = 10) -> list[CallRow]` — новые сверху.
  - `class Budget`:
    - `__init__(self, ledger: Ledger, *, total_usd: float, share_usd: float, ops_share_usd: float)`;
    - `share_of(self, caller: str) -> float` — `ops_share_usd` для `ops`,
      иначе `share_usd`. Перераспределение долей — Д10;
    - `refusal(self, caller: str) -> Literal["budget_spent", "share_spent"] | None` —
      сначала общий бюджет, потом доля.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_llm_ledger.py
T = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
U = Usage(0.0123, 100, 20, 60, 10, 5)

def close_ok(led, cid, usage=U, source="stream"):
    led.close_call(cid, status="ok", code="", http_status=200, provider="Fireworks", provider_ok=True,
                   generation_id="gen-1", usage=usage, cost_source=source, duration_ms=900)

def test_usage_from_openrouter():
    u = Usage.from_openrouter({"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0123,
                               "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 10},
                               "completion_tokens_details": {"reasoning_tokens": 5}})
    assert u == U and Usage.from_openrouter({"prompt_tokens": 1}).cost is None

def test_ledger_sums_survive_reopen(tmp_path):
    p = str(tmp_path / "core.db")
    led = Ledger(p)
    close_ok(led, led.open_call(ts=T, caller="student-01", session="ses_x", model="z-ai/glm-5.3", step=1))
    led.refused(ts=T, caller="student-02", session=None, model="x", code="pcbk_turn_limit", http_status=403)
    again = Ledger(p)
    assert (again.spent(), again.spent("student-01"), again.spent("student-02")) == (pytest.approx(0.0123),
                                                                                     pytest.approx(0.0123), 0.0)
    assert [r.status for r in again.recent()] == ["refused", "ok"]

def test_open_call_after_crash_is_unknown(tmp_path):                              # Review Focus 2
    p = str(tmp_path / "core.db")
    Ledger(p).open_call(ts=T, caller="student-01", session=None, model="m", step=1)
    t = Ledger(p).totals(T + timedelta(minutes=1))
    assert (t["cost_unknown_24h"], t["pending"]) == (1, 0)

def test_pending_then_generation_cost(tmp_path):
    led = Ledger(str(tmp_path / "core.db"))
    cid = led.open_call(ts=T, caller="ops", session=None, model="m", step=1)
    close_ok(led, cid, usage=None, source="pending")
    assert led.totals(T)["pending"] == 1 and led.spent() == 0.0
    led.set_cost(cid, 0.004, "generation")
    assert led.totals(T)["pending"] == 0 and led.spent("ops") == pytest.approx(0.004)

def test_budget_total_before_share(tmp_path):
    led = Ledger(str(tmp_path / "core.db"))
    for caller, cost in (("student-01", 100.0), ("student-02", 5.0)):
        close_ok(led, led.open_call(ts=T, caller=caller, session=None, model="m", step=1), usage=replace(U, cost=cost))
    b = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=80.0)
    assert (b.refusal("student-01"), b.refusal("student-02"), b.share_of("ops")) == ("share_spent", None, 80.0)
    assert Budget(led, total_usd=100.0, share_usd=100.0, ops_share_usd=80.0).refusal("student-02") == "budget_spent"

def test_no_content_columns(tmp_path):
    Ledger(str(tmp_path / "core.db"))
    cols = {r[1] for r in sqlite3.connect(tmp_path / "core.db").execute("PRAGMA table_info(llm_calls)")}
    assert not cols & {"messages", "content", "body", "prompt", "answer", "text"}
```

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_llm_ledger.py`. Expected: FAIL.
- [ ] **Step 3: Implement `ledger.py` по интерфейсам.**
- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS.
- [ ] **Step 5: Commit** — `git add core/ && git commit -m "LLM-прокси: журнал вызовов в SQLite без содержимого, доли и общий бюджет"`.

---

### Task 5: Проход к OpenRouter — двойник, клиент, поток, признаки

**Files:**
- Create: `core/tests/fake_openrouter.py`, `core/pcbk_core/llm/sse.py`,
  `core/pcbk_core/llm/upstream.py`
- Modify: `core/requirements.in` (`httpx==` — версия из lock), `core/requirements.lock`,
  `core/tests/conftest.py` (фикстура `fake_or`)
- Test: `core/tests/test_llm_sse.py`, `core/tests/test_llm_upstream.py`

**Interfaces:**
- Produces (`fake_openrouter.py`, только stdlib, `ThreadingHTTPServer`, HTTP/1.0):
  - `make_server(host: str, port: int) -> ThreadingHTTPServer`; при запуске
    как `__main__` — `0.0.0.0:8080`. Верный ключ — `test-openrouter-key`,
    ключ управления — `test-mgmt-key`.
  - `POST /api/v1/chat/completions` записывает `{"auth_ok": bool, "body": dict, "session": заголовок X-Session-Id}`.
    Поведение выбирает слово в тексте последнего сообщения `user`:

    | Слово | Ответ |
    |---|---|
    | `FAKE402` | 402 `{"error": {"code": 402, "message": "Insufficient credits. Add more using https://openrouter.ai/settings/credits"}}` |
    | `FAKE402INFLIGHT` | 402, `metadata.limit_source = "in_flight_budget"`, `Retry-After: 1` |
    | `FAKE403KEY` | 403 `"Key limit exceeded"` |
    | `FAKE403REGION` | 403 `"This model is not available in your region."` |
    | `FAKE401` / `FAKE500` / `FAKE404` | 401 / 500 / 404 `"No endpoints found matching your data policy"` |
    | `FAKEMIDERR` | 200, один чанк текста, затем `data: {"error": {"code": 502, "message": "Provider returned error"}}`, без `[DONE]` |
    | `FAKEMID402` | то же с кодом 402 |
    | `FAKENOUSAGE` | 200, поток до `[DONE]` без чанка с `usage` |
    | `FAKEBREAK` | один чанк и обрыв соединения |
    | `FAKESLOW` | чанк каждые 0,5 с 10 с подряд; обрыв клиента — в счётчик `aborted` |
    | `FAKEPROVIDER` | обычный ответ, но `provider: "Together"` |
    | иначе | см. ниже |

    Обычный ответ:
    - первым идёт комментарий `: OPENROUTER PROCESSING`;
    - если в `tools` есть `pcbk_tag_now` и сообщений `tool` в этом ходе нет —
      вызов инструмента `pcbk_tag_now` с `{"tags": ["20FAKE_001_PV"]}`;
    - если любое сообщение `user` содержит `FAKELOOP` — вызов инструмента на
      каждом шаге. Аргумент `20FAKE_<N>_PV` меняется с числом сообщений
      `tool`: одинаковые вызовы подряд OpenCode остановил бы вопросом
      `doom_loop`;
    - иначе текст «Готово: » и первые 60 знаков последнего сообщения `tool`
      или «нет инструмента»;
    - последний чанк —
      `{"id": gen, "provider": "Fireworks", "choices": [{"index": 0, "delta": {}, "finish_reason": …}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.0012, "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": {"reasoning_tokens": 2}}}`,
      затем `data: [DONE]`;
    - заголовок `X-Generation-Id: gen-<N>`;
    - при `stream: false` — JSON с тем же `usage`.
  - `GET /api/v1/generation?id=` — на первые два запроса по id 404, потом
    `{"data": {"id": …, "total_cost": 0.002}}`.
  - `GET /api/v1/key` — `{"data": {"label": "test", "usage": <сумма отданных цен>, "limit": null, "limit_remaining": null}}`;
    чужой ключ — 401.
  - `GET /api/v1/credits` — с ключом управления
    `{"data": {"total_credits": 50.0, "total_usage": 20.0}}`, иначе 403.
  - `GET /_requests` — записанные запросы чата, `DELETE /_requests` — сброс;
    `GET /_stats` — `{"aborted": n}`. Это ручки только для тестов.
  - Фикстура `fake_or` (на функцию): сервер на `127.0.0.1:0`, атрибуты `url`
    (`…/api/v1`), `requests` (список), `aborted`.
- Produces (`sse.py`):
  - `class SseObserver` — `feed(self, chunk: bytes) -> None`:
    - события делятся пустой строкой (`\n\n` и `\r\n\r\n`), неполное событие
      ждёт следующего куска;
    - у строк `data:` полезная часть — `[DONE]` (→ `done = True`) или объект
      JSON;
    - комментарии и не-JSON пропускаются.

    Поля: `generation_id` (первый `id`), `provider` (первый), `usage`
    (последний встреченный), `error` (первый объект `error`), `done`.
- Produces (`upstream.py`):
  - `UpstreamKind = Literal["money_out", "key_limit", "in_flight", "key_rejected", "region", "rate", "rejected", "server", "unreachable"]`
  - `KEY_LIMIT_RE = re.compile(r"key limit exceeded|budget limit exceeded", re.I)`,
    `CREDITS_RE = re.compile(r"insufficient credits", re.I)`,
    `REGION_RE = re.compile(r"not available in your region", re.I)`
  - `classify(status: int, error: dict | None) -> UpstreamKind` — первая
    подходящая строка:

    | Условие | Вид |
    |---|---|
    | 401 | `key_rejected` |
    | 402 и `error.metadata.limit_source == "in_flight_budget"` | `in_flight` |
    | 402 или 403, текст по `KEY_LIMIT_RE` | `key_limit` |
    | 402, или 403 с текстом по `CREDITS_RE` | `money_out` |
    | 403 с текстом по `REGION_RE` | `region` |
    | 429 | `rate` |
    | 408 или ≥ 500 | `server` |
    | прочие 4xx | `rejected` |

    Код события ошибки посреди потока (`error.code`) разбирается той же
    таблицей.
  - `class UpstreamError(Exception): kind: UpstreamKind; status: int; message: str; retry_after: str | None` —
    `message` — первые 300 знаков текста OpenRouter.
  - `class Upstream`:
    - `__init__(self, base_url: str, key: str, mgmt_key: str | None, *, drill: str = "", client: httpx.AsyncClient | None = None)` —
      `httpx.Timeout(connect=10, read=120, write=30, pool=10)`, до 32 соединений;
    - `async def open_chat(self, body: dict) -> httpx.Response` — ответ 200
      открытым потоком (`send(..., stream=True)`). Иначе тело ошибки читается
      (до 64 КиБ), ответ закрывается и бросается `UpstreamError`. Нет связи или
      срок → `UpstreamError("unreachable", 0, …)`. При `drill == "402"` сразу
      бросается `UpstreamError("money_out", 402, "Insufficient credits (учения)")`,
      в сеть запрос не уходит;
    - `async def generation_cost(self, gen_id: str) -> float | None` — один
      запрос; 404 и любая ошибка → `None`;
    - `async def key_info(self) -> dict` — `data` из `/key`; ошибки →
      `UpstreamError` по `classify`;
    - `async def credits(self) -> float | None` — `total_credits − total_usage`
      ключом управления; без ключа — `None`;
    - `async def aclose(self) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_llm_sse.py
STREAM = (": OPENROUTER PROCESSING\n\n"
          'data: {"id":"gen-1","provider":"Fireworks","choices":[{"index":0,"delta":{"content":"Да"}}]}\n\n'
          'data: {"id":"gen-1","provider":"Fireworks","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
          '"usage":{"prompt_tokens":10,"completion_tokens":2,"cost":0.0012}}\n\ndata: [DONE]\n\n').encode()

@pytest.mark.parametrize("size", [1, 7, 64, len(STREAM)])
def test_observer_any_chunking(size):
    o = SseObserver()
    for i in range(0, len(STREAM), size):
        o.feed(STREAM[i:i + size])
    assert (o.generation_id, o.provider, o.usage["cost"], o.done, o.error) == ("gen-1", "Fireworks", 0.0012, True, None)

def test_observer_crlf_and_error_event():
    o = SseObserver()
    o.feed(b'data: {"id":"gen-2","choices":[]}\r\n\r\ndata: {"error":{"code":402,"message":"Insufficient credits"}}\r\n\r\n')
    assert o.error["code"] == 402 and not o.done and o.usage is None

# core/tests/test_llm_upstream.py
pytestmark = pytest.mark.anyio
BODY = {"model": "openai/gpt-6-luna", "stream": True, "messages": [{"role": "user", "content": "q"}]}

def body_with(word):
    return {**BODY, "messages": [{"role": "user", "content": f"q {word}"}]}

def test_classify_table():                                                       # Review Focus 3
    e = lambda msg, **meta: {"message": msg, **({"metadata": meta} if meta else {})}
    assert classify(402, e("Insufficient credits")) == "money_out"
    assert classify(402, e("x", limit_source="in_flight_budget")) == "in_flight"
    assert classify(403, e("Key limit exceeded")) == classify(402, e("Budget limit exceeded")) == "key_limit"
    assert classify(403, e("insufficient credits")) == "money_out"
    assert classify(403, e("This model is not available in your region.")) == "region"
    assert (classify(401, None), classify(429, None), classify(503, None), classify(400, e("bad"))) == \
           ("key_rejected", "rate", "server", "rejected")

async def test_open_chat_streams_with_generation_header(fake_or):
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    r = await up.open_chat(BODY)
    data = b"".join([c async for c in r.aiter_raw()])
    await r.aclose()
    assert r.headers["x-generation-id"].startswith("gen-") and b"data: [DONE]" in data
    assert fake_or.requests[-1]["auth_ok"] is True

@pytest.mark.parametrize("word,kind,status", [("FAKE402", "money_out", 402), ("FAKE402INFLIGHT", "in_flight", 402),
    ("FAKE403KEY", "key_limit", 403), ("FAKE403REGION", "region", 403), ("FAKE401", "key_rejected", 401),
    ("FAKE500", "server", 500), ("FAKE404", "rejected", 404)])
async def test_errors_classified(fake_or, word, kind, status):                   # Review Focus 3
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "test-openrouter-key", None).open_chat(body_with(word))
    assert (e.value.kind, e.value.status) == (kind, status)
    assert (e.value.retry_after == "1") is (word == "FAKE402INFLIGHT")

async def test_unreachable_is_fast():
    t = time.monotonic()
    with pytest.raises(UpstreamError) as e:
        await Upstream("http://127.0.0.1:9/api/v1", "k", None).open_chat(BODY)
    assert e.value.kind == "unreachable" and time.monotonic() - t < 2

async def test_drill_402_never_touches_network(fake_or):
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "test-openrouter-key", None, drill="402").open_chat(BODY)
    assert (e.value.kind, e.value.status) == ("money_out", 402) and fake_or.requests == []

async def test_generation_cost_after_404s(fake_or):
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    r = await up.open_chat(BODY); gen = r.headers["x-generation-id"]; await r.aclose()
    assert [await up.generation_cost(gen) for _ in range(3)] == [None, None, 0.002]

async def test_key_info_and_credits(fake_or):
    assert (await Upstream(fake_or.url, "test-openrouter-key", "test-mgmt-key").credits()) == 30.0
    assert (await Upstream(fake_or.url, "test-openrouter-key", None).credits()) is None
    assert "usage" in await Upstream(fake_or.url, "test-openrouter-key", None).key_info()
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "wrong", None).key_info()
    assert e.value.kind == "key_rejected"
```

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_llm_sse.py tests/test_llm_upstream.py`. Expected: FAIL.
- [ ] **Step 3: Implement двойник, `sse.py`, `upstream.py`; `httpx` — явной строкой в `requirements.in`, lock пересобран `uv pip compile … --generate-hashes`.**
- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS; `test_lock_pins_python_tds_and_mcp_1x` Д3а зелёный.
- [ ] **Step 5: Commit** — `git add core/ && git commit -m "LLM-прокси: двойник OpenRouter, наблюдатель потока, клиент с разбором «кончились деньги», учение 402 без сети"`.

---

### Task 6: Роль `llm` — ручка, отказы, опрос, ключи, учения

**Files:**
- Create: `core/pcbk_core/llm/state.py`, `core/pcbk_core/llm/api.py`
- Modify: `core/pcbk_core/llm/__init__.py` (`LlmRole`), `core/pcbk_core/settings.py`,
  `core/pcbk_core/secrets.py` (`read_key_file`), `core/pcbk_core/main.py`
  (`build_roles`, `egress_targets`), `core/tests/helpers.py`
- Test: `core/tests/test_llm_role.py`, `core/tests/test_skeleton.py` (+2)

**Interfaces:**
- Consumes: задачи 2–5; `TokenTable`, `install_egress_guard`, `Settings.from_env`
  (пустое — умолчание, числа > 0), `DB_PATH` — Д3а/Д3б.
- Produces (`settings.py`) — новые поля `Settings`:

| Поле | По умолчанию | Проверка |
|---|---|---|
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | схема `https` или `http`, есть хост |
| `OPENROUTER_KEY_FILE` | `/run/secrets/openrouter.key` | — |
| `OPENROUTER_MGMT_KEY_FILE` | `/run/secrets/openrouter-mgmt.key` | — |
| `LLM_TOKENS_FILE` | `/run/secrets/llm-tokens` | — |
| `LLM_MODELS` | `""` | `select_models` |
| `LLM_TOTAL_USD` | `1000.0` | > 0 |
| `LLM_SHARE_USD` | `100.0` | > 0 (больше общего бюджета — можно: тогда держит общий) |
| `LLM_OPS_SHARE_USD` | `100.0` | > 0 |
| `LLM_MAX_TOKENS` | `16000` | 1…16000 |
| `LLM_TURN_CALLS` | `16` | ≥ 1 |
| `LLM_POLL_S` | `60.0` | > 0 |
| `LLM_MAX_BODY_BYTES` | `4194304` | ≥ 65536 |
| `LLM_STATE` | `"on"` | `on` или `stopped` |
| `DRILL_LLM` | `""` | `""` или `402` |

  Ошибка проверки — `ValueError` с именем поля.
- Produces (`secrets.py`): `read_key_file(path: str) -> str | None` — пробелы и
  перевод строки срезаются; нет файла или пусто → `None`; текст ключа не
  попадает ни в исключения, ни в журнал.
- Produces (`state.py`):
  - `UPSTREAM_TEXTS = {"unknown": "OpenRouter ещё не опрашивался", "ok": "OpenRouter отвечает", "errors": "OpenRouter отвечает ошибками подряд", "unreachable": "OpenRouter недоступен", "key_rejected": "OpenRouter отклонил ключ стенда", "money_out": "на счёте OpenRouter кончились деньги", "key_limit": "исчерпан лимит ключа OpenRouter", "region": "OpenRouter отказывает по региону"}`
  - `FAIL_STATES = frozenset({"unreachable", "key_rejected", "money_out", "key_limit", "region"})`;
    `ERRORS_IN_ROW = 3`
  - `class UpstreamState`:
    - `__init__(self, drill: bool = False)` — при учениях к тексту
      добавляется « (учения)»;
    - `record(self, kind: UpstreamKind | None, now: datetime, *, source: Literal["chat", "poll"]) -> None`;
    - `clear_money(self, now: datetime) -> None`;
    - `to_json(self) -> dict` — `state`, `text`, `since`, `checked_at`
      (последний опрос).

    Правила `record`:

    | Что пришло | Что стало |
    |---|---|
    | `kind is None`, источник `chat` | `ok`, счётчик ошибок подряд — 0 |
    | `kind is None`, источник `poll` | `ok`, только если было `unknown`, `unreachable` или `key_rejected` |
    | `money_out`, `key_limit`, `key_rejected`, `region` | это же состояние |
    | `server`, `unreachable` из `chat` | счётчик + 1; при `ERRORS_IN_ROW` и если не в `FAIL_STATES` — `errors` |
    | `unreachable` из `poll` | `unreachable` |
    | `rate`, `in_flight`, `rejected` | без изменений |
    | `clear_money` | `money_out`, `key_limit` → `ok` |
- Produces (`api.py`):
  - `LLM_MESSAGES` — постоянные тексты (дословно):

```python
LLM_MESSAGES = {
    "unauthorized": "нужен токен",
    "stopped": "LLM-прокси остановлен администратором — модель сейчас не отвечает.",
    "key_missing": "LLM-прокси не настроен: нет ключа OpenRouter. Сообщите преподавателю.",
    "body": "Запрос к модели отклонён: ",
    "model": "Модель «{model}» не разрешена на учебном стенде.",
    "share_spent": "Ваша доля бюджета на модель (${share:.0f}) израсходована — модель больше не отвечает. "
                   "Обратитесь к преподавателю.",
    "budget_spent": "Общий бюджет стенда на модель (${total:.0f}) израсходован — модель не отвечает. "
                    "Сообщите преподавателю.",
    "turn_limit": "Предел шагов на ответ: {limit} обращений к модели за один ход. Ход остановлен — "
                  "задайте вопрос заново, короче или по частям.",
    "money_out": "На счёте OpenRouter кончились деньги — модель не отвечает. Сообщите преподавателю.",
    "key_limit": "Исчерпан лимит ключа OpenRouter — модель не отвечает. Сообщите преподавателю.",
    "key_rejected": "OpenRouter отклонил ключ стенда — модель не отвечает. Сообщите преподавателю.",
    "region": "OpenRouter отказывает по региону — модель недоступна. Сообщите преподавателю.",
    "wait": "OpenRouter просит подождать — повтор через несколько секунд.",
    "down": "OpenRouter не ответил — повторите позже.",
}
```

  - `REFUSALS: dict[str, tuple[int, str]]` — ключ `LLM_MESSAGES` →
    `(статус, code)` по таблице Д4а-R12; при учениях к тексту `money_out`
    добавляется « (учения)».
  - `llm_router(role: LlmRole) -> APIRouter`:
    - `POST /llm/v1/chat/completions`, проверки по порядку, первая сработавшая
      отвечает:
      1. токен `Authorization: Bearer` по таблице LLM (`role.tokens`) → 401 и
         строка журнала `outcome=unauthorized channel=llm` без токена;
      2. `LLM_STATE=stopped` → `stopped`;
      3. нет ключа → `key_missing`;
      4. тело — объект JSON;
      5. модель — точное совпадение с `role.models`;
      6. `budget.refusal` → `budget_spent` / `share_spent`;
      7. `rebuild_body` (`BodyRefused` → `body`);
      8. `turns.step(caller, session, last_role)` → `turn_limit`, только если
         в собранном теле есть `tools`. Запрос без инструментов (заголовок
         разговора, сжатие) ход не начинает и не считается, в журнале у него
         `step = 0`. Иначе заголовок, пришедший посреди первого хода, обнулял
         бы счётчик.

      Отказы из шагов 2–8 пишутся `ledger.refused` (401 — нет: вызывающий
      неизвестен). Дальше `ledger.open_call` и `upstream.open_chat`.
    - Поток: `StreamingResponse` отдаёт байты `aiter_raw()` как есть и кормит
      `SseObserver`. В `finally` ответ OpenRouter закрывается, вызов
      закрывается в журнале, обновляются `role.state` и строка журнала. Обрыв
      клиента — это `cancelled`: поток к OpenRouter закрывается сразу.
    - Итог потока:

      | Что увидел наблюдатель | Статус вызова | Цена | Состояние |
      |---|---|---|---|
      | `usage.cost` | `ok` (или `upstream_error`, если было событие ошибки) | `stream` | успех — `record(None, …)` |
      | событие ошибки | `upstream_error`, для 402 — `money` | из `usage`, если был | `record(classify(code, error), …)` |
      | ни цены, ни `usage` | как выше | `pending`, фоновая `resolve_cost` по `generation_id` (заголовок, иначе `id` чанка) | — |

      Сверка поля `provider` с закреплённой точкой (`provider_matches`) → в
      `provider_ok`.
    - Без потока (`stream: false`): тело JSON как есть, цена — `body`.
    - Ошибка OpenRouter до потока → ответ по `REFUSALS` (`rejected` — со
      статусом и текстом OpenRouter), `ledger.close_call` со статусом `money`
      или `upstream_error`, `role.state.record(kind, …)`.
    - Строка журнала на вызов:
      `llm caller=… model=… provider=… step=N status=… cost=… cost_source=… cached=N reasoning=N ms=N` —
      без текста и токенов.
    - `GET /health/llm` → `role.health_json(now)`, без токена.
- Produces (`LlmRole(RoleBase)`, `name = "llm"`):
  - `__init__(self, settings: Settings, *, tokens: TokenTable, upstream: Upstream | None, ledger: Ledger, monotonic=time.monotonic, wallclock=lambda: datetime.now(timezone.utc), cost_retry_delays: Sequence[float] = COST_RETRY_DELAYS_S)`;
    `upstream is None` — ключа нет; `COST_RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 15.0)`.
  - поля: `models`, `budget`, `turns`, `state`, `tokens`, `ledger`,
    `upstream`, `key_usage_usd: float | None`, `account_remaining_usd: float | None`.
  - `router()` → `llm_router(self)`; `install(app)` →
    `hooks_of(app).body_limit("/llm/", settings.LLM_MAX_BODY_BYTES)`.
  - `health() -> tuple[bool, str]`, первое подходящее:
    - выключатель → `(False, "LLM-прокси остановлен администратором")`;
    - нет ключа → `(False, "нет ключа OpenRouter")`;
    - общий бюджет исчерпан → `(False, "общий бюджет исчерпан")`;
    - состояние из `FAIL_STATES` → `(False, текст состояния)`;
    - иначе `(True, текст состояния)`.
  - `health_json(now: datetime) -> dict` — ровно ключи `checked_at`,
    `stopped`, `key` (`"ok"`/`"missing"`), `drill`, `upstream`
    (`state.to_json()`), `spent_usd`, `total_usd`, `ops_usd`, `shares`
    (`{"over_80": int, "spent": int}` — только места `student-NN`), `pending`,
    `cost_unknown_24h`, `provider_mismatch_24h`, `key_usage_usd`,
    `account_remaining_usd`, `models` (имена). Токенов, id вызывающих и
    текстов в ответе нет.
  - `async def poll(self) -> None`:
    - при выключателе или без ключа ничего не делает;
    - иначе `key_info()`: `usage` → `key_usage_usd`; `limit_remaining` ≤ 0 →
      `record("key_limit")`, больше 0 или `null` → `clear_money` для
      `key_limit`;
    - затем `credits()`: остаток → `account_remaining_usd`; ≤ 0 →
      `record("money_out")`, больше 0 → `clear_money`;
    - `UpstreamError` → `record(kind, source="poll")`.
  - `lifespan()`:
    - строки журнала при старте: «LLM-прокси: моделей N, доля $100, бюджет
      $1000»; при `DRILL_LLM` — «УЧЕНИЯ: DRILL_LLM=402»; при выключателе —
      «LLM-прокси остановлен администратором (LLM_STATE=stopped)»;
    - цикл `poll` сразу и раз в `LLM_POLL_S`;
    - на выходе отменяются задачи `resolve_cost` и закрывается `upstream`.
  - `async def resolve_cost(self, call_id: int, gen_id: str) -> None` — по
    задержкам `cost_retry_delays`; нашлась цена → `set_cost(…, "generation")`,
    иначе `set_cost(…, None, "unknown")`.
- Produces (`main.py`):
  - `egress_targets(settings: Settings, cfg: BdrvConfig) -> list[tuple[str, int | None]]` —
    `[(хост историана, порт), (хост OPENROUTER_BASE_URL, порт или 443/80)]`;
    строка журнала — «охрана выхода: разрешено 2 направления»;
  - `build_roles` добавляет `LlmRole(settings, tokens=TokenTable.from_file(settings.LLM_TOKENS_FILE), upstream=Upstream(…) if key else None, ledger=Ledger(settings.DB_PATH))`.
- `helpers.py`:
  - `LLM_TOKEN = "test-llm-token-student-01"`, `OPS_LLM_TOKEN = "test-llm-token-ops"`,
    `SES = "ses_" + "A" * 26`;
  - `chat_body(text="вопрос", model="z-ai/glm-5.3", stream=True, steps=0, tools=True)` —
    `steps` добавляет пары «assistant с вызовом — tool»; `tools` — один
    инструмент `pcbk_tag_now`, как в `OC_BODY` задачи 3;
  - `auth(token, session=None) -> dict`;
  - `make_llm_role(tmp_path, fake=None, *, key: str | None = "test-openrouter-key", mgmt: str | None = None, **settings_overrides) -> LlmRole`:
    - создаёт `tmp_path`, если его нет;
    - при `fake` берёт `OPENROUTER_BASE_URL = fake.url`;
    - `Settings` — через `replace(SETTINGS, …)`, без проверок `from_env`,
      как в Д3а;
    - таблица из двух токенов, задержки дозапроса `(0.01, 0.01, 0.01)`;
  - `make_llm_app(tmp_path, fake=None, **kw) -> tuple[FastAPI, LlmRole]` —
    `create_app(settings, [role])`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_llm_role.py
def post(c, body, token=LLM_TOKEN, session=SES):
    return c.post("/llm/v1/chat/completions", json=body, headers=auth(token, session))

def test_stream_passes_through_and_is_charged(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body())
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.text.rstrip().endswith("data: [DONE]")
    sent = fake_or.requests[-1]["body"]
    assert (sent["provider"]["only"], sent["user"], sent["max_tokens"]) == (["fireworks"], "pcbk-student-01", 16000)
    row = role.ledger.recent(1)[0]
    assert (row.status, row.cost, row.cost_source, row.provider_ok, row.step) == ("ok", 0.0012, "stream", True, 1)
    assert role.ledger.spent("student-01") == pytest.approx(0.0012)

def test_401_and_data_token_is_not_llm_token(tmp_path, fake_or, capfd):
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        assert post(c, chat_body(), token=None).status_code == 401
        assert post(c, chat_body(), token=TOKEN).status_code == 401           # токен данных Д3б
    err = capfd.readouterr().err
    assert err.count("outcome=unauthorized channel=llm") == 2 and TOKEN not in err and fake_or.requests == []

@pytest.mark.parametrize("model", ["z-ai/glm-5.3:nitro", "openrouter/auto", "~z-ai/glm-latest", "Z-AI/GLM-5.3"])
def test_model_refused_before_upstream(tmp_path, fake_or, model):               # Review Focus 4
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body(model=model))
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_model_refused")
    assert fake_or.requests == [] and role.ledger.recent(1)[0].status == "refused"

def test_share_and_total_spent_are_402_with_clear_text(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, LLM_SHARE_USD=0.001)
    with TestClient(app) as c:
        assert post(c, chat_body()).status_code == 200                          # 0,0012 > 0,001
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_share_spent")
        assert "Ваша доля бюджета на модель" in r.json()["error"]["message"]
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200     # у ops своя доля
    app2, _ = make_llm_app(tmp_path / "2", fake_or, LLM_TOTAL_USD=0.001)
    with TestClient(app2) as c:
        post(c, chat_body())
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).json()["error"]["code"] == "pcbk_budget_spent"
    assert len(fake_or.requests) == 3

def test_turn_limit_17th_call_refused(tmp_path, fake_or):
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        codes = [post(c, chat_body(steps=i)).status_code for i in range(16)]    # 1 user + 15 после инструмента
        title = post(c, chat_body(text="заголовок", tools=False))               # без инструментов — ход не трогает
        r = post(c, chat_body(steps=16))
        assert codes == [200] * 16 and title.status_code == 200
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_turn_limit")
        assert post(c, chat_body()).status_code == 200                          # новый ход
    assert len(fake_or.requests) == 18

@pytest.mark.parametrize("word,status,code", [("FAKE402", 402, "pcbk_money_out"), ("FAKE403KEY", 402, "pcbk_key_limit"),
    ("FAKE403REGION", 403, "pcbk_region"), ("FAKE401", 403, "pcbk_key_rejected")])
def test_money_signs_turn_row_red_until_success(tmp_path, fake_or, word, status, code):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body(text=f"q {word}"))
        assert (r.status_code, r.json()["error"]["code"]) == (status, code)
        assert role.health()[0] is False
        assert post(c, chat_body()).status_code == 200
    assert role.health()[0] is True and role.health_json(now_utc())["upstream"]["state"] == "ok"

def test_in_flight_is_wait_not_money(tmp_path, fake_or):                         # Review Focus 3
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKE402INFLIGHT"))
    assert (r.status_code, r.headers.get("retry-after"), r.json()["error"]["code"]) == (429, "1", "pcbk_upstream_wait")
    assert role.health()[0] is True

def test_mid_stream_error_passes_and_errors_in_row(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        for _ in range(3):
            r = post(c, chat_body(text="q FAKEMIDERR"))
            assert r.status_code == 200 and '"error"' in r.text
    assert role.ledger.recent(1)[0].status == "upstream_error"
    assert role.health_json(now_utc())["upstream"]["state"] == "errors"

def test_mid_stream_402_is_money_out(tmp_path, fake_or):                         # Review Focus 3
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKEMID402"))
    assert role.health_json(now_utc())["upstream"]["state"] == "money_out"

def test_no_usage_falls_back_to_generation(tmp_path, fake_or):                   # Review Focus 2
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKENOUSAGE"))
        wait_until(lambda: role.ledger.recent(1)[0].cost_source != "pending")
    row = role.ledger.recent(1)[0]
    assert (row.cost, row.cost_source) == (0.002, "generation")

def test_client_disconnect_closes_upstream_and_costs_later(tmp_path, fake_or):  # Review Focus 1, 2
    app, role = make_llm_app(tmp_path, fake_or)
    with serve_app(app) as url, httpx.Client() as cl:
        with cl.stream("POST", url + "/llm/v1/chat/completions", json=chat_body(text="q FAKESLOW"),
                       headers=auth(LLM_TOKEN, SES)) as r:
            next(r.iter_raw())                                                   # первый чанк — и обрыв
        wait_until(lambda: fake_or.aborted == 1, timeout=3)
        wait_until(lambda: role.ledger.recent(1)[0].cost_source == "generation")
    assert role.ledger.recent(1)[0].status == "cancelled"

def test_stopped_switch_and_missing_key(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, LLM_STATE="stopped")
    with TestClient(app) as c:
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_stopped")
        assert c.get("/healthz/llm").json() == {"ok": False, "detail": "LLM-прокси остановлен администратором"}
        assert c.get("/healthz/live").status_code == 200
    role2 = make_llm_role(tmp_path / "2", fake_or, key=None)
    assert role2.health() == (False, "нет ключа OpenRouter") and role2.health_json(now_utc())["key"] == "missing"
    assert fake_or.requests == []

def test_drill_402_text_and_no_upstream(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, DRILL_LLM="402")
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert r.status_code == 402 and r.json()["error"]["message"].endswith("(учения)")
    assert role.health_json(now_utc())["upstream"]["text"] == "на счёте OpenRouter кончились деньги (учения)"
    assert fake_or.requests == []

def test_poll_sets_usage_account_and_unreachable(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or, mgmt="test-mgmt-key")
    anyio.run(role.poll)
    j = role.health_json(now_utc())
    assert (j["upstream"]["state"], j["account_remaining_usd"], j["key_usage_usd"]) == ("ok", 30.0, 0.0)
    down = make_llm_role(tmp_path / "2", None, OPENROUTER_BASE_URL="http://127.0.0.1:9/api/v1")
    anyio.run(down.poll)
    assert down.health_json(now_utc())["upstream"]["state"] == "unreachable"

def test_provider_mismatch_counted(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKEPROVIDER"))
    assert role.health_json(now_utc())["provider_mismatch_24h"] == 1

def test_health_json_keys_and_no_secrets(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body())
        r = c.get("/health/llm")
    assert set(r.json()) == {"checked_at", "stopped", "key", "drill", "upstream", "spent_usd", "total_usd", "ops_usd",
                             "shares", "pending", "cost_unknown_24h", "provider_mismatch_24h", "key_usage_usd",
                             "account_remaining_usd", "models"}
    assert not any(s in r.text for s in (LLM_TOKEN, "test-openrouter-key", "student-01"))

def test_log_line_without_content(tmp_path, fake_or, capfd):
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="секретный вопрос"))
    err = capfd.readouterr().err
    assert "llm caller=student-01 model=z-ai/glm-5.3" in err and "cost=0.0012" in err
    assert "секретный" not in err and LLM_TOKEN not in err and "test-openrouter-key" not in err

# core/tests/test_skeleton.py (+)
def test_llm_settings_validation():
    assert Settings.from_env({"LLM_MODELS": "z-ai/glm-5.3", "DRILL_LLM": "402"}).DRILL_LLM == "402"
    for bad in ({"LLM_STATE": "off"}, {"DRILL_LLM": "403"}, {"LLM_MODELS": "openrouter/auto"},
                {"LLM_MAX_TOKENS": "32000"}, {"LLM_TOTAL_USD": "0"}, {"OPENROUTER_BASE_URL": "ftp://x"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)

def test_egress_targets_include_openrouter():
    cfg = BdrvConfig("h", 1433, "Runtime", "u", "p")
    assert egress_targets(SETTINGS, cfg) == [("h", 1433), ("openrouter.ai", 443)]
    local = replace(SETTINGS, OPENROUTER_BASE_URL="http://fake-openrouter:8080/api/v1")
    assert egress_targets(local, cfg)[1] == ("fake-openrouter", 8080)
```

`now_utc()` — помощник Д1/Д3а; `TOKEN` — токен данных Д3б.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_llm_role.py tests/test_skeleton.py`. Expected: FAIL.
- [ ] **Step 3: Implement `state.py`, `api.py`, `LlmRole`, поля `Settings`, `read_key_file`, `egress_targets`, `build_roles` по интерфейсам.**
- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add core/ && git commit -m "LLM-прокси: роль llm — отказы словами, цена из потока или /generation, опрос OpenRouter, выключатель и учение 402"`.

---

### Task 7: Сторож — вид `llm` и число в строке

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/pcbk_watchdog/page.py`,
  `watchdog/pcbk_watchdog/static/status.js`, `watchdog/pcbk_watchdog/main.py`,
  `watchdog/components.json`, `watchdog/tests/{conftest,helpers}.py`
  (`fake_core.set_llm(obj)` — ответ `/health/llm`)
- Test: `watchdog/tests/test_checks.py`, `watchdog/tests/test_page.py`,
  `watchdog/tests/test_journal.py`, `watchdog/tests/test_main.py`
  (`test_components_file_d3a` → `test_components_file_d4a`),
  `watchdog/tests/js/status.test.mjs`

**Interfaces:**
- Consumes: `Check`, `Journal`, `status_json`, `render_html`, `status.js`,
  `KINDS`, `_check_one`, `NET_CHECK_TIMEOUT_S`, `fake_core` — Д1–Д3а.
- Produces:
  - `@dataclass(frozen=True) class Check: component; title; state; detail; note: str = ""`.
    `Journal` `note` не хранит и не сравнивает.
  - `status_json` — у каждой строки ещё ключ `note`. Страница и `status.js`
    показывают `detail` и через « · » `note`, если он не пуст. Правило «detail
    совпал с подписью — не повторять» прежнее.
  - `fmt_usd(x: float) -> str`:
    - `0` → `$0.00`;
    - `0 < x < 1` → четыре знака после точки (`$0.0012`);
    - иначе два (`$12.35`).

    `fmt_total(x: float) -> str` — целое без дробной части (`$1000`).
  - `LLM_STALE_S = 180`, `ACCOUNT_WARN_USD = 20.0`, `BUDGET_STEPS = (95, 80, 50)`
  - `check_llm(component: str, title: str, url: str, now: datetime, timeout: float = NET_CHECK_TIMEOUT_S) -> Check` —
    первая подходящая строка:

| Ответ `/health/llm` | Итог |
|---|---|
| нет соединения, таймаут, HTTP ≠ 200, не JSON | `fail` «LLM-прокси не отвечает» |
| `stopped` | `fail` «LLM-прокси остановлен администратором» |
| `key == "missing"` | `fail` «нет ключа OpenRouter» |
| `spent_usd ≥ total_usd` | `fail` «бюджет {fmt_total} исчерпан — модель не отвечает» |
| `upstream.state` ∈ `unreachable`, `key_rejected`, `money_out`, `key_limit`, `region` | `fail` `upstream.text` (до 80 знаков) |
| `checked_at` — `null` | `unknown` «OpenRouter ещё не опрашивался» |
| `checked_at` старше `LLM_STALE_S` | `unknown` «LLM-прокси давно не опрашивал OpenRouter» |
| `upstream.state == "errors"` | `warn` `upstream.text` |
| `spent_usd / total_usd` больше 95, 80, 50 % (первый подходящий) | `warn` «израсходовано больше P % бюджета» |
| `provider_mismatch_24h > 0` | `warn` «за сутки были ответы не от закреплённого провайдера» |
| `cost_unknown_24h > 0` | `warn` «цена неизвестна у части вызовов за сутки» |
| `account_remaining_usd` не `null` и меньше `ACCOUNT_WARN_USD` | `warn` «на счёте OpenRouter меньше $20» |
| иначе | `ok` `upstream.text` |

  `note` при разобранном JSON — всегда:
  - «расход {fmt_usd(spent)} из {fmt_total(total)}»;
  - « · отбор и проверки {fmt_usd(ops)}» при `ops_usd > 0`;
  - « · доля исчерпана: N» при `shares.spent > 0`;
  - « · на счёте {fmt_usd(остаток)}», если остаток известен.
  - `KINDS["llm"] = ("url",)`; `_check_one` → `check_llm(cid, title, comp["url"], now)`.
  - `components.json`: `llm` →
    `{"id": "llm", "title": "LLM-прокси и бюджет", "kind": "llm", "url": "http://pcbk-core:8000/health/llm"}`;
    видов `absent` больше нет.

- [ ] **Step 1: Write the failing tests**

```python
# watchdog/tests/test_checks.py (+)
def llm_obj(**kw):
    base = {"checked_at": (T0 - timedelta(seconds=10)).isoformat(), "stopped": False, "key": "ok", "drill": "",
            "upstream": {"state": "ok", "text": "OpenRouter отвечает", "since": None, "checked_at": None},
            "spent_usd": 12.3456, "total_usd": 1000.0, "ops_usd": 0.0, "shares": {"over_80": 0, "spent": 0},
            "pending": 0, "cost_unknown_24h": 0, "provider_mismatch_24h": 0, "key_usage_usd": 12.3,
            "account_remaining_usd": None, "models": ["z-ai/glm-5.3"]}
    return {**base, **kw}

def up(state, text):
    return {"state": state, "text": text, "since": None, "checked_at": None}

def llm(fake_core, obj):
    fake_core.set_llm(obj)
    return check_llm("llm", "LLM-прокси и бюджет", fake_core.base + "/health/llm", T0, timeout=0.5)

def test_llm_verdicts(fake_core):                                                # Review Focus 5
    cases = [
        (llm_obj(), ("ok", "OpenRouter отвечает", "расход $12.35 из $1000")),
        (llm_obj(spent_usd=500.0), ("ok", "OpenRouter отвечает", "расход $500.00 из $1000")),   # ровно 50 % — ещё не «больше»
        (llm_obj(spent_usd=501.0), ("warn", "израсходовано больше 50 % бюджета", "расход $501.00 из $1000")),
        (llm_obj(spent_usd=801.0), ("warn", "израсходовано больше 80 % бюджета", "расход $801.00 из $1000")),
        (llm_obj(spent_usd=951.0), ("warn", "израсходовано больше 95 % бюджета", "расход $951.00 из $1000")),
        (llm_obj(spent_usd=1000.0), ("fail", "бюджет $1000 исчерпан — модель не отвечает", "расход $1000.00 из $1000")),
        (llm_obj(stopped=True), ("fail", "LLM-прокси остановлен администратором", "расход $12.35 из $1000")),
        (llm_obj(key="missing"), ("fail", "нет ключа OpenRouter", "расход $12.35 из $1000")),
        (llm_obj(upstream=up("money_out", "на счёте OpenRouter кончились деньги (учения)")),
         ("fail", "на счёте OpenRouter кончились деньги (учения)", "расход $12.35 из $1000")),
        (llm_obj(upstream=up("errors", "OpenRouter отвечает ошибками подряд")),
         ("warn", "OpenRouter отвечает ошибками подряд", "расход $12.35 из $1000")),
        (llm_obj(checked_at=None, upstream=up("unknown", "OpenRouter ещё не опрашивался")),
         ("unknown", "OpenRouter ещё не опрашивался", "расход $12.35 из $1000")),
        (llm_obj(provider_mismatch_24h=2),
         ("warn", "за сутки были ответы не от закреплённого провайдера", "расход $12.35 из $1000")),
        (llm_obj(spent_usd=0.0012, ops_usd=0.0012, account_remaining_usd=5.0),
         ("warn", "на счёте OpenRouter меньше $20",
          "расход $0.0012 из $1000 · отбор и проверки $0.0012 · на счёте $5.00")),
    ]
    for obj, want in cases:
        r = llm(fake_core, obj)
        assert (r.state, r.detail, r.note) == want

def test_llm_down_is_fail(fake_core):
    r = check_llm("llm", "LLM-прокси и бюджет", "http://127.0.0.1:9/health/llm", T0, timeout=0.5)
    assert (r.state, r.detail, r.note) == ("fail", "LLM-прокси не отвечает", "")

# watchdog/tests/test_journal.py (+)
def test_note_does_not_make_transition(tmp_path):                                # Review Focus 5
    j = Journal(str(tmp_path / "j.db"))
    j.record([Check("llm", "L", "warn", "израсходовано больше 50 % бюджета", note="расход $500.00 из $1000")], T0)
    assert j.record([Check("llm", "L", "warn", "израсходовано больше 50 % бюджета", note="расход $501.00 из $1000")],
                    T0 + timedelta(seconds=10)) == []

# watchdog/tests/test_page.py (+)
def test_note_in_json_and_html():
    s = Snapshot(T0, (Check("llm", "LLM-прокси и бюджет", "ok", "OpenRouter отвечает", note="расход $1.00 из $1000"),))
    assert status_json(s, T0, 30)["checks"][0]["note"] == "расход $1.00 из $1000"
    assert "OpenRouter отвечает · расход $1.00 из $1000" in render_html(s, [], T0, 30, ZoneInfo("UTC"))

# watchdog/tests/test_main.py
def test_components_file_d4a():
    comps = {c["id"]: c for c in load_components("components.json")}
    assert (comps["llm"]["kind"], comps["llm"]["title"], comps["llm"]["url"]) == \
           ("llm", "LLM-прокси и бюджет", "http://pcbk-core:8000/health/llm")
    assert not [c for c in comps.values() if c["kind"] == "absent"]
```

```js
// watchdog/tests/js/status.test.mjs (+)
test('note rendered after detail', ...)   // ответ со строкой {detail: 'OpenRouter отвечает', note: 'расход $1.00 из $1000'}
                                          // → в .detail текст 'OpenRouter отвечает · расход $1.00 из $1000'
```

- [ ] **Step 2: Run tests to verify they fail** — `cd watchdog && uv run --python 3.12 --with pytest pytest -q; node --test tests/js/`. Expected: FAIL — нет `check_llm`, нет `note`.
- [ ] **Step 3: Implement по интерфейсам и таблице** (`http.client`, без перенаправлений и прокси, как `check_historian`).
- [ ] **Step 4: Run all watchdog tests** — та же команда. Expected: PASS.
- [ ] **Step 5: Commit** — `git add watchdog/ && git commit -m "Сторож: строка «LLM-прокси и бюджет» — пороги 50/80/95 % постоянным текстом, расход в note"`.

---

### Task 8: Компоновка и интеграционные тесты

**Files:**
- Modify: `compose.yaml`, `compose.test.yaml`, `deploy/images.lock`
  (`python:3.12-slim sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f # test`),
  `deploy/env.example` (`LLM_MODELS=`, `LLM_STATE=`, `DRILL_LLM=`, `LLM_OPS_SHARE_USD=` — пусто),
  `.gitignore` (`llm-tokens`), `tests/integration/conftest.py`,
  `tests/integration/test_edge.py`, `tests/integration/test_core.py`,
  `tests/integration/test_socket_proxy.py`
- Test: `tests/integration/test_llm.py`

**Interfaces:**
- Consumes: образы задач 2–7; класс `Stack` и его методы Д1–Д3б (`compose`,
  `inspect`, `network`, `prod_config`, `http_host(method, url, body=None, token=None)`,
  `logs`, `wait_status`, `ready`, `start`, `stop`, `secrets_dir`, `llm_token(n)`,
  `core_token`, `STACK_VARS`).
- Produces (`compose.yaml`, служба `core` — правки к Д3б):

```yaml
  core:
    image: pcbk-reserve/core:d4a
    mem_limit: 512m                 # тела запросов к модели — до 4 МиБ, разбор JSON от десяти мест
    environment:                    # к переменным Д3а/Д3б
      LLM_MODELS: ${LLM_MODELS:-}
      LLM_STATE: ${LLM_STATE:-on}
      DRILL_LLM: ${DRILL_LLM:-}
      LLM_OPS_SHARE_USD: ${LLM_OPS_SHARE_USD:-100}
    secrets:                        # к bdrv-env и core-tokens Д3а/Д3б
      - {source: llm-tokens, target: llm-tokens}
      - {source: openrouter-key, target: openrouter.key}
      - {source: openrouter-mgmt-key, target: openrouter-mgmt.key}
# secrets:
  llm-tokens: {file: "${SECRETS_DIR:?SECRETS_DIR не задан}/llm-tokens"}
  openrouter-key: {file: "${SECRETS_DIR:?SECRETS_DIR не задан}/openrouter.key"}
  openrouter-mgmt-key: {file: "${SECRETS_DIR:?SECRETS_DIR не задан}/openrouter-mgmt.key"}
```

  сторож — `image: pcbk-reserve/watchdog:d4a`.
- Produces (`compose.test.yaml`):
  - служба `fake-openrouter`:
    - `image: python:3.12-slim`, `pull_policy: never`,
      `container_name: pcbk-test-openrouter`;
    - `command: ["python", "/app/fake_openrouter.py"]`;
    - `user: "65534:65534"`, `read_only: true`, `cap_drop: [ALL]`,
      `no-new-privileges`;
    - bind `./core/tests/fake_openrouter.py` → `/app/fake_openrouter.py:ro`;
    - `networks: {pcbk-egress: {ipv4_address: 172.31.250.84}}`;
  - у `core`: `OPENROUTER_BASE_URL: http://fake-openrouter:8080/api/v1`,
    `LLM_POLL_S: "5"`.
- Produces (`conftest.py`):
  - фикстура пишет в тестовый `SECRETS_DIR`:
    - `openrouter.key` = `test-openrouter-key`;
    - `openrouter-mgmt.key` = `test-mgmt-key`;
    - `ops.llm-token` = `test-llm-token-ops`;
    - `llm-tokens` — хеши `student-01…10.llm-token` и `ops`.

    Файлы `0444`, как на сервере;
  - `STACK_VARS` дополняется `LLM_MODELS`, `LLM_STATE`, `DRILL_LLM`,
    `LLM_OPS_SHARE_USD`;
  - `stack.ops_llm_token: str`;
  - `stack.recreate(service: str, overrides: Mapping[str, str]) -> None` —
    `compose up -d --no-build <service>` с окружением `{**self.env, **overrides}`
    (окружение оболочки сильнее `--env-file`), затем `wait_ready`;
  - `stack.fake_requests() -> list[dict]` — `GET http://172.31.250.84:8080/_requests` с хоста;
  - `stack.llm_calls(last: int = 5) -> list[dict]` —
    `docker exec pcbk-core python -c …` печатает JSON строк `llm_calls`
    (столбцы задачи 4).
- `test_edge.py`: `DECLARED_ENV["pcbk-core"]` + `LLM_MODELS`, `LLM_STATE`,
  `DRILL_LLM`, `LLM_OPS_SHARE_USD`, `OPENROUTER_BASE_URL`, `LLM_POLL_S`
  (последние два — только тестовый стенд, комментарий); `IMAGES`: `core` и
  сторож `:d4a`.
- `test_core.py`:
  - «охрана выхода: разрешено 1 направление» → «разрешено 2 направления»;
  - `test_egress_network_only_core` проверяет «в `pcbk-egress` только `core`»
    по `prod_config()`, на живом тестовом стенде там ещё двойник.
- `test_socket_proxy.py`: в `test_status_json_overall_ok` строк `absent` нет;
  строка `llm` — `ok`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_llm.py
CORE = "http://172.31.250.82:8000"
CHAT = {"model": "z-ai/glm-5.3", "stream": True, "messages": [{"role": "user", "content": "вопрос"}]}

def row(data, cid):
    return next((c["state"], c["detail"], c.get("note", "")) for c in data["checks"] if c["component"] == cid)

def chat(stack, token, body=CHAT):
    code, text = stack.http_host("POST", CORE + "/llm/v1/chat/completions", body, token=token)
    return code, text

def test_llm_call_counted_on_status_page(stack):
    code, text = chat(stack, stack.llm_token(1))
    assert code == 200 and text.rstrip().endswith("data: [DONE]")
    data = stack.wait_status(lambda d: row(d, "llm")[2].startswith("расход $0.00"), timeout=40)
    assert row(data, "llm")[:2] == ("ok", "OpenRouter отвечает") and "$1000" in row(data, "llm")[2]
    sent = stack.fake_requests()[-1]["body"]
    assert sent["provider"]["zdr"] is True and sent["user"] == "pcbk-student-01"
    assert stack.llm_calls(1)[0]["cost_source"] == "stream"

def test_llm_refusals_without_upstream(stack):
    before = len(stack.fake_requests())
    assert chat(stack, None)[0] == 401
    assert chat(stack, stack.core_token)[0] == 401                                  # токен данных
    assert chat(stack, stack.ops_llm_token, {**CHAT, "model": "openrouter/auto"})[0] == 403
    assert len(stack.fake_requests()) == before

def test_stop_switch_keeps_data_row_green(stack):                                   # Д4а-R15
    stack.recreate("core", {"LLM_STATE": "stopped"})
    try:
        data = stack.wait_status(lambda d: row(d, "llm")[0] == "fail" and row(d, "core")[0] == "ok", timeout=90)
        assert row(data, "llm")[1] == "LLM-прокси остановлен администратором"
        code, text = chat(stack, stack.llm_token(1))
        assert code == 403 and "остановлен" in json.loads(text)["error"]["message"]
    finally:
        stack.recreate("core", {})

def test_drill_402_row_and_upstream_untouched(stack):
    stack.recreate("core", {"DRILL_LLM": "402"})
    try:
        before = len(stack.fake_requests())
        code, text = chat(stack, stack.ops_llm_token)
        assert code == 402 and json.loads(text)["error"]["message"].endswith("(учения)")
        data = stack.wait_status(lambda d: row(d, "llm")[0] == "fail", timeout=60)
        assert row(data, "llm")[1] == "на счёте OpenRouter кончились деньги (учения)"
        assert len(stack.fake_requests()) == before                                 # ни одного запроса к двойнику
    finally:
        stack.recreate("core", {})

def test_core_healthy_without_key(stack):
    key = stack.secrets_dir / "openrouter.key"
    saved = key.read_text()
    key.chmod(0o644); key.write_text(""); key.chmod(0o444)
    stack.recreate("core", {})
    try:
        assert stack.inspect("pcbk-core")["State"]["Health"]["Status"] in ("starting", "healthy")
        stack.wait_healthy("pcbk-core")
        data = stack.wait_status(lambda d: row(d, "llm")[0] == "fail", timeout=60)
        assert row(data, "llm")[1] == "нет ключа OpenRouter" and row(data, "core")[0] == "ok"
    finally:
        key.chmod(0o644); key.write_text(saved); key.chmod(0o444)
        stack.recreate("core", {})

def test_keys_and_tokens_are_files_not_env(stack):
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    for dest in ("/run/secrets/openrouter.key", "/run/secrets/openrouter-mgmt.key", "/run/secrets/llm-tokens"):
        assert (mounts[dest]["Type"], mounts[dest]["RW"]) == ("bind", False), dest
    dump = json.dumps(stack.inspect("pcbk-core"))
    assert "test-openrouter-key" not in dump and "test-mgmt-key" not in dump
    assert "test-openrouter-key" not in stack.logs("pcbk-core")
```

`stack.wait_healthy` — метод Д2 (для `core` — `State.Health.Status == "healthy"`).

- [ ] **Step 2: Run tests to verify they fail** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_llm.py`. Expected: FAIL — нет двойника, секретов и роли в образе.
- [ ] **Step 3: Implement правки компоновки, тестовые секреты, методы `stack`, правки старых тестов.**
- [ ] **Step 4: Run the whole local suite** — `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/) && uv run --python 3.12 --with pytest pytest -q tests/integration && docker network ls --filter name=pcbk- -q | wc -l`. Expected: PASS; сетей `pcbk-` после прогона — 0.
- [ ] **Step 5: Commit** — `git add compose.yaml compose.test.yaml deploy/ .gitignore tests/integration/ && git commit -m "LLM-прокси в компоновке: ключи и токены файлами, двойник OpenRouter в тестах, учения на локальном стенде"`.

---

### Task 9: Выкладка

Шаги — команды и вывод, который значит «прошло». `sudo` не нужен. Итоги — в
`docs/checks/D4a.md`, только вердикты и числа. **Шаг 1 — окно владельца**;
после него идёт задача 1, затем шаги 2–5.

**Files:**
- Modify: `deploy/README.md` — раздел «LLM-прокси»:
  - ключ и ключ управления: кто кладёт, права, смена ключа (файл и
    `docker compose up -d --no-build core`);
  - `llm-tokens`: как пересобрать из `*.llm-token`, новый студент;
  - выключатель `LLM_STATE=stopped` и учение `DRILL_LLM=402`;
  - `LLM_MODELS` после выбора модели;
  - откат.
- Modify: `docs/checks/D4a.md`

- [ ] **Step 1: Ключи (владелец, на сервере)**

```bash
cd /opt/pcbk-reserve/secrets && umask 077
read -rs k && printf '%s' "$k" > openrouter.key && unset k && chmod 0444 openrouter.key   # ввод не виден
[ -e openrouter-mgmt.key ] || { : > openrouter-mgmt.key; chmod 0444 openrouter-mgmt.key; }  # или так же, как ключ
stat -c '%a %s %n' openrouter.key openrouter-mgmt.key; grep -c '^sk-or-' openrouter.key
```

Expected: `444` у обоих файлов, размер ключа больше 0; `1`. Содержимое на
экран не выводится.

- [ ] **Step 2: Токены LLM (на сервере)**

```bash
cd /opt/pcbk-reserve/secrets && umask 077
[ -e ops.llm-token ] || { openssl rand -hex 24 | tr -d '\n' > ops.llm-token; chmod 0400 ops.llm-token; }
for f in student-*.llm-token ops.llm-token; do printf '%s %s\n' "${f%.llm-token}" "$(sha256sum < "$f" | cut -c1-64)"; done > llm-tokens.new
chmod 0444 llm-tokens.new && mv llm-tokens.new llm-tokens && wc -l < llm-tokens && stat -c '%a' llm-tokens ops.llm-token
```

Expected: `11`; `444`, `400`. Токен `ops` — в `$JOB` без вывода на экран:
`$SSH 'cat /opt/pcbk-reserve/secrets/ops.llm-token' > "$JOB/ops-llm.token"`,
затем
`printf 'Authorization: Bearer %s\n' "$(cat "$JOB/ops-llm.token")" > "$JOB/ops-llm.hdr"; chmod 600 "$JOB"/ops-llm.*`.
Заголовок токена данных `ops` (`$JOB/ops.hdr`) закрытие Д3б удалило — он
создаётся заново способом Д3б (задача 7, шаг 6) из `ops.data-token`.

- [ ] **Step 3: Образы.** Локально `docker compose build core watchdog`
  (теги `:d4a`), `docker save pcbk-reserve/core:d4a pcbk-reserve/watchdog:d4a | gzip | $SSH 'gunzip | docker load'`.
  Сверка `RootFS` на обеих сторонах. Expected: совпало; `:d3b`/`:d3a` на
  сервере остались.
- [ ] **Step 4: Поднять.** На сервере `cp -p compose.yaml compose.yaml.d3b`;
  локально `rsync -a compose.yaml …:/opt/pcbk-reserve/`. Секреты шагов 1–2 уже
  на месте: без них `docker compose` откажет по `secrets`. Затем
  `docker compose up -d --no-build core` и `docker compose up -d --no-build watchdog`.
  Expected — не позже 2 минут:
  - `pcbk-core` — `healthy`;
  - в журнале `core` — «LLM-прокси: моделей 7, доля $100, бюджет $1000» и
    «охрана выхода: разрешено 2 направления»;
  - строка «LLM-прокси и бюджет — норма — OpenRouter отвечает · расход $0.00
    из $1000» (разбор `status.json` через `python3`); «Служба данных» и
    «Историан» в норме;
  - `docker inspect pcbk-core | grep -c -F -f secrets/openrouter.key` → `0`;
  - память `core` меньше 70 % от 512 МиБ;
  - время работы контейнеров Dify и мест не сброшено.

  Откат:
  - вернуть `compose.yaml.d3b`;
  - `docker compose up -d --no-build core watchdog` — образы `:d3b` и `:d3a`;
  - секреты и журнал вызовов не удаляются.
- [ ] **Step 5: Commit** (после проверки на секреты) — `git add deploy/README.md docs/checks/D4a.md && git commit -m "Выкладка Д4а: LLM-прокси на сервере, ключ файлом у core, строка расхода на странице"`.

---

### Task 10: Живые проверки и учения

Итог каждого шага — вердиктом [П] в `docs/checks/D4a.md`. Туннель —
`$SSH -N -L 18000:172.31.250.82:8000` (как в Д3б). Снимки — способом Д3а, в
`docs/checks/D4a/`. Строки журнала вызовов печатает помощник `llmrows N` в
`~/pcbk-d4/` на сервере: `docker exec pcbk-core python -c …` выводит JSON
столбцов задачи 4. Все вызовы — токеном `ops`.

- [ ] **Step 1: §11 п. 4 через прокси (поток).**
  `curl -sN -H @"$JOB/ops-llm.hdr" -H 'Content-Type: application/json' -H 'X-Session-Id: ses_AAAAAAAAAAAAAAAAAAAAAAAAAA' --data @"$JOB/luna.json" http://127.0.0.1:18000/llm/v1/chat/completions`.
  В `luna.json` — модель `openai/gpt-6-luna`, `stream: true`, `max_tokens: 64`,
  «Ответь одним словом: да».
  Expected:
  - поток до `data: [DONE]`;
  - `llmrows 1`: `status ok`, `cost_source stream`, `cost > 0`,
    `provider_ok 1`, `step 1`, `session ses_AAA…`;
  - на странице — «расход $0.0… из $1000 · отбор и проверки $0.0…»; снимок
    `01-spend.png`.
- [ ] **Step 2: Без потока и обрыв.** Тот же запрос с `"stream": false` →
  `cost_source body`. Затем запрос на 300 слов (`max_tokens: 400`),
  `curl --max-time 2` → `status cancelled`. Не позже 60 с его `cost_source` становится `generation`
  — задержка записывается. Потрачено меньше $0,01.
- [ ] **Step 3: Отказы без денег.**
  - без токена → 401;
  - токен данных `ops` Д3б → 401;
  - `z-ai/glm-5.3:nitro` → 403 `pcbk_model_refused`;
  - `openrouter/auto` → 403.

  В `llmrows` — строки `refused`; `key_usage_usd` на `/health/llm` не
  вырос.
- [ ] **Step 4: Учение «LLM-прокси остановлен».**
  `LLM_STATE=stopped docker compose up -d --no-build core`, время `t0`.
  Expected:
  - не позже `t0` + 60 с — «LLM-прокси и бюджет — сбой — LLM-прокси
    остановлен администратором»;
  - «Служба данных — жива», историан в норме (после загрузки каталога);
  - вызов через туннель → 403 с текстом «LLM-прокси остановлен…»;
  - снимок `02-llm-stopped.png`.

  Затем `docker compose up -d --no-build core` → строка в норме, снимок
  `03-llm-back.png`.
- [ ] **Step 5: Учение «402 от OpenRouter» (заглушкой).**
  1. Записать `key_usage_usd`.
  2. `DRILL_LLM=402 docker compose up -d --no-build core`.
  3. Вызов через туннель.

  Expected:
  - 402, текст «На счёте OpenRouter кончились деньги… (учения)»;
  - в журнале `core` — «УЧЕНИЯ: DRILL_LLM=402»;
  - строка «сбой — на счёте OpenRouter кончились деньги (учения)», снимок
    `04-drill-402.png`;
  - через 2 опроса `key_usage_usd` тот же — денег не потрачено.

  Затем `docker compose up -d --no-build core` → норма, снимок
  `05-recovered.png`. В журнале сторожа — все переходы.
- [ ] **Step 6: Уборка и итог.**
  - в `$JOB` удалить `luna.json` и прочие запросы;
  - помощник `llmrows` в `~/pcbk-d4/` остаётся до закрытия Д4б;
  - строка «потрачено за Д4а: $X» по `/health/llm` (`ops_usd`).

  Expected: не больше $0,05.
- [ ] **Step 7: Commit** (после проверки на секреты) — `git add docs/checks/ && git commit -m "Д4а: цена в потоке через прокси, два учения со снимками, денег на учения не потрачено"`.

---

### Task 11: Закрытие дня

- [ ] **Step 1: Документы.**
  - `docs/DESIGN-platform-2026-09-29.md`:
    - §5 — как построено: токены, список моделей, пересборка тела, доли,
      отказы, источники цены;
    - §11 п. 4 — [П] по итогам задач 1 и 10: «через прокси»; «через OpenCode»
      — Д4б;
    - §13 — п. «Д4а» (решения Д4а-R11…R17).
  - `docs/PLAN-platform-2026-09-29.md`:
    - строка Д4 делится на Д4а и Д4б (если не внесено вместе с планом),
      «14 дней»;
    - в «Отклонениях» — строка о разрезе Д4;
    - таблица владельца: Д4а — ключ; Д4б — окно около 2 ч (чтение
      производственных данных и меньше $1 на проверку совместимости).
  - `README.md` — «Д4а готов», дальше Д4б.
- [ ] **Step 2: Критик** (Opus 5.5). Блокер — пункт «Блокер дня» дорожной
  карты. Петля — до нуля блокеров, не больше двух раундов; третий — только
  после разговора с владельцем.
- [ ] **Step 3: Слияние.** Ветку — в `main`, тег `platform-d4a`. Перед пушем:
  - `git log -p main..HEAD -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто;
  - `git ls-files | grep -cE '(llm-tokens|core-tokens|\.(llm|data)-token|openrouter.*\.key)$'` → `0`.

  Затем `git push origin main platform-d4a`. Чистый клон: снимки на месте,
  `(cd core && CORE_PYTEST)` проходит. Файлы `"$JOB"/ops-llm.*` и
  `"$JOB"/ops.hdr` остаются для Д4б, их удаляет его закрытие.
- [ ] **Step 4: План и факт.** Строка в `docs/checks/D4a.md`: «план 11,5 ч /
  факт Y ч по git». Y — от первого коммита ветки дня до тега:
  `git log --reverse --format=%cI platform-d3b..platform-d4a | sed -n '1p;$p'`.
- [ ] **Step 5: Владельцу** — «Д4а готов», снимки, сколько потрачено. Вопросы —
  только требующие его решения: итог сырого потока, если он плохой; ключ
  управления, если не дан, а остаток счёта на странице нужен.
