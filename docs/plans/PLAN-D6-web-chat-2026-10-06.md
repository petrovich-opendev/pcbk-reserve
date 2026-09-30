# Д6. Веб-чат — план реализации (Д6а и Д6б)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** студент открывает на стенде `/chat/`. Он видит постоянное предупреждение «учебный стенд» и баннер
состояния по `/status.json`, а при первом входе — предупреждение, что разговоры видит преподаватель.
Он спрашивает агента по умолчанию и получает ответ числом из БДРВ. На странице состояния у владельца
есть строка «Веб-чат»: она красная, если страница чата не отдаётся или выложена не целиком, и жёлтая,
если браузеры студентов сообщают о молчании чата.

**Architecture:**
- **Веб** — React SPA: TypeScript, Vite, `@assistant-ui/react-opencode`. Собирается на машине
  разработчика в статику `edge/static/chat/`. `edge` отдаёт её сам, со строгим CSP и без `core`.
- **Связь с местом** — только через шлюз Д5 (`/pcbk/api/oc/*`: 15 правил и поток `/event`). Всё
  идёт через свою обёртку `fetch`. Она разбирает отказы шлюза, сама считает паузы
  переподключения потока, а на 401 и «место спит» останавливает чат.
- **Вход и пробуждение места** — ручки Д5а. **Баннер** читает `/status.json` сторожа напрямую.
- **Агент по умолчанию** — свой файл в образе места (`pomoshnik`, три инструмента службы данных);
  встроенные агенты OpenCode выключены.
- **Наблюдение:** у сторожа новый вид проверки `web` — страница и опись сборки через `edge`. В Д6б
  шлюз принимает отчёты браузеров о молчании (`POST /pcbk/api/web/report`) и отдаёт их сторожу
  через `/health/web`.

**Tech Stack:**
- веб: React 19.3.0, TypeScript 6.0.3, Vite 8.3.1 и `@vitejs/plugin-react` 6.1.1,
  `@assistant-ui/react` 0.15.22, `@assistant-ui/react-opencode` 0.2.25,
  `@assistant-ui/react-markdown` 0.14.17 и `remark-gfm` 4.0.1, `@opencode-ai/sdk` 1.18.33;
- тесты веба: Vitest 5.0.3 и jsdom 30.1.1, Testing Library; Node 22.x только на машине разработчика;
- стенд: nginx 1.30.5-alpine (`edge`), сторож на stdlib, FastAPI (`core`, Д6б), Docker Compose;
- браузерные автотесты: google-chrome headless и свой клиент CDP на stdlib Python.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md):
- §1 п. 2 — веб: React, TS, assistant-ui; предупреждение о статусе стенда видно постоянно;
- §3 п. 1 — предупреждение при первом входе; п. 3 — число из БДРВ;
- §7 п. 2 — белый список шлюза;
- §8 — баннер: «состояние неизвестно», если страница сторожа не отвечает или устарела;
- §9 — строка «Наблюдаемость».

Прочие источники:
- постановка — «Итоговая постановка» и §8 [`ZADACHA-fallback-opencode-2026-09-25.md`](../ZADACHA-fallback-opencode-2026-09-25.md)
  («Состав задачи» п. 1; «студент предупреждается при первом входе»; успех 2; неуспех 1–4);
