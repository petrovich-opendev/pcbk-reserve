# Д3б. Ответ по тегу за смену — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Изменения по предпроверке (Д3а как построен)

Сверка — `.superpowers/sdd/PLAN-D3b-tag-answers-2026-10-02/preflight.md` против ветки
`d3a/data-service` (`aa4d004`). Все 25 решений приняты контроллером 29.09; текст задач ниже
уже исправлен по ним. Одна строка — одна находка: что изменено и где.

- **Предпосылка** — ослаблена: задачи 1 (шаги 1–6) и 2–6 идут на `d3b/tag-answers` от головы
  `d3a/data-service`, пока Д3а ждёт окна владельца; задачи 7–9 и слияние — после `platform-d3a`
  (шапка, задача 0).
- **F1** — форма `summary` в `TEMPLATE_FORMS` строится из тех же констант, что `summary_sql`
  (`SummaryShape`, `summary_form`); тест: вывод построителя проходит свою форму при всех
  сочетаниях `DATE_FMT` × `PIN_INTERPOLATION` × `HAS_LAST`, подделка — нет (задача 2; R7; задача 1, шаг 8).
- **F2** — охрана построителя — только в `if __name__ == "__main__"` (`main(guard=True)`);
  `main()` по умолчанию её не ставит; тесты — подмена установщика и `runpy` в подпроцессе (задача 3).
- **F3** — охрана службы — в `serve()`, не в `main()` и не в `build_roles`; `bdrv.env` читается
  тем же `try`, что в `build_roles` (`read_bdrv`); ошибка → охрана без направлений, процесс жив;
  тест точки входа в подпроцессе (задача 3; строка журнала — задачи 7, 8).
- **F4** — конструкторы без ввода-вывода: `EventLog` ленивый, `core-tokens` читается под `try` →
  `TokenTable.empty()` и причина «токены не читаются»; `helpers.SETTINGS` — пути во временном
  каталоге (задачи 4, 5, 7; Global Constraints; Review Focus 4).
- **F5** — `config_error` — первый шаг каждого инструмента и HTTP/MCP-обработчика (после токена):
  503 с причиной, событие `unavailable`, ноль вызовов `QueryFn` (задача 5; задача 0, шаг 2).
- **F6** — тесты эталона запускаются `cd core && CORE_PYTEST verify/` (задача 1, шаги 2, 4).
- **F7** — шаг 6 задачи 1 начинается с `docker image inspect pcbk-probe/tds:1.17.1`; образа нет —
  сборка по задаче 1 Д3а, шаг 1, в `$JOB/probe/` (+0,25 ч условно).
- **F8** — `session_manager.run()` — самым внешним в `lifespan`, до ветки `config_error`; тест MCP
  при ошибке настройки (задача 6; R10).
- **F9** — `writable()` идёт в фоне раз в 30 с через `asyncio.to_thread`; `health()` читает только
  запомненный итог (задача 4).
- **F10** — причина в детали «журнал событий не пишется: …» режется до 53 знаков (итог ≤ 80) и
  входит в `test_page_texts_fit_80_chars` (задача 4).
- **F11** — исход служебных событий — по таблице `OUTCOME_BY_CODE`, общей с инструментами; сырой
  код — в `summary` (задачи 4, 5).
- **F12** — `sent` — имена, переданные драйверу (при отказе входа — тоже); код не меняется (R7,
  Global Constraints, `CallEvent`, Review Focus 2).
- **F13** — фикстуры autouse с `setup_logging()` нет: тесты со строками журнала берут
  `restore_logging` (задача 5).
- **F14** — `ctypes` и `pytds` импортируются в `serve()` до охраны; стек под охраной проверяет тест
  точки входа (задача 3; R6).
- **F15** — `test_token_table` — в `test_skeleton.py` (задача 3).
- **F16** — пример события `EVENT` — в `helpers.py`; `test_role.py` импортирует его и `sqlite3` (задача 4).
- **F17** — `TOKEN = "test-token-student-01"` заводит задача 4 в `helpers.py`, задача 5 берёт оттуда.
- **F18** — `DataRole.tokens` по умолчанию `TokenTable.empty()`, не `None` (задачи 3, 4, 6).
- **F19** — `end < start` → «конец периода раньше начала»; `0 ≤ end − start < MIN_PERIOD` →
  «период короче минуты» (задача 2).
- **F20** — двойник читает список столбцов сводки из текста SQL своей регуляркой (задача 2).
- **F21** — `Period.open = is_open(end, now)` — то же правило `CLOSED_AFTER_S`, что у срока кэша;
  `CLOSED_AFTER_S` и `is_open` живут в `periods.py` (задачи 2, 3, 5).
- **F22** — карта файлов: даты и ww-значения пускает форма в `sql.py`; `gate.py` получает только
  `clear_pause`.
- **F23** — `core.db` и схема создаются в начале `lifespan` роли; том `pcbk-core-data` — ключ без
  `name:`, как `pcbk-watchdog-journal` (задачи 4, 7).
- **F24** — `docs/checks/D3b.md` создаёт задача 7; у задачи 1 — «Modify» (шаг 8).
- **F25** — петля критика без предела раундов (решение владельца 29.09, «Не лимитирую LOOP») (задача 9).
- **Часы** — задачи 2–6 +1,25 ч (2: 1,0; 3: 1,25; 4: 1,25; 5: 2,25; 6: 1,0), задача 1 — +0,25 ч
  условно (F7); черта отсечения сдвинута на конец девятого часа.

**Goal:** служба данных отвечает агентам и вебу: `catalog_search`, `tag_now`,
`tag_period` по HTTP и MCP, с токеном, бюджетом, кэшем и событием на каждый
вызов. Владелец видит по SSH-туннелю:
- ответ `tag_period` за предыдущую смену;
- независимый вердикт сверки этого ответа с сырым `Delta`;
- список инструментов по MCP;
- проверку журнала событий: в SQL ушли только имена из белого списка, чужое
  имя и попытка инъекции отклонены.

**Architecture:** продолжение Д3а в том же `pcbk_core`. К роли `data`
добавляются:
- `DataService` — три инструмента поверх ворот и каталога Д3а;
- `EventLog` — SQLite, поля `requested` и `sent`;
- HTTP `/api/data/{tool}` и MCP (`mcp==1.30.0`, stateless) — две оболочки над
  одной службой.

Ворота Д3а возвращают имена, переданные драйверу, и не пускают в SQL имя вне
белого списка. Журнал поэтому доказывает, а не повторяет, что ушло в историан.
В процесс ставится охрана выхода — в точке входа процесса, до того, как у
службы появятся вызывающие.

**Tech Stack:** как в Д3а; `mcp==1.30.0` (FastMCP, Streamable HTTP, stateless);
SQLite; pydantic.

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(§3 п. 3, §4, §9 «Успех 2», «Успех 4»); [`docs/research/05-d3-historian-facts.md`](../research/05-d3-historian-facts.md)
(рекомендации 2, 6–8, 10, 11); первая половина —
[`PLAN-D3a-data-service-2026-10-01.md`](PLAN-D3a-data-service-2026-10-01.md) —
её имена, Global Constraints и решения Д3а-R1…R5 действуют здесь; решения
этого плана пронумерованы дальше — Д3б-R6…R10. Формат и `stack` — планы Д1
и Д2; поток `/event` OpenCode и `edge` — [`docs/research/07-gateway-whitelist.md`](../research/07-gateway-whitelist.md)
(для Д5).

**Предпосылка (ослаблена 29.09 решением контроллера).** Задачи 1 (шаги 1–6) и
2–6 идут на ветке `d3b/tag-answers` от головы `d3a/data-service`, пока Д3а ждёт
окна владельца: Д3а в `main` ещё не влит, тега `platform-d3a` нет. Для задач 7–9
и шагов 7–9 задачи 1 предпосылка прежняя: Д3а выложен, влит в `main` с тегом
`platform-d3a`. Перед задачей 7 ветка `d3b/tag-answers` перебазируется на `main`
и проходит полный `cd core && CORE_PYTEST` — живые проверки Д3а могли поменять
`core`. Слияние Д3б в `main` — после `platform-d3a` (задача 9). Окно владельца
Д3б идёт после окна Д3а.

**Влезает ли в день — оценка по часам.** Задачи последовательны; в часы задач
с кодом входят 15 минут ревью и правок. Шкала — плановая, по ставкам Д1 и Д2,
без сжатия. Правки по предпроверке добавили задачам 2–6 1,25 ч.

| Задача | Часы | Где |
|---|---|---|
| 0. Хвосты Д3а (второй раунд критика и слияние, чистый клон — по черте Д3а) | 0–0,5 | — |
| 1. Эталон сверки и скрипт пробы: запросы 7–15, режим `verify` (шаги 1–6) | 1,5 (+0,25, если образа `pcbk-probe/tds:1.17.1` нет — F7) | локально |
| 2. Шаблоны с датами, форма `summary` и периоды | 1 | локально |
| 3. Охрана выхода в точках входа, токены, бюджет, кэш, single-flight | 1,25 | локально |
| 4. Разбор имён, поиск и журнал событий (ленивый, проверка в фоне) | 1,25 | локально |
| 5. Инструменты и HTTP (ошибка настройки — первым шагом) | 2,25 | локально |
| 6. MCP | 1 | локально |
| 7. Компоновка и выкладка | 1,5 | локально, сервер |
| 1б. Проба `morning-b`, константы по ней, пересборка и перевыкладка (задача 1, шаги 7–9) | 0,5 | сервер, **окно владельца** |
| 8. Живые проверки | 1 | сервер, **окно владельца** |
| 8а. Перевыкладка по `AVG_KIND` — только при закреплённой интерполяции или одном типе на всех (условно) | (0,5) | локально, сервер |
| 9. Закрытие дня | 1,25 | — |
| **Критический путь по плановой шкале** | **12,5** (с хвостами Д3а, 8а и F7 — до 13,75) | |

**Одно окно владельца около 1,5 ч — вечером:** проба (задача 1, шаги 7–9),
затем задача 8, шаги 3–6. До пробы константы стоят по умолчанию, тесты от них
параметризованы. После пробы — один коммит констант, пересборка и
перевыкладка `core` (около 15 минут) — и сразу ответ по туннелю и сверка.

**Принятое основание «один день»** — то же, что в Д3а: живой темп Д1 (план в
9,5 ч по этой шкале выполнен примерно за 2 ч 20 мин по часам, с субагентами).
Плановая шкала остаётся честной, а день укладывается в рабочий день по живому
темпу. Порог проверяется по строке «план / факт по git» задач 2–7 Д3а (они
выполнены; закрытие Д3а ждёт окна владельца): если темп Д3а вдвое медленнее Д1
или хуже, план Д3б пересчитывается до его начала, и владелец получает строку с
числами — или его явное «да» на день длиннее.

**Черта отсечения — конец девятого часа** от начала задачи 1 (было: конец
восьмого плюс хвосты Д3а; хвосты Д3а теперь идут в окне Д3а, а не перед кодом
Д3б). К ней зелёны задачи 1 (шаги 1–6) и 2–6 (по оценке — 8,25 ч; без образа
пробы Д3а — 8,5 ч). После черты порядок жёсткий:
задача 7, окно владельца (1б и 8), при нужде 8а, задача 9. **В Д4 ничего не
переносится:** Д4 начинается после тега `platform-d3b`.
- **Задача 5 или 6 не зелёна к черте.** Владельцу в тот же час уходит строка:
  Д3б кончится завтрашним утром, окно владельца переносится туда же, Д4
  сдвигается на полдня. Решение о сдвиге — его. Незелёная задача
  доделывается сверх оценки.
- **Окна владельца нет.** Задачи 1 (шаги 1–6) и 2–6 идут; задача 7 ждёт
  тега `platform-d3a` (выкладка Д3а — в его окне); проба и шаги 3–6 задачи 8
  ждут окна, выкладка идёт с константами по умолчанию. Владельцу — строка о
  сдвиге, как выше.
- **Сверка подтвердила другой вид среднего или не сошлась** (задача 8, шаг 4)
  — строка 8а, но только при `PIN_INTERPOLATION = "STAIRSTEP"` или при одном
  типе интерполяции на всех по запросу 14. Иначе вердикт записывается,
  `AVG_KIND` остаётся `"unverified"`: один тег не даёт права объявлять вид
  для всех тегов.

## Global Constraints

Действуют Global Constraints Д3а целиком. Д3б добавляет:

- **Проверка на секреты** перед каждым коммитом задач 1, 7, 8, 9 — тем же
  `git diff --cached … | grep -E -i -f <шаблоны>`. Если Д3б ведёт новая
  сессия и `$JOB` новый, файл шаблонов собирается заново по Д1, Д2 и Д3а:
  адреса, шаблон имён тегов, префиксы прежнего списка без цифры, `BDRV_PW` с
  кавычками. Затем положительный контроль, как в Д2 (задача 6, шаг 3), и
  `printf 'BDRV_PW="x1"\n' | grep -c -E -f <шаблоны>` → 1. Тестовый токен
  `TOKEN` — не 48 шестнадцатеричных знаков (`"test-token-student-01"`),
  иначе его поймает шаблон Д2.
- **Охрана выхода в процессе** (Д3б-R6) ставится только в точках входа
  процесса: в `serve()` службы и в `if __name__ == "__main__"` построителя.
  Функции, которые тесты зовут в процессе pytest (`main()`, `build_roles`,
  `build_whitelist.main` без `guard=True`), её не ставят: хук не снимается.
  Список разрешённого: историан (`BDRV_HOST` на `BDRV_PORT`, а если порт не
  задан из-за экземпляра — на любом TCP-порту) и петля. `bdrv.env` не
  читается → охрана без направлений (только петля), процесс не падает (Д3а,
  I2). OpenRouter добавит Д4.
- **Конструкторы без ввода-вывода** (F4): `DataRole`, `EventLog` и сборка MCP
  не открывают файлов. Файл журнала событий создаётся в начале `lifespan`
  роли, `core-tokens` читается в `build_roles` под `try` — сбой даёт причину
  на странице, а не падение процесса (Д3а, I2). Тесты пишут только во
  временные каталоги.
- **Ошибка настройки** (`config_error`, Д3а, I2) — первый шаг каждого
  инструмента и каждого HTTP- и MCP-обработчика после проверки токена: ответ
  `unavailable` с причиной (HTTP 503), событие, ни одного вызова `QueryFn`
  (F5).
- **Токены:** хеши sha256 в `${SECRETS_DIR}/core-tokens`; сами токены — файлы
  `<id>.data-token` только на сервере, по 48 шестнадцатеричных знаков
  (`openssl rand -hex 24`) — их ловит шаблон проверки Д2. В Д3б есть только
  `ops.data-token`, токены `student-NN.data-token` заводит Д4. В `.gitignore`
  — `core-tokens` и `*.data-token`.
- **Контракт:**
  - не больше 16 тегов, окно не длиннее 31 суток, до 288 точек (Д9), до 24
    тего-суток (для одного тега — 31); окно короче часа считается за час;
  - 60 вызовов за 5 минут на вызывающего;
  - строка тега — не длиннее 128 знаков, тело запроса — не больше 64 КиБ
    (считаются прочитанные байты, и тело без длины тоже).
- **Кэш:** закрытый период (конец старше 300 с) — 3600 с, открытый — 30 с,
  текущие значения — 15 с; «сейчас» — вниз до 30 с; до 256 записей, при
  переполнении уходит четверть с самым ранним сроком; single-flight. Ошибки и
  отказы в кэш не идут.
- **Каждый ответ говорит, что посчитано:** источник, вид среднего и σ, пояс,
  допущение смен, качество, возраст, доля достоверных. «Нет тега», «нет
  данных», «не в белом списке», «дискретный» — разными словами.
- **Событие на каждый вызов**, включая отказы, неверные аргументы (HTTP 422,
  любые ошибки аргументов MCP), неожиданные исключения (`outcome=error`) и
  служебные опросы. В событии — `requested` (сырой ввод), `tags`
  (канонические имена после разбора) и `sent` (имена, переданные драйверу:
  их возвращают ворота — и при ошибке после передачи, в том числе при отказе
  входа, когда SQL в историан не попал; завышение — в безопасную сторону,
  F12). 401 и 413 пишутся строкой журнала без токена.
- **Крючки уровня приложения** (обработчик 422, ограничитель тела) ставятся
  только через `Role.install(app)` из Д3а; `app.py` в Д3б не меняется.
- **MCP:** `mcp==1.30.0`, `FastMCP(..., log_level="WARNING")`, инструменты
  возвращают `dict[str, Any]`, режим stateless с ответом JSON, точный маршрут
  `/mcp`. Аннотации аргументов свободные, их тип проверяет служба теми же
  моделями, что HTTP.

## Решения по умолчанию (Ruling)

**Д3б-R6 — охрана выхода: растяжка, а не песочница.** Аудит-хук Python
(PEP 578) ловит случайный выход нашим кодом и библиотеками. От атакующего
внутри процесса он не защищает — PEP 578 так и говорит. Что хук делает:
- `socket.connect`: адрес вне списка → `EgressDenied` до системного вызова.
  Имя хоста в кортеже разрешается через кэш, и проверяется каждый адрес; не
  разрешилось — отказ;
- `socket.sendto` и `socket.sendmsg` на потоковом сокете — отказ: так TCP не
  уйдёт через TCP Fast Open мимо `connect`. Сверх того у `core` в
  `compose.yaml` — `sysctls: {net.ipv4.tcp_fastopen: "0"}`;
- `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`,
  `ctypes.dlopen` → `PermissionError("запуск процессов через subprocess/os запрещён")`.

