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
ведёт в `/chat/`), каталог `chat` уходит в сторону, строка «Веб-чат» краснеет (раздел «Откат», пункт
«Д6а — назад к Д5б»).

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
- **Первая выдача места** (с Д5а) — учётка с местом:
  `accounts add --login L --role student --place N` (раздел «Вход и рабочие
  места (с Д5а)»); файлы `student-NN.pw`, `student-NN.llm-token` и
  `student-NN.data-token` при этом не меняются.
- **Смена студента в слоте 01–10** — с Д5а только по процедуре владельца
  (решение к Д10): место закреплено за первой учёткой навсегда, и команда
  администратора место, закреплённое за другим логином, не выдаёт, а частичная
  смена (новые файлы паролей при прежних томах) оставила бы новому студенту
  разговоры и агентов прежнего. Уже известные шаги смены: новые файлы
  `student-NN.pw`, `student-NN.llm-token` и `student-NN.data-token` (старые удалить) — к
  трём секретам места с Д4б добавился токен данных; затем заново
  `llm-tokens` и `core-tokens` (обе команды — выше) и
  `docker compose up -d --no-build --force-recreate core`; агентов прежнего
  студента — в архив (с Д7а):
  `docker exec pcbk-core python -m pcbk_core.agents.admin archive --place NN --yes`
  — все файлы каталога места уходят в `/srv/agents/archive/student-NN-<время>/`
  (удалять архив — только по решению владельца). Сам каталог места
  остаётся, файлов в нём нет; остаются лишь пустые подкаталоги, если их
  заводили вручную. Сверка —
  `docker exec pcbk-core python -m pcbk_core.agents.admin list --place NN`:
  строк агентов нет, последняя строка «расхождений нет». Иначе в ней
  названо, что держит место (файлы без учёта, ссылки, прочие файлы), —
  `archive` повторить.
  С хоста каталог агентов не переименовывать: после смены владельца (Д7а)
  корень `0700` у uid 10003, а каталог, созданный с хоста, `core` не
  запишет. До выкладки Д7а и после отката к Д6б (раздел «Откат») —
  по-старому: прежний каталог агентов места переименовать и сохранить,
  создать пустой;
  старые тома места — только по решению владельца; `docker compose
  --profile students create --force-recreate --no-build student-NN`.
- **Место 11 и дальше:** правка регулярок `sp-ro`/`sp-ctl`, подсеть N, блок
  в `compose.yaml`, строка в `watchdog/components.json`, тесты границ
  диапазона, пересборка сторожа; с Д5а — и правка `core`: `PLACES` в
  `core/pcbk_core/` — `gateway/accounts.py`, `gateway/places.py`,
  `agents/store.py` и `main.py` (там же направления к местам — `egress_targets`).

### Освобождение места пробной учётки

Места 09 и 10 до пилота берут пробные учётки живых проверок (`dN-probe-*`);
после каждой проверки место освобождается — пилот получает все 10 мест.
`NN` — номер места, `L` — логин пробы; команды `accounts` и `agents.admin` —
через `docker exec pcbk-core python -m …`.

0. Место к этому часу обычно спит: `docker start pcbk-student-NN`, затем
   помощник `probe NN` Д2 → `200`.
1. Разговоры пробы — `DELETE /session/<id>` через API места (помощник `oc`
   Д2, пароль места — файлом) по списку id, который записал прогон; затем
   `GET "/experimental/session?roots=true&archived=true"` → `[]`. Не пусто —
   разговоры прежних дней: место выдаётся последним, чужие разговоры не
   удаляются.
2. `python -m pcbk_core.gateway.accounts disable --login L` — до архива: иначе
   проба успела бы сохранить нового агента после него.
