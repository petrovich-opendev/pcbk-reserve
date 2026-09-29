# Д2. Десять рабочих мест OpenCode под gVisor — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** на сервере ПЦБК созданы десять рабочих мест OpenCode v1.18.33 под
gVisor. У каждого своя изолированная сеть и свой пароль из файла. Страница
состояния показывает места как «спит / работает / перезапущен после сбоя /
OpenCode не отвечает». Два учения подтверждают это снимками. Журнал проверок
показывает, что из места нет пути к соседу, к серверу, к 1433 и в интернет, а
пароль места не виден ни сторожу, ни в git.

**Architecture:** один образ `pcbk-reserve/student:d2` (ubuntu 22.04, бинарники
OpenCode и ripgrep по sha256, uid 10001) и десять служб `student-01…10` в
`compose.yaml` под профилем `students`. Настройки служб: `runtime: runsc`,
корень только для чтения, без возможностей, `init: true`, лимиты по пробе Д1.
У каждого места своя сеть `pcbk-stu-NN` — внутренняя, с изолированным шлюзом:
место на `.3`, серверный слой (Д4) на `.2` через `extra_hosts`. Пароль сервера
и токен LLM приходят файлами `secrets` Compose. Точка входа экспортирует
пароль только процессу OpenCode; `HEALTHCHECK` печатает только код ответа.
Места создаются командой `create` и спят. Сторож видит их через `sp-ro` и
называет `unhealthy` «OpenCode не отвечает». Поднимать и гасить места может
только клиент `pcbk-core` через `sp-ctl` (с Д5) или администратор.