Где ставится (F2, F3, F14):
- только в точках входа процесса — `serve()` и `if __name__ == "__main__"`
  построителя; в процессе pytest хука нет, случаи охраны идут в подпроцессах;
- `serve()` до охраны импортирует `ctypes` и `pytds`: на Python 3.12.13
  `import ctypes` под хуком проходит, на 3.14 падает `PermissionError`, а
  `pytds/login.py` импортирует `ctypes` на уровне модуля. Построитель
  импортирует `pytds` при загрузке модуля, до `main`;
- `bdrv.env` не читается → охрана без направлений, процесс живёт с причиной
  `config_error` на странице (Д3а, I2);
- `EgressDenied` из `pytds.connect` `tds_query` превращает в
  `HistorianError("connect")` (`except Exception` у входа): на странице —
  «нет связи с историаном», служба не падает.

Правила `DOCKER-USER` не ставим: нужен `sudo`, они не переживают перезагрузку,
адреса OpenRouter меняются, рядом работает Dify.

**Остаточный риск — обходы хука** (названы, а не закрыты):
- уже загруженный `ctypes` и прямые системные вызовы через него;
- `_posixsubprocess.fork_exec` в обход `subprocess`;
- DNS через 127.0.0.11 и UDP (`sendto` на датаграммах);
- нативный модуль с тома `/var/lib/pcbk-core`;
- на уровне сети из `pcbk-egress` открыт любой адрес (факт Д3а).

**Цена ошибки:** при взломе процесса атакующий получит сеть сервера.
Настоящая граница — правила `DOCKER-USER` для моста `pcbk-egress`:
предложение владельцу на Д12, с его `sudo`.

**Д3б-R7 — журнал доказывает белый список.**
- Ворота Д3а пускают в SQL только инструкцию, целиком совпавшую с формой из
  `sql.TEMPLATE_FORMS` (fullmatch), и только литералы-имена из `TagName IN (…)`
  белого списка. Д3б регистрирует форму `summary`, собранную из тех же
  констант, что `summary_sql` (F1), и пускает через `literal_ok` даты по
  `DATE_FMT` и закрытый набор ww-значений. Имена, переданные драйверу, ворота
  возвращают (`GateResult.sent`, а при ошибке — `HistorianError.sent`); при
  отказе входа SQL в историан не попал, а `sent` не пуст — завышение в
  безопасную сторону, код не меняется (F12).
- Чужое имя и шаблон с `TagName = '…'` ворота не пускают и считают в
  `refused_unlisted`.
- В событии — `requested`, `tags` и `sent`. Служебные вызовы пишутся с
  `caller="system"`, `channel="system"`; у каталога и полного снимка `Live`
  `sent` пуст, а `summary` — «без имён».
- Живая проверка сравнивает `sent` всех событий с `whitelist.txt` и делает
  отрицательный контроль: существующее имя вне списка и строка-инъекция.

**Д3б-R8 — вид среднего и σ** (О2; тип интерполяции задаётся и на уровне
тега):
1. Проба пробует сводку с `wwInterpolationType = 'STAIRSTEP'` (равенство, не
   `IN`). Если провайдер параметр принял, а `Average` совпал со средним по
   времени (ступенькой) при различимости `d ≥ 0,01`, параметр закрепляется в
   шаблоне: `PIN_INTERPOLATION = "STAIRSTEP"`, `AVG_KIND = "step"` для всех
   тегов.
2. Иначе смотрится распределение `InterpolationType` по `AnalogTag` в
   разбивке по `RawType`; значение 254 («по умолчанию системы»)
   разворачивается через `InterpolationTypeReal` и `…Integer` из
   `SystemParameter`. Вид объявляется, только если после разворота он один
   на всех и сводка с ним сошлась.
3. Иначе — `"unverified"`: вид среднего по тегам — вопрос в журнал долга.

**Цена ошибки:** неверное «взвешено по времени» в ответе. Ловит его вечерняя
сверка, которая печатает различимость.

**Д3б-R9 — мёртвые теги.**
- Строка `Live` с `Value IS NULL` → `no_value` («нет текущего значения»),
  время и качество сохраняются.
- Строка сводки, где `Average`, `Minimum` или `Maximum` — `NULL` или
  `PercentGood == 0`, → `no_data` («за период данных нет»).
- Нет строки — то же.

**Д3б-R10 — прочее.**
- Дискретный тег в `tag_period` → отказ по тегу со словами «наработка — в
  следующих слайсах».
- Имя агента в событии — `NULL`, решается в Д5 или Д7.
- «Последнее» за период выдаётся, только если у сводки есть столбец `Last` и
  он совпал с `Delta`.
- Метаданные тегов вне белого списка (описание, единица, шкала) в ответах не
  появляются.
- Аргументы MCP проверяет служба: схема сообщает типы и `maxItems` модели,
  а любой отказ (строка вместо списка, нет тегов, неизвестный период, 17
  тегов) идёт событием с русским текстом.
- `GET /mcp` → 405: клиент TS SDK понимает это как «поток не поддерживается».
- Сервер MCP строится один раз, в конструкторе роли (без ввода-вывода);
  `session_manager.run()` идёт один раз, в `lifespan()` роли, и самым
  внешним — до ветки `config_error` Д3а: иначе при ошибке настройки `/mcp`
  отвечает 500 «Task group is not initialized» (F8).
- `DataRole.tokens` — всегда `TokenTable`; по умолчанию `TokenTable.empty()`
  (всем 401), не `None` (F18).

## Review Focus

1. **Мёртвый тег белого списка приходит строкой с `NULL`.** Ожидание:
   `no_value` / `no_data`, а не `ok` со значением `null`. Тесты — задача 5,
   `test_tag_now_null_value_is_no_value`, `test_tag_period_null_row_is_no_data`.
2. **Журнал пишет, что передано драйверу, а не что прошло проверку**, — и
   при сбое после передачи (F12). Иначе проверка «успеха 4» тавтологична.
   Форма `summary` пускает `tag_period` в ворота (F1). Тесты — задача 5,
   `test_events_requested_vs_sent`, `test_timeout_event_keeps_sent`; задача 4,
   `test_freshness_poll_writes_sent`.
3. **Отказ случился до кода службы:** строка вместо списка или 17 тегов по MCP,
   422 и битый JSON без токена по HTTP, тело без длины больше 64 КиБ.
   Ожидание: событие `refused` с русским текстом; без токена — 401 и строка
   журнала, без события; 413 — строкой журнала. Тесты — задача 6,
   `test_mcp_bad_arg_types_are_refused_events`; задача 5,
   `test_http_422_is_refused_event`, `test_bad_json_without_token_is_401`,
   `test_chunked_body_over_limit_is_413`.
4. **Журнал событий не пишется** — диск полон или свежий том принадлежит root.
   Ожидание: процесс жив и не уходит в цикл перезапусков (конструктор без
   ввода-вывода, F4), инструмент отвечает, строка «Служба данных» красная —
   «журнал событий не пишется», проверка записи — в фоне, не в `health()`
   (F9); на выкладке том пишется от uid 10003. Тесты — задача 4,
   `test_events_disk_full_turns_health_red`,
   `test_events_check_off_loop_and_health_without_io`; задача 5,
   `test_tool_answers_when_events_fail`; задача 7,
   `test_core_state_volume_writable`.
5. **MCP не ломает роль и чужие пути:** роль без приложения запускается, MCP
   строится один раз, `/mcp` — точный маршрут, Host `core:8000` проходит, при
   ошибке настройки `/mcp` отвечает отказом, а не 500 (F8). Тесты — задача 6,
   `test_lifespan_without_app_runs`, `test_mcp_auth_host_and_get`,
   `test_mcp_with_config_error_answers_not_500`.
6. **Ошибка настройки не доходит до историана** (F5): сломан белый список при
   целом `bdrv.env` — роль держит настоящий `tds_query`, а ворота пускают
   `clock_sql` по полосе людей. Ожидание: каждый инструмент и обработчик
   первым шагом отвечает `unavailable` с причиной (HTTP 503), ни одного вызова
   `QueryFn`. Тесты — задача 5, `test_config_error_never_enters_historian`,
   `test_config_error_http_is_503_with_reason`.
7. **Охрана выхода — только в точках входа** (F2, F3, F14): в процессе pytest
   хука нет; `serve()` при плохом `bdrv.env` живёт с охраной без направлений.
   Тесты — задача 3, `test_pytest_process_has_no_guard`,
   `test_serve_installs_guard_and_stays_up`, `test_builder_entry_point_installs_guard`.

---

## Карта файлов

```
core/verify/delta_stats.py            эталон сверки; службой не импортируется
core/verify/test_delta_stats.py
core/pcbk_core/egress.py              охрана выхода (аудит-хук), install_historian_guard
core/pcbk_core/secrets.py             + TokenTable (в т. ч. empty())
core/pcbk_core/settings.py            + TOKENS_FILE, DB_PATH
core/pcbk_core/main.py                + read_bdrv; охрана выхода — в serve() (точка входа); токены под try в build_roles
core/pcbk_core/data/sql.py            + DATE_FMT, PIN_INTERPOLATION, HAS_LAST, SummaryShape, SUMMARY_COLUMNS, lit_dt, summary_sql,
                                        summary_form, TEMPLATE_FORMS["summary"], allowed_literal
core/pcbk_core/data/periods.py        round_now, CLOSED_AFTER_S, is_open, готовые периоды и смены
core/pcbk_core/data/budget.py         Limits, cost_tag_days, check_budget
core/pcbk_core/data/cache.py          TTLCache, SingleFlight, ttl_for, cache_key (round_now, is_open — из periods)
core/pcbk_core/data/catalog.py        + lookup, search, похожие
core/pcbk_core/data/events.py         CallEvent, EventLog (ленивый), OUTCOME_BY_CODE
core/pcbk_core/data/service.py        DataService: три инструмента
core/pcbk_core/data/http_api.py       POST /api/data/{tool}; 422, 401, 413, 503 при config_error
core/pcbk_core/data/mcp_server.py     MCP Streamable HTTP
core/pcbk_core/data/__init__.py       DataRole: + события, служба, роутер, install, MCP; literal_ok=allowed_literal
core/pcbk_core/data/gate.py           + clear_pause (только для тестов)
core/pcbk_core/data/build_whitelist.py  + охрана выхода только в __main__ (main(guard=True))
core/Dockerfile                       + каталог /var/lib/pcbk-core от uid 10003
compose.yaml                          core :d3b, секрет core-tokens, том pcbk-core-data (ключ без name:)
.gitignore                            + core-tokens, *.data-token
deploy/README.md                      + токены, журнал событий, MCP
tests/integration/conftest.py         + тестовый токен; http_host(..., token=)
tests/integration/test_core.py        + вызовы через egress, 401, журнал
tests/integration/test_edge.py        IMAGES: core :d3b
docs/checks/D3b.md, docs/checks/D3b/*.png  журнал и снимок страницы после выкладки (создаёт задача 7)
```

Имена: образ `pcbk-reserve/core:d3b`; том `pcbk-core-data` (имя с префиксом проекта) →
`/var/lib/pcbk-core`; секрет Compose `core-tokens` → `/run/secrets/core-tokens`;
пробный образ `pcbk-probe/tds:d3b`.

---

### Task 0: Хвосты Д3а

- [ ] **Step 1:** Всё, что черта Д3а перенесла сюда: второй раунд критика
  (тогда слияние и тег `platform-d3a` — после него), проверка чистым клоном,
  факты о сети выхода (задача 9 Д3а, шаг 5 — тогда они делаются в задаче 8,
  шаг 6). Expected: у каждого хвоста вердикт в `docs/checks/D3a.md`. Ветка
  `d3b/tag-answers` уже идёт от головы `d3a/data-service` (предпосылка
  ослаблена); после тега `platform-d3a` и до задачи 7 она перебазируется на
  `main`, затем `cd core && CORE_PYTEST` — PASS.
- [ ] **Step 2:** Из ревью задачи 5 Д3а: роль «данные» в состоянии
  `config_error` (сломан белый список при целом `bdrv.env`) держит настоящий
  `tds_query`, а ворота пускают `clock_sql` по полосе людей и при пустом
  списке. Исполняет задача 5 (F5): шаг 0 порядка инструментов, 503 с
  причиной, ни одного вызова `QueryFn`; тесты
  `test_config_error_never_enters_historian` и
  `test_config_error_http_is_503_with_reason`. Здесь кода нет.

---

### Task 1: Эталон сверки и проба — запросы 7–15, режим `verify`

**Нужен владелец:** шаг 7 читает производственные данные — он идёт вечером, в
одном окне с задачей 8. Шаги 1–6 — утром, локально. Проба закрывает О4
(формат даты) и О2 (столбцы сводки, вид среднего и σ, интерполяция). Кроме
того, она выбирает тег для вечерней сверки и имя для отрицательного контроля.
Эталон `delta_stats` — в репозитории с тестами. Скрипт пробы `probe_b.py` —
самостоятельный, в `$JOB` этой сессии: на исходники пробы Д3а он не
опирается.

**Files:**
- Create: `core/verify/delta_stats.py`, `core/verify/test_delta_stats.py`
- Modify (шаг 8, после задачи 7): `core/pcbk_core/data/sql.py`, `core/pcbk_core/data/service.py`
  (константы), `docs/checks/D3b.md` — файл создаёт задача 7 (F24)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Point: t: datetime; v: float | None; good: bool`
  - `@dataclass(frozen=True) class Stats: n: int; min: float; max: float; last: float; mean_arith: float; mean_step: float; mean_linear: float; std_arith: float; std_step: float; covered_s: float`
  - `delta_stats(points: Sequence[Point], start: datetime, end: datetime) -> Stats` —
    точки обрезаются по `[start, end]`. Ступенька: значение держится до
    следующей точки, у последней — до `end`. Линейная: трапеция между
    соседними хорошими точками, хвост — ступенька. Плохая точка
    (`good=False` или `v is None`) открывает разрыв; разрывы не входят ни в
    средние, ни в `covered_s`. `std_arith` — выборочное (n − 1), `std_step` —
    по времени вокруг `mean_step`. Хороших точек нет → `ValueError`. Только
    stdlib, импорта `pcbk_core` нет.
  - `gap(a: float, b: float, scale: float) -> float` = `|a − b| / max(scale, |b|, 1e-9)`
    — без деления на ноль у ровного сигнала.
  - `discrimination(st: Stats) -> float` = `gap(st.mean_step, st.mean_arith, st.max − st.min)`.
  - Образ `pcbk-probe/tds:d3b` на сервере: режимы `morning-b` и
    `verify --date-fmt A|B`.
  - Файлы `/opt/pcbk-reserve/probe-out/check-tag` и `unlisted-tag` (`0400`,
    имена только там).
  - Решения для задач 2 и 5 — таблица шага 8; до неё — значения по
    умолчанию.

- [ ] **Step 1: Write the failing tests**

```python
# core/verify/test_delta_stats.py
S, H = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 7)

def pts(*pairs):
    return [Point(S + timedelta(minutes=m), v, True) for m, v in pairs]

def test_step_vs_arith_discriminates():
    st = delta_stats(pts((0, 0.0), (50, 10.0)), S, H)
    assert st.mean_step == pytest.approx(10 * 10 / 60) and st.mean_arith == pytest.approx(5.0)
    assert st.mean_linear == pytest.approx(5 * 50 / 60 + 10 * 10 / 60)
    assert (st.min, st.max, st.last, st.n) == (0.0, 10.0, 10.0, 2)
    assert discrimination(st) == pytest.approx((5.0 - 10 / 6) / 10)

def test_constant_signal_and_zero_scale_gap():
    st = delta_stats(pts((0, 7.0), (20, 7.0), (40, 7.0)), S, H)
    assert st.mean_step == st.mean_linear == st.mean_arith == 7.0 and st.std_step == 0.0
    assert discrimination(st) == 0.0 and gap(7.0, 7.0 + 1e-15, 0.0) < 1e-12

def test_bad_points_open_gaps():
    ps = pts((0, 1.0)) + [Point(S + timedelta(minutes=30), None, False)] + pts((45, 3.0))
    st = delta_stats(ps, S, H)
    assert st.covered_s == pytest.approx(45 * 60) and st.mean_step == pytest.approx((30 + 45) / 45)
    assert (st.mean_arith, st.n) == (pytest.approx(2.0), 2)

