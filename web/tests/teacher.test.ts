// @vitest-environment node
// Кабинет преподавателя (Д10-R106): чистые помощники — разбор ответов R101–R104, деньги, перевод доли — и пути и тела
// вызовов TeacherApi.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { ACTION_LABELS, fmtCents, parseAgents, parseJournal, parseMoney, parseOverview, parseSessions, parseTranscript,
         transferChanges } from "../src/teacher";
import { createTeacherApi, isPlace, PLACES } from "../src/teacherApi";
import { AGENTS_03, JOURNAL_ROWS, OVERVIEW_READY_03, SESSION, SESSION_ARCHIVED, TRANSCRIPT_WITH_HTML } from "./teacherFixtures";

const row = (place: string, share: number, floor = 0, spent = 0) =>
  ({ place, login: `stu-${place}`, free: false, disabled: false, noticed: true, locked: false, state: "sleeping",
     share_cents: share, floor_cents: floor, spent_usd: spent, agents: 0 });
const overview = {
  places: ["01","02","03","04","05","06","07","08","09","10","11"].map((p) =>   // Д15б: 11 мест
    p === "03" ? row(p, 10000, 1234, 12.349) : row(p, 10000)),
  budget: { total_cents: 100000, allocated_cents: 100000, free_cents: 0, spent_usd: 12.349, ops_usd: 0 },
  shares_ok: true,
};

test("money_format_and_parse", () => {
  expect(fmtCents(123456).replace(/\s/g, " ")).toBe("1 234,56 $");
  expect([parseMoney("12,34"), parseMoney("12.3"), parseMoney("0"), parseMoney("1,234"), parseMoney("-1"),
          parseMoney("abc"), parseMoney("")]).toEqual([1234, 1230, 0, null, null, null, null]);
});

test("transfer_builds_two_changes_or_says_why", () => {
  const o = parseOverview(overview)!;
  expect(transferChanges(o, "03", "05", 2000)).toEqual([
    { place: "03", from_cents: 10000, to_cents: 8000 }, { place: "05", from_cents: 10000, to_cents: 12000 }]);
  expect(transferChanges(o, "03", "03", 100)).toBe("Выберите два разных места");
  expect(transferChanges(o, "03", "05", 0)).toBe("Сумма должна быть больше нуля");
  expect(transferChanges(o, "03", "05", 9000)).toBe("У места 03 можно взять не больше 87,66 $");  // доля − пол (B-1)
  expect(transferChanges(o, "03", "05", 8766)).toEqual([
    { place: "03", from_cents: 10000, to_cents: 1234 }, { place: "05", from_cents: 10000, to_cents: 18766 }]);
});

test("parsers_reject_wrong_shapes", () => {
  expect(parseOverview({ places: [] })).toBeNull();
  expect(parseOverview({ ...overview, places: overview.places.slice(0, 9) })).toBeNull();
  expect(parseOverview({ ...overview, places: overview.places.map((p) => ({ ...p, floor_cents: "1" })) })).toBeNull();
  expect(parseTranscript({ session: {}, messages: "x", clipped: false })).toBeNull();
  expect(parseJournal({ rows: [{ id: 1, at: "2026-10-11T09:00:00+00:00", login: "t", action: "read", place: "03",
                                 target: "ses_x", outcome: "requested" }] })!.length).toBe(1);
  expect(parseJournal({ rows: "x" })).toBeNull();
});

// --- сверх брифа: формы, которые веб принимает и отвергает, и договор вызовов ---

test("overview_without_llm_and_places_order", () => {
  // R101: без LLM-прокси или с незагруженными долями доли, пол, расход и бюджет — null; обзор работает и так
  const none = { places: overview.places.map((p) => ({ ...p, share_cents: null, floor_cents: null, spent_usd: null })),
                 budget: null, shares_ok: false };
  const o = parseOverview(none)!;
  expect(o.budget).toBeNull();
  expect(o.places[2]!.share_cents).toBeNull();
  expect(transferChanges(o, "03", "05", 100)).toBe("Доли бюджета недоступны — перевод закрыт");
  // места — ровно 01…11 по порядку; доля — целые центы не меньше нуля
  const swapped = [...overview.places];
  [swapped[0], swapped[1]] = [swapped[1]!, swapped[0]!];
  expect(parseOverview({ ...overview, places: swapped })).toBeNull();
  expect(parseOverview({ ...overview, places: overview.places.map((p) => ({ ...p, share_cents: 10.5 })) })).toBeNull();
  expect(parseOverview({ ...overview, places: overview.places.map((p) => ({ ...p, share_cents: -1 })) })).toBeNull();
  expect(parseOverview({ ...overview, budget: { ...overview.budget, total_cents: "1" } })).toBeNull();
  // свободное место: логина нет, пометки — null
  const free = parseOverview(OVERVIEW_READY_03)!.places[9]!;
  expect([free.login, free.free, free.disabled, free.noticed, free.locked]).toEqual([null, true, null, null, null]);
  expect(transferChanges(parseOverview(OVERVIEW_READY_03)!, "", "05", 100)).toBe("Выберите два разных места");
});

