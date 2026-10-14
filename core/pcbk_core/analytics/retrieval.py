"""Поиск по корпусу (Task 1; план §Task 1/Этап 1; мастер-промпт §4, §5).

Начальный поиск — SQLite FTS5 (`kb_search`, `tokenize='unicode61'`) плюс точный поиск идентификаторов и
управляемые синонимы (мастер-промпт §4). `unicode61` не решает русскую морфологию — нормализацию запроса даёт
`terms.normalize_ru`, а не этот модуль повторно. Векторный поиск не добавляется по умолчанию (мастер-промпт §4:
«добавлять лишь при доказанном недостатке поиска и с проверкой стоимости/приватности»); этот модуль —
единственная точка, которую Task 1 заменил бы, если понадобится.

Область поиска — только процессы/карточки/механизмы КДМ-1 и связанная допустимая общая теория (мастер-промпт
§7 п.7: «поиск только доступных источников КДМ-1 и связанной общей теории»). Чужая машина (БДМ-2, КДМ-3) и
закрытые по `publication_state`/`scope` записи не возвращаются поиском вовсе — это не задача вызывающего
отфильтровать результат постфактум (ОСОБЫЙ ФОКУС ПРОВЕРКИ не называет это явно, но §5 корпуса: «листы с чужими
машинами не допускаются целиком в рабочий поиск»). На практике `kb_search` в реальном корпусе индексирует
только `claims`/`mechanisms`/`processes`/`terms` (`tools/import_seed.py`) — у `fragments` (сырые страницы/ячейки,
включая карантин `publication_state="quarantined_alternative_model"` и непересчитанные формулы `is_formula`)
нет записи в `kb_search` вовсе: поиск по ним структурно невозможен, карантин/необработанная формула не может
«случайно» попасть в выдачу как норма. Это подтверждает `test_analytics_retrieval.py` (кандидаты поиска — только
виды `SEARCH_KINDS`). Доп. проверка на каждый найденный `kb_search`-кандидат — `machine_scope` записи (если
есть в payload) начинается с токена `KDM1` (с границей `_`/конец строки, не просто «похожая» строка — см.
`_scope_ok`): это НЕ защита от «текущих данных заказчика чужой машины» (их нет по построению), а защита от
слишком узкого понимания «своей» области — у настоящего корпуса `machine_scope` принимает несколько разных
«своих» значений (`KDM1_PCBK`, `KDM1_PCBK_related`, `KDM1_related_general_mathematics` — общая статистика,
нужна отдельно от показателя COV Valmet), а не один литерал; более узкая проверка молча теряла законные записи
(правка, раунд 2 — найдено на настоящем корпусе).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from . import knowledge
from . import terms as _terms

if TYPE_CHECKING:
    from .knowledge import CorpusHandle

SearchKind = Literal["claim", "mechanism", "processe", "term"]       # kb_search.kind — "processe" как в сидере

_SEARCH_TO_ROW_KIND: dict[str, knowledge.RowKind] = {
    "claim": "claims", "mechanism": "mechanisms", "processe": "processes", "term": "terms",
}
_MACHINE = "KDM1"  # свой станок: ровно этот токен, не префикс произвольной строки (см. _scope_ok)
_MIN_WORD = 2
_PULL_FLOOR = 40
_PULL_CAP = 200
_CONNECT_TIMEOUT_S = 5.0


def _keep_token(word: str) -> bool:
    """Длиной `>=2` ИЛИ чисто цифровой — та же граница, что `terms._keep_token` (своя копия: каждый модуль
    Task 1 независим, см. докстринг модуля). Однобуквенный предлог не ищем, однозначный номер узла/бассейна
    (№7/№8/№9 — Особый фокус проверки плана) — ищем: иначе короткий цифровой запрос давал бы пустой поиск
    ровно в том случае, который план требует не путать (мутация №7/№9)."""
    return len(word) >= _MIN_WORD or word.isdigit()


@dataclass(frozen=True)
class SearchHit:
    """Один результат `kb_search`: `kind`/`id` — ключ записи в соответствующей таблице `knowledge.py`, `score`
    — ранг FTS5 (меньше — релевантнее, как `bm25()`), `snippet` — короткий фрагмент совпадения для журнала
    преданализа, не для прямого показа клиенту (показ — только через `rendering.py` по проверенной карточке)."""

    kind: SearchKind
    id: str
    score: float
    snippet: str


def _ro_connection(path: Path) -> sqlite3.Connection:
    """Своя короткая проверка на `mode=ro` — как в `knowledge._ro_connection` (не импортируется оттуда как
    приватная: каждый модуль Task 1 открывает соединение на свою операцию самостоятельно, см. докстринг
    `knowledge.py`, «Решение Task 1 по соединению» — те же причины потокобезопасности)."""
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
    except (OSError, ValueError) as exc:
        raise knowledge.CorpusError(type(exc).__name__) from None
    try:
        return sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
    except sqlite3.Error as exc:
        raise knowledge.CorpusError(type(exc).__name__) from None


def _scope_ok(payload: dict) -> bool:
    """`machine_scope`, если есть в записи, — свой станок (КДМ-1), не чужой. «Свой» — токен `KDM1` целиком,
    не произвольная строка с таким префиксом: у настоящего корпуса `machine_scope` реально принимает несколько
    разных значений для «своей» области — `KDM1_PCBK` (processes), `KDM1_PCBK_related` (большинство terms) и
    `KDM1_related_general_mathematics` (один термин — общая статистика, нужна отдельно от показателя COV
    Valmet, мастер-промпт §5/Этап 1) — все они начинаются с `KDM1_`, но не с `KDM1_PCBK`, поэтому проверка
    именно на `KDM1` с границей по `_`/концу строки, не на более узкий литерал. Граница обязательна: `KDM10_…`
    или `KDM1x…` — не КДМ-1 (другая машина/опечатка с тем же префиксом), совпадать должна не подстрока, а сам
    токен."""
    scope = payload.get("machine_scope")
    if scope is None:
        return True
    scope = str(scope)
    return scope == _MACHINE or scope.startswith(_MACHINE + "_")


def _process_ok(kind: str, row_id: str, payload: dict, process_ids: tuple[str, ...]) -> bool:
    if not process_ids:
        return True
    if kind == "processe":
        return row_id in process_ids
    if kind in ("claim", "mechanism"):
        return bool(set(payload.get("process_ids") or ()) & set(process_ids))
    return True  # "term" — словарь не привязан к узлу, фильтр процесса к нему не применяется


def search(handle: "CorpusHandle", query: str, *, kinds: tuple[SearchKind, ...] = (), limit: int = 20,
          process_ids: tuple[str, ...] = ()) -> list[SearchHit]:
    """Полнотекстовый поиск по `kb_search`: `kinds` — сузить виды записей (пусто — все виды Task 1), `limit` —
    не больше стольки результатов, `process_ids` — сузить область до этих процессов, если преданализ её уже
    определил (мастер-промпт §7 п.7). Пустой/битый запрос — пустой список, не исключение: решает, есть ли
    достаточные основания, вызывающий (`preanalysis.py`), не поиск. Сбой самого корпуса (файл/схема) —
    `knowledge.CorpusError`, это не «нет результатов»."""
    if not isinstance(query, str) or not query.strip() or limit <= 0:
        return []
    use_kinds = tuple(kinds) if kinds else knowledge.SEARCH_KINDS
    unknown = set(use_kinds) - set(knowledge.SEARCH_KINDS)
    if unknown:
        raise ValueError(f"неизвестные виды поиска: {sorted(unknown)}")
    # дефис внутри слова (`terms.normalize_ru` держит его как часть одного словарного токена) разбивается здесь:
    # unicode61-токенайзер индексирующей стороны сам режет текст по дефису, а запрос FTS5 MATCH с «-» внутри
    # токена — синтаксическая ошибка (унарный «минус» вне позиции оператора), не просто неудачный поиск
    tokens = [part for tok in _terms.normalize_ru(query).split() for part in tok.split("-") if _keep_token(part)]
    if not tokens:
        return []
    match_expr = " OR ".join(f"{tok}*" for tok in tokens)
    pull = min(max(limit * 4, _PULL_FLOOR), _PULL_CAP)
    placeholders = ",".join("?" for _ in use_kinds)
    sql = (
        "SELECT kind, id, bm25(kb_search) AS score, snippet(kb_search, 2, '', '', '…', 10) "
        f"FROM kb_search WHERE kb_search MATCH ? AND kind IN ({placeholders}) ORDER BY score LIMIT ?"
    )
    conn = _ro_connection(Path(handle.path))
    try:
        rows = conn.execute(sql, (match_expr, *use_kinds, pull)).fetchall()
    except sqlite3.Error as exc:
        raise knowledge.CorpusError(type(exc).__name__) from None
    finally:
        conn.close()
    hits: list[SearchHit] = []
    for kind, row_id, score, snippet in rows:
        row_kind = _SEARCH_TO_ROW_KIND[kind]
        payload = knowledge.get_row(handle, row_kind, row_id)
        if payload is None or not _scope_ok(payload) or not _process_ok(kind, row_id, payload, process_ids):
            continue
        hits.append(SearchHit(kind=kind, id=row_id, score=float(score), snippet=snippet or ""))
        if len(hits) >= limit:
            break
    return hits


def exact_id_lookup(handle: "CorpusHandle", kind: SearchKind, token: str) -> str | None:
    """Токен вопроса — точно существующий `id` записи вида `kind` (не через FTS5: прямой `get_row`), иначе
    `None`. Для случаев, когда технолог называет идентификатор карточки/механизма буквально, а не описывает
    его словами. Запись чужой машины (`machine_scope`, если есть) — тоже `None`, не найдено."""
    row_kind = _SEARCH_TO_ROW_KIND.get(kind)
    if row_kind is None:
        raise ValueError(f"неизвестный вид поиска: {kind!r}")
    row = knowledge.get_row(handle, row_kind, token)
    if row is None or not _scope_ok(row):
        return None
    return token


def related_fragments(handle: "CorpusHandle", claim_or_mechanism_id: str) -> list[str]:
    """`id` фрагментов (`fragments.jsonl`), на которые ссылается `evidence_refs` карточки/механизма — основа
    `EvidenceRef.fragment_id`/`locator` при сборке пакета доказательств (`validator.py`, Task 3). `evidence_refs`
    может содержать и `id` источника (`sources.jsonl`), не только фрагмента (`tools/import_seed.py` допускает
    оба) — сюда попадают только те ссылки, что действительно есть в таблице `fragments`; неизвестная/пропавшая
    ссылка тихо выпадает, не роняет вызов (нет записи — у вызывающего есть контекст решить, критично это или
    нет, не у этой функции)."""
    row = knowledge.get_row(handle, "claims", claim_or_mechanism_id)
    if row is None:
        row = knowledge.get_row(handle, "mechanisms", claim_or_mechanism_id)
    if row is None:
        return []
    refs = list(row.get("evidence_refs") or row.get("source_refs") or ())
    if not refs:
        return []
    found = knowledge.get_rows(handle, "fragments", refs)
    return [ref for ref in refs if ref in found]
