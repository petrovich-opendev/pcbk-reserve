"""Стенд отбора модели на тестовом стенде (Д8а, А6): прогонщик pcbk_bench поверх Stack и двойников.

- StackStand(stack) — Stand прогонщика (bench/pcbk_bench/stand.py) поверх Stack: SQL журналов core — изнутри pcbk-core
  (только чтение, те же LLM_ROWS_PY и AGENT_ROWS_PY, что у ServerStand), save_agent — stack.mcp_save, файл агента —
  stack.agent_file, архив — agents.admin, калькулятор — POST /api/data/<инструмент> с хоста теста токеном ops,
  run_callers — LLM_RUN_CALLERS из окружения pcbk-core, place_model — GET /config места из его сети, place_mcp_ok —
  mcp=connected в журнале здоровья места после старта core. Вызовы калькулятора и ответы rate считаются (m10).
- bench_env(run_id, cap, places) — LLM_RUN_* core на время прогона (Д8-R69).
- BenchHarness(stack, tmp, places) — режим отбора на двойниках (start) и уборка (cleanup): core с двойником историана
  и прогоном, сторож с коротким порогом тишины, персоны двойника OpenRouter, места с переопределением модели
  (render_override), учётки d8-probe-bench-NN, прогонщики (runner_for), снимок страницы состояния со строкой
  компонента (shot; Д9а — строка «Историан БДРВ» под учением). recreate_core(…, extra=…) — ещё переменные core поверх
  режима отбора (Д9а: учение DRILL_DATA).
- BenchHarness.match_runner(place, cases, out) (Д9б, Б2) — прогонщик «агент = калькулятор» (pcbk_bench.match) места
  со своим клиентом шлюза учётки места и StackStand; prepare() — вход, пробуждение, агент проверки bench-calc;
  cleanup() закрывает и его (агент — в архив), если тест не закрыл сам.
- agents_left(stack, places) — имена живых (не archived) агентов мест по порядку мест.
- Нагрузка (Д11б, Б2; Д11-R119, R120): BenchHarness(stack, tmp, places, config=…, login=…) — конфиг прогонщика
  (bench/config.load.twin.json) и вид учёток (d11-probe-load-NN — пробные, место освобождается); у конфига нагрузки
  recreate_core выставляет пороги тормоза core LLM_RUN_* ровно по полям базы окна конфига (brake_env; null — умолчание
  компоновки) и ждёт их в /health/llm — иначе прогонщик откажет RunRefused("run_limits"). delay(s) — задержка каждого
  вызова чата двойника OpenRouter (POST /_delay; 0 — DELETE), уборка её снимает. settle() — между учениями тормоза:
  агенты, оставшиеся от оборванных ходов (кроме bench-uzel), — в архив, очередь перезагрузки мест пуста (места, где
  такие были, — списком, для журнала). StackStand режима нагрузки:
  sleep_places — docker stop мест одной командой (как ServerStand), dify_swap_mib — файл двойника dify-swap (нет,
  пусто или не число — StandError: слепота, не ноль), load_check — LOAD_CHECK_PY изнутри pcbk-core, с out — и цена
  реплики OUT/price.json (load.write_price).
- BenchHarness.judge_and_table(out) (Б3) — таблица отбора по OUT после вердиктов судьи: table.build (check_verdicts,
  aggregate с load_tag_units(out) и планом конфига прогона OUT); Markdown и table.json — в OUT, копия Markdown — в
  PCBK_TABLES (twin-table.md, каталог $JOB), снимок страницы проверки с этим Markdown — PCBK_SHOTS/twin-table.png
  (markdown_page → cdp.Browser, file://).

Уборка (cleanup, решение контроллера П2 — порядок test_agents.py): прогонщики закрыты → персоны выключены → учётки
disable (до этого — пробуждение работающих мест с агентами: архив перезагружает только готовое место) → агенты мест — в
архив, пока места работают → очередь перезагрузки мест пуста → места без переопределения и остановлены
(create --force-recreate, тома не трогаются) → учётки remove → release-place → core и сторож — значения test.env (у
core — и пороги тормоза LLM_RUN_* как без переменных). Задержка двойника OpenRouter снимается вместе с персонами.
Каждый шаг смотрит на состояние стенда и идемпотентен; сбой шага не обрывает остальные — первый сбой поднимается в
конце.

Секреты — Secret (пароли учёток, токены мест): в трассировке pytest «<скрыт>». Числа и теги двойника историана — из
bench-historian.json; twin_historian сюда не импортируется (pytds, pcbk_core).
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import shutil
import threading
from collections.abc import Iterable, Mapping
from dataclasses import asdict, replace
from pathlib import Path

import cdp
from conftest import (COMPOSE_FILES, CORE_URL, FAKE_OPENROUTER_URL, HOST, PORT, REPO, STAND_HOST, STAND_ORIGIN,
                      TEST_OPS_DATA_TOKEN, Secret, Stack, _run, bench_data, bench_whitelist_tags, row_of, wait_for)
from fake_bench import PERSONAS                                   # core/tests на пути импорта (conftest)
from pcbk_bench.cards import CardSet, load_cards
from pcbk_bench.config import BenchConfig, load_config
from pcbk_bench.gateway import GatewayChat
from pcbk_bench.load import LOAD_CHECK_PY, write_price
from pcbk_bench.match import Case, MatchRunner
from pcbk_bench.override import render_override
from pcbk_bench.record import RunRecord
from pcbk_bench.runner import Runner, load_records
from pcbk_bench.scenarios import BENCH_AGENT
from pcbk_bench.stand import AGENT_ROWS_PY, DATA_TOOLS, LLM_ROWS_PY, SLEEP_STOP_S, StandError
from pcbk_bench.table import ModelRow, build, render_markdown

CONFIG = REPO / "bench" / "config.twin.json"
OPENCODE_JSON = REPO / "student" / "config" / "opencode.json"
# фикстура двойника историана внутри core: core/tests привязан в /twin только на чтение (compose.test.yaml)
TWIN_FIXTURE = "/twin/data/bench-historian.json"
PROBE_LOGIN = "d8-probe-bench-{n:02d}"
# порог «прогон отбора молчит» у сторожа на время модуля, с: тишина видна за десятки секунд, а не за 300
SILENT_S = 10
# умолчание сторожа (compose.yaml: ${LLM_RUN_SILENT_S:-300}) — так уборка узнаёт, что сторож уже вернулся
SILENT_DEFAULT = "300"
MCP_WAIT_S = 90                     # места переподключают MCP после пересоздания core не сразу (test_conversation)
PLACE_READY_S = 120
DRAIN_S = 60                        # очередь перезагрузки мест после архива
# воркер перезагрузки проходит места по одному (простой 3 с, dispose до 5 с, проверка видимости), а место, поднятое
# docker start, шлюз видит готовым не сразу: десяти местам нагрузки (Д11б) 60 с мало — срок растёт с числом мест
DRAIN_PER_PLACE_S = 15
WAKE_S = 120
# строки очереди перезагрузки мест (argv) — изнутри pcbk-core, только чтение: истина об очереди (QUEUED_PY test_agents)
QUEUED_PY = ("import sqlite3, sys; "
             "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
             "ps = [int(a) for a in sys.argv[1:]]; "
             "print(c.execute('SELECT COUNT(*) FROM agent_reloads WHERE place IN (%s)' % ','.join('?' * len(ps)), "
             "ps).fetchone()[0])")
# отказы прокси с этим code у вызывающих: argv — code и вызывающие
REFUSALS_PY = ("import sqlite3, sys; "
               "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
               "cs = sys.argv[2:]; "
               "print(c.execute('SELECT COUNT(*) FROM llm_calls WHERE code = ? AND caller IN (%s)' "
               "% ','.join('?' * len(cs)), [sys.argv[1], *cs]).fetchone()[0])")
# белый список двойника историана внутри core (PYTHONPATH=/twin): сверка с bench_data (решение контроллера к А6)
TWIN_WHITELIST_PY = "from twin_historian import bench_whitelist; print('\\n'.join(sorted(bench_whitelist())))"
# строка страницы состояния с этим заголовком (title строки status.json, %s — JSON) — в середину экрана (для снимка)
ROW_INTO_VIEW_JS = ("[...document.querySelectorAll('body *')].find((e) => e.children.length === 0 "
                    "&& e.textContent.trim() === %s).scrollIntoView({block: 'center'}), 1")
# пороги тормоза core (Д11-R117) по полям базы окна конфига нагрузки: поле конфига → переменная core → ключ run_brake
# /health/llm (так их сверяет прогонщик, доводка р1 Б1, m-3)
BRAKE_ENV = (("mem_fail_mib", "LLM_RUN_MEM_FAIL_MIB", "fail_mib"),
             ("swap_base_mib", "LLM_RUN_SWAP_BASE_MIB", "swap_base_mib"),
             ("swap_grow_mib", "LLM_RUN_SWAP_GROW_MIB", "swap_grow_mib"),
             ("psi_full_max", "LLM_RUN_PSI_FULL_MAX", "psi_full_max"))
BRAKE_READY_S = 30                  # первая проверка тормоза core после старта — раз в 5 с
FAKE_DELAY_PATH = "/_delay"         # задержка вызовов чата двойника OpenRouter (Д11б)
PLACE_NAME_RE = re.compile(r"student-\d{2}")


def bench_env(run_id: str, cap: float, places: Iterable[int]) -> dict[str, str]:
    """LLM_RUN_* core на время прогона (Д8-R69): id, потолок, места прогона через запятую."""
    return {"LLM_RUN_ID": run_id, "LLM_RUN_USD": str(float(cap)),
            "LLM_RUN_CALLERS": ",".join(f"student-{n:02d}" for n in sorted(places))}


def brake_env(cfg: BenchConfig) -> dict[str, str]:
    """Пороги тормоза core LLM_RUN_* — ровно поля базы окна конфига нагрузки (Д11-R119); поле null — переменной нет
    (умолчание компоновки), вне нагрузки — пусто."""
    if not cfg.load:
        return {}
    return {var: str(getattr(cfg, field)) for field, var, _ in BRAKE_ENV if getattr(cfg, field) is not None}


def _env_of(stack: Stack, name: str) -> dict[str, str]:
    return dict(e.split("=", 1) for e in stack.inspect(name)["Config"]["Env"] or [] if "=" in e)


def _agent_rows(stack: Stack, place: int) -> list[dict]:
    rows = json.loads(stack.exec("pcbk-core", "python", "-c", AGENT_ROWS_PY, str(place)))
    return [r for r in rows if isinstance(r, dict)]


def agents_left(stack: Stack, places: Iterable[int]) -> list[str]:
    """Имена живых (не archived) агентов мест — по порядку мест, в месте — по порядку строк учёта."""
    return [r["name"] for n in sorted(places) for r in _agent_rows(stack, n)]


class StackStand:
    """Stand прогонщика поверх Stack тестового стенда (см. шапку модуля). Методы зовутся из потоков мест."""

    def __init__(self, stack: Stack):
        self.stack = stack
        self._lock = threading.Lock()
        self.calc_calls = 0                 # вызовы калькулятора токеном ops (каталог и сверка, m10)
        self.calc_rate = 0                  # из них ответы rate (частота ops — 60 за 300 с)

    def llm_rows(self, sessions: list[str]) -> list[dict]:
        r = _run(["docker", "exec", "-i", "pcbk-core", "python", "-c", LLM_ROWS_PY],
                 input=json.dumps(list(sessions)))
        rows = json.loads(r.stdout)
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    def agent_rows(self, place: int) -> list[dict]:
        return _agent_rows(self.stack, place)

    def agent_file(self, place: int, name: str) -> str:
        return self.stack.agent_file(place, name)

    def save_agent(self, place: int, args: dict) -> dict:
        return self.stack.mcp_save(place, args)

    def archive_agent(self, place: int, name: str) -> None:
        self.stack.agents_admin("archive", "--place", str(place), "--name", name, "--yes")

    def calc(self, tool: str, args: dict) -> dict:
        if tool not in DATA_TOOLS:
            raise ValueError("калькулятор: инструмент не из службы данных")
        code, body = self.stack.http_host("POST", f"{CORE_URL}/api/data/{tool}", dict(args), token=TEST_OPS_DATA_TOKEN)
        answer = json.loads(body) if code in (200, 503) else None
        with self._lock:
            self.calc_calls += 1
            if isinstance(answer, dict) and answer.get("status") == "rate":
                self.calc_rate += 1
        if not isinstance(answer, dict):           # 503 — «служба не настроена» в своём виде (как у ServerStand)
            raise RuntimeError(f"калькулятор: код {code}")
        return answer

    def status(self) -> dict:
        code, body = self.stack.https("/status.json", timeout=10)
        if code != 200:
            raise RuntimeError(f"/status.json: код {code}")
        return json.loads(body)

    def health_llm(self) -> dict:
        return self.stack.llm_health()

    def run_callers(self) -> frozenset[str]:
        raw = _env_of(self.stack, "pcbk-core").get("LLM_RUN_CALLERS", "")
        return frozenset(c.strip() for c in raw.split(",") if c.strip())

    def place_model(self, place: int) -> str:
        code, body = self.stack.oc(place, "GET", "/config", place)
        model = json.loads(body).get("model") if code == 200 else None
        if not isinstance(model, str):
            raise RuntimeError(f"GET /config места {place:02d}: код {code}, model нет")
        return model

    def place_mcp_ok(self, place: int) -> bool:
        since = self.stack.started_at("pcbk-core")
        return "mcp=connected" in self.stack.health_output(f"pcbk-student-{place:02d}", since)

    # --- режим нагрузки (Д11-R119, R120) ---

    def sleep_places(self, places: list[int]) -> None:
        """docker stop мест одной командой, срок -t SLEEP_STOP_S (как ServerStand); остановленное место — не ошибка."""
        if any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 10 for n in places):
            raise StandError("место: 1…10")
        names = [f"pcbk-student-{n:02d}" for n in places]
        if names:
            _run(["docker", "stop", "-t", str(SLEEP_STOP_S), *names])

    def dify_swap_mib(self) -> int:
        """swap Dify двойника, МиБ: файл dify-swap каталога двойников памяти (Stack.set_dify_swap). Нет файла, пусто
        или не целое — StandError: нечитаемое — слепота сторожа памяти прогонщика, а не ноль (как у ServerStand)."""
        try:
            text = (self.stack.mem_dir / "dify-swap").read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            raise StandError("swap Dify: файл двойника не читается") from None
        if not text.isdecimal():
            raise StandError("swap Dify: в файле двойника не целое МиБ")
        return int(text)

    def load_check(self, callers: list[str], since: str, until: str, out: Path | None = None) -> dict:
        """Числа нагрузки за [since, until] (LOAD_CHECK_PY изнутри pcbk-core, только чтение); с out — и цена реплики
        в OUT/price.json (load.write_price), как у ServerStand."""
        if not all(isinstance(c, str) and PLACE_NAME_RE.fullmatch(c) for c in callers):
            raise StandError("load_check: вызывающие — student-NN")
        numbers = json.loads(self.stack.exec("pcbk-core", "python", "-c", LOAD_CHECK_PY, json.dumps(list(callers)),
                                             since, until))
        if not isinstance(numbers, dict):
            raise StandError("load_check: ответ — не объект")
        if out is not None:
            numbers["price"] = write_price(Path(out))
        return numbers


class BenchHarness:
    """Режим отбора на двойниках тестового стенда и его уборка (см. шапку модуля). start() — подъём режима, cleanup() —
    уборка; поля stack, tmp, cards, runner (прогонщик фикстуры, OUT — tmp/out), stand (StackStand). config — конфиг
    прогонщика (по умолчанию bench/config.twin.json; Д11б — config.load.twin.json), login — вид учёток мест ({n:02d};
    пробный, под PROBE_LOGIN core — release-place освобождает место)."""

    def __init__(self, stack: Stack, tmp: Path, places: Mapping[int, str], *, config: Path = CONFIG,
                 login: str = PROBE_LOGIN):
        self.stack = stack
        self.tmp = Path(tmp)
        self.places = dict(sorted(places.items()))
        cfg = load_config(config, OPENCODE_JSON)
        if cfg.places != self.places:
            raise ValueError(f"места модуля — не места {Path(config).name} (m4)")
        self._login = login
        self.drain_s = max(DRAIN_S, DRAIN_PER_PLACE_S * len(self.places))     # Д8: 4 места — 60 с, как было
        self.cfg: BenchConfig = replace(cfg, out_dir=self.tmp / "out")
        self.cards: CardSet = load_cards(self.cfg.cards)
        self.stand = StackStand(stack)
        self.override = self.tmp / "compose.bench.yaml"
        self._pw = {n: Secret(secrets.token_urlsafe(18)) for n in self.places}
        self._runners: list[Runner | MatchRunner] = []        # закрывает cleanup(); close() у обоих идемпотентен
        self._cfgs: dict[Path, BenchConfig] = {}              # конфиг прогонщика по OUT — для таблицы (Б3)
        self.runner: Runner | None = None
        self.data_dir: Path | None = None

    # --- имена ---

    def login(self, n: int) -> str:
        return self._login.format(n=n)

    def _container(self, n: int) -> str:
        return f"pcbk-student-{n:02d}"

    def _services(self) -> list[str]:
        return [f"student-{n:02d}" for n in self.places]

    # --- подъём режима отбора ---

    def start(self) -> None:
        """1) core — двойник историана, белый список отбора и прогон cfg (run_id, cap; у нагрузки — и пороги тормоза);
        сторож — порог тишины SILENT_S; 2) персоны двойника OpenRouter, места с переопределением модели; 3) учётки
        мест (login) и прогонщик."""
        self.data_dir = bench_data(self.tmp)
        self.recreate_core(self.cfg.run_id, self.cfg.cap_usd)
        twin = self.stack.exec("pcbk-core", "python", "-c", TWIN_WHITELIST_PY).split()
        assert twin == bench_whitelist_tags() and twin, "белый список отбора — не белый список двойника в core"
        self.stack.recreate("watchdog", {"LLM_RUN_SILENT_S": str(SILENT_S)})
        self._bench(on=True)
        self.override.write_text(render_override(self.places), encoding="utf-8")
        self.stack.compose("up", "-d", "--no-build", "--no-deps", "--force-recreate", *self._services(),
                           files=(*COMPOSE_FILES, str(self.override)))
        for n in self.places:
            self.stack.wait_ready(self._container(n), PLACE_READY_S)
        self._wait_mcp()
        for n in self.places:
            self.stack.add_account(self.login(n), "student", n, self._pw[n])
        self.runner = self.runner_for(self.cfg.out_dir)

    def _bench(self, on: bool) -> None:
        """Персоны двойника OpenRouter (fake_bench.PERSONAS): POST /_bench — включить, DELETE — выключить; 204."""
        if on:
            code, _ = self.stack.http_host("POST", FAKE_OPENROUTER_URL + "/_bench", {"personas": dict(PERSONAS)})
        else:
            code, _ = self.stack.http_host("DELETE", FAKE_OPENROUTER_URL + "/_bench")
        if code != 204:
            raise RuntimeError(f"двойник OpenRouter: /_bench — код {code}")

    def delay(self, s: float) -> None:
        """Задержка каждого вызова чата двойника OpenRouter, с (Д11б): больше нуля — POST /_delay {"s": s}, 0 — DELETE
        /_delay (задержки нет); 204."""
        if s:
            code, _ = self.stack.http_host("POST", FAKE_OPENROUTER_URL + FAKE_DELAY_PATH, {"s": s})
        else:
            code, _ = self.stack.http_host("DELETE", FAKE_OPENROUTER_URL + FAKE_DELAY_PATH)
        if code != 204:
            raise RuntimeError(f"двойник OpenRouter: {FAKE_DELAY_PATH} — код {code}")

    def _wait_mcp(self) -> None:
        """mcp=connected у работающих мест модуля после старта core (≤ MCP_WAIT_S)."""
        running = [n for n in self.places if self.stack.inspect(self._container(n))["State"]["Running"]]
        wait_for(lambda: all(self.stand.place_mcp_ok(n) for n in running), MCP_WAIT_S)

    def recreate_core(self, run_id: str, cap: float, extra: Mapping[str, str] | None = None) -> None:
        """core с двойником историана, белым списком отбора и прогоном run_id с потолком cap; у конфига нагрузки — и
        пороги тормоза по полям базы окна (brake_env), затем ожидание их чисел в run_brake /health/llm (до первой
        проверки тормоза их нет — прогонщик отказал бы run_limits); extra — ещё переменные core поверх (Д9а:
        {"DRILL_DATA": "tool_error"}); следующий вызов без extra их снимает — компоновка берёт умолчания. Затем
        mcp=connected у работающих мест модуля."""
        if self.data_dir is None:
            self.data_dir = bench_data(self.tmp)
        self.stack.recreate("core", {"PCBK_TWIN_HISTORIAN": TWIN_FIXTURE, "DATA_DIR": str(self.data_dir),
                                     **bench_env(run_id, cap, self.places), **brake_env(self.cfg),
                                     **dict(extra or {})})
        if self.cfg.load:
            wait_for(self._brake_numbers, BRAKE_READY_S)
        self._wait_mcp()

    def _brake_numbers(self) -> bool:
        """В run_brake /health/llm есть числа порогов, которые сверяет прогонщик, и возраст проверки."""
        brake = self.stack.llm_health().get("run_brake")
        return isinstance(brake, dict) and all(isinstance(brake.get(key), (int, float))
                                               for key in ("age_s", *(k for _, _, k in BRAKE_ENV)))

    def _chat(self, n: int) -> GatewayChat:
        account = {"login": self.login(n), "password": self._pw[n]}             # как connect() CLI стенда
        return GatewayChat(f"https://{HOST}:{PORT}", STAND_HOST, STAND_ORIGIN, **account)

    def runner_for(self, out_dir: Path, **cfg_overrides) -> Runner:
        """Прогонщик с OUT out_dir и правками конфига (reps, run_id, cap_usd …); свои клиенты шлюза мест — вход
        делает prepare(). Все прогонщики закрывает cleanup()."""
        cfg = replace(self.cfg, out_dir=Path(out_dir), **cfg_overrides)
        self._cfgs[Path(out_dir)] = cfg
        runner = Runner(cfg, self.stand, {n: self._chat(n) for n in cfg.places}, self.cards)
        self._runners.append(runner)
        return runner

    def match_runner(self, place: int, cases: list[Case], out: Path) -> MatchRunner:
        """Прогонщик «агент = калькулятор» (Д9-R91) места place модуля: клиент шлюза учётки места, StackStand,
        OUT out. Вход и агент проверки — prepare(); закрывает тест, а забытый — cleanup()."""
        if place not in self.places:
            raise ValueError("место — не место модуля")
        runner = MatchRunner(self._chat(place), self.stand, place, cases, Path(out))
        self._runners.append(runner)
        return runner

    # --- чтение ---

    def run_spent(self) -> float:
        run = self.stack.llm_health()["run"]
        return float(run["spent_usd"]) if run else 0.0

    def refusals(self, code: str) -> int:
        """Отказы прокси с этим code у мест модуля — по журналу вызовов."""
        callers = [f"student-{n:02d}" for n in self.places]
        return int(self.stack.exec("pcbk-core", "python", "-c", REFUSALS_PY, code, *callers))

    def records(self, out: Path | None = None) -> list[RunRecord]:
        return load_records(Path(out) if out is not None else self.cfg.out_dir)

    def secrets(self) -> list[Secret]:
        """Секреты модуля, которых не должно быть в OUT: пароли учёток, пароли и токены мест, токен данных ops."""
        found = list(self._pw.values())
        for n in self.places:
            found += [self.stack.password(n), self.stack.llm_token(n), self.stack.data_token(n)]
        return [*found, Secret(TEST_OPS_DATA_TOKEN)]

    def queued(self, places: Iterable[int] | None = None) -> int:
        """Строки очереди перезагрузки агентов у мест модуля (или у мест places)."""
        ps = self.places if places is None else places
        return int(self.stack.exec("pcbk-core", "python", "-c", QUEUED_PY, *map(str, ps)))

    def settle(self) -> list[int]:
        """Между учениями нагрузки (Д11б): жёсткая остановка рвёт ход, а агент, сохранённый посреди оборванного хода,
        в записи прогона не остаётся (Д8 m-р4-3) и живёт на месте — следующая подготовка отказала бы place_agents
        (R121); агенты, которых прогонщик архивировал при спящем месте, держат место в очереди перезагрузки. Такие
        агенты (кроме bench-uzel) — в архив, места с ними и с очередью работают (спящее — docker start), очередь пуста
        (≤ drain_s). Возвращает места, где были такие агенты."""
        strays = {n: [r["name"] for r in _agent_rows(self.stack, n) if r.get("name") != BENCH_AGENT]
                  for n in self.places}
        strays = {n: names for n, names in strays.items() if names}
        busy = sorted(set(strays) | {n for n in self.places if self.queued([n])})
        for n in busy:
            if not self._running(n):
                self.stack.start(self._container(n))
        for n, names in strays.items():
            for name in names:
                self.stack.agents_admin("archive", "--place", str(n), "--name", name, "--yes")
        if busy:
            wait_for(lambda: self.queued() == 0, self.drain_s)
        return sorted(strays)

    def judge_and_table(self, out: Path) -> list[ModelRow]:
        """Таблица отбора по OUT (Б3): table.build — check_verdicts, aggregate с load_tag_units(out) и планом конфига
        прогонщика этого OUT; Markdown — OUT/table.md (и копия PCBK_TABLES/twin-table.md), всё — OUT/table.json;
        снимок страницы проверки — PCBK_SHOTS/twin-table.png. Строки — в порядке плана."""
        out = Path(out)
        cfg = self._cfgs.get(out) or replace(self.cfg, out_dir=out)
        rows, meta = build(cfg)
        text = render_markdown(rows, meta)
        (out / "table.md").write_text(text, encoding="utf-8")
        doc = {"meta": meta, "rows": [asdict(r) for r in rows], "verdicts": meta["verdicts"]}
        (out / "table.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        where = os.environ.get("PCBK_TABLES")
        if where:
            Path(where).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / "table.md", Path(where) / "twin-table.md")
        self.page_shot(text, "twin-table.png")
        return rows

    def page_shot(self, markdown: str, name: str) -> None:
        """Снимок страницы проверки: Markdown → HTML (markdown_page) → cdp.Browser по file:// → PCBK_SHOTS/name;
        страница растянута на весь документ. Без PCBK_SHOTS — ничего."""
        where = os.environ.get("PCBK_SHOTS")
        if not where:
            return
        page_file = self.tmp / (Path(name).stem + ".html")
        page_file.write_text(markdown_page(markdown), encoding="utf-8")
        with cdp.Browser() as browser:
            page = browser.page(isolated=True)
            page.goto(page_file.as_uri())
            page.wait_selector("table", 20)
            width, height = page.eval("[Math.max(document.documentElement.scrollWidth, 1280), "
                                      "document.documentElement.scrollHeight]")
            page._call("Emulation.setDeviceMetricsOverride",                 # весь документ — одним снимком
                       {"width": int(width), "height": int(height), "deviceScaleFactor": 1, "mobile": False})
            page.screenshot(Path(where) / name)
            assert browser.handler_errors == [] and page.console_errors() == [], browser.handler_errors

    def shot(self, name: str, row: str = "llm") -> None:
        """Снимок страницы состояния тестового стенда (cdp.py Д6) со строкой компонента row (по умолчанию «LLM-прокси
        и бюджет»; Д9а — "historian", «Историан БДРВ») в середине экрана — в PCBK_SHOTS (снимки для владельца); без
        переменной — ничего. Страница ждёт заголовок и текст этой строки из последнего снимка сторожа."""
        where = os.environ.get("PCBK_SHOTS")
        if not where:
            return
        line = row_of(self.stack.wait_status(lambda s: True, 30), row)
        with cdp.Browser() as browser:
            page = browser.page(isolated=True)
            page.goto(f"{STAND_ORIGIN}/status")
            page.wait_text(re.escape(line["title"]), 20)
            page.wait_text(re.escape(line["detail"]), 20)
            page.eval(ROW_INTO_VIEW_JS % json.dumps(line["title"], ensure_ascii=False))
            page.screenshot(Path(where) / name)
            assert browser.handler_errors == [], browser.handler_errors

    # --- уборка ---

    def _accounts(self) -> list[list[str]]:
        out = self.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "list")
        return [line.split("\t") for line in out.splitlines()]

    def _account(self, n: int) -> list[str] | None:
        return next((cols for cols in self._accounts() if cols[0] == self.login(n)), None)

    def _bound(self, n: int) -> bool:
        return any(cols[0] == f"место {n:02d}" and len(cols) > 1 and cols[1] == self.login(n)
                   for cols in self._accounts())

    def _running(self, n: int) -> bool:
        return bool(self.stack.inspect(self._container(n))["State"]["Running"])

    def _has_agents(self, n: int) -> bool:
        """Живые строки учёта или любой не-каталог в каталоге места (то, что держит release-place, Д7-R61)."""
        if _agent_rows(self.stack, n):
            return True
        code, out, _ = self.stack.sh("pcbk-core", f"find /srv/agents/student-{n:02d} -mindepth 1 ! -type d -print -quit")
        return code != 0 or bool(out.strip())

    def _close_runners(self) -> None:
        for runner in self._runners:
            runner.close()

    def _disable(self) -> None:
        """Работающие места с агентами — пробуждение учёткой места (архив перезагружает только готовое место), затем
        учётки — disable."""
        errors: list[Exception] = []
        for n in self.places:
            row = self._account(n)
            if row is None or row[3] != "активна":
                continue
            if self._running(n) and self._has_agents(n):
                try:                                    # не проснулось — учётку всё равно отключить; сбой — в конце
                    chat = self._chat(n)
                    chat.login()
                    chat.wake(timeout=WAKE_S)
                except Exception as exc:                # noqa: BLE001
                    errors.append(exc)
            self.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "disable",
                            "--login", self.login(n))
        if errors:
            raise errors[0]

    def _archive(self) -> None:
        """Агенты мест — в архив, пока места работают; затем очередь перезагрузки мест пуста (≤ drain_s). Место без
        агентов не трогается: archive ставит место в очередь и без агентов, а спящее место её не снимет."""
        for n in self.places:
            if not self._has_agents(n):
                continue
            if not self._running(n):
                self.stack.start(self._container(n))
            self.stack.agents_admin("archive", "--place", str(n), "--yes")
        wait_for(lambda: self.queued() == 0, self.drain_s)

    def _places_plain(self) -> None:
        """Места без переопределения и остановлены: stop, затем create --force-recreate (тома не трогаются)."""
        stale = [n for n in self.places
                 if "OPENCODE_CONFIG_CONTENT" in self.stack.env_names(self._container(n)) or self._running(n)]
        for n in stale:
            if self._running(n):
                self.stack.stop(self._container(n))
        if stale:
            self.stack.compose("create", "--force-recreate", "--no-build", *(f"student-{n:02d}" for n in stale))
        for n in self.places:
            if self._running(n):
                self.stack.stop(self._container(n))

    def _release(self) -> None:
        """Учётки remove, затем release-place — только мест, закреплённых за учётками модуля."""
        for n in self.places:
            row = self._account(n)
            if row is not None:
                if row[3] == "активна":
                    self.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "disable",
                                    "--login", self.login(n))
                self.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "remove",
                                "--login", self.login(n), "--yes")
            if self._bound(n):
                self.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "release-place",
                                "--place", str(n), "--yes")

    def _core_plain(self) -> None:
        """core и сторож — значения test.env ({}), если ещё нет: прогона, двойника историана, белого списка отбора,
        учения службы данных (Д9а), своих порогов тормоза (Д11б) и короткого порога тишины нет."""
        env = _env_of(self.stack, "pcbk-core")
        mounts = {m["Destination"]: m["Source"] for m in self.stack.inspect("pcbk-core")["Mounts"]}
        whitelist = str(Path(self.stack.test_env()["DATA_DIR"]) / "whitelist.txt")
        plain = self.stack.compose_config({})["services"]["core"]["environment"]
        if any(env.get(k) for k in ("LLM_RUN_ID", "LLM_RUN_USD", "LLM_RUN_CALLERS", "PCBK_TWIN_HISTORIAN",
                                    "DRILL_DATA")) \
                or any(env.get(var) != plain.get(var) for _, var, _ in BRAKE_ENV) \
                or mounts.get("/app/data/whitelist.txt") != whitelist:
            self.stack.recreate("core", {})
        if _env_of(self.stack, "pcbk-watchdog").get("LLM_RUN_SILENT_S") != SILENT_DEFAULT:
            self.stack.recreate("watchdog", {})

    def cleanup(self) -> None:
        """Уборка по README (порядок — П2, см. шапку модуля); идемпотентна. Сбой шага не обрывает следующих."""
        errors: list[BaseException] = []
        for step in (self._close_runners, lambda: self._bench(on=False), lambda: self.delay(0), self._disable,
                     self._archive, self._places_plain, self._release, self._core_plain):
            try:
                step()
            except Exception as exc:                # noqa: BLE001 — уборка идёт дальше, первый сбой — в конце
                errors.append(exc)
        if errors:
            raise errors[0]


_MD_CELL = re.compile(r"`([^`]*)`")
PAGE_CSS = ("body{font:14px/1.45 system-ui,sans-serif;margin:24px;color:#1b1b1b;background:#fff}"
            "h1{font-size:20px}h2{font-size:16px;margin-top:22px}"
            "table{border-collapse:collapse;margin:8px 0}th,td{border:1px solid #bbb;padding:3px 6px;"
            "vertical-align:top;font-size:12px}th{background:#f0f0f0}code{background:#f4f4f4;padding:0 2px}"
            "ul,ol{margin:4px 0 4px 20px;padding:0}")


def _md_inline(text: str) -> str:
    return _MD_CELL.sub(lambda m: f"<code>{m.group(1)}</code>", html.escape(text, quote=False))


def markdown_page(text: str) -> str:
    """HTML страницы проверки из Markdown таблицы отбора (render_markdown Б2): заголовки, списки, таблицы, абзацы и
    `код` — того, что там бывает, достаточно; прочее — абзацем как есть (с экранированием)."""
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            out.append("<table><thead><tr>" + "".join(f"<th>{_md_inline(c)}</th>" for c in head) + "</tr></thead>"
                       "<tbody>" + "".join("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>"
                                           for r in body) + "</tbody></table>")
            continue
        for tag, rx in (("ul", r"- (.*)"), ("ol", r"\d+\. (.*)")):
            if re.fullmatch(rx, line):
                items = []
                while i < len(lines) and (m := re.fullmatch(rx, lines[i])):
                    items.append(f"<li>{_md_inline(m.group(1))}</li>")
                    i += 1
                out.append(f"<{tag}>{''.join(items)}</{tag}>")
                break
        else:
            if m := re.fullmatch(r"(#{1,3}) (.*)", line):
                n = len(m.group(1))
                out.append(f"<h{n}>{_md_inline(m.group(2))}</h{n}>")
            elif line.strip():
                out.append(f"<p>{_md_inline(line)}</p>")
            i += 1
    return ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Таблица отбора — двойники</title>"
            f"<style>{PAGE_CSS}</style></head><body>{''.join(out)}</body></html>")


__all__ = ["BRAKE_ENV", "BenchHarness", "StackStand", "agents_left", "bench_env", "brake_env", "markdown_page"]
