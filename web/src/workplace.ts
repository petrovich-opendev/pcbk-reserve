// Пробуждение рабочего места из чата (Д6-R39): POST wake и опрос раз в секунду до 90 с — те же ветки и
// тексты, что wake()/poll()/viewFor страницы входа (edge/static/login.js; сверка — tests/workplace.test.ts).
// Логика живёт в двух местах сознательно (R39, «Цена ошибки»): веб не тянет в сборку статику edge.
import { replyText, type Api, type Reply } from "./api";
import {
  T_PLACE_FAILED, T_PLACE_FAILED_NO_DETAIL, T_PLACE_GAVE_UP, T_PLACE_ODD, T_PLACE_PREPARING, T_PLACE_READY,
} from "./texts";

export const GIVE_UP_S = 90;           // дольше не ждём; срок — своим таймером, как у страницы входа
export const POLL_MS = 1000;           // опрос готовности — раз в секунду

export interface PlaceView { text: string; poll: boolean; ok: boolean }

// ответ места ({state, detail}) → текст; poll — спрашивать ли дальше
export function placeView(reply: unknown): PlaceView {
  const r = reply !== null && typeof reply === "object" ? reply as Record<string, unknown> : null;
  switch (r?.state) {
    case "sleeping":
    case "starting":
      return { text: T_PLACE_PREPARING, poll: true, ok: false };
    case "ready":
      return { text: T_PLACE_READY, poll: false, ok: true };
    case "failed":                     // `detail || …` — как в login.js, и для не-строки
      return { text: T_PLACE_FAILED(String(r.detail || T_PLACE_FAILED_NO_DETAIL)), poll: false, ok: false };
    case "gave_up":
      return { text: T_PLACE_GAVE_UP, poll: false, ok: false };
    default:                           // незнакомое состояние — не опрашиваем вечно
      return { text: T_PLACE_ODD, poll: false, ok: false };
  }
}

export interface Timers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
}

const defaultTimers: Timers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

export type WakeResult = "ready" | "failed" | "gave_up" | "signed_out" | "aborted";

// Будит место и ждёт готовности. onView — на каждый ответ о месте (и на сбой опроса — текстом ошибки с
// poll: true), итог — один раз. signed_out и aborted — без onView: решает вызывающий (вход или размонтирование).
export function wakeAndWait(o: {
  api: Api;
  onView(v: PlaceView): void;
  giveUpS?: number;
  pollMs?: number;
  timers?: Timers;
  signal?: AbortSignal;
}): Promise<WakeResult> {
  const { api, onView, signal } = o;
  const giveUpS = o.giveUpS ?? GIVE_UP_S;
  const pollMs = o.pollMs ?? POLL_MS;
  const timers = o.timers ?? defaultTimers;
  return new Promise<WakeResult>((resolve) => {
    if (signal?.aborted) {
      resolve("aborted");
      return;
    }
    let done = false;
    let pollTimer: unknown = null;
    let giveUpTimer: unknown = null;

    const finish = (result: WakeResult, view?: PlaceView): void => {
      if (done) return;
      done = true;
      if (giveUpTimer !== null) timers.clearTimeout(giveUpTimer);
      if (pollTimer !== null) timers.clearTimeout(pollTimer);
      pollTimer = null;
      giveUpTimer = null;
      signal?.removeEventListener("abort", onAbort);
      if (view) onView(view);
      resolve(result);
    };
    const onAbort = (): void => finish("aborted");
    signal?.addEventListener("abort", onAbort, { once: true });

    const call = (fn: () => Promise<Reply>): Promise<Reply> =>
      fn().catch((): Reply => ({ status: 0, body: null }));
    const later = (): void => { pollTimer = timers.setTimeout(() => void poll(), pollMs); };
    const refused = (r: Reply): PlaceView => ({ text: replyText(r), poll: false, ok: false });

    const showPlace = (body: unknown): void => {
      const view = placeView(body);
      if (view.ok) finish("ready", view);
      else if (!view.poll) finish("failed", view);
      else {
        onView(view);
        later();
      }
    };

    const poll = async (): Promise<void> => {
      pollTimer = null;
      const r = await call(() => api.workplace());
      if (done) return;
      if (r.status === 401) finish("signed_out");
      else if (r.status === 200) showPlace(r.body);
      else if (r.status === 0 || r.status >= 500) {      // core перезапускается — спрашиваем дальше до срока
        onView({ text: replyText(r), poll: true, ok: false });
        later();
      } else finish("failed", refused(r));
    };

    giveUpTimer = timers.setTimeout(() => finish("gave_up", placeView({ state: "gave_up" })), giveUpS * 1000);
    void (async () => {
      const r = await call(() => api.wake());
      if (done) return;
      if (r.status === 401) finish("signed_out");
      else if (r.status === 200 || r.status === 202) showPlace(r.body);
      else finish("failed", refused(r));                 // пробуждение не принято — ждать нечего
    })();
  });
}
