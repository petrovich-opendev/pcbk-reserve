"""Построитель белого списка по правилу d3-1 (Д3а-R1) — запускается на сервере при владельце.

    python -m pcbk_core.data.build_whitelist --out /data/whitelist.txt --extra /data/whitelist.extra \
        [--base /data/prior.txt] [--env-file /run/secrets/bdrv.env]

Правило d3-1:
1. Имя — только [A-Za-z0-9_] (SAFE_NAME).
2. Участок — имя начинается с 20…25. С --base вместо этого — «имя есть в прежнем списке».
3. Плюс имена из whitelist.extra.
4. Аналоговые — без служебных хвостов SERVICE_TAILS, регистр не важен. _LMN исключён до слова владельца.
5. Дискретные — только хвосты состояний STATE_TAILS (с регистром, как в прежнем правиле).
6. Правила 4–5 действуют и на имена из whitelist.extra и прежнего списка.
7. Живость не фильтрует.
8. Пустой результат файл не перезаписывает.

Имя из whitelist.extra или прежнего списка, которого нет в каталоге, в список не идёт: без каталога
неизвестен вид тега, и правила 4–5 к нему не применить. Печатаются только числа: имена тегов —
производственные данные заказчика.
"""
import argparse
import contextlib
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from typing import Literal

from ..logs import setup_logging
from ..secrets import BdrvConfig
from ..settings import Settings
from .catalog import KIND_BY_TYPE
from .historian import HistorianError, Row, tds_query
from .names import load_names
from .sql import SAFE_NAME, catalog_sql

RULE_VERSION = "d3-1"
SECTION_PREFIX = re.compile(r"2[0-5]")
SERVICE_TAILS = ("_LMN", "_TH", "_HMI", "_SP_HMI", "_MV1", "_m3")
STATE_TAILS = ("_RUN", "_OPN", "_CLS", "_ON", "_OFF", "_STOP", "_FLT", "_ALM")
CATALOG_TIMEOUT_S = 120.0
FILE_MODE = 0o444
# счётчик «по хвостам» называет только общеизвестные суффиксы (05 §3.5), остальное — одним числом:
# произвольный хвост — кусок имени тега
REPORT_TAILS = ("_PV", "_SP", "_MV", "_LOD", "_SPD", "_OP", "_CUR") + STATE_TAILS
_TAIL = re.compile(r"_([A-Za-z][A-Za-z0-9]*)$")
# длинный хвост раньше короткого: _SP_HMI считается своим, а не _HMI
_SERVICE_BY_LENGTH = sorted(SERVICE_TAILS, key=len, reverse=True)
_HEADER_KEY = re.compile(r"[a-z][a-z0-9_]*")

Verdict = Literal["ok", "rule1", "rule23", "rule4", "rule5", "type"]


def service_tail(name: str) -> str | None:
    """Служебный хвост аналогового тега (правило 4) без учёта регистра; нет — None."""
    low = name.lower()
    return next((tail for tail in _SERVICE_BY_LENGTH if low.endswith(tail.lower())), None)


def verdict(name: object, tag_type: object, extra: frozenset[str], base: frozenset[str] | None) -> Verdict:
    """Судьба строки каталога: ok — в список, иначе — какое правило её отсекло."""
    if not isinstance(name, str) or SAFE_NAME.fullmatch(name) is None:
        return "rule1"
    kind = KIND_BY_TYPE.get(tag_type)
    if kind is None:
        return "type"
    in_scope = SECTION_PREFIX.match(name) is not None if base is None else name in base
    if not (in_scope or name in extra):
        return "rule23"
    if kind == "analog" and service_tail(name) is not None:
        return "rule4"
    if kind == "discrete" and not name.endswith(STATE_TAILS):
        return "rule5"
    return "ok"


def _unique(tag_rows: list[Row]) -> Iterator[Row]:
    """Первая строка каждого имени — как в каталоге службы."""
    seen = set()
    for row in tag_rows:
        if row[0] not in seen:
            seen.add(row[0])
            yield row


def select_whitelist(tag_rows: list[Row], extra: frozenset[str], base: frozenset[str] | None = None) -> list[str]:
    """Имена по правилу d3-1 (tag_rows — ответ catalog_sql()), отсортированы."""
    return sorted(row[0] for row in _unique(tag_rows) if verdict(row[0], row[2], extra, base) == "ok")


def _tail_label(name: str) -> str:
    m = _TAIL.search(name)
    if m is None:
        return "без хвоста"
    tail = "_" + m.group(1).upper()
    return tail if tail in REPORT_TAILS else "прочие"


