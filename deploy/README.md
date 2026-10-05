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

Команды `accounts` — через `docker exec pcbk-core python -m pcbk_core.gateway.accounts …`. Свободное место — в
`accounts list` нет строки `место NN`: закрепления нет ни за чьим логином.

- **На свободном месте (с Д10б)** — кабинет преподавателя: «Учётные записи» → «Завести студента», пароль показан один
  раз (раздел «Учётные записи у преподавателя»). Без кабинета (до окна Б4 или после отката к Д9) — команда
  администратора: `accounts add --login L --role student --place N --generate` (пароль печатается один раз) или тот
  же `add` без `--generate` с паролем одной строкой stdin из файла (`docker exec -i … < <файл>`).
- **Смена студента в занятом месте (слот 01–10) — сейчас не поддерживается, до отдельной задачи.** Место закреплено
  за логином прежнего студента (таблица `gw_places` в `core.db`), и закрепление не снимают ни `disable`, ни `remove`.
  Новый логин на такое место `accounts add` и кабинет отвергают: «место NN закреплено за учёткой L — смена студента в
  месте только по процедуре владельца» (`core/pcbk_core/gateway/accounts.py`, `add`) — такой процедуры в коде пока
  нет. `release-place` снимает закрепление только у пробных логинов `dN-probe-*`: «освобождать можно только места
  пробных учёток после disable». На томах места остаются разговоры прежнего студента, в каталоге агентов — его агенты:
  смена студента должна их сохранить и отдать место новому человеку пустым — это отдельная задача. Правкой `core.db`
  закрепление в обход команды не снимать. Что работает сейчас:
  - **место пробной учётки** (`dN-probe-*`) — «Освобождение места пробной учётки» ниже (разговоры пробы → `disable` →
    `archive` → `remove` → `release-place`), затем новый студент — как на свободном месте;
  - **тот же человек вернулся** после `remove` — `accounts add --login L --role student --place N --same-person`:
    только на своё место и только студентом (кабинет `same_person` не передаёт); `remove` стёр подтверждение
    предупреждения — при входе оно покажется снова;
  - **учётка отключена** — «Включить» в кабинете или `accounts enable --login L` (с Д10б): только на своё место и
    только если на нём нет действующей учётки.
- **Место 11 и дальше:** правка регулярок `sp-ro`/`sp-ctl`, подсеть N, блок
  в `compose.yaml`, строка в `watchdog/components.json`, тесты границ
  диапазона, пересборка сторожа.

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

## Прогон отбора модели (с Д8)

Отбор — 3–4 модели-кандидата, по одной на место отбора (по умолчанию 07–10): на время окна у каждого места своя
модель по умолчанию, а прогонщик `python -m pcbk_bench` (`bench/`, только машина разработчика) ведёт разговоры через
`edge` и шлюз учётками `d8-probe-bench-NN`. Деньги OpenRouter — только в окне владельца, только через LLM-прокси и под
потолком прогона `LLM_RUN_*`. Команды `accounts` и `agents.admin` — через `docker exec pcbk-core python -m …`; `NN` —
номер места, `N` — он же без нуля; `OUT` — каталог прогона на машине разработчика (`$JOB/pcbk-d8/live/out`).
`/health/llm` на сервере — `curl -s http://172.31.250.82:8000/health/llm` (адрес `core` в `pcbk-egress`).
Прогонщик (только stdlib, Python 3.12) вызывается из каталога `bench/` репозитория:
`uv run --python 3.12 python -m pcbk_bench …`; из корня репозитория — `PYTHONPATH=bench uv run --python 3.12 python -m
pcbk_bench …`. Ниже — коротко `python -m pcbk_bench …`.

**Предпосылки** (только чтение):
- в `.env` пусты `LLM_RUN_ID`, `LLM_RUN_USD`, `LLM_RUN_CALLERS`, `GW_IDLE_STOP_S` и `DRILL_LLM` — как в «Аварийной
  уборке», шаг 1;
- `LLM_MODELS` в `.env` пусто (все кандидаты) или содержит всех кандидатов конфига прогона: модели не из списка
  прокси отказывает — 403 `pcbk_model_refused`, и прогоны её места идут в `stand_error`;
- `/health/llm` → `pending: 0` (вызовы без цены). Не 0 у мест отбора — ничего не делать: точка отсчёта прогона
  ставится по оплаченному, и цена этих вызовов войдёт в расход прогона (центы, в безопасную сторону);
- строки «LLM-прокси и бюджет» и «Память сервера» — норма, в строке «Агенты студентов» — «ждут перезагрузки: 0»;
- места отбора свободны: в `accounts list` нет строк `место NN` этих мест, места спят
  (`docker ps -a --filter label=pcbk.role=student --format '{{.Names}} {{.State}}'`);
- прежний расход каждого места отбора — не больше $20: доля места — $100, а прогону нужно до $35 на модель. Расход
  мест по журналу вызовов:
  ```bash
  docker exec pcbk-core python -c 'import sqlite3, sys
  c = sqlite3.connect("file:/var/lib/pcbk-core/core.db?mode=ro", uri=True)
  q = "SELECT caller, ROUND(SUM(COALESCE(cost, cost_est)), 2) FROM llm_calls WHERE caller IN (%s) GROUP BY caller"
  for row in c.execute(q % ",".join("?" * len(sys.argv[1:])), sys.argv[1:]): print(*row)' \
    student-07 student-08 student-09 student-10
  ```
- `key_usage_usd` из `/health/llm` — в журнал дня: точка сверки расхода после окна.

**Режим отбора** — сначала `core`, потом места:
1. `.env`: `LLM_RUN_ID=d8-<ДДММ>`, `LLM_RUN_USD=80`, `LLM_RUN_CALLERS=student-07,student-08,student-09,student-10`
   (только места отбора), `GW_IDLE_STOP_S=10800` (места не засыпают в окне); затем
   `docker compose up -d --no-build --force-recreate core`. Проверка: `/health/llm` → `run` с этим `id`, `callers` —
   число мест, `spent_usd` — `0`; в строке «LLM-прокси и бюджет» — «прогон d8-ДДММ: $0.00 из $80, вызовов за 10 мин:
   0». Ошибка в `LLM_RUN_*` (не все три, опечатка в месте, потолок больше общего бюджета) — ошибка настройки и отказ
   всем вызовам `/llm/`: исправить `.env` и пересоздать `core`.
2. Места с переопределением модели. Файл — `python -m pcbk_bench override <конфиг прогона>` на машине разработчика
   (JSON, он же YAML: у мест конфига только `OPENCODE_CONFIG_CONTENT` = `{"model": "<провайдер>/<модель>"}`) → на
   сервер в `~/pcbk-d8/compose.bench.yaml` (каталог `0700`, файл `0600`; в git его нет). Затем
   `docker compose -f compose.yaml -f ~/pcbk-d8/compose.bench.yaml --profile students up -d --no-build --force-recreate student-07 student-08 student-09 student-10`
   — тома мест не трогаются. Дождаться `mcp=connected` у каждого места (до 90 с после старта):
   `docker inspect -f '{{json .State.Health.Log}}' pcbk-student-NN` — последняя запись `200 mcp=connected`. Модель
   места прогонщик сверяет сам: `GET /config` места — до прогона, модель строк журнала прокси — в каждом прогоне.
3. Учётки — только логины `d8-probe-bench-NN` (`PROBE_LOGIN`: только их место потом освобождается):
   `ssh <сервер> docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login d8-probe-bench-NN --role student --place N < $JOB/pcbk-d8/secrets/d8-probe-bench-NN.pw`
   — пароль одной строкой из файла (`openssl rand -hex 24`, `0600`, только на машине разработчика).
4. Пуск — `python -m pcbk_bench run <конфиг>` в фоне (в конфиге `window_start` — начало окна с поясом). Прогон
   кончился — `run` выходит с кодом `0` при любой причине конца и печатает её (`finished`, `cap`, …); `2` — отказ
   запуска (`RunRefused: <код>`), `1` — сбой. **Итог прогона — по `status`, не по коду `run`.** Раз в 10 минут —
   `python -m pcbk_bench status OUT --stale-s 120` и строка страницы.

**Коды `status`.** Строка `status` — «прогон <id>: <состояние>, сделано X из Y, повтор R, причина <причина>»
(и «места остановлены: NN», если есть такие).
- `0` — идёт и свеж или закончен (`finished`).
- `1` — молчит (`updated_at` старше `--stale-s`: прогонщик умер), завис (`last_turn_at` старше срока хода + 5 мин)
  или `progress.json` нет. Разбор — по журналу прогонщика (stderr); зависший остановить, как в «Аварийной уборке»,
  шаг 2; продолжение — запуск тем же `OUT`: сделанные прогоны пропускаются.
- `2` — остановлен или пауза, причина — в строке `status`:
  - `projection` — пауза на барьере 1: «Поднять потолок на паузе» ниже;
  - `place` — названные места остановлены тремя `stand_error` подряд, прогоны прочих мест доделаны. После разбора
    места — запуск тем же `OUT`: сделанные прогоны пропускаются;
  - `cap`, `money` — потолок прогона или деньги: **без решения владельца не возобновлять**;
  - `window` — окно кончилось: возобновление тем же `LLM_RUN_ID` и `OUT` во втором окне;
  - `stand` — строка «LLM-прокси и бюджет» `fail`, «Память сервера» не в норме или в `/health/llm` нет блока `run`
    этого прогона; `interrupt` — прогонщик остановлен сигналом. Возобновление — тем же `OUT`, когда строки в норме и
    блок `run` на месте.

**Пока идёт прогон:**
- Потолок держит прокси: сверх `LLM_RUN_USD` вызовы мест прогона получают 402 «Потолок прогона отбора модели исчерпан
  — модель не отвечает. Сообщите преподавателю.», строка — «внимание» «потолок прогона отбора исчерпан — вызовы
  прогона получают отказ». Перерасход — не больше цены вызовов, начатых до того, как расход дошёл до потолка: до трёх
  на место (ход, заголовок, повтор). Прочих вызывающих потолок не трогает.
- Остановка `cap` бывает и при расходе ниже `LLM_RUN_USD`: прогонщик останавливается заранее, по запасу
  `stop_margin_usd` (`run.spent_usd + stop_margin_usd ≥ потолка`), или по 402 прокси, который считает расход вместе с
  резервами идущих вызовов, — после остановки резервы снимаются, и `run.spent_usd` ниже потолка. Тогда строка — не
  «потолок исчерпан»: к моделям прогона больше никто не обращается, и через `LLM_RUN_SILENT_S` она — «Прогон отбора
  молчит». Причина остановки — в `status OUT` (`причина cap`), а не в строке страницы.
- «Прогон отбора молчит — к модели давно никто не обращался» («внимание») — к моделям прогона не обращались дольше
  `LLM_RUN_SILENT_S` (300 с). Бывает и при живом прогоне: метка вызова ставится в его начале, и один вызов модели
  дольше 300 с даёт ту же строку. Её видит только владелец на странице, прогонщик по ней не останавливается; сверка —
  `status OUT`: код 0 и свежий `last_turn_at` в `OUT/progress.json` — прогон жив.
- Перезапуск `core` с тем же `LLM_RUN_ID` продолжает прогон: точка отсчёта лежит на диске (`llm_meta`).
- **Места и `LLM_RUN_ID` посреди прогона не менять** без нового потолка: новый id или новый вызывающий — новая точка
  отсчёта, расход прежнего прогона в новый не входит, и потолок $80 дал бы ещё $80. Если менять — `LLM_RUN_USD` =
  остаток: прежний потолок минус `run.spent_usd`.

**Поднять потолок на паузе** — `status` с кодом 2 и причиной `projection`: прогноз после повтора 1 больше потолка без
запаса. Процедура Д8-R74:
1. новое «да» владельца;
2. `LLM_RUN_USD` в `.env` — новый потолок, не больше `LLM_TOTAL_USD`;
3. `docker compose up -d --no-build --force-recreate core` с тем же `LLM_RUN_ID` — точка отсчёта сохраняется;
4. тот же потолок — `cap_usd` в конфиге прогона;
5. ожидание `mcp=connected` у мест (как в шаге 2 режима);
6. `projection_ack: true` в конфиг и запуск тем же `OUT`: возобновление пропускает сделанные прогоны и паузу на
   барьере 1 не повторяет.

Иначе — снять повтор 3 (`reps` в конфиге), `projection_ack: true` и возобновить.

**Повторный отбор после выбора** (через две недели и позже): списки моделей образа места после выбора не меняются
(меняются только `"model"` и `LLM_MODELS`), ссылки кандидатов годны. На время прогона `LLM_MODELS` в `.env` — пусто
(все кандидаты), `core` пересоздан; после уборки — снова выбранная модель и пересоздание `core`.

**Уборка после окна.** Порядок — как в тестовом стенде (`tests/integration/benchstand.py`, `BenchHarness.cleanup`):
агенты уходят в архив, пока места работают, — перезагрузку после архива делает только работающее место, у спящего
метка висит до пробуждения. Оборванную уборку продолжать с первого несделанного шага: сделанный шаг пропустить.
1. Прогонщик не идёт: `python -m pcbk_bench status OUT` — не `running`, и `flock -n OUT/.lock true` → код `0`.
2. Учётки — `accounts disable --login d8-probe-bench-NN` первыми: сессии закрыты, новый агент не сохранится.
3. Агенты мест — в архив, пока места работают. Место спит (`docker ps -a --filter label=pcbk.role=student --format
   '{{.Names}} {{.State}}'` — не `running`) — сначала `docker start pcbk-student-NN` и дождаться `healthy`
   (`docker inspect -f '{{.State.Health.Status}}' pcbk-student-NN`), как в «Освобождении места пробной учётки»,
   шаг 0. Затем `agents.admin archive --place N --yes` у каждого места отбора. Дождаться
   в строке «Агенты студентов» «ждут перезагрузки: 0»; `agents.admin list --place N` — строк агентов нет, последняя
   строка «расхождений нет».
4. Места без переопределения: `docker stop pcbk-student-07 pcbk-student-08 pcbk-student-09 pcbk-student-10`, затем
   `docker compose --profile students create --force-recreate --no-build student-07 student-08 student-09 student-10`
   — без `-f ~/pcbk-d8/compose.bench.yaml`; тома остаются, места спят.
5. `accounts remove --login d8-probe-bench-NN --yes`, затем `accounts release-place --place N --yes`.
6. В `.env` — пусто у `LLM_RUN_ID`, `LLM_RUN_USD`, `LLM_RUN_CALLERS` и `GW_IDLE_STOP_S`; затем
   `docker compose up -d --no-build --force-recreate core`.
