# Интерфейсы аналитики КДМ-1 (Task 0)

Карта для исполнителей Task 1–9 плана `docs/plans/PLAN-KDM1-analytics-2026-10-14.md`. Сигнатуры ниже — те,
что уже лежат в репозитории (`core/pcbk_core/analytics/*.py`, заглушки с `raise NotImplementedError("Task N")`)
и обратной совместимости для последующих задач они не ломают без отдельного решения (меняется — меняется и этот
файл в том же коммите). Требования каждой задачи — её раздел `IMPLEMENTATION_PLAN.md` целиком плюс ссылки на
§ `MASTER_PROMPT_KDM1_CLAUDE_CODE.md`, указанные в докстринге каждого модуля.

## Поток запроса

```
gateway/analytics_api.py (роутер, зона/настройка/сессия — как lessons_api, POST — + origin_checked)
        │  AnalysisRequest (models.py) + RequestIdentity (из сессии, НЕ из тела)
        ▼
analytics/service.py — AnalyticsService (координатор, фоновое задание на вопрос)
        │
        ▼
analytics/preanalysis.py — analyze() → (Preanalysis, tuple[EvidenceCard, ...])
        │                               │
        │ (knowledge.py/terms.py)      │ (retrieval.py — поиск; calculations.py — расчёты, если intent=calculation)
        ▼                               ▼
   решение: ready / needs_clarification / limited_answer / refused
        │
        │ если ready и role.llm настроена (мастер-промпт §9: только публичный контекст)
        ▼
analytics/llm_adapter.py — request_selection() → LlmSelection (только ID, проверенный строгий JSON)
        │
        ▼
analytics/validator.py — validate_selection() → ValidationOutcome (каждый id сверен с пакетом доказательств)
        │
        ▼
analytics/rendering.py — render_*_section()/render_response() → JobResponse (models.py)
        │
        ▼
analytics/store.py — AnalyticsStore.publish_response() (атомарно: ответ + пакет доказательств)
        │
        ▼
gateway/analytics_api.py — GET requests/{id} отдаёт сохранённый JobResponse клиенту
```

`data_bridge.py`/`reviews.py` (Task 7) включаются в этот поток отдельно: `data_bridge.py` вызывается из
`service.py`/`rendering.py` только когда `preanalysis.py` определил связанный процесс **и** `reviews.
resolved_link` вернул согласованную связь; без неё путь работает целиком (справка), просто без чтения
историана.

## Личность вызывающего (RequestIdentity)

Источник — **только серверная сессия** (`pcbk_core.gateway.accounts.Account`, зависимость `current_account`),
никогда тело запроса (`AnalysisRequest.model_config = ConfigDict(extra="forbid")` и так отверг бы лишние поля
`place/caller/login/owner/role`, но `gateway/analytics_api.py` их и не читает из тела — `Account` идёт отдельным
параметром обработчика). `service.py` (Task 4) строит `models.RequestIdentity` из `Account` так:

```python
caller = TEACHER_CALLER if account.role == "teacher" else f"student-{account.place:02d}"  # place уже проверен не-None
identity = RequestIdentity(login=account.login, role=account.role, place=account.place, caller=caller)
```

— тот же вид `caller`, что `gateway.calc_api.caller_for`/`caller_of`, и тот же формат, что `pcbk_core.places.
CALLER_RE` (`student-(0[1-9]|1[0-…])`) принимает у `/api/data/*`/MCP/калькуляторов. Владелец истории
(`store.py`) — `identity.login`, не `identity.place` (место можно переназначить, история — нет, мастер-промпт
§6). LLM видит только `identity.caller`, не `login` (мастер-промпт §6: «для LLM сохраняется student-NN»).

## `analytics/settings.py` — Task 0, готово

`AnalyticsSettings` (frozen dataclass, `from_env`/`valid_from_env`, приём `GatewaySettings`):

| Поле | Умолчание | Смысл |
|---|---|---|
| `ANALYTICS_ENABLED` | `False` | строгий bool (`1/true/on/yes` или `0/false/off/no`) |
| `ANALYTICS_CORPUS_DIR` | `/srv/analytics` | каталог версий корпуса, только для чтения |
| `ANALYTICS_STATE_PATH` | `/var/lib/pcbk-core/analytics.db` | состояние диалогов/заданий/согласования |
| `ANALYTICS_LLM_MODELS` | `""` (без доп. сужения) | подмножество имён `llm/models.CANDIDATES` |
| `ANALYTICS_LLM_EXPORT_POLICY` | `"public_only"` | `public_only \| approved_subset \| none` |
| `ANALYTICS_REVIEWERS` | `"grants"` | `grants` (право только по назначению) или `all_students` (любой `student` решает без назначения, F5, 14.10.2026) |
| `ANALYTICS_HISTORY_RETENTION_DAYS` | `365.0` | срок хранения диалогов/сообщений |
| `ANALYTICS_EVIDENCE_RETENTION_DAYS` | `365.0` | срок хранения пакетов доказательств |
| `ANALYTICS_JOB_TIMEOUT_S` | `120.0` | срок одной стадии задания до `failed` |

Читает `AnalyticsSettings.from_env()` — `main.py`/`build_gateway_role` (Task 4, ещё не подключено: Task 0
подключил только булев `GatewayRole.analytics_enabled`, см. ниже — отдельно от полного объекта настроек).

`ANALYTICS_LLM_EXPORT_POLICY` — итоговая правка F6 (14.10.2026, решение владельца, дословно): «4. Разрешен
полный контент в тч внутренний и данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в
модель!». Все три значения контракта (`models.LlmExportPolicy`) теперь принимаются настройкой: `public_only`
(умолчание) — модель видит только id; `approved_subset` — модель дополнительно видит исходный вопрос и
тексты карточек пакета (включая внутренний контент и данные БДРВ, если они есть в пакете, `llm_adapter.
build_raw_body`, см. раздел `llm_adapter.py` ниже); `none` — модель не вызывается вовсе. Разбор —
`strip().lower()` до сравнения (иной регистр/пробелы узнаются как то же значение), настоящая опечатка — явная
ошибка настройки. Ответ модели этой правкой не меняется — всё равно только выбор id, сырой текст модели
клиенту не выводится ни при одном значении. На сервере ПЦБК — `approved_subset` (решение владельца выше,
`deploy/README.md`/`deploy/env.example`).

**ANALYTICS_CORPUS_DIR в `.env` выкладки — хостовый путь** (Task 9): участвует только в `compose.yaml` →
`volumes.source` (bind-mount на чтение в `/srv/analytics`); значение самого поля `AnalyticsSettings.
ANALYTICS_CORPUS_DIR` внутри контейнера — отдельное умолчание кода `/srv/analytics`, в `core.environment` оно
не передаётся (как `LESSONS_ROOT`/`LESSONS_DIR` общего слоя) — иначе контейнер искал бы корпус по хостовому
пути, которого внутри него не существует.

## `analytics/models.py` — Task 0, готово

Публичные модели (pydantic, `extra="forbid"`) — см. докстринг файла и `CONTRACT_SCHEMAS` (модель → файл схемы
в `analytics/contracts/`): `AnalysisRequest`, `Preanalysis` (+ `ResolvedTerm`), `LlmSelection`, `JobResponse`
(+ `Section`, `EvidenceRef`, `PdfPageLocator`/`XlsxCellLocator`/`ImageLabelLocator`/`PublicSectionLocator`/
`Locator`). Внутренние (frozen dataclass): `RequestIdentity`, `EvidenceCard`, `CalculationResult`,
`ValidationOutcome`. Общие словари: `AnalysisMode`, `Intent`, `Decision`, `Risk`, `LlmExportPolicy`,
`ClarificationKey`, `CalculationId`, `JobState`, `SectionKind`, `ErrorCode`, `JOB_TERMINAL_STATES`.

Все перечисления и границы длины — зеркало `contracts/*.schema.json`; при изменении схемы меняются оба файла в
одном коммите, тест `test_analytics_contracts.py` их держит синхронными на уровне принятых/отвергнутых фикстур.

## `analytics/knowledge.py` — Task 1

Вызывает: `preanalysis.py`, `retrieval.py`, `terms.py`, `service.py` (отчёт покрытия).

```python
RowKind = Literal["sources","fragments","processes","terms","claims","conflicts","equipment_links","mechanisms"]
SEARCH_KINDS: tuple[str, ...]              # ("claim", "mechanism", "processe", "term") — "processe" как в сидере

class CorpusError(Exception): ...
class CorpusHandle:  # frozen dataclass: path, version

def open_corpus(root: str) -> CorpusHandle
def close(handle: CorpusHandle) -> None
def corpus_version(handle: CorpusHandle) -> str
def get_row(handle: CorpusHandle, kind: RowKind, row_id: str) -> dict | None
def get_rows(handle: CorpusHandle, kind: RowKind, row_ids: Sequence[str]) -> dict[str, dict]
def all_ids(handle: CorpusHandle, kind: RowKind) -> tuple[str, ...]            # Task 1, аддитивно (см. ниже)
def corpus_report(handle: CorpusHandle) -> dict
def corpus_dirs(root: str) -> list[Path]
def version_signature(root: str) -> str                      # правка F4a, аддитивно (см. ниже)
```

Дополнение Task 1 (коммит 0778dc8, не меняет уже выданных сигнатур выше): `all_ids(handle, kind) -> tuple[str, ...]`
— все `id` таблицы `kind` по возрастанию; нужно `terms.py`, чтобы перечислить весь словарь терминов корпуса
(41 запись в настоящем корпусе) и сравнить его с нормализованным словом вопроса, не открывая SQLite мимо
`knowledge.py`. Подробности и причина — докстринг `knowledge.py`, раздел «Дополнение к INTERFACES.md».

