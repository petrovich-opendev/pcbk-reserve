# Выкладка резервного стенда — для администратора

Сервер ПЦБК, пользователь с доступом к Docker (группа `docker`). `sudo` нужен
только один раз — для gVisor, каталога `/opt/pcbk-reserve` и копии
сертификата. Dify не трогается: ни одного изменения в `/opt/dify`.

## Один раз (с `sudo`)

1. gVisor release-20260921.0: тарбол `gvisor.tar.zstd` и `.sha512` с
   `storage.googleapis.com/gvisor/releases/release/20260921.0/x86_64/`,
   `sha512sum -c`; `cp -a /etc/docker/daemon.json /etc/docker/daemon.json.pre-runsc`;
   `tar --zstd -xf gvisor.tar.zstd -C /usr/local/bin`;
   `runsc install -- --platform=systrap`;
   `dockerd --validate --config-file /etc/docker/daemon.json`;
   **`systemctl reload docker`** — никогда `restart`: restart гасит все
   контейнеры, включая Dify.
2. `install -d -o <пользователь> -g <группа> /opt/pcbk-reserve`;
   `install -d -o root -g root -m 0755 /opt/pcbk-reserve/tls`;
   сертификат и ключ Dify (`/opt/dify/docker/nginx/ssl/`, имена — в
   `/opt/dify/docker/.env`, `NGINX_SSL_CERT_*`) — `install -o root -g root`
   в `tls/cert.pem` (0644) и `tls/key.pem` (0600).

## Выкладка и обновление

1. Образы — с машины разработчика, без выхода сервера в интернет:
   `docker save <образ> | gzip | ssh <сервер> 'gunzip | docker load'`;
   список сторонних образов — `deploy/images.lock` (строки `# test` не
   возить, кроме `curlimages/curl` — он остаётся на сервере как инструмент
   проверок); свои образы — `pcbk-reserve/watchdog:<день>`, `pcbk-reserve/core:<день>` и
   `pcbk-reserve/student:<день>` (собирать из чистого дерева, `git archive`). Сверка: слои
   `docker image inspect -f '{{json .RootFS.Layers}}'` совпадают.
   **Образ — из ветки своего дня.** Образ для выкладки дня собирается из ветки или тега этого
   дня (отдельный `git worktree` на её голове), а не из рабочего дерева более позднего дня:
   иначе, например, под тегом `:d4b` на сервер уедет код Д5, а откатного образа Д4б не станет.
2. `compose.yaml` и каталог `edge/` — в `/opt/pcbk-reserve` (`scp`; без
   удаления: `tls/`, `.env`, `secrets/`, `agents/` не трогаются). Перед
   копированием — на сервере копия для отката: `cp -p compose.yaml compose.yaml.<прошлый день>`
   (перед Д4б — `cp -p compose.yaml compose.yaml.d4a`). Права:
   `chmod -R o+rX edge` — nginx читает файлы от другого пользователя.
3. `.env` — по `deploy/env.example`, `chmod 600`.
4. `cd /opt/pcbk-reserve && docker compose up -d --no-build core watchdog edge sp-ro sp-ctl`
   — **с именами служб**: рабочие места (с Д2) создаются отдельно и не
   стартуют сами (серверный слой `core` — с Д3а).
   **Порядок `core` и сторожа, когда день добавляет ключ в `/health/gateway`.** Сторож `:d5a` ключи
   Д5б (`refused_1h`, `streams`, `stream_broken_1h`, `upstream_failed_1h`) понимает, а прочие лишние
   ключи пропускает, поэтому Д5б выкладывается обычным порядком `core` → `watchdog` → `edge` (Б6,
   шаг 2). Сторож с `:d5b` строже: любой ключ сверх известных — «шлюз ответил не по договору». Значит,
   в днях после Д5б, которые добавляют ключ, сторож выкладывается раньше `core`: `docker compose up -d
   --no-build watchdog`, дождаться `stale: false`, затем `docker compose up -d --no-build core`.
5. Проверка: `curl -sk https://127.0.0.1:8443/status.json` — `stale: false`.

## Статика чата (с Д6а)