3. Агенты места (с Д7а) — в архив:
   `python -m pcbk_core.agents.admin archive --place NN --yes`. Уходят все
   файлы каталога места — агенты `**/*.md`, файлы без строки учёта,
   символьные ссылки, временные файлы оборванной записи — в
   `/srv/agents/archive/student-NN-<время>/`; строки учёта получают
   `archived`, место встаёт в очередь перезагрузки (работающее место
   перезагрузится за секунды, спящее — после пробуждения). Каталог места
   остаётся, файлов в нём нет — только пустые подкаталоги, если их
   заводили вручную.
   Сверка — `python -m pcbk_core.agents.admin list --place NN`: строк
   агентов нет, последняя строка «расхождений нет». Без этого шага
   `release-place` отказывает: «на месте остались агенты — сначала …».
   С Д7б служба уносит в карантин (`/srv/agents/quarantine/student-NN/`)
   файл агента, сломавший рабочее место: файла в каталоге места уже нет, но
   строка учёта такого агента (`quarantined`) держит место, пока её не
   унесёт `archive`; сами файлы карантина `archive` не трогает.
4. `python -m pcbk_core.gateway.accounts remove --login L --yes`, затем
   `release-place --place NN --yes`.
5. `accounts list` — строки «место NN» нет; место остановлено
   (`docker stop pcbk-student-NN`).

## Служба данных (с Д3а)

- **Учётка историана** — копия файла Dify, содержимое не выводить:
  `install -m 0444 /opt/dify/scripts/.bdrv.env /opt/pcbk-reserve/secrets/bdrv.env`.
  Сменили пароль в Dify — повторить `install` и `docker compose restart core`:
  иначе защёлка входа держит строку «Историан БДРВ» красной, а служба больше
  не пробует входить (учётка общая с Dify).
- **Белый список** — правило `d3-1`: участок 20–25 по правилам 1–6 плюс имена
  из `data/whitelist.extra` (имена без цифры в начале — группы качества,
  общие по машине, узлы участка). Файл `data/whitelist.txt` (`0444`) строит
  построитель тем же сетевым путём, что служба:
  `docker run --rm --network bridge --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v /opt/pcbk-reserve/secrets/bdrv.env:/run/secrets/bdrv.env:ro -v /opt/pcbk-reserve/data:/data pcbk-reserve/core:d3a python -m pcbk_core.data.build_whitelist --out /data/whitelist.txt --extra /data/whitelist.extra`,
  затем `docker compose restart core`. Вывод построителя — только числа.
  Вариант «прежний список плюс правила 4–6» — тот же вызов с
  `--base /data/prior.txt`. Имена других участков в список не входят (решение
  владельца 30.09); добавить имя — строкой в `whitelist.extra` и пересборкой.
- **Сеть выхода** `pcbk-egress` (172.31.250.80/28, мост `pcbk-egress`) — только
  у `core`. Остаточный риск Д3а: охраны выхода в процессе до Д3б нет, и на
  уровне сети из `core` открыт любой адрес, включая сервер через шлюз моста.
- **Пороги свежести** — в `.env` (`HIST_WARN_S`, `HIST_FAIL_S`, `HIST_STALE_S`,
  `CATALOG_DEADLINE_S`); по пробе 30.09 оставлены умолчания 300 / 900 / 140 /
  60. Метки `Live` историана опережают его часы примерно на 46 с — это
  нормально, служба считает возраст от роста метки.
- **Откат Д3а:** `docker compose rm -sf core`; `docker network rm pcbk-egress`;
  вернуть `compose.yaml.d2` (и `.env.d2`); `docker compose up -d --no-build watchdog`
  (образ `:d2`).

## LLM-прокси (с Д4а)

Роль `llm` в `core`: рабочие места ходят к модели только через неё, каждое со своим токеном; ключ
OpenRouter есть только у `core`. Строка страницы — «LLM-прокси и бюджет» (`note` — «расход $X из $1000»).

