"""Вход серверного слоя: настройки, журнал, охрана выхода, роли, uvicorn; --healthcheck для образа.

FastAPI, uvicorn и httpx грузятся только в ветке сервера: проба здоровья раз в 30 с
тянет лишь http.client. Охрана выхода ставится только в serve() — точке входа
процесса: хук не снимается, а main() и build_roles тесты зовут в процессе pytest.
Импорты pcbk_core.llm и pcbk_core.gateway — только внутри функций (П8, Ш9).
"""
from __future__ import annotations

import http.client
import logging
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .settings import Settings

if TYPE_CHECKING:
    from .agents.stats import AgentStats
    from .app import Role
    from .gateway import GatewayRole
    from .llm import LlmRole
    from .secrets import BdrvConfig
    from .settings import GatewaySettings, LlmSettings

USAGE = "использование: python -m pcbk_core.main [--healthcheck]"
# места Д2: {STU_NET}.N.3, сервер OpenCode на 4096 (как gateway.places — без его импорта: он тянет httpx)
PLACES = range(1, 11)
PLACE_PORT = 4096
# мягкая остановка uvicorn: столько ждать соединений, потом снять их задачи и пройти lifespan ролей (журнал вызовов
# LLM, R19; замок входа). Без срока открытый поток /event (Д5б) держит остановку до SIGKILL stop_grace_period 10 с
GRACEFUL_SHUTDOWN_S = 2.0


def _why(exc: Exception) -> str:
    """Причина для журнала без строк файла: у ValueError чтения файлов текст — номер строки, не она сама."""
    if isinstance(exc, UnicodeDecodeError):
        return "файл не в UTF-8"
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'ошибка чтения'}"
    return str(exc)


def read_bdrv(settings: Settings) -> tuple[BdrvConfig | None, OSError | ValueError | None]:
    """Учётные данные историана из BDRV_ENV_FILE: (cfg, None) или (None, ошибка); в журнал не пишет."""
    from .secrets import BdrvConfig

    try:
        return BdrvConfig.from_env_file(settings.BDRV_ENV_FILE), None
    except (OSError, ValueError) as exc:
        # без трассы: её кадры держали бы строки файла
        return None, exc.with_traceback(None)


def egress_targets(llm: LlmSettings, gw: GatewaySettings, cfg: BdrvConfig | None) -> list[tuple[str, int | None]]:
    """Направления охраны выхода: историан (BDRV_HOST без экземпляра, BDRV_PORT; None — любой порт), хост
    OPENROUTER_BASE_URL (порт из адреса, иначе 443/80), прокси сокета SP_CTL_URL (по имени — через кэш адресов
    Д4а-R18) и места {STU_NET}.N.3:4096 литералами (Д5-R31): 13 направлений, без историана — 12. cfg None
    (bdrv.env не читается) — без историана (П3)."""
    targets: list[tuple[str, int | None]] = [] if cfg is None else [(cfg.host.split("\\")[0], cfg.port)]
    url = urlsplit(llm.OPENROUTER_BASE_URL)
    targets.append((url.hostname or "", url.port or (443 if url.scheme == "https" else 80)))
    sp_ctl = urlsplit(gw.SP_CTL_URL)
    targets.append((sp_ctl.hostname or "", sp_ctl.port or 80))
    targets += [(f"{gw.STU_NET}.{n}.3", PLACE_PORT) for n in PLACES]
    return targets


def build_roles(settings: Settings, env: Mapping[str, str] | None = None) -> list[Role]:
    """Роли процесса: «данные», LLM-прокси (Д4а) и шлюз (Д5а); env — окружение ролей llm и gateway (None —
    os.environ).

    Нет файла белого списка — роль с пустым списком: /healthz/data отвечает 503
    с причиной, ворота не пускают ни одного имени. bdrv.env или белый список не
    читаются — роль «с ошибкой настройки»: 503 с причиной, без фоновых циклов и
    входов в историан; процесс живёт, и сторож видит причину. Учётные данные —
    только файлом. core-tokens не читается — это не ошибка настройки (F4): пустая
    таблица (всем 401), причина TOKENS_ERROR на странице, фоновые циклы идут.

    Счётчики агентов студентов (Д7-R53) — один AgentStats на процесс у обеих ролей: «данные» пишет итоги save_agent,
    шлюз отдаёт их на /health/agents.
    """
    from .agents.stats import AgentStats
    from .data import CONFIG_ERRORS, TOKENS_ERROR, DataRole
    from .data.historian import HistorianError, QueryFn, tds_query
    from .data.names import load_names
    from .secrets import TokenTable

    log = logging.getLogger("pcbk_core.main")
    problems: list[str] = []

    def no_credentials(statements, remaining_s):
        raise HistorianError("connect", "учётные данные историана не прочитаны")

    query: QueryFn = no_credentials
    cfg, exc = read_bdrv(settings)
    if cfg is not None:
        query = tds_query(cfg)
    elif exc is not None:
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

    tokens, tokens_error = TokenTable.empty(), None
    try:
        tokens = TokenTable.from_file(settings.TOKENS_FILE)
    except (OSError, ValueError) as exc:
        # _why: у ValueError чтения — имя файла и номер строки, без самой строки
        log.error("токены %s: %s", settings.TOKENS_FILE, _why(exc))
        tokens_error = TOKENS_ERROR

    agent_stats = AgentStats()
    data = DataRole(settings, query, whitelist, config_error="; ".join(problems) or None,
                    tokens=tokens, tokens_error=tokens_error, agent_stats=agent_stats)
    return [data, build_llm_role(settings, env), build_gateway_role(settings, env, agent_stats=agent_stats)]


