"""Настройки серверного слоя из окружения: только несекретное, секреты — файлами."""
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from urllib.parse import urlsplit

# режимы учений свежести: "" — учений нет
DRILL_MODES = frozenset({"", "freeze_stamps", "freeze_poll"})
# учения службы данных (Д9-R83): tool_error — все инструменты чтения отвечают ошибкой без SQL; "" — учений нет
DRILL_DATA_MODES = frozenset({"", "tool_error"})
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
    # учения службы данных: tool_error — инструменты чтения отвечают ошибкой, SQL не уходит (Д9-R83)
    DRILL_DATA: str = ""
    # агенты студентов (Д7-R53): ${AGENTS_DIR} хоста на запись; student-NN — каталоги мест (их точки монтирования),
    # quarantine и archive — в корне, вне каталогов мест
    AGENTS_ROOT: str = "/srv/agents"
    # пакет уроков (Д15а): ${LESSONS_DIR} хоста на чтение (core только читает); lessons.json, <id>.md, img/<имя>
    LESSONS_ROOT: str = "/srv/lessons"

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
        if settings.DRILL_DATA not in DRILL_DATA_MODES:
            raise ValueError(f"DRILL_DATA: неизвестный режим {settings.DRILL_DATA!r}, допустимо: пусто, tool_error")
        return settings


# LLM-прокси (Д4а): отдельный класс — ошибка в LLM_* не роняет main() и службу данных (П8, F4 Д3б),
# её показывает роль llm как config_error (build_roles)
LLM_KEY_MODES = frozenset({"on", "off"})
LLM_STATES = frozenset({"on", "stopped"})
DRILL_LLM_MODES = frozenset({"", "402"})
# (поле, наименьшее, наибольшее) — целые пределы; предел шагов стоит в тексте отказа — не больше 100
# (OpenCode ищет в тексте 429|500|502|503|504|524)
_LLM_INT_RANGES = (("LLM_MAX_TOKENS", 1, 16000), ("LLM_TURN_CALLS", 1, 100),
                   ("LLM_MAX_BODY_BYTES", 65536, 4194304), ("LLM_CALLER_CONCURRENCY", 1, 16),
                   # тормоз прогона (Д11-R117), МиБ: порог свободной памяти и роста swap; база swap −1 — от первой проверки
                   ("LLM_RUN_MEM_FAIL_MIB", 1, 1048576), ("LLM_RUN_SWAP_GROW_MIB", 1, 1048576),
                   ("LLM_RUN_SWAP_BASE_MIB", -1, 1048576))
# наименьшее у дробных, кроме «больше нуля»: опрос OpenRouter не чаще раза в 5 с
_LLM_FLOAT_MIN = {"LLM_POLL_S": 5.0}
# наибольшее у дробных: давление памяти full avg10 — проценты (Д11-R117)
_LLM_FLOAT_MAX = {"LLM_RUN_PSI_FULL_MAX": 100.0}
_LLM_CHOICES = (("LLM_KEY_DEDICATED", LLM_KEY_MODES), ("LLM_STATE", LLM_STATES), ("DRILL_LLM", DRILL_LLM_MODES))
# пути к файлам секретов роли llm: без проверок, их берёт и запасная настройка при ошибке в LLM_*
_LLM_FILE_FIELDS = ("OPENROUTER_KEY_FILE", "OPENROUTER_MGMT_KEY_FILE", "LLM_TOKENS_FILE")
# прогон отбора модели (Д8-R69): id прогона и вызывающие под его потолком; ops — для ботов Д11
RUN_ID_RE = re.compile(r"[a-z0-9-]{1,32}")
RUN_CALLER_RE = re.compile(r"student-(0[1-9]|10)|ops")


def parse_run_callers(raw: str) -> frozenset[str]:
    """LLM_RUN_CALLERS (m11): через запятую, пробелы по краям срезаются; каждый — RUN_CALLER_RE, без повторов и
    пустых. Ошибка — ValueError с именем переменной, без значения (текст идёт в config_error и на страницу)."""
    items = [item.strip() for item in raw.split(",")]
    if not all(RUN_CALLER_RE.fullmatch(item) for item in items):
        raise ValueError("LLM_RUN_CALLERS: только student-01…student-10 и ops")
    if len(set(items)) != len(items):
        raise ValueError("LLM_RUN_CALLERS: вызывающий повторяется")
    return frozenset(items)