7. Разговоры отбора — после окна, без владельца (денег и данных наружу это не даёт), по журналу `OUT/sessions.log`
   (строки `<id>\t<NN>\tstart|end`), **одним вызовом на место**:
   - id места — на машине разработчика: `awk -F'\t' '$2=="NN" && $3=="start" {print $1}' OUT/sessions.log | sort -u`;
   - на сервере: `docker start pcbk-student-NN`, дождаться `healthy`, затем один одноразовый `curl` в сети места с
     паролем места файлом — все адреса `DELETE` подряд (`200` — удалён, `404` — разговора уже нет), и
     `docker stop pcbk-student-NN`:
     ```bash
     cd /opt/pcbk-reserve && SD=$(grep '^SECRETS_DIR=' .env | cut -d= -f2) && STU=$(grep '^STU_NET=' .env | cut -d= -f2) &&
     N=7 && NN=07 && IDS="<id> <id> …" && [ -n "$SD" ] &&
     docker run --rm --pull never --runtime=runsc --network pcbk-stu-$NN --read-only --cap-drop ALL \
       --security-opt no-new-privileges:true -v "$SD/student-$NN.pw:/pw:ro" curlimages/curl:8.16.0 \
       -s -m 120 --variable PW@/pw --expand-user 'opencode:{{PW}}' -X DELETE -w ' %{http_code}\n' \
       $(for i in $IDS; do echo "http://${STU:-172.31}.$N.3:4096/session/$i"; done)
     ```
   - строки конца: id без строки `end` — дописать `<id>\t<NN>\tend` в `OUT/sessions.log` (возобновление с этим `OUT`
     их больше не прерывает).
8. Проверка: `/health/llm` → `run: null`; в `accounts list` нет `d8-probe-*` и строк `место NN` мест отбора; у мест
   нет `OPENCODE_CONFIG_CONTENT`; `agents.admin list --place N` — строк агентов нет; в `.env` `LLM_RUN_*` и
   `GW_IDLE_STOP_S` пусты. Итог — строкой в журнал дня.

## Аварийная уборка режима отбора (с Д8)

Когда окно оборвалось или день кончился без уборки. Проверка — первым шагом утра Б0, первым шагом второго окна (Д8б′)
и первым шагом утра Д9.
1. **Проверка** (только чтение):
   - в `.env` **пусты** `LLM_RUN_ID`, `LLM_RUN_USD`, `LLM_RUN_CALLERS`, `GW_IDLE_STOP_S` и `DRILL_LLM` — важно
     значение, а не строка: `grep -E '^(LLM_RUN_ID|LLM_RUN_USD|LLM_RUN_CALLERS|GW_IDLE_STOP_S|DRILL_LLM)=.' .env` —
     ни одной строки;
   - у мест нет `OPENCODE_CONFIG_CONTENT`:
     `for n in $(seq -w 1 10); do docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' pcbk-student-$n | grep -q '^OPENCODE_CONFIG_CONTENT=' && echo "место $n"; done`
     — пусто;
   - в `accounts list` нет `d8-probe-*` — ни строк учёток, ни строк `место NN`:
     `docker exec pcbk-core python -m pcbk_core.gateway.accounts list | grep -c 'd8-probe-'` → `0`;
   - `/health/llm` → `run: null`:
     `curl -s http://172.31.250.82:8000/health/llm | python3 -c 'import json, sys; print(json.load(sys.stdin)["run"])'`
     → `None`.
2. **Нашлось** — сначала прогонщик, на машине разработчика. **Без `kill` по pid до проверки замка:** после обычного
   конца прогона `OUT/.lock` пуст, а после смерти прогонщика в нём устаревший pid — этот номер мог уже получить чужой
   процесс.
   - `flock -n OUT/.lock true` → код `0` — прогонщика нет, `kill` не нужен;
   - код `1` — замок держит прогонщик:
     `pid=$(cat OUT/.lock); ps -o args= -p "$pid" | grep -q pcbk_bench && kill "$pid"` — сигнал только процессу
     прогонщика: идущие разговоры получают abort, прогоны — `interrupted`. Затем снова `flock -n OUT/.lock true` —
     через несколько секунд, до кода `0`. Пустой `.lock` или pid не прогонщика при коде `1` — не останавливать,
     повторить проверку.

   Дальше — «Уборка после окна» выше, с шага 2. `DRILL_LLM` — пусто и пересоздание `core` (учения без денег идут
   только под присмотром).
3. Итог проверки — строкой в журнал дня: «режим отбора не найден» или что нашлось и что убрано.

## Калькуляторы (с Д9а)

У службы данных два новых инструмента чтения: `tag_series` — ряд на равномерной сетке историана и профиль по тегу
(начало, конец, изменение, минимум и максимум сетки), `calc_runtime` — наработка по тегам состояний (часы, доля периода
и переходы в каждом состоянии). Агенты (MCP), HTTP `/api/data/<tool>` и вкладка «Калькуляторы» чата студента зовут один
и тот же код службы. Вкладка ходит через шлюз — `POST /pcbk/api/calc/<tool>`, только пять инструментов чтения.
Вызывающий — место учётки (`student-NN`), канал `web`. Предел частоты службы (60 вызовов за 5 минут) вкладка делит с
агентами своего места. У `edge` — `location /pcbk/api/calc/` в корзине ручек места: не чаще 5 в секунду на cookie
(запас 20), сверх — 503 «Слишком много одновременных запросов». Преподавателю до Д10б — 403 «Калькуляторы для
преподавателя — в следующих выпусках стенда»; с Д10б (`core:d10b`, Д10-R109) — вкладка «Калькуляторы» в шапке кабинета,
вызывающий `teacher`, канал `web`: место из учётки и тела не действует, предел частоты службы — один на все вкладки
преподавателя. Строка журнала `core` у вызова вкладки — `tool=… caller=student-NN channel=web …` (у преподавателя —
`caller=teacher`), без имён тегов и значений. Вкладка ничего не запрашивает при открытии — только по кнопке.

**Строка «Историан БДРВ» с Д9а.** Три ошибки подряд у одного инструмента (`error`, `timeout`, `unavailable` на каналах
`http`, `mcp`, `web`) — «внимание» «инструменты службы данных отвечают ошибкой подряд», имена инструментов — в пометке.
Серию продлевают только эти три исхода (`unavailable` — это и ошибка настройки, и незагруженный каталог); любой другой
исход — `ok`, `partial`, `refused`, `budget`, `rate`, `busy` — её обнуляет, хотя у отказов SQL не уходит (Д9-R83,
решение оставлено итоговым ревью Д9а): отказы между ошибками не дают серии дойти до трёх. Так и со сроками: после
сроков подряд ворота ставят паузу запросов людей, вызов на паузе получает `busy` и обнуляет серию `timeout`, — строку
тогда держит «внимание» о паузе запросов людей, молчания нет. Если ошибкой отвечает не меньше
20 % из 10 и больше последних вызовов, строка раньше говорит «инструменты отвечают ошибкой» (k из N). После обрыва серия
ошибок держится до удачного вызова или 30 минут без вызовов. Серии — в памяти `core`: пересоздание `core` их обнуляет.
Под учением `DRILL_DATA` к пометке строки любого состояния дописано «учения: tool_error» — забытое учение видно на
странице. Разбор — строки журнала `core` `tool=<имя> … outcome=error|timeout|unavailable`.

**Учение `DRILL_DATA=tool_error`.** Пять инструментов чтения сразу после проверки частоты отвечают
`status: error`, «сбой службы данных — вызов записан в журнал». SQL не уходит, данных историана (и каталога) в ответе
нет. `save_agent` учение не трогает. В журнале `core` при старте — строка `УЧЕНИЯ: DRILL_DATA=tool_error`,
у `/health/historian` — `"drill": "tool_error"`. **Цена: учение кладёт и агентов студентов** — их вызовы тоже получают
ошибку. Учения — только без студентов. Включить — `DRILL_DATA=tool_error` в `.env`, снять — `DRILL_DATA=` (пусто); в
обоих случаях затем `docker compose up -d --no-build --force-recreate core`. Неизвестное значение — `core` не стартует
(ошибка настройки с именем поля).

**Выкладка Д9а** (Д9-R90). Предпосылки (только чтение):
- выложены Д3б…Д8б;
- «да» владельца на выкладку Д9а — дословно, с условием «до пробы ряда и наработки в окне Д9б студентов на стенд не
  пускать»;
- режим отбора не найден — «Аварийная уборка режима отбора», шаг 1;
- строки «Память сервера» и «Историан БДРВ» — норма;
- в `.env` пусты все `DRILL_*` (`grep -E '^DRILL_' .env`; у `DRILL_FREEZE_LOOP` — пусто или `0`).

Образы `core:d9a`, `watchdog:d9a`, `student:d9a` и статика чата — из одного коммита ветки или тега Д9а (шаг 1
«Выкладки»), слои сверены по `RootFS`. `compose.yaml` и `edge/` — шаг 2 «Выкладки» до шага 1 ниже, с копией
`cp -p compose.yaml compose.yaml.<прошлый день>`; в `.env` — строка `DRILL_DATA=` (пусто). Порядок:
1. `docker compose up -d --no-build core` — `core:d9a`: инструменты, маршрут калькуляторов, серия ошибок;
2. `docker compose up -d --no-build watchdog` — `watchdog:d9a`. До него серии ошибок на странице нет: сторож `:d8a`
   поля `tools.failing` и `drill` службы не читает;
3. 10 мест `student:d9a` — «Пересоздание мест с сохранением томов» (кто работает — дождаться конца; тома остаются,
   места спят);
4. конфиг `edge`: новый шаблон (`location /pcbk/api/calc/`) `edge` берёт только при перезапуске —
   `docker compose restart edge`;
5. статика чата — раздел «Статика чата» (сборка из того же коммита).

Проверка: `docker ps -a --format '{{.Names}} {{.Image}}'` — `core:d9a`, `watchdog:d9a`, десять мест `student:d9a`;
строки сторожа — норма, `stale: false`; `tools/list` MCP токеном `ops` — шесть инструментов: `catalog_search`,
`tag_now`, `tag_period`, `tag_series`, `calc_runtime`, `save_agent` (список историан не читает).

**Учение на сервере без чтения историана** (Д9-R90, шаги 1–6). Вне окна владельца историан заказчика не читается:
на сервере калькуляторы и вкладка проверяются только под учением. Каталог в ответах — тоже производственные данные:
поиск тега на сервере вне окна — только под учением.
1. `DRILL_DATA=tool_error` в `.env`, `docker compose up -d --no-build --force-recreate core`. Проверка:
   `/health/historian` с хоста — по адресу `core` в `pcbk-egress`, как `/health/llm` в «Прогоне отбора модели»: ключ
   `drill` — `tool_error`; в журнале `core` — строка `УЧЕНИЯ: DRILL_DATA=tool_error`.
2. Три вызова `POST /api/data/tag_series` токеном `ops` через туннель к `core`, тело
   `{"tags": ["20FAKE_001_PV"], "period": "last_8h"}` — каждый `status: error`. Ответ `refused` вместо `error` — учение
   не включилось (`core` не пересоздан): такого тега нет в каталоге, и до SQL вызов не дошёл бы и без учения. Что SQL
   не ушёл, доказывают события самих вызовов, а не общий счётчик ворот (его растит опрос свежести):
   ```bash
   docker exec pcbk-core python -c 'import sqlite3
   c = sqlite3.connect("file:/var/lib/pcbk-core/core.db?mode=ro", uri=True)
   q = "SELECT outcome, summary, sent FROM agent_events WHERE caller = ? AND tool = ? ORDER BY rowid DESC LIMIT 3"
   for row in c.execute(q, ("ops", "tag_series")): print(*row)'
   docker logs --since 15m pcbk-core 2>&1 | grep -c 'tool=tag_series caller=ops channel=http .*rows=0 .*outcome=error error=drill'
   ```
   Ожидается: три строки `error учения: tool_error []` (`sent` пуст), затем `3` (других вызовов `tag_series` токеном
   `ops` за эти минуты не было).
3. Строка «Историан БДРВ» — не позже следующего такта сторожа «внимание» «инструменты службы данных отвечают ошибкой
   подряд», пометка «tag_series; учения: tool_error». Снимок строки.
4. Вкладка — учёткой `d9-probe-a` (место 10, `PROBE_LOGIN`) через туннель к 8443:
   `ssh <сервер> docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login d9-probe-a --role student --place 10 < $JOB/pcbk-d9/live/secrets/d9-probe-a.pw`
   — пароль одной строкой из файла (`0600`, только на машине разработчика). Первый вход — предупреждение, «Понятно»
   (в фазе предупреждения вкладок нет); экран места сам будит место 10. Вкладка «Калькуляторы»: слова «тест» →
   «Найти тег»; тег — вписать `20FAKE_001_PV` → «Посчитать». Оба раза — «сбой службы данных — вызов записан в журнал».
   Снимок — без имён тегов, кроме `20FAKE_*`, и без чисел.
5. `DRILL_DATA=` (пусто), `docker compose up -d --no-build --force-recreate core`; строка «Историан БДРВ» — норма, без
   пометки учений.
6. Уборка учётки — «Освобождение места пробной учётки» (`disable` → `archive` → `remove` → `release-place`): в
   `accounts list` нет `d9-probe-a` и строки «место 10», место 10 свободно и спит.

Итог — строкой в журнал дня (`docs/checks/D9a.md`); снимки в git — только после просмотра глазами.

**До пробы Б4 стенд закрыт для студентов.** Ряд и наработка доступны местам; на живом историане их формы проверит
проба Б4 — до неё студентов на стенд не пускать. Формы `Cyclic` и `StateSummaryHistory` (единица `StateTimeTotal`,
концы сетки) на живом историане впервые проверяет проба окна Д9б. До неё — места `:d9a`, учение снято, учёток
студентов не заводить и не выдавать. Учётки студентов уже есть — строка владельцу, сами учётки не трогаются. Стенд
открывает Б4 (шаг 3) по пробе `ok`; проба не прошла — стенд закрыт до `core:d9b`; проба не решена (`unresolved`)
— решение владельца («Окно Д9б», «Коды пробы»).

**Откат на образы Д8** — раздел «Откат», «Д9а — назад к Д8».

## Окно Д9б

Окно владельца: проба ряда и наработки, показ, доброволец с секундомером и автотест «агент = калькулятор» — не больше
2,5 ч по часам от первой строки журнала окна. Места окна: 09 — показ, затем автотест; 10 — доброволец. Деньги
OpenRouter и чтение историана — только в окне и только после «да» владельца (дословно — п. 3 «Что нужно от владельца»
в `docs/plans/PLAN-D9-calculators-2026-10-10.md`). Прогонщик `python -m pcbk_bench` — на машине разработчика, как в
«Прогоне отбора модели» (вызов из `bench/`; туннели к `core` и `edge` — адреса в `stand.json`). `LIVE` — каталог окна
на машине разработчика, `LIVE=$JOB/pcbk-d9/live` (`0700`, не в git). Команды `accounts` и `agents.admin` — через
`docker exec pcbk-core python -m …`; `/health/llm` и расход мест по журналу вызовов — командами «Прогона отбора
модели»; строки сторожа — `/status.json` («Выкладка и обновление», шаг 5): `stale: false`, `overall: ok`.

