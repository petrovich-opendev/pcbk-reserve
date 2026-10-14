"""Координатор заданий аналитики КДМ-1 (Task 4; план §Task 4/Этап 4; мастер-промпт §7, §10).

Единственная точка, которую вызывает `gateway/analytics_api.py` — маршруты не обращаются к `store.py`/
`preanalysis.py`/`llm_adapter.py` напрямую (тот же приём, что `gateway/calc_api.py` зовёт только `DataService`,
не SQL сам). Поток запроса (`docs/kdm1/INTERFACES.md`):

    API → service.py → preanalysis.py → retrieval.py/calculations.py → (llm_adapter.py) → validator.py →
    rendering.py → store.py

Конструктор без ввода-вывода (Договор Role); `open()` зовётся из lifespan шлюза: открывает корпус
(`knowledge.open_corpus`, в потоке) и состояние (`store.AnalyticsStore.open`, тоже в потоке). Сбой корпуса или
состояния не бросает из lifespan (мастер-промпт §4: «ошибка корпуса или аналитическая база не ломают вход, чат,
калькуляторы, уроки») — `self.config_error`/`self.corpus_error` показывают причину, маршруты отвечают 503, а не
роняют процесс.

Асинхронные задания (`queued → preanalysis → retrieving → generating → validating → completed`, плюс
`needs_clarification`/`limited_answer`/`cancelled`/`failed`, мастер-промпт §10) — фоновая задача на запрос:
`submit_question` создаёт запись в `store.py` и возвращает `request_id` сразу (клиенту — 202), `_run_job`
работает в фоне и публикует результат атомарно (`store.publish_response`). Версия/права между поиском и выдачей
проверяются повторно (§10) — `_run_job` перечитывает владение диалогом перед публикацией и не кэширует
`identity`/пакет дольше одного прохода (`self._pending`, забирается `pop`, не остаётся между заданиями).

Решения преданализа `needs_clarification`/`limited_answer`/`refused` переводят стадию задания в `validating`
(`store.set_preanalysis`) — это НЕ терминальная стадия сама по себе (приём `store.py`, см. его докстринг):
терминальную стадию и клиентский `JobResponse` для всех четырёх исходов преданализа (включая `ready`) даёт
только `publish_response`. Поэтому `_run_job` для всех «не-ready» решений идёт от `set_preanalysis` до
`rendering.render_response`/`store.publish_response` без промежуточных точек ожидания (никакого `await` внешней
службы между ними) — задание либо долетает до терминальной стадии одним проходом, либо падает целиком в `except`
`_guarded_run_job` и становится `failed` сторожевым сроком/обработчиком ошибки, а не застревает в `validating`
навечно. Сторожевой срок одного задания — `AnalyticsSettings.ANALYTICS_JOB_TIMEOUT_S`: зависшее задание (в т.ч.
платный вызов без ответа) переводится в `failed` сторожем `_guarded_run_job`, повторной оплаты это не делает —
клиентский повтор того же `client_request_id` (идемпотентность `store.create_request`) и любая новая отправка
идут отдельным заданием, а зависшее само уже не публикует ответ (терминальная стадия `failed` блокирует его
`publish_response` при завершении, ОСОБЫЙ ФОКУС ПРОВЕРКИ №3 плана).

Точка вызова LLM (`llm_adapter.py`, Task 5) — в ветке `ready` ниже, по умолчанию выключена: `self.llm` не
подключается из `main.py` в Task 4 сознательно (см. `build_roles`), чтобы `capabilities().llm_available` не
обещала того, чего реально нет (`llm_adapter.py` сам пока — заглушка Task 5). Как только Task 5 реализует
`llm_adapter.py`, `main.py` подключает `llm=llm` в `AnalyticsService` — код этого модуля менять не нужно: отказ
`LlmAdapterRefusal`/`NotImplementedError` уже сейчас перехватывается здесь и даёт откат на справку без LLM
(карточки показываются целиком из разрешённого пакета преданализа), не падение задания.

GET `.../evidence/{id}`: `evidence_id` — составной `request_id:pack_id:item_id` (`encode_evidence_id`/
`EVIDENCE_SEP` ниже). Цепочка прав из мастер-промпта §10 («пакет принадлежит заданию, задание — диалогу, диалог
— identity») проверяется ДВАЖДЫ: `request_id` → `store.get_request(...).owner_login` (своё ли задание), и
`pack_id` → `store.evidence_pack_owner(pack_id) == request_id` (Правка, раунд 1, Important 3: свой `request_id`
с ЧУЖИМ `pack_id` в составном id без этой второй проверки проходил бы). `preanalysis.build_evidence_pack` кладёт
в `EvidenceRef.fragment_id` «сырой» id фрагмента корпуса (его докстринг: «`evidence_pack_id` всегда `None` —
`service.py` заполняет реальный id после `store.save_evidence_pack`») — `_persist_evidence` ниже сохраняет
неизменяемый пакет ДО публикации ответа и переписывает `fragment_id` каждой секции в составной `evidence_id`
(Правка, раунд 1, Important 2): без этого шага GET `.../evidence/{id}` никогда не находит то, что показал
клиенту ответ — веб (`web/src/components/AnalyticsSources.tsx`) зовёт ручку ровно с `fragmentId` как есть.

Путь без LLM (по умолчанию единственный живой сейчас) и путь «LLM недоступен сейчас» (`_select_with_llm` → `None`)
НЕ публикуют `allowed_claim_ids`/`allowed_mechanism_ids` преданализа напрямую (Правка, раунд 1, Critical):
`_deterministic_outcome` строит синтетический `LlmSelection` ровно из разрешённого пакета и проводит его через
ТУ ЖЕ `validator.validate_selection`, что настоящий выбор модели — повторная проверка «в момент выдачи»
(`publication_state`/`critical_local_fact`/версия корпуса ссылки/PUB-10-11) не обходится детерминированным
путём. Если после проверки не осталось ни одной карточки — ответ `limited_answer`, не пустой `completed`.

Правка итогового ревью F4a (14.10, `.superpowers/sdd/PLAN-KDM1-analytics-2026-10-14/final-fix-F4a-brief.md`) —
пять дополнений, не меняющих решения выше:

1. *Свежая проверка в момент выдачи (ОСОБЫЙ ФОКУС №2, усиление).* `_build_outcome` перечитывает карточки
   пакета из корпуса по их id (`_refresh_pack`: `knowledge.get_rows` + `preanalysis._build_card`)
   НЕПОСРЕДСТВЕННО перед `validator.validate_selection` — снимок `EvidenceCard`, собранный `preanalysis.analyze`
   во время поиска, не ловил отзыв `publication_state`/правку текста между поиском и этой проверкой; теперь
   проверяется и показывается только состояние «здесь и сейчас» — отозванная/удалённая карточка не выходит из
   `_refresh_pack` вовсе.
2. *Смена версии корпуса без перезапуска.* `knowledge.version_signature` — дешёвый признак текущей версии на
   диске (путь+размер+mtime выбранного файла, без хеширования); `_maybe_reopen_corpus` сверяет его в начале
   КАЖДОГО задания и переоткрывает корпус (`knowledge.open_corpus` — дорогая операция, хеширует файл целиком),
   когда признак изменился (`docs/kdm1/CORPUS_OPERATIONS.md`, раздел «новая версия подхватывается только после
   пересоздания core», обновлён в этом же коммите — больше не верен). `_run_job` ДОПОЛНИТЕЛЬНО сверяет признак
   ПЕРЕД публикацией с тем, с которым это задание стартовало (`job_signature`): изменился между поиском и
   выдачей (платный вызов занял время) → не публикует карточки как ни в чём не бывало — `limited_answer` с
   единственной секцией `limitation` «версия корпуса сменилась», без карточек.
3. *Исторический ответ при чтении.* `_visible_response`/`_with_historical_notice` — `get_request`/
   `list_requests` добавляют (не сохраняя) последней секцией `limitation`, если `response.corpus_version` уже
   отличается от действующей версии корпуса на момент ЧТЕНИЯ; предел секций контракта — секция не добавляется,
   строка в журнал.
4. *Занятость одного диалога.* `self._llm_inflight_conversations` — не больше одного платного вызова модели
   одновременно на диалог; второй вопрос того же диалога, пока первый в вызове модели, И предел параллельности
   самого вызывающего (`LlmAdapterRefusal("busy")`, `llm_adapter.request_selection`) — оба пути отдают справку
   без модели с `error_code="busy"` и отличимой секцией `limitation` (`_select_with_llm`/`_build_outcome`).
5. *Смешанный вопрос (вместе с F4b).* Упоминание другой машины в исходном тексте вопроса (`preanalysis.
   _mentions_other_machine`) при решении, которое не `refused` (F4b отвечает за то, что такое решение вообще
   возникает, когда в вопросе есть допустимая часть про КДМ-1) — первой секцией `limitation` с названием чужой
   машины.

Дополнение контроллера к правке F4a (14.10, после коммита F4b `74172d1`) — два дополнения поверх пп.1/4 выше:

6. *Подписи кандидатов термина (H-26, стык с F4b).* Ветка `needs_clarification` теперь зовёт `preanalysis.
   clarification_candidates(handle, parsed, resolved_terms)` (`handle` — новый обязательный параметр
   `_build_outcome`, тот же, что держит `_run_job`, не повторное чтение `self._handle`, см. `_refresh_pack`
   ниже) и передаёт результат в `rendering.render_clarification_section(keys, candidate_labels=...)` (параметр
   F4b, умолчание `()` не меняет остальные вызовы этой функции в модуле).
7. *Честное отключение LLM по политике экспорта.* `AnalyticsSettings.ANALYTICS_LLM_EXPORT_POLICY == "none"` —
   сервер-уровневый выключатель (отдельный от per-запросного `Preanalysis.llm_export_policy`, который
   `preanalysis.py` выставляет за каждое решение преданализа) — `capabilities().llm_available=False` И
   `_build_outcome` не вызывает `_select_with_llm` вовсе, та же проверка в обоих местах, не расходятся.

Итоговая правка F6 (14.10.2026) — политика передачи в LLM, решение владельца (дословно): «4. Разрешен полный
контент в тч внутренний и данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в
модель!». Владелец (заказчик и администратор стенда ПЦБК) дважды явно разрешил `approved_subset` — полный
контент пакета (вопрос и тексты карточек, включая внутренний и данные БДРВ, если они есть в пакете) в запросе
к модели. Модель остаётся существующей LLM-ролью приложения через OpenRouter — учёт, бюджет и журнал не
меняются этой правкой.

8. *Политика экспорта подставляется перед вызовом адаптера, не раньше.* `_select_with_llm` подставляет
   `self.settings.ANALYTICS_LLM_EXPORT_POLICY` в `Preanalysis.llm_export_policy` НЕПОСРЕДСТВЕННО перед
   `llm_adapter.build_raw_body` (`analysis.model_copy(update={...})`, см. её докстринг) — не в
   `preanalysis.py` (эта правка его не трогает, он по-прежнему всегда ставит `public_only`/`none`) и не в
   самом контракте `Preanalysis` (`models.LlmExportPolicy` уже несла все три значения). `intent == "control"`
   — по-прежнему без модели вовсе: ветка `_build_outcome` для `control` возвращает ответ раньше, чем доходит
   до `self.llm`/`_select_with_llm` (см. её код выше), поэтому настройка `ANALYTICS_LLM_EXPORT_POLICY` на эту
   ветку не влияет ни при каком значении."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import calculations, data_bridge, knowledge, llm_adapter, preanalysis, rendering, reviews, terms, validator
from .models import JOB_TERMINAL_STATES, CalculationResult, LlmSelection, Section, ValidationOutcome
from .reviews import ReviewStore, ReviewStoreError
from .store import AnalyticsStore
from ..data.sql import SAFE_NAME

if TYPE_CHECKING:
    from ..data.service import DataService
    from ..llm import LlmRole
    from .knowledge import CorpusHandle
    from .models import AnalysisMode, AnalysisRequest, EvidenceRef, JobResponse, RequestIdentity
    from .reviews import ReviewDecision, ReviewKind, ReviewRequest
    from .settings import AnalyticsSettings
    from .store import ConversationMeta, RequestRecord

log = logging.getLogger("pcbk_core.analytics")

# ограничение параллелизма заданий (план, Этап 4: «ограничение параллелизма»; масштаб 10 мест — запас в 4 разом
# не даёт редкому наплыву вопросов упереться в сеть OpenRouter всеми сразу, прочие ждут в "queued")
MAX_CONCURRENT_JOBS = 4
# телеметрия БДРВ в ответе (план Д17, Task 1): не больше этого числа подписей-с-согласованной-связью читается
# за один ответ — остальные отмечает render_telemetry_truncated_section, не падение и не тихое молчание
MAX_TELEMETRY_LABELS = 3
# составной evidence_id GET .../evidence/{id} — см. докстринг модуля
EVIDENCE_SEP = ":"
_CONTRACTS_DIR = Path(__file__).resolve().parent / "contracts"
# правка F1, п.3: раз в час — вызывается фоновой задачей (`AnalyticsService._retention_loop`), первый проход —
# синхронно в `open()`; модульная константа (не метод/атрибут) — тест подменяет её `monkeypatch.setattr`
# (значение читается из модуля на каждой итерации цикла, не копируется при запуске)
RETENTION_SWEEP_INTERVAL_S = 3600.0

# job-response.schema.json: JobResponse.sections maxItems — зеркало предела контракта (rendering._MAX_SECTIONS
# принадлежит F4b на этой ветке, не импортируется отсюда; свой дубль, та же цифра из того же контракта)
_JOB_RESPONSE_MAX_SECTIONS = 24

# тексты правки F4a пп.2/4 (ниже) — фиксированные серверные формулировки, не текст модели/пользователя
_BUSY_LIMITATION_TEXT = ("В этом диалоге уже готовится ответ с разбором — эта справка без разбора; повторите "
                         "позже.")
CORPUS_VERSION_CHANGED_TEXT = "Версия корпуса сменилась во время подготовки ответа — задайте вопрос ещё раз."


def _limitation_section(text: str) -> "Section":
    """Секция `limitation` с фиксированным серверным текстом (правки F4a, пп.2/4/5) — `rendering.py` принадлежит
    F4b на этой ветке (`final-fix-common.md`: «не трогай их файлы») и сегодня не даёт готового шаблона под эти
    три случая (занятость, смена версии корпуса, смешанный вопрос); секция строится прямо здесь, тем же приёмом,
    что `rendering.render_control_refusal_section` (`Section(kind="limitation", ...)`) — весь текст идёт из
    констант этого модуля или из детерминированной регулярной проверки `preanalysis.py` (название чужой машины,
    `_other_machine_limitation_text`), не из текста вопроса/ответа модели как есть."""
    return Section(kind="limitation", text=text[:4000], evidence_refs=[], rendered_by="server", claim_id=None)


def _other_machine_limitation_text(question: str) -> str:
    """Правка F4a, п.5 (вместе с F4b): название(я) чужой машины, упомянутой в исходном тексте вопроса, для
    текста ограничения — та же регулярка, что `preanalysis._mentions_other_machine` (вызывается напрямую по
    заданию координатора, см. `final-fix-F4a-brief.md`), без собственного решения, какая пара код+номер считается
    «другой машиной» (это решает `preanalysis.py`, F4b его не меняет здесь). Несколько разных кодов — через
    запятую, в порядке первого упоминания в тексте; не нашли ни одного явного кода (например, упоминание по
    обычному названию без номера) — общая формулировка без перечисления."""
    codes: list[str] = []
    for match in preanalysis._MACHINE_CODE_RE.finditer(question):
        code, number = match.group(1).upper(), match.group(2)
        if code.lower() == preanalysis._IN_SCOPE_MACHINE_CODE and number == preanalysis._IN_SCOPE_MACHINE_NUMBER:
            continue
        label = f"{code}-{number}"
        if label not in codes:
            codes.append(label)
    machines = ", ".join(codes)
    if machines:
        return (f"Часть вопроса, относящаяся к другой машине ({machines}), вне области КДМ-1 и не "
                "рассматривается.")
    return "Часть вопроса, относящаяся к другой машине, вне области КДМ-1 и не рассматривается."


def _historical_notice_text(record_version: str, current_version: str) -> str:
    """Правка F4a, п.3 (мастер-промпт §6: «старые ответы остаются историческими с предупреждением»)."""
    return (f"Ответ получен по версии корпуса {record_version}; действующая версия — {current_version}. Ответ "
            "исторический.")


def encode_evidence_id(request_id: str, pack_id: str, item_id: str) -> str:
    """Составной id для GET `.../evidence/{id}` (см. докстринг модуля): `request_id` несёт цепочку прав
    (задание→диалог→identity, через `store.get_request(request_id).owner_login`), `pack_id`/`item_id` — ключ
    `store.get_evidence_item`. `request_id`/`pack_id` — `secrets.token_hex(16)` (только hex), поэтому разбор
    `evidence_id.split(EVIDENCE_SEP, 2)` всегда однозначен, даже если `item_id` корпуса сам содержит `:`."""
    return f"{request_id}{EVIDENCE_SEP}{pack_id}{EVIDENCE_SEP}{item_id}"


class AnalyticsUnavailable(Exception):
    """Аналитика недоступна целиком (корпус/состояние не открыты) — `gateway/analytics_api.py` превращает в 503;
    текст — без путей хоста (как `knowledge.CorpusError`/`store.StoreError`)."""


@dataclass(frozen=True)
class Capabilities:
    """Ответ GET .../capabilities (мастер-промпт §10): что доступно именно этому вызывающему прямо сейчас,
    не общая конфигурация сервера."""

    reference_available: bool          # справка без LLM (корпус/состояние открыты)
    llm_available: bool                # оплачиваемый разбор (role.llm не None, не stopped, есть плательщик)
    data_bridge_available: bool        # calc подключён и у его роли нет config_error (план Д17, Task 1) — см. capabilities()
    review_authority: bool             # у identity есть хоть одно действующее полномочие согласования
    corpus_version: str | None


def _terminal_outcome(clarification_keys: tuple[str, ...] = (), *, accepted_claim_ids: tuple[str, ...] = (),
                      accepted_mechanism_ids: tuple[str, ...] = (), refused: bool = False,
                      error_code: str | None = None) -> ValidationOutcome:
    """`ValidationOutcome` без прохода через `validator.py` — для веток, которые LLM не затрагивал вовсе
    (`needs_clarification`/`refused`, и путь `ready`/`limited_answer` без LLM, Task 4: карточки уже отобраны
    серверным преданализом, а не предложением модели, поэтому «проверять предложение модели» здесь нет чего)."""
    return ValidationOutcome(accepted_claim_ids=accepted_claim_ids, accepted_mechanism_ids=accepted_mechanism_ids,
                             rejected=(), clarification_keys=tuple(clarification_keys), refused=refused,
                             error_code=error_code)


def _llm_selection_schema() -> dict:
    return json.loads((_CONTRACTS_DIR / "llm-selection.schema.json").read_text(encoding="utf-8"))


# пределы contracts/llm-selection.schema.json (LlmSelection.reference_claim_ids/mechanism_ids maxItems) —
# синтетический выбор без LLM обязан уместиться в тот же контракт, что настоящий выбор модели
_MAX_SYNTHETIC_CLAIMS = 20
_MAX_SYNTHETIC_MECHANISMS = 6


def _deterministic_outcome(analysis, pack: tuple, approved_card_ids: frozenset[str] = frozenset()
                           ) -> ValidationOutcome:
    """Путь без LLM и путь «LLM недоступен сейчас» (см. докстринг модуля) — синтетический `LlmSelection` ровно
    из `analysis.allowed_claim_ids`/`allowed_mechanism_ids` (без обращения к модели), проведённый через ТУ ЖЕ
    `validator.validate_selection`, что настоящий выбор LLM (Правка, раунд 1, Critical): без этого карточка с
    неразрешённым `publication_state`/несогласованным `critical_local_fact`/ссылкой на ограниченный источник
    (PUB-10/11)/чужой версией корпуса в пакете оказалась бы в ответе только потому, что преданализ когда-то
    счёл её допустимой, а не потому, что право показа проверено «здесь и сейчас». Срез до предела контракта —
    без модели сервер не выбирает «лучшие» среди большего числа кандидатов, берёт первые по порядку пакета
    (детерминированно, тот же порядок, что `_render_pack_sections`).

    `approved_card_ids` — Task 7, шаг 2: id критических местных карточек с действующим согласованием
    (`AnalyticsService._approved_critical_card_ids`), проброшено в `validate_selection` как есть — на ОБОИХ
    путях (без LLM и настоящий выбор, `_select_with_llm`) критический факт проверяется одним и тем же
    множеством, собранным один раз на задание."""
    selection = LlmSelection(reference_claim_ids=list(analysis.allowed_claim_ids[:_MAX_SYNTHETIC_CLAIMS]),
                             mechanism_ids=list(analysis.allowed_mechanism_ids[:_MAX_SYNTHETIC_MECHANISMS]),
                             clarification_keys=[], calculation_ids=[])
    return validator.validate_selection(selection, analysis, pack, approved_card_ids)


def _recheck_outcome(outcome: ValidationOutcome, analysis, pack: tuple,
                     approved_card_ids: frozenset[str] = frozenset()) -> ValidationOutcome:
    """Повторная проверка уже принятого выбора модели по СВЕЖЕМУ пакету и согласованиям (правка F4a, раунд 1):
    принятые карточки ещё раз идут через `validator.validate_selection` — отозванная/исчезнувшая карточка или
    критический факт, чьё согласование сняли во время платного вызова, в ответ не попадают. Новых карточек
    здесь не появляется (только подмножество уже принятых); отказы обоих проходов сохраняются в `rejected`."""
    selection = LlmSelection(reference_claim_ids=list(outcome.accepted_claim_ids),
                             mechanism_ids=list(outcome.accepted_mechanism_ids),
                             clarification_keys=list(outcome.clarification_keys), calculation_ids=[])
    fresh = validator.validate_selection(selection, analysis, pack, approved_card_ids)
    return dataclasses.replace(fresh, rejected=tuple(outcome.rejected) + tuple(fresh.rejected),
                               error_code=fresh.error_code or outcome.error_code)


@dataclass(frozen=True)
class ConversationTurn:
    """Один вопрос-ответ истории диалога (GET `conversations/{id}`, Правка, раунд 1, Minor «д» — решение
    контроллера): владельцу диалога показывается и исходный текст его вопроса `question` (как хранится,
    `store.RequestRecord.question`), не только `response` — `JobResponse` остаётся закрытым контрактом
    (`job-response.schema.json`), лишнего поля в нём самом быть не может, поэтому вопрос идёт рядом, не внутри."""

    question: str
    response: "JobResponse"


@dataclass(frozen=True)
class EquipmentLinkInfo:
    """Один элемент `GET .../analytics/links` (план Д17, Task 1) — состояние строки `equipment_links` текущего
    корпуса по отношению к согласованной связи с историаном (`"unbound"`/`"pending"`/`"linked"`), не сама
    запись корпуса (`historian_tag`/`process_id` корпуса остаются `null` — связь живёт в `reviews.py`, не
    правит `kdm1.seed.sqlite3`, см. докстринг `reviews.ResolvedLink`)."""

    id: str
    display_label: str
    document_context: str
    state: str                            # "unbound" | "pending" | "linked"
    historian_tag: str | None
    approved_by: str | None
    approved_at: datetime | None
    pending_request_id: str | None


class AnalyticsService:
    """Координатор аналитики КДМ-1. Конструктор ничего не открывает (Договор Role) — см. `open()`."""

    def __init__(self, settings: "AnalyticsSettings", *, calc: "DataService | None" = None,
                 llm: "LlmRole | None" = None):
        """`settings` — `AnalyticsSettings` (Task 0); `calc` — служба данных роли «данные» (как у
        калькуляторов страницы, `None` — связь с оборудованием недоступна, справка работает); `llm` — роль
        LLM-прокси (`None` — оплачиваемый разбор недоступен, справка работает)."""
        self.settings = settings
        self.calc = calc
        self.llm = llm
        self.store = AnalyticsStore(settings.ANALYTICS_STATE_PATH)
        self.reviews: ReviewStore = ReviewStore(settings.ANALYTICS_STATE_PATH)   # Task 7, шаг 2: тот же файл
        self.config_error: str | None = None      # состояние (SQLite) не открылось
        self.corpus_error: str | None = None       # корпус не открылся/не читается
        self.reviews_error: str | None = None      # согласование (ReviewStore) не открылось — деградирует отдельно
        self._handle: "CorpusHandle | None" = None
        # задание → (identity, conversation_id, исходный AnalysisRequest): живёт только на время обработки
        # одного прохода (мастер-промпт §10: «не кэширует identity/права дольше одного прохода»), не персистентно
        self._pending: dict[str, tuple["RequestIdentity", str, "AnalysisRequest"]] = {}
        self._jobs: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._retention_task: asyncio.Task | None = None   # правка F1, п.3 — см. open()/_retention_loop/aclose()
        # правка F4a, п.2: признак версии корпуса, с которым сейчас открыт self._handle (knowledge.
        # version_signature) — см. _maybe_reopen_corpus/_current_corpus_signature ниже
        self._corpus_signature: str | None = None
        # правка F4a, п.4: диалоги, у которых прямо сейчас идёт платный вызов модели — не больше одного на
        # диалог одновременно (set, не счётчик — конкретный conversation_id, не просто число)
        self._llm_inflight_conversations: set[str] = set()

    async def open(self) -> None:
        """Открыть состояние и корпус (lifespan шлюза, через `gateway/__init__.py::_open_analytics`). Не бросает:
        сбой — `self.config_error`/`self.corpus_error` и строка журнала, не исключение наружу (мастер-промпт
        §4)."""
        try:
            self._handle = await asyncio.to_thread(knowledge.open_corpus, self.settings.ANALYTICS_CORPUS_DIR)
        except knowledge.CorpusError as exc:
            self.corpus_error = str(exc)
        except Exception as exc:                                    # noqa: BLE001 — сбой не должен ронять lifespan
            self.corpus_error = type(exc).__name__
        if self.corpus_error:
            log.error("аналитика КДМ-1: корпус не открыт (%s)", self.corpus_error)
        else:
            # правка F4a, п.2: признак версии на момент старта процесса — база для _maybe_reopen_corpus на
            # первом задании (сбой признака здесь — не фатально: первое задание просто пересчитает его заново
            # и, если результат отличается от None, один раз выполнит безвредный «лишний» реопен)
            try:
                self._corpus_signature = await asyncio.to_thread(
                    knowledge.version_signature, self.settings.ANALYTICS_CORPUS_DIR)
            except Exception as exc:                                     # noqa: BLE001 — признак — не обязателен
                log.warning("аналитика КДМ-1: признак версии корпуса не посчитан при открытии (%s)",
                           type(exc).__name__)
        # согласование (ReviewStore, Task 7, шаг 2) — отдельный сбой, не блокирует/не блокируется основным
        # store.py (тот же файл, свои таблицы): пробуем независимо от того, открылся ли store ниже, чтобы
        # «ReviewStore сломан, AnalyticsStore жив» и наоборот деградировали раздельно (докстринг модуля, §4)
        if not await asyncio.to_thread(self.reviews.open):
            self.reviews_error = self.reviews.last_error or "сбой"
            log.error("аналитика КДМ-1: согласование не открыто (%s)", self.reviews_error)
        opened = await asyncio.to_thread(self.store.open)
        if not opened:
            self.config_error = self.store.last_error or "сбой"
            log.error("аналитика КДМ-1: состояние не открыто (%s)", self.config_error)
            return
        try:
            reaped = await asyncio.to_thread(self.store.reap_unfinished_after_restart)
        except Exception as exc:                                     # noqa: BLE001
            log.warning("аналитика КДМ-1: обрыв незавершённых заданий после перезапуска (%s)", type(exc).__name__)
        else:
            if reaped:
                log.warning("аналитика КДМ-1: перезапуск — заданий прервано: %d", reaped)
        # правка F1, п.3 (мастер-промпт §4/§9: «история, экспорты и резервные копии имеют срок хранения») —
        # `store.sweep_retention` есть, но раньше никто не вызывал: один проход сразу (не ждать первого часа
        # после старта) и фоновая задача раз в `RETENTION_SWEEP_INTERVAL_S` дальше; отменяется в aclose().
        await self._sweep_retention_once()
        self._retention_task = asyncio.ensure_future(self._retention_loop())

    async def _sweep_retention_once(self) -> None:
        """Один проход `store.sweep_retention` — из `open()` сразу и из `_retention_loop` раз в час. Сбой —
        строка журнала, не исключение наружу (как ошибка корпуса/состояния выше, мастер-промпт §4): пропуск
        одной очистки не должен класть службу или фоновую задачу."""
        try:
            deleted_conversations, deleted_packs = await asyncio.to_thread(
                self.store.sweep_retention, datetime.now(timezone.utc),
                history_days=self.settings.ANALYTICS_HISTORY_RETENTION_DAYS,
                evidence_days=self.settings.ANALYTICS_EVIDENCE_RETENTION_DAYS)
        except Exception as exc:                                      # noqa: BLE001 — сбой очистки — не падение
            log.warning("аналитика КДМ-1: сбой применения сроков хранения (%s)", type(exc).__name__)
            return
        if deleted_conversations or deleted_packs:
            log.info("аналитика КДМ-1: сроки хранения — диалогов удалено %d, пакетов доказательств удалено %d",
                     deleted_conversations, deleted_packs)

    async def _retention_loop(self) -> None:
        """Фоновая задача: раз в `RETENTION_SWEEP_INTERVAL_S` (модульная константа, не атрибут — тест правки F1
        подменяет её `monkeypatch.setattr`) повторяет `_sweep_retention_once`. Отменяется в `aclose()` —
        `asyncio.CancelledError` на `asyncio.sleep` штатно всплывает и завершает задачу, без журнала (отмена при
        остановке роли — не сбой)."""
        while True:
            await asyncio.sleep(RETENTION_SWEEP_INTERVAL_S)
            await self._sweep_retention_once()

    async def _current_corpus_signature(self) -> str | None:
        """Правка F4a, п.2: дешёвый признак (`knowledge.version_signature`, путь+размер+mtime выбранного файла,
        без хеширования) — `None`, если признак не посчитался (файл/каталог временно недоступен и т.п.); сбой
        здесь не должен сам по себе ронять задание или блокировать ответ — вызывающие (`_maybe_reopen_corpus`/
        `_run_job`) трактуют `None` как «нечего сравнивать», не как «версия не менялась»."""
        try:
            return await asyncio.to_thread(knowledge.version_signature, self.settings.ANALYTICS_CORPUS_DIR)
        except Exception as exc:                                        # noqa: BLE001 — признак — не обязателен
            log.warning("аналитика КДМ-1: сбой признака версии корпуса (%s)", type(exc).__name__)
            return None

    async def _maybe_reopen_corpus(self) -> str | None:
        """Правка F4a, п.2 («смена версии корпуса без перезапуска процесса») — вызывается в начале КАЖДОГО
        задания (`_run_job`, до преданализа): признак изменился относительно уже открытого `self._handle` →
        переоткрыть корпус (`knowledge.open_corpus` — дорогая операция, хеширует файл целиком, поэтому не на
        каждый вопрос, только когда признак говорит, что файл сменился). Старый дескриптор закрывается
        (`knowledge.close` — симметрии ради; `knowledge.py` не хранит в `CorpusHandle` постоянного соединения,
        см. его докстринг, так что «закрыть, когда им никто не пользуется» выполнено сразу: ни у старого, ни у
        нового дескриптора нет общего ресурса, который кто-то мог бы держать открытым). Сбой переоткрытия —
        строка журнала, прежний дескриптор остаётся рабочим, не падение (мастер-промпт §4).

        Возвращает признак, с которым СТАРТУЕТ это задание (`job_signature` у вызывающего) — `None`, если
        признак не посчитался вовсе (тогда `_run_job` не выполняет повторную сверку перед публикацией, см. её
        докстринг: «нечего сравнивать» не должно само по себе блокировать ответ)."""
        signature = await self._current_corpus_signature()
        if signature is None or signature == self._corpus_signature:
            return self._corpus_signature
        try:
            new_handle = await asyncio.to_thread(knowledge.open_corpus, self.settings.ANALYTICS_CORPUS_DIR)
        except Exception as exc:                                        # noqa: BLE001 — сбой переоткрытия
            log.warning("аналитика КДМ-1: смена версии корпуса — переоткрытие не удалось (%s), используется "
                       "прежняя версия", type(exc).__name__)
            return self._corpus_signature
        old_handle, self._handle = self._handle, new_handle
        self._corpus_signature = signature
        if old_handle is not None:
            await asyncio.to_thread(knowledge.close, old_handle)
        log.info("аналитика КДМ-1: корпус переоткрыт без перезапуска — версия %s", new_handle.version)
        return signature

    async def aclose(self) -> None:
        """Остановка роли: отменить фоновые задания (в т.ч. фоновую очистку сроков хранения, правка F1, п.3),
        закрыть соединения состояния/корпуса."""
        tasks = list(self._jobs.values())
        if self._retention_task is not None:
            tasks.append(self._retention_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._handle is not None:
            await asyncio.to_thread(knowledge.close, self._handle)

    def capabilities(self, identity: "RequestIdentity") -> Capabilities:
        reference_available = self.corpus_error is None and self.config_error is None and self._handle is not None
        llm = self.llm
        llm_ok = (llm is not None and getattr(llm, "config_error", None) is None
                  and not getattr(llm, "stopped", True) and getattr(llm, "upstream", None) is not None)
        # дополнение контроллера к правке F4a (хвост F4b, 14.10): ANALYTICS_LLM_EXPORT_POLICY == "none" —
        # владелец отключил передачу вопросов модели целиком (сервер-уровневый выключатель, не per-запросный
        # Preanalysis.llm_export_policy, который preanalysis.py выставляет за каждое решение отдельно) — честное
        # "недоступно" здесь И в _build_outcome/_select_with_llm ниже, оба места смотрят на один и тот же флаг
        if llm_ok and self.settings.ANALYTICS_LLM_EXPORT_POLICY == "none":
            llm_ok = False
        if llm_ok and identity.role == "teacher":
            # мастер-промпт §6: без явно настроенного плательщика преподавателя платный разбор недоступен —
            # хвост Task 5 (коммит 830a782): ANALYTICS_TEACHER_PAYER непусто — владелец явно подтвердил, что
            # у преподавателя есть кому платить; пусто (умолчание) — честный отказ, как было до Task 5
            llm_ok = bool(self.settings.ANALYTICS_TEACHER_PAYER.strip())
        # F5 (решение владельца, 14.10.2026): ANALYTICS_REVIEWERS=all_students — любой student держит право
        # согласования без назначения, это настроечный факт (не чтение ReviewStore) — teacher остаётся
        # исключением (только по действующему полномочию, как в grants, reviews.py не трогает эту ветку)
        if identity.role == "student" and self.settings.ANALYTICS_REVIEWERS == "all_students":
            review_authority = True
        else:
            review_authority = False
            if self.reviews_error is None:
                try:
                    review_authority = self.reviews.has_any_active_grant(identity.login)
                except ReviewStoreError:
                    pass      # согласование деградировало между open() и этим вызовом — честные False, не падение
        # data_bridge_available (план Д17, Task 1): телеметрия (data_bridge.py) теперь выводится в ОТВЕТ
        # аналитики секцией telemetry_observation — контракт ответа её несёт, решение владельца по формату
        # секции и по полномочиям (согласование связи — отдельное, Task 7) уже принято. Снимает решение
        # контроллера правки F1, п.2 Д16 («ВСЕГДА False» — тогда контракт ответа ещё не нёс вид секции для
        # телеметрии). Доступность зависит только от того, подключена ли служба данных и исправна ли её роль —
        # НЕ от наличия хоть одной согласованной связи оборудования (`reviews.has_any_resolved_link` сюда не
        # входит: конкретную подпись без связи справка покажет как ограничение, не как недоступность службы).
        calc = self.calc
        calc_role = getattr(calc, "role", None)
        data_bridge_available = (calc is not None and calc_role is not None
                                 and getattr(calc_role, "config_error", None) is None)
        return Capabilities(reference_available=reference_available, llm_available=llm_ok,
                            data_bridge_available=data_bridge_available, review_authority=review_authority,
                            corpus_version=None if self._handle is None else self._handle.version)

    # --- диалоги и задания: ровно то, что вызывает gateway/analytics_api.py ---

    async def list_conversations(self, identity: "RequestIdentity") -> list["ConversationMeta"]:
        return await asyncio.to_thread(self.store.list_conversations, identity)

    async def create_conversation(self, identity: "RequestIdentity", mode: "AnalysisMode") -> str:
        if self._handle is None or self.config_error:
            raise AnalyticsUnavailable(self.corpus_error or self.config_error or "аналитика не готова")
        handle = self._handle
        return await asyncio.to_thread(self.store.create_conversation, identity, mode, handle.version)

    async def get_conversation(self, identity: "RequestIdentity",
                               conversation_id: str) -> "ConversationMeta | None":
        """Своя запись; чужая без права или несуществующая — `None` (чужой id не подтверждает существование
        ресурса, мастер-промпт §10)."""
        def _read() -> "ConversationMeta | None":
            if self.store.conversation_owner(conversation_id) != identity.login:
                return None
            for meta in self.store.list_conversations(identity):
                if meta.id == conversation_id:
                    return meta
            return None
        return await asyncio.to_thread(_read)

    async def list_requests(self, identity: "RequestIdentity",
                            conversation_id: str) -> list[ConversationTurn] | None:
        """История заданий диалога как `(вопрос, JobResponse)` (GET `conversations/{id}`) — `None`, если диалог
        чужой/не существует (как `get_conversation`). Правка, раунд 1, Minor «д»: `question` рядом с `response`,
        не только ответ — см. `ConversationTurn`."""
        def _read() -> list[ConversationTurn] | None:
            if self.store.conversation_owner(conversation_id) != identity.login:
                return None
            return [ConversationTurn(question=r.question, response=self._visible_response(r))
                   for r in self.store.list_requests(conversation_id)]
        return await asyncio.to_thread(_read)

    async def submit_question(self, identity: "RequestIdentity", conversation_id: str,
                              request: "AnalysisRequest") -> str:
        """Короткое создание задания (мастер-промпт §10): проверка владения `conversation_id`, идемпотентность
        по `client_request_id` (`store.create_request`), запуск фоновой задачи `_run_job` и немедленный
        возврат `request_id` — сам разбор сюда не входит."""
        if self._handle is None or self.config_error:
            raise AnalyticsUnavailable(self.corpus_error or self.config_error or "аналитика не готова")
        request_id, created = await asyncio.to_thread(self.store.create_request, identity, conversation_id,
                                                       request)
        if created:
            self._pending[request_id] = (identity, conversation_id, request)
            task = asyncio.ensure_future(self._guarded_run_job(request_id))
            self._jobs[request_id] = task
            task.add_done_callback(lambda _t, rid=request_id: self._jobs.pop(rid, None))
            log.info("аналитика КДМ-1: задание создано request=%s mode=%s", request_id, request.mode)
        return request_id

    async def get_request(self, identity: "RequestIdentity", request_id: str) -> "JobResponse | None":
        """Текущая стадия/результат своего задания; чужое — `None` (как `get_conversation`)."""
        def _read() -> "JobResponse | None":
            record = self.store.get_request(request_id)
            if record is None or record.owner_login != identity.login:
                return None
            return self._visible_response(record)
        return await asyncio.to_thread(_read)

    async def cancel_request(self, identity: "RequestIdentity", request_id: str) -> bool:
        """Отменить своё незавершённое задание (`store.cancel_request`) и прервать фоновую задачу, если она
        ещё не дошла до `generating`/`validating`: платный вызов, уже переданный в `llm_adapter.py`, не
        прерывается на полуслове — его доводит до конца `forward()`, которая отмену несёт сама.

        Правка, раунд 1, Important 4: решение «прерывать ли фоновую `asyncio.Task`» опирается на `stage_before`
        из `store.cancel_request` — ту же транзакцию, что сама отмена, — а не на отдельное более раннее чтение
        стадии (прежний приём держал окно гонки: задание успевало перейти в `generating` между своим снимком
        стадии и вызовом отмены)."""
        cancelled, stage_before = await asyncio.to_thread(self.store.cancel_request, identity, request_id)
        if cancelled and stage_before not in ("generating", "validating"):
            task = self._jobs.get(request_id)
            if task is not None and not task.done():
                task.cancel()
        if cancelled:
            log.info("аналитика КДМ-1: задание отменено request=%s", request_id)
        return cancelled

    async def get_evidence(self, identity: "RequestIdentity", evidence_id: str) -> dict | None:
        """Один элемент пакета доказательств (`GET .../evidence/{id}`) с повторной проверкой прав
        (мастер-промпт §10): пакет принадлежит заданию, задание — диалогу, диалог — `identity`. `evidence_id` —
        составной `request_id:pack_id:item_id` (`encode_evidence_id`, см. докстринг модуля).

        Правка, раунд 1, Important 3: `pack_id` сверяется с `request_id` через `store.evidence_pack_owner` —
        свой `request_id`, но ЧУЖОЙ `pack_id` в составном id (подмена) без этой проверки проходил бы, потому что
        владение заданием уже подтверждено отдельно."""
        parts = evidence_id.split(EVIDENCE_SEP, 2)
        if len(parts) != 3 or not all(parts):
            return None
        request_id, pack_id, item_id = parts

        def _read() -> dict | None:
            record = self.store.get_request(request_id)
            if record is None or record.owner_login != identity.login:
                return None
            if self.store.evidence_pack_owner(pack_id) != request_id:
                return None
            return self.store.get_evidence_item(pack_id, item_id)
        return await asyncio.to_thread(_read)

    # --- назначенное согласование (Task 7, шаг 2): тонкий проброс в ReviewStore, вся политика — там ---

    async def list_review_requests(self, identity: "RequestIdentity") -> list["ReviewRequest"]:
        """`reviews.list_requests(identity)` — своя фильтрация видимости уже внутри (создатель видит свои,
        держатель полномочия — все в его области, посторонний — пусто); эта обёртка не сужает и не
        расширяет результат. `reviewers_mode` — текущая настройка `ANALYTICS_REVIEWERS` (F5, решение
        владельца 14.10.2026): `all_students` — любой `student` видит все заявки области КДМ-1."""
        return await asyncio.to_thread(self.reviews.list_requests, identity,
                                       reviewers_mode=self.settings.ANALYTICS_REVIEWERS)

    async def create_review_request(self, identity: "RequestIdentity", kind: "ReviewKind", subject_id: str,
                                    new_value: str, scope: str, rationale: str, *,
                                    old_value: str | None = None, period_start: "datetime | None" = None,
                                    period_end: "datetime | None" = None) -> "ReviewRequest":
        """Создать заявку от имени вызывающего (`created_by=identity`) — любой вошедший технолог/
        преподаватель, без требования полномочия (`reviews.create_request`, см. её докстринг: «может
        отправить любой увидевший конфликт/подпись»)."""
        return await asyncio.to_thread(self.reviews.create_request, kind, subject_id, new_value, scope,
                                       rationale, created_by=identity, old_value=old_value,
                                       period_start=period_start, period_end=period_end)

    async def decide_review_request(self, identity: "RequestIdentity", review_id: str,
                                    decision: "ReviewDecision", comment: str) -> "ReviewRequest":
        """Решить заявку от имени вызывающего (`decided_by=identity`) — право/самоне решение проверяет
        `reviews.decide` (`PermissionError`/`ValueError`/`ReviewStoreError` уходят как есть, `gateway/
        analytics_api.py` сопоставляет их с 403/409/404; `ReviewStoreUnavailable`, F5 — 503). `reviewers_mode`
        — текущая настройка `ANALYTICS_REVIEWERS` (F5, решение владельца 14.10.2026): `all_students` — любой
        `student` решает любую заявку без назначения, в одно действие; `teacher` остаётся исключением."""
        return await asyncio.to_thread(self.reviews.decide, review_id, decision, comment, decided_by=identity,
                                       reviewers_mode=self.settings.ANALYTICS_REVIEWERS)

    # --- связи оборудования с историаном (план Д17, Task 1): раздел «Связи с историаном» вкладки ---

    async def list_equipment_links(self, identity: "RequestIdentity") -> list["EquipmentLinkInfo"]:
        """`GET .../analytics/links` — состояние каждой строки `equipment_links` текущего корпуса, в порядке
        корпуса (по `id`). `linked` — есть `reviews.resolved_link`; `pending` — связи нет, но есть заявка
        `kind="equipment_link"` этого `id` в статусе `pending`/`info_requested`, ВИДИМАЯ `identity` (та же
        видимость, что `reviews.list_requests` даёт самому вызывающему — `reviews.py` не меняется этой задачей,
        своего «публичного» листинга заявок мимо видимости у него нет); иначе `unbound`."""
        handle = self._handle
        if handle is None:
            raise AnalyticsUnavailable(self.corpus_error or "аналитика не готова")

        def _read() -> list[EquipmentLinkInfo]:
            try:
                ids = knowledge.all_ids(handle, "equipment_links")
                rows = knowledge.get_rows(handle, "equipment_links", ids)
            except knowledge.CorpusError as exc:
                raise AnalyticsUnavailable(str(exc)) from exc
            pending_by_subject: dict[str, str] = {}
            for req in self.reviews.list_requests(identity, reviewers_mode=self.settings.ANALYTICS_REVIEWERS):
                if (req.kind == "equipment_link" and req.status in ("pending", "info_requested")
                        and req.subject_id not in pending_by_subject):
                    pending_by_subject[req.subject_id] = req.id
            result: list[EquipmentLinkInfo] = []
            for id_ in ids:
                row = rows.get(id_)
                if row is None:
                    continue
                label, context = row.get("display_label") or "", row.get("document_context") or ""
                link = self.reviews.resolved_link(id_, reviewers_mode=self.settings.ANALYTICS_REVIEWERS)
                if link is not None:
                    result.append(EquipmentLinkInfo(id=id_, display_label=label, document_context=context,
                                                     state="linked", historian_tag=link.historian_tag,
                                                     approved_by=link.approved_by, approved_at=link.approved_at,
                                                     pending_request_id=None))
                elif id_ in pending_by_subject:
                    result.append(EquipmentLinkInfo(id=id_, display_label=label, document_context=context,
                                                     state="pending", historian_tag=None, approved_by=None,
                                                     approved_at=None, pending_request_id=pending_by_subject[id_]))
                else:
                    result.append(EquipmentLinkInfo(id=id_, display_label=label, document_context=context,
                                                     state="unbound", historian_tag=None, approved_by=None,
                                                     approved_at=None, pending_request_id=None))
            return result
        return await asyncio.to_thread(_read)

    async def link_candidates(self, identity: "RequestIdentity", link_id: str) -> dict | None:
        """`GET .../analytics/links/{id}/candidates` — один `data_bridge.catalog_search(display_label,
        limit=10)` по подписи строки `equipment_links`: справочный поиск «что сейчас доступно», не
        подтверждение связи (точное совпадение строки само по себе не включает чтение, мастер-промпт §7, этап
        7). Неизвестный `link_id` — `None` (ручка отвечает 404). `self.calc is None` (служба данных не
        подключена) — `AnalyticsUnavailable` (ручка отвечает тем же 503 `_unavailable`, что и прочие сбои
        подсистемы)."""
        handle = self._handle
        if handle is None:
            raise AnalyticsUnavailable(self.corpus_error or "аналитика не готова")
        try:
            row = await asyncio.to_thread(knowledge.get_row, handle, "equipment_links", link_id)
        except knowledge.CorpusError as exc:
            raise AnalyticsUnavailable(str(exc)) from exc
        if row is None:
            return None
        if self.calc is None:
            raise AnalyticsUnavailable("служба данных не подключена")
        label = row.get("display_label") or ""
        reply = await data_bridge.catalog_search(self.calc, identity, label, 10)
        matches = reply.get("matches") or []
        return {"status": reply.get("status"), "message": reply.get("message"),
               "candidates": [{"tag": m.get("tag"), "description": m.get("description"), "unit": m.get("unit")}
                              for m in matches]}

    async def validate_equipment_link(self, subject_id: str, new_value: str) -> bool:
        """`POST reviews/requests` с `kind="equipment_link"` (план Д17, Task 1): `subject_id` обязан быть `id`
        строки `equipment_links` ТЕКУЩЕГО корпуса, `new_value` — именем тега по `data.sql.SAFE_NAME` — иначе
        `False` (ручка отвечает 400): заявка на связь не должна нести ни мусорное имя тега, ни ссылку на
        несуществующую строку корпуса. `AnalyticsUnavailable` — корпус не открыт (посчитать нечего, не тихий
        `True`)."""
        if SAFE_NAME.fullmatch(new_value) is None:
            return False
        handle = self._handle
        if handle is None:
            raise AnalyticsUnavailable(self.corpus_error or "аналитика не готова")
        try:
            row = await asyncio.to_thread(knowledge.get_row, handle, "equipment_links", subject_id)
        except knowledge.CorpusError as exc:
            raise AnalyticsUnavailable(str(exc)) from exc
        return row is not None

    async def _approved_critical_card_ids(self, pack: tuple, scope: str) -> frozenset[str]:
        """Для каждой `critical_local_fact` карточки пакета — действующее согласование `reviews.
        approved_claim(card.id, scope, now, reviewers_mode=...)` (Task 7, шаг 2, дополнение контроллера;
        `reviewers_mode` — F5, решение владельца 14.10.2026): перепроверяется заново на каждое задание, не
        кэшируется дольше одного прохода (тот же принцип, что `self._pending`, докстринг модуля). Сбой
        `ReviewStore` — карточка просто не проходит (пустое множество для нечитаемых), не падение задания:
        критический факт без подтверждённого согласования и так не прошёл бы."""
        critical_ids = tuple(card.id for card in pack if getattr(card, "critical_local_fact", False))
        if not critical_ids:
            return frozenset()

        def _check() -> frozenset[str]:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            approved: set[str] = set()
            for card_id in critical_ids:
                try:
                    if self.reviews.approved_claim(
                            card_id, scope, now, reviewers_mode=self.settings.ANALYTICS_REVIEWERS) is not None:
                        approved.add(card_id)
                except ReviewStoreError:
                    continue
            return frozenset(approved)
        return await asyncio.to_thread(_check)

    # --- вспомогательное: стадия задания → клиентский JobResponse ---

    def _visible_response(self, record: "RequestRecord") -> "JobResponse":
        """Опубликованный ответ — через `_with_historical_notice` (правка F4a, п.3); незавершённое/зависшее-без-
        публикации задание — минимальный «ответ о статусе» прямо из текущей стадии (`sections`/
        `clarification_keys` пустые, без обращения к `rendering.py`: ждать его нечего, пока сам ответ не
        опубликован) — такому синтетическому ответу историческая пометка не нужна (он и так отражает текущее
        состояние задания, не сохранённый прошлый `corpus_version`)."""
        if record.response is not None:
            return self._with_historical_notice(record.response)
        from .models import JobResponse  # локально: модуль не нужен при каждом импорте service.py
        error_code = "unavailable" if record.state == "failed" else None
        return JobResponse(request_id=record.id, conversation_id=record.conversation_id, state=record.state,
                           corpus_version=record.corpus_version, stage_label=record.stage_label, sections=[],
                           clarification_keys=[], error_code=error_code)

    def _with_historical_notice(self, response: "JobResponse") -> "JobResponse":
        """Правка F4a, п.3 (мастер-промпт §6: «старые ответы остаются историческими с предупреждением») — при
        ЧТЕНИИ (`get_request`/`list_requests`, через `_visible_response` выше), НЕ при сохранении: если
        `response.corpus_version` отличается от действующей версии корпуса ПРЯМО СЕЙЧАС (`self._handle.
        version`), добавить последней секцией `limitation` с этим предупреждением — не переписывая сам
        сохранённый ответ в `store.py` (следующее чтение может увидеть другую действующую версию и другой текст
        предупреждения, или вовсе без него, если версии к тому времени снова совпали — не наш случай при
        честной публикации вперёд, но и не запрещённый). Предел секций контракта (`job-response.schema.json#
        JobResponse.sections`, `maxItems=24`) уже достигнут — секция НЕ добавляется (заменить последнюю для
        этого нельзя — она могла быть содержательной, не декоративной), строка в журнал, ответ как есть."""
        handle = self._handle
        if handle is None or response.corpus_version == handle.version:
            return response
        if len(response.sections) >= _JOB_RESPONSE_MAX_SECTIONS:
            log.warning("аналитика КДМ-1: исторический ответ request=%s — предел секций, предупреждение о "
                       "версии корпуса не добавлено", response.request_id)
            return response
        notice = _limitation_section(_historical_notice_text(response.corpus_version, handle.version))
        return response.model_copy(update={"sections": [*response.sections, notice]})

    # --- фоновая работа одного задания ---

    async def _guarded_run_job(self, request_id: str) -> None:
        """Сторожевой срок и единая обработка сбоя одного прохода: исключение (в т.ч. `NotImplementedError` ещё
        не готового Task 3/5, таймаут) не роняет службу и не публикует повторно — задание становится `failed`
        (мастер-промпт §10, ОСОБЫЙ ФОКУС ПРОВЕРКИ №3: зависшее задание не платит повторно — новая отправка или
        отмена видят уже терминальную стадию)."""
        async with self._sem:
            try:
                await asyncio.wait_for(self._run_job(request_id), timeout=self.settings.ANALYTICS_JOB_TIMEOUT_S)
            except asyncio.CancelledError:
                log.info("аналитика КДМ-1: задание прервано request=%s", request_id)
            except TimeoutError:
                log.warning("аналитика КДМ-1: задание %s — превышен срок обработки", request_id)
                await asyncio.to_thread(self.store.advance_stage, request_id, "failed", "превышен срок обработки")
            except Exception as exc:                                  # noqa: BLE001 — сбой одного задания, не службы
                log.warning("аналитика КДМ-1: задание %s — сбой обработки (%s)", request_id, type(exc).__name__)
                await asyncio.to_thread(self.store.advance_stage, request_id, "failed", "ошибка обработки")
            finally:
                self._pending.pop(request_id, None)

    async def _run_job(self, request_id: str) -> None:
        """`preanalysis.analyze` → (при `decision == "ready"` и настроенном `llm`) `llm_adapter.
        request_selection` → `validator.validate_selection` → `rendering.render_response` →
        `store.publish_response`. На каждом шаге — `store.advance_stage`; `False` от `advance_stage`/
        `publish_response` останавливает проход без отказа (задание уже заняла отмена или гонка публикации —
        не сбой этого прохода)."""
        pending = self._pending.get(request_id)
        if pending is None:
            return
        identity, conversation_id, request = pending
        record = await asyncio.to_thread(self.store.get_request, request_id)
        if record is None or record.state in JOB_TERMINAL_STATES:
            return
        # правка F4a, п.2: признак версии корпуса на диске — в начале КАЖДОГО задания, до преданализа; изменился
        # с прошлого задания → self._handle переоткрыт здесь (новая публикация видна без перезапуска процесса).
        # job_signature — то, с чем СТАРТУЕТ именно это задание (может быть None — признак не посчитался вовсе,
        # тогда повторная сверка перед публикацией ниже не выполняется, см. её комментарий)
        job_signature = await self._maybe_reopen_corpus()
        handle = self._handle
        if handle is None:
            await asyncio.to_thread(self.store.advance_stage, request_id, "failed", "корпус не открыт")
            return
        if not await asyncio.to_thread(self.store.advance_stage, request_id, "preanalysis", "преданализ вопроса"):
            return                                                     # отменено/завершено до старта обработки
        corpus_version = knowledge.corpus_version(handle)

        analysis, pack = await asyncio.to_thread(preanalysis.analyze, identity, request, handle, corpus_version)
        await asyncio.to_thread(self.store.set_preanalysis, request_id, analysis)

        # повторная проверка владения диалогом между поиском и ответом (ОСОБЫЙ ФОКУС ПРОВЕРКИ №2 плана): диалог
        # мог исчезнуть/сменить владельца, пока преданализ шёл — публикуем только в живой, свой диалог
        owner = await asyncio.to_thread(self.store.conversation_owner, conversation_id)
        if owner != identity.login:
            return

        outcome, sections, state, stage_label = await self._build_outcome(identity, conversation_id, request,
                                                                          analysis, pack, handle)

        # смешанный вопрос (правка F4a, п.5, вместе с F4b): упоминание другой машины в исходном тексте вопроса —
        # первой секцией limitation, если преданализ не отказал целиком (F4b отвечает за то, что решение не
        # "refused", когда в вопросе вместе с чужой машиной есть допустимая часть про КДМ-1)
        if analysis.decision != "refused" and preanalysis._mentions_other_machine(request.question):
            sections = (_limitation_section(_other_machine_limitation_text(request.question)), *sections)

        # повторная сверка версии корпуса ПЕРЕД публикацией (правка F4a, п.2, 3-й подпункт): признак на диске
        # мог измениться МЕЖДУ стартом этого задания и этой точкой (поиск/платный вызов модели заняли время) —
        # свежий признак (дёшево, без хеширования) против job_signature; совпадают — обычная публикация,
        # расходятся — карточки не показываются, ответ не выдаётся «как ни в чём не бывало»
        version_changed = False
        if job_signature is not None:
            current_signature = await self._current_corpus_signature()
            version_changed = current_signature is not None and current_signature != job_signature
        if version_changed:
            outcome = _terminal_outcome()
            sections = (_limitation_section(CORPUS_VERSION_CHANGED_TEXT),)
            state, stage_label = "limited_answer", "версия корпуса сменилась"
        else:
            # сохранить пакет доказательств и перелинковать fragment_id в составной evidence_id ДО публикации
            # (Правка, раунд 1, Important 2) — после публикации ответ клиенту уже ссылался бы на «сырые» id
            sections = await asyncio.to_thread(self._persist_evidence, request_id, handle, tuple(sections))
        response = rendering.render_response(request_id, conversation_id, state, corpus_version, stage_label,
                                             outcome, sections)
        if await asyncio.to_thread(self.store.publish_response, request_id, response):
            log.info("аналитика КДМ-1: задание завершено request=%s state=%s", request_id, state)

    async def _build_outcome(self, identity: "RequestIdentity", conversation_id: str,
                             request: "AnalysisRequest", analysis, pack, handle: "CorpusHandle"):
        """Решение преданализа → `(ValidationOutcome, секции, JobState, stage_label)`. `needs_clarification`/
        `refused` обходят `llm_adapter.py` целиком (мастер-промпт §7 п.4/п.6); `ready`/`limited_answer` — точка
        вызова LLM, см. докстринг модуля (по умолчанию выключена — `self.llm is None` до Task 5).

        `request` — правка F1, п.1: нужен исходный текст вопроса (`request.question`) для `calculations.
        plan_from_question`, когда `analysis.intent == "calculation"` (ветка ниже, после needs_clarification/
        refused/control — эти три решают путь раньше и расчёт не трогают).

        `handle` — дополнение контроллера к правке F4a (хвост F4b, 14.10): нужен ветке `needs_clarification`
        для `preanalysis.clarification_candidates(handle, parsed, resolved_terms)` (подписи кандидатов
        многозначного термина, H-26) и `_refresh_pack` ниже (которая и раньше читала `self._handle` напрямую —
        теперь получает его явным параметром от единственного вызывающего, `_run_job`, той же версии, что сам
        `_run_job` использует для преданализа/публикации — не читает `self._handle` заново своим отдельным
        снимком, который мог бы отличаться, если корпус переоткрылся между вызовами)."""
        if analysis.decision == "needs_clarification":
            keys = tuple(analysis.missing_fields)
            parsed = preanalysis.original_and_normalized(request)
            candidate_labels = await asyncio.to_thread(
                preanalysis.clarification_candidates, handle, parsed, tuple(analysis.resolved_terms))
            section = rendering.render_clarification_section(keys, candidate_labels=candidate_labels)
            return _terminal_outcome(keys), [section], "needs_clarification", "нужно уточнение"
        if analysis.decision == "refused":
            return (_terminal_outcome(refused=True, error_code="policy_refused"), [], "failed",
                    "отказ предварительного анализа")

        # правка F4a, п.1 (ОСОБЫЙ ФОКУС №2 плана, усиление): перечитать карточки пакета из корпуса по их id
        # НЕПОСРЕДСТВЕННО перед validate_selection ниже (оба пути — control и ready/limited_answer) — снимок
        # EvidenceCard, собранный preanalysis.analyze на поиске, не ловит отзыв/правку между поиском и этой
        # проверкой (см. _refresh_pack). Свежий пакет используется и для validator.validate_selection (через
        # _deterministic_outcome/_select_with_llm ниже), и для рендеринга (_render_pack_sections) — показанный
        # текст совпадает с тем, что сейчас реально в корпусе, не со снимком преданализа.
        pack = await asyncio.to_thread(self._refresh_pack, pack, handle)

        if analysis.intent == "control":
            # отказ от команды с сохранением допустимой справки (§7 п.6, T-10): LLM не вызывается вовсе, карточки
            # пакета проходят ту же проверку validator, что и любой ответ без модели, первой секцией — серверный
            # отказ от управляющей части; ошибка распознавания команды стоит пользователю только этой плашки
            approved_card_ids = await self._approved_critical_card_ids(pack, analysis.scope)
            outcome = await asyncio.to_thread(_deterministic_outcome, analysis, pack, approved_card_ids)
            outcome = dataclasses.replace(outcome, error_code="policy_refused")
            sections = [rendering.render_control_refusal_section(), *self._render_pack_sections(outcome, pack)]
            return outcome, sections, "limited_answer", "команда не выполняется — справка"

        # "ready"/"limited_answer": точка вызова LLM — self.llm is None по умолчанию (Task 4, см. докстринг
        # модуля). И путь без LLM, и путь «LLM упал» идут через ТУ ЖЕ проверку validator.validate_selection, что
        # настоящий выбор модели (Правка, раунд 1, Critical) — см. _deterministic_outcome
        # критический местный факт (Task 7, шаг 2) — согласование собирается ОДИН раз на задание и передаётся
        # в оба пути одинаково, не по отдельному запросу на каждый путь
        approved_card_ids = await self._approved_critical_card_ids(pack, analysis.scope)
        outcome, busy = None, False
        # дополнение контроллера к правке F4a (хвост F4b, 14.10): ANALYTICS_LLM_EXPORT_POLICY == "none" —
        # сервер-уровневый выключатель платного пути, та же проверка, что capabilities() выше — оба места не
        # расходятся (capabilities не обещала бы llm_available, если бы этот вызов всё равно дошёл до модели)
        llm_export_disabled = self.settings.ANALYTICS_LLM_EXPORT_POLICY == "none"
        if self.llm is not None and analysis.decision == "ready" and not llm_export_disabled:
            if conversation_id in self._llm_inflight_conversations:
                # правка F4a, п.4: этот же диалог уже ждёт платный вызов модели — не запускать второй
                # одновременно, сразу справка без модели (ниже) с error_code="busy"
                busy = True
            else:
                self._llm_inflight_conversations.add(conversation_id)
                try:
                    outcome, busy = await self._select_with_llm(identity, conversation_id, analysis, pack,
                                                                 approved_card_ids, question=request.question)
                finally:
                    self._llm_inflight_conversations.discard(conversation_id)
            if outcome is not None:
                # правка F4a, раунд 1 (контроллер): ответ модели пришёл ПОСЛЕ платного вызова — за это время
                # карточку могли отозвать в корпусе, а полномочие на критический местный факт — в хранилище
                # согласований (его изменение не видно сверке версии корпуса). Перечитать пакет и согласования
                # заново и провести принятые моделью карточки через ту же проверку validator непосредственно
                # перед публикацией (особый фокус №2: «старый пакет не даёт нового разрешения»)
                pack = await asyncio.to_thread(self._refresh_pack, pack, handle)
                approved_card_ids = await self._approved_critical_card_ids(pack, analysis.scope)
                outcome = await asyncio.to_thread(_recheck_outcome, outcome, analysis, pack, approved_card_ids)
        if outcome is None:
            outcome = await asyncio.to_thread(_deterministic_outcome, analysis, pack, approved_card_ids)
            if busy:
                outcome = dataclasses.replace(outcome, error_code="busy")
        pack_sections = self._render_pack_sections(outcome, pack)
        if busy:
            # правка F4a, п.4: отличимая от прочей недоступности пометка — первой среди карточек этого пакета
            # (не абсолютно первой секцией ответа — расчётная секция intent=="calculation" ниже всё равно
            # ставится раньше, если применима; сам error_code="busy" на outcome — структурный сигнал клиенту)
            pack_sections = [_limitation_section(_BUSY_LIMITATION_TEXT), *pack_sections]

        # телеметрия БДРВ (план Д17, Task 1; мастер-промпт §7 п.7, §8): только здесь, после needs_clarification/
        # refused/control (там чтения нет вовсе) и после выбора карточек — секции телеметрии идут ПЕРВЫМИ
        # (после расчётной, если она есть, см. ниже)
        telemetry_sections = await self._telemetry_sections(identity, request, handle)

        if analysis.intent == "calculation":
            # правка F1, п.1: число считает только сервер (calculations.py) — LLM здесь не участвует вовсе
            # (ни в выборе формулы, ни в самом числе); секция расчёта/ограничения — ПЕРВАЯ в ответе, телеметрия
            # и справочные карточки пакета — следом, как обычно
            calc_section = self._calculation_section(request.question, analysis)
            sections = [calc_section, *telemetry_sections, *pack_sections]
            if calc_section.kind == "calculation":
                state = "completed" if analysis.decision == "ready" else "limited_answer"
            else:
                # только ограничение/уточнение — нет посчитанного числа (п.1 брифа: «есть результат —
                # completed; только ограничение — limited_answer»)
                state = "limited_answer"
            stage = "проверка пройдена" if state == "completed" else "ограниченный ответ"
            return outcome, sections, state, stage

        sections = [*telemetry_sections, *pack_sections]
        if sections:
            # ответ, где есть хотя бы одна telemetry_observation (даже без карточек пакета), — completed при
            # decision=="ready", тем же правилом, что ответ с карточками (план Д17, Task 1)
            state = "limited_answer" if analysis.decision == "limited_answer" else "completed"
        else:
            # ничего не прошло повторную проверку в момент выдачи (или пакет и так был пуст) — безопасный
            # ограниченный ответ, не пустой "completed" (Правка, раунд 1, Critical)
            state = "limited_answer"
        stage = "ограниченный ответ" if state == "limited_answer" else "проверка пройдена"
        return outcome, sections, state, stage

    def _calculation_section(self, question: str, analysis) -> "Section":
        """Правка F1, п.1: `calculations.plan_from_question(question)` → выполнить (`run_calculation`/
        `interval_union`) → `validator.check_calculation_consistency` → `rendering.render_calculation_section`
        (значение есть — секция `calculation`; нет — `limitation`, тот же шаблон, что пропуск базы). Синхронный
        метод (чистая арифметика/регэкспы, без ввода-вывода) — вызывается прямо из `_build_outcome`, не через
        `asyncio.to_thread` (как `_render_pack_sections`)."""
        plan = calculations.plan_from_question(question)
        if plan is None:
            # ни одна из четырёх формул, ни объединение периодов, ни известный структурный отказ — общее
            # уточнение через УЖЕ существующий rendering.render_clarification_section (rendering.py не правим)
            return rendering.render_clarification_section(("quantity",))
        if plan.refusal_reason is not None:
            # структурный отказ (кН/м→давление, избыточное→абсолютное, COV экрана Valmet, межзаводское
            # сравнение) — не ошибка/пропуск входа, причина не зависит от того, что ответит технолог на
            # уточнение; переиспользуем ветку render_calculation_section(value=None) под готовую причину, без
            # правки rendering.py (см. отчёт задачи)
            result = CalculationResult(calculation_id=plan.calculation_id, inputs=(), value=None, unit=None,
                                       assumptions=(), missing_basis=(plan.refusal_reason,))
            return rendering.render_calculation_section(result)
        if plan.calculation_id == "interval_union":
            if not plan.intervals:
                result = CalculationResult(
                    calculation_id="interval_union", inputs=(), value=None, unit=None, assumptions=(),
                    missing_basis=("укажите начало и конец каждого периода; пересекающиеся периоды "
                                   "объединяются, а не суммируются",))
                return rendering.render_calculation_section(result)
            merged = calculations.interval_union(plan.intervals)
            total_hours = sum((end - start for start, end in merged), timedelta()).total_seconds() / 3600
            result = CalculationResult(
                calculation_id="interval_union",
                inputs=(("число_периодов", float(len(plan.intervals)), "шт"),
                       ("после_объединения", float(len(merged)), "шт")),
                value=total_hours, unit="ч",
                assumptions=("Пересекающиеся периоды объединены, не просуммированы по отдельности.",))
            return rendering.render_calculation_section(result)
        try:
            result = calculations.run_calculation(plan.calculation_id, plan.inputs, plan.units)
        except calculations.CalculationInputError as exc:
            result = CalculationResult(calculation_id=plan.calculation_id, inputs=(), value=None, unit=None,
                                       assumptions=(), missing_basis=(str(exc),))
            return rendering.render_calculation_section(result)
        conflict = validator.check_calculation_consistency(result, analysis)
        if conflict is not None:
            result = dataclasses.replace(result, value=None, missing_basis=(conflict,))
        return rendering.render_calculation_section(result)

    async def _telemetry_sections(self, identity: "RequestIdentity", request: "AnalysisRequest",
                                  handle: "CorpusHandle") -> list["Section"]:
        """Телеметрия БДРВ в ответе (план Д17, Task 1; мастер-промпт §7 п.7, §8): подписи оборудования из
        вопроса, у которых есть СОГЛАСОВАННАЯ связь (`reviews.resolved_link`), читаются через `data_bridge.py`
        и идут первыми секциями ответа (после расчётной, если она есть) — единственный вызывающий,
        `_build_outcome`, зовёт этот метод уже ПОСЛЕ ветвей `needs_clarification`/`refused`/`control` (там
        чтения нет вовсе, план, Review Focus №1/№3) и после выбора карточек пакета.

        Подпись без согласованной связи, неподключённая/неисправная служба данных, отказ `DataService` или сбой
        самого хранилища согласований — секция `limitation` с объяснением для каждой такой подписи, не падение
        задания (мастер-промпт §7, этап 7: «без них справка полностью работает»). До `MAX_TELEMETRY_LABELS`
        подписей на ответ — остальные найденные только считаются, последней секцией идёт предупреждение,
        сколько показано из скольки найдено (`rendering.render_telemetry_truncated_section`)."""
        try:
            labels = await asyncio.to_thread(terms.exact_tag_candidates, handle, request.question)
        except knowledge.CorpusError:
            return []
        if not labels:
            return []
        try:
            eq_ids = await asyncio.to_thread(knowledge.all_ids, handle, "equipment_links")
            eq_rows = await asyncio.to_thread(knowledge.get_rows, handle, "equipment_links", eq_ids)
        except knowledge.CorpusError:
            return []
        # оставить только подписи, которые есть в equipment_links (подпись узла из processes — не оборудование,
        # план, Task 1: «оставь только те, что есть в equipment_links»); подпись → список id строк этой подписи
        label_to_ids: dict[str, list[str]] = {}
        for row_id, row in eq_rows.items():
            label = row.get("display_label")
            if label:
                label_to_ids.setdefault(label, []).append(row_id)
        matched = [label for label in labels if label in label_to_ids]
        if not matched:
            return []
        shown_labels = matched[:MAX_TELEMETRY_LABELS]
        period = request.context.period if request.context is not None else None
        sections = [await self._telemetry_section_for_label(identity, label, label_to_ids[label], period)
                   for label in shown_labels]
        if len(matched) > MAX_TELEMETRY_LABELS:
            sections.append(rendering.render_telemetry_truncated_section(len(shown_labels), len(matched)))
        return sections

    async def _telemetry_section_for_label(self, identity: "RequestIdentity", label: str, link_ids: list[str],
                                            period) -> "Section":
        """Одна подпись → одна секция: первая СОГЛАСОВАННАЯ связь среди строк `equipment_links` этой подписи
        (`reviews.resolved_link`, перепроверяется «здесь и сейчас» с текущим `ANALYTICS_REVIEWERS` — Review
        Focus №2: отзыв режима `all_students` сразу прекращает чтение по решениям студентов без назначения).
        `period` — `request.context.period`, если задан (`AnalysisRequest.context.period`, `models.
        PeriodWindow`) — тогда `data_bridge.tag_period`, иначе `data_bridge.tag_now`."""
        try:
            link = None
            for link_id in link_ids:
                candidate = await asyncio.to_thread(self.reviews.resolved_link, link_id,
                                                    reviewers_mode=self.settings.ANALYTICS_REVIEWERS)
                if candidate is not None:
                    link = candidate
                    break
        except ReviewStoreError:
            # сбой самого хранилища согласований — ограничение «служба недоступна», не падение задания (план,
            # Task 1, раздел service._build_outcome)
            return rendering.render_telemetry_unavailable_section(label)
        if link is None:
            return rendering.render_telemetry_unresolved_section(label)
        if self.calc is None:
            return rendering.render_telemetry_unavailable_section(label)
        if period is not None:
            reply = await data_bridge.tag_period(self.calc, identity, link, None, period.start.isoformat(),
                                                 period.end.isoformat())
        else:
            reply = await data_bridge.tag_now(self.calc, identity, link)
        if not reply.get("items"):
            # полный отказ службы данных (ворота/бюджет/сбой настройки) — текст отказа как есть, не прячется
            # (мастер-промпт: «отказ DataService показывается как есть»)
            return rendering.render_telemetry_refusal_section(label, reply.get("message") or "")
        render = rendering.render_telemetry_period_section if period is not None else rendering.render_telemetry_section
        return render(label, link, reply)

    async def _select_with_llm(self, identity: "RequestIdentity", conversation_id: str, analysis, pack,
                               approved_card_ids: frozenset[str] = frozenset(), *, question: str = ""
                               ) -> tuple[ValidationOutcome | None, bool]:
        """Точка вызова `llm_adapter.py` (Task 5, см. докстринг модуля) — `(None, busy)`, если путь с LLM
        недоступен прямо сейчас (отказ `LlmAdapterRefusal`, неготовый `llm_adapter.py` — `NotImplementedError`
        — или любой иной сбой): вызывающий (`_build_outcome`) тогда отдаёт справку без модели, не падает
        заданием. `teacher_payer` — хвост Task 5 (коммит 830a782): явно настроенный плательщик преподавателя,
        `AnalyticsSettings.ANALYTICS_TEACHER_PAYER` (пусто — преподавателю отказ раньше всего остального,
        `llm_adapter.request_selection` докстринг); `capabilities()` сообщает `llm_available=True` преподавателю
        ровно тогда, когда и этот вызов реально дойдёт до двойника/модели (то же условие, не два расходящихся).

        Предел исправлений (решение контроллера, мастер-промпт §8): здесь он РОВНО НОЛЬ — отказ
        `LlmAdapterRefusal` (в т.ч. `"invalid_selection"`, неразобранный/невалидный ответ модели) или любой
        иной сбой адаптера не повторяет платный вызов ещё раз, а сразу отдаёт `None` — вызывающий откатывается
        на детерминированный путь через ТОТ ЖЕ `validator.validate_selection` (см. докстринг модуля, «Правка,
        раунд 1, Critical», уже сделано в `ed32131`) или, если после проверки ничего не осталось,
        `limited_answer`. `validator.MAX_SELECTION_RETRIES` — константа для БУДУЩЕГО контролируемого повтора
        самого выбора модели (переспросить ту же модель с тем же пакетом), не для этого отката; этот модуль её
        не использует: учёт расхода на повторную оплату здесь не нужен, потому что повторной оплаты нет.

        Правка F4a, п.4: второй элемент возвращаемой пары — `busy`, `True` ровно когда `exc.reason == "busy"`
        (предел параллельности самого ВЫЗЫВАЮЩЕГО — `role.inflight`, см. `llm_adapter.request_selection`) —
        отличимо от прочих причин отказа (`stopped`/`config`/`key_missing`/…, которым соответствует обычный
        молчаливый откат без отдельной пометки, как и раньше). Занятость ОДНОГО ДИАЛОГА (не больше одного
        платного вызова на диалог) проверяет и отмечает сам вызывающий (`_build_outcome`) ДО вызова этого
        метода — сюда она не доходит вовсе.

        Итоговая правка F6 (решение владельца, 14.10.2026 — см. докстринг модуля, п.8): `self.settings.
        ANALYTICS_LLM_EXPORT_POLICY` подставляется в `Preanalysis.llm_export_policy` ЗДЕСЬ, непосредственно
        перед вызовом `llm_adapter.build_raw_body` — не в `preanalysis.py` (эта правка его не трогает, он
        по-прежнему всегда ставит `public_only`/`none`) и не раньше в `_build_outcome` (значение нужно только
        в этой точке, не раньше). `analysis`, переданный дальше в `validator.validate_selection` ниже, — ИСХОДНЫЙ
        объект преданализа (политика экспорта не входит в проверку смысла/прав, подменять там нечего).
        `question` — исходный текст вопроса технолога (`AnalysisRequest.question`, передаёт `_build_outcome`),
        нужен `build_raw_body` только при `approved_subset` (при `public_only` параметр не используется)."""
        try:
            model_models = self.settings.ANALYTICS_LLM_MODELS
            llm_adapter.select_model(self.llm, model_models)
            export_policy = self.settings.ANALYTICS_LLM_EXPORT_POLICY
            outgoing_analysis = (analysis if analysis.llm_export_policy == export_policy
                                else analysis.model_copy(update={"llm_export_policy": export_policy}))
            raw_body = llm_adapter.build_raw_body(outgoing_analysis, pack, schema=_llm_selection_schema(),
                                                  question=question)
            selection = await llm_adapter.request_selection(
                self.llm, identity, conversation_id, raw_body, analytics_models=model_models,
                teacher_payer=self.settings.ANALYTICS_TEACHER_PAYER)
        except llm_adapter.LlmAdapterRefusal as exc:
            log.info("аналитика КДМ-1: LLM недоступен (%s) — ответ без модели", exc.reason)
            return None, exc.reason == "busy"
        except NotImplementedError:
            return None, False                # Task 5 ещё не реализован — справочный путь остаётся рабочим
        except Exception as exc:              # noqa: BLE001 — сбой адаптера не должен ронять справочный путь
            log.warning("аналитика КДМ-1: сбой вызова LLM (%s) — ответ без модели", type(exc).__name__)
            return None, False
        return validator.validate_selection(selection, analysis, pack, approved_card_ids), False

    def _refresh_pack(self, pack: tuple, handle: "CorpusHandle | None") -> tuple:
        """Правка F4a, п.1 (см. докстринг модуля/`_build_outcome`): перечитать каждую карточку `pack` из
        корпуса по её `id` (`knowledge.get_rows`) и собрать заново ТЕМ ЖЕ `preanalysis._build_card`, что и
        исходный пакет при поиске — свежий `publication_state`/`text`/`critical_local_fact` вместо снимка со
        времени `preanalysis.analyze`. Карточка, которой больше нет в корпусе (отозвана/удалена между поиском
        и этим вызовом) — не выходит из этого метода вовсе, дальше (в `validate_selection`/рендеринг) не
        попадает; изменился текст/статус — используется свежий (сам показ карточки и так проходит повторную
        проверку `validator.validate_selection` по актуальному `publication_state` — здесь только источник
        данных для этой проверки становится свежим, а показывать её или нет решает validator, не этот метод).
        Порядок `pack` сохраняется. Сбой чтения корпуса (`knowledge.CorpusError`) — пустой пакет: безопасный
        отказ (ничего не подтверждено свежим — значит ничего не показывается), не падение задания, как и
        прочие сбои корпуса в этом модуле. Синхронный метод (блокирующий SQLite I/O) — вызывается через
        `asyncio.to_thread`.

        `handle` — параметр явно (не `self._handle`): дополнение контроллера к правке F4a (хвост F4b, 14.10) —
        `self._handle` может смениться МЕЖДУ стартом этого задания и этим вызовом из-за `_maybe_reopen_corpus`
        ДРУГОГО, конкурентного задания (разные диалоги обрабатываются параллельно, до `MAX_CONCURRENT_JOBS`
        штук); рефреш обязан сверяться с ТЕМ корпусом, который открыт для ЭТОГО задания (переменная `handle` в
        `_run_job`), не с тем, что окажется в `self._handle` к моменту вызова — иначе `corpus_version` ссылок
        перечитанных карточек мог бы молча не совпасть с `preanalysis.corpus_version` этого же задания."""
        if not pack:
            return pack
        if handle is None:
            return ()
        try:
            corpus_version = knowledge.corpus_version(handle)
            claim_ids = tuple(card.id for card in pack if card.kind == "claim")
            mechanism_ids = tuple(card.id for card in pack if card.kind == "mechanism")
            claim_rows = knowledge.get_rows(handle, "claims", claim_ids) if claim_ids else {}
            mechanism_rows = knowledge.get_rows(handle, "mechanisms", mechanism_ids) if mechanism_ids else {}
        except knowledge.CorpusError as exc:
            log.warning("аналитика КДМ-1: сбой повторного чтения карточек перед проверкой (%s)",
                       type(exc).__name__)
            return ()
        refreshed: list = []
        for card in pack:
            rows = claim_rows if card.kind == "claim" else mechanism_rows
            row = rows.get(card.id)
            if row is None:
                continue                      # отозвана/удалена между поиском и проверкой — не выходит
            fresh = preanalysis._build_card(handle, card.kind, row, corpus_version)
            if fresh is not None:
                refreshed.append(fresh)
        return tuple(refreshed)

    def _render_pack_sections(self, outcome: ValidationOutcome, pack) -> list:
        """`rendering.render_claim_section`/`render_mechanism_section` для каждой принятой `outcome` карточки
        пакета, в порядке `pack` (детерминированный порядок ответа)."""
        sections = []
        accepted = set(outcome.accepted_claim_ids) | set(outcome.accepted_mechanism_ids)
        for card in pack:
            if card.id not in accepted:
                continue
            if card.kind == "mechanism":
                sections.append(rendering.render_mechanism_section(card))
            else:
                sections.append(rendering.render_claim_section(card))
        return sections

    def _persist_evidence(self, request_id: str, handle: "CorpusHandle",
                          sections: tuple["Section", ...]) -> tuple["Section", ...]:
        """Сохранить неизменяемый пакет доказательств задания (`store.save_evidence_pack`) ДО публикации и
        перелинковать `EvidenceRef.fragment_id` каждой секции в составной `evidence_id` (Правка, раунд 1,
        Important 2, см. докстринг модуля). Секции без ссылок на фрагменты — пакет не создаётся вовсе (нечего
        сохранять, `sections` возвращается как есть). Синхронный метод — зовётся через `asyncio.to_thread`.

        Правка F1, п.5 (мастер-промпт §6: «источники раскрываются карточками с названием, …, датой, типом,
        фрагментом и ограничениями»): элемент пакета дополнен `source_title`/`source_type`/`source_date` —
        из записи источника корпуса (`sources.title`/`type`/`reviewed_on`, см. `knowledge/README.md` пакета);
        нет записи источника (`ref.source_id` не найден в `sources`) — все три поля `None`, не выдуманы.
        Контракт заказчика `EvidenceRef`/`job-response.schema.json` не меняется — эти поля есть только в НАШЕМ
        формате GET `.../evidence/{id}` (`store.get_evidence_item`), который заказчик не фиксирует."""
        refs_by_fragment: dict[str, "EvidenceRef"] = {}
        for section in sections:
            for ref in section.evidence_refs:
                if ref.fragment_id and ref.fragment_id not in refs_by_fragment:
                    refs_by_fragment[ref.fragment_id] = ref
        if not refs_by_fragment:
            return sections
        fragment_rows = knowledge.get_rows(handle, "fragments", tuple(refs_by_fragment))
        source_ids = tuple(dict.fromkeys(ref.source_id for ref in refs_by_fragment.values() if ref.source_id))
        source_rows = knowledge.get_rows(handle, "sources", source_ids) if source_ids else {}
        items: list[tuple[str, dict]] = []
        for fragment_id, ref in refs_by_fragment.items():
            row = fragment_rows.get(fragment_id)
            text = str(row.get("text") or "") if row is not None else ""
            source_row = source_rows.get(ref.source_id)
            items.append((fragment_id, {
                "id": fragment_id, "source_id": ref.source_id, "locator": ref.locator.model_dump(),
                "corpus_version": ref.corpus_version, "limitation": ref.limitation, "text": text,
                "source_title": None if source_row is None else source_row.get("title"),
                "source_type": None if source_row is None else source_row.get("type"),
                "source_date": None if source_row is None else source_row.get("reviewed_on"),
            }))
        pack_id = self.store.save_evidence_pack(request_id, tuple(items))
        rewritten: list["Section"] = []
        for section in sections:
            if not section.evidence_refs:
                rewritten.append(section)
                continue
            new_refs = [
                ref.model_copy(update={"fragment_id": encode_evidence_id(request_id, pack_id, ref.fragment_id)})
                if ref.fragment_id in refs_by_fragment else ref
                for ref in section.evidence_refs
            ]
            rewritten.append(section.model_copy(update={"evidence_refs": new_refs}))
        return tuple(rewritten)