test("sessions_transcript_agents_journal_shapes", () => {
  expect(parseSessions({ sessions: [SESSION, SESSION_ARCHIVED] })!.map((s) => [s.id, s.archived]))
    .toEqual([[SESSION.id, false], [SESSION_ARCHIVED.id, true]]);
  expect(parseSessions({ sessions: [{ ...SESSION, id: "../x" }] })).toBeNull();          // id — только ses_ и 26 знаков
  expect(parseSessions({ sessions: "x" })).toBeNull();
  const t = parseTranscript(TRANSCRIPT_WITH_HTML)!;
  expect(t.messages.map((m) => [m.role, m.agent, m.parts.length, m.error]))
    .toEqual([["user", null, 1, null], ["assistant", "pomoshnik", 2, null],
              ["assistant", "konstruktor", 0, "Ваша доля бюджета на модель израсходована"]]);
  expect(t.clipped).toBe(true);
  expect(parseTranscript({ ...TRANSCRIPT_WITH_HTML, messages: [{ role: "system", at: 1, agent: null, parts: [], error: null }] }))
    .toBeNull();
  expect(parseTranscript({ ...TRANSCRIPT_WITH_HTML, clipped: "no" })).toBeNull();
  expect(parseAgents(AGENTS_03)!.map((a) => [a.name, a.tools, a.tags]))
    .toEqual([["pech-1", ["tag_now", "tag_period"], ["20FAKE_001_PV"]]]);
  expect(parseAgents({ agents: [{ ...AGENTS_03.agents[0], tools: "x" }] })).toBeNull();
  const j = parseJournal(JOURNAL_ROWS)!;
  expect(j.map((r) => [r.action, r.place, r.detail])).toEqual([
    ["shares", null, [["03", 10000, 8000], ["05", 10000, 12000]]], ["read", "03", null], ["wake", "05", null]]);
  expect(parseJournal({ rows: [{ ...JOURNAL_ROWS.rows[1], id: "2" }] })).toBeNull();
  expect(ACTION_LABELS).toEqual({ sessions: "открыл список разговоров", read: "прочитал разговор",
                                  agents: "открыл список агентов", agent: "прочитал инструкцию агента",
                                  wake: "разбудил место", shares: "перевёл долю",
                                  // действия учёток Д10б (R108)
                                  account_add: "завёл учётку студента", account_passwd: "выдал новый пароль",
                                  account_disable: "отключил учётку", account_enable: "включил учётку",
                                  account_unlock: "снял замок входа" });
});

test("teacher_api_paths_and_bodies", async () => {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const api = createTeacherApi(async (path, init) => {
    calls.push({ path, method: String(init.method), body: init.body === undefined ? undefined : JSON.parse(String(init.body)) });
    return new Response("{}", { status: 200 });
  });
  await api.overview();
  await api.place("03");
  await api.wake("03");
  await api.sessions("03");
  await api.session("03", SESSION.id);
  await api.agents("03");
  await api.agent("03", "pech-1");
  await api.journal();
  await api.shares([{ place: "03", from_cents: 10000, to_cents: 8000 }, { place: "05", from_cents: 10000, to_cents: 12000 }]);
  await api.stopPlace("03", "05");
  const P = "/pcbk/api/teacher";
  expect(calls).toEqual([
    { path: `${P}/overview`, method: "GET", body: undefined },
    { path: `${P}/places/03`, method: "GET", body: undefined },
    { path: `${P}/places/03/wake`, method: "POST", body: undefined },
    { path: `${P}/places/03/sessions`, method: "GET", body: undefined },
    { path: `${P}/places/03/sessions/${SESSION.id}`, method: "GET", body: undefined },
    { path: `${P}/places/03/agents`, method: "GET", body: undefined },
    { path: `${P}/places/03/agents/pech-1`, method: "GET", body: undefined },
    { path: `${P}/journal`, method: "GET", body: undefined },
    { path: `${P}/shares`, method: "POST", body: { changes: [{ place: "03", from_cents: 10000, to_cents: 8000 },
                                                             { place: "05", from_cents: 10000, to_cents: 12000 }] } },
    { path: `${P}/shares/floor`, method: "POST", body: { place: "03", to: "05" } },      // I-1 р2: числа пола нет
  ]);
  // часть пути — только проверенная: иначе вызов не уходит (сервер проверяет сам, это второй рубеж)
  const before = calls.length;
  for (const r of [await api.session("03", "../../oc/session"), await api.place("3"), await api.agent("03", "a/b")]) {
    expect(r.status).toBe(400);
  }
  expect(calls.length).toBe(before);
});

test("places_run_01_to_11_then_12_is_refused_on_client", async () => {   // Д15б: 11 мест, 12 — первый недопустимый
  expect(PLACES).toEqual(["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11"]);
  expect(PLACES.map(isPlace)).toEqual(PLACES.map(() => true));
  expect([isPlace("00"), isPlace("12"), isPlace("1"), isPlace("011")]).toEqual([false, false, false, false]);
  const calls: string[] = [];
  const api = createTeacherApi(async (path) => { calls.push(path); return new Response("{}", { status: 200 }); });
  await api.place("11");
  expect(calls).toEqual(["/pcbk/api/teacher/places/11"]);
  expect((await api.place("12")).status).toBe(400);                        // отказ до запроса, без похода на сервер
  expect(calls).toEqual(["/pcbk/api/teacher/places/11"]);                   // второй вызов не ушёл
});

test("action_labels_are_exactly_core_actions", () => {                // ревью Б1+Б2, M-3: договорный словарь журнала
  // ACTIONS журнала действий ядра (teacher_log.py) и подписи кабинета — один и тот же набор кодов
  const src = readFileSync("../core/pcbk_core/teacher_log.py", "utf8");
  const block = /^ACTIONS = frozenset\(\{([^}]*)\}\)$/m.exec(src)?.[1];
  expect(block).toBeDefined();
  const actions = [...block!.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
  expect(actions).toEqual(Object.keys(ACTION_LABELS).sort());
});