**До пробы окна студентов на стенд не пускать.** Места `:d9a` отдают ряд (`tag_series`) и наработку (`calc_runtime`)
агентам и вкладке «Калькуляторы», а формы этих запросов на живом историане впервые проверяет проба окна (шаг 3
окна). До пробы `ok` учёток студентов не заводить и не выдавать; уже есть — строка владельцу, сами учётки не трогать.

### Что где лежит (машина разработчика)

- `$LIVE/stand.json` (`0600`) — как в Д8: ключи `ssh`, `core_url`, `edge_url`, `edge_host` (`origin`, `remote_dir` —
  по желанию; шапка `bench/pcbk_bench/__main__.py`);
- `$LIVE/secrets/` (`0700`, файлы `0600`): `ops.data-token` — копия токена `ops`, которым ходит стенд отбора Д8 (есть с
  учений выкладки Д9а); `d9-probe-show.pw` и `d9-probe-vol.pw` — пароли учёток окна, при повторе не перезаписываются:
  ```bash
  umask 077 && install -d -m 0700 "$LIVE/secrets" &&
  for L in d9-probe-show d9-probe-vol; do f=$LIVE/secrets/$L.pw
    [ -e "$f" ] || python3 -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(12))' > "$f"
  done
  ```
  Абсолютный путь `d9-probe-vol.pw` (по `$CLAUDE_JOB_DIR` сессии окна) — строкой владельцу: пароль он читает сам и
  передаёт добровольцу на бумаге. В чат пароль не идёт;
- `$LIVE/match.config.json` — конфиг автотеста (`MatchConfig`; пути — от каталога файла; пароль учётки —
  `secrets/<login>.pw` рядом со `stand.json`):
  ```json
  {"stand": "stand.json", "place": 9, "login": "d9-probe-show", "cases": "match.cases.json", "out_dir": "out",
   "turn_timeout_s": 300, "source": {"place": 10}}
  ```
- `$LIVE/match.cases.json` (пишет `match-cases`) и `$LIVE/out/` (`match.jsonl`, `match.md`) — `0600`: имена тегов,
  ответы и числа, в git не идут;
- `$LIVE/zadanie.txt` — задание добровольцу для печати (текст — `docs/checks/D9b.md`, «Задание добровольцу»);
  `$LIVE/shots/` — снимки с данными, только владельцу.

### Учётки окна

Заводит администратор; пароль — одной строкой через stdin из файла (`--place` — номер места без нуля):
```bash
ssh <сервер> docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login d9-probe-show --role student --place 9 < "$LIVE/secrets/d9-probe-show.pw"
ssh <сервер> docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login d9-probe-vol --role student --place 10 < "$LIVE/secrets/d9-probe-vol.pw"
```
- `d9-probe-show` — место 09: показ, затем автотест;
- `d9-probe-vol` — место 10: доброволец. Предупреждение не подтверждено — доброволец видит его сам: до окна этой
  учёткой в браузер не входить; в `accounts list` у неё последний столбец («предупреждён») — `нет`.

До заведения места 09 и 10 свободны: в `accounts list` нет строк `место 09` и `место 10` за другими логинами (прежняя
проба — «Освобождение места пробной учётки»). Команды включения у `accounts` нет: отключённую учётку окна возвращают
`accounts remove --login L --yes`, затем тем же `add` с `--same-person` (логин закреплён за своим местом) и тем же
файлом пароля; `remove` стирает и подтверждение предупреждения.

### Сухой прогон (до окна, под учением)

Без денег и данных: под `DRILL_DATA=tool_error` инструменты чтения отвечают ошибкой без SQL. Прогонщик `match` в
сухом прогоне не запускается: под учением случаям неоткуда взяться (поиск тегов отвечает `error`), режимов сухого
прогона у CLI нет — прогонщик проверен интеграцией на двойнике (Д9б, Б2).

**Сухой прогон — при владельце, в начале его окна** (заход на сервер, учение, учётки, пересоздание `core` — в окне
владельца, Б3), не «в любой день до окна». Прогон оборвался после шага 0 — **сразу шаг 4**: пока учение включено,
инструменты данных агентов всех мест отвечают ошибкой.

0. Учение: `DRILL_DATA=tool_error` в `.env`, `docker compose up -d --no-build --force-recreate core`; у строки
   «Историан БДРВ» — пометка «учения: tool_error».
1. Учётки окна заведены (выше), пароли — из файлов `secrets/`.
2. Оба места будятся запросом шлюза `POST /pcbk/api/workplace/wake` учёткой окна через туннель `edge` — не браузером:
   браузер учёткой `d9-probe-vol` показал бы предупреждение раньше добровольца. Из `bench/`:
   ```bash
   for L in d9-probe-show d9-probe-vol; do uv run --python 3.12 python -c 'import json, sys
   from pathlib import Path
   from pcbk_bench.gateway import GatewayChat
   stand, login = Path(sys.argv[1]), sys.argv[2]
   raw = json.loads(stand.read_text(encoding="utf-8"))
   pw = (stand.parent / "secrets" / f"{login}.pw").read_text(encoding="ascii").strip()
   chat = GatewayChat(raw["edge_url"], raw["edge_host"], raw.get("origin"))
   chat.login(login, pw); chat.wake(timeout=120); print(login, chat.place, chat.workplace().get("state"))' "$LIVE/stand.json" "$L"; done
   ```
   Ожидается `d9-probe-show 09 ready` и `d9-probe-vol 10 ready`; затем у каждого места `mcp=connected` (до 90 с после
   старта): `docker inspect -f '{{json .State.Health.Log}}' pcbk-student-NN` — последняя запись `200 mcp=connected`.
   В модель ничего не уходит — разговоров нет: расход мест 09 и 10 по журналу вызовов до и после — тот же; у
   `d9-probe-vol` в `accounts list` — по-прежнему `нет`.
3. `python -m pcbk_bench probe-calc "$LIVE/match.config.json"` → ровно `series: error` и `runtime: error`: под учением
   поиск не отдаёт тегов, ряд и наработка не вызываются; тегов и значений в выводе нет.
4. Учение снято: `DRILL_DATA` пусто, `docker compose up -d --no-build --force-recreate core`; строки сторожа — `ok`,
   пометки учений нет.
5. Учётки остаются до окна, если оно сегодня, иначе — `accounts disable --login d9-probe-show` и
   `accounts disable --login d9-probe-vol` (в день окна — вернуть, как в «Учётках окна»).

Итог — строкой в `docs/checks/D9b.md`: «сухой прогон: учётки, пробуждение, проба (коды), уборка — да».

### Окно

0. **Начало окна** — время с поясом первой строкой журнала; от него считаются 2,5 ч.
1. **Предпосылки** (5 мин, только чтение):
   - `grep -E '^DRILL_[A-Z_]*=.' .env` — ни одной строки;
   - строки сторожа — `ok`;
   - `/health/llm` → `pending: 0`, `models` — выбранная модель;
   - учётки окна заведены и активны, места 09 и 10 закреплены только за ними;
   - `key_usage_usd` из `/health/llm` — в журнал: точка сверки расхода окна.
2. **«Да» окна** — дословно. Без «да» окно не идёт.
3. **Деньги и проба** (10 мин):
   - `.env`: `LLM_RUN_ID=d9-<ДДММ>`, `LLM_RUN_USD=10`, `LLM_RUN_CALLERS=student-09,student-10`; затем
     `docker compose up -d --no-build --force-recreate core`. Проверка: `/health/llm` → `run` с этим `id`, `callers`
     — `2`, `spent_usd` — `0`; в строке «LLM-прокси и бюджет» — «прогон d9-ДДММ: $0.00 из $10, вызовов за 10 мин:
     0». Ошибка в `LLM_RUN_*` — отказ всем вызовам `/llm/`: исправить `.env` и пересоздать `core`;
   - `python -m pcbk_bench probe-calc "$LIVE/match.config.json"` — «Коды пробы» ниже.
4. **Показ** (≤ 10 мин) — владелец по сценарию `docs/checks/D9b.md`, «Показ», учётка `d9-probe-show`.
5. **Доброволец** (≤ 45 мин на задание и 5 мин на разговор) — владелец с секундомером, учётка `d9-probe-vol`. Сессия
   смотрит только страницу состояния и строку «LLM-прокси и бюджет»: в разговор не вмешивается, поток места не
   читает. «Внимание» «прогон отбора молчит» во время пауз добровольца — ожидаемо, не сбой.
6. **Протокол** — со слов владельца, в `docs/checks/D9b.md`. T1 сверяется с `created_at` учёта места 10 — только
   время, без имени и названия агента добровольца (в них бывает узел, Д9-R94):
   `agents.admin list --place 10 | cut -s -f5` (пятый столбец — «создан»; `-s` убирает строку сверки без столбцов,
   где бывают имена). Снимок вкладки — только в `$LIVE/shots/`.
7. **Автотест** (≤ 30 мин; только если интеграция на двойнике зелёна):
   1. `python -m pcbk_bench match-cases "$LIVE/match.config.json"` →
      «случаев 19: аналоговых 3 (из агента k), состояний 1»; код `1` — тегов нет вовсе. Тег берётся только с данными
      за период (аналоговый — сводка за последние сутки, состояния — наработка за вчера; не больше трёх тегов без
      данных на вид, I-3 итогового ревью Д9б); пропущенные — хвостом «; без данных за период пропущено m». Агента
      добровольца нет — в
      конфиге `"source": {"place": 9}` (агент показа). Меньше трёх живых аналоговых тегов даже в каталоге — строка
      владельцу, прогон на том, что есть; живого тега состояния нет ни у агента, ни в каталоге — строка владельцу,
      вида `runtime_yesterday` в случаях не будет;
   2. `python -m pcbk_bench match "$LIVE/match.config.json"` (проба не прошла — с `--only basic`). В stdout — итог без
      тегов: таблица с псевдонимами `ТЕГ-n`, строка «случаев N; match k; жёстких (неуспех 4) h; service s; нет данных
      d; прочих o» и строка «успех: да» или «успех: нет (<коды>)», то же — в `$LIVE/out/match.md`; в stderr — «случай
      k/N: <вердикт>». Код `0` — успех; `3` — прогон прошёл, но автотест не успех; `2` — `RunRefused: not_ready`
      (место 09 не проснулось или нет `mcp=connected`) или `RunRefused: calc_agent` (агент проверки `bench-calc` не
      сохранён или не стал `ready` за 60 с); `1` — сбой, в stderr только класс исключения;
   3. раз в 10 минут — строка «LLM-прокси и бюджет».

   Ожидается: «успех: да» и код `0` — жёстких 0, `service` 0, **у каждого вида случаев хотя бы один `match`** и прогон
   дошёл до конца плана (решение контроллера, I-2 итогового ревью Д9б); расход окна по `run.spent_usd` ≤ $10; разница
   `key_usage_usd` от шага 1 — не больше расхода прокси + $0,05. Мягкие вердикты отдельных случаев («нет данных»,
   `no_number`, `timeout` посреди плана) успех не роняют, но «match 0; прочих 19» — не успех. Коды строки «успех: нет»:
   - `hard` — жёсткие вердикты (`mismatch`, `wrong_period`): неуспех 4;
   - `service` — сбой службы: блокер «того же кода», разбор до закрытия дня;
   - `no_match:<вид>` — у вида ни одного `match` (агент не назвал число, у тегов вида нет данных, ходы падали);
   - `cut` — прогон оборван до конца плана: у последнего случая `timeout` (потолок прокси `pcbk_run_spent` — ход с
     ошибкой, или срок хода) или результата нет.

   Итог — владельцу целиком; «успех: нет» — разбор по `$LIVE/out/match.jsonl`, автотест на выбранной модели — в Д9б′.

   Общего срока у `match` нет (19 ходов × `turn_timeout_s` 300 с — до 95 минут): **дольше 30 минут — Ctrl-C**. Уборка
   прогонщика в `finally` снимет открытые сессии и отправит `bench-calc` в архив (проверка — «Уборка», шаг 2);
   `match.md` и строка итога не пишутся, код `1` (`KeyboardInterrupt`) — не успех; итог — по строкам
   `$LIVE/out/match.jsonl` этого прогона (метка `run` — время его начала; повтор `match` дописывает файл).
8. **Уборка** (10 мин) — ниже.

Добровольца в день окна нет — окно идёт без шагов 4–6; интеграция на двойнике не зелена — без шага 7. Д9б′ — второе
окно, если не было добровольца или проба не прошла: шаги 0–3 заново (проба — после `core:d9b`), затем недостающее
из 4–7, затем 8.

### Коды пробы

`probe-calc` печатает только коды, по строке на инструмент; тегов и значений нет. Код выхода `0` при любых строках —
годность решают строки; `1` и имя класса в stderr — сбой самой команды (туннель, `stand.json`, токен).
Тег пробы — первый живой тег вида из поиска **с данными за период**: «живой» значит лишь «в Live есть ненулевое
значение», поэтому тег без истории (элемент `no_data`) пропускается и берётся следующий живой — не больше трёх тегов
на инструмент (I-3 итогового ревью Д9б).
- `series: ok rows=<n>/8 grid=even|uneven ends=yes|no` — ряд `tag_series` за `last_8h` на 8 точек по живому
  аналоговому тегу из поиска `_pv`: `rows` — сколько точек пришло; `grid=even` — шаги между метками равны ± 1 с;
  `ends=yes` — первая метка совпала с началом периода, последняя — с концом (± 1 с);
- `runtime: ok states=<k> covered=<p> sum_hours_ok=да|нет` — наработка `calc_runtime` за `yesterday` по живому тегу
  состояния из поиска `_run`: `states` — число состояний, `covered` — покрытие периода, % (`-` — служба не дала),
  `sum_hours_ok=да` — сумма часов отличается от длины периода меньше чем на 1 %;
- `<series|runtime>: <статус>` — поиск тега ответил не `ok` или вызов — не `ok`/`partial`: статус ответа (`error` — в
  том числе под учением, `unavailable`, `rate` после всех повторов, `budget`, `refused` …); `no_item` — элемента тега
  в ответе нет; иначе статус элемента не `ok` и не `no_data`; `failed` — статус не по договору;
