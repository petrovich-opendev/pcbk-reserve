# Д17 — телеметрия в ответе аналитики, точные уточнения, сокращения Dify, уроки по вкладке

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Шаги — чек-боксы `- [ ]`.

**Goal:** закрыть отложенное после Д16 (вывод данных БДРВ в ответ, неточные варианты уточнения, мелкие хвосты
распознавания, сокращения из Dify в корпус) и дать студентам серию уроков по вкладке «Аналитический чат
технолога» — от простого к сложному.

**Architecture:** телеметрия — детерминированная серверная секция нового вида `telemetry_observation`: подпись
оборудования из вопроса → согласованная связь (`reviews.resolved_link`) → прежний `DataService` через
`data_bridge.py` → шаблон `rendering.py`. В LLM телеметрия не уходит. Связи предлагают и согласуют студенты в
новом разделе вкладки «Связи с историаном». Уточнения: фраза из вопроса, совпадающая с названием узла, имеет
приоритет над пословным разбором терминов. Корпус и уроки — данные на сервере вне git.

**Tech Stack:** Python 3.12/FastAPI (core), React/TS/Vite (web), SQLite, Docker compose, Playwright (снимки).

**Spec:** `/home/dev/incoming/kdm1-package/MASTER_PROMPT_KDM1_CLAUDE_CODE.md` (вне git) — §7 п.7 «Отделение
наблюдения документа, теории, телеметрии, расчета и гипотезы», §8 (абзацы про `data_bridge.py` и ряды), §6
(согласование); решения владельца 14.10 — `docs/checks/D16.md`, «Решения владельца»; известные ограничения Д16 —
там же. Интерфейсы подсистемы — `docs/kdm1/INTERFACES.md`.

## Global Constraints

- 19 защищённых файлов (`core/pcbk_core/data/*`, `core/pcbk_core/gateway/calc_api.py`) и разрешённый список тегов —
  НЕ МЕНЯТЬ (тест `core/tests/test_analytics_protected.py`). Импортировать из них можно.
- В git — ни одного имени тега ПЦБК: тесты только на синтетике `20FAKE_*` (`core/tests/fixtures/kdm1_corpus.py`).
  Перед коммитом — `bash /home/dev/.claude/jobs/429c159a/tmp/kdm1/taggate.sh` («тегов предприятия нет»).
- Телеметрия не передаётся в LLM ни в каком виде (ни значения, ни имена тегов): секция строится после выбора
  модели, только сервером.
- Имя тега историана для чтения — только `ResolvedLink.historian_tag` из принятой заявки `equipment_link`; ни текст
  вопроса, ни подпись корпуса, ни результат поиска по каталогу сами по себе чтение не включают (мастер-промпт §8:
  «Связь показателя с КДМ-1 согласуется отдельно, подпись PNG ее не устанавливает»).
- Отказ `DataService` показывается как есть (его `message`/`text`), не прячется и не переформулируется в «ошибку».
- Время значения — как отдаёт `DataService` (`time`, `asof`, `tz`), с явной подписью «время историана»; время
  браузера не подмешивается (мастер-промпт §8).
- Новых зависимостей нет. Тексты, комментарии, докстринги — по-русски, в стиле окружающего кода.
- Субагенты-исполнители правят только свои файлы; коммит — `git commit -m … -- <свои пути>`; запрещены `git stash`,
  `reset`, `checkout --`, `restore`, `clean`, `rebase`, `--amend`, `add -A`.

## Review Focus

1. Вопрос с подписью, у которой нет согласованной связи, — значение НЕ читается (ни одного вызова `tag_now`), в
   ответе ограничение с подсказкой, где предложить связь.
2. Связь согласована, затем режим `ANALYTICS_REVIEWERS` переключён на `grants` (решение студента перестаёт
   действовать) — следующий вопрос уже не читает значение.
3. Уточняющий вопрос, команда и отказ преданализа — данные не читаются вовсе (мастер-промпт §7 п.4: «уточнение до
   оплачиваемого вызова и чтения данных»).
4. `DataService` отказал (тег вне разрешённого списка, частота, учения) — секция-ограничение с текстом отказа, ответ
   не падает и справка по документам остаётся.
5. «Уровнемер в машинном бассейне» — уточнение предлагает два машинных бассейна (узлы), а не «машинное
   направление»; «что такое машинное направление» — по-прежнему определение без лишнего уточнения.

---

### Task 1: Телеметрия в ответе аналитики (core)

