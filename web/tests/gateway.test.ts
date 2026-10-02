// @vitest-environment node
// Клиент шлюза (Д6-R43): классы отказов, паузы потока /event, остановка после 401 и «место спит»,
// повтор 503 WorkplaceBusy с паузой. Чистый TypeScript — fetch, часы и паузы внедрены.
// Среда node: Request и AbortController — одной реализации (в jsdom сигнал jsdom Request Node не примет).
import { expect, test, vi } from "vitest";
import {
  classify, createGatewayFetch, eventDelayMs, explainError, HaltedError, SHORT_STREAM_S, streamLine, type StreamState,
} from "../src/gateway";
import { T_BUSY, T_OFFLINE, T_STREAM_RETRY, T_TOO_MANY_TABS, T_UNKNOWN } from "../src/texts";

const sdkErr = (status: number, name: string, message = "текст шлюза") =>
  new Error(message, { cause: { status, body: { name, data: { message } } } });
const json = (status: number, name?: string) => new Response(name ? JSON.stringify({ name, data: { message: "т" } }) : "",
  { status, headers: { "content-type": "application/json" } });
const ev = () => new Request("https://s/pcbk/api/oc/event");
const ses = () => new Request("https://s/pcbk/api/oc/session");

test("classify_table", () => {
  const T: [number, string | undefined, string][] = [[200, undefined, "ok"], [401, "Unauthorized", "signed_out"],
    [503, "WorkplaceNotReady", "asleep"], [429, "TooManyRequests", "too_many_tabs"], [503, "WorkplaceBusy", "busy"],
    [503, "Unavailable", "down"], [502, "BadGateway", "refused"], [403, "Forbidden", "refused"],
    [400, "BadRequest", "refused"], [429, "TooManyAttempts", "refused"], [404, "NotFound", "refused"],
    [413, "PayloadTooLarge", "refused"], [500, undefined, "unknown"]];
  for (const [s, n, k] of T) expect(classify(s, n ? { name: n, data: { message: "т" } } : "<html>"), `${s} ${n}`).toBe(k);
  expect(classify(401, "<html>")).toBe("signed_out");                                    // 401 — с любым телом
  expect(classify(503, { name: "Другое", data: { message: "т" } })).toBe("unknown");     // незнакомое имя 503
  expect(classify(403, { data: { message: "т" } })).toBe("unknown");                     // не вида OpenCode
});

test("classify_other_5xx_is_unknown", () => {
  // 5xx, кроме 502 и 503, — и с JSON вида OpenCode (UnknownError самого OpenCode) — не «отказ» с текстом (доводка 1)
  for (const s of [500, 501, 504]) expect(classify(s, { name: "UnknownError", data: { message: "т" } }), String(s)).toBe("unknown");
  expect(classify(502, { name: "BadGateway", data: { message: "т" } })).toBe("refused");
  expect(classify(499, { name: "X", data: { message: "т" } })).toBe("refused");
});

test("explain_error_texts", () => {
  expect(explainError(sdkErr(403, "Forbidden", "Вызов не разрешён"))).toEqual({ kind: "refused", text: "Вызов не разрешён" });
  expect(explainError(new TypeError("Failed to fetch")).kind).toBe("offline");
  expect(explainError(new Error("opencode server GET /x: network error (no response)")).kind).toBe("offline");
  expect(explainError(new Error("Request is not supported by this version of OpenCode Server"))).toEqual({ kind: "unknown", text: T_UNKNOWN });
  expect(explainError(new HaltedError()).kind).toBe("halted");
});

