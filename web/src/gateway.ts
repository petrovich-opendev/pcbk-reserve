// Обёртка fetch для SDK OpenCode (Д6-R43): SDK зовёт её на каждый запрос с готовым Request.
// - Разбор отказа (classify) — по коду и полю name тела вида OpenCode ({"name", "data": {"message"}}).
// - Поток /pcbk/api/oc/event: перед запросом — пауза по числу сбоев 1→30 с с разбросом; ответ 200 счёт сразу
//   не сбрасывает — итог решает следующий запрос потока: поток прожил не меньше SHORT_STREAM_S — живой, иначе
//   сбой (адаптер переподключается сам раз в секунду, и без пауз вышел бы шторм, Review Focus 2).
// - 401 и 503 WorkplaceNotReady — остановка до размонтирования: вызов, остановивший обёртку, получает свой
//   ответ, поток дальше ждёт отмены и в сеть не идёт, прочие вызовы сразу отвергаются HaltedError.
// - 503 WorkplaceBusy обычного вызова (limit_conn edge — 10 на cookie, общий для вкладок; очередь места;
//   бюджет тел) — до места запрос не дошёл: повтор с паузой, не больше BUSY_RETRIES раз.
// Тело ответа не оборачивается: отказ читается копией (res.clone()), и только при content-type JSON.
import {
  T_ASLEEP, T_BUSY, T_DOWN, T_OFFLINE, T_SIGNED_OUT, T_STREAM_RETRY, T_TOO_MANY_TABS, T_UNKNOWN,
} from "./texts";

export type Outcome =
  | "ok" | "signed_out" | "asleep" | "too_many_tabs" | "busy" | "down" | "refused" | "offline" | "unknown" | "halted";

export const EVENT_PATH = "/pcbk/api/oc/event";
export const SHORT_STREAM_S = 5;          // поток короче — сбой, чем бы ни кончился (раунд 2, Minor 4)
export const EVENT_BASE_MS = 1000;
export const EVENT_MAX_MS = 30000;
export const BUSY_RETRIES = 3;            // повторов 503 WorkplaceBusy у обычного вызова; паузы 1, 2, 4 с с разбросом

// Обёртка остановлена (после 401 или «место спит»): вызов не ушёл в сеть. Веб его не показывает.
export class HaltedError extends Error {
  constructor() {
    super("gateway client halted");
    this.name = "HaltedError";
  }
}

export interface StreamState { failures: number; nextRetryS: number | null; last: Outcome }

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object";

// тело вида OpenCode: {"name": "...", "data": {...}}; message — пустая строка, если его нет
function ocError(body: unknown): { name: string; message: string } | null {
  if (!isObject(body) || typeof body.name !== "string" || !isObject(body.data)) return null;
  const message = body.data.message;
  return { name: body.name, message: typeof message === "string" ? message : "" };
}

// ответ → класс (таблица Д6-R43)
export function classify(status: number, body: unknown): Outcome {
  if (status >= 200 && status < 300) return "ok";
  if (status === 401) return "signed_out";              // с любым телом
  const err = ocError(body);
  if (err === null) return "unknown";
  if (status === 503) {
    if (err.name === "WorkplaceNotReady") return "asleep";
    if (err.name === "WorkplaceBusy") return "busy";    // и 509 limit_conn edge — он отдаёт его как 503 busy
    if (err.name === "Unavailable") return "down";
    return "unknown";
  }
  if (status === 429 && err.name === "TooManyRequests") return "too_many_tabs";
  if ((status >= 400 && status < 500) || status === 502) return "refused";
  return "unknown";
}

function textFor(kind: Outcome, body: unknown): string {
  const message = ocError(body)?.message ?? "";
  switch (kind) {
    case "ok":
    case "halted": return "";
    case "signed_out": return T_SIGNED_OUT;
    case "asleep": return T_ASLEEP;
    case "too_many_tabs": return message || T_TOO_MANY_TABS;
    case "busy": return message || T_BUSY;
    case "down": return T_DOWN;
    case "refused": return message || T_UNKNOWN;
    case "offline": return T_OFFLINE;
    case "unknown": return T_UNKNOWN;
  }
}

// Ошибка вызова SDK → класс и текст для строки ошибки. SDK (wrapClientError) кладёт код и тело отказа в
// error.cause; сеть — TypeError fetch или «network error (no response)»; HaltedError и своя отмена
// (AbortError: «Стоп», размонтирование) — halted без текста.
export function explainError(error: unknown): { kind: Outcome; text: string } {
  if (error instanceof HaltedError) return { kind: "halted", text: "" };
  if (isObject(error) && error.name === "AbortError") return { kind: "halted", text: "" };
  if (error instanceof TypeError) return { kind: "offline", text: T_OFFLINE };
  if (error instanceof Error) {
    const cause = error.cause;
    if (isObject(cause) && typeof cause.status === "number") {
      const kind = classify(cause.status, cause.body);
      return { kind, text: textFor(kind, cause.body) };
    }
    if (error.message.includes("network error (no response)")) return { kind: "offline", text: T_OFFLINE };
  }
  return { kind: "unknown", text: T_UNKNOWN };
}