**Files:**
- Modify: `core/pcbk_core/analytics/models.py` (SectionKind), `core/pcbk_core/analytics/contracts/job-response.schema.json`
  (enum `kind`), `core/pcbk_core/analytics/rendering.py`, `core/pcbk_core/analytics/service.py`,
  `core/pcbk_core/gateway/analytics_api.py`, `docs/kdm1/INTERFACES.md`, `docs/kdm1/DRIFT.md`
- Test: `core/tests/test_analytics_service.py`, `core/tests/test_analytics_api.py` (или существующий файл тестов
  маршрутов аналитики — тот, где уже проверяются `reviews/requests`), `core/tests/test_analytics_rendering.py`

**Interfaces:**
- Consumes: `reviews.ReviewStore.resolved_link(link_id, *, reviewers_mode) -> ResolvedLink | None`;
  `data_bridge.tag_now/tag_period/catalog_search`; `terms.exact_tag_candidates(handle, text) -> list[str]`;
  `knowledge.get_rows(handle, "equipment_links", ids)`.
- Produces (Task 2 опирается на это дословно):
  - `SectionKind` += `"telemetry_observation"`; секция: `evidence_refs=[]`, `claim_id=None`, `rendered_by="server"`.
  - `GET /pcbk/api/analytics/links` → `200 {"links": [{"id": str, "display_label": str, "document_context": str,
    "state": "unbound" | "pending" | "linked", "historian_tag": str | null, "approved_by": str | null,
    "approved_at": str | null, "pending_request_id": str | null}]}` — порядок как в корпусе (по `id`).
    `linked` — есть `resolved_link`; `pending` — нет связи, но есть заявка `equipment_link` по этому `id` в статусе
    `pending`/`info_requested`; иначе `unbound`. Справка недоступна (нет корпуса) — 503, как у остальных маршрутов.
  - `GET /pcbk/api/analytics/links/{link_id}/candidates` → `200 {"status": str, "message": str | null,
    "candidates": [{"tag": str, "description": str, "unit": str | null}]}` — один `data_bridge.catalog_search(
    display_label, limit=10)`; `status`/`message` — из ответа `DataService` как есть (`message` есть только у
    отказа). Неизвестный `link_id` — 404. `DataService` не подключён — 503 `{"error": …}` тем же видом, что
    `_unavailable`.
  - `POST /pcbk/api/analytics/reviews/requests` с `kind="equipment_link"`: `subject_id` обязан быть `id` строки
    `equipment_links` текущего корпуса (иначе 400), `new_value` — имя тега по `data.sql.SAFE_NAME` (иначе 400).
  - `Capabilities.data_bridge_available` = `self.calc is not None` и у его роли нет `config_error`.

- [ ] **Step 1: Тесты (падают до реализации)** — на синтетическом корпусе (`20FAKE_*`, подпись из его
  `equipment_links`) и двойнике `DataService` (фиксирует вызовы):
  - `test_telemetry_unresolved_label_reads_nothing_and_explains` — связи нет → ни одного вызова `tag_now`, в ответе
    секция `limitation`, текст содержит подпись и слова «связь» и «не согласована».
  - `test_telemetry_resolved_label_shows_value_unit_time_quality` — принятая студентом заявка `equipment_link`
    (режим `all_students`) → `tag_now` вызван ровно с `[historian_tag]`, секция `telemetry_observation` содержит
    подпись, тег, значение, единицу, время из ответа двойника и «время историана»; в запрос к двойнику LLM (если
    путь с моделью) ни тег, ни значение не попали.
  - `test_telemetry_with_context_period_uses_tag_period` — `request.context.period` задан → `tag_period` с этими
    границами, в секции среднее/мин/макс и доля хороших точек из ответа двойника.
  - `test_telemetry_mode_switch_to_grants_stops_reading` — та же заявка, затем `ANALYTICS_REVIEWERS=grants` →
    чтения нет, ограничение.
  - `test_telemetry_not_read_on_clarification_control_refused` — три вопроса (уточнение, команда с подписью,
    отказ преданализа) → ни одного вызова двойника.
  - `test_telemetry_refusal_text_passed_through` — двойник отвечает отказом `{"status": "refused", "message": X}` →
    ограничение содержит X, карточки справки остаются.
  - `test_telemetry_at_most_three_labels` — 4 подписи со связями → 3 чтения + ограничение «показаны первые 3».
  - `test_links_list_states` и `test_link_candidates_proxy_catalog_search`, `test_create_equipment_link_validates_subject_and_tag`
    — маршруты выше (404/400/503 тоже).
  - `test_capabilities_data_bridge_available_follows_calc` — `calc=None` → `False`, двойник → `True`.
