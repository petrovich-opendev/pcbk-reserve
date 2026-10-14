"""Адаптер существующего `LlmRole` (Task 5; план §Task 5/Этап 5; мастер-промпт §9).

Не вызывает `Upstream.open_chat` напрямую и не придумывает `Authorization`/`Request` — только то, что уже
проверено для `/llm/chat` (`llm/api.py::chat`): `rebuild_body` (`llm/request.py`), `LlmRole.begin_call`/
`LlmCall.finish` (`llm/api.py`), `forward` (`llm/api.py`). Повторяет **тот же порядок допуска**, что `chat()`,
минуя только разбор `Authorization` (личность здесь уже есть — `RequestIdentity` из сессии шлюза, не токен
вызывающего LLM):

0. преподаватель (`identity.role == "teacher"`) без явно настроенного плательщика (`teacher_payer`, ниже) —
   отказ раньше всего, до журнала/бюджета/сети (мастер-промпт §6: «платный запрос нельзя скрыто списывать на
   технолога или `ops`»; §9: «решение, какой `caller`/бюджет использовать для `teacher`, здесь не захардкожено
   … этот модуль сам роль не смотрит» — сам `caller` этот модуль не выбирает, но то, что путь вообще доступен
   преподавателю, — явный флаг, не молчаливое умолчание на общую долю места);
1. `role.config_error` → отказ без обращения к бюджету/журналу;
2. `role.stopped` (`LLM_STATE=stopped`) → отказ;
3. `role.upstream is None` (ключа нет) → отказ — справка без LLM остаётся доступной (`service.py` решает это
   на уровень выше, здесь только сообщается, что путь с LLM недоступен);
4. выбор модели — `role.models`, дополнительно суженный `AnalyticsSettings.ANALYTICS_LLM_MODELS`, если он не
   пуст (пересечение, не замена: модель обязана быть и в активном наборе роли, и — если ограничение задано — в
   этом подмножестве);
5. `rebuild_body` своей политикой, `estimate_usd`;
6. `role.ledger_error()` → отказ раньше резерва;
7. **один синхронный участок без `await`** (приём Д4а, master-промпт §9: «приём/резервирование/счётчики —
   атомарный участок без `await` между проверкой и резервом»): `role.inflight` (`LLM_CALLER_CONCURRENCY`) →
   `role.budget.refusal(caller)` → `role.begin_call(...)`;
8. `forward(role, call, body, raw)` — резерв/журнал/запасной маршрут/отмену и неизвестную цену целиком несёт
   `forward`, адаптер не повторяет эту логику.

Плательщик преподавателя (мастер-промпт §6: «если допустимый плательщик преподавателя не настроен явно,
платный разбор недоступен, а справка остаётся») — сегодня в системе нет ни одного места, где владелец мог бы
это настроить (`service.py`/Task 4 комментирует ровно это: «такого механизма сегодня нет (Task 5)», см.
`test_analytics_service.py::test_capabilities_teacher_without_explicit_payer_stays_false_even_with_llm`). Этот
модуль добавляет для этого необязательный параметр `request_selection(..., teacher_payer: str = "")`: пустая
строка (сегодняшний вызов `service.py` его не передаёт вовсе — отсутствующий именованный параметр с умолчанием
не ломает уже написанный вызов `_select_with_llm`) означает «не настроен», и путь с LLM для преподавателя
отказывает всегда, что и есть безопасное умолчание мастер-промпта. Когда появится настоящий механизм (например,
`AnalyticsSettings.ANALYTICS_TEACHER_PAYER`, добавлено этим же патчем — см. `settings.py`), `service.py`
прокидывает его значение сюда без изменения этой функции ещё раз. Выбор, каким `caller`/бюджетом платить
(«решение … не захардкожено») по-прежнему не в этом модуле: `identity.caller` уже несёт нужное имя
(`gateway/analytics_api.py::TEACHER_CALLER`), сюда он передаётся как есть.

Политика передачи закрытого содержания (`AnalyticsSettings.ANALYTICS_LLM_EXPORT_POLICY`, умолчание
`public_only`) решает, что ВИДИТ модель в запросе — не что она имеет право вернуть клиенту (это по-прежнему
только id, см. ниже). `public_only` — исходящее тело содержит только публичный контекст и абстрактное
намерение, это обеспечивает `build_raw_body` самой сборкой сообщений, а не инструкцией модели «не упоминай…»
(мастер-промпт §9: «это обеспечивается сборкой запроса, а не пожеланием к модели»): в системное сообщение
попадают только `id` разрешённого пакета преданализа (`preanalysis.allowed_claim_ids`/`allowed_mechanism_ids`,
дополнительно сверенные с самим пакетом карточек), в пользовательское — намерение и уже разрешённые id
терминов/узлов/расхождений; текста карточек (`EvidenceCard.text`/`applicability`) и исходного текста вопроса
там нет вовсе.

`approved_subset` — РЕШЕНИЕ ВЛАДЕЛЬЦА (итоговая правка F6, 14.10.2026, дословно): «4. Разрешен полный контент
в тч внутренний и данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!» —
владелец (заказчик и администратор стенда ПЦБК) дважды явно разрешил полный контент в запросе к модели, в т.ч.
внутренний и данные с БДРВ, если они есть в пакете. В пользовательское сообщение ДОПОЛНИТЕЛЬНО (сверх того,
что несёт `public_only`) попадают исходный текст вопроса (`question`, параметр `build_raw_body`, из
`AnalysisRequest.question`, не из `preanalysis.py`) и тексты карточек пакета — `id → текст, фрагмент,
локатор` (`_card_payload`), ровно того же разрешённого и физически присутствующего в пакете подмножества, что
и у `public_only` (`claim_ids`/`mechanism_ids` ниже). Модель, увидев полный контент, по-прежнему обязана
ответить ТОЛЬКО выбором id по `llm-selection.schema.json` — политика расширяет вход модели, не формат её
ответа; сырой текст модели клиенту не выводится ни при одном значении политики (`_parse_selection` ниже).
Предел размера исходящего тела (`_APPROVED_SUBSET_BODY_CHAR_BUDGET`, `_budget_card_texts`) — тексты карточек
обрезаются пропорционально с явной пометкой урезания, если суммарно превышают разумный предел; исходный
вопрос не обрезается (он уже ограничен контрактом клиента, `AnalysisRequest.question`, `max_length=6000`).

`none` — сервер-уровневый выключатель платного пути целиком (`service.py`, `capabilities.llm_available=
False`); до этого модуля с политикой `none` в обычной работе дело не доходит (`service.py::_build_outcome`
не зовёт `_select_with_llm` вовсе, когда `ANALYTICS_LLM_EXPORT_POLICY == "none"`), но `build_raw_body`
по-прежнему отказывает ей явно (и любому неизвестному значению) — защита на случай ручной сборки
`Preanalysis` в тестах/будущем коде, не промолчать телом без ограничения.

Строгий формат ответа — `response_format` с `json_schema` по `contracts/llm-selection.schema.json`,
дополнительно суженной для этого запроса: `enum` у `reference_claim_ids`/`mechanism_ids` — ровно разрешённый
пакет (или `maxItems: 0`, если он пуст), `calculation_ids.maxItems = 0` вне `intent == "calculation"`
(докстринг самой схемы: «сервер дополнительно сужает enum под конкретный пакет»).
Дальнейшая строгая проверка — `models.LlmSelection.model_validate`, не доверие модели (мастер-промпт §8: «при
неподдержанном строгом формате поставщика можно принимать JSON-объект с той же последующей строгой
проверкой»); невалидный JSON или лишнее поле (`extra="forbid"` модели) — тоже `LlmAdapterRefusal`, не
`ValidationError`/текст модели наружу. Сырой текст ответа (поток, заголовки, подписи) клиенту не возвращается
никогда — наружу идёт только разобранный и провалидированный `LlmSelection`."""
from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from ..llm.api import REFUSALS, forward
from ..llm.models import CANDIDATES, select_models
from ..llm.request import BodyRefused, body_bytes, rebuild_body
from .models import LlmSelection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..llm import LlmRole
    from ..llm.models import ModelPolicy
    from .models import EvidenceCard, Preanalysis, RequestIdentity