// пауза перед запросом потока: 0 без сбоев, иначе min(1000·2^(n−1), 30000)·(0,8 + 0,4·random())
export function eventDelayMs(failures: number, random: () => number): number {
  if (failures <= 0) return 0;
  return Math.round(Math.min(EVENT_BASE_MS * 2 ** (failures - 1), EVENT_MAX_MS) * (0.8 + 0.4 * random()));
}

// строка потока над полем ввода; null — строки нет (поток жив или ещё не открыт)
export function streamLine(s: StreamState): string | null {
  if (s.nextRetryS === null) return null;
  if (s.last === "too_many_tabs") return T_TOO_MANY_TABS;   // текст шлюза: студенту — закрыть лишние вкладки
  return T_STREAM_RETRY(s.nextRetryS);
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

// пауза, которую прерывает отмена сигналом запроса
function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError(signal));
      return;
    }
    if (ms <= 0) {
      resolve();
      return;
    }
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(abortError(signal));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

// остановленный поток: ждёт только отмены (размонтирование закрывает поток адаптера)
function untilAborted(signal: AbortSignal): Promise<never> {
  return new Promise<never>((_, reject) => {
    if (signal.aborted) reject(abortError(signal));
    else signal.addEventListener("abort", () => reject(abortError(signal)), { once: true });
  });
}

const isJson = (res: Response): boolean =>
  (res.headers.get("content-type") ?? "").toLowerCase().includes("application/json");

export function createGatewayFetch(o: {
  onHalt(kind: "signed_out" | "asleep"): void;
  onStream(s: StreamState): void;
  fetchImpl?: (r: Request) => Promise<Response>;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
  now?: () => number;
}): (req: Request) => Promise<Response> {
  const fetchImpl = o.fetchImpl ?? ((r: Request) => fetch(r));
  const sleep = o.sleep ?? abortableSleep;
  const random = o.random ?? Math.random;
  const now = o.now ?? (() => performance.now());         // монотонные часы

  let halted = false;
  let failures = 0;
  let nextDelayMs = 0;                 // пауза перед следующим запросом потока
  let okAt: number | null = null;      // now() последнего ответа 200 потока, ещё не оценённого
  let last: Outcome = "ok";

  const report = (): void => {
    o.onStream({ failures, nextRetryS: nextDelayMs > 0 ? Math.ceil(nextDelayMs / 1000) : null, last });
  };

  const halt = (kind: "signed_out" | "asleep"): void => {
    if (halted) return;
    halted = true;
    o.onHalt(kind);
  };

  // класс ответа; тело отказа — копией и только JSON, иначе класс по одному коду
  const outcome = async (res: Response): Promise<Outcome> => {
    if (res.ok) return "ok";
    let body: unknown;
    if (isJson(res)) {
      try {
        body = await res.clone().json();
      } catch {
        body = undefined;
      }
    }
    return classify(res.status, body);
  };

  const streamFailed = (kind: Outcome): void => {
    failures += 1;
    last = kind;
    nextDelayMs = eventDelayMs(failures, random);
    report();
  };

  async function event(req: Request): Promise<Response> {
    if (okAt !== null) {               // прошлый поток ответил 200: живой или короткий — решает время до этого запроса
      const lived = now() - okAt;
      okAt = null;
      if (lived >= SHORT_STREAM_S * 1000) {
        failures = 0;                  // живой (в том числе конец по сроку жизни шлюза, closed_by=lifetime)
        nextDelayMs = 0;
      } else {
        failures += 1;                 // короткий — сбой, концом он кончился или обрывом
        nextDelayMs = eventDelayMs(failures, random);
        report();
      }
    }
    if (halted) return untilAborted(req.signal);
    await sleep(nextDelayMs, req.signal);
    if (halted) return untilAborted(req.signal);          // остановили, пока ждали паузу
    let res: Response;
    try {
      res = await fetchImpl(req);
    } catch (error) {
      if (!req.signal.aborted) streamFailed("offline");    // своя отмена — не сбой
      throw error;
    }
    const kind = await outcome(res);
    if (kind === "ok") {
      okAt = now();
      last = "ok";
      nextDelayMs = 0;                 // строка гаснет; счёт сбоев — до следующего запроса потока
      report();
      return res;
    }
    if (kind === "signed_out" || kind === "asleep") {
      halt(kind);
      return res;
    }
    streamFailed(kind);
    return res;
  }

  async function call(req: Request): Promise<Response> {
    for (let attempt = 0; ; attempt += 1) {
      if (halted) throw new HaltedError();
      // тело запроса читается один раз: для повтора уходит копия, последняя попытка — сам запрос
      const res = await fetchImpl(attempt < BUSY_RETRIES ? req.clone() : req);
      const kind = await outcome(res);
      if (kind === "signed_out" || kind === "asleep") {
        halt(kind);
        return res;
      }
      if (kind !== "busy" || attempt >= BUSY_RETRIES) return res;
      void res.body?.cancel().catch(() => undefined);     // отказ busy не нужен: повтор
      await sleep(eventDelayMs(attempt + 1, random), req.signal);
    }
  }

  return (req: Request): Promise<Response> =>
    new URL(req.url).pathname === EVENT_PATH ? event(req) : call(req);
}
