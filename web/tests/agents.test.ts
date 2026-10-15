// @vitest-environment node
// Агенты места (Д7-R64, R65): договор ответа GET /pcbk/api/agents, агент разговора по истории, агент хода и
// расписание опроса списка (preflight П10: отдельный модуль на поддельных таймерах, без RTL).
import { afterEach, expect, test, vi } from "vitest";
import {
  AGENTS_AFTER_TURN_MS, AGENTS_PENDING_FOR_MS, AGENTS_PENDING_MS, DEFAULT_AGENT, createAgentsPoll, needsPoll,
  parseAgents, sendAgent, threadAgent, type AgentItem,
} from "../src/agents";

const user = (agent: unknown) => ({ info: { role: "user", agent } });

test("thread_agent_from_history", () => {
  const st = { messageOrder: ["m1", "m2"], messagesById: { m1: user("konstruktor"), m2: { info: { role: "assistant" } } } };
  expect(threadAgent(st as never)).toBe("konstruktor");
  expect(threadAgent(undefined)).toBeUndefined();
  expect(threadAgent({ messageOrder: [], messagesById: {} } as never)).toBeUndefined();
});

test("thread_agent_is_first_student_message", () => {
  // первое сообщение студента — по messageOrder, ответы агента и сообщения без info (ещё не пришли) не в счёт
  const st = {
    messageOrder: ["m0", "m1", "m2", "m3"],
    messagesById: { m0: { info: undefined }, m1: { info: { role: "assistant", agent: "pomoshnik" } },
                    m2: user("konstruktor"), m3: user("agent-x") },
  };
  expect(threadAgent(st as never)).toBe("konstruktor");
  // у первого сообщения студента агента нет — неизвестно, а не агент следующего
  for (const odd of [undefined, "", 7])
    expect(threadAgent({ messageOrder: ["a", "b"], messagesById: { a: user(odd), b: user("x") } } as never)).toBeUndefined();
});

test("send_agent_omits_default", () => {
  expect(DEFAULT_AGENT).toBe("pomoshnik");
  expect(sendAgent("pomoshnik")).toBeUndefined();
  expect(sendAgent("konstruktor")).toBe("konstruktor");
  expect(sendAgent(undefined)).toBeUndefined();
});

const item = (over: Partial<Record<keyof AgentItem, unknown>> = {}) =>
  ({ name: "x", title: "X", purpose: "p", kind: "own", state: "ready", ...over });

test("parse_agents_contract", () => {
  expect(parseAgents({ agents: [item()], own: 1, limit: 20 })).toHaveLength(1);
  for (const bad of [null, {}, { agents: [{ name: 1 }] }, { agents: [item({ state: "odd" })] }])
    expect(parseAgents(bad)).toBeNull();
});

test("parse_agents_contract_fields", () => {
  const body = {
    agents: [item({ name: "pomoshnik", title: "Помощник технолога КДМ-1", kind: "builtin", extra: 1 } as never),
             item({ name: "a1", state: "pending" }), item({ name: "a2", state: "broken" })],
    own: 2, limit: 20,
  };
  expect(parseAgents(body)).toEqual([                                            // лишние поля не переносятся
    { name: "pomoshnik", title: "Помощник технолога КДМ-1", purpose: "p", kind: "builtin", state: "ready" },
    { name: "a1", title: "X", purpose: "p", kind: "own", state: "pending" },
    { name: "a2", title: "X", purpose: "p", kind: "own", state: "broken" },
  ]);
  expect(parseAgents({ agents: [], own: 0, limit: 20 })).toEqual([]);
  for (const bad of [
    "<html>502</html>", [], { agents: {}, own: 0, limit: 20 },
    { agents: [item()] }, { agents: [item()], own: "1", limit: 20 }, { agents: [item()], own: 1, limit: -1 },
    { agents: [item({ name: "" })], own: 1, limit: 20 }, { agents: [item({ title: null })], own: 1, limit: 20 },
    { agents: [item({ purpose: 3 })], own: 1, limit: 20 }, { agents: [item({ kind: "image" })], own: 1, limit: 20 },
    { agents: [null], own: 1, limit: 20 },
    // случаи брифа — и с own и limit по договору: отказ из-за элемента, а не из-за верхнего уровня
    { agents: [{ name: 1 }], own: 1, limit: 20 }, { agents: [item({ state: "odd" })], own: 1, limit: 20 },
    { agents: [item({ state: "archived" })], own: 1, limit: 20 }, { agents: [item({ name: undefined })], own: 1, limit: 20 },
  ]) expect(parseAgents(bad), JSON.stringify(bad)).toBeNull();
});

