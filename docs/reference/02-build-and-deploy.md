# Сборка из исходников и развёртывание

Этот документ описывает, как собрать все образы стенда `pcbk-reserve` из исходников и развернуть полную среду
с нуля, а также как собрать и запустить только часть компонентов. Общая архитектура и назначение ролей —
[01-architecture.md](01-architecture.md); все переменные `.env` и профили `compose` — таблицами в
[03-configuration.md](03-configuration.md); полный список HTTP/MCP ручек службы данных — в
[10-data-service.md](10-data-service.md); сторож и операционные скрипты — в
[18-watchdog-and-ops.md](18-watchdog-and-ops.md); полный тестовый набор — в [19-tests-and-bench.md](19-tests-and-bench.md).

Все пути ниже — относительно корня репозитория, если не сказано иное.

## 1. Требования к серверу

В `deploy/README.md` и `deploy/ADMIN.md` явно требуется:

- пользователь с доступом к Docker (член группы `docker`); `sudo` нужен только один раз — для установки gVisor,
  создания каталога `/opt/pcbk-reserve` и копии TLS-сертификата;
- **gVisor** release `20260921.0` (`x86_64`), платформа **`systrap`** — его `runtime: runsc` используют контейнеры
  рабочих мест (`x-student` в `compose.yaml`); без него нельзя поднять профиль `students` в продуктивной
  компоновке (в тестовом контуре `compose.test.yaml` места идут на `runtime: runc` — gVisor для локальных тестов
  не нужен, см. раздел 7);
- Docker Engine с подключаемым плагином **`docker compose`** (вызывается как `docker compose …`, а не отдельным
  `docker-compose`); `compose.yaml`/`compose.test.yaml` используют профили (`profiles:`), секреты Compose,
  `bind.create_host_path: false` и (в `compose.test.yaml`) мёрж-директиву `!override` — точная минимальная версия
  `docker compose` в исходниках не указана, но нужна современная (поддерживающая эти возможности) версия;
- сервер **не должен ходить в интернет для сборки образов** — все три собственных образа (`core`, `watchdog`,
  `student`) собираются на машине разработчика и переносятся на сервер `docker save | gzip | ssh … | gunzip |
  docker load`; сторонние образы грузятся тем же способом по дайджесту из `deploy/images.lock`, без `docker pull`
  на сервере (`pull_policy: never` у всех служб `compose.yaml`);
- свободное место на диске — под образы, тома данных, резервные копии (`backup/`, см. `deploy/README.md`,
  «Резервная копия и восстановление») и статику веб-чата. Точных минимальных цифр ОЗУ/диска в `deploy/README.md`
  нет; верхний предел памяти по жёстким `mem_limit` контейнеров `compose.yaml` (не резерв, а потолок cgroup) —
  11 рабочих мест по 1 ГиБ (`mem_limit: 1g`, `memswap_limit: 1g` — swap у места фактически 0) + `core` 512 МиБ +
  `watchdog` 128 МиБ + `edge` 64 МиБ + `sp-ro`/`sp-ctl` по 32 МиБ ≈ **11,75 ГиБ** суммарного потолка контейнеров
  стенда, не считая ОС хоста и других служб сервера (например, Dify, если он установлен на том же сервере —
  раздел «Dify и резерв» `deploy/ADMIN.md` явно требует не трогать его);
- при установленном рядом Dify — его сертификат и учётные данные историана **копируются**, а не задаются
  отдельно (разделы 4.1 и 4.3); это специфика площадки ПЦБК, не обязательное условие архитектуры: для другого
  заказчика достаточно любого действительного сертификата и собственной учётки историана.

Порт **8443** контейнера `edge` — единственный, публикуемый наружу; Docker публикует его **в обход `ufw`**
(`deploy/README.md`, «Что открывается наружу»). Остальные службы доступны только из внутренних сетей Docker.

## 2. Сборка образов

### 2.1 `core` — серверный слой

Контекст сборки — `core/` (весь каталог отправляется в контекст, но в образ копируются только `requirements.lock`
и `pcbk_core/`). `core/Dockerfile`:

- база `python:3.12-slim`, закреплённая по дайджесту (`sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`);
- зависимости — `pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock` (точные версии и
  хеши; без добора транзитивных зависимостей — все нужные уже перечислены в `requirements.lock` построителем
  `uv pip compile`, см. ниже);
- свой непривилегированный пользователь `pcbk-core`, `uid:gid 10003:10003`, без домашнего каталога, оболочка
  `/usr/sbin/nologin`; каталог `/var/lib/pcbk-core` создаётся `0750` и принадлежит `10003:10003` — на нём позже
  монтируется том `pcbk-core-data`, который наследует владельца каталога образа;
- рабочий каталог `/app`, туда копируется `pcbk_core/`;
- контейнер слушает `8000` (не публикуется наружу — только во внутренних сетях);
- `HEALTHCHECK` — `python -m pcbk_core.main --healthcheck` (интервал 30 с, таймаут 5 с, старт-период 20 с,
  3 попытки): проверяет только живость процесса uvicorn (`/healthz/live`), не здоровье отдельных ролей — это
  видит сторож через `/healthz/data`, `/health/llm` и т. д.;
- `CMD` — `python -m pcbk_core.main`.

`requirements.in` → `requirements.lock` строится командой (комментарий в самом `requirements.in`):

```bash
uv pip compile core/requirements.in --python-version 3.12 --generate-hashes -o core/requirements.lock
```

Прямые зависимости `requirements.in`: `python-tds==1.17.1` (драйвер историана), `mcp==1.30.0` (последняя версия
линии 1.x — переход на 2.x с другим API не сделан), `fastapi==0.142.0`, `uvicorn==0.54.0`, `httpx==0.28.1`
(клиент LLM-прокси к OpenRouter, версию которого также фиксирует `mcp`), `anyio==4.15.1` (срок запроса к
OpenRouter, `fail_after`, импортируется напрямую из кода LLM-прокси). Файл `core/pyproject.toml` задаёт только
`requires-python = ">=3.12"` и настройки `pytest`/`uv` (`tool.uv.managed = false` — `uv run` здесь не создаёт
`.venv`/`uv.lock`, зависимости ставятся только в образ по `requirements.lock`).

Сборка и тег (как в `compose.yaml`, служба `core`):

```bash
docker compose build core
# или напрямую, вне compose:
docker build -t pcbk-reserve/core:d28b core/
```

Текущий тег в `compose.yaml` — **`pcbk-reserve/core:d28b`**. Образы помечаются внутренним кодом выпуска
(`:dNN[буква]`); при каждой выкладке новый образ получает новый тег, а предыдущий тег с сервера **не удаляется** —
это и есть механизм отката (раздел 5).

### 2.2 `watchdog` — сторож

Контекст — `watchdog/`. `watchdog/Dockerfile`:

- та же база `python:3.12-slim` по тому же дайджесту; зависимостей нет — только стандартная библиотека
  (`watchdog/pyproject.toml` не объявляет сторонних пакетов, `tool.uv.managed = false`);
- свой пользователь `pcbk-watchdog`, `uid:gid 10002:10002`, каталог `/var/lib/pcbk-watchdog` — `0750`,
  владелец `10002:10002` (под него монтируется том `pcbk-watchdog-journal`);
- рабочий каталог `/app`, туда копируются `components.json` и `pcbk_watchdog/`;
- слушает `8090`;
- `HEALTHCHECK` — `python -m pcbk_watchdog.main --healthcheck`: 200 только при свежем снимке состояния —
  зависший цикл сторожа делает контейнер `unhealthy`;
