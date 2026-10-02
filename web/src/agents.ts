// Агенты места (Д7-R64, R65): договор ответа GET /pcbk/api/agents, агент разговора по истории, агент хода и
// расписание опроса списка. Сети и React здесь нет — их дают Chat и createApi.
//
// Агент хода (Review Focus 1 Д7б): у начатого разговора — агент его первого сообщения студента (threadAgent), у
// нового — выбранный в панели (newAgent). В prompt_async помощник уходит без agent: место берёт свой default_agent
// (sendAgent). Агента вне списка (архив) веб не подменяет — его отвергает шлюз 400 «Агент «…» недоступен» (R65).
import type { OpenCodeThreadState } from "@assistant-ui/react-opencode";

export const DEFAULT_AGENT = "pomoshnik";             // default_agent места (student/config/opencode.json)

export type AgentKind = "builtin" | "own";
export type AgentState = "ready" | "pending" | "broken";

export interface AgentItem {
  name: string;               // системное имя — его шлёт prompt_async
  title: string;              // название в панели и в строке «Агент: …»
  purpose: string;            // назначение под названием
  kind: AgentKind;            // встроенный (образ места) или свой (Конструктор)
  state: AgentState;          // ready — выбирается; pending — место его ещё не видит; broken — не загрузился
}

const KINDS: readonly string[] = ["builtin", "own"];
const STATES: readonly string[] = ["ready", "pending", "broken"];

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string" && v !== "";
const isCount = (v: unknown): boolean => typeof v === "number" && Number.isInteger(v) && v >= 0;

// ответ ручки {"agents": [...], "own": k, "limit": N} → список; не по договору (HTML прокси, чужая форма) — null.
// Лишние поля элемента не переносятся.
export function parseAgents(body: unknown): AgentItem[] | null {
  if (!isObject(body) || !Array.isArray(body.agents) || !isCount(body.own) || !isCount(body.limit)) return null;
  const items: AgentItem[] = [];
  for (const a of body.agents) {
    if (!isObject(a)) return null;
    const { name, title, purpose, kind, state } = a;
    if (!isText(name) || !isText(title) || typeof purpose !== "string") return null;
    if (typeof kind !== "string" || !KINDS.includes(kind) || typeof state !== "string" || !STATES.includes(state)) return null;
    items.push({ name, title, purpose, kind: kind as AgentKind, state: state as AgentState });
  }
  return items;
}

// агент разговора — info.agent первого сообщения студента в messageOrder; сообщения без info (пришли части, а не
// само сообщение) не в счёт. У первого сообщения студента агента нет — неизвестно (undefined), а не агент следующего
export function threadAgent(state: OpenCodeThreadState | undefined): string | undefined {
  if (state === undefined) return undefined;
  for (const id of state.messageOrder) {
    const info = state.messagesById[id]?.info;
    if (info === undefined || info.role !== "user") continue;
    const agent: unknown = info.agent;
    return isText(agent) ? agent : undefined;
  }
  return undefined;
}

// агент в prompt_async: помощник и «не задан» — без поля (место берёт default_agent), прочие — по имени
export function sendAgent(name: string | undefined): string | undefined {
  return name === undefined || name === DEFAULT_AGENT ? undefined : name;
}

export function needsPoll(items: AgentItem[]): boolean {
  return items.some((a) => a.state === "pending");
}

// --- расписание опроса (R65) ---
// Список читается при монтировании (start); через AGENTS_AFTER_TURN_MS после конца хода (turnEnded); пока в
// последнем ответе 200 есть pending — раз в AGENTS_PENDING_MS, но не позже AGENTS_PENDING_FOR_MS от последнего конца
// хода. До первого конца хода опроса нет, даже если pending есть с монтирования: якорь — конец хода, и он у каждого
// запуска свой (m16; новое монтирование чата — новый опрос без якоря). Сбой ручки (null) показывается строкой, а
// опрос идёт по последнему ответу 200: агент, ждущий места, появится, когда ручка ответит снова; без ответа 200
// (старый core — 404) опроса по pending нет. Ответы применяются только новее уже применённого.
export const AGENTS_AFTER_TURN_MS = 1000;
export const AGENTS_PENDING_MS = 3000;
export const AGENTS_PENDING_FOR_MS = 60_000;

export interface AgentsPollOptions {
  load(): Promise<AgentItem[] | null>;              // null — ответ не 200 или не по договору
  onItems(items: AgentItem[] | null): void;
  now(): number;
  setTimer(fn: () => void, ms: number): unknown;
  clearTimer(handle: unknown): void;
}

export interface AgentsPoll {
  start(): void;              // монтирование: загрузка сразу, якоря нет
  turnEnded(): void;          // ход кончился (isRunning true → false)
  stop(): void;               // размонтирование или остановка обёртки (401, «место спит»): ни таймеров, ни ответов
}

export function createAgentsPoll(o: AgentsPollOptions): AgentsPoll {
  let stopped = false;
  let timer: unknown = null;
  let anchor: number | null = null;                 // конец последнего хода
  let started = 0;                                  // номер последней начатой загрузки
  let applied = 0;                                  // номер последней применённой
  let lastGood: AgentItem[] | null = null;          // последний ответ 200 — по нему решается опрос

  const clear = (): void => {
    if (timer !== null) o.clearTimer(timer);
    timer = null;
  };
  const schedule = (ms: number): void => {
    clear();
    timer = o.setTimer(() => {
      timer = null;
      run();
    }, ms);
  };
  const run = (): void => {
    if (stopped) return;
    started += 1;
    const seq = started;
    void Promise.resolve()
      .then(() => o.load())
      .catch(() => null)
      .then((items) => {
        if (stopped || seq < applied) return;       // остановлен или уже применён ответ новее
        applied = seq;
        if (items !== null) lastGood = items;
        o.onItems(items);
        if (seq !== started || timer !== null) return;   // следующий шаг решит загрузка новее или взведённый таймер
        if (anchor === null || lastGood === null || !needsPoll(lastGood)) return;
        if (o.now() + AGENTS_PENDING_MS <= anchor + AGENTS_PENDING_FOR_MS) schedule(AGENTS_PENDING_MS);
      });
  };

  return {
    start(): void {
      anchor = null;
      run();
    },
    turnEnded(): void {
      if (stopped) return;
      anchor = o.now();
      schedule(AGENTS_AFTER_TURN_MS);
    },
    stop(): void {
      stopped = true;
      clear();
    },
  };
}