test("needs_poll_only_while_pending", () => {
  expect(needsPoll([item() as AgentItem, item({ state: "broken" }) as AgentItem])).toBe(false);
  expect(needsPoll([item() as AgentItem, item({ state: "pending" }) as AgentItem])).toBe(true);
  expect(needsPoll([])).toBe(false);
});

// --- расписание опроса (R65): при монтировании; через 1 с после конца хода; пока есть pending — раз в 3 с, не дольше
// 60 с от последнего конца хода. До первого конца хода опроса нет, якорь — на каждый запуск свой (m16)

const BUILTIN = [item({ name: "pomoshnik", kind: "builtin" }), item({ name: "konstruktor", kind: "builtin" })] as AgentItem[];
const PENDING = item({ name: "a1", state: "pending" }) as AgentItem;

afterEach(() => { vi.useRealTimers(); });

// опрос на поддельных таймерах; answer — что отдаёт очередная загрузка (по номеру вызова с 1)
function fakePoll(answer: (n: number) => AgentItem[] | null | Promise<AgentItem[] | null>) {
  vi.useFakeTimers();
  const seen: (AgentItem[] | null)[] = [];
  let calls = 0;
  const poll = createAgentsPoll({
    load: async () => { calls += 1; return answer(calls); },
    onItems: (items) => { seen.push(items); },
    now: () => Date.now(),
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  });
  return { poll, seen, calls: () => calls };
}

test("agents_refresh_after_turn_and_while_pending", async () => {
  expect([AGENTS_AFTER_TURN_MS, AGENTS_PENDING_MS, AGENTS_PENDING_FOR_MS]).toEqual([1000, 3000, 60000]);
  const p = fakePoll(() => [...BUILTIN, PENDING]);
  p.poll.start();                                                              // монтирование
  await vi.advanceTimersByTimeAsync(0); expect(p.calls()).toBe(1);
  await vi.advanceTimersByTimeAsync(30_000); expect(p.calls()).toBe(1);        // pending, но хода ещё не было (m16)
  p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(999); expect(p.calls()).toBe(1);
  await vi.advanceTimersByTimeAsync(1); expect(p.calls()).toBe(2);             // через 1 с после конца хода
  await vi.advanceTimersByTimeAsync(3000); expect(p.calls()).toBe(3);          // pending — раз в 3 с
  await vi.advanceTimersByTimeAsync(60000); const n = p.calls();
  expect(n).toBe(21);                                                          // +1, +4, … +58 с: последняя ≤ 60 с
  await vi.advanceTimersByTimeAsync(9000); expect(p.calls()).toBe(n);          // не дольше 60 с от конца хода
  expect(p.seen).toHaveLength(n);
  p.poll.stop();
});

test("agents_poll_once_after_turn_without_pending", async () => {
  const p = fakePoll(() => BUILTIN);
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(20_000);
  expect(p.calls()).toBe(2);                                                   // монтирование и +1 с — всё
  p.poll.stop();
});

test("agents_poll_stops_when_pending_gone", async () => {
  const p = fakePoll((n) => n <= 3 ? [...BUILTIN, PENDING] : [...BUILTIN, { ...PENDING, state: "ready" }]);
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(p.calls()).toBe(4);                                                   // 0, +1, +4, +7 — «готов», дальше тихо
  expect(p.seen.at(-1)!.map((a) => a.state)).toEqual(["ready", "ready", "ready"]);
  p.poll.stop();
});

