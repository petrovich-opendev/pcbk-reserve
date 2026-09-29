# Д2. Десять рабочих мест OpenCode под gVisor — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** на сервере ПЦБК созданы десять рабочих мест OpenCode v1.18.33 под
gVisor. У каждого своя изолированная сеть, свои секреты, тома и каталог
агентов. Страница состояния показывает места как «спит / работает /
перезапущен после сбоя / OpenCode не отвечает». Два учения подтверждают это
снимками, все десять мест хотя бы раз подняты под `runsc`. Журнал проверок
показывает, что из места нет пути к соседу, к серверу, к прокси сокета, к 1433
и в интернет, а пароль места не виден ни сторожу, ни в журнале контейнера, ни
в git.

**Architecture:** один образ `pcbk-reserve/student:d2` (ubuntu 22.04, бинарники
OpenCode и ripgrep по sha256, uid 10001) и десять служб `student-01…10` в
`compose.yaml` под профилем `students`. Настройки служб: `runtime: runsc`,
корень только для чтения, без возможностей, `init: true`, `ipc: private`,
память 1 ГиБ без swap. У каждого места своя сеть `pcbk-stu-NN` — внутренняя, с
изолированным шлюзом: место на `.3`, серверный слой (Д4) на `.2` через
`extra_hosts`. Тома мест внешние (`external: true`): их создаёт выкладка, и
никакая команда Compose их не удалит. Пароль сервера и токен LLM приходят
файлами `secrets` Compose; точка входа экспортирует пароль только процессу
OpenCode. `HEALTHCHECK` дёргает ручку экземпляра `GET /agent` и печатает только
код ответа. Места создаются командой `create` и спят. Сторож видит их через
`sp-ro` и называет `unhealthy` «OpenCode не отвечает», а замолчавшую проверку
здоровья — «проверка здоровья молчит». Поднимать и гасить места может только
клиент `pcbk-core` через `sp-ctl` (с Д5) или администратор.

**Tech Stack:** Docker Compose, gVisor release-20260921.0 (`runsc`, поставлен
в Д1), OpenCode 1.18.33 (`opencode-linux-x64`), ripgrep 15.1.0 (musl),
ubuntu 22.04 по дайджесту, bash (точка входа и проверка здоровья), Python 3.12
(сторож — stdlib; pytest через uv только в тестах), curlimages/curl 8.16.0
(только проверки; `--variable` есть с 8.3), google-chrome (снимки).

**Spec:** [`docs/DESIGN-platform-2026-09-29.md`](../DESIGN-platform-2026-09-29.md)
(редакция 3: §2 — своя сеть и свой пароль, §6, §7 п. 1, §9 — строка «Неуспех 1»,
§11 п. 1, 2, 5; §13 п. 2, 3, 5); факты —
[`docs/research/04-d1-facts.md`](../research/04-d1-facts.md), §2, §3, §6; итоги
пробы и выкладки Д1 — [`docs/checks/D1.md`](../checks/D1.md); дорожная карта,
строка Д2 — [`docs/PLAN-platform-2026-09-29.md`](../PLAN-platform-2026-09-29.md).
План заменяет черновик [`DRAFT-D2-workplaces.md`](DRAFT-D2-workplaces.md) — его
решения здесь обязательны. Формат, таблица `check_container` и сети взяты из
[`PLAN-D1-foundation-2026-09-29.md`](PLAN-D1-foundation-2026-09-29.md). Имена
фикстуры `stack` — из кода Д1 (`tests/integration/conftest.py`). Переносы
этого дня принимает утренний слот
[`PLAN-D3a-data-service-2026-10-01.md`](PLAN-D3a-data-service-2026-10-01.md)
(задача 0, 0,5–1,5 ч).

**Исходное состояние.** Д1 закрыт: влит в `main`, тег `platform-d1`, стенд
выложен на сервер. Образ сторожа на сервере — `pcbk-reserve/watchdog:d1`,
`deploy/README.md` описывает выкладку Д1. Проба Д1 дала следующее:
- на сервере есть AVX2 — берём только сборку `opencode-linux-x64`,
  `-baseline` не нужна;
- в простое OpenCode под `runsc` занимает 243 МиБ → `mem_limit: 1g`; держит 42
  потока → `pids_limit: 512`;
- убийство по памяти, когда `tail` — главный процесс, даёт `OOMKilled=true` и
  код 137;
- хранилище образов на сервере — containerd, поэтому образы сверяются по
  `RootFS`;
- сертификат входа истёк 21.09: заголовок страницы останется красным из-за
  строки «Входной прокси», пока владелец не даст новый сертификат. Учения
  оцениваются по строкам мест.

**Решения сверх черновика** (номера постоянны — на них ссылаются планы Д3а и
Д3б; причины — в задачах):
1. `init: true`. Сигнал `kill -STOP 1` изнутри контейнера ядро игнорирует:
   PID 1 защищён. С `docker-init` OpenCode перестаёт быть PID 1 — его можно
   заморозить для учения, а осиротевшие процессы кто-то подбирает.
2. Токен LLM места монтируется уже в Д2 (`/run/secrets/llm-token`, пока
   случайный). Без файла ссылка `{file:…}` в `opencode.json` делает
   конфигурацию неверной: OpenCode выходит при старте с кодом 1, и место падает
   в цикле.
3. `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME` — отдельные подкаталоги
   тома `/var/lib/opencode`. OpenCode дописывает к каждому `opencode`; без
   подкаталогов три каталога совпали бы.
4. Если значение пароля после чтения пустое (файла нет, он не читается, пуст
   или состоит из перевода строки), точка входа выходит с кодом 78. OpenCode
   включает Basic только при непустом пароле, так что без этой проверки место
   открылось бы.
5. Файлы секретов — `0444` в каталоге `0700`. Их монтирование — это bind, и
   uid 10001 файл `0600` чужого владельца не прочтёт. Обходить каталог `0700`
   на хосте может только его владелец. Так же устроены агенты: корень
   `AGENTS_DIR` — `0700`, каталоги мест — `0755`, файлы — `0644`. Иначе
   локальные учётные записи сервера читали бы агентов всех студентов в обход
   gVisor.
6. У `HEALTHCHECK` добавлены `--start-interval=2s` и `--retries=3`. Готовность
   видна через секунды, а зависание — не позже двух минут.
7. Строки о здоровье ставятся только у работающего контейнера: остановленный
   хранит в inspect `Health` прошлого запуска.
8. Образ сторожа получает тег `:d2`, `:d1` остаётся на сервере для отката.
9. Каталог агентов монтируется с `bind.create_host_path: false`: если каталога
   нет, место не стартует, и Docker не создаёт каталог от root.
10. В `opencode.json` добавлен `model: "pcbk/stub"`. `baseURL` —
    `http://core:8000/llm/v1`; Д4 может его поменять, это пересборка образа.
11. Сборка без строки `# syntax=`: встроенный фронтенд Docker 29 знает
    `ADD --checksum`, а строка тянула бы незакреплённый образ.
12. Проверка здоровья обращается к ручке экземпляра `GET /agent`, а не к
    `/global/health`. Глобальная ручка отвечает 200 и при сломанном экземпляре
    (например, без `.gitignore`), когда рабочие ручки отвечают 500.
13. Сторож проверяет свежесть самого сигнала здоровья. Монитор здоровья Docker
    ждёт результат `exec` без срока, и при заклинившей песочнице статус
    застывает на последнем `healthy`.
14. `memswap_limit: 1g`, то есть swap = 0: без него Docker разрешает ещё
    столько же swap, и лимит «1 ГиБ» не граница. `ipc: private` — явно, чтобы не
    зависеть от умолчания демона.
15. В одноразовый curl пароль передаётся только файлом: секрет монтируется
    `:ro`, curl берёт его через
    `--variable 'PW@/pw' --expand-user 'opencode:{{PW}}'`. Не через окружение,
    не в аргументах, без `-v`.
16. Все вызовы `docker compose` в фикстуре идут с `--profile students`.
    Профиль нужен `config` и `refuse_foreign`; что делают без него `down` и
    `ps`, зависит от версии Compose (опыты критиков дали оба исхода), поэтому
    фикстура передаёт его всегда. `up` при этом поднимает только службы вне
    профиля. На сервере `docker compose down` для отката мест не
    используется. Метод Д2 для команд оболочки называется `sh`, чтобы не
    спорить с `exec` из кода Д1.
17. Тома мест внешние (`external: true`), их создаёт выкладка
    (`docker volume create`), а в тестах — фикстура. `docker compose down -v`
    их не удаляет: запрет на удаление данных студентов держит механизм, а не
    строка README.

**Влезает ли в день — оценка по часам.** Задачи идут последовательно: одна
задача — один исполнитель, затем ревью. В часы каждой задачи с кодом входят
15 минут на ревью и правки. Задача 0а идёт параллельно задаче 1.

| Задача | Часы | Где |
|---|---|---|
| 0а. Утро: предпроверки, только чтение | (0,25 параллельно) | сервер |
| 0б. Хвосты Д1 | 0 — Д1 закрыт; §11 п. 3 — действие владельца, не блокирует | сервер |
| 1. Образ рабочего места | 1,75 | локально |
| 2. Сторож: строки здоровья, места под наблюдением | 1 | локально |
| 3. Десять мест в компоновке и тесты | 3 | локально |
| 4. Выкладка | 0,75 | сервер |
| 5. Живые проверки и учения, шаги 1–6 | 1,5 | сервер |
| 5. Шаги 7–8 (`dispose`, холодный старт и память) — переносимы | 0,5 | сервер |
| 5. Шаги 9–10 (сон, коммит) | 0,25 | сервер |
| 6. Закрытие дня | 1,5 | — |
| **Критический путь** | **10,25** (9,75 без шагов 7–8) | |

**Черта отсечения — ступенчатая, по готовности задачи 3.**

- **К 5,75 ч задачи 1–3 зелёны** (по оценке ровно 5,75) — всё по плану.
  Порядок после черты жёсткий:
  1. задача 4;
  2. задача 5, шаги 1–6;
  3. задача 5, шаги 7–8 — если к концу шага 6 прошло не больше 8,25 ч (по
     оценке — 8,0); иначе они уходят в слот Д3а;
  4. задача 5, шаги 9–10;
  5. задача 6.
- **Задача 3 зелёна между 5,75 и 6,5 ч.** Выкладка, шаги 1–6 и 9–10 идут.
  Шаги 7–8, проверка чистым клоном и второй раунд критика сразу уходят в слот
  Д3а; слияние и тег — после второго раунда.
- **Задача 3 не зелёна к 6,5 ч.** Выкладки нет. Видимый результат — снимки
  локального стенда
  (`docker compose -p pcbk-local --env-file <env в рабочем каталоге задания> -f compose.yaml -f compose.test.yaml --profile students …`)
  с учением «OpenCode завис» под runc и пометкой «не на сервере». Учение «память
  переполнена» под runc не воспроизводится: ядро убивает только `tail`, а не
  песочницу. Так и записать. В тот же час владельцу уходит строка: «Д2 не
  влезает, разрез растягивается ещё на день; планы Д3а, Д3б и Д4
  пересчитываются до их начала». Выкладка в слот Д3а не переносится: его
  0,5–1,5 ч не вмещают задачи 4–5.

Если шаги 7–8 ушли в слот Д3а, в шаге 1 задачи 6 пометку [П] получает только
§11 п. 1. П. 2 и 5 получают «локально [Л], живьём — слот Д3а». Их журнал,
пометки и возможная правка лимитов — отдельный коммит Д3а, а не довесок после
тега `platform-d2`. Проверка чистым клоном и второй раунд критика уходят в
слот Д3а, если к концу первого раунда прошло больше 9,75 ч.

