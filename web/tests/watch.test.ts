// Молчание в браузере (Д6-R51, Review Focus 3 Д6б): сторож хода — отчёт один на ход после 180 с без прироста
// ответа, по часам, а не по числу тиков; сторож потока — отчёт при восстановлении после 120 с без связи.
// useTurnWatch — связка сторожа хода с рантаймом (preflight Ф11): начало хода — isRunning false → true.
import { act, renderHook } from "@testing-library/react";
import { useExternalStoreRuntime, type ThreadMessage, type ThreadMessageLike } from "@assistant-ui/react";
import { afterEach, expect, test, vi } from "vitest";
import type { StreamState } from "../src/gateway";
import { T_TURN_SILENT } from "../src/texts";
import {
  QUIET_REFRESH_S, STREAM_DOWN_S, TICK_GAP_S, TURN_SILENT_S, createStreamWatch, createTurnWatch, turnFingerprint,
  useTurnWatch,
} from "../src/watch";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

// часы теста и тики по ним: ticksTo доводит часы до end шагами step (по умолчанию — раз в секунду, как видимая вкладка)
function clock() {
  const c = {
    t: 0,
    now: () => c.t,
    ticksTo(w: { tick(): void }, end: number, step = 1_000) {
      while (c.t + step < end) { c.t += step; w.tick(); }
      c.t = end; w.tick();
    },
  };
  return c;
}

test("turn_watch_fires_once_after_silence", () => {
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.started(); c.ticksTo(w, 179_000); expect(onSilent).not.toHaveBeenCalled();
  c.ticksTo(w, 181_000); w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_progress_resets_and_end_stops", () => {
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.started(); c.ticksTo(w, 170_000); w.progress("tool:pcbk_tag_now:running"); c.ticksTo(w, 340_000);
  expect(onSilent).not.toHaveBeenCalled();
  w.progress("tool:pcbk_tag_now:running"); c.ticksTo(w, 360_000); expect(onSilent).toHaveBeenCalledTimes(1);   // тот же отпечаток — не прогресс
  w.ended(); c.ticksTo(w, 999_000); expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_background_tab_uses_clock", () => {
  // вкладку скрыли сразу после отправки, 200 с тиков нет (фон троттлит таймеры), вкладку открыли — тик на
  // visibilitychange видит 200 с по часам. Это разрыв тиков: отсчёт заново, отчёт — через полный срок после него
  // (поправка контролёра к итоговой доводке Д6б: ложный turn_silent хуже сигнала на 3 минуты позже)
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.started(); c.t = 1_000; w.tick();
  c.t = 200_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();                     // разрыв — отсчёт заново
  c.ticksTo(w, 200_000 + TURN_SILENT_S * 1000 - 1); expect(onSilent).not.toHaveBeenCalled();
  c.ticksTo(w, 200_000 + TURN_SILENT_S * 1000); expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_sleep_gap_restarts_countdown", () => {
  // Н15 и m4: между тиками больше TICK_GAP_S — машина спала (крышка ноутбука): ход мог кончиться, пока поток стоял, —
  // отсчёт заново, полные TURN_SILENT_S от пробуждения; ход, который адаптер за это время завершил, отчёта не даёт
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.started(); c.ticksTo(w, 5_000);
  c.t = 3_600_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();                     // час сна
  c.ticksTo(w, 3_600_000 + TURN_SILENT_S * 1000 - 1); expect(onSilent).not.toHaveBeenCalled();
  c.ticksTo(w, 3_600_000 + TURN_SILENT_S * 1000); expect(onSilent).toHaveBeenCalledTimes(1);   // молчит и после — отчёт
  const quiet = vi.fn(); const v = createTurnWatch({ now: c.now, onSilent: quiet });       // ход кончился за 179 с
  v.started(); c.t += 3_600_000; v.tick(); c.ticksTo(v, c.t + 179_000); v.ended(); c.ticksTo(v, c.t + 600_000);
  expect(quiet).not.toHaveBeenCalled();
  // граница: разрыв ровно TICK_GAP_S — ещё не сон, отчёт на том же тике; на 1 мс больше — сон, отсчёт заново
  for (const [gap, reports] of [[TICK_GAP_S * 1000, 1], [TICK_GAP_S * 1000 + 1, 0]] as const) {
    const u = clock(); const on = vi.fn(); const x = createTurnWatch({ now: u.now, onSilent: on });
    x.started(); u.ticksTo(x, 170_000); u.t += gap; x.tick();
    expect(on.mock.calls.length, String(gap)).toBe(reports);
  }
});

