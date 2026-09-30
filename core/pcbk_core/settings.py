"""Настройки серверного слоя из окружения: только несекретное, секреты — файлами."""
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from urllib.parse import urlsplit

# режимы учений свежести: "" — учений нет
DRILL_MODES = frozenset({"", "freeze_stamps", "freeze_poll"})
# два запаса ворот по 5 с: при меньшем сроке каталог не загрузится никогда
MIN_CATALOG_DEADLINE_S = 10.0


@dataclass(frozen=True)
class Settings:
    CORE_PORT: int = 8000
    BDRV_ENV_FILE: str = "/run/secrets/bdrv.env"
    WHITELIST_PATH: str = "/app/data/whitelist.txt"
    # хеши токенов вызывающих: строки «<id> <sha256 hex>», сами токены — только у вызывающих
    TOKENS_FILE: str = "/run/secrets/core-tokens"
    # журнал событий (SQLite) на томе pcbk-core-data
    DB_PATH: str = "/var/lib/pcbk-core/core.db"
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


# LLM-прокси (Д4а): отдельный класс — ошибка в LLM_* не роняет main() и службу данных (П8, F4 Д3б),
# её показывает роль llm как config_error (build_roles)
LLM_KEY_MODES = frozenset({"on", "off"})
LLM_STATES = frozenset({"on", "stopped"})
DRILL_LLM_MODES = frozenset({"", "402"})
# (поле, наименьшее, наибольшее) — целые пределы; предел шагов стоит в тексте отказа — не больше 100
# (OpenCode ищет в тексте 429|500|502|503|504|524)
_LLM_INT_RANGES = (("LLM_MAX_TOKENS", 1, 16000), ("LLM_TURN_CALLS", 1, 100),
                   ("LLM_MAX_BODY_BYTES", 65536, 4194304), ("LLM_CALLER_CONCURRENCY", 1, 16))
# наименьшее у дробных, кроме «больше нуля»: опрос OpenRouter не чаще раза в 5 с
_LLM_FLOAT_MIN = {"LLM_POLL_S": 5.0}
_LLM_CHOICES = (("LLM_KEY_DEDICATED", LLM_KEY_MODES), ("LLM_STATE", LLM_STATES), ("DRILL_LLM", DRILL_LLM_MODES))
# пути к файлам секретов роли llm: без проверок, их берёт и запасная настройка при ошибке в LLM_*
_LLM_FILE_FIELDS = ("OPENROUTER_KEY_FILE", "OPENROUTER_MGMT_KEY_FILE", "LLM_TOKENS_FILE")


@dataclass(frozen=True)
class LlmSettings:
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # ключи — только файлами (Global Constraints Д4а): пустой файл — «ключа нет»
    OPENROUTER_KEY_FILE: str = "/run/secrets/openrouter.key"
    OPENROUTER_MGMT_KEY_FILE: str = "/run/secrets/openrouter-mgmt.key"
    # хеши токенов LLM: строки «<id> <sha256 hex>», отдельная таблица от core-tokens
    LLM_TOKENS_FILE: str = "/run/secrets/llm-tokens"
    LLM_MODELS: str = ""                    # пусто — все семь кандидатов (select_models)
    LLM_TOTAL_USD: float = 1000.0
    LLM_SHARE_USD: float = 100.0            # больше общего бюджета — можно: тогда держит общий
    LLM_OPS_SHARE_USD: float = 100.0
    LLM_MAX_TOKENS: int = 16000
    LLM_TURN_CALLS: int = 16
    LLM_POLL_S: float = 60.0
    LLM_MAX_BODY_BYTES: int = 1048576
    LLM_CALLER_CONCURRENCY: int = 3
    LLM_KEY_DEDICATED: str = "on"           # ключ только у резервного контура: сверка с /key
    LLM_STATE: str = "on"                   # stopped — выключатель администратора
    DRILL_LLM: str = ""                     # 402 — учение «кончились деньги» без OpenRouter

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LlmSettings":
        """Пустое значение — умолчание; ошибка — ValueError с именем поля, но без самого значения: текст идёт
        в config_error и на /health/llm, а в переменную по ошибке может попасть что угодно. env — только он,
        иначе os.environ.

        select_models импортируется здесь, а не в модуле: import pcbk_core.main в пробе здоровья не тянет
        pcbk_core.llm, fastapi и httpx (П8).
        """
        from .llm.models import select_models

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
                raise ValueError(f"{f.name}: не {'целое ' if kind is int else ''}число") from None
            if kind is float:
                low = _LLM_FLOAT_MIN.get(f.name)
                if not (math.isfinite(value) and value > 0 and (low is None or value >= low)):
                    need = "больше нуля" if low is None else f"не меньше {low:g}"
                    raise ValueError(f"{f.name}: нужно число {need}")
            values[f.name] = value
        llm = cls(**values)
        for name, low, high in _LLM_INT_RANGES:
            value = getattr(llm, name)
            if value < low or (high is not None and value > high):
                raise ValueError(f"{name}: нужно целое от {low} до {high}")
        for name, allowed in _LLM_CHOICES:
            if getattr(llm, name) not in allowed:
                raise ValueError(f"{name}: допустимо " + " или ".join(repr(v) for v in sorted(allowed)))
        _check_base_url(llm.OPENROUTER_BASE_URL)
        try:
            select_models(llm.LLM_MODELS)
        except ValueError as exc:           # «LLM_MODELS: неизвестная модель «имя»» — имя не повторяется
            raise ValueError(re.sub(r"«[^»]*»", "«…»", str(exc))) from None
        return llm

    @classmethod
    def files_from_env(cls, env: Mapping[str, str] | None = None) -> "LlmSettings":
        """Запасная настройка при ошибке в LLM_*: умолчания, но пути к файлам секретов — из env. Так роль
        с config_error читает свои llm-tokens и ключи, а не пути по умолчанию (тесты не читают /run/secrets)."""
        env = os.environ if env is None else env
        return cls(**{name: env[name] for name in _LLM_FILE_FIELDS if env.get(name, "")})


