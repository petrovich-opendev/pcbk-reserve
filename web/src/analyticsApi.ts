// Клиент вкладки «Аналитический чат технолога» (план КДМ1, Task 6; мастер-промпт §6: «Используй отдельный
// analyticsApi.ts, не ломая общий Api и старые тестовые заглушки»): отдельный интерфейс поверх callJson (тот же
// вид запроса — cookie сессии, Accept JSON, тело JSON, Reply), как teacherApi.ts у кабинета. В общем Api
// (api.ts) аналитических методов нет — заглушки Api старых тестов (lessons/events/calc) не меняются ни на один
// метод.
//
// Пути — ровно девять ручек `gateway/analytics_api.py` (docs/kdm1/INTERFACES.md, таблица «Task 0»); этот файл их
// не меняет и не переименовывает. Идентичность (place/caller/login/owner/role) сервер берёт из сессии — здесь её
// нет ни в одном теле запроса (мастер-промпт §6, §10).
import { callJson, type JsonFetch, type Reply } from "./api";
import { CLIENT_REQUEST_ID_MAX, type AnalysisMode, type ReviewDecisionKind, type ReviewKind } from "./analytics";

const PREFIX = "/pcbk/api/analytics";

// контекст вопроса — context.* схемы analysis-request.schema.json (мастер-промпт: «участок/период» необязательны)
export interface QuestionPeriod { start: string; end: string }     // ISO 8601 с явным смещением (схема: format date-time)
export interface QuestionContext {
  processId?: string | null;
  period?: QuestionPeriod | null;
  unitHint?: string | null;
}

export interface AskInput {
  clientRequestId: string;
  question: string;
  mode: AnalysisMode;
  context?: QuestionContext | null;
}

export interface CreateReviewInput {
  kind: ReviewKind;
  subjectId: string;
  newValue: string;
  scope: string;
  rationale: string;
  oldValue?: string | null;
  periodStart?: string | null;
  periodEnd?: string | null;
}

export interface AnalyticsApi {
  capabilities(): Promise<Reply>;
  conversations(): Promise<Reply>;
  createConversation(mode: AnalysisMode): Promise<Reply>;
  conversation(id: string): Promise<Reply>;
  ask(conversationId: string, input: AskInput): Promise<Reply>;
  request(requestId: string): Promise<Reply>;
  cancel(requestId: string): Promise<Reply>;
  evidence(evidenceId: string): Promise<Reply>;
  reviewRequests(): Promise<Reply>;
  createReview(input: CreateReviewInput): Promise<Reply>;
  decideReview(reviewId: string, decision: ReviewDecisionKind, comment: string): Promise<Reply>;
}

// идентификатор места повтора без двойной оплаты (ОСОБЫЙ ФОКУС ПРОВЕРКИ №3 плана): «Повторить» в AnalyticsPage
// шлёт тот же id, что первая попытка — создаёт его только новый вопрос (newClientRequestId при наборе текста)
export function newClientRequestId(): string {
  const id = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `cr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return id.slice(0, CLIENT_REQUEST_ID_MAX);
}

function questionBody(input: AskInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    client_request_id: input.clientRequestId, question: input.question, mode: input.mode,
  };
  const ctx = input.context;
  if (ctx !== undefined && ctx !== null) {
    const context: Record<string, unknown> = {};
    if (ctx.processId !== undefined) context.process_id = ctx.processId;
    if (ctx.period !== undefined) {
      context.period = ctx.period === null ? null : { start: ctx.period.start, end: ctx.period.end };
    }
    if (ctx.unitHint !== undefined) context.unit_hint = ctx.unitHint;
    if (Object.keys(context).length > 0) body.context = context;
  }
  return body;
}

export function createAnalyticsApi(fetchImpl?: JsonFetch): AnalyticsApi {
  const get = (path: string): Promise<Reply> => callJson("GET", PREFIX + path, undefined, fetchImpl);
  const post = (path: string, body?: unknown): Promise<Reply> => callJson("POST", PREFIX + path, body, fetchImpl);
  return {
    capabilities: () => get("/capabilities"),
    conversations: () => get("/conversations"),
    createConversation: (mode) => post("/conversations", { mode }),
    conversation: (id) => get(`/conversations/${id}`),
    ask: (conversationId, input) => post(`/conversations/${conversationId}/questions`, questionBody(input)),
    request: (requestId) => get(`/requests/${requestId}`),
    cancel: (requestId) => post(`/requests/${requestId}/cancel`),
    evidence: (evidenceId) => get(`/evidence/${evidenceId}`),
    reviewRequests: () => get("/reviews/requests"),
    createReview: (input) => post("/reviews/requests", {
      kind: input.kind, subject_id: input.subjectId, new_value: input.newValue, scope: input.scope,
      rationale: input.rationale, old_value: input.oldValue ?? null, period_start: input.periodStart ?? null,
      period_end: input.periodEnd ?? null,
    }),
    decideReview: (reviewId, decision, comment) =>
      post(`/reviews/requests/${reviewId}/decisions`, { decision, comment }),
  };
}