- `CMD` — `python -m pcbk_watchdog.main`.

```bash
docker compose build watchdog
# или:
docker build -t pcbk-reserve/watchdog:d25 watchdog/
```

Текущий тег в `compose.yaml` — **`pcbk-reserve/watchdog:d25`**.

### 2.3 `student` — образ рабочего места

У службы `student-NN` в `compose.yaml` **нет секции `build`** — образ один на все 11 мест (`PLACE_MAX` в
`core/pcbk_core/places.py`), собирается локально
(`docker build`) и переносится на сервер тем же способом `docker save`/`docker load`, что и прочие образы.
Контекст сборки — каталог `student/` (`deploy/README.md`, «Рабочие места»: `COPY config/` захватил бы в образ
любой лишний файл рабочей копии, поэтому собирать нужно из чистого дерева — `git archive`, см. раздел 4.1).

`student/Dockerfile` — многоступенчатая сборка:

1. стадия `fetch`, база `ubuntu:22.04` по дайджесту (`sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02`):
   скачивает и проверяет по `sha256` два бинарника через `ADD --checksum=…`:
   - OpenCode `v1.18.33`: `https://github.com/anomalyco/opencode/releases/download/v1.18.33/opencode-linux-x64.tar.gz`
     (`sha256:e546123213ae47909a4268692aa4b94950d011afe9cac9938753a2194f1c16d5`);
   - ripgrep `15.1.0`: `https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz`
     (`sha256:1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599`);

   **из-за `ADD --checksum` на внешние URL, `docker build` этого образа требует исходящий доступ в интернет с
   машины, на которой он выполняется** (это и есть причина «собирается на машине разработчика» — сервер в
   интернет не выходит);
2. финальная стадия — та же база `ubuntu:22.04` по тому же дайджесту:
   - пользователь `pcbk`, `uid:gid 10001:10001`, без домашнего каталога на диске (`-M`), оболочка
     `/usr/sbin/nologin`; каталоги `/home/pcbk`, `/etc/pcbk-opencode/opencode/agents`, `/run/secrets` — `root:root
     0755`; `/var/lib/opencode` и `/work` — `10001:10001 0700`;
   - бинарники `opencode`/`rg` из стадии `fetch` → `/usr/local/bin` (`chmod 0755`, владелец `0:0`);
   - скрипты `pcbk-entrypoint` и `pcbk-health` (ниже) → `/usr/local/bin` (`0755`, `0:0`);
   - конфигурация OpenCode — `COPY config/` → `/etc/pcbk-opencode/opencode/` (каталоги `0755`, файлы `0644`
     после отдельного `find … -exec chmod`; комментарий в Dockerfile: без пустого `.gitignore` в составе
     конфигурации `GET /agent` отвечает `500`);
   - снятие `setuid`/`setgid` у всего образа (`find / -xdev -perm /6000 … chmod a-s`) — `su`, `mount`, `passwd`
     и прочие бинарники базового образа не становятся трамплином к root;
   - переменные окружения: `HOME=/home/pcbk`, `XDG_CONFIG_HOME=/etc/pcbk-opencode`,
     `XDG_DATA_HOME=/var/lib/opencode/data`, `XDG_CACHE_HOME=/var/lib/opencode/cache`,
     `XDG_STATE_HOME=/var/lib/opencode/state`, `TMPDIR=/tmp`, флаги `OPENCODE_DISABLE_*` (обновление,
     скачивание LSP, свои плагины, автообмен, фетч моделей, пользовательский конфиг проекта),
     `OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=true`, `BUN_RUNTIME_TRANSPILER_CACHE_PATH=0`,
     `OPENCODE_SERVER_USERNAME=opencode`;
   - `USER 10001:10001`, `WORKDIR /work`, `EXPOSE 4096`;
   - `HEALTHCHECK` — `/usr/local/bin/pcbk-health` (интервал 30 с, таймаут 5 с, старт-период 20 с, старт-интервал
     2 с, 3 попытки);
   - `ENTRYPOINT ["/usr/local/bin/pcbk-entrypoint"]`.

`student/pcbk-entrypoint` (bash): читает пароль сервера OpenCode из `/run/secrets/opencode-pw` (одна строка,
без доступа к файлу или пустого значения — молча `pw=""`); если пароль пуст — печатает причину в stderr и
**завершается кодом `78`**, не запускаясь; иначе экспортирует `OPENCODE_SERVER_PASSWORD` и выполняет
`exec opencode serve --hostname 0.0.0.0 --port 4096`.

`student/pcbk-health` (bash, вызывается `HEALTHCHECK`): двумя сырыми HTTP-запросами через `/dev/tcp`
(без `curl`/`wget` в образе) проверяет `GET /agent` (ручка экземпляра, Basic-авторизация логином
`$OPENCODE_SERVER_USERNAME` и паролем из `/run/secrets/opencode-pw`) — не `200` — вывод кода и выход `1`; затем
`GET /mcp` — вытаскивает регуляркой слово `status` клиента `pcbk` (например, `connected`/`failed`/`unknown`) и
печатает `200 mcp=<слово>`, выход `0`. Если статус MCP — `failed`, не чаще раза в 60 с (метка в `/tmp/pcbk-mcp-retry`)
шлёт `POST /mcp/pcbk/connect` на переподключение, не дожидаясь ответа. Весь прогон укладывается в ~4,5 с при
сроке `HEALTHCHECK` 5 с. Эта проверка образа и есть то, что строка «рабочее место» видит на странице состояния
(«200 mcp=connected» и т. п.) — отдельного `HEALTHCHECK` на уровне `compose.yaml` у мест нет.

Сборка и тег (как в `compose.yaml`, якорь `x-student.image`):

```bash
docker build -t pcbk-reserve/student:d28 student/
```

Текущий тег — **`pcbk-reserve/student:d28`**; используется всеми одиннадцатью местами через YAML-якорь
`x-student` (`<<: *student`), поэтому у каждого `student-NN` в `compose.yaml` свой тег не указывается отдельно.

### 2.4 Статика веб-чата (`web/`)

Чат — не образ, а статические файлы, которые `edge` (nginx) отдаёт из `edge/static/chat/`; в `git` этого каталога
нет, он собирается отдельно и копируется на сервер. `web/package.json` (Node `22.x`, поле `engines.node`):

| Скрипт `npm run …` | Команда | Назначение |
|---|---|---|
| `build` | `tsc --noEmit -p . && vite build && node scripts/check-dist.mjs ${PCBK_WEB_OUT:-../edge/static/chat}` | проверка типов, сборка Vite, проверка готовой сборки |
| `test` | `vitest run && node --test tests/*.test.mjs` | unit-тесты компонентов и чистых функций (`scripts/check-dist.mjs` и т. п.) |

Зависимости — `react`/`react-dom` 19.3.0, `@assistant-ui/react*` (чат-интерфейс и адаптер к OpenCode:
`@assistant-ui/react-opencode` 0.2.25, через него же зафиксирован `@opencode-ai/sdk` 1.18.33 — та же версия, что
`student/Dockerfile`, поле `overrides` в `package.json` принуждает именно её), `remark-gfm` 4.0.1, `zod` 4.6.5.

`web/vite.config.ts` (строит и проверяет Vitest/Vite):