- строка Д6 [`PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md);
- главный источник по вебу — [`research/07-gateway-whitelist.md`](../research/07-gateway-whitelist.md)
  §1–§3, §8 и «Д6 — веб»; ещё [`research/02-web-chat-opencode.md`](../research/02-web-chat-opencode.md)
  §5 и [`research/09-constructor-agent.md`](../research/09-constructor-agent.md) §2, §9 п. 3;
- хвосты для Д6 — «Отклонения» и задача Б8 [`PLAN-D5-gateway-2026-10-05.md`](PLAN-D5-gateway-2026-10-05.md).

Имена, Global Constraints и решения Д3а-R1…Д5-R38 действуют здесь. Решения этого плана
нумеруются дальше: Д6-R39…R52.

**Сверка с кодом (30.09, то, что меняет план).** Пакеты `@assistant-ui/*` скачаны в
`$CLAUDE_JOB_DIR/tmp/d6plan`, исходники OpenCode 1.18.33 — в `$CLAUDE_JOB_DIR/tmp/oc-1.18.33`.
Сокращения источников — как в research/07: `A:` — `react-opencode@0.2.25/src`, `S:` — SDK
`packages/sdk/js/src/v2`, `O:` — `packages/opencode/src`.
1. **Паузы потока адаптер не держит.** `OpenCodeEventSource` удваивает задержку только при
   исключении до подписки (`A:OpenCodeEventSource.ts:155–216`). SDK с `sseMaxRetryAttempts: 1`
   на ответ не 2xx выходит из генератора молча, без исключения
   (`S:gen/core/serverSentEvents.gen.ts:124–131, 220–228`). В итоге на 401 и 503 поток
   повторяется раз в 1 с, и код ответа адаптер не видит. Видеть его и держать паузу может только
   наша обёртка `fetch` (Д6-R43).
2. **Ошибки SDK несут код и тело.** `wrapClientError` кладёт в `cause` `{status, body}`, текст
   сообщения берёт из `data.message` (`packages/sdk/js/src/error-interceptor.ts`). По ним веб
   различает отказы шлюза.
3. **Radix в assistant-ui.** `ActionBarMorePrimitive`, `ThreadListItemMorePrimitive`,
   `AssistantModalPrimitive`, `ComposerPrimitive.Unstable_TriggerPopover*` и `AttachmentDropzone`
   построены на меню и всплывающих окнах Radix. Они вставляют `<style>`, а это нарушает CSP без
   `unsafe-inline`. Эти примитивы не используются (Д6-R41).
4. **`default_agent` и `disable` в 1.18.33 есть.** Если агента по умолчанию нет, он `subagent` или
   `hidden`, место отвечает ошибкой (`O:agent/agent.ts:266–271, 328–335`). Поэтому нужен тест
   образа и интеграции (А5).
5. **В локальном стенде историана нет.** `pcbk_tag_now` отвечает `"status": "unavailable"`
   (`tests/integration/test_conversation.py:76–81`). Локальный автотест проверяет, что ответ
   модели построен на выводе инструмента. Число из БДРВ — только живьём, в окне владельца (А8).
6. **Смена агента по умолчанию задевает тесты Д4б.** Тесты `test_conversation_without_tools_reads_no_data`
   (`"read" in names`) и `test_model_question_is_rejected_and_reported` (FAKEASK требует
   инструмент `question`) опираются на встроенного `build` и правятся в А5.

---

## Разрез Д6 на два дня

Оценка по плановой шкале Д1–Д5 одним днём — **27,25 ч**:
- наблюдение — 3,0;
- каркас веба и клиент шлюза — 4,0;
- интерфейс — 4,5;
- агент по умолчанию — 1,5;
- предупреждение — 1,5;
- интеграция в браузере — 5,0;
- две выкладки, живые проверки и закрытия — 6,75;
- утро и хвосты — 1,0.

Это больше порога одного дня: по прецеденту — 15,1 ч у Д4а и 17,5 ч у Д5а с чертой отсечения.
Поэтому, как Д3, Д4 и Д5, Д6 делится надвое. У каждой половины свой видимый результат и своя
черта:

- **Д6а «Чат в браузере» — 16,5 ч**, при темпе 0,30 около 5,0 ч. Что видит владелец:
  - студент входит, попадает в `/chat/`, видит постоянное предупреждение «учебный стенд»,
    баннер состояния и свой логин;
  - спрашивает агента по умолчанию — **ответ числом из БДРВ** (окно владельца);
  - строка «Веб-чат» на странице состояния и её учение;
  - учение баннера «сторож остановлен → состояние неизвестно».
- **Д6б «Предупреждение, молчание, разговоры» — 10,75 ч**, при темпе 0,30 около 3,2 ч. Что видит
  владелец:
  - предупреждение при первом входе и его подтверждение (в `accounts list` —
    «предупреждён»);
  - строка «Веб-чат» желтеет по отчётам браузеров («ответ модели не пришёл вовремя», «чат падает
    в браузере», «браузеры теряют связь с сервером») — учения со снимками;
  - список разговоров с «в архив» и «переименовать».

Пилот не начинается до `platform-d6b`: до этого предупреждения при первом входе нет. Д6а
выкладывается только под пробные учётки (строка 3 раздела владельца).

## Отклонения от дорожной карты (для контролёра)

Строки для раздела «Отклонения» `docs/PLAN-platform-2026-09-29.md`. Правит их контролёр при
утверждении плана.

* **Веб-чат — два дня (Д6а и Д6б):** 16,5 + 10,75 ч по плановой шкале. Всего 16 дней, нумерация
  Д7–Д12 прежняя. Строка «Что видит владелец» делится: Д6а — ответ числом, баннер, предупреждение
  «учебный стенд», строка «Веб-чат»; Д6б — предупреждение при первом входе, отчёты о молчании,
  список разговоров.
* **Страница входа остаётся статикой Д5а.** Чат — отдельная страница `/chat/`; вход после «Рабочее
  место готово» ведёт в неё.
* **Вопросы агента кнопками и «разрешить один раз» — Д7, вместе с Конструктором.** У агента по
  умолчанию нет `question`, а `doom_loop` у него запрещён правилом `"*": deny`, так что в Д6 ни
  вопросов, ни запросов разрешения не бывает. Если такой запрос всё же повиснет, чат покажет его с
  кнопкой «Отклонить» (Д6-R42).
* **Встроенные агенты выключены, задан `default_agent`** (research/09 §9 п. 3) — в Д6а. Образ
  места — `:d6a`, места пересоздаются с сохранением томов.
* **Счётчик «`same-origin`, но `Origin` ≠ `GW_ORIGIN`»** (Д5-R26, отложен в Д6) снят без кода
  (Д6-R48).
* **Хвосты Д5б:**
  - `baseUrl` — `${location.origin}/pcbk/api/oc`;
  - логин в шапке;
  - 401 → вход; 503 `WorkplaceNotReady` → «Разбудить»; 429 `streams` → «закройте лишние вкладки»;
  - `Origin`, `Sec-Fetch-Site` и HTTP/2 — живьём;
  - «из архива» скрыт (Ш13).

  Всё это закрывается задачами А3, А4, А6, А8 и Б3.

## Global Constraints

Действуют Global Constraints Д1–Д5б целиком:
- образы — через `deploy/images.lock`;
- секреты и данные заказчика — никогда в git и в выводе проверок; секреты контейнерам — только
  файлами;
- `docker.sock` — только у `sp-ro`/`sp-ctl`;
- наружу — только 8443 `edge`;
- людям — по-русски, в коде — английские имена;
- Dify не трогаем;
- роль по договору `Role` Д3а, крючки — через реестр `AppHooks` Д4а;
- у `warn`/`fail` сторожа текст постоянный, числа — в `note`;
- места пересоздаются без удаления томов, `docker compose down` на сервере запрещён;
- проверки секретов — с положительным контролем (шаблоны и `mkforms` Д5);
- ответы и ошибки под `/pcbk/api/` — JSON вида OpenCode;
- `edge` пропускает в `core` только пути шлюза;
- деньги в живых проверках без владельца — только под `DRILL_LLM=402`.

Свои скрипты — в `$JOB` (`$CLAUDE_JOB_DIR/tmp/pcbk-d6`), не в репозитории. Д6 добавляет:

- **Сборка веба — только на машине разработчика.**
  - Node 22.x.
  - `npm ci --ignore-scripts` по `web/package-lock.json`; `web/.npmrc` — `ignore-scripts=true`,
    `save-exact=true`, `audit=false`, `fund=false`, `engine-strict=true`.
  - Все версии в `web/package.json` — точные (`^`, `~`, `*` запрещены), `@opencode-ai/sdk` —
    ещё и через `overrides` на 1.18.33 (research/07 §1).
  - На сервер и в образы уходит **только** собранная статика `edge/static/chat/` (в git её нет,
    `.gitignore`); `node_modules` на сервер не попадает никогда.
  - Сборка выкладки — из чистого дерева тега дня (`git archive`) с `PCBK_BUILD=<12 знаков
    коммита>`.
  - Никаких CDN и внешних адресов: CSP `connect-src 'self'` их закрыл бы, а `check-dist`
    отвергает уже при сборке.
- **CSP страницы чата — ровно** `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'`
  (Д6-R41). Без `unsafe-inline`, `unsafe-eval`, `data:` и `blob:`.
- **Веб зовёт только:**
  - `/pcbk/api/auth/{me,logout}`, `/pcbk/api/workplace`, `/pcbk/api/workplace/wake`;
  - 15 правил и поток `/pcbk/api/oc/*`;
  - `/status.json`;
  - с Д6б ещё `/pcbk/api/auth/notice` и `/pcbk/api/web/report`.

  В `prompt_async` уходят только текстовые части: без `agent` и `model` (агента выбирает
  `default_agent`, Д6-R42) и без вложений. Кнопок «удалить», «изменить», «повторить», «ветка»,
  «из архива», «прикрепить» нет.
- **Веб ничего не хранит:** ни пароля, ни cookie (она `HttpOnly`), ни текста разговоров — ни в
  `localStorage`, ни в `sessionStorage`, ни в IndexedDB.
- **Тексты ответа модели и вывода инструментов** выводятся только как текст или как markdown без
  сырого HTML. Ссылки — только `http:`, `https:` и относительные, с `rel="noopener noreferrer"`.
- **Снимки экрана** с тегами и числами заказчика — только в `$JOB` и владельцу, **в git не
  идут**. В `docs/checks/D6a/` — снимки без производственных данных: тестовый стенд, учения, страница
  состояния, чат под `DRILL_LLM=402`.
- **Теги образов — первой правкой дня (Ш2):**
  - Д6а — сторож и место `:d6a`, `core` остаётся `:d5b` (Д6а его не меняет);
  - Д6б — `core` и сторож `:d6b`.

  До этой правки — ни `docker compose build`, ни интеграционных прогонов, в том числе у
  ревьюеров. ID откатных образов (`watchdog:d5b`, `core:d5b`, `student:d4`, в Д6б ещё
  `watchdog:d6a`, `student:d6a`) снимаются до и после каждого интеграционного прогона. Образы
  выкладок прежних дней собираются из своей ветки (`git worktree`).
- **Пробные учётки живых проверок** — только `d6-probe-*`: `d6-probe-a` — место 10, `d6-probe-n`
  (Д6б) — место 09. Пароли — файлы `0600` в `~/pcbk-d6/` и `$JOB`. Закрытие каждой половины удаляет
  их разговоры по записанным id, отключает и удаляет учётки и освобождает места
  (`release-place`, README «Освобождение места пробной учётки»).
- **Коммиты — только по путям** (`git commit -- <пути>`, Ш20). В дереве могут работать другие;
  `stash`, `reset`, `checkout` и `clean` запрещены.
- **Тесты:**
  - веб — `(cd web && npm ci --ignore-scripts && npm test && PCBK_BUILD=dev npm run build)`;
  - серверный слой — `(cd core && CORE_PYTEST)`;
  - сторож — `(cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs)`;
  - вход — `node --test edge/tests/*.test.mjs`;
  - интеграция — `uv run --python 3.12 --with pytest pytest -q tests/integration`.

  Модуль `test_web` берёт **место 09**, гасит его к концу (в том числе в `finally` фикстур) и идёт
  после модулей Д5 (места 1, 3–8). Без google-chrome он пропускается с причиной, но закрытие дня
  требует прогона без пропусков.

---

# Д6а. Чат в браузере

**Goal Д6а:** студент входит на `/login`, и после «Рабочее место готово» его ведут в `/chat/`. В
шапке — предупреждение «учебный стенд», баннер состояния, «Вы вошли как …», «Выйти». Студент
пишет вопрос и получает ответ агента по умолчанию `pomoshnik` со строкой вызова инструмента.
В окне владельца это ответ числом из БДРВ, которое сходится с `tag_now`.

Место уснуло — чат показывает «Разбудить» и не долбит шлюз. Сессия кончилась — чат ведёт на вход.
Сторож остановлен — баннер пишет «Состояние стенда неизвестно». Строка «Веб-чат» на странице
состояния краснеет, когда страница чата не отдаётся или сборка выложена не целиком.

**Предпосылка (как Ш1 Д5).** Ветка дня `d6a/web-chat` — от головы `d5b/gateway-whitelist`.
- Код А1–А6 идёт без сервера.
- А0, шаг 3, и задачи А7–А9 — после `platform-d5b` и выкладки Д5б.
- Окно владельца (А8, шаг 5) требует выложенных Д4а и Д4б (ключ OpenRouter с балансом, места с
  MCP) и Д5а, Д5б.
- Слияние Д6а в `main` — после `platform-d5b`, с перебазированием на `main` и повтором А6, шаг 5.

**Влезает ли в день — оценка по часам.** Задачи последовательны. В часы задач с кодом входят
15 минут на ревью и правки.

| Задача | Часы | Где |
|---|---|---|
| А0. Теги первой правкой; хвосты Д5б; утро: предпроверки сервера и версий | 0,5 | локально, сервер |
| А1. Сторож: строка «Веб-чат» (наблюдение раньше функции) | 1,25 | локально |
| А2. Каркас веба: версии, lock, сборка в `edge/static/chat`, опись, проверка статики | 1,75 | локально |
| А3. Клиент шлюза: классы отказов, паузы потока, баннер, пробуждение места | 2,25 | локально |
| А4. Интерфейс: шапка, место, разговор, ожидающие ответы агента | 2,5 | локально |
| А5. Агент по умолчанию в образе места; правка тестов Д4б | 1,5 | локально |
| А6. `edge` `/chat/`, вход → чат, компоновка, автотесты в браузере | 3,0 | локально |
| А7. Выкладка | 1,0 | сервер |
| А8. Живые проверки, учения, окно владельца | 1,5 (+ окно ≈45 мин) | сервер |
| А9. Закрытие дня | 1,25 | — |
| **Критический путь** | **16,5** | |

**Основание «один день»** — как у Д5а. Живой темп по git (способ Д4а, дата автора `%aI`, код до
черты): худшая доля Д1–Д3б — 0,30; строки Д4а–Д5б — если будут к началу дня. При 0,30 Д6а — около
5,0 ч.

Если «темп кода до черты» последнего закрытого дня хуже 0,5, Д6а до начала дня делится:
- **Д6а** — А0–А7 и шаги 1–4 А8: чат, баннер и строка на сервере, без окна;
- **Д6а′** — окно владельца (А8, шаг 5) и А9.

Владелец получает строку с числами.

**Черта отсечения — конец 6,5-го часа** плюс превышение хвостов Д5б над 0,5 ч. К черте должны быть
зелёны задачи А0–А6: 12,75 ч по плановой шкале, ≈3,8 ч при 0,30. Черта ловит темп хуже ≈0,51.
После каждой задачи кода — `pace` Д4а от коммита А1 до текущего; доля хуже 0,5 — строка владельцу
сразу. После черты порядок жёсткий: А7 → А8 → А9.
- **А6 не зелёна к черте.** Выкладки нет. Видимый результат — снимки локального стенда с пометкой
  «не на сервере»: чат, ответ на тестовом стенде, баннер «неизвестно», строка «Веб-чат». Владельцу
  в тот же час уходит строка: Д6а кончится завтрашним утром, Д6б сдвигается на полдня.
- **Чат в настоящем браузере нарушает CSP** (А6, `csp == []` не проходит). Час на разбор по
  источнику нарушения: примитив Radix → заменить своим; проба `new Function` у zod 4 →
  `z.config({ jitless: true })` в `main.tsx` до первого импорта рантайма. Не вышло — строка
  владельцу с источником. **Ослаблять CSP без решения владельца нельзя.**
- **Поток не держится через `edge` по HTTP/2 в Chrome** (А6, шаг 5). Час на разбор; дальше — вердикт
  и вопрос. Поток — транспорт, а не граница, так что это не блокер: чат с паузами
  переподключения остаётся рабочим.

## Решения по умолчанию Д6а (Ruling)

**Д6-R39 — где живёт веб.**
- Чат — отдельная страница `/chat/`: SPA, `index.html` и `assets/*`, статика `edge` из
  `edge/static/chat/` (привязка `./edge/static` Д1 — та же).
- Страница входа Д5а остаётся как есть и после «Рабочее место готово» ведёт в чат
  (`location.assign('/chat/')`, запасная ссылка «Открыть чат»).
- Чат при открытии сам спрашивает `GET /pcbk/api/auth/me`:
  - 401 → `/login`;
  - у преподавателя — текст «У преподавателя пока нет чата…» и ни одного вызова `/pcbk/api/oc`;
  - у студента — `GET /pcbk/api/workplace`: «готово» → чат, иначе `POST …/wake` и опрос раз в
    секунду до 90 с, теми же состояниями и текстами, что `viewFor` страницы входа.
- `/chat` → 301 на `/chat/` с относительным `Location` (`absolute_redirect off`: через туннель
  абсолютный адрес увёл бы на 8443).

**Цена ошибки:** логика пробуждения есть в двух местах — `login.js` и `workplace.ts`. Тексты общие:
тест А3 сверяет их со страницей входа.

**Д6-R40 — сборка и поставка.**
- Версии — строкой Tech Stack, точно.
- `vite.config.ts`: `base: '/chat/'`, `build.outDir: '../edge/static/chat'`, `emptyOutDir: true`,
  `assetsInlineLimit: 0` (без `data:`), `modulePreload: { polyfill: false }`, `sourcemap: false`.
- Плагин `pcbkBuildInfo(build)`:
  - вставляет в `index.html` `<meta name="pcbk-build" content="<build>">`;
  - пишет `build.json` = `{"build": "<build>", "files": {"assets/<имя>": "<sha256 hex>", …}}` по
    всем выпущенным файлам `assets/`.
- `build` — `PCBK_BUILD` из окружения, по умолчанию `dev`, шаблон `[A-Za-z0-9._-]{1,64}`.
- `scripts/check-dist.mjs` идёт последним шагом `npm run build` и роняет сборку, если:
  - в `index.html` есть `<script>` без `src`, `<style>`, атрибут `style=`, `http:`, `https:`,
    `data:` или `//` в `src` и `href`;
  - ссылка ведёт на отсутствующий файл;
  - опись не совпадает с файлами каталога и их суммами;
  - в `assets/` есть `*.map`;
  - файлов в `assets/` больше `WEB_MAX_FILES = 8`.
- На сервер статика уходит в соседний каталог `chat.new`: суммы сверяются по `build.json`, потом
  каталог встаёт на место переименованием, прежний остаётся `chat.<день>` для отката (А7).

**Д6-R41 — CSP и что из assistant-ui берём.**
- CSP — строкой Global Constraints, на `/chat/` и `/chat/assets/*`. Туда же — `Cache-Control:
  no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`: `add_header` в
  `location` отменяет заголовки `server`.
- `build.json` на 8443 — 404: опись читает только сторож через внутренний 8080.
- Берём только примитивы без Radix-меню: `ThreadPrimitive` (`Root`, `Viewport`, `Messages`,
  `Empty`, `If`), `ComposerPrimitive` (`Input`, `Send`, `Cancel`), `MessagePrimitive` (`Parts`,
  `Error`, `If`), `ErrorPrimitive.Message`, `ThreadListPrimitive` (`New`, `Items`),
  `ThreadListItemPrimitive` (`Root`, `Trigger`, `Title`, `Archive`), `MarkdownTextPrimitive`.
- Список «не использовать» — сверка с кодом, п. 3.
- Стили — один `chat.css`, свой, без Tailwind. Шрифты — системные.

**Цена ошибки:** одно нарушение CSP в библиотеке — это сломанная часть интерфейса. Её ловит
браузерный тест, но только на тех путях, которые он проходит.

**Д6-R42 — клиент OpenCode и что в интерфейсе.**
- Клиент: `createOpencodeClient({ baseUrl: `${location.origin}/pcbk/api/oc`, fetch: gatewayFetch })`
  и `useOpenCodeRuntime({ client, onError })` — **без** `defaultAgent` и `defaultModel`, **без**
  `adapters`. Нет адаптера вложений — нет вложений; `summarize` шлюз закрывает сам.
- Клиент и рантайм — свои на каждое монтирование чата. Размонтирование (401, «место спит»,
  «Выйти») закрывает поток адаптера (`registry.dispose`).
- Кнопки Д6а: «Отправить», «Стоп», «Новый разговор». Д6б добавляет «В архив» и «Переименовать».
- Поле ввода — `maxLength` 20 000 (`MAX_TEXT` правил Д5б; тест сверяет число с `rules.py`).
- Части ответа:
  - текст — markdown (`remark-gfm`, без `rehype-raw`);
  - рассуждение — не показывается;
  - вызов инструмента — строка «Запрос к данным: <что> — <идёт…|готово|ошибка>». `pcbk_tag_now` —
    «текущее значение», `pcbk_tag_period` — «сводка за период», `pcbk_catalog_search` — «поиск
    тегов», иначе имя. Под строкой — свёрнутый `<details>` с аргументами и выводом, обрезанным до
    2 000 знаков;
  - ошибка сообщения — `MessagePrimitive.Error` с текстом места: например, «На счёте OpenRouter
    кончились деньги…» LLM-прокси Д4а.
- Висящие разрешения и вопросы (`useOpenCodePermissions`, `useOpenCodeQuestions`) — строка «Агент
  ждёт ответа, которого этот чат пока не умеет дать» и кнопка «Отклонить»: `reply(id, "reject")` /
  `rejectQuestion(id)`. Отклонение шлюз пропускает всегда (Д5-R34).
- Список разговоров, «в архив» и «переименовать» — Д6б. До этого после перезагрузки страницы
  открывается новый разговор; прежние лежат в месте.

**Д6-R43 — обёртка `fetch` шлюза.** `createGatewayFetch` зовётся на каждый запрос SDK с `Request`
(`S:client.js:44–54`). Разбор ответа (`classify`) — по коду и полю `name` тела:

| Код | `name` | Класс | Что делает веб |
|---|---|---|---|
| 2xx | — | `ok` | — |
| 401 | любое | `signed_out` | остановка, размонтирование, `/login` |
| 503 | `WorkplaceNotReady` | `asleep` | остановка, экран «Рабочее место спит» и кнопка «Разбудить» (сам не будит: Д5-R27) |
| 429 | `TooManyRequests` | `too_many_tabs` | строка потока — текст шлюза «закройте лишние вкладки» |
| 503 | `WorkplaceBusy` | `busy` | текст шлюза |
| 503 | `Unavailable` | `down` | «Серверный слой не отвечает» (текст `edge`) |
| прочий 4xx/502 с JSON вида OpenCode | — | `refused` | текст `data.message` |
| нет ответа | — | `offline` | «Нет связи с сервером — проверьте сеть» |
| иное, тело не JSON вида OpenCode | — | `unknown` | «Неожиданный ответ сервера — сообщите преподавателю.» |

Поток `/pcbk/api/oc/event`:
- перед запросом обёртка ждёт `eventDelayMs(failures) = 0` при `failures = 0`, иначе
  `min(1000·2^(failures−1), 30000)·(0,8 + 0,4·random())`;
- отмена сигналом запроса прерывает ожидание;
- ответ 200 — `failures = 0`, иное — `failures + 1`;
- состояние уходит в `onStream({failures, nextRetryS, last})`, веб пишет «Связь с рабочим местом
  прервалась — переподключаюсь через N с».

После `signed_out` или `asleep` обёртка **остановлена** до размонтирования:
- поток ждёт отмены и в сеть не идёт;
- прочие вызовы сразу отвергаются `HaltedError` (без всплывающей ошибки);
- `onHalt(kind)` зовётся ровно один раз.

Обычные вызовы не задерживаются.

**Цена ошибки:** при сбое поток восстанавливается до 30 с позже, чем при повторе раз в секунду.

**Д6-R44 — баннер и предупреждение «учебный стенд».**
- Предупреждение — `STAND_WARNING`, тот же текст, что `.stand` на `login.html`: «Учебный стенд: не
  для ведения технологического процесса — данные и выводы только для разбора и обучения.».
  Полоса закреплена сверху и видна всегда.
- Баннер опрашивает `/status.json` раз в 10 с, срок запроса 4 с. Строки чужих мест
  (`student-NN` ≠ своё) не считаются, `absent` не считается. Уровень — худший из оставшихся:
  `fail` > `unknown` > `warn` > `ok`, как в `page.py`.

  | Уровень | Текст |
  |---|---|
  | `ok` | «Стенд работает» |
  | `warn` | «Внимание: <названия>» |
  | `fail` | «Сбой: <названия>» |
  | `unknown` | «Состояние стенда неизвестно — сторож не отвечает» |

  Названий — не больше трёх, дальше « и ещё N». Рядом ссылка «Подробнее» на `/status`
  (`target=_blank`, `rel="noopener noreferrer"`).
- «Неизвестно» ставится, если:
  - ответ `stale: true`, не 200 или не JSON (страница `watchdog-down.html`);
  - по монотонным часам браузера с последнего свежего ответа прошло больше `stale_after_s`, в том
    числе когда запрос повис;
  - как в `status.js` сторожа.

**Цена ошибки:** пока сертификат `edge` истёк, у всех студентов баннер «Сбой: Входной прокси»
(строка 2 раздела владельца).

**Д6-R45 — агент по умолчанию.**
- Образ места `pcbk-reserve/student:d6a`: `student/config/agent/pomoshnik.md` (текст — задача А5);
  в `opencode.json` — `"default_agent": "pomoshnik"` и
  `"agent": {"build": {"disable": true}, "plan": {"disable": true}, "general": {"disable": true}, "explore": {"disable": true}}`.
- Права агента: `"*": deny`, затем `pcbk_catalog_search`, `pcbk_tag_now`, `pcbk_tag_period` —
  `allow`; `mode: primary`, `steps: 8`. Ни `question`, ни `read`, ни заглушек оболочки модель не
  видит.
- `compaction`, `title`, `summary` не трогаются. Имя `pomoshnik` служба Д7 не выдаёт
  (`name_reserved`, research/09 §4.2).
- Файл агента в образе меняется только пересборкой. Места пересоздаются с сохранением томов
  (README «Пересоздание мест»).

**Цена ошибки:** модель у агента по умолчанию уже, чем у `build`. Прочитать файлы `/work` агент
не может, и студенту это и не нужно.

**Д6-R46 — строка сторожа «Веб-чат».**
- Вид `web`, компонент `{"id": "web", "title": "Веб-чат", "kind": "web", "url": "http://pcbk-edge:8080/chat/"}`.
- Через внутренний 8080 `edge` — тот же корень, что 8443, но без TLS. У сторожа `_http_get`
  только `http://`; TLS и 8443 держит строка «Входной прокси».
- Порядок: `GET /chat/` (тело ≤ `MAX_BODY_BYTES`) → `GET /chat/build.json` → `GET` каждого файла
  описи (тело не читается). Вся проверка — не дольше `HTTP_CHECK_SPANS × NET_CHECK_TIMEOUT_S`
  (6 с), файлов в описи — не больше `WEB_MAX_FILES = 8`.

  | Ответ | Итог | `note` |
  |---|---|---|
  | нет соединения или срок | `fail` «не отвечает» | — |
  | `/chat/` не 200 или без `<meta name="pcbk-build" content="…">` | `fail` «страница чата не отдаётся» | «HTTP N» / «нет метки сборки» |
  | опись не 200, не JSON, не той формы, путь вне `assets/[A-Za-z0-9._-]{1,100}`, файлов больше 8 | `fail` «сборка чата неполная» | «опись неверна» |
  | `build` описи ≠ метке страницы | `fail` «сборка чата неполная» | «страница и опись разных сборок» |
  | файл описи не 200 | `fail` «сборка чата неполная» | «нет <путь>» |
  | иначе | `ok` «страница чата отдаётся» | «сборка <build>» |

**Д6-R47 — живые проверки Д6а.**
- Пробная учётка `d6-probe-a` — место 10.
- Без владельца — только под `DRILL_LLM=402`: чат, баннер, учения строки и баннера, заголовки
  настоящего браузера и HTTP/2 через туннель.
- В окне владельца:
  - один вопрос о теге, тег называет владелец;
  - сверка — правило Д4б (задача 6, шаг 4): число в ответе после округления до показанных знаков
    равно значению из ответа `pcbk_tag_now` в том же ходе;
  - `tag_now` через туннель — вторая сверка;
  - снимок с тегом — только владельцу и в `$JOB`.

**Д6-R48 — счётчик «`same-origin`, но `Origin` ≠ `GW_ORIGIN`» (Д5-R26, отложенный в Д6) снят.**
Браузер на своей странице всегда шлёт `same-origin`, так что опечатка в `GW_ORIGIN` браузерам не
мешает. Удаления cookie от `GW_ORIGIN` не зависят (Д5-R25). Через туннель проверок счётчик
срабатывал бы на каждом входе. Итог: шум без пользы.

## Review Focus Д6а

1. **Страница чата нарушает CSP в настоящем браузере.** Источники: примитив на Radix-меню, проба
   `new Function` у zod 4, встроенный скрипт сборщика, `data:` вместо файла. Итог — пустая или
   полусломанная страница. Ожидание: ноль нарушений на всех путях теста.
   Тесты:
   - задача А2 — `check_dist_rejects_inline_and_external`;
   - задача А6 — `test_chat_answers_through_gateway` (`csp == []`, `console_errors == []`).
2. **Шторм переподключений.** Место уснуло, сессия кончилась, `core` лёг или открыта четвёртая
   вкладка — адаптер долбит `/event` раз в секунду и после каждого переподключения делает пять GET
   на разговор. Ожидание: паузы 1→30 с; после 401 и «спит» — ни одного запроса до действия
   студента.
   Тесты:
   - задача А3 — `event_backoff_grows_and_resets`, `halt_stops_stream_and_rejects_calls`;
   - задача А6 — `test_place_asleep_shows_wake_and_goes_quiet`, `test_signed_out_goes_to_login`.
3. **Разметка в ответе модели или в выводе инструмента** (описания тегов приходят из историана и
   могут нести что угодно): `<img onerror>`, `<script>`, `[x](javascript:…)`, вывод на 1 МБ.
   Ожидание: HTML показан текстом, опасная ссылка без `href`, вывод обрезан до 2 000 знаков.
   Тест — задача А4, `renders_model_markup_inert`.
4. **Интерфейс зовёт вызов вне белого списка** (удалить, откат, ветка, «из архива», вложение):
   403 от шлюза, шум в `refused_1h` и непонятная ошибка у студента. Ожидание: таких кнопок и
   `input[type=file]` нет; за обычный разговор `refused_1h` не растёт, а все запросы браузера под
   `/pcbk/api/oc/` совпадают с правилами.
   Тесты:
   - задача А4 — `chat_view_has_only_allowed_controls`;
   - задача А6 — `test_chat_answers_through_gateway`.
5. **Баннер врёт.** Показывает «Стенд работает» при мёртвом стороже (HTML 502, `stale`, повисший
   запрос) или «Сбой» из-за чужого места. Ожидание: «неизвестно» не позже `stale_after_s` по
   часам браузера; учитывается только своё место.
   Тесты:
   - задача А3 — `status_view_filters_and_levels`, `polling_goes_unknown_by_clock`;
   - задача А6 — `test_banner_follows_watchdog`.

## Карта файлов Д6а

```
watchdog/pcbk_watchdog/checks.py      + check_web, WEB_META_RE, WEB_FILE_RE, WEB_MAX_FILES
watchdog/pcbk_watchdog/main.py        + KINDS["web"] = ("url",), вызов check_web
watchdog/components.json              + строка web (последней)
watchdog/tests/test_checks.py, test_main.py  (+)
edge/pcbk.conf.template               8443: = /chat, = /chat/, ^~ /chat/assets/, ^~ /chat/ (404); 8080: = /chat/, ^~ /chat/
edge/static/login.html, login.js      переход в чат после «готово», ссылка «Открыть чат»
edge/static/index.html                ссылка на чат вместо «появятся позже»
edge/tests/login.test.mjs             (+)
web/package.json, package-lock.json, .npmrc, tsconfig.json, vite.config.ts, index.html
web/build/buildInfo.ts                pcbkBuildInfo(build): метка в index.html, build.json
web/scripts/check-dist.mjs            checkDist(dir) и запуск из npm run build
web/src/main.tsx                      вход: createRoot, App
web/src/texts.ts                      все тексты людям; STAND_WARNING, MAX_TEXT
web/src/api.ts                        callJson, createApi (me, logout, wake, workplace)
web/src/gateway.ts                    classify, explainError, eventDelayMs, createGatewayFetch, HaltedError
web/src/status.ts                     statusView, startStatusPolling
web/src/workplace.ts                  placeView, wakeAndWait
web/src/App.tsx                       фазы: загрузка → вход/преподаватель/место → чат
web/src/components/Header.tsx, StatusBanner.tsx, WorkplaceScreen.tsx
web/src/components/Chat.tsx           клиент и рантайм на монтирование, onError, поток, висящие ответы
web/src/components/ChatView.tsx       Thread, Composer, сообщения — от runtime в пропсах
web/src/components/MessageParts.tsx   MarkdownText, ToolLine
web/src/components/PendingInteractions.tsx
web/src/chat.css
web/tests/deps.test.ts, checkDist.test.ts, gateway.test.ts, status.test.ts, workplace.test.ts,
          App.test.tsx, ChatView.test.tsx, texts.test.ts
student/config/agent/pomoshnik.md, student/config/opencode.json
compose.yaml                          сторож и место :d6a
tests/integration/cdp.py              Chrome по CDP: запуск, страница, ожидания, сеть, CSP, снимки (stdlib)
tests/integration/test_web.py         чат в браузере (место 09)
tests/integration/conftest.py         + сборка веба в фикстуре stack, фикстура browser
tests/integration/workplace.py        IMAGE :d6a
tests/integration/test_edge.py        + /chat/, 8080, IMAGES :d6a
tests/integration/test_student_image.py, test_students.py, test_conversation.py  агент по умолчанию
.gitignore                            + edge/static/chat/
deploy/README.md                      + «Статика чата»; выкладка Д6а
docs/checks/D6a.md, docs/checks/D6a/*.png
```

Имена: образы `pcbk-reserve/watchdog:d6a` и `pcbk-reserve/student:d6a` (`watchdog:d5b` и
`student:d4` остаются для отката), `core:d5b` без изменений; пробная учётка `d6-probe-a` (место 10);
тестовые учётки интеграции `web-a` (студент, место 9) и `web-t` (преподаватель).

---

### Task А0: Теги первой правкой, хвосты Д5б и утро

**Files:**
- Modify: `compose.yaml` (образ места в якоре `x-student` и сторож), `tests/integration/test_edge.py`
  (`IMAGES`), `tests/integration/workplace.py` (`IMAGE`)
- Create: `docs/checks/D6a.md`

- [ ] **Step 1: Теги — первой правкой, до любой сборки (Ш2).** `watchdog:d5b` → `:d6a`,
  `student:d4` → `:d6a` в `compose.yaml`, `IMAGES` и `workplace.IMAGE`; `core:d5b` не трогать.
  Expected: `/usr/bin/grep -c ':d6a' compose.yaml` → `2`; `/usr/bin/grep -c 'core:d5b' compose.yaml` → `1`.
  Commit: `git add compose.yaml tests/integration/test_edge.py tests/integration/workplace.py && git commit -m "Д6а: теги образов сторожа и места :d6a до первой сборки" -- compose.yaml tests/integration/test_edge.py tests/integration/workplace.py`.
- [ ] **Step 2: После `platform-d5b` (Ш1):** всё, что черта Д5б перенесла сюда (раунд критика,
  слияние, тег, чистый клон). Затем `d6a/web-chat` перебазируется на `main`.
  Expected: вердикты в `docs/checks/D5b.md`; `git merge-base --is-ancestor platform-d5b HEAD` → код `0`.
- [ ] **Step 3: Предпроверки сервера (только чтение) — после выкладки Д5б.** Тот же набор, что Д5б,
  Б0, шаг 2:
  - `core :d5b` `healthy` в 13 сетях;
  - пробных учёток нет;
  - у мест 09 и 10 закреплений нет;
  - места не `running`;
  - строки «Шлюз и вход» и «LLM-прокси и бюджет» в норме;
  - снимки строк и времени работы Dify — в `~/pcbk-d6a-rows.txt`, `~/pcbk-d6a-before.txt`.

  Плюс ID откатных образов:
  `docker image inspect -f '{{.Id}}' pcbk-reserve/watchdog:d5b pcbk-reserve/core:d5b pcbk-reserve/student:d4`.
  Expected — как Д5б, Б0. Если место 09 или 10 уже выдано студенту, пилот начался: живые шаги А8
  откладываются, вердикт и строка владельцу — до А7.
- [ ] **Step 4: Версии локально.**
  - `node --version` → `v22.*`;
  - `google-chrome --version` записан;
  - `npm view @assistant-ui/react-opencode@0.2.25 dist.integrity` и
    `npm view @opencode-ai/sdk@1.18.33 dist.integrity` совпадают с суммами research/07 §1;
  - темп последнего закрытого дня — правило деления из шапки.

  Expected: совпало; если нет — стоп, строка владельцу (подмена пакета в реестре).
- [ ] **Step 5: Commit** (после проверки на секреты): `git add docs/checks/D6a.md && git commit -m "Д6а: утро — стенд Д5б жив, версии веба сверены" -- docs/checks/D6a.md`.

---

### Task А1: Сторож — строка «Веб-чат»

Наблюдение раньше функции: строка пишется до того, как появится сам чат.

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/pcbk_watchdog/main.py`,
  `watchdog/components.json`, `edge/pcbk.conf.template` (server 8080)
- Test: `watchdog/tests/test_checks.py` (+2), `watchdog/tests/test_main.py` (+1)

**Interfaces:**
- Consumes: `_http_get(url, timeout, read_body)`, `Check`, `HTTP_CHECK_SPANS`,
  `NET_CHECK_TIMEOUT_S`, `MAX_BODY_BYTES` (Д1–Д3а).
- Produces:
  - `check_web(component: str, title: str, url: str, timeout: float = NET_CHECK_TIMEOUT_S) -> Check`
    — таблица Д6-R46;
  - `WEB_MAX_FILES = 8`;
  - `WEB_META_RE = re.compile(r'<meta name="pcbk-build" content="([A-Za-z0-9._-]{1,64})">')`;
  - `WEB_FILE_RE = re.compile(r"assets/[A-Za-z0-9._-]{1,100}")`;
  - `KINDS["web"] = ("url",)`;
  - строка `components.json` из Д6-R46;
  - `edge`, server 8080: `location = /chat/ { root /usr/share/nginx/pcbk; try_files /chat/index.html =404; }`
    и `location ^~ /chat/ { root /usr/share/nginx/pcbk; try_files $uri =404; }`.

  Опись `build.json` — договор с А2: `{"build": str, "files": {путь: sha256}}`.

- [ ] **Step 1: Write the failing tests**

```python
# watchdog/tests/test_checks.py (+) — фикстура web_site: ThreadingHTTPServer на 127.0.0.1 отдаёт tmp_path
def put_build(root, build="abc123", files=None, meta=None):
    files = {"assets/index-1.js": b"x", "assets/index-1.css": b"y"} if files is None else files
    chat = root / "chat"; (chat / "assets").mkdir(parents=True, exist_ok=True)
    tag = f'<meta name="pcbk-build" content="{meta or build}">' if meta != "" else ""
    (chat / "index.html").write_text(f"<!doctype html><head>{tag}</head>")
    for p, b in files.items():
        (chat / p).write_bytes(b)
    (chat / "build.json").write_text(json.dumps({"build": build,
                                                 "files": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}}))

def row(c):
    return (c.state, c.detail, c.note)

def test_web_verdicts(web_site):
    root, url = web_site                                                   # url = http://127.0.0.1:N/chat/
    put_build(root)
    assert row(check_web("web", "Веб-чат", url)) == ("ok", "страница чата отдаётся", "сборка abc123")
    (root / "chat/assets/index-1.css").unlink()
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "сборка чата неполная", "нет assets/index-1.css")
    put_build(root, meta="zzz")
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "сборка чата неполная", "страница и опись разных сборок")
    put_build(root, files={"../x.js": b"x"})
    assert row(check_web("web", "Веб-чат", url))[1:] == ("сборка чата неполная", "опись неверна")
    put_build(root, files={f"assets/f{i}.js": b"x" for i in range(9)})
    assert row(check_web("web", "Веб-чат", url))[2] == "опись неверна"
    put_build(root, meta="")
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "страница чата не отдаётся", "нет метки сборки")
    (root / "chat/index.html").unlink()
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "страница чата не отдаётся", "HTTP 404")
    assert row(check_web("web", "Веб-чат", "http://127.0.0.1:9/chat/"))[:2] == ("fail", "не отвечает")

def test_web_check_has_one_deadline(slow_site):                            # каждый ответ — через 2,5 с
    t0 = time.monotonic()
    c = check_web("web", "Веб-чат", slow_site, timeout=3.0)
    assert time.monotonic() - t0 < 2 * 3.0 + 1 and c.state == "fail"

# watchdog/tests/test_main.py (+)
def test_components_have_web_row():
    comps = load_components(COMPONENTS)
    assert comps[-1] == {"id": "web", "title": "Веб-чат", "kind": "web", "url": "http://pcbk-edge:8080/chat/"}
```

- [ ] **Step 2: Run tests to verify they fail** — `(cd watchdog && uv run --python 3.12 --with pytest pytest -q tests/test_checks.py tests/test_main.py)`. Expected: FAIL (`check_web` не определён).
- [ ] **Step 3: Implement по Interfaces.** Общий срок — `deadline = time.monotonic() +
  HTTP_CHECK_SPANS * timeout`. Перед каждым `_http_get` срок пересчитывается: остаток ≤ 0 →
  `fail` «не отвечает». Опись разбирается `json.loads` под `except (ValueError, RecursionError)`.
- [ ] **Step 4: Run both suites** — `(cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs)`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add watchdog/ edge/pcbk.conf.template && git commit -m "Сторож: строка «Веб-чат» — страница чата и опись сборки через edge" -- watchdog/ edge/pcbk.conf.template`.

---

### Task А2: Каркас веба

**Files:**
- Create: `web/package.json`, `web/package-lock.json`, `web/.npmrc`, `web/tsconfig.json`,
  `web/vite.config.ts`, `web/index.html`, `web/build/buildInfo.ts`,
  `web/scripts/check-dist.mjs`, `web/src/main.tsx` (заглушка «Чат загружается…»)
- Modify: `.gitignore` (+ `edge/static/chat/`)
- Test: `web/tests/deps.test.ts`, `web/tests/checkDist.test.ts`

**Interfaces:**
- Produces:
  - `pcbkBuildInfo(build: string): import("vite").Plugin` — Д6-R40;
  - `checkDist(dir: string): string[]` (пустой список — годится; иначе тексты нарушений) и CLI
    `node scripts/check-dist.mjs <dir>` с кодом 1 при нарушениях;
  - скрипты `package.json`: `"test": "vitest run"`,
    `"build": "tsc --noEmit -p . && vite build && node scripts/check-dist.mjs ../edge/static/chat"`;
    `vitest` в `vite.config.ts`: `environment: "jsdom"`, `include: ["tests/**/*.test.ts?(x)"]`;
  - `package.json`: `"private": true`, `"type": "module"`, `"engines": {"node": "22.x"}`,
    `"overrides": {"@opencode-ai/sdk": "1.18.33"}`.

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/deps.test.ts
const pkg = JSON.parse(readFileSync("package.json", "utf8"));
const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
test("pins_are_exact_and_single", () => {
  for (const deps of [pkg.dependencies, pkg.devDependencies])
    for (const v of Object.values(deps)) expect(v).toMatch(/^\d+\.\d+\.\d+$/);
  const versions = (name: string) => Object.entries<any>(lock.packages)
    .filter(([p]) => p.endsWith(`node_modules/${name}`)).map(([, v]) => v.version);
  expect(versions("@opencode-ai/sdk")).toEqual(["1.18.33"]);
  expect(versions("react")).toEqual(["19.3.0"]);
  expect(versions("@assistant-ui/react-opencode")).toEqual(["0.2.25"]);
  expect(lock.packages["node_modules/@assistant-ui/react-opencode"].integrity).toBe(
    "sha512-MPP7w+MbTjFKSZX2aoNd8wK/6WXIEuU/w2qc5jy+/Vx249lQZ+mwzEZ6mPoYJbVr8VHA4x8yuXG30vZaadSx1w==");
  for (const [p, v] of Object.entries<any>(lock.packages)) if (p && !v.link) {
    expect(v.resolved, p).toMatch(/^https:\/\/registry\.npmjs\.org\//);
    expect(v.integrity, p).toMatch(/^sha512-/);
  }
});

// web/tests/checkDist.test.ts — фикстура пишет каталог во временный путь
test("check_dist_accepts_clean_build", () => expect(checkDist(fixture(CLEAN))).toEqual([]));
test("check_dist_rejects_inline_and_external", () => {
  for (const html of ['<script>alert(1)</script>', '<style>a{}</style>', '<div style="x">',
                      '<script type="module" src="https://cdn.example/x.js"></script>',
                      '<link rel="stylesheet" href="data:text/css,a">', '<script src="//x/y.js"></script>'])
    expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", html + "</head>") })).length, html).toBeGreaterThan(0);
});
test("check_dist_rejects_manifest_mismatch", () => {
  expect(checkDist(fixture({ ...CLEAN, extraFile: "assets/x.js" }))).toContainEqual(expect.stringMatching(/опись/));
  expect(checkDist(fixture({ ...CLEAN, badSum: true }))).toContainEqual(expect.stringMatching(/сумма/));
  expect(checkDist(fixture({ ...CLEAN, extraFile: "assets/index.js.map" }))).toContainEqual(expect.stringMatching(/\.map/));
});
```

- [ ] **Step 2: Install и прогон — FAIL.** `(cd web && npm install --ignore-scripts --save-exact <версии Tech Stack> && npm test)`.
  `npm install` — один раз, чтобы получить `package-lock.json`; дальше только `npm ci`.
  Expected: FAIL (`checkDist` не определён).
- [ ] **Step 3: Implement** по Д6-R40 и Interfaces. `tsconfig.json`: `strict`, `module: "esnext"`,
  `moduleResolution: "bundler"`, `target: "es2022"`, `jsx: "react-jsx"`, `noEmit`. Необязательные
  пиры `@vitejs/plugin-react` (`oxc-transform-react`, `@rolldown/plugin-babel`,
  `babel-plugin-react-compiler`) не ставятся. Если `npm ci` не сходится на закреплённых версиях —
  ближайшая совместимая, строка в `docs/checks/D6a.md` и решение контролёра.
- [ ] **Step 4: Run** — `(cd web && rm -rf node_modules && npm ci --ignore-scripts && npm test && PCBK_BUILD=dev npm run build && ls ../edge/static/chat)`.
  Expected: PASS; в каталоге — `index.html`, `build.json`, `assets/`; `check-dist` без нарушений.
  **Повторяемость сборки:** вторая сборка даёт тот же `build.json`
  (`sha256sum ../edge/static/chat/build.json` дважды совпал). Не совпало — вердикт в журнал.
  Тогда А7 сверяет суммы сервера с описью **этой** сборки, а не с пересобранной.
- [ ] **Step 5: Commit** — `git add web/ .gitignore && git commit -m "Веб: каркас сборки — закреплённые версии, lock, опись сборки, проверка статики на CSP" -- web/ .gitignore`.
  Проверка: `git ls-files web | /usr/bin/grep -c node_modules` → `0`.

---

### Task А3: Клиент шлюза, баннер, пробуждение места

Чистый TypeScript без React и без сети — всё через внедрённые `fetchImpl`, таймеры и часы.

**Files:**
- Create: `web/src/texts.ts`, `web/src/api.ts`, `web/src/gateway.ts`, `web/src/status.ts`,
  `web/src/workplace.ts`
- Test: `web/tests/gateway.test.ts`, `web/tests/status.test.ts`, `web/tests/workplace.test.ts`,
  `web/tests/texts.test.ts`

**Interfaces:**
- Produces (`gateway.ts`):
  - `type Outcome = "ok" | "signed_out" | "asleep" | "too_many_tabs" | "busy" | "down" | "refused" | "offline" | "unknown" | "halted"`;
  - `classify(status: number, body: unknown): Outcome` — таблица Д6-R43;
  - `explainError(error: unknown): { kind: Outcome; text: string }`:
    - `cause.status` и `cause.body` — от `wrapClientError` SDK;
    - `TypeError` или «network error (no response)» — `offline`;
    - `HaltedError` — `halted` с пустым текстом;
  - `eventDelayMs(failures: number, random: () => number): number`;
  - `EVENT_PATH = "/pcbk/api/oc/event"`; `class HaltedError extends Error`;
  - `interface StreamState { failures: number; nextRetryS: number | null; last: Outcome }`;
  - `createGatewayFetch(o: { onHalt(kind: "signed_out" | "asleep"): void; onStream(s: StreamState): void; fetchImpl?: (r: Request) => Promise<Response>; sleep?: (ms: number, signal: AbortSignal) => Promise<void>; random?: () => number }): (req: Request) => Promise<Response>`.
- Produces (`status.ts`):
  - `type Level = "ok" | "warn" | "fail" | "unknown"`;
  - `statusView(data: unknown, ownPlace: string | null): { level: Level; text: string }` — Д6-R44;
  - `startStatusPolling(o: { onView(v): void; ownPlace: string | null; fetchImpl?; nowMs?: () => number; setTimeoutImpl?; clearTimeoutImpl?; setIntervalImpl?; clearIntervalImpl?; pollMs?: number /* 10000 */; timeoutMs?: number /* 4000 */ }): () => void`
    — возвращает остановку. Раз в секунду пересчёт по часам, как `update()` в `status.js`.
    Ответ старого запроса после нового не применяется.
- Produces (`workplace.ts`):
  - `placeView(reply: unknown): { text: string; poll: boolean; ok: boolean }` — те же ветки и тексты,
    что `viewFor` в `edge/static/login.js`;
  - `wakeAndWait(o: { api: Api; onView(v): void; giveUpS?: number /* 90 */; pollMs?: number /* 1000 */; timers?; signal?: AbortSignal }): Promise<"ready" | "failed" | "gave_up" | "signed_out" | "aborted">`.
- Produces (`api.ts`):
  - `interface Reply { status: number; body: unknown }` (`status: 0` — сеть);
  - `callJson(method: "GET" | "POST", path: string, body?: unknown, fetchImpl?): Promise<Reply>` —
    `credentials: "same-origin"`, `Accept: application/json`, тело — только JSON;
  - `interface Api { me(): Promise<Reply>; logout(): Promise<Reply>; wake(): Promise<Reply>; workplace(): Promise<Reply> }`,
    `createApi(fetchImpl?): Api`.
- Produces (`texts.ts`):
  - все строки людям из Д6-R39…R44 и таблицы Д6-R43;
  - `STAND_WARNING`;
  - `MAX_TEXT = 20000`;
  - `TOOL_LABELS = { pcbk_tag_now: "текущее значение", pcbk_tag_period: "сводка за период", pcbk_catalog_search: "поиск тегов" }`.

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/gateway.test.ts
const sdkErr = (status: number, name: string, message = "текст шлюза") =>
  new Error(message, { cause: { status, body: { name, data: { message } } } });
const json = (status: number, name?: string) => new Response(name ? JSON.stringify({ name, data: { message: "т" } }) : "",
  { status, headers: { "content-type": "application/json" } });
const ev = () => new Request("https://s/pcbk/api/oc/event");

test("classify_table", () => {
  const T: [number, string | undefined, string][] = [[200, undefined, "ok"], [401, "Unauthorized", "signed_out"],
    [503, "WorkplaceNotReady", "asleep"], [429, "TooManyRequests", "too_many_tabs"], [503, "WorkplaceBusy", "busy"],
    [503, "Unavailable", "down"], [502, "BadGateway", "refused"], [403, "Forbidden", "refused"],
    [400, "BadRequest", "refused"], [429, "TooManyAttempts", "refused"], [404, "NotFound", "refused"],
    [413, "PayloadTooLarge", "refused"], [500, undefined, "unknown"]];
  for (const [s, n, k] of T) expect(classify(s, n ? { name: n, data: { message: "т" } } : "<html>"), `${s} ${n}`).toBe(k);
});
test("explain_error_texts", () => {
  expect(explainError(sdkErr(403, "Forbidden", "Вызов не разрешён"))).toEqual({ kind: "refused", text: "Вызов не разрешён" });
  expect(explainError(new TypeError("Failed to fetch")).kind).toBe("offline");
  expect(explainError(new Error("opencode server GET /x: network error (no response)")).kind).toBe("offline");
  expect(explainError(new Error("Request is not supported by this version of OpenCode Server"))).toEqual({ kind: "unknown", text: T_UNKNOWN });
  expect(explainError(new HaltedError()).kind).toBe("halted");
});
test("event_backoff_grows_and_resets", async () => {
  const waits: number[] = []; const codes = [503, 503, 503, 503, 503, 503, 503, 200, 503];
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), random: () => 0.5,
    sleep: async (ms) => { waits.push(ms); }, fetchImpl: async () => json(codes.shift()!, "WorkplaceBusy") });
  for (let i = 0; i < 9; i++) await f(ev());
  expect(waits).toEqual([0, 1000, 2000, 4000, 8000, 16000, 30000, 30000, 0]);   // 200 сбросил счёт
});
test("halt_stops_stream_and_rejects_calls", async () => {
  const fetchImpl = vi.fn(async () => json(503, "WorkplaceNotReady")); const onHalt = vi.fn();
  const f = createGatewayFetch({ onHalt, onStream: vi.fn(), fetchImpl, sleep: async () => {} });
  await f(ev());
  expect(onHalt).toHaveBeenCalledWith("asleep");
  const ctl = new AbortController(); const pending = f(new Request("https://s/pcbk/api/oc/event", { signal: ctl.signal }));
  await expect(f(new Request("https://s/pcbk/api/oc/session"))).rejects.toBeInstanceOf(HaltedError);
  ctl.abort(); await expect(pending).rejects.toThrow();
  expect(fetchImpl).toHaveBeenCalledTimes(1); expect(onHalt).toHaveBeenCalledTimes(1);
});
test("signed_out_on_any_call_halts_once", async () => { /* 401 на GET /session → onHalt("signed_out") один раз */ });
test("plain_calls_are_not_delayed", async () => { /* после трёх сбоев потока GET /session уходит без sleep */ });

// web/tests/status.test.ts
const st = (checks: object[], extra = {}) => ({ stale: false, stale_after_s: 30, checks, ...extra });
const c = (component: string, title: string, state: string) => ({ component, title, state, detail: "", note: "" });
test("status_view_filters_and_levels", () => {
  expect(statusView(st([c("edge", "Входной прокси", "ok")]), "03")).toEqual({ level: "ok", text: "Стенд работает" });
  expect(statusView(st([c("student-05", "Рабочее место 05", "fail")]), "03").level).toBe("ok");      // чужое место
  expect(statusView(st([c("student-03", "Рабочее место 03", "fail"), c("llm", "LLM-прокси и бюджет", "warn")]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Рабочее место 03" });
  expect(statusView(st([c("a", "А", "warn"), c("b", "Б", "warn"), c("c", "В", "warn"), c("d", "Г", "warn")]), "03").text)
    .toBe("Внимание: А, Б, В и ещё 1");
  expect(statusView(st([], { stale: true }), "03").level).toBe("unknown");
  expect(statusView("<html>", "03").level).toBe("unknown");
  expect(statusView(st([c("x", "X", "absent")]), "03").level).toBe("ok");
});
test("polling_goes_unknown_by_clock", async () => { /* свежий ответ → ok; дальше fetch висит; через 31 с часов → unknown */ });
test("late_answer_of_old_request_ignored", async () => { /* ответ №1 после ответа №2 не меняет вид */ });

// web/tests/workplace.test.ts
test("place_view_matches_login_page", () => { /* те же 6 случаев, что viewFor в edge/tests/login.test.mjs */ });
test("wake_and_wait_ready_after_polls", async () => { /* 202 sleeping → starting → ready → "ready", onView трижды */ });
test("wake_and_wait_signed_out_and_gave_up", async () => { /* 401 → "signed_out"; 90 с starting → "gave_up" */ });

// web/tests/texts.test.ts
test("stand_warning_same_as_login_page", () => {
  const html = readFileSync("../edge/static/login.html", "utf8");
  expect(html).toContain(`<div class="stand" role="note">${STAND_WARNING}</div>`);
});
test("max_text_same_as_gateway_rules", () => {
  expect(readFileSync("../core/pcbk_core/gateway/rules.py", "utf8")).toMatch(new RegExp(`^MAX_TEXT = ${MAX_TEXT}$`, "m"));
});
```

  Тела тестов с комментарием вместо кода пишет исполнитель — по названию и комментарию.
- [ ] **Step 2: Run** — `(cd web && npm test)`. Expected: FAIL (модулей нет).
- [ ] **Step 3: Implement по Interfaces.** Ответ не 2xx потока обёртка читает через
  `res.clone().json()` только при `content-type: application/json`. Иное — `unknown`.
- [ ] **Step 4: Run** — `(cd web && npm test)`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add web/ && git commit -m "Веб: клиент шлюза — классы отказов, паузы потока, баннер состояния, пробуждение места" -- web/`.

---

### Task А4: Интерфейс чата

**Files:**
- Create: `web/src/App.tsx`, `web/src/components/{Header,StatusBanner,WorkplaceScreen,Chat,ChatView,MessageParts,PendingInteractions}.tsx`,
  `web/src/chat.css`
- Modify: `web/src/main.tsx`
- Test: `web/tests/App.test.tsx`, `web/tests/ChatView.test.tsx`

**Interfaces:**
- Consumes: всё из А3; `useOpenCodeRuntime`, `createOpencodeClient`, `useOpenCodePermissions`,
  `useOpenCodeQuestions`, `useOpenCodeRuntimeExtras` из `@assistant-ui/react-opencode`;
  примитивы Д6-R41.
- Produces:
  - `App(p: { api: Api; navigate(url: string): void; ChatImpl?: ComponentType<ChatProps>; startPolling?: typeof startStatusPolling }): JSX.Element`.
    Фазы:
    - `loading`;
    - `teacher` — текст, без опроса мест и чата;
    - `workplace` — `WorkplaceScreen` с `wakeAndWait`;
    - `asleep` — «Рабочее место спит» и кнопка «Разбудить»;
    - `chat`.

    `signed_out` в любой фазе — `navigate("/login")`. «Выйти» — `api.logout()`, затем
    `navigate("/login")`. Шапка — во всех фазах, кроме `loading`.
  - `interface ChatProps { onHalt(kind: "signed_out" | "asleep"): void }`;
    `Chat(p: ChatProps)` — клиент, обёртка и рантайм на монтирование (Д6-R42, R43):
    - `onError` → `explainError` → строка ошибки над полем ввода (`halted` не показывается);
    - `onStream` → строка потока;
    - `PendingInteractions` из хуков адаптера;
    - кнопка «Новый разговор» (`ThreadListPrimitive.New`).
  - `ChatView(p: { runtime: AssistantRuntime; streamLine?: string; error?: string; pending?: ReactNode; toolbar?: ReactNode }): JSX.Element`
    — только отображение. Сообщения идут через
    `MessagePrimitive.Parts components={{ Text: MarkdownText, Reasoning: () => null, tools: { Fallback: ToolLine } }}`,
    `MessagePrimitive.Error` → `ErrorPrimitive.Message`.
  - `PendingInteractions(p: { permissions: { id: string; permission: string }[]; questions: { id: string }[]; onRejectPermission(id: string): void; onRejectQuestion(id: string): void })`.
  - `MarkdownText` — `MarkdownTextPrimitive` с `remarkPlugins: [remarkGfm]` и своим `a`: `href`
    только `^(https?:|/|#)`, иначе без `href`; `rel="noopener noreferrer"`, `target="_blank"`.
    `ToolLine` — Д6-R42.

- [ ] **Step 1: Write the failing tests**

```tsx
// web/tests/App.test.tsx — api и ChatImpl подменены; startPolling — заглушка, отдающая вид «ok»
test("signed_out_goes_to_login", async () => {
  const navigate = vi.fn(); render(<App api={api({ me: [401, { name: "Unauthorized" }] })} navigate={navigate} startPolling={okPolling} />);
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
});
test("teacher_gets_no_chat_and_no_wake", async () => {
  const a = api({ me: [200, { login: "t", role: "teacher", place: null }] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(await screen.findByText(/У преподавателя пока нет чата/)).toBeTruthy();
  expect(a.wake).not.toHaveBeenCalled(); expect(screen.queryByTestId("fake-chat")).toBeNull();
});
test("student_wakes_place_then_chat", async () => {
  const a = api({ me: [200, { login: "s", role: "student", place: "03" }], workplace: [[200, { state: "sleeping" }], [200, { state: "ready" }]],
                  wake: [202, { state: "starting" }] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(await screen.findByText("Готовлю рабочее место…")).toBeTruthy();
  expect(await screen.findByTestId("fake-chat")).toBeTruthy();
  expect(screen.getByText("Вы вошли как s")).toBeTruthy();
  expect(screen.getByRole("note").textContent).toBe(STAND_WARNING);
});
test("halt_asleep_shows_wake_button", async () => { /* FakeChat зовёт onHalt("asleep") → «Рабочее место спит», кнопка «Разбудить» → wake → чат снова */ });
test("logout_posts_then_navigates", async () => { /* «Выйти» → api.logout, затем navigate("/login") */ });

// web/tests/ChatView.test.tsx — runtime: useExternalStoreRuntime({ messages, isRunning, onNew, convertMessage })
test("chat_view_has_only_allowed_controls", () => {
  const { container } = render(<Harness messages={[user("вопрос"), assistant("ответ")]} running />);
  expect(screen.getByRole("button", { name: "Стоп" })).toBeTruthy();
  for (const name of [/Удалить/, /Изменить/, /Повторить/, /Ветка/, /Из архива/, /Прикрепить/, /Копировать/])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(container.querySelector('input[type="file"]')).toBeNull();
  expect(container.querySelector("textarea")!.getAttribute("maxlength")).toBe(String(MAX_TEXT));
});
test("renders_model_markup_inert", () => {
  const { container } = render(<Harness messages={[assistant('<img src=x onerror="alert(1)"> [клик](javascript:alert(1)) **ok**')]} />);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  expect(container.querySelector("strong")!.textContent).toBe("ok");
});
test("tool_line_and_truncated_output", () => {
  render(<Harness messages={[assistantTool("pcbk_tag_now", { tags: ["T1"] }, "x".repeat(10_000), "complete")]} />);
  expect(screen.getByText("Запрос к данным: текущее значение — готово")).toBeTruthy();
  expect(document.querySelector("details pre")!.textContent!.length).toBeLessThanOrEqual(2000 + 1);
});
test("message_error_visible", () => {
  render(<Harness messages={[assistantError("На счёте OpenRouter кончились деньги — модель не отвечает. Сообщите преподавателю.")]} />);
  expect(screen.getByText(/кончились деньги/)).toBeTruthy();
});
test("pending_interactions_reject_only", () => {
  const onRejectPermission = vi.fn();
  render(<PendingInteractions permissions={[{ id: "per_1", permission: "doom_loop" }]} questions={[]} onRejectPermission={onRejectPermission} onRejectQuestion={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /Разрешить/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
  expect(onRejectPermission).toHaveBeenCalledWith("per_1");
});
```

- [ ] **Step 2: Run** — `(cd web && npm test)`. Expected: FAIL.
- [ ] **Step 3: Implement по Interfaces.** `main.tsx`:
  `createRoot(root).render(<App api={createApi()} navigate={(u) => location.assign(u)} />)`,
  импорт `./chat.css`. Если в А6 браузер покажет нарушение CSP от пробы zod — сюда же первой строкой
  `z.config({ jitless: true })` (черта Д6а).
- [ ] **Step 4: Run** — `(cd web && npm test && PCBK_BUILD=dev npm run build)`. Expected: PASS, `check-dist` чист.
- [ ] **Step 5: Commit** — `git add web/ && git commit -m "Веб: интерфейс чата — шапка, место, разговор, ожидающие ответы агента" -- web/`.

---

### Task А5: Агент по умолчанию в образе места

**Files:**
- Create: `student/config/agent/pomoshnik.md`
- Modify: `student/config/opencode.json` (+ `default_agent`, `agent`)
- Test: `tests/integration/test_student_image.py` (`test_config_files` +, новый `test_default_agent_file`),
  `tests/integration/test_students.py` (+ `test_default_agent_is_pomoshnik`),
  `tests/integration/test_conversation.py` (правка двух тестов, + `test_default_agent_tools_are_data_only`),
  `tests/integration/conftest.py` (`converse(..., tools=)` уже есть — без правок)

**Interfaces:**
- Produces: Д6-R45. Файл агента — ровно так (фронтматтер сверяется тестом побайтно, текст — по
  ключевым правилам):

```markdown
---
# pcbk-reserve: агент по умолчанию, редакция 1 (Д6а); меняется только пересборкой образа
description: "Помощник по данным: текущие значения и сводки тегов БДРВ"
mode: primary
steps: 8
permission:
  "*": deny
  pcbk_catalog_search: allow
  pcbk_tag_now: allow
  pcbk_tag_period: allow
---
Ты — помощник по данным учебного стенда ПЦБК. С тобой говорит студент-технолог. Ты отвечаешь на вопросы
«что сейчас» и «что было за период» по тегам БДРВ (историана) и умеешь только три вещи: искать теги по
словам (pcbk_catalog_search), показывать текущее значение (pcbk_tag_now) и сводку за период — среднее,
минимум, максимум, размах (pcbk_tag_period).

ПРАВИЛА
- Числа бери только из ответов этих инструментов в этом разговоре. В ответе всегда: число с единицей,
  тег, время значения или период — как их вернул инструмент.
- Имя тега бери из слов студента или из ответа pcbk_catalog_search. Не составляй имя тега сам. Если
  похожих тегов несколько — покажи список «тег — описание — единица» и попроси выбрать.
- Окончания: _PV — фактическое значение, _SP — задание регулятора. Смысл _MV у регуляторов не
  установлен — не толкуй его, покажи описание тега.
- Нормативов и норм режима в данных нет — не называй их. Границы шкалы прибора — не норма. Если студент
  спрашивает норму, так и скажи; общие знания помечай словами «общие знания, не из данных стенда».
- Если служба ответила отказом (тега нет в списке, нет данных, историан занят) — скажи это как есть.
- Отвечай по-русски и коротко; не обещай того, чего не умеешь.
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_student_image.py (+; test_config_files: набор ключей + "default_agent", "agent")
FRONT = ('# pcbk-reserve: агент по умолчанию, редакция 1 (Д6а); меняется только пересборкой образа\n'
         'description: "Помощник по данным: текущие значения и сводки тегов БДРВ"\nmode: primary\nsteps: 8\n'
         'permission:\n  "*": deny\n  pcbk_catalog_search: allow\n  pcbk_tag_now: allow\n  pcbk_tag_period: allow\n')

def test_default_agent_file():
    cfg = json.loads((CONFIG / "opencode.json").read_text())
    assert cfg["default_agent"] == "pomoshnik"
    assert cfg["agent"] == {n: {"disable": True} for n in ("build", "plan", "general", "explore")}
    text = (CONFIG / "agent" / "pomoshnik.md").read_text()
    _, front, body = text.split("---\n", 2)
    assert front == FRONT                                                # ни name, ни model, ни question
    for rule in ("только из ответов этих инструментов", "Не составляй имя тега сам", "не называй их",
                 "Смысл _MV у регуляторов не", "общие знания, не из данных стенда"):
        assert rule in body, rule
    assert sorted(p.name for p in (CONFIG / "agent").iterdir()) == ["pomoshnik.md"]

# tests/integration/test_students.py (+)
def test_default_agent_is_pomoshnik(stack, running):
    agents = json.loads(stack.oc(1, "GET", "/agent", 1)[1])
    primary = {a["name"] for a in agents if not a.get("hidden") and a.get("mode") != "subagent"}
    assert primary == {"pomoshnik"}
    p = next(a for a in agents if a["name"] == "pomoshnik")
    assert (p["native"], p["mode"]) == (False, "primary")
    assert not {"build", "plan", "general", "explore"} & {a["name"] for a in agents}

# tests/integration/test_conversation.py
PCBK3 = {"pcbk_catalog_search", "pcbk_tag_now", "pcbk_tag_period"}
def test_default_agent_tools_are_data_only(stack, place01):
    out = stack.converse(1, Q)
    assert tool_names(out["session"]) - {"invalid"} == PCBK3          # «invalid» — служебный, если OpenCode его шлёт
    msgs = json.loads(stack.oc(1, "GET", f"/session/{out['session']}/message", 1)[1])
    assert {m["info"].get("agent") for m in msgs if m["info"]["role"] == "assistant"} == {"pomoshnik"}
# test_conversation_without_tools_reads_no_data: «"read" in names» → «tool_names(control) - {"invalid"} == PCBK3»
#   и «not names & PCBK3»; прочее без изменений.
# test_model_question_is_rejected_and_reported: stack.converse(1, "FAKEASK какой тег?", timeout=60,
#   tools={"question": True}) — правило сессии даёт question (converse идёт мимо шлюза); ожидания прежние.
```

  `tool_names` поднимается из тела `test_conversation_without_tools_reads_no_data` на уровень модуля.
- [ ] **Step 2: Run** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py`. Expected: FAIL.
- [ ] **Step 3: Implement** — файл, ключи `opencode.json`. Снимок ID откатных образов до сборки;
  фикстура собирает `student:d6a` (тег — А0).
- [ ] **Step 4: Run** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py tests/integration/test_students.py tests/integration/test_conversation.py`.
  Expected: PASS; прочие тесты `test_conversation` (FAKELOOP — 16 вызовов, бюджет) зелёные без
  правок; ID `student:d4` не изменился.
- [ ] **Step 5: Commit** — `git add student/ tests/integration/ && git commit -m "Место: агент по умолчанию pomoshnik, встроенные агенты выключены" -- student/ tests/integration/test_student_image.py tests/integration/test_students.py tests/integration/test_conversation.py`.

---

### Task А6: `edge` `/chat/`, вход → чат, компоновка и автотесты в браузере

**Files:**
- Modify:
  - `edge/pcbk.conf.template` (8443: `location = /chat`, `= /chat/`, `^~ /chat/assets/`, `^~ /chat/`);
  - `edge/static/login.{html,js}`, `edge/static/index.html`;
  - `tests/integration/conftest.py` (сборка веба в фикстуре `stack`, фикстура `browser`);
  - `tests/integration/test_edge.py`;
  - `deploy/README.md` («Статика чата»).
- Create: `tests/integration/cdp.py`, `tests/integration/test_web.py`
- Test: `edge/tests/login.test.mjs` (+2)

**Interfaces:**
- Consumes: сборка А2 (`edge/static/chat/`), строка сторожа А1, образ места А5.
- Produces:
  - `edge` — Д6-R39, R41. На `= /chat/` и `^~ /chat/assets/` — CSP и три заголовка `server`
    повторены (как у `location = /login`), `charset utf-8` у `index.html`; `^~ /chat/` на 8443 —
    `return 404`.
  - `login.js`: `startPage({ …, navigate = (u) => location.assign(u) })`. В `showPlace` при
    `view.ok` — показ `#to-chat` и `navigate("/chat/")` ровно один раз за ход ожидания. У
    преподавателя перехода нет.
  - Фикстура `stack` перед `compose up`:
    `npm --prefix web ci --ignore-scripts && PCBK_BUILD=test-$(git rev-parse --short=12 HEAD) npm --prefix web run build`.
  - `cdp.py`: `class Browser` (контекстный менеджер) запускает `google-chrome --headless=new
    --remote-debugging-port=0 --user-data-dir=<tmp> --ignore-certificate-errors
    --host-resolver-rules="MAP stand.pcbk.test 127.0.0.1" --no-first-run --no-default-browser-check`.
    Адрес отладки — из `DevToolsActivePort`. Клиент WebSocket — stdlib, только `ws://127.0.0.1`,
    текстовые кадры, длина до 64 бит. `Browser.page() -> Page`.
  - `Page`: `goto(url)`, `url() -> str`, `type(selector, text)`, `press_enter(selector)`,
    `click_text(text)`, `wait_text(pattern, timeout) -> str`, `eval(expr)`, `screenshot(path)`,
    `csp() -> list[dict]`, `console_errors() -> list[str]`,
    `requests(prefix) -> list[dict(method, path, status, protocol, headers)]`,
    `chunks(path) -> int`. CSP-нарушения собирает слушатель `securitypolicyviolation`, вставленный
    через `Page.addScriptToEvaluateOnNewDocument`. Сеть — из `Network.requestWillBeSent`,
    `requestWillBeSentExtraInfo` (настоящие заголовки), `responseReceived`, `dataReceived`.
  - Фикстура `browser`: без `google-chrome` — `pytest.skip("нет google-chrome")`.
  - Модуль `test_web.py`: учётки `web-a` (студент, место 9) и `web-t` (преподаватель) —
    `stack.add_account`; место 09 гасится в `finally`.

- [ ] **Step 1: Write the failing tests**

```js
// edge/tests/login.test.mjs (+)
test('ready_navigates_to_chat_once', async () => { /* wake 202 starting → poll ready → navigate('/chat/') 1 раз, #to-chat видим */ });
test('teacher_never_navigates', async () => { /* role teacher → navigate не вызван */ });
```

```python
# tests/integration/test_edge.py (+)
CHAT_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
            "font-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
def test_edge_serves_chat_under_csp(stack):
    build = json.loads((REPO / "edge/static/chat/build.json").read_text())
    code, body, h = stack.https_full("/chat/")
    assert (code, h["content-security-policy"], h["cache-control"], h["x-content-type-options"]) == (200, CHAT_CSP, "no-store", "nosniff")
    assert f'<meta name="pcbk-build" content="{build["build"]}">' in body
    for path in build["files"]:
        code, _, h = stack.https_full("/chat/" + path)
        assert code == 200 and h["content-security-policy"] == CHAT_CSP
        assert h["content-type"].split(";")[0] in ("application/javascript", "text/css"), path
    assert stack.https_full("/chat/build.json")[0] == 404                  # опись — только 8080
    code, _, h = stack.https_full("/chat")
    assert (code, h["location"]) == (301, "/chat/")
    assert stack.http_in("pcbk-front", "http://pcbk-edge:8080/chat/build.json") == 200   # одноразовый curl в сети сторожа
```

  `https_full` — если нужно, добавляется рядом с `stack.https` (код, тело, заголовки в нижнем
  регистре). `http_in(network, url) -> int` — одноразовый `curl` в сети, по образцу `http_as`.

```python
# tests/integration/test_web.py — место 09; STAND = "https://stand.pcbk.test:18443"
Q = "что сейчас по тегу 20FAKE_001_PV?"

def login_to_chat(page, login, password):
    page.goto(f"{STAND}/login"); page.type("#login", login); page.type("#password", password); page.press_enter("#password")
    page.wait_text(r"Вы вошли как " + login, 90)
    assert page.url().endswith("/chat/")

def test_chat_answers_through_gateway(stack, browser, web_users):                          # Review Focus 1, 4
    before = stack.gw_health()["refused_1h"]
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    assert page.wait_text(re.escape(STAND_WARNING), 5) and page.wait_text(r"Стенд работает|Внимание|Сбой", 20)
    page.type("textarea", Q); page.press_enter("textarea")
    answer = page.wait_text(r"Готово: .+", 120)
    assert page.wait_text(r"Запрос к данным: текущее значение — готово", 5)
    sid = last_session(stack, 9)                                                           # последний разговор места 09
    tool = tool_output(stack, 9, sid, "pcbk_tag_now")                                      # вывод инструмента из места
    assert tool[:40] in answer                                                             # ответ построен на выводе (нет историана)
    assert page.csp() == [] and page.console_errors() == []
    assert stack.gw_health()["refused_1h"] == before
    assert all(allowed(r["method"], r["path"]) for r in page.requests("/pcbk/api/oc/"))    # rules.match — те же 15 правил

def test_browser_headers_and_h2_stream(stack, browser, web_users):                        # хвост Д5б [?]
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    posts = [r for r in page.requests("/pcbk/api/") if r["method"] == "POST"]
    assert posts and all(r["headers"].get("sec-fetch-site") == "same-origin" for r in posts)
    assert all(r["headers"].get("origin") == STAND for r in posts)                         # при Referrer-Policy no-referrer
    ev = [r for r in page.requests("/pcbk/api/oc/event") if r["status"] == 200]
    assert ev and ev[-1]["protocol"] == "h2"
    n0 = page.chunks("/pcbk/api/oc/event"); time.sleep(35)
    assert page.chunks("/pcbk/api/oc/event") - n0 >= 3                                     # пульс 10 с идёт через edge

def test_place_asleep_shows_wake_and_goes_quiet(stack, browser, web_users):               # Review Focus 2
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    stack.stop("pcbk-student-09")
    page.wait_text(r"Рабочее место спит", 90)
    n = len(page.requests("/pcbk/api/oc/")); time.sleep(30)
    assert len(page.requests("/pcbk/api/oc/")) - n == 0                                    # остановлен до действия студента
    page.click_text("Разбудить"); page.wait_text(r"Готовлю рабочее место…|Вы вошли как", 10)
    page.type("textarea", Q); page.press_enter("textarea"); page.wait_text(r"Готово: .+", 120)

def test_signed_out_goes_to_login(stack, browser, web_users):                             # Review Focus 2
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    web_users["web-a"] = stack.passwd("web-a")                                             # passwd закрывает сессии
    wait_for(lambda: page.url().endswith("/login"), timeout=45)
    n = len(page.requests("/pcbk/api/oc/event")); time.sleep(10)
    assert len(page.requests("/pcbk/api/oc/event")) == n

def test_banner_follows_watchdog(stack, browser, web_users):                              # Review Focus 5
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    stack.docker_stop("pcbk-watchdog")
    try:
        page.wait_text(r"Состояние стенда неизвестно — сторож не отвечает", 30 + 15)
    finally:
        stack.docker_start("pcbk-watchdog")
    page.wait_text(r"Стенд работает|Внимание|Сбой", 60)

def test_model_error_visible_in_chat(stack, browser, web_users):
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    page.type("textarea", "FAKE402 что сейчас?"); page.press_enter("textarea")
    page.wait_text(r"На счёте OpenRouter кончились деньги", 90)

def test_teacher_sees_no_chat(stack, browser, web_users):
    page = browser.page(); page.goto(f"{STAND}/login")
    page.type("#login", "web-t"); page.type("#password", web_users["web-t"]); page.press_enter("#password")
    page.goto(f"{STAND}/chat/"); page.wait_text(r"У преподавателя пока нет чата", 20)
    assert page.requests("/pcbk/api/oc/") == []

def test_web_row_and_drill(stack):                                                        # учение строки «Веб-чат»
    build = json.loads((REPO / "edge/static/chat/build.json").read_text())
    stack.wait_status(lambda d: row(d, "web") == ("ok", "страница чата отдаётся", f"сборка {build['build']}"), timeout=60)
    asset = REPO / "edge/static/chat" / next(p for p in build["files"] if p.endswith(".css"))
    hidden = asset.with_suffix(".hidden")
    asset.rename(hidden)
    try:
        stack.wait_status(lambda d: row(d, "web")[:2] == ("fail", "сборка чата неполная"), timeout=60)
    finally:
        hidden.rename(asset)
    stack.wait_status(lambda d: row(d, "web")[0] == "ok", timeout=60)
```

  Помощники:
  - `STAND_WARNING` — текст `.stand` из `edge/static/login.html` (читается модулем, не копируется);
  - `stack.gw_health()` — `/health/gateway` изнутри `pcbk-watchdog`, как `http_front`;
  - `stack.passwd(login)` — новый пароль в stdin команды `passwd`, возвращает его;
  - `stack.docker_stop` и `stack.docker_start`;
  - `last_session`, `tool_output`, `allowed` (через `pcbk_core.gateway.rules.match` в
    `gateway_cases.py`), `row`, фикстура `web_users` — в модуле или `conftest.py`.

  Учения — последними в модуле.
- [ ] **Step 2: Run** — `node --test edge/tests/*.test.mjs && uv run --python 3.12 --with pytest pytest -q tests/integration/test_edge.py tests/integration/test_web.py`.
  Expected: FAIL.
- [ ] **Step 3: Implement по Interfaces.** В `index.html` ссылка «Чат» ведёт на `/chat/`, текст
  «Чат с агентами и калькуляторы появятся здесь позже.» уходит. В `deploy/README.md` — раздел
  «Статика чата»:
  - сборка из тега в `$JOB`;
  - копия в `edge/static/chat.new`, сверка сумм `build.json`, переименование;
  - откат — вернуть `chat.<прошлый день>`.
- [ ] **Step 4: Полный прогон** — снимок ID откатных образов, затем
  `(cd web && npm test) && node --test edge/tests/*.test.mjs && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs) && (cd core && CORE_PYTEST) && uv run --python 3.12 --with pytest pytest -q tests/integration`,
  потом снимок ID снова. Expected:
  - PASS, в `test_web` ноль пропусков;
  - ID `watchdog:d5b`, `core:d5b`, `student:d4` до и после совпадают;
  - настенное время прогона записано в `docs/checks/D6a.md`.
- [ ] **Step 5: Снимки для владельца (локальный стенд, тестовые данные).** Тем же `cdp.py` из
  `$JOB`:
  - `01-chat-answer.png` — ответ и строка вызова;
  - `02-banner-unknown.png` — сторож остановлен;
  - `03-place-asleep.png` — «Разбудить»;
  - `04-status-web-row.png` — строка «Веб-чат».

  Файлы кладутся в `docs/checks/D6a/`.
- [ ] **Step 6: Commit** (после проверки на секреты) — `git add edge/ tests/integration/ deploy/README.md docs/checks/ && git commit -m "Веб-чат в компоновке: edge /chat/ под CSP, вход ведёт в чат; автотесты в браузере" -- edge/ tests/integration/ deploy/README.md docs/checks/`.

---

### Task А7: Выкладка

**Files:**
- Modify: `docs/checks/D6a.md`, `deploy/README.md` (если порядок разошёлся с планом)

- [ ] **Step 1: Сборка из чистого дерева ветки дня.**
  - `git worktree add $JOB/wt-d6a d6a/web-chat`;
  - образы `pcbk-reserve/watchdog:d6a` и `pcbk-reserve/student:d6a` — из `git archive` этого
    дерева;
  - веб — `git archive HEAD web edge | tar -x -C $JOB/build-d6a`, затем
    `cd $JOB/build-d6a/web && npm ci --ignore-scripts && PCBK_BUILD=$(git -C $JOB/wt-d6a rev-parse --short=12 HEAD) npm run build`.

  Expected: `check-dist` чист; `build.json` сохранён в `$JOB`.
- [ ] **Step 2: На сервер.**
  - образы — `docker save … | gzip | ssh … 'gunzip | docker load'`, слои сверены (`RootFS`);
  - копия для отката: `cp -p compose.yaml compose.yaml.d5b`, `cp -p edge/pcbk.conf.template edge/pcbk.conf.template.d5b`;
  - `scp` `compose.yaml`, `edge/pcbk.conf.template`, `edge/static/{login.html,login.js,index.html}`;
  - статика чата — в `edge/static/chat.new`;
  - на сервере сверка сумм по описи:
    `python3 -c 'import hashlib,json,sys,pathlib; d=pathlib.Path(sys.argv[1]); b=json.loads((d/"build.json").read_text()); bad=[p for p,s in b["files"].items() if hashlib.sha256((d/p).read_bytes()).hexdigest()!=s]; print(b["build"], "ok" if not bad else bad)' edge/static/chat.new`;
  - затем `mv edge/static/chat.new edge/static/chat` (прежнего нет — первая выкладка) и
    `chmod -R o+rX edge`.

  Expected: печать `<build> ok`; `build` совпал с локальным.
- [ ] **Step 3: Службы по порядку.**
  1. `docker compose up -d --no-build watchdog` — сторож знает вид `web`, иначе он не
     стартует с новым `components.json`; дождаться `stale: false`.
  2. `docker compose restart edge` — шаблон перечитывается только при старте.
  3. Места — README «Пересоздание мест с сохранением томов»: сначала список `running`; если
     место кто-то держит — ждать конца. Затем `create --force-recreate --no-build` всех десяти.

  Expected:
  - строки «Веб-чат» (`ok`, «сборка <build>»), «Входной прокси», «Шлюз и вход» — без новых сбоев;
  - места спят, образ у всех `student:d6a`;
  - `docker volume ls | /usr/bin/grep -c pcbk-student-` — как до выкладки;
  - Dify не перезапускался (время работы из снимка А0).
- [ ] **Step 4: Commit** (после проверки на секреты) — `git add docs/checks/D6a.md deploy/README.md && git commit -m "Выкладка Д6а: чат статикой edge, строка «Веб-чат», агент по умолчанию в местах" -- docs/checks/D6a.md deploy/README.md`.

---

### Task А8: Живые проверки, учения и окно владельца

Итог каждого шага — вердикт [П] в `docs/checks/D6a.md`, без паролей, cookie, адресов и тегов.
Туннель — `$SSH -o ExitOnForwardFailure=yes -N -L 28443:127.0.0.1:8443`.

Браузер — `cdp.py` из `$JOB`: `--host-resolver-rules="MAP ai-lab.pcbk.ru 127.0.0.1"`,
адрес `https://ai-lab.pcbk.ru:28443`, `--ignore-certificate-errors`. Сертификат истёк: строку
«Входной прокси — сбой» до замены не считать провалом проверок (напоминание 01.10).

- [ ] **Step 1: Пробная учётка `d6-probe-a` (место 10)** — как А8, шаг 1 Д5а: `add --generate`,
  файлы `0600`, `mkforms` одной цепочкой с контролями.
  Expected: всё, что требует Global Constraints Д5.
- [ ] **Step 2: Чат на сервере без денег.** На время шага — `DRILL_LLM=402` (Д4а-R15; строка
  «LLM-прокси и бюджет» — «учения»). Вход → чат → вопрос «Что сейчас?».
  Expected:
  - в ответе «На счёте OpenRouter кончились деньги…»;
  - `csp == []`;
  - `POST` под `/pcbk/api/` несут `sec-fetch-site: same-origin`, `Origin` записан;
  - поток `/event` — `h2`, пульсы через `edge` не меньше трёх за 35 с;
  - `refused_1h` не вырос;
  - снимок `05-live-chat-402.png`.

  Затем `DRILL_LLM` снят, строка вернулась в норму.
- [ ] **Step 3: Учение строки «Веб-чат».** На сервере `mv` одного файла `assets/*.css` в
  `*.hidden`.
  Expected:
  - за два такта — «Веб-чат — сбой — сборка чата неполная», снимок `06-web-row-fail.png`;
  - вернуть файл — строка `ok`, снимок `07-web-row-ok.png`;
  - время от переименования до красной строки — в журнал.
- [ ] **Step 4: Учение баннера.** Чат открыт, затем `docker compose stop watchdog`.
  Expected:
  - за `stale_after_s` + 15 с баннер «Состояние стенда неизвестно — сторож не отвечает», снимок
    `08-banner-unknown.png`;
  - `docker compose up -d --no-build watchdog` — баннер вернулся.
- [ ] **Step 5: Окно владельца — число из БДРВ (главный видимый результат).**
  1. Тег называет владелец. Вопрос «Что сейчас по тегу <тег>?» — в чат пробы на месте 10.
  2. Сразу за ответом:
     - вывод `pcbk_tag_now` хода — из места помощником `oc` Д2
       (`GET /session/<id>/message`, id — из страницы);
     - `tag_now` того же тега через туннель токеном `ops` — в `$JOB/direct.json`.
  3. Сверка — правило Д6-R47 (`python3` в `$JOB`).

  Expected:
  - владелец видит ответ в браузере: снимок `live-number.png` — только в `$JOB` и владельцу;
  - в журнал — «совпало / не совпало», статус инструмента, число обращений к модели, расход хода
    по `llmrows` (< $0,10);
  - не совпало — не прикрывается: вердикт и разбор по выводу инструмента.
- [ ] **Step 6: Уборка пробы.** README «Освобождение места пробной учётки»: разговоры пробы — по
  списку id, записанному шагами 2 и 5, затем сверка `[]`, `disable`, `remove`, `release-place`,
  `list`. Expected: у места 10 закрепления нет, разговоров пробы нет.
- [ ] **Step 7: Commit** (после проверки на секреты) — `git add docs/checks/D6a.md docs/checks/D6a/ && git commit -m "Д6а: живые проверки — чат через edge и HTTP/2, учения строки и баннера, число из БДРВ" -- docs/checks/D6a.md docs/checks/D6a/`.

---

### Task А9: Закрытие дня

- [ ] **Step 1: Документы.**
  - `docs/DESIGN-platform-2026-09-29.md`: §1 п. 2 — веб как построен; §8 — баннер и строка
    «Веб-чат» [П]; §13 — п. «Д6а» (Д6-R39…R48).
  - `README.md`: «Д6а готов», дальше Д6б.
  - В «Отклонения» дорожной карты — только то, что разошлось живьём.
- [ ] **Step 2: Критик** (Opus 5.5). Блокер — «Блокер дня» дорожной карты и для Д6а:
  - студент получил чужой разговор или вызов вне белого списка прошёл (неуспех 1, 3);
  - в собранной статике — секрет, адрес сервера или имя тега заказчика;
  - CSP ослаблен без решения владельца;
  - чат выложен без строки «Веб-чат»;
  - снимок с данными заказчика в git;
  - шторм переподключений (Review Focus 2) не закрыт тестом.

  Петля — до нуля блокеров, число раундов владелец не ограничил.
- [ ] **Step 3: Слияние — после `platform-d5b`.** Перебазирование на `main`, повтор А6, шаг 4 (с
  ID образов), слияние в `main`, тег `platform-d6a`. Перед пушем:
  - проверка на секреты по ветке;
  - `git ls-files | /usr/bin/grep -cE '(\.cred|\.pw|core\.db|core-tokens|llm-tokens|\.(llm-token|data-token)|openrouter.*\.key|live-.*|edge/static/chat/)'` → `0`.

  Затем `git push origin main platform-d6a` и чистый клон: веб (`npm ci`, `npm test`,
  `npm run build`), `CORE_PYTEST`, сторож, `node --test edge/tests/*.test.mjs`; снимки на месте.
  На сервере удалить `~/pcbk-d6a-*.txt`.
- [ ] **Step 4: План и факт** — две строки в `docs/checks/D6a.md`, помощники Д4а:
  - темп кода до черты — `pace "$(msg 'Сторож: строка «Веб-чат»')" "$(msg 'Веб-чат в компоновке')" 11.0`
    (задачи А2–А6);
  - полное время дня — справочно.
- [ ] **Step 5: Владельцу** — «Д6а готов»:
  - «совпало» по числу;
  - снимки `01`–`08` (из них `05`–`08` — с сервера);
  - адрес чата через туннель.

  Вопросы — строки 3–4 раздела «Что нужно от владельца», если ответа нет.

---

# Д6б. Предупреждение, молчание, разговоры

**Goal Д6б:**
- **Первый вход.** Студент при первом входе видит, что разговоры и агентов видит преподаватель, и
  подтверждает это. Без подтверждения чат не открывается. Подтверждение хранится в `core.db`, и
  администратор видит его в `accounts list`.
- **Молчание.** Браузер сам сообщает шлюзу о молчании:
  - ход без ответа 3 минуты;
  - сбой интерфейса;
  - потеря связи больше 2 минут.

  Строка «Веб-чат» желтеет с числом за час.
- **Разговоры.** В чате — список разговоров с «в архив» и «переименовать».

**Предпосылка.** Ветка `d6b/web-notice` — от головы `d6a/web-chat`. Код Б1–Б4 — без сервера;
Б0, шаг 2, и Б5–Б7 — после `platform-d6a`.

**Оценка по часам.**

| Задача | Часы | Где |
|---|---|---|
| Б0. Теги первой правкой; хвосты Д6а; утро | 0,5 | локально, сервер |
| Б1. Отчёты браузеров и строки сторожа (наблюдение до функций) | 1,75 | локально |
| Б2. Предупреждение при первом входе: `core.db`, ручка, экран | 1,5 | локально |
| Б3. Молчание в браузере; список разговоров — архив и имя | 2,0 | локально |
| Б4. `edge`, компоновка, автотесты в браузере и учения | 2,0 | локально |
| Б5. Выкладка | 0,5 | сервер |
| Б6. Живые проверки и учения | 1,25 | сервер |
| Б7. Закрытие дня | 1,25 | — |
| **Критический путь** | **10,75** | |

**Черта отсечения — 4,0 ч от начала дня.** К черте должны быть зелёны Б0–Б4: 7,75 ч по шкале,
≈2,3 ч при 0,30. Черта ловит темп хуже ≈0,52. После черты — Б5 → Б6 → Б7.

Если Б4 не зелёна к черте — выкладки нет. Видимый результат — снимки локального стенда
(предупреждение, строка с отчётами). Владельцу — строка о сдвиге Д7 на полдня.

## Решения по умолчанию Д6б (Ruling)

**Д6-R49 — предупреждение при первом входе.**
- `NOTICE = "teacher-reads-1"`. Текст (`texts.ts`): «Преподаватель видит ваши разговоры и агентов
  на этом стенде (только чтение). Не пишите сюда пароли и личные данные.», кнопка «Понятно».
- Таблица `gw_notices(login TEXT, notice TEXT, acked_at REAL, PRIMARY KEY (login, notice))` —
  `CREATE TABLE IF NOT EXISTS`, без переделки `gw_accounts`: `core.db` Д5 открывается как есть.
- `GET /pcbk/api/auth/me` добавляет `"notice"`: `NOTICE`, если студент его не подтвердил;
  иначе и у преподавателя — `null`. Ответ входа (`Account.to_json`) не меняется.
- `POST /pcbk/api/auth/notice` с телом ровно `{"notice": NOTICE}`:
  - нужны сессия и `same_origin`;
  - ответ 204, повторное подтверждение — тоже 204;
  - иной `notice` — 400 `bad_body`;
  - строка журнала `gw notice ack login=… notice=…`.
- `accounts list` — столбец «предупреждён» (`да`/`нет`).
- Новая редакция текста — новый `NOTICE`: студент подтверждает снова.
- Чат не монтируется и место не будится, пока `notice` не `null`: это фаза `notice` перед
  `workplace`.

**Д6-R50 — отчёты браузеров.**
- `POST /pcbk/api/web/report` с телом ровно `{"kind": K}`, `K ∈ {"ui_crash", "turn_silent", "stream_down"}`:
  - нужны сессия и `same_origin`;
  - тело ≤ 256 байт (`edge` — `client_max_body_size 1k`, шлюз — сам);
  - ответ 204; иной `kind` или лишнее поле — 400.
- Счётчик растёт не чаще раза в `WEB_REPORT_EVERY_S = 60` на пару (логин, вид). Сверх — 204 без
  счёта, строка журнала одна.
- Окно счётчиков — час, как у `GatewayStats`. Журнал — `gw web report login=… kind=…`, без
  свободного текста.
- `GET /health/web` — только зона `front`, как `/health/gateway` (Д5-R31):
  `{"ui_crash_1h": int, "turn_silent_1h": int, "stream_down_1h": int}`.
- `edge`: `location = /pcbk/api/web/report` — `limit_req` своей зоной `pcbk_web` 1 r/s, `burst=10`;
  проверка сырого пути как у прочих `location` шлюза. Список путей шлюза (Global Constraints Д5)
  дополняется этим путём.
- Строка «Веб-чат» — к компоненту добавляется `"reports_url": "http://pcbk-core:8000/health/web"`.
  После проверок статики Д6-R46, если статика в порядке:

  | Ответ `/health/web` | Итог | `note` |
  |---|---|---|
  | нет ответа, не 200 или не по договору | `ok` «страница чата отдаётся» | «сборка X; отчёты браузеров недоступны» |
  | `ui_crash_1h > 0` | `warn` «чат падает в браузере» | «k за час» |
  | `turn_silent_1h > 0` | `warn` «ответ модели не пришёл вовремя» | «k за час» |
  | `stream_down_1h > 0` | `warn` «браузеры теряют связь с сервером» | «k за час» |

  Порядок строк — порядок проверки. Старый `core` (404) строку не красит: порядок выкладки
  сторожа и `core` не важен.

**Д6-R51 — молчание в браузере (`web/src/watch.ts`).**
- **`turn_silent`.** После успешной отправки идёт отсчёт `TURN_SILENT_S = 180` по часам
  `Date.now()`, а не таймером: фоновая вкладка троттлит таймеры. Любой прирост содержимого
  последнего ответа ассистента (текст, рассуждение, вызов инструмента) отсчёт сбрасывает;
  конец хода (`isRunning` → `false`) снимает. Истёк — отчёт (один на ход) и строка «Ответа нет
  уже 3 минуты. Можно нажать «Стоп» и спросить снова — сбой отмечен для администратора.».
- **`ui_crash`.** `ErrorBoundary` вокруг чата: отчёт, текст «Чат столкнулся с ошибкой. Сбой
  отмечен для администратора.» и кнопка «Перезапустить чат» (перемонтирование).
- **`stream_down`.** Поток не открыт дольше `STREAM_DOWN_S = 120` подряд, при смонтированном
  чате. Отчёт уходит **при восстановлении** потока: пока связи нет, отчёт не дойдёт. Один
  отчёт на эпизод.
- Отчёт — `api.report(kind)`. Ответ отчёта не показывается, ошибка отчёта не повторяется.

**Д6-R52 — список разговоров.**
- Слева — `ThreadListPrimitive.Items` без архивных, «Новый разговор» и у элемента:
  - «В архив» — `ThreadListItemPrimitive.Archive`;
  - «Переименовать» — поле в строке. `ThreadListItemRuntime.rename(title)`
    (`@assistant-ui/core@0.3.21`, `runtime/api/thread-list-item-runtime.ts:110`), 1–200 знаков,
    пустое и длиннее не отправляются.
- «Из архива» и «Удалить» нет: `time.archived: null` место 1.18.33 не принимает (Ш13), удаление
  закрыто шлюзом.
- Заголовок разговора — от OpenCode. После его появления обновить список —
  `experimental.session.list` при `session.updated`, если адаптер этого сам не делает (research/07
  §9, проверка в Б4).

## Review Focus Д6б

1. **Предупреждение обходится.**
   - Чат монтируется до подтверждения.
   - Подтверждение приходит `GET`-ом, с чужим `Origin` или без сессии.
   - Подтверждают за другую учётку.
   - Новая редакция текста не спрашивается.

   Тесты — задача Б2, `test_notice_ack_rules`, `notice_blocks_chat_until_ack`; задача Б4,
   `test_first_login_notice`.
2. **Ручка отчётов как канал шума.** Произвольный `kind`, свободный текст, тело на 1 МБ,
   сотня отчётов в минуту, отчёт без сессии. Ожидание: считаются только три вида, не чаще раза в
   минуту на пару, тело до 256 байт, в журнале нет ввода клиента. Тест — задача Б1,
   `test_web_report_rules`.
3. **Ложное «ответа нет».** Долгий ход с вызовами инструментов, рассуждение без текста, вкладка в
   фоне с троттлингом таймеров. Или наоборот: отчёт не приходит никогда. Тест — задача Б3,
   `turn_watch_*`.
4. **Список разговоров.** Пустое имя, 201 знак, архив открытого разговора, «из архива» → 400
   места. Тесты — задача Б3, `sidebar_*`; задача Б4, `test_rename_and_archive_through_gateway`.
5. **Смешанные версии при выкладке.** `core.db` Д5 без `gw_notices`; сторож `:d6b` при `core :d5b`
   (`/health/web` 404); `core :d6b` при стороже `:d6a`. Ожидание: `core` стартует, строка не
   краснеет ложно. Тесты — задача Б1, `test_web_row_without_reports`; задача Б2,
   `test_store_opens_d5_db`.

## Карта файлов Д6б

```
core/pcbk_core/gateway/accounts.py    + gw_notices, NOTICE, notice_acked, ack_notice, столбец в list
core/pcbk_core/gateway/api.py         me + "notice"; POST /pcbk/api/auth/notice
core/pcbk_core/gateway/web.py         web_router: POST /pcbk/api/web/report, GET /health/web; WebReports
core/pcbk_core/gateway/__init__.py    + web_router (до catch-all), reports в роли
core/tests/test_gw_accounts.py, test_gw_api.py, test_gw_web.py
watchdog/pcbk_watchdog/checks.py      check_web(..., reports_url=None) — таблица Д6-R50
watchdog/pcbk_watchdog/main.py        KINDS["web"] + необязательный reports_url
watchdog/components.json              + reports_url
edge/pcbk.conf.template               + limit_req_zone pcbk_web; location = /pcbk/api/web/report
web/src/api.ts                        + ackNotice, report
web/src/watch.ts                      createTurnWatch, createStreamWatch
web/src/App.tsx                       + фаза notice
web/src/components/NoticeScreen.tsx, ErrorBoundary.tsx, ThreadSidebar.tsx; Chat.tsx (+ watch, sidebar)
web/tests/watch.test.ts, App.test.tsx (+), Sidebar.test.tsx
compose.yaml                          core и сторож :d6b
tests/integration/test_web.py         (+) предупреждение, отчёты, список разговоров, четвёртая вкладка
tests/integration/test_edge.py        + /pcbk/api/web/report, IMAGES :d6b
docs/checks/D6b.md, docs/checks/D6b/*.png
```

---

### Task Б0: Теги первой правкой, хвосты Д6а и утро

- [ ] **Step 1: Теги (Ш2)** — `core:d5b` → `:d6b`, `watchdog:d6a` → `:d6b` в `compose.yaml` и
  `IMAGES`; `student:d6a` не трогать. Commit: `git commit -m "Д6б: теги образов core и сторожа :d6b до первой сборки" -- compose.yaml tests/integration/test_edge.py`.
- [ ] **Step 2: После `platform-d6a`:** перенесённое чертой Д6а, перебазирование на `main`,
  предпроверки сервера как А0, шаг 3 (сторож и места `:d6a`, строка «Веб-чат» `ok`), снимки ID
  откатных образов. Commit `docs/checks/D6b.md` — «Д6б: утро — стенд Д6а жив».

---

### Task Б1: Отчёты браузеров и строки сторожа

**Files:**
- Create: `core/pcbk_core/gateway/web.py`, `core/tests/test_gw_web.py`
- Modify: `core/pcbk_core/gateway/__init__.py`, `watchdog/pcbk_watchdog/checks.py`,
  `watchdog/pcbk_watchdog/main.py`, `watchdog/components.json`
- Test: `watchdog/tests/test_checks.py` (+2)

**Interfaces:**
- Consumes: `front_only`, `configured`, `current_account`, `origin_checked`, `gw_error`
  (`api.py`, Д5а); реестр маршрутов роли.
- Produces:
  - `web.py`: `REPORT_KINDS = ("ui_crash", "turn_silent", "stream_down")`,
    `WEB_REPORT_EVERY_S = 60`, `WEB_REPORT_MAX_BYTES = 256`;
  - `class WebReports` (без ввода-вывода, `threading.Lock`):
    - `report(login: str, kind: str, now: float) -> bool` — `True`, если засчитан;
    - `counts_1h(now: float) -> dict[str, int]`;
  - `web_router(role) -> APIRouter` — `POST /pcbk/api/web/report`, `GET /health/web` (Д6-R50);
  - `check_web(component, title, url, timeout=…, reports_url: str | None = None) -> Check`;
  - `KINDS["web"]` — прежние обязательные ключи, `reports_url` — необязательный (проверка в
    `load_components`: строка `http://…`).

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_gw_web.py — помощники core/tests/helpers.py Д5а: make_gateway, login, ORIGIN, PW;
# учётка — role.store.add(...) внутри with TestClient (база открывается в lifespan)
FRONT = "http://172.31.250.34:8000"                                                  # адрес core в pcbk-front, как test_gw_api

def test_web_report_rules(tmp_path, caplog):
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=FRONT) as c:
        role.store.add("stu-03", "student", 3, PW); login(c, "stu-03")
        post = lambda body, origin=ORIGIN: c.post("/pcbk/api/web/report", content=body,
                                                  headers={"origin": origin, "content-type": "application/json"})
        assert post(b'{"kind":"turn_silent"}').status_code == 204
        assert post(b'{"kind":"turn_silent"}').status_code == 204                    # второй за минуту — без счёта
        assert post(b'{"kind":"ui_crash"}').status_code == 204
        for bad in (b'{"kind":"x"}', b'{"kind":"ui_crash","text":"<script>"}', b'[]', b'{"kind":"' + b"a" * 300 + b'"}'):
            assert post(bad).status_code == 400, bad
        assert post(b'{"kind":"ui_crash"}', origin="https://evil").status_code == 403
        assert c.get("/health/web").json() == {"ui_crash_1h": 1, "turn_silent_1h": 1, "stream_down_1h": 0}
    with TestClient(app, base_url=FRONT) as anon:
        assert anon.post("/pcbk/api/web/report", content=b'{"kind":"ui_crash"}',
                         headers={"origin": ORIGIN, "content-type": "application/json"}).status_code == 401
    assert "<script>" not in caplog.text                                             # ввод клиента в журнал не попал

def test_health_web_front_only(tmp_path):                                            # Д5-R31, как test_no_route_…_in_any_zone
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url="http://172.31.6.2:8000") as c:                    # адрес core в сети места 06
        assert c.get("/health/web").status_code == 404
        assert c.post("/pcbk/api/web/report", json={"kind": "ui_crash"}).status_code == 404
```

```python
# watchdog/tests/test_checks.py (+)
def test_web_report_lines(web_site, fake_core):
    root, url = web_site; put_build(root)
    for reply, want in (({"ui_crash_1h": 1, "turn_silent_1h": 2, "stream_down_1h": 0}, ("warn", "чат падает в браузере", "1 за час")),
                        ({"ui_crash_1h": 0, "turn_silent_1h": 2, "stream_down_1h": 3}, ("warn", "ответ модели не пришёл вовремя", "2 за час")),
                        ({"ui_crash_1h": 0, "turn_silent_1h": 0, "stream_down_1h": 3}, ("warn", "браузеры теряют связь с сервером", "3 за час")),
                        ({"ui_crash_1h": 0, "turn_silent_1h": 0, "stream_down_1h": 0}, ("ok", "страница чата отдаётся", "сборка abc123"))):
        fake_core.set("/health/web", 200, reply)
        assert row(check_web("web", "Веб-чат", url, reports_url=fake_core.url("/health/web"))) == want

def test_web_row_without_reports(web_site, fake_core):                               # Review Focus 5
    root, url = web_site; put_build(root)
    for status, body in ((404, {"name": "NotFound"}), (200, {"ui_crash_1h": "x"}), (200, {"extra": 1})):
        fake_core.set("/health/web", status, body)
        assert row(check_web("web", "Веб-чат", url, reports_url=fake_core.url("/health/web"))) == \
            ("ok", "страница чата отдаётся", "сборка abc123; отчёты браузеров недоступны")
    (root / "chat/index.html").unlink()                                               # статика важнее отчётов
    assert row(check_web("web", "Веб-чат", url, reports_url=fake_core.url("/health/web")))[0] == "fail"
```

- [ ] **Step 2: Run** — `(cd core && CORE_PYTEST tests/test_gw_web.py); (cd watchdog && uv run --python 3.12 --with pytest pytest -q tests/test_checks.py)`. Expected: FAIL.
- [ ] **Step 3: Implement по Interfaces.** Маршрут ставится до ловящего `catch_all_router`. Тело
  читается не больше `WEB_REPORT_MAX_BYTES + 1` байт.
- [ ] **Step 4: Run** — `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs)`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add core/ watchdog/ && git commit -m "Шлюз: отчёты браузеров о молчании чата и строки сторожа" -- core/ watchdog/`.