Дополнение правки итогового ревью F4a (14.10, «смена версии корпуса без перезапуска процесса», не меняет
выданных сигнатур выше): `version_signature(root) -> str` — дешёвый признак текущей версии на диске (путь
выбранного файла, `_find_seed_file`, + его размер/mtime, БЕЗ хеширования содержимого — в отличие от
`open_corpus`/`corpus_version`, которые читают файл целиком); `CorpusError` на тех же случаях, что
`open_corpus`. Нужен `AnalyticsService._maybe_reopen_corpus` (`service.py`), чтобы решить «переоткрывать ли
корпус заново» без дорогой операции на каждый вопрос. Подробности — докстринг `knowledge.py`.

## `analytics/terms.py` — Task 1

Вызывает: `preanalysis.py`.

```python
class TermCandidate:  # frozen dataclass: id, preferred_ru, definition, ambiguous, machine_scope

def normalize_ru(text: str) -> str
def resolve_term(handle: CorpusHandle, original: str) -> ResolvedTerm         # models.ResolvedTerm
def exact_tag_candidates(handle: CorpusHandle, text: str) -> list[str]
def known_ambiguous_words(handle: CorpusHandle) -> frozenset[str]
```

Сигнатура `known_ambiguous_words` изменена Task 1 (коммит 0778dc8) относительно черновика Task 0: добавлен
обязательный параметр `handle` — слова читаются из корпуса (`terms.ambiguous`), не из статического списка в
коде; вызывающие (`preanalysis.py`) передают тот же `CorpusHandle`, что и в `resolve_term`/`exact_tag_candidates`.

**Уточнение правки итогового ревью F1, п.4 (проверено на коде):** `preanalysis.py` эту функцию НЕ вызывает —
существенная неоднозначность слова уже даёт `resolve_term` (`requires_confirmation`/`candidate_ids`, см. выше
и докстринг `terms.py`), отдельный предварительный список неоднозначных слов этому не нужен. `known_ambiguous_
words` остаётся в модуле как готовый, протестированный инструмент на будущее (например, для подсветки в вебе
«это слово у нас неоднозначно» ДО отправки вопроса), но сегодня он не на пути запроса.

## `analytics/retrieval.py` — Task 1

Вызывает: `preanalysis.py` (`build_evidence_pack`), `validator.py` (`related_fragments`).

```python
SearchKind = Literal["claim", "mechanism", "processe", "term"]

class SearchHit:  # frozen dataclass: kind, id, score, snippet

def search(handle: CorpusHandle, query: str, *, kinds: tuple[SearchKind, ...] = (), limit: int = 20,
          process_ids: tuple[str, ...] = ()) -> list[SearchHit]
def exact_id_lookup(handle: CorpusHandle, kind: SearchKind, token: str) -> str | None
def related_fragments(handle: CorpusHandle, claim_or_mechanism_id: str) -> list[str]
```

## `analytics/store.py` — Task 2

Вызывает: `service.py` (единственный вызывающий — маршруты не трогают `store.py` напрямую).

```python
class StoreError(Exception): ...
class ConversationMeta:  # frozen dataclass: id, owner_login, mode, created_at, last_activity_at, corpus_version
class RequestRecord:     # frozen dataclass: id, conversation_id, owner_login, client_request_id, state,
                         # stage_label, corpus_version, created_at, response: JobResponse | None,
                         # question: str = ""                              # Task 4, правка раунд 1 (см. ниже)

class AnalyticsStore:
    def __init__(self, db_path: str, *, clock: Callable[[], float] | None = None)
    def open(self) -> bool
    def ping(self) -> None
    def create_conversation(self, identity: RequestIdentity, mode: AnalysisMode, corpus_version: str) -> str
    def list_conversations(self, identity: RequestIdentity) -> list[ConversationMeta]
    def conversation_owner(self, conversation_id: str) -> str | None
    def create_request(self, identity: RequestIdentity, conversation_id: str,
                       request: AnalysisRequest) -> tuple[str, bool]       # (request_id, created)
    def get_request(self, request_id: str) -> RequestRecord | None
    def set_preanalysis(self, request_id: str, preanalysis: Preanalysis) -> None
    def advance_stage(self, request_id: str, state: JobState, stage_label: str) -> bool
    def publish_response(self, request_id: str, response: JobResponse) -> bool
    def cancel_request(self, identity: RequestIdentity,
                       request_id: str) -> tuple[bool, str | None]   # (cancelled, stage_before) — правка раунд 1
    def reap_unfinished_after_restart(self) -> int
    def save_evidence_pack(self, request_id: str, pack: tuple[tuple[str, dict], ...]) -> str
    def get_evidence_item(self, evidence_pack_id: str, item_id: str) -> dict | None
    def evidence_pack_owner(self, evidence_pack_id: str) -> str | None    # Task 4, правка раунд 1 (см. ниже)
    def list_requests(self, conversation_id: str) -> list[RequestRecord]      # Task 4, аддитивно (см. ниже)
    def sweep_retention(self, now: datetime, *, history_days: float, evidence_days: float) -> tuple[int, int]
```

**Task 4, правка раунд 1** (замечания контроллера на коммит `385a260`, не меняет остальные сигнатуры выше):

- `cancel_request` теперь возвращает `(cancelled, stage_before)`, не просто `bool` — `stage_before` читается в
  ТОЙ ЖЕ транзакции, что сама отмена (Important 4): решение «прерывать ли фоновую `asyncio.Task` задания»
  (`service.cancel_request`) должно опираться на стадию из этого же снимка, не на отдельное более раннее
  чтение — иначе окно гонки (задание успевает перейти в `generating` между чужим снимком и вызовом отмены).
  `stage_before` — `None` только если задания нет/оно чужое; для уже терминального своего — сама терминальная
  стадия (не `None`), чтобы `(False, None)` и `(False, "cancelled")` не путались.
- `evidence_pack_owner(evidence_pack_id) -> str | None` — `request_id`, которому принадлежит пакет (Important
  3): составной `evidence_id` клиента несёт `request_id` и `pack_id` раздельно — свой `request_id` с чужим
  `pack_id` без этой проверки проходил бы (владение заданием подтверждено, но не то, что пакет — именно пакет
  ЭТОГО задания). Индекс `evidence_packs_request` под такой запрос уже был.
- `RequestRecord.question: str = ""` — текст исходного вопроса (`AnalysisRequest.question`), как хранится,
  заполняется `get_request`/`list_requests` (решение контроллера, Minor «д»): владельцу диалога видна история
  вопрос-ответ, не только ответы.

Уникальность: `(owner_login, conversation_id, client_request_id)` — `create_request` идемпотентен по ней
(ОСОБЫЙ ФОКУС ПРОВЕРКИ №3 плана).

Дополнение Task 4 (не меняет уже выданных сигнатур выше, приём `knowledge.all_ids` Task 1): `list_requests
(conversation_id) -> list[RequestRecord]` — задания диалога по возрастанию `created_at`, права не проверяет
(как `get_request`/`conversation_owner` — сравнение с `identity` делает вызывающий). Нужно
`gateway/analytics_api.py` (GET `conversations/{id}`) для истории диалога, которую веб (`web/src/analytics.ts`,
`ConversationDetail.requests: JobResponse[]`, Task 6, коммит f6c0c95) уже ждёт в этом виде; индекс
`requests_conversation` под такой запрос в схеме уже был, листинга без него не хватало.

**Известный разрыв с вебом (Task 6), не закрытый Task 4:** `ConversationDetail.requests` — массив `JobResponse`,
а в нём нет текста исходного вопроса (`AnalysisRequest.question` эта модель не хранит и не отдаёт — её нет и в
`RequestRecord`, только `request_json` внутри `requests` целиком, непубличный). После перезагрузки страницы веб
может показать только ответы истории, без вопросов, на которые они отвечали. Решение — за Task 7/владельцем
контракта: либо веб хранит вопрос у себя на клиенте в момент отправки (он уже знает текст при `ask()`), либо
`JobResponse`/новое поле истории получает его явно, с правкой `contracts/job-response.schema.json` и
`web/src/analytics.ts` в одном коммите с тем, кто это делает.

## `analytics/preanalysis.py` — Task 3

Вызывает: `service.py`. Вызывает: `terms.py`, `retrieval.py`, `knowledge.py`.

```python
class NormalizedQuestion:  # frozen dataclass: original, normalized, unit_hint, process_hint

def original_and_normalized(request: AnalysisRequest) -> NormalizedQuestion
def classify_intent(parsed: NormalizedQuestion) -> Intent
def requires_clarification(parsed: NormalizedQuestion, resolved_terms: tuple[ResolvedTerm, ...],
                           process_ids: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]
def build_evidence_pack(handle: CorpusHandle, process_ids: tuple[str, ...],
                        parsed: NormalizedQuestion) -> tuple[EvidenceCard, ...]
def analyze(identity: RequestIdentity, request: AnalysisRequest, handle: CorpusHandle,
           corpus_version: str) -> tuple[Preanalysis, tuple[EvidenceCard, ...]]    # точка входа service.py
```

Решения `analyze` по намерению (с правки Task 3a, раунд 4): `out_of_scope` → `refused`, пакет пуст;
`control` → **отказ от команды с сохранением справки** (T-10): `decision="limited_answer"`, `risk="control"`,
`llm_export_policy="none"`, `data_access_ready=False`, пакет — только карточки `_CONTROL_SAFE_RISK_LEVELS`, чей
текст сам не читается указанием; `service._build_outcome` для `intent == "control"` LLM не вызывает, первой
секцией ставит `rendering.render_control_refusal_section()` (kind `limitation`), `error_code="policy_refused"`,
`state="limited_answer"`.

**Правка итогового ревью F1 (14.10) — доп. к `build_evidence_pack`/`requires_clarification`, не меняет
сигнатуры выше:**

- `build_evidence_pack` (п.4): явно названный в тексте вопроса id карточки/механизма корпуса («покажи CLAIM-…»,
  «DEF-cov») теперь ищется точно (`retrieval.exact_id_lookup`, новый приватный `_explicit_card_ids`) и кладётся
  ПЕРВЫМ в пакет, раньше результатов нечёткого `retrieval.search` — без нового параметра сигнатуры (токен берётся
  из `parsed.original`, который функция уже получает).
