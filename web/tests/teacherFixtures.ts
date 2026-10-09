// Фикстуры кабинета преподавателя (Д10-R106, Д10-R110, m-2 р2): ответы ручек R101–R104 и учёток R108 и заглушка
// TeacherApi с очередями ответов. Их берут TeacherPage.test.tsx, App.test.tsx и TeacherAccounts.test.tsx: тестовые файлы
// друг друга не импортируют — импорт заново регистрировал бы чужие тесты. Теги — только 20FAKE_*, логины — учётки
// тестов, пароли — выдуманные строки тестов.
import { vi } from "vitest";
import type { Reply } from "../src/api";
import type { ShareChange, TeacherApi } from "../src/teacherApi";

export type Answer = [number, unknown];
export type Answers = Answer | Answer[];

const PLACES = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"];

// строка обзора (R101): у места со студентом — его логин и пометки; доля $100, пол и расход — ноль
const row = (place: string, patch: Record<string, unknown> = {}) => ({
  place, login: `stu-${place}`, free: false, disabled: false, noticed: true, locked: false, state: "sleeping",
  share_cents: 10000, floor_cents: 0, spent_usd: 0, agents: 0, ...patch,
});

const BUDGET = { total_cents: 100000, allocated_cents: 100000, free_cents: 0, spent_usd: 12.349, ops_usd: 0.5 };

// место 03 готово и уже потратило $12,349 (пол — 12,34 $); 06 не подтвердил предупреждение, 07 отключён, 08 под замком
// входа, 10 свободно (закрепления нет — учётки нет)
export const OVERVIEW_READY_03 = {
  places: PLACES.map((p) => {
    switch (p) {
      case "03": return row(p, { state: "ready", floor_cents: 1234, spent_usd: 12.349, agents: 2 });
      case "06": return row(p, { noticed: false });
      case "07": return row(p, { disabled: true });
      case "08": return row(p, { locked: true });
      case "10": return row(p, { login: null, free: true, disabled: null, noticed: null, locked: null });
      default: return row(p);
    }
  }),
  budget: BUDGET,
  shares_ok: true,
};

// все места спят; у 05 — студент
export const OVERVIEW_SLEEPING_05 = { places: PLACES.map((p) => row(p)), budget: BUDGET, shares_ok: true };

// учётки (Д10-R110, m-11): 04 свободно (закрепления нет); 07 — без учётки, но закреплено за удалённым логином
// (free: false — заводить туда нельзя); прочие — студенты
export const OVERVIEW_FREE_04 = {
  places: PLACES.map((p) => {
    switch (p) {
      case "04": return row(p, { login: null, free: true, disabled: null, noticed: null, locked: null });
      case "07": return row(p, { login: null, free: false, disabled: null, noticed: null, locked: null });
      default: return row(p);
    }
  }),
  budget: BUDGET,
  shares_ok: true,
};

const AT = Date.UTC(2026, 9, 11, 9, 0, 0);     // 11.10.2026 09:00 UTC, мс — как time.created OpenCode

export const SESSION = { id: "ses_" + "A1".repeat(13), title: "Значение 20FAKE_001_PV", created: AT, updated: AT + 60000,
                         archived: false };
export const SESSION_ARCHIVED = { id: "ses_" + "B2".repeat(13), title: "", created: AT - 86400000,
                                  updated: AT - 86000000, archived: true };

// стенограмма (R102): HTML и скрипт в текстах — веб показывает их только текстом
export const TRANSCRIPT_WITH_HTML = {
  session: SESSION,
  messages: [
    { role: "user", at: AT, agent: null, parts: [{ kind: "text", text: "<img src=x onerror=alert(1)>" }], error: null },
    { role: "assistant", at: AT + 5000, agent: "pomoshnik",
      parts: [{ kind: "tool", tool: "pcbk_tag_now", status: "completed", input: '{"tags": ["20FAKE_001_PV"]}',
                output: "<script>alert(2)</script> 20FAKE_001_PV = 1,5" },
              { kind: "text", text: "Готово: 20FAKE_001_PV = 1,5 <b>жирно</b>" }],
      error: null },
    { role: "assistant", at: AT + 9000, agent: "konstruktor", parts: [], error: "Ваша доля бюджета на модель израсходована" },
  ],
  clipped: true,
};