---

### Task Б2: Предупреждение при первом входе

**Files:**
- Modify: `core/pcbk_core/gateway/accounts.py`, `core/pcbk_core/gateway/api.py`,
  `web/src/api.ts`, `web/src/App.tsx`, `web/src/texts.ts`
- Create: `web/src/components/NoticeScreen.tsx`
- Test: `core/tests/test_gw_accounts.py` (+2), `core/tests/test_gw_api.py` (+1),
  `web/tests/App.test.tsx` (+1)

**Interfaces:**
- Produces:
  - `accounts.py`:
    - `NOTICE = "teacher-reads-1"`;
    - `AccountStore.notice_acked(login: str, notice: str) -> bool`;
    - `AccountStore.ack_notice(login: str, notice: str) -> None`;
    - в `list` — столбец «предупреждён»;
  - `api.py`: `me` → `{**account.to_json(), "notice": NOTICE | None}`; `POST /pcbk/api/auth/notice` —
    Д6-R49;
  - `web/src/api.ts`: `Api.ackNotice(notice: string): Promise<Reply>`;
  - `App`: фаза `notice` — `NoticeScreen({ text, onAck })` между `me` и `workplace`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_gw_accounts.py (+)
def test_store_opens_d5_db(tmp_path):                                                # Review Focus 5
    db = tmp_path / "core.db"; make_d5_db(db)                                        # схема Д5 без gw_notices (копия _SCHEMA Д5 в тесте)
    store = AccountStore(db); assert store.open()
    assert store.notice_acked("stu-03", NOTICE) is False
    store.ack_notice("stu-03", NOTICE); store.ack_notice("stu-03", NOTICE)          # повтор — без ошибки
    assert store.notice_acked("stu-03", NOTICE) and not store.notice_acked("stu-03", "teacher-reads-2")