- `requires_clarification` (дополнение контроллера к F1): новый стебель `_CALCULATION_STEMS` — `"сложи"`
  (T-32: «Сложить времена обрыва, простоя…» классифицируется `calculation`, ведёт к `interval_union`, не к
  `lookup` по умолчанию). Проверка «unit»/«basis» теперь пропускает вопрос, для которого `calculations.
  known_refusal_reason(parsed.original)` уже вернула структурную причину отказа (перевод кН/м в давление,
  избыточное→абсолютное без базы, COV экрана Valmet, межзаводское/межотраслевое сравнение) — уточнение не
  изменило бы этот исход. Модуль получил новую зависимость `from . import calculations` (сигнатуры самого
  `preanalysis.py` не меняются, цикла импортов нет — `calculations.py` от `preanalysis.py` не зависит).
- `requires_clarification` (правка контроллера, Task 3, раунд 2, Д17 — Minor M2 ревью: необязательный
  ключевой параметр, пропущенный при первой правке): `has_equipment_label: bool = False` — точная подпись
  ОБОРУДОВАНИЯ (`equipment_links.display_label`, не подпись узла `processes.source_label`) в тексте вопроса
  (`preanalysis._mentions_equipment_link_label`). При `True` и `intent != "calculation"` не добавляются
  `"quantity"`/`"basis"` — подпись прибора сама однозначно задаёт измеряемую величину и единицу (их отдаёт
  служба данных, Task 1/`data_bridge.py`); для `intent == "calculation"` поведение не меняется. Старый вызов
  без параметра работает как раньше (аддитивный kwarg со значением по умолчанию, сигнатура выше не нарушена).

## `analytics/calculations.py` — Task 3, дополнено правкой итогового ревью F1 (14.10)

Вызывает: `service.py`/`validator.py` (`run_calculation`), `preanalysis.py` (`known_refusal_reason` — доп. F1,
см. раздел `preanalysis.py`); отдельные формулы — тесты с независимым эталоном.

```python
class CalculationInputError(ValueError): ...

def dry_solid_flow(volumetric_flow_m3_h: Decimal, density_t_m3: Decimal,
                   dry_content_fraction: Decimal) -> CalculationResult
def geometric_output(width_m: Decimal, speed_m_min: Decimal, basis_weight_g_m2: Decimal) -> CalculationResult
def dry_grammage(wet_basis_weight_g_m2: Decimal, dry_content_fraction: Decimal) -> CalculationResult
def interval_union(intervals: Sequence[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]
def run_calculation(calculation_id: CalculationId, inputs: Mapping[str, Decimal],
                    units: Mapping[str, str]) -> CalculationResult        # диспетчер по CalculationId

# правка F1, п.1 — подключение расчётов к живому пути вопроса (вызывающий: service._calculation_section)
class CalculationPlan:    # frozen dataclass: calculation_id: str | None, inputs: dict[str, Decimal],
                          # units: dict[str, str], intervals: tuple[tuple[datetime, datetime], ...],
                          # refusal_reason: str | None

def plan_from_question(text: str) -> CalculationPlan | None
def known_refusal_reason(text: str) -> str | None       # дополнение контроллера, п. T-05/б/в/г (см. ниже)
```

`plan_from_question(text)` разбирает ИСХОДНЫЙ текст вопроса (не нормализованный — числа/единицы/маркеры читаются
буквально, запятая и точка как десятичный разделитель, `Decimal`, без `eval`) в план для одной из четырёх
`CalculationId` или `None` (ничего не распознано — вызывающий отвечает общим уточнением). `inputs`/`units` могут
быть НЕПОЛНЫМИ — отсутствующий именованный вход не ошибка этого модуля, а неизвестная база: сам
`run_calculation` отдаёт её как `missing_basis` (T-03: «Расход 200, концентрация 4» → план есть, входов нет →
`run_calculation` вернёт `value=None`). Распознанный, но РОВНО нулевой вход (например, «0 % по массе») остаётся
в `inputs` со значением `Decimal(0)` — ноль ≠ пропуск (мастер-промпт §8/§11), формулы (`_require_fraction`)
нулевую долю уже принимают как есть, здесь это только проверено отдельным тестом, код формул не менялся.

`known_refusal_reason(text) -> str | None` — четыре СТРУКТУРНЫХ отказа (дополнение контроллера 14.10, не
недостающий вход, а род задачи — уточнение базы/единицы исход не изменит): перевод кН/м (линейной нагрузки) в
бар/МПа/кПа (T-05); перевод избыточного/манометрического давления в абсолютное без базы (местного атмосферного
давления/способа измерения); расчёт COV/коэффициента вариации по данным экрана Valmet (алгоритма в корпусе нет;
общий статистический коэффициент вариации — не то же самое, что показатель COV экрана); сравнение КДМ-1 с
другими заводами/странами/отраслью (в корпусе нет данных для межзаводского/межотраслевого сравнения, статистика
не выдумывается). Используется и в `plan_from_question` (готовый план-отказ, `calculation_id` — короткая
русская метка категории, НЕ из `CalculationId`, только для текста секции), и в `preanalysis.
requires_clarification` (не запрашивать уточнение единицы/базы раньше этого отказа, см. раздел `preanalysis.py`).

## `analytics/validator.py` — Task 3 (дополняется Task 5)

Вызывает: `service.py`, сразу после `llm_adapter.request_selection`.

```python
MAX_SELECTION_RETRIES: int = 2

def validate_selection(selection: LlmSelection, preanalysis: Preanalysis, pack: tuple[EvidenceCard, ...],
                       approved_card_ids: frozenset[str] = frozenset()) -> ValidationOutcome
def check_claim_mutation(card: EvidenceCard, proposed_text: str) -> str | None       # причина отказа или None
def check_calculation_consistency(result: CalculationResult, preanalysis: Preanalysis) -> str | None
```

`approved_card_ids` — дополнение контроллера к Task 7, шаг 2 (не меняет Task 3b): id критических местных
карточек (`critical_local_fact=True`) с ДЕЙСТВУЮЩИМ согласованием, собранным `service.py` ДО вызова через
`reviews.ReviewStore.approved_claim` (сам `validator.py` `ReviewStore` не знает и не импортирует). Критическая
карточка проходит, если `publication_state` уже в `APPROVED_CRITICAL_FACT_STATES` ИЛИ её `id` — в этом
множестве; умолчание — пустое множество, старое поведение (все вызывающие Task 3b/Task 4, что ещё не передают
параметр, не меняют смысла своих тестов). Проверяется ПОСЛЕ `RESTRICTED_SOURCE_IDS` (PUB-10/11) — согласование
критического факта не обходит ограничение источника.

## `analytics/rendering.py` — Task 3

Вызывает: `service.py`, после `validator.py`/`calculations.py`.

```python
DOCUMENT_OBSERVATION_PREFIXES: tuple[str, str]     # ("В предоставленной модели указано: ", "На снимке указано: ")
HYPOTHESIS_TEMPLATE: str                           # "Общий возможный механизм; причина на КДМ-1 не установлена."

def render_claim_section(card: EvidenceCard) -> Section
def render_mechanism_section(card: EvidenceCard) -> Section
def render_calculation_section(result: CalculationResult) -> Section
def render_clarification_section(clarification_keys: tuple[str, ...]) -> Section
def render_response(request_id: str, conversation_id: str, state: str, corpus_version: str, stage_label: str,
                    outcome: ValidationOutcome, sections: tuple[Section, ...]) -> JobResponse
```

## `analytics/llm_adapter.py` — Task 5

Вызывает: `service.py`, когда `preanalysis.decision == "ready"` и роль `llm` настроена. Использует:
`llm/request.py::rebuild_body`, `LlmRole.begin_call`/`LlmCall.finish`, `llm/api.py::forward` — **не** вызывает
`Upstream.open_chat` напрямую.

```python
class LlmAdapterRefusal(Exception):  # .reason — код отказа (как llm/api.py::_Ask.refuse: "config","stopped",…)

def select_model(role: LlmRole, analytics_models: str) -> ModelPolicy
def build_raw_body(preanalysis: Preanalysis, pack: tuple[EvidenceCard, ...], *, schema: dict,
                   question: str = "") -> dict
async def request_selection(role: LlmRole, identity: RequestIdentity, session: str, raw_body: dict, *,
                            analytics_models: str, teacher_payer: str = "") -> LlmSelection
```

`teacher_payer` — дополнение Task 5 (коммит 830a782, `AnalyticsSettings.ANALYTICS_TEACHER_PAYER`): пусто
(умолчание) — преподавателю отказ `LlmAdapterRefusal(TEACHER_PAYER_REFUSAL)` раньше журнала/бюджета/сети;
`service.py` (Task 4/7, шаг 2) передаёт сюда `self.settings.ANALYTICS_TEACHER_PAYER` из `_select_with_llm` и
ТЕМ ЖЕ условием строит `Capabilities.llm_available` для преподавателя в `capabilities()` — оба места
проверяют один и тот же флаг «настроено», не расходятся (иначе `capabilities` сказала бы «доступно», а вызов
отказал бы). Предел исправлений (мастер-промпт §8) для платного вызова аналитики — РОВНО НОЛЬ: отказ
`LlmAdapterRefusal` (включая `"invalid_selection"`) или любой иной сбой адаптера не повторяет платный вызов,
`service._build_outcome` сразу откатывается на детерминированный путь через `validator.validate_selection`
(«Правка, раунд 1, Critical», см. раздел `service.py` ниже) или `limited_answer`; `validator.
MAX_SELECTION_RETRIES` — задел на будущий контролируемый повтор самого выбора модели, этим откатом не
используется.

