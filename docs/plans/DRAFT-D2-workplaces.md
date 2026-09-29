# Д2. Рабочие места под gVisor — черновик плана

> **Черновик.** Вынесен из первой редакции плана Д1, когда критика показала,
> что день не влезает. До начала Д2 уточняется по итогам утренней пробы
> gVisor (Д1, задача 0) и проходит критику, как план Д1. Формат — по
> writing-plans; оформление шагов «тест → падает → код → проходит → коммит» —
> как в [`PLAN-D1-foundation-2026-09-29.md`](PLAN-D1-foundation-2026-09-29.md),
> Global Constraints оттуда же действуют и здесь.

**Goal:** десять рабочих мест OpenCode v1.18.33 созданы на сервере под
gVisor, каждое в своей изолированной сети; страница состояния показывает их
«спит / работает / перезапущен после сбоя / не отвечает»; живые проверки
подтверждают, что из рабочего места нет пути к соседу, к серверу, к 1433 и в
интернет, а пароль его сервера не виден ни сторожу, ни в git.

**Видимый результат:** страница состояния с десятью рабочими местами;
учение «память рабочего места переполнена» — строка становится жёлтой
«перезапущен после сбоя», журнал это пишет; журнал проверок изоляции с
пометками [П].

## Задачи (эскиз)

### 1. Образ рабочего места

* `student/Dockerfile`: стадия загрузки на
  `ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02`
  — `opencode-linux-x64.tar.gz` v1.18.33 (sha256 `e5461232…16d5`; без AVX2 —
  `-baseline`, `440ca654…e6d5`) и ripgrep 15.1.0 musl (`1c9297be…1599`);
  итоговая стадия — тот же базовый образ, два бинарника, конфигурация,
  `install -d -o 10001 -g 10001 /work /var/lib/opencode`; пользователь 10001.
* Раскладка: `XDG_CONFIG_HOME=/etc/pcbk-opencode` (только чтение: `opencode.json`,
  `.gitignore`, `tools/*.ts`, пустой `agents/` — точка монтирования);
  `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME` — том
  `/var/lib/opencode`; `TMPDIR=/tmp` — tmpfs **с `exec`**; `/work` — том.
* **`.gitignore` в конфигурации — пустой** (или без строки `.gitignore`):
  иначе git проигнорирует сам файл, образ из чистого клона даст
  `GET /agent` → 500. Проверка в закрытии дня: `git ls-files student/config/.gitignore`.
* Переменные образа: `OPENCODE_DISABLE_MODELS_FETCH=1`,
  `OPENCODE_DISABLE_AUTOUPDATE=1`, `OPENCODE_DISABLE_SHARE=1`,
  `OPENCODE_DISABLE_LSP_DOWNLOAD=1`, `OPENCODE_DISABLE_PROJECT_CONFIG=1`,
  `OPENCODE_DISABLE_DEFAULT_PLUGINS=1`,
  `OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=true`,
  `BUN_RUNTIME_TRANSPILER_CACHE_PATH=0`, `OPENCODE_SERVER_USERNAME=opencode`.
* **Пароль сервера — файлом**: `${SECRETS_DIR}/student-NN.pw` → `:ro` в
  `/run/secrets/opencode-pw`; точка входа
  `export OPENCODE_SERVER_PASSWORD="$(cat /run/secrets/opencode-pw)"; exec opencode serve --hostname 0.0.0.0 --port 4096`.
  В `Config.Env` пароля нет — `sp-ro` его не покажет.
* `HEALTHCHECK` каждые 30 с: `GET /global/health` на `127.0.0.1:4096` с
  Basic из файла пароля (bash `/dev/tcp`, `base64`), таймаут 5 с,
  `start_period` 20 с. Сторож: `State.Health.Status == "unhealthy"` →
  `fail` «OpenCode не отвечает» (строка в таблицу `check_container`, тест).
* `opencode.json`: `autoupdate: false`, `share: "disabled"`,
  `snapshot: false`, `enabled_providers: ["pcbk"]`, провайдер `pcbk`
  (`@ai-sdk/openai-compatible`, `baseURL` и `apiKey` через `{env:…}`, модель
  `stub`); ключей `mcp`, `lsp`, `formatter`, `plugin` нет — MCP и модели в Д4.
* Заглушки `bash`, `edit`, `write`, `apply_patch` — `export default` без
  импортов, `args: {}`, описание начинается «Отключено на учебном стенде».

### 2. Десять мест в компоновке

