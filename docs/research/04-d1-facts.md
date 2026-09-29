# Факты для первого слайса: прокси сокета, gVisor, OpenCode, OpenRouter

29.09.2026 · четыре агента на Opus 5.5 · только чтение источников и опыты на
локальном Docker 29.5.2 (runc); сервер заказчика не трогали · основание для
[`plans/PLAN-D1-foundation-2026-09-29.md`](../plans/PLAN-D1-foundation-2026-09-29.md)
и §13 проекта.

**Пометки:** **[Д]** — по документации или исходникам на закреплённом теге;
**[Л]** — опыт на локальном Docker (не на сервере, без gVisor);
**[?]** — не подтверждено, проверяется на сервере в Д1.

## 1. Прокси сокета Docker

* **Tecnativa/docker-socket-proxy v0.5.0 не подходит** [Д, Л]. Правило
  `deny unless METH_GET || POST` стоит раньше `ALLOW_START/ALLOW_STOP`
  ([haproxy.cfg L48–53](https://github.com/Tecnativa/docker-socket-proxy/blob/v0.5.0/haproxy.cfg#L48-L53)):
  при `POST=0` start/stop получают 403, а при `POST=1` с `CONTAINERS=1`
  открываются create (201), exec (201), delete, update и prune. Чтение при
  `CONTAINERS=1` слишком широкое: inspect любого контейнера вместе с `Env`
  (в том числе Dify), логи, `archive` (файлы из контейнеров).
* **linuxserver/socket-proxy 3.4.6-r0-ls100 — запасной** [Д, Л]: правила
  ALLOW стоят раньше `deny`, start/stop при `POST=0` работают, logs и archive
  закрыты. Но нет ограничения клиентов, и start/stop/inspect действуют на
  любой контейнер, включая Dify.
* **wollomatic/socket-proxy 1.13.1 — выбран** [Д, Л]. Белые списки по
  методам регулярками Go (`-allowGET`, `-allowPOST`, …), прокси сам
  дописывает `^…$` — альтернативы брать в скобки; сверяется только путь, без
  строки запроса. Клиенты — `-allowfrom` (CIDR или имена, имена разрешаются по
  DNS на каждый запрос). Слушать не на 127.0.0.1 — только `-listenip=0.0.0.0`.
  Метод без списка → 405, путь вне списка → 403, чужой клиент → 403.
  Опыт с нужной политикой: inspect и start/stop только контейнеров студентов
  по имени — 200/204; inspect контейнера Dify, logs, archive, events, info,
  images, restart, kill, update, create, exec, volumes, prune — 403; DELETE —
  405; обход `start/../../create` и `%2F` (curl с `--path-as-is`) — 403.
  **Список `GET /containers/json`, если его разрешить, отдаёт все контейнеры
  сервера** вместе с `Command` (у Redis в Dify там пароль): строку запроса с
  фильтром прокси не проверяет — в опыте фильтр ставил сам клиент. Поэтому
  список не разрешается никому. Inspect отдаёт `Config.Env` — секреты
  контейнерам только файлами.
  Образ `FROM scratch`, пользователь 65534, ~5 МиБ памяти; работает с
  `--user 65534:<gid docker> --read-only --cap-drop ALL no-new-privileges`
  без `--privileged`.
* **Две копии со статичными флагами проще одной с метками** [Л]: `sp-ctl`
  для серверного слоя (GET inspect студентов + POST start/stop студентов),
  `sp-ro` для сторожа (только GET inspect). Права видны глазами в
  `compose.yaml`, не зависят от меток и потока событий Docker, падение одной
  копии не ослепляет другую. Списка контейнеров нет ни у одной копии (см. выше).
* `:ro` на `docker.sock` от записи не защищает — защищает только фильтр
  прокси [Л].
* Docker 29.7.2 отдаёт API до 1.55, минимум 1.40; префикс `/v1.44` принимают
  все 29.x [Д: матрица версий API Docker]. Клиенту на Python — `http.client`
  (не ходит по перенаправлениям), имя контейнера перед подстановкой в путь
  проверять той же регуляркой, что в прокси. `RestartCount` — поле верхнего
  уровня inspect, не внутри `State`. Коды start/stop: 204 — выполнено, 304 —
  уже в этом состоянии, 404 — нет контейнера [Л].

## 2. gVisor

* Последний выпуск **release-20260921.0** (опубликован 23.09.2026). С
  20260831.0 выпуск — один тарбол `gvisor.tar.zstd` (или `.tar.bz2`), внутри
  `runsc`, `containerd-shim-runsc-v1` и каталог `gvisor-bin/`, который должен
  лежать рядом с `runsc`; отдельных бинарников в выпуске нет [Д, Л: сумма
  sha512 сверена].
* `runsc install` добавляет `runtimes.runsc` в `/etc/docker/daemon.json`,
  остальные ключи сохраняет, но пересериализует файл и оставляет копию
  `daemon.json~` [Д, Л на копии файла].
* **`systemctl reload docker` (SIGHUP) подхватывает новый runtime без
  остановки контейнеров** — `runtimes` в списке перечитываемых опций
  ([dockerd: configuration reload](https://docs.docker.com/reference/cli/dockerd/#configuration-reload-behavior),
  [moby v29.7.2 reload_unix.go](https://github.com/moby/moby/blob/docker-v29.7.2/daemon/reload_unix.go#L14-L22));
  так делает и CI самого gVisor. **`restart` при выключенном live-restore
  гасит все контейнеры, включая Dify** [Д]. Страницы gVisor quick start
  советуют restart — они отстали от install.md.
* Платформа systrap — по умолчанию, KVM не нужен, работает внутри ВМ; нужен
  ptrace: `kernel.yama.ptrace_scope` ≤ 2 (в Ubuntu по умолчанию 1); ядро 5.15
  подходит (нужно 5.6+) [Д].
* `--read-only`, `--cap-drop ALL`, `no-new-privileges`, tmpfs, `--memory`,
  `--cpus`, `--pids-limit`, `docker stats`, политика перезапуска — работают
  с Docker и cgroup v2 [Д: e2e-тесты gVisor]. Тонкости: `--memory` покрывает
  песочницу целиком (sentry, gofer, приложение) — нужен запас; `pids.max`
  считает потоки песочницы на хосте, при упоре падает вся песочница — лимит не
  ставить крошечным, мерить [Д: #2490, #2535].
* **Внутренняя сеть (`internal: true`) не отрезает контейнер от хоста**: на
  мосту остаётся адрес хоста, и из контейнера открыты его службы на
  `0.0.0.0` (SSH, опубликованные порты). С
  `-o com.docker.network.bridge.gateway_mode_ipv4=isolated` адреса на мосту
  нет, службы хоста закрыты, соседи по сети видны [Л: опыт критика плана;
  Д: moby v29.7.2 `bridge_linux.go:699`]. При заданном `ip_range` без
  `gateway` шлюз Docker ставит на первый адрес диапазона, а не на `.1` [Л].
* **Встроенный DNS Docker (127.0.0.11) в пользовательских сетях под runsc не
  работает** ([gvisor#7469](https://github.com/google/gvisor/issues/7469),
  открыт): рабочее место находит соседей только по адресу — статические
  адреса и `extra_hosts`. Сети подключаются до старта контейнера [Д].
  Клиенты под runc (серверный слой, сторож, прокси) разрешают имена как
  обычно.
* Изменения с хоста в bind mount видны при следующем чтении
  (`--file-access-mounts=shared` по умолчанию), но событий inotify нет
  ([gvisor#8089](https://github.com/google/gvisor/issues/8089)) [Д].
* Bun 1.3.14 внутри OpenCode v1.18.33 не использует io_uring (в gVisor он
  выключен), цикл событий — epoll [Д]. **TUI OpenCode под gVisor не
  рисуется** ([opencode#29802](https://github.com/anomalyco/opencode/issues/29802))
  — нам не нужен: только `opencode serve` [Д].
* Политика `on-failure` после рестарта dockerd поднимает и контейнеры,
  остановленные нами с кодом 137/143; `unless-stopped` — нет [Д]. Для рабочих
  мест — `unless-stopped`.
* **[?] `State.OOMKilled` под runsc** — по исходникам ожидается `true` и код
  137, подтверждения нет; проверка — в Д1 на сервере.

## 3. OpenCode v1.18.33 в контейнере

Тег v1.18.33 = коммит `51ef4be1d3c1…`, npm `latest` на 29.09.2026. Опыты —
под runc с `--read-only --cap-drop ALL no-new-privileges --network none`.

* **Установка** — бинарник `opencode-linux-x64.tar.gz` из выпуска (Bun-сборка,
  glibc ≥ 2.17, node не нужен); без AVX2 — вариант `-baseline` [Д, Л].
* **Каталоги.** Уже при запуске OpenCode создаёт каталоги data, config,
  state, tmp, log, bin — `--version` падает с EROFS, если XDG-каталоги только
  для чтения [Л]. Рабочая раскладка [Л]: `HOME` и `XDG_CONFIG_HOME` — только
  чтение; `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME`, `TMPDIR` — на
  запись **с правом исполнения** (на tmpfs с `noexec` Bun не загружает
  нативный модуль: поиск возвращает пусто, наблюдатель файлов не стартует);
  рабочий каталог — на запись. **В каталоге конфигурации только для чтения
  обязателен `.gitignore`**, иначе `GET /agent` → 500 [Л].
* **Сеть не нужна** при [Д, Л]: `OPENCODE_DISABLE_MODELS_FETCH=1` (иначе
  каталог моделей раз в час); ripgrep 15.1.0 в `PATH` (иначе качает с GitHub,
  без него поиск → 500); провайдер `@ai-sdk/openai-compatible` встроен в
  бинарник; `@opencode-ai/plugin` не ставится в каталог только для чтения и
  не нужен, если инструменты без импортов (без сети его установка задерживала
  старт экземпляра на 33–71 с); `OPENCODE_DISABLE_DEFAULT_PLUGINS=1`;
  `OPENCODE_DISABLE_SHARE=1` и `"share": "disabled"`; автообновление serve не
  запускает, `autoupdate: false` — для порядка. Итог опыта без сети с
  поддельной моделью: сессия проходит целиком.
* **Свои инструменты** — `{tool,tools}/*.{js,ts}` во всех каталогах
  конфигурации; `export default` получает имя файла, одноимённый заменяет
  встроенный `bash`/`edit`/`write`/`apply_patch` [Д, Л: модель получила один
  `bash` с нашим описанием]. `apply_patch` остаётся только у моделей с `gpt-`
  в имени, `edit`/`write` у них убираются [Д]. Аргументы можно задать JSON
  Schema без пакета `@opencode-ai/plugin` [Л].
* **Подложить свой инструмент**: `~/.opencode` сканируется всегда; если
  `HOME` на запись, агент может создать `~/.opencode/tools/bash.ts` и
  перебить заглушку. Поэтому `HOME`, `XDG_CONFIG_HOME` — только чтение, и
  `OPENCODE_DISABLE_PROJECT_CONFIG=1` (игнорировать `.opencode` и
  `opencode.json` рабочего каталога) [Д].
* **Агенты** — `{agent,agents}/**/*.md` в каталогах конфигурации, имя = путь
  без расширения. **`POST /instance/dispose` есть; новый файл агента виден в
  `GET /agent` сразу после него** [Д, Л]. Глобальный `opencode.json`
  кэшируется навсегда — его правка требует перезапуска контейнера [Л].
* **Сервер.** `--hostname` по умолчанию 127.0.0.1 — нужен `0.0.0.0`. Basic
  включается только при непустом `OPENCODE_SERVER_PASSWORD`, имя —
  `OPENCODE_SERVER_USERNAME`, по умолчанию `opencode`. Пароль принимается и
  в строке запроса `?auth_token=base64(user:pass)` — шлюз вырезает. Без пароля
  отвечают только три файла манифеста; `/pty/:id/connect?ticket=` проверяет
  тикет сам [Д, Л].
* **Каталог экземпляра**: каталог сессии → `?directory=` →
  `x-opencode-directory` → `process.cwd()`; каждый новый каталог — новый
  экземпляр, который сам не освобождается [Д, Л] — шлюз вырезает оба
  параметра (как в §7 проекта).
* **Готовность.** Порт открывается через ~2 с; запросы в первые ~3 с после
  открытия висят бессрочно, с ~5 с — ответ за 20–30 мс. Проверять
  `GET /global/health` с таймаутом 1–2 с и повторами [Л].
* **Память под runc** [Л]: в простое 315–340 МиБ (пик 370), после одной
  сессии ~500 МиБ (пик 564). Под gVisor больше, не измерено. Открытые issue о
  росте памяти serve: #51340, #52041, #50578.
* Для проверки заглушек: `GET /experimental/tool?provider=&model=` отдаёт
  описания инструментов — заглушки проверяются без живой модели [Д]; шлюз
  студенту эту ручку не пропускает.

## 4. OpenRouter и учёт цены (для Д4)

* **Цена приходит всегда**: последний чанк потока перед `[DONE]` несёт
  `usage` с `cost` в долларах; `usage: {include: true}` и
  `stream_options.include_usage` устарели и ни на что не влияют
  ([usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)).
  В этом чанке `choices` не пуст, `finish_reason` повторяется [Д].
* OpenCode v1.18.33 для `@ai-sdk/openai-compatible` сам шлёт
  `stream_options.include_usage` (`provider.ts:1806`); LLM-прокси ничего
  добавлять не нужно [Д].
* Если поток оборван или сломан посредине — чанка с ценой нет: брать
  `X-Generation-Id` из заголовков и дозапрашивать
  `GET /api/v1/generation?id=` (`total_cost`); сразу после ответа бывает 404 —
  повторять с ограниченной паузой [Д; задержка — ?].
* `GET /api/v1/key` обычным ключом — расход и лимит ключа; остаток аккаунта —
  только `GET /api/v1/credits` **ключом управления** (обычный → 403): такой
  ключ хранится только у серверного слоя или сторожа [Д].
* «Кончились деньги»: 402 (смотреть `metadata.limit_source`; `in_flight_budget`
  с `Retry-After` можно повторить) **или 403** с текстом
  `key limit exceeded|budget limit exceeded|insufficient credits` — по отчётам
  сентября 2026 лимит ключа отдаёт 403, хотя документация обещает 402.
  Ошибка посреди потока — SSE-событие с `error` при статусе 200 [Д].
* Тело запроса пропускать по белому списку: `model` строго из списка, без
  суффиксов `:online`, `:nitro` и подобных; удалять `models`, `plugins`,
  `route`, `preset`, `service_tier`; выбрасывать инструменты `openrouter:*`
  (платные); `provider` — свой; `user` — псевдоним студента; `session_id` —
  студент и сессия; `max_tokens` ограничивать (OpenCode шлёт до 32 000) [Д].

## 5. Проверить на сервере

В Д1 (проба и выкладка): `ptrace_scope`, AVX2, хранилище образов, подсети —
предпроверки; OpenCode serve под `runsc` в изолированной сети; нет пути к
мосту хоста, ЛВС, 1433 и в интернет (с положительным и отрицательным
контролем); `OOMKilled` и код 137 под `runsc`; память и потоки пробы в
простое; правила двух прокси сокета — те же коды, что локально.

В Д2 (рабочие места): раскладка образа и заглушки под `runsc`; `dispose`
видит файл агента, записанный с хоста в bind mount под gVisor; холодный
старт; память и потоки мест; изоляция мест друг от друга.

## 6. Закреплённые версии и суммы

| Что | Версия | Сумма |
|---|---|---|
| wollomatic/socket-proxy | 1.13.1 (git `bc6606ad…`) | `sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002` (индекс образа) |
| linuxserver/socket-proxy — запасной | 3.4.6-r0-ls100 | `sha256:0357c479cc98e863917d1cd8b10e83d35a50ca46a0788f5a192bf792c3b7100d` |
| gVisor `gvisor.tar.zstd` | release-20260921.0 (коммит `26f3455a…`) | sha512 `66a5b17354690ec2e4037b54080eeabde55a1503779ceb823d73856283a6836953e105493d7e38fb2dcf85e5c86e0f48c66a9abb0244a78bd612df938702475c` |
| gVisor `gvisor.tar.bz2` — если нет zstd | release-20260921.0 | sha512 `7c899979bed334f0987888c41e545cede8e1f7e67a257978c8de244c3867e32bc30d08491e9af36720273c502acd01d8d86c9844ebcbe125790c43fe6bdf563e` |
| OpenCode `opencode-linux-x64.tar.gz` | v1.18.33 (`51ef4be1…`) | sha256 `e546123213ae47909a4268692aa4b94950d011afe9cac9938753a2194f1c16d5` |
| OpenCode `opencode-linux-x64-baseline.tar.gz` | v1.18.33 | sha256 `440ca65423e99505cf285f8660684503cc54f6f9e6b64cfcd11bf412369be6d5` |
| ripgrep `x86_64-unknown-linux-musl` | 15.1.0 | sha256 `1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599` |
| ubuntu | 22.04 | `sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02` |
| python (образ сторожа) | 3.12-slim (3.12.14) | `sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` |
| nginx | 1.30.5-alpine (= stable-alpine на 29.09) | `sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94` |
| официальный образ OpenCode — только для пробы Д1 | 1.18.33 | `sha256:ee31dff80f8347b4705317de808b01fce1cfbf35cff560ddc573cda9718be0f7` |
| curlimages/curl | 8.16.0 | `sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6` |
| Docker Engine на сервере | 29.7.2 | API 1.40–1.55 |

Не использовать: tecnativa/docker-socket-proxy v0.5.0 (§1).