**Итоговая правка F6 (14.10.2026, решение владельца, дословно): «4. Разрешен полный контент в тч внутренний и
данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!»** — `build_raw_body`
получил параметр `question: str = ""` (`AnalysisRequest.question`, передаёт `service.py::_select_with_llm`,
не `preanalysis.py`) и теперь принимает `preanalysis.llm_export_policy` со значением `approved_subset`, не
только `public_only` (`none` по-прежнему отклоняется `ValueError` — модель с этой политикой не вызывается
вовсе, выключатель в `service.py`, см. ниже). При `approved_subset` пользовательское сообщение ДОПОЛНИТЕЛЬНО
несёт `question` и `cards` — список `{"id", "text", "fragments": [{"fragment_id", "locator"}, ...]}` того же
разрешённого и физически присутствующего в пакете подмножества, что и у `public_only` (двойная сверка
`allowed_claim_ids`/`allowed_mechanism_ids` × `pack` не слабее); текст карточек урезается пропорционально под
общий предел `_APPROVED_SUBSET_BODY_CHAR_BUDGET = 60_000` символов с явной пометкой урезания
(`_budget_card_texts`), а не молча. Формат и строгая проверка ответа модели (только id по `llm-selection.
schema.json`, `_parse_selection`) этой правкой не меняются — политика расширяет вход модели, не то, что она
имеет право вернуть клиенту; сырой текст модели клиенту не выводится ни при одном значении.
`service._select_with_llm` подставляет `self.settings.ANALYTICS_LLM_EXPORT_POLICY` в `Preanalysis.
llm_export_policy` НЕПОСРЕДСТВЕННО перед этим вызовом (`analysis.model_copy(update={...})`) — `preanalysis.py`
не правится этой задачей и по-прежнему всегда ставит `public_only`/`none`.

## `analytics/data_bridge.py` — Task 7

Вызывает: `service.py`/`rendering.py`, только после `reviews.resolved_link`. Использует `DataService` ровно
теми же методами, что `gateway/calc_api.py` (не меняет `data/service.py`).

```python
CHANNEL: Literal["web"]

def caller_of(identity: RequestIdentity) -> str
async def catalog_search(svc: DataService, identity: RequestIdentity, query: str, limit: int = 10) -> dict
async def tag_now(svc: DataService, identity: RequestIdentity, link: ResolvedLink) -> dict
async def tag_period(svc: DataService, identity: RequestIdentity, link: ResolvedLink, period: str | None,
                     start: str | None, end: str | None) -> dict
async def tag_series(svc: DataService, identity: RequestIdentity, link: ResolvedLink, period: str | None,
                     start: str | None, end: str | None, points: int | None = None) -> dict
async def calc_runtime(svc: DataService, identity: RequestIdentity, link: ResolvedLink, period: str | None,
                       start: str | None, end: str | None) -> dict
```

## `analytics/reviews.py` — Task 7

Вызывает: `gateway/analytics_api.py` ручки `reviews/*` (через `service.py` или напрямую — решение Task 7, но
модель данных — эта).

```python
ReviewKind = Literal["local_claim", "equipment_link", "local_norm", "conflict"]
ReviewDecision = Literal["accepted", "rejected", "info_requested"]
ReviewStatus = Literal["pending", "accepted", "rejected", "info_requested"]
ReviewersMode = Literal["grants", "all_students"]                                 # F5, 14.10.2026 (см. ниже)

class ReviewStoreError(Exception): ...          # отказ по правилам (заявка не найдена/нет видимости)
class ReviewStoreUnavailable(ReviewStoreError): ...   # F5, п.6 — настоящий сбой SQLite, gateway отвечает 503
class AuthorityGrant:   # frozen dataclass: login, scope, decision_types, granted_by, granted_at, revoked_at,
                        # valid_until (F3)
class ReviewRequest:    # frozen dataclass: id, kind, subject_id, old_value, new_value, scope, period_start/end,
                        # rationale, status, created_by, created_at, decided_by, decided_at, comment,
                        # decided_by_role (F5 — роль решившего на момент decide(), см. ниже)
class ResolvedLink:     # frozen dataclass: link_id, process_id, historian_tag, unit, approved_by, approved_at, scope

class ReviewStore:
    def __init__(self, db_path: str)
    def open(self) -> bool
    def grant_authority(self, login: str, scope: str, decision_types: tuple[ReviewKind, ...], *,
                        granted_by: RequestIdentity, valid_until: datetime | None = None) -> AuthorityGrant
    def bootstrap_authority(self, login: str, scope: str, decision_types: tuple[ReviewKind, ...], *,
                           operator: str, valid_until: datetime | None = None) -> AuthorityGrant  # правка раунд 1
    def revoke_authority(self, login: str, scope: str, *, revoked_by: RequestIdentity) -> bool
    def active_grant(self, login: str, scope: str, decision_type: ReviewKind) -> AuthorityGrant | None
    def has_any_active_grant(self, login: str) -> bool                           # Task 7, шаг 2 (см. ниже)
    def create_request(self, kind, subject_id, new_value, scope, rationale, *, created_by, old_value=None,
                       period_start=None, period_end=None) -> ReviewRequest
    def list_requests(self, viewer: RequestIdentity, *, status: ReviewStatus | None = None,
                      reviewers_mode: ReviewersMode = "grants") -> list[ReviewRequest]          # F5
    def decide(self, request_id: str, decision: ReviewDecision, comment: str, *,
              decided_by: RequestIdentity, reviewers_mode: ReviewersMode = "grants") -> ReviewRequest  # F5
    def resolved_link(self, link_id: str, *, reviewers_mode: ReviewersMode = "grants") -> ResolvedLink | None  # F5
    def has_any_resolved_link(self) -> bool                                      # Task 7, шаг 2 (см. ниже)
    def approved_claim(self, card_id: str, scope: str, at: datetime | None = None, *,
                       reviewers_mode: ReviewersMode = "grants") -> ReviewRequest | None                # F5
```

**Правка F5 (итоговое ревью, 14.10.2026, решение владельца)** — `ANALYTICS_REVIEWERS` (`analytics/settings.py`):
`grants` (умолчание — прежнее поведение без исключений) или `all_students` («любому студенту без утверждения
со стороны преподавателя... любой утверждает без дополнительных утверждений», владелец, 14.10.2026). Параметр
`reviewers_mode`, умолчание `"grants"` у каждого метода — не меняет поведение существующих вызывающих. В
режиме `all_students`: `decided_by.role == "student"` в `decide()` — видимость/самозапрет/второй независимый
согласующий (F3) не применяются, любая заявка решается в одно действие (терминальный статус — исключение,
остаётся); `list_requests` — `viewer.role == "student"` видит все заявки области; `approved_claim`/
`resolved_link` — решение студента без реального полномочия (`ReviewRequest.decided_by_role == "student"`,
записано `decide()`) действует только пока `reviewers_mode == "all_students"` в момент ПРОВЕРКИ (не решения):
переключение на `grants` обесценивает его для нового применения, не трогая журнал. `teacher` — исключение из
`all_students` в любом из этих четырёх методов: только по действующему полномочию, как в `grants` (мастер-
промпт §6 это решение не отменяет). `capabilities().review_authority` (`service.py`) — `True` для `student` в
`all_students` БЕЗ обращения к `ReviewStore` (настроечный факт, не чтение стора).

`approved_claim` — дополнение контроллера к Task 7 (2026-10-14, стык с `validator.py`): действующее принятое
решение (`kind` в `local_claim`/`local_norm`/`conflict`, `subject_id == card_id`, в `scope`, в сроке
`period_start..period_end`, полномочие решившего не отозвано — проверяется заново, «здесь и сейчас», не по
факту решения в прошлом) или `None`. Нужно, чтобы `validator.APPROVED_CRITICAL_FACT_STATES =
{"reviewer_approved"}` (Task 3b, 7b22774) опиралось на настоящее согласование, не на статичную метку корпуса;
подключение к `validator.py`/`service.py` — сделано в Task 7, шаге 2 (см. разделы `validator.py`/`service.py`
ниже).

**Task 7, правка раунд 1** (ревью контроллера на коммит `1ebb538`): бутстрап первого полномочия на пустом
хранилище держался только отсутствием веб-маршрута к `grant_authority` — закрыто кодом. `grant_authority`
теперь ВСЕГДА требует у `granted_by` действующего полномочия (любой `decision_type`) в той же `scope`, без
исключения для пустого хранилища; единственный путь завести самый первый ряд `review_authority_grants` —
новый `bootstrap_authority(login, scope, decision_types, *, operator: str)` (не `RequestIdentity`, такую
сигнатуру `gateway`/`service.py` не вызывают), требует полностью пустого хранилища (иначе `ReviewStoreError`).
CLI получил подкоманду `bootstrap` (рядом с `grant`/`revoke`/`list`, `python -m pcbk_core.analytics.reviews
bootstrap --login L --scope S --types T --by BY`). `resolved_link` — докстринг и тест явно фиксируют: срок
заявки `equipment_link` НЕ проверяется (связь не временное утверждение, в отличие от `approved_claim`).

**Task 7, шаг 2** (подключение к `service.py`/`gateway/analytics_api.py`, стык с `validator.py`):

- `decide` теперь запрещает автору заявки решать её самому, даже держа нужное действующее полномочие
  (`PermissionError`) — симметрично запрету самоназначения полномочия (T-44); пустой/пробельный `comment`
  хранится как `None`, не как `""` (веб `analytics.ts::isNullableText` не принимает пустую строку, только
  `null` или непустую).
- `has_any_active_grant(login) -> bool` — хоть одно действующее полномочие логина в ЛЮБОЙ `scope` (не только
  `"KDM1_PCBK"` целиком); `has_any_resolved_link() -> bool` — хоть одна действующая связь оборудования где
  угодно (та же повторная проверка полномочия решившего, что у `resolved_link`, по всем принятым заявкам
  `equipment_link`). Оба — для `service.Capabilities` (`review_authority`/`data_bridge_available`), не нужны
  ручкам `reviews/*` напрямую.