def test_list_shows_notice_column(tmp_path, capsys): ...                             # «да»/«нет» у студентов

# core/tests/test_gw_api.py (+) — помощники Д5а: make_gateway, login, ORIGIN, PW; адрес FRONT
def test_notice_ack_rules(tmp_path):                                                 # Review Focus 1
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=FRONT) as s, TestClient(app, base_url=FRONT) as t, TestClient(app, base_url=FRONT) as anon:
        role.store.add("stu-03", "student", 3, PW); role.store.add("t-1", "teacher", None, PW)
        login(s, "stu-03"); login(t, "t-1")
        ack = lambda c, body, origin=ORIGIN: c.post("/pcbk/api/auth/notice", json=body, headers={"origin": origin})
        assert s.get("/pcbk/api/auth/me").json()["notice"] == NOTICE
        assert s.get("/pcbk/api/auth/notice").status_code == 404                     # GET не подтверждает
        assert ack(s, {"notice": NOTICE}, origin="https://evil").status_code == 403
        assert ack(s, {"notice": "other"}).status_code == 400
        assert ack(anon, {"notice": NOTICE}).status_code == 401
        assert s.get("/pcbk/api/auth/me").json()["notice"] == NOTICE                  # отказы ничего не записали
        assert ack(s, {"notice": NOTICE}).status_code == 204
        assert ack(s, {"notice": NOTICE}).status_code == 204                          # повтор — тоже 204
        assert s.get("/pcbk/api/auth/me").json()["notice"] is None
        assert t.get("/pcbk/api/auth/me").json()["notice"] is None                    # у преподавателя — нет
        assert role.store.notice_acked("t-1", NOTICE) is False                         # чужое подтверждение не записано