# шлюз (Д5а): режимы учений; GW_ORIGIN — https://хост[:порт] без пути, хост строчными (Origin браузера —
# строчными, иначе он не совпал бы никогда); STU_NET — два октета
DRILL_GATEWAY_MODES = frozenset({"", "freeze_reaper", "bad_place_pw"})
_GW_ORIGIN = re.compile(r"https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::(?P<port>[0-9]{1,5}))?")
_STU_NET = re.compile(r"([0-9]{1,3})\.([0-9]{1,3})")


@dataclass(frozen=True)
class GatewaySettings:
    """Шлюз Д5: отдельный класс, как LlmSettings, — ошибка в GW_* не роняет main() и службу данных, её
    показывает роль gateway как config_error. Проверки — только from_env; тесты строят класс напрямую."""
    GW_ORIGIN: str = ""                       # https://хост[:порт] страницы входа; пусто — ни с чем не совпадает
    GW_IDLE_STOP_S: float = 1800.0            # простой до сна места (Д5-R28)
    GW_REAPER_TICK_S: float = 30.0            # такт уборщика
    GW_WAKE_TIMEOUT_S: float = 60.0           # срок готовности места после start (Д5-R27)
    GW_SESSION_TTL_S: float = 43200.0         # срок сессии входа
    GW_PLACE_PW_DIR: str = "/run/secrets"     # student-NN.pw — пароли мест, знает только шлюз
    SP_CTL_URL: str = "http://pcbk-sp-ctl:2375"
    STU_NET: str = "172.31"                   # первые два октета сетей мест: место N — {STU_NET}.N.0/28
    DRILL_GATEWAY: str = ""                   # учения: freeze_reaper, bad_place_pw

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewaySettings":
        """Пустое значение — умолчание; ошибка — ValueError с именем поля и правилом, но без самого значения
        (Ш9): текст идёт в config_error, на страницу состояния и в ответ 503, а в GW_ORIGIN по ошибке может
        попасть путь или токен. env — только он, иначе os.environ."""
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            raw = env.get(f.name, "")
            if raw == "":
                continue
            if type(f.default) is str:
                values[f.name] = raw
                continue
            try:
                value = float(raw)
            except ValueError:
                raise ValueError(f"{f.name}: не число") from None
            if not math.isfinite(value):
                raise ValueError(f"{f.name}: нужно конечное число")
            values[f.name] = value
        gw = cls(**values)
        origin = _GW_ORIGIN.fullmatch(gw.GW_ORIGIN) if gw.GW_ORIGIN else None
        port = None if origin is None else origin["port"]
        if gw.GW_ORIGIN and (origin is None or (port is not None and not 1 <= int(port) <= 65535)):
            raise ValueError("GW_ORIGIN: нужно пусто или https://хост[:порт] без пути, хост строчными")
        if gw.GW_IDLE_STOP_S < 60:
            raise ValueError("GW_IDLE_STOP_S: нужно не меньше 60 с")
        if not 0 < gw.GW_REAPER_TICK_S <= gw.GW_IDLE_STOP_S / 2:
            raise ValueError("GW_REAPER_TICK_S: нужно больше нуля и не больше половины GW_IDLE_STOP_S")
        if not 10 <= gw.GW_WAKE_TIMEOUT_S <= 300:
            raise ValueError("GW_WAKE_TIMEOUT_S: нужно от 10 до 300 с")
        if gw.GW_SESSION_TTL_S < 300:
            raise ValueError("GW_SESSION_TTL_S: нужно не меньше 300 с")
        try:
            sp_ctl = urlsplit(gw.SP_CTL_URL)
            sp_ctl.port                               # порт вне 0…65535 или не число — ValueError
        except ValueError:
            raise ValueError("SP_CTL_URL: адрес не разбирается") from None
        if sp_ctl.scheme != "http" or not sp_ctl.hostname:
            raise ValueError("SP_CTL_URL: нужен адрес http:// с хостом")
        m = _STU_NET.fullmatch(gw.STU_NET)
        if m is None or int(m[1]) > 255 or int(m[2]) > 255:
            raise ValueError("STU_NET: нужны два октета вида 172.31, каждый не больше 255")
        if gw.DRILL_GATEWAY not in DRILL_GATEWAY_MODES:
            raise ValueError("DRILL_GATEWAY: допустимо пусто, freeze_reaper или bad_place_pw")
        return gw


def _check_base_url(url: str) -> None:
    """http или https, есть хост, порт — число в пределах; иначе ValueError с именем поля."""
    try:
        parts = urlsplit(url)
        parts.port                                  # порт вне 0…65535 или не число — ValueError
    except ValueError:
        raise ValueError("OPENROUTER_BASE_URL: адрес не разбирается") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("OPENROUTER_BASE_URL: нужен адрес http:// или https:// с хостом")