- **Ключи OpenRouter — файлами, кладёт владелец** в своей SSH-сессии, чтобы ключ не прошёл через чат:
  `openrouter.key` и `openrouter-mgmt.key` в каталоге секретов (`SECRETS_DIR` из `.env`), `0444` в каталоге
  `0700`. Ключ — отдельный для резервного контура, с лимитом $1000 без сброса (в кабинете OpenRouter: limit
  1000, reset — never): это второй стоп, не зависящий от журнала прокси. Ключ управления необязателен — без
  него страница не видит остатка счёта; пустой файл значит «ключа нет». Без любого из двух файлов
  `docker compose` не создаст `core`: оба — секреты Compose.
  ```bash
  cd /opt/pcbk-reserve && SD=$(grep '^SECRETS_DIR=' .env | cut -d= -f2) && [ -n "$SD" ] && cd "$SD" && umask 077
  rm -f openrouter.key && read -rs k && printf '%s' "$k" > openrouter.key && unset k && chmod 0444 openrouter.key
  # ключ управления — так же; если его нет — пустой файл:
  [ -e openrouter-mgmt.key ] || { : > openrouter-mgmt.key; chmod 0444 openrouter-mgmt.key; }
  stat -c '%a %s %n' openrouter.key openrouter-mgmt.key; grep -c '^sk-or-' openrouter.key
  ```
  Ожидается: `444` у обоих файлов, размер ключа больше 0; `1`. Ключ не выводится ни на экран, ни в журналы,
  ни в `docs/checks`; проверка «ключа нет в `docker inspect pcbk-core`» — только `grep -c -F -f <файл ключа>`
  с положительным контролем.
- **Смена ключа** — `rm -f`, запись (команда выше), затем `docker compose up -d --no-build --force-recreate core`:
  файл `0444` не перезаписывается, ключ читается при старте, а привязанный файл секрета — прежний inode (без
  `--force-recreate` при прежнем окружении контейнер не пересоздаётся). Сверка с `/key` после смены начинается
  заново: новая точка отсчёта у нового отпечатка ключа, прошлый расход нового ключа в бюджет не входит.
- **`LLM_KEY_DEDICATED`** в `.env`: пусто или `on` — ключ только у резерва, расход сверяется с `/key`; `off` —
  ключ общий (например, с Dify-стендом): сверки нет, иначе траты Dify остановили бы прокси раньше срока.
- **Токены LLM** — `student-NN.llm-token` (Д2) и `ops.llm-token` (администратор, `0400`); хеши — в
  `llm-tokens`, команда — раздел «Токены данных мест и `core-tokens` (с Д4б)». Токен `ops` заводится один раз,
  в каталоге секретов:
  `[ -e ops.llm-token ] || { openssl rand -hex 24 | tr -d '\n' > ops.llm-token; chmod 0400 ops.llm-token; }`.
  Токен данных к `/llm` не подходит, токен LLM к `/api/data` и `/mcp` — тоже. Новый токен места — файл, заново
  `llm-tokens`, затем `docker compose up -d --no-build --force-recreate core`.
- **Деньги.** Доля каждого вызывающего — $100 (доля `ops` — `LLM_OPS_SHARE_USD`, пусто — 100), общий бюджет —
  $1000 с жёстким стопом: сверх — отказ 402. Вызов без известной цены стоит в долях и в бюджете по верхней
  оценке, настоящая цена её заменяет. Строка — «внимание» на 50, 80 и 95 % бюджета, «сбой» на 100 %.
- **Сбой журнала расходов — отказ** (Д4а-R19, «деньги важнее доступности»): строка вызова не легла на диск за
  2 с — вызов получает 503 `pcbk_ledger` («Учёт расходов временно недоступен — повторите позже.»), в OpenRouter
  запрос не уходит. Поменять это можно только решением владельца.
- **Модели** — семь кандидатов с закреплёнными точками ZDR (`core/pcbk_core/llm/models.py`). `LLM_MODELS` в
  `.env` сужает список именами через запятую: пусто — все семь (на время отбора), после выбора модели (Д8) —
  одно имя; имя сверяется точным совпадением. Смена — `.env` и
  `docker compose up -d --no-build --force-recreate core`, без пересборки образа.