```

```tsx
// web/tests/App.test.tsx (+)
test("notice_blocks_chat_until_ack", async () => {
  const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice: "teacher-reads-1" }]], ackNotice: [204, null],
                  workplace: [[200, { state: "ready" }]] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(await screen.findByText(/Преподаватель видит ваши разговоры/)).toBeTruthy();
  expect(a.wake).not.toHaveBeenCalled(); expect(a.workplace).not.toHaveBeenCalled(); expect(screen.queryByTestId("fake-chat")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  await screen.findByTestId("fake-chat"); expect(a.ackNotice).toHaveBeenCalledWith("teacher-reads-1");
});
```

- [ ] **Step 2: Run** — `(cd core && CORE_PYTEST tests/test_gw_accounts.py tests/test_gw_api.py); (cd web && npm test)`. Expected: FAIL.
- [ ] **Step 3: Implement по Interfaces.**
- [ ] **Step 4: Run** — `(cd core && CORE_PYTEST) && (cd web && npm test && PCBK_BUILD=dev npm run build)`. Expected: PASS.
- [ ] **Step 5: Commit** — `git add core/ web/ && git commit -m "Вход: предупреждение при первом входе — подтверждение в core.db, экран в чате" -- core/ web/`.

---

### Task Б3: Молчание в браузере; список разговоров

**Files:**
- Create: `web/src/watch.ts`, `web/src/components/{ErrorBoundary,ThreadSidebar}.tsx`,
  `web/tests/watch.test.ts`, `web/tests/Sidebar.test.tsx`
- Modify: `web/src/components/Chat.tsx`, `web/src/api.ts` (+ `report`), `web/src/texts.ts`

**Interfaces:**
- Produces (`watch.ts`):
  - `createTurnWatch(o: { now(): number; onSilent(): void; silentS?: number /* 180 */ }): { started(): void; progress(fingerprint: string): void; ended(): void; tick(): void }`.
    `tick` зовётся раз в секунду и на `visibilitychange`; отчёт — один на ход.
  - `createStreamWatch(o: { now(): number; onRecoveredAfterLongDown(): void; downS?: number /* 120 */ }): { onStream(s: StreamState): void }`.
- `Api.report(kind: "ui_crash" | "turn_silent" | "stream_down"): Promise<void>` — ошибки глушатся.
- `ThreadSidebar()` — Д6-R52; `ErrorBoundary({ onCrash, children })`.

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/watch.test.ts
test("turn_watch_fires_once_after_silence", () => {
  let t = 0; const onSilent = vi.fn(); const w = createTurnWatch({ now: () => t, onSilent });
  w.started(); t = 179_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();
  t = 181_000; w.tick(); w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
});
test("turn_watch_progress_resets_and_end_stops", () => {
  let t = 0; const onSilent = vi.fn(); const w = createTurnWatch({ now: () => t, onSilent });
  w.started(); t = 170_000; w.progress("tool:pcbk_tag_now:running"); t = 340_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();
  w.progress("tool:pcbk_tag_now:running"); t = 360_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);   // тот же отпечаток — не прогресс
  w.ended(); t = 999_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
});
test("turn_watch_background_tab_uses_clock", () => { /* tick не звали 200 с (фон), затем visibilitychange → tick → отчёт */ });
test("stream_watch_reports_on_recovery_only", () => {
  let t = 0; const on = vi.fn(); const w = createStreamWatch({ now: () => t, onRecoveredAfterLongDown: on });
  w.onStream({ failures: 1, nextRetryS: 1, last: "down" }); t = 60_000; w.onStream({ failures: 0, nextRetryS: null, last: "ok" });
  expect(on).not.toHaveBeenCalled();                                                  // 60 с — коротко
  w.onStream({ failures: 1, nextRetryS: 1, last: "down" }); t = 190_000; w.onStream({ failures: 5, nextRetryS: 30, last: "down" });
  expect(on).not.toHaveBeenCalled();                                                  // пока нет связи — нет отчёта
  w.onStream({ failures: 0, nextRetryS: null, last: "ok" }); expect(on).toHaveBeenCalledTimes(1);
});
```

