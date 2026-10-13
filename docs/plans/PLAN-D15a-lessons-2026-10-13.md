# Д15а — вкладка «Уроки» и чистые чаты мест (13.10.2026) — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** студент видит во вкладке «Уроки» учебные материалы (8 уроков с картинками), отданные core только после
входа; чаты мест 01–10 очищены от разговоров проверок.

**Architecture:** пакет уроков — каталог хоста (`lessons.json`, `<id>.md`, `img/*`), смонтированный в core только на
чтение; core отдаёт его тремя ручками `GET /pcbk/api/lessons*` с проверками зоны, настройки и сессии (как
`/pcbk/api/events`); edge проксирует их тем же блоком, что события; веб — вкладка «Уроки» рядом с «Событиями», markdown
— тем же `MarkdownTextPrimitive` + `remark-gfm`, картинки — `<img>` на ручку core.

**Tech Stack:** Python 3.12, FastAPI (core); React + TypeScript, vitest (web); nginx (edge); docker compose.

**Spec:** `.superpowers/sdd/PLAN-D15-lessons-place11-2026-10-13/DESIGN.md` (вне git, раздел «Д15а»; согласован
владельцем 13.10.2026). Разведка: `$JOB/tmp/lessons/web.md`, `content.md`, `accounts.md`.

## Global Constraints

- В git — ни одного имени тега ПЦБК, узла участка, адреса стенда или снимка экрана стенда; в тестах и образце —
  только `20FAKE_*`. Ворота перед каждым коммитом: `git diff --cached | grep -nE '\b[0-9]{2}[A-Z]{2,4}[0-9]{2}'`
  — пусто (`20FAKE_001_PV` шаблону не отвечает).
- Пакет уроков: `lessons.json` = `{"lessons": [{"id", "title", "summary"}]}`; `id` — `^[a-z0-9-]{1,40}$`; текст —
  `<id>.md`, не больше 256 КиБ; картинка — `img/<имя>`, имя `^[a-z0-9-]{1,60}\.(png|jpg|jpeg|webp)$`, не больше
  2 МиБ; симлинки не отдаются.
- Ручки: `GET /pcbk/api/lessons`, `GET /pcbk/api/lessons/{id}`, `GET /pcbk/api/lessons/img/{name}`; пускают любую
  вошедшую учётку (студент и преподаватель); без входа — 401 JSON шлюза «Нужно войти»; вне зоны pcbk-front — 404.
- Чтение пакета — на каждый запрос, без кэша в core (правка на сервере видна сразу).
- После входа студент видит чат (вкладка «Уроки» по умолчанию не выбрана); вкладка — только у студента.
- Тексты, комментарии, сообщения — по-русски, в стиле соседнего кода; новых npm- и pip-пакетов нет.
- Тесты core: `cd core && uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q -p no:cacheprovider`;
  web: `cd web && npm test`; интеграция — только под `flock $JOB/tmp/pcbk-integration.lock`.
- Push — только `git -c credential.helper=store push`; Dify на сервере не трогать.

## Review Focus

1. Имя урока или картинки с `..`, `/`, `%2e`, симлинк в `img/` или на `<id>.md` — 404, ни байта чужого файла
   (задача 1, `test_lessons_traversal_and_symlink_404`).
2. Картинка урока, загруженная тегом `<img>` через edge с cookie и без своих заголовков, — 200 `image/*`; без cookie —
   401 (задача 3, `test_lessons_image_via_edge`).
3. Пакета нет / `lessons.json` битый — вкладка пишет понятный текст, чат и другие вкладки работают (задачи 1 и 2).
4. Правка `lessons.json` или `<id>.md` между запросами видна без перезапуска core (задача 1,
   `test_lessons_edit_visible_without_restart`).
5. Урок с внешней картинкой (`https://…`), ссылкой `javascript:` или сырым HTML — внешняя картинка текстом, ссылка не
   активна, HTML не исполняется (задача 2, `LessonsPage.test.tsx`).

---

### Task 1: core — чтение пакета уроков и ручки `/pcbk/api/lessons*`

**Files:**
- Create: `core/pcbk_core/gateway/lessons.py` (чтение пакета, без FastAPI)
- Create: `core/pcbk_core/gateway/lessons_api.py` (`lessons_router(role)`)
- Modify: `core/pcbk_core/settings.py` (поле `LESSONS_ROOT: str = "/srv/lessons"` рядом с `AGENTS_ROOT`)
- Modify: `core/pcbk_core/gateway/__init__.py` (`GatewayRole.router`: `include_router(lessons_router(self))` до
  `catch_all_router`, рядом с `agents_router`; роль хранит `self.lessons_root = settings.LESSONS_ROOT`)