- **Выключатель и учения** — в `.env`, затем `docker compose up -d --no-build --force-recreate core` (около 20 с
  мигают и строки службы данных); снимаются пустым значением и тем же пересозданием:
  - `LLM_STATE=stopped` — вызовов к модели и опросов OpenRouter нет, `/llm/` отвечает 403 `pcbk_stopped`,
    строка — «сбой»; на нём идёт учение «LLM-прокси остановлен»;
  - `DRILL_LLM=402` — учение «на счёте OpenRouter кончились деньги»: отказ внутри процесса, без запроса к
    OpenRouter, опрос `/key` настоящий; признак снимает только пересоздание `core` без учения.
- **Откат** — раздел «Откат», пункт «Д4а — назад к Д3а».

## Вход и рабочие места (с Д5а)

Шлюз — роль `gateway` в `core`. Студент входит на странице `/login` (статика `edge`); шлюз будит его место
через `sp-ctl`, а после простоя усыпляет. Ручки шлюза и cookie сессии — под `/pcbk/api/` (у Dify на том же
имени такого пути нет) и отвечают только через `edge` (сеть `pcbk-front`): из сетей мест — 404. С Д5б браузер
ходит к OpenCode своего места только через шлюз — `/pcbk/api/oc/*` по белому списку вызовов; пароль места
знает только шлюз. Строка страницы — «Шлюз и вход» (`note` — «работает мест: N; потоков: N; отказов за
час: N»). Пределы `edge` — раздел «Что открывается наружу».

- **Учётки** — командой администратора `docker exec -i pcbk-core python -m pcbk_core.gateway.accounts <команда>`:
  `add --login L --role student|teacher [--place N]`, `passwd --login L` (сессии закрываются, замок
  снимается), `disable --login L` (сессии закрываются, место остаётся за учёткой), `remove --login L --yes`
  (только отключённую), `unlock --login L`, `release-place --place NN --yes`, `list` (учётки и закрепления
  мест, без хешей). Пароль — одна строка stdin или `--generate` (печатается один раз): ни в истории оболочки,
  ни в argv. Логин — 3…32 знака из `a-z`, `0-9`, `.`, `_`, `-`, первый — буква.
- **Место и логин закреплены навсегда.** Место — за первой учёткой; логин удалённой учётки остаётся за
  местом, и другому человеку команда место не выдаёт: иначе новый студент увидел бы разговоры и агентов
  прежнего. Логин прежнего студента новому человеку не выдаётся; `--same-person` — только тому же человеку.
  Смена студента в месте — процедура владельца (раздел «Новый студент»).
- **Пробные учётки.** До начала пилота места 09 и 10 берут пробные учётки живых проверок (`dN-probe-*`) и
  после каждой проверки освобождают — «Освобождение места пробной учётки»; пилот получает все 10 мест.
  `release-place` освобождает только место пробной учётки после `disable` (с Д7а — и после `archive` агентов
  места).
- **Замок входа:** пять неверных паролей за 15 минут закрывают учётке вход, пока старейшая из пяти попыток не
  выйдет из 15-минутного окна; снять раньше — `unlock --login L`. В `edge` — не больше 5 запросов входа в
  секунду с адреса.
- **`.env`:** `GW_ORIGIN=https://<имя сертификата>:8443` — ровно как в адресной строке браузера, строчными, без
  пути (проверка: `grep -c '^GW_ORIGIN=https://' .env` → `1`); `GW_IDLE_STOP_S` — простой до сна, с (пусто —
  1800). `GW_STALE_S` сторожа (умолчание 120) — не меньше 4 × `GW_REAPER_TICK_S` у `core` (умолчание 30).
  Ошибка в `GW_*` — строка «Шлюз и вход» — «ошибка настройки»; служба данных работает.
- **Простой и уборщик:** место засыпает после 30 минут простоя (`GW_IDLE_STOP_S`); места, поднятые вручную
  (`docker start`), засыпают через 30 минут после того, как их увидел уборщик.