```tsx
// web/tests/Sidebar.test.tsx — runtime с адаптером списка из памяти
test("sidebar_shows_active_only_and_no_unarchive_or_delete", () => { /* архивный не виден; нет «Из архива», «Удалить» */ });
test("sidebar_rename_limits", async () => { /* "" и 201 знак — rename не зван, подсказка; 200 знаков — rename("…") */ });
test("error_boundary_reports_crash_and_restarts", () => { /* потомок бросает → report("ui_crash") 1 раз, «Перезапустить чат» → перемонтирование */ });
```

- [ ] **Step 2: Run** — `(cd web && npm test)`. Expected: FAIL.
- [ ] **Step 3: Implement по Interfaces.**
  - Отпечаток прогресса — `messageId` последнего ответа ассистента, число его частей и длина
    текста последней части.
  - `Chat` зовёт `started()` при успешном `onNew`, `progress()` на каждое изменение состояния
    сообщений, `ended()` при `isRunning` → `false`.
- [ ] **Step 4: Run** — `(cd web && npm test && PCBK_BUILD=dev npm run build)`. Expected: PASS, `check-dist` чист.
- [ ] **Step 5: Commit** — `git add web/ && git commit -m "Веб: молчание хода, сбой интерфейса, потеря связи; список разговоров — архив и имя" -- web/`.

