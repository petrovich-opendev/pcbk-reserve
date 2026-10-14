// @vitest-environment node
// Аналитика КДМ-1 (план КДМ1, Task 0): строгий разбор JobResponse — зеркало
// core/pcbk_core/analytics/contracts/job-response.schema.json / core/pcbk_core/analytics/models.py.JobResponse.
// Приём теста — как tests/lessons.test.ts: валидные формы принимаются, лишнее поле на любом уровне (включая
// вложенный locator) и значение вне enum — отказ (null), не частичный разбор.
import { expect, test } from "vitest";
import {
  parseCapabilities, parseConversationDetail, parseConversationList, parseCreatedConversation,
  parseEvidenceItem, parseJobResponse, parseQuestionAccepted, parseReviewList, type JobResponse,
} from "../src/analytics";

const section = (over: Partial<Record<string, unknown>> = {}) => ({
  kind: "document_observation", text: "В предоставленной модели указано: …", evidence_refs: [],
  rendered_by: "server", claim_id: null, ...over,
});

const evidenceRef = (over: Partial<Record<string, unknown>> = {}) => ({
  source_id: "S1", fragment_id: "F1", corpus_version: "v1", limitation: "", ...over,
  locator: "locator" in over ? over.locator : { type: "pdf_page", page: 3 },
});

const jobBody = (over: Partial<Record<string, unknown>> = {}) => ({
  request_id: "r1", conversation_id: "c1", state: "completed", corpus_version: "v1", stage_label: "Готово",
  sections: [], clarification_keys: [], error_code: null, ...over,
});

test("parse_job_response_minimal_contract", () => {
  expect(parseJobResponse(jobBody())).toEqual({
    requestId: "r1", conversationId: "c1", state: "completed", corpusVersion: "v1", stageLabel: "Готово",
    sections: [], clarificationKeys: [], errorCode: null,
  } satisfies JobResponse);
});

test("parse_job_response_top_level_checks", () => {
  for (const bad of [
    null, undefined, 7, "<html>502</html>", [], {},
    jobBody({ state: "running" }),                                // вне enum JobState
    jobBody({ error_code: "oops" }),                               // вне enum ErrorCode
    { ...jobBody(), extra: 1 },                                    // лишнее поле верхнего уровня
    (() => { const b = jobBody() as Record<string, unknown>; delete b.error_code; return b; })(),  // пропущено
    jobBody({ request_id: "" }),
    jobBody({ clarification_keys: ["unit", 7] }),
  ]) expect(parseJobResponse(bad), JSON.stringify(bad)).toBeNull();
});

test("parse_job_response_section_and_evidence_ref", () => {
  const withEvidence = jobBody({
    sections: [section({
      kind: "document_observation", claim_id: "C1",
      evidence_refs: [evidenceRef({ locator: { type: "xlsx_cell", sheet: "КДМ-1", cell: "B12" } })],
    })],
  });
  expect(parseJobResponse(withEvidence)).toEqual({
    requestId: "r1", conversationId: "c1", state: "completed", corpusVersion: "v1", stageLabel: "Готово",
    sections: [{
      kind: "document_observation", text: "В предоставленной модели указано: …",
      evidenceRefs: [{
        sourceId: "S1", fragmentId: "F1", corpusVersion: "v1", limitation: "",
        locator: { type: "xlsx_cell", sheet: "КДМ-1", cell: "B12" },
      }],
      renderedBy: "server", claimId: "C1",
    }],
    clarificationKeys: [], errorCode: null,
  } satisfies JobResponse);
});

test("parse_job_response_section_field_checks", () => {
  for (const bad of [
    jobBody({ sections: [{ ...section(), extra: 1 }] }),             // лишнее поле секции
    jobBody({ sections: [{ ...section(), kind: "unknown_kind" }] }),  // вне enum SectionKind
    jobBody({ sections: [{ ...section(), rendered_by: "client" }] }), // const "server"
    jobBody({ sections: ["строка"] }),
  ]) expect(parseJobResponse(bad), JSON.stringify(bad)).toBeNull();
});

test("parse_job_response_locator_variants_and_rejections", () => {
  const locators = [
    { type: "pdf_page", page: 1 },
    { type: "xlsx_cell", sheet: "S", cell: "A1" },
    { type: "image_label", file: "f.png", label: "L", region_pixels: [0, 0, 10, 10] },
    { type: "image_label", file: "f.png", label: "L", region_pixels: null },
    { type: "public_section", url: "https://example.invalid/x", section: "§1" },
  ];
  for (const locator of locators) {
    const body = jobBody({ sections: [section({ evidence_refs: [evidenceRef({ locator })] })] });
    expect(parseJobResponse(body), JSON.stringify(locator)).not.toBeNull();
  }

  const badLocators = [
    { type: "pdf_page", page: 0 },                                        // ge 1
    { type: "pdf_page", page: 1, extra: 1 },                              // лишнее поле варианта
    { type: "xlsx_cell", sheet: "S", cell: "a1" },                        // шаблон ячейки (нижний регистр)
    { type: "image_label", file: "f.png", label: "L" },                   // region_pixels обязателен (даже null)
    { type: "public_section", url: "http://example.invalid/x", section: "s" },  // не https
    { type: "unknown_type" },
    "строка",
    null,
  ];
  for (const locator of badLocators) {
    const body = jobBody({ sections: [section({ evidence_refs: [evidenceRef({ locator })] })] });
    expect(parseJobResponse(body), JSON.stringify(locator)).toBeNull();
  }
});