## `analytics/service.py` — Task 4

Вызывает: только `gateway/analytics_api.py`. Вызывает: `preanalysis.py`, `llm_adapter.py`, `validator.py`,
`calculations.py`, `rendering.py`, `store.py`, `reviews.py`, `data_bridge.py`, `knowledge.py`.

```python
class Capabilities:  # frozen dataclass: reference_available, llm_available, data_bridge_available,
                     # review_authority, corpus_version
class ConversationTurn:  # frozen dataclass: question: str, response: JobResponse — правка раунд 1 (см. ниже)

class AnalyticsService:
    def __init__(self, settings: AnalyticsSettings, *, calc: DataService | None = None, llm: LlmRole | None = None)
    async def open(self) -> None
    async def aclose(self) -> None
    def capabilities(self, identity: RequestIdentity) -> Capabilities
    def list_conversations(self, identity: RequestIdentity) -> list[ConversationMeta]
    def create_conversation(self, identity: RequestIdentity, mode: AnalysisMode) -> str
    def get_conversation(self, identity: RequestIdentity, conversation_id: str) -> ConversationMeta | None
    async def submit_question(self, identity: RequestIdentity, conversation_id: str,
                              request: AnalysisRequest) -> str                       # request_id
    def get_request(self, identity: RequestIdentity, request_id: str) -> JobResponse | None
    async def cancel_request(self, identity: RequestIdentity, request_id: str) -> bool
    def get_evidence(self, identity: RequestIdentity, evidence_id: str) -> dict | None

    # дополнение Task 4 — не меняет сигнатур выше; тип элемента списка изменён правкой раунд 1 (см. ниже)
    async def list_requests(self, identity: RequestIdentity,
                            conversation_id: str) -> list[ConversationTurn] | None

    # дополнение Task 7, шаг 2 — не меняет сигнатур выше; тонкий проброс в reviews.ReviewStore (см. ниже)
    async def list_review_requests(self, identity: RequestIdentity) -> list[ReviewRequest]
    async def create_review_request(self, identity: RequestIdentity, kind: ReviewKind, subject_id: str,
                                    new_value: str, scope: str, rationale: str, *, old_value: str | None = None,
                                    period_start: datetime | None = None,
                                    period_end: datetime | None = None) -> ReviewRequest
    async def decide_review_request(self, identity: RequestIdentity, review_id: str, decision: ReviewDecision,
                                    comment: str) -> ReviewRequest
```

Реализация Task 4, важные решения для Task 5/7 (не меняют сигнатуры выше без правки этого файла в том же
коммите):

**Правка итогового ревью F1 (14.10) — три дополнения, без изменения публичных сигнатур выше:**

- *(п.1, Critical)* `_build_outcome` получил новый параметр `request: AnalysisRequest` (между
  `conversation_id` и `analysis`) — приватный метод, не публичная сигнатура `AnalyticsService` из блока выше,
  но единственный вызывающий (`_run_job`) теперь передаёт его. Когда `analysis.intent == "calculation"` (и
  решение уже не `needs_clarification`/`refused`/`control` — эти три решены раньше в той же функции): новый
  приватный `_calculation_section(question, analysis)` строит план (`calculations.plan_from_question`),
  выполняет его (`calculations.run_calculation`/`calculations.interval_union`), проверяет `validator.
  check_calculation_consistency`, отдаёт `rendering.render_calculation_section` (значение есть — `calculation`;
  нет — `limitation`, тот же шаблон, что пропуск базы) — эта секция идёт ПЕРВОЙ в ответе, карточки пакета —
  следом, как обычно. Число считает только сервер — `self.llm`/`llm_adapter.py` в эту ветку не входит вовсе.
  Состояние: посчитано число — `completed` (если `decision == "ready"`, иначе `limited_answer`, как у обычного
  пути); только ограничение/уточнение — `limited_answer` независимо от `decision`.
- *(п.3, Important)* `store.sweep_retention` раньше не вызывался нигде. `open()` теперь делает один проход
  сразу после успешного открытия состояния (`_sweep_retention_once`, synchronous сбой — строка журнала, не
  исключение) и запускает фоновую задачу `_retention_loop` (раз в модульную константу
  `RETENTION_SWEEP_INTERVAL_S = 3600.0` — повторяет тот же проход); задача отменяется в `aclose()` вместе с
  заданиями `self._jobs`. Сбой `store.open()` (ранний `return`) — ни проход, ни задача не запускаются (как и
  раньше, state просто не готов).
- *(п.5, Important)* `_persist_evidence` дополняет каждый элемент пакета доказательств (формат GET `.../
  evidence/{id}`, НЕ контракт заказчика `EvidenceRef`) тремя полями — `source_title`/`source_type`/
  `source_date`, из записи источника корпуса (`knowledge.get_row(handle, "sources", ref.source_id)` →
  `title`/`type`/`reviewed_on`); нет такой записи источника — все три `None`, не выдуманы. См. раздел ниже.

- **`validating` — двойной смысл, не путать с метрикой.** `store.set_preanalysis` переводит задание в стадию
  `validating` для ВСЕХ решений преданализа кроме `ready` (`needs_clarification`/`limited_answer`/`refused`) —
  это «парковка» перед публикацией, не отдельная фаза проверки модели (та существует только на пути `ready` с
  LLM). `_run_job` идёт от `set_preanalysis` до `rendering.render_response`/`store.publish_response` одним
  проходом без промежуточных `await` внешней службы для всех «не-ready» решений — задание либо долетает до
  терминальной стадии (`needs_clarification`/`completed`/`limited_answer`/`failed`), либо весь проход падает в
  `except` `_guarded_run_job` и становится `failed` сторожевым сроком. Счётчик/дашборд по стадии `validating` не
  должен считать её как «идёт проверка модели» без различения decision — иначе метрика лжёт (просьба
  координатора 14.10).
- **Сторожевой срок и параллелизм.** `AnalyticsSettings.ANALYTICS_JOB_TIMEOUT_S` — срок ОДНОГО прохода
  `_run_job` (через `asyncio.wait_for` в `_guarded_run_job`): превышен — `failed`, без повторной оплаты (новая
  отправка/отмена видят уже терминальную стадию). `MAX_CONCURRENT_JOBS = 4` (модуль `service.py`) —
  `asyncio.Semaphore` ограничивает число одновременно обрабатываемых заданий; очередь — естественная (задание
  остаётся в стадии `queued`, пока не возьмёт семафор).
- **`evidence_id` — составной `request_id:pack_id:item_id`** (`service.encode_evidence_id`/`EVIDENCE_SEP`):
  цепочка прав мастер-промпта §10 («пакет принадлежит заданию, задание — диалогу, диалог — identity»)
  проверяется ДВАЖДЫ (правка раунд 1, Important 3): `request_id` → `store.get_request(...).owner_login`, и
  `pack_id` → `store.evidence_pack_owner(pack_id) == request_id` — без второй проверки свой `request_id` с
  чужим `pack_id` в составном id проходил бы. `_run_job` (`_persist_evidence`, правка раунд 1, Important 2)
  сам сохраняет пакет (`store.save_evidence_pack`) ДО публикации и переписывает `EvidenceRef.fragment_id` каждой
  секции ответа в составной `evidence_id` — Task 3/5 (`preanalysis.py`/`rendering.py`) ничего для этого делать
  не должны, они кладут «сырой» id фрагмента корпуса (так уже и было, докстринг `preanalysis.build_evidence_pack`:
  «`evidence_pack_id` всегда `None` — `service.py` заполняет реальный id после `store.save_evidence_pack`»).
  Веб (`web/src/components/AnalyticsSources.tsx`) зовёт `GET .../evidence/{id}` ровно с `EvidenceRef.fragmentId`
  как есть — после `_persist_evidence` это уже составной id, не сырой.
- **LLM осознанно не подключён.** `main.py::build_roles` строит `AnalyticsService(..., llm=None)` даже когда
  роль `llm` в процессе настроена и здорова — иначе `capabilities().llm_available` обещала бы оплачиваемый
  разбор, которого на деле нет (`llm_adapter.py` — заглушка `NotImplementedError` до Task 5). `_run_job`
  структурно уже содержит точку вызова (`_select_with_llm`, вызывается только когда `decision == "ready"` и
  `self.llm is not None`) и перехватывает `LlmAdapterRefusal`/`NotImplementedError`/любой сбой адаптера как
  «LLM недоступен сейчас» — откат на карточки пакета преданализа без модели, не падение задания. Task 5,
  подключив `llm_adapter.py`, должен в `build_roles` заменить `llm=None` на `llm=llm` одной строкой — `service.py`
  и `gateway/analytics_api.py` менять не нужно.
- **`data_bridge_available` — план Д17, Task 1 (14.10): СНИМАЕТ решение контроллера Д16, правка F1, п.2
  («ВСЕГДА `False`»).** Контракт ответа (`job-response.schema.json#SectionKind`) теперь несёт
  `telemetry_observation` — решение владельца по формату секции и по полномочиям согласования (Task 7) принято
  раньше. `data_bridge_available = self.calc is not None and getattr(self.calc.role, "config_error", None) is
  None` (защитно через `getattr`, не `AttributeError` на двойнике без `.role`) — НЕ зависит от того, есть ли
  хоть одна согласованная связь оборудования: конкретную подпись без связи справка показывает отдельным
  ограничением (`render_telemetry_unresolved_section`), не выключает доступность службы целиком.
