// Вкладка «Аналитический чат технолога» (план КДМ1, Task 6; мастер-промпт §6): диалог, история, уточнения,
// отправка/опрос/отмена задания, повтор после сетевого сбоя тем же client_request_id (ОСОБЫЙ ФОКУС ПРОВЕРКИ №3
// плана), чужой/не найденный диалог, 401 → вход, сервер выключен (404/503) → понятный текст, экран
// согласования — только при праве, которое сообщил сервер (capabilities.review_authority). Теги — только
// синтетические 20FAKE_*; настоящего корпуса в тестах нет (fixtures ниже — выдуманные источники S1/S2).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { AnalyticsApi } from "../src/analyticsApi";
import { AnalyticsPage } from "../src/components/AnalyticsPage";
import type { VisibilityDoc } from "../src/status";
import {
  T_AN_CANCEL, T_AN_CLARIFY_TITLE, T_AN_LINKS, T_AN_LINKS_EMPTY, T_AN_LINKS_NO_BRIDGE, T_AN_NO_CONVERSATIONS,
  T_AN_ORIGINAL_QUESTION, T_AN_REVIEWS, T_AN_SEND, T_AN_SEND_TO_REVIEW, T_AN_SEND_TO_REVIEW_DONE,
  T_AN_SEND_TO_REVIEW_RATIONALE, T_AN_SHOW_FRAGMENT, T_AN_UNAVAILABLE, T_DOWN,
} from "../src/texts";

// doc управляемый из теста (приём status.test.ts у startStatusPolling): hidden — геттер, listener захватываем
function fakeVisibilityDoc(): { doc: VisibilityDoc; setHidden(v: boolean): void } {
  let hidden = false;
  let listener: (() => void) | null = null;
  const doc: VisibilityDoc = {
    get hidden() { return hidden; },
    addEventListener: (_t, l) => { listener = l; },
    removeEventListener: () => { listener = null; },
  };
  return { doc, setHidden: (v) => { hidden = v; listener?.(); } };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type R = { status: number; body: unknown };
const reply = (status: number, body: unknown = null): R => ({ status, body });

const CAPS_PLAIN = {
  reference_available: true, llm_available: true, data_bridge_available: false, review_authority: false,
  corpus_version: "20FAKE-v1",
};
const CAPS_REVIEWER = { ...CAPS_PLAIN, review_authority: true };

function fakeApi(over: Partial<Record<keyof AnalyticsApi, (...a: never[]) => Promise<R> | R>> = {}): AnalyticsApi {
  const unset = () => Promise.resolve(reply(404, { name: "NotFound" }));
  return {
    capabilities: vi.fn(over.capabilities ?? (() => Promise.resolve(reply(200, CAPS_PLAIN)))),
    conversations: vi.fn(over.conversations ?? (() => Promise.resolve(reply(200, { conversations: [] })))),
    createConversation: vi.fn(over.createConversation ?? unset),
    conversation: vi.fn(over.conversation ?? unset),
    ask: vi.fn(over.ask ?? unset),
    request: vi.fn(over.request ?? unset),
    cancel: vi.fn(over.cancel ?? (() => Promise.resolve(reply(200, null)))),
    evidence: vi.fn(over.evidence ?? unset),
    links: vi.fn(over.links ?? (() => Promise.resolve(reply(200, { links: [] })))),
    linkCandidates: vi.fn(over.linkCandidates ?? unset),
    reviewRequests: vi.fn(over.reviewRequests ?? (() => Promise.resolve(reply(200, { requests: [] })))),
    createReview: vi.fn(over.createReview ?? unset),
    decideReview: vi.fn(over.decideReview ?? unset),
  } as unknown as AnalyticsApi;
}

const section = (over: Partial<Record<string, unknown>> = {}) => ({
  kind: "document_observation", text: "В предоставленной модели указано: расход 8 т/ч.", evidence_refs: [],
  rendered_by: "server", claim_id: null, ...over,
});

const jobBody = (over: Partial<Record<string, unknown>> = {}) => ({
  request_id: "r1", conversation_id: "c1", state: "completed", corpus_version: "20FAKE-v1", stage_label: "Готово",
  sections: [], clarification_keys: [], error_code: null, ...over,
});

test("analytics_page_empty_conversations_no_reviewer_nav", async () => {
  render(<AnalyticsPage api={fakeApi()} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_AN_NO_CONVERSATIONS)).toBeTruthy();
  expect(screen.queryByRole("button", { name: T_AN_REVIEWS })).toBeNull();
});

