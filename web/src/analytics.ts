// Аналитический чат технолога КДМ-1 (план КДМ1, Task 0 — только типы и строгий разбор; вкладка — Task 6).
// Договор ответа задания — contracts/job-response.schema.json (копия — core/pcbk_core/analytics/contracts/
// job-response.schema.json): до завершения проверки sections пуст, сырые ответы модели/системный промпт сюда
// не попадают (сервер строит ответ только через analytics/rendering.py). Разбор — строгий, как у lessons.ts:
// лишнее поле на любом уровне (включая вложенный locator) — отказ (parseJobResponse возвращает null), не тихий
// игнор — схема задана additionalProperties:false везде, и клиентский разбор не должен быть мягче серверного.
//
// Сети и React здесь нет (их даёт Task 6: analyticsApi.ts, AnalyticsPage.tsx) — только типы и чистый разбор.

export type JobState =
  | "queued" | "preanalysis" | "retrieving" | "generating" | "validating" | "completed"
  | "needs_clarification" | "limited_answer" | "cancelled" | "failed";
const JOB_STATES: readonly JobState[] =
  ["queued", "preanalysis", "retrieving", "generating", "validating", "completed", "needs_clarification",
   "limited_answer", "cancelled", "failed"];
// зеркало models.JOB_TERMINAL_STATES: после одного из них задание не меняется дальше (store.py, Task 2) —
// AnalyticsPage.tsx останавливает опрос, увидев один из них
export const JOB_TERMINAL_STATES: ReadonlySet<JobState> =
  new Set(["completed", "needs_clarification", "limited_answer", "cancelled", "failed"]);

// telemetry_observation — Д17 (план PLAN-D17-telemetry-lessons-2026-10-14, Task 1 «Produces»): секция данных
// историана по согласованной связи оборудования; как остальные серверные секции — evidence_refs=[], claim_id=
// null, rendered_by="server" (источник значения — служба данных через data_bridge.py, не корпус)
export type SectionKind =
  | "document_observation" | "public_definition" | "calculation" | "qualified_hypothesis" | "limitation"
  | "clarification" | "telemetry_observation";
const SECTION_KINDS: readonly SectionKind[] =
  ["document_observation", "public_definition", "calculation", "qualified_hypothesis", "limitation",
   "clarification", "telemetry_observation"];

export type ErrorCode =
  | "unavailable" | "invalid_input" | "policy_refused" | "source_conflict" | "validation_failed" | "interrupted"
  | "busy";
const ERROR_CODES: readonly ErrorCode[] =
  ["unavailable", "invalid_input", "policy_refused", "source_conflict", "validation_failed", "interrupted",
   "busy"];

export type Locator =
  | { type: "pdf_page"; page: number }
  | { type: "xlsx_cell"; sheet: string; cell: string }
  | { type: "image_label"; file: string; label: string; regionPixels: [number, number, number, number] | null }
  | { type: "public_section"; url: string; section: string };

export interface EvidenceRef {
  sourceId: string;
  fragmentId: string | null;
  locator: Locator;
  corpusVersion: string;
  limitation: string;
}

export interface Section {
  kind: SectionKind;
  text: string;
  evidenceRefs: EvidenceRef[];
  renderedBy: "server";
  claimId: string | null;
}

export interface JobResponse {
  requestId: string;
  conversationId: string;
  state: JobState;
  corpusVersion: string;
  stageLabel: string;
  sections: Section[];
  clarificationKeys: string[];
  errorCode: ErrorCode | null;
}

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string" && v !== "";
const isNullableText = (v: unknown): v is string | null => v === null || (typeof v === "string" && v !== "");
// строка ограниченной длины или null (правка итогового ревью, п.1 F2-brief) — приём isNullableText, с верхней
// границей: сервер отдаёт эти поля из записи источника корпуса (F1), не из схемы заказчика, границы нет в
// контракте — берём разумный запас (как MAX_TITLE texts.ts для названия, короче — для типа/даты)
const isNullableBoundedText = (v: unknown, max: number): v is string | null =>
  v === null || (typeof v === "string" && v !== "" && v.length <= max);
const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