def run_parts(llm: "LlmSettings") -> tuple[str, float, frozenset[str]] | None:
    """Прогон отбора (Д8-R69) из настроек: None — прогона нет (все три LLM_RUN_* пусты), иначе (id, потолок,
    вызывающие). Задаются все три или ни одного; потолок — больше нуля и не больше LLM_TOTAL_USD. Ошибка —
    ValueError, текст начинается с LLM_RUN_ и значения не содержит. Зовут from_env и ledger.run_spec: настройки
    тестов строятся replace без from_env, а роль проверяет их сама — закрыто при сомнении."""
    given = (llm.LLM_RUN_ID != "", llm.LLM_RUN_USD != 0, llm.LLM_RUN_CALLERS != "")
    if not any(given):
        return None
    if not all(given):
        raise ValueError("LLM_RUN_*: нужны все три переменные или ни одной")
    if not RUN_ID_RE.fullmatch(llm.LLM_RUN_ID):
        raise ValueError("LLM_RUN_ID: только a-z, 0-9 и дефис, до 32 знаков")
    cap = llm.LLM_RUN_USD
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not (
            math.isfinite(cap) and 0 < cap <= llm.LLM_TOTAL_USD):
        raise ValueError("LLM_RUN_USD: больше нуля, не больше LLM_TOTAL_USD")
    return llm.LLM_RUN_ID, float(cap), parse_run_callers(llm.LLM_RUN_CALLERS)


@dataclass(frozen=True)
class LlmSettings:
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # ключи — только файлами (Global Constraints Д4а): пустой файл — «ключа нет»
    OPENROUTER_KEY_FILE: str = "/run/secrets/openrouter.key"
    OPENROUTER_MGMT_KEY_FILE: str = "/run/secrets/openrouter-mgmt.key"
    # хеши токенов LLM: строки «<id> <sha256 hex>», отдельная таблица от core-tokens
    LLM_TOKENS_FILE: str = "/run/secrets/llm-tokens"
    LLM_MODELS: str = ""                    # пусто — все семь кандидатов (select_models)
    # запасная модель при отказе основной «подождите/недоступен» (Д14): пусто — запасного пути нет, иначе
    # пары <основная>=<запасная> через запятую, имена — точные имена CANDIDATES (parse_fallback)
    LLM_FALLBACK: str = ""
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
    # прогон отбора модели (Д8-R69): все три или ни одного; потолок — поверх долей и общего бюджета
    LLM_RUN_ID: str = ""                    # RUN_ID_RE; новый id — новая точка отсчёта
    LLM_RUN_USD: float = 0.0                # потолок прогона, $: больше нуля и не больше LLM_TOTAL_USD
    LLM_RUN_CALLERS: str = ""               # места прогона через запятую (RUN_CALLER_RE)
    # тормоз прогона (Д11-R117, llm/brake.py): работает только при LLM_RUN_*; пороги — у core вложенным умолчанием
    # compose.yaml (от MEM_FAIL_MIB, SWAP_GROW_WARN_MIB, PSI_FULL_MAX сторожа), пути — только двойники стенда
    LLM_RUN_MEM_FAIL_MIB: int = 1024        # свободно меньше — причина memory
    LLM_RUN_SWAP_GROW_MIB: int = 512        # swap вырос от базы или за 10 мин на столько — причина swap
    LLM_RUN_SWAP_BASE_MIB: int = -1         # база swap прогона, МиБ; −1 — первая проверка (хранится в llm_meta)
    LLM_RUN_PSI_FULL_MAX: float = 10.0      # давление full avg10 выше, % — причина psi
    LLM_RUN_MEMINFO: str = "/proc/meminfo"
    LLM_RUN_VMSTAT: str = "/proc/vmstat"
    LLM_RUN_PRESSURE: str = "/proc/pressure/memory"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LlmSettings":
        """Пустое значение — умолчание; ошибка — ValueError с именем поля, но без самого значения: текст идёт
        в config_error и на /health/llm, а в переменную по ошибке может попасть что угодно. env — только он,
        иначе os.environ.

        select_models импортируется здесь, а не в модуле: import pcbk_core.main в пробе здоровья не тянет
        pcbk_core.llm, fastapi и httpx (П8).
        """
        from .llm.models import parse_fallback, select_models

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
                low, high = _LLM_FLOAT_MIN.get(f.name), _LLM_FLOAT_MAX.get(f.name)
                if not (math.isfinite(value) and value > 0 and (low is None or value >= low)
                        and (high is None or value <= high)):
                    need = "больше нуля" if low is None else f"не меньше {low:g}"
                    if high is not None:
                        need += f" и не больше {high:g}"
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
            models = select_models(llm.LLM_MODELS)
        except ValueError as exc:           # «LLM_MODELS: неизвестная модель «имя»» — имя не повторяется
            raise ValueError(re.sub(r"«[^»]*»", "«…»", str(exc))) from None
        try:
            parse_fallback(llm.LLM_FALLBACK, models)
        except ValueError as exc:           # «LLM_FALLBACK: …«имя»…» — имя не повторяется
            raise ValueError(re.sub(r"«[^»]*»", "«…»", str(exc))) from None
        run_parts(llm)                      # LLM_RUN_*: все три или ни одного (Д8-R69)
        return llm

    @classmethod
    def files_from_env(cls, env: Mapping[str, str] | None = None) -> "LlmSettings":
        """Запасная настройка при ошибке в LLM_*: умолчания, но пути к файлам секретов — из env. Так роль
        с config_error читает свои llm-tokens и ключи, а не пути по умолчанию (тесты не читают /run/secrets)."""
        env = os.environ if env is None else env
        return cls(**{name: env[name] for name in _LLM_FILE_FIELDS if env.get(name, "")})


