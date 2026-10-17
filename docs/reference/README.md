# Справочник репозитория `pcbk-reserve`

Детальная документация по функциональности стенда — чтобы по исходникам быстро собрать продуктивную среду,
используя все или часть придуманных здесь компонентов. В отличие от `docs/README.md` (карта журналов
выпусков и планов по дням) этот набор описывает только **действующее** поведение кода: что делает
компонент, его интерфейсы, настройки, данные, зависимости и как его взять отдельно. Истории разработки
(«было/стало», дни, номера ревью) здесь нет — она в `docs/checks/` и `docs/plans/`.

Корневой `AGENTS.md` репозитория — короткая инструкция для ИИ-агента (Codex, Claude) со ссылкой на этот
справочник; он снаружи `docs/reference/`.

## Документы

| Файл | О чём |
|---|---|
| [01-architecture.md](01-architecture.md) | Архитектура: компоненты, контейнеры, роли `core`, путь запроса, хранение, границы доверия, карта репозитория, что можно взять отдельно |
| [02-build-and-deploy.md](02-build-and-deploy.md) | Сборка из исходников и развёртывание: полная среда с нуля, частичные сборки, обновление, откат |
| [03-configuration.md](03-configuration.md) | Все настройки: переменные окружения, файлы секретов, тома, сети, профили compose, конфиг мест |
| [10-data-service.md](10-data-service.md) | Служба данных: историан, каталог и белый список тегов, периоды и смены, пределы, кеш, MCP-инструменты и HTTP API, журнал событий, сохранение агентов |
| [11-calculations.md](11-calculations.md) | Расчёты: наработка, отклонение от задания, агрегаты, карта Шухарта, суммарный расход, формулы; вкладка «Калькуляторы» |
| [12-llm-proxy.md](12-llm-proxy.md) | LLM-прокси: OpenAI-совместимый вход, белый список моделей, ZDR, запасная модель, бюджет и доли мест, журнал вызовов, тормоз |
| [13-gateway.md](13-gateway.md) | Шлюз: учётки, вход, сессии, места (сон/пробуждение), прокси к OpenCode, перезагрузка мест, API вкладок, кабинет преподавателя |
| [14-workplaces-and-agents.md](14-workplaces-and-agents.md) | Рабочие места OpenCode: конфигурация, правила модели, агенты «Помощник» и «Конструктор», агенты студентов |
| [15-knowledge-and-lessons.md](15-knowledge-and-lessons.md) | База знаний в местах, поправки технологов, уроки |
| [16-analytics.md](16-analytics.md) | Аналитика корпуса (опциональная): ingestion, поиск, расчёты, согласования, контракты |
| [17-web-chat-and-edge.md](17-web-chat-and-edge.md) | Веб-чат (React SPA) и вход nginx: сборка, структура, API, страницы |
| [18-watchdog-and-ops.md](18-watchdog-and-ops.md) | Сторож: проверки, страница состояния; эксплуатационные скрипты |
| [19-tests-and-bench.md](19-tests-and-bench.md) | Тесты всех частей, стенд оценки моделей |
| [20-security.md](20-security.md) | Безопасность: границы доверия, токены, сети мест, прокси сокета Docker, охрана выхода, секреты, данные заказчика |
| [21-extending.md](21-extending.md) | Как расширять и переносить: новый инструмент службы данных, новая модель, новый агент, другой историан/предприятие, новая роль `core` |

## Порядок чтения

**Полная продуктивная среда** (всё как есть, на новом сервере):
[01-architecture.md](01-architecture.md) →
[03-configuration.md](03-configuration.md) →
[02-build-and-deploy.md](02-build-and-deploy.md) →
[20-security.md](20-security.md) →
по необходимости — документы ролей (10–16) и
[17-web-chat-and-edge.md](17-web-chat-and-edge.md),
[18-watchdog-and-ops.md](18-watchdog-and-ops.md),
[19-tests-and-bench.md](19-tests-and-bench.md).

**Только служба данных** (MCP/HTTP к историану, без чата и без прочих ролей):
[01-architecture.md](01-architecture.md) (разделы «Роли процесса `core`» и «Что можно взять отдельно») →
[02-build-and-deploy.md](02-build-and-deploy.md) (раздел 6.1 — `docker compose up -d --no-build core` и
обязательный полный набор секретов/томов из раздела 6) →
[10-data-service.md](10-data-service.md) →
[03-configuration.md](03-configuration.md) (переменные роли `data` и секреты историана) →
[21-extending.md](21-extending.md) (если нужен другой историан) →
[19-tests-and-bench.md](19-tests-and-bench.md) (тесты `core/tests`).

**Чат без историана** (веб-чат и место OpenCode с моделью, без доступа к реальным данным процесса):
[01-architecture.md](01-architecture.md) →
[02-build-and-deploy.md](02-build-and-deploy.md) (раздел 6.2 — последовательность
`core`/`watchdog`/`edge`/`sp-ro`/`sp-ctl` → места → пересоздание `watchdog`; `whitelist.txt` и секрет
`bdrv.env` можно оставить заглушками, но сами файлы обязаны существовать) →
[17-web-chat-and-edge.md](17-web-chat-and-edge.md) →
[14-workplaces-and-agents.md](14-workplaces-and-agents.md) →
[12-llm-proxy.md](12-llm-proxy.md) →
[13-gateway.md](13-gateway.md) (только вход и прокси к месту; калькуляторы и доли можно не поднимать) →
[03-configuration.md](03-configuration.md).
