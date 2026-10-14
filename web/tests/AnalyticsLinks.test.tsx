// Раздел «Связи с историаном» (план Д17, Task 2; мастер-промпт §8): список связей и их состояния, подбор тега
// (прокси catalog_search) → выбор + обоснование → заявка equipment_link на согласование, отказ/пустой список
// службы данных показывается как есть, честная строка при недоступной службе данных, 401 → выход. Теги —
// только синтетические 20FAKE_*/25FAKE_* (ни одного имени тега ПЦБК).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { AnalyticsApi } from "../src/analyticsApi";
import { AnalyticsLinks } from "../src/components/AnalyticsLinks";
import {
  T_AN_LINK_NO_CANDIDATES, T_AN_LINK_PENDING, T_AN_LINK_PICK, T_AN_LINK_UNBOUND, T_AN_LINKS_EMPTY,
  T_AN_LINKS_NO_BRIDGE, T_AN_SEND_TO_REVIEW, T_AN_SEND_TO_REVIEW_DONE, T_AN_SEND_TO_REVIEW_RATIONALE,
} from "../src/texts";

type R = { status: number; body: unknown };
const reply = (status: number, body: unknown = null): R => ({ status, body });

function fakeApi(over: Partial<Pick<AnalyticsApi, "links" | "linkCandidates" | "createReview">> = {}): AnalyticsApi {
  const unset = () => Promise.resolve(reply(404, { name: "NotFound" }));
  return {
    capabilities: vi.fn(), conversations: vi.fn(), createConversation: vi.fn(), conversation: vi.fn(),
    ask: vi.fn(), request: vi.fn(), cancel: vi.fn(), evidence: vi.fn(), reviewRequests: vi.fn(), decideReview: vi.fn(),
    links: vi.fn(over.links ?? (() => Promise.resolve(reply(200, { links: [] })))),
    linkCandidates: vi.fn(over.linkCandidates ?? unset),
    createReview: vi.fn(over.createReview ?? unset),
  } as unknown as AnalyticsApi;
}

const LINK_UNBOUND = {
  id: "L1", display_label: "Уровнемер бассейна формования", document_context: "уровнемер в машинном бассейне №1",
  state: "unbound", historian_tag: null, approved_by: null, approved_at: null, pending_request_id: null,
};

test("analytics_links_empty_list_shows_empty_text", async () => {
  render(<AnalyticsLinks api={fakeApi()} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  expect(await screen.findByText(T_AN_LINKS_EMPTY)).toBeTruthy();
});

test("analytics_links_shows_three_states", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [
      LINK_UNBOUND,
      { id: "L2", display_label: "Расходомер на линии сушки", document_context: "", state: "pending",
       historian_tag: null, approved_by: null, approved_at: null, pending_request_id: "rv1" },
      { id: "L3", display_label: "Датчик давления напорного ящика", document_context: "", state: "linked",
       historian_tag: "20FAKE_003_PV", approved_by: "tech1", approved_at: "2026-10-14T08:00:00+00:00",
       pending_request_id: null },
    ] })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  expect(await screen.findByText(T_AN_LINK_UNBOUND)).toBeTruthy();
  expect(screen.getByText(T_AN_LINK_PENDING)).toBeTruthy();
  expect(screen.getByText(/связана: 20FAKE_003_PV \(согласовал tech1,/)).toBeTruthy();
  expect(screen.getByText("Уровнемер бассейна формования")).toBeTruthy();
  expect(screen.getByText("уровнемер в машинном бассейне №1")).toBeTruthy();   // document_context
});

test("analytics_links_no_data_bridge_shows_honest_line_but_keeps_list_and_hides_pick_button", async () => {
  const api = fakeApi({ links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })) });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={false} />);
  expect(await screen.findByText(T_AN_LINKS_NO_BRIDGE)).toBeTruthy();
  expect(screen.getByText("Уровнемер бассейна формования")).toBeTruthy();   // список всё равно показан
  expect(screen.queryByRole("button", { name: T_AN_LINK_PICK })).toBeNull();
});

test("analytics_links_pick_shows_candidates_as_tag_description_unit", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(200, {
      status: "ok", message: null,
      candidates: [{ tag: "20FAKE_777_PV", description: "Уровень бассейна формования", unit: "%" }],
    })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  expect(await screen.findByText("20FAKE_777_PV — Уровень бассейна формования, %")).toBeTruthy();
  expect(api.linkCandidates).toHaveBeenCalledWith("L1");
});

test("analytics_links_pick_candidate_without_unit_omits_trailing_comma", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(200, {
      status: "ok", message: null,
      candidates: [{ tag: "25FAKE_007_CLS", description: "Состояние насоса", unit: null }],
    })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  expect(await screen.findByText("25FAKE_007_CLS — Состояние насоса")).toBeTruthy();
});