export const AGENTS_03 = {
  agents: [{ name: "pech-1", title: "Печь 1", node: "Узел печей", purpose: "Следит за 20FAKE_001_PV",
             tools: ["tag_now", "tag_period"], tags: ["20FAKE_001_PV"], state: "visible",
             created_at: "2026-10-11T08:00:00+00:00", visible_at: "2026-10-11T08:01:00+00:00" }],
};

export const JOURNAL_ROWS = {
  rows: [
    { id: 3, at: "2026-10-11T09:02:00+00:00", login: "t", action: "shares", place: null, target: null, outcome: "ok",
      detail: { "03": [10000, 8000], "05": [10000, 12000] } },
    { id: 2, at: "2026-10-11T09:01:00+00:00", login: "t", action: "read", place: "03", target: SESSION.id,
      outcome: "requested" },
    { id: 1, at: "2026-10-11T09:00:00+00:00", login: "t", action: "wake", place: "05", target: null, outcome: "requested" },
  ],
};

export interface TeacherAnswers {
  overview?: Answers; place?: Answers; wake?: Answers; sessions?: Answers; session?: Answers; agents?: Answers;
  agent?: Answers; journal?: Answers; shares?: Answers; stopPlace?: Answers;
  addStudent?: Answers; newPassword?: Answers; disable?: Answers; enable?: Answers; unlock?: Answers;   // учётки (R108)
}

// Один ответ — на каждый вызов, список — по очереди (последний повторяется), как api() в App.test.tsx. Ручки без
// ответа — 500; журнал без ответа — пустой (его кабинет спрашивает сам при открытии).
function queue(a: Answers | undefined, fallback: Answer): () => Promise<Reply> {
  const list: Answer[] = a === undefined ? [fallback] : typeof a[0] === "number" ? [a as Answer] : a as Answer[];
  let i = 0;
  return async () => {
    const [status, body] = list[Math.min(i, list.length - 1)]!;
    i += 1;
    return { status, body };
  };
}

const FAIL: Answer = [500, null];

export function teacherApi(o: TeacherAnswers = {}) {
  const q = (a: Answers | undefined, fallback: Answer = FAIL) => queue(a, fallback);
  const overview = q(o.overview);
  const place = q(o.place);
  const wake = q(o.wake);
  const sessions = q(o.sessions);
  const session = q(o.session);
  const agents = q(o.agents);
  const agent = q(o.agent);
  const journal = q(o.journal, [200, { rows: [] }]);
  const shares = q(o.shares);
  const stopPlace = q(o.stopPlace);
  const addStudent = q(o.addStudent);
  const newPassword = q(o.newPassword);
  const disable = q(o.disable);
  const enable = q(o.enable);
  const unlock = q(o.unlock);
  return {
    overview: vi.fn(() => overview()),
    place: vi.fn((_nn: string) => place()),
    wake: vi.fn((_nn: string) => wake()),
    sessions: vi.fn((_nn: string) => sessions()),
    session: vi.fn((_nn: string, _id: string) => session()),
    agents: vi.fn((_nn: string) => agents()),
    agent: vi.fn((_nn: string, _name: string) => agent()),
    journal: vi.fn(() => journal()),
    shares: vi.fn((_changes: ShareChange[]) => shares()),
    stopPlace: vi.fn((_nn: string, _to: string) => stopPlace()),
    addStudent: vi.fn((_login: string, _place: string) => addStudent()),
    newPassword: vi.fn((_login: string) => newPassword()),
    disable: vi.fn((_login: string) => disable()),
    enable: vi.fn((_login: string) => enable()),
    unlock: vi.fn((_login: string) => unlock()),
  } satisfies TeacherApi;
}
