"""CLI стенда отбора (Д8-R74, П4, m-р4-16): python -m pcbk_bench override CONFIG | run CONFIG | status OUT
[--stale-s N] | judge-pack CONFIG [--real-data] | judge-stage CONFIG [--only-missing] [--root DIR] | judge-audit STAGE
--batch batch-NN --transcript PATH [--discard] | judge-collect CONFIG [--batch batch-NN …] [--root DIR] | judge-check
CONFIG | table CONFIG … | calc CONFIG TOOL ARGS | cards-check CONFIG | cards-audit CONFIG --transcript PATH |
match-cases MCONFIG | match MCONFIG [--only basic] | probe-calc MCONFIG | load-check CONFIG | load-gate OUT --next K
[--warn-mib 2048].

- override CONFIG — файл переопределения мест (render_override) в stdout;
- run CONFIG — прогон: load_config → window_start (без него или позже сейчас + 5 мин — RunRefused("window_start")
  до подключения к стенду) → connect (stand.json и secrets/ рядом с ним) → Runner.run(); в stdout — причина конца
  прогона;
- status OUT — код состояния прогона по OUT/progress.json (status_main): 0 — идёт и свеж или закончен, 1 — молчит
  или завис (в режиме нагрузки — и молчит сторож памяти), 2 — остановлен или на паузе.

Нагрузка (Д11-R119…R122, R131; конфиг с "load": true):
- run CONFIG — учётки мест d11-probe-load-NN (LOAD_LOGIN, пробные: место освобождает release-place); до подключения
  к стенду — load_data twin (только стенд) — RunRefused("load_data"), нет любого поля базы окна (WINDOW_BASE_KEYS) —
  RunRefused("window_base");
- load-check CONFIG — успех 4 под нагрузкой (R120) по OUT и стенду: числа журнала службы и прокси за окно прогона
  (started_at … updated_at progress.json), цена реплики (OUT/price.json), снимки /health/llm начала и конца, «только
  своё» (OUT/isolation.json); печатает только числа и коды исходов, затем строки нарушений; код 0 — норма, 1 — иначе;
- load-gate OUT --next K [--warn-mib 2048] — прогноз памяти на K мест по ступени OUT (R122): одна строка чисел; код 0
  — «можно», 1 — «нельзя» (в том числе: на старте ступени нет данных датчика жёсткого условия).

Судья (Б1, Д8-R78; команды по конфигу — П7: OUT = out_dir, карточки — cards, секреты — secrets/ рядом со stand.json,
корень каталога судей — каталог stand.json или --root, run_id — из конфига):
- judge-pack CONFIG [--real-data] — пакеты и пачки в OUT/judge/ (только done, последняя запись ключа); --real-data —
  только с OUT/judge/real-data-consent.txt, иначе RunRefused("consent");
- judge-stage CONFIG [--only-missing] — каталог судей <root>/judge-<run_id>/ с заданиями tasks/batch-NN.txt;
  --only-missing — второй заход: задания только по пропавшим и кривым вердиктам (m7);
- judge-audit STAGE --batch batch-NN --transcript PATH [--discard] — сверка транскрипта субагента по списку
  разрешений: печатает только нарушения (инструмент и вид пути), код 1 при нарушениях; --discard — вердикты пачки
  с нарушением удаляются из каталога судей;
- judge-collect CONFIG [--batch batch-NN …] — перенос вердиктов (всех или этих пачек) в OUT/judge/verdicts/;
- judge-check CONFIG — разбор вердиктов: счёт, id пропавших и кривых пакетов, контроль J1–J4; код 1, если есть
  пропавшие или кривые;
- table CONFIG … — таблица и рекомендация (pcbk_bench.table.main, Б2), ленивым импортом.

Карточки окна (Б3, Д8-R80, I-р4-6; m19 — только ServerStand, без клиентов шлюза и паролей учёток):
- calc CONFIG TOOL ARGS — калькулятор (DATA_TOOLS: catalog_search, tag_now, tag_period, tag_series, calc_runtime)
  токеном ops; ARGS — JSON одной строкой
  ('{"tags": ["…"]}'); в stdout — ответ службы как есть (JSON). Только для субагента карточек (bench/cards/WRITER.md):
  контроллер его не зовёт;
- cards-check CONFIG — коды формы карточек конфига и статусы калькулятора (cards_check), по строке: годно — ровно
  «C2: unlisted» и «C4: no_data», код 0; иначе код 1. Карточки не того вида — одна строка «cards_form:<место>»
  (cards.CardsFormError.where: «<id>.<поле>», «bench_tags» …; файла нет или иной сбой — «cards_form»). Ожидание окна
  частоты калькулятора — строка «калькулятор: rate — пауза» в stderr на каждую паузу. Значений, тегов и текстов в
  выводе нет;
- cards-audit CONFIG --transcript PATH — сверка транскрипта субагента карточек по списку разрешений WRITER.md
  (cards.audit_cards): печатает только нарушения (инструмент и вид), код 1 при нарушениях. Команда — из того же
  дерева, откуда субагент читал WRITER.md: образец и задание сверяются по путям этого дерева.

«Агент = калькулятор» (Д9-R91, Д9-R93; MCONFIG — MatchConfig JSON, $JOB/pcbk-d9/live/match.config.json: stand,
place, login, cases, out_dir, turn_timeout_s, source; пароль учётки — secrets/<login>.pw рядом со stand.json):
- match-cases MCONFIG — случаи окна (cases_from_agent, I7) в файл cases (0600); печатает только «случаев N: аналоговых
  a (из агента k), состояний d» и, если живые теги без данных за период пропущены (I-3 итога Д9б), «; без данных за
  период пропущено m»;
- match MCONFIG [--only basic] — прогон агента проверки на месте place по файлу cases; печатает итог render_match
  (псевдонимы ТЕГ-n, без тегов и чисел) и строку «успех: да | нет (коды)», пишет OUT/match.md и OUT/match.jsonl
  (0600). Код 0 — успех; 3 — не успех (match_failures: жёсткие, service, вид без match, прогон оборван до конца
  плана). --only basic — без видов profile_last_8h и runtime_yesterday (проба не прошла); режимов сухого прогона нет
  (I6);
- probe-calc MCONFIG — проба форм окна: печатает только коды «series: …» и «runtime: …» (unresolved — у живых тегов
  вида нет данных за период: проба не решена).

Коды main: 0 — успех, 2 — RunRefused, 3 — автотест match не успех, 1 — прочее. Ошибка печатается только классом
исключения (и кодом RunRefused), без текста и трассировки: в тексте бывают данные. Журнал прогонщика (logging, без
разговоров и тегов) — в stderr.

stand.json (П4, $JOB/pcbk-d8/stand.json, 0600): {"ssh": ["ssh", …, "user@host"], "core_url": "http://127.0.0.1:18000",
"edge_url": "https://127.0.0.1:28443", "edge_host": "<имя сертификата>[:порт]", "origin": null, "remote_dir":
"/opt/pcbk-reserve"}; origin и remote_dir — необязательны. Секреты — каталог secrets/ рядом со stand.json:
ops.data-token (калькулятор и единицы каталога), d8-probe-bench-NN.pw (пароль учётки места NN).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cards import CHECK_GOOD, CardsFormError, audit_cards, cards_check, load_cards
from .config import BenchConfig, load_config
from .gateway import GatewayChat
from .match import (BASIC_KINDS, MatchConfig, MatchRunner, build_cases, case_count, cases_from_agent, hist_today,
                    load_case_tags, load_match_config, match_failures, probe_calc, render_match, write_case_tags,
                    write_private)
from .override import render_override
from .config import WINDOW_BASE_KEYS
from .load import MEM_WARN_MIB, check_lines, check_verdict, gate_from_out, overrun_usd, run_rows, runs_json
from .runner import FUTURE_WINDOW_S, Runner, RunRefused, load_records, status_main
from .secret import Secret
from .stand import DATA_TOOLS, ServerStand, Stand

REPO = Path(__file__).resolve().parents[2]
OPENCODE_JSON = REPO / "student" / "config" / "opencode.json"
PROBE_LOGIN = "d8-probe-bench-{n:02d}"
LOAD_LOGIN = "d11-probe-load-{n:02d}"   # учётки нагрузки: под PROBE_LOGIN core — место освобождается (B-1 р1)
MATCH_NOT_PASSED = 3                    # match: прогон прошёл, но автотест — не успех (match_failures, I-2 итога Д9б)
_STAND_KEYS = frozenset({"ssh", "core_url", "edge_url", "edge_host", "origin", "remote_dir"})


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m pcbk_bench", description="Стенд отбора модели (Д8)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("override", help="файл переопределения мест").add_argument("config", type=Path)
    sub.add_parser("run", help="прогон отбора").add_argument("config", type=Path)
    status = sub.add_parser("status", help="код состояния прогона по OUT/progress.json")
    status.add_argument("out", type=Path)
    status.add_argument("--stale-s", type=float, default=120.0)
    pack = sub.add_parser("judge-pack", help="пакеты судьи и пачки в OUT/judge/")
    pack.add_argument("config", type=Path)
    pack.add_argument("--real-data", action="store_true")
    stage = sub.add_parser("judge-stage", help="каталог судей с заданиями пачек")
    stage.add_argument("config", type=Path)
    stage.add_argument("--only-missing", action="store_true")
    stage.add_argument("--root", type=Path, default=None)
    audit = sub.add_parser("judge-audit", help="сверка транскрипта субагента-судьи по списку разрешений")
    audit.add_argument("stage", type=Path)
    audit.add_argument("--batch", required=True)
    audit.add_argument("--transcript", type=Path, required=True)
    audit.add_argument("--discard", action="store_true")
    collect = sub.add_parser("judge-collect", help="перенос вердиктов в OUT/judge/verdicts/")
    collect.add_argument("config", type=Path)
    collect.add_argument("--batch", action="append", default=None)
    collect.add_argument("--root", type=Path, default=None)
    check = sub.add_parser("judge-check", help="разбор вердиктов и контроль J1–J4")
    check.add_argument("config", type=Path)
    sub.add_parser("table", help="таблица и рекомендация (Б2): table CONFIG …", add_help=False)
    calc = sub.add_parser("calc", help="калькулятор токеном ops (для субагента карточек): ответ службы как есть")
    calc.add_argument("config", type=Path)
    calc.add_argument("tool", choices=DATA_TOOLS)
    calc.add_argument("args")
    sub.add_parser("cards-check", help="коды формы карточек и статусы калькулятора").add_argument("config", type=Path)
    cards_audit = sub.add_parser("cards-audit", help="сверка транскрипта субагента карточек по списку разрешений")
    cards_audit.add_argument("config", type=Path)
    cards_audit.add_argument("--transcript", type=Path, required=True)
    sub.add_parser("match-cases", help="случаи окна «агент = калькулятор» из агента места-источника").add_argument(
        "config", type=Path)
    match = sub.add_parser("match", help="прогон «агент = калькулятор»: итог без тегов, OUT/match.md")
    match.add_argument("config", type=Path)
    match.add_argument("--only", choices=["basic"], default=None)
    sub.add_parser("probe-calc", help="проба форм окна: только коды").add_argument("config", type=Path)
    sub.add_parser("load-check", help="успех 4 под нагрузкой: только числа").add_argument("config", type=Path)
    gate = sub.add_parser("load-gate", help="прогноз памяти на следующую ступень лестницы")
    gate.add_argument("out", type=Path)
    gate.add_argument("--next", type=int, required=True, dest="next_k")
    gate.add_argument("--warn-mib", type=int, default=MEM_WARN_MIB)
    return p


def _stand_json(cfg: BenchConfig) -> dict:
    if cfg.stand is None:
        raise ValueError("конфиг: stand — путь к stand.json не задан")
    return _read_stand(Path(cfg.stand))


def _read_stand(path: Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not {"ssh", "core_url", "edge_url", "edge_host"} <= set(raw) <= _STAND_KEYS:
        raise ValueError("stand.json: ключи ssh, core_url, edge_url, edge_host (origin, remote_dir — по желанию)")
    if not isinstance(raw["ssh"], list) or not all(isinstance(a, str) for a in raw["ssh"]):
        raise ValueError("stand.json: ssh — список строк")
    return raw


def server_stand(cfg: BenchConfig) -> Stand:
    """Только стенд сервера по stand.json и secrets/ рядом с ним (m19: calc и cards-check — без клиентов шлюза)."""
    if cfg.stand is None:
        raise ValueError("конфиг: stand — путь к stand.json не задан")
    return _server_stand_at(Path(cfg.stand))


def _server_stand_at(path: Path) -> Stand:
    raw = _read_stand(path)
    extra = {"remote_dir": raw["remote_dir"]} if raw.get("remote_dir") else {}
    return ServerStand(raw["ssh"], raw["core_url"], raw["edge_url"], raw["edge_host"], Path(path).parent / "secrets",
                       **extra)


def match_stand(mcfg: MatchConfig) -> Stand:
    """Стенд сервера «агент = калькулятор» (match-cases, probe-calc): только stand.json и токен ops."""
    return _server_stand_at(mcfg.stand)


def match_connect(mcfg: MatchConfig) -> tuple[Stand, GatewayChat]:
    """Стенд сервера и клиент шлюза учётки login (пароль — secrets/<login>.pw рядом со stand.json); вход — в
    MatchRunner.prepare()."""
    raw = _read_stand(mcfg.stand)
    pw_file = mcfg.stand.parent / "secrets" / f"{mcfg.login}.pw"
    account = {"login": mcfg.login, "password": Secret(pw_file.read_text(encoding="ascii").strip())}
    return match_stand(mcfg), GatewayChat(raw["edge_url"], raw["edge_host"], raw.get("origin"), **account)


def connect(cfg: BenchConfig) -> tuple[Stand, dict[int, GatewayChat]]:
    """Стенд сервера и клиенты шлюза мест по stand.json и secrets/ рядом с ним (П4)."""
    raw = _stand_json(cfg)
    secrets = Path(cfg.stand).parent / "secrets"
    stand = server_stand(cfg)
    chats = {}
    for n in cfg.places:
        login = (LOAD_LOGIN if cfg.load else PROBE_LOGIN).format(n=n)
        account = {"login": login, "password": Secret((secrets / f"{login}.pw").read_text(encoding="ascii").strip())}
        chats[n] = GatewayChat(raw["edge_url"], raw["edge_host"], raw.get("origin"), **account)   # вход — в prepare()
    return stand, chats


def _out(cfg: BenchConfig) -> Path:
    if cfg.out_dir is None:
        raise ValueError("конфиг: out_dir — путь")
    return Path(cfg.out_dir)


def _judge_root(cfg: BenchConfig, root: Path | None) -> Path:
    """Корень каталога судей: --root, иначе каталог stand.json ($JOB/pcbk-d8, П7)."""
    if root is not None:
        return root
    if cfg.stand is None:
        raise ValueError("конфиг: stand не задан — укажите --root")
    return Path(cfg.stand).parent


def _secrets(cfg: BenchConfig) -> list[Secret]:
    """Секреты стенда для сканера пакетов: файлы secrets/ рядом со stand.json (токен ops, пароли учёток)."""
    if cfg.stand is None:
        return []
    folder = Path(cfg.stand).parent / "secrets"
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.iterdir()):
        if f.is_file():
            value = f.read_text(encoding="utf-8", errors="replace").strip()
            if value:
                out.append(Secret(value))
    return out


def _judge(args: argparse.Namespace) -> int:
    from . import judge                       # лениво: прогону судья не нужен

    if args.command == "judge-audit":
        problems = judge.audit_transcripts(judge.transcript_calls(args.transcript), args.stage, args.batch)
        for line in problems:
            print(line)
        print(f"нарушений: {len(problems)}")
        if problems and args.discard:
            print(f"вердиктов пачки удалено: {judge.discard_verdicts(args.stage, args.batch)}")
        return 1 if problems else 0
    cfg = load_config(args.config, OPENCODE_JSON)
    out = _out(cfg)
    if args.command == "judge-pack":
        batches = judge.pack_batches(out, load_records(out), load_cards(cfg.cards), _secrets(cfg), seed=cfg.run_id,
                                     real_data=args.real_data)
        cmap = judge.load_map(out)
        print(f"пакетов: {len(cmap['packets']) - len(cmap['refused'])}, отказано сканером: {len(cmap['refused'])}, "
              f"контрольных: {len(cmap['controls'])}, пачек: {len(batches)}")
        return 0
    if args.command == "judge-stage":
        only = None
        if args.only_missing:
            report = judge.check_verdicts(out)
            only = set(report.missing) | set(report.invalid)
        stage = judge.stage_judges(out, cfg.run_id, _judge_root(cfg, args.root), only=only)
        print(f"каталог судей: {stage}")
        print(f"заданий: {len(list((stage / 'tasks').glob('batch-*.txt')))}")
        return 0
    if args.command == "judge-collect":
        stage = _judge_root(cfg, args.root) / f"judge-{cfg.run_id}"
        print(f"вердиктов перенесено: {judge.collect_verdicts(stage, out, args.batch)}")
        return 0
    report = judge.check_verdicts(out)                                  # judge-check
    print(f"разобрано: {len(report.ok)}, пропало: {len(report.missing)}, кривых: {len(report.invalid)}, "
          f"отказано сканером: {len(report.refused)}")
    for pid in report.missing:
        print(f"пропал: {pid}")
    for pid in report.invalid:
        print(f"кривой: {pid}")
    for cid, ok in judge.controls_ok(report, judge.load_map(out)["controls"]).items():
        print(f"контроль {cid}: {'верен' if ok else 'ПРОМАХ'}")
    return 1 if report.missing or report.invalid else 0


def _calc(args: argparse.Namespace) -> int:
    """calc CONFIG TOOL ARGS: ответ службы как есть (JSON одной строкой) — только субагенту карточек."""
    try:
        call_args = json.loads(args.args)
    except ValueError:
        raise _UsageError("args") from None
    if not isinstance(call_args, dict):
        raise _UsageError("args")
    answer = server_stand(load_config(args.config, OPENCODE_JSON)).calc(args.tool, call_args)
    print(json.dumps(answer, ensure_ascii=False))
    return 0


def _rate_pause(seconds: float) -> None:
    """Пауза калькулятора на rate у cards-check: строка в stderr (контроллер видит, что команда ждёт окно частоты)."""
    print("калькулятор: rate — пауза", file=sys.stderr, flush=True)
    time.sleep(seconds)


def _cards_check(config: Path) -> int:
    """cards-check CONFIG: коды по строке (cards_check); годно — ровно CHECK_GOOD, код 0; иначе код 1. Вид карточек
    не тот — «cards_form:<место>» (без значений); иной сбой чтения — «cards_form»."""
    cfg = load_config(config, OPENCODE_JSON)
    try:
        cs = load_cards(cfg.cards)
    except CardsFormError as exc:
        print(f"cards_form:{exc.where}")          # место без значений: текст ошибки не печатается
        return 1
    except (ValueError, OSError, TypeError):
        print("cards_form")
        return 1
    codes = cards_check(cs, server_stand(cfg), sleep=_rate_pause)
    for code in codes:
        print(code)
    return 0 if tuple(codes) == CHECK_GOOD else 1


def _cards_audit(args: argparse.Namespace) -> int:
    """cards-audit CONFIG --transcript PATH: нарушения списка разрешений субагента карточек по строке и их число."""
    from . import judge                       # лениво: разбор транскрипта и служебные инструменты харнесса

    cfg = load_config(args.config, OPENCODE_JSON)
    problems = audit_cards(judge.transcript_calls(args.transcript), args.config, cfg.cards, judge.HARNESS_TOOLS)
    for line in problems:
        print(line)
    print(f"нарушений: {len(problems)}")
    return 1 if problems else 0


def _match_cases(config: Path) -> int:
    """match-cases MCONFIG: теги окна → файл cases (0600); в stdout — только счёт."""
    mcfg = load_match_config(config)
    if mcfg.source_place is None:
        raise ValueError("конфиг: source — место-источник случаев не задано")
    analog, discrete, from_agent, skipped = cases_from_agent(match_stand(mcfg), mcfg.source_place)
    write_case_tags(mcfg.cases, analog, discrete)
    print(f"случаев {case_count(analog, discrete)}: аналоговых {len(analog)} (из агента {from_agent}), "
          f"состояний {len(discrete)}" + (f"; без данных за период пропущено {skipped}" if skipped else ""))
    return 0 if analog or discrete else 1


def _match(config: Path, only: str | None) -> int:
    """match MCONFIG [--only basic]: прогон по файлу cases; итог render_match — в stdout и OUT/match.md (0600)."""
    mcfg = load_match_config(config)
    analog, discrete = load_case_tags(mcfg.cases)
    stand, chat = match_connect(mcfg)
    cases = build_cases(analog, discrete, hist_today(stand, (analog + discrete)[0]))
    if only == "basic":
        cases = [c for c in cases if c.kind in BASIC_KINDS]
    runner = MatchRunner(chat, stand, mcfg.place, cases, mcfg.out_dir, turn_timeout_s=mcfg.turn_timeout_s)
    try:
        results = runner.run()
    finally:
        runner.close()
    md = render_match(results, cases)
    write_private(mcfg.out_dir / "match.md", md)
    sys.stdout.write(md)
    return MATCH_NOT_PASSED if match_failures(results, cases) else 0


def _probe_calc(config: Path) -> int:
    """probe-calc MCONFIG: коды пробы форм окна по строке."""
    for code in probe_calc(match_stand(load_match_config(config))):
        print(code)
    return 0


def _run(config: Path) -> int:
    cfg = load_config(config, OPENCODE_JSON)
    if cfg.window_start is None or cfg.window_start > datetime.now(timezone.utc) + timedelta(seconds=FUTURE_WINDOW_S):
        raise RunRefused("window_start")             # нет начала окна или оно в будущем (р1, m7) — до стенда
    if cfg.load:
        if cfg.load_data == "twin":
            raise RunRefused("load_data")            # двойник — только стенд (R131)
        if any(getattr(cfg, key) is None for key in WINDOW_BASE_KEYS):
            raise RunRefused("window_base")          # сервер: база окна и пороги — явно в конфиге ступени (R119)
    stand, chats = connect(cfg)
    runner = Runner(cfg, stand, chats, load_cards(cfg.cards))
    print(runner.run())
    return 0


def _json_file(path: Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{Path(path).name}: нужен объект")
    return raw


def _load_check(config: Path) -> int:
    """load-check CONFIG (R120): числа — в stdout, затем нарушения по строке; код 0 — норма, 1 — иначе."""
    cfg = load_config(config, OPENCODE_JSON)
    if not cfg.load:
        raise ValueError("конфиг: load-check — только для режима нагрузки")
    out = _out(cfg)
    progress = _json_file(out / "progress.json")
    since, until = progress.get("started_at"), progress.get("updated_at")
    if not isinstance(since, str) or not isinstance(until, str):
        raise ValueError("progress.json: нет started_at или updated_at")
    numbers = server_stand(cfg).load_check([f"student-{n:02d}" for n in cfg.places], since, until, out=out)
    price = numbers.pop("price", None) or _json_file(out / "price.json")
    before, end = _json_file(out / "health_start.json"), _json_file(out / "health_end.json")
    iso = _json_file(out / "isolation.json") if (out / "isolation.json").exists() else {}
    overrun = overrun_usd(run_rows(runs_json(out)), len(cfg.places))
    data = cfg.load_data != "none"
    problems = check_verdict(numbers, before, end, iso, overrun_usd=overrun, data=data)
    for line in check_lines(numbers, price, before, end, iso, overrun_usd=overrun, data=data):
        print(line)
    for line in problems:
        print(line)
    print("load-check: норма" if not problems else f"load-check: нарушений {len(problems)}")
    return 1 if problems else 0


def _load_gate(out: Path, next_k: int, warn_mib: int) -> int:
    """load-gate OUT --next K: одна строка чисел; код 0 — «можно», 1 — «нельзя»."""
    if not 1 <= next_k <= 11:                       # Д15б: мест 11, не 10
        raise _UsageError("--next")
    line, code = gate_from_out(out, next_k, warn_mib)
    print(line)
    return code


def main(argv: list[str] | None = None) -> int:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(threadName)s %(message)s"))
    logger = logging.getLogger("pcbk_bench")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    parser = _parser()
    args_list = list(sys.argv[1:] if argv is None else argv)
    try:
        if args_list[:1] == ["table"]:
            from .table import main as table_main          # лениво: таблица — Б2
            return table_main(args_list[1:])
        args = parser.parse_args(args_list)
        if args.command.startswith("judge-"):
            return _judge(args)
        if args.command == "override":
            sys.stdout.write(render_override(load_config(args.config, OPENCODE_JSON).places))
            return 0
        if args.command == "status":
            return status_main(args.out, args.stale_s)
        if args.command == "calc":
            return _calc(args)
        if args.command == "cards-check":
            return _cards_check(args.config)
        if args.command == "cards-audit":
            return _cards_audit(args)
        if args.command == "match-cases":
            return _match_cases(args.config)
        if args.command == "match":
            return _match(args.config, args.only)
        if args.command == "probe-calc":
            return _probe_calc(args.config)
        if args.command == "load-check":
            return _load_check(args.config)
        if args.command == "load-gate":
            return _load_gate(args.out, args.next_k, args.warn_mib)
        return _run(args.config)
    except RunRefused as exc:
        print(f"RunRefused: {exc.code}" + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 2
    except _UsageError:
        parser.print_usage(sys.stderr)
        return 1
    except SystemExit as exc:                  # --help
        return exc.code if isinstance(exc.code, int) else 1
    except BaseException as exc:               # noqa: BLE001 — верх CLI: только класс, без текста (m-р4-16)
        print(type(exc).__name__, file=sys.stderr)
        return 1
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


if __name__ == "__main__":
    sys.exit(main())