def build_llm_role(settings: Settings, env: Mapping[str, str] | None = None) -> LlmRole:
    """Роль llm по образцу роли «данные»: ошибка — причина на странице, процесс жив (П6, Д3а I2).

    LLM_* с ошибкой — умолчания (пути к файлам — свои) и config_error «настройки: …»; нечитаемый файл ключа
    или ключ не из печатных знаков ASCII — ключа нет, config_error; llm-tokens не читается — пустая таблица и
    tokens_error, это не config_error (П23). Ключ управления необязателен (Д4а-R16): нечитаемый или негодный —
    опрос /credits выключен, mgmt_key «error», строка WARNING, прокси работает. Строки журнала начинаются с
    «LLM-прокси: » — проверка журнала службы данных «токены <файл>» их не спутает. Ledger и Upstream в
    конструкторах ничего не открывают.
    """
    from .llm import LLM_TOKENS_ERROR, LlmRole
    from .llm.ledger import Ledger
    from .llm.upstream import Upstream
    from .secrets import KeyFileError, TokenTable, key_fingerprint, read_key_file
    from .settings import LlmSettings

    log = logging.getLogger("pcbk_core.main")
    problems: list[str] = []
    try:
        llm = LlmSettings.from_env(env)
    except ValueError as exc:
        log.error("LLM-прокси: настройки: %s", exc)
        llm = LlmSettings.files_from_env(env)   # умолчания, но свои файлы токенов и ключей
        problems.append(f"настройки: {exc}")

    tokens, tokens_error = TokenTable.empty(), None
    try:
        tokens = TokenTable.from_file(llm.LLM_TOKENS_FILE)
    except (OSError, ValueError) as exc:
        log.error("LLM-прокси: токены LLM %s: %s", llm.LLM_TOKENS_FILE, _why(exc))
        tokens_error = LLM_TOKENS_ERROR

    key = mgmt = None
    try:
        key = read_key_file(llm.OPENROUTER_KEY_FILE)
    except KeyFileError as exc:               # текст — имя файла и причина, без ключа
        log.error("LLM-прокси: ключ OpenRouter %s", exc)
        problems.append("ключ OpenRouter не читается")
    mgmt_failed = False
    try:
        mgmt = read_key_file(llm.OPENROUTER_MGMT_KEY_FILE)
    except KeyFileError as exc:
        log.warning("LLM-прокси: ключ управления OpenRouter %s — остаток счёта не опрашивается", exc)
        mgmt_failed = True
    upstream = None
    if key and not problems:
        try:
            upstream = Upstream(llm.OPENROUTER_BASE_URL, key, None, drill=llm.DRILL_LLM)
        except ValueError as exc:             # ключ не из печатных знаков ASCII; текст без ключа
            log.error("LLM-прокси: %s", exc)
            problems.append(str(exc))
        else:
            if mgmt:
                try:
                    upstream = Upstream(llm.OPENROUTER_BASE_URL, key, mgmt, drill=llm.DRILL_LLM)
                except ValueError as exc:     # негоден только ключ управления: основной уже проверен
                    log.warning("LLM-прокси: %s — остаток счёта не опрашивается", exc)
                    mgmt_failed = True
    role = LlmRole(settings, llm, tokens=tokens, upstream=upstream, ledger=Ledger(settings.DB_PATH),
                   key_fingerprint=key_fingerprint(key) if key else None,
                   config_error="; ".join(problems) or None, tokens_error=tokens_error)
    if mgmt_failed:
        role.mgmt_key = "error"
    return role