# порядок кандидатов llm/models.py — тот же, что select_models() отдаёт при пустом LLM_MODELS (см. select_model)
_CANDIDATE_ORDER: dict[str, int] = {policy.model: i for i, policy in enumerate(CANDIDATES)}
# код отказа llm/api.py (REFUSALS[key] == (status, code)) → причина LlmAdapterRefusal (тот же словарь, что
# _Ask.refuse); код приходит из тела, которое вернул forward(), не из HTTP-статуса (passthrough 4xx — тот же code)
_REASON_BY_CODE: dict[str, str] = {code: key for key, (_status, code) in REFUSALS.items()}
# причины этого модуля, которых нет в llm/api.py::REFUSALS — отказ до/после forward(), не его код
TEACHER_PAYER_REFUSAL = "teacher_payer"
INVALID_SELECTION_REFUSAL = "invalid_selection"

_SYSTEM_INSTRUCTION = (
    "Ты помогаешь технологу КДМ-1 выбрать среди уже проверенных сервером карточек корпуса и общих механизмов "
    "те, что относятся к вопросу, и список допустимых уточнений. В ответе — только id из приложенных "
    "разрешённых списков, строго по приложенной схеме; текст карточек, местные нормативы, ссылки и новые id "
    "не придумывай и не возвращай."
)


