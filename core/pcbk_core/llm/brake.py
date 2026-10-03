"""Тормоз прогона в core (Д11-R117): второй, независимый от прогонщика тормоз нагрузки.

Работает только при LLM_RUN_* (без прогона тормоза нет вовсе). Раз в RUN_BRAKE_POLL_S роль llm зовёт RunBrake.check() в
потоке (asyncio.to_thread): чтение /proc/meminfo (MemAvailable, MemTotal, SwapTotal, SwapFree), /proc/vmstat (oom_kill) и
/proc/pressure/memory (full avg10) — значения хоста, core их видит из контейнера под runc. Причины защёлки
(BRAKE_REASONS):
- memory — свободно меньше порога LLM_RUN_MEM_FAIL_MIB;
- swap — занятый swap минус база не меньше LLM_RUN_SWAP_GROW_MIB или рост за RUN_BRAKE_SWAP_WINDOW_S не меньше того же
  порога; база — LLM_RUN_SWAP_BASE_MIB, при −1 — первая проверка с данными swap (хранится в llm_meta);
- psi — давление full avg10 выше LLM_RUN_PSI_FULL_MAX;
- oom — счётчик ядра oom_kill вырос от базы (первая проверка прогона, хранится в llm_meta) — сразу: убийство — факт;
- blind — «память не читается»: нет MemAvailable или oom_kill (оба файла обязательны), либо датчик, который в этом
  процессе уже давал данные (swap, давление), замолчал или ответил не по формату (решение контроллера Д11а: так тормоз
  согласован со строкой «Память сервера» сторожа, А1). Никогда не читавшийся необязательный датчик (swap, давление) —
  не причина: давление в json тогда null.

memory, swap, psi, blind защёлкиваются после BRAKE_HOLD_CHECKS проверок подряд с этим условием (один шумный отсчёт прогон
не роняет); проверка без данных датчика счёт его условия не двигает и не сбрасывает (счёт ведёт blind). oom — сразу.

Защёлка навсегда в пределах прогона: условие ушло — защёлка стоит. Причина, время защёлки, базы swap и oom_kill — в
llm_meta ключами brake:{id}:… (числа; причина — номер в BRAKE_REASONS). Роль при старте с тем же LLM_RUN_ID читает их
(load): перезапуск core защёлку не снимает, а собственное убийство core по памяти даёт oom по сохранённой базе. Запись
защёлки — с flush журнала до возврата (как точка отсчёта прогона): core, убитый сразу после защёлки, её не теряет.
Снимает защёлку новый LLM_RUN_ID или администратор командой

    docker exec pcbk-core python -m pcbk_core.llm.brake clear --run <id> --yes

— простой DELETE ключей brake:<id>:% своим соединением, без Ledger.open (идущие вызовы работающего core не трогаются), и
затем пересоздание core: работающий процесс держит мету в памяти.

Модуль не импортируется пакетом pcbk_core.llm при загрузке (только внутри методов роли): иначе python -m
pcbk_core.llm.brake давал бы RuntimeWarning runpy в stderr каждой команды.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import re
import sqlite3
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import Ledger

log = logging.getLogger("pcbk_core.llm.brake")

RUN_BRAKE_POLL_S = 5.0                  # период проверки; читается при каждом такте (тесты его меняют)
RUN_BRAKE_SWAP_WINDOW_S = 600.0         # окно роста swap, с
BRAKE_HOLD_CHECKS = 3                   # проверок подряд с условием до защёлки (кроме oom)
BRAKE_REASONS = ("memory", "swap", "psi", "oom", "blind")
# порядок выбора причины, если несколько условий дошли до защёлки в одной проверке
_HELD = ("memory", "swap", "psi", "blind")
# блок run_brake ответа /health/llm — ровно эти ключи (сторож сверяет BrakeInfo)
BRAKE_KEYS = frozenset({"state", "reason", "avail_mib", "total_mib", "fail_mib", "swap_base_mib", "swap_growth_mib",
                        "swap_grow_mib", "psi_full10", "psi_full_max", "oom_kills", "age_s", "halted_at"})
# ключи llm_meta: brake:{run_id}:{часть}
META_PREFIX = "brake:{id}:"
META_PARTS = ("reason", "halted_at", "swap_base_mib", "oom_base")
# числа, которые halt() принимает в снимок json
_NUMBERS = frozenset({"avail_mib", "total_mib", "swap_growth_mib", "psi_full10", "oom_kills"})
_RUN_ID_RE = re.compile(r"[a-z0-9-]{1,32}")         # как settings.RUN_ID_RE
_PSI_FULL = re.compile(r"\bavg10=([0-9]+(?:\.[0-9]+)?)")


# --- чтение /proc: ни одно не бросает, не прочитано — None ---

@dataclass(frozen=True)
class MemInfo:
    """Нужное из /proc/meminfo, МиБ: total_mib и swap_used_mib — None, если полей нет."""
    avail_mib: int
    total_mib: int | None
    swap_used_mib: int | None


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii", errors="replace")
    except (OSError, ValueError):
        return None


def _kb_fields(text: str) -> dict[str, int]:
    """«Имя: число kB» по строкам; нечисловое значение пропускается."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, found, rest = line.partition(":")
        if not found:
            continue
        try:
            out[key.strip()] = int(rest.split()[0])
        except (IndexError, ValueError):
            pass
    return out