test("analytics_page_new_conversation_asks_and_shows_completed_answer_with_sources", async () => {
  const api = fakeApi({
    createConversation: () => Promise.resolve(reply(201, { id: "c1" })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      sections: [section({
        evidence_refs: [{ source_id: "S1", fragment_id: null, corpus_version: "20FAKE-v1",
                         limitation: "только для разбора", locator: { type: "pdf_page", page: 3 } }],
      })],
    }))),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Новый диалог: Справка по документам/ }));
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Какой расход массы?" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  expect(await screen.findByText(/расход 8 т\/ч/)).toBeTruthy();
  expect(screen.getByText(/S1.*страница 3/)).toBeTruthy();
  expect(screen.getByText(/только для разбора/)).toBeTruthy();
  expect(api.createConversation).toHaveBeenCalledWith("reference");
  const askCall = (api.ask as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(askCall[0]).toBe("c1");
  expect(askCall[1]).toMatchObject({ question: "Какой расход массы?", mode: "reference" });
  expect(typeof askCall[1].clientRequestId).toBe("string");
  expect(askCall[1].clientRequestId.length).toBeGreaterThan(0);
});

test("analytics_page_clarification_shows_keys_and_resend_uses_new_client_request_id", async () => {
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      state: "needs_clarification", stage_label: "Нужно уточнение", clarification_keys: ["unit", "period"],
    }))),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Сколько массы?" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  expect(await screen.findByText(T_AN_CLARIFY_TITLE)).toBeTruthy();
  expect(screen.getByText("единица измерения")).toBeTruthy();
  expect(screen.getByText("период")).toBeTruthy();
  expect(screen.getByText(new RegExp(`${T_AN_ORIGINAL_QUESTION}.*Сколько массы`))).toBeTruthy();

  const firstId = (api.ask as ReturnType<typeof vi.fn>).mock.calls[0][1].clientRequestId as string;
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Сколько массы по сухому?" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(2));
  const secondId = (api.ask as ReturnType<typeof vi.fn>).mock.calls[1][1].clientRequestId as string;
  expect(secondId).not.toBe(firstId);
});

test("analytics_page_cancel_stops_in_progress_request", async () => {
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({ state: "generating", stage_label: "Формирование ответа" }))),
    cancel: () => Promise.resolve(reply(200, jobBody({ state: "cancelled", stage_label: "Отменено" }))),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  fireEvent.click(await screen.findByRole("button", { name: T_AN_CANCEL }));
  await waitFor(() => expect(screen.queryByRole("button", { name: T_AN_CANCEL })).toBeNull());
  expect(api.cancel).toHaveBeenCalledWith("r1");
});

// --- правка итогового ревью, п.3 F2-brief: размонтирование с активным опросом отменяет задание ровно раз
// (мастер-промпт §6: «при выходе отменить задания») — и не продолжает опрашивать после ухода со страницы ---

test("analytics_page_unmount_with_active_poll_cancels_once_and_sends_no_further_request", async () => {
  let resolveRequest: ((r: R) => void) | null = null;
  const request = vi.fn(() => new Promise<R>((resolve) => { resolveRequest = resolve; }));   // отложенный промис — опрос «в полёте» на момент ухода
  const cancel = vi.fn(() => Promise.resolve(reply(200, null)));
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request, cancel,
  });
  const { unmount } = render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1));     // первый тик опроса уже в полёте (ответа нет)

  unmount();
  await waitFor(() => expect(cancel).toHaveBeenCalledTimes(1));
  expect(cancel).toHaveBeenCalledWith("r1");

  const callsAtUnmount = request.mock.calls.length;
  resolveRequest!(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));   // поздний ответ
  await sleep(30);
  expect(request.mock.calls.length).toBe(callsAtUnmount);          // после unmount новых request нет
  expect(cancel).toHaveBeenCalledTimes(1);                         // и cancel — ровно один раз, не повторно
});