Прочие ветки:
- **Д1 не закрыт** — утром нет тега `platform-d1` или на сервере нет
  `sp-ro`/`sp-ctl`. На 30.09 это не так, ветка — страховка. Первыми на
  критическом пути идут незакрытые задачи Д1 (задача 0б), затем черта
  пересчитывается: шаг 6 задачи 5 уходит за шаги 9–10, шаги 7–8 — в слот
  Д3а.
- **Места под `runsc` не стартуют в компоновке**, хотя проба Д1 стартовала.
  Час на разбор; вероятные причины — `init`, монтирование секретов или каталог
  агентов `0700`. Если не вышло — вопрос владельцу тем же часом: запуск без
  gVisor — его решение (вопрос 15 постановки), не исполнителя.
- **Не работает только `init: true` под `runsc`.** Убрать `init` (в тестах —
  проверки `Init` и `/proc/1/comm`). Учение «OpenCode завис» тогда проводится
  через `docker kill -s STOP` / `-s CONT pcbk-student-01`. Решение и итог — в
  журнал.

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
  проверок, журнале и журнале контейнера их нет. Файл шаблонов проверки на
  секреты дополняется строкой `\b[0-9a-f]{48}\b`; она действует для коммитов
  задач 0 и 4–6 и для проверки ветки перед слиянием.
- В одноразовый curl пароль передаётся только файлом `:ro` через
  `--variable 'PW@/pw' --expand-user 'opencode:{{PW}}'`, без `-v`. Не через
  окружение и не в аргументах.
- Без непустого значения пароля OpenCode не запускается: код 78.
- Проверка здоровья печатает только код ответа — её вывод виден сторожу в
  `State.Health.Log`.
- Место входит только в свою сеть `pcbk-stu-NN` и не публикует портов. Адреса
  статические: место `.3`, серверный слой `.2` через `extra_hosts` (под gVisor
  DNS Docker не работает). Динамические адреса — только из `.8/29`.
- Места выкладываются командой `create` и спят; поднимает их серверный слой
  (с Д5) или администратор. Профиль `students` не даёт командам выкладки Д1 без
  имён служб их поднять.
- Лимиты места: `mem_limit: 1g`, `memswap_limit: 1g`, `pids_limit: 512`,
  `cpus: 1.0`. Меняются только по замеру Д2; порог — память > 700 МиБ или
  потоки > 358 (70 % от 512).
- **Данные студентов не удаляются.** Тома `pcbk-student-NN-state` и `-work`
  внешние. На сервере запрещены `docker compose down` для отката мест (он
  снимает все десять) и `docker compose down -v` (удаляет журнал сторожа). Места
  снимаются только `docker compose rm -sf` с именами служб. Тома мест и
  каталоги `secrets/`, `agents/` удаляются только до первого входа студента и
  только по именам. `rm -rf /opt/pcbk-reserve` после первого входа запрещён.
- Образ места собирается только локально. На сервер он едет через
  `docker save | docker load` со сверкой `RootFS`; на сервере он не
  собирается и из сети не тянется.
- `curlimages/curl:8.16.0` (в `images.lock` помечен `# test`) — инструмент
  живых проверок. Он возится на сервер как исключение и сверяется с
  дайджестом из `images.lock`. Остаётся на сервере: помощник `probe` нужен и
  Д3а.
- Д2 не требует `sudo`: пользователь входит в группу `docker`. Доступ —
  `SSH="ssh -i <ключ> -o BatchMode=yes <учётная запись>@<сервер>"`; значения
  лежат в переменных из рабочего каталога задания, дальше по тексту — `$SSH`.
- Проверки сетей — способом Д1
  (`curl -sv --connect-timeout 3 -m 4 telnet://… 2>&1 | grep -cE 'Established connection|Connected to'`,
  1 — соединение есть, 0 — нет; только к портам, без пароля). В каждом наборе
  обязателен положительный контроль, без него набор не засчитывается.

## Review Focus

1. **Значение пароля пустое** — файл не смонтирован, не читается uid 10001
   (на сервере `umask 077` без `chmod`), пуст или состоит из перевода строки.
   OpenCode включает Basic только при непустом пароле, и место открылось бы
   любому в своей сети. Ожидание: место не стартует (код 78), страница
   показывает «остановлен, код 78», а при перезапусках — «падает в цикле».
   Тест — задача 1, `test_entrypoint_refuses_without_password` (четыре случая).
2. **Место NN получило объекты соседа.** Десять почти одинаковых блоков
   компоновки — самое вероятное место для ошибки копирования: чужой секрет
   (пароль соседа откроет место), чужой том (неуспех 3 — чужие разговоры),
   чужие агенты или адрес. Ожидание: у каждого места только свои объекты.
   Тесты — задача 3, `test_each_workplace_uses_own_objects` и строка с паролем
   02 в `test_opencode_requires_password`; живьём — задача 4, шаг 3
   («10 из 10»), и задача 5, шаг 6 (свой пароль 200, пароль 01 — 401).
3. **Сигнал здоровья врёт.** Пульс есть, а работы нет: `/global/health` 200 при
   сломанном экземпляре. Монитор здоровья замолчал на последнем `healthy`. У
   спящего места остался `unhealthy` прошлого запуска, при холодном старте
   стоит `starting`. Ожидание: сломанный, замёрзший и замолчавший — красные;
   спящее и стартующее место — не красные. Тесты — задача 1,
   `test_health_needs_working_instance`; задача 2, `test_health_silence_is_fail`
   и `test_health_row_only_for_running_container`; задача 3,
   `test_hung_workplace_row` (вся цепочка до `/status.json`).
4. **Место есть в компоновке, но сторож его не ждёт или ждёт другое имя.**
   Компонент без наблюдения — блокер дня; обратный случай — вечная
   «нет контейнера». Тест — задача 3, `test_every_workplace_is_watched`.
5. **Под runc всё зелёное, а защита ослаблена.** Возможные случаи: в
   `compose.yaml` место без `runsc` или без ограничений (`compose.test.yaml`
   это скрывает); в блоке одного из мест 02–10 появились `command`,
   `privileged`, `cap_add` или `environment:`, переопределивший
   `OPENCODE_DISABLE_PROJECT_CONFIG`; проектный каталог в записываемом `/work`
   подменил заглушку. Ожидание: производственный файл сам несёт `runsc` и
   ограничения, блоки 02–10 совпадают с блоком 01 с точностью до номера,
   окружение каждого места ровно равно окружению образа, `/work/.opencode`
   игнорируется. Тесты — задача 3, `test_student_hardening` и
   `test_project_dir_cannot_override_stubs`; повтор под `runsc` — задача 5,
   шаг 1.

---

## Карта файлов

```
student/Dockerfile                    образ места: загрузка по sha256, ubuntu 22.04, uid 10001
student/pcbk-entrypoint               пароль из файла → только процессу OpenCode; пустой — код 78
student/pcbk-health                   GET /agent с Basic из файла; печатает только код
student/config/opencode.json          провайдер pcbk; ключей mcp, lsp, formatter, plugin нет
student/config/.gitignore             ПУСТОЙ; без него OpenCode в каталоге только чтения даёт GET /agent → 500
student/config/tools/bash.ts          заглушки без импортов, export default, args: {}
student/config/tools/edit.ts
student/config/tools/write.ts
student/config/tools/apply_patch.ts
watchdog/pcbk_watchdog/checks.py      + строки «проверка здоровья молчит» и «OpenCode не отвечает»
watchdog/components.json              места student-01…10 → вид container
watchdog/tests/helpers.py             insp(..., health=, last_end=)
compose.yaml                          + десять мест, десять сетей, внешние тома, секреты; сторож :d2
compose.test.yaml                     + runtime: runc для мест
tests/integration/workplace.py        константы места для тестов; сюда переезжает ONESHOT_LABEL
tests/integration/conftest.py         + student_image, профиль students, тома мест, методы stack
tests/integration/test_student_image.py   образ без компоновки
tests/integration/test_students.py        места в компоновке
tests/integration/test_edge.py        IMAGES и DECLARED_ENV: + место 01, сторож :d2
tests/integration/test_socket_proxy.py    места больше не absent; место 10 после проверки границы спит
.gitignore                            + *.pw, *.llm-token
deploy/README.md                      + места; откат без down и rm -rf; новый студент — два случая
docs/checks/D2.md                     журнал живых проверок Д2
docs/checks/D2/*.png                  снимки страницы состояния
```

**Сети** — таблица Д1 без изменений, плюс десять сетей мест (N = 1…10,
`STU_NET` по умолчанию `172.31`):

| Сеть | Подсеть | Вид | Кто в ней |
|---|---|---|---|
| `pcbk-stu-NN` | `${STU_NET}.N.0/28`, динамика только `${STU_NET}.N.8/29` | внутренняя, изолированный шлюз | `pcbk-student-NN` — `.3`; с Д4 `pcbk-core` — `.2` |

Имена: образ `pcbk-reserve/student:d2`, сторож `pcbk-reserve/watchdog:d2`;
службы `student-01…10`, контейнеры `pcbk-student-01…10`; внешние тома
`pcbk-student-NN-state` (→ `/var/lib/opencode`) и `pcbk-student-NN-work`
(→ `/work`); секреты Compose `student-NN-pw` и `student-NN-llm` (в месте —
`/run/secrets/opencode-pw` и `/run/secrets/llm-token`); агенты —
`${AGENTS_DIR}/student-NN` → `/etc/pcbk-opencode/opencode/agents`. Ветка дня —
`d2/workplaces` от `main` с тегом `platform-d1`.

---

### Task 0а: Утро — предпроверки (только чтение)

Параллельно задаче 1. Итоги пишутся в `docs/checks/D2.md` вердиктами с
пометкой [П]. Адреса живут только в переменных оболочки: `STU` (из `.env`),
`HOST_LAN` и `BDRV_HOST` (получаются так же, как в Д1, задача 0, шаг 1). JSON
страницы разбирается через `python3`, не через `grep`: сторож может
экранировать кириллицу.

**Files:**
- Create: `docs/checks/D2.md`

- [ ] **Step 1: Стенд Д1 жив, подсети мест свободны**

На сервере (`cd /opt/pcbk-reserve`):
`STU=$(grep '^STU_NET=' .env | cut -d= -f2); STU=${STU:-172.31}; docker compose version --short; docker compose ps --format '{{.Name}} {{.State}}'; docker image inspect -f '{{.Id}}' pcbk-reserve/watchdog:d1; curl -sk https://127.0.0.1:8443/status.json | python3 -c 'import json,sys; print([c["component"] for c in json.load(sys.stdin)["checks"] if c["state"] == "fail"])'; docker info --format '{{json .Runtimes}}' | grep -c runsc; docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -cE "^${STU//./\\.}\.([1-9]|10)\."; ip -4 route | grep -cE "^${STU//./\\.}\.([1-9]|10)\."; docker volume ls -q --filter name=pcbk-student- | wc -l; grep -E '^(STU_NET|SECRETS_DIR|AGENTS_DIR)=' .env; free -m | grep -E '^(Mem|Swap):'; command -v openssl; docker image inspect -f '{{.Id}} {{json .RepoDigests}}' curlimages/curl:8.16.0; docker ps --format '{{.Names}} {{.Status}}' > ~/pcbk-d2-before.txt`
Локально: `git tag -l platform-d1`.
Expected:
- тег `platform-d1` есть; четыре службы Д1 в `running`, образ сторожа `:d1` на
  месте — иначе ветка «Д1 не закрыт»;
