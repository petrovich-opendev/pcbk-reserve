# Расхождение защищённых файлов: архив заказчика ↔ рабочее дерево (Task 0)

Пакет заказчика (`PROTECTED_DATA_BASELINE.json`) фиксирует хеши 19 файлов от исследованного архива
`pcbk-reserve-main.zip` (SHA-256 архива `9b87f7273b6da95069835f7bd3485f28b7c3d3d1e36db0637c1162a802b11dba`,
исследование 13.10.2026, `.git` в архиве отсутствовал). Задача 0 плана требует сверить этот список с реальным
рабочим деревом **до** начала реализации и описать расхождение, а не молча принять архивные хеши как текущие.

## Результат сверки

16 из 19 файлов — побайтово совпадают с архивными хешами (см. `docs/kdm1/PROTECTED_DATA_BASELINE.current.json`,
пересчитано от `git HEAD` = `bea62db`, веткa `kdm1/analytics`, совпадает с main `042f1b1`).

3 файла отличаются — все три только докстрингом (размер в байтах не изменился: `01…10` → `01…11` — то же число
знаков), коммит **`46b633f`** («Д15б: core — 11 мест одной константой, перенос llm_shares»), сразу после
`9a63108` («Д15а: журнал выкладки и живой проверки...»), от которого, судя по совпадению остальных 16 хешей,
исследовался архив заказчика:

| Файл | Было (архив / `9a63108`) | Стало (`46b633f` → HEAD) |
|---|---|---|
| `core/pcbk_core/data/my_agents.py` | «только `student-01…10`, как `save_agent`» (×2 в докстринге) | «…`student-01…11`…» |
| `core/pcbk_core/data/report_event.py` | «вызывающий не `student-01…10` — refused» | «…`student-01…11`…» |
| `core/pcbk_core/data/save_agent.py` | «вызывающий не `student-01…10` (`ops` и прочие)» | «…`student-01…11`…» |

Причина — Д15б добавил 11-е рабочее место стенда (учётка-образец `student_test`, `pcbk_core/places.py:
PLACE_MAX = 11`, «было 10»); три докстринга в `data/` упоминали старый диапазон мест текстом и не были
синхронно обновлены при самом переносе логики диапазона в `pcbk_core.places` (тот перенос — тоже Д15б, тот же
коммит `46b633f`). Код этих трёх файлов (проверка вызывающего, частоты, запись) не менялся: диапазон мест для
решения «разрешён/отказ» эти модули берут из `pcbk_core.places.CALLER_RE`/`PLACES`, не из своего текста — сам
текст докстринга никогда не определял поведение, он только описывал его для человека.

С `46b633f` по текущий `HEAD` (`bea62db`) — различий нет: `git diff 042f1b1 HEAD --stat` по всем 19 путям
пуст, задача 0 (и этот отчёт) эти файлы не трогала.

## Что это значит для задачи 0 и дальше

- Исходная версия — **реальная рабочая ветка**, не архив (Global Constraints плана, п. «при отличии от архива
  используй реальную версию как исходную, сохранив запрет на изменение функций данных»): база хешей для
  всех последующих задач — `docs/kdm1/PROTECTED_DATA_BASELINE.current.json`, не исходный файл пакета.
- Функции доступа к тегам (`build_whitelist.py`, `gate.py`, `catalog.py`, `names.py` и остальные) не менялись
  ни в этом расхождении, ни задачей 0 — запрет Global Constraints не затронут.
- `core/tests/test_analytics_protected.py` (Task 0) сверяет рабочее дерево с
  `PROTECTED_DATA_BASELINE.current.json` (не с архивным `PROTECTED_DATA_BASELINE.json` пакета, который заведомо
  устарел на этот докстринг) — падает при изменении любого из 19 файлов относительно текущей базы, независимо
  от этого уже учтённого расхождения.

## Расширение контракта: `telemetry_observation` (Task 1, план Д17-telemetry-lessons, 14.10.2026)

Причина — мастер-промпт §7 п.7 («Отделение наблюдения документа, теории, телеметрии, расчёта и гипотезы») и §8
(`data_bridge.py`/ряды): до Д17 секция ответа `analytics/contracts/job-response.schema.json#kind`
(`models.SectionKind`) не несла вида секции для данных историана вовсе — Д16 сознательно держал
`capabilities().data_bridge_available` ВСЕГДА `False` (правка F1, п.2 итогового ревью), пока владелец не
принял формат секции и полномочия согласования связи оборудования (Task 7, уже реализовано). Д17 закрывает
этот отложенный пункт: `kind` enum получил значение `"telemetry_observation"` — в обоих местах одновременно
(`models.py` и копия схемы `contracts/job-response.schema.json`, `test_analytics_contracts.py` держит их
синхронными).

Что несёт секция: `text` — детерминированный серверный шаблон (`rendering.render_telemetry_section`/
`render_telemetry_period_section`) буквально из полей ответа прежнего `DataService` (значение/единица/время/
качество историана или среднее/минимум/максимум/доля хороших точек за период), без пересчёта;
`evidence_refs=[]` ВСЕГДА — источник значения не корпус (не `fragments`/`claims`), а служба данных, у неё нет
локатора вида `pdf_page`/`xlsx_cell`/`image_label`/`public_section`; `claim_id=None` — не карточка; `rendered_
by="server"`, как у всех секций. Имя тега историана для чтения — только `ResolvedLink.historian_tag` принятой
заявки `kind="equipment_link"` (`reviews.resolved_link`), не подпись вопроса/корпуса и не результат поиска по
каталогу — сама секция это не проверяет повторно, проверка уже сделана до вызова `render_telemetry_*_section`
(`service._telemetry_section_for_label`).

Где проверяется: `core/tests/test_analytics_rendering.py` (сами функции рендеринга — значение/период/четыре
ограничения, на синтетическом `ResolvedLink`/ответе-двойнике `DataService`, без корпуса и без реального
историана), `core/tests/test_analytics_contracts.py` (`test_job_response_accepts_telemetry_observation_
section_kind`/`test_job_response_schema_enum_includes_telemetry_observation` — модель и копия схемы не
расходятся), `core/tests/test_analytics_service.py` (`test_telemetry_*` — координатор: подпись без связи,
подпись со связью читает ровно один раз, период, отзыв `ANALYTICS_REVIEWERS`, непопадание в needs_clarification/
control/refused, отказ `DataService` как есть, предел трёх подписей на ответ), `core/tests/test_gw_analytics_
api.py` (`test_links_list_states`/`test_link_candidates_proxy_catalog_search`/`test_create_equipment_link_
validates_subject_and_tag` — настоящий HTTP-путь маршрутов `links/*` и валидация заявки `equipment_link`).