test("analytics_page_retry_after_network_failure_reuses_same_client_request_id", async () => {
  const ask = vi.fn()
    .mockRejectedValueOnce(new Error("сеть оборвалась"))
    .mockResolvedValueOnce(reply(202, { request_id: "r1" }));
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    request: () => Promise.resolve(reply(200, jobBody())),
  });
  (api as unknown as { ask: typeof ask }).ask = ask;
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос про узел 7" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  expect(await screen.findByText(T_DOWN)).toBeTruthy();         // сеть оборвалась (status 0) — replyText → T_DOWN
  const retryBtn = await screen.findByRole("button", { name: "Повторить" });
  fireEvent.click(retryBtn);
  await waitFor(() => expect(ask).toHaveBeenCalledTimes(2));
  expect(ask.mock.calls[0][1].clientRequestId).toBe(ask.mock.calls[1][1].clientRequestId);
  expect(ask.mock.calls[0][1].question).toBe("Вопрос про узел 7");
});

// --- Доработка: GET conversations/{id} отдаёт историю как {question, response} (Task 4, ed32131; INTERFACES.md
// «Правка раунд 1», п.5) — показываем вопрос рядом с ответом, как обычный чат; вопрос только текстом ---

test("analytics_page_history_shows_question_bubble_before_its_answer", async () => {
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [{ question: "Какой расход массы на выходе?", response: jobBody({ sections: [section()] }) }],
    })),
  });
  const { container } = render(<AnalyticsPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  const question = await screen.findByText("Какой расход массы на выходе?");
  expect(question.getAttribute("data-role")).toBe("user");         // приём обычного чата (.msg[data-role="user"])
  const answer = await screen.findByText(/расход 8 т\/ч/);         // текст section() из analytics.test.ts-подобного тела
  // вопрос стоит раньше ответа в DOM (как переписка: вопрос → проверенный ответ)
  expect(question.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(container.querySelector(".an-thread")?.children.length).toBe(2);   // ровно пузырь + карточка, не лишнее
});

test("analytics_page_history_question_with_markup_stays_plain_text", async () => {
  const xss = "<img src=x onerror=alert(1)><script>alert(2)</script>";
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [{ question: xss, response: jobBody() }],
    })),
  });
  const { container } = render(<AnalyticsPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  await screen.findByText(xss);                            // текстовым узлом целиком, как написано
  expect(container.querySelector("img, script")).toBeNull();        // ни один тег из вопроса не исполнился
  expect(container.textContent).toContain(xss);
});

test("analytics_page_conversation_not_found_shows_error_not_crash", async () => {
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(404, { name: "NotFound" })),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  expect(await screen.findByText(T_AN_UNAVAILABLE)).toBeTruthy();
  // остальная страница (список и заголовок) жива — не обрушилась
  expect(screen.getByText(/Аналитический чат технолога/)).toBeTruthy();
});

test("analytics_page_signed_out_on_401", async () => {
  const onSignedOut = vi.fn();
  const api = fakeApi({ capabilities: () => Promise.resolve(reply(401, { name: "Unauthorized" })) });
  render(<AnalyticsPage api={api} onSignedOut={onSignedOut} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

test("analytics_page_service_disabled_404_shows_generic_text", async () => {
  const api = fakeApi({ capabilities: () => Promise.resolve(reply(404, { name: "NotFound" })) });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_AN_UNAVAILABLE)).toBeTruthy();
});

test("analytics_page_shows_reviewer_nav_only_with_review_authority", async () => {
  const api = fakeApi({ capabilities: () => Promise.resolve(reply(200, CAPS_REVIEWER)) });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} />);
  const reviewBtn = await screen.findByRole("button", { name: T_AN_REVIEWS });
  fireEvent.click(reviewBtn);
  expect(await screen.findByText("Заявок на согласование нет")).toBeTruthy();
});

// --- план Д17, Task 2: раздел «Связи с историаном» — доступен ВСЕМ вошедшим с доступом к вкладке (не только
// с правом согласования, в отличие от «Назначенное согласование») ---

test("analytics_page_links_nav_visible_without_review_authority_and_shows_links_section", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [{
      id: "EQ-FAKE-001", display_label: "20FAKE_001_PV", document_context: "уровнемер в машинном бассейне №1",
      state: "unbound", historian_tag: null, approved_by: null, approved_at: null, pending_request_id: null,
    }] })),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} />);
  expect(screen.queryByRole("button", { name: T_AN_REVIEWS })).toBeNull();   // CAPS_PLAIN: review_authority=false

  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINKS }));
  expect(await screen.findByText("20FAKE_001_PV")).toBeTruthy();
  // CAPS_PLAIN: data_bridge_available=false — честная строка, список при этом показан (строка выше)
  expect(screen.getByText(T_AN_LINKS_NO_BRIDGE)).toBeTruthy();
});

