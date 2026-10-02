"""CLI стенда отбора (Д8-R74, П4, m-р4-16): python -m pcbk_bench override CONFIG | run CONFIG | status OUT
[--stale-s N] | judge-pack CONFIG [--real-data] | judge-stage CONFIG [--only-missing] [--root DIR] | judge-audit STAGE
--batch batch-NN --transcript PATH [--discard] | judge-collect CONFIG [--batch batch-NN …] [--root DIR] | judge-check
CONFIG | table CONFIG … | calc CONFIG TOOL ARGS | cards-check CONFIG | cards-audit CONFIG --transcript PATH.

- override CONFIG — файл переопределения мест (render_override) в stdout;
- run CONFIG — прогон: load_config → window_start (без него или позже сейчас + 5 мин — RunRefused("window_start")
  до подключения к стенду) → connect (stand.json и secrets/ рядом с ним) → Runner.run(); в stdout — причина конца
  прогона;
- status OUT — код состояния прогона по OUT/progress.json (status_main): 0 — идёт и свеж или закончен, 1 — молчит
  или завис, 2 — остановлен или на паузе.

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
- calc CONFIG TOOL ARGS — калькулятор (catalog_search, tag_now, tag_period) токеном ops; ARGS — JSON одной строкой
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

Коды main: 0 — успех, 2 — RunRefused, 1 — прочее. Ошибка печатается только классом исключения (и кодом RunRefused),
без текста и трассировки: в тексте бывают данные. Журнал прогонщика (logging, без разговоров и тегов) — в stderr.

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
from .override import render_override
from .runner import FUTURE_WINDOW_S, Runner, RunRefused, load_records, status_main
from .secret import Secret
from .stand import DATA_TOOLS, ServerStand, Stand

REPO = Path(__file__).resolve().parents[2]
OPENCODE_JSON = REPO / "student" / "config" / "opencode.json"
PROBE_LOGIN = "d8-probe-bench-{n:02d}"
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
    return p


def _stand_json(cfg: BenchConfig) -> dict:
    if cfg.stand is None:
        raise ValueError("конфиг: stand — путь к stand.json не задан")
    raw = json.loads(Path(cfg.stand).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not {"ssh", "core_url", "edge_url", "edge_host"} <= set(raw) <= _STAND_KEYS:
        raise ValueError("stand.json: ключи ssh, core_url, edge_url, edge_host (origin, remote_dir — по желанию)")
    if not isinstance(raw["ssh"], list) or not all(isinstance(a, str) for a in raw["ssh"]):
        raise ValueError("stand.json: ssh — список строк")
    return raw


def server_stand(cfg: BenchConfig) -> Stand:
    """Только стенд сервера по stand.json и secrets/ рядом с ним (m19: calc и cards-check — без клиентов шлюза)."""
    raw = _stand_json(cfg)
    extra = {"remote_dir": raw["remote_dir"]} if raw.get("remote_dir") else {}
    return ServerStand(raw["ssh"], raw["core_url"], raw["edge_url"], raw["edge_host"],
                       Path(cfg.stand).parent / "secrets", **extra)


def connect(cfg: BenchConfig) -> tuple[Stand, dict[int, GatewayChat]]:
    """Стенд сервера и клиенты шлюза мест по stand.json и secrets/ рядом с ним (П4)."""
    raw = _stand_json(cfg)
    secrets = Path(cfg.stand).parent / "secrets"
    stand = server_stand(cfg)
    chats = {}
    for n in cfg.places:
        login = PROBE_LOGIN.format(n=n)
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


def _run(config: Path) -> int:
    cfg = load_config(config, OPENCODE_JSON)
    if cfg.window_start is None or cfg.window_start > datetime.now(timezone.utc) + timedelta(seconds=FUTURE_WINDOW_S):
        raise RunRefused("window_start")             # нет начала окна или оно в будущем (р1, m7) — до стенда
    stand, chats = connect(cfg)
    runner = Runner(cfg, stand, chats, load_cards(cfg.cards))
    print(runner.run())
    return 0


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
        return _run(args.config)
    except RunRefused as exc:
        print(f"RunRefused: {exc.code}", file=sys.stderr)
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
