// Баннер состояния стенда (Д6-R44, решение F7): опрос /status.json сторожа и вид баннера по нему.
// - Не считаются строки чужих мест (student-NN ≠ своё), строка web (она для владельца) и absent; уровень —
//   худший из оставшихся, fail > unknown > warn > ok, как в page.py.
// - «Состояние стенда неизвестно — сторож не отвечает» — только когда молчит сама страница: stale, не 200,
//   не JSON, повисший запрос и молчание дольше stale_after_s по монотонным часам браузера (как status.js
//   сторожа). Строки в unknown при живом стороже — «Состояние неизвестно: <названия>» (F7).
import {
  T_STATUS_FAIL, T_STATUS_MORE, T_STATUS_OK, T_STATUS_ROWS_UNKNOWN, T_STATUS_UNKNOWN, T_STATUS_WARN,
} from "./texts";

export type Level = "ok" | "warn" | "fail" | "unknown";

export interface StatusView { level: Level; text: string }

const SEVERITY: readonly Level[] = ["fail", "unknown", "warn", "ok"];   // худшее — первым (page.py)
const PREFIX: Record<Exclude<Level, "ok">, string> = {
  fail: T_STATUS_FAIL, unknown: T_STATUS_ROWS_UNKNOWN, warn: T_STATUS_WARN,
};
const MAX_TITLES = 3;
const STALE_AFTER_S = 30;               // если в ответе нет stale_after_s (как у status.js по умолчанию)
const SILENT: StatusView = { level: "unknown", text: T_STATUS_UNKNOWN };

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object";

function names(titles: string[]): string {
  const shown = titles.slice(0, MAX_TITLES).join(", ");
  return titles.length > MAX_TITLES ? shown + T_STATUS_MORE(titles.length - MAX_TITLES) : shown;
}

// ответ /status.json → вид баннера; ownPlace — «03» из /me (null — мест не учитывать вовсе)
export function statusView(data: unknown, ownPlace: string | null): StatusView {
  if (!isObject(data) || data.stale !== false || !Array.isArray(data.checks)) return SILENT;
  const own = ownPlace === null ? null : `student-${ownPlace}`;
  const byLevel: Record<Level, string[]> = { fail: [], unknown: [], warn: [], ok: [] };
  for (const row of data.checks) {
    if (!isObject(row)) continue;
    const component = typeof row.component === "string" ? row.component : "";
    if (component === "web" || row.state === "absent") continue;
    if (component.startsWith("student-") && component !== own) continue;     // чужое место
    // незнакомое состояние — «неизвестно», а не «норма»: баннер не врёт в сторону «работает»
    const level: Level = SEVERITY.includes(row.state as Level) ? row.state as Level : "unknown";
    byLevel[level].push(typeof row.title === "string" && row.title ? row.title : component);
  }
  const worst = SEVERITY.find((l) => l !== "ok" && byLevel[l].length > 0);
  if (worst === undefined || worst === "ok") return { level: "ok", text: T_STATUS_OK };
  return { level: worst, text: `${PREFIX[worst]}: ${names(byLevel[worst])}` };
}

type StatusFetch = (url: string, init: RequestInit) => Promise<Response>;

// Опрос раз в pollMs со сроком запроса timeoutMs и пересчёт раз в секунду по часам — как startPolling в
// status.js сторожа. onView — только при смене вида; до первого ответа (в пределах срока запроса) — молчит,
// чтобы на каждой загрузке не мигало «неизвестно». Возвращает остановку.
export function startStatusPolling(o: {
  onView(v: StatusView): void;
  ownPlace: string | null;
  fetchImpl?: StatusFetch;
  nowMs?: () => number;
  setTimeoutImpl?: (fn: () => void, ms: number) => unknown;
  clearTimeoutImpl?: (id: unknown) => void;
  setIntervalImpl?: (fn: () => void, ms: number) => unknown;
  clearIntervalImpl?: (id: unknown) => void;
  pollMs?: number;
  timeoutMs?: number;
}): () => void {
  const fetchImpl: StatusFetch = o.fetchImpl ?? ((url, init) => fetch(url, init));
  const nowMs = o.nowMs ?? (() => performance.now());
  const setTimeoutImpl = o.setTimeoutImpl ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimeoutImpl = o.clearTimeoutImpl ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>));
  const setIntervalImpl = o.setIntervalImpl ?? ((fn, ms) => setInterval(fn, ms));
  const clearIntervalImpl = o.clearIntervalImpl ?? ((id) => clearInterval(id as ReturnType<typeof setInterval>));
  const pollMs = o.pollMs ?? 10000;
  const timeoutMs = o.timeoutMs ?? 4000;

  const startedMs = nowMs();
  let lastOkMs: number | null = null;   // последний свежий ответ (stale: false)
  let failing = false;                  // последний применённый ответ — не свежий
  let staleAfterS = STALE_AFTER_S;
  let fresh: StatusView = SILENT;       // вид последнего свежего ответа
  let shown: StatusView | null = null;
  let sent = 0;
  let applied = 0;
  let stopped = false;
  const inFlight = new Set<AbortController>();

  function update(): void {
    if (stopped) return;
    const now = nowMs();
    if (lastOkMs === null && !failing && now - startedMs <= timeoutMs) return;   // первый ответ ещё в пути
    const silent = failing || lastOkMs === null || now - lastOkMs > staleAfterS * 1000;
    const view = silent ? SILENT : fresh;
    if (shown !== null && shown.level === view.level && shown.text === view.text) return;
    shown = view;
    o.onView(view);
  }

  async function poll(): Promise<void> {
    if (stopped) return;
    update();                           // часы браузера — до нового запроса
    const seq = ++sent;
    const ctl = new AbortController();
    inFlight.add(ctl);
    const timer = setTimeoutImpl(() => ctl.abort(), timeoutMs);
    let data: unknown = null;
    try {
      const res = await fetchImpl("/status.json", {
        signal: ctl.signal, cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" },
      });
      if (res.status === 200) data = await res.json();
    } catch {
      data = null;                      // сеть, отмена по сроку, не JSON
    } finally {
      clearTimeoutImpl(timer);
      inFlight.delete(ctl);
    }
    if (stopped || seq < applied) return;   // остановлен или запоздавший ответ старого запроса
    applied = seq;
    if (isObject(data) && data.stale === false) {
      failing = false;
      lastOkMs = nowMs();
      staleAfterS = Number(data.stale_after_s) || STALE_AFTER_S;
      fresh = statusView(data, o.ownPlace);
    } else {
      failing = true;
    }
    update();
  }

  const pollTimer = setIntervalImpl(() => void poll(), pollMs);
  const clockTimer = setIntervalImpl(update, 1000);   // вид по часам — даже когда запрос повис
  void poll();

  return () => {
    stopped = true;
    clearIntervalImpl(pollTimer);
    clearIntervalImpl(clockTimer);
    for (const ctl of inFlight) ctl.abort();
    inFlight.clear();
  };
}
