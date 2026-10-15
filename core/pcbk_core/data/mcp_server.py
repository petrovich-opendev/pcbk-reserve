"""MCP службы данных: catalog_search, tag_now, tag_period и калькуляторы tag_series, calc_runtime (Д9-R85) — вторая
тонкая оболочка над той же DataService; save_agent (Д7а, Д7-R57), my_agents и report_event (Д13) — только здесь,
без HTTP-ручки: тонкие оболочки над SaveAgent, MyAgents и ReportEvent роли.

mcp 1.30.0 (FastMCP), Streamable HTTP без сессий (stateless), ответ JSON, точный маршрут /mcp.

Порядок, как у HTTP:
1. токен — mcp_auth до транспорта: не POST — 405; нет или неверный Bearer — 401 и строка журнала
   outcome=unauthorized channel=mcp, без события. Вызывающего инструмент берёт по тому же заголовку;
2. ошибка настройки (F5) — config_unavailable раньше разбора аргументов. Транспорт отвечает на
   tools/call 200 (так устроен JSON-RPC), отказ — в самом результате: status unavailable, причина в
   message; историан не трогается;
3. аргументы — теми же моделями, что HTTP; ошибка — refuse_args: событие refused с русской причиной;
4. инструмент службы (событие и строка журнала — её).

Аннотации аргументов свободные (Any): иначе строку вместо списка, пустой вызов, неизвестный период и
17 тегов FastMCP отверг бы по-английски до функции, без события. Схему для модели задаёт
json_schema_extra (у tag_series — свои пределы тегов и точек: SeriesTags, Points). null равен «не задан»:
модели шлют null вместо пропуска необязательного поля.
Строку с JSON внутри FastMCP разбирает сам, до функции: '["A"]' станет списком, 'null' — «не задан».

save_agent: токен — тем же mcp_auth; ошибку настройки, частоту, «только место студента» и проверку черновика делает
SaveAgent (Д7-R57). Все восемь полей обязательны (SAVE_REQUIRED): по умолчанию None, как у трёх инструментов, и
required вписывается в схему после регистрации — FastMCP ставил бы его только полям без умолчания. Отказа по схеме нет
(call_tool(validate_input=False)): нет поля — замечание wrong_type службы. Лишние ключи FastMCP отбрасывает до функции.
"""
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import zones
from .http_api import (UNAUTHORIZED, CalcDeviationArgs, CalcRuntimeArgs, CatalogSearchArgs, Tag, TagNowArgs,
                       TagPeriodArgs, TagSeriesArgs, bearer_caller, unauthorized, validation_reason)
from .periods import PRESETS
from .report_event import REPORT_DESCRIPTION, REPORT_PROPERTIES, REPORT_REQUIRED
from .save_agent import SAVE_DESCRIPTION, SAVE_PROPERTIES, SAVE_REQUIRED
from .service import (DEVIATION_DEFAULT_POINTS, QUERY_CHARS, SEARCH_LIMIT, SPC_DEFAULT_POINTS, SPC_MAX_SUBGROUP,
                      SPC_MIN_POINTS, TAG_CHARS, requested_of)
from .sql import MAX_NAMES, SERIES_DEFAULT_POINTS, SERIES_MAX_POINTS, SERIES_MAX_TAGS, SERIES_MIN_POINTS

if TYPE_CHECKING:
    from . import DataRole