---

### Task Б4: `edge`, компоновка, автотесты в браузере и учения

**Files:**
- Modify: `edge/pcbk.conf.template`, `watchdog/components.json` (если не в Б1),
  `tests/integration/test_web.py`, `tests/integration/test_edge.py`
- Test: те же

**Interfaces:**
- Produces:
  - `edge` — `limit_req_zone $binary_remote_addr zone=pcbk_web:1m rate=1r/s;` и
    `location = /pcbk/api/web/report { default_type application/json; if ($pcbk_bad_api_uri) {…}; limit_req zone=pcbk_web burst=10 nodelay; limit_req_status 429; client_max_body_size 1k; proxy_read_timeout 10s; proxy_pass http://$pcbk_core; }`;
  - `cdp.py` (+):
    - `Page.rename_first_thread(title)` — поле «Переименовать» первой строки списка и Enter;
    - `Page.has_text(pattern, within=0) -> bool` — `True`, если текст появился за `within` с;
    - `Page.close()`;
  - помощники модуля:
    - `stack.accounts_list_row(login) -> str` — строка `accounts list`;
    - `session_of(stack, n, sid) -> dict` — `GET /session/<sid>` места через `oc`;
    - `ensure_notice_acked(stack, login)` — `POST /pcbk/api/auth/notice` клиентом `GwClient`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_web.py (+) — первым в модуле: учётка web-a ещё не подтверждала
