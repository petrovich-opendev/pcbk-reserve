// @vitest-environment node
// Чистые помощники вкладки «Калькуляторы» (Д9-R88): запрос по форме, числа ru-RU, ломаные профиля, время историана;
// ручка api.calc — POST /pcbk/api/calc/<tool> (маршрут шлюза Д9-R87).
import { expect, test } from "vitest";
import { createApi, type JsonFetch } from "../src/api";
import { badRange, buildRequest, fmtNum, fmtTime, MODE_CHOICES, PERIOD_CHOICES, POINT_CHOICES, profileSegments,
         type CalcForm } from "../src/calc";

const F: CalcForm = { mode: "period", tag: "20FAKE_001_PV", period: "prev_shift", start: "", end: "", points: 24 };

test("build_request_by_mode", () => {
  expect(buildRequest(F)).toEqual({ tool: "tag_period", args: { tags: ["20FAKE_001_PV"], period: "prev_shift" } });
  expect(buildRequest({ ...F, mode: "now", tag: " 20FAKE_001_PV " }))
    .toEqual({ tool: "tag_now", args: { tags: ["20FAKE_001_PV"] } });
  expect(buildRequest({ ...F, mode: "profile", period: "custom", start: "2026-10-01T08:00", end: "2026-10-01T12:00", points: 48 }))
    .toEqual({ tool: "tag_series", args: { tags: ["20FAKE_001_PV"], start: "2026-10-01T08:00", end: "2026-10-01T12:00", points: 48 } });
  expect(buildRequest({ ...F, mode: "runtime", tag: "25FAKE_007_CLS", period: "yesterday" }))
    .toEqual({ tool: "calc_runtime", args: { tags: ["25FAKE_007_CLS"], period: "yesterday" } });
  expect(buildRequest({ ...F, tag: "  " })).toBeNull();
  expect(buildRequest({ ...F, period: "custom", start: "2026-10-01T08:00", end: "" })).toBeNull();
});

test("build_request_now_ignores_period", () => {
  // у текущего значения периода нет: незаполненный свой период запрос не держит
  expect(buildRequest({ ...F, mode: "now", period: "custom", start: "", end: "" }))
    .toEqual({ tool: "tag_now", args: { tags: ["20FAKE_001_PV"] } });
});

test("fmt_num_ru", () => {                                                       // в «1 250,457» — U+00A0, как у Intl ru-RU
  expect([fmtNum(12.4), fmtNum(1250.4567), fmtNum(-3), fmtNum(0), fmtNum(null), fmtNum("12")])
    .toEqual(["12,4", "1 250,457", "-3", "0", "—", "—"]);
  expect([fmtNum(Number.NaN), fmtNum(Number.POSITIVE_INFINITY), fmtNum(undefined), fmtNum(-0)])
    .toEqual(["—", "—", "—", "0"]);                                              // «-0» не пишется
});

test("fmt_num_small_nonzero_never_zero", () => {                                 // доводка р1: решение контроллера по m-6
  // ненулевое число не показывается как «0» (нет ложного числа): округление до 3 знаков дало бы ноль — 3 значащие цифры
  expect([fmtNum(0.0004), fmtNum(0.000123), fmtNum(0.00012345), fmtNum(-0.0004), fmtNum(1e-9)])
    .toEqual(["0,0004", "0,000123", "0,000123", "-0,0004", "0,000000001"]);
  expect([fmtNum(0.0005), fmtNum(0.0012345), fmtNum(-0.0012), fmtNum(12.0004)])   // округление не до нуля — по-прежнему 3 знака
    .toEqual(["0,001", "0,001", "-0,001", "12"]);
});

test("profile_segments_break_on_null", () => {
  expect(profileSegments([1, 2, null, 4], 300, 100)).toEqual(["0,100 100,66.7", "300,0"]);
  expect(profileSegments([5, 5], 300, 100)).toEqual(["0,50 300,50"]);               // ровный ряд — посередине
  expect(profileSegments([null, null], 300, 100)).toEqual([]);
});

test("fmt_time_keeps_historian_clock", () => {
  // время — как у историана (без пересчёта в пояс браузера); пояс пишется отдельно строкой периода
  expect(fmtTime("2026-09-30T22:00:00+05:00")).toBe("30.09.2026 22:00");
  expect(fmtTime("2026-10-01T11:59:40+05:00")).toBe("01.10.2026 11:59:40");
  expect(fmtTime("непонятно")).toBe("непонятно");
  expect([fmtTime(null), fmtTime(5)]).toEqual(["—", "—"]);
});

test("choices", () => {
  expect(MODE_CHOICES.map((c) => [c.mode, c.tool])).toEqual(
    [["now", "tag_now"], ["period", "tag_period"], ["profile", "tag_series"], ["runtime", "calc_runtime"]]);
  expect(PERIOD_CHOICES.map((c) => c.key)).toEqual(["last_hour", "last_8h", "last_24h", "current_shift", "prev_shift",
    "today", "yesterday", "last_7d", "last_30d", "custom"]);
  expect(POINT_CHOICES).toEqual([12, 24, 48, 96]);
});

test("api_calc_posts_args_to_gateway_route", async () => {                      // Д9-R87: POST /pcbk/api/calc/<tool>
  const seen: { path: string; init: RequestInit }[] = [];
  const fetchImpl: JsonFetch = (path, init) => {
    seen.push({ path, init });
    return Promise.resolve(new Response('{"tool":"tag_series","status":"ok"}', { status: 200 }));
  };
  const r = await createApi(fetchImpl).calc("tag_series", { tags: ["20FAKE_001_PV"], period: "last_8h", points: 24 });
  expect(r).toEqual({ status: 200, body: { tool: "tag_series", status: "ok" } });
  expect(seen.map((s) => `${s.init.method} ${s.path}`)).toEqual(["POST /pcbk/api/calc/tag_series"]);
  expect(seen[0]!.init.credentials).toBe("same-origin");
  expect(seen[0]!.init.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
  expect(seen[0]!.init.body).toBe('{"tags":["20FAKE_001_PV"],"period":"last_8h","points":24}');
});

test("build_request_refuses_backward_custom_range", () => {                      // ревью А5, m-4: частота — не на отказ
  const custom = { ...F, period: "custom" };
  expect(buildRequest({ ...custom, start: "2026-10-01T12:00", end: "2026-10-01T08:00" })).toBeNull();
  expect(buildRequest({ ...custom, start: "2026-10-01T08:00", end: "2026-10-01T08:00" })).toBeNull();
  expect(buildRequest({ ...custom, start: "2026-10-01T08:00", end: "2026-10-01T08:00:30" })?.args)
    .toEqual({ tags: ["20FAKE_001_PV"], start: "2026-10-01T08:00", end: "2026-10-01T08:00:30" });
  expect(badRange({ ...custom, start: "2026-10-01T08:00", end: "" })).toBe(false);    // пустое — «укажите», не «позже»
});