MCP_PATH = "/mcp"
NOT_POST = {"detail": "метод не поддерживается: только POST"}
# их читает модель; текст постоянный
TOOL_DESCRIPTIONS = {
    "catalog_search": "Поиск тегов БДРВ участка по словам в имени и описании. Описания в историане обрезаны на "
                      "50 символах: если точного совпадения нет, вернутся похожие — покажите их студенту на "
                      "выбор, не угадывайте.",
    "tag_now": "Текущее значение тегов БДРВ (до 16 за вызов): значение, единица, время с поясом, возраст и "
               "качество. Имена берите точно из catalog_search.",
    "tag_period": "Сводка по тегам БДРВ за период (до 16 тегов, окно до 31 суток, бюджет 24 тего-суток): "
                  "среднее, минимум, максимум, размах, σ — посчитаны историаном по сырым данным. Период — "
                  "готовый (period) или начало и конец (start, end, ISO 8601). В ответе сказано, какое это "
                  "среднее, в каком поясе время и что смены 06/14/22 — допущение.",
    "tag_series": "Ряд значений тегов БДРВ за период — профиль (до 4 тегов, на тег до 96 точек, всего до 288; окно до "
                  "31 суток, бюджет 24 тего-суток): значения на равномерной сетке историана и по каждому тегу начало, "
                  "конец, изменение, минимум и максимум сетки с моментами. Всплески короче шага не видны: истинные "
                  "минимум, максимум и среднее даёт tag_period. Период — готовый (period) или начало и конец (start, "
                  "end, ISO 8601).",
    "calc_runtime": "Наработка по тегам состояний БДРВ (до 16 тегов, окно до 31 суток, бюджет 24 тего-суток): часы и "
                    "доля периода в каждом состоянии и число переходов — по сырым данным историана. Смысл 1 зависит "
                    "от окончания тега (_RUN 1 — работает, _CLS 1 — закрыт): в ответе есть подпись. Аналоговые теги "
                    "не считаются.",
    "calc_deviation": "Отклонение измерения от задания регулятора БДРВ за период — контрольная точка (один запрос, "
                      "пределы как у tag_series: на тег до 96 точек, окно до 31 суток, бюджет 24 тего-суток): для "
                      "тега измерения (_PV/_MV) и задания (_SP) того же контура считает |измерение − задание| по "
                      "совпадающим меткам ряда и сравнивает с threshold — порогом технолога (число больше 0, не "
                      "норматив ПЦБК), который вы передаёте аргументом. В ответе — наибольшее и среднее отклонение, "
                      "число точек сверх порога и интервалы подряд идущих точек сверх порога (начало, конец, число "
                      "точек, наибольшее отклонение с его измерением и заданием). Период — готовый (period) или "
                      "начало и конец (start, end, ISO 8601).",
    "calc_spc": "Карта Шухарта (SPC) по одному аналоговому тегу БДРВ за период — считает служба, не модель (один "
               "запрос, пределы как у tag_series: на тег до 96 точек, окно до 31 суток, бюджет 24 тего-суток; "
               "points — от 20 до 96, по умолчанию 96): CL/UCL/LCL и σ (σ_внутр и σ_общ), нарушения 8 правил "
               "Нельсона (время, правило, значение, зона), Cp/Cpk/Pp/Ppk при переданных границах допуска "
               "(lsl/usl — порог технолога, число, НЕ норматив ПЦБК), мини-график строкой символов (sparkline). "
               "subgroup=1 (по умолчанию) — карта индивидуальных значений (I-MR, скользящий размах); subgroup "
               "2..10 — карта средних подгрупп (X̄-R). Зовите, когда просят карту Шухарта, SPC, контрольные "
               "границы, нарушения правил или Cp/Cpk — не считайте сами: контрольные границы (UCL/LCL) — "
               "статистическая вариация процесса, НЕ границы допуска и не норматив ПЦБК; при коротком ряде "
               "(меньше 20 точек/подгрупп) границы ориентировочные. Период — готовый (period) или начало и "
               "конец (start, end, ISO 8601).",
    "equipment": "Подсчёт агрегатов каталога БДРВ участка КДМ-1 по словам в имени и описании: зовите на "
                "«сколько насосов/задвижек/мешалок…» и «сколько сейчас в работе» — служба сама группирует "
                "теги агрегата (хвосты привода и состояния: RUN, LOD, SPD, CUR, OP, OPN, CLS, ON, OFF, STOP, "
                "FLT, ALM) и считает их, не агент. Число — units_total (и state_counts при with_state=true): "
                "агрегаты, у которых слово запроса стоит в начале названия; называйте студенту как есть, по "
                "спискам catalog_search не считайте сами. mentions_total — отдельное число: агрегаты, где "
                "слово есть в описании, но не в начале (например, «клапан после насоса» на запрос «насос») — "
                "упомяните отдельной фразой, в число не включайте и не складывайте с units_total. "
                "with_state=true — дополнительно читает текущее значение тега _RUN каждого найденного "
                "агрегата из units (до 120 за вызов): running/stopped/no_data/no_run_tag; mentions в SQL не "
                "идут.",
    "save_agent": SAVE_DESCRIPTION,         # одно место правды — save_agent.py (research/09 §4.1, дословно)
    "my_agents": "Ваши сохранённые агенты этого места (без архивных и отправленных на карантин), с их инструкцией "
                "— чтобы найти агента и предложить студенту правку. Без аргументов.",
    "report_event": REPORT_DESCRIPTION,     # одно место правды — report_event.py (Д13)
}
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Д7-R57: пишет, но не разрушает; повтор того же черновика — already, не второй агент
SAVE_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Д13: report_event пишет, не разрушает; не идемпотентен — каждый вызов создаёт новую строку находки
REPORT_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                     openWorldHint=False)