// правка раунда 1 ревью (I1): успех createReview больше не вызывает общий load() (который размонтировал весь
// список и стирал формы ДРУГИХ строк) — своя строка обновляется локально (state:"pending"), без сетевого
// перезапроса списка
test("analytics_links_select_candidate_plus_rationale_sends_review_and_updates_only_that_row_locally", async () => {
  const links = vi.fn(() => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })));
  const linkCandidates = vi.fn(() => Promise.resolve(reply(200, {
    status: "ok", message: null,
    candidates: [{ tag: "20FAKE_777_PV", description: "Уровень бассейна формования", unit: "%" }],
  })));
  const createReview = vi.fn(() => Promise.resolve(reply(201, {
    id: "rv1", kind: "equipment_link", subject_id: "L1", old_value: null, new_value: "20FAKE_777_PV",
    scope: "КДМ-1", period_start: null, period_end: null, rationale: "совпадает с подписью на схеме",
    status: "pending", created_by: "student-03", created_at: "2026-10-14T08:00:00+00:00", decided_by: null,
    decided_at: null, comment: null,
  })));
  const api = fakeApi({ links, linkCandidates, createReview });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);

  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  await screen.findByRole("radio");
  fireEvent.click(screen.getByRole("radio"));
  // без обоснования отправка недоступна (обоснование студента обязательно)
  expect((screen.getByRole("button", { name: T_AN_SEND_TO_REVIEW }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText(T_AN_SEND_TO_REVIEW_RATIONALE),
                    { target: { value: "совпадает с подписью на схеме" } });
  expect((screen.getByRole("button", { name: T_AN_SEND_TO_REVIEW }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND_TO_REVIEW }));

  await waitFor(() => expect(createReview).toHaveBeenCalledWith({
    kind: "equipment_link", subjectId: "L1", newValue: "20FAKE_777_PV", scope: "КДМ-1",
    rationale: "совпадает с подписью на схеме",
  }));
  expect(await screen.findByText(T_AN_SEND_TO_REVIEW_DONE)).toBeTruthy();
  expect(await screen.findByText(T_AN_LINK_PENDING)).toBeTruthy();
  expect(links).toHaveBeenCalledTimes(1);          // ровно начальная загрузка — без перезапроса списка
});

// --- правка раунда 1 ревью (I1), воспроизведение сценария рецензента: открыть подбор на связи A, выбрать
// кандидата и ввести обоснование, НЕ отправлять; на связи B — выбрать, ввести обоснование и отправить; форма
// A должна остаться нетронутой (выбор и текст), B — «на согласовании» ---

const LINK_A = {
  id: "LA", display_label: "Уровнемер бассейна формования №1", document_context: "", state: "unbound",
  historian_tag: null, approved_by: null, approved_at: null, pending_request_id: null,
};
const LINK_B = {
  id: "LB", display_label: "Уровнемер бассейна формования №2", document_context: "", state: "unbound",
  historian_tag: null, approved_by: null, approved_at: null, pending_request_id: null,
};