# шлюз (Д5а): режимы учений; GW_ORIGIN — https://хост[:порт] без пути, хост строчными (Origin браузера —
# строчными), порт без ведущих нулей и не 443 (браузер его не пишет) — иначе Origin не совпал бы никогда;
# STU_NET — два октета без ведущих нулей (ipaddress их не принимает)
DRILL_GATEWAY_MODES = frozenset({"", "freeze_reaper", "bad_place_pw"})
# учения агентов студентов (Д7-R53, Д7-R58): freeze_reload — воркер перезагрузки замирает после первого прохода
DRILL_AGENTS_MODES = frozenset({"", "freeze_reload"})
# учения кабинета преподавателя (Д10-R96): shares_stale — перевод доли пишет диск и журнал действий, а память LLM-прокси
# не обновляет; journal_down — любая запись журнала действий (и пульс) падает до SQL. Поле GatewaySettings, а не Settings
# (m-7): опечатка — config_error шлюза при живом процессе, а не падение всего core
DRILL_TEACHER_MODES = frozenset({"", "shares_stale", "journal_down"})
_GW_ORIGIN = re.compile(r"https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::(?P<port>[1-9][0-9]{0,4}))?")
_OCTET = r"(0|[1-9][0-9]{0,2})"
_STU_NET = re.compile(_OCTET + r"\." + _OCTET)
# тексты ошибок GW_* — не длиннее 60 знаков: config_error «настройки шлюза: …» со сбоем базы — до 80
GW_ERROR_CHARS = 60
# предел тела /pcbk/api/oc/ — не меньше предела остальных ручек шлюза (64 КиБ) и не больше 4 МиБ (итоговая доводка
# Д5б, N8): тела держит общий бюджет 16 МиБ, и одно тело сверх 4 МиБ заняло бы его четверть
GW_MAX_BODY_MIN = 65536
GW_MAX_BODY_MAX = 4 * 1024 * 1024


def _gw_float(name: str, raw: str, ok: Callable[[float], bool], rule: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}: не число") from None
    if not (math.isfinite(value) and ok(value)):
        raise ValueError(f"{name}: {rule}")
    return value


def _gw_origin(raw: str) -> str:
    m = _GW_ORIGIN.fullmatch(raw)
    if m is None or (m["port"] is not None and (int(m["port"]) > 65535 or m["port"] == "443")):
        raise ValueError("GW_ORIGIN: нужно https://хост[:порт] строчными, без пути")
    return raw


def _sp_ctl_url(raw: str) -> str:
    try:
        parts = urlsplit(raw)
        port = parts.port                           # порт вне 0…65535 или не число — ValueError
    except ValueError:
        raise ValueError("SP_CTL_URL: адрес не разбирается") from None
    if parts.scheme != "http" or not parts.hostname or port == 0:
        raise ValueError("SP_CTL_URL: нужен адрес http://хост[:порт]")
    return raw


def _stu_net(raw: str) -> str:
    m = _STU_NET.fullmatch(raw)
    if m is None or int(m[1]) > 255 or int(m[2]) > 255:
        raise ValueError("STU_NET: нужны два октета вида 172.31, до 255")
    return raw


def _gw_max_body(raw: str) -> int:
    """Целое число байт в записи из цифр, от GW_MAX_BODY_MIN до GW_MAX_BODY_MAX; «1e6», «0x…», дроби — ошибка."""
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 12 or not GW_MAX_BODY_MIN <= int(raw) <= GW_MAX_BODY_MAX:
        raise ValueError(f"GW_MAX_BODY_BYTES: нужно целое от {GW_MAX_BODY_MIN} до {GW_MAX_BODY_MAX}")
    return int(raw)


def _drill_gateway(raw: str) -> str:
    if raw not in DRILL_GATEWAY_MODES:
        raise ValueError("DRILL_GATEWAY: пусто, freeze_reaper или bad_place_pw")
    return raw