- Modify: `core/tests/helpers.py` (`gateway_role(..., lessons_root=None)` — по образцу `agents_root`)
- Test: `core/tests/test_lessons.py`, `core/tests/test_gw_lessons_api.py`

**Interfaces:**
- Produces (`lessons.py`): `LESSON_ID_RE`, `IMAGE_NAME_RE`, `MAX_LESSON_BYTES = 256 * 1024`,
  `MAX_IMAGE_BYTES = 2 * 1024 * 1024`, `IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
  "webp": "image/webp"}`; `class LessonsError(Exception)` (битый `lessons.json`); `@dataclass(frozen=True) LessonMeta(id:
  str, title: str, summary: str)`; `read_index(root: str) -> list[LessonMeta]` (нет каталога или файла — `[]`; битый —
  `LessonsError`); `read_lesson(root: str, lesson_id: str) -> tuple[LessonMeta, str] | None`; `image_file(root: str,
  name: str) -> tuple[Path, str] | None` (путь и тип).
- Produces (`lessons_api.py`): `LESSONS_PATH = "/pcbk/api/lessons"`, `LESSON_PATH = LESSONS_PATH + "/{lesson_id}"`,
  `LESSON_IMG_PATH = LESSONS_PATH + "/img/{name}"`; ответы `{"lessons": [{"id","title","summary"}]}`,
  `{"id","title","markdown"}`, файл картинки с `Cache-Control: private, max-age=300` и
  `X-Content-Type-Options: nosniff`.

- [ ] **Step 1: Тесты чтения пакета** (`test_lessons.py`, пакет — во `tmp_path`):
  `test_index_order_from_json` (порядок как в файле), `test_missing_dir_or_index_is_empty` (`[]`),
  `test_broken_index_raises` (не JSON; нет `lessons`; `id` не по шаблону; повтор `id` — `LessonsError`),
  `test_read_lesson_ok_and_unknown_none` (`id` нет в индексе или файла нет — `None`),
  `test_lesson_too_big_none` (256 КиБ + 1 — `None`), `test_image_types_and_size` (png/jpg/jpeg/webp — тип;
  `.gif`, `.svg`, 2 МиБ + 1 — `None`), `test_lessons_traversal_and_symlink_404` (имена `../x.png`, `a/b.png`,
  `%2e%2e.png`, симлинк `img/s.png` → файл вне пакета, `<id>.md` — симлинк: всё `None`),
  `test_lessons_edit_visible_without_restart` (переписать `lessons.json` и `<id>.md` — следующий вызов видит новое).
- [ ] **Step 2:** запустить — FAIL (модуля нет).
- [ ] **Step 3: Реализовать `lessons.py`.** Проверка симлинков — `os.lstat`/`Path.is_symlink()` на файле и на `img/`,
  итоговый путь — `resolve()` внутри `resolve()` корня; чтение текста — UTF-8, ошибка декодирования — `None`.
- [ ] **Step 4: Тесты ручек** (`test_gw_lessons_api.py`, фикстура `gw` как в `test_gw_agents_api.py`, корень пакета —
  `tmp_path / "lessons"`): `test_lessons_refusals` (аноним — 401 тело `{"name":"Unauthorized","data":{"message":
  "Нужно войти"}}`; `client=PLACE_NET` — 404), `test_lessons_student_and_teacher_200` (`stu-03` и `teach`),
  `test_lessons_index_and_lesson` (форма ответов, 404 на чужой `id`), `test_lessons_broken_index_503`
  (503 JSON шлюза с текстом «Пакет уроков не читается: …»), `test_lessons_empty_without_pack`,
  `test_lessons_image_headers` (тип, `Cache-Control`, `nosniff`, тело — байты файла), `test_lessons_config_error_503`
  (как `test_agents_api_config_error_503`), `test_lessons_other_methods_404` (POST — 404 без Allow).
- [ ] **Step 5:** запустить — FAIL.
- [ ] **Step 6: Реализовать `lessons_router(role)`** по образцу `agents_router`: `dependencies=[Depends(front_only),
  Depends(configured(role))]`, `account: Account = Depends(current_account(role))` (учётка нужна только как
  проверка входа), чтение — `asyncio.to_thread`; картинка — `FileResponse` с типом из `image_file`.
- [ ] **Step 7:** весь набор core — PASS (было 1819 прошли, 2 пропущены; стало больше на новые).
- [ ] **Step 8:** коммит `Д15а: core — пакет уроков и ручки /pcbk/api/lessons*` (ворота тегов — пусто).

### Task 2: web — вкладка «Уроки»

**Files:**
- Create: `web/src/lessons.ts` (строгий разбор ответов), `web/src/components/LessonsPage.tsx`,
  `web/src/components/LessonMarkdown.tsx`