test("analytics_page_links_section_empty_list", async () => {
  render(<AnalyticsPage api={fakeApi()} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINKS }));
  expect(await screen.findByText(T_AN_LINKS_EMPTY)).toBeTruthy();
});

// --- план Д17, Task 1 «Produces»: секция telemetry_observation рисуется как остальные, подпись карточки —
// «Данные историана» (AN_SECTION_LABELS) ---

test("analytics_page_shows_telemetry_observation_section_label", async () => {
  const api = fakeApi({
    createConversation: () => Promise.resolve(reply(201, { id: "c1" })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      sections: [section({
        kind: "telemetry_observation",
        text: "Данные историана по подписи «20FAKE_001_PV» (тег 20FAKE_001_PV; связь согласовал tech1, " +
          "14.10.2026): 42 % на 2026-10-14T08:00:00+00:00 — время историана (Europe/Moscow); качество: хорошее.",
      })],
    }))),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Новый диалог: Справка по документам/ }));
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Какой уровень в баке 1?" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  expect(await screen.findByText("Данные историана")).toBeTruthy();
  expect(screen.getByText(/время историана/)).toBeTruthy();
});

// --- Правка, раунд 1, п.1: пауза опроса при скрытой вкладке (НЕ отмена задания, НЕ сброс pollToken) ---

test("analytics_page_pauses_polling_when_hidden_and_resumes_on_visible", async () => {
  const { doc, setHidden } = fakeVisibilityDoc();
  const request = vi.fn(async () => reply(200, jobBody({ state: "generating", stage_label: "Формирование ответа" })));
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request,
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={15} doc={doc} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await screen.findByRole("button", { name: T_AN_CANCEL });              // первый тик уже пришёл ("generating")

  const callsBeforeHidden = request.mock.calls.length;
  setHidden(true);                            // вкладка скрыта — таймер следующего тика должен погаснуть
  await sleep(80);                            // заметно дольше нескольких pollMs — без паузы было бы ≥ 5 новых вызовов
  expect(request.mock.calls.length).toBe(callsBeforeHidden);
  expect(screen.getByRole("button", { name: T_AN_CANCEL })).toBeTruthy();   // задание не отменено, кнопка жива

  setHidden(false);                           // вернулись — сразу один опрос, интервал восстанавливается
  await waitFor(() => expect(request.mock.calls.length).toBeGreaterThan(callsBeforeHidden));
});

// --- Правка, раунд 2, п.1: гонка «скрылись, пока api.request() уже в полёте» ---

function pendingConversationSetup() {
  return {
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
  };
}

test("analytics_page_hidden_while_request_in_flight_pauses_after_it_resolves", async () => {
  const { doc, setHidden } = fakeVisibilityDoc();
  let calls = 0;
  let resolveSecond: ((r: R) => void) | null = null;
  const request = vi.fn(() => {
    calls += 1;
    if (calls === 1) return Promise.resolve(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));
    return new Promise<R>((resolve) => { resolveSecond = resolve; });   // второй тик — ещё в полёте, не отвечает
  });
  const api = fakeApi({ ...pendingConversationSetup(), request });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} doc={doc} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await screen.findByRole("button", { name: T_AN_CANCEL });            // первый тик пришёл ("generating")
  await waitFor(() => expect(resolveSecond).not.toBeNull());           // второй тик стартовал — он «в полёте»
  expect(calls).toBe(2);

  setHidden(true);                      // скрылись ПОКА запрос уже ушёл в сеть: pollTimer в этот момент null,
  resolveSecond!(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));  // ответ пришёл после
  await sleep(30);
  expect(calls).toBe(2);                // третий тик не запланирован — poll() увидел hidden уже после ответа
  await sleep(60);
  expect(calls).toBe(2);                // и не появился позже — пауза держится, не однократный пропуск такта

  setHidden(false);                     // возврат — опрос продолжается без дополнительного вмешательства теста
  await waitFor(() => expect(calls).toBeGreaterThan(2));
});

