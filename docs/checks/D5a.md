# Д5а — журнал проверок входа и мест

30.09.2026 · [Л] — проверено локально (машина разработки), [П] — на сервере ·
пароли, токены, cookie, адреса и имена хостов заказчика не пишутся — только
числа и вердикты.

## Задача А0. Утро

### Хвосты Д4б (шаг 1)

- Ждёт тега `platform-d4b` (окна владельца Д3а…Д4б). До тега шаг
  пропускается по плану; ветка `d5a/entry-workplaces` идёт от головы Д4б. [Л]
- `git merge-base --is-ancestor 1c02e8d d5a/entry-workplaces` → код 0. [Л]

### Имена и сигнатуры по голове Д4б (`1c02e8d`, шаг 2)

- `git grep` шага 2: все 23 имени на месте (`def create_app`, `class RoleBase`,
  `def hooks_of`, `def body_limit`, `def install_egress_guard`,
  `test_egress_targets_include_openrouter`, `DB_PATH`, `class FakeMono`,
  `def recreate`, `def http_host`, `def llm_health`, `def logs`,
  `def wait_for`, `def wait_until`, `def serve_app`, `def converse`,
  `def pause`, `KINDS`, `def check_llm`, `test_components_file_d4b`,
  `fake-openrouter`, `pcbk-core-data`, `config_error`). [Л]
- `def egress_targets` с `LlmSettings` → 1; `def build_roles(settings, …, env)`
  в `main.py` → есть; `LLM_ENV` в `core/tests/helpers.py` → есть; `refusal`
  в `hooks.py` → есть; `class LlmSettings` → есть; ручка `health/llm` → есть;
  `add_exception_handler|add_middleware` в `core/pcbk_core/data` и
  `core/pcbk_core/llm` → 0; `class AppHooks` → есть. [Л]
- **Вердикт:** расхождений с разделами Interfaces нет, правка плана «План Д5:
  имена по коду Д4» не нужна. Сигнатуры как построено сверены предпроверкой
  (`.superpowers`, раздел Г, 20 строк; план `4ba175e`). [Л]

### Предпроверки сервера (шаг 3)

- Ждут выкладки Д4б (окно владельца). Команды — в плане, задача А0, шаг 3. [П]

### Прогон и темп (шаг 4)

- Полный интеграционный прогон Д4б — 13:48, 91 passed (отчёт задачи 4 Д4б).
  Полный прогон Д5а после задачи А6 — **20:11, 109 passed** (отчёт А6; ревьюер
  из чистой копии — 20:30, 109/109). Прибавка 6:23 — меньше ожидаемых ~9 мин. [Л]
- Темп для правила деления (Ш19), **до закрытия Д4б**: промежуточный темп Д4б
  по задачам 1–4, `70b6297` (03:16:21Z) → `02bc03e` (04:58:31Z) = 1.70 ч на
  5.55 ч шкалы, доля **0.31** ≤ 0.5 — деления нет. [Л]
- Для справки: код Д5а, задачи А1–А6 с ревью и доводками, `7d9c1a5`
  (05:48:22Z) → `fe393a5` (08:48:06Z) = 3.00 ч. [Л]
