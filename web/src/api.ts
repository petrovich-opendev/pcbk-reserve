// Вызовы шлюза вне OpenCode (Д6-R39, R49, R50): вход, выход, подтверждение предупреждения, рабочее место и отчёты
// браузера о молчании чата. Тот же вид запроса, что call() страницы
// входа: cookie сессии своего сайта (HttpOnly — её ставит и стирает шлюз), Accept JSON, тело — только JSON.
// Ответ — всегда Reply: сеть оборвалась — status 0, тело не JSON — текст как есть.
import { T_DOWN, T_UNKNOWN } from "./texts";

export interface Reply { status: number; body: unknown }

export type JsonFetch = (path: string, init: RequestInit) => Promise<Response>;

const defaultFetch: JsonFetch = (path, init) => fetch(path, init);

export async function callJson(method: "GET" | "POST", path: string, body?: unknown,
                               fetchImpl: JsonFetch = defaultFetch): Promise<Reply> {
  const headers: Record<string, string> = { Accept: "application/json" };
  // режим fetch — cors по умолчанию: так браузер шлёт Origin и при Referrer-Policy no-referrer (как login.js)
  const init: RequestInit = { method, credentials: "same-origin", headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  try {
    const res = await fetchImpl(path, init);
    const text = await res.text();
    try {
      return { status: res.status, body: JSON.parse(text) };
    } catch {
      return { status: res.status, body: text };        // не JSON — HTML или текст прокси
    }
  } catch {
    return { status: 0, body: null };                   // сеть оборвалась
  }
}

// виды отчётов браузера (R50): boot_failed шлёт загрузчик boot.js, прочие — чат
export type ReportKind = "boot_failed" | "ui_crash" | "turn_silent" | "stream_down";

export interface Api {
  me(): Promise<Reply>;
  logout(): Promise<Reply>;
  wake(): Promise<Reply>;
  workplace(): Promise<Reply>;
  ackNotice(notice: string): Promise<Reply>;          // тело ровно {"notice": N}; ответ — 204 (R49)
  report(kind: ReportKind): Promise<void>;            // тело ровно {"kind": K}; ответ не читается, ошибки глушатся (R51)
}

export function createApi(fetchImpl: JsonFetch = defaultFetch): Api {
  return {
    me: () => callJson("GET", "/pcbk/api/auth/me", undefined, fetchImpl),
    logout: () => callJson("POST", "/pcbk/api/auth/logout", undefined, fetchImpl),
    wake: () => callJson("POST", "/pcbk/api/workplace/wake", undefined, fetchImpl),
    workplace: () => callJson("GET", "/pcbk/api/workplace", undefined, fetchImpl),
    ackNotice: (notice) => callJson("POST", "/pcbk/api/auth/notice", { notice }, fetchImpl),
    // отчёт не повторяется (в том числе 404 старого core и 429 предела): потерянный отчёт дешевле шторма
    report: async (kind) => {
      try {
        await callJson("POST", "/pcbk/api/web/report", { kind }, fetchImpl);
      } catch {
        /* отчёт не ушёл — второй попытки нет */
      }
    },
  };
}

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object";

// ответ-отказ шлюза или edge ({"name", "data": {"message"}}) → текст для человека; те же ветки, что loginError
// страницы входа (сверка — tests/workplace.test.ts)
export function replyText(r: Reply): string {
  const data = isObject(r.body) ? r.body.data : undefined;
  const message = isObject(data) ? data.message : undefined;
  if (typeof message === "string" && message !== "") return message;
  if (!isObject(r.body) || (r.status >= 502 && r.status <= 504)) return T_DOWN;   // не JSON, сеть, шлюз edge
  return T_UNKNOWN;
}