- **`review_authority`/`reviews/*` — подключены Task 7, шаг 2 (не меняет этот раздел Task 4 структурно).**
  `AnalyticsService.__init__` строит `self.reviews = ReviewStore(settings.ANALYTICS_STATE_PATH)` (тот же файл,
  что `store.py`, свои таблицы); `open()` открывает его НЕЗАВИСИМО от `self.store` (отдельный
  `self.reviews_error`, не блокирует и не блокируется основным `config_error`) — `gateway/analytics_api.py::
  _reviews_broken` отвечает своим 503, если согласование не открылось, тогда как остальная справка может
  работать. `capabilities()`: `review_authority = reviews.has_any_active_grant(identity.login)` (действующее
  полномочие ЭТОГО логина в любой `scope`). Сбой `ReviewStore` в момент вызова — честные `False`, не падение
  `capabilities()`. `reviews/*` ручки `gateway/analytics_api.py` — настоящая реализация (не заглушка):
  `list_review_requests`/`create_review_request`/`decide_review_request` сервиса — тонкий проброс в
  `reviews.py`, вся политика (видимость/право/самозапрет) там; ручка различает ответ по ТИПУ исключения —
  `ReviewStoreError` → 404 (заявка не найдена, как и у `submit_question`/`StoreError`), `PermissionError` → 403
  (нет права ИЛИ автор решает свою же заявку), `ValueError` → 409 (заявка уже решена — через эту ручку
  `decision` уже ограничен `Literal` схемой тела, иное значение `ValueError` сюда не доходит, отбито раньше
  как 400). Критический местный факт (мастер-промпт §8, `validator.APPROVED_CRITICAL_FACT_STATES`):
  `_build_outcome` перед вызовом `validator.validate_selection` на ОБОИХ путях (настоящий выбор и
  детерминированный) собирает `approved_card_ids` через новый приватный `_approved_critical_card_ids` —
  `reviews.approved_claim(card.id, analysis.scope, now)` для каждой `critical_local_fact` карточки пакета,
  один раз на задание, не кэшируется дольше одного прохода (тот же принцип, что `self._pending`).
  `teacher_payer` (хвост Task 5, коммит `830a782`): `_select_with_llm` передаёт в `llm_adapter.
  request_selection` `self.settings.ANALYTICS_TEACHER_PAYER`, и `capabilities()` строит `llm_available`
  преподавателю ровно тем же условием (`ANALYTICS_TEACHER_PAYER` непусто) — оба места не расходятся. Предел
  исправлений платного вызова (мастер-промпт §8) для аналитики — РОВНО НОЛЬ: отказ `LlmAdapterRefusal`
  (включая `"invalid_selection"`) или любой иной сбой адаптера не повторяет вызов, сразу откатывается на
  детерминированный путь/`limited_answer` (см. раздел `llm_adapter.py` выше).
- **Гоча тестов: TestClient без персистентного портала обрывает фоновые задания.** `TestClient(app)`, не
  открытый через `with` (ровно так `helpers.Gw.as_()` строит клиента на каждый логин), создаёт СВОЙ
  anyio-портал на КАЖДЫЙ HTTP-вызов и закрывает его сразу после — что отменяет любую фоновую `asyncio.Task`
  (в т.ч. `_run_job`, запущенный `submit_question`), не успевшую завершиться до конца ТОГО ЖЕ запроса. Опрос
  `GET .../requests/{id}` через `Gw.as_()` после `POST .../questions` поэтому НИКОГДА не увидит прогресс — не
  баг `service.py`, а свойство `starlette.testclient._portal_factory` без входа в `with`. Тесты аналитики с
  фоновым заданием заводят ОДИН персистентный `TestClient(...)` через `with` на весь тест и шлют все вызовы
  через него (кука — заголовком на каждый вызов, не через cookie jar клиента — два логина одним клиентом иначе
  дали бы вторую `pcbk_session` и шлюз отверг бы обе, Д5-R25); приём — `core/tests/test_gw_analytics_api.py`,
  класс `AnGw`/`_AnUser`. Эта гоча касается будущих тестов Task 7/9 с похожими фоновыми заданиями, не только
  аналитики.
- **Формы JSON ответов — зеркало `web/src/analytics.ts` (Task 6, коммит f6c0c95), кроме `GET conversations/{id}`
  (правка раунд 1, см. ниже).** `capabilities`:
  `{"reference_available", "llm_available", "data_bridge_available", "review_authority", "corpus_version"}` —
  `corpus_version` веб ждёт НЕПУСТОЙ строкой всегда (`isText`), поэтому `gateway/analytics_api.py` подставляет
  `"unavailable"`, когда `Capabilities.corpus_version is None` (корпус не открыт). `POST conversations` →
  `{"id": ...}` (201). `GET conversations` → `{"conversations": [ConversationMeta, ...]}`. `POST questions` →
  `{"request_id": ...}` (202). `GET requests/{id}` и `POST requests/{id}/cancel` — ОБА отдают полный
  `JobResponse` (200): веб (`AnalyticsPage.tsx`) разбирает ответ `cancel` тем же `parseJobResponse`, не
  отдельным `{"cancelled": bool}`.

**Правка, раунд 1 (замечания контроллера на коммит `385a260`) — сводка, подробности у затронутых методов
выше и в `store.py`:**

1. *Critical.* Путь без LLM (по умолчанию единственный живой) и путь «LLM упал» (`_select_with_llm` → `None`)
   публиковали `allowed_claim_ids`/`allowed_mechanism_ids` преданализа напрямую, минуя `validator.
   validate_selection` — повторная проверка «в момент выдачи» (`publication_state`/`critical_local_fact`/версия
   корпуса ссылки/PUB-10-11) не работала на этих путях. Исправлено: `_deterministic_outcome` строит
   синтетический `LlmSelection` ровно из разрешённого пакета (срез до предела контракта — 20 карточек/6
   механизмов) и проводит его через тот же `validate_selection`, что настоящий выбор модели. Если после
   проверки ничего не осталось — `limited_answer`, не пустой `completed`.
2. *Important.* `_run_job` не сохранял пакет доказательств (`store.save_evidence_pack`), а `EvidenceRef.
   fragment_id` оставался «сырым» id фрагмента корпуса — `GET .../evidence/{id}` не мог найти то, что показал
   ответ. Исправлено: `_persist_evidence` сохраняет пакет ДО публикации и переписывает `fragment_id` в
   составной `evidence_id` каждой секции.
3. *Important.* `get_evidence` не сверял, что `pack_id` составного id принадлежит именно `request_id` того же
   id — свой `request_id` с чужим `pack_id` проходил бы. Исправлено: `store.evidence_pack_owner` (см.
   `store.py`).
4. *Important.* `cancel_request` решал, прерывать ли фоновую `asyncio.Task`, по стадии из ОТДЕЛЬНОГО более
   раннего чтения (`store.get_request`) — окно гонки между этим чтением и самой отменой. Исправлено:
   `store.cancel_request` теперь возвращает `(cancelled, stage_before)` из ОДНОЙ транзакции (см. `store.py`).
5. *Решение контроллера (Minor «д»).* `GET conversations/{id}` отдаёт историю как `{"conversation": …,
   "requests": [{"question": str, "response": JobResponse}, …]}` — КАЖДЫЙ элемент `requests` теперь объект
   `{question, response}`, не голый `JobResponse` (форма веба Task 6, f6c0c95, этого поля не ждала — обновление
   `web/src/analytics.ts`/`ConversationDetail` под новую форму не входит в Task 4, передано дальше). `question`
   — `store.RequestRecord.question`, владельцу диалога, чужим — ручка по-прежнему отвечает 404 целиком.

**Правка итогового ревью F4a (14.10, `.superpowers/sdd/PLAN-KDM1-analytics-2026-10-14/final-fix-F4a-brief.md`)
— пять дополнений, не меняющих публичные сигнатуры `AnalyticsService` из блока выше:**

1. *Свежая проверка в момент выдачи (ОСОБЫЙ ФОКУС №2, усиление).* Новый приватный `_refresh_pack(pack) ->
   tuple[EvidenceCard, ...]` — вызывается из `_build_outcome` НЕПОСРЕДСТВЕННО перед `validator.
   validate_selection` (оба пути: control и ready/limited_answer; LLM и детерминированный берут ОДИН и тот же
   уже-свежий `pack`, т.к. рефреш — до их разветвления). Перечитывает каждую карточку по `id`
   (`knowledge.get_rows`) и собирает заново ТЕМ ЖЕ `preanalysis._build_card`, что и исходный пакет при поиске —
   снимок `EvidenceCard`, построенный `preanalysis.analyze` на поиске, больше не доверяется напрямую. Карточки,
   которой больше нет в корпусе (отозвана/удалена между поиском и проверкой) — не выходит из `_refresh_pack`
   вовсе; изменился текст/статус — используется свежий (решение контроллера: сам показ и так проверяется
   `validator.validate_selection` по актуальному `publication_state` ПОСЛЕ рефреша). Сбой чтения корпуса —
   пустой пакет (безопасный отказ), не падение задания.
2. *Смена версии корпуса без перезапуска процесса.* `knowledge.version_signature` (см. раздел `knowledge.py`
   выше) — дешёвый признак на диске. Новые приватные методы `AnalyticsService`:
   - `_current_corpus_signature() -> str | None` — обёртка `knowledge.version_signature` в потоке; `None` при
     сбое (вызывающие трактуют это как «нечего сравнивать», не как «версия не менялась»);
   - `_maybe_reopen_corpus() -> str | None` — вызывается `_run_job` В НАЧАЛЕ КАЖДОГО задания, до преданализа:
     признак изменился относительно `self._corpus_signature` → переоткрыть (`knowledge.open_corpus` — дорогая
     операция, хеширует файл целиком, поэтому не на каждый вопрос) и заменить `self._handle`; сбой переоткрытия
     — строка журнала, прежний `self._handle` остаётся рабочим. Возвращает признак, с которым СТАРТУЕТ именно
     это задание (`job_signature` у `_run_job`).

   `_run_job` ДОПОЛНИТЕЛЬНО сверяет признак ПЕРЕД публикацией (`_current_corpus_signature()` ещё раз) с
   `job_signature`: отличаются (корпус сменился МЕЖДУ поиском и выдачей — поиск/платный вызов модели заняли
   время) → не публикует карточки как ни в чём не бывало — `outcome=_terminal_outcome()`, единственная секция
   `limitation` (`service.CORPUS_VERSION_CHANGED_TEXT`, «версия корпуса сменилась — задайте вопрос ещё раз»),
   `state="limited_answer"`; совпадают (или признак не посчитался вовсе на одном из двух концов — `None`) —
   обычная публикация. `open()` теперь также считает `self._corpus_signature` один раз при старте (база для
   первого задания). `docs/kdm1/CORPUS_OPERATIONS.md`, раздел «новая версия подхватывается только после
   пересоздания core» — больше не верен, обновлён в этом же коммите.
