# Точный белый список шлюза (Д5) и подключение веба (Д6)

29.09.2026 · агент на Opus 5.5 · только чтение репозитория; исходники и пакеты
— во временном каталоге; опыты — на машине разработчика, не на сервере ·
основание для Д5 и Д6 [`PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md),
§7 и §9 [`DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md),
продолжение [`02-web-chat-opencode.md`](02-web-chat-opencode.md) и §3
[`04-d1-facts.md`](04-d1-facts.md).

**Пометки:** **[Д]** — по исходникам на закреплённой версии или по
документации; **[Л]** — опыт: локально, под runc, без gVisor и без нашего
входного прокси; **[?]** — не подтверждено.

**Как ставились опыты [Л].** Бинарник OpenCode 1.18.33 из выпуска (sha256
`e5461232…` — тот же, что в `04-d1-facts.md` §6), `opencode serve` на
127.0.0.1 с паролем Basic, отдельные `HOME` и `XDG_*`,
`OPENCODE_DISABLE_PROJECT_CONFIG=1`; модель — поддельный OpenAI-совместимый
сервер на Python (текст, вызов инструмента `question` или `read`). Поверх —
черновой шлюз на FastAPI с белым списком из §5 и SDK 1.18.33 в Node с классом
`OpenCodeEventSource` из адаптера. Всё во временном каталоге, в репозиторий
ничего не попало.

**Сокращения путей в ссылках:**

* `A:` — пакет npm `@assistant-ui/react-opencode@0.2.25`, каталог `src/`
  (исходники лежат в самом пакете; `dist/` делает те же 18 вызовов — сверено);