- `<series|runtime>: no_tag` — поиск ответил, а живого тега нужного вида нет;
- `<series|runtime>: unresolved` — **проба не решена**: у первых трёх живых тегов вида (или у всех, если их меньше)
  нет данных за период. Форма запроса на живых данных не проверена — это не «проба не прошла».

**Проба прошла** — `series: ok rows=8/8 grid=even` (`ends` — любое) и `runtime: ok … sum_hours_ok=да`. Тогда строка
«стенд открыт для студентов» — в журнал и владельцу. `ends` — в журнал: при `ends=no` ряд двойника правится по факту
историана при закрытии дня (Б5), служба о концах ничего не предполагает — это не провал пробы.

**Проба не решена** (`unresolved` хотя бы в одной строке, другая — «прошла») — не правка SQL и не `core:d9b`: строки
журнала `core` вызовов пробы (команда ниже) — `outcome=partial` без `error=`, SQL ушёл и ответил без строк. Что делать
— строка владельцу: он берёт тег вида с историей вручную, по своему знанию узла, на вкладке «Калькуляторы» учётки
показа — тег → «Профиль: как менялся параметр», «Последние 8 часов» (тег состояния — «Наработка по тегу состояния»,
«Вчера») → «Посчитать». Ответ с числами — форма работает на живых данных, это проба вручную (журнал `core`:
`tool=tag_series|calc_runtime caller=student-09 channel=web outcome=ok|partial`), строка в журнал. Тега с историей у
владельца нет или он не берётся — окно идёт, как при непройденной пробе (ниже), решение о стенде — за владельцем.

**Проба не прошла** (любая другая строка) — окно идёт без новых инструментов: доброволец и автотест только по видам
`now`, `avg_*`, `minmax_*`, `range_*` (`match --only basic`); стенд для студентов закрыт до `core:d9b`, правка SQL —
в Д9б′. Причина — по строкам журнала `core` вызовов пробы (`tool=… outcome=… error=…`; тегов в них нет, только их
число):
```bash
docker logs --since <начало окна> pcbk-core 2>&1 | grep -E 'tool=(catalog_search|tag_series|calc_runtime) caller=ops channel=http '
```

### Уборка после окна

Не позже 2,5 ч от начала окна; оборванную уборку продолжать с первого несделанного шага.
1. В `.env` — пусто у `LLM_RUN_ID`, `LLM_RUN_USD`, `LLM_RUN_CALLERS`; затем
   `docker compose up -d --no-build --force-recreate core`; `/health/llm` → `run: null`.
2. `bench-calc` — в архиве: прогонщик уносит его сам. Проверка — в `agents.admin list --place 9` нет строки с именем
   `bench-calc` (второй столбец); есть (прогонщик оборвался) —
   `agents.admin archive --place 9 --name bench-calc --yes`. Прочих агентов места 09 не трогать.
3. Учётки — `accounts disable --login d9-probe-show` и `accounts disable --login d9-probe-vol`: сессии закрыты, места
   остаются за ними.
4. Проверка: `DRILL_*` в `.env` пусты; строки сторожа — `ok`; в `accounts list` обе учётки — «отключена»; от начала
   окна — не больше 2,5 ч. Итог — строкой в журнал дня.

Агенты и разговоры показа и добровольца (и разговоры автотеста на месте 09) остаются до решения владельца, как и
данные окна в `$LIVE`; после решения — «Освобождение места пробной учётки» для мест 09 и 10 (`archive` → `remove` →
`release-place`).

Окно оборвалось или день кончился без уборки — те же шаги при первой возможности; проверка — «Аварийная уборка
режима отбора», шаг 1 (`LLM_RUN_*` пусты, `run: null`), и в `accounts list` учётки `d9-probe-*` — «отключена».

## Кабинет преподавателя (с Д10а)

Кабинет — страница чата для учётки преподавателя: вход на `/login` сам ведёт в кабинет («Открываю кабинет
преподавателя…»), места у преподавателя нет, и вход ничего не будит. Ручки — `/pcbk/api/teacher/…`; роль `core`
проверяет на сервере у каждой ручки: студенту — 403. У `edge` — `location /pcbk/api/teacher/`: корзина ручек места
(не чаще 5 в секунду на cookie, запас 20), тело до 64 КиБ, срок ответа 30 с. Команды `accounts` и `agents.admin`
ниже — через `docker exec pcbk-core python -m …`. На сервере кабинет появляется окном Б4 (ниже).

- **Что видит и может преподаватель:**
  - обзор десяти мест: логин студента («свободно» — закрепления нет, «без учётки» — место закреплено за удалённым
    логином), пометки учётки («отключена», «замок», «не подтвердил предупреждение»), состояние места, доля,
    потрачено, число агентов; общий бюджет и расход `ops`;
  - разговоры и агенты студента — только на чтение. Разговор читается через само место тремя GET, агенты — из учёта
    и каталога службы, место для них не нужно;
  - «Разбудить для просмотра» у спящего места; чтение разговоров спящее место не будит;
  - перевод доли бюджета на модель между местами и «Остановить место»;
  - журнал действий преподавателя — последние 50 строк;
  - с Д10б — учётки студентов на свободных местах и вкладка «Калькуляторы» (ниже и «Калькуляторы (с Д9а)»).
- **Чего нельзя:**
  - писать в разговоры и агентов студента: любой запрос учёткой преподавателя к `/pcbk/api/oc/` (например, `POST
    /pcbk/api/oc/session`) — 403 «Преподаватель читает разговоры студентов в кабинете — писать в них нельзя»;
  - учётки преподавателей: кабинет их не показывает и не трогает, преподаватель заводится только командой
    администратора («Учётка преподавателя»);
  - удалять учётки, возвращать логин (`--same-person`) и менять студента в занятом месте — «Новый студент».
- **Пробуждение кабинетом — обычный запуск места**, как при входе студента: сохранённые, но не перезагруженные
  агенты места перезагружаются, файл агента, ломающий место, уходит в карантин («Карантин (с Д7б)»). Разговоры и
  исправные агенты не меняются. Засыпает место само, по простою (30 минут).
- **Доли бюджета на модель** — целые центы в `core.db` (таблица `llm_shares`; строка есть только у изменённых мест,
  прочие — по умолчанию `LLM_SHARE_USD`, $100):
  - изменение — всё или ничего, со сверкой «было»: две вкладки друг друга не затирают, опоздавшая получает «Доли
    изменились, пока страница была открыта, — обновите её»;
  - доля не ниже пола — оплаченного местом, округлённого вниз до цента с запасом против шума (на ровной сумме — на
    цент ниже: $0,30 → 29 центов);
  - сумма долей десяти мест после изменения — не больше общего бюджета (`LLM_TOTAL_USD`), а если уже была больше —
    не растёт. Свободный остаток — бюджет минус сумма долей;
  - «Остановить место» — серверная операция: доля места сводится к полу, разница уходит другому месту, пол берёт
    сервер под тем же замком, что перевод. Следующий же вызов модели этого места — отказ «доля израсходована».
    Остаток доли уходит ниже нуля только меньше чем на цент и на вызовы, начатые до перевода;
  - доля `ops` («отбор и проверки», `LLM_OPS_SHARE_USD`) — вне таблицы и суммы долей мест.
- **Расхождение долей** — строка «Кабинет преподавателя и доли» «сбой» «LLM-прокси считает доли не так, как
  сохранено» (пометка «мест: N»). Сверка память прокси не чинит: `docker compose up -d --no-build --force-recreate
  core` (доли перечитываются с диска), затем разбор — журнал действий (строки `shares`) и журнал `core`.
- **Журнал действий** — `accounts journal` (`--last N`, N — 1…1000, по умолчанию 50): новые первыми, через
  табуляцию — время, логин преподавателя, действие (`sessions`, `read`, `agents`, `agent`, `wake`, `shares`, с Д10б
  — `account_*`), место, цель (разговор, агент или логин учётки), итог, подробности — только коды и числа. Только
  чтение; строки не удаляются. Чтение и пробуждение пишутся строкой `requested` до доступа: строка не записалась —
  доступа нет.
- **Учения `DRILL_TEACHER`** — в `.env`, затем `docker compose up -d --no-build --force-recreate core`; снять —
  `DRILL_TEACHER=` (пусто) и так же пересоздать `core`:
  - `shares_stale` — перевод доли пишет диск и журнал действий, а память LLM-прокси нет: строка — «сбой», расхождение;
  - `journal_down` — журнал действий не пишется: кабинет закрыт, ручки — 503 «Журнал действий преподавателя не
    пишется — просмотр и изменения закрыты», строка — «сбой» «журнал действий преподавателя не пишется — кабинет
    закрыт».

  К пометке строки дописано «учения: <режим>». **Опечатка в значении** — ошибка настройки: вход (`config_error`
  шлюза) и LLM-прокси закрыты, процесс `core` жив. При `config_error` шлюза строка кабинета — «неизвестно» «сверка
  долей ещё не проходила», причина — в строке «Шлюз и вход».
- **Смена версии OpenCode** — сверить виды частей стенограммы: кабинет пересказывает ответ OpenCode 1.18.33 сам и
  незнакомые виды частей пропускает — у новой версии часть разговора может не дойти до преподавателя.
- **Откат** — раздел «Откат», «Д10 — назад к Д9б или Д9а».

### Учётка преподавателя

Заводит только администратор. Логин называет владелец (по умолчанию `prepod`). Пароль порождает команда, и он сразу
уходит в файл на машине разработчика — ни в чат, ни в git, ни на экран. `LIVE` — каталог окна на машине
разработчика, `LIVE=$JOB/pcbk-d10/live` (`0700`, не в git):
```bash
umask 077 && install -d -m 0700 "$LIVE/secrets" && f="$LIVE/secrets/<логин>.pw" &&
{ [ ! -e "$f" ] || { echo "$f уже есть"; false; }; } &&
ssh <сервер> docker exec pcbk-core python -m pcbk_core.gateway.accounts add --login <логин> --role teacher --generate > "$f"
[ -s "$f" ] || { rm -f "$f"; echo "учётка не заведена"; }
```
Сверка — в `accounts list` строка `<логин> teacher - активна -`. Абсолютный путь файла — строкой владельцу: пароль он
читает сам. Сменить пароль владелец может сам, когда удобно (с терминала, без эха; сессии учётки закрываются):
`ssh -t <сервер> docker exec -it pcbk-core python -m pcbk_core.gateway.accounts passwd --login <логин>`. Вход — на
`/login` через туннель (`ssh -N -L 28443:127.0.0.1:8443 <сервер>`) или из сети ПЦБК: страница ведёт в кабинет.
Отключить — `accounts disable --login <логин>`; удалить — после `disable`: `accounts remove --login <логин> --yes`.

### Учётные записи у преподавателя (с Д10б)

Раздел «Учётные записи» кабинета, под таблицей мест; кнопки учётки — в строке места, колонка «Учётная запись».
Только студенты: учётки преподавателей кабинет не показывает и не трогает.

- **«Завести студента»** — логин (3…32 знака из a-z, 0-9, «.», «_», «-», первый — буква a-z) и свободное место.
  Пароль (12 знаков) показан один раз — преподаватель записывает его и передаёт студенту; «Я записал пароль» стирает
  его со страницы. **Логин закрепляется за местом навсегда**: опечатка в логине занимает одно из десяти мест, а снять
  закрепление можно только у пробных логинов («Новый студент»).
- **Логины вида `dN-probe-…` — только для проверок.** Место пробного логина администратор освобождает `release-place`
  («Освобождение места пробной учётки»): если такой логин выдан живому студенту, уборка пробных мест может отдать его
  место — с разговорами на томах — другому человеку. Живым студентам — обычные логины.
- **«Новый пароль»** — тоже один раз; старый перестаёт работать, сессии студента закрываются, замок входа снимается.
- **«Отключить»** — сессии студента закрываются, место остаётся за его логином, вход тем же паролем — отказ. Студент
  не сможет продолжить: чат закроется при его следующем действии (при открытом потоке ответа — за перепроверку
  сессии, до 10 с). **Отключение не прерывает уже идущий ответ агента:** ход, начатый до отключения, доходит до конца
  — ответ появится в разговоре, вызовы модели идут по доле места. Остановить расход сразу — «Остановить место»:
  следующий же вызов модели места получит отказ. Место засыпает само, по простою.
- **«Включить»** — только на своё место: оно закреплено за этим логином, и на нём нет действующей учётки
  (`accounts enable --login L` — то же для администратора).
- **«Снять замок»** — замок входа после неудачных входов (5 за 15 минут).
- Отказ правил (логин уже есть, место занято или закреплено за другим логином) — текстом хранилища на странице,
  ничего не меняется. 503 — журнал действий не пишется или сбой учёток: действия нет.

**Выданный пароль известен преподавателю (I-10).** Кабинет — только чтение, но вход по выданному или сменённому
паролю — это вход студентом: преподаватель технически может войти как студент, писать в его разговоры и агентов и
читать их мимо журнала чтений. След:
- строки `account_add` и `account_passwd` журнала действий (`accounts journal`: логин преподавателя, логин студента —
  в столбце цели);
- строка `gw login ok login=<студент> …` журнала `core` (`docker logs pcbk-core`). Студента и преподавателя она не
  различает — сверять по времени со строками `account_*`.

Так решено по умолчанию: план Д10 (`docs/plans/PLAN-D10-teacher-2026-10-11.md`), «Что нужно от владельца», п. 4 (ж),
и «Открытые вопросы», п. 6.

Пароль не попадает ни в журнал действий, ни в журнал службы — строка `gw teacher login=… action=account_… place=…
outcome=… status=… ms=… target=<логин>`, ни в журнал доступа `edge` (в нём метод и путь); ответы с паролем — с
`Cache-Control: no-store`.

## Окно Б4: выкладка Д10а и Д10б

Одно окно (Д10-R107, R111): `core:d10b` (в нём Д10а, Д10б и Д9б), `watchdog:d10a`, конфиг `edge` и
`edge/static/login.js`, статика чата. Места не пересоздаются: образ места прежний, `student:d9a`. Денег OpenRouter и
чтения историана окно не тратит; разговоры и агенты мест 09 и 10 (данные окна Д9) учения не открывают, учётки
студентов на сервере не заводятся. **На сервере не выполнялось.** `LIVE` — как в «Учётке преподавателя»; команды
`accounts` — через `docker exec pcbk-core python -m …`; строки сторожа — `/status.json` («Выкладка и обновление»,
шаг 5).

0. **Секрет учений** — пароль учётки `d10-probe-t`, при повторе не перезаписывается:
   ```bash
   umask 077 && install -d -m 0700 "$LIVE/secrets" && f=$LIVE/secrets/d10-probe-t.pw &&
   { [ -e "$f" ] || python3 -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(12))' > "$f"; }
   ```