def test_points_outside_window_ignored_and_empty_raises():
    assert delta_stats(pts((-10, 100.0), (0, 1.0), (70, 100.0)), S, H).max == 1.0
    with pytest.raises(ValueError):
        delta_stats([Point(S, None, False)], S, H)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core && CORE_PYTEST verify/`, то есть
`cd core && uv run --python 3.12 --with-requirements requirements.lock --with pytest pytest -q verify/`.
Из `core/verify` без зависимостей запускать нельзя (F6): pytest берёт
настройки из `core/pyproject.toml`, а там `filterwarnings` с категорией
`starlette.exceptions.StarletteDeprecationWarning` — без starlette выйдет
`UsageError`, а не ожидаемое падение.
Expected: FAIL — `ModuleNotFoundError: No module named 'delta_stats'`

- [ ] **Step 3: Implement `delta_stats.py` по интерфейсу**

- [ ] **Step 4: Run tests to verify they pass** — та же команда. Expected: PASS (4 теста).
  Полный `cd core && CORE_PYTEST` собирает и `verify/` — так и должно быть.

- [ ] **Step 5: Commit** — `git add core/verify/ && git commit -m "Эталон сверки: среднее по времени и арифметическое по сырому Delta"`.

- [ ] **Step 6: Пробный образ `pcbk-probe/tds:d3b` (в `$JOB/probe-b/`)**

Сначала основа (F7): `docker image inspect pcbk-probe/tds:1.17.1 --format '{{.Id}}'`.
Задача 1 Д3а к началу Д3б могла ещё не исполниться (она — в окне владельца
Д3а), и образа может не быть. Если его нет — собрать по задаче 1 Д3а, шаг 1, в
`$JOB/probe/`: `FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`,
`req.txt` — `python-tds==1.17.1` по `uv pip compile --generate-hashes`,
установка `--require-hashes --no-deps`, `probe.py` режима `morning-a` по правилам
того шага. Это тот же образ, который окно Д3а повезёт на сервер: Id образа —
в отчёт задачи, контроллер отметит в журнале Д3а, что шаг 1 его задачи 1
сделан. На сервер
отдельно его не везти: шаг 7 переносит `pcbk-probe/tds:d3b` через `docker save`
вместе со слоями основы. Expected: `docker image inspect pcbk-probe/tds:1.17.1`
отвечает Id.

`FROM pcbk-probe/tds:1.17.1` — образ Д3а с python-tds. Сверху — копия
`core/verify/delta_stats.py` и самостоятельный
`probe_b.py` с режимами `morning-b` и `verify`,
`ENTRYPOINT ["python", "/app/probe_b.py"]`. Правила:
- учётные данные — из `/run/secrets/bdrv.env` (разбор как у `read_env_file`),
  соединение — как у службы;
- печатаются только агрегаты, имён и значений нет;
- литерал даты — собственная функция пробы, не `sql.py`;
- окно — последний полный час по `GETDATE()`; если сегодня день равен месяцу
  (10.10), — тот же час вчера: такой день форматы не различает;
- **кандидаты — только из белого списка**: `whitelist.txt` (смонтирован
  `:ro`) ∩ аналоговые по каталогу ∩ живые по снимку `Live`, по свежести —
  как `freshest` Д3а. Выходы регуляторов `_LMN` и прочие служебные хвосты
  меняются каждый скан, но вечером дали бы «не в белом списке».

| № | Запрос | Что печатается |
|---|---|---|
| 6а | `live_all_sql()` Д3а | строк, секунд |
| 6б | `catalog_sql()` Д3а | строк, секунд; кандидаты — пять самых свежих тегов множества «белый список ∩ аналоговые ∩ живые» |
| 7–8 | `SELECT DateTime, Value, Quality FROM AnalogHistory WHERE TagName = '<t>' AND DateTime >= '<a>' AND DateTime <= '<b>' AND wwRetrievalMode = 'Delta'` для первого кандидата — формат **A** `YYYY-MM-DD HH:MM:SS.000` и **B** `YYYYMMDD HH:MM:SS.000` | на формат: «внутри окна» / «вне окна» / «пусто» / «ошибка: <класс>» (О4) |
| 9–12 | тот же `Delta` для остальных четырёх кандидатов, прошедший формат | `discrimination` каждого; доля `Quality = 0` |
| 13 | `SELECT * FROM AnalogSummaryHistory WHERE TagName = '<лучший по d>' AND StartDateTime >= '<a>' AND EndDateTime <= '<b>' AND wwCycleCount = 1 AND wwInterpolationType = 'STAIRSTEP'` | принят ли параметр; имена столбцов; `gap` у `Average` к трём средним, у `StdDev` — к двум σ; `Minimum`/`Maximum` = min/max по всем точкам и только по хорошим; `PercentGood`; `Last` = последнему, если столбец есть |
| 13б | то же без `wwInterpolationType`, если 13 дал ошибку | то же |
| 14 | `SELECT RawType, InterpolationType, COUNT(*) FROM AnalogTag GROUP BY RawType, InterpolationType` и `SELECT Name, Value FROM SystemParameter WHERE Name = 'InterpolationTypeReal'` (отдельно — `…Integer`) | распределение (только числа); значение 254 («по умолчанию системы») разворачивается по типу тега через параметр системы |
| 15 | без нового запроса, по каталогу 6б | первое имя участка вне `whitelist.txt` — в `/out/unlisted-tag`; **два** лучших по `d` тега — в `/out/check-tag` (второй — запас на вечер); проба сама проверяет, что оба входят в белый список, и печатает только «check-tag в белом списке: да/нет» |

Режим `verify --date-fmt A|B` читает из stdin ответ `tag_period`. Он
запрашивает свои часы и сырой `Delta` по тегу ответа за его период, с
`AND wwCycleCount = 200000`, и печатает:
- `bounds`: 06/14/22, длина 8 ч, пояс ответа равен своему;
- `d` и `gap` к трём средним и двум σ;
- вид: «подтверждён <вид>», только если `d ≥ 0,01`, расхождение с ним не
  больше `1e-3` и хотя бы в 10 раз меньше, чем со вторым; иначе «вид не
  различим на этом теге» или «не сошёлся»;
- `min`, `max` (`gap ≤ 1e-9`) — по всем точкам и только по хорошим, плюс
  `PercentGood` ответа: расхождение из-за правила качества видно сразу;
- `last`, если поле есть;
- `rows`: порядок числа строк и «обрезано», если их 200000;
- `outside`: число строк `Delta` вне `[start, end]` — ожидание 0.

- [ ] **Step 7: Проба `morning-b` (окно владельца, вечером)**

`install -d -m 0700 /opt/pcbk-reserve/probe-out`; перенос образа (`docker save … | $SSH …`,
сверка `RootFS`); затем
`$SSH 'docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro -v /opt/pcbk-reserve/data/whitelist.txt:/data/whitelist.txt:ro -v /opt/pcbk-reserve/probe-out:/out pcbk-probe/tds:d3b morning-b' > "$JOB/probe-b.json"`
(сеть — как в Д3а). `grep -E -i -f <шаблоны> "$JOB/probe-b.json"` — пусто.
Expected: ответ на каждый запрос; оба файла в `probe-out` есть (`0400`);
«check-tag в белом списке: да».

- [ ] **Step 8: Решения по пробе — константы, пересборка, перевыкладка**

| Итог | Решение | По умолчанию |
|---|---|---|
| О4: B «внутри окна» | `DATE_FMT = "%Y%m%d %H:%M:%S.000"` | `"%Y-%m-%d %H:%M:%S.000"` (работал 08.09) |
| О4: только A «внутри окна» | `DATE_FMT = "%Y-%m-%d %H:%M:%S.000"` | — |
| О4: ни один | стоп: периоды не строятся; вопрос владельцу, строка о сдвиге | — |
| 13 принят, `d ≥ 0,01`, `Average` ближе к ступеньке хотя бы в 10 раз и `gap ≤ 1e-3` | `PIN_INTERPOLATION = "STAIRSTEP"`, `AVG_KIND = "step"` | `PIN_INTERPOLATION = None`, `AVG_KIND = "unverified"` |
| 13 не принят; 14 — один тип на всех после разворота 254; 13б сошёлся с ним так же | `AVG_KIND` = этот вид | — |
| иначе | `AVG_KIND = "unverified"`, вопрос в журнал долга | — |
| `StdDev` — так же, порог `1e-2` | `STD_KIND` = `"step"` или `"arith"` | `"unverified"` |
| есть `Last` и `LastDateTime`, `Last` = последнему `Delta` | `HAS_LAST = True`, столбцы — в `SUMMARY_COLUMNS` | `False` |

Константы правятся в `core/pcbk_core/data/sql.py` (только строки `DATE_FMT`,
`PIN_INTERPOLATION`, `HAS_LAST`) и `service.py` (`AVG_KIND`, `STD_KIND`).
`SummaryShape`, `SUMMARY_COLUMNS` и форма `TEMPLATE_FORMS["summary"]` строятся
из этих же констант (задача 2, F1) — руками их не трогать: так ворота не
разойдутся с `summary_sql`. Затем `cd core && CORE_PYTEST` — тесты
параметризованы константами, `test_summary_form_registered_from_module_constants`
сверяет форму с новыми значениями. Дальше `docker compose build core` и
перевыкладка способом задачи 7, шаг 6 (около 15 минут). Expected: вердикты и
таблица с принятыми значениями — в `docs/checks/D3b.md` (файл создала задача 7);
`core` снова `healthy`; `/health/historian` → `gate.refused_unlisted: 0` после
вызова `tag_period`.

- [ ] **Step 9: Commit** (после проверки на секреты) — `git add core/ docs/checks/D3b.md && git commit -m "Д3б: проба — формат даты, вид среднего, интерполяция; константы службы по ней"`.

---

### Task 2: Шаблоны с датами и периоды

**Files:**
- Modify: `core/pcbk_core/data/sql.py` (+ форма `summary` в `TEMPLATE_FORMS`),
  `core/pcbk_core/data/__init__.py` (ворота получают `literal_ok=allowed_literal`;
  у `HistorianGate` параметр `literal_ok` уже есть — `gate.py` не меняется, F22),
  `core/tests/fakes.py` (сводка)
- Create: `core/pcbk_core/data/periods.py`
- Test: `core/tests/test_sql.py` (+5), `core/tests/test_gate.py` (+1), `core/tests/test_periods.py`

**Interfaces:**
- Consumes: `lit_name`, `names_in`, `SAFE_NAME`, `MAX_NAMES`, `_NAME_LIST`
  (список имён формы, `sql.py:64`), `TEMPLATE_FORMS`, `template_of` — Д3а
  (`sql.py:62–79`: ворота пускают только инструкцию, целиком совпавшую с
  формой, иначе `GateRefused("unlisted")`, `gate.py:202–205`); решения задачи 1.
- Produces (`sql.py`) — всё, что зависит от пробы, строится из трёх констант,
  и форма, и SQL (F1):
  - `DATE_FMT: str = "%Y-%m-%d %H:%M:%S.000"`, `PIN_INTERPOLATION: str | None = None`,
    `HAS_LAST: bool = False` — единственные строки, которые правит задача 1, шаг 8;
  - `BASE_SUMMARY_COLUMNS = ("Minimum", "Maximum", "Average", "StdDev", "PercentGood")`,
    `LAST_COLUMNS = ("Last", "LastDateTime")`;
  - `@dataclass(frozen=True) class SummaryShape: date_fmt: str = DATE_FMT; pin: str | None = PIN_INTERPOLATION; has_last: bool = HAS_LAST`
    со свойством `columns` = `BASE_SUMMARY_COLUMNS` плюс `LAST_COLUMNS` при
    `has_last`; `SHAPE = SummaryShape()`; `SUMMARY_COLUMNS = SHAPE.columns`;
  - `lit_dt(dt: datetime, date_fmt: str = DATE_FMT) -> str` — только наивное
    время, иначе `ValueError`; секунды без долей;
  - `summary_sql(names, start, end, shape: SummaryShape = SHAPE) -> str` =
    `SELECT TagName, <shape.columns через ", "> FROM AnalogSummaryHistory WHERE TagName IN (…) AND StartDateTime >= <lit_dt> AND EndDateTime <= <lit_dt> AND wwCycleCount = 1`,
    плюс ` AND wwInterpolationType = '<shape.pin>'`, если `pin` задан; список
    имён — как у `live_sql` (`lit_name`, от 1 до `MAX_NAMES`, через `", "`);
  - `summary_form(shape: SummaryShape = SHAPE) -> re.Pattern[str]` — та же
    строка по тем же частям: заголовок со столбцами `shape.columns` —
    `re.escape`, список имён — `_NAME_LIST` Д3а, каждая дата — `'<регулярка по
    shape.date_fmt>'` (`%Y` → `\d{4}`; `%m`, `%d`, `%H`, `%M`, `%S` → `\d{2}`;
    прочие знаки — `re.escape`; другая директива — `ValueError` при импорте),
    хвост с `pin` — только если он задан;
  - `TEMPLATE_FORMS["summary"] = summary_form()` — регистрируется в модуле рядом
    с формами Д3а; других новых форм Д3б нет (часы и `live` — формы Д3а);
  - `WW_CONSTANTS = frozenset({"STAIRSTEP"})`;
    `allowed_literal(text: str, shape: SummaryShape = SHAPE) -> bool` —
    `datetime.strptime(text, shape.date_fmt)` проходит или `text` из
    `WW_CONSTANTS`. `DataRole` передаёт её воротам как `literal_ok`; прочие
    литералы по-прежнему должны быть именами из `TagName IN (…)`.
- Produces (`periods.py`):
  - `round_now(now: datetime) -> datetime` — вниз до 30 с (ею же пользуется
    кэш задачи 3);
  - `CLOSED_AFTER_S = 300`; `is_open(end: datetime, now: datetime) -> bool` =
    `end > now − CLOSED_AFTER_S` — одно правило и для заметки «период ещё
    идёт», и для срока кэша задачи 3 (F21);
  - `SHIFT_STARTS = (6, 14, 22)` (допущение, решение №6);
    `PRESETS = ("last_hour", "last_8h", "last_24h", "current_shift", "prev_shift", "today", "yesterday", "last_7d", "last_30d")`;
    `MIN_PERIOD = timedelta(seconds=60)`; `EARLIEST = datetime(2000, 1, 1)`
  - `@dataclass(frozen=True) class Period: start: datetime; end: datetime; preset: str | None; shift_assumed: bool; clamped: bool; open: bool`
  - `class PeriodError(ValueError)`
  - `resolve_period(now: datetime, offset: timedelta, preset: str | None = None, start: str | None = None, end: str | None = None) -> Period`:
    - «сейчас» — вниз до 30 с (`now_r = round_now(now)`); способ задать
      период — ровно один;
    - ISO 8601 с поясом переводится в местное через `offset`, без пояса —
      уже местное;
    - конец позже `now_r` → `now_r` и `clamped=True`;
    - проверки по порядку (F19): начало раньше `EARLIEST` → «начало периода
      раньше 2000-01-01»; `end < start` → «конец периода раньше начала»;
      `0 ≤ end − start < MIN_PERIOD` (в том числе `end == start`) → «период
      короче минуты» — всё `PeriodError`;
    - `open = is_open(end, now_r)` (F21);
    - ночная смена переходит через полночь.
- Produces (`fakes.py`): `SUMMARY = {"20FAKE_001_PV": {"Minimum": 10.0, "Maximum": 15.0, "Average": 12.4, "StdDev": 1.1, "PercentGood": 100.0, "Last": 12.5, "LastDateTime": datetime(2026, 10, 1, 5, 59, 40)}, "20FAKE_002_SP": {"Minimum": None, "Maximum": None, "Average": None, "StdDev": None, "PercentGood": 0.0, "Last": None, "LastDateTime": None}}`;
  поле `FakeHistorian.summary` (копия `SUMMARY`). Разбор SQL у двойника — свой,
  не из `sql.py` (правило Д3а, `tests/fakes.py:120`, F20): регулярка
  `_SUMMARY_IN` берёт из текста SQL список столбцов между `SELECT TagName, ` и
  ` FROM AnalogSummaryHistory`, имена — из литералов `TagName IN (…)`, и
  отвечает строками `(TagName, *столбцы)` в порядке столбцов из текста, только
  для имён из `summary`. Иначе двойник проверял бы код самим собой.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_sql.py (+)
T6, T14 = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 14)

def test_summary_template_rules():
    s = summary_sql(["20FAKE_001_PV"], T6, T14)
    assert not re.search(r"\?|%s|@|;|\bLIKE\b|\bOR\b", s) and not re.search(r"\bww\w+\s+IN\b", s)
    assert "WHERE TagName IN ('20FAKE_001_PV') AND StartDateTime >= " in s and "wwCycleCount = 1" in s
    assert ("wwInterpolationType = 'STAIRSTEP'" in s) is (PIN_INTERPOLATION == "STAIRSTEP")
    assert "wwResolution" not in s and names_in(s) == ("20FAKE_001_PV",)

def test_allowed_literals():
    assert allowed_literal(lit_dt(T6).strip("'")) and allowed_literal("STAIRSTEP")
    assert not allowed_literal("16FAKE_009_PV") and not allowed_literal("Delta")

# F1: форма и SQL — из одних констант; каждое сочетание, которое может выбрать окно владельца
FMTS = ("%Y-%m-%d %H:%M:%S.000", "%Y%m%d %H:%M:%S.000")
SHAPES = [SummaryShape(f, pin, last) for f in FMTS for pin in (None, "STAIRSTEP") for last in (False, True)]

@pytest.mark.parametrize("shape", SHAPES, ids=repr)
def test_summary_form_built_from_same_constants(shape):
    s = summary_sql(["20FAKE_001_PV", "20FAKE_004_PV"], T6, T14, shape)
    form = summary_form(shape)
    assert form.fullmatch(s)
    one = lambda sh: summary_sql(["20FAKE_001_PV"], T6, T14, sh)
    tampered = [one(replace(shape, date_fmt=FMTS[1 - FMTS.index(shape.date_fmt)])),    # дата не по формату
                one(replace(shape, pin=None if shape.pin else "STAIRSTEP")),              # хвост интерполяции
                one(replace(shape, has_last=not shape.has_last)),                         # другие столбцы
                s.replace("TagName IN (", "TagName NOT IN ("), s + " OR 1=1",
                s.replace("wwCycleCount = 1", "wwCycleCount = 100"),
                s.replace(" AND wwCycleCount = 1", " AND wwRetrievalMode = 'Delta' AND wwCycleCount = 1")]
    assert not any(form.fullmatch(t) for t in tampered)

def test_summary_form_registered_from_module_constants():
    assert TEMPLATE_FORMS["summary"].pattern == summary_form().pattern
    assert template_of(summary_sql(["20FAKE_001_PV"], T6, T14)) == "summary"
    assert SUMMARY_COLUMNS == SHAPE.columns and ("Last" in SUMMARY_COLUMNS) is HAS_LAST
    assert (SHAPE.date_fmt, SHAPE.pin) == (DATE_FMT, PIN_INTERPOLATION)