* Сеть `pcbk-stu-NN`: `internal: true`, `gateway_mode_ipv4: isolated`,
  подсеть `${STU_NET}.N.0/28`, динамика только из `ip_range: ${STU_NET}.N.8/29`,
  рабочее место `.3`, серверный слой (Д4) — `.2`, `extra_hosts: ["core:${STU_NET}.N.2"]`
  (под gVisor DNS Docker не работает). Подсеть проверяется на пересечение
  утром (как в Д1, задача 0).
* Место: `runtime: runsc`, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`,
  `mem_limit: 1g`, `cpus: 1.0`, `pids_limit` — по замеру пробы Д1 (не меньше
  нескольких сотен), `restart: unless-stopped`, `stop_grace_period: 10s`,
  тома `pcbk-student-NN-state`, `pcbk-student-NN-work`, агенты
  `${AGENTS_DIR}/student-NN` → `:ro` на `/etc/pcbk-opencode/opencode/agents`,
  `labels: {pcbk.role: student}`, `pull_policy: never`.
* `components.json`: рабочие места — вид `container`, `sleeping_ok: true`.
* Выкладка: `docker compose create` — места создаются, но не стартуют.

### 3. Тесты (локально под runc; `compose.test.yaml` — `runtime: runc`)

* `test_sp_ctl_really_starts_and_stops_student`: через `sp-ctl` клиентом
  `pcbk-core` — start → 204, start → 304, stop → 204.
* `test_student_hardening`: `ReadonlyRootfs`, `CapDrop == ["ALL"]`,
  `no-new-privileges`, память 1 ГиБ; `/proc/1/status` — `CapEff` нули,
  `NoNewPrivs: 1`.
* `test_opencode_requires_password`: `/global/health` без пароля — 401, с
  паролем — 200 (готовность — повторы с таймаутом 2 с до 30 с).
* `test_stubs_replace_builtin_tools`: `/experimental/tool?provider=pcbk&model=stub`
  — `bash`, `edit`, `write` с нашим описанием; `model=gpt-5` — `apply_patch`.
* `test_agent_file_visible_after_dispose`: сначала `GET /agent` (экземпляр
  создан), потом запись `probe.md`, без него → `POST /instance/dispose` → с
  ним; `finally` — файл удалить; тестовый `AGENTS_DIR` — свежий на сессию.
* `test_readonly_where_it_matters`: `touch` в `HOME`, конфигурации, агентах,
  `/usr/local/bin` — отказ; в `/var/lib/opencode` и `/work` — можно.
* `test_no_route_anywhere`: из места 01 — отказ или таймаут на `.1` и `.8`
  своей сети (22, 80, 443, 2375, 3389), адрес хоста в ЛВС, `HISTORIAN_ADDR:1433`
  (из тестового `.env`, адрес заказчика в коде не пишется), `1.1.1.1:443`,
  место 02 `.3:4096`; у моста сети нет адреса хоста.
* `test_password_not_visible_to_watchdog`: `GET RO/containers/pcbk-student-01/json`
  изнутри сторожа — в теле нет пароля и нет `OPENCODE_SERVER_PASSWORD`.
* `test_env_holds_only_own_secret`: в `/proc/1/environ` — только свой пароль,
  печатаются **только имена** переменных.
* `test_health_unhealthy_is_fail` (модульный, сторож).

### 4. Живые проверки на сервере (журнал `docs/checks/D2.md`, [П])

* Место 01 под `runsc`: `/global/health` 200 (curl с `--env-file`, пароль в
  вывод не попадает), `/proc/version` — ядро gVisor, заглушки на месте.
* `dispose` видит файл агента, записанный с хоста под gVisor (§11 п. 2 без
  живого потока).
* Изоляция — тот же набор, что `test_no_route_anywhere`, под `runsc`.
* Холодный старт ×3, память и `pids.current` через 60 с простоя для 01 и 02
  (§11 п. 5, простой; «в работе» — Д4, нужна модель). Порог поправки лимитов:
  память > 700 МиБ или потоки > 70 % `pids_limit`.
* Учение: `docker exec pcbk-student-01 tail /dev/zero` → песочница убита по
  памяти, `unless-stopped` поднимает её; снимок: «перезапущен после сбоя»;
  строка в журнале сторожа.
* Учение: `docker exec pcbk-student-01 kill -STOP 1` (или заморозка процесса
  OpenCode иначе) → через ≤ 2 мин `HEALTHCHECK` даёт `unhealthy`, снимок:
  «OpenCode не отвечает»; вернуть `kill -CONT 1`.

### 5. Закрытие дня — как в Д1 (критик, `main`, тег `platform-d2`, чистый клон)
