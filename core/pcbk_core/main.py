"""Вход серверного слоя: настройки, журнал, роли, uvicorn; --healthcheck для образа.

FastAPI и uvicorn грузятся только в ветке сервера: проба здоровья раз в 30 с
тянет лишь http.client.
"""
from __future__ import annotations

import http.client
import logging
import sys
from typing import TYPE_CHECKING

from .settings import Settings

if TYPE_CHECKING:
    from .app import Role

USAGE = "использование: python -m pcbk_core.main [--healthcheck]"


def _why(exc: Exception) -> str:
    """Причина для журнала без строк файла: у ValueError чтения файлов текст — номер строки, не она сама."""
    if isinstance(exc, UnicodeDecodeError):
        return "файл не в UTF-8"
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'ошибка чтения'}"
    return str(exc)


def build_roles(settings: Settings) -> list[Role]:
    """Роли процесса: в Д3а — «данные»; Д4 добавит LLM-прокси.

    Нет файла белого списка — роль с пустым списком: /healthz/data отвечает 503
    с причиной, ворота не пускают ни одного имени. bdrv.env или белый список не
    читаются — роль «с ошибкой настройки»: 503 с причиной, без фоновых циклов и
    входов в историан; процесс живёт, и сторож видит причину. Учётные данные —
    только файлом.
    """
    from .data import CONFIG_ERRORS, DataRole
    from .data.historian import HistorianError, QueryFn, tds_query
    from .data.names import load_names
    from .secrets import BdrvConfig

    log = logging.getLogger("pcbk_core.main")
    problems: list[str] = []

    def no_credentials(statements, remaining_s):
        raise HistorianError("connect", "учётные данные историана не прочитаны")

    query: QueryFn = no_credentials
    try:
        query = tds_query(BdrvConfig.from_env_file(settings.BDRV_ENV_FILE))
    except (OSError, ValueError) as exc:
        log.error("учётные данные %s: %s", settings.BDRV_ENV_FILE, _why(exc))
        problems.append(CONFIG_ERRORS["bdrv_unreadable" if isinstance(exc, OSError) else "bdrv_invalid"])

    whitelist: frozenset[str] = frozenset()
    try:
        whitelist = load_names(settings.WHITELIST_PATH)
    except FileNotFoundError:
        log.error("белый список не найден: %s — роль «данные» без тегов", settings.WHITELIST_PATH)
    except (OSError, ValueError) as exc:
        log.error("белый список %s: %s", settings.WHITELIST_PATH, _why(exc))
        problems.append(CONFIG_ERRORS["whitelist_unreadable"])

    return [DataRole(settings, query, whitelist, config_error="; ".join(problems) or None)]


def healthcheck(settings: Settings) -> int:
    """Для HEALTHCHECK образа: 0 — /healthz ответил 200, иначе 1."""
    conn = http.client.HTTPConnection("127.0.0.1", settings.CORE_PORT, timeout=4)
    try:
        conn.request("GET", "/healthz")
        return 0 if conn.getresponse().status == 200 else 1
    except (OSError, http.client.HTTPException):
        return 1
    finally:
        conn.close()


def serve(settings: Settings) -> None:
    import uvicorn

    from .app import create_app
    from .logs import setup_logging

    log = setup_logging()
    roles = build_roles(settings)
    log.info("серверный слой слушает порт %s, роли: %s",
             settings.CORE_PORT, ", ".join(r.name for r in roles) or "нет")
    # журнал uvicorn не настраивает (log_config=None), доступ не пишем: здоровье опрашивают часто
    uvicorn.run(create_app(settings, roles), host="0.0.0.0", port=settings.CORE_PORT,
                log_config=None, access_log=False)


def main(argv: list[str] | None = None) -> int:
    """Ошибка настроек или аргументов — код 1: код 2 Docker в HEALTHCHECK резервирует."""
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ["--healthcheck"]):
        print(USAGE, file=sys.stderr)
        return 1
    try:
        settings = Settings.from_env()
    except ValueError as e:
        print(f"pcbk-core: настройки: {e}", file=sys.stderr)
        return 1
    if argv == ["--healthcheck"]:
        return healthcheck(settings)
    serve(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