# core/tests/test_gate.py (+) — T6, T14 те же, что в test_sql.py
@pytest.mark.anyio
async def test_gate_passes_summary_literals_only():
    g = HistorianGate(FakeHistorian(), allowed=WHITELIST, literal_ok=allowed_literal)
    assert (await g.run([summary_sql(["20FAKE_001_PV"], T6, T14)])).sent == ("20FAKE_001_PV",)
    assert g.stats()["refused_unlisted"] == 0                    # форма summary зарегистрирована (F1)
    with pytest.raises(GateRefused):
        await g.run(["SELECT TagName FROM AnalogHistory WHERE TagName = '20FAKE_001_PV' AND wwRetrievalMode = 'Delta'"])
    with pytest.raises(GateRefused):                              # имя вне списка и в верной форме
        await g.run([summary_sql(["16FAKE_009_PV"], T6, T14)])

# core/tests/test_sql.py (+, продолжение)
def test_date_literal():
    want = {"%Y%m%d %H:%M:%S.000": "'20261001 06:00:00.000'",
            "%Y-%m-%d %H:%M:%S.000": "'2026-10-01 06:00:00.000'"}[DATE_FMT]
    assert lit_dt(datetime(2026, 10, 1, 6, 0, 0, 999_999)) == want
    with pytest.raises(ValueError):
        lit_dt(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))

# core/tests/test_periods.py
N, OFF = datetime(2026, 10, 1, 12, 0, 45), timedelta(hours=5)

def p(now=N, **kw):
    return resolve_period(now, OFF, **kw)

def test_presets_basic():
    assert (p(preset="last_hour").start, p(preset="last_hour").end) == \
           (datetime(2026, 10, 1, 11, 0, 30), datetime(2026, 10, 1, 12, 0, 30))
    assert (p(preset="prev_shift").start, p(preset="prev_shift").end) == (datetime(2026, 9, 30, 22), datetime(2026, 10, 1, 6))
    assert (p(preset="yesterday").start, p(preset="today").start) == (datetime(2026, 9, 30), datetime(2026, 10, 1))
    assert p(preset="prev_shift").shift_assumed and not p(preset="last_hour").shift_assumed
    assert p(preset="last_hour").open and not p(preset="prev_shift").open          # F21: правило кэша

def test_open_uses_closed_after():
    assert is_open(N - timedelta(seconds=CLOSED_AFTER_S - 1), N) and not is_open(N - timedelta(seconds=CLOSED_AFTER_S), N)

def test_shift_presets_cross_midnight():
    at = lambda h, m, preset: p(now=datetime(2026, 10, 1, h, m), preset=preset)
    assert (at(5, 59, "current_shift").start, at(5, 59, "prev_shift").start, at(5, 59, "prev_shift").end) == \
           (datetime(2026, 9, 30, 22), datetime(2026, 9, 30, 14), datetime(2026, 9, 30, 22))
    assert (at(23, 10, "current_shift").start, at(6, 10, "prev_shift").start) == \
           (datetime(2026, 10, 1, 22), datetime(2026, 9, 30, 22))

def test_period_shorter_than_minute_refused():                                  # F19: end == start
    with pytest.raises(PeriodError, match="короче минуты"):
        p(now=datetime(2026, 10, 1, 6, 0, 20), preset="current_shift")
    with pytest.raises(PeriodError, match="раньше начала"):
        p(start="2026-10-01 10:00", end="2026-10-01 09:59")

def test_custom_period_offset_and_clamp():
    q = p(start="2026-10-01T01:00:00+00:00", end="2026-10-01T03:00:00+00:00")
    assert (q.start, q.end, q.clamped) == (datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 8), False)
    assert p(start="2026-10-01 10:00", end="2026-10-01 13:00").clamped is True

@pytest.mark.parametrize("kw", [{"start": "2026-10-01 10:00", "end": "2026-10-01 09:00"},
                                {"start": "1999-12-31 00:00", "end": "2000-01-02 00:00"},
                                {"start": "вчера", "end": "сегодня"}, {"preset": "last_year"}, {},
                                {"preset": "prev_shift", "start": "2026-10-01 10:00", "end": "2026-10-01 11:00"}])
def test_bad_periods(kw):
    with pytest.raises(PeriodError):
        p(**kw)
```

Импорты `test_sql.py`: `dataclasses.replace`, `datetime`, `timezone` и из
`pcbk_core.data.sql` — `DATE_FMT`, `HAS_LAST`, `PIN_INTERPOLATION`, `SHAPE`,
`SUMMARY_COLUMNS`, `SummaryShape`, `TEMPLATE_FORMS`, `allowed_literal`,
`lit_dt`, `summary_form`, `summary_sql`, `template_of`, `names_in`.
`test_periods.py`: из `pcbk_core.data.periods` — `CLOSED_AFTER_S`, `PeriodError`,
`is_open`, `resolve_period`.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_sql.py tests/test_gate.py tests/test_periods.py`. Expected: FAIL — нет `summary_sql`, `summary_form`, `periods`.

- [ ] **Step 3: Implement по интерфейсам**

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS
  (тесты Д3а на формы и ворота не меняются: форм стало на одну больше).

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных: сводка с закреплённой интерполяцией и её форма в воротах из тех же констант, формат даты по пробе, смены через полночь"`.

---

### Task 3: Охрана выхода, токены, бюджет, кэш, single-flight

**Files:**
- Create: `core/pcbk_core/egress.py`, `core/pcbk_core/data/budget.py`, `core/pcbk_core/data/cache.py`
- Modify: `core/pcbk_core/secrets.py` (`TokenTable`), `core/pcbk_core/settings.py`
  (`TOKENS_FILE="/run/secrets/core-tokens"`, `DB_PATH="/var/lib/pcbk-core/core.db"`),
  `core/pcbk_core/main.py` (`read_bdrv`; охрана — в `serve()`, F3),
  `core/pcbk_core/data/build_whitelist.py` (`main(guard=False)`, охрана — в `__main__`, F2),
  `core/tests/helpers.py` (`run_python`, `sha`, `CORE_DIR`, `free_port`, `wait_http`),
  `core/tests/conftest.py` (`listeners`)
- Test: `core/tests/test_egress.py`, `core/tests/test_budget.py`, `core/tests/test_cache.py`,
  `core/tests/test_skeleton.py` (+ `test_token_table`, F15), `core/tests/test_build_whitelist.py`
  (+ охрана построителя)

**Interfaces:**
- Consumes: `SlidingWindow` — Д3а; `BdrvConfig`; `round_now`, `CLOSED_AFTER_S`,
  `is_open` — задача 2.
- Produces:
  - `class EgressDenied(PermissionError)`;
    `install_egress_guard(allowed: Sequence[tuple[str, int | None]], allowed_nets: Sequence[str] = ("127.0.0.0/8", "::1/128")) -> None`
    — порт `None` значит «любой TCP-порт этого хоста» (адрес с экземпляром,
    Д3а):
    - `sys.addaudithook`; повторная установка → `RuntimeError`;
    - `socket.connect` (AF_INET/AF_INET6): имя хоста в кортеже разрешается
      через кэш (повтор при промахе — не чаще раза в 60 с), проверяется
      каждый адрес; вне `allowed` и `allowed_nets` или не разрешилось →
      `EgressDenied("выход запрещён: <адрес>:<порт>")`;
    - `socket.sendto`/`socket.sendmsg` на потоковом сокете → `EgressDenied`
      (TCP Fast Open мимо `connect`);
    - `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`,
      `ctypes.dlopen` → `PermissionError("запуск процессов через subprocess/os запрещён")`;
    - в докстринге модуля — «растяжка по PEP 578, не песочница» и список
      обходов из Д3б-R6;
    - `egress_guard_installed() -> bool`.
  - `install_historian_guard(cfg: BdrvConfig | None) -> int` (в `egress.py`) —
    `allowed = [] if cfg is None else [(cfg.host.split("\\")[0], cfg.port)]`,
    `install_egress_guard(allowed)` и строка журнала INFO логгера
    `pcbk_core.egress`: «охрана выхода: разрешено 1 направление» или «охрана
    выхода: разрешено 0 направлений» (петля разрешена всегда и в счёт не
    идёт); возвращает число направлений.
  - Охрана — только в точках входа процесса (F2, F3): хук не снимается, а
    тесты Д3а зовут `main()` (`test_skeleton.py:230–232`), `build_roles`
    (`test_role.py`) и `build_whitelist.main` (`test_build_whitelist.py:111–122`)
    в процессе pytest.
    - `main.read_bdrv(settings) -> tuple[BdrvConfig | None, OSError | ValueError | None]` —
      `BdrvConfig.from_env_file(settings.BDRV_ENV_FILE)` под тем же
      `except (OSError, ValueError)`, что сейчас в `build_roles`; в журнал не
      пишет. `build_roles` переходит на неё: строка ERROR и причина
      `config_error` — прежние, сигнатура `build_roles(settings)` прежняя.
    - `main.serve(settings)`: `import ctypes, pytds` (F14 — до охраны), затем
      `setup_logging()`, `install_historian_guard(read_bdrv(settings)[0])`, затем
      `build_roles(settings)` и uvicorn. Плохой `bdrv.env` → 0 направлений,
      процесс живёт, причину показывает `config_error` (Д3а, I2). Ни `main()`,
      ни `build_roles` охрану не ставят; в процессе pytest `serve()` не зовётся —
      только в подпроцессе.
    - `build_whitelist.main(argv=None, *, guard: bool = False)`: при `guard=True`
      — `install_historian_guard(cfg)` сразу после удачного чтения входных
      файлов, до `tds_query`; по умолчанию охраны нет.
      `if __name__ == "__main__": sys.exit(main(guard=True))`. `pytds` модуль
      построителя импортирует при загрузке, то есть до охраны.
  - `class TokenTable`: `__init__(self, hashes: Mapping[str, str])` (id → sha256
    hex); `from_file(path) -> TokenTable`; `empty() -> TokenTable` (ни одного
    id: всем 401, F18); `caller(bearer: str | None) -> str | None`;
    `ids: frozenset[str]`. Строка файла — `<id> <sha256 hex>`, id —
    `[a-z0-9-]{1,32}`; сверка `hmac.compare_digest` по всем записям; ошибка
    файла — `OSError`/`ValueError` без строк файла.
  - `budget.py`:
    - `@dataclass(frozen=True) class Limits: max_tags=16; max_window_days=31.0; max_points=288; max_tag_days=24.0; single_tag_days=31.0; min_window=timedelta(hours=1)`;
      `LIMITS`;
    - `cost_tag_days(n, start, end) -> float`;
    - `check_budget(n, start, end, limits=LIMITS) -> str | None` — первая
      нарушенная проверка по порядку: «за один вызов — не больше 16 тегов»;
      «период длиннее 31 суток»; «запрос стоит X тего-суток при пределе 24 (для
      одного тега — 31): сократите период или число тегов» (X — два знака
      после запятой);
    - `PER_CALLER_LIMIT = 60`.
  - `cache.py`:
    - `TTL_NOW_S = 15`, `TTL_OPEN_S = 30`, `TTL_CLOSED_S = 3600`,
      `NOW_ROUND_S = 30`, `MAX_ENTRIES = 256`;
    - `round_now`, `CLOSED_AFTER_S`, `is_open` — из `periods.py` (задача 2, F21);
      `ttl_for(tool: Literal["tag_now", "tag_period"], end: datetime | None, now: datetime) -> float` —
      у `tag_period` `TTL_OPEN_S`, если `is_open(end, now)`, иначе `TTL_CLOSED_S`;
      `cache_key(tool, tags, start, end) -> str` (sha256; порядок тегов не
      важен);
    - `class TTLCache: get(key, now) / put(key, value, ttl, now) / __len__` —
      при переполнении сначала протухшие, затем ⌈n/4⌉ с самым ранним сроком;
    - `class SingleFlight: async run(key, factory) -> tuple[T, bool]` —
      `(результат, shared)`; ошибка доходит до всех; ключ снимается по
      завершении.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_egress.py — хук снять нельзя, поэтому каждый случай — в подпроцессе
def guard_py(body, a, allowed_nets="()"):
    return run_python(f"import socket, subprocess, time\n"
                      f"from pcbk_core.egress import install_egress_guard, EgressDenied\n"
                      f"install_egress_guard([('localhost', {a})], allowed_nets={allowed_nets})\n" + body)

def test_guard_blocks_other_destinations_fast(listeners):
    a, b = listeners
    out = guard_py(f"""
socket.create_connection(('127.0.0.1', {a}), 1).close(); print('OK')
for dest in (('127.0.0.1', {b}), ('192.0.2.1', 443)):
    t = time.monotonic()
    try: socket.create_connection(dest, 5); print('OPEN')
    except EgressDenied: print('DENIED', time.monotonic() - t < 0.5)
""", a)
    assert out.split() == ["OK", "DENIED", "True", "DENIED", "True"]