// ровно эти ключи, не больше и не меньше — как additionalProperties:false схемы (приём lessons.ts)
function hasExactKeys(v: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(v);
  return actual.length === keys.length && keys.every((k) => Object.hasOwn(v, k));
}

function isStringArray(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((item): item is string => isText(item));
}

function parseLocator(v: unknown): Locator | null {
  if (!isObject(v) || typeof v.type !== "string") return null;
  switch (v.type) {
    case "pdf_page":
      if (!hasExactKeys(v, ["type", "page"]) || !isInt(v.page) || v.page < 1 || v.page > 10000) return null;
      return { type: "pdf_page", page: v.page };
    case "xlsx_cell":
      if (!hasExactKeys(v, ["type", "sheet", "cell"]) || !isText(v.sheet) || !isText(v.cell)
          || !/^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(v.cell)) return null;
      return { type: "xlsx_cell", sheet: v.sheet, cell: v.cell };
    case "image_label": {
      if (!hasExactKeys(v, ["type", "file", "label", "region_pixels"]) || !isText(v.file) || !isText(v.label)) {
        return null;
      }
      const region = v.region_pixels;
      let regionPixels: [number, number, number, number] | null;
      if (region === null) {
        regionPixels = null;
      } else if (Array.isArray(region) && region.length === 4 && region.every((n) => isInt(n) && n >= 0)) {
        regionPixels = [region[0], region[1], region[2], region[3]];
      } else {
        return null;
      }
      return { type: "image_label", file: v.file, label: v.label, regionPixels };
    }
    case "public_section":
      if (!hasExactKeys(v, ["type", "url", "section"]) || !isText(v.url) || !v.url.startsWith("https://")
          || !isText(v.section)) {
        return null;
      }
      return { type: "public_section", url: v.url, section: v.section };
    default:
      return null;
  }
}

function parseEvidenceRef(v: unknown): EvidenceRef | null {
  if (!isObject(v) || !hasExactKeys(v, ["source_id", "fragment_id", "locator", "corpus_version", "limitation"])) {
    return null;
  }
  const { source_id: sourceId, fragment_id: fragmentId, corpus_version: corpusVersion, limitation } = v;
  if (!isText(sourceId) || !isNullableText(fragmentId) || !isText(corpusVersion) || typeof limitation !== "string") {
    return null;
  }
  const locator = parseLocator(v.locator);
  if (locator === null) return null;
  return { sourceId, fragmentId, locator, corpusVersion, limitation };
}

function parseSection(v: unknown): Section | null {
  if (!isObject(v) || !hasExactKeys(v, ["kind", "text", "evidence_refs", "rendered_by", "claim_id"])) return null;
  const { kind, text, evidence_refs: evidenceRefsRaw, rendered_by: renderedBy, claim_id: claimId } = v;
  if (typeof kind !== "string" || !SECTION_KINDS.includes(kind as SectionKind)) return null;
  if (!isText(text) || renderedBy !== "server" || !isNullableText(claimId) || !Array.isArray(evidenceRefsRaw)) {
    return null;
  }
  const evidenceRefs: EvidenceRef[] = [];
  for (const raw of evidenceRefsRaw) {
    const ref = parseEvidenceRef(raw);
    if (ref === null) return null;
    evidenceRefs.push(ref);
  }
  return { kind: kind as SectionKind, text, evidenceRefs, renderedBy: "server", claimId };
}

