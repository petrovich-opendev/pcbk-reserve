"""Настройки серверного слоя из окружения: только несекретное, секреты — файлами."""
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields

# режимы учений свежести: "" — учений нет
DRILL_MODES = frozenset({"", "freeze_stamps", "freeze_poll"})
# два запаса ворот по 5 с: при меньшем сроке каталог не загрузится никогда
MIN_CATALOG_DEADLINE_S = 10.0


@dataclass(frozen=True)
class Settings:
    CORE_PORT: int = 8000
    BDRV_ENV_FILE: str = "/run/secrets/bdrv.env"
    WHITELIST_PATH: str = "/app/data/whitelist.txt"
    FRESH_POLL_S: float = 30.0
    CATALOG_REFRESH_S: float = 86400.0
    CATALOG_DEADLINE_S: float = 60.0
    # учения: freeze_stamps — метки не растут, freeze_poll — опрос встаёт после первого
    DRILL_FRESHNESS: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Пустое значение — умолчание; ошибка — ValueError с именем поля.

        Проверки только здесь: тесты строят Settings напрямую с малыми сроками.
        """
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            raw = env.get(f.name, "")
            if raw == "":
                continue
            kind = type(f.default)
            if kind is str:
                values[f.name] = raw
                continue
            try:
                value = kind(raw)
            except ValueError:
                raise ValueError(f"{f.name}: неверное значение {raw!r}") from None
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{f.name}: нужно число больше нуля, получено {raw!r}")
            values[f.name] = value
        settings = cls(**values)
        if settings.CORE_PORT > 65535:
            raise ValueError(f"CORE_PORT: нет такого порта {settings.CORE_PORT}")
        if settings.CATALOG_DEADLINE_S < MIN_CATALOG_DEADLINE_S:
            raise ValueError(f"CATALOG_DEADLINE_S: нужно не меньше {MIN_CATALOG_DEADLINE_S:g} с — "
                             "два запаса ворот по 5 с, иначе каталог не загрузится никогда")
        if settings.DRILL_FRESHNESS not in DRILL_MODES:
            raise ValueError(f"DRILL_FRESHNESS: неизвестный режим {settings.DRILL_FRESHNESS!r}, "
                             "допустимо: пусто, freeze_stamps, freeze_poll")
        return settings