1. **Предпосылки** (только чтение):
   - выложены Д3б…Д9б;
   - в `d10b/accounts` влит Д9б (`git merge-base --is-ancestor d9b/match <голова>` → код `0`; коммиты окна Д9б′,
     если были, — тоже), и полный прогон интеграции на этой голове зелёный (`docs/checks/D10b.md`); иначе образы для
     сервера не собираются, окно ждёт;
   - откатный образ: `core:d9b`, если он есть на сервере, иначе `core:d9a`, и `watchdog:d9a` — их ID
     (`docker image inspect -f '{{.Id}}' <образ>`) записаны в `docs/checks/D10b.md`;
   - «да» владельца на окно — дословно (план Д10, «Что нужно от владельца», п. 1);
   - строки сторожа — норма, `stale: false`; `grep -E '^DRILL_[A-Z_]*=.' .env` — ни одной строки.
2. **Выкладка.** Образы `core:d10b` и `watchdog:d10a` пересобираются от той же головы, ID обоих — в
   `docs/checks/D10b.md`, слои сверены по `RootFS` («Выкладка и обновление», шаг 1); статика чата — из того же
   коммита. Перед копированием на сервере — копии для отката: `cd /opt/pcbk-reserve && cp -p compose.yaml
   compose.yaml.<прошлый день> && cp -p edge/static/login.js login.js.<прошлый день>`. Порядок:
   1. `compose.yaml` — шаг 2 «Выкладки»; `docker compose up -d --no-build core` — `core:d10b`;
   2. `docker compose up -d --no-build watchdog` — `watchdog:d10a`, новая строка «Кабинет преподавателя и доли»;
   3. `edge/` (шаблон с `location /pcbk/api/teacher/` и `edge/static/login.js`) — шаг 2 «Выкладки», затем
      `docker compose restart edge`: шаблон `edge` берёт только при перезапуске;
   4. статика чата — раздел «Статика чата».

   Ожидается: `docker ps -a --format '{{.Names}} {{.Image}}'` — `core:d10b`, `watchdog:d10a`, места прежние; строка
   «Кабинет преподавателя и доли» — норма, пометка «распределено $1000.00 из $1000; …»; строка «LLM-прокси и
   бюджет» — прежняя; `grep -c 'Открываю кабинет преподавателя' /opt/pcbk-reserve/edge/static/login.js` → `1`.
3. **Учётка учений и кабинет (Д10а).** `d10-probe-t` — преподаватель, без места; пароль одной строкой stdin из
   файла:
   `ssh <сервер> docker exec -i pcbk-core python -m pcbk_core.gateway.accounts add --login d10-probe-t --role teacher < "$LIVE/secrets/d10-probe-t.pw"`.
   Через туннель — вход на `/login`: страница сама ведёт в кабинет; снимок обзора
   `docs/checks/D10a/08-server-cabinet.png` (логины проб и числа, без разговоров). Затем `POST /pcbk/api/oc/session`
   этой учёткой → 403 «Преподаватель читает разговоры студентов в кабинете — писать в них нельзя». Разговоры и агентов
   мест учётка учений не открывает.
4. **Учения Д10а** (Д10-R107, п. 1 и 2):
   1. `DRILL_TEACHER=shares_stale`, `core` пересоздан. Перевод 1 цента с места 10 на 09 → строка «Кабинет
      преподавателя и доли» — «сбой» «LLM-прокси считает доли не так, как сохранено», пометка «учения: shares_stale;
      мест: 2»; снимок `docs/checks/D10a/06-server-drift-row.png`. Учение снято, `core` пересоздан — память по диску,
      строка — норма. Перевод обратно: доли 09 и 10 — прежние.
   2. **Сначала без учения:** `GET /pcbk/api/teacher/places/10` → `sleeping`. Место не спит — учение 2 не делается:
      строка владельцу, учение — когда место 10 спит. Затем `DRILL_TEACHER=journal_down`, `core` пересоздан;
      `GET …/places/10/sessions` → 503 «Журнал действий преподавателя не пишется — просмотр и изменения закрыты»; в
      журнале `core` нет строки `gw teacher … action=sessions … status=200`; место 10 по-прежнему спит (`docker ps
      -a`); строка — «сбой» «журнал действий преподавателя не пишется — кабинет закрыт»; снимок
      `docs/checks/D10a/07-server-journal-row.png`. Учение снято, `core` пересоздан, строка — норма.
   3. `accounts journal --last 10` — строки `shares` (`ok`) учётки `d10-probe-t`, без текстов.
5. **Проверка Д10б** (Д10-R111) той же учёткой: раздел «Учётные записи» виден, учётки не заводятся (места до пилота
   не тратятся). Калькулятор — только под учением: `DRILL_DATA=tool_error`, `core` пересоздан (учение кладёт и
   агентов студентов — только без студентов); вкладка «Калькуляторы», слово «тест» → «Найти тег» — «сбой службы
   данных — вызов записан в журнал»; в журнале `core` — строка `tool=catalog_search caller=teacher channel=web …
   outcome=error`. Затем `DRILL_DATA=` (пусто), `core` пересоздан; `d10-probe-t` — `disable`, затем `remove --yes`
   (места нет — освобождать нечего). Ожидается: `DRILL_*` пусты, строки — норма, сумма долей — $1000.00.
6. **Учётка преподавателя** — «Учётка преподавателя» выше; путь файла пароля — строкой владельцу.
7. **Журнал** — `docs/checks/D10a.md` (раздел «Сервер — окно Б4»), `docs/checks/D10b.md` и снимки 06–08: в git —
   только после проверки на секреты и данные и просмотра снимков глазами.

## Память сервера (с Д11)

Строка «Память сервера» сторожа `:d11a` и причины перезапуска контейнеров (Д11-R114…R116). **На сервере не
выполнялось.** Выкладка Д11а — окном 1 (Д11в, шаг В1 плана Д11 `docs/plans/PLAN-D11-load-drills-2026-10-12.md`)
после выкладок Д3б…Д10 и «да» владельца: копия `compose.yaml.<прошлый день>`, шаг 2 «Выкладки», `.env` — новые строки
по `deploy/env.example` (пустые — умолчания), затем `docker compose up -d --no-build core` (`core:d11a`) и `docker
compose up -d --no-build watchdog` (`watchdog:d11a`); места не пересоздаются. Ожидается: строка «Память сервера» с
давлением и swap в пометке, `/health/llm` — `run_brake: null`.

**Замер** — в начале каждого такта сторожа (10 с): `/proc/meminfo` (`MemAvailable`, `MemTotal`, `SwapTotal`,
`SwapFree`), `/proc/vmstat` (счётчик ядра `oom_kill` — все убийства по памяти, и по нехватке памяти сервера, и по лимиту
контейнера) и `/proc/pressure/memory` (давление `some` и `full`, `avg10`). Сторож и `core` работают под `runc` и видят
эти файлы хоста; сверка с хостом (`free -m`, `oom_kill`) — шаг В1. История замеров живёт в памяти сторожа: его
пересоздание начинает её заново — счёт убийств за час, рост swap за 10 минут, удержание давления, причины перезапусков.

**Состояния** — первое подходящее сверху; тексты постоянные, числа — в пометке:

| Условие | Состояние | Текст |
|---|---|---|
| нет `MemAvailable` | неизвестно | «нет данных о свободной памяти» |
| свободно меньше `MEM_FAIL_MIB` | сбой | «свободно меньше F МиБ» |
| датчик, который уже давал данные, замолчал | неизвестно | «датчик памяти замолчал — убийства, давление или swap не читаются» |
| убийства по памяти вне мест и `core` за час | внимание | «сервер убивал процессы по памяти вне мест и core» |
| давление `full avg10` выше `PSI_FULL_MAX` 3 такта подряд | внимание | «сервер ждёт освобождения памяти — давление выше порога» |
| swap вырос за 10 мин не меньше `SWAP_GROW_WARN_MIB` | внимание | «сервер вытесняет память в swap» |
| свободно меньше `MEM_WARN_MIB` | внимание | «свободно меньше W МиБ» |
| иначе | норма | «свободно N МиБ» |

Жёсткие для нагрузки условия (убийства, давление, swap) стоят выше мягкого порога: в полосе «свободно меньше 2048 МиБ»
строка покажет их, а не порог. Пометка — части через « · »: «свободно N МиБ» (кроме нормы), «swap занят M МиБ (+G МиБ
за 10 мин)», «давление памяти some S %, full F %», «убийств по памяти вне мест и core за час: K» (только при K больше
нуля), «учения: порог выше всей памяти» (порог `MEM_WARN_MIB` или `MEM_FAIL_MIB` выше `MemTotal`).

**Пороги** — в `.env` (или окружением команды), затем `docker compose up -d --no-build --force-recreate watchdog`.
Значение вне пределов — сторож не стартует:
- `MEM_WARN_MIB` (2048) и `MEM_FAIL_MIB` (1024), МиБ свободной памяти — как с Д1;
- `SWAP_GROW_WARN_MIB` (512, от 1 до 1048576 МиБ) — рост занятого swap за 10 минут: последний замер минус наименьший в
  окне. Swap, который вырос и больше не растёт, через 10 минут перестаёт быть ростом. Рост от базы окна нагрузки строка
  не считает — это делают тормоза (ниже и прогонщик Д11б);
- `PSI_FULL_MAX` (10 %, больше 0 и не больше 100) — «внимание» после 3 тактов подряд выше порога, снятие — после 3
  тактов подряд не выше: шум у порога строку не дёргает. Окно 1 Д11 ставит `max(5, 3 × база)` по замеру сервера.

**«Вне мест и core»** — убийство по памяти, которое не объяснил перезапуск ни одного контейнера стенда со своей строкой
вида «контейнер» на странице, то есть по сути убийство вне контейнеров стенда (текст строки короче — «вне мест и core»).
Перезапуск по памяти объясняется у всех таких строк — мест 01–10, контейнера `core` и прокси сокета серверного слоя
`sp-ctl`: их убийство строка памяти не считает, строка самого контейнера говорит «перезапущен: убит по памяти». Всё
остальное — Dify, процессы системы, а также `edge`, прокси сокета сторожа `sp-ro` и сам сторож (у них строк вида
«контейнер» нет) — «вне мест и core». Убийство, которое за 2 такта (20 с) ничто не объяснило, строка показывает;
«внимание» держится, пока убийство в окне часа, и гаснет само.

**«Датчик памяти замолчал» и «нет данных: …».** Датчики строки — swap (`SwapTotal` и `SwapFree`), vmstat (`oom_kill`)
и давление (`full avg10`):
- датчик без данных в этом такте — на месте его части пометки «нет данных: swap», «нет данных: vmstat» или «нет данных:
  давление». Пометка не говорит «убийств нет» или «давление в норме» по молчащему датчику. Если датчик в этой истории
  сторожа не читался ни разу (например, ядро без файла давления), состояние считается по остальным правилам — файлы
  vmstat и давления сторожу необязательны;
- датчик уже давал данные, а теперь молчит или отвечает не по формату — «неизвестно» «датчик памяти замолчал — …»; выше
  него только «сбой» по свободной памяти. Причина перезапуска в такте без счётчика `oom_kill` неизвестна — текст Д2.
  Датчик вернулся — строка снова по обычным правилам; «внимание» по давлению, пока файл давления молчит, само не
  снимается. Тормоз `core` такое молчание считает причиной «память не читается» («Тормоз прогона по памяти»).

**Причина перезапуска** (R115) — у строк мест, `core` и `sp-ctl`:
- «перезапущен: убит по памяти в ЧЧ:ММ UTC+пояс (N с последнего запуска)» — Docker перезапустил контейнер политикой, и в
  окне `Restarting` у него был `OOMKilled` или счётчик `oom_kill` вырос между последним тактом до перезапуска и
  осмотром после него;
- «перезапущен: сбой не по памяти в …» — перезапуск без роста счётчика (например, процесс убит сигналом);
- «перезапущен после сбоя в …» (текст Д2) — причина неизвестна: счётчик не читается или сторож не видел контейнер
  работающим до перезапуска (перезапуск до старта сторожа);
- «падает в цикле (перезапуски подряд)» — в пометке «последний — убит по памяти» или «последний — сбой не по памяти».

«Перезапущен: …» держится 15 минут после старта, затем — «работает, сбоев с последнего запуска: N». Пробуждение места
шлюзом (`docker start`) — не сбой. Сбой места не по памяти в одном такте с убийством у Dify даст месту «убит по памяти»,
а убийство у Dify во «вне» не попадёт — редко, а в нагрузке такой прирост `oom_kill` всё равно останавливает прогон
тормозом `core`. На тестовом стенде (`runc`) причина проверена настоящим переполнением места; что под gVisor убийство
места растит `oom_kill` ровно на 1, проверяет шаг В1.

**Учения строки памяти** — порог выше всей памяти: `MEM_WARN_MIB=1000000` — «внимание» «свободно меньше 1000000 МиБ»,
`MEM_FAIL_MIB=1000000` — «сбой»; в пометке — «учения: порог выше всей памяти», над строками страницы — абзац «Идут
учения: Память сервера». Включить — значение и пересоздание сторожа (как у порогов выше); снять — прежнее значение и
снова пересоздать сторожа. Две оговорки — из-за порога тормоза `core`, который по умолчанию наследует `MEM_FAIL_MIB`:
- порог `core` ограничен 1048576 МиБ. `MEM_FAIL_MIB` выше 1048576 в `.env` при пустом `LLM_RUN_MEM_FAIL_MIB` при
  следующем пересоздании `core` даёт ошибку настройки LLM-прокси — отказ всем вызовам `/llm/`, с прогоном и без него.
  Учение ставит 1000000 — проходит;
- учение `memory-fail` пересоздаёт только сторожа. Если `MEM_FAIL_MIB` учения стоит в `.env` и в это время пересоздают
  `core` при заданных `LLM_RUN_*` и пустом `LLM_RUN_MEM_FAIL_MIB`, тормоз прогона возьмёт порог учения и через 3 проверки
  (≈ 10–15 с) защёлкнется по «мало памяти»: вызовы модели мест прогона — отказ, места прогона спят. Снять — прежний
  `MEM_FAIL_MIB`, затем `brake clear` и пересоздание `core` («Тормоз прогона по памяти», «Снять защёлку»).

