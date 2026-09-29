# Веб-чат с агентами поверх OpenCode: что есть готового

29.09.2026 · исследование агента на Opus 5.5 · проверено по исходникам v1.18.33
и v2.0.19, документации, npm и issues; ничего не установлено и не запущено ·
**[Д]** — подтверждено по ссылке или коду, **[?]** — не подтверждено.

## Главное

У OpenCode сейчас две ветки: **v1.18.33** (npm `opencode-ai`, GitHub помечает
latest) и **v2.0.19** (npm `@opencode/cli`, переписанный движок с API `/api/*`,
вышел 29.09). **Отключить конфигурацией ручки, дающие оболочку, нельзя ни в
одной ветке.** Изоляция — только контейнером и нашим шлюзом с белым списком.

## 1. OpenCode сегодня

* [Д] Репозиторий переехал: sst/opencode →
  [anomalyco/opencode](https://github.com/anomalyco/opencode), MIT, ~211 тыс.
  звёзд. v1.18.33 от 28.09; v2.0.12…v2.0.19 за 21–29.09. Установщик
  opencode.ai/install ставит releases/latest = v1.18.33.
* [Д] Образ `ghcr.io/anomalyco/opencode:<версия>` есть только для v2
  ([v2 docs](https://opencode.ai/v2/docs/)). V1-плагины в v2 не работают,
  агенты и разрешения в новом формате ([migrate-v1](https://opencode.ai/v2/docs/migrate-v1/)).
* [Д] SDK: v1 — `@opencode-ai/sdk` 1.18.33; v2 — `@opencode/client` и
  `@opencode/sdk` 2.0.19, MIT.
* [Д] `serve`: по умолчанию 127.0.0.1:4096, флаги `--cors`, `--mdns`.
  Авторизация — один пароль Basic (`OPENCODE_SERVER_PASSWORD`; в v2 ещё
  `OPENCODE_PASSWORD` и токены сопряжения с HMAC). Пользователей нет
  ([server](https://opencode.ai/docs/server/), v2 `packages/server/src/auth.ts`).
* [Д] Поток событий: v1 — SSE `/event`, `/global/event`; v2 — SSE `/api/event`
  и экспериментальный `/api/experimental/session/:id/log?after=&follow=true` с
  досылкой пропущенного. OpenAPI — `/doc` (v1), `packages/protocol/openapi.json`
  (v2).
* [Д] Сессии: v1 — `/session`, `/session/:id/message`, `prompt_async`, `abort`,
  ответы на разрешения; v2 — 139 ручек
  ([V2_HTTP_API_AUDIT.md](https://github.com/anomalyco/opencode/blob/v2.0.19/V2_HTTP_API_AUDIT.md)),
  ручек v1 в v2 нет.
* [Д] Отключать ручки нечем: в `ServerOptions` (v2 `server/src/options.ts`) и
  флагах `OPENCODE_*` такого нет.
  [SECURITY.md](https://github.com/anomalyco/opencode/blob/v2.0.19/SECURITY.md):
  «permission system… not designed to provide security isolation»; доступ к API
  при включённом сервере — «expected behavior». Прецедент удалённого выполнения
  команд: [GHSA-vxw4-wv6m-9hhh](https://github.com/anomalyco/opencode/security/advisories/GHSA-vxw4-wv6m-9hhh).
* [Д] CORS жёстко пропускает `*.opencode.ai` и `localhost:*` (v2
  `server/src/cors.ts`) — неважно, пока порт недоступен из браузера.

## Опасные ручки OpenCode для нашей модели угроз

| Ручка (v2 / v1) | Что даёт | Закрывается конфигурацией | Источник |
|---|---|---|---|
| `POST /api/session/:id/shell` / `POST /session/:id/shell` | команда в оболочке без проверки разрешений | нет | v2 `core/src/session/shell.ts`; v1 `session/prompt.ts` |
| `POST /api/shell`, `/api/shell/:id/output` | произвольный процесс | нет | аудит API v2 |
| `/api/pty*`, `/api/pty/:id/connect` (WebSocket), `/api/experimental/persistent-pty/*`, `…/terminal` / v1 `/pty*` | интерактивный терминал | нет | аудит v2; v1 `groups/pty.ts` |
| `PUT /api/experimental/mcp/:server` / `POST /mcp` | MCP типа local = запуск любой команды | нет | v2 `schema/src/mcp.ts` |
| `/api/fs/read/*`, `fs/list`, `fs/find` + `location[directory]` или `x-opencode-directory` / v1 `/file/content`, `/find` + `?directory=` | чтение любого файла, доступного процессу; каталог выбирает клиент | нет | v2 `server/src/location.ts:41`; v1 `workspace-routing.ts:87` |
| `POST/PATCH /api/session` с полем `permissions` | клиент сам разрешает сессии оболочку | нет | v2 `handlers/session.ts:135,273` |
| `…/permission/:id/reply` | одобрение операций «ask» | нет (так задумано) | аудит v2 |
| v1 `PATCH /config`, `PATCH /global/config`; v2 `PATCH /api/experimental/config` | переписать разрешения, MCP, агентов | нет | v1 `handlers/config.ts` |
| v1 `POST /global/upgrade`; v2 `POST /api/plugin/update` | установка и обновление пакетов | нет | маршруты v1 и v2 |
| v1 `POST /session/:id/command` с шаблоном !\`cmd\` | оболочка через шаблон команды | частично: не давать писать в `commands/` | [commands](https://opencode.ai/docs/commands/) |
| v1 `POST /session/:id/share` | выгрузка разговора в облако opncd.ai | да: `"share":"disabled"` | [config](https://opencode.ai/docs/config/) |
| v1 `PUT /auth/:id`; v2 `/api/credential/*`, `/api/integration/*/connect/*` | подмена ключей провайдера | нет | аудит v2 |
| `POST /api/rpc/:rpcID/:method` | RPC плагинов | неизвестно | аудит v2 |
| файл агента с `permission: bash: allow` | правила агента после глобальных, последнее совпадение побеждает | v2: да (`experimental.policies` с deny + удаление инструмента плагином); v1: подменить `bash` инструментом-заглушкой | v1 `agent/agent.ts:293`; v2 `config/plugin/policy.ts`; [custom-tools](https://opencode.ai/docs/custom-tools/) |

## 2. Как агент создаётся «в чате»

* [Д] Формат — markdown с frontmatter в `.opencode/agents/*.md` или
  `~/.config/opencode/agents/`, либо ключ `agent` в JSON (v2 — `agents`, поле
  `system`, массив `permissions`). Имя файла = имя агента
  ([v1](https://opencode.ai/docs/agents/), [v2](https://opencode.ai/v2/docs/agents/)).
* [Д] `opencode agent create --path --description --mode --permissions` работает
  без диалога, но только в v1; в CLI v2 её нет.
* [Д] Отдельной API-ручки для создания агента нет ни в одной ветке (v1 —
  только `GET /agent`, v2 — `GET /api/agent`). В v1 можно записать агента через
  опасный `PATCH /config`.
* [Д] Перечитывание: v1 кэширует конфигурацию бессрочно (`config.ts:295`),
  после записи файла снаружи нужен `POST /instance/dispose` [?] живьём; v2
  следит за каталогами агентов и перечитывает через 100 мс, плюс
  `POST /api/location/reload`.

## 3. Как агенты вызывают внешнюю службу

* [Д] Remote MCP: Streamable HTTP с запасным SSE, поле `headers`, подстановка
  `{env:VAR}`; инструменты называются `<сервер>_<инструмент>`, права шаблоном
  ([v2 MCP](https://opencode.ai/v2/docs/mcp-servers/)).
* [Д] Свои инструменты: v1 — `.opencode/tools/*.ts`, одноимённый заменяет
  встроенный; v2 — только плагином (`ctx.tool.transform`, `editor.remove`,
  хуки `execute.before`, `evaluate`). Код выполняется в процессе OpenCode, то
  есть в контейнере студента.
* **Вывод агента** для «одна служба на стенд, общий бюджет и кэш»: remote MCP к
  нашей службе с токеном студента в заголовке; плагин — только для защиты
  (удалить или заблокировать shell, edit, write); local MCP не использовать.

## 4. Готовые веб-интерфейсы и надстройки

* [Д] Официальные `opencode web` (v1) и `opencode pair` (v2): один пароль,
  полный интерфейс с терминалом и файлами. Многопользовательский режим закрыт
  как «not planned» ([#20067](https://github.com/anomalyco/opencode/issues/20067)).

| Проект | Звёзды | Последний коммит | Лицензия | Пользователей |
|---|---|---|---|---|
| [cahya-wirawan/opencode-multiuser](https://github.com/cahya-wirawan/opencode-multiuser) | 4 | 18.09.2026 | нет LICENSE | много |
| [btriapitsyn/openchamber](https://github.com/btriapitsyn/openchamber) | 10 903 | 29.09.2026 | MIT | один, пароль |
| [NeuralNomadsAI/CodeNomad](https://github.com/NeuralNomadsAI/CodeNomad) | 2 609 | 28.09.2026 | MIT | один, пароль |
| [different-ai/openwork](https://github.com/different-ai/openwork) | 23 783 | 29.09.2026 | вне `ee/` открытая, `ee/` коммерческая | [?] |
| [hosenur/portal](https://github.com/hosenur/portal) | 811 | 12.05.2026 | MIT | один, Tailscale |
| [prokube/pk-opencode-webui](https://github.com/prokube/pk-opencode-webui) | 43 | 15.09.2026 | MIT | один |
| [chris-tse/opencode-web](https://github.com/chris-tse/opencode-web) | 135 | 15.07.2025, заброшен | AGPL-3.0 | один |

* [Д] **opencode-multiuser** 0.11.2 ближе всего к задаче: FastAPI-шлюз,
  rootless-контейнер Podman на рабочее место, Traefik, PostgreSQL, OIDC или
  локальные учётки, до 10 мест (`MAX_SLOTS`), `WORKSPACE_MEMORY=8g`, закреплён
  на OpenCode 1.18.30. Но проксирует весь интерфейс OpenCode с терминалом.
* [Д] Песочницы:
  * [gVisor](https://gvisor.dev/docs/architecture_guide/platforms/) (Apache-2.0,
    активен): платформа systrap без KVM, работает внутри ВМ; подключается к
    Docker и Podman как `runsc` — **подходит**;
  * sysbox — для системных контейнеров, не нужен;
  * [Daytona](https://github.com/daytonaio/daytona) — публичный репозиторий не
    поддерживается с июня 2026;
  * [E2B infra](https://github.com/e2b-dev/infra) — Firecracker, нужен KVM;
    одиночный хост — «evaluation package, not a production»;
  * [AI SDK Harness](https://vercel.com/changelog/deepagents-and-opencode-harness-adapters)
    — экспериментальный, под Vercel Sandbox.

## 5. Библиотеки чата для своего бэкенда

* [Д] `@assistant-ui/react-opencode` 0.2.25 от 24.09.2026, MIT,
  экспериментальный, поверх `@opencode-ai/sdk` ^1.18.31 — **только v1**
  ([docs](https://www.assistant-ui.com/docs/runtimes/opencode/overview)).
  Вызывает только сессии (create/get/update/delete/messages/promptAsync/abort/
  fork/revert/summarize/status), `event.subscribe`, ответы на разрешения и
  вопросы — ни shell, ни pty, ни файлов: **готовый белый список для шлюза**.
* [Д] Vercel AI SDK 7 (`ai` 7.0.122, Apache-2.0): `useChat` с любым бэкендом по
  SSE-протоколу ([protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol));
  нужен свой переводчик событий OpenCode.
* [Д] assistant-ui ExternalStoreRuntime (MIT) рисует любые сообщения и вызовы
  инструментов из нашего состояния. CopilotKit 1.75.0 (MIT, AG-UI) — готового
  переходника OpenCode→AG-UI не найдено [?].

## 6. Ресурсы

* [Д] Свежий `serve` — 161–166 МБ; за 7 ч активной работы до ~1 ГБ, после
  простоя не отдаёт (v2.0.16, macOS;
  [#51340](https://github.com/anomalyco/opencode/issues/51340)).
* [Д] Linux в простое 250–900 МБ, при параллельных сессиях рост до 25–32 ГБ —
  удержание памяти распределителем Bun (v2.0.16–2.0.19;
  [#52041](https://github.com/anomalyco/opencode/issues/52041), открыт 29.09).
* [Д] Старая сборка 0.1.44: 602 МБ RSS + 1,15 ГБ подкачки после 53 дней
  ([#16729](https://github.com/anomalyco/opencode/issues/16729)).
* [?] Для v1.18.x на Linux чисел нет.
* Оценка агента: 10 экземпляров по 250–400 МБ в простое = 2,5–4 ГБ — в 7 ГБ
  только с жёстким лимитом памяти на контейнер, перезапуском после OOM и
  остановкой простаивающих; под одновременной нагрузкой не подтверждено.

## Варианты сборки

* **A. Свой шлюз и контейнер OpenCode на студента** (рекомендация агента).
  * Шлюз: вход и роли; пропускает только ручки из списка адаптера assistant-ui
    и только сессии самого студента; поля `permissions` и `directory` не
    пропускает; преподавателю — только GET.
  * Контейнер: gVisor, `cap-drop`, корневая ФС только на чтение, лимит памяти;
    сеть внутренняя — только до нашего LLM-прокси (OpenCode умеет `baseURL`,
    ключ OpenRouter остаётся у нас) и до службы данных; порт OpenCode наружу
    не публикуется.
  * Агент-конструктор вызывает MCP-инструмент `save_agent` нашей службы; служба
    проверяет схему (без shell и edit) и пишет файл в каталог агентов студента,
    смонтированный в контейнер только на чтение.
  * Интерфейс: React и assistant-ui.
  * Плюсы: жёсткие границы, общий бюджет, минимум своего интерфейса. Минусы:
    адаптер экспериментальный; белый список сопровождать; 10 процессов по
    памяти.
* **B. За основу — opencode-multiuser.** Готовые жизненный цикл контейнеров,
  слоты, остановка по простою. Но отдаёт весь интерфейс OpenCode с терминалом,
  лицензии нет, противоречит решению 17 «с нуля» — только образец.
* **C. Один общий `serve`, студенты в разных каталогах.** Экономит память, но
  оболочка, файлы и MCP общие, каталог выбирает клиент — первая ошибка шлюза
  даёт неуспех 1 или 3. Не рекомендуется.

**Развилка для владельца — ветка OpenCode.** Агент советует для пилота закрепить
v1.18.33: стабильный канал, готовый адаптер интерфейса работает с v1; цена —
позже перенос шлюза и адаптера на v2. v2 даёт перечитывание агентов без
перезапуска и жёсткий запрет через `policies`, но ей 8 дней и у неё открытые
проблемы с памятью.

## Что проверить на живой установке

1. Изнутри контейнера вызвать `…/session/:id/shell` с `env`, чтением `/proc` и
   попыткой соединения с узлом БДРВ на 1433: пароля и маршрута быть не должно.
2. Файл агента с `bash`/`shell: allow` блокируется (v1 — подменённый
   инструмент, v2 — `policies` и удаление инструмента плагином).
3. Каждая опасная ручка из таблицы через шлюз от студента — 403.
4. После записи файла агента `GET /agent` его показывает (v1 — после
   `/instance/dispose`, v2 — сам); видит ли слежение за файлами под gVisor
   изменения в смонтированном каталоге.
5. Память: 10 контейнеров под лимитом, 30 ходов с MCP, память в простое через
   час.
6. Remote MCP с заголовком `{env:STUDENT_TOKEN}`: служба видит, какой студент
   спрашивает.
7. В закрытом контуре выключить внешний трафик: `OPENCODE_DISABLE_MODELS_FETCH`,
   `OPENCODE_DISABLE_AUTOUPDATE`, `share: disabled` (аналоги в v2 — [?]).
8. Переподключение SSE через шлюз без потерь (v2 — через `after`).

## Поправки критика (Opus 5.5, 29.09.2026)

Критик проверил ключевые утверждения по сети и исходникам v1.18.33 и v2.0.19 —
все верны, блокеров нет. Поправки:

* **Важно для шлюза.** Белый список ручек адаптера сам по себе не безопасен. В
  v1 `PATCH /session/:id` (`session.update`, адаптер его вызывает) и
  `POST /session` принимают поле `permission` (`groups/session.ts:49–52`,
  `session/session.ts:267`); `prompt_async` принимает поле `tools`, которое
  становится разрешением allow для сессии (`session/prompt.ts:1061–1066`), и
  поле `system`. Шлюз для v1 обязан **вырезать `permission`, `tools`,
  `system` и проверять `agent`** — а не только `permissions` и `directory`, как
  сказано в варианте A.
* Адаптер assistant-ui вызывает ещё `experimental.session.list`,
  `question.list/reject`, `permission.list` и `unrevert`.
* v2 не «8 дней»: v2.0.0 вышла 11.09, ей 18 дней; 8 дней — ветке 2.0.12+.
