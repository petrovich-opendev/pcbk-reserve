// @vitest-environment node
// Пробуждение места в чате (Д6-R39): те же ветки и тексты, что у страницы входа.
// Среда node (preflight M9): viewFor и loginError берутся импортом из login.js — в jsdom импорт исполнил бы
// startPage(document). Сверка импортом, а не копией ожиданий: расхождение со страницей входа тест заметит.
import { afterEach, expect, test, vi } from "vitest";
import { GIVE_UP_S as LOGIN_GIVE_UP_S, loginError, viewFor } from "../../edge/static/login.js";
import { replyText, type Api, type Reply } from "../src/api";
import { GIVE_UP_S, placeView, wakeAndWait, type PlaceView } from "../src/workplace";

afterEach(() => { vi.useRealTimers(); });

test("place_view_matches_login_page", () => {
  const cases: unknown[] = [
    { state: "starting" }, { state: "sleeping" }, { state: "ready" },
    { state: "failed", detail: "Рабочее место не создано — сообщите администратору." }, { state: "failed" },
    { state: "gave_up" }, { state: "что-то новое" }, null, "<html>",
  ];
  for (const reply of cases) expect(placeView(reply), JSON.stringify(reply)).toEqual(viewFor(reply));
  expect(GIVE_UP_S).toBe(LOGIN_GIVE_UP_S);
});

test("reply_text_matches_login_page", () => {
  const cases: Reply[] = [
    { status: 503, body: { name: "Unavailable", data: { message: "Учётные записи недоступны — повторите позже" } } },
    { status: 502, body: "<html>" }, { status: 0, body: null }, { status: 504, body: { name: "x" } },
    { status: 500, body: { name: "x" } }, { status: 400, body: { name: "BadRequest", data: { message: "" } } },
  ];
  for (const r of cases) expect(replyText(r), JSON.stringify(r)).toBe(loginError(r.status, r.body));
});

// api с очередями ответов; последний ответ очереди повторяется
function fakeApi(q: { wake?: Reply[]; workplace?: Reply[] }): Api & { calls: string[] } {
  const calls: string[] = [];
  const take = (name: string, list: Reply[] = []) => async () => {
    calls.push(name); return list.length > 1 ? list.shift()! : list[0] ?? { status: 0, body: null };
  };
  return { calls, me: take("me"), logout: take("logout"), wake: take("wake", q.wake), workplace: take("workplace", q.workplace),
           ackNotice: take("ackNotice"), agents: take("agents"), events: take("events"), calc: take("calc"),
           report: async () => { calls.push("report"); } };
}
const R = (status: number, body: unknown): Reply => ({ status, body });

test("wake_and_wait_ready_after_polls", async () => {
  vi.useFakeTimers();
  const api = fakeApi({ wake: [R(202, { state: "sleeping" })], workplace: [R(200, { state: "starting" }), R(200, { state: "ready" })] });
  const views: PlaceView[] = [];
  const done = wakeAndWait({ api, onView: (v) => views.push(v) });
  await vi.advanceTimersByTimeAsync(2_000);
  expect(await done).toBe("ready");
  expect(views.map((v) => v.text)).toEqual(["Готовлю рабочее место…", "Готовлю рабочее место…", "Рабочее место готово"]);
  expect(api.calls).toEqual(["wake", "workplace", "workplace"]);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(api.calls).toHaveLength(3);                                              // после «готово» опрос кончился
});

test("wake_and_wait_signed_out_and_gave_up", async () => {
  vi.useFakeTimers();
  const out = fakeApi({ wake: [R(202, { state: "starting" })], workplace: [R(401, { name: "Unauthorized", data: { message: "Нужно войти" } })] });
  const outViews: PlaceView[] = [];
  const signedOut = wakeAndWait({ api: out, onView: (v) => outViews.push(v) });
  await vi.advanceTimersByTimeAsync(1_000);
  expect(await signedOut).toBe("signed_out");
  expect(await wakeAndWait({ api: fakeApi({ wake: [R(401, {})] }), onView: vi.fn() })).toBe("signed_out");

  const slow = fakeApi({ wake: [R(202, { state: "starting" })], workplace: [R(200, { state: "starting" })] });
  const views: PlaceView[] = [];
  const gaveUp = wakeAndWait({ api: slow, onView: (v) => views.push(v) });
  await vi.advanceTimersByTimeAsync(89_000);
  expect(views.at(-1)!.poll).toBe(true);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(await gaveUp).toBe("gave_up");
  expect(views.at(-1)).toEqual(viewFor({ state: "gave_up" }));
  const polls = slow.calls.filter((c) => c === "workplace").length;
  expect(polls).toBeGreaterThanOrEqual(GIVE_UP_S - 2); expect(polls).toBeLessThanOrEqual(GIVE_UP_S);   // раз в 1 с
  await vi.advanceTimersByTimeAsync(10_000);
  expect(slow.calls.filter((c) => c === "workplace")).toHaveLength(polls);
});