test("turn_watch_throttled_tab_waits_for_visible", () => {
  // вкладка скрыта дольше 5 минут: Chrome тикает раз в минуту — каждый тик разрыв, отсчёт заново; отчёт приходит,
  // когда вкладку открыли и тики раз в секунду прошли полный срок (цена правила, запись в D6b.md)
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.started(); c.ticksTo(w, 3_600_000, 60_000); expect(onSilent).not.toHaveBeenCalled();  // час в фоне
  c.ticksTo(w, 3_600_000 + TURN_SILENT_S * 1000 - 1); expect(onSilent).not.toHaveBeenCalled();
  c.ticksTo(w, 3_600_000 + TURN_SILENT_S * 1000); expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_restarts_per_turn", () => {
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.tick(); expect(onSilent).not.toHaveBeenCalled();                                     // хода нет — нечего сторожить
  w.started(); c.ticksTo(w, 200_000); expect(onSilent).toHaveBeenCalledTimes(1);
  w.ended(); w.started(); c.ticksTo(w, 300_000); expect(onSilent).toHaveBeenCalledTimes(1);  // новый ход — свой отсчёт
  c.ticksTo(w, 381_000); expect(onSilent).toHaveBeenCalledTimes(2);                     // и свой отчёт
  w.ended(); w.started(); c.ticksTo(w, 500_000); w.ended(); c.ticksTo(w, 999_000);
  expect(onSilent).toHaveBeenCalledTimes(2);                                             // ход кончился до срока — отчёта нет
  w.progress("a1|1|text:5"); c.ticksTo(w, 1_999_000); expect(onSilent).toHaveBeenCalledTimes(2);   // прирост вне хода — не начало
});

// --- тихое перечитывание (onQuiet, Task 2c, случай 13.10): не больше раза за ход (Ruling 13, раунд 1 критиков) ---

test("quiet_fires_once_after_30s", () => {
  // «один раз за ход», не «на отрезок тишины»: прирост (настоящий или мнимый от самого refresh) второго вызова
  // не разрешает — иначе мигающий отпечаток после перечитывания гонял бы refresh по кругу без предела
  expect(QUIET_REFRESH_S).toBe(30);
  const c = clock(); const onSilent = vi.fn(); const onQuiet = vi.fn();
  const w = createTurnWatch({ now: c.now, onSilent, onQuiet });
  w.started(); c.ticksTo(w, 29_000); expect(onQuiet).not.toHaveBeenCalled();
  c.ticksTo(w, 30_000); expect(onQuiet).toHaveBeenCalledTimes(1);
  w.progress("tool:pcbk_tag_now:running");
  c.ticksTo(w, 60_000); expect(onQuiet).toHaveBeenCalledTimes(1);          // прирост — второго вызова всё равно нет
  w.ended(); w.started(); c.ticksTo(w, 90_000); expect(onQuiet).toHaveBeenCalledTimes(2);   // новый ход — снова один
});

test("quiet_not_on_pause", () => {
  const c = clock(); const onSilent = vi.fn(); const onQuiet = vi.fn();
  const w = createTurnWatch({ now: c.now, onSilent, onQuiet });
  w.started(); w.pause(); c.ticksTo(w, 40_000); expect(onQuiet).not.toHaveBeenCalled();   // лежит связь — не модель молчит
});

test("quiet_not_after_end", () => {
  const c = clock(); const onSilent = vi.fn(); const onQuiet = vi.fn();
  const w = createTurnWatch({ now: c.now, onSilent, onQuiet });
  w.started(); w.ended(); c.ticksTo(w, 40_000); expect(onQuiet).not.toHaveBeenCalled();   // хода нет — нечего сторожить
});