Чат — статика `edge` в `edge/static/chat/`: `index.html`, `assets/*` и опись `build.json` (суммы файлов
и метка сборки). В git её нет. На сервер уходит только собранный каталог — ни `web/`, ни `node_modules`.
Каталог `edge/` в шаге 2 «Выкладки» — из `git archive`, без `chat/`: чат выкладывается только так.

1. Сборка — на машине разработчика (Node 22.x), из закреплённого коммита выкладки, в `$JOB`:
   ```bash
   C=$(git rev-parse --short=12 <коммит выкладки>) && B=$JOB/web-$C && mkdir -p "$B" &&
   git archive "$C" web edge | tar -x -C "$B" &&
   (cd "$B/web" && npm ci --ignore-scripts && PCBK_WEB_OUT="$B/chat" PCBK_BUILD="$C" npm run build)
   ```
   `npm run build` сам проверяет сборку (`check-dist`: CSP, ссылки, опись и суммы); метка сборки — `$C`.
2. Копия — в соседний каталог `chat.new`, не поверх рабочего. Остался `chat.new` от прерванной
   выкладки — сначала удалить его (только его):
   `scp -r "$B/chat" <сервер>:/opt/pcbk-reserve/edge/static/chat.new`, на сервере
   `chmod -R o+rX /opt/pcbk-reserve/edge/static/chat.new` — nginx читает файлы от другого пользователя.
3. Сверка на сервере — суммы по описи, ни лишних, ни недостающих файлов, метка страницы — та же; расхождения
   называются поимённо:
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
4. Переименование; прежний каталог остаётся для отката (`<прошлый день>` — например, `d6a`). `mv -T` не
   вкладывает каталог внутрь уже существующего, а занятое имя отката останавливает выкладку:
   ```bash
   cd /opt/pcbk-reserve/edge/static &&
   { [ ! -e chat.<прошлый день> ] || { echo "chat.<прошлый день> уже есть"; false; }; } &&
   { [ ! -e chat ] || mv -T chat chat.<прошлый день>; } && mv -T chat.new chat
   ```
   `edge` не перезапускается: nginx читает файлы при каждом запросе. Между двумя `mv` чат на миг
   отдаёт 404 — такой ответ не кэшируется (`no-store`), а ассеты с хешем в имени кэшируются на год.
5. Проверка: `curl -sk https://127.0.0.1:8443/chat/ | grep -o 'pcbk-build" content="[^"]*"'` — метка `$C`;
   на `/status` строка «Веб-чат» — «страница чата отдаётся», сборка `$C` (сторож смотрит через 8080 `edge`).

**Откат** — с той же защитой имени:
```bash
cd /opt/pcbk-reserve/edge/static &&
{ [ ! -e chat.bad-<день> ] || { echo "chat.bad-<день> уже есть"; false; }; } &&
mv -T chat chat.bad-<день> && mv -T chat.<прошлый день> chat
```
Первой выкладке (Д6а) возвращать нечего: откат чата — это откат всего `edge/` (страница входа Д6а
ведёт в `/chat/`), каталог `chat` уходит в сторону, строка «Веб-чат» краснеет.

## Рабочие места (с Д2)

1. Секреты — на сервере, в чат и git не попадают:
   ```bash
   cd /opt/pcbk-reserve && SD=$(grep '^SECRETS_DIR=' .env | cut -d= -f2) && AD=$(grep '^AGENTS_DIR=' .env | cut -d= -f2) &&
   [ -n "$SD" ] && [ -n "$AD" ] && umask 077 && install -d -m 0700 "$SD" "$AD" &&
   for n in $(seq -w 1 10); do
     for s in pw llm-token; do f=$SD/student-$n.$s
       [ -e "$f" ] || { openssl rand -hex 24 | tr -d '\n' > "$f"; chmod 0444 "$f"; }   # не перезаписывать
     done
     install -d -m 0755 "$AD/student-$n"
     for k in state work; do docker volume create --label pcbk.role=student "pcbk-student-$n-$k" >/dev/null; done
   done
   ```
2. Места создаются, но не стартуют:
   `docker compose --profile students create --no-build $(printf 'student-%02d ' $(seq 1 10))`,
   затем `docker compose up -d --no-build watchdog` — в этом порядке, иначе
   на странице будут десять «нет контейнера».
3. Вручную: `docker start pcbk-student-NN` / `docker stop pcbk-student-NN`
   (с Д5 это делает шлюз при входе и после 30 минут простоя).
