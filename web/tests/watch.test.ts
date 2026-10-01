// Молчание в браузере (Д6-R51, Review Focus 3 Д6б): сторож хода — отчёт один на ход после 180 с без прироста
// ответа, по часам, а не по числу тиков; сторож потока — отчёт при восстановлении после 120 с без связи.
// useTurnWatch — связка сторожа хода с рантаймом (preflight Ф11): начало хода — isRunning false → true.
import { act, renderHook } from "@testing-library/react";
import { useExternalStoreRuntime, type ThreadMessage, type ThreadMessageLike } from "@assistant-ui/react";
import { afterEach, expect, test, vi } from "vitest";
import type { StreamState } from "../src/gateway";
import { T_TURN_SILENT } from "../src/texts";
import {
  STREAM_DOWN_S, TICK_GAP_S, TURN_SILENT_S, createStreamWatch, createTurnWatch, turnFingerprint, useTurnWatch,
} from "../src/watch";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

test("turn_watch_fires_once_after_silence", () => {
  let t = 0; const onSilent = vi.fn(); const w = createTurnWatch({ now: () => t, onSilent });
  w.started(); t = 179_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();
  t = 181_000; w.tick(); w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_progress_resets_and_end_stops", () => {
  let t = 0; const onSilent = vi.fn(); const w = createTurnWatch({ now: () => t, onSilent });
  w.started(); t = 170_000; w.progress("tool:pcbk_tag_now:running"); t = 340_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();
  w.progress("tool:pcbk_tag_now:running"); t = 360_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);   // тот же отпечаток — не прогресс
  w.ended(); t = 999_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_background_tab_uses_clock", () => {
  // вкладку скрыли сразу после отправки: тик на visibilitychange (скрыта), 200 с тиков нет (фон троттлит таймеры),
  // вкладку открыли — тик на visibilitychange видит 200 с по часам: отчёт сразу, а не через 180 тиков
  let t = 0; let visible = true; const onSilent = vi.fn();
  const w = createTurnWatch({ now: () => t, onSilent, isVisible: () => visible });
  w.started(); t = 1_000; visible = false; w.tick();
  t = 200_000; visible = true; w.tick();
  expect(onSilent).toHaveBeenCalledTimes(1);
});

test("turn_watch_sleep_gap_restarts_countdown", () => {
  // preflight Н15: вкладка видна, а между тиками больше TICK_GAP_S — машина спала (крышка ноутбука): ход мог
  // кончиться, пока поток стоял, — отсчёт заново, а не ложный отчёт на первом тике после сна
  let t = 0; const onSilent = vi.fn();
  const w = createTurnWatch({ now: () => t, onSilent, isVisible: () => true });
  w.started(); t = 1_000; w.tick();
  t = 3_600_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();                     // час сна
  for (let s = 0; s < TURN_SILENT_S - 1; s += 1) { t += 1_000; w.tick(); }               // дальше тики раз в секунду
  expect(onSilent).not.toHaveBeenCalled();                                                // 179 с после сна — рано
  t += 2_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);                       // 181 с после сна — отчёт
  // граница: разрыв ровно TICK_GAP_S — ещё не сон
  let u = 0; const quiet = vi.fn();
  const v = createTurnWatch({ now: () => u, onSilent: quiet, isVisible: () => true });
  v.started();
  for (let i = 0; i < TURN_SILENT_S / TICK_GAP_S; i += 1) { u += TICK_GAP_S * 1000; v.tick(); }
  expect(quiet).toHaveBeenCalledTimes(1);
});

test("turn_watch_restarts_per_turn", () => {
  let t = 0; const onSilent = vi.fn(); const w = createTurnWatch({ now: () => t, onSilent });
  w.tick(); expect(onSilent).not.toHaveBeenCalled();                                     // хода нет — нечего сторожить
  w.started(); t = 200_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
  w.ended(); w.started(); t = 300_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);  // новый ход — свой отсчёт
  t = 381_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(2);                     // и свой отчёт
  w.ended(); w.started(); t = 500_000; w.ended(); t = 999_000; w.tick();
  expect(onSilent).toHaveBeenCalledTimes(2);                                             // ход кончился до срока — отчёта нет
  w.progress("a1|1|text:5"); t = 1_999_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(2);   // прирост вне хода — не начало
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

test("turn_watch_pauses_while_stream_down", () => {
  // доводка 1, m4: пока поток не "ok", молчание хода не копится — связь лежит, а не модель молчит
  let t = 0; const onSilent = vi.fn(); const w = createTurnWatch({ now: () => t, onSilent });
  w.started(); t = 100_000; w.pause();
  t = 1_000_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();                     // 15 мин без потока — не отчёт
  w.resume();
  t = 1_079_000; w.tick(); expect(onSilent).not.toHaveBeenCalled();                     // 100 + 79 с — рано
  t = 1_081_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);                   // 181 с молчания при потоке
  w.ended(); w.pause(); w.started(); t = 1_500_000; w.tick();                            // ход начался без потока
  expect(onSilent).toHaveBeenCalledTimes(1);
  w.resume(); t = 1_679_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(1);
  t = 1_681_000; w.tick(); expect(onSilent).toHaveBeenCalledTimes(2);
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
  // Н15 через рантайм: вкладка видна, машина уснула на час посреди хода — первый тик после сна не отчёт
  const onSilent = vi.fn();
  const h = watchHook(onSilent);
  h.rerender({ running: true, messages: [USER] });
  await act(async () => { vi.advanceTimersByTime(5_000); });
  vi.setSystemTime(Date.now() + 3_600_000);
  await act(async () => { vi.advanceTimersByTime(1_000); });
  expect(onSilent).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(181_000); });                            // а молчание и после сна — отчёт
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