// ответ GET requests/{id} (и терминальная стадия POST .../questions, .../cancel) → JobResponse; не по договору
// (HTML прокси, чужая форма, лишнее поле на любом уровне) — null, не частичный разбор
export function parseJobResponse(body: unknown): JobResponse | null {
  if (!isObject(body) || !hasExactKeys(body, ["request_id", "conversation_id", "state", "corpus_version",
                                              "stage_label", "sections", "clarification_keys", "error_code"])) {
    return null;
  }
  const { request_id: requestId, conversation_id: conversationId, state, corpus_version: corpusVersion,
         stage_label: stageLabel, sections: sectionsRaw, clarification_keys: clarificationKeys,
         error_code: errorCode } = body;
  if (!isText(requestId) || !isText(conversationId) || !isText(corpusVersion) || !isText(stageLabel)) return null;
  if (typeof state !== "string" || !JOB_STATES.includes(state as JobState)) return null;
  if (errorCode !== null && (typeof errorCode !== "string" || !ERROR_CODES.includes(errorCode as ErrorCode))) {
    return null;
  }
  if (!Array.isArray(sectionsRaw) || !isStringArray(clarificationKeys)) return null;
  const sections: Section[] = [];
  for (const raw of sectionsRaw) {
    const section = parseSection(raw);
    if (section === null) return null;
    sections.push(section);
  }
  return {
    requestId, conversationId, state: state as JobState, corpusVersion, stageLabel, sections,
    clarificationKeys: [...clarificationKeys], errorCode: errorCode as ErrorCode | null,
  };
}

// --- Task 6: остальные ответы `gateway/analytics_api.py` (пути — docs/kdm1/INTERFACES.md, таблица «Task 0»).
// Контракта схемой здесь нет (CONTRACT_SCHEMAS модели покрывает только AnalysisRequest/Preanalysis/LlmSelection/
// JobResponse) — формы ниже собраны по полям серверных dataclass (store.ConversationMeta/RequestRecord,
// service.Capabilities, reviews.ReviewRequest) тем же приёмом записи (snake_case в проводе, строгий отказ на
// лишнем/пропущенном поле). Если Task 4/7 отдадут другую форму — это точка сверки, не этого файла в одиночку
// (см. отчёт Task 6: «форма ответа capabilities/conversations/evidence/reviews — предположение»).

export const CLIENT_REQUEST_ID_MAX = 80;     // analysis-request.schema.json: client_request_id
export const QUESTION_MAX = 6000;            // analysis-request.schema.json: question
export const PROCESS_ID_MAX = 80;            // analysis-request.schema.json: context.process_id
export const UNIT_HINT_MAX = 40;             // analysis-request.schema.json: context.unit_hint

export type AnalysisMode = "reference" | "deviation_review";
const ANALYSIS_MODES: readonly AnalysisMode[] = ["reference", "deviation_review"];

export type ClarificationKey =
  | "quantity" | "process" | "unit" | "basis" | "period" | "timezone" | "grade" | "measurement_quality"
  | "source_applicability";
const CLARIFICATION_KEYS: readonly ClarificationKey[] =
  ["quantity", "process", "unit", "basis", "period", "timezone", "grade", "measurement_quality",
   "source_applicability"];

export const isClarificationKey = (v: string): v is ClarificationKey =>
  (CLARIFICATION_KEYS as readonly string[]).includes(v);

// --- GET capabilities: что доступно этому пользователю (мастер-промпт §6 — у преподавателя справка без LLM) ---

export interface Capabilities {
  referenceAvailable: boolean;
  llmAvailable: boolean;
  dataBridgeAvailable: boolean;
  reviewAuthority: boolean;
  corpusVersion: string;
}

export function parseCapabilities(body: unknown): Capabilities | null {
  if (!isObject(body) || !hasExactKeys(body, ["reference_available", "llm_available", "data_bridge_available",
                                              "review_authority", "corpus_version"])) {
    return null;
  }
  const { reference_available: referenceAvailable, llm_available: llmAvailable,
         data_bridge_available: dataBridgeAvailable, review_authority: reviewAuthority,
         corpus_version: corpusVersion } = body;
  if (typeof referenceAvailable !== "boolean" || typeof llmAvailable !== "boolean"
      || typeof dataBridgeAvailable !== "boolean" || typeof reviewAuthority !== "boolean"
      || !isText(corpusVersion)) {
    return null;
  }
  return { referenceAvailable, llmAvailable, dataBridgeAvailable, reviewAuthority, corpusVersion };
}

// --- диалоги: GET/POST conversations, GET conversations/{id} ---

export interface ConversationMeta {
  id: string;
  mode: AnalysisMode;
  createdAt: string;
  lastActivityAt: string;
  corpusVersion: string;
}