4. Образ места собирается из чистого дерева (`git archive`): `COPY config/`
   заберёт в образ любой лишний файл из рабочей копии.

### Токены данных мест и `core-tokens` (с Д4б)

У каждого места три секрета-файла: пароль OpenCode `student-NN.pw`, токен
LLM-прокси `student-NN.llm-token` и токен службы данных
`student-NN.data-token` (в месте — `/run/secrets/data-token`, заголовок MCP
`pcbk`). Все — 48 шестнадцатеричных знаков, `0444` в каталоге `0700`, только
на сервере, при повторе не перезаписываются. Без файла `data-token` место не
создаётся (секрет Compose), а образ `:d4` без него падает в цикле: ссылка
`{file:…}` делает конфигурацию неверной. Хеши токенов данных `ops` и мест
лежат в `core-tokens`, хеши токенов LLM — в `llm-tokens` (Д4а); сами токены
ни в чат, ни в git, ни в вывод команд не идут.

Каталог секретов — всегда `SECRETS_DIR` из `.env` выкладки. Таблица хешей
собирается, только если каждый из одиннадцати файлов токенов есть и не пуст:

```bash
cd /opt/pcbk-reserve && SD=$(grep '^SECRETS_DIR=' .env | cut -d= -f2) && [ -n "$SD" ] && cd "$SD" && umask 077 &&
for n in $(seq -w 1 10); do f=student-$n.data-token
  [ -e "$f" ] || { openssl rand -hex 24 | tr -d '\n' > "$f"; chmod 0444 "$f"; }   # не перезаписывать
done
ok=1; for f in ops.data-token $(printf 'student-%02d.data-token ' $(seq 1 10)); do [ -s "$f" ] || { echo "нет или пуст: $f"; ok=0; }; done
[ "$ok" = 1 ] && for f in ops.data-token student-*.data-token; do printf '%s %s\n' "${f%.data-token}" "$(sha256sum < "$f" | cut -c1-64)"; done > core-tokens.new &&
  chmod 0444 core-tokens.new && mv core-tokens.new core-tokens
wc -l < core-tokens; awk 'NF!=2||length($2)!=64' core-tokens | wc -l; stat -c '%a %s' student-*.data-token | sort -u
cat student-*.data-token student-*.llm-token student-*.pw ops.*-token | fold -w48 | sort -u | wc -l
```

Ожидается: ни одной строки «нет или пуст», затем `11`; `0` — все строки вида
«`<id> <sha256>`»; `444 48`; `32` различных значения (три разных секрета у
каждого места и два токена `ops`).

Таблица хешей токенов LLM `llm-tokens` (Д4а: `ops.llm-token` и десять
`student-NN.llm-token`, тот же формат) собирается так же — в том же каталоге:

```bash
ok=1; for f in ops.llm-token $(printf 'student-%02d.llm-token ' $(seq 1 10)); do [ -s "$f" ] || { echo "нет или пуст: $f"; ok=0; }; done
[ "$ok" = 1 ] && for f in ops.llm-token student-*.llm-token; do printf '%s %s\n' "${f%.llm-token}" "$(sha256sum < "$f" | cut -c1-64)"; done > llm-tokens.new &&
  chmod 0444 llm-tokens.new && mv llm-tokens.new llm-tokens
wc -l < llm-tokens; awk 'NF!=2||length($2)!=64' llm-tokens | wc -l
```

Ожидается `11` и `0`. `core` читает `core-tokens` и `llm-tokens` только при
старте (привязанный файл — прежний inode): после нового файла —
`docker compose up -d --no-build --force-recreate core`.

`core` стоит в каждой сети места на `.2` (у мест — `core` в `extra_hosts`) с
`net.ipv4.ip_forward=0`. Проверка: `docker inspect -f '{{len .NetworkSettings.Networks}}' pcbk-core` → `13`
(с Д5а — ещё `pcbk-ctl`: `core` — клиент `sp-ctl`).

### Пересоздание мест с сохранением томов

Новый образ места, новые секреты или правка блока места в `compose.yaml`:

1. `docker ps -a --filter label=pcbk.role=student --format '{{.Names}} {{.State}}'` —
   пересоздание работающего места рвёт разговор студента: сначала выяснить,
   кто его поднял, и дождаться конца.