- Modify: `web/src/api.ts` (`lessons(): Promise<Reply>`, `lesson(id: string): Promise<Reply>` — `callJson("GET", …)`),
  `web/src/components/Header.tsx` (`View = "main" | "calc" | "events" | "lessons"`, проп `showLessons`, кнопка
  после «События»), `web/src/App.tsx` (`lessonsOpened`, `showLessons = tabs && !teacher`, блок `.view` как у событий,
  подмена `LessonsImpl` в тестах), `web/src/texts.ts`, `web/src/chat.css`
- Test: `web/tests/lessons.test.ts`, `web/tests/LessonsPage.test.tsx`, `web/tests/App.test.tsx` (дополнить)

**Interfaces:**
- Consumes: ответы задачи 1 (формы выше), адрес картинки `/pcbk/api/lessons/img/<имя>`.
- Produces: `parseLessonIndex(body: unknown): LessonMeta[] | null`, `parseLesson(body: unknown): Lesson | null`
  (`Lesson = {id, title, markdown}`); `LessonsPage(p: {api: Api; onSignedOut: () => void})`;
  `LessonMarkdown(p: {markdown: string})`; тексты `T_TAB_LESSONS = "Уроки"`, `T_LESSONS_TITLE = "Уроки"`,
  `T_LESSONS_EMPTY = "Уроки пока не добавлены"`, `T_LESSONS_ERROR = "Уроки сейчас не открываются — попробуйте позже"`.

- [ ] **Step 1: Тесты** — `lessons.test.ts`: разбор валидных ответов; лишние/пропущенные поля, не строки — `null`.
  `LessonsPage.test.tsx`: список из индекса, первый урок открыт, клик — другой урок; пустой индекс — `T_LESSONS_EMPTY`;
  503/сбой сети — `T_LESSONS_ERROR`; 401 — `onSignedOut`; markdown `![a](img/x.png)` — `<img src=
  "/pcbk/api/lessons/img/x.png" alt="a" loading="lazy">`; `![b](https://e.x/y.png)` и `![c](../y.png)` — текст
  `[картинка: …]`, `<img>` нет; `[s](javascript:alert(1))` — не ссылка; `<script>` в тексте — текстом.
  `App.test.tsx`: у студента кнопка «Уроки» есть и `aria-pressed=false` после входа; у преподавателя кнопки нет.
- [ ] **Step 2:** `npm test` — FAIL.
- [ ] **Step 3: Реализовать.** `LessonMarkdown` — тот же `MarkdownTextPrimitive` + `remarkGfm`, что
  `MessageParts.tsx`; ссылки — тот же безопасный `Link` (вынести из `MessageParts.tsx` экспортом, без копии); `img` —
  `<img>` только для `src` по `^img/[a-z0-9-]{1,60}\.(png|jpg|jpeg|webp)$`, иначе текст как `ImageText`. Вид — список
  слева (≥ 900 px) / сверху (уже), свой `ErrorBoundary`, как у `EventsPage`.
- [ ] **Step 4:** `npm test` — PASS; `npm run build` с `PCBK_WEB_OUT=$JOB/tmp/d15a-web` — проходит
  `check-dist` (не больше 8 файлов).
- [ ] **Step 5:** коммит `Д15а: web — вкладка «Уроки»`.

### Task 3: compose, edge, образец пакета, интеграция

**Files:**
- Create: `deploy/lessons-sample/lessons.json`, `deploy/lessons-sample/01-primer.md`,
  `deploy/lessons-sample/02-agent.md`, `deploy/lessons-sample/img/primer.png` (PNG ≤ 4 КиБ, нарисованный
  программно, без экрана стенда); теги в тексте — только `20FAKE_*`
- Modify: `compose.yaml` (core: `- {type: bind, source: "${LESSONS_DIR:?LESSONS_DIR не задан}", target: /srv/lessons,
  read_only: true, bind: {create_host_path: false}}`, тег образа `core:d15a`), `deploy/env.example` (`LESSONS_DIR=`
  с комментарием), `edge/pcbk.conf.template` (`location = /pcbk/api/lessons` и `location /pcbk/api/lessons/` —
  копии `location = /pcbk/api/events`, только GET), `edge/tests/login.test.mjs` (как проверки `events`),
  `tests/integration/conftest.py` (`LESSONS_DIR` в `STACK_VARS`, в test.env — абсолютный путь
  `deploy/lessons-sample` чистой копии), `tests/integration/test_edge.py` (состав переменных и образ `core:d15a`)
- Create: `tests/integration/test_lessons.py`

**Interfaces:**
- Consumes: ручки задачи 1, статика задачи 2.
- Produces: переменная `LESSONS_DIR` (обязательна, как `AGENTS_DIR`); образец пакета — формат для задачи 4.

