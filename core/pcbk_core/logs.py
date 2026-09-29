"""Журнал серверного слоя: INFO в stderr своим обработчиком, болтливые библиотеки — не ниже WARNING.

У корневого логгера — тот же обработчик на WARNING: без него корень настроил бы FastMCP
(logging.basicConfig — голый «%(message)s» без времени и имени). Обе записи — одной строкой не
длиннее RECORD_CHARS: переводы строк экранируются (подделка строк журнала именем инструмента с \\n),
трассы исключений сводятся к классу и первой строке (многострочный дамп pydantic несёт ввод).
"""
import copy
import logging
import sys

LOGGER = "pcbk_core"
# pytds на INFO пишет адрес историана и начало SQL с именами тегов
QUIET_LOGGERS = ("pytds", "mcp")
RECORD_CHARS = 500
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%dT%H:%M:%S%z"         # время — со смещением от UTC


class _StderrHandler(logging.StreamHandler):
    """Пишет в текущий sys.stderr: подмена потока (pytest, перенаправление) журнал не рвёт."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stderr


class OneLine(logging.Filter):
    """Запись одной строкой не длиннее RECORD_CHARS: \\r и \\n — экранами, исключение — «класс: первая
    строка текста», стек — не пишется.

    Возвращает копию записи (Python 3.12): общий объект другим обработчикам (caplog) не правится.
    """

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            try:
                first = str(exc).splitlines()[0] if str(exc) else ""
            except Exception:
                first = ""
            text += f" | {type(exc).__name__}" + (f": {first}" if first else "")
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > RECORD_CHARS:
            text = text[:RECORD_CHARS - 1] + "…"
        line = copy.copy(record)
        line.msg, line.args = text, None
        line.exc_info = line.exc_text = line.stack_info = None
        return line


def _handler(level: int) -> _StderrHandler:
    handler = _StderrHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATEFMT))
    handler.addFilter(OneLine())
    return handler


def setup_logging() -> logging.Logger:
    """Логгер pcbk_core (INFO, без передачи в корень) и корень (WARNING); повторный вызов второго
    обработчика не добавляет."""
    log = logging.getLogger(LOGGER)
    log.setLevel(logging.INFO)
    log.propagate = False
    if not any(isinstance(h, _StderrHandler) for h in log.handlers):
        log.addHandler(_handler(logging.NOTSET))
    root = logging.getLogger()
    if not any(isinstance(h, _StderrHandler) for h in root.handlers):
        root.addHandler(_handler(logging.WARNING))
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return log