2. `docker compose --profile students create --force-recreate --no-build $(printf 'student-%02d ' $(seq 1 10))`
   (или имена нужных мест). Тома мест внешние — `create --force-recreate` их
   не трогает: разговоры и работы студентов остаются. Места после этого спят.
3. **Не** `docker compose down`, **не** `up` без имён служб, **не** удаление
   томов `pcbk-student-NN-*`.

### Новый студент
- **Смена студента в слоте 01–10:** новые файлы `student-NN.pw`,
  `student-NN.llm-token` и `student-NN.data-token` (старые удалить) — к
  трём секретам места с Д4б добавился токен данных; затем заново
  `llm-tokens` и `core-tokens` (обе команды — выше) и
  `docker compose up -d --no-build --force-recreate core`; прежний каталог
  агентов места переименовать и сохранить (удалять — только по решению
  владельца), создать пустой;
  старые тома места — только по решению владельца; `docker compose
  --profile students create --force-recreate --no-build student-NN`.
- **Место 11 и дальше:** правка регулярок `sp-ro`/`sp-ctl`, подсеть N, блок
  в `compose.yaml`, строка в `watchdog/components.json`, тесты границ
  диапазона, пересборка сторожа.

## Что открывается наружу

- Порт **8443** контейнера `edge` — Docker публикует его **в обход ufw**.
  Закрыть: `docker compose stop edge`.
- `edge` — единственный контейнер с путём к хосту; остальные сети внутренние
  с изолированным шлюзом.
- **Пределы `edge` под `/pcbk/api/oc/`** (Д5б): не больше 10 одновременных
  запросов на cookie сессии и 64 всего, не чаще 20 в секунду на cookie
  (запас 200); тело обрывается, если между его кусками проходит больше 10 с
  (`client_body_timeout` — срок между чтениями, а не на всё тело); сверх —
  503 «Слишком много одновременных запросов». Ручки места
  (`/pcbk/api/workplace`, `…/wake`) — не чаще 5 в секунду на cookie (запас
  20), отказ тот же. Запросы без cookie и с cookie не того вида делят одну
  корзину. У учётки — не больше трёх живых сессий: четвёртый вход закрывает
  самую старую.
- **Пределы `edge` у входа и отчётов** (Д6б): вход и выход
  (`/pcbk/api/auth/login`, `…/logout`) — не чаще 5 в секунду с адреса (запас
  20), сверх — 429 «Слишком много попыток входа». «Кто я» и подтверждение
  предупреждения (`/pcbk/api/auth/me`, `…/notice`) — своя корзина, не входа: на
  cookie сессии, а без неё — на адрес, не чаще 5 в секунду (запас 40 — класс из
  30 за одним NAT открывает страницу входа разом); сверх — 503 «Слишком много
  одновременных запросов» (форма входа при этом видна). Отчёты браузеров
  о молчании чата (`/pcbk/api/web/report`) — не чаще раза в секунду на cookie
  (запас 10), тело до 1 КиБ (больше — 413), сверх предела — 503 того же вида;
  ответ отчёта чат не показывает.
- **Остаточный риск — занять общий предел 64.** Посылка «8443 доступен только
  из сети ПЦБК» его не смягчает: студенты сами в сети ПЦБК. Один студент
  своими настоящими сессиями занимает не больше 3 × 10 = 30 мест из 64;
  поддельные cookie правильного вида (43 знака) дают каждая свою корзину на
  10, и их хватит, чтобы занять все 64 (тела без учётки в core не уходят, но
  чат класса получит 503). Тело, у которого байт приходит чаще раза в 10 с,
  срок между чтениями не обрывает: такое соединение держит слот сколько
  угодно. Окончательно — предел по адресу клиента после замера
  `$remote_addr` на сервере (Б7).

## Замена сертификата

Сертификат смонтирован в edge и сторожа файлами: после копии нового
`cert.pem`/`key.pem` в `tls/` — `docker compose restart edge watchdog`.
Сторож предупреждает за 14 дней до окончания срока и показывает сбой, когда
срок истёк.

## Откат

- **`docker compose down` на сервере не выполнять** — он снимает все десять
  мест и их сети. **`down -v` запрещён** — он удаляет том журнала сторожа;
  журнал удаляется только явно: `docker volume rm pcbk-reserve_pcbk-watchdog-journal`.
