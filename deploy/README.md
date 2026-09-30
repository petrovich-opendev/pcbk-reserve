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
   проверок); свои образы — `pcbk-reserve/watchdog:<день>` и
   `pcbk-reserve/student:<день>` (собирать из чистого дерева, `git archive`). Сверка: слои
   `docker image inspect -f '{{json .RootFS.Layers}}'` совпадают.
2. `compose.yaml` и каталог `edge/` — в `/opt/pcbk-reserve` (`scp`; без
   удаления: `tls/`, `.env`, `secrets/`, `agents/` не трогаются). Права:
   `chmod -R o+rX edge` — nginx читает файлы от другого пользователя.
3. `.env` — по `deploy/env.example`, `chmod 600`.
4. `cd /opt/pcbk-reserve && docker compose up -d --no-build edge watchdog sp-ro sp-ctl`
   — **с именами служб**: рабочие места (с Д2) создаются отдельно и не
   стартуют сами.
5. Проверка: `curl -sk https://127.0.0.1:8443/status.json` — `stale: false`.

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

### Новый студент
- **Смена студента в слоте 01–10:** новые файлы `student-NN.pw` и
  `student-NN.llm-token` (старые удалить); прежний каталог агентов места
  переименовать и сохранить (удалять — только по решению владельца), создать
  пустой;
  старые тома места — только по решению владельца; `docker compose
  --profile students create --force-recreate --no-build student-NN`.
- **Место 11 и дальше:** правка регулярок `sp-ro`/`sp-ctl`, подсеть N, блок
  в `compose.yaml`, строка в `watchdog/components.json`, тесты границ
  диапазона, пересборка сторожа.

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

## Что открывается наружу

- Порт **8443** контейнера `edge` — Docker публикует его **в обход ufw**.
  Закрыть: `docker compose stop edge`.
- `edge` — единственный контейнер с путём к хосту; остальные сети внутренние
  с изолированным шлюзом.

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
- Тома мест, `secrets/` и `agents/` удаляются только до первого входа
  студента и только по именам: после — там разговоры и агенты студентов.
- `rm -rf /opt/pcbk-reserve` (с `sudo`) — только до первого входа. После —
  удалять лишь `tls/`, `compose.yaml` и `edge/`; **`secrets/` и `agents/`
  остаются**.
- gVisor: остановить контейнеры с `runsc`; вернуть
  `/etc/docker/daemon.json.pre-runsc` на место; `systemctl reload docker`;
  `rm -rf /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin`.