def read_meminfo(path: str) -> MemInfo | None:
    """/proc/meminfo: без MemAvailable или файл не читается — None."""
    text = _read(path)
    if text is None:
        return None
    kb = _kb_fields(text)
    if "MemAvailable" not in kb:
        return None
    total = kb.get("MemTotal")
    swap = max(0, kb["SwapTotal"] - kb["SwapFree"]) // 1024 if {"SwapTotal", "SwapFree"} <= kb.keys() else None
    return MemInfo(max(0, kb["MemAvailable"]) // 1024, None if total is None else max(0, total) // 1024, swap)


def read_oom_kill(path: str) -> int | None:
    """Счётчик ядра oom_kill из /proc/vmstat: все убийства по памяти — глобальные и по лимиту cgroup."""
    text = _read(path)
    if text is None:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "oom_kill":
            try:
                value = int(parts[1])
            except ValueError:
                return None
            return value if value >= 0 else None
    return None


def read_psi_full(path: str) -> float | None:
    """full avg10 из /proc/pressure/memory, %."""
    text = _read(path)
    if text is None:
        return None
    for line in text.splitlines():
        kind, _, rest = line.strip().partition(" ")
        if kind == "full" and (m := _PSI_FULL.search(rest)):
            value = float(m.group(1))
            return value if math.isfinite(value) else None
    return None


# --- тормоз ---

class RunBrake:
    """Тормоз прогона: paths — (meminfo, vmstat, pressure), thresholds — (свободно, МиБ; рост swap, МиБ; база swap, МиБ,
    −1 — первая проверка; давление full, %). meta — журнал вызовов (Ledger: meta_get, meta_set, flush), run_id — id
    прогона. Конструктор без ввода-вывода: мету читает из памяти журнала (load); роль зовёт load() ещё раз после открытия
    журнала в lifespan.

    check() и halt() — в потоке (чтение файлов, flush журнала); halted — чтение флага без замка (синхронный участок
    отказа до open_call, Д4а, не нарушается); json() — снимок под замком. event — threading.Event защёлки: ставится и
    из потока проверки, шлюз опрашивает его без привязки к циклу событий."""

    def __init__(self, paths: tuple[str, str, str], thresholds: tuple[int, int, int, float], *, meta: "Ledger",
                 run_id: str, monotonic: Callable[[], float], wallclock: Callable[[], datetime],
                 started_mono: float):
        self.meminfo_path, self.vmstat_path, self.pressure_path = paths
        self.fail_mib, self.swap_grow_mib, self.swap_base_cfg, self.psi_full_max = thresholds
        self.meta = meta
        self.run_id = run_id
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.started_mono = started_mono
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._halted_at: datetime | None = None
        self._checked_mono: float | None = None
        self._streak = dict.fromkeys(_HELD, 0)
        self._seen: set[str] = set()                     # датчики, давшие данные в этом процессе
        self._swap_base: int | None = self.swap_base_cfg if self.swap_base_cfg >= 0 else None
        self._oom_base: int | None = None
        self._swap_window: deque[tuple[float, int]] = deque()
        self._numbers: dict[str, float | int | None] = dict.fromkeys(_NUMBERS)
        self.load()

    # --- мета ---

    def _key(self, part: str) -> str:
        return META_PREFIX.format(id=self.run_id) + part

    def load(self) -> None:
        """Защёлка и базы прогона из llm_meta (память журнала, без ввода-вывода). Причина вне BRAKE_REASONS — защёлка
        с причиной blind: при сомнении закрыто."""
        get = self.meta.meta_get
        code, at = get(self._key("reason")), get(self._key("halted_at"))
        swap_base, oom_base = get(self._key("swap_base_mib")), get(self._key("oom_base"))
        with self._lock:
            if self.swap_base_cfg < 0 and swap_base is not None and swap_base >= 0:
                self._swap_base = int(swap_base)
            if oom_base is not None and oom_base >= 0:
                self._oom_base = int(oom_base)
            if code is None or self._reason is not None:
                return
            index = int(code) if math.isfinite(code) and code == int(code) else -1
            self._reason = BRAKE_REASONS[index] if 0 <= index < len(BRAKE_REASONS) else "blind"
            try:
                self._halted_at = (datetime.fromtimestamp(at, timezone.utc) if at is not None
                                   else self.wallclock())
            except (OverflowError, OSError, ValueError):
                self._halted_at = self.wallclock()
            reason = self._reason
        self.event.set()
        log.warning("llm brake latched run=%s reason=%s source=meta", self.run_id, reason)

    def _flush(self, what: str) -> None:
        """Мета — на диск до возврата (как точка отсчёта прогона). Не дождались или запись брошена — строка журнала;
        защёлка в памяти процесса стоит всё равно."""
        try:
            self.meta.flush(2.0)
        except TimeoutError as exc:                     # и LedgerWriteLost
            log.error("llm brake run=%s %s not on disk (%s)", self.run_id, what, type(exc).__name__)

    # --- признаки ---

    @property
    def halted(self) -> bool:
        return self._reason is not None

    @property
    def reason(self) -> str | None:
        return self._reason

    # --- проверка ---

    def check(self) -> None:
        """Одна проверка: чтение файлов, причины, счёт подряд, защёлка (с flush меты). Ввод-вывод — в потоке."""
        self.observe(read_meminfo(self.meminfo_path), read_oom_kill(self.vmstat_path),
                     read_psi_full(self.pressure_path))

    def failed(self) -> None:
        """Проверка упала исключением (роль пишет класс в журнал): считается проверкой без данных — blind."""
        self.observe(None, None, None)

    def observe(self, mem: MemInfo | None, oom: int | None, psi: float | None) -> None:
        """Решение по прочитанным значениям одной проверки (check без файлов — для роли и тестов)."""
        now = self.monotonic()
        latch: tuple[str, dict] | None = None
        new_bases: list[tuple[str, int]] = []
        with self._lock:
            self._checked_mono = now
            swap = None if mem is None else mem.swap_used_mib
            present = {"meminfo": mem is not None, "swap": swap is not None, "vmstat": oom is not None,
                       "pressure": psi is not None}
            silent = [name for name in ("swap", "pressure") if not present[name] and name in self._seen]
            self._seen.update(name for name, ok in present.items() if ok)
            blind = mem is None or oom is None or bool(silent)
            # базы: swap — настройка или первая проверка с данными; oom_kill — первая проверка, меньше базы — новая база
            if swap is not None:
                if self.swap_base_cfg >= 0:
                    self._swap_base = self.swap_base_cfg
                elif self._swap_base is None:
                    self._swap_base = swap
                    new_bases.append(("swap_base_mib", swap))
                self._swap_window.append((now, swap))
            while self._swap_window and now - self._swap_window[0][0] > RUN_BRAKE_SWAP_WINDOW_S:
                self._swap_window.popleft()
            if oom is not None and (self._oom_base is None or oom < self._oom_base):
                self._oom_base = oom
                new_bases.append(("oom_base", oom))
            growth = None if swap is None or self._swap_base is None else max(0, swap - self._swap_base)
            window = [v for t, v in self._swap_window]
            window_growth = max(0, window[-1] - min(window)) if swap is not None and window else 0
            kills = None if oom is None or self._oom_base is None else max(0, oom - self._oom_base)
            self._numbers.update(avail_mib=None if mem is None else mem.avail_mib,
                                 total_mib=None if mem is None else mem.total_mib,
                                 swap_growth_mib=growth, psi_full10=psi, oom_kills=kills)
            # условия: None — у проверки нет данных датчика, счёт этой причины стоит
            conditions = {
                "memory": None if mem is None else mem.avail_mib < self.fail_mib,
                "swap": None if growth is None else (growth >= self.swap_grow_mib
                                                     or window_growth >= self.swap_grow_mib),
                "psi": None if psi is None else psi > self.psi_full_max,
                "blind": blind,
            }
            for reason, hit in conditions.items():
                if hit is None:
                    continue
                self._streak[reason] = self._streak[reason] + 1 if hit else 0
            if self._reason is None:
                if kills:
                    latch = ("oom", {})
                else:
                    held = next((r for r in _HELD if self._streak[r] >= BRAKE_HOLD_CHECKS), None)
                    if held is not None:
                        latch = (held, {})
        for part, value in new_bases:
            self.meta.meta_set(self._key(part), value)
        if new_bases and latch is None:
            self._flush("base")
        if latch is not None:
            self.halt(latch[0], **latch[1])

    def halt(self, reason: str, **numbers: float | int | None) -> None:
        """Защёлка по причине reason (код BRAKE_REASONS); numbers — числа в снимок json (avail_mib, …). Уже стоит —
        ничего. Причина и время — в llm_meta с flush до возврата; затем event и строка журнала."""
        if reason not in BRAKE_REASONS:
            raise ValueError("причина тормоза вне BRAKE_REASONS")
        unknown = set(numbers) - _NUMBERS
        if unknown:
            raise TypeError("неизвестное число тормоза")
        with self._lock:
            if self._reason is not None:
                return
            self._numbers.update(numbers)
            at = self.wallclock()
            self._reason, self._halted_at = reason, at
            snapshot = dict(self._numbers)
        self.meta.meta_set(self._key("reason"), float(BRAKE_REASONS.index(reason)))
        self.meta.meta_set(self._key("halted_at"), at.timestamp())
        self._flush("latch")
        self.event.set()
        log.error("llm brake halted run=%s reason=%s avail_mib=%s swap_growth_mib=%s psi_full10=%s oom_kills=%s",
                  self.run_id, reason, *(snapshot[k] for k in ("avail_mib", "swap_growth_mib", "psi_full10",
                                                               "oom_kills")))

    def clear(self) -> None:
        """Снять защёлку в памяти процесса (счёт подряд — с нуля). llm_meta не трогает: на сервере защёлку снимают
        clear_brake (команда clear) и пересоздание core."""
        with self._lock:
            self._reason = self._halted_at = None
            self._streak = dict.fromkeys(_HELD, 0)
        self.event.clear()

    # --- /health/llm ---

    def json(self, now_mono: float) -> dict:
        """Блок run_brake: ровно BRAKE_KEYS. age_s — от последней проверки, до первой — от запуска роли; один знак."""
        with self._lock:
            since = self.started_mono if self._checked_mono is None else self._checked_mono
            n = self._numbers
            return {
                "state": "halted" if self._reason is not None else "watching",
                "reason": self._reason,
                "avail_mib": n["avail_mib"],
                "total_mib": n["total_mib"],
                "fail_mib": self.fail_mib,
                "swap_base_mib": self._swap_base,
                "swap_growth_mib": n["swap_growth_mib"],
                "swap_grow_mib": self.swap_grow_mib,
                "psi_full10": n["psi_full10"],
                "psi_full_max": self.psi_full_max,
                "oom_kills": n["oom_kills"],
                "age_s": round(max(0.0, now_mono - since), 1),
                "halted_at": None if self._halted_at is None else self._halted_at.isoformat(),
            }


# --- команда администратора: python -m pcbk_core.llm.brake clear --run <id> --yes ---

def clear_brake(db_path: str, run_id: str) -> int:
    """DELETE ключей brake:<run_id>:% из llm_meta своим соединением, без Ledger.open: идущие вызовы работающего core
    (строки open) не помечаются lost. Файла нет — ошибка (mode=rw: пустую базу не заводит). Возврат — удалено ключей."""
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("--run: только a-z, 0-9 и дефис, до 32 знаков")
    uri = f"{Path(db_path).absolute().as_uri()}?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    try:
        cur = db.execute("DELETE FROM llm_meta WHERE key LIKE ?", (META_PREFIX.format(id=run_id) + "%",))
        return cur.rowcount
    finally:
        db.close()


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Ошибка разбора — код 1 и одна строка в stderr, как у команды учёток."""

    def error(self, message: str):
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m pcbk_core.llm.brake",
                description="Тормоз прогона (Д11-R117): снять защёлку. После — пересоздать core.")
    sub = p.add_subparsers(dest="command", required=True, metavar="команда")
    clear = sub.add_parser("clear", help="удалить защёлку и базы прогона из llm_meta")
    clear.add_argument("--run", required=True, help="id прогона (LLM_RUN_ID)")
    clear.add_argument("--yes", action="store_true")
    clear.add_argument("--db", default=None, help="core.db; по умолчанию DB_PATH окружения")
    return p


def _fail(message: str) -> int:
    print(message.replace("\r", " ").replace("\n", " "), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Удача — код 0, в stdout число удалённых ключей, строка журнала «llm brake cleared»; ошибка — код 1 и одна строка
    в stderr. База — --db, иначе DB_PATH окружения, иначе Settings.DB_PATH."""
    try:
        args = _parser().parse_args(argv)
    except _UsageError as exc:
        return _fail(f"команда: {exc}")
    if not args.yes:
        return _fail("clear снимает защёлку тормоза прогона — повторите с --yes")
    from ..settings import Settings

    db_path = args.db or os.environ.get("DB_PATH") or Settings.DB_PATH
    try:
        removed = clear_brake(db_path, args.run)
    except ValueError as exc:
        return _fail(str(exc))
    except sqlite3.Error as exc:
        return _fail(f"core.db не открывается: {type(exc).__name__}")
    log.warning("llm brake cleared run=%s keys=%d — пересоздайте core", args.run, removed)
    print(removed)
    return 0


if __name__ == "__main__":
    from pcbk_core.logs import setup_logging

    setup_logging()
    sys.exit(main())
