"""Вход серверного слоя: настройки, журнал, роли, uvicorn; --healthcheck для образа."""
import http.client
import sys

import uvicorn

from .app import Role, create_app
from .logs import setup_logging
from .settings import Settings


def build_roles(settings: Settings) -> list[Role]:
    """Роли процесса; задача 5 добавит DataRole."""
    return []


def healthcheck(settings: Settings) -> int:
    """Для HEALTHCHECK образа: 0 — /healthz ответил 200."""
    conn = http.client.HTTPConnection("127.0.0.1", settings.CORE_PORT, timeout=4)
    try:
        conn.request("GET", "/healthz")
        return 0 if conn.getresponse().status == 200 else 1
    except (OSError, http.client.HTTPException):
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        settings = Settings.from_env()
    except ValueError as e:
        print(f"pcbk-core: настройки: {e}", file=sys.stderr)
        return 2
    if argv == ["--healthcheck"]:
        return healthcheck(settings)
    if argv:
        print("использование: python -m pcbk_core.main [--healthcheck]", file=sys.stderr)
        return 2
    log = setup_logging()
    roles = build_roles(settings)
    log.info("серверный слой слушает порт %s, роли: %s",
             settings.CORE_PORT, ", ".join(r.name for r in roles) or "нет")
    # журнал uvicorn не настраивает (log_config=None), доступ не пишем: здоровье опрашивают часто
    uvicorn.run(create_app(settings, roles), host="0.0.0.0", port=settings.CORE_PORT,
                log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