test("agents_poll_window_from_last_turn_end", async () => {
  const p = fakePoll(() => [...BUILTIN, PENDING]);
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(50_000);
  p.poll.turnEnded();                                                          // новый ход кончился — окно заново
  const before = p.calls();
  await vi.advanceTimersByTimeAsync(55_000);
  expect(p.calls()).toBeGreaterThan(before + 15);                              // опрос идёт и после +60 с от первого
  await vi.advanceTimersByTimeAsync(10_000); const n = p.calls();
  await vi.advanceTimersByTimeAsync(10_000); expect(p.calls()).toBe(n);
  p.poll.stop();
});

test("agents_poll_stop_cancels_timer_and_late_answer", async () => {
  let release: (v: AgentItem[]) => void = () => {};
  const p = fakePoll((n) => n === 2 ? new Promise((r) => { release = r; }) : [...BUILTIN, PENDING]);
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(1000); expect(p.calls()).toBe(2);         // вторая загрузка в пути
  p.poll.stop();                                                              // размонтирование или остановка обёртки
  release([...BUILTIN, PENDING]);
  await vi.advanceTimersByTimeAsync(20_000);
  expect(p.calls()).toBe(2);
  expect(p.seen).toHaveLength(1);                                             // поздний ответ не применяется
  p.poll.turnEnded();                                                         // остановленный опрос не оживает
  await vi.advanceTimersByTimeAsync(5_000); expect(p.calls()).toBe(2);
});

test("agents_poll_keeps_newest_answer", async () => {
  // ответы пришли не по порядку: старый после нового не затирает его
  const answers: ((v: AgentItem[] | null) => void)[] = [];
  const p = fakePoll(() => new Promise((r) => { answers.push(r); }));
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(1000); expect(p.calls()).toBe(2);
  answers[1]!([...BUILTIN, PENDING]);                                         // новый — первым
  await vi.advanceTimersByTimeAsync(0);
  answers[0]!(BUILTIN);                                                       // старый — следом
  await vi.advanceTimersByTimeAsync(0);
  expect(p.seen).toEqual([[...BUILTIN, PENDING]]);
  await vi.advanceTimersByTimeAsync(3000); expect(p.calls()).toBe(3);         // и опрос решает по новому
  p.poll.stop();
});

test("agents_poll_turn_end_while_loading_no_double_timer", async () => {
  // конец хода, пока загрузка в пути: таймер один — +1 с, а ответ загрузки второго не ставит
  let release: (v: AgentItem[]) => void = () => {};
  const p = fakePoll((n) => n === 2 ? new Promise((r) => { release = r; }) : [...BUILTIN, PENDING]);
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(1000); expect(p.calls()).toBe(2);
  p.poll.turnEnded();                                                         // ещё ход кончился, загрузка №2 в пути
  release([...BUILTIN, PENDING]);
  await vi.advanceTimersByTimeAsync(1000); expect(p.calls()).toBe(3);         // +1 с от нового конца хода
  await vi.advanceTimersByTimeAsync(2999); expect(p.calls()).toBe(3);
  await vi.advanceTimersByTimeAsync(1); expect(p.calls()).toBe(4);
  p.poll.stop();
});

test("agents_poll_failure_keeps_last_pending", async () => {
  // сбой ручки посреди ожидания: строка «недоступен» (null), а опрос идёт по последнему ответу 200 — агент появится
  const p = fakePoll((n) => n === 3 ? null : n === 4 ? [...BUILTIN, { ...PENDING, state: "ready" }] : [...BUILTIN, PENDING]);
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(p.calls()).toBe(4);
  expect(p.seen.map((s) => s === null ? null : s.length)).toEqual([3, 3, null, 3]);
  p.poll.stop();
});

test("agents_poll_failure_from_start_does_not_poll", async () => {
  // старый core (404) или load бросил: строка, а опрос — только по расписанию без pending
  const p = fakePoll((n) => { if (n === 1) throw new Error("сеть"); return null; });
  p.poll.start(); p.poll.turnEnded();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(p.calls()).toBe(2);
  expect(p.seen).toEqual([null, null]);
  p.poll.stop();
});
