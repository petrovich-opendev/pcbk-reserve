// Вкладка «События» (Д13, DESIGN п.5, п.7): договор ответа GET /pcbk/api/events и разбор одного события.
// Сети и React здесь нет — их дают EventsPage и createApi.
//
// Ответ службы (DESIGN п.5): {"events": [{"id", "ts", "agent", "agent_title", "kind", "title", "tags": [...],
// "period_start", "period_end", "value", "setpoint", "text"}], "limit": 100}; новые первыми. Поля period_start,
// period_end, value, setpoint — необязательные (их можно не прислать) или null; в EventItem и то, и то — null.

export type EventKind = "deviation" | "note";
const KINDS: readonly EventKind[] = ["deviation", "note"];

export interface EventItem {
  id: number;
  ts: string;                   // момент записи — ISO 8601, показывается местным временем браузера
  agent: string;                 // системное имя агента
  agentTitle: string;            // название — из учёта; архив — прежнее, не найден — имя (agent_title службы)
  kind: EventKind;
  title: string;
  tags: string[];
  periodStart: string | null;
  periodEnd: string | null;
  value: number | null;
  setpoint: number | null;
  text: string;
}

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string" && v !== "";
const isCount = (v: unknown): boolean => typeof v === "number" && Number.isInteger(v) && v >= 0;
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullableStr = (v: unknown): string | null => (typeof v === "string" ? v : null);
const nullableNum = (v: unknown): number | null => (isNum(v) ? v : null);

// ответ ручки {"events": [...], "limit": N} → список; не по договору (HTML прокси, чужая форма) — null.
// Лишние поля элемента не переносятся.
export function parseEvents(body: unknown): EventItem[] | null {
  if (!isObject(body) || !Array.isArray(body.events) || !isCount(body.limit)) return null;
  const items: EventItem[] = [];
  for (const e of body.events) {
    if (!isObject(e)) return null;
    const { id, ts, agent, agent_title: agentTitle, kind, title, tags, text } = e;
    if (!isCount(id) || !isText(ts) || !isText(agent) || !isText(agentTitle)) return null;
    if (typeof kind !== "string" || !KINDS.includes(kind as EventKind)) return null;
    if (!isText(title) || typeof text !== "string" || text === "") return null;
    if (!Array.isArray(tags) || !tags.every((t): t is string => typeof t === "string" && t !== "")) return null;
    items.push({
      id: id as number, ts, agent, agentTitle, kind: kind as EventKind, title, tags: [...tags], text,
      periodStart: nullableStr(e.period_start), periodEnd: nullableStr(e.period_end),
      value: nullableNum(e.value), setpoint: nullableNum(e.setpoint),
    });
  }
  return items;
}

// момент записи — местное время браузера (не историана): «ДД.ММ.ГГГГ, ЧЧ:ММ»; не строка или не разбирается — «—»
export function fmtLocal(iso: unknown): string {
  if (typeof iso !== "string") return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}