class LlmAdapterRefusal(Exception):
    """Путь с LLM недоступен: `reason` — код `llm/api.py::_Ask.refuse` (`config`, `stopped`, `key_missing`,
    `model`, `busy`, `ledger`, `budget_spent`, `share_spent`, `run_spent`, `run_halted`, …, код из `REFUSALS`
    при отказе `forward()`), либо один из двух кодов этого модуля — `TEACHER_PAYER_REFUSAL`/
    `INVALID_SELECTION_REFUSAL` — не текст для технолога напрямую, его превращает в `limitation`-секцию
    `rendering.py`/`service.py` (сейчас — молчаливый откат на справку без LLM, `service.py::_select_with_llm`)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --- 4: выбор модели ---

def select_model(role: "LlmRole", analytics_models: str) -> "ModelPolicy":
    """Пересечение `role.models` (активный `LLM_MODELS` роли) и `analytics_models`
    (`AnalyticsSettings.ANALYTICS_LLM_MODELS`, пусто — без дополнительного сужения); пусто после пересечения
    или `analytics_models` не разбирается (битое значение, в обычной работе такого не бывает — настройка уже
    проверена `AnalyticsSettings.from_env`, здесь — на случай ручной сборки настроек в тестах/вызове) —
    `LlmAdapterRefusal("model")`. Выбор одной модели из непустого пересечения — первая по порядку
    `llm/models.CANDIDATES` (тот же порядок, что `select_models` отдаёт при пустом `LLM_MODELS`), не самая
    дешёвая и не самая сильная: порядок кандидатов — решение `llm/models.py`, этот адаптер его не
    переопределяет."""
    try:
        restriction = select_models(analytics_models) if analytics_models.strip() else None
    except ValueError:
        raise LlmAdapterRefusal("model") from None
    names = [name for name in role.models if restriction is None or name in restriction]
    if not names:
        raise LlmAdapterRefusal("model")
    chosen = min(names, key=lambda name: _CANDIDATE_ORDER.get(name, len(_CANDIDATE_ORDER)))
    return role.models[chosen]


# --- сборка исходящего тела: только id разрешённого пакета и абстрактное намерение ---

def _narrow_schema(schema: dict, *, claim_ids: "Sequence[str]", mechanism_ids: "Sequence[str]",
                   calculation_allowed: bool) -> dict:
    """Копия схемы `llm-selection.schema.json` с `enum`/`maxItems`, суженными под этот запрос (докстринг
    самой схемы: «сервер дополнительно сужает enum под конкретный пакет»; мастер-промпт §8). Пустой список —
    `maxItems: 0` (не `enum: []`: пустой `enum` не все поставщики принимают одинаково), непустой — `enum`
    строго из этого списка. `calculation_ids` — `maxItems: 0` вне `intent == "calculation"` (вызывающий,
    `validator.check_calculation_consistency`, всё равно не доверяет только этому)."""
    narrowed = copy.deepcopy(schema)
    props = narrowed.get("properties")
    if not isinstance(props, dict):
        return narrowed
    for name, ids in (("reference_claim_ids", claim_ids), ("mechanism_ids", mechanism_ids)):
        prop = props.get(name)
        if not isinstance(prop, dict):
            continue
        if ids:
            items = prop.get("items")
            if isinstance(items, dict):
                items["enum"] = list(ids)
        else:
            prop["maxItems"] = 0
    if not calculation_allowed:
        calc = props.get("calculation_ids")
        if isinstance(calc, dict):
            calc["maxItems"] = 0
    return narrowed


def _resolved_term_ids(preanalysis: "Preanalysis") -> list[str]:
    """Только id терминов (не исходное слово вопроса `ResolvedTerm.original`): выбранный, если преданализ его
    определил, иначе все кандидаты — без повторов, по возрастанию (детерминированное тело)."""
    ids: list[str] = []
    for term in preanalysis.resolved_terms:
        ids.extend([term.selected_id] if term.selected_id else list(term.candidate_ids))
    return sorted(dict.fromkeys(ids))


# политики, которым этот модуль умеет собрать тело (докстринг модуля); `none` сюда в обычной работе не
# доходит (отключается на уровень выше, `service.py`), но явно не поддержана и здесь — см. build_raw_body
_SUPPORTED_EXPORT_POLICIES = frozenset({"public_only", "approved_subset"})

# approved_subset (решение владельца F6, 14.10.2026, докстринг модуля) — предел размера исходящего тела:
# большой пакет карточек не должен уйти модели без ограничения. Исходный вопрос в бюджет не входит — он уже
# ограничен контрактом клиента (`AnalysisRequest.question`, max_length=6000); под бюджет попадают только
# тексты карточек, обрезаемые пропорционально с явной пометкой урезания, а не молча.
_APPROVED_SUBSET_BODY_CHAR_BUDGET = 60_000
_CARD_TEXT_TRUNCATION_MARK = "…[текст карточки обрезан — предел размера исходящего запроса]"


def _card_payload(card: "EvidenceCard") -> dict:
    """approved_subset — полный текст карточки и ссылка на фрагмент/локатор источника (`EvidenceCard.
    evidence_refs`, та же `EvidenceRef`, что несёт `job-response.schema.json`): `id → текст, фрагмент,
    локатор` (брифа задания). Формат ответа модели это не меняет — только то, что она видит при выборе."""
    return {
        "id": card.id,
        "text": card.text,
        "fragments": [
            {"fragment_id": ref.fragment_id, "locator": ref.locator.model_dump()}
            for ref in card.evidence_refs
        ],
    }


def _budget_card_texts(cards: list[dict], *, reserved_chars: int) -> list[dict]:
    """Урезать поле `text` карточек `_card_payload` под общий символьный бюджет (`_APPROVED_SUBSET_BODY_
    CHAR_BUDGET` минус `reserved_chars`, уже занятые вопросом и прочим телом) — пропорционально между
    карточками, с явной пометкой урезания (`_CARD_TEXT_TRUNCATION_MARK`), не молчаливой потерей содержания.
    Суммарный текст в пределах бюджета — список возвращается как есть, без копирования."""
    budget = max(_APPROVED_SUBSET_BODY_CHAR_BUDGET - reserved_chars, 0)
    if not cards or sum(len(c["text"]) for c in cards) <= budget:
        return cards
    share = max(budget // len(cards), len(_CARD_TEXT_TRUNCATION_MARK) + 1)
    budgeted = []
    for c in cards:
        text = c["text"]
        if len(text) > share:
            text = text[: share - len(_CARD_TEXT_TRUNCATION_MARK)] + _CARD_TEXT_TRUNCATION_MARK
        budgeted.append({**c, "text": text})
    return budgeted


def build_raw_body(preanalysis: "Preanalysis", pack: tuple["EvidenceCard", ...], *, schema: dict,
                   question: str = "") -> dict:
    """Собрать «сырое» тело для `rebuild_body`: `messages` — одно системное сообщение с разрешённым пакетом
    id (`preanalysis.allowed_claim_ids`/`allowed_mechanism_ids`, дополнительно сверенные с `pack` — id должен и
    числиться разрешённым преданализом, и реально присутствовать в пакете этого запроса, не текстом фрагментов
    целиком) и одно пользовательское — абстрактное намерение (`preanalysis.intent`, id разрешённых терминов/
    узлов/расхождений); `stream: False` (аналитика не стримит ответ клиенту); `response_format` —
    `{"type": "json_schema", "json_schema": {...}}` по `contracts/llm-selection.schema.json`, суженной
    `_narrow_schema` под этот пакет.

    Политика передачи (докстринг модуля, решение владельца F6 14.10.2026) — `public_only` (умолчание): как
    раньше, без текста карточек и без `question`, параметр просто не используется. `approved_subset`:
    пользовательское сообщение ДОПОЛНИТЕЛЬНО несёт `question` (как передано вызывающим — `service.py` берёт
    его из `AnalysisRequest.question`, не из `preanalysis.py`) и `cards` — `_card_payload` того же разрешённого
    и физически присутствующего в пакете подмножества, что и `claim_ids`/`mechanism_ids` ниже (двойная сверка
    не слабее, чем у `public_only`), урезанного под бюджет `_budget_card_texts`. Любое иное значение (в т.ч.
    `none` — см. `_SUPPORTED_EXPORT_POLICIES`) — `ValueError` без сборки тела (вызывающий, `service.py::
    _select_with_llm`, перехватывает любое исключение и откатывается на справку без LLM — отказ здесь не
    роняет задание)."""
    if preanalysis.llm_export_policy not in _SUPPORTED_EXPORT_POLICIES:
        raise ValueError(
            f"llm_adapter.build_raw_body: политика передачи {preanalysis.llm_export_policy!r} не поддержана "
            "здесь — исходящий пакет не собран (мастер-промпт §9; `none` отключает вызов модели целиком, см. "
            "service.py)")
    pack_claim_ids = {card.id for card in pack if card.kind == "claim"}
    pack_mechanism_ids = {card.id for card in pack if card.kind == "mechanism"}
    claim_ids = tuple(cid for cid in preanalysis.allowed_claim_ids if cid in pack_claim_ids)
    mechanism_ids = tuple(mid for mid in preanalysis.allowed_mechanism_ids if mid in pack_mechanism_ids)
    calculation_allowed = preanalysis.intent == "calculation"
    system = {
        "instruction": _SYSTEM_INSTRUCTION,
        "allowed_reference_claim_ids": list(claim_ids),
        "allowed_mechanism_ids": list(mechanism_ids),
        "calculation_allowed": calculation_allowed,
    }
    user = {
        "intent": preanalysis.intent,
        "risk": preanalysis.risk,
        "resolved_term_ids": _resolved_term_ids(preanalysis),
        "process_ids": list(preanalysis.process_ids),
        "conflict_ids": list(preanalysis.conflict_ids),
    }
    if preanalysis.llm_export_policy == "approved_subset":
        allowed_ids = set(claim_ids) | set(mechanism_ids)
        cards_payload = [_card_payload(card) for card in pack if card.id in allowed_ids]
        user["question"] = question
        user["cards"] = _budget_card_texts(cards_payload, reserved_chars=len(question))
    narrowed_schema = _narrow_schema(schema, claim_ids=claim_ids, mechanism_ids=mechanism_ids,
                                     calculation_allowed=calculation_allowed)
    return {
        "messages": [
            {"role": "system", "content": json.dumps(system, ensure_ascii=False, sort_keys=True)},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)},
        ],
        "stream": False,
        "response_format": {"type": "json_schema", "json_schema": narrowed_schema},
    }


# --- разбор ответа forward(): тело без потока → LlmSelection, без доверия модели ---

def _refusal_reason_of(response: object) -> str:
    """Код отказа из JSON-тела, которое вернул `forward()` (`error.code`, см. `llm/api.py::error_body`) →
    причина `LlmAdapterRefusal` (`_REASON_BY_CODE`); код не узнан или тело не разобрать — `"down"` (тот же
    смысл, что и у `llm/api.py::REFUSALS["down"]`: OpenRouter/прокси не ответили как ожидалось)."""
    payload = getattr(response, "body", None) or b""
    try:
        parsed = json.loads(payload)
    except Exception:                                      # noqa: BLE001 — тело отказа не разобрать
        return "down"
    code = parsed.get("error", {}).get("code", "") if isinstance(parsed, dict) else ""
    return _REASON_BY_CODE.get(code, "down")


def _message_json(raw: bytes) -> dict:
    """Тело ответа OpenRouter без потока (JSON целиком) → разобранный `choices[0].message.content` (строка
    JSON по заданной схеме) → объект. Любое расхождение (не JSON, нет `choices`/`message`/`content`, `content`
    не строка, `content` не JSON-объект) — `LlmAdapterRefusal(INVALID_SELECTION_REFUSAL)`, не исключение
    разбора наружу и не текст модели клиенту (мастер-промпт §8: «сырой текст ответа … клиенту не выводится
    никогда»)."""
    try:
        body = json.loads(raw)
    except Exception:
        raise LlmAdapterRefusal(INVALID_SELECTION_REFUSAL) from None
    choices = body.get("choices") if isinstance(body, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LlmAdapterRefusal(INVALID_SELECTION_REFUSAL)
    try:
        parsed = json.loads(content)
    except Exception:
        raise LlmAdapterRefusal(INVALID_SELECTION_REFUSAL) from None
    if not isinstance(parsed, dict):
        raise LlmAdapterRefusal(INVALID_SELECTION_REFUSAL)
    return parsed


def _parse_selection(response: object) -> "LlmSelection":
    """Ответ `forward()` (всегда `Response`/`JSONResponse` без потока — тело собирается с `stream: False`) →
    провалидированный `LlmSelection`. Статус не 200 — отказ `forward()`/OpenRouter (`_refusal_reason_of`);
    статус 200, но содержимое не разобрать строгой схемой (`LlmSelection.model_validate`, `extra="forbid"` —
    лишнее поле моделью не проходит, как и неизвестный тип значения) — `INVALID_SELECTION_REFUSAL`, не
    `ValidationError` наружу (INTERFACES.md: «невалидный JSON/схема — тоже `LlmAdapterRefusal`»)."""
    status = getattr(response, "status_code", None)
    if status != 200:
        raise LlmAdapterRefusal(_refusal_reason_of(response))
    payload = getattr(response, "body", None) or b""
    selection_obj = _message_json(payload)
    try:
        return LlmSelection.model_validate(selection_obj)
    except Exception as exc:                 # ValidationError и прочее — причина фиксированная, не текст исключения
        raise LlmAdapterRefusal(INVALID_SELECTION_REFUSAL) from exc


# --- единственная точка вызова OpenRouter для аналитики ---

async def request_selection(role: "LlmRole", identity: "RequestIdentity", session: str, raw_body: dict, *,
                            analytics_models: str, teacher_payer: str = "") -> "LlmSelection":
    """Единственная точка вызова OpenRouter для аналитики — весь порядок допуска из докстринга модуля одним
    вызовом. `identity.caller` — вызывающий и плательщик журнала (как у калькуляторов страницы), `session` —
    `conversation_id` (не `None`: у аналитических диалогов, в отличие от обычного чата, сессия всегда есть).
    `teacher_payer` — явно настроенный плательщик преподавателя (докстринг модуля); пусто (умолчание — сегодня
    `service.py` этот параметр не передаёт) — преподавателю отказ до всего остального. Отказ на любом шаге —
    `LlmAdapterRefusal`; успешный ответ — тело `forward()` разобрано как JSON и провалидировано через
    `models.LlmSelection.model_validate` (невалидный JSON/схема — тоже `LlmAdapterRefusal`, не `ValidationError`
    наружу)."""
    if identity.role == "teacher" and not teacher_payer.strip():
        raise LlmAdapterRefusal(TEACHER_PAYER_REFUSAL)
    if role.config_error:
        raise LlmAdapterRefusal("config")
    if role.stopped:
        raise LlmAdapterRefusal("stopped")
    if role.upstream is None:
        raise LlmAdapterRefusal("key_missing")
    policy = select_model(role, analytics_models)
    try:
        body = rebuild_body(raw_body, policy, caller=identity.caller, session=session,
                            max_tokens_cap=role.llm.LLM_MAX_TOKENS)
    except BodyRefused as exc:
        raise LlmAdapterRefusal("body") from exc
    estimate = policy.estimate_usd(body_bytes(body), body["max_tokens"])
    if role.ledger_error() is not None:          # журнал не пишется: сразу, без резерва (как llm/api.py::chat)
        raise LlmAdapterRefusal("ledger")
    # --- один синхронный участок без await (мастер-промпт §9: «атомарный участок без await между проверкой и
    # резервом») — ровно порядок llm/api.py::chat, шаги 7–8 докстринга модуля ---
    if role.inflight.get(identity.caller, 0) >= role.llm.LLM_CALLER_CONCURRENCY:
        raise LlmAdapterRefusal("busy")
    spent = role.budget.refusal(identity.caller)
    if spent is not None:
        raise LlmAdapterRefusal(spent)
    call = role.begin_call(caller=identity.caller, session=session, parent=None, model=policy.model, step=0,
                           estimate=estimate)
    # --- конец синхронного участка: резерв стоит в журнале до единственного await ниже ---
    response = await forward(role, call, body, raw_body)
    return _parse_selection(response)