Tags = Annotated[Any, Field(json_schema_extra={"type": "array", "items": {"type": "string", "maxLength": TAG_CHARS},
                                               "minItems": 1, "maxItems": MAX_NAMES})]
SeriesTags = Annotated[Any, Field(json_schema_extra={"type": "array",
                                                     "items": {"type": "string", "maxLength": TAG_CHARS},
                                                     "minItems": 1, "maxItems": SERIES_MAX_TAGS})]
Points = Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": SERIES_MIN_POINTS,
                                                 "maximum": SERIES_MAX_POINTS})]
Preset = Annotated[Any, Field(json_schema_extra={"type": "string", "enum": list(PRESETS)})]
IsoTime = Annotated[Any, Field(json_schema_extra={"type": "string"})]
Query = Annotated[Any, Field(json_schema_extra={"type": "string", "minLength": 1, "maxLength": QUERY_CHARS})]
Limit = Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": 1, "maximum": SEARCH_LIMIT})]
DeviationTag = Annotated[Any, Field(json_schema_extra={"type": "string", "maxLength": TAG_CHARS})]
Threshold = Annotated[Any, Field(json_schema_extra={"type": "number", "exclusiveMinimum": 0})]
WithState = Annotated[Any, Field(json_schema_extra={"type": "boolean", "default": False})]
# calc_spc (Д21): свои пределы points (SPC_MIN_POINTS..SERIES_MAX_POINTS — выше нижней границы tag_series) и
# subgroup (1 — I-MR, 2..10 — X̄-R); lsl/usl — число без границ (None разрешён — отсутствие допуска)
SpcTag = Annotated[Any, Field(json_schema_extra={"type": "string", "maxLength": TAG_CHARS})]
SpcPoints = Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": SPC_MIN_POINTS,
                                                    "maximum": SERIES_MAX_POINTS})]
SpcSubgroup = Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": 1, "maximum": SPC_MAX_SUBGROUP})]
SpcBound = Annotated[Any, Field(json_schema_extra={"type": "number"})]


# equipment (Д19): MCP-инструмент без HTTP-ручки (Task 1) — своя модель аргументов, не из http_api
class EquipmentArgs(BaseModel):
    """Агрегаты каталога БДРВ: query — как у catalog_search, with_state — опрос тегов _RUN (до EQUIPMENT_RUN_CAP)."""

    query: Annotated[str, StringConstraints(min_length=1, max_length=QUERY_CHARS)]
    with_state: bool = False


# calc_spc (Д21): MCP-инструмент без HTTP-ручки, своя модель аргументов (как equipment), не из http_api.
# points/subgroup/lsl/usl — БЕЗ Field(ge=/le=/allow_inf_nan=) здесь нарочно: общий http_api.validation_reason()
# у поля "points" всегда отвечает словами SERIES_MIN_POINTS..SERIES_MAX_POINTS (там это захардкожено для
# tag_series/calc_deviation, план Д21 не трогает http_api.py) — у calc_spc своя, более высокая нижняя граница
# (SPC_MIN_POINTS); диапазон points/subgroup и конечность lsl/usl проверяет сама служба (_spc_points_problem,
# _subgroup_problem, _spc_bound_problem) — ровно той же причиной, что у прямого вызова и HTTP-аналогов других
# инструментов. Рекламируемая схема (минимум/максимум для модели) — отдельно, через SpcPoints/SpcSubgroup
# (json_schema_extra для FastMCP), а не через эту модель.
class CalcSpcArgs(BaseModel):
    """SPC и карта Шухарта (Д21): один аналоговый тег; points, subgroup, lsl, usl — без собственных пределов
    здесь (диапазон проверяет служба, см. комментарий выше); типы — целое/число, умолчания — как в сигнатуре
    MCP-инструмента."""

    tag: Tag
    period: Literal[PRESETS] | None = None
    start: str | None = None
    end: str | None = None
    points: int = SPC_DEFAULT_POINTS
    subgroup: int = 1
    lsl: float | None = None
    usl: float | None = None