test("explain_error_other_kinds", () => {
  expect(explainError(new HaltedError())).toEqual({ kind: "halted", text: "" });
  expect(explainError(new DOMException("aborted", "AbortError"))).toEqual({ kind: "halted", text: "" }); // своя отмена
  expect(explainError(new TypeError("Failed to fetch")).text).toBe(T_OFFLINE);
  expect(explainError(sdkErr(503, "WorkplaceBusy", "Слишком много — позже"))).toEqual({ kind: "busy", text: "Слишком много — позже" });
  expect(explainError(sdkErr(429, "TooManyRequests", ""))).toEqual({ kind: "too_many_tabs", text: T_TOO_MANY_TABS });
  expect(explainError(sdkErr(503, "Unavailable")).kind).toBe("down");
  expect(explainError(sdkErr(503, "WorkplaceNotReady")).kind).toBe("asleep");
  expect(explainError(sdkErr(401, "Unauthorized")).kind).toBe("signed_out");
  expect(explainError(new Error("x", { cause: { status: 500, body: "<html>" } }))).toEqual({ kind: "unknown", text: T_UNKNOWN });
  expect(explainError("строка")).toEqual({ kind: "unknown", text: T_UNKNOWN });
});

test("event_delay_formula", () => {
  expect(eventDelayMs(0, () => 0.99)).toBe(0);
  expect(eventDelayMs(1, () => 0)).toBe(800);                                            // 1000 × 0,8
  expect(eventDelayMs(1, () => 1)).toBe(1200);                                           // 1000 × 1,2
  expect(eventDelayMs(40, () => 1)).toBe(36000);                                         // предел 30 000 × 1,2
});

test("event_backoff_grows_and_resets", async () => {
  const waits: number[] = []; const codes = [503, 503, 503, 503, 503, 503, 503, 200, 503];
  let t = 0;
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), random: () => 0.5, now: () => t,
    sleep: async (ms) => { waits.push(ms); }, fetchImpl: async () => json(codes.shift()!, "WorkplaceBusy") });
  for (let i = 0; i < 9; i++) { const r = await f(ev()); if (r.ok) t += 60_000; }   // 200 живёт минуту
  expect(waits).toEqual([0, 1000, 2000, 4000, 8000, 16000, 30000, 30000, 0]);   // долгий 200 сбросил счёт
});

test("short_stream_counts_as_failure", async () => {                     // раунд 2, Minor 4
  const waits: number[] = []; let t = 0;
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), random: () => 0.5, now: () => t,
    sleep: async (ms) => { waits.push(ms); }, fetchImpl: async () => new Response("data: {}\n\n", { status: 200 }) });
  await f(ev()); t += 1_000;                                                  // следующий запрос через 1 с после 200
  await f(ev()); t += 1_500;                                                  // и ещё раз — поток снова короткий
  await f(ev()); t += 60_000;                                                 // этот прожил минуту
  await f(ev());
  expect(waits).toEqual([0, 1000, 2000, 0]);                                   // конец или обрыв — всё равно сбой; долгий — сброс
});

test("short_stream_boundary_is_inclusive", async () => {
  // ровно SHORT_STREAM_S после 200 — поток живой (>=), на миллисекунду раньше — сбой (доводка 1)
  const waits: number[] = []; let t = 0;
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), random: () => 0.5, now: () => t,
    sleep: async (ms) => { waits.push(ms); }, fetchImpl: async () => new Response("data: {}\n\n", { status: 200 }) });
  await f(ev()); t += SHORT_STREAM_S * 1000;
  await f(ev()); t += SHORT_STREAM_S * 1000 - 1;
  await f(ev());
  expect(waits).toEqual([0, 0, 1000]);
});

test("halt_stops_stream_and_rejects_calls", async () => {
  const fetchImpl = vi.fn(async () => json(503, "WorkplaceNotReady")); const onHalt = vi.fn();
  const f = createGatewayFetch({ onHalt, onStream: vi.fn(), fetchImpl, sleep: async () => {} });
  await f(ev());
  expect(onHalt).toHaveBeenCalledWith("asleep");
  const ctl = new AbortController(); const pending = f(new Request("https://s/pcbk/api/oc/event", { signal: ctl.signal }));
  await expect(f(new Request("https://s/pcbk/api/oc/session"))).rejects.toBeInstanceOf(HaltedError);
  ctl.abort(); await expect(pending).rejects.toThrow();
  expect(fetchImpl).toHaveBeenCalledTimes(1); expect(onHalt).toHaveBeenCalledTimes(1);
});