def summary_lines(tag_rows: list[Row], extra: frozenset[str], base: frozenset[str] | None,
                  selected: Sequence[str]) -> list[str]:
    """Счётчики построителя — только числа и общеизвестные хвосты, без имён тегов."""
    rows = list(_unique(tag_rows))
    names = {row[0] for row in rows}
    kinds = Counter(KIND_BY_TYPE.get(row[2], "other") for row in rows)
    verdicts = Counter(verdict(row[0], row[2], extra, base) for row in rows)
    by_service = Counter(service_tail(row[0]) for row in rows if verdict(row[0], row[2], extra, base) == "rule4")
    kind_of = {row[0]: KIND_BY_TYPE.get(row[2]) for row in rows}
    chosen = Counter(kind_of.get(n) for n in selected)
    tails = Counter(_tail_label(n) for n in selected)

    catalog = f"каталог: всего {len(rows)}, аналоговых {kinds['analog']}, дискретных {kinds['discrete']}"
    if kinds["other"]:
        catalog += f", прочих типов {kinds['other']}"
    lines = [catalog]
    if base is not None:
        lines.append(f"прежний список (--base): найдено {len(base & names)}, нет в каталоге {len(base - names)}")
    scope = "вне участка 20–25" if base is None else "нет в прежнем списке"
    lines += [
        f"whitelist.extra: найдено {len(extra & names)}, нет в каталоге {len(extra - names)}",
        f"отсечено правилом 1 (имя вне [A-Za-z0-9_]): {verdicts['rule1']}",
        f"не выбрано правилами 2–3 ({scope} и не в whitelist.extra): {verdicts['rule23']}",
        f"отсечено правилом 4 (служебные хвосты аналоговых): {verdicts['rule4']} — "
        + ", ".join(f"{tail} {by_service[tail]}" for tail in SERVICE_TAILS),
        f"отсечено правилом 5 (дискретные не состояния): {verdicts['rule5']}",
        f"белый список: {len(selected)} — аналоговых {chosen['analog']}, дискретных {chosen['discrete']}",
    ]
    order = [t for t in REPORT_TAILS if tails[t]] + [t for t in ("прочие", "без хвоста") if tails[t]]
    lines.append("по хвостам: " + (", ".join(f"{t} {tails[t]}" for t in order) or "—"))
    return lines


def _fsync_dir(directory: str) -> None:
    with contextlib.suppress(OSError):          # не всякая ФС умеет fsync каталога
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_list(path: str, names: Sequence[str], header: Mapping[str, str]) -> None:
    """Список в файл атомарно: временный файл рядом, права 0444, os.replace.

    Шапка — строки «# ключ: значение». Пустой список, недопустимое имя или строка шапки,
    которая разорвала бы файл, — ValueError до записи: старый файл цел.
    """
    if isinstance(names, str):
        raise TypeError("names — последовательность имён, не строка")
    names = list(names)
    if not names:
        raise ValueError("пустой список — файл не перезаписан")
    if any(not isinstance(n, str) or SAFE_NAME.fullmatch(n) is None for n in names):
        raise ValueError("имя вне набора [A-Za-z0-9_]{1,128} — файл не перезаписан")
    lines = []
    for key, value in header.items():
        # isprintable: ни \n, ни   и прочих разрывов строки, которые видит splitlines
        if not (isinstance(key, str) and _HEADER_KEY.fullmatch(key) and isinstance(value, str)
                and value.isprintable()):
            raise ValueError("шапка: ключ — [a-z][a-z0-9_]*, значение — одна строка")
        lines.append(f"# {key}: {value}")
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".whitelist-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines + names) + "\n")
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), FILE_MODE)
        os.replace(tmp, path)               # и поверх файла 0444: rename смотрит права каталога
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(directory)


def _load(path: str) -> frozenset[str]:
    try:
        return load_names(path)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None


def main(argv: list[str] | None = None) -> int:
    """0 — список записан; 1 — ошибка входных файлов, историана или пустой результат (файл цел)."""
    parser = argparse.ArgumentParser(prog="python -m pcbk_core.data.build_whitelist",
                                     description="Белый список тегов по правилу d3-1; печатает только числа.")
    parser.add_argument("--out", required=True, help="куда записать список (права 0444)")
    parser.add_argument("--extra", required=True, help="whitelist.extra — имена сверх участка")
    parser.add_argument("--base", help="прежний список: вместо правила участка — «имя есть в этом списке»")
    parser.add_argument("--env-file", default=Settings().BDRV_ENV_FILE, help="учётные данные историана")
    args = parser.parse_args(argv)
    setup_logging()                         # pytds — не ниже WARNING: на INFO он пишет адрес и SQL

    # входные файлы — до входа в историан: ошибка в них не тратит попытку входа общей учётки
    try:
        extra = _load(args.extra)
        base = _load(args.base) if args.base is not None else None
        cfg = BdrvConfig.from_env_file(args.env_file)
    except (OSError, ValueError) as e:
        print(f"построитель: {e}", file=sys.stderr)
        return 1
    try:
        rows = tds_query(cfg)([catalog_sql()], CATALOG_TIMEOUT_S)[0]
    except HistorianError as e:
        print(f"построитель: историан — {e.public()}", file=sys.stderr)   # detail — с учёткой, наружу нет
        return 1

    selected = select_whitelist(rows, extra, base)
    for line in summary_lines(rows, extra, base, selected):
        print(line)
    if not selected:
        print("построитель: пустой результат — файл не перезаписан", file=sys.stderr)
        return 1
    header = {"rule": RULE_VERSION, "section": "20-25" if base is None else "base",
              "built": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tags": str(len(selected))}
    try:
        write_list(args.out, selected, header)
    except (OSError, ValueError) as e:
        print(f"построитель: запись не удалась — {e}", file=sys.stderr)
        return 1
    print(f"записано: {args.out} — имён {len(selected)}, права 0444")
    return 0


if __name__ == "__main__":
    sys.exit(main())