- **Учения** `DRILL_GATEWAY` — в `.env`, затем `docker compose up -d --no-build --force-recreate core`;
  снимаются пустым значением и тем же пересозданием. `freeze_reaper` — уборщик простоя замирает после первого
  такта (строка — «сбой» «уборщик простоя молчит — места не засыпают»); `bad_place_pw` — шлюз шлёт месту
  неверный пароль. В `note` строки — «учения: <режим>».
- **Откат** — раздел «Откат», пункты «Д5а — назад к Д4б» и «Д5б — назад к Д5а».

## Агенты студентов (с Д7а)

Агентов пишет служба (`save_agent` Конструктора, с Д7б): шаблон с `mode: primary`, описанием и своими правилами
`permission`; строки учёта — `docker exec pcbk-core python -m pcbk_core.agents.admin list --place NN`. Команды
`agents.admin` ниже — через `docker exec pcbk-core python -m …`.

### Файл агента вручную

Новый файл или правка файла, записанного службой, в `/srv/agents/student-NN/` (с хоста каталог не пишется — корень
`0700` у 10003):
`docker exec -i -u 10003 pcbk-core sh -c 'cat > /srv/agents/student-NN/<имя>.md' < <файл>`, затем
`agents.admin reload --place NN`. Файл — только с frontmatter, который разбирается, и с `permission`: первым
`"*": deny`, затем `allow` нужных инструментов — как у `student/config/agent/pomoshnik.md`, плюс `mode: primary` и
`description`. Файл без `permission` или с битым YAML (OpenCode 1.18.33 молча берёт пустой frontmatter из кэша
gray-matter) место загружает агентом без своих правил: правила стенда (`student/config/opencode.json`: `"*": deny`
первым, затем `read` — чтение `/work`) и `mode: all`. Такого агента шлюз в ход не пускает (400 «Агент «…» недоступен»
— только `mode: primary`, с Д7б), строка учёта — `not_visible`, в панели — «не загрузился — сообщите преподавателю».
Проверка после `reload` — не сразу: перезагрузка идёт, когда на месте стихнет ход (спящее место — при пробуждении).
Дождаться на странице сторожа в строке «Агенты студентов» «ждут перезагрузки: 0» (место должно работать), затем
`agents.admin list --place NN`: у правленого агента службы — `visible`; `not_visible` — исправить файл и `reload` ещё раз. Новый файл без строки учёта панель студента не показывает (в ней — строки учёта), а
`list` называет его в «файлы без учёта».

### Карантин (с Д7б)

Файл агента, который OpenCode не может прочесть (`Config…Error`, например `steps: 0`), ломает всё рабочее место.
`core` уносит такой файл в карантин и перезапускает место — после перезагрузки агентов и при пробуждении.

- **Где файл.** `docker exec pcbk-core ls -la /srv/agents/quarantine/student-NN/` — имя: путь в каталоге места
  («/» → «__»), затем `.<время UTC>-<6 hex>`, например `sushka.md.20261008T091500Z-a1b2c3`. Строка учёта агента —
  `quarantined`, в панели студента — «не загрузился — сообщите преподавателю». Журнал `core` — `gw agents quarantine
  place=NN result=moved` (без имени файла).
- **`warn` «файл агента сломал рабочее место — в карантине, место перезапущено»** держится час (счётчик за час) и
  гаснет сам: место уже поднято. Разобрать файл со студентом; строку `quarantined` уносит `archive` (ниже).
- **`fail` «настройка рабочего места сломана — место не поднимается»** (тоже час; баннер его прячет от студентов,
  сломанное место пишет «Настройка рабочего места сломана — сообщите администратору.»). Журнал `core` — `result=capped`
  или `result=image`:
  - `capped` — у места уже 3 карантина за час, следующий кривой файл остался в каталоге места, место погашено.
    `agents.admin list --place NN` → `agents.admin archive --place NN --name <имя> --yes` (кривой не найти — без
    `--name`: все агенты места, файлы остаются в `/srv/agents/archive/`) → студент снова открывает место («Повторить»
    или «Разбудить» в чате);
  - `image` — сломан файл образа (путь вне каталога агентов) или файл не перенёсся (каталог агентов недоступен —
    тогда и строка «каталог агентов недоступен»). Архив не поможет: правка образа места (`student/config`) и
    «Пересоздание мест с сохранением томов» — или починка каталога агентов.
