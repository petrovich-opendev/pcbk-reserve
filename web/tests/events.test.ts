// @vitest-environment node
// Вкладка «События» (Д13): договор ответа GET /pcbk/api/events (parseEvents) и форматирование времени записи
// местным временем браузера (fmtLocal) — в отличие от fmtTime «Калькуляторов», которое не пересчитывает пояс.
import { expect, test } from "vitest";
import { fmtLocal, parseEvents, type EventItem } from "../src/events";

const ev = (over: Partial<Record<string, unknown>> = {}) => ({
  id: 1, ts: "2026-10-12T10:15:30+00:00", agent: "napusk-03", agent_title: "Напуск: напорные ящики",
  kind: "deviation", title: "Давление в НЯ ОС выше задания", tags: ["21FAKE_06_PV", "21FAKE_06_SP"],
  period_start: "2026-10-12T06:00:00+05:00", period_end: "2026-10-12T06:40:00+05:00",
  value: 1.05, setpoint: 0.94, text: "Факт превысил задание на 0,11 бар дольше 10 минут.",
  ...over,
});

test("parse_events_contract", () => {
  expect(parseEvents({ events: [ev()], limit: 100 })).toEqual([{
    id: 1, ts: "2026-10-12T10:15:30+00:00", agent: "napusk-03", agentTitle: "Напуск: напорные ящики",
    kind: "deviation", title: "Давление в НЯ ОС выше задания", tags: ["21FAKE_06_PV", "21FAKE_06_SP"],
    periodStart: "2026-10-12T06:00:00+05:00", periodEnd: "2026-10-12T06:40:00+05:00",
    value: 1.05, setpoint: 0.94, text: "Факт превысил задание на 0,11 бар дольше 10 минут.",
  }] satisfies EventItem[]);
  expect(parseEvents({ events: [], limit: 100 })).toEqual([]);
  for (const bad of [null, {}, "<html>502</html>", [], { events: {}, limit: 100 }, { events: [ev()] },
                     { events: [ev()], limit: "100" }, { events: [ev()], limit: -1 }])
    expect(parseEvents(bad), JSON.stringify(bad)).toBeNull();
});

test("parse_events_field_checks", () => {
  for (const bad of [
    ev({ id: "1" }), ev({ id: -1 }), ev({ id: 1.5 }), ev({ ts: "" }), ev({ ts: 7 }),
    ev({ agent: "" }), ev({ agent_title: "" }), ev({ agent_title: undefined }),
    ev({ kind: "odd" }), ev({ kind: "" }), ev({ title: "" }), ev({ text: "" }), ev({ text: 5 }),
    ev({ tags: "x" }), ev({ tags: [1] }), ev({ tags: [""] }),
  ]) expect(parseEvents({ events: [bad], limit: 100 }), JSON.stringify(bad)).toBeNull();
  // необязательные поля — отсутствие и null — то же, что null; лишние поля не переносятся
  const extra = ev({ extra: "x" } as never);
  expect(parseEvents({ events: [extra], limit: 100 })![0]).not.toHaveProperty("extra");
  const missingOptional = { id: 2, ts: "t", agent: "a", agent_title: "A", kind: "note", title: "T", tags: [], text: "текст" };
  expect(parseEvents({ events: [missingOptional], limit: 100 })).toEqual([{
    id: 2, ts: "t", agent: "a", agentTitle: "A", kind: "note", title: "T", tags: [], text: "текст",
    periodStart: null, periodEnd: null, value: null, setpoint: null,
  }]);
  expect(parseEvents({ events: [ev({ period_start: null, period_end: null, value: null, setpoint: null })], limit: 100 })![0])
    .toMatchObject({ periodStart: null, periodEnd: null, value: null, setpoint: null });
  // значения не-число (строка, NaN) — как отсутствие (null), не отказ: ответ службы — число или null по договору,
  // но парсер не отказывает весь список из-за чужого значения
  expect(parseEvents({ events: [ev({ value: "1.05" })], limit: 100 })![0]!.value).toBeNull();
});

test("fmt_local_is_browser_time_not_historian_zone", () => {
  // окружение тестов — UTC (TZ не задан): 10:15 UTC остаётся 10:15 местного
  expect(fmtLocal("2026-10-12T10:15:30+00:00")).toBe("12.10.2026, 10:15");
  expect(fmtLocal("2026-10-12T10:15:00Z")).toBe("12.10.2026, 10:15");
  // в отличие от fmtTime «Калькуляторов», смещение пояса в строке не игнорируется — время пересчитывается
  expect(fmtLocal("2026-10-12T15:15:00+05:00")).toBe("12.10.2026, 10:15");
  for (const bad of [null, undefined, 7, "", "не время"]) expect(fmtLocal(bad)).toBe("—");
});