test("analytics_page_rapid_visibility_toggle_during_in_flight_request_keeps_single_request", async () => {
  const { doc, setHidden } = fakeVisibilityDoc();
  let calls = 0;
  let resolveSecond: ((r: R) => void) | null = null;
  const request = vi.fn(() => {
    calls += 1;
    if (calls === 1) return Promise.resolve(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));
    return new Promise<R>((resolve) => { resolveSecond = resolve; });
  });
  const api = fakeApi({ ...pendingConversationSetup(), request });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} doc={doc} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await screen.findByRole("button", { name: T_AN_CANCEL });
  await waitFor(() => expect(resolveSecond).not.toBeNull());
  expect(calls).toBe(2);

  // быстрые переключения ровно во время полёта: ни одно visible не должно запустить второй параллельный doTick
  // (видимый — pollTimer.current===null всё это время, пока первый запрос не вернулся; без inFlight это дало бы
  // третий/четвёртый вызов раньше ответа второго)
  setHidden(true); setHidden(false); setHidden(true); setHidden(false);
  expect(calls).toBe(2);

  resolveSecond!(reply(200, jobBody({ state: "completed", stage_label: "Готово" })));
  await waitFor(() => expect(screen.queryByRole("button", { name: T_AN_CANCEL })).toBeNull());   // дошло до конца
  expect(calls).toBe(2);                 // терминально — больше тиков не нужно вовсе
});

// --- Правка, раунд 3: inFlight должен быть привязан к токену цикла, не простым boolean (round 2) — иначе
// устаревший ответ (токен сменился, пока старый запрос был в полёте: отмена или переключение диалога) оставлял
// «призрачный» признак «в полёте» навсегда, и visible после hidden не запускал новый тик — job.kind==="polling"
// зависал без ошибки на экране ---

test("analytics_page_cancel_during_in_flight_request_then_hidden_then_visible_resumes", async () => {
  const { doc, setHidden } = fakeVisibilityDoc();
  let calls = 0;
  let resolveSecond: ((r: R) => void) | null = null;
  const request = vi.fn(() => {
    calls += 1;
    if (calls === 1) return Promise.resolve(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));
    return new Promise<R>((resolve) => { resolveSecond = resolve; });
  });
  const cancel = vi.fn(() => Promise.resolve(reply(503, { name: "Unavailable" })));   // отказ отмены — нетерминально
  const api = fakeApi({ ...pendingConversationSetup(), request, cancel });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={50} doc={doc} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await screen.findByRole("button", { name: T_AN_CANCEL });              // первый тик пришёл
  await waitFor(() => expect(resolveSecond).not.toBeNull());             // второй тик — его запрос уже в полёте
  expect(calls).toBe(2);

  fireEvent.click(screen.getByRole("button", { name: T_AN_CANCEL }));    // отмена — токен цикла меняется прямо сейчас
  await waitFor(() => expect(cancel).toHaveBeenCalledWith("r1"));        // 503 → снова "polling", новый тик T2 запланирован

  resolveSecond!(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));   // устаревший (T1) ответ
  await sleep(10);                                                      // дать микрозадачам пройти; таймер T2 (50 мс) ещё ждёт

  setHidden(true);                                                      // гасим запланированный тик T2 до его срабатывания
  await sleep(70);                                                      // дольше pollMs — без паузы таймер бы уже сгорел
  expect(calls).toBe(2);                                                // третьего тика не было пока скрыто

  setHidden(false);                                                     // возврат — опрос должен продолжиться, не зависнуть
  await waitFor(() => expect(calls).toBe(3));                           // без фикса раунда 3 — "призрачный" inFlight(true)
  await sleep(40);                                                      // блокировал бы эту проверку навсегда
  expect(calls).toBe(3);                                                // ровно один новый запрос, не больше
});

