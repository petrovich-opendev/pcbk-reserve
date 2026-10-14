"""Связь с прежним `DataService` (Task 7; план §Task 7/Этап 7; мастер-промпт §8, §2).

Использует существующий `DataService` в процессе, с серверным вызывающим и каналом `web` — тот же приём, что
`gateway/calc_api.py` (Global Constraints: `data/service.py` и `gateway/calc_api.py` не меняются; новых
SQL-запросов к историану, HTTP к самому себе, обхода бюджета или записи в SCADA здесь нет и не будет).
Единственные вызовы — `catalog_search`, `tag_now`, `tag_period`, `tag_series`, `calc_runtime` ровно с той же
сигнатурой, что у `DataService`; этот модуль не решает, какой тег читать, — решение (связь оборудования КДМ-1
с историаном) выдаёт `reviews.resolved_link` после согласования (Task 7), не подпись PNG/документа.

Вызывающий (`caller`) для `DataService` — `RequestIdentity.caller` (`student-NN`/`teacher`, как у
`gateway.calc_api.caller_for`), не логин и не роль напрямую: историан и бюджет видят то же имя вызывающего, что
и калькуляторы страницы, с теми же пределами частоты (`PER_CALLER_LIMIT` службы данных). Отказ прежней службы
(ошибка ворот, неизвестный тег, пустой каталог) не оборачивается и не прячется — он уходит вызывающему
(`service.py`) как есть: `DataService` сама не бросает исключений наружу из этих пяти методов (`_run` ловит
всё и превращает в ответ со `status="error"`/`"refused"`/`"none"`/`"partial"` — см. `data/service.py`), поэтому
«не оборачивать» здесь означает буквально «вернуть то, что дал `DataService`», без своей интерпретации полей
ответа (документальная справка без историана по-прежнему работает — мастер-промпт §7, этап 7).

`catalog_search` не требует `ResolvedLink` — это справочный поиск по разрешённому каталогу имён тегов (как у
калькуляторов страницы), а не чтение значения: точное совпадение строки подписи оборудования с именем тега
само по себе не включает чтение (мастер-промпт §7, этап 7, план «ОСОБЫЙ ФОКУС ПРОВЕРКИ №3») — этот модуль не
вызывает `tag_now`/`tag_period`/`tag_series`/`calc_runtime` из `catalog_search` ни при каком результате поиска;
единственный путь к ним — явный вызывающий (`service.py`) с уже полученным `ResolvedLink` от
`reviews.resolved_link` (сам он возвращает `None`, если решение не принято или полномочие решившего с тех пор
отозвано — `reviews.py`, докстринг модуля). Четыре читающих тега функции принимают только `link.historian_tag`
как имя тега для `DataService` — одно значение в списке `tags`, ровно как `link_id`/подпись его не определяют."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..data.service import DataService
    from .models import RequestIdentity
    from .reviews import ResolvedLink

CHANNEL: Literal["web"] = "web"


def caller_of(identity: "RequestIdentity") -> str:
    """Вызывающий службы данных для этой личности — `identity.caller`, один и тот же вид, что у
    `gateway.calc_api.caller_for` (не повторная логика ролей здесь — `RequestIdentity` уже её применил при
    сборке в `service.py`)."""
    return identity.caller


async def catalog_search(svc: "DataService", identity: "RequestIdentity", query: str,
                         limit: int = 10) -> dict[str, Any]:
    """Поиск по разрешённому каталогу тегов — для справки «что сейчас доступно», не для подтверждения связи
    оборудования (совпадение строки само по себе не включает чтение, мастер-промпт §7, этап 7)."""
    return await svc.catalog_search(caller_of(identity), CHANNEL, query, limit)


async def tag_now(svc: "DataService", identity: "RequestIdentity", link: "ResolvedLink") -> dict[str, Any]:
    """Текущее значение согласованного тега. `link` — обязательно продукт `reviews.resolved_link`, не прямое
    имя тега из вопроса или корпуса: без согласованной связи эта функция не вызывается вовсе (решает
    `service.py`, здесь — только проброс в `DataService.tag_now([link.historian_tag])`)."""
    return await svc.tag_now(caller_of(identity), CHANNEL, [link.historian_tag])


async def tag_period(svc: "DataService", identity: "RequestIdentity", link: "ResolvedLink",
                     period: str | None, start: str | None, end: str | None) -> dict[str, Any]:
    """Период/пояс, качество, пропуски, свежесть сохраняются как есть из ответа `DataService.tag_period` —
    модуль их не интерпретирует и не досчитывает (мастер-промпт §8: «график показывает единицы и пробелы;
    скрытая интерполяция запрещена»)."""
    return await svc.tag_period(caller_of(identity), CHANNEL, [link.historian_tag], period, start, end)


async def tag_series(svc: "DataService", identity: "RequestIdentity", link: "ResolvedLink",
                     period: str | None, start: str | None, end: str | None,
                     points: int | None = None) -> dict[str, Any]:
    """Ряд и профиль по согласованному тегу. `points=None` — умолчание самого `DataService`
    (`SERIES_DEFAULT_POINTS`) не подменяется здесь своим числом: модуль передаёт `points` дальше только когда
    вызывающий его явно задал."""
    caller = caller_of(identity)
    if points is None:
        return await svc.tag_series(caller, CHANNEL, [link.historian_tag], period, start, end)
    return await svc.tag_series(caller, CHANNEL, [link.historian_tag], period, start, end, points)


async def calc_runtime(svc: "DataService", identity: "RequestIdentity", link: "ResolvedLink",
                       period: str | None, start: str | None, end: str | None) -> dict[str, Any]:
    """Наработка/простои согласованного тега состояний за период — прямой проброс в
    `DataService.calc_runtime`, которая сама считает часы/долю периода/переходы по каждому состоянию из
    `StateSummaryHistory` (не повторный запрос историана и не досчёт здесь: `calculations.interval_union`
    модулю `calculations.py` нужен для объединения нескольких уже полученных периодов/тегов выше по стеку
    (`rendering.py`/будущий шаг — пересекающиеся потери времени CD/MD/STMD, мастер-промпт §8), не для одного
    тега за один период — тут объединять нечего, `DataService` уже агрегировал интервалы по состоянию)."""
    return await svc.calc_runtime(caller_of(identity), CHANNEL, [link.historian_tag], period, start, end)