function parseConversationMeta(v: unknown): ConversationMeta | null {
  if (!isObject(v) || !hasExactKeys(v, ["id", "mode", "created_at", "last_activity_at", "corpus_version"])) {
    return null;
  }
  const { id, mode, created_at: createdAt, last_activity_at: lastActivityAt, corpus_version: corpusVersion } = v;
  if (!isText(id) || typeof mode !== "string" || !ANALYSIS_MODES.includes(mode as AnalysisMode)
      || !isText(createdAt) || !isText(lastActivityAt) || !isText(corpusVersion)) {
    return null;
  }
  return { id, mode: mode as AnalysisMode, createdAt, lastActivityAt, corpusVersion };
}

// ответ GET conversations: {"conversations": [...]} — пустой список, не отказ (приём parseLessonIndex)
export function parseConversationList(body: unknown): ConversationMeta[] | null {
  if (!isObject(body) || !hasExactKeys(body, ["conversations"]) || !Array.isArray(body.conversations)) return null;
  const items: ConversationMeta[] = [];
  for (const raw of body.conversations) {
    const meta = parseConversationMeta(raw);
    if (meta === null) return null;
    items.push(meta);
  }
  return items;
}

// ответ POST conversations: {"id": "..."} — минимум, нужный для первого вопроса (своя мета — своим GET)
export function parseCreatedConversation(body: unknown): string | null {
  if (!isObject(body) || !hasExactKeys(body, ["id"]) || !isText(body.id)) return null;
  return body.id;
}

// элемент истории GET conversations/{id} (Task 4, коммит ed32131 — докстринг INTERFACES.md, «Правка раунд 1»,
// п.5: КАЖДЫЙ элемент requests — объект {question, response}, не голый JobResponse): question — текст вопроса
// владельца диалога (store.RequestRecord.question), длина — как у AnalysisRequest.question схемы (1..QUESTION_MAX)
export interface ConversationTurn {
  question: string;
  response: JobResponse;
}

export interface ConversationDetail {
  conversation: ConversationMeta;
  requests: ConversationTurn[];
}

function parseConversationTurn(v: unknown): ConversationTurn | null {
  if (!isObject(v) || !hasExactKeys(v, ["question", "response"])) return null;
  const { question } = v;
  if (!isText(question) || question.length > QUESTION_MAX) return null;
  const response = parseJobResponse(v.response);
  if (response === null) return null;
  return { question, response };
}

// ответ GET conversations/{id}: {"conversation": {...}, "requests": [{"question", "response"}, ...]} — история
export function parseConversationDetail(body: unknown): ConversationDetail | null {
  if (!isObject(body) || !hasExactKeys(body, ["conversation", "requests"]) || !Array.isArray(body.requests)) {
    return null;
  }
  const conversation = parseConversationMeta(body.conversation);
  if (conversation === null) return null;
  const requests: ConversationTurn[] = [];
  for (const raw of body.requests) {
    const turn = parseConversationTurn(raw);
    if (turn === null) return null;
    requests.push(turn);
  }
  return { conversation, requests };
}

// ответ POST conversations/{id}/questions: 202 {"request_id": "..."} — опрос дальше через GET requests/{id}
export function parseQuestionAccepted(body: unknown): string | null {
  if (!isObject(body) || !hasExactKeys(body, ["request_id"]) || !isText(body.request_id)) return null;
  return body.request_id;
}

// --- GET evidence/{id}: разрешённый фрагмент известного пакета (карточка «Основания») ---

// пределы source_title/source_type/source_date (правка итогового ревью, п.1 F2-brief: «строка ограниченной
// длины или null») — title как MAX_TITLE texts.ts (имя разговора), type/date короче (значения корпуса —
// короткие коды и ГГГГ-ММ-ДД, запас на иной формат даты)
export const SOURCE_TITLE_MAX = 200;
export const SOURCE_TYPE_MAX = 80;
export const SOURCE_DATE_MAX = 40;

export interface EvidenceItem {
  id: string;
  sourceId: string;
  text: string;
  locator: Locator;
  corpusVersion: string;
  limitation: string;
  // название/тип/дата источника (F1, правка итогового ревью п.5 F1-brief: store.get_evidence_item дополнен
  // этими тремя полями из записи источника корпуса — title/type/reviewed_on; нет записи — null) — показываются
  // при раскрытии основания (AnalyticsSources.tsx)
  sourceTitle: string | null;
  sourceType: string | null;
  sourceDate: string | null;
}