- версия Compose сервера записана (от неё зависит поведение `down` без
  профиля — решение 16);
- в `fail` — только `edge`, если сертификат не заменён; `runsc` — 1;
- сетей, маршрутов и томов `pcbk-student-` — 0;
- `SECRETS_DIR` и `AGENTS_DIR` заданы и лежат под `/opt/pcbk-reserve` (задать
  по `deploy/env.example`, если нет);
- `MemAvailable` и строка `Swap` записаны числами — вердикт «swap есть / нет»;
- `openssl` есть;
- образ curl есть, и в `Id` или `RepoDigests` стоит дайджест из
  `deploy/images.lock`. Если образа нет, его надо перенести так же, как в Д1
  (исключение из правила `# test`, см. ограничения Д2).

В журнал — только вердикты.

- [ ] **Step 2: Commit** (после проверки на секреты)

```bash
git add docs/checks/D2.md
git commit -m "Д2: утро — стенд Д1 жив, подсети и имена мест свободны"
```

---

### Task 0б: Хвосты Д1

Д1 закрыт (тег `platform-d1`), поэтому на критическом пути задачи нет.
Остаётся §11 п. 3 — действие владельца, которое остальную работу не
блокирует.

**Files:**
- Modify: `docs/checks/D2.md`

- [ ] **Step 1: Страховка.** Если в задаче 0а оказалось, что Д1 не закрыт, —
  незакрытые задачи Д1 делаются первыми, по плану Д1, его шагами и коммитами;
  дальше действует ветка «Д1 не закрыт» из шапки.
- [ ] **Step 2: §11 п. 3.** В `docs/checks/D1.md` он отмечен как хвост Д2.
  Владелец просит человека в сети ПЦБК открыть страницу состояния на порту
  8443. Записать, как именно прошло: открылась, отказ соединения, ошибка
  корпоративного прокси или ошибка сертификата (сертификат истёк 21.09 — это
  ожидаемо). Нет человека до конца дня — «не проверено, ждёт владельца».
- [ ] **Step 3: Commit** (после проверки на секреты) — `docs/checks/D2.md`,
  сообщение «Д2: хвост Д1 — страница из сети ПЦБК».

---

### Task 1: Образ рабочего места

**Files:**
- Create: `student/Dockerfile`, `student/pcbk-entrypoint`, `student/pcbk-health`,
  `student/config/opencode.json`, `student/config/.gitignore` (0 байт),
  `student/config/tools/{bash,edit,write,apply_patch}.ts`,
  `tests/integration/workplace.py`
- Modify: `tests/integration/conftest.py` (фикстура `student_image`;
  `ONESHOT_LABEL` импортируется из `workplace.py`)
- Test: `tests/integration/test_student_image.py`

**Interfaces:**
- Consumes: суммы и версии из фактов §6; правило закрепления образов Д1;
  `ONESHOT_LABEL` из кода Д1.
- Produces:
  - образ `pcbk-reserve/student:d2`: `USER 10001:10001`, `WORKDIR /work`,
    `EXPOSE 4096`, `ENTRYPOINT ["/usr/local/bin/pcbk-entrypoint"]`,
    `HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 CMD ["/usr/local/bin/pcbk-health"]`
  - раскладка: `/usr/local/bin/{opencode,rg,pcbk-entrypoint,pcbk-health}`
    (root, 0755); `/etc/pcbk-opencode/opencode/` = `student/config/` (root,
    0644), плюс пустой `agents/` (0755) — точка монтирования; `HOME=/home/pcbk`
    (root, 0755); `/var/lib/opencode` и `/work` — владелец 10001, 0700;
    `/run/secrets` (0755)
  - окружение образа — ровно `WORKPLACE_ENV` (ниже), других переменных нет
  - точка входа и проверка здоровья — код ниже; договорённости: код 78 и
    сообщение «нет пароля» при пустом значении; `pcbk-health` печатает одну
    строку — код ответа `GET /agent` или `000`, выходит с 0 только при 200,
    stderr пуст, укладывается в 4 с
  - в месте ожидаются (монтирует задача 3): `/run/secrets/opencode-pw`,
    `/run/secrets/llm-token`, агенты `:ro`, тома `/var/lib/opencode` и
    `/work`, tmpfs `/tmp` с `exec`
  - `tests/integration/workplace.py`: `IMAGE = "pcbk-reserve/student:d2"`,
    `STUB_PREFIX = "Отключено на учебном стенде"`, `IMAGE_ENV`,
    `WORKPLACE_ENV = IMAGE_ENV | {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}`,
    `MOUNTS` (пять путей назначения — задача 3), `ONESHOT_LABEL = "pcbk-test.oneshot"`
  - фикстура `student_image` (на сессию): `docker build -t pcbk-reserve/student:d2 student/`
    (контекст — `student/`), возвращает имя образа

```python
# tests/integration/workplace.py — IMAGE_ENV (значения точные)
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
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_student_image.py
from workplace import IMAGE, ONESHOT_LABEL, STUB_PREFIX, WORKPLACE_ENV

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "student" / "config"
SEC = 10 ** 9

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
    assert dict(item.split("=", 1) for item in cfg["Env"]) == WORKPLACE_ENV     # ровно, со значениями
    assert (cfg["User"], cfg["WorkingDir"], cfg["Entrypoint"]) == \
           ("10001:10001", "/work", ["/usr/local/bin/pcbk-entrypoint"])
    hc = cfg["Healthcheck"]
    assert hc["Test"] == ["CMD", "/usr/local/bin/pcbk-health"]
    assert (hc["Interval"], hc["Timeout"], hc["StartPeriod"], hc["StartInterval"], hc["Retries"]) == \
           (30 * SEC, 5 * SEC, 20 * SEC, 2 * SEC, 3)
    out = docker("run", "--rm", "--network", "none", "--entrypoint", "bash", IMAGE,
                 "-c", "opencode --version; rg --version").stdout.splitlines()
    assert out[0] == "1.18.33" and out[1].startswith("ripgrep 15.1.0")

@pytest.mark.parametrize("case", ["missing", "empty", "newline", "unreadable"])
def test_entrypoint_refuses_without_password(student_image, tmp_path, case):   # Review Focus 1
    mount = []
    if case != "missing":
        pw = tmp_path / "pw"
        pw.write_text({"empty": "", "newline": "\n", "unreadable": "not-for-uid-10001"}[case])
        pw.chmod(0o600 if case == "unreadable" else 0o444)   # 0600 чужого uid — как umask 077 без chmod
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

@pytest.mark.parametrize("broken", [False, True])
def test_health_needs_working_instance(student_image, tmp_path, broken):         # Review Focus 3
    for name, value in (("pw", "probe-secret"), ("llm", "probe-token")):
        (tmp_path / name).write_text(value)
        (tmp_path / name).chmod(0o444)
    args = ["--read-only", "--network", "none", "--label", ONESHOT_LABEL,
            "--tmpfs", "/tmp:exec,mode=1777", "--tmpfs", "/var/lib/opencode:exec,uid=10001,gid=10001",
            "--tmpfs", "/work:uid=10001,gid=10001",
            "-v", f"{tmp_path / 'pw'}:/run/secrets/opencode-pw:ro",
            "-v", f"{tmp_path / 'llm'}:/run/secrets/llm-token:ro"]
    if broken:                          # конфигурация без .gitignore — как образ из неполного клона
        cfg = tmp_path / "config"
        shutil.copytree(CONFIG, cfg, ignore=shutil.ignore_patterns(".gitignore"))
        for p in [cfg, *cfg.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        args += ["-v", f"{cfg}:/etc/pcbk-opencode/opencode:ro"]
    cid = docker("run", "-d", *args, IMAGE).stdout.strip()
    try:
        codes, deadline = [], time.monotonic() + 30
        while codes[-1:] != ["200"] and time.monotonic() < deadline:
            codes.append(docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip())
            time.sleep(1)
        assert all(re.fullmatch(r"\d{3}", c) for c in codes)
        assert (codes[-1] == "200") is (not broken)     # /global/health здесь дал бы 200 и сломанному
    finally:
        docker("rm", "-f", cid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py`
Expected: FAIL — нет `workplace.py` и `student/config/opencode.json`, сборка образа падает

- [ ] **Step 3: Implement образ, точку входа, проверку здоровья и конфигурацию**

`student/Dockerfile` — две стадии на одном базовом образе, контекст сборки —
`student/`. Строки загрузки копируются как есть:

```dockerfile
FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02 AS fetch
ADD --checksum=sha256:e546123213ae47909a4268692aa4b94950d011afe9cac9938753a2194f1c16d5 \
    https://github.com/anomalyco/opencode/releases/download/v1.18.33/opencode-linux-x64.tar.gz /dl/
ADD --checksum=sha256:1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599 \
    https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz /dl/
```

Стадия `fetch` распаковывает оба архива. Итоговая стадия строится на том же
`FROM`, без apt:
- `groupadd -g 10001 pcbk`, `useradd -u 10001 -g 10001 -M -d /home/pcbk -s /usr/sbin/nologin pcbk`;
- каталоги и права — по разделу «раскладка»;
- `COPY --from=fetch` двух бинарников;
- `COPY pcbk-entrypoint pcbk-health /usr/local/bin/`;
- `COPY config/ /etc/pcbk-opencode/opencode/` (вместе с `.gitignore`);
- `ENV` (ровно `IMAGE_ENV`), `USER`, `WORKDIR`, `HEALTHCHECK`, `ENTRYPOINT`.

Скрипты — на bash, без `set -x` и без временных файлов: корень только для
чтения, а here-string создаёт временный файл.

```bash
#!/bin/bash
# student/pcbk-entrypoint — пароль сервера только из файла и только процессу OpenCode
set -eu
pw=""
{ IFS= read -r pw < /run/secrets/opencode-pw; } 2>/dev/null || true   # нет файла, нет доступа, EOF без \n
if [ -z "$pw" ]; then
  echo "pcbk: нет пароля сервера OpenCode в /run/secrets/opencode-pw — не запускаюсь" >&2
  exit 78
fi
export OPENCODE_SERVER_PASSWORD="$pw"
exec opencode serve --hostname 0.0.0.0 --port 4096
```

```bash
#!/bin/bash
# student/pcbk-health — GET /agent (ручка экземпляра); печатает только код, вывод видит сторож
pw=""
{ IFS= read -r pw < /run/secrets/opencode-pw; } 2>/dev/null || true
auth=$(printf '%s:%s' "$OPENCODE_SERVER_USERNAME" "$pw" | base64 -w0)
code=000
if { exec 3<>/dev/tcp/127.0.0.1/4096; } 2>/dev/null; then   # ошибку соединения — не в вывод
  printf 'GET /agent HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Basic %s\r\nConnection: close\r\n\r\n' "$auth" >&3
  if read -r -t 3 _ status _ <&3 && [[ $status =~ ^[0-9]{3}$ ]]; then code=$status; fi
fi
echo "$code"
[ "$code" = 200 ]
```

Заглушка — литерал описания прямо в объекте:

```ts
export default {
  description: "Отключено на учебном стенде: запуск команд недоступен.",
  args: {},
  async execute() {
    return "Отключено на учебном стенде: запуск команд недоступен."
  },
}
```