- [ ] **Step 2: Прогнать — падают.**
- [ ] **Step 3: Реализация.**
  - `rendering.render_telemetry_section(label: str, link: ResolvedLink, reply: dict) -> Section` и
    `rendering.render_telemetry_period_section(label, link, reply) -> Section` — тексты (точные заготовки, числа и
    поля подставляются из ответа `DataService` без пересчёта):
    - сейчас: «Данные историана по подписи {label} (тег {tag}; связь согласовал {approved_by},
      {approved_at:%d.%m.%Y}): {value} {unit} на {time} — время историана ({tz}); качество: {quality}.
      Это измеренное значение на момент ответа, не норматив и не уставка.» + `item_notes` через «; » если есть;
      дискретный тег — `state_label` вместо значения/единицы; `status != "ok"` у элемента — его `text`.
    - период: «Данные историана по подписи {label} (тег {tag}; связь согласовал …) за {период из reply["period"]}:
      среднее …, минимум …, максимум … {unit}; доля хороших точек … %.» + `notes` ответа.
    - ограничения: «Подпись {label} есть в документах КДМ-1, но её связь с тегом историана не согласована —
      значение не показывается. Предложить связь можно в разделе «Связи с историаном» этой вкладки.»;
      «Служба данных недоступна — значение по подписи {label} не показывается.»;
      «Служба данных отказала по подписи {label}: {message}»; «Показаны первые 3 подписи из {n}.»
  - `service._build_outcome`: после ветвей `needs_clarification`/`refused`/`control` (там чтения нет) и после выбора
    карточек — `_telemetry_sections(identity, request, handle)`: подписи вопроса из
    `terms.exact_tag_candidates`, оставить только подписи из `equipment_links` (подпись → id строк), до 3; для
    каждой — `self.reviews.resolved_link(id, reviewers_mode=self.settings.ANALYTICS_REVIEWERS)` (первая
    согласованная из строк этой подписи); есть → `tag_period` при `request.context.period`, иначе `tag_now`;
    результат → секция. Секции телеметрии идут ПЕРВЫМИ (после расчётной, если она есть). Ответ, где есть хотя бы
    одна `telemetry_observation`, — `completed` при `decision=="ready"`, как ответ с карточками. Сбой хранилища
    согласований — ограничение «служба недоступна», не падение задания.
  - `analytics_api.py`: два маршрута `links` выше и проверка `equipment_link` при создании заявки.
  - `docs/kdm1/DRIFT.md`: запись «Расширение контракта: `telemetry_observation`» — причина (мастер-промпт §7 п.7),
    что в ней (`evidence_refs` пуст: источник — служба данных, не корпус), где проверяется.
    `docs/kdm1/INTERFACES.md`: новые маршруты и секция.
- [ ] **Step 4: Прогнать тесты задачи и весь набор аналитики** — `cd core && uv run --python 3.12
  --with-requirements requirements.lock --with pytest pytest -q -p no:cacheprovider tests/test_analytics_*.py`.
- [ ] **Step 5: Коммит своих путей.**

### Task 2: Веб — секция телеметрии и раздел «Связи с историаном»

**Files:**
- Modify: `web/src/analytics.ts`, `web/src/analyticsApi.ts`, `web/src/texts.ts`, `web/src/components/AnalyticsPage.tsx`,
  `web/src/components/AnalyticsReviews.tsx`
- Create: `web/src/components/AnalyticsLinks.tsx`
- Test: `web/tests/analytics.test.ts`, `web/tests/AnalyticsPage.test.tsx`, `web/tests/AnalyticsLinks.test.tsx`

**Interfaces:** Consumes — маршруты и `SectionKind` из Task 1 дословно. Produces — компонент `AnalyticsLinks`.

- [ ] **Step 1: Тесты**: разбор `telemetry_observation` строгим парсером (`SECTION_KINDS`), подпись секции
  «Данные историана»; разбор ответов `links` и `candidates` (лишний ключ/неверный `state` — отказ разбора, как у
  остальных ответов); `AnalyticsLinks`: список со состояниями «не связана» / «на согласовании» / «связана: {тег}»;
  «Подобрать тег» → кандидаты (тег — описание, единица) → выбор + обоснование → `createReview({kind:
  "equipment_link", subjectId: id, newValue: tag, scope: "КДМ-1", rationale})` → состояние «на согласовании»;
  отказ `DataService` в кандидатах показывается текстом `message`; 401 → выход; в списке заявок
  (`AnalyticsReviews`) для `equipment_link` вместо `display-link-…` видна подпись.