test("wake_and_wait_failures", async () => {
  vi.useFakeTimers();
  const detail = "Нет связи с прокси сокета — рабочее место не поднять. Сообщите преподавателю.";
  const views: PlaceView[] = [];
  expect(await wakeAndWait({ api: fakeApi({ wake: [R(200, { state: "failed", detail })] }), onView: (v) => views.push(v) })).toBe("failed");
  expect(views.at(-1)).toEqual(viewFor({ state: "failed", detail }));
  const refused = R(403, { name: "Forbidden", data: { message: "У преподавателя нет рабочего места" } });
  expect(await wakeAndWait({ api: fakeApi({ wake: [refused] }), onView: (v) => views.push(v) })).toBe("failed");
  expect(views.at(-1)).toEqual({ text: "У преподавателя нет рабочего места", poll: false, ok: false });

  // core перезапускается: 503 и сеть при опросе — ждать дальше до срока, текст ошибки с poll
  const flaky = fakeApi({ wake: [R(202, { state: "starting" })],
    workplace: [R(503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } }), R(0, null), R(200, { state: "ready" })] });
  const fv: PlaceView[] = [];
  const done = wakeAndWait({ api: flaky, onView: (v) => fv.push(v) });
  await vi.advanceTimersByTimeAsync(3_000);
  expect(await done).toBe("ready");
  expect(fv.map((v) => [v.text, v.poll])).toEqual([["Готовлю рабочее место…", true], ["Серверный слой не отвечает", true],
    ["Серверный слой не отвечает", true], ["Рабочее место готово", false]]);
});

test("wake_and_wait_aborted_stops_polling", async () => {
  vi.useFakeTimers();
  const api = fakeApi({ wake: [R(202, { state: "starting" })], workplace: [R(200, { state: "starting" })] });
  const ctl = new AbortController(); const onView = vi.fn();
  const done = wakeAndWait({ api, onView, signal: ctl.signal });
  await vi.advanceTimersByTimeAsync(2_500);
  ctl.abort();
  expect(await done).toBe("aborted");
  const n = api.calls.length; const v = onView.mock.calls.length;
  await vi.advanceTimersByTimeAsync(100_000);
  expect(api.calls).toHaveLength(n); expect(onView).toHaveBeenCalledTimes(v);     // ни опроса, ни «не ответило»
  const pre = new AbortController(); pre.abort();
  const idle = fakeApi({});
  expect(await wakeAndWait({ api: idle, onView, signal: pre.signal })).toBe("aborted");
  expect(idle.calls).toEqual([]);
});

test("abort_during_request_ignores_late_reply", async () => {
  // доводка 1: отмена, пока запрос места в пути, — поздний ответ не даёт ни вида, ни нового опроса
  vi.useFakeTimers();
  let answer!: (r: Reply) => void;
  const calls: string[] = [];
  const api: Api = {
    me: vi.fn(), logout: vi.fn(), ackNotice: vi.fn(), agents: vi.fn(), events: vi.fn(), calc: vi.fn(), report: vi.fn(),
    wake: async () => { calls.push("wake"); return R(202, { state: "starting" }); },
    workplace: () => { calls.push("workplace"); return new Promise<Reply>((r) => { answer = r; }); },
  };
  const ctl = new AbortController(); const onView = vi.fn();
  const done = wakeAndWait({ api, onView, signal: ctl.signal });
  await vi.advanceTimersByTimeAsync(1_000);                                       // первый опрос ушёл и висит
  expect(calls).toEqual(["wake", "workplace"]); expect(onView).toHaveBeenCalledTimes(1);
  ctl.abort();
  expect(await done).toBe("aborted");
  answer(R(200, { state: "starting" }));                                          // ответ пришёл после отмены
  await vi.advanceTimersByTimeAsync(100_000);
  expect(calls).toEqual(["wake", "workplace"]); expect(onView).toHaveBeenCalledTimes(1);

  let woke!: (r: Reply) => void;                                                  // то же для ответа wake
  const api2: Api = { me: vi.fn(), logout: vi.fn(), workplace: vi.fn(), ackNotice: vi.fn(), agents: vi.fn(), events: vi.fn(),
    calc: vi.fn(), report: vi.fn(),
    wake: () => new Promise<Reply>((r) => { woke = r; }) };
  const ctl2 = new AbortController(); const onView2 = vi.fn();
  const done2 = wakeAndWait({ api: api2, onView: onView2, signal: ctl2.signal });
  ctl2.abort();
  expect(await done2).toBe("aborted");
  woke(R(202, { state: "starting" }));
  await vi.advanceTimersByTimeAsync(100_000);
  expect(onView2).not.toHaveBeenCalled(); expect(api2.workplace).not.toHaveBeenCalled();
});

test("wake_and_wait_custom_timers", async () => {
  // таймеры внедряются (App в тестах А4 — без секундного ожидания): опрос через pollMs, срок — giveUpS
  const pending: { fn: () => void; ms: number }[] = [];
  const timers = { setTimeout: (fn: () => void, ms: number) => { pending.push({ fn, ms }); return pending.length; },
                   clearTimeout: vi.fn() };
  const api = fakeApi({ wake: [R(202, { state: "starting" })], workplace: [R(200, { state: "ready" })] });
  const done = wakeAndWait({ api, onView: vi.fn(), timers, pollMs: 5, giveUpS: 7 });
  await vi.waitFor(() => expect(pending.map((p) => p.ms)).toEqual([7_000, 5]));
  pending[1].fn();
  expect(await done).toBe("ready");
  expect(timers.clearTimeout).toHaveBeenCalled();
});