test("silent_unchanged", () => {
  // onQuiet сработал по пути (QUIET_REFRESH_S короче TURN_SILENT_S) — срок и число отчётов onSilent это не меняет
  const c = clock(); const onSilent = vi.fn(); const onQuiet = vi.fn();
  const w = createTurnWatch({ now: c.now, onSilent, onQuiet });
  w.started(); c.ticksTo(w, 179_000); expect(onSilent).not.toHaveBeenCalled();
  c.ticksTo(w, 181_000); w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
  expect(onQuiet.mock.calls.length).toBeGreaterThan(0);
});

test("stream_watch_reports_on_recovery_only", () => {
  let t = 0; const on = vi.fn(); const w = createStreamWatch({ now: () => t, onRecoveredAfterLongDown: on });
  w.onStream({ failures: 1, nextRetryS: 1, last: "down" }); t = 60_000; w.onStream({ failures: 1, nextRetryS: null, last: "ok" });
  expect(on).not.toHaveBeenCalled();                                                  // 60 с — коротко
  w.onStream({ failures: 1, nextRetryS: 1, last: "down" }); t = 190_000; w.onStream({ failures: 5, nextRetryS: 30, last: "down" });
  expect(on).not.toHaveBeenCalled();                                                  // пока нет связи — нет отчёта
  w.onStream({ failures: 5, nextRetryS: null, last: "ok" }); expect(on).toHaveBeenCalledTimes(1);   // восстановление — по last, счёт сбросит только долгий поток
});

test("stream_watch_one_report_per_episode", () => {
  let t = 0; const on = vi.fn(); const w = createStreamWatch({ now: () => t, onRecoveredAfterLongDown: on });
  const ok: StreamState = { failures: 0, nextRetryS: null, last: "ok" };
  w.onStream(ok); t = 500_000; w.onStream(ok); expect(on).not.toHaveBeenCalled();        // поток жив — не отчёт
  w.onStream({ failures: 1, nextRetryS: 1, last: "offline" }); t += STREAM_DOWN_S * 1000;
  w.onStream({ failures: 2, nextRetryS: 2, last: "busy" });
  w.onStream({ failures: 2, nextRetryS: null, last: "ok" }); expect(on).not.toHaveBeenCalled();   // ровно 120 с — не дольше
  w.onStream({ failures: 3, nextRetryS: 4, last: "down" }); t += STREAM_DOWN_S * 1000 + 1;
  w.onStream({ failures: 3, nextRetryS: null, last: "ok" }); expect(on).toHaveBeenCalledTimes(1);
  t += 1_000; w.onStream({ failures: 0, nextRetryS: null, last: "ok" }); expect(on).toHaveBeenCalledTimes(1);   // эпизод закрыт
});

test("stream_watch_too_many_tabs_is_not_loss", () => {
  // доводка 1: «закройте лишние вкладки» — шлюз ответил; забытая четвёртая вкладка stream_down не даёт
  let t = 0; const on = vi.fn(); const w = createStreamWatch({ now: () => t, onRecoveredAfterLongDown: on });
  for (let i = 1; i <= 120; i += 1) { t += 30_000; w.onStream({ failures: i, nextRetryS: 30, last: "too_many_tabs" }); }   // час
  w.onStream({ failures: 120, nextRetryS: null, last: "ok" });                           // закрыли лишнюю — место есть
  expect(on).not.toHaveBeenCalled();
  w.onStream({ failures: 1, nextRetryS: 1, last: "offline" }); t += 130_000;            // а настоящая потеря связи
  w.onStream({ failures: 6, nextRetryS: 30, last: "too_many_tabs" });                   // кончается ответом шлюза
  expect(on).toHaveBeenCalledTimes(1);
  t += 3_600_000; w.onStream({ failures: 7, nextRetryS: 30, last: "too_many_tabs" }); w.onStream({ failures: 7, nextRetryS: null, last: "ok" });
  expect(on).toHaveBeenCalledTimes(1);                                                   // эпизод уже закрыт
});