- `base: "/chat/"` — сборка рассчитана на то, что `edge` отдаёт её именно по этому пути;
- `build.outDir` — переменная окружения **`PCBK_WEB_OUT`**, по умолчанию `../edge/static/chat`;
- плагин `pcbkBuildInfo(process.env.PCBK_BUILD ?? "dev")` (`web/build/buildInfo.ts`) — кладёт в `<head>`
  `index.html` метку `<meta name="pcbk-build" content="<PCBK_BUILD>">` и после сборки пишет `build.json` —
  JSON `{"build": "<PCBK_BUILD>", "files": {"<путь>": "<sha256>"}}` по всем файлам итогового каталога (кроме
  `index.html`/`build.json` самих себя); `PCBK_BUILD` обязан соответствовать `^[A-Za-z0-9._-]{1,64}$`, иначе
  сборка падает с ошибкой до записи файлов;
- `build.assetsInlineLimit: 0` (без `data:`-урлов — этого требует CSP `img-src`/`font-src 'self'`),
  `build.sourcemap: false` (карт исходников в сборке нет), `modulePreload.polyfill: false` (без встроенного
  скрипта-полифила — CSP без `'unsafe-inline'`);
- собственный плагин `outDirGuard` (`guardOutDir`, экспортирована отдельно) не даёт Vite стереть каталог вывода,
  если в нём уже что-то есть и там нет `build.json` — защита от опечатки в `PCBK_WEB_OUT` (например,
  `../edge/static` вместо `../edge/static/chat`), которая иначе стёрла бы посторонние файлы `edge/static`;
- `build.target: ["chrome109", "edge109", "firefox115"]` — класс поддерживаемых браузеров.

`web/scripts/check-dist.mjs` — последний шаг `npm run build`, вызывается и отдельно:
`node scripts/check-dist.mjs <каталог сборки>`. Код `1`, если есть ошибки (сборку нельзя выкладывать); код `0`
и строка «ошибок нет», если всё в порядке (предупреждения сборку не роняют). Проверяет:

- `index.html`: ровно один классический (без `type`/`async`/`defer`/`nomodule`) `<script src="/chat/boot.js">`
  (без него сторож не увидит самостоятельный отчёт `boot_failed`), прочие `<script src>` — только модули; нет
  встроенных `<style>`, атрибутов `style=`, обработчиков `on*=`; все `src`/`srcset`/`href` (кроме `<a href>`) —
  внутри `/chat/` и указывают на существующий файл; метка `<meta name="pcbk-build" …>` есть, в тех же первых
  64 КиБ страницы (дальше её не читает сторож), и совпадает с `build.json`;
- `build.json`: форма `{"build": str (≤64 знака из того же набора), "files": {путь: sha256}}`, пути только вида
  `assets/<имя>` или `boot.js`, от 1 до **8** записей (`WEB_MAX_FILES = 8`), суммы совпадают с файлами на диске,
  ни лишних, ни недостающих файлов относительно каталога сборки;
- нет файлов `*.map` (карты исходников) и не больше 8 файлов в `assets/`;
- во всех файлах сборки — ни адресов IPv4 (кроме `127.0.0.1`), ни заданных в коде проверки буквальных имён хоста
  заказчика (две строки — доменное имя площадки и внутреннее имя сервера, обе зашиты константами в
  `check-dist.mjs`, в этот документ не переносятся), ни «похожих на имя тега БДРВ» строк (регулярка вида
  `\d{2}[A-Z]…(PV|SP|MV|LMN)`); для CSS —
  никаких `url(…)`/`@import` на внешний адрес или схему (CSP `'self'`); для JS — предупреждения (не ошибки) на
  API браузеров новее Chrome 109 и на код вида `eval`/`new Function` (несовместим с CSP без `'unsafe-eval'`).

Сборка (как в `deploy/README.md`, «Статика чата»; закреплённый коммит — `<коммит выкладки>`):

```bash
C=$(git rev-parse --short=12 <коммит выкладки>) && B=$JOB/web-$C && mkdir -p "$B" &&
git archive "$C" web edge | tar -x -C "$B" &&
(cd "$B/web" && npm ci --ignore-scripts && PCBK_WEB_OUT="$B/chat" PCBK_BUILD="$C" npm run build)
```

Собирается строго из `git archive` закреплённого коммита (а не из рабочего дерева) — метка сборки `PCBK_BUILD`
равна короткому хешу этого коммита, а в образ не попадает ничего незакоммиченного. Как перенести готовый
каталог `chat/` на сервер и как откатить статику — раздел 5.2.

### 2.5 Сторонние образы — `deploy/images.lock`

```
nginx:1.30.5-alpine           sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94
curlimages/curl:8.16.0        sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6   # test
wollomatic/socket-proxy:1.13.1 sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002
busybox:1.38.0                sha256:fd7dc98638c8e305f4dc34e979f1c0fdfdcaeb0fbf8fcff77ae834b6da3d7e6e  # test
```

Правило (комментарий в самом файле и в `deploy/README.md`): тесты и выкладка берут образы **только отсюда** — по
дайджесту индекса, затем локальный `docker tag`. Строки `# test` на сервер не переносятся, **кроме**
`curlimages/curl` — он остаётся на сервере как инструмент диагностики (используется, например, администратором
для проверок HTTP вручную); `busybox` используется только в `compose.test.yaml` (контейнер `foreign` —
«чужой» для прокси сокета, цель проверки `403`).

```bash
docker pull nginx@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94
docker tag nginx@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94 nginx:1.30.5-alpine
docker pull wollomatic/socket-proxy@sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002
docker tag wollomatic/socket-proxy@sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002 \
  wollomatic/socket-proxy:1.13.1
```

Перенос на сервер — тот же приём, что для своих образов: `docker save <образ> | gzip | ssh <сервер> 'gunzip |
docker load'`.

## 3. Образы на сервер и проверка переноса

```bash
for img in pcbk-reserve/core:d28b pcbk-reserve/watchdog:d25 pcbk-reserve/student:d28 \
           nginx:1.30.5-alpine wollomatic/socket-proxy:1.13.1; do
  docker save "$img" | gzip | ssh <сервер> 'gunzip | docker load'
done
```

Сверка, что на сервере оказался тот же образ, что собран локально — совпадение слоёв:

```bash
docker image inspect -f '{{json .RootFS.Layers}}' pcbk-reserve/core:d28b        # на машине разработчика
ssh <сервер> "docker image inspect -f '{{json .RootFS.Layers}}' pcbk-reserve/core:d28b"
```

**Образ — строго из ветки своего дня.** `deploy/README.md` подчёркивает: образ для выкладки нужно собирать из
закреплённого коммита/тега именно того выпуска, который выкладывается (например, отдельным `git worktree` на
его голове), а не из более позднего рабочего дерева — иначе под старым тегом образа на сервер уедет более новый
код, и у отката на этот тег не будет той версии, которую он должен вернуть.

## 4. Развёртывание полной среды с нуля

### 4.1 Подготовка (`sudo`, один раз на сервере)

```bash
# gVisor release-20260921.0, платформа systrap
curl -LO https://storage.googleapis.com/gvisor/releases/release/20260921.0/x86_64/runsc
curl -LO https://storage.googleapis.com/gvisor/releases/release/20260921.0/x86_64/runsc.sha512
# (аналогично для containerd-shim-runsc-v1, либо тарбол gvisor.tar.zstd + .sha512 той же сборки)
sha512sum -c runsc.sha512
sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.pre-runsc
sudo tar --zstd -xf gvisor.tar.zstd -C /usr/local/bin
sudo runsc install -- --platform=systrap
sudo dockerd --validate --config-file /etc/docker/daemon.json
sudo systemctl reload docker        # НИКОГДА restart — restart гасит все контейнеры сервера, включая соседние
```