**Учения без пометки.** Пометку «учени» и абзац «Идут учения» дают только учения с полем `drill` (`DRILL_DATA`,
`DRILL_LLM`, `DRILL_GATEWAY`, `DRILL_AGENTS`, `DRILL_TEACHER`) и порог памяти выше всей памяти (у сторожа и у тормоза
`core`). Без пометки идут: переполнение места (`tail /dev/zero`) и убийство его процесса, `docker stop` и `docker
pause`, `DRILL_FRESHNESS`, `DRILL_FREEZE_LOOP`, `LLM_RUN_SILENT_S`, правка статики чата, свои пороги давления и swap (`PSI_FULL_MAX`,
`SWAP_GROW_WARN_MIB`, `LLM_RUN_SWAP_GROW_MIB`, `LLM_RUN_PSI_FULL_MAX`) и порог свободной памяти ниже всей памяти. Их
время начала и конца — строкой в журнал окна: страница сама не скажет, что это учение.

**`oom_score_adj` мест (Д11-R132) — не применено, вопрос владельцу.** Если память кончится раньше опроса тормозов, ядро
само выберет, кого убить, и может выбрать процесс Dify; `oom_score_adj: 1000` у мест заставил бы его жертвовать местами.
Это решение владельца, «да» нет. Проба, которая ничего не меняет, — шаг В1 окна 1: одноразовый контейнер образа места
`--runtime=runsc --oom-score-adj=1000 --network none --entrypoint sleep … 30` и `oom_score_adj` его процессов на хосте.

## Тормоз прогона по памяти (с Д11)

Второй тормоз нагрузки, независимый от прогонщика (Д11-R117, R118): `core:d11a` сам читает память сервера и жёстко
останавливает прогон — и при мёртвом прогонщике, и после своего перезапуска. **На сервере не выполнялось** (выкладка —
окно 1, «Память сервера (с Д11)»). Команды — через `docker exec pcbk-core …`; `/health/llm` — как в «Прогоне отбора
модели».

**Когда работает.** Только при прогоне: заданы все три `LLM_RUN_ID`, `LLM_RUN_USD`, `LLM_RUN_CALLERS` («Прогон отбора
модели») и нет ошибки настройки LLM-прокси. Без прогона тормоза нет вовсе: нехватка памяти или давление вне нагрузки —
только строка «Память сервера». При прогоне `core` сразу после старта и затем раз в 5 с читает `/proc/meminfo`,
`/proc/vmstat` и `/proc/pressure/memory` хоста; в журнале при старте — строка «LLM-прокси: тормоз прогона <id>:
свободно меньше F МиБ, swap +S МиБ (база …), давление full > Q %». Если при прогоне не открылся журнал вызовов
`core.db`, защёлку прогона из `llm_meta` не прочесть: тормоз сразу защёлкивается с причиной «память не читается» (только
в памяти процесса), места прогона спят, вызовам и так отказ — ошибка настройки. Пересоздание `core` с открывшимся
журналом это снимает.

**Причины защёлки** (в скобках — код в `/health/llm`):
- «мало памяти» (`memory`) — свободно меньше `LLM_RUN_MEM_FAIL_MIB`;
- «растёт swap» (`swap`) — занятый swap минус база прогона не меньше `LLM_RUN_SWAP_GROW_MIB` или рост за 10 минут не
  меньше того же порога; база — `LLM_RUN_SWAP_BASE_MIB`, пусто — первая проверка прогона;
- «давление» (`psi`) — давление `full avg10` выше `LLM_RUN_PSI_FULL_MAX`;
- «убийство по памяти» (`oom`) — счётчик `oom_kill` вырос от базы (первая проверка прогона): любое убийство — места,
  `core`, Dify, системы;
- «память не читается» (`blind`) — нет `MemAvailable` или `oom_kill` (meminfo и vmstat тормозу обязательны), или swap
  либо давление, которые этот процесс `core` уже читал, замолчали или отвечают не по формату. Давление, которое не
  читалось ни разу (ядро без файла), — не причина: в `/health/llm` у него `null`.

`memory`, `swap`, `psi` и `blind` защёлкиваются после 3 проверок подряд с условием (≈ 10–15 с): один шумный отсчёт
прогон не роняет. `oom` — сразу. Условие ушло — защёлка стоит. Swap контейнеров Dify `core` не видит (чужие cgroup —
новых прав ему не дано): это условие держит только прогонщик (Д11б).

**Пороги** — в `.env`, затем `docker compose up -d --no-build --force-recreate core`:
- `LLM_RUN_MEM_FAIL_MIB` — пусто: `MEM_FAIL_MIB` сторожа, иначе 1024; от 1 до 1048576 МиБ;
- `LLM_RUN_SWAP_GROW_MIB` — пусто: `SWAP_GROW_WARN_MIB`, иначе 512; от 1 до 1048576 МиБ;
- `LLM_RUN_SWAP_BASE_MIB` — пусто: первая проверка прогона (хранится, см. ниже); иначе от 0 до 1048576 МиБ. В окнах Д11
  — база окна, записанная до нагрузки;
- `LLM_RUN_PSI_FULL_MAX` — пусто: `PSI_FULL_MAX`, иначе 10; больше 0 и не больше 100 %.

Значение вне пределов — ошибка настройки LLM-прокси, отказ всем вызовам `/llm/` (как опечатка в `LLM_RUN_*`); так же
действует унаследованный `MEM_FAIL_MIB` выше 1048576 («Память сервера (с Д11)», «Учения строки памяти»). Свои пороги
тормоза строку «Память сервера» не трогают.

**Под защёлкой:**
- вызовы модели вызывающих прогона (мест из `LLM_RUN_CALLERS`, и `ops`, если он там) — 402 `pcbk_run_halted` «Прогон
  остановлен: сервер на пределе памяти — модель не отвечает. Сообщите преподавателю.» Общий бюджет проверяется раньше
  тормоза; прочие вызывающие работают как прежде;
- шлюз смотрит на защёлку раз в секунду — и при своей ошибке настройки, когда уборщика нет, — и по ней снимает задачи
  пробуждения мест прогона и останавливает их (уже остановленное — удача), а каждый такт уборщика (30 с), пока защёлка
  стоит, останавливает любое работающее место прогона — и занятое, и поднятое отменённым пробуждением, и
  `docker start` мимо шлюза. Обычно места засыпают за секунды после защёлки. Два редких края дольше: пробуждение, чей
  `start` уже ушёл в прокси сокета, может поднять место после остановки — его гасит следующий такт уборщика (до 30 с);
  остановка ждёт замок места, который воркер перезагрузки агентов держит до ≈ 12 с;
- пробуждение места прогона — отказ: вход студента и «Разбудить для просмотра» в кабинете — 503 «Прогон остановлен:
  сервер на пределе памяти — место не будится.» (тот же текст — на экране места); перезапуск места после карантина
  агента — тихий отказ, строка журнала `gw halt restart place=NN skipped`. Учётки студентов в кабинете (Д10б) работают;
- журнал `core`: `llm brake halted run=<id> reason=<код> …`, `шлюз: тормоз прогона — места прогона усыпляются: N`,
  `gw stop place=NN reason=run_halted result=ok`, `gw wake place=NN result=refused kind=run_halted`.

На тестовом стенде без прогонщика (сроки — по вызову модели токеном места и `State.FinishedAt`, не по журналу тормоза)
после падения памяти: 402 у места — через 13,7–15,9 с, места остановлены — через 12,7–15,1 с (три прогона); пороги
плана — 20 и 35 с, раньше удержания 3 проверок (≈ 10 с) — нет (`docs/checks/D11a.md`).

**Что видно:**
- `/health/llm` — блок `run_brake`: `null` без прогона; при прогоне ровно 13 ключей: `state` (`watching` или `halted`),
  `reason` (код причины или `null`), замер — `avail_mib`, `total_mib`, `swap_growth_mib` (от базы), `psi_full10`,
  `oom_kills` (от базы), пороги и база — `fail_mib`, `swap_grow_mib`, `swap_base_mib`, `psi_full_max`; `age_s` —
  секунд с последней проверки, `halted_at` — время защёлки;
- строка «LLM-прокси и бюджет»: «внимание» «прогон остановлен тормозом памяти — вызовы прогона получают отказ»;
  тормоз не проверял память дольше 30 с — «внимание» «тормоз прогона по памяти молчит — прогон без защиты». В пометке
  — «тормоз: свободно N МиБ (порог F), swap +G МиБ от базы (порог S), давление P % (порог Q), убийств K», при защёлке —
  «— остановлен: <причина словами>». Рост swap за 10 минут в `/health/llm` не выводится: защёлка по нему при базе выше
  занятого покажет «swap +0 МиБ от базы … — остановлен: растёт swap»; число без данных — «нет данных»; порог `LLM_RUN_MEM_FAIL_MIB` выше всей памяти —
  «учения: порог выше всей памяти» и абзац «Идут учения: LLM-прокси и бюджет». Строки тормоза стоят после «сбоя» и
  «неизвестно»: под `DRILL_LLM=402`, при сбое OpenRouter или ошибке настройки защёлку видно только в пометке и в
  `/health/llm`;
- строки мест прогона — «спит».

Сторож `:d10a` блок `run_brake` не читает — строк тормоза у него нет; сторож `:d11a` у `core` без этого блока
считает, что `core` старше Д11.

**Хранение.** Причина, время защёлки и базы swap и `oom_kill` — в `llm_meta` `core.db`, ключи `brake:<id прогона>:…`;
защёлка уходит на диск до конца проверки. Перезапуск и пересоздание `core` с тем же `LLM_RUN_ID` защёлку не снимают (в
журнале — `llm brake latched run=<id> reason=<код> source=meta`), а убийство по памяти самого `core` даёт `oom` по
сохранённой базе.

**Снять защёлку** — после разбора причины:
1. Убрать причину. Порог учения — убрать из `.env`, иначе после снятия защёлка встанет снова через 3 проверки.
2. `docker exec pcbk-core python -m pcbk_core.llm.brake clear --run <id> --yes` — удаляет ключи `brake:<id>:…` своим
   соединением с `core.db`, идущих вызовов не трогает. В stdout — число удалённых ключей, в журнале — `llm brake cleared
   run=<id> keys=N — пересоздайте core`. Без `--yes` — отказ с кодом 1.
3. **Затем** `docker compose up -d --no-build --force-recreate core`: работающий `core` держит защёлку в памяти и без
   пересоздания продолжает отказывать. Проверка — `run_brake.state` — `watching`, в строке «LLM-прокси и бюджет» нет
   «прогон остановлен». Места прогона спят; будит их обычный вход студента или кабинет.

После снятия базы ставятся заново: `oom_kill` — первая проверка; swap — `LLM_RUN_SWAP_BASE_MIB`, а при пустом — первая
проверка, и рост swap до снятия тормоз больше не видит. Другой путь — новый `LLM_RUN_ID`: у него своих ключей нет. Но
новый id — и новая точка отсчёта денег прогона: `LLM_RUN_USD` — остаток («Прогон отбора модели», «Пока идёт прогон»).

**Учение тормоза** (`run-brake` каталога учений плана Д11; на сервере — только в окне владельца): `LLM_RUN_*` на одно
место прогона и `LLM_RUN_MEM_FAIL_MIB=1000000`, пересоздать `core`. Ожидается: через 3 проверки строка «LLM-прокси и
бюджет» — «прогон остановлен тормозом памяти», место прогона спит, в пометке «учения: порог выше всей памяти», строка
«Память сервера» не меняется. Снять — `LLM_RUN_*` и `LLM_RUN_MEM_FAIL_MIB` в `.env` пусто, `brake clear --run <id>`,
пересоздать `core`. Ключи учения, оставленные в `llm_meta`, вернули бы защёлку любому прогону с тем же id — id учения
берётся с датой окна.

## Нагрузка (с Д11)

Прогонщик `python -m pcbk_bench` («Прогон отбора модели») в режиме нагрузки (Д11-R119…R122, R131): боты-студенты на
многих местах одной моделью, свой сторож памяти с жёсткой остановкой, «только своё», цена реплики и ворота лестницы.
Второй, независимый тормоз — у `core` («Тормоз прогона по памяти»). **На сервере не выполнялось.** На стенде код
прогонщика проверен модульно; десять мест прошли один раз — до правок, а учения тормоза прогонщика до тормоза не дошли.
После правок прогон с десятью местами не повторялся (`docs/checks/D11b.md`, «Нагрузка на 10 мест (Б2)»). Окно —
Д11в: учения тормозов (В3) и лестница (В4). Вызов, `stand.json`, коды `run` и `status`, «Пока идёт прогон» — как в
«Прогоне отбора модели»; ниже — только отличия.

**Машина разработчика — оговорка.** На машине разработчика 7,8 ГБ памяти, и рядом работает локальный Dify. Тест на 10
мест (`tests/integration/test_00_load.py`, и в полном прогоне `tests/integration`) рядом с другой работой там не
помещается. Первый прогон прошёл, повторный остановлен из-за нехватки памяти (свободно ≈ 0,7 ГиБ). Прогон 0 (05.10, в
одиночку) не начал ни одного теста: фикстура поднимала десять мест разом, и сторож памяти машины в тесте остановил их
при 1453 МиБ свободных (порог 1536). Теперь места спят до прогонщика, и он будит их по одному. Срабатывание сторожа
роняет свой тест с числами, а следующий тест начинается, когда свободно снова ≥ 3500 МиБ. Прогон 1 (05.10, в одиночку):
учения тормоза, цена реплики и уборка прошли, а десять мест не поместились — ≈ 236 МиБ на место в простое, сторож
сработал на десятом месте. Теперь до старта прогонщика тест считает прогноз: свободно − места × 236 МиБ. Если прогноз
ниже порога сторожа, тест падает сразу с числами и места не будит. Десяти местам нужно свободных не меньше 3896 МиБ в
простое и ≈ 5436 МиБ с ходами (≈ 390 МиБ на место). Такие прогоны — только в одиночку: без параллельных юнит-наборов,
сборок веба и других тяжёлых задач. Перезапуск — по «да» владельца.

**Конфиг ступени** — JSON отбора и ещё ключи:
- `"load": true` — режим нагрузки. Без него `load_data` и поля базы окна — ошибка конфига. `places` —
  `{"N": "<ссылка модели>"}`, в нагрузке одна модель может стоять на многих местах. `run_id` — как `LLM_RUN_ID`
  (иначе `RunRefused: run_id`), `cap_usd` — как `LLM_RUN_USD`.
- `"load_data"` — режим данных (R131):
  - `none` — «без данных». Только сценарии без агента `bench`: `A1`–`A4`, `B3`, `C2`, иначе ошибка конфига. В `.env`
    `DRILL_DATA=tool_error`, `core` пересоздан, и в строке «Историан БДРВ» стоит пометка «учения: tool_error». Нет
    пометки на старте — `RunRefused: data_drill`. Пропала посреди ступени (`core` пересоздан без `DRILL_DATA`) — жёсткая
    остановка `data_drill`;
  - `live` — «с данными», только по «да» владельца. Нужен файл `OUT/real-data-consent.txt`, нет — `RunRefused:
    consent`. Пометки учения быть не должно, есть — `RunRefused: data_drill`;
  - `twin` — только тестовый стенд: `run` отказывает `RunRefused: load_data` до подключения к серверу.