def build_gateway_role(settings: Settings, env: Mapping[str, str] | None = None, *,
                       agent_stats: AgentStats | None = None) -> GatewayRole:
    """Роль gateway по образцу build_llm_role (Ш9): GW_* с ошибкой — запасные настройки (gateway_settings) и
    config_error «настройки шлюза: …» (имя поля и правило, без значения); процесс жив. Ни один конструктор
    ничего не открывает: core.db — в lifespan роли, клиенты sp-ctl и мест — при первом запросе. agent_stats —
    общие с ролью «данные» счётчики агентов (build_roles); None — роль создаёт свои."""
    from .gateway import GatewayRole
    from .gateway.accounts import AccountStore
    from .gateway.places import SpCtl, Workplaces
    from .gateway.stats import GatewayStats

    gw, problem = gateway_settings(env)
    if problem is not None:
        logging.getLogger("pcbk_core.main").error("шлюз: настройки: %s", problem)
    stats = GatewayStats()
    return GatewayRole(settings, gw, store=AccountStore(settings.DB_PATH, ttl_s=gw.GW_SESSION_TTL_S),
                       workplaces=Workplaces(gw, SpCtl(gw.SP_CTL_URL), stats=stats), stats=stats,
                       config_error=None if problem is None else f"настройки шлюза: {problem}",
                       agent_stats=agent_stats)


def gateway_settings(env: Mapping[str, str] | None = None) -> tuple[GatewaySettings, str | None]:
    """GW_* для роли gateway и охраны выхода serve(): (настройки, None) или (запасные, текст первой ошибки).

    Запасные — поля, прошедшие проверку по отдельности (GatewaySettings.valid_from_env): опечатка в GW_ORIGIN
    не сбрасывает STU_NET и SP_CTL_URL к умолчаниям — сверка токенов мест и направления охраны остаются
    настоящими (ревью А4, I2)."""
    from .settings import GatewaySettings

    try:
        return GatewaySettings.from_env(env), None
    except ValueError as exc:
        return GatewaySettings.valid_from_env(env), str(exc)


def healthcheck(settings: Settings) -> int:
    """Для HEALTHCHECK образа — живость процесса: 0 — /healthz/live ответил 200 (uvicorn жив), иначе 1.

    Здоровье ролей сюда не входит: его видит сторож (/healthz/data, /health/llm). Так 402 от OpenRouter
    или ошибка настройки роли не делают контейнер core unhealthy.
    """
    conn = http.client.HTTPConnection("127.0.0.1", settings.CORE_PORT, timeout=4)
    try:
        conn.request("GET", "/healthz/live")
        return 0 if conn.getresponse().status == 200 else 1
    except (OSError, http.client.HTTPException):
        return 1
    finally:
        conn.close()


def uvicorn_options(settings: Settings) -> dict:
    """Параметры uvicorn.run службы: журнал uvicorn не настраивается (log_config=None), доступ не пишется —
    здоровье опрашивают часто; остановка ждёт соединений не дольше GRACEFUL_SHUTDOWN_S."""
    return {"host": "0.0.0.0", "port": settings.CORE_PORT, "log_config": None, "access_log": False,
            "timeout_graceful_shutdown": GRACEFUL_SHUTDOWN_S}


def serve(settings: Settings) -> None:
    """Точка входа службы: охрана выхода и поток обновления её адресов — до ролей. Плохой bdrv.env — без
    историана, плохие LLM_* — OpenRouter по умолчанию, плохие GW_* — прошедшие проверку поля и умолчания; причины
    показывают config_error ролей, процесс живёт (Д3а, I2)."""
    # до охраны (F14): pytds/login.py импортирует ctypes, а import ctypes под хуком на 3.14 падает
    import ctypes  # noqa: F401
    import httpx  # noqa: F401
    import pytds  # noqa: F401
    import uvicorn

    from .app import create_app
    from .egress import install_guard, start_refresh
    from .logs import setup_logging
    from .settings import LlmSettings

    log = setup_logging()
    try:
        llm = LlmSettings.from_env()
    except ValueError:
        llm = LlmSettings()                 # причину покажет роль llm (build_roles)
    gw, _ = gateway_settings()              # запасные при ошибке; причину покажет роль gateway (build_roles)
    install_guard(egress_targets(llm, gw, read_bdrv(settings)[0]))
    start_refresh()                         # адреса OpenRouter и sp-ctl — в кэше до первого вызова (Д4а-R18)
    roles = build_roles(settings)
    log.info("серверный слой слушает порт %s, роли: %s",
             settings.CORE_PORT, ", ".join(r.name for r in roles) or "нет")
    uvicorn.run(create_app(settings, roles), **uvicorn_options(settings))


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