test("stream_watch_sleep_is_not_loss", () => {
  // m4 и поправка контролёра: связь пропала перед сном (крышка ноутбука), машина проспала час — потеря, начатая до
  // разрыва тиков, считается с пробуждения: время сна в эпизод не входит
  const c = clock(); const on = vi.fn(); const w = createStreamWatch({ now: c.now, onRecoveredAfterLongDown: on });
  const down: StreamState = { failures: 1, nextRetryS: 1, last: "offline" };
  const ok: StreamState = { failures: 1, nextRetryS: null, last: "ok" };
  w.tick(); c.ticksTo(w, 10_000); w.onStream(down);
  c.t = 3_600_000; w.tick();                                                            // первый тик после сна
  c.ticksTo(w, c.t + STREAM_DOWN_S * 1000); w.onStream(ok); expect(on).not.toHaveBeenCalled();   // 120 с после — не дольше
  // связи нет и дольше STREAM_DOWN_S после пробуждения — потеря настоящая, отчёт
  w.onStream(down); c.t += 3_600_000; w.tick();
  c.ticksTo(w, c.t + STREAM_DOWN_S * 1000 + 1_000); w.onStream(ok); expect(on).toHaveBeenCalledTimes(1);
  // эпизод долгий уже к тику перед сном (сервер лежал 130 с при живой машине) — не переносится, отчёт и при быстром
  // восстановлении после сна
  w.onStream(down); c.ticksTo(w, c.t + 130_000); c.t += 3_600_000; w.tick();
  c.t += 1_000; w.onStream(ok); expect(on).toHaveBeenCalledTimes(2);
  // эпизод начался после пробуждения — обычное правило
  c.t += 3_600_000; w.tick(); w.onStream(down); c.ticksTo(w, c.t + STREAM_DOWN_S * 1000 + 1_000);
  w.onStream(ok); expect(on).toHaveBeenCalledTimes(3);
});

test("stream_watch_recovery_before_first_tick_after_sleep", () => {
  // итоговая доводка Д6б, остаток m1: связь лежала до сна, после пробуждения поток вернулся раньше первого тика —
  // разрыв по lastTick видит и onStream: это сон, stream_down нет; потом тик — тоже без отчёта
  const c = clock(); const on = vi.fn(); const w = createStreamWatch({ now: c.now, onRecoveredAfterLongDown: on });
  w.tick(); c.ticksTo(w, 10_000); w.onStream({ failures: 1, nextRetryS: 1, last: "offline" });
  c.t += 3_600_000; w.onStream({ failures: 2, nextRetryS: null, last: "ok" });         // раньше первого тика
  w.tick(); c.ticksTo(w, c.t + 300_000);
  expect(on).not.toHaveBeenCalled();
  // потеря после пробуждения тоже открывается ответом потока раньше тика — отсчёт с него, обычное правило
  c.t += 3_600_000; w.onStream({ failures: 1, nextRetryS: 1, last: "offline" });
  c.ticksTo(w, c.t + STREAM_DOWN_S * 1000 + 1_000); w.onStream({ failures: 6, nextRetryS: null, last: "ok" });
  expect(on).toHaveBeenCalledTimes(1);
  // эпизод, долгий уже к последнему тику перед сном, остаётся настоящим
  w.onStream({ failures: 1, nextRetryS: 1, last: "down" }); c.ticksTo(w, c.t + 130_000);
  c.t += 3_600_000; w.onStream({ failures: 9, nextRetryS: null, last: "ok" });
  expect(on).toHaveBeenCalledTimes(2);
});