- **Поля базы окна** (R119):
  - `swap_base_mib` и `dify_swap_base_mib` — МиБ, от 0;
  - `swap_grow_mib` (512), `dify_swap_max_mib` (256), `mem_fail_mib` (1024) — МиБ, от 1;
  - `psi_full_max` (10) — больше 0 и не больше 100.

  Базы — swap сервера и сумма swap контейнеров Dify, записанные в окне 1 до нагрузки (шаг В1); пороги — из `.env`
  сервера. Во всех ступенях лестницы база одна. `run` — путь сервера: без любого из шести полей он отказывает
  `RunRefused: window_base` до подключения. На тестовом стенде поля по желанию: `null` — умолчание, база — первое
  прочитанное значение. Образец стенда — `bench/config.load.twin.json`.

**`core` на время лестницы** — `.env`, затем пересоздать `core`. До конца лестницы `core` не пересоздаётся:
- `LLM_RUN_ID` — `run_id` всех ступеней; `LLM_RUN_USD` — `cap_usd`; `LLM_RUN_CALLERS` — все места нагрузки. Места
  ступени должны входить в вызывающих, иначе `RunRefused: run_callers`;
- `LLM_RUN_SWAP_BASE_MIB` — база окна. Пороги тормоза `core` — те же, что в конфиге: `LLM_RUN_MEM_FAIL_MIB`,
  `LLM_RUN_SWAP_GROW_MIB`, `LLM_RUN_PSI_FULL_MAX` или унаследованные от сторожа. Прогонщик сверяет `run.cap_usd` и
  `run_brake` (`fail_mib`, `swap_base_mib`, `swap_grow_mib`, `psi_full_max`) с конфигом. Расхождение или нет числа —
  `RunRefused: run_limits (<поле>)`; поле конфига `null` не сверяется;
- в «без данных» — `DRILL_DATA=tool_error`.

**Пробные учётки и места.** Логины — только `d11-probe-load-NN` (`LOAD_LOGIN` прогонщика). Это пробные учётки
`dN-probe-*`, и место потом освобождает `release-place`. Пароль лежит в `secrets/d11-probe-load-NN.pw` рядом со
`stand.json` (`openssl rand -hex 24`, `0600`, только на машине разработчика). Заводятся как у отбора: `accounts add
--login d11-probe-load-NN --role student --place N`, пароль — файлом на stdin. Места нагрузки (R128) — только свободные:
в `accounts list` нет строки `место NN`, а `agents.admin list --place N` — без строк агентов. Места людей не брать.
Свободных меньше десяти — ступень «10» становится ступенью «все свободные», и решает владелец. Перед ступенью места
нагрузки спят.

**Подготовка ступени.** Отказ — строка `RunRefused: <код>` в stderr и код 2; до шага 5 места не будятся. Проверки по
порядку:
1. до подключения к серверу — `window_start`, `load_data`, `window_base`; затем замок `OUT` — `locked`;
2. первый опрос сторожа памяти (ниже). `run_id` и `run_callers` — блок `run` в `/health/llm`. `brake` — у `core` нет
   `run_brake` в состоянии `watching` или его проверка старше 30 с. Затем `run_limits`;
3. `memory` — строка «Память сервера» на старте не «норма» или в пометке есть «нет данных: …». Датчик жёсткого условия
   без данных — отказ: решение 03.10 о слепоте;
4. `data_drill` или `consent` — режим данных. Затем пишется `OUT/health_start.json`, и стартует поток сторожа;
5. места по одному: вход, свежий опрос сторожа, пробуждение (не вошло или не проснулось — `not_ready`). После
   остановки тормозом следующие места не будятся. Затем живые агенты места, кроме `bench-uzel`, — `place_agents`: место
   не для нагрузки, архива нет. Потом `OUT/pre_sessions.json` — id разговоров, бывших на местах до прогона. Дальше как
   у отбора: `place_busy`, `place_model`, `bench_agent`.

Два известных пробела (ревью Б1, m-1 и m-2, ждут итоговой доводки Д11б):
- Страница сторожа на старте устарела (`stale`), или swap Dify не прочитан первым опросом. Подготовка не отказывает,
  места будятся, и прогонщик до ≈ 30 с слеп; данные не появились — остановка `blind`. Перед ступенью глазами:
  страница свежая, строка памяти — норма;
- места усыпляет только жёсткая остановка тормозом. Любой другой конец прогона («закончен», мягкая остановка, сигнал)
  и отказ подготовки после пробуждений оставляют места бодрствовать. Перед следующей ступенью — `docker stop
  pcbk-student-NN …` мест нагрузки.

**Сторож памяти прогонщика** работает от подготовки до конца прогона. Раз в 5 с он читает `/status.json` сторожа и
`/health/llm`, раз в 15 с — swap контейнеров Dify. Swap Dify — одна SSH-команда, только чтение: сумма
`memory.swap.current` cgroup всех контейнеров, чьё имя не начинается с `pcbk-`. Путь cgroup — v2 с драйвером
`systemd`, его сверка с хостом — шаг В1. Каждая точка — строкой в `OUT/memory.jsonl`, ровно 12 полей: `t`,
`checked_at`, `state`, `kind`, `free_mib`, `swap_used_mib`, `swap_grow10_mib`, `psi_full`, `oom_outside`,
`dify_swap_mib`, `brake`, `brake_age_s`. Решение — по числам пометки строки, а не по её тексту. В полосе «свободно
меньше 2048 МиБ» давление, убийства и swap всё равно останавливают жёстко.

**Причины остановки** — «причина …» в `status OUT`, `reason` в `OUT/load.json`:
- `memory`, жёстко:
  - строка памяти «сбой» или «неизвестно», кроме «датчик памяти замолчал»;
  - «внимание» по swap, по убийствам вне мест или по давлению;
  - свободно меньше `mem_fail_mib`; убийств по памяти вне мест и `core` за час больше 0;
  - swap за 10 мин или swap от базы окна — не меньше `swap_grow_mib`;
  - давление `full` выше `psi_full_max` в 3 разных снимках сторожа подряд (один снимок, прочитанный трижды, — не три);
  - swap Dify от базы — не меньше `dify_swap_max_mib`;
  - тормоз `core` защёлкнут (`run_brake.state` — `halted`) или ход получил 402 `pcbk_run_halted`;
- `blind`, жёстко:
  - нового свежего снимка сторожа нет дольше 30 с;
  - «датчик памяти замолчал» или пометка «нет данных: swap», «нет данных: vmstat», «нет данных: давление». Это
    слепота датчика жёсткого условия, а не «0»;
  - swap Dify не прочитан 3 раза подряд;
  - сторож прогонщика умер, или на шаге барьера его опрос старше 30 с;
- `brake`, жёстко — в `/health/llm` нет `run_brake`, или тормоз `core` не проверял память дольше 30 с;
- `data_drill`, жёстко — только `none`: пропала пометка «учения: tool_error»;
- `memory_warn`, мягко — «внимание» по порогу и ни одного жёсткого числа. Новые прогоны не начинаются, идущие доходят.
  Последний опрос в конце плана в полосе порога тоже даёт `memory_warn`, а не `finished`;
- прочие — как у отбора: `cap`, `money`, `window`, `interrupt`, `projection`, `place` и `stand`. В нагрузке `stand`
  значит, что строка «LLM-прокси и бюджет» — `fail` или пропал блок `run`; строку памяти судит только сторож.

`run_limits`, `window_base`, `load_data`, `consent`, `place_agents` — не причины остановки, а отказы старта
(`RunRefused`).

**Жёсткая остановка.** Причина встаёт поверх мягкой. Сначала все места конфига усыпляются одной командой
`docker stop -t 10` по SSH, мимо шлюза `core`; затем идущие разговоры получают abort через шлюз. После неё пробуждений
нет: пробуждение, прошедшее гонкой, сразу усыпляется снова. В конце прогона места усыпляются ещё раз. Срок по плану
(R133): места остановлены (`State.FinishedAt`) не позже 60 с от снимка сторожа с жёстким условием. **Ни на стенде, ни
на сервере этот срок не подтверждён.**

**Сон мест — вопрос владельцу.** Другого пути сна у прогонщика нет — только `docker stop`. Решение контроллера по
умолчанию: основной путь сна — шлюз `core` под защёлкой своего тормоза, а `docker stop` прогонщика — запасной. Он же
единственный для условий, которые `core` не видит: swap Dify, слепота прогонщика, пропавшая пометка учения.
Подтвердить до окна 1.

**Цена ошибки** (R119). При обрыве туннеля прогонщик слеп и через 30 с останавливается, но усыпить места уже не может —
это делает `core`. Swap Dify держит только прогонщик. Если прогонщик мёртв, это условие прогон не остановит: ход места
дойдёт, и место уснёт по простою (`GW_IDLE_STOP_S`). Остальные условия держит `core`.

**`status OUT`** — как у отбора. В нагрузке код 1 и тогда, когда метка сторожа памяти `guard_at` в `progress.json`
старше `--stale-s`: сторож прогонщика молчит. В окне — раз в минуту `status OUT` и страница.

**Что остаётся в `OUT`:**
- `memory.jsonl`, `health_start.json`, `health_end.json`, `pre_sessions.json`;
- `load.json`: `places`, `started_at`, `ended_at`, `reason` и числа сторожа — `free_base_mib`, `free_min_mib`,
  `swap_from_base_mib`, `dify_swap_from_base_mib`, `psi_full_peak`;
- `isolation.json` (R121) — по месту только числа `listed`, `foreign_sessions`, `foreign_agents`. Чужой разговор — не
  из своих строк `sessions.log` и не из `pre_sessions.json`. Чужой агент — не встроенный, не `bench-uzel` и не
  сохранённый этим местом. После жёсткой остановки — `{"skipped": "не проверено: места усыплены тормозом"}`;
- `price.json` — после `load-check`.

После сценария A прогонщик в нагрузке архивирует только агентов, которых сохранил сам. Чужой агент остаётся уликой для
`isolation.json`. Агент, сохранённый посреди хода, оборванного тормозом, в запись прогона не попадает и держит место:
следующая подготовка откажет `place_agents`. До следующей ступени его уносят в архив: место работает,
`agents.admin archive --place N --name <имя> --yes`.

**Лестница и `load-gate`** (R122). Ступени 2 → 5 → 10 — отдельные запуски `run` с одним `LLM_RUN_ID`, своим `OUT` и
своим конфигом. База окна одна, перед ступенью места нагрузки спят. После ступени —
`python -m pcbk_bench load-gate OUT --next K [--warn-mib 2048]`: одна строка, код 0 «можно», 1 «нельзя». «Нельзя» —
лестница стоп, строка владельцу.
- Счёт. База `B` — первая точка `memory.jsonl` со свободной памятью, `M` — минимум, `k` — мест (`load.json`). Прирост
  на место `P = (B − M) / k`, прогноз `B − K × P`. «Можно» — прогноз не ниже `warn-mib` + 512 МиБ. Строка: «база B МиБ,
  минимум M МиБ, мест k, прирост на место P МиБ; прогноз на K мест: X МиБ — можно» (или «— нельзя, наибольшее N»).
- «Нельзя: на старте ступени нет данных датчиков: …» — в первой строке `memory.jsonl` нет данных датчика жёсткого
  условия: свободная память, swap, давление, vmstat или swap Dify. Без них лестницу и ступень не начинать.
- «Нельзя: ступень не закончена (<причина>)» — `reason` в `load.json` не `finished`: остановка тормозом, мягкая,
  окно и т. п. Прогноз по оборванной ступени занижает прирост, а давление после сна мест спадает.
- `--next` — от 1 до 10. Оборванная строка `memory.jsonl` или нет `load.json` — сбой с кодом 1, в безопасную сторону.
- Перед первой ступенью то же правило считается по пику места из замера В2 — строкой журнала окна. Команды для этого
  нет.

**`load-check` и цена реплики** (R120) — `python -m pcbk_bench load-check <конфиг ступени>` после ступени, по `OUT` и
серверу (`stand.json` конфига). На тестовом стенде те же числа считает харнесс. Окно — `started_at` … `updated_at`
из `progress.json`. Числа читает изнутри `pcbk-core` один скрипт: sqlite `mode=ro`, белый список
`/app/data/whitelist.txt`, наружу — только числа:
- события службы мест ступени: всего, с SQL, имён вне списка в SQL (должно быть 0), в запросе вне списка (справочно:
  отказ без SQL), исходы;
- наибольшее число событий с SQL за любые скользящие 5 минут — у всех вызывающих, кроме канала `system`; предел ворот —
  300;
- вызовы модели, расход, отказы прокси по кодам;
- минуты одновременной работы — минуты, в которые у каждого места ступени был вызов модели;
- цена реплики, она же `OUT/price.json`: реплики студентов (ходы `runs.jsonl` и вопрос моста), вызовы модели и расход
  по журналу прокси (без отказов прокси), `usd_per_reply`, `calls_per_reply`;
- деньги по `health_start.json` и `health_end.json`: расход прогона не выше потолка плюс перерасход Д8-R69 (3 × мест ×
  наибольшая цена строки), исчерпанных долей не прибавилось, общий расход ниже бюджета;
- «только своё» по `isolation.json`: чужих 0. Пропуск после тормоза — нарушение «„только своё“ не проверено».

Вывод — строки чисел без тегов, имён и текстов, затем нарушения по строке. Последняя строка — «load-check: норма»
(код 0) или «load-check: нарушений N» (код 1). В «без данных» строки SQL печатаются, но не судятся: «успех 4: не
проверяется (без данных)».

**Уборка нагрузки** (R128) — после лестницы или оборванной ступени:
1. Прогонщиков нет: для каждого `OUT` — `status OUT` не `running` и `flock -n OUT/.lock true` → `0` (как «Аварийная
   уборка», шаг 2).
2. Каждая `d11-probe-load-NN` — по «Освобождению места пробной учётки»:
   - разговоры ботов — `DELETE` по `sessions.log` всех ступеней, одним вызовом на место («Уборка после окна» отбора,
     шаг 7);
   - `accounts disable`, затем `agents.admin archive --place N --yes` при работающем месте;
   - `accounts remove`, затем `release-place`. Места спят.
3. `.env` — пусто у `LLM_RUN_ID`, `LLM_RUN_USD`, `LLM_RUN_CALLERS`, `LLM_RUN_SWAP_BASE_MIB`, у прочих порогов
   `LLM_RUN_*`, если ставились, и у `DRILL_DATA`. Затем пересоздать `core`. Проверка: `/health/llm` → `run: null`,
   `run_brake: null`, строки страницы — норма.