AgentName = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["name"])]
AgentTitle = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["title"])]
AgentNode = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["node"])]
AgentPurpose = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["purpose"])]
AgentTools = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["tools"])]
AgentTags = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["tags"])]
AgentPrompt = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["prompt"])]
StudentReply = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["student_reply"])]
AgentReplace = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["replace"])]
ReportAgent = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["agent"])]
ReportKind = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["kind"])]
ReportTitle = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["title"])]
ReportTags = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["tags"])]
ReportPeriodStart = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["period_start"])]
ReportPeriodEnd = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["period_end"])]
ReportValue = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["value"])]
ReportSetpoint = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["setpoint"])]
ReportText = Annotated[Any, Field(json_schema_extra=REPORT_PROPERTIES["text"])]

log = logging.getLogger("pcbk_core.data.mcp")


def _caller(ctx: Context, role: "DataRole") -> str | None:
    """id вызывающего по заголовку Authorization запроса HTTP; вне запроса HTTP — None."""
    try:
        request = ctx.request_context.request
    except ValueError:                  # вне запроса MCP контекста нет
        return None
    if request is None:
        return None
    return bearer_caller(request.headers.get("authorization"), role.tokens, scope=request.scope)


def build_mcp(role: "DataRole") -> FastMCP:
    """Сервер MCP роли: пять инструментов только для чтения (служба — role.service в момент вызова) и my_agents
    (role.my_agents, Д13) — читают; save_agent (role.save_agent, Д7-R57) и report_event (role.report_event, Д13) —
    пишут.

    log_level="WARNING": без корневого обработчика (setup_logging его ставит) FastMCP настроил бы корень
    сам (basicConfig), и на INFO pytds писал бы адрес историана и начало SQL.

    Защита от DNS rebinding выключена. Она нужна серверу, которого страница в браузере может вызвать без
    секрета. У нас токен — заголовок Bearer, а не cookie: браузер сам его не подставит, а странице
    злоумышленника он неизвестен. К тому же Host разный: core:8000, адрес сети места, туннель.

    Настройки FastMCP (pydantic-settings) читали бы ./.env при каждой сборке: нечитаемый файл уронил
    бы конструктор роли, FIFO повесил бы его (F4). Все поля FastMCP передаются явно, файл не нужен —
    чтение выключено до сборки (mcp закреплён на 1.30.0).
    """
    FastMCPSettings.model_config["env_file"] = None
    mcp = FastMCP("pcbk-data", stateless_http=True, json_response=True, streamable_http_path=MCP_PATH,
                  log_level="WARNING",
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    async def call(ctx: Context, tool: str, raw: object, model: type[BaseModel], fields: dict[str, Any],
                   run: Callable[[str, Any], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """Токен → ошибка настройки → аргументы → служба; raw — сырой ввод для события."""
        caller = _caller(ctx, role)
        if caller is None:              # мимо mcp_auth (вызов без запроса HTTP): отказ без события
            log.warning("outcome=unauthorized channel=mcp tool=%s", tool)
            return {"tool": tool, "status": "refused", "message": UNAUTHORIZED["detail"], "notes": [],
                    "cache": "none"}
        if role.config_error:           # F5: раньше разбора аргументов
            return await role.service.config_unavailable(caller, "mcp", tool, requested_of(raw))
        try:
            args = model.model_validate({k: v for k, v in fields.items() if v is not None})
        except ValidationError as err:  # текст ошибки pydantic несёт ввод — дальше только русская причина
            reason = validation_reason(list(err.errors()))
        else:
            return await run(caller, args)
        return await role.service.refuse_args(caller, "mcp", tool, requested_of(raw), reason)

    @mcp.tool(name="catalog_search", description=TOOL_DESCRIPTIONS["catalog_search"], annotations=READ_ONLY)
    async def catalog_search(ctx: Context, query: Query = None, limit: Limit = 10) -> dict[str, Any]:
        return await call(ctx, "catalog_search", query, CatalogSearchArgs, {"query": query, "limit": limit},
                          lambda caller, a: role.service.catalog_search(caller, "mcp", a.query, a.limit))

    @mcp.tool(name="tag_now", description=TOOL_DESCRIPTIONS["tag_now"], annotations=READ_ONLY)
    async def tag_now(ctx: Context, tags: Tags = None) -> dict[str, Any]:
        return await call(ctx, "tag_now", tags, TagNowArgs, {"tags": tags},
                          lambda caller, a: role.service.tag_now(caller, "mcp", a.tags))

    @mcp.tool(name="tag_period", description=TOOL_DESCRIPTIONS["tag_period"], annotations=READ_ONLY)
    async def tag_period(ctx: Context, tags: Tags = None, period: Preset = None, start: IsoTime = None,
                         end: IsoTime = None) -> dict[str, Any]:
        return await call(ctx, "tag_period", tags, TagPeriodArgs,
                          {"tags": tags, "period": period, "start": start, "end": end},
                          lambda caller, a: role.service.tag_period(caller, "mcp", a.tags, a.period, a.start,
                                                                    a.end))

    @mcp.tool(name="tag_series", description=TOOL_DESCRIPTIONS["tag_series"], annotations=READ_ONLY)
    async def tag_series(ctx: Context, tags: SeriesTags = None, period: Preset = None, start: IsoTime = None,
                         end: IsoTime = None, points: Points = SERIES_DEFAULT_POINTS) -> dict[str, Any]:
        return await call(ctx, "tag_series", tags, TagSeriesArgs,
                          {"tags": tags, "period": period, "start": start, "end": end, "points": points},
                          lambda caller, a: role.service.tag_series(caller, "mcp", a.tags, a.period, a.start, a.end,
                                                                    a.points))

    @mcp.tool(name="calc_runtime", description=TOOL_DESCRIPTIONS["calc_runtime"], annotations=READ_ONLY)
    async def calc_runtime(ctx: Context, tags: Tags = None, period: Preset = None, start: IsoTime = None,
                           end: IsoTime = None) -> dict[str, Any]:
        return await call(ctx, "calc_runtime", tags, CalcRuntimeArgs,
                          {"tags": tags, "period": period, "start": start, "end": end},
                          lambda caller, a: role.service.calc_runtime(caller, "mcp", a.tags, a.period, a.start,
                                                                      a.end))

    @mcp.tool(name="calc_deviation", description=TOOL_DESCRIPTIONS["calc_deviation"], annotations=READ_ONLY)
    async def calc_deviation(ctx: Context, measure_tag: DeviationTag = None, setpoint_tag: DeviationTag = None,
                             threshold: Threshold = None, period: Preset = None, start: IsoTime = None,
                             end: IsoTime = None, points: Points = DEVIATION_DEFAULT_POINTS) -> dict[str, Any]:
        return await call(ctx, "calc_deviation", [measure_tag, setpoint_tag], CalcDeviationArgs,
                          {"measure_tag": measure_tag, "setpoint_tag": setpoint_tag, "threshold": threshold,
                           "period": period, "start": start, "end": end, "points": points},
                          lambda caller, a: role.service.calc_deviation(caller, "mcp", a.measure_tag,
                                                                        a.setpoint_tag, a.threshold, a.period,
                                                                        a.start, a.end, a.points))

    @mcp.tool(name="calc_spc", description=TOOL_DESCRIPTIONS["calc_spc"], annotations=READ_ONLY)
    async def calc_spc(ctx: Context, tag: SpcTag = None, period: Preset = None, start: IsoTime = None,
                       end: IsoTime = None, points: SpcPoints = SPC_DEFAULT_POINTS, subgroup: SpcSubgroup = 1,
                       lsl: SpcBound = None, usl: SpcBound = None) -> dict[str, Any]:
        return await call(ctx, "calc_spc", tag, CalcSpcArgs,
                          {"tag": tag, "period": period, "start": start, "end": end, "points": points,
                           "subgroup": subgroup, "lsl": lsl, "usl": usl},
                          lambda caller, a: role.service.calc_spc(caller, "mcp", a.tag, a.period, a.start, a.end,
                                                                  a.points, a.subgroup, a.lsl, a.usl))

    @mcp.tool(name="equipment", description=TOOL_DESCRIPTIONS["equipment"], annotations=READ_ONLY)
    async def equipment(ctx: Context, query: Query = None, with_state: WithState = False) -> dict[str, Any]:
        return await call(ctx, "equipment", query, EquipmentArgs, {"query": query, "with_state": with_state},
                          lambda caller, a: role.service.equipment(caller, "mcp", a.query, a.with_state))

    @mcp.tool(name="save_agent", description=TOOL_DESCRIPTIONS["save_agent"], annotations=SAVE_ANNOTATIONS)
    async def save_agent(ctx: Context, name: AgentName = None, title: AgentTitle = None, node: AgentNode = None,
                         purpose: AgentPurpose = None, tools: AgentTools = None, tags: AgentTags = None,
                         prompt: AgentPrompt = None, student_reply: StudentReply = None,
                         replace: AgentReplace = False) -> dict[str, Any]:
        caller = _caller(ctx, role)
        if caller is None:              # мимо mcp_auth (вызов без запроса HTTP): отказ без события
            log.warning("outcome=unauthorized channel=mcp tool=save_agent")
            return {"tool": "save_agent", "status": "refused", "message": UNAUTHORIZED["detail"], "problems": [],
                    "notes": []}
        # место — только из токена: в аргументах его нет, лишние ключи FastMCP уже отбросил
        return await role.save_agent.call(caller, {"name": name, "title": title, "node": node, "purpose": purpose,
                                                   "tools": tools, "tags": tags, "prompt": prompt,
                                                   "student_reply": student_reply, "replace": replace})

    # m8: все восемь полей обязательны — в схеме для модели; FastMCP по ней не отказывает (validate_input=False)
    mcp._tool_manager.get_tool("save_agent").parameters["required"] = list(SAVE_REQUIRED)

    @mcp.tool(name="my_agents", description=TOOL_DESCRIPTIONS["my_agents"], annotations=READ_ONLY)
    async def my_agents_tool(ctx: Context) -> dict[str, Any]:
        caller = _caller(ctx, role)
        if caller is None:
            log.warning("outcome=unauthorized channel=mcp tool=my_agents")
            return {"tool": "my_agents", "status": "refused", "message": UNAUTHORIZED["detail"], "notes": []}
        return await role.my_agents.call(caller)

    @mcp.tool(name="report_event", description=TOOL_DESCRIPTIONS["report_event"], annotations=REPORT_ANNOTATIONS)
    async def report_event(ctx: Context, agent: ReportAgent = None, kind: ReportKind = None,
                           title: ReportTitle = None, tags: ReportTags = None,
                           period_start: ReportPeriodStart = None, period_end: ReportPeriodEnd = None,
                           value: ReportValue = None, setpoint: ReportSetpoint = None,
                           text: ReportText = None) -> dict[str, Any]:
        caller = _caller(ctx, role)
        if caller is None:
            log.warning("outcome=unauthorized channel=mcp tool=report_event")
            return {"tool": "report_event", "status": "refused", "message": UNAUTHORIZED["detail"], "notes": []}
        return await role.report_event.call(caller, {"agent": agent, "kind": kind, "title": title, "tags": tags,
                                                     "period_start": period_start, "period_end": period_end,
                                                     "value": value, "setpoint": setpoint, "text": text})

    # все пять обязательны — в схеме для модели (как save_agent, m8); лишние ключи FastMCP отбрасывает до функции
    mcp._tool_manager.get_tool("report_event").parameters["required"] = list(REPORT_REQUIRED)
    return mcp


def mcp_auth(app: ASGIApp, role: "DataRole") -> ASGIApp:
    """Токен до транспорта MCP — чистое ASGI (BaseHTTPMiddleware ломает потоковые ответы).

    Только http: веб-сокет закрывается (1008), прочие виды (lifespan) внутрь не идут — второй run()
    сессий SDK не даёт. Не POST — 405: GET открыл бы поток SSE, в который без сессий никто не пишет.
    Нет или неверный Bearer (в том числе токен места из чужой сети, Ш25) — 401 {"detail": "нужен токен"} и
    строка журнала без токена, без события.
    """

    async def guard(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if scope["method"] != "POST":
            await JSONResponse(NOT_POST, status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        if bearer_caller(Headers(scope=scope).get("authorization"), role.tokens, scope=scope) is None:
            await unauthorized(scope.get("path", ""), "mcp")(scope, receive, send)
            return
        # транспорт MCP — своё приложение Starlette: оно заменит scope["app"], и сверка сети токена в
        # инструменте (_caller) не нашла бы STU_NET приложения core — кладём его в scope (Ш25)
        scope[zones.STU_NET_KEY] = zones.stu_net(scope)
        await app(scope, receive, send)

    return guard