```bash
sudo install -d -o <пользователь> -g <группа> /opt/pcbk-reserve
sudo install -d -o root -g root -m 0755 /opt/pcbk-reserve/tls
# сертификат и ключ (на площадке ПЦБК — копия файлов Dify, /opt/dify/docker/nginx/ssl/, имена — в
# /opt/dify/docker/.env, NGINX_SSL_CERT_*; для другой площадки — любой действительный сертификат/ключ этого домена)
sudo install -o root -g root -m 0644 <cert.pem источника> /opt/pcbk-reserve/tls/cert.pem
sudo install -o root -g root -m 0600 <key.pem источника>  /opt/pcbk-reserve/tls/key.pem
```

Копирование TLS-пары из соседнего Dify — специфика площадки ПЦБК (там уже есть сертификат на домен стенда),
не требование архитектуры: на другой площадке достаточно положить собственный `cert.pem`/`key.pem` тех же прав.

Вне `sudo`: `compose.yaml`, каталог `edge/` (без собранной статики чата) и переносимые образы:

```bash
scp compose.yaml <сервер>:/opt/pcbk-reserve/
scp -r edge <сервер>:/opt/pcbk-reserve/          # без chat/ — она собирается и переносится отдельно
ssh <сервер> 'chmod -R o+rX /opt/pcbk-reserve/edge'   # nginx читает файлы от другого пользователя
```

Копировать нужно из `git archive` закреплённого коммита (а не рабочую копию: иначе в `edge/` могли бы оказаться
незакоммиченные файлы).

### 4.2 `.env`

```bash
cp deploy/env.example /opt/pcbk-reserve/.env
chmod 600 /opt/pcbk-reserve/.env
```

Обязательно заполнить (без них `docker compose config` не проходит — переменные со значением `${VAR:?…}` в
`compose.yaml`):

| Переменная `.env` | Умолчание в коде | Что это | Где используется |
|---|---|---|---|
| `DOCKER_GID` | нет (обязательна) | gid группы `docker` на сервере (`getent group docker \| cut -d: -f3`) | `sp-ro`, `sp-ctl` — читают от этого gid, чтобы иметь доступ к `/var/run/docker.sock` |
| `STU_NET` | `172.31` (в `compose.yaml`, но README требует заполнить по правилу подсетей площадки) | первые два октета сетей рабочих мест, `${STU_NET}.N.0/28` | сети `pcbk-stu-01…11`, адреса `core` и мест в них |
| `TLS_DIR` | нет (обязательна) | каталог с `cert.pem`/`key.pem` входа | `edge` (сертификат 8443), `watchdog` (проверяет, что `edge` отдаёт именно этот сертификат) |
| `DATA_DIR` | нет (обязательна) | каталог с белым списком тегов (`whitelist.txt`, строит `build_whitelist`) | `core` (роль `data`) |
| `SECRETS_DIR` | по умолчанию в `env.example` — `/opt/pcbk-reserve/secrets` | каталог файлов-секретов (пароли мест, токены, ключи OpenRouter, копия `bdrv.env`) | `core` (секреты Compose) |
| `AGENTS_DIR` | по умолчанию `/opt/pcbk-reserve/agents` | каталог агентов студентов, владелец — `core` (uid 10003) | `core` (пишет), места (читают только свой подкаталог) |
| `LESSONS_DIR` | по умолчанию `/opt/pcbk-reserve/lessons` | пакет уроков вкладки «Уроки», только для чтения | `core` |

Без `AGENTS_DIR`/`LESSONS_DIR`/`DATA_DIR` на диске (сам каталог или, для `DATA_DIR`, файл `whitelist.txt` в нём)
`docker compose` не создаст контейнер `core` (bind-монтирование без `create_host_path`, `compose.yaml`) — ошибка
проявляется на `up`/`create`, а не на `docker compose config`. Прочие переменные `.env` (пороги свежести, бюджет LLM, учения и т. д.) можно оставить
пустыми — умолчания из кода применяются автоматически; полный список и назначение каждой — таблицами в
[03-configuration.md](03-configuration.md). `ANALYTICS_ENABLED`/`ANALYTICS_CORPUS_DIR` — оставить пустыми, если
аналитический чат КДМ-1 не нужен (без них `core` их не монтирует и не включает — см. [16-analytics.md](16-analytics.md)).

### 4.3 Каталоги, права, секреты

```bash
cd /opt/pcbk-reserve
SD=$(grep '^SECRETS_DIR=' .env | cut -d= -f2)
AD=$(grep '^AGENTS_DIR=' .env | cut -d= -f2)
DD=$(grep '^DATA_DIR=' .env | cut -d= -f2)
umask 077
install -d -m 0700 "$SD" "$AD" "$DD"
```

**Секреты места** (на пустом сервере, до первого `up` места; команда ниже создаёт их, если файла ещё нет, и
никогда не перезаписывает существующий): для каждого места `01…11` — три файла по 48 шестнадцатеричных знаков,
`0444` в каталоге `0700`:

| Файл (в `$SECRETS_DIR`) | Что | Монтируется как |
|---|---|---|
| `student-NN.pw` | пароль сервера OpenCode места | секрет `opencode-pw` контейнера `student-NN` |
| `student-NN.llm-token` | токен места к LLM-прокси | секрет `llm-token` контейнера `student-NN` |
| `student-NN.data-token` | токен места к службе данных (MCP/HTTP) | секрет `data-token` контейнера `student-NN` |

```bash
cd "$SD"
for n in $(seq -w 1 11); do
  for s in pw llm-token data-token; do
    f=student-$n.$s
    [ -e "$f" ] || { openssl rand -hex 24 | tr -d '\n' > "$f"; chmod 0444 "$f"; }
  done
done
```

**Токены администратора** (`ops`), по одному разу:

```bash
[ -e ops.data-token ] || { openssl rand -hex 24 | tr -d '\n' > ops.data-token; chmod 0400 ops.data-token; }
[ -e ops.llm-token  ] || { openssl rand -hex 24 | tr -d '\n' > ops.llm-token;  chmod 0400 ops.llm-token;  }
```

**Таблицы хешей** `core-tokens` (токены данных: все 11 мест + `ops`) и `llm-tokens` (токены LLM: те же) — строки
вида `<id> <sha256 токена>`, `0444`; `core` читает их только при старте, поэтому после пересборки таблицы нужно
пересоздать контейнер (`--force-recreate`):

```bash
for f in ops.data-token student-*.data-token; do
  printf '%s %s\n' "${f%.data-token}" "$(sha256sum < "$f" | cut -c1-64)"
done > core-tokens.new && chmod 0444 core-tokens.new && mv core-tokens.new core-tokens

for f in ops.llm-token student-*.llm-token; do
  printf '%s %s\n' "${f%.llm-token}" "$(sha256sum < "$f" | cut -c1-64)"
done > llm-tokens.new && chmod 0444 llm-tokens.new && mv llm-tokens.new llm-tokens
```

**Учётка историана** — копия файла с секретами подключения (на площадке ПЦБК — файл Dify, не вводится заново):

```bash
install -m 0444 <источник>/.bdrv.env /opt/pcbk-reserve/secrets/bdrv.env
```