3. *Исторический ответ при чтении.* Новый приватный `_with_historical_notice(response: JobResponse) ->
   JobResponse` — вызывается из `_visible_response` (значит, из ОБОИХ `get_request`/`list_requests`): если
   `response.corpus_version` отличается от действующей (`self._handle.version`) НА МОМЕНТ ЧТЕНИЯ, добавляет (не
   сохраняя — `store.py` не трогается) последней секцией `limitation` с текстом «ответ получен по версии X;
   действующая — Y; ответ исторический». Предел секций контракта (`job-response.schema.json#JobResponse.
   sections`, `maxItems=24`) уже достигнут — секция НЕ добавляется (заменить последнюю нельзя — она могла быть
   содержательной), строка в журнал, ответ как есть.
4. *Занятость одного диалога.* Новый атрибут `self._llm_inflight_conversations: set[str]` — не больше одного
   платного вызова модели одновременно на диалог. `_build_outcome`: второй вопрос того же диалога, пока первый
   уже в этом множестве, — модель не вызывает вовсе, сразу `busy=True`. `_select_with_llm` сменил сигнатуру
   возврата (приватный метод, не публичная сигнатура выше) с `ValidationOutcome | None` на `tuple[
   ValidationOutcome | None, bool]` — второй элемент `busy=True` ровно когда `LlmAdapterRefusal("busy")` (предел
   параллельности самого ВЫЗЫВАЮЩЕГО, `role.inflight`, см. раздел `llm_adapter.py` выше). В обоих случаях
   `_build_outcome` строит детерминированный исход (`_deterministic_outcome`, как при любой недоступности LLM),
   помечает его `error_code="busy"` (`dataclasses.replace`) и добавляет секцию `limitation`
   (`service._BUSY_LIMITATION_TEXT`, «в этом диалоге уже готовится ответ с разбором…») — отличимо от обычной
   недоступности (`unavailable`/без кода).
5. *Смешанный вопрос (вместе с F4b).* `_run_job` после `_build_outcome`: если `preanalysis.
   _mentions_other_machine(request.question)` и `analysis.decision != "refused"` — первой секцией
   `limitation` (`service._other_machine_limitation_text`, с названием(ями) чужой машины из той же регулярки,
   что `_mentions_other_machine`) перед остальными секциями ответа. F4b отвечает за то, что решение вообще
   становится не `"refused"`, когда в смешанном вопросе есть допустимая часть про КДМ-1 — эта часть F4a только
   добавляет предупреждение, не меняет само решение преданализа.

Новый публичный модульный хелпер `rendering.py` не получил (это файл F4b) — секция `limitation` для пп.2/4/5
строится прямо в `service.py` (`_limitation_section`, тот же приём, что `rendering.render_control_refusal_
section`: `Section(kind="limitation", ...)`), весь текст — из констант модуля или детерминированной регулярной
проверки `preanalysis.py`, не из текста вопроса/ответа модели как есть.

**Дополнение контроллера к правке F4a (14.10, после коммита F4b `74172d1`) — два дополнения поверх пп.1/4
выше, плюс одна проверка без изменений:**

6. *Подписи кандидатов термина (H-26, стык с F4b).* `_build_outcome` получил новый обязательный параметр
   `handle: CorpusHandle` (шестой позиционный, после `pack`; единственный вызывающий, `_run_job`, передаёт ту
   же переменную `handle`, что сам использует для преданализа/публикации этого задания — НЕ читает `self.
   _handle` заново своим отдельным снимком, который мог бы отличаться от переменной задания, если корпус
   переоткрылся МЕЖДУ стартом этого задания и этой точкой из-за `_maybe_reopen_corpus` ДРУГОГО, конкурентного
   задания). Ветка `needs_clarification`: `parsed = preanalysis.original_and_normalized(request)` →
   `candidate_labels = preanalysis.clarification_candidates(handle, parsed, tuple(analysis.resolved_terms))` →
   `rendering.render_clarification_section(keys, candidate_labels=candidate_labels)` (необязательный параметр
   F4b, умолчание `()` не меняет остальные вызовы этой функции в модуле — `render_control_refusal_section`/
   control-ветку не трогает). `_refresh_pack` (п.1 выше) тоже сменил сигнатуру на `_refresh_pack(pack, handle)`
   — по той же причине (не читает `self._handle` сам).
7. *Честное отключение LLM по политике экспорта.* `AnalyticsSettings.ANALYTICS_LLM_EXPORT_POLICY == "none"` —
   сервер-уровневый выключатель (отдельный от per-запросного `Preanalysis.llm_export_policy`, который
   `preanalysis.py` выставляет за каждое решение преданализа отдельно): `capabilities()` добавляет эту проверку
   к `llm_ok` РАНЬШЕ проверки преподавателя-плательщика; `_build_outcome` не входит в ветку вызова
   `_select_with_llm` вовсе, если политика `"none"` — та же проверка в обоих местах, не расходятся (как
   `ANALYTICS_TEACHER_PAYER` для преподавателя, см. выше).
8. *Проверено без изменений: `store.StoreUnavailable`.* `service.py` НИГДЕ не перехватывает `StoreError`/
   `StoreUnavailable` (F4b, H-18) — все вызовы `self.store.*` идут как есть через `asyncio.to_thread`, сбой
   инфраструктуры хранилища долетает до `gateway/analytics_api.py` непревращённым в «не найдено» (там и дают
   503, не 404 — см. раздел `gateway/analytics_api.py` ниже).

**Правка итогового ревью F5 (14.10.2026, решение владельца) — `ANALYTICS_REVIEWERS`:**

9. `capabilities()`: `identity.role == "student"` И `self.settings.ANALYTICS_REVIEWERS == "all_students"` →
   `review_authority = True` БЕЗ обращения к `self.reviews` (настроечный факт, не чтение стора — не может
   упасть `ReviewStoreError`); иначе — прежняя ветка `reviews.has_any_active_grant`. `teacher` в этом режиме
   НЕ затрагивается — проверяется только `reviews.has_any_active_grant`, как раньше.
10. `list_review_requests`/`decide_review_request`/`_approved_critical_card_ids` передают
   `reviewers_mode=self.settings.ANALYTICS_REVIEWERS` в `reviews.list_requests`/`decide`/`approved_claim` —
   тонкий проброс настройки, вся политика режима — в `reviews.py` (см. раздел выше, «Правка F5»).
   `reviews.ReviewStoreUnavailable` (сбой самого SQLite) уходит как есть через `asyncio.to_thread`, тем же
   приёмом, что `store.StoreUnavailable` в п.8 — `gateway/analytics_api.py` отображает в 503 (см. ниже).

**Итоговая правка F6 (14.10.2026, решение владельца, дословно): «4. Разрешен полный контент в тч внутренний и
данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!»** — политика передачи в
LLM (`ANALYTICS_LLM_EXPORT_POLICY`):

11. `_select_with_llm` подставляет `self.settings.ANALYTICS_LLM_EXPORT_POLICY` в `Preanalysis.
    llm_export_policy` НЕПОСРЕДСТВЕННО перед `llm_adapter.build_raw_body` (`analysis.model_copy(update=
    {"llm_export_policy": ...})`, сигнатура получила новый kwarg `question: str = ""`) — не в
    `preanalysis.py` (не правится этой задачей, по-прежнему всегда ставит `public_only`/`none`) и не раньше в
    `_build_outcome`. `question` — `AnalysisRequest.question`, передаёт `_build_outcome` при вызове
    `_select_with_llm`; `validator.validate_selection` ниже по-прежнему получает ИСХОДНЫЙ `analysis` (политика
    экспорта не входит в проверку смысла/прав).
12. `intent == "control"` — по-прежнему без модели вовсе, независимо от значения `ANALYTICS_LLM_EXPORT_
    POLICY`: ветка `_build_outcome` для `control` возвращает ответ раньше, чем доходит до `self.llm`/
    `_select_with_llm` (не новая проверка — существующий порядок веток этой же версии кода, см. выше).
13. `capabilities()`/`"none"`-выключатель (п.7 выше) не меняются этой правкой: `approved_subset` расширяет
    только то, что ВИДИТ модель внутри уже разрешённого платного пути, не то, доступен ли он вообще.

## `gateway/analytics_api.py` — Task 0 (заглушки), Task 4 (реализация), Task 7 шаг 2 (`reviews/*`)

`analytics_router(role: GatewayRole) -> APIRouter`, подключён в `GatewayRole.router()` только когда
`role.analytics_enabled` (новый конструктор-параметр `GatewayRole.__init__(..., analytics_enabled: bool =
False)`, умолчание `False` — старое поведение не меняется). Выключена — 404 (роутера нет вовсе); включена —
`role.analytics is None` — ВСЕ пути ниже, включая `reviews/*`, отвечают 503 «Аналитика КДМ-1 ещё не готова»;
`role.analytics` подключён (`main.py`, Task 4) — настоящая реализация, свой сбой состояния/корпуса
(`role.analytics.config_error`/`corpus_error`) — отдельный понятный 503 (не падение); сбой ТОЛЬКО согласования
(`role.analytics.reviews_error`) — такой же 503, но лишь на `reviews/*` (остальные пути её не требуют). F5,
п.6 (14.10.2026): сбой самого SQLite хранилища согласования ПОСЛЕ успешного `open()` (`reviews.
ReviewStoreUnavailable`) — ОТДЕЛЬНЫЙ от `reviews_error` 503 (тот же текст `ANALYTICS_STORE_UNAVAILABLE_TEXT`,
что у `store.StoreUnavailable`), на ВСЕХ трёх ручках `reviews/*`, проверяется ДО более общего
`ReviewStoreError` («заявка не найдена» — 404, тип исключения различает ручка, см. `_why`/докстринг файла).
`reviews/*` — настоящая реализация с Task 7, шага 2 (была заглушкой Task 0 независимо от `role.analytics` до
этого коммита): `GET`/`POST reviews/requests`, `POST reviews/requests/{id}/decisions` — формы `_review_json`/
`_reviews_json`, зеркало `web/src/analytics.ts`; маршрута выдачи полномочий через веб НЕТ и не планируется
(только офлайн-команда `python -m pcbk_core.analytics.reviews bootstrap|grant|revoke|list`, см.
`docs/kdm1/REVIEWS_OPERATIONS.md`).