Так выглядит `bash.ts`; у `edit`, `write`, `apply_patch` в обоих местах
«Отключено на учебном стенде: правка файлов недоступна.».
`student/config/.gitignore` — пустой файл, строку `.gitignore` в него не
добавлять: иначе git не возьмёт файл, и образ из чистого клона даст
`GET /agent` → 500.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py`
Expected: PASS (9 тестов)

- [ ] **Step 5: Commit**

```bash
git add student/ tests/integration/workplace.py tests/integration/conftest.py tests/integration/test_student_image.py
git commit -m "Образ рабочего места: OpenCode 1.18.33 по sha256, заглушки, пароль файлом, проверка здоровья по ручке экземпляра"
```

---

### Task 2: Сторож — строки здоровья, места под наблюдением

**Files:**
- Modify: `watchdog/pcbk_watchdog/checks.py`, `watchdog/components.json`,
  `watchdog/tests/helpers.py`
- Test: `watchdog/tests/test_checks.py` (+3 теста),
  `watchdog/tests/test_main.py` (`test_components_file_d1` → `test_components_file_d2`;
  `test_components_file_d1_details` остаётся)

**Interfaces:**
- Consumes: `check_container`, `Check`, `insp`, `T0`, `stu` из
  `test_checks.py`, `load_components`, `Settings` — без изменений (файл
  `COMPONENTS_PATH=/app/components.json` вшит в образ сторожа), `run_checks`
  передаёт `sleeping_ok` из `components.json`.
- Produces:
  - в `checks.py`: `HEALTH_SILENCE = timedelta(minutes=2)`,
    `HEALTH_SILENT_DETAIL = "проверка здоровья молчит"`,
    `UNHEALTHY_DETAIL = "OpenCode не отвечает"`
  - `insp(..., health: str | None = None, last_end: datetime | None = T0 - timedelta(seconds=10))`.
    При заданном `health` добавляет
    `State["Health"] = {"Status": health, "FailingStreak": 3 if health == "unhealthy" else 0, "Log": log}`,
    где `log` — одна запись `{"Start": …, "End": last_end.isoformat(), "ExitCode": 0 if health == "healthy" else 1, "Output": "200\n"}`;
    при `last_end=None` журнал пуст
  - `components.json`: `student-01…10` — `{"id": "student-NN", "title": "Рабочее место NN", "kind": "container", "container": "pcbk-student-NN", "sleeping_ok": true}`,
    на тех же местах списка; `core`, `historian`, `llm` остаются `absent`

Таблица `check_container` Д1 с двумя новыми строками (первая подходящая сверху
вниз):

| Состояние Docker | Итог |
|---|---|
| нет контейнера (`None`) | `fail` «нет контейнера» |
| `Paused` | `fail` «приостановлен» |
| `Running` или `Restarting`, `RestartCount` ≥ 3 и `StartedAt` моложе `RECENT_RESTART` | `fail` «падает в цикле (перезапуски подряд)» |
| `Restarting` (Docker ставит и `Running`) | `warn` «перезапускается» |
| **`Running`, есть `State.Health`, позднейшее из `End` записей `Health.Log` и `StartedAt` старше `HEALTH_SILENCE` (Д2)** | **`fail` «проверка здоровья молчит»** |
| **`Running` и `State.Health.Status == "unhealthy"` (Д2)** | **`fail` «OpenCode не отвечает»** |
| `Running`, `RestartCount` > 0 и `StartedAt` моложе `RECENT_RESTART` | `warn` «перезапущен после сбоя в ЧЧ:ММ UTC±ЧЧ:ММ (N с последнего запуска)» |
| `Running` | `ok` «работает» (при `RestartCount` > 0 — «работает, сбоев с последнего запуска: N») |
| остановлен, `OOMKilled` | `fail` «убит по памяти» |
| остановлен, `State.Error` не пуст | `fail` «не запускается: <первые 80 знаков ошибки>» |
| остановлен, `sleeping_ok`, код ∈ {0, 137, 143} | `ok` «спит» |
| остановлен, иначе | `fail` «остановлен, код N» |

Две новые строки встают в ветку работающего контейнера `check_container` Д1
сразу после `Restarting`:

```python
        health = st.get("Health")
        if health:                                   # Д2; у остановленного Health прошлого запуска не смотрим
            ends = [datetime.fromisoformat(e["End"]) for e in health.get("Log") or []]
            if now - max([started, *ends]) > HEALTH_SILENCE:
                return result("fail", HEALTH_SILENT_DETAIL)
            if health.get("Status") == "unhealthy":
                return result("fail", UNHEALTHY_DETAIL)
```

`starting` и `healthy` со свежим журналом дают то же, что отсутствие проверки
здоровья. `HEALTH_SILENCE` = интервал 30 с + срок 5 с с запасом. Монитор
здоровья Docker ждёт результат `exec` без срока, и без этой строки
заклинившая песочница навсегда осталась бы «работает». Тексты строк
постоянные — журнал пишет одно событие. Из контейнеров, которые сторож
проверяет через `check_container`, `HEALTHCHECK` есть только у мест.

- [ ] **Step 1: Write the failing tests**

```python
# test_checks.py
def test_health_unhealthy_is_fail():
    r = stu(insp(running=True, health="unhealthy"))
    assert (r.state, r.detail) == ("fail", "OpenCode не отвечает")
    fresh = stu(insp(running=True, restarts=1, started=T0 - timedelta(minutes=3), health="unhealthy"))
    assert (fresh.state, fresh.detail) == ("fail", "OpenCode не отвечает")    # сильнее «перезапущен после сбоя»

def test_health_row_only_for_running_container():                            # Review Focus 3
    starting = stu(insp(running=True, health="starting", last_end=None, started=T0 - timedelta(seconds=30)))
    assert (starting.state, starting.detail) == ("ok", "работает")
    assert stu(insp(running=True, health="healthy")).detail == "работает"
    stale = stu(insp(code=143, health="unhealthy", last_end=T0 - timedelta(hours=2)))
    assert (stale.state, stale.detail) == ("ok", "спит")
    assert stu(insp(running=True, paused=True, health="unhealthy")).detail == "приостановлен"
    loop = stu(insp(running=True, restarts=3, started=T0 - timedelta(seconds=20), health="unhealthy"))
    assert loop.state == "fail" and "падает в цикле" in loop.detail

def test_health_silence_is_fail():                                            # Review Focus 3
    silent = stu(insp(running=True, health="healthy", last_end=T0 - timedelta(minutes=5)))
    assert (silent.state, silent.detail) == ("fail", "проверка здоровья молчит")
    empty = stu(insp(running=True, health="starting", last_end=None))          # запуск час назад, журнала нет
    assert (empty.state, empty.detail) == ("fail", "проверка здоровья молчит")
    assert stu(insp(running=True, health="healthy", last_end=T0 - timedelta(seconds=30))).state == "ok"
    restarted = stu(insp(running=True, health="healthy", last_end=T0 - timedelta(minutes=5),
                         started=T0 - timedelta(seconds=20)))                   # старый журнал, свежий старт
    assert restarted.state == "ok"

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

- [ ] **Step 3: Implement две строки таблицы, константы, `insp(health=, last_end=)` и `components.json` по интерфейсам выше**

- [ ] **Step 4: Run all watchdog tests**

Run: `cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs`
Expected: PASS. Интеграционные тесты Д1 до задачи 3 ждут мест, которых ещё
нет; весь набор прогоняется в задаче 3.

- [ ] **Step 5: Commit**

```bash
git add watchdog/
git commit -m "Сторож: «OpenCode не отвечает» и «проверка здоровья молчит», рабочие места под наблюдением"
```

---

### Task 3: Десять мест в компоновке и тесты

**Files:**
- Modify: `compose.yaml` (якорь `x-student`, службы `student-01…10`, сети
  `pcbk-stu-01…10`, внешние тома, секреты; у сторожа `image: pcbk-reserve/watchdog:d2`),
  `compose.test.yaml` (места — `runtime: runc`), `tests/integration/conftest.py`
  (профиль, тома мест и методы `stack` ниже; фикстура `stack(tmp_path_factory, student_image)`;
  у `http_as` `--max-time 20`, потому что `stop` ждёт `stop_grace_period`),
  `tests/integration/workplace.py` (`MOUNTS`),
  `tests/integration/test_edge.py` (`IMAGES`: сторож `:d2`,
  `"pcbk-student-01": "pcbk-reserve/student:d2"`;
  `DECLARED_ENV["pcbk-student-01"] = set()`),
  `tests/integration/test_socket_proxy.py`: в `test_status_json_overall_ok`
  `absent >= {"core"}`, а `student-01` не `absent`; в
  `test_student_range_upper_bound_passes` — `try/finally: stack.stop("pcbk-student-10")`,
  комментарий «место есть — Docker его запускает, тест возвращает в сон».
  Без этой правки место 10 работает до конца сессии, и
  `test_every_workplace_is_watched` падает на правильной реализации.
  `.gitignore` — `*.pw`, `*.llm-token`.
- Test: `tests/integration/test_students.py`

**Interfaces:**
- Consumes: образ и его договорённости, фикстура `student_image`,
  `workplace.py` — задача 1; `components.json` — задача 2. Из кода Д1 —
  класс `Stack`: `compose(*args, files=…)`, `config(files=…) -> dict`,
  `exec(name, *cmd) -> str` (argv, при ненулевом коде — исключение),
  `_docker(*args, check=…)`, `inspect`, `network`, `containers`, `env_names`,
  `image_env_names`, `host_bridge_addresses`, `https`, `wait_status`,
  `wait_ready`, `start`, `stop`, `http_from_watchdog`, `http_as`,
  `refuse_foreign`, `up`, `down`, `test_env`, `locked_image`; `DECLARED_ENV`,
  `IMAGES`, `RO`, `CTL`; таблица сетей; `curlimages/curl:8.16.0 # test` из
  `deploy/images.lock`.
- Produces:
  - службы, контейнеры, сети, тома и секреты с именами из карты файлов;
    фрагмент ниже — образец для места NN (`N` без ведущего нуля)
  - `test.env` фикстуры — как в Д1 (`STU_NET=172.31`, `SECRETS_DIR`,
    `AGENTS_DIR` во временном каталоге). Теперь фикстура создаёт и заполняет
    эти каталоги до `up()`: секреты — `secrets.token_hex(24)` без перевода
    строки, каталог `0700`, файлы `0444`; агенты — корень `0700`, каталоги
    `student-NN` — `0755`, как на сервере.
  - `stack` зависит от `student_image`: образ пересобирается в каждом прогоне
    до `up()` и `create`.
  - `Stack.compose` добавляет `--profile students` в каждый вызов.
  - Тома мест создаёт `up()` с меткой `TEST_VOLUME_LABEL = "pcbk-test.volume"`,
    а `down()` удаляет их по метке после `compose down`. `refuse_foreign()`
    отказывается запускаться, если том `pcbk-student-NN-*` уже есть без этой
    метки.
  - `wait_ready` для `pcbk-student-*`: готовность —
    `State.Health.Status == "healthy"` (до 60 с), отказ — `State.Error` или
    выход контейнера. Места в `self.ready` не входят, и `up()` их не ждёт.
  - атрибуты `stack`: `stu_net: str` (`"172.31"`), `secrets_dir: Path`,
    `agents_root: Path`
  - новые методы `stack`:
    - `password(n: int) -> str`, `llm_token(n: int) -> str` — из `secrets_dir`
    - `agents_dir(n: int) -> Path` — `agents_root / f"student-{n:02d}"`
    - `sh(name: str, script: str) -> tuple[int, str, str]` — `docker exec <name> bash -c <script>`, без исключения при ненулевом коде
    - `opencode_pid(name: str) -> int` — PID, у которого `readlink /proc/PID/exe == /usr/local/bin/opencode`
    - `oc(n: int, method: str, path: str, pw_of: int | None, timeout: float = 10.0) -> tuple[int, str]` — код ниже
    - `wait_oc(n: int, timeout: float = 30.0) -> None` — `GET /global/health`
      со своим паролем, по 2 с на попытку, до 200
    - `probe(network: str, addr: str, port: int) -> int` — способ Д1, 0 или 1,
      контейнер с `--label ONESHOT_LABEL`
    - `wait_healthy(name: str, timeout: float = 60.0) -> None`
    - `body_from_watchdog(url: str) -> str` — как `http_from_watchdog`, но возвращает тело
    - `prod_config() -> dict` — `config(files=("compose.yaml",))` с профилем,
      кэшируется
    - `host_lan() -> str` — первый адрес `hostname -I`
  - журнал контейнера тесты читают через `stack._docker("logs", name)`. Метод
    `logs()` объявляет план Д3а, Д2 его не заводит.
  - `MOUNTS = {"/run/secrets/opencode-pw", "/run/secrets/llm-token", "/etc/pcbk-opencode/opencode/agents", "/var/lib/opencode", "/work"}`