def test_first_login_notice(stack, browser, web_users):                              # Review Focus 1
    page = browser.page(); page.goto(f"{STAND}/login")
    page.type("#login", "web-a"); page.type("#password", web_users["web-a"]); page.press_enter("#password")
    page.wait_text(r"Преподаватель видит ваши разговоры", 60)
    assert page.requests("/pcbk/api/workplace") == [] and page.requests("/pcbk/api/oc/") == []
    page.click_text("Понятно"); page.wait_text(r"Вы вошли как web-a", 60)
    assert "да" in stack.accounts_list_row("web-a")

def test_reports_reach_row(stack, browser, web_users):                               # транспорт отчёта из страницы
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    code = page.eval("fetch('/pcbk/api/web/report',{method:'POST',headers:{'Content-Type':'application/json'},"
                     "body:JSON.stringify({kind:'turn_silent'})}).then(r=>r.status)")
    assert code == 204
    stack.wait_status(lambda d: row(d, "web")[:2] == ("warn", "ответ модели не пришёл вовремя"), timeout=60)

def test_rename_and_archive_through_gateway(stack, browser, web_users):               # Review Focus 4
    before = stack.gw_health()["refused_1h"]
    page = browser.page(); login_to_chat(page, "web-a", web_users["web-a"])
    page.type("textarea", Q); page.press_enter("textarea"); page.wait_text(r"Готово: .+", 120)
    sid = last_session(stack, 9)
    page.rename_first_thread("Проверка Д6б")                                         # помощник cdp.py: поле строки + Enter
    wait_for(lambda: session_of(stack, 9, sid)["title"] == "Проверка Д6б", timeout=20)
    page.click_text("В архив")
    wait_for(lambda: session_of(stack, 9, sid).get("time", {}).get("archived") is not None, timeout=20)
    assert not page.has_text("Проверка Д6б")
    assert stack.gw_health()["refused_1h"] == before

def test_fourth_tab_told_to_close(stack, browser, web_users):                        # хвост Д5б, 429 streams
    pages = [browser.page() for _ in range(4)]
    login_to_chat(pages[0], "web-a", web_users["web-a"])
    for p in pages[1:]:
        p.goto(f"{STAND}/chat/")
    pages[3].wait_text(r"Открыто слишком много вкладок чата — закройте лишние", 60)
    pages[1].close()
    pages[3].wait_text(r"Вы вошли как web-a", 60)
    assert not pages[3].has_text("закройте лишние", within=45)                        # следующая попытка по паузе прошла
```

  Остальные тесты модуля перед входом зовут `ensure_notice_acked(stack, "web-a")`.
  `test_edge.py`: `POST /pcbk/api/web/report` через `edge` без сессии — 401 JSON; тело 2 КиБ — 413
  JSON; путь `/pcbk/api/web/other` — 404 JSON; `IMAGES` — `:d6b`.
- [ ] **Step 2: Run** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_edge.py tests/integration/test_web.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Полный прогон** — как А6, шаг 4, со снимками ID
  (`watchdog:d6a`, `student:d6a`, `core:d5b` не изменились). Expected: PASS, ноль пропусков в
  `test_web`.
- [ ] **Step 5: Снимки (тестовый стенд):**
  - `01-notice.png` — предупреждение;
  - `02-web-row-warn.png` — строка желтеет по отчёту;
  - `03-sidebar.png` — список, переименование и архив;
  - `04-fourth-tab.png` — четвёртая вкладка.

  Файлы кладутся в `docs/checks/D6b/`.
- [ ] **Step 6: Commit** — `git add edge/ watchdog/components.json tests/integration/ docs/checks/ && git commit -m "Веб-чат Д6б в компоновке: edge /pcbk/api/web/report; автотесты в браузере" -- edge/ watchdog/components.json tests/integration/ docs/checks/`.

---

### Task Б5: Выкладка

- [ ] **Step 1:** Как А7, шаги 1–2:
  - образы `core:d6b` и `watchdog:d6b` из `git worktree` ветки дня;
  - статика чата — в `chat.new`, сверка сумм, `mv chat chat.d6a && mv chat.new chat`;
  - копии `compose.yaml.d6a` и шаблона `edge` для отката.
- [ ] **Step 2: Службы.**
  1. `docker compose up -d --no-build watchdog` → `stale: false`.
  2. `docker compose up -d --no-build core` → `healthy`, 13 сетей.
  3. `docker compose restart edge`.

  Места не пересоздаются: образ места в Д6б не меняется.
  Expected: строка «Веб-чат» `ok`, «сборка <build Д6б>», без «отчёты недоступны»; «Шлюз и вход» в
  норме.
- [ ] **Step 3: Commit** — `git commit -m "Выкладка Д6б: предупреждение при первом входе, отчёты браузеров" -- docs/checks/D6b.md deploy/README.md`.

### Task Б6: Живые проверки и учения

Туннель и браузер — как А8, только под `DRILL_LLM=402`. Окна владельца не нужно.

- [ ] **Step 1: Пробная учётка `d6-probe-n` (место 09)**, как А8, шаг 1.
- [ ] **Step 2: Первый вход.**
  Expected:
  - экран предупреждения, снимок `05-live-notice.png`;
  - до «Понятно» — ни одного запроса к месту (сеть браузера);
  - после — чат;
  - `accounts list` — «да».
- [ ] **Step 3: Учение «ответ модели не пришёл вовремя».** Отчёт из страницы (`fetch` из консоли
  CDP, как Б4 — пометка «учение»).
  Expected:
  - строка «Веб-чат» — `warn` «ответ модели не пришёл вовремя», снимок `06-live-web-warn.png`;
  - через час строка возвращается в `ok` сама — проверка следующим утром (Д7, Б0),
    вердикт — туда.
- [ ] **Step 4: Список разговоров.** Два вопроса под 402 — два разговора; переименовать и
  отправить в архив один.
  Expected: заголовок и архив — в месте (помощник `oc`), `refused_1h` не вырос.
- [ ] **Step 5: Уборка** — как А8, шаг 6, для `d6-probe-n` и места 09.
- [ ] **Step 6: Commit** — `git commit -m "Д6б: живые проверки — первый вход, учение строки «Веб-чат», список разговоров" -- docs/checks/D6b.md docs/checks/D6b/`.

### Task Б7: Закрытие дня

- [ ] **Step 1: Документы.**
  - DESIGN §3 п. 1 — предупреждение [П];
  - §8 — отчёты браузеров;
  - §13 — «Д6б» (Д6-R49…R52);
  - README «Д6 готов», дальше Д7;
  - хвосты для Д7 — в строку Д7 дорожной карты:
    - вопросы кнопками и «разрешить один раз» для `doom_loop`;
    - имя `pomoshnik` в `RESERVED_NAMES` службы;
    - выбор агента в вебе («мои агенты») через поле `agent` (Д5-R33).
- [ ] **Step 2: Критик** (Opus 5.5). Блокеры — А9, шаг 2, и ещё:
  - чат монтируется без подтверждения;
  - ручка отчётов пишет ввод клиента в журнал или считает чужой логин.

  Петля — до нуля блокеров.
- [ ] **Step 3: Слияние — после `platform-d6a`.** Перебазирование, повтор Б4, шаг 4, `main`, тег
  `platform-d6b`, проверка на секреты и `git ls-files` (как А9), пуш, чистый клон.
- [ ] **Step 4: План и факт** — `pace "$(msg 'Шлюз: отчёты браузеров')" "$(msg 'Веб-чат Д6б в компоновке')" 5.5`;
  полное время — справочно.
- [ ] **Step 5: Владельцу** — «Д6 готов»: снимки `01`–`06`. Вопросы — строки 3–4 раздела ниже,
  если ответа нет.

---

## Что нужно от владельца

1. **Д6а — окно ≈45 мин вечером после выкладки** (А8, шаг 5): тег для вопроса, как в Д4б, и «да»
   на чтение производственных данных при вас. Расход — меньше $0,10 из $1000.
   Предпосылка — выложены Д4а и Д4б (отдельный ключ OpenRouter с балансом и лимитом $1000 без
   сброса), Д5а и Д5б. Без окна Д6а показывает чат под учениями без денег, а число из БДРВ
   переходит в Д6а′.
2. **Сертификат `edge` — заменить до пилота.** Пока он истёк, баннер чата у всех студентов пишет
   «Сбой: Входной прокси». Напоминание стоит на 01.10.
3. **Пилот — не раньше `platform-d6b`.** До этого у чата нет предупреждения при первом входе. Д6а
   выкладывается только под пробные учётки. Вопроса нет — для сведения.
4. **Решения с умолчаниями.** План идёт по умолчанию; ответ «оставить» ничего не меняет:
   - (а) агент по умолчанию — `pomoshnik`, «Помощник по данным», текст — в задаче А5; только три
     инструмента службы данных, без вопросов кнопками;
   - (б) встроенные агенты OpenCode `build`, `plan`, `general`, `explore` в местах выключены;
   - (в) текст предупреждения при первом входе (Д6-R49); при смене текста студент подтверждает
     снова;
   - (г) баннер показывает только своё место студента, а не все десять;
   - (д) под ответом видна строка вызова инструмента со свёрнутыми подробностями (что спросили
     у службы и что она вернула);
   - (е) удаления разговоров и «правки / повтора» нет (решения research/07); «из архива» нет,
     потому что OpenCode 1.18.33 его не принимает.

## Открытые вопросы

1. **Какие браузеры стоят на ПК класса ПЦБК и каких версий?** Сборка Vite 8 рассчитана на
   Chromium ≥ 111 (Chrome, Edge, Яндекс), Firefox ≥ 114, Safari 16.4. Если есть старее — нужен
   другой `build.target`, и это меняет А2.
2. **Сохранять ли открытый разговор в адресе страницы**, чтобы после перезагрузки открывался тот
   же? По умолчанию — нет. С Д6б прежние разговоры в списке слева.
3. **Кто, кроме владельца, смотрит на строку «Веб-чат»?** Telegram отложен, так что отчёты о
   молчании видны только на странице состояния. Если там их никто не смотрит, у отчётов нет
   адресата. Вопрос тот же, что Б5 (Telegram), — напоминание.