- Службы снимаются только по именам: `docker compose rm -sf <службы>`;
  Д1 — `docker compose stop edge watchdog sp-ro sp-ctl` /
  `docker compose rm -sf edge watchdog sp-ro sp-ctl`.
- Места Д2 (только до первого входа студента):
  `docker compose rm -sf $(printf 'student-%02d ' $(seq 1 10))`;
  `docker network rm $(printf 'pcbk-stu-%02d ' $(seq 1 10))`; тома — только
  по именам `pcbk-student-NN-*`; вернуть `compose.yaml.d1` и
  `docker compose up -d --no-build watchdog` (образ `:d1`).
- Д4б (места `:d4` и `core` в сетях мест) — назад к Д4а, тома и токены
  сохраняются:
  1. вернуть `compose.yaml.d4a` (копия `cp -p compose.yaml compose.yaml.d4a`
     перед выкладкой Д4б, шаг 2 «Выкладки»);
  2. `docker compose up -d --no-build --force-recreate core watchdog` —
     `core` и сторож на `:d4a`, `core` выходит из сетей мест; `sp-ro` не
     пересоздаётся (лишнее для `:d4a` чтение inspect `pcbk-core` безвредно);
  3. кто сейчас работает:
     `docker ps -a --filter label=pcbk.role=student --format '{{.Names}} {{.State}}'` —
     пересоздание работающего места рвёт разговор: выяснить, кто его поднял,
     и дождаться конца;
  4. `docker compose --profile students create --force-recreate --no-build $(printf 'student-%02d ' $(seq 1 10))`
     — места на `:d2`, тома сохраняются, места спят;
  5. токены данных `student-NN.data-token` и `core-tokens` не удаляются.
- Д6б — назад к Д6а (места не трогаются: образ места в Д6б тот же, `student:d6a`):
  1. вернуть `compose.yaml.d6a` и шаблон `edge` Д6а (копии перед выкладкой Д6б),
     `docker compose restart edge`;
  2. `docker compose up -d --no-build core watchdog` — `core` и сторож на `:d6a`
     (образы `:d6a` остаются на сервере); `core.db` с таблицей `gw_notices` Д6б
     `core:d6a` открывает как есть — лишняя таблица ему не мешает;
  3. статика чата — `chat.d6a` на место (раздел «Статика чата», откат) и
     `edge/static/login.js` Д6а.

  Откат только статики и `login.js` при `core:d6b` снимает предупреждение при
  первом входе: чат Д6а поле `notice` не знает, страница входа Д6а будит место
  сразу после входа, а шлюз сам до подтверждения не отказывает (как между
  шагами 3 и 4 выкладки Д6б, preflight Н19). Откатывать их — только вместе с
  `core` или зная это.

  Пока стоит `core:d6a` (или `:d5b`), логины не пересоздавать: `accounts remove`
  старого `core` строк `gw_notices` не знает и не стирает, и учётка, заведённая
  заново с тем же логином, после возврата на `core:d6b` унаследует чужое
  подтверждение — новый студент предупреждения не увидит. Если пересоздали —
  после возврата на `core:d6b` стереть подтверждения этого логина:
  ```bash
  docker exec pcbk-core python -c 'import sqlite3, sys
  from pcbk_core.settings import Settings
  db = sqlite3.connect(Settings.from_env().DB_PATH, timeout=5)
  with db: n = db.execute("DELETE FROM gw_notices WHERE login = ?", (sys.argv[1],)).rowcount
  print("стёрто подтверждений:", n)' <логин>
  ```
  (`accounts list` после этого — «нет» в последнем столбце).
- Тома мест, `secrets/` и `agents/` удаляются только до первого входа
  студента и только по именам: после — там разговоры и агенты студентов.
- `rm -rf /opt/pcbk-reserve` (с `sudo`) — только до первого входа. После —
  удалять лишь `tls/`, `compose.yaml` и `edge/`; **`secrets/` и `agents/`
  остаются**.
- gVisor: остановить контейнеры с `runsc`; вернуть
  `/etc/docker/daemon.json.pre-runsc` на место; `systemctl reload docker`;
  `rm -rf /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin`.