```python
    def up(self) -> None:
        cfg = self.config()                          # compose() уже передаёт --profile students
        places = sorted(n for n, s in cfg["services"].items() if "students" in (s.get("profiles") or []))
        for name in places:                          # тома мест внешние: создаёт фикстура, на сервере — выкладка
            for kind in ("state", "work"):
                self._docker("volume", "create", "--label", TEST_VOLUME_LABEL, f"pcbk-{name}-{kind}")
        self.compose("create", "--no-build", *places)
        self.compose("up", "-d", "--build", *(n for n in cfg["services"] if n not in places))
        ...                                          # дальше без изменений Д1: готовность, /status, свежий снимок

    def oc(self, n: int, method: str, path: str, pw_of: int | None, timeout: float = 10.0) -> tuple[int, str]:
        """Запрос к OpenCode места n из его сети; пароль места pw_of — файлом, не в argv и не в окружении."""
        auth = [] if pw_of is None else ["--variable", "PW@/pw", "--expand-user", "opencode:{{PW}}"]
        mount = [] if pw_of is None else ["-v", f"{self.secrets_dir / f'student-{pw_of:02d}.pw'}:/pw:ro"]
        r = self._docker("run", "--rm", "--pull", "never", "--network", f"pcbk-stu-{n:02d}",
                         "--label", ONESHOT_LABEL, "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", *mount, locked_image("curlimages/curl"),
                         "curl", "-s", "--max-time", str(timeout), "-X", method, *auth,
                         "-w", "\n%{http_code}", f"http://{self.stu_net}.{n}.3:4096{path}", check=False)
        body, _, code = r.stdout.rpartition("\n")
        return int(code or 0), body
```

```yaml
x-student: &student                      # общее для десяти мест
  image: pcbk-reserve/student:d2
  pull_policy: never
  runtime: runsc
  profiles: [students]
  user: "10001:10001"
  init: true
  ipc: private
  read_only: true
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  tmpfs: ["/tmp:exec,mode=1777,size=256m"]
  mem_limit: 1g
  memswap_limit: 1g                      # swap = 0
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
      - {type: bind, source: "${AGENTS_DIR:?}/student-NN", target: /etc/pcbk-opencode/opencode/agents,
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

volumes:                                 # данные студента: Compose их не создаёт и не удаляет
  pcbk-student-NN-state: {name: pcbk-student-NN-state, external: true}
  pcbk-student-NN-work: {name: pcbk-student-NN-work, external: true}

secrets:
  student-NN-pw: {file: "${SECRETS_DIR:?}/student-NN.pw"}
  student-NN-llm: {file: "${SECRETS_DIR:?}/student-NN.llm-token"}
```