- [ ] **Step 2–4:** реализация, `npx tsc --noEmit -p . && npx vitest run`, сборка в свой каталог
  `PCBK_WEB_OUT=/home/dev/.claude/jobs/429c159a/tmp/d17-web npm run build`.
- [ ] **Step 5: Коммит своих путей.**

### Task 3: Точность уточнений и мелкие хвосты распознавания (core)

**Files:**
- Modify: `core/pcbk_core/analytics/preanalysis.py`, `core/pcbk_core/analytics/calculations.py` (только таблица
  единиц, если она там)
- Test: `core/tests/test_analytics_preanalysis.py`, `core/tests/test_analytics_calculations.py`

- [ ] **Step 1: Тесты** (синтетический корпус; если в нём нет пары «узел из двух слов» + «термин с общим первым
  словом» — добавить в фикстуру, не трогая существующие записи):
  - `test_process_phrase_beats_partial_term_match` — «уровнемер в {узел-фраза в другом падеже}» → среди
    `missing_fields` есть `process` (если узлов с этой фразой два) и НЕТ `quantity` из-за термина, совпавшего
    только первым словом; кандидаты уточнения — названия узлов.
  - `test_term_phrase_still_resolves` — «что такое {термин из двух слов}» → определение без уточнения.
  - `test_single_ambiguous_word_still_clarifies` — одиночное спорное слово корпуса (вид «масса») по-прежнему
    даёт уточнение с вариантами.
  - `test_cubic_meters_per_hour_words` — «кубов в час», «кубометров в час», «куб/ч» → та же единица, что «м³/ч»;
    расчёт сухого вещества из «200 кубов в час, ρ=1 т/м³, 4 % по массе» = 8 т/ч.
  - Таблицы команд: «Заблокируй выход из зоны сканирования лентой», «Заблокируйте …», «Разблокируй …» — команда;
    «Почему заблокирован выход из зоны сканирования?» — не команда.
- [ ] **Step 3: Реализация.**
  - Приоритет фразы узла: соседние содержательные слова вопроса (после `_STOPWORDS`), чьи основы (`terms.
    normalize_ru`) вместе входят в набор основ `title`/`source_label` узла (`processes`), образуют фразу узла; её
    слова не идут в `terms.resolve_term`, а узлы с этой фразой добавляются к кандидатам узла (тот же путь, что
    `_candidate_process_ids`). Только пары и длиннее (одно слово «бассейн» фразой не считается).
  - Варианты уточнения участка: если в `missing_fields` есть `process` и кандидатов-терминов нет, подписи
    вариантов — `title` узлов-кандидатов (до `MAX_CLARIFICATION_CANDIDATES`).
  - Единицы и формы команд — таблицы модуля.
- [ ] **Step 4:** тесты задачи + весь набор аналитики (команда как в Task 1). Регрессионные таблицы Д16
  (`CONTROL_PHRASES*`, `SAFE_PHRASES*`) должны оставаться зелёными.
- [ ] **Step 5: Коммит своих путей.**

### Task 4 (контроллер): корпус с сокращениями Dify

Новая версия корпуса вне git: `knowledge/` пакета + 30 сокращений из
`/home/dev/incoming/pcbk-dify-abbrev/abbreviations-2026-10-14.json` как `terms` (`knowledge_state=
"editorial_definition_or_unresolved_candidate"`, `local_operating_authority=false`, расхождение НС/ВС — один термин
`ambiguous=true` с обоими значениями) + источник `LOCAL-DIFY-KB` с фрагментами-строками; сборка
`ingestion build` настоящим `tools/import_seed.py`, проверка `publish` локально, на сервере — новая версия рядом с
текущей и смена указателя (`docs/kdm1/CORPUS_OPERATIONS.md`).

### Task 5 (контроллер): выкладка Д17, уроки, журнал

Образ `core:d17`, статика чата; корпус новой версии; живая проверка под student_test (связь: предложить → утвердить
→ вопрос с подписью → значение); уроки 09–17 в пакете уроков на сервере со снимками Playwright; `docs/checks/D17.md`;
main + тег `platform-d17`, клон.
