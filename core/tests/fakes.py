"""Двойники историана для тестов: без сервера, имена тегов — только синтетические, с FAKE."""
import re
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytds

from pcbk_core.data.historian import PRELUDE, Row

# (TagName, Description, TagType, MinEU, MaxEU, Unit)
CATALOG_ROWS = [
    ("20FAKE_001_PV", "Факт. знач. - расход массы", 1, 0.0, 100.0, "л/час"),
    ("20FAKE_002_SP", "Задание - расход массы", 1, 0.0, 100.0, "None"),
    ("20FAKE_003_PV", "Факт. знач. - уровень в ёмкости", 1, 0.0, 100.0, "None"),
    ("20FAKE_004_PV", "Факт. знач. - давление", 1, 0.0, 10.0, "мбар"),
    ("20FAKE_005_LMN", "Выход - регулятор расхода", 1, 0.0, 100.0, "%"),
    ("20FAKE_006_SP_HMI", "служебный", 1, 0.0, 1.0, "None"),
    ("25FAKE_007_CLS", "Клапан подачи закрыт", 2, None, None, None),
    ("25FAKE_008_DIAG", "диагностика привода", 2, None, None, None),
    ("16FAKE_009_PV", "вне участка", 1, 0.0, 1.0, "None"),
    ("QFAKE_010", "качество полотна", 1, 0.0, 1.0, "г/м2"),
    ("QFAKE_011_LMN", "качество полотна, выход", 1, 0.0, 1.0, "None"),
    ("$FAKE_SYS", "системный", 1, 0.0, 1.0, "None"),
    ("20FAKE.012.DIAG", "диагностика", 2, None, None, None),
]
EXTRA = frozenset({"QFAKE_010", "QFAKE_011_LMN", "QFAKE_GONE"})
WHITELIST = frozenset({"20FAKE_001_PV", "20FAKE_002_SP", "20FAKE_003_PV", "20FAKE_004_PV",
                       "25FAKE_007_CLS", "QFAKE_010"})
LIVE_ROWS = [   # TagName, DateTime, Value, Quality
    ("20FAKE_001_PV", datetime(2026, 10, 1, 11, 59, 18), 12.5, 0),
    ("20FAKE_002_SP", datetime(2026, 10, 1, 11, 50, 0), 12.0, 0),
    ("20FAKE_004_PV", datetime(2026, 10, 1, 11, 59, 50), 3.2, 64),
    ("25FAKE_007_CLS", datetime(2026, 10, 1, 11, 40, 0), 1.0, 0),
    ("16FAKE_009_PV", datetime(2026, 10, 1, 11, 59, 59), 0.5, 0),
]
# GETDATE(), GETUTCDATE(): историан в UTC+05:00
CLOCK_ROWS = [(datetime(2026, 10, 1, 12, 0, 0), datetime(2026, 10, 1, 7, 0, 0))]


def auth_operational_error(msg_no: int, text: str = "Login failed") -> pytds.OperationalError:
    """Отказ входа, как его отдаёт pytds: OperationalError с номером сообщения SQL Server."""
    exc = pytds.OperationalError(text)
    exc.msg_no = msg_no
    exc.text = text
    return exc


class _RecordingCursor:
    def __init__(self, rec: "RecordingConnect"):
        self._rec = rec
        self._pending: list[Row] | None = None

    def execute(self, *args) -> None:
        self._rec.executed.append(args)
        if self._rec.execute_delay_s:
            time.sleep(self._rec.execute_delay_s)
        if self._rec.fail_on_execute is not None:
            raise self._rec.fail_on_execute
        if args[0] == PRELUDE:
            self._pending = None
        else:
            self._pending = self._rec.results.pop(0) if self._rec.results else []

    def fetchall(self) -> list[Row]:
        if self._pending is None:           # как pytds после инструкции без строк
            raise pytds.ProgrammingError("Previous statement didn't produce any results")
        rows, self._pending = self._pending, None
        return rows


class _RecordingSocket:
    def __init__(self, rec: "RecordingConnect"):
        self._rec = rec

    def settimeout(self, timeout: float | None) -> None:
        self._rec.read_timeouts.append(timeout)


