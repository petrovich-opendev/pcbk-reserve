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
   возить); свой образ — `pcbk-reserve/watchdog:<день>`. Сверка: слои
   `docker image inspect -f '{{json .RootFS.Layers}}'` совпадают.
2. `compose.yaml` и каталог `edge/` — в `/opt/pcbk-reserve` (`scp`; без
   удаления: `tls/`, `.env`, `secrets/`, `agents/` не трогаются). Права:
   `chmod -R o+rX edge` — nginx читает файлы от другого пользователя.
3. `.env` — по `deploy/env.example`, `chmod 600`.
4. `cd /opt/pcbk-reserve && docker compose up -d --no-build edge watchdog sp-ro sp-ctl`
   — **с именами служб**: рабочие места (с Д2) создаются отдельно и не
   стартуют сами.
5. Проверка: `curl -sk https://127.0.0.1:8443/status.json` — `stale: false`.

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

- Стенд: `cd /opt/pcbk-reserve && docker compose down` (том журнала
  сторожа остаётся; `down -v` — удалить и его). Dify не затрагивается.
- gVisor: остановить контейнеры с `runsc`; вернуть
  `/etc/docker/daemon.json.pre-runsc` на место; `systemctl reload docker`;
  `rm -rf /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin`.
- Каталог: `rm -rf /opt/pcbk-reserve` (с `sudo`).