test("analytics_page_switch_conversation_during_in_flight_then_new_question_hidden_visible_before_timer", async () => {
  const { doc, setHidden } = fakeVisibilityDoc();
  let calls = 0;
  let resolveSecond: ((r: R) => void) | null = null;
  const request = vi.fn(() => {
    calls += 1;
    if (calls === 2) return new Promise<R>((resolve) => { resolveSecond = resolve; });
    return Promise.resolve(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));
  });
  const ask = vi.fn(() => Promise.resolve(reply(202, { request_id: `r${(ask as ReturnType<typeof vi.fn>).mock.calls.length}` })));
  const api = fakeApi({ ...pendingConversationSetup(), request, ask });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={50} doc={doc} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Первый вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));
  await screen.findByRole("button", { name: T_AN_CANCEL });             // первый тик пришёл
  await waitFor(() => expect(resolveSecond).not.toBeNull());            // второй тик первого цикла — в полёте
  expect(calls).toBe(2);

  fireEvent.click(screen.getByRole("button", { name: /Диалог от/ }));   // переключение (тот же диалог) во время полёта —
  resolveSecond!(reply(200, jobBody({ state: "generating", stage_label: "Формирование" })));  // новый токен; устаревший ответ
  await sleep(10);

  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Второй вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));     // новый цикл (r2), poll() планирует таймер (50 мс)
  await waitFor(() => expect(ask).toHaveBeenCalledTimes(2));
  await sleep(10);                                                      // дать poll() успеть поставить таймер нового цикла

  setHidden(true);                                                     // гасим и сразу возвращаем — до первого срабатывания
  setHidden(false);                                                    // таймера нового цикла (у него есть ещё ~40 мс)
  await waitFor(() => expect(calls).toBe(3));                          // опрос идёт — не завис на «призрачном» inFlight
});

// --- Правка, раунд 1, п.2: конфликт источников (errorCode source_conflict) — отправка на согласование ---

test("analytics_page_source_conflict_can_be_sent_to_review", async () => {
  const api = fakeApi({
    conversations: () => Promise.resolve(reply(200, { conversations: [
      { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
       last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
    ] })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({ error_code: "source_conflict" }))),
    createReview: () => Promise.resolve(reply(201, {
      id: "rv1", kind: "conflict", subject_id: "r1", old_value: null,
      new_value: "Рассмотреть конфликт источников в ответе", scope: "КДМ-1", period_start: null, period_end: null,
      rationale: "Нашёл два противоречащих источника", status: "pending", created_by: "student-03",
      created_at: "2026-10-14T08:00:00+00:00", decided_by: null, decided_at: null, comment: null,
    })),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Диалог от/ }));
  fireEvent.change(await screen.findByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  fireEvent.click(await screen.findByRole("button", { name: T_AN_SEND_TO_REVIEW }));   // открыть форму
  fireEvent.change(screen.getByLabelText(T_AN_SEND_TO_REVIEW_RATIONALE),
                    { target: { value: "Нашёл два противоречащих источника" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND_TO_REVIEW }));          // отправить

  await waitFor(() => expect(api.createReview).toHaveBeenCalledWith({
    kind: "conflict", subjectId: "r1", newValue: "Рассмотреть конфликт источников в ответе", scope: "КДМ-1",
    rationale: "Нашёл два противоречащих источника",
  }));
  expect(await screen.findByText(T_AN_SEND_TO_REVIEW_DONE)).toBeTruthy();
});

// --- Правка, раунд 1, п.3: aria-expanded у кнопки раскрытия фрагмента источника (AnalyticsSources) ---

test("analytics_page_source_fragment_toggle_has_aria_expanded", async () => {
  const api = fakeApi({
    createConversation: () => Promise.resolve(reply(201, { id: "c1" })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      sections: [section({
        evidence_refs: [{ source_id: "S1", fragment_id: "F1", corpus_version: "20FAKE-v1", limitation: "",
                         locator: { type: "pdf_page", page: 3 } }],
      })],
    }))),
    evidence: () => Promise.resolve(reply(200, {
      id: "F1", source_id: "S1", text: "цитата источника", corpus_version: "20FAKE-v1", limitation: "",
      locator: { type: "pdf_page", page: 3 }, source_title: "ПЦБК: КДМ-1", source_type: "public_primary",
      source_date: "2026-10-13",
    })),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Новый диалог: Справка по документам/ }));
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  const toggle = await screen.findByRole("button", { name: T_AN_SHOW_FRAGMENT });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(await screen.findByText("цитата источника")).toBeTruthy();
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
});

// --- правка итогового ревью, п.1 F2-brief: название/тип/дата источника при раскрытии (мастер-промпт §6) ---