def test_guard_checks_hostname_tuple_in_plain_connect(listeners):   # имя приходит в хук неразрешённым
    a, b = listeners
    out = guard_py(f"""
s = socket.socket(); s.connect(('localhost', {a})); s.close(); print('OK')
for dest in (('localhost', {b}), ('nonexistent.invalid', 443)):   # не разрешилось — тоже отказ
    s = socket.socket()
    try: s.connect(dest); print('OPEN')
    except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["OK", "DENIED", "DENIED"]

def test_guard_blocks_processes_and_fastopen(listeners):
    a, b = listeners
    out = guard_py(f"""
try: subprocess.run(['true']); print('RAN')
except PermissionError: print('BLOCKED')
s = socket.socket()
try: s.sendto(b'x', 0x20000000, ('127.0.0.1', {b})); print('SENT')   # MSG_FASTOPEN
except EgressDenied: print('DENIED')
""", a)
    assert out.split() == ["BLOCKED", "DENIED"]

# точки входа (F2, F3, F14): охрана только там; в процессе pytest хука нет
def test_pytest_process_has_no_guard():
    assert egress_guard_installed() is False     # main(), build_roles, build_whitelist.main её не ставят

@pytest.mark.parametrize("bdrv_text, code, line", [
    ("BDRV_HOST=127.0.0.1\nBDRV_PORT={closed}\nBDRV_USER=u\nBDRV_PW=<p>\n", 200, "разрешено 1 направление"),
    (None, 503, "разрешено 0 направлений"),              # нет bdrv.env → config_error, процесс жив (I2)
])
def test_serve_installs_guard_and_stays_up(tmp_path, bdrv_text, code, line):
    port, closed = free_port(), free_port()             # на closed никто не слушает
    env = {**os.environ, "CORE_PORT": str(port), "FRESH_POLL_S": "3600",
           "BDRV_ENV_FILE": write(tmp_path, bdrv_text.format(closed=closed)) if bdrv_text else str(tmp_path / "нет.env"),
           "WHITELIST_PATH": write(tmp_path, "20FAKE_001_PV\n"),
           "TOKENS_FILE": write(tmp_path, f"ops {sha('tok')}\n"), "DB_PATH": str(tmp_path / "core.db")}
    p = subprocess.Popen([sys.executable, "-m", "pcbk_core.main"], cwd=CORE_DIR, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        got, _ = wait_http(f"http://127.0.0.1:{port}/healthz/data")   # (код, тело); ждёт слушателя до 10 с
    finally:
        p.kill()             # не terminate: поток ворот досыпает login_timeout pytds, выход ждал бы его
        err = p.communicate(timeout=10)[1]
    assert got == code and f"охрана выхода: {line}" in err
    assert not re.search(r"EgressDenied|PermissionError|Traceback", err)      # стек под хуком поднялся (F14)

# core/tests/test_skeleton.py (+) — рядом с BdrvConfig, write — оттуда же (F15)
def test_token_table(tmp_path):
    t = TokenTable.from_file(write(tmp_path, f"ops {sha('tok-b')}\nstudent-01 {sha('tok-a')}\n"))
    assert (t.caller("tok-a"), t.caller("tok-b"), t.caller("x"), t.caller(None)) == ("student-01", "ops", None, None)
    assert TokenTable.empty().ids == frozenset() and TokenTable.empty().caller("tok-a") is None
    with pytest.raises(ValueError):
        TokenTable.from_file(write(tmp_path, "Student_01 abc\n"))

# core/tests/test_build_whitelist.py (+) — _run получает guard=False и передаёт его в main
def test_main_installs_guard_only_when_asked(monkeypatch, tmp_path):             # F2
    seen = []
    monkeypatch.setattr(build_whitelist, "install_historian_guard", seen.append)
    assert _run(monkeypatch, tmp_path, FakeHistorian())[0] == 0 and seen == []
    assert _run(monkeypatch, tmp_path, FakeHistorian(), guard=True)[0] == 0
    assert [(c.host, c.port) for c in seen] == [("h", 1433)]

def test_builder_entry_point_installs_guard(tmp_path):                          # F2: охрана — в __main__
    extra, env = write(tmp_path, "QFAKE_010\n"), write(tmp_path, ENV)
    out = run_python(f"""
import runpy, sys
import pcbk_core.data.historian as h
h.tds_query = lambda cfg: (lambda statements, t: [[]])       # историана нет: пустой каталог → код 1
sys.argv = ['build_whitelist', '--out', {str(tmp_path / 'w.txt')!r}, '--extra', {extra!r}, '--env-file', {env!r}]
try:
    runpy.run_module('pcbk_core.data.build_whitelist', run_name='__main__')
except SystemExit as e:
    from pcbk_core.egress import egress_guard_installed
    print(e.code, egress_guard_installed())
""")
    assert out.split() == ["1", "True"]

# core/tests/test_budget.py
T6 = datetime(2026, 10, 1, 6)
def test_budget_contract():
    assert check_budget(16, T6, T6 + timedelta(days=1)) is None
    assert "16 тегов" in check_budget(17, T6, T6 + timedelta(hours=1))
    assert check_budget(1, T6, T6 + timedelta(days=31)) is None
    assert "31 суток" in check_budget(1, T6, T6 + timedelta(days=31, seconds=1))
    assert check_budget(2, T6, T6 + timedelta(days=12)) is None
    assert "24,08" in check_budget(2, T6, T6 + timedelta(days=12, hours=1))
    assert cost_tag_days(3, T6, T6 + timedelta(minutes=10)) == pytest.approx(3 / 24)

# core/tests/test_cache.py
def test_round_now_and_ttl():
    now = datetime(2026, 10, 1, 12, 0)
    assert round_now(datetime(2026, 10, 1, 12, 0, 59)) == datetime(2026, 10, 1, 12, 0, 30)
    assert (ttl_for("tag_now", None, now), ttl_for("tag_period", now - timedelta(seconds=301), now),
            ttl_for("tag_period", now - timedelta(seconds=299), now)) == (15, 3600, 30)
    assert cache_key("tag_now", ["B", "A"], None, None) == cache_key("tag_now", ["A", "B"], None, None)

def test_ttl_cache_evicts_quarter_soonest():
    c = TTLCache(max_entries=8)
    for i in range(8):
        c.put(f"k{i}", i, ttl=10 + i, now=0.0)
    c.put("k8", 8, ttl=100, now=1.0)
    assert len(c) == 7 and c.get("k1", 1.0) is None and c.get("k2", 1.0) == 2
    assert c.get("k2", 12.5) is None

@pytest.mark.anyio
async def test_single_flight_shares_and_clears_on_error():
    sf, calls = SingleFlight(), []
    async def ok():
        calls.append(1); await anyio.sleep(0.05); return "r"
    rs = await asyncio.gather(*(sf.run("k", ok) for _ in range(10)))
    assert len(calls) == 1 and sorted(s for _, s in rs) == [False] + [True] * 9
    async def boom():
        calls.append(1); await anyio.sleep(0.01); raise HistorianError("connect", "x")
    rs = await asyncio.gather(*(sf.run("e", boom) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, HistorianError) for r in rs) and len(calls) == 2
    assert (await sf.run("e", ok))[1] is False and len(calls) == 3
```

В `helpers.py`: `CORE_DIR` (каталог `core/`); `run_python(code) -> str` —
подпроцесс `sys.executable -c`, `cwd=CORE_DIR`, `check=True`, stdout;
`sha(text) -> str` — sha256 hex; `free_port() -> int` (свободный порт
127.0.0.1); `wait_http(url, timeout=10) -> tuple[int, str]` — опрос `urllib`,
пока слушатель не ответит (код и тело, в том числе 503). `write` — Д3а. В
`conftest.py`: фикстура `listeners` (два слушающих сокета на 127.0.0.1).

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_egress.py tests/test_budget.py tests/test_cache.py tests/test_skeleton.py tests/test_build_whitelist.py`. Expected: FAIL.

- [ ] **Step 3: Implement по интерфейсам.** Семейства, кроме AF_INET и AF_INET6
  (сокеты-пары asyncio — AF_UNIX), хук пропускает.

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS,
  в том числе все тесты Д3а, которые зовут `main()`, `build_roles` и
  `build_whitelist.main` в процессе, и `test_pytest_process_has_no_guard` в
  любом порядке прогона.

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Серверный слой: охрана выхода и запуска процессов в точках входа, токены хешами, бюджет 16/31/24, кэш и single-flight"`.

---

### Task 4: Разбор имён, поиск и журнал событий

**Files:**
- Modify: `core/pcbk_core/data/catalog.py`, `core/pcbk_core/data/__init__.py`
  (`DataRole`: события, токены, здоровье, `lifespan`), `core/tests/helpers.py`
  (`SETTINGS` во временном каталоге, `TOKEN`, `TEST_TOKENS`, `EVENT`,
  `make_role(db_dir=, tokens=)`)
- Create: `core/pcbk_core/data/events.py`
- Test: `core/tests/test_catalog.py` (+), `core/tests/test_events.py`, `core/tests/test_role.py` (+,
  и две детали в `test_page_texts_fit_80_chars`)

**Interfaces:**
- Consumes: `Catalog`, `DataRole`, `GateResult.sent`, `CONFIG_ERRORS`,
  `ERROR_TEXTS` — Д3а; `TokenTable`, `sha`, `Settings.DB_PATH`,
  `Settings.TOKENS_FILE` — задача 3.
- Produces (`catalog.py`):
  - `norm(text) -> str` — нижний регистр, «ё» → «е», пробелы схлопнуты
  - `@dataclass(frozen=True) class Lookup: status: Literal["ok", "unlisted", "unknown"]; info: TagInfo | None; similar: tuple[str, ...]`:
    `info` есть **только** при `ok`; у `unknown` `similar` — до 5 имён
    белого списка с той же основой (имя без последнего `_ХВОСТА`) или
    содержащих введённое
  - `Catalog.lookup(raw: str) -> Lookup` — `strip()`, регистр не важен;
    каноническое имя берётся из каталога
  - `@dataclass(frozen=True) class SearchResult: matches: tuple[TagInfo, ...]; similar: bool; total: int`;
    `Catalog.search(query: str, limit: int = 10) -> SearchResult`:
    - только теги белого списка; все слова запроса (после `norm`) должны
      найтись в `norm(имя + " " + описание)`;
    - порядок: точное имя, имя с начала, затем по имени;
    - ничего не нашлось → `similar=True`: теги хотя бы с одним словом, по
      убыванию числа слов; если запрос похож на имя (`SAFE_NAME` и есть `_`),
      — ещё теги с той же основой.
- Produces (`events.py`):
  - `Outcome = Literal["ok", "partial", "refused", "budget", "rate", "busy", "timeout", "unavailable", "error"]`;
    `Channel = Literal["mcp", "http", "system"]`; `CacheState = Literal["hit", "miss", "shared", "none"]`
  - `@dataclass(frozen=True) class CallEvent: ts: datetime; caller: str; agent: str | None; channel: Channel; tool: str; requested: tuple[str, ...]; tags: tuple[str, ...]; sent: tuple[str, ...]; start: datetime | None; end: datetime | None; outcome: Outcome; summary: str; cost: float; cache: CacheState; rows: int; duration_ms: int`:
    `requested` — сырой ввод, обрезается до 16 строк по 128 знаков; `tags` —
    канонические имена после разбора со статусами `ok`, `no_value`,
    `no_data` (их слой событий мнемосхем М4 ставит «у своего тега»; есть и
    у попадания в кэш); `sent` — имена, переданные драйверу (ворота
    возвращают их и при отказе входа, когда SQL в историан не попал, F12)
  - `OUTCOME_BY_CODE: dict[str, Outcome]` — одна таблица для инструментов
    (задача 5) и служебных событий (F11): `connect`, `auth` → `unavailable`;
    `timeout` → `timeout`; `query`, `unlisted` → `error`; `busy` → `busy`;
    `rate` → `rate`; `no_rows`, `no_tags`, `catalog` → `unavailable`.
    `outcome_for(code: str) -> Outcome` — по таблице, неизвестный код →
    `error`. Сырой код идёт в `summary` события.
  - `EVENTS_CHECK_S = 30.0` — период фоновой проверки записи.
  - `class EventLog` — конструктор без ввода-вывода (F4, образец —
    `LazyJournal` сторожа, `watchdog/pcbk_watchdog/main.py:231`):
    - `__init__(self, path: str)` — только запоминает путь; файл не
      открывается. SQLite; соединение открывается на каждую операцию, как
      журнал сторожа Д1: из потоков TestClient и uvicorn нет ошибки
      `check_same_thread`;
    - `open(self) -> bool` — файл, WAL и таблица `agent_events` (`requested`,
      `tags`, `sent` — JSON); идемпотентна; сбой не бросает: `writable_ok =
      False`, `last_error = "<класс>: <текст>"`, `False`. Роль зовёт её в начале
      `lifespan` (F23: `core.db` есть сразу после старта);
    - `write(self, e: CallEvent) -> None` — схема при первой операции
      (`_ensure`), вставка — внутренним `_insert`; при ошибке
      `consecutive_failures += 1`, `last_error = "<класс>: <текст>"`,
      исключение дальше; удача сбрасывает счётчик;
    - `writable(self) -> bool` — на новом соединении, как у журнала сторожа
      Д1: `BEGIN IMMEDIATE`, `INSERT`, `ROLLBACK`; сбой → `last_error`, `False`.
      Это ввод-вывод до 5 с ожидания блокировки — в цикле событий его не
      зовут (F9);
    - `check(self) -> None` — `writable_ok = writable()`; роль зовёт её в фоне
      через `asyncio.to_thread`;
    - `writable_ok: bool | None` — итог последней проверки, `None` до первой;
      `consecutive_failures: int`; `last_error: str | None`;
    - `recent(self, limit: int = 50, channel: Channel | None = None) -> list[CallEvent]` —
      новые сверху; `channel` отсеивает, например, события `system`;
    - `count(self, channel: Channel | None = None) -> int` — `SELECT COUNT(*)`:
      тесты на работающем приложении считают им, а не длиной `recent()`
      (опрос свежести пишет свои события между замерами).
- `DataRole` (правки):
  - конструктор получает `tokens: TokenTable | None = None` и
    `tokens_error: str | None = None`; поле `tokens` — всегда `TokenTable`:
    `TokenTable.empty()`, если не передан (F18); поле
    `events = EventLog(settings.DB_PATH)` — без ввода-вывода (F4);
  - `TOKENS_ERROR = "токены не читаются — все вызовы получают 401"` — рядом
    с `CONFIG_ERRORS` (44 знака); это не `config_error`: историан не под
    угрозой, фоновые циклы идут;
  - `refresh_catalog` и `poll_freshness` пишут событие: `caller="system"`,
    `channel="system"`, `tool="catalog"`/`"freshness"`, `sent` из
    `GateResult`. У каталога `summary` начинается с «без имён»; исход —
    `ok` или `outcome_for(код)` (F11): у каталога код — тот, что идёт в
    `_catalog_failed` (`busy` там уже `timeout`), у свежести — код ошибки
    опроса или `freshness.error` после `observe`, пропуск опроса воротами —
    `busy`. Ошибка записи события опрос не ломает;
  - `async def check_events(self)` — `await asyncio.to_thread(self.events.check)`;
  - `_lifespan`: первым делом `await asyncio.to_thread(self.events.open)` —
    и при `config_error` тоже (F23); вне `config_error` к циклам каталога и
    свежести добавляется `_events_loop`: `check_events()` сразу и дальше раз
    в `EVENTS_CHECK_S` (F9). Задача 6 обернёт всё это в
    `session_manager.run()`;
  - `health()` — без ввода-вывода, по порядку: `config_error` → пустой белый
    список → `tokens_error` → `events.consecutive_failures > 0` или
    `events.writable_ok is False` → `(False, "журнал событий не пишется: " + (events.last_error or "нет записи")[:53])`
    (27 + 53 ≤ 80, F10) → каталог (как в Д3а).

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_catalog.py (+)
@pytest.fixture
def cat():
    return Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST)

def test_lookup_statuses_and_no_metadata_outside_list(cat):
    assert (cat.lookup("  20fake_001_pv ").status, cat.lookup("  20fake_001_pv ").info.name) == ("ok", "20FAKE_001_PV")
    unl = cat.lookup("20FAKE_005_LMN")
    assert (unl.status, unl.info) == ("unlisted", None)
    miss = cat.lookup("20FAKE_001")
    assert miss.status == "unknown" and miss.info is None and "20FAKE_001_PV" in miss.similar

def test_search_words_yo_all_words_and_similar(cat):
    assert [t.name for t in cat.search("РАСХОД массы").matches] == ["20FAKE_001_PV", "20FAKE_002_SP"]
    assert [t.name for t in cat.search("емкости").matches] == ["20FAKE_003_PV"]    # в описании — «ёмкости»
    r = cat.search("расход клапан")
    assert r.similar and {"20FAKE_001_PV", "25FAKE_007_CLS"} <= {t.name for t in r.matches}
    assert cat.search("вне участка").matches == () and cat.search("системный").matches == ()

# core/tests/test_events.py
from helpers import EVENT as E            # пример события — в helpers: его берёт и test_role.py (F16)

def test_event_roundtrip_newest_first(tmp_path):
    log = EventLog(str(tmp_path / "core.db"))
    assert not (tmp_path / "core.db").exists()                  # F4: конструктор без ввода-вывода
    log.write(E); log.write(replace(E, outcome="refused", sent=()))
    assert [e.outcome for e in log.recent()] == ["refused", "partial"] and log.recent()[1] == E
    assert log.writable() is True

def test_readonly_db_is_not_writable(tmp_path):
    d = tmp_path / "ro"; d.mkdir()
    log = EventLog(str(d / "core.db"))
    assert log.open() is True                                   # файл есть — теперь отнять права
    (d / "core.db").chmod(0o400); d.chmod(0o500)
    assert log.writable() is False and log.last_error

def test_open_failure_is_recorded_not_raised(tmp_path):         # F4, F23: том от root — причина, не падение
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o500)
    log = EventLog(str(d / "core.db"))
    assert log.open() is False and log.writable_ok is False and log.last_error

def test_outcome_table_covers_all_codes():                      # F11: только значения Outcome
    codes = [*get_args(ErrorCode), "busy", "rate", "unlisted", *ERROR_TEXTS]
    assert {outcome_for(c) for c in codes} <= set(get_args(Outcome)) and outcome_for("что-то") == "error"
    assert (outcome_for("connect"), outcome_for("no_rows"), outcome_for("busy")) == ("unavailable", "unavailable", "busy")

# core/tests/test_role.py (+) — импорты: sqlite3; from helpers import EVENT as E, TEST_TOKENS, TOKEN (F16)
async def test_freshness_poll_writes_sent():                                    # Review Focus 2
    role, _ = make_role()
    await role.refresh_catalog()
    await role.poll_freshness()
    fresh, catalog = role.events.recent()[:2]
    assert (fresh.caller, fresh.channel, fresh.tool, fresh.outcome) == ("system", "system", "freshness", "ok")
    assert set(fresh.sent) == {"20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP"} <= WHITELIST
    assert (catalog.tool, catalog.sent) == ("catalog", ()) and catalog.summary.startswith("без имён")

async def test_system_event_outcome_is_outcome_not_raw_code():                  # F11
    role, fake = make_role()
    fake.fail = HistorianError("connect", "обрыв")
    await role.refresh_catalog()
    e = role.events.recent()[0]
    assert (e.tool, e.outcome) == ("catalog", "unavailable") and "connect" in e.summary

def test_events_disk_full_turns_health_red(monkeypatch):                       # Review Focus 4
    role, _ = make_role()
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(role.events, "_insert", full)
    with pytest.raises(sqlite3.OperationalError):
        role.events.write(E)
    ok, detail = role.health()
    assert ok is False and detail.startswith("журнал событий не пишется") and "disk is full" in detail

async def test_events_check_off_loop_and_health_without_io(tmp_path, monkeypatch):   # F9, Review Focus 4
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o500)
    role, _ = make_role(db_dir=d)
    await role.check_events()                                   # в фоне: asyncio.to_thread
    monkeypatch.setattr(role.events, "writable", lambda: pytest.fail("health() не делает ввода-вывода"))
    ok, detail = role.health()
    assert ok is False and detail.startswith("журнал событий не пишется: ") and len(detail) <= 80

def test_constructor_without_io_and_empty_tokens(tmp_path):                     # F4, F18
    role = DataRole(replace(SETTINGS, DB_PATH=str(tmp_path / "нет" / "core.db")), FakeHistorian(), WHITELIST)
    assert role.tokens.ids == frozenset() and role.tokens.caller("x") is None
    assert role.health() == (True, "каталог ещё не загружен") and not (tmp_path / "нет").exists()
    assert make_role(tokens=TEST_TOKENS)[0].tokens.caller(TOKEN) == "student-01"
```

В `test_page_texts_fit_80_chars` Д3а (`test_role.py`) — ещё две детали (F10):
`TOKENS_ERROR` и деталь роли с `events.consecutive_failures = 1`,
`events.last_error = "x" * 200`; предел 80 — прежний.

`_insert` — внутренний метод `EventLog`, которым пользуется `write`; тест
подменяет его, `write` остаётся настоящим.

Помощники (`helpers.py`, F4, F16, F17):
- `TOKEN = "test-token-student-01"` — не 48 шестнадцатеричных знаков, шаблон
  проверки на секреты Д2 его не ловит; `TEST_TOKENS = TokenTable({"student-01": sha(TOKEN)})`;
- `EVENT` — пример `CallEvent` (прежний `E` этого плана: `caller="student-01"`,
  `channel="http"`, `tool="tag_now"`, `requested=("20FAKE_001_PV", "X' OR 1=1--")`,
  `tags=sent=("20FAKE_001_PV",)`, `outcome="partial"`, `cache="miss"`, `rows=1`,
  `duration_ms=12`, `ts=datetime(2026, 10, 1, 7, tzinfo=timezone.utc)`, прочее — пусто/ноль);
- `SETTINGS = Settings(DB_PATH=…, TOKENS_FILE=…)` — пути в каталоге сессии
  `tempfile.mkdtemp(prefix="pcbk-core-tests-")` (удаляется через `atexit`); в
  нём же файл `core-tokens` со строкой `student-01 <sha(TOKEN)>`. Тесты Д3а,
  которые строят `DataRole` и `build_roles` из `SETTINGS`
  (`test_role.py:244–258, 291–297, 327–345`), не трогают
  `/var/lib/pcbk-core` и `/run/secrets`;
- `make_role(..., *, db_dir=None, tokens=TEST_TOKENS)` — `DB_PATH` в `db_dir`
  (по умолчанию — новый временный каталог внутри каталога сессии).

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_catalog.py tests/test_events.py tests/test_role.py`. Expected: FAIL.

- [ ] **Step 3: Implement по интерфейсам**

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS
  (все тесты `build_roles` и `DataRole` Д3а зелёны: конструктор файлов не
  открывает, пути — во временном каталоге).

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных: разбор имён и поиск по белому списку, журнал событий с requested и sent, служебные опросы в журнале"`.

---

### Task 5: Инструменты и HTTP

**Files:**
- Create: `core/pcbk_core/data/service.py`, `core/pcbk_core/data/http_api.py`
- Modify: `core/pcbk_core/data/__init__.py` (`DataRole.service`, `router`,
  `install` — обработчик 422 и ограничитель тела), `core/pcbk_core/data/gate.py` (`clear_pause`),
  `core/pcbk_core/main.py` (`build_roles`: `TokenTable` под `try`, F4), `core/tests/helpers.py`
  (`make_service`, `make_app`, `sent_of`; `make_role(config_error=)`). `TOKEN` уже есть —
  задача 4 (F17). `core/tests/conftest.py` не меняется (F13).
- Test: `core/tests/test_service.py`, `core/tests/test_http.py`, `core/tests/test_role.py` (+ токены в `build_roles`)

**Interfaces:**
- Consumes: всё из задач 2–4 и Д3а (в том числе `OUTCOME_BY_CODE`, `TOKENS_ERROR`,
  `TOKEN`, `Period.open`, `is_open`); `AVG_KIND`, `STD_KIND` — задача 1.
- Produces (`service.py`):
  - `AVG_KIND: Literal["step", "linear", "arith", "unverified"]`,
    `STD_KIND: Literal["step", "arith", "unverified"]`
  - `KIND_TEXTS = {"step": "взвешено по времени (ступенчатая интерполяция)", "linear": "взвешено по времени (линейная интерполяция)", "arith": "арифметическое по сырым значениям", "unverified": "вид не подтверждён сверкой"}`
  - постоянные тексты:
    - `SOURCE_LIVE = "текущее значение из таблицы Live историана"`;
    - `SOURCE_SUMMARY = "сводка историана по сырым данным: AnalogSummaryHistory, одна корзина на весь период"`;
    - `SHIFT_NOTE = "границы смен 06:00, 14:00, 22:00 — допущение, технологом не подтверждено"`;
    - `OPEN_NOTE = "период ещё идёт — числа изменятся"`;
    - `CLAMP_NOTE = "конец периода ограничен текущим временем историана"`;
    - `NO_LAST_NOTE = "последнее значение за период пока не считается"`;
    - `TRUNCATED_NOTE = "описания в историане обрезаны на 50 символах"`;
    - `SIMILAR_NOTE = "точного совпадения нет — вот похожие, выберите вместе со студентом"`;
    - `ITEM_TEXTS = {"no_value": "нет текущего значения", "no_data": "за период данных нет", "unlisted": "тег не входит в белый список стенда", "unknown": "такого тега нет в каталоге историана", "discrete": "дискретный тег: сводка не считается, наработка — в следующих слайсах"}`;
    - `MESSAGES = {"rate": "слишком часто: не больше 60 вызовов за 5 минут — подождите минуту", "global_rate": "общий предел запросов к историану исчерпан — повторите через несколько минут", "busy": "историан занят другими запросами — повторите через минуту", "timeout": "историан не ответил за 15 с — сократите период или число тегов", "catalog": "каталог тегов ещё не загружен", "connect": "нет связи с историаном", "auth": "историан отклонил учётные данные — сообщите преподавателю", "query": "историан вернул ошибку — вызов записан в журнал", "none": "ни один тег нельзя запросить", "args": "неверные аргументы: "}`;
    - `STATE_LABELS = {"_RUN": ("работает", "стоит"), "_OPN": ("открыт", "не открыт"), "_CLS": ("закрыт", "не закрыт"), "_ON": ("включён", "не включён"), "_OFF": ("выключен", "не выключен"), "_STOP": ("остановлен", "не остановлен"), "_FLT": ("неисправность", "исправен"), "_ALM": ("авария", "нет аварии")}` —
      подпись для 1 и для 0, в ответе — с пометкой «по суффиксу».
  - `class DataService`:
    - `__init__(self, role: DataRole, *, cache: TTLCache | None = None, flight: SingleFlight | None = None, per_caller: SlidingWindow | None = None)` —
      ворота, каталог, часы, события и `monotonic` берутся из роли; «сейчас
      историана» — последние часы плюс прошедшее по `monotonic`, при часах
      старше 300 с — сначала `[clock_sql()]`;
    - `async def catalog_search(self, caller: str, channel: Channel, query: str, limit: int = 10) -> dict[str, Any]`;
    - `async def tag_now(self, caller: str, channel: Channel, tags: list[str]) -> dict[str, Any]`;
    - `async def tag_period(self, caller: str, channel: Channel, tags: list[str], period: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]`;
    - `def refuse_args(self, caller: str, channel: Channel, tool: str, requested: Sequence[str], reason: str) -> dict[str, Any]` —
      ответ `refused` с `MESSAGES["args"] + reason` и событие;
    - `def config_unavailable(self, caller: str, channel: Channel, tool: str, requested: Sequence[str]) -> dict[str, Any]` —
      ответ `status: unavailable`, `message` = `role.config_error` (причина
      Д3а, не длиннее 80), `notes: []`, `cache: "none"`; событие
      `outcome="unavailable"`, `sent=()`, `summary` — «ошибка настройки»;
      строка журнала. Историан не трогает (F5).
  - Порядок в инструментах:
    0. **ошибка настройки** (F5): `role.config_error` → `config_unavailable(...)`.
       Это первая строка каждого публичного метода `DataService` —
       `catalog_search`, `tag_now`, `tag_period` и `refuse_args`: ни частоты,
       ни каталога, ни ворот. Иначе при сломанном белом списке и целом
       `bdrv.env` настоящий `tds_query` получит `clock_sql` по полосе людей
       (`gate.py:212`) — входы общей с Dify учётки;
    1. частота вызывающего;
    2. проверка аргументов: больше 16 тегов, строка длиннее 128, неизвестный
       `period` → `refuse_args`;
    3. каталог загружен;
    4. `lookup`: повторы схлопываются, порядок `items` — как во вводе;
    5. период и бюджет (у `tag_period`);
    6. кэш → single-flight → ворота;
    7. сборка ответа → кэш → событие → строка журнала.

    «Сейчас историана» — `role.clock.local` плюс прошедшее по `monotonic` с
    `role.clock_mono`; опрос свежести обновляет их раз в 30 с, отдельный
    `[clock_sql()]` идёт, только если часы старше 300 с. В вызове
    `tag_period` «сейчас» округляется один раз (`now_r = round_now(...)`), и
    это же `now_r` идёт в `resolve_period` и в `ttl_for`: заметка
    `OPEN_NOTE` (по `period.open`) и срок кэша считаются одним `is_open` (F21).

    **Любой отказ до ворот не шлёт SQL.** Событие пишется на каждый вызов:
    - `requested` — сырой ввод;
    - `tags` — канонические имена со статусами `ok`/`no_value`/`no_data`, в
      том числе при попадании в кэш;
    - `sent` — из `GateResult` или `HistorianError.sent`: имена, переданные
      драйверу, — и при сроке или сбое после передачи, и при отказе входа
      (F12); у попадания в кэш, у ожидающего single-flight и у
      `config_unavailable` — пусто;
    - `outcome` — `ok`/`partial`/`refused`/`budget` по ответу, коды ошибок —
      через `outcome_for` (`OUTCOME_BY_CODE` задачи 4, F11).

    Неожиданное исключение в инструменте → ответ `status: error`, событие
    `outcome=error` и строка журнала с классом ошибки. Ошибка записи события
    ответ не ломает. Строка журнала —
    `tool=… caller=… channel=… tags=N period=…..… cost=0.333 cache=miss rows=N ms=N outcome=ok`,
    без значений.
  - Контракт ответов (JSON: только str, float, int, bool, null, списки,
    словари):
    - общие ключи: `tool`, `status` (`ok` | `partial` | `refused` | `budget` |
      `rate` | `busy` | `timeout` | `unavailable` | `error`), `message` (кроме
      `ok`/`partial`), `notes`, `cache`;
    - `tag_now`: `asof`, `tz` («UTC+05:00»), `source = SOURCE_LIVE`,
      `items[]`:
      - `tag`, `status` (`ok` | `no_value` | `unlisted` | `unknown`), `text`
        (кроме `ok`), `similar` (у `unknown`);
      - только у `ok` и `no_value`: `description`, `unit`, `kind`, `value`,
        `time`, `age_s` (целое, не меньше 0), `quality` («хорошее» при 0,
        иначе «недостоверное (код N)»), `state_label` (у дискретных);
      - `item_notes`: метка впереди больше чем на 60 с → «метка тега впереди
        часов историана на N с»; возраст больше 600 с → «значение не
        менялось N мин: это и ровный процесс, и возможное залипание —
        смотрите качество»;
      - строка `Live` с `Value IS NULL` → `no_value` с `time` и `quality`;
    - `tag_period`: `period` {`start`, `end`, `tz`, `preset`,
      `shift_grid_assumed`, `clamped`, `open`}, `source = SOURCE_SUMMARY`,
      `avg_kind` {`code`, `text`}, `std_kind` {`code`, `text`},
      `cost_tag_days`, `items[]`:
      - `tag`, `status` (`ok` | `no_data` | `discrete` | `unlisted` |
        `unknown`), `text`;
      - только у `ok`: `description`, `unit`, `avg`, `min`, `max`,
        `range = max − min`, `std`, `percent_good`, `last` и `last_time`
        (при `HAS_LAST`), `quality_note` («доля достоверных N % — ниже 99 %»);
      - строки нет, либо `NULL` в `Average`/`Minimum`/`Maximum`, либо
        `PercentGood == 0` → `no_data`;
      - `period.open` — `Period.open` задачи 2 (F21);
      - `notes`: `SHIFT_NOTE` (готовая смена), `OPEN_NOTE` (только при
        `period.open`), `CLAMP_NOTE` (при `clamped`), `NO_LAST_NOTE` (без `HAS_LAST`);
    - `catalog_search`: `query`, `matches[]` {`tag`, `description`, `unit`,
      `kind`, `min_eu`, `max_eu`, `live`, `description_truncated`},
      `similar`, `total`; `notes`: `SIMILAR_NOTE`, `TRUNCATED_NOTE`;
    - ошибки → статус (та же таблица `OUTCOME_BY_CODE`, что у служебных
      событий, F11):
      - `config_error` → `unavailable` с `message` = причина — до всех шагов
        (F5), по HTTP — 503;
      - `HistorianError`: `connect` → `unavailable` с `MESSAGES["connect"]`,
        `auth` → `unavailable` с `MESSAGES["auth"]`, `timeout` → `timeout`,
        `query` → `error`;
      - `GateRefused`: `busy` → `busy`, `rate` → `rate` с
        `MESSAGES["global_rate"]`, `unlisted` → `error`;
      - каталог не загружен → `unavailable` с `MESSAGES["catalog"]`.
- Produces (`http_api.py`):
  - `Tag = Annotated[str, StringConstraints(max_length=128)]`;
    `CatalogSearchArgs(query: str (1..200), limit: int = 10 (1..50))`,
    `TagNowArgs(tags: list[Tag] (1..16))`,
    `TagPeriodArgs(tags: list[Tag] (1..16), period: Literal[<PRESETS>] | None, start: str | None, end: str | None)`
  - `data_router(role: DataRole) -> APIRouter`:
    - `POST /api/data/catalog_search`, `/api/data/tag_now`, `/api/data/tag_period`;
      `Authorization: Bearer` → вызывающий;
    - порядок обработчика: токен → `config_error` → инструмент;
    - нет или неверный токен → `401 {"detail": "нужен токен"}` и строка
      журнала `outcome=unauthorized channel=http path=…` (без токена), без
      события;
    - `role.config_error` → `503`, тело — `service.config_unavailable(...)`
      (F5): первый шаг после токена, ворота не зовутся;
    - ответ инструмента — `200`.
  - `DataRole.install(app)` — единственное место крючков уровня приложения
    (протокол `Role` из Д3а; `app.py` не меняется):
    - `app.add_exception_handler(RequestValidationError, …)`: для путей
      `/api/data/*` сначала проверяется токен — нет или неверный → `401` и
      строка журнала, без события (битый JSON без токена не пишет мусор в
      журнал). С токеном и `config_error` → `503` с телом
      `service.config_unavailable(...)` (F5). С токеном → `422` с телом
      `service.refuse_args(...)` (русская причина: поля и короткий текст) и
      событием. Прочие пути — обработчик FastAPI по умолчанию;
    - `app.add_middleware(BodyLimit, max_bytes=65536)` — чистое ASGI-звено,
      **до маршрутизации**. Сначала оно сверяет `Content-Length`; затем
      читает тело целиком, но не больше `max_bytes + 1` байт, и только потом
      зовёт приложение, отдавая ему буфер заново. Так 413 выходит и на теле
      без длины, и на любом пути: исключение из `receive` FastAPI превратил
      бы в 400, а транспорт MCP — в 500. Образец —
      `RequestBodyLimitMiddleware` из `mcp`; его можно взять самого, обернув
      строкой журнала. Больше предела → `413` и строка журнала
      `outcome=too_large path=…`; стоит на всём приложении, в том числе на
      `/mcp`.
  - `DataRole.router()` — роутер Д3а плюс `data_router`.
  - `main.build_roles` (F4): `TokenTable.from_file(settings.TOKENS_FILE)` под
    `except (OSError, ValueError)`; сбой → строка ERROR «токены <путь>:
    <_why>» (без строк файла), `tokens=TokenTable.empty()` (всем 401) и
    `tokens_error=TOKENS_ERROR`. Это не `config_error`: фоновые циклы идут,
    процесс не падает, на странице — причина (Д3а, I2).

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_service.py
pytestmark = pytest.mark.anyio

@pytest.fixture
async def svc(tmp_path):
    s, fake = make_service(tmp_path)          # роль с загруженным каталогом; fake.calls очищены
    return s, fake

async def test_tag_now_answer_says_what_and_when(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20fake_001_pv"])
    it = r["items"][0]
    assert (r["status"], r["tz"], r["source"], r["cache"]) == ("ok", "UTC+05:00", SOURCE_LIVE, "miss")
    assert (it["tag"], it["value"], it["age_s"], it["quality"], it["time"]) == \
           ("20FAKE_001_PV", 12.5, 42, "хорошее", "2026-10-01T11:59:18+05:00")

async def test_events_requested_vs_sent(svc):                                  # Review Focus 2
    s, fake = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV", "16FAKE_009_PV", "20FAKE_001_PV' OR 1=1--",
                                           "20FAKE_005_LMN", "20FAKE_001_PV\n"])
    e = s.role.events.recent()[0]
    assert "16FAKE_009_PV" in e.requested and "20FAKE_001_PV' OR 1=1--" in e.requested
    assert e.sent == ("20FAKE_001_PV",) and sent_of(fake) == ["20FAKE_001_PV"]

async def test_tag_now_statuses_distinct_and_no_foreign_metadata(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20FAKE_003_PV", "16FAKE_009_PV", "20FAKE_404_PV"])
    assert r["status"] == "partial" and [i["status"] for i in r["items"]] == ["no_value", "unlisted", "unknown"]
    assert not {"description", "unit", "kind", "min_eu", "max_eu"} & set(r["items"][1])

async def test_tag_now_null_value_is_no_value(svc):                             # Review Focus 1
    s, fake = svc
    fake.live["20FAKE_002_SP"] = (datetime(2026, 10, 1, 11, 0), None, 0)
    it = (await s.tag_now("student-01", "http", ["20FAKE_002_SP"]))["items"][0]
    assert (it["status"], it["text"], it["time"]) == ("no_value", "нет текущего значения", "2026-10-01T11:00:00+05:00")

async def test_tag_now_future_timestamp_clamped(svc):
    s, fake = svc
    fake.live["20FAKE_001_PV"] = (datetime(2026, 10, 1, 12, 0, 31), 12.5, 0)
    fake.live["20FAKE_004_PV"] = (datetime(2026, 10, 1, 12, 1, 30), 3.2, 0)
    a, b = (await s.tag_now("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV"]))["items"]
    assert (a["age_s"], a.get("item_notes", [])) == (0, [])
    assert b["age_s"] == 0 and "впереди часов историана на 90 с" in b["item_notes"][0]

async def test_quality_and_discrete_labels(svc):
    s, _ = svc
    a, b = (await s.tag_now("student-01", "http", ["20FAKE_004_PV", "25FAKE_007_CLS"]))["items"]
    assert a["quality"] == "недостоверное (код 64)" and b["state_label"].startswith("закрыт")

async def test_tag_period_answer_says_what_was_computed(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    it = r["items"][0]
    assert r["status"] == "ok" and it["range"] == pytest.approx(5.0) == it["max"] - it["min"]
    assert (r["source"], r["avg_kind"]["code"], r["std_kind"]["code"]) == (SOURCE_SUMMARY, AVG_KIND, STD_KIND)
    assert r["period"]["start"] == "2026-09-30T22:00:00+05:00" and r["period"]["shift_grid_assumed"] is True
    assert SHIFT_NOTE in r["notes"] and ("last" in it) is HAS_LAST
    assert r["period"]["open"] is False and OPEN_NOTE not in r["notes"]

async def test_open_note_follows_period_open(svc):                             # F21
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="last_hour")
    assert r["period"]["open"] is True and OPEN_NOTE in r["notes"]

async def test_config_error_never_enters_historian(tmp_path):                  # F5, Review Focus 6
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    s = role.service
    outs = [await s.catalog_search("student-01", "http", "расход"),
            await s.tag_now("student-01", "http", ["20FAKE_001_PV"]),
            await s.tag_period("student-01", "mcp", ["20FAKE_001_PV"], period="prev_shift"),
            s.refuse_args("student-01", "mcp", "tag_now", ["x"], "tags: нужен список")]
    assert {(o["status"], o["message"]) for o in outs} == {("unavailable", "белый список не читается")}
    assert fake.calls == [] and role.gate.stats()["sent_names_total"] == 0      # ноль вызовов QueryFn
    events = role.events.recent()
    assert [e.outcome for e in events] == ["unavailable"] * 4 and {e.sent for e in events} == {()}

async def test_tag_period_null_row_is_no_data(svc):                             # Review Focus 1
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_002_SP"], period="prev_shift")
    assert (r["items"][0]["status"], r["items"][0]["text"]) == ("no_data", "за период данных нет")

async def test_tag_period_four_distinct_refusals(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV", "16FAKE_009_PV",
                                                  "20FAKE_404_PV", "25FAKE_007_CLS"], period="prev_shift")
    assert [i["status"] for i in r["items"]] == ["ok", "no_data", "unlisted", "unknown", "discrete"]
    assert len({i["text"] for i in r["items"][1:]}) == 4

async def test_refusals_send_no_sql_and_are_events(svc):
    s, fake = svc
    outs = [(await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"],
                                start="2026-09-01 00:00", end="2026-09-14 00:00"))["status"],
            (await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift",
                                start="2026-10-01 10:00", end="2026-10-01 11:00"))["status"],
            (await s.tag_now("student-01", "http", ["16FAKE_009_PV"]))["status"],
            (await s.tag_now("student-01", "http", [f"20FAKE_{i:03d}_PV" for i in range(17)]))["status"]]
    assert outs == ["budget", "refused", "refused", "refused"] and fake.calls == []
    assert [e.outcome for e in s.role.events.recent()[:4]] == ["refused", "refused", "refused", "budget"]

async def test_rate_and_single_flight(svc):
    s, fake = svc
    fake.delay_s = 0.1
    rs = await asyncio.gather(*(s.tag_period(f"student-{n:02d}", "http", ["20FAKE_001_PV"], period="prev_shift")
                                for n in range(1, 11)))
    assert sum("AnalogSummaryHistory" in q for c in fake.calls for q in c) == 1
    assert sorted(r["cache"] for r in rs) == ["miss"] + ["shared"] * 9
    for _ in range(59):
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "rate"

async def test_historian_errors_are_named(svc):
    s, fake = svc
    for code, want in (("connect", "нет связи с историаном"), ("auth", MESSAGES["auth"])):
        fake.fail = HistorianError(code, "x")
        r = await s.tag_now(f"student-{code}", "http", ["20FAKE_004_PV"])
        assert (r["status"], r["message"]) == ("unavailable", want)
        s.role.gate.clear_pause()          # сброс паузы и защёлки между случаями

async def test_timeout_event_keeps_sent(svc):                                   # Review Focus 2
    s, fake = svc
    fake.fail = HistorianError("timeout", "долго")      # ворота добавят sent — SQL уже ушёл
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    e = s.role.events.recent()[0]
    assert (r["status"], e.outcome, e.sent) == ("timeout", "timeout", ("20FAKE_001_PV",))

async def test_cache_hit_event_has_tags(svc):
    s, _ = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    await s.tag_now("student-02", "http", ["20fake_001_pv"])
    e = s.role.events.recent()[0]
    assert (e.cache, e.tags, e.sent) == ("hit", ("20FAKE_001_PV",), ())

async def test_unexpected_error_is_error_event(svc):
    s, fake = svc
    fake.fail = RuntimeError("boom")
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert r["status"] == "error" and s.role.events.recent()[0].outcome == "error"

async def test_tool_answers_when_events_fail(svc, monkeypatch):                 # Review Focus 4
    s, _ = svc
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(s.role.events, "_insert", full)
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "ok"
    assert s.role.health()[0] is False

async def test_call_log_line_without_values(svc, capfd, restore_logging):
    setup_logging()                                    # F13: как test_role.py Д3а, без autouse
    s, _ = svc
    await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    err = capfd.readouterr().err
    assert "tool=tag_period" in err and "cost=0.333" in err and "12.4" not in err

# core/tests/test_http.py
def test_401_is_logged(tmp_path, capfd, restore_logging):                       # Review Focus 3
    setup_logging()
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}).status_code == 401
        assert c.post("/api/data/tag_now", json={"tags": ["x"]}, headers={"Authorization": "Bearer nope"}).status_code == 401
    err = capfd.readouterr().err
    assert err.count("outcome=unauthorized channel=http") == 2 and "nope" not in err

def test_http_422_is_refused_event(tmp_path):                                   # Review Focus 3
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers=auth)
        assert r.status_code == 422 and r.json()["status"] == "refused" and "неверные аргументы" in r.json()["message"]
        e = role.events.recent(channel="http")[0]
        assert (e.outcome, e.channel, e.tool) == ("refused", "http", "tag_now")
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}, headers=auth).json()["status"] == "ok"

def test_bad_json_without_token_is_401(tmp_path):                               # Review Focus 3
    app, role = make_app(tmp_path)
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        before = role.events.count(channel="http")
        r = c.post("/api/data/tag_now", content=b"{bad", headers={"Content-Type": "application/json"})
        assert r.status_code == 401 and role.events.count(channel="http") == before   # мусора в журнале нет

def test_chunked_body_over_limit_is_413(tmp_path, capfd, restore_logging):      # Review Focus 3
    setup_logging()
    app, _ = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    with TestClient(app) as c:
        chunks = (b"x" * 10_000 for _ in range(7))          # без Content-Length
        assert c.post("/api/data/tag_now", content=chunks, headers=auth).status_code == 413
        assert c.post("/api/data/tag_now", content=b"x" * 70_000, headers=auth).status_code == 413   # по длине
        assert c.post("/mcp", content=b"x" * 70_000, headers=auth).status_code == 413   # до маршрутизации
    assert "outcome=too_large" in capfd.readouterr().err

def test_config_error_http_is_503_with_reason(tmp_path):                       # F5, Review Focus 6
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(SETTINGS, [role])) as c:
        r = c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}, headers=auth)
        assert (r.status_code, r.json()["status"], r.json()["message"]) == (503, "unavailable", "белый список не читается")
        assert c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers=auth).status_code == 503   # и до 422
        assert c.post("/api/data/tag_now", json={"tags": ["x"]}).status_code == 401                        # токен — раньше
    assert fake.calls == []

# core/tests/test_role.py (+) — токены не читаются: причина на странице, не падение (F4)
def test_build_roles_unreadable_tokens_is_red_not_crash(tmp_path, capfd, restore_logging):
    setup_logging()
    settings = replace(SETTINGS, BDRV_ENV_FILE=write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"),
                       WHITELIST_PATH=write(tmp_path, "20FAKE_001_PV\n"), TOKENS_FILE=str(tmp_path / "нет"))
    [role] = build_roles(settings)
    assert role.config_error is None and role.tokens.ids == frozenset()
    assert role.health() == (False, TOKENS_ERROR) and "токены" in capfd.readouterr().err

# core/tests/test_service.py (+) — текст ошибки SQL Server не выходит из процесса
async def test_error_detail_never_leaves_process(svc, capfd, restore_logging):
    setup_logging()                                    # иначе проверка журнала холостая
    s, fake = svc
    fake.fail = HistorianError("auth", "Login failed for user 'FAKEUSER'")
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    e = s.role.events.recent(channel="http")[0]
    assert "FAKEUSER" not in e.summary and "FAKEUSER" not in capfd.readouterr().err
```

`make_service(tmp_path)` строит `DataRole` с `FakeHistorian` и `FakeMono`,
загружает каталог, чистит `fake.calls` и возвращает `(role.service, fake)`;
у `DataService` есть поле `role`. `sent_of(fake)` — имена из `names_in` всех
отправленных SQL. `gate.clear_pause()` — метод ворот, дописывается здесь:
снимает защёлку и паузу; им пользуются только тесты, у людей защёлку снимает
перезапуск службы. У `FakeHistorian` поле `fail` принимает любое исключение.
`make_role` получает ещё `config_error: str | None = None` и передаёт его
роли. Фикстуры `autouse` с `setup_logging()` нет (F13): обработчик Д3а уже
пишет в текущий `sys.stderr` (`logs.py:10–21`), а autouse оставил бы журнал
настроенным на всю сессию и обесценил `restore_logging`. Каждый тест со
строками журнала берёт `restore_logging` и зовёт `setup_logging()` первым
делом, как `test_role.py:95–96` Д3а.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_service.py tests/test_http.py tests/test_role.py`. Expected: FAIL.

- [ ] **Step 3: Implement по интерфейсам**

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных: tag_now, tag_period, catalog_search по HTTP — мёртвые теги, отказы событиями, 401 и 413 в журнале"`.

---

### Task 6: MCP

**Files:**
- Create: `core/pcbk_core/data/mcp_server.py`
- Modify: `core/pcbk_core/data/__init__.py` (конструктор строит MCP; `mounts`, `lifespan`),
  `core/tests/{conftest,helpers}.py` (`serve_role`, `core_server`, `mcp_session`, `http_post`)
- Test: `core/tests/test_mcp.py`

**Interfaces:**
- Consumes: `DataService`, `refuse_args`, `config_unavailable`, `TokenTable`, модели
  `CatalogSearchArgs`, `TagNowArgs`, `TagPeriodArgs` — задача 5; `DataRole.tokens`
  (всегда `TokenTable`, F18), `events.open` в начале `lifespan` — задача 4; `Role`,
  `RoleBase`, точные маршруты `mounts()`, ветка `config_error` в `_lifespan`
  (`data/__init__.py:194–198`) — Д3а.
- Produces:
  - `build_mcp(role: DataRole) -> FastMCP` —
    `FastMCP("pcbk-data", stateless_http=True, json_response=True, streamable_http_path="/mcp", log_level="WARNING", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`:
    - `log_level="WARNING"`: иначе FastMCP включает INFO на корневом логгере,
      и `pytds` пишет в журнал адрес историана и начало SQL;
    - защита от DNS rebinding выключена: хост разный (`core:8000`,
      `${STU_NET}.N.2:8000`, туннель), защищает токен на каждом запросе;
    - три инструмента с аннотацией `-> dict[str, Any]` (с `-> dict`
      `structuredContent` пуст), `readOnlyHint=True`, `openWorldHint=False`;
    - аргументы свободные, схему для модели задаёт `json_schema_extra`:
      `tags: Annotated[Any, Field(json_schema_extra={"type": "array", "items": {"type": "string", "maxLength": 128}, "minItems": 1, "maxItems": 16})] = None`,
      `period: Annotated[Any, Field(json_schema_extra={"type": "string", "enum": list(PRESETS)})] = None`,
      `start`, `end`, `query` — `Any` со схемой строки, `limit: Any = 10`.
      Внутри — `TagNowArgs.model_validate(...)` и т. д. Ошибка проверки →
      `role.service.refuse_args(...)` с событием. Строка вместо списка, нет
      тегов, неизвестный период и 17 тегов дают событие, а не английский
      отказ FastMCP до функции;
    - при `config_error` инструмент отвечает `config_unavailable` раньше
      разбора аргументов: это первая строка методов службы, включая
      `refuse_args` (F5). Транспорт MCP отвечает на `tools/call` 200 (так
      устроен JSON-RPC), отказ — в самом результате: `status: unavailable`,
      причина в `message`;
    - канал `"mcp"`; вызывающий — `role.tokens.caller` по заголовку
      `Authorization` из `ctx.request_context.request`; `role.tokens` — всегда
      `TokenTable`, проверки на `None` нет (F18).
  - `mcp_auth(app: ASGIApp, role: DataRole) -> ASGIApp` — чистое ASGI (не
    `BaseHTTPMiddleware`: тот ломает потоковые ответы). Для `/mcp`: `GET` →
    `405`; без верного `Bearer` → `401 {"detail": "нужен токен"}` и строка
    журнала `outcome=unauthorized channel=mcp`.
  - `DataRole.__init__` строит `self.mcp = build_mcp(self)` и
    `self.mcp_app = mcp_auth(self.mcp.streamable_http_app(), self)` один раз.
    `mounts()` → `[("/mcp", self.mcp_app)]` — точный маршрут, без `Mount("/")`,
    чужие пути ролей Д4/Д5 он не перехватит. Сборка MCP в конструкторе
    файлов не открывает (F4).
  - `_lifespan` (F8): `self.mcp.session_manager.run()` — **самым внешним**,
    ровно один раз (`run()` второй раз SDK не даёт), до ветки `config_error`
    Д3а (`if self.config_error: …; yield; return`) и до `events.open`.
    Иначе при ошибке настройки `/mcp` отвечает 500 «Task group is not
    initialized». Порядок внутри — как в задаче 4:
    ```python
    @asynccontextmanager
    async def _lifespan(self):
        async with self.mcp.session_manager.run():
            await asyncio.to_thread(self.events.open)          # задача 4, F23
            if self.config_error:
                log.error("служба данных: фоновые циклы не запущены — %s", self.config_error)
                yield
                return
            ...                                                  # циклы каталога, свежести, журнала — Д3а и задача 4
    ```
  - `TOOL_DESCRIPTIONS` (их читает модель; текст постоянный):
    - `catalog_search`: «Поиск тегов БДРВ участка по словам в имени и описании. Описания в историане обрезаны на 50 символах: если точного совпадения нет, вернутся похожие — покажите их студенту на выбор, не угадывайте.»
    - `tag_now`: «Текущее значение тегов БДРВ (до 16 за вызов): значение, единица, время с поясом, возраст и качество. Имена берите точно из catalog_search.»
    - `tag_period`: «Сводка по тегам БДРВ за период (до 16 тегов, окно до 31 суток, бюджет 24 тего-суток): среднее, минимум, максимум, размах, σ — посчитаны историаном по сырым данным. Период — готовый (period) или начало и конец (start, end, ISO 8601). В ответе сказано, какое это среднее, в каком поясе время и что смены 06/14/22 — допущение.»
  - помощник `serve_role(role, *, wait_catalog: bool = True)` — контекстный
    менеджер: `create_app(SETTINGS, [role])`, `uvicorn.Server` в потоке на
    `127.0.0.1:0`; при `wait_catalog` ждёт загрузки каталога; отдаёт `url`.
    Фикстура `core_server` — `serve_role` с `DataRole` на `FakeHistorian` и
    `FakeMono` (`make_role(db_dir=tmp_path)`); отдаёт `url`, `role`. Тест F8
    берёт `serve_role(..., wait_catalog=False)` с ролью в `config_error`:
    каталог у неё не грузится.
    `mcp_session(url, token)` — `streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"})`
    → `ClientSession` → `initialize()`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_mcp.py
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
ACCEPT = "application/json, text/event-stream"

def raw(url, method="POST", body=INIT, **headers):
    req = urllib.request.Request(url, json.dumps(body).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "Accept": ACCEPT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

@pytest.mark.anyio
async def test_mcp_lists_three_readonly_tools(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"catalog_search", "tag_now", "tag_period"}
    assert all(t.annotations.readOnlyHint for t in tools.values())
    tags = tools["tag_now"].inputSchema["properties"]["tags"]
    assert (tags["type"], tags["maxItems"], tags["items"]["maxLength"]) == ("array", 16, 128)
    assert tools["tag_now"].description == TOOL_DESCRIPTIONS["tag_now"]

@pytest.mark.anyio
async def test_mcp_and_http_give_same_answer(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        sc = (await s.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})).structuredContent
    code, body = http_post(core_server.url + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, TOKEN)
    drop = lambda d: {k: v for k, v in d.items() if k not in ("cache", "asof")}
    assert code == 200 and sc is not None and drop(sc) == drop(json.loads(body))

@pytest.mark.anyio
async def test_mcp_bad_arg_types_are_refused_events(core_server):              # Review Focus 3
    bad = [("tag_now", {"tags": "20FAKE_001_PV"}), ("tag_now", {}),
           ("tag_period", {"tags": ["20FAKE_001_PV"], "period": "last_year"}),
           ("tag_now", {"tags": [f"20FAKE_{i:03d}_PV" for i in range(17)]})]
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        for tool, args in bad:
            sc = (await s.call_tool(tool, args)).structuredContent
            assert sc["status"] == "refused" and "неверные аргументы" in sc["message"], args
            e = core_server.role.events.recent(channel="mcp")[0]
            assert (e.outcome, e.channel, e.caller, e.sent) == ("refused", "mcp", "student-01", ())

def test_mcp_auth_host_and_get(core_server):                                    # Review Focus 5
    url = core_server.url + "/mcp"
    assert raw(url) == 401 and raw(url, Authorization="Bearer nope") == 401
    for host in ("core:8000", "172.31.1.2:8000"):
        assert raw(url, Authorization=f"Bearer {TOKEN}", Host=host) == 200
    assert raw(url, method="GET", Authorization=f"Bearer {TOKEN}") == 405
    assert raw(url + "/", Authorization=f"Bearer {TOKEN}") == 404                  # точный маршрут

@pytest.mark.anyio
async def test_lifespan_without_app_runs(tmp_path):                             # Review Focus 5
    role, _ = make_role(db_dir=tmp_path)
    async with role.lifespan():                       # MCP построен в конструкторе, run() — один раз
        pass
    assert not logging.getLogger("pytds").isEnabledFor(logging.INFO)
    assert role.mcp.settings.log_level == "WARNING"   # под pytest корень уже настроен — проверяем настройку

@pytest.mark.anyio
async def test_mcp_with_config_error_answers_not_500(tmp_path):                 # F8, Review Focus 5
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    with serve_role(role, wait_catalog=False) as url:
        async with mcp_session(url + "/mcp", TOKEN) as s:            # initialize проходит: run() уже идёт
            sc = (await s.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})).structuredContent
            bad = (await s.call_tool("tag_now", {"tags": "20FAKE_001_PV"})).structuredContent
    assert (sc["status"], sc["message"]) == ("unavailable", "белый список не читается")
    assert bad["status"] == "unavailable" and fake.calls == []        # и раньше разбора аргументов
```

`make_role(db_dir=…, config_error=…)` — помощник задач 4 и 5: `DB_PATH` во
временном каталоге, тестовый `TokenTable` (`TEST_TOKENS`), `TOKEN` — из
`helpers.py`.

- [ ] **Step 2: Run tests to verify they fail** — `cd core && CORE_PYTEST tests/test_mcp.py`. Expected: FAIL.

- [ ] **Step 3: Implement `build_mcp`, `mcp_auth`, построение в конструкторе, точный маршрут и жизненный цикл**

- [ ] **Step 4: Run the whole core suite** — `cd core && CORE_PYTEST`. Expected: PASS (включая `test_drill_freeze_poll_stops_loop` Д3а — роль без приложения).

- [ ] **Step 5: Commit** — `git add core/ && git commit -m "Служба данных по MCP 1.30.0: точный маршрут /mcp, любые ошибки аргументов — событиями, журнал без INFO SDK"`.

---

### Task 7: Компоновка и выкладка

**Когда:** после тега `platform-d3a` (Д3а выложен и влит в `main`): ветка
`d3b/tag-answers` перебазирована на `main`, полный `cd core && CORE_PYTEST` —
PASS (задача 0, шаг 1). Шаг 6 делает копию `compose.yaml.d3a` — Д3а на
сервере уже должен стоять.

**Files:**
- Modify: `core/Dockerfile` — до `USER`:
  `RUN install -d -o 10003 -g 10003 -m 0750 /var/lib/pcbk-core`, как у
  сторожа Д1 (`watchdog/Dockerfile:12`). Новый именованный том наследует
  владельца каталога образа; без каталога том получит `root:root 0755`,
  `EventLog.open()` не создаст `core.db` — служба живёт (конструктор без
  ввода-вывода, F4), инструменты отвечают, но строка «Служба данных» красная
  («журнал событий не пишется»).
- Modify: `compose.yaml` (у `core` — `image: pcbk-reserve/core:d3b`,
  `sysctls: {net.ipv4.tcp_fastopen: "0"}` (Д3б-R6), секрет
  `core-tokens`, том `pcbk-core-data:/var/lib/pcbk-core`; в `volumes:` ключ
  `pcbk-core-data:` без `name:` — как `pcbk-watchdog-journal`
  (`compose.yaml:470–473`): имя с префиксом проекта, тестовый проект
  `pcbk-test` том выкладки не тронет, F23), `.gitignore`
  (`core-tokens`, `*.data-token`), `tests/integration/conftest.py` (тестовый
  `core-tokens` с токеном `student-01`; `stack.core_token`;
  `http_host(method, url, body=None, token=None)`), `tests/integration/test_core.py`,
  `tests/integration/test_edge.py` (`IMAGES["pcbk-core"]` → `:d3b`),
  `deploy/README.md` (токены, журнал событий, MCP)
- Create: `docs/checks/D3b.md` — первым его пишет эта задача (шаги 6–7); задача 1,
  шаг 8, и задача 8 дописывают (F24)

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_core.py (+)
def test_tool_over_egress_answers_and_journals(stack):
    code, body = stack.http_host("POST", CORE + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, token=stack.core_token)
    assert code == 200 and json.loads(body)["status"] == "unavailable"          # историана нет — словами
    assert stack.http_host("POST", CORE + "/api/data/tag_now", {"tags": ["x"]})[0] == 401
    assert "outcome=unauthorized channel=http" in stack.logs("pcbk-core")

def test_core_state_volume_writable(stack):                                     # Review Focus 4
    # в тестовом стенде bdrv.env целый — 1 направление (при config_error было бы «0 направлений», F3)
    assert "охрана выхода: разрешено 1 направление" in stack.logs("pcbk-core")
    # core.db создаётся в начале lifespan роли (F23) — файл есть до первого вызова инструмента
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    assert mounts["/var/lib/pcbk-core"]["Type"] == "volume"
    assert (mounts["/run/secrets/core-tokens"]["Type"], mounts["/run/secrets/core-tokens"]["RW"]) == ("bind", False)
    stack.exec("pcbk-core", "test", "-w", "/var/lib/pcbk-core")               # при отказе exec бросит исключение
    assert stack.exec("pcbk-core", "stat", "-c", "%u", "/var/lib/pcbk-core/core.db") == "10003"
    code, body = stack.http_host("GET", CORE + "/healthz/data")
    assert code == 200 and json.loads(body)["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail** — `uv run --python 3.12 --with pytest pytest -q tests/integration/test_core.py`. Expected: FAIL.

- [ ] **Step 3: Implement правки компоновки и фикстуры**

- [ ] **Step 4: Run the whole local suite** — `(cd core && CORE_PYTEST) && (cd watchdog && uv run --python 3.12 --with pytest pytest -q) && uv run --python 3.12 --with pytest pytest -q tests/integration`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add core/Dockerfile compose.yaml .gitignore tests/integration/ && git commit -m "Серверный слой: каталог журнала от uid 10003, токены и журнал событий в компоновке"`.

- [ ] **Step 6: Выкладка**

На сервере (содержимое на экран не выводится):

```bash
cd /opt/pcbk-reserve/secrets && umask 077
if [ ! -e core-tokens ]; then
  t=$(openssl rand -hex 24); printf '%s' "$t" > ops.data-token && chmod 0400 ops.data-token   # 48 знаков — под шаблоном Д2
  printf 'ops %s\n' "$(printf '%s' "$t" | sha256sum | cut -d' ' -f1)" > core-tokens && chmod 0444 core-tokens
fi
stat -c '%a %n' core-tokens ops.data-token
```

Токен `ops` — в `$JOB` без вывода на экран:
`$SSH 'cat /opt/pcbk-reserve/secrets/ops.data-token' > "$JOB/ops.token"`, затем
`printf 'Authorization: Bearer %s\n' "$(cat "$JOB/ops.token")" > "$JOB/ops.hdr"; chmod 600 "$JOB"/ops.*`.
Образ `:d3b` — `docker save … | $SSH …`, сверка `RootFS`;
`cp -p compose.yaml compose.yaml.d3a`; `rsync compose.yaml`;
`docker compose up -d --no-build core`.
Expected:
- `healthy`; на странице «Служба данных — жива», историан в норме — снимок
  `docs/checks/D3b/01-after-deploy.png` способом Д3а;
- `docker logs pcbk-core 2>&1 | grep -c 'охрана выхода: разрешено 1 направление'` → 1;
- `docker exec pcbk-core stat -c '%u %a' /var/lib/pcbk-core` → `10003 750`,
  `docker exec pcbk-core stat -c '%u' /var/lib/pcbk-core/core.db` → `10003`;
- память меньше 70 % от 256 МиБ.

Откат: `compose.yaml.d3a` и `docker compose up -d --no-build core` (образ
`:d3a`); том журнала событий (`pcbk-core-data` с префиксом проекта) не
удаляется.

- [ ] **Step 7: Commit** (после проверки на секреты) — `docs/checks/D3b.md` создаётся здесь (F24):
  `git add deploy/README.md docs/checks/D3b.md docs/checks/D3b/ && git commit -m "Выкладка Д3б: инструменты службы данных на сервере"`.

---

### Task 8: Живые проверки

Итог каждого шага — вердиктом [П] в `docs/checks/D3b.md`, без имён тегов,
значений и адресов. **Шаги 3–6 — при владельце.** Туннель —
`$SSH -N -L 18000:172.31.250.82:8000` в фоне. Имена для проверки лежат на
сервере в `probe-out/`, в `$JOB` едут через `scp` и на экран не выводятся.

- [ ] **Step 1: Здоровье через туннель.** `curl -s http://127.0.0.1:18000/healthz/data`
  → `{"ok": true, …}`; `/health/historian` → `error: null`,
  `gate.refused_unlisted: 0`.

- [ ] **Step 2: MCP.** `initialize`, затем `tools/list` — оба запроса `curl`
  с `-H @"$JOB/ops.hdr"`, `-H 'Content-Type: application/json'`,
  `-H 'Accept: application/json, text/event-stream'`, телом JSON-RPC, на
  `http://127.0.0.1:18000/mcp` → `200`, три имени инструментов. Без
  `Content-Type` curl шлёт форму, и транспорт отвечает ошибкой — ложный сбой.
  `GET /mcp` → `405`; без токена → `401`.

- [ ] **Step 3: Ответ за смену.** Тег — первая строка `check-tag` пробы
  (лучший по различимости, в белом списке); вторая строка — запас, если
  первый ответит «нет данных». Либо тег, который назовёт владелец. Запрос
  `$JOB/req.json` —
  `{"tags": ["<тег>"], "period": "prev_shift"}`.
  `curl -s -H @"$JOB/ops.hdr" -H 'Content-Type: application/json' --data @"$JOB/req.json" http://127.0.0.1:18000/api/data/tag_period | tee "$JOB/answer.json"`.
  Expected:
  - владелец видит ответ;
  - в журнал: `status: ok`, период — предыдущая смена, 8 ч, с поясом;
    `source`, `avg_kind`, `std_kind` и `SHIFT_NOTE` на месте;
  - `duration_ms` вызова из журнала событий — не больше 5000 (критерий p95
    ≤ 5 с);
  - повтор даёт `cache: hit`.

- [ ] **Step 4: Независимая сверка.**
  `$SSH 'docker run --rm -i --network bridge --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro pcbk-probe/tds:d3b verify --date-fmt <A|B>' < "$JOB/answer.json" > "$JOB/verify.json"`;
  `grep -E -i -f <шаблоны> "$JOB/verify.json"` — пусто.
  Expected: `bounds`, `min`, `max`, `last` (если есть) — «сошлось»; печатается
  `d` и вердикт вида. Дальше по вердикту:
  - вид «подтверждён», но не тот, что в `avg_kind`, или «не сошёлся» —
    строка 8а таблицы, но только при `PIN_INTERPOLATION = "STAIRSTEP"` или
    одном типе на всех по запросу 14: `AVG_KIND` правится одним коммитом
    (на подтверждённый вид или на `"unverified"`), перевыкладка, шаги 3–4
    повторяются. Иначе вердикт записывается, `AVG_KIND` остаётся
    `"unverified"`: один тег не даёт права объявлять вид для всех;
  - «не различим на этом теге»: так и записать; вид остаётся по пробе;
  - вопрос владельцу — если вид так и не подтверждён.

  Успехом дня расхождение не прикрывается.

- [ ] **Step 5: Журнал событий доказывает белый список (основа успеха 4).**
  1. Отрицательный контроль: `tag_now` с `unlisted-tag` пробы и со строкой
     `X' OR 1=1--` → `status: refused`.
  2. Затем на сервере:
     `docker exec pcbk-core python -c "import json,sqlite3; w={l.strip() for l in open('/app/data/whitelist.txt') if l.strip() and not l.startswith('#')}; rows=list(sqlite3.connect('/var/lib/pcbk-core/core.db').execute('select requested, sent from agent_events')); sent=[x for _,s in rows for x in json.loads(s)]; req=[x for r,_ in rows for x in json.loads(r)]; print(len(rows), len(sent), sum(x not in w for x in sent), sum(x not in w for x in req))"`.

  Expected:
  - `N S 0 R`: имён вне списка в `sent` — 0, в `requested` — не меньше 2
    (отрицательный контроль виден — проверка не холостая);
  - `/health/historian` → `gate.refused_unlisted: 0`: служба и не пыталась
    отправить чужое.

- [ ] **Step 6: Охрана выхода.** Строка «охрана выхода: разрешено 1
  направление» есть. Если черта Д3а перенесла сюда факты о сети выхода —
  они делаются тут (задача 9 Д3а, шаг 5).

- [ ] **Step 7: Commit** (после проверки на секреты) — `git add docs/checks/ && git commit -m "Д3б: ответ за смену по туннелю, сверка с сырым Delta, MCP, журнал событий против белого списка"`.

---

### Task 9: Закрытие дня

- [ ] **Step 1: Документы.**
  - `docs/DESIGN-platform-2026-09-29.md`: §4 — инструменты как построены
    (имена, контракт ответа, ссылка на план); §13 — п. 8 «Д3б» (решения
    Д3б-R6…R10).
  - Предпосылки Д4 — в §13 и в строке Д4 `docs/PLAN-platform-2026-09-29.md`:
    - у MCP места `"oauth": false` и URL ровно `http://core:8000/mcp`;
    - токены `student-NN.data-token` (`openssl rand -hex 24`) и перезапуск
      `core`;
    - `pcbk-core` в сетях мест на `.2`;
    - охрана выхода: `install_egress_guard` получает ещё `openrouter.ai:443`
      — по имени, через кэш разрешения (адреса Cloudflare меняются); иначе
      LLM-прокси упрётся в `EgressDenied`. Ставится по-прежнему только в
      `serve()` (точка входа), не в `build_roles` и не в функциях, которые
      тесты зовут в процессе;
    - `core` поднимается раньше мест. OpenCode v1.18.33 не переподключает
      remote MCP сам: если при старте места `core` недоступен или токен
      неверен, сервер MCP остаётся в `failed` до перезапуска OpenCode.
      В Д4 это проверяет **сторож** (в Д5 — ещё и шлюз): состояние MCP каждого
      места, при `failed` — переподключение. Учение «`core` перезапущен при
      работающих местах» — вызов после перезапуска проходит без ручных
      действий. Это новая работа — **в оценку плана Д4**;
    - крючки уровня приложения ролей `llm` и `gateway` — только через
      `Role.install(app)`, свои маршруты — точные, как `/mcp`. Обработчик
      `RequestValidationError` на приложение один, и последний `install`
      побеждает. Поэтому Д4 заводит один общий обработчик, который по
      префиксу пути отдаёт разбор роли. Тест Д4: при двух ролях 422 на
      `/api/data` по-прежнему даёт событие;
    - `HEALTHCHECK` образа — живость процесса (`/healthz/live`), а здоровье
      ролей — только строки сторожа. Иначе 402 от OpenRouter сделает
      `core` unhealthy, и тестовый стенд Д4 без ключа не поднимется;
    - нагрузка на историан на странице: в `stats()` ворот — p95 за 5 минут,
      число сроков и `busy`; предупреждение при p95 больше 5 с; после 3
      сроков подряд — пауза полосы людей на 60 с. Сюда же — число ошибок
      инструментов за последние N вызовов (строка «инструменты отвечают
      ошибкой: k из N»). Всё это — до подключения мест;
    - строка контейнера `core` видом `container` («убит по памяти», «падает в
      цикле») требует правила `sp-ro` на `pcbk-core` — правка Д4.
  - Предпосылки Д5 — там же:
    - конфликт псевдонима `pcbk-core` в `http_as` для `sp-ctl`, когда сам
      `core` войдёт в `pcbk-ctl`;
    - поток `/event` OpenCode `edge` не должен рвать: у него свой `location`
      без `proxy_read_timeout 5s`, который сейчас стоит у ручек сторожа
      (`docs/research/07-gateway-whitelist.md`).
  - `README.md` — «Д3 готов (Д3а и Д3б)».
- [ ] **Step 2: Критик** (Opus 5.5); петля — до нуля блокеров, без предела
  раундов: владелец число раундов не ограничил (29.09, «Не лимитирую LOOP»), как
  в задаче 10 Д3а (F25). Раунды сверх первого идут сверх оценки задачи 9.
- [ ] **Step 3: Слияние.** В `main` — только после тега `platform-d3a`
  (предпосылка ослаблена лишь для кода задач 1–6), тег `platform-d3b`. Перед пушем
  проверка на секреты по ветке и
  `git ls-files | grep -cE '(core-tokens|\.data-token|whitelist\.(txt|extra))$'` → `0`.
  Затем `git push origin main platform-d3b`, чистый клон. Удалить
  `rm -f "$JOB"/{req,answer,verify,probe-b}.json "$JOB"/ops.* "$JOB"/{check,unlisted}-tag`; на
  сервере — `probe-out/`.
- [ ] **Step 4: Владельцу** — «Д3 готов», ответ за смену и вердикт сверки
  одной строкой. Вопросы: вид среднего, если не подтверждён; правила
  `DOCKER-USER` на Д12; границы смен (О6).