test("signed_out_on_any_call_halts_once", async () => {
  const fetchImpl = vi.fn(async () => json(401, "Unauthorized")); const onHalt = vi.fn(); const sleep = vi.fn(async () => {});
  const f = createGatewayFetch({ onHalt, onStream: vi.fn(), fetchImpl, sleep });
  const [first, second] = await Promise.all([f(ses()), f(ses())]);              // оба в пути до остановки
  expect([first.status, second.status]).toEqual([401, 401]);                     // остановивший вызов получает свой ответ (M8)
  expect(onHalt).toHaveBeenCalledWith("signed_out");
  await expect(f(ses())).rejects.toBeInstanceOf(HaltedError);
  await expect(f(new Request("https://s/pcbk/api/oc/session/ses_1/prompt_async", { method: "POST", body: "{}" })))
    .rejects.toBeInstanceOf(HaltedError);
  const ctl = new AbortController(); let settled = false;
  const pending = f(new Request("https://s/pcbk/api/oc/event", { signal: ctl.signal })).finally(() => { settled = true; });
  await new Promise((r) => setTimeout(r, 20));
  expect(settled).toBe(false);                                                   // поток ждёт отмены, в сеть не идёт
  ctl.abort(); await expect(pending).rejects.toThrow();
  expect(fetchImpl).toHaveBeenCalledTimes(2); expect(onHalt).toHaveBeenCalledTimes(1);
  expect(sleep).not.toHaveBeenCalled();
});

test("halt_during_stream_pause_goes_quiet", async () => {
  // поток ждёт паузу после сбоя, а в это время вызов получил 401: после паузы поток в сеть не идёт
  let release!: () => void;
  const paused = new Promise<void>((r) => { release = r; });
  const fetchImpl = vi.fn(async (r: Request) => new URL(r.url).pathname.endsWith("/event") ? json(503, "WorkplaceBusy") : json(401, "Unauthorized"));
  const sleeps: number[] = [];
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), fetchImpl, random: () => 0.5,
    sleep: async (ms) => { sleeps.push(ms); if (ms > 0) await paused; } });
  await f(ev());                                                                 // 503 busy — пауза перед следующим
  const ctl = new AbortController();
  const pending = f(new Request("https://s/pcbk/api/oc/event", { signal: ctl.signal }));
  await f(ses());                                                                // 401 — остановка
  release();
  await new Promise((r) => setTimeout(r, 20));
  expect(fetchImpl).toHaveBeenCalledTimes(2);                                    // поток после паузы не ушёл в сеть
  ctl.abort(); await expect(pending).rejects.toThrow();
  expect(sleeps).toEqual([0, 1000]);
});

test("plain_calls_are_not_delayed", async () => {
  const waits: number[] = [];
  const fetchImpl = vi.fn(async (r: Request) => new URL(r.url).pathname.endsWith("/event") ? json(503, "Unavailable") : json(200));
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), fetchImpl, random: () => 0.5,
    sleep: async (ms) => { waits.push(ms); } });
  for (let i = 0; i < 3; i++) await f(ev());
  expect(waits).toEqual([0, 1000, 2000]);
  const r = await f(ses());
  expect(r.status).toBe(200);
  expect(waits).toEqual([0, 1000, 2000]);                                        // GET /session ушёл без паузы
  expect(fetchImpl).toHaveBeenCalledTimes(4);
});

test("busy_call_retried_with_pause", async () => {
  // 503 WorkplaceBusy (limit_conn edge, очередь места) — до места запрос не дошёл: повтор с паузой и тем же телом
  const codes = [503, 503, 200]; const bodies: string[] = []; const waits: number[] = [];
  const fetchImpl = vi.fn(async (r: Request) => { bodies.push(await r.text()); return json(codes.shift()!, "WorkplaceBusy"); });
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), fetchImpl, random: () => 0.5,
    sleep: async (ms) => { waits.push(ms); } });
  const body = JSON.stringify({ parts: [{ type: "text", text: "вопрос" }] });
  const r = await f(new Request("https://s/pcbk/api/oc/session/ses_1/prompt_async", { method: "POST", body }));
  expect(r.status).toBe(200);
  expect(waits).toEqual([1000, 2000]);
  expect(bodies).toEqual([body, body, body]);
});