test("parse_job_response_evidence_ref_field_checks", () => {
  for (const bad of [
    jobBody({ sections: [section({ evidence_refs: [{ ...evidenceRef(), extra: 1 }] })] }),
    jobBody({ sections: [section({ evidence_refs: [{ ...evidenceRef(), fragment_id: 7 }] })] }),
    jobBody({ sections: [section({ evidence_refs: [{ ...evidenceRef(), fragment_id: "" }] })] }),
  ]) expect(parseJobResponse(bad), JSON.stringify(bad)).toBeNull();

  const nullFragment = jobBody({ sections: [section({ evidence_refs: [evidenceRef({ fragment_id: null })] })] });
  expect(parseJobResponse(nullFragment)).not.toBeNull();
});

// --- Task 6: разбор остальных ответов gateway/analytics_api.py (форма — предположение web/src/analytics.ts,
// не контракт схемой; см. docs/kdm1/INTERFACES.md и отчёт Task 6) ---

test("parse_capabilities_ok_and_rejections", () => {
  const ok = { reference_available: true, llm_available: false, data_bridge_available: false,
              review_authority: true, corpus_version: "20FAKE-v1" };
  expect(parseCapabilities(ok)).toEqual({
    referenceAvailable: true, llmAvailable: false, dataBridgeAvailable: false, reviewAuthority: true,
    corpusVersion: "20FAKE-v1",
  });
  for (const bad of [null, {}, { ...ok, extra: 1 }, { ...ok, llm_available: "yes" },
                     (() => { const b = { ...ok } as Record<string, unknown>; delete b.corpus_version; return b; })()]) {
    expect(parseCapabilities(bad), JSON.stringify(bad)).toBeNull();
  }
});