test("stream_watch_throttled_tab_waits_for_visible", () => {
  // вкладка скрыта дольше 5 минут: тик раз в минуту — каждый тик разрыв, потеря считается заново с каждого тика, и
  // stream_down нет (цена правила, запись в D6b.md); вкладку открыли — тики раз в секунду, обычное правило
  const c = clock(); const on = vi.fn(); const w = createStreamWatch({ now: c.now, onRecoveredAfterLongDown: on });
  w.tick(); c.t = 10_000; w.onStream({ failures: 1, nextRetryS: 1, last: "down" });
  c.ticksTo(w, 3_600_000, 60_000);
  c.t += 59_000; w.tick();                                                              // вкладку открыли: тик — тоже разрыв
  w.onStream({ failures: 9, nextRetryS: null, last: "ok" });                            // час без связи, тики редкие
  expect(on).not.toHaveBeenCalled();
  w.onStream({ failures: 1, nextRetryS: 1, last: "down" }); c.ticksTo(w, c.t + STREAM_DOWN_S * 1000 + 1_000);
  w.onStream({ failures: 9, nextRetryS: null, last: "ok" }); expect(on).toHaveBeenCalledTimes(1);   // видимая — отчёт
});

test("turn_watch_pauses_while_stream_down", () => {
  // доводка 1, m4: пока поток не "ok", молчание хода не копится — связь лежит, а не модель молчит
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.started(); c.ticksTo(w, 100_000); w.pause();
  c.ticksTo(w, 1_000_000); expect(onSilent).not.toHaveBeenCalled();                     // 15 мин без потока — не отчёт
  w.resume();
  c.ticksTo(w, 1_079_000); expect(onSilent).not.toHaveBeenCalled();                     // 100 + 79 с — рано
  c.ticksTo(w, 1_081_000); expect(onSilent).toHaveBeenCalledTimes(1);                   // 181 с молчания при потоке
  w.ended(); w.pause(); w.started(); c.ticksTo(w, 1_500_000);                            // ход начался без потока
  expect(onSilent).toHaveBeenCalledTimes(1);
  w.resume(); c.ticksTo(w, 1_679_000); expect(onSilent).toHaveBeenCalledTimes(1);
  c.ticksTo(w, 1_681_000); expect(onSilent).toHaveBeenCalledTimes(2);
});

test("turn_watch_pause_before_turn_start", () => {
  // итоговая доводка Д6б, Б3-m3: поток лёг раньше начала хода — из отсчёта вычитается только пауза после начала
  // (Math.max в resume): ход 100 с без потока, потом 180 с молчания при потоке — отчёт, не позже
  const c = clock(); const onSilent = vi.fn(); const w = createTurnWatch({ now: c.now, onSilent });
  w.pause(); c.ticksTo(w, 400_000);                                                      // поток лежит, хода нет
  w.started(); c.ticksTo(w, 500_000); w.resume();                                        // ход шёл 100 с без потока
  c.ticksTo(w, 679_000); expect(onSilent).not.toHaveBeenCalled();
  c.ticksTo(w, 681_000); expect(onSilent).toHaveBeenCalledTimes(1);                      // 181 с после resume
});

// --- отпечаток прогресса: messageId последнего ответа ассистента, число частей, последняя часть ---

const msg = (m: ThreadMessageLike): ThreadMessage => m as unknown as ThreadMessage;

test("turn_fingerprint_marks_growth", () => {
  const user = msg({ id: "u1", role: "user", content: [{ type: "text", text: "вопрос" }] });
  expect(turnFingerprint([])).toBe("");
  expect(turnFingerprint([user])).toBe("");                                                // ответа ещё нет
  const a = (content: ThreadMessageLike["content"]) => msg({ id: "a1", role: "assistant", content });
  const seen = [
    turnFingerprint([user, a([{ type: "reasoning", text: "ду" }])]),
    turnFingerprint([user, a([{ type: "reasoning", text: "думаю" }])]),                  // рассуждение без текста — тоже прирост
    turnFingerprint([user, a([{ type: "reasoning", text: "думаю" }, { type: "tool-call", toolCallId: "c1", toolName: "pcbk_tag_now", args: {} }])]),
    turnFingerprint([user, a([{ type: "reasoning", text: "думаю" }, { type: "tool-call", toolCallId: "c1", toolName: "pcbk_tag_now", args: {}, result: "1" }])]),
    turnFingerprint([user, a([{ type: "reasoning", text: "думаю" }, { type: "tool-call", toolCallId: "c1", toolName: "pcbk_tag_now", args: {}, result: "1" }, { type: "text", text: "О" }])]),
    turnFingerprint([user, a([{ type: "reasoning", text: "думаю" }, { type: "tool-call", toolCallId: "c1", toolName: "pcbk_tag_now", args: {}, result: "1" }, { type: "text", text: "Ответ" }])]),
  ];
  expect(new Set(seen).size).toBe(seen.length);                                           // каждый шаг — новый отпечаток
  expect(seen[2]).toContain("tool:pcbk_tag_now:running");                                 // статус вызова — в отпечатке (Н14)
  expect(seen[3]).toContain("tool:pcbk_tag_now:done");
  const next = msg({ id: "a2", role: "assistant", content: [{ type: "text", text: "Ответ" }] });
  expect(turnFingerprint([user, a([{ type: "text", text: "Ответ" }]), user, next])).not.toBe(
    turnFingerprint([user, a([{ type: "text", text: "Ответ" }])]));                       // новое сообщение — новый отпечаток
});