test("busy_call_gives_up_after_retries", async () => {
  const waits: number[] = [];
  const fetchImpl = vi.fn(async () => json(503, "WorkplaceBusy"));
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), fetchImpl, random: () => 0.5,
    sleep: async (ms) => { waits.push(ms); } });
  const r = await f(ses());
  expect(r.status).toBe(503);                                                    // SDK получит отказ — текст шлюза
  expect(await r.json()).toEqual({ name: "WorkplaceBusy", data: { message: "т" } }); // тело не съедено
  expect(fetchImpl).toHaveBeenCalledTimes(4);                                    // 1 + 3 повтора, не шторм
  expect(waits).toEqual([1000, 2000, 4000]);
});

test("halt_during_busy_pause_rejects_retry", async () => {
  // вызов ждёт паузу повтора busy, а другой вызов получил 401: повтор в сеть не уходит (доводка 1)
  let release!: () => void;
  const paused = new Promise<void>((r) => { release = r; });
  const fetchImpl = vi.fn(async (r: Request) => new URL(r.url).pathname.endsWith("/busy") ? json(503, "WorkplaceBusy") : json(401, "Unauthorized"));
  const onHalt = vi.fn();
  const f = createGatewayFetch({ onHalt, onStream: vi.fn(), fetchImpl, random: () => 0.5, sleep: async () => { await paused; } });
  const busy = f(new Request("https://s/pcbk/api/oc/busy"));
  await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));             // первый ответ busy — ждёт паузу
  expect((await f(ses())).status).toBe(401);
  release();
  await expect(busy).rejects.toBeInstanceOf(HaltedError);
  expect(fetchImpl).toHaveBeenCalledTimes(2); expect(onHalt).toHaveBeenCalledTimes(1);
});

test("default_sleep_waits_and_abort_interrupts", async () => {
  // пауза по умолчанию (без внедрённого sleep): идёт своим таймером, отмена сигналом её прерывает
  vi.useFakeTimers();
  try {
    const fetchImpl = vi.fn(async () => json(503, "WorkplaceBusy"));
    const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), fetchImpl, random: () => 0.5 });
    await f(ev());                                                                  // сбой — следующая пауза 1000 мс
    const ctl = new AbortController();
    const cut = f(new Request("https://s/pcbk/api/oc/event", { signal: ctl.signal }));
    const cutDone = expect(cut).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(999);
    ctl.abort(); await cutDone;
    await vi.advanceTimersByTimeAsync(5_000);
    expect(fetchImpl).toHaveBeenCalledTimes(1);                                     // прерванная пауза в сеть не ушла
    const next = f(ev());                                                           // сбой не добавился: снова 1000 мс
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect((await next).status).toBe(503); expect(fetchImpl).toHaveBeenCalledTimes(2);
    const busyCtl = new AbortController();                                          // и пауза повтора busy обычного вызова
    const call = f(new Request("https://s/pcbk/api/oc/session", { signal: busyCtl.signal }));
    const callDone = expect(call).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(500);
    busyCtl.abort(); await callDone;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  } finally {
    vi.useRealTimers();
  }
});

test("other_refusals_not_retried", async () => {
  for (const [code, name] of [[503, "Unavailable"], [403, "Forbidden"], [429, "TooManyRequests"], [500, undefined]] as const) {
    const fetchImpl = vi.fn(async () => json(code, name)); const sleep = vi.fn(async () => {});
    const f = createGatewayFetch({ onHalt: vi.fn(), onStream: vi.fn(), fetchImpl, sleep });
    expect((await f(ses())).status).toBe(code);
    expect(fetchImpl).toHaveBeenCalledTimes(1); expect(sleep).not.toHaveBeenCalled();
  }
});

