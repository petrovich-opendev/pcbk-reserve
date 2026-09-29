"""Журнал серверного слоя: INFO в stderr своим обработчиком, болтливые библиотеки — не ниже WARNING."""
import logging
import sys

LOGGER = "pcbk_core"
# pytds на INFO пишет адрес историана и начало SQL с именами тегов
QUIET_LOGGERS = ("pytds", "mcp")


class _StderrHandler(logging.StreamHandler):
    """Пишет в текущий sys.stderr: подмена потока (pytest, перенаправление) журнал не рвёт."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stderr


def setup_logging() -> logging.Logger:
    """Логгер pcbk_core; повторный вызов второго обработчика не добавляет."""
    log = logging.getLogger(LOGGER)
    log.setLevel(logging.INFO)
    log.propagate = False
    if not any(isinstance(h, _StderrHandler) for h in log.handlers):
        handler = _StderrHandler()
        # время — со смещением от UTC
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                               datefmt="%Y-%m-%dT%H:%M:%S%z"))
        log.addHandler(handler)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return log