- [ ] **Step 1: Тесты** — `edge/tests/login.test.mjs`: оба location есть на 8443 ровно по разу, тело — как у
  `/pcbk/api/events`. `test_lessons.py` (стек как в `test_agents.py`): `test_lessons_via_edge_requires_login` (401 без
  cookie на все три ручки), `test_lessons_via_edge_student` (индекс образца, урок, markdown содержит `20FAKE_`),
  `test_lessons_image_via_edge` (GET картинки с cookie, только заголовки браузера `Sec-Fetch-Dest: image`,
  `Sec-Fetch-Site: same-origin` — 200 `image/png`, байты = файл образца).
- [ ] **Step 2:** `node --test edge/tests/*.test.mjs` — FAIL; правка шаблона — PASS.
- [ ] **Step 3:** compose, env.example, conftest, образец пакета.
- [ ] **Step 4:** интеграция на чистом клоне ветки под замком: `test_edge.py` и `test_lessons.py` — PASS.
- [ ] **Step 5:** коммит `Д15а: compose/edge — каталог уроков, location /pcbk/api/lessons, образец пакета`.

### Task 4: настоящий пакет уроков (только сервер, не git)

**Files (вне git):** `$JOB/tmp/lessons/pack/lessons.json`, `…/pack/0N-<id>.md` (8 уроков), `…/pack/img/*`

**Interfaces:**
- Consumes: формат и ограничения задачи 1; содержание — `$JOB/tmp/lessons/content.md`, разд. 3; кадры —
  `$JOB/tmp/demo/v2/shots/NN.png` и подписи `$JOB/tmp/demo/v2/manifest.pdf.json`.

- [ ] **Step 1:** 8 уроков (ids: `01-vhod`, `02-pomoshnik`, `03-tegi`, `04-svoy-agent`, `05-kontrolnaya-tochka`,
  `06-sverka-s-kalkulyatorom`, `07-pravka-agenta`, `08-sobytiya`): в каждом — «Цель», «Что сделать» (шаги),
  «Спросите агента» (образцы вопросов цитатами), «Что должно получиться», 1–3 картинки; урок 7 — с честной оговоркой
  о сбое сохранения правки и что делать. Язык — простой, для технолога, без внутренних терминов разработки.
- [ ] **Step 2:** картинки — копии кадров v2, имена по `IMAGE_NAME_RE`, каждая ≤ 2 МиБ.
- [ ] **Step 3: Проверка пакета** — `read_index`/`read_lesson`/`image_file` из задачи 1 на `$JOB/tmp/lessons/pack`:
  8 уроков, у всех текст, все `img/…` из markdown существуют; внешних ссылок нет.

### Task 5: выкладка, чистые чаты, живая проверка, публикация

- [ ] **Step 1:** образ `core:d15a` из чистого клона; загрузка на сервер со сверкой id; копия `core.db`
  (`core.db.before-d15a-20261013`), `compose.yaml.d14a`, `.env.pre-d15a`, `edge/pcbk.conf.template.d14a`, `chat.d14`.
- [ ] **Step 2:** `/opt/pcbk-reserve/lessons` (0755, файлы 0644) — пакет задачи 4; `.env`: `LESSONS_DIR=/opt/pcbk-reserve/lessons`.
- [ ] **Step 3:** ни одно место не работает (иначе ждать) → `compose.yaml` → `up -d --no-deps core` → `healthy`;
  шаблон edge → пересоздание `edge`; статика чата (README «Статика чата»: `chat.new` → сверка → `mv -T`).
- [ ] **Step 4: Чистые чаты 01–10** (скрипт в `$JOB/tmp/lessons/`, не в репо): на каждое спящее место — копия тома
  `pcbk-student-NN-state` в `/opt/pcbk-reserve/backup/places-20261013/` (0600) → `docker start` → API места
  (README «Освобождение места пробной учётки», п. 0–1) → `DELETE /session/<id>` только для созданных до
  2026-10-13T07:32Z → список пуст или только новее → `docker stop`; работающее место — пропустить и доложить.
- [ ] **Step 5: Живая проверка** под student-01 (Playwright, туннель `$JOB/tmp/demo/tunnel.sh`): вход → чат открыт →
  «Уроки» — 8 уроков, картинки грузятся; без входа `GET /pcbk/api/lessons` — 401; правка `lessons.json` на сервере
  (заголовок урока 1) видна после обновления страницы и откатывается; список разговоров места 01 пуст;
  страница состояния — `stale: false`.
- [ ] **Step 6:** README — раздел «Уроки (с Д15а)» (каталог, формат, как править, права); `docs/checks/D15a.md`
  (журнал); слияние в main, тег `platform-d15a`, проверка клоном; память.