* `S:` — пакет npm `@opencode-ai/sdk@1.18.33`, каталог `dist/v2/`;
* `O:` — [anomalyco/opencode@v1.18.33](https://github.com/anomalyco/opencode/tree/v1.18.33)
  (коммит `51ef4be1d3c1…`), каталог `packages/opencode/src/`;
* `H:` — `O:server/routes/instance/httpapi/`.

## Главное

1. **Адаптер делает 18 HTTP-вызовов и один поток SSE `/event`.** Ни оболочки,
   ни файлов, ни конфигурации среди них нет [Д, Л]. Для пилота нужны 13 из
   них; `summarize` шлюз не пересылает, а `delete`, `revert`, `unrevert`,
   `fork` в пилоте закрыты (§5).
2. **Вырезать поля мало — тело надо собирать заново.** OpenCode молча
   игнорирует лишние поля, а опасные принимает. Опыт: даже при
   `external_directory: deny` в конфигурации поле `tools` в `prompt_async`
   или `permission` в `POST /session` снимает запрет. После этого агент читает
   `/proc/self/environ`, и **пароль сервера OpenCode оказывается в истории
   разговора** [Л].
3. **Часть `file` с адресом `file://` читает любой файл места без спроса.**
   Так читается `/proc/self/environ` с паролем [Л], тем же путём — файлы
   секретов `/run/secrets/*` [Д]. Шлюз пропускает только текстовые части.
4. **В 1.18.33 работает ещё одна поверхность — 51 ручка `/api/*`.** Среди
   них `/api/fs/read/*` отдаёт файл `/etc/hostname` [Д, Л]. Поэтому шлюз
   сверяет путь **после префикса** с точным списком, а не отсекает «опасное».
5. **Префикс `/api/oc/` работает** (`baseUrl` с путём, косая черта в конце
   срезается). **Basic в браузере не нужен:** SDK сам заголовок
   `Authorization` не ставит [Л]. Пароль знает только шлюз. CORS не нужен —
   веб и шлюз на одном origin.
6. **При ошибке `/event` адаптер переподключается раз в секунду бесконечно.**
   После каждого переподключения он делает пять GET на каждый открытый
   разговор [Д, Л]. Отсюда три требования: входной прокси не рвёт поток (сейчас
   у него `proxy_read_timeout 5s`), веб сам ловит 401, место не открывается
   до готовности.
7. **С настройками по умолчанию студент может одобрить агенту чтение файла
   вне `/work`.** Так пароль тоже попадает в разговор [Л]. Лекарство —
   `external_directory: deny` в конфигурации образа [Л] плюс политика ответов
   на разрешения в шлюзе (§7).

## 1. Версии — закрепить точно

| Пакет | Версия | Сумма в npm | Замечание |
|---|---|---|---|
| `@assistant-ui/react-opencode` | **0.2.25** (24.09.2026, `latest`) | `sha512-MPP7w+MbTjFKSZX2aoNd8wK/6WXIEuU/w2qc5jy+/Vx249lQZ+mwzEZ6mPoYJbVr8VHA4x8yuXG30vZaadSx1w==` | MIT, «experimental»; зависит от `@opencode-ai/sdk ^1.18.31` [Д: `npm view`] |
| `@opencode-ai/sdk` | **1.18.33** (`latest`) | `sha512-Nyurky9+AA2tvZ6my8UtO5pxPoXxrNypqjeEqMshBGiR542glOy+KE1Y+3lwWU+Z6HC/hSmrdXNbX81+qiuTzg==` | закрепить через `overrides`: `^1.18.31` подтянет 1.18.34+, а сервер остаётся 1.18.33 |
| `@assistant-ui/react` | 0.15.22 | — | обязательная парная зависимость `^0.15.0` |
| `@assistant-ui/core` / `store` | 0.3.21 / 0.3.15 | — | тянутся адаптером |
| `assistant-cloud` | 0.2.3 | — | в адаптере указан как `"*"`; держать в lock-файле |
| `react`, `react-dom` | 19.3.0 | — | |

* Проба установки [Л]: `overrides: {"@opencode-ai/sdk": "1.18.33"}` плюс
  `--save-exact` → в дереве ровно одна `@opencode-ai/sdk@1.18.33`,
  103 пакета, 52 МБ `node_modules`.
* **Путаница имён.** Адаптер импортирует `@opencode-ai/sdk/v2/client`
  (`A:useOpenCodeRuntime.ts:20`, `A:OpenCodeThreadController.ts:7`). Это
  новый сгенерированный клиент **внутри SDK 1.18.x**, а не сервер OpenCode
  v2 (тот ставится пакетом `@opencode/sdk`). Ручки v1 он вызывает те же
  [Д, Л].
* Внешние адреса в коде assistant-ui (`backend.assistant-api.com`) нужны
  только облачному режиму `cloud`; мы его не включаем. CSP `connect-src
  'self'` их всё равно закроет [Д].

## 2. Все вызовы адаптера к OpenCode

Запросы сняты подменным `fetch` с SDK 1.18.33 на тех же аргументах, что у
адаптера [Л]. Заголовки: `Content-Type: application/json` — только если есть
тело; ни `Authorization`, ни `x-opencode-*`, ни `Accept` у потока нет.

| # | Вызов SDK | HTTP | Запрос | Тело от адаптера | Где в адаптере | Когда |
|---|---|---|---|---|---|---|
| 1 | `experimental.session.list` | `GET /experimental/session` | `roots=true&archived=true` | — | `A:openCodeThreadListAdapter.ts:41–46` | список разговоров |
| 2 | `session.create` | `POST /session` | — | без тела | `…ThreadListAdapter.ts:27` | новый разговор (первое сообщение) |
| 3 | `session.update` | `PATCH /session/{ses}` | — | `{"title"}` / `{"time":{"archived":N}}` / `{"time":{"archived":null}}` | `…ThreadListAdapter.ts:60, 69, 78` | переименовать, в архив, из архива |
| 4 | `session.delete` | `DELETE /session/{ses}` | — | — | `…ThreadListAdapter.ts:89`; `A:useOpenCodeRuntime.ts:414` (облако) | удалить разговор |
| 5 | `session.summarize` | `POST /session/{ses}/summarize` | — | без тела | `…ThreadListAdapter.ts:98` | авто-заголовок после первого ответа |
| 6 | `session.get` | `GET /session/{ses}` | — | — | `…ThreadListAdapter.ts:113`; `A:OpenCodeThreadController.ts:677` | открыть разговор |
| 7 | `session.messages` | `GET /session/{ses}/message` | — | — | `A:OpenCodeThreadController.ts:681` | история; повторно после переподключения |
| 8 | `session.promptAsync` | `POST /session/{ses}/prompt_async` | — | `{"parts":[…], "model"?, "agent"?}` | `…Controller.ts:756–768`; `model` и `agent` из `defaultModel`, `defaultAgent` (`A:useOpenCodeRuntime.ts:136–139`) | отправить сообщение |
| 9 | `session.abort` | `POST /session/{ses}/abort` | — | — | `…Controller.ts:835` | «стоп» |
| 10 | `session.revert` | `POST /session/{ses}/revert` | — | `{"messageID"}` | `…Controller.ts:854`; вызывается из `onReload` (`A:useOpenCodeRuntime.ts:230–242`) | правка и повтор |
| 11 | `session.unrevert` | `POST /session/{ses}/unrevert` | — | — | `…Controller.ts:868` | отменить откат |
| 12 | `session.fork` | `POST /session/{ses}/fork` | — | `{"messageID"}` | `…Controller.ts:877` | ветка разговора (по вызову из интерфейса) |
| 13 | `session.status` | `GET /session/status` | — | — | `…Controller.ts:580` | после переподключения потока |
| 14 | `permission.list` | `GET /permission` | — | — | `…Controller.ts:598` | после переподключения |
| 15 | `permission.reply` | `POST /permission/{per}/reply` | — | `{"reply":"once"|"always"|"reject"}` | `…Controller.ts:894` | ответ на разрешение |
| 16 | `question.list` | `GET /question` | — | — | `…Controller.ts:613` | после переподключения |
| 17 | `question.reply` | `POST /question/{que}/reply` | — | `{"answers":[["…"]]}` | `…Controller.ts:913` | ответ на вопрос агента |
| 18 | `question.reject` | `POST /question/{que}/reject` | — | — | `…Controller.ts:929` | отказ отвечать |
| П | `event.subscribe` | `GET /event` (SSE) | — | — | `A:OpenCodeEventSource.ts:165–168` | всё время, пока открыт разговор |

* Пути и способ передачи каждого параметра взяты из генератора SDK [Д]:
  `S:gen/sdk.gen.js` — `experimental.session.list` 260–280, `event.subscribe`
  644–658, `question.*` 1717–1779, `permission.list/reply` 1787–1829,
  `session.create` 1997–2023, `status` 2029–2043, `delete` 2049–2064, `get`
  2070–2085, `update` 2091–2115, `messages` 2185–2202, `fork` 2287–2308,
  `abort` 2314–2329, `summarize` 2406–2429, `promptAsync` 2435–2464, `revert`
  2533–2555, `unrevert` 2561–2576.
* **Кроме того, что шлёт адаптер, SDK умеет подставить в каждый вызов**
  `?directory=` и `?workspace=`. Если клиенту задать `directory`, он добавит
  заголовок `x-opencode-directory` и для GET перенесёт его в
  `?directory=` (`S:client.js:17–42, 55–66`). Веб их не задаёт, а шлюз
  всё равно выбрасывает.
* Части сообщения у адаптера бывают `text` и `file` (картинки тоже уходят как
  `file` с адресом `data:`). Других частей адаптер не шлёт
  (`A:OpenCodeThreadController.ts:71–114`).

## 3. Поток событий

* Адаптер слушает **`/event`**, не `/global/event` (`A:OpenCodeEventSource.ts:165`,
  `S:gen/sdk.gen.js:654`) [Д].
* Формат [Д, Л]: каждое событие — `data: {"id","type","properties"}`, строк
  `id:` нет, так что `Last-Event-ID` ничего не даёт и пропущенное после
  переподключения не досылается. Первым приходит `server.connected`, каждые
  10 с — `server.heartbeat`. Поток **кончается сам** событием
  `server.instance.disposed` после `POST /instance/dispose`. Заголовки ответа:
  `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`
  (`H:handlers/event.ts:34–84`). После dispose поток закрылся за секунды [Л].
* OpenCode отдаёт в поток только события экземпляра с тем же каталогом
  (`H:handlers/event.ts:34–41`). Разговоры, заведённые с чужим
  `?directory=`, в поток не попадут.
* **Типы, которые разбирает адаптер** [Д: `A:OpenCodeThreadController.ts:969–1160`,
  `A:OpenCodeEventSource.ts:26–33`]: `session.created`, `session.updated`,
  `session.deleted`, `session.status`, `session.idle`, `session.compacted`,
  `session.error`, `message.updated`, `message.removed`,
  `message.part.updated`, `message.part.delta`, `message.part.removed`,
  `permission.asked`, `permission.replied`, `question.asked`,
  `question.replied`, `question.rejected`. Прочие события с `sessionID` адаптер
  копит в `unhandledEvents` и ни на что не использует.
* **Что пришло вживую** за ход с текстом, вопросом и разрешением [Л]: к списку
  выше добавились `plugin.added` (90 шт.), `catalog.updated`,
  `integration.updated`, `reference.updated`, `session.diff`. Для чата это
  шум.
* **Переподключение** [Д: `A:OpenCodeEventSource.ts:155–216`; Л]. Задержка
  1 с, без роста и без предела. Замер через подменный `fetch`: при ответе 503
  или 401 — 10 запросов `/event` за 10 с и 9 событий `stream.reconnected`.
  На каждое такое событие каждый открытый разговор запрашивает `GET
  /session/{ses}`, `…/message`, `/session/status`, `/permission`, `/question`
  (`A:OpenCodeThreadController.ts:571–627`).

## 4. Сверка с сервером 1.18.33: что сервер принимает сверх нужного

Все 18 вызовов есть на сервере: `H:groups/session.ts:78–105`,
`H:groups/permission.ts:21–43`, `H:groups/question.ts`,
`H:groups/event.ts:7–29`, `H:groups/experimental.ts:99` [Д]. Опасно то, что
**тела и параметры шире, чем нужно адаптеру**:

| Где | Что принимает сервер | К чему ведёт | Источник |
|---|---|---|---|
| все ручки | `?directory=`, `?workspace=`, заголовок `x-opencode-directory` | каталог экземпляра выбирает клиент; новый каталог — новый экземпляр | `H:middleware/workspace-routing.ts:22–25, 69–72, 86–88` [Д]; `POST /session?directory=…` и с заголовком дали сессию в чужом каталоге [Л] |
| все ручки | `?auth_token=base64(user:pass)` | **главнее заголовка**: с верным Basic и неверным `auth_token` ответ — 401 | `H:middleware/authorization.ts:77–83` [Д, Л] |
| `POST /session` | `parentID`, `title`, `agent`, `model`, `metadata`, `permission`, `workspaceID` | `permission` — правила сессии; `parentID` прячет разговор из списка | `O:session/session.ts:260–270` [Д]; сессия с `permission:[*:allow]` создана [Л] |
| `PATCH /session/{ses}` | `title`, `metadata`, `permission`, `time.archived` | `permission` **дописывается** к правилам сессии | `H:groups/session.ts:49–58`, `H:handlers/session.ts:194–199` [Д, Л] |
| `prompt_async` | `messageID`, `model`, `agent`, `noReply`, `tools`, `format`, `system`, `variant`, `parts` | `tools` становится правилами сессии allow/deny; `system` — свой системный текст; `model`, `variant` — модель и режим; `messageID` — свой id сообщения | `O:session/prompt.ts:1499–1520, 661–669, 1060–1067` [Д]; `tools` записан в правила [Л] |
| `prompt_async`, части | `text`, `file`, `agent`, `subtask` | `file` с `file://` читает файл в обход проверки каталога (`bypassCwdCheck`); `file` с `source.type=resource` читает ресурс MCP; `subtask` запускает любого агента с любой моделью; `agent` велит вызвать подагента | `O:session/prompt.ts:703–715, 808–970, 974–990` [Д]; `file:///proc/self/environ` → пароль в истории [Л] |
| `summarize` | обязательные `providerID`, `modelID` | это **сжатие разговора моделью** (платно, переписывает контекст), а не заголовок | `H:groups/session.ts:65–69`, `H:handlers/session.ts:273–293` [Д]; без тела — `400 Expected object` [Л] |
| `permission/{per}/reply` | `reply`: `once`, `always`, `reject`; `message` | `always` дописывает разрешающие правила в конец, а побеждает **последнее** совпадение, то есть они перебивают запреты конфигурации | `O:permission/index.ts:28–38, 145–151` [Д] |
| любое тело | лишние поля | молча игнорируются, ошибки нет | [Л: `PATCH` с полем `evil` → 200] |
| `/api/*` (51 ручка) | `fs/read`, `fs/list`, `pty`, `credential`, `integration/*/connect/key`, свой `session/*/prompt` и др. | вторая поверхность API; в 1.18.33 включена | `H:server.ts:177, 281`, `S:gen/sdk.gen.js` (список путей) [Д]; `GET /api/fs/read/hostname?location[directory]=/etc` вернул файл [Л] |
| ответ 401 | заголовок `www-authenticate: Basic realm="Secure Area"` | если отдать его браузеру, тот покажет окно ввода пароля | `…/authorization.ts:48–49, 93–96` [Д, Л] |
| CORS | разрешены `localhost:*`, `127.0.0.1:*`, `*.opencode.ai` и список `--cors` | наш origin не разрешён; неважно, браузер в OpenCode не ходит | `packages/server/src/cors.ts:11–20` [Д, Л] |

Ещё три наблюдения:

* **Разрешения по умолчанию** (`O:agent/agent.ts:119–134`) [Д]: `"*": allow`,
  но `external_directory: ask`, `read *.env: ask`, `doom_loop: ask`; у агента
  `build` ещё `question: allow` (`agent.ts:141–151`). Конфигурация места из Д2
  блока `permission` не задаёт: ключи ровно `autoupdate`, `share`, `snapshot`,
  `enabled_providers`, `model`, `provider`
  (`plans/PLAN-D2-workplaces-2026-09-30.md:449`).
  Поэтому модель просит разрешение прочитать `/proc/self/environ`, студент
  жмёт «один раз», и пароль попадает в разговор [Л].
* **`external_directory: deny` в глобальном `opencode.json` это закрывает:**
  вызов `read` падает, запроса разрешения нет, пароля в истории нет [Л]. Но
  `tools:{"external_directory":true}` в `prompt_async` **или**
  `permission:[…allow]` в `POST /session` запрет снимают, и пароль снова в
  истории [Л]. Значит, вырезать эти поля в шлюзе обязательно, это не
  «защита впрок».
* `POST /session/{чужой}/abort` отвечает `200 true` без проверки, что
  разговор есть [Л]. Вреда нет.

## 5. Белый список: вызов → пропускаем или нет → что вырезаем

Путь сверяется **после** префикса `/api/oc` целиком, регулярным выражением,
с точным методом. Путь к OpenCode шлюз **собирает сам** из проверенных id,
сырой путь не пересылает. Строку запроса пропускает только из графы
«Запрос». Тело **собирает заново** из графы «Тело». Всё, чего нет в
таблице, — `403` с телом JSON.

| # | Вызов | Нужен студенту | Решение | Запрос: пропускаем | Тело: собираем заново | Проверки шлюза |
|---|---|---|---|---|---|---|
| 1 | `GET /experimental/session` | да — список разговоров | **пропускаем** | `roots`, `archived` только `true`/`false` | — | — |
| 2 | `POST /session` | да | **пропускаем** | — | всегда `{}`: вырезаны `parentID`, `title`, `agent`, `model`, `metadata`, `permission`, `workspaceID` | — |
| 3 | `GET /session/{ses}` | да | **пропускаем** | — | — | id по регулярке |
| 4 | `GET /session/{ses}/message` | да | **пропускаем** | — (`limit`, `before` не нужны) | — | id |
| 5 | `POST /session/{ses}/prompt_async` | да, главное | **пропускаем после пересборки** | — | `parts`: 1–20 частей только `{"type":"text","text"}`, текст ≤ 20 000 знаков; `agent` — только из списка агентов студента. Вырезаны `messageID`, `model`, `noReply`, `tools`, `format`, `system`, `variant`, у частей — `id`, `synthetic`, `ignored`, `time`, `metadata`. Части `file`, `agent`, `subtask` — `400` | id; агент из списка |
| 6 | `POST /session/{ses}/abort` | да — «стоп» | **пропускаем** | — | без тела | id |
| 7 | `GET /session/status` | да (после переподключения) | **пропускаем** | — | — | — |
| 8 | `GET /permission` | да (после переподключения) | **пропускаем** | — | — | — |
| 9 | `POST /permission/{per}/reply` | редко (`doom_loop`) | **пропускаем с политикой §7** | — | `{"reply"}`: `once` или `reject`; `always` — `400`; `message` вырезан | id; `once` — только если висящий запрос с этим id имеет `permission` из списка шлюза |
| 10 | `GET /question` | да | **пропускаем** | — | — | — |
| 11 | `POST /question/{que}/reply` | да — агент спрашивает | **пропускаем после пересборки** | — | `{"answers"}`: ≤ 10 списков по ≤ 20 строк ≤ 2 000 знаков | id |
| 12 | `POST /question/{que}/reject` | да | **пропускаем** | — | без тела | id |
| 13 | `GET /event` (SSE) | да — поток | **пропускаем потоком** | — | — | только типы из §3 плюс `server.connected`, `server.heartbeat`, `server.instance.disposed`; конец потока OpenCode — конец потока браузеру |
| 14 | `PATCH /session/{ses}` | да — переименовать, в архив | **пропускаем после пересборки** | — | `title` (1–200 знаков) и/или `time.archived` (число или `null`); вырезаны `permission`, `metadata`; пустое — `400` | id |
| 15 | `POST /session/{ses}/summarize` | нет | **не пересылаем**: шлюз сам отвечает `200 true` | — | — | — |
| 16 | `DELETE /session/{ses}` | нет | **403** (в вебе кнопки нет; вместо удаления — архив) | | | |
| 17 | `POST /session/{ses}/revert` | только для «правки» и «повтора» | **в пилоте 403**; если откроем — тело `{"messageID"}` по регулярке, `partID` вырезать | | | |
| 18 | `POST /session/{ses}/unrevert` | то же | **в пилоте 403** | | | |
| 19 | `POST /session/{ses}/fork` | нет | **в пилоте 403** | | | |
| — | всё прочее | нет | **403** | | | |

Почему так:

* **15.** Заголовок OpenCode делает сам: после первого ответа `title`
  разговора заполнен [Л]. Адаптер ошибку `summarize` только пишет в консоль
  ([`@assistant-ui/core@0.3.21` `src/react/runtimes/RemoteThreadResource.ts:45–55`]).
  Пересылать с моделью — значит платное сжатие разговора. Если ответить
  `true` самим, нет ни шума, ни расхода.
* **16–19.** Удаление и откат стирают разговор. При откате и следующем
  сообщении отменённые сообщения удаляются (`O:session/prompt.ts:1056`
  `revert.cleanup`), а преподаватель должен видеть разговоры (§3 проекта).
  Адаптер зовёт `revert` только из «повтора» и «правки» (`onReload`), а
  `fork` — только по вызову из интерфейса. Нет кнопок — нет вызовов.
* **Не пропускаем никогда** (для примера, список не исчерпывающий — решает
  таблица): `/session/{ses}/shell`, `/command`, `/init`, `/share`, `/diff`,
  `/todo`, `/children`, `/message/{msg}` (DELETE), `/message/{msg}/part/*`;
  `/pty*`, `/file*`, `/find*`, `/config*`, `/global/*` (вместе с
  `/global/event`, `/global/config`, `/global/upgrade`), `/mcp*`, `/auth/*`,
  `/provider*`, `/experimental/*` кроме строки 1, `/instance/dispose` (его
  зовёт только сам шлюз после хода Конструктора, Д7), `/agent`, `/tui/*`,
  `/sync/*`, `/vcs*`, `/project*`, `/lsp`, `/formatter`, `/path`, `/skill`,
  `/doc`, **весь `/api/*`**, статика интерфейса OpenCode.

**Заголовки** [Д: `H:middleware/workspace-routing.ts:87`,
`…/authorization.ts:77–83`]:

* браузер → шлюз: cookie сессии, `Origin`, `Content-Type`;
* шлюз → OpenCode: **только** `Authorization: Basic base64(opencode:<пароль
  места>)`, `Content-Type: application/json` при теле, `Accept:
  text/event-stream` для потока. Всё прочее не пересылается: cookie,
  `Authorization` из браузера, `x-opencode-directory`, `x-opencode-workspace`,
  `Origin`, `Referer`, `X-Forwarded-*`;
* OpenCode → браузер: код ответа и `Content-Type` (`application/json` или
  `text/event-stream`), для потока — `Cache-Control: no-cache` и
  `X-Accel-Buffering: no`. Не отдаются `www-authenticate`,
  `access-control-*`, `link`, `x-next-cursor`. **Ответ 401 от места шлюз
  превращает в 502** «место не приняло пароль шлюза»: это ошибка у нас, а не
  у студента.

**Черновой шлюз по этой таблице прошёл сквозной опыт** [Л]. SDK с
`baseUrl=…/api/oc` завёл разговор, получил ответ «Ответ: 42» и поток событий
(`server.connected`, `session.created`, `session.updated`, `message.updated`,
`message.part.updated`, `message.part.delta`, `session.status`,
`session.idle`). `tools` и `system` вырезаны: у сессии `permission = null`, у
сообщения `system = null`. Отказы: `file://` → 400, `agent:"plan"` → 400,
`subtask` → 400, `reply:"always"` → 400, `shell`, `delete`, `/config`,
`/file/content` → 403. `/api/oc/api/health`, `/api/oc/session/../global/health`
(`--path-as-is`), `/api/oc/session%2F..%2Fglobal%2Fhealth` → 403. Чужой
`ses_…` правильной длины → 404 из своего места.

## 6. Как шлюз проверяет, что sessionID принадлежит студенту

1. **По устройству, а не по списку.** Место выбирается по учётной записи из
   cookie, никогда по параметру запроса (§7 проекта). Чужой `ses_…` в своём
   месте просто не существует, ответ — `404 Session not found` [Л]. Реестр
   разговоров шлюзу не нужен.
2. **Формат id** [Д: `packages/schema/src/identifier.ts` —
   12 шестнадцатеричных знаков времени и 14 случайных из `[0-9A-Za-z]`;
   префиксы — `packages/core/src/id/id.ts:3–14`; Л: живые id вида
   `ses_f11ec356effelufzJ6nLzJyTda`]. Регулярки шлюза:
   `^ses_[0-9A-Za-z]{26}$`, `^msg_[0-9A-Za-z]{26}$`, `^per_[0-9A-Za-z]{26}$`,
   `^que_[0-9A-Za-z]{26}$`. Путь к OpenCode собирается из проверенного id,
   поэтому `..`, `%2F`, `//` не проходят.
3. **Каталог не выбирает никто, кроме образа.** `?directory=`, `?workspace=`,
   `x-opencode-directory` вырезаны, значит все разговоры — в `/work` (рабочий
   каталог образа, Д2). Для путей `/session/{ses}/…` OpenCode и сам берёт
   каталог из записи разговора (`H:middleware/workspace-routing.ts:182`).
4. **`requestID` у разрешений и вопросов** живут в памяти того же места
   (`O:permission/index.ts:86–100`). Чужой студент до них не дотянется. Для
   `once` шлюз читает `GET /permission` и находит запрос (§7).
5. **Мелочь для §9 проекта.** Там ждут «403» на чужие id, а честный ответ —
   404 из своего места. В автотесте Д5 считать успехом «не 2xx и в ответе
   нет данных Б».

## 7. Разрешения и вопросы

* **Вопросы нужны.** Инструмент `question` включён для клиента `cli`, а
  `serve` по умолчанию именно он (`O:tool/registry.ts:207`, флаг
  `OPENCODE_CLIENT`). Агенту `build` он разрешён. Конструктор может
  расспрашивать студента кнопками. Цепочка `question.asked` → `GET /question`
  → `POST /question/{que}/reply {"answers":[["Варка"]]}` → `question.replied`
  прошла живьём [Л].
* **Разрешения — узко.** С конфигурацией из п. 10 для Д5 (в конце файла)
  запрос разрешения остаётся один — `doom_loop` (модель трижды повторила тот
  же вызов).
  Непокрытое правилом по умолчанию разрешено, а не спрошено: первым идёт
  `"*": allow` (`O:agent/agent.ts:120`).
  Политика шлюза:
  * `reject` пропускается всегда;
  * `once` — только если висящий запрос с этим id (по `GET /permission`) имеет
    `permission` из списка `APPROVABLE = {"doom_loop"}`, иначе `403` «это
    разрешение даёт только преподаватель»;
  * `always` — `400`: он дописывает правила, которые перебивают запреты
    конфигурации до перезапуска места (§4);
  * поле `message` вырезается.
* **Цепочка разрешения живьём** [Л]: `permission.asked`
  (`external_directory`, `patterns:["/etc/*"]`, `always:["/etc/*"]`) →
  `GET /permission` → `reply once` → `permission.replied`, файл прочитан.
  Именно поэтому `external_directory` должен быть запрещён конфигурацией, а
  не ждать ответа студента.

## 8. Подключение веба к шлюзу: baseUrl, авторизация, CORS

* **Префикс можно** [Л]: `baseUrl: "https://ai-lab.pcbk.ru:8443/api/oc/"` →
  `…/api/oc/session`. Косая черта в конце срезается
  (`S:gen/client/utils.gen.js:127–131`), путь склеивается как `baseUrl + url`
  (`S:gen/core/utils.gen.js:55–69`). Относительный `baseUrl: "/api/oc"` в
  Node падает на `new Request` [Л]. Браузер относительный адрес разрешит, но
  надёжнее `` `${location.origin}/api/oc` ``.
* **Basic в браузере не нужен и недопустим.** SDK сам `Authorization` не
  ставит [Л]. Вход — наша cookie сессии (`HttpOnly`, `Secure`,
  `SameSite=Strict`). SDK не трогает `credentials`, а по умолчанию `fetch`
  шлёт cookie на тот же origin
  ([Fetch: credentials mode](https://fetch.spec.whatwg.org/#concept-request-credentials-mode)) [Д].
* **CORS не нужен:** веб и шлюз — один origin (`ai-lab.pcbk.ru:8443`).
  Заголовков `Access-Control-*` шлюз не ставит. **Защита от подделки
  запросов:** на всё, кроме GET, шлюз требует `Origin` = наш origin — браузер
  ставит `Origin` на POST, PATCH и DELETE и на своём же сайте
  ([Fetch: Origin header](https://fetch.spec.whatwg.org/#origin-header)) [Д].
  Плюс `SameSite=Strict`. У `abort`, `unrevert`, `reject` и `POST /session`
  тела нет, так что проверка «только JSON» от подделки не спасает — нужен
  `Origin`. Наш edge ставит `Referrer-Policy: no-referrer`
  (`edge/pcbk.conf.template:26`); по спецификации это превращает `Origin` в
  `null` только у запросов не в режиме `cors`, а `fetch` из SDK идёт в режиме
  `cors`, так что `Origin` будет настоящим [Д: шаг 4.1 по ссылке выше; в
  браузере — ?]. Второй признак — `Sec-Fetch-Site: same-origin`; шлюз
  принимает запрос, если сходится хотя бы один.
* **Ошибки — только JSON вида OpenCode**: `{"name":"Forbidden","data":{"message":"…"}}`.
  SDK берёт текст из `data.message` (`S:../error-interceptor.js:20–26`). На
  `Content-Type: text/html` SDK бросает «Request is not supported by this
  version of OpenCode Server» (`S:client.js:72–77`) [Д]. Значит, страницы
  ошибок nginx для `/api/` должны быть JSON.
* **Сессия кончилась или место спит.** Из-за цикла переподключения (§3) веб
  оборачивает `fetch`: на 401 закрывает чат и ведёт на вход. Чат
  монтируется только после «место готово» от своей ручки шлюза (например
  `GET /api/workplace`); пока место поднимается, веб показывает «Готовлю
  рабочее место…».
* **Вложения в пилоте выключены**: без `adapters.attachments` адаптер шлёт
  только текст, и шлюз пропускает только текст (строка 5). Позже можно
  открыть `data:` с `image/png`, `image/jpeg`, `text/plain` и пределом
  размера. `file:` — никогда.

### Минимальный пример: веб (Д6)

```tsx
// web/src/chat/Chat.tsx — адаптер 0.2.25 + SDK 1.18.33, всё через шлюз
import { useMemo } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { createOpencodeClient, useOpenCodeRuntime } from "@assistant-ui/react-opencode";
import { Thread } from "@/components/assistant-ui/thread"; // компонент из набора assistant-ui

// cookie сессии уходит сама: тот же origin. Basic знает только шлюз.
const gatewayFetch = async (req: Request) => {
  const res = await fetch(req);
  if (res.status === 401) window.location.assign("/login"); // иначе /event долбит раз в секунду
  return res;
};

export function Chat({ agent }: { agent: string }) { // agent — из «мои агенты» (служба)
  const client = useMemo(
    () => createOpencodeClient({ baseUrl: `${window.location.origin}/api/oc`, fetch: gatewayFetch }),
    [],
  );
  // defaultModel не задаём: модель задаёт агент, шлюз поле model всё равно вырезает
  const runtime = useOpenCodeRuntime({ client, defaultAgent: agent });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread /> {/* без кнопок «удалить», «правка», «повтор», без вложений */}
    </AssistantRuntimeProvider>
  );
}
```

`useOpenCodeRuntime` берёт готовый `client`, если его передать
(`A:useOpenCodeRuntime.ts:385–388`). Собственный `fetch` SDK вызывает как
`fetch(request)` (`S:client.js:44–54`, `S:gen/client/client.gen.js:56–59`) [Д].

### Минимальный пример: входной прокси (edge) и шлюз (Д5)

```nginx
# edge/pcbk.conf.template, server 8443 — дополнение
set $pcbk_core pcbk-core:8000;
location /api/ {                    # вход, место, чат — всё в pcbk-core
    proxy_pass http://$pcbk_core;
    proxy_read_timeout 30s;         # у server-блока сейчас 5s
    error_page 502 503 504 = @api_down;
}
location = /api/oc/event {          # поток: без буфера, heartbeat OpenCode — 10 с
    proxy_pass http://$pcbk_core;
    proxy_buffering off;
    proxy_read_timeout 1h;
    gzip off;
    error_page 502 503 504 = @api_down;
}
location @api_down {
    default_type application/json;
    return 503 '{"name":"Unavailable","data":{"message":"Серверный слой не отвечает"}}';
}
```

```python
# pcbk-core, шлюз: правила = таблица §5 (проверено черновиком [Л])
SES, MSG, PER, QUE = (rf"{p}_[0-9A-Za-z]{{26}}" for p in ("ses", "msg", "per", "que"))
RULES = [  # (метод, путь после /api/oc, разрешённый запрос, сборщик тела)
    ("GET",   r"/experimental/session",          {"roots", "archived"}, None),
    ("POST",  r"/session",                       set(), lambda b: {}),
    ("GET",   r"/session/status",                set(), None),
    ("GET",   rf"/session/{SES}",                set(), None),
    ("PATCH", rf"/session/{SES}",                set(), body_update),   # title, time.archived
    ("GET",   rf"/session/{SES}/message",        set(), None),
    ("POST",  rf"/session/{SES}/prompt_async",   set(), body_prompt),   # только text + agent из списка
    ("POST",  rf"/session/{SES}/abort",          set(), NO_BODY),
    ("GET",   r"/permission",                    set(), None),
    ("POST",  rf"/permission/{PER}/reply",       set(), body_perm),     # once|reject + APPROVABLE
    ("GET",   r"/question",                      set(), None),
    ("POST",  rf"/question/{QUE}/reply",         set(), body_answers),
    ("POST",  rf"/question/{QUE}/reject",        set(), NO_BODY),
]
# отдельно: GET /event — поток с фильтром типов; POST /session/{SES}/summarize — ответ 200 true без пересылки;
# всё прочее — 403 {"name":"Forbidden","data":{"message":"вызов не разрешён"}}
```

## 9. Что осталось проверить [?]

* Поток через настоящий edge (HTTP/2, `proxy_buffering off`) и `StreamingResponse`
  FastAPI под uvicorn на сервере, под gVisor — Д5.
* Обновляется ли заголовок в списке разговоров по `session.updated` без
  перезагрузки страницы: адаптер пишет его в состояние разговора, список
  берёт из `GET /experimental/session` — Д6 в браузере.
* Поведение кнопок одобрения assistant-ui, если шлюз ответил 403 на `once`
  (ошибка через `onError`, запрос остаётся висеть до `reject`) — Д6.
* Режим преподавателя тем же адаптером (только чтение) — Д10.
* Какой `Origin` и `Sec-Fetch-Site` реально приходят от страницы с
  `Referrer-Policy: no-referrer` — Д6 в браузере (§8).

## Что это значит для планов Д5 и Д6

**Д5 — шлюз:**

1. Префикс чата — **`/api/oc/`**; шлюз — в `pcbk-core` (порт 8000). Место —
   `http://${STU_NET}.N.3:4096`, выбор только по учётной записи. Пароль Basic
   `opencode:<пароль>` — из секрета `student-NN-pw`, в браузер не уходит.
2. Правила — **13 строк `RULES` + поток `/event` + заглушка `summarize`**
   (§5, §8); остальное — 403 JSON. Путь сверяется после префикса целиком,
   запрос собирается заново: путь из проверенных id, строка запроса из
   разрешённых ключей, тело из разрешённых полей. Пересылки «как есть» нет
   ни для чего.
3. Регулярки id — `^(ses|msg|per|que)_[0-9A-Za-z]{26}$`. Пределы: частей
   ≤ 20, текст ≤ 20 000 знаков, заголовок ≤ 200, ответы ≤ 10 × 20 × 2 000.
4. Политика разрешений: `reject` — всегда; `once` — только для
   `APPROVABLE = {"doom_loop"}` по `GET /permission`; `always` — 400;
   `message` вырезать.
5. Поток: фильтр 20 типов (17 из §3 + `server.connected`, `server.heartbeat`,
   `server.instance.disposed`), без буфера; поток к браузеру закрывается
   вместе с потоком места.
6. Таймауты к месту: соединение 2 с, чтение 30 с (кроме потока). Первые
   секунды после старта запросы висят (`04-d1-facts.md` §3), поэтому 503 JSON
   «место поднимается», пока `GET /agent` не ответит 200 (как у проверки
   здоровья Д2).
7. Ответ места 401 → 502 без `www-authenticate`. На всё, кроме GET, — проверка
   `Origin` = наш origin или `Sec-Fetch-Site: same-origin`. Cookie —
   `HttpOnly; Secure; SameSite=Strict`.
8. Простой 30 минут считать **по последнему запросу, кроме `/event`**:
   открытая вкладка держит поток часами и иначе не даст месту уснуть.
9. edge: `location /api/` с `proxy_read_timeout 30s`, `location = /api/oc/event`
   с `proxy_buffering off` и `proxy_read_timeout 1h`, ошибки 502–504 —
   JSON (§8). Нынешние 5 с `proxy_read_timeout` рвали бы поток при
   heartbeat 10 с.
10. **Дополнение к конфигурации образа (Д2, пересборка в Д5):** в
    `student/config/opencode.json` добавить
    `"permission": {"external_directory": "deny", "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"}}`.
    Опыт [Л]: без этого одобренный студентом `read` выносит пароль из
    `/proc/self/environ`; с этим — отказ без вопроса. Тест образа
    `set(cfg) == {…}` (план Д2, стр. 449) дополнить ключом `permission`.
11. Автотесты Д5 (каждый — отказ **и** проверка состояния места):
    * все ручки из «не пропускаем никогда» → 403, включая `/api/oc/api/fs/read/…`,
      `..`, `%2F`;
    * `tools`, `system`, `permission` (в POST и PATCH), `model`, `messageID`
      вырезаны: у сессии `permission == null`, у сообщения `system == null`;
    * `file://…`, `subtask`, `agent`-часть → 400;
    * `?directory=`, `?workspace=`, `x-opencode-directory`, `?auth_token=`
      не влияют: `directory == "/work"`, ответ 200;
    * `reply:"always"` → 400; `once` на `external_directory` → 403;
    * чужой `ses_…` → 404 без данных;
    * чужой `Origin` → 403;
    * место ответило 401 → 502 без `www-authenticate`.
12. §9 проекта: «Успех 5 … — 403» читать как «отказ (403 или 404), данных Б
    нет».

**Д6 — веб:**

1. Закрепить точно: `@assistant-ui/react-opencode@0.2.25`,
   `@assistant-ui/react@0.15.22`, `@opencode-ai/sdk@1.18.33` плюс
   `overrides` на него, `react@19.3.0`; lock-файл в git; CSP
   `connect-src 'self'`.
2. Клиент — ``createOpencodeClient({ baseUrl: `${location.origin}/api/oc`, fetch: gatewayFetch })``
   и `useOpenCodeRuntime({ client, defaultAgent })`; `defaultModel` не
   задавать (пример в §8).
3. `gatewayFetch` на 401 ведёт на вход. Чат монтируется только после «место
   готово».
4. В интерфейсе нет «удалить», «правка», «повтор», «ветка», вложений; есть
   «переименовать», «в архив», «стоп». Заголовок разговора — из OpenCode.
5. Вопросы агента (`useOpenCodeQuestions`) — кнопками; разрешения — показать
   «Отклонить» всегда, «Разрешить один раз» — только для `doom_loop`.

**Решения владельца** (по умолчанию — как написано выше):

* удаление разговоров студентом закрыто, вместо него архив — чтобы
  преподаватель видел всё;
* «правка» и «повтор» сообщения в пилоте выключены: откат удаляет
  сообщения из истории, которую читает преподаватель.