def _drill_agents(raw: str) -> str:
    if raw not in DRILL_AGENTS_MODES:
        raise ValueError("DRILL_AGENTS: пусто или freeze_reload")
    return raw


def _drill_teacher(raw: str) -> str:
    if raw not in DRILL_TEACHER_MODES:
        raise ValueError("DRILL_TEACHER: пусто, shares_stale или journal_down")
    return raw


# проверка каждого поля по отдельности: значение из env → значение поля или ValueError (имя и правило)
_GW_FIELD_CHECKS: dict[str, Callable[[str], object]] = {
    "GW_ORIGIN": _gw_origin,
    "GW_IDLE_STOP_S": lambda raw: _gw_float("GW_IDLE_STOP_S", raw, lambda v: v >= 60, "нужно не меньше 60 с"),
    "GW_REAPER_TICK_S": lambda raw: _gw_float("GW_REAPER_TICK_S", raw, lambda v: v > 0, "нужно больше нуля"),
    "GW_WAKE_TIMEOUT_S": lambda raw: _gw_float("GW_WAKE_TIMEOUT_S", raw, lambda v: 10 <= v <= 300,
                                               "нужно от 10 до 300 с"),
    "GW_SESSION_TTL_S": lambda raw: _gw_float("GW_SESSION_TTL_S", raw, lambda v: v >= 300, "нужно не меньше 300 с"),
    "SP_CTL_URL": _sp_ctl_url,
    "STU_NET": _stu_net,
    "DRILL_GATEWAY": _drill_gateway,
    "GW_MAX_BODY_BYTES": _gw_max_body,
    "DRILL_AGENTS": _drill_agents,
    "DRILL_TEACHER": _drill_teacher,
}


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
    GW_MAX_BODY_BYTES: int = 524288           # предел тела /pcbk/api/oc/ (Д5-R36; у edge — client_max_body_size 512k)
    DRILL_AGENTS: str = ""                    # учения агентов студентов (Д7а): freeze_reload
    DRILL_TEACHER: str = ""                   # учения кабинета преподавателя (Д10а): shares_stale, journal_down

    @classmethod
    def _parse(cls, env: Mapping[str, str] | None) -> tuple[dict[str, object], list[str]]:
        """Поля, прошедшие проверку по отдельности, и тексты ошибок остальных (по порядку полей, затем правило
        такта, зависящее от GW_IDLE_STOP_S). Пустое значение — умолчание."""
        env = os.environ if env is None else env
        values: dict[str, object] = {}
        errors: list[str] = []
        for f in fields(cls):
            raw = env.get(f.name, "")
            if raw == "":
                continue
            try:
                values[f.name] = _GW_FIELD_CHECKS.get(f.name, str)(raw)
            except ValueError as exc:
                errors.append(str(exc))
        idle = values.get("GW_IDLE_STOP_S", cls.GW_IDLE_STOP_S)
        if values.get("GW_REAPER_TICK_S", cls.GW_REAPER_TICK_S) > idle / 2:
            errors.append("GW_REAPER_TICK_S: не больше GW_IDLE_STOP_S / 2")
            values.pop("GW_REAPER_TICK_S", None)
        return values, errors

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewaySettings":
        """Пустое значение — умолчание; ошибка — ValueError с именем поля и правилом, но без самого значения
        (Ш9): текст идёт в config_error, на страницу состояния и в ответ 503, а в GW_ORIGIN по ошибке может
        попасть путь или токен. Текст — не длиннее GW_ERROR_CHARS. env — только он, иначе os.environ."""
        values, errors = cls._parse(env)
        if errors:
            raise ValueError(errors[0])
        return cls(**values)

    @classmethod
    def valid_from_env(cls, env: Mapping[str, str] | None = None) -> "GatewaySettings":
        """Запасная настройка при ошибке в GW_* (по образцу LlmSettings.files_from_env): поля, прошедшие проверку
        по отдельности, — из env, прочие — умолчания. Так опечатка в GW_ORIGIN не сбрасывает STU_NET и
        SP_CTL_URL: сверка токенов мест и охрана выхода идут по настоящим сетям (ревью А4, I2)."""
        return cls(**cls._parse(env)[0])


def _check_base_url(url: str) -> None:
    """http или https, есть хост, порт — число в пределах; иначе ValueError с именем поля."""
    try:
        parts = urlsplit(url)
        parts.port                                  # порт вне 0…65535 или не число — ValueError
    except ValueError:
        raise ValueError("OPENROUTER_BASE_URL: адрес не разбирается") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("OPENROUTER_BASE_URL: нужен адрес http:// или https:// с хостом")
