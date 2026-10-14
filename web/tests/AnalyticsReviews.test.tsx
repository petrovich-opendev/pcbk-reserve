// Экран назначенного согласования КДМ-1 (план КДМ1, Task 6 — только интерфейс; мастер-промпт §6): список заявок,
// принять/отклонить/запросить сведения с комментарием, 401 → вход, отказ ручки — понятный текст. Видимость самого
// экрана (capabilities.review_authority) проверяет AnalyticsPage.test.tsx — здесь компонент сам по себе.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { AnalyticsApi } from "../src/analyticsApi";
import { AnalyticsReviews } from "../src/components/AnalyticsReviews";
import {
  T_AN_REVIEW_ACCEPT, T_AN_REVIEW_DECIDE_CONFLICT, T_AN_REVIEW_DECIDE_FORBIDDEN, T_AN_REVIEW_REJECT,
  T_AN_REVIEWS_EMPTY,
} from "../src/texts";

type R = { status: number; body: unknown };
const reply = (status: number, body: unknown = null): R => ({ status, body });

function fakeApi(over: Partial<Pick<AnalyticsApi, "reviewRequests" | "decideReview">> = {}): AnalyticsApi {
  return {
    capabilities: vi.fn(), conversations: vi.fn(), createConversation: vi.fn(), conversation: vi.fn(),
    ask: vi.fn(), request: vi.fn(), cancel: vi.fn(), evidence: vi.fn(), createReview: vi.fn(),
    reviewRequests: vi.fn(over.reviewRequests ?? (() => Promise.resolve(reply(200, { requests: [] })))),
    decideReview: vi.fn(over.decideReview ?? (() => Promise.resolve(reply(200, null)))),
  } as unknown as AnalyticsApi;
}

const PENDING = {
  id: "rv1", kind: "equipment_link", subject_id: "20FAKE_07_PV", old_value: null,
  new_value: "связать с узлом формования", scope: "КДМ-1", period_start: null, period_end: null,
  rationale: "подпись совпадает с руководством Valmet", status: "pending", created_by: "student-03",
  created_at: "2026-10-14T08:00:00+00:00", decided_by: null, decided_at: null, comment: null,
};

test("analytics_reviews_empty_list", async () => {
  render(<AnalyticsReviews api={fakeApi()} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_AN_REVIEWS_EMPTY)).toBeTruthy();
});

test("analytics_reviews_lists_pending_and_accepts_with_comment", async () => {
  const api = fakeApi({ reviewRequests: () => Promise.resolve(reply(200, { requests: [PENDING] })) });
  render(<AnalyticsReviews api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("связать с узлом формования")).toBeTruthy();
  const textareas = screen.getAllByRole("textbox");
  fireEvent.change(textareas[0]!, { target: { value: "подтверждено по руководству" } });
  fireEvent.click(screen.getByRole("button", { name: T_AN_REVIEW_ACCEPT }));
  await waitFor(() => expect(api.decideReview).toHaveBeenCalledWith("rv1", "accepted", "подтверждено по руководству"));
});

test("analytics_reviews_refusal_shows_text", async () => {
  const api = fakeApi({
    reviewRequests: () => Promise.resolve(reply(503, { name: "Unavailable", data: { message: "Заявки сейчас не читаются" } })),
  });
  render(<AnalyticsReviews api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("Заявки сейчас не читаются")).toBeTruthy();
});

// --- правка итогового ревью, п.2 F2-brief: отказ decide() теперь виден проверяющему (раньше — молчание) ---

test("analytics_reviews_decide_409_shows_conflict_text_and_reloads_list", async () => {
  const reviewRequests = vi.fn()
    .mockResolvedValueOnce(reply(200, { requests: [PENDING] }))
    .mockResolvedValueOnce(reply(200, { requests: [{ ...PENDING, status: "accepted", decided_by: "other-teacher" }] }));
  const api = fakeApi({
    reviewRequests,
    decideReview: () => Promise.resolve(reply(409, { name: "Conflict" })),
  });
  render(<AnalyticsReviews api={api} onSignedOut={vi.fn()} />);
  await screen.findByText("связать с узлом формования");
  fireEvent.click(screen.getByRole("button", { name: T_AN_REVIEW_ACCEPT }));

  expect((await screen.findByRole("alert")).textContent).toBe(T_AN_REVIEW_DECIDE_CONFLICT);
  await waitFor(() => expect(reviewRequests).toHaveBeenCalledTimes(2));   // список перезагружен после отказа
});

test("analytics_reviews_decide_403_shows_forbidden_text", async () => {
  const api = fakeApi({
    reviewRequests: () => Promise.resolve(reply(200, { requests: [PENDING] })),
    decideReview: () => Promise.resolve(reply(403, { name: "Forbidden" })),
  });
  render(<AnalyticsReviews api={api} onSignedOut={vi.fn()} />);
  await screen.findByText("связать с узлом формования");
  fireEvent.click(screen.getByRole("button", { name: T_AN_REVIEW_REJECT }));

  expect((await screen.findByRole("alert")).textContent).toBe(T_AN_REVIEW_DECIDE_FORBIDDEN);
});

test("analytics_reviews_signed_out_on_401", async () => {
  const onSignedOut = vi.fn();
  const api = fakeApi({ reviewRequests: () => Promise.resolve(reply(401, { name: "Unauthorized" })) });
  render(<AnalyticsReviews api={api} onSignedOut={onSignedOut} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});