class _RecordingConn:
    def __init__(self, rec: "RecordingConnect"):
        self._rec = rec
        # как у pytds 1.17.1: соединение → _tds_socket → sock (предел чтения ставится на нём)
        self._tds_socket = SimpleNamespace(sock=_RecordingSocket(rec))

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self._rec)

    def close(self) -> None:
        self._rec.closed = True


class RecordingConnect:
    """Двойник pytds.connect: запоминает аргументы входа, инструкции и пределы чтения, отдаёт results по порядку.

    execute_delay_s — задержка каждой инструкции (срок вызова между инструкциями).
    """

    def __init__(self, results: list[list[Row]] | None = None, fail_on_connect: BaseException | None = None,
                 fail_on_execute: BaseException | None = None, execute_delay_s: float = 0.0):
        self.results = list(results or [])
        self.fail_on_connect = fail_on_connect
        self.fail_on_execute = fail_on_execute
        self.execute_delay_s = execute_delay_s
        self.read_timeouts: list[float | None] = []
        self.kwargs: dict | None = None
        self.connect_count = 0
        self.executed: list[tuple] = []
        self.closed = False

    def __call__(self, **kwargs) -> _RecordingConn:
        self.connect_count += 1
        self.kwargs = kwargs
        if self.fail_on_connect is not None:
            raise self.fail_on_connect
        return _RecordingConn(self)


# разбор SQL у двойника свой, не из pcbk_core.data.sql: иначе он проверял бы код сам собой
_LIT = re.compile(r"'((?:[^']|'')*)'")
_CLOCK = re.compile(r"SELECT GETDATE\(\) AS NowLocal, GETUTCDATE\(\) AS NowUtc")
_CATALOG = re.compile(r"SELECT t\.TagName, t\.Description, t\.TagType, a\.MinEU, a\.MaxEU, e\.Unit FROM Tag t "
                      r"LEFT JOIN AnalogTag a ON a\.TagName = t\.TagName "
                      r"LEFT JOIN EngineeringUnit e ON e\.EUKey = a\.EUKey WHERE t\.TagType IN \(1, 2\)")
_LIVE_ALL = re.compile(r"SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL")
_LIVE_IN = re.compile(r"SELECT TagName, DateTime, Value, Quality FROM Live WHERE TagName IN \('[^()]*'\)")


class FakeHistorian:
    """Вызываемый QueryFn: отвечает на шаблоны службы синтетикой, иной SQL — AssertionError.

    delay_s — задержка вызова (только если какая-то строка совпала с регуляркой slow;
    slow=None — на все вызовы); fail — исключение вместо ответа, после задержки.
    """

    def __init__(self, delay_s: float = 0.0, fail: BaseException | None = None, slow: str | None = None):
        self.delay_s = delay_s
        self.fail = fail
        self.slow = slow
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.max_concurrency = 0            # по полосам не делится
        self.live: dict[str, tuple[datetime, float | None, int]] = {r[0]: r[1:] for r in LIVE_ROWS}
        self.clock: list[Row] = list(CLOCK_ROWS)
        self._active = 0
        self._lock = threading.Lock()

    def __call__(self, statements, timeout: float) -> list[list[Row]]:
        statements = list(statements)
        with self._lock:
            self.calls.append(statements)
            self.timeouts.append(timeout)
            self._active += 1
            self.max_concurrency = max(self.max_concurrency, self._active)
        try:
            assert timeout > 0, "срок драйвера 0 у pytds — «без предела»"
            if self.delay_s and (self.slow is None or any(re.search(self.slow, s) for s in statements)):
                time.sleep(self.delay_s)
            if self.fail is not None:
                raise self.fail
            return [self._answer(s) for s in statements]
        finally:
            with self._lock:
                self._active -= 1

    def _answer(self, sql: str) -> list[Row]:
        if _CLOCK.fullmatch(sql):
            return list(self.clock)
        if _CATALOG.fullmatch(sql):
            return list(CATALOG_ROWS)
        if _LIVE_ALL.fullmatch(sql):
            return [(name, *rest) for name, rest in self.live.items() if rest[1] is not None]
        if _LIVE_IN.fullmatch(sql):
            names = [lit.replace("''", "'") for lit in _LIT.findall(sql)]
            return [(name, *self.live[name]) for name in names if name in self.live]
        raise AssertionError("двойник историана: SQL вне шаблонов службы")
