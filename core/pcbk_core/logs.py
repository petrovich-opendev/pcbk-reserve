"""Журнал серверного слоя: INFO в stderr своим обработчиком, болтливые библиотеки — не ниже WARNING.

У корневого логгера — тот же обработчик на WARNING: без него корень настроил бы FastMCP
(logging.basicConfig — голый «%(message)s» без времени и имени). Обе записи — одной строкой не
длиннее RECORD_CHARS: переводы строк экранируются (подделка строк журнала именем инструмента с \\n),
трассы исключений сводятся к классу, первой строке и месту (многострочный дамп pydantic несёт ввод).
"""
import copy
import logging
import sys

LOGGER = "pcbk_core"
# pytds на INFO пишет адрес историана и начало SQL с именами тегов
QUIET_LOGGERS = ("pytds", "mcp")
RECORD_CHARS = 500
EXC_TEXT_CHARS = 200             # первая строка текста исключения в записи
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%dT%H:%M:%S%z"         # время — со смещением от UTC


class _StderrHandler(logging.StreamHandler):
    """Пишет в текущий sys.stderr: подмена потока (pytest, перенаправление) журнал не рвёт."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stderr


def _flat(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(0, limit - 1)] + "…"


def _exc_tail(exc_info) -> str:
    """« | Класс: первая строка текста @ файл:строка» — место самого глубокого кадра трассы, без его
    переменных; нет исключения — пусто."""
    if not isinstance(exc_info, tuple) or len(exc_info) != 3 or exc_info[1] is None:
        return ""
    exc, tb = exc_info[1], exc_info[2]
    try:
        text = str(exc)
        first = text.splitlines()[0] if text else ""
    except Exception:
        first = ""
    tail = f" | {type(exc).__name__}" + (f": {_cut(first, EXC_TEXT_CHARS)}" if first else "")
    if tb is not None:
        while tb.tb_next is not None:
            tb = tb.tb_next
        tail += f" @ {tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}"
    return _flat(tail)


class OneLine(logging.Filter):
    """Запись одной строкой не длиннее RECORD_CHARS: \\r и \\n — экранами, исключение — «класс: первая
    строка текста @ файл:строка», стек — не пишется. Длинный текст режется так, чтобы хвост исключения
    остался целым.

    Кривой вызов журнала (warning("%d", "x")) даёт запись «запись не собрана (Класс)»: фильтр Handler.handle
    зовёт вне try, исключение отсюда ушло бы вызывающему и молча убило бы фоновый цикл.

    Возвращает копию записи (Python 3.12): общий объект другим обработчикам (caplog) не правится.
    """

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        try:
            text = record.getMessage()
        except Exception as err:
            text = f"запись не собрана ({type(err).__name__})"
        tail = _exc_tail(record.exc_info)
        text = _cut(_cut(_flat(text), RECORD_CHARS - len(tail)) + tail, RECORD_CHARS)
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
