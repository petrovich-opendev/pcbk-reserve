"""Команда оператора (Д25): поправки базы знаний в core.db — список и отзыв. Запуск в контейнере core:

    python -m pcbk_core.kb list [--all]      действующие (с --all — и отозванные)
    python -m pcbk_core.kb withdraw <№>      отозвать любую поправку (строка остаётся с отметкой «operator»)

База — DB_PATH окружения (как у core) или --db. Код 0 — сделано, 1 — отказ или сбой (причина в stderr).
"""
import argparse
import os
import sys
from datetime import datetime, timezone

from ..settings import Settings
from .store import CorrectionRefused, CorrectionsError, CorrectionStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pcbk_core.kb", description="Поправки базы знаний (Д25)")
    parser.add_argument("--db", default=None, help="core.db; по умолчанию DB_PATH окружения")
    sub = parser.add_subparsers(dest="cmd", required=True)
    listing = sub.add_parser("list", help="действующие поправки")
    listing.add_argument("--all", action="store_true", help="и отозванные")
    withdraw = sub.add_parser("withdraw", help="отозвать поправку")
    withdraw.add_argument("id", type=int)
    args = parser.parse_args(argv)
    store = CorrectionStore(args.db or os.environ.get("DB_PATH") or Settings.DB_PATH)
    try:
        if args.cmd == "list":
            rows = store.all() if args.all else [c for c in store.all() if c.withdrawn_ts is None]
            for c in rows:
                state = f"отозвана {c.withdrawn_ts} ({c.withdrawn_by})" if c.withdrawn_ts else "действует"
                print(f"№{c.id} {c.ts} {c.author} [{state}] {c.record}: {c.statement}"
                      + (f" | неверно: {c.wrong}" if c.wrong else "") + (f" | основание: {c.basis}" if c.basis else ""))
            print(f"всего: {len(rows)}")
        else:
            c = store.withdraw(args.id, "operator", datetime.now(timezone.utc), operator=True)
            print(f"поправка №{c.id} отозвана")
    except (CorrectionRefused, CorrectionsError) as exc:
        print(f"отказ: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