| Путь | Метод | Константа |
|---|---|---|
| `/pcbk/api/analytics/capabilities` | GET | `CAPABILITIES_PATH` |
| `/pcbk/api/analytics/conversations` | GET, POST | `CONVERSATIONS_PATH` |
| `/pcbk/api/analytics/conversations/{conversation_id}` | GET | `CONVERSATION_PATH` |
| `/pcbk/api/analytics/conversations/{conversation_id}/questions` | POST | `QUESTIONS_PATH` |
| `/pcbk/api/analytics/requests/{request_id}` | GET | `REQUESTS_PATH` |
| `/pcbk/api/analytics/requests/{request_id}/cancel` | POST | `REQUEST_CANCEL_PATH` |
| `/pcbk/api/analytics/evidence/{evidence_id}` | GET | `EVIDENCE_PATH` |
| `/pcbk/api/analytics/reviews/requests` | GET, POST | `REVIEW_REQUESTS_PATH` |
| `/pcbk/api/analytics/reviews/requests/{review_id}/decisions` | POST | `REVIEW_DECISION_PATH` |

Эти девять — минимальный зафиксированный набор (мастер-промпт §10 + §6 согласование); Task 7 может **добавить**
дальнейшие пути под `reviews/*` (например, `reviews/grants` для полномочий), но не переименовывать/убирать эти.
Порядок проверок у всех: `front_only → configured(role) → current_account` (как `lessons_api`); у `POST` —
дополнительно `origin_checked` после сессии (как `workplace_router`/`calc_router`).

## Правка плана Д17, Task 1 (14.10.2026): телеметрия БДРВ в ответе

Мастер-промпт §7 п.7/§8, §2 — вывод данных историана в ответ аналитики новой серверной секцией, плюс маршруты
«Связи с историаном». Телеметрия НЕ передаётся в LLM ни в каком виде (секция строится после выбора модели,
только сервером) — Global Constraints плана Д17.

- `models.SectionKind` += `"telemetry_observation"` (и зеркало в `contracts/job-response.schema.json#kind`
  enum). Секция: `evidence_refs=[]`, `claim_id=None`, `rendered_by="server"` — источник значения не корпус, а
  служба данных (`docs/kdm1/DRIFT.md`, запись «Расширение контракта: `telemetry_observation`»).
- `rendering.py` (новые функции, без изменения уже выданных сигнатур выше):
  - `render_telemetry_section(label: str, link: ResolvedLink, reply: dict) -> Section` — значение «сейчас»
    (`data_bridge.tag_now`), текст только из полей ответа `DataService`, без пересчёта; дискретный тег —
    `state_label`; `item["status"] != "ok"` — его `text`.
  - `render_telemetry_period_section(label: str, link: ResolvedLink, reply: dict) -> Section` — то же за
    период (`data_bridge.tag_period`): среднее/минимум/максимум/доля хороших точек + `notes` ответа.
  - `render_telemetry_unresolved_section(label) -> Section`, `render_telemetry_unavailable_section(label) ->
    Section`, `render_telemetry_refusal_section(label, message) -> Section`,
    `render_telemetry_truncated_section(shown, total) -> Section` — четыре фиксированных ограничения (связь не
    согласована / служба не подключена / служба отказала — текст отказа как есть / показаны первые N из M).
- `service.py`:
  - `MAX_TELEMETRY_LABELS = 3` — модульная константа, не больше этого числа подписей на ответ.
  - `AnalyticsService._telemetry_sections(identity, request, handle) -> list[Section]` — единственная точка
    вызова: `_build_outcome` зовёт её ПОСЛЕ ветвей `needs_clarification`/`refused`/`control` (там чтения нет
    вовсе) и ПОСЛЕ выбора карточек пакета (`_render_pack_sections`), раньше расчётной секции ничего не читает
    (секции телеметрии идут ПЕРВЫМИ после расчётной, если она есть). Подписи вопроса —
    `terms.exact_tag_candidates(handle, request.question)`, оставлены только те, что есть в
    `equipment_links.display_label` (подписи `processes.source_label` отфильтрованы — это подпись узла, не
    оборудования). Для каждой — `reviews.resolved_link(id, reviewers_mode=self.settings.ANALYTICS_REVIEWERS)`
    (первая согласованная из строк этой подписи, перепроверяется «здесь и сейчас»); `request.context.period`
    задан → `data_bridge.tag_period`, иначе `data_bridge.tag_now`. Ответ без решённого `_build_outcome` —
    `completed` при `decision=="ready"`, если в `sections` есть хоть одна `telemetry_observation` (то же
    правило, что у ответа с карточками).
  - `AnalyticsService.list_equipment_links(identity) -> list[EquipmentLinkInfo]` — `GET .../analytics/links`
    (см. ниже); `link_candidates(identity, link_id) -> dict | None` — `GET .../links/{id}/candidates`;
    `validate_equipment_link(subject_id, new_value) -> bool` — проверка перед `POST reviews/requests` с
    `kind="equipment_link"` (используется `gateway/analytics_api.py`, не вызывается из `reviews.py`).
  - `capabilities().data_bridge_available` — см. раздел выше.
- `gateway/analytics_api.py`: два новых маршрута (таблица ниже) и проверка `kind="equipment_link"` при
  создании заявки (`subject_id` — id строки `equipment_links` текущего корпуса, иначе 400; `new_value` — имя
  тега по `data.sql.SAFE_NAME`, иначе 400) — проверяется ДО `svc.create_review_request`, через новый
  `svc.validate_equipment_link`.

| Путь | Метод | Константа |
|---|---|---|
| `/pcbk/api/analytics/links` | GET | `LINKS_PATH` |
| `/pcbk/api/analytics/links/{link_id}/candidates` | GET | `LINK_CANDIDATES_PATH` |

`GET .../links` → `200 {"links": [{"id", "display_label", "document_context", "state": "unbound"|"pending"|
"linked", "historian_tag", "approved_by", "approved_at", "pending_request_id"}, ...]}`, порядок — как в
корпусе (по `id`). `linked` — есть `resolved_link`; `pending` — связи нет, но есть заявка `equipment_link`
этого `id` в статусе `pending`/`info_requested`, ВИДИМАЯ вызывающему (та же видимость, что
`reviews.list_requests` даёт самому вызывающему — `reviews.py` не меняется этой задачей, публичного листинга
заявок мимо видимости у него нет: студент без полномочия и не автор заявки в режиме `grants` не увидит чужую
заявку как `pending`, увидит `unbound` — известное ограничение, снимается `ANALYTICS_REVIEWERS=all_students`,
где видимость — все заявки области); иначе `unbound`. Справка недоступна (нет корпуса/состояния) — 503, как у
остальных маршрутов; сбой самого хранилища согласований (`reviews.ReviewStoreUnavailable`) — тот же 503, что у
`reviews/*`.

`GET .../links/{link_id}/candidates` → `200 {"status": str, "message": str | null, "candidates": [{"tag",
"description", "unit"}, ...]}` — один `data_bridge.catalog_search(display_label, limit=10)`; `status`/
`message` — из ответа `DataService` как есть (`message` есть только у отказа). Неизвестный `link_id` — 404.
`DataService` не подключён (`self.calc is None`) — 503 тем же `_unavailable`, что и прочие сбои подсистемы.

## Тесты Task 0 и как их продолжать

| Файл | Проверяет |
|---|---|
| `core/tests/test_analytics_contracts.py` | модели ⇄ схемы: валидные фикстуры принимаются, невалидные (включая лишние поля на любом уровне) — отказ |
| `core/tests/test_analytics_protected.py` | 19 защищённых файлов не меняются относительно `docs/kdm1/PROTECTED_DATA_BASELINE.current.json` |
| `core/tests/test_gw_analytics_api.py` | выключенная аналитика — 404 везде, старые маршруты живы; включённая без `role.analytics` — 503-заглушки (Task 0); с `role.analytics` (Task 4, класс `AnGw`) — полный путь справки без LLM через настоящий API, 5 фокусов плана на уровне HTTP |
| `core/tests/fixtures/kdm1_corpus.py` | синтетический корпус (`20FAKE_*`) той же схемы — подставлять в `knowledge.open_corpus`/тесты retrieval с Task 1 |
| `web/tests/analytics.test.ts` | строгий разбор `JobResponse` на стороне клиента (зеркало серверной модели) |
| `core/tests/test_analytics_service.py` (Task 4) | координатор `AnalyticsService` на фейках `preanalysis`/`validator`/`rendering`/`llm_adapter` (Task 3/5 ещё заглушки): 5 фокусов плана на уровне координатора — чужая идентичность, отзыв права/карточки между поиском и ответом, идемпотентность/отмена/таймаут без повторной оплаты, ложный смысл отклонён validator'ом, неисправный корпус/состояние не ломают остальные методы |

Новые тесты задач 1–9 — `core/tests/test_analytics_<module>.py` (по таблице `IMPLEMENTATION_PLAN.md`,
«Файлы и ответственность»), без изменения уже зафиксированных здесь сигнатур без обновления этого файла.
