// Чистые помощники кабинета преподавателя (Д10-R106): разбор ответов ручек R101–R104, деньги и перевод доли.
// Разбор строгий: ответ не той формы — null, и страница пишет «Неожиданный ответ сервера…» (T_TCH_UNKNOWN), а не рисует
// половину. Деньги — целые центы, как у сервера (Д10-R97); доллары расхода — числом как пришли. Правила перевода здесь —
// только подсказка до запроса: проверяет сервер, его отказ показывается дословно.
import { replyText, type Reply } from "./api";
import { isAgentName, isSessionId, type ShareChange } from "./teacherApi";
import { T_PLACE_GAVE_UP, T_PLACE_ODD, T_TCH_NO_SHARES, T_TCH_PLACE_GAVE_UP, T_TCH_PLACE_ODD, T_TCH_SAME_PLACE,
         T_TCH_TAKE_MAX, T_TCH_UNKNOWN, T_TCH_ZERO, T_UNKNOWN } from "./texts";

export { ACTION_LABELS } from "./texts";

export interface PlaceRow {
  place: string;                       // «01»…«10»
  login: string | null;                // учётка студента места; null — учётки нет
  free: boolean;                       // закрепления нет — место свободно (m-11)
  disabled: boolean | null;            // пометки учётки; null — учётки нет
  noticed: boolean | null;
  locked: boolean | null;
  state: string;                       // состояние места (workplaces.view): sleeping, starting, ready, failed
  share_cents: number | null;          // доля; null — доли не загружены или нет LLM-прокси
  floor_cents: number | null;          // пол доли — оплаченное вниз до цента (R97)
  spent_usd: number | null;            // потрачено местом, $
  agents: number | null;               // живых агентов места
}

export interface Budget { total_cents: number; allocated_cents: number; free_cents: number; spent_usd: number; ops_usd: number }

export interface Overview { places: PlaceRow[]; budget: Budget | null; shares_ok: boolean }

export interface SessionRow { id: string; title: string; created: number | null; updated: number | null; archived: boolean }

export type Part =
  | { kind: "text"; text: string }
  | { kind: "tool"; tool: string; status: string; input: string; output: string };

export interface Message {
  role: "user" | "assistant";
  at: number | null;
  agent: string | null;
  parts: Part[];
  error: string | null;
}

export interface Transcript { session: SessionRow; messages: Message[]; clipped: boolean }

export interface AgentCard {
  name: string; title: string; node: string; purpose: string; tools: string[]; tags: string[]; state: string;
  created_at: string | null; visible_at: string | null;
}