// --- useTurnWatch: рантайм из внешнего хранилища, часы — Date.now под vi.useFakeTimers ---

type Props = { running: boolean; messages: ThreadMessageLike[]; streamOk?: boolean };
const USER: ThreadMessageLike = { id: "u1", role: "user", content: [{ type: "text", text: "вопрос" }] };
const answer = (text: string): ThreadMessageLike =>
  ({ id: "a1", role: "assistant", content: [{ type: "text", text }], status: { type: "running" } });

function watchHook(onSilent: () => void) {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
  return renderHook((p: Props) => {
    const runtime = useExternalStoreRuntime<ThreadMessageLike>({
      messages: p.messages, isRunning: p.running, onNew: async () => {}, convertMessage: (m) => m,
    });
    return useTurnWatch(runtime, { onSilent, streamOk: p.streamOk });
  }, { initialProps: { running: false, messages: [USER] } as Props });
}

test("use_turn_watch_start_progress_end", async () => {
  const onSilent = vi.fn();
  const h = watchHook(onSilent);
  await act(async () => { vi.advanceTimersByTime(300_000); });
  expect(onSilent).not.toHaveBeenCalled();                                                // хода нет
  h.rerender({ running: true, messages: [USER] });                                        // ход начался: false → true
  await act(async () => { vi.advanceTimersByTime(170_000); });
  h.rerender({ running: true, messages: [USER, answer("Ч")] });                           // прирост ответа
  await act(async () => { vi.advanceTimersByTime(170_000); });
  expect(onSilent).not.toHaveBeenCalled(); expect(h.result.current).toBe(false);
  await act(async () => { vi.advanceTimersByTime(11_000); });                             // 181 с без прироста
  expect(onSilent).toHaveBeenCalledTimes(1); expect(h.result.current).toBe(true);        // строка «Ответа нет…» видна
  h.rerender({ running: true, messages: [USER, answer("Число")] });
  expect(h.result.current).toBe(false);                                                   // ответ пошёл — строка снята
  await act(async () => { vi.advanceTimersByTime(400_000); });
  expect(onSilent).toHaveBeenCalledTimes(1);                                              // отчёт — один на ход
  h.rerender({ running: false, messages: [USER, answer("Число")] });
  h.rerender({ running: true, messages: [USER, answer("Число")] });                       // следующий ход
  await act(async () => { vi.advanceTimersByTime(181_000); });
  expect(onSilent).toHaveBeenCalledTimes(2);
  h.rerender({ running: false, messages: [USER, answer("Число")] });
  expect(h.result.current).toBe(false);                                                   // конец хода снимает строку
  await act(async () => { vi.advanceTimersByTime(400_000); });
  expect(onSilent).toHaveBeenCalledTimes(2);
  h.rerender({ running: true, messages: [USER, answer("Число")] });                       // доводка 1, I2: ход кончился
  await act(async () => { vi.advanceTimersByTime(100_000); });                            // раньше срока — сторож снят
  h.rerender({ running: false, messages: [USER, answer("Число")] });
  await act(async () => { vi.advanceTimersByTime(400_000); });
  expect(onSilent).toHaveBeenCalledTimes(2);
  h.unmount();
});