export function parseEvidenceItem(body: unknown): EvidenceItem | null {
  if (!isObject(body)
      || !hasExactKeys(body, ["id", "source_id", "text", "locator", "corpus_version", "limitation",
                              "source_title", "source_type", "source_date"])) {
    return null;
  }
  const { id, source_id: sourceId, text, corpus_version: corpusVersion, limitation,
         source_title: sourceTitle, source_type: sourceType, source_date: sourceDate } = body;
  if (!isText(id) || !isText(sourceId) || typeof text !== "string" || !isText(corpusVersion)
      || typeof limitation !== "string") {
    return null;
  }
  if (!isNullableBoundedText(sourceTitle, SOURCE_TITLE_MAX) || !isNullableBoundedText(sourceType, SOURCE_TYPE_MAX)
      || !isNullableBoundedText(sourceDate, SOURCE_DATE_MAX)) {
    return null;
  }
  const locator = parseLocator(body.locator);
  if (locator === null) return null;
  return { id, sourceId, text, locator, corpusVersion, limitation, sourceTitle, sourceType, sourceDate };
}

// --- согласование (мастер-промпт §6: заявки/решения назначенного полномочия) ---

export type ReviewKind = "local_claim" | "equipment_link" | "local_norm" | "conflict";
const REVIEW_KINDS: readonly ReviewKind[] = ["local_claim", "equipment_link", "local_norm", "conflict"];

export type ReviewStatus = "pending" | "accepted" | "rejected" | "info_requested";
const REVIEW_STATUSES: readonly ReviewStatus[] = ["pending", "accepted", "rejected", "info_requested"];

export type ReviewDecisionKind = "accepted" | "rejected" | "info_requested";
export const REVIEW_DECISION_KINDS: readonly ReviewDecisionKind[] = ["accepted", "rejected", "info_requested"];

export interface ReviewRequestItem {
  id: string;
  kind: ReviewKind;
  subjectId: string;
  oldValue: string | null;
  newValue: string;
  scope: string;
  periodStart: string | null;
  periodEnd: string | null;
  rationale: string;
  status: ReviewStatus;
  createdBy: string;
  createdAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  comment: string | null;
}

function parseReviewRequestItem(v: unknown): ReviewRequestItem | null {
  if (!isObject(v) || !hasExactKeys(v, ["id", "kind", "subject_id", "old_value", "new_value", "scope",
                                        "period_start", "period_end", "rationale", "status", "created_by",
                                        "created_at", "decided_by", "decided_at", "comment"])) {
    return null;
  }
  const { id, kind, subject_id: subjectId, old_value: oldValue, new_value: newValue, scope,
         period_start: periodStart, period_end: periodEnd, rationale, status, created_by: createdBy,
         created_at: createdAt, decided_by: decidedBy, decided_at: decidedAt, comment } = v;
  if (!isText(id) || typeof kind !== "string" || !REVIEW_KINDS.includes(kind as ReviewKind) || !isText(subjectId)) {
    return null;
  }
  if (!isNullableText(oldValue) || !isText(newValue) || !isText(scope)) return null;
  if (!isNullableText(periodStart) || !isNullableText(periodEnd) || typeof rationale !== "string") return null;
  if (typeof status !== "string" || !REVIEW_STATUSES.includes(status as ReviewStatus)) return null;
  if (!isText(createdBy) || !isText(createdAt) || !isNullableText(decidedBy) || !isNullableText(decidedAt)) {
    return null;
  }
  if (!isNullableText(comment)) return null;
  return {
    id, kind: kind as ReviewKind, subjectId, oldValue, newValue, scope, periodStart, periodEnd, rationale,
    status: status as ReviewStatus, createdBy, createdAt, decidedBy, decidedAt, comment,
  };
}