- **Файлы карантина — данные студентов.** `archive` их не трогает, `release-place` они не держат. Удалить — только по
  решению владельца: `docker exec -u 10003 pcbk-core rm /srv/agents/quarantine/student-NN/<файл>`.

## Что открывается наружу

- Порт **8443** контейнера `edge` — Docker публикует его **в обход ufw**.
  Закрыть: `docker compose stop edge`.
- `edge` — единственный контейнер с опубликованным портом. Выход наружу, кроме
  него, есть только у `core` — через сеть `pcbk-egress` (к историану с Д3а, к
  OpenRouter с Д4а; охрана выхода в процессе — с Д3б); остальные сети внутренние
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
  чат класса получит 503). Тот же класс — у «кто я» и подтверждения
  предупреждения (`/pcbk/api/auth/me`, `…/notice`, Д6б) и у ручек места: каждая
  поддельная cookie правильного вида — своя корзина, предел на неё обходится
  перебором cookie (запросы без учётки core отклоняет сразу, 401). Тело, у
  которого байт приходит чаще раза в 10 с, срок между чтениями не обрывает:
  такое соединение держит слот сколько угодно. Окончательно — предел по адресу
  клиента после замера `$remote_addr` на сервере (Б7).

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
- Д3а — раздел «Служба данных (с Д3а)», пункт «Откат Д3а».
- Д4а — назад к Д3а. Своей компоновки у Д3б нет (её несёт Д4а), поэтому Д3б выкладывается и откатывается
  вместе с Д4а. Секреты (`ops.*-token`, `core-tokens`, `llm-tokens`, ключи OpenRouter) и том `pcbk-core-data`
  (журнал событий и журнал вызовов) не удаляются:
  1. вернуть `compose.yaml.d3a` (копия перед выкладкой Д4а, шаг 2 «Выкладки»; если Д3б всё же выкладывался
     раньше отдельно — `compose.yaml.d3b`, тогда `core` встанет на `:d3b`);
  2. `docker compose up -d --no-build --force-recreate core watchdog` — `core` и сторож на `:d3a`; проверка —
     `docker inspect -f '{{.Config.Image}}' pcbk-core` и `stale: false` на `/status.json`.
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
- Д5а — назад к Д4б (места не трогаются: образ места тот же, `student:d4`):
  1. вернуть `compose.yaml.d4b`, `edge/pcbk.conf.template.d4b` и `edge/static/index.html.d4b` — копии перед
     выкладкой Д5а (иначе ссылка «Войти» главной страницы ведёт на «не найдено»);
  2. `docker compose up -d --no-build core watchdog` — `core` и сторож на `:d4b`;
  3. `docker compose up -d --no-build --force-recreate edge` — шаблон читается при старте.

  Таблицы `gw_*` в `core.db` остаются: `core:d4b` их не читает.
- Д5б — назад к Д5а:
  1. вернуть `compose.yaml.d5a` и `edge/pcbk.conf.template.d5a` — копии перед выкладкой Д5б;
  2. `docker compose up -d --no-build core watchdog` — `core` и сторож на `:d5a`;
  3. `docker compose up -d --no-build --force-recreate edge` — у `edge` Д5б свой том `pcbk-edge-temp` и
     поворот журнала, а шаблон читается при старте; том не удаляется.