// видимость вкладки в jsdom: своё свойство поверх геттера документа (снимается в finally)
function setVisibility(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

test("use_turn_watch_ticks_on_visibility", async () => {
  const onSilent = vi.fn();
  const h = watchHook(onSilent);
  try {
    h.rerender({ running: true, messages: [USER] });
    await act(async () => { setVisibility("hidden"); });                                 // вкладку скрыли — тик со «скрыта»
    vi.setSystemTime(Date.now() + 181_000);                                               // часы ушли, тиков не было (фон)
    expect(onSilent).not.toHaveBeenCalled();
    await act(async () => { setVisibility("visible"); });                                // вернулись — тик по visibilitychange
    expect(onSilent).not.toHaveBeenCalled();                                              // разрыв тиков — отсчёт заново
    await act(async () => { vi.advanceTimersByTime(TURN_SILENT_S * 1000 - 1_000); });
    expect(onSilent).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(1_000); });                            // полный срок после разрыва
    expect(onSilent).toHaveBeenCalledTimes(1); expect(h.result.current).toBe(true);
    h.unmount();
    vi.setSystemTime(Date.now() + 999_000);
    setVisibility("hidden"); setVisibility("visible");
    expect(onSilent).toHaveBeenCalledTimes(1);                                            // снятый сторож молчит
  } finally {
    delete (document as { visibilityState?: unknown }).visibilityState;
  }
});

test("use_turn_watch_sleep_does_not_report", async () => {
  // Н15 и m4 через рантайм: машина уснула на час посреди хода — отсчёт заново с первого тика после сна; ход, который
  // адаптер за полный срок завершил (перечитал статус), отчёта не даёт; молчание и после — отчёт через полный срок
  const onSilent = vi.fn();
  const h = watchHook(onSilent);
  h.rerender({ running: true, messages: [USER] });
  await act(async () => { vi.advanceTimersByTime(5_000); });
  vi.setSystemTime(Date.now() + 3_600_000);
  await act(async () => { vi.advanceTimersByTime(1_000); });                              // первый тик после сна
  expect(onSilent).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(TURN_SILENT_S * 1000 - 1_000); });
  expect(onSilent).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(1_000); });                              // полный срок после него
  expect(onSilent).toHaveBeenCalledTimes(1);
  h.rerender({ running: false, messages: [USER, answer("Число")] });
  h.rerender({ running: true, messages: [USER, answer("Число")] });                       // следующий ход
  await act(async () => { vi.advanceTimersByTime(5_000); });
  vi.setSystemTime(Date.now() + 3_600_000);
  await act(async () => { vi.advanceTimersByTime(1_000); });                              // сон и первый тик
  await act(async () => { vi.advanceTimersByTime(170_000); });
  h.rerender({ running: false, messages: [USER, answer("Число и ещё")] });                // адаптер завершил ход
  await act(async () => { vi.advanceTimersByTime(400_000); });
  expect(onSilent).toHaveBeenCalledTimes(1);
  h.unmount();
});

test("use_turn_watch_pauses_on_stream_down", async () => {
  const onSilent = vi.fn();
  const h = watchHook(onSilent);
  h.rerender({ running: true, messages: [USER] });
  await act(async () => { vi.advanceTimersByTime(60_000); });
  h.rerender({ running: true, messages: [USER], streamOk: false });                      // поток лёг
  await act(async () => { vi.advanceTimersByTime(600_000); });
  expect(onSilent).not.toHaveBeenCalled();
  h.rerender({ running: true, messages: [USER], streamOk: true });                       // вернулся — отсчёт с 60 с
  await act(async () => { vi.advanceTimersByTime(119_000); });
  expect(onSilent).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(2_000); });
  expect(onSilent).toHaveBeenCalledTimes(1);
  h.unmount();
});

test("silent_text_says_three_minutes", () => {
  expect(TURN_SILENT_S).toBe(180);
  expect(T_TURN_SILENT).toBe("Ответа нет уже 3 минуты. Можно нажать «Стоп» и спросить снова — сбой отмечен для администратора.");
});
