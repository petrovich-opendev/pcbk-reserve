"""Аналитический чат технолога КДМ-1 (план `docs/plans/PLAN-KDM1-analytics-2026-10-14.md`, Task 0 — каркас).

Пакет без импортов на уровне модуля: его тянет роль gateway лениво, только когда `ANALYTICS_ENABLED`
(тот же приём, что у `data/`, `agents/` — предпроверка П8/П13). Поток запроса (`docs/kdm1/INTERFACES.md`):
gateway/analytics_api.py → service.py → preanalysis.py → retrieval.py/calculations.py →
(llm_adapter.py) → validator.py → rendering.py → store.py.

Модули (ответственность — в докстринге каждого; "Task N" — волна плана, которая его реализует):
- settings.py — настройки (Task 0, готово);
- models.py — закрытые модели контрактов и внутренние структуры (Task 0, готово);
- knowledge.py, terms.py, retrieval.py — корпус только для чтения, термины, поиск (Task 1);
- store.py — состояние диалогов/заданий (Task 2);
- preanalysis.py, calculations.py — преданализ и расчёты (Task 3);
- validator.py, rendering.py — проверка выбора LLM и серверное отображение (Task 3, доп. Task 5);
- llm_adapter.py — существующий LlmRole (Task 5);
- data_bridge.py, reviews.py — связь с DataService и согласование (Task 7);
- service.py — координатор заданий (Task 4).
"""
