# AGENTS.md

Инструкция для ИИ-агентов (Codex, Claude и других), работающих в этом репозитории.

## Что это

`pcbk-reserve` — учебный стенд: веб-чат с ИИ-агентами (OpenCode в изолированных контейнерах-местах),
служба данных к историану реального времени (MCP + HTTP), LLM-прокси к OpenRouter, шлюз (вход, места,
калькуляторы, уроки, кабинет преподавателя), сторож, вход nginx. Один серверный процесс `core` несёт
несколько ролей — `data`, `llm`, `gateway`, `kb` (и опционально сервис аналитики).

**Справочник по функциональности** — [`docs/reference/`](docs/reference/README.md): архитектура,
интерфейсы (HTTP/MCP), настройки, данные, сборка и перенос каждого компонента. Начни с
[`docs/reference/01-architecture.md`](docs/reference/01-architecture.md) и
[`docs/reference/README.md`](docs/reference/README.md) (там — порядок чтения под конкретную задачу).
`docs/README.md`, `docs/checks/`, `docs/plans/` — журнал того, как стенд строили по дням; для текущего
поведения кода не нужен.

## Структура каталогов

| Каталог | Что там |
|---|---|
| `core/pcbk_core/` | Серверный слой (роли `data`, `llm`, `gateway`, `kb`, аналитика) |
| `core/tests/`, `core/verify/` | Тесты и эталонные проверки серверного слоя |
| `student/` | Образ рабочего места OpenCode: `Dockerfile`, `pcbk-entrypoint`, `config/` (правила модели, агенты) |
| `web/` | Веб-чат, React SPA; собранная статика уходит в `edge/static/chat/` (в git не хранится) |
| `edge/` | Вход nginx: шаблон конфигурации, статика страницы входа |
| `watchdog/` | Сторож: проверки компонентов, страница состояния |
| `bench/` | Стенд отбора моделей — только машина разработчика, в образы и на сервер не едет |
| `tests/integration/` | Межкомпонентные и живые проверки всего стенда (нужен Docker/gVisor) |
| `deploy/` | Ранбук выкладки (`README.md`), список образов (`images.lock`), эксплуатационные скрипты `ops/` |
| `docs/reference/` | Этот справочник |
| `docs/kdm1/` | Пакет заказчика: защищённые файлы, расхождения, интерфейсы аналитики |
| `compose.yaml`, `compose.test.yaml` | Описание стенда и тестового стенда |

## Сборка

```bash
# серверный слой и сторож — есть секция build в compose.yaml
docker compose build core watchdog

# место студента собирается отдельно, без docker compose build
docker build -t pcbk-reserve/student:<тег> student/

# статика веб-чата (нужен Node 22.x); результат — в edge/static/chat/
cd web && npm ci && npm run build
```

Сторонние образы (`nginx`, `wollomatic/socket-proxy`, …) берутся только по тегу и дайджесту из
`deploy/images.lock`, без `pull` с Docker Hub на сервере.

## Тесты

| Часть | Команда |
|---|---|
| `core` (pytest; нужны зависимости из `requirements.lock`) | `cd core && uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q -p no:cacheprovider` |
| `watchdog` (только стандартная библиотека) | `cd watchdog && uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider && node --test tests/js/*.test.mjs` |
| `bench` (только стандартная библиотека) | `cd bench && uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider` |
| `core/verify` | `cd core/verify && uv run --python 3.12 --with pytest pytest -q -p no:cacheprovider` |
| `edge` (конфигурация nginx) | `node --test edge/tests/*.test.mjs` |
| `web` | `cd web && npm ci && npm test` |
| Межкомпонентные/живые (`tests/integration/`) | Требуют поднятого стенда (`compose.test.yaml`) и/или Docker с `runsc`; смотри `docs/reference/19-tests-and-bench.md` перед запуском — часть модулей ходит на сервер по SSH и ничего не трогает без явного договора |

## Правила

- **Производственные данные заказчика не коммитятся**: реестр и значения тегов историана, описания
  процесса и узлов участка, выгрузки историана, материалы курса. Они живут на стенде и у преподавателя, не
  в git.
- **Настоящие имена тегов предприятия не появляются в репозитории** — ни в коде, ни в примерах, ни в
  тестах, ни в документации. В примерах — только вымышленные имена (вида `20FAKE_001_PV`).
- **Секреты — никогда в git.** Пароль историана, ключи OpenRouter, пароли мест и токены передаются
  контейнерам только файлами (`secrets:` в `compose.yaml`, каталог вне репозитория), не переменными
  окружения.
- **Защищённые файлы КДМ-1** — список и их текущие хеши в
  [`docs/kdm1/PROTECTED_DATA_BASELINE.current.json`](docs/kdm1/PROTECTED_DATA_BASELINE.current.json). Правка
  — только добавлением (новая функциональность рядом со старой, без изменения существующего поведения),
  никогда переписыванием уже защищённого; после правки — пересчитать хеши в этом файле и записать, что и
  почему изменилось, в [`docs/kdm1/DRIFT.md`](docs/kdm1/DRIFT.md).
- Личные учётные записи и имена сотрудников заказчика не коммитятся.
- **Стиль кода**: комментарии и строки интерфейса — по-русски, как в окружающем коде; докстринг модуля —
  что делает и почему, без пересказа истории правок.