const CONV = { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
               last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" };

test("parse_conversation_list_ok_empty_and_rejections", () => {
  expect(parseConversationList({ conversations: [] })).toEqual([]);
  expect(parseConversationList({ conversations: [CONV] })).toEqual([{
    id: "c1", mode: "reference", createdAt: CONV.created_at, lastActivityAt: CONV.last_activity_at,
    corpusVersion: "20FAKE-v1",
  }]);
  for (const bad of [null, [], { conversations: [{ ...CONV, mode: "unknown_mode" }] },
                     { conversations: [{ ...CONV, extra: 1 }] }, { conversations: "nope" }]) {
    expect(parseConversationList(bad), JSON.stringify(bad)).toBeNull();
  }
});

test("parse_created_conversation", () => {
  expect(parseCreatedConversation({ id: "c1" })).toBe("c1");
  expect(parseCreatedConversation({ id: "c1", extra: 1 })).toBeNull();
  expect(parseCreatedConversation({})).toBeNull();
});

test("parse_question_accepted", () => {
  expect(parseQuestionAccepted({ request_id: "r1" })).toBe("r1");
  expect(parseQuestionAccepted({ request_id: "" })).toBeNull();
  expect(parseQuestionAccepted(null)).toBeNull();
});

test("parse_conversation_detail_ok_and_rejections", () => {
  const body = { conversation: CONV, requests: [] };
  expect(parseConversationDetail(body)).toEqual({
    conversation: { id: "c1", mode: "reference", createdAt: CONV.created_at, lastActivityAt: CONV.last_activity_at,
                   corpusVersion: "20FAKE-v1" },
    requests: [],
  });
  expect(parseConversationDetail({ conversation: { ...CONV, extra: 1 }, requests: [] })).toBeNull();
  expect(parseConversationDetail({ conversation: CONV, requests: [{ not: "a job response" }] })).toBeNull();
  expect(parseConversationDetail({ conversation: CONV })).toBeNull();
});

// Task 4, коммит ed32131 (docs/kdm1/INTERFACES.md, «Правка раунд 1», п.5): каждый элемент requests —
// {question, response}, не голый JobResponse (форма до этой правки) — тот же приём строгого разбора: лишнее
// поле, пропущенный question, question вне формы (пустая строка, длиннее QUESTION_MAX) или битый response — null
test("parse_conversation_detail_turn_question_plus_response", () => {
  const turn = { question: "Какой расход массы на выходе?", response: jobBody() };
  expect(parseConversationDetail({ conversation: CONV, requests: [turn] })).toEqual({
    conversation: { id: "c1", mode: "reference", createdAt: CONV.created_at, lastActivityAt: CONV.last_activity_at,
                   corpusVersion: "20FAKE-v1" },
    requests: [{
      question: "Какой расход массы на выходе?",
      response: { requestId: "r1", conversationId: "c1", state: "completed", corpusVersion: "v1",
                 stageLabel: "Готово", sections: [], clarificationKeys: [], errorCode: null },
    }],
  });
});

test("parse_conversation_detail_turn_rejections", () => {
  const okTurn = { question: "Вопрос", response: jobBody() };
  for (const bad of [
    { ...okTurn, extra: 1 },                                   // лишнее поле элемента истории
    (() => { const t = { ...okTurn } as Record<string, unknown>; delete t.question; return t; })(),  // без вопроса
    { ...okTurn, question: "" },                                // пустой вопрос — не форма AnalysisRequest.question
    { ...okTurn, question: 7 },                                 // не строка
    { ...okTurn, question: "x".repeat(6001) },                  // длиннее QUESTION_MAX (analysis-request.schema.json)
    { ...okTurn, response: { not: "a job response" } },
    { question: "Вопрос" },                                     // без response
    "строка",
  ]) expect(parseConversationDetail({ conversation: CONV, requests: [bad] }), JSON.stringify(bad)).toBeNull();

  // ровно QUESTION_MAX — на границе принимается
  const atLimit = { question: "x".repeat(6000), response: jobBody() };
  expect(parseConversationDetail({ conversation: CONV, requests: [atLimit] })).not.toBeNull();
});

// source_title/source_type/source_date — F1 (store.get_evidence_item), правка итогового ревью п.1 F2-brief:
// строка ограниченной длины или null; лишнее поле/неверный тип — отказ (как остальные поля этого ответа)
test("parse_evidence_item_ok_and_rejections", () => {
  const ok = { id: "e1", source_id: "S1", text: "фрагмент источника", corpus_version: "20FAKE-v1", limitation: "",
              locator: { type: "pdf_page", page: 2 }, source_title: "ПЦБК: КДМ-1", source_type: "public_primary",
              source_date: "2026-10-13" };
  expect(parseEvidenceItem(ok)).toEqual({
    id: "e1", sourceId: "S1", text: "фрагмент источника", corpusVersion: "20FAKE-v1", limitation: "",
    locator: { type: "pdf_page", page: 2 }, sourceTitle: "ПЦБК: КДМ-1", sourceType: "public_primary",
    sourceDate: "2026-10-13",
  });
  // null у всех трёх — нет записи источника (F1-brief, п.5): принимается
  expect(parseEvidenceItem({ ...ok, source_title: null, source_type: null, source_date: null })).toEqual({
    id: "e1", sourceId: "S1", text: "фрагмент источника", corpusVersion: "20FAKE-v1", limitation: "",
    locator: { type: "pdf_page", page: 2 }, sourceTitle: null, sourceType: null, sourceDate: null,
  });
  for (const bad of [
    null, { ...ok, extra: 1 }, { ...ok, locator: { type: "unknown" } },
    (() => { const b = { ...ok } as Record<string, unknown>; delete b.text; return b; })(),
    (() => { const b = { ...ok } as Record<string, unknown>; delete b.source_title; return b; })(),  // пропущено
    { ...ok, source_title: 7 },                            // неверный тип
    { ...ok, source_type: "" },                            // пустая строка — не "текст или null"
    { ...ok, source_date: "x".repeat(41) },                // длиннее SOURCE_DATE_MAX
  ]) {
    expect(parseEvidenceItem(bad), JSON.stringify(bad)).toBeNull();
  }
});

test("parse_review_list_ok_empty_and_rejections", () => {
  const item = { id: "rv1", kind: "local_claim", subject_id: "S1", old_value: null, new_value: "новое значение",
                 scope: "КДМ-1", period_start: null, period_end: null, rationale: "основание",
                 status: "pending", created_by: "student-03", created_at: "2026-10-14T08:00:00+00:00",
                 decided_by: null, decided_at: null, comment: null };
  expect(parseReviewList({ requests: [] })).toEqual([]);
  expect(parseReviewList({ requests: [item] })).toEqual([{
    id: "rv1", kind: "local_claim", subjectId: "S1", oldValue: null, newValue: "новое значение", scope: "КДМ-1",
    periodStart: null, periodEnd: null, rationale: "основание", status: "pending", createdBy: "student-03",
    createdAt: item.created_at, decidedBy: null, decidedAt: null, comment: null,
  }]);
  for (const bad of [null, { requests: [{ ...item, kind: "unknown_kind" }] },
                     { requests: [{ ...item, status: "unknown_status" }] }, { requests: [{ ...item, extra: 1 }] }]) {
    expect(parseReviewList(bad), JSON.stringify(bad)).toBeNull();
  }
});