Формат читает `core/pcbk_core/secrets.py` (`BdrvConfig.from_env_file`) — простые строки `KEY=VALUE` (с
необязательным `export ` и одной парой кавычек по краям), обязательные ключи `BDRV_HOST`, `BDRV_USER`, `BDRV_PW`;
`BDRV_PORT` (по умолчанию `1433`) и `BDRV_DB` (по умолчанию `Runtime`) — необязательны. Если `BDRV_HOST`
содержит `\` (вид `хост\ЭКЗЕМПЛЯР`), порт не задаётся явно — его находит сам драйвер `pytds`. Ошибки чтения
называют файл и номер строки, но никогда не показывают её содержимое (там пароль).

**Ключи OpenRouter** — кладёт сам владелец ключа в своей SSH-сессии (чтобы ключ не прошёл через чат/логи):

```bash
cd "$SD"
rm -f openrouter.key && read -rs k && printf '%s' "$k" > openrouter.key && unset k && chmod 0444 openrouter.key
[ -e openrouter-mgmt.key ] || { : > openrouter-mgmt.key; chmod 0444 openrouter-mgmt.key; }   # пусто — «ключа нет»
```

Оба файла обязательны **как файлы** (пустой допустим только для `openrouter-mgmt.key`) — без любого из двух
`docker compose` не создаст контейнер `core` вовсе (оба объявлены секретами Compose без умолчания). Формат читает
`core/pcbk_core/secrets.py` (`read_key_file`): до 4096 байт, UTF-8, пробелы/переводы строк/BOM по краям
срезаются; файл длиннее лимита или не в UTF-8 — ошибка с именем файла, без текста ключа.

**Пароли студентов и преподавателя** заводятся позже, командой `accounts` (раздел 4.7) — не руками в файл.

### 4.4 Белый список тегов историана

Строит `core/pcbk_core/data/build_whitelist.py` (запускается тем же образом `core`, сетевым путём `bridge`,
временно, вне `compose`). Файл `--extra` обязан существовать (`core/pcbk_core/data/names.py`,
`load_names` — нет файла, построитель завершится ошибкой): если дополнительных имён сверх правила участка нет,
завести пустой файл заранее — `: > "$DD/whitelist.extra"`.

```bash
docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -v "$SD/bdrv.env:/run/secrets/bdrv.env:ro" \
  -v "$DD:/data" \
  pcbk-reserve/core:d28b python -m pcbk_core.data.build_whitelist \
  --out /data/whitelist.txt --extra /data/whitelist.extra
```

```
python -m pcbk_core.data.build_whitelist --out <куда> --extra <whitelist.extra>
    [--base <прежний список>] [--env-file <bdrv.env>]
```

Правило `d3-1` (как реализовано в коде, без переноса конкретных имён тегов — они данные заказчика):

1. имя тега — только символы `[A-Za-z0-9_]`;
2. **участок** — имя начинается на `20`…`25` (регулярка `2[0-5]`); с `--base <файл>` вместо этого правила —
   «имя есть в прежнем списке `<файл>`»;
3. плюс любые имена из `whitelist.extra` (отдельный файл, не ограниченный участком);
4. аналоговые теги — отбрасываются, если имя (без учёта регистра) кончается на служебный хвост:
   `_LMN`, `_TH`, `_HMI`, `_SP_HMI`, `_MV1`, `_m3`;
5. дискретные теги — попадают в список только с хвостом состояния (с учётом регистра): `_RUN`, `_OPN`, `_CLS`,
   `_ON`, `_OFF`, `_STOP`, `_FLT`, `_ALM`;
6. правила 4–5 применяются и к именам из `whitelist.extra`/прежнего списка;
7. текущая «живость» тега список не фильтрует;
8. пустой итоговый список **не перезаписывает** существующий файл (команда завершается кодом `1`).

На печать идут только числа (сводка по правилам, по видам тегов, по хвостам) — имена тегов, как производственные
данные заказчика, в вывод не попадают. Результат — файл `0444` с заголовком из строк `# ключ: значение`
(`rule`, `section`, `built`, `tags`) и отсортированными именами, записанный атомарно (временный файл рядом +
`os.replace`).

После построения:

```bash
docker compose up -d --no-build --force-recreate core
```

**Что заменить для другого предприятия.** Правило `2[0-5]` (номер участка), списки `SERVICE_TAILS`/`STATE_TAILS`
и сама схема имён (`<номер участка><буква>…`) — специфика КДМ-1 ПЦБК, зашиты константами в
`build_whitelist.py`. Для другого предприятия с другой схемой имён тегов нужно либо вести список через
`--base`/`whitelist.extra` (без переноса, без прежнего списка — самый быстрый путь), либо править константы
`SECTION_PREFIX`/`SERVICE_TAILS`/`STATE_TAILS` модуля под свою номенклатуру (см.
[21-extending.md](21-extending.md) и [10-data-service.md](10-data-service.md)).

### 4.5 Подключение к историану

Историан подключается через `bdrv.env` (раздел 4.3) — сетевой доступ от сервера к адресу `BDRV_HOST` (TCP,
`python-tds`/FreeTDS-протокол) должен быть открыт заранее; сам стенд в `compose.yaml` ничего не настраивает
кроме выхода `core` в сеть `pcbk-egress` (с masquerade, без ограничений на уровне сети — остаточный риск,
раздел «Служба данных» `deploy/README.md`; аудит-хук в процессе `core` разрешает только нужные направления).
Если историан недоступен или учётка неверна — `core` стартует, но строка «Историан БДРВ» показывает ошибку;
остальные роли (`llm`, `gateway`) не затрагиваются (это же свойство используется для частичной сборки C,
раздел 6.2).

### 4.6 Первый запуск Compose

```bash
cd /opt/pcbk-reserve
docker compose up -d --no-build core watchdog edge sp-ro sp-ctl
```

Именно **с именами служб**: команда затрагивает ровно перечисленные службы. Места (`student-NN`) объявлены под
профилем `students` (`profiles: [students]` в шаблоне `x-student`), поэтому без `--profile students` compose их не
создаёт и не запускает — и не видит их вовсе: в командах для мест профиль указывается всегда. Рабочие места
создаются отдельно (запускает их шлюз по входу студента):

```bash
docker compose --profile students create --no-build $(printf 'student-%02d ' $(seq 1 11))
docker compose up -d --no-build watchdog
```

Порядок важен: сначала `create` (места появляются, но не запускаются — спят), затем пересоздание `watchdog`,
иначе страница состояния на мгновение покажет одиннадцать строк «нет контейнера». Вручную поднимать/гасить
место — `docker start`/`docker stop pcbk-student-NN`; штатно это делает роль `gateway` при входе студента и
после простоя (раздел «Вход и рабочие места», [13-gateway.md](13-gateway.md)).

**Тома рабочих мест — внешние** (`external: true` в `compose.yaml`: `pcbk-student-NN-state`,
`pcbk-student-NN-work`) — Compose их не создаёт и не удаляет сам; на пустом сервере их нужно завести явно, до
первого `create`:

```bash
for n in $(seq -w 1 11); do
  for k in state work; do docker volume create --label pcbk.role=student "pcbk-student-$n-$k" >/dev/null; done
done
```

**Каталог агентов (`$AGENTS_DIR`) — владелец `core` (uid 10003), корень `0700`.** С хоста в него не пишут; на
чистом сервере, когда `core` уже поднят, подкаталоги мест заводит сам контейнер:

```bash
for n in $(seq -w 1 11); do docker exec -u 10003 pcbk-core install -d -m 0755 "/srv/agents/student-$n"; done
docker exec pcbk-core sh -c 'stat -c "%u %a %n" /srv/agents /srv/agents/student-*'   # ожидается 10003 700/755
```

На самой первой выкладке (когда каталог ещё пуст и принадлежит тому, кто его создал) владелец меняется
одноразовым контейнером образа `core` от `root` — по сути `sudo`; делать это нужно только с явного согласия
владельца сервера:

```bash
docker run --rm --network none --user 0 -v "$AD:/a" --entrypoint sh pcbk-reserve/core:d28b \
  -c 'chown 10003:10003 /a /a/student-* && chmod 0700 /a && chmod 0755 /a/student-* \
      && install -d -o 10003 -g 10003 -m 0700 /a/quarantine /a/archive && ls -lan /a'
```

Безымянные тома (без `name:` в `compose.yaml`: `pcbk-core-data`, `pcbk-watchdog-journal`, `pcbk-edge-temp`,
`pcbk-analytics-empty`) Compose создаёт автоматически с префиксом имени проекта: `pcbk-reserve_pcbk-core-data`
и т. д. (имя проекта — `name: pcbk-reserve` в самом `compose.yaml`, фиксировано и не зависит от каталога, из
которого запускается `docker compose`).

Проверка:

```bash
curl -sk https://127.0.0.1:8443/status.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["stale"])'
```

Ожидается `False`.

### 4.7 Статика веб-чата на сервер

Сборка на машине разработчика — раздел 2.4. Перенос, сверка и атомарная подмена каталога:

```bash
scp -r "$B/chat" <сервер>:/opt/pcbk-reserve/edge/static/chat.new
ssh <сервер> 'chmod -R o+rX /opt/pcbk-reserve/edge/static/chat.new'
```

Сверка на сервере (суммы по `build.json`, отсутствие лишних/недостающих файлов, совпадение метки):

```bash
cd /opt/pcbk-reserve/edge/static/chat.new && python3 -c 'import hashlib, json, os, sys
b = json.load(open("build.json")); files = b["files"]
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
problems = ["нет " + p for p in sorted(files) if not os.path.isfile(p)]
problems += ["сумма " + p for p in sorted(files) if os.path.isfile(p) and sha(p) != files[p]]
disk = {"assets/" + f for f in os.listdir("assets")} | set(os.listdir(".")) - {"assets", "index.html", "build.json"}
problems += ["лишний " + p for p in sorted(disk - set(files))]
if "<meta name=\"pcbk-build\" content=\"" + b["build"] + "\">" not in open("index.html").read():
    problems.append("метка index.html не " + b["build"])
print(b["build"], "сходится" if not problems else "НЕ сходится: " + "; ".join(problems)); sys.exit(bool(problems))'
```

Атомарная подмена (прежний каталог остаётся рядом для отката):

```bash
cd /opt/pcbk-reserve/edge/static &&
{ [ ! -e chat.prev ] || { echo "chat.prev уже есть"; false; }; } &&
{ [ ! -e chat ] || mv -T chat chat.prev; } && mv -T chat.new chat
```

`edge` не перезапускается — nginx читает файлы заново на каждый запрос. Между двумя `mv` чат на миг отдаёт `404`
(не кэшируется, `no-store`); файлы с хешем в имени (`assets/*`) кэшируются браузером на год. Проверка:

```bash
curl -sk https://127.0.0.1:8443/chat/ | grep -o 'pcbk-build" content="[^"]*"'
```

### 4.8 Учётные записи

Команды — через `docker exec pcbk-core python -m pcbk_core.gateway.accounts <команда>`:

| Команда | Аргументы | Действие |
|---|---|---|
| `add` | `--login L --role {student,teacher} [--place N] [--generate] [--same-person]` | завести учётку; пароль — одна строка stdin или `--generate` (печатается один раз); логин — `3…32` знаков `a-z0-9._-`, первый символ — буква |
| `passwd` | `--login L [--generate]` | сменить пароль (сессии закрываются, замок входа снимается) |
| `disable` | `--login L` | отключить (сессии закрываются, место остаётся за логином) |
| `enable` | `--login L` | включить отключённую учётку студента на её же место |
| `remove` | `--login L --yes` | удалить отключённую учётку (логин остаётся закреплён за местом) |
| `unlock` | `--login L` | снять замок входа (5 неверных паролей за 15 минут) |
| `release-place` | `--place N --yes` | освободить место пробной учётки — только после `disable` и после того, как `agents.admin archive` вынес агентов места (иначе отказ: «на месте остались агенты») |
| `list` | — | учётки и закрепления мест, без хешей паролей |
| `journal` | `[--last N]` | журнал действий преподавателя (только чтение; `N` — `1…1000` (`RECENT_MAX`), по умолчанию `50`) |

Пароль читается из stdin одной строкой (`getpass` без эха, если stdin — терминал) — никогда из аргумента
командной строки и никогда не попадает в argv/историю оболочки. Удача — код `0`; ошибка — код `1` и одна строка
в stderr без пароля.

Преподаватель (заводит администратор, один раз на курс):

```bash
LIVE=<каталог окна на машине разработчика> && umask 077 && install -d -m 0700 "$LIVE/secrets" && \
f="$LIVE/secrets/<логин>.pw" && [ ! -e "$f" ] &&
ssh <сервер> docker exec pcbk-core python -m pcbk_core.gateway.accounts add --login <логин> --role teacher --generate > "$f"
```

Пароль сразу уходит в файл на машине разработчика — не в чат, не в git, не на экран; владелец читает его сам.

Студент — либо через кабинет преподавателя («Учётные записи» → «Завести студента», раздел
[13-gateway.md](13-gateway.md)), либо той же командой администратора:

```bash
docker exec pcbk-core python -m pcbk_core.gateway.accounts add --login <логин> --role student --place <N> --generate
```

**Место и логин закрепляются навсегда** — после `remove` логин остаётся закреплён за местом, и другой логин на
занятое место `add` не примет («место NN закреплено за учёткой L — смена студента в месте только по процедуре
владельца»); исключение — пробные логины `dN-probe-*`, которые снимает `release-place`.

### 4.9 Проверка готовой среды

```bash
curl -sk https://127.0.0.1:8443/status.json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["stale"], [c["component"]+":"+c["state"] for c in d["checks"] if c["state"] != "ok"])'
docker compose ps
```

Ожидается `False []` (ничего не красное/жёлтое за вычетом ожидаемых состояний, например «спит» у ещё не
занятого места — полный разбор строк страницы состояния и первые действия — в `deploy/ADMIN.md` и
[18-watchdog-and-ops.md](18-watchdog-and-ops.md)). Вход без браузера, тем же паролем, что получит студент:

```bash
L=<логин> && PWF=<файл с паролем, одна строка> && C=<имя в сертификате edge> && T=$(umask 077; mktemp -d)
python3 -c 'import json,sys; print(json.dumps({"login": sys.argv[1], "password": open(sys.argv[2]).read().rstrip(chr(10))}))' "$L" "$PWF" > "$T/login.json"
curl -sk --resolve "$C:8443:127.0.0.1" -c "$T/cookie.txt" -H 'Content-Type: application/json' \
  -H 'Sec-Fetch-Site: same-origin' --data-binary "@$T/login.json" "https://$C:8443/pcbk/api/auth/login"
rm -rf "$T"
```

Ожидается JSON учётки (`login`, `role`, `place`, …); `401 bad_credentials` — неверные данные, `429` — замок
входа. Подробности маршрутов входа/чата — [13-gateway.md](13-gateway.md) и [17-web-chat-and-edge.md](17-web-chat-and-edge.md).

## 5. Обновление и откат

### 5.1 Общий механизм

`deploy/README.md` и `deploy/ADMIN.md` описывают один и тот же приём для каждого выпуска, без исключений:

1. **Перед обновлением — резервная копия** (`core.db`, агенты, секреты, `.env`/`compose.yaml*`/`edge/`/белый
   список, разговоры спящих мест) — процедура и проверка целостности описаны в `deploy/README.md`, «Резервная
   копия и восстановление»; операционная сторона — [18-watchdog-and-ops.md](18-watchdog-and-ops.md).
