// Вызовы кабинета преподавателя (Д10-R106): ручки /pcbk/api/teacher/* (R101–R104) поверх callJson — тот же вид запроса,
// что у Api чата (cookie сессии, Accept JSON, тело — JSON), и тот же Reply. Отдельный интерфейс: в Api его нет, и
// заглушки Api прежних тестов не меняются. Роль решает сервер: кабинет только просит.
// Части пути — номер места, id разговора, имя агента — уходят только проверенными по шаблонам шлюза (NN — 01…10, ses_ и
// 26 знаков, имя агента — NAME_RE черновика): иначе вызов не уходит, ответ — 400 без запроса. Сервер проверяет сам
// (R100, шаг 6), это второй рубеж: ввод в путь как есть не идёт и отсюда.
import { callJson, type JsonFetch, type Reply } from "./api";
import { T_TCH_BAD_PART } from "./texts";

// изменение доли места (R104): «было» — для сверки двух вкладок, «станет» — целые центы
export interface ShareChange { place: string; from_cents: number; to_cents: number }

export interface TeacherApi {
  overview(): Promise<Reply>;                         // обзор мест и бюджета (R101)
  place(nn: string): Promise<Reply>;                  // состояние места — опрос, пока просыпается (R102)
  wake(nn: string): Promise<Reply>;                   // пробуждение для просмотра: 202 и состояние (R102)
  sessions(nn: string): Promise<Reply>;               // разговоры места, и из архива (R102)
  session(nn: string, id: string): Promise<Reply>;    // стенограмма разговора (R102)
  agents(nn: string): Promise<Reply>;                 // агенты места из учёта (R103)
  agent(nn: string, name: string): Promise<Reply>;    // текст файла агента (R103)
  journal(): Promise<Reply>;                          // последние строки журнала действий (R101, I-1)
  shares(changes: ShareChange[]): Promise<Reply>;     // перевод доли: тело ровно {"changes": [...]} (R104)
  stopPlace(nn: string, to: string): Promise<Reply>;  // «Остановить место»: тело ровно {"place", "to"}, пол считает сервер (I-1 р2)
}

const PREFIX = "/pcbk/api/teacher";
const PLACE_RE = /^(0[1-9]|10)$/;
const SES_RE = /^ses_[0-9A-Za-z]{26}$/;                // ID_RE["ses"] правил шлюза
const AGENT_RE = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;    // NAME_RE черновика агента (agents/draft.py)
const AGENT_MIN = 3;
const AGENT_MAX = 40;

export const isPlace = (nn: string): boolean => PLACE_RE.test(nn);
export const isSessionId = (id: string): boolean => SES_RE.test(id);
export const isAgentName = (name: string): boolean =>
  name.length >= AGENT_MIN && name.length <= AGENT_MAX && AGENT_RE.test(name);

// отказ без запроса — тем же видом, что отказ шлюза
const refused = async (): Promise<Reply> => ({ status: 400, body: { name: "BadRequest", data: { message: T_TCH_BAD_PART } } });

export function createTeacherApi(fetchImpl?: JsonFetch): TeacherApi {
  const get = (path: string): Promise<Reply> => callJson("GET", PREFIX + path, undefined, fetchImpl);
  const post = (path: string, body?: unknown): Promise<Reply> => callJson("POST", PREFIX + path, body, fetchImpl);
  const atPlace = (nn: string, rest: string, ok = true): Promise<Reply> =>
    isPlace(nn) && ok ? get(`/places/${nn}${rest}`) : refused();
  return {
    overview: () => get("/overview"),
    place: (nn) => atPlace(nn, ""),
    wake: (nn) => (isPlace(nn) ? post(`/places/${nn}/wake`) : refused()),
    sessions: (nn) => atPlace(nn, "/sessions"),
    session: (nn, id) => atPlace(nn, `/sessions/${id}`, isSessionId(id)),
    agents: (nn) => atPlace(nn, "/agents"),
    agent: (nn, name) => atPlace(nn, `/agents/${name}`, isAgentName(name)),
    journal: () => get("/journal"),
    // ровно три ключа на изменение: лишний ключ шлюз отклонил бы (400)
    shares: (changes) => post("/shares", { changes: changes.map(({ place, from_cents, to_cents }) => ({ place, from_cents, to_cents })) }),
    stopPlace: (nn, to) => (isPlace(nn) && isPlace(to) ? post("/shares/floor", { place: nn, to }) : refused()),
  };
}