export interface JournalRow {
  id: number;
  at: string;                          // ISO с поясом
  login: string;
  action: string;
  place: string | null;
  target: string | null;
  outcome: string;
  detail: [string, number, number][] | null;   // только у shares: [место, было, станет] в центах, по номеру места
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => v !== null && typeof v === "object" && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isBool = (v: unknown): v is boolean => typeof v === "boolean";
const isCents = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const isUsd = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const isTime = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const orNull = <T>(v: unknown, ok: (x: unknown) => x is T): v is T | null => v === null || ok(v);
const isStrings = (v: unknown): v is string[] => Array.isArray(v) && v.every(isStr);
const PLACES = Array.from({ length: 10 }, (_, i) => String(i + 1).padStart(2, "0"));
const PLACE_KEY = /^(0[1-9]|10)$/;

// массив из разборщика элемента; один плохой элемент — весь ответ не по договору
function all<T>(v: unknown, one: (x: unknown) => T | null): T[] | null {
  if (!Array.isArray(v)) return null;
  const out: T[] = [];
  for (const x of v) {
    const r = one(x);
    if (r === null) return null;
    out.push(r);
  }
  return out;
}

function placeRow(v: unknown, nn: string): PlaceRow | null {
  if (!isObj(v) || v.place !== nn) return null;
  const { login, free, disabled, noticed, locked, state, share_cents, floor_cents, spent_usd, agents } = v;
  if (!orNull(login, isStr) || !isBool(free) || !orNull(disabled, isBool) || !orNull(noticed, isBool)
      || !orNull(locked, isBool) || !isStr(state) || !orNull(share_cents, isCents) || !orNull(floor_cents, isCents)
      || !orNull(spent_usd, isUsd) || !orNull(agents, isCents)) return null;
  return { place: nn, login, free, disabled, noticed, locked, state, share_cents, floor_cents, spent_usd, agents };
}

function budgetOf(v: unknown): Budget | null {
  if (!isObj(v)) return null;
  const { total_cents, allocated_cents, free_cents, spent_usd, ops_usd } = v;
  if (!isCents(total_cents) || !isCents(allocated_cents) || !isCents(free_cents) || !isUsd(spent_usd) || !isUsd(ops_usd)) {
    return null;
  }
  return { total_cents, allocated_cents, free_cents, spent_usd, ops_usd };
}

// R101: ровно десять мест, 01…10 по порядку; budget — объект или null (без LLM-прокси или долей)
export function parseOverview(body: unknown): Overview | null {
  if (!isObj(body) || !Array.isArray(body.places) || body.places.length !== PLACES.length || !isBool(body.shares_ok)) {
    return null;
  }
  const places: PlaceRow[] = [];
  for (const [i, nn] of PLACES.entries()) {
    const row = placeRow(body.places[i], nn);
    if (row === null) return null;
    places.push(row);
  }
  let budget: Budget | null = null;
  if (body.budget !== null) {
    budget = budgetOf(body.budget);
    if (budget === null) return null;
  }
  return { places, budget, shares_ok: body.shares_ok };
}

function sessionRow(v: unknown): SessionRow | null {
  if (!isObj(v)) return null;
  const { id, title, created, updated, archived } = v;
  if (!isStr(id) || !isSessionId(id) || !orNull(title, isStr) || !orNull(created, isTime) || !orNull(updated, isTime)
      || !isBool(archived)) return null;
  return { id, title: title ?? "", created, updated, archived };
}

// R102: {"sessions": [{id, title, created, updated, archived}]}; id — только ses_ и 26 знаков (из него строится путь)
export function parseSessions(body: unknown): SessionRow[] | null {
  return isObj(body) ? all(body.sessions, sessionRow) : null;
}

function part(v: unknown): Part | null {
  if (!isObj(v)) return null;
  if (v.kind === "text") return isStr(v.text) ? { kind: "text", text: v.text } : null;
  if (v.kind === "tool") {
    const { tool, status, input, output } = v;
    if (!isStr(tool) || !isStr(status) || !orNull(input, isStr) || !orNull(output, isStr)) return null;
    return { kind: "tool", tool, status, input: input ?? "", output: output ?? "" };
  }
  return null;
}

function message(v: unknown): Message | null {
  if (!isObj(v) || (v.role !== "user" && v.role !== "assistant")) return null;
  const { at, agent, error } = v;
  const parts = all(v.parts, part);
  if (parts === null || !orNull(at, isTime) || !orNull(agent, isStr) || !orNull(error, isStr)) return null;
  return { role: v.role, at, agent, parts, error };
}

// R102: стенограмма — {"session", "messages", "clipped"}; тексты — как есть, экранирует отрисовка (только текстом)
export function parseTranscript(body: unknown): Transcript | null {
  if (!isObj(body) || !isBool(body.clipped)) return null;
  const session = sessionRow(body.session);
  const messages = all(body.messages, message);
  if (session === null || messages === null) return null;
  return { session, messages, clipped: body.clipped };
}

function agentCard(v: unknown): AgentCard | null {
  if (!isObj(v)) return null;
  const { name, title, node, purpose, tools, tags, state, created_at, visible_at } = v;
  if (!isStr(name) || !isAgentName(name) || !isStr(title) || !isStr(node) || !isStr(purpose) || !isStrings(tools)
      || !isStrings(tags) || !isStr(state) || !orNull(created_at, isStr) || !orNull(visible_at, isStr)) return null;
  return { name, title, node, purpose, tools, tags, state, created_at, visible_at };
}

// R103: {"agents": [карточка]}; имя — по NAME_RE (из него строится путь чтения файла)
export function parseAgents(body: unknown): AgentCard[] | null {
  return isObj(body) ? all(body.agents, agentCard) : null;
}

// R103: {"name", "text"} — текст файла агента
export function parseAgentText(body: unknown): string | null {
  return isObj(body) && isStr(body.name) && isStr(body.text) ? body.text : null;
}

// detail строки shares: {"03": [было, станет], …} — только пары центов по номерам мест; прочие ключи (kind) не показываются
function shareDetail(v: unknown): [string, number, number][] | null {
  if (!isObj(v)) return null;
  const out: [string, number, number][] = [];
  for (const [key, pair] of Object.entries(v)) {
    if (PLACE_KEY.test(key) && Array.isArray(pair) && pair.length === 2 && isCents(pair[0]) && isCents(pair[1])) {
      out.push([key, pair[0], pair[1]]);
    }
  }
  return out.sort((a, b) => a[0].localeCompare(b[0]));
}

function journalRow(v: unknown): JournalRow | null {
  if (!isObj(v)) return null;
  const { id, at, login, action, place, target, outcome } = v;
  if (!isCents(id) || !isStr(at) || !isStr(login) || !isStr(action) || !orNull(place, isStr) || !orNull(target, isStr)
      || !isStr(outcome)) return null;
  return { id, at, login, action, place, target, outcome, detail: action === "shares" ? shareDetail(v.detail) : null };
}

// R101 (I-1): {"rows": [{id, at, login, action, place, target, outcome, detail?}]}, новые первыми
export function parseJournal(body: unknown): JournalRow[] | null {
  return isObj(body) ? all(body.rows, journalRow) : null;
}

// --- подписи и тексты кабинета ---

// подпись кода из словаря: только свои ключи словаря (ревью А4, m-6) — «constructor» с сервера дал бы функцию вместо
// текста; незнакомый код — как пришёл
export const label = (dict: Readonly<Record<string, string>>, key: string): string =>
  Object.hasOwn(dict, key) ? dict[key]! : key;

// общие тексты чата для студента («…сообщите преподавателю») — кабинетным (ревью А4, m-4); прочие — как есть
const FOR_TEACHER: ReadonlyMap<string, string> = new Map([
  [T_UNKNOWN, T_TCH_UNKNOWN], [T_PLACE_GAVE_UP, T_TCH_PLACE_GAVE_UP], [T_PLACE_ODD, T_TCH_PLACE_ODD],
]);
export const forTeacher = (text: string): string => FOR_TEACHER.get(text) ?? text;

// отказ ручки кабинета — текстом для преподавателя: текст сервера дословно, иначе свой запасной (replyText)
export const tchReplyText = (r: Reply): string => forTeacher(replyText(r));

// --- деньги: центы и доллары ru-RU (1 234,56 $) ---

const MONEY = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtCents = (cents: number): string => `${MONEY.format(cents / 100)} $`;
export const fmtUsd = (usd: number): string => `${MONEY.format(usd)} $`;

// «12,34» или «12.3» → центы; не больше двух знаков после запятой, не меньше нуля, иначе null
const MONEY_RE = /^(\d{1,9})(?:[.,](\d{1,2}))?$/;
export function parseMoney(text: string): number | null {
  const m = MONEY_RE.exec(text.trim());
  if (m === null) return null;
  return Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
}

// --- время: местное браузера ---

const WHEN = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit",
                                                 minute: "2-digit" });