// ответ GET reviews/requests: {"requests": [...]}
export function parseReviewList(body: unknown): ReviewRequestItem[] | null {
  if (!isObject(body) || !hasExactKeys(body, ["requests"]) || !Array.isArray(body.requests)) return null;
  const items: ReviewRequestItem[] = [];
  for (const raw of body.requests) {
    const item = parseReviewRequestItem(raw);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

// ответ POST reviews/requests и POST reviews/requests/{id}/decisions — одна заявка целиком
export function parseReviewRequestItemResponse(body: unknown): ReviewRequestItem | null {
  return parseReviewRequestItem(body);
}

// --- связи оборудования с историаном (план Д17, Task 2; мастер-промпт §8): подпись оборудования из документов
// КДМ-1 ↔ тег историана. Связь предлагает любой вошедший (раздел «Связи с историаном», AnalyticsLinks.tsx),
// согласует — тот, у кого право согласования, обычной заявкой kind="equipment_link" (выше). Имя тега для чтения
// значения — только ResolvedLink.historian_tag принятой заявки; сама подпись чтение не включает (Task 1 плана,
// «Produces»: GET analytics/links и GET analytics/links/{id}/candidates — форма оттуда дословно).

export type LinkState = "unbound" | "pending" | "linked";
const LINK_STATES: readonly LinkState[] = ["unbound", "pending", "linked"];

export interface EquipmentLink {
  id: string;
  displayLabel: string;
  documentContext: string;
  state: LinkState;
  historianTag: string | null;
  approvedBy: string | null;
  approvedAt: string | null;
  pendingRequestId: string | null;
}

function parseEquipmentLink(v: unknown): EquipmentLink | null {
  if (!isObject(v) || !hasExactKeys(v, ["id", "display_label", "document_context", "state", "historian_tag",
                                        "approved_by", "approved_at", "pending_request_id"])) {
    return null;
  }
  const { id, display_label: displayLabel, document_context: documentContext, state,
         historian_tag: historianTag, approved_by: approvedBy, approved_at: approvedAt,
         pending_request_id: pendingRequestId } = v;
  if (!isText(id) || !isText(displayLabel) || typeof documentContext !== "string") return null;
  if (typeof state !== "string" || !LINK_STATES.includes(state as LinkState)) return null;
  if (!isNullableText(historianTag) || !isNullableText(approvedBy) || !isNullableText(approvedAt)
      || !isNullableText(pendingRequestId)) {
    return null;
  }
  return {
    id, displayLabel, documentContext, state: state as LinkState, historianTag, approvedBy, approvedAt,
    pendingRequestId,
  };
}

// ответ GET analytics/links: {"links": [...]} — порядок как отдаёт сервер (по id корпуса); пустой список — не
// отказ (приём parseConversationList)
export function parseEquipmentLinkList(body: unknown): EquipmentLink[] | null {
  if (!isObject(body) || !hasExactKeys(body, ["links"]) || !Array.isArray(body.links)) return null;
  const items: EquipmentLink[] = [];
  for (const raw of body.links) {
    const item = parseEquipmentLink(raw);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

export interface LinkCandidate {
  tag: string;
  description: string;
  unit: string | null;
}

function parseLinkCandidate(v: unknown): LinkCandidate | null {
  if (!isObject(v) || !hasExactKeys(v, ["tag", "description", "unit"])) return null;
  const { tag, description, unit } = v;
  if (!isText(tag) || typeof description !== "string" || !isNullableText(unit)) return null;
  return { tag, description, unit };
}

// ответ GET analytics/links/{id}/candidates: status/message — из ответа DataService как есть (message есть
// только у отказа), candidates — один catalog_search по подписи (может быть пуст)
export interface LinkCandidates {
  status: string;
  message: string | null;
  candidates: LinkCandidate[];
}

export function parseLinkCandidates(body: unknown): LinkCandidates | null {
  if (!isObject(body) || !hasExactKeys(body, ["status", "message", "candidates"])
      || !Array.isArray(body.candidates)) {
    return null;
  }
  const { status, message } = body;
  if (!isText(status) || !isNullableText(message)) return null;
  const candidates: LinkCandidate[] = [];
  for (const raw of body.candidates) {
    const c = parseLinkCandidate(raw);
    if (c === null) return null;
    candidates.push(c);
  }
  return { status, message, candidates };
}