test("analytics_links_submitting_one_row_keeps_unsubmitted_open_form_on_another_row", async () => {
  const links = vi.fn(() => Promise.resolve(reply(200, { links: [LINK_A, LINK_B] })));
  const linkCandidates = vi.fn((linkId: string) => Promise.resolve(reply(200, {
    status: "ok", message: null,
    candidates: [{
      tag: linkId === "LA" ? "20FAKE_AAA_PV" : "20FAKE_BBB_PV",
      description: linkId === "LA" ? "Кандидат A" : "Кандидат B", unit: "%",
    }],
  })));
  const createReview = vi.fn(() => Promise.resolve(reply(201, {
    id: "rvB", kind: "equipment_link", subject_id: "LB", old_value: null, new_value: "20FAKE_BBB_PV",
    scope: "КДМ-1", period_start: null, period_end: null, rationale: "обоснование B", status: "pending",
    created_by: "student-03", created_at: "2026-10-14T08:00:00+00:00", decided_by: null, decided_at: null,
    comment: null,
  })));
  const api = fakeApi({ links, linkCandidates, createReview });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);

  // открыть подбор на A, выбрать кандидата и ввести обоснование, но НЕ отправлять
  fireEvent.click((await screen.findAllByRole("button", { name: T_AN_LINK_PICK }))[0]!);
  await screen.findByText("20FAKE_AAA_PV — Кандидат A, %");
  fireEvent.click(screen.getAllByRole("radio")[0]!);
  fireEvent.change(screen.getAllByLabelText(T_AN_SEND_TO_REVIEW_RATIONALE)[0]!,
                    { target: { value: "обоснование A — не отправлено" } });

  // открыть подбор на B, выбрать кандидата, ввести обоснование и отправить
  fireEvent.click(screen.getAllByRole("button", { name: T_AN_LINK_PICK })[1]!);
  await screen.findByText("20FAKE_BBB_PV — Кандидат B, %");
  fireEvent.click(screen.getAllByRole("radio")[1]!);
  fireEvent.change(screen.getAllByLabelText(T_AN_SEND_TO_REVIEW_RATIONALE)[1]!,
                    { target: { value: "обоснование B" } });
  fireEvent.click(screen.getAllByRole("button", { name: T_AN_SEND_TO_REVIEW })[1]!);

  await waitFor(() => expect(createReview).toHaveBeenCalledWith({
    kind: "equipment_link", subjectId: "LB", newValue: "20FAKE_BBB_PV", scope: "КДМ-1", rationale: "обоснование B",
  }));
  expect(await screen.findByText(T_AN_LINK_PENDING)).toBeTruthy();   // B — «на согласовании»

  // A — форма сохранилась: выбранный кандидат и текст обоснования не стёрлись успехом B
  expect((screen.getAllByRole("radio")[0]! as HTMLInputElement).checked).toBe(true);
  expect((screen.getAllByLabelText(T_AN_SEND_TO_REVIEW_RATIONALE)[0]! as HTMLTextAreaElement).value)
    .toBe("обоснование A — не отправлено");
  expect(links).toHaveBeenCalledTimes(1);          // без перезагрузки всего списка
});

// --- Minor ревью, раунд 1: aria-expanded у кнопки «Подобрать тег» (приём toggle-кнопки AnalyticsSources) ---

test("analytics_links_pick_button_tracks_aria_expanded", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(200, {
      status: "ok", message: null,
      candidates: [{ tag: "20FAKE_777_PV", description: "Уровень бассейна формования", unit: "%" }],
    })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  const button = await screen.findByRole("button", { name: T_AN_LINK_PICK });
  expect(button.getAttribute("aria-expanded")).toBe("false");

  fireEvent.click(button);
  await screen.findByText("20FAKE_777_PV — Уровень бассейна формования, %");
  expect(button.getAttribute("aria-expanded")).toBe("true");

  fireEvent.click(button);                         // тот же переключатель закрывает подбор обратно
  expect(button.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("20FAKE_777_PV — Уровень бассейна формования, %")).toBeNull();
});

test("analytics_links_candidates_refusal_shows_message_text", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(200, {
      status: "refused", message: "Частота тега вне разрешённого списка", candidates: [],
    })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  expect((await screen.findByRole("alert")).textContent).toBe("Частота тега вне разрешённого списка");
});

test("analytics_links_candidates_empty_shows_no_candidates_text", async () => {
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(200, { status: "ok", message: null, candidates: [] })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={vi.fn()} dataBridgeAvailable={true} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  expect(await screen.findByText(T_AN_LINK_NO_CANDIDATES)).toBeTruthy();
});

test("analytics_links_signed_out_on_401_loading_list", async () => {
  const onSignedOut = vi.fn();
  const api = fakeApi({ links: () => Promise.resolve(reply(401, { name: "Unauthorized" })) });
  render(<AnalyticsLinks api={api} onSignedOut={onSignedOut} dataBridgeAvailable={true} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

test("analytics_links_signed_out_on_401_candidates", async () => {
  const onSignedOut = vi.fn();
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(401, { name: "Unauthorized" })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={onSignedOut} dataBridgeAvailable={true} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

test("analytics_links_signed_out_on_401_create_review", async () => {
  const onSignedOut = vi.fn();
  const api = fakeApi({
    links: () => Promise.resolve(reply(200, { links: [LINK_UNBOUND] })),
    linkCandidates: () => Promise.resolve(reply(200, {
      status: "ok", message: null,
      candidates: [{ tag: "20FAKE_777_PV", description: "Уровень бассейна формования", unit: "%" }],
    })),
    createReview: () => Promise.resolve(reply(401, { name: "Unauthorized" })),
  });
  render(<AnalyticsLinks api={api} onSignedOut={onSignedOut} dataBridgeAvailable={true} />);
  fireEvent.click(await screen.findByRole("button", { name: T_AN_LINK_PICK }));
  fireEvent.click(await screen.findByRole("radio"));
  fireEvent.change(screen.getByLabelText(T_AN_SEND_TO_REVIEW_RATIONALE), { target: { value: "обоснование" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_SEND_TO_REVIEW }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});