2. **Перед копированием нового `compose.yaml` — копия прежнего**, с меткой предыдущего выпуска:
   `cp -p compose.yaml compose.yaml.<метка>`. То же — для шаблона `edge` (`edge/pcbk.conf.template.<метка>`),
   если он меняется, и для статики чата (`chat.<метка>` — уже само собой получается при атомарной подмене,
   раздел 4.7).
3. Новые образы — на сервер (раздел 3); **старый образ с сервера не удаляется** — именно он нужен для отката
   (`docker rmi` старого тега делать не нужно).
4. `.env` обновляется только если новый выпуск добавляет переменные — старый `.env` обычно продолжает работать
   (умолчания из кода покрывают отсутствующие переменные).
5. Пересоздание изменившихся служб — **только по именам**, никогда без имён и никогда `docker compose down`/
   `down -v` (снимает все рабочие места и их сети; `-v` к тому же удаляет том журнала сторожа):
   ```bash
   docker compose up -d --no-build --force-recreate core      # если менялся core
   docker compose up -d --no-build --force-recreate watchdog  # если менялся watchdog
   docker compose up -d --no-build --force-recreate edge      # если менялся шаблон edge
   ```
   Рабочие места с новым образом — «Пересоздание мест с сохранением томов» (раздел 6.3) — **не** `down`/`up`
   без имён и не удаление томов `pcbk-student-NN-*`.
6. Проверка — `curl -sk https://127.0.0.1:8443/status.json` → `stale: false`, плюс обычные строки страницы
   состояния.

**Откат** — симметричная операция: вернуть сохранённую копию `compose.yaml.<метка>` (и, если менялись, копии
шаблона `edge`/статики чата) на место, пересоздать те же службы с `--no-build` **без** пересборки образа —
Compose возьмёт тег, который указан в восстановленном `compose.yaml` (а он уже есть на сервере с прошлой
выкладки), и проверить `docker inspect -f '{{.Config.Image}}' pcbk-core` и `/status.json`. Тома данных
(`pcbk-core-data`, каталог агентов, тома мест) при этом не трогаются — старые образы читают те же базы и файлы,
если не меняли их схему несовместимо (в этом случае откат явно требует вернуть и соответствующий дамп из
резервной копии, раздел «Резервная копия и восстановление»).

**Запрещено навсегда:**

- `docker compose down` на сервере — снимает все рабочие места и их сети;
- `docker compose down -v` — вдобавок удаляет том журнала сторожа (`pcbk-watchdog-journal`); если журнал
  действительно нужно стереть — только явно: `docker volume rm pcbk-reserve_pcbk-watchdog-journal`;
- удаление томов `pcbk-student-NN-*`/`pcbk-core-data`/`pcbk-watchdog-journal` по отдельности, вне описанных
  команд;
- `systemctl restart docker` (гасит все контейнеры сервера сразу) — только `reload`.

### 5.2 Откат статики чата

Своя, более узкая версия того же приёма (раздел 4.7): прежний каталог `chat.prev` остаётся на диске после
подмены — откат — переименовать текущий в сторону и вернуть прежний:

```bash
cd /opt/pcbk-reserve/edge/static &&
{ [ ! -e chat.bad ] || { echo "chat.bad уже есть"; false; }; } &&
mv -T chat chat.bad && mv -T chat.prev chat
```

## 6. Частичные сборки

`core` — один процесс FastAPI, совмещающий все четыре роли (`data`, `llm`, `gateway`, `kb` — `build_roles` в
`core/pcbk_core/main.py`); отдельного переключателя «выключить роль целиком» в коде нет — поведение каждой роли
управляется своими переменными окружения, но
**секреты и bind-тома, которые `compose.yaml` объявляет для службы `core`, обязательны все сразу**: Compose
создаёт секрет из файла-источника при запуске контейнера и откажется поднимать `core`, если файла нет — даже
если соответствующая роль в этом частичном развёртывании не нужна. Поэтому во всех рецептах ниже нужен полный
набор секретов/каталогов из раздела 4.3–4.4 (можно — с нерабочими/пустыми значениями там, где роль реально не
используется), а не только те, что относятся к нужной роли.

### 6.1 (B) Только служба данных — MCP/HTTP для своего агента

Поднять только `core`, не трогая места, `edge`, `watchdog`, `sp-ro`/`sp-ctl`:

```bash
docker compose up -d --no-build core
```

Порт `8000` `core` не публикуется наружу (`compose.yaml` не задаёт `ports:` для этой службы) — действующий
способ достать до HTTP/MCP ручек, которым пользуется и сам администратор (`deploy/README.md`, «Служба данных»):
обратиться прямо на адрес `core` в сети `pcbk-egress` — **`172.31.250.82:8000`** — с той же машины (Docker
отвечает на адрес бриджа без публикации порта) или через SSH-туннель, если нужно с другой машины:

```bash
ssh -N -L 18000:172.31.250.82:8000 <сервер>
curl -s -H "Authorization: Bearer $(cat "$SD/ops.data-token")" \
  -X POST http://127.0.0.1:18000/api/data/catalog_search -d '{"query": "…"}'
```

Свой агент, который должен получить MCP-доступ (Streamable HTTP, без сессий, `POST /mcp`), нужно либо подключить
в ту же сеть Docker, что и `core` (`docker network connect pcbk-egress <контейнер агента>` — тогда адрес
`172.31.250.82:8000` доступен прямо), либо, для локальной разработки без лишних контейнеров, временно опубликовать
порт через отдельный файл `compose.override.yaml`:

```yaml
services:
  core:
    ports: ["127.0.0.1:8000:8000"]
```

```bash
docker compose -f compose.yaml -f compose.override.yaml up -d --no-build core
```

Токен — `ops.data-token` (администратор) или `student-NN.data-token` (конкретное место; для стороннего агента
логичнее завести новый id в `core-tokens`, скопировав формат «`<id> <sha256>`» вручную, так как команда
`accounts` работает только с ролями `student`/`teacher` шлюза, а не с токенами данных). Полный список
инструментов (`catalog_search`, `tag_now`, `tag_period`, `save_agent`), их аргументы, пределы и формат
ответа — [10-data-service.md](10-data-service.md).

### 6.2 (C) Чат + шлюз + места + LLM-прокси, без историана

Поднять всё, кроме реального подключения к историану:

В отличие от 6.1 (там поднимается только `core`), здесь дополнительно поднимаются `edge`, `sp-ro` и `sp-ctl` —
этим трём службам, помимо общего набора из 4.3–4.4, нужны ещё переменные `.env` раздела 4.2, которые
`compose.yaml` требует явно (`${VAR:?…}`) и без которых `docker compose config`/`up` откажется с текстом ошибки
`TLS_DIR не задан` или `DOCKER_GID не задан`:

- `TLS_DIR` — каталог с `cert.pem`/`key.pem`, подготовленный в разделе 4.1; монтируется в `edge` (том
  `${TLS_DIR:?TLS_DIR не задан}`) и в `watchdog` (том `${TLS_DIR:?TLS_DIR не задан}/cert.pem` — сверяет, что
  `edge` отдаёт именно этот сертификат);
- `DOCKER_GID` — gid группы `docker` хоста; `user: "65534:${DOCKER_GID:?DOCKER_GID не задан}"` у `sp-ro` и
  `sp-ctl` (доступ к `/var/run/docker.sock`).

