"""CLI стенда отбора (Д8-R74, П4, m-р4-16): python -m pcbk_bench override CONFIG | run CONFIG | status OUT
[--stale-s N].

- override CONFIG — файл переопределения мест (render_override) в stdout;
- run CONFIG — прогон: load_config → window_start (без него или позже сейчас + 5 мин — RunRefused("window_start")
  до подключения к стенду) → connect (stand.json и secrets/ рядом с ним) → Runner.run(); в stdout — причина конца
  прогона;
- status OUT — код состояния прогона по OUT/progress.json (status_main): 0 — идёт и свеж или закончен, 1 — молчит
  или завис, 2 — остановлен или на паузе.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cards import load_cards
from .config import BenchConfig, load_config
from .gateway import GatewayChat
from .override import render_override
from .runner import FUTURE_WINDOW_S, Runner, RunRefused, status_main
from .secret import Secret
from .stand import ServerStand, Stand

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
    return p


def connect(cfg: BenchConfig) -> tuple[Stand, dict[int, GatewayChat]]:
    """Стенд сервера и клиенты шлюза мест по stand.json и secrets/ рядом с ним (П4)."""
    if cfg.stand is None:
        raise ValueError("конфиг: stand — путь к stand.json не задан")
    raw = json.loads(Path(cfg.stand).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not {"ssh", "core_url", "edge_url", "edge_host"} <= set(raw) <= _STAND_KEYS:
        raise ValueError("stand.json: ключи ssh, core_url, edge_url, edge_host (origin, remote_dir — по желанию)")
    if not isinstance(raw["ssh"], list) or not all(isinstance(a, str) for a in raw["ssh"]):
        raise ValueError("stand.json: ssh — список строк")
    secrets = Path(cfg.stand).parent / "secrets"
    extra = {"remote_dir": raw["remote_dir"]} if raw.get("remote_dir") else {}
    stand = ServerStand(raw["ssh"], raw["core_url"], raw["edge_url"], raw["edge_host"], secrets, **extra)
    chats = {}
    for n in cfg.places:
        login = PROBE_LOGIN.format(n=n)
        account = {"login": login, "password": Secret((secrets / f"{login}.pw").read_text(encoding="ascii").strip())}
        chats[n] = GatewayChat(raw["edge_url"], raw["edge_host"], raw.get("origin"), **account)   # вход — в prepare()
    return stand, chats


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
    try:
        args = parser.parse_args(argv)
        if args.command == "override":
            sys.stdout.write(render_override(load_config(args.config, OPENCODE_JSON).places))
            return 0
        if args.command == "status":
            return status_main(args.out, args.stale_s)
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