test("analytics_page_source_reveal_shows_title_type_date_with_code_nearby", async () => {
  const api = fakeApi({
    createConversation: () => Promise.resolve(reply(201, { id: "c1" })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      sections: [section({
        evidence_refs: [{ source_id: "PUB-01", fragment_id: "F1", corpus_version: "20FAKE-v1", limitation: "",
                         locator: { type: "pdf_page", page: 3 } }],
      })],
    }))),
    evidence: () => Promise.resolve(reply(200, {
      id: "F1", source_id: "PUB-01", text: "цитата источника", corpus_version: "20FAKE-v1", limitation: "",
      locator: { type: "pdf_page", page: 3 }, source_title: "ПЦБК: производство бумаги и картона",
      source_type: "public_primary", source_date: "2026-10-13",
    })),
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Новый диалог: Справка по документам/ }));
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  // до раскрытия — только код (как раньше), ни названия, ни типа, ни даты ещё нет
  expect(await screen.findByText(/PUB-01.*страница 3/)).toBeTruthy();
  expect(screen.queryByText("ПЦБК: производство бумаги и картона")).toBeNull();

  fireEvent.click(await screen.findByRole("button", { name: T_AN_SHOW_FRAGMENT }));

  expect(await screen.findByText("ПЦБК: производство бумаги и картона")).toBeTruthy();   // название вместо кода
  expect(screen.getByText(/PUB-01/)).toBeTruthy();                                       // код — мелко рядом
  expect(screen.getByText("public_primary")).toBeTruthy();                               // тип
  expect(screen.getByText(/проверено 2026-10-13/)).toBeTruthy();                         // дата
});

test("analytics_page_source_title_xss_string_stays_plain_text", async () => {
  const xss = "<img src=x onerror=alert(1)><script>alert(2)</script>";
  const api = fakeApi({
    createConversation: () => Promise.resolve(reply(201, { id: "c1" })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      sections: [section({
        evidence_refs: [{ source_id: "S1", fragment_id: "F1", corpus_version: "20FAKE-v1", limitation: "",
                         locator: { type: "pdf_page", page: 3 } }],
      })],
    }))),
    evidence: () => Promise.resolve(reply(200, {
      id: "F1", source_id: "S1", text: "цитата источника", corpus_version: "20FAKE-v1", limitation: "",
      locator: { type: "pdf_page", page: 3 }, source_title: xss, source_type: null, source_date: null,
    })),
  });
  const { container } = render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Новый диалог: Справка по документам/ }));
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  fireEvent.click(await screen.findByRole("button", { name: T_AN_SHOW_FRAGMENT }));
  await screen.findByText(xss);                                       // текстовым узлом целиком, как написано
  expect(container.querySelector("img, script")).toBeNull();          // ни один тег из названия не исполнился
  expect(container.textContent).toContain(xss);
});

// --- Правка, раунд 2, п.2 (мелкое): aria-expanded — true и при ошибке получения фрагмента, не только при тексте ---

test("analytics_page_source_fragment_toggle_aria_expanded_true_on_error_too", async () => {
  const api = fakeApi({
    createConversation: () => Promise.resolve(reply(201, { id: "c1" })),
    conversation: () => Promise.resolve(reply(200, {
      conversation: { id: "c1", mode: "reference", created_at: "2026-10-14T08:00:00+00:00",
                     last_activity_at: "2026-10-14T08:00:00+00:00", corpus_version: "20FAKE-v1" },
      requests: [],
    })),
    ask: () => Promise.resolve(reply(202, { request_id: "r1" })),
    request: () => Promise.resolve(reply(200, jobBody({
      sections: [section({
        evidence_refs: [{ source_id: "S1", fragment_id: "F1", corpus_version: "20FAKE-v1", limitation: "",
                         locator: { type: "pdf_page", page: 3 } }],
      })],
    }))),
    evidence: () => Promise.resolve(reply(503, { name: "Unavailable" })),   // фрагмент сейчас не открывается
  });
  render(<AnalyticsPage api={api} onSignedOut={vi.fn()} pollMs={10} />);
  fireEvent.click(await screen.findByRole("button", { name: /Новый диалог: Справка по документам/ }));
  fireEvent.change(screen.getByLabelText(/Ваш вопрос технологу/), { target: { value: "Вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND }));

  const toggle = await screen.findByRole("button", { name: T_AN_SHOW_FRAGMENT });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  await screen.findByRole("alert");                                     // T_AN_FRAGMENT_ERROR показан
  expect(screen.getByRole("button", { name: /Скрыть/ }).getAttribute("aria-expanded")).toBe("true");
});
