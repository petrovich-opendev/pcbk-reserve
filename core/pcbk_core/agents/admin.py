"""Команда администратора агентов студентов (Д7-R61): python -m pcbk_core.agents.admin
(в core: docker compose exec core python -m pcbk_core.agents.admin …).

- list [--place N] — строки учёта: место, имя, название, состояние, создан, признаки черновика («-» — нет). Последние
  строки — расхождения файлов с учётом (drift): с --place — «файлы без учёта: a.md; учёт без файлов: c; ссылки: d.md»
  или «расхождений нет», без --place — то же с «место NN: » по каждому месту с расхождениями. Места, каталог которых
  не читается, — одной строкой в stderr и код 1 (строки учёта и сверка остальных мест всё равно выводятся);
- archive --place N [--name X] --yes — файлы в архив, строки archived, место в очередь перезагрузки. Без --name
  уносит все файлы каталога места — и без учёта, и символьные ссылки, и временные файлы оборванной записи: после него
  release-place не упирается в агентов места, а новому студенту не остаётся данных прежнего;
- reload --place N — место в очередь перезагрузки (после ручной правки файлов).

Коды выхода и вывод — как у pcbk_core.gateway.accounts (П6): удача — 0; ошибка, в том числе ошибка разбора и archive
без --yes, — 1 и одна строка в stderr. База и корень — Settings.from_env(): DB_PATH и AGENTS_ROOT. Только stdlib.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone

from pcbk_core.settings import Settings

from .store import PLACES, AgentStore, DirUnavailable, StoreError

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Ошибка разбора — код 1 и одна строка в stderr, как у accounts (П6), а не usage и код 2."""

    def error(self, message: str):
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m pcbk_core.agents.admin",
                description="Агенты студентов: учёт и файлы мест, архив, очередь перезагрузки мест.")
    sub = p.add_subparsers(dest="command", required=True, metavar="команда")
    show = sub.add_parser("list", help="агенты (место, имя, название, состояние, создан, признаки) и расхождения"
                                       " файлов с учётом")
    show.add_argument("--place", type=int, help="место 1…10; без него — все места")
    archive = sub.add_parser("archive", help="агентов места — в архив, место — в очередь перезагрузки")
    archive.add_argument("--place", required=True, type=int)
    archive.add_argument("--name", help="имя агента (как в list); без него — все агенты и файлы места")
    archive.add_argument("--yes", action="store_true")
    reload = sub.add_parser("reload", help="место — в очередь перезагрузки (после ручной правки файлов)")
    reload.add_argument("--place", required=True, type=int)
    return p


def _show(text: str) -> str:
    """Имя для вывода: файлы без учёта кладёт кто угодно — управляющие знаки и не-UTF-8 не ломают строку."""
    return _CONTROL.sub("?", text.encode("utf-8", "backslashreplace").decode("utf-8"))


def _place(place: int) -> int:
    if place not in PLACES:
        raise ValueError("место: 1…10")
    return place


def _drift_text(store: AgentStore, place: int) -> str:
    files, rows, links = store.drift(place)
    parts = []
    if files:
        parts.append("файлы без учёта: " + ", ".join(map(_show, files)))
    if rows:
        parts.append("учёт без файлов: " + ", ".join(map(_show, rows)))
    if links:
        parts.append("ссылки: " + ", ".join(map(_show, links)))
    return "; ".join(parts)


def _list(store: AgentStore, place: int | None) -> int:
    places = list(PLACES) if place is None else [_place(place)]
    for n in places:
        for row in store.list_place(n):
            flags = ",".join(store.flags_of(n, row.name)) or "-"
            print(f"{n:02d}\t{_show(row.name)}\t{_show(row.title)}\t{row.state}\t{row.created_at}\t{flags}")
    lines, unreadable = [], []
    for n in places:
        try:
            text = _drift_text(store, n)
        except DirUnavailable:
            unreadable.append(f"{n:02d}")
            continue
        if text:
            lines.append(text if place is not None else f"место {n:02d}: {text}")
    if not unreadable:
        print("\n".join(lines) or "расхождений нет")
        return 0
    for line in lines:
        print(line)
    # договор команды — одна строка в stderr (доводка р1, m6): все недоступные места в ней
    where = f"место {unreadable[0]}" if len(unreadable) == 1 else f"места {', '.join(unreadable)}"
    return _fail(f"{where}: расхождения не сверены — каталог места отсутствует или не читается")


def _run(store: AgentStore, args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    if args.command == "list":
        return _list(store, args.place)
    place = _place(args.place)
    if args.command == "archive":
        if not args.yes:
            raise ValueError("archive уносит агентов места в архив — повторите с --yes")
        names = store.archive(place, args.name, now)
        if not names and args.name is not None:
            raise ValueError(f"агента «{_show(args.name)}» на месте {place:02d} нет — имя как в list, без .md")
        done = f"в архив: {', '.join(map(_show, names))}" if names else "архивировать нечего"
        print(f"место {place:02d}: {done}; место в очереди перезагрузки")
    elif args.command == "reload":
        store.mark_reload(place, now)
        print(f"место {place:02d}: в очереди перезагрузки")
    return 0


def _fail(message: str) -> int:
    print(message.replace("\r", " ").replace("\n", " "), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Команда администратора. Перед операцией — AgentStore.open(). Удача — 0; ошибка — 1 и одна строка в stderr."""
    try:
        args = _parser().parse_args(argv)
    except _UsageError as exc:
        return _fail(f"команда: {exc}")
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        return _fail(f"настройки: {exc}")
    store = AgentStore(settings.DB_PATH, settings.AGENTS_ROOT)
    if not store.open():
        return _fail(f"учёт агентов не открывается: {store.last_error}")
    try:
        return _run(store, args)
    except (ValueError, StoreError, DirUnavailable) as exc:
        return _fail(str(exc))
    except Exception as exc:                        # текст непредвиденной ошибки может нести ввод — только класс
        return _fail(f"сбой команды: {type(exc).__name__}")


if __name__ == "__main__":
    sys.exit(main())