- Д6а — назад к Д5б. Откатывается весь `edge/`: страница входа Д6а ведёт в `/chat/`, а первой выкладке чата
  возвращать нечего (раздел «Статика чата», откат):
  1. вернуть `compose.yaml.d5b` и `edge/pcbk.conf.template.d5b` — копии перед выкладкой Д6а;
     `edge/static/login.html`, `login.js` и `index.html` Д5б — из git (копий выкладка Д6а не снимает):
     `git show <тег platform-d5b>:edge/static/login.js` и так же остальные; `chmod -R o+rX edge`;
  2. чат — в сторону: `cd /opt/pcbk-reserve/edge/static && mv -T chat chat.bad-d6a`;
  3. `docker compose restart edge` — шаблон перечитывается при старте;
  4. `docker compose up -d --no-build watchdog core` — сторож и `core` на `:d5b` (код `core` Д6а тот же, что
     у `:d5b`);
  5. места на `student:d4` — раздел «Пересоздание мест с сохранением томов» (кто работает — дождаться конца;
     `create --force-recreate --no-build` всех десяти, тома остаются, места спят).
- Д6б — назад к Д6а (места не трогаются: образ места в Д6б тот же, `student:d6a`):
  1. вернуть `compose.yaml.d6a` и шаблон `edge` Д6а — копии перед выкладкой Д6б
     или из git: `git show <тег platform-d6a>:edge/pcbk.conf.template`;
     `docker compose restart edge`;
  2. `docker compose up -d --no-build core watchdog` — `core` и сторож на `:d6a`
     (образы `:d6a` остаются на сервере); `core.db` с таблицей `gw_notices` Д6б
     `core:d6a` открывает как есть — лишняя таблица ему не мешает;
  3. статика чата — `chat.d6a` на место (раздел «Статика чата», откат) и
     `edge/static/login.js` Д6а — копия перед выкладкой Д6б или из git:
     `git show <тег platform-d6a>:edge/static/login.js`, как шаблон в шаге 1.

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
  from pathlib import Path
  from pcbk_core.settings import Settings
  uri = Path(Settings.from_env().DB_PATH).absolute().as_uri() + "?mode=rw"
  db = sqlite3.connect(uri, uri=True, timeout=5)
  with db: n = db.execute("DELETE FROM gw_notices WHERE login = ?", (sys.argv[1],)).rowcount
  print("стёрто подтверждений:", n)' <логин>
  ```
  `mode=rw`, как у `accounts.py`: при неверном пути — ошибка, а не новый пустой
  файл. `accounts list` после этого — «нет» в последнем столбце.
- Д7а — назад к Д6б (места не трогаются: образ места тот же, `student:d6a`, и
  каталог агентов место по-прежнему монтирует только на чтение):
  1. вернуть `compose.yaml.d6b` (копия перед выкладкой Д7а, шаг 2 «Выкладки»);
  2. `docker compose up -d --no-build core watchdog` — `core` и сторож на `:d6b`
     **вместе**. Только `core` — нельзя: сторож `:d7a` получит от `core:d6b` 404
     на `/health/agents`, и строка «Агенты студентов» — `unknown` «служба агентов
     ещё не выложена» — встанет в баннер каждого студента. `core.db` с таблицами
     `agents` и `agent_reloads` Д7а `core:d6b` открывает как есть: свои таблицы
     он заводит `CREATE TABLE IF NOT EXISTS`, по имени проверяет только
     `llm_calls`, а строки `save_agent` в `agent_events` не читает — лишние
     таблицы и строки ему не мешают;
  3. владелец каталога агентов — снова прежний, из снимка утра Д7а
     (`~/pcbk-d7a-before.txt`, строки `stat` корня и `student-*`: `uid:gid` и
     права корня). Только после шага 2: `core:d7a` без записи в каталог
     агентов — красная строка. Оставить 10003 нельзя: `core:d6b` каталог
     агентов не монтирует (через `docker exec` его не достать), а «Новый
     студент» Д6б переименовывает и заводит каталоги мест с хоста — в корне
     `0700` у 10003 это не выйдет. Та же команда, что при выкладке, с прежними
     значениями:
     ```bash
     cd /opt/pcbk-reserve && AD=$(grep '^AGENTS_DIR=' .env | cut -d= -f2) && [ -n "$AD" ] &&
     docker run --rm --network none --user 0 -v "$AD:/a" --entrypoint sh pcbk-reserve/core:d6b \
       -c 'chown <uid>:<gid> /a /a/student-* && chmod <права корня> /a && ls -lan /a'
     ```
     Ожидается: у корня и `student-01…10` — прежние `uid:gid`, у корня — прежние
     права. Файлы агентов в `student-NN` остаются у 10003 (`0644`): места их
     читают, а владелец каталога места переименует их вместе с каталогом.
     `archive/` и `quarantine/` — данные студентов: не трогать, не удалять,
     владельца не менять (с хоста они не читаются);
  4. статика чата — `chat.d6b` на место (раздел «Статика чата», откат) —
     вместе со сторожем или после него, не раньше: чат Д6б не знает строки
     «Агенты студентов» и показал бы её `warn` (счётчики по всем местам) в
     баннере каждого студента, пока стоит сторож `:d7a`.

  Агенты, сохранённые при Д7а, остаются в каталогах мест — места видят их и
  при Д6б; строки их учёта остаются в `core.db`. Если при `core:d6b` студента
  места сменили по «Новому студенту» Д6б, после возврата на `core:d7a` — для
  этого места `agents.admin archive --place NN --yes` (иначе учёт без файлов
  держит место).
- Д7б — назад к Д7а (тома мест, секреты, каталог агентов и `core.db` сохраняются). Порядок — обратный выкладке
  Д7б: статика, места, `core`; сторож остаётся на `:d7a` (в Д7б он не менялся):
  1. статика чата — копия, снятая при выкладке Д7б (`chat.<день, выложенный до Д7б>`: `chat.d7a`, если Д7а
     выкладывал статику, иначе `chat.d6b`), на место — раздел «Статика чата», откат. Первой: чат Д7б показывает
     Конструктора в панели, а места после шага 3 его уже не знают (ход — 400 «Агент «konstruktor» недоступен»);
  2. вернуть `compose.yaml.d7a` (копия перед выкладкой Д7б, шаг 2 «Выкладки»; в нём `core:d7a` и места
     `student:d6a`): `cp -p compose.yaml.d7a compose.yaml`;
  3. места на `student:d6a` — раздел «Пересоздание мест с сохранением томов» (кто работает — дождаться конца;
     `create --force-recreate --no-build` всех десяти, тома остаются, места спят);
  4. `docker compose up -d --no-build core` — `core:d7a`; проверка — `docker inspect -f '{{.Config.Image}}' pcbk-core`
     и `stale: false` на `/status.json`, строка «Агенты студентов» — в норме.

  **Только `core` при местах на `student:d7b` не откатывать** — шаги 3 и 4 идут вместе и в этом порядке: у `core:d7a`
  карантина нет (его `repair` — заглушка), и кривой файл агента кладёт место — `dispose` раз в 30 с; а `save_agent`
  Конструктора `core:d7a` на строку `quarantined` отвечает `already` — ложное «сохранено». Строки `quarantined` в
  `core.db` и каталог `/srv/agents/quarantine/` — данные студентов: не трогать, не удалять.
  Шаблон `edge` Д7б (`location = /pcbk/api/agents`) откатывать не обязательно: прежняя статика этот путь не зовёт, а
  `core:d7a` ответил бы на него 404. Агенты, сохранённые при Д7б, остаются в каталогах мест: места `student:d6a`
  видят их, как при Д7а.
- Тома мест, `secrets/` и `agents/` удаляются только до первого входа
  студента и только по именам: после — там разговоры и агенты студентов.
- `rm -rf /opt/pcbk-reserve` (с `sudo`) — только до первого входа. После —
  удалять лишь `tls/`, `compose.yaml` и `edge/`; **`secrets/` и `agents/`
  остаются**.
- gVisor: остановить контейнеры с `runsc`; вернуть
  `/etc/docker/daemon.json.pre-runsc` на место; `systemctl reload docker`;
  `rm -rf /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin`.