test("non_json_refusal_is_unknown", async () => {
  // 503 без content-type application/json (HTML прокси) — не «спит» и не «занято»: без остановки и повтора
  // тело — полноценный отказ вида OpenCode: разбор при любом content-type дал бы «спит» и остановку (доводка 1)
  const fetchImpl = vi.fn(async () => new Response('{"name":"WorkplaceNotReady","data":{"message":"т"}}',
    { status: 503, headers: { "content-type": "text/html" } }));
  const onHalt = vi.fn(); const states: StreamState[] = [];
  const f = createGatewayFetch({ onHalt, onStream: (s) => states.push(s), fetchImpl, random: () => 0.5, sleep: async () => {} });
  await f(ses()); await f(ev());
  expect(onHalt).not.toHaveBeenCalled(); expect(fetchImpl).toHaveBeenCalledTimes(2);
  expect(states.at(-1)).toEqual({ failures: 1, nextRetryS: 1, last: "unknown" });
});

test("stream_state_reported", async () => {
  const states: StreamState[] = []; let t = 0;
  const codes: [number, string?][] = [[429, "TooManyRequests"], [503, "WorkplaceBusy"], [200]];
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: (s) => states.push({ ...s }), random: () => 0.5, now: () => t,
    sleep: async () => {}, fetchImpl: async () => { const [c, n] = codes.shift()!; return json(c, n); } });
  await f(ev()); await f(ev()); await f(ev());
  t += 2_000;                                                                     // поток прожил 2 с — короткий
  codes.push([200]); await f(ev());
  expect(states).toEqual([
    { failures: 1, nextRetryS: 1, last: "too_many_tabs" },
    { failures: 2, nextRetryS: 2, last: "busy" },
    { failures: 2, nextRetryS: null, last: "ok" },                               // 200 гасит строку, счёт не сброшен
    { failures: 3, nextRetryS: 4, last: "ok" },                                  // короткий поток — сбой, пауза 4 с
    { failures: 3, nextRetryS: null, last: "ok" },
  ]);
});

test("stream_network_error_counts_abort_does_not", async () => {
  const waits: number[] = []; const states: StreamState[] = [];
  let mode: "net" | "ok" = "net";
  const fetchImpl = vi.fn(async (r: Request) => {
    if (mode === "net") throw new TypeError("Failed to fetch");
    return new Promise<Response>((_, reject) => r.signal.addEventListener("abort", () => reject(r.signal.reason)));
  });
  const f = createGatewayFetch({ onHalt: vi.fn(), onStream: (s) => states.push(s), fetchImpl, random: () => 0.5,
    sleep: async (ms) => { waits.push(ms); } });
  await expect(f(ev())).rejects.toBeInstanceOf(TypeError);
  expect(states.at(-1)).toEqual({ failures: 1, nextRetryS: 1, last: "offline" });
  mode = "ok";
  const ctl = new AbortController();
  const pending = f(new Request("https://s/pcbk/api/oc/event", { signal: ctl.signal }));
  await new Promise((r) => setTimeout(r, 10));
  ctl.abort(); await expect(pending).rejects.toThrow();
  expect(states).toHaveLength(1);                                                // своя отмена — не сбой
  expect(waits).toEqual([0, 1000]);
});

test("stream_line_texts", () => {
  expect(streamLine({ failures: 0, nextRetryS: null, last: "ok" })).toBeNull();
  expect(streamLine({ failures: 2, nextRetryS: null, last: "ok" })).toBeNull();
  expect(streamLine({ failures: 1, nextRetryS: 1, last: "busy" })).toBe(T_STREAM_RETRY(1));
  expect(streamLine({ failures: 3, nextRetryS: 4, last: "ok" })).toBe("Связь с рабочим местом прервалась — переподключаюсь через 4 с");
  expect(streamLine({ failures: 1, nextRetryS: 1, last: "too_many_tabs" })).toBe(T_TOO_MANY_TABS);
  expect(T_BUSY).toBe("Слишком много одновременных запросов — повторите позже");
});