**Tech Stack:** Docker Compose, gVisor release-20260921.0 (`runsc`, поставлен
в Д1), OpenCode 1.18.33 (`opencode-linux-x64`), ripgrep 15.1.0 (musl),
ubuntu 22.04 по дайджесту, bash (точка входа и проверка здоровья), Python 3.12
(сторож — stdlib; pytest через uv только в тестах), curlimages/curl 8.16.0
(только проверки), google-chrome (снимки).

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(редакция 3: §2 — своя сеть и свой пароль, §6, §7 п. 1, §9 — строка «Неуспех 1»,
§11 п. 1, 2, 5; §13 п. 2, 3, 5); факты —
[`docs/research/04-d1-facts.md`](../research/04-d1-facts.md), §2, §3, §6; итоги
пробы — [`docs/checks/D1.md`](../checks/D1.md); дорожная карта, строка Д2 —
[`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md). План
заменяет черновик [`DRAFT-D2-workplaces.md`](DRAFT-D2-workplaces.md) — его
решения здесь обязательны. Формат, фикстура `stack`, таблица `check_container`
и сети взяты из [`PLAN-D1-foundation-2026-09-29.md`](PLAN-D1-foundation-2026-09-29.md).

**Что поменяла проба Д1.** На сервере есть AVX2 — берём только сборку
`opencode-linux-x64`, `-baseline` не нужна. В простое OpenCode под `runsc`
занимает 243 МиБ → `mem_limit: 1g`; держит 42 потока → `pids_limit: 512`.
Убийство по памяти под `runsc` видно как `OOMKilled=true` с кодом 137.
Хранилище образов на сервере — containerd, поэтому образы сверяются по `RootFS`.
Сертификат входа истёк 21.09: заголовок страницы останется красным из-за строки
«Входной прокси», пока владелец не даст новый сертификат. Учения оцениваются
по строкам мест.

**Решения сверх черновика** (причины — в задачах):
1. `init: true`. Сигнал `kill -STOP 1` изнутри контейнера ядро игнорирует:
   PID 1 защищён. С `docker-init` OpenCode перестаёт быть PID 1 — его можно
   заморозить для учения, а осиротевшие процессы кто-то подбирает.
2. Токен LLM места монтируется уже в Д2 (`/run/secrets/llm-token`, пока
   случайный). Без файла ссылка `{file:…}` в `opencode.json` ломает загрузку
   конфигурации, и `GET /agent` отвечает 500.
3. `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME` — отдельные подкаталоги
   тома `/var/lib/opencode`. OpenCode дописывает к каждому `opencode`; без
   подкаталогов три каталога совпали бы.
4. Если файл пароля пуст или не читается, точка входа выходит с кодом 78.
   OpenCode включает Basic только при непустом пароле, так что без этой
   проверки место открылось бы.
5. Файлы секретов — `0444` в каталоге `0700`. Их монтирование — это bind, и
   uid 10001 файл `0600` чужого владельца не прочтёт. Обходить каталог `0700`
   на хосте может только его владелец.
6. У `HEALTHCHECK` добавлены `--start-interval=2s` и `--retries=3`. Готовность
   видна через секунды, а зависание — не позже двух минут.
7. Строка «OpenCode не отвечает» ставится только у работающего контейнера:
   остановленный хранит в inspect `Health` прошлого запуска.
8. Образ сторожа получает тег `:d2`, `:d1` остаётся на сервере для отката.
9. Каталог агентов монтируется с `bind.create_host_path: false`: если каталога
   нет, место не стартует, и Docker не создаёт каталог от root.
10. В `opencode.json` добавлен `model: "pcbk/stub"`. `baseURL` —
    `http://core:8000/llm/v1`; Д4 может его поменять, это пересборка образа.
11. Сборка без строки `# syntax=`: встроенный фронтенд Docker 29 знает
    `ADD --checksum`, а строка тянула бы незакреплённый образ.

**Влезает ли в день — оценка по часам.** Задача 0 идёт параллельно задачам 1–2
и в критический путь не входит. Задачи 1 и 2 не зависят друг от друга.

| Задача | Часы | Где |
|---|---|---|
| 0. Утро: предпроверки и хвосты Д1 | (0,5 параллельно) | сервер, только чтение |
| 1. Образ рабочего места | 1,5 | локально |
| 2. Сторож: «OpenCode не отвечает», места под наблюдением | 0,5 | локально |
| 3. Десять мест в компоновке и тесты | 2,5 | локально |
| 4. Выкладка | 0,75 | сервер |
| 5. Живые проверки и учения | 2 | сервер |
| 6. Закрытие дня | 1,5 | — |
| **Критический путь** | **8,75** | |

**Черта отсечения — седьмой час.** К ней зелёны задачи 1–3 и места выложены
(задача 4). В задаче 5 первыми идут шаги 1–5: место 01 под `runsc`, изоляция,
пароль и оба учения. Это видимый результат дня, его не переносим. После черты в
утро Д3 уходят шаг 6 (`dispose`) и шаг 7 (холодный старт и память); лимиты
тогда остаются по пробе Д1.

- **Задача 3 не зелёна к седьмому часу.** Выкладки нет. Видимый результат —
  снимки локального стенда
  (`docker compose -p pcbk-local --env-file <env в рабочем каталоге задания> … --profile students`)
  с учением «OpenCode завис» под runc и пометкой «не на сервере». Учение «память
  переполнена» под runc не воспроизводится: ядро убивает только `tail`, а не
  песочницу. Так и записать. Выкладка — первым делом утром Д3.
- **Места под `runsc` не стартуют в компоновке**, хотя проба Д1 стартовала.
  Час на разбор; вероятные причины — `init` или монтирование секретов. Если не
  вышло — вопрос владельцу тем же часом: запуск без gVisor — его решение
  (вопрос 15 постановки), не исполнителя.
- **Не работает только `init: true` под `runsc`.** Убрать `init` (в тестах —
  проверки `Init` и `/proc/1/comm`). Учение «OpenCode завис» тогда проводится
  через `docker kill -s STOP` / `-s CONT pcbk-student-01`. Решение и итог — в
  журнал.
- **Хвосты Д1 (задача 0) заняли больше часа.** Шаг 7 задачи 5 уходит в Д3.

## Global Constraints

Действуют все ограничения Д1 (повторены дословно) и ограничения Д2 ниже.

- Сторонние образы — только закреплённые: в `Dockerfile` — `FROM имя:тег@sha256:…`;
  в `compose.yaml` — `имя:тег` с `pull_policy: never`; тег к дайджесту
  привязывает `deploy/images.lock` (строка `имя:тег sha256:<дайджест индекса>`,
  тестовые образы помечены `# test`, на сервер не возятся), тесты и выкладка
  берут образы только через него; `latest` запрещён. Бинарники — по sha256
  или sha512 из [`04-d1-facts.md`](../research/04-d1-facts.md).
- Секреты и данные заказчика — **никогда в git и в выводе проверок**: ключ
  TLS, `.env` выкладки, адреса, имена хостов и учётных записей заказчика.
  Адреса в командах — только в переменных оболочки, в журнал — только
  вердикт. Перед каждым коммитом задач 0 и 4–6:
  `git diff --cached -U0 -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто,
  где `<шаблоны>` — файл в рабочем каталоге задания (не в git): адреса
  сервера и историана, имя хоста, учётные записи заказчика и общие шаблоны
  `BEGIN [A-Z ]*PRIVATE KEY`, `Basic [A-Za-z0-9+/=]{16,}`, `PASSWORD=[^$<{ "]`.
- **Секреты контейнерам — только файлами** с монтированием `:ro`, никогда
  через `environment`/`env_file`: `sp-ro` отдаёт сторожу inspect, а в нём
  `Config.Env`.
- Личные учётные записи и имена сотрудников заказчика не пишутся нигде;
  учётная запись ОС в командах — `$(id -un)`.
- Dify не трогаем: ни одного изменения в `/opt/dify`, его контейнерах и сетях;
  из каталога Dify только копируются сертификат и ключ. Docker перечитывает
  настройки только `systemctl reload docker`, **никогда `restart`**.
- `docker.sock` смонтирован **только** в `sp-ro` и `sp-ctl`.
- Сети стенда — с закреплёнными подсетями (таблица ниже) и `name:` без
  префикса проекта. Все, кроме `pcbk-public`, — `internal: true` и
  `driver_opts: {com.docker.network.bridge.gateway_mode_ipv4: isolated}`,
  `gateway` не задаётся; у `pcbk-public` —
  `com.docker.network.bridge.enable_ip_masquerade: "false"`. Исключение с Д3:
  сеть выхода `pcbk-egress` только для `pcbk-core` (историан и OpenRouter).
- Наружу публикуется только `8443` контейнера `edge`.
- Всё, что видит человек, — по-русски; имена в коде — по-английски,
  комментарии — по-русски и коротко.
- Время на странице — с явным смещением от UTC; пояс — `DISPLAY_TZ`, по
  умолчанию `UTC`.
- Свои вспомогательные скрипты, пробный образ и шаблоны проверки на секреты —
  в рабочем каталоге задания, не в репозитории.

**Д2 добавляет:**

- Рабочие места в `compose.yaml` — только с `runtime: runsc`; `runc` допустим
  только в `compose.test.yaml`. Запустить места на сервере без gVisor может
  решить только владелец.
- Пароль сервера OpenCode и токен LLM места хранятся в файлах
  `${SECRETS_DIR}/student-NN.pw` и `student-NN.llm-token`: 48 шестнадцатеричных
  знаков без перевода строки. Порождаются только на сервере: `umask 077`,
  каталог `0700`, файлы `0444`; при повторе не перезаписываются. В git, выводе
  проверок и журнале их нет. В команды пароль попадает только через файл или
  `--env-file` — не в аргументах и не на экране.
- Без читаемого непустого пароля OpenCode не запускается: код 78.
- Проверка здоровья печатает только код ответа — её вывод виден сторожу в
  `State.Health.Log`.
- Место входит только в свою сеть `pcbk-stu-NN` и не публикует портов. Адреса
  статические: место `.3`, серверный слой `.2` через `extra_hosts` (под gVisor
  DNS Docker не работает). Динамические адреса — только из `.8/29`.
- Места выкладываются командой `create` и спят; поднимает их серверный слой
  (с Д5) или администратор. Профиль `students` не даёт командам выкладки Д1 без
  имён служб их поднять.
- Лимиты места: `mem_limit: 1g`, `pids_limit: 512`, `cpus: 1.0`. Меняются
  только по замеру Д2; порог — память > 700 МиБ или потоки > 358 (70 % от 512).
- Тома `pcbk-student-NN-state` и `pcbk-student-NN-work` с первого входа
  студента — его данные, удалять их нельзя. Откат Д2 удаляет только тома,
  созданные в этот день.
- Образ места собирается только локально. На сервер он едет через
  `docker save | docker load` со сверкой `RootFS`; на сервере он не
  собирается и из сети не тянется.
- Д2 не требует `sudo`: пользователь входит в группу `docker`. Доступ —
  `SSH="ssh -i ~/.ssh/pcbk_ai_lab -o BatchMode=yes expert@<сервер>"`, дальше
  по тексту `$SSH`.
- Проверки сетей — способом Д1
  (`curl -sv --connect-timeout 3 -m 4 telnet://… 2>&1 | grep -cE 'Established connection|Connected to'`,
  1 — соединение есть, 0 — нет). В каждом наборе обязателен положительный
  контроль, без него набор не засчитывается.

## Review Focus

1. **Файл пароля пуст, не смонтирован или не читается uid 10001** — например,
   на сервере `umask 077` без `chmod` или опечатка в `SECRETS_DIR`. OpenCode
   включает Basic только при непустом пароле, и место открылось бы любому в
   своей сети. Ожидание: место не стартует (код 78), страница показывает
   «остановлен, код 78», а при перезапусках — «падает в цикле». Тест — задача 1,
   `test_entrypoint_refuses_without_password` (три случая).
2. **Спящее место с `unhealthy` прошлого запуска или место в холодном старте
   (`starting`).** Ложная красная «OpenCode не отвечает» появлялась бы у
   спящего места или при каждом входе студента. Ожидание: «спит» и «работает».
   Красная строка — только у работающего `unhealthy`; цикл падений и пауза
   важнее её. Тест — задача 2, `test_health_row_only_for_running_container`.
3. **Место есть в компоновке, но сторож его не ждёт или ждёт другое имя.**
   Компонент без наблюдения — блокер дня; обратный случай — вечная
   «нет контейнера». Тест — задача 3, `test_every_workplace_is_watched`.
4. **Локально под runc всё зелёное, а в `compose.yaml` место без `runsc` или
   без ограничений.** `compose.test.yaml` подменяет рантайм и может это скрыть.
   Ожидание: производственный файл сам несёт `runsc`, только чтение,
   `cap_drop`, лимиты, `init` и профиль. Тесты — задача 3, производственная
   часть `test_student_hardening` (`stack.prod_config()`); повтор проверок
   только чтения под `runsc` — задача 5, шаг 1.
5. **Чужой пароль открывает место** — выкладка скопировала один файл на всех,
   или генерация повторилась. Изоляция паролей держится только на их различии.
   Ожидание: пароль места 02 к месту 01 — 401; на сервере 20 файлов секретов
   попарно различны. Тесты — задача 3, `test_opencode_requires_password`
   (строка с паролем 02); задача 4, шаг 2 (`sort -u | wc -l` → 20).

---

## Карта файлов

```
student/Dockerfile                    образ места: загрузка по sha256, ubuntu 22.04, uid 10001
student/pcbk-entrypoint               пароль из файла → только процессу OpenCode; без пароля — код 78
student/pcbk-health                   GET /global/health с Basic из файла; печатает только код
student/config/opencode.json          провайдер pcbk; ключей mcp, lsp, formatter, plugin нет
student/config/.gitignore             ПУСТОЙ; без него OpenCode в каталоге только чтения даёт GET /agent → 500
student/config/tools/bash.ts          заглушки без импортов, export default, args: {}
student/config/tools/edit.ts
student/config/tools/write.ts
student/config/tools/apply_patch.ts
watchdog/pcbk_watchdog/checks.py      + строка «OpenCode не отвечает»
watchdog/components.json              места student-01…10 → вид container
watchdog/tests/helpers.py             insp(..., health=)
compose.yaml                          + десять мест, десять сетей, тома, секреты; сторож :d2
compose.test.yaml                     + runtime: runc для мест
tests/integration/conftest.py         + student_image, методы stack для мест
tests/integration/test_student_image.py   образ без компоновки
tests/integration/test_students.py        места в компоновке
tests/integration/test_edge.py        DECLARED_ENV и образы: + место 01, сторож :d2
tests/integration/test_socket_proxy.py    места больше не absent
.gitignore                            + *.pw, *.llm-token
deploy/README.md                      + места: секреты, агенты, create, откат, новый студент
docs/checks/D2.md                     журнал живых проверок Д2
docs/checks/D2/*.png                  снимки страницы состояния
```

**Сети** — таблица Д1 без изменений, плюс десять сетей мест (N = 1…10,
`STU_NET` по умолчанию `172.31`):

| Сеть | Подсеть | Вид | Кто в ней |
|---|---|---|---|
| `pcbk-stu-NN` | `${STU_NET}.N.0/28`, динамика только `${STU_NET}.N.8/29` | внутренняя, изолированный шлюз | `pcbk-student-NN` — `.3`; с Д4 `pcbk-core` — `.2` |

Имена: образ `pcbk-reserve/student:d2`, сторож `pcbk-reserve/watchdog:d2`;
службы `student-01…10`, контейнеры `pcbk-student-01…10`; тома
`pcbk-student-NN-state` (→ `/var/lib/opencode`) и `pcbk-student-NN-work`
(→ `/work`); секреты Compose `student-NN-pw` и `student-NN-llm` (в месте —
`/run/secrets/opencode-pw` и `/run/secrets/llm-token`); агенты —
`${AGENTS_DIR}/student-NN` → `/etc/pcbk-opencode/opencode/agents`. Ветка дня —
`d2/workplaces` от `main` с тегом `platform-d1`.

---

### Task 0: Утро — предпроверки и хвосты Д1

Только чтение, по SSH, параллельно задачам 1–2. Итоги пишутся в
`docs/checks/D2.md` вердиктами с пометкой [П]. Адреса живут только в
переменных оболочки: `STU` (из `.env`), `HOST_LAN` и `BDRV_HOST` (получаются
так же, как в Д1, задача 0, шаг 1). JSON страницы разбирается через `python3`,
не через `grep`: сторож может экранировать кириллицу.

**Files:**
- Create: `docs/checks/D2.md`

- [ ] **Step 1: Стенд Д1 жив, подсети мест свободны**

На сервере (`cd /opt/pcbk-reserve`):
`STU=$(grep '^STU_NET=' .env | cut -d= -f2); STU=${STU:-172.31}; docker compose ps --format '{{.Name}} {{.State}}'; curl -sk https://127.0.0.1:8443/status.json | python3 -c 'import json,sys; print([c["component"] for c in json.load(sys.stdin)["checks"] if c["state"] == "fail"])'; docker info --format '{{json .Runtimes}}' | grep -c runsc; docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -cE "^${STU//./\\.}\.([1-9]|10)\."; ip -4 route | grep -cE "^${STU//./\\.}\.([1-9]|10)\."; grep -cE '^(STU_NET|SECRETS_DIR|AGENTS_DIR)=.+' .env; grep MemAvailable /proc/meminfo; command -v openssl; docker image inspect -f '{{.Id}}' curlimages/curl:8.16.0; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d2-before.txt`
Expected: четыре службы Д1 в `running`. В `fail` — только `edge`, если
сертификат не заменён. `runsc` — 1. Сетей и маршрутов в `${STU}.1–10.x` — 0.
Переменных — 3 (задать по `deploy/env.example`, если меньше). `MemAvailable`
записать числом. `openssl` есть. Образ curl есть с пробы Д1; если его нет —
перенести его так же, как в Д1: это инструмент проверок, в `compose.yaml` его
нет. В журнал — только вердикты.

- [ ] **Step 2: Хвосты Д1**

Сначала — всё, что черта отсечения Д1 перенесла в Д2 (по README, раздел
«Состояние», и по `docs/checks/D1.md`): каждый пункт своей записью.
Если в `docs/checks/D1.md` нет итога §11 п. 3: владелец просит человека в сети
ПЦБК открыть страницу состояния на порту 8443. Записать, как именно прошло:
открылась, отказ соединения, ошибка корпоративного прокси или ошибка
сертификата (сертификат истёк 21.09 — это ожидаемо). Нет человека до конца
дня — «не проверено, ждёт владельца».
Expected: у каждого хвоста вердикт или «ждёт владельца».

- [ ] **Step 3: Commit** (после проверки на секреты)

```bash
git add docs/checks/D2.md
git commit -m "Д2: утро — стенд Д1 жив, подсети мест свободны, хвосты Д1"
```

---

### Task 1: Образ рабочего места

**Files:**
- Create: `student/Dockerfile`, `student/pcbk-entrypoint`, `student/pcbk-health`,
  `student/config/opencode.json`, `student/config/.gitignore` (0 байт),
  `student/config/tools/{bash,edit,write,apply_patch}.ts`
- Modify: `tests/integration/conftest.py` (фикстура `student_image`)
- Test: `tests/integration/test_student_image.py`

**Interfaces:**
- Consumes: суммы и версии из фактов §6; правило закрепления образов Д1.
- Produces:
  - образ `pcbk-reserve/student:d2`: `USER 10001:10001`, `WORKDIR /work`,
    `EXPOSE 4096`, `ENTRYPOINT ["/usr/local/bin/pcbk-entrypoint"]`,
    `HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 CMD ["/usr/local/bin/pcbk-health"]`
  - раскладка: `/usr/local/bin/{opencode,rg,pcbk-entrypoint,pcbk-health}`
    (root, 0755); `/etc/pcbk-opencode/opencode/` = `student/config/` (root,
    0644), плюс пустой `agents/` (0755) — точка монтирования; `HOME=/home/pcbk`
    (root, 0755); `/var/lib/opencode` и `/work` — владелец 10001, 0700;
    `/run/secrets` (0755)
  - переменные образа — ровно `IMAGE_ENV` из теста ниже
  - точка входа: читает `/run/secrets/opencode-pw`. Если файла нет, он пуст
    или не читается — в stderr «pcbk: нет пароля сервера OpenCode в
    /run/secrets/opencode-pw — не запускаюсь», код 78. Иначе
    `export OPENCODE_SERVER_PASSWORD=…` и
    `exec opencode serve --hostname 0.0.0.0 --port 4096`
  - `pcbk-health`: запрос `GET /global/health` на `127.0.0.1:4096` с Basic
    `$OPENCODE_SERVER_USERNAME:<файл пароля>`. Печатает одну строку — код
    ответа, или `000`, если ответа нет. Код выхода 0 только при 200; stderr
    пуст
  - в месте ожидаются (монтирует задача 3): `/run/secrets/opencode-pw`,
    `/run/secrets/llm-token`, агенты `:ro`, тома `/var/lib/opencode` и
    `/work`, tmpfs `/tmp` с `exec`
  - фикстура `student_image` (на сессию): `docker build -t pcbk-reserve/student:d2 student/`,
    возвращает имя образа
  - `STUB_PREFIX = "Отключено на учебном стенде"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_student_image.py
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "student" / "config"
IMAGE = "pcbk-reserve/student:d2"
STUB_PREFIX = "Отключено на учебном стенде"
SEC = 10 ** 9
IMAGE_ENV = {
    "HOME": "/home/pcbk", "XDG_CONFIG_HOME": "/etc/pcbk-opencode",
    "XDG_DATA_HOME": "/var/lib/opencode/data", "XDG_CACHE_HOME": "/var/lib/opencode/cache",
    "XDG_STATE_HOME": "/var/lib/opencode/state", "TMPDIR": "/tmp",
    "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true", "BUN_RUNTIME_TRANSPILER_CACHE_PATH": "0",
    "OPENCODE_SERVER_USERNAME": "opencode",
}

def docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)

def test_config_files():
    cfg = json.loads((CONFIG / "opencode.json").read_text())
    assert set(cfg) == {"autoupdate", "share", "snapshot", "enabled_providers", "model", "provider"}
    assert (cfg["autoupdate"], cfg["share"], cfg["snapshot"], cfg["enabled_providers"], cfg["model"]) == \
           (False, "disabled", False, ["pcbk"], "pcbk/stub")
    pcbk = cfg["provider"]["pcbk"]
    assert (pcbk["npm"], list(pcbk["models"])) == ("@ai-sdk/openai-compatible", ["stub"])
    assert pcbk["options"] == {"baseURL": "http://core:8000/llm/v1", "apiKey": "{file:/run/secrets/llm-token}"}
    assert (CONFIG / ".gitignore").read_bytes() == b""
    assert subprocess.run(["git", "check-ignore", "-q", "student/config/.gitignore"], cwd=ROOT).returncode == 1
    tools = sorted((CONFIG / "tools").iterdir())
    assert [p.name for p in tools] == ["apply_patch.ts", "bash.ts", "edit.ts", "write.ts"]
    for p in tools:
        src = p.read_text()
        assert "export default" in src and not re.search(r"\b(import|require)\b", src), p.name
        assert re.search(r'description:\s*"' + STUB_PREFIX, src) and re.search(r"args:\s*\{\s*\}", src), p.name

def test_image_metadata(student_image):
    cfg = json.loads(docker("image", "inspect", IMAGE).stdout)[0]["Config"]
    env = dict(item.split("=", 1) for item in cfg["Env"])
    assert {k: env.get(k) for k in IMAGE_ENV} == IMAGE_ENV
    assert [k for k in env if re.search(r"PASSWORD|SECRET|TOKEN|APIKEY", k)] == []
    assert (cfg["User"], cfg["WorkingDir"], cfg["Entrypoint"]) == \
           ("10001:10001", "/work", ["/usr/local/bin/pcbk-entrypoint"])
    hc = cfg["Healthcheck"]
    assert hc["Test"] == ["CMD", "/usr/local/bin/pcbk-health"]
    assert (hc["Interval"], hc["Timeout"], hc["StartPeriod"], hc["StartInterval"], hc["Retries"]) == \
           (30 * SEC, 5 * SEC, 20 * SEC, 2 * SEC, 3)
    out = docker("run", "--rm", "--network", "none", "--entrypoint", "bash", IMAGE,
                 "-c", "opencode --version; rg --version").stdout.splitlines()
    assert out[0] == "1.18.33" and out[1].startswith("ripgrep 15.1.0")

@pytest.mark.parametrize("case", ["missing", "empty", "unreadable"])
def test_entrypoint_refuses_without_password(student_image, tmp_path, case):   # Review Focus 1
    mount = []
    if case != "missing":
        pw = tmp_path / "pw"
        pw.write_text("" if case == "empty" else "not-for-uid-10001")
        pw.chmod(0o444 if case == "empty" else 0o600)   # 0600 чужого uid — как umask 077 без chmod
        mount = ["-v", f"{pw}:/run/secrets/opencode-pw:ro"]
    r = docker("run", "--rm", "--network", "none", "--read-only", *mount, IMAGE, timeout=30)
    assert r.returncode == 78 and "нет пароля" in r.stderr

def test_health_prints_only_code(student_image, tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("probe-secret")
    pw.chmod(0o444)
    r = docker("run", "--rm", "--network", "none", "--read-only", "-v", f"{pw}:/run/secrets/opencode-pw:ro",
               "--entrypoint", "/usr/local/bin/pcbk-health", IMAGE, timeout=30)
    assert (r.returncode, r.stdout, r.stderr) == (1, "000\n", "")     # сервера нет — только код
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py`
Expected: FAIL — нет `student/config/opencode.json`, сборка образа падает

- [ ] **Step 3: Implement образ, точку входа, проверку здоровья и конфигурацию по интерфейсам выше**

`student/Dockerfile` — две стадии на одном базовом образе. Строки загрузки
копируются как есть:

```dockerfile
FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02 AS fetch
ADD --checksum=sha256:e546123213ae47909a4268692aa4b94950d011afe9cac9938753a2194f1c16d5 \
    https://github.com/anomalyco/opencode/releases/download/v1.18.33/opencode-linux-x64.tar.gz /dl/
ADD --checksum=sha256:1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599 \
    https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz /dl/
```

Стадия `fetch` распаковывает оба архива. Итоговая стадия строится на том же
`FROM`, без apt: `groupadd -g 10001 pcbk`, `useradd -u 10001 -g 10001 -M -d /home/pcbk -s /usr/sbin/nologin pcbk`,
каталоги и права — по разделу «раскладка», два бинарника и два скрипта из
`fetch` и `student/`, `COPY student/config/ /etc/pcbk-opencode/opencode/`
(вместе с `.gitignore`), затем `ENV`, `USER`, `WORKDIR`, `HEALTHCHECK`,
`ENTRYPOINT`.

`pcbk-entrypoint` и `pcbk-health` написаны на bash, без `set -x` и без
временных файлов: корень только для чтения, here-string создал бы временный
файл. `pcbk-health` открывает `/dev/tcp/127.0.0.1/4096` с подавленным stderr,
читает строку статуса через `read -t 4` и печатает только код. Заглушки —
`export default { description, args: {}, async execute() { return description } }`:
у `bash` описание «Отключено на учебном стенде: запуск команд недоступен.», у
`edit`, `write`, `apply_patch` — «Отключено на учебном стенде: правка файлов
недоступна.». `student/config/.gitignore` — пустой файл, строку `.gitignore`
в него не добавлять: иначе git не возьмёт файл, и образ из чистого клона даст
`GET /agent` → 500.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py`
Expected: PASS (6 тестов)

- [ ] **Step 5: Commit**

```bash
git add student/ tests/integration/conftest.py tests/integration/test_student_image.py
git commit -m "Образ рабочего места: OpenCode 1.18.33 по sha256, заглушки, пароль файлом, проверка здоровья без секрета"
```

---

### Task 2: Сторож — «OpenCode не отвечает», места под наблюдением

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/components.json`,
  `watchdog/tests/helpers.py`
- Test: `watchdog/tests/test_checks.py` (+2 теста),
  `watchdog/tests/test_main.py` (`test_components_file_d1` → `test_components_file_d2`)

**Interfaces:**
- Consumes: `check_container`, `Check`, `insp`, `T0`, `stu` из
  `test_checks.py`, `load_components`, `Settings` — без изменений (файл
  `COMPONENTS_PATH=/app/components.json` вшит в образ сторожа), `run_checks`
  передаёт `sleeping_ok` из `components.json`.
- Produces:
  - `UNHEALTHY_DETAIL = "OpenCode не отвечает"` в `checks.py`
  - `insp(..., health: str | None = None)` — при заданном `health` добавляет
    `State["Health"] = {"Status": health, "FailingStreak": 3 if health == "unhealthy" else 0, "Log": []}`
  - `components.json`: `student-01…10` — `{"id": "student-NN", "title": "Рабочее место NN", "kind": "container", "container": "pcbk-student-NN", "sleeping_ok": true}`,
    на тех же местах списка; `core`, `historian`, `llm` остаются `absent`

Таблица `check_container` Д1 с новой строкой (первая подходящая сверху вниз):

| Состояние Docker | Итог |
|---|---|
| нет контейнера (`None`) | `fail` «нет контейнера» |
| `Paused` | `fail` «приостановлен» |
| `Running` или `Restarting`, `RestartCount` ≥ 3 и `StartedAt` моложе `RECENT_RESTART` | `fail` «падает в цикле: N перезапусков» |
| `Restarting` (Docker ставит и `Running`) | `warn` «перезапускается» |
| **`Running` и `State.Health.Status == "unhealthy"` (Д2)** | **`fail` «OpenCode не отвечает»** |
| `Running`, `RestartCount` > 0 и `StartedAt` моложе `RECENT_RESTART` | `warn` «перезапущен после сбоя в ЧЧ:ММ (N с последнего запуска)» |
| `Running` | `ok` «работает» (при `RestartCount` > 0 — «работает, сбоев с последнего запуска: N») |
| остановлен, `OOMKilled` | `fail` «убит по памяти» |
| остановлен, `State.Error` не пуст | `fail` «не запускается: <первые 80 знаков ошибки>» |
| остановлен, `sleeping_ok`, код ∈ {0, 137, 143} | `ok` «спит» |
| остановлен, иначе | `fail` «остановлен, код N» |

У остановленного контейнера `Health` не смотрится: Docker оставляет в inspect
статус прошлого запуска. `starting` и `healthy` дают то же, что отсутствие
проверки здоровья. Текст строки постоянный — журнал пишет одно событие. Из
контейнеров, которые сторож проверяет через `check_container`, `HEALTHCHECK`
есть только у мест: у `sp-ctl` проверки здоровья нет, а свою сторож через
`check_container` не смотрит.

- [ ] **Step 1: Write the failing tests**

```python
# test_checks.py
def test_health_unhealthy_is_fail():
    r = stu(insp(running=True, health="unhealthy"))
    assert (r.state, r.detail) == ("fail", "OpenCode не отвечает")
    fresh = stu(insp(running=True, restarts=1, started=T0 - timedelta(minutes=3), health="unhealthy"))
    assert (fresh.state, fresh.detail) == ("fail", "OpenCode не отвечает")    # сильнее «перезапущен после сбоя»

def test_health_row_only_for_running_container():                            # Review Focus 2
    assert (stu(insp(running=True, health="starting")).state, stu(insp(running=True, health="starting")).detail) == ("ok", "работает")
    assert stu(insp(running=True, health="healthy")).detail == "работает"
    assert (stu(insp(code=143, health="unhealthy")).state, stu(insp(code=143, health="unhealthy")).detail) == ("ok", "спит")
    assert stu(insp(running=True, paused=True, health="unhealthy")).detail == "приостановлен"
    loop = stu(insp(running=True, restarts=3, started=T0 - timedelta(seconds=20), health="unhealthy"))
    assert loop.state == "fail" and "падает в цикле" in loop.detail

# test_main.py — заменяет test_components_file_d1
def test_components_file_d2():
    comps = load_components("components.json")
    assert [c["id"] for c in comps][:4] == ["edge", "sp-ro", "sp-ctl", "memory"]
    students = [c for c in comps if c["id"].startswith("student-")]
    assert [(c["id"], c["title"], c["kind"], c["container"], c["sleeping_ok"]) for c in students] == \
           [(f"student-{n:02d}", f"Рабочее место {n:02d}", "container", f"pcbk-student-{n:02d}", True)
            for n in range(1, 11)]
    assert {c["id"] for c in comps if c["kind"] == "absent"} == {"core", "historian", "llm"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q`
Expected: FAIL — `insp() got an unexpected keyword argument 'health'`;
`test_components_file_d2` — места ещё `absent`

- [ ] **Step 3: Implement строку таблицы, `UNHEALTHY_DETAIL`, `insp(health=)` и `components.json` по интерфейсам выше**

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/`
Expected: PASS. Интеграционные тесты Д1 до задачи 3 ждут мест, которых ещё
нет; весь набор прогоняется в задаче 3.

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: «OpenCode не отвечает» по проверке здоровья, рабочие места под наблюдением"
```

---

### Task 3: Десять мест в компоновке и тесты

**Files:**
- Modify: `compose.yaml` (якорь `x-student`, службы `student-01…10`, сети
  `pcbk-stu-01…10`, тома, секреты; у сторожа `image: pcbk-reserve/watchdog:d2`),
  `compose.test.yaml` (места — `runtime: runc`), `tests/integration/conftest.py`
  (методы `stack` ниже), `tests/integration/test_edge.py`
  (`DECLARED_ENV["pcbk-student-01"] = set()`; в `test_env_holds_only_known_names` —
  `("pcbk-student-01", "pcbk-reserve/student:d2")` и сторож `:d2`),
  `tests/integration/test_socket_proxy.py` (в `test_status_json_overall_ok`:
  `absent >= {"core"}`, а `student-01` не `absent`), `.gitignore`
  (`*.pw`, `*.llm-token`)
- Test: `tests/integration/test_students.py`

**Interfaces:**
- Consumes: образ и его договорённости, фикстура `student_image` — задача 1;
  `components.json` — задача 2; из Д1 — фикстура `stack` (`https`,
  `wait_status`, `inspect`, `network`, `host_bridge_addresses`, `containers`,
  `env_names`, `image_env_names`, `start`, `stop`, `restart`, `pause`,
  `unpause`, `http_from_watchdog`, `http_as`), `DECLARED_ENV`, `RO`, `CTL`,
  таблица сетей, `curlimages/curl:8.16.0 # test` из `deploy/images.lock`.
- Produces:
  - службы, контейнеры, сети, тома и секреты с именами из карты файлов;
    фрагмент ниже — образец для места NN (`N` без ведущего нуля)
  - `test.env` фикстуры: плюс `STU_NET=172.31`, `SECRETS_DIR`, `AGENTS_DIR`
    (свежие на сессию). Секреты — `secrets.token_hex(24)` без перевода
    строки, каталог `0700`, файлы `0444` — как на сервере. Агенты — каталоги
    `student-NN` с правами `0755`.
  - порядок фикстуры: `student_image` → `up -d --build` (без профиля, как в
    Д1) → `create` десяти мест по именам служб → … → в конце
    `--profile students down -v`
  - `start`/`stop` для `pcbk-student-*`: готовность — `State.Health.Status == "healthy"`
    (до 60 с); отказ — `State.Error` или выход контейнера
  - новые методы `stack`:
    - `password(n: int) -> str`, `llm_token(n: int) -> str` — из тестового `SECRETS_DIR`
    - `agents_dir(n: int) -> Path`
    - `exec(name: str, script: str) -> tuple[int, str, str]` — `docker exec <name> bash -c <script>`
    - `opencode_pid(name: str) -> int` — PID, у которого `readlink /proc/PID/exe == /usr/local/bin/opencode`
    - `oc(n: int, method: str, path: str, password: str | None, timeout: float = 10.0) -> tuple[int, str]` —
      одноразовый `curlimages/curl` в `pcbk-stu-NN` к `http://172.31.n.3:4096{path}`.
      Пароль передаётся через `--env-file` (временный файл), не через
      аргументы; `None` — запрос без Basic
    - `wait_oc(n: int, timeout: float = 30.0) -> None` — `GET /global/health`
      со своим паролем, по 2 с на попытку, до 200
    - `probe(network: str, addr: str, port: int) -> int` — способ Д1, 0 или 1
    - `wait_healthy(name: str, timeout: float = 60.0) -> None`
    - `body_from_watchdog(url: str) -> str` — как `http_from_watchdog`, но возвращает тело
    - `prod_config() -> dict` —
      `docker compose -p pcbk-test --env-file <test.env> -f compose.yaml --profile students config --format json`,
      без `compose.test.yaml`; результат кэшируется
    - `host_lan() -> str` — первый адрес `hostname -I`

```yaml
x-student: &student                      # общее для десяти мест
  image: pcbk-reserve/student:d2
  pull_policy: never
  runtime: runsc
  profiles: [students]
  user: "10001:10001"
  init: true
  read_only: true
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  tmpfs: ["/tmp:exec,mode=1777,size=256m"]
  mem_limit: 1g
  cpus: 1.0
  pids_limit: 512
  restart: unless-stopped
  stop_grace_period: 10s
  labels: {pcbk.role: student}

services:
  student-NN:
    <<: *student
    container_name: pcbk-student-NN
    networks: {pcbk-stu-NN: {ipv4_address: "${STU_NET:-172.31}.N.3"}}
    extra_hosts: ["core:${STU_NET:-172.31}.N.2"]
    volumes:
      - pcbk-student-NN-state:/var/lib/opencode
      - pcbk-student-NN-work:/work
      - {type: bind, source: "${AGENTS_DIR}/student-NN", target: /etc/pcbk-opencode/opencode/agents,
         read_only: true, bind: {create_host_path: false}}
    secrets:
      - {source: student-NN-pw, target: opencode-pw}
      - {source: student-NN-llm, target: llm-token}

networks:
  pcbk-stu-NN:
    name: pcbk-stu-NN
    internal: true
    driver_opts: {com.docker.network.bridge.gateway_mode_ipv4: isolated}
    ipam: {config: [{subnet: "${STU_NET:-172.31}.N.0/28", ip_range: "${STU_NET:-172.31}.N.8/29"}]}

volumes:
  pcbk-student-NN-state: {name: pcbk-student-NN-state}
  pcbk-student-NN-work: {name: pcbk-student-NN-work}

secrets:
  student-NN-pw: {file: "${SECRETS_DIR}/student-NN.pw"}
  student-NN-llm: {file: "${SECRETS_DIR}/student-NN.llm-token"}
```

Секции `build` у мест нет: образ один на десять служб и собирается фикстурой
`student_image` (на сервере — `docker save`/`load`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_students.py
ROOT = Path(__file__).resolve().parents[2]
STU = "172.31"                                                     # STU_NET из test.env
RO = "http://pcbk-sp-ro:2375/v1.44"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
STUB_PREFIX = "Отключено на учебном стенде"
WORKPLACES = {f"pcbk-student-{n:02d}" for n in range(1, 11)}
HISTORIAN_ADDR = os.environ.get("HISTORIAN_ADDR", "192.0.2.10")   # адрес заказчика — только из окружения
RUNTIME_ENV = {"HOSTNAME", "PWD", "SHLVL", "_", "OLDPWD"}         # добавляют Docker и bash при exec
PROBE_AGENT = "---\ndescription: Проба видимости агента\nmode: primary\n---\nТы — проба.\n"

@pytest.fixture(scope="module")
def running(stack):                                                # места 01 и 02 работают до конца модуля
    for name in ("pcbk-student-01", "pcbk-student-02"):
        stack.start(name)
    yield
    for name in ("pcbk-student-01", "pcbk-student-02"):
        stack.stop(name)

def student_rows(data):
    return {c["component"]: (c["state"], c["detail"]) for c in data["checks"] if c["component"].startswith("student-")}

def test_every_workplace_is_watched(stack):                        # Review Focus 3; первый в модуле — все спят
    in_compose = {s["container_name"] for s in stack.prod_config()["services"].values()
                  if s.get("labels", {}).get("pcbk.role") == "student"}
    comps = json.loads((ROOT / "watchdog" / "components.json").read_text())
    watched = {c["container"] for c in comps if c["kind"] == "container" and c["sleeping_ok"]}
    assert in_compose == watched == WORKPLACES
    data = stack.wait_status(lambda d: len(student_rows(d)) == 10 and
                             all(s == "ok" for s, _ in student_rows(d).values()), timeout=30)
    assert set(student_rows(data).values()) == {("ok", "спит")}

def test_sp_ctl_really_starts_and_stops_student(stack):
    url = CTL + "/containers/pcbk-student-03"
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/start") == 204
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/start") == 304
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/stop") == 204
    assert stack.inspect("pcbk-student-03")["State"]["Running"] is False
    stack.wait_status(lambda d: student_rows(d).get("student-03") == ("ok", "спит"), timeout=30)

def test_student_hardening(stack, running):
    hc = stack.inspect("pcbk-student-01")["HostConfig"]
    assert (hc["ReadonlyRootfs"], hc["CapDrop"], hc["CapAdd"], hc["Privileged"]) == (True, ["ALL"], None, False)
    assert "no-new-privileges:true" in hc["SecurityOpt"]
    assert (hc["Memory"], hc["PidsLimit"], hc["NanoCpus"], hc["Init"]) == (1024 ** 3, 512, 10 ** 9, True)
    assert hc["RestartPolicy"]["Name"] == "unless-stopped" and not hc["PortBindings"]
    pid = stack.opencode_pid("pcbk-student-01")
    status = dict(line.split(":", 1) for line in
                  stack.exec("pcbk-student-01", f"cat /proc/{pid}/status")[1].splitlines() if ":" in line)
    assert {k: status[k].split() for k in ("CapEff", "CapBnd", "NoNewPrivs")} == \
           {"CapEff": ["0000000000000000"], "CapBnd": ["0000000000000000"], "NoNewPrivs": ["1"]}
    assert status["Uid"].split() == ["10001"] * 4
    assert stack.exec("pcbk-student-01", "cat /proc/1/comm")[1].strip() == "docker-init"
    services = stack.prod_config()["services"]                    # без compose.test.yaml — Review Focus 4
    for nn in (f"{n:02d}" for n in range(1, 11)):
        s = services[f"student-{nn}"]
        assert (s["runtime"], s["read_only"], s["cap_drop"], s["init"], s["profiles"]) == \
               ("runsc", True, ["ALL"], True, ["students"])
        assert "no-new-privileges:true" in s["security_opt"] and not s.get("ports")
        assert (int(s["mem_limit"]), s["pids_limit"], float(s["cpus"])) == (1024 ** 3, 512, 1.0)
        assert list(s["networks"]) == [f"pcbk-stu-{nn}"]
        agents = next(v for v in s["volumes"] if v["target"] == "/etc/pcbk-opencode/opencode/agents")
        assert agents["read_only"] is True and not agents.get("bind", {}).get("create_host_path")

def test_opencode_requires_password(stack, running):
    stack.wait_oc(1, timeout=30)                                   # первые секунды запросы висят
    assert stack.oc(1, "GET", "/global/health", None)[0] == 401
    assert stack.oc(1, "GET", "/global/health", stack.password(1))[0] == 200
    assert stack.oc(1, "GET", "/global/health", stack.password(2))[0] == 401   # Review Focus 5

def tool_descriptions(stack, model):
    code, body = stack.oc(1, "GET", f"/experimental/tool?provider=pcbk&model={model}", stack.password(1))
    assert code == 200
    return {t["id"]: t["description"] for t in json.loads(body)}

def test_stubs_replace_builtin_tools(stack, running):
    stub = tool_descriptions(stack, "stub")
    assert all(stub[t].startswith(STUB_PREFIX) for t in ("bash", "edit", "write"))
    assert not {"apply_patch", "multiedit", "patch"} & set(stub)
    gpt = tool_descriptions(stack, "gpt-5")                        # apply_patch есть только у gpt-*
    assert gpt["apply_patch"].startswith(STUB_PREFIX) and gpt["bash"].startswith(STUB_PREFIX)
    assert not {"edit", "write", "multiedit", "patch"} & set(gpt)

def agent_names(stack):
    code, body = stack.oc(1, "GET", "/agent", stack.password(1))
    assert code == 200
    return {a["name"] for a in json.loads(body)}

def test_agent_file_visible_after_dispose(stack, running):
    assert "probe" not in agent_names(stack)                       # экземпляр создан до записи файла
    probe = stack.agents_dir(1) / "probe.md"
    try:
        probe.write_text(PROBE_AGENT)
        probe.chmod(0o644)
        assert "probe" not in agent_names(stack)                   # без dispose не виден
        assert stack.oc(1, "POST", "/instance/dispose", stack.password(1))[0] == 200
        assert "probe" in agent_names(stack)
    finally:
        probe.unlink(missing_ok=True)
        stack.oc(1, "POST", "/instance/dispose", stack.password(1))

def test_readonly_where_it_matters(stack, running):
    for d in ("/home/pcbk", "/etc/pcbk-opencode/opencode", "/etc/pcbk-opencode/opencode/tools",
              "/etc/pcbk-opencode/opencode/agents", "/usr/local/bin", "/run/secrets"):
        assert stack.exec("pcbk-student-01", f"touch {d}/.pcbk-w")[0] != 0, f"{d} пишется"
    for d in ("/var/lib/opencode", "/work", "/tmp"):
        assert stack.exec("pcbk-student-01", f"touch {d}/.pcbk-w && rm {d}/.pcbk-w")[0] == 0, f"{d} не пишется"
    assert list(stack.agents_dir(1).iterdir()) == []

def test_no_route_anywhere(stack, running):
    nets = [f"pcbk-stu-{n:02d}" for n in range(1, 11)]
    assert stack.host_bridge_addresses(*nets) == []                # главное: у мостов нет адреса хоста
    for n, net in enumerate(nets, 1):
        info = stack.network(net)
        assert info["Internal"] is True
        assert info["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
        assert [(c["Subnet"], c["IPRange"]) for c in info["IPAM"]["Config"]] == \
               [(f"{STU}.{n}.0/28", f"{STU}.{n}.8/29")]
    eps = stack.inspect("pcbk-student-01")["NetworkSettings"]["Networks"]
    assert list(eps) == ["pcbk-stu-01"] and eps["pcbk-stu-01"]["IPAddress"] == f"{STU}.1.3"
    assert stack.exec("pcbk-student-01", "getent hosts core")[1].split()[0] == f"{STU}.1.2"
    assert stack.probe("pcbk-stu-01", f"{STU}.1.3", 4096) == 1    # положительный контроль: своё место
    assert stack.probe("pcbk-stu-02", f"{STU}.2.3", 4096) == 1    # место 02 живо — его 0 ниже не пустой
    gateway = stack.network("pcbk-stu-01")["IPAM"]["Config"][0].get("Gateway")
    targets = [(a, p) for a in sorted({f"{STU}.1.1", gateway} - {None}) for p in (22, 80, 443, 2375, 3389)]
    targets += [(stack.host_lan(), 22), (stack.host_lan(), 443), (HISTORIAN_ADDR, 1433),
                ("1.1.1.1", 443), (f"{STU}.2.3", 4096)]
    assert [t for t in targets if stack.probe("pcbk-stu-01", *t) != 0] == []

def test_password_not_visible_to_watchdog(stack, running):
    stack.wait_healthy("pcbk-student-01", timeout=60)              # в Health.Log уже есть вывод проверки
    body = stack.body_from_watchdog(RO + "/containers/pcbk-student-01/json")
    log = json.loads(body)["State"]["Health"]["Log"]
    assert log and all(re.fullmatch(r"\d{3}\n?", e["Output"]) for e in log)
    leaked = stack.password(1) in body or stack.llm_token(1) in body   # значения не попадают в вывод pytest
    assert leaked is False and "OPENCODE_SERVER_PASSWORD" not in body

def test_env_holds_only_own_secret(stack, running):
    pid = stack.opencode_pid("pcbk-student-01")
    raw = stack.exec("pcbk-student-01", f"cat /proc/{pid}/environ")[1]
    pairs = [item.split("=", 1) for item in raw.split("\0") if item]
    extra = {k for k, _ in pairs} - stack.env_names("pcbk-student-01") - RUNTIME_ENV
    assert extra == {"OPENCODE_SERVER_PASSWORD"}, sorted(extra)      # печатаются только имена
    values = {v for _, v in pairs}
    own = stack.password(1) in values
    foreign = bool(values & ({stack.password(n) for n in range(2, 11)} | {stack.llm_token(1)}))
    assert (own, foreign) == (True, False)
```

`test_health_unhealthy_is_fail` из черновика — модульный тест сторожа, он в
задаче 2.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_students.py`
Expected: FAIL — нет служб `student-NN`, у `stack` нет `prod_config`, `oc`, `probe`

- [ ] **Step 3: Implement десять мест, `compose.test.yaml`, методы `stack`, правки тестов Д1 и `.gitignore` по интерфейсам выше**

В `compose.test.yaml` у десяти мест — только `runtime: runc`, через якорь; всё
остальное берётся из производственного файла. Таймаут curl в `http_as` — не
меньше 20 с: `stop` ждёт `stop_grace_period`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_students.py`
Expected: PASS

- [ ] **Step 5: Run the whole local suite**

Run: `(cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/) && uv run --python 3.12 --with pytest pytest -q tests/integration && docker network ls --filter name=pcbk- -q | wc -l && docker volume ls --filter name=pcbk-student- -q | wc -l`
Expected: PASS; после прогона сетей `pcbk-` — 0, томов `pcbk-student-` — 0

- [ ] **Step 6: Commit**

```bash
git add compose.yaml compose.test.yaml tests/integration/ .gitignore
git commit -m "Десять рабочих мест под gVisor: своя изолированная сеть, пароль и токен файлами, профиль students"
```

---

### Task 4: Выкладка на сервер

Шаги — команды и вывод, который значит «прошло». Итоги — в `docs/checks/D2.md`.
`sudo` не нужен.

**Files:**
- Modify: `deploy/README.md` — места: порождение секретов (`umask 077`,
  каталог `0700`, файлы `0444`, не перезаписывать), каталоги агентов, `create`
  вместо `up`, ручной запуск и остановка места, откат. Отдельно: после первого
  входа студента тома не удаляются. Новый студент — правка `compose.yaml` и
  `components.json`, его секреты и каталог агентов.
- Modify: `docs/checks/D2.md`

- [ ] **Step 1: Собрать и перенести образы**

Локально: `docker build -t pcbk-reserve/student:d2 student/`,
`docker compose build watchdog` (тег `:d2`);
`docker save pcbk-reserve/student:d2 pcbk-reserve/watchdog:d2 | gzip | $SSH 'gunzip | docker load'`.
Сверка на обеих сторонах: `docker image inspect -f '{{json .RootFS}}'` —
хранилище containerd, как записано в Д1.
Expected: у обоих образов `RootFS` совпали.

- [ ] **Step 2: Секреты и каталоги агентов (на сервере)**

```bash
cd /opt/pcbk-reserve && umask 077 && install -d -m 0700 secrets && install -d -m 0755 agents &&
for n in $(seq -w 1 10); do
  for s in pw llm-token; do f=secrets/student-$n.$s
    [ -e "$f" ] || { openssl rand -hex 24 | tr -d '\n' > "$f"; chmod 0444 "$f"; }
  done
  install -d -m 0755 agents/student-$n
done
stat -c '%a' secrets; stat -c '%a %s' secrets/* | sort -u; stat -c '%a' agents agents/* | sort -u
sha256sum secrets/* | cut -c1-64 | sort -u | wc -l
```
Expected: `700`; `444 48`; `755`; `20` — все двадцать секретов разные
(Review Focus 5). Содержимое на экран не выводится.

- [ ] **Step 3: Создать места и обновить сторожа**

`cp -p compose.yaml compose.yaml.d1` на сервере; локально
`rsync -a compose.yaml …:/opt/pcbk-reserve/` (без `--delete`). На сервере:
`docker compose --profile students create --no-build $(printf 'student-%02d ' $(seq 1 10))`,
затем `docker compose up -d --no-build watchdog`.
Порядок важен: если сначала поднять сторожа, на странице появятся десять
красных «нет контейнера».
Expected:
- `docker ps -a --filter label=pcbk.role=student --format '{{.State}}' | sort | uniq -c` → `10 created`;
- `docker network ls --filter name=pcbk-stu- -q | wc -l` → `10`;
- у мостов десяти сетей нет IPv4 (`ip -4 addr show dev br-<id>` пусто);
- `curl -sk https://127.0.0.1:8443/status.json | python3 -c 'import json,sys; print(sum(c["detail"] == "спит" for c in json.load(sys.stdin)["checks"]))'` → `10`;
- `ip route get "$BDRV_HOST"` по-прежнему через шлюз (вердикт);
- у контейнеров Dify время работы продолжает `~/pcbk-d2-before.txt`.

Откат: `docker compose rm -sf $(printf 'student-%02d ' $(seq 1 10))`;
`docker network rm $(printf 'pcbk-stu-%02d ' $(seq 1 10))`; тома
`pcbk-student-NN-*` удалить только до первого входа студента; вернуть
`compose.yaml.d1` и выполнить `docker compose up -d --no-build watchdog`
(образ `:d1`).

- [ ] **Step 4: Commit** (после проверки на секреты)

```bash
git add deploy/README.md docs/checks/D2.md
git commit -m "Выкладка Д2: десять рабочих мест созданы и спят, сторож их видит"
```

---

### Task 5: Живые проверки и учения

Шаги идут по приоритету, черта отсечения — в шапке. Итог каждого шага —
вердиктом с пометкой [П] в `docs/checks/D2.md`. Снимки делаются через
`$SSH -N -L 18443:127.0.0.1:8443` и
`google-chrome --headless=new --ignore-certificate-errors --virtual-time-budget=8000 --window-size=1200,2000 --screenshot=docs/checks/D2/<имя>.png https://127.0.0.1:18443/status`.

Помощники оболочки лежат в рабочем каталоге задания. На сервер их копируют в
`~/pcbk-d2/` и удаляют в конце дня; в репозиторий они не попадают. Все
выполняются в `/opt/pcbk-reserve`:
- `probe NET ADDR PORT` —
  `docker run --rm --runtime=runsc --network NET curlimages/curl:8.16.0 -sv --connect-timeout 3 -m 4 telnet://ADDR:PORT 2>&1 | grep -cE 'Established connection|Connected to'`;
- `oc NN METHOD PATH [нет]` — одноразовый curl под `runsc` в `pcbk-stu-NN`,
  запрос к `$STU.N.3:4096`. Файл `--env-file` с `PW=` из
  `secrets/student-NN.pw` создаётся под `umask 077` и удаляется сразу; со
  словом `нет` он без `PW`. Печатает тело и последней строкой — код;
- `ocpid NN` — `docker exec pcbk-student-NN bash -c 'for p in /proc/[0-9]*; do [ "$(readlink $p/exe)" = /usr/local/bin/opencode ] && echo ${p#/proc/}; done'`.

- [ ] **Step 1: Место 01 под `runsc` (§11 п. 1)**

`docker start pcbk-student-01`; ждать, пока `docker inspect -f '{{.State.Health.Status}}'`
не покажет `healthy` (не дольше 60 с).
Expected:
- `docker inspect -f '{{.HostConfig.Runtime}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.Init}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}' pcbk-student-01` → `runsc true true 1073741824 512`;
- `docker exec pcbk-student-01 cat /proc/version` → ядро gVisor, а не хоста; `/proc/1/comm` → `docker-init`;
- `oc 01 GET /global/health нет` → `401`; `oc 01 GET /global/health` → `200`;
- `oc 01 GET '/experimental/tool?provider=pcbk&model=stub'`: полный список `id` записывается в журнал; `grep -o 'Отключено на учебном стенде' | wc -l` → `3`; с `model=gpt-5` → `2`, среди них `apply_patch`;
- повтор `test_readonly_where_it_matters` под `runsc` одним `docker exec`: печатаются только отклонения («пишется <каталог>» / «не пишется <каталог>») → пусто;
- `/proc/$(ocpid 01)/status` → `CapEff` и `CapBnd` нули, `NoNewPrivs: 1`;
- `getent hosts core` в месте → `.2` своей сети (вердикт).

Снимок `01-ten-workplaces.png`: место 01 «работает», 02–10 «спит».

- [ ] **Step 2: Изоляция (§9 «Неуспех 1»)**

`docker start pcbk-student-02`, дождаться `healthy`. Структура:
`docker network inspect` десяти сетей — `Internal: true`, `isolated`;
`ip -4 addr show dev br-<id>` для каждой — пусто. Порты из `pcbk-stu-01`:
- положительные контроли: `probe pcbk-stu-01 $STU.1.3 4096` → **1**; `probe pcbk-stu-02 $STU.2.3 4096` → **1**;
- `$STU.1.1` и шлюз из `IPAM` на 22, 80, 443, 2375, 3389 → 0;
- `$HOST_LAN` на 22, 443; `$BDRV_HOST` на 1433; `1.1.1.1` на 443; `$STU.2.3` на 4096 → 0;
- отрицательный контроль: временная сеть `pcbk-stu-ctl` — `--internal`,
  **без** isolated, `--subnet 172.31.250.240/28`. Адрес её моста на 22 → **1**:
  проверка видит путь, который закрывает изолированный шлюз. Сеть удалить.
  Если здесь 0 — вход с мостов режет ufw; записать это, как в Д1.

Expected: как указано. Без положительных контролей набор не засчитывается.

- [ ] **Step 3: Пароль не виден**

- `docker exec pcbk-watchdog python -c 'import urllib.request as u; print(u.urlopen("http://pcbk-sp-ro:2375/v1.44/containers/pcbk-student-01/json").read().decode())' | grep -c -F -f secrets/student-01.pw` → `0`; тот же вывод `| grep -c OPENCODE_SERVER_PASSWORD` → `0`;
- `docker inspect pcbk-student-01 | grep -c -F -f secrets/student-01.pw` → `0`;
- `docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' pcbk-student-01` → только строки `200`;
- имена в окружении OpenCode: `docker exec pcbk-student-01 bash -c "tr '\0' '\n' < /proc/$(ocpid 01)/environ | cut -d= -f1 | sort"` — список записывается в журнал, сверх переменных образа и `RUNTIME_ENV` там только `OPENCODE_SERVER_PASSWORD`;
- своё: `docker exec pcbk-student-01 bash -c "grep -qxF \"OPENCODE_SERVER_PASSWORD=\$(cat /run/secrets/opencode-pw)\" <(tr '\0' '\n' < /proc/$(ocpid 01)/environ) && echo своё"` → `своё`;
- чужое: `docker exec pcbk-student-01 cat /proc/$(ocpid 01)/environ | tr '\0' '\n' | grep -c -F -f <(for f in secrets/student-0[2-9].pw secrets/student-10.pw secrets/student-01.llm-token; do cat "$f"; echo; done)` → `0`.

Expected: как указано; на экран выходят только числа, имена переменных и слово
«своё».

- [ ] **Step 4: Учение «память места переполнена»**

До учения `MemAvailable` ≥ 3 ГиБ (вердикт). Затем
`t0=$(date -Iseconds); docker exec pcbk-student-01 tail /dev/zero` — команда
оборвётся вместе с песочницей.
Expected:
- `docker events --since "$t0" --until "$(date -Iseconds)" --filter container=pcbk-student-01 --format '{{.Action}}' | grep -cx oom` ≥ 1;
- `docker inspect -f '{{.RestartCount}} {{.State.Running}}' pcbk-student-01` → `1 true`, затем `healthy`;
- страница: «Рабочее место 01 — перезапущен после сбоя в ЧЧ:ММ…» жёлтым, событие в журнале страницы.

Снимок `02-oom-restarted.png` — не позже 15 минут после сбоя. Если песочница
выжила (внутри gVisor убит только `tail`) — записать как есть [П]; жёлтой
строки не будет, вопрос — в отчёт владельцу.

- [ ] **Step 5: Учение «OpenCode завис»**

`docker exec pcbk-student-01 kill -STOP $(ocpid 01)`; время записать.
Expected: не позже чем через 2 минуты
`docker inspect -f '{{.State.Health.Status}}'` → `unhealthy`, на странице —
«Рабочее место 01 — OpenCode не отвечает» красным. Снимок `03-opencode-hung.png`.
Затем `docker exec pcbk-student-01 kill -CONT $(ocpid 01)` → `healthy` за ≤ 35 с,
красной строки места нет. Снимок `04-recovered.png`. В журнале страницы есть
сбой и восстановление.

- [ ] **Step 6: `dispose` видит файл агента, записанный с хоста (§11 п. 2, без живого потока)**

`oc 01 GET /agent` (экземпляр создан); на хосте
`install -m 0644 /dev/stdin agents/student-01/probe.md` с текстом
`PROBE_AGENT` из задачи 3.
Expected: `oc 01 GET /agent | grep -c '"name":"probe"'` → `0`;
`oc 01 POST /instance/dispose` → `200`; снова → `1`. Затем `rm agents/student-01/probe.md`,
`dispose`, → `0`.

- [ ] **Step 7: Холодный старт ×3 и память в простое (§11 п. 5, простой)**

Три раза: `docker stop pcbk-student-01`; `t0=$(date +%s%N)`;
`docker start pcbk-student-01`; один одноразовый curl под `runsc` в
`pcbk-stu-01` с `--env-file` крутит `GET /global/health` (`-m 2`, пауза 0,2 с)
до `200`; записать миллисекунды от `start`. Через 60 с простоя мест 01 и 02:
`docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.PIDs}}' pcbk-student-01 pcbk-student-02`
и `cat /sys/fs/cgroup/system.slice/docker-<полный id>.scope/{memory.current,pids.current}`.
Expected: три времени и два замера в журнале. Если память > 700 МиБ или
потоков > 358, `mem_limit`/`pids_limit` правятся одним коммитом, и места
пересоздаются (`create --force-recreate --no-build`, тома сохраняются).

- [ ] **Step 8: Вернуть места в сон**

`docker stop pcbk-student-01 pcbk-student-02`.
Expected: на странице десять строк «спит»; снимок `05-rest.png`.

- [ ] **Step 9: Commit** (после проверки на секреты)

```bash
git add docs/checks/
git commit -m "Д2: живые проверки мест под gVisor — изоляция, пароль, dispose, холодный старт; два учения"
```

---

### Task 6: Закрытие дня

- [ ] **Step 1:** В `docs/DESIGN-platform-2026-09-29.md` §11 п. 1, 2 (без
  живого потока) и 5 (простой) получают пометку [П] и ссылку на
  `docs/checks/D2.md`. В `README.md`, раздел «Состояние» — «Д2 готов», что
  дальше (Д3), карта документов с этим планом. В шапку
  `docs/plans/DRAFT-D2-workplaces.md` — одна строка: заменён этим планом. В
  `docs/PLAN-platform-2026-09-29.md`, «Отклонения», — строка только о том, что
  расходится с проектом: хвосты, ушедшие в Д3, поправки лимитов, отказ от
  `init`, если он был.
- [ ] **Step 2:** Критик (Opus 5.5) по итогу дня. Блокер — любой пункт
  «Блокер дня» дорожной карты: результат не виден; достижим неуспех 1
  (путь из места к соседу, серверу, 1433 или в интернет, пароль виден);
  место выложено без наблюдения сторожа; секрет в git. Петля — до нуля
  блокеров, не больше двух раундов; третий — только после разговора с
  владельцем.
- [ ] **Step 3:** Ветку `d2/workplaces` — в `main` (fast-forward), тег
  `platform-d2`. Проверки перед пушем:
  `git log -p main..HEAD -- . ':!docs/plans' | grep -E -i -f <шаблоны>` — пусто;
  `git ls-files | grep -cE '\.(pw|llm-token)$'` → `0`;
  `git ls-files student/config/.gitignore` — файл в индексе. Затем
  `git push origin main platform-d2`. Чистый клон
  (`git clone "$(git remote get-url origin)"` в рабочий каталог задания,
  тег `platform-d2`): `student/config/.gitignore` есть и пуст, есть
  `deploy/images.lock` и снимки `docs/checks/D2/`;
  `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py "tests/integration/test_students.py::test_agent_file_visible_after_dispose"` → PASS
  (образ из чистого клона отвечает на `GET /agent`). Удалить `~/pcbk-d2/` на
  сервере.
- [ ] **Step 4:** Владельцу — «Д2 готов», снимки и только вопросы, которые
  требуют его решения: новый сертификат входа (истёк 21.09); 8443 из сети ПЦБК,
  если не проверено; учение по памяти, если песочница выжила; поправки лимитов,
  если замер их потребовал.