4. Ключи `brake:<id>:…` лестницы остаются в `llm_meta`. Этот id больше не брать, иначе `brake clear --run <id> --yes`
   («Тормоз прогона по памяти»). Итог — строкой в журнал окна.

**Тестовый стенд** (R123):
- `tests/integration/test_00_load.py` идёт первым. Все десять мест — учётками `d11-probe-load-NN`, конфиг
  `bench/config.load.twin.json` (`twin`), двойник OpenRouter `:d11b` с задержкой хода (`/_delay`). Фикстура места
  только создаёт; будит их прогонщик по одному, как на сервере.
- Учения тормоза прогонщика — на двойнике памяти сторожа: сбой, swap от базы, давление. Тормоз `core` при этом не
  защёлкивается.
- Между учениями `BenchHarness.settle()` уносит в архив агентов оборванных ходов. Уборка — `BenchHarness.cleanup`:
  задержка и персоны двойника сняты, агенты в архиве, места без переопределения и остановлены, учётки удалены, места
  освобождены, `core` и сторож — по `test.env`. Последний тест это сверяет.
- `test_isolation_live.py` учёток не берёт.

## Изоляция целиком (с Д11)

Один модуль `tests/integration/test_isolation_live.py` (Д11-R125) проверяет все десять мест — на тестовом стенде (без
переменной, договор даёт `Stack.iso_conf()`) и на сервере (`PCBK_LIVE_ISO`, окно 2 Д11г). Только чтение; учёток не
берёт; места людей не будит.

- **Сети.** У каждой `pcbk-stu-NN` — `Internal`, изолированный шлюз, мост есть и без IPv4. Из сети каждого места
  одноразовой пробой `curl` (способ Д1): `core` `.2:8000` открыт (положительный контроль); закрыты мост и шлюз своей
  сети и `core` `.2` на 22, 80, 443, 2375, 3389, сосед-свидетель `.3:4096`, сервер в ЛВС (22, 443), историан 1433,
  интернет 443, `sp-ctl`, `sp-ro`, сторож и `edge` во всех их сетях, `core` в чужих сетях. Свидетели — два свободных
  места: только они бодрствуют (спали — уснут снова), их `.3:4096` открыт из своей сети в начале и в конце обхода.
- **Отрицательный контроль — свой слушатель** (I-3 р2), а не `sshd` машины: одноразовая сеть `pcbk-iso-ctl`
  (внутренняя, без изолированного шлюза, `ctl_subnet`) и в ней `pcbk-iso-listen` (`listener_image`,
  `python -m http.server 9000`, `--read-only`, `--cap-drop ALL`, uid 65534). Из `pcbk-iso-ctl` слушатель открыт, из сети
  каждого места — закрыт; на сервере ещё шлюз `pcbk-iso-ctl`:`ctl_host_port` открыт (путь к хосту без изолированного
  шлюза реален, как Д2). Сбой `docker run` пробы (125–127) — ошибка теста, а не «закрыто».
- **Секреты.** В `/run/secrets` каждого места — ровно три своих файла (`student-NN.pw`, `.llm-token`, `.data-token`
  из `secrets_dir`); пароль БДРВ и ключ OpenRouter — 0 совпадений в `docker inspect` места (как есть и раскрытым из
  JSON: кавычки и `<>&` не прячут значение) и в окружении OpenCode свидетелей (`/proc/<pid>/environ`, pid — по `exe`).
  Значение читается так, как его читает `core`: пароль — разбором `read_env_file` (`export`, одна пара кавычек по
  краям, последняя строка), ключ — как `read_key_file`; сверка — `test_value_parse_like_core`. Значение уходит только
  во временный файл 0600 той же команды (`mktemp`, удаляется ею же); положительный контроль на голом значении — `1`.
- **Вывод** — коды, счёт и имена целей («ЛВС», «историан», без адресов); значений нет. Последний тест печатает сводку
  (`-rP`) и с `PCBK_SHOTS` снимает страницу отчёта: стенд — `d11b-04-isolation.png`, сервер — `isolation-server.png`
  (числа мест, целей и контролей; адресов нет).

**Договор `PCBK_LIVE_ISO`** — JSON вне git, `$JOB/pcbk-d11/live/iso.json` (рядом с прочими файлами окна):

```json
{"ssh": ["ssh", "-o", "BatchMode=yes", "<сервер>"], "runtime": "runsc", "stu_net": "<STU_NET из .env>",
 "secrets_dir": "<SECRETS_DIR из .env, буквально>", "places": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
 "witnesses": [<свободное место>, <свободное место>], "lan_addr": "<адрес сервера в ЛВС>",
 "historian_addr": "<адрес историана>", "remote_dir": "/opt/pcbk-reserve", "listener_image": "pcbk-reserve/core:d11a",
 "ctl_subnet": "<свободная /28 по журналу Д1>", "ctl_host_port": 22}
```

`ssh` — argv до команды: команда уходит одной строкой оболочке сервера. Нужны на сервере: `docker` без `sudo` у
пользователя `ssh`, `python3`, `ip`, образ `curlimages/curl:8.16.0` (он остаётся, см. «Выкладка и обновление») и
`listener_image`; чтение `secrets_dir/bdrv.env` и `secrets_dir/openrouter.key`. Свидетели — только места без учёток
студентов. Нет ключа договора — тест падает сразу с именем ключа. `remote_dir` — каталог выкладки (в проверках не
участвует).

**Команда** (с машины разработчика, из корня репозитория, без замка стенда — стенд не поднимается):
`PCBK_LIVE_ISO=$JOB/pcbk-d11/live/iso.json PCBK_SHOTS=<каталог снимков> uv run --python 3.12 --with pytest pytest -q
-rP tests/integration/test_isolation_live.py`. На стенде модуль идёт в общем прогоне `tests/integration` под замком.

**Что создаётся и удаляется:** одноразовые пробы `curl` (`--rm`, метка `pcbk-test.oneshot`, `--runtime` из договора,
`--read-only`, `--cap-drop ALL`); сеть `pcbk-iso-ctl` и слушатель `pcbk-iso-listen` с той же меткой — удаляются в
`finally` (остаток оборванного прогона с меткой — и перед началом; не удалились — тест красный); временные файлы
значений — той же командой. Свидетель, который спал, после проверки снова остановлен (`docker stop`). Ни учёток, ни
томов, ни `DRILL_*` модуль не трогает.

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
- Д8а — назад к Д7б (места не трогаются: образ места тот же, `student:d7b`; тома, секреты, каталог агентов и
  `core.db` сохраняются):
  1. **только вне режима отбора** — «Аварийная уборка режима отбора», шаг 1: ничего не нашлось (нашлось — сначала
     уборка). `compose.yaml.d7b` не передаёт `core` переменных `LLM_RUN_*`, а `core:d7b` их и не знает: откат `core`
     посреди прогона снял бы потолок прогона — места отбора тратили бы до своих долей;
  2. вернуть `compose.yaml.d7b` (копия перед выкладкой Д8а, шаг 2 «Выкладки»; в нём `core:d7b` и сторож `:d7a`):
     `cp -p compose.yaml.d7b compose.yaml`;
  3. `docker compose up -d --no-build core watchdog` — порядок не важен: сторож `:d7a` блок `run` в `/health/llm` не
     проверяет, а сторож `:d8a` ответ `core:d7b` без блока `run` принимает как «прогона нет». Проверка —
     `docker inspect -f '{{.Config.Image}}' pcbk-core pcbk-watchdog` и `stale: false` на `/status.json`.

  Точки отсчёта прогонов Д8а — строки `run_base_usd:<id>:student-NN` в `llm_meta` `core.db` — `core:d7b` не читает: они
  ему не мешают и не удаляются (возврат на `core:d8a` с тем же `LLM_RUN_ID` продолжит прогон с прежней точки).
  `LLM_RUN_*` и `LLM_RUN_SILENT_S` в `.env` читают только образы `:d8a`.
- Д9а — назад к Д8 (тома мест, секреты, каталог агентов и `core.db` сохраняются: новых таблиц Д9а не заводит, строки
  `agent_events` с каналом `web` остаются). Образы `core:d8a`, `watchdog:d8a` и мест `student:d7b` (или `:d8b`, если
  выложен Б8 Д8) остаются на сервере. Порядок — обратный выкладке Д9а:
  1. `DRILL_DATA` в `.env` — пусто: иначе при возврате на Д9а учение включится само;
  2. статика чата — копия, снятая при выкладке Д9а (`chat.<день, выложенный до Д9а>`), на место — раздел «Статика
     чата», откат. Первой: вкладка Д9а у `core:d8a` получала бы 404;
  3. вернуть `compose.yaml.<прошлый день>` (копия перед выкладкой Д9а; в нём `core:d8a`, сторож `:d8a` и места
     прежнего образа): `cp -p compose.yaml.<прошлый день> compose.yaml`;
  4. места на прежнем образе — «Пересоздание мест с сохранением томов». Раньше `core`: помощник и Конструктор мест
     `:d9a` знают ряд и наработку, а `core:d8a` их не отдаёт (`tools/list` — четыре инструмента), и его `save_agent`
     отказал бы черновику с `tag_series` или `calc_runtime` («Инструмента … на стенде нет»);
  5. `docker compose up -d --no-build core watchdog` — **вместе**: сторож `:d8a` при `core:d9a` серию ошибок не видит
     (поля `tools.failing` и `drill` не читает), а сторож `:d9a` у `core:d8a` этих полей не ждёт. Проверка —
     `docker inspect -f '{{.Config.Image}}' pcbk-core pcbk-watchdog` и `stale: false` на `/status.json`.

  Шаблон `edge` Д9а (`location /pcbk/api/calc/`) откатывать не обязательно: прежняя статика этот путь не зовёт, а
  `core:d8a` отвечает на него 404 JSON. Агенты, сохранённые при Д9а, остаются в каталогах мест; ряда и наработки у
  `core:d8a` нет — такой агент их не получит.
- Д10 — назад к Д9б или Д9а (окно Б4 выложило Д10а и Д10б вместе): `core:d9b`, если он был на сервере, иначе
  `core:d9a`, и `watchdog:d9a` — их ID записаны в `docs/checks/D10b.md` до окна. Места не трогаются (образ места
  тот же, `student:d9a`); тома, секреты, каталог агентов и `core.db` сохраняются. **Строка владельцу перед откатом:**
  таблицы `llm_shares`, `teacher_actions` и `teacher_heartbeat` остаются в `core.db`, но старый `core` их не читает —
  доли мест снова по $100 у всех (`LLM_SHARE_USD`), кабинета нет, калькуляторы преподавателю — снова 403. Порядок —
  обратный выкладке:
  1. `DRILL_TEACHER` в `.env` — пусто: иначе при возврате на Д10 учение включится само;
  2. статика чата — копия, снятая в окне Б4 (`chat.<прошлый день>`), на место — раздел «Статика чата», откат.
     Первой: кабинет Д10 у старого `core` получал бы 404;
  3. страница входа — прежний `login.js` (копия `login.js.<прошлый день>` из окна Б4 или
     `git show <ветка или тег отката>:edge/static/login.js`) в `/opt/pcbk-reserve/edge/static/login.js`: страница Д10
     ведёт преподавателя в кабинет, которого у прежней статики нет;
  4. вернуть `compose.yaml.<прошлый день>` (копия перед окном Б4; в нём `core:d9b` или `core:d9a` и сторож `:d9a`):
     `cp -p compose.yaml.<прошлый день> compose.yaml`;
  5. `docker compose up -d --no-build core watchdog` — **вместе**: сторож `:d10a` у старого `core` получает 404 на
     `/health/teacher`, и строка «Кабинет преподавателя и доли» — «неизвестно» «кабинет преподавателя ещё не
     выложен»; сторож `:d9a` этой строки не строит. Проверка — `docker inspect -f '{{.Config.Image}}' pcbk-core
     pcbk-watchdog` и `stale: false` на `/status.json`.

  Шаблон `edge` Д10 (`location /pcbk/api/teacher/`) откатывать не обязательно: прежняя статика этот путь не зовёт, а
  старый `core` отвечает на него 404. Учётки, заведённые кабинетом, — обычные строки учёток шлюза: старый `core` их
  понимает. Команд `accounts enable` и `accounts journal` у старого `core` нет: отключённую учётку студента
  возвращают `accounts remove --login L --yes` и `add … --same-person` (как в «Учётках окна» Д9б), журнал действий
  читается после возврата на Д10. После возврата на `core:d10b` доли снова берутся из `llm_shares`.
- Д11а — назад к Д10: `core:d10b` и `watchdog:d10a` (места не трогаются: образ места тот же; тома, секреты, каталог
  агентов и `core.db` сохраняются):
  1. **только вне прогона** — «Аварийная уборка режима отбора», шаг 1: `LLM_RUN_*` пусты (нашлось — сначала уборка).
     У `core:d10b` тормоза прогона нет: откат посреди прогона снял бы второй тормоз нагрузки;
  2. вернуть `compose.yaml.<прошлый день>` (копия перед выкладкой Д11а; в нём `core:d10b` и сторож `:d10a`):
     `cp -p compose.yaml.<прошлый день> compose.yaml`;
  3. `docker compose up -d --no-build core watchdog` — порядок не важен: сторож `:d10a` лишний ключ `run_brake` в
     `/health/llm` не читает, а сторож `:d11a` ответ без него принимает как «`core` старше Д11». Проверка —
     `docker inspect -f '{{.Config.Image}}' pcbk-core pcbk-watchdog` и `stale: false` на `/status.json`.

  После отката строка «Память сервера» — снова только по свободной памяти (как с Д1): swap, давления, убийств вне мест и
  причин перезапуска нет. Ключи `brake:<id>:…` в `llm_meta` `core:d10b` не читает: они ему не мешают и не удаляются —
  возврат на `core:d11a` с тем же `LLM_RUN_ID` вернёт и защёлку (снять — «Тормоз прогона по памяти», «Снять
  защёлку»). Строки Д11 в `.env` (`SWAP_GROW_WARN_MIB`, `PSI_FULL_MAX`, `LLM_RUN_*` порогов тормоза) прежний
  `compose.yaml` контейнерам не передаёт.
- Тома мест, `secrets/` и `agents/` удаляются только до первого входа
  студента и только по именам: после — там разговоры и агенты студентов.
- `rm -rf /opt/pcbk-reserve` (с `sudo`) — только до первого входа. После —
  удалять лишь `tls/`, `compose.yaml` и `edge/`; **`secrets/` и `agents/`
  остаются**.
- gVisor: остановить контейнеры с `runsc`; вернуть
  `/etc/docker/daemon.json.pre-runsc` на место; `systemctl reload docker`;
  `rm -rf /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin`.