Секции `build` у мест нет: образ один на десять служб и собирается фикстурой
`student_image` (на сервере — `docker save`/`load`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_students.py
from workplace import MOUNTS, STUB_PREFIX, WORKPLACE_ENV

ROOT = Path(__file__).resolve().parents[2]
RO = "http://pcbk-sp-ro:2375/v1.44"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
WORKPLACES = {f"pcbk-student-{n:02d}" for n in range(1, 11)}
HISTORIAN_ADDR = os.environ.get("HISTORIAN_ADDR", "192.0.2.10")   # адрес заказчика — только из окружения
RUNTIME_ENV = {"HOSTNAME", "PWD", "SHLVL", "_", "OLDPWD"}         # добавляют Docker и bash при exec
PROBE_AGENT = "---\ndescription: Проба видимости агента\nmode: primary\n---\nТы — проба.\n"
BYPASS_TOOL = 'export default { description: "BYPASS real shell", args: {}, async execute() { return "x" } }'
BYPASS_CONFIG = '{"agent": {"bypass": {"description": "обход", "mode": "primary"}}}'

@pytest.fixture(scope="module")
def asleep(stack):                       # модуль не зависит от порядка файлов: работающие места — в сон
    for name in sorted(WORKPLACES):
        if stack.inspect(name)["State"]["Running"]:
            stack.stop(name)

@pytest.fixture(scope="module")
def running(stack):                                                # места 01 и 02 работают до конца модуля
    for name in ("pcbk-student-01", "pcbk-student-02"):
        stack.start(name)
    yield
    for name in ("pcbk-student-01", "pcbk-student-02"):
        stack.stop(name)

def student_rows(data):
    return {c["component"]: (c["state"], c["detail"]) for c in data["checks"] if c["component"].startswith("student-")}

def norm(service: dict, n: int) -> dict:
    """Блок места с номером, заменённым меткой: блоки 02–10 обязаны совпасть с блоком 01."""
    text = json.dumps(service, sort_keys=True)
    for old, new in ((f"student-{n:02d}", "student-NN"), (f"stu-{n:02d}", "stu-NN"), (f".{n}.", ".N.")):
        text = text.replace(old, new)
    return json.loads(text)

def test_every_workplace_is_watched(stack, asleep):                # Review Focus 4
    in_compose = {s["container_name"] for s in stack.prod_config()["services"].values()
                  if s.get("labels", {}).get("pcbk.role") == "student"}
    comps = json.loads((ROOT / "watchdog" / "components.json").read_text())
    watched = {c["container"] for c in comps if c["kind"] == "container" and c["sleeping_ok"]}
    assert in_compose == watched == WORKPLACES
    data = stack.wait_status(lambda d: len(student_rows(d)) == 10 and
                             all(s == "ok" for s, _ in student_rows(d).values()), timeout=30)
    assert set(student_rows(data).values()) == {("ok", "спит")}

def test_each_workplace_uses_own_objects(stack):                   # Review Focus 2
    cfg, stu = stack.prod_config(), stack.stu_net
    for n in range(1, 11):
        nn = f"{n:02d}"
        s = cfg["services"][f"student-{nn}"]
        assert s["container_name"] == f"pcbk-student-{nn}"
        assert sorted((x["source"], x["target"]) for x in s["secrets"]) == \
               [(f"student-{nn}-llm", "llm-token"), (f"student-{nn}-pw", "opencode-pw")]
        assert (cfg["secrets"][f"student-{nn}-pw"]["file"], cfg["secrets"][f"student-{nn}-llm"]["file"]) == \
               (str(stack.secrets_dir / f"student-{nn}.pw"), str(stack.secrets_dir / f"student-{nn}.llm-token"))
        assert {v["target"]: v["source"] for v in s["volumes"]} == {
            "/var/lib/opencode": f"pcbk-student-{nn}-state", "/work": f"pcbk-student-{nn}-work",
            "/etc/pcbk-opencode/opencode/agents": str(stack.agents_dir(n))}
        assert [(cfg["volumes"][f"pcbk-student-{nn}-{k}"]["name"], cfg["volumes"][f"pcbk-student-{nn}-{k}"]["external"])
                for k in ("state", "work")] == [(f"pcbk-student-{nn}-state", True), (f"pcbk-student-{nn}-work", True)]
        assert s["networks"][f"pcbk-stu-{nn}"]["ipv4_address"] == f"{stu}.{n}.3"
        assert cfg["networks"][f"pcbk-stu-{nn}"]["ipam"]["config"][0]["subnet"] == f"{stu}.{n}.0/28"
        c = stack.inspect(f"pcbk-student-{nn}")                    # созданный контейнер — как на сервере
        assert [tuple(re.split(r"[:=]", h, maxsplit=1)) for h in c["HostConfig"]["ExtraHosts"]] == \
               [("core", f"{stu}.{n}.2")]
        assert len(c["Mounts"]) == 5 and all(f"student-{nn}" in m["Source"] for m in c["Mounts"])

def test_sp_ctl_really_starts_and_stops_student(stack):
    url = CTL + "/containers/pcbk-student-03"
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/start") == 204
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/start") == 304
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/stop") == 204
    assert stack.inspect("pcbk-student-03")["State"]["Running"] is False
    stack.wait_status(lambda d: student_rows(d).get("student-03") == ("ok", "спит"), timeout=30)

def test_student_hardening(stack, running):                        # Review Focus 5
    c = stack.inspect("pcbk-student-01")
    hc = c["HostConfig"]
    assert (hc["ReadonlyRootfs"], hc["CapDrop"], hc["CapAdd"], hc["Privileged"]) == (True, ["ALL"], None, False)
    assert "no-new-privileges:true" in hc["SecurityOpt"]
    assert (hc["Memory"], hc["MemorySwap"], hc["PidsLimit"], hc["NanoCpus"], hc["Init"]) == \
           (1024 ** 3, 1024 ** 3, 512, 10 ** 9, True)
    assert (hc["PidMode"], hc["IpcMode"], hc["Devices"] or [], hc["PortBindings"] or {}) == ("", "private", [], {})
    assert hc["RestartPolicy"]["Name"] == "unless-stopped"
    assert {m["Destination"] for m in c["Mounts"]} == MOUNTS and set(hc["Tmpfs"]) == {"/tmp"}
    assert stack.sh("pcbk-student-01", "ls -A /run/secrets")[1].split() == ["llm-token", "opencode-pw"]
    pid = stack.opencode_pid("pcbk-student-01")
    status = dict(line.split(":", 1) for line in
                  stack.sh("pcbk-student-01", f"cat /proc/{pid}/status")[1].splitlines() if ":" in line)
    assert {k: status[k].split() for k in ("CapEff", "CapBnd", "NoNewPrivs")} == \
           {"CapEff": ["0000000000000000"], "CapBnd": ["0000000000000000"], "NoNewPrivs": ["1"]}
    assert status["Uid"].split() == ["10001"] * 4
    assert stack.exec("pcbk-student-01", "cat", "/proc/1/comm") == "docker-init"
    for name in sorted(WORKPLACES):                                # окружение — ровно образ, со значениями
        assert dict(e.split("=", 1) for e in stack.inspect(name)["Config"]["Env"]) == WORKPLACE_ENV, name
    services = stack.prod_config()["services"]                    # без compose.test.yaml
    s = services["student-01"]
    assert (s["runtime"], s["read_only"], s["cap_drop"], s["init"], s["profiles"], s["ipc"]) == \
           ("runsc", True, ["ALL"], True, ["students"], "private")
    assert "no-new-privileges:true" in s["security_opt"] and not s.get("ports") and not s.get("environment")
    assert not {"command", "entrypoint", "privileged", "cap_add", "devices", "pid"} & set(s)
    assert (int(s["mem_limit"]), int(s["memswap_limit"]), s["pids_limit"], float(s["cpus"])) == \
           (1024 ** 3, 1024 ** 3, 512, 1.0)
    agents = next(v for v in s["volumes"] if v["target"] == "/etc/pcbk-opencode/opencode/agents")
    assert agents["read_only"] is True and not agents.get("bind", {}).get("create_host_path")
    for n in range(2, 11):                                         # блоки 02–10 — копия 01 с точностью до номера
        assert norm(services[f"student-{n:02d}"], n) == norm(s, 1), n

def test_opencode_requires_password(stack, running):
    stack.wait_oc(1, timeout=30)                                   # первые секунды запросы висят
    assert stack.oc(1, "GET", "/global/health", None)[0] == 401
    assert stack.oc(1, "GET", "/global/health", 1)[0] == 200
    assert stack.oc(1, "GET", "/global/health", 2)[0] == 401      # Review Focus 2

def last_descriptions(stack, model):
    code, body = stack.oc(1, "GET", f"/experimental/tool?provider=pcbk&model={model}", 1)
    assert code == 200
    # id повторяется: сначала встроенный, потом заглушка; модель получает последнюю запись
    return {t["id"]: t["description"] for t in json.loads(body)}

def test_stubs_replace_builtin_tools(stack, running):
    stub = last_descriptions(stack, "stub")
    assert all(stub[t].startswith(STUB_PREFIX) for t in ("bash", "edit", "write"))
    assert not {"apply_patch", "multiedit", "patch"} & set(stub)
    gpt = last_descriptions(stack, "gpt-5")                        # apply_patch есть только у gpt-*
    assert gpt["apply_patch"].startswith(STUB_PREFIX) and gpt["bash"].startswith(STUB_PREFIX)
    assert not {"edit", "write", "multiedit", "patch"} & set(gpt)

def agent_names(stack):
    code, body = stack.oc(1, "GET", "/agent", 1)
    assert code == 200
    return {a["name"] for a in json.loads(body)}

def test_project_dir_cannot_override_stubs(stack, running):        # Review Focus 5
    try:
        rc = stack.sh("pcbk-student-01", "mkdir -p /work/.opencode/tools && "
                      f"cat > /work/.opencode/tools/bash.ts <<'EOF'\n{BYPASS_TOOL}\nEOF\n"
                      f"cat > /work/opencode.json <<'EOF'\n{BYPASS_CONFIG}\nEOF")[0]
        assert rc == 0                                             # /work пишется — обход был бы возможен
        assert stack.oc(1, "POST", "/instance/dispose", 1)[0] == 200
        descs = last_descriptions(stack, "stub")
        assert descs["bash"].startswith(STUB_PREFIX) and "BYPASS real shell" not in descs.values()
        assert "bypass" not in agent_names(stack)
    finally:
        stack.sh("pcbk-student-01", "rm -rf /work/.opencode /work/opencode.json")
        stack.oc(1, "POST", "/instance/dispose", 1)
    assert stack.sh("pcbk-student-01", "ls -A /work")[1].strip() == ""

def test_agent_file_visible_after_dispose(stack, running):
    assert "probe" not in agent_names(stack)                       # экземпляр создан до записи файла
    probe = stack.agents_dir(1) / "probe.md"
    try:
        probe.write_text(PROBE_AGENT)
        probe.chmod(0o644)
        assert "probe" not in agent_names(stack)                   # без dispose не виден
        assert stack.oc(1, "POST", "/instance/dispose", 1)[0] == 200
        assert "probe" in agent_names(stack)
    finally:
        probe.unlink(missing_ok=True)
        stack.oc(1, "POST", "/instance/dispose", 1)

def test_readonly_where_it_matters(stack, running):
    for d in ("/home/pcbk", "/etc/pcbk-opencode/opencode", "/etc/pcbk-opencode/opencode/tools",
              "/etc/pcbk-opencode/opencode/agents", "/usr/local/bin", "/run/secrets"):
        assert stack.sh("pcbk-student-01", f"touch {d}/.pcbk-w")[0] != 0, f"{d} пишется"
    for d in ("/var/lib/opencode", "/work", "/tmp"):
        assert stack.sh("pcbk-student-01", f"touch {d}/.pcbk-w && rm {d}/.pcbk-w")[0] == 0, f"{d} не пишется"
    assert list(stack.agents_dir(1).iterdir()) == []

def test_no_route_anywhere(stack, running):
    stu = stack.stu_net
    nets = [f"pcbk-stu-{n:02d}" for n in range(1, 11)]
    assert stack.host_bridge_addresses(*nets) == []                # главное: у мостов нет адреса хоста
    for n, net in enumerate(nets, 1):
        info = stack.network(net)
        assert info["Internal"] is True
        assert info["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
        assert [(c["Subnet"], c["IPRange"]) for c in info["IPAM"]["Config"]] == \
               [(f"{stu}.{n}.0/28", f"{stu}.{n}.8/29")]
    eps = stack.inspect("pcbk-student-01")["NetworkSettings"]["Networks"]
    assert list(eps) == ["pcbk-stu-01"] and eps["pcbk-stu-01"]["IPAddress"] == f"{stu}.1.3"
    assert stack.sh("pcbk-student-01", "getent hosts core")[1].split()[0] == f"{stu}.1.2"
    assert "eth0" not in stack.sh("pcbk-student-01", "cat /proc/net/if_inet6")[1]   # у места нет IPv6
    assert stack.probe("pcbk-stu-01", f"{stu}.1.3", 4096) == 1    # положительный контроль: своё место
    assert stack.probe("pcbk-stu-02", f"{stu}.2.3", 4096) == 1    # место 02 живо — его 0 ниже не пустой
    gateway = stack.network("pcbk-stu-01")["IPAM"]["Config"][0].get("Gateway")
    targets = [(a, p) for a in sorted({f"{stu}.1.1", gateway} - {None}) for p in (22, 80, 443, 2375, 3389)]
    targets += [(stack.host_lan(), 22), (stack.host_lan(), 443), (HISTORIAN_ADDR, 1433),
                ("1.1.1.1", 443), (f"{stu}.2.3", 4096)]
    targets += [(ep["IPAddress"], port) for name, port in
                (("pcbk-sp-ctl", 2375), ("pcbk-sp-ro", 2375), ("pcbk-watchdog", 8090), ("pcbk-edge", 8443))
                for ep in stack.inspect(name)["NetworkSettings"]["Networks"].values()]
    assert [t for t in targets if stack.probe("pcbk-stu-01", *t) != 0] == []

def test_password_not_visible_to_watchdog(stack, running):
    stack.wait_healthy("pcbk-student-01", timeout=60)              # в Health.Log уже есть вывод проверки
    body = stack.body_from_watchdog(RO + "/containers/pcbk-student-01/json")
    log = json.loads(body)["State"]["Health"]["Log"]
    assert all(re.fullmatch(r"\d{3}\n?", e["Output"]) for e in log) and log[-1]["Output"].strip() == "200"
    r = stack._docker("logs", "pcbk-student-01")
    logs = r.stdout + r.stderr
    leaked = any(secret in text for secret in (stack.password(1), stack.llm_token(1)) for text in (body, logs))
    assert leaked is False and "OPENCODE_SERVER_PASSWORD" not in body   # значения не попадают в вывод pytest

def test_env_holds_only_own_secret(stack, running):
    pid = stack.opencode_pid("pcbk-student-01")
    raw = stack.sh("pcbk-student-01", f"cat /proc/{pid}/environ")[1]
    pairs = [item.split("=", 1) for item in raw.split("\0") if item]
    extra = {k for k, _ in pairs} - stack.env_names("pcbk-student-01") - RUNTIME_ENV
    assert extra == {"OPENCODE_SERVER_PASSWORD"}, sorted(extra)      # печатаются только имена
    values = {v for _, v in pairs}
    own = stack.password(1) in values
    foreign = bool(values & ({stack.password(n) for n in range(2, 11)} | {stack.llm_token(1)}))
    assert (own, foreign) == (True, False)

def test_hung_workplace_row(stack, running):                       # Review Focus 3; последний в модуле
    row = lambda d: student_rows(d).get("student-01")
    stack.wait_status(lambda d: row(d) == ("ok", "работает"), timeout=60)
    pid = stack.opencode_pid("pcbk-student-01")
    stack.sh("pcbk-student-01", f"kill -STOP {pid}")
    try:                                                           # HEALTHCHECK → Docker → sp-ro → сторож → /status.json
        stack.wait_status(lambda d: row(d) == ("fail", "OpenCode не отвечает"), timeout=150)
    finally:
        stack.sh("pcbk-student-01", f"kill -CONT {pid}")
    stack.wait_status(lambda d: row(d) == ("ok", "работает"), timeout=60)
```

`test_health_unhealthy_is_fail` из черновика — модульный тест сторожа, он в
задаче 2.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_students.py`
Expected: FAIL — нет служб `student-NN`, у `stack` нет `prod_config`, `sh`, `oc`, `probe`

- [ ] **Step 3: Implement десять мест, `compose.test.yaml`, профиль, тома и методы `stack`, правки тестов Д1 и `.gitignore` по интерфейсам выше**

В `compose.test.yaml` у десяти мест — только `runtime: runc`, через якорь; всё
остальное берётся из производственного файла.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --python 3.12 --with pytest pytest -q tests/integration/test_students.py`
Expected: PASS

- [ ] **Step 5: Run the whole local suite**

Run: `(cd watchdog && uv run --python 3.12 --with pytest pytest -q && node --test tests/js/*.test.mjs) && uv run --python 3.12 --with pytest pytest -q tests/integration && docker network ls --filter name=pcbk- -q | wc -l && docker volume ls --filter name=pcbk-student- -q | wc -l && docker ps -aq --filter label=pcbk-test.oneshot | wc -l`
Expected: PASS (в том числе `test_every_workplace_is_watched` после
`test_socket_proxy.py`); после прогона сетей `pcbk-`, томов `pcbk-student-` и
одноразовых контейнеров — 0

- [ ] **Step 6: Commit**

```bash
git add compose.yaml compose.test.yaml tests/integration/ .gitignore
git commit -m "Десять рабочих мест под gVisor: своя изолированная сеть, свои секреты, внешние тома и агенты, профиль students"
```

---

### Task 4: Выкладка на сервер

Шаги — команды и вывод, который значит «прошло». Итоги — в `docs/checks/D2.md`.
`sudo` не нужен.

**Files:**
- Modify: `deploy/README.md`:
  - раздел «Рабочие места»: порождение секретов (`umask 077`, каталог `0700`,
    файлы `0444`, не перезаписывать); каталоги агентов (корень `0700`, места
    `0755`); тома — `docker volume create` до `create`; `create` вместо `up`;
    ручной запуск и остановка места.
  - раздел «Откат» переписывается целиком:
    1. на сервере `docker compose down` не выполняется: он снимает все десять
       мест и их сети;
    2. `down -v` запрещён: он удаляет том журнала сторожа. Журнал удаляется
       только явно: `docker volume rm pcbk-reserve_pcbk-watchdog-journal`;
    3. службы снимаются только `docker compose rm -sf <имена служб>`, а Д1 —
       `docker compose stop` / `rm -sf edge watchdog sp-ro sp-ctl`;
    4. тома мест, `secrets/` и `agents/` удаляются только до первого входа
       студента и только по именам;
    5. `rm -rf /opt/pcbk-reserve` — только до первого входа. После — только
       `tls/`, `compose.yaml` и `edge/`, а `secrets/` и `agents/` остаются, с
       предупреждением в тексте README.
  - «Новый студент» — два случая:
    - смена студента в слоте 01–10: новые секреты места и пустой каталог
      агентов; старые тома — только по решению владельца;
    - новое место 11+: правка allow-list `sp-ro`/`sp-ctl`, подсеть N, блок в
      `compose.yaml`, строка в `components.json`, тесты границ диапазона,
      пересборка сторожа.
- Modify: `docs/checks/D2.md`

- [ ] **Step 1: Собрать и перенести образы**

Локально: `docker build -t pcbk-reserve/student:d2 student/`,
`docker build -t pcbk-reserve/watchdog:d2 watchdog/`. `docker compose build`
без `.env` не выполнится: `compose.yaml` требует `${TLS_DIR:?}` и другие
переменные. Затем
`docker save pcbk-reserve/student:d2 pcbk-reserve/watchdog:d2 | gzip | $SSH 'gunzip | docker load'`.
Сверка на обеих сторонах: `docker image inspect -f '{{json .RootFS}}'` —
хранилище containerd, как записано в Д1.
Expected: у обоих образов `RootFS` совпали.

- [ ] **Step 2: Секреты, каталоги агентов и тома (на сервере; пути — из `.env`)**

```bash
cd /opt/pcbk-reserve && SD=$(grep '^SECRETS_DIR=' .env | cut -d= -f2) && AD=$(grep '^AGENTS_DIR=' .env | cut -d= -f2) &&
echo "$SD $AD" && umask 077 && install -d -m 0700 "$SD" "$AD" &&
for n in $(seq -w 1 10); do
  for s in pw llm-token; do f=$SD/student-$n.$s
    [ -e "$f" ] || { openssl rand -hex 24 | tr -d '\n' > "$f"; chmod 0444 "$f"; }
  done
  install -d -m 0755 "$AD/student-$n"
  for k in state work; do docker volume create --label pcbk.role=student "pcbk-student-$n-$k" >/dev/null; done
done
stat -c '%a' "$SD" "$AD"; stat -c '%a %s' "$SD"/* | sort -u; ls "$SD" | wc -l; stat -c '%a' "$AD"/* | sort -u
sha256sum "$SD"/* | cut -c1-64 | sort -u | wc -l; docker volume ls -q --filter label=pcbk.role=student | wc -l
```
Expected: оба пути лежат под `/opt/pcbk-reserve`; `700` и `700`; `444 48`;
`20` файлов; `755`; `20` различных сумм — все двадцать секретов разные
(Review Focus 2); `20` томов. Содержимое на экран не выводится.

- [ ] **Step 3: Создать места и обновить сторожа**

`cp -p compose.yaml compose.yaml.d1` на сервере; локально
`rsync -a compose.yaml …:/opt/pcbk-reserve/` (без `--delete`). На сервере:
`docker compose --profile students create --no-build $(printf 'student-%02d ' $(seq 1 10))`,
затем `docker compose up -d --no-build watchdog`.
Порядок важен: если сначала поднять сторожа, на странице появятся десять
красных «нет контейнера».

Свои объекты у каждого места (Review Focus 2):
```bash
ok=0; for n in $(seq -w 1 10); do
  set -- $(docker inspect -f '{{.HostConfig.Runtime}} {{range .Mounts}}{{.Source}} {{end}}' pcbk-student-$n)
  rt=$1; shift
  [ "$rt" = runsc ] && [ $# -eq 5 ] && ! printf '%s\n' "$@" | grep -qv "student-$n" && ok=$((ok+1))
done; echo "$ok из 10"
```
Expected:
- `10 из 10` — у каждого места `runsc` и пять источников, в каждом `student-NN`;
- `docker ps -a --filter label=pcbk.role=student --format '{{.State}}' | sort | uniq -c` → `10 created`;
- `docker network ls --filter name=pcbk-stu- -q | wc -l` → `10`;
- у мостов десяти сетей нет IPv4 (`ip -4 addr show dev br-<id>` пусто);
- `curl -sk https://127.0.0.1:8443/status.json | python3 -c 'import json,sys; print(sum(c["detail"] == "спит" for c in json.load(sys.stdin)["checks"]))'` → `10`;
- `ip route get "$BDRV_HOST"` по-прежнему через шлюз (вердикт);
- у контейнеров Dify время работы продолжает `~/pcbk-d2-before.txt`.

Откат (до первого входа студента):
- `docker compose rm -sf $(printf 'student-%02d ' $(seq 1 10))`;
- `docker network rm $(printf 'pcbk-stu-%02d ' $(seq 1 10))`;
- тома `docker volume rm` по именам `pcbk-student-NN-*`;
- вернуть `compose.yaml.d1` и выполнить `docker compose up -d --no-build watchdog`
  (образ `:d1`).

`docker compose down` не выполнять.

- [ ] **Step 4: Commit** (после проверки на секреты)

```bash
git add deploy/README.md docs/checks/D2.md
git commit -m "Выкладка Д2: десять рабочих мест созданы и спят, у каждого свои объекты, откат без удаления данных"
```

---

### Task 5: Живые проверки и учения

Шаги идут в порядке номеров. Шаги 1–6 и 9–10 обязательны; шаги 7–8
выполняются, только если к концу шага 6 прошло не больше 8,25 ч, иначе
уходят в слот Д3а (см. черту в шапке). Итог каждого шага — вердиктом с пометкой
[П] в `docs/checks/D2.md`.

Снимки делаются через туннель на свободный порт:
`$SSH -o ExitOnForwardFailure=yes -N -L 28443:127.0.0.1:8443` и
`google-chrome --headless=new --ignore-certificate-errors --virtual-time-budget=8000 --window-size=1200,2000 --screenshot=docs/checks/D2/<имя>.png https://127.0.0.1:28443/status`.
Порт 18443 не берём: его занимает локальный тестовый стенд. Перед первым
снимком сверить признак сервера: строка `edge` в
`https://127.0.0.1:28443/status.json` совпадает с серверной (на 30.09 —
«сертификат истёк»).

Помощники оболочки лежат в рабочем каталоге задания. На сервер их копируют в
`~/pcbk-d2/` и удаляют в конце дня; в репозиторий они не попадают. Все
выполняются в `/opt/pcbk-reserve`; `STU`, `SD`, `AD` берутся из `.env`. `NN` в
аргументах — с ведущим нулём, а адрес — без него: `N=$((10#$NN))`.
- `probe NET ADDR PORT` —
  `docker run --rm --runtime=runsc --network NET curlimages/curl:8.16.0 -sv --connect-timeout 3 -m 4 telnet://ADDR:PORT 2>&1 | grep -cE 'Established connection|Connected to'`
  (только порт, без пароля).
- `oc NN METHOD PATH [MM|нет]` — `N=$((10#$1))`; одноразовый curl под `runsc`
  в `pcbk-stu-$1` с `-v "$SD/student-MM.pw:/pw:ro"` (по умолчанию `MM = NN`):
  `curl -s -m 10 -X METHOD --variable 'PW@/pw' --expand-user 'opencode:{{PW}}' -w '\n%{http_code}' http://$STU.$N.3:4096PATH`.
  Со словом `нет` — без файла и без Basic. Печатает тело и последней строкой —
  код.
- `ocpid NN` — `docker exec pcbk-student-NN bash -c 'for p in /proc/[0-9]*; do [ "$(readlink $p/exe)" = /usr/local/bin/opencode ] && echo ${p#/proc/}; done'`.
- `lasttools NN MODEL` —
  `oc NN GET "/experimental/tool?provider=pcbk&model=MODEL" | head -n -1 | python3 -c 'import json,sys; d={t["id"]: t["description"] for t in json.load(sys.stdin)}; print({k: v[:40] for k, v in d.items() if k in ("bash","edit","write","apply_patch")})'`
  — последняя запись каждого id.

- [ ] **Step 1: Место 01 под `runsc` (§11 п. 1)**

`docker start pcbk-student-01`; ждать, пока `docker inspect -f '{{.State.Health.Status}}'`
не покажет `healthy` (не дольше 60 с).
Expected:
- `docker inspect -f '{{.HostConfig.Runtime}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.Init}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.PidsLimit}} {{.HostConfig.IpcMode}} [{{.HostConfig.PidMode}}] {{len .HostConfig.Devices}}' pcbk-student-01` → `runsc true true 1073741824 1073741824 512 private [] 0`;
- `docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}{{range $k, $v := .HostConfig.Tmpfs}}{{$k}}{{end}}' pcbk-student-01` — ровно пять назначений `MOUNTS` и `/tmp`;
  `docker exec pcbk-student-01 ls -A /run/secrets` → `llm-token opencode-pw`;
- `docker exec pcbk-student-01 cat /proc/version` → ядро gVisor, а не хоста; `/proc/1/comm` → `docker-init`;
- `oc 01 GET /global/health нет` → `401`; `oc 01 GET /global/health` → `200`;
- `lasttools 01 stub` — у `bash`, `edit`, `write` описание «Отключено на учебном
  стенде…»; `lasttools 01 gpt-5` — у `bash` и `apply_patch` тоже. В журнал:
  `id` в `/experimental/tool` повторяются (встроенный, затем заглушка), модель
  получает последнюю запись;
- обход через `/work` под `runsc`: `docker exec` пишет
  `/work/.opencode/tools/bash.ts` («BYPASS real shell») и `/work/opencode.json`,
  затем `oc 01 POST /instance/dispose`. `lasttools 01 stub` → у `bash`
  заглушка, `BYPASS` нет. Файлы удалить, `dispose` повторить;
  `docker exec pcbk-student-01 ls -A /work` → пусто;
- повтор `test_readonly_where_it_matters` под `runsc` одним `docker exec`:
  печатаются только отклонения («пишется <каталог>» / «не пишется
  <каталог>») → пусто; после него
  `docker exec pcbk-student-01 ls -A /var/lib/opencode/.pcbk-w /tmp/.pcbk-w /work 2>/dev/null` → пусто;
- `/proc/$(ocpid 01)/status` → `CapEff` и `CapBnd` нули, `NoNewPrivs: 1`;
- `getent hosts core` в месте → `.2` своей сети (вердикт).

Вердикт в журнал: «пароля БДРВ в месте нет: монтирования только
перечисленные, в окружении только переменные образа». Снимок
`01-ten-workplaces.png`: место 01 «работает», 02–10 «спит».

- [ ] **Step 2: Изоляция (§9 «Неуспех 1», для мест)**

`docker start pcbk-student-02`, дождаться `healthy`. Структура:
`docker network inspect` десяти сетей — `Internal: true`, `isolated`;
`ip -4 addr show dev br-<id>` для каждой — пусто. IPv6:
`docker exec pcbk-student-01 cat /proc/net/if_inet6` → у `eth0` адресов нет.
Отсутствие адреса на мосту проверено для IPv4; путь по IPv6 закрыт тем, что у
места нет IPv6. Порты из `pcbk-stu-01`:
- положительные контроли: `probe pcbk-stu-01 $STU.1.3 4096` → **1**; `probe pcbk-stu-02 $STU.2.3 4096` → **1**;
- `$STU.1.1` и шлюз из `IPAM` на 22, 80, 443, 2375, 3389 → 0;
- `$HOST_LAN` на 22, 443; `$BDRV_HOST` на 1433; `1.1.1.1` на 443; `$STU.2.3` на 4096 → 0;
- стенд: адреса `pcbk-sp-ctl`, `pcbk-sp-ro` на 2375, `pcbk-watchdog` на 8090,
  `pcbk-edge` на 8443 (из `docker inspect`, все их сети) → 0;
- отрицательный контроль: временная сеть `pcbk-stu-ctl` — `--internal`,
  **без** isolated, `--subnet 172.31.250.240/28`. Адрес её моста на 22 → **1**:
  проверка видит путь, который закрывает изолированный шлюз. Сеть удалить.
  Если здесь 0 — вход с мостов режет ufw; записать это, как в Д1.

Expected: как указано. Без положительных контролей набор не засчитывается.

- [ ] **Step 3: Пароль не виден**

- `docker exec pcbk-watchdog python -c 'import urllib.request as u; print(u.urlopen("http://pcbk-sp-ro:2375/v1.44/containers/pcbk-student-01/json").read().decode())' > ~/pcbk-d2/insp01.json`;
  `grep -c -F -f "$SD/student-01.pw" ~/pcbk-d2/insp01.json` → `0`,
  `grep -c -F -f "$SD/student-01.llm-token" ~/pcbk-d2/insp01.json` → `0`,
  `grep -c OPENCODE_SERVER_PASSWORD ~/pcbk-d2/insp01.json` → `0`; файл удалить;
- `docker inspect pcbk-student-01 | grep -c -F -f "$SD/student-01.pw"` → `0`;
- журнал контейнера: `docker logs pcbk-student-01 2>&1 | grep -c -F -f "$SD/student-01.pw"` → `0`,
  то же с `student-01.llm-token` → `0`;
- `docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' pcbk-student-01`:
  `| grep -cvE '^[0-9]{3}$'` → `0` (каждая запись — три цифры; в первые
  секунды бывает `000`), `| tail -1` → `200`;
- имена в окружении OpenCode: `docker exec pcbk-student-01 bash -c "tr '\0' '\n' < /proc/$(ocpid 01)/environ | cut -d= -f1 | sort"` — список записывается в журнал, сверх переменных образа и `RUNTIME_ENV` там только `OPENCODE_SERVER_PASSWORD`;
- своё: `docker exec pcbk-student-01 bash -c "tr '\0' '\n' < /proc/$(ocpid 01)/environ | grep -qxFf <(printf 'OPENCODE_SERVER_PASSWORD=%s\n' \"\$(cat /run/secrets/opencode-pw)\") && echo своё"` → `своё`;
- чужое: `docker exec pcbk-student-01 cat /proc/$(ocpid 01)/environ | tr '\0' '\n' | grep -c -F -f <(for f in "$SD"/student-0[2-9].pw "$SD"/student-10.pw "$SD"/student-01.llm-token; do cat "$f"; echo; done)` → `0`.

Expected: как указано; на экран выходят только числа, имена переменных и слово
«своё».

- [ ] **Step 4: Учение «память места переполнена»**

До учения `MemAvailable` ≥ 3 ГиБ (вердикт). Подписка на событие — заранее, в
фоне: буфер `docker events --since` рядом с Dify покрывает меньше минуты.
```bash
docker events --filter container=pcbk-student-01 --filter event=oom --format '{{.Action}} {{.Time}}' > ~/pcbk-d2/oom.txt & EVPID=$!
docker exec pcbk-student-01 tail /dev/zero      # оборвётся вместе с песочницей
```
Expected:
- `grep -c '^oom' ~/pcbk-d2/oom.txt` ≥ 1, затем `kill $EVPID` — причина
  записывается в журнал по этому событию;
- `docker inspect -f '{{.RestartCount}} {{.State.Running}}' pcbk-student-01` → `1 true`, затем `healthy`;
- на странице «Рабочее место 01 — перезапущен после сбоя в ЧЧ:ММ UTC±ЧЧ:ММ…» жёлтым, событие в журнале страницы.

Причину («по памяти») страница не покажет: Docker при перезапуске сбрасывает
`OOMKilled`. Строка «убит по памяти» для мест с `unless-stopped` недостижима —
причина перезапуска на странице отложена в Д11. Это же сказать в отчёте
владельцу. Снимок `02-oom-restarted.png` — не позже 15 минут после сбоя.

Если песочница выжила (внутри gVisor убит только `tail`: проба Д1 проверяла
главный процесс, а здесь память ест второстепенный), это записывается как есть
[П]. Видимая замена без `sudo`: `docker exec pcbk-student-01 kill -KILL $(ocpid 01)`.
Тогда `docker-init` выходит с кодом 137, `unless-stopped` поднимает место, и на
странице та же жёлтая строка. В журнале это называется «сбой OpenCode», не «по
памяти». Вопрос о переполнении — в отчёт владельцу.

- [ ] **Step 5: Учение «OpenCode завис»**

`docker exec pcbk-student-01 kill -STOP $(ocpid 01)`; время записать.
Expected: не позже чем через 2 минуты
`docker inspect -f '{{.State.Health.Status}}'` → `unhealthy`, на странице —
«Рабочее место 01 — OpenCode не отвечает» красным. Снимок `03-opencode-hung.png`.
Затем `docker exec pcbk-student-01 kill -CONT $(ocpid 01)` → `healthy` за ≤ 35 с,
красной строки места нет. Снимок `04-recovered.png`. В журнале страницы есть
сбой и восстановление.
В журнал Д2 записать: строка «проверка здоровья молчит» проверена только
модульным тестом, её живое учение требует заклинить монитор здоровья, а это
`sudo`.

- [ ] **Step 6: Обход мест 03–10 (Review Focus 2)**

```bash
for n in $(seq -w 3 10); do
  docker start pcbk-student-$n >/dev/null
  for i in $(seq 60); do [ "$(docker inspect -f '{{.State.Health.Status}}' pcbk-student-$n)" = healthy ] && break; sleep 1; done
  echo "$n $(docker inspect -f '{{.State.Health.Status}}' pcbk-student-$n) $(oc $n GET /global/health | tail -1) $(oc $n GET /global/health 01 | tail -1)"
  docker stop pcbk-student-$n >/dev/null
done
```
Expected: восемь строк `NN healthy 200 401` (для 08 и 09 тоже — `oc` снимает
ведущий ноль); затем на странице восемь строк «спит». Вердикт — одной строкой:
«места 03–10 под runsc: стартуют, свой пароль 200, пароль места 01 — 401,
после остановки спят — 8 из 8».

- [ ] **Step 7: `dispose` видит файл агента, записанный с хоста (§11 п. 2, без живого потока)** — переносимый

`oc 01 GET /agent` (экземпляр создан); на хосте
`install -m 0644 /dev/stdin "$AD/student-01/probe.md"` с текстом
`PROBE_AGENT` из задачи 3.
Expected: `oc 01 GET /agent | grep -c '"name":"probe"'` → `0`;
`oc 01 POST /instance/dispose` → `200`; снова → `1`. Затем удалить файл,
`dispose`, → `0`.

- [ ] **Step 8: Холодный старт ×3 и память в простое (§11 п. 5, простой)** — переносимый

Три раза: `docker stop pcbk-student-01`; `t0=$(date +%s%N)`;
`docker start pcbk-student-01`; один одноразовый curl под `runsc` в
`pcbk-stu-01` (пароль — файлом, как в `oc`) крутит `GET /global/health`
(`-m 2`, пауза 0,2 с) до `200`; записать миллисекунды от `start`. Через 60 с
простоя мест 01 и 02:
`docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.PIDs}}' pcbk-student-01 pcbk-student-02`
и `cat /sys/fs/cgroup/system.slice/docker-<полный id>.scope/{memory.current,memory.swap.max,memory.swap.current,pids.current}`.
Expected: три времени и два замера в журнале; `memory.swap.max` — `0`
(решение 14 на сервере), `memory.swap.current` — как замер. Если память >
700 МиБ или потоков > 358, `mem_limit`/`pids_limit` правятся одним коммитом до
шага 10, и места пересоздаются (`create --force-recreate --no-build`, тома
внешние и сохраняются).

- [ ] **Step 9: Вернуть места в сон**

`docker stop pcbk-student-01 pcbk-student-02`.
Expected: на странице десять строк «спит»; снимок `05-rest.png`.

- [ ] **Step 10: Commit** (после проверки на секреты)

```bash
git add docs/checks/
git commit -m "Д2: живые проверки мест под gVisor — изоляция, пароль, свои объекты, обход 03–10; два учения"
```

---

### Task 6: Закрытие дня

- [ ] **Step 1:** В `docs/DESIGN-platform-2026-09-29.md` пометку [П] и ссылку
  на `docs/checks/D2.md` получают только проверенные пункты. §11 п. 1 —
  всегда. П. 2 (без живого потока) и п. 5 (простой) — только если шаги 7–8
  задачи 5 выполнены; иначе они получают «локально [Л], живьём — слот Д3а». §9
  «Неуспех 1» целиком [П] не помечать: для мест проверены сеть, пароль места и
  отсутствие пароля БДРВ, а шлюз и опасные вызовы — это Д5. В `README.md`,
  раздел «Состояние», — «Д2 готов», что дальше (Д3а), что ушло в его слот,
  карта документов с этим планом. В шапку `docs/plans/DRAFT-D2-workplaces.md` —
  одна строка: заменён этим планом. В `docs/PLAN-platform-2026-09-29.md`,
  «Отклонения», — строка только о том, что расходится с проектом: переносы в
  Д3а, поправки лимитов, отказ от `init`, если он был.
- [ ] **Step 2:** Критик (Opus 5.5) по итогу дня. Блокер — любой пункт
  «Блокер дня» дорожной карты: результат не виден; достижим неуспех 1
  (путь из места к соседу, серверу, прокси сокета, 1433 или в интернет,
  пароль виден); место выложено без наблюдения сторожа; секрет в git. Петля —
  до нуля блокеров, не больше двух раундов; третий — только после разговора с
  владельцем. Второй раунд после 9,75 ч — в слот Д3а (шапка), слияние и тег —
  после него.
- [ ] **Step 3:** До слияния — проверка на секреты по всей ветке, с
  положительным контролем шаблонов:
  `echo 'Basic QUJDREVGR0hJSktMTU5PUA==' | grep -c -E -f <шаблоны>` → `1`;
  `printf '%048x\n' 1 | grep -c -E -f <шаблоны>` → `1`;
  `git log -p main..d2/workplaces -- . ':!docs/plans' | grep -E -i -f <шаблоны>` → пусто;
  `git ls-files | grep -cE '\.(pw|llm-token)$'` → `0`;
  `git ls-files student/config/.gitignore` — файл в индексе. Затем ветку
  `d2/workplaces` — в `main` (fast-forward), тег `platform-d2`,
  `git push origin main platform-d2`. Удалить `~/pcbk-d2/` на сервере; образ
  curl остаётся (ограничения Д2).
- [ ] **Step 4:** Чистый клон (`git clone "$(git remote get-url origin)"` в
  рабочий каталог задания, тег `platform-d2`): `student/config/.gitignore`
  есть и пуст, есть `deploy/images.lock` и снимки `docs/checks/D2/`;
  `uv run --python 3.12 --with pytest pytest -q tests/integration/test_student_image.py "tests/integration/test_students.py::test_agent_file_visible_after_dispose"` → PASS
  (образ из чистого клона отвечает на `GET /agent`). После 9,75 ч — в слот
  Д3а.
- [ ] **Step 5:** Владельцу — «Д2 готов», снимки и только вопросы, которые
  требуют его решения: новый сертификат входа (истёк 21.09); 8443 из сети
  ПЦБК, если не проверено; причина перезапуска места на странице не видна (в
  Д11); учение по памяти, если песочница выжила; поправки лимитов, если замер
  их потребовал; что ушло в слот Д3а.