Нужны и образы — `core`, `watchdog`, `student`, а также сторонние `nginx:1.30.5-alpine` и
`wollomatic/socket-proxy:1.13.1` (службы `edge`, `sp-ro`, `sp-ctl`), собранные и перенесённые по разделам 2–3:
флаг `--no-build` только запускает уже существующие образы, он их не собирает.

```bash
docker compose up -d --no-build core watchdog edge sp-ro sp-ctl
docker compose --profile students create --no-build $(printf 'student-%02d ' $(seq 1 11))
docker compose up -d --no-build watchdog
```

`DATA_DIR/whitelist.txt` и секрет `bdrv.env` всё равно обязаны существовать (bind-том и секрет Compose), но их
содержимое может быть нерабочим-заглушкой: тестовый стенд (`tests/integration/conftest.py`) именно так и делает
для изоляции от настоящего историана — адрес `BDRV_HOST` берётся из диапазона `TEST-NET-1` (RFC 5737,
недостижим в реальной сети, например `192.0.2.10`), подставляются заглушечные `BDRV_USER`/`BDRV_PW`, а
`whitelist.txt` строится с одним вымышленным именем (`20FAKE_001_PV`). При таком `bdrv.env` строка «Историан
БДРВ» на странице состояния показывает ошибку/недоступность, инструменты `catalog_search`/`tag_now`/`tag_period`
отвечают ошибкой историана, но это не мешает: входу, чату через места, LLM-прокси, шлюзу и кабинету
преподавателя — они не зависят от историана.

### 6.3 (D) Только LLM-прокси

Тот же единственный процесс `core`, что в B/C — своего отдельного образа или профиля у роли `llm` нет. Минимальный
набор для работы `/llm/*`:

```bash
docker compose up -d --no-build core
```

С настоящими секретами `openrouter.key`/`openrouter-mgmt.key` и таблицей `llm-tokens` (раздел 4.3); `DATA_DIR`,
`bdrv.env`, `AGENTS_DIR`, `LESSONS_DIR` и все 11 `student-NN.pw` — всё равно обязаны существовать на диске
(как в разделе 6.2 — можно заглушками), иначе `docker compose up core` не создаст контейнер вовсе. Сузить набор
разрешённых моделей или остановить прокси — переменными `.env` (`LLM_MODELS`, `LLM_STATE`), без пересборки
образа, пересоздание — `docker compose up -d --no-build --force-recreate core`. Вызов — `POST /llm/...`
(OpenAI-совместимый вход) с заголовком токена места/`ops`; полный разбор ручек, бюджета и моделей —
[12-llm-proxy.md](12-llm-proxy.md).

## 7. Тестовый контур `compose.test.yaml`

`compose.test.yaml` — не отдельная среда, а **наложение** (`docker compose -f compose.yaml -f compose.test.yaml`)
поверх продуктивной `compose.yaml`, под отдельным именем проекта `pcbk-test` (чтобы не задеть тома и сети
выкладки — имя проекта входит в префикс безымянных томов, раздел 4.6). Отличия от продуктивной компоновки:

| Что | Продуктив (`compose.yaml`) | Тестовый контур (`compose.test.yaml`) |
|---|---|---|
| Порт `edge` | `8443:8443` | `127.0.0.1:18443:8443` (боевой порт на машине разработчика часто занят) |
| Рантайм мест | `runsc` (gVisor) | `runc` (gVisor на машине разработчика не нужен) |
| OpenRouter | настоящий, по ключу | двойник `fake-openrouter` (только stdlib, `core/tests/Dockerfile.fake`), в сети `pcbk-egress` на `172.31.250.84:8080`; `core` получает `OPENROUTER_BASE_URL` на него |
| Опрос свежести историана | `FRESH_POLL_S` из `.env`, по умолчанию `30` (секунд) | `FRESH_POLL_S=5` — опрос за секунды, не за полминуты |
| Опрос OpenRouter (`/key`) | `LLM_POLL_S` в `compose.yaml` не задаётся вовсе (нет такой переменной у продуктивной службы `core`) | `LLM_POLL_S=5` — задаётся только тестовым контуром |
| Общий бюджет LLM | умолчание кода (1000 $) или `.env` | `LLM_TOTAL_USD=1100` по умолчанию — те же 11 мест по 100 $, что на сервере |
| `core` | обычный `pcbk_core.main` | обёртка `twin_core` (`command: ["python", "-m", "twin_core"]`), с опциональным двойником историана `PCBK_TWIN_HISTORIAN` (путь к JSON-фикстуре) |
| Двойники памяти | читает `/proc` хоста | `core`/`watchdog` читают файлы каталога `PCBK_TEST_MEM` вместо `/proc/meminfo`,`/proc/vmstat`,`/proc/pressure/memory` (фикстуры подставляют конкретные числа свободной памяти/swap/давления/`oom_kill`) |
| Статика `edge` | `edge/static` (собранная статика чата) | `${PCBK_TEST_STATIC}` — отдельная копия со своей тестовой сборкой чата |
| Доп. контейнер `foreign` | нет | `busybox:1.38.0`, без сети — мишень для проверки, что прокси сокета отвечает чужому контейнеру `403` |

Переменные, помеченные выше `:?` (обязательные без умолчания) — `PCBK_TEST_STATIC`, `PCBK_TEST_MEM` (плюс
обычные обязательные `TLS_DIR`, `DATA_DIR`, `AGENTS_DIR`, `SECRETS_DIR`, `LESSONS_DIR` из раздела 4.2) — их
готовит фикстура тестов, а не человек руками.

На практике контур **не поднимается вручную** отдельной командой `docker compose -f … up` — его поднимает,
настраивает переменные и чистит за собой тестовая фикстура `tests/integration/conftest.py` (класс `Stack`),
единая для всего набора `tests/integration/*.py`. Фикстура сама: строит тестовую сборку статики чата
(`build_test_static`) и файлы-двойники `/proc` (`make_test_mem`) до первого `up`, пишет временный `test.env` со
всеми нужными переменными (включая `SECRETS_DIR`/`AGENTS_DIR`/`DATA_DIR` во временных каталогах, тестовые
ключи/токены — заглушки, не настоящие), заводит тестовый белый список (`20FAKE_001_PV`) и тестовый `bdrv.env` на
недостижимый адрес `TEST-NET-1`, и вызывает ровно то наложение, которое нужно воспроизвести руками при отладке:

```bash
docker compose -p pcbk-test --env-file <test.env> \
  -f compose.yaml -f compose.test.yaml --profile students <команда…>
```

Точная команда запуска всего набора `tests/integration` — в [19-tests-and-bench.md](19-tests-and-bench.md).
Явно задокументирован в `deploy/README.md` только вызов одного
модуля изоляции с машины разработчика, с реальным сервером (а не с этим локальным контуром) в качестве цели:
`PCBK_LIVE_ISO=<путь к JSON-договору> PCBK_SHOTS=<каталог снимков> uv run --python 3.12 --with pytest pytest -q
-rP tests/integration/test_isolation_live.py`, из корня репозитория; на локальном контуре `compose.test.yaml` тот
же модуль идёт в общем прогоне `tests/integration` вместе с остальными. По завершении
(в том числе при сбое) фикстура сама гасит стенд `-p pcbk-test` (`down -v`) и удаляет созданные ею тестовые тома
мест по метке — это безопасно только потому, что имя проекта (`pcbk-test`) и метки томов отдельные от
продуктивных (`pcbk-reserve`, раздел 4.6). Что именно проверяет каждый тестовый модуль и как писать новые —
[19-tests-and-bench.md](19-tests-and-bench.md).