// мс от эпохи (time.created OpenCode) или ISO; не время — «—»
export function fmtWhen(v: number | string | null): string {
  const ms = typeof v === "string" ? Date.parse(v) : v;
  return ms === null || !Number.isFinite(ms) ? "—" : WHEN.format(new Date(ms));
}

// --- перевод доли (R104): два изменения — с места минус, на место плюс ---

// Взять с места можно не больше «доля − пол» (B-1): пол — оплаченное, его сервер берёт заново под замком перевода и сам
// отказывает below_spent. Здесь — подсказка до запроса, чтобы не тратить ход на заведомый отказ.
export function transferChanges(o: Overview, from: string, to: string, cents: number): ShareChange[] | string {
  const src = o.places.find((p) => p.place === from);
  const dst = o.places.find((p) => p.place === to);
  if (src === undefined || dst === undefined || from === to) return T_TCH_SAME_PLACE;
  if (!Number.isSafeInteger(cents) || cents <= 0) return T_TCH_ZERO;
  if (o.budget === null || !o.shares_ok || src.share_cents === null || dst.share_cents === null) return T_TCH_NO_SHARES;
  const available = Math.max(0, src.share_cents - (src.floor_cents ?? 0));
  if (cents > available) return T_TCH_TAKE_MAX(from, fmtCents(available));
  return [
    { place: from, from_cents: src.share_cents, to_cents: src.share_cents - cents },
    { place: to, from_cents: dst.share_cents, to_cents: dst.share_cents + cents },
  ];
}
