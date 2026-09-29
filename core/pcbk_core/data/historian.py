"""Соединение с историаном через python-tds и часы историана.

Одно соединение на вызов: вход, прелюдия, инструкции по одной, закрытие.
Срок — на весь вызов: вход не дольше остатка, перед каждой инструкцией предел
чтения сокета — остаток срока, после срока следующая инструкция не уходит.
Текст ошибок SQL Server бывает с учётной записью («Login failed for user …») и
с началом SQL, поэтому он живёт только в HistorianError.detail и из процесса не
выходит; исключение pytds к HistorianError не цепляется ни причиной, ни
контекстом.
"""
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import pytds

from ..secrets import BdrvConfig

Row = tuple
# несколько SQL на одном соединении; второй аргумент — остаток срока в секундах
QueryFn = Callable[[Sequence[str], float], list[list[Row]]]

PRELUDE = "SET LOCK_TIMEOUT 5000; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;"
ErrorCode = Literal["connect", "timeout", "query", "auth"]
# отказ входа по учётным данным: общая с Dify учётка, повторы её заблокируют
AUTH_MSG_NOS = frozenset({18452, 18456, 18486, 18487, 18488})
LOGIN_TIMEOUT_S = 10
READ_TIMEOUT_S = 45
APPNAME = "pcbk-core"
DETAIL_CHARS = 200
OFFSET_STEP_S = 900          # пояс историана — с точностью до 15 минут
MAX_OFFSET = timedelta(hours=14)


class HistorianError(Exception):
    """Сбой обращения к историану.

    str() и repr() — только код; detail (класс, msg_no и начало текста) — для процесса,
    в журнал и события идёт public(): код, класс и msg_no.
    """

    def __init__(self, code: ErrorCode, detail: str, sent: tuple[str, ...] = (), *,
                 kind: str = "", msg_no: int | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.sent = tuple(sent)
        self.kind = kind
        self.msg_no = msg_no

    def public(self) -> str:
        parts = [self.code]
        if self.kind:
            parts.append(self.kind)
        if self.msg_no is not None:
            parts.append(f"msg_no={self.msg_no}")
        return " ".join(parts)

    def with_sent(self, sent: tuple[str, ...]) -> "HistorianError":
        return HistorianError(self.code, self.detail, sent, kind=self.kind, msg_no=self.msg_no)


def _msg_no(exc: BaseException) -> int | None:
    n = getattr(exc, "msg_no", None)
    return n if isinstance(n, int) and n else None


def _error(code: ErrorCode, exc: BaseException) -> HistorianError:
    kind, msg_no = type(exc).__name__, _msg_no(exc)
    text = str(getattr(exc, "text", "") or exc)[:DETAIL_CHARS]
    return HistorianError(code, f"{kind} msg_no={msg_no}: {text}", kind=kind, msg_no=msg_no)


def _login_code(exc: BaseException) -> ErrorCode:
    if isinstance(exc, pytds.LoginError):
        return "auth"
    if isinstance(exc, pytds.OperationalError) and _msg_no(exc) in AUTH_MSG_NOS:
        return "auth"
    return "connect"            # в том числе встроенный TimeoutError: вход не успел — это связь


def _read_code(exc: BaseException) -> ErrorCode:
    if isinstance(exc, TimeoutError):           # socket.timeout — тот же класс
        return "timeout"
    if isinstance(exc, OSError):
        return "connect"
    return "query"              # pytds.Error и всё прочее при чтении


def _set_read_timeout(conn: Any, seconds: float) -> None:
    """Предел чтения сокета на следующую инструкцию.

    Публичного пути у pytds 1.17.1 нет: сокет — conn._tds_socket.sock, его же
    перенастраивает сам pytds при откате транзакции. Путь сверяет тест
    test_read_timeout_path_matches_pinned_pytds.
    """
    conn._tds_socket.sock.settimeout(seconds)


def tds_query(cfg: BdrvConfig, connect: Callable[..., Any] = pytds.connect) -> QueryFn:
    def query(statements: Sequence[str], remaining_s: float) -> list[list[Row]]:
        # timeout=0 у pytds — «без предела»: исчерпанный срок не входит вовсе
        if not (remaining_s > 0 and math.isfinite(remaining_s)):
            raise HistorianError("timeout", "срок вызова исчерпан до входа")
        end = time.monotonic() + remaining_s
        kwargs: dict[str, Any] = dict(
            dsn=cfg.host, database=cfg.database, user=cfg.user, password=cfg.password,
            autocommit=True, login_timeout=min(LOGIN_TIMEOUT_S, remaining_s),
            timeout=min(READ_TIMEOUT_S, remaining_s), appname=APPNAME,
            # иначе pytds внутри одного connect делает до 4 попыток входа
            disable_connect_retry=True,
        )
        if cfg.port is not None:            # None — в адресе экземпляр, порт находит pytds
            kwargs["port"] = cfg.port
        # ошибка собирается внутри except, а бросается после блока: иначе исключение pytds
        # с текстом SQL Server осталось бы в __context__
        failure: HistorianError | None = None
        try:
            conn = connect(**kwargs)
        except Exception as exc:
            failure = _error(_login_code(exc), exc)
        if failure is not None:
            raise failure
        out: list[list[Row]] = []
        try:
            cur = conn.cursor()
            # PRELUDE строк не даёт: fetchall после неё — ошибка pytds
            for n, sql in enumerate((PRELUDE, *statements)):
                left = end - time.monotonic()
                if left <= 0:
                    failure = HistorianError("timeout", "срок вызова истёк между инструкциями")
                    break
                _set_read_timeout(conn, min(READ_TIMEOUT_S, left))
                cur.execute(sql)            # одним аргументом: без параметров драйвера
                if n:
                    out.append(cur.fetchall())
        except Exception as exc:
            failure = _error(_read_code(exc), exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if failure is not None:
            raise failure
        return out

    return query


@dataclass(frozen=True)
class HistClock:
    """Часы историана: GETDATE() и GETUTCDATE() одного запроса."""

    local: datetime
    utc: datetime

    @property
    def offset(self) -> timedelta:
        seconds = (self.local - self.utc).total_seconds()
        return timedelta(seconds=round(seconds / OFFSET_STEP_S) * OFFSET_STEP_S)

    @property
    def tz(self) -> timezone:
        return timezone(self.offset)

    @property
    def tz_label(self) -> str:
        minutes = int(self.offset.total_seconds()) // 60
        sign = "+" if minutes >= 0 else "-"
        hours, minutes = divmod(abs(minutes), 60)
        return f"UTC{sign}{hours:02d}:{minutes:02d}"

    def aware(self, dt: datetime) -> datetime:
        """Метка историана (pytds отдаёт её без пояса) — с поясом историана."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)


def parse_clock(rows: list[Row]) -> HistClock:
    if len(rows) != 1 or len(rows[0]) != 2 or not all(isinstance(v, datetime) for v in rows[0]):
        raise ValueError("часы историана: нужна одна строка (GETDATE, GETUTCDATE)")
    clock = HistClock(local=rows[0][0], utc=rows[0][1])
    if abs(clock.offset) > MAX_OFFSET:
        raise ValueError("часы историана: сдвиг от UTC больше 14 часов")
    return clock
